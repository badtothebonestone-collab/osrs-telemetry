import argparse
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import live_context_query as query
from live_context_format import format_context_human
from telemetry_paths import find_newest_session, get_sessions_dir


REQUEST_SCHEMA = "context_request.v1"
RESPONSE_SCHEMA = "context_response.v1"
HEALTH_SCHEMA = "context_health.v1"
STATUS_SCHEMA = "context_status.v1"
SCHEMA_SCHEMA = "context_schema.v1"
SUPPORTED_TASKS = ["woodcutting"]
SUPPORTED_RESPONSE_MODES = ["compact", "normal", "full"]
SUPPORTED_NEEDS = [
    "baseline",
    "inventory",
    "activity",
    "liveness",
    "diagnostics",
    "navigation_readiness",
    "frame",
    "candidates",
    "events",
    "aim_point",
    "task_summary",
    "best:<classId>",
    "nearest:<classId>",
    "reachability:<classId>",
]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=False).encode("utf-8")


def is_local_host(host: str) -> bool:
    return str(host or "").lower() in LOCAL_HOSTS


def resolve_session(session: str | None, latest_session: bool, sessions_dir: str | None) -> tuple[Path | None, list[str]]:
    warnings: list[str] = []
    if session:
        return Path(session).expanduser(), warnings
    if latest_session:
        return find_newest_session(get_sessions_dir(sessions_dir)), warnings
    warnings.append("No --session or --latest-session supplied.")
    return None, warnings


def file_signature(path: Path) -> tuple[bool, int | None, int | None]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False, None, None
    except OSError:
        return False, None, None
    return True, int(stat.st_mtime_ns), int(stat.st_size)


def read_json_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise json.JSONDecodeError("empty file", text, 0)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def read_jsonl_file(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                records.append(value)
    return records


class LiveContextCache:
    def __init__(self, session: Path | None, reload_interval: float = 0.5):
        self.session = session
        self.reload_interval = max(0.0, float(reload_interval))
        self.lock = threading.RLock()
        self.last_check = 0.0
        self.context: dict | None = None
        self.signatures: dict[str, tuple[bool, int | None, int | None]] = {}
        self.reload_count = 0
        self.cache_hit_count = 0
        self.read_error_count = 0
        self.last_reload_time: str | None = None
        self.last_error: str | None = None
        self.last_error_file: str | None = None
        self.last_error_time: str | None = None

    def paths(self) -> dict[str, Path]:
        if self.session is None:
            return {}
        paths = query.live_paths(self.session)
        paths["performance"] = self.session / "interaction_geometry" / "live" / "live_performance_summary.json"
        return paths

    def stats(self) -> dict:
        return {
            "reloadCount": self.reload_count,
            "cacheHitCount": self.cache_hit_count,
            "readErrorCount": self.read_error_count,
            "lastReloadUtc": self.last_reload_time,
            "lastError": self.last_error,
            "lastErrorPath": self.last_error_file,
            "lastErrorUtc": self.last_error_time,
        }

    def load(self, force: bool = False) -> dict:
        with self.lock:
            now = time.monotonic()
            if not force and self.context is not None and now - self.last_check < self.reload_interval:
                self.cache_hit_count += 1
                return self._with_cache_stats(self.context)
            self.last_check = now

            if self.session is None:
                context = {
                    "session": None,
                    "paths": {},
                    "baseline": {},
                    "context": {},
                    "status": {},
                    "activity": {},
                    "events": [],
                    "navigation": {},
                    "performance": {},
                    "candidates": [],
                    "warnings": ["No telemetry session selected."],
                    "missingFields": ["session"],
                    "sourceFiles": [],
                }
                self.context = context
                self.cache_hit_count += 1
                return self._with_cache_stats(context)

            paths = self.paths()
            signatures = {name: file_signature(path) for name, path in paths.items()}
            if not force and self.context is not None and signatures == self.signatures:
                self.cache_hit_count += 1
                return self._with_cache_stats(self.context)

            warnings: list[str] = []
            missing: list[str] = []
            previous = self.context or {}

            def load_json(name: str, required: bool) -> dict:
                path = paths[name]
                exists, _mtime, _size = signatures[name]
                if not exists:
                    if required:
                        warnings.append(f"{name} missing: {path}")
                        missing.append(name)
                    return {}
                try:
                    return read_json_file(path)
                except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError, ValueError) as exc:
                    self._record_error(path, exc)
                    cached = previous.get(self._context_key(name))
                    if isinstance(cached, dict) and cached:
                        warnings.append(f"{name} transient read failure; kept previous cached data: {exc}")
                        return cached
                    warnings.append(f"{name} unreadable: {exc}")
                    missing.append(name)
                    return {}

            def load_candidates() -> list[dict]:
                path = paths["candidates"]
                exists, _mtime, _size = signatures["candidates"]
                if not exists:
                    warnings.append(f"candidates missing: {path}")
                    missing.append("candidates")
                    return []
                try:
                    return read_jsonl_file(path)
                except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError) as exc:
                    self._record_error(path, exc)
                    cached = previous.get("candidates")
                    if isinstance(cached, list) and cached:
                        warnings.append(f"candidates transient read failure; kept previous cached data: {exc}")
                        return cached
                    warnings.append(f"candidates unreadable: {exc}")
                    missing.append("candidates")
                    return []

            def load_events() -> list[dict]:
                path = paths["events"]
                exists, _mtime, _size = signatures["events"]
                if not exists:
                    return []
                try:
                    return read_jsonl_file(path)
                except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError) as exc:
                    self._record_error(path, exc)
                    cached = previous.get("events")
                    if isinstance(cached, list):
                        warnings.append(f"events transient read failure; kept previous cached data: {exc}")
                        return cached
                    warnings.append(f"events unreadable: {exc}")
                    return []

            context = {
                "session": self.session,
                "paths": paths,
                "baseline": load_json("baseline", True),
                "context": load_json("context", True),
                "status": load_json("status", True),
                "activity": load_json("activity", False),
                "events": load_events(),
                "navigation": load_json("navigation", False),
                "performance": load_json("performance", False),
                "candidates": load_candidates(),
                "warnings": warnings,
                "missingFields": missing,
                "sourceFiles": query.source_files_payload(paths),
            }
            if paths["candidates"].exists() and not context["candidates"]:
                context["warnings"].append("live_candidates is present but empty.")
            self.context = context
            self.signatures = signatures
            self.reload_count += 1
            self.last_reload_time = utc_now()
            return self._with_cache_stats(context)

    def _context_key(self, name: str) -> str:
        return "context" if name == "context" else name

    def _record_error(self, path: Path, exc: BaseException) -> None:
        self.read_error_count += 1
        self.last_error = str(exc)
        self.last_error_file = str(path)
        self.last_error_time = utc_now()

    def _with_cache_stats(self, context: dict) -> dict:
        context = dict(context)
        context["cacheStats"] = self.stats()
        return context


def request_args(request: dict, default_max_candidates: int) -> SimpleNamespace:
    constraints = request.get("constraints") if isinstance(request.get("constraints"), dict) else {}
    response_mode = request.get("responseMode") or "compact"
    if response_mode not in SUPPORTED_RESPONSE_MODES:
        response_mode = "compact"
    return SimpleNamespace(
        profile=request.get("profile"),
        max_distance=query.as_number(constraints.get("maxDistanceTiles")),
        freshness_ticks=int(request.get("maxAgeTicks") or query.DEFAULT_FRESHNESS_TICKS),
        freshness_ms=int(request.get("maxAgeMillis") or query.DEFAULT_FRESHNESS_MS),
        verbose=response_mode == "full",
        fields=response_mode,
        top=int(request.get("maxCandidates") or default_max_candidates or 3),
        benchmark=False,
    )


def candidate_satisfies(candidate: dict, constraints: dict) -> bool:
    if constraints.get("onScreen") is True and candidate.get("onScreen") is not True:
        return False
    if constraints.get("notUiBlocked") is True and candidate.get("uiBlocked") is not False:
        return False
    if constraints.get("geometryAvailable") is True and candidate.get("geometryAvailable") is not True:
        return False
    max_distance = query.as_number(constraints.get("maxDistanceTiles"))
    if max_distance is not None:
        distance = query.candidate_distance(candidate)
        if distance is None or distance > max_distance:
            return False
    return True


def constrained_context(context: dict, request: dict) -> dict:
    constraints = request.get("constraints") if isinstance(request.get("constraints"), dict) else {}
    profile = request.get("profile")
    candidates = query.filter_candidates(context.get("candidates", []), profile=profile)
    candidates = [candidate for candidate in candidates if candidate_satisfies(candidate, constraints)]
    copy = dict(context)
    copy["candidates"] = candidates
    return copy


def compact_baseline(baseline: dict, mode: str) -> dict:
    if mode == "full":
        return baseline
    player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
    viewport = baseline.get("cameraViewport") if isinstance(baseline.get("cameraViewport"), dict) else {}
    return {
        "schema": baseline.get("schema"),
        "generatedAtUtc": baseline.get("generatedAtUtc"),
        "latestTick": baseline.get("latestTick"),
        "latestFrameTick": baseline.get("latestFrameTick"),
        "latestFramePath": baseline.get("latestFramePath"),
        "gameState": baseline.get("gameState"),
        "player": {
            key: player.get(key)
            for key in (
                "worldX",
                "worldY",
                "plane",
                "sceneX",
                "sceneY",
                "localX",
                "localY",
                "animation",
                "poseAnimation",
                "animationFrame",
                "interacting",
                "isMoving",
                "runEnergy",
            )
        },
        "cameraViewport": {
            key: viewport.get(key)
            for key in (
                "cameraX",
                "cameraY",
                "cameraZ",
                "cameraPitch",
                "cameraYaw",
                "canvasWidth",
                "canvasHeight",
                "projectionStateHash",
            )
        },
    }


def compact_candidate_answer(answer: dict | None, mode: str) -> dict | None:
    if not answer:
        return None
    if mode == "full":
        return answer
    keys = [
        "classId",
        "targetName",
        "id",
        "rawId",
        "hash",
        "worldX",
        "worldY",
        "plane",
        "sceneX",
        "sceneY",
        "distanceTiles",
        "onScreen",
        "geometryAvailable",
        "uiBlocked",
        "blockingUiRegions",
        "qualityScore",
        "qualityTier",
        "aimPoint",
        "preferredGeometryType",
        "tick",
        "freshness",
        "targetLiveState",
        "livenessInterpretation",
        "navigation",
    ]
    if mode == "normal":
        keys.extend(["positiveSignals", "negativeSignals", "targetLiveEvidence", "lastSeenTick", "lastChangedTick", "lastDespawnedTick"])
    return {key: answer.get(key) for key in keys if key in answer}


def select_class_candidate(context: dict, class_id: str, mode: str, args: SimpleNamespace) -> tuple[dict | None, str, float, list[str], list[str], list[str]]:
    candidate = (
        query.nearest_candidate(context["candidates"], class_id, args.max_distance, args.profile)
        if mode == "nearest"
        else query.best_candidate(context["candidates"], class_id, args.max_distance, args.profile)
    )
    answer, status, confidence, reasons, warnings, missing = query.candidate_answer(candidate, context, args.freshness_ticks, args.freshness_ms)
    return answer, status, confidence, reasons, warnings, missing


def candidate_items(context: dict, request: dict, args: SimpleNamespace, limit: int, mode: str) -> list[dict]:
    candidates = list(context.get("candidates") or [])
    candidates.sort(key=query.best_sort_key)
    items = []
    for candidate in candidates[:limit]:
        answer, _status, _confidence, _reasons, _warnings, _missing = query.candidate_answer(candidate, context, args.freshness_ticks, args.freshness_ms)
        items.append(compact_candidate_answer(answer, mode) or {})
    return items


def combine_status(current: str, incoming: str) -> str:
    order = {"PASS": 0, "WARN": 1, "FAIL": 2}
    return incoming if order.get(incoming, 1) > order.get(current, 1) else current


def diagnostics_payload(context: dict, mode: str) -> dict:
    status = context.get("status") or {}
    performance = context.get("performance") or {}
    input_source = {
        "inputSourceRequested": status.get("inputSourceRequested"),
        "inputSourceActive": status.get("inputSourceActive"),
        "defaultLiveInputPreference": status.get("defaultLiveInputPreference"),
        "compactPacketsAvailable": status.get("compactPacketsAvailable"),
        "compactPacketsRecent": status.get("compactPacketsRecent"),
        "inputFallbackReason": status.get("inputFallbackReason"),
        "latestCompactPacketSequence": status.get("compactPacketLastSequence") or status.get("compactPacketLatestSequence"),
        "latestCompactPacketSegment": status.get("compactPacketLatestSegment"),
        "recordingMode": status.get("recordingMode"),
        "rawTickRecordingEnabled": status.get("rawTickRecordingEnabled"),
        "rawEventRecordingEnabled": status.get("rawEventRecordingEnabled"),
        "frameRecordingEnabled": status.get("frameRecordingEnabled"),
        "compactPacketRecordingEnabled": status.get("compactPacketRecordingEnabled"),
    }
    payload = {
        "activeProfile": status.get("profile"),
        "candidateCount": status.get("candidateCount"),
        "sourceSceneKnowledgeComplete": status.get("sourceSceneKnowledgeComplete"),
        "sourceCapHit": status.get("sourceCapHit"),
        "budgetExceeded": status.get("budgetExceeded"),
        "writeFailures": status.get("writeFailureCount"),
        "latestTickProcessed": status.get("latestTickProcessed"),
        "performance": performance if mode == "full" else {
            key: performance.get(key)
            for key in ("avgTotalMs", "p95TotalMs", "maxTotalMs", "budgetExceededCount", "recommendations")
            if key in performance
        },
        "inputSource": input_source,
        "cacheStats": context.get("cacheStats") or {},
    }
    return payload


def source_files_summary(source_files: list[dict]) -> dict:
    missing = [item.get("name") for item in source_files if not item.get("exists")]
    return {
        "allRequiredPresent": not any(name in {"baseline", "context", "candidates", "status"} for name in missing),
        "missingFiles": missing,
        "staleFiles": [],
        "liveFilesPresent": not missing,
        "fileCount": len(source_files),
    }


def compact_liveness_state(state: dict, *, examples: int = 0) -> dict:
    if not isinstance(state, dict):
        return {}
    keys = [
        "activeCandidateLiveState",
        "bestCandidateLiveState",
        "livenessInterpretation",
        "recentlyUnavailableCount",
        "recentlyDepletedCount",
        "suppressedCandidateCount",
        "candidatesSuppressedByLiveness",
        "candidatesSuppressedAsDepleted",
        "candidatesRevivedAfterRespawn",
        "livenessMode",
        "livenessDegraded",
        "livenessBudgetExceeded",
        "livenessCandidatesChecked",
        "livenessCandidatesSkippedByBudget",
    ]
    compact = {key: state.get(key) for key in keys if key in state}
    if "livenessInterpretation" not in compact:
        live_state = state.get("bestCandidateLiveState") or state.get("activeCandidateLiveState")
        if state.get("livenessDegraded") or state.get("livenessBudgetExceeded"):
            compact["livenessInterpretation"] = "degraded"
        elif live_state in ("recently_despawned", "depleted_or_stump", "stale", "changed"):
            compact["livenessInterpretation"] = "degraded"
        elif live_state == "live":
            compact["livenessInterpretation"] = "direct"
        elif live_state == "live_assumed":
            compact["livenessInterpretation"] = "assumed"
        else:
            compact["livenessInterpretation"] = "unknown"
    if state.get("bestCandidateChanged"):
        for key in ("previousBestCandidate", "currentBestCandidate", "bestCandidateChanged", "bestCandidateChangeReason", "previousBestSuppressedReason"):
            if key in state:
                compact[key] = state.get(key)
    if examples > 0 and isinstance(state.get("recentlyUnavailableTargets"), list):
        compact["recentlyUnavailableTargets"] = state["recentlyUnavailableTargets"][:examples]
    return compact


def compact_reachability_report(report: dict, mode: str) -> tuple[dict, list[dict]]:
    if mode == "full":
        return report, report.get("candidates") or []
    summary = report.get("reachabilitySummary") or {}
    compact_summary = {
        "status": report.get("status"),
        "latestTick": report.get("latestTick"),
        "classId": report.get("classId"),
        "player": report.get("player"),
        "collisionWindow": report.get("collisionWindow"),
        "navigationStatus": (report.get("navigationReadiness") or {}).get("status"),
        "candidateCount": summary.get("candidateCount"),
        "candidatesInsideCollisionWindow": summary.get("candidatesInsideCollisionWindow"),
        "candidatesOutsideCollisionWindow": summary.get("candidatesOutsideCollisionWindow"),
        "reachableCount": summary.get("reachableCount"),
        "blockedCount": summary.get("blockedCount"),
        "unknownCount": summary.get("unknownCount"),
    }
    candidates = []
    for candidate in report.get("candidates") or []:
        item = {
            key: candidate.get(key)
            for key in (
                "classId",
                "targetName",
                "id",
                "hash",
                "worldX",
                "worldY",
                "plane",
                "sceneX",
                "sceneY",
                "distanceTiles",
                "onScreen",
                "geometryAvailable",
                "targetLiveState",
                "aimPoint",
                "directReachability",
                "targetInCollisionWindow",
                "pathLengthTiles",
                "reachabilityConfidence",
                "reachabilityEvidence",
                "missingNavigationFields",
            )
            if key in candidate
        }
        candidates.append(item)
    return compact_summary, candidates


def frame_payload(context: dict) -> dict:
    baseline = context.get("baseline") or {}
    status = context.get("status") or {}
    return {
        "latestFrameTick": query.first_value(status.get("latestFrameTick"), baseline.get("latestFrameTick")),
        "latestFramePath": query.first_value(status.get("latestFramePath"), baseline.get("latestFramePath")),
        "selectedTickHasFrame": status.get("selectedTickHasFrame"),
        "frameIndexExists": status.get("frameIndexExists"),
    }


def requested_class_needs(needs: list[str], prefix: str) -> list[str]:
    values = []
    marker = prefix + ":"
    for need in needs:
        if isinstance(need, str) and need.startswith(marker) and need[len(marker) :]:
            values.append(need[len(marker) :])
    return values


def build_context_response(
    context: dict,
    request: dict,
    *,
    default_max_candidates: int = 3,
    max_response_bytes: int = 1_000_000,
    compact_include_source_files: bool = False,
    compact_liveness_examples: int = 0,
) -> dict:
    started = time.perf_counter()
    request_id = request.get("requestId")
    response_mode = request.get("responseMode") if request.get("responseMode") in SUPPORTED_RESPONSE_MODES else "compact"
    needs = request.get("needs") if isinstance(request.get("needs"), list) else []
    needs = [str(need) for need in needs]
    if not needs:
        needs = ["task_summary"] if request.get("task") else ["baseline", "diagnostics"]
    max_candidates = int(request.get("maxCandidates") or default_max_candidates or 3)
    max_candidates = max(1, max_candidates)
    try:
        max_events = max(0, int(request.get("maxEvents") if request.get("maxEvents") is not None else 5))
    except (TypeError, ValueError):
        max_events = 5
    args = request_args(request, max_candidates)
    scoped_context = constrained_context(context, request)
    warnings = list(scoped_context.get("warnings") or [])
    missing = list(scoped_context.get("missingFields") or [])
    status = "PASS"
    confidence_values: list[float] = []

    selected_candidate_for_freshness = scoped_context["candidates"][0] if scoped_context.get("candidates") else None
    freshness, freshness_warnings = query.freshness_info(scoped_context, selected_candidate_for_freshness, args.freshness_ticks, args.freshness_ms)
    warnings.extend(freshness_warnings)
    if not freshness.get("freshByTicks") or not freshness.get("freshByMillis"):
        status = combine_status(status, "WARN")

    response: dict[str, Any] = {
        "schema": RESPONSE_SCHEMA,
        "requestId": request_id,
        "generatedAtUtc": query.utc_now(),
        "latestTick": query.latest_tick(scoped_context),
        "status": "PASS",
        "freshness": freshness,
        "warnings": [],
        "missingCapabilities": [],
    }
    if response_mode == "compact" and not compact_include_source_files:
        response["sourceFilesSummary"] = source_files_summary(scoped_context.get("sourceFiles") or [])
    else:
        response["sourceFiles"] = scoped_context.get("sourceFiles") or []

    if "baseline" in needs:
        response["baseline"] = compact_baseline(scoped_context.get("baseline") or {}, response_mode)
    if "inventory" in needs:
        inventory = query.inventory_payload(scoped_context)
        response["inventory"] = inventory.get("inventoryState") if response_mode != "full" else inventory
        if response_mode == "compact":
            response["recentInventoryDeltas"] = (inventory.get("recentInventoryDeltas") or [])[:max_candidates]
        status = combine_status(status, inventory.get("status", "WARN"))
        warnings.extend(inventory.get("warnings") or [])
        missing.extend(inventory.get("missingFields") or [])
    if "activity" in needs:
        activity = query.activity_payload(scoped_context)
        response["activity"] = activity.get("activityState") if response_mode != "full" else activity
        if response_mode == "compact":
            response["woodcuttingState"] = activity.get("woodcuttingState")
            response["recentActivityEvents"] = (activity.get("recentActivityEvents") or [])[:max_candidates]
        else:
            response["woodcuttingState"] = activity.get("woodcuttingState")
        status = combine_status(status, activity.get("status", "WARN"))
        warnings.extend(activity.get("warnings") or [])
        missing.extend(activity.get("missingFields") or [])
    if "liveness" in needs:
        liveness = query.liveness_payload(scoped_context)
        if response_mode == "compact":
            response["liveness"] = compact_liveness_state(liveness.get("targetLivenessState") or {}, examples=compact_liveness_examples)
        elif response_mode == "normal":
            response["liveness"] = compact_liveness_state(liveness.get("targetLivenessState") or {}, examples=max(1, compact_liveness_examples or 3))
        else:
            response["liveness"] = liveness
        status = combine_status(status, liveness.get("status", "WARN"))
        warnings.extend(liveness.get("warnings") or [])
        missing.extend(liveness.get("missingFields") or [])
    if "navigation_readiness" in needs:
        response["navigationReadiness"] = query.navigation_readiness(scoped_context.get("navigation") or {}, scoped_context.get("baseline") or {})
        missing.extend(response["navigationReadiness"].get("missingCapabilities") or [])
        if response["navigationReadiness"].get("status") in {"unknown", "summary"}:
            status = combine_status(status, "WARN")
            warnings.extend(response["navigationReadiness"].get("warnings") or [])
            warning = response["navigationReadiness"].get("warning")
            if warning:
                warnings.append(warning)
    if "diagnostics" in needs:
        response["diagnostics"] = diagnostics_payload(scoped_context, response_mode)
    if "frame" in needs:
        response["frame"] = frame_payload(scoped_context)
    if "candidates" in needs:
        response["candidates"] = {
            "count": len(scoped_context.get("candidates") or []),
            "items": candidate_items(scoped_context, request, args, max_candidates, response_mode),
        }
    if "events" in needs:
        events = query.events_payload(scoped_context, max_events)
        if response_mode == "full":
            all_events = scoped_context.get("events") if isinstance(scoped_context.get("events"), list) else []
            event_items = [event for event in all_events[-max_events:] if isinstance(event, dict)] if max_events else []
        else:
            event_items = events.get("events") or []
        response["events"] = event_items
        response["recentEvents"] = event_items
        response["eventCount"] = events.get("eventCount")
        if not events.get("events"):
            status = combine_status(status, "WARN")
            warnings.extend(events.get("warnings") or [])

    reachability_summary: dict[str, Any] = {}
    reachability_candidates: dict[str, list[dict]] = {}
    reachability_reports: dict[str, dict] = {}
    for class_id in requested_class_needs(needs, "reachability"):
        report = query.reachability_payload(scoped_context, class_id, args)
        compact_summary, items = compact_reachability_report(report, response_mode)
        if response_mode == "full":
            reachability_reports[class_id] = compact_summary
        else:
            reachability_summary[class_id] = compact_summary
            reachability_candidates[class_id] = items[:max_candidates]
        status = combine_status(status, report.get("status", "WARN"))
        warnings.extend(report.get("warnings") or [])
        missing.extend(report.get("missingCapabilities") or [])
        missing.extend(report.get("missingFields") or [])
    if reachability_summary:
        response["reachabilitySummary"] = reachability_summary
    if reachability_candidates:
        response["reachabilityCandidates"] = reachability_candidates
    if reachability_reports:
        response["reachabilityReports"] = reachability_reports

    best_candidates: dict[str, dict | None] = {}
    nearest_candidates: dict[str, dict | None] = {}
    for class_id in requested_class_needs(needs, "best"):
        answer, answer_status, confidence, reasons, answer_warnings, answer_missing = select_class_candidate(scoped_context, class_id, "best", args)
        best_candidates[class_id] = compact_candidate_answer(answer, response_mode)
        status = combine_status(status, answer_status)
        confidence_values.append(confidence)
        warnings.extend(answer_warnings)
        missing.extend(answer_missing)
        if response_mode != "compact" and isinstance(best_candidates[class_id], dict):
            best_candidates[class_id]["reasons"] = reasons if best_candidates[class_id] else reasons
    for class_id in requested_class_needs(needs, "nearest"):
        answer, answer_status, confidence, reasons, answer_warnings, answer_missing = select_class_candidate(scoped_context, class_id, "nearest", args)
        nearest_candidates[class_id] = compact_candidate_answer(answer, response_mode)
        status = combine_status(status, answer_status)
        confidence_values.append(confidence)
        warnings.extend(answer_warnings)
        missing.extend(answer_missing)
        if response_mode != "compact" and isinstance(nearest_candidates[class_id], dict):
            nearest_candidates[class_id]["reasons"] = reasons if nearest_candidates[class_id] else reasons
    if best_candidates:
        response["bestCandidates"] = best_candidates
    if nearest_candidates:
        response["nearestCandidates"] = nearest_candidates
    if "aim_point" in needs:
        source = None
        if best_candidates:
            source = next((candidate for candidate in best_candidates.values() if candidate), None)
        if source is None and nearest_candidates:
            source = next((candidate for candidate in nearest_candidates.values() if candidate), None)
        if source is None and scoped_context.get("candidates"):
            answer, _answer_status, _confidence, _reasons, _answer_warnings, _answer_missing = query.candidate_answer(
                scoped_context["candidates"][0],
                scoped_context,
                args.freshness_ticks,
                args.freshness_ms,
            )
            source = answer
        response["aimPoint"] = source.get("aimPoint") if isinstance(source, dict) else None
        if response["aimPoint"] is None:
            status = combine_status(status, "WARN")
            missing.append("aimPoint")
            warnings.append("No aim point available in requested context.")
    if "task_summary" in needs or request.get("task"):
        task = str(request.get("task") or "").lower()
        if task == "woodcutting":
            task_payload = query.woodcutting_task_payload(scoped_context, args)
            response["taskSummary"] = query.compact_json_payload(task_payload, args) if response_mode == "compact" else task_payload
            status = combine_status(status, task_payload.get("status", "WARN"))
            warnings.extend(task_payload.get("warnings") or [])
            missing.extend(task_payload.get("missingFields") or [])
            missing.extend(task_payload.get("missingCapabilities") or [])
        elif task:
            status = combine_status(status, "FAIL")
            warnings.append(f"Unsupported task context: {task}")
            missing.append("task")

    if not scoped_context.get("baseline") and not scoped_context.get("status") and not scoped_context.get("candidates"):
        status = combine_status(status, "FAIL")

    response["status"] = status
    response["confidence"] = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else (0.7 if status == "PASS" else 0.4 if status == "WARN" else 0.0)
    response["warnings"] = sorted(set(str(warning) for warning in warnings if warning))
    response["missingCapabilities"] = sorted(set(str(field) for field in missing if field))
    status_doc = scoped_context.get("status") or {}
    if status_doc.get("livenessDegraded") or status_doc.get("livenessBudgetExceeded"):
        response["warnings"] = sorted(set(response["warnings"] + ["liveness degraded due to realtime budget; targetLiveState may be assumed."]))
        if response["status"] == "PASS":
            response["status"] = "WARN"
    if status_doc.get("livenessMode") == "off":
        response["missingCapabilities"] = sorted(set(response["missingCapabilities"] + ["realtimeLiveness"]))
    if status_doc.get("inputSourceActive") == "raw-ticks" and status_doc.get("inputSourceRequested") == "auto":
        response["warnings"] = sorted(
            set(response["warnings"] + ["live processor is using raw tick fallback; compact packets are not active."])
        )
    response["serviceTimingMillis"] = round((time.perf_counter() - started) * 1000.0, 3)
    return enforce_response_size(response, max_response_bytes, response_mode)


def enforce_response_size(payload: dict, max_bytes: int, response_mode: str) -> dict:
    if max_bytes <= 0 or len(json_bytes(payload)) <= max_bytes or response_mode == "full":
        return payload
    warnings = payload.setdefault("warnings", [])
    warnings.append(f"response exceeded {max_bytes} bytes; candidate arrays were truncated.")
    candidates = payload.get("candidates")
    if isinstance(candidates, dict) and isinstance(candidates.get("items"), list):
        candidates["items"] = candidates["items"][:1]
        candidates["truncated"] = True
    if len(json_bytes(payload)) > max_bytes:
        payload.pop("sourceFiles", None)
        warnings.append("sourceFiles omitted to keep compact response below size guard.")
    return payload


def health_payload(context: dict) -> dict:
    status_doc = context.get("status") or {}
    baseline = context.get("baseline") or {}
    latest = query.latest_tick(context)
    candidate = context.get("candidates", [None])[0] if context.get("candidates") else None
    freshness, freshness_warnings = query.freshness_info(context, candidate, query.DEFAULT_FRESHNESS_TICKS, query.DEFAULT_FRESHNESS_MS)
    warnings = list(context.get("warnings") or []) + freshness_warnings
    missing = context.get("missingFields") or []
    status = "ok"
    if missing:
        status = "warn"
    if not baseline and not status_doc:
        status = "fail"
    return {
        "schema": HEALTH_SCHEMA,
        "status": status,
        "sessionPath": str(context.get("session")) if context.get("session") else None,
        "latestTick": latest,
        "liveFilesPresent": not missing,
        "liveFreshness": freshness,
        "warnings": sorted(set(str(warning) for warning in warnings if warning)),
    }


def status_payload(context: dict) -> dict:
    status_doc = context.get("status") or {}
    context_index = context.get("context") or {}
    return {
        "schema": STATUS_SCHEMA,
        "status": "ok" if status_doc else "warn",
        "sessionPath": str(context.get("session")) if context.get("session") else None,
        "latestTick": query.latest_tick(context),
        "liveProcessorFreshness": health_payload(context).get("liveFreshness"),
        "activeProfile": status_doc.get("profile") or context_index.get("activeProfile"),
        "candidateCount": len(context.get("candidates") or []),
        "inputSourceActive": status_doc.get("inputSourceActive"),
        "compactPacketsAvailable": status_doc.get("compactPacketsAvailable"),
        "inputFallbackReason": status_doc.get("inputFallbackReason"),
        "recordingMode": status_doc.get("recordingMode"),
        "rawTickRecordingEnabled": status_doc.get("rawTickRecordingEnabled"),
        "rawEventRecordingEnabled": status_doc.get("rawEventRecordingEnabled"),
        "frameRecordingEnabled": status_doc.get("frameRecordingEnabled"),
        "sourceSceneKnowledgeComplete": status_doc.get("sourceSceneKnowledgeComplete"),
        "sourceCapHit": status_doc.get("sourceCapHit"),
        "budgetExceeded": status_doc.get("budgetExceeded"),
        "writeFailures": status_doc.get("writeFailureCount"),
        "warnings": sorted(set(str(warning) for warning in context.get("warnings", []) if warning)),
        "cacheStats": context.get("cacheStats") or {},
    }


def schema_payload() -> dict:
    return {
        "schema": SCHEMA_SCHEMA,
        "supportedRequestSchemas": [REQUEST_SCHEMA],
        "supportedResponseSchemas": [RESPONSE_SCHEMA, HEALTH_SCHEMA, STATUS_SCHEMA],
        "supportedNeeds": SUPPORTED_NEEDS,
        "supportedTasks": SUPPORTED_TASKS,
        "supportedResponseModes": SUPPORTED_RESPONSE_MODES,
        "supportedRequestOptions": ["maxCandidates", "maxEvents", "responseMode", "constraints", "maxAgeTicks", "maxAgeMillis"],
        "endpoints": {
            "GET": ["/health", "/schema", "/status", "/summary"],
            "POST": ["/context", "/context/batch"],
        },
        "notes": [
            "This service is read-only.",
            "Responses contain observations and readiness hints only.",
            "No action, click, menu, mouse, or keyboard endpoints are implemented.",
        ],
    }


def error_payload(message: str, status: str = "FAIL", request_id: Any = None) -> dict:
    return {
        "schema": RESPONSE_SCHEMA,
        "requestId": request_id,
        "generatedAtUtc": utc_now(),
        "latestTick": None,
        "status": status,
        "freshness": {},
        "warnings": [message],
        "missingCapabilities": [],
        "sourceFiles": [],
        "serviceTimingMillis": 0.0,
    }


class ContextState:
    def __init__(self, args):
        self.args = args
        session, session_warnings = resolve_session(args.session, args.latest_session, args.sessions_dir)
        self.session_warnings = session_warnings
        self.cache = LiveContextCache(session, args.reload_interval)
        self.max_candidates = args.max_candidates
        self.max_response_bytes = args.max_response_bytes
        self.auth_token = args.auth_token if not args.no_auth_token else None
        self.debug = args.debug
        self.compact_include_source_files = bool(args.compact_include_source_files)
        self.compact_include_liveness_examples = max(0, int(args.compact_include_liveness_examples))

    def load_context(self, force: bool = False) -> dict:
        context = self.cache.load(force=force)
        if self.session_warnings:
            context = dict(context)
            context["warnings"] = self.session_warnings + list(context.get("warnings") or [])
        return context


class ContextRequestHandler(BaseHTTPRequestHandler):
    server_version = "OSRSTelemetryContextService/0.1"

    def log_message(self, format, *args):  # noqa: A002
        if getattr(self.server.context_state, "debug", False):
            super().log_message(format, *args)

    def do_GET(self):  # noqa: N802
        if not self.authorized():
            self.send_json(error_payload("missing or invalid X-Context-Token"), status_code=401)
            return
        context = self.server.context_state.load_context()
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        if path == "/health":
            self.send_json(health_payload(context))
        elif path == "/schema":
            self.send_json(schema_payload())
        elif path == "/status":
            self.send_json(status_payload(context))
        elif path == "/summary":
            self.handle_summary(context, params)
        else:
            self.send_json(error_payload(f"unknown endpoint: {self.path}"), status_code=404)

    def do_POST(self):  # noqa: N802
        if not self.authorized():
            self.send_json(error_payload("missing or invalid X-Context-Token"), status_code=401)
            return
        if self.path not in {"/context", "/context/batch"}:
            self.send_json(error_payload(f"unknown endpoint: {self.path}"), status_code=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        try:
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.send_json(error_payload(f"invalid JSON request: {exc}"), status_code=400)
            return
        state = self.server.context_state
        context = state.load_context()
        if self.path == "/context/batch":
            if not isinstance(payload, list):
                self.send_json(error_payload("/context/batch expects a JSON list"), status_code=400)
                return
            responses = [handle_context_request(context, item, state) for item in payload]
            self.send_json(responses)
        else:
            response = handle_context_request(context, payload, state)
            self.send_json(response)

    def authorized(self) -> bool:
        token = self.server.context_state.auth_token
        if not token:
            return True
        return self.headers.get("X-Context-Token") == token

    def send_json(self, payload: Any, status_code: int = 200) -> None:
        data = json_bytes(payload)
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_text(self, text: str, status_code: int = 200) -> None:
        data = text.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_summary(self, context: dict, params: dict[str, list[str]]) -> None:
        task = (params.get("task") or ["woodcutting"])[0] or "woodcutting"
        response_format = ((params.get("format") or ["text"])[0] or "text").lower()
        try:
            top = max(1, int((params.get("top") or ["3"])[0]))
        except ValueError:
            top = 3
        request = {
            "schema": REQUEST_SCHEMA,
            "task": task,
            "needs": [
                "baseline",
                "best:tree",
                "nearest:tree",
                "reachability:tree",
                "inventory",
                "activity",
                "liveness",
                "events",
                "navigation_readiness",
                "diagnostics",
                "task_summary",
            ],
            "maxCandidates": top,
            "maxEvents": top,
            "responseMode": "compact",
        }
        response = build_context_response(
            context,
            request,
            default_max_candidates=self.server.context_state.max_candidates,
            max_response_bytes=self.server.context_state.max_response_bytes,
            compact_include_source_files=self.server.context_state.compact_include_source_files,
            compact_liveness_examples=self.server.context_state.compact_include_liveness_examples,
        )
        if response_format == "json":
            self.send_json(response)
            return
        self.send_text(format_context_human(response, compact=False, top=top))


def handle_context_request(context: dict, payload: Any, state: ContextState) -> dict:
    if not isinstance(payload, dict):
        return error_payload("context request must be a JSON object")
    if payload.get("schema") != REQUEST_SCHEMA:
        return error_payload(f"unsupported schema: {payload.get('schema')}", request_id=payload.get("requestId"))
    return build_context_response(
        context,
        payload,
        default_max_candidates=state.max_candidates,
        max_response_bytes=state.max_response_bytes,
        compact_include_source_files=state.compact_include_source_files,
        compact_liveness_examples=state.compact_include_liveness_examples,
    )


def serve(args) -> int:
    if not is_local_host(args.host):
        if not args.allow_nonlocal_host:
            print("Refusing to bind to a non-local host without --allow-nonlocal-host.")
            return 2
        print("WARNING: non-local host binding requested. This sidecar exposes read-only telemetry context.")
        if not args.auth_token and not args.no_auth_token:
            print("Refusing non-local host without --auth-token or explicit --no-auth-token.")
            return 2
    state = ContextState(args)
    if state.cache.session is None:
        print("No session selected. Use --session or --latest-session.")
        return 2
    server = ThreadingHTTPServer((args.host, args.port), ContextRequestHandler)
    server.context_state = state
    print(f"Read-only context service listening on http://{args.host}:{args.port}")
    print(f"session: {state.cache.session}")
    print("endpoints: GET /health /schema /status, POST /context /context/batch")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("context service stopped")
    finally:
        server.server_close()
    return 0


def oneshot(args) -> int:
    state = ContextState(args)
    context = state.load_context(force=True)
    try:
        payload = json.loads(args.oneshot_request)
    except json.JSONDecodeError as exc:
        print(json.dumps(error_payload(f"invalid JSON request: {exc}"), separators=(",", ":")))
        return 1
    response = handle_context_request(context, payload, state)
    print(json.dumps(response, separators=(",", ":"), sort_keys=False))
    return 0 if response.get("status") != "FAIL" else 1


def parse_args():
    parser = argparse.ArgumentParser(description="Read-only local context request/response sidecar for live telemetry files.")
    parser.add_argument("--session", help="Telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override sessions directory when --latest-session is used.")
    parser.add_argument("--latest-session", action="store_true", help="Use the newest telemetry session.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1.")
    parser.add_argument("--port", type=int, default=8890, help="Bind port. Default: 8890.")
    parser.add_argument("--reload-interval", type=float, default=0.5, help="Minimum seconds between live file stat checks. Default: 0.5.")
    parser.add_argument("--max-candidates", type=int, default=3, help="Default maximum candidates per response.")
    parser.add_argument("--compact", action="store_true", help="Prefer compact responses unless request asks otherwise.")
    parser.add_argument("--debug", action="store_true", help="Enable HTTP request logging.")
    parser.add_argument("--allow-nonlocal-host", action="store_true", help="Allow binding to a host other than localhost/127.0.0.1.")
    parser.add_argument("--auth-token", help="Require X-Context-Token header for requests.")
    parser.add_argument("--no-auth-token", action="store_true", help="Explicitly run without an auth token.")
    parser.add_argument("--oneshot-request", help="Process one context_request.v1 JSON string and exit.")
    parser.add_argument("--max-response-bytes", type=int, default=1_000_000, help="Compact response size guard. Default: 1000000.")
    parser.add_argument("--compact-include-source-files", action="store_true", help="Include full sourceFiles even for compact responses.")
    parser.add_argument("--compact-include-liveness-examples", type=int, default=0, help="Recently unavailable examples to include in compact liveness responses. Default: 0.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.oneshot_request:
        return oneshot(args)
    return serve(args)


if __name__ == "__main__":
    raise SystemExit(main())

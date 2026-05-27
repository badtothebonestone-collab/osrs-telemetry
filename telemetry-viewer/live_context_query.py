import argparse
import json
import math
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from candidate_core import woodcutting_resource_preference_key
from telemetry_paths import find_newest_session, get_sessions_dir
from live_context_format import format_context_human


SUMMARY_SCHEMA = "live_context_summary.v1"
ANSWER_SCHEMA = "live_context_answer.v1"
TASK_SCHEMA = "live_task_context.v1"
SELF_TEST_SCHEMA = "live_context_self_test.v1"
REACHABILITY_SCHEMA = "live_candidate_reachability_qa.v1"
EVENTS_SCHEMA = "live_context_events.v1"
DEFAULT_FRESHNESS_TICKS = 5
DEFAULT_FRESHNESS_MS = 5000
TREE_CLASSES = {"tree", "oak_tree", "willow_tree", "maple_tree", "yew_tree", "magic_tree"}
QUERY_TIMING_KEYS = ("queryReadMillis", "queryParseMillis", "querySelectMillis", "totalQueryMillis")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def json_dump_compact(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def resolve_session(args) -> tuple[Path | None, list[str]]:
    warnings = []
    if args.session:
        return Path(args.session).expanduser(), warnings
    if args.latest_session:
        return find_newest_session(get_sessions_dir(args.sessions_dir)), warnings
    warnings.append("No --session or --latest-session supplied; using newest session for compatibility.")
    return find_newest_session(get_sessions_dir(args.sessions_dir)), warnings


def live_paths(session: Path) -> dict[str, Path]:
    live_dir = session / "interaction_geometry" / "live"
    return {
        "baseline": live_dir / "live_baseline_state.json",
        "context": live_dir / "live_context_index.json",
        "candidates": live_dir / "live_candidates.jsonl",
        "status": live_dir / "live_status.json",
        "activity": live_dir / "live_activity_state.json",
        "events": live_dir / "live_event_timeline.jsonl",
        "navigation": live_dir / "live_navigation_summary.json",
        "watchValues": live_dir / "live_watch_values.json",
        "overlayDebug": live_dir / "overlay_debug_state.json",
    }


def source_files_payload(paths: dict[str, Path]) -> list[dict]:
    payload = []
    for name, path in paths.items():
        payload.append(
            {
                "name": name,
                "path": str(path),
                "exists": path.exists(),
                "sizeBytes": path.stat().st_size if path.exists() else None,
                "modifiedUtc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
                if path.exists()
                else None,
            }
        )
    return payload


def add_timing(timing: dict | None, key: str, started: float) -> None:
    if timing is not None:
        timing[key] = timing.get(key, 0.0) + (time.perf_counter() - started) * 1000.0


def read_json(path: Path, warnings: list[str], missing_fields: list[str], label: str, timing: dict | None = None) -> dict:
    if not path.exists():
        warnings.append(f"{label} missing: {path}")
        missing_fields.append(label)
        return {}
    try:
        started = time.perf_counter()
        text = path.read_text(encoding="utf-8")
        add_timing(timing, "queryReadMillis", started)
        started = time.perf_counter()
        value = json.loads(text)
        add_timing(timing, "queryParseMillis", started)
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"{label} unreadable: {exc}")
        missing_fields.append(label)
        return {}
    if not isinstance(value, dict):
        warnings.append(f"{label} did not contain a JSON object.")
        missing_fields.append(label)
        return {}
    return value


def read_jsonl(path: Path, warnings: list[str], missing_fields: list[str], label: str, timing: dict | None = None) -> list[dict]:
    records = []
    if not path.exists():
        warnings.append(f"{label} missing: {path}")
        missing_fields.append(label)
        return records
    malformed = 0
    try:
        read_started = time.perf_counter()
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                text = line.strip()
                if not text:
                    continue
                try:
                    parse_started = time.perf_counter()
                    value = json.loads(text)
                    add_timing(timing, "queryParseMillis", parse_started)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if isinstance(value, dict):
                    records.append(value)
        add_timing(timing, "queryReadMillis", read_started)
    except OSError as exc:
        warnings.append(f"{label} unreadable: {exc}")
        missing_fields.append(label)
        return records
    if malformed:
        warnings.append(f"{label} had {malformed} malformed JSONL line(s).")
    return records


def load_live_context(session: Path, timing: dict | None = None) -> dict:
    warnings: list[str] = []
    missing_fields: list[str] = []
    paths = live_paths(session)
    baseline = read_json(paths["baseline"], warnings, missing_fields, "live_baseline_state", timing)
    context = read_json(paths["context"], warnings, missing_fields, "live_context_index", timing)
    status = read_json(paths["status"], warnings, missing_fields, "live_status", timing)
    activity = read_json(paths["activity"], warnings, missing_fields, "live_activity_state", timing) if paths["activity"].exists() else {}
    events = read_jsonl(paths["events"], warnings, missing_fields, "live_event_timeline", timing) if paths["events"].exists() else []
    navigation = read_json(paths["navigation"], warnings, missing_fields, "live_navigation_summary", timing) if paths["navigation"].exists() else {}
    watch_values = read_json(paths["watchValues"], warnings, missing_fields, "live_watch_values", timing) if paths["watchValues"].exists() else {}
    candidates = read_jsonl(paths["candidates"], warnings, missing_fields, "live_candidates", timing)
    if paths["candidates"].exists() and not candidates:
        warnings.append("live_candidates is present but empty.")
    return {
        "session": session,
        "paths": paths,
        "baseline": baseline,
        "context": context,
        "status": status,
        "activity": activity,
        "events": events,
        "navigation": navigation,
        "watchValues": watch_values,
        "candidates": candidates,
        "warnings": warnings,
        "missingFields": missing_fields,
        "sourceFiles": source_files_payload(paths),
    }


def first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def target_payload(candidate: dict) -> dict:
    target = candidate.get("target")
    return target if isinstance(target, dict) else {}


def candidate_class_ids(candidate: dict) -> set[str]:
    ids = set()
    for key in ("classId", "targetClass"):
        value = candidate.get(key)
        if value:
            ids.add(str(value).lower())
    for value in candidate.get("targetClassIds") or []:
        if value:
            ids.add(str(value).lower())
    target = target_payload(candidate)
    for value in target.get("targetClassIds") or []:
        if value:
            ids.add(str(value).lower())
    return ids


def candidate_class_id(candidate: dict) -> str:
    ids = candidate_class_ids(candidate)
    if ids:
        return sorted(ids)[0] if candidate.get("classId") is None else str(candidate.get("classId")).lower()
    target = target_payload(candidate)
    category = first_value(candidate.get("category"), candidate.get("targetCategory"), target.get("targetCategory"))
    return str(category or "unclassified").lower()


def candidate_matches_class(candidate: dict, class_id: str) -> bool:
    wanted = class_id.lower()
    if wanted in candidate_class_ids(candidate):
        return True
    target = target_payload(candidate)
    category = str(first_value(candidate.get("category"), candidate.get("targetCategory"), target.get("targetCategory"), "")).lower()
    target_type = str(first_value(candidate.get("targetType"), target.get("targetType"), "")).lower()
    tags = {str(tag).lower() for tag in first_value(candidate.get("tags"), candidate.get("targetTags"), target.get("targetTags"), []) or []}
    return wanted in {category, target_type} or wanted in tags


def is_tree_like(candidate: dict) -> bool:
    if candidate_class_ids(candidate) & TREE_CLASSES:
        return True
    target = target_payload(candidate)
    name = str(first_value(candidate.get("name"), target.get("name"), target.get("targetName"), "")).lower()
    category = str(first_value(candidate.get("category"), candidate.get("targetCategory"), target.get("targetCategory"), "")).lower()
    tags = {str(tag).lower() for tag in first_value(candidate.get("targetTags"), target.get("targetTags"), []) or []}
    return category == "tree" or "tree" in tags or any(text in name for text in ("tree", "oak", "willow", "maple", "yew"))


def candidate_distance(candidate: dict) -> float | None:
    for key in ("distanceTiles", "targetDistanceChebyshev", "targetDistanceTiles"):
        value = as_number(candidate.get(key))
        if value is not None:
            return value
    return None


def candidate_tick(candidate: dict) -> int | None:
    return as_int(first_value(candidate.get("tick"), candidate.get("tickId")))


def latest_tick(context: dict) -> int | None:
    status = context["status"]
    baseline = context["baseline"]
    return as_int(first_value(status.get("latestTickProcessed"), status.get("latestTick"), baseline.get("latestTick")))


def candidate_aim_point(candidate: dict) -> dict | None:
    context = candidate.get("aimPointContext")
    if isinstance(context, dict):
        x = as_number(first_value(context.get("canvasX"), context.get("x")))
        y = as_number(first_value(context.get("canvasY"), context.get("y")))
        if x is not None and y is not None:
            return {"canvasX": x, "canvasY": y, "source": context.get("source")}
    aim = candidate.get("aimPoint")
    if not isinstance(aim, dict):
        geometry = candidate.get("geometry") if isinstance(candidate.get("geometry"), dict) else {}
        aim = geometry.get("aimPoint")
    if isinstance(aim, dict):
        x = as_number(first_value(aim.get("canvasX"), aim.get("x")))
        y = as_number(first_value(aim.get("canvasY"), aim.get("y")))
        if x is not None and y is not None:
            source = candidate.get("preferredGeometryType")
            geometry = candidate.get("geometry") if isinstance(candidate.get("geometry"), dict) else {}
            if not source:
                source = geometry.get("preferredAimGeometryType")
            return {
                "canvasX": x,
                "canvasY": y,
                "source": source,
            }
    return None


def candidate_profile(candidate: dict) -> str | None:
    value = candidate.get("profileId") or candidate.get("profile")
    return str(value) if value else None


def filter_candidates(candidates: list[dict], *, profile: str | None = None) -> list[dict]:
    if not profile:
        return list(candidates)
    wanted = profile.lower()
    filtered = []
    for candidate in candidates:
        profile_id = candidate_profile(candidate)
        if profile_id is None or profile_id.lower() == wanted:
            filtered.append(candidate)
    return filtered


def best_sort_key(candidate: dict) -> tuple:
    quality = as_number(candidate.get("qualityScore")) or 0.0
    score = as_number(candidate.get("score")) or 0.0
    rank = as_number(candidate.get("rank"))
    distance = candidate_distance(candidate)
    return (
        *woodcutting_resource_preference_key(candidate),
        -quality,
        -score,
        rank if rank is not None else 999999,
        distance if distance is not None else 999999,
    )


def nearest_sort_key(candidate: dict) -> tuple:
    distance = candidate_distance(candidate)
    quality = as_number(candidate.get("qualityScore")) or 0.0
    score = as_number(candidate.get("score")) or 0.0
    return (distance if distance is not None else 999999, -quality, -score)


def select_candidates(candidates: list[dict], class_id: str, *, max_distance: float | None = None, profile: str | None = None) -> list[dict]:
    matches = [candidate for candidate in filter_candidates(candidates, profile=profile) if candidate_matches_class(candidate, class_id)]
    if max_distance is not None:
        matches = [candidate for candidate in matches if candidate_distance(candidate) is not None and candidate_distance(candidate) <= max_distance]
    return matches


def nearest_candidate(candidates: list[dict], class_id: str, max_distance: float | None = None, profile: str | None = None) -> dict | None:
    matches = select_candidates(candidates, class_id, max_distance=max_distance, profile=profile)
    return min(matches, key=nearest_sort_key) if matches else None


def best_candidate(candidates: list[dict], class_id: str, max_distance: float | None = None, profile: str | None = None) -> dict | None:
    matches = select_candidates(candidates, class_id, max_distance=max_distance, profile=profile)
    return min(matches, key=best_sort_key) if matches else None


def freshness_info(context: dict, candidate: dict | None, freshness_ticks: int, freshness_ms: int) -> tuple[dict, list[str]]:
    warnings = []
    status = context["status"]
    baseline = context["baseline"]
    latest = latest_tick(context)
    tick = candidate_tick(candidate) if candidate else None
    tick_delta = latest - tick if isinstance(latest, int) and isinstance(tick, int) else None
    generated = parse_utc(first_value(status.get("generatedAtUtc"), baseline.get("generatedAtUtc")))
    age_ms = None
    if generated:
        age_ms = (datetime.now(timezone.utc) - generated).total_seconds() * 1000.0
    if tick_delta is None:
        warnings.append("candidate/latest tick freshness is unknown.")
    elif tick_delta > freshness_ticks:
        warnings.append(f"candidate tick is stale by {tick_delta} tick(s).")
    if age_ms is None:
        warnings.append("live processor file age is unknown.")
    elif age_ms > freshness_ms:
        warnings.append(f"live processor files are stale by {int(age_ms)} ms.")
    return {
        "candidateTick": tick,
        "latestTick": latest,
        "tickDelta": tick_delta,
        "liveFileAgeMillis": round(age_ms, 3) if age_ms is not None else None,
        "freshnessTickThreshold": freshness_ticks,
        "freshnessMillisThreshold": freshness_ms,
        "freshByTicks": tick_delta is not None and tick_delta <= freshness_ticks,
        "freshByMillis": age_ms is not None and age_ms <= freshness_ms,
    }, warnings


def liveness_interpretation(candidate: dict | None, context: dict) -> str:
    status = context.get("status") if isinstance(context.get("status"), dict) else {}
    live_state = candidate.get("targetLiveState") if isinstance(candidate, dict) else None
    if status.get("livenessDegraded") or status.get("livenessBudgetExceeded"):
        return "degraded"
    if live_state in ("recently_despawned", "depleted_or_stump", "stale", "changed"):
        return "degraded"
    if live_state == "live":
        return "direct"
    if live_state == "live_assumed":
        return "assumed"
    return "unknown"


def candidate_answer(candidate: dict | None, context: dict, freshness_ticks: int, freshness_ms: int) -> tuple[dict, str, float, list[str], list[str], list[str]]:
    reasons = []
    warnings = []
    missing = []
    if not candidate:
        return {}, "FAIL", 0.0, reasons, ["no matching candidate found."], ["candidate"]

    target = target_payload(candidate)
    aim = candidate_aim_point(candidate)
    freshness, freshness_warnings = freshness_info(context, candidate, freshness_ticks, freshness_ms)
    warnings.extend(freshness_warnings)
    on_screen = candidate.get("onScreen")
    geometry_available = candidate.get("geometryAvailable")
    ui_blocked = candidate.get("uiBlocked")
    live_state = candidate.get("targetLiveState")
    live_interpretation = liveness_interpretation(candidate, context)
    status_doc = context.get("status") if isinstance(context.get("status"), dict) else {}
    navigation = candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}
    direct_reachability = navigation.get("directReachability") if navigation else None

    if on_screen is True:
        reasons.append("candidate is on screen.")
    elif on_screen is False:
        warnings.append("candidate is off screen.")
    else:
        warnings.append("candidate onScreen is unknown.")
        missing.append("onScreen")

    if geometry_available is True:
        reasons.append("candidate has geometry.")
    elif geometry_available is False:
        warnings.append("candidate geometry is unavailable.")
    else:
        warnings.append("candidate geometry availability is unknown.")
        missing.append("geometryAvailable")

    if aim:
        reasons.append("candidate has an aim point telemetry field.")
    else:
        warnings.append("candidate has no aim point telemetry field.")
        missing.append("aimPoint")

    if ui_blocked is True:
        warnings.append("candidate aim point is UI-blocked.")
    elif ui_blocked is False:
        reasons.append("candidate is not UI-blocked.")
    else:
        warnings.append("candidate UI blocking is unknown.")

    if live_state in ("recently_despawned", "depleted_or_stump", "stale", "changed"):
        warnings.append(f"candidate target liveness is degraded: {live_state}.")
    elif live_state == "live":
        reasons.append("candidate target liveness is live.")
    elif live_state == "live_assumed" and live_interpretation == "assumed":
        reasons.append("target liveness assumed from current candidate data; no direct depletion delta seen.")
    elif live_state == "live_assumed":
        warnings.append("candidate target liveness is assumed but degraded by realtime liveness status.")
    else:
        if status_doc.get("livenessMode") == "off":
            warnings.append("candidate target liveness is unknown because liveness mode is off.")
        else:
            warnings.append("candidate target liveness is unknown.")
        missing.append("targetLiveState")

    if direct_reachability == "reachable":
        reasons.append("candidate has a reachable local collision-window path observation.")
    elif direct_reachability == "blocked":
        warnings.append("candidate local collision-window reachability appears blocked.")
    elif direct_reachability == "unknown" and navigation.get("collisionWindowAvailable"):
        warnings.append("candidate local collision-window reachability is unknown.")
        missing.extend(navigation.get("missingNavigationFields") or ["localReachability"])

    if status_doc.get("budgetExceeded") is True:
        warnings.append("live processor budget was exceeded on the latest update.")
    if as_int(status_doc.get("writeFailureCount")) and as_int(status_doc.get("writeFailureCount")) > 0:
        warnings.append("live processor reported write failures.")
    if status_doc.get("sourceCapHit") is True:
        warnings.append("source scene object cap was hit.")
    if status_doc.get("sourceSceneKnowledgeComplete") is False:
        warnings.append("source scene knowledge is not complete.")

    canvas = context["baseline"].get("cameraViewport") if isinstance(context["baseline"].get("cameraViewport"), dict) else {}
    if aim and as_number(canvas.get("canvasWidth")) and as_number(canvas.get("canvasHeight")):
        width = as_number(canvas.get("canvasWidth"))
        height = as_number(canvas.get("canvasHeight"))
        if aim["canvasX"] < 0 or aim["canvasY"] < 0 or aim["canvasX"] > width or aim["canvasY"] > height:
            warnings.append("candidate aim point is outside known canvas bounds.")

    liveness_ok = live_state == "live" or (live_state == "live_assumed" and live_interpretation == "assumed")
    navigation_ok = direct_reachability in (None, "reachable") or not navigation.get("collisionWindowAvailable")
    pass_conditions = [
        freshness.get("freshByTicks"),
        freshness.get("freshByMillis"),
        on_screen is True,
        geometry_available is True,
        bool(aim),
        ui_blocked is False,
        liveness_ok,
        navigation_ok,
    ]
    if all(pass_conditions):
        status = "PASS"
    elif bool(aim) and on_screen is not False and geometry_available is not False:
        status = "WARN"
    else:
        status = "FAIL"

    confidence = 0.3
    confidence += 0.15 if freshness.get("freshByTicks") else 0.0
    confidence += 0.15 if freshness.get("freshByMillis") else 0.0
    confidence += 0.15 if on_screen is True else 0.0
    confidence += 0.15 if geometry_available is True else 0.0
    confidence += 0.1 if aim else 0.0
    confidence += 0.1 if ui_blocked is False else 0.0
    confidence += 0.1 if live_state == "live" else 0.06 if live_state == "live_assumed" and live_interpretation == "assumed" else 0.0
    confidence = min(1.0, confidence)

    geometry_payload = candidate.get("geometry") if isinstance(candidate.get("geometry"), dict) else {}
    preferred_geometry = candidate.get("preferredGeometryType") or geometry_payload.get("preferredAimGeometryType")
    answer = {
        "classId": candidate_class_id(candidate),
        "targetName": first_value(candidate.get("name"), target.get("name"), target.get("targetName")),
        "id": first_value(candidate.get("id"), target.get("id")),
        "rawId": first_value(candidate.get("rawId"), target.get("rawId")),
        "hash": first_value(candidate.get("hash"), target.get("hash")),
        "worldX": first_value(candidate.get("worldX"), target.get("worldX")),
        "worldY": first_value(candidate.get("worldY"), target.get("worldY")),
        "plane": first_value(candidate.get("plane"), target.get("plane")),
        "sceneX": first_value(candidate.get("sceneX"), target.get("sceneX")),
        "sceneY": first_value(candidate.get("sceneY"), target.get("sceneY")),
        "distanceTiles": candidate_distance(candidate),
        "onScreen": on_screen,
        "geometryAvailable": geometry_available,
        "uiBlocked": ui_blocked,
        "blockingUiRegions": candidate.get("blockingUiRegions") or [],
        "qualityScore": candidate.get("qualityScore"),
        "qualityTier": candidate.get("qualityTier"),
        "aimPoint": aim,
        "preferredGeometryType": preferred_geometry,
        "positiveSignals": candidate.get("positiveSignals") or [],
        "negativeSignals": candidate.get("negativeSignals") or [],
        "tick": candidate_tick(candidate),
        "freshness": freshness,
        "targetLiveState": live_state,
        "livenessInterpretation": live_interpretation,
        "targetLiveStateConfidence": candidate.get("targetLiveStateConfidence"),
        "targetLiveEvidence": candidate.get("targetLiveEvidence") or [],
        "navigation": navigation or None,
        "lastSeenTick": candidate.get("lastSeenTick"),
        "lastChangedTick": candidate.get("lastChangedTick"),
        "lastDespawnedTick": candidate.get("lastDespawnedTick"),
        "suppressUntilTick": candidate.get("suppressUntilTick"),
        "suppressReason": candidate.get("suppressReason"),
    }
    return answer, status, round(confidence, 3), reasons, warnings, missing


def direct_query_payload(context: dict, query_type: str, class_id: str, args) -> dict:
    candidate = (
        nearest_candidate(context["candidates"], class_id, args.max_distance, args.profile)
        if query_type == "nearest"
        else best_candidate(context["candidates"], class_id, args.max_distance, args.profile)
    )
    answer, status, confidence, reasons, warnings, missing = candidate_answer(
        candidate,
        context,
        args.freshness_ticks,
        args.freshness_ms,
    )
    warnings = list(context["warnings"]) + warnings
    missing = sorted(set(context["missingFields"] + missing))
    payload = {
        "schema": ANSWER_SCHEMA,
        "query": {
            "type": query_type,
            "classId": class_id,
            "profile": args.profile,
            "maxDistance": args.max_distance,
        },
        "answer": answer,
        "status": status,
        "confidence": confidence,
        "reasons": reasons,
        "warnings": warnings,
        "missingFields": missing,
        "sourceFiles": context["sourceFiles"],
        "generatedAtUtc": utc_now(),
    }
    # Backwards-compatible aliases for the earlier tiny helper.
    payload[query_type] = class_id
    payload["candidate"] = answer
    return payload


def reachability_candidate_record(candidate: dict, context: dict, args) -> dict:
    answer, _status, _confidence, _reasons, _warnings, _missing = candidate_answer(
        candidate,
        context,
        getattr(args, "freshness_ticks", DEFAULT_FRESHNESS_TICKS),
        getattr(args, "freshness_ms", DEFAULT_FRESHNESS_MS),
    )
    navigation = answer.get("navigation") if isinstance(answer.get("navigation"), dict) else {}
    return {
        "classId": answer.get("classId"),
        "targetName": answer.get("targetName"),
        "id": answer.get("id"),
        "rawId": answer.get("rawId"),
        "hash": answer.get("hash"),
        "worldX": answer.get("worldX"),
        "worldY": answer.get("worldY"),
        "plane": answer.get("plane"),
        "sceneX": answer.get("sceneX"),
        "sceneY": answer.get("sceneY"),
        "distanceTiles": answer.get("distanceTiles"),
        "onScreen": answer.get("onScreen"),
        "geometryAvailable": answer.get("geometryAvailable"),
        "targetLiveState": answer.get("targetLiveState"),
        "aimPoint": answer.get("aimPoint"),
        "directReachability": navigation.get("directReachability"),
        "targetInCollisionWindow": navigation.get("targetInCollisionWindow"),
        "pathLengthTiles": navigation.get("pathLengthTiles"),
        "interactionRadiusTiles": navigation.get("interactionRadiusTiles"),
        "reachabilityConfidence": navigation.get("reachabilityConfidence"),
        "reachabilityEvidence": navigation.get("reachabilityEvidence") or [],
        "missingNavigationFields": navigation.get("missingNavigationFields") or [],
    }


def reachability_payload(context: dict, class_id: str, args) -> dict:
    class_id = str(class_id or "tree")
    max_distance = getattr(args, "max_distance", None)
    profile = getattr(args, "profile", None)
    top_limit = max(1, int(getattr(args, "top", 10) or 10))
    candidates = select_candidates(context["candidates"], class_id, max_distance=max_distance, profile=profile)
    name_contains = getattr(args, "name_contains", None)
    id_filter = getattr(args, "id", None)
    if name_contains:
        candidates = [
            candidate
            for candidate in candidates
            if str(name_contains).lower() in str(candidate.get("name") or "").lower()
        ]
    if id_filter is not None:
        candidates = [
            candidate
            for candidate in candidates
            if first_value(candidate.get("rawId"), candidate.get("id")) == id_filter
        ]
    if getattr(args, "show_blocked", False) or getattr(args, "show_reachable", False) or getattr(args, "show_unknown", False):
        requested = set()
        if getattr(args, "show_blocked", False):
            requested.add("blocked")
        if getattr(args, "show_reachable", False):
            requested.add("reachable")
        if getattr(args, "show_unknown", False):
            requested.add("unknown")
        candidates = [
            candidate
            for candidate in candidates
            if ((candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}).get("directReachability") in requested)
        ]
    sorted_candidates = sorted(candidates, key=best_sort_key)
    navigation = navigation_readiness(context["navigation"], context["baseline"])
    player = context["baseline"].get("player") if isinstance(context["baseline"].get("player"), dict) else {}

    inside_window = 0
    outside_window = 0
    unknown_window = 0
    reachable = 0
    blocked = 0
    unknown = 0
    for candidate in candidates:
        nav = candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}
        in_window = nav.get("targetInCollisionWindow")
        if in_window is True:
            inside_window += 1
        elif in_window is False:
            outside_window += 1
        else:
            unknown_window += 1
        direct = nav.get("directReachability")
        if direct == "reachable":
            reachable += 1
        elif direct == "blocked":
            blocked += 1
        else:
            unknown += 1

    warnings = list(context["warnings"])
    missing = list(context["missingFields"])
    nav_status = navigation.get("status")
    if nav_status != "local":
        warnings.extend(navigation.get("warnings") or [])
        warning = navigation.get("warning")
        if warning:
            warnings.append(warning)
        warnings.append("collision window unavailable; candidate reachability remains unknown or summary-only.")
        missing.append("collisionWindow")
    if not candidates:
        warnings.append(f"no candidates found for classId {class_id}.")

    if not context.get("baseline") or "live_baseline_state" in missing:
        status = "FAIL"
    elif nav_status != "local" or not candidates:
        status = "WARN"
    else:
        status = "PASS"

    summary = {
        "classId": class_id,
        "candidateCount": len(candidates),
        "candidatesInsideCollisionWindow": inside_window,
        "candidatesOutsideCollisionWindow": outside_window,
        "candidatesWithUnknownCollisionWindow": unknown_window,
        "reachableCount": reachable,
        "blockedCount": blocked,
        "unknownCount": unknown,
    }
    return {
        "schema": REACHABILITY_SCHEMA,
        "status": status,
        "generatedAtUtc": utc_now(),
        "latestTick": latest_tick(context),
        "classId": class_id,
        "player": {
            "sceneX": first_value(navigation.get("playerSceneX"), player.get("sceneX")),
            "sceneY": first_value(navigation.get("playerSceneY"), player.get("sceneY")),
            "plane": first_value(navigation.get("plane"), player.get("plane")),
        },
        "navigationReadiness": navigation,
        "collisionWindow": {
            "available": navigation.get("collisionWindowAvailable"),
            "radius": navigation.get("collisionWindowRadius"),
            "bounds": navigation.get("collisionWindowBounds"),
            "tick": navigation.get("collisionWindowTick"),
            "hash": navigation.get("collisionWindowHash"),
        },
        "reachabilitySummary": summary,
        "candidates": [
            reachability_candidate_record(candidate, context, args)
            for candidate in sorted_candidates[:top_limit]
        ],
        "warnings": sorted(set(str(warning) for warning in warnings if warning)),
        "missingFields": sorted(set(str(field) for field in missing if field)),
        "missingCapabilities": sorted(set(navigation.get("missingCapabilities") or [])),
        "sourceFiles": context["sourceFiles"],
    }


def player_location_known(player: dict) -> bool:
    return player.get("worldX") is not None and player.get("worldY") is not None and player.get("plane") is not None


UNKNOWN_ACTIVITY_VALUES = {"", "unknown", "none", "null", "n/a", "na", "-1", "0"}


def is_unknown_activity_value(value: Any) -> bool:
    if value is None or value == -1:
        return True
    if isinstance(value, str):
        return value.strip().lower() in UNKNOWN_ACTIVITY_VALUES
    return False


def explicit_interacting_present(value: Any) -> bool:
    if isinstance(value, dict):
        return any(explicit_interacting_present(value.get(key)) for key in ("type", "name", "id", "index", "targetType", "targetName"))
    if is_unknown_activity_value(value):
        return False
    return bool(value)


def player_busy_summary(baseline: dict) -> dict:
    player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
    animation = player.get("animation")
    interacting = player.get("interacting")
    moving = player.get("isMoving")
    evidence = {
        "animation": animation,
        "interacting": interacting,
        "isMoving": moving,
        "poseAnimation": player.get("poseAnimation"),
        "idlePoseAnimation": player.get("idlePoseAnimation"),
    }
    if explicit_interacting_present(interacting):
        value = True
        reason = "explicit interacting target present."
    elif animation not in (None, -1, 0):
        value = True
        reason = "active animation present."
    elif animation in (-1, 0) and not explicit_interacting_present(interacting):
        value = False
        reason = "animation indicates no active animation and no explicit interacting target was observed."
    else:
        value = None
        reason = "animation/interacting fields are unavailable or unknown."
    return {"value": value, "evidence": evidence, "reason": reason}


def normalize_inventory_state(inventory: dict) -> dict:
    if not isinstance(inventory, dict):
        return {"known": False}
    normalized = dict(inventory)
    filled = as_int(normalized.get("filledSlots"))
    free = as_int(normalized.get("freeSlots"))
    slot_count = as_int(first_value(normalized.get("inventorySlotCount"), normalized.get("slotCount")))
    if slot_count is None and filled is not None and free is not None:
        slot_count = max(28, filled + free)
    if slot_count is None and normalized.get("known") is True:
        slot_count = 28
    if filled is None and slot_count is not None and free is not None:
        filled = max(0, slot_count - free)
    if free is None and slot_count is not None and filled is not None:
        free = max(0, slot_count - filled)
    elif slot_count is not None and filled is not None and free is not None and filled + free != slot_count:
        free = max(0, slot_count - filled)
    total_quantity = as_int(first_value(normalized.get("totalItemQuantity"), normalized.get("itemCount")))
    if total_quantity is None and isinstance(normalized.get("items"), list):
        total_quantity = sum(as_int(item.get("quantity")) or 1 for item in normalized["items"] if isinstance(item, dict))
    known = normalized.get("known")
    if known is None:
        known = any(value is not None for value in (free, filled, total_quantity, normalized.get("signature")))
    normalized.update(
        {
            "known": bool(known),
            "inventorySlotCount": slot_count,
            "slotCount": slot_count,
            "freeSlots": free,
            "filledSlots": filled,
            "itemCount": total_quantity,
            "totalItemQuantity": total_quantity,
            "inventoryFull": (free == 0) if free is not None else normalized.get("inventoryFull"),
        }
    )
    return normalized


def inventory_readiness(baseline: dict) -> dict:
    inventory = normalize_inventory_state(baseline.get("inventory") if isinstance(baseline.get("inventory"), dict) else {})
    known = bool(inventory.get("known"))
    return {
        "known": known,
        "inventorySlotCount": inventory.get("inventorySlotCount"),
        "freeSlots": inventory.get("freeSlots"),
        "filledSlots": inventory.get("filledSlots"),
        "itemCount": inventory.get("itemCount"),
        "totalItemQuantity": inventory.get("totalItemQuantity"),
        "inventoryFull": inventory.get("inventoryFull"),
        "signature": inventory.get("signature"),
    }


def navigation_readiness(navigation: dict, baseline: dict) -> dict:
    player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
    if not navigation:
        return {
            "status": "unknown",
            "collisionKnown": None,
            "currentPlaneKnown": player.get("plane") is not None,
            "playerTileKnown": player.get("sceneX") is not None and player.get("sceneY") is not None,
            "reachabilityComputed": False,
            "warning": "collision/navigation data unavailable; reachability questions cannot be answered yet",
        }
    collision_known = navigation.get("collisionKnown")
    collision_window_available = bool(navigation.get("collisionWindowAvailable"))
    player_tile_known = navigation.get("playerTileKnown")
    if player_tile_known is None:
        player_tile_known = navigation.get("playerSceneX") is not None and navigation.get("playerSceneY") is not None
    notes = navigation.get("notes") or []
    warnings = list(navigation.get("warnings") or [])
    missing_capabilities = []
    if collision_known and collision_window_available:
        warnings.append("local collision window available; full pathfinding is not implemented")
        missing_capabilities.append("fullPathfinding")
    elif collision_known:
        warnings.append("collision summary available; full reachability/pathing not implemented")
        missing_capabilities.append("fullPathfinding")
        if not navigation.get("fullCollisionGridAvailable"):
            missing_capabilities.append("collisionGridPathing")
    else:
        warnings.append("collision/navigation data unavailable; reachability questions cannot be answered yet")
        missing_capabilities.append("collisionSummary")
    return {
        "status": "local" if collision_known and collision_window_available else "summary" if collision_known else "unknown",
        "collisionKnown": collision_known,
        "collisionWindowAvailable": collision_window_available,
        "collisionWindowRadius": navigation.get("collisionWindowRadius"),
        "collisionWindowBounds": navigation.get("collisionWindowBounds"),
        "collisionWindowHash": navigation.get("collisionWindowHash"),
        "collisionWindowTick": navigation.get("collisionWindowTick"),
        "plane": navigation.get("plane"),
        "currentPlaneKnown": navigation.get("plane") is not None or player.get("plane") is not None,
        "playerSceneX": navigation.get("playerSceneX"),
        "playerSceneY": navigation.get("playerSceneY"),
        "playerTileKnown": player_tile_known,
        "mapBounds": navigation.get("mapBounds"),
        "mapWidth": navigation.get("mapWidth"),
        "mapHeight": navigation.get("mapHeight"),
        "blockedMovementTileCount": navigation.get("blockedMovementTileCount"),
        "blockedFullTileCount": navigation.get("blockedFullTileCount"),
        "collisionHash": navigation.get("collisionHash") or navigation.get("signature"),
        "obstaclesKnown": navigation.get("obstaclesKnown"),
        "reachabilityComputed": bool(navigation.get("reachabilityComputed")),
        "fullCollisionGridAvailable": bool(navigation.get("fullCollisionGridAvailable")),
        "directReachability": "per_candidate" if collision_window_available else "unknown",
        "missingCapabilities": sorted(set(missing_capabilities)),
        "warnings": sorted(set(str(warning) for warning in warnings if warning)),
        "notes": notes,
    }


def candidate_identity(candidate: dict | None) -> str | None:
    if not candidate:
        return None
    target = target_payload(candidate)
    return "|".join(
        str(part)
        for part in (
            first_value(candidate.get("objectKey"), target.get("objectKey")),
            first_value(candidate.get("targetKey"), target.get("targetKey")),
            first_value(candidate.get("id"), candidate.get("rawId"), target.get("id"), target.get("rawId")),
            first_value(candidate.get("worldX"), target.get("worldX")),
            first_value(candidate.get("worldY"), target.get("worldY")),
            first_value(candidate.get("plane"), target.get("plane")),
        )
        if part is not None
    ) or None


def aim_jitter(candidates: list[dict]) -> float | None:
    points = [candidate_aim_point(candidate) for candidate in candidates]
    points = [point for point in points if point]
    if len(points) < 2:
        return None
    base = points[0]
    return round(max(math.hypot(point["canvasX"] - base["canvasX"], point["canvasY"] - base["canvasY"]) for point in points[1:]), 3)


def stability_for(candidates: list[dict]) -> dict:
    by_tick: dict[int, list[dict]] = defaultdict(list)
    for candidate in candidates:
        tick = candidate_tick(candidate)
        if tick is not None:
            by_tick[tick].append(candidate)
    if len(by_tick) < 2:
        return {
            "candidateStable": None,
            "reason": "candidate stability unknown; run rolling candidate output to evaluate stability.",
            "recentTickCount": len(by_tick),
        }
    best_by_tick = [min(values, key=best_sort_key) for _tick, values in sorted(by_tick.items())]
    identity_counts = Counter(candidate_identity(candidate) or "unknown" for candidate in best_by_tick)
    top_identity, same_count = identity_counts.most_common(1)[0]
    same_candidates = [candidate for candidate in best_by_tick if candidate_identity(candidate) == top_identity]
    jitter = aim_jitter(same_candidates)
    stable = same_count >= max(2, int(len(best_by_tick) * 0.7)) and (jitter is None or jitter <= 10)
    return {
        "candidateSeenAcrossRecentTicks": dict(identity_counts.most_common(5)),
        "sameBestCandidateTicks": same_count,
        "aimPointJitterPixels": jitter,
        "candidateStable": stable,
        "reason": "same best candidate persisted across recent ticks." if stable else "best candidate flickered or aim point moved noticeably.",
        "recentTickCount": len(by_tick),
    }


def tree_candidates(candidates: list[dict], profile: str | None = None) -> list[dict]:
    return [candidate for candidate in filter_candidates(candidates, profile=profile) if is_tree_like(candidate)]


def woodcutting_task_payload(context: dict, args) -> dict:
    warnings = list(context["warnings"])
    missing = list(context["missingFields"])
    baseline = context["baseline"]
    activity_doc = context["activity"]
    status_doc = context["status"]
    player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
    candidates = tree_candidates(context["candidates"], profile=args.profile)
    visible = [candidate for candidate in candidates if candidate.get("onScreen") is True]
    nearest_tree = min(candidates, key=nearest_sort_key) if candidates else None
    best_tree = min(candidates, key=best_sort_key) if candidates else None
    include_top = bool(getattr(args, "verbose", False)) or getattr(args, "fields", "compact") in {"normal", "full"}
    top_limit = max(1, int(getattr(args, "top", 3) or 3))
    top_tree_candidates = [
        candidate_answer(candidate, context, args.freshness_ticks, args.freshness_ms)[0]
        for candidate in sorted(candidates, key=best_sort_key)[:top_limit]
    ] if include_top else []
    nearest_oak = nearest_candidate(candidates, "oak_tree")
    nearest_willow = nearest_candidate(candidates, "willow_tree")
    best_answer, best_status, _confidence, best_reasons, best_warnings, best_missing = candidate_answer(
        best_tree,
        context,
        args.freshness_ticks,
        args.freshness_ms,
    )
    warnings.extend(best_warnings)
    missing.extend(best_missing)
    freshness, freshness_warnings = freshness_info(context, best_tree, args.freshness_ticks, args.freshness_ms)
    warnings.extend(freshness_warnings)
    busy = (activity_doc.get("activity") or {}).get("playerAppearsBusy") if isinstance((activity_doc.get("activity") or {}), dict) else None
    busy = player_busy_summary(baseline) if busy is None else busy
    inventory_source = activity_doc.get("inventoryState") if isinstance(activity_doc.get("inventoryState"), dict) else activity_doc.get("inventory")
    inventory = (
        normalize_inventory_state(inventory_source)
        if isinstance(inventory_source, dict)
        else inventory_readiness(baseline)
    )
    activity_state = activity_doc.get("activityState") if isinstance(activity_doc.get("activityState"), dict) else activity_doc.get("activity")
    activity_state = activity_state if isinstance(activity_state, dict) else {}
    woodcutting_state = activity_doc.get("woodcuttingState") if isinstance(activity_doc.get("woodcuttingState"), dict) else {}
    target_liveness = activity_doc.get("targetLiveness") if isinstance(activity_doc.get("targetLiveness"), dict) else {}
    recent_inventory_deltas = activity_doc.get("recentInventoryDeltas")
    if not isinstance(recent_inventory_deltas, list):
        recent_inventory_deltas = inventory.get("recentItemDeltas") if isinstance(inventory.get("recentItemDeltas"), list) else []
    recent_activity_events = activity_doc.get("recentActivityEvents") if isinstance(activity_doc.get("recentActivityEvents"), list) else []
    recent_events = events_payload(context, getattr(args, "events", 5) or 5).get("events", [])
    navigation = navigation_readiness(context["navigation"], baseline)
    reachability_report = reachability_payload(context, "tree", args)

    if not player_location_known(player):
        warnings.append("no player location.")
        missing.append("player.location")
    if not candidates:
        warnings.append("no tree-like live candidates.")
    if not inventory["known"]:
        warnings.append("no inventory/status information available for woodcutting context.")
    if not activity_doc:
        warnings.append("live_activity_state missing; activity/inventory/liveness checks are degraded.")
        missing.append("live_activity_state")
    if not target_liveness:
        warnings.append("target liveness state unavailable.")
        missing.append("targetLiveness")
    if navigation.get("status") == "unknown":
        warnings.extend(navigation.get("warnings") or [navigation.get("warning") or "collision/navigation data unavailable; reachability questions cannot be answered yet"])
    elif navigation.get("status") == "summary":
        warnings.extend(navigation.get("warnings") or [])
    if not baseline.get("latestFramePath"):
        warnings.append("no frame path in live baseline.")
    if status_doc.get("sourceCapHit") is True:
        warnings.append("source scene object cap was hit.")
    if status_doc.get("sourceSceneKnowledgeComplete") is False:
        warnings.append("source scene knowledge is not complete.")

    core_ok = bool(player_location_known(player) and candidates and best_answer.get("aimPoint") and freshness.get("freshByTicks") and freshness.get("freshByMillis"))
    if not candidates or not baseline:
        overall = "FAIL"
    elif best_status == "PASS" and core_ok:
        overall = "PASS" if inventory["known"] and navigation.get("currentPlaneKnown") else "WARN"
    else:
        overall = "WARN" if core_ok or candidates else "FAIL"
    if overall == "PASS" and navigation.get("directReachability") == "unknown":
        overall = "WARN"

    return {
        "schema": TASK_SCHEMA,
        "task": "woodcutting",
        "status": overall,
        "canAnswerCoreQuestions": core_ok,
        "observations": {
            "whereIsPlayerKnown": player_location_known(player),
            "treeCandidatesVisible": bool(visible),
            "bestCandidateHasAimPoint": bool(best_answer.get("aimPoint")),
            "bestCandidateUiBlocked": best_answer.get("uiBlocked"),
            "bestCandidateLiveState": best_answer.get("targetLiveState"),
            "liveFeedFresh": bool(freshness.get("freshByTicks") and freshness.get("freshByMillis")),
            "playerAppearsBusy": busy,
        },
        "candidateSummary": {
            "visibleTreeCandidateCount": len(visible),
            "treeCandidateCount": len(candidates),
            "nearestTree": candidate_answer(nearest_tree, context, args.freshness_ticks, args.freshness_ms)[0] if nearest_tree else None,
            "bestTree": best_answer,
            "topTreeCandidates": top_tree_candidates,
            "nearestOakTree": candidate_answer(nearest_oak, context, args.freshness_ticks, args.freshness_ms)[0] if nearest_oak else None,
            "nearestWillowTree": candidate_answer(nearest_willow, context, args.freshness_ticks, args.freshness_ms)[0] if nearest_willow else None,
            "bestCandidateAimPoint": best_answer.get("aimPoint"),
            "stability": stability_for(candidates),
        },
        "stateSummary": {
            "player": player,
            "playerAppearsBusy": busy,
            "inventoryKnown": inventory["known"],
            "inventory": inventory,
            "recentInventoryDeltas": recent_inventory_deltas,
            "recentActivityEvents": recent_activity_events,
            "freshness": freshness,
            "sourceSceneKnowledgeComplete": status_doc.get("sourceSceneKnowledgeComplete"),
            "sourceCapHit": status_doc.get("sourceCapHit"),
        },
        "activityState": activity_state,
        "inventoryState": inventory,
        "recentInventoryDeltas": recent_inventory_deltas,
        "recentActivityEvents": recent_activity_events,
        "recentEvents": recent_events,
        "targetLivenessState": target_liveness,
        "taskReadiness": {
            "bestCandidateLiveState": best_answer.get("targetLiveState"),
            "bestCandidateUsableTelemetry": best_status,
            "woodcuttingState": woodcutting_state.get("woodcuttingState"),
            "sourceFresh": bool(freshness.get("freshByTicks") and freshness.get("freshByMillis")),
        },
        "woodcuttingState": woodcutting_state,
        "navigationReadiness": navigation,
        "reachabilitySummary": reachability_report.get("reachabilitySummary"),
        "reachabilityCandidates": reachability_report.get("candidates") or [],
        "missingCapabilities": [
            capability
            for capability, missing_capability in (
                ("collisionSummary", navigation.get("status") == "unknown"),
                ("fullPathfinding", navigation.get("status") in {"summary", "local"}),
                ("collisionGridPathing", navigation.get("status") == "summary" and not navigation.get("fullCollisionGridAvailable")),
                ("inventoryDeltas", not inventory.get("recentItemDeltas")),
                ("animationFrame", player.get("animationFrame") is None),
                ("explicitMovementState", player.get("isMoving") is None),
            )
            if missing_capability
        ],
        "warnings": sorted(set(str(warning) for warning in warnings if warning)),
        "missingFields": sorted(set(str(field) for field in missing if field)),
        "sourceFiles": context["sourceFiles"],
        "generatedAtUtc": utc_now(),
        "reasons": best_reasons,
    }


def summary_payload(context: dict, args) -> dict:
    status = context["status"]
    baseline = context["baseline"]
    activity = context["activity"]
    context_index = context["context"]
    candidates = filter_candidates(context["candidates"], profile=args.profile)
    player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
    freshness, freshness_warnings = freshness_info(context, candidates[0] if candidates else None, args.freshness_ticks, args.freshness_ms)
    counts_by_class = Counter(candidate_class_id(candidate) for candidate in candidates)
    counts_by_category = Counter(str(first_value(candidate.get("category"), candidate.get("targetCategory"), target_payload(candidate).get("targetCategory"), "unknown")) for candidate in candidates)
    counts_by_quality = Counter(str(candidate.get("qualityTier") or "unknown") for candidate in candidates)
    warnings = list(context["warnings"]) + freshness_warnings
    if status.get("budgetExceeded") is True:
        warnings.append("live processor budgetExceeded is true.")
    if as_int(status.get("writeFailureCount")) and as_int(status.get("writeFailureCount")) > 0:
        warnings.append("live processor reported write failures.")
    if status.get("sourceCapHit") is True:
        warnings.append("source scene object cap was hit.")
    return {
        "schema": SUMMARY_SCHEMA,
        "sessionPath": str(context["session"]),
        "generatedAtUtc": utc_now(),
        "latestTick": latest_tick(context),
        "liveProcessorFreshness": freshness,
        "activeProfile": args.profile or status.get("profile") or context_index.get("activeProfile"),
        "player": {
            "worldX": player.get("worldX"),
            "worldY": player.get("worldY"),
            "plane": player.get("plane"),
            "sceneX": player.get("sceneX"),
            "sceneY": player.get("sceneY"),
            "localX": player.get("localX"),
            "localY": player.get("localY"),
        },
        "playerState": {
            "animation": player.get("animation"),
            "interacting": player.get("interacting"),
            "isMoving": player.get("isMoving"),
            "healthRatio": player.get("healthRatio"),
            "healthScale": player.get("healthScale"),
            "runEnergy": player.get("runEnergy") or baseline.get("player", {}).get("runEnergy") if isinstance(baseline.get("player"), dict) else None,
            "inventory": baseline.get("inventory") if isinstance(baseline.get("inventory"), dict) else {},
        },
        "candidateCount": len(candidates),
        "candidateCountsByClassId": dict(counts_by_class.most_common()),
        "candidateCountsByCategory": dict(counts_by_category.most_common()),
        "candidateCountsByQualityTier": dict(counts_by_quality.most_common()),
        "bestCandidateByClassId": context_index.get("bestCandidateByClassId") or {},
        "nearestCandidateByClassId": context_index.get("nearestCandidateByClassId") or {},
        "sourceSceneKnowledgeComplete": status.get("sourceSceneKnowledgeComplete"),
        "sourceCapHit": status.get("sourceCapHit"),
        "budgetExceeded": status.get("budgetExceeded"),
        "writeFailures": status.get("writeFailureCount"),
        "warningCount": len(warnings) + int(status.get("warningCount") or 0),
        "liveFileAgeMillis": freshness.get("liveFileAgeMillis"),
        "warnings": sorted(set(str(warning) for warning in warnings if warning)),
        "missingFields": sorted(set(context["missingFields"])),
        "sourceFiles": context["sourceFiles"],
    }


def status_for_checks(checks: list[dict]) -> str:
    if any(check["status"] == "FAIL" for check in checks):
        return "FAIL"
    if any(check["status"] == "WARN" for check in checks):
        return "WARN"
    return "PASS"


def check(name: str, ok: bool | None, reason: str) -> dict:
    if ok is True:
        status = "PASS"
    elif ok is False:
        status = "FAIL"
    else:
        status = "WARN"
    return {"name": name, "status": status, "reason": reason}


def self_test_payload(context: dict, args) -> dict:
    status = context["status"]
    baseline = context["baseline"]
    activity = context["activity"]
    context_index = context["context"]
    candidates = context["candidates"]
    active_profile = args.profile or status.get("profile")
    nearest_tree = nearest_candidate(candidates, "tree", profile=args.profile)
    best_tree = best_candidate(candidates, "tree", profile=args.profile)
    freshness, freshness_warnings = freshness_info(context, best_tree or (candidates[0] if candidates else None), args.freshness_ticks, args.freshness_ms)
    checks = [
        check("baseline readable", bool(baseline), "live_baseline_state.json was loaded."),
        check("status readable", bool(status), "live_status.json was loaded."),
        check("context index readable", bool(context_index), "live_context_index.json was loaded."),
        check("candidates readable", context["paths"]["candidates"].exists(), "live_candidates.jsonl was present."),
        check("activity state readable", bool(activity), "live_activity_state.json was loaded."),
        check("latest tick known", latest_tick(context) is not None, "latest tick is available in live status/baseline."),
        check("candidate count > 0", len(candidates) > 0, f"{len(candidates)} candidate record(s) loaded."),
        check("live data fresh", freshness.get("freshByTicks") and freshness.get("freshByMillis"), "candidate and live files are inside freshness thresholds."),
        check("no write failures", not (as_int(status.get("writeFailureCount")) and as_int(status.get("writeFailureCount")) > 0), "live_status writeFailureCount is zero."),
        check("source cap not hit", status.get("sourceCapHit") is not True, "sourceCapHit is not true."),
        check("inventory state readable", bool((activity.get("inventory") if isinstance(activity, dict) else None)), "inventory state is available or reported unknown."),
        check("target liveness state readable", bool((activity.get("targetLiveness") if isinstance(activity, dict) else None)), "target liveness state is available."),
    ]
    player = activity.get("player") if isinstance(activity.get("player"), dict) else {}
    checks.append(check("player animation/interacting fields", player.get("animation") is not None or player.get("interacting"), "activity state has animation or interacting fields."))
    inventory = activity.get("inventory") if isinstance(activity.get("inventory"), dict) else {}
    checks.append(check("inventory delta tracking present", "recentItemDeltas" in inventory, "inventory state has recentItemDeltas."))
    liveness = activity.get("targetLiveness") if isinstance(activity.get("targetLiveness"), dict) else {}
    checks.append(check("recently unavailable cache present", "recentlyUnavailableCount" in liveness, "target liveness has recentlyUnavailableCount."))
    if str(active_profile or "").lower() == "woodcutting":
        checks.extend(
            [
                check("nearest tree candidate", nearest_tree is not None, "woodcutting profile has a nearest tree candidate."),
                check("best tree candidate", best_tree is not None, "woodcutting profile has a best tree candidate."),
                check("best tree aim point", candidate_aim_point(best_tree) is not None if best_tree else False, "best tree has an aim point."),
                check("best tree liveness", bool(best_tree and best_tree.get("targetLiveState")), "best tree has target liveness state."),
                check("woodcutting activity state", bool(activity.get("woodcuttingState") if isinstance(activity, dict) else None), "woodcuttingState is available."),
            ]
        )
    return {
        "schema": SELF_TEST_SCHEMA,
        "status": status_for_checks(checks),
        "checks": checks,
        "warnings": sorted(set(context["warnings"] + freshness_warnings)),
        "missingFields": sorted(set(context["missingFields"])),
        "sourceFiles": context["sourceFiles"],
        "generatedAtUtc": utc_now(),
    }


def activity_payload(context: dict) -> dict:
    activity = context["activity"]
    if not activity:
        return {
            "schema": "live_activity_answer.v1",
            "status": "WARN",
            "activityState": {},
            "warnings": context["warnings"] + ["live_activity_state missing."],
            "missingFields": sorted(set(context["missingFields"] + ["live_activity_state"])),
            "sourceFiles": context["sourceFiles"],
            "generatedAtUtc": utc_now(),
        }
    return {
        "schema": "live_activity_answer.v1",
        "status": "PASS",
        "latestTick": activity.get("latestTick"),
        "activityState": activity.get("activityState") or activity.get("activity") or {},
        "woodcuttingState": activity.get("woodcuttingState") or {},
        "recentActivityEvents": activity.get("recentActivityEvents") or [],
        "player": activity.get("player") or {},
        "warnings": context["warnings"],
        "missingFields": context["missingFields"],
        "sourceFiles": context["sourceFiles"],
        "generatedAtUtc": utc_now(),
    }


def inventory_payload(context: dict) -> dict:
    activity = context["activity"]
    inventory = activity.get("inventoryState") if isinstance(activity.get("inventoryState"), dict) else activity.get("inventory") if isinstance(activity.get("inventory"), dict) else {}
    if not inventory:
        baseline = context["baseline"]
        inventory = baseline.get("inventory") if isinstance(baseline.get("inventory"), dict) else {}
    inventory = normalize_inventory_state(inventory)
    known = inventory.get("known") if "known" in inventory else bool(inventory)
    inventory_warnings = inventory.get("warnings") if isinstance(inventory.get("warnings"), list) else []
    return {
        "schema": "live_inventory_answer.v1",
        "status": "PASS" if known else "WARN",
        "inventoryState": inventory,
        "inventoryKnown": known,
        "inventoryFull": inventory.get("inventoryFull") if isinstance(inventory, dict) else None,
        "recentInventoryDeltas": inventory.get("recentItemDeltas") if isinstance(inventory, dict) else [],
        "warnings": context["warnings"] + ([] if known else ["inventory state is unknown."]) + inventory_warnings,
        "missingFields": context["missingFields"] + ([] if known else ["inventory"]),
        "sourceFiles": context["sourceFiles"],
        "generatedAtUtc": utc_now(),
    }


def compact_event(event: dict) -> dict:
    return {
        key: event.get(key)
        for key in (
            "generatedAtUtc",
            "tick",
            "eventType",
            "severity",
            "summary",
            "relatedCandidate",
            "previousValue",
            "currentValue",
            "profile",
        )
        if key in event
    }


def events_payload(context: dict, limit: int = 5) -> dict:
    events = context.get("events") if isinstance(context.get("events"), list) else []
    limit = max(0, int(limit or 0))
    recent = events[-limit:] if limit else []
    return {
        "schema": EVENTS_SCHEMA,
        "status": "PASS" if events else "WARN",
        "latestTick": latest_tick(context),
        "eventCount": len(events),
        "events": [compact_event(event) for event in recent if isinstance(event, dict)],
        "warnings": context["warnings"] + ([] if events else ["live event timeline is empty or unavailable."]),
        "missingFields": context["missingFields"],
        "sourceFiles": context["sourceFiles"],
        "generatedAtUtc": utc_now(),
    }


def liveness_payload(context: dict) -> dict:
    activity = context["activity"]
    liveness = activity.get("targetLiveness") if isinstance(activity.get("targetLiveness"), dict) else {}
    status_doc = context["status"]
    if not liveness:
        liveness = {
            "recentlyUnavailableCount": status_doc.get("recentlyUnavailableCount"),
            "recentlyDepletedCount": status_doc.get("recentlyDepletedCount"),
            "suppressedCandidateCount": status_doc.get("candidatesSuppressedByLiveness"),
            "candidatesSuppressedAsDepleted": status_doc.get("candidatesSuppressedAsDepleted"),
            "previousBestCandidate": status_doc.get("previousBestCandidate"),
            "currentBestCandidate": status_doc.get("currentBestCandidate"),
            "bestCandidateChanged": status_doc.get("bestCandidateChanged"),
            "bestCandidateChangeReason": status_doc.get("bestCandidateChangeReason"),
        }
    if "livenessInterpretation" not in liveness:
        live_state = liveness.get("bestCandidateLiveState") or liveness.get("activeCandidateLiveState")
        if status_doc.get("livenessDegraded") or status_doc.get("livenessBudgetExceeded"):
            liveness["livenessInterpretation"] = "degraded"
        elif live_state in ("recently_despawned", "depleted_or_stump", "stale", "changed"):
            liveness["livenessInterpretation"] = "degraded"
        elif live_state == "live":
            liveness["livenessInterpretation"] = "direct"
        elif live_state == "live_assumed":
            liveness["livenessInterpretation"] = "assumed"
        else:
            liveness["livenessInterpretation"] = "unknown"
    known = bool(liveness) and any(
        value is not None
        for key, value in liveness.items()
        if key != "livenessInterpretation"
    )
    return {
        "schema": "live_liveness_answer.v1",
        "status": "PASS" if known else "WARN",
        "targetLivenessState": liveness,
        "candidateLiveStateCounts": status_doc.get("candidateStats", {}).get("candidateLiveStateCounts") if isinstance(status_doc.get("candidateStats"), dict) else None,
        "warnings": context["warnings"] + ([] if known else ["target liveness state is unknown."]),
        "missingFields": context["missingFields"] + ([] if known else ["targetLiveness"]),
        "sourceFiles": context["sourceFiles"],
        "generatedAtUtc": utc_now(),
    }


def print_summary_human(payload: dict) -> None:
    print("Live Context Summary")
    print(f"session: {payload.get('sessionPath')}")
    print(f"latest tick: {payload.get('latestTick', 'unknown')}")
    print(f"active profile: {payload.get('activeProfile') or 'unknown'}")
    print(f"candidate count: {payload.get('candidateCount')}")
    player = payload.get("player") or {}
    print(f"player world: {player.get('worldX', 'unknown')},{player.get('worldY', 'unknown')} plane={player.get('plane', 'unknown')}")
    print(f"player scene: {player.get('sceneX', 'unknown')},{player.get('sceneY', 'unknown')}")
    print(f"source complete: {payload.get('sourceSceneKnowledgeComplete')} capHit={payload.get('sourceCapHit')}")
    print(f"budgetExceeded: {payload.get('budgetExceeded')} writeFailures={payload.get('writeFailures')}")
    print("counts by class:", payload.get("candidateCountsByClassId") or {})
    print("counts by quality:", payload.get("candidateCountsByQualityTier") or {})
    if payload.get("warnings"):
        print("warnings:")
        for warning in payload["warnings"]:
            print(f"- {warning}")


def print_answer_human(payload: dict) -> None:
    print(f"Live Context Answer: {payload.get('status')} confidence={payload.get('confidence')}")
    print(f"query: {payload.get('query')}")
    answer = payload.get("answer") or {}
    if answer:
        print(f"candidate: {answer.get('targetName')} class={answer.get('classId')} quality={answer.get('qualityTier')} score={answer.get('qualityScore')}")
        print(f"distance: {answer.get('distanceTiles')} onScreen={answer.get('onScreen')} geometry={answer.get('geometryAvailable')} uiBlocked={answer.get('uiBlocked')}")
        print(f"aimPoint: {answer.get('aimPoint')}")
    if payload.get("warnings"):
        print("warnings:")
        for warning in payload["warnings"]:
            print(f"- {warning}")


def print_task_human(payload: dict, args) -> None:
    print(f"Live Task Context: {payload.get('task')} status={payload.get('status')}")
    print(f"can answer core questions: {payload.get('canAnswerCoreQuestions')}")
    state = payload.get("stateSummary") or {}
    player = state.get("player") or {}
    if player:
        print(f"player: world={player.get('worldX')},{player.get('worldY')} plane={player.get('plane')}")
    activity = payload.get("activityState") or {}
    woodcutting = payload.get("woodcuttingState") or {}
    if activity or woodcutting:
        print(f"activity: {activity.get('apparentState', 'unknown')} woodcutting={woodcutting.get('woodcuttingState', 'unknown')}")
    inventory = payload.get("inventoryState") or {}
    if inventory:
        print(f"inventory: known={inventory.get('known')} free={inventory.get('freeSlots')} full={inventory.get('inventoryFull')} changed={inventory.get('changedRecently')}")
    summary = payload.get("candidateSummary") or {}
    print(f"visible tree candidates: {summary.get('visibleTreeCandidateCount')}")
    best = summary.get("bestTree") or {}
    if best:
        print(
            "best tree: "
            f"{best.get('classId')}/{best.get('targetName')} id={best.get('id')} "
            f"distance={best.get('distanceTiles')} onScreen={best.get('onScreen')} "
            f"geometry={best.get('geometryAvailable')} uiBlocked={best.get('uiBlocked')} "
            f"aim={best.get('aimPoint')} liveState={best.get('targetLiveState')}"
        )
    if getattr(args, "verbose", False) or getattr(args, "fields", "compact") in {"normal", "full"}:
        top = (summary.get("topTreeCandidates") or [])[: max(1, int(getattr(args, "top", 3) or 3))]
        if top:
            print("top candidates:")
            for index, candidate in enumerate(top, start=1):
                print(f"{index}. {candidate.get('targetName')} distance={candidate.get('distanceTiles')} quality={candidate.get('qualityTier')} aim={candidate.get('aimPoint')}")
    navigation = payload.get("navigationReadiness") or {}
    print(f"navigation readiness: {navigation.get('status')} collisionKnown={navigation.get('collisionKnown')}")
    missing = payload.get("missingCapabilities") or []
    if missing:
        print("missing capabilities:", ", ".join(str(item) for item in missing))
    if payload.get("warnings"):
        print("warnings:")
        for warning in payload["warnings"][:20]:
            print(f"- {warning}")
        if len(payload["warnings"]) > 20:
            print(f"- ... {len(payload['warnings']) - 20} more warnings; use --verbose or --fields full for details")


def print_reachability_human(payload: dict) -> None:
    summary = payload.get("reachabilitySummary") or {}
    player = payload.get("player") or {}
    window = payload.get("collisionWindow") or {}
    print(f"Candidate Reachability QA: class={payload.get('classId')} status={payload.get('status')}")
    print(f"latest tick: {payload.get('latestTick')}")
    print(f"player scene: {player.get('sceneX', 'unknown')},{player.get('sceneY', 'unknown')} plane={player.get('plane', 'unknown')}")
    print(f"collision window: available={window.get('available')} radius={window.get('radius')} bounds={window.get('bounds')}")
    print(
        "counts: "
        f"candidates={summary.get('candidateCount')} "
        f"inside={summary.get('candidatesInsideCollisionWindow')} "
        f"outside={summary.get('candidatesOutsideCollisionWindow')} "
        f"reachable={summary.get('reachableCount')} "
        f"blocked={summary.get('blockedCount')} "
        f"unknown={summary.get('unknownCount')}"
    )
    candidates = payload.get("candidates") or []
    if candidates:
        print("top candidates:")
        for index, candidate in enumerate(candidates, start=1):
            aim = candidate.get("aimPoint") or {}
            aim_text = f"{aim.get('canvasX')},{aim.get('canvasY')}" if aim else "unknown"
            print(
                f"{index}. {candidate.get('classId')}/{candidate.get('targetName')} "
                f"id={candidate.get('id')} world={candidate.get('worldX')},{candidate.get('worldY')} "
                f"scene={candidate.get('sceneX')},{candidate.get('sceneY')} "
                f"distance={candidate.get('distanceTiles')} onScreen={candidate.get('onScreen')} "
                f"geometry={candidate.get('geometryAvailable')} live={candidate.get('targetLiveState')} "
                f"aim={aim_text} reachability={candidate.get('directReachability')} "
                f"pathLength={candidate.get('pathLengthTiles')} confidence={candidate.get('reachabilityConfidence')}"
            )
            missing = candidate.get("missingNavigationFields") or []
            evidence = candidate.get("reachabilityEvidence") or []
            if evidence:
                print(f"   evidence: {', '.join(str(item) for item in evidence[:3])}")
            if missing:
                print(f"   missing navigation fields: {', '.join(str(item) for item in missing[:5])}")
    if payload.get("warnings"):
        print("warnings:")
        for warning in payload["warnings"][:20]:
            print(f"- {warning}")


def print_events_human(payload: dict) -> None:
    print(f"Live Event Timeline: {payload.get('status')} latestTick={payload.get('latestTick')}")
    print(f"event count: {payload.get('eventCount', 0)}")
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    if events:
        print("recent events:")
        for event in events:
            tick = event.get("tick", "unknown")
            severity = event.get("severity") or "info"
            summary = event.get("summary") or event.get("eventType") or "event"
            print(f"  [tick {tick}] {summary} ({severity})")
    if payload.get("warnings"):
        print("warnings:")
        for warning in payload["warnings"][:20]:
            print(f"- {warning}")


def compact_candidate(candidate: dict | None) -> dict | None:
    if not candidate:
        return None
    return {
        "classId": candidate.get("classId"),
        "targetName": candidate.get("targetName"),
        "id": candidate.get("id"),
        "hash": candidate.get("hash"),
        "distanceTiles": candidate.get("distanceTiles"),
        "onScreen": candidate.get("onScreen"),
        "geometryAvailable": candidate.get("geometryAvailable"),
        "uiBlocked": candidate.get("uiBlocked"),
        "aimPoint": candidate.get("aimPoint"),
        "targetLiveState": candidate.get("targetLiveState"),
        "navigation": candidate.get("navigation"),
        "qualityTier": candidate.get("qualityTier"),
        "qualityScore": candidate.get("qualityScore"),
        "tick": candidate.get("tick"),
    }


def compact_json_payload(payload: dict, args) -> dict:
    schema = payload.get("schema")
    compact = {
        key: payload.get(key)
        for key in ("schema", "status", "confidence", "generatedAtUtc", "warnings", "missingFields", "queryPerformance")
        if key in payload
    }
    if schema == TASK_SCHEMA:
        summary = payload.get("candidateSummary") or {}
        state = payload.get("stateSummary") or {}
        compact.update(
            {
                "task": payload.get("task"),
                "canAnswerCoreQuestions": payload.get("canAnswerCoreQuestions"),
                "latestTick": (state.get("freshness") or {}).get("latestTick"),
                "player": state.get("player"),
                "activityState": payload.get("activityState"),
                "woodcuttingState": payload.get("woodcuttingState"),
                "inventoryState": {
                    key: (payload.get("inventoryState") or {}).get(key)
                    for key in (
                        "known",
                        "inventorySlotCount",
                        "freeSlots",
                        "filledSlots",
                        "itemCount",
                        "totalItemQuantity",
                        "inventoryFull",
                        "changedThisTick",
                        "changedRecently",
                        "inventoryDeltaTrackingKnown",
                    )
                },
                "recentInventoryDeltaCount": len(payload.get("recentInventoryDeltas") or []),
                "recentActivityEventCount": len(payload.get("recentActivityEvents") or []),
                "recentEvents": payload.get("recentEvents") or [],
                "bestTree": compact_candidate(summary.get("bestTree")),
                "nearestTree": compact_candidate(summary.get("nearestTree")),
                "visibleTreeCandidateCount": summary.get("visibleTreeCandidateCount"),
                "treeCandidateCount": summary.get("treeCandidateCount"),
                "navigationReadiness": payload.get("navigationReadiness"),
                "missingCapabilities": payload.get("missingCapabilities"),
            }
        )
    elif schema == ANSWER_SCHEMA:
        compact.update({"query": payload.get("query"), "answer": compact_candidate(payload.get("answer"))})
        for alias in ("nearest", "best", "candidate"):
            if alias in payload:
                compact[alias] = compact_candidate(payload.get(alias)) if alias == "candidate" else payload.get(alias)
    elif schema == SUMMARY_SCHEMA:
        compact.update(
            {
                "sessionPath": payload.get("sessionPath"),
                "latestTick": payload.get("latestTick"),
                "activeProfile": payload.get("activeProfile"),
                "player": payload.get("player"),
                "candidateCount": payload.get("candidateCount"),
                "candidateCountsByClassId": payload.get("candidateCountsByClassId"),
                "candidateCountsByQualityTier": payload.get("candidateCountsByQualityTier"),
                "sourceSceneKnowledgeComplete": payload.get("sourceSceneKnowledgeComplete"),
                "sourceCapHit": payload.get("sourceCapHit"),
                "budgetExceeded": payload.get("budgetExceeded"),
                "writeFailures": payload.get("writeFailures"),
            }
        )
    elif schema == REACHABILITY_SCHEMA:
        compact.update(
            {
                "latestTick": payload.get("latestTick"),
                "classId": payload.get("classId"),
                "player": payload.get("player"),
                "collisionWindow": payload.get("collisionWindow"),
                "navigationReadiness": payload.get("navigationReadiness"),
                "reachabilitySummary": payload.get("reachabilitySummary"),
                "candidates": payload.get("candidates"),
                "missingCapabilities": payload.get("missingCapabilities"),
            }
        )
    elif schema == EVENTS_SCHEMA:
        compact.update(
            {
                "latestTick": payload.get("latestTick"),
                "eventCount": payload.get("eventCount"),
                "events": payload.get("events"),
                "warnings": payload.get("warnings"),
            }
        )
    else:
        compact.update({key: value for key, value in payload.items() if key not in {"sourceFiles", "items", "recentItemDeltas"}})
    if getattr(args, "fields", "compact") == "full" or getattr(args, "verbose", False):
        return payload
    return compact


def print_query_benchmark(payload: dict) -> None:
    performance = payload.get("queryPerformance")
    if not isinstance(performance, dict):
        return
    print(
        "query timing: "
        f"readMs={performance.get('queryReadMillis', 0)} "
        f"parseMs={performance.get('queryParseMillis', 0)} "
        f"selectMs={performance.get('querySelectMillis', 0)} "
        f"totalMs={performance.get('totalQueryMillis', 0)}"
    )


def print_self_test_human(payload: dict) -> None:
    print(f"Live Context Self-Test: {payload.get('status')}")
    for item in payload.get("checks") or []:
        print(f"{item.get('status'):4} {item.get('name')}: {item.get('reason')}")
    if payload.get("warnings"):
        print("warnings:")
        for warning in payload["warnings"]:
            print(f"- {warning}")


def attach_query_performance(payload: dict, timing: dict, total_started: float, args) -> dict:
    if not getattr(args, "benchmark", False):
        return payload
    timing["totalQueryMillis"] = (time.perf_counter() - total_started) * 1000.0
    payload["queryPerformance"] = {key: round(float(timing.get(key) or 0.0), 3) for key in QUERY_TIMING_KEYS}
    return payload


def build_payload(args) -> tuple[dict, int]:
    total_started = time.perf_counter()
    timing = {key: 0.0 for key in QUERY_TIMING_KEYS}
    session, session_warnings = resolve_session(args)
    if session is None:
        payload = {
            "schema": ANSWER_SCHEMA,
            "query": {},
            "answer": {},
            "status": "FAIL",
            "confidence": 0.0,
            "reasons": [],
            "warnings": session_warnings + ["No telemetry session found."],
            "missingFields": ["session"],
            "sourceFiles": [],
            "generatedAtUtc": utc_now(),
        }
        return attach_query_performance(payload, timing, total_started, args), 1
    context = load_live_context(session, timing if getattr(args, "benchmark", False) else None)
    context["warnings"] = session_warnings + context["warnings"]
    select_started = time.perf_counter()
    if args.events_only:
        payload = events_payload(context, getattr(args, "events", 5) or 5)
        add_timing(timing, "querySelectMillis", select_started)
        return attach_query_performance(payload, timing, total_started, args), 0
    if args.activity:
        payload = activity_payload(context)
        add_timing(timing, "querySelectMillis", select_started)
        return attach_query_performance(payload, timing, total_started, args), 0
    if args.inventory:
        payload = inventory_payload(context)
        add_timing(timing, "querySelectMillis", select_started)
        return attach_query_performance(payload, timing, total_started, args), 0
    if args.liveness:
        payload = liveness_payload(context)
        add_timing(timing, "querySelectMillis", select_started)
        return attach_query_performance(payload, timing, total_started, args), 0
    if args.self_test:
        payload = self_test_payload(context, args)
        add_timing(timing, "querySelectMillis", select_started)
        return attach_query_performance(payload, timing, total_started, args), 0
    if args.reachability:
        payload = reachability_payload(context, args.class_id or "tree", args)
        add_timing(timing, "querySelectMillis", select_started)
        return attach_query_performance(payload, timing, total_started, args), 0
    if args.task:
        if args.task.lower() == "woodcutting":
            payload = woodcutting_task_payload(context, args)
            add_timing(timing, "querySelectMillis", select_started)
            return attach_query_performance(payload, timing, total_started, args), 0
        payload = {
            "schema": TASK_SCHEMA,
            "task": args.task,
            "status": "FAIL",
            "canAnswerCoreQuestions": False,
            "observations": {},
            "candidateSummary": {},
            "stateSummary": {},
            "navigationReadiness": {},
            "warnings": context["warnings"] + [f"Unsupported task context: {args.task}"],
            "missingFields": context["missingFields"],
            "sourceFiles": context["sourceFiles"],
            "generatedAtUtc": utc_now(),
        }
        add_timing(timing, "querySelectMillis", select_started)
        return attach_query_performance(payload, timing, total_started, args), 1
    if args.nearest:
        payload = direct_query_payload(context, "nearest", args.nearest, args)
        add_timing(timing, "querySelectMillis", select_started)
        return attach_query_performance(payload, timing, total_started, args), 0
    if args.best:
        payload = direct_query_payload(context, "best", args.best, args)
        add_timing(timing, "querySelectMillis", select_started)
        return attach_query_performance(payload, timing, total_started, args), 0
    if args.baseline:
        add_timing(timing, "querySelectMillis", select_started)
        return attach_query_performance(context["baseline"], timing, total_started, args), 0
    payload = summary_payload(context, args)
    add_timing(timing, "querySelectMillis", select_started)
    return attach_query_performance(payload, timing, total_started, args), 0


def print_payload(payload: dict, args) -> None:
    wants_human = bool(getattr(args, "human", False) or getattr(args, "compact_human", False) or getattr(args, "watch_human", False))
    if wants_human:
        print(format_context_human(payload, compact=bool(getattr(args, "compact_human", False)), top=max(1, int(getattr(args, "top", 3) or 3))), end="")
        if getattr(args, "benchmark", False):
            print_query_benchmark(payload)
        if getattr(args, "show_json", False):
            print()
            print(json_dump_compact(compact_json_payload(payload, args)))
        return
    if args.json:
        json_payload = compact_json_payload(payload, args) if (args.compact_json or not args.verbose and args.fields != "full") else payload
        print(json_dump_compact(json_payload))
        return
    schema = payload.get("schema")
    if schema == SUMMARY_SCHEMA:
        print_summary_human(payload)
    elif schema == ANSWER_SCHEMA:
        print_answer_human(payload)
    elif schema == TASK_SCHEMA:
        print_task_human(payload, args)
    elif schema == SELF_TEST_SCHEMA:
        print_self_test_human(payload)
    elif schema == REACHABILITY_SCHEMA:
        print_reachability_human(payload)
    elif schema == EVENTS_SCHEMA:
        print_events_human(payload)
    else:
        if args.fields == "full" or args.verbose:
            print(json.dumps(payload, indent=2))
        else:
            print(json.dumps(compact_json_payload(payload, args), indent=2))
    if args.benchmark:
        print_query_benchmark(payload)


def parse_args():
    parser = argparse.ArgumentParser(description="Read-only live context QA/query helper for rolling target telemetry files.")
    parser.add_argument("--session", help="Telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override sessions directory when --session is omitted.")
    parser.add_argument("--latest-session", action="store_true", help="Use the newest telemetry session.")
    parser.add_argument("--summary", action="store_true", help="Print a compact live context summary.")
    parser.add_argument("--nearest", help="Return nearest candidate for a class id, such as tree or npc.")
    parser.add_argument("--best", help="Return best quality candidate for a class id, such as tree or npc.")
    parser.add_argument("--profile", help="Filter candidates by active profile id.")
    parser.add_argument("--max-distance", type=float, help="Maximum candidate Chebyshev tile distance for nearest/best queries.")
    parser.add_argument("--freshness-ticks", type=int, default=DEFAULT_FRESHNESS_TICKS, help="Maximum acceptable candidate tick delta.")
    parser.add_argument("--freshness-ms", type=int, default=DEFAULT_FRESHNESS_MS, help="Maximum acceptable live file age in milliseconds.")
    parser.add_argument("--task", help="Run a task-context QA report, currently woodcutting.")
    parser.add_argument("--activity", action="store_true", help="Report read-only apparent activity state from live_activity_state.json.")
    parser.add_argument("--inventory", action="store_true", help="Report read-only inventory state and recent deltas.")
    parser.add_argument("--liveness", action="store_true", help="Report read-only target liveness/depletion state.")
    parser.add_argument("--reachability", action="store_true", help="Report read-only candidate local collision reachability QA.")
    parser.add_argument("--events-only", action="store_true", help="Print only the rolling live event timeline.")
    parser.add_argument("--class-id", default="tree", help="Class id for --reachability, such as tree, npc, or ground_item. Default: tree.")
    parser.add_argument("--name-contains", help="Filter --reachability candidates by case-insensitive target name text.")
    parser.add_argument("--id", type=int, help="Filter --reachability candidates by target/object id.")
    parser.add_argument("--show-blocked", action="store_true", help="With --reachability, show only blocked candidates.")
    parser.add_argument("--show-reachable", action="store_true", help="With --reachability, show only reachable candidates.")
    parser.add_argument("--show-unknown", action="store_true", help="With --reachability, show only candidates with unknown reachability.")
    parser.add_argument("--self-test", action="store_true", help="Run read-only live context readiness checks.")
    parser.add_argument("--baseline", action="store_true", help="Return live_baseline_state.json.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    parser.add_argument("--compact-json", action="store_true", help="In JSON mode, omit bulky arrays and return compact answer fields.")
    parser.add_argument("--human", action="store_true", help="Print a readable mission-control style context report.")
    parser.add_argument("--watch-human", action="store_true", help="Refresh a readable mission-control style context report until interrupted.")
    parser.add_argument("--compact-human", action="store_true", help="Print a shorter one-screen readable context report.")
    parser.add_argument("--show-json", action="store_true", help="Print compact JSON after the human report.")
    parser.add_argument("--verbose", action="store_true", help="Print expanded details.")
    parser.add_argument("--top", type=int, default=3, help="Number of top candidates to include for normal/full output. Default: 3.")
    parser.add_argument("--events", type=int, default=5, help="Number of recent live timeline events to show in human/task output. Default: 5.")
    parser.add_argument("--fields", choices=["compact", "normal", "full"], default="compact", help="Human/JSON detail level. Default: compact.")
    parser.add_argument("--benchmark", action="store_true", help="Include query read/parse/select timing.")
    parser.add_argument("--watch", action="store_true", help="Repeat the query until interrupted.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between --watch refreshes.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    exit_code = 0
    while True:
        payload, code = build_payload(args)
        exit_code = max(exit_code, code)
        if args.watch_human:
            os.system("cls")
        print_payload(payload, args)
        if not (args.watch or args.watch_human):
            break
        try:
            time.sleep(max(0.1, args.interval))
        except KeyboardInterrupt:
            break
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

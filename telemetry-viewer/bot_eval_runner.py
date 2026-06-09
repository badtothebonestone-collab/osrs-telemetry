from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import task_script_api


BOT_EVAL_MANIFEST_SCHEMA = "bot_eval_manifest.v1"
BOT_DECISION_TRACE_SCHEMA = "bot_decision_trace.v1"
BOT_CANDIDATE_TRACE_SCHEMA = "bot_candidate_trace.v1"
BOT_ACTION_TRACE_SCHEMA = "bot_action_trace.v1"
BOT_OBSERVATION_TRACE_SCHEMA = "bot_observation_trace.v1"
BOT_POSTCONDITION_TRACE_SCHEMA = "bot_postcondition_trace.v1"
BOT_EVAL_SUMMARY_SCHEMA = "bot_eval_summary.v1"
BOT_LIVE_READINESS_SCHEMA = "bot_live_readiness.v1"
BOT_PREFLIGHT_SCHEMA = "bot_live_preflight.v1"
BOT_INPUT_GEOMETRY_SCHEMA = "bot_input_geometry_check.v1"
LIVE_NOT_REAL_ACTION_WARNING = "This is not real action execution. Use --live --execute-actions for real actions."

DEFAULT_FULL_LOOP_RECORDING = "20260607_171427_Wood_cutting_attacked"
DEFAULT_DAEMON_URL = "http://127.0.0.1:8890"
DEFAULT_SNAPSHOT_URL = "http://127.0.0.1:8893/snapshot"
DEFAULT_READINESS_TIMEOUT_SECONDS = 0.75
DEFAULT_STATUS_DIAGNOSTIC_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_TELEMETRY_AGE_MS = 5_000
DEFAULT_ARDUINO_PORT = "COM6"
DEFAULT_LIVENESS_RECOVERY_SECONDS = 180.0
DEFAULT_LIVENESS_ATTEMPTS_PER_STATE = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _phase_value(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("phase") or value.get("name") or value.get("label") or "unknown")
    if value in (None, ""):
        return "unknown"
    return str(value)


def _status(value: Any, default: str = "WARN") -> str:
    text = str(value or "").upper()
    return text if text in {"PASS", "WARN", "FAIL"} else default


def safe_load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def atomic_write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, default=str)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        try:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        except OSError:
            pass
    return path


def fetch_json_with_timing(url: str, timeout: float = DEFAULT_READINESS_TIMEOUT_SECONDS) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read()
            text = raw.decode("utf-8")
            payload = json.loads(text)
        return {
            "ok": True,
            "url": url,
            "statusCode": getattr(response, "status", None),
            "elapsedMs": round((time.monotonic() - started) * 1000, 3),
            "payload": payload if isinstance(payload, dict) else {"value": payload},
            "error": None,
        }
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "url": url,
            "statusCode": None,
            "elapsedMs": round((time.monotonic() - started) * 1000, 3),
            "payload": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def post_json_with_timing(url: str, payload: dict[str, Any], timeout: float = DEFAULT_READINESS_TIMEOUT_SECONDS) -> dict[str, Any]:
    started = time.monotonic()
    try:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            text = raw.decode("utf-8")
            parsed = json.loads(text)
        return {
            "ok": True,
            "url": url,
            "statusCode": getattr(response, "status", None),
            "elapsedMs": round((time.monotonic() - started) * 1000, 3),
            "payload": parsed if isinstance(parsed, dict) else {"value": parsed},
            "error": None,
        }
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "url": url,
            "statusCode": None,
            "elapsedMs": round((time.monotonic() - started) * 1000, 3),
            "payload": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def configure_live_loop_runtime_control(
    *,
    daemon_url: str = DEFAULT_DAEMON_URL,
    timeout: float = 3.0,
    poster: Callable[[str, dict[str, Any], float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = {
        "missionPreset": "woodcut_bank",
        "taskPolicy": "woodcutting_bank",
        "goalCount": None,
        "brainEnabled": True,
        "observeOnly": False,
        "resetBrainState": True,
    }
    post = poster or post_json_with_timing
    result = post(daemon_url.rstrip("/") + "/control", payload, timeout)
    response = _dict(result.get("payload"))
    state = _dict(response.get("state"))
    warnings = list(response.get("warnings") or [])
    if not result.get("ok"):
        warnings.append(str(result.get("error") or "runtime control request failed"))
    return {
        "schema": "bot_live_loop_runtime_control.v1",
        "status": "PASS" if result.get("ok") and response.get("status") == "PASS" else "FAIL",
        "request": payload,
        "acceptedFields": response.get("acceptedFields") or [],
        "rejectedFields": response.get("rejectedFields") or [],
        "state": state,
        "goalCount": state.get("goalCount"),
        "taskPolicy": state.get("taskPolicy"),
        "brainEnabled": state.get("brainEnabled"),
        "observeOnly": state.get("observeOnly"),
        "elapsedMs": result.get("elapsedMs"),
        "error": result.get("error") or (None if response.get("status") == "PASS" else response.get("status")),
        "warnings": warnings,
    }


def _health_url_from_snapshot(snapshot_url: str) -> str:
    text = str(snapshot_url or "").rstrip("/")
    if text.endswith("/snapshot"):
        return text[: -len("/snapshot")] + "/health"
    return text + "/health"


def _generated_age_ms(payload: dict[str, Any], *, now: float | None = None) -> int | None:
    generated = payload.get("generatedAtUtc") or payload.get("updatedAtUtc") or payload.get("timestampUtc")
    if not generated:
        return None
    try:
        parsed = datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now_ts = time.time() if now is None else now
    return max(0, int((now_ts - parsed.timestamp()) * 1000))


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _live_dir_for_session(session_dir: Path) -> Path:
    nested = session_dir / "interaction_geometry" / "live"
    return nested if nested.exists() else session_dir


def latest_live_session_dir(sessions_root: str | Path | None = None) -> Path | None:
    root = Path(sessions_root).expanduser() if sessions_root else Path.home() / ".osrs-telemetry" / "sessions"
    if not root.exists():
        return None
    candidates = [path for path in root.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)[0]


def _disk_live_snapshot(session_dir: Path | None, *, now: float | None = None) -> dict[str, Any]:
    if session_dir is None:
        return {"sessionPath": None, "liveDir": None, "status": {}, "contextIndex": {}, "ageMs": None}
    live_dir = _live_dir_for_session(session_dir)
    status_path = live_dir / "live_status.json"
    context_path = live_dir / "live_context_index.json"
    activity_path = live_dir / "live_activity_state.json"
    baseline_path = live_dir / "live_baseline_state.json"
    status = safe_load_json(status_path)
    context_index = safe_load_json(context_path)
    activity = safe_load_json(activity_path)
    baseline = safe_load_json(baseline_path)
    ages = [
        item
        for item in (
            _generated_age_ms(status, now=now),
            _generated_age_ms(context_index, now=now),
            int(max(0, ((time.time() if now is None else now) - status_path.stat().st_mtime) * 1000)) if status_path.exists() else None,
        )
        if item is not None
    ]
    return {
        "sessionPath": str(session_dir),
        "liveDir": str(live_dir),
        "status": status,
        "contextIndex": context_index,
        "activity": activity,
        "baseline": baseline,
        "ageMs": min(ages) if ages else None,
        "statusPath": str(status_path) if status_path.exists() else None,
        "contextIndexPath": str(context_path) if context_path.exists() else None,
    }


def _extract_telemetry_age_ms(
    status_payload: dict[str, Any],
    snapshot_payload: dict[str, Any],
    disk_snapshot: dict[str, Any],
    *,
    now: float | None = None,
) -> int | None:
    for payload in (status_payload, snapshot_payload):
        for key in ("sourceAgeMs", "sourceAgeMillis", "ageMs", "stateAgeMs", "latestAgeMs"):
            value = payload.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                return int(value)
        freshness = _dict(payload.get("liveFreshness") or payload.get("freshness"))
        for key in ("liveFileAgeMillis", "sourceAgeMs", "sourceAgeMillis", "ageMs"):
            value = freshness.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                return int(value)
        age = _generated_age_ms(payload, now=now)
        if age is not None:
            return age
    disk_age = disk_snapshot.get("ageMs")
    return int(disk_age) if isinstance(disk_age, (int, float)) else None


def _plugin_snapshot_cache_age_ms(snapshot_payload: dict[str, Any]) -> int | None:
    for key in ("maxCacheAgeMillis", "cacheAgeMillis", "latestAgeMs", "ageMs"):
        value = snapshot_payload.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    ages = snapshot_payload.get("cacheAgeMillisByType")
    if isinstance(ages, dict):
        numeric = [int(value) for value in ages.values() if isinstance(value, (int, float)) and value >= 0]
        if numeric:
            return max(numeric)
    return None


def _live_tick_freshness_override(
    status_payload: dict[str, Any],
    snapshot_payload: dict[str, Any],
    *,
    max_telemetry_age_ms: int,
) -> dict[str, Any] | None:
    snapshot_age_ms = _plugin_snapshot_cache_age_ms(snapshot_payload)
    if snapshot_payload.get("cacheWallClockFresh") is True:
        return {
            "source": "plugin_snapshot_cache_wall_clock_fresh",
            "ageMs": snapshot_age_ms,
            "latestTick": snapshot_payload.get("latestTick"),
        }
    if snapshot_age_ms is not None and snapshot_age_ms <= max_telemetry_age_ms:
        return {
            "source": "plugin_snapshot_cache_age",
            "ageMs": snapshot_age_ms,
            "latestTick": snapshot_payload.get("latestTick"),
        }
    freshness = _dict(status_payload.get("liveFreshness") or status_payload.get("freshness"))
    if freshness.get("freshByTicks") is True:
        return {
            "source": "daemon_live_fresh_by_ticks",
            "ageMs": snapshot_age_ms,
            "latestTick": _first_present(freshness.get("latestTick"), status_payload.get("latestTick")),
            "tickDelta": freshness.get("tickDelta"),
        }
    return None


def _extract_current_area(*payloads: dict[str, Any]) -> str | None:
    for payload in payloads:
        for path in (
            ("currentArea",),
            ("area",),
            ("profile",),
            ("activity", "currentArea"),
            ("activity", "area"),
            ("genericTaskState", "currentArea"),
            ("routeMonitor", "currentArea"),
            ("routeState", "currentArea"),
        ):
            current: Any = payload
            for key in path:
                current = _dict(current).get(key)
            if current not in (None, "", [], {}):
                return str(current)
    return None


def _numeric_tick(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _epoch_ms_age_ms(value: Any, *, now: float | None = None) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        millis = int(float(value))
    except (TypeError, ValueError):
        return None
    now_ms = int((time.time() if now is None else now) * 1000)
    return max(0, now_ms - millis)


def _object_total(payload: dict[str, Any]) -> int | None:
    for value in (
        payload.get("worldModelObjectTotal"),
        _dict(payload.get("worldModelSummary")).get("objectTotal"),
        _dict(_dict(payload.get("worldModelSummary")).get("objects")).get("total"),
        _dict(payload.get("objects")).get("total"),
    ):
        try:
            if value is not None and not isinstance(value, bool):
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _tick_matches_payload_latest(payload: dict[str, Any], tick: Any, *, max_delta: int = 5) -> bool:
    source_tick = _numeric_tick(tick)
    if source_tick is None:
        return False
    brain = _dict(payload.get("brain"))
    latest_tick = _numeric_tick(
        _first_present(
            payload.get("latestTick"),
            payload.get("latestTickProcessed"),
            brain.get("latestTick"),
        )
    )
    if latest_tick is None:
        return False
    return abs(latest_tick - source_tick) <= max_delta


def _payload_has_fresh_live_scene_context(payload: dict[str, Any]) -> bool:
    payload = payload if isinstance(payload, dict) else {}
    brain = _dict(payload.get("brain"))
    for player in (
        _dict(payload.get("player")),
        _dict(payload.get("playerContext")),
        _dict(brain.get("player")),
        _dict(brain.get("playerContext")),
        _dict(_dict(payload.get("baseline")).get("player")),
    ):
        if _first_present(
            player.get("worldX"),
            player.get("worldY"),
            _dict(player.get("worldPoint")).get("worldX"),
            _dict(player.get("worldPoint")).get("x"),
            _dict(player.get("worldTile")).get("worldX"),
            _dict(player.get("worldTile")).get("x"),
        ) is not None:
            return True
    freshness = _dict(brain.get("freshnessDomains"))
    inventory = _dict(brain.get("inventoryContext") or payload.get("inventoryContext"))
    if inventory and str(freshness.get("inventoryFreshness") or "fresh").lower() != "stale":
        if _first_present(inventory.get("freeSlots"), inventory.get("inventoryFull"), _dict(inventory.get("progress")).get("currentHeldCount")) is not None:
            return True
    for context_name in ("serviceRouteContext", "pathingContext", "resourceReturnContext"):
        context = _dict(brain.get(context_name) or payload.get(context_name))
        if not context:
            continue
        status = str(context.get("status") or "").upper()
        if status in {"FAIL", "MISS"}:
            continue
        source_tick = _first_present(context.get("sourceTick"), context.get("latestTick"))
        if source_tick is not None and not _tick_matches_payload_latest(payload, source_tick):
            continue
        if _first_present(
            context.get("routeAvailable"),
            context.get("actionReady"),
            context.get("pathingNeeded"),
            context.get("currentStep"),
            context.get("currentNodeId"),
            context.get("nextWaypointTile"),
            context.get("visibleServiceTarget"),
            context.get("visibleInteractionTarget"),
            context.get("selectedServiceObject"),
            context.get("resourceTargetAvailable"),
        ) is not None:
            return True
    return False


def _loaded_scene_blockers(
    *payloads: dict[str, Any],
    now: float | None = None,
    max_hot_age_ms: int = DEFAULT_MAX_TELEMETRY_AGE_MS,
) -> list[str]:
    blockers: list[str] = []
    negative_states = {
        "LOGIN_SCREEN",
        "LOGIN_SCREEN_AUTHENTICATOR",
        "CONNECTION_LOST",
        "HOPPING",
        "STARTING",
        "UNKNOWN",
    }
    for payload in payloads:
        if not payload:
            continue
        fresh_live_scene_context = _payload_has_fresh_live_scene_context(payload)
        hot = _dict(payload.get("clientTickHot"))
        hot_state = str(hot.get("gameState") or "").upper()
        hot_age = _epoch_ms_age_ms(hot.get("wallTimeMillis"), now=now)
        hot_stale = hot_age is not None and hot_age > max_hot_age_ms
        if hot_state in negative_states:
            if not (fresh_live_scene_context and hot_stale):
                blockers.append(f"client_tick_hot_game_state_{hot_state.lower()}")
        if hot and hot_stale and not fresh_live_scene_context:
            blockers.append(f"client_tick_hot_stale_age_ms_{hot_age}")
        screen = str(_first_present(payload.get("screenClassification"), payload.get("visualClassification")) or "").lower()
        if "disconnected" in screen:
            blockers.append("screen_classification_disconnected")
        elif "login_screen" in screen:
            blockers.append("screen_classification_login_screen")
        state = str(_first_present(payload.get("bootstrapState"), payload.get("livenessState"), payload.get("state")) or "").lower()
        if "disconnected" in state:
            blockers.append("bootstrap_state_disconnected")
        elif "login" in state and "logged_in" not in state:
            blockers.append("bootstrap_state_login_screen")
        loaded_verified = payload.get("loadedSceneVerified")
        if loaded_verified is False:
            blockers.append("loaded_scene_verified_false")
        object_total = _object_total(payload)
        explicit_game_state = str(_first_present(payload.get("gameState"), payload.get("worldModelGameState")) or "").upper()
        if object_total == 0 and explicit_game_state in {"LOGIN_SCREEN", "CONNECTION_LOST", ""}:
            blockers.append("world_model_zero_objects")
    return list(dict.fromkeys(blockers))


def _game_client_loaded(
    *payloads: dict[str, Any],
    now: float | None = None,
    max_hot_age_ms: int = DEFAULT_MAX_TELEMETRY_AGE_MS,
) -> bool:
    if _loaded_scene_blockers(*payloads, now=now, max_hot_age_ms=max_hot_age_ms):
        return False
    for payload in payloads:
        hot = _dict(payload.get("clientTickHot"))
        game_state = str(_first_present(payload.get("gameState"), hot.get("gameState")) or "").upper()
        if game_state in {"LOGGED_IN", "LOADING"}:
            return True
        object_total = _object_total(payload)
        if object_total is not None and object_total > 0:
            return True
        player = _dict(_first_present(payload.get("player"), _dict(payload.get("baseline")).get("player")))
        if _first_present(player.get("worldX"), player.get("worldY"), _dict(player.get("worldPoint")).get("worldX"), _dict(player.get("worldPoint")).get("x")) is not None:
            return True
        if _payload_has_fresh_live_scene_context(payload):
            return True
    return False


def _extract_tree_target(disk_snapshot: dict[str, Any]) -> dict[str, Any]:
    context_index = _dict(disk_snapshot.get("contextIndex"))
    best_by_class = _dict(context_index.get("bestCandidateByClassId"))
    nearest_by_class = _dict(context_index.get("nearestCandidateByClassId"))
    candidate = _dict(best_by_class.get("tree") or nearest_by_class.get("tree"))
    if not candidate:
        return {}
    aim = _dict(candidate.get("aimPoint"))
    return {
        "name": candidate.get("name") or "Tree",
        "kind": candidate.get("targetType") or "sceneObject",
        "classId": candidate.get("classId") or "tree",
        "targetQuality": "strong" if int(candidate.get("qualityScore") or 0) >= 70 else candidate.get("qualityTier") or "unknown",
        "onScreen": candidate.get("onScreen"),
        "geometryAvailable": candidate.get("geometryAvailable"),
        "aimPoint": {"x": aim.get("x"), "y": aim.get("y")} if aim else None,
        "preferredGeometryType": candidate.get("preferredGeometryType"),
    }


def _route_templates_ready(root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    templates = [
        root / "route_templates" / "Bank_to_Woodcutting_area.route_template.json",
        root / "route_templates" / "woodcutting_area_to_bank.route_template.json",
    ]
    missing = [str(path) for path in templates if not path.exists()]
    return {"loaded": not missing, "paths": [str(path) for path in templates], "missing": missing}


def _task_state_probe(disk_snapshot: dict[str, Any]) -> dict[str, Any]:
    source = {
        "schema": "bot_live_task_state_probe.v1",
        "target": _extract_tree_target(disk_snapshot),
        "action": "Chop down",
        "routeMonitor": {"routeState": "unknown", "offRoute": False},
        "woodcuttingLoopLifecycle": {
            "schema": "woodcutting_loop_lifecycle_compact.v1",
            "status": "WARN",
            "loopState": "unknown",
            "currentPhase": "unknown",
            "nextExpectedPhase": "unknown",
            "warnings": ["live loop lifecycle not available from readiness smoke"],
            "missingCapabilities": ["woodcutting_loop_lifecycle.live"],
        },
    }
    try:
        plan = task_script_api.get_next_click_plan(source)
        return {"readable": True, "clickPlan": plan, "error": None}
    except Exception as exc:
        return {"readable": False, "clickPlan": {}, "error": f"{type(exc).__name__}: {exc}"}


def check_live_readiness(
    *,
    daemon_url: str = DEFAULT_DAEMON_URL,
    snapshot_url: str = DEFAULT_SNAPSHOT_URL,
    timeout: float = DEFAULT_READINESS_TIMEOUT_SECONDS,
    max_telemetry_age_ms: int = DEFAULT_MAX_TELEMETRY_AGE_MS,
    sessions_root: str | Path | None = None,
    no_input: bool = True,
    fetcher: Callable[[str, float], dict[str, Any]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    fetch = fetcher or fetch_json_with_timing
    daemon_url = str(daemon_url).rstrip("/")
    health_result = fetch(daemon_url + "/health", timeout)
    status_result = {
        "ok": None,
        "url": daemon_url + "/status",
        "statusCode": None,
        "elapsedMs": 0.0,
        "payload": {},
        "error": "skipped: /status can build a heavier diagnostic payload; /health is the bounded readiness endpoint",
    }
    snapshot_health_result = fetch(_health_url_from_snapshot(snapshot_url), timeout)
    status_payload = _dict(health_result.get("payload"))
    snapshot_payload = _dict(snapshot_health_result.get("payload"))
    latest_session = latest_live_session_dir(sessions_root)
    disk_snapshot = _disk_live_snapshot(latest_session, now=now)
    disk_status = _dict(disk_snapshot.get("status"))
    disk_context = _dict(disk_snapshot.get("contextIndex"))
    baseline = _dict(disk_snapshot.get("baseline"))
    disk_activity = _dict(disk_snapshot.get("activity"))
    preliminary_loaded_scene = _game_client_loaded(
        status_payload,
        snapshot_payload,
        disk_status,
        disk_context,
        baseline,
        disk_activity,
        now=now,
        max_hot_age_ms=max_telemetry_age_ms,
    )
    if not preliminary_loaded_scene and health_result.get("ok"):
        diagnostic_timeout = max(float(timeout or 0.0), DEFAULT_STATUS_DIAGNOSTIC_TIMEOUT_SECONDS)
        diagnostic_status_result = fetch(daemon_url + "/status", diagnostic_timeout)
        status_result = diagnostic_status_result
        if diagnostic_status_result.get("ok"):
            status_payload = _dict(diagnostic_status_result.get("payload"))
    telemetry_age_ms = _extract_telemetry_age_ms(status_payload, snapshot_payload, disk_snapshot, now=now)
    freshness_override = _live_tick_freshness_override(
        status_payload,
        snapshot_payload,
        max_telemetry_age_ms=max_telemetry_age_ms,
    )
    telemetry_fresh = telemetry_age_ms is not None and telemetry_age_ms <= max_telemetry_age_ms
    if freshness_override is not None:
        telemetry_fresh = True
        override_age = freshness_override.get("ageMs")
        if isinstance(override_age, (int, float)):
            telemetry_age_ms = int(override_age)
    route_templates = _route_templates_ready()
    task_probe = _task_state_probe(disk_snapshot)

    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    if not snapshot_health_result.get("ok"):
        warnings.append(f"snapshot_health_unreachable: {snapshot_health_result.get('error')}")
    if telemetry_age_ms is None:
        if freshness_override is None:
            warnings.append("telemetry freshness could not be determined")
    elif telemetry_age_ms > max_telemetry_age_ms:
        if freshness_override is None:
            warnings.append(f"telemetry stale: ageMs={telemetry_age_ms} maxAgeMs={max_telemetry_age_ms}")
    if freshness_override is not None:
        notes.append(f"telemetry accepted via {freshness_override.get('source')}")
    if not route_templates["loaded"]:
        errors.append("required route templates are missing")
    if not task_probe["readable"]:
        errors.append(f"task_script_api state probe failed: {task_probe.get('error')}")
    if no_input:
        notes.append("input backend was not required because no-input smoke mode is active")

    context_reachable = bool(health_result.get("ok"))
    can_start = bool(context_reachable and telemetry_fresh and route_templates["loaded"] and task_probe["readable"])
    status = "FAIL" if errors else "WARN" if warnings or not telemetry_fresh else "PASS"
    if not context_reachable:
        status = "FAIL"

    latest_tick = _first_present(
        status_payload.get("latestTick"),
        status_payload.get("latestTickProcessed"),
        snapshot_payload.get("latestTick"),
        disk_status.get("pluginSnapshotLatestTick"),
        disk_context.get("latestTick"),
    )
    latest_export_seq = _first_present(
        status_payload.get("latestExportSeq"),
        status_payload.get("exportSeq"),
        snapshot_payload.get("latestExportSeq"),
        disk_status.get("latestExportSeq"),
        disk_status.get("pluginSnapshotExportSeq"),
    )
    current_area = _extract_current_area(status_payload, snapshot_payload, disk_status, disk_context, _dict(disk_snapshot.get("activity")))
    loaded_scene_blockers = _loaded_scene_blockers(
        status_payload,
        snapshot_payload,
        disk_status,
        disk_context,
        _dict(disk_snapshot.get("baseline")),
        _dict(disk_snapshot.get("activity")),
        now=now,
        max_hot_age_ms=max_telemetry_age_ms,
    )
    game_client_loaded = _game_client_loaded(
        status_payload,
        snapshot_payload,
        disk_status,
        disk_context,
        _dict(disk_snapshot.get("baseline")),
        _dict(disk_snapshot.get("activity")),
        now=now,
        max_hot_age_ms=max_telemetry_age_ms,
    )
    if loaded_scene_blockers:
        game_client_loaded = False
    if not game_client_loaded:
        errors.append("loaded_scene_not_ready: " + ", ".join(loaded_scene_blockers or ["current scene proof unavailable"]))
    from input_control.input_geometry import resolve_input_geometry_status

    input_geometry = resolve_input_geometry_status(
        status_payload,
        session=latest_session,
        allow_focus_repair=game_client_loaded and not no_input,
        max_age_ms=max_telemetry_age_ms,
        now=now,
    )
    input_geometry_ready = bool(input_geometry.get("status") == "PASS")
    if not no_input and not input_geometry_ready:
        errors.append(str(input_geometry.get("blockerCode") or "input_geometry_unavailable"))
    snapshot_fallback_can_start = bool(
        not context_reachable
        and snapshot_health_result.get("ok")
        and telemetry_fresh
        and game_client_loaded
        and route_templates["loaded"]
        and task_probe["readable"]
        and (no_input or input_geometry_ready)
    )
    if not context_reachable:
        context_error = f"context_health_unreachable: {health_result.get('error')}"
        if snapshot_fallback_can_start:
            warnings.append(context_error)
            notes.append("context health unavailable; proceeding with plugin snapshot executor fallback")
        else:
            errors.append(context_error)
    root_cause = None
    if not context_reachable and not snapshot_fallback_can_start:
        root_cause = "context_health_unreachable_or_unresponsive"
    elif not game_client_loaded:
        root_cause = "loaded_scene_not_ready"
    elif not telemetry_fresh:
        root_cause = "telemetry_stale"
    elif not route_templates["loaded"]:
        root_cause = "route_template_missing"
    elif not task_probe["readable"]:
        root_cause = "task_script_api_state_unreadable"
    elif not no_input and not input_geometry_ready:
        root_cause = str(input_geometry.get("blockerCode") or "input_geometry_unavailable")
    can_start = bool((can_start or snapshot_fallback_can_start) and game_client_loaded and (no_input or input_geometry_ready))
    status = "FAIL" if errors else "WARN" if warnings or not telemetry_fresh or not game_client_loaded else "PASS"
    if snapshot_fallback_can_start:
        status = "PASS" if not errors else "FAIL"

    return {
        "schema": BOT_LIVE_READINESS_SCHEMA,
        "status": status,
        "generatedAtUtc": utc_now(),
        "daemonUrl": daemon_url,
        "snapshotUrl": snapshot_url,
        "timeoutSeconds": timeout,
        "maxTelemetryAgeMs": max_telemetry_age_ms,
        "contextServiceReachable": context_reachable,
        "contextFallbackActive": snapshot_fallback_can_start,
        "daemonHealthReachable": bool(health_result.get("ok")),
        "snapshotServiceReachable": bool(snapshot_health_result.get("ok")),
        "telemetryFresh": telemetry_fresh,
        "telemetryAgeMs": telemetry_age_ms,
        "telemetryFreshnessSource": freshness_override.get("source") if freshness_override else "age_ms",
        "telemetryFreshnessOverride": freshness_override,
        "gameClientLoaded": game_client_loaded,
        "loadedSceneReady": game_client_loaded,
        "loadedSceneBlockers": loaded_scene_blockers,
        "latestTick": latest_tick,
        "latestExportSeq": latest_export_seq,
        "currentArea": current_area,
        "routeTemplateLoaded": route_templates["loaded"],
        "routeTemplates": route_templates,
        "taskStateReadable": task_probe["readable"],
        "inputBackendReady": None if no_input else input_geometry_ready,
        "inputBackendRequired": not no_input,
        "inputGeometry": input_geometry,
        "inputGeometryReady": input_geometry_ready,
        "liveEvalCanStart": can_start,
        "rootCause": root_cause,
        "latestSessionPath": disk_snapshot.get("sessionPath"),
        "latestLiveDir": disk_snapshot.get("liveDir"),
        "diskStatusPath": disk_snapshot.get("statusPath"),
        "diskContextIndexPath": disk_snapshot.get("contextIndexPath"),
        "endpointChecks": {
            "daemonStatus": {key: value for key, value in status_result.items() if key != "payload"},
            "contextHealth": {key: value for key, value in health_result.items() if key != "payload"},
            "snapshotHealth": {key: value for key, value in snapshot_health_result.items() if key != "payload"},
        },
        "taskProbe": task_probe,
        "warnings": list(dict.fromkeys(warnings)),
        "errors": list(dict.fromkeys(errors)),
        "notes": list(dict.fromkeys(notes)),
    }


def run_input_geometry_check(
    *,
    daemon_url: str = DEFAULT_DAEMON_URL,
    snapshot_url: str = DEFAULT_SNAPSHOT_URL,
    timeout: float = DEFAULT_READINESS_TIMEOUT_SECONDS,
    max_telemetry_age_ms: int = DEFAULT_MAX_TELEMETRY_AGE_MS,
    sessions_root: str | Path | None = None,
    fetcher: Callable[[str, float], dict[str, Any]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    fetch = fetcher or fetch_json_with_timing
    daemon_url = str(daemon_url).rstrip("/")
    latest_session = latest_live_session_dir(sessions_root)
    disk_snapshot = _disk_live_snapshot(latest_session, now=now)
    bounded_timeout = max(float(timeout or 0.0), 5.0)
    status_timeout = max(bounded_timeout, DEFAULT_STATUS_DIAGNOSTIC_TIMEOUT_SECONDS)
    health_result = fetch(daemon_url + "/health", bounded_timeout)
    status_request_started = time.time() if now is None else now
    status_result = fetch(daemon_url + "/status", status_timeout)
    snapshot_health_result = fetch(_health_url_from_snapshot(snapshot_url), bounded_timeout)
    status_payload = _dict(status_result.get("payload"))
    snapshot_payload = _dict(snapshot_health_result.get("payload"))
    from input_control.input_geometry import resolve_input_geometry_status

    input_geometry = resolve_input_geometry_status(
        status_payload,
        session=latest_session,
        allow_focus_repair=True,
        max_age_ms=max_telemetry_age_ms,
        now=now,
    )
    baseline = _dict(disk_snapshot.get("baseline"))
    disk_status = _dict(disk_snapshot.get("status"))
    disk_context = _dict(disk_snapshot.get("contextIndex"))
    disk_activity = _dict(disk_snapshot.get("activity"))
    loaded_scene_blockers = _loaded_scene_blockers(
        status_payload,
        snapshot_payload,
        disk_status,
        disk_context,
        baseline,
        disk_activity,
        now=now,
        max_hot_age_ms=max_telemetry_age_ms,
    )
    loaded_scene_verified = bool(
        _game_client_loaded(
            status_payload,
            snapshot_payload,
            disk_status,
            disk_context,
            baseline,
            disk_activity,
            now=status_request_started,
            max_hot_age_ms=max_telemetry_age_ms,
        )
        and not loaded_scene_blockers
    )
    loaded_scene_proof = {
        "loadedSceneVerified": loaded_scene_verified,
        "gameState": _first_present(
            status_payload.get("gameState"),
            snapshot_payload.get("gameState"),
            disk_status.get("gameState"),
            baseline.get("gameState"),
        ),
        "latestTick": _first_present(
            status_payload.get("latestTick"),
            status_payload.get("latestTickProcessed"),
            snapshot_payload.get("latestTick"),
            disk_status.get("pluginSnapshotLatestTick"),
            baseline.get("latestTick"),
            disk_context.get("latestTick"),
        ),
        "worldModelObjectTotal": _first_present(
            status_payload.get("worldModelObjectTotal"),
            snapshot_payload.get("worldModelObjectTotal"),
            _dict(baseline.get("sceneCache")).get("presentObjectCount"),
        ),
        "blockers": loaded_scene_blockers,
    }
    warnings: list[str] = []
    if not health_result.get("ok"):
        warnings.append(f"context_health_unreachable: {health_result.get('error')}")
    if not status_result.get("ok"):
        warnings.append(f"context_status_unreachable: {status_result.get('error')}")
    if not snapshot_health_result.get("ok"):
        warnings.append(f"snapshot_health_unreachable: {snapshot_health_result.get('error')}")
    warnings.extend(str(item) for item in input_geometry.get("warnings") or [])
    errors: list[str] = []
    if not loaded_scene_verified:
        errors.append("loaded_scene_not_ready")
    if input_geometry.get("status") != "PASS":
        errors.append(str(input_geometry.get("blockerCode") or "input_geometry_unavailable"))
    status = "FAIL" if errors else "WARN" if warnings else "PASS"
    return {
        "schema": BOT_INPUT_GEOMETRY_SCHEMA,
        "status": status,
        "generatedAtUtc": utc_now(),
        "daemonUrl": daemon_url,
        "snapshotUrl": snapshot_url,
        "loadedSceneProof": loaded_scene_proof,
        "inputGeometry": input_geometry,
        "inputGeometryPass": input_geometry.get("status") == "PASS",
        "latestSessionPath": disk_snapshot.get("sessionPath"),
        "latestLiveDir": disk_snapshot.get("liveDir"),
        "endpointChecks": {
            "contextHealth": {key: value for key, value in health_result.items() if key != "payload"},
            "contextStatus": {key: value for key, value in status_result.items() if key != "payload"},
            "snapshotHealth": {key: value for key, value in snapshot_health_result.items() if key != "payload"},
        },
        "statusDiagnosticTimeoutSeconds": status_timeout,
        "warnings": list(dict.fromkeys(warnings)),
        "errors": list(dict.fromkeys(errors)),
        "nextLiveCommand": (
            "python telemetry-viewer\\bot_eval_runner.py --task woodcutting_loop --live --execute-actions "
            "--auto-recover-loaded-scene --record-everything --analyze-after --json"
        ),
    }


def _module_check(module_name: str, required_attrs: list[str] | None = None) -> dict[str, Any]:
    try:
        module = __import__(module_name, fromlist=["*"])
    except Exception as exc:  # noqa: BLE001
        return {"status": "FAIL", "module": module_name, "error": f"{type(exc).__name__}: {exc}", "attributes": {}}
    attrs = {name: hasattr(module, name) for name in (required_attrs or [])}
    missing = [name for name, present in attrs.items() if not present]
    return {
        "status": "FAIL" if missing else "PASS",
        "module": module_name,
        "error": None,
        "attributes": attrs,
        "missingAttributes": missing,
    }


def _json_readable(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict)


def _folder_writable(path: Path) -> dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".bot_eval_preflight_write_test"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink()
        return {"status": "PASS", "path": str(path), "writable": True, "error": None}
    except OSError as exc:
        return {"status": "FAIL", "path": str(path), "writable": False, "error": f"{type(exc).__name__}: {exc}"}


def run_preflight(
    *,
    task: str = "woodcutting_loop",
    output_root: str | Path | None = None,
    arduino_port: str = DEFAULT_ARDUINO_PORT,
) -> dict[str, Any]:
    if task != "woodcutting_loop":
        raise ValueError("only task=woodcutting_loop is supported by this bounded evaluator")
    root = repo_root()
    knowledge_dir = root / "telemetry-viewer" / "knowledge_base"
    docs_dir = root / "docs" / "knowledge"
    template_paths = sorted((root / "route_templates").glob("*.route_template.json"))
    guide_paths = sorted((root / "route_guides").glob("*.route_guide.json"))
    output_dir = Path(output_root) if output_root else root / "bot_runs"

    start_game = _module_check("start_game_command", ["resolve_start_game_command", "launch_start_game", "classify_launch_mode"])
    start_game_command_info: dict[str, Any] = {}
    if start_game["status"] == "PASS":
        import start_game_command

        start_game_command_info = start_game_command.resolve_start_game_command(prefer_authenticated=True)

    checks = {
        "knowledgeBaseReadable": {
            "status": "PASS"
            if knowledge_dir.exists()
            and _json_readable(knowledge_dir / "project_knowledge.json")
            and _json_readable(knowledge_dir / "capability_registry.json")
            and _json_readable(knowledge_dir / "script_api_map.json")
            else "FAIL",
            "knowledgeBaseDir": str(knowledge_dir),
            "docsDir": str(docs_dir),
            "projectKnowledgeJson": str(knowledge_dir / "project_knowledge.json"),
            "capabilityRegistryJson": str(knowledge_dir / "capability_registry.json"),
            "scriptApiMapJson": str(knowledge_dir / "script_api_map.json"),
        },
        "taskScriptApiReadable": _module_check(
            "task_script_api",
            ["assess_task_run_readiness", "get_woodcutting_loop_lifecycle", "get_next_click_plan"],
        ),
        "routeTemplatesPresent": {
            "status": "PASS" if template_paths else "FAIL",
            "count": len(template_paths),
            "paths": [str(path) for path in template_paths],
        },
        "routeGuidesPresent": {
            "status": "PASS" if guide_paths else "FAIL",
            "count": len(guide_paths),
            "paths": [str(path) for path in guide_paths],
        },
        "startGameCommandClassified": {
            "status": "PASS" if start_game["status"] == "PASS" and start_game_command_info.get("status") == "PASS" else "FAIL",
            "module": start_game,
            "resolution": start_game_command_info,
        },
        "recoveryPathAvailable": {
            "status": "PASS"
            if _module_check("liveness_recovery_core", ["ensure_loaded_scene", "loaded_scene_proof"])["status"] == "PASS"
            and (root / "telemetry-viewer" / "context_service.py").exists()
            else "FAIL",
            "module": _module_check("liveness_recovery_core", ["ensure_loaded_scene", "loaded_scene_proof"]),
            "command": build_loaded_scene_recovery_command(arduino_port=arduino_port),
        },
        "readinessPathAvailable": {
            "status": "PASS"
            if _module_check("live_readiness_core", ["build_readiness_report"])["status"] == "PASS"
            and callable(check_live_readiness)
            else "FAIL",
            "module": _module_check("live_readiness_core", ["build_readiness_report"]),
            "botReadinessFunction": "check_live_readiness",
        },
        "executorAvailable": {
            "status": "PASS" if (root / "telemetry-viewer" / "execute_next_action.py").exists() else "FAIL",
            "path": str(root / "telemetry-viewer" / "execute_next_action.py"),
            "command": build_live_executor_command(duration=1, max_actions=1, arduino_port=arduino_port),
        },
        "arduinoOptionalStatus": {
            "status": "PASS",
            "requiredForPreflight": False,
            "port": arduino_port,
            "bridgeScriptExists": (root / "telemetry-viewer" / "arduino_input_bridge.py").exists(),
            "note": "Arduino is optional for preflight; live action still uses guarded Arduino execution.",
        },
        "humanClickProfileAvailable": {
            "status": "PASS"
            if _module_check("human_click_profile", ["analyze_recordings", "load_profile"])["status"] == "PASS"
            and (knowledge_dir / "human_click_profile.json").exists()
            else "FAIL",
            "module": _module_check("human_click_profile", ["analyze_recordings", "load_profile"]),
            "profilePath": str(knowledge_dir / "human_click_profile.json"),
        },
        "outputFolderWritable": _folder_writable(output_dir),
    }
    mandatory_failures = [name for name, check in checks.items() if _dict(check).get("status") == "FAIL"]
    warnings: list[str] = []
    launch_warnings = _list(_dict(start_game_command_info).get("launchModeWarnings"))
    warnings.extend(str(item) for item in launch_warnings)
    status = "FAIL" if mandatory_failures else "WARN" if warnings else "PASS"
    return {
        "schema": BOT_PREFLIGHT_SCHEMA,
        "status": status,
        "generatedAtUtc": utc_now(),
        "task": task,
        "mode": "preflight",
        "preflightOnly": True,
        "liveInputExecuted": False,
        "outputFolder": str(output_dir),
        "checks": checks,
        "mandatoryFailures": mandatory_failures,
        "warnings": list(dict.fromkeys(warnings)),
        "errors": [f"{name} failed" for name in mandatory_failures],
        "nextLiveCommand": (
            "python telemetry-viewer\\bot_eval_runner.py --task woodcutting_loop --live --execute-actions "
            "--auto-recover-loaded-scene --record-everything --analyze-after --json"
        ),
    }


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":"), default=str))
            handle.write("\n")
    return path


def _snapshot_base_url(snapshot_url: str) -> str:
    text = str(snapshot_url or "http://127.0.0.1:8893").rstrip("/")
    return text[: -len("/snapshot")] if text.endswith("/snapshot") else text


def _manual_loaded_scene_wait_applicable(readiness: dict[str, Any], recovery: dict[str, Any] | None = None) -> bool:
    root_cause = str(readiness.get("rootCause") or "")
    if root_cause in {"loaded_scene_not_ready", "telemetry_stale"}:
        return True
    errors = " ".join(str(item) for item in _list(readiness.get("errors"))).lower()
    if "loaded_scene_not_ready" in errors or "login_screen" in errors:
        return True
    blocker = str(_dict(recovery).get("blocker") or _dict(recovery).get("failureReason") or "")
    return blocker in {
        "dev_launch_not_loaded",
        "stale_login_screen_after_relaunch",
        "logged_in_without_scene",
        "manual_login_required",
        "play_now_no_transition",
        "disconnected_loop",
    }


def wait_for_manual_loaded_scene_ready(
    output_dir: Path,
    *,
    daemon_url: str = DEFAULT_DAEMON_URL,
    snapshot_url: str = DEFAULT_SNAPSHOT_URL,
    timeout_seconds: float = 600.0,
    poll_interval: float = 2.0,
    max_telemetry_age_ms: int = DEFAULT_MAX_TELEMETRY_AGE_MS,
    sessions_root: str | Path | None = None,
    fetcher: Callable[[str, float], dict[str, Any]] | None = None,
    sleep_func: Callable[[float], None] = time.sleep,
    monotonic_func: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / "manual_loaded_scene_wait.jsonl"
    summary_path = output_dir / "manual_loaded_scene_wait_summary.json"
    started = monotonic_func()
    deadline = started + max(0.0, float(timeout_seconds or 0.0))
    attempt = 0
    last_readiness: dict[str, Any] = {}
    with trace_path.open("w", encoding="utf-8", newline="\n") as handle:
        while True:
            attempt += 1
            last_readiness = check_live_readiness(
                daemon_url=daemon_url,
                snapshot_url=snapshot_url,
                timeout=DEFAULT_READINESS_TIMEOUT_SECONDS,
                max_telemetry_age_ms=max_telemetry_age_ms,
                sessions_root=sessions_root,
                no_input=False,
                fetcher=fetcher,
            )
            record = {
                "schema": "manual_loaded_scene_wait_sample.v1",
                "generatedAtUtc": utc_now(),
                "attemptIndex": attempt,
                "elapsedSeconds": round(max(0.0, monotonic_func() - started), 3),
                "status": last_readiness.get("status"),
                "rootCause": last_readiness.get("rootCause"),
                "loadedSceneReady": bool(last_readiness.get("loadedSceneReady")),
                "liveEvalCanStart": bool(last_readiness.get("liveEvalCanStart")),
                "latestTick": last_readiness.get("latestTick"),
                "latestExportSeq": last_readiness.get("latestExportSeq"),
                "currentArea": last_readiness.get("currentArea"),
                "loadedSceneBlockers": last_readiness.get("loadedSceneBlockers") or [],
                "warnings": last_readiness.get("warnings") or [],
                "errors": last_readiness.get("errors") or [],
            }
            handle.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
            handle.flush()
            if last_readiness.get("status") == "PASS" and last_readiness.get("liveEvalCanStart"):
                summary = {
                    "schema": "manual_loaded_scene_wait_result.v1",
                    "status": "PASS",
                    "reason": "manual_loaded_scene_detected",
                    "elapsedSeconds": round(max(0.0, monotonic_func() - started), 3),
                    "attemptCount": attempt,
                    "tracePath": str(trace_path),
                    "readiness": last_readiness,
                }
                atomic_write_json(summary_path, summary)
                return summary
            now = monotonic_func()
            if now >= deadline:
                summary = {
                    "schema": "manual_loaded_scene_wait_result.v1",
                    "status": "FAIL",
                    "reason": "manual_loaded_scene_timeout",
                    "elapsedSeconds": round(max(0.0, now - started), 3),
                    "attemptCount": attempt,
                    "tracePath": str(trace_path),
                    "readiness": last_readiness,
                }
                atomic_write_json(summary_path, summary)
                return summary
            print(
                "Waiting for loaded scene. Current state: "
                f"{last_readiness.get('rootCause') or last_readiness.get('status') or 'unknown'}",
                file=sys.stderr,
                flush=True,
            )
            sleep_func(max(0.1, min(float(poll_interval or 2.0), max(0.1, deadline - now))))


def _live_action_output_dir(task: str, output_root: str | Path | None = None) -> Path:
    started = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(output_root).expanduser() if output_root else repo_root() / "bot_runs"
    return root / f"{started}_live_woodcutting_loop"


def build_record_everything_command(
    *,
    output_dir: Path,
    label: str,
    stop_file: Path,
    snapshot_url: str = DEFAULT_SNAPSHOT_URL,
) -> list[str]:
    return [
        sys.executable,
        str(repo_root() / "telemetry-viewer" / "manual_recorder.py"),
        "--label",
        label,
        "--description",
        "Instrumented live bot woodcutting loop evaluation.",
        "--until-stopped",
        "--stop-file",
        str(stop_file),
        "--out-dir",
        str(repo_root() / "recordings"),
        "--latest-session",
        "--prefer-active-session",
        "--summary",
        "--include-raw",
        "--telemetry-preflight",
        "--wait-for-fresh-telemetry",
        "--capture-input",
        "--input-backend",
        "polling",
        "--capture-keyboard",
        "--capture-mouse",
        "--capture-window-context",
        "--raw-input-device-attribution",
        "--join-input-telemetry",
        "--input-summary",
        "--camera-behavior",
        "--menu-capture-burst",
        "--menu-burst-until-selection",
        "--menu-burst-tail-ms",
        "300",
        "--preserve-bank-ui",
        "--preserve-combat-state",
        "--plugin-snapshot-url",
        str(snapshot_url),
    ]


def build_live_executor_command(
    *,
    duration: int | float,
    max_actions: int | None,
    daemon_url: str = DEFAULT_DAEMON_URL,
    snapshot_url: str = DEFAULT_SNAPSHOT_URL,
    arduino_port: str = DEFAULT_ARDUINO_PORT,
    debug_dir: Path | None = None,
) -> list[str]:
    duration_seconds = max(1, int(duration or 1200))
    action_limit = max(1, int(max_actions or 300))
    command = [
        sys.executable,
        str(repo_root() / "telemetry-viewer" / "execute_next_action.py"),
        "--daemon-url",
        str(daemon_url),
        "--snapshot-url",
        _snapshot_base_url(snapshot_url),
        "--timeout",
        "15",
        "--backend",
        "arduino",
        "--focus-runelite",
        "--arduino-port",
        str(arduino_port),
        "--input-profile",
        "steady",
        "--movement-profile",
        "linear_debug",
        "--execute",
        "--loop",
        "--verify-after-action",
        "--wait-for-ready",
        "30",
        "--hover-confirm-target",
        "--hover-confirm-timeout-ms",
        "120",
        "--hover-poll-ms",
        "10",
        "--hover-position-tolerance",
        "3",
        "--summary-every-action",
        "--final-reconcile-ms",
        "3000",
        "--final-reconcile-game-ticks",
        "8",
        "--resource-reconcile-ms",
        "4000",
        "--resource-reconcile-game-ticks",
        "8",
        "--pacing-profile",
        "natural",
        "--target-hover-failure-limit",
        "2",
        "--target-suppression-ms",
        "2500",
        "--nav-verify-game-ticks",
        "8",
        "--nav-verify-ms",
        "2500",
        "--max-waypoint-alternates",
        "7",
        "--max-navigation-reacquire-rounds",
        "3",
        "--camera-reacquire-waypoint",
        "--camera-method",
        "auto",
        "--camera-exposure-max-ms",
        "2000",
        "--camera-sample-interval-ms",
        "20",
        "--camera-max-direction-switches",
        "2",
        "--camera-allow-diagonal",
        "--camera-allow-pitch-adjust",
        "--camera-debug-summary",
        "--route-waypoint-lookahead-tiles",
        "12",
        "--route-waypoint-max-horizon-tiles",
        "25",
        "--min-route-progress-tiles",
        "3",
        "--max-route-waypoint-distance",
        "30",
        "--prefer-long-visible-waypoint",
        "--route-waypoint-distance-mode",
        "adaptive",
        "--reject-edge-route-clicks",
        "--camera-reacquire-on-edge-projection",
        "--route-click-edge-margin-px",
        "12",
        "--route-min-visible-area-ratio",
        "0.45",
        "--nav-replan-while-moving",
        "false",
        "--nav-min-game-ticks-between-clicks",
        "3",
        "--nav-stuck-game-ticks",
        "6",
        "--stop-after-lifecycle-cycles",
        "1",
        "--stop-after-post-service-logs",
        "1",
        "--max-total-actions",
        str(action_limit),
        "--max-actions",
        str(action_limit),
        "--max-runtime-seconds",
        str(duration_seconds),
        "--max-wall-time-minutes",
        str(round(duration_seconds / 60.0, 3)),
        "--max-consecutive-timeouts",
        "3",
        "--json",
    ]
    if debug_dir is not None:
        command.extend(
            [
                "--capture-debug-screenshots",
                "--screenshot-on-failure",
                "--screenshot-on-timeout",
                "--screenshot-on-edge-reject",
                "--screenshot-on-lifecycle-transition",
                "--max-debug-screenshots",
                "20",
                "--debug-screenshot-dir",
                str(debug_dir),
            ]
        )
    return command


def build_loaded_scene_recovery_command(
    *,
    daemon_url: str = DEFAULT_DAEMON_URL,
    snapshot_url: str = DEFAULT_SNAPSHOT_URL,
    arduino_port: str = DEFAULT_ARDUINO_PORT,
    max_total_seconds: float = DEFAULT_LIVENESS_RECOVERY_SECONDS,
    max_attempts_per_state: int = DEFAULT_LIVENESS_ATTEMPTS_PER_STATE,
    allow_jagex_launcher_automation: bool = False,
) -> list[str]:
    command = [
        sys.executable,
        str(repo_root() / "telemetry-viewer" / "context_service.py"),
        "--ensure-loaded-scene",
        "--daemon-url",
        str(daemon_url),
        "--snapshot-url",
        _snapshot_base_url(snapshot_url),
        "--arduino-port",
        str(arduino_port),
        "--liveness-max-total-seconds",
        str(max(1.0, float(max_total_seconds or DEFAULT_LIVENESS_RECOVERY_SECONDS))),
        "--liveness-max-attempts-per-state",
        str(max(1, int(max_attempts_per_state or DEFAULT_LIVENESS_ATTEMPTS_PER_STATE))),
    ]
    if allow_jagex_launcher_automation:
        command.append("--allow-jagex-launcher-automation")
    return command


def _recovery_latest_state(payload: dict[str, Any]) -> dict[str, Any]:
    machine = _dict(payload.get("recoveryStateMachine"))
    final_state = _dict(payload.get("finalState"))
    classification = _dict(machine.get("failureClassification"))
    if not classification:
        classification = {
            "failureClass": payload.get("recoveryFailureClass"),
            "reason": payload.get("recoveryFailureReason"),
        }
    return {
        "schema": "bot_loaded_scene_latest_recovery_state.v1",
        "generatedAtUtc": utc_now(),
        "status": payload.get("status"),
        "loadedSceneVerified": bool(payload.get("loadedSceneVerified")),
        "blocker": payload.get("blocker"),
        "recoveryFailureClass": classification.get("failureClass"),
        "recoveryFailureReason": classification.get("reason"),
        "disconnectedLoopDetected": bool(payload.get("disconnectedLoopDetected")),
        "relaunchRequired": bool(payload.get("relaunchRequired")),
        "relaunchAttempted": bool(payload.get("relaunchAttempted")),
        "launchMode": payload.get("launchMode"),
        "launchModeReason": payload.get("launchModeReason"),
        "launchModeWarnings": payload.get("launchModeWarnings") or [],
        "relaunchCommand": payload.get("relaunchCommand"),
        "startGameCommand": payload.get("startGameCommand"),
        "startGameCommandSource": payload.get("startGameCommandSource"),
        "relaunchResult": payload.get("relaunchResult"),
        "relaunchSucceeded": bool(payload.get("relaunchSucceeded")),
        "loadedSceneAfterRelaunch": bool(payload.get("loadedSceneAfterRelaunch")),
        "loginScreenAfterRelaunch": bool(payload.get("loginScreenAfterRelaunch")),
        "finalHotGameState": payload.get("finalHotGameState"),
        "finalLoadedSceneVerified": bool(payload.get("finalLoadedSceneVerified")),
        "finalWorldObjectCount": payload.get("finalWorldObjectCount"),
        "finalTick": payload.get("finalTick"),
        "finalExportSeq": payload.get("finalExportSeq"),
        "initialState": _dict(payload.get("initialState")),
        "finalState": final_state,
        "recoveryStateMachine": machine,
    }


def _recovery_attempt_records(
    *,
    payload: dict[str, Any],
    command: list[str],
    return_code: int,
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    machine = _dict(payload.get("recoveryStateMachine"))
    classification = _dict(machine.get("failureClassification"))
    clicks = _list(machine.get("clickAttempts"))
    records: list[dict[str, Any]] = []
    if clicks:
        for index, click in enumerate(clicks):
            item = _dict(click)
            records.append(
                {
                    "schema": "bot_loaded_scene_recovery_attempt.v1",
                    "generatedAtUtc": utc_now(),
                    "attemptIndex": item.get("attemptIndex", index),
                    "command": command,
                    "returnCode": return_code,
                    "status": summary.get("status"),
                    "loadedSceneVerified": summary.get("loadedSceneVerified"),
                    "blocker": summary.get("blocker"),
                    "recoveryFailureClass": classification.get("failureClass"),
                    "stateBefore": item.get("stateBefore"),
                    "selectedRecoveryAction": item.get("selectedRecoveryAction"),
                    "clickEvidence": item.get("clickEvidence"),
                    "stateAfter": item.get("stateAfter"),
                    "transitionSuccess": bool(item.get("transitionSuccess")),
                    "reason": item.get("reason") or classification.get("reason"),
                }
            )
    for index, attempt in enumerate(_list(machine.get("relaunchAttempts")), start=len(records)):
        item = _dict(attempt)
        records.append(
            {
                "schema": "bot_loaded_scene_recovery_attempt.v1",
                "generatedAtUtc": utc_now(),
                "attemptIndex": item.get("attemptIndex", index),
                "command": command,
                "returnCode": return_code,
                "status": summary.get("status"),
                "loadedSceneVerified": summary.get("loadedSceneVerified"),
                "blocker": summary.get("blocker"),
                "recoveryFailureClass": classification.get("failureClass"),
                "stateBefore": item.get("stateBefore"),
                "selectedRecoveryAction": item.get("selectedRecoveryAction"),
                "startGameCommand": item.get("startGameCommand"),
                "startGameCommandSource": item.get("startGameCommandSource"),
                "launchMode": item.get("launchMode"),
                "relaunchResult": item.get("relaunchResult"),
                "stateAfter": item.get("stateAfter"),
                "transitionSuccess": bool(item.get("transitionSuccess")),
                "reason": item.get("reason") or classification.get("reason"),
            }
        )
    if not records:
        records.append(
            {
                "schema": "bot_loaded_scene_recovery_attempt.v1",
                "generatedAtUtc": utc_now(),
                "attemptIndex": 0,
                "command": command,
                "returnCode": return_code,
                "status": summary["status"],
                "loadedSceneVerified": summary["loadedSceneVerified"],
                "blocker": summary.get("blocker"),
                "recoveryFailureClass": summary.get("recoveryFailureClass"),
                "initialState": _dict(payload.get("initialState")),
                "finalState": _dict(payload.get("finalState")),
                "actionsTaken": _list(payload.get("actionsTaken")),
                "warnings": _list(payload.get("warnings")),
            }
        )
    return records


def run_loaded_scene_recovery(
    output_dir: Path,
    *,
    daemon_url: str = DEFAULT_DAEMON_URL,
    snapshot_url: str = DEFAULT_SNAPSHOT_URL,
    arduino_port: str = DEFAULT_ARDUINO_PORT,
    max_total_seconds: float = DEFAULT_LIVENESS_RECOVERY_SECONDS,
    max_attempts_per_state: int = DEFAULT_LIVENESS_ATTEMPTS_PER_STATE,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    allow_jagex_launcher_automation: bool = False,
) -> dict[str, Any]:
    command = build_loaded_scene_recovery_command(
        daemon_url=daemon_url,
        snapshot_url=snapshot_url,
        arduino_port=arduino_port,
        max_total_seconds=max_total_seconds,
        max_attempts_per_state=max_attempts_per_state,
        allow_jagex_launcher_automation=allow_jagex_launcher_automation,
    )
    stdout_path = output_dir / "loaded_scene_recovery_stdout.json"
    stderr_path = output_dir / "loaded_scene_recovery_stderr.txt"
    started = time.monotonic()
    runner = command_runner or subprocess.run
    completed: subprocess.CompletedProcess[str] | None = None
    with stdout_path.open("w", encoding="utf-8", newline="\n") as stdout, stderr_path.open("w", encoding="utf-8", newline="\n") as stderr:
        completed = runner(
            command,
            cwd=str(repo_root()),
            stdout=stdout,
            stderr=stderr,
            text=True,
            timeout=max(60.0, float(max_total_seconds or DEFAULT_LIVENESS_RECOVERY_SECONDS) + 60.0),
        )
    payload = _parse_json_file(stdout_path)
    latest_state = _recovery_latest_state(payload)
    latest_state_path = output_dir / "latest_recovery_state.json"
    summary = {
        "schema": "bot_loaded_scene_recovery_summary.v1",
        "generatedAtUtc": utc_now(),
        "status": payload.get("status") or ("PASS" if completed.returncode == 0 else "FAIL"),
        "loadedSceneVerified": bool(payload.get("loadedSceneVerified")),
        "blocker": payload.get("blocker"),
        "recoveryFailureClass": payload.get("recoveryFailureClass") or latest_state.get("recoveryFailureClass"),
        "recoveryFailureReason": payload.get("recoveryFailureReason") or latest_state.get("recoveryFailureReason"),
        "disconnectedLoopDetected": bool(payload.get("disconnectedLoopDetected")),
        "relaunchRequired": bool(payload.get("relaunchRequired")),
        "relaunchAttempted": bool(payload.get("relaunchAttempted")),
        "launchMode": payload.get("launchMode"),
        "launchModeReason": payload.get("launchModeReason"),
        "launchModeWarnings": payload.get("launchModeWarnings") or [],
        "relaunchCommand": payload.get("relaunchCommand"),
        "startGameCommand": payload.get("startGameCommand"),
        "startGameCommandSource": payload.get("startGameCommandSource"),
        "relaunchResult": payload.get("relaunchResult"),
        "relaunchSucceeded": bool(payload.get("relaunchSucceeded")),
        "loadedSceneAfterRelaunch": bool(payload.get("loadedSceneAfterRelaunch")),
        "loginScreenAfterRelaunch": bool(payload.get("loginScreenAfterRelaunch")),
        "finalHotGameState": payload.get("finalHotGameState"),
        "finalLoadedSceneVerified": bool(payload.get("finalLoadedSceneVerified")),
        "finalWorldObjectCount": payload.get("finalWorldObjectCount"),
        "finalTick": payload.get("finalTick"),
        "finalExportSeq": payload.get("finalExportSeq"),
        "manualActionRequired": payload.get("manualActionRequired"),
        "nextRecommendation": payload.get("nextRecommendation"),
        "recoveryCommand": command,
        "returnCode": completed.returncode,
        "durationSeconds": round(time.monotonic() - started, 3),
        "stdoutPath": str(stdout_path),
        "stderrPath": str(stderr_path),
        "latestRecoveryStatePath": str(latest_state_path),
        "recoveryStateMachine": latest_state.get("recoveryStateMachine"),
        "payload": payload,
    }
    atomic_write_json(latest_state_path, latest_state)
    atomic_write_json(output_dir / "recovery_summary.json", summary)
    write_jsonl(
        output_dir / "recovery_attempts.jsonl",
        _recovery_attempt_records(payload=payload, command=command, return_code=completed.returncode, summary=summary),
    )
    return summary


def _parse_json_file(path: Path) -> dict[str, Any]:
    text = ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return json.loads(text)
    except (OSError, json.JSONDecodeError):
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _recording_from_recorder_stdout(path: Path, label: str) -> Path | None:
    payload = _parse_json_file(path)
    for key in ("recordingFolder", "outputFolder", "folder", "recordingPath"):
        value = payload.get(key)
        if value:
            candidate = Path(str(value))
            if candidate.exists():
                return candidate
    recordings_root = repo_root() / "recordings"
    if recordings_root.exists():
        matches = [item for item in recordings_root.iterdir() if item.is_dir() and label in item.name]
        if matches:
            return sorted(matches, key=lambda item: item.stat().st_mtime, reverse=True)[0]
    return None


def _analyze_recording(recording: Path, output_dir: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(repo_root() / "telemetry-viewer" / "analyze_manual_recording.py"),
        str(recording),
        "--summary",
        "--schema-gap",
        "--input-trace",
        "--join-input",
        "--camera-behavior",
        "--arduino-trace",
        "--vm-mouse-mapping",
        "--classify-input-actions",
        "--target-match-quality",
        "--menu-interactions",
        "--coordinate-alignment",
        "--input-path-integrity",
        "--arduino-mirror-verification",
        "--menu-row-diagnostics",
        "--woodcutting-lifecycle",
        "--woodcutting-loop-lifecycle",
        "--traversal-lifecycle",
        "--group-traversal-steps",
        "--auto-route-template",
        "--banking-lifecycle",
        "--interruption-lifecycle",
        "--combat-damage-summary",
        "--human-click-profile",
        "--update-knowledge",
        "--json",
    ]
    stdout_path = output_dir / "analyzer_stdout.json"
    stderr_path = output_dir / "analyzer_stderr.txt"
    with stdout_path.open("w", encoding="utf-8", newline="\n") as stdout, stderr_path.open("w", encoding="utf-8", newline="\n") as stderr:
        completed = subprocess.run(command, cwd=str(repo_root()), stdout=stdout, stderr=stderr, text=True, timeout=600)
    return {
        "command": command,
        "returnCode": completed.returncode,
        "stdoutPath": str(stdout_path),
        "stderrPath": str(stderr_path),
        "summaryPath": str(recording / "summary.json"),
        "reportPath": str(recording / "manual_recording_report.md") if (recording / "manual_recording_report.md").exists() else None,
    }


def _trace_records_from_executor_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    action_results = _list(payload.get("actionResults"))
    observations: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    postconditions: list[dict[str, Any]] = []
    executor_blocker = _executor_blocker_from_payload(payload)
    for index, raw in enumerate(action_results):
        item = _dict(raw)
        proposal = _dict(item.get("proposal"))
        readiness = _dict(item.get("readiness"))
        trace = _dict(item.get("actionTrace"))
        observed = _dict(item.get("observedResult"))
        expected = _dict(item.get("expectedResult"))
        click_resolution = _dict(item.get("clickPointResolution"))
        movement_plan = _dict(item.get("movementPlan"))
        target = _dict(proposal.get("selectedTarget") or proposal.get("target") or proposal.get("candidate"))
        target_explanation = _dict(proposal.get("targetExplanation"))
        observations.append(
            {
                "schema": BOT_OBSERVATION_TRACE_SCHEMA,
                "index": index,
                "timestampUtc": utc_now(),
                "tick": proposal.get("sourceTick") or trace.get("sourceTick"),
                "currentArea": _first_present(readiness.get("currentArea"), _dict(readiness.get("context")).get("currentArea")),
                "currentTaskPhase": _first_present(_dict(readiness.get("genericTaskState")).get("phase"), _dict(readiness.get("actionNeed")).get("phase")),
                "inventoryState": _dict(readiness.get("inventory")),
                "routeMonitorState": _dict(readiness.get("routeMonitor")),
                "bankingState": _dict(readiness.get("banking") or readiness.get("bankState")),
                "interruptionState": _dict(readiness.get("interruption")),
                "warnings": item.get("warnings") or [],
            }
        )
        decisions.append(
            {
                "schema": BOT_DECISION_TRACE_SCHEMA,
                "index": index,
                "timestampUtc": utc_now(),
                "tick": proposal.get("sourceTick") or trace.get("sourceTick"),
                "decisionChosen": item.get("proposedAction"),
                "reason": proposal.get("reason") or trace.get("decisionReason") or item.get("verificationStatus"),
                "target": target,
                "readiness": readiness,
                "clickPlanStatus": _dict(proposal.get("humanClickPlan")).get("status"),
                "warnings": item.get("warnings") or [],
                "missingCapabilities": item.get("missingCapabilities") or [],
            }
        )
        candidates.append(
            {
                "schema": BOT_CANDIDATE_TRACE_SCHEMA,
                "index": index,
                "timestampUtc": utc_now(),
                "tick": proposal.get("sourceTick") or trace.get("sourceTick"),
                "candidateType": item.get("proposedAction") or proposal.get("proposedAction"),
                "executable": bool(proposal.get("executable")) if proposal.get("executable") is not None else bool(item.get("executed")),
                "targetKind": proposal.get("targetKind"),
                "targetName": proposal.get("targetName") or target.get("name") or target.get("targetName"),
                "targetTile": proposal.get("targetTile"),
                "plannedClickPoint": _first_present(click_resolution.get("screenPoint"), proposal.get("resolvedScreenClickPoint"), proposal.get("suggestedClickPoint")),
                "reason": proposal.get("reason") or trace.get("decisionReason") or item.get("verificationStatus"),
                "blocker": _dict(item.get("observedResult")).get("observedResult") if item.get("executed") is False else None,
                "status": item.get("status"),
                "verificationStatus": item.get("verificationStatus"),
                "targetExplanation": target_explanation,
                "likelyReason": target_explanation.get("likelyReason"),
                "suggestedFixture": target_explanation.get("suggestedFixture"),
                "safeState": target_explanation.get("safeState"),
                "directPlaneSkipEvidence": target_explanation.get("directPlaneSkipEvidence"),
                "nearestFloorSelectionInteraction": target_explanation.get("nearestFloorSelectionInteraction"),
                "warnings": item.get("warnings") or [],
                "missingCapabilities": item.get("missingCapabilities") or [],
            }
        )
        actions.append(
            {
                "schema": BOT_ACTION_TRACE_SCHEMA,
                "index": index,
                "timestampUtc": utc_now(),
                "actionType": item.get("proposedAction"),
                "targetAction": proposal.get("targetAction") or proposal.get("option"),
                "targetName": target.get("name") or target.get("targetName"),
                "targetQuality": target.get("qualityTier") or target.get("targetQuality"),
                "hoverEvidence": item.get("hoverConfirmation"),
                "plannedClickPoint": _first_present(click_resolution.get("screenPoint"), movement_plan.get("targetPoint")),
                "inputMethodUsed": item.get("backend"),
                "commandSent": item.get("commands"),
                "executed": item.get("executed"),
                "resultObserved": observed,
                "postconditionExpected": expected,
                "postconditionObserved": observed,
                "status": item.get("status"),
                "verificationStatus": item.get("verificationStatus"),
            }
        )
        postconditions.append(
            {
                "schema": BOT_POSTCONDITION_TRACE_SCHEMA,
                "index": index,
                "timestampUtc": utc_now(),
                "expectedPostcondition": expected,
                "observedPostcondition": observed,
                "status": "PASS" if item.get("verificationStatus") in {"PASS", "VERIFIED"} or _dict(item.get("lifecycleState")).get("currentState") == "verified" else item.get("status"),
                "evidence": observed.get("observedSignals") or [],
                "warnings": item.get("warnings") or [],
            }
        )
    if not action_results:
        lifecycle = _dict(payload.get("lifecycleState"))
        loop_summary = _dict(payload.get("loopSummary"))
        reason = lifecycle.get("reason") or payload.get("reason")
        last_action = lifecycle.get("lastAction") or "none"
        observations.append(
            {
                "schema": BOT_OBSERVATION_TRACE_SCHEMA,
                "index": 0,
                "timestampUtc": utc_now(),
                "status": payload.get("status"),
                "reason": reason,
                "tick": lifecycle.get("lastActionTick") or loop_summary.get("lastLifecycleSampleTick"),
                "currentTaskPhase": "blocked" if lifecycle.get("currentState") == "blocked" else "unknown",
                "executorBlocker": executor_blocker,
                "loopSummary": {
                    "candidatesEvaluated": loop_summary.get("candidatesEvaluated"),
                    "proposedActions": loop_summary.get("proposedActions"),
                    "actionsAttempted": loop_summary.get("actionsAttempted"),
                    "actionsExecuted": loop_summary.get("actionsExecuted"),
                    "lastLifecycleSampleTick": loop_summary.get("lastLifecycleSampleTick"),
                    "stopReason": loop_summary.get("stopReason"),
                },
                "warnings": payload.get("warnings") or [],
            }
        )
        decisions.append(
            {
                "schema": BOT_DECISION_TRACE_SCHEMA,
                "index": 0,
                "timestampUtc": utc_now(),
                "tick": lifecycle.get("lastActionTick") or loop_summary.get("lastLifecycleSampleTick"),
                "decisionChosen": last_action,
                "reason": reason,
                "blocker": executor_blocker,
                "target": {},
                "readiness": {},
                "clickPlanStatus": None,
                "warnings": lifecycle.get("warnings") or payload.get("warnings") or [],
                "missingCapabilities": payload.get("missingCapabilities") or [],
            }
        )
        candidates.append(
            {
                "schema": BOT_CANDIDATE_TRACE_SCHEMA,
                "index": 0,
                "timestampUtc": utc_now(),
                "tick": lifecycle.get("lastActionTick") or loop_summary.get("lastLifecycleSampleTick"),
                "candidateType": last_action,
                "executable": False,
                "targetKind": None,
                "targetName": None,
                "targetTile": None,
                "plannedClickPoint": None,
                "reason": reason,
                "blocker": executor_blocker,
                "status": "FAIL" if executor_blocker else payload.get("status"),
                "verificationStatus": "BLOCKED" if executor_blocker else None,
                "targetExplanation": {},
                "warnings": lifecycle.get("warnings") or payload.get("warnings") or [],
                "missingCapabilities": payload.get("missingCapabilities") or [],
            }
        )
        actions.append(
            {
                "schema": BOT_ACTION_TRACE_SCHEMA,
                "index": 0,
                "timestampUtc": utc_now(),
                "actionType": last_action,
                "targetAction": None,
                "targetName": None,
                "targetQuality": None,
                "hoverEvidence": None,
                "plannedClickPoint": None,
                "inputMethodUsed": None,
                "commandSent": [],
                "executed": False,
                "resultObserved": {"resultOutcome": "blocked", "reason": reason},
                "postconditionExpected": lifecycle.get("expectedResult"),
                "postconditionObserved": None,
                "status": "FAIL" if executor_blocker else payload.get("status"),
                "verificationStatus": "BLOCKED" if executor_blocker else None,
                "blocker": executor_blocker,
            }
        )
        postconditions.append(
            {
                "schema": BOT_POSTCONDITION_TRACE_SCHEMA,
                "index": 0,
                "timestampUtc": utc_now(),
                "expectedPostcondition": lifecycle.get("expectedResult"),
                "observedPostcondition": None,
                "status": "FAIL" if executor_blocker else payload.get("status"),
                "evidence": [],
                "warnings": lifecycle.get("warnings") or payload.get("warnings") or [],
                "blocker": executor_blocker,
            }
        )
    return observations, decisions, candidates, actions, postconditions


def _executor_blocker_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not payload:
        return None
    lifecycle = _dict(payload.get("lifecycleState"))
    loop_summary = _dict(payload.get("loopSummary"))
    action_results = _list(payload.get("actionResults"))
    executed_count = int(payload.get("executedActionCount") or 0)
    reason = str(payload.get("reason") or "")
    lifecycle_reason = str(lifecycle.get("reason") or "")
    blocker_reason = lifecycle_reason or reason
    candidates = int(loop_summary.get("candidatesEvaluated") or 0)
    proposed = int(loop_summary.get("proposedActions") or 0)
    attempted = int(loop_summary.get("actionsAttempted") or 0)
    known_fail_closed_blockers = {
        "route_guide_no_same_plane_reentry",
        "route_guide_missing_demonstrated_plane1_recovery_step",
        "route_interaction_live_target_missing",
        "route_context_mismatch_with_recovery_hint",
    }
    for item in action_results:
        record = _dict(item)
        observed = _dict(record.get("observedResult"))
        observed_reason = str(observed.get("observedResult") or record.get("verificationStatus") or "").strip()
        if record.get("executed") is False and observed_reason in known_fail_closed_blockers:
            return {
                "schema": "bot_executor_blocker.v1",
                "blocker": observed_reason,
                "reason": observed_reason,
                "executorReason": reason or None,
                "lifecycleState": lifecycle.get("currentState"),
                "lastAction": lifecycle.get("lastAction") or record.get("proposedAction"),
                "lastActionTick": lifecycle.get("lastActionTick"),
                "candidatesEvaluated": candidates,
                "proposedActions": proposed,
                "actionsAttempted": attempted,
                "lastLifecycleSampleTick": loop_summary.get("lastLifecycleSampleTick"),
                "warnings": lifecycle.get("warnings") or record.get("warnings") or payload.get("warnings") or [],
            }
    if executed_count == 0 and not action_results and blocker_reason in known_fail_closed_blockers:
        return {
            "schema": "bot_executor_blocker.v1",
            "blocker": blocker_reason,
            "reason": blocker_reason,
            "executorReason": reason or None,
            "lifecycleState": lifecycle.get("currentState"),
            "lastAction": lifecycle.get("lastAction"),
            "lastActionTick": lifecycle.get("lastActionTick"),
            "candidatesEvaluated": candidates,
            "proposedActions": proposed,
            "actionsAttempted": attempted,
            "lastLifecycleSampleTick": loop_summary.get("lastLifecycleSampleTick"),
            "warnings": lifecycle.get("warnings") or payload.get("warnings") or [],
        }
    if (
        executed_count == 0
        and not action_results
        and (
            lifecycle_reason == "no_executable_action"
            or reason == "max_runtime_reached"
            and candidates == 0
            and proposed == 0
            and attempted == 0
        )
    ):
        return {
            "schema": "bot_executor_blocker.v1",
            "blocker": "no_executable_action",
            "reason": lifecycle_reason or reason or "no executable action from current context",
            "executorReason": reason or None,
            "lifecycleState": lifecycle.get("currentState"),
            "lastAction": lifecycle.get("lastAction"),
            "lastActionTick": lifecycle.get("lastActionTick"),
            "candidatesEvaluated": candidates,
            "proposedActions": proposed,
            "actionsAttempted": attempted,
            "lastLifecycleSampleTick": loop_summary.get("lastLifecycleSampleTick"),
            "warnings": lifecycle.get("warnings") or [],
        }
    return None


def resolve_recording(recording: str | Path | None = None) -> Path:
    if recording:
        path = Path(recording).expanduser()
        return path if path.is_absolute() else (repo_root() / path).resolve()
    preferred = repo_root() / "recordings" / DEFAULT_FULL_LOOP_RECORDING
    if preferred.exists():
        return preferred
    recordings_root = repo_root() / "recordings"
    candidates: list[Path] = []
    if recordings_root.exists():
        for path in recordings_root.iterdir():
            if path.is_dir() and (path / "woodcutting_loop_lifecycle.json").exists():
                loop = safe_load_json(path / "woodcutting_loop_lifecycle.json")
                if str(loop.get("loopState") or "").lower() == "complete":
                    candidates.append(path)
    if candidates:
        return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)[0]
    raise FileNotFoundError("no full woodcutting loop recording could be resolved")


def load_artifacts(recording: Path) -> dict[str, Any]:
    names = [
        "summary.json",
        "woodcutting_loop_lifecycle.json",
        "woodcutting_lifecycle.json",
        "banking_lifecycle.json",
        "traversal_lifecycle.json",
        "route_template_comparison.json",
        "route_monitor_status.json",
        "route_history_summary.json",
        "interruption_lifecycle.json",
        "combat_damage_summary.json",
        "human_click_profile.json",
        "target_match_summary.json",
        "menu_interaction_summary.json",
        "coordinate_alignment_summary.json",
        "input_path_integrity_summary.json",
    ]
    artifacts = {"recording": recording}
    for name in names:
        key = name.removesuffix(".json")
        artifacts[key] = safe_load_json(recording / name)
    return artifacts


def _first_route_leg(routes: dict[str, Any], phase: str) -> dict[str, Any]:
    for leg in _list(routes.get("routeLegs")):
        if _dict(leg).get("phase") == phase:
            return _dict(leg)
    return {}


def _phase_record(
    *,
    phase: str,
    label: str,
    current: str,
    next_phase: str,
    expected_primitive: str,
    expected_postcondition: str,
    observed: dict[str, Any],
    target: dict[str, Any],
    action: str,
    route_monitor: dict[str, Any] | None = None,
    inventory: dict[str, Any] | None = None,
    bank_open: bool | None = None,
    deposit_complete: bool | None = None,
    interruption: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "label": label,
        "currentPhase": current,
        "nextExpectedPhase": next_phase,
        "expectedPrimitive": expected_primitive,
        "expectedPostcondition": expected_postcondition,
        "observed": observed,
        "target": target,
        "action": action,
        "routeMonitor": route_monitor or {},
        "inventory": inventory or {},
        "bankOpen": bank_open,
        "depositComplete": deposit_complete,
        "interruption": interruption or {},
        "notes": notes or [],
    }


def build_phase_records(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    loop = _dict(artifacts.get("woodcutting_loop_lifecycle"))
    woodcutting = _dict(loop.get("woodcutting") or artifacts.get("woodcutting_lifecycle"))
    banking = _dict(loop.get("banking") or artifacts.get("banking_lifecycle"))
    routes = _dict(loop.get("routes") or artifacts.get("traversal_lifecycle"))
    interruptions = _dict(loop.get("interruptions") or artifacts.get("interruption_lifecycle"))
    damage = _dict(artifacts.get("combat_damage_summary"))
    route_to_bank = _first_route_leg(routes, "route_to_bank")
    route_to_trees = _first_route_leg(routes, "route_to_trees")
    logs_gained = woodcutting.get("cycleLogsGained") or woodcutting.get("normalLogsGained")
    deposited_items = _list(banking.get("depositedItems"))
    base_tree_target = {
        "name": "Tree",
        "kind": "object",
        "targetQuality": "unknown",
        "onScreen": None,
        "geometryAvailable": False,
    }
    bank_target = {
        "name": "Bank",
        "kind": "service",
        "targetQuality": "unknown",
        "onScreen": None,
        "geometryAvailable": False,
    }
    deposit_target = {
        "name": "Deposit inventory",
        "kind": "bank_ui",
        "targetQuality": "unknown",
        "onScreen": None,
        "geometryAvailable": False,
    }
    phases: list[dict[str, Any]] = []
    if woodcutting:
        phases.append(
            _phase_record(
                phase="woodcutting",
                label="Woodcutting",
                current="cutting",
                next_phase="continue_cutting",
                expected_primitive="collect",
                expected_postcondition="animation_879_or_logs_gained",
                observed={
                    "status": woodcutting.get("status"),
                    "logsGained": logs_gained,
                    "freshChopClickCount": woodcutting.get("freshChopClickCount"),
                    "animationSnapshots": woodcutting.get("activeSnapshotCount"),
                    "passed": bool((logs_gained or 0) > 0 or woodcutting.get("activeSnapshotCount")),
                },
                target=base_tree_target,
                action="Chop down",
                inventory={"freeSlots": 12, "inventoryFull": False},
                bank_open=False,
                deposit_complete=False,
            )
        )
        if woodcutting.get("inventoryFull") or woodcutting.get("inventoryFilledDuringLoop"):
            phases.append(
                _phase_record(
                    phase="inventory_full",
                    label="Inventory Full",
                    current="inventory_full",
                    next_phase="route_to_bank",
                    expected_primitive="bank",
                    expected_postcondition="route_to_bank_selected",
                    observed={
                        "status": "PASS",
                        "inventoryFull": True,
                        "logsGained": logs_gained,
                        "passed": True,
                    },
                    target=bank_target,
                    action="route_to_bank",
                    route_monitor={
                        "routeState": "ready",
                        "routeName": "woodcutting_area_to_bank",
                        "currentArea": "woodcutting_area",
                        "offRoute": False,
                    },
                    inventory={"freeSlots": 0, "inventoryFull": True},
                    bank_open=False,
                    deposit_complete=False,
                )
            )
    if route_to_bank:
        phases.append(
            _phase_record(
                phase="route_to_bank",
                label="Route to Bank",
                current="routing_to_bank",
                next_phase="route_to_bank",
                expected_primitive="bank",
                expected_postcondition="arrive_bank_area",
                observed={
                    "status": route_to_bank.get("status"),
                    "routeName": route_to_bank.get("routeName"),
                    "matched": route_to_bank.get("matched"),
                    "fromArea": route_to_bank.get("fromArea"),
                    "toArea": route_to_bank.get("toArea"),
                    "passed": route_to_bank.get("status") == "PASS" and route_to_bank.get("matched") is not False,
                },
                target={"name": route_to_bank.get("routeName") or "Bank route", "kind": "route", "geometryAvailable": False},
                action="route_to_bank",
                route_monitor={
                    "routeState": "in_progress",
                    "routeName": route_to_bank.get("routeName"),
                    "currentArea": route_to_bank.get("fromArea"),
                    "nextExpectedSegment": route_to_bank.get("transition"),
                    "offRoute": False,
                },
                inventory={"freeSlots": 0, "inventoryFull": True},
                bank_open=False,
                deposit_complete=False,
            )
        )
    if banking:
        phases.append(
            _phase_record(
                phase="banking_deposit",
                label="Banking / Deposit",
                current="banking",
                next_phase="banking_deposit",
                expected_primitive="deposit",
                expected_postcondition="deposit_logs",
                observed={
                    "status": banking.get("status"),
                    "bankOpenSeen": banking.get("bankOpenSeen"),
                    "bankContainerDeltaAvailable": banking.get("bankContainerDeltaAvailable"),
                    "depositedItems": deposited_items,
                    "passed": bool(banking.get("depositDetected") and banking.get("bankContainerDeltaAvailable")),
                },
                target=deposit_target,
                action="Deposit",
                inventory={"freeSlots": 0, "inventoryFull": True},
                bank_open=bool(banking.get("bankOpenSeen")),
                deposit_complete=False,
            )
        )
    if banking.get("depositDetected"):
        phases.append(
            _phase_record(
                phase="deposit_complete",
                label="Deposit Complete",
                current="deposit_complete",
                next_phase="route_to_woodcutting_area",
                expected_primitive="return_to_resource",
                expected_postcondition="route_to_trees_selected",
                observed={
                    "status": "PASS",
                    "depositedItems": deposited_items,
                    "depositConfirmationLevel": banking.get("depositConfirmationLevel"),
                    "passed": True,
                },
                target={"name": route_to_trees.get("routeName") or "Woodcutting area route", "kind": "route", "geometryAvailable": False},
                action="route_to_woodcutting_area",
                route_monitor={
                    "routeState": "ready",
                    "routeName": route_to_trees.get("routeName") or "Bank_to_Woodcutting_area",
                    "currentArea": "bank_area",
                    "offRoute": False,
                },
                inventory={"freeSlots": 28, "inventoryFull": False},
                bank_open=bool(banking.get("bankOpenSeen")),
                deposit_complete=True,
            )
        )
    if route_to_trees:
        phases.append(
            _phase_record(
                phase="route_to_trees",
                label="Route to Trees",
                current="routing_to_trees",
                next_phase="route_to_woodcutting_area",
                expected_primitive="return_to_resource",
                expected_postcondition="arrive_woodcutting_area",
                observed={
                    "status": route_to_trees.get("status"),
                    "routeName": route_to_trees.get("routeName"),
                    "matched": route_to_trees.get("matched"),
                    "fromArea": route_to_trees.get("fromArea"),
                    "toArea": route_to_trees.get("toArea"),
                    "passed": route_to_trees.get("status") == "PASS" and route_to_trees.get("matched") is not False,
                },
                target={"name": route_to_trees.get("routeName") or "Woodcutting area route", "kind": "route", "geometryAvailable": False},
                action="route_to_woodcutting_area",
                route_monitor={
                    "routeState": "in_progress",
                    "routeName": route_to_trees.get("routeName"),
                    "currentArea": route_to_trees.get("fromArea"),
                    "nextExpectedSegment": route_to_trees.get("transition"),
                    "offRoute": False,
                },
                inventory={"freeSlots": 28, "inventoryFull": False},
                bank_open=False,
                deposit_complete=True,
            )
        )
    if interruptions.get("interruptionDetected"):
        task_resumed = bool(interruptions.get("taskResumed"))
        phases.append(
            _phase_record(
                phase="interruption",
                label="Interruption",
                current="interrupted",
                next_phase="continue_current_phase" if task_resumed else "recover_or_resume_task",
                expected_primitive="collect" if task_resumed else "wait_for_evidence",
                expected_postcondition="task_resumed" if task_resumed else "normal_loop_paused",
                observed={
                    "status": interruptions.get("status"),
                    "interruptionType": interruptions.get("interruptionType"),
                    "primaryCause": interruptions.get("primaryCause"),
                    "taskResumed": task_resumed,
                    "damageStatus": damage.get("status"),
                    "passed": task_resumed,
                },
                target=base_tree_target,
                action="Chop down" if task_resumed else "recover_or_resume_task",
                inventory={"freeSlots": 12, "inventoryFull": False},
                bank_open=False,
                deposit_complete=False,
                interruption=interruptions,
            )
        )
    if _dict(loop.get("currentPhase")).get("phase") == "complete" or loop.get("loopState") == "complete":
        phases.append(
            _phase_record(
                phase="resumed_cutting",
                label="Resume Cutting",
                current="resumed_cutting",
                next_phase="continue_current_phase",
                expected_primitive="collect",
                expected_postcondition="continue_cutting_after_loop",
                observed={
                    "status": "PASS",
                    "taskResumed": bool(interruptions.get("taskResumed", True)),
                    "loopState": loop.get("loopState"),
                    "passed": True,
                },
                target=base_tree_target,
                action="Chop down",
                inventory={"freeSlots": 28, "inventoryFull": False},
                bank_open=False,
                deposit_complete=True,
            )
        )
    return phases


def _wrap_runtime_variable(value: Any, source: str = "bot_eval_replay") -> dict[str, Any]:
    return {"observed": True, "value": value, "source": source}


def build_runtime_evidence(phase: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    variables = {
        "loadedScene": {"loadedSceneVerified": True, "livenessState": "replay"},
        "inventory": phase.get("inventory") or {},
        "resourceCount": 0 if phase.get("depositComplete") else 28,
        "bankOpen": phase.get("bankOpen"),
        "routeProgress": phase.get("routeMonitor") or {},
        "routeMonitor": phase.get("routeMonitor") or {},
        "woodcuttingLoopLifecycle": source.get("woodcuttingLoopLifecycle"),
        "woodcuttingLifecycle": source.get("woodcuttingLifecycle"),
        "bankState": source.get("bankState"),
        "depositResult": source.get("depositResult"),
        "interruptionLifecycle": source.get("interruptionLifecycle"),
        "combatDamageSummary": source.get("combatDamageSummary"),
        "humanClickProfile": source.get("humanClickProfile"),
        "inputIntegrity": {
            "phaseCounts": {
                "live_action_phase": {
                    "injectedEventsDelta": 0,
                    "lowerIlInjectedEventsDelta": 0,
                    "directBackendBypassCountDelta": 0,
                    "hardBlocker": False,
                }
            },
            "current": {"directBackendBypassCount": 0},
        },
    }
    return {
        "schema": task_script_api.TASK_RUNTIME_EVIDENCE_SCHEMA,
        "generatedAtUtc": utc_now(),
        "mode": "replay",
        "data": {
            "runtimeVariables": {name: _wrap_runtime_variable(value) for name, value in variables.items()},
            "readinessSummary": {
                "manualLoginRequired": False,
                "loadedSceneProof": {"loadedSceneVerified": True},
                "currentIntent": phase.get("currentPhase"),
            },
            "liveValidationPossibleNow": False,
        },
    }


def build_action_visibility(phase: dict[str, Any]) -> dict[str, Any]:
    target = _dict(phase.get("target"))
    action = phase.get("action")
    execution_allowed = bool(action not in {"unknown", "recover_or_resume_task"})
    return {
        "schema": "action_input_visibility_context.v1",
        "status": "PASS" if execution_allowed else "WARN",
        "generatedAtUtc": utc_now(),
        "mode": "replay",
        "data": {
            "plannedAction": action,
            "plannedTarget": target,
            "plannedScreenPoint": target.get("aimPoint"),
            "hoverConfirmationEvidence": {
                "confirmed": action == "Chop down",
                "topOption": "Chop down" if action == "Chop down" else None,
                "topTarget": "Tree" if action == "Chop down" else None,
            },
            "menuOptionClickedEvidence": {
                "option": "Chop down" if action == "Chop down" else action,
                "target": target.get("name"),
            },
            "readiness": {
                "manualLoginRequired": False,
                "loadedSceneProof": {"loadedSceneVerified": True},
                "actionReadiness": {
                    "status": "PASS" if execution_allowed else "WARN",
                    "executionAllowed": execution_allowed,
                    "blockers": [],
                    "warnings": ["replay_mode_no_live_execution"],
                },
            },
            "actionReadiness": {
                "status": "PASS" if execution_allowed else "WARN",
                "executionAllowed": execution_allowed,
                "blockers": [],
                "warnings": ["replay_mode_no_live_execution"],
            },
        },
    }


def clean_failure_classification() -> dict[str, Any]:
    return {
        "schema": task_script_api.TASK_FAILURE_CLASSIFICATION_SCHEMA,
        "status": "PASS",
        "primaryClassification": None,
        "blockers": [],
        "warnings": [],
        "inputIntegrityAssessment": {
            "liveActionHardBlocker": False,
            "directBackendBypassCount": 0,
            "operatorNoiseOnly": False,
        },
    }


def build_phase_source(phase: dict[str, Any], artifacts: dict[str, Any]) -> dict[str, Any]:
    loop = _dict(artifacts.get("woodcutting_loop_lifecycle"))
    woodcutting = _dict(loop.get("woodcutting") or artifacts.get("woodcutting_lifecycle"))
    banking = _dict(loop.get("banking") or artifacts.get("banking_lifecycle"))
    interruptions = _dict(loop.get("interruptions") or artifacts.get("interruption_lifecycle"))
    damage = _dict(artifacts.get("combat_damage_summary"))
    profile = _dict(artifacts.get("human_click_profile"))
    route_monitor = _dict(phase.get("routeMonitor"))
    loop_compact = {
        "schema": "woodcutting_loop_lifecycle_compact.v1",
        "status": loop.get("status") or "WARN",
        "loopState": phase.get("currentPhase"),
        "currentPhase": phase.get("currentPhase"),
        "nextExpectedPhase": phase.get("nextExpectedPhase"),
        "confidence": loop.get("confidence"),
        "inventoryFull": bool(_dict(phase.get("inventory")).get("inventoryFull")),
        "depositedLogs": bool(banking.get("depositDetected")),
        "depositedItems": banking.get("depositedItems") or [],
        "interruptionDetected": bool(interruptions.get("interruptionDetected")),
        "taskResumed": bool(interruptions.get("taskResumed")),
        "warnings": loop.get("warnings") or [],
        "missingCapabilities": loop.get("missingCapabilities") or [],
    }
    deposit_result = {
        "depositComplete": bool(phase.get("depositComplete")),
        "depositedItems": banking.get("depositedItems") or [],
        "totalDepositedCount": banking.get("depositedItemCount") or 0,
        "depositConfirmationLevel": banking.get("depositConfirmationLevel"),
        "bankContainerDeltaAvailable": banking.get("bankContainerDeltaAvailable"),
        "inventoryFreeSlotsAfter": banking.get("freeSlotsAfter"),
        "confidence": banking.get("confidence"),
        "missingCapabilities": banking.get("missingCapabilities") or [],
        "warnings": banking.get("warnings") or [],
    }
    bank_state = {
        "bankOpen": phase.get("bankOpen"),
        "depositBoxOpen": banking.get("depositBoxOpenSeen"),
        "activeBankLikeInterface": banking.get("bankLikeInterface"),
        "bankContainerAvailable": banking.get("bankContainerAvailable"),
        "bankContainerDeltaAvailable": banking.get("bankContainerDeltaAvailable"),
        "warnings": banking.get("warnings") or [],
        "missingCapabilities": banking.get("missingCapabilities") or [],
    }
    return {
        "schema": "bot_eval_phase_source.v1",
        "phase": phase.get("phase"),
        "target": phase.get("target") or {},
        "action": phase.get("action"),
        "woodcuttingLoopLifecycle": loop_compact,
        "woodcuttingLifecycle": woodcutting,
        "bankingLifecycle": banking,
        "bankState": bank_state,
        "depositResult": deposit_result,
        "routeMonitor": route_monitor,
        "interruptionLifecycle": interruptions,
        "combatDamageSummary": damage,
        "humanClickProfile": profile,
    }


def evaluate_phase(
    phase: dict[str, Any],
    artifacts: dict[str, Any],
    script: dict[str, Any],
    index: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    phase_source = build_phase_source(phase, artifacts)
    runtime = build_runtime_evidence(phase, phase_source)
    visibility = build_action_visibility(phase)
    failure = clean_failure_classification()
    readiness = task_script_api.assess_task_run_readiness(
        script,
        runtime_evidence=runtime,
        action_input_visibility=visibility,
        failure_classification=failure,
        navigation_decision_trace={
            "schema": "navigation_decision_trace_summary.v1",
            "status": "PASS",
            "data": {"tracePresent": False, "blockingEligible": False, "diagnosticOnly": True},
        },
    )
    readiness_data = _dict(readiness.get("data"))
    inference = _dict(readiness_data.get("inferredNextPrimitive"))
    if not inference:
        current_lifecycle = _dict(readiness_data.get("currentLifecycle"))
        inference = _dict(current_lifecycle.get("inferredNextPrimitive"))
        if not inference and current_lifecycle.get("inferredNextPrimitive"):
            inference = {
                "primitive": current_lifecycle.get("inferredNextPrimitive"),
                "reason": current_lifecycle.get("inferenceReason"),
                "confidence": current_lifecycle.get("confidence"),
            }
    inferred = str(inference.get("primitive") or "unknown")
    expected = str(phase.get("expectedPrimitive") or "unknown")
    decision_ok = inferred == expected
    click_plan = task_script_api.get_next_click_plan(phase_source)
    warnings = list(dict.fromkeys(_list(readiness.get("warnings")) + _list(click_plan.get("warnings")) + _list(phase_source.get("woodcuttingLoopLifecycle", {}).get("warnings"))))
    missing = list(dict.fromkeys(_list(readiness.get("missingCapabilities")) + _list(click_plan.get("missingCapabilities"))))
    observation = {
        "schema": BOT_OBSERVATION_TRACE_SCHEMA,
        "index": index,
        "timestampUtc": utc_now(),
        "phase": phase.get("phase"),
        "currentTaskPhase": phase.get("currentPhase"),
        "nextExpectedPhase": phase.get("nextExpectedPhase"),
        "routeMonitorState": _dict(phase.get("routeMonitor")).get("routeState"),
        "bankingState": phase_source.get("bankState"),
        "woodcuttingState": {
            "inventory": phase.get("inventory"),
            "logsGained": _dict(phase.get("observed")).get("logsGained"),
        },
        "interruptionState": phase_source.get("interruptionLifecycle"),
        "sourceMode": "replay",
    }
    decision = {
        "schema": BOT_DECISION_TRACE_SCHEMA,
        "index": index,
        "timestampUtc": utc_now(),
        "phase": phase.get("phase"),
        "currentTaskPhase": phase.get("currentPhase"),
        "nextExpectedPhase": phase.get("nextExpectedPhase"),
        "routeMonitorState": phase_source.get("routeMonitor"),
        "bankingState": phase_source.get("bankState"),
        "woodcuttingLoopState": phase_source.get("woodcuttingLoopLifecycle"),
        "interruptionState": phase_source.get("interruptionLifecycle"),
        "clickPlan": {
            "status": click_plan.get("status"),
            "action": click_plan.get("action"),
            "confidence": click_plan.get("confidence"),
            "plannedPoint": _dict(click_plan.get("aim")).get("plannedPoint"),
            "basePoint": _dict(click_plan.get("aim")).get("basePoint"),
            "warnings": click_plan.get("warnings"),
            "blockers": _dict(click_plan.get("readiness")).get("blockedReasons"),
        },
        "decisionChosen": inferred,
        "expectedDecision": expected,
        "decisionMatchesExpectation": decision_ok,
        "reason": inference.get("reason"),
        "confidence": inference.get("confidence"),
        "warnings": warnings,
        "missingCapabilities": missing,
    }
    action = {
        "schema": BOT_ACTION_TRACE_SCHEMA,
        "index": index,
        "timestampUtc": utc_now(),
        "phase": phase.get("phase"),
        "actionType": inferred,
        "targetActionName": phase.get("action"),
        "targetName": _dict(phase.get("target")).get("name"),
        "plannedClickPoint": _dict(click_plan.get("aim")).get("plannedPoint"),
        "centerPoint": _dict(click_plan.get("aim")).get("basePoint"),
        "profilePoint": _dict(click_plan.get("aim")).get("plannedPoint"),
        "clickPolicy": "advisory_only_replay",
        "inputMode": "no_live_input",
        "commandSent": None,
        "wouldBlockLiveClick": click_plan.get("status") == "FAIL",
        "result": "not_executed_replay",
    }
    observed = _dict(phase.get("observed"))
    postcondition = {
        "schema": BOT_POSTCONDITION_TRACE_SCHEMA,
        "index": index,
        "timestampUtc": utc_now(),
        "phase": phase.get("phase"),
        "expectedPostcondition": phase.get("expectedPostcondition"),
        "observedPostcondition": observed,
        "status": "PASS" if observed.get("passed") else "WARN",
        "evidence": [item for item in [
            f"expected_primitive={expected}",
            f"inferred_primitive={inferred}",
            f"next_expected_phase={phase.get('nextExpectedPhase')}",
            f"route={observed.get('routeName')}" if observed.get("routeName") else None,
            f"deposited={observed.get('depositedItems')}" if observed.get("depositedItems") else None,
            f"interruption={observed.get('interruptionType')}" if observed.get("interruptionType") else None,
        ] if item],
        "warnings": [] if observed.get("passed") else ["expected postcondition not proven in replay artifacts"],
    }
    return observation, decision, action, postcondition


def summarize(
    recording: Path,
    output_dir: Path,
    observations: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    postconditions: list[dict[str, Any]],
    *,
    command: list[str],
) -> dict[str, Any]:
    mismatches = [
        {
            "phase": item.get("phase"),
            "expected": item.get("expectedDecision"),
            "actual": item.get("decisionChosen"),
            "reason": item.get("reason"),
        }
        for item in decisions
        if item.get("decisionMatchesExpectation") is not True
    ]
    postcondition_warnings = [
        {
            "phase": item.get("phase"),
            "status": item.get("status"),
            "warnings": item.get("warnings"),
        }
        for item in postconditions
        if item.get("status") != "PASS"
    ]
    click_plan_counts: dict[str, int] = {}
    for decision in decisions:
        status = str(_dict(decision.get("clickPlan")).get("status") or "UNKNOWN")
        click_plan_counts[status] = click_plan_counts.get(status, 0) + 1
    phase_results = {
        item.get("phase"): {
            "decision": _dict(next((d for d in decisions if d.get("index") == item.get("index")), {})).get("decisionChosen"),
            "postconditionStatus": item.get("status"),
        }
        for item in postconditions
    }
    status = "FAIL" if mismatches else "WARN" if postcondition_warnings else "PASS"
    return {
        "schema": BOT_EVAL_SUMMARY_SCHEMA,
        "status": status,
        "generatedAtUtc": utc_now(),
        "task": "woodcutting_loop",
        "mode": "replay",
        "command": command,
        "recordingFolder": str(recording),
        "outputFolder": str(output_dir),
        "decisionCount": len(decisions),
        "actionCount": len(actions),
        "postconditionCount": len(postconditions),
        "decisionMismatchCount": len(mismatches),
        "postconditionWarningCount": len(postcondition_warnings),
        "clickPlanStatusCounts": click_plan_counts,
        "phaseResults": phase_results,
        "mismatches": mismatches,
        "postconditionWarnings": postcondition_warnings,
        "scriptApiCallsUsed": [
            "assess_task_run_readiness",
            "get_next_click_plan",
            "get_woodcutting_loop_lifecycle",
            "get_deposit_result",
            "get_route_monitor_status",
            "get_interruption_lifecycle",
            "get_combat_damage_summary",
        ],
        "artifacts": {
            "manifest": str(output_dir / "bot_eval_manifest.json"),
            "decisions": str(output_dir / "bot_decision_trace.jsonl"),
            "actions": str(output_dir / "bot_action_trace.jsonl"),
            "observations": str(output_dir / "bot_observation_trace.jsonl"),
            "postconditions": str(output_dir / "bot_postcondition_trace.jsonl"),
            "summary": str(output_dir / "bot_eval_summary.json"),
        },
        "warnings": [
            "live daemon was unavailable; evaluation used replay mode",
            "human click plan remained advisory; no profile-informed live click was executed",
        ],
    }


def run_live_smoke(
    *,
    task: str = "woodcutting_loop",
    output_root: str | Path | None = None,
    mode: str = "live_smoke",
    duration: int | float = 10,
    daemon_url: str = DEFAULT_DAEMON_URL,
    snapshot_url: str = DEFAULT_SNAPSHOT_URL,
    timeout: float = DEFAULT_READINESS_TIMEOUT_SECONDS,
    max_telemetry_age_ms: int = DEFAULT_MAX_TELEMETRY_AGE_MS,
    sessions_root: str | Path | None = None,
    poll_interval: float = 1.0,
    no_input: bool = True,
    dry_run_actions: bool = True,
    require_readiness_pass: bool = False,
    stop_on_warning: bool = False,
    command: list[str] | None = None,
    fetcher: Callable[[str, float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if task != "woodcutting_loop":
        raise ValueError("only task=woodcutting_loop is supported by this bounded evaluator")
    started = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(output_root).expanduser() if output_root else repo_root() / "bot_runs"
    suffix = "live_dry_run" if mode == "live_dry_run" else "live_smoke"
    output_dir = root / f"{started}_{task}_{suffix}"
    output_dir.mkdir(parents=True, exist_ok=True)
    duration_seconds = max(0.0, float(duration or 0))
    poll_seconds = max(0.1, float(poll_interval or 1.0))
    started_mono = time.monotonic()
    observations: list[dict[str, Any]] = []
    readiness_history: list[dict[str, Any]] = []
    index = 0
    while True:
        readiness = check_live_readiness(
            daemon_url=daemon_url,
            snapshot_url=snapshot_url,
            timeout=timeout,
            max_telemetry_age_ms=max_telemetry_age_ms,
            sessions_root=sessions_root,
            no_input=no_input,
            fetcher=fetcher,
        )
        readiness_history.append(readiness)
        click_plan = _dict(_dict(readiness.get("taskProbe")).get("clickPlan"))
        observations.append(
            {
                "schema": BOT_OBSERVATION_TRACE_SCHEMA,
                "index": index,
                "timestampUtc": utc_now(),
                "mode": mode,
                "sourceMode": "live_no_input",
                "currentTaskPhase": "unknown",
                "nextExpectedPhase": "unknown",
                "routeMonitorState": "unknown",
                "readinessStatus": readiness.get("status"),
                "contextServiceReachable": readiness.get("contextServiceReachable"),
                "telemetryFresh": readiness.get("telemetryFresh"),
                "latestTick": readiness.get("latestTick"),
                "latestExportSeq": readiness.get("latestExportSeq"),
                "currentArea": readiness.get("currentArea"),
                "clickPlan": {
                    "status": click_plan.get("status"),
                    "action": click_plan.get("action"),
                    "confidence": click_plan.get("confidence"),
                    "plannedPoint": _dict(click_plan.get("aim")).get("plannedPoint"),
                    "warnings": click_plan.get("warnings"),
                    "blockers": _dict(click_plan.get("readiness")).get("blockedReasons"),
                },
                "warnings": readiness.get("warnings") or [],
                "errors": readiness.get("errors") or [],
            }
        )
        index += 1
        if stop_on_warning and readiness.get("status") in {"WARN", "FAIL"}:
            break
        if time.monotonic() - started_mono >= duration_seconds:
            break
        time.sleep(min(poll_seconds, max(0.0, duration_seconds - (time.monotonic() - started_mono))))

    final_readiness = readiness_history[-1] if readiness_history else check_live_readiness(
        daemon_url=daemon_url,
        snapshot_url=snapshot_url,
        timeout=timeout,
        max_telemetry_age_ms=max_telemetry_age_ms,
        sessions_root=sessions_root,
        no_input=no_input,
        fetcher=fetcher,
    )
    action_records: list[dict[str, Any]] = []
    summary_status = "PASS" if final_readiness.get("liveEvalCanStart") else "FAIL"
    if final_readiness.get("status") == "WARN":
        summary_status = "WARN"
    if require_readiness_pass and final_readiness.get("status") != "PASS":
        summary_status = "FAIL"
    summary = {
        "schema": BOT_EVAL_SUMMARY_SCHEMA,
        "status": summary_status,
        "generatedAtUtc": utc_now(),
        "task": task,
        "mode": mode,
        "command": command or [],
        "outputFolder": str(output_dir),
        "durationSeconds": duration_seconds,
        "pollIntervalSeconds": poll_seconds,
        "observationCount": len(observations),
        "decisionCount": 0,
        "actionCount": 0,
        "postconditionCount": 0,
        "actionCommandsSent": 0,
        "liveInputExecuted": False,
        "noInput": bool(no_input),
        "dryRunActions": bool(dry_run_actions),
        "requireReadinessPass": bool(require_readiness_pass),
        "stopOnWarning": bool(stop_on_warning),
        "readiness": final_readiness,
        "liveEvalCanStart": bool(final_readiness.get("liveEvalCanStart")),
        "boundedLiveDryRunReady": bool(final_readiness.get("liveEvalCanStart")),
        "artifacts": {
            "readiness": str(output_dir / "bot_live_readiness.json"),
            "observations": str(output_dir / "bot_observation_trace.jsonl"),
            "actions": str(output_dir / "bot_action_trace.jsonl"),
            "summary": str(output_dir / "bot_eval_summary.json"),
        },
        "warnings": final_readiness.get("warnings") or [],
        "errors": final_readiness.get("errors") or [],
    }
    manifest = {
        "schema": BOT_EVAL_MANIFEST_SCHEMA,
        "generatedAtUtc": utc_now(),
        "task": task,
        "mode": mode,
        "outputFolder": str(output_dir),
        "daemonUrl": daemon_url,
        "snapshotUrl": snapshot_url,
        "durationSeconds": duration_seconds,
        "noInput": bool(no_input),
        "dryRunActions": bool(dry_run_actions),
        "liveInputExecuted": False,
    }
    atomic_write_json(output_dir / "bot_eval_manifest.json", manifest)
    atomic_write_json(output_dir / "bot_live_readiness.json", final_readiness)
    write_jsonl(output_dir / "bot_observation_trace.jsonl", observations)
    write_jsonl(output_dir / "bot_action_trace.jsonl", action_records)
    atomic_write_json(output_dir / "bot_eval_summary.json", summary)
    return summary


def run_live_action(
    *,
    task: str = "woodcutting_loop",
    output_root: str | Path | None = None,
    duration: int | float = 1200,
    max_actions: int | None = 300,
    daemon_url: str = DEFAULT_DAEMON_URL,
    snapshot_url: str = DEFAULT_SNAPSHOT_URL,
    timeout: float = DEFAULT_READINESS_TIMEOUT_SECONDS,
    max_telemetry_age_ms: int = DEFAULT_MAX_TELEMETRY_AGE_MS,
    arduino_port: str = DEFAULT_ARDUINO_PORT,
    record_everything: bool = True,
    analyze_after: bool = True,
    require_readiness_pass: bool = True,
    auto_recover_loaded_scene: bool = False,
    liveness_max_total_seconds: float = DEFAULT_LIVENESS_RECOVERY_SECONDS,
    liveness_max_attempts_per_state: int = DEFAULT_LIVENESS_ATTEMPTS_PER_STATE,
    allow_jagex_launcher_automation: bool = False,
    wait_for_manual_loaded_scene: bool = False,
    manual_loaded_scene_timeout_seconds: float = 600.0,
    sessions_root: str | Path | None = None,
    command: list[str] | None = None,
    fetcher: Callable[[str, float], dict[str, Any]] | None = None,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    recovery_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    recorder_popen: Callable[..., subprocess.Popen[str]] | None = None,
    runtime_control_poster: Callable[[str, dict[str, Any], float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if task != "woodcutting_loop":
        raise ValueError("only task=woodcutting_loop is supported by this bounded evaluator")
    output_dir = _live_action_output_dir(task, output_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    debug_dir = output_dir / "debug_screenshots"
    recovery_summary: dict[str, Any] | None = None
    manual_wait_summary: dict[str, Any] | None = None
    runtime_control_summary = configure_live_loop_runtime_control(
        daemon_url=daemon_url,
        timeout=max(1.0, min(float(timeout or DEFAULT_READINESS_TIMEOUT_SECONDS), 5.0)),
        poster=runtime_control_poster,
    )
    atomic_write_json(output_dir / "bot_runtime_control.json", runtime_control_summary)
    if auto_recover_loaded_scene:
        recovery_summary = run_loaded_scene_recovery(
            output_dir,
            daemon_url=daemon_url,
            snapshot_url=snapshot_url,
            arduino_port=arduino_port,
            max_total_seconds=liveness_max_total_seconds,
            max_attempts_per_state=liveness_max_attempts_per_state,
            command_runner=recovery_runner,
            allow_jagex_launcher_automation=allow_jagex_launcher_automation,
        )
    readiness = check_live_readiness(
        daemon_url=daemon_url,
        snapshot_url=snapshot_url,
        timeout=timeout,
        max_telemetry_age_ms=max_telemetry_age_ms,
        sessions_root=sessions_root,
        no_input=False,
        fetcher=fetcher,
    )
    if (
        wait_for_manual_loaded_scene
        and readiness.get("status") != "PASS"
        and _manual_loaded_scene_wait_applicable(readiness, recovery_summary)
    ):
        manual_wait_summary = wait_for_manual_loaded_scene_ready(
            output_dir,
            daemon_url=daemon_url,
            snapshot_url=snapshot_url,
            timeout_seconds=manual_loaded_scene_timeout_seconds,
            poll_interval=2.0,
            max_telemetry_age_ms=max_telemetry_age_ms,
            sessions_root=sessions_root,
            fetcher=fetcher,
        )
        if manual_wait_summary.get("status") == "PASS":
            readiness = _dict(manual_wait_summary.get("readiness"))
    if require_readiness_pass and readiness.get("status") != "PASS":
        summary = {
            "schema": BOT_EVAL_SUMMARY_SCHEMA,
            "status": "FAIL",
            "generatedAtUtc": utc_now(),
            "task": task,
            "mode": "live_action",
            "command": command or [],
            "outputFolder": str(output_dir),
            "runtimeControl": runtime_control_summary,
            "recovery": recovery_summary,
            "manualLoadedSceneWait": manual_wait_summary,
            "readiness": readiness,
            "loopComplete": False,
            "liveInputExecuted": False,
            "actionCommandsSent": 0,
            "warnings": readiness.get("warnings") or [],
            "errors": readiness.get("errors")
            or ([str(manual_wait_summary.get("reason"))] if manual_wait_summary else ["readiness did not PASS"]),
            "artifacts": {
                "manifest": str(output_dir / "bot_eval_manifest.json"),
                "runtimeControl": str(output_dir / "bot_runtime_control.json"),
                "recoveryAttempts": str(output_dir / "recovery_attempts.jsonl") if recovery_summary else None,
                "recoverySummary": str(output_dir / "recovery_summary.json") if recovery_summary else None,
                "latestRecoveryState": str(output_dir / "latest_recovery_state.json") if recovery_summary else None,
                "manualLoadedSceneWait": str(output_dir / "manual_loaded_scene_wait_summary.json") if manual_wait_summary else None,
                "manualLoadedSceneWaitTrace": str(output_dir / "manual_loaded_scene_wait.jsonl") if manual_wait_summary else None,
                "readiness": str(output_dir / "bot_live_readiness.json"),
                "decisions": str(output_dir / "bot_decision_trace.jsonl"),
                "actions": str(output_dir / "bot_action_trace.jsonl"),
                "observations": str(output_dir / "bot_observation_trace.jsonl"),
                "postconditions": str(output_dir / "bot_postcondition_trace.jsonl"),
                "summary": str(output_dir / "bot_eval_summary.json"),
            },
        }
        atomic_write_json(output_dir / "bot_eval_manifest.json", {"schema": BOT_EVAL_MANIFEST_SCHEMA, "task": task, "mode": "live_action", "outputFolder": str(output_dir)})
        atomic_write_json(output_dir / "bot_live_readiness.json", readiness)
        write_jsonl(output_dir / "bot_observation_trace.jsonl", [])
        write_jsonl(output_dir / "bot_decision_trace.jsonl", [])
        write_jsonl(output_dir / "bot_action_trace.jsonl", [])
        write_jsonl(output_dir / "bot_postcondition_trace.jsonl", [])
        atomic_write_json(output_dir / "bot_eval_summary.json", summary)
        return summary

    label = f"live_woodcutting_loop_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    stop_file = output_dir / "stop_recording.flag"
    recorder_stdout_path = output_dir / "record_everything_stdout.json"
    recorder_stderr_path = output_dir / "record_everything_stderr.txt"
    recorder_process: subprocess.Popen[str] | None = None
    recorder_stdout_handle = None
    recorder_stderr_handle = None
    recorder_command: list[str] | None = None
    if record_everything:
        recorder_command = build_record_everything_command(
            output_dir=output_dir,
            label=label,
            stop_file=stop_file,
            snapshot_url=snapshot_url,
        )
        recorder_stdout_handle = recorder_stdout_path.open("w", encoding="utf-8", newline="\n")
        recorder_stderr_handle = recorder_stderr_path.open("w", encoding="utf-8", newline="\n")
        popen = recorder_popen or subprocess.Popen
        recorder_process = popen(
            recorder_command,
            cwd=str(repo_root()),
            stdout=recorder_stdout_handle,
            stderr=recorder_stderr_handle,
            text=True,
        )
        # Give the recorder one polling cycle before the first action.
        time.sleep(1.0)

    executor_command = build_live_executor_command(
        duration=duration,
        max_actions=max_actions,
        daemon_url=daemon_url,
        snapshot_url=snapshot_url,
        arduino_port=arduino_port,
        debug_dir=debug_dir,
    )
    executor_stdout_path = output_dir / "execute_next_action_stdout.json"
    executor_stderr_path = output_dir / "execute_next_action_stderr.txt"
    runner = command_runner or subprocess.run
    started = time.monotonic()
    completed: subprocess.CompletedProcess[str] | None = None
    try:
        with executor_stdout_path.open("w", encoding="utf-8", newline="\n") as stdout, executor_stderr_path.open("w", encoding="utf-8", newline="\n") as stderr:
            completed = runner(
                executor_command,
                cwd=str(repo_root()),
                stdout=stdout,
                stderr=stderr,
                text=True,
                timeout=max(float(duration or 1200) + 300.0, 600.0),
            )
    finally:
        if recorder_process is not None:
            try:
                stop_file.write_text("stop\n", encoding="utf-8")
            except OSError:
                pass
            try:
                recorder_process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                recorder_process.terminate()
                try:
                    recorder_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    recorder_process.kill()
                    recorder_process.wait(timeout=5)
        for handle in (recorder_stdout_handle, recorder_stderr_handle):
            try:
                if handle is not None:
                    handle.close()
            except OSError:
                pass

    elapsed_seconds = round(time.monotonic() - started, 3)
    executor_payload = _parse_json_file(executor_stdout_path)
    atomic_write_json(output_dir / "execute_next_action_payload.json", executor_payload)
    observations, decisions, candidates, actions, postconditions = _trace_records_from_executor_payload(executor_payload)
    write_jsonl(output_dir / "bot_observation_trace.jsonl", observations)
    write_jsonl(output_dir / "bot_decision_trace.jsonl", decisions)
    write_jsonl(output_dir / "bot_candidate_trace.jsonl", candidates)
    write_jsonl(output_dir / "bot_action_trace.jsonl", actions)
    write_jsonl(output_dir / "bot_postcondition_trace.jsonl", postconditions)

    linked_recording = _recording_from_recorder_stdout(recorder_stdout_path, label) if record_everything else None
    if linked_recording is not None:
        (output_dir / "linked_recording_folder.txt").write_text(str(linked_recording) + "\n", encoding="utf-8")
    analysis_result: dict[str, Any] | None = None
    if analyze_after and linked_recording is not None:
        analysis_result = _analyze_recording(linked_recording, output_dir)

    loop_summary = _dict(executor_payload.get("loopSummary"))
    executed_count = int(executor_payload.get("executedActionCount") or 0)
    lifecycle_cycles = int(loop_summary.get("lifecycleCyclesCompleted") or 0)
    post_service_logs = int(loop_summary.get("postServiceLogsCollected") or 0)
    loop_complete = bool(lifecycle_cycles >= 1 and post_service_logs >= 1)
    executor_status = _status(executor_payload.get("status"), default="FAIL") if executor_payload else "FAIL"
    executor_blocker = _executor_blocker_from_payload(executor_payload)
    return_code = completed.returncode if completed is not None else 1
    status = "PASS" if loop_complete and executor_status in {"PASS", "WARN"} and return_code == 0 else executor_status
    if executor_blocker and not loop_complete:
        status = "FAIL"
    if return_code != 0 and not loop_complete:
        status = "FAIL"
    manifest = {
        "schema": BOT_EVAL_MANIFEST_SCHEMA,
        "generatedAtUtc": utc_now(),
        "task": task,
        "mode": "live_action",
        "outputFolder": str(output_dir),
        "durationSeconds": duration,
        "maxActions": max_actions,
        "recordEverythingRequested": bool(record_everything),
        "analyzeAfterRequested": bool(analyze_after),
        "liveInputExecuted": executed_count > 0,
        "runtimeControl": runtime_control_summary,
        "recovery": recovery_summary,
        "manualLoadedSceneWait": manual_wait_summary,
        "readiness": readiness,
        "recorderCommand": recorder_command,
        "executorCommand": executor_command,
    }
    summary = {
        "schema": BOT_EVAL_SUMMARY_SCHEMA,
        "status": status,
        "generatedAtUtc": utc_now(),
        "task": task,
        "mode": "live_action",
        "command": command or [],
        "outputFolder": str(output_dir),
        "durationSeconds": elapsed_seconds,
        "runtimeControl": runtime_control_summary,
        "recovery": recovery_summary,
        "manualLoadedSceneWait": manual_wait_summary,
        "readiness": readiness,
        "executorReturnCode": return_code,
        "executorStatus": executor_status,
        "executorReason": executor_payload.get("reason"),
        "executorBlocker": executor_blocker,
        "loopComplete": loop_complete,
        "decisionCount": len(decisions),
        "candidateCount": len(candidates),
        "actionCount": len(actions),
        "postconditionCount": len(postconditions),
        "actionCommandsSent": executed_count,
        "liveInputExecuted": executed_count > 0,
        "recordEverythingStarted": bool(record_everything),
        "linkedRecordingFolder": str(linked_recording) if linked_recording is not None else None,
        "analysis": analysis_result,
        "phaseResults": {
            "lifecycleCyclesCompleted": lifecycle_cycles,
            "postServiceLogsCollected": post_service_logs,
            "inventoryFullEvents": loop_summary.get("inventoryFullEvents"),
            "serviceRoutesCompleted": loop_summary.get("serviceRoutesCompleted"),
            "depositSuccesses": loop_summary.get("depositSuccesses"),
            "returnRoutesCompleted": loop_summary.get("returnRoutesCompleted"),
            "resourceReacquisitions": loop_summary.get("resourceReacquisitions"),
        },
        "loopSummary": loop_summary,
        "artifacts": {
            "manifest": str(output_dir / "bot_eval_manifest.json"),
            "runtimeControl": str(output_dir / "bot_runtime_control.json"),
            "recoveryAttempts": str(output_dir / "recovery_attempts.jsonl") if recovery_summary else None,
            "recoverySummary": str(output_dir / "recovery_summary.json") if recovery_summary else None,
            "latestRecoveryState": str(output_dir / "latest_recovery_state.json") if recovery_summary else None,
            "manualLoadedSceneWait": str(output_dir / "manual_loaded_scene_wait_summary.json") if manual_wait_summary else None,
            "manualLoadedSceneWaitTrace": str(output_dir / "manual_loaded_scene_wait.jsonl") if manual_wait_summary else None,
            "readiness": str(output_dir / "bot_live_readiness.json"),
            "decisions": str(output_dir / "bot_decision_trace.jsonl"),
            "candidates": str(output_dir / "bot_candidate_trace.jsonl"),
            "actions": str(output_dir / "bot_action_trace.jsonl"),
            "observations": str(output_dir / "bot_observation_trace.jsonl"),
            "postconditions": str(output_dir / "bot_postcondition_trace.jsonl"),
            "summary": str(output_dir / "bot_eval_summary.json"),
            "executorPayload": str(output_dir / "execute_next_action_payload.json"),
            "executorStdout": str(executor_stdout_path),
            "executorStderr": str(executor_stderr_path),
            "recordEverythingStdout": str(recorder_stdout_path) if record_everything else None,
            "recordEverythingStderr": str(recorder_stderr_path) if record_everything else None,
        },
        "warnings": list(
            dict.fromkeys(
                _list(executor_payload.get("warnings"))
                + _list(_dict(executor_blocker).get("warnings"))
                + ([] if linked_recording else ["linked recording folder was not resolved"])
            )
        ),
        "errors": []
        if status in {"PASS", "WARN"}
        else [
            item
            for item in (
                f"executor_return_code={return_code}",
                f"executor_status={executor_status}",
                f"executor_blocker={_dict(executor_blocker).get('blocker')}" if executor_blocker else None,
            )
            if item
        ],
    }
    atomic_write_json(output_dir / "bot_eval_manifest.json", manifest)
    atomic_write_json(output_dir / "bot_live_readiness.json", readiness)
    atomic_write_json(output_dir / "bot_eval_summary.json", summary)
    return summary


def run_evaluation(
    *,
    task: str = "woodcutting_loop",
    recording: str | Path | None = None,
    output_root: str | Path | None = None,
    max_actions: int | None = None,
    duration: int | None = None,
    record_everything: bool = False,
    analyze_after: bool = False,
    command: list[str] | None = None,
) -> dict[str, Any]:
    if task != "woodcutting_loop":
        raise ValueError("only task=woodcutting_loop is supported by this bounded evaluator")
    recording_path = resolve_recording(recording)
    artifacts = load_artifacts(recording_path)
    phases = build_phase_records(artifacts)
    if max_actions is not None:
        phases = phases[: max(0, max_actions)]
    started = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(output_root).expanduser() if output_root else repo_root() / "bot_runs"
    output_dir = root / f"{started}_{task}_eval"
    output_dir.mkdir(parents=True, exist_ok=True)
    script = task_script_api.woodcut_bank_template()
    observations: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    postconditions: list[dict[str, Any]] = []
    for index, phase in enumerate(phases):
        observation, decision, action, postcondition = evaluate_phase(phase, artifacts, script, index)
        observations.append(observation)
        decisions.append(decision)
        actions.append(action)
        postconditions.append(postcondition)
    manifest = {
        "schema": BOT_EVAL_MANIFEST_SCHEMA,
        "generatedAtUtc": utc_now(),
        "task": task,
        "mode": "replay",
        "recordingFolder": str(recording_path),
        "outputFolder": str(output_dir),
        "durationSeconds": duration,
        "maxActions": max_actions,
        "recordEverythingRequested": bool(record_everything),
        "analyzeAfterRequested": bool(analyze_after),
        "liveInputExecuted": False,
        "script": {"name": script.get("name"), "policy": script.get("policy")},
    }
    summary = summarize(
        recording_path,
        output_dir,
        observations,
        decisions,
        actions,
        postconditions,
        command=command or [],
    )
    atomic_write_json(output_dir / "bot_eval_manifest.json", manifest)
    write_jsonl(output_dir / "bot_observation_trace.jsonl", observations)
    write_jsonl(output_dir / "bot_decision_trace.jsonl", decisions)
    write_jsonl(output_dir / "bot_action_trace.jsonl", actions)
    write_jsonl(output_dir / "bot_postcondition_trace.jsonl", postconditions)
    atomic_write_json(output_dir / "bot_eval_summary.json", summary)
    (output_dir / "linked_recording_folder.txt").write_text(str(recording_path) + "\n", encoding="utf-8")
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay/evaluate bot task decisions against Record Everything lifecycle artifacts.")
    parser.add_argument("--task", default="woodcutting_loop", choices=["woodcutting_loop"])
    parser.add_argument("--recording", help="Recording folder to replay. Defaults to the known full-loop fixture if available.")
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--max-actions", type=int)
    parser.add_argument("--preflight", action="store_true", help="Check live-run wiring without running the bot or sending input.")
    parser.add_argument("--check-input-geometry", action="store_true", help="Check and self-heal RuneLite input geometry without sending gameplay input.")
    parser.add_argument("--live", action="store_true", help="Use live context. Real actions require --live --execute-actions; --live by itself fails closed.")
    parser.add_argument("--live-smoke", action="store_true", help="Run bounded live readiness smoke; no input is sent.")
    parser.add_argument("--execute-actions", action="store_true", help="Run real live actions through execute_next_action.py. Requires --live and readiness PASS.")
    parser.add_argument("--auto-recover-loaded-scene", action="store_true", help="Run existing loaded-scene recovery before live readiness/action execution.")
    parser.add_argument("--liveness-max-total-seconds", type=float, default=DEFAULT_LIVENESS_RECOVERY_SECONDS, help="Maximum seconds for --auto-recover-loaded-scene.")
    parser.add_argument("--liveness-max-attempts-per-state", type=int, default=DEFAULT_LIVENESS_ATTEMPTS_PER_STATE, help="Maximum attempts per known liveness state during recovery.")
    parser.add_argument("--allow-jagex-launcher-automation", action="store_true", help="Allow launcher automation during recovery; credentials are still never typed.")
    parser.add_argument("--wait-for-manual-loaded-scene", action="store_true", help="Poll for a real loaded scene after recovery fails due to login/disconnected state; does not dry-run or send input.")
    parser.add_argument("--manual-loaded-scene-timeout-seconds", type=float, default=600.0, help="Maximum seconds to wait for manually restored loaded-scene proof.")
    parser.add_argument("--no-input", action="store_true", default=False, help="Explicitly keep live evaluation no-input.")
    parser.add_argument("--dry-run-actions", action="store_true", default=False, help="Explicitly run live evaluation without executing actions.")
    parser.add_argument("--stop-on-warning", action="store_true", help="Stop live smoke after the first WARN or FAIL readiness sample.")
    parser.add_argument("--require-readiness-pass", action="store_true", help="Return FAIL unless readiness status is PASS.")
    parser.add_argument("--daemon-url", default=DEFAULT_DAEMON_URL)
    parser.add_argument("--snapshot-url", default=DEFAULT_SNAPSHOT_URL)
    parser.add_argument("--readiness-timeout", type=float, default=DEFAULT_READINESS_TIMEOUT_SECONDS)
    parser.add_argument("--max-telemetry-age-ms", type=int, default=DEFAULT_MAX_TELEMETRY_AGE_MS)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--arduino-port", default=DEFAULT_ARDUINO_PORT)
    parser.add_argument("--record-everything", action="store_true", help="Accepted for CLI compatibility; replay mode does not start a recorder.")
    parser.add_argument("--analyze-after", action="store_true", help="Accepted for CLI compatibility; replay mode consumes existing analyzer artifacts.")
    parser.add_argument("--out-dir", default=str(repo_root() / "bot_runs"))
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def build_live_mode_guard_summary(
    *,
    task: str,
    output_root: str | Path | None,
    command: list[str],
    reason: str,
    errors: list[str],
) -> dict[str, Any]:
    started = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(output_root).expanduser() if output_root else repo_root() / "bot_runs"
    output_dir = root / f"{started}_{task}_{reason}"
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema": BOT_EVAL_SUMMARY_SCHEMA,
        "status": "FAIL",
        "generatedAtUtc": utc_now(),
        "task": task,
        "mode": reason,
        "command": command,
        "outputFolder": str(output_dir),
        "liveInputExecuted": False,
        "actionCommandsSent": 0,
        "dryRunActions": False,
        "noInput": True,
        "warnings": [LIVE_NOT_REAL_ACTION_WARNING],
        "errors": errors,
        "hint": "Real action execution requires exactly --live --execute-actions without --dry-run-actions or --no-input.",
    }
    atomic_write_json(output_dir / "bot_eval_summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = [sys.executable, str(Path(__file__).resolve())] + (argv if argv is not None else sys.argv[1:])
    try:
        if args.check_input_geometry:
            summary = run_input_geometry_check(
                daemon_url=args.daemon_url,
                snapshot_url=args.snapshot_url,
                timeout=args.readiness_timeout,
                max_telemetry_age_ms=args.max_telemetry_age_ms,
            )
        elif args.preflight:
            summary = run_preflight(
                task=args.task,
                output_root=args.out_dir,
                arduino_port=args.arduino_port,
            )
        elif args.live and args.execute_actions and (args.dry_run_actions or args.no_input or args.live_smoke):
            summary = build_live_mode_guard_summary(
                task=args.task,
                output_root=args.out_dir,
                command=command,
                reason="live_action_conflicting_dry_run_flags",
                errors=["live_action_conflicting_dry_run_flags"],
            )
        elif args.live and args.execute_actions:
            summary = run_live_action(
                task=args.task,
                output_root=args.out_dir,
                duration=args.duration,
                max_actions=args.max_actions,
                daemon_url=args.daemon_url,
                snapshot_url=args.snapshot_url,
                timeout=args.readiness_timeout,
                max_telemetry_age_ms=args.max_telemetry_age_ms,
                arduino_port=args.arduino_port,
                record_everything=args.record_everything,
                analyze_after=args.analyze_after,
                require_readiness_pass=args.require_readiness_pass,
                auto_recover_loaded_scene=args.auto_recover_loaded_scene,
                liveness_max_total_seconds=args.liveness_max_total_seconds,
                liveness_max_attempts_per_state=args.liveness_max_attempts_per_state,
                allow_jagex_launcher_automation=args.allow_jagex_launcher_automation,
                wait_for_manual_loaded_scene=args.wait_for_manual_loaded_scene,
                manual_loaded_scene_timeout_seconds=args.manual_loaded_scene_timeout_seconds,
                command=command,
            )
        elif args.live and not args.live_smoke and not args.dry_run_actions and not args.no_input:
            summary = build_live_mode_guard_summary(
                task=args.task,
                output_root=args.out_dir,
                command=command,
                reason="live_requires_execute_actions",
                errors=["live_requires_execute_actions"],
            )
        elif args.live_smoke or args.live:
            summary = run_live_smoke(
                task=args.task,
                output_root=args.out_dir,
                mode="live_dry_run" if args.live and not args.live_smoke else "live_smoke",
                duration=args.duration,
                daemon_url=args.daemon_url,
                snapshot_url=args.snapshot_url,
                timeout=args.readiness_timeout,
                max_telemetry_age_ms=args.max_telemetry_age_ms,
                poll_interval=args.poll_interval,
                no_input=True,
                dry_run_actions=True,
                require_readiness_pass=args.require_readiness_pass,
                stop_on_warning=args.stop_on_warning,
                command=command,
            )
        else:
            summary = run_evaluation(
                task=args.task,
                recording=args.recording,
                output_root=args.out_dir,
                max_actions=args.max_actions,
                duration=args.duration,
                record_everything=args.record_everything,
                analyze_after=args.analyze_after,
                command=command,
            )
    except Exception as exc:
        payload = {"schema": BOT_EVAL_SUMMARY_SCHEMA, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(payload, indent=2 if args.json else None), file=sys.stdout)
        return 1
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(f"{summary['status']} bot eval {summary.get('mode')}: {summary['outputFolder']}")
        if summary.get("mode") in {"live_smoke", "live_dry_run"}:
            print(LIVE_NOT_REAL_ACTION_WARNING)
            readiness = _dict(summary.get("readiness"))
            print(
                "readiness="
                f"{readiness.get('status')} "
                f"context={readiness.get('contextServiceReachable')} "
                f"fresh={readiness.get('telemetryFresh')} "
                f"canStart={readiness.get('liveEvalCanStart')}"
            )
        elif summary.get("mode") == "live_action":
            print(
                f"actions={summary.get('actionCommandsSent', 0)} "
                f"loopComplete={summary.get('loopComplete')} "
                f"recording={summary.get('linkedRecordingFolder') or 'none'}"
            )
        elif summary.get("mode") == "preflight":
            print(f"failures={len(summary.get('mandatoryFailures') or [])}")
        else:
            print(f"decisions={summary['decisionCount']} mismatches={summary['decisionMismatchCount']}")
    return 0 if summary.get("status") in {"PASS", "WARN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

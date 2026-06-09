from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import external_knowledge
import human_click_profile
import task_script_api
import target_view_core
import world_model_client
import world_model_core


STATUS_SCHEMA = "knowledge_fabric_status.v1"
LIVE_WORLD_INDEX_SCHEMA = "live_world_index.v1"
SESSION_MEMORY_SCHEMA = "session_memory.v1"
STATIC_LIBRARY_SCHEMA = "static_knowledge_library.v1"
DEBUG_EVIDENCE_SCHEMA = "debug_evidence_index.v1"
QUERY_SCHEMA = "knowledge_fabric_query_response.v1"
SCRIPT_AUTHORING_CONTEXT_SCHEMA = "script_authoring_context.v1"
SCRIPT_AUTHORING_CAPTURE_SCHEMA = "script_authoring_context_capture.v1"
REPLAY_SCENARIO_SCHEMA = "replay_scenario.v1"
REPLAY_RESULT_SCHEMA = "replay_scenario_result.v1"
DATA_QUALITY_SCHEMA = "data_quality_report.v1"
DEBUG_CONTEXT_DIFF_SCHEMA = "debug_context_diff.v1"
HANDOFF_SUMMARY_SCHEMA = "knowledge_fabric_handoff_summary.v1"
DATA_SOURCE_INVENTORY_SCHEMA = "data_source_inventory.v1"
QUERY_COVERAGE_SCHEMA = "query_coverage_matrix.v1"
COVERAGE_REPORT_SCHEMA = "coverage_report.v1"
TASK_PROBE_SCHEMA = "task_probe_report.v1"
ACTION_INPUT_VISIBILITY_SCHEMA = "action_input_visibility_context.v1"
NAVIGATION_DECISION_TRACE_SUMMARY_SCHEMA = "navigation_decision_trace_summary.v1"

VIEWER_DIR = Path(__file__).resolve().parent
TARGET_PROFILES_PATH = VIEWER_DIR / "target_profiles.json"
TARGET_LIBRARY_PATH = VIEWER_DIR / "target_library.json"
SERVICE_ROUTES_PATH = VIEWER_DIR / "profiles" / "service_routes.json"
LIVE_DIR = Path("interaction_geometry") / "live"
SESSION_MEMORY_FILE = "session_memory.json"
DEFAULT_QUERY_LIMIT = 25
HARD_QUERY_LIMIT = 250
SERVICE_MIN_VISIBLE_AREA_PX = 96.0
SERVICE_MIN_VISIBLE_AREA_RATIO = 0.35
SERVICE_MIN_EDGE_DISTANCE_PX = 32.0
SERVICE_COMFORTABLE_REGION_FRACTION = 0.78


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str:
    return "" if value is None else str(value)


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _norm(value: Any) -> str:
    return _str(value).strip().lower()


def _safe_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_QUERY_LIMIT
    try:
        return max(0, min(HARD_QUERY_LIMIT, int(limit)))
    except (TypeError, ValueError):
        return DEFAULT_QUERY_LIMIT


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}
    return value if isinstance(value, dict) else {}


def _trace_payload(value: dict[str, Any]) -> dict[str, Any]:
    if isinstance(value.get("actionTrace"), dict):
        return _dict(value.get("actionTrace"))
    results = _list(value.get("actionResults"))
    for item in reversed(results):
        if isinstance(item, dict) and isinstance(item.get("actionTrace"), dict):
            return dict(item["actionTrace"])
    return value if isinstance(value, dict) else {}


def _input_count_summary(status: dict[str, Any] | None) -> dict[str, int]:
    status = status if isinstance(status, dict) else {}
    flags = _dict(status.get("injectionFlags"))
    mouse_injected = _int(flags.get("mouseInjectedCount")) or 0
    keyboard_injected = _int(flags.get("keyboardInjectedCount")) or 0
    mouse_lower = _int(flags.get("mouseLowerIlInjectedCount")) or 0
    keyboard_lower = _int(flags.get("keyboardLowerIlInjectedCount")) or 0
    return {
        "mouseInjectedCount": mouse_injected,
        "keyboardInjectedCount": keyboard_injected,
        "mouseLowerIlInjectedCount": mouse_lower,
        "keyboardLowerIlInjectedCount": keyboard_lower,
        "injectedEvents": _int(status.get("injectedEvents")) or mouse_injected + keyboard_injected,
        "lowerIlInjectedEvents": _int(status.get("lowerIlInjectedEvents")) or mouse_lower + keyboard_lower,
    }


def _trace_input_phase_summary(trace: dict[str, Any]) -> dict[str, Any]:
    report = _dict(trace.get("inputIntegrityPhaseReport"))
    if report:
        return report
    before = _dict(trace.get("inputIntegrityStatusBefore"))
    after = _dict(trace.get("inputIntegrityStatusAfter"))
    before_counts = _input_count_summary(before)
    after_counts = _input_count_summary(after)
    injected_delta = (_int(trace.get("mouseInjectedCountDelta")) or 0) + (_int(trace.get("keyboardInjectedCountDelta")) or 0)
    lower_delta = _int(trace.get("lowerIlInjectedCountDelta")) or 0
    direct_delta = _int(trace.get("directBackendBypassCountDelta")) or 0
    return {
        "schema": "input_integrity_phase_report.v1",
        "policy": "phase_aware_live_window_only",
        "operator_phase": {
            "operatorInjectedEvents": before_counts["injectedEvents"],
            "operatorLowerIlInjectedEvents": before_counts["lowerIlInjectedEvents"],
            "blocking": False,
            "classification": "operatorInjectedEvents" if before_counts["injectedEvents"] or before_counts["lowerIlInjectedEvents"] else "none",
        },
        "pre_live_phase": {
            "baselineEstablished": bool(before),
            "monitorPassAtBaseline": before.get("monitorPass"),
            "injectedEventsAtBaseline": before_counts["injectedEvents"],
            "lowerIlInjectedEventsAtBaseline": before_counts["lowerIlInjectedEvents"],
            "blocking": False,
        },
        "live_action_phase": {
            "injectedEventsDelta": injected_delta,
            "lowerIlInjectedEventsDelta": lower_delta,
            "directBackendBypassCountDelta": direct_delta,
            "hardBlocker": bool(injected_delta > 0 or lower_delta > 0 or direct_delta > 0),
        },
        "post_live_phase": {
            "monitorPassAfter": after.get("monitorPass"),
            "injectedEventsAfter": after_counts["injectedEvents"],
            "lowerIlInjectedEventsAfter": after_counts["lowerIlInjectedEvents"],
        },
    }


def _navigation_trace_records_from_trace(trace: dict[str, Any]) -> list[dict[str, Any]]:
    trace = _trace_payload(trace)
    records = []
    for item in _list(trace.get("navigationDecisionTrace")):
        if isinstance(item, dict) and item.get("schema") == "navigation_decision_trace.v1":
            records.append(dict(item))
    return records


def _navigation_trace_records_from_debug(debug_evidence: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for trace in _list(debug_evidence.get("latestActionTraces")):
        if not isinstance(trace, dict):
            continue
        for item in _list(trace.get("navigationDecisionTrace")):
            if isinstance(item, dict):
                copied = dict(item)
                copied.setdefault("_sourcePath", trace.get("path"))
                records.append(copied)
    return records


def _navigation_trace_records_from_session_files(session_path: Path | None, *, limit: int = 50) -> list[dict[str, Any]]:
    if session_path is None:
        return []
    live_dir = session_path / LIVE_DIR
    candidates = [
        live_dir / "navigation_decision_trace.jsonl",
        live_dir / "navigation_decisions.jsonl",
    ]
    records: list[dict[str, Any]] = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        start_line = max(0, len(lines) - max(1, int(limit or 50)))
        for offset, line in enumerate(lines[start_line:], start=start_line + 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("schema") == "navigation_decision_trace.v1":
                copied = dict(item)
                copied.setdefault("_sourcePath", str(path))
                copied.setdefault("_line", offset)
                records.append(copied)
    return records[-max(1, int(limit or 50)) :]


def _navigation_trace_compact_row(record: dict[str, Any]) -> dict[str, Any]:
    observed = _dict(record.get("observed"))
    pending = _dict(record.get("pending"))
    distances = _dict(record.get("distances"))
    subgoal = _dict(record.get("chosenSubgoal"))
    route = _dict(record.get("routeStep"))
    return {
        "line": record.get("_line"),
        "sourcePath": record.get("_sourcePath"),
        "tick": record.get("tick"),
        "decision": record.get("decision"),
        "reason": record.get("reason"),
        "playerWorldPosition": record.get("playerWorldPosition"),
        "destinationWorldPosition": record.get("destinationWorldPosition"),
        "targetTile": subgoal.get("targetTile"),
        "targetName": subgoal.get("targetName"),
        "proposedAction": subgoal.get("proposedAction"),
        "actionTargetSource": subgoal.get("actionTargetSource"),
        "actionability": subgoal.get("actionability"),
        "executable": subgoal.get("executable"),
        "routeId": route.get("routeId"),
        "routeNode": route.get("currentNodeId"),
        "routeStepIndex": route.get("currentStepIndex"),
        "routeStepStatus": route.get("routeStepStatus"),
        "distanceToGoal": distances.get("currentDistanceToGoal") or distances.get("distanceToGoal"),
        "distanceDelta": distances.get("distanceDelta"),
        "distanceImproving": distances.get("distanceImproving"),
        "movementState": pending.get("movementState"),
        "observedResult": observed.get("observedResult") or pending.get("observedResult"),
        "nextActionAllowed": observed.get("nextActionAllowed") if observed.get("nextActionAllowed") is not None else pending.get("nextActionAllowed"),
        "recoveryMode": record.get("recoveryMode"),
    }


def _navigation_trace_block_evidence_present(record: dict[str, Any]) -> bool:
    text = json.dumps(record, sort_keys=True, default=str).lower()
    return any(token in text for token in ("blockevidence", "barrierdetected", "localreachability=blocked", "wallhuggingdetected", "wallhuggingdetected"))


def _navigation_trace_repeated_short_click(record: dict[str, Any], previous: dict[str, Any]) -> bool:
    if str(record.get("decision") or "") != "click" or str(previous.get("decision") or "") != "click":
        return False
    current_goal = _dict(record.get("chosenSubgoal"))
    previous_goal = _dict(previous.get("chosenSubgoal"))
    if current_goal.get("targetTile") != previous_goal.get("targetTile"):
        return False
    delta = _dict(record.get("distances")).get("distanceDelta")
    try:
        return abs(float(delta)) < 1.0
    except (TypeError, ValueError):
        return True


def _navigation_trace_suspicious_summary(record: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any] | None:
    reason = str(record.get("reason") or "").strip()
    decision = str(record.get("decision") or "").strip()
    observed = _dict(record.get("observed"))
    pending = _dict(record.get("pending"))
    distances = _dict(record.get("distances"))
    subgoal = _dict(record.get("chosenSubgoal"))
    issue: str | None = None
    if not reason:
        issue = "missing_reason_string"
    elif decision in {"click", "recover"} and subgoal.get("executable") is False:
        issue = "click_or_recover_for_non_executable_subgoal"
    elif decision in {"click", "recover"} and (observed.get("nextActionAllowed") is False or pending.get("nextActionAllowed") is False):
        issue = "click_or_recover_while_previous_result_pending"
    elif decision in {"click", "recover"} and distances.get("distanceImproving") is True:
        issue = "click_or_recover_while_distance_improving"
    elif decision in {"click", "recover"} and any(token in reason.lower() for token in ("stale", "daemon_latest_tick_missing", "input_geometry_unavailable")):
        issue = "stale_state_allowed_click"
    elif decision in {"click", "recover"} and "wall" in reason.lower() and not _navigation_trace_block_evidence_present(record):
        issue = "blocked_recovery_without_block_evidence"
    elif previous and _navigation_trace_repeated_short_click(record, previous):
        issue = "repeated_short_click"
    if issue is None:
        return None
    return {
        "issue": issue,
        "line": record.get("_line"),
        "sourcePath": record.get("_sourcePath"),
        "decision": decision or None,
        "reason": reason or None,
        "observedResult": observed.get("observedResult") or pending.get("observedResult"),
        "targetTile": subgoal.get("targetTile"),
        "routeStep": _dict(record.get("routeStep")),
    }


def _navigation_trace_first_suspicious_index(records: list[dict[str, Any]]) -> int | None:
    for index, record in enumerate(records):
        previous = records[index - 1] if index > 0 else None
        if _navigation_trace_suspicious_summary(record, previous):
            return index
    return None


def _navigation_trace_summary(records: list[dict[str, Any]], *, limit: int = DEFAULT_QUERY_LIMIT) -> dict[str, Any]:
    normalized = [dict(record) for record in records if isinstance(record, dict)]
    for index, record in enumerate(normalized):
        record.setdefault("_line", index + 1)
    decisions = Counter(str(record.get("decision") or "missing") for record in normalized)
    reasons = Counter(str(record.get("reason") or "missing") for record in normalized)
    suspicious_index = _navigation_trace_first_suspicious_index(normalized)
    suspicious = None
    context_rows: list[dict[str, Any]] = []
    if suspicious_index is not None:
        suspicious = _navigation_trace_suspicious_summary(normalized[suspicious_index], normalized[suspicious_index - 1] if suspicious_index > 0 else None)
        half_window = max(1, min(5, int(limit // 2) if limit else 5))
        start = max(0, suspicious_index - half_window)
        end = min(len(normalized), suspicious_index + half_window + 1)
        context_rows = [_navigation_trace_compact_row(record) for record in normalized[start:end]]
    else:
        context_rows = [_navigation_trace_compact_row(record) for record in normalized[-limit:]] if limit else []
    latest = normalized[-1] if normalized else {}
    return {
        "decisionCount": len(normalized),
        "decisionCounts": dict(sorted(decisions.items())),
        "reasonCounts": dict(sorted(reasons.items())),
        "firstSuspiciousDecision": suspicious,
        "contextRows": context_rows,
        "latestDecision": _navigation_trace_compact_row(latest) if latest else None,
        "allDecisionsHaveReasons": all(bool(str(record.get("reason") or "").strip()) for record in normalized) if normalized else False,
    }


def _action_trace_visibility(trace: dict[str, Any], *, path: str | None = None) -> dict[str, Any]:
    trace = _trace_payload(trace)
    selected = _dict(trace.get("selectedTarget"))
    intended = _dict(trace.get("intendedPoint"))
    client_tick = _dict(trace.get("clientTick"))
    live_input = _dict(trace.get("liveInput"))
    human_input = _dict(trace.get("humanInput"))
    mouse_move = _dict(trace.get("mouseMove"))
    readiness = _dict(trace.get("actionReadiness"))
    point_resolution = _dict(intended.get("clickPointResolution") or trace.get("clickPointResolution"))
    movement_safety = _dict(trace.get("movementSafetyPreflight") or point_resolution.get("movementSafetyPreflight"))
    planned_screen_point = intended.get("screen") or point_resolution.get("screenPointAfterScaling") or mouse_move.get("plannedEndScreenPoint")
    hover_sample = client_tick.get("acceptedHoverSample") or client_tick.get("latestRejectedHoverSample")
    clicked = client_tick.get("lastMenuOptionClickedAfter")
    return {
        "path": path,
        "classification": trace.get("finalClassification"),
        "plannedAction": trace.get("proposedAction"),
        "plannedTarget": {
            "name": selected.get("targetName") or selected.get("name") or trace.get("targetName"),
            "classId": selected.get("classId") or trace.get("classId"),
            "type": selected.get("targetType") or selected.get("targetKind") or trace.get("targetKind"),
            "worldLocation": selected.get("worldLocation")
            or {
                "worldX": selected.get("worldX"),
                "worldY": selected.get("worldY"),
                "plane": selected.get("plane"),
            },
            "actionTargetSource": selected.get("actionTargetSource") or trace.get("actionTargetSource"),
            "actionability": selected.get("actionability") or trace.get("actionability"),
        },
        "plannedScreenPoint": planned_screen_point,
        "coordinateConversionTrace": intended or point_resolution,
        "displayScaleApplied": _first_present(intended.get("displayScaleApplied"), point_resolution.get("displayScaleApplied")),
        "displayScaleReason": _first_present(intended.get("displayScaleReason"), point_resolution.get("displayScaleReason")),
        "arduinoCalibrationStatus": {
            "movementSafetyStatus": movement_safety.get("status"),
            "source": movement_safety.get("source"),
            "allowedWindow": movement_safety.get("allowedWindow"),
            "blockers": _list(movement_safety.get("blockers")),
            "warnings": _list(movement_safety.get("warnings")),
        },
        "humanInputController": {
            "profile": human_input.get("profile"),
            "movementGenerator": human_input.get("movementGenerator"),
            "liveInputBackend": human_input.get("liveInputBackend") or trace.get("liveInputBackend"),
            "movementProfile": human_input.get("profile"),
            "movementMetrics": human_input,
        },
        "cursorMovementTrace": {
            "mouseMove": mouse_move,
            "movementPlan": trace.get("movementPlan"),
            "lastMovementProof": {
                "startScreenPoint": mouse_move.get("startScreenPoint"),
                "plannedEndScreenPoint": mouse_move.get("plannedEndScreenPoint"),
                "endScreenPoint": mouse_move.get("endScreenPoint") or mouse_move.get("actualEndScreenPoint"),
            },
        },
        "hoverConfirmationEvidence": {
            "acceptedHoverSample": hover_sample,
            "rejectedHoverSamples": _list(client_tick.get("rejectedHoverSamples")),
            "hoverConfirmationSamples": _list(client_tick.get("hoverConfirmationSamples")),
            "hoverConfirmedTopExpected": selected.get("hoverConfirmedTopExpected"),
        },
        "menuOptionClickedEvidence": clicked,
        "input_integrity_status": {
            "before": trace.get("inputIntegrityStatusBefore"),
            "after": trace.get("inputIntegrityStatusAfter"),
            "delta": live_input.get("inputIntegrityDelta") or {
                "mouseInjectedCountDelta": trace.get("mouseInjectedCountDelta"),
                "keyboardInjectedCountDelta": trace.get("keyboardInjectedCountDelta"),
                "lowerIlInjectedCountDelta": trace.get("lowerIlInjectedCountDelta"),
                "directBackendBypassCountDelta": trace.get("directBackendBypassCountDelta"),
            },
            "phaseCounts": _trace_input_phase_summary(trace),
        },
        "directBackendBypassCount": human_input.get("directBackendBypassCount"),
        "lastClickProof": {
            "clickTimestampWallMillis": trace.get("clickTimestampWallMillis"),
            "clickedMenuClassification": client_tick.get("clickedMenuClassification"),
            "lastMenuOptionClickedAfter": clicked,
        },
        "blockedReason": live_input.get("blockReason")
        or readiness.get("blockReason")
        or trace.get("clickFailureBucket")
        or trace.get("finalClassification"),
        "target_view_state": trace.get("targetViewState") or selected.get("targetViewState"),
        "actionReadiness": readiness,
        "liveInput": live_input,
    }


def _point_from_target(target: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    for key in ("screenAimPoint", "screenClickPoint", "screenPoint"):
        point = _dict(target.get(key))
        if point.get("x") is not None and point.get("y") is not None:
            return {"x": point.get("x"), "y": point.get("y")}, "screen"
    for key in ("canvasAimPoint", "aimPoint", "rawAimPoint"):
        point = _dict(target.get(key))
        if point.get("x") is not None and point.get("y") is not None:
            return {"x": point.get("x"), "y": point.get("y")}, "canvas"
    safe = _dict(target.get("safeAimPoint"))
    accepted = _dict(safe.get("acceptedAimpoint") or safe.get("rawAimPoint"))
    if accepted.get("x") is not None and accepted.get("y") is not None:
        return {"x": accepted.get("x"), "y": accepted.get("y")}, "canvas"
    if safe.get("canvasX") is not None and safe.get("canvasY") is not None:
        return {"x": safe.get("canvasX"), "y": safe.get("canvasY")}, "canvas"
    return None, None


def _derive_planned_point_visibility(
    target: dict[str, Any],
    *,
    readiness: dict[str, Any],
    status: dict[str, Any],
) -> dict[str, Any]:
    point, coordinate_space = _point_from_target(target)
    if point is None:
        return {
            "plannedScreenPoint": None,
            "coordinateConversionTrace": {
                "status": "WARN",
                "method": "target_point_missing",
                "source": "current_action_proposal",
                "warnings": ["planned target has no screen/canvas aimpoint"],
                "missingCapabilities": ["target.aimPoint"],
                "noLiveInput": True,
            },
            "displayScaleApplied": None,
            "displayScaleReason": "target_point_missing",
        }
    try:
        from input_control.input_geometry import resolve_screen_click_point

        input_geometry = _dict(readiness.get("inputGeometry") or status.get("inputGeometry"))
        resolution = resolve_screen_click_point(
            point,
            click_point_space=coordinate_space or "canvas",
            input_geometry=input_geometry,
            source_canvas_size=_dict(target.get("sourceCanvasSize")),
        )
    except Exception as error:  # noqa: BLE001
        return {
            "plannedScreenPoint": None,
            "coordinateConversionTrace": {
                "status": "WARN",
                "method": "coordinate_resolution_unavailable",
                "source": "current_action_proposal",
                "error": f"{type(error).__name__}: {error}",
                "noLiveInput": True,
            },
            "displayScaleApplied": None,
            "displayScaleReason": "coordinate_resolution_unavailable",
        }
    trace = dict(resolution)
    trace["source"] = "current_action_proposal"
    trace["targetName"] = target.get("name")
    trace["targetKey"] = target.get("targetKey")
    trace["targetSource"] = target.get("actionTargetSource") or target.get("targetSource")
    trace["inputPoint"] = point
    trace["inputPointSpace"] = coordinate_space
    trace["noLiveInput"] = True
    return {
        "plannedScreenPoint": resolution.get("screenClickPoint"),
        "coordinateConversionTrace": trace,
        "displayScaleApplied": resolution.get("displayScaleApplied"),
        "displayScaleReason": resolution.get("displayScaleReason"),
    }


def _readiness_block_reason(readiness: dict[str, Any]) -> str | None:
    action_readiness = _dict(readiness.get("actionReadiness"))
    action_execution = _dict(readiness.get("actionExecution"))
    for value in (
        action_readiness.get("blockReason"),
        action_execution.get("refusalReason"),
        readiness.get("blockReason"),
        readiness.get("refusalReason"),
    ):
        if value:
            return str(value)
    blockers = _list(action_readiness.get("blockers")) or _list(readiness.get("blockers"))
    for blocker in blockers:
        if isinstance(blocker, dict):
            value = blocker.get("code") or blocker.get("reason") or blocker.get("message")
            if value:
                return str(value)
        elif blocker:
            return str(blocker)
    return None


def _readiness_block_evidence(
    readiness: dict[str, Any],
    current_input_integrity: dict[str, Any],
) -> dict[str, Any]:
    action_readiness = _dict(readiness.get("actionReadiness"))
    phase_counts = _trace_input_phase_summary({"inputIntegrityStatusBefore": current_input_integrity})
    live_phase = _dict(phase_counts.get("live_action_phase"))
    operator_phase = _dict(phase_counts.get("operator_phase"))
    reason = _readiness_block_reason(readiness)
    execution_allowed = action_readiness.get("executionAllowed")
    readiness_blockers = _list(action_readiness.get("blockers")) or _list(readiness.get("blockers"))
    raw_monitor_blockers = _list(current_input_integrity.get("blockers"))
    return {
        "schema": "action_input_block_evidence.v1",
        "source": "readiness+phase_aware_input_integrity",
        "blocked": bool(execution_allowed is False or reason or live_phase.get("hardBlocker")),
        "blockedReason": reason,
        "executionAllowed": execution_allowed,
        "readinessStatus": action_readiness.get("status") or readiness.get("status"),
        "readinessBlockers": readiness_blockers,
        "phaseAwareLiveInputHardBlocker": bool(live_phase.get("hardBlocker")),
        "liveActionPhase": live_phase,
        "operatorInjectedEvents": operator_phase.get("operatorInjectedEvents"),
        "operatorInjectedEventsBlocking": False,
        "rawMonitorStatus": current_input_integrity.get("status"),
        "rawMonitorBlockers": raw_monitor_blockers,
        "rawMonitorBlockersArePhaseQualified": True,
        "whyInputWasBlocked": reason
        or ("live_action_input_integrity_hard_blocker" if live_phase.get("hardBlocker") else None),
        "noLiveInput": True,
    }


def _phase_aware_input_integrity_status(current_input_integrity: dict[str, Any]) -> dict[str, Any]:
    phase_counts = _trace_input_phase_summary({"inputIntegrityStatusBefore": current_input_integrity})
    live_phase = _dict(phase_counts.get("live_action_phase"))
    operator_phase = _dict(phase_counts.get("operator_phase"))
    direct_bypass_count = _dict(current_input_integrity).get("directBackendBypassCount")
    return {
        "current": current_input_integrity,
        "phaseCounts": phase_counts,
        "phaseAwareAssessment": {
            "operatorInjectedEventsAreBlocking": False,
            "operatorInjectedEvents": operator_phase.get("operatorInjectedEvents"),
            "operatorLowerIlInjectedEvents": operator_phase.get("operatorLowerIlInjectedEvents"),
            "liveActionHardBlocker": bool(live_phase.get("hardBlocker")),
            "liveActionInjectedEventsDelta": live_phase.get("injectedEventsDelta"),
            "liveActionLowerIlInjectedEventsDelta": live_phase.get("lowerIlInjectedEventsDelta"),
            "liveActionDirectBackendBypassCountDelta": live_phase.get("directBackendBypassCountDelta"),
            "directBackendBypassCount": direct_bypass_count,
            "policy": "phase_aware_live_window_only",
            "noLiveInput": True,
        },
        "directBackendBypassCount": direct_bypass_count,
        "rawMonitorStatus": _dict(current_input_integrity).get("status"),
        "rawMonitorBlockers": _list(_dict(current_input_integrity).get("blockers")),
        "rawMonitorBlockersArePhaseQualified": True,
        "noLiveInput": True,
    }


def _derive_arduino_calibration_status(
    readiness: dict[str, Any],
    current_input_integrity: dict[str, Any],
    *,
    planned_screen_point: dict[str, Any] | None,
    coordinate_trace: dict[str, Any] | None,
) -> dict[str, Any]:
    input_geometry = _dict(readiness.get("inputGeometry"))
    phase_counts = _trace_input_phase_summary({"inputIntegrityStatusBefore": current_input_integrity})
    live_phase = _dict(phase_counts.get("live_action_phase"))
    warnings = list(dict.fromkeys(
        _list(current_input_integrity.get("warnings"))
        + _list(input_geometry.get("warnings"))
        + ["movement_safety_not_evaluated_without_live_action_trace"]
    ))
    blockers = []
    if input_geometry.get("inputGeometryAvailable") is False:
        blockers.append("input_geometry_unavailable")
    if current_input_integrity.get("expectedVidPidMatched") is False:
        blockers.append("arduino_vid_pid_mismatch")
    if live_phase.get("hardBlocker"):
        blockers.append("live_action_input_integrity_hard_blocker")
    return {
        "schema": "arduino_calibration_visibility.v1",
        "status": "WARN" if warnings or blockers else "UNKNOWN",
        "source": "readiness_input_geometry+input_integrity_status",
        "movementSafetyStatus": "NOT_EVALUATED",
        "movementSafetyEvaluated": False,
        "movementSafetyReason": "no_live_action_trace",
        "plannedScreenPoint": planned_screen_point,
        "coordinateResolutionStatus": _dict(coordinate_trace).get("status"),
        "inputGeometryStatus": input_geometry.get("status"),
        "inputGeometryAvailable": input_geometry.get("inputGeometryAvailable"),
        "canvasScreenOrigin": input_geometry.get("canvasScreenOrigin"),
        "canvasSize": input_geometry.get("canvasSize"),
        "sourceCanvasSize": input_geometry.get("sourceCanvasSize"),
        "clientWindowBounds": input_geometry.get("clientWindowBounds"),
        "displayScale": input_geometry.get("displayScale"),
        "isClientFocused": input_geometry.get("isClientFocused"),
        "inputIntegrityMonitorPass": current_input_integrity.get("monitorPass"),
        "inputIntegrityStatus": current_input_integrity.get("status"),
        "expectedVidPidMatched": current_input_integrity.get("expectedVidPidMatched"),
        "lastArduinoEventAgeMs": current_input_integrity.get("lastArduinoEventAgeMs"),
        "liveActionHardBlocker": bool(live_phase.get("hardBlocker")),
        "operatorInjectedEvents": _dict(phase_counts.get("operator_phase")).get("operatorInjectedEvents"),
        "directBackendBypassCount": current_input_integrity.get("directBackendBypassCount"),
        "rawMonitorBlockers": _list(current_input_integrity.get("blockers")),
        "phaseAwareBlockers": blockers,
        "warnings": warnings,
        "noLiveInput": True,
    }


def _derive_human_input_controller_visibility(
    readiness: dict[str, Any],
    current_input_integrity: dict[str, Any],
) -> dict[str, Any]:
    action_readiness = _dict(readiness.get("actionReadiness"))
    try:
        from input_control.human_input_controller import resolve_input_profile

        default_profile = resolve_input_profile("instant_debug")
        default_profile_payload = {
            "profile": default_profile.name,
            "movementGenerator": default_profile.movement_generator,
            "minMoveMs": default_profile.min_move_ms,
            "maxMoveMs": default_profile.max_move_ms,
            "preClickSettleMs": list(default_profile.pre_click_settle_ms),
            "clickHoldMs": list(default_profile.click_hold_ms),
            "postClickSettleMs": list(default_profile.post_click_settle_ms),
        }
    except Exception as error:  # noqa: BLE001
        default_profile_payload = {
            "profile": "instant_debug",
            "movementGenerator": None,
            "error": f"{type(error).__name__}: {error}",
        }
    return {
        "schema": "human_input_controller_visibility.v1",
        "source": "executor_defaults+readiness+input_integrity_status",
        "controllerInstantiated": False,
        "metricsAvailable": False,
        "reason": "no_live_action_trace",
        "requiredLivePipeline": "HumanInputController -> ArduinoHIDBackend",
        "liveInputBackend": current_input_integrity.get("liveInputBackend") or "arduino",
        "liveInputBackendRequired": True,
        "softwareInputAllowed": False,
        "actionExecutionAllowed": action_readiness.get("executionAllowed"),
        "movementProfile": "not_instantiated_until_bounded_live_step",
        "movementGenerator": "not_instantiated_until_bounded_live_step",
        "executorDefaultProfile": default_profile_payload,
        "directBackendBypassCount": current_input_integrity.get("directBackendBypassCount"),
        "noLiveInput": True,
    }


def _derive_cursor_movement_trace(
    *,
    planned_screen_point: dict[str, Any] | None,
    coordinate_trace: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema": "cursor_movement_trace_visibility.v1",
        "source": "current_action_proposal",
        "movementPlanned": planned_screen_point is not None,
        "movementExecuted": False,
        "reason": "no_live_action_trace",
        "plannedEndScreenPoint": planned_screen_point,
        "coordinateResolutionStatus": _dict(coordinate_trace).get("status"),
        "lastMovementProof": None,
        "noLiveInput": True,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=False, default=str) + "\n", encoding="utf-8")
    temp.replace(path)


def _slug(value: Any, fallback: str = "context") -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or fallback)).strip("_")
    return (text or fallback)[:80]


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _response_size(payload: dict[str, Any]) -> int:
    try:
        return len(json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8"))
    except Exception:  # noqa: BLE001
        return 0


def _object_count_from_data(data: Any) -> int | None:
    if isinstance(data, dict):
        for key in ("count", "objectCount", "visibleResourceCandidateCount", "visualBundleCount"):
            parsed = _int(data.get(key))
            if parsed is not None:
                return parsed
        for key in ("objects", "items", "frontier", "candidates", "routes", "targetClasses"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
        nested = data.get("frontier")
        if isinstance(nested, dict):
            return _object_count_from_data(nested)
    if isinstance(data, list):
        return len(data)
    return None


def _perf_summary(payload: dict[str, Any]) -> dict[str, Any]:
    perf = _dict(payload.get("performanceStats"))
    return {
        "queryTimeMs": perf.get("queryTimeMs"),
        "responseBytes": perf.get("responseBytes") or _response_size(payload),
        "objectCount": perf.get("objectCount"),
        "sourceAgeMs": perf.get("sourceAgeMs"),
        "capHit": payload.get("capHit"),
        "truncated": payload.get("truncated"),
        "status": payload.get("status"),
        "schema": payload.get("schema"),
    }


def _external_summary_compact() -> dict[str, Any]:
    status = external_knowledge.knowledge_status()
    data = _dict(status.get("data"))
    return {
        "status": status.get("status"),
        "externalKnowledgeEnabled": data.get("externalKnowledgeEnabled"),
        "cacheFirst": data.get("cacheFirst"),
        "explicitRefreshOnly": data.get("explicitRefreshOnly"),
        "cachePath": data.get("cachePath"),
        "cacheSizeMb": data.get("cacheSizeMb"),
        "maxCacheMb": data.get("maxCacheMb"),
        "externalApiEnabledByDefault": data.get("externalApiEnabledByDefault"),
        "hotRuntimeExternalApiCallsAllowed": data.get("hotRuntimeExternalApiCallsAllowed"),
        "userAgentRequired": data.get("userAgentRequired"),
        "sourceCount": data.get("sourceCount"),
        "externalSourcesHealthy": data.get("externalSourcesHealthy"),
        "externalApiDisabledReason": data.get("externalApiDisabledReason"),
    }


def _cap_flags(payload: Any, prefix: str = "") -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            lower = key.lower()
            current = f"{prefix}.{key}" if prefix else key
            if lower in {"caphit", "truncated"} and value is True:
                flags.append({"path": current, "field": key, "value": True})
            elif lower.endswith("caphit") and value is True:
                flags.append({"path": current, "field": key, "value": True})
            elif isinstance(value, (dict, list)):
                flags.extend(_cap_flags(value, current))
    elif isinstance(payload, list):
        for index, item in enumerate(payload[:20]):
            if isinstance(item, (dict, list)):
                flags.extend(_cap_flags(item, f"{prefix}[{index}]"))
    return flags


def _point(value: Any) -> dict[str, Any] | None:
    value = _dict(value)
    x = value.get("worldX", value.get("x"))
    y = value.get("worldY", value.get("y"))
    plane = value.get("plane")
    if x is None or y is None:
        return None
    return {"worldX": x, "worldY": y, "plane": plane}


def _object_location(obj: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("worldLocation", "location", "tile", "worldTile"):
        point = _point(obj.get(key))
        if point is not None:
            return point
    x = obj.get("worldX", obj.get("x"))
    y = obj.get("worldY", obj.get("y"))
    if x is None or y is None:
        return None
    return {"worldX": x, "worldY": y, "plane": obj.get("plane")}


def _distance_tiles(a: dict[str, Any] | None, b: dict[str, Any] | None) -> float | None:
    if not a or not b:
        return None
    ax = _float(a.get("worldX"))
    ay = _float(a.get("worldY"))
    bx = _float(b.get("worldX"))
    by = _float(b.get("worldY"))
    if ax is None or ay is None or bx is None or by is None:
        return None
    return math.hypot(ax - bx, ay - by)


def _plane_matches(obj: dict[str, Any], plane: int | None) -> bool:
    if plane is None:
        return True
    obj_plane = _int(obj.get("plane"))
    return obj_plane is None or obj_plane == plane


def _actions(obj: dict[str, Any]) -> list[str]:
    values = obj.get("actions") or obj.get("actionNames") or obj.get("menuActions") or []
    return [str(item) for item in _list(values) if item is not None]


def _has_explicit_live_actions(obj: dict[str, Any]) -> bool:
    return any(isinstance(obj.get(key), list) for key in ("actions", "actionNames", "menuActions"))


def _resource_live_action_status(obj: dict[str, Any]) -> dict[str, Any]:
    actions = _actions(obj)
    normalized = [_norm(action) for action in actions]
    matching = [action for action, norm in zip(actions, normalized) if "chop" in norm]
    name = _norm(obj.get("name") or obj.get("targetName") or obj.get("objectName"))
    explicit = _has_explicit_live_actions(obj)
    blocked = bool(explicit and not matching)
    reasons = []
    if blocked:
        if "stump" in name:
            reasons.append("resource_stump_no_live_action")
        reasons.extend(["no_matching_live_resource_action", "external_knowledge_advisory_only"])
    return {
        "liveActionsExplicit": explicit,
        "liveActions": actions,
        "matchingLiveResourceActions": matching,
        "hasMatchingLiveResourceAction": bool(matching),
        "blockedByLiveAction": blocked,
        "rejectionReasons": reasons,
    }


def _action_text(obj: dict[str, Any]) -> str:
    return " ".join(_actions(obj)).lower()


def _projection(obj: dict[str, Any]) -> dict[str, Any]:
    return world_model_core.object_projection_status(obj)


def _projection_classification(obj: dict[str, Any]) -> str:
    projection = _projection(obj)
    for key in ("classification", "status", "reason"):
        value = projection.get(key)
        if isinstance(value, str) and value:
            return value
    if projection.get("projectionSentinel") is True:
        return "sentinel"
    if projection.get("actionableByCanvas") is True:
        return "actionable"
    if projection.get("visible") is True or projection.get("onScreen") is True:
        return "visible"
    return "unavailable"


def _point_xy(value: Any) -> tuple[float, float] | None:
    value = _dict(value)
    x = _float(value.get("canvasX", value.get("x")))
    y = _float(value.get("canvasY", value.get("y")))
    if x is None or y is None:
        return None
    return x, y


def _service_exposure_metrics(obj: dict[str, Any]) -> dict[str, Any]:
    projection = _projection(obj)
    safe = _dict(obj.get("safeAimPoint"))
    point = _point_xy(safe) or _point_xy(projection.get("aimPoint")) or _point_xy(projection.get("canvasPoint"))
    edge_distance = _float(
        safe.get("distanceToViewportEdgePx")
        if safe.get("distanceToViewportEdgePx") is not None
        else safe.get("distanceToCanvasEdgePx")
        if safe.get("distanceToCanvasEdgePx") is not None
        else projection.get("edgeDistancePx")
    )
    if edge_distance is None and point is not None:
        x, y = point
        edge_distance = min(x, 765.0 - x, y, 503.0 - y)
    area_px = _float(
        safe.get("clippedVisibleAreaPx")
        if safe.get("clippedVisibleAreaPx") is not None
        else projection.get("clippedVisibleAreaPx")
        if projection.get("clippedVisibleAreaPx") is not None
        else projection.get("visibleAreaPx")
    )
    ratio = _float(
        safe.get("clippedVisibleAreaRatio")
        if safe.get("clippedVisibleAreaRatio") is not None
        else projection.get("clippedVisibleAreaRatio")
        if projection.get("clippedVisibleAreaRatio") is not None
        else projection.get("visibleAreaRatio")
    )
    centrality = None
    comfortable = False
    if point is not None:
        x, y = point
        centrality = max(0.0, min(1.0, 1.0 - max(abs(x - 382.5) / 382.5, abs(y - 251.5) / 251.5)))
        margin_x = 765.0 * (1.0 - SERVICE_COMFORTABLE_REGION_FRACTION) / 2.0
        margin_y = 503.0 * (1.0 - SERVICE_COMFORTABLE_REGION_FRACTION) / 2.0
        comfortable = bool(margin_x <= x <= 765.0 - margin_x and margin_y <= y <= 503.0 - margin_y)
    safe_click = bool(
        projection.get("actionableByCanvas") is True
        or safe.get("actionable") is True
        or str(safe.get("status") or "").upper() == "PASS"
    )
    visible_area_ok = bool(area_px is None or area_px >= SERVICE_MIN_VISIBLE_AREA_PX) and bool(
        ratio is None or ratio >= SERVICE_MIN_VISIBLE_AREA_RATIO
    )
    edge_ok = bool(edge_distance is not None and edge_distance >= SERVICE_MIN_EDGE_DISTANCE_PX)
    edge_sliver = bool(
        safe_click
        and (
            (edge_distance is not None and edge_distance < SERVICE_MIN_EDGE_DISTANCE_PX)
            or (area_px is not None and area_px < SERVICE_MIN_VISIBLE_AREA_PX)
            or (ratio is not None and ratio < SERVICE_MIN_VISIBLE_AREA_RATIO)
        )
    )
    usable = bool(safe_click and visible_area_ok and edge_ok and comfortable and safe.get("uiBlocked") is not True)
    score = 0
    score += 30 if safe_click else 0
    score += 20 if visible_area_ok else 0
    score += 20 if edge_ok else 0
    score += 20 if comfortable else 0
    score += 10 if safe.get("uiBlocked") is not True else 0
    return {
        "safeClickAvailable": safe_click,
        "usableExposureThresholdMet": usable,
        "edgeSliverVisible": edge_sliver,
        "visibleAreaPx": area_px,
        "visibleAreaRatio": ratio,
        "edgeDistancePx": edge_distance,
        "centralityScore": round(centrality, 3) if centrality is not None else None,
        "comfortableViewRegionMet": comfortable,
        "usableExposureScore": max(0, min(100, score)),
    }


def _source_tick(payloads: dict[str, Any], daemon_status: dict[str, Any]) -> int | None:
    quality = world_model_core.world_model_quality(payloads)
    for value in (
        quality.get("sourceTick"),
        quality.get("tick"),
        _dict(payloads.get("world_model_summary")).get("tick"),
        daemon_status.get("latestTick"),
        daemon_status.get("tick"),
    ):
        parsed = _int(value)
        if parsed is not None:
            return parsed
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _status_view(status: dict[str, Any]) -> dict[str, Any]:
    status = _dict(status)
    nested = _dict(status.get("status"))
    if not nested:
        return status
    merged = dict(nested)
    for key, value in status.items():
        if key == "status":
            continue
        if key not in merged or merged.get(key) is None:
            merged[key] = value
    return merged


def _status_label(status: dict[str, Any]) -> Any:
    value = _dict(status).get("status")
    if isinstance(value, dict):
        return value.get("status") or value.get("schema")
    return value


def _source_metadata(status: dict[str, Any], *, fallback_source: str = "daemon_status") -> dict[str, Any]:
    status = _status_view(status)
    metadata = _dict(status.get("sourceMetadata") or status.get("_liveQueryMetadata"))
    return {
        "sourceUsed": metadata.get("sourceUsed") or status.get("sourceUsed") or fallback_source,
        "daemonUrl": metadata.get("daemonUrl") or status.get("daemonUrl"),
        "snapshotUrl": metadata.get("snapshotUrl") or status.get("snapshotUrl"),
        "contextSource": metadata.get("contextSource") or status.get("contextSource") or fallback_source,
        "fileSessionFallbackUsed": bool(metadata.get("fileSessionFallbackUsed", status.get("fileSessionFallbackUsed", False))),
        "freshnessSource": metadata.get("freshnessSource") or status.get("freshnessSource") or fallback_source,
    }


def _session_path_from_status(status: dict[str, Any]) -> Path | None:
    status = _status_view(status)
    for value in (
        status.get("sessionPath"),
        status.get("activeSessionPath"),
        _dict(status.get("session")).get("sessionPath"),
        _dict(status.get("session")).get("activeSessionPath"),
        _dict(status.get("brain")).get("sessionPath"),
    ):
        if isinstance(value, str) and value.strip():
            return Path(value)
    return None


def _world_payloads_from_status(status: dict[str, Any]) -> dict[str, Any]:
    status = _status_view(status)
    payloads = dict(_dict(status.get("worldModelPayloads")))
    mapping = {
        "world_model_summary": ("worldModelSummary",),
        "scene_object_census": ("worldModelSceneObjectCensus", "sceneObjectCensus"),
        "resource_object_census": ("worldModelResourceObjectCensus", "resourceObjectCensus"),
        "service_object_census": ("worldModelServiceObjectCensus", "serviceObjectCensus"),
        "route_object_census": ("worldModelRouteObjectCensus", "routeObjectCensus"),
        "projection_audit": ("worldModelProjectionAudit", "projectionAudit"),
        "view_quality_inputs": ("worldModelViewQualityInputs", "viewQualityInputs"),
        "pathing_frontier": ("worldModelPathingFrontier", "pathingFrontier"),
    }
    for key, status_keys in mapping.items():
        if key in payloads:
            continue
        for status_key in status_keys:
            if isinstance(status.get(status_key), dict):
                payloads[key] = status[status_key]
                break
    quality = {
        key: status.get(key)
        for key in (
            "worldModelAvailable",
            "worldModelAgeMs",
            "worldModelSourceTick",
            "worldModelClientTick",
            "worldModelObjectCensusCapHit",
            "worldModelCollisionAvailable",
            "worldModelProjectionAuditAvailable",
            "worldModelProjectionCapHit",
            "worldModelLoadedSceneOnly",
            "worldModelFullWorldLoaded",
        )
        if key in status
    }
    if quality and "quality" not in payloads:
        payloads["quality"] = quality
    return payloads


def _world_model_summary_payload(payloads: dict[str, Any] | None, status: dict[str, Any] | None = None) -> dict[str, Any]:
    payloads = _dict(payloads)
    status = _status_view(_dict(status))
    summary = _dict(payloads.get("world_model_summary") or status.get("worldModelSummary"))
    if not summary:
        return {}
    nested_data = _dict(summary.get("data"))
    nested_summary = _dict(nested_data.get("worldModelSummary") or summary.get("worldModelSummary"))
    if nested_summary:
        return nested_summary
    nested_payloads = _dict(summary.get("payloads"))
    payload_summary = _dict(nested_payloads.get("world_model_summary"))
    if payload_summary:
        return payload_summary
    return summary


def _world_model_object_total(payloads: dict[str, Any] | None, status: dict[str, Any] | None = None) -> int | None:
    status = _status_view(_dict(status))
    explicit_total = _int(status.get("worldModelObjectTotal"))
    if explicit_total is not None:
        return explicit_total
    summary = _world_model_summary_payload(payloads, status)
    objects = _dict(summary.get("objects"))
    total = _int(objects.get("total"))
    if total is not None:
        return total
    projection = _dict(summary.get("projection"))
    total = _int(projection.get("objectCount"))
    if total is not None:
        return total
    sizing = _dict(summary.get("sizing"))
    return _int(sizing.get("objectCount"))


def _copy_snapshot_liveness_fields(status: dict[str, Any], snapshot: dict[str, Any] | None, *, source: str) -> None:
    snapshot = _dict(snapshot)
    if not snapshot:
        return
    hot = _dict(snapshot.get("clientTickHot"))
    if hot and not _dict(status.get("clientTickHot")):
        status["clientTickHot"] = hot
        status.setdefault("clientTickHotSource", source)
    hover = _dict(snapshot.get("hoverMenu"))
    if hover and not _dict(status.get("hoverMenu")):
        status["hoverMenu"] = hover
    clicked = _dict(snapshot.get("lastMenuOptionClicked"))
    if clicked and not _dict(status.get("lastMenuOptionClicked")):
        status["lastMenuOptionClicked"] = clicked
    baseline = _dict(_dict(snapshot.get("payloads")).get("baseline"))
    game_state = _first_present(hot.get("gameState"), baseline.get("gameState"))
    if game_state is not None and status.get("gameState") is None:
        status["gameState"] = game_state
    latest_tick = _int(_first_present(snapshot.get("latestTick"), baseline.get("tick"), hot.get("gameTickAtSample")))
    if latest_tick is not None and status.get("pluginSnapshotLatestTick") is None:
        status["pluginSnapshotLatestTick"] = latest_tick


def _daemon_status_from_context(status: dict[str, Any]) -> dict[str, Any]:
    status = _dict(status)
    debug_context = _dict(_dict(status.get("knowledgeCurrentDebugContext")).get("data"))
    if debug_context:
        live = _dict(debug_context.get("liveStatus"))
        phase = _dict(live.get("phase"))
        location = _dict(live.get("location"))
        inventory = _dict(live.get("inventory"))
        merged_debug = dict(status)
        for key, value in (
            ("phase", phase.get("phase")),
            ("currentCycleStage", phase.get("cycleStage")),
            ("activeIntent", phase.get("activeIntent")),
            ("currentIntent", phase.get("currentIntent")),
            ("playerLocation", location.get("worldLocation")),
            ("playerLocationSource", location.get("source")),
            ("playerLocationConfidence", location.get("confidence")),
            ("inventoryFreeSlots", inventory.get("freeSlots")),
            ("inventoryOccupiedSlots", inventory.get("occupiedSlots")),
            ("resourceCount", inventory.get("resourceCount")),
        ):
            if value is not None and merged_debug.get(key) is None:
                merged_debug[key] = value
        if live.get("sessionPath") and merged_debug.get("sessionPath") is None:
            merged_debug["sessionPath"] = live.get("sessionPath")
        status = merged_debug
    nested = _dict(status.get("status"))
    if not nested:
        return status
    merged = _status_view(status)
    if status.get("warnings"):
        merged.setdefault("contextServiceWarnings", status.get("warnings"))
        if not merged.get("warnings"):
            merged["warnings"] = status.get("warnings")
    if status.get("missingFields"):
        merged.setdefault("contextServiceMissingFields", status.get("missingFields"))
    return merged


def _compact_object(obj: dict[str, Any]) -> dict[str, Any]:
    location = _object_location(obj)
    projection = _projection(obj)
    service_exposure = _service_exposure_metrics(obj) if obj.get("serviceObjectCandidate") is True else {}
    compact = {
        "objectKey": obj.get("objectKey") or obj.get("targetKey"),
        "id": obj.get("id", obj.get("rawId")),
        "hash": obj.get("hash"),
        "name": obj.get("name") or obj.get("targetName") or obj.get("objectName"),
        "kind": obj.get("kind") or obj.get("objectKind") or obj.get("targetType"),
        "actions": _actions(obj),
        "worldLocation": location,
        "plane": obj.get("plane") if obj.get("plane") is not None else (location or {}).get("plane"),
        "distanceToPlayer": obj.get("distanceToPlayer", obj.get("distanceTiles")),
        "resourceCandidate": obj.get("resourceCandidate"),
        "resourceType": obj.get("resourceType"),
        "serviceObjectCandidate": obj.get("serviceObjectCandidate"),
        "serviceObjectType": obj.get("serviceObjectType"),
        "routeObjectCandidate": obj.get("routeObjectCandidate"),
        "routeObjectKind": obj.get("routeObjectKind"),
        "projectionClassification": _projection_classification(obj),
        "projection": {
            "visible": projection.get("visible"),
            "onScreen": projection.get("onScreen"),
            "geometryAvailable": projection.get("geometryAvailable"),
            "actionableByCanvas": projection.get("actionableByCanvas"),
            "edgeDistancePx": projection.get("edgeDistancePx"),
            "visibleAreaRatio": projection.get("visibleAreaRatio"),
            "aimPoint": projection.get("aimPoint"),
            "canvasLocation": projection.get("canvasLocation"),
            "convexHullBounds": projection.get("convexHullBounds"),
            "canvasTileBounds": projection.get("canvasTileBounds"),
        },
    }
    if isinstance(obj.get("safeAimPoint"), dict):
        compact["safeAimPoint"] = obj.get("safeAimPoint")
    if isinstance(obj.get("targetViewState"), dict):
        compact["targetViewState"] = obj.get("targetViewState")
    if service_exposure:
        compact["serviceTargetExposure"] = service_exposure
    for key in (
        "requiredSkill",
        "requiredLevel",
        "playerLevelKnown",
        "playerLevel",
        "levelRequirementMet",
        "visibleButNotExecutable",
        "targetTemporarilyLockedReason",
        "futureEligibleWhenLevelMet",
    ):
        if key in obj:
            compact[key] = obj.get(key)
    return {key: value for key, value in compact.items() if value is not None}


def _compact_inventory(status: dict[str, Any]) -> dict[str, Any]:
    status = _status_view(status)
    brain = _dict(status.get("brain"))
    inventory = _dict(brain.get("inventoryContext"))
    progress = _dict(brain.get("goalProgress") or status.get("goalProgress") or status.get("brainProgress"))
    free_slots = _first_present(inventory.get("freeSlots"), status.get("inventoryFreeSlots"))
    occupied_slots = _first_present(inventory.get("occupiedSlots"), status.get("inventoryOccupiedSlots"))
    resource_count = _first_present(
        progress.get("heldResourceCount"),
        progress.get("currentHeldCount"),
        status.get("resourceCount"),
        status.get("inventoryMatchingResourceCount"),
    )
    parsed_free_slots = _int(free_slots)
    return {
        "freeSlots": free_slots,
        "occupiedSlots": occupied_slots,
        "inventoryFull": bool(parsed_free_slots == 0 or inventory.get("inventoryFull") is True or status.get("inventoryFull") is True),
        "resourceCount": resource_count,
        "resourceGroup": progress.get("resourceGroup"),
    }


def _client_tick_fresh(status: dict[str, Any]) -> bool:
    status = _status_view(status)
    hot = _dict(status.get("clientTickHot"))
    latency = _dict(hot.get("latency"))
    age = _float(
        _first_present(
            hot.get("ageMillis"),
            status.get("clientTickHotAgeMillis"),
            status.get("clientTickPostMenuSortAgeMillis"),
            latency.get("ageMillis"),
            latency.get("postMenuSortAgeMillis"),
        )
    )
    game_state = _first_present(hot.get("gameState"), status.get("gameState"))
    return bool(age is not None and age <= 1000 and (not game_state or game_state == "LOGGED_IN"))


def _compact_location(status: dict[str, Any]) -> dict[str, Any]:
    status = _status_view(status)
    player = _dict(status.get("playerLocation"))
    if not player:
        x = _first_present(status.get("playerWorldX"), status.get("worldX"))
        y = _first_present(status.get("playerWorldY"), status.get("worldY"))
        plane = _first_present(status.get("playerPlane"), status.get("plane"))
        if x is not None and y is not None:
            player = {"worldX": x, "worldY": y, "plane": plane}
    return {
        "worldLocation": player or None,
        "plane": player.get("plane") if player else status.get("playerPlane"),
        "source": status.get("playerLocationSource"),
        "confidence": status.get("playerLocationConfidence"),
    }


def _compact_phase(status: dict[str, Any]) -> dict[str, Any]:
    status = _status_view(status)
    brain = _dict(status.get("brain"))
    generic = _dict(brain.get("genericTaskState"))
    return {
        "phase": _first_present(status.get("brainPhase"), status.get("phase"), generic.get("phase")),
        "cycleStage": _first_present(status.get("currentCycleStage"), status.get("cycleStage"), generic.get("cycleStage")),
        "activeIntent": _first_present(status.get("activeIntent"), generic.get("activeIntent")),
        "currentIntent": status.get("currentIntent"),
        "latestTick": status.get("latestTick") or brain.get("latestTick"),
    }


def _compact_route_context(status: dict[str, Any]) -> dict[str, Any]:
    status = _status_view(status)
    brain = _dict(status.get("brain"))
    service_route = _dict(brain.get("serviceRouteContext") or status.get("serviceRouteContext"))
    pathing = _dict(brain.get("pathingContext") or status.get("pathingContext"))
    return {
        "routeId": _first_present(service_route.get("routeId"), status.get("serviceRouteId")),
        "currentNodeId": _first_present(service_route.get("currentNodeId"), status.get("serviceRouteCurrentNodeId")),
        "currentStepIndex": _first_present(service_route.get("currentStepIndex"), status.get("serviceRouteCurrentStepIndex")),
        "nextEdgeType": _first_present(service_route.get("nextEdgeType"), status.get("serviceRouteNextEdgeType")),
        "routeStepStatus": _first_present(service_route.get("routeStepStatus"), status.get("serviceRouteStepStatus")),
        "actionReady": _first_present(service_route.get("actionReady"), status.get("serviceRouteActionReady")),
        "serviceReady": _first_present(status.get("serviceReady"), service_route.get("serviceReady")),
        "serviceReadyReason": status.get("serviceReadyReason"),
        "pathingNeeded": _first_present(pathing.get("pathingNeeded"), status.get("pathingNeeded")),
        "nextWaypointTile": _first_present(pathing.get("nextWaypointTile"), status.get("pathingNextWaypointTile")),
        "destinationTile": _first_present(pathing.get("destinationTile"), status.get("pathingDestinationTile")),
        "pathTargetTile": _first_present(pathing.get("pathTargetTile"), status.get("pathingPathTargetTile")),
        "pathLengthTiles": _first_present(pathing.get("pathLengthTiles"), status.get("pathingPathLengthTiles")),
        "lineOfSightToTarget": _first_present(pathing.get("lineOfSightToTarget"), status.get("pathingLineOfSightToTarget")),
        "wallLoopDetected": _first_present(pathing.get("wallLoopDetected"), status.get("routeWallLoopDetected")),
        "wallHuggingDetected": status.get("routeWallHuggingDetected"),
        "rejectedApproachTileReasons": _first_present(
            pathing.get("rejectedApproachTileReasons"),
            status.get("pathingRejectedApproachTileReasons"),
        ),
        "predictedPathTiles": _list(pathing.get("predictedPathTiles") or status.get("pathingPredictedPathTiles"))[:35],
    }


def _route_context_applicability(
    status: dict[str, Any],
    *,
    readiness: dict[str, Any] | None = None,
    proposal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = _status_view(status)
    readiness = _dict(readiness)
    proposal = _dict(proposal)
    route = _compact_route_context(status)
    phase = _compact_phase(status)
    inventory = _compact_inventory(status)
    action_need = _dict(readiness.get("actionNeed"))
    current_intent = str(_first_present(readiness.get("currentIntent"), phase.get("currentIntent"), phase.get("activeIntent"), "") or "")
    proposed_action = str(_first_present(readiness.get("proposedAction"), proposal.get("proposedAction"), "") or "")
    cycle_stage = str(_first_present(action_need.get("cycleStage"), phase.get("cycleStage"), "") or "").lower()
    phase_name = str(_first_present(action_need.get("phase"), phase.get("phase"), "") or "").lower()
    active_intent = str(_first_present(action_need.get("activeIntent"), phase.get("activeIntent"), "") or "").lower()
    route_context_present = any(
        route.get(key) is not None
        for key in ("routeId", "currentNodeId", "currentStepIndex", "nextEdgeType", "routeStepStatus")
    )
    if action_need:
        needs_service = action_need.get("needsService") is True
    else:
        needs_service = bool(status.get("serviceNeeded") is True)
    inventory_full = bool(inventory.get("inventoryFull") is True or action_need.get("inventoryFreeSlots") == 0)
    route_intents = {
        "navigation_waypoint_action",
        "route_transition_action",
        "service_object_action",
        "interface_dialogue_choice_action",
    }
    route_phases = {
        "needs_service",
        "route_to_service",
        "pathing_to_service",
        "inventory_full",
        "service",
        "return_to_resource",
        "return_to_resource_area",
    }
    active_route_action = bool(current_intent in route_intents or proposed_action in {"navigate_to_service", "return_to_resource_area", "open_service", "interact_service_route_object", "interface_dialogue_choice"})
    route_phase_active = bool(cycle_stage in route_phases or phase_name in route_phases or active_intent in route_phases)
    selected_target = _dict(proposal.get("targetExplanation"))
    safe_aimpoint = _dict(selected_target.get("safeAimPoint"))
    selected_resource_actionable = bool(
        proposal.get("executable") is True
        and (safe_aimpoint.get("status") in {None, "PASS"} or safe_aimpoint.get("actionable") is True)
    )
    resource_collection_ready = bool(
        route_context_present
        and current_intent == "resource_object_action"
        and proposed_action == "select_resource_target"
        and selected_resource_actionable
        and not needs_service
        and not inventory_full
    )
    applicable = bool(route_context_present and (needs_service or inventory_full or active_route_action or route_phase_active))
    if resource_collection_ready:
        applicable = False
        reason = "collecting_resources_resource_target_ready"
    elif not route_context_present:
        reason = "route_context_absent"
    elif applicable:
        reason = (
            "route_intent_active"
            if active_route_action
            else "service_needed"
            if needs_service
            else "inventory_full"
            if inventory_full
            else "route_phase_active"
        )
    else:
        reason = "route_context_not_required_for_current_intent"
    age_ms = _first_present(
        status.get("routeContextAgeMs"),
        status.get("serviceRouteContextAgeMs"),
        _dict(_dict(status.get("brain")).get("serviceRouteContext")).get("ageMs"),
        _dict(_dict(status.get("brain")).get("pathingContext")).get("ageMs"),
    )
    return {
        "routeContextPresent": bool(route_context_present),
        "routeContextApplicable": bool(applicable),
        "routeContextApplicabilityReason": reason,
        "routeContextWarningOnly": bool(route_context_present and not applicable),
        "staleRouteContextSuppressed": bool(route_context_present and not applicable),
        "routeContextSource": "daemon_status" if route_context_present else None,
        "routeContextAgeMs": age_ms,
    }


def _dedupe_objects(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for obj in objects:
        location = _object_location(obj) or {}
        key = (
            obj.get("objectKey") or obj.get("targetKey"),
            obj.get("hash"),
            obj.get("id", obj.get("rawId")),
            location.get("worldX"),
            location.get("worldY"),
            location.get("plane"),
            obj.get("name") or obj.get("targetName") or obj.get("objectName"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(obj)
    return unique


def _cap_items(items: list[dict[str, Any]], limit: int | None) -> tuple[list[dict[str, Any]], bool]:
    cap = _safe_limit(limit)
    if cap == 0:
        return [], bool(items)
    return items[:cap], len(items) > cap


def _query_response(
    schema: str,
    data: Any,
    *,
    started: float,
    source: str,
    freshness: dict[str, Any],
    warnings: list[str] | None = None,
    cap_hit: bool = False,
    truncated: bool = False,
    status: str = "PASS",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": schema,
        "status": status,
        "generatedAtUtc": utc_now(),
        "source": source,
        "freshness": freshness,
        "data": data,
        "warnings": list(warnings or []),
        "capHit": bool(cap_hit),
        "truncated": bool(truncated),
        "performanceStats": {
            "queryTimeMs": round((time.perf_counter() - started) * 1000.0, 3),
            "objectCount": _object_count_from_data(data),
            "sourceAgeMs": freshness.get("worldModelAgeMs") if isinstance(freshness, dict) else None,
        },
    }
    if extra:
        payload.update(extra)
    payload["performanceStats"]["responseBytes"] = _response_size(payload)
    return payload


def static_library_paths(root: Path | None = None) -> dict[str, Path]:
    base = Path(root) if root else VIEWER_DIR
    return {
        "targetProfiles": base / "target_profiles.json",
        "targetLibrary": base / "target_library.json",
        "serviceRoutes": base / "profiles" / "service_routes.json",
    }


def load_static_library(root: Path | None = None) -> dict[str, Any]:
    paths = static_library_paths(root)
    target_profiles = _read_json(paths["targetProfiles"])
    target_library = _read_json(paths["targetLibrary"])
    service_routes = _read_json(paths["serviceRoutes"])
    skill_requirements = {
        "Tree": {"requiredSkill": "WOODCUTTING", "requiredLevel": 1},
        "Dead tree": {"requiredSkill": "WOODCUTTING", "requiredLevel": 1},
        "Oak": {"requiredSkill": "WOODCUTTING", "requiredLevel": 15},
        "Willow": {"requiredSkill": "WOODCUTTING", "requiredLevel": 30},
    }
    for target in _list(target_library.get("targetClasses")):
        if not isinstance(target, dict):
            continue
        display = target.get("displayName") or target.get("classId")
        required_level = target.get("requiredLevel")
        required_skill = target.get("requiredSkill")
        if display and required_level is not None:
            skill_requirements[str(display)] = {
                "requiredSkill": required_skill or "WOODCUTTING",
                "requiredLevel": required_level,
            }
    routes = _list(service_routes.get("routes"))
    service_anchors = []
    area_hints = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        if route.get("areaHint"):
            area_hints.append({"routeId": route.get("routeId"), "areaHint": route.get("areaHint")})
        for node in _list(route.get("nodes")):
            if not isinstance(node, dict):
                continue
            node_type = _norm(node.get("type"))
            if "service" in node_type or "bank" in node_type:
                service_anchors.append({
                    "routeId": route.get("routeId"),
                    "nodeId": node.get("nodeId"),
                    "label": node.get("label"),
                    "serviceType": route.get("serviceType"),
                    "worldLocation": node.get("worldLocation"),
                    "verifiedLive": node.get("verifiedLive", False),
                    "confidence": node.get("confidence"),
                    "advisoryOnly": not bool(node.get("verifiedLive")),
                })
    payload = {
        "schema": STATIC_LIBRARY_SCHEMA,
        "routes": routes,
        "serviceAnchors": service_anchors,
        "targetProfiles": _list(target_profiles.get("profiles")),
        "targetLibrary": _list(target_library.get("targetClasses")),
        "skillRequirements": skill_requirements,
        "areaHints": area_hints,
        "sourceFiles": {key: str(value) for key, value in paths.items()},
    }
    payload["versionHash"] = _json_hash(payload)
    payload["summary"] = {
        "routeCount": len(payload["routes"]),
        "serviceAnchorCount": len(service_anchors),
        "targetProfileCount": len(payload["targetProfiles"]),
        "targetClassCount": len(payload["targetLibrary"]),
        "skillRequirementCount": len(skill_requirements),
        "staticPriorsAdvisory": True,
    }
    return payload


def session_memory_path(session_path: str | Path | None) -> Path | None:
    if not session_path:
        return None
    return Path(session_path) / LIVE_DIR / SESSION_MEMORY_FILE


def load_session_memory(session_path: str | Path | None) -> dict[str, Any]:
    path = session_memory_path(session_path)
    if path is None or not path.exists():
        return {"schema": SESSION_MEMORY_SCHEMA, "sessionPath": str(session_path) if session_path else None}
    payload = _read_json(path)
    if payload.get("schema") != SESSION_MEMORY_SCHEMA:
        return {"schema": SESSION_MEMORY_SCHEMA, "sessionPath": str(session_path), "warnings": ["session memory schema mismatch"]}
    return payload


def save_session_memory(session_path: str | Path, memory: dict[str, Any]) -> Path:
    path = session_memory_path(session_path)
    if path is None:
        raise ValueError("session path is required")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temp.write_text(json.dumps(memory, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    temp.replace(path)
    return path


def _memory_list(memory: dict[str, Any], key: str) -> list[Any]:
    values = memory.get(key)
    if isinstance(values, list):
        return values
    memory[key] = []
    return memory[key]


def record_session_observation(
    session_path: str | Path,
    kind: str,
    payload: dict[str, Any],
    *,
    source_tick: int | None = None,
    max_items: int = 200,
) -> dict[str, Any]:
    memory = load_session_memory(session_path)
    memory.setdefault("schema", SESSION_MEMORY_SCHEMA)
    memory["sessionPath"] = str(session_path)
    memory["lastUpdatedTick"] = source_tick
    memory["lastUpdatedUtc"] = utc_now()
    key_by_kind = {
        "resource_area": "observedResourceAreas",
        "service_anchor": "observedServiceAnchors",
        "route_object": "observedRouteObjects",
        "successful_waypoint": "successfulWaypoints",
        "failed_waypoint": "failedWaypoints",
        "menu_flip_zone": "menuFlipZones",
        "camera_view_outcome": "cameraViewOutcomes",
        "area_label": "learnedAreaLabels",
    }
    key = key_by_kind.get(kind, kind)
    item = dict(payload)
    item.setdefault("kind", kind)
    item.setdefault("sourceTick", source_tick)
    item.setdefault("observedUtc", utc_now())
    values = _memory_list(memory, key)
    values.append(item)
    if len(values) > max_items:
        del values[: len(values) - max_items]
    save_session_memory(session_path, memory)
    return memory


def session_memory_is_current(memory: dict[str, Any], current_session_path: str | Path | None) -> bool:
    if not current_session_path:
        return False
    return str(memory.get("sessionPath") or "").lower() == str(current_session_path).lower()


def _memory_from_status(status: dict[str, Any], session_path: Path | None) -> dict[str, Any]:
    loaded = load_session_memory(session_path) if session_path else {"schema": SESSION_MEMORY_SCHEMA}
    brain = _dict(status.get("brain"))
    service_route = _dict(brain.get("serviceRouteContext") or status.get("serviceRouteContext"))
    return_route = _dict(brain.get("returnRouteContext") or status.get("returnRouteContext"))
    resource_memory = _dict(
        brain.get("resourceAreaMemory")
        or _dict(brain.get("decision")).get("resourceAreaMemory")
        or status.get("resourceAreaMemory")
    )
    memory = {
        "schema": SESSION_MEMORY_SCHEMA,
        "sessionPath": str(session_path) if session_path else loaded.get("sessionPath"),
        "observedResourceAreas": list(_list(loaded.get("observedResourceAreas"))),
        "observedServiceAnchors": list(_list(loaded.get("observedServiceAnchors"))),
        "observedRouteObjects": list(_list(loaded.get("observedRouteObjects"))),
        "successfulWaypoints": list(_list(loaded.get("successfulWaypoints"))),
        "failedWaypoints": list(_list(loaded.get("failedWaypoints"))),
        "menuFlipZones": list(_list(loaded.get("menuFlipZones"))),
        "cameraViewOutcomes": list(_list(loaded.get("cameraViewOutcomes"))),
        "learnedAreaLabels": list(_list(loaded.get("learnedAreaLabels"))),
        "lastUpdatedTick": loaded.get("lastUpdatedTick") or status.get("latestTick"),
        "lastUpdatedUtc": loaded.get("lastUpdatedUtc"),
    }
    if resource_memory:
        memory["observedResourceAreas"].append(resource_memory)
    anchors = _dict(service_route.get("observedAnchors") or return_route.get("observedAnchors"))
    for anchor in anchors.values():
        if isinstance(anchor, dict):
            memory["observedServiceAnchors"].append(anchor)
    route_census = service_route.get("routeObjectCensus") or status.get("serviceRouteObjectCensus")
    if isinstance(route_census, dict):
        memory["observedRouteObjects"].extend(_list(route_census.get("objects") or route_census.get("topObjects")))
    memory["summary"] = {
        "observedResourceAreaCount": len(memory["observedResourceAreas"]),
        "observedServiceAnchorCount": len(memory["observedServiceAnchors"]),
        "observedRouteObjectCount": len(memory["observedRouteObjects"]),
        "failedWaypointCount": len(memory["failedWaypoints"]),
        "menuFlipZoneCount": len(memory["menuFlipZones"]),
        "cameraViewOutcomeCount": len(memory["cameraViewOutcomes"]),
        "loadedFromFile": bool(loaded.get("lastUpdatedUtc")),
        "currentSessionOnly": True,
        "executionUse": "advisory_prior_until_live_verified",
    }
    return memory


def _debug_evidence_index(session_path: Path | None, limit: int = 10) -> dict[str, Any]:
    if session_path is None:
        return {"schema": DEBUG_EVIDENCE_SCHEMA, "sessionPath": None, "visualBundles": [], "latestActionTraces": []}
    live_dir = session_path / LIVE_DIR
    bundles_root = live_dir / "debug_bundles"
    visual_bundles = []
    if bundles_root.exists():
        for bundle in sorted(bundles_root.iterdir(), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)[:limit]:
            if not bundle.is_dir():
                continue
            bundle_json = _read_json(bundle / "bundle.json")
            visual_bundles.append({
                "bundleDir": str(bundle),
                "reason": bundle_json.get("reason"),
                "screenshotPath": bundle_json.get("screenshotPath"),
                "finalDecision": bundle_json.get("finalDecision"),
                "classification": bundle_json.get("classification"),
            })
    trace_paths = []
    for name in ("last_action_trace.json", "action_trace.json"):
        path = live_dir / name
        if path.exists():
            trace = _read_json(path)
            trace_payload = _trace_payload(trace)
            visibility = _action_trace_visibility(trace_payload, path=str(path))
            navigation_trace = _navigation_trace_records_from_trace(trace_payload)
            trace_paths.append({
                "path": str(path),
                "classification": trace_payload.get("finalClassification"),
                "proposedAction": trace_payload.get("proposedAction"),
                "actionIntentType": trace_payload.get("actionIntentType"),
                "plannedTarget": visibility.get("plannedTarget"),
                "plannedScreenPoint": visibility.get("plannedScreenPoint"),
                "displayScaleApplied": visibility.get("displayScaleApplied"),
                "displayScaleReason": visibility.get("displayScaleReason"),
                "directBackendBypassCount": visibility.get("directBackendBypassCount"),
                "inputIntegrityPhaseCounts": _dict(visibility.get("input_integrity_status")).get("phaseCounts"),
                "hoverConfirmationEvidence": visibility.get("hoverConfirmationEvidence"),
                "menuOptionClickedEvidence": visibility.get("menuOptionClickedEvidence"),
                "blockedReason": visibility.get("blockedReason"),
                "navigationDecisionTrace": navigation_trace,
                "navigationDecisionTraceCount": len(navigation_trace),
                "lastNavigationDecisionTrace": dict(navigation_trace[-1]) if navigation_trace else None,
                "visibility": visibility,
            })
    failure_reasons = Counter(
        str(item.get("reason") or item.get("classification") or "unknown")
        for item in visual_bundles
    )
    return {
        "schema": DEBUG_EVIDENCE_SCHEMA,
        "sessionPath": str(session_path),
        "latestActionTraces": trace_paths,
        "visualBundles": visual_bundles,
        "failureReasons": dict(failure_reasons),
        "screenshots": [item.get("screenshotPath") for item in visual_bundles if item.get("screenshotPath")],
        "relevantTelemetryFiles": {
            "overlayDebugState": str(live_dir / "overlay_debug_state.json") if (live_dir / "overlay_debug_state.json").exists() else None,
            "sessionMemory": str(session_memory_path(session_path)) if session_memory_path(session_path) and session_memory_path(session_path).exists() else None,
        },
    }


class KnowledgeFabric:
    def __init__(
        self,
        *,
        world_model_payloads: dict[str, Any] | None = None,
        daemon_status: dict[str, Any] | None = None,
        static_library: dict[str, Any] | None = None,
        session_path: str | Path | None = None,
        source: str = "in_memory",
    ) -> None:
        started = time.perf_counter()
        self.world_model_payloads = _dict(world_model_payloads)
        self.daemon_status = _dict(daemon_status)
        self.session_path = Path(session_path) if session_path else _session_path_from_status(self.daemon_status)
        self.source = source
        self.static_library = static_library or load_static_library()
        self.session_memory = _memory_from_status(self.daemon_status, self.session_path)
        self.debug_evidence = _debug_evidence_index(self.session_path)
        self.objects = self._collect_objects()
        self.index = self._build_index()
        self.build_time_ms = round((time.perf_counter() - started) * 1000.0, 3)

    @classmethod
    def from_status(cls, status: dict[str, Any] | None, **kwargs: Any) -> "KnowledgeFabric":
        raw_status = _dict(status)
        daemon_status = _daemon_status_from_context(raw_status)
        payloads = _world_payloads_from_status(daemon_status) or _world_payloads_from_status(raw_status)
        return cls(world_model_payloads=payloads, daemon_status=daemon_status, source="daemon_status", **kwargs)

    @classmethod
    def from_snapshot_response(cls, response: dict[str, Any] | None, **kwargs: Any) -> "KnowledgeFabric":
        payloads = world_model_core.extract_world_model_payloads(_dict(response))
        return cls(world_model_payloads=payloads, daemon_status={}, source="plugin_snapshot_response", **kwargs)

    def _collect_objects(self) -> list[dict[str, Any]]:
        payloads = self.world_model_payloads
        objects: list[dict[str, Any]] = []
        for key in ("scene_object_census", "resource_object_census", "service_object_census", "route_object_census"):
            census = _dict(payloads.get(key))
            for obj in world_model_core.census_objects(census):
                item = dict(obj)
                item.setdefault("_knowledgeSource", key)
                objects.append(item)
        return _dedupe_objects(objects)

    def _build_index(self) -> dict[str, Any]:
        plane_counts: Counter[str] = Counter()
        action_counts: Counter[str] = Counter()
        name_counts: Counter[str] = Counter()
        projection_counts: Counter[str] = Counter()
        by_action: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_name_token: dict[str, list[dict[str, Any]]] = defaultdict(list)
        resource_objects = []
        service_objects = []
        route_objects = []
        for obj in self.objects:
            plane_counts[str(obj.get("plane", "unknown"))] += 1
            name = _norm(obj.get("name") or obj.get("targetName") or obj.get("objectName"))
            if name:
                name_counts[name] += 1
                for token in name.replace("/", " ").replace("-", " ").split():
                    by_name_token[token].append(obj)
            for action in _actions(obj):
                normalized = _norm(action)
                if normalized:
                    action_counts[normalized] += 1
                    by_action[normalized].append(obj)
            projection_counts[_projection_classification(obj)] += 1
            if obj.get("resourceCandidate") is True:
                resource_objects.append(obj)
            if obj.get("serviceObjectCandidate") is True:
                service_objects.append(obj)
            if obj.get("routeObjectCandidate") is True:
                route_objects.append(obj)
        frontier = _dict(self.world_model_payloads.get("pathing_frontier"))
        projection_audit = _dict(self.world_model_payloads.get("projection_audit"))
        view_inputs = _dict(self.world_model_payloads.get("view_quality_inputs"))
        return {
            "schema": LIVE_WORLD_INDEX_SCHEMA,
            "objectCount": len(self.objects),
            "spatialIndexSummary": {
                "objectCount": len(self.objects),
                "planeCounts": dict(plane_counts),
                "indexedObjectsWithLocation": sum(1 for obj in self.objects if _object_location(obj) is not None),
            },
            "objectActionIndexSummary": {
                "distinctActions": len(action_counts),
                "actionCounts": dict(action_counts.most_common(25)),
                "distinctNames": len(name_counts),
            },
            "routeObjectIndexSummary": {"count": len(route_objects)},
            "resourceIndexSummary": {"count": len(resource_objects)},
            "serviceIndexSummary": {"count": len(service_objects)},
            "projectionIndexSummary": {
                "classificationCounts": dict(projection_counts),
                "projectionAuditAvailable": bool(projection_audit),
                "projectionCapHit": projection_audit.get("projectionCapHit"),
            },
            "collisionFrontierIndexSummary": {
                "available": bool(frontier),
                "frontierCount": frontier.get("frontierCount") or frontier.get("candidateCount") or len(_list(frontier.get("frontier"))),
                "capHit": frontier.get("capHit") or frontier.get("truncated"),
            },
            "viewQualityInputSummary": {
                "available": bool(view_inputs),
                "resourceCount": view_inputs.get("resourceCount") or len(_list(view_inputs.get("resources"))),
                "routeCount": view_inputs.get("routeCount") or len(_list(view_inputs.get("routes"))),
                "serviceCount": view_inputs.get("serviceCount") or len(_list(view_inputs.get("services"))),
            },
            "_byAction": by_action,
            "_byNameToken": by_name_token,
            "_resourceObjects": resource_objects,
            "_serviceObjects": service_objects,
            "_routeObjects": route_objects,
        }

    def freshness(self) -> dict[str, Any]:
        quality = world_model_core.world_model_quality(self.world_model_payloads)
        return {
            "sourceTick": _source_tick(self.world_model_payloads, self.daemon_status),
            "worldModelFresh": quality.get("worldModelAvailable") is True and quality.get("worldModelAgeMs") not in (None, ""),
            "worldModelAgeMs": quality.get("worldModelAgeMs"),
            "sessionPath": str(self.session_path) if self.session_path else None,
            "sessionMemoryFresh": session_memory_is_current(self.session_memory, self.session_path),
            "staticLibraryLoaded": bool(self.static_library.get("summary")),
        }

    def status(self) -> dict[str, Any]:
        quality = world_model_core.world_model_quality(self.world_model_payloads)
        stale_sources = []
        if quality.get("worldModelAvailable") is not True:
            stale_sources.append("world_model")
        if not session_memory_is_current(self.session_memory, self.session_path):
            stale_sources.append("session_memory")
        cap_warnings = []
        for key in ("objectCensusCapHit", "worldModelObjectCensusCapHit", "projectionCapHit", "worldModelProjectionCapHit"):
            if quality.get(key) is True:
                cap_warnings.append(key)
        return {
            "schema": STATUS_SCHEMA,
            "status": "WARN" if stale_sources or cap_warnings else "PASS",
            "generatedAtUtc": utc_now(),
            "worldModelFresh": quality.get("worldModelAvailable") is True,
            "worldModelAgeMs": quality.get("worldModelAgeMs"),
            "sessionMemoryFresh": session_memory_is_current(self.session_memory, self.session_path),
            "staticLibraryLoaded": bool(self.static_library.get("summary")),
            "indexesBuilt": True,
            "queryCapabilities": [
                "query_world_summary",
                "query_current_debug_context",
                "query_objects_near",
                "query_actions",
                "query_resource_candidates",
                "query_service_candidates",
                "query_route_objects",
                "query_path_frontier",
                "query_view_quality",
                "query_navigation_decision_trace",
                "query_worksite_context",
                "query_session_memory",
                "query_debug_evidence",
                "explain_current_blocker",
                "explain_next_action_context",
                "list_available_profiles",
                "describe_profile",
                "list_target_classes",
                "list_known_actions",
                "list_service_routes",
                "describe_route",
                "explain_required_telemetry_for_task",
                "query_scene_for_new_task_keywords",
                "suggest_profile_skeleton_from_scene",
                "list_seen_objects_by_action",
                "list_seen_objects_by_name",
                "list_seen_widgets",
                "list_seen_inventory_items",
                "export_task_context_bundle",
                "capture_script_authoring_context",
                "capture_replay_scenario",
                "data_quality_report",
                "coverage_report",
                "data_source_inventory",
                "query_coverage_matrix",
                "external_knowledge_status",
                "external_lookup_item",
                "external_lookup_item_id",
                "external_lookup_object",
                "external_lookup_area",
                "external_get_skill_requirement",
                "probe_task",
                "task_script_api_spec",
                "validate_task_script",
                "compile_task_script",
                "explain_script_plan",
                "task_script_evidence_plan",
                "query_task_script_runtime_evidence",
                "compare_task_script_runtime_evidence",
                "classify_task_failure",
                "assess_task_script_step",
                "assess_task_script_run",
                "suggest_task_template",
                "probe_task_from_scene",
                "handoff_summary",
            ],
            "staleSources": stale_sources,
            "capWarnings": cap_warnings,
            "performanceStats": {
                "buildTimeMs": self.build_time_ms,
                "objectCount": len(self.objects),
                "indexObjectCount": self.index.get("objectCount"),
            },
            "liveWorldIndex": self.live_world_index_summary(),
            "sessionMemorySummary": self.session_memory.get("summary", {}),
            "staticLibrarySummary": self.static_library.get("summary", {}),
            "externalKnowledgeSummary": _external_summary_compact(),
            "debugEvidenceSummary": {
                "visualBundleCount": len(_list(self.debug_evidence.get("visualBundles"))),
                "latestActionTraceCount": len(_list(self.debug_evidence.get("latestActionTraces"))),
            },
        }

    def live_world_index_summary(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.index.items()
            if not key.startswith("_")
        }

    def data_source_inventory(self) -> dict[str, Any]:
        started = time.perf_counter()
        external_status = external_knowledge.knowledge_status()
        data = {
            "sources": [
                {
                    "sourceName": "8893 PluginSnapshotEndpoint",
                    "sourceType": "live",
                    "producer": "RuneLite plugin HTTP endpoint",
                    "consumer": "live_core_daemon, Knowledge Fabric, diagnostics",
                    "schema": "plugin_snapshot_response.v1",
                    "freshnessField": "latestTick/sourceAgeMs/pluginSnapshotFresh",
                    "capFields": ["capHit", "truncated", "responseSizing", "pluginSnapshotMaxResponseBytes"],
                    "runtimeCritical": True,
                    "explicitDebugOnly": False,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "oldLivePacketReplacement": "Direct endpoint query replaces live_packets/live-*.ndjson.",
                    "sampleQuery": "Invoke-RestMethod http://127.0.0.1:8893/health",
                },
                {
                    "sourceName": "WorldModelCache",
                    "sourceType": "live_loaded_scene",
                    "producer": "RuneLite Java cache",
                    "consumer": "PluginSnapshotEndpoint, Knowledge Fabric",
                    "schema": "world_model_snapshot.v1",
                    "freshnessField": "worldModelAgeMs/sourceTick",
                    "capFields": ["objectCensusCapHit", "projectionCapHit", "truncated"],
                    "runtimeCritical": True,
                    "explicitDebugOnly": False,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "sampleQuery": "python telemetry-viewer\\context_service.py --query current-debug-context",
                },
                {
                    "sourceName": "8890 daemon/context service",
                    "sourceType": "live_context",
                    "producer": "live_core_daemon.py/context_service.py",
                    "consumer": "Codex, executor readiness, diagnostics, MCP adapter",
                    "schema": "context_status.v1/context_response.v1",
                    "freshnessField": "daemonFresh/latestTick/clientTickFresh",
                    "capFields": ["maxCandidates", "maxResponseBytes", "capHit", "truncated"],
                    "runtimeCritical": True,
                    "explicitDebugOnly": False,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "sampleQuery": "python telemetry-viewer\\context_service.py --query current-debug-context",
                },
                {
                    "sourceName": "client_tick_hot / hover/menu/click proof",
                    "sourceType": "live_hot",
                    "producer": "RuneLite plugin",
                    "consumer": "hover confirmation, blocker explanation, action trace",
                    "schema": "client_tick_hot.v1",
                    "freshnessField": "clientTickHotFresh/sourceAgeMs",
                    "capFields": ["menuTailLimit"],
                    "runtimeCritical": True,
                    "explicitDebugOnly": False,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "sampleQuery": "get_current_debug_context -> liveStatus/clientTickHot",
                },
                {
                    "sourceName": "overlay_debug_state",
                    "sourceType": "bounded_debug_latest_state",
                    "producer": "live_core_daemon overlay writer",
                    "consumer": "RuneLite debug overlay, visual bundles",
                    "schema": "telemetry_overlay_debug_state.v1",
                    "freshnessField": "lastOverlayWriteAgeMs",
                    "capFields": ["overlayDebugTargetLimit"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": True,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "sampleQuery": "get_current_debug_context -> overlayHealth",
                },
                {
                    "sourceName": "input_integrity_status",
                    "sourceType": "bounded_debug_latest_state",
                    "producer": "input integrity monitor",
                    "consumer": "readiness, blocker explanation, live safety audit",
                    "schema": "input_integrity_status.v1",
                    "freshnessField": "generatedAtUtc/sourceAgeMs",
                    "capFields": [],
                    "runtimeCritical": True,
                    "explicitDebugOnly": False,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "sampleQuery": "get_current_debug_context -> inputIntegrity",
                },
                {
                    "sourceName": "action_input_visibility_context",
                    "sourceType": "bounded_debug_latest_state",
                    "producer": "Knowledge Fabric over action_trace.v2, current action proposal, readiness input geometry, and daemon status",
                    "consumer": "Codex MCP/direct visibility into planned action, target, coordinates, hover/click proof, input block evidence, and input integrity by phase; derived proposal fields are read-only",
                    "schema": ACTION_INPUT_VISIBILITY_SCHEMA,
                    "freshnessField": "latest action trace/session freshness",
                    "capFields": ["candidate limits", "debug evidence limits"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": True,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "sampleQuery": "get_action_input_visibility",
                },
                {
                    "sourceName": "navigation_decision_trace.v1",
                    "sourceType": "bounded_debug_latest_state",
                    "producer": "executor action_trace.v2 navigationDecisionTrace entries",
                    "consumer": "Codex route/pathing regression diagnosis",
                    "schema": NAVIGATION_DECISION_TRACE_SUMMARY_SCHEMA,
                    "freshnessField": "latest action trace/session freshness",
                    "capFields": ["trace context row limit"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": True,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "sampleQuery": "query_navigation_decision_trace",
                },
                {
                    "sourceName": "visual_debug_bundle",
                    "sourceType": "explicit_debug_bundle",
                    "producer": "executor/visual_debug_bundle.py",
                    "consumer": "Codex visual QA and replay evidence",
                    "schema": "visual_debug_bundle_summary.v1",
                    "freshnessField": "createdAt/generatedAtUtc",
                    "capFields": ["maxDebugScreenshots", "summary caps"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": True,
                    "canGrowOnDisk": True,
                    "requiresInternet": False,
                    "sampleQuery": "get_latest_visual_bundle",
                },
                {
                    "sourceName": "replay_scenario.v1",
                    "sourceType": "explicit_replay",
                    "producer": "context_service.py --capture-replay-scenario",
                    "consumer": "offline Knowledge Fabric replay",
                    "schema": "replay_scenario.v1",
                    "freshnessField": "createdAt/sourceTick",
                    "capFields": ["limit", "maxCandidates"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": True,
                    "canGrowOnDisk": True,
                    "requiresInternet": False,
                    "sampleQuery": "python telemetry-viewer\\context_service.py --replay-scenario <path>",
                },
                {
                    "sourceName": "script_authoring_context.v1",
                    "sourceType": "explicit_script_authoring",
                    "producer": "Knowledge Fabric",
                    "consumer": "Codex future script/profile authoring",
                    "schema": "script_authoring_context.v1",
                    "freshnessField": "createdAt",
                    "capFields": ["limit", "queryTimes", "responseSizes"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": True,
                    "canGrowOnDisk": True,
                    "requiresInternet": False,
                    "sampleQuery": "python telemetry-viewer\\context_service.py --capture-script-authoring-context --profile woodcutting",
                },
                {
                    "sourceName": "task_script_api.v1",
                    "sourceType": "script_authoring_contract",
                    "producer": "task_script_api.py",
                    "consumer": "Knowledge Fabric, MCP/direct script validators, future task scripts",
                    "schema": task_script_api.TASK_SCRIPT_SPEC_SCHEMA,
                    "freshnessField": "static code version",
                    "capFields": ["repeat_until.maxIterations", "validation errors"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": False,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "sampleQuery": "get_task_script_api_spec",
                },
                {
                    "sourceName": "task_script_runtime_evidence.v1",
                    "sourceType": "read_only_runtime_evidence",
                    "producer": "Knowledge Fabric over daemon/readiness/client_tick/action visibility",
                    "consumer": "Codex script validation, replay/live lifecycle proof comparison",
                    "schema": task_script_api.TASK_RUNTIME_EVIDENCE_SCHEMA,
                    "freshnessField": "freshness.sourceTick/worldModelAgeMs/clientTickHot age",
                    "capFields": ["script evidence variable catalog"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": False,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "sampleQuery": "get_task_script_runtime_evidence",
                },
                {
                    "sourceName": "task_failure_classification.v1",
                    "sourceType": "read_only_failure_diagnosis",
                    "producer": "task_script_api.py over Knowledge Fabric evidence",
                    "consumer": "Codex before-patching failure classification",
                    "schema": task_script_api.TASK_FAILURE_CLASSIFICATION_SCHEMA,
                    "freshnessField": "source evidence freshness",
                    "capFields": ["evidence sections supplied"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": False,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "sampleQuery": "classify_task_failure",
                },
                {
                    "sourceName": "task_step_readiness.v1",
                    "sourceType": "read_only_script_step_gate",
                    "producer": "Knowledge Fabric + task_script_api.py",
                    "consumer": "Codex before requesting bounded script/operator steps",
                    "schema": task_script_api.TASK_STEP_READINESS_SCHEMA,
                    "freshnessField": "runtime/readiness/action-input/navigation evidence freshness",
                    "capFields": ["compiled step count", "evidence section caps"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": False,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "sampleQuery": "assess_task_script_step",
                },
                {
                    "sourceName": "task_run_readiness.v1",
                    "sourceType": "read_only_script_lifecycle_gate",
                    "producer": "Knowledge Fabric + task_script_api.py",
                    "consumer": "Codex before selecting/requesting the next high-level script primitive",
                    "schema": task_script_api.TASK_RUN_READINESS_SCHEMA,
                    "freshnessField": "runtime/readiness/action-input/navigation evidence freshness",
                    "capFields": ["compiled step count", "inferred primitive", "evidence section caps"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": False,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "sampleQuery": "assess_task_script_run",
                },
                {
                    "sourceName": "session_memory",
                    "sourceType": "session_memory",
                    "producer": "Knowledge Fabric/session observation writers",
                    "consumer": "Codex planning, advisory anchors",
                    "schema": "session_memory.v1",
                    "freshnessField": "sessionPath/lastUpdatedTick",
                    "capFields": ["ring buffer limits"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": False,
                    "canGrowOnDisk": True,
                    "requiresInternet": False,
                    "sampleQuery": "search_session_memory",
                },
                {
                    "sourceName": "static project libraries",
                    "sourceType": "static_library",
                    "producer": "target_library.json, target_profiles.json, service_routes.json",
                    "consumer": "Knowledge Fabric, task probe, script authoring",
                    "schema": "static_knowledge_library.v1",
                    "freshnessField": "versionHash",
                    "capFields": ["query limit"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": False,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "sampleQuery": "search_static_library",
                },
                {
                    "sourceName": "external OSRS knowledge cache",
                    "sourceType": "external_advisory_cache",
                    "producer": "external_knowledge.py explicit refresh/manual seeds",
                    "consumer": "task probe, script authoring, unknown ID/name resolver",
                    "schema": "external_knowledge_sources.v1",
                    "freshnessField": "lastRefresh/cacheAge/fetchedAt",
                    "capFields": ["maxCacheMb", "query limit"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": False,
                    "canGrowOnDisk": True,
                    "requiresInternet": "only for explicit refresh/search",
                    "sampleQuery": "python telemetry-viewer\\context_service.py --external-knowledge-status",
                },
                {
                    "sourceName": "maintenance/disk report",
                    "sourceType": "maintenance",
                    "producer": "maintenance.py",
                    "consumer": "disk cleanup, legacy packet archive audit",
                    "schema": "legacy_live_packets_report.v1",
                    "freshnessField": "generatedAtUtc/report time",
                    "capFields": ["top"],
                    "runtimeCritical": False,
                    "explicitDebugOnly": False,
                    "canGrowOnDisk": False,
                    "requiresInternet": False,
                    "sampleQuery": "python telemetry-viewer\\maintenance.py --live-packets-report",
                },
            ],
            "externalKnowledge": _dict(external_status.get("data")),
            "livePacketArchiveRemoved": True,
            "replacementForLivePackets": {
                "currentState": "get_current_debug_context",
                "worldAndCandidates": "WorldModelCache + Knowledge Fabric queries",
                "currentBlocker": "explain_current_blocker",
                "historicalScenario": "replay_scenario.v1",
                "scriptWriting": "task_script_api.v1 + script_authoring_context.v1",
                "debugEvidence": "visual_debug_bundle",
            },
        }
        return _query_response(DATA_SOURCE_INVENTORY_SCHEMA, data, started=started, source="knowledge_fabric", freshness=self.freshness())

    def query_coverage_matrix(self) -> dict[str, Any]:
        started = time.perf_counter()
        rows = [
            ("What is the player doing?", "get_current_debug_context", "get_current_debug_context", "daemon status/activity/client_tick_hot/phase-aware input integrity", "knowledge_fabric_current_debug_context.v1", "high if daemon fresh", "loaded-scene stale", "test_knowledge_fabric.py"),
            ("Where is the player?", "get_current_debug_context", "osrs://debug/current-context", "plugin baseline/player location", "location summary", "high if source is plugin_snapshot_baseline_player", "proxy fallback confidence lower", "test_context_service.py"),
            ("What is blocking progress?", "explain_current_blocker", "explain_current_blocker", "readiness, trace, world model, pathing", "knowledge_fabric_current_blocker_explanation.v1", "medium-high", "unknown if evidence absent", "test_knowledge_fabric.py"),
            ("What resource targets exist?", "query_resource_candidates", "query_resource_candidates", "resource_object_census + static/external requirements", "knowledge_fabric_resource_candidates.v1", "high if world model fresh", "projection caps", "test_knowledge_fabric.py"),
            ("What service objects exist?", "query_service_candidates", "query_service_candidates", "service_object_census + service_routes", "knowledge_fabric_service_candidates.v1", "high if loaded scene contains service", "static anchors advisory", "test_knowledge_fabric.py"),
            ("What route objects exist?", "query_route_objects", "query_route_objects", "route_object_census", "knowledge_fabric_route_objects.v1", "high if loaded scene fresh", "off-scene objects unavailable", "test_knowledge_fabric.py"),
            ("What pathing frontier exists?", "query_path_frontier", "query_path_frontier", "collision/pathing frontier plus daemon player-location and route-context reconciliation", "knowledge_fabric_path_frontier.v1", "medium-high", "stale frontier may disagree with fresher daemon location", "test_knowledge_fabric.py"),
            ("What camera/view problem exists?", "query_view_quality", "query_view_quality", "view_quality_inputs/projection audit", "knowledge_fabric_view_quality.v1", "medium", "occlusion is heuristic", "test_knowledge_fabric.py"),
            ("What did the navigation decision trace say?", "query_navigation_decision_trace", "query_navigation_decision_trace/osrs://debug/navigation-decision-trace", "latest action_trace navigationDecisionTrace or supplied records", NAVIGATION_DECISION_TRACE_SUMMARY_SCHEMA, "high if trace present", "trace disabled or no latest action trace", "test_knowledge_fabric.py"),
            ("What widgets/dialogue/bank UI are open?", "list_seen_widgets", "list_seen_widgets", "daemon widget/bank/dialogue state", "knowledge_fabric_seen_widgets.v1", "medium", "widget sections may be compact", "test_knowledge_fabric.py"),
            ("What target is executable?", "get_current_debug_context", "get_current_debug_context", "action proposal/readiness/hover", "actionReadiness/actionProposal", "high only after hover evidence", "must not rely on static/external only", "readiness tests"),
            ("What action was actually clicked?", "get_latest_action_trace", "get_latest_action_trace", "action trace/MenuOptionClicked", "knowledge_fabric_latest_action_trace.v1", "high if trace present", "no click trace if skipped", "test_knowledge_fabric.py"),
            ("What did Codex know about the planned click/input?", "get_action_input_visibility", "get_action_input_visibility/osrs://debug/action-input-visibility", "latest action trace or current action proposal plus readiness input geometry, coordinate conversion, HumanInputController, input integrity phase report, input block evidence", ACTION_INPUT_VISIBILITY_SCHEMA, "high for planned point and blocker reason when proposal/readiness/input geometry are present", "derived proposal fields have no executed movement/hover proof until live action trace exists", "test_knowledge_fabric.py"),
            ("What data is stale/capped/missing?", "data-quality-report", "get_data_quality_report", "world model + query perf + disk/external status", "data_quality_report.v1", "high", "requires current context", "test_knowledge_fabric.py"),
            ("What item/object/NPC ID is this?", "external lookup commands", "external_lookup_item_id/external_lookup_object", "external cache/static library", "external_*_lookup.v1", "advisory", "cache miss until refresh", "test_knowledge_fabric.py"),
            ("What wiki/static fact explains this?", "external-search-wiki/external lookup", "external_search_wiki", "external cache/API explicit refresh", "external_wiki_search.v1", "advisory", "internet disabled unless explicit", "test_knowledge_fabric.py"),
            ("What should a future script profile include?", "probe-task/export_task_context_bundle", "probe_task", "scene + static + external cache", "task_probe_report.v1", "medium", "needs loaded scene for best suggestions", "test_knowledge_fabric.py"),
            ("What high-level primitives can a script use?", "task_script_api_spec", "get_task_script_api_spec/osrs://script-api/spec", "task_script_api.py", task_script_api.TASK_SCRIPT_SPEC_SCHEMA, "high", "none", "test_task_script_api.py"),
            ("Is this high-level task script valid?", "validate_task_script", "validate_task_script", "task script JSON", task_script_api.TASK_SCRIPT_VALIDATION_SCHEMA, "high", "raw input fields or unbounded loops", "test_task_script_api.py"),
            ("What existing engine actions will this script use?", "compile_task_script/explain_script_plan", "compile_task_script/explain_script_plan", "task script JSON + task policy", task_script_api.TASK_SCRIPT_PLAN_SCHEMA, "high", "unknown primitive or missing evidence", "test_task_script_api.py"),
            ("Which live variables must prove this script changed state?", "task_script_evidence_plan", "get_task_script_evidence_plan", "task script JSON", task_script_api.TASK_SCRIPT_EVIDENCE_PLAN_SCHEMA, "high", "script does not cover required lifecycle variables", "test_task_script_api.py"),
            ("What are the current live values for script evidence variables?", "query_task_script_runtime_evidence", "get_task_script_runtime_evidence", "daemon/readiness/client_tick/action visibility/proof eligibility", task_script_api.TASK_RUNTIME_EVIDENCE_SCHEMA, "high if loaded scene fresh", "manual login, stale liveness, or advisory-only variables", "test_task_script_api.py"),
            ("Did before/after runtime evidence prove a script step changed state?", "compare_task_script_runtime_evidence", "compare_task_script_runtime_evidence", "two task_runtime_evidence snapshots plus proof eligibility", task_script_api.TASK_RUNTIME_EVIDENCE_COMPARISON_SCHEMA, "high with fresh before/after snapshots", "missing after evidence, advisory-only changes, or input-integrity hard blocker", "test_task_script_api.py"),
            ("How should a failed script/live attempt be classified before patching?", "classify_task_failure", "classify_task_failure/osrs://script-api/failure-classification", "current or supplied blocker/runtime/action evidence", task_script_api.TASK_FAILURE_CLASSIFICATION_SCHEMA, "medium-high with fresh evidence", "needs current evidence bundle", "test_task_script_api.py"),
            ("Is the next high-level script step ready to request?", "assess_task_script_step", "assess_task_script_step/osrs://script-api/step-readiness", "compiled task script + runtime proof eligibility/readiness/action-input/navigation evidence, route context, and path-frontier diagnosis", task_script_api.TASK_STEP_READINESS_SCHEMA, "medium-high with fresh evidence", "manual login, action readiness, input integrity, advisory expected variables, missing trace, or suspicious navigation trace", "test_task_script_api.py"),
            ("What high-level script primitive should be considered next?", "assess_task_script_run", "assess_task_script_run/osrs://script-api/run-readiness", "compiled task script + runtime/readiness/action-input/navigation evidence, route context, and path-frontier diagnosis", task_script_api.TASK_RUN_READINESS_SCHEMA, "medium-high with fresh evidence", "manual login, stale liveness, readiness, input integrity, missing trace, or suspicious navigation trace", "test_task_script_api.py"),
            ("Can the current scene inform a script template?", "probe_task_from_scene", "probe_task_from_scene", "loaded scene + static library + external cache", "task_scene_probe.v1", "medium", "stale loaded scene", "test_task_script_api.py"),
        ]
        data = {
            "rows": [
                {
                    "question": question,
                    "directQuery": direct,
                    "mcpToolOrResource": mcp,
                    "sourceData": source_data,
                    "expectedSchema": schema,
                    "confidence": confidence,
                    "currentGaps": gaps,
                    "testCoverage": tests,
                }
                for question, direct, mcp, source_data, schema, confidence, gaps, tests in rows
            ],
            "defaultFirstQuery": "get_current_debug_context",
            "liveTruthRule": "RuneLite/8893/WorldModel/8890 remain live truth. External knowledge is advisory enrichment only.",
        }
        return _query_response(QUERY_COVERAGE_SCHEMA, data, started=started, source="knowledge_fabric_docs", freshness=self.freshness())

    def query_world_summary(self) -> dict[str, Any]:
        started = time.perf_counter()
        data = {
            "worldModelSummary": _dict(self.world_model_payloads.get("world_model_summary")),
            "liveWorldIndex": self.live_world_index_summary(),
            "staticLibrary": self.static_library.get("summary", {}),
            "sessionMemory": self.session_memory.get("summary", {}),
        }
        return _query_response("knowledge_fabric_world_summary.v1", data, started=started, source=self.source, freshness=self.freshness())

    def query_objects_near(
        self,
        location: dict[str, Any] | None,
        radius: float = 12,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        filters = _dict(filters)
        plane = _int(filters.get("plane") if filters.get("plane") is not None else _dict(location).get("plane"))
        name_contains = _norm(filters.get("nameContains"))
        action_contains = _norm(filters.get("actionContains"))
        only_kinds = {str(item).lower() for item in _list(filters.get("objectKinds"))}
        matches = []
        for obj in self.objects:
            if not _plane_matches(obj, plane):
                continue
            if name_contains and name_contains not in _norm(obj.get("name") or obj.get("targetName") or obj.get("objectName")):
                continue
            if action_contains and action_contains not in _action_text(obj):
                continue
            if only_kinds:
                kind = _norm(obj.get("kind") or obj.get("targetType") or obj.get("objectKind"))
                if kind not in only_kinds:
                    continue
            distance = _distance_tiles(_object_location(obj), location)
            if location is not None and (distance is None or distance > float(radius)):
                continue
            compact = _compact_object(obj)
            compact["distanceToQuery"] = distance
            matches.append(compact)
        matches.sort(key=lambda item: (item.get("distanceToQuery") is None, item.get("distanceToQuery") or 999999))
        capped, cap_hit = _cap_items(matches, limit)
        return _query_response(
            "knowledge_fabric_objects_near.v1",
            {"objects": capped, "count": len(matches), "queryLocation": location, "radiusTiles": radius},
            started=started,
            source=self.source,
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def query_actions(
        self,
        action_contains: str,
        *,
        location: dict[str, Any] | None = None,
        radius: float | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        needle = _norm(action_contains)
        matches = []
        for obj in self.objects:
            if needle and needle not in _action_text(obj):
                continue
            distance = _distance_tiles(_object_location(obj), location)
            if location is not None and radius is not None and (distance is None or distance > float(radius)):
                continue
            compact = _compact_object(obj)
            compact["distanceToQuery"] = distance
            matches.append(compact)
        matches.sort(key=lambda item: (item.get("distanceToQuery") is None, item.get("distanceToQuery") or 999999))
        capped, cap_hit = _cap_items(matches, limit)
        return _query_response(
            "knowledge_fabric_action_query.v1",
            {"objects": capped, "count": len(matches), "actionContains": action_contains},
            started=started,
            source=self.source,
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def query_resource_candidates(
        self,
        profile: str = "woodcutting",
        location: dict[str, Any] | None = None,
        worksite: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        worksite = _dict(worksite)
        anchor = _point(worksite.get("anchor") or worksite.get("worldLocation")) or location
        radius = _float(worksite.get("radiusTiles")) or None
        matches = []
        oak_rejected = 0
        for obj in self.index["_resourceObjects"]:
            distance = _distance_tiles(_object_location(obj), location)
            worksite_distance = _distance_tiles(_object_location(obj), anchor)
            if anchor is not None and radius is not None and (worksite_distance is None or worksite_distance > radius):
                continue
            compact = _compact_object(obj)
            compact["externalKnowledge"] = external_knowledge.enrich_name(str(compact.get("name") or ""))
            compact["distanceToQuery"] = distance
            compact["worksiteDistanceTiles"] = worksite_distance
            action_status = _resource_live_action_status(obj)
            compact["liveResourceActionStatus"] = action_status
            level_status = world_model_core.target_level_status(obj)
            compact.update({k: v for k, v in level_status.items() if v is not None})
            if action_status.get("blockedByLiveAction") is True:
                compact["executable"] = False
                compact["actionability"] = "blocked_no_matching_action"
                compact["candidateRole"] = "rejected"
                compact["rejectionReason"] = ",".join(action_status.get("rejectionReasons") or ["no_matching_live_resource_action"])
            elif compact.get("visibleButNotExecutable") is True:
                compact["executable"] = False
                compact["rejectionReason"] = compact.get("targetTemporarilyLockedReason") or "insufficient_level"
                if "oak" in _norm(compact.get("name")):
                    oak_rejected += 1
            else:
                projection = _projection(obj)
                compact["executable"] = projection.get("actionableByCanvas") is True
            score = 0.0
            if compact["executable"]:
                score += 50.0
            if "tree" in _norm(compact.get("name")) and "oak" not in _norm(compact.get("name")):
                score += 12.0
            if action_status.get("hasMatchingLiveResourceAction") is True:
                score += 18.0
            if action_status.get("blockedByLiveAction") is True:
                score -= 120.0
            if worksite_distance is not None:
                score -= worksite_distance
            edge = _float(_projection(obj).get("edgeDistancePx"))
            if edge is not None:
                score += min(edge, 80.0) / 10.0
            if compact.get("visibleButNotExecutable") is True:
                score -= 100.0
            compact["score"] = round(score, 3)
            matches.append(compact)
        matches.sort(key=lambda item: item.get("score", 0), reverse=True)
        capped, cap_hit = _cap_items(matches, limit)
        return _query_response(
            "knowledge_fabric_resource_candidates.v1",
            {
                "profile": profile,
                "objects": capped,
                "count": len(matches),
                "oakRejectedInsufficientLevelCount": oak_rejected,
                "worksite": worksite or None,
            },
            started=started,
            source=self.source,
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def query_service_candidates(
        self,
        service_type: str = "bank",
        route_context: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        service_type_norm = _norm(service_type)
        matches = []
        for obj in self.index["_serviceObjects"]:
            text = " ".join((_norm(obj.get("serviceObjectType")), _norm(obj.get("name")), _action_text(obj)))
            service_matches = service_type_norm in text or service_type_norm == "any"
            if service_type_norm == "bank" and "deposit" in text:
                service_matches = True
            if service_type_norm and not service_matches:
                continue
            compact = _compact_object(obj)
            compact["sourceConfidence"] = "live_loaded_scene"
            matches.append(compact)
        for anchor in _list(self.session_memory.get("observedServiceAnchors")):
            if not isinstance(anchor, dict):
                continue
            matches.append({
                "name": anchor.get("targetName") or anchor.get("name") or anchor.get("label"),
                "worldLocation": _object_location(anchor),
                "sourceConfidence": "session_observed_anchor",
                "advisoryOnly": True,
                "source": "session_memory",
            })
        for anchor in _list(self.static_library.get("serviceAnchors")):
            matches.append({
                "name": anchor.get("label"),
                "worldLocation": anchor.get("worldLocation"),
                "sourceConfidence": "static_advisory_anchor",
                "advisoryOnly": True,
                "source": "static_library",
                "routeId": anchor.get("routeId"),
            })
        capped, cap_hit = _cap_items(matches, limit)
        return _query_response(
            "knowledge_fabric_service_candidates.v1",
            {"serviceType": service_type, "routeContext": route_context or {}, "objects": capped, "count": len(matches)},
            started=started,
            source=self.source,
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def query_route_objects(self, route_context: dict[str, Any] | None = None, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        matches = [_compact_object(obj) for obj in self.index["_routeObjects"]]
        for obj in matches:
            obj["staticPriorExecutable"] = False
            obj["executionRule"] = "live object still requires projection and hover confirmation"
        capped, cap_hit = _cap_items(matches, limit)
        return _query_response(
            "knowledge_fabric_route_objects.v1",
            {"routeContext": route_context or {}, "objects": capped, "count": len(matches)},
            started=started,
            source=self.source,
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def query_path_frontier(self, goal: dict[str, Any] | None = None, constraints: dict[str, Any] | None = None, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        frontier = dict(_dict(self.world_model_payloads.get("pathing_frontier")))
        candidates = _list(frontier.get("frontier") or frontier.get("candidates") or frontier.get("items"))
        capped_candidates, cap_hit = _cap_items([dict(item) for item in candidates if isinstance(item, dict)], limit)
        if candidates:
            frontier["frontier"] = capped_candidates
        route_context = _compact_route_context(self.daemon_status)
        player_location = _compact_location(self.daemon_status)
        latest_bundle = self._latest_visual_bundle_summary()
        wall_reasons = []
        if route_context.get("wallLoopDetected") or route_context.get("wallHuggingDetected"):
            wall_reasons.append("daemon_status_wall_hugging")
        if "wall_hugging" in _norm(latest_bundle.get("reason")) or "wall_hugging" in _norm(latest_bundle.get("classification")):
            wall_reasons.append("latest_debug_bundle_wall_hugging")
        nested_frontier = _dict(frontier.get("frontier"))
        nested_reason = nested_frontier.get("reason")
        nested_status = nested_frontier.get("status") or frontier.get("status")
        frontier_candidates = _list(nested_frontier.get("candidates")) if nested_frontier else capped_candidates
        stale_frontier_player_location = bool(
            str(nested_reason or "").lower() == "player_location_unavailable"
            and player_location.get("worldLocation")
        )
        frontier_warning_reasons = []
        if stale_frontier_player_location:
            frontier_warning_reasons.append("frontier_player_location_unavailable_but_daemon_location_present")
        if nested_status and str(nested_status).upper() not in {"PASS", "OK"}:
            frontier_warning_reasons.append(f"frontier_status_{str(nested_status).lower()}")
        status_pathing = {
            "currentRouteNode": route_context.get("currentNodeId"),
            "currentRouteEdge": route_context.get("nextEdgeType"),
            "routeStepIndex": route_context.get("currentStepIndex"),
            "nextWaypointTile": route_context.get("nextWaypointTile"),
            "pathTargetTile": route_context.get("pathTargetTile"),
            "destinationTile": route_context.get("destinationTile"),
            "predictedPathTiles": route_context.get("predictedPathTiles"),
            "rejectedApproachTileReasons": route_context.get("rejectedApproachTileReasons"),
        }
        data = {
            "goal": goal or route_context.get("destinationTile") or {},
            "constraints": constraints or {},
            "frontier": frontier,
            "playerLocation": player_location,
            "frontierDiagnosis": {
                "schema": "path_frontier_diagnosis.v1",
                "frontierStatus": nested_status,
                "frontierReason": nested_reason,
                "frontierCandidateCount": len(frontier_candidates),
                "playerLocationAvailable": bool(player_location.get("worldLocation")),
                "playerLocationSource": player_location.get("source"),
                "staleFrontierPlayerLocation": stale_frontier_player_location,
                "routeContextHasPredictedPath": bool(route_context.get("predictedPathTiles")),
                "routeContextCanGuideDiagnosis": bool(
                    route_context.get("nextWaypointTile")
                    or route_context.get("pathTargetTile")
                    or route_context.get("destinationTile")
                ),
                "frontierUsableForNavigation": bool(frontier_candidates and not frontier_warning_reasons),
                "diagnosticOnly": True,
                "noGlobalPathfindingAdded": True,
            },
            "routeContext": route_context,
            "statusPathing": status_pathing,
            "wallHuggingRisk": {
                "status": "WARN" if wall_reasons else "PASS",
                "score": 0.85 if wall_reasons else 0.0,
                "reasons": wall_reasons,
                "latestDebugBundle": latest_bundle,
            },
            "routeObjectSummary": {
                "visible": self.daemon_status.get("serviceRouteObjectsVisible"),
                "actionable": self.daemon_status.get("serviceRouteObjectsActionable"),
                "selectedObjectPresent": self.daemon_status.get("serviceRouteSelectedObjectPresent"),
                "selectedObjectAction": self.daemon_status.get("serviceRouteSelectedObjectAction"),
                "rejectedReason": self.daemon_status.get("serviceRouteObjectRejectedReason"),
            },
            "frontierSource": "world_model_collision" if frontier else "daemon_status_pathing_context",
        }
        return _query_response(
            "knowledge_fabric_path_frontier.v1",
            data,
            started=started,
            source=self.source,
            freshness=self.freshness(),
            cap_hit=cap_hit or bool(frontier.get("capHit") or frontier.get("truncated")),
            truncated=cap_hit,
            status="PASS" if frontier and not frontier_warning_reasons else "WARN",
            warnings=frontier_warning_reasons if frontier else ["pathing frontier unavailable"],
        )

    def query_view_quality(self, intent: str = "unknown", goal: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        view = dict(_dict(self.world_model_payloads.get("view_quality_inputs")))
        resources = self.query_resource_candidates(limit=10)["data"]
        service_objects = _list(_dict(self.query_service_candidates(limit=20).get("data")).get("objects"))
        projection = _dict(self.world_model_payloads.get("projection_audit"))
        world_summary = _dict(self.world_model_payloads.get("world_model_summary"))
        metadata = _dict(world_summary.get("metadata"))
        overlay_summary = _dict(self.daemon_status.get("overlaySummary") or self.daemon_status.get("intentOverlaySummary"))
        camera_reasons = []
        if self.daemon_status.get("cameraReacquireRecommended") is True:
            camera_reasons.append("daemon_camera_reacquire_recommended")
        if overlay_summary.get("edgeClippedCandidates"):
            camera_reasons.append("edge_clipped_candidates")
        if projection.get("projectionCapHit") is True:
            camera_reasons.append("projection_cap_hit")
        live_service_objects = [obj for obj in service_objects if _dict(obj).get("advisoryOnly") is not True]
        service_actionable = [
            obj for obj in live_service_objects
            if _dict(obj.get("serviceTargetExposure")).get("usableExposureThresholdMet") is True
        ]
        service_offscreen = [
            obj for obj in live_service_objects
            if str(_dict(obj).get("projectionClassification") or "") == "offscreen"
            or _dict(_dict(obj).get("projection")).get("onScreen") is False
        ]
        service_edge = [
            obj for obj in live_service_objects
            if str(_dict(obj).get("projectionClassification") or "") == "edge_clipped"
            or _dict(obj.get("serviceTargetExposure")).get("edgeSliverVisible") is True
            or (
                _dict(obj.get("serviceTargetExposure")).get("edgeDistancePx") is not None
                and (_float(_dict(obj.get("serviceTargetExposure")).get("edgeDistancePx")) or 0.0) < SERVICE_MIN_EDGE_DISTANCE_PX
            )
        ]
        service_visible_not_usable = [
            obj for obj in live_service_objects
            if _dict(obj.get("serviceTargetExposure")).get("safeClickAvailable") is True
            and _dict(obj.get("serviceTargetExposure")).get("usableExposureThresholdMet") is not True
        ]
        selected_service = (
            service_actionable[0]
            if service_actionable
            else service_visible_not_usable[0]
            if service_visible_not_usable
            else service_edge[0]
            if service_edge
            else service_offscreen[0]
            if service_offscreen
            else live_service_objects[0]
            if live_service_objects
            else {}
        )
        target_view_state = _dict(_dict(selected_service).get("targetViewState")) or _dict(
            _dict(_dict(selected_service).get("serviceTargetExposure")).get("targetViewState")
        )
        if selected_service and not target_view_state:
            viewport = dict(_dict(metadata.get("viewport")))
            if metadata.get("cameraYaw") is not None and viewport.get("cameraYaw") is None:
                viewport["cameraYaw"] = metadata.get("cameraYaw")
            if metadata.get("cameraPitch") is not None and viewport.get("cameraPitch") is None:
                viewport["cameraPitch"] = metadata.get("cameraPitch")
            player_location = (
                _point(self.daemon_status.get("playerLocation"))
                or _point(_dict(self.daemon_status.get("location")).get("worldLocation"))
                or _point(_dict(self.daemon_status.get("worldLocation")))
            )
            target_view_state = target_view_core.build_target_view_state(
                selected_service,
                target_kind="service_object",
                player_location=player_location,
                expected_action="Bank",
                target_source="live_world_model",
                target_route_relevant=True,
                target_action_relevant=True,
                safe_aimpoint=_dict(selected_service.get("safeAimPoint")),
                viewport=viewport,
                source_canvas_size=viewport,
                status=self.daemon_status,
            )
        service_recovery_recommended = bool(live_service_objects and not service_actionable and (service_offscreen or service_edge or service_visible_not_usable))
        if service_recovery_recommended:
            camera_reasons.append("service_object_loaded_offscreen")
        if service_actionable:
            service_view_classification = "good_service_view"
        elif any(_dict(obj.get("serviceTargetExposure")).get("edgeSliverVisible") is True for obj in service_edge):
            service_view_classification = "service_object_edge_sliver"
        elif service_visible_not_usable:
            service_view_classification = "service_object_visible_but_not_usable"
        elif service_offscreen:
            service_view_classification = "service_object_loaded_offscreen"
        elif service_edge:
            service_view_classification = "service_object_edge_clipped"
        elif live_service_objects:
            service_view_classification = "poor_service_projection"
        else:
            service_view_classification = "service_object_not_loaded"
        data = {
            "intent": intent,
            "goal": goal or {},
            "camera": {
                "yaw": _first_present(metadata.get("cameraYaw"), self.daemon_status.get("cameraYaw")),
                "pitch": _first_present(metadata.get("cameraPitch"), self.daemon_status.get("cameraPitch")),
            },
            "currentViewGoal": intent,
            "viewQualityInputs": view,
            "projectionAudit": projection,
            "routeWaypointVisibility": {
                "navigationWaypointRequired": self.daemon_status.get("navigationWaypointRequired"),
                "nextWaypointTile": self.daemon_status.get("pathingNextWaypointTile"),
                "pathingReason": self.daemon_status.get("pathingReason"),
                "edgeRouteClicksRejected": self.daemon_status.get("edgeRouteClicksRejected"),
            },
            "routeObjectVisibility": {
                "visible": self.daemon_status.get("serviceRouteObjectsVisible"),
                "actionable": self.daemon_status.get("serviceRouteObjectsActionable"),
                "selectedObjectPresent": self.daemon_status.get("serviceRouteSelectedObjectPresent"),
            },
            "serviceObjectVisibility": {
                "visible": self.daemon_status.get("serviceObjectsVisible"),
                "actionable": self.daemon_status.get("serviceObjectsActionable"),
                "candidateCount": self.daemon_status.get("serviceCandidateCount"),
                "loadedSceneCount": len(live_service_objects),
                "actionableByCanvasCount": len(service_actionable),
                "offscreenCount": len(service_offscreen),
                "edgeClippedCount": len(service_edge),
                "visibleButNotUsableCount": len(service_visible_not_usable),
                "selectedServiceObject": selected_service or None,
            },
            "serviceViewScore": {
                "schema": "service_view_score.v1",
                "loadedServiceObjects": len(live_service_objects),
                "actionableServiceObjects": len(service_actionable),
                "offscreenServiceObjects": len(service_offscreen),
                "edgeClippedServiceObjects": len(service_edge),
                "visibleButNotUsableServiceObjects": len(service_visible_not_usable),
                "score": (
                    100
                    if service_actionable
                    else max(
                        [
                            int(_dict(obj.get("serviceTargetExposure")).get("usableExposureScore") or 0)
                            for obj in live_service_objects
                        ]
                        or [0]
                    )
                    if live_service_objects
                    else 0
                ),
            },
            "serviceViewClassification": service_view_classification,
            "recommendedServiceCameraAction": "camera_reacquire_service_target" if service_recovery_recommended else None,
            "serviceObjectProjectionStatus": _dict(selected_service).get("projectionClassification") if selected_service else None,
            "serviceObjectHoverStatus": "requires_hover_confirmation" if service_actionable else "not_hover_ready",
            "serviceObjectVisibleArea": _dict(_dict(selected_service).get("serviceTargetExposure")).get("visibleAreaPx") if selected_service else None,
            "serviceObjectEdgeDistance": _dict(_dict(selected_service).get("serviceTargetExposure")).get("edgeDistancePx") if selected_service else None,
            "serviceObjectCentrality": _dict(_dict(selected_service).get("serviceTargetExposure")).get("centralityScore") if selected_service else None,
            "serviceViewRecoveryAvailable": bool(live_service_objects),
            "serviceViewRecoveryRecommendedReason": (
                "service_object_visible_but_not_usable"
                if service_visible_not_usable
                else "service_object_loaded_but_not_actionable"
                if service_recovery_recommended
                else None
            ),
            "targetViewScore": target_view_state.get("usableExposureScore"),
            "targetViewClassification": target_view_state.get("viewQualityClassification") or (
                "needs_target_camera_recovery" if service_recovery_recommended else "target_not_loaded"
            ),
            "targetKind": target_view_state.get("targetKind") or ("service_object" if selected_service else None),
            "targetViewPolicy": target_view_state.get("targetViewPolicy"),
            "recommendedCameraAction": "camera_reacquire_target" if service_recovery_recommended else None,
            "targetVisibility": {
                "onScreen": target_view_state.get("currentlyOnScreen"),
                "offscreen": target_view_state.get("currentlyOffscreen"),
                "edgeSliver": target_view_state.get("edgeSliverVisible"),
                "usableExposure": target_view_state.get("usableExposureThresholdMet"),
            } if target_view_state else {},
            "targetProjectionStatus": target_view_state.get("currentProjectionStatus"),
            "targetHoverStatus": target_view_state.get("hoverTopOption") or ("requires_hover_confirmation" if service_actionable else "not_hover_ready"),
            "targetVisibleArea": target_view_state.get("visibleAreaPx"),
            "targetEdgeDistance": target_view_state.get("edgeDistancePx"),
            "targetCentrality": target_view_state.get("centralityScore"),
            "targetBearing": target_view_state.get("targetBearing"),
            "targetYawError": target_view_state.get("yawErrorToTarget"),
            "cameraResponseCalibration": target_view_state.get("cameraResponseCalibration") or target_view_core.camera_response_calibration_from_status(self.daemon_status),
            "targetViewRecoveryAvailable": bool(live_service_objects),
            "targetViewRecoveryRecommendedReason": (
                target_view_state.get("cameraExposureReason")
                if target_view_state.get("shouldAttemptCameraExposure") is True
                else None
            ),
            "resourceCandidateSummary": {
                "count": resources.get("count"),
                "top": resources.get("objects", [])[:3],
            },
            "visibilityCounts": {
                "safeAimpoints": self.daemon_status.get("safeAimpoints") or overlay_summary.get("safeAimpoints"),
                "edgeClippedCandidates": self.daemon_status.get("edgeClippedCandidates") or overlay_summary.get("edgeClippedCandidates"),
                "offscreenCandidates": self.daemon_status.get("offscreenCandidates") or overlay_summary.get("offscreenCandidates"),
                "occludedCandidates": self.daemon_status.get("occludedCandidates") or overlay_summary.get("occludedCandidates"),
            },
            "cameraRecommendation": {
                "recommended": bool(camera_reasons),
                "reasons": camera_reasons,
                "minimapFallbackAvailable": self.daemon_status.get("minimapProjectionAvailable"),
                "minimapFallbackDeferred": self.daemon_status.get("minimapFallbackDeferred"),
            },
            "latestVisualBundle": self._latest_visual_bundle_summary(),
        }
        return _query_response(
            "knowledge_fabric_view_quality.v1",
            data,
            started=started,
            source=self.source,
            freshness=self.freshness(),
            status="PASS" if view or projection or resources.get("count") else "WARN",
            warnings=[] if view or projection or resources.get("count") else ["view quality inputs unavailable"],
        )

    def query_worksite_context(self, profile: str = "woodcutting") -> dict[str, Any]:
        started = time.perf_counter()
        resource_areas = _list(self.session_memory.get("observedResourceAreas"))
        resource_query = self.query_resource_candidates(profile=profile, limit=25)
        objects = _list(_dict(resource_query.get("data")).get("objects"))
        locations = [_point(obj.get("worldLocation")) for obj in objects if _point(obj.get("worldLocation"))]
        centroid = None
        if locations:
            centroid = {
                "worldX": round(sum(float(item["worldX"]) for item in locations) / len(locations), 2),
                "worldY": round(sum(float(item["worldY"]) for item in locations) / len(locations), 2),
                "plane": locations[0].get("plane"),
            }
        return _query_response(
            "knowledge_fabric_worksite_context.v1",
            {
                "profile": profile,
                "observedResourceAreas": resource_areas[:10],
                "visibleResourceCandidateCount": len(objects),
                "candidateClusterCentroid": centroid,
                "worksiteLeashRule": "prefer live basic Tree/Dead tree inside session/static worksite; static anchors are advisory",
            },
            started=started,
            source=self.source,
            freshness=self.freshness(),
        )

    def query_session_memory(self, kind: str | None = None, filters: dict[str, Any] | None = None, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        filters = _dict(filters)
        items: list[dict[str, Any]] = []
        keys = [
            "observedResourceAreas",
            "observedServiceAnchors",
            "observedRouteObjects",
            "successfulWaypoints",
            "failedWaypoints",
            "menuFlipZones",
            "cameraViewOutcomes",
            "learnedAreaLabels",
        ]
        if kind:
            normalized = _norm(kind)
            keys = [key for key in keys if normalized in key.lower()]
        for key in keys:
            for item in _list(self.session_memory.get(key)):
                if isinstance(item, dict):
                    payload = dict(item)
                    payload["_memoryKind"] = key
                    items.append(payload)
        contains = _norm(filters.get("contains"))
        if contains:
            items = [item for item in items if contains in json.dumps(item, default=str).lower()]
        capped, cap_hit = _cap_items(items, limit)
        return _query_response(
            "knowledge_fabric_session_memory_query.v1",
            {
                "sessionMemory": {k: self.session_memory.get(k) for k in ("schema", "sessionPath", "lastUpdatedTick", "summary")},
                "items": capped,
                "count": len(items),
                "canUseForExecution": False,
                "executionRule": "session memory is advisory until a live target verifies it",
            },
            started=started,
            source="session_memory",
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def query_debug_evidence(self, reason: str | None = None, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        bundles = [item for item in _list(self.debug_evidence.get("visualBundles")) if isinstance(item, dict)]
        if reason:
            needle = _norm(reason)
            bundles = [item for item in bundles if needle in _norm(item.get("reason")) or needle in _norm(item.get("classification"))]
        capped, cap_hit = _cap_items(bundles, limit)
        data = dict(self.debug_evidence)
        data["visualBundles"] = capped
        data["visualBundleCount"] = len(bundles)
        return _query_response(
            "knowledge_fabric_debug_evidence_query.v1",
            data,
            started=started,
            source="debug_bundles",
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def query_navigation_decision_trace(
        self,
        *,
        records: list[dict[str, Any]] | None = None,
        action_trace: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        cap = _safe_limit(limit)
        source = "latest_action_trace_index"
        if records is not None:
            trace_records = [dict(item) for item in records if isinstance(item, dict)]
            source = "supplied_records"
        elif action_trace is not None:
            trace_records = _navigation_trace_records_from_trace(action_trace)
            source = "supplied_action_trace"
        else:
            trace_records = _navigation_trace_records_from_debug(self.debug_evidence)
            if trace_records:
                source = "latest_action_trace_index"
            else:
                trace_records = _navigation_trace_records_from_session_files(self.session_path, limit=cap)
                if trace_records:
                    source = "session_navigation_trace_jsonl"
        summary = _navigation_trace_summary(trace_records, limit=cap)
        data = {
            **summary,
            "tracePresent": bool(trace_records),
            "source": source,
            "diagnosticOnly": source == "session_navigation_trace_jsonl",
            "blockingEligible": source != "session_navigation_trace_jsonl",
            "sessionPath": self.debug_evidence.get("sessionPath"),
            "latestActionTraceCount": len(_list(self.debug_evidence.get("latestActionTraces"))),
            "routeContext": _compact_route_context(self.daemon_status),
            "pathingFrontier": self.query_path_frontier(limit=5).get("data"),
            "diagnosisRules": [
                "missing_reason_string",
                "click_or_recover_for_non_executable_subgoal",
                "click_or_recover_while_previous_result_pending",
                "click_or_recover_while_distance_improving",
                "stale_state_allowed_click",
                "blocked_recovery_without_block_evidence",
                "repeated_short_click",
            ],
            "noLiveInput": True,
        }
        warnings = []
        if not trace_records:
            warnings.append("navigation_decision_trace_missing")
        if summary.get("firstSuspiciousDecision"):
            warnings.append("suspicious_navigation_decision_detected")
        return _query_response(
            NAVIGATION_DECISION_TRACE_SUMMARY_SCHEMA,
            data,
            started=started,
            source=source,
            freshness=self.freshness(),
            warnings=warnings,
            status="WARN" if warnings else "PASS",
        )

    def _latest_visual_bundle_summary(self) -> dict[str, Any]:
        bundles = [item for item in _list(self.debug_evidence.get("visualBundles")) if isinstance(item, dict)]
        return dict(bundles[0]) if bundles else {}

    def _latest_action_trace_summary(self) -> dict[str, Any]:
        traces = [item for item in _list(self.debug_evidence.get("latestActionTraces")) if isinstance(item, dict)]
        return dict(traces[0]) if traces else {}

    def query_action_input_visibility(self) -> dict[str, Any]:
        started = time.perf_counter()
        readiness = self._readiness_report()
        proposal = self._action_proposal()
        latest_trace = self._latest_action_trace_summary()
        latest_bundle = self._latest_visual_bundle_summary()
        status = self.daemon_status
        hot = _dict(status.get("clientTickHot"))
        route_context = _compact_route_context(status)
        visibility = _dict(latest_trace.get("visibility"))
        bootstrap = self._bootstrap_liveness_summary()
        current_input_integrity = self._input_integrity_summary()
        planned_target = visibility.get("plannedTarget") or _dict(proposal.get("targetExplanation"))
        derived_point = (
            _derive_planned_point_visibility(planned_target, readiness=readiness, status=status)
            if planned_target
            and (
                visibility.get("plannedScreenPoint") is None
                or visibility.get("coordinateConversionTrace") is None
                or visibility.get("displayScaleApplied") is None
            )
            else {}
        )
        planned_screen_point = _first_present(visibility.get("plannedScreenPoint"), derived_point.get("plannedScreenPoint"))
        coordinate_trace = _first_present(visibility.get("coordinateConversionTrace"), derived_point.get("coordinateConversionTrace"))
        input_block_evidence = _readiness_block_evidence(readiness, current_input_integrity)
        arduino_calibration_status = visibility.get("arduinoCalibrationStatus") or _derive_arduino_calibration_status(
            readiness,
            current_input_integrity,
            planned_screen_point=planned_screen_point,
            coordinate_trace=coordinate_trace,
        )
        human_input_controller = visibility.get("humanInputController") or _derive_human_input_controller_visibility(
            readiness,
            current_input_integrity,
        )
        cursor_movement_trace = visibility.get("cursorMovementTrace") or _derive_cursor_movement_trace(
            planned_screen_point=planned_screen_point,
            coordinate_trace=coordinate_trace,
        )
        input_integrity_status = visibility.get("input_integrity_status") or _phase_aware_input_integrity_status(current_input_integrity)
        data = {
            "latestActionTrace": visibility or latest_trace,
            "latestActionTraceSummary": latest_trace,
            "latestDebugBundle": latest_bundle,
            "readiness": readiness,
            "actionReadiness": readiness.get("actionReadiness") if isinstance(readiness.get("actionReadiness"), dict) else {},
            "plannedAction": visibility.get("plannedAction") or proposal.get("proposedAction"),
            "plannedTarget": planned_target,
            "plannedScreenPoint": planned_screen_point,
            "coordinateConversionTrace": coordinate_trace,
            "displayScaleApplied": _first_present(visibility.get("displayScaleApplied"), derived_point.get("displayScaleApplied")),
            "displayScaleReason": _first_present(visibility.get("displayScaleReason"), derived_point.get("displayScaleReason")),
            "arduinoCalibrationStatus": arduino_calibration_status,
            "humanInputController": human_input_controller,
            "cursorMovementTrace": cursor_movement_trace,
            "hoverConfirmationEvidence": visibility.get("hoverConfirmationEvidence") or {
                "hoverMenu": hot.get("hoverMenu") or hot.get("postMenuSort"),
            },
            "menuOptionClickedEvidence": visibility.get("menuOptionClickedEvidence") or hot.get("lastMenuOptionClicked"),
            "input_integrity_status": input_integrity_status,
            "directBackendBypassCount": _first_present(
                visibility.get("directBackendBypassCount"),
                _dict(current_input_integrity).get("directBackendBypassCount"),
            ),
            "lastClickProof": visibility.get("lastClickProof"),
            "lastMovementProof": _dict(cursor_movement_trace).get("lastMovementProof"),
            "blockedReason": visibility.get("blockedReason") or input_block_evidence.get("blockedReason"),
            "inputBlockEvidence": input_block_evidence,
            "livenessRecoveryActions": {
                "recommended": bool(bootstrap.get("livenessRecoveryRecommended")),
                "available": bool(bootstrap.get("livenessRecoveryAvailable")),
                "lastResult": status.get("livenessRecoveryLastResult"),
            },
            "boundedWatcherDecisions": status.get("boundedWatcherDecisions") or status.get("watcherDecisions"),
            "navigationDecisionTrace": self.query_navigation_decision_trace(limit=10),
            "target_view_state": visibility.get("target_view_state") or _dict(_dict(proposal.get("targetExplanation")).get("targetViewState")),
            "target_view_state_source": "latest_action_trace" if visibility.get("target_view_state") else "current_action_proposal",
            "serviceResourceRouteCandidateState": {
                "resourceCandidates": self.query_resource_candidates(limit=5).get("data"),
                "serviceCandidates": self.query_service_candidates(limit=5).get("data"),
                "routeObjects": self.query_route_objects(limit=5).get("data"),
                "routeContext": route_context,
            },
            "readinessActionEvidence": readiness,
            "canonicalPipeline": [
                "action proposal",
                "readiness",
                "hover/menu proof",
                "HumanInputController",
                "ArduinoHIDBackend",
                "input integrity",
                "lifecycle verification",
            ],
            "rawInputBypassToolsExposed": False,
            "externalKnowledgePolicy": {
                "advisoryOnly": True,
                "liveTruth": "RuneLite / 8893 / WorldModel / 8890",
                "hotExecutorExternalCallsAllowed": False,
                "cacheFirst": True,
                "source": _external_summary_compact(),
            },
        }
        return _query_response(
            ACTION_INPUT_VISIBILITY_SCHEMA,
            data,
            started=started,
            source="knowledge_fabric_debug_evidence",
            freshness=self.freshness(),
            status="PASS" if data.get("plannedAction") or latest_trace else "WARN",
        )

    def _status_with_world_model_context(self) -> dict[str, Any]:
        status = dict(self.daemon_status)
        if self.world_model_payloads and not status.get("worldModelPayloads"):
            status["worldModelPayloads"] = self.world_model_payloads
        summary = _world_model_summary_payload(self.world_model_payloads, status)
        if summary and not status.get("worldModelSummary"):
            status["worldModelSummary"] = summary
        object_total = _world_model_object_total(self.world_model_payloads, status)
        if object_total is not None and status.get("worldModelObjectTotal") is None:
            status["worldModelObjectTotal"] = object_total
        if self.world_model_payloads:
            for key, value in world_model_core.status_fields(self.world_model_payloads).items():
                if value is not None and status.get(key) is None:
                    status[key] = value
        return status

    def _readiness_report(self) -> dict[str, Any]:
        try:
            import live_readiness_core

            report = live_readiness_core.build_readiness_report(daemon_status=self._status_with_world_model_context())
            return report if isinstance(report, dict) else {}
        except Exception as error:  # noqa: BLE001
            return {"schema": "live_readiness_unavailable.v1", "status": "WARN", "error": f"{type(error).__name__}: {error}"}

    def _action_proposal(self) -> dict[str, Any]:
        try:
            from input_control.action_proposal import build_action_proposal

            status = self._status_with_world_model_context()
            proposal = build_action_proposal(status)
            payload = proposal.to_dict() if hasattr(proposal, "to_dict") else {}
            return payload if isinstance(payload, dict) else {}
        except Exception as error:  # noqa: BLE001
            return {"schema": "action_proposal_unavailable.v1", "status": "WARN", "error": f"{type(error).__name__}: {error}"}

    def _input_integrity_summary(self) -> dict[str, Any]:
        live_input = _dict(self.daemon_status.get("liveInput"))
        monitor = _dict(live_input.get("monitor") or self.daemon_status.get("inputIntegrityStatus"))
        if not monitor and self.session_path is not None:
            path = self.session_path / LIVE_DIR / "input_integrity_status.json"
            if path.exists():
                monitor = _read_json(path)
        if not monitor:
            local_path = Path("interaction_geometry") / "live" / "input_integrity_status.json"
            if local_path.exists():
                monitor = _read_json(local_path)
        flags = _dict(monitor.get("injectionFlags"))
        backend = _dict(monitor.get("backend"))
        return {
            "status": monitor.get("status"),
            "monitorPass": monitor.get("monitorPass"),
            "monitorAvailable": monitor.get("monitorAvailable"),
            "expectedVidPidMatched": monitor.get("expectedVidPidMatched"),
            "injectedEvents": monitor.get("injectedEvents")
            if monitor.get("injectedEvents") is not None
            else (_int(flags.get("mouseInjectedCount")) or 0) + (_int(flags.get("keyboardInjectedCount")) or 0),
            "lowerIlInjectedEvents": monitor.get("lowerIlInjectedEvents")
            if monitor.get("lowerIlInjectedEvents") is not None
            else (_int(flags.get("mouseLowerIlInjectedCount")) or 0) + (_int(flags.get("keyboardLowerIlInjectedCount")) or 0),
            "directBackendBypassCount": _first_present(
                backend.get("directBackendBypassCount"),
                self.daemon_status.get("directBackendBypassCount"),
            ),
            "liveInputBackend": backend.get("liveInputBackend"),
            "liveInputBackendRequired": backend.get("liveInputBackendRequired"),
            "arduinoBackendSelected": backend.get("arduinoBackendSelected"),
            "arduinoArmed": backend.get("arduinoArmed"),
            "lastArduinoEventAgeMs": monitor.get("lastArduinoEventAgeMs"),
            "arduinoDetected": monitor.get("arduinoDetected"),
            "arduinoActivity": monitor.get("arduinoActivity"),
            "warnings": _list(monitor.get("warnings")),
            "blockers": _list(monitor.get("blockers")),
        }

    def _bootstrap_liveness_summary(self) -> dict[str, Any]:
        status_with_world = self._status_with_world_model_context()
        hot = _dict(status_with_world.get("clientTickHot"))
        game_state = _first_present(hot.get("gameState"), status_with_world.get("gameState"))
        latest_tick = _first_present(status_with_world.get("latestTick"), hot.get("gameTickAtSample"))
        quality = world_model_core.world_model_quality(self.world_model_payloads)
        world_object_total = _world_model_object_total(self.world_model_payloads, status_with_world)
        liveness_object_count = _first_present(world_object_total, len(self.objects))
        local_object_count = len(self.objects)
        client_tick_fresh = _client_tick_fresh(status_with_world)
        world_fresh = quality.get("worldModelAvailable") is True
        loaded_scene_verified = bool(
            str(game_state or "").upper() == "LOGGED_IN"
            and latest_tick is not None
            and world_fresh
            and _int(liveness_object_count) is not None
            and (_int(liveness_object_count) or 0) > 0
            and client_tick_fresh
        )
        stale_logged_in_no_scene = bool(str(game_state or "").upper() == "LOGGED_IN" and not loaded_scene_verified)
        if loaded_scene_verified:
            state = "loaded_scene"
        elif str(game_state or "").upper() == "LOGIN_SCREEN":
            state = "login_screen"
        elif stale_logged_in_no_scene:
            state = "stale_logged_in_no_scene"
        elif self.daemon_status:
            state = "plugin_endpoint_down" if not world_fresh else "loading"
        else:
            state = "unknown"
        try:
            import liveness_recovery_core

            recovery_hint = liveness_recovery_core.liveness_hint_from_daemon_status(
                {
                    **status_with_world,
                    "worldModelSummary": _world_model_summary_payload(self.world_model_payloads, status_with_world),
                    "worldModelObjectTotal": world_object_total,
                }
            )
        except Exception:  # noqa: BLE001
            recovery_hint = {
                "livenessRecoveryAvailable": False,
                "livenessRecoveryRecommended": not loaded_scene_verified,
                "livenessState": state,
                "knownRecoverableState": state in {"login_screen", "stale_logged_in_no_scene", "plugin_endpoint_down", "loading"},
                "manualLoginRequired": state == "login_screen",
                "unknownScreen": state == "unknown",
                "loadedSceneProof": {
                    "loadedSceneVerified": loaded_scene_verified,
                    "gameState": game_state,
                    "clientTickHotFresh": client_tick_fresh,
                    "worldModelObjectTotal": world_object_total,
                },
            }
        loaded_scene_verified = bool(loaded_scene_verified or _dict(recovery_hint.get("loadedSceneProof")).get("loadedSceneVerified"))
        return {
            "schema": "runelite_bootstrap_state.v1",
            "state": recovery_hint.get("livenessState") or state,
            "loadedSceneVerified": loaded_scene_verified,
            "loginScreenDetected": state == "login_screen",
            "credentialRequired": False,
            "disconnectedDialogDetected": False,
            "savedAccountPlayNowDetected": False,
            "clickHereToPlayDetected": False,
            "staleLoggedInNoScene": stale_logged_in_no_scene,
            "bootstrapRecommended": bool(recovery_hint.get("livenessRecoveryRecommended")),
            "bootstrapSafeActionAvailable": False,
            "livenessRecoveryRecommended": bool(recovery_hint.get("livenessRecoveryRecommended")),
            "livenessRecoveryAvailable": bool(recovery_hint.get("livenessRecoveryAvailable")),
            "knownRecoverableState": bool(recovery_hint.get("knownRecoverableState")),
            "manualLoginRequired": bool(recovery_hint.get("manualLoginRequired")),
            "unknownScreen": bool(recovery_hint.get("unknownScreen")),
            "recommendedCommand": "python telemetry-viewer\\context_service.py --ensure-loaded-scene --arduino-port COM6",
            "evidence": {
                "gameState": game_state,
                "latestTick": latest_tick,
                "clientTickFresh": client_tick_fresh,
                "worldModelAvailable": world_fresh,
                "objectCount": liveness_object_count,
                "localObjectCount": local_object_count,
                "worldModelObjectTotal": world_object_total,
                "worldModelSummarySource": status_with_world.get("worldModelSummarySource"),
                "broadFetchTimedOut": status_with_world.get("broadFetchTimedOut"),
                "minimalLiveLivenessFallbackUsed": status_with_world.get("minimalLiveLivenessFallbackUsed"),
                "loadedSceneProof": recovery_hint.get("loadedSceneProof"),
            },
        }

    def _static_route(self, route_id: str | None) -> dict[str, Any]:
        if not route_id:
            return {}
        route_id_norm = _norm(route_id)
        for route in _list(self.static_library.get("routes")):
            if not isinstance(route, dict):
                continue
            aliases = {_norm(item) for item in _list(route.get("aliases"))}
            if _norm(route.get("routeId")) == route_id_norm or route_id_norm in aliases or _norm(route.get("destinationRouteId")) == route_id_norm:
                return dict(route)
        return {}

    def _blocker_category(self, *, text: str, readiness: dict[str, Any], latest_bundle: dict[str, Any], proposal: dict[str, Any]) -> tuple[str, str, str, bool, bool]:
        status = self.daemon_status
        phase = _compact_phase(status)
        route = _compact_route_context(status)
        hot = _dict(status.get("clientTickHot"))
        game_state = _first_present(hot.get("gameState"), status.get("gameState"))
        latest_text = " ".join(
            _norm(value)
            for value in (
                latest_bundle.get("reason"),
                latest_bundle.get("classification"),
                latest_bundle.get("finalDecision"),
                proposal.get("reason"),
            )
        )
        world_quality = world_model_core.world_model_quality(self.world_model_payloads)
        bootstrap_state = self._bootstrap_liveness_summary()
        action_readiness = _dict(readiness.get("actionReadiness"))
        execution_allowed = bool(action_readiness.get("executionAllowed"))
        action_blockers = _list(action_readiness.get("blockers"))
        action_blocker_text = " ".join(json.dumps(item, default=str).lower() for item in action_blockers)
        route_applicability = _route_context_applicability(status, readiness=readiness, proposal=proposal)
        route_or_service_context_present = bool(
            route_applicability.get("routeContextApplicable")
            and (
                route.get("currentNodeId")
                or route.get("nextEdgeType")
                or phase.get("cycleStage") in {"pathing_to_service", "needs_service", "service"}
                or "route" in text
                or "path" in text
                or "bank" in text
                or "service" in text
            )
        )
        if bootstrap_state.get("state") == "login_screen" or (
            bootstrap_state.get("state") == "stale_logged_in_no_scene" and not route_or_service_context_present
        ):
            return (
                "login/liveness",
                f"RuneLite bootstrap state is {bootstrap_state.get('state')}; loaded scene is not verified.",
                "Run ensure_loaded_scene to recover a loaded scene and rebind the daemon.",
                False,
                False,
            )
        if game_state and str(game_state) != "LOGGED_IN":
            return (
                "login/liveness",
                f"RuneLite is {game_state}; live scene is not ready.",
                "Run ensure_loaded_scene unless a manual login/account prompt is present.",
                False,
                False,
            )
        if world_quality.get("worldModelAvailable") is not True:
            return (
                "plugin/daemon freshness",
                "The live world model is unavailable or stale.",
                "Verify 8893 health, loaded scene packets, and daemon binding.",
                False,
                False,
            )
        if (
            "plugin_snapshot_source_not_ready" in action_blocker_text
            or "client_tick_hot_unavailable" in action_blocker_text
            or "plugin.snapshot" in action_blocker_text
            or "client_tick_hot" in action_blocker_text
        ):
            return (
                "plugin/daemon freshness",
                "The plugin snapshot or client-tick hot interaction stream is not fresh enough for live action.",
                "Run ensure_loaded_scene once, then recheck 8893/8890 freshness.",
                False,
                False,
            )
        if "session" in text and "mismatch" in text:
            return (
                "session mismatch",
                "Daemon, latest-session, or overlay sources disagree.",
                "Use daemon-bound session data and rebind/restart stale readers.",
                False,
                True,
            )
        if "arduino" in text or "input_integrity" in text or "monitor" in text:
            input_summary = self._input_integrity_summary()
            safe = input_summary.get("monitorPass") is True and not input_summary.get("injectedEvents") and not input_summary.get("lowerIlInjectedEvents")
            return (
                "input/Arduino",
                "Live input integrity needs attention before another action." if not safe else "Input integrity is passing; Arduino is not the current blocker.",
                "Fix monitor/firmware safety if failing; otherwise inspect action/pathing.",
                bool(safe),
                not bool(safe),
            )
        if "hover" in text or "menu" in text:
            return (
                "hover/menu",
                "Hover/menu confirmation did not match the current action intent.",
                "Inspect latest hover sample, target stack, and screenshot before clicking.",
                False,
                True,
            )
        if ("safeaim" in text or "projection" in text) and (not execution_allowed or bool(action_blockers)):
            return (
                "projection/safeAimPoint",
                "The target projection or safe aim point is not action-ready.",
                "Use query_view_quality and candidate projection evidence; recover camera if needed.",
                False,
                True,
            )
        if "overlay" in text or "highlighter" in text:
            return (
                "overlay-only",
                "Overlay/highlighter state is warning or unavailable for the current context.",
                "Check whether overlay markers are applicable to the current intent before blocking.",
                readiness.get("readinessPassed") is True,
                False,
            )
        if action_blockers and not execution_allowed and not route_applicability.get("routeContextApplicable"):
            return (
                "readiness",
                "Action readiness is blocking execution.",
                "Inspect actionReadiness blockers in current-debug-context before any live action.",
                False,
                True,
            )
        if route_applicability.get("routeContextApplicable") and (
            "wall_hugging" in latest_text or "wall_hugging" in text or "wall hugging" in text or route.get("wallLoopDetected") or route.get("wallHuggingDetected")
        ):
            return (
                "route/pathing",
                f"Route navigation is near {route.get('currentNodeId') or 'the current route node'} and the latest evidence reports wall-hugging risk.",
                "Inspect route context, collision frontier, and approach-node candidates before another long live run.",
                False,
                True,
            )
        if route_applicability.get("routeContextApplicable") and (
            "route" in text
            or "path" in text
            or phase.get("cycleStage") in {"pathing_to_service", "needs_service"}
            or route.get("nextEdgeType")
        ):
            return (
                "route/pathing",
                f"Current route node is {route.get('currentNodeId') or 'unknown'} with edge {route.get('nextEdgeType') or 'unknown'}.",
                "Query path frontier, route objects, and service candidates; run only a bounded action if readiness allows.",
                execution_allowed,
                False,
            )
        if route_applicability.get("routeContextApplicable") and (
            "bank" in text or "service" in text or phase.get("cycleStage") in {"service", "needs_service"}
        ):
            return (
                "service/bank",
                "The active lifecycle needs bank/service progress.",
                "Query service candidates and route objects, then use strict readiness before acting.",
                execution_allowed,
                False,
            )
        proposal_executable = proposal.get("executable")
        if ("target" in text or "candidate" in text) and (
            not execution_allowed or bool(action_blockers) or proposal_executable is False
        ):
            return (
                "target/candidate",
                "The current target/candidate evidence is incomplete or stale.",
                "Requery live candidates and avoid static priors as executable targets.",
                False,
                True,
            )
        if proposal.get("actionTargetSource") in {"static_route_prior", "route_context_goal"}:
            return (
                "static prior only",
                "The proposal is still advisory/static and lacks a fresh executable live target.",
                "Reacquire live projection/object/hover evidence before execution.",
                False,
                True,
            )
        allowed = execution_allowed
        return (
            "unknown" if not allowed else "ready",
            "No clear blocker was found." if allowed else "Action readiness is not currently allowing execution.",
            "Use current-debug-context, then inspect the newest blocker-specific query.",
            allowed,
            not allowed,
        )

    def explain_current_blocker(self) -> dict[str, Any]:
        started = time.perf_counter()
        status = self.daemon_status
        stored_blocker = _dict(_dict(status.get("knowledgeCurrentBlocker")).get("data"))
        if stored_blocker and status.get("schema") == "context_response.v1":
            data = dict(stored_blocker)
            data.setdefault("replayedFromSavedContext", True)
            return _query_response(
                "knowledge_fabric_current_blocker_explanation.v1",
                data,
                started=started,
                source="saved_context_response",
                freshness=self.freshness(),
                status="PASS" if data.get("primaryBlockerCategory") == "ready" else "WARN",
            )
        readiness = self._readiness_report()
        proposal = self._action_proposal()
        warnings = [str(item) for item in _list(status.get("warnings"))]
        blockers = [str(item) for item in _list(status.get("blockers"))]
        action_readiness = _dict(
            readiness.get("actionReadiness")
            or status.get("actionReadiness")
            or _dict(status.get("readiness")).get("actionReadiness")
        )
        if action_readiness:
            blockers.extend(str(item) for item in _list(action_readiness.get("blockers")))
            warnings.extend(str(item) for item in _list(action_readiness.get("warnings")))
        latest_bundle = self._latest_visual_bundle_summary()
        latest_trace = self._latest_action_trace_summary()
        text = " ".join(blockers + warnings + [json.dumps(latest_bundle, default=str), json.dumps(latest_trace, default=str)]).lower()
        category, summary, recommended, safe_to_run, code_likely = self._blocker_category(
            text=text,
            readiness=readiness,
            latest_bundle=latest_bundle,
            proposal=proposal,
        )
        execution_allowed = _dict(action_readiness).get("executionAllowed") is True
        proposed_action = _first_present(readiness.get("proposedAction"), proposal.get("proposedAction"))
        non_click_recovery_allowed = bool(
            proposed_action in {"resource_view_recovery"}
            and _dict(action_readiness).get("status") == "PASS"
        )
        if not execution_allowed and not non_click_recovery_allowed:
            safe_to_run = False
        phase = _compact_phase(status)
        route_context = _compact_route_context(status)
        route_context_applicability = _route_context_applicability(status, readiness=readiness, proposal=proposal)
        source_metadata = _source_metadata(status, fallback_source="daemon_status")
        location = _compact_location(status)
        inventory = _compact_inventory(status)
        selected_target = _dict(proposal.get("targetExplanation"))
        safe_aimpoint = _dict(selected_target.get("safeAimPoint"))
        data = {
            "humanSummary": summary,
            "primaryBlockerCategory": category,
            "primaryBlockerSummary": summary,
            "recommendedNextStep": recommended,
            "recommendedNextQuery": "query_path_frontier" if category == "route/pathing" else "get_current_debug_context",
            "recommendedNextCodeArea": "pathing/frontier selection" if category == "route/pathing" else "none until query evidence points to a code path",
            "safeToRunBoundedLiveAction": safe_to_run,
            "codeChangeLikelyNeeded": code_likely,
            "captureReplayBeforeCodeChange": category not in {"ready", "login/liveness"},
            "externalKnowledgeWouldHelp": category in {"target/candidate", "route/pathing", "service/bank", "route object not observed", "external knowledge/cache miss"},
            **source_metadata,
            "phase": phase.get("phase"),
            "cycleStage": phase.get("cycleStage"),
            "currentIntent": _first_present(readiness.get("currentIntent"), phase.get("currentIntent"), phase.get("activeIntent")),
            "location": location,
            "inventory": inventory,
            **route_context_applicability,
            "blockers": blockers[:20],
            "warnings": warnings[:20],
            "evidence": {
                "bootstrapState": self._bootstrap_liveness_summary(),
                "worldModelFreshness": self.freshness(),
                "knowledgeFabricStatus": {
                    "status": self.status().get("status"),
                    "capWarnings": self.status().get("capWarnings"),
                    "objectCount": self.status().get("performanceStats", {}).get("objectCount"),
                },
                "actionReadiness": action_readiness,
                "actionTargetSource": proposal.get("actionTargetSource"),
                "actionability": proposal.get("actionability"),
                "selectedTarget": {
                    "name": selected_target.get("name") or proposal.get("targetName"),
                    "targetKind": proposal.get("targetKind"),
                    "worldLocation": selected_target.get("worldLocation") or selected_target.get("world"),
                    "freshness": selected_target.get("freshness"),
                },
                "safeAimPoint": safe_aimpoint or None,
                "hoverMenu": _dict(status.get("clientTickHot")).get("postMenuSort") or status.get("hoverMenu"),
                "routeContext": route_context,
                "routeContextApplicability": route_context_applicability,
                "pathingFrontier": self.query_path_frontier(limit=5).get("data"),
                "serviceAnchor": {
                    "selectedServiceTargetName": status.get("selectedServiceTargetName"),
                    "selectedServiceTargetTile": status.get("selectedServiceTargetTile"),
                    "serviceReady": status.get("serviceReady"),
                    "serviceReadyReason": status.get("serviceReadyReason"),
                },
                "overlayHealth": readiness.get("overlayHealth"),
                "inputIntegrity": self._input_integrity_summary(),
                "latestActionClassification": latest_trace.get("classification") or latest_bundle.get("classification"),
                "latestDebugBundlePath": latest_bundle.get("bundleDir"),
                "latestDebugScreenshotPath": latest_bundle.get("screenshotPath"),
                "externalKnowledge": {
                    "status": _external_summary_compact().get("status"),
                    "cachePath": _external_summary_compact().get("cachePath"),
                    "couldHelpLabelBlocker": category in {"target/candidate", "route/pathing", "service/bank"},
                },
            },
        }
        return _query_response(
            "knowledge_fabric_current_blocker_explanation.v1",
            data,
            started=started,
            source="daemon_status",
            freshness=self.freshness(),
            status="PASS" if category == "ready" else "WARN",
            extra=source_metadata,
        )

    def query_current_debug_context(self, *, profile: str = "woodcutting", limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        cap = _safe_limit(limit)
        readiness = self._readiness_report()
        proposal = self._action_proposal()
        phase = _compact_phase(self.daemon_status)
        current_intent = _first_present(readiness.get("currentIntent"), phase.get("currentIntent"), phase.get("activeIntent"), proposal.get("proposedAction"))
        bootstrap_state = self._bootstrap_liveness_summary()
        route_context_applicability = _route_context_applicability(self.daemon_status, readiness=readiness, proposal=proposal)
        source_metadata = _source_metadata(self.daemon_status, fallback_source=self.source)
        input_integrity = self._input_integrity_summary()
        input_integrity_status = _phase_aware_input_integrity_status(input_integrity)
        data = {
            "liveStatus": {
                "schema": self.daemon_status.get("schema"),
                "status": _status_label(self.daemon_status),
                "latestTick": self.daemon_status.get("latestTick"),
                "sessionPath": self.daemon_status.get("sessionPath"),
                "inputSourceActive": self.daemon_status.get("inputSourceActive"),
                "livePacketsRuntimeRemoved": True,
                "ndjsonRuntimeRemoved": True,
                "jsonlRuntimeRemoved": True,
                "livePacketWriterActive": False,
                "legacyLivePacketFilesPresent": self.daemon_status.get("legacyLivePacketFilesPresent"),
                "legacyLivePacketTotalMb": self.daemon_status.get("legacyLivePacketTotalMb"),
                "cleanupRecommended": self.daemon_status.get("cleanupRecommended"),
                "gameState": _dict(self.daemon_status.get("clientTickHot")).get("gameState"),
                "phase": phase,
                "location": _compact_location(self.daemon_status),
                "inventory": _compact_inventory(self.daemon_status),
            },
            **source_metadata,
            "readiness": readiness,
            "bootstrapState": bootstrap_state,
            "loadedSceneVerified": bootstrap_state.get("loadedSceneVerified"),
            "livenessRecoveryRecommended": bootstrap_state.get("livenessRecoveryRecommended"),
            "livenessRecoveryAvailable": bootstrap_state.get("livenessRecoveryAvailable"),
            "livenessRecoveryLastResult": self.daemon_status.get("livenessRecoveryLastResult"),
            "livenessState": bootstrap_state.get("state"),
            "loadedSceneProof": _dict(bootstrap_state.get("evidence")).get("loadedSceneProof"),
            "knownRecoverableState": bootstrap_state.get("knownRecoverableState"),
            **route_context_applicability,
            "manualLoginRequired": bootstrap_state.get("manualLoginRequired"),
            "unknownScreen": bootstrap_state.get("unknownScreen"),
            "worldModelSummary": self.query_world_summary(),
            "knowledgeFabricStatus": self.status(),
            "currentBlocker": self.explain_current_blocker(),
            "actionProposal": proposal,
            "resourceCandidates": self.query_resource_candidates(profile=profile, limit=min(cap, 15)),
            "routeObjects": self.query_route_objects(limit=min(cap, 15)),
            "serviceObjects": self.query_service_candidates(limit=min(cap, 15)),
            "pathingFrontier": self.query_path_frontier(limit=min(cap, 15)),
            "viewQuality": self.query_view_quality(intent=str(current_intent or "unknown")),
            "navigationDecisionTrace": self.query_navigation_decision_trace(limit=min(cap, 15)),
            "overlayHealth": readiness.get("overlayHealth"),
            "inputIntegrity": input_integrity,
            "input_integrity_status": input_integrity_status,
            "inputIntegrityPhaseReport": input_integrity_status.get("phaseCounts"),
            "phaseAwareInputIntegrity": input_integrity_status.get("phaseAwareAssessment"),
            "actionInputVisibility": self.query_action_input_visibility(),
            "latestActionTraceSummary": self._latest_action_trace_summary(),
            "latestVisualBundleSummary": self.query_debug_evidence(limit=3),
            "sessionMemorySummary": {
                "schema": self.session_memory.get("schema"),
                "sessionPath": self.session_memory.get("sessionPath"),
                "summary": self.session_memory.get("summary", {}),
            },
            "staticProfileSummary": self.static_library.get("summary", {}),
            "externalKnowledgeSummary": _external_summary_compact(),
            "dataQualityReport": self.data_quality_report(limit=cap),
            "coverageReport": self.coverage_report(intent=str(current_intent or "unknown"), limit=cap),
            "dataSourceInventorySummary": {
                "schema": DATA_SOURCE_INVENTORY_SCHEMA,
                "sourceCount": len(_list(_dict(self.data_source_inventory().get("data")).get("sources"))),
                "livePacketArchiveRemoved": True,
                "externalKnowledgeCachePath": _external_summary_compact().get("cachePath"),
            },
            "storageWarningSummary": {
                "legacyLivePacketFilesPresent": self.daemon_status.get("legacyLivePacketFilesPresent"),
                "legacyLivePacketTotalMb": self.daemon_status.get("legacyLivePacketTotalMb"),
                "externalCacheSizeMb": _external_summary_compact().get("cacheSizeMb"),
            },
            "queryFirstWorkflow": [
                "get_current_debug_context",
                "explain_current_blocker",
                "query_resource_candidates/query_route_objects/query_service_candidates",
                "query_path_frontier for route issues",
                "query_navigation_decision_trace for route decision reasons",
                "query_view_quality for camera/visibility issues",
                "get_latest_action_trace and get_latest_visual_bundle for evidence",
            ],
            "runtimeSafety": "read-only query context; live execution remains 8890/8893 -> executor -> HumanInputController",
        }
        return _query_response(
            "knowledge_fabric_current_debug_context.v1",
            data,
            started=started,
            source=self.source,
            freshness=self.freshness(),
            extra=source_metadata,
        )

    def explain_next_action_context(self) -> dict[str, Any]:
        started = time.perf_counter()
        status = self.daemon_status
        brain = _dict(status.get("brain"))
        generic = _dict(brain.get("genericTaskState"))
        data = {
            "phase": status.get("phase") or generic.get("phase") or status.get("currentCycleStage"),
            "currentIntent": status.get("currentIntent") or generic.get("activeIntent") or status.get("activeIntent"),
            "actionReadiness": status.get("actionReadiness") or _dict(status.get("readiness")).get("actionReadiness"),
            "worldModelStatus": self.status(),
            "resourceCandidates": self.query_resource_candidates(limit=5)["data"],
            "serviceCandidates": self.query_service_candidates(limit=5)["data"],
            "routeObjects": self.query_route_objects(limit=5)["data"],
            "pathFrontier": self.query_path_frontier(limit=5)["data"],
            "safetyRule": "MCP/Knowledge Fabric is read-only; execution still goes through readiness and HumanInputController",
        }
        return _query_response(
            "knowledge_fabric_next_action_context.v1",
            data,
            started=started,
            source="daemon_status",
            freshness=self.freshness(),
        )

    def list_available_profiles(self, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        profiles = []
        for profile in _list(self.static_library.get("targetProfiles")):
            if not isinstance(profile, dict):
                continue
            profiles.append({
                "profileId": profile.get("profileId"),
                "displayName": profile.get("displayName"),
                "description": profile.get("description"),
                "includeTargetClasses": profile.get("includeTargetClasses"),
                "defaultLimit": profile.get("defaultLimit"),
            })
        capped, cap_hit = _cap_items(profiles, limit)
        return _query_response(
            "knowledge_fabric_profiles.v1",
            {"profiles": capped, "count": len(profiles)},
            started=started,
            source="static_library",
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def describe_profile(self, profile: str) -> dict[str, Any]:
        started = time.perf_counter()
        profile_norm = _norm(profile)
        match = {}
        for item in _list(self.static_library.get("targetProfiles")):
            if isinstance(item, dict) and _norm(item.get("profileId")) == profile_norm:
                match = dict(item)
                break
        target_classes = self.list_target_classes(profile)["data"].get("targetClasses", [])
        return _query_response(
            "knowledge_fabric_profile_description.v1",
            {"profile": match, "targetClasses": target_classes, "found": bool(match)},
            started=started,
            source="static_library",
            freshness=self.freshness(),
            status="PASS" if match else "WARN",
            warnings=[] if match else [f"profile not found: {profile}"],
        )

    def list_target_classes(self, profile: str | None = None, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        include: set[str] | None = None
        if profile:
            for item in _list(self.static_library.get("targetProfiles")):
                if isinstance(item, dict) and _norm(item.get("profileId")) == _norm(profile):
                    include = {str(value) for value in _list(item.get("includeTargetClasses"))}
                    break
        classes = []
        for item in _list(self.static_library.get("targetLibrary")):
            if not isinstance(item, dict):
                continue
            if include is not None and str(item.get("classId")) not in include:
                continue
            classes.append({
                "classId": item.get("classId"),
                "displayName": item.get("displayName"),
                "targetTypes": item.get("targetTypes"),
                "actions": item.get("usefulActions") or item.get("actionContains"),
                "requiredSkill": item.get("requiredSkill"),
                "requiredLevel": item.get("requiredLevel"),
                "profileHints": item.get("profileHints"),
            })
        capped, cap_hit = _cap_items(classes, limit)
        return _query_response(
            "knowledge_fabric_target_classes.v1",
            {"profile": profile, "targetClasses": capped, "count": len(classes)},
            started=started,
            source="static_library",
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def list_known_actions(self, target_class: str | None = None, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        actions = []
        class_norm = _norm(target_class)
        for item in _list(self.static_library.get("targetLibrary")):
            if not isinstance(item, dict):
                continue
            if class_norm and class_norm not in {_norm(item.get("classId")), _norm(item.get("displayName"))}:
                continue
            values = list(dict.fromkeys(_list(item.get("usefulActions")) + _list(item.get("actionContains"))))
            actions.append({
                "classId": item.get("classId"),
                "displayName": item.get("displayName"),
                "knownActions": values,
                "requiredSkill": item.get("requiredSkill"),
                "requiredLevel": item.get("requiredLevel"),
            })
        capped, cap_hit = _cap_items(actions, limit)
        return _query_response(
            "knowledge_fabric_known_actions.v1",
            {"targetClass": target_class, "items": capped, "count": len(actions)},
            started=started,
            source="static_library",
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def list_service_routes(self, profile: str | None = None, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        profile_norm = _norm(profile)
        routes = []
        for route in _list(self.static_library.get("routes")):
            if not isinstance(route, dict):
                continue
            route_profiles = {_norm(route.get("profile")), *{_norm(item) for item in _list(route.get("profiles"))}}
            if profile_norm and profile_norm not in route_profiles:
                continue
            routes.append({
                "routeId": route.get("routeId"),
                "aliases": route.get("aliases"),
                "profile": route.get("profile"),
                "profiles": route.get("profiles"),
                "serviceType": route.get("serviceType"),
                "areaHint": route.get("areaHint"),
                "verifiedLive": route.get("verifiedLive"),
                "confidence": route.get("confidence"),
                "nodeCount": len(_list(route.get("nodes"))),
                "edgeCount": len(_list(route.get("edges"))),
                "stepCount": len(_list(route.get("steps"))),
                "advisoryOnly": not bool(route.get("verifiedLive")),
            })
        capped, cap_hit = _cap_items(routes, limit)
        return _query_response(
            "knowledge_fabric_service_routes.v1",
            {"profile": profile, "routes": capped, "count": len(routes)},
            started=started,
            source="static_library",
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def describe_route(self, route_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        route = self._static_route(route_id)
        route_context = _compact_route_context(self.daemon_status)
        data = {
            "route": route,
            "found": bool(route),
            "currentRouteContext": route_context,
            "liveRouteObjects": self.query_route_objects(limit=10).get("data"),
            "sessionMemoryRouteObjects": self.query_session_memory(kind="route", limit=10).get("data"),
            "executionRule": "static route details are advisory until current live target/projected waypoint/hover evidence verifies them",
        }
        return _query_response(
            "knowledge_fabric_route_description.v1",
            data,
            started=started,
            source="static_library+daemon_status",
            freshness=self.freshness(),
            status="PASS" if route else "WARN",
            warnings=[] if route else [f"route not found: {route_id}"],
        )

    def explain_required_telemetry_for_task(self, task_name: str) -> dict[str, Any]:
        started = time.perf_counter()
        text = _norm(task_name)
        needs = [
            "plugin snapshot freshness and LOGGED_IN baseline",
            "inventory/resource-count packets",
            "client_tick_hot PostMenuSort hover state",
            "MenuOptionClicked proof after click",
            "world model object census for current loaded scene",
            "projection/safeAimPoint evidence",
            "input integrity if executing live actions",
        ]
        if any(word in text for word in ("wood", "tree", "resource", "mine", "fish")):
            needs.extend(["resource_object_census", "target profile with level requirements", "worksite/resource area memory"])
        if any(word in text for word in ("bank", "service", "deposit")):
            needs.extend(["service_object_census", "service route/static anchors", "bank UI and bank operation context"])
        if any(word in text for word in ("route", "walk", "navigate", "path")):
            needs.extend(["collision/pathing frontier", "route object census", "view quality for waypoint projection"])
        return _query_response(
            "knowledge_fabric_required_telemetry.v1",
            {"taskName": task_name, "requiredTelemetry": list(dict.fromkeys(needs)), "runtimePath": "8893 snapshot -> 8890 daemon -> executor"},
            started=started,
            source="static_guidance",
            freshness=self.freshness(),
        )

    def query_scene_for_new_task_keywords(self, keywords: list[str] | str, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        tokens = [_norm(item) for item in (keywords if isinstance(keywords, list) else str(keywords).replace(",", " ").split()) if _norm(item)]
        matches = []
        for obj in self.objects:
            haystack = " ".join([_norm(obj.get("name") or obj.get("targetName") or obj.get("objectName")), _action_text(obj)])
            if tokens and not any(token in haystack for token in tokens):
                continue
            matches.append(_compact_object(obj))
        capped, cap_hit = _cap_items(matches, limit)
        return _query_response(
            "knowledge_fabric_scene_keyword_query.v1",
            {"keywords": tokens, "objects": capped, "count": len(matches)},
            started=started,
            source=self.source,
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def suggest_profile_skeleton_from_scene(self, description: str | None = None, keywords: list[str] | str | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        token_source = keywords if keywords is not None else str(description or "")
        scene = self.query_scene_for_new_task_keywords(token_source, limit=25).get("data", {})
        objects = _list(scene.get("objects"))
        actions = sorted({action for obj in objects for action in _list(obj.get("actions")) if action})
        names = sorted({str(obj.get("name")) for obj in objects if obj.get("name")})
        class_hints = []
        for target in _list(self.static_library.get("targetLibrary")):
            if not isinstance(target, dict):
                continue
            target_text = json.dumps(target, default=str).lower()
            if any(_norm(name) and _norm(name) in target_text for name in names[:10]):
                class_hints.append(target.get("classId"))
        skeleton = {
            "schema": "target_profile_skeleton.v1",
            "profileId": "_new_profile_id_",
            "displayName": description or "New task profile",
            "includeTargetClasses": list(dict.fromkeys(class_hints)) or ["unknown_scene_object"],
            "includeTargetTypes": ["sceneObject", "npc", "groundItem"],
            "includeRoles": ["interactable"],
            "requireOnScreen": True,
            "requireGeometryAvailable": True,
            "candidateNameHints": names[:12],
            "candidateActionHints": actions[:12],
            "notes": "Generated from loaded-scene evidence; review before execution.",
        }
        return _query_response(
            "knowledge_fabric_profile_skeleton_suggestion.v1",
            {"description": description, "sceneMatches": scene, "profileSkeleton": skeleton},
            started=started,
            source="live_scene+static_library",
            freshness=self.freshness(),
        )

    def list_seen_objects_by_action(self, action: str, limit: int | None = None) -> dict[str, Any]:
        return self.query_actions(action, limit=limit)

    def list_seen_objects_by_name(self, name_contains: str, limit: int | None = None) -> dict[str, Any]:
        return self.query_objects_near(None, filters={"nameContains": name_contains}, limit=limit)

    def export_task_context_bundle(self, profile: str | None = None, task: str | None = None, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        profile_value = profile or task or "woodcutting"
        data = {
            "profile": profile_value,
            "task": task,
            "profileDescription": self.describe_profile(profile_value).get("data"),
            "targetClasses": self.list_target_classes(profile_value).get("data"),
            "knownActions": self.list_known_actions(limit=limit).get("data"),
            "serviceRoutes": self.list_service_routes(profile_value).get("data"),
            "requiredTelemetry": self.explain_required_telemetry_for_task(task or profile_value).get("data"),
            "currentDebugContext": self.query_current_debug_context(profile=profile_value, limit=limit).get("data"),
            "staticLibraryVersionHash": self.static_library.get("versionHash"),
        }
        return _query_response(
            "knowledge_fabric_task_context_bundle.v1",
            data,
            started=started,
            source="knowledge_fabric",
            freshness=self.freshness(),
        )

    def list_seen_widgets(self, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        widgets: list[dict[str, Any]] = []

        def visit(value: Any, path: str) -> None:
            if len(widgets) > HARD_QUERY_LIMIT * 4:
                return
            if isinstance(value, dict):
                lower_path = path.lower()
                looks_widget = (
                    "widget" in lower_path
                    or "dialogue" in lower_path
                    or "bankui" in lower_path
                    or "interface" in lower_path
                    or value.get("widgetId") is not None
                    or value.get("componentId") is not None
                )
                if looks_widget and any(key in value for key in ("id", "widgetId", "componentId", "text", "name", "visible", "bounds")):
                    widgets.append({
                        "path": path,
                        "id": value.get("id"),
                        "widgetId": value.get("widgetId"),
                        "componentId": value.get("componentId"),
                        "name": value.get("name"),
                        "text": value.get("text"),
                        "visible": value.get("visible"),
                        "bounds": value.get("bounds"),
                        "actions": value.get("actions") or value.get("menuActions"),
                    })
                for key, child in value.items():
                    if isinstance(child, (dict, list)):
                        visit(child, f"{path}.{key}" if path else str(key))
            elif isinstance(value, list):
                for index, child in enumerate(value[:80]):
                    if isinstance(child, (dict, list)):
                        visit(child, f"{path}[{index}]")

        visit(self.daemon_status, "daemonStatus")
        capped, cap_hit = _cap_items([item for item in widgets if isinstance(item, dict)], limit)
        return _query_response(
            "knowledge_fabric_seen_widgets.v1",
            {"widgets": capped, "count": len(widgets)},
            started=started,
            source="daemon_status",
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def list_seen_inventory_items(self, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        status = _status_view(self.daemon_status)
        brain = _dict(status.get("brain"))
        inventory_context = _dict(brain.get("inventoryContext") or status.get("inventoryContext"))
        candidates: list[Any] = []
        for value in (
            inventory_context.get("items"),
            inventory_context.get("inventoryItems"),
            status.get("inventoryItems"),
            status.get("inventory"),
            _dict(status.get("baseline")).get("inventory"),
            _dict(status.get("worldModelPayloads")).get("inventory"),
            self.world_model_payloads.get("inventory"),
        ):
            if isinstance(value, list):
                candidates.extend(value)
            elif isinstance(value, dict):
                candidates.extend(_list(value.get("items")))
                candidates.extend(_list(_dict(value.get("inventory")).get("items")))
        items = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            item_id = _first_present(item.get("id"), item.get("itemId"))
            external = external_knowledge.lookup_item_id(item_id) if item_id is not None else {}
            external_item = _dict(_dict(external.get("data")).get("item"))
            items.append({
                "id": item_id,
                "name": _first_present(item.get("name"), item.get("itemName"), external_item.get("name"), external_item.get("canonicalName")),
                "quantity": _first_present(item.get("quantity"), item.get("qty")),
                "slot": _first_present(item.get("slot"), item.get("index")),
                "actions": item.get("actions") or item.get("menuActions"),
                "externalKnowledge": external,
            })
        capped, cap_hit = _cap_items(items, limit)
        return _query_response(
            "knowledge_fabric_seen_inventory_items.v1",
            {
                "inventorySummary": _compact_inventory(self.daemon_status),
                "items": capped,
                "count": len(items),
            },
            started=started,
            source="daemon_status",
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def list_seen_npcs(self, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        npcs = []
        for obj in self.objects:
            kind = _norm(obj.get("kind") or obj.get("targetType") or obj.get("objectKind"))
            if "npc" in kind:
                compact = _compact_object(obj)
                compact["externalKnowledge"] = external_knowledge.enrich_name(str(compact.get("name") or ""))
                npcs.append(compact)
        capped, cap_hit = _cap_items(npcs, limit)
        return _query_response(
            "knowledge_fabric_seen_npcs.v1",
            {"npcs": capped, "count": len(npcs)},
            started=started,
            source=self.source,
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def list_seen_ground_items(self, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        items = []
        for obj in self.objects:
            kind = _norm(obj.get("kind") or obj.get("targetType") or obj.get("objectKind"))
            if "ground" in kind or "item" in kind:
                compact = _compact_object(obj)
                compact["externalKnowledge"] = external_knowledge.enrich_name(str(compact.get("name") or ""))
                items.append(compact)
        capped, cap_hit = _cap_items(items, limit)
        return _query_response(
            "knowledge_fabric_seen_ground_items.v1",
            {"groundItems": capped, "count": len(items)},
            started=started,
            source=self.source,
            freshness=self.freshness(),
            cap_hit=cap_hit,
            truncated=cap_hit,
        )

    def external_knowledge_status(self) -> dict[str, Any]:
        return external_knowledge.knowledge_status()

    def external_lookup_item_id(self, item_id: int | str) -> dict[str, Any]:
        return external_knowledge.lookup_item_id(item_id)

    def external_search_item(self, name: str, limit: int | None = None) -> dict[str, Any]:
        return external_knowledge.search_item(name, limit=_safe_limit(limit))

    def external_lookup_object(self, name: str) -> dict[str, Any]:
        return external_knowledge.lookup_object(name)

    def external_lookup_npc(self, name: str) -> dict[str, Any]:
        return external_knowledge.lookup_npc(name)

    def external_lookup_area(self, name: str) -> dict[str, Any]:
        return external_knowledge.lookup_area(name)

    def external_lookup_area_by_coord(self, x: int | float, y: int | float, plane: int = 0) -> dict[str, Any]:
        return external_knowledge.lookup_area_by_coord(x, y, plane)

    def external_get_skill_requirement(self, name: str) -> dict[str, Any]:
        return external_knowledge.get_skill_requirement(name)

    def external_search_wiki(self, query: str, *, allow_refresh: bool = False, limit: int | None = None) -> dict[str, Any]:
        return external_knowledge.search_wiki(query, allow_refresh=allow_refresh, limit=_safe_limit(limit))

    def external_get_route_prior(self, current_area: str, service_area: str) -> dict[str, Any]:
        return external_knowledge.route_prior_between(current_area, service_area)

    def coverage_report(self, intent: str | None = None, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        phase = _compact_phase(self.daemon_status)
        current_intent = intent or _first_present(phase.get("currentIntent"), phase.get("activeIntent"), self.daemon_status.get("currentIntent"), "unknown")
        quality = self.data_quality_report(limit=limit)
        quality_data = _dict(quality.get("data"))
        needs = {
            "resource": ["worldModelFresh", "objectCount", "projectionAuditAvailable", "clientTickFresh"],
            "route": ["worldModelFresh", "collisionAvailable", "routeObjectCounts", "projectionAuditAvailable"],
            "service": ["worldModelFresh", "serviceObjectCounts", "clientTickFresh"],
            "unknown": ["worldModelFresh", "objectCount", "clientTickFresh"],
        }
        bucket = "resource" if "resource" in _norm(current_intent) or "target" in _norm(current_intent) else "route" if "route" in _norm(current_intent) or "nav" in _norm(current_intent) else "service" if "service" in _norm(current_intent) or "bank" in _norm(current_intent) else "unknown"
        present = {
            "worldModelFresh": bool(quality_data.get("worldModelFresh")),
            "objectCount": bool((quality_data.get("objectCount") or 0) > 0),
            "collisionAvailable": bool(quality_data.get("collisionAvailable")),
            "projectionAuditAvailable": bool(quality_data.get("projectionAuditAvailable")),
            "clientTickFresh": bool(quality_data.get("clientTickFresh")),
            "routeObjectCounts": bool(_dict(self.query_route_objects(limit=1).get("data")).get("count")),
            "serviceObjectCounts": bool(_dict(self.query_service_candidates(limit=1).get("data")).get("count")),
        }
        missing = [key for key in needs[bucket] if not present.get(key)]
        data = {
            "currentIntent": current_intent,
            "intentBucket": bucket,
            "requiredData": needs[bucket],
            "presentData": present,
            "missingData": missing,
            "staleOrCappedData": {
                "staleSources": quality_data.get("staleSources"),
                "capHits": quality_data.get("capHits"),
                "missingExpectedSections": quality_data.get("missingExpectedSections"),
            },
            "externalKnowledgeCouldHelp": bucket in {"resource", "service", "route"},
            "blocksAction": bool(missing and bucket in {"resource", "route", "service"}),
            "confidence": "high" if not missing and quality_data.get("confidence") == "high" else "medium" if not missing else "low",
            "recommendedNextQuery": "query_path_frontier" if bucket == "route" else "query_resource_candidates" if bucket == "resource" else "query_service_candidates",
        }
        return _query_response(
            COVERAGE_REPORT_SCHEMA,
            data,
            started=started,
            source=self.source,
            freshness=self.freshness(),
            status="PASS" if not data["blocksAction"] else "WARN",
            warnings=[f"missing:{key}" for key in missing],
        )

    def probe_task(
        self,
        task_description: str,
        *,
        profile: str = "woodcutting",
        limit: int | None = None,
        capture_bundle: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        cap = _safe_limit(limit)
        tokens = [token for token in re.split(r"[^A-Za-z0-9]+", task_description.lower()) if token]
        expanded_tokens = list(tokens)
        if any(token in {"woodcutting", "woodcut", "logs", "log", "chop", "tree"} for token in tokens):
            expanded_tokens.extend(["tree", "oak", "chop"])
        if any(token in {"bank", "banking", "deposit"} for token in tokens):
            expanded_tokens.extend(["bank", "deposit"])
        tokens = list(dict.fromkeys(expanded_tokens))
        scene = self.query_scene_for_new_task_keywords(tokens, limit=cap)
        candidate_objects = _list(_dict(scene.get("data")).get("objects"))
        candidate_actions = sorted({action for obj in candidate_objects for action in _list(obj.get("actions")) if action})
        profile_skeleton = self.suggest_profile_skeleton_from_scene(description=task_description, keywords=tokens).get("data", {}).get("profileSkeleton")
        requirements = []
        wiki_pages = []
        external_used = []
        for name in [str(obj.get("name") or "") for obj in candidate_objects[:10]]:
            if not name:
                continue
            enrichment = external_knowledge.enrich_name(name)
            if enrichment.get("externalKnowledgeAvailable"):
                external_used.append({"name": name, "fact": enrichment})
                if enrichment.get("wikiPage"):
                    wiki_pages.append(enrichment.get("wikiPage"))
                if enrichment.get("requiredSkill") or enrichment.get("requiredLevel"):
                    requirements.append({
                        "target": enrichment.get("canonicalName") or name,
                        "requiredSkill": enrichment.get("requiredSkill"),
                        "requiredLevel": enrichment.get("requiredLevel"),
                        "provenance": enrichment.get("provenance"),
                    })
        if not requirements:
            for token in tokens:
                req = external_knowledge.get_skill_requirement(token)
                if req.get("status") == "PASS":
                    requirements.append(_dict(_dict(req.get("data")).get("requirement")))
        possible_profiles = self.list_available_profiles(limit=cap).get("data", {}).get("profiles", [])
        reusable = []
        text = _norm(task_description)
        if any(word in text for word in ("wood", "tree", "logs")):
            reusable.extend(["resource_progress.py", "candidate_core.py", "action_proposal.py", "woodcutting profile"])
        if any(word in text for word in ("bank", "deposit", "service")):
            reusable.extend(["service_route_core.py", "bank_operation_analyzer.py", "profiles/service_routes.json"])
        bundle = None
        if capture_bundle:
            bundle = self.capture_script_authoring_context(profile=profile, task_name=_slug(task_description), reason="task_probe", limit=cap)
        data = {
            "taskDescription": task_description,
            "candidateObjects": candidate_objects,
            "candidateActions": candidate_actions,
            "candidateWidgets": self.list_seen_widgets(limit=cap).get("data", {}).get("widgets", []),
            "candidateInventoryItems": self.list_seen_inventory_items(limit=cap).get("data", {}).get("items", []),
            "possibleProfiles": possible_profiles,
            "reusableAnalyzers": list(dict.fromkeys(reusable)),
            "suggestedNewProfile": profile_skeleton,
            "missingData": self.coverage_report(intent=task_description, limit=cap).get("data", {}).get("missingData", []),
            "externalKnowledgeUsed": external_used,
            "wikiPages": list(dict.fromkeys(wiki_pages)),
            "requirements": requirements,
            "nextImplementationSteps": [
                "Start from current-debug-context and task_probe_report.",
                "Review suggested profile skeleton before enabling execution.",
                "Keep static/external facts advisory until live candidates, projection, and hover confirm the action.",
            ],
            "scriptAuthoringContext": _dict(bundle.get("data")) if isinstance(bundle, dict) else None,
            "noLiveInput": True,
        }
        return _query_response(TASK_PROBE_SCHEMA, data, started=started, source="knowledge_fabric+external_cache", freshness=self.freshness())

    def task_script_api_spec(self) -> dict[str, Any]:
        return task_script_api.script_api_spec()

    def human_click_profile(self, activity: str | None = None, source: Any = None) -> dict[str, Any]:
        started = time.perf_counter()
        profile = human_click_profile.load_profile(source)
        data = human_click_profile.compact_profile(profile, activity=activity) if profile else {
            "schema": "human_click_profile_compact.v1",
            "status": "FAIL",
            "recordingCount": 0,
            "warnings": ["human click profile source was not available"],
            "missingCapabilities": ["human_click_profile"],
        }
        return _query_response(
            "knowledge_fabric_human_click_profile.v1",
            data,
            started=started,
            source="human_click_profile",
            freshness=self.freshness(),
            warnings=data.get("warnings") or [],
            status=data.get("status") or "WARN",
        )

    def human_click_plan(
        self,
        *,
        target: dict[str, Any] | None = None,
        action: str | None = None,
        activity: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        visibility = self.query_action_input_visibility().get("data") or {}
        runtime = self.query_task_script_runtime_evidence().get("data") or {}
        plan = task_script_api.get_human_click_plan(
            target=target,
            action=action,
            activity=activity,
            source={
                "actionInputVisibility": visibility,
                "taskScriptRuntimeEvidence": {"data": runtime},
            },
        )
        return _query_response(
            "knowledge_fabric_human_click_plan.v1",
            plan,
            started=started,
            source="task_script_api+human_click_profile",
            freshness=self.freshness(),
            warnings=plan.get("warnings") or [],
            status=plan.get("status") or "WARN",
        )

    def route_demonstration_guide(self, route_name: str, guide_dir: str | Path | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        guide = task_script_api.get_route_demonstration_guide(route_name, guide_dir=guide_dir)
        return _query_response(
            "knowledge_fabric_route_demonstration_guide.v1",
            guide,
            started=started,
            source="task_script_api+route_demonstration",
            freshness=self.freshness(),
            warnings=guide.get("warnings") or [],
            status=guide.get("status") or "WARN",
        )

    def route_guide_progress(
        self,
        route_name: str,
        current_world: dict[str, Any],
        guide_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        progress = task_script_api.get_route_guide_progress(route_name, current_world, guide_dir=guide_dir)
        return _query_response(
            "knowledge_fabric_route_guide_progress.v1",
            progress,
            started=started,
            source="task_script_api+route_demonstration",
            freshness=self.freshness(),
            warnings=progress.get("warnings") or [],
            status=progress.get("status") or "WARN",
        )

    def route_guide_reentry(
        self,
        route_name: str,
        current_world: dict[str, Any],
        guide_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        reentry = task_script_api.get_route_guide_reentry(route_name, current_world, guide_dir=guide_dir)
        return _query_response(
            "knowledge_fabric_route_guide_reentry.v1",
            reentry,
            started=started,
            source="task_script_api+route_demonstration",
            freshness=self.freshness(),
            warnings=reentry.get("warnings") or [],
            status=reentry.get("status") or "WARN",
        )

    def validate_task_script(self, script: dict[str, Any] | str | Path) -> dict[str, Any]:
        return task_script_api.validate_task_script(script)

    def compile_task_script(self, script: dict[str, Any] | str | Path) -> dict[str, Any]:
        return task_script_api.compile_task_script(script)

    def explain_script_plan(self, script: dict[str, Any] | str | Path) -> dict[str, Any]:
        return task_script_api.explain_script_plan(script)

    def task_script_evidence_plan(self, script: dict[str, Any] | str | Path) -> dict[str, Any]:
        return task_script_api.build_task_script_evidence_plan(script)

    def _task_script_runtime_variables(self, *, visibility: dict[str, Any] | None = None, readiness: dict[str, Any] | None = None) -> dict[str, Any]:
        status = _status_view(self.daemon_status)
        brain = _dict(status.get("brain"))
        generic = _dict(brain.get("genericTaskState") or status.get("genericTaskState"))
        inventory = _compact_inventory(status)
        route = _compact_route_context(status)
        location = _compact_location(status)
        phase = _compact_phase(status)
        hot = _dict(status.get("clientTickHot"))
        hover = _dict(hot.get("postMenuSort")) or _dict(hot.get("hoverMenu"))
        last_click = _dict(hot.get("lastMenuOptionClicked"))
        route_monitor_status = _dict(
            status.get("routeMonitor")
            or status.get("route_monitor")
            or status.get("routeMonitorStatus")
            or status.get("route_monitor_status")
        )
        bank_ui = _dict(brain.get("bankUiContext") or status.get("bankUiContext") or status.get("bankUi"))
        combat_state = _dict(brain.get("combatState") or brain.get("combat_state") or status.get("combatState") or status.get("combat_state"))
        bank_open = _first_present(bank_ui.get("bankOpen"), bank_ui.get("bank_open"), status.get("bankOpen"), generic.get("bankOpen"))
        deposit_box_open = _first_present(bank_ui.get("depositBoxOpen"), bank_ui.get("deposit_box_open"), status.get("depositBoxOpen"))
        bank_state = {
            "bankOpen": bank_open,
            "depositBoxOpen": deposit_box_open,
            "activeBankLikeInterface": _first_present(bank_ui.get("activeBankLikeInterface"), "deposit_box" if deposit_box_open is True else None, "bank" if bank_open is True else None),
            "bankContainerAvailable": _first_present(bank_ui.get("bankContainerAvailable"), bank_ui.get("bankContainerVisible"), bank_ui.get("bankReadable")),
            "bankContainerDeltaAvailable": bool(_dict(bank_ui.get("bankContainerDelta")).get("available")),
            "bankUiPresent": bool(bank_ui),
            "missingCapabilities": [] if bank_ui else ["banking.bankUi"],
            "warnings": [] if bank_ui else ["bank_ui context missing from daemon status"],
        }
        banking_lifecycle_value = {
            "status": "PASS" if bank_state["bankOpen"] is True and bank_state["bankContainerAvailable"] else ("WARN" if bank_state["bankOpen"] is True else "FAIL"),
            "phase": "bank_open" if bank_state["bankOpen"] is True else "unknown",
            "confidence": 0.7 if bank_state["bankOpen"] is True else 0.0,
            **bank_state,
        }
        visibility_data = _dict(visibility)
        readiness_data = _dict(readiness)
        action_need = _dict(readiness_data.get("actionNeed"))
        loaded_scene = _dict(readiness_data.get("loadedSceneProof"))
        input_integrity = visibility_data.get("input_integrity_status") or self._input_integrity_summary()
        deposit_result = {
            "depositComplete": _first_present(action_need.get("bankingComplete"), generic.get("bankingComplete")),
            "depositedItems": [],
            "depositConfirmationLevel": "live_state_only",
            "bankContainerDeltaAvailable": bank_state["bankContainerDeltaAvailable"],
            "confidence": banking_lifecycle_value["confidence"],
            "missingCapabilities": bank_state["missingCapabilities"],
            "warnings": bank_state["warnings"],
        }
        combat_summary = {
            "inCombat": combat_state.get("inCombat"),
            "playerInteracting": combat_state.get("playerInteracting"),
            "actorsInteractingWithPlayer": combat_state.get("actorsInteractingWithPlayer") or [],
            "nearbyHostileNpcs": combat_state.get("nearbyHostileNpcs") or [],
            "recentHitsplats": combat_state.get("recentHitsplats") or [],
            "recentStatChanges": combat_state.get("recentStatChanges") or [],
            "recentChatMessages": combat_state.get("recentChatMessages") or [],
            "playerHealth": combat_state.get("playerHealth"),
            "missingCapabilities": [] if combat_state else ["combat_state"],
            "warnings": [] if combat_state else ["combat_state context missing from daemon status"],
        }
        interruption_value = {
            "interruptionDetected": bool(combat_summary["inCombat"] or combat_summary["actorsInteractingWithPlayer"] or combat_summary["recentHitsplats"]),
            "interruptionType": "combat" if bool(combat_summary["inCombat"] or combat_summary["actorsInteractingWithPlayer"] or combat_summary["recentHitsplats"]) else "unknown",
            "primaryCause": "hostile_npc" if combat_summary["actorsInteractingWithPlayer"] or combat_summary["nearbyHostileNpcs"] else ("player_combat" if combat_summary["inCombat"] else "unknown"),
            "combatObserved": bool(combat_summary["inCombat"] or combat_summary["actorsInteractingWithPlayer"] or combat_summary["recentHitsplats"]),
            "hitsplatsSeen": len(combat_summary["recentHitsplats"]),
            "confidence": 0.75 if combat_state else 0.0,
            "missingCapabilities": combat_summary["missingCapabilities"],
            "warnings": combat_summary["warnings"],
        }
        combat_damage_value = task_script_api.get_combat_damage_summary(
            {
                "combat_state": combat_state,
                "interruption_lifecycle": interruption_value,
            }
        ) if combat_state else {
            "status": "FAIL",
            "combatObserved": False,
            "damageTakenTotal": None,
            "damageDealtTotal": None,
            "hitsplatCount": 0,
            "missingCapabilities": ["combat.damageSummary"],
            "warnings": ["combat_state context missing from daemon status"],
        }
        woodcutting_loop_value = task_script_api.get_woodcutting_loop_lifecycle(
            {
                "woodcutting_lifecycle": {
                    "schema": "woodcutting_lifecycle.v1",
                    "status": "PASS" if inventory.get("resourceCount") is not None or inventory.get("inventoryFull") is not None else "WARN",
                    "phase": "inventory_full" if inventory.get("inventoryFull") is True else _first_present(phase.get("cycleStage"), phase.get("phase"), "unknown"),
                    "confidence": 0.55 if inventory else 0.0,
                    "inventory": {
                        "freeSlotsStart": inventory.get("freeSlots"),
                        "freeSlotsEnd": inventory.get("freeSlots"),
                        "normalLogsStart": inventory.get("resourceCount"),
                        "normalLogsEnd": inventory.get("resourceCount"),
                        "normalLogsGained": 0,
                        "inventoryFull": inventory.get("inventoryFull"),
                    },
                    "clicks": {"freshChopClickCount": 0},
                    "animation": {"activeSnapshotCount": 0},
                    "current": {"freeSlots": inventory.get("freeSlots"), "inventoryFull": inventory.get("inventoryFull")},
                },
                "banking_lifecycle": {
                    "schema": "banking_lifecycle.v1",
                    "status": banking_lifecycle_value.get("status"),
                    "phase": banking_lifecycle_value.get("phase"),
                    "confidence": banking_lifecycle_value.get("confidence"),
                    "bank": {
                        "openSeen": bank_state.get("bankOpen"),
                        "depositBoxOpenSeen": bank_state.get("depositBoxOpen"),
                        "containerAvailable": bank_state.get("bankContainerAvailable"),
                        "bankUiPresent": bank_state.get("bankUiPresent"),
                        "bankContainerDeltaAvailable": bank_state.get("bankContainerDeltaAvailable"),
                    },
                    "deposit": {
                        "detected": deposit_result.get("depositComplete") is True,
                        "items": deposit_result.get("depositedItems") or [],
                        "totalDepositedCount": sum(
                            (_dict(item).get("quantity") or 0)
                            if isinstance(_dict(item).get("quantity") or 0, (int, float))
                            else 0
                            for item in deposit_result.get("depositedItems") or []
                        ),
                        "confirmationLevel": deposit_result.get("depositConfirmationLevel"),
                    },
                },
                "interruption_lifecycle": interruption_value,
                "combat_damage_summary": combat_damage_value,
            }
        )
        route_monitor_value = task_script_api.get_route_monitor_status(
            {"route_monitor": route_monitor_status}
        ) if route_monitor_status else {
            "schema": "route_monitor_status.v1",
            "status": "WARN",
            "routeState": _first_present(route.get("routeState"), route.get("routeStepStatus"), "unknown"),
            "currentArea": route.get("currentArea"),
            "nextExpectedSegment": route.get("nextExpectedSegment"),
            "offRoute": route.get("offRoute"),
            "warnings": ["route monitor status was not present in live daemon status"],
            "missingCapabilities": ["route_monitor"],
        }
        human_click_profile_value = task_script_api.get_human_click_profile()

        def observed(value: Any) -> bool:
            if isinstance(value, dict):
                return any(observed(item) for item in value.values())
            if isinstance(value, list):
                return bool(value)
            return value is not None

        menu_evidence = visibility_data.get("menuOptionClickedEvidence") or last_click
        hover_evidence = visibility_data.get("hoverConfirmationEvidence") or {
            "topOption": _first_present(hover.get("topOption"), hover.get("option"), hot.get("topOption")),
            "topTarget": _first_present(hover.get("topTarget"), hover.get("target"), hot.get("topTarget")),
            "source": hot.get("sourceEvent") or hot.get("sampleSource"),
        }
        phase_intent = {
            **phase,
            "proposedAction": _first_present(readiness_data.get("proposedAction"), visibility_data.get("plannedAction")),
            "readinessStatus": readiness_data.get("status"),
            "executionAllowed": _dict(readiness_data.get("actionReadiness")).get("executionAllowed"),
            "needsService": action_need.get("needsService"),
            "bankingComplete": action_need.get("bankingComplete"),
        }
        variables = {
            "inventory": {"observed": observed(inventory), "value": inventory, "source": "compact_inventory"},
            "resourceCount": {
                "observed": inventory.get("resourceCount") is not None or action_need.get("resourceCount") is not None,
                "value": _first_present(inventory.get("resourceCount"), action_need.get("resourceCount")),
                "source": "inventorySummary/actionNeed",
            },
            "bankOpen": {"observed": bank_open is not None, "value": bank_open, "source": "bankUiContext/status"},
            "bankState": {"observed": observed(bank_state), "value": bank_state, "source": "bankUiContext/status"},
            "bankingLifecycle": {"observed": observed(banking_lifecycle_value), "value": banking_lifecycle_value, "source": "bankUiContext/status"},
            "combatState": {"observed": observed(combat_state), "value": combat_summary, "source": "combatState/status"},
            "interruptionLifecycle": {"observed": observed(combat_state), "value": interruption_value, "source": "combatState/status"},
            "combatDamageSummary": {"observed": observed(combat_state), "value": combat_damage_value, "source": "combatState/status"},
            "woodcuttingLoopLifecycle": {"observed": observed(woodcutting_loop_value), "value": woodcutting_loop_value, "source": "inventory/bank/combat live summaries"},
            "inventoryDelta": {"observed": bool(action_need.get("resourceCount") is not None or inventory), "value": {"inventory": inventory, "resourceCount": action_need.get("resourceCount")}, "source": "inventorySummary/actionNeed"},
            "depositResult": {"observed": deposit_result.get("depositComplete") is not None or bank_state["bankOpen"] is True, "value": deposit_result, "source": "genericTaskState/actionNeed/bankUiContext"},
            "menuOptionClicked": {"observed": observed(menu_evidence), "value": menu_evidence, "source": "clientTickHot/actionTrace"},
            "hoverTarget": {"observed": observed(hover_evidence), "value": hover_evidence, "source": "PostMenuSort/actionTrace"},
            "location": {"observed": observed(location.get("worldLocation")), "value": location, "source": "playerLocation"},
            "routeProgress": {"observed": observed(route), "value": route, "source": "serviceRouteContext/pathingContext"},
            "routeMonitor": {"observed": observed(route_monitor_status), "value": route_monitor_value, "source": "routeMonitor/status"},
            "phaseIntent": {"observed": observed(phase_intent), "value": phase_intent, "source": "genericTaskState/readiness"},
            "loadedScene": {"observed": observed(loaded_scene), "value": loaded_scene, "source": "readiness.loadedSceneProof"},
            "humanClickProfile": {
                "observed": human_click_profile_value.get("status") not in {None, "FAIL"},
                "value": human_click_profile_value,
                "source": "knowledge_base/human_click_profile.json",
            },
            "inputIntegrity": {"observed": observed(input_integrity), "value": input_integrity, "source": "input_integrity/action_visibility"},
        }
        return variables

    def _task_runtime_evidence_integrity(
        self,
        variables: dict[str, Any],
        *,
        readiness: dict[str, Any],
        live_validation_possible: bool,
    ) -> dict[str, Any]:
        loaded_scene = _dict(readiness.get("loadedSceneProof"))
        manual_login = readiness.get("manualLoginRequired") is True
        loaded_scene_verified = loaded_scene.get("loadedSceneVerified") is True
        action_readiness = _dict(readiness.get("actionReadiness"))
        proof_blockers: list[str] = []
        if manual_login:
            proof_blockers.append("manual_login_required")
        if not loaded_scene_verified:
            proof_blockers.append("loaded_scene_not_verified")
        if action_readiness.get("executionAllowed") is False:
            proof_blockers.append("action_readiness_blocked")
        lifecycle_variables = {
            "inventory",
            "resourceCount",
            "bankOpen",
            "menuOptionClicked",
            "hoverTarget",
            "location",
            "routeProgress",
            "routeMonitor",
            "phaseIntent",
            "humanClickProfile",
        }
        variable_integrity: dict[str, Any] = {}
        advisory_variables: list[str] = []
        proof_eligible_variables: list[str] = []
        for name, wrapper in variables.items():
            observed = _dict(wrapper).get("observed") is True
            advisory_only = bool(name in lifecycle_variables and proof_blockers)
            proof_eligible = bool(observed and not advisory_only)
            if advisory_only:
                advisory_variables.append(name)
            if proof_eligible:
                proof_eligible_variables.append(name)
            variable_integrity[name] = {
                "observed": observed,
                "advisoryOnly": advisory_only,
                "proofEligibleNow": proof_eligible,
                "proofBlockers": list(proof_blockers) if advisory_only else [],
                "source": _dict(wrapper).get("source"),
                "rule": (
                    "Value is context-only until loaded scene and action readiness are fresh."
                    if advisory_only
                    else "Value may participate in proof if compared against a fresh before/after snapshot."
                ),
            }
        status = "WARN" if proof_blockers or advisory_variables else "PASS"
        return {
            "schema": "task_runtime_evidence_integrity.v1",
            "status": status,
            "loadedSceneVerified": loaded_scene_verified,
            "manualLoginRequired": manual_login,
            "livenessState": readiness.get("livenessState"),
            "liveValidationPossibleNow": live_validation_possible,
            "proofBlockers": list(dict.fromkeys(proof_blockers)),
            "advisoryVariables": advisory_variables,
            "proofEligibleVariables": proof_eligible_variables,
            "variableIntegrity": variable_integrity,
            "rule": "Runtime values remain visible, but lifecycle variables cannot prove live progress while liveness/readiness is blocked.",
            "noLiveInput": True,
        }

    def query_task_script_runtime_evidence(self, script: dict[str, Any] | str | Path | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        script_payload = script if script is not None else task_script_api.woodcut_bank_template()
        evidence_plan = task_script_api.build_task_script_evidence_plan(script_payload)
        readiness = self._readiness_report()
        visibility_response = self.query_action_input_visibility()
        visibility_data = _dict(visibility_response.get("data"))
        variables = self._task_script_runtime_variables(visibility=visibility_data, readiness=readiness)
        covered = _list(_dict(evidence_plan.get("data")).get("coveredVariables"))
        observed = [name for name in covered if _dict(variables.get(name)).get("observed") is True]
        missing_observations = [name for name in covered if name not in observed]
        action_readiness = _dict(readiness.get("actionReadiness"))
        blockers = _list(action_readiness.get("blockers"))
        manual_login = bool(readiness.get("manualLoginRequired") is True)
        live_validation_possible = bool(action_readiness.get("executionAllowed") is True and not manual_login)
        evidence_integrity = self._task_runtime_evidence_integrity(
            variables,
            readiness=readiness,
            live_validation_possible=live_validation_possible,
        )
        data = {
            "scriptEvidencePlan": _dict(evidence_plan.get("data")),
            "runtimeVariables": variables,
            "coveredVariablesObservedNow": observed,
            "coveredVariablesMissingNow": missing_observations,
            "runtimeEvidenceIntegrity": evidence_integrity,
            "proofEligibleVariablesNow": evidence_integrity.get("proofEligibleVariables"),
            "advisoryVariablesNow": evidence_integrity.get("advisoryVariables"),
            "readinessSummary": {
                "status": readiness.get("status"),
                "ready": readiness.get("ready"),
                "proposedAction": readiness.get("proposedAction"),
                "currentIntent": readiness.get("currentIntent"),
                "livenessState": readiness.get("livenessState"),
                "manualLoginRequired": readiness.get("manualLoginRequired"),
                "loadedSceneProof": readiness.get("loadedSceneProof"),
                "blockers": blockers,
            },
            "actionInputVisibilitySummary": {
                "schema": visibility_response.get("schema"),
                "status": visibility_response.get("status"),
                "plannedAction": visibility_data.get("plannedAction"),
                "plannedTarget": visibility_data.get("plannedTarget"),
                "hoverConfirmationEvidence": visibility_data.get("hoverConfirmationEvidence"),
                "menuOptionClickedEvidence": visibility_data.get("menuOptionClickedEvidence"),
                "input_integrity_status": visibility_data.get("input_integrity_status"),
                "directBackendBypassCount": visibility_data.get("directBackendBypassCount"),
                "blockedReason": visibility_data.get("blockedReason"),
            },
            "snapshotProtocol": _dict(_dict(evidence_plan.get("data")).get("snapshotProtocol")),
            "liveValidationPossibleNow": live_validation_possible,
            "noLiveInput": True,
        }
        warnings = []
        if manual_login:
            warnings.append("manual_login_required")
        if evidence_integrity.get("status") == "WARN":
            warnings.append("runtime_evidence_advisory_until_loaded_scene_verified")
        warnings.extend(f"missing_runtime_variable:{name}" for name in missing_observations)
        return _query_response(
            task_script_api.TASK_RUNTIME_EVIDENCE_SCHEMA,
            data,
            started=started,
            source="knowledge_fabric+readiness+action_input_visibility",
            freshness=self.freshness(),
            warnings=warnings,
            status="WARN" if warnings or action_readiness.get("executionAllowed") is not True else "PASS",
        )

    def compare_task_script_runtime_evidence(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        *,
        script: dict[str, Any] | str | Path | None = None,
        primitive: str | None = None,
    ) -> dict[str, Any]:
        return task_script_api.compare_task_runtime_evidence_snapshots(before, after, script=script, primitive=primitive)

    def classify_task_failure(
        self,
        evidence: dict[str, Any] | None = None,
        *,
        current_blocker: dict[str, Any] | None = None,
        debug_context: dict[str, Any] | None = None,
        runtime_evidence: dict[str, Any] | None = None,
        comparison: dict[str, Any] | None = None,
        action_input_visibility: dict[str, Any] | None = None,
        action_trace: dict[str, Any] | None = None,
        external_knowledge: dict[str, Any] | None = None,
        error_text: str | None = None,
    ) -> dict[str, Any]:
        if evidence is None and not any(
            item is not None
            for item in (
                current_blocker,
                debug_context,
                runtime_evidence,
                comparison,
                action_input_visibility,
                action_trace,
                external_knowledge,
                error_text,
            )
        ):
            evidence = {
                "currentBlocker": self.explain_current_blocker(),
                "debugContext": self.query_current_debug_context(),
                "runtimeEvidence": self.query_task_script_runtime_evidence(),
                "actionInputVisibility": self.query_action_input_visibility(),
                "externalKnowledge": _external_summary_compact(),
            }
        return task_script_api.classify_task_failure(
            evidence,
            current_blocker=current_blocker,
            debug_context=debug_context,
            runtime_evidence=runtime_evidence,
            comparison=comparison,
            action_input_visibility=action_input_visibility,
            action_trace=action_trace,
            external_knowledge=external_knowledge,
            error_text=error_text,
        )

    def assess_task_script_step(
        self,
        script: dict[str, Any] | str | Path | None = None,
        *,
        step_index: Any = None,
        primitive: str | None = None,
        runtime_evidence: dict[str, Any] | None = None,
        action_input_visibility: dict[str, Any] | None = None,
        failure_classification: dict[str, Any] | None = None,
        navigation_decision_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        script_payload = script if script is not None else task_script_api.woodcut_bank_template()
        runtime = runtime_evidence if runtime_evidence is not None else self.query_task_script_runtime_evidence(script_payload)
        visibility = action_input_visibility if action_input_visibility is not None else self.query_action_input_visibility()
        navigation = navigation_decision_trace if navigation_decision_trace is not None else self.query_navigation_decision_trace()
        failure = failure_classification if failure_classification is not None else self.classify_task_failure(
            {
                "runtimeEvidence": runtime,
                "actionInputVisibility": visibility,
                "navigationDecisionTrace": navigation,
            }
        )
        return task_script_api.assess_task_step_readiness(
            script_payload,
            step_index=step_index,
            primitive=primitive,
            runtime_evidence=runtime,
            action_input_visibility=visibility,
            failure_classification=failure,
            navigation_decision_trace=navigation,
        )

    def assess_task_script_run(
        self,
        script: dict[str, Any] | str | Path | None = None,
        *,
        runtime_evidence: dict[str, Any] | None = None,
        action_input_visibility: dict[str, Any] | None = None,
        failure_classification: dict[str, Any] | None = None,
        navigation_decision_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        script_payload = script if script is not None else task_script_api.woodcut_bank_template()
        runtime = runtime_evidence if runtime_evidence is not None else self.query_task_script_runtime_evidence(script_payload)
        visibility = action_input_visibility if action_input_visibility is not None else self.query_action_input_visibility()
        navigation = navigation_decision_trace if navigation_decision_trace is not None else self.query_navigation_decision_trace()
        failure = failure_classification if failure_classification is not None else self.classify_task_failure(
            {
                "runtimeEvidence": runtime,
                "actionInputVisibility": visibility,
                "navigationDecisionTrace": navigation,
            }
        )
        return task_script_api.assess_task_run_readiness(
            script_payload,
            runtime_evidence=runtime,
            action_input_visibility=visibility,
            failure_classification=failure,
            navigation_decision_trace=navigation,
        )

    def suggest_task_template(self, task_description: str | None = None, *, profile: str | None = None) -> dict[str, Any]:
        return task_script_api.suggest_task_template(task_description, profile=profile)

    def probe_task_from_scene(
        self,
        task_description: str,
        *,
        profile: str = "woodcutting",
        limit: int | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        probe = self.probe_task(task_description, profile=profile, limit=limit)
        template = self.suggest_task_template(task_description, profile=profile)
        data = {
            "taskDescription": task_description,
            "profile": profile,
            "taskProbe": probe.get("data"),
            "suggestedTemplate": _dict(template.get("data")).get("template"),
            "templateValidation": _dict(template.get("data")).get("validation"),
            "externalKnowledgePolicy": task_script_api.EXTERNAL_KNOWLEDGE_POLICY,
            "liveTruth": "RuneLite / 8893 / WorldModel / 8890",
            "noLiveInput": True,
        }
        return _query_response(
            "task_scene_probe.v1",
            data,
            started=started,
            source="knowledge_fabric+task_script_api+external_cache",
            freshness=self.freshness(),
            status="PASS" if probe.get("status") != "FAIL" and template.get("status") != "FAIL" else "WARN",
        )

    def data_quality_report(self, *, limit: int | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        expected = [
            "world_model_summary",
            "scene_object_census",
            "resource_object_census",
            "service_object_census",
            "route_object_census",
            "pathing_frontier",
            "projection_audit",
            "view_quality_inputs",
        ]
        quality = world_model_core.world_model_quality(self.world_model_payloads)
        status = self.status()
        missing = [key for key in expected if not _dict(self.world_model_payloads.get(key))]
        cap_hits = _cap_flags(self.world_model_payloads)
        cap_hits.extend({"path": f"status.capWarnings[{index}]", "field": warning, "value": True} for index, warning in enumerate(_list(status.get("capWarnings"))))
        sample_queries = {
            "worldModelSummary": self.query_world_summary(),
            "resourceCandidates": self.query_resource_candidates(limit=limit),
            "serviceCandidates": self.query_service_candidates(limit=limit),
            "routeObjects": self.query_route_objects(limit=limit),
            "pathingFrontier": self.query_path_frontier(limit=limit),
            "viewQuality": self.query_view_quality(),
        }
        query_failures = [
            {"query": name, "status": payload.get("status"), "warnings": payload.get("warnings")}
            for name, payload in sample_queries.items()
            if payload.get("status") == "FAIL"
        ]
        world_fresh = quality.get("worldModelAvailable") is True
        object_count = len(self.objects)
        collision_available = bool(quality.get("collisionAvailable") or quality.get("worldModelCollisionAvailable") or _dict(self.world_model_payloads.get("pathing_frontier")))
        confidence = "high"
        if not world_fresh or object_count <= 0:
            confidence = "low"
        elif missing or cap_hits or query_failures:
            confidence = "medium"
        recommended = []
        if not world_fresh:
            recommended.append("Verify 8893 health, loaded scene packets, and daemon binding.")
        if object_count <= 0:
            recommended.append("Recover a loaded RuneLite scene before diagnosing object/pathing behavior.")
        if missing:
            recommended.append("Request or capture the missing world-model sections for this task.")
        if cap_hits:
            recommended.append("Use narrower filters or explicit debug/full snapshot for capped sections.")
        if not collision_available:
            recommended.append("Request collision/pathing frontier before route/pathing diagnosis.")
        if not recommended:
            recommended.append("Data quality is sufficient for query-first debugging.")
        external_status = external_knowledge.knowledge_status()
        external_data = _dict(external_status.get("data"))
        bootstrap_state = self._bootstrap_liveness_summary()
        data = {
            "worldModelFresh": world_fresh,
            "clientTickFresh": _client_tick_fresh(self.daemon_status),
            "loadedSceneVerified": bootstrap_state.get("loadedSceneVerified"),
            "bootstrapState": bootstrap_state.get("state"),
            "bootstrapRecommended": bootstrap_state.get("bootstrapRecommended"),
            "daemonFresh": self.daemon_status.get("fresh") if "fresh" in self.daemon_status else self.daemon_status.get("daemonFresh"),
            "pluginFresh": self.daemon_status.get("pluginSnapshotFresh") or self.daemon_status.get("pluginSnapshotAvailable"),
            "objectCount": object_count,
            "collisionAvailable": collision_available,
            "projectionAuditAvailable": bool(_dict(self.world_model_payloads.get("projection_audit")) or quality.get("projectionAuditAvailable")),
            "livePacketsRuntimeRemoved": True,
            "ndjsonRuntimeRemoved": True,
            "jsonlRuntimeRemoved": True,
            "livePacketWriterActive": False,
            "legacyLivePacketFilesPresent": self.daemon_status.get("legacyLivePacketFilesPresent"),
            "legacyLivePacketTotalMb": self.daemon_status.get("legacyLivePacketTotalMb"),
            "cleanupRecommended": self.daemon_status.get("cleanupRecommended"),
            "externalKnowledgeEnabled": external_data.get("externalKnowledgeEnabled"),
            "externalCacheSizeMb": external_data.get("cacheSizeMb"),
            "externalCacheFreshness": _dict(_dict(external_data.get("sourceInventory")).get("sourceStatus")).get("lastRefresh"),
            "externalSourcesHealthy": external_data.get("externalSourcesHealthy"),
            "externalCacheMisses": [],
            "externalApiDisabledReason": external_data.get("externalApiDisabledReason"),
            "externalRateLimitBackoff": external_data.get("externalRateLimitBackoff"),
            "capHits": cap_hits,
            "truncationWarnings": [item for item in _list(status.get("capWarnings")) if item],
            "staleSources": status.get("staleSources") or [],
            "missingExpectedSections": missing,
            "queryFailures": query_failures,
            "responseSizes": {name: _perf_summary(payload).get("responseBytes") for name, payload in sample_queries.items()},
            "queryTimes": {name: _perf_summary(payload).get("queryTimeMs") for name, payload in sample_queries.items()},
            "confidence": confidence,
            "recommendedFixes": recommended,
        }
        return _query_response(
            DATA_QUALITY_SCHEMA,
            data,
            started=started,
            source=self.source,
            freshness=self.freshness(),
            warnings=[f"missing:{key}" for key in missing] + [f"cap:{item.get('path')}" for item in cap_hits[:10]],
            cap_hit=bool(cap_hits),
            truncated=bool(cap_hits),
            status="PASS" if confidence == "high" else "WARN",
        )

    def _artifact_root(self, kind: str, output_root: str | Path | None = None) -> Path:
        if output_root:
            return Path(output_root)
        if self.session_path is not None:
            return self.session_path / LIVE_DIR / kind
        return Path.cwd() / LIVE_DIR / kind

    def _overlay_debug_state(self) -> dict[str, Any]:
        if self.session_path is None:
            return {}
        path = self.session_path / LIVE_DIR / "overlay_debug_state.json"
        return _read_json(path) if path.exists() else {}

    def _copy_latest_screenshot(self, bundle_dir: Path) -> tuple[str | None, str]:
        latest = self._latest_visual_bundle_summary()
        source = latest.get("screenshotPath")
        if not source:
            return None, "missing_latest_debug_screenshot"
        source_path = Path(str(source))
        if not source_path.exists():
            return None, "latest_debug_screenshot_path_missing"
        target = bundle_dir / "screenshot.png"
        try:
            shutil.copy2(source_path, target)
        except Exception as error:  # noqa: BLE001
            return None, f"screenshot_copy_failed:{type(error).__name__}"
        return str(target), "copied_latest_debug_screenshot"

    def _static_excerpts(self, limit: int | None) -> dict[str, Any]:
        cap = _safe_limit(limit)
        return {
            "target_library_excerpt": {
                "schema": "target_library_excerpt.v1",
                "items": _list(self.static_library.get("targetLibrary"))[:cap],
                "count": len(_list(self.static_library.get("targetLibrary"))),
            },
            "target_profiles_excerpt": {
                "schema": "target_profiles_excerpt.v1",
                "items": _list(self.static_library.get("targetProfiles"))[:cap],
                "count": len(_list(self.static_library.get("targetProfiles"))),
            },
            "service_routes_excerpt": {
                "schema": "service_routes_excerpt.v1",
                "items": _list(self.static_library.get("routes"))[:cap],
                "count": len(_list(self.static_library.get("routes"))),
            },
        }

    def capture_script_authoring_context(
        self,
        *,
        profile: str = "woodcutting",
        task_name: str | None = None,
        reason: str | None = None,
        limit: int | None = None,
        output_root: str | Path | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        cap = _safe_limit(limit)
        label = _slug(task_name or profile or reason or "script_authoring_context")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        bundle_dir = self._artifact_root("script_authoring_context", output_root) / f"{timestamp}_{label}"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        current_context = self.query_current_debug_context(profile=profile, limit=cap)
        blocker = self.explain_current_blocker()
        data_quality = self.data_quality_report(limit=cap)
        pathing_frontier = self.query_path_frontier(limit=cap)
        view_quality = self.query_view_quality(intent=str(_dict(current_context.get("data")).get("currentIntent") or profile))
        static_excerpts = self._static_excerpts(cap)
        files: dict[str, str] = {}
        payloads: dict[str, dict[str, Any]] = {
            "current_debug_context.json": current_context,
            "explain_current_blocker.json": blocker,
            "world_model_summary.json": self.query_world_summary(),
            "knowledge_fabric_status.json": self.status(),
            "scene_object_census.json": _dict(self.world_model_payloads.get("scene_object_census")),
            "resource_candidates.json": self.query_resource_candidates(profile=profile, limit=cap),
            "route_objects.json": self.query_route_objects(limit=cap),
            "service_objects.json": self.query_service_candidates(limit=cap),
            "pathing_frontier.json": pathing_frontier,
            "collision_summary.json": {
                "schema": "collision_summary.v1",
                "collisionAvailable": _dict(data_quality.get("data")).get("collisionAvailable"),
                "pathingFrontier": _dict(pathing_frontier.get("data")).get("frontier"),
            },
            "projection_audit.json": _dict(self.world_model_payloads.get("projection_audit")),
            "view_quality.json": view_quality,
            "overlay_debug_state.json": self._overlay_debug_state(),
            "input_integrity_status.json": self._input_integrity_summary(),
            "latest_action_trace_excerpt.json": self._latest_action_trace_summary(),
            "session_memory_summary.json": self.session_memory,
            "static_library_summary.json": self.static_library.get("summary", {}),
            "target_library_excerpt.json": static_excerpts["target_library_excerpt"],
            "target_profiles_excerpt.json": static_excerpts["target_profiles_excerpt"],
            "service_routes_excerpt.json": static_excerpts["service_routes_excerpt"],
            "data_quality_report.json": data_quality,
            "coverage_report.json": self.coverage_report(intent=str(_dict(current_context.get("data")).get("currentIntent") or profile), limit=cap),
            "external_knowledge_status.json": external_knowledge.knowledge_status(),
            "external_source_inventory.json": {"schema": "external_knowledge_sources_resource.v1", "data": external_knowledge.source_inventory()},
        }
        for filename, payload in payloads.items():
            if not isinstance(payload, dict):
                payload = {"schema": "missing_payload.v1", "status": "WARN", "reason": "payload_unavailable"}
            path = bundle_dir / filename
            _write_json(path, payload)
            files[filename] = str(path)
        screenshot_path, screenshot_status = self._copy_latest_screenshot(bundle_dir)
        if screenshot_path:
            files["screenshot.png"] = screenshot_path
        query_payloads = {name: payload for name, payload in payloads.items() if isinstance(payload, dict)}
        phase = _compact_phase(self.daemon_status)
        blocker_data = _dict(blocker.get("data"))
        object_counts = {
            "worldModelObjects": len(self.objects),
            "resourceCandidates": _dict(_dict(payloads["resource_candidates.json"]).get("data")).get("count"),
            "routeObjects": _dict(_dict(payloads["route_objects.json"]).get("data")).get("count"),
            "serviceObjects": _dict(_dict(payloads["service_objects.json"]).get("data")).get("count"),
        }
        manifest = {
            "schema": SCRIPT_AUTHORING_CONTEXT_SCHEMA,
            "createdAt": utc_now(),
            "sessionPath": str(self.session_path) if self.session_path else None,
            "profile": profile,
            "taskName": task_name,
            "reason": reason,
            "playerLocation": _compact_location(self.daemon_status).get("worldLocation"),
            "plane": _compact_location(self.daemon_status).get("plane"),
            "inventorySummary": _compact_inventory(self.daemon_status),
            "currentPhase": phase.get("phase"),
            "currentIntent": _first_present(phase.get("currentIntent"), phase.get("activeIntent"), blocker_data.get("currentIntent")),
            "blockerCategory": blocker_data.get("primaryBlockerCategory"),
            "objectCounts": object_counts,
            "capWarnings": self.status().get("capWarnings") or [],
            "staleWarnings": self.status().get("staleSources") or [],
            "queryTimes": {name: _perf_summary(payload).get("queryTimeMs") for name, payload in query_payloads.items()},
            "responseSizes": {name: _perf_summary(payload).get("responseBytes") for name, payload in query_payloads.items()},
            "recommendedNextSteps": [blocker_data.get("recommendedNextStep"), *_list(_dict(data_quality.get("data")).get("recommendedFixes"))],
            "files": files,
            "screenshotStatus": screenshot_status,
        }
        files["manifest.json"] = str(bundle_dir / "manifest.json")
        _write_json(bundle_dir / "manifest.json", manifest)
        return _query_response(
            SCRIPT_AUTHORING_CAPTURE_SCHEMA,
            {"bundlePath": str(bundle_dir), "manifest": manifest, "files": files},
            started=started,
            source=self.source,
            freshness=self.freshness(),
            cap_hit=any(bool(_dict(payload).get("capHit")) for payload in payloads.values() if isinstance(payload, dict)),
            truncated=any(bool(_dict(payload).get("truncated")) for payload in payloads.values() if isinstance(payload, dict)),
        )

    def capture_replay_scenario(
        self,
        *,
        profile: str = "woodcutting",
        reason: str | None = None,
        limit: int | None = None,
        output_root: str | Path | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        cap = _safe_limit(limit)
        label = _slug(reason or profile or "replay_scenario")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        scenario_dir = self._artifact_root("replay_scenarios", output_root) / f"{timestamp}_{label}"
        scenario_dir.mkdir(parents=True, exist_ok=True)
        scenario = {
            "schema": REPLAY_SCENARIO_SCHEMA,
            "createdAt": utc_now(),
            "sessionPath": str(self.session_path) if self.session_path else None,
            "profile": profile,
            "reason": reason,
            "daemonStatus": self.daemon_status,
            "worldModelPayloads": self.world_model_payloads,
            "staticLibrary": {
                "summary": self.static_library.get("summary", {}),
                "versionHash": self.static_library.get("versionHash"),
                **self._static_excerpts(cap),
            },
            "sessionMemory": self.session_memory,
            "currentDebugContext": self.query_current_debug_context(profile=profile, limit=cap),
            "explainCurrentBlocker": self.explain_current_blocker(),
            "readiness": self._readiness_report(),
            "actionProposal": self._action_proposal(),
            "routeContext": _compact_route_context(self.daemon_status),
            "serviceAnchorContext": self.query_service_candidates(limit=cap),
            "resourceWorksiteContext": self.query_worksite_context(profile=profile),
            "pathingFrontier": self.query_path_frontier(limit=cap),
            "viewQuality": self.query_view_quality(),
            "overlayHealth": self._readiness_report().get("overlayHealth"),
            "inputIntegritySummary": self._input_integrity_summary(),
            "dataQualityReport": self.data_quality_report(limit=cap),
            "coverageReport": self.coverage_report(limit=cap),
            "externalKnowledgeStatus": external_knowledge.knowledge_status(),
            "noLiveInput": True,
        }
        scenario_path = scenario_dir / "scenario.json"
        _write_json(scenario_path, scenario)
        return _query_response(
            "replay_scenario_capture.v1",
            {"scenarioPath": str(scenario_path), "scenarioDir": str(scenario_dir), "profile": profile, "reason": reason},
            started=started,
            source=self.source,
            freshness=self.freshness(),
        )

    def handoff_summary(self) -> dict[str, Any]:
        started = time.perf_counter()
        blocker = self.explain_current_blocker()
        blocker_data = _dict(blocker.get("data"))
        phase = _compact_phase(self.daemon_status)
        latest_trace = self._latest_action_trace_summary()
        latest_bundle = self._latest_visual_bundle_summary()
        input_integrity = self._input_integrity_summary()
        recommended_query = "query_path_frontier" if blocker_data.get("primaryBlockerCategory") == "route/pathing" else "get_current_debug_context"
        data = {
            "phase": phase.get("phase"),
            "cycleStage": phase.get("cycleStage"),
            "currentIntent": _first_present(phase.get("currentIntent"), phase.get("activeIntent"), blocker_data.get("currentIntent")),
            "currentBlocker": {
                "category": blocker_data.get("primaryBlockerCategory"),
                "summary": blocker_data.get("primaryBlockerSummary"),
                "recommendedNextStep": blocker_data.get("recommendedNextStep"),
            },
            "latestSuccessfulAction": latest_trace if latest_trace.get("classification") in {"success", "resource_progress", "resource_return_progress"} else None,
            "latestFailedOrSkippedAction": latest_trace if latest_trace.get("classification") not in {"success", "resource_progress", "resource_return_progress", None} else latest_bundle,
            "mostRelevantBundlePath": latest_bundle.get("bundleDir"),
            "recommendedNextDiagnosticQuery": recommended_query,
            "recommendedNextCodingTarget": "pathing/frontier route evidence" if blocker_data.get("primaryBlockerCategory") == "route/pathing" else "inspect blocker-specific query output before code changes",
            "safetyInputStatus": input_integrity,
            "testsIfCodeChanges": [
                "python -m py_compile telemetry-viewer\\knowledge_fabric.py telemetry-viewer\\mcp_server.py telemetry-viewer\\context_service.py",
                "python telemetry-viewer\\run_stabilization_suite.py",
            ],
        }
        return _query_response(
            HANDOFF_SUMMARY_SCHEMA,
            data,
            started=started,
            source=self.source,
            freshness=self.freshness(),
            status="PASS" if blocker_data.get("primaryBlockerCategory") == "ready" else "WARN",
        )


def _resolve_context_file(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_dir():
        for name in ("current_debug_context.json", "scenario.json", "manifest.json", "explain_current_blocker.json"):
            child = candidate / name
            if child.exists():
                return child
    return candidate


def _debug_context_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") == "context_response.v1" and isinstance(payload.get("knowledgeCurrentDebugContext"), dict):
        payload = _dict(payload.get("knowledgeCurrentDebugContext"))
    if payload.get("schema") == REPLAY_SCENARIO_SCHEMA:
        payload = _dict(payload.get("currentDebugContext"))
    if payload.get("schema") == SCRIPT_AUTHORING_CONTEXT_SCHEMA:
        return {
            "phase": payload.get("currentPhase"),
            "intent": payload.get("currentIntent"),
            "location": payload.get("playerLocation"),
            "inventory": payload.get("inventorySummary"),
            "blockerCategory": payload.get("blockerCategory"),
            "objectCounts": payload.get("objectCounts"),
            "capWarnings": payload.get("capWarnings"),
        }
    data = _dict(payload.get("data")) if "data" in payload else payload
    live = _dict(data.get("liveStatus"))
    phase = _dict(live.get("phase"))
    blocker = _dict(_dict(data.get("currentBlocker")).get("data"))
    pathing = _dict(_dict(data.get("pathingFrontier")).get("data"))
    route_context = _dict(pathing.get("routeContext"))
    view_quality = _dict(_dict(data.get("viewQuality")).get("data"))
    return {
        "phase": phase.get("phase"),
        "cycleStage": phase.get("cycleStage"),
        "intent": _first_present(phase.get("currentIntent"), phase.get("activeIntent"), blocker.get("currentIntent")),
        "location": _dict(live.get("location")).get("worldLocation"),
        "inventory": live.get("inventory"),
        "blockerCategory": blocker.get("primaryBlockerCategory"),
        "blockerSummary": blocker.get("primaryBlockerSummary"),
        "routeNode": route_context.get("currentNodeId"),
        "routeEdge": route_context.get("nextEdgeType"),
        "resourceCandidateCount": _dict(_dict(data.get("resourceCandidates")).get("data")).get("count"),
        "routeObjectCount": _dict(_dict(data.get("routeObjects")).get("data")).get("count"),
        "serviceObjectCount": _dict(_dict(data.get("serviceObjects")).get("data")).get("count"),
        "capWarnings": _dict(data.get("knowledgeFabricStatus")).get("capWarnings"),
        "viewCameraRecommended": _dict(view_quality.get("cameraRecommendation")).get("recommended"),
        "sessionMemory": data.get("sessionMemorySummary"),
    }


def diff_debug_context(path_a: str | Path, path_b: str | Path) -> dict[str, Any]:
    started = time.perf_counter()
    file_a = _resolve_context_file(path_a)
    file_b = _resolve_context_file(path_b)
    payload_a = _read_json(file_a)
    payload_b = _read_json(file_b)
    snap_a = _debug_context_snapshot(payload_a)
    snap_b = _debug_context_snapshot(payload_b)
    differences: dict[str, dict[str, Any]] = {}
    for key in sorted(set(snap_a) | set(snap_b)):
        if snap_a.get(key) != snap_b.get(key):
            differences[key] = {"before": snap_a.get(key), "after": snap_b.get(key)}
    return _query_response(
        DEBUG_CONTEXT_DIFF_SCHEMA,
        {
            "bundleA": str(file_a),
            "bundleB": str(file_b),
            "snapshotA": snap_a,
            "snapshotB": snap_b,
            "differences": differences,
            "differenceCount": len(differences),
        },
        started=started,
        source="debug_context_files",
        freshness={"sourceFiles": [str(file_a), str(file_b)]},
        status="PASS",
    )


def replay_scenario(path: str | Path, *, limit: int | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    scenario_path = _resolve_context_file(path)
    scenario = _read_json(scenario_path)
    if scenario.get("schema") != REPLAY_SCENARIO_SCHEMA:
        return _query_response(
            REPLAY_RESULT_SCHEMA,
            {"scenarioPath": str(scenario_path), "error": "unsupported or missing replay_scenario.v1"},
            started=started,
            source="replay_scenario",
            freshness={"sourceFile": str(scenario_path)},
            status="FAIL",
            warnings=["replay scenario file is missing or has the wrong schema"],
        )
    static_library = scenario.get("staticLibrary")
    if not isinstance(static_library, dict) or "routes" not in static_library:
        static_library = load_static_library()
    fabric = KnowledgeFabric(
        world_model_payloads=_dict(scenario.get("worldModelPayloads")),
        daemon_status=_dict(scenario.get("daemonStatus")),
        static_library=static_library,
        session_path=scenario.get("sessionPath"),
        source="replay_scenario",
    )
    profile = str(scenario.get("profile") or "woodcutting")
    current_debug_context = fabric.query_current_debug_context(profile=profile, limit=limit)
    blocker = fabric.explain_current_blocker()
    data = {
        "scenarioPath": str(scenario_path),
        "profile": profile,
        "reason": scenario.get("reason"),
        "noLiveInput": True,
        "candidateSelection": {
            "resourceCandidates": fabric.query_resource_candidates(profile=profile, limit=limit),
            "serviceCandidates": fabric.query_service_candidates(limit=limit),
            "routeObjects": fabric.query_route_objects(limit=limit),
        },
        "actionProposal": fabric._action_proposal(),
        "readiness": fabric._readiness_report(),
        "explainCurrentBlocker": blocker,
        "pathingFrontierExplanation": fabric.query_path_frontier(limit=limit),
        "viewQualityExplanation": fabric.query_view_quality(),
        "currentDebugContext": current_debug_context,
        "storedBlockerCategory": _dict(_dict(scenario.get("explainCurrentBlocker")).get("data")).get("primaryBlockerCategory"),
        "replayedBlockerCategory": _dict(blocker.get("data")).get("primaryBlockerCategory"),
        "replayMatchesStoredBlocker": _dict(_dict(scenario.get("explainCurrentBlocker")).get("data")).get("primaryBlockerCategory")
        == _dict(blocker.get("data")).get("primaryBlockerCategory"),
    }
    return _query_response(
        REPLAY_RESULT_SCHEMA,
        data,
        started=started,
        source="replay_scenario",
        freshness={"sourceFile": str(scenario_path), **fabric.freshness()},
        status="PASS",
    )


def latest_artifact(session_path: str | Path | None, kind: str) -> dict[str, Any]:
    started = time.perf_counter()
    root = Path(session_path) / LIVE_DIR / kind if session_path else Path.cwd() / LIVE_DIR / kind
    latest: Path | None = None
    if root.exists():
        dirs = [path for path in root.iterdir() if path.is_dir()]
        if dirs:
            latest = max(dirs, key=lambda item: item.stat().st_mtime)
    data = {"root": str(root), "latestPath": str(latest) if latest else None, "exists": latest is not None}
    if latest:
        manifest = _read_json(latest / "manifest.json")
        scenario = _read_json(latest / "scenario.json")
        data["manifest"] = manifest or scenario
    return _query_response(
        "knowledge_fabric_latest_artifact.v1",
        data,
        started=started,
        source="debug_artifacts",
        freshness={"sessionPath": str(session_path) if session_path else None},
        status="PASS" if latest else "WARN",
        warnings=[] if latest else [f"no {kind} artifacts found"],
    )


def fetch_json(url: str, timeout: float = 1.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=max(0.001, float(timeout))) as response:
            value = json.loads(response.read().decode("utf-8", errors="replace"))
        return value if isinstance(value, dict) else {}
    except Exception as error:  # noqa: BLE001
        return {"schema": "http_fetch_error.v1", "status": "FAIL", "url": url, "error": f"{type(error).__name__}: {error}"}


def fabric_from_live(
    *,
    daemon_url: str = "http://127.0.0.1:8890",
    snapshot_url: str = "http://127.0.0.1:8893/snapshot",
    timeout: float = 1.0,
    include_projection: bool = False,
    include_collision: bool = False,
    max_objects: int = 160,
) -> KnowledgeFabric:
    daemon_status = fetch_json(daemon_url.rstrip("/") + "/status", timeout=timeout)
    if isinstance(daemon_status, dict):
        daemon_status["sourceMetadata"] = {
            "sourceUsed": "live_daemon",
            "daemonUrl": daemon_url,
            "snapshotUrl": snapshot_url,
            "contextSource": "live_daemon",
            "fileSessionFallbackUsed": False,
            "freshnessSource": "daemon_status+plugin_snapshot",
        }
        daemon_status.setdefault("sourceUsed", "live_daemon")
        daemon_status.setdefault("daemonUrl", daemon_url)
        daemon_status.setdefault("snapshotUrl", snapshot_url)
        daemon_status.setdefault("contextSource", "live_daemon")
        daemon_status.setdefault("fileSessionFallbackUsed", False)
        daemon_status.setdefault("freshnessSource", "daemon_status+plugin_snapshot")
    request = world_model_client.build_request(
        needs=[*world_model_core.WORLD_MODEL_NEEDS, "inventory"],
        max_objects=max_objects,
        include_projection=include_projection,
        include_collision=include_collision,
    )
    broad_fetch_timed_out = False
    try:
        snapshot = world_model_client.fetch(snapshot_url, timeout=timeout, request=request)
    except Exception as error:  # noqa: BLE001
        broad_fetch_timed_out = "timeout" in type(error).__name__.lower() or "timed out" in str(error).lower()
        snapshot = {
            "schema": "plugin_snapshot_fetch_error.v1",
            "status": "FAIL",
            "error": f"{type(error).__name__}: {error}",
        }
    if isinstance(daemon_status, dict):
        _copy_snapshot_liveness_fields(daemon_status, snapshot, source="broad_live_snapshot")
    payloads = world_model_core.extract_world_model_payloads(snapshot)
    world_summary_source = "broad_live_snapshot" if _dict(payloads.get("world_model_summary")) else None
    minimal_fallback_used = False
    if not _dict(payloads.get("world_model_summary")):
        minimal_request = world_model_client.build_request(
            needs=["baseline", "client_tick_hot", "world_model_summary"],
            max_objects=0,
            include_projection=False,
            include_collision=False,
        )
        try:
            minimal_snapshot = world_model_client.fetch(snapshot_url, timeout=timeout, request=minimal_request)
            minimal_payloads = world_model_core.extract_world_model_payloads(minimal_snapshot)
        except Exception:  # noqa: BLE001
            minimal_snapshot = {}
            minimal_payloads = {}
        if _dict(minimal_payloads.get("world_model_summary")):
            payloads = {**minimal_payloads, **payloads}
            minimal_fallback_used = True
            world_summary_source = "minimal_live_liveness_fallback"
        if isinstance(daemon_status, dict):
            _copy_snapshot_liveness_fields(daemon_status, minimal_snapshot, source="minimal_live_liveness_fallback")
    if not payloads:
        payloads = _world_payloads_from_status(daemon_status)
        if _dict(payloads.get("world_model_summary")) and world_summary_source is None:
            world_summary_source = "daemon_status"
    if isinstance(daemon_status, dict):
        object_total = _world_model_object_total(payloads, daemon_status)
        if object_total is not None:
            daemon_status.setdefault("worldModelObjectTotal", object_total)
        if world_summary_source:
            daemon_status.setdefault("worldModelSummarySource", world_summary_source)
        daemon_status.setdefault("broadFetchTimedOut", broad_fetch_timed_out)
        daemon_status.setdefault("minimalLiveLivenessFallbackUsed", minimal_fallback_used)
    return KnowledgeFabric(world_model_payloads=payloads, daemon_status=daemon_status, source="live_8890_8893")


def query_static_library(search: str | None = None, limit: int | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    library = load_static_library()
    items = []
    for key in ("routes", "serviceAnchors", "targetProfiles", "targetLibrary"):
        for item in _list(library.get(key)):
            if isinstance(item, dict):
                payload = dict(item)
                payload["_libraryKind"] = key
                items.append(payload)
    if search:
        needle = _norm(search)
        items = [item for item in items if needle in json.dumps(item, default=str).lower()]
    capped, cap_hit = _cap_items(items, limit)
    return _query_response(
        "knowledge_fabric_static_library_query.v1",
        {"summary": library.get("summary"), "items": capped, "count": len(items), "versionHash": library.get("versionHash")},
        started=started,
        source="static_library",
        freshness={"staticLibraryLoaded": True},
        cap_hit=cap_hit,
        truncated=cap_hit,
    )

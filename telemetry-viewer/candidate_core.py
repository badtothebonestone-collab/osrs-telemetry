from __future__ import annotations

import urllib.error
from collections import Counter
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any

from live_file_core import file_age_seconds, live_dir, overlay_targets, path_text, read_json, read_jsonl
from live_session_core import (
    choose_highlighter_session,
    daemon_session_from_status,
    daemon_status_url,
    fetch_json,
    same_path,
)
from telemetry_paths import find_newest_live_session, find_newest_session, get_sessions_dir


SCHEMA = "woodcutting_candidate_diagnostic.v1"
CANDIDATE_EXPLANATION_SCHEMA = "candidate_explanation.v1"
TREE_CLASSES = {"tree", "oak_tree", "willow_tree", "woodcutting_tree"}
VALID_CHOP_NAMES = {"tree", "oak", "oak tree", "willow", "willow tree", "maple tree", "yew tree", "magic tree"}
MAX_REASONABLE_CANVAS_COORDINATE = 100000.0


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _optional_int(value: Any) -> int | None:
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


def _numeric_coordinate(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = float(value)
        return value if isfinite(number) and abs(number) <= MAX_REASONABLE_CANVAS_COORDINATE else None
    if isinstance(value, float):
        if not isfinite(value) or abs(value) > MAX_REASONABLE_CANVAS_COORDINATE:
            return None
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        if not isfinite(parsed) or abs(parsed) > MAX_REASONABLE_CANVAS_COORDINATE:
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
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


def _target_key(target: dict[str, Any]) -> str | None:
    for key in ("targetKey", "objectKey", "key", "markerId"):
        value = target.get(key)
        if value is not None:
            return str(value)
    parts = [target.get("id"), target.get("worldX"), target.get("worldY"), target.get("plane"), target.get("classId")]
    if any(value is not None for value in parts):
        return ":".join(str(value) for value in parts)
    return None


def target_identity(target: dict[str, Any]) -> tuple[Any, ...] | None:
    if not target:
        return None
    if target.get("id") is None or target.get("worldX") is None or target.get("worldY") is None:
        return None
    return (
        target.get("id"),
        target.get("worldX"),
        target.get("worldY"),
        target.get("plane", 0),
        str(target.get("classId") or target.get("targetClass") or "").lower(),
    )


def target_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_key = _target_key(left)
    right_key = _target_key(right)
    if left_key and right_key and left_key == right_key:
        return True
    left_identity = target_identity(left)
    return bool(left_identity and left_identity == target_identity(right))


def target_name(target: dict[str, Any]) -> str:
    return str(target.get("name") or target.get("targetName") or target.get("label") or "unknown")


def target_tick(target: dict[str, Any]) -> int | None:
    for key in ("tick", "tickId", "sourceTick", "lastUpdatedTick"):
        value = _optional_int(target.get(key))
        if value is not None:
            return value
    return None


def selected_target_from_status(status: dict[str, Any]) -> dict[str, Any]:
    brain = _dict(status.get("brain"))
    generic = _dict(brain.get("genericTaskState"))
    target = _dict(generic.get("activeIntentTarget"))
    if target:
        return target
    summary = _dict(brain.get("currentContextSummary"))
    return _dict(summary.get("bestTarget") or status.get("brainBestTree"))


def _world_location(target: dict[str, Any]) -> dict[str, Any] | None:
    world = target.get("world")
    if isinstance(world, dict):
        x = _first_present(world.get("worldX"), world.get("x"))
        y = _first_present(world.get("worldY"), world.get("y"))
        if x is not None and y is not None:
            return {"worldX": x, "worldY": y, "plane": _first_present(world.get("plane"), world.get("z"), 0)}
    if target.get("worldX") is not None and target.get("worldY") is not None:
        return {"worldX": target.get("worldX"), "worldY": target.get("worldY"), "plane": target.get("plane", 0)}
    return None


def _point_from_mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    x = _first_present(value.get("x"), value.get("canvasX"), value.get("screenX"))
    y = _first_present(value.get("y"), value.get("canvasY"), value.get("screenY"))
    x = _numeric_coordinate(x)
    y = _numeric_coordinate(y)
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def _has_aim_point_payload(target: dict[str, Any]) -> bool:
    for key in ("aimPoint", "aimPointContext", "suggestedClickPoint", "clickPoint", "canvasPoint", "screenPoint"):
        value = target.get(key)
        if not isinstance(value, dict):
            continue
        if any(value.get(point_key) is not None for point_key in ("x", "y", "canvasX", "canvasY", "screenX", "screenY")):
            return True
    return False


def aim_point(target: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("aimPoint", "aimPointContext", "suggestedClickPoint", "clickPoint", "canvasPoint", "screenPoint"):
        point = _point_from_mapping(target.get(key))
        if point is not None:
            return point
    return None


def _aim_source(target: dict[str, Any]) -> str | None:
    for key in ("aimPoint", "aimPointContext", "suggestedClickPoint"):
        value = target.get(key)
        if isinstance(value, dict) and value.get("source"):
            return str(value.get("source"))
    return None


def _target_freshness_status(status: dict[str, Any], target: dict[str, Any], source_tick: int | None = None) -> dict[str, Any]:
    brain = _dict(status.get("brain"))
    domains = _dict(brain.get("freshnessDomains"))
    latest_tick = _optional_int(source_tick if source_tick is not None else brain.get("latestTick"))
    if latest_tick is None:
        latest_tick = _optional_int(status.get("latestTick"))
    selected_tick = target_tick(target)
    stale_reasons: list[str] = []
    freshness = domains.get("targetCandidateFreshness") or status.get("targetCandidateFreshness")
    if isinstance(freshness, str) and freshness and freshness.lower() not in {"fresh", "not_required"}:
        stale_reasons.append(f"targetCandidateFreshness={freshness}")
    if latest_tick is not None and selected_tick is not None and latest_tick - selected_tick > 3:
        stale_reasons.append(f"selected target tick {selected_tick} trails latest tick {latest_tick}")
    age_ms = None
    parsed = _parse_utc(status.get("latestUpdateUtc"))
    if parsed is not None:
        age_ms = int((datetime.now(timezone.utc) - parsed).total_seconds() * 1000)
        if age_ms > 30000:
            stale_reasons.append(f"daemon status age {age_ms}ms")
    elif isinstance(status.get("latestUpdateUtc"), str):
        stale_reasons.append("latestUpdateUtc could not be parsed")
    status_text = "stale" if stale_reasons else str(freshness or "unknown")
    return {
        "status": status_text,
        "latestTick": latest_tick,
        "targetTick": selected_tick,
        "targetCandidateFreshness": freshness,
        "daemonStatusAgeMillis": age_ms,
        "stale": bool(stale_reasons),
        "staleReasons": stale_reasons,
    }


def target_freshness_issue(
    status: dict[str, Any],
    brain: dict[str, Any],
    target: dict[str, Any],
    source_tick: int | None,
) -> str | None:
    merged_status = dict(status)
    if brain and "brain" not in merged_status:
        merged_status["brain"] = brain
    freshness = _target_freshness_status(merged_status, target, source_tick)
    reasons = freshness.get("staleReasons") if isinstance(freshness.get("staleReasons"), list) else []
    return "; ".join(str(reason) for reason in reasons) if reasons else None


def explain_candidate(
    target: dict[str, Any],
    *,
    rank: int | None = None,
    profile: str = "woodcutting",
    source_session: Path | str | None = None,
    source_file: Path | str | None = None,
    source_tick: int | None = None,
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = target if isinstance(target, dict) else {}
    point = aim_point(target)
    has_aim_payload = _has_aim_point_payload(target)
    aim_status = "valid" if point else "invalid" if has_aim_payload else "missing"
    safe_aimpoint = target.get("safeAimPoint") if isinstance(target.get("safeAimPoint"), dict) else None
    safe_point = None
    if safe_aimpoint and safe_aimpoint.get("status") == "PASS" and safe_aimpoint.get("canvasX") is not None and safe_aimpoint.get("canvasY") is not None:
        safe_point = {"x": safe_aimpoint.get("canvasX"), "y": safe_aimpoint.get("canvasY")}
    world = _world_location(target)
    freshness = _target_freshness_status(status or {}, target, source_tick)
    class_id = target.get("classId") or target.get("targetClass")
    profile_id = target.get("profileId") or target.get("profile")
    profile_match = True if profile_id == profile or str(class_id or "").lower() in TREE_CLASSES else None
    geometry_available = _first_present(target.get("geometryAvailable"), bool(point or target.get("bounds")))
    on_screen = target.get("onScreen")
    accepted = list(
        dict.fromkeys(
            str(item)
            for item in (
                _list(target.get("acceptedReasons"))
                + _list(target.get("reasons"))
                + _list(target.get("positiveSignals"))
            )
        )
    )
    rejected = list(
        dict.fromkeys(
            str(item)
            for item in (
                _list(target.get("rejectedReasons"))
                + _list(target.get("rejectReasons"))
                + _list(target.get("negativeSignals"))
            )
        )
    )
    if profile_match is True and "profileMatch" not in accepted:
        accepted.insert(0, "profileMatch")
    if aim_status == "invalid" and "invalidAimPoint" not in rejected:
        rejected.append("invalidAimPoint")
    source_session_text = str(source_session) if source_session is not None else None
    source_file_text = str(source_file) if source_file is not None else None
    return {
        "schema": CANDIDATE_EXPLANATION_SCHEMA,
        "name": target_name(target),
        "id": _first_present(target.get("rawId"), target.get("id"), target.get("objectId")),
        "objectId": _first_present(target.get("objectId"), target.get("rawId"), target.get("id")),
        "classId": class_id,
        "targetType": target.get("targetType"),
        "targetKey": _target_key(target),
        "profile": profile,
        "profileMatch": profile_match,
        "score": _first_present(target.get("score"), target.get("qualityScore"), target.get("confidence")),
        "rank": rank if rank is not None else target.get("rank"),
        "screen": target.get("screen") or target.get("screenPoint"),
        "world": world,
        "worldLocation": world,
        "canvasAimPoint": safe_point or point,
        "screenAimPoint": target.get("screenAimPoint") or target.get("resolvedScreenClickPoint"),
        "aimPoint": safe_point or point,
        "rawAimPoint": point,
        "aimPointStatus": aim_status,
        "safeAimPoint": safe_aimpoint,
        "aimPointSource": safe_aimpoint.get("source") if safe_aimpoint else _aim_source(target),
        "geometryAvailable": geometry_available,
        "geometryStatus": "available" if geometry_available is True else "missing" if geometry_available is False else "unknown",
        "onScreen": on_screen,
        "onScreenStatus": "on_screen" if on_screen is True else "off_screen" if on_screen is False else "unknown",
        "uiBlocked": target.get("uiBlocked"),
        "reacquiredAfterSuppression": target.get("reacquiredAfterSuppression"),
        "suppressedTargetKeysAtSelection": target.get("suppressedTargetKeysAtSelection"),
        "actions": _list(target.get("actions")) or _list(target.get("menuActions")) or _list(target.get("actionNames")),
        "expectedOptions": _list(target.get("expectedOptions")),
        "dialogueOpenerOptions": _list(target.get("dialogueOpenerOptions")),
        "dialogueExpectedPromptContains": _list(target.get("dialogueExpectedPromptContains")),
        "expectedTargets": _list(target.get("expectedTargets")),
        "expectedObjectIds": _list(target.get("expectedObjectIds")),
        "expectedPlaneChange": target.get("expectedPlaneChange"),
        "routeId": target.get("routeId"),
        "routeStepIndex": target.get("routeStepIndex"),
        "routeStepType": target.get("routeStepType"),
        "routeMode": target.get("routeMode"),
        "goalDirectedFallback": target.get("goalDirectedFallback"),
        "selectedServiceAnchor": target.get("selectedServiceAnchor"),
        "selectedApproachNode": target.get("selectedApproachNode"),
        "routeSourceMismatch": target.get("routeSourceMismatch"),
        "targetSource": target.get("source"),
        "pathTargetTile": target.get("pathTargetTile"),
        "destinationTile": target.get("destinationTile"),
        "localFrontierWaypoint": target.get("localFrontierWaypoint"),
        "frontierDistanceBefore": target.get("frontierDistanceBefore"),
        "frontierDistanceAfterEstimate": target.get("frontierDistanceAfterEstimate"),
        "progressScore": target.get("progressScore"),
        "routeWaypointSelection": target.get("routeWaypointSelection") if isinstance(target.get("routeWaypointSelection"), dict) else None,
        "predictedPathTiles": _list(target.get("predictedPathTiles")),
        "localScoutPath": _list(target.get("localScoutPath")),
        "availableWaypointTiles": _list(target.get("availableWaypointTiles")),
        "sourceTick": source_tick,
        "targetTick": target_tick(target),
        "freshness": freshness,
        "stale": bool(freshness.get("stale")),
        "staleReason": "; ".join(str(item) for item in freshness.get("staleReasons") or []) or None,
        "acceptedReasons": accepted,
        "rejectedReasons": rejected,
        "source": {
            "sessionPath": source_session_text,
            "filePath": source_file_text,
            "tick": source_tick,
        },
    }


def target_summary(
    target: dict[str, Any],
    *,
    rank: int | None = None,
    profile: str = "woodcutting",
    source_session: Path | str | None = None,
    source_file: Path | str | None = None,
    source_tick: int | None = None,
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    explanation = explain_candidate(
        target,
        rank=rank,
        profile=profile,
        source_session=source_session,
        source_file=source_file,
        source_tick=source_tick,
        status=status,
    )
    world = explanation.get("worldLocation") if isinstance(explanation.get("worldLocation"), dict) else {}
    return {
        **explanation,
        "qualityTier": target.get("qualityTier"),
        "worldX": world.get("worldX"),
        "worldY": world.get("worldY"),
        "plane": world.get("plane"),
        "distanceTiles": target.get("distanceTiles") if target.get("distanceTiles") is not None else target.get("targetDistanceChebyshev"),
        "tick": target_tick(target),
        "role": target.get("role") or target.get("targetRole"),
        "sourceName": target.get("source"),
        "reason": target.get("reason"),
        "rejectReasons": target.get("rejectReasons") or [],
    }


def is_tree_candidate(target: dict[str, Any]) -> bool:
    class_id = str(target.get("classId") or target.get("targetClass") or "").lower()
    name = target_name(target).lower()
    return class_id in TREE_CLASSES or "tree" in name or "oak" in name or "willow" in name


def is_known_chop_candidate(target: dict[str, Any]) -> bool:
    class_id = str(target.get("classId") or target.get("targetClass") or "").lower()
    name = target_name(target).lower()
    return class_id in {"oak_tree", "willow_tree"} or name in VALID_CHOP_NAMES


def freshness_from_status(status: dict[str, Any], selected: dict[str, Any]) -> dict[str, Any]:
    freshness = _target_freshness_status(status, selected)
    return {
        "latestTick": freshness.get("latestTick"),
        "selectedTargetTick": freshness.get("targetTick"),
        "targetCandidateFreshness": freshness.get("targetCandidateFreshness"),
        "daemonStatusAgeMillis": freshness.get("daemonStatusAgeMillis"),
        "stale": freshness.get("stale"),
        "staleReasons": freshness.get("staleReasons"),
    }


def build_report(
    *,
    session: Path | None = None,
    latest_session: bool = False,
    sessions_dir: str | Path | None = None,
    profile: str = "woodcutting",
    top: int = 20,
    daemon_url: str = "http://127.0.0.1:8890",
    timeout: float = 3.0,
    daemon_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    failures: list[str] = []
    root = get_sessions_dir(sessions_dir)
    latest = find_newest_session(root) if latest_session or session is None else None
    live_latest = find_newest_live_session(root)
    selected_session = session or latest
    status = daemon_status or {}
    if daemon_status is None:
        try:
            status = fetch_json(daemon_status_url(daemon_url), timeout=timeout)
        except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as error:
            warnings.append(f"daemon status unavailable: {type(error).__name__}: {error}")
            status = {}

    action_session = daemon_session_from_status(status)
    highlighter_session = choose_highlighter_session(action_session, live_latest)
    selected_target = selected_target_from_status(status)
    highlighter_live_dir = live_dir(highlighter_session)
    latest_live_dir = live_dir(selected_session)
    overlay_path = highlighter_live_dir / "overlay_debug_state.json" if highlighter_live_dir else None
    latest_candidate_file = latest_live_dir / "live_candidates.jsonl" if latest_live_dir else None
    highlighter_candidate_file = highlighter_live_dir / "live_candidates.jsonl" if highlighter_live_dir else None
    overlay = read_json(overlay_path)
    latest_candidates = read_jsonl(latest_candidate_file)
    highlighter_candidates = read_jsonl(highlighter_candidate_file)
    markers = overlay_targets(overlay)
    source_targets = highlighter_candidates or markers
    source_file = highlighter_candidate_file if highlighter_candidates else overlay_path
    top_targets = [
        target_summary(
            candidate,
            rank=index,
            profile=profile,
            source_session=highlighter_session,
            source_file=source_file,
            source_tick=target_tick(candidate),
            status=status,
        )
        for index, candidate in enumerate(source_targets[: max(0, top)], start=1)
    ]
    selected_key = _target_key(selected_target)
    source_matches = any(target_matches(selected_target, marker) for marker in markers)
    if selected_session and action_session and not same_path(selected_session, action_session):
        warnings.append("latest session differs from daemon action session; file-based --latest-session tools may be stale")
    if highlighter_session and action_session and not same_path(highlighter_session, action_session):
        warnings.append("highlighter source differs from daemon action source")
    if selected_key and not source_matches and markers:
        warnings.append("selected daemon target is not present in highlighter marker source")
    file_outputs_disabled = status.get("noFileDaily") is True or status.get("writeDebugLiveFiles") is False
    if latest_candidate_file and not latest_candidate_file.exists() and not file_outputs_disabled:
        warnings.append("latest session live_candidates.jsonl is missing")
    if overlay_path and not overlay_path.exists():
        warnings.append("highlighter overlay_debug_state.json is missing")

    freshness = freshness_from_status(status, selected_target)
    if freshness["stale"]:
        failures.append("candidate data stale; refusing target selection")

    reject_counts = Counter()
    for candidate in source_targets + latest_candidates:
        for reason in candidate.get("rejectReasons") or []:
            reject_counts[str(reason)] += 1
    for key in ("pluginSnapshotPrefilterRejectReasons", "pluginSnapshotCandidateRejectReasons"):
        values = status.get(key)
        if isinstance(values, dict):
            for reason, count in values.items():
                if isinstance(count, int):
                    reject_counts[str(reason)] += count

    tree_candidates = [candidate for candidate in source_targets if is_tree_candidate(candidate)]
    valid_chop = [candidate for candidate in source_targets if is_known_chop_candidate(candidate)]
    selected_has_aim = bool(aim_point(selected_target)) if selected_target else False
    selected_safe_aimpoint = selected_target.get("safeAimPoint") if isinstance(selected_target.get("safeAimPoint"), dict) else None
    selected_actionable = (
        bool(selected_safe_aimpoint.get("actionable"))
        if selected_safe_aimpoint
        else bool(selected_has_aim and selected_target.get("onScreen") is not False and selected_target.get("geometryAvailable") is not False)
    ) if selected_target else False
    if selected_target and not selected_actionable and selected_target.get("onScreen") is not False:
        warnings.append("selected target is visible but not actionable; missing safe aim point or valid click point")
    status_candidate_count = status.get("candidateCount") if isinstance(status.get("candidateCount"), int) else None
    source_mismatch = bool(
        (selected_session and action_session and not same_path(selected_session, action_session))
        or (highlighter_session and action_session and not same_path(highlighter_session, action_session))
    )
    report_status = "FAIL" if failures else "WARN" if warnings else "PASS"
    selected_summary = (
        target_summary(
            selected_target,
            rank=selected_target.get("rank"),
            profile=profile,
            source_session=action_session,
            source_file=None,
            source_tick=target_tick(selected_target),
            status=status,
        )
        if selected_target
        else None
    )
    return {
        "schema": SCHEMA,
        "status": report_status,
        "profile": profile,
        "sessions": {
            "latestSessionPath": path_text(latest),
            "requestedSessionPath": path_text(selected_session),
            "daemonActionSessionPath": path_text(action_session),
            "highlighterSessionPath": path_text(highlighter_session),
            "sourceMismatch": source_mismatch,
        },
        "freshness": {
            **freshness,
            "latestCandidateFileAgeSeconds": file_age_seconds(latest_candidate_file),
            "highlighterOverlayAgeSeconds": file_age_seconds(overlay_path),
        },
        "counts": {
            "rawSceneObjects": status.get("broadCandidateCount"),
            "daemonInMemoryCandidates": status_candidate_count,
            "latestFileCandidates": len(latest_candidates),
            "highlighterFileCandidates": len(highlighter_candidates),
            "highlighterMarkers": len(markers),
            "woodcuttingProfileCandidates": status.get("profileCandidateCount"),
            "treeClassCandidates": len(tree_candidates),
            "knownChopCandidates": len(valid_chop),
            "rejectedByReason": dict(reject_counts.most_common()),
        },
        "selectedTarget": selected_summary,
        "selectedTargetChecks": {
            "present": bool(selected_target),
            "onScreen": selected_target.get("onScreen") if selected_target else None,
            "geometryAvailable": selected_target.get("geometryAvailable") if selected_target else None,
            "hasAimPoint": selected_has_aim,
            "actionable": selected_actionable,
            "safeAimPointStatus": selected_safe_aimpoint.get("status") if selected_safe_aimpoint else None,
            "uiBlocked": selected_target.get("uiBlocked") if selected_target else None,
            "stale": freshness["stale"],
            "inHighlighterSource": source_matches,
        },
        "sourceHealth": {
            "sourceCapHit": status.get("sourceCapHit"),
            "budgetExceeded": status.get("budgetExceeded"),
            "sourceSceneKnowledgeComplete": status.get("sourceSceneKnowledgeComplete"),
            "overlayStateWritten": status.get("overlayStateWritten"),
            "writeDebugLiveFiles": status.get("writeDebugLiveFiles"),
            "candidateFilesExpected": not file_outputs_disabled,
        },
        "topCandidates": top_targets,
        "warnings": warnings,
        "failures": failures,
    }

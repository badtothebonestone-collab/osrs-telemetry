from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    import knowledge_fabric
except Exception:  # noqa: BLE001
    knowledge_fabric = None  # type: ignore


LIVE_DIR = Path("interaction_geometry") / "live"
DEFAULT_DEBUG_DIR = LIVE_DIR / "debug_bundles"
SCHEMA = "visual_debug_bundle.v1"


ScreenshotFunc = Callable[[tuple[int, int, int, int] | None], Any]


def _default_screenshot(region: tuple[int, int, int, int] | None = None) -> Any:
    try:
        from PIL import ImageGrab

        if region is not None:
            x, y, width, height = region
            return ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True)
        return ImageGrab.grab(all_screens=True)
    except Exception:  # noqa: BLE001
        pass
    import pyautogui  # type: ignore

    if region is not None:
        return pyautogui.screenshot(region=region)
    return pyautogui.screenshot()


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int_or_none(value: Any) -> int | None:
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


def _status_context(status: dict[str, Any], key: str) -> dict[str, Any]:
    brain = _safe_dict(status.get("brain"))
    value = brain.get(key)
    if isinstance(value, dict):
        return value
    value = status.get(key)
    return value if isinstance(value, dict) else {}


def _session_path(status: dict[str, Any] | None) -> Path | None:
    status = status if isinstance(status, dict) else {}
    for value in (
        status.get("sessionPath"),
        status.get("activeSessionPath"),
        _safe_dict(status.get("session")).get("activeSessionPath"),
        _safe_dict(status.get("session")).get("sessionPath"),
        _safe_dict(status.get("brain")).get("sessionPath"),
    ):
        if isinstance(value, str) and value.strip():
            return Path(value)
    return None


def _overlay_path(status: dict[str, Any] | None) -> Path | None:
    status = status if isinstance(status, dict) else {}
    for value in (status.get("overlayDebugStatePath"), status.get("overlayDebugPath")):
        if isinstance(value, str) and value.strip():
            path = Path(value)
            if path.exists():
                return path
    session = _session_path(status)
    if session is None:
        return None
    path = session / LIVE_DIR / "overlay_debug_state.json"
    return path if path.exists() else None


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return decoded if isinstance(decoded, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _write_world_model_evidence(bundle_dir: Path, status: dict[str, Any]) -> dict[str, str]:
    evidence = {
        "world_model_summary.json": status.get("worldModelSummary"),
        "route_object_census.json": status.get("worldModelRouteObjectCensus"),
        "resource_object_census.json": status.get("worldModelResourceObjectCensus"),
        "service_object_census.json": status.get("worldModelServiceObjectCensus"),
        "projection_audit.json": status.get("worldModelProjectionAudit"),
        "collision_frontier.json": status.get("worldModelPathingFrontier"),
    }
    if knowledge_fabric is not None and not any(isinstance(payload, dict) and payload for payload in evidence.values()):
        try:
            fabric = knowledge_fabric.fabric_from_live(max_objects=160, include_collision=True, timeout=1.5)
            payloads = getattr(fabric, "world_model_payloads", {})
            if isinstance(payloads, dict):
                evidence = {
                    "world_model_summary.json": payloads.get("world_model_summary"),
                    "route_object_census.json": payloads.get("route_object_census"),
                    "resource_object_census.json": payloads.get("resource_object_census"),
                    "service_object_census.json": payloads.get("service_object_census"),
                    "projection_audit.json": payloads.get("projection_audit"),
                    "collision_frontier.json": payloads.get("pathing_frontier"),
                }
        except Exception:  # noqa: BLE001
            pass
    written: dict[str, str] = {}
    for filename, payload in evidence.items():
        if not isinstance(payload, dict) or not payload:
            continue
        path = bundle_dir / filename
        _write_json(path, payload)
        written[filename] = str(path)
    return written


def _write_knowledge_fabric_evidence(bundle_dir: Path, status: dict[str, Any]) -> dict[str, str]:
    if knowledge_fabric is None:
        return {}
    try:
        fabric = knowledge_fabric.KnowledgeFabric.from_status(status)
        evidence = {
            "knowledge_fabric_status.json": fabric.status(),
            "current_debug_context.json": fabric.query_current_debug_context(limit=20),
            "explain_current_blocker.json": fabric.explain_current_blocker(),
            "resource_candidates.json": fabric.query_resource_candidates(limit=20),
            "service_candidates.json": fabric.query_service_candidates(limit=20),
            "route_objects.json": fabric.query_route_objects(limit=20),
            "pathing_frontier.json": fabric.query_path_frontier(limit=20),
            "view_quality.json": fabric.query_view_quality(intent=str(status.get("currentIntent") or "unknown")),
            "session_memory_summary.json": fabric.session_memory,
            "static_library_summary.json": fabric.static_library,
            "data_quality_report.json": fabric.data_quality_report(limit=20),
            "handoff_summary.json": fabric.handoff_summary(),
        }
    except Exception:  # noqa: BLE001
        return {}
    written: dict[str, str] = {}
    for filename, payload in evidence.items():
        if not isinstance(payload, dict) or not payload:
            continue
        path = bundle_dir / filename
        _write_json(path, payload)
        written[filename] = str(path)
    return written


def _input_integrity_payload(
    status: dict[str, Any],
    action_trace: dict[str, Any] | None,
    extra: dict[str, Any] | None,
) -> dict[str, Any] | None:
    trace = action_trace if isinstance(action_trace, dict) else {}
    extra = extra if isinstance(extra, dict) else {}
    for value in (
        extra.get("inputIntegrityStatus"),
        trace.get("inputIntegrityStatusAfter"),
        trace.get("inputIntegrityStatusBefore"),
        _safe_dict(trace.get("liveInput")).get("inputIntegrityStatusAfter"),
        _safe_dict(trace.get("liveInput")).get("inputIntegrityStatusBefore"),
        _safe_dict(trace.get("liveInput")).get("inputIntegrityStatus"),
        status.get("inputIntegrityStatus"),
        _safe_dict(status.get("liveInput")).get("inputIntegrityStatus"),
    ):
        if isinstance(value, dict) and value:
            return dict(value)
    return None


def _sanitize_reason(reason: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(reason or "debug")).strip("_")
    return text[:80] or "debug"


def _proposal_payload(proposal: Any) -> dict[str, Any]:
    if proposal is None:
        return {}
    if isinstance(proposal, dict):
        return dict(proposal)
    to_dict = getattr(proposal, "to_dict", None)
    if callable(to_dict):
        try:
            value = to_dict()
            return value if isinstance(value, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _proposal_summary(proposal: Any) -> dict[str, Any]:
    payload = _proposal_payload(proposal)
    explanation = _safe_dict(payload.get("targetExplanation"))
    resolution = _safe_dict(payload.get("clickPointResolution"))
    return {
        "proposedAction": payload.get("proposedAction"),
        "targetKind": payload.get("targetKind"),
        "targetName": payload.get("targetName"),
        "reason": payload.get("reason"),
        "confidence": payload.get("confidence"),
        "clickPointSpace": payload.get("clickPointSpace"),
        "suggestedClickPoint": payload.get("suggestedClickPoint"),
        "clickPointResolution": resolution or None,
        "coordinateSpace": resolution.get("coordinateSpace"),
        "scaleX": resolution.get("scaleX"),
        "scaleY": resolution.get("scaleY"),
        "screenPointBeforeScaling": resolution.get("screenPointBeforeScaling"),
        "screenPointAfterScaling": resolution.get("screenPointAfterScaling"),
        "windowBoundsSource": resolution.get("windowBoundsSource"),
        "canvasBoundsSource": resolution.get("canvasBoundsSource"),
        "targetTile": payload.get("targetTile"),
        "selectedTarget": explanation.get("targetName") or explanation.get("name") or payload.get("targetName"),
        "targetSource": explanation.get("targetSource") or explanation.get("source"),
        "actionTargetSource": payload.get("actionTargetSource") or explanation.get("actionTargetSource"),
        "actionability": payload.get("actionability") or explanation.get("actionability"),
        "advisoryTargetSource": explanation.get("advisoryTargetSource"),
        "staleProposalDetected": payload.get("staleProposalDetected"),
        "staleProposalSource": payload.get("staleProposalSource"),
        "routeProjectionStatus": explanation.get("routeProjectionStatus"),
        "resourceProjectionStatus": explanation.get("resourceProjectionStatus"),
        "resourceViewScore": explanation.get("resourceViewScore"),
        "resourceViewClassification": explanation.get("resourceViewClassification"),
        "resourceCameraRecoveryRecommended": explanation.get("resourceCameraRecoveryRecommended"),
        "serviceTargetExposure": explanation.get("serviceTargetExposure"),
        "targetViewState": explanation.get("targetViewState"),
        "targetViewPolicy": explanation.get("targetViewPolicy"),
        "resourceTargetAmbiguity": explanation.get("resourceTargetAmbiguity"),
        "aimpointSamplesTried": explanation.get("aimpointSamplesTried"),
        "aimpointSampleResults": explanation.get("aimpointSampleResults"),
        "selectedAimpointSource": explanation.get("selectedAimpointSource"),
        "hoverConfirmedTopExpected": explanation.get("hoverConfirmedTopExpected"),
        "safeAimPoint": explanation.get("safeAimPoint"),
    }


def _trace_excerpt(action_trace: dict[str, Any] | None) -> dict[str, Any]:
    trace = action_trace if isinstance(action_trace, dict) else {}
    client_tick = _safe_dict(trace.get("clientTick"))
    return {
        "proposedAction": trace.get("proposedAction"),
        "actionIntentType": trace.get("actionIntentType"),
        "finalClassification": trace.get("finalClassification"),
        "selectedTarget": trace.get("selectedTarget"),
        "intendedPoint": trace.get("intendedPoint"),
        "coordinateScaling": _safe_dict(trace.get("intendedPoint")),
        "reacquisition": trace.get("reacquisition"),
        "cameraInput": trace.get("cameraInput"),
        "humanInput": trace.get("humanInput"),
        "routeStability": trace.get("routeStability"),
        "routeTransitionLedgerEntry": trace.get("routeTransitionLedgerEntry"),
        "resourceTargetAmbiguity": trace.get("resourceTargetAmbiguity"),
        "targetViewState": trace.get("targetViewState"),
        "hoverMenu": client_tick.get("acceptedHoverSample") or client_tick.get("latestRejectedHoverSample"),
        "clickedMenu": client_tick.get("lastMenuOptionClickedAfter"),
        "menuMismatch": client_tick.get("menuMismatch"),
    }


def _player_location(status: dict[str, Any]) -> dict[str, Any] | None:
    player = _status_context(status, "playerContext") or _status_context(status, "player")
    location = status.get("playerLocation") if isinstance(status.get("playerLocation"), dict) else None
    if location:
        return {
            "worldX": location.get("worldX", location.get("x")),
            "worldY": location.get("worldY", location.get("y")),
            "plane": location.get("plane"),
        }
    tile = player.get("worldTile") if isinstance(player.get("worldTile"), dict) else player.get("tile")
    if isinstance(tile, dict):
        return {
            "worldX": tile.get("worldX", tile.get("x")),
            "worldY": tile.get("worldY", tile.get("y")),
            "plane": tile.get("plane"),
        }
    world_x = player.get("worldX") if player.get("worldX") is not None else status.get("playerWorldX")
    world_y = player.get("worldY") if player.get("worldY") is not None else status.get("playerWorldY")
    if world_x is None or world_y is None:
        pathing = _status_context(status, "pathingContext")
        for value in (
            pathing.get("playerTile"),
            pathing.get("currentPlayerTile"),
            pathing.get("collisionWindowCenterWorld"),
            status.get("pathingCollisionWindowCenterWorld"),
            status.get("collisionWindowCenterWorld"),
        ):
            if isinstance(value, dict):
                fallback_x = value.get("worldX", value.get("x"))
                fallback_y = value.get("worldY", value.get("y"))
                if fallback_x is not None and fallback_y is not None:
                    return {"worldX": fallback_x, "worldY": fallback_y, "plane": value.get("plane")}
        return None
    return {"worldX": world_x, "worldY": world_y, "plane": player.get("plane", status.get("playerPlane"))}


def _player_location_source(status: dict[str, Any]) -> str | None:
    player = _status_context(status, "playerContext") or _status_context(status, "player")
    source = status.get("playerLocationSource") or player.get("locationSource")
    if isinstance(source, str) and source:
        return source
    if isinstance(status.get("playerLocation"), dict):
        return "daemon_player_location"
    if player.get("worldX") is not None or player.get("world_x") is not None or isinstance(player.get("worldTile"), dict):
        return "player_context"
    pathing = _status_context(status, "pathingContext")
    if (
        isinstance(pathing.get("collisionWindowCenterWorld"), dict)
        or isinstance(status.get("pathingCollisionWindowCenterWorld"), dict)
        or isinstance(status.get("collisionWindowCenterWorld"), dict)
    ):
        return "collision_window_center_proxy"
    return None


def _player_location_confidence(status: dict[str, Any]) -> float | None:
    player = _status_context(status, "playerContext") or _status_context(status, "player")
    for value in (status.get("playerLocationConfidence"), player.get("locationConfidence")):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    source = _player_location_source(status)
    if source == "collision_window_center_proxy":
        return 0.35
    if source:
        return 1.0
    return None


def _inventory_free_slots(status: dict[str, Any]) -> int | None:
    inventory = _status_context(status, "inventoryContext")
    for key in ("freeSlots", "inventoryFreeSlots"):
        value = _int_or_none(inventory.get(key))
        if value is not None:
            return value
    return _int_or_none(status.get("inventoryFreeSlots"))


def _resource_count(status: dict[str, Any]) -> int | None:
    progress = _safe_dict(_status_context(status, "inventoryContext").get("progress"))
    for key in ("currentHeldCount", "currentHeldResourceCount", "heldResourceCount"):
        value = _int_or_none(progress.get(key))
        if value is not None:
            return value
    for key in ("resourceItemQuantity", "heldResourceCount", "resourceCount"):
        value = _int_or_none(status.get(key))
        if value is not None:
            return value
    return None


def _phase_intent(status: dict[str, Any]) -> tuple[str | None, str | None]:
    generic = _status_context(status, "genericTaskState")
    return (
        generic.get("phase") or status.get("phase") or status.get("currentCycleStage"),
        generic.get("activeIntent") or status.get("activeIntent") or status.get("currentIntent"),
    )


def _camera_state(status: dict[str, Any]) -> dict[str, Any] | None:
    for value in (
        status.get("cameraViewport"),
        _status_context(status, "cameraViewport"),
        _safe_dict(_status_context(status, "inputContext")).get("cameraViewport"),
    ):
        if isinstance(value, dict) and value:
            return dict(value)
    return None


def _hover_menu(status: dict[str, Any]) -> dict[str, Any] | None:
    for value in (
        status.get("hoverMenu"),
        status.get("clientTickHot"),
        _status_context(status, "clientTickHot"),
    ):
        if isinstance(value, dict):
            if value.get("hoverMenu") and isinstance(value.get("hoverMenu"), dict):
                return dict(value["hoverMenu"])
            if value.get("postMenuSort") and isinstance(value.get("postMenuSort"), dict):
                return dict(value["postMenuSort"])
            if value.get("topOption") or value.get("option"):
                return dict(value)
    return None


def _latest_clicked_menu(status: dict[str, Any]) -> dict[str, Any] | None:
    for value in (
        status.get("lastMenuOptionClicked"),
        _safe_dict(status.get("clientTickHot")).get("lastMenuOptionClicked"),
        _safe_dict(_status_context(status, "clientTickHot")).get("lastMenuOptionClicked"),
    ):
        if isinstance(value, dict) and value:
            return dict(value)
    return None


def _client_tick_hot_summary(status: dict[str, Any]) -> dict[str, Any] | None:
    hot = _safe_dict(status.get("clientTickHot")) or _status_context(status, "clientTickHot")
    if not hot:
        return None
    latency = _safe_dict(hot.get("latency"))
    hover = hot.get("hoverMenu") if isinstance(hot.get("hoverMenu"), dict) else hot.get("postMenuSort")
    clicked = hot.get("lastMenuOptionClicked") if isinstance(hot.get("lastMenuOptionClicked"), dict) else None
    return {
        "schema": hot.get("schema"),
        "clientTick": hot.get("clientTick"),
        "gameTickAtSample": hot.get("gameTickAtSample"),
        "gameState": hot.get("gameState"),
        "sessionId": hot.get("sessionId"),
        "ageMillis": hot.get("ageMillis") or hot.get("ageMs") or latency.get("ageMillis") or latency.get("ageMs"),
        "hoverMenu": dict(hover) if isinstance(hover, dict) else None,
        "lastMenuOptionClicked": dict(clicked) if isinstance(clicked, dict) else None,
    }


def _inventory_state(status: dict[str, Any]) -> dict[str, Any]:
    inventory = _status_context(status, "inventoryContext")
    progress = _safe_dict(inventory.get("progress"))
    return {
        "freeSlots": _inventory_free_slots(status),
        "resourceCount": _resource_count(status),
        "inventoryFull": inventory.get("inventoryFull", status.get("inventoryFull")),
        "resourceItemQuantity": status.get("resourceItemQuantity"),
        "currentHeldCount": progress.get("currentHeldCount"),
        "currentInventorySignature": progress.get("currentInventorySignature"),
    }


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) and value else None


def _route_contexts(status: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        _status_context(status, "serviceRouteContext"),
        _status_context(status, "returnRouteContext"),
        _status_context(status, "pathingContext"),
    )


def _route_mode(
    status: dict[str, Any],
    service_route: dict[str, Any],
    return_route: dict[str, Any],
    pathing: dict[str, Any],
    extra: dict[str, Any] | None,
) -> str:
    extra = extra if isinstance(extra, dict) else {}
    for value in (
        extra.get("currentRouteMode"),
        extra.get("routeMode"),
        status.get("currentRouteMode"),
        pathing.get("routeMode"),
        service_route.get("routeMode"),
        return_route.get("routeMode"),
    ):
        if isinstance(value, str) and value.strip():
            normalized = value.strip()
            if normalized in {"explicit_route", "reverse_route", "goal_directed_fallback", "local_frontier_to_service", "unknown"}:
                if normalized == "reverse_route" and service_route.get("routeId"):
                    continue
                return normalized
    if bool(service_route.get("goalDirectedFallback")) or bool(pathing.get("goalDirectedFallback")):
        return "goal_directed_fallback"
    if str(pathing.get("pathingReason") or pathing.get("reason") or "") == "destination_outside_collision_window":
        return "local_frontier_to_service"
    if service_route.get("routeId"):
        return "explicit_route"
    if return_route:
        return "reverse_route"
    return "unknown"


def _route_context_summary(
    status: dict[str, Any],
    proposal_summary: dict[str, Any],
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    service_route, return_route, pathing = _route_contexts(status)
    route = service_route or return_route
    next_edge = route.get("nextEdge") if isinstance(route.get("nextEdge"), dict) else None
    selected_service_anchor = (
        _dict_or_none(route.get("selectedServiceAnchor"))
        or _dict_or_none(route.get("selectedServiceObject"))
        or _dict_or_none(route.get("visibleServiceTarget"))
        or _dict_or_none(route.get("selectedRouteObject"))
        or _dict_or_none(route.get("visibleInteractionTarget"))
    )
    selected_approach_node = (
        _dict_or_none(pathing.get("selectedApproachNode"))
        or _dict_or_none(route.get("selectedApproachNode"))
        or _dict_or_none(pathing.get("alternateApproachNode"))
        or _dict_or_none(route.get("alternateApproachNode"))
    )
    selected_waypoint = (
        _dict_or_none(proposal_summary.get("targetTile"))
        or _dict_or_none(pathing.get("nextWaypointTile"))
        or _dict_or_none(pathing.get("pathTargetTile"))
        or _dict_or_none(route.get("currentNavigationTarget"))
    )
    route_source_mismatch = (
        _dict_or_none((extra or {}).get("routeSourceMismatchDetails"))
        or _dict_or_none((extra or {}).get("routeSourceMismatch"))
        or _dict_or_none(route.get("routeSourceMismatch"))
        or _dict_or_none(pathing.get("routeSourceMismatch"))
    )
    pathing_reason = (
        (extra or {}).get("pathingReason")
        or pathing.get("pathingReason")
        or pathing.get("reason")
        or route.get("pathingReason")
        or route.get("routeStepStatus")
        or proposal_summary.get("reason")
    )
    return {
        "routeId": route.get("routeId") or return_route.get("sourceRouteId"),
        "currentNodeId": route.get("currentNodeId"),
        "currentEdge": dict(next_edge) if isinstance(next_edge, dict) else None,
        "currentStepIndex": route.get("currentStepIndex"),
        "currentStep": dict(route.get("currentStep")) if isinstance(route.get("currentStep"), dict) else None,
        "routeStepStatus": route.get("routeStepStatus"),
        "routeMode": _route_mode(status, service_route, return_route, pathing, extra),
        "selectedServiceAnchor": selected_service_anchor,
        "selectedApproachNode": selected_approach_node,
        "selectedWaypoint": selected_waypoint,
        "routeSourceMismatchDetails": route_source_mismatch,
        "pathingReason": pathing_reason,
        "routeWallLoopDetected": route.get("routeWallLoopDetected") or pathing.get("routeWallLoopDetected"),
        "routeObjectsVisible": route.get("routeObjectsVisible"),
        "routeObjectsActionable": route.get("routeObjectsActionable"),
        "routeRelevantObjects": route.get("routeRelevantObjects"),
        "routeRelevantActionableObjects": route.get("routeRelevantActionableObjects"),
        "serviceObjectsVisible": route.get("serviceObjectsVisible"),
        "serviceObjectsActionable": route.get("serviceObjectsActionable"),
    }


def _wall_loop_classification(
    reason: str,
    classification: str | None,
    route_summary: dict[str, Any],
    trace_excerpt: dict[str, Any],
    extra: dict[str, Any] | None,
) -> str | None:
    extra = extra if isinstance(extra, dict) else {}
    for value in (
        extra.get("wallLoopClassification"),
        classification,
        reason,
        _safe_dict(trace_excerpt.get("routeStability")).get("classification"),
    ):
        if isinstance(value, str) and ("wall_hugging" in value or "wall_loop" in value or "wrong_side_of_wall" in value):
            return value
    if route_summary.get("routeWallLoopDetected"):
        return "route_wall_hugging_detected"
    return None


def _final_decision(
    reason: str,
    classification: str | None,
    trace_excerpt: dict[str, Any],
    clicked_menu: dict[str, Any] | None,
    extra: dict[str, Any] | None,
) -> str | None:
    extra = extra if isinstance(extra, dict) else {}
    explicit = extra.get("finalDecision")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    text = " ".join(str(item or "") for item in (reason, classification, trace_excerpt.get("finalClassification"))).lower()
    if "camera_reacquire" in text or "projection_recovery" in text:
        return "camera adjusted"
    if any(token in text for token in ("skip", "rejected", "mismatch")):
        return "skipped"
    if "timeout" in text:
        return "waited"
    if any(token in text for token in ("blocked", "failure", "failed", "wall_hugging", "path_blocked", "unexpected_current_area")):
        return "stopped safely"
    if clicked_menu or trace_excerpt.get("clickedMenu"):
        return "clicked"
    return None


def _window_rect(backend: Any | None, warnings: list[str]) -> dict[str, int] | None:
    if backend is None or not hasattr(backend, "canvas_client_geometry"):
        return None
    try:
        origin, size = backend.canvas_client_geometry()
        return {"x": int(origin[0]), "y": int(origin[1]), "width": int(size[0]), "height": int(size[1])}
    except Exception as error:  # noqa: BLE001
        warnings.append(f"window geometry unavailable: {type(error).__name__}: {error}")
        return None


def _mouse_position(backend: Any | None, warnings: list[str]) -> dict[str, int] | None:
    if backend is None or not hasattr(backend, "current_position"):
        return None
    try:
        x, y = backend.current_position()
        return {"x": int(x), "y": int(y)}
    except Exception as error:  # noqa: BLE001
        warnings.append(f"mouse position unavailable: {type(error).__name__}: {error}")
        return None


def _trigger_flag(reason: str) -> str | None:
    value = str(reason or "")
    if value in {
        "resource_projection_recovery_start",
        "resource_projection_recovery_end",
        "resource_camera_reacquire_start",
        "resource_camera_reacquire_end",
        "camera_reacquire_start",
        "camera_reacquire_end",
        "target_loaded_offscreen",
        "target_edge_sliver",
        "target_insufficient_exposure",
        "target_screen_click_point_unavailable",
        "target_view_recovery_start",
        "target_view_recovery_success",
        "target_view_recovery_failed",
        "target_hover_confirmed_after_camera",
        "target_zoom_recovery_used",
        "target_bearing_camera_alignment",
        "service_object_loaded_offscreen",
        "service_object_edge_sliver",
        "service_view_recovery_start",
        "service_view_recovery_success",
        "service_view_recovery_failed",
    }:
        return "screenshot_on_camera_recovery"
    if value in {"route_edge_projection_rejected", "route_waypoint_edge_rejected"}:
        return "screenshot_on_edge_reject"
    if "timeout" in value or value in {"route_no_progress_timeout", "resource_timeout"}:
        return "screenshot_on_timeout"
    if value in {
        "return_transition_pending",
        "route_transition_pending",
        "return_transition_retry_required",
        "route_transition_retry_required",
        "return_transition_retry_success",
        "route_transition_retry_success",
        "return_transition_reconciled_success",
        "route_transition_reconciled_success",
        "retry_while_pending_detected",
    }:
        return "screenshot_on_lifecycle_transition"
    if value in {
        "menu_flip_mismatch",
        "failure",
        "execution_failed",
        "pre_action_readiness_failed",
        "route_source_mismatch",
        "target_source_mismatch",
        "stale_static_route_target",
        "hover_intent_mismatch",
        "stale_proposal_reacquire_failed",
        "route_wall_hugging_detected",
        "goal_directed_path_blocked",
        "unexpected_current_area",
        "repeated_navigation_no_progress",
        "poor_resource_view",
        "resource_target_edge_rejected",
        "worksite_drift_detected",
        "no_executable_resource_view",
        "input_integrity_fail",
        "arduino_missing",
        "monitor_missing",
        "injected_input_detected",
        "backend_bypass_detected",
        "arduino_self_test_fail",
    }:
        return "screenshot_on_failure"
    if value in {
        "post_bank_reacquisition",
        "lifecycle_transition",
        "goal_directed_fallback_started",
        "alternate_approach_node_selected",
        "service_anchor_reached",
        "route_object_reacquired",
        "live_target_reacquired",
        "post_depletion_reacquire",
        "arduino_self_test_pass",
    }:
        return "screenshot_on_lifecycle_transition"
    if value == "final_summary":
        return "capture_debug_screenshots"
    return None


@dataclass
class VisualDebugBundleWriter:
    enabled: bool
    base_dir: Path | None = None
    max_bundles: int = 20
    backend: Any | None = None
    screenshot_func: ScreenshotFunc = _default_screenshot
    trigger_flags: dict[str, bool] = field(default_factory=dict)
    captured: int = 0
    capture_failures: int = 0
    skipped_by_limit: int = 0
    bundle_paths: list[str] = field(default_factory=list)

    @classmethod
    def from_options(
        cls,
        options: Any,
        *,
        backend: Any | None = None,
        screenshot_func: ScreenshotFunc = _default_screenshot,
    ) -> "VisualDebugBundleWriter":
        flags = {
            "capture_debug_screenshots": bool(getattr(options, "capture_debug_screenshots", False)),
            "screenshot_on_failure": bool(getattr(options, "screenshot_on_failure", False)),
            "screenshot_on_camera_recovery": bool(getattr(options, "screenshot_on_camera_recovery", False)),
            "screenshot_on_timeout": bool(getattr(options, "screenshot_on_timeout", False)),
            "screenshot_on_edge_reject": bool(getattr(options, "screenshot_on_edge_reject", False)),
            "screenshot_on_lifecycle_transition": bool(getattr(options, "screenshot_on_lifecycle_transition", False)),
        }
        raw_dir = getattr(options, "debug_screenshot_dir", None)
        base_dir = Path(raw_dir) if raw_dir else None
        return cls(
            enabled=any(flags.values()),
            base_dir=base_dir,
            max_bundles=max(0, int(getattr(options, "max_debug_screenshots", 20) or 0)),
            backend=backend,
            screenshot_func=screenshot_func,
            trigger_flags=flags,
        )

    def metrics(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "captured": self.captured,
            "captureFailures": self.capture_failures,
            "skippedByLimit": self.skipped_by_limit,
            "bundlePaths": list(self.bundle_paths),
        }

    def reason_enabled(self, reason: str) -> bool:
        if not self.enabled:
            return False
        flag = _trigger_flag(reason)
        if flag is None:
            return bool(self.trigger_flags.get("capture_debug_screenshots"))
        return bool(self.trigger_flags.get(flag) or self.trigger_flags.get("capture_debug_screenshots") and reason == "final_summary")

    def _bundle_root(self, status: dict[str, Any] | None) -> Path:
        if self.base_dir is not None:
            return self.base_dir
        session = _session_path(status)
        return (session / DEFAULT_DEBUG_DIR) if session is not None else DEFAULT_DEBUG_DIR

    def capture(
        self,
        reason: str,
        *,
        daemon_status: dict[str, Any] | None = None,
        proposal: Any | None = None,
        action_trace: dict[str, Any] | None = None,
        readiness: dict[str, Any] | None = None,
        clicked_menu: dict[str, Any] | None = None,
        classification: str | None = None,
        loop_summary: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self.reason_enabled(reason):
            return None
        if self.captured >= self.max_bundles:
            self.skipped_by_limit += 1
            return None
        status = daemon_status if isinstance(daemon_status, dict) else {}
        warnings: list[str] = []
        root = self._bundle_root(status)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        bundle_dir = root / f"{stamp}_{_sanitize_reason(reason)}"
        bundle_dir.mkdir(parents=True, exist_ok=True)

        window = _window_rect(self.backend, warnings)
        mouse = _mouse_position(self.backend, warnings)
        region = None
        if window is not None:
            region = (window["x"], window["y"], window["width"], window["height"])
        screenshot_path: Path | None = None
        try:
            image = self.screenshot_func(region)
            screenshot_path = bundle_dir / "screenshot.png"
            image.save(str(screenshot_path))
        except Exception as error:  # noqa: BLE001
            self.capture_failures += 1
            warnings.append(f"screenshot capture failed: {type(error).__name__}: {error}")
            screenshot_path = None

        overlay = _read_json(_overlay_path(status))
        if status:
            _write_json(bundle_dir / "daemon_status.json", status)
        if overlay is not None:
            _write_json(bundle_dir / "overlay_debug_state.json", overlay)
        trace_excerpt = _trace_excerpt(action_trace)
        if trace_excerpt:
            _write_json(bundle_dir / "action_trace_excerpt.json", trace_excerpt)
        world_model_paths = _write_world_model_evidence(bundle_dir, status)
        knowledge_fabric_paths = _write_knowledge_fabric_evidence(bundle_dir, status)
        input_integrity = _input_integrity_payload(status, action_trace, extra)
        input_integrity_path = None
        if input_integrity:
            input_integrity_path = bundle_dir / "input_integrity_status.json"
            _write_json(input_integrity_path, input_integrity)

        proposal_summary = _proposal_summary(proposal)
        route_summary = _route_context_summary(status, proposal_summary, extra)
        phase, intent = _phase_intent(status)
        action_readiness = _safe_dict(readiness.get("actionReadiness")) if isinstance(readiness, dict) else {}
        action_need = _safe_dict(readiness.get("actionNeed")) if isinstance(readiness, dict) else {}
        overlay_health = _safe_dict(readiness.get("overlayHealth")) if isinstance(readiness, dict) else {}
        action_safety_evidence = _safe_dict(readiness.get("actionSafetyEvidence")) if isinstance(readiness, dict) else {}
        clicked = clicked_menu or trace_excerpt.get("clickedMenu") or _latest_clicked_menu(status)
        hover = trace_excerpt.get("hoverMenu") or _hover_menu(status)
        wall_loop_classification = _wall_loop_classification(reason, classification, route_summary, trace_excerpt, extra)
        click_action_classification = classification or trace_excerpt.get("finalClassification")
        final_decision = _final_decision(reason, classification, trace_excerpt, clicked if isinstance(clicked, dict) else None, extra)
        safe_aimpoint = proposal_summary.get("safeAimPoint") if isinstance(proposal_summary.get("safeAimPoint"), dict) else {}
        bundle = {
            "schema": SCHEMA,
            "reason": reason,
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "sessionPath": str(_session_path(status)) if _session_path(status) is not None else None,
            "bundleDir": str(bundle_dir),
            "screenshotPath": str(screenshot_path) if screenshot_path is not None else None,
            "screenshotCaptureFailed": screenshot_path is None,
            "daemonStatusPath": str(bundle_dir / "daemon_status.json") if status else None,
            "overlayDebugStatePath": str(bundle_dir / "overlay_debug_state.json") if overlay is not None else None,
            "actionTraceExcerptPath": str(bundle_dir / "action_trace_excerpt.json") if trace_excerpt else None,
            "worldModelEvidencePaths": world_model_paths,
            "knowledgeFabricEvidencePaths": knowledge_fabric_paths,
            "inputIntegrityStatusPath": str(input_integrity_path) if input_integrity_path is not None else None,
            "inputIntegrityStatus": input_integrity,
            "playerLocation": _player_location(status),
            "playerLocationSource": _player_location_source(status),
            "playerLocationConfidence": _player_location_confidence(status),
            "plane": (_player_location(status) or {}).get("plane") if _player_location(status) else None,
            "inventoryFreeSlots": _inventory_free_slots(status),
            "resourceCount": _resource_count(status),
            "inventoryState": _inventory_state(status),
            "currentIntent": intent or proposal_summary.get("proposedAction"),
            "phase": phase,
            "actionReadiness": action_readiness or None,
            "actionNeed": action_need or None,
            "overlayHealth": overlay_health or None,
            "actionSafetyEvidence": action_safety_evidence or None,
            "selectedActionIntent": action_readiness.get("intent") or intent or proposal_summary.get("proposedAction"),
            "selectedTarget": proposal_summary.get("selectedTarget"),
            "selectedTargetSource": proposal_summary.get("targetSource"),
            "selectedActionTargetSource": proposal_summary.get("actionTargetSource"),
            "selectedActionability": proposal_summary.get("actionability"),
            "advisoryTargetSource": proposal_summary.get("advisoryTargetSource"),
            "staleProposalDetected": proposal_summary.get("staleProposalDetected"),
            "staleProposalSource": proposal_summary.get("staleProposalSource"),
            "selectedWaypoint": route_summary.get("selectedWaypoint"),
            "routeNode": _status_context(status, "serviceRouteContext").get("currentNodeId")
            or status.get("serviceRouteCurrentNodeId"),
            "currentRouteMode": route_summary.get("routeMode"),
            "currentRouteNode": route_summary.get("currentNodeId"),
            "currentRouteEdge": route_summary.get("currentEdge"),
            "routeContextSummary": route_summary,
            "selectedServiceAnchor": route_summary.get("selectedServiceAnchor"),
            "selectedApproachNode": route_summary.get("selectedApproachNode"),
            "routeSourceMismatchDetails": route_summary.get("routeSourceMismatchDetails"),
            "pathingReason": route_summary.get("pathingReason"),
            "wallLoopClassification": wall_loop_classification,
            "projectionStatus": proposal_summary.get("routeProjectionStatus") or proposal_summary.get("resourceProjectionStatus"),
            "resourceViewScore": proposal_summary.get("resourceViewScore"),
            "resourceViewClassification": proposal_summary.get("resourceViewClassification"),
            "resourceCameraRecoveryRecommended": proposal_summary.get("resourceCameraRecoveryRecommended"),
            "serviceTargetExposure": proposal_summary.get("serviceTargetExposure"),
            "targetViewState": proposal_summary.get("targetViewState"),
            "targetViewPolicy": proposal_summary.get("targetViewPolicy"),
            "resourceTargetAmbiguity": proposal_summary.get("resourceTargetAmbiguity")
            or trace_excerpt.get("resourceTargetAmbiguity"),
            "aimpointSamplesTried": proposal_summary.get("aimpointSamplesTried"),
            "aimpointSampleResults": proposal_summary.get("aimpointSampleResults"),
            "selectedAimpointSource": proposal_summary.get("selectedAimpointSource"),
            "hoverConfirmedTopExpected": proposal_summary.get("hoverConfirmedTopExpected"),
            "safeAimPointStatus": safe_aimpoint.get("status"),
            "safeAimPointSummary": safe_aimpoint or None,
            "cameraState": _camera_state(status),
            "clientTickHotSummary": _client_tick_hot_summary(status),
            "daemonSessionPath": str(_session_path(status)) if _session_path(status) is not None else None,
            "daemonTick": status.get("latestTick") or status.get("tick"),
            "pluginSnapshotSessionPath": status.get("pluginSnapshotSessionPath"),
            "pluginSnapshotTick": status.get("pluginSnapshotLatestTick") or status.get("pluginSnapshotTick"),
            "hoverMenu": hover,
            "latestHoverMenu": hover,
            "clickedMenu": clicked,
            "latestMenuOptionClicked": clicked,
            "classification": click_action_classification,
            "clickActionClassification": click_action_classification,
            "finalDecision": final_decision,
            "proposal": proposal_summary,
            "actionProposalSummary": proposal_summary,
            "coordinateScaling": proposal_summary.get("clickPointResolution")
            or _safe_dict(trace_excerpt.get("intendedPoint"))
            or None,
            "actionTraceExcerpt": trace_excerpt,
            "humanInput": trace_excerpt.get("humanInput"),
            "liveInputBackend": _safe_dict(trace_excerpt.get("humanInput")).get("liveInputBackend") or trace_excerpt.get("liveInputBackend"),
            "directBackendBypassCount": _safe_dict(trace_excerpt.get("humanInput")).get("directBackendBypassCount"),
            "loopSummary": loop_summary if isinstance(loop_summary, dict) else None,
            "mousePosition": mouse,
            "windowRect": window,
            "canvasRect": window,
            "warnings": warnings,
        }
        if isinstance(extra, dict):
            bundle["extra"] = dict(extra)
        _write_json(bundle_dir / "bundle.json", bundle)
        self.captured += 1
        self.bundle_paths.append(str(bundle_dir))
        return {
            "schema": "visual_debug_bundle_event.v1",
            "reason": reason,
            "bundleDir": str(bundle_dir),
            "screenshotPath": str(screenshot_path) if screenshot_path is not None else None,
            "screenshotCaptureFailed": screenshot_path is None,
            "warnings": warnings,
        }

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from candidate_core import explain_candidate, target_freshness_issue
import dialogue_core
import safe_aimpoint_core

from .input_geometry import input_geometry_from_status, resolve_screen_click_point, source_canvas_size_from_status


SCHEMA = "action_proposal.v1"

KNOWN_ACTIONS = {
    "none",
    "select_resource_target",
    "resource_view_recovery",
    "wait_for_resource_result",
    "navigate_to_service",
    "interact_service_route_object",
    "interface_dialogue_choice",
    "open_service",
    "deposit_inventory",
    "deposit_resources",
    "close_bank",
    "return_to_resource_area",
    "wait_for_context",
}


@dataclass
class ActionProposal:
    proposed_action: str = "none"
    target_kind: str = "none"
    target_name: str | None = None
    target_tile: dict[str, Any] | None = None
    suggested_click_point: dict[str, int] | None = None
    click_point_space: str = "screen"
    resolved_screen_click_point: dict[str, int] | None = None
    click_point_resolution: dict[str, Any] | None = None
    input_geometry: dict[str, Any] | None = None
    suggested_world_tile: dict[str, Any] | None = None
    key_action: dict[str, str] | None = None
    target_explanation: dict[str, Any] | None = None
    reason: str = "not_applicable"
    confidence: float = 0.0
    required_context: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "PASS"
    source_tick: int | None = None

    @property
    def executable(self) -> bool:
        if self.proposed_action in {"none", "wait_for_context"}:
            return False
        if (
            self.target_kind == "path_tile"
            and isinstance(self.target_tile, dict)
            and self.proposed_action in {"navigate_to_service", "return_to_resource_area"}
        ):
            return True
        return bool(self.key_action or self.suggested_click_point)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "status": self.status,
            "proposedAction": self.proposed_action,
            "targetKind": self.target_kind,
            "targetName": self.target_name,
            "targetTile": self.target_tile,
            "suggestedClickPoint": self.suggested_click_point,
            "clickPointSpace": self.click_point_space,
            "resolvedScreenClickPoint": self.resolved_screen_click_point,
            "clickPointResolution": self.click_point_resolution,
            "inputGeometry": self.input_geometry,
            "suggestedWorldTile": self.suggested_world_tile,
            "keyAction": self.key_action,
            "targetExplanation": self.target_explanation,
            "reason": self.reason,
            "confidence": self.confidence,
            "requiredContext": list(self.required_context),
            "missingCapabilities": list(self.missing_capabilities),
            "warnings": list(self.warnings),
            "sourceTick": self.source_tick,
            "executable": self.executable,
        }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "open", "ready", "available", "complete"}:
            return True
        if text in {"false", "no", "0", "closed", "not_ready", "unavailable", "incomplete"}:
            return False
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _contains_any(haystack: Any, needles: list[Any] | tuple[Any, ...] | set[Any]) -> bool:
    text = _lower(haystack)
    return bool(text and any(_lower(needle) and _lower(needle) in text for needle in needles))


def _int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _target_name(target: Any) -> str | None:
    if not isinstance(target, dict) or not target:
        return None
    value = (
        target.get("targetName")
        or target.get("name")
        or target.get("label")
        or target.get("classId")
        or target.get("targetType")
        or target.get("id")
    )
    return str(value) if value is not None else None


def _tile_from(target: Any) -> dict[str, Any] | None:
    if not isinstance(target, dict):
        return None
    if isinstance(target.get("targetTile"), dict):
        return dict(target["targetTile"])
    if isinstance(target.get("returnDestinationTile"), dict):
        return dict(target["returnDestinationTile"])
    if target.get("worldX") is not None and target.get("worldY") is not None:
        return {
            "worldX": target.get("worldX"),
            "worldY": target.get("worldY"),
            "plane": target.get("plane", 0),
        }
    return None


def _point_from_aim(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    x = _first_present(value.get("x"), value.get("canvasX"), value.get("screenX"), value.get("centerX"))
    y = _first_present(value.get("y"), value.get("canvasY"), value.get("screenY"), value.get("centerY"))
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return {"x": int(round(float(x))), "y": int(round(float(y)))}
    return None


def _point_space_from_aim(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    if value.get("canvasX") is not None or value.get("canvasY") is not None or str(value.get("source") or "").lower().startswith("canvas"):
        return "canvas"
    if value.get("screenX") is not None or value.get("screenY") is not None:
        return "screen"
    return None


def _point_from_bounds(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("bounds"), dict):
        return _point_from_bounds(value.get("bounds"))
    x = _first_present(value.get("x"), value.get("canvasX"), value.get("screenX"), value.get("left"), value.get("minX"))
    y = _first_present(value.get("y"), value.get("canvasY"), value.get("screenY"), value.get("top"), value.get("minY"))
    width = _first_present(value.get("width"), value.get("w"))
    height = _first_present(value.get("height"), value.get("h"))
    if width is None and value.get("right") is not None and x is not None:
        width = float(value["right"]) - float(x)
    if height is None and value.get("bottom") is not None and y is not None:
        height = float(value["bottom"]) - float(y)
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        if isinstance(width, (int, float)) and isinstance(height, (int, float)):
            return {"x": int(round(float(x) + float(width) / 2.0)), "y": int(round(float(y) + float(height) / 2.0))}
        return {"x": int(round(float(x))), "y": int(round(float(y)))}
    return None


def _point_space_from_bounds(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("bounds"), dict):
        return _point_space_from_bounds(value.get("bounds"))
    if any(key in value for key in ("canvasX", "canvasY", "canvasMinX", "canvasMinY")):
        return "canvas"
    if any(key in value for key in ("screenX", "screenY", "screenMinX", "screenMinY")):
        return "screen"
    source = str(value.get("source") or "").lower()
    if "canvas" in source or "clickbox" in source or "convex" in source:
        return "canvas"
    return None


def _click_point_from(target: Any) -> dict[str, int] | None:
    if not isinstance(target, dict):
        return None
    for key in (
        "suggestedClickPoint",
        "clickPoint",
        "aimPoint",
        "aimPointContext",
        "canvasPoint",
        "canvasLocation",
        "canvasCenter",
    ):
        point = _point_from_aim(target.get(key))
        if point:
            return point
    for key in (
        "bounds",
        "clickboxBounds",
        "convexHullBounds",
        "geometrySummary",
        "closeButtonBounds",
        "depositInventoryButtonBounds",
    ):
        point = _point_from_bounds(target.get(key))
        if point:
            return point
    geometry = _dict(target.get("geometry"))
    if geometry:
        return _click_point_from(geometry)
    return None


def _click_point_space_from(target: Any) -> str:
    if not isinstance(target, dict):
        return "screen"
    for key in (
        "suggestedClickPoint",
        "clickPoint",
        "aimPoint",
        "aimPointContext",
        "canvasPoint",
        "canvasLocation",
        "canvasCenter",
    ):
        space = _point_space_from_aim(target.get(key))
        if space:
            return space
        if key.startswith("canvas") and isinstance(target.get(key), dict):
            return "canvas"
    for key in (
        "bounds",
        "clickboxBounds",
        "convexHullBounds",
        "geometrySummary",
        "closeButtonBounds",
        "depositInventoryButtonBounds",
    ):
        space = _point_space_from_bounds(target.get(key))
        if space:
            return space
        if key in {"clickboxBounds", "convexHullBounds"} and isinstance(target.get(key), dict):
            return "canvas"
    geometry = _dict(target.get("geometry"))
    if geometry:
        return _click_point_space_from(geometry)
    return "screen"


def _intent_overlay_context(status: dict[str, Any], brain: dict[str, Any]) -> dict[str, Any]:
    status_overlay = _dict(status.get("intentOverlayContext"))
    brain_overlay = _dict(brain.get("intentOverlayContext"))
    if status_overlay.get("markers") or status_overlay.get("backupMarkers"):
        return status_overlay
    return brain_overlay or status_overlay


def _overlay_selected(status: dict[str, Any], brain: dict[str, Any]) -> dict[str, Any]:
    overlay = _intent_overlay_context(status, brain)
    selected = _dict(overlay.get("selectedMarker") or overlay.get("selectedTarget"))
    if selected:
        return selected
    for marker in _list(overlay.get("markers")):
        if isinstance(marker, dict) and marker.get("markerType") == "selected_target":
            return marker
    return {}


def _target_key_for_suppression(target: dict[str, Any]) -> str | None:
    parts = [target.get("id"), target.get("worldX"), target.get("worldY"), target.get("plane", 0), target.get("classId")]
    if any(value is not None for value in parts):
        return ":".join(str(value) for value in parts)
    for key in ("targetKey", "objectKey", "candidateKey", "key", "markerId"):
        value = target.get(key)
        if value is not None:
            return str(value)
    return None


def _target_suppression_keys(target: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    primary = _target_key_for_suppression(target)
    if primary:
        keys.add(primary)
    for key in ("targetKey", "objectKey", "candidateKey", "key", "markerId"):
        value = target.get(key)
        if value is not None:
            keys.add(str(value))
    return keys


def _suppressed_resource_target_keys(status: dict[str, Any], brain: dict[str, Any]) -> set[str]:
    values = status.get("suppressedResourceTargetKeys")
    if not isinstance(values, list):
        values = brain.get("suppressedResourceTargetKeys")
    return {str(value) for value in values or [] if value is not None}


def _resource_candidate_lists(status: dict[str, Any], brain: dict[str, Any], active_target: dict[str, Any], overlay_selected: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for value in (active_target, overlay_selected):
        if value:
            candidates.append(dict(value))
    for key in ("returnBestResourceTarget", "brainBestTree", "bestResourceTarget", "selectedResourceTarget"):
        for value in (status.get(key), brain.get(key)):
            if isinstance(value, dict) and value:
                candidates.append(dict(value))
    for context_key in ("returnToResourceContext", "postBankReacquisitionContext", "currentContextSummary"):
        context = _dict(status.get(context_key)) or _dict(brain.get(context_key))
        for key in ("bestResourceTarget", "resourceTarget", "selectedResourceTarget", "bestTarget"):
            value = context.get(key)
            if isinstance(value, dict) and value:
                candidates.append(dict(value))
    for key in ("profileCandidates", "candidates", "candidateTargets"):
        for value in _list(brain.get(key)) + _list(status.get(key)):
            if isinstance(value, dict):
                candidates.append(dict(value))
    overlay = _intent_overlay_context(status, brain)
    for marker in _list(overlay.get("markers")):
        if isinstance(marker, dict) and marker.get("markerType") in {"selected_target", "backup_candidate"}:
            candidates.append(dict(marker))

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _target_key_for_suppression(candidate)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(candidate)
    return unique


def _is_resource_target_candidate(candidate: dict[str, Any] | None) -> bool:
    if not isinstance(candidate, dict) or not candidate:
        return False
    class_id = str(candidate.get("classId") or "").lower()
    target_type = str(candidate.get("targetType") or "").lower()
    name = str(candidate.get("targetName") or candidate.get("name") or "").lower()
    if class_id in {"resource_return", "path_tile", "bank_booth", "banker", "bank_chest", "deposit_box", "deposit_chest", "bank_related"}:
        return False
    if class_id in {"tree", "woodcutting_tree"}:
        return True
    if "tree" in class_id or "tree" in name:
        return True
    return "resource" in target_type and "return" not in class_id and "return" not in target_type


def _resource_target_from_context(status: dict[str, Any], brain: dict[str, Any], active_target: dict[str, Any], overlay_selected: dict[str, Any]) -> dict[str, Any]:
    target = active_target or overlay_selected
    candidates = _resource_candidate_lists(status, brain, active_target, overlay_selected)
    suppressed = _suppressed_resource_target_keys(status, brain)
    if not suppressed and _is_resource_target_candidate(target):
        return target
    target_keys = _target_suppression_keys(target) if target else set()
    if target_keys and not target_keys.intersection(suppressed) and _is_resource_target_candidate(target):
        return target
    for candidate in candidates:
        candidate_keys = _target_suppression_keys(candidate)
        if candidate_keys and candidate_keys.intersection(suppressed):
            continue
        if _is_resource_target_candidate(candidate):
            selected = dict(candidate)
            if suppressed:
                selected["reacquiredAfterSuppression"] = True
                selected["suppressedTargetKeysAtSelection"] = sorted(suppressed)
            return selected
    return target


def _camera_viewport_from_status(status: dict[str, Any] | None, brain: dict[str, Any] | None = None) -> dict[str, Any] | None:
    status = _dict(status)
    brain = _dict(brain)
    baseline = _dict(status.get("baseline"))
    for value in (
        status.get("cameraViewport"),
        baseline.get("cameraViewport"),
        brain.get("cameraViewport"),
        _dict(brain.get("baseline")).get("cameraViewport"),
    ):
        if isinstance(value, dict) and value:
            return value
    return None


def _target_has_aimpoint_geometry(target: dict[str, Any]) -> bool:
    for key in (
        "aimPoint",
        "aimPointContext",
        "suggestedClickPoint",
        "clickPoint",
        "canvasPoint",
        "canvasLocation",
        "canvasCenter",
        "clickableHull",
        "clickboxPolygon",
        "convexHull",
        "convexHullPolygon",
        "canvasTilePolygon",
        "tilePolygon",
        "clickboxBounds",
        "convexHullBounds",
        "bounds",
    ):
        if target.get(key) is not None:
            return True
    return False


def _resource_target_with_safe_aimpoint(
    target: dict[str, Any],
    *,
    source_canvas_size: dict[str, Any] | None,
    status: dict[str, Any] | None,
    brain: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    merged = dict(target)
    if not _target_has_aimpoint_geometry(merged):
        return merged, None
    safe = safe_aimpoint_core.safe_aimpoint_for_target(
        merged,
        source_canvas_size=source_canvas_size,
        viewport=_camera_viewport_from_status(status, brain),
    )
    merged["safeAimPoint"] = safe
    if safe.get("status") == "PASS" and safe.get("canvasX") is not None and safe.get("canvasY") is not None:
        merged["suggestedClickPoint"] = {
            "canvasX": safe.get("canvasX"),
            "canvasY": safe.get("canvasY"),
            "source": "safeAimPoint",
        }
    return merged, safe


def _resource_projection_status(
    target: dict[str, Any],
    *,
    safe_aimpoint: dict[str, Any] | None,
    source_canvas_size: dict[str, Any] | None,
    status: dict[str, Any] | None,
    brain: dict[str, Any] | None,
) -> dict[str, Any]:
    status = _dict(status)
    return safe_aimpoint_core.resource_projection_status(
        target,
        safe_aimpoint=safe_aimpoint,
        source_canvas_size=source_canvas_size,
        viewport=_camera_viewport_from_status(status, brain),
        source_cap_hit=_bool(status.get("sourceCapHit")),
        projection_cap_hit=_bool(status.get("compactLiveGeometryCapHit")),
        stale_projection=_bool(target.get("projectionStale")),
    )


def _projection_recovery_trigger(classification: str | None) -> str:
    value = str(classification or "")
    mapping = {
        "projection_sentinel": "resource_projection_sentinel",
        "edge_clipped": "resource_edge_projection",
        "offscreen": "resource_offscreen_projection",
        "tiny_projection": "resource_tiny_projection",
        "degenerate_projection": "resource_degenerate_projection",
        "no_projection": "resource_no_safe_aimpoint",
        "no_safe_aimpoint": "resource_no_safe_aimpoint",
        "no_visible_interactable_geometry": "resource_no_safe_aimpoint",
    }
    return mapping.get(value, "resource_no_safe_aimpoint")


def _resource_recovery_target(
    target: dict[str, Any],
    projection_status: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(target)
    merged["targetType"] = merged.get("targetType") or "sceneObject"
    merged["classId"] = merged.get("classId") or "tree"
    merged["resourceProjectionStatus"] = dict(projection_status)
    merged["bestLogicalResourceTarget"] = {
        key: value
        for key, value in {
            "name": merged.get("targetName") or merged.get("name"),
            "id": merged.get("id", merged.get("rawId")),
            "hash": merged.get("hash"),
            "worldX": merged.get("worldX"),
            "worldY": merged.get("worldY"),
            "plane": merged.get("plane"),
            "projectionClassification": projection_status.get("classification"),
        }.items()
        if value is not None
    }
    merged["selectedExecutableResourceTarget"] = None
    merged["recoverySuggested"] = True
    merged["recoveryAction"] = "camera_reacquire_resource_target"
    merged["cameraTriggeredBy"] = _projection_recovery_trigger(projection_status.get("classification"))
    return merged


def _resource_projection_recovery_proposal(
    *,
    target: dict[str, Any],
    projection_status: dict[str, Any],
    input_geometry: dict[str, Any] | None,
    source_canvas_size: dict[str, Any] | None,
    source_tick: int | None,
    status: dict[str, Any],
    brain: dict[str, Any],
) -> ActionProposal:
    recovery_target = _resource_recovery_target(target, projection_status)
    proposal = _proposal(
        "resource_view_recovery",
        target_kind="resource_recovery",
        target=recovery_target,
        key_action={
            "type": "camera_reacquire",
            "method": "keyboard_arrows",
            "command": "yaw_right_pitch_up",
            "cameraTriggeredBy": recovery_target.get("cameraTriggeredBy"),
            "durationMs": 180,
        },
        reason="resource_projection_recovery_needed",
        confidence=0.62,
        required_context=["target", "inventory", "camera.controller"],
        warnings=[f"resource candidates found but projection is not safely clickable: {projection_status.get('classification')}"],
        source_tick=source_tick,
        input_geometry=input_geometry,
        source_canvas_size=source_canvas_size,
        status=status,
        brain=brain,
        suppress_click_point=True,
    )
    if isinstance(proposal.target_explanation, dict):
        proposal.target_explanation["resourceProjectionStatus"] = dict(projection_status)
        proposal.target_explanation["bestLogicalResourceTarget"] = dict(recovery_target["bestLogicalResourceTarget"])
        proposal.target_explanation["selectedExecutableResourceTarget"] = None
        proposal.target_explanation["recoverySuggested"] = True
        proposal.target_explanation["cameraTriggeredBy"] = recovery_target.get("cameraTriggeredBy")
    return proposal


def _status_context(status_or_context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    status = status_or_context if isinstance(status_or_context, dict) else {}
    brain = _dict(status.get("brain")) or status
    return status, brain


def _proposal(
    action: str,
    *,
    target_kind: str,
    target: dict[str, Any] | None = None,
    click_point: dict[str, int] | None = None,
    key_action: dict[str, str] | None = None,
    reason: str,
    confidence: float,
    required_context: list[str] | None = None,
    warnings: list[str] | None = None,
    missing: list[str] | None = None,
    source_tick: int | None = None,
    input_geometry: dict[str, Any] | None = None,
    source_canvas_size: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
    brain: dict[str, Any] | None = None,
    suppress_click_point: bool = False,
) -> ActionProposal:
    target = target if isinstance(target, dict) else {}
    click = None if suppress_click_point else (click_point or _click_point_from(target))
    click_space = "screen" if click_point else _click_point_space_from(target)
    resolution = resolve_screen_click_point(
        click,
        click_point_space=click_space,
        input_geometry=input_geometry,
        source_canvas_size=source_canvas_size,
    ) if click else None
    resolved_screen = resolution.get("screenClickPoint") if isinstance(resolution, dict) and isinstance(resolution.get("screenClickPoint"), dict) else None
    proposal = ActionProposal(
        proposed_action=action if action in KNOWN_ACTIONS else "none",
        target_kind=target_kind,
        target_name=_target_name(target),
        target_tile=_tile_from(target),
        suggested_click_point=click,
        click_point_space=click_space,
        resolved_screen_click_point=resolved_screen,
        click_point_resolution=resolution,
        input_geometry=input_geometry,
        suggested_world_tile=_tile_from(target),
        key_action=key_action,
        target_explanation=explain_candidate(target, source_tick=source_tick, status=status or {"brain": brain or {}}) if target else None,
        reason=reason,
        confidence=confidence,
        required_context=required_context or [],
        missing_capabilities=missing or [],
        warnings=warnings or [],
        source_tick=source_tick,
    )
    if resolution and resolution.get("status") == "FAIL":
        proposal.status = "FAIL"
        proposal.missing_capabilities.extend(str(item) for item in resolution.get("missingCapabilities") or [])
        proposal.warnings.extend(str(item) for item in resolution.get("warnings") or [])
    elif proposal.proposed_action not in {"none", "wait_for_context"} and not proposal.executable:
        proposal.status = "WARN"
        if "click_point" not in proposal.missing_capabilities:
            proposal.missing_capabilities.append("click_point")
        if not any("click point" in warning for warning in proposal.warnings):
            proposal.warnings.append("missing click point or key action")
    elif proposal.warnings or proposal.missing_capabilities:
        proposal.status = "WARN"
    else:
        proposal.status = "PASS"
    return proposal


def _service_target(service: dict[str, Any], generic: dict[str, Any]) -> dict[str, Any]:
    return _dict(service.get("bestServiceCandidate") or service.get("bestServiceTarget") or service.get("target") or generic.get("activeIntentTarget"))


def _service_route_context(status: dict[str, Any], brain: dict[str, Any], service: dict[str, Any]) -> dict[str, Any]:
    route = _dict(brain.get("serviceRouteContext") or status.get("serviceRouteContext"))
    if route:
        return route
    return _dict(service.get("serviceRouteContext"))


def _return_route_context(status: dict[str, Any], brain: dict[str, Any]) -> dict[str, Any]:
    return _dict(brain.get("returnRouteContext") or status.get("returnRouteContext"))


def _dialogue_state(status: dict[str, Any], brain: dict[str, Any]) -> dict[str, Any]:
    return _dict(brain.get("dialogueState") or status.get("dialogueState"))


def _current_route_step(route_context: dict[str, Any]) -> dict[str, Any]:
    step = _dict(route_context.get("currentStep"))
    if step:
        return step
    index = _int(route_context.get("currentStepIndex"), None)
    steps = _list(route_context.get("routeSteps"))
    if index is not None and 0 <= index < len(steps) and isinstance(steps[index], dict):
        return dict(steps[index])
    return {}


def _dialogue_choice_target(choice: dict[str, Any], route_context: dict[str, Any]) -> dict[str, Any]:
    option = _dict(choice.get("option"))
    bounds = _dict(option.get("bounds"))
    if bounds:
        bounds.setdefault("source", "canvas_widget_bounds")
    return {
        "targetName": choice.get("selectedDialogueOption") or option.get("text") or "Dialogue option",
        "targetType": "interface_dialogue",
        "classId": "dialogue_choice",
        "dialoguePrompt": choice.get("dialoguePrompt"),
        "dialogueOptions": list(_list(choice.get("dialogueOptions"))),
        "expectedDialogueOption": choice.get("expectedDialogueOption"),
        "selectedDialogueOption": choice.get("selectedDialogueOption"),
        "selectionMethod": choice.get("selectionMethod"),
        "key": choice.get("key"),
        "option": option,
        "bounds": bounds,
        "routeId": route_context.get("routeId"),
        "routeStepIndex": route_context.get("currentStepIndex"),
        "routeStepLabel": _current_route_step(route_context).get("label"),
        "expectedPlaneChange": _current_route_step(route_context).get("planeChange"),
        "source": "dialogue_state",
    }


def _service_route_interaction_target(route_context: dict[str, Any]) -> dict[str, Any]:
    target = _dict(route_context.get("visibleInteractionTarget"))
    if not target:
        return {}
    merged = dict(target)
    if route_context.get("routeId") is not None:
        merged.setdefault("routeId", route_context.get("routeId"))
    if route_context.get("currentStepIndex") is not None:
        merged.setdefault("routeStepIndex", route_context.get("currentStepIndex"))
    current_step = _dict(route_context.get("currentStep"))
    if current_step:
        merged.setdefault("routeStepType", current_step.get("type"))
        merged.setdefault("routeStepLabel", current_step.get("label"))
        if isinstance(current_step.get("expectedOptions"), list):
            merged.setdefault("expectedOptions", list(current_step["expectedOptions"]))
        if isinstance(current_step.get("dialogueOpenerOptions"), list):
            merged.setdefault("dialogueOpenerOptions", list(current_step["dialogueOpenerOptions"]))
        if isinstance(current_step.get("dialogueExpectedPromptContains"), list):
            merged.setdefault("dialogueExpectedPromptContains", list(current_step["dialogueExpectedPromptContains"]))
        if isinstance(current_step.get("expectedTargetContains"), list):
            merged.setdefault("expectedTargets", list(current_step["expectedTargetContains"]))
        if current_step.get("planeChange") is not None:
            merged.setdefault("expectedPlaneChange", current_step.get("planeChange"))
    if isinstance(merged.get("expectedOptions"), list) and not isinstance(merged.get("actions"), list):
        merged["actions"] = list(merged["expectedOptions"]) + list(_list(merged.get("dialogueOpenerOptions")))
    return merged


def _service_route_service_target(route_context: dict[str, Any]) -> dict[str, Any]:
    target = _dict(route_context.get("visibleServiceTarget") or route_context.get("selectedServiceObject"))
    if not target:
        return {}
    current_step = _dict(route_context.get("currentStep"))
    if current_step and current_step.get("type") != "service_interact":
        return {}
    merged = dict(target)
    if route_context.get("routeId") is not None:
        merged.setdefault("routeId", route_context.get("routeId"))
    if route_context.get("currentStepIndex") is not None:
        merged.setdefault("routeStepIndex", route_context.get("currentStepIndex"))
    if current_step:
        merged.setdefault("routeStepType", current_step.get("type"))
        merged.setdefault("routeStepLabel", current_step.get("label"))
        if isinstance(current_step.get("expectedOptions"), list):
            merged.setdefault("expectedOptions", list(current_step["expectedOptions"]))
        if isinstance(current_step.get("expectedTargetContains"), list):
            merged.setdefault("expectedTargets", list(current_step["expectedTargetContains"]))
        if current_step.get("serviceType") is not None:
            merged.setdefault("serviceType", current_step.get("serviceType"))
    if isinstance(merged.get("expectedOptions"), list) and not isinstance(merged.get("actions"), list):
        merged["actions"] = list(merged["expectedOptions"])
    return merged


def _deposit_inventory_target(bank_ui: dict[str, Any]) -> dict[str, Any]:
    return {
        "targetName": "Deposit inventory",
        "bounds": _dict(
            bank_ui.get("depositInventoryButtonBounds")
            or bank_ui.get("depositInventoryButtonWidget")
            or bank_ui.get("depositInventoryWidget")
        ),
        "aimPoint": _point_from_aim(bank_ui.get("depositInventoryButtonAimPoint")),
    }


def _deposit_resource_target(bank_operation: dict[str, Any]) -> dict[str, Any]:
    bounds_values = _list(bank_operation.get("resourceItemSlotBounds"))
    widget_values = _list(bank_operation.get("resourceItemWidgets") or bank_operation.get("resourceSlotWidgets"))
    first_widget = widget_values[0] if widget_values else {}
    first_bounds = dict(bounds_values[0]) if bounds_values and isinstance(bounds_values[0], dict) else dict(_dict(first_widget).get("bounds") if isinstance(first_widget, dict) and isinstance(_dict(first_widget).get("bounds"), dict) else {})
    if first_bounds:
        first_bounds.setdefault("source", "bank_inventory_slot_widget_canvas")
    display_name = _text(bank_operation.get("resourceDisplayName")) or "resources"
    expected_targets = list(
        dict.fromkeys(
            target
            for target in (
                display_name,
                display_name.rstrip("s"),
                "Logs" if "log" in display_name.lower() else None,
                "Log" if "log" in display_name.lower() else None,
            )
            if target
        )
    )
    actions = _list(_dict(first_widget).get("actions")) or ["Deposit"]
    return {
        "targetName": display_name,
        "bounds": first_bounds,
        "resourceItemSlot": _dict(first_widget).get("slot"),
        "resourceItemId": _dict(first_widget).get("itemId"),
        "resourceItemQuantity": _dict(first_widget).get("quantity"),
        "actions": actions,
        "expectedOptions": ["Deposit"],
        "expectedTargets": expected_targets,
        "source": "bank_inventory_slot_widget" if first_bounds else "bank_operation_context",
    }


def _close_bank_key(close_bank: dict[str, Any], bank_ui: dict[str, Any]) -> dict[str, str] | None:
    keyboard_close_possible = _first_present(close_bank.get("keyboardClosePossible"), bank_ui.get("keyboardClosePossible"))
    return {"type": "key_press", "key": "escape"} if _bool(keyboard_close_possible) is True else None


def _close_bank_target(close_bank: dict[str, Any], bank_ui: dict[str, Any]) -> dict[str, Any]:
    bounds = (
        close_bank.get("closeButtonBounds")
        or close_bank.get("closeButtonWidget")
        or bank_ui.get("closeButtonBounds")
        or bank_ui.get("bankCloseButtonBounds")
        or bank_ui.get("closeButtonWidget")
        or bank_ui.get("bankCloseButtonWidget")
    )
    return {"targetName": "Close bank", "bounds": _dict(bounds)}


def _banking_complete(bank_operation: dict[str, Any]) -> bool:
    if _bool(bank_operation.get("bankingComplete")) is True:
        return True
    if _bool(bank_operation.get("operationNeeded")) is False and _int(bank_operation.get("resourceItemsHeld"), -1) == 0:
        return True
    return False


def _service_required(
    *,
    generic: dict[str, Any],
    inventory: dict[str, Any],
    service: dict[str, Any],
    bank_operation: dict[str, Any] | None = None,
    status: dict[str, Any],
) -> bool:
    phase = _lower(generic.get("phase") or status.get("brainPhase") or status.get("phase"))
    active_intent = _lower(generic.get("activeIntent") or status.get("activeIntent"))
    if phase in {"inventory_full", "needs_service", "route_to_service", "pathing_to_service"}:
        return True
    if active_intent in {"inventory_full", "needs_service", "route_to_service", "pathing_to_service"}:
        return True
    if _bool(_first_present(inventory.get("inventoryFull"), status.get("inventoryFull"))) is True:
        return True
    free_slots = _int(_first_present(inventory.get("freeSlots"), status.get("inventoryFreeSlots")), -1)
    if free_slots == 0:
        return True
    if _banking_complete(_dict(bank_operation)):
        return False
    return _bool(service.get("serviceNeeded")) is True


def _candidate_actions(candidate: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    for key in ("actions", "menuActions", "actionNames", "expectedOptions"):
        value = candidate.get(key)
        if isinstance(value, list):
            actions.extend(_text(item) for item in value if _text(item))
    return list(dict.fromkeys(actions))


def _hover_menu_from_status(status: dict[str, Any], brain: dict[str, Any]) -> dict[str, Any]:
    hot = _dict(status.get("clientTickHot") or brain.get("clientTickHot"))
    return _dict(hot.get("postMenuSort")) or _dict(hot.get("hoverMenu")) or _dict(status.get("hoverMenu"))


def _hover_route_steps(route_context: dict[str, Any]) -> list[dict[str, Any]]:
    steps = [dict(step) for step in _list(route_context.get("routeSteps")) if isinstance(step, dict)]
    current_step = _dict(route_context.get("currentStep"))
    if current_step and current_step.get("type") in {"interact_object", "service_interact"}:
        steps.insert(0, dict(current_step))
    if not steps:
        expected_options = _list(route_context.get("interactionExpectedOptions"))
        expected_targets = _list(route_context.get("interactionExpectedTargets"))
        if expected_options or expected_targets:
            steps.append(
                {
                    "type": "interact_object",
                    "label": "route interaction",
                    "expectedOptions": expected_options,
                    "expectedTargetContains": expected_targets,
                    "planeChange": route_context.get("expectedPlaneChange"),
                }
            )
    return [step for step in steps if step.get("type") in {"interact_object", "service_interact"}]


def _route_step_matches_hover(step: dict[str, Any], hover: dict[str, Any]) -> bool:
    option = hover.get("topOption") if hover.get("topOption") is not None else hover.get("option")
    target = hover.get("topTarget") if hover.get("topTarget") is not None else hover.get("target")
    option_text = _lower(option)
    target_text = _lower(target)
    if not option_text:
        return False
    if "walk here" in option_text or "attack" in option_text or "chop" in option_text or option_text == "cancel":
        return False
    expected_options = _list(step.get("expectedOptions"))
    dialogue_opener_options = _list(step.get("dialogueOpenerOptions"))
    expected_targets = _list(step.get("expectedTargetContains") or step.get("expectedTargets"))
    option_matches_expected = _contains_any(option_text, expected_options) if expected_options else True
    option_matches_dialogue_opener = bool(dialogue_opener_options and _contains_any(option_text, dialogue_opener_options))
    if expected_options and not option_matches_expected and not option_matches_dialogue_opener:
        return False
    if expected_targets and not _contains_any(target_text, expected_targets):
        return False
    if expected_options or expected_targets:
        return True
    if _contains_any(option_text, ["climb", "open"]) and _contains_any(target_text, ["stair", "stairs", "staircase", "ladder", "door", "gate"]):
        return True
    if _contains_any(option_text, ["bank", "use", "deposit"]) and _contains_any(target_text, ["bank", "booth", "banker", "deposit"]):
        return True
    return False


def _route_hover_interaction_target(
    *,
    status: dict[str, Any],
    brain: dict[str, Any],
    route_context: dict[str, Any],
) -> dict[str, Any]:
    if not route_context:
        return {}
    hover = _hover_menu_from_status(status, brain)
    if not hover:
        return {}
    current_step = _dict(route_context.get("currentStep"))
    current_step_is_interaction = current_step.get("type") in {"interact_object", "service_interact"}
    for step_index, step in enumerate(_hover_route_steps(route_context)):
        if not _route_step_matches_hover(step, hover):
            continue
        canvas_x = _int(hover.get("mouseCanvasX"), -1)
        canvas_y = _int(hover.get("mouseCanvasY"), -1)
        if canvas_x < 0 or canvas_y < 0:
            continue
        option = _text(hover.get("topOption") if hover.get("topOption") is not None else hover.get("option"))
        target_name = _text(hover.get("topTarget") if hover.get("topTarget") is not None else hover.get("target")) or _text(step.get("label")) or "Route interaction"
        step_type = str(step.get("type") or "interact_object")
        identifier = hover.get("topIdentifier") if hover.get("topIdentifier") is not None else hover.get("identifier")
        step_matches_current = current_step_is_interaction and (
            step is current_step
            or step.get("label") == current_step.get("label")
            or step.get("nodeId") == current_step.get("nodeId")
        )
        relevance_status = "PASS" if step_matches_current or route_context.get("routeObjectInterceptReady") is True else "WARN"
        rejection_reason = None if relevance_status == "PASS" else "hover_confirmed_but_route_unresolved"
        return {
            "targetName": target_name,
            "targetType": "sceneObject" if step_type == "interact_object" else "service",
            "classId": "service_route_transition" if step_type == "interact_object" else "bank_service",
            "id": _int(identifier, 0),
            "aimPoint": {"canvasX": canvas_x, "canvasY": canvas_y, "source": "client_tick_hot_hover"},
            "actions": [option] if option else [],
            "expectedOptions": list(_list(step.get("expectedOptions"))) or ([option] if option else []),
            "dialogueOpenerOptions": list(_list(step.get("dialogueOpenerOptions"))),
            "dialogueExpectedPromptContains": list(_list(step.get("dialogueExpectedPromptContains"))),
            "expectedTargets": list(_list(step.get("expectedTargetContains") or step.get("expectedTargets"))),
            "expectedPlaneChange": step.get("planeChange"),
            "routeId": route_context.get("routeId"),
            "routeStepIndex": step.get("routeStepIndex") if step.get("routeStepIndex") is not None else step_index,
            "routeStepType": step_type,
            "routeStepLabel": step.get("label"),
            "source": "client_tick_hot_hover",
            "hoverMenu": dict(hover),
            "verifiedLive": True,
            "routeRelevance": {
                "schema": "route_relevance.v1",
                "routeId": route_context.get("routeId"),
                "expectedStepType": step_type,
                "candidateName": target_name,
                "candidateActions": [option] if option else [],
                "relevanceStatus": relevance_status,
                "rejectionReason": rejection_reason,
            },
            "hoverDiscoveredRouteObject": True,
            "hoverDiscoveryStatus": "route_relevance_pass" if relevance_status == "PASS" else "hover_confirmed_but_route_unresolved",
        }
    return {}


def _tile_key(tile: Any) -> tuple[int | None, int | None, int | None]:
    tile = _dict(tile)
    return (
        _int(tile.get("worldX") if tile.get("worldX") is not None else tile.get("x"), None),
        _int(tile.get("worldY") if tile.get("worldY") is not None else tile.get("y"), None),
        _int(tile.get("plane"), 0),
    )


def _normalise_tile(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    world_x = _int(value.get("worldX") if value.get("worldX") is not None else value.get("x"), None)
    world_y = _int(value.get("worldY") if value.get("worldY") is not None else value.get("y"), None)
    if world_x is None or world_y is None:
        return None
    return {"worldX": world_x, "worldY": world_y, "plane": _int(value.get("plane"), 0)}


def _path_tiles(pathing: dict[str, Any]) -> list[dict[str, Any]]:
    tiles: list[dict[str, Any]] = []
    seen: set[tuple[int | None, int | None, int | None]] = set()
    for key in ("predictedPathTiles", "localScoutPath", "availableWaypointTiles"):
        for item in _list(pathing.get(key)):
            tile = _normalise_tile(item)
            if tile is None:
                continue
            tile_key = _tile_key(tile)
            if tile_key in seen:
                continue
            seen.add(tile_key)
            tiles.append(tile)
    return tiles


def _selected_route_waypoint(pathing: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    next_tile = _normalise_tile(pathing.get("nextWaypointTile"))
    mode = str(pathing.get("routeWaypointDistanceMode") or "adaptive")
    near_transition = _bool(pathing.get("routeWaypointNearTransition")) is True or str(pathing.get("nextEdgeType") or "").startswith(("interact_", "reacquire_"))
    lookahead = max(1, _int(pathing.get("routeWaypointLookaheadTiles"), 12))
    max_horizon = max(1, _int(pathing.get("routeWaypointMaxHorizonTiles"), 25))
    min_progress = max(1, _int(pathing.get("minRouteProgressTiles"), 3))
    tiles = _path_tiles(pathing)
    if mode != "adaptive" or near_transition or not tiles:
        return next_tile, {
            "schema": "route_waypoint_selection.v1",
            "mode": mode,
            "reason": "near_transition_precision" if near_transition else "next_waypoint",
            "waypointDistanceTiles": 1 if next_tile else None,
            "consideredTiles": len(tiles),
            "lookaheadTiles": lookahead,
            "maxHorizonTiles": max_horizon,
        }
    index = min(len(tiles), max_horizon, lookahead) - 1
    if index < min_progress - 1 and len(tiles) >= min_progress:
        index = min(len(tiles), max_horizon, min_progress) - 1
    selected = tiles[max(0, index)]
    return selected, {
        "schema": "route_waypoint_selection.v1",
        "mode": "adaptive",
        "reason": "long_visible_route_progress",
        "waypointDistanceTiles": max(1, index + 1),
        "consideredTiles": len(tiles),
        "lookaheadTiles": lookahead,
        "maxHorizonTiles": max_horizon,
        "minRouteProgressTiles": min_progress,
        "selectedTile": dict(selected),
        "nextWaypointTile": dict(next_tile) if next_tile else None,
    }


def _path_target(pathing: dict[str, Any], fallback: dict[str, Any], name: str) -> dict[str, Any]:
    target = _dict(pathing.get("nextWaypointTarget") or pathing.get("destination") or fallback)
    merged = dict(target)
    merged.setdefault("targetName", name)
    selected_waypoint, selection = _selected_route_waypoint(pathing)
    if isinstance(selected_waypoint, dict):
        merged["targetTile"] = dict(selected_waypoint)
        merged["routeWaypointSelection"] = selection
    for key in ("pathTargetTile", "destinationTile"):
        if isinstance(pathing.get(key), dict):
            merged.setdefault(key, pathing.get(key))
    for key in ("predictedPathTiles", "localScoutPath", "availableWaypointTiles"):
        if isinstance(pathing.get(key), list):
            merged.setdefault(key, list(pathing.get(key)))
    for key in (
        "routeMode",
        "goalDirectedFallback",
        "fallbackGoal",
        "fallbackApproachNode",
        "localFrontierWaypoint",
        "frontierDistanceBefore",
        "frontierDistanceAfterEstimate",
        "progressScore",
    ):
        if pathing.get(key) is not None:
            merged.setdefault(key, pathing.get(key))
    route_context = _dict(fallback.get("routeContext"))
    for key in ("routeMode", "goalDirectedFallback", "selectedServiceAnchor", "selectedApproachNode", "routeSourceMismatch"):
        if fallback.get(key) is not None:
            merged.setdefault(key, fallback.get(key))
        elif route_context.get(key) is not None:
            merged.setdefault(key, route_context.get(key))
    for key in ("nextWaypointAimPoint", "pathClickPoint", "destinationAimPoint"):
        if isinstance(pathing.get(key), dict):
            merged.setdefault("aimPoint", pathing.get(key))
            break
    return merged


def _resource_selection_proposal(
    *,
    status: dict[str, Any],
    brain: dict[str, Any],
    active_target: dict[str, Any],
    overlay_selected: dict[str, Any],
    inventory: dict[str, Any],
    input_geometry: dict[str, Any] | None,
    source_canvas_size: dict[str, Any] | None,
    source_tick: int | None,
    reason: str,
    confidence: float,
) -> ActionProposal | None:
    inventory_full = _bool(_first_present(inventory.get("inventoryFull"), status.get("inventoryFull")))
    free_slots = _int(_first_present(inventory.get("freeSlots"), status.get("inventoryFreeSlots")), -1)
    target = _resource_target_from_context(status, brain, active_target, overlay_selected)
    if inventory_full is True or free_slots == 0 or not _is_resource_target_candidate(target):
        return None
    freshness_issue = target_freshness_issue(status, brain, target, source_tick)
    if freshness_issue:
        return _proposal(
            "wait_for_context",
            target_kind="none",
            reason="candidate_data_stale",
            confidence=0.35,
            warnings=[f"candidate data stale; refusing target selection: {freshness_issue}"],
            missing=["target.freshness"],
            required_context=["target", "inventory"],
            source_tick=source_tick,
            input_geometry=input_geometry,
            source_canvas_size=source_canvas_size,
            status=status,
            brain=brain,
        )
    target, safe_aimpoint = _resource_target_with_safe_aimpoint(
        target,
        source_canvas_size=source_canvas_size,
        status=status,
        brain=brain,
    )
    projection_status = _resource_projection_status(
        target,
        safe_aimpoint=safe_aimpoint,
        source_canvas_size=source_canvas_size,
        status=status,
        brain=brain,
    )
    if (
        safe_aimpoint is None or safe_aimpoint.get("status") != "PASS"
    ) and projection_status.get("recoverySuggested") is True and _tile_from(target):
        return _resource_projection_recovery_proposal(
            target=target,
            projection_status=projection_status,
            input_geometry=input_geometry,
            source_canvas_size=source_canvas_size,
            source_tick=source_tick,
            status=status,
            brain=brain,
        )
    if safe_aimpoint and safe_aimpoint.get("status") != "PASS":
        safe_reason = str(safe_aimpoint.get("rejectionReason") or "safe aim point unavailable")
        return _proposal(
            "select_resource_target",
            target_kind="resource",
            target=target,
            reason="resource_target_not_actionable",
            confidence=0.35,
            required_context=["target", "inventory"],
            warnings=[f"safe aim point unavailable: {safe_reason}"],
            missing=["safe_aimpoint"],
            source_tick=source_tick,
            input_geometry=input_geometry,
            source_canvas_size=source_canvas_size,
            status=status,
            brain=brain,
            suppress_click_point=True,
        )
    return _proposal(
        "select_resource_target",
        target_kind="resource",
        target=target,
        reason=reason,
        confidence=confidence,
        required_context=["target", "inventory"],
        source_tick=source_tick,
        input_geometry=input_geometry,
        source_canvas_size=source_canvas_size,
        status=status,
        brain=brain,
    )


def build_action_proposal(status_or_context: dict[str, Any]) -> ActionProposal:
    status, brain = _status_context(status_or_context)
    input_geometry = input_geometry_from_status(status)
    source_canvas_size = source_canvas_size_from_status(status)
    generic = _dict(brain.get("genericTaskState"))
    inventory = _dict(brain.get("inventoryContext"))
    service = _dict(brain.get("serviceContext"))
    pathing = _dict(brain.get("pathingContext"))
    bank_ui = _dict(brain.get("bankUiContext"))
    bank_operation = _dict(brain.get("bankOperationContext"))
    close_bank = _dict(brain.get("closeBankContext"))
    resource_return = _dict(brain.get("resourceReturnContext"))
    service_route = _service_route_context(status, brain, service)
    return_route = _return_route_context(status, brain)
    active_target = _dict(generic.get("activeIntentTarget"))
    overlay_selected = _overlay_selected(status, brain)
    source_tick = status.get("latestTick") if isinstance(status.get("latestTick"), int) else None

    if _bool(bank_ui.get("bankPinOpen")) is True or "bank_pin_required" in _list(generic.get("blockingConditions")):
        return _proposal(
            "wait_for_context",
            target_kind="none",
            reason="bank_pin_required",
            confidence=1.0,
            warnings=["bank_pin_required"],
            required_context=["bank_ui"],
            source_tick=source_tick,
            input_geometry=input_geometry,
            source_canvas_size=source_canvas_size,
            status=status,
            brain=brain,
        )

    service_required = _service_required(
        generic=generic,
        inventory=inventory,
        service=service,
        bank_operation=bank_operation,
        status=status,
    )
    banking_complete = _banking_complete(bank_operation) and not service_required

    if banking_complete and _bool(close_bank.get("closeBankReady")) is True:
        key_action = _close_bank_key(close_bank, bank_ui)
        return _proposal(
            "close_bank",
            target_kind="bank_ui",
            target=_close_bank_target(close_bank, bank_ui),
            key_action=key_action,
            reason="close_bank_ready",
            confidence=0.95,
            required_context=["bank_operation", "close_bank"],
            source_tick=source_tick,
            input_geometry=input_geometry,
            source_canvas_size=source_canvas_size,
            status=status,
            brain=brain,
        )

    if _bool(bank_ui.get("bankReadable")) is True and _int(bank_operation.get("resourceItemsHeld")) > 0:
        deposit_available = _bool(_first_present(bank_operation.get("depositInventoryAvailable"), bank_ui.get("depositInventoryButtonVisible")))
        non_resource_items = _int(bank_operation.get("nonResourceItemsHeld"), 0)
        if deposit_available is True and non_resource_items <= 0:
            return _proposal(
                "deposit_inventory",
                target_kind="bank_ui",
                target=_deposit_inventory_target(bank_ui),
                reason="deposit_inventory_available",
                confidence=0.9,
                required_context=["bank_ui", "bank_operation"],
                source_tick=source_tick,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
        if deposit_available is True and non_resource_items > 0:
            return _proposal(
                "deposit_resources",
                target_kind="bank_ui",
                target=_deposit_resource_target(bank_operation),
                reason="protected_non_resource_items_present",
                confidence=0.78,
                warnings=["protected item or non-resource inventory item present; depositing target resources selectively"],
                required_context=["bank_ui", "bank_operation"],
                source_tick=source_tick,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
        return _proposal(
            "deposit_resources",
            target_kind="bank_ui",
            target=_deposit_resource_target(bank_operation),
            reason="deposit_inventory_unavailable",
            confidence=0.72,
            required_context=["bank_ui", "bank_operation"],
            source_tick=source_tick,
            input_geometry=input_geometry,
            source_canvas_size=source_canvas_size,
            status=status,
            brain=brain,
        )

    dialogue_state = _dialogue_state(status, brain)
    dialogue_route = service_route if service_required and service_route else (return_route or service_route)
    if dialogue_state.get("active") is True:
        dialogue_choice = dialogue_core.route_dialogue_choice(dialogue_state, _current_route_step(dialogue_route))
        if isinstance(dialogue_choice, dict) and dialogue_choice.get("status") == "PASS":
            key_action = None
            if dialogue_choice.get("selectionMethod") == "number_key" and dialogue_choice.get("key"):
                key_action = {"type": "key_press", "key": str(dialogue_choice["key"])}
            return _proposal(
                "interface_dialogue_choice",
                target_kind="interface_dialogue",
                target=_dialogue_choice_target(dialogue_choice, dialogue_route),
                key_action=key_action,
                reason="route_transition_dialogue_choice_ready",
                confidence=0.92,
                required_context=["dialogue_state", "return_route" if return_route else "service_route"],
                source_tick=source_tick,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
                suppress_click_point=dialogue_choice.get("selectionMethod") == "number_key",
            )
        return _proposal(
            "interface_dialogue_choice",
            target_kind="interface_dialogue",
            target=_dialogue_choice_target(dialogue_choice or {}, dialogue_route),
            reason=str((dialogue_choice or {}).get("reason") or "dialogue_state_active_but_unresolved"),
            confidence=0.35,
            warnings=["dialogue_state active but no route-correct selectable option was found"],
            missing=["dialogue_state.expected_option"],
            required_context=["dialogue_state", "return_route" if return_route else "service_route"],
            source_tick=source_tick,
            input_geometry=input_geometry,
            source_canvas_size=source_canvas_size,
            status=status,
            brain=brain,
            suppress_click_point=True,
        )

    return_route_target = _service_route_interaction_target(return_route)
    if not return_route_target:
        return_route_target = _route_hover_interaction_target(status=status, brain=brain, route_context=return_route)
    if not service_required:
        if (_bool(return_route.get("returnActionReady")) is True or _bool(return_route.get("actionReady")) is True) and return_route_target:
            return_route_target, _safe_aimpoint = _resource_target_with_safe_aimpoint(
                return_route_target,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
            return _proposal(
                "interact_service_route_object",
                target_kind="service_route_object",
                target=return_route_target,
                reason=str(return_route.get("state") or return_route.get("routeStepStatus") or "return_transition_actionable"),
                confidence=0.76,
                required_context=["return_route", "client_tick"],
                source_tick=source_tick,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
        return_target_relevance = _dict(return_route_target.get("routeRelevance")) if return_route_target else {}
        if return_route_target and return_route_target.get("source") == "client_tick_hot_hover" and return_target_relevance.get("relevanceStatus") == "PASS":
            return_route_target, _safe_aimpoint = _resource_target_with_safe_aimpoint(
                return_route_target,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
            return _proposal(
                "interact_service_route_object",
                target_kind="service_route_object",
                target=return_route_target,
                reason="client_tick_hot_return_route_object_hover",
                confidence=0.73,
                required_context=["return_route", "client_tick"],
                source_tick=source_tick,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )

    post_bank_resource_visible = (
        _bool(resource_return.get("resourceTargetCurrentlyVisible")) is True
        or _bool(status.get("postBankResourceTargetAvailable")) is True
    )
    if not service_required and banking_complete and _bool(bank_ui.get("bankOpen")) is not True and post_bank_resource_visible:
        resource_proposal = _resource_selection_proposal(
            status=status,
            brain=brain,
            active_target=active_target,
            overlay_selected=overlay_selected,
            inventory=inventory,
            input_geometry=input_geometry,
            source_canvas_size=source_canvas_size,
            source_tick=source_tick,
            reason="post_service_resource_reacquired",
            confidence=0.84,
        )
        if resource_proposal is not None and resource_proposal.executable and resource_proposal.proposed_action == "select_resource_target":
            return resource_proposal

    if not service_required:
        return_navigation_target = _dict(return_route.get("currentNavigationTarget"))
        if return_navigation_target:
            target = _path_target(pathing, return_navigation_target, "Resource return")
            return _proposal(
                "return_to_resource_area",
                target_kind="path_tile",
                target=target,
                reason=str(return_route.get("state") or return_route.get("routeStepStatus") or "return_route_ready"),
                confidence=0.8,
                required_context=["return_route", "pathing"],
                source_tick=source_tick,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )

        if _bool(resource_return.get("returnDestinationAvailable")) is True:
            target = _path_target(pathing, active_target or {"returnDestinationTile": resource_return.get("returnDestinationTile")}, "Resource return")
            return _proposal(
                "return_to_resource_area",
                target_kind="path_tile",
                target=target,
                reason=str(resource_return.get("reason") or "using_remembered_resource_area"),
                confidence=0.78,
                required_context=["resource_return", "pathing"],
                source_tick=source_tick,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )

    service_ready = _bool(_first_present(service.get("serviceReady"), pathing.get("serviceReady"))) is True
    if service_ready and _bool(bank_ui.get("bankOpen")) is not True and banking_complete:
        return _proposal(
            "wait_for_context",
            target_kind="none",
            reason="service_complete_waiting_for_return_context",
            confidence=0.84,
            required_context=["bank_operation", "resource_return"],
            source_tick=source_tick,
            input_geometry=input_geometry,
            source_canvas_size=source_canvas_size,
            status=status,
            brain=brain,
        )

    if service_ready and _bool(bank_ui.get("bankOpen")) is not True:
        target, _safe_aimpoint = _resource_target_with_safe_aimpoint(
            _service_target(service, generic),
            source_canvas_size=source_canvas_size,
            status=status,
            brain=brain,
        )
        return _proposal(
            "open_service",
            target_kind="service",
            target=target,
            reason="service_ready_bank_closed",
            confidence=0.86,
            required_context=["service", "bank_ui"],
            source_tick=source_tick,
            input_geometry=input_geometry,
            source_canvas_size=source_canvas_size,
            status=status,
            brain=brain,
        )

    if banking_complete and _bool(bank_ui.get("bankOpen")) is not True:
        post_bank_target = _resource_target_from_context(status, brain, active_target, overlay_selected)
        post_bank_target_class = str(post_bank_target.get("classId") or "").lower()
        post_bank_target_type = str(post_bank_target.get("targetType") or "").lower()
        post_bank_resource_visible = post_bank_target and (
            post_bank_target_class in {"tree", "woodcutting_tree"}
            or "resource" in post_bank_target_type
            or "tree" in post_bank_target_class
        )
        if not post_bank_resource_visible:
            return _proposal(
                "wait_for_context",
                target_kind="none",
                reason="service_complete_waiting_for_return_context",
                confidence=0.84,
                required_context=["bank_operation", "resource_return"],
                source_tick=source_tick,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )

    if not banking_complete:
        route_target = _service_route_interaction_target(service_route)
        route_service_target = _service_route_service_target(service_route)
        if _bool(service_route.get("actionReady")) is True and route_service_target:
            route_service_target, _safe_aimpoint = _resource_target_with_safe_aimpoint(
                route_service_target,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
            return _proposal(
                "open_service",
                target_kind="service",
                target=route_service_target,
                reason=str(service_route.get("routeStepStatus") or "service_target_actionable"),
                confidence=0.82,
                required_context=["service_route", "client_tick", "bank_ui"],
                source_tick=source_tick,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
        if not route_target:
            route_target = _route_hover_interaction_target(status=status, brain=brain, route_context=service_route)
        if _bool(service_route.get("actionReady")) is True and route_target:
            route_target, _safe_aimpoint = _resource_target_with_safe_aimpoint(
                route_target,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
            return _proposal(
                "interact_service_route_object",
                target_kind="service_route_object",
                target=route_target,
                reason=str(service_route.get("routeStepStatus") or "service_route_interaction_ready"),
                confidence=0.74,
                required_context=["service_route", "client_tick"],
                source_tick=source_tick,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
        route_target_relevance = _dict(route_target.get("routeRelevance")) if route_target else {}
        if route_target and route_target.get("source") == "client_tick_hot_hover" and route_target_relevance.get("relevanceStatus") == "PASS":
            route_target, _safe_aimpoint = _resource_target_with_safe_aimpoint(
                route_target,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
            return _proposal(
                "interact_service_route_object",
                target_kind="service_route_object",
                target=route_target,
                reason="client_tick_hot_route_object_hover",
                confidence=0.73,
                required_context=["service_route", "client_tick"],
                source_tick=source_tick,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )

        if _bool(pathing.get("pathingNeeded")) is True:
            route_navigation_target = _dict(service_route.get("currentNavigationTarget"))
            return _proposal(
                "navigate_to_service",
                target_kind="path_tile",
                target=_path_target(pathing, route_navigation_target or _service_target(service, generic), "Service waypoint"),
                reason="pathing_to_service",
                confidence=0.72,
                required_context=["pathing"],
                source_tick=source_tick,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                status=status,
            brain=brain,
        )

    resource_proposal = _resource_selection_proposal(
        status=status,
        brain=brain,
        active_target=active_target,
        overlay_selected=overlay_selected,
        inventory=inventory,
        input_geometry=input_geometry,
        source_canvas_size=source_canvas_size,
        source_tick=source_tick,
        reason="resource_target_visible",
        confidence=0.82,
    )
    if resource_proposal is not None:
        return resource_proposal

    return _proposal(
        "wait_for_context",
        target_kind="none",
        reason="no_executable_action",
        confidence=0.2,
        warnings=["no executable action from current context"],
        source_tick=source_tick,
        input_geometry=input_geometry,
        source_canvas_size=source_canvas_size,
        status=status,
        brain=brain,
    )

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from candidate_core import (
    explain_candidate,
    preferred_woodcutting_resource_candidate,
    target_freshness_issue,
    target_matches,
    woodcutting_level_from_context,
    woodcutting_required_level,
)
import dialogue_core
import safe_aimpoint_core
import target_view_core

from . import camera_control
from .input_geometry import input_geometry_from_status, resolve_screen_click_point, source_canvas_size_from_status


SCHEMA = "action_proposal.v1"

SERVICE_MIN_VISIBLE_AREA_PX = 96.0
SERVICE_MIN_VISIBLE_AREA_RATIO = 0.35
SERVICE_MIN_EDGE_DISTANCE_PX = 32.0
SERVICE_COMFORTABLE_EDGE_DISTANCE_PX = 48.0
SERVICE_COMFORTABLE_REGION_FRACTION = 0.78
RESOURCE_MEMORY_WORKSITE_RADIUS_TILES = 18
RESOURCE_RETURN_REACQUIRE_RADIUS_TILES = 8

KNOWN_ACTIONS = {
    "none",
    "select_resource_target",
    "resource_view_recovery",
    "wait_for_resource_result",
    "navigate_to_service",
    "interact_service_route_object",
    "service_view_recovery",
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
    action_target_source: str | None = None
    actionability: str | None = None

    @property
    def executable(self) -> bool:
        if self.proposed_action in {"none", "wait_for_context"}:
            return False
        if self.actionability in {"advisory_only", "stale", "blocked"} or str(self.actionability or "").startswith("blocked_"):
            return False
        if (
            self.target_kind == "path_tile"
            and isinstance(self.target_tile, dict)
            and self.proposed_action in {"navigate_to_service", "return_to_resource_area"}
        ):
            if self.suggested_click_point:
                return True
            return self.action_target_source in {"local_frontier_waypoint", "live_projected_waypoint"}
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
            "actionTargetSource": self.action_target_source,
            "actionability": self.actionability,
            "staleProposalDetected": self.actionability == "stale",
            "staleProposalSource": self.action_target_source if self.actionability == "stale" else None,
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


def _resource_live_actions(target: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("actions", "actionNames", "menuActions"):
        for item in _list(target.get(key)):
            if item is not None:
                values.append(str(item))
    return values


def _has_explicit_live_actions(target: dict[str, Any]) -> bool:
    return any(isinstance(target.get(key), list) for key in ("actions", "actionNames", "menuActions"))


def _resource_live_action_status(target: dict[str, Any]) -> dict[str, Any]:
    target = _dict(target)
    actions = _resource_live_actions(target)
    matching = [action for action in actions if "chop" in _lower(action)]
    name = _lower(_target_name(target) or target.get("objectName"))
    explicit = _has_explicit_live_actions(target)
    blocked = bool(explicit and not matching)
    reasons: list[str] = []
    if blocked:
        if "stump" in name:
            reasons.append("resource_stump_no_live_action")
        reasons.append("no_matching_live_resource_action")
    return {
        "liveActionsExplicit": explicit,
        "liveActions": actions,
        "matchingLiveResourceActions": matching,
        "hasMatchingLiveResourceAction": bool(matching),
        "blockedByLiveAction": blocked,
        "rejectionReasons": reasons,
    }


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


def _number(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _clamp_float(value: float, lower: float, upper: float) -> float:
    return max(float(lower), min(float(upper), float(value)))


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


def _action_target_source(target: dict[str, Any], *, target_kind: str) -> str | None:
    explicit = target.get("actionTargetSource") or target.get("action_target_source")
    if explicit:
        return str(explicit)
    if target.get("markerType") in {"selected_target", "backup_candidate"}:
        return "overlay_marker"
    source = target.get("source")
    if isinstance(source, dict):
        source_type = str(source.get("type") or source.get("sourceType") or "").lower()
        file_type = str(source.get("fileType") or source.get("sourceFileType") or "").lower()
        if target_kind == "resource" and (source.get("staticIndex") is True or source_type in {"world_targets", "live_targets"} or file_type == "world"):
            return "live_resource_candidate"
        if target_kind in {"service", "service_route_object"}:
            return "live_service_object" if target_kind == "service" else "live_route_object"
        return "unknown"
    if source == "client_tick_hot_hover":
        return "hover_discovered_object"
    if target_kind == "resource" and source:
        return "live_resource_candidate"
    if target_kind == "resource" and _is_resource_target_candidate(target) and (
        target.get("targetLiveState")
        or target.get("directReachability")
        or target.get("pathLengthTiles") is not None
        or target.get("sourceTick") is not None
        or target.get("tick") is not None
    ):
        return "live_resource_candidate"
    if target_kind == "path_tile" and source:
        return str(source)
    return str(source) if source else None


def _actionability(target: dict[str, Any], *, target_kind: str, click: dict[str, Any] | None, key_action: dict[str, Any] | None) -> str | None:
    explicit = target.get("actionability")
    if explicit:
        return str(explicit)
    source = str(target.get("source") or "").lower()
    if source in {"static_route_prior", "route_context_goal"}:
        return "advisory_only"
    if target.get("stale") is True:
        return "stale"
    if target_kind == "path_tile":
        return "needs_live_projection"
    if click or key_action:
        return "needs_hover_confirmation"
    return None


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


def _suppressed_action_target_keys(status: dict[str, Any], brain: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for source in (status, brain):
        if not isinstance(source, dict):
            continue
        for field in ("suppressedActionTargetKeys", "suppressedNavigationTargetKeys"):
            values = source.get(field)
            if isinstance(values, list):
                keys.update(str(value) for value in values if value is not None)
    return keys


def _route_tile_suppression_keys(
    tile: dict[str, Any] | None,
    *,
    class_id: Any = None,
    target_id: Any = None,
) -> set[str]:
    tile = _normalise_tile(tile)
    if tile is None:
        return set()
    class_values: list[Any] = []
    for value in (class_id, None):
        if value not in class_values:
            class_values.append(value)
    id_values: list[Any] = []
    for value in (target_id, None):
        if value not in id_values:
            id_values.append(value)
    keys: set[str] = set()
    for object_id in id_values:
        for target_class in class_values:
            keys.add(":".join(str(value) for value in (object_id, tile["worldX"], tile["worldY"], tile.get("plane", 0), target_class)))
    return keys


def _route_tile_is_suppressed(
    tile: dict[str, Any] | None,
    suppressed_keys: set[str],
    *,
    class_id: Any = None,
    target_id: Any = None,
) -> bool:
    return bool(suppressed_keys and _route_tile_suppression_keys(tile, class_id=class_id, target_id=target_id).intersection(suppressed_keys))


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


def _resource_target_from_context(
    status: dict[str, Any],
    brain: dict[str, Any],
    active_target: dict[str, Any],
    overlay_selected: dict[str, Any],
    *,
    source_canvas_size: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = active_target or overlay_selected
    candidates = _resource_candidate_lists(status, brain, active_target, overlay_selected)
    suppressed = _suppressed_resource_target_keys(status, brain)
    woodcutting_level = woodcutting_level_from_context(status, brain)
    resource_candidates: list[dict[str, Any]] = []
    for candidate in ([target] if isinstance(target, dict) and target else []) + candidates:
        candidate_keys = _target_suppression_keys(candidate)
        if candidate_keys and candidate_keys.intersection(suppressed):
            continue
        if _is_resource_target_candidate(candidate):
            resource_candidates.append(candidate)
    selected = _preferred_resource_candidate_for_view(
        resource_candidates,
        status=status,
        brain=brain,
        source_canvas_size=source_canvas_size,
        woodcutting_level=woodcutting_level,
        suppressed_keys=suppressed,
    )
    if selected is None:
        selected = preferred_woodcutting_resource_candidate(
            [candidate for candidate in resource_candidates if _resource_live_action_status(candidate).get("blockedByLiveAction") is not True],
            woodcutting_level=woodcutting_level,
            suppressed_keys=suppressed,
        )
    if selected:
        selected = dict(selected)
        if suppressed:
            selected["reacquiredAfterSuppression"] = True
            selected["suppressedTargetKeysAtSelection"] = sorted(suppressed)
        if target and not target_matches(selected, target):
            selected["reacquiredFromResourceTarget"] = {
                "name": _target_name(target),
                "id": target.get("id"),
                "worldX": target.get("worldX"),
                "worldY": target.get("worldY"),
                "plane": target.get("plane"),
            }
            selected["resourceSelectionReason"] = "preferred_skill_eligible_resource_candidate"
        return selected
    if target and _is_resource_target_candidate(target):
        target_keys = _target_suppression_keys(target)
        if target_keys and target_keys.intersection(suppressed):
            return {}
        action_status = _resource_live_action_status(target)
        target = dict(target)
        target["resourceLiveActionStatus"] = action_status
        if action_status.get("blockedByLiveAction") is True:
            target["actionability"] = "blocked_no_matching_action"
            target["resourceSelectionRejectionReason"] = ",".join(action_status.get("rejectionReasons") or ["no_matching_live_resource_action"])
            return target
        required = woodcutting_required_level(target)
        if required is not None:
            if woodcutting_level is None and required > 1:
                return {}
            if woodcutting_level is not None and required > woodcutting_level:
                return {}
    return target


def _world_model_summary_viewport(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    metadata = _dict(_dict(summary).get("metadata"))
    viewport = _dict(metadata.get("viewport"))
    if not viewport:
        return None
    merged = dict(viewport)
    if metadata.get("cameraYaw") is not None and merged.get("cameraYaw") is None:
        merged["cameraYaw"] = metadata.get("cameraYaw")
    if metadata.get("cameraPitch") is not None and merged.get("cameraPitch") is None:
        merged["cameraPitch"] = metadata.get("cameraPitch")
    return merged


def _camera_viewport_from_status(status: dict[str, Any] | None, brain: dict[str, Any] | None = None) -> dict[str, Any] | None:
    status = _dict(status)
    brain = _dict(brain)
    baseline = _dict(status.get("baseline"))
    world_model_summary = _dict(status.get("worldModelSummary"))
    world_model_payloads = _dict(status.get("worldModelPayloads"))
    payload_summary = _dict(world_model_payloads.get("world_model_summary"))
    for value in (
        status.get("cameraViewport"),
        baseline.get("cameraViewport"),
        status.get("worldModelCameraViewport"),
        brain.get("cameraViewport"),
        brain.get("worldModelCameraViewport"),
        _dict(brain.get("baseline")).get("cameraViewport"),
        _world_model_summary_viewport(world_model_summary),
        _world_model_summary_viewport(payload_summary),
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
        merged.setdefault("selectedAimpointSource", "original_safeAimPoint")
        merged.setdefault("hoverConfirmedTopExpected", False)
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
        proposal.target_explanation["recoveryAction"] = recovery_target.get("recoveryAction")
        proposal.target_explanation["cameraTriggeredBy"] = recovery_target.get("cameraTriggeredBy")
        if isinstance(target.get("resourceViewScore"), dict):
            proposal.target_explanation["resourceViewScore"] = dict(target["resourceViewScore"])
            proposal.target_explanation["resourceViewClassification"] = target["resourceViewScore"].get("classification")
    return proposal


def _world_tile(target: Any) -> dict[str, Any] | None:
    target = _dict(target)
    world = _dict(target.get("worldLocation") or target.get("world"))
    if world.get("worldX") is not None and world.get("worldY") is not None:
        return {"worldX": world.get("worldX"), "worldY": world.get("worldY"), "plane": world.get("plane", target.get("plane", 0))}
    return _tile_from(target)


def _tile_distance(a: dict[str, Any] | None, b: dict[str, Any] | None) -> int | None:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    ax = _number(a.get("worldX", a.get("x")))
    ay = _number(a.get("worldY", a.get("y")))
    bx = _number(b.get("worldX", b.get("x")))
    by = _number(b.get("worldY", b.get("y")))
    if ax is None or ay is None or bx is None or by is None:
        return None
    return int(max(abs(ax - bx), abs(ay - by)))


def _is_resource_return_navigation_target(target: Any) -> bool:
    target = _dict(target)
    if not target:
        return False
    class_id = _lower(target.get("classId") or target.get("targetClass"))
    target_type = _lower(target.get("targetType") or target.get("type"))
    name = _lower(target.get("targetName") or target.get("name"))
    return (
        class_id in {"resource_return", "service_route_anchor", "path_tile"}
        or target_type in {"tile", "path_tile", "resource_return"}
        or "resource return" in name
        or "return waypoint" in name
    )


def _return_resource_target_reacquired(
    *,
    status: dict[str, Any],
    resource_return: dict[str, Any],
    active_target: dict[str, Any],
    overlay_selected: dict[str, Any],
) -> bool:
    visible = (
        _bool(resource_return.get("resourceTargetCurrentlyVisible")) is True
        or _bool(status.get("postBankResourceTargetAvailable")) is True
    )
    if not visible:
        return False
    if _bool(resource_return.get("returnDestinationAvailable")) is not True:
        return True
    if str(resource_return.get("reason") or "") == "resource_target_visible":
        return True
    destination = _world_tile(resource_return.get("returnDestinationTile"))
    for target in (
        _dict(status.get("returnBestResourceTarget")),
        active_target,
        overlay_selected,
    ):
        target_tile = _world_tile(target)
        distance = _tile_distance(target_tile, destination)
        if distance is not None and distance <= RESOURCE_RETURN_REACQUIRE_RADIUS_TILES:
            return True
    return False


def _pathing_for_resource_return(pathing: dict[str, Any]) -> dict[str, Any]:
    clean = dict(pathing)
    for key in ("nextWaypointTarget", "destination"):
        if key in clean and not _is_resource_return_navigation_target(clean.get(key)):
            clean.pop(key, None)
    return clean


def _resource_return_fallback_target(active_target: dict[str, Any], resource_return: dict[str, Any]) -> dict[str, Any]:
    if _is_resource_return_navigation_target(active_target):
        return active_target
    destination_target = _dict(resource_return.get("destinationTarget"))
    if destination_target:
        return destination_target
    return {"returnDestinationTile": resource_return.get("returnDestinationTile")}


def _player_world_tile(status: dict[str, Any], brain: dict[str, Any]) -> dict[str, Any] | None:
    for value in (
        status.get("playerLocation"),
        status.get("playerContext"),
        brain.get("playerLocation"),
        brain.get("playerContext"),
        _dict(status.get("baseline")).get("player"),
        _dict(brain.get("baseline")).get("player"),
    ):
        tile = _world_tile(value)
        if tile:
            return tile
        value = _dict(value)
        nested = _world_tile(value.get("worldTile") or value.get("tile"))
        if nested:
            return nested
    return None


def _resource_worksite_context(status: dict[str, Any], brain: dict[str, Any]) -> dict[str, Any]:
    for context in (
        _dict(status.get("resourceViewContext")),
        _dict(brain.get("resourceViewContext")),
        _dict(status.get("resourceReturnContext")),
        _dict(brain.get("resourceReturnContext")),
        _dict(status.get("returnToResourceContext")),
        _dict(brain.get("returnToResourceContext")),
        _dict(status.get("postBankReacquisitionContext")),
        _dict(brain.get("postBankReacquisitionContext")),
        _dict(status.get("returnRouteContext")),
        _dict(brain.get("returnRouteContext")),
    ):
        anchor = _dict(context.get("worksiteAnchor") or context.get("resourceAnchor"))
        tile = (
            _world_tile(anchor)
            or _world_tile(context.get("worksiteTile"))
            or _world_tile(context.get("resourceAreaTile"))
            or _world_tile(context.get("returnDestinationTile"))
            or _world_tile(context.get("lastResourceTile"))
        )
        if tile:
            radius = _int(
                _first_present(
                    context.get("worksiteRadiusTiles"),
                    context.get("resourceRadiusTiles"),
                    anchor.get("radiusTiles"),
                    anchor.get("radius"),
                    RESOURCE_MEMORY_WORKSITE_RADIUS_TILES,
                ),
                RESOURCE_MEMORY_WORKSITE_RADIUS_TILES,
            )
            return {
                "worksiteId": context.get("worksiteId") or context.get("returnNodeId") or anchor.get("anchorId") or anchor.get("id"),
                "anchor": tile,
                "radiusTiles": max(3, radius),
                "source": context.get("returnDestinationSource") or context.get("source") or anchor.get("type"),
            }
    for memory in (
        _dict(status.get("resourceAreaMemory")),
        _dict(brain.get("resourceAreaMemory")),
    ):
        if not memory:
            continue
        if _bool(memory.get("resourceMemoryValid")) is False:
            continue
        tile = (
            _world_tile(memory.get("lastResourceClusterCenter"))
            or _world_tile(memory.get("lastResourceTargetTile"))
            or _world_tile(memory.get("lastResourcePlayerTile"))
        )
        if tile:
            return {
                "worksiteId": memory.get("lastResourceProfile") or "resource_area_memory",
                "anchor": tile,
                "radiusTiles": RESOURCE_MEMORY_WORKSITE_RADIUS_TILES,
                "source": "resource_area_memory",
            }
    policy_name = _lower(
        _first_present(
            status.get("brainTaskPolicy"),
            brain.get("taskPolicy"),
            brain.get("task_policy"),
            brain.get("preset"),
            status.get("preset"),
        )
    )
    if policy_name in {"woodcutting_bank", "woodcut_bank"}:
        return {
            "worksiteId": "lumbridge_west_tree_area",
            "anchor": {"worldX": 3196, "worldY": 3248, "plane": 0},
            "radiusTiles": RESOURCE_MEMORY_WORKSITE_RADIUS_TILES,
            "source": "profile_anchor",
        }
    return {}


def _resource_level_status(candidate: dict[str, Any], woodcutting_level: int | None) -> dict[str, Any]:
    required = woodcutting_required_level(candidate)
    level_known = woodcutting_level is not None
    met = required is not None and (woodcutting_level is None and required <= 1 or woodcutting_level is not None and required <= woodcutting_level)
    return {
        "requiredSkill": "woodcutting" if required is not None else None,
        "requiredLevel": required,
        "playerLevelKnown": level_known,
        "playerLevel": woodcutting_level,
        "levelRequirementMet": bool(met),
        "targetTemporarilyLockedReason": None if met else ("insufficient_level" if required and required > 1 else None),
        "visibleButNotExecutable": bool(not met and required is not None),
        "futureEligibleWhenLevelMet": bool(not met and required is not None),
    }


def _resource_candidate_view_metrics(
    candidate: dict[str, Any],
    *,
    status: dict[str, Any],
    brain: dict[str, Any],
    source_canvas_size: dict[str, Any] | None,
    worksite: dict[str, Any],
    woodcutting_level: int | None,
) -> dict[str, Any]:
    safe = candidate.get("safeAimPoint") if isinstance(candidate.get("safeAimPoint"), dict) else None
    if safe is None and _target_has_aimpoint_geometry(candidate):
        safe = safe_aimpoint_core.safe_aimpoint_for_target(
            candidate,
            source_canvas_size=source_canvas_size,
            viewport=_camera_viewport_from_status(status, brain),
        )
    projection = _resource_projection_status(
        candidate,
        safe_aimpoint=safe,
        source_canvas_size=source_canvas_size,
        status=status,
        brain=brain,
    )
    level = _resource_level_status(candidate, woodcutting_level)
    edge_distance = _number((safe or {}).get("distanceToViewportEdgePx"))
    ratio = _number((safe or {}).get("clippedVisibleAreaRatio"))
    tile = _world_tile(candidate)
    distance_from_worksite = _tile_distance(tile, _dict(worksite.get("anchor"))) if worksite else None
    inside_worksite = distance_from_worksite is None or distance_from_worksite <= _int(worksite.get("radiusTiles"), 12)
    safe_ok = isinstance(safe, dict) and safe.get("status") == "PASS"
    central = bool(safe_ok and (edge_distance is None or edge_distance >= 48) and (ratio is None or ratio >= 0.7))
    edge_clipped = bool(
        projection.get("classification") == "edge_clipped"
        or projection.get("edgeClipped") is True
        or (edge_distance is not None and edge_distance < 18)
        or (ratio is not None and ratio < 0.45)
    )
    if central:
        edge_clipped = False
    offscreen = bool(projection.get("offscreen") is True or projection.get("classification") == "offscreen")
    occluded = bool(
        projection.get("classification") in {"no_safe_aimpoint", "no_visible_interactable_geometry", "raw_aimpoint_outside_interactable_region"}
        and projection.get("projectionAvailable") is True
    )
    action_status = _resource_live_action_status(candidate)
    executable = bool(level.get("levelRequirementMet") and safe_ok and not edge_clipped and not offscreen and not action_status.get("blockedByLiveAction"))
    ambiguity = _dict(candidate.get("resourceTargetAmbiguity"))
    ambiguity_status = str(ambiguity.get("ambiguityStatus") or "clear")
    ambiguous = bool(ambiguity_status and ambiguity_status not in {"clear", "unknown"})
    overlap_penalty = bool(
        ambiguity.get("overlapPenaltyFromNonExecutableTarget") is True
        or ambiguity_status
        in {
            "ambiguous_top_hover_mismatch",
            "ambiguous_overlap_with_higher_level_target",
            "ambiguous_expected_entry_not_top",
            "ambiguous_object_stack",
            "unsafe",
        }
    )
    if ambiguous:
        executable = False
    return {
        "candidate": candidate,
        "safeAimPoint": safe,
        "projectionStatus": projection,
        "levelStatus": level,
        "resourceLiveActionStatus": action_status,
        "resourceTargetAmbiguity": ambiguity or None,
        "ambiguousResourceTarget": ambiguous,
        "overlapPenaltyFromNonExecutableTarget": overlap_penalty,
        "worldLocation": tile,
        "distanceFromWorksite": distance_from_worksite,
        "insideWorksite": bool(inside_worksite),
        "safeAimpoint": bool(safe_ok),
        "centralSafeAimpoint": central,
        "edgeClipped": edge_clipped,
        "partiallyOffscreen": bool(ratio is not None and ratio < 1.0),
        "occluded": occluded,
        "executable": executable,
        "edgeDistancePx": int(edge_distance) if edge_distance is not None else None,
        "visibleAreaRatio": float(ratio) if ratio is not None else None,
    }


def _preferred_resource_candidate_for_view(
    candidates: list[dict[str, Any]],
    *,
    status: dict[str, Any],
    brain: dict[str, Any],
    source_canvas_size: dict[str, Any] | None,
    woodcutting_level: int | None,
    suppressed_keys: set[str] | None = None,
) -> dict[str, Any] | None:
    suppressed_keys = suppressed_keys or set()
    worksite = _resource_worksite_context(status, brain)
    ranked: list[tuple[tuple[Any, ...], dict[str, Any], dict[str, Any]]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or not candidate:
            continue
        candidate_keys = _target_suppression_keys(candidate)
        if candidate_keys and candidate_keys.intersection(suppressed_keys):
            continue
        level = _resource_level_status(candidate, woodcutting_level)
        if not level.get("levelRequirementMet"):
            continue
        metrics = _resource_candidate_view_metrics(
            candidate,
            status=status,
            brain=brain,
            source_canvas_size=source_canvas_size,
            worksite=worksite,
            woodcutting_level=woodcutting_level,
        )
        if _dict(metrics.get("resourceLiveActionStatus")).get("blockedByLiveAction") is True:
            continue
        distance = _number(candidate.get("distanceTiles", candidate.get("targetDistanceChebyshev")), 1_000_000.0)
        quality = _number(candidate.get("qualityScore", candidate.get("score")), 0.0)
        key = (
            0 if metrics.get("executable") else 1,
            1 if metrics.get("ambiguousResourceTarget") else 0,
            1 if metrics.get("overlapPenaltyFromNonExecutableTarget") else 0,
            0 if metrics.get("safeAimpoint") else 1,
            0 if metrics.get("centralSafeAimpoint") else 1,
            0 if metrics.get("insideWorksite") else 1,
            1 if metrics.get("edgeClipped") else 0,
            1 if metrics.get("occluded") else 0,
            metrics.get("distanceFromWorksite") if metrics.get("distanceFromWorksite") is not None else 999,
            distance if distance is not None else 999,
            -float(quality or 0.0),
            (_target_name(candidate) or "").lower(),
        )
        ranked.append((key, candidate, metrics))
    if not ranked:
        return None
    _key, selected, metrics = min(ranked, key=lambda item: item[0])
    selected = dict(selected)
    selected["resourceViewCandidateScore"] = {
        "insideWorksite": metrics.get("insideWorksite"),
        "distanceFromWorksite": metrics.get("distanceFromWorksite"),
        "edgeDistancePx": metrics.get("edgeDistancePx"),
        "visibleAreaRatio": metrics.get("visibleAreaRatio"),
        "centralSafeAimpoint": metrics.get("centralSafeAimpoint"),
        "edgeClipped": metrics.get("edgeClipped"),
    }
    selected["resourceLiveActionStatus"] = metrics.get("resourceLiveActionStatus")
    return selected


def _resource_view_goal_for_status(status: dict[str, Any], brain: dict[str, Any]) -> str:
    phase = str(_dict(brain.get("genericTaskState")).get("phase") or status.get("phase") or "")
    intent = str(_dict(brain.get("genericTaskState")).get("activeIntent") or status.get("activeIntent") or "")
    if "deplet" in phase or "deplet" in intent:
        return "post_resource_depletion_view"
    if "reacquir" in phase or "reacquir" in intent:
        return "resource_reacquisition_view"
    if "return" in phase or "return" in intent:
        return "worksite_overview_view"
    return "resource_candidate_selection_view"


def _resource_view_score(
    *,
    status: dict[str, Any],
    brain: dict[str, Any],
    candidates: list[dict[str, Any]],
    selected_target: dict[str, Any],
    source_canvas_size: dict[str, Any] | None,
) -> dict[str, Any]:
    woodcutting_level = woodcutting_level_from_context(status, brain)
    worksite = _resource_worksite_context(status, brain)
    player = _player_world_tile(status, brain)
    metrics = [
        _resource_candidate_view_metrics(
            candidate,
            status=status,
            brain=brain,
            source_canvas_size=source_canvas_size,
            worksite=worksite,
            woodcutting_level=woodcutting_level,
        )
        for candidate in candidates
        if _is_resource_target_candidate(candidate)
    ]
    selected_tile = _world_tile(selected_target)
    selected_metrics = None
    for item in metrics:
        if _tile_distance(item.get("worldLocation"), selected_tile) == 0 and _target_name(item.get("candidate")) == _target_name(selected_target):
            selected_metrics = item
            break
    if selected_metrics is None and selected_target:
        selected_metrics = _resource_candidate_view_metrics(
            selected_target,
            status=status,
            brain=brain,
            source_canvas_size=source_canvas_size,
            worksite=worksite,
            woodcutting_level=woodcutting_level,
        )

    visible = [item for item in metrics if item["projectionStatus"].get("projectionAvailable") and not item["projectionStatus"].get("projectionSentinel")]
    executable = [item for item in metrics if item.get("executable")]
    safe_count = sum(1 for item in metrics if item.get("safeAimpoint"))
    central_count = sum(1 for item in metrics if item.get("centralSafeAimpoint"))
    edge_count = sum(1 for item in metrics if item.get("edgeClipped"))
    partial_count = sum(1 for item in metrics if item.get("partiallyOffscreen"))
    occluded_count = sum(1 for item in metrics if item.get("occluded"))
    low_level_rejected = sum(1 for item in metrics if item["levelStatus"].get("targetTemporarilyLockedReason") == "insufficient_level")
    world_tiles = [item.get("worldLocation") for item in visible if isinstance(item.get("worldLocation"), dict)]
    if len(world_tiles) >= 2:
        xs = [_number(tile.get("worldX"), 0.0) or 0.0 for tile in world_tiles]
        ys = [_number(tile.get("worldY"), 0.0) or 0.0 for tile in world_tiles]
        spread = int(max(max(xs) - min(xs), max(ys) - min(ys)))
    else:
        spread = 0
    selected_distance = _tile_distance(selected_tile, _dict(worksite.get("anchor"))) if worksite else None
    selected_pulls = bool(selected_distance is not None and selected_distance > _int(worksite.get("radiusTiles"), 12))
    score = 45
    score += min(24, len(executable) * 8)
    score += min(18, central_count * 6)
    if selected_metrics and selected_metrics.get("centralSafeAimpoint"):
        score += 12
    if worksite and selected_metrics and selected_metrics.get("insideWorksite"):
        score += 10
    if edge_count:
        score -= min(24, edge_count * 8)
    if selected_metrics and selected_metrics.get("edgeClipped"):
        score -= 20
    if occluded_count:
        score -= min(18, occluded_count * 9)
    if selected_pulls:
        score -= 18
    if len(executable) == 0:
        score -= 35
    elif len(executable) == 1 and not (selected_metrics and selected_metrics.get("centralSafeAimpoint")):
        score -= 10
    if low_level_rejected and not executable:
        score -= 15
    score = max(0, min(100, int(round(score))))
    if selected_pulls:
        classification = "needs_worksite_recenter"
    elif selected_metrics and selected_metrics.get("edgeClipped"):
        classification = "poor_edge_resource_view"
    elif selected_metrics and selected_metrics.get("occluded"):
        classification = "poor_occluded_resource_view"
    elif not executable:
        classification = "no_executable_resource_view"
    elif len(executable) <= 1 and score < 65:
        classification = "poor_single_candidate_view"
    elif score >= 78:
        classification = "good_resource_view"
    elif score >= 60:
        classification = "usable_resource_view"
    else:
        classification = "needs_resource_camera_reacquire"
    recovery_recommended = classification in {
        "poor_edge_resource_view",
        "poor_occluded_resource_view",
        "poor_single_candidate_view",
        "needs_resource_camera_reacquire",
        "needs_worksite_recenter",
        "no_executable_resource_view",
    }
    return {
        "schema": "resource_view_score.v1",
        "viewGoal": _resource_view_goal_for_status(status, brain),
        "worksiteId": worksite.get("worksiteId"),
        "worksiteAnchor": worksite.get("anchor"),
        "worksiteRadiusTiles": worksite.get("radiusTiles"),
        "playerLocation": player,
        "cameraYaw": status.get("cameraYaw") or brain.get("cameraYaw"),
        "cameraPitch": status.get("cameraPitch") or brain.get("cameraPitch"),
        "visibleResourceCandidates": len(visible),
        "executableResourceCandidates": len(executable),
        "safeAimpointCount": safe_count,
        "centralSafeAimpointCount": central_count,
        "edgeClippedResourceCandidates": edge_count,
        "ambiguousResourceCandidates": sum(1 for item in metrics if item.get("ambiguousResourceTarget")),
        "overlapPenalizedResourceCandidates": sum(1 for item in metrics if item.get("overlapPenaltyFromNonExecutableTarget")),
        "partiallyOffscreenResourceCandidates": partial_count,
        "occludedResourceCandidates": occluded_count,
        "candidateSpread": spread,
        "selectedTargetName": _target_name(selected_target),
        "selectedTargetWorldLocation": selected_tile,
        "selectedTargetDistanceFromWorksite": selected_distance,
        "selectedTargetEdgeDistancePx": selected_metrics.get("edgeDistancePx") if selected_metrics else None,
        "selectedTargetVisibleAreaRatio": selected_metrics.get("visibleAreaRatio") if selected_metrics else None,
        "selectedTargetHoverReady": bool(selected_metrics and selected_metrics.get("safeAimpoint")),
        "selectedTargetPullsAwayFromWorksite": selected_pulls,
        "lowLevelResourceCandidatesRejected": low_level_rejected,
        "score": score,
        "classification": classification,
        "cameraRecoveryRecommended": recovery_recommended,
    }


def _resource_view_recovery_trigger(score: dict[str, Any]) -> str:
    classification = str(score.get("classification") or "")
    view_goal = str(score.get("viewGoal") or "resource_candidate_selection_view")
    mapping = {
        "poor_edge_resource_view": "resource_target_edge_rejected",
        "poor_occluded_resource_view": "resource_candidate_occluded",
        "poor_single_candidate_view": "poor_single_candidate_view",
        "needs_worksite_recenter": "worksite_drift_detected",
        "no_executable_resource_view": "no_executable_resource_view",
    }
    return mapping.get(classification, view_goal)


def _resource_view_recovery_proposal(
    *,
    target: dict[str, Any],
    projection_status: dict[str, Any],
    resource_view_score: dict[str, Any],
    input_geometry: dict[str, Any] | None,
    source_canvas_size: dict[str, Any] | None,
    source_tick: int | None,
    status: dict[str, Any],
    brain: dict[str, Any],
) -> ActionProposal:
    recovery_target = _resource_recovery_target(target, projection_status)
    trigger = _resource_view_recovery_trigger(resource_view_score)
    recovery_target["resourceViewScore"] = dict(resource_view_score)
    recovery_target["cameraTriggeredBy"] = trigger
    recovery_target["recoveryAction"] = "camera_reacquire_resource_view"
    proposal = _proposal(
        "resource_view_recovery",
        target_kind="resource_recovery",
        target=recovery_target,
        key_action={
            "type": "camera_reacquire",
            "method": "keyboard_arrows",
            "command": "yaw_right_pitch_up",
            "cameraTriggeredBy": trigger,
            "durationMs": 220,
        },
        reason="resource_view_recovery_needed",
        confidence=0.64,
        required_context=["target", "inventory", "camera.controller"],
        warnings=[f"resource view is not good enough for a safe chop: {resource_view_score.get('classification')}"],
        source_tick=source_tick,
        input_geometry=input_geometry,
        source_canvas_size=source_canvas_size,
        status=status,
        brain=brain,
        suppress_click_point=True,
    )
    if isinstance(proposal.target_explanation, dict):
        proposal.target_explanation["resourceProjectionStatus"] = dict(projection_status)
        proposal.target_explanation["resourceViewScore"] = dict(resource_view_score)
        proposal.target_explanation["resourceViewClassification"] = resource_view_score.get("classification")
        proposal.target_explanation["resourceCameraTriggeredBy"] = trigger
        proposal.target_explanation["recoverySuggested"] = True
        proposal.target_explanation["recoveryAction"] = recovery_target.get("recoveryAction")
        proposal.target_explanation["cameraTriggeredBy"] = trigger
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
    action_target_source = _action_target_source(target, target_kind=target_kind)
    actionability = _actionability(target, target_kind=target_kind, click=click, key_action=key_action)
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
        action_target_source=action_target_source,
        actionability=actionability,
    )
    if isinstance(proposal.target_explanation, dict):
        proposal.target_explanation["actionTargetSource"] = action_target_source
        proposal.target_explanation["actionability"] = actionability
        if target.get("advisoryTargetSource") or target.get("advisory_target_source"):
            proposal.target_explanation["advisoryTargetSource"] = target.get("advisoryTargetSource") or target.get("advisory_target_source")
        if target.get("resourceSelectionReason"):
            proposal.target_explanation["resourceSelectionReason"] = target.get("resourceSelectionReason")
            proposal.target_explanation["reacquiredFromResourceTarget"] = target.get("reacquiredFromResourceTarget")
        if isinstance(target.get("resourceLiveActionStatus"), dict):
            proposal.target_explanation["resourceLiveActionStatus"] = dict(target["resourceLiveActionStatus"])
        if target.get("resourceSelectionRejectionReason"):
            proposal.target_explanation["resourceSelectionRejectionReason"] = target.get("resourceSelectionRejectionReason")
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


def _route_census_recovery_target(route_context: dict[str, Any]) -> dict[str, Any]:
    census = _dict(route_context.get("routeObjectCensus"))
    top_objects = _list(census.get("topRouteObjects"))
    current_step = _current_route_step(route_context)
    for item in top_objects:
        if not isinstance(item, dict):
            continue
        if item.get("routeObjectKind") != "route_transition":
            continue
        if item.get("routeRelevanceStatus") != "PASS":
            continue
        projection = _dict(item.get("projectionStatus"))
        if projection.get("actionableByCanvas") is True:
            continue
        rejection = _lower(item.get("rejectionReason") or projection.get("rejectionReason"))
        if rejection in {"wrongobjectkind", "randomtransitionobject", "backwardrouteobject", "wrongplane"}:
            continue
        candidate = _dict(item.get("candidate"))
        if not candidate:
            continue
        merged = dict(candidate)
        if route_context.get("routeId") is not None:
            merged.setdefault("routeId", route_context.get("routeId"))
        route_step_index = _first_present(route_context.get("currentStepIndex"), item.get("matchedRouteStepIndex"))
        if route_step_index is not None:
            merged.setdefault("routeStepIndex", route_step_index)
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
        if isinstance(merged.get("expectedOptions"), list):
            merged["actions"] = list(
                dict.fromkeys(list(_list(merged.get("actions"))) + list(merged["expectedOptions"]) + list(_list(merged.get("dialogueOpenerOptions"))))
            )
        merged.setdefault("targetName", item.get("name") or candidate.get("targetName") or candidate.get("name"))
        merged.setdefault("classId", candidate.get("classId") or candidate.get("targetClass") or "service_route_transition")
        merged["routeRelevance"] = _dict(item.get("routeRelevance")) or _dict(candidate.get("routeRelevance"))
        merged["projectionStatus"] = projection
        merged["routeObjectSource"] = item.get("source") or candidate.get("source")
        merged["routeObjectRecoveryCandidate"] = True
        merged["routeObjectRecoveryReason"] = rejection or "route_transition_not_actionable"
        return merged
    return {}


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


def _service_target_kind(target: dict[str, Any]) -> str:
    class_id = _lower(target.get("classId") or target.get("targetClass") or target.get("serviceObjectType"))
    name = _lower(target.get("targetName") or target.get("name"))
    text = f"{class_id} {name}"
    if "deposit" in text:
        return "deposit_box"
    if "booth" in text:
        return "bank_booth"
    if "banker" in text:
        return "banker"
    return "unknown"


def _projection_from_target(target: dict[str, Any]) -> dict[str, Any]:
    return _dict(target.get("projectionStatus") or target.get("projection") or target.get("resourceProjectionStatus"))


def _canvas_point_from_projection_or_target(target: dict[str, Any], safe_aimpoint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    projection = _projection_from_target(target)
    for value in (
        _dict(projection.get("aimPoint")),
        _dict(projection.get("canvasLocation")),
        _dict(target.get("aimPoint")),
        _dict(target.get("aimPointContext")),
        _dict(target.get("canvasAimPoint")),
        _dict(target.get("canvasLocation")),
        _dict(safe_aimpoint or {}).get("rawAimPoint"),
    ):
        point = _point_from_aim(value)
        if point:
            return point
    return None


def _service_projection_classification(target: dict[str, Any], safe_aimpoint: dict[str, Any] | None) -> str:
    projection = _projection_from_target(target)
    for key in ("classification", "status", "reason"):
        value = projection.get(key)
        if isinstance(value, str) and value:
            return value
    safe = safe_aimpoint if isinstance(safe_aimpoint, dict) else {}
    if safe.get("status") == "PASS":
        return "actionable"
    if safe.get("rejectionReason"):
        return str(safe["rejectionReason"])
    if target.get("onScreen") is False:
        return "offscreen"
    return "unknown"


def _bounds_area(value: Any) -> float | None:
    bounds = _dict(value)
    if isinstance(bounds.get("bounds"), dict):
        return _bounds_area(bounds.get("bounds"))
    width = _number(_first_present(bounds.get("width"), bounds.get("w")))
    height = _number(_first_present(bounds.get("height"), bounds.get("h")))
    if width is None and bounds.get("right") is not None:
        left = _number(_first_present(bounds.get("left"), bounds.get("x"), bounds.get("minX")))
        right = _number(bounds.get("right"))
        if left is not None and right is not None:
            width = right - left
    if height is None and bounds.get("bottom") is not None:
        top = _number(_first_present(bounds.get("top"), bounds.get("y"), bounds.get("minY")))
        bottom = _number(bounds.get("bottom"))
        if top is not None and bottom is not None:
            height = bottom - top
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    return float(width) * float(height)


def _service_target_exposure_metrics(
    target: dict[str, Any],
    safe_aimpoint: dict[str, Any] | None,
    *,
    canvas_point: dict[str, Any] | None,
    projection: dict[str, Any],
    viewport: dict[str, Any] | None,
    source_canvas_size: dict[str, Any] | None,
) -> dict[str, Any]:
    safe = safe_aimpoint if isinstance(safe_aimpoint, dict) else {}
    rect = camera_control.viewport_rect(viewport, canvas_size=source_canvas_size)
    point = _point_from_aim(safe) or _point_from_aim(canvas_point)
    edge_distance = _number(
        _first_present(
            safe.get("distanceToViewportEdgePx"),
            safe.get("distanceToCanvasEdgePx"),
            projection.get("edgeDistancePx"),
            projection.get("distanceToViewportEdgePx"),
        )
    )
    if point and edge_distance is None:
        x = float(point["x"])
        y = float(point["y"])
        edge_distance = min(x - rect["left"], rect["right"] - x, y - rect["top"], rect["bottom"] - y)
    visible_area_px = _number(
        _first_present(
            safe.get("clippedVisibleAreaPx"),
            projection.get("clippedVisibleAreaPx"),
            projection.get("visibleAreaPx"),
        )
    )
    if visible_area_px is None:
        visible_area_px = _bounds_area(safe.get("bounds")) or _bounds_area(projection.get("bounds")) or _bounds_area(target.get("bounds"))
    visible_ratio = _number(
        _first_present(
            safe.get("clippedVisibleAreaRatio"),
            projection.get("clippedVisibleAreaRatio"),
            projection.get("visibleAreaRatio"),
        )
    )
    centrality_score = None
    comfortable_region_met = False
    if point:
        x = float(point["x"])
        y = float(point["y"])
        half_w = max(1.0, rect["width"] / 2.0)
        half_h = max(1.0, rect["height"] / 2.0)
        normalized_distance = max(abs(x - rect["centerX"]) / half_w, abs(y - rect["centerY"]) / half_h)
        centrality_score = round(_clamp_float(1.0 - normalized_distance, 0.0, 1.0), 3)
        margin_x = rect["width"] * (1.0 - SERVICE_COMFORTABLE_REGION_FRACTION) / 2.0
        margin_y = rect["height"] * (1.0 - SERVICE_COMFORTABLE_REGION_FRACTION) / 2.0
        comfortable_region_met = bool(
            rect["left"] + margin_x <= x <= rect["right"] - margin_x
            and rect["top"] + margin_y <= y <= rect["bottom"] - margin_y
        )
    safe_click_available = bool(safe.get("status") == "PASS" and safe.get("canvasX") is not None and safe.get("canvasY") is not None)
    visible_area_threshold_met = bool(
        visible_area_px is None or visible_area_px >= SERVICE_MIN_VISIBLE_AREA_PX
    ) and bool(visible_ratio is None or visible_ratio >= SERVICE_MIN_VISIBLE_AREA_RATIO)
    edge_threshold_met = bool(edge_distance is not None and edge_distance >= SERVICE_MIN_EDGE_DISTANCE_PX)
    comfortable_edge_met = bool(edge_distance is not None and edge_distance >= SERVICE_COMFORTABLE_EDGE_DISTANCE_PX)
    raw_center_inside = _bool(safe.get("rawCenterInsideViewport"))
    if raw_center_inside is False and edge_distance is not None and edge_distance < SERVICE_COMFORTABLE_EDGE_DISTANCE_PX:
        comfortable_region_met = False
    edge_sliver = bool(
        safe_click_available
        and (
            (edge_distance is not None and edge_distance < SERVICE_MIN_EDGE_DISTANCE_PX)
            or (visible_area_px is not None and visible_area_px < SERVICE_MIN_VISIBLE_AREA_PX)
            or (visible_ratio is not None and visible_ratio < SERVICE_MIN_VISIBLE_AREA_RATIO)
        )
    )
    usable = bool(
        safe_click_available
        and visible_area_threshold_met
        and edge_threshold_met
        and comfortable_region_met
        and safe.get("uiBlocked") is not True
    )
    score = 0.0
    if safe_click_available:
        score += 30.0
    if visible_area_threshold_met:
        score += 20.0
    if edge_threshold_met:
        score += 20.0
    if comfortable_region_met:
        score += 20.0
    if safe.get("uiBlocked") is not True:
        score += 10.0
    if edge_distance is not None:
        score += min(10.0, max(0.0, edge_distance) / 8.0)
    if centrality_score is not None:
        score += centrality_score * 10.0
    score = round(_clamp_float(score, 0.0, 100.0), 3)
    if edge_sliver:
        exposure_result = "edge_sliver_only"
    elif safe_click_available and not visible_area_threshold_met:
        exposure_result = "insufficient_visible_area"
    elif usable:
        exposure_result = "comfortably_exposed"
    else:
        exposure_result = None
    return {
        "safeClickAvailable": safe_click_available,
        "visibleAreaPx": round(float(visible_area_px), 3) if visible_area_px is not None else None,
        "visibleAreaRatio": round(float(visible_ratio), 3) if visible_ratio is not None else None,
        "edgeDistancePx": round(float(edge_distance), 3) if edge_distance is not None else None,
        "centralityScore": centrality_score,
        "edgeSliverVisible": edge_sliver,
        "usableExposureScore": score,
        "usableExposureThresholdMet": usable,
        "comfortableViewRegionMet": comfortable_region_met,
        "visibleAreaThresholdMet": visible_area_threshold_met,
        "edgeDistanceThresholdMet": edge_threshold_met,
        "comfortableEdgeDistanceMet": comfortable_edge_met,
        "exposureResult": exposure_result,
    }


def _service_target_exposure(
    target: dict[str, Any],
    safe_aimpoint: dict[str, Any] | None,
    *,
    source_canvas_size: dict[str, Any] | None,
    status: dict[str, Any] | None,
    brain: dict[str, Any] | None,
) -> dict[str, Any]:
    projection = _projection_from_target(target)
    viewport = _camera_viewport_from_status(status, brain)
    canvas_point = _canvas_point_from_projection_or_target(target, safe_aimpoint)
    exposure_error = camera_control.exposure_error_from_canvas_point(
        canvas_point,
        viewport=viewport,
        canvas_size=source_canvas_size,
    )
    safe = safe_aimpoint if isinstance(safe_aimpoint, dict) else {}
    classification = _service_projection_classification(target, safe)
    exposure_metrics = _service_target_exposure_metrics(
        target,
        safe_aimpoint,
        canvas_point=canvas_point,
        projection=projection,
        viewport=viewport,
        source_canvas_size=source_canvas_size,
    )
    on_screen = (
        projection.get("actionableByCanvas") is True
        or projection.get("onScreen") is True
        or projection.get("visible") is True
        or target.get("onScreen") is True
        or safe.get("status") == "PASS"
    )
    offscreen = (
        classification == "offscreen"
        or exposure_error.get("status") == "offscreen"
        or target.get("onScreen") is False
        or projection.get("onScreen") is False
    )
    safe_click_available = bool(exposure_metrics.get("safeClickAvailable"))
    usable_exposure = bool(exposure_metrics.get("usableExposureThresholdMet"))
    edge_sliver = bool(exposure_metrics.get("edgeSliverVisible"))
    insufficient_visible_area = bool(
        safe_click_available
        and exposure_metrics.get("visibleAreaThresholdMet") is False
    )
    insufficient_edge_distance = bool(
        safe_click_available
        and exposure_metrics.get("edgeDistanceThresholdMet") is False
    )
    loaded = bool(target)
    route_relevance = _dict(target.get("routeRelevance"))
    route_relevant = route_relevance.get("relevanceStatus") in {None, "", "PASS"} or bool(target.get("routeId"))
    route_action_relevant = route_relevant and bool(
        target.get("routeId")
        or target.get("routeStepType")
        or str(target.get("classId") or "") == "service_route_transition"
        or _contains_any(_candidate_actions(target), ["climb", "open", "cross", "enter", "exit"])
    )
    action_relevant = bool(_contains_any(_candidate_actions(target), ["bank", "deposit", "use", "collect"]) or route_action_relevant)
    expected_action = None
    for action in _candidate_actions(target):
        lowered_action = _lower(action)
        if lowered_action in {"bank", "deposit", "deposit-box", "use", "collect"} or (
            route_action_relevant and lowered_action not in {"examine", "cancel"}
        ):
            expected_action = str(action)
            break
    target_view_state = target_view_core.build_target_view_state(
        target,
        target_kind="service_object",
        player_location=_player_world_tile(status or {}, brain or {}),
        expected_action=expected_action,
        target_source="live_world_model" if target.get("worldModelSource") or target.get("projectionStatus") else target.get("source"),
        target_route_relevant=route_relevant,
        target_action_relevant=action_relevant,
        safe_aimpoint=safe,
        viewport=viewport,
        source_canvas_size=source_canvas_size,
        status=status,
    )
    should_attempt = bool(
        loaded
        and action_relevant
        and route_relevant
        and not usable_exposure
        and (
            offscreen
            or not safe_click_available
            or edge_sliver
            or insufficient_visible_area
            or insufficient_edge_distance
            or exposure_metrics.get("comfortableViewRegionMet") is False
            or classification in {"raw_aimpoint_outside_interactable_region", "centerOffViewport", "centerOutsideInteractableRegion"}
        )
    )
    if usable_exposure:
        view_classification = "usable_service_view"
    elif offscreen:
        view_classification = "needs_service_camera_recovery"
    elif edge_sliver:
        view_classification = "service_object_edge_sliver"
    elif insufficient_visible_area or insufficient_edge_distance or safe_click_available:
        view_classification = "service_object_visible_but_not_usable"
    else:
        view_classification = "poor_service_projection"
    if offscreen:
        exposure_reason = "service_object_loaded_offscreen"
    elif edge_sliver:
        exposure_reason = "service_object_edge_sliver"
    elif insufficient_visible_area:
        exposure_reason = "service_object_insufficient_visible_area"
    elif insufficient_edge_distance:
        exposure_reason = "service_object_too_close_to_edge"
    elif not safe_click_available:
        exposure_reason = "service_screen_click_point_unavailable"
    elif exposure_metrics.get("comfortableViewRegionMet") is False:
        exposure_reason = "service_object_not_in_comfortable_view_region"
    else:
        exposure_reason = "not_needed"
    exposure_result = (
        exposure_metrics.get("exposureResult")
        or ("not_needed" if usable_exposure else "still_offscreen" if offscreen else "still_no_projection")
    )
    target_plan = _dict(target_view_state.get("cameraMotorPlan"))
    plan = {
        "schema": "camera_motor_plan.v1",
        "cameraInputMethod": target_plan.get("cameraInputMethod", "keyboard_arrows"),
        "cameraDirectionChosen": target_plan.get("cameraDirectionChosen", "yaw_right_pitch_up"),
        "cameraDirectionReason": target_plan.get("cameraDirectionReason", "target_view_recovery"),
        "cameraHoldMs": target_plan.get("cameraHoldMs", 220),
        "keyCombination": list(target_plan.get("keyCombination") or []),
        "dragPathSummary": target_plan.get("dragPathSummary"),
        "errorMagnitude": target_plan.get("errorMagnitude"),
        "viewTolerancePx": target_plan.get("viewTolerancePx", camera_control.DEFAULT_VIEW_TOLERANCE_PX),
        "targetBearing": target_plan.get("targetBearing"),
        "yawErrorBefore": target_plan.get("yawErrorBefore"),
        "pitchErrorHint": target_plan.get("pitchErrorHint"),
        "cameraResponseCalibration": target_plan.get("cameraResponseCalibration"),
        "controlLaw": target_plan.get("controlLaw"),
    }
    return {
        "schema": "service_target_exposure.v1",
        "serviceTargetName": _target_name(target),
        "serviceTargetId": _first_present(target.get("id"), target.get("rawId"), target.get("objectId")),
        "serviceTargetKind": _service_target_kind(target),
        "serviceTargetWorldLocation": _tile_from(target),
        "serviceTargetPlane": _first_present(target.get("plane"), _dict(_tile_from(target)).get("plane")),
        "serviceObjectLoaded": loaded,
        "serviceObjectRouteRelevant": route_relevant,
        "serviceObjectActionRelevant": action_relevant,
        "currentProjectionStatus": classification,
        "currentCanvasPoint": canvas_point,
        "currentSafeAimPoint": safe if safe else None,
        "currentScreenClickPoint": target.get("screenClickPoint") or target.get("screenAimPoint"),
        "currentlyOnScreen": bool(on_screen),
        "currentlyOffscreen": bool(offscreen),
        "edgeClipped": classification == "edge_clipped",
        "edgeSliverVisible": exposure_metrics.get("edgeSliverVisible"),
        "visibleAreaPx": exposure_metrics.get("visibleAreaPx"),
        "visibleAreaRatio": exposure_metrics.get("visibleAreaRatio"),
        "centralityScore": exposure_metrics.get("centralityScore"),
        "edgeDistancePx": exposure_metrics.get("edgeDistancePx"),
        "usableExposureScore": exposure_metrics.get("usableExposureScore"),
        "usableExposureThresholdMet": exposure_metrics.get("usableExposureThresholdMet"),
        "comfortableViewRegionMet": exposure_metrics.get("comfortableViewRegionMet"),
        "visibleAreaThresholdMet": exposure_metrics.get("visibleAreaThresholdMet"),
        "edgeDistanceThresholdMet": exposure_metrics.get("edgeDistanceThresholdMet"),
        "comfortableEdgeDistanceMet": exposure_metrics.get("comfortableEdgeDistanceMet"),
        "uiBlocked": safe.get("uiBlocked"),
        "cameraYaw": _dict(viewport).get("cameraYaw"),
        "cameraPitch": _dict(viewport).get("cameraPitch"),
        "playerWorldLocation": target_view_state.get("playerWorldLocation"),
        "targetBearing": target_view_state.get("targetBearing"),
        "targetBearingDegrees": target_view_state.get("targetBearingDegrees"),
        "yawErrorToTarget": target_view_state.get("yawErrorToTarget"),
        "targetViewState": target_view_state,
        "targetViewPolicy": target_view_state.get("targetViewPolicy"),
        "cameraResponseCalibration": target_view_state.get("cameraResponseCalibration"),
        "viewQualityClassification": view_classification,
        "shouldAttemptCameraExposure": should_attempt,
        "cameraExposureReason": exposure_reason,
        "exposureAttempts": 0,
        "exposureResult": exposure_result,
        "evidenceSources": [
            source
            for source, present in (
                ("live_world_model", bool(target.get("worldModelSource") or target.get("projectionStatus"))),
                ("action_proposal", True),
                ("projection_audit", bool(projection)),
                ("view_quality", False),
                ("screenshot", False),
                ("external_knowledge", False),
                ("replay_scenario", False),
            )
            if present
        ],
        "finalDecision": "service_view_recovery" if should_attempt else ("service_object_action" if usable_exposure else "block_or_reposition"),
        "exposureError": exposure_error,
        "cameraMotorPlan": plan,
    }


def _service_view_recovery_proposal(
    *,
    target: dict[str, Any],
    exposure: dict[str, Any],
    input_geometry: dict[str, Any] | None,
    source_canvas_size: dict[str, Any] | None,
    source_tick: int | None,
    status: dict[str, Any],
    brain: dict[str, Any],
    reason: str,
    confidence: float,
) -> ActionProposal:
    motor = _dict(exposure.get("cameraMotorPlan"))
    command = str(motor.get("cameraDirectionChosen") or "yaw_right_pitch_up")
    method = str(motor.get("cameraInputMethod") or "keyboard_arrows")
    duration_ms = _int(motor.get("cameraHoldMs"), 220)
    recovery_target = dict(target)
    recovery_target["serviceTargetExposure"] = dict(exposure)
    if isinstance(exposure.get("targetViewState"), dict):
        recovery_target["targetViewState"] = dict(exposure["targetViewState"])
    recovery_target["recoverySuggested"] = True
    recovery_target["recoveryAction"] = "camera_reacquire_service_target"
    recovery_target["cameraTriggeredBy"] = exposure.get("cameraExposureReason")
    proposal = _proposal(
        "service_view_recovery",
        target_kind="service_recovery",
        target=recovery_target,
        key_action={
            "type": "camera_reacquire",
            "method": method,
            "command": command,
            "cameraTriggeredBy": exposure.get("cameraExposureReason"),
            "durationMs": duration_ms,
        },
        reason=reason,
        confidence=confidence,
        required_context=["service_route", "camera.controller", "bank_ui"],
        warnings=[f"service object loaded but not safely clickable: {exposure.get('cameraExposureReason')}"],
        source_tick=source_tick,
        input_geometry=input_geometry,
        source_canvas_size=source_canvas_size,
        status=status,
        brain=brain,
        suppress_click_point=True,
    )
    if isinstance(proposal.target_explanation, dict):
        proposal.target_explanation["serviceTargetExposure"] = dict(exposure)
        if isinstance(exposure.get("targetViewState"), dict):
            proposal.target_explanation["targetViewState"] = dict(exposure["targetViewState"])
            proposal.target_explanation["targetViewPolicy"] = dict(_dict(exposure.get("targetViewPolicy")))
        proposal.target_explanation["recoverySuggested"] = True
        proposal.target_explanation["recoveryAction"] = "camera_reacquire_service_target"
        proposal.target_explanation["cameraTriggeredBy"] = exposure.get("cameraExposureReason")
    return proposal


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
    resource_items_held = _int(bank_operation.get("resourceItemsHeld"), None)
    resource_item_quantity = _int(bank_operation.get("resourceItemQuantity"), None)
    if (resource_items_held is not None and resource_items_held > 0) or (
        resource_item_quantity is not None and resource_item_quantity > 0
    ):
        return False
    if _bool(bank_operation.get("bankingComplete")) is True:
        return True
    if _bool(bank_operation.get("operationNeeded")) is False and _int(bank_operation.get("resourceItemsHeld"), -1) == 0:
        return True
    return False


def _held_resource_count(
    *,
    generic: dict[str, Any],
    inventory: dict[str, Any],
    bank_operation: dict[str, Any],
    status: dict[str, Any],
) -> int | None:
    progress = _dict(generic.get("goalProgress"))
    for value in (
        bank_operation.get("resourceItemsHeld"),
        bank_operation.get("resourceItemQuantity"),
        progress.get("heldResourceCount"),
        progress.get("currentHeldCount"),
        inventory.get("resourceCount"),
        status.get("resourceCount"),
        status.get("inventoryMatchingResourceCount"),
        status.get("bankResourceItemsHeld"),
    ):
        count = _int(value, None)
        if count is not None:
            return count
    return None


def _service_action_context_ready(service: dict[str, Any], status: dict[str, Any]) -> bool:
    if _bool(_first_present(service.get("serviceReady"), status.get("serviceReady"))) is True:
        return True
    route_context = _dict(service.get("serviceRouteContext") or status.get("serviceRouteContext"))
    route_action_ready = _bool(_first_present(route_context.get("actionReady"), status.get("serviceRouteActionReady"))) is True
    if not route_action_ready:
        return False
    if _dict(route_context.get("visibleServiceTarget") or route_context.get("selectedServiceObject") or route_context.get("visibleInteractionTarget")):
        return True
    return _bool(
        _first_present(
            route_context.get("serviceObjectInterceptReady"),
            status.get("serviceObjectInterceptReady"),
            status.get("serviceRouteObjectInterceptReady"),
        )
    ) is True


def _service_target_action_context_ready(service: dict[str, Any], status: dict[str, Any]) -> bool:
    if _bool(_first_present(service.get("serviceReady"), status.get("serviceReady"))) is True:
        return True
    route_context = _dict(service.get("serviceRouteContext") or status.get("serviceRouteContext"))
    route_action_ready = _bool(_first_present(route_context.get("actionReady"), status.get("serviceRouteActionReady"))) is True
    if not route_action_ready:
        return False
    if _dict(route_context.get("visibleServiceTarget") or route_context.get("selectedServiceObject")):
        return True
    return _bool(_first_present(route_context.get("serviceObjectInterceptReady"), status.get("serviceObjectInterceptReady"))) is True


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
    service_policy_needed = (
        _bool(service.get("serviceNeeded")) is True
        or _bool(service.get("serviceRequired")) is True
        or _bool(status.get("serviceNeeded")) is True
    )
    held_resource_count = _held_resource_count(
        generic=generic,
        inventory=inventory,
        bank_operation=_dict(bank_operation),
        status=status,
    )
    if (
        service_policy_needed
        and held_resource_count is not None
        and held_resource_count > 0
    ):
        if _service_target_action_context_ready(service, status):
            return True
        route_context = _dict(service.get("serviceRouteContext") or status.get("serviceRouteContext"))
        route_progress_active = bool(route_context.get("completedSteps")) or str(route_context.get("routeStepStatus") or "") in {
            "retained_route_interaction_anchor",
            "retained_service_anchor",
        }
        resource_collection_intent = phase in {
            "select_target",
            "target_selected",
            "continue_current_target",
            "continue_task",
        } or active_intent in {
            "select_target",
            "target_selected",
            "continue_current_target",
            "continue_task",
        }
        if (resource_collection_intent or route_progress_active) and _service_action_context_ready(service, status):
            return True
    if service_policy_needed and _bool(_dict(bank_operation).get("operationNeeded")) is True:
        return True
    # serviceNeeded/serviceRequired in serviceContext means the task policy has
    # a banking service available. It is not, by itself, an immediate lifecycle
    # demand to leave the resource area while inventory still has room.
    return False


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


def _pathing_has_concrete_waypoint(pathing: dict[str, Any]) -> bool:
    if _normalise_tile(pathing.get("nextWaypointTile")) is not None:
        return True
    if isinstance(pathing.get("nextWaypointTarget"), dict):
        return True
    if isinstance(pathing.get("nextWaypointAimPoint"), dict) or isinstance(pathing.get("pathClickPoint"), dict):
        return True
    return bool(_path_tiles(pathing))


def _selected_route_waypoint(
    pathing: dict[str, Any],
    *,
    class_id: Any = None,
    target_id: Any = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    next_tile = _normalise_tile(pathing.get("nextWaypointTile"))
    mode = str(pathing.get("routeWaypointDistanceMode") or "adaptive")
    near_transition = _bool(pathing.get("routeWaypointNearTransition")) is True or str(pathing.get("nextEdgeType") or "").startswith(("interact_", "reacquire_"))
    lookahead = max(1, _int(pathing.get("routeWaypointLookaheadTiles"), 12))
    max_horizon = max(1, _int(pathing.get("routeWaypointMaxHorizonTiles"), 25))
    min_progress = max(1, _int(pathing.get("minRouteProgressTiles"), 3))
    tiles = _path_tiles(pathing)
    suppressed_keys = {str(value) for value in _list(pathing.get("suppressedNavigationTargetKeys")) + _list(pathing.get("suppressedActionTargetKeys")) if value is not None}
    unsuppressed_tiles = [
        tile
        for tile in tiles
        if not _route_tile_is_suppressed(tile, suppressed_keys, class_id=class_id, target_id=target_id)
    ]
    next_tile_suppressed = _route_tile_is_suppressed(next_tile, suppressed_keys, class_id=class_id, target_id=target_id)
    if mode != "adaptive" or near_transition or not tiles:
        if next_tile_suppressed and unsuppressed_tiles:
            selected = unsuppressed_tiles[0]
            return selected, {
                "schema": "route_waypoint_selection.v1",
                "mode": mode,
                "reason": "suppressed_waypoint_alternate",
                "waypointDistanceTiles": 1,
                "consideredTiles": len(tiles),
                "candidateTilesAfterSuppression": len(unsuppressed_tiles),
                "lookaheadTiles": lookahead,
                "maxHorizonTiles": max_horizon,
                "selectedTile": dict(selected),
                "suppressedWaypointTile": dict(next_tile) if next_tile else None,
                "suppressedTargetKeys": sorted(suppressed_keys),
            }
        return next_tile, {
            "schema": "route_waypoint_selection.v1",
            "mode": mode,
            "reason": "all_waypoints_suppressed" if next_tile_suppressed else ("near_transition_precision" if near_transition else "next_waypoint"),
            "waypointDistanceTiles": 1 if next_tile else None,
            "consideredTiles": len(tiles),
            "candidateTilesAfterSuppression": len(unsuppressed_tiles) if suppressed_keys else None,
            "lookaheadTiles": lookahead,
            "maxHorizonTiles": max_horizon,
            "suppressedTargetKeys": sorted(suppressed_keys) if suppressed_keys else [],
        }
    if suppressed_keys:
        tiles = unsuppressed_tiles
        if not tiles:
            return next_tile, {
                "schema": "route_waypoint_selection.v1",
                "mode": "adaptive",
                "reason": "all_waypoints_suppressed",
                "waypointDistanceTiles": 1 if next_tile else None,
                "consideredTiles": len(_path_tiles(pathing)),
                "candidateTilesAfterSuppression": 0,
                "lookaheadTiles": lookahead,
                "maxHorizonTiles": max_horizon,
                "minRouteProgressTiles": min_progress,
                "suppressedTargetKeys": sorted(suppressed_keys),
            }
    direct_distance = _int(pathing.get("distanceToDestination"), None)
    if direct_distance is not None and direct_distance <= lookahead and len(tiles) > lookahead:
        destination_tile = _normalise_tile(pathing.get("destinationTile") or pathing.get("pathTargetTile"))
        next_distance = _tile_distance(next_tile, destination_tile)
        detour_threshold = max(lookahead, max(1, direct_distance) * 4)
        if (
            direct_distance <= 4
            and len(tiles) > detour_threshold
            and next_distance is not None
            and next_distance >= direct_distance
        ):
            return None, {
                "schema": "route_waypoint_selection.v1",
                "mode": "adaptive",
                "reason": "close_destination_detour_safety_block",
                "blocked": True,
                "blockedReason": "close destination path detours without immediate progress",
                "waypointDistanceTiles": None,
                "consideredTiles": len(tiles),
                "lookaheadTiles": lookahead,
                "maxHorizonTiles": max_horizon,
                "minRouteProgressTiles": min_progress,
                "directDistanceToDestination": direct_distance,
                "nextWaypointTile": dict(next_tile) if next_tile else None,
                "nextWaypointDistanceToDestination": next_distance,
                "destinationTile": dict(destination_tile) if destination_tile else None,
                "suppressedTargetKeys": sorted(suppressed_keys) if suppressed_keys else [],
            }
        return next_tile, {
            "schema": "route_waypoint_selection.v1",
            "mode": "adaptive",
            "reason": "close_destination_detour_precision",
            "waypointDistanceTiles": 1 if next_tile else None,
            "consideredTiles": len(tiles),
            "lookaheadTiles": lookahead,
            "maxHorizonTiles": max_horizon,
            "minRouteProgressTiles": min_progress,
            "directDistanceToDestination": direct_distance,
            "selectedTile": dict(next_tile) if next_tile else None,
            "suppressedTargetKeys": sorted(suppressed_keys) if suppressed_keys else [],
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
        "candidateTilesAfterSuppression": len(tiles) if suppressed_keys else None,
        "suppressedTargetKeys": sorted(suppressed_keys) if suppressed_keys else [],
    }


def _path_target(pathing: dict[str, Any], fallback: dict[str, Any], name: str) -> dict[str, Any]:
    target = _dict(pathing.get("nextWaypointTarget") or pathing.get("destination") or fallback)
    merged = dict(target)
    merged.setdefault("targetName", name)
    advisory_source = target.get("source") or fallback.get("source")
    if advisory_source:
        merged.setdefault("advisoryTargetSource", advisory_source)
    target_id = target.get("objectId", target.get("rawId", target.get("id")))
    class_id = target.get("classId") or target.get("targetClass")
    selected_waypoint, selection = _selected_route_waypoint(pathing, class_id=class_id, target_id=target_id)
    if selection:
        merged["routeWaypointSelection"] = selection
    if isinstance(selected_waypoint, dict):
        merged["targetTile"] = dict(selected_waypoint)
        merged["actionTargetSource"] = "local_frontier_waypoint"
        merged["actionability"] = "needs_live_projection"
        if selection.get("suppressedTargetKeys"):
            merged["suppressedTargetKeysAtSelection"] = list(selection.get("suppressedTargetKeys") or [])
    elif selection.get("blocked"):
        merged.setdefault("actionTargetSource", "route_detour_safety_block")
        merged.setdefault("actionability", "blocked")
    elif str(advisory_source or "").lower() in {"static_route_prior", "route_context_goal"}:
        merged.setdefault("actionTargetSource", str(advisory_source))
        merged.setdefault("actionability", "advisory_only")
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
    target = _resource_target_from_context(status, brain, active_target, overlay_selected, source_canvas_size=source_canvas_size)
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
    action_status = _resource_live_action_status(target)
    if action_status.get("blockedByLiveAction") is True:
        target = dict(target)
        target["resourceLiveActionStatus"] = action_status
        target["actionability"] = "blocked_no_matching_action"
        target["resourceSelectionRejectionReason"] = ",".join(action_status.get("rejectionReasons") or ["no_matching_live_resource_action"])
        proposal = _proposal(
            "select_resource_target",
            target_kind="resource",
            target=target,
            reason="resource_target_missing_live_action",
            confidence=0.25,
            required_context=["target", "inventory", "resource_action"],
            warnings=["resource target lacks a live matching action; refusing to click"],
            missing=["resource_action"],
            source_tick=source_tick,
            input_geometry=input_geometry,
            source_canvas_size=source_canvas_size,
            status=status,
            brain=brain,
            suppress_click_point=True,
        )
        if isinstance(proposal.target_explanation, dict):
            proposal.target_explanation["resourceLiveActionStatus"] = action_status
            proposal.target_explanation["resourceSelectionRejectionReason"] = target["resourceSelectionRejectionReason"]
        return proposal
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
    resource_candidates = [
        candidate
        for candidate in _resource_candidate_lists(status, brain, active_target, overlay_selected)
        if _is_resource_target_candidate(candidate)
    ]
    resource_view = _resource_view_score(
        status=status,
        brain=brain,
        candidates=resource_candidates or [target],
        selected_target=target,
        source_canvas_size=source_canvas_size,
    )
    target["resourceViewScore"] = resource_view
    target["resourceViewClassification"] = resource_view.get("classification")
    target["resourceCameraRecoveryRecommended"] = resource_view.get("cameraRecoveryRecommended")
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
    hover_ready_for_selected = resource_view.get("selectedTargetHoverReady") is True
    selected_projection_safe = (
        hover_ready_for_selected
        and projection_status.get("classification") == "safe"
        and projection_status.get("safeAimPointAvailable") is True
    )
    recovery_reason = str(resource_view.get("classification") or "")
    worksite_drift = recovery_reason == "needs_worksite_recenter"
    if (
        resource_view.get("cameraRecoveryRecommended") is True
        and _tile_from(target)
        and (worksite_drift or not selected_projection_safe)
    ):
        return _resource_view_recovery_proposal(
            target=target,
            projection_status=projection_status,
            resource_view_score=resource_view,
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
    proposal = _proposal(
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
    if isinstance(proposal.target_explanation, dict):
        proposal.target_explanation["resourceViewScore"] = dict(resource_view)
        proposal.target_explanation["resourceViewClassification"] = resource_view.get("classification")
        proposal.target_explanation["resourceCameraRecoveryRecommended"] = resource_view.get("cameraRecoveryRecommended")
    return proposal


def build_action_proposal(status_or_context: dict[str, Any]) -> ActionProposal:
    status, brain = _status_context(status_or_context)
    input_geometry = input_geometry_from_status(status)
    source_canvas_size = source_canvas_size_from_status(status)
    generic = _dict(brain.get("genericTaskState"))
    inventory = _dict(brain.get("inventoryContext"))
    service = _dict(brain.get("serviceContext"))
    pathing = _dict(brain.get("pathingContext"))
    suppressed_action_keys = _suppressed_action_target_keys(status, brain)
    if suppressed_action_keys:
        pathing = dict(pathing)
        values = sorted(suppressed_action_keys)
        pathing["suppressedActionTargetKeys"] = values
        pathing["suppressedNavigationTargetKeys"] = values
    bank_ui = _dict(brain.get("bankUiContext"))
    bank_operation = _dict(brain.get("bankOperationContext"))
    close_bank = _dict(brain.get("closeBankContext"))
    resource_return = _dict(brain.get("resourceReturnContext"))
    service_route = _service_route_context(status, brain, service)
    return_route = _return_route_context(status, brain)
    active_target = _dict(generic.get("activeIntentTarget"))
    overlay_selected = _overlay_selected(status, brain)
    source_tick = status.get("latestTick") if isinstance(status.get("latestTick"), int) else None
    phase = _lower(generic.get("phase") or status.get("phase") or status.get("brainPhase"))
    active_intent = _lower(generic.get("activeIntent") or status.get("activeIntent"))
    returning_to_resource_intent = phase == "return_to_resource" or active_intent in {"return_to_resource_area", "navigate_to_resource_area"}
    if phase == "goal_complete" or active_intent == "goal_complete":
        return _proposal(
            "none",
            target_kind="none",
            reason="goal_complete",
            confidence=1.0,
            required_context=["task_lifecycle"],
            source_tick=source_tick,
            input_geometry=input_geometry,
            source_canvas_size=source_canvas_size,
            status=status,
            brain=brain,
        )
    if phase in {"wait_for_result", "waiting_for_result"} or active_intent in {"wait_for_result", "wait_for_resource_result"}:
        return _proposal(
            "wait_for_resource_result",
            target_kind="none",
            reason="waiting_for_previous_action_result",
            confidence=1.0,
            warnings=["already waiting for previous action result"],
            required_context=["action_lifecycle"],
            source_tick=source_tick,
            input_geometry=input_geometry,
            source_canvas_size=source_canvas_size,
            status=status,
            brain=brain,
        )

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
    dialogue_route = return_route if returning_to_resource_intent and return_route else (service_route if service_required and service_route else (return_route or service_route))
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
    if returning_to_resource_intent or not service_required:
        if (_bool(return_route.get("returnActionReady")) is True or _bool(return_route.get("actionReady")) is True) and return_route_target:
            return_route_target, _safe_aimpoint = _resource_target_with_safe_aimpoint(
                return_route_target,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
            if not isinstance(_safe_aimpoint, dict) or _safe_aimpoint.get("status") != "PASS":
                exposure = _service_target_exposure(
                    return_route_target,
                    _safe_aimpoint,
                    source_canvas_size=source_canvas_size,
                    status=status,
                    brain=brain,
                )
                if exposure.get("shouldAttemptCameraExposure") is True:
                    return _service_view_recovery_proposal(
                        target=return_route_target,
                        exposure=exposure,
                        input_geometry=input_geometry,
                        source_canvas_size=source_canvas_size,
                        source_tick=source_tick,
                        status=status,
                        brain=brain,
                        reason="service_view_recovery_needed",
                        confidence=0.76,
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

        return_recovery_target = _route_census_recovery_target(return_route)
        if return_recovery_target:
            return_recovery_target, _safe_aimpoint = _resource_target_with_safe_aimpoint(
                return_recovery_target,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
            exposure = _service_target_exposure(
                return_recovery_target,
                _safe_aimpoint,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
            if exposure.get("shouldAttemptCameraExposure") is True:
                return _service_view_recovery_proposal(
                    target=return_recovery_target,
                    exposure=exposure,
                    input_geometry=input_geometry,
                    source_canvas_size=source_canvas_size,
                    source_tick=source_tick,
                    status=status,
                    brain=brain,
                    reason="return_route_transition_view_recovery_needed",
                    confidence=0.74,
                )

    post_bank_resource_reacquired = _return_resource_target_reacquired(
        status=status,
        resource_return=resource_return,
        active_target=active_target,
        overlay_selected=overlay_selected,
    )
    if not service_required and banking_complete and _bool(bank_ui.get("bankOpen")) is not True and post_bank_resource_reacquired:
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

    if returning_to_resource_intent or not service_required:
        return_navigation_target = _dict(return_route.get("currentNavigationTarget"))
        if return_navigation_target:
            target = _path_target(_pathing_for_resource_return(pathing), return_navigation_target, "Resource return")
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

        if active_intent in {"return_to_resource_area", "navigate_to_resource_area"} and _bool(pathing.get("pathingNeeded")) is True:
            target = _path_target(_pathing_for_resource_return(pathing), active_target, "Resource return")
            return _proposal(
                "return_to_resource_area",
                target_kind="path_tile",
                target=target,
                reason=str(pathing.get("reason") or "resource_return_pathing"),
                confidence=0.78,
                required_context=["pathing"],
                source_tick=source_tick,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )

        if _bool(resource_return.get("returnDestinationAvailable")) is True:
            fallback_target = _resource_return_fallback_target(active_target, resource_return)
            target = _path_target(_pathing_for_resource_return(pathing), fallback_target, "Resource return")
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
        exposure = _service_target_exposure(
            target,
            _safe_aimpoint,
            source_canvas_size=source_canvas_size,
            status=status,
            brain=brain,
        )
        if exposure.get("shouldAttemptCameraExposure") is True:
            return _service_view_recovery_proposal(
                target=target,
                exposure=exposure,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                source_tick=source_tick,
                status=status,
                brain=brain,
                reason="service_view_recovery_needed",
                confidence=0.76,
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
        post_bank_target = _resource_target_from_context(status, brain, active_target, overlay_selected, source_canvas_size=source_canvas_size)
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

    if service_required and not banking_complete:
        route_target = _service_route_interaction_target(service_route)
        route_service_target = _service_route_service_target(service_route)
        if _bool(service_route.get("actionReady")) is True and route_service_target:
            route_service_target, _safe_aimpoint = _resource_target_with_safe_aimpoint(
                route_service_target,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
            exposure = _service_target_exposure(
                route_service_target,
                _safe_aimpoint,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
            if exposure.get("shouldAttemptCameraExposure") is True:
                return _service_view_recovery_proposal(
                    target=route_service_target,
                    exposure=exposure,
                    input_geometry=input_geometry,
                    source_canvas_size=source_canvas_size,
                    source_tick=source_tick,
                    status=status,
                    brain=brain,
                    reason="service_view_recovery_needed",
                    confidence=0.78,
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
            if not isinstance(_safe_aimpoint, dict) or _safe_aimpoint.get("status") != "PASS":
                exposure = _service_target_exposure(
                    route_target,
                    _safe_aimpoint,
                    source_canvas_size=source_canvas_size,
                    status=status,
                    brain=brain,
                )
                if exposure.get("shouldAttemptCameraExposure") is True:
                    return _service_view_recovery_proposal(
                        target=route_target,
                        exposure=exposure,
                        input_geometry=input_geometry,
                        source_canvas_size=source_canvas_size,
                        source_tick=source_tick,
                        status=status,
                        brain=brain,
                        reason="service_view_recovery_needed",
                        confidence=0.76,
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

        route_navigation_target = _dict(service_route.get("currentNavigationTarget"))
        if _bool(pathing.get("pathingNeeded")) is True and (_pathing_has_concrete_waypoint(pathing) or route_navigation_target):
            return _proposal(
                "navigate_to_service",
                target_kind="path_tile",
                target=_path_target(pathing, route_navigation_target or _service_target(service, generic), "Service waypoint"),
                reason="pathing_to_service",
                confidence=0.74,
                required_context=["pathing"],
                source_tick=source_tick,
                input_geometry=input_geometry,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )

        service_recovery_target = _route_census_recovery_target(service_route)
        if service_recovery_target:
            service_recovery_target, _safe_aimpoint = _resource_target_with_safe_aimpoint(
                service_recovery_target,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
            exposure = _service_target_exposure(
                service_recovery_target,
                _safe_aimpoint,
                source_canvas_size=source_canvas_size,
                status=status,
                brain=brain,
            )
            if exposure.get("shouldAttemptCameraExposure") is True:
                return _service_view_recovery_proposal(
                    target=service_recovery_target,
                    exposure=exposure,
                    input_geometry=input_geometry,
                    source_canvas_size=source_canvas_size,
                    source_tick=source_tick,
                    status=status,
                    brain=brain,
                    reason="service_route_transition_view_recovery_needed",
                    confidence=0.74,
                )

        if _bool(pathing.get("pathingNeeded")) is True:
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

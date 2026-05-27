from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from input_control import camera_control


SCHEMA = "target_view_state.v1"
POLICY_SCHEMA = "target_view_policy.v1"
CAMERA_RESPONSE_SCHEMA = "camera_response_calibration.v1"
CAMERA_YAW_UNITS = 2048


@dataclass(frozen=True)
class TargetViewPolicy:
    target_kind: str
    min_visible_area_px: float
    min_visible_area_ratio: float | None
    min_edge_distance_px: float
    comfortable_central_region: float
    min_centrality_score: float
    allow_edge_click: bool
    require_hover_confirmation: bool
    require_top_menu_match: bool
    allow_lower_menu_match: bool
    allow_minimap: bool
    action_safety_level: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": POLICY_SCHEMA,
            "targetKind": self.target_kind,
            "minVisibleAreaPx": self.min_visible_area_px,
            "minVisibleAreaRatio": self.min_visible_area_ratio,
            "minEdgeDistancePx": self.min_edge_distance_px,
            "comfortableCentralRegion": self.comfortable_central_region,
            "minCentralityScore": self.min_centrality_score,
            "allowEdgeClick": self.allow_edge_click,
            "requireHoverConfirmation": self.require_hover_confirmation,
            "requireTopMenuMatch": self.require_top_menu_match,
            "allowLowerMenuMatch": self.allow_lower_menu_match,
            "allowMinimap": self.allow_minimap,
            "actionSafetyLevel": self.action_safety_level,
        }


_POLICIES: dict[str, TargetViewPolicy] = {
    "service_object": TargetViewPolicy(
        target_kind="service_object",
        min_visible_area_px=96.0,
        min_visible_area_ratio=0.35,
        min_edge_distance_px=32.0,
        comfortable_central_region=0.78,
        min_centrality_score=0.18,
        allow_edge_click=False,
        require_hover_confirmation=True,
        require_top_menu_match=True,
        allow_lower_menu_match=False,
        allow_minimap=False,
        action_safety_level="strict",
    ),
    "resource_object": TargetViewPolicy(
        target_kind="resource_object",
        min_visible_area_px=128.0,
        min_visible_area_ratio=0.25,
        min_edge_distance_px=24.0,
        comfortable_central_region=0.72,
        min_centrality_score=0.12,
        allow_edge_click=False,
        require_hover_confirmation=True,
        require_top_menu_match=True,
        allow_lower_menu_match=False,
        allow_minimap=False,
        action_safety_level="strict",
    ),
    "route_object": TargetViewPolicy(
        target_kind="route_object",
        min_visible_area_px=64.0,
        min_visible_area_ratio=0.15,
        min_edge_distance_px=16.0,
        comfortable_central_region=0.82,
        min_centrality_score=0.08,
        allow_edge_click=False,
        require_hover_confirmation=True,
        require_top_menu_match=True,
        allow_lower_menu_match=False,
        allow_minimap=False,
        action_safety_level="strict",
    ),
    "navigation_waypoint": TargetViewPolicy(
        target_kind="navigation_waypoint",
        min_visible_area_px=0.0,
        min_visible_area_ratio=None,
        min_edge_distance_px=8.0,
        comfortable_central_region=0.90,
        min_centrality_score=0.0,
        allow_edge_click=True,
        require_hover_confirmation=True,
        require_top_menu_match=True,
        allow_lower_menu_match=False,
        allow_minimap=False,
        action_safety_level="normal",
    ),
    "npc": TargetViewPolicy(
        target_kind="npc",
        min_visible_area_px=128.0,
        min_visible_area_ratio=0.25,
        min_edge_distance_px=24.0,
        comfortable_central_region=0.72,
        min_centrality_score=0.12,
        allow_edge_click=False,
        require_hover_confirmation=True,
        require_top_menu_match=True,
        allow_lower_menu_match=False,
        allow_minimap=False,
        action_safety_level="strict",
    ),
    "ground_item": TargetViewPolicy(
        target_kind="ground_item",
        min_visible_area_px=32.0,
        min_visible_area_ratio=0.10,
        min_edge_distance_px=16.0,
        comfortable_central_region=0.86,
        min_centrality_score=0.05,
        allow_edge_click=False,
        require_hover_confirmation=True,
        require_top_menu_match=True,
        allow_lower_menu_match=False,
        allow_minimap=False,
        action_safety_level="normal",
    ),
    "widget": TargetViewPolicy(
        target_kind="widget",
        min_visible_area_px=0.0,
        min_visible_area_ratio=None,
        min_edge_distance_px=0.0,
        comfortable_central_region=1.0,
        min_centrality_score=0.0,
        allow_edge_click=True,
        require_hover_confirmation=False,
        require_top_menu_match=False,
        allow_lower_menu_match=True,
        allow_minimap=False,
        action_safety_level="normal",
    ),
}


def target_view_policy(target_kind: str | None) -> dict[str, Any]:
    kind = str(target_kind or "unknown").strip() or "unknown"
    policy = _POLICIES.get(kind) or _POLICIES["route_object"]
    if kind == "unknown":
        policy = _POLICIES["route_object"]
    return policy.to_dict() | {"targetKind": kind}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _float(value: Any, default: float | None = None) -> float | None:
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


def _int(value: Any, default: int | None = None) -> int | None:
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


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _location(value: Any) -> dict[str, Any] | None:
    value = _dict(value)
    nested = _dict(value.get("worldLocation") or value.get("world") or value.get("targetTile") or value.get("tile"))
    if nested:
        value = nested
    x = _first_present(value.get("worldX"), value.get("x"))
    y = _first_present(value.get("worldY"), value.get("y"))
    if x is None or y is None:
        return None
    return {"worldX": x, "worldY": y, "plane": _first_present(value.get("plane"), value.get("z"), 0)}


def _point(value: Any) -> dict[str, Any] | None:
    value = _dict(value)
    x = _first_present(value.get("x"), value.get("canvasX"), value.get("screenX"), value.get("centerX"))
    y = _first_present(value.get("y"), value.get("canvasY"), value.get("screenY"), value.get("centerY"))
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def _bounds_area(value: Any) -> float | None:
    bounds = _dict(value)
    if isinstance(bounds.get("bounds"), dict):
        return _bounds_area(bounds.get("bounds"))
    width = _float(_first_present(bounds.get("width"), bounds.get("w")))
    height = _float(_first_present(bounds.get("height"), bounds.get("h")))
    if width is None and bounds.get("right") is not None:
        left = _float(_first_present(bounds.get("left"), bounds.get("x"), bounds.get("minX")))
        right = _float(bounds.get("right"))
        if left is not None and right is not None:
            width = right - left
    if height is None and bounds.get("bottom") is not None:
        top = _float(_first_present(bounds.get("top"), bounds.get("y"), bounds.get("minY")))
        bottom = _float(bounds.get("bottom"))
        if top is not None and bottom is not None:
            height = bottom - top
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    return float(width) * float(height)


def _projection_from_target(target: dict[str, Any]) -> dict[str, Any]:
    for key in ("projectionStatus", "projection", "projectionContext"):
        value = target.get(key)
        if isinstance(value, dict) and value:
            return value
    return {}


def compute_target_bearing(player_location: Any, target_location: Any) -> dict[str, Any]:
    player = _location(player_location)
    target = _location(target_location)
    if not player or not target:
        return {
            "schema": "target_bearing.v1",
            "available": False,
            "targetBearing": None,
            "targetBearingDegrees": None,
            "coordinateModel": "osrs_world_atan2_dx_dy",
        }
    px = _float(player.get("worldX"))
    py = _float(player.get("worldY"))
    tx = _float(target.get("worldX"))
    ty = _float(target.get("worldY"))
    if px is None or py is None or tx is None or ty is None:
        return {
            "schema": "target_bearing.v1",
            "available": False,
            "targetBearing": None,
            "targetBearingDegrees": None,
            "coordinateModel": "osrs_world_atan2_dx_dy",
        }
    dx = tx - px
    dy = ty - py
    if dx == 0 and dy == 0:
        angle = 0.0
    else:
        angle = math.atan2(dx, dy)
    normalized = angle % (2.0 * math.pi)
    units = int(round(normalized / (2.0 * math.pi) * CAMERA_YAW_UNITS)) % CAMERA_YAW_UNITS
    return {
        "schema": "target_bearing.v1",
        "available": True,
        "coordinateModel": "osrs_world_atan2_dx_dy",
        "dx": round(dx, 3),
        "dy": round(dy, 3),
        "targetBearing": units,
        "targetBearingDegrees": round(math.degrees(normalized), 3),
    }


def normalize_yaw_error(target_yaw: Any, current_yaw: Any, *, modulus: int = CAMERA_YAW_UNITS) -> int | None:
    target = _int(target_yaw)
    current = _int(current_yaw)
    if target is None or current is None:
        return None
    half = int(modulus) // 2
    return int(((target - current + half) % int(modulus)) - half)


def camera_response_calibration_from_status(status: dict[str, Any] | None = None) -> dict[str, Any]:
    status = _dict(status)
    for key in ("cameraResponseCalibration", "camera_response_calibration"):
        calibration = _dict(status.get(key))
        if calibration:
            calibration.setdefault("schema", CAMERA_RESPONSE_SCHEMA)
            return calibration
    # From live observations in this VM, a 310 ms right-arrow hold changed yaw
    # by about 180 camera units. This is an advisory fallback until a session
    # calibration is recorded; feedback after each recovery remains authoritative.
    return {
        "schema": CAMERA_RESPONSE_SCHEMA,
        "status": "fallback_estimate",
        "inputMethod": "keyboard_arrows",
        "yawRatePerMsLeft": 0.58,
        "yawRatePerMsRight": 0.58,
        "pitchRatePerMsUp": None,
        "pitchRatePerMsDown": None,
        "calibrationMethod": "observed_default_estimate",
        "confidence": "low",
    }


def _projection_classification(projection: dict[str, Any], safe_aimpoint: dict[str, Any], target: dict[str, Any]) -> str:
    for key in ("classification", "status", "reason"):
        value = projection.get(key)
        if isinstance(value, str) and value:
            return value
    if str(safe_aimpoint.get("status") or "").upper() == "PASS":
        return "actionable"
    if safe_aimpoint.get("rejectionReason"):
        return str(safe_aimpoint["rejectionReason"])
    if target.get("onScreen") is False or projection.get("onScreen") is False:
        return "offscreen"
    return "unknown"


def _visible_metrics(
    *,
    target: dict[str, Any],
    safe_aimpoint: dict[str, Any],
    projection: dict[str, Any],
    canvas_point: dict[str, Any] | None,
    viewport: dict[str, Any] | None,
    canvas_size: dict[str, Any] | None,
    policy: dict[str, Any],
) -> dict[str, Any]:
    rect = camera_control.viewport_rect(viewport, canvas_size=canvas_size)
    point = _point(safe_aimpoint) or _point(canvas_point)
    edge_distance = _float(
        _first_present(
            safe_aimpoint.get("distanceToViewportEdgePx"),
            safe_aimpoint.get("distanceToCanvasEdgePx"),
            projection.get("edgeDistancePx"),
            projection.get("distanceToViewportEdgePx"),
        )
    )
    if point and edge_distance is None:
        x = float(point["x"])
        y = float(point["y"])
        edge_distance = min(x - rect["left"], rect["right"] - x, y - rect["top"], rect["bottom"] - y)
    visible_area_px = _float(
        _first_present(
            safe_aimpoint.get("clippedVisibleAreaPx"),
            projection.get("clippedVisibleAreaPx"),
            projection.get("visibleAreaPx"),
        )
    )
    if visible_area_px is None:
        visible_area_px = (
            _bounds_area(safe_aimpoint.get("bounds"))
            or _bounds_area(projection.get("bounds"))
            or _bounds_area(projection.get("convexHullBounds"))
            or _bounds_area(target.get("bounds"))
            or _bounds_area(target.get("clickboxBounds"))
        )
    visible_ratio = _float(
        _first_present(
            safe_aimpoint.get("clippedVisibleAreaRatio"),
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
        centrality_score = round(max(0.0, min(1.0, 1.0 - normalized_distance)), 3)
        fraction = _float(policy.get("comfortableCentralRegion"), 0.78) or 0.78
        margin_x = rect["width"] * (1.0 - fraction) / 2.0
        margin_y = rect["height"] * (1.0 - fraction) / 2.0
        comfortable_region_met = bool(
            rect["left"] + margin_x <= x <= rect["right"] - margin_x
            and rect["top"] + margin_y <= y <= rect["bottom"] - margin_y
        )
    safe_click = bool(
        str(safe_aimpoint.get("status") or "").upper() == "PASS"
        and safe_aimpoint.get("canvasX") is not None
        and safe_aimpoint.get("canvasY") is not None
    )
    min_area_px = _float(policy.get("minVisibleAreaPx"), 0.0) or 0.0
    min_ratio = _float(policy.get("minVisibleAreaRatio"))
    min_edge = _float(policy.get("minEdgeDistancePx"), 0.0) or 0.0
    min_centrality = _float(policy.get("minCentralityScore"), 0.0) or 0.0
    visible_area_ok = bool(visible_area_px is None or visible_area_px >= min_area_px) and bool(
        min_ratio is None or visible_ratio is None or visible_ratio >= min_ratio
    )
    edge_ok = bool(edge_distance is not None and edge_distance >= min_edge)
    centrality_ok = bool(centrality_score is None or centrality_score >= min_centrality)
    raw_center_inside = safe_aimpoint.get("rawCenterInsideViewport")
    if raw_center_inside is False and edge_distance is not None and edge_distance < max(min_edge, 1.0):
        comfortable_region_met = False
    edge_sliver = bool(
        safe_click
        and not bool(policy.get("allowEdgeClick"))
        and (
            (edge_distance is not None and edge_distance < min_edge)
            or (visible_area_px is not None and visible_area_px < min_area_px)
            or (min_ratio is not None and visible_ratio is not None and visible_ratio < min_ratio)
        )
    )
    usable = bool(
        safe_click
        and visible_area_ok
        and edge_ok
        and centrality_ok
        and comfortable_region_met
        and safe_aimpoint.get("uiBlocked") is not True
    )
    score = 0.0
    if safe_click:
        score += 30.0
    if visible_area_ok:
        score += 20.0
    if edge_ok:
        score += 20.0
    if comfortable_region_met:
        score += 20.0
    if safe_aimpoint.get("uiBlocked") is not True:
        score += 10.0
    if edge_distance is not None:
        score += min(10.0, max(0.0, edge_distance) / 8.0)
    if centrality_score is not None:
        score += centrality_score * 10.0
    return {
        "safeClickAvailable": safe_click,
        "visibleAreaPx": round(float(visible_area_px), 3) if visible_area_px is not None else None,
        "visibleAreaRatio": round(float(visible_ratio), 3) if visible_ratio is not None else None,
        "edgeDistancePx": round(float(edge_distance), 3) if edge_distance is not None else None,
        "centralityScore": centrality_score,
        "edgeSliverVisible": edge_sliver,
        "usableExposureScore": round(max(0.0, min(100.0, score)), 3),
        "usableExposureThresholdMet": usable,
        "comfortableViewRegionMet": comfortable_region_met,
        "visibleAreaThresholdMet": visible_area_ok,
        "edgeDistanceThresholdMet": edge_ok,
        "centralityThresholdMet": centrality_ok,
        "uiBlocked": safe_aimpoint.get("uiBlocked") is True,
    }


def _canvas_point_from_target(target: dict[str, Any], safe_aimpoint: dict[str, Any], projection: dict[str, Any]) -> dict[str, Any] | None:
    for value in (
        safe_aimpoint,
        projection.get("aimPoint"),
        projection.get("canvasLocation"),
        projection.get("canvasPoint"),
        target.get("aimPoint"),
        target.get("canvasLocation"),
        target.get("canvasPoint"),
    ):
        point = _point(value)
        if point:
            return point
    return None


def build_target_view_state(
    target: dict[str, Any] | None,
    *,
    target_kind: str,
    player_location: dict[str, Any] | None = None,
    expected_action: str | None = None,
    target_source: str | None = None,
    target_route_relevant: bool | None = None,
    target_action_relevant: bool | None = None,
    safe_aimpoint: dict[str, Any] | None = None,
    viewport: dict[str, Any] | None = None,
    source_canvas_size: dict[str, Any] | None = None,
    hover: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
    evidence_sources: list[str] | None = None,
) -> dict[str, Any]:
    target = _dict(target)
    safe = _dict(safe_aimpoint or target.get("safeAimPoint"))
    projection = _projection_from_target(target)
    canvas_point = _canvas_point_from_target(target, safe, projection)
    policy = target_view_policy(target_kind)
    location = _location(target)
    player = _location(player_location)
    bearing = compute_target_bearing(player, location)
    camera_yaw = _first_present(_dict(viewport).get("cameraYaw"), _dict(status).get("cameraYaw"))
    camera_pitch = _first_present(_dict(viewport).get("cameraPitch"), _dict(status).get("cameraPitch"))
    camera_scale = _first_present(_dict(viewport).get("cameraScale"), _dict(status).get("cameraScale"))
    target_bearing = bearing.get("targetBearing")
    yaw_error = normalize_yaw_error(target_bearing, camera_yaw)
    classification = _projection_classification(projection, safe, target)
    exposure_error = camera_control.exposure_error_from_canvas_point(
        canvas_point,
        viewport=viewport,
        canvas_size=source_canvas_size,
    )
    metrics = _visible_metrics(
        target=target,
        safe_aimpoint=safe,
        projection=projection,
        canvas_point=canvas_point,
        viewport=viewport,
        canvas_size=source_canvas_size,
        policy=policy,
    )
    loaded = bool(target)
    on_screen = bool(
        projection.get("actionableByCanvas") is True
        or projection.get("onScreen") is True
        or projection.get("visible") is True
        or target.get("onScreen") is True
        or safe.get("status") == "PASS"
    )
    offscreen = bool(
        classification == "offscreen"
        or exposure_error.get("status") == "offscreen"
        or target.get("onScreen") is False
        or projection.get("onScreen") is False
    )
    hover = _dict(hover)
    hover_top_option = hover.get("topOption")
    hover_top_target = hover.get("topTarget")
    hover_expected = None
    if expected_action and hover_top_option:
        hover_expected = str(expected_action).lower() in str(hover_top_option).lower()
    usable = bool(metrics["usableExposureThresholdMet"])
    edge_sliver = bool(metrics["edgeSliverVisible"])
    if not loaded:
        view_classification = "target_not_loaded"
        exposure_result = "target_not_loaded"
    elif usable:
        view_classification = "usable_target_view"
        exposure_result = "comfortably_exposed"
    elif edge_sliver:
        view_classification = "target_edge_sliver"
        exposure_result = "edge_sliver_only"
    elif offscreen:
        view_classification = "target_loaded_offscreen"
        exposure_result = "still_offscreen"
    elif metrics["safeClickAvailable"] and not usable:
        view_classification = "target_visible_but_not_usable"
        exposure_result = "insufficient_visible_area" if metrics["visibleAreaThresholdMet"] is False else "failed"
    elif not metrics["safeClickAvailable"]:
        view_classification = "poor_target_projection"
        exposure_result = "still_no_projection"
    else:
        view_classification = "poor_target_projection"
        exposure_result = "failed"
    should_attempt = bool(
        loaded
        and target_kind != "widget"
        and not usable
        and (
            offscreen
            or edge_sliver
            or not metrics["safeClickAvailable"]
            or metrics["visibleAreaThresholdMet"] is False
            or metrics["edgeDistanceThresholdMet"] is False
            or metrics["comfortableViewRegionMet"] is False
            or classification in {"raw_aimpoint_outside_interactable_region", "centerOffViewport", "centerOutsideInteractableRegion"}
        )
    )
    if not loaded:
        reason = "target_not_loaded"
    elif edge_sliver:
        reason = "target_edge_sliver"
    elif offscreen:
        reason = "target_loaded_offscreen"
    elif not metrics["safeClickAvailable"]:
        reason = "target_screen_click_point_unavailable"
    elif metrics["visibleAreaThresholdMet"] is False:
        reason = "target_insufficient_visible_area"
    elif metrics["edgeDistanceThresholdMet"] is False:
        reason = "target_too_close_to_edge"
    elif metrics["comfortableViewRegionMet"] is False:
        reason = "target_not_in_comfortable_view_region"
    else:
        reason = "not_needed"
    sources = list(evidence_sources or [])
    for source, present in (
        ("live_world_model", bool(target.get("worldModelSource") or projection)),
        ("action_proposal", True),
        ("projection_audit", bool(projection)),
    ):
        if present and source not in sources:
            sources.append(source)
    state = {
        "schema": SCHEMA,
        "targetKind": target_kind,
        "targetName": target.get("targetName") or target.get("name") or target.get("objectName") or target.get("classId"),
        "targetId": _first_present(target.get("id"), target.get("rawId"), target.get("objectId")),
        "targetWorldLocation": location,
        "targetPlane": _first_present(target.get("plane"), _dict(location).get("plane")),
        "targetSource": target_source or target.get("source") or target.get("targetSource"),
        "targetLoaded": loaded,
        "targetRouteRelevant": target_route_relevant,
        "targetActionRelevant": target_action_relevant,
        "targetExpectedAction": expected_action,
        "targetRequiredSkill": target.get("requiredSkill"),
        "targetRequiredLevel": target.get("requiredLevel"),
        "currentProjectionStatus": classification,
        "currentCanvasPoint": canvas_point,
        "currentSafeAimPoint": safe if safe else None,
        "currentScreenClickPoint": target.get("screenClickPoint") or target.get("screenAimPoint"),
        "currentlyOnScreen": on_screen,
        "currentlyOffscreen": offscreen,
        "edgeClipped": classification == "edge_clipped",
        "edgeSliverVisible": edge_sliver,
        "visibleAreaPx": metrics["visibleAreaPx"],
        "visibleAreaRatio": metrics["visibleAreaRatio"],
        "centralityScore": metrics["centralityScore"],
        "edgeDistancePx": metrics["edgeDistancePx"],
        "usableExposureScore": metrics["usableExposureScore"],
        "usableExposureThresholdMet": usable,
        "comfortableViewRegionMet": metrics["comfortableViewRegionMet"],
        "visibleAreaThresholdMet": metrics["visibleAreaThresholdMet"],
        "edgeDistanceThresholdMet": metrics["edgeDistanceThresholdMet"],
        "centralityThresholdMet": metrics["centralityThresholdMet"],
        "uiBlocked": metrics["uiBlocked"],
        "targetAmbiguityStatus": target.get("ambiguityStatus"),
        "hoverTopOption": hover_top_option,
        "hoverTopTarget": hover_top_target,
        "hoverExpectedTopMatch": hover_expected,
        "cameraYaw": camera_yaw,
        "cameraPitch": camera_pitch,
        "cameraZoomOrScale": camera_scale,
        "playerWorldLocation": player,
        "targetBearing": target_bearing,
        "targetBearingDegrees": bearing.get("targetBearingDegrees"),
        "yawErrorToTarget": yaw_error,
        "pitchErrorHint": exposure_error.get("dyFromCenter"),
        "viewQualityClassification": view_classification,
        "targetViewPolicy": policy,
        "shouldAttemptCameraExposure": should_attempt,
        "cameraExposureReason": reason,
        "exposureAttempts": 0,
        "exposureResult": exposure_result,
        "evidenceSources": sources,
        "finalDecision": "target_view_recovery" if should_attempt else ("target_action" if usable else "block_or_reposition"),
        "exposureError": exposure_error,
        "targetBearingEvidence": bearing,
    }
    state["cameraResponseCalibration"] = camera_response_calibration_from_status(status)
    state["cameraMotorPlan"] = target_camera_motor_plan(state, calibration=state["cameraResponseCalibration"])
    return state


def target_camera_motor_plan(
    target_view_state: dict[str, Any],
    *,
    calibration: dict[str, Any] | None = None,
    method: str | None = "keyboard_arrows",
    min_ms: int = 160,
    max_ms: int = 900,
    yaw_alignment_fraction: float = 0.82,
) -> dict[str, Any]:
    state = _dict(target_view_state)
    calibration = _dict(calibration) or camera_response_calibration_from_status()
    yaw_error = _int(state.get("yawErrorToTarget"))
    exposure_error = _dict(state.get("exposureError"))
    command = None
    reason = None
    hold_candidates: list[int] = []
    yaw_rate = None
    if yaw_error is not None and abs(yaw_error) >= 24:
        if yaw_error > 0:
            command = "yaw_right"
            yaw_rate = _float(calibration.get("yawRatePerMsRight"))
        else:
            command = "yaw_left"
            yaw_rate = _float(calibration.get("yawRatePerMsLeft"))
        reason = "target_bearing_yaw_alignment"
        if yaw_rate and yaw_rate > 0:
            hold_candidates.append(int(round(abs(yaw_error) * yaw_alignment_fraction / yaw_rate)))
    if command is None:
        command, reason = camera_control.camera_command_from_exposure_error(exposure_error, allow_pitch=True, allow_diagonal=True)
    dy = _float(exposure_error.get("dyFromCenter"), 0.0) or 0.0
    tolerance = float(exposure_error.get("tolerancePx") or camera_control.DEFAULT_VIEW_TOLERANCE_PX)
    if command in {"yaw_left", "yaw_right"} and abs(dy) >= tolerance:
        pitch = "pitch_up" if dy >= 0 else "pitch_down"
        command = f"{command}_{pitch}"
        reason = f"{reason}_with_projection_pitch"
    error_magnitude = max(
        abs(float(yaw_error or 0.0)),
        _float(exposure_error.get("errorMagnitude"), 0.0) or 0.0,
    )
    hold_candidates.append(
        camera_control.fitts_hold_duration_ms(
            error_magnitude,
            tolerance_px=max(24.0, tolerance),
            min_ms=min_ms,
            max_ms=max_ms,
            base_ms=120,
            slope_ms=110,
        )
    )
    hold_ms = max(int(min_ms), min(int(max_ms), max(hold_candidates or [min_ms])))
    spec = camera_control.camera_input_spec(method=method, command=command or "yaw_right")
    return {
        "schema": "target_view_camera_plan.v1",
        "cameraInputMethod": spec.method,
        "cameraDirectionChosen": spec.command,
        "cameraDirectionReason": reason or "projection_feedback",
        "cameraHoldMs": hold_ms,
        "keyCombination": list(spec.keys),
        "dragPathSummary": {"dx": spec.drag_dx, "dy": spec.drag_dy} if spec.method == "middle_mouse_drag" else None,
        "wheelImpulseCount": 0,
        "wheelImpulseSpacingMs": None,
        "targetBearing": state.get("targetBearing"),
        "yawErrorBefore": yaw_error,
        "pitchErrorHint": state.get("pitchErrorHint"),
        "errorMagnitude": round(error_magnitude, 3),
        "viewTolerancePx": tolerance,
        "cameraResponseCalibration": calibration,
        "controlLaw": "bearing_yaw_alignment_plus_fitts_hold",
    }

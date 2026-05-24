from __future__ import annotations

from typing import Any


SAFE_AIMPOINT_SCHEMA = "safe_aimpoint.v1"
RESOURCE_PROJECTION_SCHEMA = "resource_projection_status.v1"
DEFAULT_EDGE_MARGIN_PX = 6
DEFAULT_MIN_VISIBLE_AREA_PX = 4.0
MAX_REASONABLE_CANVAS_COORDINATE = 100000.0


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
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


def _point_from(value: Any) -> dict[str, float] | None:
    value = _dict(value)
    x = _number(value.get("x", value.get("canvasX", value.get("screenX"))))
    y = _number(value.get("y", value.get("canvasY", value.get("screenY"))))
    if x is None or y is None:
        return None
    return {"x": x, "y": y, "source": value.get("source")}


def _projection_sentinel_number(value: Any) -> bool:
    number = _number(value)
    return number is not None and abs(float(number)) > MAX_REASONABLE_CANVAS_COORDINATE


def point_is_projection_sentinel(point: dict[str, Any] | None) -> bool:
    point = _dict(point)
    if not point:
        return False
    return any(_projection_sentinel_number(point.get(key)) for key in ("x", "y", "canvasX", "canvasY", "screenX", "screenY"))


def raw_aim_point(target: dict[str, Any]) -> dict[str, float] | None:
    for key in ("aimPoint", "aimPointContext", "suggestedClickPoint", "clickPoint", "canvasPoint", "canvasLocation", "canvasCenter"):
        point = _point_from(target.get(key))
        if point is not None:
            return point
    geometry = _dict(target.get("geometry"))
    if geometry:
        return raw_aim_point(geometry)
    return None


def _bounds_from(value: Any) -> dict[str, float] | None:
    value = _dict(value)
    if isinstance(value.get("bounds"), dict):
        return _bounds_from(value.get("bounds"))
    x = _number(value.get("x", value.get("left", value.get("minX"))))
    y = _number(value.get("y", value.get("top", value.get("minY"))))
    width = _number(value.get("width", value.get("w")))
    height = _number(value.get("height", value.get("h")))
    right = _number(value.get("right", value.get("maxX")))
    bottom = _number(value.get("bottom", value.get("maxY")))
    if width is None and right is not None and x is not None:
        width = right - x
    if height is None and bottom is not None and y is not None:
        height = bottom - y
    if x is None or y is None or width is None or height is None or width <= 0 or height <= 0:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def bounds_is_projection_sentinel(bounds: dict[str, Any] | None) -> bool:
    bounds = _dict(bounds)
    if not bounds:
        return False
    return any(_projection_sentinel_number(bounds.get(key)) for key in ("x", "y", "left", "top", "minX", "minY"))


def _candidate_bounds(target: dict[str, Any]) -> dict[str, float] | None:
    for key in ("clickboxBounds", "convexHullBounds", "bounds", "canvasTileBounds"):
        bounds = _bounds_from(target.get(key))
        if bounds:
            return bounds
    geometry = _dict(target.get("geometry"))
    if geometry:
        bounds = _candidate_bounds(geometry)
        if bounds:
            return bounds
    summary = _dict(target.get("geometrySummary"))
    if summary:
        for key in ("bounds", "aimBounds", "clickboxBounds", "convexHullBounds", "canvasTileBounds"):
            bounds = _bounds_from(summary.get(key))
            if bounds:
                return bounds
    return None


def _bounds_polygon(bounds: dict[str, float]) -> list[dict[str, float]]:
    x = bounds["x"]
    y = bounds["y"]
    w = bounds["width"]
    h = bounds["height"]
    return [
        {"x": x, "y": y},
        {"x": x + w, "y": y},
        {"x": x + w, "y": y + h},
        {"x": x, "y": y + h},
    ]


def _polygon_points(value: Any) -> list[dict[str, float]]:
    if isinstance(value, dict):
        for key in ("points", "vertices", "polygon"):
            points = _polygon_points(value.get(key))
            if points:
                return points
        if value.get("x") is not None or value.get("canvasX") is not None:
            point = _point_from(value)
            return [point] if point else []
    if not isinstance(value, list):
        return []
    points: list[dict[str, float]] = []
    for item in value:
        if isinstance(item, dict):
            point = _point_from(item)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            x = _number(item[0])
            y = _number(item[1])
            point = {"x": x, "y": y} if x is not None and y is not None else None
        else:
            point = None
        if point is not None:
            points.append({"x": float(point["x"]), "y": float(point["y"])})
    return points


def _candidate_polygon(target: dict[str, Any]) -> tuple[list[dict[str, float]], str | None]:
    for key, source in (
        ("clickableHull", "visibleHullInterior"),
        ("clickboxPolygon", "clippedClickboxInterior"),
        ("convexHull", "visibleHullInterior"),
        ("convexHullPolygon", "visibleHullInterior"),
        ("canvasTilePolygon", "visibleHullInterior"),
        ("tilePolygon", "visibleHullInterior"),
    ):
        points = _polygon_points(target.get(key))
        if len(points) >= 3:
            return points, source
    for key, source in (
        ("clickboxBounds", "clippedClickboxInterior"),
        ("convexHullBounds", "visibleHullInterior"),
        ("bounds", "boundsCenter"),
    ):
        bounds = _bounds_from(target.get(key))
        if bounds:
            return _bounds_polygon(bounds), source
    geometry = _dict(target.get("geometry"))
    if geometry:
        return _candidate_polygon(geometry)
    return [], None


def _rect_from_source_size(source_canvas_size: dict[str, Any] | None) -> dict[str, float]:
    size = _dict(source_canvas_size)
    width = _number(size.get("width", size.get("canvasWidth"))) or 765.0
    height = _number(size.get("height", size.get("canvasHeight"))) or 503.0
    return {"x": 0.0, "y": 0.0, "width": width, "height": height}


def viewport_rect(
    *,
    source_canvas_size: dict[str, Any] | None = None,
    viewport: dict[str, Any] | None = None,
) -> dict[str, float]:
    base = _rect_from_source_size(source_canvas_size)
    viewport = _dict(viewport)
    width = _number(viewport.get("viewportWidth", viewport.get("width")))
    height = _number(viewport.get("viewportHeight", viewport.get("height")))
    x = _number(viewport.get("viewportXOffset", viewport.get("x")))
    y = _number(viewport.get("viewportYOffset", viewport.get("y")))
    if width is None:
        width = _number(viewport.get("canvasWidth")) or base["width"]
    if height is None:
        height = _number(viewport.get("canvasHeight")) or base["height"]
    if x is None:
        x = 0.0
    if y is None:
        y = 0.0
    width = max(0.0, min(width, base["width"] - x))
    height = max(0.0, min(height, base["height"] - y))
    return {"x": x, "y": y, "width": width, "height": height}


def _inset_rect(rect: dict[str, float], margin: int) -> dict[str, float]:
    margin = max(0, int(margin))
    if rect["width"] <= margin * 2 or rect["height"] <= margin * 2:
        return dict(rect)
    return {
        "x": rect["x"] + margin,
        "y": rect["y"] + margin,
        "width": rect["width"] - margin * 2,
        "height": rect["height"] - margin * 2,
    }


def _inside_rect(point: dict[str, float] | None, rect: dict[str, float]) -> bool:
    if point is None:
        return False
    return (
        point["x"] >= rect["x"]
        and point["y"] >= rect["y"]
        and point["x"] <= rect["x"] + rect["width"]
        and point["y"] <= rect["y"] + rect["height"]
    )


def _clip_rect_bounds(points: list[dict[str, float]], rect: dict[str, float]) -> dict[str, float] | None:
    if not points:
        return None
    min_x = max(rect["x"], min(point["x"] for point in points))
    min_y = max(rect["y"], min(point["y"] for point in points))
    max_x = min(rect["x"] + rect["width"], max(point["x"] for point in points))
    max_y = min(rect["y"] + rect["height"], max(point["y"] for point in points))
    width = max_x - min_x
    height = max_y - min_y
    if width <= 0 or height <= 0:
        return None
    return {"x": min_x, "y": min_y, "width": width, "height": height}


def _area(bounds: dict[str, float] | None) -> float:
    if not bounds:
        return 0.0
    return max(0.0, bounds["width"]) * max(0.0, bounds["height"])


def _center(bounds: dict[str, float]) -> dict[str, float]:
    return {"x": bounds["x"] + bounds["width"] / 2.0, "y": bounds["y"] + bounds["height"] / 2.0}


def _clamp_point_to_bounds(point: dict[str, float], bounds: dict[str, float]) -> dict[str, float]:
    return {
        "x": min(max(point["x"], bounds["x"]), bounds["x"] + bounds["width"]),
        "y": min(max(point["y"], bounds["y"]), bounds["y"] + bounds["height"]),
    }


def _edge_distance(point: dict[str, float], rect: dict[str, float]) -> float:
    return min(
        point["x"] - rect["x"],
        point["y"] - rect["y"],
        rect["x"] + rect["width"] - point["x"],
        rect["y"] + rect["height"] - point["y"],
    )


def _unsafe_reason_name(reason: str | None) -> str | None:
    mapping = {
        "no_visible_interactable_geometry": "noVisibleInteractableGeometry",
        "raw_aimpoint_outside_interactable_region": "centerOffViewport",
        "projection_sentinel": "projectionSentinel",
        "ui_blocked": "uiBlocked",
    }
    if not reason:
        return None
    return mapping.get(reason, reason)


def _sample_points(bounds: dict[str, float], rect: dict[str, float]) -> list[dict[str, Any]]:
    center = _center(bounds)
    candidates = [center]
    for x_ratio, y_ratio in ((0.35, 0.5), (0.65, 0.5), (0.5, 0.35), (0.5, 0.65), (0.35, 0.35), (0.65, 0.35), (0.35, 0.65), (0.65, 0.65)):
        candidates.append({"x": bounds["x"] + bounds["width"] * x_ratio, "y": bounds["y"] + bounds["height"] * y_ratio})
    unique: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for point in candidates:
        rounded = {"x": int(round(point["x"])), "y": int(round(point["y"]))}
        key = (rounded["x"], rounded["y"])
        if key in seen or not _inside_rect({"x": float(rounded["x"]), "y": float(rounded["y"])}, rect):
            continue
        seen.add(key)
        unique.append(rounded)
    return unique


def _result(
    *,
    status: str,
    raw: dict[str, float] | None,
    source: str | None = None,
    point: dict[str, float] | None = None,
    canvas_rect: dict[str, float] | None = None,
    viewport: dict[str, float] | None = None,
    interactable_rect: dict[str, float] | None = None,
    clipped_area: float = 0.0,
    original_area: float = 0.0,
    sampled: list[dict[str, Any]] | None = None,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    canvas_rect = canvas_rect or {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
    viewport = viewport or canvas_rect
    interactable_rect = interactable_rect or viewport
    inside_canvas = _inside_rect(point, canvas_rect)
    inside_viewport = _inside_rect(point, viewport)
    inside_interactable = _inside_rect(point, interactable_rect)
    edge_distance = _edge_distance(point, viewport) if point is not None and inside_viewport else None
    unsafe_reasons: list[str] = []
    if raw is not None and not _inside_rect(raw, viewport):
        unsafe_reasons.append("centerOffViewport")
    if raw is not None and not _inside_rect(raw, interactable_rect):
        unsafe_reasons.append("centerOutsideInteractableRegion")
    mapped_rejection = _unsafe_reason_name(rejection_reason)
    if mapped_rejection:
        unsafe_reasons.append(mapped_rejection)
    if status != "PASS" and point is None and not unsafe_reasons:
        unsafe_reasons.append("noSafeVisibleAimPoint")
    unsafe_reasons = list(dict.fromkeys(unsafe_reasons))
    payload = {
        "schema": SAFE_AIMPOINT_SCHEMA,
        "status": status,
        "actionable": status == "PASS",
        "validButUnsafe": status != "PASS",
        "unsafeReasons": unsafe_reasons,
        "canvasX": int(round(point["x"])) if point is not None else None,
        "canvasY": int(round(point["y"])) if point is not None else None,
        "source": source,
        "insideCanvas": bool(inside_canvas),
        "insideViewport": bool(inside_viewport),
        "insideInteractableRegion": bool(inside_canvas and inside_viewport and inside_interactable),
        "uiBlocked": False,
        "distanceToViewportEdgePx": int(round(edge_distance)) if edge_distance is not None else None,
        "distanceToCanvasEdgePx": int(round(_edge_distance(point, canvas_rect))) if point is not None and inside_canvas else None,
        "clippedVisibleAreaPx": round(clipped_area, 3),
        "clippedVisibleAreaRatio": round(clipped_area / original_area, 6) if original_area > 0 else None,
        "hoverConfirmed": False,
        "hoverTopOption": None,
        "hoverTopTarget": None,
        "rawAimPoint": {"x": int(round(raw["x"])), "y": int(round(raw["y"])), "source": raw.get("source")} if raw else None,
        "rawCenterInsideViewport": bool(_inside_rect(raw, viewport)) if raw else None,
        "safePointInsideViewport": bool(inside_viewport),
        "sampledAimpoints": sampled or [],
        "acceptedAimpoint": {"x": int(round(point["x"])), "y": int(round(point["y"]))} if point is not None else None,
        "rejectedAimpoints": [],
        "rejectionReason": rejection_reason,
    }
    if status != "PASS":
        payload["acceptedAimpoint"] = None
    return payload


def safe_aimpoint_for_target(
    target: dict[str, Any] | None,
    *,
    source_canvas_size: dict[str, Any] | None = None,
    viewport: dict[str, Any] | None = None,
    edge_margin_px: int = DEFAULT_EDGE_MARGIN_PX,
    min_visible_area_px: float = DEFAULT_MIN_VISIBLE_AREA_PX,
) -> dict[str, Any]:
    target = _dict(target)
    raw = raw_aim_point(target)
    canvas_rect = _rect_from_source_size(source_canvas_size)
    visible_rect = viewport_rect(source_canvas_size=source_canvas_size, viewport=viewport)
    interactable_rect = _inset_rect(visible_rect, edge_margin_px)
    if target.get("uiBlocked") is True:
        result = _result(status="FAIL", raw=raw, canvas_rect=canvas_rect, viewport=visible_rect, interactable_rect=interactable_rect, rejection_reason="ui_blocked")
        result["uiBlocked"] = True
        return result

    if point_is_projection_sentinel(raw) or bounds_is_projection_sentinel(_candidate_bounds(target)):
        return _result(
            status="FAIL",
            raw=raw,
            canvas_rect=canvas_rect,
            viewport=visible_rect,
            interactable_rect=interactable_rect,
            rejection_reason="projection_sentinel",
        )

    polygon, polygon_source = _candidate_polygon(target)
    original_bounds = _clip_rect_bounds(polygon, canvas_rect) if polygon else None
    clipped = _clip_rect_bounds(polygon, interactable_rect) if polygon else None
    original_area = _area(original_bounds)
    clipped_area = _area(clipped)
    if raw is not None and _inside_rect(raw, interactable_rect):
        return _result(
            status="PASS",
            raw=raw,
            point=raw,
            source=str(raw.get("source") or "clickboxCenter"),
            canvas_rect=canvas_rect,
            viewport=visible_rect,
            interactable_rect=interactable_rect,
            clipped_area=clipped_area or original_area,
            original_area=original_area or clipped_area,
            sampled=_sample_points(clipped or {"x": raw["x"], "y": raw["y"], "width": 1.0, "height": 1.0}, interactable_rect),
        )
    if clipped is not None and clipped_area >= float(min_visible_area_px):
        point = _clamp_point_to_bounds(raw, clipped) if raw is not None else _center(clipped)
        source = polygon_source or "visibleHullInterior"
        if source == "boundsCenter" and raw is not None and not _inside_rect(raw, interactable_rect):
            source = "clippedClickboxInterior"
        return _result(
            status="PASS",
            raw=raw,
            point=point,
            source=source,
            canvas_rect=canvas_rect,
            viewport=visible_rect,
            interactable_rect=interactable_rect,
            clipped_area=clipped_area,
            original_area=original_area,
            sampled=_sample_points(clipped, interactable_rect),
        )
    if polygon:
        return _result(
            status="FAIL",
            raw=raw,
            canvas_rect=canvas_rect,
            viewport=visible_rect,
            interactable_rect=interactable_rect,
            clipped_area=clipped_area,
            original_area=original_area,
            rejection_reason="no_visible_interactable_geometry",
        )
    if raw is not None:
        return _result(
            status="FAIL",
            raw=raw,
            canvas_rect=canvas_rect,
            viewport=visible_rect,
            interactable_rect=interactable_rect,
            clipped_area=clipped_area,
            original_area=original_area,
            rejection_reason="raw_aimpoint_outside_interactable_region",
        )
    return _result(
        status="FAIL",
        raw=raw,
        canvas_rect=canvas_rect,
        viewport=visible_rect,
        interactable_rect=interactable_rect,
        clipped_area=clipped_area,
        original_area=original_area,
        rejection_reason="no_visible_interactable_geometry",
    )


def _world_location(target: dict[str, Any]) -> dict[str, Any] | None:
    world = _dict(target.get("world") or target.get("worldLocation"))
    if world:
        x = world.get("worldX", world.get("x"))
        y = world.get("worldY", world.get("y"))
        if x is not None and y is not None:
            return {"worldX": x, "worldY": y, "plane": world.get("plane", world.get("z", target.get("plane", 0)))}
    if target.get("worldX") is not None and target.get("worldY") is not None:
        return {"worldX": target.get("worldX"), "worldY": target.get("worldY"), "plane": target.get("plane", 0)}
    return None


def _polygon_is_degenerate(points: list[dict[str, float]]) -> bool:
    if not points:
        return False
    if len(points) < 3:
        return True
    min_x = min(point["x"] for point in points)
    max_x = max(point["x"] for point in points)
    min_y = min(point["y"] for point in points)
    max_y = max(point["y"] for point in points)
    return max_x <= min_x or max_y <= min_y


def _raw_candidate_bounds_value(target: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("clickboxBounds", "convexHullBounds", "bounds", "canvasTileBounds"):
        value = target.get(key)
        if isinstance(value, dict):
            return value
    geometry = _dict(target.get("geometry"))
    if geometry:
        value = _raw_candidate_bounds_value(geometry)
        if value:
            return value
    summary = _dict(target.get("geometrySummary"))
    if summary:
        for key in ("bounds", "aimBounds", "clickboxBounds", "convexHullBounds", "canvasTileBounds"):
            value = summary.get(key)
            if isinstance(value, dict):
                return value
    return None


def resource_projection_status(
    target: dict[str, Any] | None,
    *,
    safe_aimpoint: dict[str, Any] | None = None,
    source_canvas_size: dict[str, Any] | None = None,
    viewport: dict[str, Any] | None = None,
    source_cap_hit: bool | None = None,
    projection_cap_hit: bool | None = None,
    stale_projection: bool | None = None,
) -> dict[str, Any]:
    target = _dict(target)
    safe = _dict(safe_aimpoint) or safe_aimpoint_for_target(target, source_canvas_size=source_canvas_size, viewport=viewport)
    raw = raw_aim_point(target)
    bounds = _candidate_bounds(target)
    raw_bounds = _raw_candidate_bounds_value(target)
    polygon, _polygon_source = _candidate_polygon(target)
    hull_available = bool(polygon)
    hull_valid = bool(polygon and not _polygon_is_degenerate(polygon) and not any(point_is_projection_sentinel(point) for point in polygon))
    projection_sentinel = (
        point_is_projection_sentinel(raw)
        or bounds_is_projection_sentinel(bounds)
        or bounds_is_projection_sentinel(raw_bounds)
        or safe.get("rejectionReason") == "projection_sentinel"
    )
    projection_available = bool(not projection_sentinel and (raw is not None or bounds is not None or hull_valid))
    ratio = safe.get("clippedVisibleAreaRatio")
    edge_clipped = bool(
        not projection_sentinel
        and (
            "centerOffViewport" in (safe.get("unsafeReasons") or [])
            or (isinstance(ratio, (int, float)) and float(ratio) < 1.0)
        )
    )
    offscreen = bool(target.get("onScreen") is False or (safe and safe.get("insideViewport") is False and not projection_sentinel))
    tiny = bool(bounds and (bounds.get("width", 0.0) < 3.0 or bounds.get("height", 0.0) < 3.0) and not projection_sentinel)
    degenerate = bool(_polygon_is_degenerate(polygon) and not projection_sentinel)
    safe_available = safe.get("status") == "PASS"
    safe_reason = str(safe.get("rejectionReason") or "")
    projection_mode = str(target.get("projectionMode") or target.get("projectionSource") or "")
    projection_pending = bool("pending" in projection_mode.lower() and not safe_available)
    source_cap = bool(source_cap_hit) if source_cap_hit is not None else bool(target.get("sourceCapHit"))
    projection_cap = bool(projection_cap_hit) if projection_cap_hit is not None else bool(target.get("projectionCapHit") or target.get("compactLiveGeometryCapHit"))
    stale = bool(stale_projection) if stale_projection is not None else bool(target.get("projectionStale") or target.get("staleProjection"))

    if projection_sentinel:
        classification = "projection_sentinel"
    elif projection_cap and not safe_available:
        classification = "projection_cap_hit"
    elif source_cap and not safe_available:
        classification = "source_cap_hit"
    elif stale and not safe_available:
        classification = "stale_projection"
    elif not projection_available:
        classification = "no_projection"
    elif tiny:
        classification = "tiny_projection"
    elif degenerate:
        classification = "degenerate_projection"
    elif offscreen:
        classification = "offscreen"
    elif edge_clipped:
        classification = "edge_clipped"
    elif safe_available:
        classification = "safe"
    else:
        classification = safe_reason or "no_safe_aimpoint"

    recoverable = {
        "projection_sentinel",
        "no_projection",
        "stale_projection",
        "edge_clipped",
        "offscreen",
        "tiny_projection",
        "degenerate_projection",
        "no_safe_aimpoint",
        "no_visible_interactable_geometry",
        "raw_aimpoint_outside_interactable_region",
    }
    recovery_suggested = classification in recoverable and not source_cap and not projection_cap
    return {
        "schema": RESOURCE_PROJECTION_SCHEMA,
        "candidateName": target.get("targetName") or target.get("name") or target.get("classId"),
        "candidateId": target.get("id", target.get("rawId")),
        "candidateHash": target.get("hash"),
        "worldLocation": _world_location(target),
        "plane": target.get("plane"),
        "projectionAvailable": projection_available,
        "projectionSentinel": projection_sentinel,
        "projectionPending": projection_pending,
        "canvasPoint": dict(raw) if raw is not None else None,
        "canvasBounds": dict(bounds) if bounds is not None else None,
        "hullAvailable": hull_available,
        "hullPolygonValid": hull_valid,
        "safeAimPointAvailable": safe_available,
        "safeAimPointReason": None if safe_available else (safe_reason or "no_safe_aimpoint"),
        "edgeClipped": edge_clipped,
        "offscreen": offscreen,
        "tinyProjection": tiny,
        "degenerateProjection": degenerate,
        "sourceCapHit": source_cap,
        "projectionCapHit": projection_cap,
        "staleProjection": stale,
        "recoverySuggested": recovery_suggested,
        "classification": classification,
    }

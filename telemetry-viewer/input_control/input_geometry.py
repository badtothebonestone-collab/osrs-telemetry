from __future__ import annotations

from typing import Any


SCHEMA = "input_geometry.v1"
DEFAULT_FALLBACK_CANVAS_SIZE = {"width": 765, "height": 503}


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


def _int(value: Any) -> int | None:
    number = _number(value)
    return int(round(number)) if number is not None else None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "available", "showing", "focused"}:
            return True
        if text in {"false", "no", "0", "unavailable", "hidden", "unfocused"}:
            return False
    return None


def _size_from(value: dict[str, Any], width_key: str, height_key: str) -> dict[str, int] | None:
    width = _int(value.get(width_key))
    height = _int(value.get(height_key))
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    return {"width": width, "height": height}


def _origin_from(value: dict[str, Any]) -> dict[str, int] | None:
    if isinstance(value.get("canvasScreenOrigin"), dict):
        origin = _dict(value.get("canvasScreenOrigin"))
        x = _int(origin.get("x"))
        y = _int(origin.get("y"))
    else:
        x = _int(value.get("canvasScreenX"))
        y = _int(value.get("canvasScreenY"))
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def _client_bounds_from(value: dict[str, Any]) -> dict[str, int] | None:
    if isinstance(value.get("clientWindowBounds"), dict):
        bounds = _dict(value.get("clientWindowBounds"))
        x = _int(bounds.get("x"))
        y = _int(bounds.get("y"))
        width = _int(bounds.get("width"))
        height = _int(bounds.get("height"))
    else:
        x = _int(value.get("clientWindowX"))
        y = _int(value.get("clientWindowY"))
        width = _int(value.get("clientWindowWidth"))
        height = _int(value.get("clientWindowHeight"))
    if x is None or y is None or width is None or height is None:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def _display_scale_from(value: dict[str, Any]) -> dict[str, float]:
    if isinstance(value.get("displayScale"), dict):
        scale = _dict(value.get("displayScale"))
        x = _number(scale.get("x"))
        y = _number(scale.get("y"))
    else:
        x = _number(value.get("displayScaleX"))
        y = _number(value.get("displayScaleY"))
    x = x if x is not None and x > 0 else 1.0
    y = y if y is not None and y > 0 else 1.0
    return {"x": x, "y": y}


def normalize_input_geometry(value: Any, *, source_tick: int | None = None) -> dict[str, Any]:
    raw = _dict(value)
    origin = _origin_from(raw)
    canvas_size = raw.get("canvasSize") if isinstance(raw.get("canvasSize"), dict) else None
    if canvas_size:
        canvas_size = _size_from(canvas_size, "width", "height")
    else:
        canvas_size = _size_from(raw, "canvasWidth", "canvasHeight")
    client_bounds = _client_bounds_from(raw)
    source_canvas_size = raw.get("sourceCanvasSize") if isinstance(raw.get("sourceCanvasSize"), dict) else None
    if source_canvas_size:
        source_canvas_size = _size_from(source_canvas_size, "width", "height")
    else:
        source_canvas_size = _size_from(raw, "sourceCanvasWidth", "sourceCanvasHeight")
    display_scale = _display_scale_from(raw)
    explicit_available = _bool(raw.get("inputGeometryAvailable"))
    raw_available = _bool(raw.get("geometryAvailable"))
    available = explicit_available if explicit_available is not None else raw_available
    if available is None:
        available = bool(origin and canvas_size)
    if not origin or not canvas_size:
        available = False
    reason = str(raw.get("reason") or raw.get("inputGeometryReason") or ("available" if available else "geometry_unavailable"))
    tick = source_tick if source_tick is not None else _int(raw.get("sourceTick"))
    return {
        "schema": SCHEMA,
        "status": "PASS" if available else "WARN",
        "inputGeometryAvailable": bool(available),
        "geometryAvailable": bool(available),
        "reason": reason,
        "canvasScreenOrigin": origin,
        "canvasSize": canvas_size,
        "sourceCanvasSize": source_canvas_size,
        "clientWindowBounds": client_bounds,
        "displayScale": display_scale,
        "isCanvasShowing": _bool(raw.get("isCanvasShowing")),
        "isClientFocused": _bool(raw.get("isClientFocused")),
        "sourceTick": tick,
    }


def input_geometry_from_status(status: dict[str, Any]) -> dict[str, Any]:
    status = _dict(status)
    for key in ("inputGeometry", "canvasGeometry"):
        if isinstance(status.get(key), dict):
            return normalize_input_geometry(status.get(key))
    brain = _dict(status.get("brain"))
    for key in ("inputGeometry", "inputGeometryContext", "canvasGeometry"):
        if isinstance(brain.get(key), dict):
            return normalize_input_geometry(brain.get(key))
    baseline = _dict(status.get("baseline"))
    for key in ("inputGeometry", "canvasGeometry"):
        if isinstance(baseline.get(key), dict):
            return normalize_input_geometry(baseline.get(key))
    return normalize_input_geometry({})


def source_canvas_size_from_status(status: dict[str, Any]) -> dict[str, int] | None:
    status = _dict(status)
    brain = _dict(status.get("brain"))
    for viewport in (
        _dict(_dict(status.get("baseline")).get("cameraViewport")),
        _dict(status.get("cameraViewport")),
        _dict(status.get("worldModelCameraViewport")),
        _dict(brain.get("cameraViewport")),
        _dict(brain.get("worldModelCameraViewport")),
    ):
        size = _size_from(viewport, "canvasWidth", "canvasHeight")
        if size:
            return size
    geometry = input_geometry_from_status(status)
    return _dict(geometry.get("sourceCanvasSize")) or None


def resolve_screen_click_point(
    point: dict[str, Any] | None,
    *,
    click_point_space: str = "screen",
    input_geometry: dict[str, Any] | None = None,
    source_canvas_size: dict[str, Any] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    missing: list[str] = []
    if not isinstance(point, dict) or point.get("x") is None or point.get("y") is None:
        return {
            "status": "FAIL",
            "method": "missing_click_point",
            "screenClickPoint": None,
            "warnings": ["no click point available"],
            "missingCapabilities": ["click_point"],
        }
    x = float(point["x"])
    y = float(point["y"])
    if click_point_space != "canvas":
        screen_point = {"x": int(round(x)), "y": int(round(y))}
        return {
            "status": "PASS",
            "method": "screen_direct",
            "screenClickPoint": screen_point,
            "coordinateSpace": "physical_pyautogui",
            "scaleX": 1.0,
            "scaleY": 1.0,
            "screenPointBeforeScaling": dict(screen_point),
            "screenPointAfterScaling": dict(screen_point),
            "windowBoundsSource": "screen_direct",
            "canvasBoundsSource": "none",
            "warnings": [],
            "missingCapabilities": [],
        }

    geometry = normalize_input_geometry(input_geometry or {})
    if not geometry.get("inputGeometryAvailable"):
        warnings.append("dynamic input geometry unavailable; backend fallback required")
        return {
            "status": "WARN",
            "method": "backend_fallback_required",
            "screenClickPoint": None,
            "inputGeometryAvailable": False,
            "warnings": warnings,
            "missingCapabilities": [],
        }

    origin = _dict(geometry.get("canvasScreenOrigin"))
    canvas_size = _dict(geometry.get("canvasSize"))
    display_scale = _dict(geometry.get("displayScale"))
    source_size = _size_from(_dict(source_canvas_size), "width", "height") or _dict(geometry.get("sourceCanvasSize")) or canvas_size
    canvas_width = float(canvas_size.get("width") or 1)
    canvas_height = float(canvas_size.get("height") or 1)
    source_width = float(source_size.get("width") or canvas_width or 1)
    source_height = float(source_size.get("height") or canvas_height or 1)
    scale_x = canvas_width / max(1.0, source_width)
    scale_y = canvas_height / max(1.0, source_height)
    display_scale_x = float(display_scale.get("x") or 1.0)
    display_scale_y = float(display_scale.get("y") or 1.0)
    client_bounds = geometry.get("clientWindowBounds") if isinstance(geometry.get("clientWindowBounds"), dict) else None
    source_matches_canvas = abs(canvas_width - source_width) < 0.5 and abs(canvas_height - source_height) < 0.5
    # On Windows high-DPI VMs, Java/AWT can report window/canvas locations in
    # logical coordinates while HID and DPI-aware cursor APIs move in physical
    # pixels. Component size can still be expanded relative to the source canvas,
    # so client window bounds are the stronger signal that the final screen point
    # must be promoted into physical cursor space.
    logical_screen_scale_applied = bool(
        client_bounds
        and (abs(display_scale_x - 1.0) > 0.01 or abs(display_scale_y - 1.0) > 0.01)
    )
    legacy_delta_scale_applied = bool(not logical_screen_scale_applied and source_matches_canvas and (abs(display_scale_x - 1.0) > 0.01 or abs(display_scale_y - 1.0) > 0.01))
    if legacy_delta_scale_applied:
        scale_x *= display_scale_x
        scale_y *= display_scale_y
    display_scale_applied = logical_screen_scale_applied or legacy_delta_scale_applied
    logical_x = float(origin.get("x") or 0) + x * scale_x
    logical_y = float(origin.get("y") or 0) + y * scale_y
    screen_point_before_scaling = {"x": int(round(logical_x)), "y": int(round(logical_y))}
    screen_point = {
        "x": int(round(logical_x * display_scale_x if logical_screen_scale_applied else logical_x)),
        "y": int(round(logical_y * display_scale_y if logical_screen_scale_applied else logical_y)),
    }
    coordinate_space = "scaled_logical_to_physical" if logical_screen_scale_applied else "physical_pyautogui"
    if x < 0 or y < 0 or x > source_width or y > source_height:
        warnings.append("canvas click point outside source canvas bounds")
        missing.append("screen_click_point")
    max_logical_x = float(origin.get("x") or 0) + source_width * scale_x
    max_logical_y = float(origin.get("y") or 0) + source_height * scale_y
    min_screen_x = float(origin.get("x") or 0) * display_scale_x if logical_screen_scale_applied else float(origin.get("x") or 0)
    min_screen_y = float(origin.get("y") or 0) * display_scale_y if logical_screen_scale_applied else float(origin.get("y") or 0)
    max_screen_x = max_logical_x * display_scale_x if logical_screen_scale_applied else max_logical_x
    max_screen_y = max_logical_y * display_scale_y if logical_screen_scale_applied else max_logical_y
    if (
        screen_point["x"] < int(round(min_screen_x))
        or screen_point["y"] < int(round(min_screen_y))
        or screen_point["x"] > int(round(max_screen_x))
        or screen_point["y"] > int(round(max_screen_y))
    ):
        warnings.append("resolved screen click point outside canvas bounds")
        if "screen_click_point" not in missing:
            missing.append("screen_click_point")
    return {
        "status": "FAIL" if missing else "PASS",
        "method": "dynamic_input_geometry",
        "screenClickPoint": screen_point,
        "inputGeometryAvailable": True,
        "canvasScreenOrigin": geometry.get("canvasScreenOrigin"),
        "canvasSize": geometry.get("canvasSize"),
        "displayScale": geometry.get("displayScale"),
        "sourceCanvasSize": source_size,
        "scale": {"x": scale_x, "y": scale_y},
        "coordinateSpace": coordinate_space,
        "scaleX": scale_x * display_scale_x if logical_screen_scale_applied else scale_x,
        "scaleY": scale_y * display_scale_y if logical_screen_scale_applied else scale_y,
        "screenPointBeforeScaling": screen_point_before_scaling,
        "screenPointAfterScaling": dict(screen_point),
        "windowBoundsSource": "clientWindowBounds" if client_bounds else "canvasScreenOrigin",
        "canvasBoundsSource": "canvasSize/sourceCanvasSize",
        "displayScaleApplied": display_scale_applied,
        "warnings": warnings,
        "missingCapabilities": missing,
    }

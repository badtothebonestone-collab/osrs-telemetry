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
    viewport = _dict(_dict(status.get("baseline")).get("cameraViewport"))
    if not viewport:
        viewport = _dict(status.get("cameraViewport"))
    return _size_from(viewport, "canvasWidth", "canvasHeight")


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
        return {
            "status": "PASS",
            "method": "screen_direct",
            "screenClickPoint": {"x": int(round(x)), "y": int(round(y))},
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
    scale_x = (canvas_width / max(1.0, source_width)) * float(display_scale.get("x") or 1.0)
    scale_y = (canvas_height / max(1.0, source_height)) * float(display_scale.get("y") or 1.0)
    screen_point = {
        "x": int(round(float(origin.get("x") or 0) + x * scale_x)),
        "y": int(round(float(origin.get("y") or 0) + y * scale_y)),
    }
    if x < 0 or y < 0 or x > source_width or y > source_height:
        warnings.append("canvas click point outside source canvas bounds")
        missing.append("screen_click_point")
    max_screen_x = float(origin.get("x") or 0) + canvas_width * float(display_scale.get("x") or 1.0)
    max_screen_y = float(origin.get("y") or 0) + canvas_height * float(display_scale.get("y") or 1.0)
    if (
        screen_point["x"] < int(origin.get("x") or 0)
        or screen_point["y"] < int(origin.get("y") or 0)
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
        "warnings": warnings,
        "missingCapabilities": missing,
    }

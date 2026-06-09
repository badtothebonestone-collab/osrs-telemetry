from __future__ import annotations

import ctypes
import json
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any


SCHEMA = "input_geometry.v1"
DEFAULT_FALLBACK_CANVAS_SIZE = {"width": 765, "height": 503}
CLICK_FAILURE_BUCKETS = {
    "coordinate_transform_error",
    "arduino_movement_error",
    "target_aimpoint_error",
    "game_state_stale",
}
COORDINATE_RESOLVER = "input_geometry.resolve_screen_click_point"


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


def _rect_xywh(x: Any, y: Any, width: Any, height: Any) -> dict[str, int] | None:
    rect_x = _int(x)
    rect_y = _int(y)
    rect_width = _int(width)
    rect_height = _int(height)
    if rect_x is None or rect_y is None or rect_width is None or rect_height is None:
        return None
    if rect_width <= 0 or rect_height <= 0:
        return None
    return {
        "x": rect_x,
        "y": rect_y,
        "width": rect_width,
        "height": rect_height,
        "left": rect_x,
        "top": rect_y,
        "right": rect_x + rect_width,
        "bottom": rect_y + rect_height,
    }


def _rect_ltrb(left: Any, top: Any, right: Any, bottom: Any) -> dict[str, int] | None:
    rect_left = _int(left)
    rect_top = _int(top)
    rect_right = _int(right)
    rect_bottom = _int(bottom)
    if rect_left is None or rect_top is None or rect_right is None or rect_bottom is None:
        return None
    return _rect_xywh(rect_left, rect_top, rect_right - rect_left, rect_bottom - rect_top)


def _canvas_rect(origin: dict[str, Any] | None, size: dict[str, Any] | None) -> dict[str, int] | None:
    origin = _dict(origin)
    size = _dict(size)
    return _rect_xywh(origin.get("x"), origin.get("y"), size.get("width"), size.get("height"))


def _viewport_rect_from(value: dict[str, Any]) -> dict[str, int] | None:
    viewport = _dict(value.get("viewportRect") or value.get("cameraViewport"))
    if viewport:
        rect = _rect_xywh(
            viewport.get("x", viewport.get("viewportXOffset", 0)),
            viewport.get("y", viewport.get("viewportYOffset", 0)),
            viewport.get("width", viewport.get("viewportWidth")),
            viewport.get("height", viewport.get("viewportHeight")),
        )
        if rect:
            return rect
    width = _int(value.get("viewportWidth"))
    height = _int(value.get("viewportHeight"))
    if width is not None and height is not None:
        return _rect_xywh(value.get("viewportXOffset", 0), value.get("viewportYOffset", 0), width, height)
    return None


def _user32() -> Any | None:
    if sys.platform != "win32":
        return None
    try:
        return ctypes.windll.user32  # type: ignore[attr-defined]
    except Exception:
        return None


def _window_text(user32: Any, hwnd: int) -> str:
    try:
        length = int(user32.GetWindowTextLengthW(hwnd))
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value
    except Exception:
        return ""


def _win_rects(user32: Any, hwnd: int) -> dict[str, Any]:
    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    rect = RECT()
    client = RECT()
    origin = POINT(0, 0)
    window_rect = None
    client_rect = None
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        window_rect = _rect_ltrb(rect.left, rect.top, rect.right, rect.bottom)
    if user32.GetClientRect(hwnd, ctypes.byref(client)) and user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        client_rect = _rect_xywh(origin.x, origin.y, client.right - client.left, client.bottom - client.top)
    screen_to_client_available = False
    client_to_screen_available = False
    round_trip = None
    try:
        probe = POINT(10, 10)
        client_to_screen_available = bool(user32.ClientToScreen(hwnd, ctypes.byref(probe)))
        if client_to_screen_available:
            screen = {"x": int(probe.x), "y": int(probe.y)}
            screen_to_client_available = bool(user32.ScreenToClient(hwnd, ctypes.byref(probe)))
            round_trip = {
                "clientPoint": {"x": 10, "y": 10},
                "screenPoint": screen,
                "roundTripClientPoint": {"x": int(probe.x), "y": int(probe.y)} if screen_to_client_available else None,
            }
    except Exception:
        pass
    dpi = None
    try:
        get_dpi = getattr(user32, "GetDpiForWindow")
        dpi = int(get_dpi(hwnd))
    except Exception:
        dpi = None
    return {
        "windowRect": window_rect,
        "clientRect": client_rect,
        "screenToClientAvailable": screen_to_client_available,
        "clientToScreenAvailable": client_to_screen_available,
        "screenClientRoundTrip": round_trip,
        "dpi": dpi,
        "dpiScale": round(float(dpi) / 96.0, 4) if isinstance(dpi, int) and dpi > 0 else None,
    }


def find_runelite_window(title_filter: str = "RuneLite") -> dict[str, Any]:
    user32 = _user32()
    if user32 is None:
        return {
            "schema": "runelite_window_status.v1",
            "status": "WARN",
            "available": False,
            "runeliteWindowMatched": False,
            "reason": "win32_unavailable",
        }
    title_filter_lower = str(title_filter or "RuneLite").lower()
    matches: list[dict[str, Any]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd: int, _lparam: int) -> bool:
        try:
            title = _window_text(user32, int(hwnd))
            if not title:
                return True
            lowered = title.lower()
            if title_filter_lower not in lowered and "old school" not in lowered:
                return True
            visible = bool(user32.IsWindowVisible(hwnd))
            minimized = bool(user32.IsIconic(hwnd))
            payload = {
                "hwnd": int(hwnd),
                "title": title,
                "visible": visible,
                "minimized": minimized,
                **_win_rects(user32, int(hwnd)),
            }
            matches.append(payload)
        except Exception:
            return True
        return True

    try:
        user32.EnumWindows(enum_proc, 0)
    except Exception as error:  # noqa: BLE001
        return {
            "schema": "runelite_window_status.v1",
            "status": "FAIL",
            "available": False,
            "runeliteWindowMatched": False,
            "reason": f"enum_windows_failed:{type(error).__name__}",
        }
    foreground_hwnd = int(user32.GetForegroundWindow() or 0)
    foreground_title = _window_text(user32, foreground_hwnd) if foreground_hwnd else ""
    best = next((item for item in matches if item.get("visible") and not item.get("minimized")), matches[0] if matches else None)
    if best:
        best = dict(best)
        best["foreground"] = bool(best.get("hwnd") == foreground_hwnd)
    return {
        "schema": "runelite_window_status.v1",
        "status": "PASS" if best and best.get("visible") and not best.get("minimized") else "WARN" if best else "FAIL",
        "available": bool(best),
        "runeliteWindowMatched": bool(best),
        "foregroundWindowTitle": foreground_title,
        "foregroundHwnd": foreground_hwnd or None,
        "matchedWindow": best,
        "matchedWindowCount": len(matches),
        "warnings": [] if best else [f"no window title matched {title_filter!r}"],
    }


def repair_runelite_focus(title_filter: str = "RuneLite", *, sleep_seconds: float = 0.25) -> dict[str, Any]:
    before = find_runelite_window(title_filter)
    matched = _dict(before.get("matchedWindow"))
    hwnd = _int(matched.get("hwnd"))
    result = {
        "schema": "runelite_focus_repair.v1",
        "focusRepairAttempted": bool(hwnd),
        "focusRepairSucceeded": False,
        "windowRestoreAttempted": False,
        "windowRestoreSucceeded": False,
        "runeliteWindowMatched": bool(hwnd),
        "before": before,
        "after": None,
        "warnings": [],
    }
    if hwnd is None:
        result["warnings"].append("RuneLite window was not found")
        return result
    user32 = _user32()
    if user32 is None:
        result["warnings"].append("Win32 user32 unavailable")
        return result
    if matched.get("minimized"):
        result["windowRestoreAttempted"] = True
        try:
            result["windowRestoreSucceeded"] = bool(user32.ShowWindow(hwnd, 9))
        except Exception as error:  # noqa: BLE001
            result["warnings"].append(f"window restore failed: {type(error).__name__}: {error}")
    else:
        try:
            user32.ShowWindow(hwnd, 5)
        except Exception:
            pass
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    except Exception as error:  # noqa: BLE001
        result["warnings"].append(f"foreground focus failed: {type(error).__name__}: {error}")
    if sleep_seconds > 0:
        time.sleep(float(sleep_seconds))
    after = find_runelite_window(title_filter)
    result["after"] = after
    result["focusRepairSucceeded"] = bool(_dict(after.get("matchedWindow")).get("foreground"))
    result["foregroundWindowTitle"] = after.get("foregroundWindowTitle")
    result["runeliteWindowMatched"] = bool(after.get("runeliteWindowMatched"))
    return result


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
    canvas_rect = _canvas_rect(origin, canvas_size)
    client_rect = client_bounds
    viewport_rect = _viewport_rect_from(raw)
    blockers: list[str] = []
    warnings: list[str] = [str(item) for item in raw.get("warnings") or [] if item is not None] if isinstance(raw.get("warnings"), list) else []
    if not origin:
        blockers.append("canvas_origin_missing")
    if not canvas_size:
        blockers.append("input_geometry_canvas_missing")
    if client_bounds is None:
        warnings.append("client rect unavailable from telemetry")
    if available and (canvas_rect is None or canvas_rect["width"] <= 0 or canvas_rect["height"] <= 0):
        available = False
        blockers.append("input_geometry_canvas_missing")
    screen_to_client = bool(origin and canvas_size)
    client_to_screen = bool(origin and canvas_size)
    screen_to_canvas_transform = None
    canvas_to_screen_transform = None
    if origin and canvas_size:
        screen_to_canvas_transform = {
            "method": "subtract_canvas_screen_origin",
            "origin": dict(origin),
            "canvasSize": dict(canvas_size),
        }
        canvas_to_screen_transform = {
            "method": "add_canvas_screen_origin",
            "origin": dict(origin),
            "canvasSize": dict(canvas_size),
        }
    return {
        "schema": SCHEMA,
        "status": "PASS" if available else "FAIL",
        "inputGeometryAvailable": bool(available),
        "geometryAvailable": bool(available),
        "reason": reason,
        "canvasScreenOrigin": origin,
        "canvasSize": canvas_size,
        "canvasWidth": canvas_size.get("width") if canvas_size else None,
        "canvasHeight": canvas_size.get("height") if canvas_size else None,
        "canvasRect": canvas_rect,
        "sourceCanvasSize": source_canvas_size,
        "clientWindowBounds": client_bounds,
        "clientRect": client_rect,
        "viewportRect": viewport_rect,
        "displayScale": display_scale,
        "dpiScale": display_scale,
        "screenToClientAvailable": screen_to_client,
        "clientToScreenAvailable": client_to_screen,
        "screenToCanvasTransform": screen_to_canvas_transform,
        "canvasToScreenTransform": canvas_to_screen_transform,
        "blockers": blockers,
        "warnings": list(dict.fromkeys(warnings)),
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


def _latest_session_path(sessions_dir: str | Path | None = None) -> Path | None:
    root = Path(sessions_dir).expanduser() if sessions_dir else Path.home() / ".osrs-telemetry" / "sessions"
    try:
        candidates = [path for path in root.iterdir() if path.is_dir()]
    except OSError:
        return None
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)[0] if candidates else None


def _baseline_geometry_source(session: str | Path | None, *, now: float | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if session is None:
        return {}, {}
    session_path = Path(session).expanduser()
    live_dir = session_path / "interaction_geometry" / "live"
    if not live_dir.exists():
        live_dir = session_path
    baseline_path = live_dir / "live_baseline_state.json"
    try:
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {"source": "file_session.baseline", "path": str(baseline_path), "readable": False}
    if not isinstance(payload, dict):
        return {}, {"source": "file_session.baseline", "path": str(baseline_path), "readable": False}
    age_ms = None
    try:
        age_ms = int(max(0.0, ((time.time() if now is None else now) - baseline_path.stat().st_mtime) * 1000.0))
    except OSError:
        pass
    geometry = payload.get("inputGeometry") if isinstance(payload.get("inputGeometry"), dict) else {}
    metadata = {
        "source": "file_session.baseline.inputGeometry",
        "path": str(baseline_path),
        "readable": True,
        "geometryFreshnessMs": age_ms,
        "latestTick": payload.get("latestTick"),
        "gameState": payload.get("gameState"),
        "player": payload.get("player") if isinstance(payload.get("player"), dict) else None,
        "cameraViewport": payload.get("cameraViewport") if isinstance(payload.get("cameraViewport"), dict) else None,
    }
    if isinstance(payload.get("cameraViewport"), dict) and isinstance(geometry, dict):
        geometry = dict(geometry)
        geometry.setdefault("cameraViewport", payload.get("cameraViewport"))
    return geometry if isinstance(geometry, dict) else {}, metadata


def _window_fallback_geometry(window_status: dict[str, Any]) -> dict[str, Any]:
    matched = _dict(window_status.get("matchedWindow"))
    client_rect = _dict(matched.get("clientRect"))
    if not client_rect:
        return {}
    scale = matched.get("dpiScale")
    return {
        "geometryAvailable": True,
        "reason": "window_client_geometry_fallback",
        "canvasScreenX": client_rect.get("x"),
        "canvasScreenY": client_rect.get("y"),
        "canvasWidth": client_rect.get("width"),
        "canvasHeight": client_rect.get("height"),
        "clientWindowX": client_rect.get("x"),
        "clientWindowY": client_rect.get("y"),
        "clientWindowWidth": client_rect.get("width"),
        "clientWindowHeight": client_rect.get("height"),
        "displayScaleX": scale or 1.0,
        "displayScaleY": scale or 1.0,
        "isCanvasShowing": bool(matched.get("visible") and not matched.get("minimized")),
        "isClientFocused": bool(matched.get("foreground")),
    }


def resolve_input_geometry_status(
    status: dict[str, Any] | None = None,
    *,
    session: str | Path | None = None,
    sessions_dir: str | Path | None = None,
    allow_focus_repair: bool = False,
    title_filter: str = "RuneLite",
    max_age_ms: int = 5000,
    now: float | None = None,
) -> dict[str, Any]:
    status = _dict(status)
    session_path = Path(session).expanduser() if session is not None else _latest_session_path(sessions_dir)
    sources: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    if isinstance(status.get("inputGeometry"), dict):
        sources.append(("daemon_status.inputGeometry", _dict(status.get("inputGeometry")), {"source": "daemon_status.inputGeometry"}))
    baseline = _dict(status.get("baseline"))
    if isinstance(baseline.get("inputGeometry"), dict):
        raw = dict(_dict(baseline.get("inputGeometry")))
        if isinstance(baseline.get("cameraViewport"), dict):
            raw.setdefault("cameraViewport", baseline.get("cameraViewport"))
        sources.append(("daemon_status.baseline.inputGeometry", raw, {"source": "daemon_status.baseline.inputGeometry"}))
    file_geometry, file_metadata = _baseline_geometry_source(session_path, now=now)
    if file_geometry:
        sources.append((str(file_metadata.get("source") or "file_session.baseline.inputGeometry"), file_geometry, file_metadata))
    window_status = find_runelite_window(title_filter)

    focus_repair = None
    matched_window = _dict(window_status.get("matchedWindow"))
    focus_needed = bool(matched_window and (matched_window.get("minimized") or not matched_window.get("foreground")))
    if allow_focus_repair and focus_needed:
        focus_repair = repair_runelite_focus(title_filter)
        after = _dict(focus_repair.get("after"))
        if after:
            window_status = after
            matched_window = _dict(window_status.get("matchedWindow"))
    window_geometry = _window_fallback_geometry(window_status)
    if window_geometry:
        sources.append(("win32.window_client_geometry", window_geometry, {"source": "win32.window_client_geometry"}))

    selected = normalize_input_geometry({})
    selected_source = "none"
    selected_metadata: dict[str, Any] = {}
    for source_name, raw, metadata in sources:
        geometry = normalize_input_geometry(raw)
        if geometry.get("inputGeometryAvailable"):
            selected = geometry
            selected_source = source_name
            selected_metadata = metadata
            break
        if selected_source == "none":
            selected = geometry
            selected_source = source_name
            selected_metadata = metadata

    selected = dict(selected)
    selected["source"] = selected_source
    if selected_metadata:
        selected["sourceMetadata"] = selected_metadata
    if selected_metadata.get("geometryFreshnessMs") is not None:
        selected["geometryFreshnessMs"] = selected_metadata.get("geometryFreshnessMs")
    selected["runeliteWindowTitle"] = matched_window.get("title")
    selected["hwnd"] = matched_window.get("hwnd")
    selected["windowRect"] = matched_window.get("windowRect")
    selected["foregroundWindowTitle"] = window_status.get("foregroundWindowTitle")
    selected["runeliteWindowMatched"] = bool(window_status.get("runeliteWindowMatched"))
    selected["foreground"] = matched_window.get("foreground")
    selected["minimized"] = matched_window.get("minimized")
    selected["visible"] = matched_window.get("visible")
    if matched_window.get("foreground") is not None:
        selected["isClientFocused"] = bool(matched_window.get("foreground"))
    if matched_window.get("visible") is not None or matched_window.get("minimized") is not None:
        selected["isCanvasShowing"] = bool(matched_window.get("visible") and not matched_window.get("minimized"))
    selected["focusRepair"] = focus_repair
    selected["focusRepairAttempted"] = bool(focus_repair and focus_repair.get("focusRepairAttempted"))
    selected["focusRepairSucceeded"] = bool(focus_repair and focus_repair.get("focusRepairSucceeded"))
    selected["windowRestoreAttempted"] = bool(focus_repair and focus_repair.get("windowRestoreAttempted"))
    selected["windowRestoreSucceeded"] = bool(focus_repair and focus_repair.get("windowRestoreSucceeded"))
    if matched_window.get("screenToClientAvailable") is not None:
        selected["screenToClientAvailable"] = bool(matched_window.get("screenToClientAvailable"))
    if matched_window.get("clientToScreenAvailable") is not None:
        selected["clientToScreenAvailable"] = bool(matched_window.get("clientToScreenAvailable"))
    if matched_window.get("screenClientRoundTrip") is not None:
        selected["screenClientRoundTrip"] = matched_window.get("screenClientRoundTrip")
    warnings = [str(item) for item in selected.get("warnings") or [] if item is not None]
    blockers = [str(item) for item in selected.get("blockers") or [] if item is not None]
    age = selected.get("geometryFreshnessMs")
    if isinstance(age, (int, float)) and int(age) > int(max_age_ms):
        blockers.append("input_geometry_stale")
        selected["reason"] = "geometry_stale"
    if allow_focus_repair and matched_window and matched_window.get("minimized"):
        blockers.append("input_geometry_focus_needed")
    if allow_focus_repair and matched_window and not matched_window.get("foreground"):
        blockers.append("input_geometry_focus_needed")
    if selected.get("isClientFocused") is False:
        blockers.append("input_geometry_focus_needed")
    if not selected.get("inputGeometryAvailable"):
        if "input_geometry_canvas_missing" not in blockers and "canvas_origin_missing" not in blockers:
            blockers.append("input_geometry_unavailable")
    if selected.get("inputGeometryAvailable") and not selected.get("screenToClientAvailable"):
        blockers.append("input_geometry_transform_missing")
    if selected.get("inputGeometryAvailable") and not selected.get("clientToScreenAvailable"):
        blockers.append("input_geometry_transform_missing")
    blockers = list(dict.fromkeys(blockers))
    if focus_repair:
        warnings.extend(str(item) for item in focus_repair.get("warnings") or [])
    selected["blockers"] = blockers
    selected["warnings"] = list(dict.fromkeys(warnings))
    selected["blockerCode"] = blockers[0] if blockers else "input_geometry_pass"
    selected["inputGeometryAvailable"] = bool(selected.get("inputGeometryAvailable") and not blockers)
    selected["geometryAvailable"] = bool(selected["inputGeometryAvailable"])
    selected["status"] = "PASS" if selected["inputGeometryAvailable"] else "FAIL"
    return selected


def validate_screen_point_inside_geometry(
    screen_point: dict[str, Any] | None,
    input_geometry: dict[str, Any] | None,
    *,
    max_age_ms: int = 5000,
) -> dict[str, Any]:
    geometry = normalize_input_geometry(input_geometry or {})
    age = geometry.get("geometryFreshnessMs")
    if isinstance(age, (int, float)) and int(age) > int(max_age_ms):
        geometry = dict(geometry)
        geometry["status"] = "FAIL"
        geometry["inputGeometryAvailable"] = False
        geometry["blockerCode"] = "input_geometry_stale"
        geometry["blockers"] = list(dict.fromkeys([*(geometry.get("blockers") or []), "input_geometry_stale"]))
    if geometry.get("status") != "PASS":
        blockers = [str(item) for item in geometry.get("blockers") or [] if item is not None]
        reason = str(geometry.get("blockerCode") or (blockers[0] if blockers else "input_geometry_invalid"))
        if reason == "input_geometry_stale":
            blocker = "geometry_stale"
        elif reason in {"input_geometry_unavailable", "input_geometry_canvas_missing", "canvas_origin_missing"}:
            blocker = "input_geometry_invalid"
        else:
            blocker = reason
        return {
            "schema": "input_geometry_point_validation.v1",
            "status": "FAIL",
            "reason": blocker,
            "inputGeometryStatus": geometry,
            "targetScreenPoint": screen_point,
            "canvasRect": geometry.get("canvasRect"),
            "clientRect": geometry.get("clientRect"),
        }
    if not isinstance(screen_point, dict):
        return {
            "schema": "input_geometry_point_validation.v1",
            "status": "FAIL",
            "reason": "screen_click_point_unavailable",
            "inputGeometryStatus": geometry,
            "targetScreenPoint": screen_point,
            "canvasRect": geometry.get("canvasRect"),
            "clientRect": geometry.get("clientRect"),
        }
    x = _int(screen_point.get("x"))
    y = _int(screen_point.get("y"))
    rect = _dict(geometry.get("canvasRect"))
    if x is None or y is None or not rect:
        return {
            "schema": "input_geometry_point_validation.v1",
            "status": "FAIL",
            "reason": "input_geometry_invalid",
            "inputGeometryStatus": geometry,
            "targetScreenPoint": screen_point,
            "canvasRect": geometry.get("canvasRect"),
            "clientRect": geometry.get("clientRect"),
        }
    inside = bool(rect.get("left") <= x <= rect.get("right") and rect.get("top") <= y <= rect.get("bottom"))
    return {
        "schema": "input_geometry_point_validation.v1",
        "status": "PASS" if inside else "FAIL",
        "reason": "inside_canvas" if inside else "planned_point_outside_canvas",
        "inputGeometryStatus": geometry,
        "targetScreenPoint": {"x": x, "y": y},
        "canvasRect": geometry.get("canvasRect"),
        "clientRect": geometry.get("clientRect"),
    }


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
            "coordinateResolver": COORDINATE_RESOLVER,
            "clickFailureBucket": "target_aimpoint_error",
            "screenClickPoint": None,
            "displayScaleApplied": False,
            "displayScaleReason": "click_point_missing",
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
            "coordinateResolver": COORDINATE_RESOLVER,
            "screenClickPoint": screen_point,
            "coordinateSpace": "physical_pyautogui",
            "scaleX": 1.0,
            "scaleY": 1.0,
            "screenPointBeforeScaling": dict(screen_point),
            "screenPointAfterScaling": dict(screen_point),
            "windowBoundsSource": "screen_direct",
            "canvasBoundsSource": "none",
            "displayScaleApplied": False,
            "displayScaleReason": "screen_direct_already_physical",
            "warnings": [],
            "missingCapabilities": [],
        }

    geometry = normalize_input_geometry(input_geometry or {})
    if not geometry.get("inputGeometryAvailable"):
        warnings.append("dynamic input geometry unavailable; backend fallback required")
        return {
            "status": "WARN",
            "method": "backend_fallback_required",
            "coordinateResolver": COORDINATE_RESOLVER,
            "screenClickPoint": None,
            "inputGeometryAvailable": False,
            "displayScaleApplied": False,
            "displayScaleReason": "dynamic_input_geometry_unavailable",
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
    # pixels. If the canvas has already been expanded from the source canvas,
    # though, the reported canvas dimensions are already in the cursor space we
    # should click in. Applying display scale again double-scales the target.
    logical_screen_scale_applied = bool(
        client_bounds
        and source_matches_canvas
        and (abs(display_scale_x - 1.0) > 0.01 or abs(display_scale_y - 1.0) > 0.01)
    )
    legacy_delta_scale_applied = bool(not logical_screen_scale_applied and source_matches_canvas and (abs(display_scale_x - 1.0) > 0.01 or abs(display_scale_y - 1.0) > 0.01))
    if legacy_delta_scale_applied:
        scale_x *= display_scale_x
        scale_y *= display_scale_y
    display_scale_applied = logical_screen_scale_applied or legacy_delta_scale_applied
    if logical_screen_scale_applied:
        display_scale_reason = "client_window_bounds_logical_scaled_to_physical"
    elif legacy_delta_scale_applied:
        display_scale_reason = "source_canvas_matches_current_canvas_display_scale_applied_to_delta"
    elif source_matches_canvas:
        display_scale_reason = "canvas_coordinates_already_physical"
    elif abs(display_scale_x - 1.0) > 0.01 or abs(display_scale_y - 1.0) > 0.01:
        display_scale_reason = "source_canvas_expanded_to_physical_canvas_no_display_rescale"
    else:
        display_scale_reason = "display_scale_identity"
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
    result = {
        "status": "FAIL" if missing else "PASS",
        "method": "dynamic_input_geometry",
        "coordinateResolver": COORDINATE_RESOLVER,
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
        "displayScaleReason": display_scale_reason,
        "warnings": warnings,
        "missingCapabilities": missing,
    }
    if missing:
        result["clickFailureBucket"] = "coordinate_transform_error"
    return result

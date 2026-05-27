from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


WINDOW_TITLE_HINTS = ["RuneLite", "RuneLite Launcher", "Jagex Launcher", "Java", "Old School RuneScape"]
_DPI_AWARENESS_ATTEMPTED = False


def enable_windows_dpi_awareness() -> None:
    """Keep pygetwindow bounds in the same physical pixel space as pyautogui."""
    global _DPI_AWARENESS_ATTEMPTED
    if _DPI_AWARENESS_ATTEMPTED:
        return
    _DPI_AWARENESS_ATTEMPTED = True
    try:
        import ctypes

        awareness_context_per_monitor_v2 = ctypes.c_void_p(-4 & ((1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1))
        set_context = getattr(ctypes.windll.user32, "SetProcessDpiAwarenessContext", None)
        if set_context and set_context(awareness_context_per_monitor_v2):
            return
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


@dataclass(frozen=True)
class MonitorBounds:
    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


def unique_title_filters(window_title_filter: str | None) -> list[str]:
    values = [window_title_filter or ""] + WINDOW_TITLE_HINTS
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def window_bounds(window: Any) -> dict[str, int]:
    return {
        "x": int(getattr(window, "left", 0)),
        "y": int(getattr(window, "top", 0)),
        "width": int(getattr(window, "width", 0)),
        "height": int(getattr(window, "height", 0)),
    }


def physical_window_bounds_from_handle(handle: int | None) -> dict[str, int] | None:
    if not handle:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        enable_windows_dpi_awareness()

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        rect = RECT()
        dwmapi = getattr(ctypes.windll, "dwmapi", None)
        if dwmapi is not None:
            # DWMWA_EXTENDED_FRAME_BOUNDS gives the visible window frame in
            # desktop pixels and avoids the DPI virtualization that pygetwindow
            # can expose in high-DPI VMware guests.
            if dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(handle),
                wintypes.DWORD(9),
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            ) == 0:
                width = int(rect.right - rect.left)
                height = int(rect.bottom - rect.top)
                if width > 0 and height > 0:
                    return {"x": int(rect.left), "y": int(rect.top), "width": width, "height": height}
        if ctypes.windll.user32.GetWindowRect(wintypes.HWND(handle), ctypes.byref(rect)):
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width > 0 and height > 0:
                return {"x": int(rect.left), "y": int(rect.top), "width": width, "height": height}
    except Exception:  # noqa: BLE001
        return None
    return None


def coordinate_scale(logical: dict[str, int] | None, physical: dict[str, int] | None) -> dict[str, float | bool]:
    if not logical or not physical:
        return {"x": 1.0, "y": 1.0, "applied": False}
    logical_width = max(1, int(logical.get("width") or 1))
    logical_height = max(1, int(logical.get("height") or 1))
    physical_width = max(1, int(physical.get("width") or 1))
    physical_height = max(1, int(physical.get("height") or 1))
    scale_x = float(physical_width) / float(logical_width)
    scale_y = float(physical_height) / float(logical_height)
    return {
        "x": scale_x,
        "y": scale_y,
        "applied": abs(scale_x - 1.0) > 0.01 or abs(scale_y - 1.0) > 0.01,
    }


def enrich_window_geometry(window_info: dict[str, Any], handle: int | None = None) -> dict[str, Any]:
    output = dict(window_info)
    logical = output.get("windowBounds") if isinstance(output.get("windowBounds"), dict) else None
    physical = physical_window_bounds_from_handle(handle or output.get("windowHandle"))
    if logical and "logicalWindowBounds" not in output:
        output["logicalWindowBounds"] = dict(logical)
    if physical:
        output["physicalWindowBounds"] = physical
        output["coordinateScale"] = coordinate_scale(logical, physical)
        output["windowBoundsSource"] = "dwm_extended_frame_physical"
    elif logical:
        output["physicalWindowBounds"] = dict(logical)
        output["coordinateScale"] = {"x": 1.0, "y": 1.0, "applied": False}
        output["windowBoundsSource"] = "pygetwindow_bounds"
    return output


def window_title_from_handle(handle: int | None) -> str:
    if not handle:
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        length = ctypes.windll.user32.GetWindowTextLengthW(wintypes.HWND(handle))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(wintypes.HWND(handle), buffer, length + 1)
        return str(buffer.value or "")
    except Exception:  # noqa: BLE001
        return ""


def foreground_window_title() -> str:
    try:
        import ctypes
        from ctypes import wintypes

        handle = ctypes.windll.user32.GetForegroundWindow()
        return window_title_from_handle(int(handle)) if handle else ""
    except Exception:  # noqa: BLE001
        return ""


def focus_window_handle(handle: int | None) -> dict[str, Any]:
    if not handle:
        return {"focused": False, "focusMethod": "win32_no_handle", "foregroundTitle": foreground_window_title(), "warnings": ["window focus skipped: no handle"]}
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = wintypes.HWND(handle)
        user32 = ctypes.windll.user32
        try:
            user32.AllowSetForegroundWindow(wintypes.DWORD(0xFFFFFFFF))
        except Exception:  # noqa: BLE001
            pass
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        hwnd_topmost = wintypes.HWND(-1)
        hwnd_notopmost = wintypes.HWND(-2)
        flags = 0x0001 | 0x0002 | 0x0040  # NOSIZE | NOMOVE | SHOWWINDOW
        user32.SetWindowPos(hwnd, hwnd_topmost, 0, 0, 0, 0, flags)
        user32.SetWindowPos(hwnd, hwnd_notopmost, 0, 0, 0, 0, flags)
        foreground = user32.GetForegroundWindow()
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        attached: list[int] = []
        for thread_id in {int(foreground_thread or 0), int(target_thread or 0)}:
            if thread_id and thread_id != int(current_thread):
                if user32.AttachThreadInput(wintypes.DWORD(current_thread), wintypes.DWORD(thread_id), True):
                    attached.append(thread_id)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
        for thread_id in attached:
            user32.AttachThreadInput(wintypes.DWORD(current_thread), wintypes.DWORD(thread_id), False)
        foreground_title = foreground_window_title()
        target_title = window_title_from_handle(handle)
        focused = bool(target_title and (foreground_title == target_title or target_title.lower() in foreground_title.lower()))
        return {
            "focused": focused,
            "focusMethod": "win32_set_foreground",
            "foregroundTitle": foreground_title,
            "warnings": [] if focused else [f"window focus not confirmed; foreground={foreground_title!r}"],
        }
    except Exception as error:  # noqa: BLE001
        return {
            "focused": False,
            "focusMethod": "win32_set_foreground",
            "foregroundTitle": foreground_window_title(),
            "warnings": [f"window focus failed: {type(error).__name__}: {error}"],
        }


def _load_windows() -> list[Any]:
    try:
        enable_windows_dpi_awareness()
        import pygetwindow  # type: ignore
    except ImportError as error:
        raise RuntimeError("pygetwindow unavailable; install with: pip install pygetwindow") from error
    return list(pygetwindow.getAllWindows())


def _load_monitors() -> list[MonitorBounds]:
    try:
        import ctypes
        from ctypes import wintypes

        monitors_with_primary: list[tuple[bool, MonitorBounds]] = []

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)

        def callback(monitor, _hdc, _rect, _data):  # type: ignore[no-untyped-def]
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                monitors_with_primary.append(
                    (
                        bool(info.dwFlags & 1),
                        MonitorBounds(
                            int(info.rcMonitor.left),
                            int(info.rcMonitor.top),
                            int(info.rcMonitor.right - info.rcMonitor.left),
                            int(info.rcMonitor.bottom - info.rcMonitor.top),
                        ),
                    )
                )
            return True

        ctypes.windll.user32.EnumDisplayMonitors(0, 0, callback_type(callback), 0)
        if monitors_with_primary:
            monitors_with_primary.sort(key=lambda item: (not item[0], item[1].x, item[1].y))
            return [monitor for _primary, monitor in monitors_with_primary]
    except Exception:  # noqa: BLE001
        pass
    try:
        from screeninfo import get_monitors  # type: ignore

        monitors = [
            MonitorBounds(int(monitor.x), int(monitor.y), int(monitor.width), int(monitor.height))
            for monitor in get_monitors()
            if int(monitor.width) > 0 and int(monitor.height) > 0
        ]
        if monitors:
            return monitors
    except Exception:  # noqa: BLE001
        pass
    try:
        import pyautogui  # type: ignore

        size = pyautogui.size()
        return [MonitorBounds(0, 0, int(size.width), int(size.height))]
    except Exception:  # noqa: BLE001
        return []


def _matching_window(windows: list[Any], filters: list[str]) -> Any | None:
    lowered = [item.lower() for item in filters]
    for window in windows:
        title = str(getattr(window, "title", "") or "")
        if any(hint in title.lower() for hint in lowered):
            return window
    return None


def _target_bounds(
    original: dict[str, int],
    monitors: list[MonitorBounds],
    *,
    monitor_index: int,
    window_x: int | None,
    window_y: int | None,
    window_width: int | None,
    window_height: int | None,
) -> tuple[dict[str, int] | None, int | None]:
    if window_x is not None or window_y is not None:
        return {
            "x": int(window_x if window_x is not None else original["x"]),
            "y": int(window_y if window_y is not None else original["y"]),
            "width": max(1, int(window_width if window_width is not None else original["width"])),
            "height": max(1, int(window_height if window_height is not None else original["height"])),
        }, None
    if not monitors or monitor_index < 0 or monitor_index >= len(monitors):
        return None, monitor_index
    monitor = monitors[monitor_index]
    margin = 80
    return {
        "x": monitor.x + margin,
        "y": monitor.y + margin,
        "width": max(1, int(window_width if window_width is not None else original["width"])),
        "height": max(1, int(window_height if window_height is not None else original["height"])),
    }, monitor_index


def _win_shift_fallback(hotkey_func: Callable[..., Any] | None) -> bool:
    if hotkey_func is None:
        try:
            import pyautogui  # type: ignore

            hotkey_func = pyautogui.hotkey
        except Exception:  # noqa: BLE001
            return False
    hotkey_func("win", "shift", "right")
    return True


def find_and_prepare_window(
    filters: list[str],
    *,
    move_to_secondary: bool = False,
    monitor_index: int = 1,
    window_x: int | None = None,
    window_y: int | None = None,
    window_width: int | None = None,
    window_height: int | None = None,
    fallback_win_shift_arrow: bool = False,
    execute: bool = False,
    window_provider: Callable[[], list[Any]] | None = None,
    monitor_provider: Callable[[], list[MonitorBounds]] | None = None,
    hotkey_func: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    try:
        windows = list(window_provider() if window_provider is not None else _load_windows())
    except Exception as error:  # noqa: BLE001
        return {
            "matchedWindowTitle": None,
            "windowBounds": None,
            "originalWindowBounds": None,
            "finalWindowBounds": None,
            "focused": False,
            "focusMethod": "unavailable",
            "placement": {"status": "WARN", "method": "unavailable", "monitorTarget": monitor_index},
            "warnings": [f"window enumeration failed: {type(error).__name__}: {error}"],
        }
    window = _matching_window(windows, filters)
    if window is None:
        return {
            "matchedWindowTitle": None,
            "windowBounds": None,
            "originalWindowBounds": None,
            "finalWindowBounds": None,
            "focused": False,
            "focusMethod": "not_found",
            "placement": {"status": "WARN", "method": "not_found", "monitorTarget": monitor_index},
            "warnings": ["RuneLite/Jagex window not found"],
        }
    original = window_bounds(window)
    placement = {"status": "not_requested", "method": "none", "monitorTarget": monitor_index}
    should_place = bool(move_to_secondary or window_x is not None or window_y is not None or window_width is not None or window_height is not None)
    if should_place:
        monitors = list(monitor_provider() if monitor_provider is not None else _load_monitors())
        target, monitor_target = _target_bounds(
            original,
            monitors,
            monitor_index=monitor_index,
            window_x=window_x,
            window_y=window_y,
            window_width=window_width,
            window_height=window_height,
        )
        if target is None:
            if fallback_win_shift_arrow:
                try:
                    used = _win_shift_fallback(hotkey_func) if execute else True
                    placement = {"status": "WARN" if used else "FAIL", "method": "win_shift_arrow", "monitorTarget": monitor_target}
                    if used:
                        warnings.append("monitor geometry unavailable; used Windows+Shift+Right fallback" if execute else "monitor geometry unavailable; would use Windows+Shift+Right fallback")
                    else:
                        warnings.append("monitor geometry unavailable and Windows+Shift fallback unavailable")
                except Exception as error:  # noqa: BLE001
                    placement = {"status": "WARN", "method": "win_shift_arrow", "monitorTarget": monitor_target}
                    warnings.append(f"window fallback move failed: {type(error).__name__}: {error}")
            else:
                placement = {"status": "WARN", "method": "monitor_unavailable", "monitorTarget": monitor_target}
                warnings.append(f"monitor index {monitor_index} unavailable")
        elif execute:
            try:
                restore = getattr(window, "restore", None)
                if callable(restore):
                    restore()
                resize_to = getattr(window, "resizeTo", None)
                if callable(resize_to):
                    resize_to(int(target["width"]), int(target["height"]))
                move_to = getattr(window, "moveTo", None)
                if callable(move_to):
                    move_to(int(target["x"]), int(target["y"]))
                activate = getattr(window, "activate", None)
                if callable(activate):
                    activate()
                placement = {"status": "PASS", "method": "pygetwindow", "monitorTarget": monitor_index}
            except Exception as error:  # noqa: BLE001
                placement = {"status": "WARN", "method": "pygetwindow", "monitorTarget": monitor_index}
                warnings.append(f"window move failed: {type(error).__name__}: {error}")
        else:
            placement = {"status": "WARN", "method": "dry_run", "monitorTarget": monitor_index, "targetBounds": target}
    final = window_bounds(window)
    return {
        "matchedWindowTitle": str(getattr(window, "title", "") or ""),
        "windowHandle": int(getattr(window, "_hWnd", 0) or 0),
        "windowBounds": final,
        "originalWindowBounds": original,
        "finalWindowBounds": final,
        "focused": False,
        "focusMethod": "pygetwindow_match",
        "placement": placement,
        "warnings": warnings,
    } | enrich_window_geometry({"windowBounds": final}, int(getattr(window, "_hWnd", 0) or 0))

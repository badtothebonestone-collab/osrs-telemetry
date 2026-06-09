from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import queue
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


INPUT_EVENT_SCHEMA = "input_event.v1"
INPUT_TRACE_SUMMARY_SCHEMA = "input_trace_summary.v1"

VK_NAMES = {
    0x01: "left_mouse",
    0x02: "right_mouse",
    0x04: "middle_mouse",
    0x05: "x1_mouse",
    0x06: "x2_mouse",
    0x08: "backspace",
    0x09: "tab",
    0x0D: "enter",
    0x10: "shift",
    0x11: "ctrl",
    0x12: "alt",
    0x1B: "escape",
    0x20: "space",
    0x25: "left",
    0x26: "up",
    0x27: "right",
    0x28: "down",
}
for index in range(10):
    VK_NAMES[0x30 + index] = str(index)
for index, key in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    VK_NAMES[0x41 + index] = key
for index in range(1, 13):
    VK_NAMES[0x6F + index] = f"f{index}"

POLL_BUTTONS = {
    "left": 0x01,
    "right": 0x02,
    "middle": 0x04,
    "x1": 0x05,
    "x2": 0x06,
}
POLL_KEYS = [
    0x10,
    0x11,
    0x12,
    0x1B,
    0x20,
    0x0D,
    0x25,
    0x26,
    0x27,
    0x28,
    0x41,
    0x44,
    0x53,
    0x57,
    *range(0x30, 0x3A),
    *range(0x70, 0x7C),
]

REAL_INPUT_KINDS = {
    "mouse_move",
    "mouse_down",
    "mouse_up",
    "click",
    "double_click",
    "drag_start",
    "drag_move",
    "drag_end",
    "wheel",
    "key_down",
    "key_up",
}
CLICK_KINDS = {"click", "double_click"}
BUTTON_KINDS = {"mouse_down", "mouse_up", "click", "double_click"}
KEY_KINDS = {"key_down", "key_up"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


class JsonlWriter:
    def __init__(self, path: str | Path, *, pretty: bool = False) -> None:
        self.path = Path(path)
        self.pretty = bool(pretty)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._handle = self.path.open("a", encoding="utf-8")

    def write(self, payload: dict[str, Any]) -> None:
        line = json.dumps(
            payload,
            indent=None,
            sort_keys=False,
            separators=(",", ":") if not self.pretty else None,
            default=str,
        )
        with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._handle.close()


def _user32() -> Any | None:
    if sys.platform != "win32":
        return None
    try:
        return ctypes.windll.user32  # type: ignore[attr-defined]
    except Exception:
        return None


def modifier_state() -> dict[str, bool]:
    user32 = _user32()
    if user32 is None:
        return {"shift": False, "ctrl": False, "alt": False}
    return {
        "shift": bool(user32.GetAsyncKeyState(0x10) & 0x8000),
        "ctrl": bool(user32.GetAsyncKeyState(0x11) & 0x8000),
        "alt": bool(user32.GetAsyncKeyState(0x12) & 0x8000),
    }


def key_name(vk_code: int) -> str:
    return VK_NAMES.get(int(vk_code), f"vk_{int(vk_code)}")


def get_cursor_position() -> dict[str, int] | None:
    user32 = _user32()
    if user32 is None:
        return None

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    point = POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None
    return {"screen_x": int(point.x), "screen_y": int(point.y)}


def foreground_window_info() -> dict[str, Any]:
    user32 = _user32()
    if user32 is None:
        return {"available": False, "runelite_window_match": "unknown"}
    try:
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        client_rect = RECT()
        user32.GetClientRect(hwnd, ctypes.byref(client_rect))

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        origin = POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(origin))
        dpi = None
        try:
            get_dpi = getattr(user32, "GetDpiForWindow")
            dpi = int(get_dpi(hwnd))
        except Exception:
            dpi = None
        title = buffer.value
        return {
            "available": True,
            "foreground_hwnd": int(hwnd),
            "foreground_window_title": title,
            "foreground_window_pid": int(pid.value),
            "foreground_window_rect": {
                "left": int(rect.left),
                "top": int(rect.top),
                "right": int(rect.right),
                "bottom": int(rect.bottom),
                "width": int(rect.right - rect.left),
                "height": int(rect.bottom - rect.top),
            },
            "client_rect": {
                "left": int(client_rect.left),
                "top": int(client_rect.top),
                "right": int(client_rect.right),
                "bottom": int(client_rect.bottom),
                "width": int(client_rect.right - client_rect.left),
                "height": int(client_rect.bottom - client_rect.top),
            },
            "client_origin_screen_x": int(origin.x),
            "client_origin_screen_y": int(origin.y),
            "dpi": dpi,
            "dpi_scale_x": round((dpi or 96) / 96.0, 4) if dpi else None,
            "dpi_scale_y": round((dpi or 96) / 96.0, 4) if dpi else None,
            "runelite_window_match": "runelite" in title.lower(),
        }
    except Exception as error:  # noqa: BLE001
        return {"available": False, "error": f"{type(error).__name__}: {error}", "runelite_window_match": "unknown"}


def client_point_from_screen(screen_x: int | None, screen_y: int | None) -> dict[str, int] | None:
    if screen_x is None or screen_y is None:
        return None
    user32 = _user32()
    if user32 is None:
        return None

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    try:
        hwnd = user32.GetForegroundWindow()
        point = POINT(int(screen_x), int(screen_y))
        if user32.ScreenToClient(hwnd, ctypes.byref(point)):
            return {"client_x": int(point.x), "client_y": int(point.y)}
    except Exception:
        return None
    return None


def classify_client_region(client_x: int | None, client_y: int | None) -> str:
    if client_x is None or client_y is None:
        return "unknown"
    x = int(client_x)
    y = int(client_y)
    if x < 0 or y < 0:
        return "unknown"
    if x <= 765 and y <= 503:
        return "viewport"
    if x > 765 and y <= 170:
        return "minimap"
    if x > 765 and 170 < y <= 520:
        return "inventory/sidebar"
    if y > 503:
        return "chatbox"
    return "unknown"


def attach_window_context(event: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return event
    enriched = dict(event)
    if event.get("screen_x") is not None:
        enriched.setdefault("raw_screen_x", event.get("screen_x"))
    if event.get("screen_y") is not None:
        enriched.setdefault("raw_screen_y", event.get("screen_y"))
    window = foreground_window_info()
    enriched.update({key: value for key, value in window.items() if key != "available"})
    client = client_point_from_screen(_safe_int(event.get("screen_x")), _safe_int(event.get("screen_y")))
    if client:
        enriched.update(client)
        enriched["region"] = classify_client_region(client.get("client_x"), client.get("client_y"))
        enriched["coordinate_capture_method"] = "GetCursorPos+ScreenToClient"
    else:
        enriched.setdefault("region", "unknown")
        enriched["coordinate_capture_method"] = "GetCursorPos"
    return enriched


class WindowsPollingApi:
    name = "windows_user32"

    def __init__(self) -> None:
        self.user32 = _user32()
        if self.user32 is None:
            raise RuntimeError("polling input backend requires Windows user32")

    def cursor_position(self) -> dict[str, int] | None:
        return get_cursor_position()

    def button_down(self, vk_code: int) -> bool:
        return bool(self.user32.GetAsyncKeyState(int(vk_code)) & 0x8000)

    def key_down(self, vk_code: int) -> bool:
        return bool(self.user32.GetAsyncKeyState(int(vk_code)) & 0x8000)

    def modifier_state(self) -> dict[str, bool]:
        return modifier_state()

    def foreground_window_info(self) -> dict[str, Any]:
        return foreground_window_info()


class PollingInputBackend:
    name = "polling"

    def __init__(
        self,
        *,
        sample_ms: int = 10,
        mouse_move_min_px: int = 2,
        capture_mouse: bool = True,
        capture_keyboard: bool = False,
        api: Any | None = None,
    ) -> None:
        self.sample_ms = max(1, int(sample_ms or 10))
        self.mouse_move_min_px = max(0, int(mouse_move_min_px or 0))
        self.capture_mouse = bool(capture_mouse)
        self.capture_keyboard = bool(capture_keyboard)
        self.api = api
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_pos: dict[str, int] | None = None
        self._button_down: dict[str, bool] = {name: False for name in POLL_BUTTONS}
        self._button_down_pos: dict[str, dict[str, int] | None] = {name: None for name in POLL_BUTTONS}
        self._dragging: dict[str, bool] = {name: False for name in POLL_BUTTONS}
        self._key_down: dict[int, bool] = {code: False for code in POLL_KEYS}
        self.stats: dict[str, Any] = {
            "moves": 0,
            "downs": 0,
            "ups": 0,
            "clicks": 0,
            "drags": 0,
            "wheels": 0,
            "key_downs": 0,
            "key_ups": 0,
            "polls": 0,
            "errors": 0,
            "backend": self.name,
            "api": getattr(api, "name", None),
            "started_at": None,
            "stopped_at": None,
            "wheel_available": False,
        }

    def start(self, callback: Callable[[dict[str, Any]], None]) -> None:
        if self.api is None:
            self.api = WindowsPollingApi()
            self.stats["api"] = getattr(self.api, "name", "windows_user32")
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.stats["started_at"] = utc_now()
        self._thread = threading.Thread(target=self._loop, args=(callback,), name="input-trace-polling", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self.stats["stopped_at"] = utc_now()

    def _loop(self, callback: Callable[[dict[str, Any]], None]) -> None:
        if self.api is None:
            return
        while not self._stop.is_set():
            self.stats["polls"] += 1
            try:
                now_pos = self.api.cursor_position()
                if self.capture_mouse and now_pos:
                    self._emit_mouse_poll(callback, now_pos)
                if self.capture_keyboard:
                    self._emit_keyboard_poll(callback)
            except Exception as error:  # noqa: BLE001
                self.stats["errors"] += 1
                if self.stats["errors"] <= 3:
                    self._emit(callback, {"kind": "capture_error", "backend": self.name, "error": f"{type(error).__name__}: {error}"})
            time.sleep(self.sample_ms / 1000.0)

    def _emit(self, callback: Callable[[dict[str, Any]], None], payload: dict[str, Any]) -> None:
        kind = str(payload.get("kind") or "")
        if kind == "mouse_move":
            self.stats["moves"] += 1
        elif kind == "mouse_down":
            self.stats["downs"] += 1
        elif kind == "mouse_up":
            self.stats["ups"] += 1
        elif kind in CLICK_KINDS:
            self.stats["clicks"] += 1
        elif kind.startswith("drag_"):
            self.stats["drags"] += 1
        elif kind == "wheel":
            self.stats["wheels"] += 1
        elif kind == "key_down":
            self.stats["key_downs"] += 1
        elif kind == "key_up":
            self.stats["key_ups"] += 1
        callback(payload)

    def _emit_mouse_poll(self, callback: Callable[[dict[str, Any]], None], now_pos: dict[str, int]) -> None:
        previous = self._last_pos
        if previous is None:
            self._last_pos = dict(now_pos)
        else:
            dx = int(now_pos["screen_x"] - previous["screen_x"])
            dy = int(now_pos["screen_y"] - previous["screen_y"])
            if math.hypot(dx, dy) >= self.mouse_move_min_px:
                self._emit(callback, {"kind": "mouse_move", **now_pos, "dx": dx, "dy": dy})
                for button, down in self._button_down.items():
                    if down:
                        if not self._dragging[button]:
                            self._dragging[button] = True
                            self._emit(callback, {"kind": "drag_start", "button": button, **(self._button_down_pos[button] or now_pos)})
                        self._emit(callback, {"kind": "drag_move", "button": button, **now_pos, "dx": dx, "dy": dy})
                self._last_pos = dict(now_pos)

        for button, vk in POLL_BUTTONS.items():
            is_down = bool(self.api.button_down(vk)) if self.api is not None else False
            was_down = self._button_down.get(button, False)
            if is_down and not was_down:
                self._button_down[button] = True
                self._button_down_pos[button] = dict(now_pos)
                self._dragging[button] = False
                self._emit(callback, {"kind": "mouse_down", "button": button, **now_pos})
            elif was_down and not is_down:
                self._button_down[button] = False
                if self._dragging.get(button):
                    self._emit(callback, {"kind": "drag_end", "button": button, **now_pos})
                self._emit(callback, {"kind": "mouse_up", "button": button, **now_pos})
                self._emit(callback, {"kind": "click", "button": button, **now_pos})
                self._button_down_pos[button] = None
                self._dragging[button] = False

    def _emit_keyboard_poll(self, callback: Callable[[dict[str, Any]], None]) -> None:
        mods = self.api.modifier_state() if self.api is not None and hasattr(self.api, "modifier_state") else modifier_state()
        for code in POLL_KEYS:
            is_down = bool(self.api.key_down(code)) if self.api is not None else False
            was_down = self._key_down.get(code, False)
            if is_down == was_down:
                continue
            self._key_down[code] = is_down
            self._emit(
                callback,
                {
                    "kind": "key_down" if is_down else "key_up",
                    "key_name": key_name(code),
                    "vk_code": int(code),
                    "modifiers": mods,
                    "camera_navigation_key": code in {0x25, 0x26, 0x27, 0x28, 0x41, 0x44, 0x53, 0x57},
                }
            )


class WindowsHookInputBackend:
    name = "windows_hook"

    def __init__(self, *, capture_mouse: bool = True, capture_keyboard: bool = False, **_kwargs: Any) -> None:
        self.capture_mouse = bool(capture_mouse)
        self.capture_keyboard = bool(capture_keyboard)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._callback: Callable[[dict[str, Any]], None] | None = None
        self._mouse_ref: Any | None = None
        self._keyboard_ref: Any | None = None
        self._hooks: list[Any] = []
        self._last_button_down: dict[str, dict[str, int] | None] = {}
        self._last_click_time: dict[str, float] = {}

    def start(self, callback: Callable[[dict[str, Any]], None]) -> None:
        if sys.platform != "win32":
            raise RuntimeError("windows_hook input backend requires Windows")
        if self._thread and self._thread.is_alive():
            return
        self._callback = callback
        self._stop.clear()
        self._thread = threading.Thread(target=self._message_loop, name="input-trace-windows-hook", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        user32 = _user32()
        if user32 is not None:
            for hook in self._hooks:
                try:
                    user32.UnhookWindowsHookEx(hook)
                except Exception:
                    pass
        self._hooks = []
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _emit(self, payload: dict[str, Any]) -> None:
        if self._callback:
            self._callback(payload)

    def _message_loop(self) -> None:
        user32 = _user32()
        if user32 is None:
            return
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        WH_MOUSE_LL = 14
        WH_KEYBOARD_LL = 13
        HC_ACTION = 0

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        def mouse_proc(code: int, wparam: Any, lparam: Any) -> int:
            if code == HC_ACTION:
                data = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                msg = int(wparam)
                payload: dict[str, Any] | None = None
                base = {"screen_x": int(data.pt.x), "screen_y": int(data.pt.y)}
                if msg == 0x0200:
                    payload = {"kind": "mouse_move", **base}
                elif msg in {0x0201, 0x0204, 0x0207}:
                    button = {0x0201: "left", 0x0204: "right", 0x0207: "middle"}[msg]
                    self._last_button_down[button] = dict(base)
                    payload = {"kind": "mouse_down", "button": button, **base}
                elif msg in {0x0202, 0x0205, 0x0208}:
                    button = {0x0202: "left", 0x0205: "right", 0x0208: "middle"}[msg]
                    payload = {"kind": "mouse_up", "button": button, **base}
                    self._emit(payload)
                    now = time.monotonic()
                    click_kind = "double_click" if now - self._last_click_time.get(button, 0.0) <= 0.35 else "click"
                    self._last_click_time[button] = now
                    self._emit({"kind": click_kind, "button": button, **base})
                    return user32.CallNextHookEx(None, code, wparam, lparam)
                elif msg == 0x020A:
                    delta = ctypes.c_short((int(data.mouseData) >> 16) & 0xFFFF).value
                    payload = {"kind": "wheel", "wheel_delta": int(delta), **base}
                if payload:
                    self._emit(payload)
            return user32.CallNextHookEx(None, code, wparam, lparam)

        def keyboard_proc(code: int, wparam: Any, lparam: Any) -> int:
            if code == HC_ACTION:
                data = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                msg = int(wparam)
                if msg in {0x0100, 0x0104, 0x0101, 0x0105}:
                    vk = int(data.vkCode)
                    self._emit(
                        {
                            "kind": "key_down" if msg in {0x0100, 0x0104} else "key_up",
                            "key_name": key_name(vk),
                            "vk_code": vk,
                            "scan_code": int(data.scanCode),
                            "modifiers": modifier_state(),
                            "camera_navigation_key": vk in {0x25, 0x26, 0x27, 0x28, 0x41, 0x44, 0x53, 0x57},
                        }
                    )
            return user32.CallNextHookEx(None, code, wparam, lparam)

        hmod = kernel32.GetModuleHandleW(None)
        if self.capture_mouse:
            self._mouse_ref = HOOKPROC(mouse_proc)
            hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_ref, hmod, 0)
            if hook:
                self._hooks.append(hook)
        if self.capture_keyboard:
            self._keyboard_ref = HOOKPROC(keyboard_proc)
            hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._keyboard_ref, hmod, 0)
            if hook:
                self._hooks.append(hook)
        if not self._hooks:
            raise RuntimeError("failed to install Windows input hooks")

        msg = wintypes.MSG()
        while not self._stop.is_set():
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0x0001):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.005)


class InputTraceRecorder:
    def __init__(
        self,
        recording_dir: str | Path,
        *,
        session_id: str,
        recording_id: str,
        backend_name: str = "auto",
        sample_ms: int = 10,
        mouse_move_min_px: int = 2,
        capture_mouse: bool = True,
        capture_keyboard: bool = False,
        capture_window_context: bool = True,
        raw_input_device_attribution: bool = False,
        include_raw: bool = False,
        pretty: bool = False,
        telemetry_provider: Callable[[], dict[str, Any]] | None = None,
        backend: Any | None = None,
        started_monotonic: float | None = None,
        event_listeners: list[Callable[[dict[str, Any]], None]] | None = None,
    ) -> None:
        self.recording_dir = Path(recording_dir)
        self.session_id = session_id
        self.recording_id = recording_id
        self.backend_name = backend_name or "auto"
        self.capture_window_context = bool(capture_window_context)
        self.raw_input_device_attribution = bool(raw_input_device_attribution)
        self.include_raw = bool(include_raw)
        self.telemetry_provider = telemetry_provider
        self.started_monotonic = float(started_monotonic) if started_monotonic is not None else time.monotonic()
        self.writer = JsonlWriter(self.recording_dir / "input_events.jsonl", pretty=pretty)
        self._seq = 0
        self._lock = threading.Lock()
        self.warnings: list[str] = []
        self._backend = backend or self._select_backend(
            backend_name,
            sample_ms=sample_ms,
            mouse_move_min_px=mouse_move_min_px,
            capture_mouse=capture_mouse,
            capture_keyboard=capture_keyboard,
        )
        self.source_backend = getattr(self._backend, "name", backend_name)
        self.started = False
        self.event_count = 0
        self.kind_counts: dict[str, int] = {}
        self.last_event: dict[str, Any] | None = None
        self._event_listeners: list[Callable[[dict[str, Any]], None]] = list(event_listeners or [])

    def add_event_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        self._event_listeners.append(listener)

    def _select_backend(self, name: str, **kwargs: Any) -> Any:
        normalized = str(name or "auto").lower()
        if normalized in {"auto", "polling"}:
            try:
                return PollingInputBackend(**kwargs)
            except Exception as error:  # noqa: BLE001
                if normalized == "polling":
                    raise
                self.warnings.append(f"polling unavailable: {type(error).__name__}: {error}")
        if normalized == "windows_hook" and sys.platform == "win32":
            try:
                return WindowsHookInputBackend(**kwargs)
            except Exception as error:  # noqa: BLE001
                raise
        if normalized == "auto" and sys.platform == "win32":
            try:
                return WindowsHookInputBackend(**kwargs)
            except Exception as error:  # noqa: BLE001
                self.warnings.append(f"windows_hook unavailable: {type(error).__name__}: {error}")
        if normalized == "windows_hook":
            raise RuntimeError("windows_hook input backend requires Windows")
        return PollingInputBackend(**kwargs)

    def start(self) -> None:
        if self.started:
            return
        try:
            self._backend.start(self.record_event)
            self.started = True
            self.record_event({"kind": "capture_start", "backend_requested": self.backend_name, "backend_selected": self.source_backend})
        except Exception as error:  # noqa: BLE001
            self.warnings.append(f"input capture start failed: {type(error).__name__}: {error}")
            self.record_event({"kind": "capture_error", "error": self.warnings[-1]})

    def stop(self) -> dict[str, Any]:
        if self.started:
            try:
                self._backend.stop()
            except Exception as error:  # noqa: BLE001
                self.warnings.append(f"input capture stop failed: {type(error).__name__}: {error}")
        self.record_event({"kind": "capture_stop", "eventCount": self.event_count, "backend_stats": getattr(self._backend, "stats", None)})
        self.started = False
        self.writer.close()
        summary = self.summary()
        atomic_write_json(self.recording_dir / "input_trace_summary.json", summary)
        return summary

    def record_event(self, payload: dict[str, Any]) -> None:
        now = time.monotonic()
        with self._lock:
            self._seq += 1
            seq = self._seq
        event = {
            "schema": INPUT_EVENT_SCHEMA,
            "session_id": self.session_id,
            "recording_id": self.recording_id,
            "event_seq": seq,
            "monotonic_time": now,
            "elapsed_seconds": round(now - self.started_monotonic, 6),
            "wall_time_utc": utc_now(),
            "kind": payload.get("kind") or "unknown",
            "source_backend": self.source_backend,
        }
        telemetry = self.telemetry_provider() if self.telemetry_provider else {}
        if isinstance(telemetry, dict):
            if telemetry.get("latest_tick") is not None:
                event["nearest_tick"] = telemetry.get("latest_tick")
            if telemetry.get("latest_export_sequence") is not None:
                event["nearest_export_sequence"] = telemetry.get("latest_export_sequence")
        clean_payload = dict(payload)
        clean_payload.pop("kind", None)
        event.update(clean_payload)
        event.update(modifier_state() if event["kind"].startswith("mouse") or event["kind"] in {"click", "double_click", "wheel"} else {})
        event = attach_window_context(event, enabled=self.capture_window_context)
        if self.raw_input_device_attribution:
            event["rawInputDevice"] = {
                "available": False,
                "deviceClass": None,
                "deviceName": None,
                "reason": "raw input device attribution is not implemented in the polling backend yet",
            }
        if self.include_raw:
            event.setdefault("debug", {})["raw_payload"] = payload
        self.event_count += 1
        self.kind_counts[str(event["kind"])] = self.kind_counts.get(str(event["kind"]), 0) + 1
        self.last_event = dict(event)
        self.writer.write(event)
        for listener in list(self._event_listeners):
            try:
                listener(dict(event))
            except Exception as error:  # noqa: BLE001
                warning = f"input event listener failed: {type(error).__name__}: {error}"
                self.warnings.append(warning)

    def summary(self) -> dict[str, Any]:
        real_event_count = sum(count for kind, count in self.kind_counts.items() if kind in REAL_INPUT_KINDS)
        diagnostics = input_capture_diagnostics(
            event_count=self.event_count,
            kind_counts=self.kind_counts,
            backend_used=self.source_backend,
            backend_requested=self.backend_name,
            warnings=self.warnings,
        )
        return {
            "schema": INPUT_TRACE_SUMMARY_SCHEMA,
            "recording_id": self.recording_id,
            "session_id": self.session_id,
            "backend": self.source_backend,
            "backendRequested": self.backend_name,
            "backendUsed": self.source_backend,
            "status": "PASS" if real_event_count > 0 and not any("failed" in warning for warning in self.warnings) else "WARN",
            "captureStatus": diagnostics["captureStatus"],
            "eventCount": self.event_count,
            "realEventCount": real_event_count,
            "kindCounts": dict(sorted(self.kind_counts.items())),
            "mouseMoveCount": self.kind_counts.get("mouse_move", 0),
            "mouseDownCount": self.kind_counts.get("mouse_down", 0),
            "mouseUpCount": self.kind_counts.get("mouse_up", 0),
            "clickCount": sum(self.kind_counts.get(kind, 0) for kind in CLICK_KINDS),
            "keyboardEventCount": sum(self.kind_counts.get(kind, 0) for kind in KEY_KINDS),
            "backendStats": getattr(self._backend, "stats", None),
            "rawInputDeviceAttribution": {
                "requested": self.raw_input_device_attribution,
                "available": False,
                "reason": "Raw Input attribution is optional and was unavailable; polling capture remains valid.",
            },
            "lastEvent": self.last_event,
            "message": diagnostics.get("message"),
            "recommendations": diagnostics.get("recommendations") or [],
            "warnings": sorted(set(list(self.warnings) + list(diagnostics.get("warnings") or []))),
        }


def atomic_write_json(path: str | Path, payload: dict[str, Any], *, pretty: bool = True) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, sort_keys=False, default=str)
        handle.write("\n")
    temp.replace(output)


def load_input_events(path: str | Path) -> list[dict[str, Any]]:
    events_path = Path(path)
    events: list[dict[str, Any]] = []
    try:
        with events_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    events.append(value)
    except OSError:
        return []
    return events


def _backend_from_events(events: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    requested = None
    used = None
    for event in events:
        requested = requested or event.get("backend_requested")
        used = used or event.get("source_backend") or event.get("backend_selected")
        if requested and used:
            break
    return (str(requested) if requested else None, str(used) if used else None)


def input_capture_diagnostics(
    *,
    event_count: int,
    kind_counts: dict[str, int],
    backend_used: str | None = None,
    backend_requested: str | None = None,
    input_file_exists: bool | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    warnings = list(warnings or [])
    real_event_count = sum(int(kind_counts.get(kind, 0) or 0) for kind in REAL_INPUT_KINDS)
    capture_status = "unknown"
    message = None
    recommendations: list[str] = []

    if event_count <= 0:
        capture_status = "input_file_missing" if input_file_exists is False else "backend_started_but_no_events"
        message = "input_events.jsonl is missing or empty."
        recommendations.extend(
            [
                "run input_trace_recorder.py --smoke-test",
                "retry with --input-backend polling",
            ]
        )
    elif real_event_count <= 0:
        backend_text = str(backend_used or backend_requested or "").lower()
        if "windows_hook" in backend_text:
            capture_status = "hook_backend_no_events"
        elif "polling" in backend_text:
            capture_status = "polling_backend_no_events"
        else:
            capture_status = "backend_started_but_no_events"
        message = "Input capture started but captured no real mouse/keyboard activity."
        recommendations.extend(
            [
                "retry with --input-backend polling",
                "run input_trace_recorder.py --smoke-test",
                "check whether the process is running in the same Windows desktop/session",
                "check whether the UI launched the recorder with the expected input flags",
                "check backendRequested/backendUsed in input_trace_summary.json",
            ]
        )
    else:
        capture_status = "captured_real_input"
        message = "Input capture observed real mouse/keyboard activity."

    if "failed" in " ".join(warnings).lower() and capture_status == "captured_real_input":
        capture_status = "unknown"
    return {
        "captureStatus": capture_status,
        "realEventCount": real_event_count,
        "message": message,
        "recommendations": recommendations,
        "warnings": [] if real_event_count > 0 else ([message] if message else []),
    }


def summarize_input_events(events: list[dict[str, Any]], *, input_file_exists: bool | None = None) -> dict[str, Any]:
    kind_counts: dict[str, int] = {}
    click_events: list[dict[str, Any]] = []
    key_events: list[dict[str, Any]] = []
    mouse_moves = 0
    for event in events:
        kind = str(event.get("kind") or "unknown")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        if kind in {"click", "double_click", "mouse_down", "mouse_up"}:
            click_events.append(event)
        if kind in {"key_down", "key_up"}:
            key_events.append(event)
        if kind == "mouse_move":
            mouse_moves += 1
    backend_requested, backend_used = _backend_from_events(events)
    diagnostics = input_capture_diagnostics(
        event_count=len(events),
        kind_counts=kind_counts,
        backend_used=backend_used,
        backend_requested=backend_requested,
        input_file_exists=input_file_exists,
    )
    return {
        "schema": INPUT_TRACE_SUMMARY_SCHEMA,
        "status": "PASS" if diagnostics["realEventCount"] > 0 else "WARN",
        "captureStatus": diagnostics["captureStatus"],
        "message": diagnostics.get("message"),
        "eventCount": len(events),
        "realEventCount": diagnostics["realEventCount"],
        "kindCounts": dict(sorted(kind_counts.items())),
        "clickCount": sum(1 for event in events if event.get("kind") in {"click", "double_click"}),
        "mouseDownCount": sum(1 for event in events if event.get("kind") == "mouse_down"),
        "mouseUpCount": sum(1 for event in events if event.get("kind") == "mouse_up"),
        "mouseMoveCount": mouse_moves,
        "keyboardEventCount": len(key_events),
        "backendRequested": backend_requested,
        "backendUsed": backend_used,
        "firstEventTime": events[0].get("wall_time_utc") if events else None,
        "lastEventTime": events[-1].get("wall_time_utc") if events else None,
        "lastMouseEvent": next((event for event in reversed(events) if str(event.get("kind", "")).startswith("mouse") or event.get("kind") in {"click", "wheel"}), None),
        "lastClick": next((event for event in reversed(events) if event.get("kind") in {"click", "double_click"}), None),
        "recommendations": diagnostics.get("recommendations") or [],
        "warnings": diagnostics.get("warnings") or ([] if events else ["input_events.jsonl is missing or empty"]),
    }


def run_smoke_test(
    out: str | Path,
    *,
    backend: str = "polling",
    duration: float = 8.0,
    sample_ms: int = 10,
    mouse_move_min_px: int = 2,
    capture_mouse: bool = True,
    capture_keyboard: bool = True,
    capture_window_context: bool = True,
    raw_input_device_attribution: bool = False,
    json_output: bool = False,
    backend_obj: Any | None = None,
) -> dict[str, Any]:
    output = Path(out)
    recording_dir = output.parent if output.suffix == ".jsonl" else output
    recording_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("input_events.jsonl", "input_capture_status.json", "input_capture_smoke_test.json"):
        try:
            (recording_dir / filename).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    if not json_output:
        print("Input smoke test: move the mouse, left click, right click, and press an arrow key.", flush=True)
    recorder = InputTraceRecorder(
        recording_dir,
        session_id="input_smoke_test",
        recording_id="input_smoke_test",
        backend_name=backend,
        sample_ms=sample_ms,
        mouse_move_min_px=mouse_move_min_px,
        capture_mouse=capture_mouse,
        capture_keyboard=capture_keyboard,
        capture_window_context=capture_window_context,
        raw_input_device_attribution=raw_input_device_attribution,
        backend=backend_obj,
    )
    recorder.start()
    try:
        time.sleep(max(0.0, float(duration or 0.0)))
    finally:
        recorder.stop()
    events = load_input_events(recording_dir / "input_events.jsonl")
    summary = summarize_input_events(events, input_file_exists=(recording_dir / "input_events.jsonl").exists())
    mouse_ok = int(summary.get("mouseMoveCount") or 0) >= 1
    click_ok = int(summary.get("clickCount") or 0) >= 1 or int(summary.get("mouseDownCount") or 0) >= 1
    keyboard_ok = int(summary.get("keyboardEventCount") or 0) >= 1 if capture_keyboard else None
    success = bool(mouse_ok and click_ok)
    reason = "captured mouse movement and click/button activity" if success else "missing mouse movement or click/button activity"
    foreground_samples = [
        {
            key: event.get(key)
            for key in ("wall_time_utc", "foreground_window_title", "foreground_window_pid", "runelite_window_match", "region")
            if event.get(key) is not None
        }
        for event in events
        if event.get("foreground_window_title") is not None
    ][:5]
    result = {
        "schema": "input_capture_smoke_test.v1",
        "generated_at_utc": utc_now(),
        "success": success,
        "reason": reason,
        "backendRequested": backend,
        "backendUsed": summary.get("backendUsed") or getattr(recorder, "source_backend", backend),
        "durationSeconds": float(duration or 0.0),
        "mouseSuccess": {"move": mouse_ok, "clickOrDown": click_ok},
        "keyboardSuccess": keyboard_ok,
        "eventCounts": {
            "events": summary.get("eventCount"),
            "realEvents": summary.get("realEventCount"),
            "moves": summary.get("mouseMoveCount"),
            "downs": summary.get("mouseDownCount"),
            "clicks": summary.get("clickCount"),
            "key_downs": summary.get("kindCounts", {}).get("key_down", 0),
            "key_ups": summary.get("kindCounts", {}).get("key_up", 0),
        },
        "captureStatus": summary.get("captureStatus"),
        "errors": [event for event in events if event.get("kind") == "capture_error"],
        "foregroundWindowSamples": foreground_samples,
        "recommendations": summary.get("recommendations") or [],
    }
    atomic_write_json(recording_dir / "input_capture_status.json", summary)
    atomic_write_json(recording_dir / "input_capture_smoke_test.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture OS-level input events into input_events.jsonl.")
    parser.add_argument("--out", required=True, help="Recording folder or output jsonl path.")
    parser.add_argument("--backend", choices=("auto", "windows_hook", "polling"), default="auto")
    parser.add_argument("--smoke-test", action="store_true", help="Run an interactive input capture smoke test and exit.")
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--sample-ms", type=int, default=10)
    parser.add_argument("--mouse-move-min-px", type=int, default=2)
    parser.add_argument("--capture-mouse", action="store_true")
    parser.add_argument("--capture-keyboard", action="store_true")
    parser.add_argument("--capture-window-context", action="store_true")
    parser.add_argument("--raw-input-device-attribution", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print only JSON result.")
    parser.add_argument("--recording-id", default="standalone")
    parser.add_argument("--session-id", default="standalone")
    args = parser.parse_args(argv)

    output = Path(args.out)
    recording_dir = output.parent if output.suffix == ".jsonl" else output
    capture_mouse = bool(args.capture_mouse or not args.capture_keyboard)
    if args.smoke_test:
        result = run_smoke_test(
            recording_dir,
            backend=args.backend,
            duration=args.duration,
            sample_ms=args.sample_ms,
            mouse_move_min_px=args.mouse_move_min_px,
            capture_mouse=bool(args.capture_mouse or not args.capture_keyboard),
            capture_keyboard=bool(args.capture_keyboard),
            capture_window_context=bool(args.capture_window_context),
            raw_input_device_attribution=bool(args.raw_input_device_attribution),
            json_output=bool(args.json),
        )
        print(json.dumps(result, indent=2 if args.json else None, separators=None if args.json else (",", ":"), default=str))
        return 0 if result.get("success") else 1
    recorder = InputTraceRecorder(
        recording_dir,
        session_id=args.session_id,
        recording_id=args.recording_id,
        backend_name=args.backend,
        sample_ms=args.sample_ms,
        mouse_move_min_px=args.mouse_move_min_px,
        capture_mouse=capture_mouse,
        capture_keyboard=bool(args.capture_keyboard),
        capture_window_context=bool(args.capture_window_context),
        raw_input_device_attribution=bool(args.raw_input_device_attribution),
    )
    recorder.start()
    try:
        time.sleep(max(0.0, float(args.duration or 0.0)))
    finally:
        summary = recorder.stop()
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

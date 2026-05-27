from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

try:
    from .input_integrity import build_input_integrity_status, compact_status_lines, overlay_display_state, wall_time_millis
except ImportError:  # pragma: no cover - script execution path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from input_control.input_integrity import build_input_integrity_status, compact_status_lines, overlay_display_state, wall_time_millis


DEFAULT_STATUS_PATH = Path("interaction_geometry") / "live" / "input_integrity_status.json"
DEFAULT_BACKEND_STATUS_PATH = Path("interaction_geometry") / "live" / "arduino_backend_status.json"
LLMHF_INJECTED = 0x00000001
LLMHF_LOWER_IL_INJECTED = 0x00000002
LLKHF_LOWER_IL_INJECTED = 0x00000002
LLKHF_INJECTED = 0x00000010


def _foreground_window_info() -> dict[str, Any]:
    if sys.platform != "win32":
        return {"available": False}
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return {"available": True, "hwnd": int(hwnd), "title": buffer.value, "pid": int(pid.value)}
    except Exception as error:  # noqa: BLE001
        return {"available": False, "error": f"{type(error).__name__}: {error}"}


def _apply_passive_overlay_window(root: Any, *, click_through: bool = True, no_focus: bool = True) -> dict[str, Any]:
    status: dict[str, Any] = {
        "requested": True,
        "applied": False,
        "clickThrough": bool(click_through),
        "noFocus": bool(no_focus),
        "warnings": [],
    }
    if sys.platform != "win32":
        status["warnings"].append("passive_overlay_styles_require_windows")
        return status
    try:
        root.update_idletasks()
        hwnd = int(root.winfo_id())
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        SWP_NOACTIVATE = 0x0010
        get_window_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = int(get_window_long(hwnd, GWL_EXSTYLE))
        style |= WS_EX_TOOLWINDOW
        if click_through:
            style |= WS_EX_TRANSPARENT
        if no_focus:
            style |= WS_EX_NOACTIVATE
        set_window_long(hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        status["applied"] = True
        status["hwnd"] = hwnd
    except Exception as error:  # noqa: BLE001
        status["warnings"].append(f"passive_overlay_style_failed: {type(error).__name__}: {error}")
    return status


def _restore_foreground_hwnd(hwnd: Any) -> bool:
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        return bool(user32.SetForegroundWindow(int(hwnd)))
    except Exception:  # noqa: BLE001
        return False


def _safe_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _contains_vid_pid(text: str, vid: str | None, pid: str | None) -> bool:
    haystack = str(text or "").upper()
    return bool((not vid or vid.upper() in haystack) and (not pid or pid.upper() in haystack))


def _pnp_arduino_devices(vid: str | None, pid: str | None) -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    token = "&".join(item for item in (vid, pid) if item)
    script = (
        "$devices = Get-PnpDevice -PresentOnly | "
        f"Where-Object {{ $_.InstanceId -match '{token}' }} | "
        "Select-Object Class,FriendlyName,InstanceId,Status; "
        "$devices | ConvertTo-Json -Depth 4"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return []
        decoded = json.loads(completed.stdout)
    except Exception:  # noqa: BLE001
        return []
    rows = decoded if isinstance(decoded, list) else [decoded]
    return [row for row in rows if isinstance(row, dict)]


class InputIntegrityMonitor:
    def __init__(
        self,
        *,
        expected_vid: str = "VID_2341",
        expected_pid: str = "PID_8036",
        expected_com_port: str | None = None,
        expected_device_path: str | None = None,
        live_input_backend: str = "arduino",
        status_output: Path = DEFAULT_STATUS_PATH,
        backend_status_path: Path = DEFAULT_BACKEND_STATUS_PATH,
        max_age_ms: int = 3000,
        fail_on_injected: bool = True,
        fail_on_bypass: bool = True,
    ) -> None:
        self.expected_vid = expected_vid
        self.expected_pid = expected_pid
        self.expected_com_port = expected_com_port
        self.expected_device_path = expected_device_path
        self.live_input_backend = live_input_backend
        self.status_output = status_output
        self.backend_status_path = backend_status_path
        self.max_age_ms = int(max_age_ms or 3000)
        self.fail_on_injected = bool(fail_on_injected)
        self.fail_on_bypass = bool(fail_on_bypass)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._raw_device_paths: set[str] = set()
        self._raw_mouse_paths: set[str] = set()
        self._raw_keyboard_paths: set[str] = set()
        self._raw_input_mouse_count = 0
        self._raw_input_keyboard_count = 0
        self._raw_mouse_dx_total = 0
        self._raw_mouse_dy_total = 0
        self._last_raw_mouse_delta: dict[str, int] | None = None
        self._mouse_event_count = 0
        self._keyboard_event_count = 0
        self._mouse_injected = 0
        self._mouse_lower_il = 0
        self._keyboard_injected = 0
        self._keyboard_lower_il = 0
        self._last_any_ms: int | None = None
        self._last_mouse_ms: int | None = None
        self._last_keyboard_ms: int | None = None
        self._last_injected_ms: int | None = None
        self._pnp_devices: list[dict[str, Any]] = []
        self._warnings: list[str] = []
        self._thread: threading.Thread | None = None
        self._hooks: list[Any] = []
        self._wnd_proc_ref: Any | None = None
        self._mouse_hook_ref: Any | None = None
        self._keyboard_hook_ref: Any | None = None
        self._hwnd: int | None = None
        self.vm_input_focus_safety: dict[str, Any] = {
            "status": "NOT_EVALUATED",
            "postTestInputState": "not_evaluated",
            "postTestFocusRecovery": "not_evaluated",
        }

    def start(self) -> None:
        self.refresh_pnp()
        if sys.platform != "win32":
            self._warnings.append("raw_input_monitor_requires_windows")
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._windows_message_loop, name="ArduinoInputIntegrityMonitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if sys.platform == "win32":
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            for hook in self._hooks:
                try:
                    user32.UnhookWindowsHookEx(hook)
                except Exception:  # noqa: BLE001
                    pass
        self._hooks = []

    def refresh_pnp(self) -> None:
        devices = _pnp_arduino_devices(self.expected_vid, self.expected_pid)
        with self._lock:
            if devices or not self._pnp_devices:
                self._pnp_devices = devices

    def _device_path(self, device_handle: Any) -> str:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        RIDI_DEVICENAME = 0x20000007
        size = wintypes.UINT(0)
        user32.GetRawInputDeviceInfoW(device_handle, RIDI_DEVICENAME, None, ctypes.byref(size))
        if size.value <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(size.value + 1)
        if user32.GetRawInputDeviceInfoW(device_handle, RIDI_DEVICENAME, buffer, ctypes.byref(size)) == 0xFFFFFFFF:
            return ""
        return buffer.value

    def _record_raw_input(self, kind: str, device_path: str, *, dx: int = 0, dy: int = 0) -> None:
        now = wall_time_millis()
        with self._lock:
            if device_path:
                self._raw_device_paths.add(device_path)
            self._last_any_ms = now
            if kind == "mouse":
                self._raw_input_mouse_count += 1
                self._mouse_event_count += 1
                self._raw_mouse_dx_total += int(dx or 0)
                self._raw_mouse_dy_total += int(dy or 0)
                self._last_raw_mouse_delta = {"dx": int(dx or 0), "dy": int(dy or 0)}
                self._last_mouse_ms = now
                if device_path:
                    self._raw_mouse_paths.add(device_path)
            elif kind == "keyboard":
                self._raw_input_keyboard_count += 1
                self._keyboard_event_count += 1
                self._last_keyboard_ms = now
                if device_path:
                    self._raw_keyboard_paths.add(device_path)

    def _record_injection_flags(self, kind: str, flags: int) -> None:
        now = wall_time_millis()
        with self._lock:
            if kind == "mouse":
                if flags & LLMHF_INJECTED:
                    self._mouse_injected += 1
                    self._last_injected_ms = now
                if flags & LLMHF_LOWER_IL_INJECTED:
                    self._mouse_lower_il += 1
                    self._last_injected_ms = now
            elif kind == "keyboard":
                if flags & LLKHF_INJECTED:
                    self._keyboard_injected += 1
                    self._last_injected_ms = now
                if flags & LLKHF_LOWER_IL_INJECTED:
                    self._keyboard_lower_il += 1
                    self._last_injected_ms = now

    def _windows_message_loop(self) -> None:
        try:
            self._install_windows_monitor()
        except Exception as error:  # noqa: BLE001
            with self._lock:
                self._warnings.append(f"windows_monitor_unavailable: {type(error).__name__}: {error}")
            return
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        msg = wintypes.MSG()
        PM_REMOVE = 0x0001
        while not self._stop.is_set():
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.01)

    def _install_windows_monitor(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HANDLE
        WPARAM_T = ctypes.c_size_t
        LPARAM_T = ctypes.c_ssize_t
        LRESULT_T = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT_T, wintypes.HWND, wintypes.UINT, WPARAM_T, LPARAM_T)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HANDLE),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        class RAWINPUTDEVICE(ctypes.Structure):
            _fields_ = [
                ("usUsagePage", wintypes.USHORT),
                ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD),
                ("hwndTarget", wintypes.HWND),
            ]

        class RAWINPUTHEADER(ctypes.Structure):
            _fields_ = [
                ("dwType", wintypes.DWORD),
                ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE),
                ("wParam", WPARAM_T),
            ]

        class RAWMOUSE(ctypes.Structure):
            _fields_ = [
                ("usFlags", wintypes.USHORT),
                ("usButtonFlags", wintypes.USHORT),
                ("usButtonData", wintypes.USHORT),
                ("usPadding", wintypes.USHORT),
                ("ulRawButtons", wintypes.DWORD),
                ("lLastX", wintypes.LONG),
                ("lLastY", wintypes.LONG),
                ("ulExtraInformation", wintypes.DWORD),
            ]

        def wnd_proc(hwnd: Any, msg: int, wparam: Any, lparam: Any) -> int:
            WM_INPUT = 0x00FF
            WM_NCCREATE = 0x0081
            RID_INPUT = 0x10000003
            if msg == WM_NCCREATE:
                return 1
            if msg == WM_INPUT:
                size = wintypes.UINT(0)
                header_size = ctypes.sizeof(RAWINPUTHEADER)
                user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), header_size)
                if size.value:
                    buffer = ctypes.create_string_buffer(size.value)
                    if user32.GetRawInputData(lparam, RID_INPUT, buffer, ctypes.byref(size), header_size) != 0xFFFFFFFF:
                        header = RAWINPUTHEADER.from_buffer_copy(buffer.raw[:header_size])
                        kind = "mouse" if int(header.dwType) == 0 else "keyboard" if int(header.dwType) == 1 else "other"
                        if kind == "mouse":
                            dx = 0
                            dy = 0
                            try:
                                mouse = RAWMOUSE.from_buffer_copy(buffer.raw[header_size : header_size + ctypes.sizeof(RAWMOUSE)])
                                dx = int(mouse.lLastX)
                                dy = int(mouse.lLastY)
                            except Exception:  # noqa: BLE001
                                pass
                            self._record_raw_input(kind, self._device_path(header.hDevice), dx=dx, dy=dy)
                        elif kind == "keyboard":
                            self._record_raw_input(kind, self._device_path(header.hDevice))
            return 0

        self._wnd_proc_ref = WNDPROC(wnd_proc)
        hinstance = kernel32.GetModuleHandleW(None)
        class_name = "OsrsTelemetryArduinoInputMonitor"
        wndclass = WNDCLASS()
        wndclass.lpfnWndProc = self._wnd_proc_ref
        wndclass.hInstance = hinstance
        wndclass.lpszClassName = class_name
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        atom = user32.RegisterClassW(ctypes.byref(wndclass))
        register_error = ctypes.get_last_error() if not atom else 0
        hwnd = user32.CreateWindowExW(0, class_name, class_name, 0, 0, 0, 1, 1, None, None, hinstance, None)
        if not hwnd:
            raise RuntimeError(f"CreateWindowExW failed last_error={ctypes.get_last_error()} register_error={register_error}")
        self._hwnd = int(hwnd)
        RIDEV_INPUTSINK = 0x00000100
        devices = (RAWINPUTDEVICE * 2)(
            RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd),
            RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, hwnd),
        )
        if not user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(RAWINPUTDEVICE)):
            raise RuntimeError("RegisterRawInputDevices failed")
        self._install_low_level_hooks()

    def _install_low_level_hooks(self) -> None:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        WPARAM_T = ctypes.c_size_t
        LPARAM_T = ctypes.c_ssize_t
        LRESULT_T = ctypes.c_ssize_t
        user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, WPARAM_T, LPARAM_T]
        user32.CallNextHookEx.restype = LRESULT_T
        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", wintypes.POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        def mouse_proc(n_code: int, wparam: Any, lparam: Any) -> int:
            if n_code >= 0:
                event = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                self._record_injection_flags("mouse", int(event.flags))
            return user32.CallNextHookEx(None, n_code, wparam, lparam)

        def keyboard_proc(n_code: int, wparam: Any, lparam: Any) -> int:
            if n_code >= 0:
                event = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                self._record_injection_flags("keyboard", int(event.flags))
            return user32.CallNextHookEx(None, n_code, wparam, lparam)

        self._mouse_hook_ref = HOOKPROC(mouse_proc)
        self._keyboard_hook_ref = HOOKPROC(keyboard_proc)
        mouse_hook = user32.SetWindowsHookExW(14, self._mouse_hook_ref, None, 0)
        keyboard_hook = user32.SetWindowsHookExW(13, self._keyboard_hook_ref, None, 0)
        if mouse_hook:
            self._hooks.append(mouse_hook)
        else:
            self._warnings.append("mouse_low_level_hook_unavailable")
        if keyboard_hook:
            self._hooks.append(keyboard_hook)
        else:
            self._warnings.append("keyboard_low_level_hook_unavailable")

    def raw_payload(self) -> dict[str, Any]:
        now = wall_time_millis()
        with self._lock:
            device_paths = sorted(self._raw_device_paths)
            mouse_paths = sorted(self._raw_mouse_paths)
            keyboard_paths = sorted(self._raw_keyboard_paths)
            pnp = list(self._pnp_devices)
            raw_mouse_count = self._raw_input_mouse_count
            raw_keyboard_count = self._raw_input_keyboard_count
            mouse_event_count = self._mouse_event_count
            keyboard_event_count = self._keyboard_event_count
            raw_mouse_dx_total = self._raw_mouse_dx_total
            raw_mouse_dy_total = self._raw_mouse_dy_total
            last_raw_mouse_delta = dict(self._last_raw_mouse_delta or {})
            last_any = self._last_any_ms
            last_mouse = self._last_mouse_ms
            last_keyboard = self._last_keyboard_ms
            last_injected = self._last_injected_ms
            warnings = list(self._warnings)
            mouse_injected = self._mouse_injected
            mouse_lower = self._mouse_lower_il
            keyboard_injected = self._keyboard_injected
            keyboard_lower = self._keyboard_lower_il
        pnp_text = json.dumps(pnp, default=str)
        raw_text = json.dumps(device_paths, default=str)
        backend_state = _read_json(self.backend_status_path)
        backend_status = backend_state.get("backendStatus") if isinstance(backend_state.get("backendStatus"), dict) else {}
        identity = backend_status.get("identity") if isinstance(backend_status.get("identity"), dict) else {}
        caps = backend_status.get("capabilities") if isinstance(backend_status.get("capabilities"), dict) else {}
        firmware_status = backend_status.get("firmwareStatus") if isinstance(backend_status.get("firmwareStatus"), dict) else {}
        protocol = identity.get("protocol")
        firmware_ok = bool(protocol == "arduino_hid.v1" and caps.get("stopAll") is True and caps.get("watchdog") is True and caps.get("resetSafe") is True)
        vid_pid_match = _contains_vid_pid(raw_text, self.expected_vid, self.expected_pid) or _contains_vid_pid(pnp_text, self.expected_vid, self.expected_pid)
        mouse_present = bool(mouse_paths or raw_mouse_count or any(str(item.get("Class", "")).lower() in {"mouse", "hidclass"} for item in pnp))
        keyboard_present = bool(keyboard_paths or raw_keyboard_count or any(str(item.get("Class", "")).lower() in {"keyboard", "hidclass"} for item in pnp))
        raw_present = bool(device_paths or pnp)
        foreground = _foreground_window_info()
        vm_focus = dict(self.vm_input_focus_safety)
        title = str(foreground.get("title") or "")
        vm_focus["foregroundWindowTitle"] = title
        vm_focus["foregroundProcess"] = foreground.get("pid")
        vm_focus["monitorWindowActive"] = bool("Arduino Input Integrity" in title)
        if vm_focus["monitorWindowActive"] and vm_focus.get("status") == "PASS":
            vm_focus["status"] = "WARN"
            warnings_for_focus = list(vm_focus.get("warnings") or [])
            warnings_for_focus.append("monitor_window_active")
            vm_focus["warnings"] = list(dict.fromkeys(str(item) for item in warnings_for_focus if item))
        return {
            "schema": "input_integrity_monitor_raw.v1",
            "generatedAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now / 1000.0)),
            "generatedAtMillis": now,
            "monitorRunning": True,
            "arduinoExpected": {
                "vid": self.expected_vid,
                "pid": self.expected_pid,
                "devicePath": self.expected_device_path,
                "comPort": self.expected_com_port,
            },
            "arduinoDetected": {
                "rawInputDevicePresent": raw_present,
                "keyboardPresent": keyboard_present,
                "mousePresent": mouse_present,
                "vidPidMatched": vid_pid_match,
                "devicePathMatched": None if not self.expected_device_path else self.expected_device_path.upper() in raw_text.upper(),
                "comPortMatched": None if not self.expected_com_port else self.expected_com_port.upper() in pnp_text.upper(),
                "rawInputDevicePaths": device_paths,
                "rawInputMouseDevicePaths": mouse_paths,
                "rawInputKeyboardDevicePaths": keyboard_paths,
                "pnpDevices": pnp,
            },
            "arduinoActivity": {
                "lastAnyEventAgeMs": None if last_any is None else max(0, now - last_any),
                "lastMouseEventAgeMs": None if last_mouse is None else max(0, now - last_mouse),
                "lastKeyboardEventAgeMs": None if last_keyboard is None else max(0, now - last_keyboard),
                "mouseEventCount": mouse_event_count,
                "keyboardEventCount": keyboard_event_count,
                "rawInputMouseCount": raw_mouse_count,
                "rawInputKeyboardCount": raw_keyboard_count,
                "rawInputMouseDxTotal": raw_mouse_dx_total,
                "rawInputMouseDyTotal": raw_mouse_dy_total,
                "lastRawMouseDelta": last_raw_mouse_delta,
            },
            "injectionFlags": {
                "mouseInjectedCount": mouse_injected,
                "mouseLowerIlInjectedCount": mouse_lower,
                "keyboardInjectedCount": keyboard_injected,
                "keyboardLowerIlInjectedCount": keyboard_lower,
                "lastInjectedEventAgeMs": None if last_injected is None else max(0, now - last_injected),
            },
            "backend": {
                "liveInputBackend": backend_state.get("liveInputBackend") or self.live_input_backend,
                "arduinoBackendSelected": self.live_input_backend == "arduino",
                "arduinoArmed": bool(backend_state.get("arduinoArmed") or backend_status.get("armed")),
                "softwareInputAllowed": False,
                "directBackendBypassCount": int(backend_state.get("directBackendBypassCount") or 0),
            },
            "firmware": {
                "status": "OK" if firmware_ok else "UNKNOWN" if not identity and not caps else "FAIL",
                "name": identity.get("name"),
                "version": identity.get("version"),
                "board": identity.get("board"),
                "protocol": protocol,
                "resetSafe": caps.get("resetSafe"),
                "stopAll": caps.get("stopAll"),
                "watchdog": caps.get("watchdog"),
                "watchdogMs": firmware_status.get("watchdogMs"),
                "armed": firmware_status.get("armed"),
                "keysDown": firmware_status.get("keysDown"),
                "mouseButtonsDown": firmware_status.get("mouseButtonsDown"),
                "lastCommandAgeMs": firmware_status.get("lastCommandAgeMs"),
            },
            "vmInputFocusSafety": vm_focus,
            "warnings": warnings,
        }

    def status(self, *, require_monitor: bool = False, arduino_armed: bool | None = None, direct_backend_bypass_count: int = 0) -> dict[str, Any]:
        raw = self.raw_payload()
        status = build_input_integrity_status(
            raw,
            status_source=self.status_output,
            expected_vid=self.expected_vid,
            expected_pid=self.expected_pid,
            expected_device_path=self.expected_device_path,
            expected_com_port=self.expected_com_port,
            live_input_backend=self.live_input_backend,
            arduino_backend_selected=self.live_input_backend == "arduino",
            arduino_armed=arduino_armed,
            direct_backend_bypass_count=direct_backend_bypass_count,
            require_monitor=require_monitor,
            require_armed=False,
            fail_on_injected=self.fail_on_injected,
            fail_on_bypass=self.fail_on_bypass,
            max_age_ms=self.max_age_ms,
        )
        raw_warnings = raw.get("warnings") if isinstance(raw.get("warnings"), list) else []
        status["warnings"] = list(dict.fromkeys(list(status.get("warnings") or []) + [str(item) for item in raw_warnings]))
        if status.get("status") == "PASS" and status["warnings"]:
            status["status"] = "WARN"
            status["monitorPass"] = False
        return status

    def write_status(self, **kwargs: Any) -> dict[str, Any]:
        payload = self.status(**kwargs)
        _safe_json_write(self.status_output, payload)
        return payload


def _print_status(payload: dict[str, Any], *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=False), flush=True)
        return
    print("\n".join(compact_status_lines(payload)), flush=True)


def _run_status_loop(monitor: InputIntegrityMonitor, args: argparse.Namespace) -> int:
    monitor.start()
    try:
        while True:
            monitor.refresh_pnp()
            payload = monitor.write_status(require_monitor=args.require_monitor, direct_backend_bypass_count=args.direct_backend_bypass_count)
            _print_status(payload, as_json=args.json)
            if args.once:
                return 0 if payload.get("status") != "FAIL" else 1
            time.sleep(max(0.05, float(args.poll_ms or 250) / 1000.0))
    except KeyboardInterrupt:
        return 0
    finally:
        monitor.stop()


def _run_overlay(monitor: InputIntegrityMonitor, args: argparse.Namespace) -> int:
    monitor.start()
    try:
        import tkinter as tk
    except Exception as error:  # noqa: BLE001
        print(f"Tk overlay unavailable, falling back to status loop: {type(error).__name__}: {error}", file=sys.stderr)
        return _run_status_loop(monitor, args)

    previous_foreground = _foreground_window_info()
    root = tk.Tk()
    root.title("Arduino Input Integrity")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    if args.overlay_passive:
        try:
            root.overrideredirect(True)
        except Exception:  # noqa: BLE001
            pass
    label = tk.Label(root, text="ARDUINO INPUT: starting", justify="left", font=("Consolas", 10), padx=10, pady=8)
    label.pack()
    passive_status = (
        _apply_passive_overlay_window(root, click_through=True, no_focus=bool(args.overlay_no_focus))
        if args.overlay_passive or args.overlay_no_focus
        else {"requested": False, "applied": False, "clickThrough": False, "noFocus": False, "warnings": []}
    )
    restored_previous = _restore_foreground_hwnd(previous_foreground.get("hwnd")) if args.overlay_no_focus else False
    monitor.vm_input_focus_safety = {
        "status": "PASS" if (passive_status.get("applied") or not (args.overlay_passive or args.overlay_no_focus)) else "WARN",
        "overlayFocusable": not bool(args.overlay_no_focus),
        "overlayClickThrough": bool(passive_status.get("clickThrough")),
        "overlayTopmost": True,
        "monitorWindowActive": False,
        "postTestFocusRecovery": "not_evaluated",
        "postTestInputState": "normal" if passive_status.get("applied") else "unknown",
        "warnings": list(passive_status.get("warnings") or []),
        "overlayPassive": bool(args.overlay_passive),
        "restoredPreviousForeground": restored_previous,
        "passiveStyle": passive_status,
    }

    def place() -> None:
        root.update_idletasks()
        width = root.winfo_width()
        height = root.winfo_height()
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        margin = 16
        corner = str(args.corner or "top_right")
        x = margin if "left" in corner else max(margin, screen_w - width - margin)
        y = margin if "top" in corner else max(margin, screen_h - height - margin)
        root.geometry(f"+{x}+{y}")

    def update() -> None:
        monitor.refresh_pnp()
        payload = monitor.write_status(require_monitor=args.require_monitor, direct_backend_bypass_count=args.direct_backend_bypass_count)
        display = overlay_display_state(payload)
        label.configure(text="\n".join(compact_status_lines(payload)), bg=display["background"], fg=display["foreground"])
        root.configure(bg=display["background"])
        place()
        root.after(max(50, int(args.poll_ms or 250)), update)

    try:
        update()
        root.mainloop()
        return 0
    finally:
        monitor.stop()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor Arduino Raw Input and injected-input flags.")
    parser.add_argument("--status-output", default=str(DEFAULT_STATUS_PATH))
    parser.add_argument("--backend-status", default=str(DEFAULT_BACKEND_STATUS_PATH))
    parser.add_argument("--status-loop", action="store_true")
    parser.add_argument("--show-overlay", action="store_true")
    parser.add_argument("--no-overlay", action="store_true")
    parser.add_argument("--overlay-passive", action="store_true", help="Make the Tk status overlay passive/click-through when possible.")
    parser.add_argument("--overlay-no-focus", action="store_true", help="Ask Windows not to activate the overlay window.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--corner", choices=["top_left", "top_right", "bottom_left", "bottom_right"], default="top_right")
    parser.add_argument("--poll-ms", type=int, default=250)
    parser.add_argument("--max-age-ms", type=int, default=3000)
    parser.add_argument("--vid", default="VID_2341")
    parser.add_argument("--pid", default="PID_8036")
    parser.add_argument("--com-port")
    parser.add_argument("--device-path")
    parser.add_argument("--live-backend", default="arduino")
    parser.add_argument("--direct-backend-bypass-count", type=int, default=0)
    parser.add_argument("--require-monitor", action="store_true")
    parser.add_argument("--fail-on-injected", dest="fail_on_injected", action="store_true", default=True)
    parser.add_argument("--no-fail-on-injected", dest="fail_on_injected", action="store_false")
    parser.add_argument("--fail-on-bypass", dest="fail_on_bypass", action="store_true", default=True)
    parser.add_argument("--no-fail-on-bypass", dest="fail_on_bypass", action="store_false")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    monitor = InputIntegrityMonitor(
        expected_vid=args.vid,
        expected_pid=args.pid,
        expected_com_port=args.com_port,
        expected_device_path=args.device_path,
        live_input_backend=args.live_backend,
        status_output=Path(args.status_output),
        backend_status_path=Path(args.backend_status),
        max_age_ms=args.max_age_ms,
        fail_on_injected=args.fail_on_injected,
        fail_on_bypass=args.fail_on_bypass,
    )
    if args.show_overlay and not args.no_overlay:
        return _run_overlay(monitor, args)
    args.once = bool(args.once or not args.status_loop)
    return _run_status_loop(monitor, args)


if __name__ == "__main__":
    raise SystemExit(main())

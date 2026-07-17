from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .input_integrity import build_input_integrity_status, input_integrity_delta, read_status_payload
from .observability import MAX_DURATION_MILLIS


ACK_TOKENS = {"ACK", "OK", "PONG", "READY"}
REQUIRED_PROTOCOL = "arduino_hid.v1"
SUPPORTED_PROTOCOLS = frozenset({"arduino_hid.v1", "arduino_hid.v2"})
REQUIRED_CAPS = ("mouse", "keyboard", "stopAll", "watchdog", "resetSafe")
ENDPOINT_CORRECTION_TOLERANCE_PX = 1
ENDPOINT_CORRECTION_ATTEMPTS = 3
DEFAULT_CLOSED_LOOP_CHUNK_PX = 12
DEFAULT_CLOSED_LOOP_TOLERANCE_PX = 3
DEFAULT_CLOSED_LOOP_FEEDBACK_TOLERANCE_PX = 8
DEFAULT_MOVE_SETTLE_MS = 80
DEFAULT_MOVE_POLL_MS = 10
DEFAULT_MOVE_NOEFFECT_TIMEOUT_MS = 200
DEFAULT_MOVE_NOEFFECT_RETRIES = 2
DEFAULT_MOVE_MIN_EFFECTIVE_PX = 2
DEFAULT_MOVE_RETRY_SCALE = 1.25
DEFAULT_MOVE_MAX_CONSECUTIVE_NOEFFECT = 3
DEFAULT_CURSOR_START_REGION_TOLERANCE_PX = 8
DEFAULT_COMMAND_TIMEOUT_MS = 2000
CAMERA_HOLD_ACK_MARGIN_MS = 500
MAX_CAMERA_HOLD_ACK_TIMEOUT_MS = 2_000
DEFAULT_SERIAL_LOCK_TIMEOUT_MS = 1500
DEFAULT_SERIAL_LOCK_STALE_MS = 120000
_GA_ROOT = 2
_PHYSICAL_MOUSE_BUTTON_VKS = (0x01, 0x02, 0x04, 0x05, 0x06)
_OWNED_MOUSE_TRANSITION_SETTLE_SECONDS = 0.01
_OWNED_MOUSE_TRANSITION_TIMEOUT_SECONDS = 0.50
_PHYSICAL_MOUSE_QUIET_DWELL_SECONDS = 0.01
_PHYSICAL_MOUSE_QUIET_CLEAR_SAMPLES = 2
_COMMAND_TERMINAL_STATUSES = {
    "PASS",
    "WRITE_FAIL",
    "ACK_TIMEOUT_OR_READ_FAIL",
    "REJECTED",
    "UNEXPECTED_RESPONSE",
}
_BOOLEAN_WIRE_FIELDS = frozenset(
    {
        "armed",
        "pointer",
        "mouse",
        "relativeMove",
        "buttonDownUp",
        "click",
        "keyboard",
        "keyPress",
        "holdKeys",
        "cameraKeyHold",
        "wheel",
        "arm",
        "watchdog",
        "stopAll",
        "disarm",
        "status",
        "resetSafe",
    }
)
_PROCESS_SERIAL_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_SERIAL_LOCKS_GUARD = threading.Lock()


class ArduinoHIDError(RuntimeError):
    pass


def _bounded_duration_millis(value: object) -> int | None:
    """Sanitize one transport duration without retaining its source payload."""

    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, min(value, MAX_DURATION_MILLIS))


@dataclass
class ArduinoHIDStatus:
    port: str | None = None
    baud: int = 115200
    connected: bool = False
    identified: bool = False
    protocol: str | None = None
    armed: bool = False
    session_token_hash: str | None = None
    command_count: int = 0
    ack_failures: int = 0
    timeouts: int = 0
    last_error: str | None = None
    identity: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    firmware_status: dict[str, Any] = field(default_factory=dict)
    stop_all_sent: bool = False
    serial_owner: str | None = None
    serial_lock: dict[str, Any] = field(default_factory=dict)
    write_timeout_ms: int = DEFAULT_COMMAND_TIMEOUT_MS
    read_timeout_ms: int = 2000
    last_command_trace: dict[str, Any] = field(default_factory=dict)
    command_trace: list[dict[str, Any]] = field(default_factory=list)
    port_reopened: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "arduino_hid_backend_status.v1",
            "port": self.port,
            "baud": self.baud,
            "connected": self.connected,
            "identified": self.identified,
            "protocol": self.protocol,
            "armed": self.armed,
            "sessionTokenHash": self.session_token_hash,
            "commandCount": self.command_count,
            "ackFailures": self.ack_failures,
            "timeouts": self.timeouts,
            "lastError": self.last_error,
            "identity": dict(self.identity),
            "capabilities": dict(self.capabilities),
            "firmwareStatus": dict(self.firmware_status),
            "stopAllSent": self.stop_all_sent,
            "serialOwner": self.serial_owner,
            "serialLock": dict(self.serial_lock),
            "writeTimeoutMs": self.write_timeout_ms,
            "readTimeoutMs": self.read_timeout_ms,
            "lastCommandTrace": dict(self.last_command_trace),
            "commandTrace": list(self.command_trace[-16:]),
            "portReopened": self.port_reopened,
        }


def _token_hash(token: str | None) -> str | None:
    if not token:
        return None
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:16]


def _json_from_line(line: str) -> dict[str, Any]:
    text = line.strip()
    if not text:
        return {}
    if " " in text:
        text = text.split(" ", 1)[1].strip()
    if not text.startswith("{"):
        return {"raw": line.strip()}
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": line.strip()}
    return decoded if isinstance(decoded, dict) else {"value": decoded}


def _fields_from_line(line: str) -> dict[str, Any]:
    text = line.strip()
    parts = text.split()
    fields: dict[str, Any] = {}
    for part in parts[2:] if len(parts) >= 2 and parts[0].upper() == "OK" else parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        normalized: Any = value
        if key in _BOOLEAN_WIRE_FIELDS and value in {"0", "1"}:
            normalized = value == "1"
        else:
            try:
                normalized = int(value)
            except ValueError:
                normalized = value
        fields[key] = normalized
    if fields:
        return fields
    parsed = _json_from_line(line)
    return parsed if isinstance(parsed, dict) else {}


def _line_token(line: str) -> str:
    return str(line or "").strip().split(" ", 1)[0].upper()


def _line_payload_token(line: str) -> str:
    parts = str(line or "").strip().split()
    if len(parts) >= 2 and parts[0].upper() == "OK":
        return parts[1].upper()
    return parts[0].upper() if parts else ""


def _command_name(command: str) -> str:
    return str(command or "").strip().split(" ", 1)[0].upper()


def _safe_port_name(port: str | None) -> str:
    text = str(port or "unknown").strip().upper() or "UNKNOWN"
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in text)


def _default_lock_dir() -> Path:
    raw = os.environ.get("OSRS_TELEMETRY_ARDUINO_LOCK_DIR")
    if raw:
        return Path(raw)
    return Path(tempfile.gettempdir()) / "osrs_telemetry_arduino_locks"


def _pid_running(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # type: ignore[attr-defined]
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
                return True
        except Exception:  # noqa: BLE001
            return False
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def _load_lock_payload(path: Path) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _command_timeout_classification(command_name: str, *, phase: str) -> str:
    name = str(command_name or "").upper()
    if name == "MOVE":
        return "serial_timeout_during_move"
    if name in {"CLICK", "MOUSE_DOWN", "MOUSE_UP"}:
        return "serial_timeout_during_click"
    if name in {"KEY_DOWN", "KEY_UP", "KEY_PRESS", "HOLD_KEYS", "CAMERA_HOLD"}:
        return "serial_timeout_during_key_input"
    if name == "WHEEL":
        return "serial_timeout_during_wheel_input"
    if phase == "read":
        return "serial_timeout_waiting_for_ack"
    return "serial_timeout_before_command"


class ArduinoSerialPortLock:
    def __init__(
        self,
        port: str | None,
        *,
        owner: str,
        lock_dir: str | Path | None = None,
        timeout_ms: int = DEFAULT_SERIAL_LOCK_TIMEOUT_MS,
        stale_ms: int = DEFAULT_SERIAL_LOCK_STALE_MS,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        self.port = str(port or "unknown")
        self.owner = str(owner or f"pid:{os.getpid()}")
        self.lock_dir = Path(lock_dir) if lock_dir is not None else _default_lock_dir()
        self.timeout_ms = max(0, int(timeout_ms or 0))
        self.stale_ms = max(1000, int(stale_ms or DEFAULT_SERIAL_LOCK_STALE_MS))
        self.sleep_func = sleep_func
        self.path = self.lock_dir / f"{_safe_port_name(self.port)}.lock"
        self.acquired = False
        self.wait_ms = 0
        self.concurrent_access_detected = False
        self.owner_payload: dict[str, Any] | None = None
        self._process_lock: threading.Lock | None = None

    def acquire(self) -> dict[str, Any]:
        key = _safe_port_name(self.port)
        with _PROCESS_SERIAL_LOCKS_GUARD:
            lock = _PROCESS_SERIAL_LOCKS.setdefault(key, threading.Lock())
        start = time.monotonic()
        while not lock.acquire(blocking=False):
            self.concurrent_access_detected = True
            elapsed_ms = int(round((time.monotonic() - start) * 1000))
            if elapsed_ms >= self.timeout_ms:
                self.wait_ms = elapsed_ms
                self.owner_payload = {"pid": os.getpid(), "owner": "same_process_thread"}
                raise ArduinoHIDError(f"Arduino serial port {self.port} is already owned by this process")
            self.sleep_func(0.05)
        self._process_lock = lock
        try:
            self.lock_dir.mkdir(parents=True, exist_ok=True)
            while True:
                now_ms = int(round(time.time() * 1000))
                payload = {
                    "schema": "arduino_serial_lock.v1",
                    "port": self.port,
                    "pid": os.getpid(),
                    "owner": self.owner,
                    "createdAtMillis": now_ms,
                    "createdAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000.0)),
                }
                try:
                    fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(json.dumps(payload, sort_keys=True) + "\n")
                    self.acquired = True
                    self.wait_ms = int(round((time.monotonic() - start) * 1000))
                    self.owner_payload = payload
                    return self.to_dict()
                except FileExistsError:
                    self.concurrent_access_detected = True
                    existing = _load_lock_payload(self.path)
                    existing_pid = existing.get("pid")
                    try:
                        existing_pid_int = int(existing_pid) if existing_pid is not None else None
                    except (TypeError, ValueError):
                        existing_pid_int = None
                    created = existing.get("createdAtMillis")
                    try:
                        age_ms = now_ms - int(created)
                    except (TypeError, ValueError):
                        age_ms = self.stale_ms + 1
                    if age_ms > self.stale_ms and not _pid_running(existing_pid_int):
                        try:
                            self.path.unlink()
                            continue
                        except FileNotFoundError:
                            continue
                        except Exception:
                            pass
                    elapsed_ms = int(round((time.monotonic() - start) * 1000))
                    if elapsed_ms >= self.timeout_ms:
                        self.wait_ms = elapsed_ms
                        self.owner_payload = existing
                        raise ArduinoHIDError(f"Arduino serial port {self.port} is locked by {existing.get('owner') or existing.get('pid') or 'unknown owner'}")
                    self.sleep_func(0.05)
        except Exception:
            if self._process_lock is not None:
                try:
                    self._process_lock.release()
                except RuntimeError:
                    pass
                self._process_lock = None
            raise

    def release(self) -> None:
        if self.acquired:
            try:
                payload = _load_lock_payload(self.path)
                if int(payload.get("pid") or -1) == os.getpid() and str(payload.get("owner") or "") == self.owner:
                    self.path.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
        self.acquired = False
        if self._process_lock is not None:
            try:
                self._process_lock.release()
            except RuntimeError:
                pass
            self._process_lock = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "arduino_serial_session_lock.v1",
            "port": self.port,
            "serialOwner": self.owner,
            "lockPath": str(self.path),
            "lockAcquired": bool(self.acquired),
            "lockWaitMs": int(self.wait_ms or 0),
            "concurrentAccessDetected": bool(self.concurrent_access_detected),
            "owner": dict(self.owner_payload or {}),
        }


def _expected_response_token(command: str) -> str | None:
    name = _command_name(command)
    mapping = {
        "PING": "PONG",
        "IDENTIFY": "IDENTIFY",
        "CAPS": "CAPS",
        "STATUS": "STATUS",
        "ARM": "ARMED",
        "DISARM": "DISARMED",
        "STOP_ALL": "STOP_ALL",
        "MOVE": "MOVE",
        "MOUSE_DOWN": "MOUSE_DOWN",
        "MOUSE_UP": "MOUSE_UP",
        "CLICK": "CLICK",
        "KEY_DOWN": "KEY_DOWN",
        "KEY_UP": "KEY_UP",
        "KEY_PRESS": "KEY_PRESS",
        "HOLD_KEYS": "HOLD_KEYS",
        "CAMERA_HOLD": "CAMERA_HOLD",
        "WHEEL": "WHEEL",
    }
    return mapping.get(name)


def _ensure_cursor_dpi_awareness(user32: Any) -> None:
    """Put the calling thread in the device-pixel coordinate space.

    Every cursor sample must verify the current thread instead of trusting a
    process-global flag.  Live execution can enter through a worker thread or
    a fresh CLI process, and DPI virtualization would otherwise make a real
    cursor position look like a different in-window point.
    """
    if os.name != "nt":
        raise ArduinoHIDError(
            "DPI-aware cursor sampling is supported only on Windows"
        )
    context_getter = getattr(user32, "GetThreadDpiAwarenessContext", None)
    contexts_equal = getattr(user32, "AreDpiAwarenessContextsEqual", None)
    thread_setter = getattr(user32, "SetThreadDpiAwarenessContext", None)
    if not all(
        callable(function)
        for function in (context_getter, contexts_equal, thread_setter)
    ):
        raise ArduinoHIDError(
            "Windows per-monitor-v2 cursor DPI APIs are unavailable"
        )

    context_getter.argtypes = ()
    context_getter.restype = ctypes.c_void_p
    contexts_equal.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    contexts_equal.restype = ctypes.c_bool
    thread_setter.argtypes = (ctypes.c_void_p,)
    thread_setter.restype = ctypes.c_void_p

    bits = ctypes.sizeof(ctypes.c_void_p) * 8
    per_monitor_v2 = ctypes.c_void_p((-4) & ((1 << bits) - 1))

    def is_per_monitor_v2_aware() -> bool:
        active_context = context_getter()
        return bool(active_context) and bool(
            contexts_equal(active_context, per_monitor_v2)
        )

    if not is_per_monitor_v2_aware():
        previous_context = thread_setter(per_monitor_v2)
        if not previous_context or not is_per_monitor_v2_aware():
            raise ArduinoHIDError(
                "Windows per-monitor-v2 cursor DPI awareness could not be "
                "established"
            )


def _cursor_position(user32: Any | None = None) -> tuple[int, int]:
    try:
        user32 = user32 or ctypes.windll.user32  # type: ignore[attr-defined]
        _ensure_cursor_dpi_awareness(user32)
        point = wintypes.POINT()
        getter = getattr(user32, "GetCursorPos", None)
        if not callable(getter):
            raise ArduinoHIDError("Windows GetCursorPos is unavailable")
        getter.argtypes = (ctypes.POINTER(wintypes.POINT),)
        getter.restype = wintypes.BOOL
        if getter(ctypes.byref(point)):
            return int(point.x), int(point.y)
        raise ArduinoHIDError("Windows GetCursorPos failed")
    except ArduinoHIDError:
        raise
    except Exception as error:  # noqa: BLE001
        raise ArduinoHIDError(
            f"DPI-aware Windows cursor sampling failed: {error}"
        ) from error


def _handle_int(value: Any) -> int:
    return int(getattr(value, "value", value) or 0)


def _strict_coordinate_tuple(
    value: tuple[int, ...],
    *,
    length: int,
    label: str,
) -> tuple[int, ...]:
    if (
        not isinstance(value, tuple)
        or len(value) != length
        or any(not isinstance(item, int) or isinstance(item, bool) for item in value)
    ):
        raise ArduinoHIDError(f"{label} must contain exactly {length} integers")
    return value


def _bounds_payload(bounds: tuple[int, int, int, int]) -> dict[str, int]:
    x, y, width, height = bounds
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "right": x + width,
        "bottom": y + height,
    }


def _window_pid(user32: Any, hwnd: int) -> int:
    getter = getattr(user32, "GetWindowThreadProcessId", None)
    if not callable(getter):
        raise ArduinoHIDError("Windows GetWindowThreadProcessId is unavailable")
    getter.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    getter.restype = wintypes.DWORD
    pid = wintypes.DWORD()
    if not getter(wintypes.HWND(hwnd), ctypes.byref(pid)) or int(pid.value) <= 0:
        raise ArduinoHIDError("Windows could not resolve the RuneLite window PID")
    return int(pid.value)


def _exact_foreground_root_window(
    user32: Any,
    *,
    expected_pid: int,
    expected_hwnd: int,
) -> None:
    foreground_getter = getattr(user32, "GetForegroundWindow", None)
    ancestor_getter = getattr(user32, "GetAncestor", None)
    if not callable(foreground_getter) or not callable(ancestor_getter):
        raise ArduinoHIDError("Windows foreground-root APIs are unavailable")
    foreground_getter.argtypes = ()
    foreground_getter.restype = wintypes.HWND
    ancestor_getter.argtypes = (wintypes.HWND, wintypes.UINT)
    ancestor_getter.restype = wintypes.HWND
    foreground = _handle_int(foreground_getter())
    root = _handle_int(
        ancestor_getter(wintypes.HWND(foreground), _GA_ROOT)
    )
    if root != expected_hwnd:
        raise ArduinoHIDError(
            "foreground root HWND changed during client geometry verification"
        )
    if _window_pid(user32, root) != expected_pid:
        raise ArduinoHIDError(
            "foreground root PID changed during client geometry verification"
        )


def _physical_mouse_button_states(user32: Any) -> dict[int, int]:
    getter = getattr(user32, "GetAsyncKeyState", None)
    if not callable(getter):
        raise ArduinoHIDError("Windows GetAsyncKeyState is unavailable")
    getter.argtypes = (ctypes.c_int,)
    getter.restype = ctypes.c_short
    return {vk: int(getter(vk)) for vk in _PHYSICAL_MOUSE_BUTTON_VKS}


def _require_physical_mouse_buttons_up(user32: Any) -> None:
    states = _physical_mouse_button_states(user32)
    active = [
        vk for vk, state in states.items() if state & 0x8001
    ]
    if active:
        raise ArduinoHIDError(
            "physical mouse button held or pressed during guarded operation"
        )


def _verify_physical_mouse_quiet(
    user32: Any | None = None,
) -> dict[str, Any]:
    """Prove all physical mouse buttons are up with no queued activity."""

    try:
        user32 = user32 or ctypes.windll.user32  # type: ignore[attr-defined]
        _ensure_cursor_dpi_awareness(user32)
        initial = _physical_mouse_button_states(user32)
        if any(state & 0x8000 for state in initial.values()):
            raise ArduinoHIDError(
                "physical mouse button held during quiet baseline"
            )
        historical_activity_consumed = any(
            state & 0x0001 for state in initial.values()
        )
        sample_count = 1
        for _ in range(_PHYSICAL_MOUSE_QUIET_CLEAR_SAMPLES):
            time.sleep(_PHYSICAL_MOUSE_QUIET_DWELL_SECONDS)
            sample = _physical_mouse_button_states(user32)
            sample_count += 1
            if any(state & 0x8001 for state in sample.values()):
                raise ArduinoHIDError(
                    "physical mouse activity detected during quiet dwell"
                )
        return {
            "schema": "physical_mouse_quiet.v1",
            "buttonsUp": True,
            "activityClear": True,
            "historicalActivityConsumed": historical_activity_consumed,
            "sampleCount": sample_count,
        }
    except ArduinoHIDError:
        raise
    except Exception as error:  # noqa: BLE001
        raise ArduinoHIDError(
            f"DPI-aware physical mouse quiet verification failed: {error}"
        ) from error


def _verify_physical_mouse_buttons_released(
    user32: Any | None = None,
) -> dict[str, Any]:
    """Take one nonblocking held/activity sample during Arduino movement."""

    try:
        user32 = user32 or ctypes.windll.user32  # type: ignore[attr-defined]
        _ensure_cursor_dpi_awareness(user32)
        states = _physical_mouse_button_states(user32)
        if any(state & 0x8001 for state in states.values()):
            raise ArduinoHIDError(
                "physical mouse button held or pressed during cursor reacquisition"
            )
        return {
            "schema": "physical_mouse_buttons_released.v1",
            "buttonsUp": True,
            "activityClear": True,
        }
    except ArduinoHIDError:
        raise
    except Exception as error:  # noqa: BLE001
        raise ArduinoHIDError(
            f"DPI-aware physical mouse button sampling failed: {error}"
        ) from error


def _virtual_desktop_bounds(user32: Any | None = None) -> dict[str, Any]:
    """Return the current PMv2 Win32 virtual desktop in device pixels."""

    try:
        user32 = user32 or ctypes.windll.user32  # type: ignore[attr-defined]
        _ensure_cursor_dpi_awareness(user32)
        getter = getattr(user32, "GetSystemMetrics", None)
        if not callable(getter):
            raise ArduinoHIDError("Windows GetSystemMetrics is unavailable")
        getter.argtypes = (ctypes.c_int,)
        getter.restype = ctypes.c_int
        left = int(getter(76))  # SM_XVIRTUALSCREEN
        top = int(getter(77))  # SM_YVIRTUALSCREEN
        width = int(getter(78))  # SM_CXVIRTUALSCREEN
        height = int(getter(79))  # SM_CYVIRTUALSCREEN
        if width <= 0 or height <= 0:
            raise ArduinoHIDError(
                "Windows virtual desktop has invalid physical dimensions"
            )
        return {
            "schema": "virtual_desktop_geometry.v1",
            "coordinateSpace": "device_pixels_pm_v2",
            "bounds": _bounds_payload((left, top, width, height)),
        }
    except ArduinoHIDError:
        raise
    except Exception as error:  # noqa: BLE001
        raise ArduinoHIDError(
            f"DPI-aware virtual desktop sampling failed: {error}"
        ) from error


def _consume_owned_mouse_transition(
    *,
    button: str,
    user32: Any | None = None,
) -> dict[str, Any]:
    """Consume only one acknowledged Arduino button-release transition."""

    if not isinstance(button, str) or button.strip().lower() not in {
        "left",
        "right",
    }:
        raise ArduinoHIDError(
            "owned mouse transition button must be left or right"
        )
    canonical_button = button.strip().lower()
    owned_vk = 0x01 if canonical_button == "left" else 0x02
    try:
        user32 = user32 or ctypes.windll.user32  # type: ignore[attr-defined]
        _ensure_cursor_dpi_awareness(user32)
        deadline = time.monotonic() + _OWNED_MOUSE_TRANSITION_TIMEOUT_SECONDS
        states = _physical_mouse_button_states(user32)
        owned_transition_consumed = False
        consecutive_clear_samples = 0
        while True:
            if any(
                vk != owned_vk and state & 0x8001
                for vk, state in states.items()
            ):
                raise ArduinoHIDError(
                    "unowned physical mouse activity during owned transition consumption"
                )
            owned_state = states[owned_vk]
            expected_transition = bool(owned_state & 0x0001)
            owned_transition_consumed = (
                owned_transition_consumed or expected_transition
            )
            if owned_state & 0x8001:
                consecutive_clear_samples = 0
            else:
                consecutive_clear_samples += 1
            if consecutive_clear_samples >= 2:
                if time.monotonic() > deadline:
                    raise ArduinoHIDError(
                        "owned physical mouse transition did not settle before deadline"
                    )
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ArduinoHIDError(
                    "owned physical mouse transition did not settle before deadline"
                )
            time.sleep(
                min(_OWNED_MOUSE_TRANSITION_SETTLE_SECONDS, remaining)
            )
            states = _physical_mouse_button_states(user32)
        return {
            "schema": "owned_mouse_transition.v1",
            "button": canonical_button,
            "ownedTransitionConsumed": owned_transition_consumed,
            "buttonsUp": True,
            "activityClear": True,
        }
    except ArduinoHIDError:
        raise
    except Exception as error:  # noqa: BLE001
        raise ArduinoHIDError(
            f"owned physical mouse transition verification failed: {error}"
        ) from error


def _window_rect(user32: Any, hwnd: int) -> tuple[int, int, int, int]:
    getter = getattr(user32, "GetWindowRect", None)
    if not callable(getter):
        raise ArduinoHIDError("Windows GetWindowRect is unavailable")
    getter.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    getter.restype = wintypes.BOOL
    rect = wintypes.RECT()
    if not getter(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise ArduinoHIDError("Windows GetWindowRect failed")
    width = int(rect.right) - int(rect.left)
    height = int(rect.bottom) - int(rect.top)
    if width <= 0 or height <= 0:
        raise ArduinoHIDError("RuneLite window has invalid physical dimensions")
    return int(rect.left), int(rect.top), width, height


def _verify_window_geometry(
    *,
    expected_pid: int,
    expected_hwnd: int,
    expected_outer_bounds: tuple[int, int, int, int] | None,
    expected_client_bounds: tuple[int, int, int, int] | None,
    required_inner_bounds: tuple[int, int, int, int],
    user32: Any | None = None,
) -> dict[str, Any]:
    """Prove outer, client, and required-inner geometry without mutation."""

    if (
        not isinstance(expected_pid, int)
        or isinstance(expected_pid, bool)
        or expected_pid <= 0
        or not isinstance(expected_hwnd, int)
        or isinstance(expected_hwnd, bool)
        or expected_hwnd <= 0
    ):
        raise ArduinoHIDError(
            "window geometry verification requires positive PID and HWND"
        )

    def validated_bounds(
        value: tuple[int, int, int, int] | None,
        *,
        label: str,
        optional: bool,
    ) -> tuple[int, int, int, int] | None:
        if value is None:
            if optional:
                return None
            raise ArduinoHIDError(f"{label} are required")
        values = _strict_coordinate_tuple(value, length=4, label=label)
        result = (values[0], values[1], values[2], values[3])
        if result[2] <= 0 or result[3] <= 0:
            raise ArduinoHIDError(f"{label} dimensions must be positive")
        return result

    expected_outer = validated_bounds(
        expected_outer_bounds,
        label="expected outer bounds",
        optional=True,
    )
    expected_client = validated_bounds(
        expected_client_bounds,
        label="expected client bounds",
        optional=True,
    )
    required_inner_value = validated_bounds(
        required_inner_bounds,
        label="required inner bounds",
        optional=False,
    )
    if required_inner_value is None:
        raise ArduinoHIDError("required inner bounds are required")
    required_inner = required_inner_value

    try:
        user32 = user32 or ctypes.windll.user32  # type: ignore[attr-defined]
        _ensure_cursor_dpi_awareness(user32)
        required_functions = (
            "IsWindow",
            "IsWindowVisible",
            "GetAncestor",
            "IsIconic",
            "GetWindowRect",
            "GetClientRect",
            "ClientToScreen",
        )
        if any(
            not callable(getattr(user32, name, None))
            for name in required_functions
        ):
            raise ArduinoHIDError(
                "required Windows window geometry API is unavailable"
            )
        user32.IsWindow.argtypes = (wintypes.HWND,)
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = (wintypes.HWND,)
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.IsIconic.argtypes = (wintypes.HWND,)
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetClientRect.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        )
        user32.GetClientRect.restype = wintypes.BOOL
        user32.ClientToScreen.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.POINT),
        )
        user32.ClientToScreen.restype = wintypes.BOOL

        hwnd = wintypes.HWND(expected_hwnd)
        if not user32.IsWindow(hwnd):
            raise ArduinoHIDError("expected RuneLite HWND is not a window")
        if not user32.IsWindowVisible(hwnd):
            raise ArduinoHIDError("expected RuneLite window is not visible")
        if _handle_int(user32.GetAncestor(hwnd, _GA_ROOT)) != expected_hwnd:
            raise ArduinoHIDError("expected RuneLite window is not top-level")
        if user32.IsIconic(hwnd):
            raise ArduinoHIDError("expected RuneLite window is minimized")
        if _window_pid(user32, expected_hwnd) != expected_pid:
            raise ArduinoHIDError(
                "expected RuneLite HWND belongs to a different PID"
            )
        _exact_foreground_root_window(
            user32,
            expected_pid=expected_pid,
            expected_hwnd=expected_hwnd,
        )

        actual_outer = _window_rect(user32, expected_hwnd)
        client_rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(client_rect)):
            raise ArduinoHIDError("Windows GetClientRect failed")
        client_left = int(client_rect.left)
        client_top = int(client_rect.top)
        client_width = int(client_rect.right) - client_left
        client_height = int(client_rect.bottom) - client_top
        if client_left != 0 or client_top != 0:
            raise ArduinoHIDError("Windows GetClientRect returned a nonzero origin")
        if client_width <= 0 or client_height <= 0:
            raise ArduinoHIDError("RuneLite client has invalid physical dimensions")
        origin = wintypes.POINT(client_left, client_top)
        if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise ArduinoHIDError("Windows ClientToScreen failed")
        actual_client = (
            int(origin.x),
            int(origin.y),
            client_width,
            client_height,
        )
        if _window_rect(user32, expected_hwnd) != actual_outer:
            raise ArduinoHIDError(
                "RuneLite outer geometry changed during verification"
            )
        _exact_foreground_root_window(
            user32,
            expected_pid=expected_pid,
            expected_hwnd=expected_hwnd,
        )

        inner_x, inner_y, inner_width, inner_height = required_inner
        client_x, client_y, client_width, client_height = actual_client
        inner_contained = (
            client_x <= inner_x
            and client_y <= inner_y
            and inner_x + inner_width <= client_x + client_width
            and inner_y + inner_height <= client_y + client_height
        )
        return {
            "schema": "cursor_window_geometry.v1",
            "expectedPid": expected_pid,
            "expectedHwnd": expected_hwnd,
            "expectedOuterBounds": (
                _bounds_payload(expected_outer)
                if expected_outer is not None
                else None
            ),
            "expectedClientBounds": (
                _bounds_payload(expected_client)
                if expected_client is not None
                else None
            ),
            "requiredInnerBounds": _bounds_payload(required_inner),
            "actualOuterBounds": _bounds_payload(actual_outer),
            "actualClientBounds": _bounds_payload(actual_client),
            "outerMatches": (
                actual_outer == expected_outer
                if expected_outer is not None
                else None
            ),
            "clientMatches": (
                actual_client == expected_client
                if expected_client is not None
                else None
            ),
            "innerContainedByClient": inner_contained,
        }
    except ArduinoHIDError:
        raise
    except Exception as error:  # noqa: BLE001
        raise ArduinoHIDError(
            f"DPI-aware RuneLite window geometry verification failed: {error}"
        ) from error


def _rect_from_region(region: dict[str, Any] | None) -> dict[str, int] | None:
    if not isinstance(region, dict):
        return None
    x = region.get("x", region.get("left"))
    y = region.get("y", region.get("top"))
    width = region.get("width")
    height = region.get("height")
    right = region.get("right")
    bottom = region.get("bottom")
    try:
        left_i = int(round(float(x)))
        top_i = int(round(float(y)))
        if width is not None and height is not None:
            width_i = int(round(float(width)))
            height_i = int(round(float(height)))
            right_i = left_i + width_i
            bottom_i = top_i + height_i
        elif right is not None and bottom is not None:
            right_i = int(round(float(right)))
            bottom_i = int(round(float(bottom)))
            width_i = right_i - left_i
            height_i = bottom_i - top_i
        else:
            return None
    except (TypeError, ValueError):
        return None
    if width_i <= 0 or height_i <= 0:
        return None
    return {"x": left_i, "y": top_i, "width": width_i, "height": height_i, "right": right_i, "bottom": bottom_i}


def _point_dict(point: tuple[int, int] | dict[str, Any] | None) -> dict[str, int] | None:
    if isinstance(point, dict):
        try:
            return {"x": int(round(float(point.get("x")))), "y": int(round(float(point.get("y"))))}
        except (TypeError, ValueError):
            return None
    if isinstance(point, tuple) and len(point) >= 2:
        return {"x": int(point[0]), "y": int(point[1])}
    return None


def _point_in_region(point: tuple[int, int] | dict[str, Any] | None, region: dict[str, Any] | None, *, margin: int = 0) -> bool:
    rect = _rect_from_region(region)
    point_dict = _point_dict(point)
    if not rect or not point_dict:
        return False
    margin_i = max(0, int(margin or 0))
    return (
        int(rect["x"]) + margin_i <= int(point_dict["x"]) <= int(rect["right"]) - margin_i
        and int(rect["y"]) + margin_i <= int(point_dict["y"]) <= int(rect["bottom"]) - margin_i
    )


def _point_near_region(point: tuple[int, int] | dict[str, Any] | None, region: dict[str, Any] | None, *, tolerance: int = 0) -> bool:
    rect = _rect_from_region(region)
    point_dict = _point_dict(point)
    if not rect or not point_dict:
        return False
    tolerance_i = max(0, int(tolerance or 0))
    return (
        int(rect["x"]) - tolerance_i <= int(point_dict["x"]) <= int(rect["right"]) + tolerance_i
        and int(rect["y"]) - tolerance_i <= int(point_dict["y"]) <= int(rect["bottom"]) + tolerance_i
    )


def _foreground_window_info() -> dict[str, Any]:
    if os.name != "nt":
        return {"available": False}
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        foreground = user32.GetForegroundWindow()
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = user32.GetAncestor(foreground, _GA_ROOT) or foreground
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return {"available": True, "hwnd": int(hwnd), "title": buffer.value, "pid": int(pid.value)}
    except Exception as error:  # noqa: BLE001
        return {"available": False, "error": f"{type(error).__name__}: {error}"}


def _window_info_at_point(
    point: tuple[int, int], user32: Any | None = None
) -> dict[str, Any]:
    if os.name != "nt":
        return {"available": False}
    try:
        user32 = user32 or ctypes.windll.user32  # type: ignore[attr-defined]
        _ensure_cursor_dpi_awareness(user32)
        user32.WindowFromPoint.argtypes = (wintypes.POINT,)
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        child = user32.WindowFromPoint(wintypes.POINT(int(point[0]), int(point[1])))
        root = user32.GetAncestor(child, 2) or child  # GA_ROOT
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
        return {
            "available": bool(root),
            "hwnd": int(root or 0),
            "pid": int(pid.value),
        }
    except Exception as error:  # noqa: BLE001
        return {"available": False, "error": f"{type(error).__name__}: {error}"}


def _foreground_allowed(info: dict[str, Any] | None, allowed_titles: list[str] | tuple[str, ...] | None) -> bool:
    if not allowed_titles:
        return True
    title = str((info or {}).get("title") or "").lower()
    return any(str(item or "").lower() in title for item in allowed_titles)


def _movement_abort_trace(reason: str, trace: dict[str, Any]) -> None:
    trace["movementAbortedReason"] = reason
    trace["status"] = "FAIL"
    raise ArduinoHIDError(reason)


def _target_error(point: tuple[int, int] | dict[str, Any] | None, target: dict[str, int] | None) -> int | None:
    point_dict = _point_dict(point)
    if not point_dict or not target:
        return None
    return max(abs(int(point_dict["x"]) - int(target["x"])), abs(int(point_dict["y"]) - int(target["y"])))


def _raw_input_seen(delta: dict[str, Any] | None) -> bool:
    delta = delta if isinstance(delta, dict) else {}
    return (
        int(delta.get("rawInputMouseCountDelta") or 0) > 0
        or int(delta.get("rawInputMouseDxDelta") or 0) != 0
        or int(delta.get("rawInputMouseDyDelta") or 0) != 0
    )


def _cursor_delta_tuple(before: tuple[int, int], after: tuple[int, int]) -> tuple[int, int]:
    return int(after[0]) - int(before[0]), int(after[1]) - int(before[1])


def _cursor_moved_expected_direction(
    before: tuple[int, int],
    after: tuple[int, int],
    commanded_delta: tuple[int, int],
    *,
    min_effective_px: int,
) -> bool:
    actual_dx, actual_dy = _cursor_delta_tuple(before, after)
    command_x, command_y = int(commanded_delta[0]), int(commanded_delta[1])
    threshold_x = min(abs(command_x), max(1, int(min_effective_px or 1))) if command_x else 0
    threshold_y = min(abs(command_y), max(1, int(min_effective_px or 1))) if command_y else 0
    checks: list[bool] = []
    if command_x:
        checks.append((actual_dx > 0 if command_x > 0 else actual_dx < 0) and abs(actual_dx) >= threshold_x)
    if command_y:
        checks.append((actual_dy > 0 if command_y > 0 else actual_dy < 0) and abs(actual_dy) >= threshold_y)
    return any(checks)


class _ArduinoHIDTransport:
    name = "arduino"
    arduino_hid_backend = True
    live_input_backend = True
    software_input_backend = False
    requires_arming = True

    def __init__(
        self,
        *,
        port: str | None = None,
        baud: int = 115200,
        handshake_timeout_ms: int = 2000,
        command_timeout_ms: int = DEFAULT_COMMAND_TIMEOUT_MS,
        session_token: str | None = None,
        fail_closed: bool = True,
        vid: str | None = None,
        pid: str | None = None,
        serial_factory: Callable[..., Any] | None = None,
        sleep_func: Callable[[float], None] = time.sleep,
        serial_lock_enabled: bool | None = None,
        serial_lock_dir: str | Path | None = None,
        serial_lock_timeout_ms: int = DEFAULT_SERIAL_LOCK_TIMEOUT_MS,
        serial_lock_stale_ms: int = DEFAULT_SERIAL_LOCK_STALE_MS,
        serial_owner: str | None = None,
    ) -> None:
        self.port = port or os.environ.get("OSRS_TELEMETRY_ARDUINO_PORT")
        self.baud = int(baud or 115200)
        self.handshake_timeout_ms = max(1, int(handshake_timeout_ms or 2000))
        self.command_timeout_ms = max(1, int(command_timeout_ms or DEFAULT_COMMAND_TIMEOUT_MS))
        self.session_token = session_token if session_token and session_token != "auto" else secrets.token_hex(8)
        self.fail_closed = bool(fail_closed)
        self.expected_vid = vid
        self.expected_pid = pid
        self.serial_factory = serial_factory
        self.sleep_func = sleep_func
        self.serial_lock_enabled = bool(serial_lock_enabled) if serial_lock_enabled is not None else serial_factory is None
        self.serial_lock_dir = serial_lock_dir
        self.serial_lock_timeout_ms = max(0, int(serial_lock_timeout_ms or DEFAULT_SERIAL_LOCK_TIMEOUT_MS))
        self.serial_lock_stale_ms = max(1000, int(serial_lock_stale_ms or DEFAULT_SERIAL_LOCK_STALE_MS))
        self.serial_owner = serial_owner or f"{Path(os.environ.get('OSRS_TELEMETRY_SERIAL_OWNER') or 'osrs-telemetry').name}:{os.getpid()}"
        self._serial_lock: ArduinoSerialPortLock | None = None
        self._serial: Any | None = None
        self._status = ArduinoHIDStatus(
            port=self.port,
            baud=self.baud,
            session_token_hash=_token_hash(self.session_token),
            serial_owner=self.serial_owner,
            write_timeout_ms=self.command_timeout_ms,
            read_timeout_ms=self.handshake_timeout_ms,
        )
        self._tracked_position: tuple[int, int] | None = None
        self._live_session_active = False
        self._movement_safety: dict[str, Any] | None = None
        self.last_movement_trace: dict[str, Any] | None = None
        self._command_sequence = 0
        self._command_ledger_active = False
        self._command_ledger_order: list[int] = []
        self._command_ledger_records: dict[int, dict[str, Any]] = {}
        self._negotiated_input_capabilities: Any | None = None

    def __del__(self) -> None:
        try:
            if self._serial is not None:
                try:
                    self._stop_all()
                except Exception:
                    pass
            if self._armed:
                self._disarm()
            self._close()
        except Exception:  # noqa: BLE001
            pass

    @property
    def _armed(self) -> bool:
        return bool(self._status.armed)

    def _status_snapshot(self) -> dict[str, Any]:
        return self._status.to_dict()

    def _connect(self) -> None:
        if self._serial is not None:
            return
        self._acquire_input_lease()
        try:
            factory = self.serial_factory
            if factory is None:
                try:
                    import serial  # type: ignore
                except ImportError as error:
                    raise ArduinoHIDError("pyserial is not installed. Install with: pip install pyserial") from error
                factory = serial.Serial
            self._serial = factory(
                self.port,
                self.baud,
                timeout=self.handshake_timeout_ms / 1000.0,
                write_timeout=self.command_timeout_ms / 1000.0,
                rtscts=False,
                dsrdtr=False,
            )
            self._status.connected = True
            self._status.serial_owner = self.serial_owner
            self._status.write_timeout_ms = self.command_timeout_ms
            self._status.read_timeout_ms = self.handshake_timeout_ms
            self._status.last_error = None
            self._reset_serial_buffers()
        except Exception as error:  # noqa: BLE001
            self._status.connected = False
            self._status.last_error = f"{type(error).__name__}: {error}"
            self._close()
            raise

    def _acquire_input_lease(self) -> None:
        """Own the cross-process input/handoff lane without opening hardware."""

        if not self.port:
            raise ArduinoHIDError("Arduino serial port is not configured; pass --arduino-port COMx or set OSRS_TELEMETRY_ARDUINO_PORT")
        if self.serial_lock_enabled and self._serial_lock is None:
            lock = ArduinoSerialPortLock(
                self.port,
                owner=self.serial_owner,
                lock_dir=self.serial_lock_dir,
                timeout_ms=self.serial_lock_timeout_ms,
                stale_ms=self.serial_lock_stale_ms,
                sleep_func=self.sleep_func,
            )
            try:
                self._status.serial_lock = lock.acquire()
            except Exception as error:
                self._status.serial_lock = lock.to_dict()
                self._status.last_error = f"{type(error).__name__}: {error}"
                raise
            self._serial_lock = lock

    def _close(self) -> None:
        serial_obj = self._serial
        self._serial = None
        self._status.connected = False
        self._status.armed = False
        self._live_session_active = False
        close_error: Exception | None = None
        if serial_obj is not None:
            try:
                serial_obj.close()
            except Exception as error:  # noqa: BLE001
                close_error = error
                self._status.last_error = (
                    f"serial_close_failed: {type(error).__name__}: {error}"
                )
        if self._serial_lock is not None:
            serial_lock = self._serial_lock
            try:
                serial_lock.release()
                self._status.serial_lock = serial_lock.to_dict()
            except Exception as error:  # noqa: BLE001
                if close_error is None:
                    close_error = error
                self._status.last_error = (
                    f"serial_lock_release_failed: {type(error).__name__}: {error}"
                )
            finally:
                self._serial_lock = None
        if close_error is not None:
            raise ArduinoHIDError("Arduino serial transport close failed") from close_error

    def _reset_serial_buffers(self) -> None:
        if self._serial is None:
            return
        for name in ("reset_input_buffer", "reset_output_buffer"):
            method = getattr(self._serial, name, None)
            if callable(method):
                try:
                    method()
                except Exception:  # noqa: BLE001
                    pass

    def _append_command_trace(self, trace: dict[str, Any]) -> None:
        self._status.last_command_trace = dict(trace)
        self._status.command_trace.append(dict(trace))
        if len(self._status.command_trace) > 32:
            self._status.command_trace = self._status.command_trace[-32:]
        command_id = trace.get("commandId")
        if (
            self._command_ledger_active
            and isinstance(command_id, int)
            and not isinstance(command_id, bool)
        ):
            if command_id not in self._command_ledger_records:
                self._command_ledger_order.append(command_id)
            self._command_ledger_records[command_id] = dict(trace)

    def _begin_command_ledger(self) -> None:
        """Begin one coordinator-owned wire-command ledger."""

        if self._command_ledger_active:
            raise ArduinoHIDError("Arduino command ledger is already active")
        self._command_ledger_active = True
        self._command_ledger_order = []
        self._command_ledger_records = {}

    def _command_evidence(self) -> dict[str, Any]:
        """Return a redacted, non-truncating snapshot of the active ledger."""

        records: list[dict[str, Any]] = []
        for command_id in self._command_ledger_order:
            raw = self._command_ledger_records[command_id]
            status = str(raw.get("status") or "PENDING")
            command_name = str(raw.get("commandName") or "UNKNOWN")
            response_token = raw.get("responseToken")
            payload_token = raw.get("payloadToken")
            ack_received = bool(
                status
                in {
                    "PASS",
                    "REJECTED",
                    "UNEXPECTED_RESPONSE",
                }
                or raw.get("ackLine")
            )
            record: dict[str, Any] = {
                "schema": "arduino_command_evidence.v1",
                "commandId": f"cmd-{command_id:08d}",
                "sequence": command_id,
                "command": command_name,
                "status": status,
                "writeOk": status not in {"PENDING", "WRITE_FAIL"},
                "ackReceived": ack_received,
                "accepted": status == "PASS",
                "firmwareAck": {
                    "responseToken": response_token,
                    "payloadToken": payload_token,
                }
                if ack_received
                else None,
                "error": (
                    "ARM command failed"
                    if command_name == "ARM" and raw.get("error")
                    else raw.get("error")
                ),
                "timeoutClassification": raw.get("timeoutClassification"),
                "retryCount": int(raw.get("retryCount") or 0),
                # These values are already measured by the authoritative
                # transport.  Publish only bounded numeric durations: never
                # the raw command line, ACK line, serial owner, or lease
                # payload that produced them.
                "writeDurationMillis": _bounded_duration_millis(
                    raw.get("writeDurationMs")
                ),
                "acknowledgementDurationMillis": _bounded_duration_millis(
                    raw.get("ackDurationMs")
                ),
            }
            records.append(record)
        unresolved = sum(
            1 for record in records if record["status"] not in _COMMAND_TERMINAL_STATUSES
        )
        failed = sum(1 for record in records if record["status"] != "PASS")
        ack_missing = sum(1 for record in records if not record["ackReceived"])
        return {
            "schema": "arduino_command_ledger.v1",
            "records": records,
            "unresolvedCount": unresolved,
            "failedCount": failed,
            "ackMissingCount": ack_missing,
        }

    def _end_command_ledger(self) -> dict[str, Any]:
        evidence = self._command_evidence()
        self._command_ledger_active = False
        return evidence

    def _write_line(
        self,
        command: str,
        *,
        read_timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        if self._serial is None:
            raise ArduinoHIDError("Arduino serial connection is not open")
        encoded = (command.strip() + "\n").encode("utf-8")
        name = _command_name(command)
        self._command_sequence += 1
        trace: dict[str, Any] = {
            "schema": "arduino_serial_command_trace.v1",
            "commandId": self._command_sequence,
            "serialOwner": self.serial_owner,
            "port": self.port,
            "baud": self.baud,
            "writeTimeoutMs": self.command_timeout_ms,
            "readTimeoutMs": (
                self.handshake_timeout_ms
                if read_timeout_ms is None
                else read_timeout_ms
            ),
            "commandName": name,
            "commandBytes": len(encoded),
            "writeDurationMs": None,
            "ackDurationMs": None,
            "retryCount": 0,
            "portReopened": False,
            "timeoutClassification": None,
            "lockAcquired": bool((self._serial_lock and self._serial_lock.acquired) or not self.serial_lock_enabled),
            "lockWaitMs": int((self._serial_lock.wait_ms if self._serial_lock else 0) or 0),
            "concurrentAccessDetected": bool(self._serial_lock.concurrent_access_detected if self._serial_lock else False),
            "status": "PENDING",
        }
        started = time.monotonic()
        try:
            self._serial.write(encoded)
            flush = getattr(self._serial, "flush", None)
            if callable(flush):
                flush()
            trace["writeDurationMs"] = int(round((time.monotonic() - started) * 1000))
            trace["status"] = "WRITE_OK"
            self._status.command_count += 1
        except Exception as error:  # noqa: BLE001
            trace["writeDurationMs"] = int(round((time.monotonic() - started) * 1000))
            trace["status"] = "WRITE_FAIL"
            trace["error"] = f"{type(error).__name__}: {error}"
            if "timeout" in str(error).lower():
                trace["timeoutClassification"] = _command_timeout_classification(name, phase="write")
                self._status.timeouts += 1
            self._status.last_error = f"{type(error).__name__}: {error}"
            self._status.armed = False
            self._append_command_trace(trace)
            raise ArduinoHIDError(f"Arduino serial write failed: {error}") from error
        self._append_command_trace(trace)
        return trace

    def _best_effort_stop_all(self) -> None:
        if self._serial is None:
            return
        try:
            # Keep even the emergency cleanup inside the authoritative command
            # ledger.  A raw write/read left the record at ACK_READ without the
            # parsed firmware tokens, which made an otherwise acknowledged
            # rejection impossible for InputCoordinator to report safely.
            self._send("STOP_ALL", require_ack=True, expected_token="STOP_ALL")
            self._status.stop_all_sent = True
        except Exception:  # noqa: BLE001
            pass
        self._status.armed = False

    def _read_line(self, *, timeout_ms: int | None = None) -> str:
        if self._serial is None:
            raise ArduinoHIDError("Arduino serial connection is not open")
        prior_timeout = getattr(self._serial, "timeout", None)
        timeout_changed = timeout_ms is not None and hasattr(self._serial, "timeout")
        if timeout_changed:
            self._serial.timeout = timeout_ms / 1000.0
        started = time.monotonic()
        try:
            raw = self._serial.readline()
        except Exception as error:  # noqa: BLE001
            last = dict(self._status.last_command_trace)
            if last:
                last["ackDurationMs"] = int(
                    round((time.monotonic() - started) * 1000)
                )
                self._append_command_trace(last)
            self._status.last_error = f"{type(error).__name__}: {error}"
            self._status.armed = False
            raise ArduinoHIDError(f"Arduino serial read failed: {error}") from error
        finally:
            if timeout_changed:
                self._serial.timeout = prior_timeout
        ack_duration_millis = int(round((time.monotonic() - started) * 1000))
        if not raw:
            last = dict(self._status.last_command_trace)
            if last:
                last["ackDurationMs"] = ack_duration_millis
                self._append_command_trace(last)
            self._status.timeouts += 1
            self._status.armed = False
            raise ArduinoHIDError("Arduino command timed out waiting for ACK")
        line = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else str(raw).strip()
        last = dict(self._status.last_command_trace)
        if last:
            last["ackDurationMs"] = ack_duration_millis
            last["ackLine"] = line
            last["status"] = "ACK_READ"
            self._append_command_trace(last)
        return line

    def _send(
        self,
        command: str,
        *,
        require_ack: bool = True,
        expected_token: str | None = None,
        ack_timeout_ms: int | None = None,
    ) -> str:
        if self._serial is None:
            raise ArduinoHIDError(
                "Arduino serial connection is not open; the input coordinator must connect explicitly"
            )
        name = _command_name(command)
        if ack_timeout_ms is not None and (
            isinstance(ack_timeout_ms, bool)
            or not isinstance(ack_timeout_ms, int)
            or not 1 <= ack_timeout_ms <= MAX_CAMERA_HOLD_ACK_TIMEOUT_MS
        ):
            raise ArduinoHIDError("ACK timeout must be a bounded positive integer")
        ack_started = time.monotonic()
        write_trace = self._write_line(
            command,
            read_timeout_ms=ack_timeout_ms,
        )
        ack_deadline = (
            ack_started + (ack_timeout_ms / 1000.0)
            if ack_timeout_ms is not None
            else None
        )

        def raise_overall_ack_deadline() -> None:
            failed = dict(self._status.last_command_trace or write_trace)
            failed["status"] = "ACK_TIMEOUT_OR_READ_FAIL"
            failed["timeoutClassification"] = _command_timeout_classification(
                name,
                phase="read",
            )
            failed["ackDurationMs"] = int(
                round((time.monotonic() - ack_started) * 1000)
            )
            failed["error"] = "overall ACK deadline exceeded"
            self._append_command_trace(failed)
            self._status.timeouts += 1
            self._status.last_error = "overall ACK deadline exceeded"
            self._status.armed = False
            if name != "STOP_ALL":
                self._best_effort_stop_all()
            raise ArduinoHIDError(
                f"Arduino {name} exceeded its overall ACK deadline"
            )

        expected = expected_token or _expected_response_token(command)
        last_line = ""
        for _attempt in range(6):
            read_timeout_ms = ack_timeout_ms
            if ack_deadline is not None:
                remaining_seconds = ack_deadline - time.monotonic()
                if remaining_seconds <= 0:
                    raise_overall_ack_deadline()
                read_timeout_ms = max(
                    1,
                    min(
                        ack_timeout_ms,
                        int((remaining_seconds * 1000.0) + 0.999),
                    ),
                )
            try:
                line = self._read_line(timeout_ms=read_timeout_ms)
            except Exception as error:
                failed = dict(self._status.last_command_trace or write_trace)
                failed["status"] = "ACK_TIMEOUT_OR_READ_FAIL"
                failed["timeoutClassification"] = _command_timeout_classification(name, phase="read")
                failed["error"] = f"{type(error).__name__}: {error}"
                self._append_command_trace(failed)
                if str(command).strip().split(" ", 1)[0].upper() != "STOP_ALL":
                    self._best_effort_stop_all()
                raise
            if ack_deadline is not None and time.monotonic() > ack_deadline:
                raise_overall_ack_deadline()
            last_line = line
            token = _line_token(line)
            payload_token = _line_payload_token(line)
            if token == "ERR":
                rejected = dict(self._status.last_command_trace or write_trace)
                rejected["status"] = "REJECTED"
                rejected["responseToken"] = token
                rejected["payloadToken"] = payload_token
                rejected["error"] = "firmware rejected command"
                self._append_command_trace(rejected)
                self._status.ack_failures += 1
                self._status.last_error = f"ERR {payload_token}"
                self._status.armed = False
                if name != "STOP_ALL":
                    self._best_effort_stop_all()
                raise ArduinoHIDError(
                    f"Arduino rejected {name} (response={token}, payload={payload_token})"
                )
            if not require_ack:
                success = dict(self._status.last_command_trace or write_trace)
                success["status"] = "PASS"
                success["responseToken"] = token
                success["payloadToken"] = payload_token
                self._append_command_trace(success)
                self._status.last_error = None
                return line
            if token in ACK_TOKENS and (expected is None or payload_token == expected):
                success = dict(self._status.last_command_trace or write_trace)
                success["status"] = "PASS"
                success["responseToken"] = token
                success["payloadToken"] = payload_token
                self._append_command_trace(success)
                self._status.last_error = None
                return line
            if token == "OK" and payload_token in {"BOOT", "WATCHDOG_STOP"}:
                if name in {"CAMERA_HOLD", "WHEEL"}:
                    break
                continue
        self._status.ack_failures += 1
        unexpected_token = _line_token(last_line)
        unexpected_payload = _line_payload_token(last_line)
        self._status.last_error = (
            f"unexpected response token={unexpected_token} payload={unexpected_payload}"
        )
        self._status.armed = False
        unexpected = dict(self._status.last_command_trace or write_trace)
        unexpected["status"] = "UNEXPECTED_RESPONSE"
        unexpected["responseToken"] = unexpected_token
        unexpected["payloadToken"] = unexpected_payload
        unexpected["error"] = "unexpected firmware response"
        self._append_command_trace(unexpected)
        if name != "STOP_ALL":
            self._best_effort_stop_all()
        raise ArduinoHIDError(
            f"Arduino {name} returned an unexpected response "
            f"(response={unexpected_token}, payload={unexpected_payload})"
        )

    def _send_armed(
        self,
        command: str,
        *,
        require_ack: bool = True,
        expected_token: str | None = None,
        ack_timeout_ms: int | None = None,
    ) -> str:
        self._require_armed()
        return self._send(
            command,
            require_ack=require_ack,
            expected_token=expected_token,
            ack_timeout_ms=ack_timeout_ms,
        )

    def _move_relative(self, dx: int, dy: int) -> dict[str, Any]:
        self._require_armed()
        dx_i = int(dx)
        dy_i = int(dy)
        if dx_i == 0 and dy_i == 0:
            raise ArduinoHIDError("relative movement must change at least one axis")
        if abs(dx_i) > 20 or abs(dy_i) > 20:
            raise ArduinoHIDError("relative movement exceeds the firmware 20px axis limit")
        command = f"MOVE {dx_i} {dy_i}"
        ack = self._send_armed(command, require_ack=True, expected_token="MOVE")
        return {
            "schema": "arduino_move_command_trace.v1",
            "commandSent": command,
            "firmwareAck": ack,
            "dx": dx_i,
            "dy": dy_i,
            "ackOk": True,
        }

    def _ping(self) -> str:
        return self._send("PING", require_ack=True, expected_token="PONG")

    def _identify(self) -> dict[str, Any]:
        line = self._send("IDENTIFY", require_ack=True, expected_token="IDENTIFY")
        self._status.identity = _fields_from_line(line)
        self._status.identified = True
        self._status.protocol = str(self._status.identity.get("protocol") or "") or None
        return dict(self._status.identity)

    def _capabilities(self) -> dict[str, Any]:
        line = self._send("CAPS", require_ack=True, expected_token="CAPS")
        self._status.capabilities = _fields_from_line(line)
        return dict(self._status.capabilities)

    def _firmware_status(self) -> dict[str, Any]:
        line = self._send("STATUS", require_ack=True, expected_token="STATUS")
        status = _fields_from_line(line)
        for field in ("keysDown", "mouseButtonsDown", "lastCommandAgeMs", "watchdogMs"):
            if field in status:
                try:
                    status[field] = int(status[field])
                except (TypeError, ValueError):
                    pass
        self._status.firmware_status = status
        armed = self._status.firmware_status.get("armed")
        if isinstance(armed, bool):
            self._status.armed = armed
        return dict(self._status.firmware_status)

    def _port_health(self) -> dict[str, Any]:
        started = time.monotonic()
        payload: dict[str, Any] = {
            "schema": "arduino_port_health.v1",
            "portHealth": "PASS",
            "port": self.port,
            "baud": self.baud,
            "serialOwner": self.serial_owner,
            "writeTimeoutMs": self.command_timeout_ms,
            "readTimeoutMs": self.handshake_timeout_ms,
            "lock": None,
            "ping": None,
            "identify": None,
            "caps": None,
            "status": None,
            "writeLatencyMs": None,
            "ackLatencyMs": None,
            "commandTraces": [],
            "warnings": [],
        }
        trace_start = len(self._status.command_trace)
        try:
            self._connect()
            payload["lock"] = dict(self._status.serial_lock)
            payload["ping"] = self._ping()
            payload["identify"] = self._identify()
            payload["caps"] = self._capabilities()
            payload["status"] = self._firmware_status()
        except Exception as error:  # noqa: BLE001
            payload["portHealth"] = "FAIL"
            payload["warnings"].append(f"{type(error).__name__}: {error}")
        finally:
            traces = [dict(item) for item in self._status.command_trace[trace_start:]]
            payload["commandTraces"] = traces
            write_latencies = [item.get("writeDurationMs") for item in traces if isinstance(item.get("writeDurationMs"), int)]
            ack_latencies = [item.get("ackDurationMs") for item in traces if isinstance(item.get("ackDurationMs"), int)]
            payload["writeLatencyMs"] = max(write_latencies) if write_latencies else None
            payload["ackLatencyMs"] = max(ack_latencies) if ack_latencies else None
            payload["durationMs"] = int(round((time.monotonic() - started) * 1000))
            payload["backendStatus"] = self._status_snapshot()
        return payload

    def _stop_all(self) -> dict[str, Any]:
        try:
            self._send("STOP_ALL", require_ack=True, expected_token="STOP_ALL")
            self._status.stop_all_sent = True
            self._status.armed = False
            self._status.firmware_status = {}
        except Exception as error:  # noqa: BLE001
            self._status.last_error = f"{type(error).__name__}: {error}"
            self._status.armed = False
            raise
        return self._status_snapshot()

    def _verify_protocol(self) -> None:
        protocol = str(self._status.identity.get("protocol") or "")
        if protocol not in SUPPORTED_PROTOCOLS:
            self._status.last_error = f"unsupported protocol: {protocol or 'missing'}"
            raise ArduinoHIDError(
                "Arduino firmware protocol mismatch: expected one of "
                f"{', '.join(sorted(SUPPORTED_PROTOCOLS))}, got {protocol or 'missing'}. "
                "Flash arduino/ArduinoHIDBridge/ArduinoHIDBridge.ino."
            )
        missing = []
        for cap in REQUIRED_CAPS:
            value = self._status.capabilities.get(cap)
            if value is not True and str(value).lower() not in {"1", "true", "yes"}:
                missing.append(cap)
        if missing:
            self._status.last_error = "missing required caps: " + ",".join(missing)
            raise ArduinoHIDError(f"Arduino firmware missing required safety caps: {', '.join(missing)}")
        watchdog = self._status.firmware_status.get("watchdogMs")
        if watchdog is not None:
            try:
                if int(watchdog) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise ArduinoHIDError(f"Arduino firmware reported invalid watchdogMs={watchdog!r}") from None
        try:
            from .input_capabilities import InputCapabilities

            self._negotiated_input_capabilities = InputCapabilities.from_negotiation(
                self._status.identity,
                self._status.capabilities,
                self._status.firmware_status,
            )
        except Exception as error:
            self._status.last_error = (
                "invalid negotiated input capabilities: "
                f"{type(error).__name__}: {error}"
            )
            raise ArduinoHIDError(self._status.last_error) from error

    def _input_capabilities(self) -> Any:
        capabilities = self._negotiated_input_capabilities
        if capabilities is None:
            raise ArduinoHIDError("input capabilities have not been negotiated")
        return capabilities

    def _arm(self, session_token: str | None = None) -> dict[str, Any]:
        token = session_token or self.session_token
        self.session_token = token
        self._negotiated_input_capabilities = None
        self._status.session_token_hash = _token_hash(token)
        try:
            self._stop_all()
        except Exception:
            if self.fail_closed:
                raise
        self._ping()
        self._identify()
        self._capabilities()
        firmware_status = self._firmware_status()
        if firmware_status.get("armed"):
            raise ArduinoHIDError("Arduino firmware was armed before ARM; refusing live session")
        if int(firmware_status.get("keysDown") or 0) != 0 or int(firmware_status.get("mouseButtonsDown") or 0) != 0:
            raise ArduinoHIDError("Arduino firmware reports held keys/buttons before ARM")
        self._verify_protocol()
        self._send(f"ARM {token}", require_ack=True)
        self._status.armed = True
        self._live_session_active = True
        return self._status_snapshot()

    def _disarm(self) -> dict[str, Any]:
        if self._serial is not None:
            try:
                self._send("DISARM", require_ack=True)
            except Exception as error:  # noqa: BLE001
                self._status.last_error = f"{type(error).__name__}: {error}"
                try:
                    self._stop_all()
                except Exception:
                    pass
                self._live_session_active = False
                raise ArduinoHIDError("Arduino DISARM was not acknowledged") from error
        self._status.armed = False
        self._live_session_active = False
        return self._status_snapshot()

    def _require_armed(self) -> None:
        if self.fail_closed and not self._armed:
            raise ArduinoHIDError("Arduino HID backend is not armed")

    def _current_position(self) -> tuple[int, int]:
        position = _cursor_position()
        self._tracked_position = position
        return position

    def _verify_physical_mouse_quiet(self) -> dict[str, Any]:
        return _verify_physical_mouse_quiet()

    def _verify_physical_mouse_buttons_released(self) -> dict[str, Any]:
        return _verify_physical_mouse_buttons_released()

    def _virtual_desktop_bounds(self) -> dict[str, Any]:
        return _virtual_desktop_bounds()

    def _consume_owned_mouse_transition(
        self, button: str
    ) -> dict[str, Any]:
        return _consume_owned_mouse_transition(button=button)

    def _verify_window_geometry(
        self,
        *,
        expected_pid: int,
        expected_hwnd: int,
        expected_outer_bounds: tuple[int, int, int, int] | None,
        expected_client_bounds: tuple[int, int, int, int] | None,
        required_inner_bounds: tuple[int, int, int, int],
    ) -> dict[str, Any]:
        return _verify_window_geometry(
            expected_pid=expected_pid,
            expected_hwnd=expected_hwnd,
            expected_outer_bounds=expected_outer_bounds,
            expected_client_bounds=expected_client_bounds,
            required_inner_bounds=required_inner_bounds,
        )

    def _legacy_configure_movement_safety(
        self,
        *,
        allowed_region: dict[str, Any] | None = None,
        allowed_foreground_titles: list[str] | tuple[str, ...] | None = None,
        enabled: bool = True,
        margin_px: int = 0,
        max_chunk_px: int = DEFAULT_CLOSED_LOOP_CHUNK_PX,
        tolerance_px: int = DEFAULT_CLOSED_LOOP_TOLERANCE_PX,
        feedback_tolerance_px: int = DEFAULT_CLOSED_LOOP_FEEDBACK_TOLERANCE_PX,
        move_settle_ms: int = DEFAULT_MOVE_SETTLE_MS,
        move_poll_ms: int = DEFAULT_MOVE_POLL_MS,
        move_noeffect_timeout_ms: int = DEFAULT_MOVE_NOEFFECT_TIMEOUT_MS,
        move_noeffect_retries: int = DEFAULT_MOVE_NOEFFECT_RETRIES,
        move_min_effective_px: int = DEFAULT_MOVE_MIN_EFFECTIVE_PX,
        move_retry_scale: float = DEFAULT_MOVE_RETRY_SCALE,
        move_max_consecutive_noeffect: int = DEFAULT_MOVE_MAX_CONSECUTIVE_NOEFFECT,
    ) -> dict[str, Any]:
        rect = _rect_from_region(allowed_region)
        self._movement_safety = {
            "enabled": bool(enabled),
            "allowedRegion": rect,
            "allowedForegroundTitles": list(allowed_foreground_titles or []),
            "marginPx": max(0, int(margin_px or 0)),
            "maxChunkSize": max(1, min(20, int(max_chunk_px or DEFAULT_CLOSED_LOOP_CHUNK_PX))),
            "tolerancePx": max(0, int(tolerance_px or DEFAULT_CLOSED_LOOP_TOLERANCE_PX)),
            "feedbackTolerancePx": max(0, int(feedback_tolerance_px or DEFAULT_CLOSED_LOOP_FEEDBACK_TOLERANCE_PX)),
            "moveSettleMs": max(0, int(move_settle_ms if move_settle_ms is not None else DEFAULT_MOVE_SETTLE_MS)),
            "movePollMs": max(1, int(move_poll_ms if move_poll_ms is not None else DEFAULT_MOVE_POLL_MS)),
            "moveNoEffectTimeoutMs": max(1, int(move_noeffect_timeout_ms if move_noeffect_timeout_ms is not None else DEFAULT_MOVE_NOEFFECT_TIMEOUT_MS)),
            "moveNoEffectRetries": max(0, int(move_noeffect_retries if move_noeffect_retries is not None else DEFAULT_MOVE_NOEFFECT_RETRIES)),
            "moveMinEffectivePx": max(1, int(move_min_effective_px if move_min_effective_px is not None else DEFAULT_MOVE_MIN_EFFECTIVE_PX)),
            "moveRetryScale": max(0.25, min(3.0, float(move_retry_scale if move_retry_scale is not None else DEFAULT_MOVE_RETRY_SCALE))),
            "moveMaxConsecutiveNoEffect": max(1, int(move_max_consecutive_noeffect if move_max_consecutive_noeffect is not None else DEFAULT_MOVE_MAX_CONSECUTIVE_NOEFFECT)),
        }
        return dict(self._movement_safety)

    def _legacy_movement_safety(self) -> dict[str, Any] | None:
        if not isinstance(self._movement_safety, dict):
            return None
        return dict(self._movement_safety)

    def _legacy_clear_movement_safety(self) -> None:
        self._movement_safety = None

    def _abort_movement(self, reason: str, trace: dict[str, Any]) -> None:
        try:
            self._stop_all()
        except Exception as error:  # noqa: BLE001
            trace.setdefault("cleanupWarnings", []).append(f"stop_all failed: {type(error).__name__}: {error}")
        try:
            self._disarm()
            trace["disarmedOnAbort"] = True
        except Exception as error:  # noqa: BLE001
            trace.setdefault("cleanupWarnings", []).append(f"disarm failed: {type(error).__name__}: {error}")
        self.last_movement_trace = trace
        _movement_abort_trace(reason, trace)

    def _legacy_move_to_absolute(
        self,
        target_screen_point: dict[str, Any] | tuple[int, int],
        *,
        allowed_region: dict[str, Any] | None = None,
        allowed_foreground_titles: list[str] | tuple[str, ...] | None = None,
        max_chunk_px: int = DEFAULT_CLOSED_LOOP_CHUNK_PX,
        tolerance_px: int = DEFAULT_CLOSED_LOOP_TOLERANCE_PX,
        feedback_tolerance_px: int = DEFAULT_CLOSED_LOOP_FEEDBACK_TOLERANCE_PX,
        margin_px: int = 0,
        max_chunks: int = 160,
        move_settle_ms: int = DEFAULT_MOVE_SETTLE_MS,
        move_poll_ms: int = DEFAULT_MOVE_POLL_MS,
        move_noeffect_timeout_ms: int = DEFAULT_MOVE_NOEFFECT_TIMEOUT_MS,
        move_noeffect_retries: int = DEFAULT_MOVE_NOEFFECT_RETRIES,
        move_min_effective_px: int = DEFAULT_MOVE_MIN_EFFECTIVE_PX,
        move_retry_scale: float = DEFAULT_MOVE_RETRY_SCALE,
        move_max_consecutive_noeffect: int = DEFAULT_MOVE_MAX_CONSECUTIVE_NOEFFECT,
        monitor_status_reader: Callable[[], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self._require_armed()
        target = _point_dict(target_screen_point)
        region = _rect_from_region(allowed_region)
        foreground_before = _foreground_window_info()
        trace: dict[str, Any] = {
            "schema": "arduino_closed_loop_move.v1",
            "status": "PASS",
            "cursorPositionBefore": None,
            "cursorPositionAfter": None,
            "targetScreenPoint": target,
            "allowedRegion": region,
            "movementChunks": [],
            "chunkCount": 0,
            "maxChunkSize": max(1, min(20, int(max_chunk_px or DEFAULT_CLOSED_LOOP_CHUNK_PX))),
            "positionErrorPx": None,
            "leftAllowedRegion": False,
            "foregroundWindowBefore": foreground_before,
            "foregroundWindowAfter": None,
            "movementAbortedReason": None,
            "targetInsideAllowedRegion": bool(_point_in_region(target, region, margin=max(0, int(margin_px or 0)))) if target and region else False,
            "cursorInsideAllowedRegion": None,
            "cursorNearAllowedRegion": None,
            "cursorStartRegionTolerancePx": DEFAULT_CURSOR_START_REGION_TOLERANCE_PX,
            "foregroundWindowAllowed": _foreground_allowed(foreground_before, allowed_foreground_titles),
            "noClick": True,
            "moveSettleMs": max(0, int(move_settle_ms if move_settle_ms is not None else DEFAULT_MOVE_SETTLE_MS)),
            "movePollMs": max(1, int(move_poll_ms if move_poll_ms is not None else DEFAULT_MOVE_POLL_MS)),
            "moveNoEffectTimeoutMs": max(1, int(move_noeffect_timeout_ms if move_noeffect_timeout_ms is not None else DEFAULT_MOVE_NOEFFECT_TIMEOUT_MS)),
            "moveNoEffectRetries": max(0, int(move_noeffect_retries if move_noeffect_retries is not None else DEFAULT_MOVE_NOEFFECT_RETRIES)),
            "moveMinEffectivePx": max(1, int(move_min_effective_px if move_min_effective_px is not None else DEFAULT_MOVE_MIN_EFFECTIVE_PX)),
            "moveRetryScale": max(0.25, min(3.0, float(move_retry_scale if move_retry_scale is not None else DEFAULT_MOVE_RETRY_SCALE))),
            "moveMaxConsecutiveNoEffect": max(1, int(move_max_consecutive_noeffect if move_max_consecutive_noeffect is not None else DEFAULT_MOVE_MAX_CONSECUTIVE_NOEFFECT)),
            "totalChunks": 0,
            "successfulChunks": 0,
            "retryChunks": 0,
            "noEffectChunks": 0,
            "consecutiveNoEffectChunks": 0,
            "maxConsecutiveNoEffectChunks": 0,
            "movementSuccessRate": None,
            "maxPositionErrorPx": None,
            "maxTargetErrorPxDuringMove": None,
            "finalPositionErrorPx": None,
        }
        if not target:
            self._abort_movement("missing_target_screen_point", trace)
        if not region:
            self._abort_movement("missing_allowed_region", trace)
        if not trace["targetInsideAllowedRegion"]:
            self._abort_movement("target_outside_allowed_region", trace)
        if not trace["foregroundWindowAllowed"]:
            self._abort_movement("foreground_window_not_allowed", trace)
        current = self._current_position()
        trace["cursorPositionBefore"] = {"x": current[0], "y": current[1]}
        trace["cursorInsideAllowedRegion"] = _point_in_region(current, region, margin=0)
        trace["cursorNearAllowedRegion"] = _point_near_region(
            current,
            region,
            tolerance=DEFAULT_CURSOR_START_REGION_TOLERANCE_PX,
        )
        if not trace["cursorInsideAllowedRegion"] and not trace["cursorNearAllowedRegion"]:
            self._abort_movement("cursor_start_outside_allowed_region", trace)
        if not trace["cursorInsideAllowedRegion"]:
            trace.setdefault("warnings", []).append("cursor_start_near_allowed_region")
        max_chunk = int(trace["maxChunkSize"])
        tolerance = max(0, int(tolerance_px or DEFAULT_CLOSED_LOOP_TOLERANCE_PX))
        feedback_tolerance = max(0, int(feedback_tolerance_px or DEFAULT_CLOSED_LOOP_FEEDBACK_TOLERANCE_PX))
        settle_ms = int(trace["moveSettleMs"])
        poll_ms = int(trace["movePollMs"])
        noeffect_timeout_ms = max(settle_ms, int(trace["moveNoEffectTimeoutMs"]))
        noeffect_retries = int(trace["moveNoEffectRetries"])
        min_effective_px = int(trace["moveMinEffectivePx"])
        retry_scale = float(trace["moveRetryScale"])
        max_consecutive_noeffect = int(trace["moveMaxConsecutiveNoEffect"])
        max_polls = max(1, int((noeffect_timeout_ms + poll_ms - 1) / poll_ms) + 1)

        def monitor_status() -> dict[str, Any] | None:
            if not callable(monitor_status_reader):
                return None
            try:
                status = monitor_status_reader()
            except Exception as error:  # noqa: BLE001
                trace.setdefault("monitorReadWarnings", []).append(f"{type(error).__name__}: {error}")
                return None
            return status if isinstance(status, dict) else None

        def scaled_retry_delta(base_delta: int, remaining_delta: int, retry_index: int) -> int:
            if retry_index <= 0 or retry_scale == 1.0:
                return int(base_delta)
            if base_delta == 0:
                return 0
            sign = 1 if base_delta > 0 else -1
            magnitude = int(round(abs(base_delta) * retry_scale))
            magnitude = max(min(abs(remaining_delta), max_chunk), min(max_chunk, max(1, magnitude)))
            return sign * magnitude

        def poll_after_move(
            *,
            before_position: tuple[int, int],
            before_monitor: dict[str, Any] | None,
            target_error_before: int | None,
            command_delta: tuple[int, int],
            expected_after: tuple[int, int],
            ack_elapsed_ms: int,
            chunk: dict[str, Any],
        ) -> tuple[tuple[int, int], dict[str, Any]]:
            last_position = before_position
            last_monitor = before_monitor
            result: dict[str, Any] = {
                "success": False,
                "abortReason": None,
                "classification": "move_chunk_no_effect",
                "pollCount": 0,
                "cursorDeltaObserved": False,
                "rawInputDeltaObserved": False,
                "targetErrorBefore": target_error_before,
                "targetErrorAfter": target_error_before,
                "firstCursorDeltaTimeMs": None,
                "firstRawInputDeltaTimeMs": None,
                "inputIntegrityDelta": None,
            }
            for poll_index in range(max_polls):
                if poll_index > 0:
                    self.sleep_func(poll_ms / 1000.0)
                elapsed_ms = ack_elapsed_ms + (poll_index * poll_ms)
                position = self._current_position()
                current_monitor = monitor_status()
                delta = input_integrity_delta(before_monitor, current_monitor) if before_monitor is not None or current_monitor is not None else {}
                cursor_dx, cursor_dy = _cursor_delta_tuple(before_position, position)
                cursor_moved = bool(cursor_dx or cursor_dy)
                raw_seen = _raw_input_seen(delta)
                target_error_after = _target_error(position, target)
                result["pollCount"] = poll_index + 1
                result["cursorDeltaObserved"] = bool(result["cursorDeltaObserved"] or cursor_moved)
                result["rawInputDeltaObserved"] = bool(result["rawInputDeltaObserved"] or raw_seen)
                result["targetErrorAfter"] = target_error_after
                result["inputIntegrityDelta"] = delta
                if cursor_moved and result["firstCursorDeltaTimeMs"] is None:
                    result["firstCursorDeltaTimeMs"] = elapsed_ms
                if raw_seen and result["firstRawInputDeltaTimeMs"] is None:
                    result["firstRawInputDeltaTimeMs"] = elapsed_ms
                foreground_after = _foreground_window_info()
                result["foregroundWindow"] = foreground_after
                result["insideAllowedRegion"] = _point_in_region(position, region, margin=0)
                result["nearAllowedRegion"] = _point_near_region(
                    position,
                    region,
                    tolerance=DEFAULT_CURSOR_START_REGION_TOLERANCE_PX,
                )
                if not result["insideAllowedRegion"]:
                    expected_inside = _point_in_region(expected_after, region, margin=0)
                    if result["nearAllowedRegion"] and expected_inside:
                        result["transientNearAllowedRegion"] = True
                    else:
                        result["abortReason"] = "cursor_left_allowed_region"
                        return position, result
                if not _foreground_allowed(foreground_after, allowed_foreground_titles):
                    result["abortReason"] = "foreground_window_changed"
                    return position, result
                direction_ok = _cursor_moved_expected_direction(
                    before_position,
                    position,
                    command_delta,
                    min_effective_px=min_effective_px,
                )
                target_improved = (
                    target_error_before is not None
                    and target_error_after is not None
                    and int(target_error_after) < int(target_error_before)
                )
                target_reached = target_error_after is not None and int(target_error_after) <= tolerance
                if target_reached or direction_ok or target_improved:
                    result["success"] = True
                    result["classification"] = "move_chunk_success" if elapsed_ms <= settle_ms else "move_chunk_delayed_success"
                    return position, result
                if cursor_moved and not target_improved and not direction_ok:
                    result["abortReason"] = "movement_feedback_mismatch"
                    result["classification"] = "move_chunk_feedback_mismatch"
                    return position, result
                last_position = position
                last_monitor = current_monitor
            if result["rawInputDeltaObserved"] and not result["cursorDeltaObserved"]:
                result["classification"] = "move_chunk_rawinput_seen_cursor_no_move"
            elif not result["rawInputDeltaObserved"] and not result["cursorDeltaObserved"]:
                result["classification"] = "move_chunk_no_rawinput_no_cursor"
            else:
                result["classification"] = "move_chunk_no_effect"
            return last_position, result

        for _index in range(max(1, int(max_chunks or 1))):
            dx = int(target["x"]) - int(current[0])
            dy = int(target["y"]) - int(current[1])
            if max(abs(dx), abs(dy)) <= tolerance:
                break
            scale = min(1.0, max_chunk / max(abs(dx), abs(dy), 1))
            part_x = int(round(dx * scale))
            part_y = int(round(dy * scale))
            if part_x == 0 and dx != 0:
                part_x = 1 if dx > 0 else -1
            if part_y == 0 and dy != 0:
                part_y = 1 if dy > 0 else -1
            before = current
            target_error_before = _target_error(before, target)
            accepted = False
            for retry_index in range(noeffect_retries + 1):
                command_x = scaled_retry_delta(part_x, dx, retry_index)
                command_y = scaled_retry_delta(part_y, dy, retry_index)
                expected = (int(current[0]) + command_x, int(current[1]) + command_y)
                if not _point_in_region(expected, region, margin=0):
                    self._abort_movement("move_chunk_retry_would_leave_allowed_region", trace)
                before_monitor = monitor_status()
                command = f"MOVE {command_x} {command_y}"
                command_start = time.monotonic()
                firmware_ack = self._send_armed(command, require_ack=True, expected_token="MOVE")
                chunk_ack_time_ms = int(round((time.monotonic() - command_start) * 1000))
                chunk = {
                    "logicalChunkIndex": _index,
                    "retryIndex": retry_index,
                    "before": {"x": before[0], "y": before[1]},
                    "commandSent": command,
                    "firmwareAck": firmware_ack,
                    "chunkAckTimeMs": chunk_ack_time_ms,
                    "commandedDelta": {"x": command_x, "y": command_y},
                    "expectedAfter": {"x": expected[0], "y": expected[1]},
                    "settleWindowMs": settle_ms,
                    "pollIntervalMs": poll_ms,
                    "noEffectTimeoutMs": noeffect_timeout_ms,
                    "targetErrorBefore": target_error_before,
                }
                after, poll = poll_after_move(
                    before_position=before,
                    before_monitor=before_monitor,
                    target_error_before=target_error_before,
                    command_delta=(command_x, command_y),
                    expected_after=expected,
                    ack_elapsed_ms=chunk_ack_time_ms,
                    chunk=chunk,
                )
                error_px = max(abs(int(after[0]) - expected[0]), abs(int(after[1]) - expected[1]))
                chunk.update(
                    {
                        "actualAfter": {"x": after[0], "y": after[1]},
                        "errorPx": error_px,
                        "insideAllowedRegion": bool(poll.get("insideAllowedRegion", _point_in_region(after, region, margin=0))),
                        "foregroundWindow": poll.get("foregroundWindow"),
                        "classification": poll.get("classification"),
                        "pollCount": poll.get("pollCount"),
                        "cursorDeltaObserved": poll.get("cursorDeltaObserved"),
                        "rawInputDeltaObserved": poll.get("rawInputDeltaObserved"),
                        "targetErrorAfter": poll.get("targetErrorAfter"),
                        "firstCursorDeltaTimeMs": poll.get("firstCursorDeltaTimeMs"),
                        "firstRawInputDeltaTimeMs": poll.get("firstRawInputDeltaTimeMs"),
                        "inputIntegrityDelta": poll.get("inputIntegrityDelta"),
                        "feedbackMismatchTolerated": bool(poll.get("success") and error_px > feedback_tolerance),
                    }
                )
                trace["movementChunks"].append(chunk)
                trace["chunkCount"] = len(trace["movementChunks"])
                trace["totalChunks"] = int(trace["totalChunks"] or 0) + 1
                if retry_index > 0:
                    trace["retryChunks"] = int(trace["retryChunks"] or 0) + 1
                    if poll.get("success"):
                        chunk["classification"] = "move_chunk_retry_success"
                trace["foregroundWindowAfter"] = poll.get("foregroundWindow")
                trace["maxTargetErrorPxDuringMove"] = max(
                    int(trace["maxTargetErrorPxDuringMove"] if trace.get("maxTargetErrorPxDuringMove") is not None else 0),
                    int(poll.get("targetErrorAfter") if poll.get("targetErrorAfter") is not None else target_error_before or 0),
                )
                if poll.get("abortReason") == "cursor_left_allowed_region":
                    trace["leftAllowedRegion"] = True
                    self._abort_movement("cursor_left_allowed_region", trace)
                if poll.get("abortReason"):
                    self._abort_movement(str(poll.get("abortReason")), trace)
                if poll.get("success"):
                    trace["successfulChunks"] = int(trace["successfulChunks"] or 0) + 1
                    trace["consecutiveNoEffectChunks"] = 0
                    current = after
                    accepted = True
                    break
                trace["noEffectChunks"] = int(trace["noEffectChunks"] or 0) + 1
                trace["consecutiveNoEffectChunks"] = int(trace["consecutiveNoEffectChunks"] or 0) + 1
                trace["maxConsecutiveNoEffectChunks"] = max(
                    int(trace["maxConsecutiveNoEffectChunks"] or 0),
                    int(trace["consecutiveNoEffectChunks"] or 0),
                )
                if int(trace["consecutiveNoEffectChunks"] or 0) >= max_consecutive_noeffect:
                    chunk["abortReason"] = "move_chunk_no_effect_abort"
                    self._abort_movement("move_chunk_no_effect_abort", trace)
                if retry_index >= noeffect_retries:
                    chunk["abortReason"] = "move_chunk_no_effect_abort"
                    self._abort_movement("move_chunk_no_effect_abort", trace)
            if not accepted:
                self._abort_movement("move_chunk_no_effect_abort", trace)
        final = self._current_position()
        trace["cursorPositionAfter"] = {"x": final[0], "y": final[1]}
        trace["foregroundWindowAfter"] = _foreground_window_info()
        trace["positionErrorPx"] = max(abs(int(final[0]) - int(target["x"])), abs(int(final[1]) - int(target["y"])))
        trace["finalPositionErrorPx"] = trace["positionErrorPx"]
        trace["maxPositionErrorPx"] = trace["positionErrorPx"]
        total_chunks = int(trace.get("totalChunks") or 0)
        trace["movementSuccessRate"] = (float(trace.get("successfulChunks") or 0) / float(total_chunks)) if total_chunks else 1.0
        if not _point_in_region(final, region, margin=0):
            trace["leftAllowedRegion"] = True
            self._abort_movement("cursor_left_allowed_region", trace)
        if not _foreground_allowed(trace["foregroundWindowAfter"], allowed_foreground_titles):
            self._abort_movement("foreground_window_changed", trace)
        if int(trace["positionErrorPx"] or 0) > tolerance:
            self._abort_movement("target_not_reached_within_tolerance", trace)
        self.last_movement_trace = trace
        return trace

    def _move_relative_chunked(self, dx: int, dy: int, *, duration_ms: int = 0) -> None:
        self._require_armed()
        dx = int(dx)
        dy = int(dy)
        if dx == 0 and dy == 0:
            return
        # Firmware rejects MOVE deltas larger than MAX_MOVE_DELTA in
        # arduino/ArduinoHIDBridge/ArduinoHIDBridge.ino.
        max_delta = 20
        steps = max(1, int(max(abs(dx), abs(dy)) / max_delta) + (1 if max(abs(dx), abs(dy)) % max_delta else 0))
        prev_x = 0
        prev_y = 0
        for index in range(1, steps + 1):
            target_x = int(round(dx * index / steps))
            target_y = int(round(dy * index / steps))
            part_x = target_x - prev_x
            part_y = target_y - prev_y
            prev_x = target_x
            prev_y = target_y
            part_duration = int(round(max(0, int(duration_ms or 0)) / steps))
            self._send_armed(f"MOVE {part_x} {part_y}", require_ack=True)
            if part_duration > 0:
                self.sleep_func(part_duration / 1000.0)

    def _correct_to_endpoint(self, x: int, y: int) -> None:
        target_x = int(x)
        target_y = int(y)
        for _attempt in range(ENDPOINT_CORRECTION_ATTEMPTS):
            actual_x, actual_y = self._current_position()
            dx = target_x - int(actual_x)
            dy = target_y - int(actual_y)
            if max(abs(dx), abs(dy)) <= ENDPOINT_CORRECTION_TOLERANCE_PX:
                self._tracked_position = (actual_x, actual_y)
                return
            self._move_relative_chunked(dx, dy, duration_ms=0)
            self.sleep_func(0.01)
        self._tracked_position = self._current_position()

    def _legacy_move_relative(self, dx: int, dy: int, *, duration_ms: int = 0) -> None:
        self._move_relative_chunked(int(dx), int(dy), duration_ms=duration_ms)
        if self._tracked_position is not None:
            self._tracked_position = (self._tracked_position[0] + int(dx), self._tracked_position[1] + int(dy))

    def _move_to(self, x: int, y: int, *, duration_ms: int = 0) -> None:
        current = self._current_position()
        dx = int(x) - int(current[0])
        dy = int(y) - int(current[1])
        self._move_relative_chunked(dx, dy, duration_ms=duration_ms)
        self._correct_to_endpoint(int(x), int(y))

    def _legacy_move(self, plan: Any) -> None:
        self._require_armed()
        safety = self._movement_safety if isinstance(self._movement_safety, dict) and self._movement_safety.get("enabled") else None
        if safety:
            points = list(getattr(plan, "points", []) or [])
            click_point = getattr(plan, "click_point", None)
            target_point = click_point if click_point is not None else (points[-1] if points else None)
            if target_point is None:
                return
            self._legacy_move_to_absolute(
                {"x": int(target_point.x), "y": int(target_point.y)},
                allowed_region=safety.get("allowedRegion"),
                allowed_foreground_titles=safety.get("allowedForegroundTitles"),
                max_chunk_px=int(safety.get("maxChunkSize") or DEFAULT_CLOSED_LOOP_CHUNK_PX),
                tolerance_px=int(safety.get("tolerancePx") or DEFAULT_CLOSED_LOOP_TOLERANCE_PX),
                feedback_tolerance_px=int(safety.get("feedbackTolerancePx") or DEFAULT_CLOSED_LOOP_FEEDBACK_TOLERANCE_PX),
                margin_px=int(safety.get("marginPx") or 0),
                move_settle_ms=int(safety.get("moveSettleMs") or DEFAULT_MOVE_SETTLE_MS),
                move_poll_ms=int(safety.get("movePollMs") or DEFAULT_MOVE_POLL_MS),
                move_noeffect_timeout_ms=int(safety.get("moveNoEffectTimeoutMs") or DEFAULT_MOVE_NOEFFECT_TIMEOUT_MS),
                move_noeffect_retries=int(safety.get("moveNoEffectRetries") or DEFAULT_MOVE_NOEFFECT_RETRIES),
                move_min_effective_px=int(safety.get("moveMinEffectivePx") or DEFAULT_MOVE_MIN_EFFECTIVE_PX),
                move_retry_scale=float(safety.get("moveRetryScale") or DEFAULT_MOVE_RETRY_SCALE),
                move_max_consecutive_noeffect=int(safety.get("moveMaxConsecutiveNoEffect") or DEFAULT_MOVE_MAX_CONSECUTIVE_NOEFFECT),
            )
            return
        last_time = 0
        for point in getattr(plan, "points", []) or []:
            target_x = int(point.x)
            target_y = int(point.y)
            timestamp = int(getattr(point, "timestamp_ms", 0) or 0)
            duration = max(0, timestamp - last_time)
            current = self._current_position()
            self._move_relative_chunked(target_x - current[0], target_y - current[1], duration_ms=duration)
            current = self._current_position()
            self._tracked_position = current
            last_time = timestamp
        click_point = getattr(plan, "click_point", None)
        if click_point is not None:
            self._correct_to_endpoint(int(click_point.x), int(click_point.y))

    def _mouse_down(self, *, button: str = "left") -> None:
        self._send_armed(f"MOUSE_DOWN {button}", require_ack=True)

    def _mouse_up(self, *, button: str = "left") -> None:
        self._send_armed(f"MOUSE_UP {button}", require_ack=True)

    def _legacy_click_at(self, x: int, y: int, *, button: str = "left", hold_ms: int = 0) -> None:
        self._require_armed()
        safety = self._movement_safety if isinstance(self._movement_safety, dict) and self._movement_safety.get("enabled") else None
        if safety:
            self._legacy_move_to_absolute(
                {"x": int(x), "y": int(y)},
                allowed_region=safety.get("allowedRegion"),
                allowed_foreground_titles=safety.get("allowedForegroundTitles"),
                max_chunk_px=int(safety.get("maxChunkSize") or DEFAULT_CLOSED_LOOP_CHUNK_PX),
                tolerance_px=int(safety.get("tolerancePx") or DEFAULT_CLOSED_LOOP_TOLERANCE_PX),
                feedback_tolerance_px=int(safety.get("feedbackTolerancePx") or DEFAULT_CLOSED_LOOP_FEEDBACK_TOLERANCE_PX),
                margin_px=int(safety.get("marginPx") or 0),
                move_settle_ms=int(safety.get("moveSettleMs") or DEFAULT_MOVE_SETTLE_MS),
                move_poll_ms=int(safety.get("movePollMs") or DEFAULT_MOVE_POLL_MS),
                move_noeffect_timeout_ms=int(safety.get("moveNoEffectTimeoutMs") or DEFAULT_MOVE_NOEFFECT_TIMEOUT_MS),
                move_noeffect_retries=int(safety.get("moveNoEffectRetries") or DEFAULT_MOVE_NOEFFECT_RETRIES),
                move_min_effective_px=int(safety.get("moveMinEffectivePx") or DEFAULT_MOVE_MIN_EFFECTIVE_PX),
                move_retry_scale=float(safety.get("moveRetryScale") or DEFAULT_MOVE_RETRY_SCALE),
                move_max_consecutive_noeffect=int(safety.get("moveMaxConsecutiveNoEffect") or DEFAULT_MOVE_MAX_CONSECUTIVE_NOEFFECT),
            )
        else:
            self._move_to(int(x), int(y), duration_ms=0)
        self._send_armed(f"CLICK {button} {max(0, int(hold_ms or 0))}", require_ack=True)

    def _legacy_move_and_click(self, plan: Any, *, button: str = "left") -> None:
        self._legacy_move(plan)
        self._legacy_click_at(int(plan.click_point.x), int(plan.click_point.y), button=button)

    def _legacy_key_down(self, key: str) -> None:
        self._send_armed(f"KEY_DOWN {str(key).strip().lower()}", require_ack=True)

    def _legacy_key_up(self, key: str) -> None:
        self._send_armed(f"KEY_UP {str(key).strip().lower()}", require_ack=True)

    def _press(self, key: str, hold_millis: int = 50) -> None:
        # ApprovedKeyIntent deliberately stores a canonical uppercase token,
        # while arduino_hid.v1 names multi-character keys in lowercase.
        if (
            not isinstance(hold_millis, int)
            or isinstance(hold_millis, bool)
            or not 1 <= hold_millis <= 250
        ):
            raise ArduinoHIDError("key hold must be between 1 and 250 milliseconds")
        self._send_armed(
            f"KEY_PRESS {str(key).strip().lower()} {hold_millis}",
            require_ack=True,
        )

    def _camera_hold(self, direction: str, hold_millis: int) -> dict[str, Any]:
        """Execute one negotiated atomic camera-direction hold."""

        from .input_capabilities import RequiredInputCapabilities

        normalized = str(direction).strip().lower()
        requirement = RequiredInputCapabilities.camera_hold(
            normalized,
            hold_millis,
        )
        requirement.require(self._input_capabilities())
        ack_timeout_ms = hold_millis + CAMERA_HOLD_ACK_MARGIN_MS
        if ack_timeout_ms > MAX_CAMERA_HOLD_ACK_TIMEOUT_MS:
            raise ArduinoHIDError("camera hold ACK timeout exceeds its bounded limit")
        line = self._send_armed(
            f"CAMERA_HOLD {normalized} {hold_millis}",
            require_ack=True,
            expected_token="CAMERA_HOLD",
            ack_timeout_ms=ack_timeout_ms,
        )
        fields = _fields_from_line(line)
        requested = fields.get("requestedDurationMs")
        applied = fields.get("appliedDurationMs")
        if (
            fields.get("direction") != normalized
            or isinstance(requested, bool)
            or not isinstance(requested, int)
            or isinstance(applied, bool)
            or not isinstance(applied, int)
            or requested != hold_millis
            or applied != hold_millis
        ):
            self._status.armed = False
            self._best_effort_stop_all()
            raise ArduinoHIDError("CAMERA_HOLD acknowledgement did not exactly match")
        return {
            "schema": "camera_key_hold_ack.v1",
            "direction": normalized,
            "requestedDurationMs": requested,
            "appliedDurationMs": applied,
            "ackTimeoutMs": ack_timeout_ms,
        }

    def _wheel(self, amount: int) -> dict[str, Any]:
        """Execute one negotiated nonzero bounded wheel step."""

        from .input_capabilities import RequiredInputCapabilities

        requirement = RequiredInputCapabilities.camera_zoom(amount)
        requirement.require(self._input_capabilities())
        line = self._send_armed(
            f"WHEEL {amount}",
            require_ack=True,
            expected_token="WHEEL",
        )
        fields = _fields_from_line(line)
        requested = fields.get("requestedAmount")
        applied = fields.get("appliedAmount")
        if (
            isinstance(requested, bool)
            or not isinstance(requested, int)
            or isinstance(applied, bool)
            or not isinstance(applied, int)
            or requested != amount
            or applied != amount
        ):
            self._status.armed = False
            self._best_effort_stop_all()
            raise ArduinoHIDError("WHEEL acknowledgement did not exactly match")
        return {
            "schema": "camera_zoom_ack.v1",
            "requestedAmount": requested,
            "appliedAmount": applied,
        }

    def _assert_foreground(
        self,
        allowed_titles: list[str] | tuple[str, ...],
        *,
        expected_pid: int | None = None,
    ) -> dict[str, Any]:
        info = _foreground_window_info()
        if not _foreground_allowed(info, allowed_titles):
            raise ArduinoHIDError("foreground window is not an allowed RuneLite window")
        if expected_pid is None or int(info.get("pid") or 0) != int(expected_pid):
            raise ArduinoHIDError("foreground RuneLite process does not own the telemetry observation")
        return info

    def _window_info_at_point(self, point: tuple[int, int]) -> dict[str, Any]:
        return _window_info_at_point(point)


def _read_monitor_json(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
        decoded = json.loads(text)
    except Exception:
        return None
    return decoded if isinstance(decoded, dict) else None


def _first_bool(payload: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes", "pass"}:
            return True
    return False


def _first_int(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    return 0


def check_arduino_monitor_status(
    *,
    require_monitor: bool = False,
    status_path: str | Path | None = None,
    expected_vid: str | None = None,
    expected_pid: str | None = None,
    max_event_age_ms: int = 3000,
    expected_com_port: str | None = None,
    live_input_backend: str | None = None,
    arduino_armed: bool | None = None,
    software_input_allowed: bool = False,
    direct_backend_bypass_count: int = 0,
    fail_on_injected: bool = True,
    fail_on_bypass: bool = True,
    require_armed: bool = False,
) -> dict[str, Any]:
    source = status_path or os.environ.get("OSRS_TELEMETRY_ARDUINO_MONITOR_STATUS")
    monitor_payload = read_status_payload(source)
    status = build_input_integrity_status(
        monitor_payload,
        status_source=source,
        expected_vid=expected_vid,
        expected_pid=expected_pid,
        expected_com_port=expected_com_port,
        live_input_backend=live_input_backend,
        arduino_backend_selected=(str(live_input_backend or "").lower() == "arduino") if live_input_backend else None,
        arduino_armed=arduino_armed,
        software_input_allowed=software_input_allowed,
        direct_backend_bypass_count=direct_backend_bypass_count,
        require_monitor=require_monitor,
        require_armed=require_armed,
        fail_on_injected=fail_on_injected,
        fail_on_bypass=fail_on_bypass,
        max_age_ms=max_event_age_ms,
    )
    if monitor_payload is None and require_monitor:
        status["monitorBlockReason"] = "monitor_status_unavailable"
        if "monitor_status_unavailable" not in status.get("blockers", []):
            status.setdefault("blockers", []).insert(0, "monitor_status_unavailable")
        status["status"] = "FAIL"
        status["monitorPass"] = False
    status["schema"] = "arduino_hid_monitor_status.v1"
    status["inputIntegrityStatus"] = {
        key: value
        for key, value in status.items()
        if key not in {"inputIntegrityStatus"}
    } | {"schema": "input_integrity_status.v1"}
    status["statusPath"] = str(source) if source else None
    return status

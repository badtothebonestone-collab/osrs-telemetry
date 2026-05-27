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


ACK_TOKENS = {"ACK", "OK", "PONG", "READY"}
REQUIRED_PROTOCOL = "arduino_hid.v1"
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
DEFAULT_COMMAND_TIMEOUT_MS = 2000
DEFAULT_SERIAL_LOCK_TIMEOUT_MS = 1500
DEFAULT_SERIAL_LOCK_STALE_MS = 120000
ARM_REQUIRED_COMMANDS = {
    "MOVE",
    "MOUSE_DOWN",
    "MOUSE_UP",
    "CLICK",
    "KEY_DOWN",
    "KEY_UP",
    "KEY_PRESS",
    "HOLD_KEYS",
}
_PROCESS_SERIAL_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_SERIAL_LOCKS_GUARD = threading.Lock()


class ArduinoHIDError(RuntimeError):
    pass


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
    watchdog_rearms: int = 0
    session_rearms: int = 0
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
            "watchdogRearms": self.watchdog_rearms,
            "sessionRearms": self.session_rearms,
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
        if value in {"0", "1"}:
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
    if name in {"KEY_DOWN", "KEY_UP", "KEY_PRESS", "HOLD_KEYS"}:
        return "serial_timeout_during_key_input"
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
    }
    return mapping.get(name)


def _not_armed_error(line: str) -> bool:
    return "NOT_ARMED" in str(line or "").upper().split()


def _cursor_position() -> tuple[int, int]:
    try:
        point = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):  # type: ignore[attr-defined]
            return int(point.x), int(point.y)
    except Exception:  # noqa: BLE001
        pass
    return (0, 0)


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


def _foreground_window_info() -> dict[str, Any]:
    if os.name != "nt":
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


class ArduinoHIDBackend:
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

    def __del__(self) -> None:
        try:
            if self._serial is not None:
                try:
                    self.stop_all()
                except Exception:
                    pass
            if self.armed:
                self.disarm()
            self.close()
        except Exception:  # noqa: BLE001
            pass

    @property
    def armed(self) -> bool:
        return bool(self._status.armed)

    def status(self) -> dict[str, Any]:
        return self._status.to_dict()

    def connect(self) -> None:
        if self._serial is not None:
            return
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
            self.close()
            raise

    def close(self) -> None:
        serial_obj = self._serial
        self._serial = None
        self._status.connected = False
        self._status.armed = False
        self._live_session_active = False
        if serial_obj is not None:
            try:
                serial_obj.close()
            except Exception:  # noqa: BLE001
                pass
        if self._serial_lock is not None:
            self._serial_lock.release()
            self._status.serial_lock = self._serial_lock.to_dict()
            self._serial_lock = None

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

    def _write_line(self, command: str) -> dict[str, Any]:
        if self._serial is None:
            raise ArduinoHIDError("Arduino serial connection is not open")
        encoded = (command.strip() + "\n").encode("utf-8")
        name = _command_name(command)
        trace: dict[str, Any] = {
            "schema": "arduino_serial_command_trace.v1",
            "serialOwner": self.serial_owner,
            "port": self.port,
            "baud": self.baud,
            "writeTimeoutMs": self.command_timeout_ms,
            "readTimeoutMs": self.handshake_timeout_ms,
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
            self._write_line("STOP_ALL")
            try:
                self._read_line()
            except Exception:
                pass
            self._status.stop_all_sent = True
        except Exception:  # noqa: BLE001
            pass
        self._status.armed = False

    def _read_line(self) -> str:
        if self._serial is None:
            raise ArduinoHIDError("Arduino serial connection is not open")
        started = time.monotonic()
        try:
            raw = self._serial.readline()
        except Exception as error:  # noqa: BLE001
            self._status.last_error = f"{type(error).__name__}: {error}"
            self._status.armed = False
            raise ArduinoHIDError(f"Arduino serial read failed: {error}") from error
        if not raw:
            self._status.timeouts += 1
            self._status.armed = False
            raise ArduinoHIDError("Arduino command timed out waiting for ACK")
        line = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else str(raw).strip()
        last = dict(self._status.last_command_trace)
        if last:
            last["ackDurationMs"] = int(round((time.monotonic() - started) * 1000))
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
        recover_watchdog_disarm: bool = False,
    ) -> str:
        self.connect() if self._serial is None else None
        name = _command_name(command)
        can_recover_watchdog_disarm = bool(recover_watchdog_disarm and self.armed and name in ARM_REQUIRED_COMMANDS)
        write_trace = self._write_line(command)
        expected = expected_token or _expected_response_token(command)
        last_line = ""
        for _attempt in range(6):
            try:
                line = self._read_line()
            except Exception:
                failed = dict(self._status.last_command_trace or write_trace)
                failed["status"] = "ACK_TIMEOUT_OR_READ_FAIL"
                failed["timeoutClassification"] = _command_timeout_classification(name, phase="read")
                self._append_command_trace(failed)
                if str(command).strip().split(" ", 1)[0].upper() != "STOP_ALL":
                    self._best_effort_stop_all()
                raise
            last_line = line
            token = _line_token(line)
            payload_token = _line_payload_token(line)
            if token == "ERR":
                if can_recover_watchdog_disarm and _not_armed_error(line):
                    self._status.last_error = line
                    self.arm(self.session_token)
                    self._status.watchdog_rearms += 1
                    self._status.session_rearms += 1
                    return self._send(command, require_ack=require_ack, expected_token=expected, recover_watchdog_disarm=False)
                self._status.ack_failures += 1
                self._status.last_error = line
                self._status.armed = False
                if name != "STOP_ALL":
                    self._best_effort_stop_all()
                raise ArduinoHIDError(f"Arduino rejected command {command!r}: {line}")
            if not require_ack:
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
                continue
        self._status.ack_failures += 1
        self._status.last_error = f"unexpected response: {last_line}"
        self._status.armed = False
        if name != "STOP_ALL":
            self._best_effort_stop_all()
        raise ArduinoHIDError(f"Arduino command {command!r} returned unexpected response: {last_line}")

    def _send_armed(self, command: str, *, require_ack: bool = True, expected_token: str | None = None) -> str:
        self._require_armed()
        return self._send(
            command,
            require_ack=require_ack,
            expected_token=expected_token,
            recover_watchdog_disarm=True,
        )

    def diagnostic_move_relative(self, dx: int, dy: int) -> dict[str, Any]:
        self._require_armed()
        dx_i = int(dx)
        dy_i = int(dy)
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

    def ping(self) -> str:
        return self._send("PING", require_ack=True, expected_token="PONG")

    def identify(self) -> dict[str, Any]:
        line = self._send("IDENTIFY", require_ack=True, expected_token="IDENTIFY")
        self._status.identity = _fields_from_line(line)
        self._status.identified = True
        self._status.protocol = str(self._status.identity.get("protocol") or "") or None
        return dict(self._status.identity)

    def capabilities(self) -> dict[str, Any]:
        line = self._send("CAPS", require_ack=True, expected_token="CAPS")
        self._status.capabilities = _fields_from_line(line)
        return dict(self._status.capabilities)

    def firmware_status(self) -> dict[str, Any]:
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

    def port_health(self) -> dict[str, Any]:
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
            self.connect()
            payload["lock"] = dict(self._status.serial_lock)
            payload["ping"] = self.ping()
            payload["identify"] = self.identify()
            payload["caps"] = self.capabilities()
            payload["status"] = self.firmware_status()
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
            payload["backendStatus"] = self.status()
        return payload

    def stop_all(self) -> dict[str, Any]:
        try:
            self._send("STOP_ALL", require_ack=True, expected_token="STOP_ALL")
            self._status.stop_all_sent = True
            self._status.armed = False
            self._status.firmware_status = {}
        except Exception as error:  # noqa: BLE001
            self._status.last_error = f"{type(error).__name__}: {error}"
            self._status.armed = False
            raise
        return self.status()

    def _verify_protocol(self) -> None:
        protocol = str(self._status.identity.get("protocol") or "")
        if protocol != REQUIRED_PROTOCOL:
            self._status.last_error = f"unsupported protocol: {protocol or 'missing'}"
            raise ArduinoHIDError(
                f"Arduino firmware protocol mismatch: expected {REQUIRED_PROTOCOL}, got {protocol or 'missing'}. "
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

    def arm(self, session_token: str | None = None) -> dict[str, Any]:
        token = session_token or self.session_token
        self.session_token = token
        self._status.session_token_hash = _token_hash(token)
        try:
            self.stop_all()
        except Exception:
            if self.fail_closed:
                raise
        self.ping()
        self.identify()
        self.capabilities()
        firmware_status = self.firmware_status()
        if firmware_status.get("armed"):
            raise ArduinoHIDError("Arduino firmware was armed before ARM; refusing live session")
        if int(firmware_status.get("keysDown") or 0) != 0 or int(firmware_status.get("mouseButtonsDown") or 0) != 0:
            raise ArduinoHIDError("Arduino firmware reports held keys/buttons before ARM")
        self._verify_protocol()
        self._send(f"ARM {token}", require_ack=True)
        self._status.armed = True
        self._live_session_active = True
        return self.status()

    def disarm(self) -> dict[str, Any]:
        if self._serial is not None:
            try:
                self._send("DISARM", require_ack=True)
            except Exception as error:  # noqa: BLE001
                self._status.last_error = f"{type(error).__name__}: {error}"
                try:
                    self.stop_all()
                except Exception:
                    pass
        self._status.armed = False
        self._live_session_active = False
        return self.status()

    def _require_armed(self) -> None:
        if self.fail_closed and not self.armed:
            raise ArduinoHIDError("Arduino HID backend is not armed")

    def ensure_armed(self) -> bool:
        if self.armed:
            return True
        if not self._live_session_active:
            return False
        self.arm(self.session_token)
        self._status.session_rearms += 1
        return bool(self.armed)

    def current_position(self) -> tuple[int, int]:
        position = _cursor_position()
        self._tracked_position = position
        return position

    def configure_movement_safety(
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

    def clear_movement_safety(self) -> None:
        self._movement_safety = None

    def _abort_movement(self, reason: str, trace: dict[str, Any]) -> None:
        try:
            self.stop_all()
        except Exception as error:  # noqa: BLE001
            trace.setdefault("cleanupWarnings", []).append(f"stop_all failed: {type(error).__name__}: {error}")
        try:
            self.disarm()
            trace["disarmedOnAbort"] = True
        except Exception as error:  # noqa: BLE001
            trace.setdefault("cleanupWarnings", []).append(f"disarm failed: {type(error).__name__}: {error}")
        self.last_movement_trace = trace
        _movement_abort_trace(reason, trace)

    def move_to_absolute(
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
        current = self.current_position()
        trace["cursorPositionBefore"] = {"x": current[0], "y": current[1]}
        trace["cursorInsideAllowedRegion"] = _point_in_region(current, region, margin=0)
        if not trace["cursorInsideAllowedRegion"]:
            self._abort_movement("cursor_start_outside_allowed_region", trace)
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
                position = self.current_position()
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
                if not result["insideAllowedRegion"]:
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
        final = self.current_position()
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
            actual_x, actual_y = self.current_position()
            dx = target_x - int(actual_x)
            dy = target_y - int(actual_y)
            if max(abs(dx), abs(dy)) <= ENDPOINT_CORRECTION_TOLERANCE_PX:
                self._tracked_position = (actual_x, actual_y)
                return
            self._move_relative_chunked(dx, dy, duration_ms=0)
            self.sleep_func(0.01)
        self._tracked_position = self.current_position()

    def move_relative(self, dx: int, dy: int, *, duration_ms: int = 0) -> None:
        self._move_relative_chunked(int(dx), int(dy), duration_ms=duration_ms)
        if self._tracked_position is not None:
            self._tracked_position = (self._tracked_position[0] + int(dx), self._tracked_position[1] + int(dy))

    def _move_to(self, x: int, y: int, *, duration_ms: int = 0) -> None:
        current = self.current_position()
        dx = int(x) - int(current[0])
        dy = int(y) - int(current[1])
        self._move_relative_chunked(dx, dy, duration_ms=duration_ms)
        self._correct_to_endpoint(int(x), int(y))

    def move(self, plan: Any) -> None:
        self._require_armed()
        safety = self._movement_safety if isinstance(self._movement_safety, dict) and self._movement_safety.get("enabled") else None
        if safety:
            points = list(getattr(plan, "points", []) or [])
            click_point = getattr(plan, "click_point", None)
            target_point = click_point if click_point is not None else (points[-1] if points else None)
            if target_point is None:
                return
            self.move_to_absolute(
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
            current = self.current_position()
            self._move_relative_chunked(target_x - current[0], target_y - current[1], duration_ms=duration)
            current = self.current_position()
            self._tracked_position = current
            last_time = timestamp
        click_point = getattr(plan, "click_point", None)
        if click_point is not None:
            self._correct_to_endpoint(int(click_point.x), int(click_point.y))

    def mouse_down(self, *, button: str = "left") -> None:
        self._send_armed(f"MOUSE_DOWN {button}", require_ack=True)

    def mouse_up(self, *, button: str = "left") -> None:
        self._send_armed(f"MOUSE_UP {button}", require_ack=True)

    def click_at(self, x: int, y: int, *, button: str = "left", hold_ms: int = 0) -> None:
        self._require_armed()
        safety = self._movement_safety if isinstance(self._movement_safety, dict) and self._movement_safety.get("enabled") else None
        if safety:
            self.move_to_absolute(
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

    def move_and_click(self, plan: Any, *, button: str = "left") -> None:
        self.move(plan)
        self.click_at(int(plan.click_point.x), int(plan.click_point.y), button=button)

    def key_down(self, key: str) -> None:
        self._send_armed(f"KEY_DOWN {key}", require_ack=True)

    def key_up(self, key: str) -> None:
        self._send_armed(f"KEY_UP {key}", require_ack=True)

    def press(self, key: str) -> None:
        self._send_armed(f"KEY_PRESS {key} 50", require_ack=True)


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

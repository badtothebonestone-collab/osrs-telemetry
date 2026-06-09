from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ARDUINO_EVENT_SCHEMA = "arduino_event.v1"
ARDUINO_STATUS_SCHEMA = "arduino_input_bridge_status.v1"
ARDUINO_CALIBRATION_SCHEMA = "arduino_calibration.v1"
ARDUINO_ACTION_COMMAND_SCHEMA = "arduino_action_command.v1"
DEFAULT_BAUD = 115200

ARDUINO_HEALTH_COMMANDS = {"PING", "IDENTIFY", "CAPS", "STATUS", "STOP_ALL", "RESET_SAFE", "ARM", "DISARM"}
ARDUINO_MOVEMENT_COMMANDS = {"MOVE", "MOVE_REL", "MOUSE_MOVE", "DRAG_MOVE"}
ARDUINO_CLICK_COMMANDS = {"CLICK", "MOUSE_DOWN", "MOUSE_UP", "DOUBLE_CLICK"}
ARDUINO_ACTION_COMMANDS = ARDUINO_MOVEMENT_COMMANDS | ARDUINO_CLICK_COMMANDS | {
    "KEY_DOWN",
    "KEY_UP",
    "KEY_PRESS",
    "HOLD_KEYS",
    "WHEEL",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: str | Path, payload: dict[str, Any], *, pretty: bool = True) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, sort_keys=False, default=str)
        handle.write("\n")
    temp.replace(output)


class JsonlWriter:
    def __init__(self, path: str | Path, *, pretty: bool = False) -> None:
        self.path = Path(path)
        self.pretty = bool(pretty)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._handle = self.path.open("a", encoding="utf-8")

    def write(self, payload: dict[str, Any]) -> None:
        text = json.dumps(payload, indent=None, separators=(",", ":") if not self.pretty else None, default=str)
        with self._lock:
            self._handle.write(text + "\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._handle.close()


def _serial_list_ports() -> list[dict[str, Any]]:
    try:
        import serial.tools.list_ports  # type: ignore
    except ImportError:
        return []
    ports = []
    for port in serial.tools.list_ports.comports():  # type: ignore[attr-defined]
        ports.append(
            {
                "device": getattr(port, "device", None),
                "name": getattr(port, "name", None),
                "description": getattr(port, "description", None),
                "hwid": getattr(port, "hwid", None),
                "vid": getattr(port, "vid", None),
                "pid": getattr(port, "pid", None),
                "serial_number": getattr(port, "serial_number", None),
            }
        )
    return ports


def _windows_com_ports() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    script = "[System.IO.Ports.SerialPort]::getportnames() | ConvertTo-Json"
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=5, check=False)
    except OSError:
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        decoded = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    values = decoded if isinstance(decoded, list) else [decoded]
    return [{"device": str(value), "name": str(value), "description": "Windows COM port"} for value in values if value]


def discover_arduino_ports() -> list[dict[str, Any]]:
    ports = _serial_list_ports()
    if not ports:
        ports = _windows_com_ports()
    likely = []
    for port in ports:
        text = " ".join(str(port.get(key) or "") for key in ("device", "name", "description", "hwid")).lower()
        item = dict(port)
        item["likelyArduino"] = any(token in text for token in ("arduino", "leonardo", "micro", "vid_2341", "pid_8036"))
        likely.append(item)
    likely.sort(key=lambda item: (not bool(item.get("likelyArduino")), str(item.get("device") or "")))
    return likely


def parse_firmware_line(line: str) -> dict[str, Any]:
    parts = str(line or "").strip().split()
    payload: dict[str, Any] = {"raw": str(line or "").strip()}
    if parts:
        payload["token"] = parts[0].upper()
    if len(parts) >= 2:
        payload["topic"] = parts[1].upper()
    for part in parts[2:] if len(parts) >= 2 and parts[0].upper() == "OK" else parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if value in {"0", "1"}:
            payload[key] = value == "1"
            continue
        try:
            payload[key] = int(value)
        except ValueError:
            payload[key] = value
    return payload


def calibration_result(commanded_dx: int, commanded_dy: int, observed_dx: int, observed_dy: int) -> dict[str, Any]:
    commanded_dx = int(commanded_dx or 0)
    commanded_dy = int(commanded_dy or 0)
    observed_dx = int(observed_dx or 0)
    observed_dy = int(observed_dy or 0)
    return {
        "commanded_dx": commanded_dx,
        "commanded_dy": commanded_dy,
        "observed_dx": observed_dx,
        "observed_dy": observed_dy,
        "error_dx": observed_dx - commanded_dx,
        "error_dy": observed_dy - commanded_dy,
        "scale_x": (observed_dx / commanded_dx) if commanded_dx else None,
        "scale_y": (observed_dy / commanded_dy) if commanded_dy else None,
    }


def load_arduino_events(path: str | Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict):
                    events.append(decoded)
    except OSError:
        return []
    return events


def load_arduino_action_commands(path: str | Path) -> list[dict[str, Any]]:
    return load_arduino_events(path)


def load_combined_arduino_events(recording_dir: str | Path) -> list[dict[str, Any]]:
    recording = Path(recording_dir)
    events = load_arduino_events(recording / "arduino_events.jsonl")
    commands = load_arduino_action_commands(recording / "arduino_action_commands.jsonl")
    combined = list(events) + list(commands)
    combined.sort(key=lambda item: (float(item.get("monotonic_time") or item.get("sent_at_monotonic") or 0.0), int(item.get("event_seq") or 0)))
    return combined


def summarize_arduino_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    kind_counts: dict[str, int] = {}
    command_count = 0
    status_health_command_count = 0
    action_command_count = 0
    movement_command_count = 0
    click_command_count = 0
    ack_count = 0
    errors = []
    ports = []
    protocols = []
    passthrough_modes = []
    for event in events:
        kind = str(event.get("kind") or "unknown")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        if event.get("port") is not None:
            ports.append(str(event.get("port")))
        if event.get("protocol") is not None:
            protocols.append(str(event.get("protocol")))
        if event.get("passthroughMode") is not None:
            passthrough_modes.append(str(event.get("passthroughMode")))
        if isinstance(event.get("status"), dict) and event["status"].get("protocol") is not None:
            protocols.append(str(event["status"].get("protocol")))
        if kind in {"command_sent", "action_command"}:
            command_count += 1
            command = str(event.get("command") or event.get("commandSent") or event.get("commandName") or "").upper()
            trace = event.get("commandTrace") if isinstance(event.get("commandTrace"), dict) else {}
            command = command or str(event.get("command_kind") or trace.get("commandName") or "").upper()
            if command in ARDUINO_HEALTH_COMMANDS:
                status_health_command_count += 1
            if command in ARDUINO_ACTION_COMMANDS:
                action_command_count += 1
            if command in ARDUINO_MOVEMENT_COMMANDS:
                movement_command_count += 1
            if command in ARDUINO_CLICK_COMMANDS:
                click_command_count += 1
            if event.get("ack_received") is True:
                ack_count += 1
                if event.get("protocol") is not None:
                    protocols.append(str(event.get("protocol")))
        if kind == "ack_received":
            ack_count += 1
            ack = event.get("acknowledgement") if isinstance(event.get("acknowledgement"), dict) else {}
            if ack.get("protocol") is not None:
                protocols.append(str(ack.get("protocol")))
        if kind == "error":
            errors.append(event)
    connected = bool(events and (kind_counts.get("connect") or command_count or ack_count))
    mirror_seen = any("mirror" in str(value).lower() for value in passthrough_modes)
    if not events:
        classification = "arduino_unavailable"
    elif action_command_count:
        classification = "arduino_action_commands_seen"
    elif mirror_seen:
        classification = "arduino_mirror_mode_seen"
    elif connected and command_count:
        classification = "arduino_status_only"
    elif connected:
        classification = "arduino_bridge_connected"
    else:
        classification = "arduino_mapping_only"
    warnings: list[str] = []
    if not events:
        warnings.append("arduino_events.jsonl is missing or empty")
    elif classification == "arduino_status_only":
        warnings.append("Arduino bridge connected, but no per-action movement/click command stream was captured.")
    return {
        "schema": "arduino_trace_summary.v1",
        "status": "PASS" if events and not errors else ("WARN" if events else "WARN"),
        "classification": classification,
        "eventCount": len(events),
        "kindCounts": dict(sorted(kind_counts.items())),
        "commandCount": command_count,
        "statusHealthCommandCount": status_health_command_count,
        "actionCommandCount": action_command_count,
        "movementCommandCount": movement_command_count,
        "clickCommandCount": click_command_count,
        "ackCount": ack_count,
        "errorCount": len(errors),
        "port": ports[-1] if ports else None,
        "protocol": protocols[-1] if protocols else None,
        "perActionHidEvidence": bool(action_command_count),
        "firstEventTime": events[0].get("wall_time_utc") if events else None,
        "lastEventTime": events[-1].get("wall_time_utc") if events else None,
        "lastEvent": events[-1] if events else None,
        "warnings": warnings,
    }


class ArduinoInputBridge:
    def __init__(
        self,
        recording_dir: str | Path | None = None,
        *,
        recording_id: str = "standalone",
        port: str | None = None,
        baud: int = DEFAULT_BAUD,
        protocol: str = "arduino_hid.v1",
        passthrough_mode: str = "bridge",
        include_raw: bool = False,
        pretty: bool = False,
        serial_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.recording_dir = Path(recording_dir) if recording_dir is not None else None
        self.recording_id = recording_id
        self.port = port
        self.baud = int(baud or DEFAULT_BAUD)
        self.protocol = protocol
        self.passthrough_mode = passthrough_mode or "bridge"
        self.include_raw = bool(include_raw)
        self.serial_factory = serial_factory
        self.writer = JsonlWriter(self.recording_dir / "arduino_events.jsonl", pretty=pretty) if self.recording_dir else None
        self._seq = 0
        self.backend: Any | None = None
        self.status_payload: dict[str, Any] = self._base_status("not_started")
        self.warnings: list[str] = []

    def _base_status(self, status: str) -> dict[str, Any]:
        return {
            "schema": ARDUINO_STATUS_SCHEMA,
            "status": status,
            "available": False,
            "recording_id": self.recording_id,
            "port": self.port,
            "baud": self.baud,
            "protocol": self.protocol,
            "passthroughMode": self.passthrough_mode,
            "connected": False,
            "backendStatus": None,
            "warnings": [],
            "updated_at_utc": utc_now(),
        }

    def _event(self, kind: str, **payload: Any) -> dict[str, Any]:
        self._seq += 1
        event = {
            "schema": ARDUINO_EVENT_SCHEMA,
            "recording_id": self.recording_id,
            "event_seq": self._seq,
            "monotonic_time": time.monotonic(),
            "wall_time_utc": utc_now(),
            "port": self.port,
            "baud": self.baud,
            "kind": kind,
        }
        event.update(payload)
        if self.writer:
            self.writer.write(event)
        return event

    def write_status(self) -> None:
        if self.recording_dir:
            atomic_write_json(self.recording_dir / "arduino_status.json", self.status_payload)

    def start(self, *, require_available: bool = False) -> dict[str, Any]:
        if self.passthrough_mode == "off":
            self.status_payload = self._base_status("disabled")
            self.status_payload["warnings"].append("Arduino passthrough mode is off.")
            self.write_status()
            return self.status_payload
        if self.passthrough_mode == "label_only":
            self.status_payload = self._base_status("label_only")
            self.status_payload["available"] = False
            self.status_payload["warnings"].append("Recording marked for Arduino-style analysis without connecting hardware.")
            self._event("status", status="label_only")
            self.write_status()
            return self.status_payload
        if self.passthrough_mode == "mirror":
            self.warnings.append("mirror mode requested; bridge startup records status only unless a probe or live action command source sends Arduino HID commands.")
        if not self.port:
            discovered = discover_arduino_ports()
            self.port = str(discovered[0].get("device")) if discovered else None
        if not self.port:
            self.status_payload = self._base_status("unavailable")
            self.status_payload["warnings"].append("No Arduino serial port configured or discovered.")
            self._event("error", error="no_arduino_port")
            self.write_status()
            if require_available:
                raise RuntimeError("Arduino is required, but no serial port was configured or discovered.")
            return self.status_payload
        try:
            from input_control.backend_arduino_hid import ArduinoHIDBackend

            self.backend = ArduinoHIDBackend(port=self.port, baud=self.baud, serial_factory=self.serial_factory, fail_closed=False)
            self._event("connect")
            health = self.backend.port_health()
            backend_status = self.backend.status()
            self.status_payload = self._base_status("PASS" if health.get("portHealth") == "PASS" else "WARN")
            self.status_payload.update(
                {
                    "available": bool(health.get("portHealth") == "PASS"),
                    "connected": bool(backend_status.get("connected")),
                    "backendStatus": backend_status,
                    "portHealth": health,
                    "warnings": list(self.warnings) + list(health.get("warnings") or []),
                    "updated_at_utc": utc_now(),
                }
            )
            for trace in health.get("commandTraces") or []:
                command = trace.get("commandName")
                if command:
                    self._event("command_sent", command=command, commandTrace=trace if self.include_raw else None)
                if trace.get("ackLine"):
                    self._event("ack_received", command=command, acknowledgement=parse_firmware_line(str(trace.get("ackLine"))))
        except Exception as error:  # noqa: BLE001
            self.status_payload = self._base_status("unavailable")
            self.status_payload["warnings"] = list(self.warnings) + [f"{type(error).__name__}: {error}"]
            self._event("error", error=f"{type(error).__name__}: {error}")
            self.write_status()
            if require_available:
                raise RuntimeError(f"Arduino is required, but bridge startup failed: {type(error).__name__}: {error}") from error
            return self.status_payload
        self.write_status()
        return self.status_payload

    def stop(self) -> dict[str, Any]:
        if self.backend is not None:
            try:
                status = self.backend.stop_all()
                self._event("status", status="stop_all", backendStatus=status if self.include_raw else None)
            except Exception as error:  # noqa: BLE001
                self._event("error", error=f"{type(error).__name__}: {error}")
            try:
                self.backend.close()
            except Exception:
                pass
        self._event("disconnect")
        if self.writer:
            self.writer.close()
        self.status_payload["connected"] = False
        self.status_payload["updated_at_utc"] = utc_now()
        self.write_status()
        return self.status_payload

    def calibrate(self, *, commanded_dx: int = 10, commanded_dy: int = 0, observed_dx: int = 0, observed_dy: int = 0) -> dict[str, Any]:
        sample = calibration_result(commanded_dx, commanded_dy, observed_dx, observed_dy)
        payload = {
            "schema": ARDUINO_CALIBRATION_SCHEMA,
            "recording_id": self.recording_id,
            "port": self.port,
            "baud": self.baud,
            "generated_at_utc": utc_now(),
            "samples": [sample],
            "warnings": ["manual/observed calibration sample; live MOVE calibration is intentionally not run by default"],
        }
        self._event("calibration_sample", **sample)
        if self.recording_dir:
            atomic_write_json(self.recording_dir / "arduino_calibration.json", payload)
        return payload


class ArduinoCommandClient:
    """Small synchronous command wrapper over the existing ArduinoHIDBackend."""

    def __init__(
        self,
        recording_dir: str | Path | None = None,
        *,
        recording_id: str = "standalone",
        port: str | None = None,
        baud: int = DEFAULT_BAUD,
        protocol: str = "arduino_hid.v1",
        pretty: bool = False,
        serial_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.recording_dir = Path(recording_dir) if recording_dir is not None else None
        self.recording_id = recording_id
        self.port = port
        self.baud = int(baud or DEFAULT_BAUD)
        self.protocol = protocol
        self.serial_factory = serial_factory
        self.writer = JsonlWriter(self.recording_dir / "arduino_action_commands.jsonl", pretty=pretty) if self.recording_dir else None
        self.backend: Any | None = None
        self.last_record: dict[str, Any] | None = None
        self.status_payload: dict[str, Any] = {}

    def connect(self) -> dict[str, Any]:
        if self.backend is None:
            if not self.port:
                discovered = discover_arduino_ports()
                self.port = str(discovered[0].get("device")) if discovered else None
            from input_control.backend_arduino_hid import ArduinoHIDBackend

            self.backend = ArduinoHIDBackend(port=self.port, baud=self.baud, serial_factory=self.serial_factory, fail_closed=False)
        health = self.backend.port_health()
        self.status_payload = {
            "schema": ARDUINO_STATUS_SCHEMA,
            "status": "PASS" if health.get("portHealth") == "PASS" else "WARN",
            "available": bool(health.get("portHealth") == "PASS"),
            "recording_id": self.recording_id,
            "port": self.port,
            "baud": self.baud,
            "protocol": self.protocol,
            "passthroughMode": "mirror",
            "connected": bool(self.backend.status().get("connected")),
            "backendStatus": self.backend.status(),
            "portHealth": health,
            "warnings": list(health.get("warnings") or []),
            "updated_at_utc": utc_now(),
        }
        if self.recording_dir:
            atomic_write_json(self.recording_dir / "arduino_status.json", self.status_payload)
        return self.status_payload

    def status(self) -> dict[str, Any]:
        return self.connect()

    def _ensure_armed(self) -> None:
        self.connect()
        if self.backend is not None and not self.backend.armed:
            self.backend.arm()

    def _last_ack_line(self) -> str | None:
        if self.backend is None:
            return None
        trace = self.backend.status().get("lastCommandTrace") if isinstance(self.backend.status(), dict) else {}
        if isinstance(trace, dict):
            return trace.get("ackLine")
        return None

    def _write_record(self, record: dict[str, Any]) -> dict[str, Any]:
        self.last_record = record
        if self.writer:
            self.writer.write(record)
        return record

    def _unsupported(self, command_kind: str, payload: dict[str, Any], reason: str, hint: str) -> dict[str, Any]:
        now = time.monotonic()
        return self._write_record(
            {
                "schema": ARDUINO_ACTION_COMMAND_SCHEMA,
                "kind": "command_sent",
                "recording_id": self.recording_id,
                "command_id": f"cmd_{uuid.uuid4().hex[:12]}",
                "command": command_kind,
                "command_kind": command_kind,
                "monotonic_time": now,
                "sent_at_monotonic": now,
                "sent_at_utc": utc_now(),
                "port": self.port,
                "baud": self.baud,
                "protocol": self.protocol,
                "payload": payload,
                "supported": False,
                "reason": reason,
                "required_protocol_hint": hint,
                "expected_ack": None,
                "ack_received": False,
                "ack_at_monotonic": None,
                "ack_latency_ms": None,
                "error": reason,
                "raw_line": None,
            }
        )

    def _send_action(
        self,
        command_kind: str,
        payload: dict[str, Any],
        sender: Callable[[], Any],
        *,
        expected_ack: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sent = time.monotonic()
        record = {
            "schema": ARDUINO_ACTION_COMMAND_SCHEMA,
            "kind": "command_sent",
            "recording_id": self.recording_id,
            "command_id": f"cmd_{uuid.uuid4().hex[:12]}",
            "command": command_kind,
            "command_kind": command_kind,
            "monotonic_time": sent,
            "sent_at_monotonic": sent,
            "sent_at_utc": utc_now(),
            "port": self.port,
            "baud": self.baud,
            "protocol": self.protocol,
            "payload": dict(payload),
            "supported": True,
            "expected_ack": expected_ack,
            "ack_received": False,
            "ack_at_monotonic": None,
            "ack_latency_ms": None,
            "error": None,
            "raw_line": None,
        }
        if metadata:
            record.update(dict(metadata))
        record.update({key: value for key, value in payload.items() if key in {"dx", "dy", "button", "key", "delta"}})
        try:
            self._ensure_armed()
            response = sender()
            ack_time = time.monotonic()
            raw_line = self._last_ack_line()
            if raw_line is None and isinstance(response, str):
                raw_line = response
            if raw_line is None and isinstance(response, dict):
                raw_line = response.get("firmwareAck")
            record.update(
                {
                    "ack_received": True,
                    "ack_at_monotonic": ack_time,
                    "ack_latency_ms": round((ack_time - sent) * 1000.0, 3),
                    "raw_line": raw_line,
                }
            )
        except Exception as error:  # noqa: BLE001
            record.update(
                {
                    "ack_at_monotonic": time.monotonic(),
                    "ack_latency_ms": round((time.monotonic() - sent) * 1000.0, 3),
                    "error": f"{type(error).__name__}: {error}",
                    "raw_line": self._last_ack_line(),
                }
            )
        return self._write_record(record)

    def send_move(self, dx: int, dy: int, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        dx_i = int(dx)
        dy_i = int(dy)
        if max(abs(dx_i), abs(dy_i)) > 20:
            chunks = []
            steps = max(abs(dx_i), abs(dy_i)) // 20 + (1 if max(abs(dx_i), abs(dy_i)) % 20 else 0)
            previous_x = previous_y = 0
            for index in range(1, max(1, steps) + 1):
                next_x = int(round(dx_i * index / max(1, steps)))
                next_y = int(round(dy_i * index / max(1, steps)))
                chunk_metadata = dict(metadata or {})
                chunk_metadata.update({"aggregateSourceDx": dx_i, "aggregateSourceDy": dy_i, "aggregateChunkIndex": index, "aggregateChunkCount": max(1, steps)})
                chunks.append(self.send_move(next_x - previous_x, next_y - previous_y, metadata=chunk_metadata))
                previous_x, previous_y = next_x, next_y
            aggregate = dict(chunks[-1]) if chunks else {}
            aggregate["aggregate"] = True
            aggregate["chunks"] = chunks
            aggregate["dx"] = dx_i
            aggregate["dy"] = dy_i
            aggregate["payload"] = {"dx": dx_i, "dy": dy_i}
            return aggregate
        return self._send_action("MOVE", {"dx": dx_i, "dy": dy_i}, lambda: self.backend.diagnostic_move_relative(dx_i, dy_i), expected_ack="MOVE", metadata=metadata)

    def send_click(self, button: str = "left", *, hold_ms: int = 40, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        button_s = str(button or "left")
        hold_i = max(0, min(250, int(hold_ms or 0)))
        return self._send_action(
            "CLICK",
            {"button": button_s, "hold_ms": hold_i},
            lambda: self.backend._send_armed(f"CLICK {button_s} {hold_i}", require_ack=True, expected_token="CLICK"),
            expected_ack="CLICK",
            metadata=metadata,
        )

    def send_mouse_down(self, button: str = "left", *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        button_s = str(button or "left")
        return self._send_action("MOUSE_DOWN", {"button": button_s}, lambda: self.backend.mouse_down(button=button_s), expected_ack="MOUSE_DOWN", metadata=metadata)

    def send_mouse_up(self, button: str = "left", *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        button_s = str(button or "left")
        return self._send_action("MOUSE_UP", {"button": button_s}, lambda: self.backend.mouse_up(button=button_s), expected_ack="MOUSE_UP", metadata=metadata)

    def send_wheel(self, delta: int) -> dict[str, Any]:
        return self._unsupported("WHEEL", {"delta": int(delta or 0)}, "unsupported_protocol", "Firmware arduino_hid.v1 currently has no WHEEL command.")

    def send_key_down(self, key: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        key_s = str(key or "")
        return self._send_action("KEY_DOWN", {"key": key_s}, lambda: self.backend.key_down(key_s), expected_ack="KEY_DOWN", metadata=metadata)

    def send_key_up(self, key: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        key_s = str(key or "")
        return self._send_action("KEY_UP", {"key": key_s}, lambda: self.backend.key_up(key_s), expected_ack="KEY_UP", metadata=metadata)

    def read_ack(self) -> dict[str, Any] | None:
        return dict(self.last_record) if isinstance(self.last_record, dict) else None

    def poll_ack(self) -> dict[str, Any] | None:
        return self.read_ack()

    def close(self) -> None:
        if self.backend is not None:
            try:
                if self.backend.armed:
                    self.backend.disarm()
            except Exception:
                pass
            try:
                self.backend.close()
            except Exception:
                pass
        if self.writer:
            self.writer.close()


def status_payload(port: str | None = None, *, baud: int = DEFAULT_BAUD) -> dict[str, Any]:
    bridge = ArduinoInputBridge(None, port=port, baud=baud, passthrough_mode="bridge")
    try:
        return bridge.start(require_available=False)
    finally:
        try:
            bridge.stop()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Arduino HID input-path status and recording bridge.")
    parser.add_argument("--status", action="store_true", help="Print Arduino bridge status.")
    parser.add_argument("--list-ports", action="store_true", help="List likely serial ports.")
    parser.add_argument("--port", help="Arduino serial port, for example COM6.")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--calibrate", action="store_true", help="Write a simple calibration payload.")
    parser.add_argument("--out", help="Recording folder or output JSON/JSONL path.")
    parser.add_argument("--record-events", action="store_true", help="Record Arduino status/events until interrupted.")
    parser.add_argument("--passthrough-mode", choices=("off", "label_only", "bridge", "mirror"), default="bridge")
    parser.add_argument("--move", nargs=2, type=int, metavar=("DX", "DY"), help="Send one relative Arduino MOVE command.")
    parser.add_argument("--click", choices=("left", "right", "middle"), help="Send one Arduino CLICK command.")
    args = parser.parse_args(argv)

    if args.list_ports:
        print(json.dumps({"schema": "arduino_port_list.v1", "ports": discover_arduino_ports()}, indent=2, default=str))
        return 0

    out = Path(args.out) if args.out else None
    recording_dir = out.parent if out and out.suffix else out
    if args.move or args.click:
        client = ArduinoCommandClient(recording_dir, port=args.port, baud=args.baud)
        records = []
        try:
            if args.move:
                records.append(client.send_move(int(args.move[0]), int(args.move[1])))
            if args.click:
                records.append(client.send_click(args.click))
        finally:
            client.close()
        payload = {"schema": "arduino_action_command_result.v1", "records": records, "status": "PASS" if all(not record.get("error") for record in records) else "FAIL"}
        if out and out.suffix:
            atomic_write_json(out, payload)
        print(json.dumps(payload, indent=2, default=str))
        return 0 if payload["status"] == "PASS" else 1
    bridge = ArduinoInputBridge(recording_dir, port=args.port, baud=args.baud, passthrough_mode=args.passthrough_mode)
    if args.calibrate:
        bridge.start(require_available=False)
        payload = bridge.calibrate()
        bridge.stop()
        if out and out.suffix:
            atomic_write_json(out, payload)
        print(json.dumps(payload, indent=2, default=str))
        return 0
    if args.record_events:
        status = bridge.start(require_available=False)
        print(json.dumps(status, indent=2, default=str))
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            bridge.stop()
        return 0
    payload = bridge.start(require_available=False)
    bridge.stop()
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

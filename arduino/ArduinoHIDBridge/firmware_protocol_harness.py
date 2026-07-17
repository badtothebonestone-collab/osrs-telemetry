"""Deterministic source-contract and golden-vector checks for ArduinoHIDBridge.

This harness never opens a serial port and never uploads firmware.  It binds a
small protocol model to constants, handlers, dispatch, and exact response text
in the sketch, while arduino-cli separately compiles the actual sketch.
"""

from __future__ import annotations

import json
import re
import unittest
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SKETCH_PATH = ROOT / "ArduinoHIDBridge.ino"
VECTORS_PATH = ROOT / "firmware_protocol_vectors.json"
SKETCH = SKETCH_PATH.read_text(encoding="utf-8")
GOLDEN = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


def _numeric_constant(name: str) -> int:
    match = re.search(
        rf"const\s+(?:unsigned\s+long|int)\s+{re.escape(name)}\s*=\s*(-?\d+)\s*;",
        SKETCH,
    )
    if match is None:
        raise AssertionError(f"missing numeric sketch constant {name}")
    return int(match.group(1))


def _string_constant(name: str) -> str:
    match = re.search(
        rf'const\s+char\s+\*{re.escape(name)}\s*=\s*"([^"]+)"\s*;',
        SKETCH,
    )
    if match is None:
        raise AssertionError(f"missing string sketch constant {name}")
    return match.group(1)


def _function_body(name: str) -> str:
    match = re.search(rf"\b(?:void|char|bool|int|long)\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", SKETCH)
    if match is None:
        raise AssertionError(f"missing sketch function {name}")
    body_start = match.end()
    depth = 1
    cursor = body_start
    while cursor < len(SKETCH) and depth:
        if SKETCH[cursor] == "{":
            depth += 1
        elif SKETCH[cursor] == "}":
            depth -= 1
        cursor += 1
    if depth:
        raise AssertionError(f"unclosed sketch function {name}")
    return SKETCH[body_start : cursor - 1]


def _caps_response() -> str:
    body = _function_body("sendCapabilities")
    match = re.search(r'Serial\.println\(F\("([^"]+)"\)\);', body)
    if match is None:
        raise AssertionError("CAPS response must be one immutable flash string")
    return match.group(1)


def _fields(response: str) -> dict[str, str]:
    return dict(item.split("=", 1) for item in response.split()[2:])


@dataclass
class FirmwareModel:
    """Golden model for only the safety-critical v2 additions."""

    armed: bool = False
    keys_down: int = 0
    mouse_buttons_down: int = 0
    now_ms: int = 0
    last_command_ms: int = 0
    wheel_events: list[int] = field(default_factory=list)
    camera_events: list[tuple[str, int]] = field(default_factory=list)

    def _stop_all(self) -> None:
        self.keys_down = 0
        self.mouse_buttons_down = 0
        self.armed = False

    def _fatal(self, code: str, command: str) -> str:
        self._stop_all()
        return f"ERR {code} {command}" if command else f"ERR {code}"

    @staticmethod
    def _integer(token: str) -> int | None:
        if re.fullmatch(r"[+-]?\d+", token) is None:
            return None
        return int(token)

    def command(self, line: str) -> str | None:
        tokens = line.strip().split()
        if not tokens:
            return None
        command = tokens[0].upper()
        self.last_command_ms = self.now_ms

        if command == "CAMERA_HOLD":
            if not self.armed:
                return self._fatal("NOT_ARMED", command)
            if len(tokens) != 3:
                return self._fatal("BAD_ARGS", command)
            direction = tokens[1]
            if direction not in {"left", "right", "up", "down"}:
                return self._fatal("UNSUPPORTED_KEY", command)
            duration = self._integer(tokens[2])
            if duration is None:
                return self._fatal("BAD_ARGS", command)
            if duration <= 0 or duration > _numeric_constant("MAX_CAMERA_HOLD_MS"):
                return self._fatal("ERR_LIMIT", command)
            self.keys_down = 1
            self.now_ms += duration
            self.keys_down = 0
            self.camera_events.append((direction, duration))
            return (
                f"OK CAMERA_HOLD direction={direction} "
                f"requestedDurationMs={duration} appliedDurationMs={duration}"
            )

        if command == "WHEEL":
            if not self.armed:
                return self._fatal("NOT_ARMED", command)
            if len(tokens) != 2:
                return self._fatal("BAD_ARGS", command)
            amount = self._integer(tokens[1])
            if amount is None:
                return self._fatal("BAD_ARGS", command)
            maximum = _numeric_constant("MAX_WHEEL_STEP")
            if amount == 0 or amount < -maximum or amount > maximum:
                return self._fatal("ERR_LIMIT", command)
            self.wheel_events.append(amount)
            return f"OK WHEEL requestedAmount={amount} appliedAmount={amount}"

        return self._fatal("UNKNOWN", command)

    def idle(self, elapsed_ms: int) -> str | None:
        self.now_ms += elapsed_ms
        if self.armed and self.now_ms - self.last_command_ms > _numeric_constant("WATCHDOG_MS"):
            self._stop_all()
            return "OK WATCHDOG_STOP armed=0 released=1"
        return None


class SourceContractTests(unittest.TestCase):
    def test_exact_caps_matches_constants_and_handler_dispatch(self) -> None:
        response = _caps_response()
        self.assertEqual(GOLDEN["caps"], response)
        caps = _fields(response)
        self.assertEqual("input_capabilities.v2", caps["schema"])
        self.assertEqual(_string_constant("PROTOCOL"), caps["protocol"])
        self.assertEqual(_string_constant("VERSION"), caps["firmwareVersion"])
        self.assertEqual(str(_numeric_constant("MAX_MOVE_DELTA")), caps["maxMoveDelta"])
        self.assertEqual(str(_numeric_constant("MAX_HOLD_MS")), caps["maxClickHoldMs"])
        self.assertEqual(str(_numeric_constant("MAX_HOLD_MS")), caps["maxKeyPressMs"])
        self.assertEqual(str(_numeric_constant("MAX_HOLD_MS")), caps["maxHoldKeysMs"])
        self.assertEqual(str(_numeric_constant("MAX_CAMERA_HOLD_MS")), caps["maxCameraHoldMs"])
        self.assertEqual(str(_numeric_constant("MAX_WHEEL_STEP")), caps["maxWheelStep"])
        self.assertEqual(str(_numeric_constant("WATCHDOG_MS")), caps["watchdogMs"])
        self.assertEqual("left,right,up,down", caps["cameraKeys"])
        for required in (
            "pointer",
            "mouse",
            "relativeMove",
            "buttonDownUp",
            "click",
            "keyboard",
            "keyPress",
            "cameraKeyHold",
            "wheel",
            "arm",
            "watchdog",
            "stopAll",
            "disarm",
            "status",
            "resetSafe",
        ):
            self.assertEqual("1", caps[required], required)

        dispatch = _function_body("handleLine")
        self.assertIn('command == "CAMERA_HOLD"', dispatch)
        self.assertIn("handleCameraHold(line, command);", dispatch)
        self.assertIn('command == "WHEEL"', dispatch)
        self.assertIn("handleWheel(line, command);", dispatch)
        self.assertIn("fatalErr(\"UNKNOWN\", command);", dispatch)

    def test_camera_handler_is_atomic_strict_direction_only_and_not_clamped(self) -> None:
        body = _function_body("handleCameraHold")
        self.assertIn("tokenCount(line) != 3", body)
        self.assertIn("cameraKeyCode(direction)", body)
        self.assertNotIn("clampHold", body)
        self.assertIn("requestedDurationMs <= 0", body)
        self.assertIn("requestedDurationMs > (long)MAX_CAMERA_HOLD_MS", body)
        press = body.index("Keyboard.press(code);")
        wait = body.index("delay((unsigned long)requestedDurationMs);")
        release = body.index("Keyboard.release(code);")
        ack = body.index('Serial.print(F("OK CAMERA_HOLD direction="));')
        self.assertLess(press, wait)
        self.assertLess(wait, release)
        self.assertLess(release, ack)

        key_body = _function_body("cameraKeyCode")
        for direction in ("left", "right", "up", "down"):
            self.assertIn(f'direction == "{direction}"', key_body)
        for forbidden in ("enter", "esc", "space"):
            self.assertNotIn(forbidden, key_body)

    def test_wheel_handler_is_signed_strict_bounded_and_exact(self) -> None:
        body = _function_body("handleWheel")
        self.assertIn("tokenCount(line) != 2", body)
        self.assertIn("requestedAmount == 0", body)
        self.assertIn("requestedAmount < -MAX_WHEEL_STEP", body)
        self.assertIn("requestedAmount > MAX_WHEEL_STEP", body)
        self.assertIn("Mouse.move(0, 0, (signed char)requestedAmount);", body)
        self.assertIn('Serial.print(F("OK WHEEL requestedAmount="));', body)
        self.assertIn('Serial.print(F(" appliedAmount="));', body)

    def test_error_cleanup_and_watchdog_relationship_are_source_enforced(self) -> None:
        self.assertIn("stopAll();", _function_body("fatalErr"))
        self.assertIn(
            'static_assert(MAX_CAMERA_HOLD_MS < WATCHDOG_MS, "camera hold must remain below watchdog");',
            SKETCH,
        )
        self.assertLess(_numeric_constant("MAX_CAMERA_HOLD_MS"), _numeric_constant("WATCHDOG_MS"))

    def test_legacy_bounded_commands_and_dispatch_remain_present(self) -> None:
        self.assertEqual(250, _numeric_constant("MAX_HOLD_MS"))
        dispatch = _function_body("handleLine")
        for command in (
            "MOVE",
            "MOUSE_DOWN",
            "MOUSE_UP",
            "CLICK",
            "KEY_DOWN",
            "KEY_UP",
            "KEY_PRESS",
            "HOLD_KEYS",
            "STOP_ALL",
            "DISARM",
            "STATUS",
        ):
            self.assertIn(f'command == "{command}"', dispatch)


class GoldenVectorTests(unittest.TestCase):
    def test_all_protocol_vectors(self) -> None:
        for vector in GOLDEN["vectors"]:
            with self.subTest(vector["name"]):
                model = FirmwareModel(
                    armed=bool(vector.get("armed", False)),
                    keys_down=int(vector.get("keysDown", 0)),
                    mouse_buttons_down=int(vector.get("mouseButtonsDown", 0)),
                )
                response = model.command(vector["command"])
                self.assertEqual(vector["expected"], response)
                self.assertEqual(int(vector.get("delayMs", 0)), model.now_ms)
                expected_wheel = [] if "wheel" not in vector else [int(vector["wheel"])]
                self.assertEqual(expected_wheel, model.wheel_events)
                if vector.get("error"):
                    self.assertFalse(model.armed)
                    self.assertEqual(0, model.keys_down)
                    self.assertEqual(0, model.mouse_buttons_down)
                else:
                    self.assertTrue(model.armed)
                    self.assertEqual(0, model.keys_down)
                    self.assertEqual(0, model.mouse_buttons_down)

    def test_watchdog_boundary_and_release(self) -> None:
        at_boundary = FirmwareModel(armed=True, keys_down=1, mouse_buttons_down=1)
        self.assertIsNone(at_boundary.idle(_numeric_constant("WATCHDOG_MS")))
        self.assertTrue(at_boundary.armed)

        expired = FirmwareModel(armed=True, keys_down=1, mouse_buttons_down=1)
        self.assertEqual(
            "OK WATCHDOG_STOP armed=0 released=1",
            expired.idle(_numeric_constant("WATCHDOG_MS") + 1),
        )
        self.assertFalse(expired.armed)
        self.assertEqual(0, expired.keys_down)
        self.assertEqual(0, expired.mouse_buttons_down)

    def test_max_camera_hold_completes_before_watchdog_and_later_expires(self) -> None:
        model = FirmwareModel(armed=True)
        maximum = _numeric_constant("MAX_CAMERA_HOLD_MS")
        self.assertEqual(
            f"OK CAMERA_HOLD direction=right requestedDurationMs={maximum} appliedDurationMs={maximum}",
            model.command(f"CAMERA_HOLD right {maximum}"),
        )
        self.assertTrue(model.armed)
        self.assertEqual(0, model.keys_down)
        self.assertIsNone(model.idle(_numeric_constant("WATCHDOG_MS") - maximum))
        self.assertTrue(model.armed)
        self.assertEqual(
            "OK WATCHDOG_STOP armed=0 released=1",
            model.idle(1),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

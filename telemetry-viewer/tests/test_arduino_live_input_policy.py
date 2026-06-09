import json
import re
import sys
import tempfile
import unittest
from io import StringIO
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import execute_next_action as execute_cli
import input_control.backend_arduino_hid as arduino_hid_module
import input_control.arduino_monitor as arduino_monitor_module
from input_control.backend_arduino_hid import ArduinoHIDBackend, ArduinoHIDError, check_arduino_monitor_status
from input_control.backend_pyautogui import PyAutoGuiBackend
from input_control.executor import _ensure_live_input_session_for_action, _live_input_status
from input_control.human_input_controller import HumanInputController
from input_control.mouse_movement import MouseMovementPlan, MousePoint, MouseTarget


class FakeSerial:
    def __init__(self, *_args, **_kwargs):
        self.commands = []
        self.closed = False

    def write(self, data):
        self.commands.append(data.decode("utf-8").strip())

    def flush(self):
        pass

    def readline(self):
        command = self.commands[-1]
        if command == "PING":
            return b"OK PONG\n"
        if command == "IDENTIFY":
            return b"OK IDENTIFY name=ArduinoHIDBridge version=1.0.0 board=leonardo protocol=arduino_hid.v1\n"
        if command == "CAPS":
            return b"OK CAPS mouse=1 keyboard=1 relativeMove=1 buttons=left,right,middle keys=basic holdKeys=1 watchdog=1 stopAll=1 resetSafe=1\n"
        if command == "STATUS":
            return b"OK STATUS armed=0 keysDown=0 mouseButtonsDown=0 lastCommandAgeMs=10 watchdogMs=1000\n"
        if command == "STOP_ALL":
            return b"OK STOP_ALL\n"
        if command.startswith("ARM "):
            return b"OK ARMED\n"
        if command == "DISARM":
            return b"OK DISARMED\n"
        if command.startswith("MOVE "):
            return b"OK MOVE\n"
        if command.startswith("KEY_DOWN"):
            return b"OK KEY_DOWN\n"
        if command.startswith("KEY_UP"):
            return b"OK KEY_UP\n"
        if command.startswith("KEY_PRESS"):
            return b"OK KEY_PRESS\n"
        if command.startswith("MOUSE_DOWN"):
            return b"OK MOUSE_DOWN\n"
        if command.startswith("MOUSE_UP"):
            return b"OK MOUSE_UP\n"
        if command.startswith("CLICK"):
            return b"OK CLICK\n"
        return b"ERR UNKNOWN\n"

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def close(self):
        self.closed = True


class FakeArduinoBackend:
    name = "arduino"
    arduino_hid_backend = True
    requires_arming = True
    software_input_backend = False

    def __init__(self):
        self.armed = False
        self.calls = []

    def press(self, key):
        self.calls.append(("press", key))

    def key_down(self, key):
        self.calls.append(("key_down", key))

    def key_up(self, key):
        self.calls.append(("key_up", key))


class FakeCalibrationBackend:
    name = "arduino"
    arduino_hid_backend = True
    requires_arming = True

    def __init__(self):
        self.armed = False
        self.calls = []
        self.traces = []

    def status(self):
        return {"schema": "arduino_hid_backend_status.v1", "armed": self.armed}

    def stop_all(self):
        self.calls.append(("stop_all",))
        self.armed = False
        return self.status()

    def arm(self, _token=None):
        self.calls.append(("arm",))
        self.armed = True
        return self.status()

    def configure_movement_safety(self, **kwargs):
        self.calls.append(("configure_movement_safety", kwargs))
        return kwargs

    def move_to_absolute(self, point, **kwargs):
        self.calls.append(("move_to_absolute", point, kwargs))
        trace = {
            "schema": "arduino_closed_loop_move.v1",
            "status": "PASS",
            "targetScreenPoint": dict(point),
            "cursorPositionAfter": dict(point),
            "positionErrorPx": 0,
            "leftAllowedRegion": False,
            "movementChunks": [{"commandedDelta": {"x": 1, "y": 0}}],
        }
        self.traces.append(trace)
        return trace

    def disarm(self):
        self.calls.append(("disarm",))
        self.armed = False
        return self.status()

    def firmware_status(self):
        return {"armed": False, "keysDown": 0, "mouseButtonsDown": 0, "watchdogMs": 1000}

    def click_at(self, *_args, **_kwargs):
        self.calls.append(("click_at",))

    def press(self, key):
        self.calls.append(("press", key))


class FakeMovementDiagnosticBackend(FakeCalibrationBackend):
    def __init__(self, raw_state):
        super().__init__()
        self.raw_state = raw_state

    def firmware_status(self):
        return {"armed": bool(self.armed), "keysDown": 0, "mouseButtonsDown": 0, "watchdogMs": 1000}

    def diagnostic_move_relative(self, dx, dy):
        self.calls.append(("diagnostic_move_relative", int(dx), int(dy)))
        self.raw_state["mouse_count"] += 1
        self.raw_state["dx_total"] += int(dx)
        self.raw_state["dy_total"] += int(dy)
        return {
            "schema": "arduino_move_command_trace.v1",
            "commandSent": f"MOVE {int(dx)} {int(dy)}",
            "firmwareAck": "OK MOVE",
            "dx": int(dx),
            "dy": int(dy),
            "ackOk": True,
        }


class FakeMovementMonitor:
    def __init__(self, raw_state, **_kwargs):
        self.raw_state = raw_state
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def status(self, **_kwargs):
        return {
            "schema": "input_integrity_status.v1",
            "status": "PASS",
            "monitorAvailable": True,
            "monitorPass": True,
            "arduinoDetected": {"rawInputDevicePresent": True, "mousePresent": True, "vidPidMatched": True},
            "arduinoActivity": {
                "mouseEventCount": self.raw_state["mouse_count"],
                "rawInputMouseCount": self.raw_state["mouse_count"],
                "rawInputKeyboardCount": 0,
                "rawInputMouseDxTotal": self.raw_state["dx_total"],
                "rawInputMouseDyTotal": self.raw_state["dy_total"],
            },
            "injectionFlags": {
                "mouseInjectedCount": 0,
                "mouseLowerIlInjectedCount": 0,
                "keyboardInjectedCount": 0,
                "keyboardLowerIlInjectedCount": 0,
            },
            "backend": {"directBackendBypassCount": 0},
        }

    def write_status(self, **kwargs):
        return self.status(**kwargs)


def live_options(**overrides):
    values = {
        "execute": True,
        "hover_only": False,
        "camera_self_test": False,
        "allow_software_input": False,
        "unsafe_allow_pyautogui_live": False,
        "unsafe_allow_software_live": False,
        "arduino_require_monitor": False,
        "arduino_monitor_status_path": None,
        "arduino_monitor_max_age_ms": 3000,
        "arduino_vid": "VID_2341",
        "arduino_pid": "PID_8036",
    }
    values.update(overrides)
    return Namespace(**values)


def pointer_calibration_record(*, allowed_window="runelite"):
    return {
        "schema": execute_cli.POINTER_CALIBRATION_RECORD_SCHEMA,
        "status": "PASS",
        "writtenAtUtc": execute_cli._utc_now_text(),
        "writtenAtMillis": execute_cli.time.time() * 1000,
        "arduinoPort": "COM9",
        "allowedWindow": allowed_window,
        "movementMetrics": {"movementSuccessRate": 1.0},
        "totalChunks": 5,
        "successfulChunks": 5,
        "retryChunks": 0,
        "noEffectChunks": 0,
        "consecutiveNoEffectChunks": 0,
        "movementSuccessRate": 1.0,
        "maxPositionErrorPx": 1,
        "finalPositionErrorPx": 0,
        "clickSent": False,
        "keySent": False,
        "directBackendBypassCount": 0,
        "cursorLeftAllowedRegion": False,
        "firmwareStatusAfter": {"armed": False, "keysDown": 0, "mouseButtonsDown": 0, "watchdogMs": 1000},
        "monitorAfter": {
            "schema": "input_integrity_status.v1",
            "status": "PASS",
            "monitorPass": True,
            "injectionFlags": {
                "mouseInjectedCount": 0,
                "mouseLowerIlInjectedCount": 0,
                "keyboardInjectedCount": 0,
                "keyboardLowerIlInjectedCount": 0,
            },
            "backend": {"directBackendBypassCount": 0},
        },
        "calibrationPayload": {
            "allowedWindow": allowed_window,
            "allowedRegion": {"x": 100, "y": 120, "width": 640, "height": 480},
            "runeliteWindow": {"title": "RuneLite - Test", "bounds": {"x": 90, "y": 100, "width": 700, "height": 540}},
        },
    }


class ArduinoLiveInputPolicyTest(unittest.TestCase):
    def test_cli_live_execution_defaults_to_arduino(self):
        args = execute_cli.parse_args(["--execute"])

        execute_cli.apply_focus_default(args)

        self.assertEqual(args.backend, "arduino")
        self.assertTrue(args.live_input_backend_required)

    def test_no_move_self_test_defaults_to_arduino(self):
        args = execute_cli.parse_args(["--input-integrity-self-test-no-move"])

        execute_cli.apply_focus_default(args)

        self.assertTrue(args.input_integrity_self_test)
        self.assertEqual(args.backend, "arduino")
        self.assertTrue(args.live_input_backend_required)

    def test_pyautogui_live_execution_is_blocked_without_override(self):
        status = _live_input_status(live_options(), PyAutoGuiBackend())

        self.assertEqual(status["status"], "FAIL")
        self.assertEqual(status["blockReason"], "software_input_blocked")

    def test_pyautogui_live_execution_can_be_explicitly_overridden(self):
        status = _live_input_status(live_options(allow_software_input=True), PyAutoGuiBackend())

        self.assertEqual(status["status"], "PASS")
        self.assertTrue(status["softwareInputAllowed"])

    def test_arduino_live_execute_blocked_until_pointer_calibrated(self):
        original_stdout = sys.stdout
        capture = StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = str(Path(tmp) / "missing_pointer_calibration.json")
            try:
                sys.stdout = capture
                rc = execute_cli.main(["--execute", "--backend", "arduino", "--json", "--arduino-pointer-calibration-path", missing_path])
            finally:
                sys.stdout = original_stdout

        payload = json.loads(capture.getvalue())
        self.assertEqual(rc, 1)
        self.assertEqual(payload["reason"], "arduino_pointer_calibration_required")
        self.assertTrue(payload["liveRuneLiteClicksBlocked"])
        self.assertFalse(payload["clickSent"])
        self.assertIn("calibration_record_missing", payload["pointerCalibration"]["blockers"])

    def test_arduino_live_movement_safety_uses_pointer_calibration_region(self):
        with tempfile.TemporaryDirectory() as tmp:
            calibration_path = Path(tmp) / "pointer_calibration.json"
            calibration_path.write_text(json.dumps(pointer_calibration_record()), encoding="utf-8")
            args = execute_cli.parse_args([
                "--execute",
                "--backend",
                "arduino",
                "--arduino-port",
                "COM9",
                "--arduino-pointer-calibration-path",
                str(calibration_path),
            ])
            backend = FakeCalibrationBackend()

            calibration = execute_cli._load_pointer_calibration_for_live_movement(args)
            configured = execute_cli._configure_live_arduino_movement_safety(args, backend, calibration)

        self.assertEqual(calibration["status"], "PASS")
        self.assertEqual(configured["status"], "PASS")
        configure_calls = [call for call in backend.calls if call[0] == "configure_movement_safety"]
        self.assertEqual(len(configure_calls), 1)
        kwargs = configure_calls[0][1]
        self.assertEqual(kwargs["allowed_region"], {"x": 100, "y": 120, "width": 640, "height": 480})
        self.assertIn("RuneLite - Test", kwargs["allowed_foreground_titles"])
        self.assertTrue(kwargs["enabled"])

    def test_arduino_live_movement_safety_scales_region_to_current_runelite_window(self):
        record = pointer_calibration_record()

        with patch.object(
            execute_cli,
            "_window_info_matching",
            return_value={"title": "RuneLite - Test", "bounds": {"x": 180, "y": 200, "width": 1400, "height": 1080}},
        ):
            safety = execute_cli._live_movement_safety_from_calibration(record)

        self.assertEqual(safety["status"], "PASS")
        self.assertEqual(safety["sourceAllowedRegion"], {"x": 100, "y": 120, "width": 640, "height": 480})
        self.assertEqual(safety["allowedRegion"], {"x": 200, "y": 240, "width": 1280, "height": 960})
        self.assertTrue(safety["coordinateTransform"]["scaled"])
        self.assertIn("calibration_allowed_region_transformed_to_current_runelite_window", safety["warnings"])

    def test_arduino_live_movement_safety_rejects_non_runelite_calibration(self):
        with tempfile.TemporaryDirectory() as tmp:
            calibration_path = Path(tmp) / "pointer_calibration.json"
            calibration_path.write_text(json.dumps(pointer_calibration_record(allowed_window="calibration")), encoding="utf-8")
            args = execute_cli.parse_args([
                "--execute",
                "--backend",
                "arduino",
                "--arduino-port",
                "COM9",
                "--arduino-pointer-calibration-path",
                str(calibration_path),
            ])
            backend = FakeCalibrationBackend()

            calibration = execute_cli._load_pointer_calibration_for_live_movement(args)
            configured = execute_cli._configure_live_arduino_movement_safety(args, backend, calibration)

        self.assertEqual(calibration["status"], "PASS")
        self.assertEqual(configured["status"], "FAIL")
        self.assertIn("calibration_allowed_window_not_runelite", configured["blockers"])
        self.assertFalse(any(call[0] == "configure_movement_safety" for call in backend.calls))

    def test_arduino_stop_all_cli_sends_stop_all(self):
        serials = []

        def factory(*args, **kwargs):
            serial = FakeSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        args = execute_cli.parse_args(["--arduino-stop-all", "--arduino-port", "COM9"])
        execute_cli.apply_focus_default(args)
        backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=lambda _seconds: None)

        payload = execute_cli.run_arduino_check(args, backend)

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(args.arduino_check, "stop-all")
        self.assertIn("STOP_ALL", serials[0].commands)

    def test_arduino_port_health_cli_reports_protocol_status_under_lock(self):
        serials = []
        serial_kwargs = []

        def factory(*args, **kwargs):
            serial = FakeSerial(*args, **kwargs)
            serials.append(serial)
            serial_kwargs.append(kwargs)
            return serial

        args = execute_cli.parse_args(["--arduino-port-health", "--arduino-port", "COM9"])
        execute_cli.apply_focus_default(args)
        with tempfile.TemporaryDirectory() as tmp:
            backend = ArduinoHIDBackend(
                port="COM9",
                serial_factory=factory,
                sleep_func=lambda _seconds: None,
                serial_lock_enabled=True,
                serial_lock_dir=tmp,
            )

            payload = execute_cli.run_arduino_check(args, backend)
            backend.close()

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(args.arduino_check, "port-health")
        self.assertEqual(payload["result"]["portHealth"], "PASS")
        self.assertEqual(payload["result"]["identify"]["protocol"], "arduino_hid.v1")
        self.assertFalse(serial_kwargs[0]["rtscts"])
        self.assertFalse(serial_kwargs[0]["dsrdtr"])
        self.assertGreaterEqual(serial_kwargs[0]["write_timeout"], 2.0)
        self.assertIn("PING", serials[0].commands)
        self.assertIn("IDENTIFY", serials[0].commands)
        self.assertIn("CAPS", serials[0].commands)
        self.assertIn("STATUS", serials[0].commands)

    def test_arduino_serial_session_lock_blocks_concurrent_port_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = ArduinoHIDBackend(
                port="COM9",
                serial_factory=FakeSerial,
                sleep_func=lambda _seconds: None,
                serial_lock_enabled=True,
                serial_lock_dir=tmp,
                serial_lock_timeout_ms=0,
                serial_owner="first",
            )
            second = ArduinoHIDBackend(
                port="COM9",
                serial_factory=FakeSerial,
                sleep_func=lambda _seconds: None,
                serial_lock_enabled=True,
                serial_lock_dir=tmp,
                serial_lock_timeout_ms=0,
                serial_owner="second",
            )
            first.connect()
            try:
                with self.assertRaises(ArduinoHIDError):
                    second.connect()
                self.assertTrue(second.status()["serialLock"]["concurrentAccessDetected"])
            finally:
                first.close()
                second.close()

    def test_arduino_usb_diagnostics_does_not_connect_or_reset(self):
        args = execute_cli.parse_args(["--arduino-usb-diagnostics", "--arduino-port", "COM6", "--arduino-bootloader-port", "COM4"])
        execute_cli.apply_focus_default(args)
        backend = ArduinoHIDBackend(port="COM6", serial_factory=FakeSerial, sleep_func=lambda _seconds: None)

        original_powershell = execute_cli._powershell_json
        original_run = execute_cli.subprocess.run
        try:
            execute_cli._powershell_json = lambda *_args, **_kwargs: []

            def fake_run(*_args, **_kwargs):
                class Completed:
                    returncode = 1
                    stdout = ""
                    stderr = ""

                return Completed()

            execute_cli.subprocess.run = fake_run
            payload = execute_cli.run_arduino_check(args, backend)
        finally:
            execute_cli._powershell_json = original_powershell
            execute_cli.subprocess.run = original_run

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["result"]["normalComPort"], "COM6")
        self.assertEqual(payload["result"]["bootloaderComPort"], "COM4")
        self.assertFalse(payload["result"]["resetOrUploadPerformed"])
        self.assertFalse(backend.status()["connected"])

    def test_pointer_calibration_sends_no_click_or_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            calibration_path = Path(tmp) / "pointer_calibration.json"
            args = execute_cli.parse_args([
                "--arduino-pointer-calibration-test",
                "--allowed-window",
                "calibration",
                "--arduino-port",
                "COM9",
                "--arduino-pointer-calibration-path",
                str(calibration_path),
            ])
            execute_cli.apply_focus_default(args)
            backend = FakeCalibrationBackend()
            original_context = execute_cli._calibration_window_context
            original_cursor = execute_cli._cursor_position
            original_foreground = execute_cli._foreground_window_info
            try:
                execute_cli._calibration_window_context = lambda _args: (
                    None,
                    {"type": "calibration", "window": {"title": "Arduino Cursor Calibration"}, "fallbackCalibrationWindow": True},
                    {"x": 10, "y": 10, "width": 100, "height": 80},
                    ["Arduino Cursor Calibration"],
                )
                execute_cli._cursor_position = lambda: {"x": 20, "y": 20}
                execute_cli._foreground_window_info = lambda: {"available": True, "title": "Arduino Cursor Calibration", "pid": 123}

                payload = execute_cli.run_arduino_pointer_calibration_test(args, backend)
                calibration_exists = calibration_path.exists()
                loaded = execute_cli._load_pointer_calibration_for_live_movement(args)
            finally:
                execute_cli._calibration_window_context = original_context
                execute_cli._cursor_position = original_cursor
                execute_cli._foreground_window_info = original_foreground

        self.assertEqual(payload["status"], "PASS")
        self.assertFalse(payload["clickSent"])
        self.assertFalse(payload["keySent"])
        self.assertEqual(payload["directBackendBypassCount"], 0)
        self.assertEqual(len(payload["movementTraces"]), 5)
        self.assertTrue(payload["calibrationPersisted"])
        self.assertTrue(calibration_exists)
        self.assertEqual(loaded["status"], "PASS")
        self.assertIn(("stop_all",), backend.calls)
        self.assertIn(("disarm",), backend.calls)
        self.assertNotIn(("click_at",), backend.calls)
        self.assertFalse(any(call[0] == "press" for call in backend.calls))

    def test_pointer_calibration_focuses_runelite_for_runelite_allowed_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            calibration_path = Path(tmp) / "pointer_calibration.json"
            args = execute_cli.parse_args([
                "--arduino-pointer-calibration-test",
                "--allowed-window",
                "runelite",
                "--arduino-port",
                "COM9",
                "--arduino-pointer-calibration-path",
                str(calibration_path),
            ])
            execute_cli.apply_focus_default(args)
            backend = FakeCalibrationBackend()
            original_context = execute_cli._calibration_window_context
            original_cursor = execute_cli._cursor_position
            original_foreground = execute_cli._foreground_window_info
            original_focus = execute_cli._restore_post_test_focus
            focus_calls = []
            try:
                execute_cli._calibration_window_context = lambda _args: (
                    None,
                    {
                        "type": "runelite",
                        "window": {"title": "RuneLite - Test", "bounds": {"x": 0, "y": 0, "width": 300, "height": 220}},
                        "fallbackCalibrationWindow": False,
                    },
                    {"x": 10, "y": 10, "width": 100, "height": 80},
                    ["RuneLite"],
                )
                execute_cli._cursor_position = lambda: {"x": 20, "y": 20}
                execute_cli._foreground_window_info = lambda: {"available": True, "title": "RuneLite - Test", "pid": 123}

                def fake_focus(target, *, window_title_filter="RuneLite"):
                    focus_calls.append((target, window_title_filter))
                    return {"schema": "post_test_focus_recovery.v1", "target": target, "status": "PASS", "reason": "foreground_restored"}

                execute_cli._restore_post_test_focus = fake_focus
                payload = execute_cli.run_arduino_pointer_calibration_test(args, backend)
            finally:
                execute_cli._calibration_window_context = original_context
                execute_cli._cursor_position = original_cursor
                execute_cli._foreground_window_info = original_foreground
                execute_cli._restore_post_test_focus = original_focus

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(focus_calls, [("runelite", "RuneLite")])
        self.assertEqual(payload["preCalibrationFocus"]["status"], "PASS")
        self.assertFalse(payload["fallbackCalibrationWindow"])

    def test_pointer_calibration_stages_when_cursor_near_allowed_region(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = execute_cli.parse_args(
                [
                    "--arduino-pointer-calibration-test",
                    "--allowed-window",
                    "calibration",
                    "--arduino-port",
                    "COM9",
                    "--calibration-staging-max-distance-px",
                    "150",
                    "--arduino-pointer-calibration-path",
                    str(Path(tmp) / "pointer_calibration.json"),
                ]
            )
            execute_cli.apply_focus_default(args)
            backend = FakeCalibrationBackend()
            original_context = execute_cli._calibration_window_context
            original_cursor = execute_cli._cursor_position
            original_foreground = execute_cli._foreground_window_info
            try:
                execute_cli._calibration_window_context = lambda _args: (
                    None,
                    {"type": "calibration", "window": {"title": "Arduino Cursor Calibration"}, "fallbackCalibrationWindow": True},
                    {"x": 100, "y": 100, "width": 100, "height": 80},
                    ["Arduino Cursor Calibration"],
                )
                execute_cli._cursor_position = lambda: {"x": 40, "y": 120}
                execute_cli._foreground_window_info = lambda: {"available": True, "title": "Arduino Cursor Calibration", "pid": 123}

                payload = execute_cli.run_arduino_pointer_calibration_test(args, backend)
            finally:
                execute_cli._calibration_window_context = original_context
                execute_cli._cursor_position = original_cursor
                execute_cli._foreground_window_info = original_foreground

        self.assertEqual(payload["status"], "PASS")
        self.assertTrue(payload["stagingUsed"])
        self.assertTrue(payload["stagingMoveAllowed"])
        self.assertEqual(payload["nearestSafePointInsideAllowedRegion"], {"x": 102, "y": 120})
        self.assertFalse(payload["clickSent"])
        self.assertFalse(payload["keySent"])
        move_calls = [call for call in backend.calls if call[0] == "move_to_absolute"]
        self.assertEqual(len(move_calls), 6)
        self.assertEqual(move_calls[0][1], {"x": 102, "y": 120})
        self.assertEqual(move_calls[0][2]["allowed_region"], payload["expandedStagingRegion"])

    def test_pointer_calibration_far_cursor_requires_manual_placement(self):
        args = execute_cli.parse_args(
            [
                "--arduino-pointer-calibration-test",
                "--allowed-window",
                "calibration",
                "--arduino-port",
                "COM9",
                "--calibration-staging-max-distance-px",
                "50",
            ]
        )
        execute_cli.apply_focus_default(args)
        backend = FakeCalibrationBackend()
        original_context = execute_cli._calibration_window_context
        original_cursor = execute_cli._cursor_position
        original_foreground = execute_cli._foreground_window_info
        try:
            execute_cli._calibration_window_context = lambda _args: (
                None,
                {"type": "calibration", "window": {"title": "Arduino Cursor Calibration"}, "fallbackCalibrationWindow": True},
                {"x": 200, "y": 200, "width": 100, "height": 80},
                ["Arduino Cursor Calibration"],
            )
            execute_cli._cursor_position = lambda: {"x": 0, "y": 0}
            execute_cli._foreground_window_info = lambda: {"available": True, "title": "Arduino Cursor Calibration", "pid": 123}

            payload = execute_cli.run_arduino_pointer_calibration_test(args, backend)
        finally:
            execute_cli._calibration_window_context = original_context
            execute_cli._cursor_position = original_cursor
            execute_cli._foreground_window_info = original_foreground

        self.assertEqual(payload["status"], "FAIL")
        self.assertFalse(payload["stagingMoveAllowed"])
        self.assertEqual(payload["stagingAbortReason"], "cursor_outside_expanded_staging_region")
        self.assertFalse(payload["clickSent"])
        self.assertFalse(payload["keySent"])
        self.assertIn(("stop_all",), backend.calls)
        self.assertIn(("disarm",), backend.calls)
        self.assertFalse(any(call[0] == "move_to_absolute" for call in backend.calls))

    def test_calibration_window_defaults_away_from_screen_edge(self):
        args = execute_cli.parse_args(["--arduino-pointer-calibration-test", "--allowed-window", "calibration"])

        geometry = execute_cli._calibration_window_geometry(
            args,
            {"x": 0, "y": 972},
            {"width": 1920, "height": 1080},
        )

        self.assertGreater(geometry["x"], 200)
        self.assertGreater(geometry["y"], 200)
        self.assertLess(geometry["x"] + geometry["width"], 1720)
        self.assertLess(geometry["y"] + geometry["height"], 900)

    def test_movement_diagnostics_classifies_rawinput_without_cursor_move(self):
        raw_state = {"mouse_count": 0, "dx_total": 0, "dy_total": 0}
        args = execute_cli.parse_args(["--arduino-movement-diagnostics", "--arduino-port", "COM9"])
        execute_cli.apply_focus_default(args)
        backend = FakeMovementDiagnosticBackend(raw_state)
        original_monitor = arduino_monitor_module.InputIntegrityMonitor
        original_cursor = execute_cli._cursor_position
        original_observations = execute_cli._vmware_mouse_observations
        original_write_backend = execute_cli._write_selftest_backend_status
        try:
            arduino_monitor_module.InputIntegrityMonitor = lambda **kwargs: FakeMovementMonitor(raw_state, **kwargs)
            execute_cli._cursor_position = lambda: {"x": 100, "y": 100}
            execute_cli._vmware_mouse_observations = lambda _args: {"schema": "vmware_mouse_observations.v1"}
            execute_cli._write_selftest_backend_status = lambda *_args, **_kwargs: None

            payload = execute_cli.run_arduino_movement_diagnostics(args, backend)
        finally:
            arduino_monitor_module.InputIntegrityMonitor = original_monitor
            execute_cli._cursor_position = original_cursor
            execute_cli._vmware_mouse_observations = original_observations
            execute_cli._write_selftest_backend_status = original_write_backend

        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(payload["classification"], "serial_ok_rawinput_ok_cursor_no_move")
        self.assertEqual(payload["reliabilityClassification"], "rawinput_ok_cursor_no_move")
        self.assertIn("vmware_mouse_integration_blocking_possible", payload["possibleCauses"])
        self.assertIn("vmware_integration_possible", payload["possibleCauses"])
        self.assertEqual(len(payload["movementSteps"]), 12)
        self.assertEqual(payload["movementSteps"][0]["commandTrace"]["commandSent"], "MOVE 5 0")
        self.assertEqual(payload["movementSteps"][0]["inputIntegrityDelta"]["rawInputMouseCountDelta"], 1)
        self.assertEqual(payload["movementSteps"][0]["windowsCursorDelta"], {"dx": 0, "dy": 0})
        self.assertGreaterEqual(payload["movementSteps"][0]["pollCount"], 1)
        self.assertFalse(payload["clickSent"])
        self.assertFalse(payload["keySent"])
        self.assertEqual(payload["directBackendBypassCount"], 0)
        self.assertIn(("stop_all",), backend.calls)
        self.assertIn(("disarm",), backend.calls)

    def test_movement_diagnostics_passes_when_cursor_moves_but_rawinput_is_coalesced(self):
        raw_state = {"mouse_count": 0, "dx_total": 0, "dy_total": 0}
        cursor = {"x": 100, "y": 100}

        class CursorOnlyBackend(FakeMovementDiagnosticBackend):
            def diagnostic_move_relative(self, dx, dy):
                self.calls.append(("diagnostic_move_relative", int(dx), int(dy)))
                cursor["x"] += int(dx)
                cursor["y"] += int(dy)
                return {
                    "schema": "arduino_move_command_trace.v1",
                    "commandSent": f"MOVE {int(dx)} {int(dy)}",
                    "firmwareAck": "OK MOVE",
                    "dx": int(dx),
                    "dy": int(dy),
                    "ackOk": True,
                }

        args = execute_cli.parse_args(["--arduino-movement-diagnostics", "--arduino-port", "COM9"])
        execute_cli.apply_focus_default(args)
        backend = CursorOnlyBackend(raw_state)
        original_monitor = arduino_monitor_module.InputIntegrityMonitor
        original_cursor = execute_cli._cursor_position
        original_observations = execute_cli._vmware_mouse_observations
        original_write_backend = execute_cli._write_selftest_backend_status
        try:
            arduino_monitor_module.InputIntegrityMonitor = lambda **kwargs: FakeMovementMonitor(raw_state, **kwargs)
            execute_cli._cursor_position = lambda: dict(cursor)
            execute_cli._vmware_mouse_observations = lambda _args: {"schema": "vmware_mouse_observations.v1"}
            execute_cli._write_selftest_backend_status = lambda *_args, **_kwargs: None

            payload = execute_cli.run_arduino_movement_diagnostics(args, backend)
        finally:
            arduino_monitor_module.InputIntegrityMonitor = original_monitor
            execute_cli._cursor_position = original_cursor
            execute_cli._vmware_mouse_observations = original_observations
            execute_cli._write_selftest_backend_status = original_write_backend

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["classification"], "serial_ok_rawinput_ok_cursor_ok")
        self.assertEqual(payload["reliabilityClassification"], "reliable")
        self.assertIn("rawinput_counter_coalesced_possible", payload["possibleCauses"])
        self.assertEqual(payload["movementSteps"][0]["classification"], "serial_ok_cursor_ok_rawinput_missing")
        self.assertFalse(payload["clickSent"])
        self.assertFalse(payload["keySent"])

    def test_dry_run_can_keep_software_backend_for_planning(self):
        status = _live_input_status(live_options(execute=False), PyAutoGuiBackend())

        self.assertEqual(status["status"], "PASS")
        self.assertFalse(status["liveInputBackendRequired"])

    def test_human_input_controller_requires_armed_arduino_for_live_keys(self):
        backend = FakeArduinoBackend()
        controller = HumanInputController(backend, live_input_backend_required=True)

        with self.assertRaisesRegex(RuntimeError, "arduino_unarmed"):
            controller.press_key("1")

        backend.armed = True
        controller.press_key("1")

        self.assertEqual(backend.calls, [("press", "1")])
        metrics = controller.metrics()
        self.assertEqual(metrics["liveInputBackend"], "arduino")
        self.assertTrue(metrics["liveInputBackendRequired"])
        self.assertEqual(metrics["backendBlockedCommandCount"], 1)

    def test_arduino_backend_fails_closed_when_unarmed(self):
        backend = ArduinoHIDBackend(port="COM9", serial_factory=FakeSerial, sleep_func=lambda _seconds: None)

        with self.assertRaises(ArduinoHIDError):
            backend.move_relative(1, 0)

    def test_arduino_status_reports_numeric_counters(self):
        backend = ArduinoHIDBackend(port="COM9", serial_factory=FakeSerial, sleep_func=lambda _seconds: None)

        status = backend.firmware_status()

        self.assertFalse(status["armed"])
        self.assertEqual(status["keysDown"], 0)
        self.assertEqual(status["mouseButtonsDown"], 0)
        self.assertEqual(status["lastCommandAgeMs"], 10)
        self.assertEqual(status["watchdogMs"], 1000)

    def test_arduino_write_timeout_during_click_is_classified(self):
        class ClickTimeoutSerial(FakeSerial):
            def write(self, data):
                command = data.decode("utf-8").strip()
                self.commands.append(command)
                if command.startswith("CLICK"):
                    raise TimeoutError("serial write timeout")

        backend = ArduinoHIDBackend(port="COM9", serial_factory=ClickTimeoutSerial, sleep_func=lambda _seconds: None)

        backend.arm("session")
        with self.assertRaises(ArduinoHIDError):
            backend.click_at(*backend.current_position(), hold_ms=80)

        trace = backend.status()["lastCommandTrace"]
        self.assertEqual(trace["commandName"], "CLICK")
        self.assertEqual(trace["timeoutClassification"], "serial_timeout_during_click")
        self.assertEqual(trace["status"], "WRITE_FAIL")
        self.assertFalse(backend.armed)

    def test_arduino_backend_arms_sends_hid_commands_and_disarms(self):
        serials = []

        def factory(*args, **kwargs):
            serial = FakeSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=lambda _seconds: None)

        backend.arm("session")
        backend.move_relative(2, 0, duration_ms=20)
        backend.key_down("right")
        backend.key_up("right")
        backend.disarm()

        commands = serials[0].commands
        self.assertIn("PING", commands)
        self.assertIn("IDENTIFY", commands)
        self.assertIn("CAPS", commands)
        self.assertIn("STATUS", commands)
        self.assertIn("ARM session", commands)
        self.assertIn("MOVE 2 0", commands)
        self.assertIn("KEY_DOWN right", commands)
        self.assertIn("KEY_UP right", commands)
        self.assertEqual(commands[-1], "DISARM")
        self.assertFalse(backend.armed)

    def test_arduino_move_relative_chunks_to_firmware_limit(self):
        serials = []

        def factory(*args, **kwargs):
            serial = FakeSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        sleeps = []
        backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=sleeps.append)

        backend.arm("session")
        backend.move_relative(45, -45, duration_ms=60)

        move_commands = [command for command in serials[0].commands if command.startswith("MOVE ")]
        self.assertEqual(move_commands, ["MOVE 15 -15", "MOVE 15 -15", "MOVE 15 -15"])
        self.assertEqual([round(value, 3) for value in sleeps], [0.02, 0.02, 0.02])
        for command in move_commands:
            _name, dx, dy = command.split()
            self.assertLessEqual(abs(int(dx)), 20)
            self.assertLessEqual(abs(int(dy)), 20)

    def test_arduino_plan_move_uses_actual_cursor_between_waypoints(self):
        cursor = [0, 0]

        class HalfMovementSerial(FakeSerial):
            def write(self, data):
                command = data.decode("utf-8").strip()
                if command.startswith("MOVE "):
                    _name, dx, dy = command.split()
                    cursor[0] += int(round(int(dx) * 0.5))
                    cursor[1] += int(round(int(dy) * 0.5))
                super().write(data)

        serials = []

        def factory(*args, **kwargs):
            serial = HalfMovementSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        original_cursor = arduino_hid_module._cursor_position
        try:
            arduino_hid_module._cursor_position = lambda: (cursor[0], cursor[1])
            backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=lambda _seconds: None)
            plan = MouseMovementPlan(
                schema="mouse_movement_plan.v1",
                profile_name="test",
                start=MousePoint(0, 0, 0),
                target=MouseTarget(20, 0),
                duration_ms=20,
                points=[MousePoint(10, 0, 10), MousePoint(20, 0, 20)],
                click_point=MousePoint(20, 0, 20),
                path_length_px=20,
                estimated_difficulty=1.0,
            )

            backend.arm("session")
            backend.move(plan)
        finally:
            arduino_hid_module._cursor_position = original_cursor

        move_commands = [command for command in serials[0].commands if command.startswith("MOVE ")]
        self.assertGreaterEqual(len(move_commands), 2)
        self.assertEqual(move_commands[:2], ["MOVE 10 0", "MOVE 15 0"])

    def test_arduino_move_corrects_endpoint_drift(self):
        cursor = [0, 0]

        class DriftSerial(FakeSerial):
            def write(self, data):
                command = data.decode("utf-8").strip()
                if command.startswith("MOVE "):
                    _name, dx, dy = command.split()
                    cursor[0] += int(round(int(dx) * 0.8))
                    cursor[1] += int(round(int(dy) * 0.8))
                super().write(data)

        serials = []

        def factory(*args, **kwargs):
            serial = DriftSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        original_cursor = arduino_hid_module._cursor_position
        try:
            arduino_hid_module._cursor_position = lambda: (cursor[0], cursor[1])
            backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=lambda _seconds: None)
            plan = MouseMovementPlan(
                schema="mouse_movement_plan.v1",
                profile_name="test",
                start=MousePoint(0, 0, 0),
                target=MouseTarget(100, 0),
                duration_ms=100,
                points=[MousePoint(100, 0, 100)],
                click_point=MousePoint(100, 0, 100),
                path_length_px=100,
                estimated_difficulty=1.0,
            )

            backend.arm("session")
            backend.move(plan)
        finally:
            arduino_hid_module._cursor_position = original_cursor

        move_commands = [command for command in serials[0].commands if command.startswith("MOVE ")]
        self.assertGreater(len(move_commands), 5)
        self.assertLessEqual(abs(cursor[0] - 100), 1)
        self.assertLessEqual(abs(cursor[1]), 1)

    def test_arduino_absolute_move_uses_closed_loop_chunks(self):
        cursor = [50, 50]

        class TrackingSerial(FakeSerial):
            def write(self, data):
                command = data.decode("utf-8").strip()
                if command.startswith("MOVE "):
                    _name, dx, dy = command.split()
                    cursor[0] += int(dx)
                    cursor[1] += int(dy)
                super().write(data)

        serials = []

        def factory(*args, **kwargs):
            serial = TrackingSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        original_cursor = arduino_hid_module._cursor_position
        original_foreground = arduino_hid_module._foreground_window_info
        try:
            arduino_hid_module._cursor_position = lambda: (cursor[0], cursor[1])
            arduino_hid_module._foreground_window_info = lambda: {"available": True, "title": "RuneLite", "pid": 123}
            backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=lambda _seconds: None)
            backend.arm("session")

            trace = backend.move_to_absolute(
                {"x": 86, "y": 74},
                allowed_region={"x": 40, "y": 40, "width": 80, "height": 60},
                allowed_foreground_titles=["RuneLite"],
                max_chunk_px=12,
                tolerance_px=0,
                feedback_tolerance_px=0,
            )
        finally:
            arduino_hid_module._cursor_position = original_cursor
            arduino_hid_module._foreground_window_info = original_foreground

        self.assertEqual(trace["status"], "PASS")
        self.assertEqual(trace["cursorPositionAfter"], {"x": 86, "y": 74})
        self.assertEqual(trace["positionErrorPx"], 0)
        self.assertFalse(trace["leftAllowedRegion"])
        move_commands = [command for command in serials[0].commands if command.startswith("MOVE ")]
        self.assertGreater(len(move_commands), 1)
        self.assertEqual(trace["movementChunks"][0]["commandSent"], move_commands[0])
        self.assertEqual(trace["movementChunks"][0]["firmwareAck"], "OK MOVE")
        for command in move_commands:
            _name, dx, dy = command.split()
            self.assertLessEqual(abs(int(dx)), 12)
            self.assertLessEqual(abs(int(dy)), 12)

    def test_arduino_absolute_move_accepts_delayed_cursor_feedback(self):
        cursor = [50, 50]
        pending = [0, 0]

        class DelayedSerial(FakeSerial):
            def write(self, data):
                command = data.decode("utf-8").strip()
                if command.startswith("MOVE "):
                    _name, dx, dy = command.split()
                    pending[0] += int(dx)
                    pending[1] += int(dy)
                super().write(data)

        def sleep(_seconds):
            if pending[0] or pending[1]:
                cursor[0] += pending[0]
                cursor[1] += pending[1]
                pending[0] = 0
                pending[1] = 0

        original_cursor = arduino_hid_module._cursor_position
        original_foreground = arduino_hid_module._foreground_window_info
        try:
            arduino_hid_module._cursor_position = lambda: (cursor[0], cursor[1])
            arduino_hid_module._foreground_window_info = lambda: {"available": True, "title": "RuneLite", "pid": 123}
            backend = ArduinoHIDBackend(port="COM9", serial_factory=DelayedSerial, sleep_func=sleep)
            backend.arm("session")

            trace = backend.move_to_absolute(
                {"x": 62, "y": 50},
                allowed_region={"x": 40, "y": 40, "width": 80, "height": 60},
                allowed_foreground_titles=["RuneLite"],
                max_chunk_px=12,
                tolerance_px=0,
                feedback_tolerance_px=0,
                move_settle_ms=0,
                move_poll_ms=10,
                move_noeffect_timeout_ms=30,
            )
        finally:
            arduino_hid_module._cursor_position = original_cursor
            arduino_hid_module._foreground_window_info = original_foreground

        self.assertEqual(trace["status"], "PASS")
        self.assertEqual(trace["positionErrorPx"], 0)
        self.assertEqual(trace["movementChunks"][0]["classification"], "move_chunk_delayed_success")
        self.assertGreaterEqual(trace["movementChunks"][0]["pollCount"], 2)

    def test_arduino_absolute_move_retries_one_no_effect_chunk(self):
        cursor = [50, 50]
        move_count = {"value": 0}
        pending = [0, 0]

        class IntermittentSerial(FakeSerial):
            def write(self, data):
                command = data.decode("utf-8").strip()
                if command.startswith("MOVE "):
                    move_count["value"] += 1
                    if move_count["value"] > 1:
                        _name, dx, dy = command.split()
                        pending[0] += int(dx)
                        pending[1] += int(dy)
                super().write(data)

        def sleep(_seconds):
            if pending[0] or pending[1]:
                cursor[0] += pending[0]
                cursor[1] += pending[1]
                pending[0] = 0
                pending[1] = 0

        original_cursor = arduino_hid_module._cursor_position
        original_foreground = arduino_hid_module._foreground_window_info
        try:
            arduino_hid_module._cursor_position = lambda: (cursor[0], cursor[1])
            arduino_hid_module._foreground_window_info = lambda: {"available": True, "title": "RuneLite", "pid": 123}
            backend = ArduinoHIDBackend(port="COM9", serial_factory=IntermittentSerial, sleep_func=sleep)
            backend.arm("session")

            trace = backend.move_to_absolute(
                {"x": 62, "y": 50},
                allowed_region={"x": 40, "y": 40, "width": 80, "height": 60},
                allowed_foreground_titles=["RuneLite"],
                max_chunk_px=12,
                tolerance_px=0,
                feedback_tolerance_px=0,
                move_settle_ms=0,
                move_poll_ms=5,
                move_noeffect_timeout_ms=10,
                move_noeffect_retries=1,
            )
        finally:
            arduino_hid_module._cursor_position = original_cursor
            arduino_hid_module._foreground_window_info = original_foreground

        self.assertEqual(trace["status"], "PASS")
        self.assertEqual(trace["noEffectChunks"], 1)
        self.assertEqual(trace["retryChunks"], 1)
        self.assertEqual(trace["movementChunks"][0]["classification"], "move_chunk_no_rawinput_no_cursor")
        self.assertEqual(trace["movementChunks"][1]["classification"], "move_chunk_retry_success")
        self.assertEqual(trace["movementSuccessRate"], 0.5)

    def test_arduino_absolute_move_aborts_after_repeated_no_effect(self):
        cursor = [50, 50]
        serials = []

        def factory(*args, **kwargs):
            serial = FakeSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        original_cursor = arduino_hid_module._cursor_position
        original_foreground = arduino_hid_module._foreground_window_info
        try:
            arduino_hid_module._cursor_position = lambda: (cursor[0], cursor[1])
            arduino_hid_module._foreground_window_info = lambda: {"available": True, "title": "RuneLite", "pid": 123}
            backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=lambda _seconds: None)
            backend.arm("session")

            with self.assertRaisesRegex(ArduinoHIDError, "move_chunk_no_effect_abort"):
                backend.move_to_absolute(
                    {"x": 62, "y": 50},
                    allowed_region={"x": 40, "y": 40, "width": 80, "height": 60},
                    allowed_foreground_titles=["RuneLite"],
                    max_chunk_px=12,
                    move_poll_ms=5,
                    move_noeffect_timeout_ms=10,
                    move_noeffect_retries=2,
                    move_max_consecutive_noeffect=2,
                )
        finally:
            arduino_hid_module._cursor_position = original_cursor
            arduino_hid_module._foreground_window_info = original_foreground

        self.assertIn("STOP_ALL", serials[0].commands)
        self.assertIn("DISARM", serials[0].commands)
        self.assertFalse(backend.armed)
        self.assertEqual(backend.last_movement_trace["movementAbortedReason"], "move_chunk_no_effect_abort")
        self.assertEqual(backend.last_movement_trace["maxConsecutiveNoEffectChunks"], 2)

    def test_arduino_absolute_move_classifies_rawinput_seen_cursor_no_move(self):
        cursor = [50, 50]
        raw_state = {"count": 0}

        class RawOnlySerial(FakeSerial):
            def write(self, data):
                command = data.decode("utf-8").strip()
                if command.startswith("MOVE "):
                    raw_state["count"] += 1
                super().write(data)

        def monitor_status():
            return {
                "arduinoActivity": {
                    "rawInputMouseCount": raw_state["count"],
                    "rawInputMouseDxTotal": 12 * raw_state["count"],
                    "rawInputMouseDyTotal": 0,
                },
                "injectionFlags": {},
                "backend": {"directBackendBypassCount": 0},
            }

        original_cursor = arduino_hid_module._cursor_position
        original_foreground = arduino_hid_module._foreground_window_info
        try:
            arduino_hid_module._cursor_position = lambda: (cursor[0], cursor[1])
            arduino_hid_module._foreground_window_info = lambda: {"available": True, "title": "RuneLite", "pid": 123}
            backend = ArduinoHIDBackend(port="COM9", serial_factory=RawOnlySerial, sleep_func=lambda _seconds: None)
            backend.arm("session")

            with self.assertRaisesRegex(ArduinoHIDError, "move_chunk_no_effect_abort"):
                backend.move_to_absolute(
                    {"x": 62, "y": 50},
                    allowed_region={"x": 40, "y": 40, "width": 80, "height": 60},
                    allowed_foreground_titles=["RuneLite"],
                    max_chunk_px=12,
                    move_poll_ms=5,
                    move_noeffect_timeout_ms=10,
                    move_noeffect_retries=0,
                    monitor_status_reader=monitor_status,
                )
        finally:
            arduino_hid_module._cursor_position = original_cursor
            arduino_hid_module._foreground_window_info = original_foreground

        self.assertEqual(backend.last_movement_trace["movementChunks"][0]["classification"], "move_chunk_rawinput_seen_cursor_no_move")
        self.assertTrue(backend.last_movement_trace["movementChunks"][0]["rawInputDeltaObserved"])
        self.assertFalse(backend.last_movement_trace["movementChunks"][0]["cursorDeltaObserved"])

    def test_arduino_absolute_move_aborts_target_outside_allowed_region(self):
        cursor = [50, 50]
        serials = []

        def factory(*args, **kwargs):
            serial = FakeSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        original_cursor = arduino_hid_module._cursor_position
        original_foreground = arduino_hid_module._foreground_window_info
        try:
            arduino_hid_module._cursor_position = lambda: (cursor[0], cursor[1])
            arduino_hid_module._foreground_window_info = lambda: {"available": True, "title": "RuneLite", "pid": 123}
            backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=lambda _seconds: None)
            backend.arm("session")

            with self.assertRaisesRegex(ArduinoHIDError, "target_outside_allowed_region"):
                backend.move_to_absolute(
                    {"x": 500, "y": 74},
                    allowed_region={"x": 40, "y": 40, "width": 80, "height": 60},
                    allowed_foreground_titles=["RuneLite"],
                )
        finally:
            arduino_hid_module._cursor_position = original_cursor
            arduino_hid_module._foreground_window_info = original_foreground

        commands = serials[0].commands
        self.assertIn("STOP_ALL", commands)
        self.assertIn("DISARM", commands)
        self.assertFalse(backend.armed)
        self.assertEqual(backend.last_movement_trace["movementAbortedReason"], "target_outside_allowed_region")

    def test_arduino_absolute_move_aborts_when_cursor_leaves_region(self):
        cursor = [50, 50]

        class OvershootSerial(FakeSerial):
            def write(self, data):
                command = data.decode("utf-8").strip()
                if command.startswith("MOVE "):
                    _name, dx, dy = command.split()
                    cursor[0] += int(dx) * 8
                    cursor[1] += int(dy) * 8
                super().write(data)

        serials = []

        def factory(*args, **kwargs):
            serial = OvershootSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        original_cursor = arduino_hid_module._cursor_position
        original_foreground = arduino_hid_module._foreground_window_info
        try:
            arduino_hid_module._cursor_position = lambda: (cursor[0], cursor[1])
            arduino_hid_module._foreground_window_info = lambda: {"available": True, "title": "RuneLite", "pid": 123}
            backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=lambda _seconds: None)
            backend.arm("session")

            with self.assertRaisesRegex(ArduinoHIDError, "cursor_left_allowed_region|movement_feedback_mismatch"):
                backend.move_to_absolute(
                    {"x": 70, "y": 50},
                    allowed_region={"x": 40, "y": 40, "width": 45, "height": 45},
                    allowed_foreground_titles=["RuneLite"],
                    feedback_tolerance_px=3,
                )
        finally:
            arduino_hid_module._cursor_position = original_cursor
            arduino_hid_module._foreground_window_info = original_foreground

        self.assertIn("STOP_ALL", serials[0].commands)
        self.assertIn("DISARM", serials[0].commands)
        self.assertFalse(backend.armed)

    def test_arduino_absolute_move_aborts_when_foreground_changes(self):
        cursor = [50, 50]
        foreground_calls = {"count": 0}

        class TrackingSerial(FakeSerial):
            def write(self, data):
                command = data.decode("utf-8").strip()
                if command.startswith("MOVE "):
                    _name, dx, dy = command.split()
                    cursor[0] += int(dx)
                    cursor[1] += int(dy)
                super().write(data)

        serials = []

        def factory(*args, **kwargs):
            serial = TrackingSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        def foreground():
            foreground_calls["count"] += 1
            title = "RuneLite" if foreground_calls["count"] <= 1 else "Jagex Launcher"
            return {"available": True, "title": title, "pid": 123}

        original_cursor = arduino_hid_module._cursor_position
        original_foreground = arduino_hid_module._foreground_window_info
        try:
            arduino_hid_module._cursor_position = lambda: (cursor[0], cursor[1])
            arduino_hid_module._foreground_window_info = foreground
            backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=lambda _seconds: None)
            backend.arm("session")

            with self.assertRaisesRegex(ArduinoHIDError, "foreground_window_changed"):
                backend.move_to_absolute(
                    {"x": 74, "y": 50},
                    allowed_region={"x": 40, "y": 40, "width": 80, "height": 60},
                    allowed_foreground_titles=["RuneLite"],
                )
        finally:
            arduino_hid_module._cursor_position = original_cursor
            arduino_hid_module._foreground_window_info = original_foreground

        self.assertIn("STOP_ALL", serials[0].commands)
        self.assertIn("DISARM", serials[0].commands)
        self.assertFalse(backend.armed)

    def test_arduino_backend_rearms_once_after_watchdog_disarm(self):
        class WatchdogDisarmSerial(FakeSerial):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.not_armed_sent = False

            def readline(self):
                command = self.commands[-1]
                if command.startswith("MOVE ") and not self.not_armed_sent:
                    self.not_armed_sent = True
                    return b"ERR NOT_ARMED MOVE\n"
                return super().readline()

        serials = []

        def factory(*args, **kwargs):
            serial = WatchdogDisarmSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=lambda _seconds: None)

        backend.arm("session")
        backend.move_relative(2, 0, duration_ms=20)

        commands = serials[0].commands
        self.assertEqual(commands.count("ARM session"), 2)
        self.assertEqual([command for command in commands if command == "MOVE 2 0"], ["MOVE 2 0", "MOVE 2 0"])
        self.assertTrue(backend.armed)
        self.assertEqual(backend.status()["watchdogRearms"], 1)
        self.assertEqual(backend.status()["sessionRearms"], 1)
        self.assertIsNone(backend.status()["lastError"])

    def test_arduino_backend_ensure_armed_rearms_only_active_session(self):
        serials = []

        def factory(*args, **kwargs):
            serial = FakeSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=lambda _seconds: None)

        self.assertFalse(backend.ensure_armed())
        self.assertEqual(serials, [])

        backend.arm("session")
        backend.stop_all()
        self.assertFalse(backend.armed)
        self.assertTrue(backend.ensure_armed())
        self.assertTrue(backend.armed)

        commands = serials[0].commands
        self.assertEqual(commands.count("ARM session"), 2)
        self.assertEqual(backend.status()["sessionRearms"], 1)

        backend.disarm()
        self.assertFalse(backend.ensure_armed())
        self.assertEqual(commands.count("ARM session"), 2)

    def test_human_input_controller_rearms_active_arduino_session(self):
        serials = []

        def factory(*args, **kwargs):
            serial = FakeSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=lambda _seconds: None)
        backend.arm("session")
        backend.stop_all()
        controller = HumanInputController(backend, live_input_backend_required=True)

        controller.press_key("1")

        commands = serials[0].commands
        self.assertEqual(commands.count("ARM session"), 2)
        self.assertIn("KEY_PRESS 1 50", commands)
        self.assertEqual(controller.metrics()["backendBlockedCommandCount"], 0)

    def test_executor_rearms_before_each_live_action_when_backend_is_unarmed(self):
        backend = FakeCalibrationBackend()
        backend.session_token = "session"
        status = {
            "schema": "live_input_policy.v1",
            "liveInputBackend": "arduino",
            "liveInputBackendRequired": True,
            "requestedLiveInput": True,
            "arduinoArmed": True,
        }

        _ensure_live_input_session_for_action(live_options(), backend, status)

        self.assertTrue(backend.armed)
        self.assertIn(("arm",), backend.calls)
        self.assertEqual(status["arduinoRearmedBeforeActionCount"], 1)
        self.assertTrue(status["arduino"]["armed"])

    def test_arduino_backend_does_not_rearm_stop_all_or_disarm(self):
        class DisarmNotArmedSerial(FakeSerial):
            def readline(self):
                command = self.commands[-1]
                if command == "DISARM":
                    return b"ERR NOT_ARMED DISARM\n"
                return super().readline()

        serials = []

        def factory(*args, **kwargs):
            serial = DisarmNotArmedSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=lambda _seconds: None)

        backend.arm("session")
        backend.disarm()

        commands = serials[0].commands
        self.assertEqual(commands.count("ARM session"), 1)
        self.assertFalse(backend.armed)

    def test_backend_fails_closed_on_identify_err_unknown(self):
        class OldSerial(FakeSerial):
            def readline(self):
                command = self.commands[-1]
                if command == "STOP_ALL":
                    return b"OK STOP_ALL\n"
                if command == "PING":
                    return b"OK PONG\n"
                if command == "IDENTIFY":
                    return b"ERR UNKNOWN IDENTIFY\n"
                return super().readline()

        backend = ArduinoHIDBackend(port="COM9", serial_factory=OldSerial, sleep_func=lambda _seconds: None)

        with self.assertRaisesRegex(ArduinoHIDError, "IDENTIFY"):
            backend.arm("session")
        self.assertFalse(backend.armed)

    def test_backend_fails_closed_when_caps_missing_safety_caps(self):
        class UnsafeCapsSerial(FakeSerial):
            def readline(self):
                command = self.commands[-1]
                if command == "CAPS":
                    return b"OK CAPS mouse=1 keyboard=1 stopAll=0 watchdog=0 resetSafe=0\n"
                return super().readline()

        backend = ArduinoHIDBackend(port="COM9", serial_factory=UnsafeCapsSerial, sleep_func=lambda _seconds: None)

        with self.assertRaisesRegex(ArduinoHIDError, "missing required safety caps"):
            backend.arm("session")
        self.assertFalse(backend.armed)

    def test_stop_all_works_even_when_disarmed(self):
        serials = []

        def factory(*args, **kwargs):
            serial = FakeSerial(*args, **kwargs)
            serials.append(serial)
            return serial

        backend = ArduinoHIDBackend(port="COM9", serial_factory=factory, sleep_func=lambda _seconds: None)

        backend.stop_all()

        self.assertIn("STOP_ALL", serials[0].commands)
        self.assertFalse(backend.armed)

    def test_monitor_required_blocks_when_status_missing(self):
        status = check_arduino_monitor_status(require_monitor=True, status_path="missing-monitor.json")

        self.assertFalse(status["monitorPass"])
        self.assertEqual(status["monitorBlockReason"], "monitor_status_unavailable")

    def test_monitor_passes_with_raw_input_and_no_injected_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "monitor.json"
            path.write_text(
                json.dumps(
                    {
                        "arduinoRawInputSeen": True,
                        "arduinoKeyboardSeen": True,
                        "arduinoMouseSeen": True,
                        "devicePath": "HID\\VID_2341&PID_8036",
                        "injectedEvents": 0,
                        "lowerIlInjectedEvents": 0,
                        "lastArduinoEventAgeMs": 50,
                        "firmware": {
                            "status": "OK",
                            "protocol": "arduino_hid.v1",
                            "resetSafe": True,
                            "stopAll": True,
                            "watchdog": True,
                            "watchdogMs": 1000,
                            "keysDown": 0,
                            "mouseButtonsDown": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )

            status = check_arduino_monitor_status(
                require_monitor=True,
                status_path=path,
                expected_vid="VID_2341",
                expected_pid="PID_8036",
            )

        self.assertTrue(status["monitorPass"])
        self.assertIsNone(status["monitorBlockReason"])

    def test_monitor_stale_last_event_age_is_warning_not_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "monitor.json"
            path.write_text(
                json.dumps(
                    {
                        "monitorRunning": True,
                        "arduinoDetected": {
                            "rawInputDevicePresent": True,
                            "keyboardPresent": True,
                            "mousePresent": True,
                            "vidPidMatched": True,
                        },
                        "arduinoActivity": {
                            "rawInputMouseCount": 1,
                            "rawInputKeyboardCount": 0,
                            "lastAnyEventAgeMs": 20000,
                        },
                        "injectionFlags": {
                            "mouseInjectedCount": 0,
                            "keyboardInjectedCount": 0,
                            "mouseLowerIlInjectedCount": 0,
                            "keyboardLowerIlInjectedCount": 0,
                        },
                        "backend": {
                            "liveInputBackend": "arduino",
                            "arduinoBackendSelected": True,
                            "arduinoArmed": False,
                            "directBackendBypassCount": 0,
                        },
                        "firmware": {
                            "status": "OK",
                            "protocol": "arduino_hid.v1",
                            "resetSafe": True,
                            "stopAll": True,
                            "watchdog": True,
                            "keysDown": 0,
                            "mouseButtonsDown": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )

            status = check_arduino_monitor_status(require_monitor=True, status_path=path, max_event_age_ms=3000)

        self.assertTrue(status["monitorPass"])
        self.assertEqual(status["status"], "WARN")
        self.assertIn("last_arduino_event_stale", status["warnings"])
        self.assertNotIn("last_arduino_event_stale", status["blockers"])

    def test_monitor_blocks_when_reset_safe_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "monitor.json"
            path.write_text(
                json.dumps(
                    {
                        "arduinoDetected": {
                            "rawInputDevicePresent": True,
                            "keyboardPresent": True,
                            "mousePresent": True,
                            "vidPidMatched": True,
                        },
                        "injectionFlags": {
                            "mouseInjectedCount": 0,
                            "keyboardInjectedCount": 0,
                            "mouseLowerIlInjectedCount": 0,
                            "keyboardLowerIlInjectedCount": 0,
                        },
                        "firmware": {
                            "status": "FAIL",
                            "protocol": "old",
                            "resetSafe": False,
                            "stopAll": False,
                            "watchdog": False,
                        },
                    }
                ),
                encoding="utf-8",
            )

            status = check_arduino_monitor_status(require_monitor=True, status_path=path)

        self.assertFalse(status["monitorPass"])
        self.assertIn("firmware_protocol_failed", status["blockers"])

    def test_static_scan_keeps_direct_software_input_in_allowed_modules(self):
        allowed = {
            VIEWER_DIR / "input_control" / "backend_pyautogui.py",
            VIEWER_DIR / "input_control" / "backend_pydirectinput.py",
            VIEWER_DIR / "input_control" / "visual_debug_bundle.py",
        }
        violations = []
        direct_pattern = re.compile(r"\bimport\s+(pyautogui|pydirectinput)\b|\b(pyautogui|pydirectinput)\.")
        for path in (VIEWER_DIR / "input_control").glob("*.py"):
            if path in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            if direct_pattern.search(text):
                violations.append(str(path.relative_to(VIEWER_DIR)))

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()

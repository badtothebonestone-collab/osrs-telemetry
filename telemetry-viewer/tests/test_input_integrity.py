import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import execute_next_action as execute_cli
from input_control.executor import ExecutionResult, _attach_live_input_status, _start_live_input_session
from input_control.input_integrity import (
    build_input_integrity_status,
    build_vmware_autoconnect_recommendation,
    input_integrity_delta,
    overlay_display_state,
)


def raw_status(**overrides):
    payload = {
        "generatedAtMillis": 1000,
        "monitorRunning": True,
        "arduinoDetected": {
            "rawInputDevicePresent": True,
            "keyboardPresent": True,
            "mousePresent": True,
            "vidPidMatched": True,
        },
        "arduinoActivity": {
            "lastAnyEventAgeMs": 20,
            "lastMouseEventAgeMs": 20,
            "lastKeyboardEventAgeMs": 20,
            "mouseEventCount": 1,
            "keyboardEventCount": 1,
            "rawInputMouseCount": 1,
            "rawInputKeyboardCount": 1,
        },
        "injectionFlags": {
            "mouseInjectedCount": 0,
            "mouseLowerIlInjectedCount": 0,
            "keyboardInjectedCount": 0,
            "keyboardLowerIlInjectedCount": 0,
        },
        "backend": {
            "liveInputBackend": "arduino",
            "arduinoBackendSelected": True,
            "arduinoArmed": True,
            "softwareInputAllowed": False,
            "directBackendBypassCount": 0,
        },
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
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key].update(value)
        else:
            payload[key] = value
    return payload


class InputIntegrityStatusTest(unittest.TestCase):
    def test_pass_when_arduino_raw_input_present_and_injected_counts_zero(self):
        status = build_input_integrity_status(raw_status(), require_monitor=True, require_armed=True, now_ms=1010)

        self.assertEqual(status["schema"], "input_integrity_status.v1")
        self.assertEqual(status["status"], "PASS")
        self.assertTrue(status["monitorPass"])

    def test_fail_when_vid_pid_mismatch(self):
        status = build_input_integrity_status(
            raw_status(arduinoDetected={"vidPidMatched": False}),
            require_monitor=True,
            expected_vid="VID_2341",
            expected_pid="PID_8036",
            now_ms=1010,
        )

        self.assertEqual(status["status"], "FAIL")
        self.assertIn("expected_vid_pid_not_matched", status["blockers"])

    def test_fail_when_monitor_stale_and_required(self):
        status = build_input_integrity_status(raw_status(generatedAtMillis=1000), require_monitor=True, max_age_ms=100, now_ms=2000)

        self.assertEqual(status["status"], "FAIL")
        self.assertIn("monitor_stale", status["blockers"])

    def test_fail_when_mouse_injected_count_increases(self):
        status = build_input_integrity_status(
            raw_status(injectionFlags={"mouseInjectedCount": 1}),
            require_monitor=True,
            now_ms=1010,
        )

        self.assertEqual(status["status"], "FAIL")
        self.assertIn("injected_input_detected", status["blockers"])

    def test_fail_when_keyboard_injected_count_increases(self):
        status = build_input_integrity_status(
            raw_status(injectionFlags={"keyboardInjectedCount": 1}),
            require_monitor=True,
            now_ms=1010,
        )

        self.assertEqual(status["status"], "FAIL")
        self.assertIn("injected_input_detected", status["blockers"])

    def test_fail_when_lower_il_injected_count_increases(self):
        status = build_input_integrity_status(
            raw_status(injectionFlags={"keyboardLowerIlInjectedCount": 1}),
            require_monitor=True,
            now_ms=1010,
        )

        self.assertEqual(status["status"], "FAIL")
        self.assertIn("injected_input_detected", status["blockers"])

    def test_fail_when_direct_backend_bypass_count_is_nonzero(self):
        status = build_input_integrity_status(
            raw_status(backend={"directBackendBypassCount": 1}),
            require_monitor=True,
            now_ms=1010,
        )

        self.assertEqual(status["status"], "FAIL")
        self.assertIn("backend_bypass_detected", status["blockers"])

    def test_overlay_status_maps_to_expected_display_state(self):
        self.assertEqual(overlay_display_state({"status": "PASS"})["background"], "#0f7d32")
        self.assertEqual(overlay_display_state({"status": "WARN"})["background"], "#b7791f")
        self.assertEqual(overlay_display_state({"status": "FAIL"})["background"], "#b00020")

    def test_delta_reports_raw_input_and_injected_changes(self):
        before = build_input_integrity_status(raw_status(), now_ms=1010)
        after = build_input_integrity_status(
            raw_status(
                arduinoActivity={"rawInputMouseCount": 2},
                injectionFlags={"mouseInjectedCount": 1, "keyboardLowerIlInjectedCount": 1},
                backend={"directBackendBypassCount": 1},
            ),
            now_ms=1020,
        )

        delta = input_integrity_delta(before, after)

        self.assertEqual(delta["rawInputMouseCountDelta"], 1)
        self.assertEqual(delta["mouseInjectedCountDelta"], 1)
        self.assertEqual(delta["lowerIlInjectedCountDelta"], 1)
        self.assertEqual(delta["directBackendBypassCountDelta"], 1)

    def test_firmware_pass_does_not_imply_vm_focus_pass(self):
        status = build_input_integrity_status(
            raw_status(
                vmInputFocusSafety={
                    "status": "WARN",
                    "postTestInputState": "unknown",
                    "postTestFocusRecovery": "unknown",
                }
            ),
            require_monitor=True,
            require_armed=True,
            now_ms=1010,
        )

        self.assertEqual(status["firmwareSafety"]["status"], "PASS")
        self.assertEqual(status["vmInputFocusSafety"]["status"], "WARN")
        self.assertEqual(status["status"], "WARN")
        self.assertIn("vm_input_focus_not_confirmed", status["warnings"])

    def test_overlay_passive_flags_are_represented_in_focus_status(self):
        status = build_input_integrity_status(
            raw_status(
                overlay={"focusable": False, "clickThrough": True, "topmost": True},
                vmInputFocusSafety={"status": "PASS", "postTestInputState": "normal", "postTestFocusRecovery": "PASS"},
            ),
            now_ms=1010,
        )

        self.assertFalse(status["vmInputFocusSafety"]["overlayFocusable"])
        self.assertTrue(status["vmInputFocusSafety"]["overlayClickThrough"])
        self.assertTrue(status["vmInputFocusSafety"]["overlayTopmost"])

    def test_autoconnect_recommendation_includes_sketch_and_bootloader_pids(self):
        recommendation = build_vmware_autoconnect_recommendation(
            sketch_vid="VID_2341",
            sketch_pid="PID_8036",
            bootloader_vid="VID_2341",
            bootloader_pid="PID_0036",
        )

        self.assertIn('usb.autoConnect.device0 = "vid:2341 pid:8036"', recommendation["lines"])
        self.assertIn('usb.autoConnect.device1 = "vid:2341 pid:0036"', recommendation["lines"])


class SelfTestBackend:
    name = "arduino"
    arduino_hid_backend = True

    def __init__(self):
        self.armed = False
        self.disarmed = False
        self.moves = []
        self.stop_all_count = 0

    def status(self):
        return {
            "schema": "arduino_hid_backend_status.v1",
            "armed": self.armed,
            "identity": {"protocol": "arduino_hid.v1", "name": "ArduinoHIDBridge"},
            "capabilities": {"mouse": True, "keyboard": True, "stopAll": True, "watchdog": True, "resetSafe": True},
            "firmwareStatus": {"armed": self.armed, "keysDown": 0, "mouseButtonsDown": 0, "watchdogMs": 1000},
        }

    def stop_all(self):
        self.stop_all_count += 1
        self.armed = False
        return self.status()

    def ping(self):
        return "OK PONG"

    def identify(self):
        return {"protocol": "arduino_hid.v1", "name": "ArduinoHIDBridge"}

    def capabilities(self):
        return {"mouse": True, "keyboard": True, "stopAll": True, "watchdog": True, "resetSafe": True}

    def firmware_status(self):
        return {"armed": self.armed, "keysDown": 0, "mouseButtonsDown": 0, "watchdogMs": 1000}

    def arm(self, _token=None):
        self.armed = True
        return self.status()

    def move_relative(self, _dx, _dy, *, duration_ms=0):
        self.moves.append((_dx, _dy, duration_ms))
        raise RuntimeError("simulated movement failure")

    def disarm(self):
        self.armed = False
        self.disarmed = True
        return self.status()


class InputIntegritySelfTestTest(unittest.TestCase):
    def test_pre_live_operator_injected_counts_do_not_block_arming(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_path = Path(tmp) / "status.json"
            status_path.write_text(
                json.dumps(
                    raw_status(
                        arduinoDetected={"comPortMatched": True},
                        injectionFlags={"mouseInjectedCount": 3, "keyboardLowerIlInjectedCount": 1},
                    )
                ),
                encoding="utf-8",
            )
            backend = SelfTestBackend()
            args = Namespace(
                execute=True,
                hover_only=False,
                camera_self_test=False,
                arduino_require_monitor=True,
                arduino_monitor_status_path=str(status_path),
                arduino_vid="VID_2341",
                arduino_pid="PID_8036",
                arduino_port="COM9",
                arduino_monitor_max_age_ms=10**15,
                arduino_session_token="session",
                input_integrity_fail_on_injected=True,
                input_integrity_fail_on_bypass=True,
            )

            status = _start_live_input_session(args, backend)

        self.assertEqual(status["status"], "PASS")
        self.assertTrue(status["arduinoArmed"])
        self.assertEqual(status["inputIntegrityStatusBefore"]["injectedEvents"], 3)
        self.assertEqual(status["inputIntegrityStatusBefore"]["lowerIlInjectedEvents"], 1)

    def test_live_action_injected_delta_remains_hard_blocker(self):
        before = build_input_integrity_status(
            raw_status(injectionFlags={"mouseInjectedCount": 3, "keyboardLowerIlInjectedCount": 1}),
            fail_on_injected=False,
            now_ms=1010,
        )
        after = build_input_integrity_status(
            raw_status(injectionFlags={"mouseInjectedCount": 4, "keyboardLowerIlInjectedCount": 1}),
            fail_on_injected=False,
            now_ms=1020,
        )
        status = {
            "schema": "live_input_policy.v1",
            "liveInputBackend": "arduino",
            "liveInputBackendRequired": True,
            "softwareInputAllowed": False,
            "inputIntegrityStatusBefore": before,
            "inputIntegrityStatusAfter": after,
            "inputIntegrityDelta": input_integrity_delta(before, after),
        }
        result = ExecutionResult(
            status="PASS",
            proposed_action="select_resource_target",
            dry_run=False,
            action_trace={"actionTraceSchema": "action_trace.v2", "humanInput": {"directBackendBypassCount": 0}},
        )
        args = Namespace(execute=True, hover_only=False, camera_self_test=False, input_integrity_fail_on_injected=True)

        _attach_live_input_status(result, status, options=args)

        phase = result.action_trace["inputIntegrityPhaseReport"]
        self.assertEqual(result.status, "FAIL")
        self.assertIn("live_input.injected_input", result.missing_capabilities)
        self.assertEqual(phase["operator_phase"]["operatorInjectedEvents"], 3)
        self.assertFalse(phase["operator_phase"]["blocking"])
        self.assertEqual(phase["live_action_phase"]["injectedEventsDelta"], 1)
        self.assertTrue(phase["live_action_phase"]["hardBlocker"])

    def test_self_test_disarms_arduino_even_on_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_path = Path(tmp) / "status.json"
            status_path.write_text(json.dumps(raw_status()), encoding="utf-8")
            backend = SelfTestBackend()
            args = Namespace(
                show_input_integrity_overlay=False,
                arduino_monitor_status_path=str(status_path),
                arduino_require_monitor=False,
                arduino_vid="VID_2341",
                arduino_pid="PID_8036",
                arduino_port="COM9",
                arduino_session_token="session",
                arduino_monitor_max_age_ms=3000,
                input_integrity_fail_on_injected=True,
                input_integrity_fail_on_bypass=True,
                input_integrity_self_test_no_move=False,
                no_overlay=True,
                close_overlay_after_test=True,
                post_test_focus_target="none",
                require_user_control_confirmation=False,
                window_title_filter="RuneLite",
            )

            payload = execute_cli.run_input_integrity_self_test(args, backend)

        self.assertEqual(payload["status"], "FAIL")
        self.assertTrue(payload["armed"])
        self.assertTrue(payload["disarmed"])
        self.assertTrue(backend.disarmed)

    def test_no_move_self_test_sends_no_move_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_path = Path(tmp) / "status.json"
            status_path.write_text(json.dumps(raw_status()), encoding="utf-8")
            backend = SelfTestBackend()
            args = Namespace(
                show_input_integrity_overlay=False,
                no_overlay=True,
                arduino_monitor_status_path=str(status_path),
                arduino_require_monitor=False,
                arduino_vid="VID_2341",
                arduino_pid="PID_8036",
                arduino_port="COM9",
                arduino_session_token="session",
                arduino_monitor_max_age_ms=3000,
                input_integrity_fail_on_injected=True,
                input_integrity_fail_on_bypass=True,
                input_integrity_self_test_no_move=True,
                close_overlay_after_test=True,
                post_test_focus_target="none",
                require_user_control_confirmation=False,
                window_title_filter="RuneLite",
            )

            payload = execute_cli.run_input_integrity_self_test(args, backend)

        self.assertEqual(payload["testMode"], "no_move")
        self.assertFalse(payload["tinyMoveSent"])
        self.assertEqual(backend.moves, [])
        self.assertGreaterEqual(backend.stop_all_count, 2)
        self.assertTrue(payload["disarmed"])

    def test_user_control_confirmation_blocks_without_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_path = Path(tmp) / "status.json"
            status_path.write_text(json.dumps(raw_status()), encoding="utf-8")
            backend = SelfTestBackend()
            args = Namespace(
                show_input_integrity_overlay=False,
                no_overlay=True,
                arduino_monitor_status_path=str(status_path),
                arduino_require_monitor=False,
                arduino_vid="VID_2341",
                arduino_pid="PID_8036",
                arduino_port="COM9",
                arduino_session_token="session",
                arduino_monitor_max_age_ms=3000,
                input_integrity_fail_on_injected=True,
                input_integrity_fail_on_bypass=True,
                input_integrity_self_test_no_move=True,
                close_overlay_after_test=True,
                post_test_focus_target="none",
                require_user_control_confirmation=True,
                window_title_filter="RuneLite",
            )
            original_input = __builtins__["input"] if isinstance(__builtins__, dict) else __builtins__.input
            try:
                if isinstance(__builtins__, dict):
                    __builtins__["input"] = lambda _prompt="": "NO"
                else:
                    __builtins__.input = lambda _prompt="": "NO"
                payload = execute_cli.run_input_integrity_self_test(args, backend)
            finally:
                if isinstance(__builtins__, dict):
                    __builtins__["input"] = original_input
                else:
                    __builtins__.input = original_input

        self.assertFalse(payload["continuationAllowed"])
        self.assertEqual(payload["userControlConfirmation"]["status"], "FAIL")
        self.assertIn("user_control_confirmation_missing", payload["warnings"])


if __name__ == "__main__":
    unittest.main()

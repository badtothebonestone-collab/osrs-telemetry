import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import arduino_mirror_verifier


class FakeSerial:
    def __init__(self, port, baud, timeout=None, write_timeout=None, rtscts=False, dsrdtr=False):
        self.port = port
        self.baud = baud
        self.last_command = ""

    def write(self, data):
        self.last_command = data.decode("utf-8").strip()
        return len(data)

    def flush(self):
        return None

    def readline(self):
        command = self.last_command.split(" ", 1)[0].upper()
        responses = {
            "PING": "OK PONG\n",
            "IDENTIFY": "OK IDENTIFY name=ArduinoHIDBridge version=1 protocol=arduino_hid.v1\n",
            "CAPS": "OK CAPS mouse=1 keyboard=1 stopAll=1 watchdog=1 resetSafe=1\n",
            "STATUS": "OK STATUS armed=0 keysDown=0 mouseButtonsDown=0 lastCommandAgeMs=1 watchdogMs=5000\n",
            "STOP_ALL": "OK STOP_ALL\n",
            "ARM": "OK ARMED\n",
            "DISARM": "OK DISARMED\n",
            "MOVE": "OK MOVE\n",
            "CLICK": "OK CLICK\n",
        }
        return responses.get(command, "OK STATUS armed=0\n").encode("utf-8")

    def reset_input_buffer(self):
        return None

    def reset_output_buffer(self):
        return None

    def close(self):
        return None


class ArduinoMirrorVerifierTest(unittest.TestCase):
    def test_mirror_verified_when_move_command_correlates(self):
        input_events = [{"kind": "mouse_move", "event_seq": 1, "elapsed_seconds": 1.05, "dx": 10, "dy": 0}]
        arduino_events = [{"kind": "command_sent", "event_seq": 1, "elapsed_seconds": 1.0, "command": "MOVE", "dx": 10, "dy": 0}]
        summary = arduino_mirror_verifier.build_input_path_integrity(input_events, arduino_events, requested_mode="mirror")
        self.assertEqual(summary["inputPathClassification"], "arduino_mirror_verified")
        self.assertEqual(summary["correlatedCommandToObservedMovementCount"], 1)

    def test_mirror_failed_when_no_observed_movement(self):
        summary = arduino_mirror_verifier.build_input_path_integrity([], [{"kind": "connect"}], requested_mode="mirror")
        self.assertEqual(summary["inputPathClassification"], "arduino_mirror_failed")
        self.assertFalse(summary["mirrorVerified"])

    def test_status_only_classification(self):
        arduino_events = [{"kind": "connect"}, {"kind": "command_sent", "command": "PING"}, {"kind": "ack_received", "command": "PING"}]
        summary = arduino_mirror_verifier.build_input_path_integrity([], arduino_events, requested_mode="bridge")
        self.assertEqual(summary["inputPathClassification"], "arduino_status_only")

    def test_mixed_input_path_when_mirror_commands_uncorrelated(self):
        input_events = [{"kind": "mouse_move", "event_seq": 1, "elapsed_seconds": 5.0, "dx": 4, "dy": 0}]
        arduino_events = [{"kind": "command_sent", "event_seq": 1, "elapsed_seconds": 1.0, "command": "MOVE", "dx": 4, "dy": 0}]
        summary = arduino_mirror_verifier.build_input_path_integrity(input_events, arduino_events, requested_mode="mirror")
        self.assertEqual(summary["inputPathClassification"], "mixed_input_path")
        self.assertTrue(summary["possibleDoubleInput"])

    def test_conversion_error_and_latency(self):
        input_events = [{"kind": "mouse_move", "event_seq": 2, "elapsed_seconds": 1.1, "dx": 12, "dy": 1}]
        arduino_events = [{"kind": "command_sent", "event_seq": 1, "elapsed_seconds": 1.0, "command": "MOVE", "dx": 10, "dy": 0}]
        summary = arduino_mirror_verifier.build_input_path_integrity(input_events, arduino_events, requested_mode="mirror")
        self.assertEqual(summary["correlatedCommandToObservedMovementCount"], 1)
        self.assertAlmostEqual(summary["correlationLatencyMs"]["average"], 100.0, places=2)
        self.assertGreater(summary["conversionErrorPx"]["average"], 0)

    def test_preflight_unproven_payload(self):
        payload = arduino_mirror_verifier.preflight_unproven_payload(requested_mode="mirror", port="COM6")
        self.assertFalse(payload["mirrorVerified"])
        self.assertEqual(payload["inputPathClassification"], "arduino_mirror_requested")

    def test_probe_verified_classification_is_not_live_mirror_verified(self):
        summary = arduino_mirror_verifier.build_input_path_integrity(
            [],
            [{"kind": "command_sent", "command": "MOVE", "dx": 12, "dy": 0, "ack_received": True, "probeCommand": True, "probeVerified": True, "probeClassification": "arduino_probe_verified_noisy"}],
            requested_mode="mirror",
        )
        self.assertEqual(summary["inputPathClassification"], "arduino_probe_verified_noisy")
        self.assertTrue(summary["probeVerified"])
        self.assertFalse(summary["mirrorVerified"])

    def test_run_probe_success_with_fake_serial_and_cursor_delta(self):
        samples = iter([(100, 100), (112, 100)])

        def cursor():
            return next(samples)

        with tempfile.TemporaryDirectory() as tmp:
            payload = arduino_mirror_verifier.run_probe(
                tmp,
                port="COM9",
                move=(12, 0),
                observe_ms=0,
                serial_factory=FakeSerial,
                cursor_reader=cursor,
                sleep_func=lambda _seconds: None,
            )
            self.assertTrue(payload["success"])
            self.assertEqual(payload["classification"], "arduino_probe_verified_clean")
            self.assertTrue((Path(tmp) / "arduino_action_commands.jsonl").exists())
            integrity = arduino_mirror_verifier.load_json(Path(tmp) / "input_path_integrity_summary.json")
            self.assertEqual(integrity["inputPathClassification"], "arduino_probe_verified_clean")

    def test_run_probe_no_observed_cursor_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = arduino_mirror_verifier.run_probe(
                tmp,
                port="COM9",
                move=(12, 0),
                observe_ms=0,
                serial_factory=FakeSerial,
                cursor_reader=lambda: (100, 100),
                sleep_func=lambda _seconds: None,
            )
            self.assertFalse(payload["success"])
            self.assertEqual(payload["classification"], "arduino_probe_sent_no_observed_delta")

    def test_previous_noisy_probe_is_not_clean_verified(self):
        quality = arduino_mirror_verifier.classify_probe_result(
            command_sent=True,
            supported=True,
            acked=True,
            requested_move=True,
            commanded_dx=12,
            commanded_dy=0,
            observed_dx=-465,
            observed_dy=178,
            max_error_px=100,
        )
        self.assertEqual(quality["classification"], "arduino_probe_verified_noisy")
        self.assertFalse(quality["clean"])


if __name__ == "__main__":
    unittest.main()

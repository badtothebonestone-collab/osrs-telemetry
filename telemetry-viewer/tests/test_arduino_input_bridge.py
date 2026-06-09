import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import arduino_input_bridge


class FakeSerial:
    def __init__(self, port, baud, timeout=None, write_timeout=None, rtscts=False, dsrdtr=False):
        self.port = port
        self.baud = baud
        self.last_command = ""
        self.closed = False

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
            "ARM": "OK ARMED\n",
            "DISARM": "OK DISARMED\n",
            "MOVE": "OK MOVE\n",
            "CLICK": "OK CLICK\n",
            "MOUSE_DOWN": "OK MOUSE_DOWN\n",
            "MOUSE_UP": "OK MOUSE_UP\n",
            "KEY_DOWN": "OK KEY_DOWN\n",
            "KEY_UP": "OK KEY_UP\n",
            "STOP_ALL": "OK STOP_ALL\n",
        }
        return responses.get(command, "OK STATUS armed=0\n").encode("utf-8")

    def reset_input_buffer(self):
        return None

    def reset_output_buffer(self):
        return None

    def close(self):
        self.closed = True


class ArduinoInputBridgeTest(unittest.TestCase):
    def test_port_discovery_helper_with_fake_data(self):
        with mock.patch("arduino_input_bridge._serial_list_ports", return_value=[{"device": "COM9", "description": "Arduino Leonardo"}]):
            ports = arduino_input_bridge.discover_arduino_ports()
        self.assertEqual(ports[0]["device"], "COM9")
        self.assertTrue(ports[0]["likelyArduino"])

    def test_status_unavailable_without_port(self):
        with mock.patch("arduino_input_bridge.discover_arduino_ports", return_value=[]):
            bridge = arduino_input_bridge.ArduinoInputBridge(None, passthrough_mode="bridge")
            status = bridge.start(require_available=False)
        self.assertEqual(status["status"], "unavailable")
        self.assertFalse(status["available"])

    def test_arduino_event_serialization_with_fake_serial(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = arduino_input_bridge.ArduinoInputBridge(
                tmp,
                recording_id="r1",
                port="COM9",
                serial_factory=FakeSerial,
                passthrough_mode="bridge",
            )
            status = bridge.start(require_available=True)
            bridge.stop()
            self.assertTrue(status["available"])
            events = arduino_input_bridge.load_arduino_events(Path(tmp) / "arduino_events.jsonl")
            kinds = {event["kind"] for event in events}
            self.assertIn("connect", kinds)
            self.assertIn("ack_received", kinds)
            self.assertIn("disconnect", kinds)
            saved = json.loads((Path(tmp) / "arduino_status.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["schema"], "arduino_input_bridge_status.v1")

    def test_calibration_math(self):
        result = arduino_input_bridge.calibration_result(10, -5, 12, -4)
        self.assertEqual(result["error_dx"], 2)
        self.assertEqual(result["error_dy"], 1)
        self.assertEqual(result["scale_x"], 1.2)

    def test_classifies_status_only_trace(self):
        summary = arduino_input_bridge.summarize_arduino_events(
            [
                {"kind": "connect", "port": "COM6"},
                {"kind": "command_sent", "command": "PING", "port": "COM6"},
                {"kind": "ack_received", "command": "PING", "port": "COM6"},
                {"kind": "command_sent", "command": "STATUS", "port": "COM6"},
            ]
        )
        self.assertEqual(summary["classification"], "arduino_status_only")
        self.assertEqual(summary["statusHealthCommandCount"], 2)
        self.assertEqual(summary["actionCommandCount"], 0)
        self.assertFalse(summary["perActionHidEvidence"])

    def test_classifies_action_command_trace(self):
        summary = arduino_input_bridge.summarize_arduino_events(
            [
                {"kind": "connect", "port": "COM6"},
                {"kind": "command_sent", "command": "MOVE", "dx": 5, "dy": 2, "port": "COM6"},
                {"kind": "command_sent", "command": "CLICK", "button": "left", "port": "COM6"},
                {"kind": "ack_received", "command": "CLICK", "port": "COM6"},
            ]
        )
        self.assertEqual(summary["classification"], "arduino_action_commands_seen")
        self.assertEqual(summary["actionCommandCount"], 2)
        self.assertEqual(summary["movementCommandCount"], 1)
        self.assertEqual(summary["clickCommandCount"], 1)
        self.assertTrue(summary["perActionHidEvidence"])

    def test_command_wrapper_writes_action_command_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = arduino_input_bridge.ArduinoCommandClient(tmp, recording_id="r2", port="COM9", serial_factory=FakeSerial)
            move = client.send_move(12, 0)
            click = client.send_click("left")
            client.close()
            self.assertEqual(move["command"], "MOVE")
            self.assertTrue(move["ack_received"])
            self.assertEqual(click["command"], "CLICK")
            records = arduino_input_bridge.load_arduino_action_commands(Path(tmp) / "arduino_action_commands.jsonl")
            self.assertEqual(len(records), 2)
            self.assertIn("command_id", records[0])
            self.assertIn("ack_latency_ms", records[0])
            summary = arduino_input_bridge.summarize_arduino_events(records)
            self.assertEqual(summary["movementCommandCount"], 1)
            self.assertEqual(summary["clickCommandCount"], 1)

    def test_command_wrapper_reports_unsupported_wheel(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = arduino_input_bridge.ArduinoCommandClient(tmp, recording_id="r3", port="COM9", serial_factory=FakeSerial)
            record = client.send_wheel(1)
            client.close()
            self.assertFalse(record["supported"])
            self.assertEqual(record["reason"], "unsupported_protocol")


if __name__ == "__main__":
    unittest.main()

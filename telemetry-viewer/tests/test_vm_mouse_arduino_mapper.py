import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import vm_mouse_arduino_mapper


class VmMouseArduinoMapperTest(unittest.TestCase):
    def test_mouse_path_to_relative_deltas(self):
        result = vm_mouse_arduino_mapper.mouse_path_to_relative_sequence(
            [{"x": 0, "y": 0}, {"x": 10, "y": -5}, {"x": 20, "y": 5}],
            max_step=20,
        )
        self.assertEqual(result["total_dx"], 20)
        self.assertEqual(result["total_dy"], 5)
        self.assertEqual(result["segment_count"], 2)

    def test_chunking_for_large_movement(self):
        chunks = vm_mouse_arduino_mapper.chunk_delta(55, -5, max_step=20)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(sum(chunk["dx"] for chunk in chunks), 55)
        self.assertEqual(sum(chunk["dy"] for chunk in chunks), -5)
        self.assertLessEqual(max(abs(chunk["dx"]) for chunk in chunks), 20)

    def test_click_to_button_events(self):
        commands = vm_mouse_arduino_mapper.click_to_arduino_button_events({"button": "right"}, hold_ms=25)
        self.assertEqual(commands[0]["command"], "MOUSE_DOWN")
        self.assertEqual(commands[1]["command"], "MOUSE_UP")
        self.assertEqual(commands[1]["delay_ms"], 25)

    def test_target_relative_click_offset(self):
        result = vm_mouse_arduino_mapper.target_relative_click(
            {"canvas_x": 105, "canvas_y": 95},
            {"effectiveName": "Tree", "effectiveId": 1276, "aimPoint": {"x": 100, "y": 100}},
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["dx"], 5)
        self.assertEqual(result["dy"], -5)

    def test_build_mapping_from_input_events(self):
        mapping = vm_mouse_arduino_mapper.build_mapping(
            [
                {"kind": "mouse_move", "screen_x": 0, "screen_y": 0},
                {"kind": "mouse_move", "screen_x": 30, "screen_y": 0},
                {"kind": "click", "event_seq": 3, "screen_x": 30, "screen_y": 0, "button": "left"},
            ],
            [],
        )
        self.assertEqual(mapping["schema"], "vm_mouse_arduino_mapping.v1")
        self.assertEqual(mapping["status"], "PASS")
        self.assertEqual(mapping["mousePath"]["total_dx"], 30)
        self.assertEqual(len(mapping["clickMappings"]), 1)

    def test_mapping_warns_when_input_trace_missing(self):
        mapping = vm_mouse_arduino_mapper.build_mapping(
            [],
            [{"kind": "command_sent", "command": "STATUS"}],
            telemetry_summary={"snapshotCount": 3},
            arduino_summary={"classification": "arduino_status_only"},
        )
        self.assertEqual(mapping["status"], "WARN")
        self.assertEqual(mapping["reason"], "input_trace_missing_or_empty")
        self.assertEqual(mapping["arduino"]["classification"], "arduino_status_only")

    def test_mapping_generates_relative_deltas_when_trace_exists(self):
        mapping = vm_mouse_arduino_mapper.build_mapping(
            [
                {"kind": "mouse_move", "screen_x": 5, "screen_y": 5},
                {"kind": "mouse_move", "screen_x": 15, "screen_y": 25},
            ],
            [],
        )
        self.assertEqual(mapping["status"], "PASS")
        self.assertEqual(mapping["mousePath"]["total_dx"], 10)
        self.assertEqual(mapping["mousePath"]["total_dy"], 20)
        self.assertEqual(mapping["mappingClassification"], "conversion_trace_only")
        self.assertTrue(mapping["conversionTraceOnly"])

    def test_mapping_uses_live_action_path_when_non_probe_commands_exist(self):
        mapping = vm_mouse_arduino_mapper.build_mapping(
            [{"kind": "mouse_move", "screen_x": 0, "screen_y": 0}, {"kind": "mouse_move", "screen_x": 12, "screen_y": 0}],
            [{"kind": "command_sent", "command": "MOVE", "dx": 12, "dy": 0, "ack_received": True}],
            arduino_summary={"actionCommandCount": 1},
            input_path_integrity={"inputPathClassification": "arduino_mirror_verified", "nonProbeActionCommandCount": 1},
        )
        self.assertEqual(mapping["mappingClassification"], "arduino_mirror_verified")
        self.assertFalse(mapping["conversionTraceOnly"])


if __name__ == "__main__":
    unittest.main()

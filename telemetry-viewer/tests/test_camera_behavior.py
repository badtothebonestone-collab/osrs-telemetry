import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import camera_behavior


class CameraBehaviorTest(unittest.TestCase):
    def test_camera_segment_from_middle_mouse_drag(self):
        telemetry_events = [
            {"event_type": "source_snapshot", "elapsed_seconds": 1.0, "high_value_fields": {"cameraYaw": 100, "cameraPitch": 200}},
            {"event_type": "source_snapshot", "elapsed_seconds": 1.5, "high_value_fields": {"cameraYaw": 140, "cameraPitch": 220}},
            {"event_type": "source_snapshot", "elapsed_seconds": 2.0, "high_value_fields": {"cameraYaw": 140, "cameraPitch": 220}},
        ]
        input_events = [
            {"kind": "drag_start", "button": "middle", "elapsed_seconds": 1.05, "screen_x": 800, "screen_y": 450},
            {"kind": "drag_move", "button": "middle", "elapsed_seconds": 1.2, "screen_x": 760, "screen_y": 430},
            {"kind": "drag_end", "button": "middle", "elapsed_seconds": 1.45, "screen_x": 740, "screen_y": 425},
            {"kind": "click", "elapsed_seconds": 2.2, "screen_x": 700, "screen_y": 400},
        ]
        summary = camera_behavior.summarize_camera_behavior(telemetry_events, input_events)
        self.assertEqual(summary["totalCameraSegments"], 1)
        self.assertEqual(summary["middleMouseDragSegments"], 1)
        self.assertEqual(summary["cameraBeforeClickCount"], 1)
        self.assertEqual(summary["segments"][0]["source"], "middle_mouse_drag")

    def test_camera_segment_from_arrow_keys(self):
        telemetry_events = [
            {"event_type": "source_snapshot", "elapsed_seconds": 1.0, "high_value_fields": {"cameraYaw": 100, "cameraPitch": 200}},
            {"event_type": "source_snapshot", "elapsed_seconds": 1.2, "high_value_fields": {"cameraYaw": 110, "cameraPitch": 200}},
            {"event_type": "source_snapshot", "elapsed_seconds": 1.5, "high_value_fields": {"cameraYaw": 110, "cameraPitch": 200}},
        ]
        input_events = [{"kind": "key_down", "key_name": "left", "elapsed_seconds": 1.1}]
        summary = camera_behavior.summarize_camera_behavior(telemetry_events, input_events)
        self.assertEqual(summary["arrowKeyCameraSegments"], 1)
        self.assertEqual(summary["segments"][0]["source"], "arrow_keys")

    def test_no_camera_fields_warns(self):
        summary = camera_behavior.summarize_camera_behavior([{"event_type": "source_snapshot", "elapsed_seconds": 1.0}], [])
        self.assertEqual(summary["status"], "WARN")
        self.assertIn("warnings", summary)


if __name__ == "__main__":
    unittest.main()

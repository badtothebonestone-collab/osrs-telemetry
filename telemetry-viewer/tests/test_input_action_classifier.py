import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import analyze_manual_recording
import camera_behavior
import input_action_classifier
import input_trace_joiner
import vm_mouse_arduino_mapper


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def click(seq=1, *, button="left", x=100, y=100, elapsed=1.0, region="viewport", runelite=True, kind="click"):
    return {
        "kind": kind,
        "event_seq": seq,
        "elapsed_seconds": elapsed,
        "button": button,
        "canvas_x": x,
        "canvas_y": y,
        "client_x": x,
        "client_y": y,
        "region": region,
        "runelite_window_match": runelite,
        "foreground_window_title": "RuneLite - Test" if runelite else "Other App",
    }


def snapshot(elapsed=0.5, *, with_target=True, moved=False, menu=False):
    base = {
        "elapsed_seconds": elapsed,
        "player_world_point": {"worldX": 3200 + int(moved), "worldY": 3200, "plane": 0},
        "menu": {"menuOpen": menu},
        "hover": {"topOption": "Chop down", "topTarget": "Tree"} if with_target else {},
        "nearby_objects": [],
        "route_objects": [],
        "nearby_npcs": [],
    }
    if with_target:
        base["nearby_objects"] = [
            {
                "kind": "object",
                "effectiveName": "Tree",
                "effectiveId": 1276,
                "effectiveActions": ["Chop down"],
                "aimPoint": {"x": 102, "y": 99},
                "worldPoint": {"worldX": 3201, "worldY": 3200, "plane": 0},
            }
        ]
    return base


class InputActionClassifierTest(unittest.TestCase):
    def test_left_click_with_target_is_object_action_click(self):
        rows, summary = input_action_classifier.classify_input_actions([click()], [snapshot()])
        row = rows[0]
        self.assertEqual(row["classification"], "object_action_click")
        self.assertTrue(row["targetRelativeEligible"])
        self.assertEqual(summary["targetRelativeClickCount"], 1)

    def test_left_click_with_movement_after_is_world_walk_click(self):
        rows, _summary = input_action_classifier.classify_input_actions(
            [click()],
            [snapshot(0.5, with_target=False), snapshot(1.5, with_target=False, moved=True)],
        )
        self.assertEqual(rows[0]["classification"], "world_walk_click")
        self.assertFalse(rows[0]["targetRelativeEligible"])

    def test_right_click_is_menu_open(self):
        rows, _summary = input_action_classifier.classify_input_actions([click(button="right")], [snapshot()])
        self.assertEqual(rows[0]["classification"], "right_click_menu_open")
        self.assertFalse(rows[0]["targetRelativeEligible"])

    def test_left_after_right_click_is_menu_selection(self):
        events = [click(1, button="right", elapsed=1.0), click(2, button="left", elapsed=1.8)]
        rows, summary = input_action_classifier.classify_input_actions(events, [snapshot(0.5), snapshot(2.0)])
        self.assertEqual(rows[1]["classification"], "menu_selection_click")
        self.assertIn("menuSelection", rows[1])
        self.assertEqual(rows[1]["selectedTarget"], "Tree")
        self.assertEqual(summary["menuSelectionClickCount"], 1)

    def test_middle_mouse_drag_release_is_excluded(self):
        events = [
            click(1, button="middle", x=10, y=10, elapsed=1.0, kind="mouse_down"),
            {"kind": "drag_move", "event_seq": 2, "button": "middle", "canvas_x": 30, "canvas_y": 10, "elapsed_seconds": 1.2},
            click(3, button="middle", x=30, y=10, elapsed=1.4, kind="mouse_up"),
            click(4, button="middle", x=30, y=10, elapsed=1.41),
        ]
        rows, summary = input_action_classifier.classify_input_actions(events, [snapshot()])
        labels = {row["eventSeq"]: row["classification"] for row in rows}
        self.assertEqual(labels[1], "camera_drag_click")
        self.assertEqual(labels[3], "camera_drag_release")
        self.assertEqual(labels[4], "camera_drag_release")
        self.assertEqual(summary["cameraDragReleaseCount"], 1)
        self.assertEqual(summary["targetRelativeClickCount"], 0)

    def test_mouse_up_after_drag_is_not_game_action(self):
        events = [
            click(1, button="left", x=10, y=10, elapsed=1.0, kind="mouse_down"),
            {"kind": "mouse_move", "event_seq": 2, "canvas_x": 40, "canvas_y": 10, "elapsed_seconds": 1.2},
            click(3, button="left", x=40, y=10, elapsed=1.4, kind="mouse_up"),
            click(4, button="left", x=40, y=10, elapsed=1.41),
        ]
        rows, summary = input_action_classifier.classify_input_actions(events, [snapshot()])
        self.assertEqual(rows[-1]["classification"], "ambiguous_click")
        self.assertFalse(rows[-1]["targetRelativeEligible"])
        self.assertEqual(summary["targetRelativeClickCount"], 0)

    def test_minimap_click(self):
        rows, _summary = input_action_classifier.classify_input_actions(
            [click(region="minimap", x=900, y=100)],
            [snapshot()],
        )
        self.assertEqual(rows[0]["classification"], "minimap_click")
        self.assertFalse(rows[0]["targetRelativeEligible"])

    def test_sidebar_click(self):
        rows, _summary = input_action_classifier.classify_input_actions(
            [click(region="inventory/sidebar", x=900, y=300)],
            [snapshot()],
        )
        self.assertIn(rows[0]["classification"], {"inventory_click", "sidebar_click"})
        self.assertFalse(rows[0]["targetRelativeEligible"])

    def test_external_click(self):
        rows, _summary = input_action_classifier.classify_input_actions(
            [click(runelite=False)],
            [snapshot()],
        )
        self.assertEqual(rows[0]["classification"], "external_click")

    def test_non_runelite_ui_drag_click_is_ui_control(self):
        events = [
            click(1, runelite=False, elapsed=1.0, x=10, y=10, kind="mouse_down"),
            {"kind": "mouse_move", "event_seq": 2, "canvas_x": 100, "canvas_y": 10, "elapsed_seconds": 1.1},
            click(3, runelite=False, elapsed=1.2, x=100, y=10, kind="mouse_up"),
            click(4, runelite=False, elapsed=1.21, x=100, y=10),
        ]
        for event in events:
            if isinstance(event, dict):
                event["foreground_window_title"] = "OSRS Telemetry Control"
                event["runelite_window_match"] = False
        rows, summary = input_action_classifier.classify_input_actions(events, [snapshot()])
        self.assertEqual(rows[-1]["classification"], "ui_control_click")
        self.assertEqual(summary["uiControlClickCount"], 1)
        self.assertFalse(rows[-1]["targetRelativeEligible"])

    def test_ambiguous_click_has_low_confidence_and_reason(self):
        rows, _summary = input_action_classifier.classify_input_actions(
            [click()],
            [snapshot(with_target=False), snapshot(elapsed=2.0, with_target=False)],
        )
        self.assertEqual(rows[0]["classification"], "ambiguous_click")
        self.assertLess(rows[0]["confidence"], 0.5)
        self.assertTrue(rows[0]["reasons"])

    def test_stale_telemetry_click_history_does_not_create_os_click(self):
        rows, summary = input_action_classifier.classify_input_actions(
            [],
            [{"elapsed_seconds": 1.0, "menu": {"lastMenuOptionClicked": {"option": "Chop down", "target": "Tree"}}}],
        )
        self.assertEqual(rows, [])
        self.assertEqual(summary["rawOsClickCount"], 0)

    def test_joiner_writes_classification_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            recording.mkdir()
            write_jsonl(
                recording / "events.jsonl",
                [
                    {
                        "event_type": "source_snapshot",
                        "elapsed_seconds": 0.5,
                        "latest_tick": 1,
                        "high_value_fields": {"nearby_objects": snapshot()["nearby_objects"]},
                    }
                ],
            )
            write_jsonl(recording / "input_events.jsonl", [click()])
            result = input_trace_joiner.analyze_recording(recording, write=True, include_mapping=True)
            self.assertIn("input_action_summary", result)
            self.assertTrue((recording / "input_action_classifications.jsonl").exists())
            self.assertTrue((recording / "input_action_summary.json").exists())

    def test_analyzer_summary_includes_action_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            recording.mkdir()
            write_jsonl(
                recording / "events.jsonl",
                [
                    {"event_type": "recording_start", "elapsed_seconds": 0, "session_id": "s1"},
                    {
                        "event_type": "source_snapshot",
                        "elapsed_seconds": 0.5,
                        "latest_tick": 1,
                        "high_value_fields": {"nearby_objects": snapshot()["nearby_objects"]},
                    },
                    {"event_type": "recording_stop", "elapsed_seconds": 2, "duration_seconds": 2},
                ],
            )
            write_jsonl(recording / "input_events.jsonl", [click()])
            summary = analyze_manual_recording.update_outputs(recording)
            self.assertIn("input_action_summary", summary)

    def test_camera_behavior_uses_eligible_clicks_only(self):
        telemetry_events = [
            {"event_type": "source_snapshot", "elapsed_seconds": 1.0, "high_value_fields": {"cameraYaw": 100, "cameraPitch": 200}},
            {"event_type": "source_snapshot", "elapsed_seconds": 1.5, "high_value_fields": {"cameraYaw": 140, "cameraPitch": 220}},
            {"event_type": "source_snapshot", "elapsed_seconds": 2.0, "high_value_fields": {"cameraYaw": 140, "cameraPitch": 220}},
        ]
        input_events = [click(10, button="middle", elapsed=2.2), click(11, button="left", elapsed=2.4)]
        classes = [
            {"eventSeq": 10, "eventKind": "click", "classification": "camera_drag_release", "targetRelativeEligible": False, "eligibleForTargetMatching": False},
            {"eventSeq": 11, "eventKind": "click", "classification": "object_action_click", "targetRelativeEligible": True, "eligibleForTargetMatching": True},
        ]
        summary = camera_behavior.summarize_camera_behavior(telemetry_events, input_events, action_classifications=classes)
        self.assertEqual(summary["cameraBeforeClickCount"], 1)
        self.assertEqual(summary["segments"][0]["nextClick"]["event_seq"], 11)

    def test_mapper_excludes_camera_drag_release_from_target_click_mapping(self):
        events = [click(1, button="middle")]
        classes = [{"eventSeq": 1, "eventKind": "click", "classification": "camera_drag_release", "targetRelativeEligible": False, "eligibleForTargetMatching": False}]
        mapping = vm_mouse_arduino_mapper.build_mapping(events, [], action_classifications=classes)
        self.assertEqual(len(mapping["clickMappings"]), 0)
        self.assertEqual(mapping["mappedCameraDragClickCount"], 1)


if __name__ == "__main__":
    unittest.main()

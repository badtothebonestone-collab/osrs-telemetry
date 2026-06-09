import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import analyze_manual_recording
import coordinate_spaces
import input_action_classifier
import input_trace_joiner
import menu_interaction_model
import target_match_quality


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def menu_sample():
    return {
        "sourceEvent": "MenuOpened",
        "menuOpen": True,
        "menuBounds": {"x": 369, "y": 206, "width": 103, "height": 52},
        "entryCount": 5,
        "entriesDisplayOrder": "top_to_bottom",
        "entries": [
            {"option": "Examine", "target": "Staircase", "type": "EXAMINE_OBJECT", "identifier": 16672},
            {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
            {"option": "Climb-down", "target": "Staircase", "type": "GAME_OBJECT_THIRD_OPTION", "identifier": 16672},
            {"option": "Climb-up", "target": "Staircase", "type": "GAME_OBJECT_SECOND_OPTION", "identifier": 16672},
            {"option": "Climb", "target": "Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 16672},
        ],
    }


def click(seq=37):
    return {
        "kind": "click",
        "event_seq": seq,
        "elapsed_seconds": 14.468,
        "button": "left",
        "client_x": 570,
        "client_y": 349,
        "screen_x": 1286,
        "screen_y": 403,
        "region": "viewport",
        "runelite_window_match": True,
        "foreground_window_title": "RuneLite - Test",
    }


def target():
    return {
        "kind": "object",
        "ref": "stair-ref",
        "rawId": 16672,
        "effectiveId": 16672,
        "effectiveName": "Staircase",
        "effectiveActions": ["Climb", "Climb-up", "Climb-down"],
        "worldPoint": {"worldX": 3204, "worldY": 3207, "plane": 1},
        "distance": 2,
        "onScreen": True,
        "menuActionAvailable": True,
    }


def snapshot(elapsed=14.0):
    sample = menu_sample()
    return {
        "elapsed_seconds": elapsed,
        "latest_tick": 257,
        "latest_export_sequence": 2570,
        "player_world_point": {"worldX": 3204, "worldY": 3207, "plane": 1},
        "hover": sample,
        "menu": sample,
        "nearby_objects": [target()],
        "route_objects": [],
        "nearby_npcs": [],
        "raw_event": {"high_value_fields": {"player": {"animation": -1}}},
    }


class CoordinateSpacesTest(unittest.TestCase):
    def test_point_in_bounds(self):
        self.assertTrue(coordinate_spaces.point_in_bounds({"x": 380, "y": 233}, {"x": 369, "y": 206, "width": 103, "height": 52}))
        self.assertFalse(coordinate_spaces.point_in_bounds({"x": 570, "y": 349}, {"x": 369, "y": 206, "width": 103, "height": 52}))

    def test_distance(self):
        self.assertEqual(coordinate_spaces.distance({"x": 0, "y": 0}, {"x": 3, "y": 4}), 5.0)

    def test_inverse_dpi_scale(self):
        scaled = coordinate_spaces.scale_point({"x": 570, "y": 349}, 1 / 1.5)
        self.assertAlmostEqual(scaled["x"], 380.0, places=2)
        self.assertAlmostEqual(scaled["y"], 232.666, places=2)

    def test_inverse_dpi_hits_menu_bounds(self):
        point = coordinate_spaces.scale_point({"x": 570, "y": 349}, 1 / 1.5)
        self.assertTrue(coordinate_spaces.point_in_bounds(point, {"x": 369, "y": 206, "width": 103, "height": 52}))

    def test_best_transform_chooses_inverse_scale_for_v2_shape(self):
        menu = menu_interaction_model.normalize_menu_snapshot({"hover": menu_sample()})
        result = coordinate_spaces.infer_best_transform_for_menu_hit(click(), menu, fallback_target=target())
        chosen = result["chosen"]
        self.assertEqual(chosen["selectedRow"]["option"], "Climb")
        self.assertTrue(chosen["insideRowBounds"])
        self.assertIn(chosen["name"], {"client_inverse_dpi_1_4", "client_inverse_dpi_1_425", "client_inverse_scale_target_row_anchor"})

    def test_menu_selection_uses_normalized_row_hit(self):
        menu = menu_interaction_model.normalize_menu_snapshot({"hover": menu_sample()})
        selection = menu_interaction_model.resolve_menu_selection(click(), menu, fallback_target=target())
        self.assertEqual(selection["selectedOption"], "Climb")
        self.assertTrue(selection["insideRowBounds"])
        self.assertIsNotNone(selection["normalizedClickPoint"])

    def test_classifier_uses_normalized_menu_selection(self):
        rows, summary = input_action_classifier.classify_input_actions([click()], [snapshot()])
        self.assertEqual(rows[0]["classification"], "menu_selection_click")
        self.assertTrue(rows[0]["insideMenuRowBounds"])
        self.assertEqual(summary["menuSelectionClickCount"], 1)

    def test_target_quality_confirms_normalized_menu_geometry(self):
        rows, _summary = input_action_classifier.classify_input_actions([click()], [snapshot()])
        quality = target_match_quality.score_target_match(click(), rows[0], None, snapshot(), snapshot(15), [snapshot(), snapshot(15)])
        self.assertEqual(quality["menuSelectionQuality"]["rowGeometryQuality"], "strong")
        self.assertIn("coordinateTransformUsed", quality["menuSelectionQuality"])

    def test_joiner_writes_coordinate_alignment_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            recording.mkdir()
            write_jsonl(recording / "events.jsonl", [{"event_type": "source_snapshot", "elapsed_seconds": 14.0, "latest_tick": 257, "high_value_fields": {"hover": menu_sample(), "menu": menu_sample(), "nearby_objects": [target()], "player": {"worldPoint": {"worldX": 3204, "worldY": 3207, "plane": 1}}}}])
            write_jsonl(recording / "input_events.jsonl", [click()])
            result = input_trace_joiner.analyze_recording(recording, write=True, include_mapping=True)
            self.assertIn("coordinate_alignment_summary", result)
            self.assertTrue((recording / "coordinate_alignment_summary.json").exists())

    def test_analyzer_includes_coordinate_alignment_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            recording.mkdir()
            write_jsonl(
                recording / "events.jsonl",
                [
                    {"event_type": "recording_start", "elapsed_seconds": 0},
                    {
                        "event_type": "source_snapshot",
                        "elapsed_seconds": 14.0,
                        "latest_tick": 257,
                        "high_value_fields": {
                            "hover": menu_sample(),
                            "menu": menu_sample(),
                            "nearby_objects": [target()],
                            "player": {"worldPoint": {"worldX": 3204, "worldY": 3207, "plane": 1}},
                        },
                    },
                    {"event_type": "recording_stop", "elapsed_seconds": 15, "duration_seconds": 15},
                ],
            )
            write_jsonl(recording / "input_events.jsonl", [click()])
            summary = analyze_manual_recording.update_outputs(recording)
            self.assertIn("coordinate_alignment_summary", summary)


if __name__ == "__main__":
    unittest.main()

import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import analyze_manual_recording
import input_action_classifier
import input_trace_joiner
import menu_interaction_model
import target_match_quality
import telemetry_ui


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def menu_sample():
    return {
        "schema": "plugin_hover_menu.v1",
        "sourceEvent": "MenuOpened",
        "menuOpen": True,
        "menuBounds": {"x": 500, "y": 300, "width": 160, "height": 63},
        "entryCount": 3,
        "entriesDisplayOrder": "top_to_bottom",
        "entries": [
            {"option": "Open", "target": "Door", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 1535, "param0": 47, "param1": 59},
            {"option": "Examine", "target": "Door", "type": "EXAMINE_OBJECT", "identifier": 1535},
            {"option": "Cancel", "target": "", "type": "CANCEL", "identifier": 0},
        ],
    }


def zero_bounds_staircase_sample():
    sample = menu_sample()
    sample["menuBounds"] = {"x": 0, "y": 0, "width": 0, "height": 0}
    sample["entryCount"] = 5
    sample["entries"] = [
        {"option": "Climb", "target": "Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 16672, "param0": 52, "param1": 47},
        {"option": "Climb-up", "target": "Staircase", "type": "GAME_OBJECT_SECOND_OPTION", "identifier": 16672, "param0": 52, "param1": 47},
        {"option": "Climb-down", "target": "Staircase", "type": "GAME_OBJECT_THIRD_OPTION", "identifier": 16672, "param0": 52, "param1": 47},
        {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
        {"option": "Examine", "target": "Staircase", "type": "EXAMINE_OBJECT", "identifier": 16672},
    ]
    return sample


def bounded_staircase_sample():
    sample = zero_bounds_staircase_sample()
    sample["sourceEvent"] = "PostMenuSort"
    sample["menuBounds"] = {"x": 290, "y": 125, "width": 150, "height": 112}
    return sample


def cancel_only_sample():
    return {
        "schema": "plugin_hover_menu.v1",
        "sourceEvent": "PostMenuSort",
        "menuOpen": False,
        "menuBounds": {"x": 290, "y": 125, "width": 150, "height": 112},
        "entryCount": 2,
        "entries": [
            {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
            {"option": "Cancel", "target": "", "type": "CANCEL", "identifier": 0},
        ],
    }


def click(seq=1, *, button="left", x=580, y=327, elapsed=2.0, kind="click"):
    return {
        "kind": kind,
        "event_seq": seq,
        "elapsed_seconds": elapsed,
        "button": button,
        "client_x": x,
        "client_y": y,
        "canvas_x": x,
        "canvas_y": y,
        "region": "viewport",
        "runelite_window_match": True,
        "foreground_window_title": "RuneLite - Test",
    }


def target():
    return {
        "kind": "object",
        "ref": "door-ref",
        "rawId": 1535,
        "effectiveId": 1535,
        "effectiveName": "Door",
        "effectiveActions": ["Open"],
        "worldPoint": {"worldX": 3207, "worldY": 3227, "plane": 1},
        "distance": 2,
        "onScreen": True,
        "menuActionAvailable": True,
        "geometry": {"aimPoint": {"x": 250, "y": 270}, "clickbox": {"x": 240, "y": 260, "w": 30, "h": 30}},
    }


def staircase_target():
    item = target()
    item.update(
        {
            "ref": "stair-ref",
            "rawId": 16672,
            "effectiveId": 16672,
            "effectiveName": "Staircase",
            "effectiveActions": ["Climb", "Climb-up", "Climb-down"],
            "worldPoint": {"worldX": 3204, "worldY": 3207, "plane": 1},
        }
    )
    return item


def snapshot(elapsed=1.5, *, include_menu=True):
    return {
        "elapsed_seconds": elapsed,
        "latest_tick": 10,
        "latest_export_sequence": 100,
        "player_world_point": {"worldX": 3206, "worldY": 3227, "plane": 1},
        "hover": menu_sample() if include_menu else {},
        "menu": menu_sample() if include_menu else {},
        "nearby_objects": [target()],
        "route_objects": [],
        "nearby_npcs": [],
        "raw_event": {"high_value_fields": {"player": {"animation": -1}}},
    }


def staircase_snapshot(elapsed, sample):
    return {
        "elapsed_seconds": elapsed,
        "latest_tick": int(elapsed * 10),
        "latest_export_sequence": int(elapsed * 100),
        "player_world_point": {"worldX": 3204, "worldY": 3207, "plane": 1},
        "hover": sample,
        "menu": sample,
        "nearby_objects": [staircase_target()],
        "route_objects": [],
        "nearby_npcs": [],
        "raw_event": {"high_value_fields": {"player": {"animation": -1}}},
    }


class MenuInteractionModelTest(unittest.TestCase):
    def test_normalizes_open_menu_snapshot_with_bounds_and_rows(self):
        normalized = menu_interaction_model.normalize_menu_snapshot({"hover": menu_sample()})
        self.assertTrue(normalized["isOpen"])
        self.assertEqual(normalized["bounds"]["x"], 500.0)
        self.assertEqual(len(normalized["rowsVisualOrder"]), 3)

    def test_computes_row_bounds(self):
        row_bounds = menu_interaction_model.compute_row_bounds(menu_sample(), 0)
        self.assertIsNotNone(row_bounds)
        self.assertEqual(row_bounds["x"], 500.0)
        self.assertGreater(row_bounds["y"], 300.0)

    def test_maps_click_inside_row_to_selected_row(self):
        normalized = menu_interaction_model.normalize_menu_snapshot({"hover": menu_sample()})
        selection = menu_interaction_model.resolve_menu_selection(click(), normalized)
        self.assertEqual(selection["selectedOption"], "Open")
        self.assertTrue(selection["insideRowBounds"])

    def test_maps_click_outside_rows_as_no_selection(self):
        normalized = menu_interaction_model.normalize_menu_snapshot({"hover": menu_sample()})
        selection = menu_interaction_model.resolve_menu_selection(click(x=50, y=50), normalized)
        self.assertIsNone(selection["selectedOption"])
        self.assertIn("menu_row_unresolved", selection["warnings"])

    def test_links_menu_row_to_target(self):
        row = menu_interaction_model.normalize_menu_snapshot({"hover": menu_sample()})["rowsVisualOrder"][0]
        self.assertEqual(row["linkedTarget"]["name"], "Door")
        self.assertEqual(row["linkedTarget"]["action"], "Open")

    def test_classifier_right_click_menu_open(self):
        rows, _summary = input_action_classifier.classify_input_actions([click(button="right")], [snapshot()])
        self.assertEqual(rows[0]["classification"], "right_click_menu_open")

    def test_classifier_left_click_inside_row_selection(self):
        events = [click(1, button="right", elapsed=1.0), click(2, button="left", elapsed=1.8)]
        rows, _summary = input_action_classifier.classify_input_actions(events, [snapshot(1.2), snapshot(2.0)])
        self.assertEqual(rows[1]["classification"], "menu_selection_click")
        self.assertEqual(rows[1]["selectedOption"], "Open")
        self.assertTrue(rows[1]["insideMenuRowBounds"])

    def test_menu_selection_attaches_selected_row_fields(self):
        events = [click(1, button="right", elapsed=1.0), click(2, button="left", elapsed=1.8)]
        rows, _summary = input_action_classifier.classify_input_actions(events, [snapshot(1.2), snapshot(2.0)])
        self.assertIn("menuSelection", rows[1])
        self.assertEqual(rows[1]["linkedGameTarget"]["name"], "Door")

    def test_target_quality_separates_menu_row_geometry(self):
        events = [click(1, button="right", elapsed=1.0), click(2, button="left", elapsed=1.8)]
        classes, _summary = input_action_classifier.classify_input_actions(events, [snapshot(1.2), snapshot(2.0)])
        quality = target_match_quality.score_target_match(events[1], classes[1], None, snapshot(), snapshot(2.2), [snapshot(), snapshot(2.2)])
        self.assertEqual(quality["menuSelectionQuality"]["rowGeometryQuality"], "strong")
        self.assertIn("gameTargetQuality", quality)

    def test_door_menu_selection_strong_without_object_clickbox_proximity(self):
        events = [click(1, button="right", elapsed=1.0), click(2, button="left", elapsed=1.8)]
        classes, _summary = input_action_classifier.classify_input_actions(events, [snapshot(1.2), snapshot(2.0)])
        quality = target_match_quality.score_target_match(events[1], classes[1], None, snapshot(), snapshot(2.2), [snapshot(), snapshot(2.2)])
        self.assertEqual(quality["quality"], "strong")
        self.assertIn("object_clickbox_proximity_not_required_for_menu_row_selection", quality["reasons"])

    def test_joiner_writes_menu_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            recording.mkdir()
            write_jsonl(recording / "events.jsonl", [{"event_type": "source_snapshot", "elapsed_seconds": 1.2, "latest_tick": 10, "high_value_fields": {"hover": menu_sample(), "menu": menu_sample(), "nearby_objects": [target()], "player": {"worldPoint": {"worldX": 3206, "worldY": 3227, "plane": 1}}}}])
            write_jsonl(recording / "input_events.jsonl", [click(1, button="right", elapsed=1.0), click(2, button="left", elapsed=1.8)])
            result = input_trace_joiner.analyze_recording(recording, write=True, include_mapping=True)
            self.assertIn("menu_interaction_summary", result)
            self.assertTrue((recording / "menu_interactions.jsonl").exists())
            self.assertTrue((recording / "menu_interaction_summary.json").exists())

    def test_analyzer_writes_menu_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            recording.mkdir()
            write_jsonl(
                recording / "events.jsonl",
                [
                    {"event_type": "recording_start", "elapsed_seconds": 0, "session_id": "s1"},
                    {"event_type": "source_snapshot", "elapsed_seconds": 1.2, "latest_tick": 10, "high_value_fields": {"hover": menu_sample(), "menu": menu_sample(), "nearby_objects": [target()], "player": {"worldPoint": {"worldX": 3206, "worldY": 3227, "plane": 1}}}},
                    {"event_type": "recording_stop", "elapsed_seconds": 3, "duration_seconds": 3},
                ],
            )
            write_jsonl(recording / "input_events.jsonl", [click(1, button="right", elapsed=1.0), click(2, button="left", elapsed=1.8)])
            summary = analyze_manual_recording.update_outputs(recording)
            self.assertIn("menu_interaction_summary", summary)

    def test_ui_check_command_includes_menu_interactions(self):
        command = telemetry_ui.build_analyzer_command(Path("recordings/test"))
        self.assertIn("--menu-interactions", command)
        self.assertIn("--menu-row-diagnostics", command)

    def test_snapshot_buffer_recovers_first_selection_row_bounds(self):
        snapshots = [
            staircase_snapshot(2.0, zero_bounds_staircase_sample()),
            staircase_snapshot(3.5, zero_bounds_staircase_sample()),
            staircase_snapshot(5.1, bounded_staircase_sample()),
            staircase_snapshot(6.0, cancel_only_sample()),
        ]
        buffer = menu_interaction_model.build_menu_snapshot_buffer(snapshots)
        event = click(seq=4, button="left", x=559, y=320, elapsed=3.484)
        fallback = {"name": "Staircase", "action": "Climb", "rawId": 16672, "effectiveId": 16672}
        pairing = menu_interaction_model.select_menu_snapshot_for_selection(event, buffer, fallback_target=fallback)
        selection = pairing["selection"]
        self.assertEqual(selection["selectedOption"], "Climb")
        self.assertEqual(selection["selectedTarget"], "Staircase")
        self.assertIsNotNone(selection["rowBounds"])
        self.assertEqual(selection["rowGeometrySource"], "option_target_match")
        self.assertGreaterEqual(selection["candidateSnapshotCount"], 3)

    def test_stale_cancel_only_snapshot_does_not_beat_matching_option_target_snapshot(self):
        snapshots = [
            staircase_snapshot(3.5, zero_bounds_staircase_sample()),
            staircase_snapshot(5.1, bounded_staircase_sample()),
            staircase_snapshot(5.8, cancel_only_sample()),
        ]
        buffer = menu_interaction_model.build_menu_snapshot_buffer(snapshots)
        event = click(seq=4, button="left", x=559, y=320, elapsed=3.484)
        fallback = {"name": "Staircase", "action": "Climb", "rawId": 16672, "effectiveId": 16672}
        selection = menu_interaction_model.select_menu_snapshot_for_selection(event, buffer, fallback_target=fallback)["selection"]
        self.assertEqual(selection["selectedOption"], "Climb")
        self.assertNotEqual(selection["selectedOption"], "Cancel")

    def test_joiner_recovers_climb_staircase_first_selection_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            recording.mkdir()
            write_jsonl(
                recording / "events.jsonl",
                [
                    {"event_type": "source_snapshot", "elapsed_seconds": 2.0, "latest_tick": 20, "latest_export_sequence": 200, "high_value_fields": {"hover": zero_bounds_staircase_sample(), "menu": zero_bounds_staircase_sample(), "nearby_objects": [staircase_target()], "player": {"worldPoint": {"worldX": 3204, "worldY": 3207, "plane": 1}}}},
                    {"event_type": "source_snapshot", "elapsed_seconds": 5.1, "latest_tick": 51, "latest_export_sequence": 510, "high_value_fields": {"hover": bounded_staircase_sample(), "menu": bounded_staircase_sample(), "nearby_objects": [staircase_target()], "player": {"worldPoint": {"worldX": 3204, "worldY": 3207, "plane": 1}}}},
                    {"event_type": "source_snapshot", "elapsed_seconds": 5.8, "latest_tick": 58, "latest_export_sequence": 580, "high_value_fields": {"hover": cancel_only_sample(), "menu": cancel_only_sample(), "nearby_objects": [staircase_target()], "player": {"worldPoint": {"worldX": 3204, "worldY": 3207, "plane": 1}}}},
                ],
            )
            write_jsonl(recording / "input_events.jsonl", [click(seq=4, button="left", x=559, y=320, elapsed=3.484)])
            result = input_trace_joiner.analyze_recording(recording, write=True, include_mapping=True)
            menu_summary = result["menu_interaction_summary"]
            self.assertEqual(menu_summary["menuSelectionsWithRowGeometryCount"], 1)
            self.assertEqual(menu_summary["menuSelectionsMissingRowGeometryCount"], 0)
            self.assertIn(menu_summary["examples"][0]["rowGeometrySource"], {"direct_row_hit", "option_target_match"})


if __name__ == "__main__":
    unittest.main()

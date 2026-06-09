import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import analyze_manual_recording
import context_service
import telemetry_ui
import traversal_lifecycle


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def obj(name="Staircase", action="Climb-up", *, x=3201, y=3200, plane=0, ident=16672):
    return {
        "kind": "object",
        "rawId": ident,
        "effectiveId": ident,
        "effectiveName": name,
        "effectiveActions": [action],
        "worldPoint": {"worldX": x, "worldY": y, "plane": plane},
        "distance": 1,
        "onScreen": True,
    }


def snap(elapsed, tick, x, y, plane, *, objects=None, route_objects=None, bank=None, inventory=None):
    return {
        "event_type": "source_snapshot",
        "elapsed_seconds": elapsed,
        "latest_tick": tick,
        "high_value_fields": {
            "latest_tick": tick,
            "player": {"worldPoint": {"worldX": x, "worldY": y, "plane": plane}},
            "nearby_objects": objects or [],
            "route_objects": route_objects or [],
            "bank": bank or {},
            "inventory": inventory or {},
        },
    }


def target_row(seq=1, *, name="Staircase", action="Climb-up", quality="strong", classification="object_action_click", x=3201, y=3200, plane=0):
    return {
        "schema": "target_match_quality.v1",
        "eventSeq": seq,
        "classification": classification,
        "quality": quality,
        "score": {"strong": 0.95, "medium": 0.68, "weak": 0.35}.get(quality, 0.2),
        "matchedTarget": {
            "kind": "object",
            "name": name,
            "action": action,
            "effectiveId": 16672,
            "world": {"worldX": x, "worldY": y, "plane": plane},
        },
        "warnings": [],
    }


def joined(seq=1, *, elapsed=1.0, tick=10, classification="object_action_click"):
    return {
        "inputEvent": {"event_seq": seq, "elapsed_seconds": elapsed, "nearest_tick": tick},
        "classification": {"classification": classification},
        "coordinateTransformUsed": "client_identity",
    }


def menu(seq=1, *, option="Climb-up", target="Staircase", row_geometry=True):
    return {
        "eventSeq": seq,
        "option": option,
        "target": target,
        "selectedRowIndex": 0,
        "rowBoundsPresent": row_geometry,
        "insideRowBounds": row_geometry,
        "rowGeometrySource": "direct_row_hit" if row_geometry else "fallback_target_link",
        "selectedSnapshotId": "menu_001",
        "menuSelection": {
            "selectedOption": option,
            "selectedTarget": target,
            "selectedRowIndex": 0,
            "rowBounds": {"x": 100, "y": 100, "width": 120, "height": 16} if row_geometry else None,
            "insideRowBounds": row_geometry,
            "rowGeometrySource": "direct_row_hit" if row_geometry else "fallback_target_link",
            "selectedSnapshotId": "menu_001",
        },
    }


class TraversalLifecycleTest(unittest.TestCase):
    def test_extracts_walk_movement_from_world_positions(self):
        lifecycle = traversal_lifecycle.analyze_data(events=[snap(0, 1, 3200, 3200, 0), snap(1, 2, 3203, 3200, 0)])
        self.assertGreaterEqual(len(lifecycle["movement"]["positionChanges"]), 1)
        self.assertGreater(lifecycle["movement"]["distanceApprox"], 0)

    def test_extracts_object_action_step_from_target_quality(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[snap(0, 1, 3200, 3200, 0), snap(2, 2, 3200, 3200, 1)],
            joined_input_telemetry=[joined()],
            target_match_quality=[target_row()],
        )
        self.assertTrue(any(step["type"] == "object_action" for step in lifecycle["steps"]))

    def test_extracts_menu_selection_with_row_geometry(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[snap(0, 1, 3200, 3200, 0), snap(2, 2, 3200, 3200, 1)],
            joined_input_telemetry=[joined(classification="menu_selection_click")],
            target_match_quality=[target_row(classification="menu_selection_click")],
            menu_interactions=[menu()],
        )
        action_step = next(step for step in lifecycle["steps"] if step.get("inputEventSeq") == 1)
        self.assertTrue(action_step["menuSelection"]["rowBoundsPresent"])

    def test_climb_up_followed_by_plane_change_is_success(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[snap(0, 1, 3200, 3200, 0), snap(2, 2, 3200, 3200, 1)],
            joined_input_telemetry=[joined()],
            target_match_quality=[target_row(action="Climb-up", plane=0)],
        )
        action_step = next(step for step in lifecycle["steps"] if step.get("inputEventSeq") == 1)
        self.assertEqual(action_step["result"], "success")
        self.assertTrue(action_step["postcondition"]["planeChanged"])

    def test_climb_down_followed_by_plane_change_is_success(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[snap(0, 1, 3200, 3200, 2), snap(2, 2, 3200, 3200, 1)],
            joined_input_telemetry=[joined()],
            target_match_quality=[target_row(action="Climb-down", plane=2)],
        )
        action_step = next(step for step in lifecycle["steps"] if step.get("inputEventSeq") == 1)
        self.assertEqual(action_step["result"], "success")

    def test_ladder_without_plane_change_warns_partial(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[snap(0, 1, 3200, 3200, 0), snap(2, 2, 3200, 3200, 0)],
            joined_input_telemetry=[joined()],
            target_match_quality=[target_row(name="Ladder", action="Climb-up")],
        )
        action_step = next(step for step in lifecycle["steps"] if step.get("inputEventSeq") == 1)
        self.assertEqual(action_step["result"], "partial")
        self.assertTrue(action_step["warnings"])

    def test_door_open_followed_by_position_change_is_success(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[snap(0, 1, 3200, 3200, 0), snap(2, 2, 3202, 3200, 0)],
            joined_input_telemetry=[joined()],
            target_match_quality=[target_row(name="Door", action="Open", x=3201, y=3200)],
        )
        action_step = next(step for step in lifecycle["steps"] if step.get("inputEventSeq") == 1)
        self.assertEqual(action_step["result"], "success")

    def test_bank_action_without_bank_state_is_medium_warn(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[snap(0, 1, 3200, 3200, 0, objects=[obj("Bank booth", "Bank")]), snap(2, 2, 3200, 3200, 0)],
            joined_input_telemetry=[joined()],
            target_match_quality=[target_row(name="Bank booth", action="Bank", quality="medium")],
        )
        action_step = next(step for step in lifecycle["steps"] if step.get("inputEventSeq") == 1)
        self.assertEqual(action_step["result"], "partial")
        self.assertIn("bank state/widget proof missing", action_step["warnings"])

    def test_start_end_world_and_plane_are_summarized(self):
        lifecycle = traversal_lifecycle.analyze_data(events=[snap(0, 1, 3200, 3200, 0), snap(2, 2, 3205, 3206, 1)])
        self.assertEqual(lifecycle["start"]["world"]["plane"], 0)
        self.assertEqual(lifecycle["end"]["world"]["plane"], 1)

    def test_area_label_woodcutting_from_tree_context(self):
        lifecycle = traversal_lifecycle.analyze_data(events=[snap(0, 1, 3195, 3244, 0, objects=[obj("Tree", "Chop down")])])
        self.assertEqual(lifecycle["start"]["areaLabel"], "woodcutting_area")

    def test_area_label_bank_from_bank_objects(self):
        lifecycle = traversal_lifecycle.analyze_data(events=[snap(0, 1, 3209, 3220, 0, objects=[obj("Bank booth", "Bank")])])
        self.assertEqual(lifecycle["start"]["areaLabel"], "bank_area")

    def test_multi_action_recording_does_not_fail(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[snap(0, 1, 3200, 3200, 0), snap(2, 2, 3201, 3200, 1), snap(4, 3, 3202, 3200, 2)],
            joined_input_telemetry=[joined(1, elapsed=1), joined(2, elapsed=3)],
            target_match_quality=[target_row(1, action="Climb-up"), target_row(2, action="Climb-up", plane=1)],
        )
        self.assertNotEqual(lifecycle["status"], "FAIL")
        self.assertGreaterEqual(lifecycle["stepCount"], 2)

    def test_strong_target_quality_increases_step_confidence(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[snap(0, 1, 3200, 3200, 0), snap(2, 2, 3200, 3200, 1)],
            joined_input_telemetry=[joined()],
            target_match_quality=[target_row(quality="strong")],
        )
        action_step = next(step for step in lifecycle["steps"] if step.get("inputEventSeq") == 1)
        self.assertGreaterEqual(action_step["confidence"], 0.9)

    def test_missing_row_geometry_does_not_fail_with_strong_postcondition(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[snap(0, 1, 3200, 3200, 0), snap(2, 2, 3200, 3200, 1)],
            joined_input_telemetry=[joined(classification="menu_selection_click")],
            target_match_quality=[target_row(classification="menu_selection_click")],
            menu_interactions=[menu(row_geometry=False)],
            summaries={"menu_interaction_summary": {"menuSelectionsMissingRowGeometryCount": 1}},
        )
        self.assertNotEqual(lifecycle["status"], "FAIL")

    def test_climb_action_and_plane_change_group_into_one_segment(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[snap(0, 1, 3200, 3200, 0), snap(2, 2, 3200, 3200, 1)],
            joined_input_telemetry=[joined()],
            target_match_quality=[target_row(action="Climb-up", plane=0)],
        )
        self.assertEqual(lifecycle["rawStepCount"], 2)
        self.assertEqual(lifecycle["groupedStepCount"], 1)
        self.assertEqual(lifecycle["partialStepCount"], 0)
        step = lifecycle["steps"][0]
        self.assertEqual(step["type"], "object_action")
        self.assertIn("plane_transition", [item["type"] for item in step["supportingEvidence"]])
        self.assertTrue(any(segment["segmentType"] == "stair_transition" for segment in lifecycle["routeSegments"]))

    def test_door_open_movement_is_route_segment(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[snap(0, 1, 3200, 3200, 0), snap(2, 2, 3202, 3200, 0)],
            joined_input_telemetry=[joined()],
            target_match_quality=[target_row(name="Door", action="Open", x=3201, y=3200)],
        )
        door_segments = [segment for segment in lifecycle["routeSegments"] if segment["segmentType"] == "door_transition"]
        self.assertEqual(len(door_segments), 1)
        self.assertEqual(door_segments[0]["postcondition"]["result"], "success")

    def test_weak_unrelated_click_becomes_review_evidence(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[snap(0, 1, 3208, 3220, 0), snap(2, 2, 3196, 3242, 0, objects=[obj("Tree", "Chop down")])],
            joined_input_telemetry=[joined()],
            target_match_quality=[target_row(name="Table", action="Examine", quality="weak")],
        )
        self.assertEqual(lifecycle["reviewEvidenceCount"], 1)
        self.assertIn("weak", lifecycle["reviewEvidence"][0]["reviewReason"])

    def test_bank_context_does_not_create_partial_route_step_when_route_progress_exists(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[
                snap(0, 1, 3208, 3220, 2, objects=[obj("Bank booth", "Bank")]),
                snap(2, 2, 3206, 3211, 2, objects=[obj("Bank booth", "Bank")]),
                snap(5, 3, 3193, 3243, 0, objects=[obj("Tree", "Chop down")]),
                snap(6, 4, 3194, 3244, 0, objects=[obj("Tree", "Chop down")]),
                snap(7, 5, 3195, 3245, 0, objects=[obj("Tree", "Chop down")]),
            ],
            joined_input_telemetry=[joined(classification="menu_selection_click")],
            target_match_quality=[target_row(name="Bank table", action=None, quality="strong", classification="menu_selection_click")],
            menu_interactions=[menu(option=None, target="Bank table", row_geometry=False)],
        )
        self.assertEqual(lifecycle["routeName"], "Bank_to_Woodcutting_area")
        self.assertEqual(lifecycle["partialStepCount"], 0)
        self.assertEqual(lifecycle["reviewEvidenceCount"], 1)

    def test_raw_and_grouped_steps_preserve_counts(self):
        lifecycle = traversal_lifecycle.analyze_data(
            events=[snap(0, 1, 3200, 3200, 0), snap(2, 2, 3200, 3200, 1)],
            joined_input_telemetry=[joined()],
            target_match_quality=[target_row(action="Climb-up", plane=0)],
        )
        self.assertGreater(lifecycle["rawStepCount"], lifecycle["groupedStepCount"])
        self.assertEqual(lifecycle["grouping"]["partialStepsResolved"], 0)
        self.assertEqual(lifecycle["routeSegmentCount"], len(lifecycle["routeSegments"]))

    def test_analyzer_writes_traversal_lifecycle_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            recording.mkdir()
            write_jsonl(recording / "events.jsonl", [snap(0, 1, 3200, 3200, 0), snap(2, 2, 3203, 3200, 0)])
            summary = analyze_manual_recording.update_outputs(recording)
            self.assertIn("traversal_lifecycle", summary)
            self.assertTrue((recording / "traversal_lifecycle.json").exists())

    def test_context_service_summary_includes_traversal_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = root / "recording"
            recording.mkdir()
            lifecycle = traversal_lifecycle.analyze_data(events=[snap(0, 1, 3200, 3200, 0), snap(1, 2, 3201, 3200, 0)])
            (recording / "summary.json").write_text(json.dumps({"traversal_lifecycle": lifecycle}), encoding="utf-8")
            payload = context_service.recording_summary_payload("recording", root=root)
            self.assertEqual(payload["traversalStatus"], lifecycle["status"])
            self.assertIn("traversalSummary", payload)
            self.assertEqual(payload["routeSegmentCount"], lifecycle["routeSegmentCount"])
            self.assertEqual(payload["reviewEvidenceCount"], lifecycle["reviewEvidenceCount"])

    def test_ui_route_preset_includes_traversal_lifecycle_flag(self):
        config = telemetry_ui.config_for_preset(telemetry_ui.default_config(), telemetry_ui.PRESET_ROUTE)
        command = telemetry_ui.build_analyzer_command(Path("recordings/test"), config)
        self.assertIn("--traversal-lifecycle", command)
        self.assertIn("--group-traversal-steps", command)
        self.assertTrue(config["menu_capture_burst"])


if __name__ == "__main__":
    unittest.main()

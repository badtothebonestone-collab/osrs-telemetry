import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import analyze_manual_recording
import camera_behavior
import input_trace_joiner
import target_match_quality
import vm_mouse_arduino_mapper


DEFAULT_TARGET = object()


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def click(seq=1, *, x=100, y=100, elapsed=1.0, nearest_tick=10):
    return {
        "kind": "click",
        "event_seq": seq,
        "elapsed_seconds": elapsed,
        "nearest_tick": nearest_tick,
        "nearest_export_sequence": nearest_tick * 10,
        "button": "left",
        "client_x": x,
        "client_y": y,
        "region": "viewport",
        "runelite_window_match": True,
    }


def target(**updates):
    base = {
        "ref": "0:3200:3200:object",
        "kind": "object",
        "rawId": 100,
        "effectiveId": 100,
        "effectiveName": "Tree",
        "effectiveActions": ["Chop down"],
        "worldPoint": {"worldX": 3201, "worldY": 3200, "plane": 0},
        "distance": 1,
        "onScreen": True,
        "geometry": {
            "available": True,
            "aimPoint": {"x": 100, "y": 100},
            "clickbox": {"x": 90, "y": 90, "w": 30, "h": 30},
            "canvas": {"x": 100, "y": 100},
        },
        "menuActionAvailable": True,
    }
    base.update(updates)
    return base


def classification(seq=1, *, label="object_action_click", target_payload=DEFAULT_TARGET, eligible=True, reasons=None):
    target_payload = target() if target_payload is DEFAULT_TARGET else target_payload
    return {
        "clickId": f"click_{seq:03d}",
        "eventSeq": seq,
        "eventKind": "click",
        "classification": label,
        "targetRelativeEligible": eligible,
        "eligibleForTargetMatching": eligible,
        "targetContext": {
            "matchedTarget": target_payload,
            "targetName": target_payload.get("effectiveName") or target_payload.get("name") if isinstance(target_payload, dict) else None,
            "targetAction": (target_payload.get("effectiveActions") or target_payload.get("actions") or [None])[0] if isinstance(target_payload, dict) else None,
        },
        "reasons": reasons or ["matched_object_target"],
    }


def click_analysis(click_event=None, target_payload=None):
    click_event = click_event or click()
    target_payload = target_payload if target_payload is not None else target()
    return input_trace_joiner.click_analysis(click_event, snapshot(target_payload=target_payload), snapshot(target_payload=target_payload), [snapshot(target_payload=target_payload)])


def snapshot(elapsed=0.5, *, tick=10, plane=0, target_payload=None, player=None):
    target_payload = target_payload if target_payload is not None else target()
    return {
        "elapsed_seconds": elapsed,
        "latest_tick": tick,
        "latest_export_sequence": tick * 10,
        "player_world_point": player or {"worldX": 3200, "worldY": 3200, "plane": plane},
        "hover": {"topOption": "Chop down", "topTarget": "Tree"},
        "menu": {"menuOpen": False},
        "nearby_objects": [target_payload] if target_payload else [],
        "route_objects": [],
        "nearby_npcs": [],
        "raw_event": {"high_value_fields": {"player": {"animation": -1}}},
    }


class TargetMatchQualityTest(unittest.TestCase):
    def test_clickbox_containment_action_match_is_strong(self):
        event = click()
        quality = target_match_quality.score_target_match(event, classification(), click_analysis(event), snapshot(), snapshot(), [snapshot()])
        self.assertEqual(quality["quality"], "strong")
        self.assertTrue(quality["geometry"]["insideClickbox"])

    def test_aim_distance_under_12_is_strong(self):
        event = click(x=106, y=105)
        t = target(geometry={"available": True, "aimPoint": {"x": 100, "y": 100}, "clickbox": None, "canvas": {"x": 100, "y": 100}})
        quality = target_match_quality.score_target_match(event, classification(target_payload=t), click_analysis(event, t), snapshot(target_payload=t), snapshot(target_payload=t), [snapshot(target_payload=t)])
        self.assertEqual(quality["quality"], "strong")
        self.assertLessEqual(quality["geometry"]["distanceFromAimPointPx"], 12)

    def test_menu_selection_with_menu_target_is_strong(self):
        event = click(x=500, y=380)
        t = target(effectiveName="Door", effectiveActions=["Open"], geometry={"available": True, "aimPoint": {"x": 250, "y": 270}, "clickbox": None})
        before = snapshot(target_payload=t)
        before["hover"] = {"topOption": "Open", "topTarget": "Door"}
        row = target_match_quality.score_target_match(
            event,
            {
                **classification(label="menu_selection_click", target_payload=t, reasons=["left_click_after_recent_right_click_or_open_menu"]),
                "menuSelection": {
                    "selectedRowIndex": 0,
                    "selectedOption": "Open",
                    "selectedTarget": "Door",
                    "rowBounds": None,
                    "insideRowBounds": None,
                    "linkedGameTarget": {"kind": "object", "name": "Door", "action": "Open", "rawId": 100},
                    "warnings": ["menu_row_bounds_missing"],
                },
            },
            click_analysis(event, t),
            before,
            snapshot(target_payload=t),
            [before],
        )
        self.assertEqual(row["quality"], "strong")
        self.assertTrue(row["evidence"]["hoverConfirmed"])
        self.assertIn("menuSelectionQuality", row)
        self.assertIn("menu_row_geometry_missing", row["warnings"])
        self.assertIn("object_clickbox_proximity_not_required_for_menu_row_selection", row["reasons"])

    def test_hover_menu_target_wins_over_conflicting_fallback_target(self):
        event = click(x=114, y=358)
        fallback = target(effectiveName="Gate", effectiveActions=["Open"], rawId=12986, effectiveId=12986)
        before = snapshot(target_payload=fallback)
        before["hover"] = {"topOption": "Chop down", "topTarget": "<col=ffff>Tree"}
        after = snapshot(2.0, tick=11, target_payload=fallback)
        after["raw_event"] = {"high_value_fields": {"player": {"animation": 879}}}
        row = target_match_quality.score_target_match(
            event,
            {
                **classification(label="menu_selection_click", target_payload=fallback, reasons=["left_click_after_recent_right_click_or_open_menu"]),
                "menuContext": {
                    "hoverOption": "Chop down",
                    "hoverTarget": "<col=ffff>Tree",
                    "menuOpenBefore": True,
                    "menuOpenAfter": False,
                },
                "menuSelection": {
                    "selectedRowIndex": 0,
                    "selectedOption": "Open",
                    "selectedTarget": "Gate",
                    "rowBounds": None,
                    "insideRowBounds": None,
                    "warnings": ["menu_row_bounds_missing", "selection_inferred_from_game_target_without_row_geometry"],
                },
            },
            click_analysis(event, fallback),
            before,
            after,
            [before, after],
        )
        self.assertEqual(row["quality"], "strong")
        self.assertEqual(row["matchedTarget"]["name"], "Tree")
        self.assertEqual(row["matchedTarget"]["action"], "Chop down")
        self.assertEqual(row["matchedTarget"]["kind"], "menu_hover")
        self.assertEqual(row["targetAssociation"]["associationMethod"], "hover_menu_identity")
        self.assertGreaterEqual(len(row["targetAssociation"]["rejectedCandidates"]), 1)
        self.assertIn("menu_hover_target_used", row["reasons"])
        self.assertNotIn("climb_target_without_plane_change_in_window", row["warnings"])

    def test_object_action_hover_tree_uses_identity_matching_tree_geometry(self):
        event = click(seq=49, x=521, y=212, elapsed=4.9)
        fallback = target(
            ref="0:3185:3268:41:76:WALL_OBJECT:12988:13619045929:8",
            kind="route",
            effectiveName="Gate",
            effectiveActions=["Close"],
            rawId=12988,
            effectiveId=12988,
            geometry={"available": True, "aimPoint": {"x": 342, "y": 4}, "clickbox": {"x": 333, "y": -8, "w": 16, "h": 13}, "canvas": {"x": 342, "y": 4}},
        )
        tree_geometry = target(
            ref="0:3200:3246:56:54:GAME_OBJECT:1278:1340218168:0",
            kind="object",
            effectiveName="Tree",
            effectiveActions=[],
            rawId=1278,
            effectiveId=1278,
            worldPoint={"worldX": 3200, "worldY": 3246, "plane": 0},
            distance=5,
            onScreen=True,
            geometry={"available": True, "aimPoint": {"x": 489, "y": 234}, "clickbox": None, "canvas": None},
        )
        before = snapshot(elapsed=4.8, target_payload=fallback)
        before["hover"] = {"topOption": "Chop down", "topTarget": "<col=ffff>Tree"}
        before["nearby_objects"] = [fallback, tree_geometry]
        after = snapshot(5.2, tick=11, target_payload=fallback)
        after["raw_event"] = {"high_value_fields": {"player": {"animation": 879}}}
        after["nearby_objects"] = [fallback, tree_geometry]
        row = target_match_quality.score_target_match(
            event,
            {
                **classification(seq=49, label="object_action_click", target_payload=fallback, reasons=["matched_object_target"]),
                "menuContext": {
                    "hoverOption": "Chop down",
                    "hoverTarget": "<col=ffff>Tree",
                    "menuOpenBefore": False,
                    "menuOpenAfter": False,
                },
            },
            click_analysis(event, fallback),
            before,
            after,
            [before, after],
        )

        self.assertEqual(row["matchedTarget"]["name"], "Tree")
        self.assertEqual(row["matchedTarget"]["action"], "Chop down")
        self.assertEqual(row["matchedTarget"]["rawId"], 1278)
        self.assertEqual(row["targetAssociation"]["associationMethod"], "hover_menu_identity")
        self.assertEqual(row["targetAssociation"]["intendedTargetClass"], "woodcutting_target")
        self.assertEqual(row["geometry"]["clickboxAvailable"], False)
        self.assertEqual(row["geometry"]["aimPoint"], {"x": 489.0, "y": 234.0})
        self.assertEqual(row["geometry"]["distanceFromAimPointPx"], 38.833)
        self.assertNotIn("target_geometry_missing", row["warnings"])
        self.assertIn("target_identity_conflicting_geometry_rejected", row["warnings"])
        self.assertIn("candidate_action_missing_but_woodcutting_identity_matches", row["geometry"]["geometryMatchReasons"])
        rejected = row["targetAssociation"]["rejectedCandidates"]
        self.assertTrue(any(item.get("name") == "Gate" and item.get("action") == "Close" for item in rejected))
        reasons = {reason for item in rejected for reason in item.get("reasons", [])}
        self.assertIn("target_name_conflict", reasons)
        self.assertIn("target_action_conflict", reasons)

    def test_hover_tree_without_tree_geometry_warns_without_using_gate(self):
        event = click(seq=50, x=521, y=212, elapsed=4.9)
        fallback = target(
            ref="0:3185:3268:41:76:WALL_OBJECT:12988:13619045929:8",
            kind="route",
            effectiveName="Gate",
            effectiveActions=["Close"],
            rawId=12988,
            effectiveId=12988,
            geometry={"available": True, "aimPoint": {"x": 342, "y": 4}, "clickbox": {"x": 333, "y": -8, "w": 16, "h": 13}, "canvas": {"x": 342, "y": 4}},
        )
        before = snapshot(elapsed=4.8, target_payload=fallback)
        before["hover"] = {"topOption": "Chop down", "topTarget": "<col=ffff>Tree"}
        after = snapshot(5.2, tick=11, target_payload=fallback)
        after["raw_event"] = {"high_value_fields": {"player": {"animation": 879}}}
        row = target_match_quality.score_target_match(
            event,
            {
                **classification(seq=50, label="object_action_click", target_payload=fallback, reasons=["matched_object_target"]),
                "menuContext": {
                    "hoverOption": "Chop down",
                    "hoverTarget": "<col=ffff>Tree",
                    "menuOpenBefore": False,
                    "menuOpenAfter": False,
                },
            },
            click_analysis(event, fallback),
            before,
            after,
            [before, after],
        )

        self.assertEqual(row["matchedTarget"]["name"], "Tree")
        self.assertEqual(row["matchedTarget"]["action"], "Chop down")
        self.assertEqual(row["matchedTarget"]["kind"], "menu_hover")
        self.assertIsNone(row["geometry"]["aimPoint"])
        self.assertIn("target_geometry_missing", row["warnings"])
        self.assertTrue(any(item.get("name") == "Gate" for item in row["targetAssociation"]["rejectedCandidates"]))

    def test_hover_linked_tree_candidate_ranks_before_nearby_tree(self):
        event = click(seq=51, x=120, y=120, elapsed=2.0)
        linked = target(
            ref="linked-tree",
            effectiveName="Tree",
            effectiveActions=[],
            rawId=1276,
            effectiveId=1276,
            localPoint={"sceneX": 49, "sceneY": 52},
            geometry={"available": True, "aimPoint": {"x": 190, "y": 190}, "clickbox": None, "canvas": None},
        )
        nearest = target(
            ref="nearest-tree",
            effectiveName="Tree",
            effectiveActions=[],
            rawId=1278,
            effectiveId=1278,
            localPoint={"sceneX": 50, "sceneY": 52},
            geometry={"available": True, "aimPoint": {"x": 122, "y": 122}, "clickbox": None, "canvas": None},
        )
        before = snapshot(elapsed=1.9, target_payload=None)
        before["hover"] = {"topOption": "Chop down", "topTarget": "<col=ffff>Tree", "topIdentifier": 1276, "topParam0": 49, "topParam1": 52}
        before["nearby_objects"] = [nearest, linked]
        row = target_match_quality.score_target_match(
            event,
            {
                **classification(seq=51, label="object_action_click", target_payload=nearest, reasons=["matched_object_target"]),
                "menuContext": {"hoverOption": "Chop down", "hoverTarget": "<col=ffff>Tree"},
            },
            click_analysis(event, nearest),
            before,
            before,
            [before],
        )

        self.assertEqual(row["matchedTarget"]["ref"], "linked-tree")
        self.assertTrue(row["geometry"]["geometryHoverLinked"])
        self.assertIn("hover_ref_match", row["geometry"]["geometryMatchReasons"])

    def test_climb_plane_change_is_strong(self):
        event = click()
        t = target(effectiveName="Staircase", effectiveActions=["Climb-up"])
        before = snapshot(target_payload=t, plane=0)
        after = snapshot(2.0, tick=11, plane=1, target_payload=t)
        quality = target_match_quality.score_target_match(event, classification(target_payload=t), click_analysis(event, t), before, after, [before, after])
        self.assertEqual(quality["quality"], "strong")
        self.assertTrue(quality["postClickResult"]["planeChanged"])

    def test_identity_action_without_geometry_is_medium(self):
        event = click()
        t = target(geometry={"available": False, "aimPoint": None, "clickbox": None, "canvas": None})
        quality = target_match_quality.score_target_match(event, classification(target_payload=t), click_analysis(event, t), snapshot(target_payload=t), snapshot(target_payload=t), [snapshot(target_payload=t)])
        self.assertEqual(quality["quality"], "medium")
        self.assertIn("target_geometry_missing", quality["warnings"])

    def test_stale_telemetry_warns(self):
        event = click(elapsed=10.0, nearest_tick=30)
        before = snapshot(elapsed=1.0, tick=10)
        quality = target_match_quality.score_target_match(event, classification(), click_analysis(event), before, before, [before])
        self.assertIn(quality["quality"], {"weak", "medium"})
        self.assertIn("nearest_telemetry_snapshot_stale_or_unknown", quality["warnings"])

    def test_non_target_relative_is_unmatched(self):
        row = target_match_quality.score_target_match(click(), classification(label="camera_drag_release", eligible=False), None, snapshot(), snapshot(), [snapshot()])
        self.assertEqual(row["quality"], "unmatched")

    def test_camera_drag_release_not_scored_as_target_click(self):
        row = target_match_quality.score_target_match(click(), classification(label="camera_drag_release", eligible=False), None, snapshot(), snapshot(), [snapshot()])
        self.assertIn("not_target_relative_eligible", row["warnings"])

    def test_ui_control_click_not_scored_as_target_click(self):
        row = target_match_quality.score_target_match(click(), classification(label="ui_control_click", eligible=False), None, snapshot(), snapshot(), [snapshot()])
        self.assertEqual(row["quality"], "unmatched")

    def test_no_target_is_unmatched(self):
        row = target_match_quality.score_target_match(click(), classification(target_payload=None), None, snapshot(target_payload=None), snapshot(target_payload=None), [])
        self.assertEqual(row["quality"], "unmatched")

    def test_summary_counts(self):
        rows = [
            {"quality": "strong", "eventSeq": 1},
            {"quality": "medium", "eventSeq": 2},
            {"quality": "weak", "eventSeq": 3},
            {"quality": "unmatched", "eventSeq": 4},
        ]
        summary = target_match_quality.summarize_quality(rows)
        self.assertEqual(summary["qualityCounts"], {"strong": 1, "medium": 1, "weak": 1, "unmatched": 1})

    def test_summary_includes_click_landing_tolerance(self):
        rows = [
            {
                "quality": "strong",
                "eventSeq": 1,
                "classification": "object_action_click",
                "matchedTarget": {"name": "Tree", "action": "Chop down"},
                "geometry": {"insideClickbox": True, "clickboxAvailable": True, "distanceFromAimPointPx": 8},
                "warnings": [],
            },
            {
                "quality": "medium",
                "eventSeq": 2,
                "classification": "object_action_click",
                "matchedTarget": {"name": "Staircase", "action": "Climb-down"},
                "geometry": {"insideClickbox": False, "clickboxAvailable": True, "distanceFromAimPointPx": 116.103},
                "warnings": ["click_outside_clickbox"],
            },
            {
                "quality": "strong",
                "eventSeq": 3,
                "classification": "menu_selection_click",
                "matchedTarget": {"name": "Door", "action": "Open"},
                "geometry": {"insideClickbox": None, "clickboxAvailable": False, "distanceFromAimPointPx": None},
                "menuSelectionQuality": {"rowBounds": {"x": 1}, "insideRowBounds": True, "rowCenterDistancePx": 12},
                "warnings": [],
            },
        ]
        summary = target_match_quality.summarize_quality(rows)
        landing = summary["clickLandingSummary"]
        self.assertEqual(landing["clickboxCounts"]["inside"], 1)
        self.assertEqual(landing["clickboxCounts"]["outside"], 1)
        self.assertEqual(landing["clickboxCounts"]["unavailable"], 1)
        self.assertEqual(landing["aimDistanceBuckets"]["gt80"], 1)
        self.assertEqual(landing["menuRowCounts"]["inside"], 1)
        self.assertEqual(landing["imperfectButUsefulClickCount"], 1)

    def test_summary_includes_rejected_target_association_candidates(self):
        rows = [
            {
                "quality": "strong",
                "eventSeq": 49,
                "classification": "object_action_click",
                "matchedTarget": {"name": "Tree", "action": "Chop down"},
                "geometry": {"insideClickbox": None, "clickboxAvailable": False, "distanceFromAimPointPx": None},
                "warnings": ["target_identity_conflicting_geometry_rejected"],
                "targetAssociation": {
                    "associationMethod": "hover_menu_identity",
                    "intendedAction": "Chop down",
                    "intendedTargetName": "Tree",
                    "selectedCandidate": {"name": "Tree", "action": "Chop down"},
                    "rejectedCandidates": [{"name": "Gate", "action": "Close", "reasons": ["target_name_conflict", "target_action_conflict"]}],
                    "conflictReasons": ["target_name_conflict", "target_action_conflict"],
                },
            }
        ]
        summary = target_match_quality.summarize_quality(rows)
        association = summary["targetAssociation"]
        self.assertEqual(association["conflictCount"], 1)
        self.assertEqual(association["rejectedCandidateCount"], 1)
        self.assertEqual(association["examples"][0]["intendedTargetName"], "Tree")
        self.assertEqual(association["examples"][0]["rejectedCandidates"][0]["name"], "Gate")

    def test_joiner_writes_quality_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            recording.mkdir()
            t = target()
            write_jsonl(
                recording / "events.jsonl",
                [{"event_type": "source_snapshot", "elapsed_seconds": 0.5, "latest_tick": 10, "high_value_fields": {"nearby_objects": [t], "player": {"worldPoint": {"worldX": 3200, "worldY": 3200, "plane": 0}}}}],
            )
            write_jsonl(recording / "input_events.jsonl", [click()])
            result = input_trace_joiner.analyze_recording(recording, write=True, include_mapping=True)
            self.assertIn("target_match_summary", result)
            self.assertTrue((recording / "target_match_quality.jsonl").exists())
            self.assertTrue((recording / "target_match_summary.json").exists())

    def test_analyzer_summary_includes_target_match_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            recording.mkdir()
            t = target()
            write_jsonl(
                recording / "events.jsonl",
                [
                    {"event_type": "recording_start", "elapsed_seconds": 0, "session_id": "s1"},
                    {"event_type": "source_snapshot", "elapsed_seconds": 0.5, "latest_tick": 10, "high_value_fields": {"nearby_objects": [t], "player": {"worldPoint": {"worldX": 3200, "worldY": 3200, "plane": 0}}}},
                    {"event_type": "recording_stop", "elapsed_seconds": 2, "duration_seconds": 2},
                ],
            )
            write_jsonl(recording / "input_events.jsonl", [click()])
            summary = analyze_manual_recording.update_outputs(recording)
            self.assertIn("target_match_summary", summary)

    def test_camera_behavior_includes_next_click_quality(self):
        telemetry_events = [
            {"event_type": "source_snapshot", "elapsed_seconds": 1.0, "high_value_fields": {"cameraYaw": 100, "cameraPitch": 200}},
            {"event_type": "source_snapshot", "elapsed_seconds": 1.5, "high_value_fields": {"cameraYaw": 140, "cameraPitch": 220}},
            {"event_type": "source_snapshot", "elapsed_seconds": 2.0, "high_value_fields": {"cameraYaw": 140, "cameraPitch": 220}},
        ]
        input_events = [click(1, elapsed=2.2)]
        classes = [{"eventSeq": 1, "eventKind": "click", "classification": "object_action_click", "targetRelativeEligible": True, "eligibleForTargetMatching": True}]
        qualities = [{"eventSeq": 1, "quality": "strong", "score": 0.9, "matchedTarget": {"name": "Tree", "action": "Chop down"}}]
        summary = camera_behavior.summarize_camera_behavior(telemetry_events, input_events, action_classifications=classes, target_match_quality=qualities)
        self.assertEqual(summary["segments"][0]["nextClickTargetQuality"], "strong")

    def test_mapper_includes_quality_counts(self):
        events = [click(1), click(2, x=200)]
        classes = [
            {"eventSeq": 1, "eventKind": "click", "classification": "object_action_click", "targetRelativeEligible": True, "eligibleForTargetMatching": True},
            {"eventSeq": 2, "eventKind": "click", "classification": "object_action_click", "targetRelativeEligible": True, "eligibleForTargetMatching": True},
        ]
        qualities = [
            {"eventSeq": 1, "quality": "strong", "score": 0.9},
            {"eventSeq": 2, "quality": "weak", "score": 0.4, "warnings": ["large_distance_from_aim_px=120"]},
        ]
        mapping = vm_mouse_arduino_mapper.build_mapping(events, [], action_classifications=classes, target_match_quality=qualities)
        self.assertEqual(mapping["mappedStrongTargetClickCount"], 1)
        self.assertEqual(mapping["mappedWeakTargetClickCount"], 1)
        self.assertEqual(len(mapping["clickMappings"]), 1)


if __name__ == "__main__":
    unittest.main()

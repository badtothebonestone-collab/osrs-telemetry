import json
import tempfile
import unittest
from pathlib import Path

import sys


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import route_demonstration


def write_json(path: Path, payload: dict):
    path.write_text(json.dumps(payload), encoding="utf-8")


class RouteDemonstrationTest(unittest.TestCase):
    def test_guide_extraction_creates_ordered_path_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "rec"
            recording.mkdir()
            write_json(
                recording / "traversal_lifecycle.json",
                {
                    "schema": "traversal_lifecycle.v1",
                    "status": "PASS",
                    "routeName": "woodcutting_area_to_bank",
                    "start": {"areaLabel": "woodcutting_area"},
                    "end": {"areaLabel": "bank_area"},
                    "routeSegments": [
                        {
                            "segmentIndex": 1,
                            "segmentType": "area_start",
                            "label": "Start",
                            "startWorld": {"worldX": 1, "worldY": 1, "plane": 0},
                            "endWorld": {"worldX": 1, "worldY": 1, "plane": 0},
                        },
                        {
                            "segmentIndex": 2,
                            "segmentType": "walk_segment",
                            "label": "Walk",
                            "startWorld": {"worldX": 1, "worldY": 1, "plane": 0},
                            "endWorld": {"worldX": 5, "worldY": 5, "plane": 0},
                        },
                    ],
                    "steps": [],
                },
            )
            write_json(recording / "camera_behavior_summary.json", {"segments": []})

            guide = route_demonstration.build_route_guide([recording], route_name="woodcutting_area_to_bank")

            self.assertEqual(guide["status"], "PASS")
            self.assertEqual([point["world"] for point in guide["pathPoints"]], [
                {"worldX": 1, "worldY": 1, "plane": 0},
                {"worldX": 5, "worldY": 5, "plane": 0},
            ])

    def test_guide_extraction_creates_stair_interaction_with_plane_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "rec"
            recording.mkdir()
            write_json(
                recording / "traversal_lifecycle.json",
                {
                    "schema": "traversal_lifecycle.v1",
                    "status": "PASS",
                    "routeName": "woodcutting_area_to_bank",
                    "start": {"areaLabel": "woodcutting_area"},
                    "end": {"areaLabel": "bank_area"},
                    "routeSegments": [],
                    "steps": [
                        {
                            "stepIndex": 1,
                            "stepId": "route_step_001",
                            "type": "plane_transition",
                            "action": "Climb-up",
                            "targetName": "Staircase",
                            "targetId": 123,
                            "world": {"worldX": 10, "worldY": 20, "plane": 0},
                            "postcondition": {
                                "planeChanged": True,
                                "positionChanged": True,
                                "beforeSnapshot": {"world": {"worldX": 10, "worldY": 19, "plane": 0}},
                                "afterSnapshot": {"world": {"worldX": 10, "worldY": 20, "plane": 2}},
                            },
                            "result": "success",
                        }
                    ],
                },
            )
            write_json(recording / "camera_behavior_summary.json", {"segments": []})

            guide = route_demonstration.build_route_guide([recording], route_name="woodcutting_area_to_bank")

            self.assertEqual(len(guide["interactionSteps"]), 1)
            step = guide["interactionSteps"][0]
            self.assertEqual(step["action"], "Climb-up")
            self.assertEqual(step["targetName"], "Staircase")
            self.assertEqual(step["expectedPlaneChange"], 2)

    def test_bottom_floor_menu_interaction_is_extracted_as_floor_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "rec"
            recording.mkdir()
            write_json(recording / "summary.json", {"label": "Bank stairs Bottom floor option Woodcutting area."})
            write_json(
                recording / "traversal_lifecycle.json",
                {
                    "schema": "traversal_lifecycle.v1",
                    "status": "PASS",
                    "routeName": "Bank_to_Woodcutting_area",
                    "start": {"areaLabel": "bank_area"},
                    "end": {"areaLabel": "woodcutting_area"},
                    "routeSegments": [],
                    "steps": [
                        {
                            "stepIndex": 1,
                            "stepId": "route_step_001",
                            "type": "menu_selection",
                            "action": "Bottom-floor",
                            "targetName": "Staircase",
                            "targetId": 56231,
                            "world": {"worldX": 3205, "worldY": 3208, "plane": 2},
                            "menuSelection": {
                                "present": True,
                                "selectedRowIndex": 2,
                                "option": "Bottom-floor",
                                "target": "Staircase",
                            },
                            "postcondition": {
                                "planeChanged": True,
                                "positionChanged": True,
                                "beforeSnapshot": {"world": {"worldX": 3205, "worldY": 3209, "plane": 2}},
                                "afterSnapshot": {"world": {"worldX": 3206, "worldY": 3208, "plane": 0}},
                            },
                            "result": "success",
                        }
                    ],
                },
            )
            write_json(recording / "camera_behavior_summary.json", {"segments": []})

            guide = route_demonstration.build_route_guide([recording], route_name="Bank_to_Woodcutting_area")

            self.assertEqual(len(guide["floorSelectionInteractions"]), 1)
            step = guide["floorSelectionInteractions"][0]
            self.assertEqual(step["interactionType"], "floor_selection")
            self.assertEqual(step["option"], "Bottom floor")
            self.assertEqual(step["sourcePlane"], 2)
            self.assertEqual(step["destinationPlane"], 0)
            self.assertEqual(step["allowedSourcePlanes"], [2])
            self.assertEqual(step["objectId"], 56231)
            self.assertEqual(guide["directPlaneSkips"][0]["skippedPlanes"], [1])
            self.assertTrue(guide["directPlaneSkips"][0]["evidence"]["floorSelectionOptionCaptured"])

    def test_floor_selection_probe_accepts_strict_bottom_floor_menu_evidence(self):
        probe = {
            "currentWorld": {"worldX": 3209, "worldY": 3220, "plane": 2},
            "currentPlane": 2,
            "expectedRouteLeg": "Bank_to_Woodcutting_area",
            "probeFolder": "recordings/top_floor_probe",
            "staircaseObjectId": 56231,
            "staircaseWorld": {"worldX": 3205, "worldY": 3229, "plane": 2},
            "matchingMenuEntries": [
                {
                    "option": "Bottom-floor",
                    "target": "<col=ffff>Staircase",
                    "identifier": 56231,
                    "param0": 45,
                    "param1": 61,
                }
            ],
            "evidenceFreshness": {"status": "PASS"},
        }

        interaction = route_demonstration.floor_selection_interaction_from_probe(probe)

        self.assertEqual(interaction["interactionType"], "floor_selection")
        self.assertEqual(interaction["option"], "Bottom floor")
        self.assertEqual(interaction["objectId"], 56231)
        self.assertEqual(interaction["world"], {"worldX": 3205, "worldY": 3229, "plane": 2})
        self.assertEqual(interaction["sourcePlane"], 2)
        self.assertEqual(interaction["destinationPlane"], 0)
        self.assertEqual(interaction["expectedPlaneChange"], -2)
        self.assertFalse(interaction["evidence"]["labelOnlyEvidenceAccepted"])

    def test_floor_selection_probe_rejects_stale_label_or_wrong_identifier(self):
        base = {
            "currentWorld": {"worldX": 3209, "worldY": 3220, "plane": 2},
            "currentPlane": 2,
            "staircaseObjectId": 56231,
            "staircaseWorld": {"worldX": 3205, "worldY": 3229, "plane": 2},
        }
        label_only = {**base, "recordingLabel": "Bottom floor", "matchingMenuEntries": []}
        stale = {
            **base,
            "evidenceFreshness": {"status": "FAIL"},
            "matchingMenuEntries": [{"option": "Bottom-floor", "target": "Staircase", "identifier": 56231}],
        }
        wrong_id = {**base, "matchingMenuEntries": [{"option": "Bottom-floor", "target": "Staircase", "identifier": 16672}]}

        self.assertEqual(route_demonstration.floor_selection_interaction_from_probe(label_only), {})
        self.assertEqual(route_demonstration.floor_selection_interaction_from_probe(stale), {})
        self.assertEqual(route_demonstration.floor_selection_interaction_from_probe(wrong_id), {})

    def test_progress_prefers_floor_selection_over_climb_down_on_top_floor(self):
        guide = {
            "schema": route_demonstration.SCHEMA,
            "routeName": "Bank_to_Woodcutting_area",
            "pathPoints": [],
            "interactionSteps": [
                {
                    "interactionType": "route_object",
                    "action": "Climb-down",
                    "option": "Climb-down",
                    "targetName": "Staircase",
                    "targetId": 56231,
                    "world": {"worldX": 3205, "worldY": 3229, "plane": 2},
                    "sourcePlane": 2,
                    "destinationPlane": 1,
                    "expectedPlaneChange": -1,
                }
            ],
            "floorSelectionInteractions": [
                {
                    "interactionType": "floor_selection",
                    "action": "Bottom floor",
                    "option": "Bottom floor",
                    "targetName": "Staircase",
                    "targetId": 56231,
                    "world": {"worldX": 3205, "worldY": 3229, "plane": 2},
                    "sourcePlane": 2,
                    "destinationPlane": 0,
                    "allowedSourcePlanes": [2],
                    "expectedPlaneChange": -2,
                }
            ],
        }

        progress = route_demonstration.resolve_progress(guide, {"worldX": 3209, "worldY": 3220, "plane": 2})

        self.assertEqual(progress["status"], "PASS")
        self.assertEqual(progress["nextGuideInteraction"]["interactionType"], "floor_selection")
        self.assertEqual(progress["nextGuideInteraction"]["option"], "Bottom floor")

    def test_plane1_floor_selection_reentry_requires_allowed_source_plane(self):
        guide = {
            "schema": route_demonstration.SCHEMA,
            "routeName": "Bank_to_Woodcutting_area",
            "pathPoints": [],
            "interactionSteps": [],
            "floorSelectionInteractions": [
                {
                    "interactionType": "floor_selection",
                    "action": "Bottom floor",
                    "option": "Bottom floor",
                    "targetName": "Staircase",
                    "targetId": 56231,
                    "world": {"worldX": 3205, "worldY": 3208, "plane": 2},
                    "sourcePlane": 2,
                    "destinationPlane": 0,
                    "allowedSourcePlanes": [2],
                }
            ],
            "routeLegs": [],
        }

        reentry = route_demonstration.resolve_reentry(guide, {"worldX": 3205, "worldY": 3208, "plane": 1})

        self.assertEqual(reentry["status"], "WARN")
        self.assertEqual(reentry["blocker"], "route_guide_no_same_plane_reentry")

        guide["floorSelectionInteractions"][0]["allowedSourcePlanes"] = [1, 2]
        reentry = route_demonstration.resolve_reentry(guide, {"worldX": 3205, "worldY": 3208, "plane": 1})

        self.assertEqual(reentry["status"], "PASS")
        self.assertEqual(reentry["recoveryCandidateType"], "floor_selection_interaction")
        self.assertEqual(reentry["nextRecoveryStep"]["option"], "Bottom floor")

    def test_current_position_maps_to_nearest_and_next_point(self):
        guide = {
            "schema": route_demonstration.SCHEMA,
            "routeName": "woodcutting_area_to_bank",
            "pathPoints": [
                {"orderIndex": 0, "world": {"worldX": 1, "worldY": 1, "plane": 0}, "reachedToleranceTiles": 2},
                {"orderIndex": 1, "world": {"worldX": 5, "worldY": 5, "plane": 0}, "reachedToleranceTiles": 2},
            ],
            "interactionSteps": [],
        }

        progress = route_demonstration.resolve_progress(guide, {"worldX": 1, "worldY": 1, "plane": 0})

        self.assertEqual(progress["routeGuideProgressIndex"], 0)
        self.assertEqual(progress["nextGuidePoint"]["world"], {"worldX": 5, "worldY": 5, "plane": 0})

    def test_3203_3238_advances_to_next_demonstrated_point(self):
        guide = route_demonstration.load_route_guide("woodcutting_area_to_bank")

        progress = route_demonstration.resolve_progress(guide, {"worldX": 3203, "worldY": 3238, "plane": 0})

        self.assertEqual(progress["status"], "PASS")
        self.assertNotEqual(progress["nextGuidePoint"]["world"], {"worldX": 3203, "worldY": 3238, "plane": 0})

    def test_sparse_future_point_is_not_marked_reached(self):
        guide = route_demonstration.load_route_guide("woodcutting_area_to_bank")

        progress = route_demonstration.resolve_progress(guide, {"worldX": 3201, "worldY": 3219, "plane": 0})

        self.assertEqual(progress["status"], "PASS")
        self.assertEqual(progress["nextGuidePoint"]["world"], {"worldX": 3208, "worldY": 3212, "plane": 0})
        self.assertNotEqual(progress["blocker"], "route_guide_next_step_missing")

    def test_quarantined_trapdoor_bank_segment_is_not_route_progress(self):
        guide = route_demonstration.load_route_guide("woodcutting_area_to_bank")

        progress = route_demonstration.resolve_progress(guide, {"worldX": 3208, "worldY": 3212, "plane": 0})

        self.assertEqual(progress["status"], "WARN")
        self.assertEqual(progress["blocker"], "route_guide_invalid_members_route_segment")
        self.assertIsNone(progress["nextGuidePoint"])
        self.assertIsNone(progress["nextGuideInteraction"])
        self.assertIn("Trapdoor", progress["invalidRouteEvidence"]["item"]["label"])

    def test_wrong_floor_uses_plane1_recovery_when_probe_evidence_is_modeled(self):
        guide = route_demonstration.load_route_guide("Bank_to_Woodcutting_area")

        reentry = route_demonstration.resolve_reentry(guide, {"worldX": 3206, "worldY": 3229, "plane": 1})

        self.assertEqual(reentry["status"], "PASS")
        self.assertTrue(reentry["routeGuideReentryAttempted"])
        self.assertIsNone(reentry["blocker"])
        self.assertEqual(reentry["currentPlane"], 1)
        self.assertEqual(reentry["inferredSubsegment"]["classification"], "intermediate_floor_between_route_transitions")
        self.assertEqual(reentry["recoveryCandidateType"], "plane1_recovery_interaction")
        self.assertEqual(reentry["nextRecoveryStep"]["interactionType"], "plane1_recovery")
        self.assertEqual(reentry["nextRecoveryStep"]["option"], "Climb-down")
        self.assertEqual(reentry["nextRecoveryStep"]["objectId"], 16672)
        self.assertEqual(reentry["nextRecoveryStep"]["world"], {"worldX": 3204, "worldY": 3229, "plane": 1})
        self.assertFalse(reentry["nextRecoveryStep"]["evidence"]["bottomFloorCaptured"])

    def test_wrong_floor_without_same_plane_step_reports_reentry_gap(self):
        guide = {
            "schema": route_demonstration.SCHEMA,
            "routeName": "Bank_to_Woodcutting_area",
            "pathPoints": [],
            "interactionSteps": [],
            "floorSelectionInteractions": [
                {
                    "interactionType": "floor_selection",
                    "action": "Bottom floor",
                    "option": "Bottom floor",
                    "targetName": "Staircase",
                    "targetId": 56231,
                    "world": {"worldX": 3205, "worldY": 3229, "plane": 2},
                    "sourcePlane": 2,
                    "destinationPlane": 0,
                    "allowedSourcePlanes": [2],
                    "expectedPlaneChange": -2,
                }
            ],
            "routeLegs": [
                {
                    "segmentIndex": 1,
                    "segmentType": "plane_transition",
                    "startWorld": {"worldX": 3205, "worldY": 3229, "plane": 2},
                    "endWorld": {"worldX": 3205, "worldY": 3229, "plane": 0},
                }
            ],
        }

        reentry = route_demonstration.resolve_reentry(guide, {"worldX": 3206, "worldY": 3229, "plane": 1})

        self.assertEqual(reentry["status"], "WARN")
        self.assertEqual(reentry["blocker"], "route_guide_no_same_plane_reentry")
        self.assertEqual(reentry["suggestedFixture"], "record a short plane-1 Staircase recovery from 3206,3229,1")
        self.assertEqual(reentry["safeState"], "no click sent because route guide lacks same-plane proof")
        self.assertIn("Bottom floor direct transition was missed", reentry["likelyReason"])

    def test_plane1_recovery_probe_rejects_label_only_evidence(self):
        probe = {
            "nearPlane1RecoveryState": True,
            "player": {"worldX": 3206, "worldY": 3229, "plane": 1},
            "recordingLabel": "Bottom floor recovery",
            "staircaseObjects": [
                {"name": "Staircase", "objectId": 16672, "world": {"worldX": 3204, "worldY": 3229, "plane": 1}}
            ],
            "matchingMenuEntries": [],
        }

        self.assertEqual(route_demonstration.plane1_recovery_interaction_from_probe(probe), {})

    def test_plane1_recovery_probe_accepts_captured_climb_down_menu_evidence(self):
        probe = {
            "nearPlane1RecoveryState": True,
            "player": {"worldX": 3206, "worldY": 3229, "plane": 1},
            "probeFolder": "recordings/probe",
            "staircaseObjects": [
                {"name": "Staircase", "objectId": 16672, "world": {"worldX": 3204, "worldY": 3229, "plane": 1}}
            ],
            "matchingMenuEntries": [
                {"option": "Climb-up", "target": "Staircase", "identifier": 16672},
                {"option": "Climb-down", "target": "Staircase", "identifier": 16672, "rowBounds": {"x": 1, "y": 2, "w": 3, "h": 4}}
            ],
        }

        interaction = route_demonstration.plane1_recovery_interaction_from_probe(probe)

        self.assertEqual(interaction["interactionType"], "plane1_recovery")
        self.assertEqual(interaction["option"], "Climb-down")
        self.assertEqual(interaction["objectId"], 16672)
        self.assertEqual(interaction["world"], {"worldX": 3204, "worldY": 3229, "plane": 1})
        self.assertEqual(interaction["allowedSourcePlanes"], [1])

    def test_plane1_recovery_probe_rejects_stale_or_wrong_identifier_evidence(self):
        base = {
            "nearPlane1RecoveryState": True,
            "player": {"worldX": 3206, "worldY": 3229, "plane": 1},
            "staircaseObjects": [
                {"name": "Staircase", "objectId": 16672, "world": {"worldX": 3204, "worldY": 3229, "plane": 1}}
            ],
        }
        stale = {
            **base,
            "attempts": [{"hoverMenu": {"clientTickHot": {"postMenuSortAgeMillis": 9000}}}],
            "matchingMenuEntries": [{"option": "Climb-down", "target": "Staircase", "identifier": 16672}],
        }
        wrong_id = {**base, "matchingMenuEntries": [{"option": "Climb-down", "target": "Staircase", "identifier": 56231}]}

        self.assertEqual(route_demonstration.plane1_recovery_interaction_from_probe(stale), {})
        self.assertEqual(route_demonstration.plane1_recovery_interaction_from_probe(wrong_id), {})

    def test_same_plane_reentry_finds_nearest_guide_step(self):
        guide = {
            "schema": route_demonstration.SCHEMA,
            "routeName": "Bank_to_Woodcutting_area",
            "pathPoints": [
                {"orderIndex": 0, "world": {"worldX": 3206, "worldY": 3229, "plane": 1}, "reachedToleranceTiles": 2},
                {"orderIndex": 1, "world": {"worldX": 3204, "worldY": 3229, "plane": 1}, "reachedToleranceTiles": 2},
            ],
            "interactionSteps": [
                {
                    "orderIndex": 0,
                    "segmentIndex": 2,
                    "action": "Climb-down",
                    "targetName": "Staircase",
                    "targetId": 16672,
                    "world": {"worldX": 3204, "worldY": 3229, "plane": 1},
                    "expectedPlaneChange": -1,
                }
            ],
            "routeLegs": [],
        }

        reentry = route_demonstration.resolve_reentry(guide, {"worldX": 3206, "worldY": 3229, "plane": 1})

        self.assertEqual(reentry["status"], "PASS")
        self.assertEqual(reentry["recoveryCandidateType"], "route_guide_interaction")
        self.assertEqual(reentry["nextRecoveryStep"]["targetId"], 16672)

    def test_camera_hint_attaches_to_nearby_interaction_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "rec"
            recording.mkdir()
            write_json(
                recording / "traversal_lifecycle.json",
                {
                    "routeName": "woodcutting_area_to_bank",
                    "start": {"areaLabel": "woodcutting_area"},
                    "end": {"areaLabel": "bank_area"},
                    "routeSegments": [],
                    "steps": [
                        {
                            "stepIndex": 1,
                            "type": "plane_transition",
                            "startTime": 10.0,
                            "action": "Climb-down",
                            "targetName": "Trapdoor",
                            "world": {"worldX": 3209, "worldY": 3216, "plane": 0},
                            "postcondition": {
                                "planeChanged": True,
                                "beforeSnapshot": {"world": {"worldX": 3209, "worldY": 3216, "plane": 0}},
                                "afterSnapshot": {"world": {"worldX": 3205, "worldY": 3209, "plane": 2}},
                            },
                        }
                    ],
                },
            )
            write_json(
                recording / "camera_behavior_summary.json",
                {"segments": [{"segmentId": "cam_001", "endTime": 8.0, "deltaYaw": 100, "deltaPitch": -5}]},
            )

            guide = route_demonstration.build_route_guide([recording], route_name="woodcutting_area_to_bank")

            self.assertEqual(guide["interactionSteps"][0]["cameraHints"][0]["segmentId"], "cam_001")


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import target_view_core


class TargetViewCoreTest(unittest.TestCase):
    def test_target_bearing_uses_player_and_target_world_coordinates(self):
        bearing = target_view_core.compute_target_bearing(
            {"worldX": 3206, "worldY": 3229, "plane": 2},
            {"worldX": 3209, "worldY": 3221, "plane": 2},
        )

        self.assertTrue(bearing["available"])
        self.assertEqual(bearing["coordinateModel"], "osrs_world_atan2_dx_dy")
        self.assertAlmostEqual(bearing["targetBearingDegrees"], 159.444, places=2)
        self.assertEqual(bearing["targetBearing"], 907)

    def test_yaw_error_normalizes_across_wraparound(self):
        self.assertEqual(target_view_core.normalize_yaw_error(100, 2000), 148)
        self.assertEqual(target_view_core.normalize_yaw_error(1900, 100), -248)

    def test_policies_cover_current_and_future_target_kinds(self):
        for kind in ("service_object", "resource_object", "route_object", "navigation_waypoint", "npc", "ground_item", "widget"):
            policy = target_view_core.target_view_policy(kind)
            self.assertEqual(policy["schema"], "target_view_policy.v1")
            self.assertEqual(policy["targetKind"], kind)

        service = target_view_core.target_view_policy("service_object")
        waypoint = target_view_core.target_view_policy("navigation_waypoint")
        self.assertEqual(service["actionSafetyLevel"], "strict")
        self.assertFalse(service["allowMinimap"])
        self.assertLess(waypoint["minEdgeDistancePx"], service["minEdgeDistancePx"])

    def test_service_offscreen_state_uses_bearing_for_camera_plan(self):
        state = target_view_core.build_target_view_state(
            {
                "targetName": "deposit_box",
                "id": 27291,
                "worldX": 3209,
                "worldY": 3221,
                "plane": 2,
                "projectionStatus": {
                    "classification": "offscreen",
                    "onScreen": False,
                    "visible": False,
                    "actionableByCanvas": False,
                    "canvasLocation": {"x": 325, "y": 400},
                    "aimPoint": {"canvasX": 325, "canvasY": 400},
                    "visibleAreaRatio": 0.0,
                    "edgeDistancePx": 0.0,
                },
            },
            target_kind="service_object",
            player_location={"worldX": 3206, "worldY": 3229, "plane": 2},
            expected_action="Bank",
            target_source="live_world_model",
            target_route_relevant=True,
            target_action_relevant=True,
            safe_aimpoint={
                "status": "FAIL",
                "rejectionReason": "raw_aimpoint_outside_interactable_region",
                "rawAimPoint": {"x": 325, "y": 400},
            },
            viewport={
                "viewportXOffset": 4,
                "viewportYOffset": 4,
                "viewportWidth": 512,
                "viewportHeight": 334,
                "canvasWidth": 765,
                "canvasHeight": 503,
                "cameraYaw": 32,
                "cameraPitch": 383,
            },
            source_canvas_size={"canvasWidth": 765, "canvasHeight": 503},
        )

        self.assertEqual(state["schema"], "target_view_state.v1")
        self.assertEqual(state["targetKind"], "service_object")
        self.assertEqual(state["targetBearing"], 907)
        self.assertEqual(state["yawErrorToTarget"], 875)
        self.assertTrue(state["shouldAttemptCameraExposure"])
        self.assertEqual(state["viewQualityClassification"], "target_loaded_offscreen")
        plan = state["cameraMotorPlan"]
        self.assertEqual(plan["cameraDirectionChosen"], "yaw_right_pitch_up")
        self.assertEqual(plan["cameraDirectionReason"], "target_bearing_yaw_alignment_with_projection_pitch")
        self.assertGreater(plan["cameraHoldMs"], 310)

    def test_camera_hold_duration_decreases_as_yaw_error_decreases(self):
        large = target_view_core.target_camera_motor_plan(
            {
                "yawErrorToTarget": 800,
                "exposureError": {"errorMagnitude": 240, "dyFromCenter": 220},
            },
            calibration=target_view_core.camera_response_calibration_from_status({}),
        )
        small = target_view_core.target_camera_motor_plan(
            {
                "yawErrorToTarget": 80,
                "exposureError": {"errorMagnitude": 60, "dyFromCenter": 40},
            },
            calibration=target_view_core.camera_response_calibration_from_status({}),
        )

        self.assertGreater(large["cameraHoldMs"], small["cameraHoldMs"])
        self.assertEqual(large["controlLaw"], "bearing_yaw_alignment_plus_fitts_hold")


if __name__ == "__main__":
    unittest.main()

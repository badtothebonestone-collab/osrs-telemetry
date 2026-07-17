from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from osrs_bot.camera import (
    CameraCorrectionPhase,
    CameraKeyCapabilities,
    CameraResponseModel,
    CameraResponseSample,
    desired_camera_yaw,
    proves_yaw_overshoot,
    select_camera_hold_millis,
    shortest_yaw_error,
    world_bearing_yaw,
    yaw_direction_for_error,
    yaw_error_to_world_target,
    yaw_reversal_allowed,
)
from osrs_bot.model import CAMERA_YAW_UNITS, WorldPoint


class CameraControlTest(unittest.TestCase):
    def test_current_capability_is_immutable_and_defaults_to_250_millis(self) -> None:
        capability = CameraKeyCapabilities()

        self.assertEqual(250, capability.max_hold_millis)
        self.assertIn("arduino_hid", capability.source)
        with self.assertRaises(FrozenInstanceError):
            capability.max_hold_millis = 500  # type: ignore[misc]
        with self.assertRaises(ValueError):
            CameraKeyCapabilities(max_hold_millis=0)

    def test_world_bearing_and_desired_yaw_cover_cardinal_directions(self) -> None:
        source = WorldPoint(0, 0, 0)

        self.assertEqual(0, world_bearing_yaw(source, WorldPoint(0, -1, 0)))
        self.assertEqual(4096, world_bearing_yaw(source, WorldPoint(1, 0, 0)))
        self.assertEqual(8192, world_bearing_yaw(source, WorldPoint(0, 1, 0)))
        self.assertEqual(12288, world_bearing_yaw(source, WorldPoint(-1, 0, 0)))
        self.assertEqual(8192, desired_camera_yaw(source, WorldPoint(0, -1, 0)))
        self.assertEqual(0, desired_camera_yaw(source, WorldPoint(0, 1, 0)))
        self.assertIsNone(world_bearing_yaw(source, source))

    def test_shortest_yaw_error_preserves_wraparound_and_direction(self) -> None:
        self.assertEqual(22, shortest_yaw_error(8170, 8192))
        self.assertEqual(20, shortest_yaw_error(CAMERA_YAW_UNITS - 10, 10))
        self.assertEqual(-20, shortest_yaw_error(10, CAMERA_YAW_UNITS - 10))
        self.assertEqual("right", yaw_direction_for_error(20, deadband_units=8))
        self.assertEqual("left", yaw_direction_for_error(-20, deadband_units=8))
        self.assertIsNone(yaw_direction_for_error(8, deadband_units=8))

    def test_world_target_error_matches_existing_engine_convention(self) -> None:
        source = WorldPoint(3197, 3237, 0)
        target = WorldPoint(3193, 3244, 0)

        self.assertEqual(-2416, yaw_error_to_world_target(source, target, 3770))

    def test_response_model_is_bounded_and_uses_direction_specific_medians(self) -> None:
        model = CameraResponseModel(max_samples=3)
        model = model.record(
            CameraResponseSample("right", 100, observed_yaw_delta=400)
        )
        model = model.record(
            CameraResponseSample("right", 100, observed_yaw_delta=600)
        )
        model = model.record(
            CameraResponseSample("left", 100, observed_yaw_delta=-300)
        )

        self.assertEqual(5.0, model.median_rate("right"))
        self.assertEqual(3.0, model.median_rate("left"))

        model = model.record(
            CameraResponseSample("right", 100, observed_yaw_delta=-900)
        )
        self.assertEqual(3, len(model.samples))
        self.assertEqual(6.0, model.median_rate("right"))
        self.assertEqual(3.0, model.median_rate("left"))

    def test_no_effect_and_cross_direction_samples_do_not_calibrate_rate(self) -> None:
        model = CameraResponseModel().record(
            CameraResponseSample("up", 100, observed_pitch_delta=0, no_effect=True)
        ).record(
            CameraResponseSample("right", 100, observed_yaw_delta=-500)
        )

        self.assertIsNone(model.median_rate("up"))
        self.assertIsNone(model.median_rate("right"))

    def test_pitch_limit_blocks_only_same_direction_at_unchanged_pose(self) -> None:
        model = CameraResponseModel().record(
            CameraResponseSample(
                "down",
                120,
                before_pitch=1024,
                after_pitch=1024,
                pose_limit=True,
                no_effect=True,
            )
        )

        self.assertTrue(model.pitch_direction_blocked("down", 1024))
        self.assertFalse(model.pitch_direction_blocked("up", 1024))
        self.assertFalse(model.pitch_direction_blocked("down", 1025))

    def test_coarse_hold_increases_with_error_and_respects_device_cap(self) -> None:
        capability = CameraKeyCapabilities()
        near = select_camera_hold_millis(
            400,
            "right",
            CameraCorrectionPhase.COARSE,
            capability,
            minimum_hold_millis=1,
        )
        far = select_camera_hold_millis(
            1600,
            "right",
            CameraCorrectionPhase.COARSE,
            capability,
            minimum_hold_millis=1,
        )

        self.assertLess(near, far)
        self.assertEqual(250, far)
        self.assertEqual(
            75,
            select_camera_hold_millis(
                8192,
                "right",
                CameraCorrectionPhase.COARSE,
                CameraKeyCapabilities(max_hold_millis=75, source="test-device"),
            ),
        )

    def test_fine_hold_decreases_near_goal_and_is_smaller_than_coarse(self) -> None:
        capability = CameraKeyCapabilities()
        coarse = select_camera_hold_millis(
            800,
            "left",
            CameraCorrectionPhase.COARSE,
            capability,
            minimum_hold_millis=1,
        )
        fine_far = select_camera_hold_millis(
            800,
            "left",
            CameraCorrectionPhase.FINE,
            capability,
            minimum_hold_millis=1,
        )
        fine_near = select_camera_hold_millis(
            200,
            "left",
            CameraCorrectionPhase.FINE,
            capability,
            minimum_hold_millis=1,
        )

        self.assertLess(fine_near, fine_far)
        self.assertLess(fine_far, coarse)
        self.assertEqual(
            0,
            select_camera_hold_millis(
                16,
                "left",
                CameraCorrectionPhase.FINE,
                capability,
                deadband_units=16,
            ),
        )

    def test_measured_direction_rate_changes_hold_without_cross_axis_leakage(self) -> None:
        model = CameraResponseModel().record(
            CameraResponseSample("right", 100, observed_yaw_delta=1000)
        ).record(
            CameraResponseSample("up", 100, observed_pitch_delta=100)
        )
        capability = CameraKeyCapabilities()

        right = select_camera_hold_millis(
            1000,
            "right",
            CameraCorrectionPhase.COARSE,
            capability,
            response_model=model,
            minimum_hold_millis=1,
        )
        up = select_camera_hold_millis(
            1000,
            "up",
            CameraCorrectionPhase.COARSE,
            capability,
            response_model=model,
            minimum_hold_millis=1,
        )

        self.assertEqual(85, right)
        self.assertEqual(250, up)

    def test_reversal_requires_fresh_changed_geometry_overshoot(self) -> None:
        self.assertFalse(
            proves_yaw_overshoot(
                600,
                -120,
                pose_result_fresh=False,
                geometry_changed=True,
                deadband_units=32,
            )
        )
        self.assertFalse(
            proves_yaw_overshoot(
                600,
                -120,
                pose_result_fresh=True,
                geometry_changed=False,
                deadband_units=32,
            )
        )
        overshoot = proves_yaw_overshoot(
            600,
            -120,
            pose_result_fresh=True,
            geometry_changed=True,
            deadband_units=32,
        )

        self.assertTrue(overshoot)
        self.assertTrue(
            yaw_reversal_allowed("right", "right", overshoot_proved=False)
        )
        self.assertFalse(
            yaw_reversal_allowed("right", "left", overshoot_proved=False)
        )
        self.assertTrue(
            yaw_reversal_allowed("right", "left", overshoot_proved=overshoot)
        )


if __name__ == "__main__":
    unittest.main()

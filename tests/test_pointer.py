from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import inspect
import math
import random
import unittest

import osrs_bot.pointer as pointer_module
from osrs_bot.model import ScreenBounds, ScreenPoint
from osrs_bot.pointer import (
    FIRMWARE_MAX_DELTA_PX,
    PointerDelta,
    PointerMotionLimits,
    PointerMotionPlan,
    PointerTrajectoryConfig,
    plan_pointer_motion,
)


BOUNDS = ScreenBounds(-100, -100, 400, 400)
START = ScreenPoint(50, 50)
TIMESTEP = 0.01


class PointerMotionPolicyTest(unittest.TestCase):
    def assert_plan_invariants(self, plan: PointerMotionPlan) -> None:
        self.assertTrue(plan.bounds.contains(plan.start))
        self.assertTrue(plan.bounds.contains(plan.target))
        self.assertEqual(len(plan.steps) * plan.timestep_seconds, plan.duration_seconds)

        previous_dx = 0
        previous_dy = 0
        x = plan.start.x
        y = plan.start.y
        for index, step in enumerate(plan.steps):
            self.assertNotEqual((0, 0), (step.dx, step.dy), index)
            self.assertLessEqual(abs(step.dx), FIRMWARE_MAX_DELTA_PX, index)
            self.assertLessEqual(abs(step.dy), FIRMWARE_MAX_DELTA_PX, index)
            self.assertLessEqual(abs(step.dx), plan.limits.max_step_delta_px, index)
            self.assertLessEqual(abs(step.dy), plan.limits.max_step_delta_px, index)

            velocity = max(abs(step.dx), abs(step.dy)) / plan.timestep_seconds
            acceleration = max(
                abs(step.dx - previous_dx),
                abs(step.dy - previous_dy),
            ) / (plan.timestep_seconds * plan.timestep_seconds)
            self.assertLessEqual(
                velocity,
                plan.limits.max_velocity_px_per_second + 1e-9,
                index,
            )
            self.assertLessEqual(
                acceleration,
                plan.limits.max_acceleration_px_per_second_squared + 1e-9,
                index,
            )
            x += step.dx
            y += step.dy
            self.assertTrue(plan.bounds.contains(ScreenPoint(x, y)), index)
            previous_dx = step.dx
            previous_dy = step.dy

        stopping_acceleration = max(abs(previous_dx), abs(previous_dy)) / (
            plan.timestep_seconds * plan.timestep_seconds
        )
        self.assertLessEqual(
            stopping_acceleration,
            plan.limits.max_acceleration_px_per_second_squared + 1e-9,
        )
        self.assertEqual(plan.target, ScreenPoint(x, y))
        self.assertEqual(plan.positions[-1:] or (), (plan.target,) if plan.steps else ())
        self.assertLessEqual(
            plan.peak_velocity_px_per_second,
            plan.limits.max_velocity_px_per_second + 1e-9,
        )
        self.assertLessEqual(
            plan.peak_acceleration_px_per_second_squared,
            plan.limits.max_acceleration_px_per_second_squared + 1e-9,
        )

    def test_short_moves_arrive_exactly_without_zero_or_teleport_steps(self) -> None:
        for target in (
            ScreenPoint(51, 50),
            ScreenPoint(49, 49),
            ScreenPoint(52, 51),
            ScreenPoint(50, 53),
        ):
            with self.subTest(target=target):
                plan = plan_pointer_motion(
                    START,
                    target,
                    BOUNDS,
                    timestep_seconds=TIMESTEP,
                )
                self.assert_plan_invariants(plan)
                self.assertLessEqual(len(plan.steps), 2)

    def test_stationary_request_is_an_empty_exact_plan(self) -> None:
        plan = plan_pointer_motion(
            START,
            START,
            BOUNDS,
            timestep_seconds=0.0001,
        )

        self.assertEqual((), plan.steps)
        self.assertEqual((), plan.positions)
        self.assertEqual(0.0, plan.duration_seconds)
        self.assertEqual(0.0, plan.peak_velocity_px_per_second)
        self.assertEqual(0.0, plan.peak_acceleration_px_per_second_squared)
        self.assert_plan_invariants(plan)

    def test_long_move_accelerates_cruises_and_brakes_for_target(self) -> None:
        plan = plan_pointer_motion(
            ScreenPoint(-50, 20),
            ScreenPoint(250, 20),
            BOUNDS,
            timestep_seconds=TIMESTEP,
        )
        speeds = [abs(step.dx) for step in plan.steps]

        self.assert_plan_invariants(plan)
        self.assertGreater(max(speeds), speeds[0])
        last_peak = max(index for index, speed in enumerate(speeds) if speed == max(speeds))
        self.assertGreater(last_peak, 0)
        self.assertTrue(
            all(left >= right for left, right in zip(speeds[last_peak:], speeds[last_peak + 1 :])),
            speeds,
        )
        self.assertLess(speeds[-1], max(speeds))
        self.assertLess(len(plan.steps), 100)

    def test_many_directions_and_distances_preserve_every_invariant(self) -> None:
        offsets = (-140, -57, -21, -20, -7, -2, -1, 0, 1, 3, 19, 20, 57, 140)
        for dx in offsets:
            for dy in offsets:
                target = ScreenPoint(START.x + dx, START.y + dy)
                with self.subTest(dx=dx, dy=dy):
                    plan = plan_pointer_motion(
                        START,
                        target,
                        BOUNDS,
                        timestep_seconds=TIMESTEP,
                    )
                    self.assert_plan_invariants(plan)
                    self.assertTrue(all(step.dx * dx >= 0 for step in plan.steps))
                    self.assertTrue(all(step.dy * dy >= 0 for step in plan.steps))

    def test_unseeded_motion_remains_compatible_and_transport_free(self) -> None:
        arguments = (
            ScreenPoint(-80, -70),
            ScreenPoint(275, 260),
            BOUNDS,
        )
        first = plan_pointer_motion(*arguments, timestep_seconds=0.016)
        second = plan_pointer_motion(*arguments, timestep_seconds=0.016)
        imports = {
            alias.name.split(".", maxsplit=1)[0]
            for node in ast.walk(ast.parse(inspect.getsource(pointer_module)))
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        self.assertEqual(first, second)
        self.assertNotIn("serial", imports)
        self.assertNotIn("pyautogui", imports)
        self.assertEqual("linear", first.path_style)
        self.assertIsNone(first.trajectory_seed)
        self.assert_plan_invariants(first)

    def test_seeded_curve_is_reproducible_and_does_not_touch_global_rng(self) -> None:
        bounds = ScreenBounds(0, 0, 800, 600)
        arguments = (
            ScreenPoint(40, 300),
            ScreenPoint(700, 200),
            bounds,
        )
        options = {
            "timestep_seconds": 0.02,
            "seed": 1,
            "decision_id": "tree-interaction-17",
            "target_bounds": ScreenBounds(680, 180, 40, 40),
            "context": "object",
        }
        random.seed(8_675_309)
        state_before = random.getstate()
        first = plan_pointer_motion(*arguments, **options)
        state_after = random.getstate()
        second = plan_pointer_motion(*arguments, **options)

        self.assertEqual(state_before, state_after)
        self.assertEqual(first, second)
        self.assertEqual("cubic_bezier", first.path_style)
        self.assertEqual("1", first.trajectory_seed)
        self.assertEqual("tree-interaction-17", first.decision_id)
        self.assertEqual("object", first.context)
        self.assertEqual(2, len(first.control_points))
        self.assertGreater(first.path_length_px, first.movement_distance_px)
        self.assertTrue(all(bounds.contains(point) for point in first.positions))
        self.assert_plan_invariants(first)

    def test_seed_and_decision_id_vary_curve_duration_and_approach(self) -> None:
        bounds = ScreenBounds(0, 0, 800, 600)
        arguments = (
            ScreenPoint(40, 300),
            ScreenPoint(700, 200),
            bounds,
        )
        common = {
            "timestep_seconds": 0.02,
            "decision_id": "tree-1",
            "target_bounds": ScreenBounds(680, 180, 40, 40),
            "context": "object",
        }
        first = plan_pointer_motion(*arguments, seed=1, **common)
        other_seed = plan_pointer_motion(*arguments, seed=10, **common)
        other_decision = plan_pointer_motion(
            *arguments,
            seed=1,
            **{**common, "decision_id": "tree-2"},
        )

        self.assertNotEqual(first.steps, other_seed.steps)
        self.assertNotEqual(first.control_points, other_seed.control_points)
        self.assertNotEqual(first.duration_seconds, other_seed.duration_seconds)
        self.assertNotEqual(
            first.approach_angle_degrees,
            other_seed.approach_angle_degrees,
        )
        self.assertNotEqual(first, other_decision)
        for plan in (first, other_seed, other_decision):
            self.assert_plan_invariants(plan)

    def test_target_size_and_distance_change_motion_characteristics(self) -> None:
        bounds = ScreenBounds(0, 0, 800, 600)
        start = ScreenPoint(40, 300)
        target = ScreenPoint(700, 200)
        precise = plan_pointer_motion(
            start,
            target,
            bounds,
            timestep_seconds=0.02,
            seed=10,
            decision_id="same-distance",
            target_bounds=ScreenBounds(698, 198, 5, 5),
            context="precise_object",
        )
        broad = plan_pointer_motion(
            start,
            target,
            bounds,
            timestep_seconds=0.02,
            seed=10,
            decision_id="same-distance",
            target_bounds=ScreenBounds(680, 180, 40, 40),
            context="broad_walk",
        )
        short = plan_pointer_motion(
            start,
            ScreenPoint(90, 300),
            bounds,
            timestep_seconds=0.02,
            seed=10,
            decision_id="short-precise",
            target_bounds=ScreenBounds(88, 298, 5, 5),
            context="precise_object",
        )

        self.assertLess(broad.duration_seconds, precise.duration_seconds)
        self.assertGreater(
            broad.path_length_px / broad.duration_seconds,
            precise.path_length_px / precise.duration_seconds,
        )
        self.assertGreater(broad.peak_velocity_px_per_second, short.peak_velocity_px_per_second)
        self.assertGreater(broad.movement_distance_px, short.movement_distance_px)
        for plan in (precise, broad, short):
            self.assert_plan_invariants(plan)

    def test_seeded_curve_falls_back_safely_when_tight_bounds_reject_curvature(self) -> None:
        bounds = ScreenBounds(10, 20, 31, 31)
        plan = plan_pointer_motion(
            ScreenPoint(10, 20),
            ScreenPoint(40, 50),
            bounds,
            timestep_seconds=TIMESTEP,
            seed="tight-corner",
            decision_id="bounded",
            target_bounds=ScreenBounds(38, 48, 3, 3),
            context="precise_object",
        )

        self.assertIn(plan.path_style, {"cubic_bezier", "seeded_linear"})
        self.assertTrue(all(bounds.contains(point) for point in plan.positions))
        self.assert_plan_invariants(plan)

    def test_trajectory_configuration_and_target_region_fail_closed(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            PointerTrajectoryConfig().maximum_curve_offset_px = 10.0  # type: ignore[misc]
        with self.assertRaises(ValueError):
            PointerTrajectoryConfig(minimum_speed_scale=0.95, maximum_speed_scale=0.9)
        with self.assertRaises(ValueError):
            PointerTrajectoryConfig(
                precise_target_threshold_px=20,
                broad_target_threshold_px=10,
            )
        with self.assertRaises(ValueError):
            plan_pointer_motion(
                START,
                ScreenPoint(80, 65),
                BOUNDS,
                timestep_seconds=TIMESTEP,
                seed=7,
                target_bounds=ScreenBounds(0, 0, 10, 10),
            )
        with self.assertRaises(TypeError):
            plan_pointer_motion(
                START,
                ScreenPoint(80, 65),
                BOUNDS,
                timestep_seconds=TIMESTEP,
                seed=True,
            )

    def test_tighter_explicit_caps_are_respected(self) -> None:
        limits = PointerMotionLimits(
            max_velocity_px_per_second=400.0,
            max_acceleration_px_per_second_squared=10_000.0,
            max_step_delta_px=4,
        )
        plan = plan_pointer_motion(
            ScreenPoint(-90, -90),
            ScreenPoint(290, 290),
            BOUNDS,
            timestep_seconds=0.02,
            limits=limits,
        )

        self.assert_plan_invariants(plan)
        self.assertTrue(all(abs(step.dx) <= 4 and abs(step.dy) <= 4 for step in plan.steps))
        self.assertGreater(len(plan.steps), 90)

    def test_intermediate_points_stay_inside_tight_verified_bounds(self) -> None:
        bounds = ScreenBounds(10, 20, 31, 31)
        for start, target in (
            (ScreenPoint(10, 20), ScreenPoint(40, 50)),
            (ScreenPoint(40, 50), ScreenPoint(10, 20)),
            (ScreenPoint(10, 50), ScreenPoint(40, 20)),
            (ScreenPoint(40, 20), ScreenPoint(10, 50)),
        ):
            with self.subTest(start=start, target=target):
                plan = plan_pointer_motion(
                    start,
                    target,
                    bounds,
                    timestep_seconds=TIMESTEP,
                )
                self.assert_plan_invariants(plan)
                self.assertTrue(all(bounds.contains(point) for point in plan.positions))

    def test_public_values_are_deeply_shaped_as_immutable_slot_records(self) -> None:
        limits = PointerMotionLimits()
        plan = plan_pointer_motion(
            START,
            ScreenPoint(80, 65),
            BOUNDS,
            timestep_seconds=TIMESTEP,
            limits=limits,
        )

        for value in (limits, plan.steps[0], plan):
            self.assertFalse(hasattr(value, "__dict__"))
        self.assertIsInstance(plan.steps, tuple)
        with self.assertRaises(FrozenInstanceError):
            plan.timestep_seconds = 1.0  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            plan.steps[0].dx = 5  # type: ignore[misc]

    def test_invalid_limits_fail_clearly(self) -> None:
        for value in (0, -1, math.inf, -math.inf, math.nan):
            with self.subTest(velocity=value):
                with self.assertRaises(ValueError):
                    PointerMotionLimits(max_velocity_px_per_second=value)
            with self.subTest(acceleration=value):
                with self.assertRaises(ValueError):
                    PointerMotionLimits(max_acceleration_px_per_second_squared=value)
        for value in (True, "1000"):
            with self.subTest(non_numeric=value):
                with self.assertRaises(TypeError):
                    PointerMotionLimits(max_velocity_px_per_second=value)  # type: ignore[arg-type]
        for value in (0, -1, FIRMWARE_MAX_DELTA_PX + 1):
            with self.subTest(step_cap=value):
                with self.assertRaises(ValueError):
                    PointerMotionLimits(max_step_delta_px=value)
        with self.assertRaises(TypeError):
            PointerMotionLimits(max_step_delta_px=True)
        with self.assertRaises(ValueError):
            PointerMotionLimits(max_velocity_px_per_second=10**10_000)

    def test_invalid_timestep_or_unrepresentable_integer_kinematics_fail(self) -> None:
        for timestep in (0, -0.01, math.inf, math.nan):
            with self.subTest(timestep=timestep):
                with self.assertRaises(ValueError):
                    plan_pointer_motion(
                        START,
                        ScreenPoint(51, 50),
                        BOUNDS,
                        timestep_seconds=timestep,
                    )
        with self.assertRaises(TypeError):
            plan_pointer_motion(
                START,
                ScreenPoint(51, 50),
                BOUNDS,
                timestep_seconds=True,
            )
        with self.assertRaisesRegex(ValueError, "velocity cap"):
            plan_pointer_motion(
                START,
                ScreenPoint(51, 50),
                BOUNDS,
                timestep_seconds=0.0001,
            )
        with self.assertRaisesRegex(ValueError, "acceleration cap"):
            plan_pointer_motion(
                START,
                ScreenPoint(51, 50),
                BOUNDS,
                timestep_seconds=0.001,
                limits=PointerMotionLimits(max_velocity_px_per_second=2_000.0),
            )

    def test_malformed_or_out_of_bounds_geometry_fails_closed(self) -> None:
        invalid_calls = (
            (ScreenPoint(-101, 0), START, BOUNDS),
            (START, ScreenPoint(300, 0), BOUNDS),
            (START, ScreenPoint(51, 50), ScreenBounds(0, 0, 0, 10)),
            (START, ScreenPoint(51, 50), ScreenBounds(0, 0, 10, -1)),
        )
        for start, target, bounds in invalid_calls:
            with self.subTest(start=start, target=target, bounds=bounds):
                with self.assertRaises(ValueError):
                    plan_pointer_motion(
                        start,
                        target,
                        bounds,
                        timestep_seconds=TIMESTEP,
                    )
        with self.assertRaises(TypeError):
            plan_pointer_motion(  # type: ignore[arg-type]
                (50, 50),
                START,
                BOUNDS,
                timestep_seconds=TIMESTEP,
            )
        with self.assertRaises(TypeError):
            plan_pointer_motion(
                ScreenPoint(True, 50),
                START,
                BOUNDS,
                timestep_seconds=TIMESTEP,
            )

    def test_delta_and_direct_plan_construction_cannot_bypass_invariants(self) -> None:
        with self.assertRaises(ValueError):
            PointerDelta(0, 0)
        with self.assertRaises(ValueError):
            PointerDelta(FIRMWARE_MAX_DELTA_PX + 1, 0)
        with self.assertRaises(TypeError):
            PointerDelta(True, 0)

        with self.assertRaisesRegex(ValueError, "acceleration"):
            PointerMotionPlan(
                start=ScreenPoint(0, 0),
                target=ScreenPoint(3, 0),
                bounds=ScreenBounds(0, 0, 20, 20),
                timestep_seconds=TIMESTEP,
                limits=PointerMotionLimits(),
                steps=(PointerDelta(3, 0),),
            )
        with self.assertRaisesRegex(ValueError, "leaves verified bounds"):
            PointerMotionPlan(
                start=ScreenPoint(5, 5),
                target=ScreenPoint(6, 5),
                bounds=ScreenBounds(0, 0, 20, 20),
                timestep_seconds=0.1,
                limits=PointerMotionLimits(),
                steps=(PointerDelta(15, 0), PointerDelta(-14, 0)),
            )
        with self.assertRaisesRegex(ValueError, "arrive exactly"):
            PointerMotionPlan(
                start=ScreenPoint(5, 5),
                target=ScreenPoint(6, 5),
                bounds=ScreenBounds(0, 0, 20, 20),
                timestep_seconds=0.1,
                limits=PointerMotionLimits(),
                steps=(PointerDelta(2, 0),),
            )
        with self.assertRaises(TypeError):
            PointerMotionPlan(
                start=ScreenPoint(5, 5),
                target=ScreenPoint(6, 5),
                bounds=ScreenBounds(0, 0, 20, 20),
                timestep_seconds=0.1,
                limits=PointerMotionLimits(),
                steps=[PointerDelta(1, 0)],  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()

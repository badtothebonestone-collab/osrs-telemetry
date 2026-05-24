import math
import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from input_control.human_input_controller import HumanInputController, HumanInputContext, resolve_input_profile
from input_control.mouse_movement import MousePoint, MouseTarget


class FakeBackend:
    name = "fake"

    def __init__(self):
        self.calls = []

    def move(self, plan):
        self.calls.append(("move", plan.click_point.x, plan.click_point.y, plan.duration_ms))

    def click_at(self, x, y, *, button="left", hold_ms=0):
        self.calls.append(("click_at", x, y, button, hold_ms))

    def key_down(self, key):
        self.calls.append(("key_down", key))

    def key_up(self, key):
        self.calls.append(("key_up", key))

    def mouse_down(self, *, button="left"):
        self.calls.append(("mouse_down", button))

    def mouse_up(self, *, button="left"):
        self.calls.append(("mouse_up", button))

    def move_relative(self, dx, dy, *, duration_ms=0):
        self.calls.append(("move_relative", dx, dy, duration_ms))


class HumanInputControllerTest(unittest.TestCase):
    def test_fitts_duration_increases_with_distance_and_smaller_target(self):
        controller = HumanInputController(FakeBackend(), profile="steady", sleep_func=lambda _seconds: None, seed=1)
        start = MousePoint(0, 0)

        near = controller.plan_mouse_movement(start, MouseTarget(120, 0, radius_px=12, width_px=24), "linear_debug")
        far = controller.plan_mouse_movement(start, MouseTarget(640, 0, radius_px=12, width_px=24), "linear_debug")
        small = controller.plan_mouse_movement(start, MouseTarget(640, 0, radius_px=3, width_px=6), "linear_debug")

        self.assertGreater(far.duration_ms, near.duration_ms)
        self.assertGreater(small.duration_ms, far.duration_ms)

    def test_steady_trajectory_has_acceleration_and_deceleration_timing(self):
        controller = HumanInputController(FakeBackend(), profile="steady", sleep_func=lambda _seconds: None, seed=2)

        plan = controller.plan_mouse_movement(MousePoint(0, 0), MouseTarget(500, 0, radius_px=10, width_px=20), "linear_debug")
        intervals = [
            int(right.timestamp_ms or 0) - int(left.timestamp_ms or 0)
            for left, right in zip(plan.points, plan.points[1:])
            if int(right.timestamp_ms or 0) > int(left.timestamp_ms or 0)
        ]

        self.assertGreater(len(intervals), 4)
        middle = intervals[len(intervals) // 2]
        self.assertGreater(intervals[0], middle)
        self.assertGreater(intervals[-1], middle)

    def test_endpoint_jitter_stays_inside_safe_target_radius(self):
        controller = HumanInputController(FakeBackend(), profile="natural", sleep_func=lambda _seconds: None, seed=7)
        target = MouseTarget(300, 250, radius_px=4, width_px=8)

        plan = controller.plan_mouse_movement(MousePoint(0, 0), target, "linear_debug")

        self.assertLessEqual(math.hypot(plan.click_point.x - target.x, plan.click_point.y - target.y), target.radius_px)

    def test_click_uses_profile_settle_and_hold_duration(self):
        backend = FakeBackend()
        sleeps = []
        controller = HumanInputController(backend, profile="steady", sleep_func=sleeps.append, seed=3)

        controller.click_at(100, 120, context=HumanInputContext(reason="unit_test"))

        self.assertEqual(backend.calls, [("click_at", 100, 120, "left", 82)])
        self.assertEqual([round(value, 3) for value in sleeps], [0.08, 0.08])
        metrics = controller.metrics()
        self.assertEqual(metrics["profile"], "steady")
        self.assertEqual(metrics["averageClickHoldMs"], 82)
        self.assertEqual(metrics["directBackendBypassCount"], 0)

    def test_hold_keys_uses_key_down_key_up_and_releases_on_error(self):
        backend = FakeBackend()
        controller = HumanInputController(backend, profile="natural", sleep_func=lambda _seconds: None, seed=4)

        with self.assertRaisesRegex(RuntimeError, "boom"):
            with controller.hold_keys(("right", "up"), context=HumanInputContext(reason="camera")):
                raise RuntimeError("boom")

        self.assertEqual(
            backend.calls,
            [
                ("key_down", "right"),
                ("key_down", "up"),
                ("key_up", "up"),
                ("key_up", "right"),
            ],
        )
        self.assertEqual(controller.metrics()["cameraDirectionSwitches"], 0)

    def test_reaction_delay_is_reason_specific_and_recorded(self):
        sleeps = []
        controller = HumanInputController(FakeBackend(), profile="steady", sleep_func=sleeps.append, seed=5)

        delay_ms = controller.apply_reaction_delay("after_navigation_progress")

        self.assertEqual(delay_ms, 300)
        self.assertEqual(sleeps, [0.3])
        self.assertEqual(controller.metrics()["averageReactionDelayMs"], 300)

    def test_resolve_manual_calibrated_falls_back_to_natural_parameters(self):
        profile = resolve_input_profile("manual_calibrated")

        self.assertEqual(profile.name, "manual_calibrated")
        self.assertGreaterEqual(profile.max_move_ms, resolve_input_profile("steady").max_move_ms)


if __name__ == "__main__":
    unittest.main()

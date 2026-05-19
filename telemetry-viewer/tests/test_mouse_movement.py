import math
import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from input_control.mouse_movement import (
    MouseMovementProfile,
    MousePoint,
    MouseTarget,
    estimate_fitts_duration_ms,
    plan_mouse_movement,
)


class MouseMovementTest(unittest.TestCase):
    def target(self):
        return MouseTarget(x=100, y=50, radius_px=6, label="target", source="test")

    def assert_starts_and_ends(self, plan):
        self.assertEqual((plan.points[0].x, plan.points[0].y), (0, 0))
        self.assertLessEqual(math.hypot(plan.click_point.x - 100, plan.click_point.y - 50), 6)
        self.assertEqual((plan.points[-1].x, plan.points[-1].y), (plan.click_point.x, plan.click_point.y))
        self.assertEqual(plan.validation_status, "PASS")

    def test_instant_test_creates_direct_path(self):
        plan = plan_mouse_movement(MousePoint(0, 0), self.target(), "instant_test")

        self.assertEqual(len(plan.points), 2)
        self.assertEqual(plan.duration_ms, 0)
        self.assert_starts_and_ends(plan)

    def test_linear_debug_starts_and_ends_correctly(self):
        plan = plan_mouse_movement(MousePoint(0, 0), self.target(), "linear_debug")

        self.assertGreater(len(plan.points), 2)
        self.assert_starts_and_ends(plan)

    def test_smooth_bezier_starts_and_ends_correctly(self):
        plan = plan_mouse_movement(MousePoint(0, 0), self.target(), MouseMovementProfile(name="smooth_bezier", seed=42))

        self.assertGreater(len(plan.points), 4)
        self.assert_starts_and_ends(plan)

    def test_fitts_guided_duration_responds_to_distance_and_target_size(self):
        near = estimate_fitts_duration_ms(20, 30, min_duration_ms=80, max_duration_ms=800)
        far = estimate_fitts_duration_ms(500, 6, min_duration_ms=80, max_duration_ms=800)

        self.assertGreater(far, near)

    def test_wind_mouse_starts_and_ends_correctly(self):
        plan = plan_mouse_movement(MousePoint(0, 0), self.target(), MouseMovementProfile(name="wind_mouse", seed=123))

        self.assertGreater(len(plan.points), 3)
        self.assert_starts_and_ends(plan)

    def test_wind_mouse_is_deterministic_with_same_seed(self):
        first = plan_mouse_movement(MousePoint(0, 0), self.target(), MouseMovementProfile(name="wind_mouse", seed=123))
        second = plan_mouse_movement(MousePoint(0, 0), self.target(), MouseMovementProfile(name="wind_mouse", seed=123))

        self.assertEqual([(p.x, p.y) for p in first.points], [(p.x, p.y) for p in second.points])

    def test_wind_mouse_differs_with_different_seed(self):
        first = plan_mouse_movement(MousePoint(0, 0), self.target(), MouseMovementProfile(name="wind_mouse", seed=123))
        second = plan_mouse_movement(MousePoint(0, 0), self.target(), MouseMovementProfile(name="wind_mouse", seed=456))

        self.assertNotEqual([(p.x, p.y) for p in first.points[1:-1]], [(p.x, p.y) for p in second.points[1:-1]])


if __name__ == "__main__":
    unittest.main()

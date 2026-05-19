import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from argparse import Namespace

from input_control.action_proposal import ActionProposal
from input_control.backend_pyautogui import scale_canvas_point_to_screen
from input_control.executor import execute_action, execute_next_action


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.converted_points = []

    def current_position(self):
        return (0, 0)

    def canvas_to_screen_point(self, point):
        self.converted_points.append(dict(point))
        return {"x": point["x"] + 1000, "y": point["y"] + 2000}

    def move_and_click(self, plan, *, button="left"):
        self.calls.append(("move_and_click", plan.click_point.x, plan.click_point.y, button))

    def press(self, key):
        self.calls.append(("press", key))


class InputControlExecutorTest(unittest.TestCase):
    def test_dry_run_never_calls_backend_execute(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Oak tree",
            suggested_click_point={"x": 100, "y": 120},
            confidence=0.9,
        )

        result = execute_action(proposal, backend=backend, movement_profile="linear_debug", dry_run=True)

        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.dry_run)
        self.assertEqual(backend.calls, [])
        self.assertEqual(result.commands[0]["type"], "move_and_click")

    def test_canvas_click_point_is_transformed_before_planning(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Oak tree",
            suggested_click_point={"x": 100, "y": 120},
            click_point_space="canvas",
            confidence=0.9,
        )

        result = execute_action(proposal, backend=backend, movement_profile="instant_test", dry_run=True)

        self.assertEqual(result.status, "PASS")
        self.assertEqual(backend.converted_points, [{"x": 100, "y": 120}])
        self.assertEqual(result.commands[0]["clickPoint"]["x"], 1100)
        self.assertEqual(result.commands[0]["clickPoint"]["y"], 2120)

    def test_canvas_point_scaling_uses_client_width_ratio(self):
        point = scale_canvas_point_to_screen(
            {"x": 278, "y": 68},
            origin=(5762, 127),
            client_size=(1873, 1379),
            canvas_size=(765, 503),
        )

        self.assertEqual(point, {"x": 6443, "y": 293})

    def test_no_click_point_prevents_execution(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            status="WARN",
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Oak tree",
            confidence=0.5,
            missing_capabilities=["click_point"],
        )

        result = execute_action(proposal, backend=backend, movement_profile="linear_debug", dry_run=False)

        self.assertEqual(result.status, "FAIL")
        self.assertFalse(result.executed)
        self.assertEqual(backend.calls, [])

    def test_pyautogui_backend_can_be_mocked(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="close_bank",
            target_kind="bank_ui",
            target_name="Bank",
            key_action={"type": "key_press", "key": "escape"},
        )

        result = execute_action(proposal, backend=backend, movement_profile="linear_debug", dry_run=False)

        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.executed)
        self.assertEqual(backend.calls, [("press", "escape")])

    def test_execute_next_action_reports_daemon_unreachable_without_backend_calls(self):
        backend = FakeBackend()
        options = Namespace(timeout=0.01, backend="pyautogui", movement_profile="linear_debug", execute=False, verify_after_action=False)

        result = execute_next_action(
            "http://127.0.0.1:1",
            options,
            fetch_json_func=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("daemon down")),
            backend=backend,
        )

        self.assertEqual(result.status, "FAIL")
        self.assertEqual(result.proposed_action, "none")
        self.assertIn("daemon.status", result.missing_capabilities)
        self.assertEqual(backend.calls, [])


if __name__ == "__main__":
    unittest.main()

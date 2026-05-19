import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_action_lifecycle.py"
sys.path.insert(0, str(VIEWER_DIR))

from input_control.action_lifecycle import (
    build_lifecycle_diagnostic,
    verify_expected_result,
)
from input_control.executor import execute_action_loop, execute_next_action


def aim(x=100, y=120):
    return {"canvasX": x, "canvasY": y}


def resource_status(*, phase="target_selected", active_intent="select_target", tick=1):
    return {
        "latestTick": tick,
        "currentCycleStage": "collecting_resources",
        "brain": {
            "genericTaskState": {
                "phase": phase,
                "activeIntent": active_intent,
                "activeIntentTarget": {
                    "targetName": "Oak tree",
                    "classId": "tree",
                    "aimPoint": aim(110, 130),
                },
            },
            "inventoryContext": {"inventoryFull": False, "freeSlots": 12},
            "bankUiContext": {"bankOpen": False},
        },
    }


def close_bank_status(*, bank_open=True, tick=1):
    return {
        "latestTick": tick,
        "currentCycleStage": "close_bank_needed" if bank_open else "return_to_resource",
        "brain": {
            "genericTaskState": {
                "phase": "waiting_for_world_view" if bank_open else "return_to_resource",
                "activeIntent": "close_service_context" if bank_open else "select_target",
            },
            "bankUiContext": {"bankOpen": bank_open, "bankReadable": bank_open},
            "bankOperationContext": {"bankingComplete": True, "resourceItemsHeld": 0},
            "closeBankContext": {"closeBankNeeded": bank_open, "closeBankReady": bank_open, "keyboardClosePossible": True},
        },
    }


def deposit_status(*, held=4, banking_complete=False, tick=1):
    return {
        "latestTick": tick,
        "brain": {
            "genericTaskState": {"phase": "service_open", "activeIntent": "bank_operation_pending"},
            "bankUiContext": {
                "bankOpen": True,
                "bankReadable": True,
                "depositInventoryButtonVisible": True,
                "depositInventoryButtonBounds": {"x": 20, "y": 30, "width": 12, "height": 8},
            },
            "bankOperationContext": {
                "operationNeeded": held > 0,
                "operationType": "deposit_inventory",
                "resourceItemsHeld": held,
                "depositInventoryAvailable": True,
                "bankingComplete": banking_complete,
            },
        },
    }


class FakeBackend:
    name = "fake"

    def __init__(self):
        self.calls = []

    def current_position(self):
        return (0, 0)

    def canvas_to_screen_point(self, point):
        return {"x": point["x"] + 1000, "y": point["y"] + 2000}

    def move_and_click(self, plan, *, button="left"):
        self.calls.append(("move_and_click", plan.click_point.x, plan.click_point.y, button))

    def press(self, key):
        self.calls.append(("press", key))


class ActionLifecycleTest(unittest.TestCase):
    def test_after_select_resource_execution_lifecycle_enters_waiting_for_result(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            cooldown_ms=250,
            action_timeout_ms=1000,
        )
        statuses = [resource_status(), resource_status(phase="wait_for_result", active_intent="wait_for_result", tick=2)]

        result = execute_next_action(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.lifecycle_state["currentState"], "waiting_for_result")
        self.assertEqual(result.expected_result["resultType"], "wait_for_result_or_activity")
        self.assertEqual(result.observed_result["verificationStatus"], "PASS")

    def test_loop_does_not_run_second_action_while_waiting_for_result(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            cooldown_ms=1000,
            action_timeout_ms=5000,
            max_actions=2,
            max_runtime_seconds=0.2,
            stop_on_warn=False,
            stop_on_fail=True,
            seed=None,
        )
        statuses = [resource_status(tick=1)] + [
            resource_status(phase="wait_for_result", active_intent="wait_for_result", tick=tick)
            for tick in range(2, 20)
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=iter([0.0, 0.01, 0.02, 0.03, 0.21]).__next__,
        )

        payload = result.to_dict()
        self.assertEqual(payload["executedActionCount"], 1)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(payload["actionResults"][0]["lifecycleState"]["currentState"], "waiting_for_result")

    def test_loop_does_not_execute_when_starting_in_waiting_for_result(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            cooldown_ms=1000,
            action_timeout_ms=5000,
            max_actions=2,
            max_runtime_seconds=0.2,
            stop_on_warn=False,
            stop_on_fail=True,
            seed=None,
        )

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: resource_status(phase="wait_for_result", active_intent="wait_for_result"),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=iter([0.0, 0.01, 0.21]).__next__,
        )

        payload = result.to_dict()
        self.assertEqual(payload["executedActionCount"], 0)
        self.assertEqual(len(backend.calls), 0)
        self.assertEqual(payload["lifecycleState"]["currentState"], "waiting_for_result")

    def test_expected_result_verified_for_close_bank_when_bank_open_false(self):
        observed = verify_expected_result(
            "close_bank",
            close_bank_status(bank_open=True),
            close_bank_status(bank_open=False),
        )

        self.assertEqual(observed["verificationStatus"], "PASS")
        self.assertEqual(observed["observedResult"], "bank_closed")

    def test_expected_result_verified_for_deposit_when_resources_clear(self):
        observed = verify_expected_result(
            "deposit_inventory",
            deposit_status(held=4),
            deposit_status(held=0, banking_complete=True),
        )

        self.assertEqual(observed["verificationStatus"], "PASS")
        self.assertEqual(observed["observedResult"], "banking_complete")

    def test_timeout_transitions_to_timed_out(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            cooldown_ms=0,
            action_timeout_ms=1,
            max_actions=2,
            max_runtime_seconds=1.0,
            stop_on_warn=False,
            stop_on_fail=False,
            seed=None,
        )
        statuses = [resource_status(tick=1)] + [
            resource_status(phase="wait_for_result", active_intent="wait_for_result", tick=tick)
            for tick in range(2, 10)
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=iter([0.0, 0.01, 0.02, 0.03, 0.5]).__next__,
        )

        self.assertEqual(result.to_dict()["lifecycleState"]["currentState"], "timed_out")

    def test_loop_stops_at_max_actions(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            cooldown_ms=0,
            action_timeout_ms=1000,
            max_actions=1,
            max_runtime_seconds=5.0,
            stop_on_warn=False,
            stop_on_fail=True,
            seed=None,
        )
        statuses = [close_bank_status(bank_open=True, tick=1), close_bank_status(bank_open=False, tick=2)]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=iter([0.0, 0.01, 0.02]).__next__,
        )

        self.assertEqual(result.to_dict()["executedActionCount"], 1)
        self.assertEqual(result.to_dict()["reason"], "max_actions_reached")

    def test_loop_stops_at_max_runtime(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=False,
            after_action_wait_ms=0,
            cooldown_ms=0,
            action_timeout_ms=1000,
            max_actions=2,
            max_runtime_seconds=0.0,
            stop_on_warn=False,
            stop_on_fail=True,
            seed=None,
        )

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: resource_status(),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=iter([0.0, 0.1]).__next__,
        )

        self.assertEqual(result.to_dict()["executedActionCount"], 0)
        self.assertEqual(result.to_dict()["reason"], "max_runtime_reached")

    def test_wait_for_context_does_not_execute(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=False,
            after_action_wait_ms=0,
            cooldown_ms=0,
            action_timeout_ms=1000,
        )
        status = {"brain": {"genericTaskState": {"phase": "needs_more_context", "activeIntent": "select_target"}}}

        result = execute_next_action(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: status,
            backend=backend,
        )

        self.assertFalse(result.executed)
        self.assertEqual(backend.calls, [])
        self.assertEqual(result.lifecycle_state["currentState"], "blocked")

    def test_one_shot_does_not_execute_while_current_state_waits_for_result(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=False,
            after_action_wait_ms=0,
            cooldown_ms=0,
            action_timeout_ms=1000,
        )

        result = execute_next_action(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: resource_status(phase="wait_for_result", active_intent="wait_for_result"),
            backend=backend,
        )

        self.assertFalse(result.executed)
        self.assertEqual(backend.calls, [])
        self.assertEqual(result.lifecycle_state["currentState"], "waiting_for_result")
        self.assertIn("already waiting for previous action result", result.warnings)

    def test_lifecycle_diagnostic_json_has_schema(self):
        payload = build_lifecycle_diagnostic(resource_status(phase="wait_for_result", active_intent="wait_for_result"))

        self.assertEqual(payload["schema"], "action_lifecycle_diagnostic.v1")
        self.assertEqual(payload["lifecycleState"]["currentState"], "waiting_for_result")

    def test_diagnostic_cli_writes_no_files(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--from-daemon",
                    "--daemon-url",
                    "http://127.0.0.1:1",
                    "--timeout",
                    "0.01",
                    "--json",
                ],
                cwd=temp,
                capture_output=True,
                text=True,
                check=False,
            )
            after = set(os.listdir(temp))

        self.assertNotEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema"], "action_lifecycle_diagnostic.v1")
        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

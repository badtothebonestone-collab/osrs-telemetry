import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_action_lifecycle.py"
sys.path.insert(0, str(VIEWER_DIR))

from input_control.action_lifecycle import (
    build_lifecycle_diagnostic,
    verify_expected_result,
)
from input_control.action_proposal import ActionProposal
from input_control.executor import execute_action_loop, execute_next_action
from diagnose_action_lifecycle import format_human as format_lifecycle_human


def aim(x=100, y=120):
    return {"canvasX": x, "canvasY": y}


def resource_status(
    *,
    phase="target_selected",
    active_intent="select_target",
    tick=1,
    free_slots=12,
    inventory_full=False,
    held_count=0,
    progress_count=0,
    current_activity="idle",
    recent_task_signals=None,
    blocking_conditions=None,
):
    return {
        "latestTick": tick,
        "currentCycleStage": "collecting_resources",
        "brain": {
            "genericTaskState": {
                "phase": phase,
                "activeIntent": active_intent,
                "blockingConditions": list(blocking_conditions or []),
                "activeIntentTarget": {
                    "targetName": "Tree",
                    "classId": "tree",
                    "id": 1278,
                    "aimPoint": aim(110, 130),
                },
            },
            "inventoryContext": {
                "inventoryFull": inventory_full,
                "freeSlots": free_slots,
                "progress": {
                    "currentHeldCount": held_count,
                    "displayedGoalProgress": progress_count,
                    "currentInventorySignature": f"slots={free_slots};held={held_count}",
                },
            },
            "activityContext": {
                "currentActivity": current_activity,
                "recentTaskSignals": list(recent_task_signals or []),
            },
            "bankUiContext": {"bankOpen": False},
        },
        "inventoryFull": inventory_full,
        "inventoryFreeSlots": free_slots,
        "brainCurrentHeldCount": held_count,
        "brainProgress": {
            "currentHeldCount": held_count,
            "displayedGoalProgress": progress_count,
            "currentInventorySignature": f"slots={free_slots};held={held_count}",
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


def route_status(*, plane=0, tick=1, route_step_index=1):
    return {
        "latestTick": tick,
        "brain": {
            "genericTaskState": {"phase": "needs_service", "activeIntent": "needs_service"},
            "playerContext": {"worldX": 3205, "worldY": 3229, "plane": plane},
            "serviceRouteContext": {
                "schema": "service_route_context.v1",
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "currentStepIndex": route_step_index,
                "currentStep": {
                    "type": "interact_object",
                    "label": "first stairs up",
                    "plane": 0,
                    "planeChange": "+1",
                },
                "visibleInteractionTarget": {
                    "targetName": "Staircase",
                    "id": 56230,
                    "plane": 0,
                    "expectedPlaneChange": "+1",
                },
            },
        },
    }


def return_route_status(*, plane=2, tick=1, route_step_index=0, active_dialogue=False):
    status = {
        "latestTick": tick,
        "currentCycleStage": "return_to_resource",
        "brain": {
            "genericTaskState": {"phase": "return_to_resource", "activeIntent": "return_to_resource_area"},
            "playerContext": {"worldX": 3206, "worldY": 3221, "plane": plane},
            "returnRouteContext": {
                "schema": "return_route_context.v1",
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                "returnRouteId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                "currentStepIndex": route_step_index,
                "currentStep": {
                    "type": "interact_object",
                    "label": "bank floor stairs down",
                    "plane": 2,
                    "planeChange": "-1",
                },
                "visibleInteractionTarget": {
                    "targetName": "Staircase",
                    "id": 16672,
                    "plane": plane,
                    "expectedPlaneChange": "-1",
                },
            },
        },
    }
    if active_dialogue:
        status["brain"]["dialogueState"] = {
            "active": True,
            "type": "options",
            "promptText": "Climb up or down the stairs?",
            "options": [
                {"index": 1, "key": "1", "text": "Climb up the stairs."},
                {"index": 2, "key": "2", "text": "Climb down the stairs."},
            ],
        }
    return status


def navigation_status(
    *,
    tick=1,
    x=3196,
    y=3248,
    plane=0,
    service_distance=19,
    path_distance=4,
    phase="needs_service",
    active_intent="navigate_to_service",
    service_ready=False,
    route_action_ready=False,
    route_node="lumbridge_castle_west_approach",
):
    return {
        "latestTick": tick,
        "currentCycleStage": "needs_service",
        "brain": {
            "genericTaskState": {"phase": phase, "activeIntent": active_intent},
            "playerContext": {"worldX": x, "worldY": y, "plane": plane},
            "serviceContext": {
                "serviceReady": service_ready,
                "distanceToServiceTarget": service_distance,
            },
            "pathingContext": {
                "pathingNeeded": not service_ready,
                "distanceToDestination": service_distance,
                "distanceToPathTarget": path_distance,
            },
            "serviceRouteContext": {
                "actionReady": route_action_ready,
                "currentNodeId": route_node,
                "visibleInteractionTarget": {"targetName": "Staircase"} if route_action_ready else None,
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

    def test_select_resource_progress_wins_when_inventory_becomes_full(self):
        before = resource_status(free_slots=1, held_count=12, progress_count=4)
        after = resource_status(
            phase="inventory_full",
            active_intent="needs_service",
            free_slots=0,
            inventory_full=True,
            held_count=13,
            progress_count=5,
        )
        after["serviceContext"] = {"serviceReady": True}

        observed = verify_expected_result(
            "select_resource_target",
            before,
            after,
            elapsed_ms=15000,
            timeout_ms=15000,
        )

        self.assertEqual(observed["verificationStatus"], "PASS")
        self.assertEqual(observed["observedResult"], "inventory_changed")
        self.assertEqual(observed["resultOutcome"], "success")
        self.assertIn("held_resource_count_increased", observed["observedSignals"])
        self.assertNotIn("unexpected_service_context", observed["observedSignals"])

    def test_select_resource_inventory_gain_wins_over_blocked_full_transition(self):
        before = resource_status(free_slots=1, held_count=12, progress_count=4)
        after = resource_status(
            phase="blocked",
            active_intent="needs_service",
            free_slots=0,
            inventory_full=True,
            held_count=13,
            progress_count=5,
            blocking_conditions=["inventory_full"],
        )

        observed = verify_expected_result(
            "select_resource_target",
            before,
            after,
            elapsed_ms=15000,
            timeout_ms=15000,
        )

        self.assertEqual(observed["verificationStatus"], "PASS")
        self.assertEqual(observed["observedResult"], "inventory_changed")
        self.assertEqual(observed["resultOutcome"], "success")
        self.assertIn("held_resource_count_increased", observed["observedSignals"])
        self.assertNotIn("blocked_phase", observed["observedSignals"])

    def test_service_route_object_expects_transition_progress(self):
        expected = verify_expected_result(
            "interact_service_route_object",
            route_status(plane=0, tick=10),
            route_status(plane=1, tick=11, route_step_index=2),
            elapsed_ms=700,
            timeout_ms=3000,
        )

        self.assertEqual(expected["verificationStatus"], "PASS")
        self.assertEqual(expected["observedResult"], "route_transition_progress")
        self.assertIn("player_plane_changed", expected["observedSignals"])
        self.assertIn("route_step_changed", expected["observedSignals"])

    def test_return_route_object_reports_return_transition_plane_change(self):
        expected = verify_expected_result(
            "interact_service_route_object",
            return_route_status(plane=2, tick=10),
            return_route_status(plane=1, tick=11, route_step_index=1),
            elapsed_ms=700,
            timeout_ms=3000,
        )

        self.assertEqual(expected["verificationStatus"], "PASS")
        self.assertEqual(expected["observedResult"], "return_transition_plane_changed")
        self.assertIn("player_plane_changed", expected["observedSignals"])

    def test_return_route_generic_climb_can_open_down_dialogue(self):
        expected = verify_expected_result(
            "interact_service_route_object",
            return_route_status(plane=1, tick=10, route_step_index=1),
            return_route_status(plane=1, tick=11, route_step_index=1, active_dialogue=True),
            elapsed_ms=700,
            timeout_ms=3000,
        )

        self.assertEqual(expected["verificationStatus"], "PASS")
        self.assertEqual(expected["observedResult"], "return_transition_dialogue_opened")
        self.assertIn("route_transition_dialogue_opened", expected["observedSignals"])

    def test_return_dialogue_choice_reports_down_transition_selected(self):
        expected = verify_expected_result(
            "interface_dialogue_choice",
            return_route_status(plane=1, tick=10, route_step_index=1, active_dialogue=True),
            return_route_status(plane=0, tick=11, route_step_index=2),
            elapsed_ms=700,
            timeout_ms=3000,
        )

        self.assertEqual(expected["verificationStatus"], "PASS")
        self.assertEqual(expected["observedResult"], "return_transition_dialogue_choice_selected")
        self.assertIn("player_plane_changed", expected["observedSignals"])

    def test_return_transition_with_pathing_active_remains_pending_not_timeout(self):
        before = return_route_status(plane=1, tick=10, route_step_index=1)
        after = return_route_status(plane=1, tick=14, route_step_index=1)
        after["brain"]["pathingContext"] = {
            "movementState": "moving",
            "localDestination": {"worldX": 3206, "worldY": 3208, "plane": 1},
        }

        expected = verify_expected_result(
            "interact_service_route_object",
            before,
            after,
            elapsed_ms=6000,
            timeout_ms=5000,
            wait_started_tick=10,
            timeout_ticks=4,
        )

        self.assertEqual(expected["verificationStatus"], "WARN")
        self.assertEqual(expected["observedResult"], "return_transition_pending")
        self.assertEqual(expected["resultOutcome"], "still_waiting")
        self.assertFalse(expected["resultComplete"])
        self.assertFalse(expected["nextActionAllowed"])
        self.assertIn("pathing_started", expected["observedSignals"])

    def test_generic_route_transition_click_can_open_dialogue(self):
        after = route_status(plane=1, tick=11, route_step_index=4)
        after["brain"]["dialogueState"] = {
            "active": True,
            "type": "options",
            "promptText": "Climb up or down the stairs?",
            "options": [{"index": 1, "key": "1", "text": "Climb up the stairs."}],
        }

        expected = verify_expected_result(
            "interact_service_route_object",
            route_status(plane=1, tick=10, route_step_index=4),
            after,
            elapsed_ms=700,
            timeout_ms=3000,
        )

        self.assertEqual(expected["verificationStatus"], "PASS")
        self.assertEqual(expected["observedResult"], "route_transition_dialogue_opened")
        self.assertIn("route_transition_dialogue_opened", expected["observedSignals"])

    def test_dialogue_choice_expects_transition_progress(self):
        expected = verify_expected_result(
            "interface_dialogue_choice",
            route_status(plane=1, tick=10, route_step_index=4),
            route_status(plane=2, tick=11, route_step_index=5),
            elapsed_ms=700,
            timeout_ms=3000,
        )

        self.assertEqual(expected["verificationStatus"], "PASS")
        self.assertEqual(expected["observedResult"], "route_transition_dialogue_choice_selected")
        self.assertIn("player_plane_changed", expected["observedSignals"])

    def test_navigation_progress_when_player_moves_and_service_distance_improves(self):
        observed = verify_expected_result(
            "navigate_to_service",
            navigation_status(tick=10, x=3196, y=3248, service_distance=19, path_distance=4),
            navigation_status(tick=12, x=3201, y=3242, service_distance=13, path_distance=1),
            elapsed_ms=1600,
            timeout_ms=2500,
            wait_started_tick=10,
            timeout_ticks=4,
            progress_min_distance=1,
        )

        self.assertEqual(observed["verificationStatus"], "PASS")
        self.assertEqual(observed["observedResult"], "service_navigation_progress")
        self.assertEqual(observed["resultOutcome"], "progress")
        self.assertTrue(observed["resultComplete"])
        self.assertIn("player_tile_changed", observed["observedSignals"])
        self.assertIn("service_distance_decreased", observed["observedSignals"])
        self.assertIn("path_target_distance_decreased", observed["observedSignals"])

    def test_return_navigation_rejects_movement_away_from_clicked_waypoint(self):
        before = navigation_status(
            tick=10,
            x=3206,
            y=3231,
            service_distance=12,
            path_distance=1,
            phase="return_to_resource",
            active_intent="return_to_resource_area",
        )
        after = navigation_status(
            tick=12,
            x=3212,
            y=3231,
            service_distance=8,
            path_distance=7,
            phase="return_to_resource",
            active_intent="return_to_resource_area",
        )
        before["brain"]["pathingContext"]["distanceToDestination"] = 1
        after["brain"]["pathingContext"]["distanceToDestination"] = 7
        proposal = ActionProposal(
            proposed_action="return_to_resource_area",
            target_kind="path_tile",
            target_tile={"worldX": 3205, "worldY": 3231, "plane": 0},
        )

        observed = verify_expected_result(
            "return_to_resource_area",
            before,
            after,
            elapsed_ms=1800,
            timeout_ms=3000,
            wait_started_tick=10,
            timeout_ticks=4,
            progress_min_distance=1,
            proposal=proposal,
        )

        self.assertEqual(observed["verificationStatus"], "FAIL")
        self.assertEqual(observed["observedResult"], "resource_return_wrong_way")
        self.assertEqual(observed["resultOutcome"], "no_change_timeout")
        self.assertIn("clicked_waypoint_distance_increased", observed["observedSignals"])
        self.assertIn("route_wrong_way", observed["observedSignals"])
        self.assertIn("service_distance_decreased", observed["observedSignals"])

    def test_navigation_progress_reads_player_from_current_context_summary(self):
        before = navigation_status(tick=10, x=3241, y=3248, service_distance=36, path_distance=3)
        after = navigation_status(tick=12, x=3241, y=3245, service_distance=36, path_distance=3)
        before["brain"].pop("playerContext")
        after["brain"].pop("playerContext")
        before["brain"]["currentContextSummary"] = {"player": {"worldX": 3241, "worldY": 3248, "plane": 0}}
        after["brain"]["currentContextSummary"] = {"player": {"worldX": 3241, "worldY": 3245, "plane": 0}}

        observed = verify_expected_result(
            "navigate_to_service",
            before,
            after,
            elapsed_ms=1600,
            timeout_ms=2500,
            wait_started_tick=10,
            timeout_ticks=4,
            progress_min_distance=1,
        )

        self.assertEqual(observed["verificationStatus"], "PASS")
        self.assertEqual(observed["observedResult"], "service_navigation_progress")
        self.assertIn("player_tile_changed", observed["observedSignals"])

    def test_navigation_wait_state_without_movement_is_not_complete(self):
        observed = verify_expected_result(
            "navigate_to_service",
            navigation_status(tick=10),
            navigation_status(tick=11, phase="wait_for_result", active_intent="navigate_to_service"),
            elapsed_ms=500,
            timeout_ms=2500,
            wait_started_tick=10,
            timeout_ticks=4,
            progress_min_distance=1,
        )

        self.assertEqual(observed["verificationStatus"], "WARN")
        self.assertEqual(observed["observedResult"], "service_navigation_clicked_waiting")
        self.assertFalse(observed["resultComplete"])
        self.assertFalse(observed["nextActionAllowed"])
        self.assertIn("movement_or_wait_state", observed["observedSignals"])

    def test_navigation_reacquired_route_object_counts_as_progress(self):
        observed = verify_expected_result(
            "navigate_to_service",
            navigation_status(tick=10, route_action_ready=False),
            navigation_status(tick=12, route_action_ready=True),
            elapsed_ms=1200,
            timeout_ms=2500,
            wait_started_tick=10,
        )

        self.assertEqual(observed["verificationStatus"], "PASS")
        self.assertEqual(observed["observedResult"], "service_route_object_reacquired")
        self.assertIn("route_object_reacquired", observed["observedSignals"])

    def test_navigation_route_node_change_counts_as_progress(self):
        observed = verify_expected_result(
            "navigate_to_service",
            navigation_status(tick=10, route_node="lumbridge_castle_west_approach"),
            navigation_status(tick=12, route_node="lumbridge_castle_entrance_or_courtyard"),
            elapsed_ms=1200,
            timeout_ms=2500,
            wait_started_tick=10,
            timeout_ticks=4,
            progress_min_distance=3,
        )

        self.assertEqual(observed["verificationStatus"], "PASS")
        self.assertEqual(observed["observedResult"], "service_navigation_progress")
        self.assertIn("route_node_changed", observed["observedSignals"])
        self.assertEqual(observed["routeNodeBefore"], "lumbridge_castle_west_approach")
        self.assertEqual(observed["routeNodeAfter"], "lumbridge_castle_entrance_or_courtyard")

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
            max_runtime_seconds=0.5,
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
            monotonic_func=iter([0.0, 0.01, 0.02, 0.03, 0.51]).__next__,
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

    def test_open_service_pathing_to_object_is_progress_not_dead_wait(self):
        before = navigation_status(tick=10, x=3206, y=3218, plane=2, service_distance=5, path_distance=4)
        after = navigation_status(
            tick=12,
            x=3207,
            y=3220,
            plane=2,
            service_distance=3,
            path_distance=1,
            phase="wait_for_result",
            active_intent="open_service",
        )
        before["brain"]["bankUiContext"] = {"bankOpen": False, "bankReadable": False}
        after["brain"]["bankUiContext"] = {"bankOpen": False, "bankReadable": False}

        observed = verify_expected_result(
            "open_service",
            before,
            after,
            elapsed_ms=900,
            timeout_ms=3000,
            wait_started_tick=10,
            timeout_ticks=6,
            progress_min_distance=1,
        )

        self.assertEqual(observed["verificationStatus"], "WARN")
        self.assertEqual(observed["observedResult"], "service_object_pathing_to_object")
        self.assertEqual(observed["resultOutcome"], "still_waiting")
        self.assertFalse(observed["resultComplete"])
        self.assertFalse(observed["nextActionAllowed"])
        self.assertIn("player_tile_changed", observed["observedSignals"])
        self.assertIn("service_distance_decreased", observed["observedSignals"])

    def test_inventory_change_completes_select_resource_target_wait(self):
        observed = verify_expected_result(
            "select_resource_target",
            resource_status(free_slots=12, held_count=0, progress_count=0),
            resource_status(phase="wait_for_result", active_intent="wait_for_result", free_slots=11, held_count=1, progress_count=1),
        )

        self.assertTrue(observed["resultComplete"])
        self.assertEqual(observed["resultOutcome"], "success")
        self.assertTrue(observed["nextActionAllowed"])
        self.assertIn("inventory_changed", observed["observedSignals"])
        self.assertIn("held_resource_count_increased", observed["observedSignals"])

    def test_resource_view_recovery_fails_when_projection_stays_sentinel(self):
        target = {
            "targetName": "Tree",
            "safeAimPoint": {
                "status": "FAIL",
                "actionable": False,
                "rawAimPoint": {"x": 2147483648, "y": 2147483648},
            },
            "bounds": {"x": 2147483647, "y": 2147483647, "w": 1, "h": 1},
        }

        observed = verify_expected_result(
            "resource_view_recovery",
            {"selectedHighlighterTarget": target, "latestTick": 10},
            {"selectedHighlighterTarget": target, "latestTick": 12},
            elapsed_ms=2500,
            timeout_ms=2000,
        )

        self.assertEqual(observed["observedResult"], "resource_projection_recovery_failed")
        self.assertEqual(observed["resultOutcome"], "no_change_timeout")
        self.assertEqual(observed["verificationStatus"], "FAIL")
        self.assertEqual(observed["projectionBefore"]["classification"], "projection_sentinel")
        self.assertEqual(observed["projectionAfter"]["classification"], "projection_sentinel")

    def test_resource_view_recovery_succeeds_when_safe_aimpoint_appears(self):
        before = {
            "targetName": "Tree",
            "safeAimPoint": {
                "status": "FAIL",
                "actionable": False,
                "rawAimPoint": {"x": 2147483648, "y": 2147483648},
            },
            "bounds": {"x": 2147483647, "y": 2147483647, "w": 1, "h": 1},
        }
        after = {
            "targetName": "Tree",
            "safeAimPoint": {"status": "PASS", "actionable": True, "canvasX": 200, "canvasY": 180},
            "aimPoint": {"canvasX": 200, "canvasY": 180},
        }

        observed = verify_expected_result(
            "resource_view_recovery",
            {"selectedHighlighterTarget": before, "latestTick": 10},
            {"selectedHighlighterTarget": after, "latestTick": 11},
            elapsed_ms=300,
            timeout_ms=2000,
        )

        self.assertEqual(observed["observedResult"], "resource_camera_reacquire_success")
        self.assertEqual(observed["resultOutcome"], "progress")
        self.assertIn("resource_safe_aimpoint_available", observed["observedSignals"])

    def test_service_view_recovery_succeeds_when_safe_aimpoint_appears(self):
        before = {
            "brain": {
                "serviceRouteContext": {
                    "visibleServiceTarget": {
                        "targetName": "Bank Deposit Box",
                        "classId": "deposit_box",
                        "projectionStatus": {
                            "classification": "offscreen",
                            "onScreen": False,
                            "actionableByCanvas": False,
                            "aimPoint": {"canvasX": 1793, "canvasY": 1935},
                        },
                    }
                }
            },
            "latestTick": 10,
        }
        after = {
            "brain": {
                "serviceRouteContext": {
                    "visibleServiceTarget": {
                        "targetName": "Bank Deposit Box",
                        "classId": "deposit_box",
                        "projectionStatus": {
                            "classification": "actionable",
                            "onScreen": True,
                            "actionableByCanvas": True,
                            "aimPoint": {"canvasX": 380, "canvasY": 220},
                        },
                        "safeAimPoint": {"status": "PASS", "actionable": True, "canvasX": 380, "canvasY": 220},
                    }
                }
            },
            "latestTick": 11,
        }

        observed = verify_expected_result(
            "service_view_recovery",
            before,
            after,
            elapsed_ms=320,
            timeout_ms=2000,
        )

        self.assertEqual(observed["observedResult"], "service_camera_reacquire_success")
        self.assertEqual(observed["resultOutcome"], "progress")
        self.assertIn("service_safe_aimpoint_available", observed["observedSignals"])

    def test_service_view_recovery_does_not_succeed_from_actionability_only(self):
        target = {
            "targetName": "Bank Deposit Box",
            "classId": "deposit_box",
            "actionability": "needs_hover_confirmation",
            "projectionStatus": {
                "classification": "projection_sentinel",
                "onScreen": False,
                "actionableByCanvas": False,
                "aimPoint": {"canvasX": 2147483648, "canvasY": 2147483648},
            },
            "safeAimPoint": {
                "status": "FAIL",
                "actionable": False,
                "rawAimPoint": {"x": 2147483648, "y": 2147483648},
                "rejectionReason": "projection_sentinel",
            },
        }
        before = {
            "brain": {"serviceRouteContext": {"visibleServiceTarget": target}},
            "latestTick": 10,
        }
        after = {
            "brain": {"serviceRouteContext": {"visibleServiceTarget": target}},
            "latestTick": 11,
        }

        observed = verify_expected_result(
            "service_view_recovery",
            before,
            after,
            elapsed_ms=2500,
            timeout_ms=2000,
        )

        self.assertEqual(observed["observedResult"], "service_view_recovery_failed")
        self.assertEqual(observed["resultOutcome"], "no_change_timeout")
        self.assertNotIn("service_safe_aimpoint_available", observed["observedSignals"])

    def test_service_view_recovery_does_not_succeed_from_edge_sliver(self):
        before = {
            "brain": {
                "serviceRouteContext": {
                    "visibleServiceTarget": {
                        "targetName": "Bank Deposit Box",
                        "classId": "deposit_box",
                        "projectionStatus": {
                            "classification": "offscreen",
                            "onScreen": False,
                            "actionableByCanvas": False,
                            "aimPoint": {"canvasX": 1793, "canvasY": 1935},
                        },
                    }
                }
            },
            "latestTick": 10,
        }
        after = {
            "brain": {
                "serviceRouteContext": {
                    "visibleServiceTarget": {
                        "targetName": "Bank Deposit Box",
                        "classId": "deposit_box",
                        "projectionStatus": {
                            "classification": "actionable",
                            "onScreen": True,
                            "actionableByCanvas": True,
                            "aimPoint": {"canvasX": 6, "canvasY": 93},
                        },
                        "safeAimPoint": {
                            "status": "PASS",
                            "actionable": True,
                            "canvasX": 6,
                            "canvasY": 93,
                            "distanceToViewportEdgePx": 6,
                            "clippedVisibleAreaPx": 756.0,
                            "clippedVisibleAreaRatio": 0.75,
                            "rawCenterInsideViewport": False,
                        },
                    }
                }
            },
            "latestTick": 11,
        }

        observed = verify_expected_result(
            "service_view_recovery",
            before,
            after,
            elapsed_ms=320,
            timeout_ms=2000,
        )

        self.assertEqual(observed["observedResult"], "service_insufficient_exposure")
        self.assertEqual(observed["serviceViewRecoveryClassification"], "service_insufficient_exposure")
        self.assertEqual(observed["resultOutcome"], "still_waiting")
        self.assertFalse(observed["resultComplete"])
        self.assertFalse(observed["nextActionAllowed"])
        self.assertIn("service_edge_sliver_only", observed["observedSignals"])
        self.assertNotEqual(observed["resultOutcome"], "success")

    def test_service_view_recovery_timeout_with_partial_exposure_allows_next_camera_primitive(self):
        before = {
            "brain": {
                "serviceRouteContext": {
                    "visibleServiceTarget": {
                        "targetName": "Bank booth",
                        "classId": "bank_booth",
                        "projectionStatus": {
                            "classification": "offscreen",
                            "onScreen": False,
                            "actionableByCanvas": False,
                            "aimPoint": {"canvasX": 1560, "canvasY": 2471},
                        },
                    }
                }
            },
            "latestTick": 10,
        }
        after = {
            "brain": {
                "serviceRouteContext": {
                    "visibleServiceTarget": {
                        "targetName": "Bank booth",
                        "classId": "bank_booth",
                        "projectionStatus": {
                            "classification": "actionable",
                            "onScreen": True,
                            "actionableByCanvas": True,
                            "aimPoint": {"canvasX": 60, "canvasY": 236},
                        },
                        "safeAimPoint": {
                            "status": "PASS",
                            "actionable": True,
                            "canvasX": 60,
                            "canvasY": 236,
                            "distanceToViewportEdgePx": 24,
                            "clippedVisibleAreaPx": 500.0,
                            "clippedVisibleAreaRatio": 0.2,
                        },
                    }
                }
            },
            "latestTick": 11,
        }

        observed = verify_expected_result(
            "service_view_recovery",
            before,
            after,
            elapsed_ms=2500,
            timeout_ms=2000,
        )

        self.assertEqual(observed["observedResult"], "service_insufficient_exposure")
        self.assertEqual(observed["resultOutcome"], "progress")
        self.assertTrue(observed["resultComplete"])
        self.assertTrue(observed["nextActionAllowed"])
        self.assertTrue(observed["serviceViewRecoveryPartialProgress"])
        self.assertNotEqual(observed["resultOutcome"], "success")

    def test_service_view_recovery_fails_if_loaded_scene_lost(self):
        before = {
            "brain": {
                "serviceRouteContext": {
                    "visibleServiceTarget": {
                        "targetName": "Bank Deposit Box",
                        "projectionStatus": {
                            "classification": "offscreen",
                            "onScreen": False,
                            "actionableByCanvas": False,
                            "aimPoint": {"canvasX": 1793, "canvasY": 1935},
                        },
                    }
                }
            },
            "gameState": "LOGGED_IN",
            "latestTick": 10,
        }
        after = {
            "gameState": "LOGIN_SCREEN",
            "loadedSceneVerified": False,
            "worldModelObjectTotal": 0,
            "latestTick": 10,
            "brain": {
                "serviceRouteContext": {
                    "visibleServiceTarget": {
                        "targetName": "Bank Deposit Box",
                        "projectionStatus": {
                            "classification": "actionable",
                            "onScreen": True,
                            "actionableByCanvas": True,
                            "aimPoint": {"canvasX": 380, "canvasY": 220},
                        },
                        "safeAimPoint": {"status": "PASS", "actionable": True, "canvasX": 380, "canvasY": 220},
                    }
                }
            },
        }

        observed = verify_expected_result(
            "service_view_recovery",
            before,
            after,
            elapsed_ms=320,
            timeout_ms=2000,
        )

        self.assertEqual(observed["observedResult"], "service_view_recovery_liveness_lost")
        self.assertEqual(observed["verificationStatus"], "FAIL")
        self.assertEqual(observed["resultOutcome"], "interrupted")
        self.assertIn("loaded_scene_unavailable", observed["observedSignals"])

    def test_service_view_recovery_not_success_if_post_camera_proposal_still_recovery(self):
        before = {
            "brain": {
                "serviceRouteContext": {
                    "visibleServiceTarget": {
                        "targetName": "Bank Deposit Box",
                        "classId": "deposit_box",
                        "projectionStatus": {
                            "classification": "offscreen",
                            "onScreen": False,
                            "actionableByCanvas": False,
                            "aimPoint": {"canvasX": 1793, "canvasY": 1935},
                        },
                    }
                }
            },
            "latestTick": 10,
        }
        after = {
            "brain": {
                "serviceRouteContext": {
                    "visibleServiceTarget": {
                        "targetName": "Bank Deposit Box",
                        "classId": "deposit_box",
                        "projectionStatus": {
                            "classification": "actionable",
                            "onScreen": True,
                            "actionableByCanvas": True,
                            "aimPoint": {"canvasX": 380, "canvasY": 220},
                        },
                        "safeAimPoint": {"status": "PASS", "actionable": True, "canvasX": 380, "canvasY": 220},
                    }
                }
            },
            "latestTick": 11,
        }

        with patch(
            "input_control.action_lifecycle.build_action_proposal",
            return_value=Namespace(
                proposed_action="service_view_recovery",
                reason="service_view_recovery_needed",
                target_name="Bank Deposit Box",
            ),
        ):
            observed = verify_expected_result(
                "service_view_recovery",
                before,
                after,
                elapsed_ms=320,
                timeout_ms=2000,
            )

        self.assertEqual(observed["observedResult"], "service_recovery_still_required")
        self.assertEqual(observed["verificationStatus"], "WARN")
        self.assertEqual(observed["resultOutcome"], "still_waiting")
        self.assertFalse(observed["resultComplete"])

    def test_progress_increase_completes_select_resource_target_wait(self):
        observed = verify_expected_result(
            "select_resource_target",
            resource_status(progress_count=1),
            resource_status(phase="wait_for_result", active_intent="wait_for_result", progress_count=2),
        )

        self.assertTrue(observed["resultComplete"])
        self.assertEqual(observed["resultOutcome"], "progress")
        self.assertIn("resource_progress_increased", observed["observedSignals"])

    def test_chopping_activity_signal_marks_progress(self):
        observed = verify_expected_result(
            "select_resource_target",
            resource_status(current_activity="idle"),
            resource_status(phase="wait_for_result", active_intent="wait_for_result", current_activity="animating"),
        )

        self.assertTrue(observed["resultComplete"])
        self.assertEqual(observed["resultOutcome"], "progress")
        self.assertIn("activity_animating", observed["observedSignals"])

    def test_depletion_signal_completes_with_depleted_outcome(self):
        observed = verify_expected_result(
            "select_resource_target",
            resource_status(),
            resource_status(recent_task_signals=["target depleted recently"]),
        )

        self.assertTrue(observed["resultComplete"])
        self.assertEqual(observed["resultOutcome"], "depleted")
        self.assertIn("target_depleted_recently", observed["observedSignals"])

    def test_no_change_before_timeout_remains_still_waiting(self):
        observed = verify_expected_result(
            "select_resource_target",
            resource_status(),
            resource_status(phase="wait_for_result", active_intent="wait_for_result"),
            elapsed_ms=250,
            timeout_ms=1000,
        )

        self.assertFalse(observed["resultComplete"])
        self.assertEqual(observed["resultOutcome"], "still_waiting")
        self.assertFalse(observed["nextActionAllowed"])

    def test_no_change_after_timeout_becomes_timed_out(self):
        observed = verify_expected_result(
            "select_resource_target",
            resource_status(),
            resource_status(phase="wait_for_result", active_intent="wait_for_result"),
            elapsed_ms=1250,
            timeout_ms=1000,
        )

        self.assertTrue(observed["resultComplete"])
        self.assertEqual(observed["resultOutcome"], "no_change_timeout")
        self.assertEqual(observed["resourceProgressClassification"], "resource_timeout_no_progress")
        self.assertFalse(observed["nextActionAllowed"])

    def test_waiting_resource_click_has_pending_classification(self):
        observed = verify_expected_result(
            "select_resource_target",
            resource_status(),
            resource_status(phase="wait_for_result", active_intent="wait_for_result"),
            elapsed_ms=250,
            timeout_ms=1000,
        )

        self.assertEqual(observed["resourceProgressClassification"], "resource_click_confirmed_waiting")
        self.assertFalse(observed["resultComplete"])

    def test_blocked_phase_interrupts_select_resource_target_wait(self):
        observed = verify_expected_result(
            "select_resource_target",
            resource_status(),
            resource_status(phase="blocked", active_intent="needs_user_resolution", blocking_conditions=["bank_pin_required"]),
        )

        self.assertTrue(observed["resultComplete"])
        self.assertEqual(observed["resultOutcome"], "interrupted")
        self.assertFalse(observed["nextActionAllowed"])
        self.assertIn("blocked_phase", observed["observedSignals"])

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

    def test_loop_continues_after_select_resource_result_completes(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            cooldown_ms=0,
            action_timeout_ms=5000,
            max_actions=2,
            max_runtime_seconds=5.0,
            stop_on_warn=False,
            stop_on_fail=True,
            seed=None,
        )
        statuses = [
            resource_status(tick=1, held_count=0, progress_count=0),
            resource_status(phase="wait_for_result", active_intent="wait_for_result", tick=2, held_count=0, progress_count=0),
            resource_status(phase="wait_for_result", active_intent="wait_for_result", tick=3, held_count=1, progress_count=1),
            resource_status(phase="target_selected", active_intent="select_target", tick=4, held_count=1, progress_count=1),
            resource_status(phase="wait_for_result", active_intent="wait_for_result", tick=5, held_count=1, progress_count=1),
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=iter([0.0, 0.01, 0.02, 0.03, 0.04, 0.05]).__next__,
        )

        payload = result.to_dict()
        self.assertEqual(payload["executedActionCount"], 2)
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(payload["actionResults"][0]["observedResult"]["resultOutcome"], "success")

    def test_stop_after_inventory_changes_stops_loop(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            cooldown_ms=0,
            action_timeout_ms=5000,
            result_timeout_ms=5000,
            max_actions=5,
            max_runtime_seconds=5.0,
            stop_on_warn=False,
            stop_on_fail=True,
            stop_after_inventory_changes=2,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
        )
        statuses = [
            resource_status(tick=1, free_slots=12, held_count=0, progress_count=0),
            resource_status(phase="wait_for_result", active_intent="wait_for_result", tick=2, free_slots=11, held_count=1, progress_count=1),
            resource_status(tick=3, free_slots=11, held_count=1, progress_count=1),
            resource_status(phase="wait_for_result", active_intent="wait_for_result", tick=4, free_slots=10, held_count=2, progress_count=2),
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=iter([0.0, 0.01, 0.02, 0.03]).__next__,
        )

        payload = result.to_dict()
        summary = payload["loopSummary"]
        self.assertEqual(payload["executedActionCount"], 2)
        self.assertEqual(payload["reason"], "inventory_change_limit_reached")
        self.assertEqual(summary["inventoryChanges"], 2)
        self.assertEqual(summary["inventoryFreeSlotsStart"], 12)
        self.assertEqual(summary["inventoryFreeSlotsEnd"], 10)
        self.assertEqual(summary["resourceCountStart"], 0)
        self.assertEqual(summary["resourceCountEnd"], 2)

    def test_stop_when_inventory_full_stops_loop(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            cooldown_ms=0,
            action_timeout_ms=5000,
            result_timeout_ms=5000,
            max_actions=5,
            max_runtime_seconds=5.0,
            stop_on_warn=False,
            stop_on_fail=True,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=True,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
        )
        statuses = [
            resource_status(tick=1, free_slots=1, held_count=26, progress_count=26),
            resource_status(
                phase="wait_for_result",
                active_intent="wait_for_result",
                tick=2,
                free_slots=0,
                inventory_full=True,
                held_count=27,
                progress_count=27,
            ),
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=iter([0.0, 0.01]).__next__,
        )

        payload = result.to_dict()
        self.assertEqual(payload["executedActionCount"], 1)
        self.assertEqual(payload["reason"], "inventory_full")
        self.assertTrue(payload["loopSummary"]["inventoryFullEnd"])

    def test_max_successful_actions_stops_loop(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            cooldown_ms=0,
            action_timeout_ms=5000,
            result_timeout_ms=5000,
            max_actions=5,
            max_runtime_seconds=5.0,
            stop_on_warn=False,
            stop_on_fail=True,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=False,
            max_successful_actions=1,
            max_timeouts=None,
            seed=None,
        )
        statuses = [
            resource_status(tick=1, held_count=0, progress_count=0),
            resource_status(phase="wait_for_result", active_intent="wait_for_result", tick=2, held_count=1, progress_count=1),
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=iter([0.0, 0.01]).__next__,
        )

        payload = result.to_dict()
        self.assertEqual(payload["executedActionCount"], 1)
        self.assertEqual(payload["reason"], "successful_action_limit_reached")
        self.assertEqual(payload["loopSummary"]["successfulActions"], 1)

    def test_max_timeouts_stops_loop_without_immediate_repeat(self):
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
            result_timeout_ms=1,
            max_actions=5,
            max_runtime_seconds=5.0,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=1,
            seed=None,
        )
        statuses = [
            resource_status(tick=1),
            resource_status(phase="wait_for_result", active_intent="wait_for_result", tick=2),
            resource_status(phase="wait_for_result", active_intent="wait_for_result", tick=3),
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=iter([0.0, 0.01, 0.02, 0.03]).__next__,
        )

        payload = result.to_dict()
        self.assertEqual(payload["executedActionCount"], 1)
        self.assertEqual(payload["reason"], "max_timeouts_reached")
        self.assertEqual(payload["loopSummary"]["timeouts"], 1)
        self.assertEqual(len(backend.calls), 1)

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
        self.assertIn("observedSignals", payload)
        self.assertEqual(payload["resultOutcome"], "still_waiting")

    def test_lifecycle_diagnostic_prints_observed_signals(self):
        observed = verify_expected_result(
            "select_resource_target",
            resource_status(),
            resource_status(free_slots=11, held_count=1, progress_count=1),
        )
        payload = build_lifecycle_diagnostic(resource_status(phase="wait_for_result", active_intent="wait_for_result"))
        payload["observedResult"] = observed
        payload["observedSignals"] = observed["observedSignals"]
        payload["resultComplete"] = observed["resultComplete"]
        payload["resultOutcome"] = observed["resultOutcome"]
        payload["nextActionAllowed"] = observed["nextActionAllowed"]

        text = format_lifecycle_human(payload)

        self.assertIn("observed signals:", text)
        self.assertIn("held_resource_count_increased", text)
        self.assertIn("result outcome: success", text)

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

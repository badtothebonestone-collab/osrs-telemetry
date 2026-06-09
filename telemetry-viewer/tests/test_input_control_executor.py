import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from argparse import Namespace

import execute_next_action as execute_cli
from input_control.action_lifecycle import ActionLifecycleState
from input_control.action_proposal import ActionProposal, build_action_proposal
from input_control.backend_pyautogui import scale_canvas_point_to_screen
from input_control.camera_control import camera_input_spec, fitts_hold_duration_ms, hold_camera_input, smooth_drag_segments
from input_control.human_input_controller import HumanInputController
from input_control.mouse_movement import MouseMovementProfile
from input_control.executor import (
    ExecutionResult,
    HoverConfirmationOptions,
    _apply_lifecycle,
    _apply_reconciled_observation,
    _maybe_reset_reacquire_budget_on_scope_change,
    _confirm_hover_menu,
    _hover_failure_category,
    _loop_counts,
    _loop_stop_reason,
    _new_loop_summary,
    _no_click_safety_skip_observed,
    _proposal_has_actionable_safe_target,
    _proposal_reacquire_budget_type,
    _resource_progress_during_view_recovery_observation,
    _clear_suppression_on_progress_if_needed,
    _record_loop_status,
    _record_target_hover_failure,
    _record_target_no_progress_failure,
    _record_navigation_trace,
    _recovery_verified_loaded_scene,
    _route_transition_retry_required_observation,
    _goal_reached_with_only_recoverable_failures,
    _fetch_status_or_action_context,
    _service_object_timeout_pending_observation,
    _service_object_timeout_wait_extension_allowed,
    human_click_profile_handoff,
    build_click_plan_from_handoff,
    compare_center_click_vs_profile_click,
    _target_key_from_proposal,
    _verify_action_after_execution,
    _mark_navigation_no_progress,
    _navigation_motion_lock_observation,
    _navigation_not_executed_allows_retry,
    _navigation_decision_from_observed,
    _navigation_alternate_tile_requests,
    _navigation_hover_failure_reason,
    _navigation_walk_here_menu_entry,
    _try_navigation_alternate_hover,
    _route_stability_issue,
    _route_transition_reverse_issue,
    _route_transition_plane_mismatch_issue,
    _executed_navigation_waypoint_key,
    _menu_row_canvas_point,
    _maybe_context_action_proposal,
    _blocked_by_no_executable_result,
    _proposal_specific_blocker,
    camera_exposure_score,
    next_camera_direction_from_exposure,
    classify_last_menu_option_clicked,
    execute_action_loop,
    execute_action,
    execute_next_action,
    hover_menu_matches_target,
    route_projection_status,
)


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

    def move(self, plan):
        self.calls.append(("move", plan.click_point.x, plan.click_point.y))

    def click_at(self, x, y, *, button="left", hold_ms=0):
        self.calls.append(("click_at", x, y, button, hold_ms))

    def press(self, key):
        self.calls.append(("press", key))

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


class MovementSafetyBackend(FakeBackend):
    def __init__(self, allowed_region, *, canvas_offset=(0, 0)):
        super().__init__()
        self.allowed_region = dict(allowed_region)
        self.canvas_offset = tuple(canvas_offset)

    def movement_safety(self):
        return {
            "enabled": True,
            "allowedRegion": dict(self.allowed_region),
            "marginPx": 0,
        }

    def canvas_to_screen_point(self, point):
        self.converted_points.append(dict(point))
        return {"x": int(point["x"]) + int(self.canvas_offset[0]), "y": int(point["y"]) + int(self.canvas_offset[1])}


class FakeImage:
    def save(self, path):
        Path(path).write_bytes(b"fake image\n")


class FailingCanvasBackend(FakeBackend):
    def canvas_to_screen_point(self, point):
        raise AssertionError("dynamic geometry should avoid backend fallback conversion")


class RecoveryResultTest(unittest.TestCase):
    def test_daemon_rebind_failure_can_still_prove_loaded_scene(self):
        recovery = {
            "status": "daemon_rebind_failed",
            "finalState": {
                "state": "daemon_down",
                "loadedSceneVerified": True,
                "loadedSceneProof": {"loadedSceneVerified": True},
            },
        }

        self.assertTrue(_recovery_verified_loaded_scene(recovery))

    def test_retryable_navigation_safety_skip_does_not_end_loop(self):
        observed = {
            "observedResult": "no_click_safety_skip",
            "resultOutcome": "skipped",
            "nextActionAllowed": True,
            "skipReason": "volatile_hover_zone",
        }

        self.assertTrue(_navigation_not_executed_allows_retry(observed))
        self.assertTrue(
            _navigation_not_executed_allows_retry(
                {**observed, "observedResult": "hover_confirm_timeout"}
            )
        )
        self.assertFalse(
            _navigation_not_executed_allows_retry(
                {**observed, "nextActionAllowed": False, "observedResult": "no_click_safety_block"}
            )
        )


class CameraMotorMathTest(unittest.TestCase):
    def test_fitts_hold_duration_shrinks_as_exposure_error_decreases(self):
        far = fitts_hold_duration_ms(900, tolerance_px=72, min_ms=120, max_ms=900)
        near = fitts_hold_duration_ms(80, tolerance_px=72, min_ms=120, max_ms=900)

        self.assertGreater(far, near)
        self.assertGreaterEqual(near, 120)
        self.assertLessEqual(far, 900)

    def test_middle_mouse_drag_segments_use_smooth_envelope(self):
        segments = smooth_drag_segments(80, 0, steps=5)

        self.assertGreater(len(segments), 1)
        self.assertEqual(sum(dx for dx, _dy in segments), 80)
        self.assertEqual(sum(dy for _dx, dy in segments), 0)
        self.assertLess(abs(segments[0][0]), abs(segments[2][0]))
        self.assertLess(abs(segments[-1][0]), abs(segments[2][0]))

    def test_human_click_profile_handoff_exposes_click_and_camera_guidance(self):
        profile = {
            "schema": "human_click_profile.v1",
            "status": "PASS",
            "recordingCount": 3,
            "clicks": {"targetRelativeClicks": 12, "menuRowSelectionCount": 4, "rightClickMenuOpenCount": 2},
            "landing": {"medianAimDistancePx": 35, "p75AimDistancePx": 60, "p90AimDistancePx": 90, "aimDistanceBucketsPx": {"le80": 8}},
            "camera": {"cameraBeforeClickCount": 5, "middleMouseDragCount": 1, "medianCameraToClickMs": 900},
            "taskProfiles": {
                "woodcutting": {
                    "strongOrMediumTargetRate": 0.92,
                    "cameraBeforeClickFrequency": 0.5,
                    "imperfectSuccessfulClickCount": 7,
                }
            },
            "warnings": ["geometry is advisory"],
        }

        handoff = human_click_profile_handoff(profile, activity="woodcutting")

        self.assertEqual(handoff["schema"], "human_click_profile_executor_handoff.v1")
        self.assertEqual(handoff["status"], "PASS")
        self.assertEqual(handoff["clickLanding"]["medianAimDistancePx"], 35)
        self.assertEqual(handoff["clickLanding"]["strongOrMediumTargetRate"], 0.92)
        self.assertEqual(handoff["cameraBehavior"]["cameraBeforeClickFrequency"], 0.5)
        self.assertEqual(handoff["imperfectSuccessfulClickCount"], 7)
        self.assertIn("Advisory only", handoff["rule"])

    def test_click_plan_from_handoff_compares_center_and_profile_point(self):
        handoff = human_click_profile_handoff(
            {
                "schema": "human_click_profile.v1",
                "status": "PASS",
                "recordingCount": 3,
                "landing": {"medianAimDistancePx": 30, "p75AimDistancePx": 40},
                "taskProfiles": {"woodcutting": {"cameraBeforeClickFrequency": 0.25}},
            },
            activity="woodcutting",
        )

        plan = build_click_plan_from_handoff(
            handoff,
            target={
                "name": "Tree",
                "targetQuality": "strong",
                "onScreen": True,
                "geometryAvailable": True,
                "aimPoint": {"x": 100, "y": 120},
            },
            action="Chop down",
            activity="woodcutting",
        )
        comparison = compare_center_click_vs_profile_click(plan)

        self.assertEqual(plan["schema"], "human_click_plan.v1")
        self.assertEqual(comparison["schema"], "center_vs_profile_click.v1")
        self.assertTrue(comparison["differentFromCenter"])
        self.assertEqual(comparison["centerPoint"], {"x": 100, "y": 120})


def status_payload_for_loop(
    *,
    phase="target_selected",
    active_intent="select_target",
    free_slots=12,
    held_count=0,
    progress_count=0,
    tick=1,
):
    target = {
        "targetName": "Tree",
        "classId": "tree",
        "id": 1276,
        "aimPoint": {"canvasX": 200, "canvasY": 146},
        "onScreen": True,
        "geometryAvailable": True,
    }
    return {
        "latestTick": tick,
        "inputGeometry": {
            "inputGeometryAvailable": True,
            "canvasScreenOrigin": {"x": 1000, "y": 2000},
            "canvasSize": {"width": 765, "height": 503},
            "sourceCanvasSize": {"width": 765, "height": 503},
        },
        "cameraViewport": {"canvasWidth": 765, "canvasHeight": 503, "viewportXOffset": 0, "viewportYOffset": 0, "viewportWidth": 765, "viewportHeight": 503},
        "brain": {
            "latestTick": tick,
            "freshnessDomains": {"targetCandidateFreshness": "fresh"},
            "genericTaskState": {
                "phase": phase,
                "activeIntent": active_intent,
                "activeIntentTarget": target,
                "blockingConditions": [],
            },
            "inventoryContext": {
                "inventoryFull": False,
                "freeSlots": free_slots,
                "progress": {
                    "currentHeldCount": held_count,
                    "displayedGoalProgress": progress_count,
                    "currentInventorySignature": f"sig-{held_count}-{free_slots}",
                },
            },
            "intentOverlayContext": {"selectedMarker": target},
            "bankUiContext": {"bankOpen": False, "bankReadable": False, "bankPinOpen": False},
        },
    }


def lifecycle_status_for_summary(
    *,
    stage: str,
    phase: str | None = None,
    active_intent: str | None = None,
    free_slots: int = 12,
    held_count: int = 0,
    progress_count: int = 0,
    tick: int = 1,
    inventory_full: bool = False,
    bank_open: bool = False,
    banking_complete: bool = False,
    resource_items_held: int | None = None,
    resource_target_available: bool = False,
):
    status = status_payload_for_loop(
        phase=phase or stage,
        active_intent=active_intent or phase or stage,
        free_slots=free_slots,
        held_count=held_count,
        progress_count=progress_count,
        tick=tick,
    )
    status["currentCycleStage"] = stage
    status["inventoryFull"] = inventory_full
    status["inventoryFreeSlots"] = free_slots
    status["brain"]["inventoryContext"]["inventoryFull"] = inventory_full
    status["brain"]["bankUiContext"]["bankOpen"] = bank_open
    status["brain"]["bankOperationContext"] = {
        "bankingComplete": banking_complete,
        "resourceItemsHeld": resource_items_held if resource_items_held is not None else held_count,
    }
    status["brain"]["resourceReturnContext"] = {
        "resourceTargetAvailable": resource_target_available,
        "returnDestinationAvailable": stage == "return_to_resource",
    }
    status["returnResourceTargetAvailable"] = resource_target_available
    return status


def status_payload_with_candidates_for_loop(*, active_target, candidates, free_slots=12, held_count=0, progress_count=0, tick=1):
    status = status_payload_for_loop(free_slots=free_slots, held_count=held_count, progress_count=progress_count, tick=tick)
    status["brain"]["genericTaskState"]["activeIntentTarget"] = active_target
    status["brain"]["intentOverlayContext"] = {
        "selectedMarker": active_target,
        "markers": [
            dict(candidate, markerType="selected_target" if index == 0 else "backup_candidate")
            for index, candidate in enumerate(candidates)
        ],
    }
    status["brain"]["profileCandidates"] = list(candidates)
    status["profileCandidates"] = list(candidates)
    return status


def navigation_status_payload(
    *,
    tick=10,
    x=3200,
    y=3248,
    service_distance=12,
    path_distance=6,
    movement_state="stationary",
    route_action_ready=False,
):
    waypoint = {"worldX": 3206, "worldY": 3242, "plane": 0}
    status = status_payload_for_loop(
        phase="inventory_full",
        active_intent="needs_service",
        free_slots=0,
        held_count=28,
        tick=tick,
    )
    status["playerContext"] = {"worldX": x, "worldY": y, "plane": 0, "worldTile": {"worldX": x, "worldY": y, "plane": 0}}
    status["brain"]["playerContext"] = dict(status["playerContext"])
    status["brain"]["genericTaskState"]["phase"] = "inventory_full"
    status["brain"]["genericTaskState"]["activeIntent"] = "needs_service"
    status["brain"]["serviceContext"] = {
        "serviceNeeded": True,
        "serviceReady": False,
        "distanceToServiceTarget": service_distance,
    }
    status["brain"]["serviceRouteContext"] = {
        "routeAvailable": True,
        "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
        "routeStepStatus": "static_route_prior",
        "actionReady": route_action_ready,
        "currentNodeId": "lumbridge_castle_entrance_or_courtyard",
    }
    status["brain"]["pathingContext"] = {
        "pathingNeeded": True,
        "movementState": movement_state,
        "nextWaypointTile": waypoint,
        "nextWaypointAimPoint": {"canvasX": 260, "canvasY": 280},
        "distanceToServiceTarget": service_distance,
        "distanceToPathTarget": path_distance,
        "distanceToDestination": service_distance,
        "destinationTile": {"worldX": 3208, "worldY": 3228, "plane": 0},
        "pathTargetTile": waypoint,
        "predictedPathTiles": [waypoint],
    }
    return status


def route_transition_status_payload(*, tick=10, x=3200, y=3232, plane=0, route_step_index=3, current_node="lumbridge_ground_floor_stairs"):
    status = status_payload_for_loop(
        phase="inventory_full",
        active_intent="needs_service",
        free_slots=0,
        held_count=28,
        tick=tick,
    )
    status["playerContext"] = {"worldX": x, "worldY": y, "plane": plane, "worldTile": {"worldX": x, "worldY": y, "plane": plane}}
    status["brain"]["playerContext"] = dict(status["playerContext"])
    status["serviceRouteCurrentStepIndex"] = route_step_index
    status["serviceRouteCurrentNodeId"] = current_node
    status["brain"]["serviceRouteContext"] = {
        "routeAvailable": True,
        "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
        "currentStepIndex": route_step_index,
        "currentNodeId": current_node,
        "routeStepStatus": "route_interaction_visible",
        "actionReady": True,
    }
    return status


class IncrementingClock:
    def __init__(self, start=0.0, step=0.01):
        self.value = float(start)
        self.step = float(step)

    def __call__(self):
        current = self.value
        self.value += self.step
        return current


class InputControlExecutorTest(unittest.TestCase):
    def test_context_fallback_original_reason_becomes_blocked_result_reason(self):
        proposal = ActionProposal(
            proposed_action="wait_for_context",
            target_kind="route_reentry",
            reason="no_executable_action",
            target_explanation={
                "contextActionFallback": {
                    "schema": "context_action_fallback.v1",
                    "status": "WARN",
                    "originalAction": "wait_for_context",
                    "originalReason": "route_guide_no_same_plane_reentry",
                    "reason": "context_action_proposal_not_executable",
                }
            },
        )
        blocker = _proposal_specific_blocker(proposal)

        result = _blocked_by_no_executable_result(
            proposal,
            status={"latestTick": 9561},
            options=Namespace(execute=True, backend="arduino", movement_profile="instant_test", max_actions=1),
            reason=blocker,
        )

        self.assertEqual(blocker, "route_guide_no_same_plane_reentry")
        self.assertEqual(result.observed_result["observedResult"], "route_guide_no_same_plane_reentry")
        self.assertEqual(result.lifecycle_state["reason"], "route_guide_no_same_plane_reentry")

    def test_status_fetch_falls_back_to_compact_action_context(self):
        context_response = {
            "schema": "context_response.v1",
            "status": "WARN",
            "latestTick": 91,
            "knowledgeCurrentDebugContext": {
                "data": {
                    "actionProposal": {
                        "schema": "action_proposal.v1",
                        "status": "PASS",
                        "proposedAction": "select_resource_target",
                        "targetKind": "resource",
                        "targetName": "Tree",
                        "targetTile": {"worldX": 3192, "worldY": 3238, "plane": 0},
                        "suggestedClickPoint": {"x": 222, "y": 178},
                        "reason": "resource_target_selected",
                        "confidence": 0.95,
                        "sourceTick": 91,
                    }
                }
            },
            "warnings": ["compact context warning"],
        }

        with (
            patch("input_control.executor.fetch_action_context", return_value=context_response),
            patch("input_control.executor.fetch_plugin_snapshot", return_value={}),
        ):
            status = _fetch_status_or_action_context(
                "http://daemon",
                Namespace(task="woodcutting_loop"),
                fetch_json_func=lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("status too large")),
                timeout=0.01,
                purpose="test",
            )

        self.assertEqual(status["schema"], "context_status_fallback.v1")
        self.assertEqual(status["latestTick"], 91)
        self.assertEqual(status["brain"]["contextActionProposal"]["targetName"], "Tree")
        self.assertEqual(status["daemonStatusFallback"]["status"], "PASS")
        self.assertIn("compact context warning", status["warnings"])

    def test_status_fetch_falls_back_to_plugin_snapshot_when_daemon_and_context_timeout(self):
        snapshot = {
            "schema": "plugin_snapshot_response.v1",
            "latestTick": 122,
            "freshness": {"maxCacheAgeMillis": 20},
            "payloads": {
                "interaction_hot": {
                    "schema": "client_tick_hot.v1",
                    "sessionPath": "C:/sessions/fresh",
                    "gameState": "LOGGED_IN",
                },
                "baseline": {
                    "player": {
                        "worldX": 3196,
                        "worldY": 3247,
                        "plane": 0,
                    }
                },
                "inventory": {
                    "inventory": {
                        "known": True,
                        "freeSlots": 0,
                        "filledSlots": 28,
                        "totalQuantityByItemId": {"1511": 28},
                        "signature": "full",
                    }
                },
                "activity": {"animation": -1},
            },
        }

        with (
            patch("input_control.executor.fetch_action_context", side_effect=TimeoutError("context too slow")),
            patch("input_control.executor.fetch_plugin_snapshot", return_value=snapshot),
        ):
            status = _fetch_status_or_action_context(
                "http://daemon",
                Namespace(task="woodcutting_loop", snapshot_url="http://snapshot"),
                fetch_json_func=lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("status too slow")),
                timeout=0.01,
                purpose="action_selection",
            )

        self.assertEqual(status["schema"], "plugin_snapshot_status_fallback.v1")
        self.assertEqual(status["latestTick"], 122)
        self.assertEqual(status["sessionPath"], "C:/sessions/fresh")
        self.assertEqual(status["playerLocation"], {"worldX": 3196, "worldY": 3247, "plane": 0})
        self.assertEqual(status["brain"]["inventoryContext"]["freeSlots"], 0)
        self.assertEqual(status["daemonStatusFallback"]["source"], "plugin_snapshot")
        self.assertIn("using plugin snapshot", " ".join(status["warnings"]))

    def test_plugin_snapshot_full_inventory_blocks_unproven_ladder_transition(self):
        snapshot = {
            "schema": "plugin_snapshot_response.v1",
            "latestTick": 124,
            "freshness": {"maxCacheAgeMillis": 20},
            "payloads": {
                "interaction_hot": {
                    "schema": "client_tick_hot.v1",
                    "sessionPath": "C:/sessions/fresh",
                    "gameState": "LOGGED_IN",
                },
                "baseline": {
                    "player": {
                        "worldX": 3203,
                        "worldY": 3238,
                        "plane": 0,
                    }
                },
                "inventory": {
                    "inventory": {
                        "known": True,
                        "freeSlots": 0,
                        "filledSlots": 28,
                        "totalQuantityByItemId": {"1511": 22, "1521": 6},
                        "signature": "full-at-route-anchor",
                    }
                },
                "projection": {
                    "visibleObjectRefs": [
                        {
                            "id": 16683,
                            "name": "Ladder",
                            "targetType": "sceneObject",
                            "worldX": 3204,
                            "worldY": 3238,
                            "plane": 0,
                            "onScreen": True,
                            "geometryAvailable": True,
                            "aimPoint": {"canvasX": 498, "canvasY": 122, "source": "clickboxBoundsCenter"},
                            "actions": ["Climb-up"],
                        },
                        {
                            "id": 1541,
                            "name": "Door",
                            "targetType": "sceneObject",
                            "worldX": 3205,
                            "worldY": 3238,
                            "plane": 0,
                            "onScreen": True,
                            "geometryAvailable": True,
                            "aimPoint": {"canvasX": 518, "canvasY": 97, "source": "clickboxBoundsCenter"},
                            "actions": ["Close"],
                        },
                    ]
                },
            },
        }

        with (
            patch("input_control.executor.fetch_action_context", side_effect=TimeoutError("context too slow")),
            patch("input_control.executor.fetch_plugin_snapshot", return_value=snapshot),
        ):
            status = _fetch_status_or_action_context(
                "http://daemon",
                Namespace(task="woodcutting_loop", snapshot_url="http://snapshot"),
                fetch_json_func=lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("status too slow")),
                timeout=0.01,
                purpose="action_selection",
            )

        route_context = status["brain"]["serviceRouteContext"]
        self.assertEqual(route_context["routeStepStatus"], "plugin_snapshot_route_transition_visible")
        self.assertEqual(route_context["visibleInteractionTarget"]["targetName"], "Ladder")
        proposal = build_action_proposal(status)
        self.assertEqual(proposal.proposed_action, "wait_for_context")
        self.assertEqual(proposal.target_kind, "service_route_object")
        self.assertEqual(proposal.target_name, "Ladder")
        self.assertEqual(proposal.reason, "route_object_not_on_expected_segment")
        self.assertFalse(proposal.executable)
        self.assertIsNone(proposal.suggested_click_point)
        self.assertFalse(proposal.target_explanation["routeCorridorMatch"])
        self.assertIn("unrelated_route_object", proposal.target_explanation["rejectedReasons"])

    def test_status_fetch_enriches_sparse_status_from_compact_action_context(self):
        context_response = {
            "schema": "context_response.v1",
            "status": "PASS",
            "latestTick": 91,
            "knowledgeCurrentDebugContext": {
                "data": {
                    "liveStatus": {
                        "sessionPath": "C:/sessions/current",
                        "latestTick": 91,
                        "inputSourceActive": "plugin-snapshot",
                    },
                    "actionProposal": {
                        "schema": "action_proposal.v1",
                        "status": "PASS",
                        "proposedAction": "select_resource_target",
                        "targetKind": "resource",
                        "targetName": "Tree",
                        "suggestedClickPoint": {"x": 222, "y": 178},
                        "inputGeometry": {"schema": "input_geometry.v1", "inputGeometryAvailable": True},
                        "targetExplanation": {
                            "schema": "candidate_explanation.v1",
                            "name": "Tree",
                            "classId": "tree",
                            "safeAimPoint": {"status": "PASS", "canvasX": 222, "canvasY": 178},
                        },
                        "actionTargetSource": "live_resource_candidate",
                        "actionability": "needs_hover_confirmation",
                        "reason": "resource_target_visible",
                        "confidence": 0.95,
                        "sourceTick": 91,
                    },
                }
            },
        }
        sparse_status = {"schema": "live_status.v1", "latestTick": 91}

        with (
            patch("input_control.executor.fetch_action_context", return_value=context_response),
            patch("input_control.executor.fetch_plugin_snapshot", return_value={}),
        ):
            status = _fetch_status_or_action_context(
                "http://daemon",
                Namespace(task="woodcutting_loop"),
                fetch_json_func=lambda *_args, **_kwargs: dict(sparse_status),
                timeout=0.01,
                purpose="test",
            )

        self.assertEqual(status["sessionPath"], "C:/sessions/current")
        self.assertTrue(status["inputGeometry"]["inputGeometryAvailable"])
        self.assertEqual(status["brain"]["genericTaskState"]["activeIntentTarget"]["targetName"], "Tree")
        self.assertEqual(status["brain"]["genericTaskState"]["activeIntentTarget"]["safeAimPoint"]["status"], "PASS")
        self.assertEqual(status["daemonStatusFallback"]["statusEndpointReason"], "status_payload_missing_action_context")

    def test_status_fetch_merges_plugin_inventory_and_activity_snapshot(self):
        context_response = {
            "schema": "context_response.v1",
            "status": "PASS",
            "latestTick": 91,
            "knowledgeCurrentDebugContext": {
                "data": {
                    "actionProposal": {
                        "schema": "action_proposal.v1",
                        "status": "PASS",
                        "proposedAction": "select_resource_target",
                        "targetKind": "resource",
                        "targetName": "Tree",
                        "suggestedClickPoint": {"x": 222, "y": 178},
                        "reason": "resource_target_visible",
                        "confidence": 0.95,
                        "sourceTick": 91,
                    },
                }
            },
        }
        blocked_status = {
            "schema": "live_status.v1",
            "latestTick": 90,
            "brain": {
                "genericTaskState": {
                    "phase": "blocked",
                    "activeIntent": "observe",
                    "blockingConditions": ["stale_context"],
                }
            },
        }
        snapshot = {
            "schema": "plugin_snapshot_response.v1",
            "latestTick": 120,
            "freshness": {"maxCacheAgeMillis": 20},
            "payloads": {
                "interaction_hot": {"sessionPath": "C:/sessions/fresh", "gameState": "LOGGED_IN"},
                "baseline": {
                    "player": {
                        "worldX": 3189,
                        "worldY": 3254,
                        "plane": 0,
                        "animation": 879,
                        "poseAnimation": 808,
                    }
                },
                "inventory": {
                    "inventory": {
                        "known": True,
                        "freeSlots": 10,
                        "filledSlots": 18,
                        "totalQuantityByItemId": {"1511": 12, "1521": 6},
                        "signature": "fresh-sig",
                    }
                },
                "activity": {"animation": 879, "poseAnimation": 808},
                "projection": {
                    "visibleObjectRefs": [
                        {
                            "id": 1278,
                            "name": "Tree",
                            "targetType": "sceneObject",
                            "worldX": 3190,
                            "worldY": 3255,
                            "plane": 0,
                            "onScreen": True,
                            "geometryAvailable": True,
                            "aimPoint": {"canvasX": 318, "canvasY": 139, "source": "clickboxBoundsCenter"},
                            "bounds": {"x": 276, "y": 81, "w": 84, "h": 116},
                            "actions": ["Chop down"],
                        }
                    ]
                },
            },
        }

        with (
            patch("input_control.executor.fetch_action_context", return_value=context_response),
            patch("input_control.executor.fetch_plugin_snapshot", return_value=snapshot),
        ):
            status = _fetch_status_or_action_context(
                "http://daemon",
                Namespace(task="woodcutting_loop"),
                fetch_json_func=lambda *_args, **_kwargs: dict(blocked_status),
                timeout=0.01,
                purpose="post_action_verification",
            )

        self.assertEqual(status["latestTick"], 120)
        self.assertEqual(status["sessionPath"], "C:/sessions/fresh")
        self.assertEqual(status["brain"]["playerContext"]["animation"], 879)
        self.assertEqual(status["brain"]["inventoryContext"]["freeSlots"], 10)
        self.assertEqual(status["brain"]["inventoryContext"]["progress"]["currentHeldCount"], 18)
        self.assertEqual(status["brain"]["inventoryContext"]["progress"]["currentInventorySignature"], "fresh-sig")
        self.assertEqual(status["brain"]["genericTaskState"]["phase"], "target_selected")
        self.assertEqual(status["brain"]["genericTaskState"]["activeIntentTarget"]["targetName"], "Tree")
        self.assertEqual(status["brain"]["candidateTargets"][0]["targetName"], "Tree")
        self.assertEqual(status["brain"]["candidateTargets"][0]["sourceTick"], 120)
        self.assertEqual(status["latestEventSummary"], "Player animation changed: 879")
        proposal = build_action_proposal(status)
        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertNotEqual(proposal.reason, "candidate_data_stale")

    def test_context_action_fallback_rehydrates_executable_tree_proposal(self):
        current = ActionProposal(
            status="WARN",
            proposed_action="wait_for_context",
            target_kind="none",
            reason="no_executable_action",
            missing_capabilities=["live.action_context"],
        )
        context_response = {
            "schema": "context_response.v1",
            "status": "WARN",
            "latestTick": 77,
            "inventory": {
                "known": True,
                "freeSlots": 18,
                "filledSlots": 10,
                "itemCounts": {"1511": 10},
            },
            "knowledgeCurrentDebugContext": {
                "data": {
                    "actionProposal": {
                        "schema": "action_proposal.v1",
                        "status": "PASS",
                        "proposedAction": "select_resource_target",
                        "targetKind": "resource",
                        "targetName": "Tree",
                        "targetTile": {"worldX": 3192, "worldY": 3238, "plane": 0},
                        "suggestedClickPoint": {"x": 222, "y": 178},
                        "clickPointSpace": "canvas",
                        "resolvedScreenClickPoint": {"x": 505, "y": 364},
                        "clickPointResolution": {"status": "PASS", "method": "safe_aimpoint"},
                        "targetExplanation": {"classId": "tree"},
                        "reason": "resource_target_selected",
                        "confidence": 0.95,
                        "sourceTick": 77,
                    }
                }
            },
        }

        with patch("input_control.executor.fetch_action_context", return_value=context_response):
            enriched, proposal, fallback = _maybe_context_action_proposal(
                "http://daemon",
                Namespace(task="woodcutting_loop"),
                {"schema": "context_status.v1", "latestTick": 77},
                current,
                timeout=0.01,
            )

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertTrue(proposal.executable)
        self.assertEqual(proposal.target_name, "Tree")
        self.assertEqual(proposal.suggested_click_point, {"x": 222, "y": 178})
        self.assertEqual(proposal.resolved_screen_click_point, {"x": 505, "y": 364})
        self.assertEqual(fallback["status"], "PASS")
        self.assertEqual(enriched["brain"]["inventoryContext"]["freeSlots"], 18)
        self.assertEqual(enriched["brain"]["inventoryContext"]["progress"]["currentHeldCount"], 10)

    def test_context_action_fallback_rejects_tree_collection_when_inventory_full_route_missing(self):
        current = ActionProposal(
            status="FAIL",
            proposed_action="wait_for_context",
            target_kind="none",
            reason="inventory_full_route_context_missing",
            missing_capabilities=["service_route.route_to_bank", "pathing.route_to_bank"],
        )
        context_response = {
            "schema": "context_response.v1",
            "status": "WARN",
            "latestTick": 77,
            "inventory": {
                "known": True,
                "freeSlots": 0,
                "filledSlots": 28,
                "itemCounts": {"1511": 28},
            },
            "knowledgeCurrentDebugContext": {
                "data": {
                    "actionProposal": {
                        "schema": "action_proposal.v1",
                        "status": "PASS",
                        "proposedAction": "select_resource_target",
                        "targetKind": "resource",
                        "targetName": "Tree",
                        "targetTile": {"worldX": 3192, "worldY": 3238, "plane": 0},
                        "suggestedClickPoint": {"x": 222, "y": 178},
                        "reason": "resource_target_selected",
                        "confidence": 0.95,
                        "sourceTick": 77,
                    }
                }
            },
        }

        with patch("input_control.executor.fetch_action_context", return_value=context_response):
            enriched, proposal, fallback = _maybe_context_action_proposal(
                "http://daemon",
                Namespace(task="woodcutting_loop"),
                {"schema": "context_status.v1", "latestTick": 77},
                current,
                timeout=0.01,
            )

        self.assertEqual(proposal.proposed_action, "wait_for_context")
        self.assertFalse(proposal.executable)
        self.assertEqual(proposal.reason, "inventory_full_route_context_missing")
        self.assertEqual(fallback["status"], "WARN")
        self.assertEqual(fallback["reason"], "context_action_proposal_rejected_inventory_full")
        self.assertEqual(enriched["brain"]["inventoryContext"]["freeSlots"], 0)
        self.assertTrue(any("inventory is full" in warning for warning in proposal.warnings))

    def test_context_action_fallback_rejects_stale_navigation_when_fresh_route_guide_available(self):
        current = ActionProposal(
            status="FAIL",
            proposed_action="wait_for_context",
            target_kind="none",
            reason="route_object_not_on_expected_segment",
            missing_capabilities=["service_route.route_to_bank"],
        )
        context_response = {
            "schema": "context_response.v1",
            "status": "WARN",
            "latestTick": 3406,
            "inventory": {
                "known": True,
                "freeSlots": 0,
                "filledSlots": 28,
                "itemCounts": {"1511": 28},
            },
            "knowledgeCurrentDebugContext": {
                "data": {
                    "actionProposal": {
                        "schema": "action_proposal.v1",
                        "status": "PASS",
                        "proposedAction": "navigate_to_service",
                        "targetKind": "path_tile",
                        "targetName": "Lumbridge Castle entrance or ground-floor courtyard",
                        "targetTile": {"worldX": 3200, "worldY": 3233, "plane": 0},
                        "suggestedClickPoint": {"x": 456, "y": 616},
                        "reason": "pathing_to_service",
                        "confidence": 0.74,
                        "sourceTick": 3406,
                        "targetExplanation": {
                            "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                            "freshness": {
                                "status": "stale",
                                "targetCandidateFreshness": "stale",
                                "daemonStatusAgeMillis": 210277,
                            },
                        },
                    }
                }
            },
        }
        live_status = {
            "schema": "context_status.v1",
            "latestTick": 3796,
            "playerWorldPosition": {"worldX": 3201, "worldY": 3236, "plane": 0},
            "brain": {
                "inventoryContext": {
                    "freeSlots": 0,
                    "inventoryFull": True,
                    "progress": {"currentHeldCount": 28},
                },
                "genericTaskState": {"phase": "needs_service", "activeIntent": "needs_service"},
            },
        }

        with patch("input_control.executor.fetch_action_context", return_value=context_response):
            enriched, proposal, fallback = _maybe_context_action_proposal(
                "http://daemon",
                Namespace(task="woodcutting_loop"),
                live_status,
                current,
                timeout=0.01,
            )

        self.assertEqual(fallback["status"], "WARN")
        self.assertEqual(fallback["reason"], "context_action_proposal_rejected_stale_navigation")
        self.assertEqual(fallback["freshProposalAction"], "navigate_to_service")
        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertTrue(proposal.executable)
        self.assertEqual(proposal.reason, "route_guide_progress_without_live_route_context")
        self.assertEqual(proposal.target_tile, {"worldX": 3208, "worldY": 3212, "plane": 0})
        self.assertEqual(proposal.target_explanation["routeGuideName"], "woodcutting_area_to_bank")
        self.assertEqual(enriched["brain"]["inventoryContext"]["freeSlots"], 0)

    def test_context_action_fallback_rejects_non_executable_when_fresh_route_guide_available(self):
        current = ActionProposal(
            status="FAIL",
            proposed_action="wait_for_context",
            target_kind="none",
            reason="route_object_not_on_expected_segment",
            missing_capabilities=["service_route.route_to_bank"],
        )
        context_response = {
            "schema": "context_response.v1",
            "status": "WARN",
            "latestTick": 3406,
            "inventory": {
                "known": True,
                "freeSlots": 0,
                "filledSlots": 28,
                "itemCounts": {"1511": 28},
            },
            "knowledgeCurrentDebugContext": {
                "data": {
                    "actionProposal": {
                        "schema": "action_proposal.v1",
                        "status": "FAIL",
                        "proposedAction": "wait_for_context",
                        "targetKind": "none",
                        "reason": "inventory_full_route_context_missing",
                        "confidence": 0.45,
                        "missingCapabilities": ["service_route.route_to_bank", "pathing.route_to_bank"],
                    }
                }
            },
        }
        live_status = {
            "schema": "context_status.v1",
            "latestTick": 3796,
            "playerWorldPosition": {"worldX": 3201, "worldY": 3219, "plane": 0},
            "brain": {
                "inventoryContext": {
                    "freeSlots": 0,
                    "inventoryFull": True,
                    "progress": {"currentHeldCount": 28},
                },
                "genericTaskState": {"phase": "needs_service", "activeIntent": "needs_service"},
            },
        }

        with patch("input_control.executor.fetch_action_context", return_value=context_response):
            enriched, proposal, fallback = _maybe_context_action_proposal(
                "http://daemon",
                Namespace(task="woodcutting_loop"),
                live_status,
                current,
                timeout=0.01,
            )

        self.assertEqual(fallback["status"], "WARN")
        self.assertEqual(fallback["reason"], "context_action_proposal_rejected_non_executable")
        self.assertEqual(fallback["freshProposalAction"], "navigate_to_service")
        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertTrue(proposal.executable)
        self.assertEqual(proposal.reason, "route_guide_progress_without_live_route_context")
        self.assertEqual(proposal.target_tile, {"worldX": 3209, "worldY": 3216, "plane": 0})
        self.assertEqual(proposal.target_explanation["routeGuideName"], "woodcutting_area_to_bank")
        self.assertEqual(enriched["brain"]["inventoryContext"]["freeSlots"], 0)

    def test_context_action_fallback_rejects_fresh_tree_proposal_when_inventory_full(self):
        current = ActionProposal(
            status="FAIL",
            proposed_action="wait_for_context",
            target_kind="service_route_object",
            target_name="Staircase",
            reason="route_object_not_on_expected_segment",
            missing_capabilities=["service_route.route_to_bank"],
        )
        context_response = {
            "schema": "context_response.v1",
            "status": "WARN",
            "latestTick": 5628,
            "inventory": {
                "known": True,
                "freeSlots": 0,
                "filledSlots": 28,
                "itemCounts": {"1511": 28},
            },
            "knowledgeCurrentDebugContext": {
                "data": {
                    "actionProposal": {
                        "schema": "action_proposal.v1",
                        "status": "FAIL",
                        "proposedAction": "navigate_to_service",
                        "targetKind": "path_tile",
                        "targetName": "Demonstrated woodcutting-to-bank route waypoint",
                        "reason": "route_guide_next_step_missing_after_3203_3238",
                        "confidence": 0.45,
                    }
                }
            },
        }
        fresh_tree = ActionProposal(
            status="PASS",
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Tree",
            target_tile={"worldX": 3195, "worldY": 3220, "plane": 0},
            suggested_click_point={"x": 65, "y": 118},
            reason="resource_target_visible",
            confidence=0.82,
        )

        with (
            patch("input_control.executor.fetch_action_context", return_value=context_response),
            patch("input_control.executor.build_action_proposal", return_value=fresh_tree),
        ):
            enriched, proposal, fallback = _maybe_context_action_proposal(
                "http://daemon",
                Namespace(task="woodcutting_loop"),
                {"schema": "context_status.v1", "latestTick": 5628},
                current,
                timeout=0.01,
            )

        self.assertEqual(fallback["status"], "WARN")
        self.assertEqual(fallback["reason"], "context_action_proposal_rejected_inventory_full")
        self.assertEqual(fallback["freshProposalAction"], "select_resource_target")
        self.assertEqual(proposal.proposed_action, "wait_for_context")
        self.assertEqual(proposal.reason, "route_object_not_on_expected_segment")
        self.assertFalse(proposal.executable)
        self.assertEqual(enriched["brain"]["inventoryContext"]["freeSlots"], 0)

    def test_unsafe_geometry_skip_counts_as_suppressible_failure(self):
        result = ExecutionResult(
            status="WARN",
            dry_run=False,
            proposed_action="interact_service_route_object",
            executed=False,
            missing_capabilities=["screen_click_point"],
            lifecycle_state={"reason": "screen_click_point_unavailable"},
        )

        self.assertEqual(_hover_failure_category(result), "unsafe_geometry")

    def test_movement_safety_block_is_fatal_not_suppressed_hover_failure(self):
        result = ExecutionResult(
            status="FAIL",
            dry_run=False,
            proposed_action="navigate_to_service",
            executed=False,
            missing_capabilities=["screen_click_point"],
            lifecycle_state={"reason": "screen_click_point_outside_movement_safety_region"},
        )

        self.assertIsNone(_hover_failure_category(result))
        observed = _no_click_safety_skip_observed(result)
        self.assertEqual(observed["observedResult"], "no_click_safety_block")
        self.assertEqual(observed["resultOutcome"], "blocked")
        self.assertFalse(observed["nextActionAllowed"])
        self.assertEqual(observed["verificationStatus"], "FAIL")

    def test_resource_movement_safety_edge_is_suppressible_unsafe_geometry(self):
        result = ExecutionResult(
            status="FAIL",
            dry_run=False,
            proposed_action="select_resource_target",
            executed=False,
            missing_capabilities=["screen_click_point"],
            proposal={
                "proposedAction": "select_resource_target",
                "targetKind": "resource",
                "targetName": "Tree",
                "targetExplanation": {
                    "name": "Tree",
                    "targetKey": "tree-edge",
                    "worldLocation": {"worldX": 3225, "worldY": 3245, "plane": 0},
                },
            },
            lifecycle_state={"reason": "screen_click_point_outside_movement_safety_region"},
        )

        self.assertEqual(_hover_failure_category(result), "unsafe_geometry")
        observed = _no_click_safety_skip_observed(result)
        self.assertEqual(observed["observedResult"], "no_click_safety_skip")
        self.assertEqual(observed["resultOutcome"], "skipped")
        self.assertTrue(observed["nextActionAllowed"])
        self.assertEqual(observed["skipReason"], "unsafe_geometry")

        summary = {}
        event = _record_target_hover_failure(
            options=Namespace(target_hover_failure_limit=1, target_suppression_ms=2500),
            cache={},
            summary=summary,
            result=result,
            now_ms=1000,
        )

        self.assertIsNotNone(event)
        self.assertTrue(event["suppressed"])
        self.assertEqual(event["reason"], "unsafe_geometry")
        self.assertEqual(summary["targetsSuppressed"], 1)

    def test_live_movement_safety_blocks_off_region_screen_point_before_move(self):
        backend = MovementSafetyBackend({"x": 100, "y": 100, "width": 500, "height": 500})
        proposal = ActionProposal(
            status="PASS",
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3223, "worldY": 3219, "plane": 0},
            suggested_click_point={"x": 50, "y": 700},
            click_point_space="screen",
            resolved_screen_click_point={"x": 50, "y": 700},
            click_point_resolution={"status": "PASS", "screenClickPoint": {"x": 50, "y": 700}},
            confidence=0.7,
        )

        result = execute_action(proposal, backend=backend, movement_profile="instant_test", dry_run=False)

        self.assertEqual(result.status, "FAIL")
        self.assertFalse(result.executed)
        self.assertEqual(backend.calls, [])
        self.assertIn("screen_click_point", result.missing_capabilities)
        self.assertEqual(result.lifecycle_state["reason"], "screen_click_point_outside_movement_safety_region")
        self.assertFalse(result.observed_result["nextActionAllowed"])

    def test_failed_screen_resolution_is_not_actionable_for_suppression_override(self):
        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            suggested_click_point={"x": 354, "y": 61},
            missing_capabilities=["screen_click_point"],
            target_explanation={
                "name": "Staircase",
                "safeAimPoint": {"status": "PASS", "actionable": True},
            },
        )

        self.assertFalse(_proposal_has_actionable_safe_target(proposal))

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

    def test_dynamic_geometry_is_used_before_backend_fallback(self):
        backend = FailingCanvasBackend()
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Oak tree",
            suggested_click_point={"x": 200, "y": 150},
            click_point_space="canvas",
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 1000, "y": 2000},
                "canvasSize": {"width": 800, "height": 600},
                "displayScale": {"x": 2.0, "y": 2.0},
            },
            click_point_resolution={
                "status": "PASS",
                "method": "dynamic_input_geometry",
                "screenClickPoint": {"x": 1400, "y": 2300},
            },
            resolved_screen_click_point={"x": 1400, "y": 2300},
            confidence=0.9,
        )

        result = execute_action(proposal, backend=backend, movement_profile="instant_test", dry_run=True)

        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.commands[0]["clickPoint"]["x"], 1400)
        self.assertEqual(result.commands[0]["clickPoint"]["y"], 2300)
        self.assertEqual(result.click_point_resolution["method"], "dynamic_input_geometry")

    def test_offscreen_dynamic_geometry_blocks_execution(self):
        backend = FailingCanvasBackend()
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Oak tree",
            suggested_click_point={"x": 900, "y": 150},
            click_point_space="canvas",
            click_point_resolution={
                "status": "FAIL",
                "method": "dynamic_input_geometry",
                "screenClickPoint": {"x": 1900, "y": 2150},
                "warnings": ["resolved screen click point outside canvas bounds"],
                "missingCapabilities": ["screen_click_point"],
            },
            confidence=0.9,
        )

        result = execute_action(proposal, backend=backend, movement_profile="instant_test", dry_run=False)

        self.assertEqual(result.status, "FAIL")
        self.assertFalse(result.executed)
        self.assertIn("screen_click_point", result.missing_capabilities)

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

    def test_navigation_path_tile_uses_plugin_tile_projection(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            status="WARN",
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3205, "worldY": 3229, "plane": 0},
            confidence=0.72,
            missing_capabilities=["click_point"],
            warnings=["missing click point or key action"],
        )
        seen_requests = []

        def snapshot_fetch(_url, **kwargs):
            seen_requests.extend(kwargs.get("tile_projection_requests") or [])
            return {
                "tileProjections": {
                    "schema": "tile_projection_response.v1",
                    "status": "PASS",
                    "tiles": [
                        {
                            "status": "PASS",
                            "worldX": 3205,
                            "worldY": 3229,
                            "plane": 0,
                            "geometryAvailable": True,
                            "onScreen": True,
                            "aimPoint": {"canvasX": 300, "canvasY": 240, "source": "tileProjectionCenter"},
                            "canvasTileBounds": {"x": 280, "y": 220, "w": 40, "h": 40},
                        }
                    ],
                }
            }

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            snapshot_fetch_func=snapshot_fetch,
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(seen_requests[0]["worldX"], 3205)
        self.assertEqual(result.proposal["suggestedClickPoint"], {"x": 300, "y": 240})
        self.assertEqual(result.proposal["clickPointSpace"], "canvas")
        self.assertEqual(result.click_point_resolution["method"], "plugin_tile_projection")
        self.assertEqual(result.commands[0]["clickPoint"]["x"], 1300)
        self.assertEqual(result.commands[0]["clickPoint"]["y"], 2240)
        self.assertEqual(result.proposal["targetExplanation"]["routeProjectionStatus"]["classification"], "visible")
        self.assertEqual(backend.calls, [])

    def test_route_projection_status_classifies_offscreen_and_degenerate_tiles(self):
        offscreen = route_projection_status(
            {
                "worldX": 3206,
                "worldY": 3242,
                "plane": 0,
                "onScreen": False,
                "aimPoint": {"x": 900, "y": 260},
                "cameraViewport": {"canvasWidth": 765, "canvasHeight": 503, "viewportXOffset": 0, "viewportYOffset": 0, "viewportWidth": 765, "viewportHeight": 503},
            }
        )
        degenerate = route_projection_status(
            {
                "worldX": 3206,
                "worldY": 3242,
                "plane": 0,
                "aimPoint": {"x": 0, "y": 0},
                "canvasTilePolygon": [{"x": 0, "y": 0}, {"x": 0, "y": 0}, {"x": 0, "y": 0}],
            }
        )

        self.assertEqual(offscreen["classification"], "offscreen")
        self.assertFalse(offscreen["actionableByCanvas"])
        self.assertEqual(degenerate["classification"], "degenerate")
        self.assertTrue(degenerate["degenerateProjection"])

    def test_route_projection_status_rejects_edge_clipped_tiles_when_policy_enabled(self):
        status = route_projection_status(
            {
                "worldX": 3206,
                "worldY": 3242,
                "plane": 0,
                "onScreen": True,
                "geometryAvailable": True,
                "aimPoint": {"canvasX": 760, "canvasY": 250},
                "canvasTileBounds": {"x": 758, "y": 236, "w": 26, "h": 24},
                "canvasTilePolygon": [
                    {"x": 758, "y": 236},
                    {"x": 784, "y": 236},
                    {"x": 784, "y": 260},
                    {"x": 758, "y": 260},
                ],
                "cameraViewport": {
                    "canvasWidth": 765,
                    "canvasHeight": 503,
                    "viewportXOffset": 0,
                    "viewportYOffset": 0,
                    "viewportWidth": 765,
                    "viewportHeight": 503,
                },
            },
            reject_edge_route_clicks=True,
            edge_margin_px=12,
            min_visible_area_ratio=0.7,
        )

        self.assertEqual(status["classification"], "edge_clipped")
        self.assertFalse(status["actionableByCanvas"])
        self.assertEqual(status["distanceToViewportEdgePx"], 5)
        self.assertLess(status["clippedVisibleAreaRatio"], 0.7)
        self.assertIn("viewport edge", status["rejectionReason"])

    def test_route_projection_status_rejects_tile_exactly_on_edge_margin(self):
        status = route_projection_status(
            {
                "worldX": 3246,
                "worldY": 3242,
                "plane": 0,
                "onScreen": True,
                "geometryAvailable": True,
                "aimPoint": {"canvasX": 12, "canvasY": 208},
                "canvasTileBounds": {"x": -15, "y": 203, "w": 54, "h": 10},
                "canvasTilePolygon": [
                    {"x": -15, "y": 213},
                    {"x": 17, "y": 212},
                    {"x": 39, "y": 203},
                    {"x": 10, "y": 203},
                ],
                "cameraViewport": {
                    "canvasWidth": 765,
                    "canvasHeight": 503,
                    "viewportXOffset": 0,
                    "viewportYOffset": 0,
                    "viewportWidth": 765,
                    "viewportHeight": 503,
                },
            },
            reject_edge_route_clicks=True,
            edge_margin_px=12,
            min_visible_area_ratio=0.45,
        )

        self.assertEqual(status["classification"], "edge_clipped")
        self.assertFalse(status["actionableByCanvas"])
        self.assertEqual(status["distanceToViewportEdgePx"], 12)
        self.assertTrue(status["partiallyOffscreen"])
        self.assertIn("<= 12px", status["rejectionReason"])

    def test_resource_view_recovery_uses_held_camera_input_without_click(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="resource_view_recovery",
            target_kind="resource_recovery",
            key_action={"type": "camera_reacquire", "command": "yaw_right_pitch_up", "method": "keyboard_arrows", "durationMs": 160},
            target_explanation={
                "targetName": "Tree",
                "resourceProjectionStatus": {"classification": "projection_sentinel"},
            },
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=False,
            navigation_options=Namespace(input_profile="steady", camera_method="keyboard_arrows"),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.executed)
        self.assertIn(("key_down", "right"), backend.calls)
        self.assertIn(("key_down", "up"), backend.calls)
        self.assertIn(("key_up", "up"), backend.calls)
        self.assertIn(("key_up", "right"), backend.calls)
        self.assertFalse(any(call[0] == "click_at" for call in backend.calls))
        self.assertEqual(result.action_trace["reacquisition"]["cameraTriggeredBy"], "resource_projection_sentinel")
        self.assertEqual(result.action_trace["finalClassification"], "resource_projection_recovery_started")

    def test_service_view_recovery_uses_held_camera_input_without_click(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="service_view_recovery",
            target_kind="service_recovery",
            key_action={"type": "camera_reacquire", "command": "yaw_right_pitch_up", "method": "keyboard_arrows", "durationMs": 180},
            target_explanation={
                "targetName": "Bank Deposit Box",
                "serviceTargetExposure": {
                    "schema": "service_target_exposure.v1",
                    "serviceTargetKind": "deposit_box",
                    "cameraExposureReason": "service_object_loaded_offscreen",
                    "currentProjectionStatus": "offscreen",
                    "cameraMotorPlan": {
                        "cameraInputMethod": "keyboard_arrows",
                        "cameraDirectionChosen": "yaw_right_pitch_up",
                        "cameraDirectionReason": "canvas_point_offscreen_diagonal",
                        "cameraHoldMs": 180,
                    },
                },
            },
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=False,
            navigation_options=Namespace(input_profile="steady", camera_method="keyboard_arrows"),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.executed)
        self.assertIn(("key_down", "right"), backend.calls)
        self.assertIn(("key_down", "up"), backend.calls)
        self.assertIn(("key_up", "up"), backend.calls)
        self.assertIn(("key_up", "right"), backend.calls)
        self.assertFalse(any(call[0] == "click_at" for call in backend.calls))
        self.assertEqual(result.commands[0]["type"], "service_camera_reacquire")
        self.assertTrue(result.commands[0]["nonClick"])
        self.assertEqual(result.action_trace["reacquisition"]["cameraTriggeredBy"], "service_object_loaded_offscreen")
        self.assertEqual(result.action_trace["finalClassification"], "service_view_recovery_started")

    def test_resource_view_recovery_loop_stops_when_projection_does_not_improve(self):
        backend = FakeBackend()
        target = {
            "targetName": "Tree",
            "name": "Tree",
            "classId": "tree",
            "targetType": "sceneObject",
            "id": 1278,
            "objectId": 1278,
            "worldX": 3212,
            "worldY": 3232,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "bounds": {"x": 2147483647, "y": 2147483647, "w": 1, "h": 1},
            "safeAimPoint": {
                "status": "FAIL",
                "actionable": False,
                "rawAimPoint": {"x": 2147483648, "y": 2147483648},
                "rejectionReason": "projection_sentinel",
            },
        }
        status = status_payload_with_candidates_for_loop(active_target=target, candidates=[target], tick=10)
        status["selectedHighlighterTarget"] = target
        status["selectedTarget"] = target
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            input_profile="steady",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=False,
            hover_confirm_timeout_ms=0,
            hover_poll_ms=10,
            hover_position_tolerance=3,
            click_hold_ms=0,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=20,
            action_timeout_ms=20,
            poll_interval_ms=10,
            max_actions=10,
            max_total_actions=10,
            max_runtime_seconds=2,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            max_consecutive_timeouts=None,
            seed=None,
            client_tick_debug=False,
            client_tick_tail=0,
            menu_entry_limit=5,
            require_clicked_menu_match=False,
            require_live_readiness=False,
            camera_method="keyboard_arrows",
        )

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: status,
            backend=backend,
            snapshot_fetch_func=lambda *_args, **_kwargs: {},
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.05),
        )

        payload = result.to_dict()
        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(payload["reason"], "resource_projection_recovery_failed")
        self.assertEqual(payload["actionResultCount"], 1)
        summary = payload["loopSummary"]
        self.assertEqual(summary["actualClicks"], 0)
        self.assertEqual(summary["actionsExecuted"], 1)
        self.assertEqual(summary["resourceProjectionRecoveryAttempts"], 1)
        self.assertEqual(summary["resourceProjectionRecoveryFailures"], 1)
        self.assertEqual(summary["cameraAdjustments"], 1)

    def test_resource_view_recovery_wait_is_recoverable_when_resource_progress_lands(self):
        backend = FakeBackend()
        target = {
            "targetName": "Tree",
            "name": "Tree",
            "classId": "tree",
            "targetType": "sceneObject",
            "id": 1278,
            "objectId": 1278,
            "worldX": 3212,
            "worldY": 3232,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "bounds": {"x": 2147483647, "y": 2147483647, "w": 1, "h": 1},
            "safeAimPoint": {
                "status": "FAIL",
                "actionable": False,
                "rawAimPoint": {"x": 2147483648, "y": 2147483648},
                "rejectionReason": "projection_sentinel",
            },
        }
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            input_profile="steady",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=False,
            hover_confirm_timeout_ms=0,
            hover_poll_ms=10,
            hover_position_tolerance=3,
            click_hold_ms=0,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=20,
            action_timeout_ms=20,
            poll_interval_ms=10,
            max_actions=10,
            max_total_actions=10,
            max_runtime_seconds=2,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=1,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            max_consecutive_timeouts=None,
            seed=None,
            client_tick_debug=False,
            client_tick_tail=0,
            menu_entry_limit=5,
            require_clicked_menu_match=False,
            require_live_readiness=False,
            camera_method="keyboard_arrows",
        )
        statuses = [
            status_payload_with_candidates_for_loop(active_target=target, candidates=[target], free_slots=4, held_count=12, progress_count=5, tick=10),
            status_payload_with_candidates_for_loop(active_target=target, candidates=[target], free_slots=3, held_count=13, progress_count=6, tick=11),
        ]
        for status in statuses:
            status["selectedHighlighterTarget"] = target
            status["selectedTarget"] = target

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            snapshot_fetch_func=lambda *_args, **_kwargs: {},
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.05),
        )

        payload = result.to_dict()
        observed = payload["actionResults"][0]["observedResult"]
        summary = payload["loopSummary"]
        self.assertEqual(payload["status"], "WARN")
        self.assertEqual(payload["reason"], "inventory_change_limit_reached")
        self.assertEqual(observed["observedResult"], "resource_progress_during_view_recovery")
        self.assertEqual(observed["previousObservedResult"], "resource_projection_recovery_waiting")
        self.assertTrue(observed["resourceProgressDuringViewRecovery"])
        self.assertEqual(observed["resourceProgressDelta"]["resourceCountDelta"], 1)
        self.assertEqual(summary["inventoryChanges"], 1)
        self.assertEqual(summary["resourceProgressSuccesses"], 1)
        self.assertEqual(summary["resourceProjectionRecoveryFailures"], 0)
        self.assertEqual(summary["cameraAdjustments"], 1)

    def test_resource_view_recovery_progress_clears_suppressed_targets(self):
        summary = _new_loop_summary()
        summary.update(
            {
                "inventoryFreeSlotsStart": 3,
                "inventoryFreeSlotsEnd": 2,
                "resourceCountStart": 13,
                "resourceCountEnd": 14,
                "progressStart": 6,
                "progressEnd": 7,
            }
        )
        cache = {"1278:3225:3232:0:tree": {"suppressionUntil": 999999}}
        observed = _resource_progress_during_view_recovery_observation(
            {
                "observedResult": "resource_projection_recovery_waiting",
                "resultOutcome": "still_waiting",
            },
            summary,
        )

        _clear_suppression_on_progress_if_needed(
            Namespace(clear_suppression_on_progress=True),
            cache,
            summary,
            observed,
        )

        self.assertEqual(cache, {})
        self.assertEqual(summary["suppressionClearsOnProgress"], 1)
        self.assertIn("resource_progress_increased", observed["observedSignals"])

    def test_resource_recovery_loop_captures_bounded_debug_bundles(self):
        backend = FakeBackend()
        target = {
            "targetName": "Tree",
            "name": "Tree",
            "classId": "tree",
            "targetType": "sceneObject",
            "id": 1278,
            "objectId": 1278,
            "worldX": 3212,
            "worldY": 3232,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "bounds": {"x": 2147483647, "y": 2147483647, "w": 1, "h": 1},
            "safeAimPoint": {
                "status": "FAIL",
                "actionable": False,
                "rawAimPoint": {"x": 2147483648, "y": 2147483648},
                "rejectionReason": "projection_sentinel",
            },
        }
        status = status_payload_with_candidates_for_loop(active_target=target, candidates=[target], tick=10)
        status["selectedHighlighterTarget"] = target
        status["selectedTarget"] = target
        with tempfile.TemporaryDirectory() as tmp:
            options = Namespace(
                timeout=0.01,
                backend="pyautogui",
                movement_profile="instant_test",
                input_profile="steady",
                execute=True,
                verify_after_action=True,
                after_action_wait_ms=0,
                hover_confirm_target=False,
                hover_confirm_timeout_ms=0,
                hover_poll_ms=10,
                hover_position_tolerance=3,
                click_hold_ms=0,
                wait_for_ready=0,
                cooldown_ms=0,
                result_timeout_ms=20,
                action_timeout_ms=20,
                poll_interval_ms=10,
                max_actions=10,
                max_total_actions=10,
                max_runtime_seconds=2,
                stop_on_warn=False,
                stop_on_fail=False,
                stop_after_inventory_changes=None,
                stop_when_inventory_full=False,
                max_successful_actions=None,
                max_timeouts=None,
                max_consecutive_timeouts=None,
                seed=None,
                client_tick_debug=False,
                client_tick_tail=0,
                menu_entry_limit=5,
                require_clicked_menu_match=False,
                require_live_readiness=False,
                focus_runelite=False,
                camera_method="keyboard_arrows",
                capture_debug_screenshots=True,
                screenshot_on_failure=True,
                screenshot_on_camera_recovery=True,
                screenshot_on_timeout=True,
                screenshot_on_edge_reject=False,
                screenshot_on_lifecycle_transition=False,
                max_debug_screenshots=3,
                debug_screenshot_dir=tmp,
                visual_debug_screenshot_func=lambda _region=None: FakeImage(),
            )

            result = execute_action_loop(
                "http://daemon",
                options,
                fetch_json_func=lambda *_args, **_kwargs: status,
                backend=backend,
                snapshot_fetch_func=lambda *_args, **_kwargs: {},
                sleep_func=lambda _seconds: None,
                monotonic_func=IncrementingClock(start=0.0, step=0.05),
            )

            payload = result.to_dict()
            summary = payload["loopSummary"]
            self.assertEqual(payload["reason"], "resource_projection_recovery_failed")
            self.assertEqual(summary["debugScreenshotBundlesCaptured"], 3)
            self.assertEqual(summary["debugScreenshotCaptureFailures"], 0)
            self.assertLessEqual(len(summary["debugScreenshotBundlePaths"]), 3)
            reasons = [
                json.loads((Path(path) / "bundle.json").read_text(encoding="utf-8"))["reason"]
                for path in summary["debugScreenshotBundlePaths"]
            ]
            self.assertIn("resource_projection_recovery_start", reasons)
            self.assertIn("resource_projection_recovery_end", reasons)
            self.assertIn("failure", reasons)

    def test_debug_screenshot_failure_does_not_crash_loop(self):
        backend = FakeBackend()
        target = {
            "targetName": "Tree",
            "name": "Tree",
            "classId": "tree",
            "targetType": "sceneObject",
            "id": 1278,
            "objectId": 1278,
            "worldX": 3212,
            "worldY": 3232,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "bounds": {"x": 2147483647, "y": 2147483647, "w": 1, "h": 1},
            "safeAimPoint": {"status": "FAIL", "actionable": False, "rejectionReason": "projection_sentinel"},
        }
        status = status_payload_with_candidates_for_loop(active_target=target, candidates=[target], tick=10)

        def fail_screenshot(_region=None):
            raise RuntimeError("screen unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            options = Namespace(
                timeout=0.01,
                backend="pyautogui",
                movement_profile="instant_test",
                input_profile="steady",
                execute=True,
                verify_after_action=True,
                after_action_wait_ms=0,
                hover_confirm_target=False,
                hover_confirm_timeout_ms=0,
                hover_poll_ms=10,
                hover_position_tolerance=3,
                click_hold_ms=0,
                wait_for_ready=0,
                cooldown_ms=0,
                result_timeout_ms=20,
                action_timeout_ms=20,
                poll_interval_ms=10,
                max_actions=10,
                max_total_actions=10,
                max_runtime_seconds=2,
                stop_on_warn=False,
                stop_on_fail=False,
                stop_after_inventory_changes=None,
                stop_when_inventory_full=False,
                max_successful_actions=None,
                max_timeouts=None,
                max_consecutive_timeouts=None,
                seed=None,
                client_tick_debug=False,
                client_tick_tail=0,
                menu_entry_limit=5,
                require_clicked_menu_match=False,
                require_live_readiness=False,
                focus_runelite=False,
                camera_method="keyboard_arrows",
                capture_debug_screenshots=True,
                screenshot_on_failure=True,
                screenshot_on_camera_recovery=True,
                screenshot_on_timeout=True,
                screenshot_on_edge_reject=False,
                screenshot_on_lifecycle_transition=False,
                max_debug_screenshots=2,
                debug_screenshot_dir=tmp,
                visual_debug_screenshot_func=fail_screenshot,
            )

            result = execute_action_loop(
                "http://daemon",
                options,
                fetch_json_func=lambda *_args, **_kwargs: status,
                backend=backend,
                snapshot_fetch_func=lambda *_args, **_kwargs: {},
                sleep_func=lambda _seconds: None,
                monotonic_func=IncrementingClock(start=0.0, step=0.05),
            )

            payload = result.to_dict()
            self.assertEqual(payload["reason"], "resource_projection_recovery_failed")
            self.assertEqual(payload["loopSummary"]["debugScreenshotCaptureFailures"], 2)

    def test_navigation_motion_lock_suppresses_replan_while_player_moves(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_tile={"worldX": 3206, "worldY": 3242, "plane": 0},
        )
        observed = {
            "verificationStatus": "PASS",
            "observedResult": "service_navigation_progress",
            "resultOutcome": "progress",
            "resultComplete": True,
            "nextActionAllowed": True,
            "elapsedTicks": 2,
            "observedSignals": ["player_tile_changed", "service_distance_decreased"],
        }

        locked = _navigation_motion_lock_observation(
            action="navigate_to_service",
            proposal=proposal,
            status=navigation_status_payload(tick=12, x=3201, y=3247, service_distance=10, path_distance=5, movement_state="moving"),
            observed=observed,
            options=Namespace(nav_replan_while_moving=False, nav_min_game_ticks_between_clicks=3, nav_destination_arrival_distance=1),
        )

        self.assertIsNotNone(locked)
        self.assertFalse(locked["resultComplete"])
        self.assertFalse(locked["nextActionAllowed"])
        self.assertEqual(locked["observedResult"], "service_navigation_clicked_waiting")
        self.assertEqual(locked["navigationInProgress"]["replanSuppressedReason"], "player_still_moving_to_clicked_waypoint")

    def test_navigation_motion_lock_releases_after_arrival_or_route_object(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_tile={"worldX": 3206, "worldY": 3242, "plane": 0},
        )
        observed = {
            "verificationStatus": "PASS",
            "observedResult": "service_navigation_progress",
            "resultOutcome": "progress",
            "resultComplete": True,
            "nextActionAllowed": True,
            "elapsedTicks": 4,
            "observedSignals": ["player_tile_changed", "service_distance_decreased"],
        }

        released = _navigation_motion_lock_observation(
            action="navigate_to_service",
            proposal=proposal,
            status=navigation_status_payload(tick=14, x=3206, y=3242, service_distance=8, path_distance=0, movement_state="stationary"),
            observed=observed,
            options=Namespace(nav_replan_while_moving=False, nav_min_game_ticks_between_clicks=3, nav_destination_arrival_distance=1),
        )
        route_object = _navigation_motion_lock_observation(
            action="navigate_to_service",
            proposal=proposal,
            status=navigation_status_payload(tick=14, x=3202, y=3246, service_distance=8, path_distance=4, movement_state="moving", route_action_ready=True),
            observed=observed,
            options=Namespace(nav_replan_while_moving=False, nav_min_game_ticks_between_clicks=3, nav_destination_arrival_distance=1),
        )

        self.assertIsNone(released)
        self.assertIsNone(route_object)

    def test_route_stability_detects_immediate_waypoint_cycle(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_tile={"worldX": 3201, "worldY": 3247, "plane": 0},
        )

        issue = _route_stability_issue(
            proposal,
            [
                (3201, 3247, 0),
                (3202, 3246, 0),
            ],
        )

        self.assertIsNotNone(issue)
        self.assertEqual(issue["classification"], "route_oscillation_detected")
        self.assertTrue(issue["backtrackingDetected"])

    def test_route_stability_history_uses_resolved_clicked_alternate_waypoint(self):
        proposal = ActionProposal(
            proposed_action="return_to_resource_area",
            target_kind="path_tile",
            target_tile={"worldX": 3216, "worldY": 3219, "plane": 0},
        )
        result = ExecutionResult(
            status="PASS",
            proposed_action="return_to_resource_area",
            dry_run=False,
            executed=True,
            proposal={"targetTile": {"worldX": 3211, "worldY": 3228, "plane": 0}},
        )

        self.assertEqual(_executed_navigation_waypoint_key(proposal, result), (3211, 3228, 0))

    def test_navigation_walk_here_menu_entry_detects_lower_walk_here_under_npc(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_tile={"worldX": 3205, "worldY": 3214, "plane": 0},
        )
        confirmation = {
            "confirmed": False,
            "sample": {
                "menuOpen": False,
                "topOption": "Attack",
                "topTarget": "<col=ffff00>Rat<col=80ff00>  (level-1)",
                "topType": "NPC_SECOND_OPTION",
                "entries": [
                    {"option": "Attack", "target": "<col=ffff00>Rat<col=80ff00>  (level-1)", "type": "NPC_SECOND_OPTION"},
                    {"option": "Walk here", "target": "", "type": "WALK", "param0": 447, "param1": 172},
                    {"option": "Examine", "target": "<col=ffff>Crate", "type": "EXAMINE_OBJECT"},
                    {"option": "Cancel", "target": "", "type": "CANCEL"},
                ],
            },
        }

        entry = _navigation_walk_here_menu_entry(proposal, confirmation)

        self.assertIsNotNone(entry)
        self.assertEqual(entry["option"], "Walk here")

    def test_route_stability_allows_repeat_after_navigation_progress(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_tile={"worldX": 3231, "worldY": 3218, "plane": 0},
        )
        last_result = ExecutionResult(
            status="PASS",
            proposed_action="navigate_to_service",
            dry_run=False,
            executed=True,
            observed_result={
                "observedResult": "service_navigation_progress",
                "resultOutcome": "progress",
                "observedSignals": ["player_tile_changed", "destination_distance_decreased"],
            },
        )

        issue = _route_stability_issue(
            proposal,
            [(3231, 3218, 0)],
            last_result=last_result,
        )

        self.assertIsNone(issue)

    def test_route_stability_repeat_without_block_evidence_does_not_enter_wall_hug_recovery(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_tile={"worldX": 3231, "worldY": 3218, "plane": 0},
        )
        last_result = ExecutionResult(
            status="FAIL",
            proposed_action="navigate_to_service",
            dry_run=False,
            executed=True,
            observed_result={
                "observedResult": "service_navigation_no_progress",
                "resultOutcome": "no_change_timeout",
                "observedSignals": ["route_no_progress"],
            },
        )
        status = navigation_status_payload(tick=5, x=3231, y=3220, service_distance=10, path_distance=2)
        status["brain"]["pathingContext"]["localReachability"] = "reachable"

        issue = _route_stability_issue(
            proposal,
            [(3231, 3218, 0)],
            last_result=last_result,
            current_status=status,
        )

        self.assertIsNotNone(issue)
        self.assertEqual(issue["classification"], "route_repeat_suppressed_no_block_evidence")
        self.assertFalse(issue["barrierDetected"])
        self.assertTrue(issue["recoverySuppressed"])

    def test_route_stability_reached_waypoint_advances_instead_of_clicking_again(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_tile={"worldX": 3231, "worldY": 3218, "plane": 0},
        )
        status = navigation_status_payload(tick=5, x=3231, y=3218, service_distance=8, path_distance=0)

        issue = _route_stability_issue(
            proposal,
            [(3231, 3218, 0)],
            last_result=ExecutionResult(
                status="WARN",
                proposed_action="navigate_to_service",
                dry_run=False,
                executed=True,
                observed_result={
                    "observedResult": "service_route_object_reacquired",
                    "resultOutcome": "progress",
                    "observedSignals": ["route_object_reacquired"],
                },
            ),
            current_status=status,
        )

        self.assertIsNotNone(issue)
        self.assertEqual(issue["classification"], "route_waypoint_arrived_advance_required")
        self.assertTrue(issue["advanceRecommended"])
        self.assertFalse(issue["barrierDetected"])

    def test_route_transition_reverse_oscillation_is_blocked_before_click(self):
        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Ladder",
            target_tile={"worldX": 3211, "worldY": 3242, "plane": 1},
            target_explanation={
                "name": "Ladder",
                "expectedOptions": ["Climb-down"],
                "worldLocation": {"worldX": 3211, "worldY": 3242, "plane": 1},
                "routeId": "plugin_snapshot_route_to_service",
            },
        )
        previous = {
            "expectedAction": "Climb-up",
            "objectName": "Ladder",
            "worldLocation": {"worldX": 3211, "worldY": 3242, "plane": 0},
            "planeBefore": 0,
            "planeAfter": 1,
            "routeId": "plugin_snapshot_route_to_service",
        }
        issue = _route_transition_reverse_issue(
            proposal,
            previous,
            current_status=route_transition_status_payload(tick=8, x=3211, y=3243, plane=1),
        )

        self.assertIsNotNone(issue)
        self.assertEqual(issue["classification"], "route_transition_reverse_oscillation_prevented")
        self.assertTrue(issue["backtrackingDetected"])
        self.assertEqual(issue["proposedTransition"]["expectedAction"], "Climb-down")

    def test_route_stability_repeat_with_block_evidence_can_enter_wall_hug_recovery(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_tile={"worldX": 3231, "worldY": 3218, "plane": 0},
        )
        status = navigation_status_payload(tick=5, x=3231, y=3220, service_distance=10, path_distance=2)
        status["brain"]["pathingContext"]["localReachability"] = "blocked"

        issue = _route_stability_issue(
            proposal,
            [(3231, 3218, 0)],
            last_result=ExecutionResult(
                status="FAIL",
                proposed_action="navigate_to_service",
                dry_run=False,
                executed=True,
                observed_result={"observedResult": "service_navigation_no_progress", "resultOutcome": "no_change_timeout"},
            ),
            current_status=status,
        )

        self.assertIsNotNone(issue)
        self.assertEqual(issue["classification"], "route_wall_hugging_detected")
        self.assertTrue(issue["barrierDetected"])
        self.assertIn("pathing.localReachability=blocked", issue["blockEvidence"])

    def test_navigation_trace_writes_compact_jsonl_with_reason_and_distance_delta(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_tile={"worldX": 3206, "worldY": 3242, "plane": 0},
            reason="service_route_waypoint",
            source_tick=12,
            action_target_source="live_projected_waypoint",
        )
        previous = navigation_status_payload(tick=12, x=3200, y=3248, service_distance=12, path_distance=6)
        current = navigation_status_payload(tick=13, x=3201, y=3247, service_distance=10, path_distance=5, movement_state="moving")

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "nav.jsonl"
            result = ExecutionResult(status="PASS", proposed_action="navigate_to_service", dry_run=False, action_trace={})
            entry = _record_navigation_trace(
                options=Namespace(nav_trace=True, nav_trace_console=False, nav_trace_output=str(output)),
                loop_summary={},
                decision="wait",
                reason="player_still_moving_to_clicked_waypoint",
                status=current,
                previous_status=previous,
                proposal=proposal,
                observed={
                    "observedResult": "service_navigation_clicked_waiting",
                    "resultOutcome": "still_waiting",
                    "resultComplete": False,
                    "nextActionAllowed": False,
                },
                result=result,
            )
            lines = output.read_text(encoding="utf-8").strip().splitlines()

        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(entry["decision"], "wait")
        self.assertEqual(payload["schema"], "navigation_decision_trace.v1")
        self.assertEqual(payload["decision"], "wait")
        self.assertEqual(payload["reason"], "player_still_moving_to_clicked_waypoint")
        self.assertEqual(payload["playerWorldPosition"], {"worldX": 3201, "worldY": 3247, "plane": 0})
        self.assertEqual(payload["chosenSubgoal"]["targetTile"], {"worldX": 3206, "worldY": 3242, "plane": 0})
        self.assertLess(payload["distances"]["distanceDelta"], 0)
        self.assertTrue(payload["distances"]["distanceImproving"])
        self.assertEqual(result.action_trace["navigationDecisionTrace"][0]["decision"], "wait")

    def test_navigation_decision_trace_classifies_waypoint_reached_as_advance(self):
        decision, reason, recovery = _navigation_decision_from_observed(
            {
                "observedResult": "service_navigation_reached_node",
                "resultOutcome": "progress",
                "observedSignals": ["route_step_index_changed"],
            }
        )

        self.assertEqual(decision, "advance")
        self.assertEqual(reason, "service_navigation_reached_node")
        self.assertIsNone(recovery)

    def test_navigation_decision_trace_classifies_stale_state_as_wait_no_click(self):
        proposal = ActionProposal(
            proposed_action="wait_for_context",
            target_kind="none",
            reason="daemon_latest_tick_missing",
        )
        with tempfile.TemporaryDirectory() as tmp:
            entry = _record_navigation_trace(
                options=Namespace(nav_trace=True, nav_trace_console=False, nav_trace_output=str(Path(tmp) / "nav.jsonl")),
                loop_summary={},
                decision="wait",
                reason=proposal.reason,
                status=navigation_status_payload(tick=0, x=3200, y=3248, service_distance=12, path_distance=6),
                proposal=proposal,
                observed={
                    "observedResult": proposal.reason,
                    "resultOutcome": "still_waiting",
                    "resultComplete": False,
                    "nextActionAllowed": False,
                },
            )

        self.assertEqual(entry["decision"], "wait")
        self.assertEqual(entry["reason"], "daemon_latest_tick_missing")
        self.assertFalse(entry["chosenSubgoal"]["executable"])

    def test_navigation_no_progress_without_block_evidence_does_not_mark_route_barrier(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_tile={"worldX": 3203, "worldY": 3238, "plane": 0},
        )
        result = ExecutionResult(
            status="FAIL",
            proposed_action="navigate_to_service",
            dry_run=False,
            observed_result={
                "observedResult": "service_navigation_no_progress",
                "resultOutcome": "no_change_timeout",
                "observedSignals": ["destination_distance_changed"],
            },
            action_trace={},
        )

        self.assertTrue(_mark_navigation_no_progress(result, proposal))
        observed = result.observed_result
        route_stability = result.action_trace["routeStability"]
        self.assertNotIn("route_wrong_node_or_barrier", observed["observedSignals"])
        self.assertEqual(observed["routeNoProgress"]["classification"], "navigation_no_progress_no_block_evidence")
        self.assertFalse(observed["routeNoProgress"]["barrierEvidence"])
        self.assertFalse(route_stability["barrierDetected"])
        counts = _loop_counts([result])
        self.assertEqual(counts["routeBarrierDetections"], 0)
        self.assertEqual(counts["navigationNoProgressWithoutBlockEvidence"], 1)

    def test_navigation_no_progress_with_block_evidence_marks_route_barrier(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_tile={"worldX": 3203, "worldY": 3238, "plane": 0},
        )
        result = ExecutionResult(
            status="FAIL",
            proposed_action="navigate_to_service",
            dry_run=False,
            observed_result={
                "observedResult": "service_navigation_no_progress",
                "resultOutcome": "no_change_timeout",
                "observedSignals": [],
            },
            action_trace={},
        )
        status = navigation_status_payload(tick=5, x=3203, y=3238, service_distance=12, path_distance=6)
        status["brain"]["pathingContext"]["localReachability"] = "blocked"

        self.assertTrue(_mark_navigation_no_progress(result, proposal, status=status))
        observed = result.observed_result
        route_stability = result.action_trace["routeStability"]
        self.assertIn("route_wrong_node_or_barrier", observed["observedSignals"])
        self.assertEqual(observed["routeNoProgress"]["classification"], "route_wrong_node_or_barrier")
        self.assertTrue(observed["routeNoProgress"]["barrierEvidence"])
        self.assertTrue(route_stability["barrierDetected"])
        self.assertEqual(_loop_counts([result])["routeBarrierDetections"], 1)

    def test_navigation_no_progress_continues_until_bounded_limit(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            input_profile="steady",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=False,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=20,
            action_timeout_ms=20,
            poll_interval_ms=10,
            max_actions=2,
            max_total_actions=2,
            max_runtime_seconds=2,
            final_reconcile_ms=0,
            final_reconcile_game_ticks=0,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            max_consecutive_no_progress=3,
            max_consecutive_timeouts=3,
            seed=None,
            require_live_readiness=False,
        )
        def nav_status(*, tick, x, y, service_distance, path_distance, waypoint_y):
            status = navigation_status_payload(
                tick=tick,
                x=x,
                y=y,
                service_distance=service_distance,
                path_distance=path_distance,
            )
            waypoint = {"worldX": 3233, "worldY": waypoint_y, "plane": 0}
            pathing = status["brain"]["pathingContext"]
            pathing["nextWaypointTile"] = waypoint
            pathing["pathTargetTile"] = waypoint
            pathing["predictedPathTiles"] = [waypoint]
            return status

        statuses = [
            nav_status(tick=1, x=3233, y=3227, service_distance=19, path_distance=19, waypoint_y=3226),
            nav_status(tick=2, x=3233, y=3227, service_distance=19, path_distance=19, waypoint_y=3226),
            nav_status(tick=3, x=3233, y=3227, service_distance=19, path_distance=19, waypoint_y=3225),
            nav_status(tick=4, x=3233, y=3226, service_distance=18, path_distance=18, waypoint_y=3225),
        ]

        def fetch_status(*_args, **_kwargs):
            if statuses:
                return statuses.pop(0)
            return navigation_status_payload(tick=5, x=3233, y=3226, service_distance=18, path_distance=18)

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=fetch_status,
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.1),
        )

        payload = result.to_dict()
        self.assertNotEqual(payload["reason"], "route_wrong_node_or_barrier")
        self.assertEqual(payload["reason"], "max_actions_reached")
        self.assertEqual(payload["actionResultCount"], 2)
        self.assertEqual(payload["loopSummary"]["timeouts"], 1)
        self.assertEqual(payload["loopSummary"]["navigationInProgressWaits"], 1)
        self.assertEqual(payload["actionResults"][0]["observedResult"]["observedResult"], "service_navigation_stuck")
        self.assertEqual(payload["actionResults"][1]["observedResult"]["observedResult"], "service_navigation_clicked_waiting")

    def test_loop_counts_classifies_unresolved_and_reconciled_timeouts(self):
        unresolved = ExecutionResult(
            status="FAIL",
            proposed_action="select_resource_target",
            dry_run=False,
            executed=True,
            observed_result={
                "observedResult": "no_change_timeout",
                "resultOutcome": "no_change_timeout",
                "resourceProgressClassification": "resource_timeout_no_progress",
            },
        )
        reconciled = ExecutionResult(
            status="PASS",
            proposed_action="select_resource_target",
            dry_run=False,
            executed=True,
            observed_result={
                "observedResult": "inventory_changed",
                "resultOutcome": "success",
                "delayedProgressReconciliation": True,
                "resourceProgressClassification": "resource_timeout_reconciled_success",
            },
        )

        counts = _loop_counts([unresolved, reconciled])

        self.assertEqual(counts["timeouts"], 1)
        self.assertEqual(counts["unresolvedTimeouts"], 1)
        self.assertEqual(counts["timeoutClassifications"]["resource_timeout_no_progress"], 1)
        self.assertEqual(counts["timeoutClassifications"]["resource_timeout_reconciled_success"], 1)
        self.assertEqual(counts["timeoutReasons"]["resource_timeout_no_progress"], 1)
        self.assertEqual(counts["timeoutActionTypes"]["select_resource_target"], 1)
        self.assertEqual(counts["timeoutRecoveredBy"]["delayed_progress_reconciliation"], 1)
        self.assertEqual(counts["evidenceAfterTimeout"], ["delayed_progress_reconciliation"])

    def test_loop_counts_reports_edge_rejects_and_edge_camera_reacquire(self):
        edge_reject = ExecutionResult(
            status="WARN",
            proposed_action="navigate_to_service",
            dry_run=False,
            executed=False,
            proposal={
                "targetExplanation": {
                    "routeProjectionStatus": {
                        "classification": "edge_clipped",
                        "actionableByCanvas": False,
                    }
                }
            },
        )
        edge_camera = ExecutionResult(
            status="PASS",
            proposed_action="navigate_to_service",
            dry_run=False,
            executed=True,
            commands=[{"type": "navigation_reacquire_camera_waypoint", "reason": "waypoint_edge_projection"}],
            action_trace={"reacquisition": {"primaryWaypointFailure": "waypoint_edge_projection", "cameraTriggeredBy": "edge_projection"}},
        )

        counts = _loop_counts([edge_reject, edge_camera])

        self.assertEqual(counts["edgeRouteClicksRejected"], 2)
        self.assertEqual(counts["cameraReacquireOnEdgeCount"], 1)

    def test_navigation_path_tile_rejects_degenerate_origin_projection(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            status="WARN",
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3203, "worldY": 3238, "plane": 0},
            confidence=0.72,
            missing_capabilities=["click_point"],
            warnings=["missing click point or key action"],
        )

        def snapshot_fetch(_url, **kwargs):
            requests = kwargs.get("tile_projection_requests") or []
            return {
                "tileProjections": {
                    "schema": "tile_projection_response.v1",
                    "status": "PASS",
                    "tiles": [
                        {
                            "status": "PASS",
                            "worldX": requests[0]["worldX"],
                            "worldY": requests[0]["worldY"],
                            "plane": requests[0].get("plane", 0),
                            "geometryAvailable": True,
                            "onScreen": True,
                            "aimPoint": {"canvasX": 0, "canvasY": 0, "source": "tileProjectionCenter"},
                            "canvasCenter": {"x": 0, "y": 0},
                            "canvasTileBounds": {"x": 0, "y": 0, "w": 1, "h": 1},
                            "canvasTilePolygon": [[0, 0], [0, 0], [0, 0], [0, 0]],
                        }
                    ],
                }
            }

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            snapshot_fetch_func=snapshot_fetch,
        )

        self.assertEqual(result.status, "FAIL")
        self.assertFalse(result.executed)
        self.assertIn("click_point", result.missing_capabilities)
        self.assertTrue(any("degenerate canvas polygon" in warning for warning in result.warnings))
        self.assertEqual(result.commands, [])
        self.assertEqual(backend.calls, [])

    def test_navigation_path_tile_projection_uses_structured_alternate_when_primary_offscreen(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            status="WARN",
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3203, "worldY": 3238, "plane": 0},
            target_explanation={
                "destinationTile": {"worldX": 3203, "worldY": 3238, "plane": 0},
                "predictedPathTiles": [
                    {"worldX": 3196, "worldY": 3240, "plane": 0},
                    {"worldX": 3197, "worldY": 3240, "plane": 0},
                    {"worldX": 3198, "worldY": 3240, "plane": 0},
                    {"worldX": 3199, "worldY": 3239, "plane": 0},
                    {"worldX": 3200, "worldY": 3239, "plane": 0},
                    {"worldX": 3201, "worldY": 3239, "plane": 0},
                    {"worldX": 3202, "worldY": 3239, "plane": 0},
                    {"worldX": 3203, "worldY": 3238, "plane": 0},
                ],
            },
            confidence=0.72,
            missing_capabilities=["click_point"],
            warnings=["missing click point or key action"],
        )
        request_batches = []

        def tile_payload(request, *, status="PASS", on_screen=True, x=240, y=180):
            return {
                "status": status,
                "worldX": request["worldX"],
                "worldY": request["worldY"],
                "plane": request.get("plane", 0),
                "geometryAvailable": True,
                "onScreen": on_screen,
                "aimPoint": {"canvasX": x, "canvasY": y, "source": "tileProjectionCenter"},
                "canvasTileBounds": {"x": x - 10, "y": y - 10, "w": 20, "h": 20},
                "canvasTilePolygon": [[x - 10, y - 10], [x + 10, y - 10], [x + 10, y + 10], [x - 10, y + 10]],
                "reason": "tile projection is outside the visible viewport" if not on_screen else None,
            }

        def snapshot_fetch(_url, **kwargs):
            requests = kwargs.get("tile_projection_requests") or []
            if requests:
                request_batches.append(list(requests))
                if len(requests) == 1:
                    return {
                        "payloads": {"baseline": {"player": {"worldX": 3195, "worldY": 3240, "plane": 0}}},
                        "tileProjections": {
                            "schema": "tile_projection_response.v1",
                            "status": "WARN",
                            "tiles": [tile_payload(requests[0], status="WARN", on_screen=False, x=816, y=359)],
                        },
                    }
                tiles = []
                for index, request in enumerate(requests):
                    tiles.append(tile_payload(request, status="WARN", on_screen=False, x=800, y=360) if index == 0 else tile_payload(request, x=260, y=190))
                return {"tileProjections": {"schema": "tile_projection_response.v1", "status": "PASS", "tiles": tiles}}
            return {"payloads": {"baseline": {"player": {"worldX": 3195, "worldY": 3240, "plane": 0}}}}

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            snapshot_fetch_func=snapshot_fetch,
            navigation_options=Namespace(max_waypoint_alternates=3),
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(len(request_batches), 2)
        self.assertIn(result.proposal["targetTile"], [{"worldX": 3201, "worldY": 3239, "plane": 0}, {"worldX": 3202, "worldY": 3238, "plane": 0}])
        self.assertEqual(result.proposal["suggestedClickPoint"], {"x": 260, "y": 190})
        self.assertTrue(any("selected structured alternate" in warning for warning in result.warnings))

    def test_navigation_path_tile_projection_skips_alternate_outside_movement_safety(self):
        backend = MovementSafetyBackend({"x": 100, "y": 100, "width": 500, "height": 500})
        proposal = ActionProposal(
            status="WARN",
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3203, "worldY": 3238, "plane": 0},
            target_explanation={
                "destinationTile": {"worldX": 3203, "worldY": 3238, "plane": 0},
                "predictedPathTiles": [
                    {"worldX": 3196, "worldY": 3240, "plane": 0},
                    {"worldX": 3197, "worldY": 3240, "plane": 0},
                    {"worldX": 3198, "worldY": 3240, "plane": 0},
                    {"worldX": 3199, "worldY": 3239, "plane": 0},
                    {"worldX": 3200, "worldY": 3239, "plane": 0},
                    {"worldX": 3201, "worldY": 3239, "plane": 0},
                    {"worldX": 3202, "worldY": 3239, "plane": 0},
                    {"worldX": 3203, "worldY": 3238, "plane": 0},
                ],
            },
            confidence=0.72,
            missing_capabilities=["click_point"],
            warnings=["missing click point or key action"],
        )
        alternate_batch = {"seen": False}

        def tile_payload(request, *, on_screen=True, x=240, y=180):
            return {
                "status": "PASS" if on_screen else "WARN",
                "worldX": request["worldX"],
                "worldY": request["worldY"],
                "plane": request.get("plane", 0),
                "geometryAvailable": True,
                "onScreen": on_screen,
                "aimPoint": {"canvasX": x, "canvasY": y, "source": "tileProjectionCenter"},
                "canvasTileBounds": {"x": x - 10, "y": y - 10, "w": 20, "h": 20},
                "canvasTilePolygon": [[x - 10, y - 10], [x + 10, y - 10], [x + 10, y + 10], [x - 10, y + 10]],
                "reason": "tile projection is outside the visible viewport" if not on_screen else None,
            }

        def snapshot_fetch(_url, **kwargs):
            requests = kwargs.get("tile_projection_requests") or []
            if len(requests) == 1:
                return {
                    "payloads": {"baseline": {"player": {"worldX": 3195, "worldY": 3240, "plane": 0}}},
                    "tileProjections": {
                        "schema": "tile_projection_response.v1",
                        "status": "WARN",
                        "tiles": [tile_payload(requests[0], on_screen=False, x=816, y=359)],
                    },
                }
            if requests:
                alternate_batch["seen"] = True
                tiles = []
                for index, request in enumerate(requests):
                    tiles.append(tile_payload(request, x=50, y=300) if index == 0 else tile_payload(request, x=260, y=190))
                return {"tileProjections": {"schema": "tile_projection_response.v1", "status": "PASS", "tiles": tiles}}
            return {"payloads": {"baseline": {"player": {"worldX": 3195, "worldY": 3240, "plane": 0}}}}

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            snapshot_fetch_func=snapshot_fetch,
            navigation_options=Namespace(max_waypoint_alternates=3),
        )

        self.assertEqual(result.status, "PASS", result.warnings)
        self.assertTrue(alternate_batch["seen"])
        self.assertEqual(backend.converted_points[:2], [{"x": 50, "y": 300}, {"x": 260, "y": 190}])
        self.assertEqual(result.proposal["suggestedClickPoint"], {"x": 260, "y": 190})
        self.assertTrue(result.click_point_resolution["movementSafetyPreflight"]["targetInsideAllowedRegion"])

    def test_navigation_path_tile_alternates_without_seed_player_tile(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            status="WARN",
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3203, "worldY": 3238, "plane": 0},
            target_explanation={
                "destinationTile": {"worldX": 3203, "worldY": 3238, "plane": 0},
                "predictedPathTiles": [
                    {"worldX": 3196, "worldY": 3240, "plane": 0},
                    {"worldX": 3197, "worldY": 3240, "plane": 0},
                    {"worldX": 3198, "worldY": 3240, "plane": 0},
                    {"worldX": 3199, "worldY": 3239, "plane": 0},
                    {"worldX": 3200, "worldY": 3239, "plane": 0},
                    {"worldX": 3201, "worldY": 3239, "plane": 0},
                    {"worldX": 3202, "worldY": 3239, "plane": 0},
                    {"worldX": 3203, "worldY": 3238, "plane": 0},
                ],
            },
            confidence=0.72,
            missing_capabilities=["click_point"],
            warnings=["missing click point or key action"],
        )
        request_batches = []

        def tile_payload(request, *, on_screen=True, x=240, y=180):
            return {
                "status": "PASS" if on_screen else "WARN",
                "worldX": request["worldX"],
                "worldY": request["worldY"],
                "plane": request.get("plane", 0),
                "geometryAvailable": True,
                "onScreen": on_screen,
                "aimPoint": {"canvasX": x, "canvasY": y, "source": "tileProjectionCenter"},
                "canvasTileBounds": {"x": x - 10, "y": y - 10, "w": 20, "h": 20},
                "canvasTilePolygon": [[x - 10, y - 10], [x + 10, y - 10], [x + 10, y + 10], [x - 10, y + 10]],
                "reason": "tile projection is outside the visible viewport" if not on_screen else None,
            }

        def snapshot_fetch(_url, **kwargs):
            requests = kwargs.get("tile_projection_requests") or []
            if requests:
                request_batches.append(list(requests))
                if len(requests) == 1:
                    return {
                        "tileProjections": {
                            "schema": "tile_projection_response.v1",
                            "status": "WARN",
                            "tiles": [tile_payload(requests[0], on_screen=False, x=816, y=359)],
                        },
                    }
                return {
                    "tileProjections": {
                        "schema": "tile_projection_response.v1",
                        "status": "PASS",
                        "tiles": [tile_payload(request, x=260, y=190) for request in requests],
                    },
                }
            return {}

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            snapshot_fetch_func=snapshot_fetch,
            navigation_options=Namespace(max_waypoint_alternates=3),
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(len(request_batches), 2)
        self.assertEqual(result.proposal["targetTile"], {"worldX": 3196, "worldY": 3240, "plane": 0})
        self.assertEqual(result.proposal["suggestedClickPoint"], {"x": 260, "y": 190})
        self.assertTrue(any("selected structured alternate" in warning for warning in result.warnings))

    def test_navigation_alternate_hover_skips_volatile_walk_here_tile(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 10, "worldY": 9, "plane": 0},
            target_explanation={
                "destinationTile": {"worldX": 10, "worldY": 3, "plane": 0},
                "predictedPathTiles": [
                    {"worldX": 10, "worldY": 9, "plane": 0},
                    {"worldX": 10, "worldY": 8, "plane": 0},
                    {"worldX": 10, "worldY": 7, "plane": 0},
                    {"worldX": 10, "worldY": 6, "plane": 0},
                ],
            },
        )
        hover_by_canvas_y = {
            188: {
                "topOption": "Walk here",
                "topTarget": "",
                "topType": "WALK",
                "entries": [{"option": "Walk here", "target": "", "type": "WALK"}],
                "postMenuSortTail": [
                    {
                        "mouseCanvasX": 240,
                        "mouseCanvasY": 188,
                        "topOption": "Attack",
                        "topTarget": "Goblin",
                        "topType": "NPC_FIRST_OPTION",
                        "entries": [{"option": "Attack", "target": "Goblin", "type": "NPC_FIRST_OPTION"}],
                    }
                ],
            },
            196: {
                "topOption": "Walk here",
                "topTarget": "",
                "topType": "WALK",
                "entries": [{"option": "Walk here", "target": "", "type": "WALK"}],
                "postMenuSortTail": [],
            },
        }

        def snapshot_fetch(_url, **kwargs):
            requests = kwargs.get("tile_projection_requests") or []
            if requests:
                tiles = []
                for request in requests:
                    canvas_y = 180 + (9 - request["worldY"]) * 8
                    tiles.append(
                        {
                            "status": "PASS",
                            "worldX": request["worldX"],
                            "worldY": request["worldY"],
                            "plane": request.get("plane", 0),
                            "geometryAvailable": True,
                            "onScreen": True,
                            "aimPoint": {"canvasX": 240, "canvasY": canvas_y, "source": "tileProjectionCenter"},
                            "canvasTileBounds": {"x": 230, "y": canvas_y - 10, "w": 20, "h": 20},
                            "canvasTilePolygon": [
                                [230, canvas_y - 10],
                                [250, canvas_y - 10],
                                [250, canvas_y + 10],
                                [230, canvas_y + 10],
                            ],
                        }
                    )
                return {
                    "payloads": {"baseline": {"player": {"worldX": 10, "worldY": 10, "plane": 0}}},
                    "tileProjections": {"schema": "tile_projection_response.v1", "status": "PASS", "tiles": tiles},
                }
            current_canvas = backend.converted_points[-1] if backend.converted_points else {"x": 240, "y": 196}
            mouse_y = current_canvas["y"]
            sample = dict(hover_by_canvas_y.get(mouse_y, hover_by_canvas_y[196]))
            sample.update({"mouseCanvasX": current_canvas["x"], "mouseCanvasY": mouse_y, "wallTimeMillis": 2000})
            for item in sample.get("postMenuSortTail") or []:
                item.setdefault("wallTimeMillis", 1990)
            return {
                "payloads": {"baseline": {"player": {"worldX": 10, "worldY": 10, "plane": 0}}},
                "hoverMenu": sample,
                "clientTickHot": {"postMenuSortTail": list(sample.get("postMenuSortTail") or [])},
            }

        result = _try_navigation_alternate_hover(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            hover_options=HoverConfirmationOptions(
                enabled=True,
                hover_only=False,
                snapshot_url="http://snapshot",
                timeout_ms=0,
                poll_ms=1,
                tolerance_px=3,
            ),
            navigation_options=Namespace(
                max_waypoint_alternates=3,
                min_route_progress_tiles=1,
                route_waypoint_lookahead_tiles=2,
                route_waypoint_max_horizon_tiles=4,
                max_route_waypoint_distance=10,
            ),
            snapshot_url="http://snapshot",
            snapshot_fetch_func=snapshot_fetch,
            monotonic_func=IncrementingClock(start=1.0, step=0.01),
            wall_time_millis_func=lambda: 1000,
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["attempts"][0]["reason"], "volatile_hover_zone")
        self.assertNotEqual(result["proposal"].target_tile, {"worldX": 10, "worldY": 8, "plane": 0})
        self.assertEqual(result["attempts"][-1]["accepted"], True)

    def test_navigation_pre_click_volatility_reacquires_nonvolatile_waypoint(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            status="PASS",
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 10, "worldY": 8, "plane": 0},
            suggested_click_point={"x": 240, "y": 188},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1240, "y": 2188},
            click_point_resolution={"status": "PASS", "screenClickPoint": {"x": 1240, "y": 2188}},
            target_explanation={
                "name": "Service waypoint",
                "destinationTile": {"worldX": 10, "worldY": 3, "plane": 0},
                "predictedPathTiles": [
                    {"worldX": 10, "worldY": 8, "plane": 0},
                    {"worldX": 10, "worldY": 7, "plane": 0},
                    {"worldX": 10, "worldY": 6, "plane": 0},
                    {"worldX": 10, "worldY": 5, "plane": 0},
                ],
            },
        )
        hover_calls = {"count": 0}

        def walk_sample(x, y, *, volatile=False):
            tail = []
            if volatile:
                tail.append(
                    {
                        "mouseCanvasX": x,
                        "mouseCanvasY": y,
                        "wallTimeMillis": 1990,
                        "topOption": "Attack",
                        "topTarget": "Goblin",
                        "topType": "NPC_FIRST_OPTION",
                        "entries": [{"option": "Attack", "target": "Goblin", "type": "NPC_FIRST_OPTION"}],
                    }
                )
            return {
                "wallTimeMillis": 2000,
                "mouseCanvasX": x,
                "mouseCanvasY": y,
                "topOption": "Walk here",
                "topTarget": "",
                "topType": "WALK",
                "entries": [{"option": "Walk here", "target": "", "type": "WALK"}],
                "postMenuSortTail": tail,
            }

        def snapshot_fetch(_url, **kwargs):
            requests = kwargs.get("tile_projection_requests") or []
            if requests:
                tiles = []
                for request in requests:
                    canvas_y = 180 + (8 - request["worldY"]) * 8
                    tiles.append(
                        {
                            "status": "PASS",
                            "worldX": request["worldX"],
                            "worldY": request["worldY"],
                            "plane": request.get("plane", 0),
                            "geometryAvailable": True,
                            "onScreen": True,
                            "aimPoint": {"canvasX": 240, "canvasY": canvas_y, "source": "tileProjectionCenter"},
                            "canvasTileBounds": {"x": 230, "y": canvas_y - 10, "w": 20, "h": 20},
                            "canvasTilePolygon": [
                                [230, canvas_y - 10],
                                [250, canvas_y - 10],
                                [250, canvas_y + 10],
                                [230, canvas_y + 10],
                            ],
                        }
                    )
                return {
                    "payloads": {"baseline": {"player": {"worldX": 10, "worldY": 9, "plane": 0}}},
                    "tileProjections": {"schema": "tile_projection_response.v1", "status": "PASS", "tiles": tiles},
                }
            hover_calls["count"] += 1
            current_canvas = backend.converted_points[-1] if backend.converted_points else {"x": 240, "y": 188}
            volatile = hover_calls["count"] == 2 and current_canvas["y"] == 188
            sample = walk_sample(current_canvas["x"], current_canvas["y"], volatile=volatile)
            return {
                "payloads": {"baseline": {"player": {"worldX": 10, "worldY": 9, "plane": 0}}},
                "hoverMenu": sample,
                "clientTickHot": {"postMenuSortTail": list(sample.get("postMenuSortTail") or [])},
            }

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=False,
            hover_options=HoverConfirmationOptions(
                enabled=True,
                hover_only=False,
                snapshot_url="http://snapshot",
                timeout_ms=0,
                poll_ms=1,
                tolerance_px=3,
                click_hold_ms=0,
            ),
            snapshot_fetch_func=snapshot_fetch,
            monotonic_func=IncrementingClock(start=1.0, step=0.01),
            wall_time_millis_func=lambda: 1000,
            navigation_options=Namespace(
                max_waypoint_alternates=3,
                max_navigation_reacquire_rounds=3,
                max_hover_checks_per_waypoint=1,
                min_route_progress_tiles=1,
                route_waypoint_lookahead_tiles=2,
                route_waypoint_max_horizon_tiles=4,
                max_route_waypoint_distance=10,
            ),
        )

        self.assertEqual(result.status, "PASS", result.warnings)
        self.assertTrue(result.executed)
        self.assertNotEqual(result.proposal["targetTile"], {"worldX": 10, "worldY": 8, "plane": 0})
        self.assertIn("navigation_reacquire_volatile_waypoint", [command["type"] for command in result.commands])

    def test_navigation_alternate_requests_include_near_mid_path_when_long_primary_unprojectable(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3200, "worldY": 3224, "plane": 0},
            target_explanation={
                "destinationTile": {"worldX": 3205, "worldY": 3232, "plane": 0},
                "predictedPathTiles": [
                    {"worldX": 3200, "worldY": 3235, "plane": 0},
                    {"worldX": 3200, "worldY": 3234, "plane": 0},
                    {"worldX": 3200, "worldY": 3233, "plane": 0},
                    {"worldX": 3200, "worldY": 3232, "plane": 0},
                    {"worldX": 3200, "worldY": 3231, "plane": 0},
                    {"worldX": 3200, "worldY": 3230, "plane": 0},
                    {"worldX": 3200, "worldY": 3229, "plane": 0},
                    {"worldX": 3200, "worldY": 3228, "plane": 0},
                    {"worldX": 3200, "worldY": 3227, "plane": 0},
                    {"worldX": 3200, "worldY": 3226, "plane": 0},
                    {"worldX": 3200, "worldY": 3225, "plane": 0},
                    {"worldX": 3200, "worldY": 3224, "plane": 0},
                    {"worldX": 3200, "worldY": 3223, "plane": 0},
                    {"worldX": 3200, "worldY": 3222, "plane": 0},
                    {"worldX": 3199, "worldY": 3221, "plane": 0},
                    {"worldX": 3198, "worldY": 3220, "plane": 0},
                    {"worldX": 3198, "worldY": 3219, "plane": 0},
                    {"worldX": 3198, "worldY": 3218, "plane": 0},
                    {"worldX": 3199, "worldY": 3218, "plane": 0},
                    {"worldX": 3200, "worldY": 3218, "plane": 0},
                    {"worldX": 3201, "worldY": 3218, "plane": 0},
                    {"worldX": 3201, "worldY": 3219, "plane": 0},
                    {"worldX": 3201, "worldY": 3220, "plane": 0},
                    {"worldX": 3201, "worldY": 3221, "plane": 0},
                ],
            },
        )
        snapshot = {"payloads": {"baseline": {"player": {"worldX": 3201, "worldY": 3236, "plane": 0}}}}

        requests = _navigation_alternate_tile_requests(
            proposal,
            snapshot,
            max_requests=8,
            navigation_options=Namespace(
                min_route_progress_tiles=3,
                route_waypoint_lookahead_tiles=12,
                route_waypoint_max_horizon_tiles=25,
                max_route_waypoint_distance=30,
            ),
        )

        requested_tiles = [(request["worldX"], request["worldY"]) for request in requests]
        self.assertIn((3200, 3233), requested_tiles[:2])
        self.assertIn((3200, 3230), requested_tiles[:3])
        self.assertNotIn((3200, 3224), requested_tiles)
        self.assertLess(requested_tiles.index((3200, 3233)), requested_tiles.index((3201, 3218)))

    def test_navigation_path_tile_projection_prefers_dynamic_input_geometry(self):
        backend = FailingCanvasBackend()
        proposal = ActionProposal(
            status="WARN",
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3195, "worldY": 3248, "plane": 0},
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 3932, "y": 107},
                "canvasSize": {"width": 1834, "height": 1205},
                "sourceCanvasSize": {"width": 765, "height": 503},
            },
            missing_capabilities=["click_point"],
            warnings=["missing click point or key action"],
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            snapshot_fetch_func=lambda *_args, **_kwargs: {
                "tileProjections": {
                    "schema": "tile_projection_response.v1",
                    "status": "PASS",
                    "tiles": [
                        {
                            "status": "PASS",
                            "worldX": 3195,
                            "worldY": 3248,
                            "plane": 0,
                            "geometryAvailable": True,
                            "onScreen": True,
                            "aimPoint": {"canvasX": 260, "canvasY": 164, "source": "tileProjectionCenter"},
                        }
                    ],
                }
            },
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.click_point_resolution["coordinateMethod"], "dynamic_input_geometry")
        self.assertEqual(result.click_point_resolution["coordinateResolver"], "input_geometry.resolve_screen_click_point")
        self.assertFalse(result.click_point_resolution["displayScaleApplied"])
        self.assertEqual(result.click_point_resolution["displayScaleReason"], "display_scale_identity")
        self.assertEqual(result.action_trace["intendedPoint"]["coordinateSpace"], "physical_pyautogui")
        self.assertEqual(result.action_trace["intendedPoint"]["windowBoundsSource"], "canvasScreenOrigin")
        self.assertFalse(result.action_trace["intendedPoint"]["displayScaleApplied"])
        self.assertEqual(result.action_trace["intendedPoint"]["displayScaleReason"], "display_scale_identity")
        self.assertEqual(result.action_trace["intendedPoint"]["coordinateResolver"], "input_geometry.resolve_screen_click_point")
        self.assertEqual(result.commands[0]["clickPoint"]["x"], 4555)
        self.assertEqual(result.commands[0]["clickPoint"]["y"], 500)

    def test_coordinate_transform_failure_bucket_attaches_to_failed_click_result(self):
        result = ExecutionResult(
            status="FAIL",
            proposed_action="navigate_to_service",
            dry_run=False,
            click_point_resolution={
                "status": "FAIL",
                "method": "dynamic_input_geometry",
                "coordinateResolver": "input_geometry.resolve_screen_click_point",
                "clickFailureBucket": "coordinate_transform_error",
                "warnings": ["resolved screen click point outside canvas bounds"],
                "missingCapabilities": ["screen_click_point"],
            },
            missing_capabilities=["screen_click_point"],
            action_trace={},
        )
        lifecycle = ActionLifecycleState(
            current_state="blocked",
            result_outcome="blocked",
            reason="screen_click_point_outside_movement_safety_region",
            observed_result={
                "observedResult": "no_click_safety_block",
                "resultOutcome": "blocked",
                "resultComplete": True,
            },
        )

        _apply_lifecycle(result, lifecycle)

        self.assertEqual(result.observed_result["clickFailureBucket"], "coordinate_transform_error")
        self.assertEqual(result.action_trace["clickFailureBucket"], "coordinate_transform_error")

    def test_executor_allows_inside_canvas_screen_point_when_geometry_passes(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            status="PASS",
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Tree",
            suggested_click_point={"x": 1200, "y": 2146},
            click_point_space="screen",
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 1000, "y": 2000},
                "canvasSize": {"width": 800, "height": 600},
                "clientWindowBounds": {"x": 990, "y": 1980, "width": 840, "height": 650},
            },
            target_explanation={"safeAimPoint": {"status": "PASS", "actionable": True}},
        )

        result = execute_action(proposal, backend=backend, movement_profile="instant_test", dry_run=False)

        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.executed)
        self.assertTrue(backend.calls)
        self.assertEqual(result.action_trace["inputGeometryValidation"]["status"], "PASS")

    def test_executor_blocks_outside_canvas_screen_point_before_click(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            status="PASS",
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Tree",
            suggested_click_point={"x": 2500, "y": 2146},
            click_point_space="screen",
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 1000, "y": 2000},
                "canvasSize": {"width": 800, "height": 600},
                "clientWindowBounds": {"x": 990, "y": 1980, "width": 840, "height": 650},
            },
            target_explanation={"safeAimPoint": {"status": "PASS", "actionable": True}},
        )

        result = execute_action(proposal, backend=backend, movement_profile="instant_test", dry_run=False)

        self.assertEqual(result.status, "FAIL")
        self.assertFalse(result.executed)
        self.assertEqual(backend.calls, [])
        self.assertEqual(result.lifecycle_state["reason"], "planned_point_outside_canvas")
        self.assertIn("input.geometry", result.missing_capabilities)
        self.assertEqual(result.action_trace["inputGeometryValidation"]["reason"], "planned_point_outside_canvas")

    def test_cursor_start_outside_allowed_region_is_coordinate_bucket(self):
        result = ExecutionResult(
            status="FAIL",
            proposed_action="interact_service_route_object",
            dry_run=False,
            warnings=["hover movement failed: ArduinoHIDError: cursor_start_outside_allowed_region"],
            missing_capabilities=["hover_movement"],
            action_trace={
                "mouseMove": {
                    "movementAbortedReason": "cursor_start_outside_allowed_region",
                    "cursorPositionBefore": {"x": 1713, "y": 862},
                    "allowedRegion": {"x": 9, "y": 41, "width": 1282, "height": 906},
                }
            },
        )
        lifecycle = ActionLifecycleState(
            current_state="blocked",
            result_outcome="blocked",
            reason="hover movement failed: cursor_start_outside_allowed_region",
            observed_result={
                "observedResult": "no_click_safety_block",
                "resultOutcome": "blocked",
                "resultComplete": True,
            },
        )

        _apply_lifecycle(result, lifecycle)

        self.assertEqual(result.observed_result["clickFailureBucket"], "coordinate_transform_error")
        self.assertEqual(result.action_trace["clickFailureBucket"], "coordinate_transform_error")

    def test_navigation_projection_rejects_edge_primary_and_uses_safe_alternate(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            status="WARN",
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3200, "worldY": 3240, "plane": 0},
            target_explanation={
                "destinationTile": {"worldX": 3204, "worldY": 3238, "plane": 0},
                "predictedPathTiles": [
                    {"worldX": 3201, "worldY": 3239, "plane": 0},
                    {"worldX": 3202, "worldY": 3238, "plane": 0},
                ],
            },
            missing_capabilities=["click_point"],
            warnings=["missing click point or key action"],
        )
        request_batches = []

        def projection_for(request, *, edge=False):
            if edge:
                return {
                    "status": "PASS",
                    "worldX": request["worldX"],
                    "worldY": request["worldY"],
                    "plane": request.get("plane", 0),
                    "geometryAvailable": True,
                    "onScreen": True,
                    "aimPoint": {"canvasX": 761, "canvasY": 250, "source": "tileProjectionCenter"},
                    "canvasTileBounds": {"x": 745, "y": 238, "w": 25, "h": 24},
                    "cameraViewport": {"canvasWidth": 765, "canvasHeight": 503, "viewportXOffset": 0, "viewportYOffset": 0, "viewportWidth": 765, "viewportHeight": 503},
                }
            return {
                "status": "PASS",
                "worldX": request["worldX"],
                "worldY": request["worldY"],
                "plane": request.get("plane", 0),
                "geometryAvailable": True,
                "onScreen": True,
                "aimPoint": {"canvasX": 260, "canvasY": 190, "source": "tileProjectionCenter"},
                "canvasTileBounds": {"x": 250, "y": 180, "w": 20, "h": 20},
                "cameraViewport": {"canvasWidth": 765, "canvasHeight": 503, "viewportXOffset": 0, "viewportYOffset": 0, "viewportWidth": 765, "viewportHeight": 503},
            }

        def snapshot_fetch(_url, **kwargs):
            requests = kwargs.get("tile_projection_requests") or []
            if requests:
                request_batches.append(list(requests))
                if len(requests) == 1:
                    return {
                        "payloads": {"baseline": {"player": {"worldX": 3200, "worldY": 3241, "plane": 0}}},
                        "tileProjections": {"schema": "tile_projection_response.v1", "status": "PASS", "tiles": [projection_for(requests[0], edge=True)]},
                    }
                return {
                    "tileProjections": {
                        "schema": "tile_projection_response.v1",
                        "status": "PASS",
                        "tiles": [projection_for(request) for request in requests],
                    }
                }
            return {"payloads": {"baseline": {"player": {"worldX": 3200, "worldY": 3241, "plane": 0}}}}

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            snapshot_fetch_func=snapshot_fetch,
            navigation_options=Namespace(
                max_waypoint_alternates=2,
                reject_edge_route_clicks=True,
                route_click_edge_margin_px=12,
                route_min_visible_area_ratio=0.7,
            ),
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(len(request_batches), 2)
        self.assertIn(
            result.proposal["targetTile"],
            [
                {"worldX": 3201, "worldY": 3239, "plane": 0},
                {"worldX": 3202, "worldY": 3238, "plane": 0},
            ],
        )
        self.assertEqual(result.proposal["targetExplanation"]["routeProjectionStatus"]["classification"], "visible")
        self.assertTrue(any("selected structured alternate" in warning for warning in result.warnings))

    def test_navigation_hover_reacquires_alternate_waypoint_when_primary_is_object_covered(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            status="WARN",
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 10, "worldY": 9, "plane": 0},
            target_explanation={
                "name": "Service waypoint",
                "classId": "service_route_anchor",
                "worldLocation": {"worldX": 12, "worldY": 8, "plane": 0},
                "predictedPathTiles": [
                    {"worldX": 11, "worldY": 9, "plane": 0},
                    {"worldX": 12, "worldY": 8, "plane": 0},
                ],
            },
            missing_capabilities=["click_point"],
            warnings=["missing click point or key action"],
        )
        hot_calls = {"count": 0}

        def projection_for(request):
            world_x = request["worldX"]
            world_y = request["worldY"]
            canvas_x = 100 if (world_x, world_y) == (10, 9) else 120
            canvas_y = 100 if (world_x, world_y) == (10, 9) else 120
            return {
                "status": "PASS",
                "worldX": world_x,
                "worldY": world_y,
                "plane": request.get("plane", 0),
                "geometryAvailable": True,
                "onScreen": True,
                "aimPoint": {"canvasX": canvas_x, "canvasY": canvas_y, "source": "tileProjectionCenter"},
            }

        def snapshot_fetch(_url, **kwargs):
            requests = kwargs.get("tile_projection_requests") or []
            if requests:
                return {
                    "tileProjections": {
                        "schema": "tile_projection_response.v1",
                        "status": "PASS",
                        "tiles": [projection_for(request) for request in requests],
                    }
                }
            hot_calls["count"] += 1
            if hot_calls["count"] == 1:
                return {
                    "hoverMenu": {
                        "wallTimeMillis": 2000,
                        "mouseCanvasX": 100,
                        "mouseCanvasY": 100,
                        "topOption": "Chop down",
                        "topTarget": "Tree",
                        "topIdentifier": 1276,
                    }
                }
            if hot_calls["count"] == 2:
                return {
                    "payloads": {
                        "baseline": {
                            "player": {"worldX": 10, "worldY": 10, "plane": 0}
                        }
                    }
                }
            return {
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 120,
                    "mouseCanvasY": 120,
                    "topOption": "Walk here",
                    "topTarget": "",
                    "topIdentifier": 0,
                }
            }

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            hover_options=HoverConfirmationOptions(
                enabled=True,
                hover_only=True,
                snapshot_url="http://snapshot",
                timeout_ms=1,
                poll_ms=1,
                tolerance_px=3,
            ),
            snapshot_fetch_func=snapshot_fetch,
            monotonic_func=IncrementingClock(start=1.0, step=0.01),
            wall_time_millis_func=lambda: 1000,
        )

        self.assertEqual(result.status, "PASS")
        self.assertFalse(result.executed)
        self.assertEqual(backend.calls, [("move", 1100, 2100), ("move", 1120, 2120)])
        self.assertTrue(result.hover_confirmation["confirmed"])
        self.assertEqual(result.hover_confirmation["sample"]["topOption"], "Walk here")
        self.assertNotEqual(result.proposal["targetTile"], {"worldX": 10, "worldY": 9, "plane": 0})
        self.assertTrue(any(command["type"] == "navigation_reacquire_alternate_waypoint" for command in result.commands))

    def test_service_hover_reacquires_alternate_safe_aimpoint_when_center_is_cancel(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="open_service",
            target_kind="service",
            target_name="Bank booth",
            suggested_click_point={"x": 470, "y": 344},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1470, "y": 2344},
            click_point_resolution={"status": "PASS", "screenClickPoint": {"x": 1470, "y": 2344}},
            target_explanation={
                "name": "Bank booth",
                "objectId": 18491,
                "expectedOptions": ["Bank", "Use", "Deposit"],
                "expectedTargets": ["Bank booth", "Banker", "Deposit box"],
                "safeAimPoint": {
                    "status": "PASS",
                    "sampledAimpoints": [
                        {"x": 470, "y": 344},
                        {"x": 470, "y": 316},
                        {"x": 443, "y": 316},
                    ],
                },
            },
            confidence=0.82,
        )
        snapshots = [
            {
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 470,
                    "mouseCanvasY": 344,
                    "topOption": "Cancel",
                    "topTarget": "",
                    "topIdentifier": 0,
                }
            },
            {
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 470,
                    "mouseCanvasY": 316,
                    "topOption": "Bank",
                    "topTarget": "Bank booth",
                    "topIdentifier": 18491,
                }
            },
        ]

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            hover_options=HoverConfirmationOptions(
                enabled=True,
                hover_only=True,
                snapshot_url="http://snapshot",
                timeout_ms=1,
                poll_ms=1,
                tolerance_px=3,
            ),
            snapshot_fetch_func=lambda *_args, **_kwargs: snapshots.pop(0),
            monotonic_func=IncrementingClock(start=1.0, step=0.01),
            wall_time_millis_func=lambda: 1000,
        )

        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.hover_confirmation["confirmed"])
        self.assertEqual(result.hover_confirmation["sample"]["topOption"], "Bank")
        self.assertEqual(result.proposal["suggestedClickPoint"], {"x": 470, "y": 316})
        self.assertEqual(backend.calls, [("move", 1470, 2344), ("move", 1470, 2316)])
        self.assertTrue(any(command["type"] == "service_reacquire_alternate_aimpoint" for command in result.commands))

    def test_navigation_alternate_waypoints_obey_max_option(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            status="WARN",
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 10, "worldY": 9, "plane": 0},
            target_explanation={
                "name": "Service waypoint",
                "classId": "service_route_anchor",
                "worldLocation": {"worldX": 16, "worldY": 8, "plane": 0},
                "predictedPathTiles": [
                    {"worldX": 11, "worldY": 9, "plane": 0},
                    {"worldX": 12, "worldY": 8, "plane": 0},
                    {"worldX": 13, "worldY": 8, "plane": 0},
                ],
            },
            missing_capabilities=["click_point"],
        )
        projected_request_batches = []

        def snapshot_fetch(_url, **kwargs):
            requests = kwargs.get("tile_projection_requests") or []
            if requests:
                projected_request_batches.append(list(requests))
                return {
                    "tileProjections": {
                        "schema": "tile_projection_response.v1",
                        "status": "PASS",
                        "tiles": [
                            {
                                "status": "PASS",
                                "worldX": request["worldX"],
                                "worldY": request["worldY"],
                                "plane": request.get("plane", 0),
                                "geometryAvailable": True,
                                "onScreen": True,
                                "aimPoint": {"canvasX": 120, "canvasY": 120, "source": "tileProjectionCenter"},
                            }
                            for request in requests
                        ],
                    }
                }
            if not projected_request_batches:
                return {
                    "hoverMenu": {
                        "wallTimeMillis": 2000,
                        "mouseCanvasX": 100,
                        "mouseCanvasY": 100,
                        "topOption": "Chop down",
                        "topTarget": "Tree",
                    }
                }
            return {
                "payloads": {"baseline": {"player": {"worldX": 10, "worldY": 10, "plane": 0}}},
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 120,
                    "mouseCanvasY": 120,
                    "topOption": "Chop down",
                    "topTarget": "Tree",
                },
            }

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            hover_options=HoverConfirmationOptions(
                enabled=True,
                hover_only=True,
                snapshot_url="http://snapshot",
                timeout_ms=0,
                poll_ms=1,
                tolerance_px=3,
            ),
            snapshot_fetch_func=snapshot_fetch,
            monotonic_func=IncrementingClock(start=1.0, step=0.01),
            wall_time_millis_func=lambda: 1000,
            navigation_options=Namespace(max_waypoint_alternates=2, max_hover_checks_per_waypoint=1),
        )

        self.assertEqual(result.status, "FAIL")
        self.assertGreater(len(projected_request_batches[0]), 0)
        self.assertLessEqual(len(projected_request_batches[0]), 2)
        attempts = result.action_trace["reacquisition"]["navigationAlternateWaypoints"]
        self.assertLessEqual(len(attempts), 2)

    def test_navigation_alternate_requests_skip_suppressed_route_tile(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3235, "worldY": 3224, "plane": 0},
            target_explanation={
                "name": "Service waypoint",
                "classId": "service_route_anchor",
                "destinationTile": {"worldX": 3240, "worldY": 3223, "plane": 0},
                "suppressedTargetKeysAtSelection": ["None:3236:3223:0:service_route_anchor"],
                "predictedPathTiles": [
                    {"worldX": 3236, "worldY": 3223, "plane": 0},
                    {"worldX": 3237, "worldY": 3223, "plane": 0},
                    {"worldX": 3238, "worldY": 3223, "plane": 0},
                ],
            },
        )
        snapshot = {"payloads": {"baseline": {"player": {"worldX": 3234, "worldY": 3226, "plane": 0}}}}

        requests = _navigation_alternate_tile_requests(
            proposal,
            snapshot,
            max_requests=3,
            navigation_options=Namespace(max_route_waypoint_distance=30, min_route_progress_tiles=1),
        )

        requested_tiles = {(request["worldX"], request["worldY"], request.get("plane", 0)) for request in requests}
        self.assertNotIn((3236, 3223, 0), requested_tiles)
        self.assertIn((3237, 3223, 0), requested_tiles)

    def test_path_tile_suppression_key_prefers_actual_waypoint_tile(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3236, "worldY": 3223, "plane": 0},
            target_explanation={
                "name": "Lumbridge Castle south entrance approach",
                "classId": "service_route_anchor",
                "targetKey": "None:3221:3218:0:service_route_anchor",
                "worldLocation": {"worldX": 3221, "worldY": 3218, "plane": 0},
            },
        )

        self.assertEqual(_target_key_from_proposal(proposal), "None:3236:3223:0:service_route_anchor")

    def test_navigation_trace_records_occluded_waypoint_failure(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 10, "worldY": 9, "plane": 0},
            suggested_click_point={"x": 100, "y": 100},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1100, "y": 2100},
            click_point_resolution={"status": "PASS", "screenClickPoint": {"x": 1100, "y": 2100}},
            target_explanation={
                "name": "Service waypoint",
                "classId": "service_route_anchor",
                "tileProjection": {
                    "status": "PASS",
                    "worldX": 10,
                    "worldY": 9,
                    "plane": 0,
                    "geometryAvailable": True,
                    "onScreen": True,
                    "aimPoint": {"canvasX": 100, "canvasY": 100, "source": "tileProjectionCenter"},
                },
            },
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            hover_options=HoverConfirmationOptions(
                enabled=True,
                hover_only=True,
                snapshot_url="http://snapshot",
                timeout_ms=0,
                poll_ms=1,
                tolerance_px=3,
            ),
            snapshot_fetch_func=lambda *_args, **_kwargs: {
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 100,
                    "mouseCanvasY": 100,
                    "topOption": "Chop down",
                    "topTarget": "Tree",
                    "topType": "GAME_OBJECT_FIRST_OPTION",
                }
            },
            monotonic_func=IncrementingClock(start=1.0, step=0.01),
            wall_time_millis_func=lambda: 1000,
            navigation_options=Namespace(max_waypoint_alternates=0, max_camera_adjustments_per_route_step=0),
        )

        self.assertEqual(result.status, "FAIL")
        self.assertEqual(result.action_trace["reacquisition"]["primaryWaypointFailure"], "waypoint_occluded_by_object")
        self.assertIn("Chop down", result.warnings[-1])

    def test_camera_exposure_score_classifies_walk_here_and_object_occlusion(self):
        exposed = camera_exposure_score(
            hover_sample={
                "mouseCanvasX": 120,
                "mouseCanvasY": 140,
                "topOption": "Walk here",
                "topTarget": "",
                "topType": "WALK",
            },
            canvas_point={"x": 120, "y": 140},
            projection={"onScreen": True, "geometryAvailable": True, "canvasTileBounds": {"x": 110, "y": 130, "w": 20, "h": 20}},
            tolerance_px=3,
        )
        occluded = camera_exposure_score(
            hover_sample={
                "mouseCanvasX": 120,
                "mouseCanvasY": 140,
                "topOption": "Chop down",
                "topTarget": "Tree",
                "topType": "GAME_OBJECT_FIRST_OPTION",
            },
            canvas_point={"x": 120, "y": 140},
            projection={"onScreen": True, "geometryAvailable": True, "canvasTileBounds": {"x": 110, "y": 130, "w": 20, "h": 20}},
            tolerance_px=3,
        )

        self.assertEqual(exposed["classification"], "exposed_walk_here")
        self.assertGreater(exposed["score"], 0)
        self.assertEqual(occluded["classification"], "occluded_by_object")
        self.assertLess(occluded["score"], 0)
        self.assertEqual(occluded["blockingHoverOption"], "Chop down")
        self.assertEqual(occluded["blockingHoverTarget"], "Tree")

    def test_navigation_hover_timeout_uses_rejected_object_sample_as_occlusion(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 10, "worldY": 9, "plane": 0},
        )
        reason = _navigation_hover_failure_reason(
            proposal,
            {
                "reason": "hover_confirm_timeout",
                "rejectedHoverSamples": [
                    {
                        "reason": "top_option_not_expected",
                        "sample": {
                            "topOption": "Chop down",
                            "topTarget": "Tree",
                            "topType": "GAME_OBJECT_FIRST_OPTION",
                        },
                    }
                ],
            },
        )

        self.assertEqual(reason, "waypoint_occluded_by_object")

    def test_navigation_hover_position_mismatch_is_classified_structurally(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 10, "worldY": 9, "plane": 0},
        )
        reason = _navigation_hover_failure_reason(
            proposal,
            {
                "reason": "hover_confirm_timeout",
                "latestMatch": {
                    "reason": "mouse_position_outside_tolerance",
                    "details": {"dx": 227, "dy": 19, "tolerancePx": 3, "mismatchReason": "hover_position_mismatch"},
                    "sample": {
                        "topOption": "Walk here",
                        "topTarget": "",
                        "topType": "WALK",
                    },
                },
            },
        )

        self.assertEqual(reason, "hover_position_mismatch")

    def test_executor_refuses_static_advisory_target_without_live_projection(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="return_to_resource_area",
            target_kind="path_tile",
            target_name="Resource return",
            target_tile={"worldX": 3203, "worldY": 3238, "plane": 0},
            action_target_source="static_route_prior",
            actionability="advisory_only",
            target_explanation={"targetSource": "static_route_prior", "actionability": "advisory_only"},
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=False,
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(result.status, "FAIL")
        self.assertFalse(result.executed)
        self.assertEqual(result.lifecycle_state["reason"], "static_target_not_executable")
        self.assertEqual(backend.calls, [])
        self.assertEqual(result.action_trace["humanInput"]["directBackendBypassCount"], 0)

    def test_camera_exposure_direction_reverses_after_worsening_score(self):
        direction = next_camera_direction_from_exposure(
            [
                {"cameraAction": "yaw_right_small", "exposureScoreBefore": {"score": -25}, "exposureScoreAfter": {"score": -40}},
            ],
            preferred_direction="right",
        )

        self.assertEqual(direction, "left")

    def test_camera_keyboard_hold_uses_key_down_and_releases_on_error(self):
        backend = FakeBackend()
        spec = camera_input_spec(method="keyboard_arrows", command="yaw_left_pitch_up")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            with hold_camera_input(backend, spec):
                raise RuntimeError("boom")

        self.assertEqual(
            backend.calls,
            [
                ("key_down", "left"),
                ("key_down", "up"),
                ("key_up", "up"),
                ("key_up", "left"),
            ],
        )

    def test_navigation_camera_reacquires_same_world_tile_after_camera_nudge(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 10, "worldY": 9, "plane": 0},
            suggested_click_point={"x": 100, "y": 100},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1100, "y": 2100},
            click_point_resolution={"status": "PASS", "screenClickPoint": {"x": 1100, "y": 2100}},
            target_explanation={
                "name": "Service waypoint",
                "classId": "service_route_anchor",
                "tileProjection": {
                    "status": "PASS",
                    "worldX": 10,
                    "worldY": 9,
                    "plane": 0,
                    "geometryAvailable": True,
                    "onScreen": True,
                    "aimPoint": {"canvasX": 100, "canvasY": 100, "source": "tileProjectionCenter"},
                },
            },
        )
        projection_requests = []
        hover_calls = {"count": 0}

        def snapshot_fetch(_url, **kwargs):
            requests = kwargs.get("tile_projection_requests") or []
            if requests:
                projection_requests.extend(list(requests))
                return {
                    "payloads": {"baseline": {"cameraViewport": {"cameraYaw": 101, "cameraPitch": 383}}},
                    "tileProjections": {
                        "schema": "tile_projection_response.v1",
                        "status": "PASS",
                        "tiles": [
                            {
                                "status": "PASS",
                                "worldX": request["worldX"],
                                "worldY": request["worldY"],
                                "plane": request.get("plane", 0),
                                "geometryAvailable": True,
                                "onScreen": True,
                                "aimPoint": {"canvasX": 130, "canvasY": 112, "source": "tileProjectionCenter"},
                                "canvasTileBounds": {"x": 120, "y": 102, "w": 20, "h": 20},
                            }
                            for request in requests
                        ],
                    },
                }
            hover_calls["count"] += 1
            if hover_calls["count"] == 1:
                return {
                    "payloads": {"baseline": {"cameraViewport": {"cameraYaw": 100, "cameraPitch": 383}}},
                    "hoverMenu": {
                        "wallTimeMillis": 2000,
                        "mouseCanvasX": 100,
                        "mouseCanvasY": 100,
                        "topOption": "Chop down",
                        "topTarget": "Tree",
                        "topType": "GAME_OBJECT_FIRST_OPTION",
                    },
                }
            return {
                "payloads": {"baseline": {"cameraViewport": {"cameraYaw": 101, "cameraPitch": 383}}},
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 130,
                    "mouseCanvasY": 112,
                    "topOption": "Walk here",
                    "topTarget": "",
                    "topType": "WALK",
                },
            }

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            hover_options=HoverConfirmationOptions(
                enabled=True,
                hover_only=True,
                snapshot_url="http://snapshot",
                timeout_ms=1,
                poll_ms=1,
                tolerance_px=3,
            ),
            snapshot_fetch_func=snapshot_fetch,
            monotonic_func=IncrementingClock(start=1.0, step=0.01),
            wall_time_millis_func=lambda: 1000,
            navigation_options=Namespace(
                max_navigation_reacquire_rounds=0,
                max_waypoint_alternates=0,
                camera_reacquire_waypoint=True,
                camera_follow_target=True,
                camera_reacquire_timeout_ms=500,
                camera_exposure_max_ms=500,
                camera_probe_ms=1,
                camera_sample_interval_ms=1,
                camera_max_nudges=1,
                camera_max_direction_switches=1,
                camera_method="keyboard_arrows",
                camera_adjust_direction="right",
                camera_min_score_improvement=1,
                camera_min_projection_delta_px=2,
                camera_allow_pitch_adjust=False,
                camera_allow_diagonal=False,
            ),
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(
            backend.calls,
            [
                ("move", 1100, 2100),
                ("key_down", "right"),
                ("move", 1130, 2112),
                ("key_up", "right"),
            ],
        )
        self.assertTrue(all((request["worldX"], request["worldY"]) == (10, 9) for request in projection_requests))
        self.assertEqual(result.proposal["targetTile"], {"worldX": 10, "worldY": 9, "plane": 0})
        self.assertEqual(result.hover_confirmation["sample"]["topOption"], "Walk here")
        reacquisition = result.action_trace["reacquisition"]
        self.assertTrue(reacquisition["waypointReacquiredByCamera"])
        self.assertEqual(reacquisition["cameraExposureAttempts"][0]["targetWorldTile"]["worldX"], 10)
        self.assertTrue(reacquisition["cameraExposureAttempts"][0]["cameraMoved"])
        self.assertEqual(reacquisition["cameraExposureAttempts"][0]["cameraMethod"], "keyboard_arrows")
        self.assertGreaterEqual(len(reacquisition["cameraExposureAttempts"][0]["samples"]), 1)

    def test_navigation_camera_reacquires_edge_projected_waypoint_before_click(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            status="WARN",
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 10, "worldY": 9, "plane": 0},
            target_explanation={"name": "Service waypoint", "classId": "service_route_anchor"},
            missing_capabilities=["click_point"],
            warnings=["missing click point or key action"],
        )
        projection_requests = []

        def snapshot_fetch(_url, **kwargs):
            requests = kwargs.get("tile_projection_requests") or []
            if requests:
                projection_requests.extend(list(requests))
                tiles = []
                for request in requests:
                    if len(projection_requests) == 1:
                        tiles.append(
                            {
                                "status": "PASS",
                                "worldX": request["worldX"],
                                "worldY": request["worldY"],
                                "plane": request.get("plane", 0),
                                "geometryAvailable": True,
                                "onScreen": True,
                                "aimPoint": {"canvasX": 762, "canvasY": 250, "source": "tileProjectionCenter"},
                                "canvasTileBounds": {"x": 744, "y": 238, "w": 24, "h": 24},
                                "cameraViewport": {
                                    "canvasWidth": 765,
                                    "canvasHeight": 503,
                                    "viewportXOffset": 0,
                                    "viewportYOffset": 0,
                                    "viewportWidth": 765,
                                    "viewportHeight": 503,
                                    "cameraYaw": 100,
                                    "cameraPitch": 383,
                                },
                            }
                        )
                    else:
                        tiles.append(
                            {
                                "status": "PASS",
                                "worldX": request["worldX"],
                                "worldY": request["worldY"],
                                "plane": request.get("plane", 0),
                                "geometryAvailable": True,
                                "onScreen": True,
                                "aimPoint": {"canvasX": 130, "canvasY": 112, "source": "tileProjectionCenter"},
                                "canvasTileBounds": {"x": 120, "y": 102, "w": 20, "h": 20},
                                "cameraViewport": {
                                    "canvasWidth": 765,
                                    "canvasHeight": 503,
                                    "viewportXOffset": 0,
                                    "viewportYOffset": 0,
                                    "viewportWidth": 765,
                                    "viewportHeight": 503,
                                    "cameraYaw": 101,
                                    "cameraPitch": 383,
                                },
                            }
                        )
                return {
                    "payloads": {"baseline": {"cameraViewport": {"cameraYaw": 101, "cameraPitch": 383}}},
                    "tileProjections": {"schema": "tile_projection_response.v1", "status": "PASS", "tiles": tiles},
                }
            return {
                "payloads": {"baseline": {"cameraViewport": {"cameraYaw": 101, "cameraPitch": 383}}},
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 130,
                    "mouseCanvasY": 112,
                    "topOption": "Walk here",
                    "topTarget": "",
                    "topType": "WALK",
                },
            }

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            hover_options=HoverConfirmationOptions(
                enabled=True,
                hover_only=True,
                snapshot_url="http://snapshot",
                timeout_ms=1,
                poll_ms=1,
                tolerance_px=3,
            ),
            snapshot_fetch_func=snapshot_fetch,
            monotonic_func=IncrementingClock(start=1.0, step=0.01),
            wall_time_millis_func=lambda: 1000,
            navigation_options=Namespace(
                max_navigation_reacquire_rounds=0,
                max_waypoint_alternates=0,
                reject_edge_route_clicks=True,
                camera_reacquire_on_edge_projection=True,
                route_click_edge_margin_px=12,
                route_min_visible_area_ratio=0.7,
                camera_reacquire_waypoint=True,
                camera_follow_target=True,
                camera_reacquire_timeout_ms=500,
                camera_exposure_max_ms=500,
                camera_probe_ms=1,
                camera_sample_interval_ms=1,
                camera_max_nudges=1,
                camera_max_direction_switches=1,
                camera_method="keyboard_arrows",
                camera_adjust_direction="right",
                camera_min_score_improvement=1,
                camera_min_projection_delta_px=2,
                camera_allow_pitch_adjust=False,
                camera_allow_diagonal=False,
            ),
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(backend.calls, [("key_down", "right"), ("move", 1130, 2112), ("key_up", "right")])
        self.assertTrue(all((request["worldX"], request["worldY"]) == (10, 9) for request in projection_requests))
        self.assertTrue(result.action_trace["reacquisition"]["waypointReacquiredByCamera"])
        self.assertEqual(result.action_trace["reacquisition"]["cameraTriggeredBy"], "edge_projection")
        self.assertTrue(result.action_trace["reacquisition"]["cameraImprovedProjection"])
        self.assertIn("projectionBefore", result.action_trace["reacquisition"])
        self.assertIn("projectionAfter", result.action_trace["reacquisition"])
        self.assertEqual(result.commands[0]["reason"], "waypoint_edge_projection")

    def test_navigation_camera_reacquire_enforces_max_nudges(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 10, "worldY": 9, "plane": 0},
            suggested_click_point={"x": 100, "y": 100},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1100, "y": 2100},
            click_point_resolution={"status": "PASS", "screenClickPoint": {"x": 1100, "y": 2100}},
            target_explanation={
                "name": "Service waypoint",
                "classId": "service_route_anchor",
                "tileProjection": {
                    "status": "PASS",
                    "worldX": 10,
                    "worldY": 9,
                    "plane": 0,
                    "geometryAvailable": True,
                    "onScreen": True,
                    "aimPoint": {"canvasX": 100, "canvasY": 100, "source": "tileProjectionCenter"},
                },
            },
        )

        def snapshot_fetch(_url, **kwargs):
            requests = kwargs.get("tile_projection_requests") or []
            if requests:
                return {
                    "tileProjections": {
                        "schema": "tile_projection_response.v1",
                        "status": "PASS",
                        "tiles": [
                            {
                                "status": "PASS",
                                "worldX": request["worldX"],
                                "worldY": request["worldY"],
                                "plane": request.get("plane", 0),
                                "geometryAvailable": True,
                                "onScreen": True,
                                "aimPoint": {"canvasX": 100, "canvasY": 100, "source": "tileProjectionCenter"},
                            }
                            for request in requests
                        ],
                    },
                }
            return {
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 100,
                    "mouseCanvasY": 100,
                    "topOption": "Chop down",
                    "topTarget": "Tree",
                    "topType": "GAME_OBJECT_FIRST_OPTION",
                }
            }

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            hover_options=HoverConfirmationOptions(
                enabled=True,
                hover_only=True,
                snapshot_url="http://snapshot",
                timeout_ms=0,
                poll_ms=1,
                tolerance_px=3,
            ),
            snapshot_fetch_func=snapshot_fetch,
            monotonic_func=IncrementingClock(start=1.0, step=0.01),
            wall_time_millis_func=lambda: 1000,
            navigation_options=Namespace(
                max_navigation_reacquire_rounds=0,
                max_waypoint_alternates=0,
                camera_reacquire_waypoint=True,
                camera_follow_target=True,
                camera_reacquire_timeout_ms=500,
                camera_exposure_max_ms=500,
                camera_probe_ms=1,
                camera_sample_interval_ms=1,
                camera_max_nudges=2,
                camera_max_direction_switches=2,
                camera_method="keyboard_arrows",
                camera_adjust_direction="right",
                camera_min_score_improvement=1,
                camera_min_projection_delta_px=2,
                camera_allow_pitch_adjust=False,
                camera_allow_diagonal=False,
            ),
        )

        self.assertEqual(result.status, "FAIL")
        self.assertEqual(
            [call for call in backend.calls if call[0] in {"key_down", "key_up"}],
            [("key_down", "right"), ("key_up", "right"), ("key_down", "left"), ("key_up", "left")],
        )
        self.assertEqual(len(result.action_trace["reacquisition"]["cameraExposureAttempts"]), 2)
        self.assertFalse(result.action_trace["reacquisition"].get("waypointReacquiredByCamera", False))
        self.assertEqual(result.action_trace["reacquisition"]["cameraExposureAttempts"][0]["reason"], "no_camera_delta")

    def test_loop_summary_counts_only_real_camera_movement(self):
        no_delta = ExecutionResult(
            status="FAIL",
            proposed_action="navigate_to_service",
            dry_run=True,
            action_trace={
                "reacquisition": {
                    "cameraExposureAttempts": [
                        {"cameraMoved": False, "reason": "no_camera_delta"},
                    ]
                }
            },
        )
        moved = ExecutionResult(
            status="FAIL",
            proposed_action="navigate_to_service",
            dry_run=True,
            action_trace={
                "reacquisition": {
                    "cameraExposureAttempts": [
                        {"cameraMoved": True, "reason": "hover_confirm_timeout"},
                    ]
                }
            },
        )

        self.assertEqual(_loop_counts([no_delta])["cameraAdjustments"], 0)
        self.assertEqual(_loop_counts([moved])["cameraAdjustments"], 1)

    def test_pre_click_hover_confirmation_blocks_menu_flip_before_click(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 10, "worldY": 9, "plane": 0},
            suggested_click_point={"x": 100, "y": 100},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1100, "y": 2100},
            click_point_resolution={"status": "PASS", "screenClickPoint": {"x": 1100, "y": 2100}},
            target_explanation={"name": "Service waypoint", "classId": "service_route_anchor"},
        )
        hot_calls = {"count": 0}

        def snapshot_fetch(_url, **_kwargs):
            hot_calls["count"] += 1
            if hot_calls["count"] == 1:
                return {
                    "hoverMenu": {
                        "wallTimeMillis": 2000,
                        "mouseCanvasX": 100,
                        "mouseCanvasY": 100,
                        "topOption": "Walk here",
                        "topTarget": "",
                        "topIdentifier": 0,
                    }
                }
            return {
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 100,
                    "mouseCanvasY": 100,
                    "topOption": "Chop down",
                    "topTarget": "Tree",
                    "topIdentifier": 1276,
                }
            }

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=False,
            hover_options=HoverConfirmationOptions(
                enabled=True,
                snapshot_url="http://snapshot",
                timeout_ms=1,
                poll_ms=1,
                tolerance_px=3,
                require_clicked_menu_match=True,
            ),
            snapshot_fetch_func=snapshot_fetch,
            monotonic_func=IncrementingClock(start=1.0, step=0.01),
            wall_time_millis_func=lambda: 1000,
        )

        self.assertEqual(result.status, "FAIL")
        self.assertFalse(result.executed)
        self.assertEqual(backend.calls, [("move", 1100, 2100)])
        self.assertIn("pre-click hover confirmation failed", result.warnings[-1])
        self.assertTrue(any(command["type"] == "pre_click_hover_confirm" for command in result.commands))

    def test_navigation_hover_confirm_fails_known_clicked_menu_mismatch_by_default(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3203, "worldY": 3238, "plane": 0},
            suggested_click_point={"x": 260, "y": 223},
            click_point_space="canvas",
            target_explanation={"name": "Service waypoint", "classId": "service_route_anchor"},
        )
        snapshots = [
            {
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 260,
                    "mouseCanvasY": 223,
                    "topOption": "Walk here",
                    "topTarget": "",
                    "topType": "WALK",
                    "topIdentifier": 0,
                },
                "lastMenuOptionClicked": {"clientTick": 1, "wallTimeMillis": 1000, "option": "Play", "target": "", "type": "CC_OP"},
            },
            {
                "hoverMenu": {
                    "wallTimeMillis": 2010,
                    "mouseCanvasX": 260,
                    "mouseCanvasY": 223,
                    "topOption": "Walk here",
                    "topTarget": "",
                    "topType": "WALK",
                    "topIdentifier": 0,
                },
                "lastMenuOptionClicked": {"clientTick": 1, "wallTimeMillis": 1000, "option": "Play", "target": "", "type": "CC_OP"},
            },
            {
                "lastMenuOptionClicked": {
                    "clientTick": 2,
                    "wallTimeMillis": 2020,
                    "option": "Attack",
                    "target": "<col=ffff00>Giant spider",
                    "type": "NPC_SECOND_OPTION",
                    "identifier": 2660,
                }
            },
        ]

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=False,
            hover_options=HoverConfirmationOptions(enabled=True, snapshot_url="http://snapshot", timeout_ms=1, poll_ms=1, tolerance_px=3),
            snapshot_fetch_func=lambda *_args, **_kwargs: snapshots.pop(0),
            monotonic_func=IncrementingClock(start=1.0, step=0.01),
            wall_time_millis_func=iter([1500, 1510, 1520, 1530, 1540]).__next__,
        )

        self.assertEqual(result.status, "FAIL")
        self.assertTrue(result.executed)
        self.assertEqual(result.hover_confirmation["clickClassification"], "clicked_npc_action")
        self.assertIn("clicked menu did not match expected action", result.warnings[-1])
        self.assertEqual(result.observed_result["actionResultClassification"], "menu_flip_mismatch")
        self.assertEqual(result.action_trace["finalClassification"], "menu_flip_mismatch")
        mismatch = result.action_trace["clientTick"]["menuMismatch"]
        self.assertEqual(mismatch["mismatchReason"], "clicked_menu_did_not_match_navigation_waypoint_action")
        self.assertEqual(mismatch["actualClickedMenu"]["option"], "Attack")
        self.assertIn("hover_flip", mismatch["possibleCauses"])

    def test_loop_suppresses_menu_flip_without_resource_wait(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=False,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=1,
            action_timeout_ms=1,
            poll_interval_ms=10,
            max_actions=2,
            max_total_actions=0,
            max_runtime_seconds=1,
            final_reconcile_ms=0,
            final_reconcile_game_ticks=0,
            resource_reconcile_ms=0,
            resource_reconcile_game_ticks=0,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=False,
            max_successful_actions=1,
            max_timeouts=None,
            max_consecutive_timeouts=4,
            seed=None,
            require_live_readiness=False,
            target_hover_failure_limit=2,
            target_suppression_ms=2500,
            max_candidate_reacquire_rounds=3,
            clear_suppression_on_progress=True,
            pacing_profile="instant_debug",
        )
        menu_flip_result = ExecutionResult(
            status="FAIL",
            proposed_action="select_resource_target",
            dry_run=False,
            executed=True,
            proposal={
                "proposedAction": "select_resource_target",
                "targetKind": "resource",
                "targetName": "Tree",
                "targetExplanation": {
                    "name": "Tree",
                    "classId": "tree",
                    "id": 1276,
                    "worldLocation": {"worldX": 3212, "worldY": 3232, "plane": 0},
                },
            },
            commands=[{"type": "click_at", "x": 1200, "y": 2200}],
            observed_result={"actionResultClassification": "menu_flip_mismatch"},
            hover_confirmation={"confirmed": True, "clickClassification": "clicked_npc_action"},
            action_trace={
                "finalClassification": "menu_flip_mismatch",
                "gameTickVerificationTimeline": [],
                "clientTick": {
                    "menuMismatch": {
                        "mismatchReason": "clicked_menu_did_not_match_resource_object_action",
                        "actualClickedMenu": {"option": "Attack", "target": "Man"},
                    }
                },
            },
        )

        with patch("input_control.executor.execute_action", return_value=menu_flip_result):
            result = execute_action_loop(
                "http://daemon",
                options,
                fetch_json_func=lambda *_args, **_kwargs: status_payload_for_loop(free_slots=12, held_count=0, progress_count=0),
                backend=backend,
                sleep_func=lambda _seconds: None,
                monotonic_func=IncrementingClock(step=0.1),
            )

        payload = result.to_dict()
        observed = payload["actionResults"][0]["observedResult"]
        self.assertEqual(payload["status"], "WARN")
        self.assertEqual(observed["observedResult"], "menu_flip_mismatch")
        self.assertEqual(observed["resultOutcome"], "menu_mismatch")
        self.assertTrue(observed["nextActionAllowed"])
        self.assertNotEqual(observed.get("observedResult"), "resource_click_confirmed_waiting")
        self.assertNotIn("resourceTimeoutExtendedWait", observed)
        self.assertEqual(payload["actionResults"][0]["lifecycleState"]["reason"], "menu_flip_mismatch")
        self.assertEqual(payload["loopSummary"]["menuFlipMismatchCount"], 1)
        self.assertEqual(payload["loopSummary"]["targetMenuFlipSuppressions"], 1)
        self.assertEqual(payload["loopSummary"]["suppressedTargets"][0]["reason"], "menu_flip_mismatch")

    def test_loop_retries_wait_for_context_when_service_route_context_is_present(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=False,
            after_action_wait_ms=0,
            hover_confirm_target=False,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=1,
            action_timeout_ms=1,
            poll_interval_ms=10,
            max_actions=1,
            max_total_actions=0,
            max_runtime_seconds=1,
            final_reconcile_ms=0,
            final_reconcile_game_ticks=0,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            max_consecutive_timeouts=4,
            seed=None,
            require_live_readiness=False,
            max_navigation_reacquire_rounds=3,
            max_candidate_reacquire_rounds=0,
            suppressed_target_wait_ms=0,
            nav_trace=False,
            nav_trace_console=False,
        )
        waiting_proposal = ActionProposal(
            status="WARN",
            proposed_action="wait_for_context",
            target_kind="service_route_object",
            reason="route_object_not_on_expected_segment",
            missing_capabilities=["service_route.route_to_bank"],
        )
        service_proposal = ActionProposal(
            proposed_action="open_service",
            target_kind="service",
            target_name="Bank booth",
            suggested_click_point={"x": 325, "y": 89},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1473, "y": 146},
            click_point_resolution={"status": "PASS", "screenClickPoint": {"x": 1473, "y": 146}},
            target_explanation={"name": "Bank booth", "objectId": 18491},
            reason="service_target_actionable",
            confidence=0.9,
        )
        service_result = ExecutionResult(
            status="PASS",
            proposed_action="open_service",
            dry_run=False,
            executed=True,
            proposal=service_proposal.to_dict(),
            commands=[{"type": "move_and_click", "clickPoint": {"x": 1473, "y": 146}}],
            observed_result={
                "observedResult": "bank_opened",
                "resultOutcome": "success",
                "resultComplete": True,
                "nextActionAllowed": True,
                "verificationStatus": "PASS",
            },
            action_trace={},
        )
        proposals = [waiting_proposal, service_proposal]

        def fetch_status(*_args, **_kwargs):
            return navigation_status_payload(tick=10, x=3205, y=3212, service_distance=0, path_distance=0)

        with (
            patch("input_control.executor.build_action_proposal", side_effect=lambda _status: proposals.pop(0)),
            patch("input_control.executor.fetch_action_context", side_effect=TimeoutError("context warming")),
            patch("input_control.executor.execute_action", return_value=service_result),
        ):
            result = execute_action_loop(
                "http://daemon",
                options,
                fetch_json_func=fetch_status,
                backend=backend,
                snapshot_fetch_func=lambda *_args, **_kwargs: {},
                sleep_func=lambda _seconds: None,
                monotonic_func=IncrementingClock(start=0.0, step=0.05),
            )

        payload = result.to_dict()
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["reason"], "max_actions_reached")
        self.assertEqual(payload["actionResultCount"], 1)
        self.assertEqual(payload["actionResults"][0]["proposedAction"], "open_service")
        summary = payload["loopSummary"]
        self.assertEqual(summary["contextWaitReacquireAttempts"], 1)
        self.assertEqual(summary["contextWaitReacquireLimit"], 3)
        self.assertEqual(summary["reacquireResult"], "waiting_for_context")
        self.assertEqual(summary["targetReacquireWaits"], 1)

    def test_navigation_volatile_menu_tail_blocks_click_before_mousedown(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3203, "worldY": 3238, "plane": 0},
            suggested_click_point={"x": 260, "y": 223},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1260, "y": 2223},
            click_point_resolution={"status": "PASS", "screenClickPoint": {"x": 1260, "y": 2223}},
            target_explanation={"name": "Service waypoint", "classId": "service_route_anchor"},
        )
        tail_requests = []

        def snapshot_fetch(_url, **kwargs):
            tail_requests.append(kwargs.get("client_tick_tail", 0))
            return {
                "hoverMenu": {
                    "clientTick": 12,
                    "wallTimeMillis": 2020,
                    "mouseCanvasX": 260,
                    "mouseCanvasY": 223,
                    "topOption": "Walk here",
                    "topTarget": "",
                    "topType": "WALK",
                    "topIdentifier": 0,
                },
                "payloads": {
                    "client_tick_tail": {
                        "postMenuSortTail": [
                            {
                                "clientTick": 11,
                                "wallTimeMillis": 2010,
                                "mouseCanvasX": 260,
                                "mouseCanvasY": 223,
                                "topOption": "Attack",
                                "topTarget": "Moving NPC",
                                "topType": "NPC_FIRST_OPTION",
                                "topIdentifier": 2660,
                            },
                            {
                                "clientTick": 12,
                                "wallTimeMillis": 2020,
                                "mouseCanvasX": 260,
                                "mouseCanvasY": 223,
                                "topOption": "Walk here",
                                "topTarget": "",
                                "topType": "WALK",
                                "topIdentifier": 0,
                            },
                        ]
                    }
                },
                "lastMenuOptionClicked": {"clientTick": 1, "wallTimeMillis": 1000, "option": "Play", "target": "", "type": "CC_OP"},
            }

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=False,
            hover_options=HoverConfirmationOptions(enabled=True, snapshot_url="http://snapshot", timeout_ms=1, poll_ms=1, tolerance_px=3),
            snapshot_fetch_func=snapshot_fetch,
            monotonic_func=IncrementingClock(start=1.0, step=0.01),
            wall_time_millis_func=iter([1500, 1510, 1520, 1530, 1540]).__next__,
        )

        self.assertEqual(result.status, "FAIL")
        self.assertFalse(result.executed)
        self.assertEqual(backend.calls, [("move", 1260, 2223)])
        self.assertTrue(any(value and value > 0 for value in tail_requests))
        self.assertIn("volatile navigation hover zone", result.warnings[-1])
        self.assertTrue(result.action_trace["clientTick"]["volatileHoverZone"])
        self.assertIn("recent_npc_action", result.action_trace["clientTick"]["volatileReasons"])
        self.assertEqual(result.action_trace["finalClassification"], "hover_mismatch_skipped")

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
        self.assertEqual(result.action_trace["humanInput"]["profile"], "instant_debug")
        self.assertEqual(result.action_trace["humanInput"]["directBackendBypassCount"], 0)

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

    def test_execute_next_action_uses_action_readiness_execution_allowed(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=False,
            wait_for_ready=0,
            poll_interval_ms=1,
            focus_runelite=False,
            window_title_filter="RuneLite",
            require_live_readiness=True,
        )
        readiness = {
            "schema": "live_readiness.v2",
            "status": "WARN",
            "readinessPassed": False,
            "currentIntent": "resource_object_action",
            "actionReadiness": {
                "status": "PASS",
                "executionAllowed": True,
                "intent": "resource_object_action",
                "blockers": [],
                "warnings": [],
            },
            "contextReadiness": {
                "status": "WARN",
                "warnings": ["context-only warning"],
            },
            "warnings": ["context-only warning"],
            "blockers": [],
            "missingCapabilities": [],
        }

        with patch("input_control.executor.build_readiness_report", return_value=readiness):
            result = execute_next_action(
                "http://daemon",
                options,
                fetch_json_func=lambda *_args, **_kwargs: status_payload_for_loop(),
                backend=backend,
            )

        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.executed)
        self.assertEqual([call[0] for call in backend.calls], ["move", "mouse_down", "mouse_up"])
        self.assertEqual(result.readiness["actionReadiness"]["status"], "PASS")

    def test_execute_next_action_refuses_when_action_readiness_fails_even_if_legacy_passed(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=False,
            wait_for_ready=0,
            poll_interval_ms=1,
            focus_runelite=False,
            window_title_filter="RuneLite",
            require_live_readiness=True,
        )
        readiness = {
            "schema": "live_readiness.v2",
            "status": "WARN",
            "readinessPassed": True,
            "currentIntent": "resource_object_action",
            "actionReadiness": {
                "status": "FAIL",
                "executionAllowed": False,
                "intent": "resource_object_action",
                "blockers": [{"code": "selected_target_not_in_highlighter_source", "message": "missing"}],
                "warnings": [],
            },
            "contextReadiness": {"status": "PASS", "warnings": []},
            "warnings": [],
            "blockers": [],
            "missingCapabilities": ["target.highlighterMatch"],
        }

        with patch("input_control.executor.build_readiness_report", return_value=readiness):
            result = execute_next_action(
                "http://daemon",
                options,
                fetch_json_func=lambda *_args, **_kwargs: status_payload_for_loop(),
                backend=backend,
            )

        self.assertEqual(result.status, "FAIL")
        self.assertFalse(result.executed)
        self.assertEqual(backend.calls, [])
        self.assertEqual(result.readiness["actionReadiness"]["status"], "FAIL")

    def test_execute_next_action_auto_recovers_stale_liveness_once(self):
        backend = FakeBackend()
        stale_status = status_payload_for_loop(tick=1)
        stale_status["inputSourceActive"] = "plugin-snapshot"
        stale_status["clientTickHot"] = {
            "schema": "client_tick_hot.v1",
            "gameState": "LOGGED_IN",
            "latency": {"ageMillis": 5000, "postMenuSortAgeMillis": 5000},
        }
        stale_status["worldModelSummary"] = {
            "schema": "world_model_summary.v1",
            "metadata": {"gameState": "LOGGED_IN"},
            "objects": {"total": 0},
        }
        fresh_status = status_payload_for_loop(tick=2)
        fresh_status["inputSourceActive"] = "plugin-snapshot"
        fresh_status["clientTickHot"] = {
            "schema": "client_tick_hot.v1",
            "gameState": "LOGGED_IN",
            "latency": {"ageMillis": 25, "postMenuSortAgeMillis": 25},
        }
        fresh_status["worldModelSummary"] = {
            "schema": "world_model_summary.v1",
            "metadata": {"gameState": "LOGGED_IN"},
            "objects": {"total": 12},
        }
        statuses = [stale_status, fresh_status]
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=False,
            wait_for_ready=0,
            poll_interval_ms=1,
            focus_runelite=False,
            window_title_filter="RuneLite",
            require_live_readiness=False,
            auto_recover_loaded_scene=True,
            snapshot_url="http://snapshot",
            arduino_port="COM6",
            liveness_max_total_seconds=120,
            liveness_max_attempts_per_state=2,
            allow_jagex_launcher_automation=False,
        )
        recovery = {"schema": "liveness_recovery_result.v1", "status": "recovered_loaded_scene", "loadedSceneVerified": True}

        with patch("liveness_recovery_core.ensure_loaded_scene", return_value=recovery) as ensure:
            result = execute_next_action(
                "http://daemon",
                options,
                fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
                backend=backend,
            )

        ensure.assert_called_once()
        self.assertEqual(result.status, "PASS")
        self.assertEqual([call[0] for call in backend.calls], ["move", "mouse_down", "mouse_up"])
        self.assertEqual(result.readiness["livenessRecoveryLastResult"], recovery)

    def test_execute_next_action_auto_recovers_when_daemon_status_initially_down(self):
        backend = FakeBackend()
        fresh_status = status_payload_for_loop(tick=2)
        fresh_status["inputSourceActive"] = "plugin-snapshot"
        fresh_status["clientTickHot"] = {
            "schema": "client_tick_hot.v1",
            "gameState": "LOGGED_IN",
            "latency": {"ageMillis": 25, "postMenuSortAgeMillis": 25},
        }
        fresh_status["worldModelSummary"] = {
            "schema": "world_model_summary.v1",
            "metadata": {"gameState": "LOGGED_IN"},
            "objects": {"total": 12},
        }
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=False,
            wait_for_ready=0,
            poll_interval_ms=1,
            focus_runelite=False,
            window_title_filter="RuneLite",
            require_live_readiness=False,
            auto_recover_loaded_scene=True,
            snapshot_url="http://snapshot",
            arduino_port="COM6",
            liveness_max_total_seconds=120,
            liveness_max_attempts_per_state=2,
            allow_jagex_launcher_automation=False,
        )
        recovery = {"schema": "liveness_recovery_result.v1", "status": "recovered_loaded_scene", "loadedSceneVerified": True}
        calls = {"count": 0}

        def fetch_status(*_args, **_kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("daemon down")
            return fresh_status

        with patch("liveness_recovery_core.ensure_loaded_scene", return_value=recovery) as ensure:
            result = execute_next_action(
                "http://daemon",
                options,
                fetch_json_func=fetch_status,
                backend=backend,
            )

        ensure.assert_called_once()
        self.assertEqual(calls["count"], 2)
        self.assertEqual(result.status, "PASS")
        self.assertEqual([call[0] for call in backend.calls], ["move", "mouse_down", "mouse_up"])
        self.assertEqual(result.readiness["livenessRecoveryLastResult"], recovery)

    def test_execute_next_action_blocks_when_auto_recovery_fails(self):
        backend = FakeBackend()
        stale_status = status_payload_for_loop(tick=1)
        stale_status["inputSourceActive"] = "plugin-snapshot"
        stale_status["clientTickHot"] = {
            "schema": "client_tick_hot.v1",
            "gameState": "LOGIN_SCREEN",
            "latency": {"ageMillis": 5000, "postMenuSortAgeMillis": 5000},
        }
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=False,
            wait_for_ready=0,
            poll_interval_ms=1,
            focus_runelite=False,
            window_title_filter="RuneLite",
            require_live_readiness=False,
            auto_recover_loaded_scene=True,
            snapshot_url="http://snapshot",
            arduino_port="COM6",
            liveness_max_total_seconds=120,
            liveness_max_attempts_per_state=2,
            allow_jagex_launcher_automation=False,
        )
        recovery = {
            "schema": "liveness_recovery_result.v1",
            "status": "manual_login_required",
            "blocker": "manual_login_required",
        }

        with patch("liveness_recovery_core.ensure_loaded_scene", return_value=recovery):
            result = execute_next_action(
                "http://daemon",
                options,
                fetch_json_func=lambda *_args, **_kwargs: stale_status,
                backend=backend,
            )

        self.assertEqual(result.status, "FAIL")
        self.assertEqual(result.proposed_action, "none")
        self.assertEqual(backend.calls, [])
        self.assertEqual(result.readiness["livenessRecoveryLastResult"], recovery)

    def test_execute_next_action_dry_run_verify_after_action_does_not_wait_for_result(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=False,
            verify_after_action=True,
            wait_for_ready=0,
            poll_interval_ms=1,
            focus_runelite=False,
            window_title_filter="RuneLite",
            require_live_readiness=False,
        )
        calls = {"count": 0}

        def fetch_once(*_args, **_kwargs):
            calls["count"] += 1
            if calls["count"] > 1:
                raise AssertionError("dry-run verification should not poll for game progress")
            return status_payload_for_loop()

        result = execute_next_action(
            "http://daemon",
            options,
            fetch_json_func=fetch_once,
            backend=backend,
        )

        self.assertEqual(result.status, "PASS")
        self.assertFalse(result.executed)
        self.assertEqual(result.verification_status, "SKIPPED")
        self.assertEqual(result.observed_result["observedResult"], "dry_run_not_executed")
        self.assertEqual(calls["count"], 1)
        self.assertEqual(backend.calls, [])

    def test_cli_execute_focuses_runelite_by_default(self):
        args = execute_cli.parse_args(["--execute", "--backend", "pyautogui"])

        execute_cli.apply_focus_default(args)

        self.assertTrue(args.focus_runelite)

    def test_cli_dry_run_does_not_focus_runelite_by_default(self):
        args = execute_cli.parse_args(["--dry-run", "--backend", "pyautogui"])
        args.execute = False

        execute_cli.apply_focus_default(args)

        self.assertFalse(args.focus_runelite)

    def test_cli_focus_default_can_be_disabled(self):
        args = execute_cli.parse_args(["--execute", "--backend", "pyautogui", "--no-focus-runelite"])

        execute_cli.apply_focus_default(args)

        self.assertFalse(args.focus_runelite)

    def test_cli_parses_input_profile(self):
        args = execute_cli.parse_args(["--execute", "--input-profile", "steady"])

        self.assertEqual(args.input_profile, "steady")

    def test_cli_parses_bounded_inventory_loop_options(self):
        args = execute_cli.parse_args(
            [
                "--loop",
                "--stop-after-inventory-changes",
                "3",
                "--stop-when-inventory-full",
                "--max-successful-actions",
                "4",
                "--max-timeouts",
                "2",
                "--summary-every-action",
            ]
        )

        self.assertEqual(args.stop_after_inventory_changes, 3)
        self.assertTrue(args.stop_when_inventory_full)
        self.assertEqual(args.max_successful_actions, 4)
        self.assertEqual(args.max_timeouts, 2)
        self.assertTrue(args.summary_every_action)

    def test_cli_parses_lifecycle_soak_options(self):
        args = execute_cli.parse_args(
            [
                "--stop-after-lifecycle-cycles",
                "1",
                "--stop-after-service-cycles",
                "2",
                "--stop-after-post-service-logs",
                "3",
                "--max-total-actions",
                "150",
                "--max-wall-time-minutes",
                "12",
                "--max-consecutive-no-progress",
                "4",
                "--max-consecutive-timeouts",
                "5",
                "--resource-reconcile-ms",
                "4000",
                "--resource-reconcile-game-ticks",
                "8",
                "--post-click-progress-tail-ticks",
                "6",
            ]
        )

        self.assertEqual(args.stop_after_lifecycle_cycles, 1)
        self.assertEqual(args.stop_after_service_cycles, 2)
        self.assertEqual(args.stop_after_post_service_logs, 3)
        self.assertEqual(args.max_total_actions, 150)
        self.assertEqual(args.max_wall_time_minutes, 12)
        self.assertEqual(args.max_consecutive_no_progress, 4)
        self.assertEqual(args.max_consecutive_timeouts, 5)
        self.assertEqual(args.resource_reconcile_ms, 4000)
        self.assertEqual(args.resource_reconcile_game_ticks, 8)
        self.assertEqual(args.post_click_progress_tail_ticks, 6)

    def test_cli_parses_wait_for_ready(self):
        args = execute_cli.parse_args(["--execute", "--wait-for-ready", "30"])

        self.assertEqual(args.wait_for_ready, 30)

    def test_cli_parses_navigation_trace_options(self):
        args = execute_cli.parse_args(
            [
                "--nav-trace",
                "--nav-trace-output",
                "interaction_geometry/live/custom_navigation_trace.jsonl",
                "--nav-trace-console",
            ]
        )

        self.assertTrue(args.nav_trace)
        self.assertEqual(args.nav_trace_output, "interaction_geometry/live/custom_navigation_trace.jsonl")
        self.assertTrue(args.nav_trace_console)

    def test_cli_parses_reconcile_and_pacing_options(self):
        args = execute_cli.parse_args(
            [
                "--final-reconcile-ms",
                "1200",
                "--final-reconcile-game-ticks",
                "4",
                "--pacing-profile",
                "steady",
                "--target-switch-min-ms",
                "400",
                "--target-switch-max-ms",
                "1400",
            ]
        )

        self.assertEqual(args.final_reconcile_ms, 1200)
        self.assertEqual(args.final_reconcile_game_ticks, 4)
        self.assertEqual(args.pacing_profile, "steady")
        self.assertEqual(args.target_switch_min_ms, 400)
        self.assertEqual(args.target_switch_max_ms, 1400)

    def test_cli_parses_visual_debug_bundle_options(self):
        args = execute_cli.parse_args(
            [
                "--capture-debug-screenshots",
                "--screenshot-on-failure",
                "--screenshot-on-camera-recovery",
                "--screenshot-on-timeout",
                "--screenshot-on-edge-reject",
                "--screenshot-on-lifecycle-transition",
                "--max-debug-screenshots",
                "3",
                "--debug-screenshot-dir",
                "C:\\tmp\\visual-bundles",
            ]
        )

        self.assertTrue(args.capture_debug_screenshots)
        self.assertTrue(args.screenshot_on_failure)
        self.assertTrue(args.screenshot_on_camera_recovery)
        self.assertTrue(args.screenshot_on_timeout)
        self.assertTrue(args.screenshot_on_edge_reject)
        self.assertTrue(args.screenshot_on_lifecycle_transition)
        self.assertEqual(args.max_debug_screenshots, 3)
        self.assertEqual(args.debug_screenshot_dir, "C:\\tmp\\visual-bundles")

    def test_cli_persists_latest_action_trace_under_active_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            payload = {
                "schema": "input_control_execution_result.v1",
                "status": "FAIL",
                "proposedAction": "interact_service_route_object",
                "actionId": "1:interact_service_route_object:Staircase",
                "executed": True,
                "readiness": {"session": {"activeSessionPath": str(session)}},
                "observedResult": {"observedResult": "no_change_timeout"},
                "hoverConfirmation": {
                    "rightClickMenuSelection": {
                        "reason": "clicked_direct_menu_mismatch",
                        "selectedEntry": {"option": "Climb-down"},
                    }
                },
                "actionTrace": {
                    "actionTraceSchema": "action_trace.v2",
                    "proposedAction": "interact_service_route_object",
                    "finalClassification": "clicked_direct_menu_mismatch",
                    "rightClickMenuSelection": {"reason": "clicked_direct_menu_mismatch"},
                    "inputIntegrityPhaseReport": {
                        "live_action_phase": {
                            "injectedEventsDelta": 0,
                            "lowerIlInjectedEventsDelta": 0,
                            "directBackendBypassCountDelta": 0,
                        }
                    },
                },
            }

            persisted = execute_cli.persist_latest_action_trace(payload)
            path = Path(persisted["path"])
            record = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(persisted["status"], "PASS")
        self.assertEqual(path.name, "last_action_trace.json")
        self.assertEqual(record["schema"], "latest_action_trace_record.v1")
        self.assertEqual(record["actionTrace"]["finalClassification"], "clicked_direct_menu_mismatch")
        self.assertEqual(record["inputIntegrityPhaseReport"]["live_action_phase"]["directBackendBypassCountDelta"], 0)

    def test_cli_persists_latest_action_trace_from_daemon_session_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            payload = {
                "schema": "input_control_execution_result.v1",
                "status": "PASS",
                "proposedAction": "interact_service_route_object",
                "actionTrace": {
                    "actionTraceSchema": "action_trace.v2",
                    "proposedAction": "interact_service_route_object",
                    "finalClassification": "route_transition_progress",
                },
            }

            with patch.object(execute_cli, "_session_path_from_daemon_url", return_value=session):
                persisted = execute_cli.persist_latest_action_trace(payload, daemon_url="http://127.0.0.1:8890")
            path = Path(persisted["path"])
            record = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(persisted["status"], "PASS")
        self.assertEqual(record["source"], "execute_next_action")
        self.assertEqual(record["actionTrace"]["finalClassification"], "route_transition_progress")

    def test_cli_parses_target_suppression_options(self):
        args = execute_cli.parse_args(
            [
                "--target-hover-failure-limit",
                "2",
                "--target-suppression-ms",
                "2500",
                "--clear-suppression-on-progress",
                "--max-candidate-reacquire-rounds",
                "3",
                "--no-safe-target-wait-ms",
                "150",
                "--suppressed-target-wait-ms",
                "75",
            ]
        )

        self.assertEqual(args.target_hover_failure_limit, 2)
        self.assertEqual(args.target_suppression_ms, 2500)
        self.assertTrue(args.clear_suppression_on_progress)
        self.assertEqual(args.max_candidate_reacquire_rounds, 3)
        self.assertEqual(args.no_safe_target_wait_ms, 150)
        self.assertEqual(args.suppressed_target_wait_ms, 75)

    def test_cli_parses_navigation_verification_and_reacquire_options(self):
        args = execute_cli.parse_args(
            [
                "--nav-verify-game-ticks",
                "4",
                "--nav-verify-ms",
                "2200",
                "--nav-progress-min-distance",
                "1",
                "--max-waypoint-alternates",
                "5",
                "--max-hover-checks-per-waypoint",
                "1",
                "--max-navigation-reacquire-rounds",
                "3",
                "--max-camera-adjustments-per-route-step",
                "2",
                "--camera-adjust-ms",
                "120",
                "--camera-adjust-direction",
                "right",
                "--camera-reacquire-ms",
                "150",
                "--camera-reacquire-waypoint",
                "--camera-method",
                "keyboard_arrows",
                "--camera-exposure-max-ms",
                "2100",
                "--camera-sample-interval-ms",
                "20",
                "--camera-max-direction-switches",
                "2",
                "--camera-allow-diagonal",
                "--camera-reacquire-timeout-ms",
                "2000",
                "--camera-probe-ms",
                "120",
                "--camera-max-nudges",
                "4",
                "--camera-follow-target",
                "--camera-min-score-improvement",
                "2",
                "--camera-min-projection-delta-px",
                "4",
                "--camera-allow-pitch-adjust",
                "--camera-debug-summary",
                "--reject-edge-route-clicks",
                "--camera-reacquire-on-edge-projection",
                "--route-click-edge-margin-px",
                "12",
                "--route-min-visible-area-ratio",
                "0.7",
                "--allow-minimap-navigation",
                "--route-waypoint-lookahead-tiles",
                "12",
                "--route-waypoint-max-horizon-tiles",
                "25",
                "--min-route-progress-tiles",
                "3",
                "--max-route-waypoint-distance",
                "30",
                "--prefer-long-visible-waypoint",
                "--route-waypoint-distance-mode",
                "adaptive",
                "--nav-replan-while-moving",
                "false",
                "--nav-min-game-ticks-between-clicks",
                "3",
                "--nav-stuck-game-ticks",
                "6",
                "--nav-destination-arrival-distance",
                "1",
                "--camera-self-test",
                "--camera-test-return",
            ]
        )

        self.assertEqual(args.nav_verify_game_ticks, 4)
        self.assertEqual(args.nav_verify_ms, 2200)
        self.assertEqual(args.nav_progress_min_distance, 1)
        self.assertEqual(args.max_waypoint_alternates, 5)
        self.assertEqual(args.max_hover_checks_per_waypoint, 1)
        self.assertEqual(args.max_navigation_reacquire_rounds, 3)
        self.assertEqual(args.max_camera_adjustments_per_route_step, 2)
        self.assertEqual(args.camera_adjust_ms, 120)
        self.assertEqual(args.camera_adjust_direction, "right")
        self.assertEqual(args.camera_reacquire_ms, 150)
        self.assertTrue(args.camera_reacquire_waypoint)
        self.assertEqual(args.camera_method, "keyboard_arrows")
        self.assertEqual(args.camera_exposure_max_ms, 2100)
        self.assertEqual(args.camera_sample_interval_ms, 20)
        self.assertEqual(args.camera_max_direction_switches, 2)
        self.assertTrue(args.camera_allow_diagonal)
        self.assertEqual(args.camera_reacquire_timeout_ms, 2000)
        self.assertEqual(args.camera_probe_ms, 120)
        self.assertEqual(args.camera_max_nudges, 4)
        self.assertTrue(args.camera_follow_target)
        self.assertEqual(args.camera_min_score_improvement, 2)
        self.assertEqual(args.camera_min_projection_delta_px, 4)
        self.assertTrue(args.camera_allow_pitch_adjust)
        self.assertTrue(args.camera_debug_summary)
        self.assertTrue(args.reject_edge_route_clicks)
        self.assertTrue(args.camera_reacquire_on_edge_projection)
        self.assertEqual(args.route_click_edge_margin_px, 12)
        self.assertEqual(args.route_min_visible_area_ratio, 0.7)
        self.assertTrue(args.allow_minimap_navigation)
        self.assertEqual(args.route_waypoint_lookahead_tiles, 12)
        self.assertEqual(args.route_waypoint_max_horizon_tiles, 25)
        self.assertEqual(args.min_route_progress_tiles, 3)
        self.assertEqual(args.max_route_waypoint_distance, 30)
        self.assertTrue(args.prefer_long_visible_waypoint)
        self.assertEqual(args.route_waypoint_distance_mode, "adaptive")
        self.assertFalse(args.nav_replan_while_moving)
        self.assertEqual(args.nav_min_game_ticks_between_clicks, 3)
        self.assertEqual(args.nav_stuck_game_ticks, 6)
        self.assertEqual(args.nav_destination_arrival_distance, 1)
        self.assertTrue(args.camera_self_test)
        self.assertTrue(args.camera_test_return)

    def test_edge_route_click_defaults_are_safe_when_enabled(self):
        args = execute_cli.parse_args(["--reject-edge-route-clicks"])

        self.assertTrue(args.reject_edge_route_clicks)
        self.assertEqual(args.route_click_edge_margin_px, 12)
        self.assertEqual(args.route_min_visible_area_ratio, 0.45)

    def test_loop_human_output_includes_inventory_summary(self):
        text = execute_cli.format_human(
            {
                "schema": execute_cli.LOOP_SCHEMA,
                "status": "PASS",
                "dryRun": False,
                "executedActionCount": 2,
                "actionResultCount": 2,
                "maxActions": 5,
                "reason": "inventory_change_limit_reached",
                "loopSummary": {
                    "actionsAttempted": 2,
                    "actionsExecuted": 2,
                    "successfulActions": 2,
                    "timeouts": 0,
                    "delayedProgressReconciliations": 1,
                    "resourceTimeoutReconciledSuccesses": 1,
                    "goalReachedWithRecoverableFailures": True,
                    "recoverableFailuresAfterGoal": 1,
                    "inventoryChanges": 2,
                    "lifecycleCyclesStarted": 1,
                    "lifecycleCyclesCompleted": 1,
                    "serviceCompleteEvents": 1,
                    "returnRoutesCompleted": 1,
                    "postServiceLogsCollected": 2,
                    "inventoryFreeSlotsStart": 12,
                    "inventoryFreeSlotsEnd": 10,
                    "resourceCountStart": 0,
                    "resourceCountEnd": 2,
                    "progressStart": 0,
                    "progressEnd": 2,
                    "finalCycleStage": "collecting_resources",
                    "finalPhase": "target_selected",
                    "finalActiveIntent": "select_target",
                    "lastObservedSignals": ["inventory_changed"],
                },
                "actionResults": [],
                "warnings": [],
            }
        )

        self.assertIn("Inventory free slots: 12 -> 10", text)
        self.assertIn("Inventory changes: 2", text)
        self.assertIn("Delayed reconciliations: 1 resource timeout recoveries=1", text)
        self.assertIn("Recoverable goal retries: 1", text)
        self.assertIn("Lifecycle cycles: started=1 completed=1 serviceComplete=1 returnComplete=1 post-service logs=2", text)
        self.assertIn("Last observed signals: inventory_changed", text)

    def test_lifecycle_summary_counts_full_cycle_after_post_service_collection(self):
        summary = _new_loop_summary()
        for index, status in enumerate(
            [
                lifecycle_status_for_summary(
                    stage="collecting_resources",
                    phase="target_selected",
                    active_intent="select_target",
                    free_slots=2,
                    held_count=26,
                    progress_count=26,
                    tick=1,
                    resource_target_available=True,
                ),
                lifecycle_status_for_summary(
                    stage="inventory_full",
                    phase="inventory_full",
                    active_intent="needs_service",
                    free_slots=0,
                    held_count=28,
                    progress_count=28,
                    tick=2,
                    inventory_full=True,
                ),
                lifecycle_status_for_summary(
                    stage="needs_service",
                    phase="needs_service",
                    active_intent="navigate_to_service",
                    free_slots=0,
                    held_count=28,
                    progress_count=28,
                    tick=3,
                    inventory_full=True,
                ),
                lifecycle_status_for_summary(
                    stage="service_open",
                    phase="service_open",
                    active_intent="bank_operation_pending",
                    free_slots=0,
                    held_count=28,
                    progress_count=28,
                    tick=4,
                    bank_open=True,
                    resource_items_held=28,
                ),
                lifecycle_status_for_summary(
                    stage="return_to_resource",
                    phase="return_to_resource",
                    active_intent="return_to_resource_area",
                    free_slots=28,
                    held_count=0,
                    progress_count=28,
                    tick=5,
                    banking_complete=True,
                    resource_items_held=0,
                ),
                lifecycle_status_for_summary(
                    stage="collecting_resources",
                    phase="target_selected",
                    active_intent="select_target",
                    free_slots=28,
                    held_count=0,
                    progress_count=28,
                    tick=6,
                    banking_complete=True,
                    resource_target_available=True,
                ),
                lifecycle_status_for_summary(
                    stage="collecting_resources",
                    phase="wait_for_result",
                    active_intent="wait_for_result",
                    free_slots=27,
                    held_count=1,
                    progress_count=29,
                    tick=7,
                    banking_complete=True,
                    resource_target_available=True,
                ),
            ],
            start=1,
        ):
            _record_loop_status(summary, status)
            self.assertEqual(summary["lastLifecycleSampleTick"], index)

        self.assertEqual(summary["lifecycleCyclesStarted"], 1)
        self.assertEqual(summary["inventoryFullEvents"], 1)
        self.assertEqual(summary["bankOpenEvents"], 1)
        self.assertEqual(summary["depositSuccesses"], 1)
        self.assertEqual(summary["serviceCompleteEvents"], 1)
        self.assertEqual(summary["returnRoutesStarted"], 1)
        self.assertEqual(summary["returnRoutesCompleted"], 1)
        self.assertEqual(summary["resourceReacquisitions"], 1)
        self.assertEqual(summary["postServiceResourceCollections"], 1)
        self.assertEqual(summary["postServiceLogsCollected"], 1)
        self.assertEqual(summary["lifecycleCyclesCompleted"], 1)
        self.assertEqual(_loop_stop_reason(Namespace(stop_after_lifecycle_cycles=1), summary), "lifecycle_cycle_limit_reached")

    def test_loop_summary_prefers_authoritative_player_location_and_labels_proxy_fallback(self):
        summary = _new_loop_summary()

        _record_loop_status(
            summary,
            {
                "playerLocation": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "playerLocationSource": "plugin_snapshot_baseline_player",
                "playerLocationConfidence": 1.0,
                "collisionWindowCenterWorld": {"worldX": 3200, "worldY": 3239, "plane": 0},
            },
        )

        self.assertEqual(summary["finalLocation"], {"worldX": 3196, "worldY": 3248, "plane": 0})
        self.assertEqual(summary["finalLocationSource"], "plugin_snapshot_baseline_player")
        self.assertEqual(summary["finalLocationConfidence"], 1.0)

        proxy_summary = _new_loop_summary()
        _record_loop_status(
            proxy_summary,
            {"collisionWindowCenterWorld": {"worldX": 3200, "worldY": 3239, "plane": 0}},
        )

        self.assertEqual(proxy_summary["finalLocation"], {"worldX": 3200, "worldY": 3239, "plane": 0})
        self.assertEqual(proxy_summary["finalLocationSource"], "collision_window_center_proxy")
        self.assertEqual(proxy_summary["finalLocationConfidence"], 0.35)

    def test_loop_stop_reason_honors_soak_limits(self):
        summary = _new_loop_summary()
        summary.update(
            {
                "serviceCompleteEvents": 2,
                "postServiceLogsCollected": 3,
                "consecutiveNoProgress": 4,
                "consecutiveTimeouts": 5,
            }
        )

        self.assertEqual(_loop_stop_reason(Namespace(stop_after_service_cycles=2), summary), "service_cycle_limit_reached")
        self.assertEqual(_loop_stop_reason(Namespace(stop_after_post_service_logs=3), summary), "post_service_log_limit_reached")
        self.assertEqual(_loop_stop_reason(Namespace(max_consecutive_no_progress=4), summary), "max_consecutive_no_progress_reached")
        self.assertEqual(_loop_stop_reason(Namespace(max_consecutive_timeouts=5), summary), "max_consecutive_timeouts_reached")

    def test_goal_reached_accepts_recoverable_resource_failures_as_warn(self):
        summary = _new_loop_summary()
        summary.update({"postServiceLogsCollected": 2, "inventoryChanges": 2, "resourceProgressSuccesses": 2})
        results = [
            ExecutionResult(
                status="FAIL",
                proposed_action="select_resource_target",
                dry_run=False,
                executed=True,
                observed_result={
                    "observedResult": "no_change_timeout",
                    "resultOutcome": "no_change_timeout",
                    "resourceProgressClassification": "resource_timeout_no_progress",
                },
                lifecycle_state={"reason": "resource_no_progress_target_reacquired"},
            ),
            ExecutionResult(
                status="PASS",
                proposed_action="select_resource_target",
                dry_run=False,
                executed=True,
                observed_result={
                    "observedResult": "inventory_changed",
                    "resultOutcome": "success",
                    "observedSignals": ["inventory_changed", "resource_progress_increased"],
                },
            ),
        ]

        self.assertEqual(
            _goal_reached_with_only_recoverable_failures("post_service_log_limit_reached", results, summary),
            1,
        )

    def test_repeated_resource_no_progress_suppresses_target_for_reacquisition(self):
        target = {
            "name": "Oak tree",
            "classId": "tree",
            "id": 10820,
            "worldLocation": {"worldX": 3199, "worldY": 3227, "plane": 0},
        }
        options = Namespace(target_hover_failure_limit=2, target_suppression_ms=2500)
        summary = _new_loop_summary()
        cache = {}
        result = ExecutionResult(
            status="FAIL",
            proposed_action="select_resource_target",
            dry_run=False,
            executed=True,
            proposal={"targetName": "Oak tree", "targetExplanation": target},
            observed_result={
                "observedResult": "no_change_timeout",
                "resultOutcome": "no_change_timeout",
                "resourceProgressClassification": "resource_timeout_no_progress",
            },
            lifecycle_state={"reason": "resource_no_progress_target_reacquired"},
            action_trace={},
        )

        first = _record_target_no_progress_failure(
            options=options,
            cache=cache,
            summary=summary,
            result=result,
            now_ms=1000,
        )
        second = _record_target_no_progress_failure(
            options=options,
            cache=cache,
            summary=summary,
            result=result,
            now_ms=1100,
        )

        self.assertFalse(first["suppressed"])
        self.assertTrue(second["suppressed"])
        self.assertEqual(summary["targetsSuppressed"], 1)
        self.assertEqual(summary["targetNoProgressSuppressions"], 1)
        self.assertEqual(summary["suppressedTargets"][0]["reason"], "resource_no_progress")
        self.assertEqual(result.action_trace["targetNoProgressSuppression"]["failureCount"], 2)

    def test_reacquire_budget_type_identifies_return_transition_objects(self):
        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            suggested_click_point={"x": 181, "y": 18},
            click_point_space="canvas",
            target_explanation={
                "name": "Staircase",
                "classId": "route_transition",
                "worldLocation": {"worldX": 3205, "worldY": 3208, "plane": 2},
                "safeAimPoint": {"status": "PASS", "actionable": True},
                "routeRelevance": {"relevanceStatus": "PASS"},
                "expectedPlaneChange": -1,
            },
        )

        self.assertEqual(_proposal_reacquire_budget_type(proposal), "route_transition")

    def test_phase_scope_change_clears_stale_reacquire_suppression(self):
        cache = {
            "56231:3205:3208:2:route_transition": {
                "targetKey": "56231:3205:3208:2:route_transition",
                "targetName": "Staircase",
                "suppressionUntil": 999999,
            }
        }
        summary = _new_loop_summary()
        previous_scope = (
            "needs_service",
            "route_transition_action",
            2,
            "lumbridge_castle_bank",
            None,
        )
        current_scope = (
            "return_to_resource",
            "route_transition_action",
            2,
            None,
            "lumbridge_first_floor_stairs",
        )

        next_scope = _maybe_reset_reacquire_budget_on_scope_change(
            cache,
            summary,
            previous_scope=previous_scope,
            current_scope=current_scope,
        )

        self.assertEqual(next_scope, current_scope)
        self.assertEqual(cache, {})
        self.assertTrue(summary["phaseScopedBudget"])
        self.assertEqual(summary["budgetResetReason"], "reacquire_scope_changed")
        self.assertEqual(summary["reacquireBudgetResets"], 1)

    def test_route_transition_timeout_reconciles_when_later_plane_evidence_arrives(self):
        result = ExecutionResult(
            status="FAIL",
            proposed_action="interact_service_route_object",
            dry_run=False,
            executed=True,
            observed_result={
                "observedResult": "no_change_timeout",
                "resultOutcome": "no_change_timeout",
                "resultComplete": True,
            },
            lifecycle_state={"currentState": "timed_out", "reason": "action_timeout"},
            action_trace={},
        )
        observed = {
            "observedResult": "return_transition_plane_changed",
            "resultOutcome": "progress",
            "resultComplete": True,
            "verificationStatus": "PASS",
            "observedSignals": ["player_plane_changed"],
        }

        _apply_reconciled_observation(result, observed, elapsed_ms=1400)

        self.assertTrue(result.observed_result["delayedProgressReconciliation"])
        self.assertEqual(result.observed_result["previousResultOutcome"], "no_change_timeout")
        self.assertEqual(
            result.observed_result["routeTransitionProgressClassification"],
            "return_transition_reconciled_success",
        )
        self.assertEqual(result.action_trace["finalClassification"], "return_transition_reconciled_success")

    def test_route_transition_timeout_without_evidence_becomes_retry_required(self):
        result = ExecutionResult(
            status="PASS",
            proposed_action="interact_service_route_object",
            dry_run=False,
            action_id="action-1",
            executed=True,
            proposal={
                "proposedAction": "interact_service_route_object",
                "targetName": "Staircase",
                "targetExplanation": {
                    "name": "Staircase",
                    "objectId": 16672,
                    "worldLocation": {"worldX": 3204, "worldY": 3207, "plane": 1},
                    "expectedPlaneChange": "-1",
                },
            },
            hover_confirmation={
                "confirmed": True,
                "clickClassification": "clicked_expected_action",
                "lastMenuOptionClickedAfter": {
                    "option": "Climb-down",
                    "target": "<col=ffff>Staircase",
                    "identifier": 16672,
                },
            },
            action_trace={"actionId": "action-1"},
        )
        observed = {
            "observedResult": "no_change_timeout",
            "resultOutcome": "no_change_timeout",
            "resultComplete": True,
            "nextActionAllowed": False,
            "verificationStatus": "FAIL",
            "observedSignals": [],
        }

        retry = _route_transition_retry_required_observation(result, observed)

        self.assertEqual(retry["observedResult"], "return_transition_retry_required")
        self.assertEqual(retry["resultOutcome"], "retry_required")
        self.assertEqual(retry["routeTransitionProgressClassification"], "return_transition_retry_required")
        self.assertTrue(retry["nextActionAllowed"])
        self.assertEqual(retry["previousObservedResult"], "no_change_timeout")
        self.assertEqual(retry["clickedMenuAfter"]["option"], "Climb-down")

    def test_route_transition_retry_summary_counts_retry_success_separately(self):
        first = ExecutionResult(
            status="WARN",
            proposed_action="interact_service_route_object",
            dry_run=False,
            action_id="action-1",
            executed=True,
            observed_result={
                "observedResult": "return_transition_retry_required",
                "resultOutcome": "retry_required",
                "resultComplete": True,
                "routeTransitionProgressClassification": "return_transition_retry_required",
                "routeTransitionLedgerEntry": {"actionId": "action-1"},
            },
            action_trace={
                "routeTransitionLedgerEntry": {"actionId": "action-1"},
                "humanInput": {"directBackendBypassCount": 0},
            },
            hover_confirmation={"confirmed": True, "clickClassification": "clicked_expected_action"},
        )
        second = ExecutionResult(
            status="PASS",
            proposed_action="interact_service_route_object",
            dry_run=False,
            action_id="action-2",
            executed=True,
            observed_result={
                "observedResult": "return_transition_plane_changed",
                "resultOutcome": "progress",
                "resultComplete": True,
                "routeTransitionProgressClassification": "return_transition_retry_success",
                "retryOfActionId": "action-1",
                "routeTransitionLedgerEntry": {"actionId": "action-2", "retryOfActionId": "action-1"},
            },
            action_trace={
                "routeTransitionLedgerEntry": {"actionId": "action-2", "retryOfActionId": "action-1"},
                "humanInput": {"directBackendBypassCount": 0},
            },
            hover_confirmation={"confirmed": True, "clickClassification": "clicked_expected_action"},
        )

        counts = _loop_counts([first, second])

        self.assertEqual(counts["routeTransitionAttempts"], 2)
        self.assertEqual(counts["routeTransitionRetryRequired"], 1)
        self.assertEqual(counts["routeTransitionRetrySuccesses"], 1)
        self.assertEqual(counts["routeTransitionTrueTimeouts"], 0)
        self.assertEqual(counts["resolvedByRetry"], 1)
        self.assertEqual(counts["trueUnresolvedTimeouts"], 0)

    def test_human_output_shows_execute_mode_even_when_blocked_before_click(self):
        text = execute_cli.format_human(
            {
                "schema": "input_control_execution_result.v1",
                "status": "FAIL",
                "dryRun": False,
                "executed": False,
                "backend": "pyautogui",
                "movementProfile": "linear_debug",
                "proposal": {"proposedAction": "select_resource_target", "targetName": "Tree"},
                "movementPlan": {},
                "clickPointResolution": {},
                "lifecycleState": {"currentState": "blocked"},
                "observedResult": {},
                "readiness": {"status": "FAIL", "readinessPassed": False},
                "commands": [],
                "warnings": ["pre-action readiness failed"],
            }
        )

        self.assertIn("Mode: execute", text)
        self.assertIn("Status: FAIL", text)

    def test_hover_menu_parser_accepts_chop_tree(self):
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Oak tree",
            suggested_click_point={"x": 200, "y": 146},
            click_point_space="canvas",
            target_explanation={"objectId": 10820, "name": "Oak tree"},
        )
        sample = {
            "wallTimeMillis": 2000,
            "mouseCanvasX": 201,
            "mouseCanvasY": 145,
            "topOption": "Chop down",
            "topTarget": "<col=ffff>Oak tree",
            "topIdentifier": 10820,
        }

        result = hover_menu_matches_target(
            sample,
            proposal,
            {"x": 200, "y": 146},
            tolerance_px=3,
            min_wall_time_millis=1000,
        )

        self.assertTrue(result.confirmed)
        self.assertEqual(result.reason, "hover_menu_confirmed")

    def test_hover_menu_parser_accepts_oak_top_for_generic_tree_woodcutting_target(self):
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Tree",
            suggested_click_point={"x": 200, "y": 146},
            click_point_space="canvas",
            target_explanation={"objectId": 1276, "name": "Tree"},
        )
        sample = {
            "wallTimeMillis": 2000,
            "mouseCanvasX": 200,
            "mouseCanvasY": 146,
            "topOption": "Chop down",
            "topTarget": "<col=ffff>Oak tree",
            "topIdentifier": 10820,
            "entries": [
                {"option": "Chop down", "target": "<col=ffff>Oak tree", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 10820},
                {"option": "Chop down", "target": "<col=ffff>Tree", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 1276},
            ],
        }

        result = hover_menu_matches_target(
            sample,
            proposal,
            {"x": 200, "y": 146},
            tolerance_px=3,
            min_wall_time_millis=1000,
        )

        self.assertTrue(result.confirmed)
        self.assertEqual(result.reason, "hover_menu_confirmed")
        self.assertTrue(result.details["expectedEntryPresentButNotTop"])

    def test_hover_menu_parser_rejects_walk_here(self):
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Tree",
            suggested_click_point={"x": 200, "y": 146},
            click_point_space="canvas",
            target_explanation={"objectId": 1276, "name": "Tree"},
        )
        sample = {
            "wallTimeMillis": 2000,
            "mouseCanvasX": 200,
            "mouseCanvasY": 146,
            "topOption": "Walk here",
            "topTarget": "",
            "topIdentifier": 0,
        }

        result = hover_menu_matches_target(
            sample,
            proposal,
            {"x": 200, "y": 146},
            tolerance_px=3,
            min_wall_time_millis=1000,
        )

        self.assertFalse(result.confirmed)
        self.assertEqual(result.reason, "top_option_not_chop")

    def test_hover_menu_parser_rejects_stale_sample(self):
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Tree",
            suggested_click_point={"x": 200, "y": 146},
            click_point_space="canvas",
            target_explanation={"objectId": 1276, "name": "Tree"},
        )
        sample = {
            "wallTimeMillis": 900,
            "mouseCanvasX": 200,
            "mouseCanvasY": 146,
            "topOption": "Chop down",
            "topTarget": "Tree",
            "topIdentifier": 1276,
        }

        result = hover_menu_matches_target(
            sample,
            proposal,
            {"x": 200, "y": 146},
            tolerance_px=3,
            min_wall_time_millis=1000,
        )

        self.assertFalse(result.confirmed)
        self.assertEqual(result.reason, "hover_menu_stale")

    def test_navigation_pre_click_accepts_stationary_walk_here_at_request_limit(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Bank booth",
            target_tile={"worldX": 3206, "worldY": 3228, "plane": 2},
            suggested_click_point={"x": 464, "y": 385},
            click_point_space="canvas",
        )
        sample = {
            "clientTick": 20,
            "wallTimeMillis": 500,
            "mouseCanvasX": 464,
            "mouseCanvasY": 385,
            "menuOpen": False,
            "topOption": "Walk here",
            "topTarget": "",
            "topType": "WALK",
            "topIdentifier": 0,
            "entries": [
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
            ],
        }

        result = _confirm_hover_menu(
            proposal,
            HoverConfirmationOptions(enabled=True, timeout_ms=0, poll_ms=1, tolerance_px=3, menu_entry_limit=5),
            {"x": 464, "y": 385},
            move_started_wall_millis=1000,
            max_requests=1,
            snapshot_fetch_func=lambda *_args, **_kwargs: {"clientTickHot": {"hoverMenu": sample, "postMenuSort": sample}},
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.01),
        )

        self.assertTrue(result["confirmed"])
        self.assertEqual(result["reason"], "stationary_navigation_hover_confirmed")
        self.assertTrue(result["matchDetails"]["staleSampleAccepted"])

    def test_hover_menu_parser_rejects_position_outside_tolerance(self):
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Tree",
            suggested_click_point={"x": 200, "y": 146},
            click_point_space="canvas",
            target_explanation={"objectId": 1276, "name": "Tree"},
        )
        sample = {
            "wallTimeMillis": 2000,
            "mouseCanvasX": 205,
            "mouseCanvasY": 146,
            "topOption": "Chop down",
            "topTarget": "Tree",
            "topIdentifier": 1276,
        }

        result = hover_menu_matches_target(
            sample,
            proposal,
            {"x": 200, "y": 146},
            tolerance_px=3,
            min_wall_time_millis=1000,
        )

        self.assertFalse(result.confirmed)
        self.assertEqual(result.reason, "mouse_position_outside_tolerance")

    def test_resource_hover_position_mismatch_retargets_matching_observed_tree(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Tree",
            suggested_click_point={"x": 200, "y": 146},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1200, "y": 2146},
            click_point_resolution={"status": "PASS", "screenClickPoint": {"x": 1200, "y": 2146}},
            target_explanation={
                "objectId": 1276,
                "name": "Tree",
                "requiredLevel": 1,
                "playerLevelKnown": False,
                "safeAimPoint": {
                    "status": "PASS",
                    "canvasX": 200,
                    "canvasY": 146,
                    "sampledAimpoints": [{"x": 202, "y": 146}],
                },
            },
            confidence=0.9,
        )
        snapshots = [
            {
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 185,
                    "mouseCanvasY": 146,
                    "topOption": "Chop down",
                    "topTarget": "Tree",
                    "topIdentifier": 1276,
                    "topType": "GAME_OBJECT_FIRST_OPTION",
                    "entries": [
                        {"option": "Chop down", "target": "Tree", "identifier": 1276, "type": "GAME_OBJECT_FIRST_OPTION"},
                        {"option": "Walk here", "target": "", "identifier": 0, "type": "WALK"},
                    ],
                }
            }
        ]

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            input_controller=HumanInputController(backend, profile="steady", sleep_func=lambda _seconds: None, seed=10),
            hover_options=HoverConfirmationOptions(
                enabled=True,
                hover_only=True,
                snapshot_url="http://snapshot",
                timeout_ms=0,
                poll_ms=10,
                tolerance_px=3,
            ),
            snapshot_fetch_func=lambda *_args, **_kwargs: snapshots.pop(0),
            monotonic_func=IncrementingClock(start=1.0, step=0.01),
            wall_time_millis_func=lambda: 1000,
        )

        self.assertEqual(result.status, "PASS", result.warnings)
        self.assertFalse(result.executed)
        self.assertEqual(result.hover_confirmation["reason"], "resource_hover_confirmed_at_observed_point")
        self.assertEqual(result.hover_confirmation["expectedCanvasPoint"], {"x": 185, "y": 146})
        self.assertIn("resource_retarget_observed_hover", [command["type"] for command in result.commands])
        self.assertEqual(result.proposal["suggestedClickPoint"], {"x": 185, "y": 146})
        self.assertEqual(result.proposal["resolvedScreenClickPoint"], {"x": 1185, "y": 2146})
        self.assertEqual(result.proposal["targetExplanation"]["selectedAimpointSource"], "hover_observed_same_target")
        self.assertTrue(result.action_trace["reacquisition"]["resourceRetargetedToObservedHover"])

    def test_hover_only_moves_and_does_not_click(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Oak tree",
            suggested_click_point={"x": 200, "y": 146},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1200, "y": 2146},
            click_point_resolution={"status": "PASS", "screenClickPoint": {"x": 1200, "y": 2146}},
            target_explanation={"objectId": 10820, "name": "Oak tree"},
            confidence=0.9,
        )
        snapshots = [
            {
                "hoverMenu": {
                    "wallTimeMillis": 9999999990000,
                    "mouseCanvasX": 200,
                    "mouseCanvasY": 146,
                    "topOption": "Chop down",
                    "topTarget": "Oak tree",
                    "topIdentifier": 10820,
                }
            }
        ]

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            input_controller=HumanInputController(backend, profile="steady", sleep_func=lambda _seconds: None, seed=10),
            hover_options=HoverConfirmationOptions(
                enabled=True,
                hover_only=True,
                snapshot_url="http://snapshot",
                timeout_ms=120,
                poll_ms=10,
                tolerance_px=3,
            ),
            snapshot_fetch_func=lambda *_args, **_kwargs: snapshots.pop(0),
            monotonic_func=lambda: 1.0,
            wall_time_millis_func=lambda: 1000,
        )

        self.assertEqual(result.status, "PASS")
        self.assertFalse(result.executed)
        self.assertEqual(backend.calls, [("move", 1200, 2146)])
        self.assertEqual(result.action_trace["humanInput"]["profile"], "steady")
        self.assertEqual(result.action_trace["humanInput"]["movementCount"], 1)
        self.assertEqual(result.action_trace["humanInput"]["directBackendBypassCount"], 0)
        self.assertTrue(result.hover_confirmation["confirmed"])
        trace = result.to_dict()["actionTrace"]
        self.assertEqual(trace["actionTraceSchema"], "action_trace.v2")
        self.assertEqual(trace["clientTick"]["acceptedHoverSample"]["topTarget"], "Oak tree")
        self.assertEqual(trace["finalClassification"], "hover_confirmed_click")

    def test_resource_alternate_aimpoint_recovers_from_tree_oak_overlap(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Tree",
            suggested_click_point={"x": 200, "y": 146},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1200, "y": 2146},
            click_point_resolution={"status": "PASS", "screenClickPoint": {"x": 1200, "y": 2146}},
            target_explanation={
                "objectId": 1276,
                "name": "Tree",
                "requiredLevel": 1,
                "playerLevelKnown": False,
                "safeAimPoint": {
                    "status": "PASS",
                    "canvasX": 200,
                    "canvasY": 146,
                    "sampledAimpoints": [{"x": 202, "y": 146}, {"x": 205, "y": 146}],
                },
            },
            confidence=0.9,
        )
        snapshots = [
            {
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 200,
                    "mouseCanvasY": 146,
                    "topOption": "Chop down",
                    "topTarget": "Oak tree",
                    "topIdentifier": 10820,
                    "entries": [
                        {"option": "Chop down", "target": "Oak tree", "identifier": 10820, "type": "GAME_OBJECT_FIRST_OPTION"},
                        {"option": "Chop down", "target": "Tree", "identifier": 1276, "type": "GAME_OBJECT_FIRST_OPTION"},
                    ],
                }
            },
            {
                "hoverMenu": {
                    "wallTimeMillis": 2100,
                    "mouseCanvasX": 202,
                    "mouseCanvasY": 146,
                    "topOption": "Chop down",
                    "topTarget": "Oak tree",
                    "topIdentifier": 10820,
                }
            },
            {
                "hoverMenu": {
                    "wallTimeMillis": 2200,
                    "mouseCanvasX": 205,
                    "mouseCanvasY": 146,
                    "topOption": "Chop down",
                    "topTarget": "Tree",
                    "topIdentifier": 1276,
                }
            },
        ]

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=True,
            input_controller=HumanInputController(backend, profile="steady", sleep_func=lambda _seconds: None, seed=10),
            hover_options=HoverConfirmationOptions(
                enabled=True,
                snapshot_url="http://snapshot",
                timeout_ms=0,
                poll_ms=10,
                tolerance_px=3,
            ),
            snapshot_fetch_func=lambda *_args, **_kwargs: snapshots.pop(0),
            monotonic_func=lambda: 1.0,
            wall_time_millis_func=lambda: 1000,
        )

        self.assertEqual(result.status, "PASS")
        self.assertFalse(result.executed)
        command_types = [command["type"] for command in result.commands]
        self.assertIn("resource_reacquire_alternate_aimpoint", command_types)
        self.assertTrue(result.action_trace["reacquisition"]["resourceAimpointReacquired"])
        self.assertEqual(result.action_trace["resourceTargetAmbiguity"]["ambiguityStatus"], "clear")
        self.assertEqual(result.proposal["targetExplanation"]["selectedAimpointSource"], "alternate_hull_sample")
        self.assertEqual(result.proposal["targetExplanation"]["aimpointSamplesTried"], 2)

    def test_last_menu_option_clicked_classifies_walk_and_chop(self):
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Tree",
            target_explanation={"objectId": 1276, "name": "Tree"},
        )

        self.assertEqual(
            classify_last_menu_option_clicked(
                None,
                {"wallTimeMillis": 2000, "option": "Walk here", "target": "", "identifier": 0},
                proposal,
            ),
            "clicked_walk_here",
        )
        self.assertEqual(
            classify_last_menu_option_clicked(
                None,
                {"wallTimeMillis": 2000, "option": "Chop down", "target": "Tree", "identifier": 1276},
                proposal,
            ),
            "clicked_chop_tree",
        )

    def test_route_transition_click_is_not_labeled_as_chop(self):
        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            target_explanation={
                "name": "Staircase",
                "objectId": 56230,
                "profile": "woodcutting",
                "expectedOptions": ["Climb-up"],
                "expectedTargets": ["Staircase"],
            },
        )

        self.assertEqual(
            classify_last_menu_option_clicked(
                None,
                {"wallTimeMillis": 2000, "option": "Climb-up", "target": "Staircase", "identifier": 56230},
                proposal,
            ),
            "clicked_expected_action",
        )

    def test_route_transition_rejects_opposite_climb_action_and_wrong_object_id(self):
        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            target_explanation={
                "name": "Staircase",
                "objectId": 16672,
                "profile": "woodcutting",
                "expectedOptions": ["Climb-down", "Climb down"],
                "dialogueOpenerOptions": ["Climb"],
                "expectedTargets": ["Staircase"],
            },
        )
        sample = {
            "wallTimeMillis": 2000,
            "mouseCanvasX": 241,
            "mouseCanvasY": 168,
            "menuOpen": False,
            "topOption": "Climb-up",
            "topTarget": "<col=ffff>Staircase",
            "topType": "GAME_OBJECT_FIRST_OPTION",
            "topIdentifier": 56230,
            "entries": [
                {"option": "Climb-up", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 56230},
                {"option": "Top-floor", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_SECOND_OPTION", "identifier": 56230},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
            ],
        }

        hover = hover_menu_matches_target(sample, proposal, {"x": 241, "y": 168}, tolerance_px=3)

        self.assertFalse(hover.confirmed)
        self.assertEqual(hover.reason, "top_option_not_expected")
        self.assertNotEqual(
            classify_last_menu_option_clicked(None, {"option": "Climb-up", "target": "Staircase", "identifier": 56230}, proposal),
            "clicked_expected_action",
        )

    def test_route_transition_plane_mismatch_blocks_stale_target_before_click(self):
        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            target_explanation={
                "name": "Staircase",
                "objectId": 16672,
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                "routeStepIndex": 1,
                "worldLocation": {"worldX": 3204, "worldY": 3229, "plane": 1},
                "expectedOptions": ["Climb-down"],
            },
        )
        status = route_transition_status_payload(tick=8384, x=3205, y=3228, plane=0, route_step_index=1)

        issue = _route_transition_plane_mismatch_issue(proposal, current_status=status)

        self.assertIsNotNone(issue)
        self.assertEqual(issue["classification"], "route_transition_target_plane_mismatch")
        self.assertEqual(issue["proposedTransition"]["worldLocation"]["plane"], 1)
        self.assertEqual(issue["playerWorldPosition"]["plane"], 0)

    def test_route_transition_menu_row_uses_observed_full_option_band(self):
        sample = {
            "menuBounds": {"x": 145, "y": 154, "width": 150, "height": 112},
            "entryCount": 6,
            "entries": [
                {"option": "Climb", "target": "<col=ffff>Staircase"},
                {"option": "Climb-up", "target": "<col=ffff>Staircase"},
                {"option": "Climb-down", "target": "<col=ffff>Staircase"},
                {"option": "Walk here", "target": ""},
                {"option": "Examine", "target": "<col=ffff>Staircase"},
            ],
        }

        self.assertEqual(_menu_row_canvas_point(sample, 2), {"x": 220, "y": 214})

    def test_route_transition_selects_direct_right_click_row_when_top_is_generic_climb(self):
        backend = FakeBackend()
        hover_sample = {
            "clientTick": 20,
            "wallTimeMillis": 500,
            "mouseCanvasX": 100,
            "mouseCanvasY": 100,
            "menuOpen": False,
            "topOption": "Climb",
            "topTarget": "<col=ffff>Staircase",
            "topType": "GAME_OBJECT_FIRST_OPTION",
            "topIdentifier": 16672,
            "entries": [
                {"option": "Climb", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 16672},
                {"option": "Climb-up", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_SECOND_OPTION", "identifier": 16672},
                {"option": "Climb-down", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_THIRD_OPTION", "identifier": 16672},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
                {"option": "Examine", "target": "<col=ffff>Staircase", "type": "EXAMINE_OBJECT", "identifier": 16672},
            ],
        }
        menu_open_sample = {
            **hover_sample,
            "clientTick": 21,
            "wallTimeMillis": 2100,
            "menuOpen": True,
            "menuBounds": {"x": 90, "y": 90, "width": 160, "height": 97},
        }
        clicked_sample = {
            "clientTick": 22,
            "wallTimeMillis": 2200,
            "option": "Climb-up",
            "target": "<col=ffff>Staircase",
            "type": "GAME_OBJECT_SECOND_OPTION",
            "identifier": 16672,
        }
        snapshots = iter(
            [
                {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}},
                {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}},
                {"clientTickHot": {"hoverMenu": menu_open_sample, "postMenuSort": menu_open_sample}},
                {"clientTickHot": {"hoverMenu": menu_open_sample, "postMenuSort": menu_open_sample, "lastMenuOptionClicked": clicked_sample}},
            ]
        )

        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            suggested_click_point={"x": 100, "y": 100},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1100, "y": 2100},
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 1000, "y": 2000},
                "canvasSize": {"width": 1834, "height": 1205},
                "sourceCanvasSize": {"width": 765, "height": 503},
                "displayScale": {"x": 1.0, "y": 1.0},
            },
            target_explanation={
                "name": "Staircase",
                "objectId": 16672,
                "expectedOptions": ["Climb-up", "Climb up"],
                "expectedTargets": ["Staircase"],
                "dialogueOpenerOptions": ["Climb"],
            },
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile=MouseMovementProfile(name="instant_test", min_duration_ms=1, max_duration_ms=1, waypoint_count=2),
            hover_options=HoverConfirmationOptions(enabled=True, timeout_ms=200, poll_ms=1, tolerance_px=3, menu_entry_limit=5),
            dry_run=False,
            snapshot_fetch_func=lambda *_args, **_kwargs: next(snapshots),
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.05),
            wall_time_millis_func=lambda: 1000,
            input_controller=HumanInputController(backend, profile="instant_debug", sleep_func=lambda _seconds: None, monotonic_func=IncrementingClock()),
        )

        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.executed)
        self.assertEqual(result.hover_confirmation["clickClassification"], "clicked_expected_action")
        self.assertEqual(result.hover_confirmation["rightClickMenuSelection"]["selectedEntry"]["option"], "Climb-up")
        self.assertIn(("mouse_down", "right"), backend.calls)
        self.assertIn(("mouse_up", "right"), backend.calls)
        self.assertIn(("mouse_down", "left"), backend.calls)
        self.assertIn(("mouse_up", "left"), backend.calls)

    def test_floor_selection_selects_bottom_floor_row_when_top_is_climb_down(self):
        backend = FakeBackend()
        hover_sample = {
            "clientTick": 20,
            "wallTimeMillis": 500,
            "mouseCanvasX": 173,
            "mouseCanvasY": 98,
            "menuOpen": False,
            "topOption": "Climb-down",
            "topTarget": "<col=ffff>Staircase",
            "topType": "GAME_OBJECT_FIRST_OPTION",
            "topIdentifier": 56231,
            "entries": [
                {"option": "Climb-down", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 56231},
                {"option": "Bottom-floor", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_SECOND_OPTION", "identifier": 56231},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
                {"option": "Examine", "target": "<col=ffff>Staircase", "type": "EXAMINE_OBJECT", "identifier": 56231},
            ],
        }
        menu_open_sample = {
            **hover_sample,
            "clientTick": 21,
            "wallTimeMillis": 2100,
            "menuOpen": True,
            "menuBounds": {"x": 90, "y": 90, "width": 170, "height": 97},
        }
        clicked_sample = {
            "clientTick": 22,
            "wallTimeMillis": 2200,
            "option": "Bottom-floor",
            "target": "<col=ffff>Staircase",
            "type": "GAME_OBJECT_SECOND_OPTION",
            "identifier": 56231,
        }
        def snapshot_fetch(_url, **_kwargs):
            if ("mouse_up", "left") in backend.calls:
                return {"clientTickHot": {"hoverMenu": menu_open_sample, "postMenuSort": menu_open_sample, "lastMenuOptionClicked": clicked_sample}}
            if ("mouse_up", "right") in backend.calls:
                return {"clientTickHot": {"hoverMenu": menu_open_sample, "postMenuSort": menu_open_sample}}
            return {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}}

        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            suggested_click_point={"x": 173, "y": 98},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1173, "y": 2098},
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 1000, "y": 2000},
                "canvasSize": {"width": 1834, "height": 1205},
                "sourceCanvasSize": {"width": 765, "height": 503},
                "displayScale": {"x": 1.0, "y": 1.0},
            },
            target_explanation={
                "name": "Staircase",
                "objectId": 56231,
                "interactionType": "floor_selection",
                "routeStepType": "floor_selection_interaction",
                "floorSelectionOption": "Bottom floor",
                "expectedOptions": ["Bottom floor"],
                "expectedTargets": ["Staircase"],
            },
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile=MouseMovementProfile(name="instant_test", min_duration_ms=1, max_duration_ms=1, waypoint_count=2),
            hover_options=HoverConfirmationOptions(enabled=True, timeout_ms=200, poll_ms=1, tolerance_px=3, menu_entry_limit=5),
            dry_run=False,
            snapshot_fetch_func=snapshot_fetch,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.05),
            wall_time_millis_func=lambda: 1000,
            input_controller=HumanInputController(backend, profile="instant_debug", sleep_func=lambda _seconds: None, monotonic_func=IncrementingClock()),
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.hover_confirmation["rightClickMenuSelection"]["selectedEntry"]["option"], "Bottom-floor")
        self.assertEqual(result.hover_confirmation["clickClassification"], "clicked_expected_action")
        self.assertIn(("mouse_down", "right"), backend.calls)
        self.assertIn(("mouse_down", "left"), backend.calls)

    def test_floor_selection_missing_bottom_floor_fails_closed_without_left_click(self):
        backend = FakeBackend()
        hover_sample = {
            "clientTick": 20,
            "wallTimeMillis": 500,
            "mouseCanvasX": 173,
            "mouseCanvasY": 98,
            "menuOpen": False,
            "topOption": "Climb-down",
            "topTarget": "<col=ffff>Staircase",
            "topType": "GAME_OBJECT_FIRST_OPTION",
            "topIdentifier": 56231,
            "entries": [
                {"option": "Climb-down", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 56231},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
                {"option": "Examine", "target": "<col=ffff>Staircase", "type": "EXAMINE_OBJECT", "identifier": 56231},
            ],
        }
        menu_open_sample = {
            **hover_sample,
            "clientTick": 21,
            "wallTimeMillis": 2100,
            "menuOpen": True,
            "menuBounds": {"x": 90, "y": 90, "width": 170, "height": 97},
        }
        def snapshot_fetch(_url, **_kwargs):
            if ("mouse_up", "right") in backend.calls:
                return {"clientTickHot": {"hoverMenu": menu_open_sample, "postMenuSort": menu_open_sample}}
            return {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}}

        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            suggested_click_point={"x": 173, "y": 98},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1173, "y": 2098},
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 1000, "y": 2000},
                "canvasSize": {"width": 1834, "height": 1205},
                "sourceCanvasSize": {"width": 765, "height": 503},
                "displayScale": {"x": 1.0, "y": 1.0},
            },
            target_explanation={
                "name": "Staircase",
                "objectId": 56231,
                "interactionType": "floor_selection",
                "routeStepType": "floor_selection_interaction",
                "floorSelectionOption": "Bottom floor",
                "expectedOptions": ["Bottom floor"],
                "expectedTargets": ["Staircase"],
            },
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile=MouseMovementProfile(name="instant_test", min_duration_ms=1, max_duration_ms=1, waypoint_count=2),
            hover_options=HoverConfirmationOptions(enabled=True, timeout_ms=200, poll_ms=1, tolerance_px=3, menu_entry_limit=5),
            dry_run=False,
            snapshot_fetch_func=snapshot_fetch,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.05),
            wall_time_millis_func=lambda: 1000,
            input_controller=HumanInputController(backend, profile="instant_debug", sleep_func=lambda _seconds: None, monotonic_func=IncrementingClock()),
        )

        self.assertEqual(result.status, "FAIL")
        self.assertEqual(result.observed_result["observedResult"], "floor_selection_option_missing")
        self.assertEqual(result.lifecycle_state["reason"], "floor_selection_option_missing")
        self.assertEqual(result.hover_confirmation["rightClickMenuSelection"]["reason"], "floor_selection_option_missing")
        self.assertIn(("mouse_down", "right"), backend.calls)
        self.assertNotIn(("mouse_down", "left"), backend.calls)

    def test_route_transition_direct_menu_uses_tick_tail_on_scaled_runelite(self):
        backend = FakeBackend()
        hover_sample = {
            "clientTick": 20,
            "wallTimeMillis": 500,
            "mouseCanvasX": 193,
            "mouseCanvasY": 184,
            "menuOpen": False,
            "topOption": "Climb",
            "topTarget": "<col=ffff>Staircase",
            "topType": "GAME_OBJECT_FIRST_OPTION",
            "topIdentifier": 16672,
            "entries": [
                {"option": "Climb", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 16672},
                {"option": "Climb-up", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_SECOND_OPTION", "identifier": 16672},
                {"option": "Climb-down", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_THIRD_OPTION", "identifier": 16672},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
                {"option": "Examine", "target": "<col=ffff>Staircase", "type": "EXAMINE_OBJECT", "identifier": 16672},
            ],
        }
        menu_opened_sample = {
            **hover_sample,
            "clientTick": 21,
            "wallTimeMillis": 2100,
            "sourceEvent": "MenuOpened",
            "sampleSource": "MenuOpened",
            "menuOpen": False,
            "entryCount": 6,
            "menuBounds": {"x": 124, "y": 148, "width": 150, "height": 112},
            "entries": [
                {"option": "Examine", "target": "<col=ffff>Staircase", "type": "EXAMINE_OBJECT", "identifier": 16672},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
                {"option": "Climb-down", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_THIRD_OPTION", "identifier": 16672},
                {"option": "Climb-up", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_SECOND_OPTION", "identifier": 16672},
                {"option": "Climb", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 16672},
            ],
        }
        clicked_sample = {
            "clientTick": 22,
            "wallTimeMillis": 2200,
            "option": "Climb-up",
            "target": "<col=ffff>Staircase",
            "type": "GAME_OBJECT_SECOND_OPTION",
            "identifier": 16672,
        }
        tail_requests = []

        def snapshot_fetch(_url, **kwargs):
            tail_requests.append(kwargs.get("client_tick_tail", 0))
            right_up = ("mouse_up", "right") in backend.calls
            left_up = ("mouse_up", "left") in backend.calls
            if left_up:
                return {
                    "clientTickHot": {
                        "hoverMenu": menu_opened_sample,
                        "postMenuSort": menu_opened_sample,
                        "lastMenuOptionClicked": clicked_sample,
                    }
                }
            if right_up:
                return {
                    "clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample},
                    "payloads": {
                        "client_tick_tail": {
                            "postMenuSortTail": [
                                hover_sample,
                                menu_opened_sample,
                            ]
                        }
                    },
                }
            return {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}}

        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            suggested_click_point={"x": 195, "y": 185},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 333, "y": 387},
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 20, "y": 68},
                "canvasSize": {"width": 1229, "height": 868},
                "sourceCanvasSize": {"width": 765, "height": 503},
                "displayScale": {"x": 1.75, "y": 1.75},
            },
            click_point_resolution={
                "status": "PASS",
                "displayScaleApplied": False,
                "displayScale": {"x": 1.75, "y": 1.75},
                "displayScaleReason": "source_canvas_expanded_to_physical_canvas_no_display_rescale",
            },
            target_explanation={
                "name": "Staircase",
                "objectId": 16672,
                "expectedOptions": ["Climb-up", "Climb up"],
                "expectedTargets": ["Staircase"],
                "dialogueOpenerOptions": ["Climb"],
                "dialogueExpectedPromptContains": ["Climb up or down"],
            },
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile=MouseMovementProfile(name="instant_test", min_duration_ms=1, max_duration_ms=1, waypoint_count=2),
            hover_options=HoverConfirmationOptions(enabled=True, timeout_ms=200, poll_ms=1, tolerance_px=3, menu_entry_limit=5),
            dry_run=False,
            snapshot_fetch_func=snapshot_fetch,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.05),
            wall_time_millis_func=lambda: 1000,
            input_controller=HumanInputController(backend, profile="instant_debug", sleep_func=lambda _seconds: None, monotonic_func=IncrementingClock()),
        )

        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.executed)
        self.assertEqual(result.hover_confirmation["clickClassification"], "clicked_expected_action")
        selection = result.hover_confirmation["rightClickMenuSelection"]
        self.assertEqual(selection["menuOpenSample"]["sourceEvent"], "MenuOpened")
        self.assertEqual(selection["selectedEntry"]["option"], "Climb-up")
        self.assertEqual(selection["clientTickTailRequested"], 5)
        self.assertGreaterEqual(max(tail_requests), 5)
        self.assertNotIn("rightClickMenuSelectionFallback", result.hover_confirmation)
        self.assertIn(("mouse_down", "right"), backend.calls)
        self.assertIn(("mouse_up", "right"), backend.calls)
        self.assertIn(("mouse_down", "left"), backend.calls)
        self.assertIn(("mouse_up", "left"), backend.calls)

    def test_route_transition_blocks_when_right_click_menu_does_not_open(self):
        backend = FakeBackend()
        hover_sample = {
            "clientTick": 20,
            "wallTimeMillis": 500,
            "mouseCanvasX": 100,
            "mouseCanvasY": 100,
            "menuOpen": False,
            "topOption": "Climb",
            "topTarget": "<col=ffff>Staircase",
            "topType": "GAME_OBJECT_FIRST_OPTION",
            "topIdentifier": 16672,
            "entries": [
                {"option": "Climb", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 16672},
                {"option": "Climb-up", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_SECOND_OPTION", "identifier": 16672},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
            ],
        }
        clicked_sample = {
            "clientTick": 22,
            "wallTimeMillis": 2200,
            "option": "Climb",
            "target": "<col=ffff>Staircase",
            "type": "GAME_OBJECT_FIRST_OPTION",
            "identifier": 16672,
        }
        snapshots = iter(
            [
                {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}},
                {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}},
                {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}},
                {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample, "lastMenuOptionClicked": clicked_sample}},
            ]
        )

        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            suggested_click_point={"x": 100, "y": 100},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1100, "y": 2100},
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 1000, "y": 2000},
                "canvasSize": {"width": 1834, "height": 1205},
                "sourceCanvasSize": {"width": 765, "height": 503},
                "displayScale": {"x": 1.0, "y": 1.0},
            },
            target_explanation={
                "name": "Staircase",
                "objectId": 16672,
                "expectedOptions": ["Climb-up", "Climb up"],
                "expectedTargets": ["Staircase"],
                "dialogueOpenerOptions": ["Climb"],
                "dialogueExpectedPromptContains": ["Climb up or down"],
            },
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile=MouseMovementProfile(name="instant_test", min_duration_ms=1, max_duration_ms=1, waypoint_count=2),
            hover_options=HoverConfirmationOptions(enabled=True, timeout_ms=200, poll_ms=1, tolerance_px=3, menu_entry_limit=5),
            dry_run=False,
            snapshot_fetch_func=lambda *_args, **_kwargs: next(snapshots),
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=1.0),
            wall_time_millis_func=lambda: 1000,
            input_controller=HumanInputController(backend, profile="instant_debug", sleep_func=lambda _seconds: None, monotonic_func=IncrementingClock()),
        )

        self.assertEqual(result.status, "FAIL")
        self.assertFalse(result.executed)
        self.assertEqual(result.hover_confirmation["rightClickMenuSelection"]["reason"], "menu_open_not_observed")
        self.assertNotIn("rightClickMenuSelectionFallback", result.hover_confirmation)
        self.assertNotIn("clickClassification", result.hover_confirmation)
        self.assertEqual(result.observed_result["observedResult"], "route_target_hover_not_confirmed")
        self.assertTrue(result.observed_result["routeTargetHoverFailure"])
        self.assertEqual(result.lifecycle_state["reason"], "route_target_hover_not_confirmed")
        self.assertEqual(result.action_trace["finalClassification"], "route_target_hover_not_confirmed")
        self.assertEqual(_hover_failure_category(result), "route_target_hover_not_confirmed")
        self.assertIn(("mouse_down", "right"), backend.calls)
        self.assertNotIn(("mouse_down", "left"), backend.calls)
        self.assertNotIn(("mouse_up", "left"), backend.calls)

    def test_route_transition_menu_open_failure_suppresses_after_two_attempts(self):
        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            suggested_click_point={"x": 535, "y": 347},
            click_point_space="screen",
            resolved_screen_click_point={"x": 535, "y": 347},
            target_explanation={
                "name": "Staircase",
                "objectId": 16672,
                "classId": "route_transition",
                "worldLocation": {"worldX": 3204, "worldY": 3229, "plane": 1},
                "expectedOptions": ["Climb-down", "Climb down"],
                "expectedTargets": ["Staircase"],
                "dialogueOpenerOptions": ["Climb"],
            },
        )
        menu_sample = {
            "sourceEvent": "PostMenuSort",
            "clientTick": 20,
            "wallTimeMillis": 500,
            "mouseCanvasX": 241,
            "mouseCanvasY": 168,
            "menuOpen": False,
            "topOption": "Climb",
            "topTarget": "<col=ffff>Staircase",
            "topIdentifier": 16672,
            "entries": [
                {"option": "Climb", "target": "<col=ffff>Staircase", "identifier": 16672, "entryIndex": 0},
                {"option": "Climb-up", "target": "<col=ffff>Staircase", "identifier": 16672, "entryIndex": 1},
                {"option": "Climb-down", "target": "<col=ffff>Staircase", "identifier": 16672, "entryIndex": 2},
                {"option": "Walk here", "target": "", "identifier": 0, "entryIndex": 3},
            ],
        }
        result = ExecutionResult(
            status="FAIL",
            dry_run=False,
            proposed_action="interact_service_route_object",
            executed=False,
            proposal=proposal.to_dict(),
            movement_plan={"clickPoint": {"x": 535, "y": 347}},
            lifecycle_state={"reason": "route_target_hover_not_confirmed"},
            hover_confirmation={
                "rightClickMenuSelection": {
                    "status": "FAIL",
                    "reason": "menu_open_not_observed",
                    "menuOpenSample": menu_sample,
                }
            },
            action_trace={"finalClassification": "route_target_hover_not_confirmed"},
            observed_result={"observedResult": "route_target_hover_not_confirmed"},
        )
        cache = {}
        summary = _new_loop_summary()

        first = _record_target_hover_failure(
            options=Namespace(target_hover_failure_limit=5, target_suppression_ms=2500),
            cache=cache,
            summary=summary,
            result=result,
            now_ms=1000,
        )
        second = _record_target_hover_failure(
            options=Namespace(target_hover_failure_limit=5, target_suppression_ms=2500),
            cache=cache,
            summary=summary,
            result=result,
            now_ms=1100,
        )

        self.assertEqual(first["reason"], "route_target_hover_not_confirmed")
        self.assertFalse(first["suppressed"])
        self.assertEqual(second["failureLimit"], 2)
        self.assertTrue(second["suppressed"])
        self.assertEqual(summary["targetsSuppressed"], 1)
        self.assertEqual(summary["suppressedTargets"][0]["reason"], "route_target_hover_not_confirmed")
        self.assertEqual(second["attemptedPoints"], [{"x": 535, "y": 347}])
        self.assertEqual(second["observedMenus"][-1]["entries"][2]["option"], "Climb-down")
        counts = _loop_counts([result])
        self.assertEqual(counts["routeTargetHoverFailures"], 1)

    def test_route_transition_uses_structured_alternate_aimpoint_after_cancel_hover(self):
        backend = FakeBackend()
        cancel_sample = {
            "clientTick": 20,
            "wallTimeMillis": 500,
            "mouseCanvasX": 100,
            "mouseCanvasY": 100,
            "menuOpen": False,
            "topOption": "Cancel",
            "topTarget": "",
            "topType": "CANCEL",
            "topIdentifier": 0,
            "entries": [{"option": "Cancel", "target": "", "type": "CANCEL", "identifier": 0}],
        }
        climb_sample = {
            "clientTick": 21,
            "wallTimeMillis": 600,
            "mouseCanvasX": 120,
            "mouseCanvasY": 100,
            "menuOpen": False,
            "topOption": "Climb-up",
            "topTarget": "<col=ffff>Staircase",
            "topType": "GAME_OBJECT_SECOND_OPTION",
            "topIdentifier": 56230,
            "entries": [{"option": "Climb-up", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_SECOND_OPTION", "identifier": 56230}],
        }
        clicked_sample = {
            "clientTick": 23,
            "wallTimeMillis": 800,
            "option": "Climb-up",
            "target": "<col=ffff>Staircase",
            "type": "GAME_OBJECT_SECOND_OPTION",
            "identifier": 56230,
        }
        snapshots = iter(
            [
                {"clientTickHot": {"hoverMenu": cancel_sample, "postMenuSort": cancel_sample}},
                {"clientTickHot": {"hoverMenu": climb_sample, "postMenuSort": climb_sample}},
                {"clientTickHot": {"hoverMenu": climb_sample, "postMenuSort": climb_sample}},
                {"clientTickHot": {"hoverMenu": climb_sample, "postMenuSort": climb_sample, "lastMenuOptionClicked": clicked_sample}},
            ]
        )

        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            suggested_click_point={"x": 100, "y": 100},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1100, "y": 2100},
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 1000, "y": 2000},
                "canvasSize": {"width": 765, "height": 503},
                "sourceCanvasSize": {"width": 765, "height": 503},
                "displayScale": {"x": 1.0, "y": 1.0},
            },
            target_explanation={
                "name": "Staircase",
                "objectId": 56230,
                "expectedOptions": ["Climb-up", "Climb up"],
                "expectedTargets": ["Staircase"],
                "safeAimPoint": {
                    "status": "PASS",
                    "sampledAimpoints": [{"x": 100, "y": 100}, {"x": 120, "y": 100}],
                },
            },
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile=MouseMovementProfile(name="instant_test", min_duration_ms=1, max_duration_ms=1, waypoint_count=2),
            hover_options=HoverConfirmationOptions(enabled=True, timeout_ms=0, poll_ms=1, tolerance_px=3, menu_entry_limit=5),
            dry_run=False,
            snapshot_fetch_func=lambda *_args, **_kwargs: next(snapshots),
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.05),
            wall_time_millis_func=lambda: 1000,
            input_controller=HumanInputController(backend, profile="instant_debug", sleep_func=lambda _seconds: None, monotonic_func=IncrementingClock()),
        )

        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.executed)
        self.assertEqual(result.hover_confirmation["clickClassification"], "clicked_expected_action")
        self.assertEqual(result.proposal["suggestedClickPoint"], {"x": 120, "y": 100})
        self.assertIn(("mouse_down", "left"), backend.calls)
        self.assertIn(("mouse_up", "left"), backend.calls)
        reacquisition = result.action_trace.get("reacquisition", {})
        self.assertTrue(reacquisition.get("routeObjectAimpointReacquired"))
        self.assertEqual(reacquisition["routeObjectAlternateAimpoints"][0]["hoverConfirmation"]["topOption"], "Climb-up")

    def test_return_route_transition_selects_direct_climb_down_row_when_available(self):
        backend = FakeBackend()
        hover_sample = {
            "clientTick": 20,
            "wallTimeMillis": 500,
            "mouseCanvasX": 100,
            "mouseCanvasY": 100,
            "menuOpen": False,
            "topOption": "Climb",
            "topTarget": "<col=ffff>Staircase",
            "topType": "GAME_OBJECT_FIRST_OPTION",
            "topIdentifier": 16672,
            "entries": [
                {"option": "Climb", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 16672},
                {"option": "Climb-up", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_SECOND_OPTION", "identifier": 16672},
                {"option": "Climb-down", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_THIRD_OPTION", "identifier": 16672},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
            ],
        }
        clicked_sample = {
            "clientTick": 22,
            "wallTimeMillis": 2200,
            "option": "Climb-down",
            "target": "<col=ffff>Staircase",
            "type": "GAME_OBJECT_THIRD_OPTION",
            "identifier": 16672,
        }
        menu_open_sample = {
            **hover_sample,
            "clientTick": 21,
            "wallTimeMillis": 2100,
            "menuOpen": True,
            "menuBounds": {"x": 90, "y": 90, "width": 160, "height": 97},
        }
        snapshots = iter(
            [
                {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}},
                {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}},
                {"clientTickHot": {"hoverMenu": menu_open_sample, "postMenuSort": menu_open_sample}},
                {"clientTickHot": {"hoverMenu": menu_open_sample, "postMenuSort": menu_open_sample, "lastMenuOptionClicked": clicked_sample}},
            ]
        )

        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            suggested_click_point={"x": 100, "y": 100},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1100, "y": 2100},
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 3932, "y": 107},
                "canvasSize": {"width": 1834, "height": 1205},
                "sourceCanvasSize": {"width": 765, "height": 503},
                "displayScale": {"x": 1.0, "y": 1.0},
            },
            target_explanation={
                "name": "Staircase",
                "objectId": 16672,
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                "expectedOptions": ["Climb-down", "Climb down"],
                "expectedTargets": ["Staircase"],
                "dialogueOpenerOptions": ["Climb"],
                "expectedPlaneChange": "-1",
            },
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile=MouseMovementProfile(name="instant_test", min_duration_ms=1, max_duration_ms=1, waypoint_count=2),
            hover_options=HoverConfirmationOptions(enabled=True, timeout_ms=200, poll_ms=1, tolerance_px=3, menu_entry_limit=5),
            dry_run=False,
            snapshot_fetch_func=lambda *_args, **_kwargs: next(snapshots),
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.05),
            wall_time_millis_func=lambda: 1000,
            input_controller=HumanInputController(backend, profile="instant_debug", sleep_func=lambda _seconds: None, monotonic_func=IncrementingClock()),
        )

        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.executed)
        self.assertEqual(result.hover_confirmation["clickClassification"], "clicked_expected_action")
        self.assertIn(("mouse_down", "right"), backend.calls)
        self.assertEqual(result.hover_confirmation["rightClickMenuSelection"]["selectedEntry"]["option"], "Climb-down")

    def test_return_route_transition_opens_menu_when_hover_only_shows_generic_climb(self):
        backend = FakeBackend()
        hover_sample = {
            "clientTick": 20,
            "wallTimeMillis": 500,
            "mouseCanvasX": 94,
            "mouseCanvasY": 101,
            "menuOpen": False,
            "topOption": "Climb",
            "topTarget": "<col=ffff>Staircase",
            "topType": "GAME_OBJECT_FIRST_OPTION",
            "topIdentifier": 16672,
            "entries": [
                {"option": "Climb", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 16672},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
            ],
        }
        menu_open_sample = {
            **hover_sample,
            "clientTick": 21,
            "wallTimeMillis": 2100,
            "menuOpen": True,
            "menuBounds": {"x": 90, "y": 90, "width": 160, "height": 97},
            "entries": [
                {"option": "Climb", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 16672},
                {"option": "Climb-up", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_SECOND_OPTION", "identifier": 16672},
                {"option": "Climb-down", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_THIRD_OPTION", "identifier": 16672},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
            ],
        }
        clicked_sample = {
            "clientTick": 22,
            "wallTimeMillis": 2200,
            "option": "Climb-down",
            "target": "<col=ffff>Staircase",
            "type": "GAME_OBJECT_THIRD_OPTION",
            "identifier": 16672,
        }
        snapshots = iter(
            [
                {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}},
                {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}},
                {"clientTickHot": {"hoverMenu": menu_open_sample, "postMenuSort": menu_open_sample}},
                {"clientTickHot": {"hoverMenu": menu_open_sample, "postMenuSort": menu_open_sample, "lastMenuOptionClicked": clicked_sample}},
            ]
        )

        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            suggested_click_point={"x": 100, "y": 100},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1100, "y": 2100},
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 1000, "y": 2000},
                "canvasSize": {"width": 1834, "height": 1205},
                "sourceCanvasSize": {"width": 765, "height": 503},
                "displayScale": {"x": 1.0, "y": 1.0},
            },
            target_explanation={
                "name": "Staircase",
                "objectId": 16672,
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                "expectedOptions": ["Climb-down", "Climb down"],
                "expectedTargets": ["Staircase"],
                "dialogueOpenerOptions": ["Climb"],
                "expectedPlaneChange": "-1",
            },
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile=MouseMovementProfile(name="instant_test", min_duration_ms=1, max_duration_ms=1, waypoint_count=2),
            hover_options=HoverConfirmationOptions(enabled=True, timeout_ms=0, poll_ms=1, tolerance_px=3, menu_entry_limit=5),
            dry_run=False,
            snapshot_fetch_func=lambda *_args, **_kwargs: next(snapshots),
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.05),
            wall_time_millis_func=lambda: 1000,
            input_controller=HumanInputController(backend, profile="instant_debug", sleep_func=lambda _seconds: None, monotonic_func=IncrementingClock()),
        )

        self.assertEqual(result.status, "PASS")
        selection = result.hover_confirmation["rightClickMenuSelection"]
        self.assertTrue(selection["expectedEntry"]["syntheticEntry"])
        self.assertEqual(selection["selectedEntry"]["option"], "Climb-down")
        self.assertNotIn("rightClickMenuSelectionFallback", result.hover_confirmation)
        self.assertIn(("mouse_down", "right"), backend.calls)

    def test_return_route_transition_blocks_when_right_click_reports_generic_climb(self):
        backend = FakeBackend()
        hover_sample = {
            "clientTick": 20,
            "wallTimeMillis": 500,
            "mouseCanvasX": 94,
            "mouseCanvasY": 101,
            "menuOpen": False,
            "topOption": "Climb",
            "topTarget": "<col=ffff>Staircase",
            "topType": "GAME_OBJECT_FIRST_OPTION",
            "topIdentifier": 16672,
            "entries": [
                {"option": "Climb", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 16672},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
            ],
        }
        menu_open_sample = {
            **hover_sample,
            "clientTick": 21,
            "wallTimeMillis": 2100,
            "menuOpen": True,
            "menuBounds": {"x": 90, "y": 90, "width": 160, "height": 97},
            "entries": [
                {"option": "Climb", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 16672},
                {"option": "Climb-down", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_THIRD_OPTION", "identifier": 16672},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
            ],
        }
        clicked_sample = {
            "clientTick": 22,
            "wallTimeMillis": 2200,
            "option": "Climb",
            "target": "<col=ffff>Staircase",
            "type": "GAME_OBJECT_FIRST_OPTION",
            "identifier": 16672,
        }
        snapshots = iter(
            [
                {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}},
                {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}},
                {"clientTickHot": {"hoverMenu": menu_open_sample, "postMenuSort": menu_open_sample}},
                {"clientTickHot": {"hoverMenu": menu_open_sample, "postMenuSort": menu_open_sample, "lastMenuOptionClicked": clicked_sample}},
            ]
        )

        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            suggested_click_point={"x": 100, "y": 100},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1100, "y": 2100},
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 1000, "y": 2000},
                "canvasSize": {"width": 1834, "height": 1205},
                "sourceCanvasSize": {"width": 765, "height": 503},
                "displayScale": {"x": 1.0, "y": 1.0},
            },
            target_explanation={
                "name": "Staircase",
                "objectId": 16672,
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                "expectedOptions": ["Climb-down", "Climb down"],
                "expectedTargets": ["Staircase"],
                "dialogueOpenerOptions": ["Climb"],
                "expectedPlaneChange": "-1",
            },
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile=MouseMovementProfile(name="instant_test", min_duration_ms=1, max_duration_ms=1, waypoint_count=2),
            hover_options=HoverConfirmationOptions(enabled=True, timeout_ms=200, poll_ms=1, tolerance_px=3, menu_entry_limit=5),
            dry_run=False,
            snapshot_fetch_func=lambda *_args, **_kwargs: next(snapshots),
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.05),
            wall_time_millis_func=lambda: 1000,
            input_controller=HumanInputController(backend, profile="instant_debug", sleep_func=lambda _seconds: None, monotonic_func=IncrementingClock()),
        )

        self.assertEqual(result.status, "FAIL")
        self.assertEqual(result.hover_confirmation["rightClickMenuSelection"]["reason"], "clicked_direct_menu_mismatch")
        self.assertEqual(result.action_trace["finalClassification"], "clicked_direct_menu_mismatch")
        self.assertFalse(any(command.get("type") == "route_transition_dialogue_opener_fallback" for command in result.commands))

    def test_return_route_transition_uses_generic_opener_when_no_direct_row_is_available(self):
        backend = FakeBackend()
        hover_sample = {
            "clientTick": 20,
            "wallTimeMillis": 500,
            "mouseCanvasX": 94,
            "mouseCanvasY": 101,
            "menuOpen": False,
            "topOption": "Climb",
            "topTarget": "<col=ffff>Staircase",
            "topType": "GAME_OBJECT_FIRST_OPTION",
            "topIdentifier": 16672,
            "entries": [
                {"option": "Climb", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 16672},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
            ],
        }
        clicked_sample = {
            "clientTick": 22,
            "wallTimeMillis": 2200,
            "option": "Climb",
            "target": "<col=ffff>Staircase",
            "type": "GAME_OBJECT_FIRST_OPTION",
            "identifier": 16672,
        }
        def snapshot_fetch(*_args, **_kwargs):
            if any(call[0] == "mouse_up" and call[1] == "left" for call in backend.calls):
                return {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample, "lastMenuOptionClicked": clicked_sample}}
            return {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}}

        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            suggested_click_point={"x": 100, "y": 100},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1100, "y": 2100},
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 3932, "y": 107},
                "canvasSize": {"width": 1834, "height": 1205},
                "sourceCanvasSize": {"width": 765, "height": 503},
                "displayScale": {"x": 1.0, "y": 1.0},
            },
            target_explanation={
                "name": "Staircase",
                "objectId": 16672,
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                "expectedOptions": ["Climb-down", "Climb down"],
                "expectedTargets": ["Staircase"],
                "dialogueOpenerOptions": ["Climb"],
                "expectedPlaneChange": "-1",
            },
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile=MouseMovementProfile(name="instant_test", min_duration_ms=1, max_duration_ms=1, waypoint_count=2),
            hover_options=HoverConfirmationOptions(enabled=True, timeout_ms=0, poll_ms=1, tolerance_px=3, menu_entry_limit=5),
            dry_run=False,
            snapshot_fetch_func=snapshot_fetch,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.05),
            wall_time_millis_func=lambda: 1000,
            input_controller=HumanInputController(backend, profile="instant_debug", sleep_func=lambda _seconds: None, monotonic_func=IncrementingClock()),
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.hover_confirmation["reason"], "stationary_route_hover_confirmed")
        self.assertEqual(result.hover_confirmation["rightClickMenuSelection"]["reason"], "menu_open_not_observed")
        self.assertEqual(result.hover_confirmation["rightClickMenuSelectionFallback"], "left_click_dialogue_opener")
        self.assertEqual(result.hover_confirmation["clickClassification"], "clicked_expected_action")
        self.assertIn(("mouse_down", "right"), backend.calls)

    def test_route_transition_uses_menu_opened_sample_when_menu_open_flag_is_false(self):
        backend = FakeBackend()
        hover_sample = {
            "clientTick": 20,
            "wallTimeMillis": 500,
            "mouseCanvasX": 100,
            "mouseCanvasY": 100,
            "menuOpen": False,
            "topOption": "Climb",
            "topTarget": "<col=ffff>Staircase",
            "topType": "GAME_OBJECT_FIRST_OPTION",
            "topIdentifier": 16672,
            "entries": [
                {"option": "Climb", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 16672},
                {"option": "Climb-up", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_SECOND_OPTION", "identifier": 16672},
                {"option": "Climb-down", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_THIRD_OPTION", "identifier": 16672},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
                {"option": "Examine", "target": "<col=ffff>Staircase", "type": "EXAMINE_OBJECT", "identifier": 16672},
            ],
        }
        menu_opened_sample = {
            **hover_sample,
            "clientTick": 21,
            "wallTimeMillis": 2100,
            "sourceEvent": "MenuOpened",
            "sampleSource": "MenuOpened",
            "menuOpen": False,
            "entryCount": 6,
            "menuBounds": {"x": 124, "y": 148, "width": 150, "height": 112},
            "entries": [
                {"option": "Examine", "target": "<col=ffff>Staircase", "type": "EXAMINE_OBJECT", "identifier": 16672},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
                {"option": "Climb-down", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_THIRD_OPTION", "identifier": 16672},
                {"option": "Climb-up", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_SECOND_OPTION", "identifier": 16672},
                {"option": "Climb", "target": "<col=ffff>Staircase", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 16672},
            ],
        }
        clicked_sample = {
            "clientTick": 22,
            "wallTimeMillis": 2200,
            "option": "Climb-up",
            "target": "<col=ffff>Staircase",
            "type": "GAME_OBJECT_SECOND_OPTION",
            "identifier": 16672,
        }
        snapshots = iter(
            [
                {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}},
                {"clientTickHot": {"hoverMenu": hover_sample, "postMenuSort": hover_sample}},
                {"clientTickHot": {"hoverMenu": menu_opened_sample, "postMenuSort": menu_opened_sample}},
                {"clientTickHot": {"hoverMenu": menu_opened_sample, "postMenuSort": menu_opened_sample, "lastMenuOptionClicked": clicked_sample}},
            ]
        )

        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            suggested_click_point={"x": 100, "y": 100},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1100, "y": 2100},
            input_geometry={
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 3932, "y": 107},
                "canvasSize": {"width": 1834, "height": 1205},
                "sourceCanvasSize": {"width": 765, "height": 503},
                "displayScale": {"x": 1.0, "y": 1.0},
            },
            target_explanation={
                "name": "Staircase",
                "objectId": 16672,
                "expectedOptions": ["Climb-up", "Climb up"],
                "expectedTargets": ["Staircase"],
                "dialogueOpenerOptions": ["Climb"],
            },
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile=MouseMovementProfile(name="instant_test", min_duration_ms=1, max_duration_ms=1, waypoint_count=2),
            hover_options=HoverConfirmationOptions(enabled=True, timeout_ms=200, poll_ms=1, tolerance_px=3, menu_entry_limit=5),
            dry_run=False,
            snapshot_fetch_func=lambda *_args, **_kwargs: next(snapshots),
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.05),
            wall_time_millis_func=lambda: 1000,
            input_controller=HumanInputController(backend, profile="instant_debug", sleep_func=lambda _seconds: None, monotonic_func=IncrementingClock()),
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.hover_confirmation["rightClickMenuSelection"]["menuOpenSample"]["sourceEvent"], "MenuOpened")
        selection = result.hover_confirmation["rightClickMenuSelection"]
        self.assertEqual(selection["selectedEntry"]["option"], "Climb-up")
        self.assertEqual(selection["selectedEntry"]["sourceEntryIndex"], 3)
        self.assertEqual(selection["selectedEntry"]["displayEntryIndex"], 1)
        row_screen = selection["rowScreenPoint"]
        self.assertGreater(row_screen["y"], 558)
        self.assertLess(row_screen["y"], 569)
        self.assertEqual(selection["rowCanvasGeometry"]["rowIndex"], 1)
        self.assertIn(("mouse_down", "right"), backend.calls)
        self.assertIn(("mouse_up", "right"), backend.calls)

    def test_menu_row_canvas_point_uses_live_lumbridge_menu_geometry(self):
        sample = {
            "menuBounds": {"x": 124, "y": 148, "width": 150, "height": 112},
            "entryCount": 6,
            "entries": [
                {"option": "Climb", "target": "<col=ffff>Staircase"},
                {"option": "Climb-up", "target": "<col=ffff>Staircase"},
                {"option": "Climb-down", "target": "<col=ffff>Staircase"},
                {"option": "Walk here", "target": ""},
                {"option": "Examine", "target": "<col=ffff>Staircase"},
            ],
        }

        point = _menu_row_canvas_point(sample, 1)

        self.assertIsNotNone(point)
        self.assertEqual(point["x"], 199)
        self.assertGreater(point["y"], 188)
        self.assertLess(point["y"], 193)

    def test_route_transition_verification_waits_for_path_to_interact_plane_change(self):
        before = route_transition_status_payload(tick=10, x=3200, y=3232, plane=0, route_step_index=3)
        samples = [
            route_transition_status_payload(tick=11, x=3200, y=3232, plane=0, route_step_index=3),
            route_transition_status_payload(tick=13, x=3206, y=3229, plane=1, route_step_index=4, current_node="lumbridge_first_floor_stairs"),
        ]
        options = Namespace(
            after_action_wait_ms=0,
            result_timeout_ms=100,
            action_timeout_ms=100,
            nav_verify_ms=2500,
            nav_verify_game_ticks=8,
            nav_progress_min_distance=1,
            nav_replan_while_moving=False,
            nav_min_game_ticks_between_clicks=3,
            nav_stuck_game_ticks=6,
        )

        def fetch_json(_url, *, timeout=0):
            return samples.pop(0) if samples else route_transition_status_payload(tick=13, x=3206, y=3229, plane=1, route_step_index=4)

        after, observed, elapsed_ms, timeline = _verify_action_after_execution(
            daemon_url="http://127.0.0.1:8890",
            options=options,
            action="interact_service_route_object",
            proposal=ActionProposal(proposed_action="interact_service_route_object", target_kind="service_route_object"),
            before_status=before,
            fetch_json_func=fetch_json,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.1),
            timeout=0.1,
        )

        self.assertEqual(after["playerContext"]["plane"], 1)
        self.assertEqual(observed["verificationStatus"], "PASS")
        self.assertEqual(observed["observedResult"], "route_transition_progress")
        self.assertIn("player_plane_changed", observed["observedSignals"])
        self.assertGreaterEqual(len(timeline), 2)

    def test_route_transition_tick_window_prevents_short_wall_timeout(self):
        before = route_transition_status_payload(tick=10, x=3200, y=3232, plane=0, route_step_index=3)
        samples = [
            route_transition_status_payload(tick=11, x=3200, y=3232, plane=0, route_step_index=3),
            route_transition_status_payload(tick=12, x=3200, y=3232, plane=0, route_step_index=3),
            route_transition_status_payload(tick=17, x=3206, y=3229, plane=1, route_step_index=4, current_node="lumbridge_first_floor_stairs"),
        ]
        options = Namespace(
            after_action_wait_ms=0,
            result_timeout_ms=100,
            action_timeout_ms=100,
            nav_verify_ms=2500,
            nav_verify_game_ticks=8,
            nav_progress_min_distance=1,
            nav_replan_while_moving=False,
            nav_min_game_ticks_between_clicks=3,
            nav_stuck_game_ticks=6,
        )

        def fetch_json(_url, *, timeout=0):
            return samples.pop(0) if samples else route_transition_status_payload(tick=17, x=3206, y=3229, plane=1, route_step_index=4)

        _after, observed, elapsed_ms, timeline = _verify_action_after_execution(
            daemon_url="http://127.0.0.1:8890",
            options=options,
            action="interact_service_route_object",
            proposal=ActionProposal(proposed_action="interact_service_route_object", target_kind="service_route_object"),
            before_status=before,
            fetch_json_func=fetch_json,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=1.0),
            timeout=0.1,
        )

        self.assertGreater(elapsed_ms, 2500)
        self.assertEqual(observed["verificationStatus"], "PASS")
        self.assertEqual(observed["observedResult"], "route_transition_progress")
        self.assertIn("player_plane_changed", observed["observedSignals"])
        self.assertGreaterEqual(len(timeline), 3)

    def test_loop_accounting_separates_hover_skip_from_action_attempt(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=True,
            hover_confirm_timeout_ms=10,
            hover_poll_ms=10,
            hover_position_tolerance=3,
            click_hold_ms=0,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=100,
            action_timeout_ms=100,
            poll_interval_ms=10,
            max_actions=2,
            max_runtime_seconds=1,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
            client_tick_debug=False,
            client_tick_tail=0,
            menu_entry_limit=5,
            require_clicked_menu_match=False,
            require_live_readiness=False,
        )
        status = status_payload_for_loop(free_slots=12, held_count=0, progress_count=0)

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: status,
            backend=backend,
            snapshot_fetch_func=lambda *_args, **_kwargs: {
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 200,
                    "mouseCanvasY": 146,
                    "topOption": "Walk here",
                    "topTarget": "",
                    "topIdentifier": 0,
                }
            },
            sleep_func=lambda _seconds: None,
            monotonic_func=iter([0.0, 0.02, 0.04]).__next__,
        )

        summary = result.to_dict()["loopSummary"]
        self.assertEqual(summary["proposedActions"], 1)
        self.assertEqual(summary["actionsAttempted"], 0)
        self.assertEqual(summary["actualClicks"], 0)
        self.assertEqual(summary["hoverConfirmFailures"], 1)
        self.assertEqual(summary["skippedHoverMismatch"], 1)
        self.assertEqual(backend.calls, [("move", 1200, 2146)])

    def test_max_total_actions_bounds_no_click_hover_skips(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=True,
            hover_confirm_timeout_ms=10,
            hover_poll_ms=10,
            hover_position_tolerance=3,
            click_hold_ms=0,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=100,
            action_timeout_ms=100,
            poll_interval_ms=10,
            max_actions=100,
            max_total_actions=2,
            max_runtime_seconds=10,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
            client_tick_debug=False,
            client_tick_tail=0,
            menu_entry_limit=5,
            require_clicked_menu_match=False,
            require_live_readiness=False,
        )
        status = status_payload_for_loop(free_slots=12, held_count=0, progress_count=0)

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: status,
            backend=backend,
            snapshot_fetch_func=lambda *_args, **_kwargs: {
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 200,
                    "mouseCanvasY": 146,
                    "topOption": "Walk here",
                    "topTarget": "",
                    "topIdentifier": 0,
                }
            },
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(start=0.0, step=0.01),
        )

        payload = result.to_dict()
        self.assertEqual(payload["reason"], "max_total_actions_reached")
        self.assertEqual(payload["actionResultCount"], 2)
        self.assertEqual(payload["executedActionCount"], 0)
        self.assertEqual(payload["loopSummary"]["skippedHoverMismatch"], 2)

    def test_navigation_no_click_failure_stops_repeated_waypoint_trace(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "navigation_decisions.jsonl"
            options = Namespace(
                timeout=0.01,
                backend="pyautogui",
                movement_profile="instant_test",
                input_profile="steady",
                execute=True,
                verify_after_action=True,
                after_action_wait_ms=0,
                hover_confirm_target=True,
                hover_confirm_timeout_ms=10,
                hover_poll_ms=10,
                hover_position_tolerance=3,
                click_hold_ms=0,
                wait_for_ready=0,
                cooldown_ms=0,
                result_timeout_ms=100,
                action_timeout_ms=100,
                poll_interval_ms=10,
                max_actions=100,
                max_total_actions=5,
                max_runtime_seconds=10,
                stop_on_warn=False,
                stop_on_fail=False,
                stop_after_inventory_changes=None,
                stop_when_inventory_full=False,
                max_successful_actions=None,
                max_timeouts=None,
                max_consecutive_timeouts=None,
                max_consecutive_no_progress=None,
                seed=None,
                client_tick_debug=False,
                client_tick_tail=0,
                menu_entry_limit=5,
                require_clicked_menu_match=False,
                require_live_readiness=False,
                nav_trace=True,
                nav_trace_output=str(trace_path),
                nav_trace_console=False,
                nav_verify_game_ticks=3,
                nav_progress_min_distance=1,
                nav_replan_while_moving=False,
                max_navigation_reacquire_rounds=0,
                camera_reacquire_waypoint=False,
                camera_reacquire_on_edge_projection=False,
            )
            status = navigation_status_payload(tick=20, x=3233, y=3227, service_distance=12, path_distance=12)

            result = execute_action_loop(
                "http://daemon",
                options,
                fetch_json_func=lambda *_args, **_kwargs: status,
                backend=backend,
                snapshot_fetch_func=lambda *_args, **_kwargs: {},
                sleep_func=lambda _seconds: None,
                monotonic_func=IncrementingClock(start=0.0, step=0.01),
            )

            payload = result.to_dict()
            records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(payload["actionResultCount"], 1)
        self.assertEqual(payload["executedActionCount"], 0)
        self.assertEqual(payload["reason"], "hover_confirm_timeout")
        self.assertEqual(sum(1 for record in records if record["decision"] == "click"), 1)
        self.assertEqual(records[-1]["decision"], "fail")
        self.assertEqual(records[-1]["pending"]["observedResult"], "hover_confirm_timeout")
        self.assertEqual(records[-1]["pending"]["resultOutcome"], "blocked")
        self.assertEqual(payload["loopSummary"]["actualClicks"], 0)

    def test_loop_counts_volatile_no_click_skip_without_timeout(self):
        result = ExecutionResult(
            status="WARN",
            proposed_action="navigate_to_service",
            dry_run=False,
            executed=False,
            hover_confirmation={"status": "PASS", "confirmed": True, "latencyMillis": 20},
            observed_result={"observedResult": "no_click_safety_skip", "resultOutcome": "skipped", "resultComplete": True},
            action_trace={
                "finalClassification": "hover_mismatch_skipped",
                "clientTick": {
                    "volatileHoverZone": True,
                    "volatileReasons": ["recent_npc_action"],
                },
            },
        )

        counts = _loop_counts([result])

        self.assertEqual(counts["actualClicks"], 0)
        self.assertEqual(counts["timeouts"], 0)
        self.assertEqual(counts["volatileHoverSkips"], 1)
        self.assertEqual(counts["volatileHoverFailures"], 1)

    def test_loop_stops_on_free_slots_zero_without_clicking(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=True,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=100,
            action_timeout_ms=100,
            poll_interval_ms=10,
            max_actions=5,
            max_runtime_seconds=1,
            final_reconcile_ms=0,
            stop_on_warn=False,
            stop_on_fail=True,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=True,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
            require_live_readiness=False,
        )
        status = status_payload_for_loop(
            phase="inventory_full",
            active_intent="needs_service",
            free_slots=0,
            held_count=28,
        )

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: status,
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=iter([0.0, 0.01]).__next__,
        )

        payload = result.to_dict()
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["reason"], "inventory_full")
        self.assertEqual(payload["executedActionCount"], 0)
        self.assertEqual(payload["loopSummary"]["inventoryFullEnd"], True)
        self.assertEqual(payload["loopSummary"]["actualClicks"], 0)
        self.assertEqual(backend.calls, [])

    def test_repeated_cancel_hover_suppresses_target_and_reacquires_next_candidate(self):
        backend = FakeBackend()
        target_a = {
            "targetName": "Tree",
            "classId": "tree",
            "id": 1276,
            "worldX": 3194,
            "worldY": 3249,
            "plane": 0,
            "aimPoint": {"canvasX": 200, "canvasY": 146},
            "onScreen": True,
            "geometryAvailable": True,
        }
        target_b = {
            "targetName": "Tree",
            "classId": "tree",
            "id": 1276,
            "worldX": 3197,
            "worldY": 3247,
            "plane": 0,
            "aimPoint": {"canvasX": 300, "canvasY": 150},
            "onScreen": True,
            "geometryAvailable": True,
        }
        status = status_payload_with_candidates_for_loop(active_target=target_a, candidates=[target_a, target_b])
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=False,
            after_action_wait_ms=0,
            hover_confirm_target=True,
            hover_confirm_timeout_ms=0,
            hover_poll_ms=10,
            hover_position_tolerance=3,
            click_hold_ms=0,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=100,
            action_timeout_ms=100,
            poll_interval_ms=10,
            max_actions=2,
            max_runtime_seconds=5,
            final_reconcile_ms=0,
            pacing_profile="instant_debug",
            target_hover_failure_limit=2,
            target_suppression_ms=2500,
            clear_suppression_on_progress=True,
            max_candidate_reacquire_rounds=3,
            no_safe_target_wait_ms=0,
            suppressed_target_wait_ms=0,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
            client_tick_debug=False,
            client_tick_tail=0,
            menu_entry_limit=5,
            require_clicked_menu_match=False,
            require_live_readiness=False,
        )
        snapshots = [
            {
                "hoverMenu": {
                    "wallTimeMillis": 2000,
                    "mouseCanvasX": 200,
                    "mouseCanvasY": 146,
                    "menuOpen": False,
                    "topOption": "Cancel",
                    "topTarget": "",
                    "topType": "CANCEL",
                    "topIdentifier": 0,
                    "entries": [{"option": "Cancel", "target": "", "type": "CANCEL", "identifier": 0}],
                }
            },
            {
                "hoverMenu": {
                    "wallTimeMillis": 9999999990010,
                    "mouseCanvasX": 200,
                    "mouseCanvasY": 146,
                    "menuOpen": False,
                    "topOption": "Cancel",
                    "topTarget": "",
                    "topType": "CANCEL",
                    "topIdentifier": 0,
                    "entries": [{"option": "Cancel", "target": "", "type": "CANCEL", "identifier": 0}],
                }
            },
            {
                "hoverMenu": {
                    "wallTimeMillis": 9999999990020,
                    "mouseCanvasX": 300,
                    "mouseCanvasY": 150,
                    "topOption": "Chop down",
                    "topTarget": "Tree",
                    "topType": "GAME_OBJECT_FIRST_OPTION",
                    "topIdentifier": 1276,
                },
                "lastMenuOptionClicked": {"clientTick": 1, "wallTimeMillis": 9999999990000, "option": "Walk here", "identifier": 0},
            },
            {
                "hoverMenu": {
                    "wallTimeMillis": 9999999990025,
                    "mouseCanvasX": 300,
                    "mouseCanvasY": 150,
                    "topOption": "Chop down",
                    "topTarget": "Tree",
                    "topType": "GAME_OBJECT_FIRST_OPTION",
                    "topIdentifier": 1276,
                },
                "lastMenuOptionClicked": {"clientTick": 1, "wallTimeMillis": 9999999990000, "option": "Walk here", "identifier": 0},
            },
            {
                "lastMenuOptionClicked": {
                    "clientTick": 2,
                    "wallTimeMillis": 9999999990030,
                    "option": "Chop down",
                    "target": "Tree",
                    "type": "GAME_OBJECT_FIRST_OPTION",
                    "identifier": 1276,
                }
            },
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: status,
            backend=backend,
            snapshot_fetch_func=lambda *_args, **_kwargs: snapshots.pop(0),
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(),
        )

        payload = result.to_dict()
        summary = payload["loopSummary"]
        self.assertEqual(payload["executedActionCount"], 1)
        self.assertEqual(summary["actualClicks"], 1)
        self.assertEqual(summary["expectedMenuClicks"], 1)
        self.assertEqual(summary["cancelHoverFailures"], 2)
        self.assertEqual(summary["targetsSuppressed"], 2)
        self.assertEqual(summary["targetReacquireRounds"], 6)
        self.assertEqual(summary["walkHereClicks"], 0)
        self.assertEqual(summary["cancelClicks"], 0)
        self.assertIn(("mouse_down", "left"), backend.calls)
        self.assertIn(("mouse_up", "left"), backend.calls)
        self.assertEqual(payload["actionResults"][-1]["proposal"]["targetTile"]["worldX"], 3197)
        self.assertEqual(payload["actionResults"][1]["actionTrace"]["targetSuppression"]["suppressed"], True)

    def test_walk_here_resource_hover_suppresses_immediately(self):
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Tree",
            target_tile={"worldX": 3217, "worldY": 3231, "plane": 0},
            suggested_click_point={"x": 220, "y": 170},
            target_explanation={
                "targetKey": "tree-3217-3231",
                "name": "Tree",
                "worldLocation": {"worldX": 3217, "worldY": 3231, "plane": 0},
            },
            actionability="needs_hover_confirmation",
        )
        result = ExecutionResult(
            status="WARN",
            proposed_action="select_resource_target",
            dry_run=False,
            proposal=proposal.to_dict(),
            hover_confirmation={
                "confirmed": False,
                "reason": "top_option_rejected",
                "latestHoverMenu": {"topOption": "Walk here", "topTarget": "", "type": "WALK"},
            },
            action_trace={},
        )
        summary = _new_loop_summary()

        event = _record_target_hover_failure(
            options=Namespace(target_hover_failure_limit=2, target_suppression_ms=2500),
            cache={},
            summary=summary,
            result=result,
            now_ms=1000,
        )

        self.assertIsNotNone(event)
        self.assertTrue(event["suppressed"])
        self.assertEqual(event["failureCount"], 1)
        self.assertEqual(event["failureLimit"], 1)
        self.assertEqual(event["reason"], "walk_here_hover_for_resource")
        self.assertEqual(summary["targetsSuppressed"], 1)
        self.assertEqual(summary["suppressedTargets"][0]["reason"], "walk_here_hover_for_resource")

    def test_all_candidates_suppressed_waits_without_clicking(self):
        backend = FakeBackend()
        target = {
            "targetName": "Tree",
            "classId": "tree",
            "id": 1276,
            "worldX": 3194,
            "worldY": 3249,
            "plane": 0,
            "aimPoint": {"canvasX": 200, "canvasY": 146},
            "onScreen": True,
            "geometryAvailable": True,
        }
        status = status_payload_with_candidates_for_loop(active_target=target, candidates=[target])
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=False,
            hover_confirm_target=True,
            hover_confirm_timeout_ms=0,
            hover_poll_ms=10,
            hover_position_tolerance=3,
            click_hold_ms=0,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=100,
            action_timeout_ms=100,
            poll_interval_ms=10,
            max_actions=1,
            max_runtime_seconds=0.2,
            final_reconcile_ms=0,
            pacing_profile="instant_debug",
            target_hover_failure_limit=1,
            target_suppression_ms=2500,
            clear_suppression_on_progress=True,
            max_candidate_reacquire_rounds=2,
            no_safe_target_wait_ms=0,
            suppressed_target_wait_ms=0,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
            client_tick_debug=False,
            client_tick_tail=0,
            menu_entry_limit=5,
            require_clicked_menu_match=False,
            require_live_readiness=False,
        )

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: status,
            backend=backend,
            snapshot_fetch_func=lambda *_args, **_kwargs: {
                "hoverMenu": {
                    "wallTimeMillis": 9999999990000,
                    "mouseCanvasX": 200,
                    "mouseCanvasY": 146,
                    "menuOpen": False,
                    "topOption": "Cancel",
                    "topTarget": "",
                    "topType": "CANCEL",
                    "topIdentifier": 0,
                    "entries": [{"option": "Cancel", "target": "", "type": "CANCEL", "identifier": 0}],
                }
            },
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(step=0.02),
        )

        summary = result.to_dict()["loopSummary"]
        self.assertEqual(summary["actualClicks"], 0)
        self.assertEqual(summary["targetsSuppressed"], 1)
        self.assertGreaterEqual(summary["targetReacquireWaits"], 1)
        self.assertTrue(all(call[0] != "click_at" for call in backend.calls))

    def test_final_reconcile_folds_delayed_inventory_success(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=False,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=50,
            action_timeout_ms=50,
            poll_interval_ms=10,
            max_actions=1,
            max_runtime_seconds=1,
            final_reconcile_ms=100,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=1,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
            require_live_readiness=False,
        )
        statuses = [
            status_payload_for_loop(free_slots=12, held_count=0, progress_count=0),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=12, held_count=0, progress_count=0),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=11, held_count=1, progress_count=1),
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
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["reason"], "inventory_change_limit_reached")
        self.assertEqual(payload["loopSummary"]["inventoryChanges"], 1)
        self.assertEqual(payload["loopSummary"]["finalReconcileResult"], "inventory_changed")
        self.assertEqual(payload["actionResults"][0]["actionTrace"]["finalClassification"], "inventory_changed_success")

    def test_final_reconcile_can_wait_for_game_ticks_beyond_wall_window(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=False,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=50,
            action_timeout_ms=50,
            poll_interval_ms=10,
            max_actions=1,
            max_runtime_seconds=1,
            final_reconcile_ms=1,
            final_reconcile_game_ticks=3,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=1,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
            require_live_readiness=False,
        )
        statuses = [
            status_payload_for_loop(free_slots=12, held_count=0, progress_count=0, tick=10),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=12, held_count=0, progress_count=0, tick=10),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=12, held_count=0, progress_count=0, tick=11),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=11, held_count=1, progress_count=1, tick=13),
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(step=0.02),
        )

        payload = result.to_dict()
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["loopSummary"]["finalReconcileGameTicks"], 3)
        self.assertEqual(payload["loopSummary"]["finalReconcileResult"], "inventory_changed")
        self.assertEqual(payload["loopSummary"]["inventoryChanges"], 1)

    def test_confirmed_service_click_timeout_keeps_waiting_until_bank_opens(self):
        result = ExecutionResult(
            status="PASS",
            proposed_action="open_service",
            dry_run=False,
            executed=True,
            hover_confirmation={"clickClassification": "clicked_expected_action"},
            observed_result={
                "observedResult": "service_object_no_progress",
                "resultOutcome": "no_change_timeout",
                "warnings": ["bank UI did not open before timeout"],
            },
            action_trace={},
        )

        self.assertTrue(_service_object_timeout_wait_extension_allowed(result))
        pending = _service_object_timeout_pending_observation(result.observed_result)
        self.assertEqual(pending["observedResult"], "service_object_click_confirmed_waiting")
        self.assertEqual(pending["resultOutcome"], "still_waiting")
        self.assertFalse(pending["resultComplete"])
        result.observed_result = pending
        result.action_trace["serviceObjectTimeoutExtendedWait"] = True
        counts = _loop_counts([result])
        self.assertEqual(counts["serviceObjectTimeoutExtendedWaits"], 1)
        self.assertEqual(counts["pendingButSafe"], 1)

    def test_resource_timeout_waiting_state_reconciles_when_inventory_lands_late(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=False,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=50,
            action_timeout_ms=50,
            poll_interval_ms=10,
            max_actions=2,
            max_runtime_seconds=1,
            final_reconcile_ms=0,
            final_reconcile_game_ticks=0,
            resource_reconcile_ms=0,
            resource_reconcile_game_ticks=0,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=1,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            max_consecutive_timeouts=3,
            seed=None,
            require_live_readiness=False,
        )
        statuses = [
            status_payload_for_loop(free_slots=12, held_count=0, progress_count=0, tick=1),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=12, held_count=0, progress_count=0, tick=1),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=12, held_count=0, progress_count=0, tick=2),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=11, held_count=1, progress_count=1, tick=3),
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(step=0.1),
        )

        payload = result.to_dict()
        observed = payload["actionResults"][0]["observedResult"]
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["reason"], "inventory_change_limit_reached")
        self.assertTrue(observed["delayedProgressReconciliation"])
        self.assertEqual(observed["previousResultOutcome"], "no_change_timeout")
        self.assertEqual(observed["resourceProgressClassification"], "resource_timeout_reconciled_success")
        self.assertEqual(payload["loopSummary"]["resourceTimeoutReconciledSuccesses"], 1)

    def test_timeout_screenshots_do_not_repeat_during_extended_resource_wait(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            options = Namespace(
                timeout=0.01,
                backend="pyautogui",
                movement_profile="instant_test",
                execute=True,
                verify_after_action=True,
                after_action_wait_ms=0,
                hover_confirm_target=False,
                wait_for_ready=0,
                cooldown_ms=0,
                result_timeout_ms=50,
                action_timeout_ms=50,
                poll_interval_ms=10,
                max_actions=2,
                max_runtime_seconds=2,
                final_reconcile_ms=0,
                final_reconcile_game_ticks=0,
                resource_reconcile_ms=0,
                resource_reconcile_game_ticks=0,
                stop_on_warn=False,
                stop_on_fail=False,
                stop_after_inventory_changes=1,
                stop_when_inventory_full=False,
                max_successful_actions=None,
                max_timeouts=None,
                max_consecutive_timeouts=8,
                seed=None,
                require_live_readiness=False,
                capture_debug_screenshots=True,
                screenshot_on_failure=False,
                screenshot_on_camera_recovery=False,
                screenshot_on_timeout=True,
                screenshot_on_edge_reject=False,
                screenshot_on_lifecycle_transition=False,
                max_debug_screenshots=10,
                debug_screenshot_dir=tmp,
                visual_debug_screenshot_func=lambda _region=None: FakeImage(),
            )
            statuses = [
                status_payload_for_loop(free_slots=12, held_count=0, progress_count=0, tick=1),
                status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=12, held_count=0, progress_count=0, tick=1),
                status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=12, held_count=0, progress_count=0, tick=2),
                status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=12, held_count=0, progress_count=0, tick=3),
                status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=12, held_count=0, progress_count=0, tick=4),
                status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=11, held_count=1, progress_count=1, tick=5),
            ]

            result = execute_action_loop(
                "http://daemon",
                options,
                fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
                backend=backend,
                sleep_func=lambda _seconds: None,
                monotonic_func=IncrementingClock(step=0.1),
            )

            summary = result.to_dict()["loopSummary"]
            self.assertEqual(summary["resourceTimeoutReconciledSuccesses"], 1)
            self.assertLessEqual(summary["debugScreenshotBundlesCaptured"], 1)

    def test_resource_timeout_reacquired_target_extends_wait_for_late_inventory(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=False,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=50,
            action_timeout_ms=50,
            poll_interval_ms=10,
            max_actions=2,
            max_runtime_seconds=1,
            final_reconcile_ms=0,
            final_reconcile_game_ticks=0,
            resource_reconcile_ms=0,
            resource_reconcile_game_ticks=0,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=1,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            max_consecutive_timeouts=3,
            seed=None,
            require_live_readiness=False,
        )
        statuses = [
            status_payload_for_loop(free_slots=12, held_count=0, progress_count=0, tick=1),
            status_payload_for_loop(phase="target_selected", active_intent="select_target", free_slots=12, held_count=0, progress_count=0, tick=1),
            status_payload_for_loop(phase="target_selected", active_intent="select_target", free_slots=12, held_count=0, progress_count=0, tick=2),
            status_payload_for_loop(phase="target_selected", active_intent="select_target", free_slots=11, held_count=1, progress_count=1, tick=3),
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(step=0.1),
        )

        payload = result.to_dict()
        observed = payload["actionResults"][0]["observedResult"]
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["reason"], "inventory_change_limit_reached")
        self.assertTrue(observed["delayedProgressReconciliation"])
        self.assertTrue(observed["resourceNoProgressTargetReacquired"])
        self.assertEqual(observed["resourceProgressClassification"], "resource_timeout_reconciled_success")
        self.assertEqual(payload["loopSummary"]["resourceTimeoutReconciledSuccesses"], 1)

    def test_final_reconcile_converts_timeout_to_inventory_full_success(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=False,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=50,
            action_timeout_ms=50,
            poll_interval_ms=10,
            max_actions=2,
            max_runtime_seconds=1,
            final_reconcile_ms=100,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=True,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
            require_live_readiness=False,
        )
        before = status_payload_for_loop(free_slots=1, held_count=0, progress_count=0, tick=1)
        waiting = status_payload_for_loop(
            phase="wait_for_result",
            active_intent="wait_for_result",
            free_slots=1,
            held_count=0,
            progress_count=0,
            tick=2,
        )
        final = status_payload_for_loop(
            phase="inventory_full",
            active_intent="needs_service",
            free_slots=0,
            held_count=1,
            progress_count=1,
            tick=3,
        )
        final["inventoryFull"] = True
        final["brain"]["inventoryContext"]["inventoryFull"] = True
        final["serviceContext"] = {"serviceReady": True}
        statuses = [before, waiting, waiting, final]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=iter([0.0, 0.0, 0.1, 0.11, 0.12]).__next__,
        )

        payload = result.to_dict()
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["reason"], "inventory_full")
        self.assertEqual(payload["lifecycleState"]["currentState"], "verified")
        self.assertEqual(payload["loopSummary"]["inventoryChanges"], 1)
        self.assertEqual(payload["loopSummary"]["inventoryFreeSlotsEnd"], 0)
        self.assertEqual(payload["loopSummary"]["finalReconcileResult"], "inventory_changed")
        self.assertEqual(payload["actionResults"][0]["actionTrace"]["finalClassification"], "inventory_changed_success")

    def test_resource_reconcile_converts_resource_timeout_to_delayed_success(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=2,
            hover_confirm_target=False,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=1,
            action_timeout_ms=1,
            poll_interval_ms=10,
            max_actions=1,
            max_runtime_seconds=1,
            final_reconcile_ms=100,
            final_reconcile_game_ticks=0,
            resource_reconcile_ms=0,
            resource_reconcile_game_ticks=0,
            post_click_progress_tail_ticks=0,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=1,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
            require_live_readiness=False,
        )
        statuses = [
            status_payload_for_loop(free_slots=12, held_count=0, progress_count=0, tick=1),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=12, held_count=0, progress_count=0, tick=2),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=11, held_count=1, progress_count=1, tick=3),
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(step=0.02),
        )

        payload = result.to_dict()
        observed = payload["actionResults"][0]["observedResult"]
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["reason"], "inventory_change_limit_reached")
        self.assertEqual(payload["loopSummary"]["timeouts"], 0)
        self.assertEqual(payload["loopSummary"]["delayedProgressReconciliations"], 1)
        self.assertEqual(payload["loopSummary"]["resourceTimeoutReconciledSuccesses"], 1)
        self.assertEqual(observed["resourceProgressClassification"], "resource_timeout_reconciled_success")
        self.assertTrue(observed["delayedProgressReconciliation"])

    def test_resource_reconcile_extends_initial_resource_verification_window(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=2,
            hover_confirm_target=False,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=1,
            action_timeout_ms=1,
            poll_interval_ms=10,
            max_actions=2,
            max_runtime_seconds=1,
            final_reconcile_ms=0,
            final_reconcile_game_ticks=0,
            resource_reconcile_ms=100,
            resource_reconcile_game_ticks=0,
            post_click_progress_tail_ticks=0,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=1,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
            require_live_readiness=False,
        )
        statuses = [
            status_payload_for_loop(free_slots=12, held_count=0, progress_count=0, tick=1),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=12, held_count=0, progress_count=0, tick=2),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=11, held_count=1, progress_count=1, tick=3),
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(step=0.02),
        )

        payload = result.to_dict()
        observed = payload["actionResults"][0]["observedResult"]
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["reason"], "inventory_change_limit_reached")
        self.assertEqual(payload["loopSummary"]["timeouts"], 0)
        self.assertEqual(payload["loopSummary"]["delayedProgressReconciliations"], 0)
        self.assertEqual(observed["resourceProgressClassification"], "resource_delayed_inventory_success")

    def test_resource_no_progress_timeout_continues_when_target_reacquired(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=False,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=1,
            action_timeout_ms=1,
            poll_interval_ms=10,
            max_actions=2,
            max_runtime_seconds=1,
            final_reconcile_ms=0,
            final_reconcile_game_ticks=0,
            resource_reconcile_ms=0,
            resource_reconcile_game_ticks=0,
            post_click_progress_tail_ticks=0,
            stop_on_warn=False,
            stop_on_fail=False,
            stop_after_inventory_changes=1,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
            require_live_readiness=False,
            pacing_profile="instant_debug",
        )
        statuses = [
            status_payload_for_loop(free_slots=12, held_count=0, progress_count=0, tick=1),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=12, held_count=0, progress_count=0, tick=2),
            status_payload_for_loop(phase="target_selected", active_intent="continue_current_target", free_slots=12, held_count=0, progress_count=0, tick=3),
            status_payload_for_loop(phase="target_selected", active_intent="continue_current_target", free_slots=12, held_count=0, progress_count=0, tick=4),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=11, held_count=1, progress_count=1, tick=5),
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(step=0.02),
        )

        payload = result.to_dict()
        first_observed = payload["actionResults"][0]["observedResult"]
        self.assertEqual(payload["status"], "WARN")
        self.assertEqual(payload["reason"], "inventory_change_limit_reached")
        self.assertEqual(payload["loopSummary"]["timeouts"], 1)
        self.assertEqual(payload["loopSummary"]["resourceTimeoutNoProgress"], 1)
        self.assertTrue(first_observed["resourceNoProgressContinued"])

    def test_immediate_inventory_progress_updates_action_trace_classification(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=False,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=5000,
            action_timeout_ms=5000,
            poll_interval_ms=10,
            max_actions=1,
            max_runtime_seconds=1,
            final_reconcile_ms=0,
            stop_on_warn=False,
            stop_on_fail=True,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
            require_live_readiness=False,
        )
        statuses = [
            status_payload_for_loop(free_slots=12, held_count=0, progress_count=0),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=11, held_count=1, progress_count=1),
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(step=0.01),
        )

        payload = result.to_dict()
        self.assertEqual(payload["loopSummary"]["inventoryChanges"], 1)
        self.assertEqual(payload["actionResults"][0]["actionTrace"]["finalClassification"], "inventory_changed_success")

    def test_steady_pacing_applies_bounded_delay_after_inventory_change(self):
        backend = FakeBackend()
        sleeps = []
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            execute=True,
            verify_after_action=True,
            after_action_wait_ms=0,
            hover_confirm_target=False,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=5000,
            action_timeout_ms=5000,
            poll_interval_ms=10,
            max_actions=2,
            max_runtime_seconds=5,
            final_reconcile_ms=0,
            pacing_profile="steady",
            target_switch_min_ms=400,
            target_switch_max_ms=1400,
            post_resource_min_ms=0,
            post_resource_max_ms=0,
            occasional_idle_chance=0.0,
            occasional_idle_min_ms=0,
            occasional_idle_max_ms=0,
            stop_on_warn=False,
            stop_on_fail=True,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            seed=None,
            require_live_readiness=False,
        )
        statuses = [
            status_payload_for_loop(free_slots=12, held_count=0, progress_count=0, tick=1),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=11, held_count=1, progress_count=1, tick=2),
            status_payload_for_loop(free_slots=11, held_count=1, progress_count=1, tick=3),
            status_payload_for_loop(phase="wait_for_result", active_intent="wait_for_result", free_slots=10, held_count=2, progress_count=2, tick=4),
        ]

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: statuses.pop(0),
            backend=backend,
            sleep_func=lambda seconds: sleeps.append(seconds),
            monotonic_func=iter([0.0, 0.01, 0.02, 0.03, 1.03, 1.04, 1.05, 1.06]).__next__,
        )

        payload = result.to_dict()
        self.assertEqual(payload["executedActionCount"], 2)
        self.assertIn(0.9, sleeps)
        self.assertEqual(payload["loopSummary"]["pacingDelayMinMillis"], 900)
        self.assertEqual(payload["loopSummary"]["pacingDelayMaxMillis"], 900)
        self.assertEqual(payload["actionResults"][0]["actionTrace"]["pacing"]["appliedDelayMs"], 900)

    def test_execute_click_uses_human_controller_click_hold_and_trace(self):
        backend = FakeBackend()
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 10, "worldY": 9, "plane": 0},
            suggested_click_point={"x": 100, "y": 100},
            click_point_space="canvas",
            resolved_screen_click_point={"x": 1100, "y": 2100},
            click_point_resolution={"status": "PASS", "screenClickPoint": {"x": 1100, "y": 2100}},
            target_explanation={"name": "Service waypoint", "classId": "service_route_anchor"},
        )

        result = execute_action(
            proposal,
            backend=backend,
            movement_profile="instant_test",
            dry_run=False,
            input_controller=HumanInputController(backend, profile="steady", sleep_func=lambda _seconds: None, seed=11),
        )

        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.executed)
        self.assertEqual(backend.calls[-2:], [("mouse_down", "left"), ("mouse_up", "left")])
        human = result.action_trace["humanInput"]
        self.assertEqual(human["profile"], "steady")
        self.assertEqual(human["averageClickHoldMs"], 82)
        self.assertEqual(human["directBackendBypassCount"], 0)

    def test_action_loop_summary_reports_human_input_metrics(self):
        backend = FakeBackend()
        options = Namespace(
            timeout=0.01,
            backend="pyautogui",
            movement_profile="instant_test",
            input_profile="steady",
            execute=True,
            verify_after_action=False,
            hover_confirm_target=False,
            wait_for_ready=0,
            cooldown_ms=0,
            result_timeout_ms=100,
            action_timeout_ms=100,
            poll_interval_ms=10,
            max_actions=1,
            max_runtime_seconds=1,
            final_reconcile_ms=0,
            final_reconcile_game_ticks=0,
            pacing_profile="instant_debug",
            stop_on_warn=False,
            stop_on_fail=True,
            stop_after_inventory_changes=None,
            stop_when_inventory_full=False,
            max_successful_actions=None,
            max_timeouts=None,
            seed=12,
            require_live_readiness=False,
        )

        result = execute_action_loop(
            "http://daemon",
            options,
            fetch_json_func=lambda *_args, **_kwargs: status_payload_for_loop(),
            backend=backend,
            sleep_func=lambda _seconds: None,
            monotonic_func=IncrementingClock(step=0.01),
        )

        summary = result.to_dict()["loopSummary"]
        self.assertEqual(summary["inputProfile"], "steady")
        self.assertEqual(summary["directBackendBypassCount"], 0)
        self.assertGreaterEqual(summary["averageMouseMoveMs"], 0)


if __name__ == "__main__":
    unittest.main()

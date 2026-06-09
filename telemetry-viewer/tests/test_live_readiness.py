import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_live_readiness.py"
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_live_readiness
import live_readiness
from input_control.executor import execute_next_action
from input_control.input_geometry import resolve_input_geometry_status


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_baseline_geometry(session: Path, *, width: int = 800, height: int = 600, generated_tick: int = 10) -> None:
    write_json(
        session / "interaction_geometry" / "live" / "live_baseline_state.json",
        {
            "schema": "live_baseline_state.v1",
            "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
            "latestTick": generated_tick,
            "gameState": "LOGGED_IN",
            "player": {"worldX": 3200, "worldY": 3201, "plane": 0},
            "sceneCache": {"presentObjectCount": 25},
            "inputGeometry": {
                "schema": "input_geometry.v1",
                "geometryAvailable": True,
                "reason": "available",
                "canvasScreenX": 1000,
                "canvasScreenY": 2000,
                "canvasWidth": width,
                "canvasHeight": height,
                "clientWindowX": 990,
                "clientWindowY": 1980,
                "clientWindowWidth": width + 20,
                "clientWindowHeight": height + 40,
                "isCanvasShowing": True,
                "isClientFocused": True,
            },
        },
    )


def target(name="Tree", key="tree-a", tick=10):
    return {
        "targetName": name,
        "name": name,
        "classId": "tree",
        "targetType": "sceneObject",
        "objectKey": key,
        "targetKey": key,
        "id": 1276,
        "worldX": 3200,
        "worldY": 3201,
        "plane": 0,
        "onScreen": True,
        "geometryAvailable": True,
        "aimPoint": {"canvasX": 100, "canvasY": 120},
        "tick": tick,
    }


def status_for(session: Path, marker=None, *, latest_tick=10, input_geometry=True):
    marker = marker if marker is not None else target(tick=latest_tick)
    status = {
        "sessionPath": str(session),
        "latestTick": latest_tick,
        "candidateCount": 1,
        "profileCandidateCount": 1,
        "broadCandidateCount": 1,
        "writeDebugLiveFiles": False,
        "noFileDaily": True,
        "overlayStateWritten": True,
        "inputGeometry": {
            "inputGeometryAvailable": input_geometry,
            "canvasScreenOrigin": {"x": 1000, "y": 2000},
            "canvasSize": {"width": 800, "height": 600},
        },
        "brain": {
            "latestTick": latest_tick,
            "freshnessDomains": {"targetCandidateFreshness": "fresh"},
            "genericTaskState": {
                "phase": "target_selected",
                "activeIntent": "select_target",
                "activeIntentTarget": marker,
                "blockingConditions": [],
            },
            "inventoryContext": {"inventoryFull": False, "freeSlots": 15},
            "bankUiContext": {"bankOpen": False, "bankReadable": False, "bankPinOpen": False},
            "intentOverlayContext": {"selectedMarker": marker},
        },
    }
    return status


def navigation_status_for(session: Path, *, resource_marker=None, latest_tick=10, input_geometry=True):
    resource_marker = resource_marker if resource_marker is not None else target(key="selected", tick=latest_tick)
    status = status_for(session, resource_marker, latest_tick=latest_tick, input_geometry=input_geometry)
    brain = status["brain"]
    brain["genericTaskState"] = {
        "phase": "needs_service",
        "activeIntent": "needs_service",
        "activeIntentTarget": resource_marker,
        "blockingConditions": [],
    }
    brain["inventoryContext"] = {"inventoryFull": True, "freeSlots": 0}
    brain["serviceContext"] = {"serviceNeeded": True, "serviceReady": False}
    brain["serviceRouteContext"] = {
        "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
        "currentStepIndex": 0,
        "currentStep": {"type": "navigate_world", "label": "Lumbridge Castle west stair approach"},
        "actionReady": False,
    }
    brain["pathingContext"] = {
        "pathingNeeded": True,
        "nextWaypointTarget": {
            "targetName": "Lumbridge Castle west stair approach",
            "targetType": "tile",
            "classId": "service_route_anchor",
            "targetTile": {"worldX": 3203, "worldY": 3238, "plane": 0},
            "aimPoint": {"canvasX": 280, "canvasY": 190},
        },
        "nextWaypointTile": {"worldX": 3203, "worldY": 3238, "plane": 0},
        "destinationTile": {"worldX": 3205, "worldY": 3229, "plane": 0},
    }
    return status


def dialogue_status_for(session: Path, *, latest_tick=10):
    status = navigation_status_for(session, latest_tick=latest_tick)
    brain = status["brain"]
    brain["serviceRouteContext"] = {
        "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
        "currentStepIndex": 4,
        "currentStep": {
            "type": "interact_object",
            "label": "second stairs up",
            "planeChange": "+1",
        },
    }
    brain["dialogueState"] = {
        "schema": "dialogue_state.v1",
        "active": True,
        "type": "options",
        "promptText": "Climb up or down the stairs?",
        "canUseNumberKeys": True,
        "options": [
            {"index": 1, "key": "1", "text": "Climb up the stairs."},
            {"index": 2, "key": "2", "text": "Climb down the stairs."},
        ],
    }
    status["dialogueState"] = brain["dialogueState"]
    return status


def enable_plugin_snapshot(
    status: dict,
    *,
    post_menu_age_ms=25,
    client_tick_age_ms=None,
    game_state="LOGGED_IN",
    source_event=None,
):
    status["inputSourceActive"] = "plugin-snapshot"
    status["pluginSnapshotHost"] = "127.0.0.1"
    status["pluginSnapshotPort"] = 8893
    hot = {
        "schema": "client_tick_hot.v1",
        "clientTick": 100,
        "gameTickAtSample": status.get("latestTick"),
        "gameState": game_state,
        "postMenuSort": {
            "topOption": "Walk here",
            "topTarget": "",
            "topType": "WALK",
            "mouseCanvasX": 280,
            "mouseCanvasY": 190,
            "gameState": game_state,
        },
        "latency": {"postMenuSortAgeMillis": post_menu_age_ms, "ageMillis": post_menu_age_ms if client_tick_age_ms is None else client_tick_age_ms},
    }
    if source_event:
        hot["sourceEvent"] = source_event
        hot["sampleSource"] = source_event
    status["clientTickHot"] = hot
    return status


class FakeBackend:
    name = "fake"

    def __init__(self):
        self.calls = []

    def current_position(self):
        return (0, 0)

    def move_and_click(self, plan, *, button="left"):
        self.calls.append(("move_and_click", plan.click_point.x, plan.click_point.y, button))


class LiveReadinessTest(unittest.TestCase):
    def test_readiness_passes_when_daemon_overlay_target_and_geometry_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})

            report = live_readiness.build_readiness_report(daemon_status=status_for(session, marker), sessions_dir=root)

            self.assertEqual(report["status"], "PASS")
            self.assertTrue(report["readinessPassed"])
            self.assertEqual(report["currentIntent"], "resource_object_action")
            self.assertEqual(report["actionReadiness"]["status"], "PASS")
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            self.assertTrue(report["selectedTargetChecks"]["inHighlighterSource"])

    def test_nested_post_menu_sort_hot_sample_overrides_stale_game_state_changed_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
            status = enable_plugin_snapshot(
                status_for(session, marker),
                post_menu_age_ms=100_000,
                game_state="LOGIN_SCREEN",
                source_event="GameStateChanged",
            )
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            hot = status["clientTickHot"]
            hot.pop("latency", None)
            hot["wallTimeMillis"] = now_ms - 100_000
            hot["postMenuSort"].update(
                {
                    "sourceEvent": "PostMenuSort",
                    "sampleSource": "PostMenuSort",
                    "gameState": "LOGGED_IN",
                    "wallTimeMillis": now_ms,
                }
            )

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertNotIn("client_tick_hot_stale", [item["code"] for item in report["blockers"]])
            self.assertEqual(report["clientTickHot"]["gameState"], "LOGGED_IN")
            self.assertEqual(report["clientTickHot"]["clientTickHotSource"], "PostMenuSort")
            self.assertTrue(report["clientTickHot"]["fresh"])

    def test_daemon_unavailable_is_fail(self):
        report = live_readiness.build_readiness_report(
            fetch_json_func=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("daemon down"))
        )

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["blockers"][0]["code"], "daemon_status_unavailable")

    def test_stale_client_tick_recommends_loaded_scene_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
            status = enable_plugin_snapshot(status_for(session, marker), post_menu_age_ms=5000)
            status["worldModelSummary"] = {
                "schema": "world_model_summary.v1",
                "metadata": {"gameState": "LOGGED_IN"},
                "objects": {"total": 0},
            }

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertTrue(report["livenessRecoveryAvailable"])
            self.assertTrue(report["livenessRecoveryRecommended"])
            self.assertEqual(report["livenessState"], "stale_logged_in_no_scene")
            self.assertEqual(report["loadedSceneProof"]["loadedSceneVerified"], False)
            self.assertTrue(report["knownRecoverableState"])
            self.assertTrue(report["actionReadiness"]["checks"]["livenessRecoveryRecommended"])

    def test_missing_debug_overlay_blocks_resource_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            write_json(session / "manifest.json", {"sessionId": "session"})

            report = live_readiness.build_readiness_report(daemon_status=status_for(session), sessions_dir=root)

            self.assertEqual(report["status"], "FAIL")
            self.assertIn("latest_live_session_missing", [item["code"] for item in report["blockers"]])

    def test_empty_highlighter_source_blocks_resource_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            marker["actionTargetSource"] = "overlay_marker"
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": []})

            report = live_readiness.build_readiness_report(daemon_status=status_for(session, marker), sessions_dir=root)

            self.assertEqual(report["status"], "FAIL")
            self.assertIn("highlighter_source_not_ready", [item["code"] for item in report["blockers"]])
            self.assertEqual(report["overlayHealth"]["markerCountZeroStatus"], "unexpected_collecting_needs_target")
            self.assertTrue(report["overlayHealth"]["overlayBlocksCurrentAction"])

    def test_marker_count_zero_expected_after_goal_complete_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": []})
            status = status_for(session, marker)
            status["brain"]["genericTaskState"] = {
                "phase": "goal_complete",
                "activeIntent": "none",
                "activeIntentTarget": None,
            }
            status["brain"]["goalProgress"] = {
                "goalCount": 5,
                "displayedGoalProgress": 5,
                "heldResourceCount": 13,
            }

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["status"], "PASS")
            self.assertTrue(report["readinessPassed"])
            self.assertEqual(report["proposedAction"], "none")
            self.assertFalse(report["actionNeed"]["actionReadinessNeeded"])
            self.assertTrue(report["actionNeed"]["goalComplete"])
            self.assertEqual(report["overlayHealth"]["markerCountZeroStatus"], "expected_goal_complete")
            self.assertFalse(report["overlayHealth"]["overlayBlocksCurrentAction"])

    def test_marker_count_zero_expected_while_waiting_for_result_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": []})
            status = status_for(session, marker)
            status["brain"]["genericTaskState"] = {
                "phase": "wait_for_result",
                "activeIntent": "wait_for_result",
                "activeIntentTarget": marker,
            }

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["status"], "PASS")
            self.assertTrue(report["readinessPassed"])
            self.assertFalse(report["actionNeed"]["actionReadinessNeeded"])
            self.assertTrue(report["actionNeed"]["waitingForResult"])
            self.assertEqual(report["overlayHealth"]["markerCountZeroStatus"], "expected_waiting_for_result")
            self.assertFalse(report["overlayHealth"]["overlayBlocksCurrentAction"])

    def test_marker_count_zero_is_warning_only_with_live_candidate_safety_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            marker["targetLiveState"] = "live_assumed"
            marker["directReachability"] = "reachable"
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": []})
            status = enable_plugin_snapshot(status_for(session, marker))

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["status"], "WARN")
            self.assertTrue(report["readinessPassed"])
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            self.assertEqual(report["actionSafetyEvidence"]["proposalActionTargetSource"], "live_resource_candidate")
            self.assertTrue(report["actionSafetyEvidence"]["canUseLiveTargetWithoutOverlayMarker"])
            self.assertEqual(report["overlayHealth"]["markerCountZeroStatus"], "unexpected_collecting_needs_target")
            self.assertTrue(report["overlayHealth"]["overlayWarningOnly"])
            self.assertFalse(report["overlayHealth"]["overlayBlocksCurrentAction"])
            self.assertIn("highlighter.markers", report["optionalCapabilities"])

    def test_hover_guarded_live_candidate_ignores_stale_file_candidate_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target(tick=10)
            marker["targetLiveState"] = "live_assumed"
            marker["directReachability"] = "reachable"
            marker["safeAimPoint"] = {"status": "PASS", "actionable": True, "canvasX": 100, "canvasY": 120}
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": []})
            status = enable_plugin_snapshot(status_for(session, marker, latest_tick=100))
            status["brain"]["contextActionProposal"] = {
                "schema": "action_proposal.v1",
                "status": "PASS",
                "proposedAction": "select_resource_target",
                "targetKind": "resource",
                "targetName": "Tree",
                "targetTile": {"worldX": 3200, "worldY": 3201, "plane": 0},
                "suggestedClickPoint": {"x": 100, "y": 120},
                "targetExplanation": {
                    "name": "Tree",
                    "classId": "tree",
                    "safeAimPoint": {"status": "PASS", "actionable": True, "canvasX": 100, "canvasY": 120},
                    "actionTargetSource": "live_resource_candidate",
                    "actionability": "needs_hover_confirmation",
                },
                "actionTargetSource": "live_resource_candidate",
                "actionability": "needs_hover_confirmation",
                "sourceTick": 100,
                "confidence": 0.9,
            }

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)
            blocker_codes = [item["code"] for item in report["blockers"]]

            self.assertEqual(report["status"], "WARN")
            self.assertTrue(report["readinessPassed"])
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            self.assertNotIn("candidate_data_stale", blocker_codes)
            self.assertTrue(report["actionSafetyEvidence"]["hoverGuardedLiveTarget"])
            self.assertTrue(report["actionSafetyEvidence"]["canUseLiveTargetWithoutOverlayMarker"])
            self.assertIn("target.freshness", report["optionalCapabilities"])

    def test_plugin_snapshot_projection_candidate_supplies_aim_and_freshness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            stale_selected = {"targetType": "none", "tick": 10, "sourceTick": 10}
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": []})
            status = enable_plugin_snapshot(status_for(session, stale_selected, latest_tick=100))
            status["brain"]["contextActionProposal"] = {
                "schema": "action_proposal.v1",
                "status": "PASS",
                "proposedAction": "select_resource_target",
                "targetKind": "resource",
                "targetName": "Tree",
                "targetTile": {"worldX": 3200, "worldY": 3201, "plane": 0},
                "suggestedClickPoint": {"x": 100, "y": 120},
                "targetExplanation": {
                    "name": "Tree",
                    "classId": "tree",
                    "onScreen": True,
                    "geometryAvailable": True,
                    "safeAimPoint": {"status": "PASS", "actionable": True, "canvasX": 100, "canvasY": 120},
                    "actionTargetSource": "plugin_snapshot_projection",
                    "actionability": "needs_hover_confirmation",
                },
                "actionTargetSource": "plugin_snapshot_projection",
                "actionability": "needs_hover_confirmation",
                "sourceTick": 100,
                "confidence": 0.9,
            }

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)
            blocker_codes = [item["code"] for item in report["blockers"]]

            self.assertEqual(report["status"], "WARN")
            self.assertTrue(report["readinessPassed"])
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            self.assertNotIn("selected_target_aim_missing", blocker_codes)
            self.assertNotIn("candidate_data_stale", blocker_codes)
            self.assertTrue(report["actionSafetyEvidence"]["canUseLiveTargetWithoutOverlayMarker"])

    def test_plugin_snapshot_projection_can_hover_confirm_with_stale_hot_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            stale_selected = {"targetType": "none", "tick": 10, "sourceTick": 10}
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": []})
            status = enable_plugin_snapshot(
                status_for(session, stale_selected, latest_tick=100),
                post_menu_age_ms=60_000,
            )
            status["brain"]["contextActionProposal"] = {
                "schema": "action_proposal.v1",
                "status": "PASS",
                "proposedAction": "select_resource_target",
                "targetKind": "resource",
                "targetName": "Tree",
                "targetTile": {"worldX": 3200, "worldY": 3201, "plane": 0},
                "suggestedClickPoint": {"x": 100, "y": 120},
                "targetExplanation": {
                    "name": "Tree",
                    "classId": "tree",
                    "onScreen": True,
                    "geometryAvailable": True,
                    "safeAimPoint": {"status": "PASS", "actionable": True, "canvasX": 100, "canvasY": 120},
                    "actionTargetSource": "plugin_snapshot_projection",
                    "actionability": "needs_hover_confirmation",
                },
                "actionTargetSource": "plugin_snapshot_projection",
                "actionability": "needs_hover_confirmation",
                "sourceTick": 100,
                "confidence": 0.9,
            }

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)
            blocker_codes = [item["code"] for item in report["blockers"]]

            self.assertEqual(report["status"], "WARN")
            self.assertTrue(report["readinessPassed"])
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            self.assertFalse(report["clientTickHot"]["fresh"])
            self.assertNotIn("client_tick_hot_stale", blocker_codes)
            self.assertNotIn("selected_target_aim_missing", blocker_codes)
            self.assertNotIn("candidate_data_stale", blocker_codes)
            self.assertTrue(report["actionSafetyEvidence"]["hoverGuardedLiveTarget"])
            self.assertTrue(report["actionSafetyEvidence"]["canUseLiveTargetWithoutOverlayMarker"])
            self.assertIn("client_tick_hot.fresh", report["optionalCapabilities"])

    def test_service_context_policy_is_not_immediate_service_need_with_free_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            marker["targetLiveState"] = "live_assumed"
            marker["directReachability"] = "reachable"
            marker["safeAimPoint"] = {"status": "PASS", "actionable": True, "canvasX": 100, "canvasY": 120}
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
            status = enable_plugin_snapshot(status_for(session, marker))
            status["brain"]["serviceContext"] = {"serviceNeeded": True, "serviceRequired": True, "serviceReady": False}
            status["serviceNeeded"] = True
            status["inventoryFreeSlots"] = 2

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["status"], "PASS")
            self.assertFalse(report["actionNeed"]["needsService"])
            self.assertTrue(report["actionNeed"]["serviceContextRequired"])
            self.assertTrue(report["actionNeed"]["needsNextTarget"])

    def test_held_resources_at_actionable_service_target_are_immediate_service_need(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": []})
            status = enable_plugin_snapshot(status_for(session))
            service_target = {
                "targetName": "Bank Deposit Box",
                "name": "Bank Deposit Box",
                "classId": "bank_related",
                "targetType": "sceneObject",
                "source": "live_route_object",
                "worldX": 3210,
                "worldY": 3217,
                "plane": 2,
                "bounds": {"x": 293, "y": 92, "width": 67, "height": 133},
                "aimPoint": {"canvasX": 326, "canvasY": 158},
                "safeAimPoint": {
                    "status": "PASS",
                    "canvasX": 326,
                    "canvasY": 158,
                    "distanceToViewportEdgePx": 100,
                    "rawCenterInsideViewport": True,
                },
                "expectedOptions": ["Bank", "Use", "Deposit"],
                "projectionStatus": {"actionableByCanvas": True, "visible": True, "visibleAreaRatio": 1.0},
            }
            brain = status["brain"]
            brain["genericTaskState"] = {
                "phase": "needs_more_context",
                "activeIntent": "observe",
                "activeIntentTarget": None,
                "blockingConditions": [],
                "goalProgress": {"heldResourceCount": 7},
            }
            brain["inventoryContext"] = {"inventoryFull": False, "freeSlots": 8}
            brain["serviceContext"] = {
                "serviceNeeded": True,
                "serviceRequired": True,
                "serviceReady": False,
                "serviceRouteContext": {
                    "schema": "service_route_context.v1",
                    "routeStepStatus": "service_target_actionable",
                    "actionReady": True,
                    "currentStep": {
                        "type": "service_interact",
                        "expectedOptions": ["Bank", "Use", "Deposit"],
                        "expectedTargetContains": ["Bank", "Deposit"],
                    },
                    "visibleServiceTarget": service_target,
                },
            }
            brain["bankOperationContext"] = {"operationNeeded": False, "bankingComplete": False, "resourceItemsHeld": None}
            brain["intentOverlayContext"] = {"selectedMarker": None}
            status["serviceNeeded"] = True
            status["serviceRouteActionReady"] = True
            status["inventoryFreeSlots"] = 8

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["proposedAction"], "open_service")
            self.assertTrue(report["actionNeed"]["needsService"])
            self.assertEqual(report["actionNeed"]["resourceCount"], 7)
            self.assertTrue(report["actionReadiness"]["executionAllowed"])

    def test_overlay_marker_source_is_required_when_target_source_is_overlay_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            marker["markerType"] = "selected_target"
            marker["source"] = "brain"
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": []})
            status = enable_plugin_snapshot(status_for(session, marker))

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["actionSafetyEvidence"]["proposalActionTargetSource"], "overlay_marker")
            self.assertTrue(report["overlayHealth"]["overlaySourceRequiredForCurrentAction"])
            self.assertTrue(report["overlayHealth"]["overlayBlocksCurrentAction"])

    def test_plugin_snapshot_request_failure_blocks_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
            status = status_for(session, marker)
            status["inputSourceActive"] = "plugin-snapshot"
            status["pluginSnapshotHost"] = "127.0.0.1"
            status["pluginSnapshotPort"] = 8893
            status["warnings"] = ["plugin snapshot request failed: URLError: timed out"]

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["status"], "FAIL")
            blockers = {item["code"]: item for item in report["blockers"]}
            self.assertIn("plugin_snapshot_source_not_ready", blockers)
            self.assertEqual(blockers["plugin_snapshot_source_not_ready"]["sourceUrl"], "http://127.0.0.1:8893/snapshot")
            self.assertIn("plugin.snapshot", report["requiredCapabilities"])
            self.assertEqual(report["capabilities"]["pluginSnapshot"]["url"], "http://127.0.0.1:8893/snapshot")
            self.assertTrue(report["capabilities"]["pluginSnapshot"]["required"])
            self.assertFalse(report["capabilities"]["pluginSnapshot"]["available"])

    def test_plugin_snapshot_request_failure_warns_for_fresh_live_navigation_waypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
            status = enable_plugin_snapshot(navigation_status_for(session, resource_marker=marker), post_menu_age_ms=25)
            status["pluginSnapshotAvailable"] = False
            status["warnings"] = ["plugin snapshot request failed: TimeoutError: timed out"]

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["currentIntent"], "navigation_waypoint_action")
            self.assertEqual(report["actionReadiness"]["status"], "WARN")
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            self.assertNotIn("plugin_snapshot_source_not_ready", [item["code"] for item in report["blockers"]])
            self.assertNotIn("plugin.snapshot", report["requiredCapabilities"])
            self.assertIn("plugin.snapshot", report["optionalCapabilities"])
            self.assertFalse(report["capabilities"]["pluginSnapshot"]["required"])
            self.assertTrue(any("fresh live navigation waypoint" in warning for warning in report["actionReadiness"]["warnings"]))

    def test_fresh_daemon_suppresses_historical_plugin_snapshot_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
            status = enable_plugin_snapshot(status_for(session, marker), post_menu_age_ms=25)
            status["warnings"] = ["plugin snapshot request failed: TimeoutError: timed out"]
            status["liveProcessorFreshness"] = {
                "candidateTick": 10,
                "latestTick": 10,
                "freshByTicks": True,
                "freshByMillis": True,
            }
            status["sourceMetadata"] = {
                "sourceUsed": "live_daemon",
                "daemonUrl": "http://127.0.0.1:8890",
                "snapshotUrl": "http://127.0.0.1:8893/snapshot",
                "contextSource": "live_daemon",
                "fileSessionFallbackUsed": False,
                "freshnessSource": "daemon_status+plugin_snapshot",
            }

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["status"], "WARN")
            self.assertTrue(report["readinessPassed"])
            self.assertTrue(report["pluginSnapshotFresh"])
            self.assertNotIn("plugin_snapshot_source_not_ready", [item["code"] for item in report["blockers"]])
            self.assertTrue(report["capabilities"]["pluginSnapshot"]["warningSuppressed"])
            self.assertEqual(report["sourceUsed"], "live_daemon")
            self.assertEqual(report["contextSource"], "live_daemon")
            self.assertFalse(report["fileSessionFallbackUsed"])
            self.assertEqual(report["freshnessSource"], "daemon_status+plugin_snapshot")

    def test_explicit_fresh_client_tick_hot_status_prevents_unavailable_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
            status = status_for(session, marker)
            status["inputSourceActive"] = "plugin-snapshot"
            status["clientTickHotFresh"] = True
            status["clientTickHotAvailable"] = True
            status["gameState"] = "LOGGED_IN"

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["actionReadiness"]["status"], "PASS")
            self.assertTrue(report["actionReadiness"]["checks"]["clientTickHotFresh"])
            self.assertNotIn("client_tick_hot_unavailable", [item["code"] for item in report["blockers"]])

    def test_plugin_snapshot_warning_is_optional_when_not_active_input_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
            status = status_for(session, marker)
            status["inputSourceActive"] = "compact-packets"
            status["warnings"] = ["plugin snapshot request failed: URLError: timed out"]

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["status"], "PASS")
            self.assertNotIn("plugin.snapshot", report["requiredCapabilities"])
            self.assertIn("plugin.snapshot", report["optionalCapabilities"])
            self.assertFalse(report["capabilities"]["pluginSnapshot"]["required"])

    def test_latest_live_session_mismatch_blocks_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            daemon_session = root / "daemon"
            newer_session = root / "newer"
            marker = target()
            write_json(daemon_session / "manifest.json", {"sessionId": "daemon"})
            write_json(newer_session / "manifest.json", {"sessionId": "newer"})
            write_json(daemon_session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
            newer_overlay = newer_session / "interaction_geometry" / "live" / "overlay_debug_state.json"
            write_json(newer_overlay, {"markers": [marker]})
            os.utime(newer_overlay, (time_value := 4102444800, time_value))

            report = live_readiness.build_readiness_report(daemon_status=status_for(daemon_session, marker), sessions_dir=root)

            self.assertEqual(report["status"], "FAIL")
            self.assertIn("daemon_latest_live_session_mismatch", [item["code"] for item in report["blockers"]])

    def test_selected_target_must_match_highlighter_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            selected = target(key="selected")
            selected["actionTargetSource"] = "overlay_marker"
            other = target(key="other")
            other["worldX"] = 3300
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [other]})

            report = live_readiness.build_readiness_report(daemon_status=status_for(session, selected), sessions_dir=root)

            self.assertEqual(report["status"], "FAIL")
            self.assertIn("selected_target_not_in_highlighter_source", [item["code"] for item in report["blockers"]])
            self.assertEqual(report["currentIntent"], "resource_object_action")
            self.assertEqual(report["actionReadiness"]["status"], "FAIL")
            self.assertFalse(report["actionReadiness"]["executionAllowed"])

    def test_post_service_reacquired_resource_uses_proposal_target_for_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            return_marker = {
                "markerType": "selected_target",
                "targetName": "Lumbridge Castle west approach return",
                "name": "Lumbridge Castle west approach return",
                "classId": "resource_return",
                "targetType": "tile",
                "worldX": 3203,
                "worldY": 3238,
                "plane": 0,
                "aimPoint": {"canvasX": 330, "canvasY": 90},
            }
            tree = target(key="tree-after-return", tick=10)
            tree["worldX"] = 3196
            tree["worldY"] = 3248
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [return_marker]})
            status = status_for(session, return_marker)
            status["brain"]["genericTaskState"] = {
                "phase": "return_to_resource",
                "activeIntent": "return_to_resource_area",
                "activeIntentTarget": return_marker,
                "blockingConditions": [],
            }
            status["brain"]["bankOperationContext"] = {"bankingComplete": True, "resourceItemsHeld": 0}
            status["brain"]["resourceReturnContext"] = {
                "returnDestinationAvailable": True,
                "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "resourceTargetCurrentlyVisible": True,
            }
            status["brain"]["returnRouteContext"] = {
                "returnRouteId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                "state": "return_route_ready",
                "currentNavigationTarget": {"worldX": 3203, "worldY": 3238, "plane": 0},
            }
            status["returnBestResourceTarget"] = tree
            status["postBankResourceTargetAvailable"] = True
            enable_plugin_snapshot(status)

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["proposedAction"], "select_resource_target")
            self.assertEqual(report["currentIntent"], "resource_object_action")
            self.assertEqual(report["actionReadiness"]["status"], "PASS")
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            self.assertEqual(report["selectedTarget"]["name"], "Tree")
            self.assertTrue(report["selectedTargetChecks"]["inHighlighterSource"])
            self.assertFalse(any("selected daemon target is not present in highlighter" in warning for warning in report["warnings"]))

    def test_navigation_intent_does_not_require_resource_target_in_highlighter_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            selected = target(key="selected")
            selected["actionTargetSource"] = "overlay_marker"
            other = target(key="other")
            other["worldX"] = 3300
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [other]})

            report = live_readiness.build_readiness_report(
                daemon_status=navigation_status_for(session, resource_marker=selected),
                sessions_dir=root,
            )

            self.assertEqual(report["status"], "WARN")
            self.assertTrue(report["readinessPassed"])
            self.assertEqual(report["currentIntent"], "navigation_waypoint_action")
            self.assertEqual(report["actionReadiness"]["status"], "PASS")
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            self.assertNotIn("selected_target_not_in_highlighter_source", [item["code"] for item in report["actionReadiness"]["blockers"]])
            self.assertIn("target.highlighterMatch", report["actionReadiness"]["checksSkippedAsNotApplicable"])
            self.assertFalse(report["selectedResourceTargetFreshnessApplicable"])
            self.assertTrue(any("selected daemon target is not present in highlighter marker source" in warning for warning in report["nonApplicableContextWarnings"]))
            self.assertFalse(any("selected daemon target is not present in highlighter marker source" in warning for warning in report["applicableWarnings"]))

    def test_navigation_intent_marks_stale_resource_target_as_non_applicable_context_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            selected = target(key="selected")
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [selected]})
            status = navigation_status_for(session, resource_marker=selected, latest_tick=20)
            status["brain"]["freshnessDomains"] = {"targetCandidateFreshness": "stale"}

            report = live_readiness.build_readiness_report(
                daemon_status=status,
                sessions_dir=root,
            )

            self.assertEqual(report["currentIntent"], "navigation_waypoint_action")
            self.assertEqual(report["actionReadiness"]["status"], "PASS")
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            self.assertFalse(report["selectedResourceTargetFreshnessApplicable"])
            self.assertEqual(report["selectedResourceTargetFreshnessStatus"], "stale")
            self.assertIn("target.candidateFreshness", report["actionReadiness"]["checksSkippedAsNotApplicable"])
            self.assertTrue(any("not applicable while current intent is navigation_waypoint_action" in warning for warning in report["nonApplicableContextWarnings"]))

    def test_live_navigation_waypoint_allows_newer_live_session_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            daemon_session = root / "daemon"
            newer_session = root / "newer"
            selected = target(key="selected")
            write_json(daemon_session / "manifest.json", {"sessionId": "daemon"})
            write_json(newer_session / "manifest.json", {"sessionId": "newer"})
            write_json(daemon_session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [selected]})
            newer_overlay = newer_session / "interaction_geometry" / "live" / "overlay_debug_state.json"
            write_json(newer_overlay, {"markers": []})
            os.utime(newer_overlay, (time_value := 4102444800, time_value))
            status = enable_plugin_snapshot(
                navigation_status_for(daemon_session, resource_marker=selected, latest_tick=20),
                post_menu_age_ms=25,
            )

            report = live_readiness.build_readiness_report(
                daemon_status=status,
                sessions_dir=root,
            )

            self.assertEqual(report["currentIntent"], "navigation_waypoint_action")
            self.assertEqual(report["actionReadiness"]["status"], "WARN")
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            self.assertNotIn("daemon_latest_live_session_mismatch", [item["code"] for item in report["blockers"]])
            self.assertNotIn("daemon_latest_session_mismatch", [item["code"] for item in report["blockers"]])
            self.assertIn("session.match", report["optionalCapabilities"])
            self.assertTrue(any("fresh live navigation waypoint" in warning for warning in report["actionReadiness"]["warnings"]))

    def test_plugin_snapshot_route_transition_allows_session_mismatch_and_geometry_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            daemon_session = root / "daemon"
            newer_session = root / "newer"
            selected = target(key="selected")
            write_json(daemon_session / "manifest.json", {"sessionId": "daemon"})
            write_json(newer_session / "manifest.json", {"sessionId": "newer"})
            write_json(daemon_session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": []})
            newer_overlay = newer_session / "interaction_geometry" / "live" / "overlay_debug_state.json"
            write_json(newer_overlay, {"markers": []})
            os.utime(newer_overlay, (time_value := 4102444800, time_value))
            status = enable_plugin_snapshot(
                navigation_status_for(daemon_session, resource_marker=selected, latest_tick=20, input_geometry=False),
                post_menu_age_ms=60_000,
            )
            route_target = {
                "targetName": "Ladder",
                "name": "Ladder",
                "classId": "service_route_transition",
                "targetType": "sceneObject",
                "id": 16683,
                "worldX": 3204,
                "worldY": 3238,
                "plane": 0,
                "onScreen": True,
                "geometryAvailable": True,
                "aimPoint": {"canvasX": 498, "canvasY": 122, "source": "clickboxBoundsCenter"},
                "actions": ["Climb-up"],
                "actionTargetSource": "plugin_snapshot_projection",
                "actionability": "needs_hover_confirmation",
            }
            status["brain"]["serviceRouteContext"].update(
                {
                    "currentStep": {"type": "interact_object", "label": "Ladder", "expectedOptions": ["Climb-up"]},
                    "routeStepStatus": "plugin_snapshot_route_transition_visible",
                    "actionReady": True,
                    "visibleInteractionTarget": route_target,
                }
            )

            report = live_readiness.build_readiness_report(
                daemon_status=status,
                sessions_dir=root,
            )

            self.assertEqual(report["currentIntent"], "route_transition_action")
            self.assertEqual(report["proposedAction"], "interact_service_route_object")
            self.assertEqual(report["actionReadiness"]["status"], "WARN")
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            blocker_codes = [item["code"] for item in report["blockers"]]
            self.assertNotIn("daemon_latest_live_session_mismatch", blocker_codes)
            self.assertNotIn("daemon_latest_session_mismatch", blocker_codes)
            self.assertNotIn("input_geometry_unavailable", blocker_codes)
            self.assertIn("input.geometry", report["optionalCapabilities"])
            self.assertIn("client_tick_hot.fresh", report["optionalCapabilities"])
            self.assertTrue(any("route transition target" in warning for warning in report["actionReadiness"]["warnings"]))

    def test_live_route_object_transition_allows_session_mismatch_warning_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            daemon_session = root / "daemon"
            newer_session = root / "newer"
            selected = target(key="selected")
            write_json(daemon_session / "manifest.json", {"sessionId": "daemon"})
            write_json(newer_session / "manifest.json", {"sessionId": "newer"})
            write_json(daemon_session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": []})
            newer_overlay = newer_session / "interaction_geometry" / "live" / "overlay_debug_state.json"
            write_json(newer_overlay, {"markers": []})
            os.utime(newer_overlay, (time_value := 4102444800, time_value))
            status = enable_plugin_snapshot(
                navigation_status_for(daemon_session, resource_marker=selected, latest_tick=20, input_geometry=True),
                post_menu_age_ms=25,
            )
            route_target = {
                "targetName": "Staircase",
                "name": "Staircase",
                "classId": "route_transition",
                "targetType": "sceneObject",
                "id": 56230,
                "worldX": 3204,
                "worldY": 3229,
                "plane": 0,
                "onScreen": True,
                "geometryAvailable": True,
                "aimPoint": {"canvasX": 339, "canvasY": 53, "source": "canvasLocation"},
                "safeAimPoint": {"status": "PASS", "canvasX": 339, "canvasY": 53},
                "actions": ["Climb-up", "Top-floor"],
                "expectedOptions": ["Climb-up", "Climb up", "Top-floor"],
                "actionTargetSource": "live_route_object",
                "actionability": "needs_hover_confirmation",
            }
            status["brain"]["serviceRouteContext"].update(
                {
                    "currentStep": {"type": "interact_object", "label": "Staircase", "expectedOptions": ["Climb-up"]},
                    "routeStepStatus": "route_interaction_visible",
                    "actionReady": True,
                    "visibleInteractionTarget": route_target,
                }
            )

            report = live_readiness.build_readiness_report(
                daemon_status=status,
                sessions_dir=root,
            )

            self.assertEqual(report["currentIntent"], "route_transition_action")
            self.assertEqual(report["proposedAction"], "interact_service_route_object")
            self.assertEqual(report["actionReadiness"]["status"], "WARN")
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            blocker_codes = [item["code"] for item in report["blockers"]]
            self.assertNotIn("daemon_latest_live_session_mismatch", blocker_codes)
            self.assertNotIn("daemon_latest_session_mismatch", blocker_codes)
            self.assertIn("session.match", report["optionalCapabilities"])

    def test_resource_intent_treats_stale_resource_target_as_applicable_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target(tick=10)
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
            status = status_for(session, marker, latest_tick=20)
            status["brain"]["freshnessDomains"] = {"targetCandidateFreshness": "stale"}

            report = live_readiness.build_readiness_report(
                daemon_status=status,
                sessions_dir=root,
                proposed_action="select_resource_target",
            )

            self.assertEqual(report["currentIntent"], "resource_object_action")
            self.assertTrue(report["selectedResourceTargetFreshnessApplicable"])
            self.assertEqual(report["selectedResourceTargetFreshnessStatus"], "stale")
            self.assertEqual(report["actionReadiness"]["status"], "FAIL")
            self.assertFalse(report["actionReadiness"]["executionAllowed"])
            self.assertIn("candidate_data_stale", [item["code"] for item in report["blockers"]])

    def test_stale_latest_file_session_is_reported_separately_from_fresh_daemon_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            daemon_session = root / "daemon"
            newer_session = root / "newer"
            selected = target(key="selected")
            write_json(daemon_session / "manifest.json", {"sessionId": "daemon"})
            write_json(newer_session / "manifest.json", {"sessionId": "newer"})
            write_json(daemon_session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [selected]})
            os.utime(newer_session / "manifest.json", (time_value := 4102444800, time_value))

            report = live_readiness.build_readiness_report(
                daemon_status=navigation_status_for(daemon_session, resource_marker=selected),
                sessions_dir=root,
            )

            self.assertTrue(report["staleFileSessionContext"])
            self.assertTrue(report["daemonSessionFresh"])
            self.assertTrue(any("latest file session differs" in warning for warning in report["nonApplicableContextWarnings"]))
            self.assertNotIn("daemon_latest_session_mismatch", [item["code"] for item in report["blockers"]])

    def test_static_route_prior_blocks_navigation_execution_until_live_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            write_json(session / "manifest.json", {"sessionId": "session"})
            status = status_for(session)
            brain = status["brain"]
            brain["genericTaskState"] = {
                "phase": "return_to_resource",
                "activeIntent": "return_to_resource_area",
                "activeIntentTarget": {},
                "blockingConditions": [],
            }
            brain["inventoryContext"] = {"inventoryFull": False, "freeSlots": 28}
            brain["bankOperationContext"] = {"operationNeeded": False, "bankingComplete": True, "resourceItemsHeld": 0}
            brain["resourceReturnContext"] = {
                "returnDestinationAvailable": True,
                "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "resourceTargetCurrentlyVisible": False,
            }
            brain["returnRouteContext"] = {
                "schema": "return_route_context.v1",
                "returnRouteId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                "state": "return_route_ready",
                "currentNavigationTarget": {
                    "targetName": "Lumbridge Castle west approach return",
                    "classId": "resource_return",
                    "targetType": "tile",
                    "worldX": 3203,
                    "worldY": 3238,
                    "plane": 0,
                    "source": "static_route_prior",
                },
            }
            brain["pathingContext"] = {}

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["currentIntent"], "navigation_waypoint_action")
            self.assertEqual(report["actionReadiness"]["status"], "FAIL")
            self.assertFalse(report["actionReadiness"]["executionAllowed"])
            self.assertIn("static_target_not_executable", [item["code"] for item in report["blockers"]])
            self.assertEqual(report["actionReadiness"]["checks"]["proposalActionTargetSource"], "static_route_prior")
            self.assertEqual(report["actionReadiness"]["checks"]["proposalActionability"], "advisory_only")

    def test_navigation_intent_requires_fresh_client_tick_hot_when_plugin_snapshot_is_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            selected = target(key="selected")
            other = target(key="other")
            other["worldX"] = 3300
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [other]})

            stale_status = enable_plugin_snapshot(
                navigation_status_for(session, resource_marker=selected),
                post_menu_age_ms=65_000,
            )
            stale_report = live_readiness.build_readiness_report(
                daemon_status=stale_status,
                sessions_dir=root,
            )

            self.assertEqual(stale_report["currentIntent"], "navigation_waypoint_action")
            self.assertEqual(stale_report["actionReadiness"]["status"], "FAIL")
            self.assertFalse(stale_report["actionReadiness"]["executionAllowed"])
            self.assertIn("client_tick_hot_stale", [item["code"] for item in stale_report["blockers"]])
            self.assertIn("client_tick_hot.fresh", stale_report["missingCapabilities"])
            self.assertTrue(stale_report["capabilities"]["clientTickHot"]["required"])
            self.assertFalse(stale_report["capabilities"]["clientTickHot"]["fresh"])

            fresh_status = enable_plugin_snapshot(
                navigation_status_for(session, resource_marker=selected),
                post_menu_age_ms=25,
            )
            fresh_report = live_readiness.build_readiness_report(
                daemon_status=fresh_status,
                sessions_dir=root,
            )

            self.assertEqual(fresh_report["actionReadiness"]["status"], "PASS")
            self.assertTrue(fresh_report["actionReadiness"]["executionAllowed"])
            self.assertTrue(fresh_report["capabilities"]["clientTickHot"]["fresh"])

    def test_stale_static_post_menu_does_not_fail_when_client_tick_is_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            selected = target(key="selected")
            other = target(key="other")
            other["worldX"] = 3300
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [other]})

            status = enable_plugin_snapshot(
                navigation_status_for(session, resource_marker=selected),
                post_menu_age_ms=100_000,
                client_tick_age_ms=25,
            )
            report = live_readiness.build_readiness_report(
                daemon_status=status,
                sessions_dir=root,
            )

            self.assertEqual(report["currentIntent"], "navigation_waypoint_action")
            self.assertEqual(report["actionReadiness"]["status"], "PASS")
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            self.assertTrue(report["capabilities"]["clientTickHot"]["fresh"])
            self.assertEqual(report["capabilities"]["clientTickHot"]["latestPostMenuSortAgeMillis"], 100_000)

    def test_game_state_changed_hot_sample_does_not_allow_action_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            selected = target(key="selected")
            other = target(key="other")
            other["worldX"] = 3300
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [other]})

            status = enable_plugin_snapshot(
                navigation_status_for(session, resource_marker=selected),
                post_menu_age_ms=100_000,
                client_tick_age_ms=25,
                game_state="LOGGED_IN",
                source_event="GameStateChanged",
            )
            report = live_readiness.build_readiness_report(
                daemon_status=status,
                sessions_dir=root,
            )

            self.assertEqual(report["currentIntent"], "navigation_waypoint_action")
            self.assertEqual(report["actionReadiness"]["status"], "FAIL")
            self.assertFalse(report["actionReadiness"]["executionAllowed"])
            self.assertIn("client_tick_hot_stale", [item["code"] for item in report["blockers"]])
            self.assertEqual(report["clientTickHot"]["gameState"], "LOGGED_IN")
            self.assertTrue(report["clientTickHot"]["livenessGameStateFresh"])
            self.assertFalse(report["clientTickHot"]["actionHotFresh"])
            self.assertFalse(report["clientTickHot"]["clientTickHotUsableForAction"])
            self.assertEqual(report["clientTickHot"]["clientTickHotSource"], "GameStateChanged")
            self.assertEqual(report["clientTickHot"]["staleReason"], "game_state_changed_only")
            self.assertFalse(report["actionReadiness"]["checks"]["clientTickHotFresh"])
            self.assertTrue(report["actionReadiness"]["checks"]["livenessGameStateFresh"])
            self.assertFalse(report["actionReadiness"]["checks"]["actionHotFresh"])
            self.assertFalse(report["actionReadiness"]["checks"]["clientTickHotUsableForAction"])
            self.assertEqual(report["capabilities"]["clientTickHot"]["sourceEvent"], "GameStateChanged")

    def test_dialogue_choice_readiness_passes_when_expected_option_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            write_json(session / "manifest.json", {"sessionId": "session"})

            report = live_readiness.build_readiness_report(
                daemon_status=dialogue_status_for(session),
                sessions_dir=root,
            )

            self.assertEqual(report["currentIntent"], "interface_dialogue_choice_action")
            self.assertEqual(report["actionReadiness"]["status"], "PASS")
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            self.assertIn("dialogue_state", report["requiredCapabilities"])

    def test_dialogue_choice_readiness_fails_when_option_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            write_json(session / "manifest.json", {"sessionId": "session"})
            status = dialogue_status_for(session)
            status["brain"]["dialogueState"]["options"] = [{"index": 1, "key": "1", "text": "Climb down the stairs."}]
            status["dialogueState"] = status["brain"]["dialogueState"]

            report = live_readiness.build_readiness_report(
                daemon_status=status,
                sessions_dir=root,
            )

            self.assertEqual(report["currentIntent"], "interface_dialogue_choice_action")
            self.assertEqual(report["actionReadiness"]["status"], "FAIL")
            self.assertFalse(report["actionReadiness"]["executionAllowed"])
            self.assertIn("dialogue_state.expected_option", report["missingCapabilities"])

    def test_stale_client_tick_hot_on_login_screen_explains_recovery_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            selected = target(key="selected")
            other = target(key="other")
            other["worldX"] = 3300
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [other]})

            stale_status = enable_plugin_snapshot(
                navigation_status_for(session, resource_marker=selected),
                post_menu_age_ms=323_000,
                game_state="LOGIN_SCREEN",
            )
            report = live_readiness.build_readiness_report(
                daemon_status=stale_status,
                sessions_dir=root,
            )

            self.assertEqual(report["actionReadiness"]["status"], "FAIL")
            blockers = {item["code"]: item for item in report["blockers"]}
            self.assertIn("client_tick_hot_stale", blockers)
            self.assertEqual(blockers["client_tick_hot_stale"]["staleReason"], "login_screen")
            self.assertEqual(blockers["client_tick_hot_stale"]["gameState"], "LOGIN_SCREEN")
            self.assertIn("bootstrap", blockers["client_tick_hot_stale"]["recovery"])
            self.assertEqual(report["clientTickHot"]["staleReason"], "login_screen")
            self.assertFalse(report["clientTickHot"]["isLoggedIn"])
            self.assertEqual(report["capabilities"]["clientTickHot"]["staleReason"], "login_screen")

    def test_selected_target_without_safe_aimpoint_requires_resource_view_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            marker.pop("aimPoint", None)
            marker["geometryAvailable"] = False
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
            status = status_for(session, marker)
            status["compactLiveGeometryCapHit"] = True

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["status"], "WARN")
            self.assertEqual(report["proposedAction"], "resource_view_recovery")
            self.assertEqual(report["currentIntent"], "resource_view_recovery_action")
            self.assertEqual(report["actionReadiness"]["status"], "PASS")
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            self.assertIn("target.safeAimPoint", report["actionReadiness"]["checksSkippedAsNotApplicable"])
            self.assertFalse(report["selectedTargetChecks"]["actionable"])
            self.assertTrue(report["actionExecution"]["allowed"])

    def test_resource_projection_recovery_allows_non_click_action_when_safe_aimpoint_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            marker["aimPoint"] = {"canvasX": 2147483647.5, "canvasY": 2147483647.5, "source": "live_object_pending"}
            marker["bounds"] = {"x": 2147483647, "y": 2147483647, "width": 1, "height": 1}
            marker["projectionMode"] = "live_object_pending"
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
            status = enable_plugin_snapshot(status_for(session, marker))

            report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertEqual(report["currentIntent"], "resource_view_recovery_action")
            self.assertEqual(report["actionReadiness"]["status"], "PASS")
            self.assertTrue(report["actionReadiness"]["executionAllowed"])
            self.assertEqual(report["actionExecution"]["proposalReason"], "resource_projection_recovery_needed")
            self.assertIn("target.safeAimPoint", report["actionReadiness"]["checksSkippedAsNotApplicable"])

    def test_cli_json_stdout_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = set(os.listdir(tmp))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--daemon-url",
                    "http://127.0.0.1:1",
                    "--timeout",
                    "0.01",
                    "--json",
                ],
                cwd=tmp,
                capture_output=True,
                text=True,
                check=False,
            )
            after = set(os.listdir(tmp))

        self.assertNotEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema"], "live_readiness.v2")
        self.assertEqual(before, after)

    def test_human_output_uses_safe_next_step_when_readiness_fails(self):
        report = {
            "schema": "live_readiness.v2",
            "status": "FAIL",
            "daemonUrl": "http://127.0.0.1:8890",
            "livenessRecoveryRecommended": True,
            "actionReadiness": {
                "status": "FAIL",
                "executionAllowed": False,
                "blockers": [{"code": "client_tick_hot_stale", "message": "stale"}],
            },
            "clientTickHot": {"recovery": "run ensure_loaded_scene once"},
        }

        text = diagnose_live_readiness.format_human(report)

        self.assertIn("--ensure-loaded-scene", text)
        self.assertIn("--arduino-port COM6", text)
        self.assertNotIn("pyautogui", text)
        self.assertNotIn("--execute", text)

    def test_human_output_uses_arduino_backend_when_execution_is_allowed(self):
        report = {
            "schema": "live_readiness.v2",
            "status": "PASS",
            "daemonUrl": "http://127.0.0.1:8890",
            "actionReadiness": {"status": "PASS", "executionAllowed": True},
        }

        text = diagnose_live_readiness.format_human(report)

        self.assertIn("--backend arduino", text)
        self.assertIn("--execute", text)
        self.assertNotIn("pyautogui", text)

    def test_cli_exits_nonzero_when_execution_is_blocked(self):
        report = {
            "schema": "live_readiness.v2",
            "status": "WARN",
            "daemonUrl": "http://127.0.0.1:8890",
            "actionReadiness": {"status": "WARN", "executionAllowed": False},
        }

        with patch.object(diagnose_live_readiness, "build_readiness_report", return_value=report):
            with redirect_stdout(io.StringIO()):
                result = diagnose_live_readiness.main([])

        self.assertEqual(result, 1)


class ExecutorReadinessGateTest(unittest.TestCase):
    def test_execute_refuses_click_when_readiness_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            write_json(session / "manifest.json", {"sessionId": "session"})
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
                sessions_dir=str(root),
            )

            result = execute_next_action(
                "http://127.0.0.1:8890",
                options,
                fetch_json_func=lambda *_args, **_kwargs: status_for(session),
                backend=backend,
            )

            self.assertEqual(result.status, "FAIL")
            self.assertFalse(result.executed)
            self.assertEqual(backend.calls, [])
            self.assertIn("session.liveOutputs", result.missing_capabilities)

    def test_wait_for_ready_waits_then_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            write_json(session / "manifest.json", {"sessionId": "session"})
            calls = {"count": 0}
            backend = FakeBackend()
            options = Namespace(
                timeout=0.01,
                backend="pyautogui",
                movement_profile="instant_test",
                execute=True,
                verify_after_action=False,
                wait_for_ready=1,
                poll_interval_ms=1,
                focus_runelite=False,
                window_title_filter="RuneLite",
                sessions_dir=str(root),
            )

            def fetch_status(*_args, **_kwargs):
                calls["count"] += 1
                if calls["count"] == 2:
                    write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
                return status_for(session, marker)

            with (
                patch("input_control.executor.fetch_action_context", side_effect=RuntimeError("offline unit test")),
                patch("input_control.executor.fetch_plugin_snapshot", side_effect=RuntimeError("offline unit test")),
            ):
                result = execute_next_action(
                    "http://127.0.0.1:8890",
                    options,
                    fetch_json_func=fetch_status,
                    backend=backend,
                    sleep_func=lambda *_args, **_kwargs: None,
                )

            self.assertEqual(result.status, "PASS")
            self.assertTrue(result.executed)
            self.assertEqual(len(backend.calls), 1)
            self.assertEqual(result.readiness["status"], "PASS")

    def test_wait_for_ready_times_out_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            write_json(session / "manifest.json", {"sessionId": "session"})
            backend = FakeBackend()
            options = Namespace(
                timeout=0.01,
                backend="pyautogui",
                movement_profile="instant_test",
                execute=True,
                verify_after_action=False,
                wait_for_ready=0.01,
                poll_interval_ms=1,
                focus_runelite=False,
                window_title_filter="RuneLite",
                sessions_dir=str(root),
            )
            monotonic_values = iter([0.0, 0.0, 0.02])

            with (
                patch("input_control.executor.fetch_action_context", side_effect=RuntimeError("offline unit test")),
                patch("input_control.executor.fetch_plugin_snapshot", side_effect=RuntimeError("offline unit test")),
            ):
                result = execute_next_action(
                    "http://127.0.0.1:8890",
                    options,
                    fetch_json_func=lambda *_args, **_kwargs: status_for(session),
                    backend=backend,
                    sleep_func=lambda *_args, **_kwargs: None,
                    monotonic_func=monotonic_values.__next__,
                )

            self.assertEqual(result.status, "FAIL")
            self.assertFalse(result.executed)
            self.assertEqual(backend.calls, [])
            self.assertIn("session.liveOutputs", result.missing_capabilities)


class InputGeometryResolverTest(unittest.TestCase):
    def test_valid_file_session_geometry_passes_with_window_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "sessions" / "session"
            write_baseline_geometry(session)

            with patch("input_control.input_geometry.find_runelite_window", return_value={"runeliteWindowMatched": False, "matchedWindow": None}):
                status = resolve_input_geometry_status({}, session=session)

            self.assertEqual(status["status"], "PASS")
            self.assertEqual(status["source"], "file_session.baseline.inputGeometry")
            self.assertEqual(status["canvasWidth"], 800)
            self.assertEqual(status["canvasHeight"], 600)
            self.assertTrue(status["screenToClientAvailable"])
            self.assertTrue(status["clientToScreenAvailable"])
            self.assertIsNotNone(status["canvasRect"])
            self.assertIsNotNone(status["clientRect"])

    def test_zero_canvas_fails_with_canvas_missing(self):
        with patch("input_control.input_geometry.find_runelite_window", return_value={"runeliteWindowMatched": False, "matchedWindow": None}):
            status = resolve_input_geometry_status(
                {"inputGeometry": {"inputGeometryAvailable": True, "canvasScreenX": 10, "canvasScreenY": 10, "canvasWidth": 0, "canvasHeight": 0}},
                sessions_dir=Path(tempfile.gettempdir()) / "missing-osrs-geometry-test-sessions",
            )

        self.assertEqual(status["status"], "FAIL")
        self.assertIn("input_geometry_canvas_missing", status["blockers"])

    def test_stale_file_geometry_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "sessions" / "session"
            write_baseline_geometry(session)

            with patch("input_control.input_geometry.find_runelite_window", return_value={"runeliteWindowMatched": False, "matchedWindow": None}):
                status = resolve_input_geometry_status({}, session=session, now=time.time() + 10, max_age_ms=1)

            self.assertEqual(status["status"], "FAIL")
            self.assertEqual(status["blockerCode"], "input_geometry_stale")

    def test_focus_repair_attempts_for_minimized_runelite(self):
        first_window = {
            "runeliteWindowMatched": True,
            "matchedWindow": {"hwnd": 123, "title": "RuneLite", "visible": True, "minimized": True, "foreground": False},
        }
        repaired = {
            "focusRepairAttempted": True,
            "focusRepairSucceeded": True,
            "windowRestoreAttempted": True,
            "windowRestoreSucceeded": True,
            "after": {
                "runeliteWindowMatched": True,
                "matchedWindow": {"hwnd": 123, "title": "RuneLite", "visible": True, "minimized": False, "foreground": True},
                "foregroundWindowTitle": "RuneLite",
            },
        }
        with (
            patch("input_control.input_geometry.find_runelite_window", return_value=first_window),
            patch("input_control.input_geometry.repair_runelite_focus", return_value=repaired),
        ):
            status = resolve_input_geometry_status(
                {
                    "inputGeometry": {
                        "inputGeometryAvailable": True,
                        "canvasScreenOrigin": {"x": 100, "y": 200},
                        "canvasSize": {"width": 800, "height": 600},
                    }
                },
                allow_focus_repair=True,
            )

        self.assertTrue(status["focusRepairAttempted"])
        self.assertTrue(status["focusRepairSucceeded"])

    def test_readiness_uses_latest_baseline_geometry_when_daemon_geometry_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target(tick=20)
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
            write_baseline_geometry(session, generated_tick=20)
            status = status_for(session, marker, latest_tick=20, input_geometry=False)

            with patch("input_control.input_geometry.find_runelite_window", return_value={"runeliteWindowMatched": False, "matchedWindow": None}):
                report = live_readiness.build_readiness_report(daemon_status=status, sessions_dir=root)

            self.assertNotIn("input_geometry_unavailable", [item["code"] for item in report["blockers"]])
            self.assertTrue(report["inputGeometry"]["inputGeometryAvailable"])
            self.assertEqual(report["inputGeometry"]["source"], "file_session.baseline.inputGeometry")


if __name__ == "__main__":
    unittest.main()

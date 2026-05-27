import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_live_readiness.py"
sys.path.insert(0, str(VIEWER_DIR))

import live_readiness
from input_control.executor import execute_next_action


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


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


def enable_plugin_snapshot(status: dict, *, post_menu_age_ms=25, client_tick_age_ms=None, game_state="LOGGED_IN"):
    status["inputSourceActive"] = "plugin-snapshot"
    status["pluginSnapshotHost"] = "127.0.0.1"
    status["pluginSnapshotPort"] = 8893
    status["clientTickHot"] = {
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

    def test_daemon_unavailable_is_fail(self):
        report = live_readiness.build_readiness_report(
            fetch_json_func=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("daemon down"))
        )

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["blockers"][0]["code"], "daemon_status_unavailable")

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


if __name__ == "__main__":
    unittest.main()

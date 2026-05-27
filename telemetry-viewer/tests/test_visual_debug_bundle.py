import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from input_control.visual_debug_bundle import VisualDebugBundleWriter


class FakeImage:
    def __init__(self, label="image"):
        self.label = label
        self.saved = []

    def save(self, path):
        self.saved.append(str(path))
        Path(path).write_bytes((self.label + "\n").encode("utf-8"))


class FakeBackend:
    def __init__(self):
        self.name = "fake"

    def current_position(self):
        return (111, 222)

    def canvas_client_geometry(self):
        return (10, 20), (765, 503)


def options(**overrides):
    defaults = {
        "capture_debug_screenshots": False,
        "screenshot_on_failure": False,
        "screenshot_on_camera_recovery": False,
        "screenshot_on_timeout": False,
        "screenshot_on_edge_reject": False,
        "screenshot_on_lifecycle_transition": False,
        "max_debug_screenshots": 20,
        "debug_screenshot_dir": None,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


class VisualDebugBundleTest(unittest.TestCase):
    def test_bundle_capture_is_off_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = VisualDebugBundleWriter.from_options(
                options(debug_screenshot_dir=tmp),
                backend=FakeBackend(),
                screenshot_func=lambda _region=None: FakeImage(),
            )

            self.assertFalse(writer.enabled)
            self.assertIsNone(writer.capture("resource_timeout", daemon_status={"latestTick": 1}))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_failure_bundle_includes_screenshot_status_overlay_and_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live = session / "interaction_geometry" / "live"
            live.mkdir(parents=True)
            (live / "overlay_debug_state.json").write_text(json.dumps({"latestTick": 7, "targets": [{"name": "Tree"}]}), encoding="utf-8")
            writer = VisualDebugBundleWriter.from_options(
                options(capture_debug_screenshots=True, screenshot_on_failure=True, debug_screenshot_dir=tmp),
                backend=FakeBackend(),
                screenshot_func=lambda region=None: FakeImage(f"region={region}"),
            )

            event = writer.capture(
                "failure",
                daemon_status={"latestTick": 8, "sessionPath": str(session), "inventoryFreeSlots": 11},
                proposal={"proposedAction": "select_resource_target", "targetName": "Tree"},
                action_trace={"finalClassification": "resource_timeout_no_progress", "selectedTarget": {"targetName": "Tree"}},
                readiness={
                    "actionReadiness": {"status": "PASS", "intent": "resource_object_action"},
                    "actionNeed": {"schema": "action_need.v1", "needsNextTarget": True},
                    "overlayHealth": {"schema": "overlay_health.v1", "markerCountZeroStatus": "unexpected_collecting_needs_target"},
                    "actionSafetyEvidence": {"schema": "action_safety_evidence.v1", "safeAimPointReady": True},
                },
                classification="resource_timeout_no_progress",
            )

            self.assertIsNotNone(event)
            bundle_dir = Path(event["bundleDir"])
            self.assertTrue((bundle_dir / "screenshot.png").exists())
            self.assertTrue((bundle_dir / "daemon_status.json").exists())
            self.assertTrue((bundle_dir / "overlay_debug_state.json").exists())
            payload = json.loads((bundle_dir / "bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "visual_debug_bundle.v1")
            self.assertEqual(payload["reason"], "failure")
            self.assertEqual(payload["sessionPath"], str(session))
            self.assertEqual(payload["currentIntent"], "select_resource_target")
            self.assertEqual(payload["classification"], "resource_timeout_no_progress")
            self.assertEqual(payload["mousePosition"], {"x": 111, "y": 222})
            self.assertEqual(payload["windowRect"], {"x": 10, "y": 20, "width": 765, "height": 503})
            self.assertEqual(payload["screenshotPath"], str(bundle_dir / "screenshot.png"))
            self.assertEqual(payload["actionNeed"]["needsNextTarget"], True)
            self.assertEqual(payload["overlayHealth"]["markerCountZeroStatus"], "unexpected_collecting_needs_target")
            self.assertEqual(payload["actionSafetyEvidence"]["safeAimPointReady"], True)

    def test_bundle_writes_world_model_evidence_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = VisualDebugBundleWriter.from_options(
                options(capture_debug_screenshots=True, debug_screenshot_dir=tmp),
                backend=FakeBackend(),
                screenshot_func=lambda _region=None: FakeImage(),
            )
            status = {
                "latestTick": 9,
                "worldModelSummary": {"schema": "world_model_summary.v1", "objects": {"total": 12}},
                "worldModelRouteObjectCensus": {"schema": "route_object_census.v1", "count": 1},
                "worldModelResourceObjectCensus": {"schema": "resource_object_census.v1", "count": 2},
                "worldModelServiceObjectCensus": {"schema": "service_object_census.v1", "count": 1},
                "worldModelProjectionAudit": {"schema": "projection_audit.v1", "projectionObjectsProjected": 4},
                "worldModelPathingFrontier": {"schema": "pathing_frontier.v1", "candidateCount": 3},
            }

            event = writer.capture("final_summary", daemon_status=status)

            self.assertIsNotNone(event)
            bundle_dir = Path(event["bundleDir"])
            for filename in (
                "world_model_summary.json",
                "route_object_census.json",
                "resource_object_census.json",
                "service_object_census.json",
                "projection_audit.json",
                "collision_frontier.json",
                "knowledge_fabric_status.json",
                "current_debug_context.json",
                "explain_current_blocker.json",
                "resource_candidates.json",
                "service_candidates.json",
                "route_objects.json",
                "pathing_frontier.json",
                "view_quality.json",
                "session_memory_summary.json",
                "static_library_summary.json",
                "data_quality_report.json",
                "handoff_summary.json",
            ):
                self.assertTrue((bundle_dir / filename).exists(), filename)
            payload = json.loads((bundle_dir / "bundle.json").read_text(encoding="utf-8"))
            self.assertIn("world_model_summary.json", payload["worldModelEvidencePaths"])
            self.assertIn("knowledge_fabric_status.json", payload["knowledgeFabricEvidencePaths"])
            self.assertIn("current_debug_context.json", payload["knowledgeFabricEvidencePaths"])
            self.assertIn("explain_current_blocker.json", payload["knowledgeFabricEvidencePaths"])

    def test_bundle_writes_input_integrity_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = VisualDebugBundleWriter.from_options(
                options(screenshot_on_failure=True, debug_screenshot_dir=tmp),
                backend=FakeBackend(),
                screenshot_func=lambda _region=None: FakeImage(),
            )
            integrity = {
                "schema": "input_integrity_status.v1",
                "status": "FAIL",
                "arduinoDetected": {"vidPidMatched": True},
                "injectionFlags": {"mouseInjectedCount": 1},
                "backend": {"liveInputBackend": "arduino", "directBackendBypassCount": 0},
            }

            event = writer.capture(
                "input_integrity_fail",
                daemon_status={"latestTick": 1},
                action_trace={"inputIntegrityStatusAfter": integrity, "humanInput": {"directBackendBypassCount": 0}},
            )

            self.assertIsNotNone(event)
            bundle_dir = Path(event["bundleDir"])
            self.assertTrue((bundle_dir / "input_integrity_status.json").exists())
            payload = json.loads((bundle_dir / "bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["inputIntegrityStatusPath"], str(bundle_dir / "input_integrity_status.json"))
            self.assertEqual(payload["inputIntegrityStatus"]["status"], "FAIL")

    def test_bundle_capture_respects_max_screenshot_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = VisualDebugBundleWriter.from_options(
                options(capture_debug_screenshots=True, screenshot_on_timeout=True, max_debug_screenshots=1, debug_screenshot_dir=tmp),
                backend=FakeBackend(),
                screenshot_func=lambda _region=None: FakeImage(),
            )

            first = writer.capture("resource_timeout", daemon_status={"latestTick": 1})
            second = writer.capture("resource_timeout", daemon_status={"latestTick": 2})

            self.assertIsNotNone(first)
            self.assertIsNone(second)
            self.assertEqual(writer.metrics()["captured"], 1)
            self.assertEqual(writer.metrics()["skippedByLimit"], 1)

    def test_screenshot_capture_failure_writes_bundle_and_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fail_screenshot(_region=None):
                raise RuntimeError("screen unavailable")

            writer = VisualDebugBundleWriter.from_options(
                options(capture_debug_screenshots=True, screenshot_on_timeout=True, debug_screenshot_dir=tmp),
                backend=FakeBackend(),
                screenshot_func=fail_screenshot,
            )

            event = writer.capture("resource_timeout", daemon_status={"latestTick": 1})

            self.assertIsNotNone(event)
            bundle_dir = Path(event["bundleDir"])
            payload = json.loads((bundle_dir / "bundle.json").read_text(encoding="utf-8"))
            self.assertIsNone(payload["screenshotPath"])
            self.assertTrue(payload["screenshotCaptureFailed"])
            self.assertIn("screenshot capture failed", payload["warnings"][0])
            self.assertEqual(writer.metrics()["captureFailures"], 1)

    def test_route_debug_reasons_map_to_existing_screenshot_flags(self):
        failure_writer = VisualDebugBundleWriter.from_options(
            options(screenshot_on_failure=True),
            backend=FakeBackend(),
            screenshot_func=lambda _region=None: FakeImage(),
        )
        edge_writer = VisualDebugBundleWriter.from_options(
            options(screenshot_on_edge_reject=True),
            backend=FakeBackend(),
            screenshot_func=lambda _region=None: FakeImage(),
        )
        camera_writer = VisualDebugBundleWriter.from_options(
            options(screenshot_on_camera_recovery=True),
            backend=FakeBackend(),
            screenshot_func=lambda _region=None: FakeImage(),
        )
        lifecycle_writer = VisualDebugBundleWriter.from_options(
            options(screenshot_on_lifecycle_transition=True),
            backend=FakeBackend(),
            screenshot_func=lambda _region=None: FakeImage(),
        )

        for reason in (
            "route_source_mismatch",
            "route_wall_hugging_detected",
            "goal_directed_path_blocked",
            "unexpected_current_area",
            "menu_flip_mismatch",
            "repeated_navigation_no_progress",
            "target_source_mismatch",
            "stale_static_route_target",
            "hover_intent_mismatch",
            "stale_proposal_reacquire_failed",
            "poor_resource_view",
            "resource_target_edge_rejected",
            "worksite_drift_detected",
            "no_executable_resource_view",
        ):
            self.assertTrue(failure_writer.reason_enabled(reason), reason)
        self.assertTrue(edge_writer.reason_enabled("route_waypoint_edge_rejected"))
        self.assertTrue(edge_writer.reason_enabled("route_edge_projection_rejected"))
        self.assertTrue(camera_writer.reason_enabled("camera_reacquire_start"))
        self.assertTrue(camera_writer.reason_enabled("resource_projection_recovery_end"))
        self.assertTrue(camera_writer.reason_enabled("resource_camera_reacquire_start"))
        self.assertTrue(camera_writer.reason_enabled("resource_camera_reacquire_end"))
        for reason in (
            "goal_directed_fallback_started",
            "alternate_approach_node_selected",
            "service_anchor_reached",
            "route_object_reacquired",
            "live_target_reacquired",
            "return_transition_pending",
            "return_transition_retry_required",
            "return_transition_retry_success",
            "return_transition_reconciled_success",
            "retry_while_pending_detected",
            "post_depletion_reacquire",
        ):
            self.assertTrue(lifecycle_writer.reason_enabled(reason), reason)

    def test_route_bundle_includes_visual_review_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            session.mkdir()
            writer = VisualDebugBundleWriter.from_options(
                options(screenshot_on_failure=True, debug_screenshot_dir=tmp),
                backend=FakeBackend(),
                screenshot_func=lambda _region=None: FakeImage(),
            )
            status = {
                "sessionPath": str(session),
                "inventoryFreeSlots": 0,
                "heldResourceCount": 15,
                "brain": {
                    "playerContext": {"worldTile": {"worldX": 3254, "worldY": 3240, "plane": 0}},
                    "inventoryContext": {"freeSlots": 0, "progress": {"currentHeldCount": 15}},
                    "clientTickHot": {
                        "schema": "client_tick_hot.v1",
                        "clientTick": 44,
                        "gameTickAtSample": 101,
                        "gameState": "LOGGED_IN",
                        "hoverMenu": {"topOption": "Walk here", "topTarget": ""},
                        "lastMenuOptionClicked": {"option": "Walk here", "target": ""},
                    },
                    "serviceRouteContext": {
                        "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                        "currentNodeId": "lumbridge_castle_west_approach",
                        "nextEdge": {"edgeId": "west_to_approach", "type": "walk_to"},
                        "routeStepStatus": "destination_outside_collision_window",
                        "currentNavigationTarget": {"worldX": 3206, "worldY": 3242, "plane": 0},
                        "selectedServiceObject": {"name": "Bank booth", "worldX": 3208, "worldY": 3220, "plane": 2},
                        "routeWallLoopDetected": True,
                        "routeSourceMismatch": {"expectedArea": "lumbridge_west_trees", "observedArea": "east_lumbridge"},
                    },
                    "pathingContext": {
                        "pathingReason": "destination_outside_collision_window",
                        "routeMode": "local_frontier_to_service",
                        "selectedApproachNode": {"nodeId": "castle_approach", "worldX": 3207, "worldY": 3230, "plane": 0},
                        "nextWaypointTile": {"worldX": 3250, "worldY": 3238, "plane": 0},
                    },
                },
            }
            proposal = {
                "proposedAction": "navigate_to_service",
                "targetKind": "path_tile",
                "actionTargetSource": "live_projected_waypoint",
                "actionability": "needs_hover_confirmation",
                "clickPointResolution": {
                    "coordinateSpace": "scaled_logical_to_physical",
                    "scaleX": 2.81,
                    "scaleY": 3.02,
                    "screenPointBeforeScaling": {"x": 782, "y": 687},
                    "screenPointAfterScaling": {"x": 1368, "y": 1203},
                    "windowBoundsSource": "clientWindowBounds",
                    "canvasBoundsSource": "canvasSize/sourceCanvasSize",
                },
                "targetTile": {"worldX": 3250, "worldY": 3238, "plane": 0},
                "targetExplanation": {
                    "targetSource": "static_route_prior",
                    "advisoryTargetSource": "static_route_prior",
                    "routeProjectionStatus": {"classification": "visible", "edgeDistancePx": 36},
                    "safeAimPoint": {"status": "PASS", "actionable": True},
                },
            }

            event = writer.capture(
                "route_wall_hugging_detected",
                daemon_status=status,
                proposal=proposal,
                classification="route_wall_hugging_detected",
                extra={"finalDecision": "stopped safely"},
            )

            payload = json.loads((Path(event["bundleDir"]) / "bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["playerLocation"], {"worldX": 3254, "worldY": 3240, "plane": 0})
            self.assertEqual(payload["currentRouteMode"], "local_frontier_to_service")
            self.assertEqual(payload["currentRouteNode"], "lumbridge_castle_west_approach")
            self.assertEqual(payload["currentRouteEdge"], {"edgeId": "west_to_approach", "type": "walk_to"})
            self.assertEqual(payload["selectedServiceAnchor"]["name"], "Bank booth")
            self.assertEqual(payload["selectedApproachNode"]["nodeId"], "castle_approach")
            self.assertEqual(payload["selectedWaypoint"], {"worldX": 3250, "worldY": 3238, "plane": 0})
            self.assertEqual(payload["routeSourceMismatchDetails"]["observedArea"], "east_lumbridge")
            self.assertEqual(payload["pathingReason"], "destination_outside_collision_window")
            self.assertEqual(payload["wallLoopClassification"], "route_wall_hugging_detected")
            self.assertEqual(payload["clickActionClassification"], "route_wall_hugging_detected")
            self.assertEqual(payload["finalDecision"], "stopped safely")
            self.assertEqual(payload["clientTickHotSummary"]["clientTick"], 44)
            self.assertEqual(payload["latestHoverMenu"]["topOption"], "Walk here")
            self.assertEqual(payload["latestMenuOptionClicked"]["option"], "Walk here")
            self.assertEqual(payload["actionProposalSummary"]["proposedAction"], "navigate_to_service")
            self.assertEqual(payload["selectedTargetSource"], "static_route_prior")
            self.assertEqual(payload["selectedActionTargetSource"], "live_projected_waypoint")
            self.assertEqual(payload["selectedActionability"], "needs_hover_confirmation")
            self.assertEqual(payload["advisoryTargetSource"], "static_route_prior")
            self.assertEqual(payload["routeContextSummary"]["routeId"], "lumbridge_west_trees_to_lumbridge_castle_bank")
            self.assertEqual(payload["coordinateScaling"]["coordinateSpace"], "scaled_logical_to_physical")
            self.assertEqual(payload["coordinateScaling"]["windowBoundsSource"], "clientWindowBounds")

    def test_transition_bundle_includes_action_ledger_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = VisualDebugBundleWriter.from_options(
                options(screenshot_on_lifecycle_transition=True, debug_screenshot_dir=tmp),
                backend=FakeBackend(),
                screenshot_func=lambda _region=None: FakeImage(),
            )

            event = writer.capture(
                "return_transition_retry_required",
                daemon_status={"latestTick": 10},
                action_trace={
                    "finalClassification": "return_transition_retry_required",
                    "routeTransitionLedgerEntry": {
                        "schema": "route_transition_action_ledger.v1",
                        "actionId": "action-1",
                        "actionIntent": "return_transition_action",
                        "evidence": {"menuClickMatched": True, "planeChanged": False},
                    },
                },
                classification="return_transition_retry_required",
            )

            payload = json.loads((Path(event["bundleDir"]) / "bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(
                payload["actionTraceExcerpt"]["routeTransitionLedgerEntry"]["schema"],
                "route_transition_action_ledger.v1",
            )
            self.assertEqual(payload["actionTraceExcerpt"]["routeTransitionLedgerEntry"]["actionId"], "action-1")

    def test_resource_bundle_includes_view_score_and_worksite_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = VisualDebugBundleWriter.from_options(
                options(screenshot_on_failure=True, debug_screenshot_dir=tmp),
                backend=FakeBackend(),
                screenshot_func=lambda _region=None: FakeImage(),
            )
            status = {
                "sessionPath": str(Path(tmp) / "session"),
                "latestTick": 101,
                "playerLocation": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "brain": {
                    "genericTaskState": {"phase": "target_selected", "activeIntent": "select_target"},
                    "inventoryContext": {"freeSlots": 12, "progress": {"currentHeldCount": 2}},
                    "resourceReturnContext": {
                        "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                        "worksiteRadiusTiles": 8,
                    },
                },
            }
            proposal = {
                "proposedAction": "resource_view_recovery",
                "targetKind": "resource_recovery",
                "targetName": "Tree",
                "targetExplanation": {
                    "name": "Tree",
                    "resourceViewScore": {
                        "schema": "resource_view_score.v1",
                        "worksiteId": "lumbridge_west_trees",
                        "worksiteAnchor": {"worldX": 3196, "worldY": 3248, "plane": 0},
                        "classification": "poor_edge_resource_view",
                        "score": 42,
                        "selectedTargetEdgeDistancePx": 4,
                    },
                    "resourceViewClassification": "poor_edge_resource_view",
                    "resourceCameraRecoveryRecommended": True,
                    "safeAimPoint": {"status": "PASS", "distanceToViewportEdgePx": 4},
                },
            }

            event = writer.capture(
                "poor_resource_view",
                daemon_status=status,
                proposal=proposal,
                classification="poor_edge_resource_view",
            )

            payload = json.loads((Path(event["bundleDir"]) / "bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["resourceViewClassification"], "poor_edge_resource_view")
            self.assertTrue(payload["resourceCameraRecoveryRecommended"])
            self.assertEqual(payload["resourceViewScore"]["selectedTargetEdgeDistancePx"], 4)
            self.assertEqual(payload["actionProposalSummary"]["resourceViewScore"]["score"], 42)

    def test_route_mode_prefers_active_service_route_over_stale_return_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = VisualDebugBundleWriter.from_options(
                options(screenshot_on_failure=True, debug_screenshot_dir=tmp),
                backend=FakeBackend(),
                screenshot_func=lambda _region=None: FakeImage(),
            )
            status = {
                "collisionWindowCenterWorld": {"worldX": 3254, "worldY": 3240, "plane": 0},
                "brain": {
                    "genericTaskState": {"phase": "inventory_full", "activeIntent": "needs_service"},
                    "serviceRouteContext": {
                        "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                        "currentNodeId": "lumbridge_castle_west_approach",
                        "nextEdge": {"type": "walk_to"},
                    },
                    "returnRouteContext": {
                        "routeMode": "reverse_route",
                        "returnRouteId": "lumbridge_return",
                    },
                    "pathingContext": {
                        "pathingReason": "destination_outside_collision_window",
                        "pathTargetTile": {"worldX": 3252, "worldY": 3240, "plane": 0},
                    },
                },
            }

            event = writer.capture("route_wall_hugging_detected", daemon_status=status, classification="route_wall_hugging_detected")
            payload = json.loads((Path(event["bundleDir"]) / "bundle.json").read_text(encoding="utf-8"))

            self.assertEqual(payload["currentRouteMode"], "local_frontier_to_service")
            self.assertEqual(payload["playerLocation"], {"worldX": 3254, "worldY": 3240, "plane": 0})
            self.assertEqual(payload["playerLocationSource"], "collision_window_center_proxy")
            self.assertEqual(payload["playerLocationConfidence"], 0.35)

    def test_goal_directed_route_source_mismatch_bundle_uses_service_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = VisualDebugBundleWriter.from_options(
                options(screenshot_on_lifecycle_transition=True, debug_screenshot_dir=tmp),
                backend=FakeBackend(),
                screenshot_func=lambda _region=None: FakeImage(),
            )
            status = {
                "sessionPath": str(Path(tmp) / "session"),
                "brain": {
                    "playerContext": {"worldTile": {"worldX": 3254, "worldY": 3240, "plane": 0}},
                    "serviceRouteContext": {
                        "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                        "routeMode": "goal_directed_fallback",
                        "goalDirectedFallback": True,
                        "currentNodeId": "lumbridge_castle_entrance_or_courtyard",
                        "selectedServiceAnchor": {"anchorId": "lumbridge_castle_bank"},
                        "selectedApproachNode": {"nodeId": "lumbridge_castle_entrance_or_courtyard"},
                        "routeSourceMismatch": {"classification": "route_source_mismatch"},
                    },
                    "pathingContext": {
                        "routeMode": "goal_directed_fallback",
                        "pathingReason": "destination_outside_collision_window",
                        "localFrontierWaypoint": {"worldX": 3250, "worldY": 3238, "plane": 0},
                    },
                },
            }

            event = writer.capture("goal_directed_fallback_started", daemon_status=status, classification="goal_directed_fallback_started")
            payload = json.loads((Path(event["bundleDir"]) / "bundle.json").read_text(encoding="utf-8"))

            self.assertEqual(payload["currentRouteMode"], "goal_directed_fallback")
            self.assertEqual(payload["selectedServiceAnchor"]["anchorId"], "lumbridge_castle_bank")
            self.assertEqual(payload["selectedApproachNode"]["nodeId"], "lumbridge_castle_entrance_or_courtyard")
            self.assertEqual(payload["routeSourceMismatchDetails"]["classification"], "route_source_mismatch")


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from input_control.action_proposal import build_action_proposal


def aim(x=100, y=120):
    return {"canvasX": x, "canvasY": y}


def bounds(x=10, y=20, width=30, height=40):
    return {"x": x, "y": y, "width": width, "height": height}


def status_for(
    *,
    phase="target_selected",
    active_intent="select_target",
    active_target=None,
    inventory_full=False,
    free_slots=15,
    service=None,
    pathing=None,
    bank_ui=None,
    bank_operation=None,
    close_bank=None,
    resource_return=None,
    return_route=None,
    service_route=None,
    overlay=None,
    latest_tick=None,
    freshness="fresh",
    input_geometry=None,
    camera_viewport=None,
    dialogue_state=None,
):
    active_target = active_target or {"targetName": "Tree", "classId": "tree", "id": 1278, "aimPoint": aim(110, 130)}
    status = {
        "brain": {
            "latestTick": latest_tick,
            "freshnessDomains": {"targetCandidateFreshness": freshness} if freshness is not None else {},
            "genericTaskState": {
                "phase": phase,
                "activeIntent": active_intent,
                "activeIntentTarget": active_target,
                "blockingConditions": [],
            },
            "inventoryContext": {"inventoryFull": inventory_full, "freeSlots": free_slots},
            "serviceContext": service or {},
            "serviceRouteContext": service_route or {},
            "pathingContext": pathing or {},
            "bankUiContext": bank_ui or {"bankOpen": False, "bankReadable": False, "bankPinOpen": False},
            "bankOperationContext": bank_operation or {},
            "closeBankContext": close_bank or {},
            "resourceReturnContext": resource_return or {},
            "returnRouteContext": return_route or {},
            "dialogueState": dialogue_state or {},
            "intentOverlayContext": overlay or {"selectedMarker": active_target},
            "missingRequiredContextDomains": [],
            "warnings": [],
        }
    }
    if latest_tick is not None:
        status["latestTick"] = latest_tick
    if input_geometry is not None:
        status["inputGeometry"] = input_geometry
    if camera_viewport is not None:
        status["cameraViewport"] = camera_viewport
    return status


class ActionProposalTest(unittest.TestCase):
    def test_resource_candidates_without_safe_aimpoint_propose_projection_recovery(self):
        target = {
            "targetName": "Tree",
            "name": "Tree",
            "classId": "tree",
            "targetType": "sceneObject",
            "id": 1278,
            "worldX": 3212,
            "worldY": 3232,
            "plane": 0,
            "projectionMode": "live_object_pending",
            "aimPoint": {"canvasX": 2147483647.5, "canvasY": 2147483647.5, "source": "live_object_pending"},
            "bounds": {"x": 2147483647, "y": 2147483647, "width": 1, "height": 1},
            "geometryAvailable": True,
            "onScreen": True,
        }

        proposal = build_action_proposal(
            status_for(
                active_target=target,
                overlay={"selectedMarker": target, "markers": [target]},
            )
        )

        self.assertEqual(proposal.proposed_action, "resource_view_recovery")
        self.assertEqual(proposal.target_kind, "resource_recovery")
        self.assertTrue(proposal.executable)
        self.assertIsNone(proposal.suggested_click_point)
        self.assertEqual(proposal.key_action["type"], "camera_reacquire")
        self.assertEqual(proposal.target_explanation["resourceProjectionStatus"]["classification"], "projection_sentinel")
        self.assertEqual(proposal.target_explanation["bestLogicalResourceTarget"]["name"], "Tree")
        self.assertIsNone(proposal.target_explanation["selectedExecutableResourceTarget"])

    def test_collecting_resource_target_proposes_select_resource_target(self):
        proposal = build_action_proposal(status_for())

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertEqual(proposal.target_kind, "resource")
        self.assertEqual(proposal.target_name, "Tree")
        self.assertEqual(proposal.suggested_click_point, {"x": 110, "y": 130})
        self.assertEqual(proposal.status, "PASS")

    def test_service_context_policy_does_not_force_service_before_inventory_full(self):
        proposal = build_action_proposal(
            status_for(
                free_slots=2,
                inventory_full=False,
                service={"serviceNeeded": True, "serviceRequired": True, "serviceReady": False},
                pathing={
                    "pathingNeeded": True,
                    "nextWaypointTile": {"worldX": 3201, "worldY": 3200, "plane": 0},
                    "nextWaypointAimPoint": aim(260, 280),
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertEqual(proposal.target_kind, "resource")

    def test_inventory_full_without_route_context_reports_specific_blocker(self):
        proposal = build_action_proposal(
            status_for(
                inventory_full=True,
                free_slots=0,
                service={"serviceNeeded": True, "serviceRequired": True, "serviceReady": False},
            )
        )

        self.assertEqual(proposal.proposed_action, "wait_for_context")
        self.assertEqual(proposal.reason, "inventory_full_route_context_missing")
        self.assertIn("service_route.route_to_bank", proposal.missing_capabilities)
        self.assertIn("pathing.route_to_bank", proposal.missing_capabilities)

    def test_inventory_full_sparse_route_guide_position_proposes_next_bank_point(self):
        status = status_for(
            phase="needs_service",
            active_intent="needs_service",
            inventory_full=True,
            free_slots=0,
        )
        status["playerWorldPosition"] = {"worldX": 3201, "worldY": 3219, "plane": 0}
        status["brain"]["genericTaskState"]["activeIntentTarget"] = None
        status["brain"]["intentOverlayContext"] = {"selectedMarker": None}

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertTrue(proposal.executable)
        self.assertEqual(proposal.reason, "route_guide_progress_without_live_route_context")
        self.assertEqual(proposal.target_tile, {"worldX": 3209, "worldY": 3216, "plane": 0})
        self.assertEqual(proposal.target_explanation["routeGuideName"], "woodcutting_area_to_bank")

    def test_logs_held_at_actionable_service_target_proposes_open_service_with_free_slots(self):
        service_target = {
            "targetName": "Bank Deposit Box",
            "name": "Bank Deposit Box",
            "classId": "bank_related",
            "targetType": "sceneObject",
            "source": "live_route_object",
            "worldX": 3210,
            "worldY": 3217,
            "plane": 2,
            "bounds": bounds(293, 92, 67, 133),
            "aimPoint": aim(326, 158),
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
        status = status_for(
            phase="needs_more_context",
            active_intent="observe",
            free_slots=8,
            inventory_full=False,
            service={
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
            },
            bank_ui={"bankOpen": False, "bankReadable": False},
            bank_operation={"operationNeeded": False, "bankingComplete": False, "resourceItemsHeld": None},
        )
        status["brain"]["genericTaskState"]["activeIntentTarget"] = None
        status["brain"]["genericTaskState"]["goalProgress"] = {"heldResourceCount": 7}
        status["brain"]["intentOverlayContext"] = {"selectedMarker": None}

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "open_service")
        self.assertEqual(proposal.target_kind, "service")
        self.assertEqual(proposal.target_name, "Bank Deposit Box")
        self.assertEqual(proposal.reason, "service_target_actionable")
        self.assertTrue(proposal.executable)

    def test_logs_held_do_not_route_to_service_from_observe_context_with_free_slots(self):
        staircase = {
            "targetName": "Staircase",
            "classId": "route_transition",
            "targetType": "sceneObject",
            "worldX": 3204,
            "worldY": 3229,
            "plane": 1,
            "actions": ["Climb", "Climb-up", "Climb-down"],
            "aimPoint": aim(208, 191),
            "routeRelevance": {"relevanceStatus": "PASS", "candidateWouldAdvanceRoute": True},
        }
        status = status_for(
            phase="needs_more_context",
            active_intent="observe",
            free_slots=15,
            inventory_full=False,
            service={
                "serviceNeeded": True,
                "serviceRequired": True,
                "serviceReady": False,
                "serviceRouteContext": {
                    "schema": "service_route_context.v1",
                    "routeStepStatus": "route_interaction_visible",
                    "actionReady": True,
                    "visibleInteractionTarget": staircase,
                    "selectedRouteObject": staircase,
                },
            },
            bank_operation={"operationNeeded": False, "bankingComplete": False, "resourceItemsHeld": None},
            bank_ui={"bankOpen": False, "bankReadable": False},
        )
        status["brain"]["genericTaskState"]["activeIntentTarget"] = None
        status["brain"]["genericTaskState"]["goalProgress"] = {"heldResourceCount": 1}
        status["brain"]["intentOverlayContext"] = {"selectedMarker": None}

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "wait_for_context")
        self.assertFalse(proposal.executable)
        self.assertNotEqual(proposal.target_name, "Staircase")

    def test_logs_held_observe_context_continues_active_service_route_progress(self):
        staircase = {
            "targetName": "Staircase",
            "classId": "route_transition",
            "targetType": "sceneObject",
            "worldX": 3204,
            "worldY": 3229,
            "plane": 1,
            "actions": ["Climb", "Climb-up", "Climb-down"],
            "aimPoint": aim(208, 191),
            "routeRelevance": {"relevanceStatus": "PASS", "candidateWouldAdvanceRoute": True},
        }
        status = status_for(
            phase="needs_more_context",
            active_intent="observe",
            free_slots=15,
            inventory_full=False,
            service={
                "serviceNeeded": True,
                "serviceRequired": True,
                "serviceReady": False,
                "serviceRouteContext": {
                    "schema": "service_route_context.v1",
                    "routeStepStatus": "route_interaction_visible",
                    "completedSteps": ["first stairs up"],
                    "actionReady": True,
                    "currentStep": {
                        "type": "interact_object",
                        "expectedOptions": ["Climb-up", "Top-floor"],
                        "expectedTargetContains": ["Stair", "Stairs", "Staircase"],
                    },
                    "visibleInteractionTarget": staircase,
                    "selectedRouteObject": staircase,
                },
            },
            bank_operation={"operationNeeded": False, "bankingComplete": False, "resourceItemsHeld": None},
            bank_ui={"bankOpen": False, "bankReadable": False},
            overlay={"selectedMarker": None},
        )
        status["brain"]["genericTaskState"]["activeIntentTarget"] = None
        status["brain"]["genericTaskState"]["goalProgress"] = {"heldResourceCount": 1}

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "interact_service_route_object")
        self.assertEqual(proposal.target_kind, "service_route_object")
        self.assertEqual(proposal.target_name, "Staircase")

    def test_logs_held_active_resource_target_prefers_live_service_route_transition(self):
        tree = {
            "targetName": "Tree",
            "classId": "tree",
            "id": 1278,
            "worldX": 3212,
            "worldY": 3232,
            "plane": 0,
            "distanceTiles": 7,
            "aimPoint": aim(358, 60),
        }
        staircase = {
            "targetName": "Staircase",
            "classId": "route_transition",
            "targetType": "sceneObject",
            "worldX": 3204,
            "worldY": 3229,
            "plane": 0,
            "actions": ["Climb-up", "Top-floor"],
            "aimPoint": aim(224, 160),
            "routeRelevance": {"relevanceStatus": "PASS", "candidateWouldAdvanceRoute": True},
        }
        status = status_for(
            phase="target_selected",
            active_intent="continue_current_target",
            active_target=tree,
            free_slots=15,
            inventory_full=False,
            service={
                "serviceNeeded": True,
                "serviceRequired": True,
                "serviceReady": False,
                "serviceRouteContext": {
                    "schema": "service_route_context.v1",
                    "routeStepStatus": "route_interaction_visible",
                    "actionReady": True,
                    "currentStep": {
                        "type": "interact_object",
                        "expectedOptions": ["Climb-up", "Top-floor"],
                        "expectedTargetContains": ["Stair", "Stairs", "Staircase"],
                    },
                    "visibleInteractionTarget": staircase,
                    "selectedRouteObject": staircase,
                },
            },
            bank_operation={"operationNeeded": False, "bankingComplete": False, "resourceItemsHeld": None},
            bank_ui={"bankOpen": False, "bankReadable": False},
            overlay={"selectedMarker": tree},
        )
        status["brain"]["genericTaskState"]["goalProgress"] = {"heldResourceCount": 1}

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "interact_service_route_object")
        self.assertEqual(proposal.target_kind, "service_route_object")
        self.assertEqual(proposal.target_name, "Staircase")
        self.assertEqual(proposal.suggested_click_point, {"x": 224, "y": 160})

    def test_static_index_resource_candidate_is_live_resource_source(self):
        proposal = build_action_proposal(
            status_for(
                active_target={
                    "targetName": "Tree",
                    "classId": "tree",
                    "id": 1278,
                    "worldX": 3205,
                    "worldY": 3240,
                    "plane": 0,
                    "onScreen": True,
                    "geometryAvailable": True,
                    "aimPoint": aim(110, 130),
                    "source": {"type": "world_targets", "fileType": "world", "staticIndex": True},
                }
            )
        )

        payload = proposal.to_dict()
        self.assertEqual(payload["actionTargetSource"], "live_resource_candidate")
        self.assertEqual(payload["targetExplanation"]["actionTargetSource"], "live_resource_candidate")

    def test_unknown_skill_resource_reacquire_replaces_oak_with_basic_tree(self):
        oak = {
            "targetName": "Oak tree",
            "classId": "tree",
            "id": 10820,
            "worldX": 3205,
            "worldY": 3240,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "aimPoint": aim(110, 130),
        }
        tree = {
            "targetName": "Tree",
            "classId": "tree",
            "id": 1278,
            "worldX": 3191,
            "worldY": 3252,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "qualityScore": 80,
            "aimPoint": aim(210, 230),
        }
        status = status_for(active_target=oak, overlay={"selectedMarker": oak, "markers": [oak, tree]})
        status["brain"]["profileCandidates"] = [oak, tree]
        status["profileCandidates"] = [oak, tree]

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertEqual(proposal.target_name, "Tree")
        self.assertEqual(proposal.suggested_click_point, {"x": 210, "y": 230})
        self.assertEqual(proposal.target_explanation["resourceSelectionReason"], "preferred_skill_eligible_resource_candidate")

    def test_unknown_skill_does_not_reacquire_oak_after_basic_tree_suppression(self):
        oak = {
            "targetName": "Oak tree",
            "classId": "tree",
            "id": 10820,
            "worldX": 3205,
            "worldY": 3240,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "aimPoint": aim(110, 130),
            "targetKey": "oak-target",
        }
        tree = {
            "targetName": "Tree",
            "classId": "tree",
            "id": 1278,
            "worldX": 3191,
            "worldY": 3252,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "qualityScore": 80,
            "aimPoint": aim(210, 230),
            "targetKey": "tree-target",
        }
        status = status_for(active_target=tree, overlay={"selectedMarker": tree, "markers": [tree, oak]})
        status["brain"]["profileCandidates"] = [tree, oak]
        status["profileCandidates"] = [tree, oak]
        status["suppressedResourceTargetKeys"] = ["tree-target"]

        proposal = build_action_proposal(status)

        self.assertNotEqual(proposal.target_name, "Oak tree")
        self.assertFalse(proposal.executable)
        self.assertEqual(proposal.proposed_action, "wait_for_context")

    def test_service_ready_bank_closed_proposes_open_service(self):
        proposal = build_action_proposal(
            status_for(
                phase="service_available",
                active_intent="service_available",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={
                    "serviceNeeded": True,
                    "serviceReady": True,
                    "bestServiceCandidate": {"targetName": "Bank booth", "classId": "bank_booth", "aimPoint": aim(220, 240)},
                },
                bank_ui={"bankOpen": False},
            )
        )

        self.assertEqual(proposal.proposed_action, "open_service")
        self.assertEqual(proposal.target_kind, "service")
        self.assertEqual(proposal.target_name, "Bank booth")
        self.assertEqual(proposal.suggested_click_point, {"x": 220, "y": 240})

    def test_active_route_dialogue_proposes_number_key_choice(self):
        proposal = build_action_proposal(
            status_for(
                phase="pathing_to_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service_route={
                    "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                    "currentStepIndex": 4,
                    "currentStep": {
                        "type": "interact_object",
                        "label": "second stairs up",
                        "planeChange": "+1",
                    },
                },
                dialogue_state={
                    "schema": "dialogue_state.v1",
                    "active": True,
                    "type": "options",
                    "promptText": "Climb up or down the stairs?",
                    "canUseNumberKeys": True,
                    "options": [
                        {"index": 1, "key": "1", "text": "Climb up the stairs."},
                        {"index": 2, "key": "2", "text": "Climb down the stairs."},
                    ],
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "interface_dialogue_choice")
        self.assertEqual(proposal.target_kind, "interface_dialogue")
        self.assertEqual(proposal.key_action, {"type": "key_press", "key": "1"})
        self.assertTrue(proposal.executable)

    def test_inventory_full_with_service_path_needed_proposes_navigate_to_service(self):
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={
                    "serviceNeeded": True,
                    "serviceReady": False,
                    "bestServiceCandidate": {"targetName": "Bank booth", "classId": "bank_booth"},
                },
                pathing={
                    "pathingNeeded": True,
                    "pathCompleted": False,
                    "nextWaypointTile": {"worldX": 3201, "worldY": 3200, "plane": 0},
                    "nextWaypointAimPoint": aim(260, 280),
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_kind, "path_tile")
        self.assertEqual(proposal.suggested_click_point, {"x": 260, "y": 280})

    def test_service_path_without_live_waypoint_is_not_executable(self):
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={
                    "serviceNeeded": True,
                    "serviceReady": False,
                    "bestServiceCandidate": {
                        "targetName": "Closed bank booth",
                        "classId": "bank_booth",
                        "worldX": 3268,
                        "worldY": 3170,
                        "plane": 0,
                        "source": "initialFullPlaneScan",
                    },
                },
                pathing={
                    "pathingNeeded": True,
                    "pathCompleted": False,
                    "pathingBudgetExceeded": True,
                    "reason": "pathing_budget_exceeded",
                    "predictedPathTiles": [],
                },
            )
        )

        payload = proposal.to_dict()
        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_kind, "path_tile")
        self.assertFalse(proposal.executable)
        self.assertEqual(payload["actionTargetSource"], "initialFullPlaneScan")
        self.assertEqual(payload["actionability"], "needs_live_projection")
        self.assertIn("click_point", payload["missingCapabilities"])

    def test_service_needed_by_phase_and_free_slots_outranks_post_bank_resource_target(self):
        proposal = build_action_proposal(
            status_for(
                phase="inventory_full",
                active_intent="needs_service",
                inventory_full=False,
                free_slots=0,
                active_target={
                    "targetName": "Oak tree",
                    "name": "Oak tree",
                    "classId": "tree",
                    "targetType": "sceneObject",
                    "actions": ["Chop down"],
                    "aimPoint": aim(430, 220),
                },
                service={"serviceNeeded": True, "serviceReady": False},
                pathing={
                    "pathingNeeded": True,
                    "pathCompleted": False,
                    "nextWaypointTile": {"worldX": 3201, "worldY": 3200, "plane": 0},
                    "nextWaypointAimPoint": aim(260, 280),
                },
                bank_ui={"bankOpen": False},
                bank_operation={"operationNeeded": False, "bankingComplete": True, "resourceItemsHeld": 0},
                resource_return={"resourceTargetCurrentlyVisible": True},
            )
        )

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_kind, "path_tile")
        self.assertEqual(proposal.reason, "pathing_to_service")

    def test_service_needed_by_phase_and_free_slots_ignores_stale_return_route(self):
        proposal = build_action_proposal(
            status_for(
                phase="inventory_full",
                active_intent="needs_service",
                inventory_full=False,
                free_slots=0,
                active_target={
                    "targetName": "Lumbridge Castle west approach return",
                    "classId": "resource_return",
                    "targetType": "tile",
                    "aimPoint": aim(700, 710),
                },
                service={"serviceNeeded": True, "serviceReady": False},
                pathing={
                    "pathingNeeded": True,
                    "pathCompleted": False,
                    "nextWaypointTile": {"worldX": 3201, "worldY": 3200, "plane": 0},
                    "nextWaypointAimPoint": aim(260, 280),
                },
                bank_ui={"bankOpen": False},
                bank_operation={"operationNeeded": False, "bankingComplete": True, "resourceItemsHeld": 0},
                resource_return={
                    "returnDestinationAvailable": True,
                    "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                },
                return_route={
                    "schema": "return_route_context.v1",
                    "returnRouteId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                    "state": "return_route_ready",
                    "currentNavigationTarget": {"worldX": 3203, "worldY": 3238, "plane": 0},
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_kind, "path_tile")
        self.assertEqual(proposal.reason, "pathing_to_service")

    def test_static_route_prior_is_advisory_not_executable(self):
        proposal = build_action_proposal(
            status_for(
                phase="return_to_resource",
                active_intent="return_to_resource_area",
                inventory_full=False,
                free_slots=28,
                active_target=None,
                bank_operation={"operationNeeded": False, "bankingComplete": True, "resourceItemsHeld": 0},
                resource_return={
                    "returnDestinationAvailable": True,
                    "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                    "resourceTargetCurrentlyVisible": False,
                },
                return_route={
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
                },
                pathing={},
                bank_ui={"bankOpen": False},
            )
        )

        payload = proposal.to_dict()
        self.assertEqual(proposal.proposed_action, "return_to_resource_area")
        self.assertEqual(proposal.target_kind, "path_tile")
        self.assertFalse(proposal.executable)
        self.assertEqual(payload["actionTargetSource"], "static_route_prior")
        self.assertEqual(payload["actionability"], "advisory_only")
        self.assertEqual(proposal.target_explanation["advisoryTargetSource"], "static_route_prior")

    def test_static_route_prior_can_be_replaced_by_local_frontier_waypoint(self):
        proposal = build_action_proposal(
            status_for(
                phase="return_to_resource",
                active_intent="return_to_resource_area",
                inventory_full=False,
                free_slots=28,
                active_target=None,
                bank_operation={"operationNeeded": False, "bankingComplete": True, "resourceItemsHeld": 0},
                resource_return={
                    "returnDestinationAvailable": True,
                    "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                    "resourceTargetCurrentlyVisible": False,
                },
                return_route={
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
                },
                pathing={
                    "pathingNeeded": True,
                    "nextWaypointTile": {"worldX": 3200, "worldY": 3239, "plane": 0},
                    "predictedPathTiles": [
                        {"worldX": 3200, "worldY": 3239, "plane": 0},
                        {"worldX": 3201, "worldY": 3239, "plane": 0},
                        {"worldX": 3202, "worldY": 3238, "plane": 0},
                    ],
                },
                bank_ui={"bankOpen": False},
            )
        )

        payload = proposal.to_dict()
        self.assertEqual(proposal.proposed_action, "return_to_resource_area")
        self.assertTrue(proposal.executable)
        self.assertEqual(proposal.target_tile, {"worldX": 3202, "worldY": 3238, "plane": 0})
        self.assertEqual(payload["actionTargetSource"], "local_frontier_waypoint")
        self.assertEqual(payload["actionability"], "needs_live_projection")
        self.assertEqual(proposal.target_explanation["advisoryTargetSource"], "static_route_prior")

    def test_arrived_route_destination_uses_route_guide_before_stale_reverse_waypoint(self):
        status = status_for(
            phase="needs_service",
            active_intent="needs_service",
            inventory_full=True,
            free_slots=0,
            active_target=None,
            service={"serviceNeeded": True, "serviceReady": False},
            pathing={
                "pathingNeeded": True,
                "destinationTile": {"worldX": 3203, "worldY": 3238, "plane": 0},
                "pathTargetTile": {"worldX": 3203, "worldY": 3238, "plane": 0},
                "predictedPathTiles": [
                    {"worldX": 3207, "worldY": 3238, "plane": 0},
                    {"worldX": 3206, "worldY": 3238, "plane": 0},
                    {"worldX": 3205, "worldY": 3238, "plane": 0},
                    {"worldX": 3204, "worldY": 3238, "plane": 0},
                    {"worldX": 3203, "worldY": 3238, "plane": 0},
                ],
            },
        )
        status["playerWorldPosition"] = {"worldX": 3203, "worldY": 3238, "plane": 0}
        status["contextActionProposal"] = {
            "schema": "action_proposal.v1",
            "status": "PASS",
            "proposedAction": "navigate_to_service",
            "targetKind": "path_tile",
            "targetName": "Lumbridge Castle west approach",
            "targetTile": {"worldX": 3203, "worldY": 3238, "plane": 0},
            "reason": "pathing_to_service",
            "confidence": 0.74,
            "requiredContext": ["pathing"],
            "actionTargetSource": "local_frontier_waypoint",
            "actionability": "needs_live_projection",
            "targetExplanation": {
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "routeStepIndex": 0,
                "routeStepType": "navigate_world",
                "destinationTile": {"worldX": 3203, "worldY": 3238, "plane": 0},
                "pathTargetTile": {"worldX": 3203, "worldY": 3238, "plane": 0},
                "predictedPathTiles": [
                    {"worldX": 3207, "worldY": 3238, "plane": 0},
                    {"worldX": 3206, "worldY": 3238, "plane": 0},
                    {"worldX": 3205, "worldY": 3238, "plane": 0},
                    {"worldX": 3204, "worldY": 3238, "plane": 0},
                    {"worldX": 3203, "worldY": 3238, "plane": 0},
                ],
                "routeWaypointSelection": {
                    "schema": "route_waypoint_selection.v1",
                    "reason": "long_visible_route_progress",
                    "selectedTile": {"worldX": 3203, "worldY": 3238, "plane": 0},
                    "nextWaypointTile": {"worldX": 3207, "worldY": 3238, "plane": 0},
                },
            },
        }

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertTrue(proposal.executable)
        self.assertNotEqual(proposal.target_tile, {"worldX": 3203, "worldY": 3238, "plane": 0})
        self.assertEqual(proposal.target_tile, {"worldX": 3208, "worldY": 3212, "plane": 0})
        self.assertEqual(proposal.actionability, "needs_live_projection")
        selection = proposal.target_explanation["routeWaypointSelection"]
        self.assertEqual(selection["reason"], "arrived_waypoint_advanced_by_demonstrated_route_guide")
        self.assertEqual(selection["playerTile"], {"worldX": 3203, "worldY": 3238, "plane": 0})
        self.assertNotEqual(selection.get("selectedTile"), {"worldX": 3207, "worldY": 3238, "plane": 0})
        self.assertTrue(proposal.target_explanation["routeGuideLoaded"])
        self.assertEqual(proposal.target_explanation["routeGuideName"], "woodcutting_area_to_bank")

    def test_route_guide_interaction_preferred_over_cross_plane_path_point(self):
        status = status_for(
            phase="needs_service",
            active_intent="needs_service",
            inventory_full=True,
            free_slots=0,
            active_target=None,
            service={"serviceNeeded": True, "serviceReady": False},
            pathing={
                "pathingNeeded": True,
                "destinationTile": {"worldX": 3208, "worldY": 3212, "plane": 0},
                "pathTargetTile": {"worldX": 3208, "worldY": 3212, "plane": 0},
                "predictedPathTiles": [
                    {"worldX": 3208, "worldY": 3212, "plane": 0},
                    {"worldX": 3205, "worldY": 3209, "plane": 2},
                ],
            },
        )
        status["playerWorldPosition"] = {"worldX": 3208, "worldY": 3212, "plane": 0}
        status["contextActionProposal"] = {
            "schema": "action_proposal.v1",
            "status": "PASS",
            "proposedAction": "navigate_to_service",
            "targetKind": "path_tile",
            "targetName": "Castle bank approach waypoint",
            "targetTile": {"worldX": 3208, "worldY": 3212, "plane": 0},
            "reason": "pathing_to_service",
            "confidence": 0.74,
            "requiredContext": ["pathing"],
            "actionTargetSource": "local_frontier_waypoint",
            "actionability": "needs_live_projection",
            "targetExplanation": {
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "destinationTile": {"worldX": 3208, "worldY": 3212, "plane": 0},
                "pathTargetTile": {"worldX": 3208, "worldY": 3212, "plane": 0},
                "predictedPathTiles": [
                    {"worldX": 3208, "worldY": 3212, "plane": 0},
                    {"worldX": 3205, "worldY": 3209, "plane": 2},
                ],
            },
        }

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "wait_for_context")
        self.assertFalse(proposal.executable)
        self.assertEqual(proposal.reason, "route_guide_interaction_needs_live_target")
        self.assertIn("route.interaction.liveTarget", proposal.missing_capabilities)
        self.assertEqual(proposal.target_explanation["name"], "Trapdoor")
        self.assertEqual(proposal.target_explanation["routeGuideName"], "woodcutting_area_to_bank")

    def test_live_service_route_uses_guide_from_pathing_player_tile_and_service_route_id(self):
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={"serviceNeeded": True, "serviceReady": False},
                service_route={
                    "schema": "service_route_context.v1",
                    "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                    "routeStepStatus": "pathing_to_service",
                    "currentNavigationTarget": {
                        "targetName": "Lumbridge Castle entrance or ground-floor courtyard",
                        "worldX": 3205,
                        "worldY": 3232,
                        "plane": 0,
                        "source": "static_route_prior",
                    },
                },
                pathing={
                    "pathingNeeded": True,
                    "currentPlayerTile": {"worldX": 3202, "worldY": 3237, "plane": 0},
                    "nextWaypointTile": {"worldX": 3202, "worldY": 3237, "plane": 0},
                    "destinationTile": {"worldX": 3205, "worldY": 3232, "plane": 0},
                    "pathTargetTile": {"worldX": 3205, "worldY": 3232, "plane": 0},
                    "predictedPathTiles": [
                        {"worldX": 3202, "worldY": 3237, "plane": 0},
                        {"worldX": 3201, "worldY": 3236, "plane": 0},
                    ],
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_tile, {"worldX": 3208, "worldY": 3212, "plane": 0})
        self.assertEqual(proposal.action_target_source, "local_frontier_waypoint")
        self.assertTrue(proposal.target_explanation["routeGuideLoaded"])
        self.assertEqual(proposal.target_explanation["routeGuideName"], "woodcutting_area_to_bank")
        self.assertEqual(proposal.target_explanation["routeWaypointSelection"]["reason"], "demonstrated_route_guide_next_point")

    def test_inventory_full_without_route_context_uses_demonstrated_route_guide(self):
        status = status_for(
            phase="needs_service",
            active_intent="needs_service",
            inventory_full=True,
            free_slots=0,
            active_target=None,
            service={"serviceNeeded": True, "serviceReady": False},
            pathing={},
        )
        status["playerWorldPosition"] = {"worldX": 3201, "worldY": 3236, "plane": 0}

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.reason, "route_guide_progress_without_live_route_context")
        self.assertEqual(proposal.target_tile, {"worldX": 3208, "worldY": 3212, "plane": 0})
        self.assertEqual(proposal.target_explanation["routeGuideName"], "woodcutting_area_to_bank")
        self.assertIn("route_guide", proposal.required_context)

    def test_goal_directed_service_fallback_metadata_reaches_navigation_proposal(self):
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={"serviceNeeded": True, "serviceReady": False, "candidateCount": 0},
                service_route={
                    "schema": "service_route_context.v1",
                    "routeAvailable": True,
                    "routeMode": "goal_directed_fallback",
                    "goalDirectedFallback": True,
                    "routeStepStatus": "goal_directed_route_prior",
                    "currentNodeId": "lumbridge_castle_entrance_or_courtyard",
                    "currentNavigationTarget": {
                        "targetName": "Lumbridge Castle entrance or ground-floor courtyard",
                        "classId": "service_route_anchor",
                        "targetType": "service_route_anchor",
                        "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                        "routeMode": "goal_directed_fallback",
                        "goalDirectedFallback": True,
                        "worldX": 3205,
                        "worldY": 3232,
                        "plane": 0,
                        "selectedServiceAnchor": {"anchorId": "lumbridge_castle_bank"},
                        "selectedApproachNode": {"nodeId": "lumbridge_castle_entrance_or_courtyard"},
                        "routeSourceMismatch": {"classification": "route_source_mismatch"},
                    },
                },
                pathing={
                    "pathingNeeded": True,
                    "reason": "destination_outside_collision_window",
                    "routeMode": "goal_directed_fallback",
                    "goalDirectedFallback": True,
                    "nextWaypointTile": {"worldX": 3253, "worldY": 3240, "plane": 0},
                    "pathTargetTile": {"worldX": 3250, "worldY": 3238, "plane": 0},
                    "predictedPathTiles": [
                        {"worldX": 3253, "worldY": 3240, "plane": 0},
                        {"worldX": 3252, "worldY": 3239, "plane": 0},
                        {"worldX": 3250, "worldY": 3238, "plane": 0},
                    ],
                    "localFrontierWaypoint": {"worldX": 3250, "worldY": 3238, "plane": 0},
                    "frontierDistanceBefore": 49,
                    "frontierDistanceAfterEstimate": 45,
                    "progressScore": 4,
                },
                bank_ui={"bankOpen": False},
            )
        )

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_explanation["routeMode"], "goal_directed_fallback")
        self.assertTrue(proposal.target_explanation["goalDirectedFallback"])
        self.assertEqual(proposal.target_explanation["selectedServiceAnchor"]["anchorId"], "lumbridge_castle_bank")
        self.assertEqual(proposal.target_explanation["selectedApproachNode"]["nodeId"], "lumbridge_castle_entrance_or_courtyard")
        self.assertEqual(proposal.target_explanation["routeSourceMismatch"]["classification"], "route_source_mismatch")
        self.assertEqual(proposal.target_explanation["localFrontierWaypoint"], {"worldX": 3250, "worldY": 3238, "plane": 0})
        self.assertEqual(proposal.target_explanation["progressScore"], 4)

    def test_visible_service_route_transition_proposes_interaction_object(self):
        route_target = {
            "targetName": "Staircase",
            "classId": "service_route_transition",
            "targetType": "sceneObject",
            "id": 56230,
            "worldX": 3205,
            "worldY": 3229,
            "plane": 0,
            "aimPoint": aim(250, 260),
            "actions": ["Climb-up", "Top-floor"],
            "expectedOptions": ["Climb-up", "Climb up"],
            "expectedTargets": ["Stair", "Staircase"],
            "expectedPlaneChange": "+1",
            "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
            "routeStepIndex": 1,
        }
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={"serviceNeeded": True, "serviceReady": False, "candidateCount": 0},
                service_route={
                    "schema": "service_route_context.v1",
                    "routeAvailable": True,
                    "routeStepStatus": "route_interaction_visible",
                    "actionReady": True,
                    "visibleInteractionTarget": route_target,
                },
                bank_ui={"bankOpen": False},
            )
        )

        self.assertEqual(proposal.proposed_action, "interact_service_route_object")
        self.assertEqual(proposal.target_kind, "service_route_object")
        self.assertEqual(proposal.target_name, "Staircase")
        self.assertEqual(proposal.suggested_click_point, {"x": 250, "y": 260})
        self.assertIn("Climb-up", proposal.target_explanation["expectedOptions"])
        self.assertEqual(proposal.target_explanation["expectedPlaneChange"], "+1")

    def test_route_to_bank_rejects_ladder_not_on_expected_stair_segment(self):
        route_target = {
            "targetName": "Ladder",
            "classId": "service_route_transition",
            "targetType": "sceneObject",
            "id": 16683,
            "worldX": 3211,
            "worldY": 3242,
            "plane": 0,
            "aimPoint": aim(250, 260),
            "actions": ["Climb-up"],
            "routeId": "plugin_snapshot_route_to_service",
        }
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={"serviceNeeded": True, "serviceReady": False, "candidateCount": 0},
                service_route={
                    "schema": "service_route_context.v1",
                    "routeAvailable": True,
                    "routeStepStatus": "plugin_snapshot_route_transition_visible",
                    "actionReady": True,
                    "currentStep": {
                        "type": "interact_object",
                        "expectedOptions": ["Climb-up", "Top-floor"],
                        "expectedTargetContains": ["Stair", "Staircase"],
                        "planeChange": "+1",
                    },
                    "visibleInteractionTarget": route_target,
                },
                bank_ui={"bankOpen": False},
            )
        )

        self.assertEqual(proposal.proposed_action, "wait_for_context")
        self.assertFalse(proposal.executable)
        self.assertEqual(proposal.reason, "route_object_not_on_expected_segment")
        self.assertFalse(proposal.target_explanation["routeCorridorMatch"])
        self.assertIn("route_object_not_on_expected_segment", proposal.target_explanation["rejectedReasons"])

    def test_visible_service_route_transition_includes_safe_aimpoint_samples(self):
        route_target = {
            "targetName": "Staircase",
            "classId": "service_route_transition",
            "targetType": "sceneObject",
            "id": 56230,
            "worldX": 3205,
            "worldY": 3229,
            "plane": 0,
            "aimPoint": aim(250, 260),
            "clickboxBounds": bounds(230, 240, 40, 50),
            "actions": ["Climb-up", "Top-floor"],
            "expectedOptions": ["Climb-up", "Climb up"],
            "expectedTargets": ["Stair", "Staircase"],
            "expectedPlaneChange": "+1",
            "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
            "routeStepIndex": 3,
        }

        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={"serviceNeeded": True, "serviceReady": False, "candidateCount": 0},
                service_route={
                    "schema": "service_route_context.v1",
                    "routeAvailable": True,
                    "routeStepStatus": "route_interaction_visible",
                    "actionReady": True,
                    "visibleInteractionTarget": route_target,
                },
                bank_ui={"bankOpen": False},
            )
        )

        self.assertEqual(proposal.proposed_action, "interact_service_route_object")
        safe = proposal.target_explanation.get("safeAimPoint")
        self.assertIsInstance(safe, dict)
        self.assertEqual(safe.get("status"), "PASS")
        self.assertGreater(len(safe.get("sampledAimpoints") or []), 1)

    def test_offscreen_service_route_transition_triggers_view_recovery(self):
        route_target = {
            "targetName": "Staircase",
            "classId": "service_route_transition",
            "targetType": "sceneObject",
            "id": 56230,
            "worldX": 3204,
            "worldY": 3229,
            "plane": 0,
            "aimPoint": {"canvasX": 208, "canvasY": -4},
            "actions": ["Climb-up", "Top-floor"],
            "expectedOptions": ["Climb-up", "Climb up"],
            "expectedTargets": ["Stair", "Staircase"],
            "expectedPlaneChange": "+1",
            "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
            "routeStepIndex": 1,
            "routeStepType": "interact_object",
            "projectionStatus": {
                "geometryAvailable": True,
                "onScreen": False,
                "visible": False,
                "actionableByCanvas": False,
                "aimPoint": {"canvasX": 208, "canvasY": -4, "source": "canvasLocation"},
                "classification": "offscreen",
            },
            "routeRelevance": {"relevanceStatus": "PASS"},
        }
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={"serviceNeeded": True, "serviceReady": False, "candidateCount": 0},
                service_route={
                    "schema": "service_route_context.v1",
                    "routeAvailable": True,
                    "routeStepStatus": "route_interaction_visible",
                    "actionReady": True,
                    "visibleInteractionTarget": route_target,
                },
                bank_ui={"bankOpen": False},
                camera_viewport={
                    "canvasWidth": 765,
                    "canvasHeight": 503,
                    "viewportXOffset": 0,
                    "viewportYOffset": 0,
                    "viewportWidth": 765,
                    "viewportHeight": 503,
                    "cameraYaw": 32,
                    "cameraPitch": 383,
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "service_view_recovery")
        self.assertEqual(proposal.target_kind, "service_recovery")
        exposure = proposal.target_explanation["serviceTargetExposure"]
        self.assertTrue(exposure["serviceObjectActionRelevant"])
        self.assertTrue(exposure["shouldAttemptCameraExposure"])
        self.assertEqual(exposure["currentProjectionStatus"], "offscreen")

    def test_offscreen_route_census_transition_blocks_visible_random_door_fallback(self):
        route_census = {
            "topRouteObjects": [
                {
                    "name": "Staircase",
                    "routeObjectKind": "route_transition",
                    "routeRelevanceStatus": "PASS",
                    "matchedRouteStepIndex": 0,
                    "rejectionReason": "offscreen",
                    "source": "worldModelRouteObjectCensus",
                    "projectionStatus": {
                        "geometryAvailable": True,
                        "onScreen": False,
                        "visible": False,
                        "actionableByCanvas": False,
                        "aimPoint": {"canvasX": 59, "canvasY": 381, "source": "canvasLocation"},
                        "classification": "offscreen",
                    },
                    "routeRelevance": {"relevanceStatus": "PASS"},
                    "candidate": {
                        "targetName": "Staircase",
                        "classId": "route_transition",
                        "targetType": "sceneObject",
                        "id": 56231,
                        "worldX": 3205,
                        "worldY": 3229,
                        "plane": 2,
                        "actions": ["Climb-down", "Bottom-floor"],
                        "source": "world_model_cache",
                        "worldModelSource": True,
                    },
                },
                {
                    "name": "route_transition",
                    "routeObjectKind": "route_transition",
                    "routeRelevanceStatus": "FAIL",
                    "matchedRouteStepIndex": None,
                    "rejectionReason": "randomTransitionObject",
                    "projectionStatus": {
                        "geometryAvailable": True,
                        "onScreen": True,
                        "visible": True,
                        "actionableByCanvas": True,
                        "aimPoint": {"canvasX": 318, "canvasY": 97, "source": "canvasLocation"},
                    },
                    "routeRelevance": {"relevanceStatus": "FAIL", "rejectionReason": "randomTransitionObject"},
                    "candidate": {
                        "targetName": "route_transition",
                        "classId": "route_transition",
                        "targetType": "sceneObject",
                        "id": 27270,
                        "worldX": 3210,
                        "worldY": 3216,
                        "plane": 2,
                        "actions": ["Open", "Toggle XP"],
                    },
                },
            ]
        }
        status = status_for(
            phase="needs_service",
            active_intent="needs_service",
            inventory_full=True,
            free_slots=0,
            active_target=None,
            service={"serviceNeeded": True, "serviceReady": False, "candidateCount": 0},
            service_route={
                "schema": "service_route_context.v1",
                "routeAvailable": True,
                "routeStepStatus": "route_anchor_missing",
                "actionReady": False,
                "currentStepIndex": 0,
                "currentStep": {
                    "type": "interact_object",
                    "label": "bank floor stairs down",
                    "expectedOptions": ["Climb-down", "Climb down"],
                    "dialogueOpenerOptions": ["Climb"],
                    "expectedTargetContains": ["Stair", "Staircase", "Ladder"],
                    "planeChange": "-1",
                },
                "routeObjectCensus": route_census,
            },
            pathing={
                "pathingNeeded": True,
                "destinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
            },
            camera_viewport={
                "canvasWidth": 765,
                "canvasHeight": 503,
                "viewportXOffset": 0,
                "viewportYOffset": 0,
                "viewportWidth": 765,
                "viewportHeight": 503,
                "cameraYaw": 32,
                "cameraPitch": 383,
            },
        )
        status["playerLocation"] = {"worldX": 3208, "worldY": 3220, "plane": 2}
        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "service_view_recovery")
        self.assertEqual(proposal.reason, "service_route_transition_view_recovery_needed")
        self.assertEqual(proposal.target_name, "Staircase")
        exposure = proposal.target_explanation["serviceTargetExposure"]
        self.assertTrue(exposure["shouldAttemptCameraExposure"])
        self.assertEqual(exposure["currentProjectionStatus"], "offscreen")

    def test_offscreen_route_census_transition_with_waypoint_navigates_first(self):
        route_census = {
            "topRouteObjects": [
                {
                    "name": "Staircase",
                    "routeObjectKind": "route_transition",
                    "routeRelevanceStatus": "PASS",
                    "matchedRouteStepIndex": 0,
                    "rejectionReason": "offscreen",
                    "source": "worldModelRouteObjectCensus",
                    "projectionStatus": {
                        "geometryAvailable": True,
                        "onScreen": False,
                        "visible": False,
                        "actionableByCanvas": False,
                        "aimPoint": {"canvasX": -332, "canvasY": -185, "source": "canvasLocation"},
                        "classification": "offscreen",
                    },
                    "routeRelevance": {"relevanceStatus": "PASS"},
                    "candidate": {
                        "targetName": "Staircase",
                        "classId": "route_transition",
                        "targetType": "sceneObject",
                        "id": 56230,
                        "worldX": 3204,
                        "worldY": 3229,
                        "plane": 0,
                        "actions": ["Climb-up", "Top-floor"],
                        "source": "world_model_cache",
                        "worldModelSource": True,
                    },
                }
            ]
        }
        next_waypoint = {"worldX": 3233, "worldY": 3231, "plane": 0}
        status = status_for(
            phase="needs_service",
            active_intent="needs_service",
            inventory_full=True,
            free_slots=0,
            active_target=None,
            service={"serviceNeeded": True, "serviceReady": False, "candidateCount": 0},
            service_route={
                "schema": "service_route_context.v1",
                "routeAvailable": True,
                "routeStepStatus": "goal_directed_route_prior",
                "actionReady": False,
                "currentStepIndex": 8,
                "currentStep": {"type": "navigate_world", "label": "Lumbridge Castle approach"},
                "routeObjectCensus": route_census,
            },
            pathing={
                "pathingNeeded": True,
                "nextWaypointTile": next_waypoint,
                "nextWaypointAimPoint": aim(255, 201),
                "destinationTile": {"worldX": 3221, "worldY": 3218, "plane": 0},
                "pathTargetTile": {"worldX": 3221, "worldY": 3218, "plane": 0},
            },
            camera_viewport={
                "canvasWidth": 765,
                "canvasHeight": 503,
                "viewportXOffset": 0,
                "viewportYOffset": 0,
                "viewportWidth": 765,
                "viewportHeight": 503,
                "cameraYaw": 32,
                "cameraPitch": 383,
            },
        )
        status["playerLocation"] = {"worldX": 3233, "worldY": 3232, "plane": 0}
        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.reason, "pathing_to_service")
        self.assertEqual(proposal.target_kind, "path_tile")
        self.assertEqual(proposal.target_tile, next_waypoint)
        self.assertEqual(proposal.target_explanation["actionTargetSource"], "local_frontier_waypoint")

    def test_route_ready_bank_service_target_proposes_open_service(self):
        service_target = {
            "targetName": "Bank booth",
            "classId": "bank_booth",
            "targetType": "sceneObject",
            "id": 18491,
            "worldX": 3208,
            "worldY": 3220,
            "plane": 2,
            "aimPoint": aim(330, 240),
            "clickboxBounds": bounds(300, 210, 60, 60),
            "actions": ["Bank", "Collect"],
            "expectedOptions": ["Bank", "Use"],
            "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
            "routeStepIndex": 5,
            "routeStepType": "service_interact",
            "routeRelevance": {"relevanceStatus": "PASS"},
        }
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={"serviceNeeded": True, "serviceReady": False, "candidateCount": 1},
                service_route={
                    "schema": "service_route_context.v1",
                    "routeAvailable": True,
                    "routeStepStatus": "service_target_actionable",
                    "currentStepIndex": 5,
                    "currentStep": {"type": "service_interact", "label": "Lumbridge Castle bank", "expectedOptions": ["Bank", "Use"]},
                    "actionReady": True,
                    "visibleServiceTarget": service_target,
                },
                bank_ui={"bankOpen": False},
            )
        )

        self.assertEqual(proposal.proposed_action, "open_service")
        self.assertEqual(proposal.target_kind, "service")
        self.assertEqual(proposal.target_name, "Bank booth")
        self.assertEqual(proposal.suggested_click_point, {"x": 330, "y": 240})
        self.assertIn("Bank", proposal.target_explanation["expectedOptions"])
        self.assertEqual(proposal.target_explanation["safeAimPoint"]["status"], "PASS")
        self.assertGreaterEqual(len(proposal.target_explanation["safeAimPoint"]["sampledAimpoints"]), 3)

    def test_route_ready_offscreen_service_target_triggers_service_view_recovery(self):
        service_target = {
            "targetName": "Bank Deposit Box",
            "classId": "deposit_box",
            "targetType": "sceneObject",
            "id": 27291,
            "worldX": 3209,
            "worldY": 3221,
            "plane": 2,
            "aimPoint": aim(1793, 1935),
            "actions": ["Bank", "Collect", "Deposit-box"],
            "expectedOptions": ["Bank", "Use", "Deposit"],
            "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
            "routeStepIndex": 5,
            "routeStepType": "service_interact",
            "projectionStatus": {
                "geometryAvailable": True,
                "onScreen": False,
                "visible": False,
                "actionableByCanvas": False,
                "aimPoint": {"canvasX": 1793, "canvasY": 1935, "source": "canvasLocation"},
                "classification": "offscreen",
            },
            "routeRelevance": {"relevanceStatus": "PASS"},
        }
        status = status_for(
            phase="needs_service",
            active_intent="needs_service",
            inventory_full=True,
            free_slots=0,
            active_target=None,
            service={"serviceNeeded": True, "serviceReady": False, "candidateCount": 1},
            service_route={
                "schema": "service_route_context.v1",
                "routeAvailable": True,
                "routeStepStatus": "service_target_actionable",
                "currentStepIndex": 5,
                "currentStep": {"type": "service_interact", "label": "Lumbridge Castle bank", "expectedOptions": ["Bank", "Use"]},
                "actionReady": True,
                "visibleServiceTarget": service_target,
            },
            bank_ui={"bankOpen": False},
            camera_viewport={
                "canvasWidth": 765,
                "canvasHeight": 503,
                "viewportXOffset": 0,
                "viewportYOffset": 0,
                "viewportWidth": 765,
                "viewportHeight": 503,
                "cameraYaw": 32,
                "cameraPitch": 383,
            },
        )
        status["playerLocation"] = {"worldX": 3206, "worldY": 3229, "plane": 2}
        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "service_view_recovery")
        self.assertEqual(proposal.target_kind, "service_recovery")
        self.assertFalse(proposal.suggested_click_point)
        self.assertEqual(proposal.key_action["type"], "camera_reacquire")
        self.assertEqual(proposal.key_action["command"], "yaw_right_pitch_up")
        exposure = proposal.target_explanation["serviceTargetExposure"]
        self.assertEqual(exposure["serviceTargetKind"], "deposit_box")
        self.assertTrue(exposure["shouldAttemptCameraExposure"])
        self.assertEqual(exposure["currentProjectionStatus"], "offscreen")
        self.assertEqual(exposure["finalDecision"], "service_view_recovery")
        self.assertEqual(exposure["targetBearing"], 907)
        self.assertEqual(exposure["yawErrorToTarget"], 875)
        self.assertEqual(exposure["targetViewState"]["schema"], "target_view_state.v1")

    def test_service_target_edge_sliver_triggers_service_view_recovery(self):
        service_target = {
            "targetName": "Bank Deposit Box",
            "classId": "deposit_box",
            "targetType": "sceneObject",
            "id": 27291,
            "worldX": 3210,
            "worldY": 3217,
            "plane": 2,
            "aimPoint": {"canvasX": 6, "canvasY": 93},
            "bounds": {"x": -1, "y": 78, "width": 28, "height": 30},
            "actions": ["Bank", "Use", "Deposit"],
            "expectedOptions": ["Bank", "Use", "Deposit"],
            "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
            "routeStepIndex": 5,
            "routeStepType": "service_interact",
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
            "routeRelevance": {"relevanceStatus": "PASS"},
        }
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={"serviceNeeded": True, "serviceReady": False, "candidateCount": 1},
                service_route={
                    "schema": "service_route_context.v1",
                    "routeAvailable": True,
                    "routeStepStatus": "service_target_actionable",
                    "currentStepIndex": 5,
                    "currentStep": {"type": "service_interact", "label": "Lumbridge Castle bank", "expectedOptions": ["Bank", "Use"]},
                    "actionReady": True,
                    "visibleServiceTarget": service_target,
                },
                bank_ui={"bankOpen": False},
                camera_viewport={"canvasWidth": 765, "canvasHeight": 503, "viewportXOffset": 0, "viewportYOffset": 0, "viewportWidth": 765, "viewportHeight": 503},
            )
        )

        self.assertEqual(proposal.proposed_action, "service_view_recovery")
        exposure = proposal.target_explanation["serviceTargetExposure"]
        self.assertTrue(exposure["edgeSliverVisible"])
        self.assertFalse(exposure["usableExposureThresholdMet"])
        self.assertEqual(exposure["exposureResult"], "edge_sliver_only")
        self.assertEqual(exposure["cameraExposureReason"], "service_object_edge_sliver")
        self.assertEqual(exposure["finalDecision"], "service_view_recovery")

    def test_service_target_below_world_model_viewport_triggers_recovery(self):
        service_target = {
            "targetName": "Bank booth",
            "classId": "bank_related",
            "targetType": "sceneObject",
            "id": 27291,
            "worldX": 3209,
            "worldY": 3221,
            "plane": 2,
            "aimPoint": {"canvasX": 412, "canvasY": 350},
            "clickboxBounds": {"x": 388, "y": 330, "width": 48, "height": 40},
            "actions": ["Bank", "Use", "Deposit"],
            "expectedOptions": ["Bank", "Use", "Deposit"],
            "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
            "routeStepIndex": 5,
            "routeStepType": "service_interact",
            "routeRelevance": {"relevanceStatus": "PASS"},
        }
        status = status_for(
            phase="needs_service",
            active_intent="needs_service",
            inventory_full=True,
            free_slots=0,
            active_target=None,
            service={"serviceNeeded": True, "serviceReady": False, "candidateCount": 1},
            service_route={
                "schema": "service_route_context.v1",
                "routeAvailable": True,
                "routeStepStatus": "service_target_actionable",
                "currentStepIndex": 5,
                "currentStep": {"type": "service_interact", "label": "Lumbridge Castle bank", "expectedOptions": ["Bank", "Use"]},
                "actionReady": True,
                "visibleServiceTarget": service_target,
            },
            bank_ui={"bankOpen": False},
        )
        status["worldModelCameraViewport"] = {
            "viewportWidth": 512,
            "viewportHeight": 334,
            "viewportXOffset": 4,
            "viewportYOffset": 4,
            "canvasWidth": 765,
            "canvasHeight": 503,
        }

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "service_view_recovery")
        self.assertFalse(proposal.suggested_click_point)
        safe = proposal.target_explanation["safeAimPoint"]
        self.assertEqual(safe["source"], "clippedClickboxInterior")
        self.assertFalse(safe["rawCenterInsideViewport"])
        exposure = proposal.target_explanation["serviceTargetExposure"]
        self.assertTrue(exposure["edgeSliverVisible"])
        self.assertFalse(exposure["usableExposureThresholdMet"])
        self.assertEqual(exposure["finalDecision"], "service_view_recovery")

    def test_hover_confirmed_route_object_intercepts_waypoint_navigation(self):
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={"serviceNeeded": True, "serviceReady": False, "candidateCount": 0},
                service_route={
                    "schema": "service_route_context.v1",
                    "routeAvailable": True,
                    "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                    "routeStepStatus": "first_stairs_search_area",
                    "actionReady": False,
                    "currentStep": {
                        "type": "interact_object",
                        "label": "first stairs up",
                        "expectedOptions": ["Climb-up", "Climb up", "Top-floor"],
                        "expectedTargetContains": ["Stair", "Staircase", "Ladder"],
                        "planeChange": "+1",
                    },
                    "routeSteps": [
                        {"type": "navigate_world", "label": "Lumbridge Castle approach"},
                        {
                            "type": "interact_object",
                            "label": "first stairs up",
                            "expectedOptions": ["Climb-up", "Climb up", "Top-floor"],
                            "expectedTargetContains": ["Stair", "Staircase", "Ladder"],
                            "planeChange": "+1",
                        },
                    ],
                },
                pathing={
                    "pathingNeeded": True,
                    "nextWaypointTile": {"worldX": 3202, "worldY": 3239, "plane": 0},
                    "nextWaypointAimPoint": aim(260, 280),
                },
            )
            | {
                "clientTickHot": {
                    "schema": "client_tick_hot.v1",
                    "gameState": "LOGGED_IN",
                    "postMenuSort": {
                        "topOption": "Climb-up",
                        "topTarget": "Staircase",
                        "topType": "GAME_OBJECT_FIRST_OPTION",
                        "topIdentifier": 56230,
                        "mouseCanvasX": 431,
                        "mouseCanvasY": 214,
                    },
                    "latency": {"postMenuSortAgeMillis": 18},
                }
            }
        )

        self.assertEqual(proposal.proposed_action, "interact_service_route_object")
        self.assertEqual(proposal.target_kind, "service_route_object")
        self.assertEqual(proposal.target_name, "Staircase")
        self.assertEqual(proposal.suggested_click_point, {"x": 431, "y": 214})
        self.assertEqual(proposal.target_explanation["targetSource"], "client_tick_hot_hover")
        self.assertIn("Climb-up", proposal.target_explanation["expectedOptions"])

    def test_hover_confirmed_future_route_object_without_relevance_does_not_bypass_waypoint(self):
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={"serviceNeeded": True, "serviceReady": False, "candidateCount": 0},
                service_route={
                    "schema": "service_route_context.v1",
                    "routeAvailable": True,
                    "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                    "routeStepStatus": "static_route_prior",
                    "actionReady": False,
                    "currentStep": {"type": "navigate_world", "label": "Lumbridge Castle approach"},
                    "routeSteps": [
                        {"type": "navigate_world", "label": "Lumbridge Castle approach"},
                        {
                            "type": "interact_object",
                            "label": "first stairs up",
                            "expectedOptions": ["Climb-up", "Climb up", "Top-floor"],
                            "expectedTargetContains": ["Stair", "Staircase", "Ladder"],
                            "planeChange": "+1",
                        },
                    ],
                },
                pathing={
                    "pathingNeeded": True,
                    "nextWaypointTile": {"worldX": 3202, "worldY": 3239, "plane": 0},
                    "nextWaypointAimPoint": aim(260, 280),
                },
            )
            | {
                "clientTickHot": {
                    "schema": "client_tick_hot.v1",
                    "gameState": "LOGGED_IN",
                    "postMenuSort": {
                        "topOption": "Climb-up",
                        "topTarget": "Staircase",
                        "topType": "GAME_OBJECT_FIRST_OPTION",
                        "topIdentifier": 56230,
                        "mouseCanvasX": 431,
                        "mouseCanvasY": 214,
                    },
                    "latency": {"postMenuSortAgeMillis": 18},
                }
            }
        )

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_kind, "path_tile")

    def test_adaptive_route_waypoint_prefers_meaningful_progress_over_micro_step(self):
        predicted = [{"worldX": 3200 + step, "worldY": 3248 - step, "plane": 0} for step in range(1, 26)]
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={"serviceNeeded": True, "serviceReady": False},
                pathing={
                    "pathingNeeded": True,
                    "nextWaypointTile": predicted[0],
                    "predictedPathTiles": predicted,
                    "destinationTile": predicted[-1],
                    "routeWaypointDistanceMode": "adaptive",
                    "routeWaypointLookaheadTiles": 12,
                    "routeWaypointMaxHorizonTiles": 25,
                    "minRouteProgressTiles": 3,
                    "preferLongVisibleWaypoint": True,
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_tile, predicted[11])
        self.assertEqual(proposal.target_explanation["routeWaypointSelection"]["mode"], "adaptive")
        self.assertEqual(proposal.target_explanation["routeWaypointSelection"]["waypointDistanceTiles"], 12)

    def test_adaptive_service_route_does_not_treat_lookahead_distance_as_close_destination(self):
        predicted = [
            {"worldX": 3233, "worldY": 3226 - step, "plane": 0}
            for step in range(0, 7)
        ] + [
            {"worldX": 3232 - step, "worldY": 3219, "plane": 0}
            for step in range(0, 12)
        ]
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={"serviceNeeded": True, "serviceReady": False},
                pathing={
                    "pathingNeeded": True,
                    "nextWaypointTile": predicted[0],
                    "predictedPathTiles": predicted,
                    "destinationTile": predicted[-1],
                    "distanceToDestination": 12,
                    "routeWaypointDistanceMode": "adaptive",
                    "routeWaypointLookaheadTiles": 12,
                    "routeWaypointMaxHorizonTiles": 25,
                    "minRouteProgressTiles": 3,
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_tile, predicted[11])
        selection = proposal.target_explanation["routeWaypointSelection"]
        self.assertEqual(selection["reason"], "long_visible_route_progress")
        self.assertEqual(selection["directDistanceToDestination"], 12)
        self.assertEqual(selection["waypointDistanceTiles"], 12)

    def test_adaptive_route_waypoint_keeps_short_step_near_transition_geometry(self):
        predicted = [{"worldX": 3200 + step, "worldY": 3230, "plane": 0} for step in range(1, 12)]
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={"serviceNeeded": True, "serviceReady": False},
                pathing={
                    "pathingNeeded": True,
                    "nextWaypointTile": predicted[0],
                    "predictedPathTiles": predicted,
                    "destinationTile": predicted[-1],
                    "routeWaypointDistanceMode": "adaptive",
                    "routeWaypointLookaheadTiles": 12,
                    "routeWaypointMaxHorizonTiles": 25,
                    "routeWaypointNearTransition": True,
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_tile, predicted[0])
        self.assertEqual(proposal.target_explanation["routeWaypointSelection"]["reason"], "near_transition_precision")

    def test_adaptive_return_route_keeps_short_step_when_close_destination_detours(self):
        predicted = [
            {"worldX": 3205, "worldY": 3231, "plane": 0},
            {"worldX": 3204, "worldY": 3231, "plane": 0},
            {"worldX": 3203, "worldY": 3231, "plane": 0},
            {"worldX": 3203, "worldY": 3230, "plane": 0},
            {"worldX": 3203, "worldY": 3229, "plane": 0},
            {"worldX": 3203, "worldY": 3228, "plane": 0},
            {"worldX": 3203, "worldY": 3227, "plane": 0},
            {"worldX": 3203, "worldY": 3226, "plane": 0},
            {"worldX": 3203, "worldY": 3225, "plane": 0},
            {"worldX": 3203, "worldY": 3224, "plane": 0},
            {"worldX": 3203, "worldY": 3223, "plane": 0},
            {"worldX": 3203, "worldY": 3222, "plane": 0},
            {"worldX": 3203, "worldY": 3221, "plane": 0},
        ]
        route_target = {
            "targetName": "Lumbridge Castle west approach return",
            "targetType": "tile",
            "classId": "resource_return",
            "worldX": 3203,
            "worldY": 3238,
            "plane": 0,
            "source": "static_route_prior",
        }
        proposal = build_action_proposal(
            status_for(
                phase="return_to_resource",
                active_intent="return_to_resource_area",
                active_target=route_target,
                service={"serviceNeeded": True, "serviceRequired": True, "serviceReady": False},
                return_route={
                    "state": "return_route_ready",
                    "routeAvailable": True,
                    "returnActionReady": True,
                    "currentNavigationTarget": route_target,
                },
                pathing={
                    "pathingNeeded": True,
                    "nextWaypointTile": predicted[0],
                    "predictedPathTiles": predicted,
                    "destinationTile": {"worldX": 3203, "worldY": 3238, "plane": 0},
                    "pathTargetTile": {"worldX": 3203, "worldY": 3238, "plane": 0},
                    "distanceToDestination": 7,
                    "routeWaypointDistanceMode": "adaptive",
                    "routeWaypointLookaheadTiles": 12,
                    "routeWaypointMaxHorizonTiles": 25,
                    "minRouteProgressTiles": 3,
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "return_to_resource_area")
        self.assertEqual(proposal.target_tile, predicted[0])
        self.assertEqual(proposal.target_explanation["routeWaypointSelection"]["reason"], "close_destination_detour_precision")

    def test_adaptive_service_route_skips_arrived_current_waypoint(self):
        predicted = [
            {"worldX": 3204, "worldY": 3237, "plane": 0},
            {"worldX": 3203, "worldY": 3237, "plane": 0},
            {"worldX": 3202, "worldY": 3237, "plane": 0},
            {"worldX": 3201, "worldY": 3236, "plane": 0},
            {"worldX": 3200, "worldY": 3235, "plane": 0},
            {"worldX": 3200, "worldY": 3234, "plane": 0},
            {"worldX": 3200, "worldY": 3233, "plane": 0},
            {"worldX": 3200, "worldY": 3232, "plane": 0},
            {"worldX": 3200, "worldY": 3231, "plane": 0},
            {"worldX": 3200, "worldY": 3230, "plane": 0},
            {"worldX": 3200, "worldY": 3229, "plane": 0},
            {"worldX": 3200, "worldY": 3228, "plane": 0},
            {"worldX": 3200, "worldY": 3227, "plane": 0},
        ]
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={"serviceNeeded": True, "serviceReady": False},
                pathing={
                    "pathingNeeded": True,
                    "nextWaypointTile": predicted[0],
                    "predictedPathTiles": predicted,
                    "destinationTile": {"worldX": 3205, "worldY": 3232, "plane": 0},
                    "distanceToDestination": 5,
                    "routeWaypointDistanceMode": "adaptive",
                    "routeWaypointLookaheadTiles": 12,
                    "routeWaypointMaxHorizonTiles": 25,
                    "minRouteProgressTiles": 3,
                    "currentPlayerTile": predicted[0],
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_tile, predicted[1])
        self.assertEqual(
            proposal.target_explanation["routeWaypointSelection"]["reason"],
            "close_destination_current_waypoint_arrived_forward_step",
        )

    def test_adaptive_service_route_uses_live_player_world_position_to_skip_arrived_waypoint(self):
        predicted = [
            {"worldX": 3203, "worldY": 3237, "plane": 0},
            {"worldX": 3202, "worldY": 3237, "plane": 0},
            {"worldX": 3201, "worldY": 3236, "plane": 0},
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
        ]
        status = status_for(
            phase="needs_service",
            active_intent="needs_service",
            inventory_full=True,
            free_slots=0,
            active_target=None,
            service={"serviceNeeded": True, "serviceReady": False},
            pathing={
                "pathingNeeded": True,
                "nextWaypointTile": predicted[0],
                "predictedPathTiles": predicted,
                "destinationTile": {"worldX": 3205, "worldY": 3232, "plane": 0},
                "distanceToDestination": 5,
                "routeWaypointDistanceMode": "adaptive",
                "routeWaypointLookaheadTiles": 12,
                "routeWaypointMaxHorizonTiles": 25,
                "minRouteProgressTiles": 3,
            },
        )
        status["playerWorldPosition"] = predicted[0]

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_tile, predicted[1])
        self.assertEqual(
            proposal.target_explanation["routeWaypointSelection"]["reason"],
            "close_destination_current_waypoint_arrived_forward_step",
        )

    def test_context_route_proposal_advances_past_arrived_current_tile(self):
        current = {"worldX": 3203, "worldY": 3237, "plane": 0}
        next_tile = {"worldX": 3202, "worldY": 3237, "plane": 0}
        predicted = [
            current,
            next_tile,
            {"worldX": 3201, "worldY": 3236, "plane": 0},
        ]

        proposal = build_action_proposal(
            {
                "playerWorldPosition": current,
                "contextActionProposal": {
                    "proposedAction": "navigate_to_service",
                    "targetKind": "path_tile",
                    "targetName": "Castle bank approach waypoint",
                    "targetTile": current,
                    "suggestedWorldTile": current,
                    "suggestedClickPoint": {"x": 512, "y": 320},
                    "clickPointSpace": "screen",
                    "targetExplanation": {
                        "freshness": {"stale": True},
                        "predictedPathTiles": predicted,
                        "routeWaypointSelection": {
                            "reason": "close_destination_detour_precision",
                            "selectedTile": current,
                        },
                    },
                    "actionTargetSource": "live_projected_waypoint",
                    "actionability": "clickable",
                    "confidence": 0.72,
                },
            }
        )

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_tile, next_tile)
        self.assertEqual(proposal.suggested_world_tile, next_tile)
        self.assertIsNone(proposal.suggested_click_point)
        self.assertEqual(proposal.action_target_source, "local_frontier_waypoint")
        self.assertEqual(proposal.actionability, "needs_live_projection")
        self.assertTrue(proposal.executable)
        explanation = proposal.target_explanation
        self.assertTrue(explanation["waypointAlreadyReached"])
        self.assertTrue(explanation["routeStateStale"])
        self.assertTrue(explanation["livePositionFresh"])
        self.assertEqual(explanation["reconciliationMethod"], "playerWorldPosition_progress")
        selection = explanation["routeWaypointSelection"]
        self.assertEqual(selection["reason"], "context_current_waypoint_arrived_forward_step")
        self.assertEqual(selection["skippedWaypoint"], current)
        self.assertEqual(selection["nextWaypoint"], next_tile)

    def test_context_route_proposal_does_not_skip_same_xy_on_different_plane(self):
        current = {"worldX": 3203, "worldY": 3237, "plane": 1}
        target = {"worldX": 3203, "worldY": 3237, "plane": 0}
        next_tile = {"worldX": 3202, "worldY": 3237, "plane": 0}

        proposal = build_action_proposal(
            {
                "playerWorldPosition": current,
                "contextActionProposal": {
                    "proposedAction": "navigate_to_service",
                    "targetKind": "path_tile",
                    "targetName": "Castle bank approach waypoint",
                    "targetTile": target,
                    "suggestedWorldTile": target,
                    "suggestedClickPoint": {"x": 512, "y": 320},
                    "clickPointSpace": "screen",
                    "targetExplanation": {
                        "predictedPathTiles": [target, next_tile],
                        "routeWaypointSelection": {
                            "reason": "close_destination_detour_precision",
                            "selectedTile": target,
                        },
                    },
                    "actionTargetSource": "live_projected_waypoint",
                    "actionability": "clickable",
                    "confidence": 0.72,
                },
            }
        )

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_tile, target)
        self.assertEqual(proposal.suggested_click_point, {"x": 512, "y": 320})
        self.assertNotIn("waypointAlreadyReached", proposal.target_explanation)

    def test_adaptive_return_route_blocks_close_destination_sideways_detour(self):
        predicted = [
            {"worldX": 3206, "worldY": 3228, "plane": 0},
            {"worldX": 3207, "worldY": 3228, "plane": 0},
            {"worldX": 3208, "worldY": 3228, "plane": 0},
            {"worldX": 3209, "worldY": 3228, "plane": 0},
            {"worldX": 3210, "worldY": 3228, "plane": 0},
            {"worldX": 3211, "worldY": 3228, "plane": 0},
            {"worldX": 3212, "worldY": 3228, "plane": 0},
            {"worldX": 3213, "worldY": 3228, "plane": 0},
            {"worldX": 3214, "worldY": 3227, "plane": 0},
            {"worldX": 3215, "worldY": 3226, "plane": 0},
            {"worldX": 3215, "worldY": 3225, "plane": 0},
            {"worldX": 3215, "worldY": 3224, "plane": 0},
            {"worldX": 3215, "worldY": 3223, "plane": 0},
            {"worldX": 3215, "worldY": 3222, "plane": 0},
            {"worldX": 3215, "worldY": 3221, "plane": 0},
            {"worldX": 3215, "worldY": 3220, "plane": 0},
            {"worldX": 3215, "worldY": 3219, "plane": 0},
        ]
        route_target = {
            "targetName": "Lumbridge Castle entrance or ground-floor courtyard return",
            "targetType": "tile",
            "classId": "resource_return",
            "worldX": 3205,
            "worldY": 3232,
            "plane": 0,
            "source": "static_route_prior",
        }
        proposal = build_action_proposal(
            status_for(
                phase="return_to_resource",
                active_intent="return_to_resource_area",
                active_target=route_target,
                service={"serviceNeeded": True, "serviceRequired": True, "serviceReady": False},
                return_route={
                    "state": "return_route_ready",
                    "routeAvailable": True,
                    "returnActionReady": True,
                    "currentNavigationTarget": route_target,
                },
                pathing={
                    "pathingNeeded": True,
                    "nextWaypointTile": predicted[0],
                    "predictedPathTiles": predicted,
                    "destinationTile": {"worldX": 3205, "worldY": 3232, "plane": 0},
                    "pathTargetTile": {"worldX": 3205, "worldY": 3232, "plane": 0},
                    "distanceToDestination": 4,
                    "routeWaypointDistanceMode": "adaptive",
                    "routeWaypointLookaheadTiles": 12,
                    "routeWaypointMaxHorizonTiles": 25,
                    "minRouteProgressTiles": 3,
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "return_to_resource_area")
        self.assertFalse(proposal.executable)
        self.assertEqual(proposal.action_target_source, "route_detour_safety_block")
        self.assertEqual(proposal.target_explanation["actionability"], "blocked")
        self.assertEqual(
            proposal.target_explanation["routeWaypointSelection"]["reason"],
            "close_destination_detour_safety_block",
        )

    def test_suppressed_navigation_waypoint_selects_alternate_route_tile(self):
        predicted = [{"worldX": 3236 + step, "worldY": 3223, "plane": 0} for step in range(3)]
        status = status_for(
            phase="needs_service",
            active_intent="needs_service",
            inventory_full=True,
            free_slots=0,
            active_target=None,
            service={"serviceNeeded": True, "serviceReady": False},
            service_route={
                "currentNavigationTarget": {
                    "targetName": "Lumbridge Castle south entrance approach",
                    "classId": "service_route_anchor",
                }
            },
            pathing={
                "pathingNeeded": True,
                "nextWaypointTile": predicted[0],
                "predictedPathTiles": predicted,
                "destinationTile": predicted[-1],
                "routeWaypointDistanceMode": "adaptive",
                "routeWaypointNearTransition": True,
            },
        )
        status["suppressedActionTargetKeys"] = ["None:3236:3223:0:service_route_anchor"]
        status["brain"]["suppressedActionTargetKeys"] = ["None:3236:3223:0:service_route_anchor"]

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_tile, predicted[1])
        selection = proposal.target_explanation["routeWaypointSelection"]
        self.assertEqual(selection["reason"], "suppressed_waypoint_alternate")
        self.assertEqual(selection["suppressedWaypointTile"], predicted[0])
        self.assertIn("None:3236:3223:0:service_route_anchor", proposal.target_explanation["suppressedTargetKeysAtSelection"])

    def test_bank_readable_resources_held_deposit_inventory_available(self):
        proposal = build_action_proposal(
            status_for(
                phase="service_open",
                active_intent="bank_operation_pending",
                active_target=None,
                bank_ui={
                    "bankOpen": True,
                    "bankReadable": True,
                    "depositInventoryButtonVisible": True,
                    "depositInventoryButtonBounds": bounds(300, 400, 20, 10),
                },
                bank_operation={
                    "operationNeeded": True,
                    "operationType": "deposit_inventory",
                    "resourceItemsHeld": 18,
                    "depositInventoryAvailable": True,
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "deposit_inventory")
        self.assertEqual(proposal.target_kind, "bank_ui")
        self.assertEqual(proposal.suggested_click_point, {"x": 310, "y": 405})

    def test_banking_complete_true_does_not_suppress_known_held_resources(self):
        proposal = build_action_proposal(
            status_for(
                phase="service_open",
                active_intent="bank_operation_pending",
                active_target=None,
                bank_ui={
                    "bankOpen": True,
                    "bankReadable": True,
                    "depositInventoryButtonVisible": True,
                    "depositInventoryButtonBounds": bounds(300, 400, 20, 10),
                },
                bank_operation={
                    "operationNeeded": False,
                    "operationType": "none",
                    "bankingComplete": True,
                    "resourceItemsHeld": 3,
                    "depositInventoryAvailable": True,
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "deposit_inventory")
        self.assertEqual(proposal.reason, "deposit_inventory_available")

    def test_bank_readable_non_resource_item_uses_selective_resource_deposit(self):
        proposal = build_action_proposal(
            status_for(
                phase="service_open",
                active_intent="bank_operation_pending",
                active_target=None,
                bank_ui={
                    "bankOpen": True,
                    "bankReadable": True,
                    "depositInventoryButtonVisible": True,
                    "depositInventoryButtonBounds": bounds(300, 400, 20, 10),
                },
                bank_operation={
                    "operationNeeded": True,
                    "operationType": "deposit_inventory",
                    "resourceItemsHeld": 18,
                    "nonResourceItemsHeld": 1,
                    "depositInventoryAvailable": True,
                    "resourceItemSlotBounds": [bounds(500, 600, 28, 28)],
                    "resourceItemWidgets": [{"slot": 9, "itemId": 1511, "quantity": 1, "actions": ["Deposit-1", "Deposit-All"], "bounds": bounds(500, 600, 28, 28)}],
                    "resourceDisplayName": "logs",
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "deposit_resources")
        self.assertEqual(proposal.reason, "protected_non_resource_items_present")
        self.assertEqual(proposal.target_name, "logs")
        self.assertEqual(proposal.suggested_click_point, {"x": 514, "y": 614})
        self.assertEqual(proposal.target_explanation["expectedOptions"], ["Deposit"])
        self.assertIn("Logs", proposal.target_explanation["expectedTargets"])
        self.assertIn("protected item", " ".join(proposal.warnings))

    def test_bank_readable_resources_held_without_deposit_inventory_deposit_resources(self):
        proposal = build_action_proposal(
            status_for(
                phase="service_open",
                active_intent="bank_operation_pending",
                active_target=None,
                bank_ui={"bankOpen": True, "bankReadable": True},
                bank_operation={
                    "operationNeeded": True,
                    "operationType": "deposit_resources",
                    "resourceItemsHeld": 3,
                    "depositInventoryAvailable": False,
                    "resourceItemSlotBounds": [bounds(500, 600, 28, 28)],
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "deposit_resources")
        self.assertEqual(proposal.target_kind, "bank_ui")
        self.assertEqual(proposal.suggested_click_point, {"x": 514, "y": 614})

    def test_banking_complete_close_ready_proposes_close_bank_keyboard_first(self):
        proposal = build_action_proposal(
            status_for(
                phase="waiting_for_world_view",
                active_intent="close_service_context",
                active_target=None,
                bank_ui={"bankOpen": True, "bankReadable": True},
                bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
                close_bank={"closeBankNeeded": True, "closeBankReady": True, "keyboardClosePossible": True},
            )
        )

        self.assertEqual(proposal.proposed_action, "close_bank")
        self.assertEqual(proposal.key_action, {"type": "key_press", "key": "escape"})

    def test_banking_complete_close_ready_ignores_stale_service_needed_signal(self):
        proposal = build_action_proposal(
            status_for(
                phase="waiting_for_world_view",
                active_intent="close_service_context",
                active_target=None,
                free_slots=15,
                service={
                    "serviceNeeded": True,
                    "serviceReady": True,
                    "bestServiceCandidate": {
                        "targetName": "Bank Deposit Box",
                        "classId": "bank_related",
                        "aimPoint": aim(388, 220),
                    },
                },
                bank_ui={"bankOpen": True, "bankReadable": True},
                bank_operation={
                    "operationNeeded": False,
                    "operationType": "none",
                    "bankingComplete": True,
                    "resourceItemsHeld": 0,
                    "resourceItemQuantity": 0,
                },
                close_bank={
                    "closeBankNeeded": True,
                    "closeBankReady": True,
                    "keyboardClosePossible": True,
                },
                service_route={
                    "schema": "service_route_context.v1",
                    "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                    "currentNodeId": "lumbridge_castle_bank",
                    "routeStepStatus": "service_target_actionable",
                    "actionReady": True,
                    "visibleServiceTarget": {
                        "targetName": "Bank Deposit Box",
                        "classId": "bank_related",
                        "targetType": "sceneObject",
                        "aimPoint": aim(388, 220),
                        "actions": ["Bank", "Use", "Deposit"],
                    },
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "close_bank")
        self.assertEqual(proposal.reason, "close_bank_ready")

    def test_banking_complete_bank_closed_does_not_reopen_service(self):
        proposal = build_action_proposal(
            status_for(
                phase="service_ready",
                active_intent="observe_service_context",
                active_target=None,
                bank_ui={"bankOpen": False, "bankReadable": False},
                bank_operation={"operationNeeded": False, "bankingComplete": True, "resourceItemsHeld": 0},
                service={"serviceReady": True, "selectedServiceTargetName": "Bank booth"},
                pathing={"serviceReady": True},
            )
        )

        self.assertEqual(proposal.proposed_action, "wait_for_context")
        self.assertEqual(proposal.reason, "service_complete_waiting_for_return_context")
        self.assertIsNone(proposal.suggested_click_point)

    def test_banking_complete_route_service_target_does_not_reopen_service(self):
        proposal = build_action_proposal(
            status_for(
                phase="return_to_resource",
                active_intent="return_to_resource_area",
                active_target={"targetName": "", "classId": "none"},
                bank_ui={"bankOpen": False, "bankReadable": False},
                bank_operation={
                    "operationNeeded": False,
                    "operationType": "none",
                    "bankingComplete": True,
                    "resourceItemsHeld": 0,
                    "resourceItemQuantity": 0,
                },
                service_route={
                    "schema": "service_route_context.v1",
                    "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                    "currentNodeId": "lumbridge_castle_bank",
                    "routeStepStatus": "service_target_visible",
                    "actionReady": True,
                    "visibleServiceTarget": {
                        "targetName": "Bank Deposit Box",
                        "classId": "bank_related",
                        "targetType": "sceneObject",
                        "aimPoint": aim(324, 220),
                        "actions": ["Bank", "Use", "Deposit"],
                    },
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "wait_for_context")
        self.assertEqual(proposal.reason, "service_complete_waiting_for_return_context")
        self.assertIsNone(proposal.suggested_click_point)

    def test_valid_return_destination_proposes_return_to_resource_area(self):
        proposal = build_action_proposal(
            status_for(
                phase="return_to_resource",
                active_intent="return_to_resource_area",
                active_target={"targetName": "Resource return", "classId": "resource_return", "aimPoint": aim(700, 710)},
                bank_ui={"bankOpen": False},
                bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
                resource_return={
                    "returnDestinationAvailable": True,
                    "returnDestinationTile": {"worldX": 3156, "worldY": 3237, "plane": 0},
                    "reason": "using_remembered_resource_area",
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "return_to_resource_area")
        self.assertEqual(proposal.target_kind, "path_tile")
        self.assertEqual(proposal.suggested_click_point, {"x": 700, "y": 710})

    def test_return_intent_without_post_bank_context_uses_pathing_waypoint(self):
        proposal = build_action_proposal(
            status_for(
                phase="return_to_resource",
                active_intent="return_to_resource_area",
                active_target={
                    "targetName": "Resource return",
                    "classId": "resource_return",
                    "targetType": "tile",
                    "worldX": 3196,
                    "worldY": 3248,
                    "plane": 0,
                },
                bank_ui={"bankOpen": False},
                bank_operation={"bankingComplete": False, "resourceItemsHeld": 0},
                pathing={
                    "pathingNeeded": True,
                    "reason": "path_reachable",
                    "nextWaypointTile": {"worldX": 3209, "worldY": 3235, "plane": 0},
                    "nextWaypointAimPoint": aim(360, 340),
                    "predictedPathTiles": [
                        {"worldX": 3209, "worldY": 3235, "plane": 0},
                        {"worldX": 3207, "worldY": 3236, "plane": 0},
                    ],
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "return_to_resource_area")
        self.assertEqual(proposal.target_kind, "path_tile")
        self.assertEqual(proposal.target_tile, {"worldX": 3207, "worldY": 3236, "plane": 0})
        self.assertEqual(proposal.suggested_click_point, {"x": 360, "y": 340})

    def test_return_intent_with_stale_service_route_uses_resource_waypoint(self):
        staircase = {
            "targetName": "Staircase",
            "classId": "route_transition",
            "targetType": "sceneObject",
            "id": 56230,
            "worldX": 3204,
            "worldY": 3229,
            "plane": 0,
            "actions": ["Climb-up", "Top-floor"],
            "aimPoint": aim(8, 315),
            "expectedOptions": ["Climb-up", "Top-floor"],
            "expectedPlaneChange": "+1",
        }
        status = status_for(
            phase="return_to_resource",
            active_intent="return_to_resource_area",
            active_target={
                "targetName": "Resource return",
                "classId": "resource_return",
                "targetType": "tile",
                "worldX": 3196,
                "worldY": 3248,
                "plane": 0,
            },
            service={
                "serviceNeeded": True,
                "serviceRequired": True,
                "serviceReady": False,
            },
            service_route={
                "schema": "service_route_context.v1",
                "actionReady": True,
                "routeStepStatus": "route_interaction_visible",
                "currentStep": {
                    "type": "interact_object",
                    "expectedOptions": ["Climb-up", "Top-floor"],
                    "expectedTargetContains": ["Staircase"],
                    "planeChange": "+1",
                },
                "visibleInteractionTarget": staircase,
            },
            bank_ui={"bankOpen": False},
            bank_operation={"bankingComplete": False, "resourceItemsHeld": None},
            pathing={
                "pathingNeeded": True,
                "reason": "path_reachable",
                "nextWaypointTile": {"worldX": 3210, "worldY": 3231, "plane": 0},
                "nextWaypointAimPoint": aim(260, 280),
                "predictedPathTiles": [
                    {"worldX": 3210, "worldY": 3231, "plane": 0},
                    {"worldX": 3209, "worldY": 3231, "plane": 0},
                ],
            },
        )
        status["brain"]["genericTaskState"]["goalProgress"] = {"heldResourceCount": 1}

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "return_to_resource_area")
        self.assertEqual(proposal.target_kind, "path_tile")
        self.assertNotEqual(proposal.target_name, "Staircase")

    def test_stale_post_bank_tree_target_does_not_override_return_destination(self):
        castle_tree = {
            "targetName": "Tree",
            "name": "Tree",
            "classId": "tree",
            "targetType": "sceneObject",
            "objectId": 1278,
            "actions": ["Chop down"],
            "aimPoint": aim(202, 154),
            "worldX": 3212,
            "worldY": 3232,
            "plane": 0,
            "targetTick": 42,
        }
        status = status_for(
            phase="target_selected",
            active_intent="select_target",
            active_target=castle_tree,
            bank_ui={"bankOpen": False},
            bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
            resource_return={
                "returnDestinationAvailable": True,
                "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "returnDestinationSource": "profile_anchor",
                "resourceTargetCurrentlyVisible": True,
                "reason": "using_profile_resource_anchor",
            },
            return_route={
                "schema": "return_route_context.v1",
                "returnRouteId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                "state": "return_route_ready",
                "currentNavigationTarget": {
                    "targetType": "tile",
                    "classId": "resource_return",
                    "targetName": "Lumbridge Castle west approach return",
                    "worldX": 3203,
                    "worldY": 3238,
                    "plane": 0,
                    "source": "static_route_prior",
                },
            },
            pathing={
                "destination": castle_tree,
                "localReachability": "reachable",
                "reason": "target_reachable",
            },
        )

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "return_to_resource_area")
        self.assertEqual(proposal.target_kind, "path_tile")
        self.assertNotEqual(proposal.target_name, "Tree")

    def test_return_route_yields_to_visible_resource_candidate_after_service(self):
        status = status_for(
            phase="return_to_resource",
            active_intent="return_to_resource_area",
            active_target={
                "targetName": "Lumbridge Castle west approach return",
                "classId": "resource_return",
                "targetType": "tile",
                "aimPoint": aim(700, 710),
            },
            bank_ui={"bankOpen": False},
            bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
            resource_return={
                "returnDestinationAvailable": True,
                "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "reason": "resource_target_visible",
                "resourceTargetCurrentlyVisible": True,
            },
            return_route={
                "schema": "return_route_context.v1",
                "returnRouteId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                "state": "return_route_ready",
                "currentNavigationTarget": {"worldX": 3203, "worldY": 3238, "plane": 0},
            },
        )
        status["brain"]["candidates"] = [
            {
                "targetName": "Tree",
                "name": "Tree",
                "classId": "tree",
                "targetType": "sceneObject",
                "objectId": 1276,
                "actions": ["Chop down"],
                "aimPoint": aim(150, 174),
                "worldLocation": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "targetTick": 42,
            }
        ]

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertEqual(proposal.target_name, "Tree")

    def test_return_route_yields_to_flattened_return_best_resource_target(self):
        status = status_for(
            phase="return_to_resource",
            active_intent="return_to_resource_area",
            active_target={
                "targetName": "Lumbridge Castle west approach return",
                "classId": "resource_return",
                "targetType": "tile",
                "aimPoint": aim(700, 710),
            },
            bank_ui={"bankOpen": False},
            bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
            resource_return={
                "returnDestinationAvailable": True,
                "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "resourceTargetCurrentlyVisible": True,
            },
            return_route={
                "schema": "return_route_context.v1",
                "returnRouteId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                "state": "return_route_ready",
                "currentNavigationTarget": {"worldX": 3203, "worldY": 3238, "plane": 0},
            },
        )
        status["returnBestResourceTarget"] = {
            "targetName": "Tree",
            "name": "Tree",
            "classId": "tree",
            "targetType": "sceneObject",
            "objectId": 1276,
            "actions": ["Chop down"],
            "aimPoint": aim(151, 174),
            "worldX": 3196,
            "worldY": 3248,
            "plane": 0,
            "targetTick": 42,
        }

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertEqual(proposal.target_name, "Tree")

    def test_return_route_prefers_safe_visible_resource_over_offscreen_worksite_memory(self):
        visible_tree = {
            "targetName": "Tree",
            "name": "Tree",
            "classId": "tree",
            "targetType": "sceneObject",
            "id": 1278,
            "actions": ["Chop down"],
            "aimPoint": aim(438, 152),
            "bounds": bounds(356, 45, 165, 214),
            "safeAimPoint": {
                "status": "PASS",
                "canvasX": 438,
                "canvasY": 152,
                "distanceToViewportEdgePx": 78,
                "clippedVisibleAreaRatio": 0.93,
            },
            "worldX": 3213,
            "worldY": 3238,
            "plane": 0,
            "distanceTiles": 2,
            "qualityScore": 100,
            "targetTick": 42,
        }
        offscreen_memory_tree = {
            "targetName": "Tree",
            "name": "Tree",
            "classId": "tree",
            "targetType": "sceneObject",
            "id": 1278,
            "actions": ["Chop down"],
            "aimPoint": {"x": 25.5, "y": -5},
            "bounds": bounds(7, -16, 37, 22),
            "worldX": 3200,
            "worldY": 3246,
            "plane": 0,
            "distanceTiles": 11,
            "qualityScore": 96,
            "targetTick": 42,
        }
        status = status_for(
            phase="return_to_resource",
            active_intent="return_to_resource_area",
            active_target=visible_tree,
            bank_ui={"bankOpen": False},
            bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
            resource_return={
                "returnDestinationAvailable": True,
                "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "resourceTargetCurrentlyVisible": True,
                "reason": "resource_target_visible",
            },
            return_route={
                "schema": "return_route_context.v1",
                "returnRouteId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                "state": "return_route_ready",
                "currentNavigationTarget": {"worldX": 3203, "worldY": 3238, "plane": 0},
            },
            overlay={"selectedMarker": visible_tree, "markers": [visible_tree, offscreen_memory_tree]},
        )

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertEqual(proposal.target_tile, {"worldX": 3213, "worldY": 3238, "plane": 0})
        self.assertTrue(proposal.executable)

    def test_return_route_keeps_navigating_when_reacquired_resource_is_not_actionable(self):
        status = status_for(
            phase="return_to_resource",
            active_intent="return_to_resource_area",
            active_target={
                "targetName": "Lumbridge Castle west approach return",
                "classId": "resource_return",
                "targetType": "tile",
                "aimPoint": aim(700, 710),
            },
            bank_ui={"bankOpen": False},
            bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
            resource_return={
                "returnDestinationAvailable": True,
                "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "resourceTargetCurrentlyVisible": True,
            },
            return_route={
                "schema": "return_route_context.v1",
                "returnRouteId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                "state": "return_route_ready",
                "currentNavigationTarget": {"worldX": 3203, "worldY": 3238, "plane": 0},
            },
        )
        status["returnBestResourceTarget"] = {
            "targetName": "Tree",
            "name": "Tree",
            "classId": "tree",
            "targetType": "sceneObject",
            "objectId": 1276,
            "actions": ["Chop down"],
            "aimPoint": {"x": 2147483648, "y": 2147483648},
            "geometrySummary": {"bounds": {"x": 2147483648, "y": 2147483648, "w": 0, "h": 0}},
            "worldX": 3196,
            "worldY": 3226,
            "plane": 0,
            "targetTick": 42,
        }

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "return_to_resource_area")
        self.assertEqual(proposal.target_kind, "path_tile")

    def test_return_route_transition_outranks_resource_return_waypoint(self):
        proposal = build_action_proposal(
            status_for(
                phase="return_to_resource",
                active_intent="return_to_resource_area",
                active_target={"targetName": "Resource return", "classId": "resource_return", "aimPoint": aim(700, 710)},
                bank_ui={"bankOpen": False},
                bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
                resource_return={
                    "returnDestinationAvailable": True,
                    "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                    "reason": "using_profile_resource_anchor",
                },
                return_route={
                    "schema": "return_route_context.v1",
                    "returnActionReady": True,
                    "state": "return_transition_actionable",
                    "currentStep": {
                        "type": "interact_object",
                        "label": "bank floor stairs down",
                        "expectedOptions": ["Climb-down"],
                        "dialogueOpenerOptions": ["Climb"],
                        "expectedTargetContains": ["Staircase"],
                        "planeChange": "-1",
                    },
                    "visibleInteractionTarget": {
                        "targetName": "Staircase",
                        "classId": "staircase",
                        "targetType": "sceneObject",
                        "aimPoint": aim(431, 214),
                        "actions": ["Climb-down"],
                        "expectedOptions": ["Climb-down"],
                        "expectedTargets": ["Staircase"],
                        "expectedPlaneChange": "-1",
                    },
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "interact_service_route_object")
        self.assertEqual(proposal.target_kind, "service_route_object")
        self.assertEqual(proposal.target_name, "Staircase")
        self.assertEqual(proposal.suggested_click_point, {"x": 431, "y": 214})
        self.assertEqual(proposal.target_explanation["expectedPlaneChange"], "-1")
        self.assertEqual(proposal.target_explanation["dialogueOpenerOptions"], ["Climb"])

    def test_return_route_offscreen_transition_outranks_final_resource_destination(self):
        status = status_for(
            phase="return_to_resource",
            active_intent="return_to_resource_area",
            active_target={
                "targetName": "Resource return",
                "classId": "resource_return",
                "targetType": "tile",
                "worldX": 3196,
                "worldY": 3248,
                "plane": 0,
            },
            bank_ui={"bankOpen": False},
            bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
            resource_return={
                "returnDestinationAvailable": True,
                "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "reason": "using_profile_resource_anchor",
            },
            return_route={
                "schema": "return_route_context.v1",
                "state": "return_blocked",
                "returnActionReady": False,
                "currentStepIndex": 0,
                "currentStep": {
                    "type": "interact_object",
                    "label": "bank floor stairs down",
                    "expectedOptions": ["Climb-down", "Climb down"],
                    "dialogueOpenerOptions": ["Climb"],
                    "expectedTargetContains": ["Stair", "Staircase", "Ladder"],
                    "planeChange": "-1",
                },
                "routeObjectCensus": {
                    "topRouteObjects": [
                        {
                            "name": "Staircase",
                            "routeObjectKind": "route_transition",
                            "routeRelevanceStatus": "PASS",
                            "matchedRouteStepIndex": 0,
                            "rejectionReason": "offscreen",
                            "projectionStatus": {
                                "geometryAvailable": True,
                                "onScreen": False,
                                "visible": False,
                                "actionableByCanvas": False,
                                "aimPoint": {"canvasX": 59, "canvasY": 381, "source": "canvasLocation"},
                                "classification": "offscreen",
                            },
                            "routeRelevance": {"relevanceStatus": "PASS"},
                            "candidate": {
                                "targetName": "Staircase",
                                "classId": "route_transition",
                                "targetType": "sceneObject",
                                "id": 56231,
                                "worldX": 3205,
                                "worldY": 3229,
                                "plane": 2,
                                "actions": ["Climb-down", "Bottom-floor"],
                                "source": "world_model_cache",
                                "worldModelSource": True,
                            },
                        }
                    ]
                },
            },
            pathing={
                "pathingNeeded": True,
                "destinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
            },
            camera_viewport={
                "canvasWidth": 765,
                "canvasHeight": 503,
                "viewportXOffset": 0,
                "viewportYOffset": 0,
                "viewportWidth": 765,
                "viewportHeight": 503,
                "cameraYaw": 32,
                "cameraPitch": 383,
            },
        )
        status["playerLocation"] = {"worldX": 3208, "worldY": 3220, "plane": 2}
        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "service_view_recovery")
        self.assertEqual(proposal.reason, "return_route_transition_view_recovery_needed")
        self.assertEqual(proposal.target_name, "Staircase")
        self.assertEqual(proposal.suggested_world_tile, {"worldX": 3205, "worldY": 3229, "plane": 2})
        exposure = proposal.target_explanation["serviceTargetExposure"]
        self.assertTrue(exposure["serviceObjectRouteRelevant"])
        self.assertTrue(exposure["serviceObjectActionRelevant"])
        self.assertEqual(exposure["currentProjectionStatus"], "offscreen")

    def test_return_route_dialogue_proposes_climb_down_number_key_choice(self):
        proposal = build_action_proposal(
            status_for(
                phase="return_to_resource",
                active_intent="return_to_resource_area",
                active_target=None,
                bank_ui={"bankOpen": False},
                bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
                return_route={
                    "schema": "return_route_context.v1",
                    "returnRouteId": "lumbridge_west_trees_to_lumbridge_castle_bank_return",
                    "currentStepIndex": 1,
                    "currentStep": {
                        "type": "interact_object",
                        "label": "first floor stairs down",
                        "planeChange": "-1",
                    },
                },
                dialogue_state={
                    "schema": "dialogue_state.v1",
                    "active": True,
                    "type": "options",
                    "promptText": "Climb up or down the stairs?",
                    "canUseNumberKeys": True,
                    "options": [
                        {"index": 1, "key": "1", "text": "Climb up the stairs."},
                        {"index": 2, "key": "2", "text": "Climb down the stairs."},
                    ],
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "interface_dialogue_choice")
        self.assertEqual(proposal.target_kind, "interface_dialogue")
        self.assertEqual(proposal.key_action, {"type": "key_press", "key": "2"})
        self.assertEqual(proposal.target_explanation["expectedPlaneChange"], "-1")

    def test_banking_complete_ignores_stale_service_route_object_when_resource_target_visible(self):
        proposal = build_action_proposal(
            status_for(
                phase="target_selected",
                active_intent="resource_target",
                active_target={
                    "targetName": "Tree",
                    "name": "Tree",
                    "classId": "tree",
                    "targetType": "sceneObject",
                    "objectId": 1276,
                    "actions": ["Chop down"],
                    "aimPoint": aim(520, 530),
                    "worldLocation": {"worldX": 3196, "worldY": 3248, "plane": 0},
                    "targetTick": 42,
                },
                bank_ui={"bankOpen": False},
                bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
                service_route={
                    "actionReady": True,
                    "routeStepStatus": "route_interaction_visible",
                    "visibleInteractionTarget": {
                        "targetName": "Staircase",
                        "classId": "route_transition",
                        "targetType": "sceneObject",
                        "aimPoint": aim(240, 138),
                        "actions": ["Climb-up"],
                        "expectedOptions": ["Climb-up"],
                        "expectedTargets": ["Staircase"],
                    },
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertEqual(proposal.target_name, "Tree")

    def test_missing_click_point_warns_and_prevents_execution(self):
        proposal = build_action_proposal(
            status_for(
                active_target={"targetName": "Tree", "classId": "tree", "id": 1278},
                overlay={"selectedMarker": {"targetName": "Tree", "classId": "tree", "id": 1278}},
            )
        )

        self.assertEqual(proposal.status, "WARN")
        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertIn("click_point", proposal.missing_capabilities)
        self.assertFalse(proposal.executable)

    def test_stale_resource_candidate_refuses_selection(self):
        proposal = build_action_proposal(
            status_for(
                latest_tick=20,
                active_target={"targetName": "Tree", "classId": "tree", "tick": 10, "aimPoint": aim(110, 130)},
            )
        )

        self.assertEqual(proposal.proposed_action, "wait_for_context")
        self.assertEqual(proposal.reason, "candidate_data_stale")
        self.assertIn("target.freshness", proposal.missing_capabilities)
        self.assertTrue(any("candidate data stale" in warning for warning in proposal.warnings))
        self.assertFalse(proposal.executable)

    def test_current_live_resource_target_ignores_lagging_daemon_status_age(self):
        status = status_for(
            latest_tick=20,
            freshness="stale",
            active_target={
                "targetName": "Tree",
                "classId": "tree",
                "id": 1276,
                "tick": 20,
                "sourceTick": 20,
                "onScreen": True,
                "geometryAvailable": True,
                "aimPoint": aim(110, 130),
            },
        )
        status["latestUpdateUtc"] = "2000-01-01T00:00:00Z"

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertNotEqual(proposal.reason, "candidate_data_stale")
        self.assertNotIn("target.freshness", proposal.missing_capabilities)
        explanation = proposal.to_dict().get("targetExplanation") or {}
        self.assertFalse(explanation.get("stale"))
        self.assertEqual((explanation.get("freshness") or {}).get("status"), "fresh")

    def test_current_resource_identity_waives_stale_label_but_keeps_geometry_guard(self):
        status = status_for(
            latest_tick=20,
            freshness="stale",
            active_target={
                "targetName": "Tree",
                "classId": "tree",
                "id": 1276,
                "tick": 20,
                "sourceTick": 20,
                "onScreen": True,
                "geometryAvailable": False,
            },
        )
        status["latestUpdateUtc"] = "2000-01-01T00:00:00Z"

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertNotEqual(proposal.reason, "candidate_data_stale")
        self.assertNotIn("target.freshness", proposal.missing_capabilities)
        self.assertIn("click_point", proposal.missing_capabilities)
        self.assertFalse(proposal.executable)
        explanation = proposal.to_dict().get("targetExplanation") or {}
        self.assertFalse((explanation.get("freshness") or {}).get("stale"))

    def test_brain_tick_resource_identity_waives_stale_label(self):
        status = status_for(
            latest_tick=20,
            freshness="stale",
            active_target={
                "targetName": "Tree",
                "classId": "tree",
                "id": 1276,
                "onScreen": True,
                "geometryAvailable": False,
            },
        )
        status.pop("latestTick", None)
        status["latestUpdateUtc"] = "2000-01-01T00:00:00Z"

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.source_tick, 20)
        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertNotEqual(proposal.reason, "candidate_data_stale")
        self.assertNotIn("target.freshness", proposal.missing_capabilities)
        self.assertIn("click_point", proposal.missing_capabilities)
        self.assertFalse(proposal.executable)
        explanation = proposal.to_dict().get("targetExplanation") or {}
        self.assertFalse((explanation.get("freshness") or {}).get("stale"))

    def test_select_resource_target_includes_candidate_explanation(self):
        proposal = build_action_proposal(
            status_for(
                latest_tick=20,
                active_target={
                    "targetName": "Tree",
                    "classId": "tree",
                    "id": 1276,
                    "tick": 20,
                    "worldX": 3200,
                    "worldY": 3201,
                    "plane": 0,
                    "onScreen": True,
                    "geometryAvailable": True,
                    "aimPoint": aim(110, 130),
                    "positiveSignals": ["profileMatch", "onScreen"],
                },
            )
        )

        payload = proposal.to_dict()
        explanation = payload["targetExplanation"]
        self.assertEqual(explanation["name"], "Tree")
        self.assertEqual(explanation["id"], 1276)
        self.assertEqual(explanation["classId"], "tree")
        self.assertEqual(explanation["targetTick"], 20)
        self.assertFalse(explanation["stale"])
        self.assertIn("profileMatch", explanation["acceptedReasons"])

    def test_resource_target_uses_safe_visible_aimpoint_when_raw_center_is_off_viewport(self):
        proposal = build_action_proposal(
            status_for(
                active_target={
                    "targetName": "Tree",
                    "classId": "tree",
                    "id": 1276,
                    "onScreen": True,
                    "geometryAvailable": True,
                    "aimPoint": {"canvasX": 770, "canvasY": 250, "source": "clickboxCenter"},
                    "clickboxBounds": {"x": 748, "y": 220, "width": 40, "height": 60},
                },
                input_geometry={
                    "inputGeometryAvailable": True,
                    "canvasScreenOrigin": {"x": 1000, "y": 2000},
                    "canvasSize": {"width": 1530, "height": 1006},
                    "sourceCanvasSize": {"width": 765, "height": 503},
                },
                camera_viewport={"canvasWidth": 765, "canvasHeight": 503, "viewportXOffset": 0, "viewportYOffset": 0, "viewportWidth": 765, "viewportHeight": 503},
            )
        )

        self.assertEqual(proposal.status, "PASS")
        self.assertEqual(proposal.suggested_click_point["x"], 759)
        self.assertEqual(proposal.target_explanation["safeAimPoint"]["source"], "clippedClickboxInterior")
        self.assertTrue(proposal.target_explanation["safeAimPoint"]["insideViewport"])

    def test_resource_target_without_safe_visible_point_is_not_executable(self):
        proposal = build_action_proposal(
            status_for(
                active_target={
                    "targetName": "Tree",
                    "classId": "tree",
                    "id": 1276,
                    "onScreen": True,
                    "geometryAvailable": True,
                    "aimPoint": {"canvasX": 830, "canvasY": 250, "source": "clickboxCenter"},
                    "clickboxBounds": {"x": 820, "y": 220, "width": 40, "height": 60},
                },
                camera_viewport={"canvasWidth": 765, "canvasHeight": 503, "viewportXOffset": 0, "viewportYOffset": 0, "viewportWidth": 765, "viewportHeight": 503},
            )
        )

        self.assertEqual(proposal.status, "WARN")
        self.assertFalse(proposal.executable)
        self.assertIn("safe_aimpoint", proposal.missing_capabilities)
        self.assertEqual(proposal.target_explanation["safeAimPoint"]["rejectionReason"], "no_visible_interactable_geometry")

    def test_poor_edge_resource_view_triggers_camera_recovery_before_click(self):
        edge_tree = {
            "targetName": "Tree",
            "classId": "tree",
            "id": 1278,
            "worldX": 3196,
            "worldY": 3248,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "aimPoint": {"canvasX": 4, "canvasY": 180, "source": "clickboxCenter"},
            "clickboxBounds": {"x": 0, "y": 160, "width": 24, "height": 42},
        }

        proposal = build_action_proposal(
            status_for(
                active_target=edge_tree,
                resource_return={
                    "returnDestinationAvailable": False,
                    "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                    "worksiteRadiusTiles": 8,
                },
                camera_viewport={"canvasWidth": 765, "canvasHeight": 503, "viewportXOffset": 0, "viewportYOffset": 0, "viewportWidth": 765, "viewportHeight": 503},
            )
        )

        self.assertEqual(proposal.proposed_action, "resource_view_recovery")
        score = proposal.target_explanation["resourceViewScore"]
        self.assertEqual(score["schema"], "resource_view_score.v1")
        self.assertEqual(score["classification"], "poor_edge_resource_view")
        self.assertEqual(proposal.target_explanation["resourceCameraTriggeredBy"], "resource_target_edge_rejected")

    def test_good_resource_view_does_not_trigger_unnecessary_camera_recovery(self):
        trees = [
            {
                "targetName": "Tree",
                "classId": "tree",
                "id": 1278,
                "worldX": 3196 + index,
                "worldY": 3248,
                "plane": 0,
                "onScreen": True,
                "geometryAvailable": True,
                "aimPoint": aim(220 + index * 70, 210),
                "clickboxBounds": {"x": 205 + index * 70, "y": 185, "width": 36, "height": 48},
                "qualityScore": 50 - index,
            }
            for index in range(3)
        ]
        status = status_for(
            active_target=trees[0],
            overlay={"selectedMarker": trees[0], "markers": trees},
            resource_return={
                "returnDestinationAvailable": False,
                "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "worksiteRadiusTiles": 8,
            },
            camera_viewport={"canvasWidth": 765, "canvasHeight": 503, "viewportXOffset": 0, "viewportYOffset": 0, "viewportWidth": 765, "viewportHeight": 503},
        )
        status["brain"]["profileCandidates"] = trees
        status["profileCandidates"] = trees

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertIn(proposal.target_explanation["resourceViewClassification"], {"good_resource_view", "usable_resource_view"})
        self.assertFalse(proposal.target_explanation["resourceCameraRecoveryRecommended"])

    def test_no_action_stump_is_not_selected_over_visible_chop_tree(self):
        stump = {
            "targetName": "Tree stump",
            "classId": "tree",
            "id": 1342,
            "worldX": 3217,
            "worldY": 3231,
            "plane": 0,
            "actions": [],
            "onScreen": True,
            "geometryAvailable": True,
            "aimPoint": aim(220, 170),
            "clickboxBounds": {"x": 210, "y": 160, "width": 20, "height": 20},
            "qualityScore": 100,
        }
        tree = {
            "targetName": "Tree",
            "classId": "tree",
            "id": 1278,
            "worldX": 3212,
            "worldY": 3232,
            "plane": 0,
            "actions": ["Chop down"],
            "onScreen": True,
            "geometryAvailable": True,
            "aimPoint": aim(300, 190),
            "clickboxBounds": {"x": 280, "y": 160, "width": 40, "height": 60},
            "qualityScore": 5,
        }
        status = status_for(active_target=stump, overlay={"selectedMarker": stump, "markers": [stump, tree]})
        status["brain"]["profileCandidates"] = [stump, tree]
        status["profileCandidates"] = [stump, tree]

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertTrue(proposal.executable)
        self.assertEqual(proposal.target_name, "Tree")
        self.assertEqual(proposal.target_tile, {"worldX": 3212, "worldY": 3232, "plane": 0})
        self.assertEqual(proposal.target_explanation["resourceSelectionReason"], "preferred_skill_eligible_resource_candidate")
        action_status = proposal.target_explanation["resourceLiveActionStatus"]
        self.assertTrue(action_status["hasMatchingLiveResourceAction"])
        self.assertEqual(action_status["matchingLiveResourceActions"], ["Chop down"])

    def test_only_no_action_stump_is_blocked_without_camera_recovery(self):
        stump = {
            "targetName": "Tree stump",
            "classId": "tree",
            "id": 1342,
            "worldX": 3217,
            "worldY": 3231,
            "plane": 0,
            "actions": [],
            "onScreen": True,
            "geometryAvailable": True,
            "aimPoint": aim(220, 170),
            "clickboxBounds": {"x": 210, "y": 160, "width": 20, "height": 20},
            "qualityScore": 100,
        }

        proposal = build_action_proposal(status_for(active_target=stump, overlay={"selectedMarker": stump, "markers": [stump]}))

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertEqual(proposal.status, "WARN")
        self.assertFalse(proposal.executable)
        self.assertEqual(proposal.reason, "resource_target_missing_live_action")
        self.assertEqual(proposal.actionability, "blocked_no_matching_action")
        self.assertIn("resource_action", proposal.missing_capabilities)
        self.assertIn("resource_stump_no_live_action", proposal.target_explanation["resourceSelectionRejectionReason"])

    def test_safe_selected_resource_remains_clickable_in_poor_single_candidate_view(self):
        selected = {
            "targetName": "Tree",
            "classId": "tree",
            "id": 1278,
            "worldX": 3213,
            "worldY": 3238,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "aimPoint": aim(352, 167),
            "safeAimPoint": {
                "status": "PASS",
                "canvasX": 352,
                "canvasY": 167,
                "distanceToViewportEdgePx": 163,
                "clippedVisibleAreaRatio": None,
            },
            "qualityScore": 100,
        }
        edge_candidate = {
            "targetName": "Tree",
            "classId": "tree",
            "id": 1278,
            "worldX": 3212,
            "worldY": 3232,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "aimPoint": aim(4, 349),
            "clickboxBounds": {"x": 0, "y": 330, "width": 8, "height": 40},
            "qualityScore": 50,
        }
        status = status_for(
            active_target=selected,
            overlay={"selectedMarker": selected, "markers": [selected, edge_candidate]},
        )
        status["brain"]["profileCandidates"] = [selected, edge_candidate]

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertEqual(proposal.target_tile, {"worldX": 3213, "worldY": 3238, "plane": 0})
        self.assertTrue(proposal.executable)

    def test_candidate_inside_worksite_outranks_far_visible_tree(self):
        far = {
            "targetName": "Tree",
            "classId": "tree",
            "id": 1278,
            "worldX": 3218,
            "worldY": 3268,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "aimPoint": aim(240, 220),
            "clickboxBounds": {"x": 224, "y": 196, "width": 36, "height": 48},
            "qualityScore": 100,
        }
        near = {
            "targetName": "Dead tree",
            "classId": "tree",
            "id": 1286,
            "worldX": 3197,
            "worldY": 3248,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "aimPoint": aim(360, 220),
            "clickboxBounds": {"x": 344, "y": 196, "width": 36, "height": 48},
            "qualityScore": 5,
        }
        status = status_for(
            active_target=far,
            overlay={"selectedMarker": far, "markers": [far, near]},
            resource_return={
                "returnDestinationAvailable": False,
                "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "worksiteRadiusTiles": 8,
            },
        )
        status["brain"]["profileCandidates"] = [far, near]
        status["profileCandidates"] = [far, near]

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertEqual(proposal.target_name, "Dead tree")
        self.assertEqual(proposal.target_explanation["resourceViewScore"]["selectedTargetDistanceFromWorksite"], 1)

    def test_safe_resource_far_from_memory_worksite_triggers_view_recovery(self):
        far_tree = {
            "targetName": "Dead tree",
            "classId": "tree",
            "id": 1286,
            "worldX": 3216,
            "worldY": 3194,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "aimPoint": aim(360, 220),
            "clickboxBounds": {"x": 344, "y": 196, "width": 36, "height": 48},
            "qualityScore": 100,
        }
        status = status_for(
            active_target=far_tree,
            overlay={"selectedMarker": far_tree, "markers": [far_tree]},
            camera_viewport={"canvasWidth": 765, "canvasHeight": 503, "viewportXOffset": 0, "viewportYOffset": 0, "viewportWidth": 765, "viewportHeight": 503},
        )
        status["brain"]["resourceAreaMemory"] = {
            "resourceMemoryValid": True,
            "lastResourceTargetTile": {"worldX": 3213, "worldY": 3238, "plane": 0},
            "lastResourceClusterCenter": {"worldX": 3213, "worldY": 3238, "plane": 0},
            "lastResourceProfile": "woodcutting",
        }
        status["brain"]["profileCandidates"] = [far_tree]
        status["profileCandidates"] = [far_tree]

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "resource_view_recovery")
        score = proposal.target_explanation["resourceViewScore"]
        self.assertEqual(score["worksiteAnchor"], {"worldX": 3213, "worldY": 3238, "plane": 0})
        self.assertEqual(score["classification"], "needs_worksite_recenter")
        self.assertTrue(score["selectedTargetPullsAwayFromWorksite"])

    def test_oak_becomes_executable_when_woodcutting_level_is_sufficient(self):
        oak = {
            "targetName": "Oak tree",
            "classId": "tree",
            "id": 10820,
            "worldX": 3196,
            "worldY": 3248,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "aimPoint": aim(260, 210),
            "clickboxBounds": {"x": 240, "y": 188, "width": 42, "height": 54},
            "qualityScore": 90,
        }
        tree = {
            "targetName": "Tree",
            "classId": "tree",
            "id": 1278,
            "worldX": 3197,
            "worldY": 3248,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "aimPoint": aim(360, 210),
            "clickboxBounds": {"x": 344, "y": 188, "width": 36, "height": 48},
            "qualityScore": 10,
        }
        status = status_for(active_target=oak, overlay={"selectedMarker": oak, "markers": [oak, tree]})
        status["brain"]["woodcuttingLevel"] = 15
        status["brain"]["profileCandidates"] = [oak, tree]
        status["profileCandidates"] = [oak, tree]

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertEqual(proposal.target_name, "Oak tree")
        self.assertEqual(proposal.target_explanation["resourceViewScore"]["lowLevelResourceCandidatesRejected"], 0)

    def test_post_depletion_reacquisition_scores_resource_view_before_next_target(self):
        edge_tree = {
            "targetName": "Tree",
            "classId": "tree",
            "id": 1278,
            "worldX": 3196,
            "worldY": 3248,
            "plane": 0,
            "onScreen": True,
            "geometryAvailable": True,
            "aimPoint": {"canvasX": 762, "canvasY": 210, "source": "clickboxCenter"},
            "clickboxBounds": {"x": 746, "y": 188, "width": 42, "height": 54},
        }

        proposal = build_action_proposal(
            status_for(
                phase="recent_target_depletion_observed",
                active_intent="post_depletion_reacquire",
                active_target=edge_tree,
                resource_return={
                    "returnDestinationAvailable": False,
                    "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                },
                camera_viewport={"canvasWidth": 765, "canvasHeight": 503, "viewportXOffset": 0, "viewportYOffset": 0, "viewportWidth": 765, "viewportHeight": 503},
            )
        )

        self.assertEqual(proposal.proposed_action, "resource_view_recovery")
        self.assertEqual(proposal.target_explanation["resourceViewScore"]["viewGoal"], "post_resource_depletion_view")

    def test_suppressed_selected_target_reacquires_backup_from_status_overlay_context(self):
        selected = {
            "targetName": "Oak tree",
            "name": "Oak tree",
            "classId": "tree",
            "id": 10820,
            "worldX": 3189,
            "worldY": 3248,
            "plane": 0,
            "aimPoint": aim(522, 174),
            "objectKey": "oak-selected",
        }
        backup = {
            "targetName": "Tree",
            "name": "Tree",
            "classId": "tree",
            "id": 1276,
            "worldX": 3194,
            "worldY": 3249,
            "plane": 0,
            "aimPoint": aim(620, 220),
            "objectKey": "tree-backup",
            "markerType": "backup_candidate",
        }
        status = status_for(active_target=selected)
        status["suppressedResourceTargetKeys"] = ["oak-selected"]
        status["intentOverlayContext"] = {
            "selectedMarker": {**selected, "markerType": "selected_target"},
            "markers": [{**selected, "markerType": "selected_target"}, backup],
        }

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertEqual(proposal.target_name, "Tree")
        self.assertEqual(proposal.suggested_click_point, {"x": 620, "y": 220})
        self.assertTrue(proposal.target_explanation["reacquiredAfterSuppression"])

    def test_suppression_matches_active_hash_key_to_overlay_object_key_by_identity(self):
        active = {
            "targetName": "Oak tree",
            "name": "Oak tree",
            "classId": "tree",
            "id": 10820,
            "worldX": 3189,
            "worldY": 3248,
            "plane": 0,
            "aimPoint": aim(522, 174),
            "key": "hash:11345729460",
        }
        selected_marker = {
            **active,
            "key": None,
            "objectKey": "0:3189:3248:53:48:GAME_OBJECT:10820:11345729460:1024",
            "markerType": "selected_target",
        }
        backup = {
            "targetName": "Tree",
            "name": "Tree",
            "classId": "tree",
            "id": 1276,
            "worldX": 3194,
            "worldY": 3249,
            "plane": 0,
            "aimPoint": aim(620, 220),
            "objectKey": "0:3194:3249:58:49:GAME_OBJECT:1276:1338120378:1024",
            "markerType": "backup_candidate",
        }
        status = status_for(active_target=active)
        status["suppressedResourceTargetKeys"] = ["10820:3189:3248:0:tree"]
        status["intentOverlayContext"] = {
            "selectedMarker": selected_marker,
            "markers": [selected_marker, backup],
        }

        proposal = build_action_proposal(status)

        self.assertEqual(proposal.target_name, "Tree")
        self.assertEqual(proposal.suggested_click_point, {"x": 620, "y": 220})
        self.assertTrue(proposal.target_explanation["reacquiredAfterSuppression"])


if __name__ == "__main__":
    unittest.main()

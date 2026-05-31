import os
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import service_route_core
from analyzers.live_state import TargetContext


def player(world_x=3194, world_y=3249, plane=0):
    return {
        "worldX": world_x,
        "worldY": world_y,
        "plane": plane,
        "sceneX": 50,
        "sceneY": 50,
    }


def stair(**overrides):
    candidate = {
        "targetName": "Staircase",
        "classId": "staircase",
        "targetType": "sceneObject",
        "id": 56230,
        "worldX": 3205,
        "worldY": 3229,
        "plane": 0,
        "actions": ["Climb-up", "Top-floor"],
        "aimPoint": {"canvasX": 200, "canvasY": 210},
    }
    candidate.update(overrides)
    return candidate


def bank_booth(**overrides):
    candidate = {
        "targetName": "Bank booth",
        "classId": "bank_booth",
        "targetType": "sceneObject",
        "id": 18491,
        "worldX": 3208,
        "worldY": 3220,
        "plane": 2,
        "actions": ["Bank"],
        "aimPoint": {"canvasX": 300, "canvasY": 220},
    }
    candidate.update(overrides)
    return candidate


def deposit_box(**overrides):
    candidate = {
        "targetName": "Bank Deposit Box",
        "classId": "deposit_box",
        "targetType": "sceneObject",
        "id": 10529,
        "worldX": 3210,
        "worldY": 3217,
        "plane": 2,
        "actions": ["Deposit"],
        "aimPoint": {"canvasX": 310, "canvasY": 225},
    }
    candidate.update(overrides)
    return candidate


class ServiceRouteCoreTest(unittest.TestCase):
    def test_default_lumbridge_route_loads_as_unverified_static_prior(self):
        routes = service_route_core.load_service_routes()
        route = service_route_core.select_service_route(routes, profile="woodcut_bank", service_type="bank")

        self.assertIsNotNone(route)
        self.assertEqual(route["routeId"], "lumbridge_west_trees_to_lumbridge_castle_bank")
        self.assertFalse(route["verifiedLive"])
        self.assertEqual(route["schema"], "service_route.v1")
        self.assertGreaterEqual(len(route["steps"]), 4)
        self.assertGreaterEqual(len(route["nodes"]), 4)
        self.assertGreaterEqual(len(route["edges"]), 4)
        self.assertIn("walk_to", {edge["type"] for edge in route["edges"]})
        self.assertIn("interact_climb_up", {edge["type"] for edge in route["edges"]})
        self.assertEqual(
            service_route_core.select_service_route(routes, profile="woodcutting_bank", service_type="bank_full")["routeId"],
            route["routeId"],
        )
        stair_steps = [step for step in route["steps"] if step.get("type") == "interact_object"]
        self.assertGreaterEqual(len(stair_steps), 2)
        for step in stair_steps[:2]:
            self.assertIn("Climb-up", step["expectedOptions"])
            self.assertNotIn("Climb", step["expectedOptions"])

    def test_return_route_intercepts_bank_floor_climb_down_staircase(self):
        context = service_route_core.build_return_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3209, world_y=3220, plane=2),
            target_context={"broadCandidates": [stair(id=16672, plane=2, actions=["Climb-down"], worldX=3206, worldY=3221)]},
            service_context={},
            resource_return_context={
                "returnDestinationAvailable": True,
                "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "returnDestinationSource": "profile_anchor",
            },
            route_state=service_route_core.ServiceRouteState(),
            routes=service_route_core.load_service_routes(),
            source_tick=90,
        )

        self.assertEqual(context["schema"], "return_route_context.v1")
        self.assertEqual(context["sourceRouteId"], "lumbridge_west_trees_to_lumbridge_castle_bank")
        self.assertEqual(context["state"], "return_transition_actionable")
        self.assertEqual(context["currentNodeId"], "lumbridge_bank_floor_stairs_down")
        self.assertTrue(context["returnActionReady"])
        self.assertEqual(context["visibleInteractionTarget"]["targetName"], "Staircase")
        self.assertEqual(context["visibleInteractionTarget"]["expectedPlaneChange"], "-1")
        self.assertIn("Climb-down", context["visibleInteractionTarget"]["expectedOptions"])

    def test_return_route_ignores_offscreen_staircase_projection_with_canvas_point(self):
        offscreen_stair = stair(
            id=56231,
            plane=2,
            actions=["Climb-down", "Bottom-floor"],
            worldX=3206,
            worldY=3221,
            objectKey="offscreen-staircase",
            onScreen=False,
            aimPoint={"canvasX": -1566, "canvasY": 2762},
        )
        visible_stair = stair(
            id=56231,
            plane=2,
            actions=["Climb-down", "Bottom-floor"],
            worldX=3206,
            worldY=3221,
            objectKey="visible-staircase",
            onScreen=True,
            aimPoint={"canvasX": 127, "canvasY": 35},
        )

        context = service_route_core.build_return_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3209, world_y=3220, plane=2),
            target_context={"broadCandidates": [offscreen_stair, visible_stair]},
            service_context={},
            resource_return_context={
                "returnDestinationAvailable": True,
                "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "returnDestinationSource": "profile_anchor",
            },
            route_state=service_route_core.ServiceRouteState(),
            routes=service_route_core.load_service_routes(),
            source_tick=91,
        )

        self.assertEqual(context["state"], "return_transition_actionable")
        self.assertEqual(context["visibleInteractionTarget"]["objectKey"], "visible-staircase")
        self.assertEqual(context["routeObjectsVisible"], 1)
        self.assertEqual(context["routeRelevantObjects"], 2)
        self.assertEqual(context["routeRelevantActionableObjects"], 1)
        offscreen_projection = next(
            item["projectionStatus"]
            for item in context["routeObjectCensus"]["topRouteObjects"]
            if item.get("objectKey") == "offscreen-staircase"
        )
        self.assertFalse(offscreen_projection["visible"])
        self.assertFalse(offscreen_projection["inCanvas"])
        self.assertFalse(offscreen_projection["actionableByCanvas"])
        self.assertEqual(offscreen_projection["rejectionReason"], "offscreen")

    def test_return_route_after_ground_floor_descend_targets_west_approach(self):
        context = service_route_core.build_return_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3205, world_y=3228, plane=0),
            target_context={"broadCandidates": []},
            service_context={},
            resource_return_context={
                "returnDestinationAvailable": True,
                "returnDestinationTile": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "returnDestinationSource": "profile_anchor",
            },
            route_state=service_route_core.ServiceRouteState(),
            routes=service_route_core.load_service_routes(),
            source_tick=92,
        )

        self.assertEqual(context["state"], "return_route_ready")
        self.assertEqual(context["currentNodeId"], "lumbridge_castle_west_approach")
        self.assertEqual(context["currentNavigationTarget"]["worldX"], 3203)
        self.assertEqual(context["currentNavigationTarget"]["worldY"], 3238)

    def test_route_prior_provides_scout_navigation_anchor_when_service_target_missing(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"serviceCandidateInputs": [], "loadedServiceScene": [], "broadCandidates": []},
            source_tick=42,
        )

        self.assertEqual(context["status"], "WARN")
        self.assertTrue(context["routeAvailable"])
        self.assertEqual(context["routeStepStatus"], "static_route_prior")
        self.assertEqual(context["currentStep"]["type"], "navigate_world")
        self.assertEqual(context["currentNodeId"], "lumbridge_castle_west_approach")
        self.assertEqual(context["nextEdge"]["type"], "walk_to")
        self.assertGreaterEqual(len(context["routeNodes"]), 4)
        self.assertGreaterEqual(len(context["routeEdges"]), 4)
        self.assertEqual(context["currentNavigationTarget"]["targetType"], "service_route_anchor")
        self.assertFalse(context["currentNavigationTarget"]["verifiedLive"])
        self.assertIn("route prior is unverified", " ".join(context["warnings"]).lower())

    def test_unmapped_current_area_uses_goal_directed_service_context(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3254, world_y=3240, plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"serviceCandidateInputs": [], "loadedServiceScene": [], "broadCandidates": []},
            source_tick=77,
        )

        self.assertEqual(context["routeMode"], "goal_directed_fallback")
        self.assertEqual(context["routeSourceStatus"], "unmapped_source")
        self.assertEqual(context["routeStepStatus"], "goal_directed_route_prior")
        self.assertEqual(context["routeContext"]["schema"], "route_context.v1")
        self.assertEqual(context["routeContext"]["currentAreaSource"], "unknown_current_position")
        self.assertEqual(context["routeContext"]["serviceGoal"]["anchorId"], "lumbridge_castle_bank")
        self.assertEqual(context["selectedServiceAnchor"]["anchorId"], "lumbridge_castle_bank")
        self.assertEqual(context["selectedApproachNode"]["nodeId"], "lumbridge_bridge_east_approach")
        self.assertEqual(context["currentNodeId"], "lumbridge_bridge_east_approach")
        self.assertEqual(context["currentNavigationTarget"]["targetName"], "Lumbridge bridge east approach")
        self.assertEqual(context["currentNavigationTarget"]["source"], "goal_directed_service_anchor")
        self.assertEqual(context["routeSourceMismatch"]["classification"], "route_source_mismatch")
        self.assertEqual(context["blockerReason"], "route_source_mismatch")

    def test_goal_directed_bridge_east_hands_off_to_bridge_west_when_reached(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3258, world_y=3228, plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"serviceCandidateInputs": [], "loadedServiceScene": [], "broadCandidates": []},
            source_tick=80,
        )

        self.assertEqual(context["routeMode"], "goal_directed_fallback")
        self.assertEqual(context["selectedApproachNode"]["nodeId"], "lumbridge_bridge_west_approach")
        self.assertEqual(context["currentNodeId"], "lumbridge_bridge_west_approach")

    def test_goal_directed_bridge_west_hands_off_to_south_approach_when_reached(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3243, world_y=3226, plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"serviceCandidateInputs": [], "loadedServiceScene": [], "broadCandidates": []},
            source_tick=80,
        )

        self.assertEqual(context["routeMode"], "goal_directed_fallback")
        self.assertEqual(context["selectedApproachNode"]["nodeId"], "lumbridge_castle_south_entrance_approach")
        self.assertEqual(context["currentNodeId"], "lumbridge_castle_south_entrance_approach")

    def test_goal_directed_bridge_west_stays_complete_after_bankward_progress(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3237, world_y=3223, plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"serviceCandidateInputs": [], "loadedServiceScene": [], "broadCandidates": []},
            source_tick=81,
        )

        self.assertEqual(context["routeMode"], "goal_directed_fallback")
        self.assertEqual(context["selectedApproachNode"]["nodeId"], "lumbridge_castle_south_entrance_approach")
        self.assertEqual(context["currentNodeId"], "lumbridge_castle_south_entrance_approach")

    def test_goal_directed_south_approach_hands_off_to_castle_entry_when_reached(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3221, world_y=3218, plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"serviceCandidateInputs": [], "loadedServiceScene": [], "broadCandidates": []},
            source_tick=79,
        )

        self.assertEqual(context["routeMode"], "goal_directed_fallback")
        self.assertEqual(context["selectedApproachNode"]["nodeId"], "lumbridge_castle_entrance_or_courtyard")
        self.assertEqual(context["currentNodeId"], "lumbridge_castle_entrance_or_courtyard")

    def test_goal_directed_south_approach_stays_complete_on_castle_road(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3221, world_y=3224, plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"serviceCandidateInputs": [], "loadedServiceScene": [], "broadCandidates": []},
            source_tick=82,
        )

        self.assertEqual(context["routeMode"], "goal_directed_fallback")
        self.assertEqual(context["selectedApproachNode"]["nodeId"], "lumbridge_castle_entrance_or_courtyard")
        self.assertEqual(context["currentNodeId"], "lumbridge_castle_entrance_or_courtyard")

    def test_goal_directed_fallback_does_not_target_first_stairs_before_entry(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3194, world_y=3217, plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"serviceCandidateInputs": [], "loadedServiceScene": [], "broadCandidates": []},
            source_tick=83,
        )

        self.assertEqual(context["routeMode"], "goal_directed_fallback")
        self.assertEqual(context["routeSourceStatus"], "unmapped_source")
        self.assertEqual(context["selectedApproachNode"]["nodeId"], "lumbridge_castle_entrance_or_courtyard")
        self.assertEqual(context["currentNodeId"], "lumbridge_castle_entrance_or_courtyard")

    def test_known_west_tree_area_keeps_explicit_route_context(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3196, world_y=3248, plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"serviceCandidateInputs": [], "loadedServiceScene": [], "broadCandidates": []},
            source_tick=78,
        )

        self.assertEqual(context["routeMode"], "explicit_route")
        self.assertEqual(context["routeSourceStatus"], "known_source")
        self.assertEqual(context["currentNodeId"], "lumbridge_castle_west_approach")
        self.assertEqual(context["routeContext"]["currentAreaSource"], "known_route_node")

    def test_route_context_prefers_authoritative_player_location_over_collision_proxy(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context={
                "worldX": 3196,
                "worldY": 3248,
                "plane": 0,
                "collisionWindowCenterWorld": {"worldX": 3254, "worldY": 3240, "plane": 0},
                "locationSource": "plugin_snapshot_baseline_player",
                "locationConfidence": 1.0,
            },
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"serviceCandidateInputs": [], "loadedServiceScene": [], "broadCandidates": []},
            source_tick=78,
        )

        self.assertEqual(context["routeContext"]["currentLocation"], {"worldX": 3196, "worldY": 3248, "plane": 0})
        self.assertEqual(context["routeContext"]["locationSource"], "plugin_snapshot_baseline_player")
        self.assertEqual(context["routeContext"]["locationConfidence"], 1.0)
        self.assertEqual(context["routeContext"]["routeSourceStatus"], "known_source")

    def test_collision_window_proxy_location_is_labeled_low_confidence(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context={"collisionWindowCenterWorld": {"worldX": 3254, "worldY": 3240, "plane": 0}},
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"serviceCandidateInputs": [], "loadedServiceScene": [], "broadCandidates": []},
            source_tick=78,
        )

        self.assertEqual(context["routeContext"]["currentLocation"], {"worldX": 3254, "worldY": 3240, "plane": 0})
        self.assertEqual(context["routeContext"]["locationSource"], "collision_window_center_proxy")
        self.assertEqual(context["routeContext"]["locationConfidence"], 0.35)
        self.assertEqual(context["routeContext"]["currentAreaConfidence"], 0.35)

    def test_destination_route_alias_selects_lumbridge_bank_route(self):
        routes = service_route_core.load_service_routes()
        route = service_route_core.select_service_route(routes, profile="woodcut_bank", service_type="bank", route_id="lumbridge_castle_bank")

        self.assertIsNotNone(route)
        self.assertEqual(route["routeId"], "lumbridge_west_trees_to_lumbridge_castle_bank")
        self.assertIn("lumbridge_castle_bank", route["aliases"])

    def test_visible_first_floor_stair_becomes_route_interaction_target(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank"},
            target_context={"broadCandidates": [stair()]},
            source_tick=43,
        )

        self.assertEqual(context["status"], "PASS")
        self.assertEqual(context["currentStep"]["type"], "interact_object")
        self.assertEqual(context["currentStep"]["label"], "first stairs up")
        self.assertTrue(context["actionReady"])
        self.assertEqual(context["visibleInteractionTarget"]["targetName"], "Staircase")
        self.assertIn("Climb-up", context["interactionExpectedOptions"])
        self.assertEqual(context["expectedPlaneChange"], "+1")
        self.assertEqual(context["routeObjectsVisible"], 1)
        self.assertEqual(context["routeObjectsActionable"], 1)
        self.assertTrue(context["selectedRouteObjectPresent"])
        anchor = next(iter(context["observedAnchors"].values()))
        self.assertEqual(anchor["confidence"], 0.85)
        self.assertEqual(anchor["verificationSource"], "visible_with_matching_action")
        census = context["routeObjectCensus"]
        self.assertEqual(census["routeTransitionCandidates"], 1)
        self.assertEqual(census["routeRelevantActionableObjects"], 1)
        self.assertEqual(census["visibleButRouteIrrelevantObjects"], 0)
        self.assertEqual(context["selectedRouteObjectRelevance"]["relevanceStatus"], "PASS")

    def test_world_model_loaded_service_scene_route_object_intercepts_goal_directed_walk(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3203, world_y=3220, plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context=TargetContext(
                loaded_service_scene=[
                    stair(
                        worldX=3204,
                        worldY=3229,
                        plane=0,
                        objectKey="world-model-stair-3204-3229",
                        source="world_model_cache",
                        actionTargetSource="live_route_object",
                        worldModelSourceLane="worldModelRouteObjectCensus",
                        _routeObjectScanSource="worldModelRouteObjectCensus",
                    )
                ],
                loaded_service_scene_count=1,
            ),
            source_tick=84,
        )

        self.assertEqual(context["routeMode"], "goal_directed_fallback")
        self.assertEqual(context["routeStepStatus"], "route_interaction_visible")
        self.assertEqual(context["currentNodeId"], "lumbridge_ground_floor_stairs")
        self.assertTrue(context["actionReady"])
        self.assertEqual(context["visibleInteractionTarget"]["targetName"], "Staircase")
        self.assertEqual(context["visibleInteractionTarget"]["actionTargetSource"], "live_route_object")
        self.assertEqual(context["routeObjectsVisible"], 1)
        self.assertEqual(context["routeRelevantActionableObjects"], 1)
        self.assertTrue(context["routeObjectInterceptReady"])
        self.assertEqual(context["routeObjectCensus"]["routeObjectScanSource"]["worldModelRouteObjectCensus"], 1)
        self.assertEqual(context["selectedRouteObjectRelevance"]["relevanceStatus"], "PASS")

    def test_random_visible_staircase_is_visible_but_route_irrelevant(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank"},
            target_context={"broadCandidates": [stair(worldX=3300, worldY=3300, aimPoint={"canvasX": 240, "canvasY": 180})]},
            source_tick=48,
        )

        self.assertEqual(context["routeStepStatus"], "static_route_prior")
        self.assertFalse(context["actionReady"])
        self.assertIsNone(context["visibleInteractionTarget"])
        self.assertEqual(context["routeObjectsVisible"], 1)
        self.assertEqual(context["routeObjectsActionable"], 0)
        self.assertEqual(context["routeRelevantActionableObjects"], 0)
        self.assertEqual(context["visibleButRouteIrrelevantObjects"], 1)
        self.assertEqual(context["routeObjectCensus"]["rejectedRouteObjectsByReason"]["outsideRouteCorridor"], 1)

    def test_wrong_plane_staircase_is_not_clicked_for_first_floor_step(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank"},
            target_context={"broadCandidates": [stair(id=16672, plane=1, worldX=3205, worldY=3229)]},
            source_tick=49,
        )

        self.assertEqual(context["routeStepStatus"], "static_route_prior")
        self.assertFalse(context["actionReady"])
        self.assertEqual(context["routeObjectCensus"]["rejectedRouteObjectsByReason"]["wrongPlane"], 1)

    def test_service_route_can_have_no_resource_safe_count_but_actionable_route_object(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank"},
            target_context={"profileCandidates": [], "broadCandidates": [stair()]},
            source_tick=50,
        )

        self.assertEqual(context["routeObjectsVisible"], 1)
        self.assertEqual(context["routeRelevantActionableObjects"], 1)
        self.assertTrue(context["routeObjectInterceptReady"])
        self.assertEqual(context["routeObjectCensus"]["routeObjectScanSource"]["broadCandidates"], 1)

    def test_lumbridge_west_approach_advances_when_player_is_within_static_radius(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3200, world_y=3236, plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"serviceCandidateInputs": [], "loadedServiceScene": [], "broadCandidates": []},
            source_tick=47,
        )

        self.assertEqual(context["currentNodeId"], "lumbridge_castle_entrance_or_courtyard")
        self.assertEqual(context["currentStep"]["type"], "navigate_world")
        self.assertIn("Lumbridge Castle west approach", context["completedSteps"])
        self.assertEqual(context["currentStep"]["worldLocation"], {"worldX": 3205, "worldY": 3232, "plane": 0})

    def test_completed_navigation_node_is_not_reselected_after_arrival(self):
        state = service_route_core.ServiceRouteState()
        first_context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3205, world_y=3232, plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"serviceCandidateInputs": [], "loadedServiceScene": [], "broadCandidates": []},
            route_state=state,
            source_tick=48,
        )

        self.assertEqual(first_context["currentNodeId"], "lumbridge_first_stairs_search_area")
        self.assertIn("Lumbridge Castle west approach", first_context["completedSteps"])
        self.assertIn("Lumbridge Castle entrance or ground-floor courtyard", first_context["completedSteps"])

        second_context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3206, world_y=3233, plane=0),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"serviceCandidateInputs": [], "loadedServiceScene": [], "broadCandidates": []},
            route_state=state,
            source_tick=49,
        )

        self.assertEqual(second_context["currentNodeId"], "lumbridge_first_stairs_search_area")
        self.assertEqual(second_context["currentStep"]["label"], "Lumbridge Castle first stairs search area")

    def test_visible_second_floor_stair_is_selected_after_plane_change(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(plane=1),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank"},
            target_context={"broadCandidates": [stair(id=16672, plane=1, actions=["Climb-up", "Climb-down"])]},
            source_tick=44,
        )

        self.assertEqual(context["currentStep"]["label"], "second stairs up")
        self.assertEqual(context["visibleInteractionTarget"]["plane"], 1)
        self.assertTrue(context["visibleInteractionTarget"]["verifiedLive"])
        self.assertIn("first stairs up", context["completedSteps"])
        self.assertEqual(context["currentNodeId"], "lumbridge_first_floor_stairs")

    def test_plane_one_without_visible_second_stair_does_not_reuse_ground_floor_anchor(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(plane=1),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank"},
            target_context={"broadCandidates": []},
            source_tick=46,
        )

        self.assertEqual(context["routeStepStatus"], "route_anchor_missing")
        self.assertIsNone(context["currentNavigationTarget"])
        self.assertIn("service.route.anchor", context["missingCapabilities"])
        self.assertIn("first stairs up", context["completedSteps"])

    def test_visible_bank_service_candidate_wins_over_route_prior(self):
        booth = bank_booth()
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3208, world_y=3220, plane=2),
            service_context={
                "serviceNeeded": True,
                "serviceTypeNeeded": "bank",
                "bestServiceCandidate": booth,
                "candidateCount": 1,
            },
            target_context={"broadCandidates": [stair(plane=1)]},
            source_tick=45,
        )

        self.assertEqual(context["routeStepStatus"], "service_target_actionable")
        self.assertEqual(context["currentStep"]["type"], "service_interact")
        self.assertEqual(context["visibleServiceTarget"]["targetName"], "Bank booth")
        self.assertTrue(context["actionReady"])

    def test_actionable_bank_service_candidate_becomes_service_interact_ready(self):
        booth = bank_booth(onScreen=True, geometryAvailable=True)
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3208, world_y=3220, plane=2),
            service_context={
                "serviceNeeded": True,
                "serviceTypeNeeded": "bank",
                "bestServiceCandidate": booth,
                "candidateCount": 1,
            },
            target_context={"broadCandidates": []},
            source_tick=45,
        )

        self.assertEqual(context["routeStepStatus"], "service_target_actionable")
        self.assertEqual(context["currentStep"]["type"], "service_interact")
        self.assertTrue(context["actionReady"])
        self.assertEqual(context["visibleServiceTarget"]["targetName"], "Bank booth")
        self.assertEqual(context["serviceObjectsVisible"], 1)
        self.assertEqual(context["serviceObjectsActionable"], 1)
        self.assertEqual(context["routeRelevantServiceObjects"], 1)
        self.assertEqual(context["routeRelevantActionableServiceObjects"], 1)
        census = context["serviceObjectCensus"]
        self.assertEqual(census["serviceObjectCandidatesTotal"], 1)
        self.assertEqual(census["bankBoothCandidates"], 1)
        self.assertEqual(census["routeRelevantActionableServiceObjects"], 1)
        self.assertEqual(census["topServiceObjects"][0]["name"], "Bank booth")

    def test_selected_service_action_prefers_expected_bank_action(self):
        booth = bank_booth(actions=["Collect", "Bank"], onScreen=True, geometryAvailable=True)
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3208, world_y=3220, plane=2),
            service_context={
                "serviceNeeded": True,
                "serviceTypeNeeded": "bank",
                "bestServiceCandidate": booth,
                "candidateCount": 1,
            },
            target_context={"broadCandidates": []},
            source_tick=45,
        )

        self.assertEqual(context["selectedServiceAction"], "Bank")

    def test_deposit_box_is_route_relevant_bank_floor_service_target(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3208, world_y=3220, plane=2),
            service_context={
                "serviceNeeded": True,
                "serviceTypeNeeded": "bank",
                "bestServiceCandidate": deposit_box(onScreen=True, geometryAvailable=True),
                "candidateCount": 1,
            },
            target_context={"broadCandidates": []},
            source_tick=45,
        )

        self.assertEqual(context["routeStepStatus"], "service_target_actionable")
        self.assertEqual(context["visibleServiceTarget"]["targetName"], "Bank Deposit Box")
        self.assertEqual(context["selectedServiceAction"], "Deposit")
        self.assertEqual(context["serviceObjectCensus"]["depositBoxCandidates"], 1)
        self.assertEqual(context["serviceObjectCensus"]["routeRelevantActionableServiceObjects"], 1)

    def test_actionable_deposit_box_is_preferred_over_bank_booth_for_service(self):
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3208, world_y=3220, plane=2),
            service_context={
                "serviceNeeded": True,
                "serviceTypeNeeded": "bank",
                "bestServiceCandidate": bank_booth(onScreen=True, geometryAvailable=True),
                "serviceCandidates": [
                    bank_booth(onScreen=True, geometryAvailable=True),
                    deposit_box(onScreen=True, geometryAvailable=True),
                ],
                "candidateCount": 2,
            },
            target_context={"broadCandidates": []},
            source_tick=45,
        )

        self.assertEqual(context["visibleServiceTarget"]["targetName"], "Bank Deposit Box")
        self.assertEqual(context["selectedServiceAction"], "Deposit")

    def test_offscreen_bank_booth_is_reported_in_service_census_not_absent(self):
        booth = bank_booth(onScreen=False, aimPoint=None)
        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3208, world_y=3220, plane=2),
            service_context={
                "serviceNeeded": True,
                "serviceTypeNeeded": "bank",
                "bestServiceCandidate": booth,
                "candidateCount": 1,
            },
            target_context={"broadCandidates": []},
            source_tick=45,
        )

        self.assertEqual(context["routeStepStatus"], "service_target_visible")
        self.assertFalse(context["actionReady"])
        census = context["serviceObjectCensus"]
        self.assertEqual(census["serviceObjectCandidatesTotal"], 1)
        self.assertEqual(census["visibleServiceObjects"], 0)
        self.assertEqual(census["routeRelevantServiceObjects"], 1)
        self.assertEqual(census["routeRelevantActionableServiceObjects"], 0)
        self.assertEqual(census["topServiceObjects"][0]["projectionStatus"]["offscreen"], True)

    def test_observed_service_anchor_is_reused_as_verified_navigation_target(self):
        state = service_route_core.ServiceRouteState()
        service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3208, world_y=3220, plane=2),
            service_context={
                "serviceNeeded": True,
                "serviceTypeNeeded": "bank",
                "bestServiceCandidate": bank_booth(),
                "candidateCount": 1,
            },
            target_context={"broadCandidates": []},
            route_state=state,
            source_tick=45,
        )

        context = service_route_core.build_service_route_context(
            profile="woodcut_bank",
            service_type="bank",
            player_context=player(world_x=3204, world_y=3220, plane=2),
            service_context={"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 0},
            target_context={"broadCandidates": []},
            route_state=state,
            source_tick=46,
        )

        self.assertEqual(context["routeStepStatus"], "retained_service_anchor")
        self.assertEqual(context["currentNavigationTarget"]["targetName"], "Bank booth")
        self.assertTrue(context["currentNavigationTarget"]["verifiedLive"])
        self.assertEqual(context["currentNavigationTarget"]["source"], "observed_route_anchor")

    def test_no_files_written_while_building_context(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            service_route_core.build_service_route_context(
                profile="woodcut_bank",
                service_type="bank",
                player_context=player(),
                service_context={"serviceNeeded": True},
                target_context={"broadCandidates": []},
                source_tick=1,
            )
            after = set(os.listdir(temp))

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

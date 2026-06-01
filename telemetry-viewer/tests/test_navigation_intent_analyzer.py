import os
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from analyzers import navigation_intent_analyzer
from analyzers.live_state import NavigationContext, ProcessInventoryContext, ServiceContext, TargetContext
import task_policy


def service_candidate(**overrides):
    candidate = {
        "targetType": "sceneObject",
        "classId": "bank_booth",
        "targetName": "Bank booth",
        "id": 10355,
        "worldX": 3208,
        "worldY": 3219,
        "plane": 0,
        "distanceTiles": 4,
        "navigation": {"directReachability": "reachable", "pathLengthTiles": 6},
    }
    candidate.update(overrides)
    return candidate


def resource_candidate(**overrides):
    candidate = {
        "targetType": "sceneObject",
        "classId": "tree",
        "targetName": "Tree",
        "id": 1278,
        "worldX": 3156,
        "worldY": 3237,
        "plane": 0,
        "distanceTiles": 2,
        "navigation": {"directReachability": "reachable"},
    }
    candidate.update(overrides)
    return candidate


class NavigationIntentAnalyzerTest(unittest.TestCase):
    def test_service_candidate_reachable_reports_navigation_destination(self):
        candidate = service_candidate()
        service = ServiceContext(
            service_required=True,
            service_type_needed="bank",
            best_service_candidate=candidate,
            candidate_count=1,
            source_tick=12,
        )

        context = navigation_intent_analyzer.analyze_navigation_intent(
            task_policy.resolve_task_policy("woodcutting_bank"),
            service_context=service,
            navigation_context=NavigationContext(collision_window_available=True, source_tick=12),
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "PASS")
        self.assertTrue(payload["navigationNeeded"])
        self.assertEqual(payload["navigationReason"], "service_target_available")
        self.assertEqual(payload["targetKind"], "service")
        self.assertEqual(payload["destinationTarget"]["classId"], "bank_booth")
        self.assertEqual(payload["distanceTiles"], 4)
        self.assertEqual(payload["directReachability"], "reachable")
        self.assertEqual(payload["pathLengthTiles"], 6)
        self.assertTrue(payload["collisionWindowAvailable"])

    def test_service_candidate_missing_waits_for_service_context(self):
        service = ServiceContext(
            service_required=True,
            service_type_needed="bank",
            candidate_count=0,
            source_tick=20,
        )

        context = navigation_intent_analyzer.analyze_navigation_intent(
            "woodcutting_bank",
            service_context=service,
            navigation_context=NavigationContext(collision_window_available=True),
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "WARN")
        self.assertFalse(payload["navigationNeeded"])
        self.assertEqual(payload["navigationReason"], "service_target_missing")
        self.assertEqual(payload["targetKind"], "none")
        self.assertIsNone(payload["destinationTarget"])
        self.assertIn("service target missing", " ".join(payload["warnings"]).lower())

    def test_service_route_prior_supplies_navigation_destination_when_service_target_missing(self):
        service = ServiceContext(
            service_required=True,
            service_type_needed="bank",
            candidate_count=0,
            source_tick=21,
            service_route_context={
                "schema": "service_route_context.v1",
                "status": "WARN",
                "routeAvailable": True,
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "routeStepStatus": "static_route_prior",
                "currentNavigationTarget": {
                    "targetType": "service_route_anchor",
                    "classId": "service_route_anchor",
                    "targetName": "Lumbridge Castle west stair approach",
                    "worldX": 3205,
                    "worldY": 3229,
                    "plane": 0,
                    "verifiedLive": False,
                },
                "warnings": ["route prior is unverified"],
            },
        )

        context = navigation_intent_analyzer.analyze_navigation_intent(
            "woodcutting_bank",
            service_context=service,
            navigation_context=NavigationContext(collision_window_available=True),
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "WARN")
        self.assertTrue(payload["navigationNeeded"])
        self.assertEqual(payload["navigationReason"], "service_route_prior")
        self.assertEqual(payload["targetKind"], "service_route")
        self.assertEqual(payload["destinationTarget"]["targetType"], "service_route_anchor")
        self.assertEqual(payload["destinationTarget"]["worldX"], 3205)
        self.assertIn("route prior", " ".join(payload["warnings"]).lower())

    def test_goal_directed_route_overrides_off_route_visible_service_target(self):
        visible_al_kharid_booth = service_candidate(
            targetName="Closed bank booth",
            id=10528,
            worldX=3268,
            worldY=3170,
            plane=0,
            distanceTiles=24,
            navigation={"directReachability": "unknown"},
        )
        service = ServiceContext(
            service_required=True,
            service_type_needed="bank",
            best_service_candidate=visible_al_kharid_booth,
            candidate_count=1,
            source_tick=22,
            service_route_context={
                "schema": "service_route_context.v1",
                "status": "WARN",
                "routeAvailable": True,
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "routeStepStatus": "goal_directed_route_prior",
                "routeMode": "goal_directed_fallback",
                "goalDirectedFallback": True,
                "currentNavigationTarget": {
                    "targetType": "service_route_anchor",
                    "classId": "service_route_anchor",
                    "targetName": "Lumbridge bridge east approach",
                    "worldX": 3236,
                    "worldY": 3223,
                    "plane": 0,
                    "verifiedLive": False,
                },
                "routeSourceMismatch": {"classification": "route_source_mismatch"},
                "warnings": ["visible service target ignored because it does not match the selected route: outsideRouteCorridor"],
            },
        )

        context = navigation_intent_analyzer.analyze_navigation_intent(
            "woodcutting_bank",
            service_context=service,
            navigation_context=NavigationContext(collision_window_available=True),
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "WARN")
        self.assertTrue(payload["navigationNeeded"])
        self.assertEqual(payload["navigationReason"], "service_route_prior")
        self.assertEqual(payload["targetKind"], "service_route")
        self.assertEqual(payload["destinationTarget"]["worldX"], 3236)
        self.assertEqual(payload["destinationTarget"]["worldY"], 3223)

    def test_route_interaction_overrides_far_visible_service_target(self):
        visible_al_kharid_booth = service_candidate(
            targetName="Bank table",
            id=591,
            worldX=3266,
            worldY=3172,
            plane=0,
            distanceTiles=59,
            navigation={"directReachability": "unknown"},
        )
        service = ServiceContext(
            service_required=True,
            service_type_needed="bank",
            best_service_candidate=visible_al_kharid_booth,
            candidate_count=1,
            source_tick=23,
            service_route_context={
                "schema": "service_route_context.v1",
                "status": "PASS",
                "routeAvailable": True,
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "routeStepStatus": "route_interaction_visible",
                "actionReady": True,
                "routeObjectInterceptReady": True,
                "visibleInteractionTarget": {
                    "targetType": "sceneObject",
                    "classId": "route_transition",
                    "targetName": "Staircase",
                    "worldX": 3204,
                    "worldY": 3229,
                    "plane": 0,
                    "verifiedLive": True,
                    "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                },
            },
        )

        context = navigation_intent_analyzer.analyze_navigation_intent(
            "woodcutting_bank",
            service_context=service,
            navigation_context=NavigationContext(collision_window_available=True),
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "PASS")
        self.assertTrue(payload["navigationNeeded"])
        self.assertEqual(payload["navigationReason"], "service_route_prior")
        self.assertEqual(payload["targetKind"], "service_route")
        self.assertEqual(payload["destinationTarget"]["targetName"], "Staircase")
        self.assertEqual(payload["destinationTarget"]["worldX"], 3204)
        self.assertEqual(payload["destinationTarget"]["worldY"], 3229)

    def test_needs_service_intent_without_service_context_waits_for_context(self):
        context = navigation_intent_analyzer.analyze_navigation_intent(
            "woodcutting_bank",
            generic_task_state={"activeIntent": "needs_service"},
        )

        self.assertEqual(context.status, "WARN")
        self.assertFalse(context.navigation_needed)
        self.assertEqual(context.navigation_reason, "service_target_missing")

    def test_firemake_and_drop_policies_do_not_request_service_navigation(self):
        for policy_name in ("woodcutting_firemake", "woodcutting_drop"):
            with self.subTest(policy_name=policy_name):
                context = navigation_intent_analyzer.analyze_navigation_intent(
                    task_policy.resolve_task_policy(policy_name),
                    process_inventory_context=ProcessInventoryContext(process_required=True, process_type_needed="firemaking"),
                )

                self.assertEqual(context.status, "PASS")
                self.assertFalse(context.navigation_needed)
                self.assertEqual(context.target_kind, "process_inventory")
                self.assertEqual(context.navigation_reason, "local_navigation_only")

    def test_target_selected_reachable_resource_does_not_need_navigation(self):
        target = resource_candidate()

        context = navigation_intent_analyzer.analyze_navigation_intent(
            "observe_only",
            target_context=TargetContext(raw_best_target=target, candidate_count=1),
            generic_task_state={"activeIntent": "target_selected", "activeIntentTarget": target},
        )

        self.assertEqual(context.status, "PASS")
        self.assertFalse(context.navigation_needed)
        self.assertEqual(context.target_kind, "resource")
        self.assertEqual(context.navigation_reason, "target_reachable")

    def test_unreachable_resource_target_requests_navigation_context(self):
        target = resource_candidate(
            navigation={"directReachability": "blocked"},
            interactionRadiusTiles=2,
            approachRadiusTiles=3,
        )

        context = navigation_intent_analyzer.analyze_navigation_intent(
            "observe_only",
            target_context=TargetContext(raw_best_target=target, candidate_count=1),
            generic_task_state={"activeIntent": "target_selected", "activeIntentTarget": target},
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "WARN")
        self.assertTrue(payload["navigationNeeded"])
        self.assertEqual(payload["targetKind"], "resource")
        self.assertEqual(payload["navigationReason"], "target_unreachable")
        self.assertEqual(payload["directReachability"], "blocked")
        self.assertEqual(payload["destinationTarget"]["interactionRadiusTiles"], 2)
        self.assertEqual(payload["destinationTarget"]["approachRadiusTiles"], 3)

    def test_no_files_written(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            navigation_intent_analyzer.analyze_navigation_intent(
                "woodcutting_bank",
                service_context=ServiceContext(service_required=True, service_type_needed="bank", best_service_candidate=service_candidate()),
            )
            after = set(os.listdir(temp))

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

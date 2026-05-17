import os
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from analyzers import service_analyzer
import task_policy


class ServiceAnalyzerTest(unittest.TestCase):
    def test_reports_bank_service_only_when_policy_requires_service(self):
        context = service_analyzer.analyze_service_context(
            task_policy.resolve_task_policy("woodcutting_bank"),
            candidates=[
                {"targetType": "npc", "classId": "banker", "name": "Banker", "id": 2897},
                {"targetType": "sceneObject", "classId": "tree", "name": "Tree", "id": 1278},
            ],
            source_tick=12,
        )

        self.assertTrue(context.service_required)
        self.assertEqual(context.service_type_needed, "bank")
        self.assertEqual(context.best_service_candidate["classId"], "banker")
        self.assertEqual(context.source_tick, 12)
        self.assertTrue(context.to_dict()["serviceNeeded"])
        self.assertEqual(context.to_dict()["candidateCount"], 1)

    def test_identifies_bank_booth_by_name_and_class_and_preserves_context_fields(self):
        context = service_analyzer.analyze_service_context(
            task_policy.resolve_task_policy("woodcutting_bank"),
            candidates=[
                {
                    "targetType": "sceneObject",
                    "classId": "bank_booth",
                    "targetName": "Bank booth",
                    "id": 10355,
                    "worldX": 3208,
                    "worldY": 3219,
                    "plane": 0,
                    "distanceTiles": 4,
                    "navigation": {"directReachability": "reachable"},
                    "interactionRadiusTiles": 2,
                    "clickbox": {"x": 10, "y": 20, "w": 30, "h": 40},
                    "clickableHull": [{"x": 1, "y": 2}],
                    "actions": ["Bank"],
                    "menuActions": ["Bank"],
                }
            ],
            source_tick=44,
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "PASS")
        self.assertTrue(payload["serviceNeeded"])
        self.assertEqual(payload["candidateCount"], 1)
        self.assertEqual(payload["reachableCount"], 1)
        self.assertEqual(payload["bestServiceCandidate"]["classId"], "bank_booth")
        self.assertEqual(payload["bestServiceCandidate"]["serviceCandidateType"], "bank_booth")
        self.assertEqual(payload["nearestServiceCandidate"]["classId"], "bank_booth")
        self.assertEqual(payload["unknownReachabilityCount"], 0)
        self.assertEqual(payload["candidateCountsByType"], {"bank_booth": 1})
        self.assertEqual(len(payload["candidatesByType"]["bank_booth"]), 1)
        self.assertEqual(payload["bestServiceCandidate"]["interactionRadiusTiles"], 2)
        self.assertEqual(payload["bestServiceCandidate"]["clickbox"]["w"], 30)
        self.assertEqual(payload["bestServiceCandidate"]["clickableHull"], [{"x": 1, "y": 2}])

    def test_identifies_bank_booth_by_name_when_class_is_generic(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[{"targetType": "sceneObject", "classId": "bank_related", "targetName": "Bank booth", "distanceTiles": 3}],
        )

        self.assertEqual(context.best_service_candidate["serviceCandidateType"], "bank_booth")
        self.assertEqual(context.to_dict()["candidateCountsByType"], {"bank_booth": 1})

    def test_identifies_banker_npc_by_name_and_class(self):
        context = service_analyzer.analyze_service_context(
            task_policy.resolve_task_policy("woodcutting_bank"),
            candidates=[
                {
                    "targetType": "npc",
                    "classId": "banker",
                    "name": "Banker",
                    "id": 2897,
                    "worldX": 3207,
                    "worldY": 3222,
                    "plane": 0,
                    "distanceTiles": 2,
                }
            ],
        )

        self.assertEqual(context.best_service_candidate["targetType"], "npc")
        self.assertEqual(context.best_service_candidate["classId"], "banker")
        self.assertEqual(context.best_service_candidate["serviceCandidateType"], "banker")
        self.assertIn("service.actions", context.missing_capabilities)

    def test_identifies_deposit_boxes_by_class_and_name(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Deposit box", "distanceTiles": 4},
                {"targetType": "sceneObject", "classId": "bank_related", "targetName": "Bank deposit box", "distanceTiles": 2},
                {"targetType": "sceneObject", "classId": "bank_related", "targetName": "Deposit chest", "distanceTiles": 6},
            ],
        )
        payload = context.to_dict()

        self.assertEqual(payload["candidateCountsByType"], {"deposit_box": 2, "deposit_chest": 1})
        self.assertEqual(payload["nearestServiceCandidate"]["serviceCandidateType"], "deposit_box")

    def test_best_nearest_and_reachability_buckets_are_reported(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "bank_booth", "targetName": "Bank booth", "qualityScore": 30, "distanceTiles": 1},
                {"targetType": "npc", "classId": "banker", "targetName": "Banker", "qualityScore": 100, "distanceTiles": 5, "navigation": {"directReachability": "reachable"}},
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Deposit box", "qualityScore": 80, "distanceTiles": 2, "navigation": {"directReachability": "blocked"}},
            ],
        )
        payload = context.to_dict()

        self.assertEqual(payload["bestServiceCandidate"]["serviceCandidateType"], "bank_booth")
        self.assertEqual(payload["nearestServiceCandidate"]["serviceCandidateType"], "bank_booth")
        self.assertEqual(payload["reachableCount"], 1)
        self.assertEqual(payload["unknownReachabilityCount"], 1)

    def test_bank_booth_outranks_generic_bank_table(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {
                    "targetType": "sceneObject",
                    "classId": "bank_related",
                    "targetName": "Bank table",
                    "qualityScore": 100,
                    "distanceTiles": 1,
                    "navigation": {"directReachability": "reachable"},
                },
                {
                    "targetType": "sceneObject",
                    "classId": "bank_booth",
                    "targetName": "Bank booth",
                    "qualityScore": 10,
                    "distanceTiles": 6,
                    "navigation": {"directReachability": "unknown"},
                },
            ],
        )

        best = context.to_dict()["bestServiceCandidate"]
        self.assertEqual(best["serviceCandidateType"], "bank_booth")
        self.assertLess(best["serviceTypePriority"], context.service_candidates[0]["serviceTypePriority"])
        self.assertIn("type priority", best["serviceSelectedReason"])
        self.assertIn("serviceScore", best)
        self.assertIn("serviceReachabilityContribution", best)
        self.assertIn("serviceDistanceContribution", best)
        self.assertIn("servicePathingContribution", best)

    def test_deposit_box_outranks_generic_bank_related_fallback(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "bank_related", "targetName": "Bank table", "qualityScore": 100, "distanceTiles": 1},
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Deposit box", "qualityScore": 1, "distanceTiles": 8},
            ],
        )

        self.assertEqual(context.best_service_candidate["serviceCandidateType"], "deposit_box")

    def test_distance_breaks_ties_within_same_service_priority(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "bank_booth", "targetName": "Bank booth", "objectKey": "far", "distanceTiles": 8},
                {"targetType": "sceneObject", "classId": "bank_booth", "targetName": "Bank booth", "objectKey": "near", "distanceTiles": 2},
            ],
        )

        self.assertEqual(context.best_service_candidate["objectKey"], "near")
        self.assertEqual(context.best_service_candidate["serviceDistanceContribution"], 2.0)

    def test_does_not_request_service_for_process_or_continue_policies(self):
        for policy_name in ("woodcutting_firemake", "woodcutting_drop", "combat_default", "observe_only"):
            with self.subTest(policy_name=policy_name):
                context = service_analyzer.analyze_service_context(task_policy.resolve_task_policy(policy_name), candidates=[])
                self.assertFalse(context.service_required)
                self.assertIsNone(context.service_type_needed)
                self.assertEqual(context.missing_capabilities, [])
                self.assertEqual(context.warnings, [])

    def test_no_service_candidate_is_clean_warning(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[{"targetType": "sceneObject", "classId": "tree", "targetName": "Tree"}],
        )
        payload = context.to_dict()

        self.assertEqual(context.status, "WARN")
        self.assertTrue(payload["serviceNeeded"])
        self.assertEqual(payload["candidateCount"], 0)
        self.assertEqual(payload["candidatesByType"], {})
        self.assertIn("bank_service candidate", " ".join(context.warnings))

    def test_service_context_preserves_read_only_interaction_metadata(self):
        context = service_analyzer.analyze_service_context(
            task_policy.resolve_task_policy("woodcutting_bank"),
            candidates=[
                {
                    "targetType": "sceneObject",
                    "classId": "deposit_box",
                    "name": "Deposit box",
                    "interactionRadiusTiles": 1,
                    "approachRadiusTiles": 2,
                    "clickboxPolygon": [{"x": 1, "y": 1}],
                }
            ],
        )

        candidate = context.to_dict()["bestServiceCandidate"]
        self.assertEqual(candidate["interactionRadiusTiles"], 1)
        self.assertEqual(candidate["approachRadiusTiles"], 2)
        self.assertEqual(candidate["clickboxPolygon"], [{"x": 1, "y": 1}])

    def test_service_ranking_writes_no_files(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            context = service_analyzer.analyze_service_context(
                "woodcutting_bank",
                candidates=[{"targetType": "sceneObject", "classId": "bank_booth", "targetName": "Bank booth"}],
            )
            after = set(os.listdir(temp))

        self.assertEqual(before, after)
        self.assertEqual(context.best_service_candidate["serviceCandidateType"], "bank_booth")


if __name__ == "__main__":
    unittest.main()

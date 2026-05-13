import sys
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

    def test_identifies_bank_booth_by_name_and_class_without_action_fields(self):
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
        self.assertEqual(payload["nearestServiceCandidate"]["classId"], "bank_booth")
        self.assertNotIn("actions", payload["bestServiceCandidate"])
        self.assertNotIn("menuActions", payload["bestServiceCandidate"])

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
        self.assertIn("service.actions", context.missing_capabilities)

    def test_does_not_request_service_for_process_or_continue_policies(self):
        for policy_name in ("woodcutting_firemake", "woodcutting_drop", "combat_default", "observe_only"):
            with self.subTest(policy_name=policy_name):
                context = service_analyzer.analyze_service_context(task_policy.resolve_task_policy(policy_name), candidates=[])
                self.assertFalse(context.service_required)
                self.assertIsNone(context.service_type_needed)
                self.assertEqual(context.missing_capabilities, [])
                self.assertEqual(context.warnings, [])

    def test_service_context_has_no_action_fields(self):
        context = service_analyzer.analyze_service_context(
            task_policy.resolve_task_policy("woodcutting_bank"),
            candidates=[{"targetType": "sceneObject", "classId": "deposit_box", "name": "Deposit box", "actions": ["Deposit"]}],
        )

        def walk_keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield str(key)
                    yield from walk_keys(item)
            elif isinstance(value, list):
                for item in value:
                    yield from walk_keys(item)

        keys = " ".join(key.lower() for key in walk_keys(context.to_dict()))

        for forbidden in ("action", "click", "mouse", "keyboard", "menu", "invoke", "execute"):
            self.assertNotIn(forbidden, keys)


if __name__ == "__main__":
    unittest.main()

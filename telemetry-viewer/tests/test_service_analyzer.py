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

    def test_does_not_request_service_for_process_or_continue_policies(self):
        for policy_name in ("woodcutting_firemake", "woodcutting_drop", "combat_default", "observe_only"):
            with self.subTest(policy_name=policy_name):
                context = service_analyzer.analyze_service_context(task_policy.resolve_task_policy(policy_name), candidates=[])
                self.assertFalse(context.service_required)
                self.assertIsNone(context.service_type_needed)
                self.assertEqual(context.missing_capabilities, [])
                self.assertEqual(context.warnings, [])

    def test_service_context_has_no_action_fields(self):
        context = service_analyzer.analyze_service_context(task_policy.resolve_task_policy("woodcutting_bank"), candidates=[])
        keys = " ".join(str(key).lower() for key in context.to_dict().keys())

        for forbidden in ("action", "click", "mouse", "keyboard", "menu", "invoke", "execute"):
            self.assertNotIn(forbidden, keys)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from analyzers import process_inventory_analyzer
from analyzers.live_state import InventoryContext
import task_policy


class ProcessInventoryAnalyzerTest(unittest.TestCase):
    def test_reports_firemaking_process_context(self):
        inventory = InventoryContext(
            inventory={"items": [{"slot": 0, "itemId": 1511, "quantity": 12}, {"slot": 1, "itemId": 590, "quantity": 1}]},
            progress={"currentHeldCount": 12},
        )

        context = process_inventory_analyzer.analyze_process_inventory_context(
            task_policy.resolve_task_policy("woodcutting_firemake"),
            inventory,
            source_tick=20,
        )

        self.assertTrue(context.process_required)
        self.assertEqual(context.process_type_needed, "firemaking")
        self.assertEqual(context.resource_disposition, "burn")
        self.assertTrue(context.resources_available)
        self.assertTrue(context.tinderbox_present)
        self.assertEqual(context.tinderbox_status, "present")
        self.assertEqual(context.source_tick, 20)

    def test_firemaking_reports_tinderbox_missing_or_unknown(self):
        missing = process_inventory_analyzer.analyze_process_inventory_context(
            task_policy.resolve_task_policy("woodcutting_firemake"),
            InventoryContext(inventory={"items": [{"slot": 0, "itemId": 1511, "quantity": 12}]}, progress={"currentHeldCount": 12}),
        )
        unknown = process_inventory_analyzer.analyze_process_inventory_context(
            task_policy.resolve_task_policy("woodcutting_firemake"),
            InventoryContext(inventory={}, progress={"currentHeldCount": 12}),
        )

        self.assertFalse(missing.tinderbox_present)
        self.assertEqual(missing.tinderbox_status, "missing")
        self.assertIn("tinderbox not observed", " ".join(missing.warnings).lower())
        self.assertIsNone(unknown.tinderbox_present)
        self.assertEqual(unknown.tinderbox_status, "unknown")
        self.assertIn("inventory.items", unknown.missing_capabilities)

    def test_reports_drop_process_context_without_service(self):
        inventory = InventoryContext(progress={"currentHeldCount": 5})

        context = process_inventory_analyzer.analyze_process_inventory_context(
            task_policy.resolve_task_policy("woodcutting_drop"),
            inventory,
        )

        self.assertTrue(context.process_required)
        self.assertEqual(context.process_type_needed, "drop")
        self.assertEqual(context.resource_disposition, "drop")
        self.assertTrue(context.resources_available)
        self.assertIsNone(context.service_type_needed)
        self.assertEqual(context.tinderbox_status, "not_required")

    def test_non_process_policies_do_not_request_inventory_processing(self):
        for policy_name in ("woodcutting_bank", "combat_default", "observe_only"):
            with self.subTest(policy_name=policy_name):
                context = process_inventory_analyzer.analyze_process_inventory_context(
                    task_policy.resolve_task_policy(policy_name),
                    InventoryContext(progress={"currentHeldCount": 28}),
                )
                self.assertFalse(context.process_required)
                self.assertFalse(context.resources_available)


if __name__ == "__main__":
    unittest.main()

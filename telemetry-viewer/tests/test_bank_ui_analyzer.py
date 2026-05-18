import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import task_policy
from analyzers import bank_ui_analyzer
from analyzers.live_state import InventoryContext, PathingContext, ServiceContext


def inventory_context() -> InventoryContext:
    return InventoryContext(
        inventory={
            "known": True,
            "freeSlots": 0,
            "filledSlots": 28,
            "itemCount": 28,
            "items": [{"slot": slot, "itemId": 1511, "quantity": 1} for slot in range(28)],
        },
        progress={"currentHeldCount": 28},
        source_tick=77,
    )


def service_ready_context() -> ServiceContext:
    return ServiceContext(
        service_required=True,
        service_type_needed="bank_full",
        service_ready=True,
        service_ready_reason="arrived_at_service",
        service_ready_stable_for_ticks=2,
        selected_service_target_name="Bank booth",
        selected_service_target_tile={"worldX": 3208, "worldY": 3219, "plane": 0},
        source_tick=77,
    )


def arrived_pathing_context() -> PathingContext:
    return PathingContext(
        service_ready=True,
        service_ready_reason="arrived_at_service",
        service_ready_stable_for_ticks=2,
        path_completed=True,
        source_tick=77,
    )


class BankUiAnalyzerTest(unittest.TestCase):
    def test_missing_payload_reports_missing_capability(self):
        context = bank_ui_analyzer.analyze_bank_ui_context(
            task_policy.resolve_task_policy("woodcutting_bank"),
            bank_ui_payload=None,
            inventory_context=inventory_context(),
            service_context=service_ready_context(),
            pathing_context=arrived_pathing_context(),
            source_tick=77,
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "WARN")
        self.assertIsNone(payload["bankOpen"])
        self.assertFalse(payload["bankReadable"])
        self.assertIn("bank_ui.telemetry", payload["missingCapabilities"])
        self.assertEqual(payload["sourceTick"], 77)

    def test_bank_closed_preserves_inventory_summary_after_service_ready(self):
        context = bank_ui_analyzer.analyze_bank_ui_context(
            "woodcutting_bank",
            bank_ui_payload={
                "bankOpen": False,
                "bankPinOpen": False,
                "bankRootVisible": False,
                "inventorySummary": {"freeSlots": 0, "occupiedSlots": 28, "matchingResourceCount": 28},
            },
            inventory_context=inventory_context(),
            service_context=service_ready_context(),
            pathing_context=arrived_pathing_context(),
            source_tick=78,
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "PASS")
        self.assertFalse(payload["bankOpen"])
        self.assertFalse(payload["bankReadable"])
        self.assertEqual(payload["inventorySummary"]["freeSlots"], 0)
        self.assertEqual(payload["inventorySummary"]["matchingResourceCount"], 28)

    def test_visible_root_and_container_make_bank_readable(self):
        context = bank_ui_analyzer.analyze_bank_ui_context(
            "woodcutting_bank",
            bank_ui_payload={
                "topLevelInterfaceId": 12,
                "bankOpen": True,
                "bankPinOpen": False,
                "bankRootVisible": True,
                "bankContainerVisible": True,
                "bankInventoryVisible": True,
                "depositInventoryButtonVisible": True,
                "closeButtonVisible": True,
                "bankSummary": {"occupiedSlots": 42, "uniqueItemIds": [1511, 1521]},
                "inventorySummary": {"freeSlots": 0, "occupiedSlots": 28, "matchingResourceCount": 28},
            },
            inventory_context=inventory_context(),
            service_context=service_ready_context(),
            pathing_context=arrived_pathing_context(),
            source_tick=79,
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "PASS")
        self.assertTrue(payload["bankOpen"])
        self.assertTrue(payload["bankReadable"])
        self.assertTrue(payload["bankContainerReadable"])
        self.assertTrue(payload["bankInventoryReadable"])
        self.assertTrue(payload["depositInventoryAvailable"])
        self.assertTrue(payload["closeButtonAvailable"])
        self.assertTrue(payload["closeButtonVisible"])
        self.assertEqual(payload["topLevelInterfaceId"], 12)
        self.assertEqual(payload["bankSummary"]["occupiedSlots"], 42)
        self.assertEqual(payload["bankSummary"]["uniqueItemCount"], 2)

    def test_bank_pin_open_blocks_readability(self):
        context = bank_ui_analyzer.analyze_bank_ui_context(
            "woodcutting_bank",
            bank_ui_payload={
                "bankOpen": True,
                "bankPinOpen": True,
                "bankRootVisible": True,
                "bankContainerVisible": False,
            },
            inventory_context=inventory_context(),
            service_context=service_ready_context(),
            pathing_context=arrived_pathing_context(),
            source_tick=80,
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "WARN")
        self.assertTrue(payload["bankOpen"])
        self.assertTrue(payload["bankPinOpen"])
        self.assertFalse(payload["bankReadable"])
        self.assertIn("bank_pin_required", payload["warnings"])


if __name__ == "__main__":
    unittest.main()

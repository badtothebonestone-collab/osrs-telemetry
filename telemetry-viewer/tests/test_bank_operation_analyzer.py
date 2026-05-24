import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import resource_progress
import task_policy
from analyzers import bank_operation_analyzer
from analyzers.live_state import BankUiContext, InventoryContext


WOODCUTTING = resource_progress.ResourceDefinition(
    "woodcutting_logs",
    (1511, 1521, 1519, 1517, 1515, 1513),
    "logs",
)


def log_item(slot: int, item_id: int = 1511, quantity: int = 1) -> dict:
    return {"slot": slot, "itemId": item_id, "quantity": quantity}


def coin_item(slot: int, quantity: int = 100) -> dict:
    return {"slot": slot, "itemId": 995, "quantity": quantity}


def inventory_context(items: list[dict]) -> InventoryContext:
    free_slots = max(0, 28 - len(items))
    return InventoryContext(
        inventory={
            "known": True,
            "items": items,
            "freeSlots": free_slots,
            "filledSlots": len(items),
            "inventoryFull": free_slots == 0,
            "inventorySlotCount": 28,
            "slotCount": 28,
        },
        progress={"currentHeldCount": sum(item.get("quantity", 1) for item in items if item.get("itemId") in WOODCUTTING.item_ids)},
        source_tick=42,
    )


def bank_ui_context(*, readable: bool = True, deposit_inventory_available: bool | None = True, pin_open: bool = False) -> BankUiContext:
    return BankUiContext(
        bank_open=True if readable or pin_open else False,
        bank_readable=readable and not pin_open,
        bank_pin_open=pin_open,
        bank_root_visible=readable or pin_open,
        bank_container_visible=readable,
        bank_inventory_visible=readable,
        deposit_inventory_available=deposit_inventory_available,
        deposit_inventory_button_visible=deposit_inventory_available,
        source_tick=42,
    )


def bank_ui_context_with_inventory_slots() -> BankUiContext:
    context = bank_ui_context(deposit_inventory_available=True)
    context.inventory_slots = [
        {"slot": 9, "itemId": 1511, "quantity": 1, "bounds": {"x": 550, "y": 250, "w": 32, "h": 32}, "actions": ["Deposit-1", "Deposit-All"]},
        {"slot": 10, "itemId": 995, "quantity": 42, "bounds": {"x": 590, "y": 250, "w": 32, "h": 32}, "actions": ["Deposit-1"]},
    ]
    return context


class BankOperationAnalyzerTest(unittest.TestCase):
    def test_readable_bank_with_logs_needs_deposit_inventory(self):
        context = bank_operation_analyzer.analyze_bank_operation_context(
            task_policy.resolve_task_policy("woodcutting_bank"),
            bank_ui_context=bank_ui_context(deposit_inventory_available=True),
            inventory_context=inventory_context([log_item(0, quantity=2), log_item(1, item_id=1521), coin_item(2)]),
            resource_definition=WOODCUTTING,
            current_task_state={"phase": "service_open"},
            source_tick=42,
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "PASS")
        self.assertTrue(payload["operationNeeded"])
        self.assertEqual(payload["operationType"], "deposit_inventory")
        self.assertEqual(payload["resourceItemsHeld"], 2)
        self.assertEqual(payload["resourceItemSlots"], [0, 1])
        self.assertEqual(payload["resourceItemQuantity"], 3)
        self.assertEqual(payload["nonResourceItemsHeld"], 1)
        self.assertEqual(payload["inventoryFreeSlots"], 25)
        self.assertFalse(payload["bankingComplete"])

    def test_readable_bank_without_deposit_inventory_uses_resource_deposit(self):
        context = bank_operation_analyzer.analyze_bank_operation_context(
            "woodcutting_bank",
            bank_ui_context=bank_ui_context(deposit_inventory_available=False),
            inventory_context=inventory_context([log_item(9), coin_item(10)]),
            resource_definition=WOODCUTTING,
            source_tick=43,
        )

        payload = context.to_dict()
        self.assertTrue(payload["operationNeeded"])
        self.assertEqual(payload["operationType"], "deposit_resources")
        self.assertFalse(payload["depositInventoryAvailable"])
        self.assertEqual(payload["resourceItemSlots"], [9])

    def test_resource_deposit_carries_slot_widget_bounds(self):
        context = bank_operation_analyzer.analyze_bank_operation_context(
            "woodcutting_bank",
            bank_ui_context=bank_ui_context_with_inventory_slots(),
            inventory_context=inventory_context([log_item(9), coin_item(10)]),
            resource_definition=WOODCUTTING,
            source_tick=43,
        )

        payload = context.to_dict()
        self.assertEqual(payload["resourceItemSlots"], [9])
        self.assertEqual(payload["resourceItemSlotBounds"][0]["x"], 550)
        self.assertEqual(payload["resourceItemWidgets"][0]["actions"], ["Deposit-1", "Deposit-All"])
        self.assertEqual(payload["resourceDisplayName"], "logs")

    def test_resource_slots_can_come_from_bank_ui_inventory_summary(self):
        bank_ui = bank_ui_context_with_inventory_slots()
        bank_ui.inventory_summary = {
            "known": True,
            "items": [log_item(9), coin_item(10)],
            "freeSlots": 26,
            "occupiedSlots": 2,
        }

        context = bank_operation_analyzer.analyze_bank_operation_context(
            "woodcutting_bank",
            bank_ui_context=bank_ui,
            inventory_context=None,
            resource_definition=WOODCUTTING,
            source_tick=43,
        )

        payload = context.to_dict()
        self.assertEqual(payload["resourceItemSlots"], [9])
        self.assertEqual(payload["resourceItemSlotBounds"][0]["x"], 550)
        self.assertEqual(payload["nonResourceItemsHeld"], 1)

    def test_readable_bank_without_logs_is_complete(self):
        context = bank_operation_analyzer.analyze_bank_operation_context(
            "woodcutting_bank",
            bank_ui_context=bank_ui_context(),
            inventory_context=inventory_context([coin_item(0)]),
            resource_definition=WOODCUTTING,
            source_tick=44,
        )

        payload = context.to_dict()
        self.assertFalse(payload["operationNeeded"])
        self.assertEqual(payload["operationType"], "none")
        self.assertEqual(payload["resourceItemsHeld"], 0)
        self.assertEqual(payload["resourceItemQuantity"], 0)
        self.assertTrue(payload["bankingComplete"])
        self.assertEqual(payload["completionReason"], "no_resource_items_held")

    def test_bank_not_readable_waits_for_readable_bank(self):
        context = bank_operation_analyzer.analyze_bank_operation_context(
            "woodcutting_bank",
            bank_ui_context=bank_ui_context(readable=False, deposit_inventory_available=None),
            inventory_context=inventory_context([log_item(0)]),
            resource_definition=WOODCUTTING,
            source_tick=45,
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "WARN")
        self.assertFalse(payload["operationNeeded"])
        self.assertEqual(payload["operationType"], "unknown")
        self.assertFalse(payload["bankReadable"])
        self.assertIn("waiting_for_readable_bank", payload["warnings"])
        self.assertEqual(payload["completionReason"], "waiting_for_readable_bank")

    def test_closed_bank_with_summary_showing_no_logs_is_complete(self):
        bank_ui = bank_ui_context(readable=False, deposit_inventory_available=False)
        bank_ui.inventory_summary = {
            "known": True,
            "items": [coin_item(0)],
            "freeSlots": 27,
            "occupiedSlots": 1,
        }

        context = bank_operation_analyzer.analyze_bank_operation_context(
            "woodcutting_bank",
            bank_ui_context=bank_ui,
            inventory_context=None,
            resource_definition=WOODCUTTING,
            source_tick=45,
        )

        payload = context.to_dict()
        self.assertFalse(payload["operationNeeded"])
        self.assertEqual(payload["operationType"], "none")
        self.assertFalse(payload["bankReadable"])
        self.assertTrue(payload["bankingComplete"])
        self.assertEqual(payload["resourceItemQuantity"], 0)
        self.assertEqual(payload["completionReason"], "no_resource_items_held")

    def test_bank_pin_open_reports_user_resolution_blocker(self):
        context = bank_operation_analyzer.analyze_bank_operation_context(
            "woodcutting_bank",
            bank_ui_context=bank_ui_context(readable=False, pin_open=True),
            inventory_context=inventory_context([log_item(0)]),
            resource_definition=WOODCUTTING,
            source_tick=46,
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "WARN")
        self.assertEqual(payload["operationType"], "unknown")
        self.assertFalse(payload["bankingComplete"])
        self.assertIn("bank_pin_required", payload["warnings"])
        self.assertEqual(payload["completionReason"], "bank_pin_required")

    def test_missing_bank_context_reports_missing_capability(self):
        context = bank_operation_analyzer.analyze_bank_operation_context(
            "woodcutting_bank",
            bank_ui_context=None,
            inventory_context=inventory_context([log_item(0)]),
            resource_definition=WOODCUTTING,
            source_tick=47,
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "WARN")
        self.assertIn("bank_ui.telemetry", payload["missingCapabilities"])
        self.assertIn("bank operation is waiting for bank UI context", payload["warnings"])


if __name__ == "__main__":
    unittest.main()

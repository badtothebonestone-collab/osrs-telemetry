import sys
import unittest
from pathlib import Path
from unittest import mock


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import resource_progress
from analyzers import inventory_analyzer


WOODCUTTING = resource_progress.ResourceDefinition("woodcutting_logs", (1511, 1521, 1519, 1517, 1515, 1513), "logs")


class InventoryAnalyzerTest(unittest.TestCase):
    def test_uses_resource_progress_and_keeps_progress_math_there(self):
        state = resource_progress.ResourceProgressState()
        response = {"sessionPath": "session-a", "status": {"lastProcessedTick": 1}}
        inventory = {
            "inventorySlotCount": 28,
            "items": [{"slot": slot, "itemId": 1511, "quantity": 1} for slot in range(5)],
        }

        with mock.patch.object(resource_progress, "initialize_or_update_progress", wraps=resource_progress.initialize_or_update_progress) as wrapped:
            context = inventory_analyzer.analyze_inventory(
                response=response,
                inventory=inventory,
                progress_state=state,
                resource_definition=WOODCUTTING,
                goal_count=5,
            )

        self.assertTrue(wrapped.called)
        self.assertEqual(context.progress["baselineHeldCount"], 5)
        self.assertEqual(context.progress["displayedGoalProgress"], 0)
        self.assertTrue(all(row["itemId"] is not None for row in context.matched_slots))

    def test_item_id_none_is_not_counted(self):
        state = resource_progress.ResourceProgressState()
        context = inventory_analyzer.analyze_inventory(
            response={"sessionPath": "session-a", "latestTick": 1},
            inventory={
                "inventorySignature": "sig-a",
                "inventorySlotCount": 28,
                "items": [
                    {"slot": 9, "itemId": None, "quantity": None, "counted": True},
                    {"slot": 10, "itemId": 1511, "quantity": 1},
                ],
            },
            progress_state=state,
            resource_definition=WOODCUTTING,
            goal_count=5,
        )

        self.assertEqual(context.progress["currentHeldCount"], 1)
        self.assertEqual(context.progress["matchedSlots"], [10])
        self.assertIn(resource_progress.INVALID_MATCHED_SLOT_WARNING, context.warnings)

    def test_items_known_false_empty_list_does_not_become_zero_count(self):
        state = resource_progress.ResourceProgressState()

        context = inventory_analyzer.analyze_inventory(
            response={"sessionPath": "session-a", "latestTick": 1},
            inventory={
                "inventorySignature": "sig-a",
                "inventorySlotCount": 28,
                "items": [],
                "itemsKnown": False,
                "itemListAvailable": False,
            },
            progress_state=state,
            resource_definition=WOODCUTTING,
            goal_count=5,
        )

        self.assertFalse(context.progress["currentSnapshotValid"])
        self.assertIsNone(context.progress["currentHeldCount"])
        self.assertEqual(context.progress["progressSource"], "baseline_pending")


if __name__ == "__main__":
    unittest.main()

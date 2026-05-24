import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from analyzers import return_to_resource_analyzer
from analyzers.live_state import BankOperationContext, InventoryContext, TargetContext


def inventory_context(*, free_slots: int) -> InventoryContext:
    return InventoryContext(
        inventory={
            "known": True,
            "freeSlots": free_slots,
            "filledSlots": 28 - free_slots,
            "inventoryFull": free_slots == 0,
        },
        progress={"currentHeldCount": 0, "baselineEstablished": True, "baselineHeldCount": 0},
        source_tick=42,
    )


def tree_target() -> dict:
    return {
        "objectKey": "oak-1",
        "targetName": "Oak tree",
        "targetType": "sceneObject",
        "classId": "tree",
        "id": 10820,
        "worldX": 3201,
        "worldY": 3201,
        "plane": 0,
        "distanceTiles": 4,
        "navigation": {"directReachability": "reachable", "pathLengthTiles": 0},
    }


class ReturnToResourceAnalyzerTest(unittest.TestCase):
    def test_banking_complete_with_free_slots_needs_return(self):
        context = return_to_resource_analyzer.analyze_return_to_resource_context(
            "woodcutting_bank",
            bank_operation_context=BankOperationContext(
                banking_complete=True,
                inventory_free_slots=15,
                source_tick=42,
            ),
            inventory_context=inventory_context(free_slots=15),
            target_context=TargetContext(raw_best_target=None, candidate_count=0, source_tick=42),
            current_task_state={"phase": "service_complete"},
            source_tick=42,
        )

        payload = context.to_dict()
        self.assertTrue(payload["returnNeeded"])
        self.assertTrue(payload["serviceComplete"])
        self.assertFalse(payload["returnReady"])
        self.assertFalse(payload["resourceTargetAvailable"])
        self.assertEqual(payload["reason"], "no_resource_target_observed")
        self.assertIn("target.candidates", payload["missingCapabilities"])

    def test_banking_complete_with_tree_target_is_return_ready(self):
        target = tree_target()
        context = return_to_resource_analyzer.analyze_return_to_resource_context(
            "woodcutting_bank",
            bank_operation_context=BankOperationContext(
                banking_complete=True,
                inventory_free_slots=15,
                source_tick=43,
            ),
            inventory_context=inventory_context(free_slots=15),
            target_context=TargetContext(raw_best_target=target, candidates=[target], candidate_count=1, source_tick=43),
            current_task_state={"phase": "service_complete"},
            source_tick=43,
        )

        payload = context.to_dict()
        self.assertTrue(payload["returnNeeded"])
        self.assertTrue(payload["returnReady"])
        self.assertTrue(payload["resourceTargetAvailable"])
        self.assertEqual(payload["bestResourceTarget"]["objectKey"], "oak-1")
        self.assertFalse(payload["resourcePathingNeeded"])
        self.assertEqual(payload["reason"], "resource_target_available")

    def test_broad_service_candidate_is_not_treated_as_resource_target(self):
        bank_target = {
            "objectKey": "bank-booth-1",
            "targetName": "Bank booth",
            "targetType": "sceneObject",
            "classId": "bank_related",
            "worldX": 3208,
            "worldY": 3221,
            "plane": 2,
            "navigation": {"directReachability": "reachable"},
        }
        context = return_to_resource_analyzer.analyze_return_to_resource_context(
            "woodcutting_bank",
            bank_operation_context=BankOperationContext(
                banking_complete=True,
                inventory_free_slots=15,
                source_tick=45,
            ),
            inventory_context=inventory_context(free_slots=15),
            target_context=TargetContext(
                candidates=[bank_target],
                broad_candidates=[bank_target],
                raw_best_target=None,
                candidate_count=1,
                profile_candidate_count=0,
                source_tick=45,
            ),
            current_task_state={"phase": "service_complete"},
            source_tick=45,
        )

        payload = context.to_dict()
        self.assertTrue(payload["returnNeeded"])
        self.assertFalse(payload["returnReady"])
        self.assertFalse(payload["resourceTargetAvailable"])
        self.assertIsNone(payload["bestResourceTarget"])
        self.assertIn("target.candidates", payload["missingCapabilities"])

    def test_resource_return_anchor_is_not_treated_as_reacquired_resource_target(self):
        return_anchor = {
            "objectKey": "resource-return-3203-3238-0",
            "targetName": "Lumbridge Castle west approach return",
            "targetType": "tile",
            "classId": "resource_return",
            "worldX": 3203,
            "worldY": 3238,
            "plane": 0,
            "navigation": {"directReachability": "unknown"},
        }
        context = return_to_resource_analyzer.analyze_return_to_resource_context(
            "woodcutting_bank",
            bank_operation_context=BankOperationContext(
                banking_complete=True,
                inventory_free_slots=13,
                source_tick=46,
            ),
            inventory_context=inventory_context(free_slots=13),
            target_context=TargetContext(
                raw_best_target=return_anchor,
                candidates=[return_anchor],
                candidate_count=1,
                profile_candidate_count=0,
                source_tick=46,
            ),
            current_task_state={"phase": "return_to_resource"},
            source_tick=46,
        )

        payload = context.to_dict()
        self.assertTrue(payload["returnNeeded"])
        self.assertFalse(payload["returnReady"])
        self.assertFalse(payload["resourceTargetAvailable"])
        self.assertIsNone(payload["bestResourceTarget"])
        self.assertEqual(payload["reason"], "no_resource_target_observed")

    def test_banking_not_complete_does_not_request_return(self):
        context = return_to_resource_analyzer.analyze_return_to_resource_context(
            "woodcutting_bank",
            bank_operation_context=BankOperationContext(
                banking_complete=False,
                operation_needed=True,
                inventory_free_slots=0,
                source_tick=44,
            ),
            inventory_context=inventory_context(free_slots=0),
            target_context=TargetContext(source_tick=44),
            source_tick=44,
        )

        payload = context.to_dict()
        self.assertFalse(payload["returnNeeded"])
        self.assertFalse(payload["returnReady"])
        self.assertEqual(payload["reason"], "waiting_for_banking_complete")


if __name__ == "__main__":
    unittest.main()

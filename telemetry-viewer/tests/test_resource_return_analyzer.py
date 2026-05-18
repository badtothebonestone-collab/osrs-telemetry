import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from analyzers import resource_return_analyzer
from analyzers.live_state import BankOperationContext, BankUiContext, InventoryContext, PlayerContext, TargetContext


def inventory_context(*, free_slots: int = 23) -> InventoryContext:
    return InventoryContext(
        inventory={
            "known": True,
            "freeSlots": free_slots,
            "filledSlots": 28 - free_slots,
            "inventoryFull": free_slots == 0,
        },
        source_tick=42,
    )


def player_context(*, x: int = 3155, y: int = 3236, plane: int = 0) -> PlayerContext:
    return PlayerContext(world_x=x, world_y=y, plane=plane, scene_x=51, scene_y=52)


def tree_target(*, name: str = "Oak tree", x: int = 3156, y: int = 3237, plane: int = 0) -> dict:
    return {
        "objectKey": f"tree-{x}-{y}",
        "targetName": name,
        "targetType": "sceneObject",
        "classId": "tree",
        "id": 10820,
        "worldX": x,
        "worldY": y,
        "plane": plane,
        "sceneX": 52,
        "sceneY": 53,
        "distanceTiles": 2,
        "navigation": {"directReachability": "reachable", "pathLengthTiles": 2},
    }


def bank_target() -> dict:
    return {
        "objectKey": "bank-booth-3208-3219",
        "targetName": "Bank booth",
        "targetType": "sceneObject",
        "classId": "bank_booth",
        "id": 10355,
        "worldX": 3208,
        "worldY": 3219,
        "plane": 0,
    }


class ResourceReturnAnalyzerTest(unittest.TestCase):
    def test_resource_memory_records_last_visible_tree_target(self):
        memory = resource_return_analyzer.ResourceAreaMemoryState()
        target = tree_target()

        updated = resource_return_analyzer.update_resource_area_memory(
            "woodcutting_bank",
            memory,
            inventory_context=inventory_context(free_slots=23),
            target_context=TargetContext(raw_best_target=target, candidates=[target], profile_candidates=[target], candidate_count=1, source_tick=100),
            bank_ui_context=BankUiContext(bank_open=False, source_tick=100),
            current_task_state={"phase": "target_selected", "activeIntent": "continue_current_target", "activeIntentTarget": target},
            player_context=player_context(),
            source_tick=100,
        )

        payload = updated.to_dict(source_tick=105)
        self.assertTrue(payload["resourceMemoryValid"])
        self.assertEqual(payload["lastResourceActivityTick"], 100)
        self.assertEqual(payload["lastResourceTargetName"], "Oak tree")
        self.assertEqual(payload["lastResourceTargetTile"], {"worldX": 3156, "worldY": 3237, "plane": 0})
        self.assertEqual(payload["lastResourcePlayerTile"], {"worldX": 3155, "worldY": 3236, "plane": 0})
        self.assertEqual(payload["lastResourceClusterCenter"], {"worldX": 3156, "worldY": 3237, "plane": 0})
        self.assertEqual(payload["resourceMemoryAgeTicks"], 5)

    def test_resource_memory_does_not_update_from_bank_or_service_targets(self):
        memory = resource_return_analyzer.ResourceAreaMemoryState()
        service_target = bank_target()

        updated = resource_return_analyzer.update_resource_area_memory(
            "woodcutting_bank",
            memory,
            inventory_context=inventory_context(free_slots=23),
            target_context=TargetContext(raw_best_target=service_target, candidates=[service_target], source_tick=101),
            bank_ui_context=BankUiContext(bank_open=False, source_tick=101),
            current_task_state={"phase": "inventory_full", "activeIntent": "needs_service", "activeIntentTarget": service_target},
            player_context=player_context(x=3207, y=3219),
            source_tick=101,
        )

        payload = updated.to_dict(source_tick=101)
        self.assertFalse(payload["resourceMemoryValid"])
        self.assertEqual(payload["resourceMemoryInvalidReason"], "no_resource_memory")

    def test_resource_memory_does_not_update_while_bank_is_open(self):
        memory = resource_return_analyzer.ResourceAreaMemoryState()
        target = tree_target()

        updated = resource_return_analyzer.update_resource_area_memory(
            "woodcutting_bank",
            memory,
            inventory_context=inventory_context(free_slots=23),
            target_context=TargetContext(raw_best_target=target, candidates=[target], source_tick=102),
            bank_ui_context=BankUiContext(bank_open=True, source_tick=102),
            current_task_state={"phase": "waiting_for_world_view", "activeIntent": "close_service_context"},
            player_context=player_context(),
            source_tick=102,
        )

        self.assertFalse(updated.to_dict(source_tick=102)["resourceMemoryValid"])

    def test_banking_complete_bank_closed_no_target_uses_valid_memory(self):
        memory = resource_return_analyzer.ResourceAreaMemoryState()
        resource_return_analyzer.update_resource_area_memory(
            "woodcutting_bank",
            memory,
            inventory_context=inventory_context(free_slots=23),
            target_context=TargetContext(raw_best_target=tree_target(), candidates=[tree_target()], profile_candidates=[tree_target()], source_tick=50),
            bank_ui_context=BankUiContext(bank_open=False, source_tick=50),
            current_task_state={"phase": "target_selected", "activeIntent": "continue_current_target", "activeIntentTarget": tree_target()},
            player_context=player_context(),
            source_tick=50,
        )

        context = resource_return_analyzer.analyze_resource_return_context(
            "woodcutting_bank",
            bank_operation_context=BankOperationContext(banking_complete=True, inventory_free_slots=15, source_tick=75),
            bank_ui_context=BankUiContext(bank_open=False, source_tick=75),
            target_context=TargetContext(raw_best_target=None, candidates=[], candidate_count=0, source_tick=75),
            resource_memory_state=memory,
            player_context=player_context(x=3208, y=3219),
            source_tick=75,
        )

        payload = context.to_dict()
        self.assertTrue(payload["returnDestinationNeeded"])
        self.assertTrue(payload["returnDestinationAvailable"])
        self.assertEqual(payload["returnDestinationSource"], "last_resource_target")
        self.assertEqual(payload["returnDestinationTile"], {"worldX": 3156, "worldY": 3237, "plane": 0})
        self.assertEqual(payload["reason"], "using_remembered_resource_area")

    def test_banking_complete_bank_closed_visible_target_does_not_use_memory(self):
        visible = tree_target(name="Willow", x=3160, y=3240)
        memory = resource_return_analyzer.ResourceAreaMemoryState(
            last_resource_activity_tick=20,
            last_resource_target_tile={"worldX": 3156, "worldY": 3237, "plane": 0},
            last_resource_target_name="Oak tree",
            last_resource_plane=0,
            last_resource_profile="woodcutting",
        )

        context = resource_return_analyzer.analyze_resource_return_context(
            "woodcutting_bank",
            bank_operation_context=BankOperationContext(banking_complete=True, inventory_free_slots=15, source_tick=80),
            bank_ui_context=BankUiContext(bank_open=False, source_tick=80),
            target_context=TargetContext(raw_best_target=visible, candidates=[visible], candidate_count=1, source_tick=80),
            resource_memory_state=memory,
            player_context=player_context(),
            source_tick=80,
        )

        payload = context.to_dict()
        self.assertFalse(payload["returnDestinationNeeded"])
        self.assertFalse(payload["returnDestinationAvailable"])
        self.assertTrue(payload["resourceTargetCurrentlyVisible"])
        self.assertEqual(payload["reason"], "resource_target_visible")

    def test_banking_complete_bank_closed_without_target_or_memory_reports_missing_memory(self):
        context = resource_return_analyzer.analyze_resource_return_context(
            "woodcutting_bank",
            bank_operation_context=BankOperationContext(banking_complete=True, inventory_free_slots=15, source_tick=90),
            bank_ui_context=BankUiContext(bank_open=False, source_tick=90),
            target_context=TargetContext(raw_best_target=None, candidates=[], candidate_count=0, source_tick=90),
            resource_memory_state=resource_return_analyzer.ResourceAreaMemoryState(),
            player_context=player_context(),
            source_tick=90,
        )

        payload = context.to_dict()
        self.assertTrue(payload["returnDestinationNeeded"])
        self.assertFalse(payload["returnDestinationAvailable"])
        self.assertFalse(payload["resourceMemoryValid"])
        self.assertEqual(payload["reason"], "no_resource_memory")
        self.assertIn("resource.memory", payload["missingCapabilities"])


if __name__ == "__main__":
    unittest.main()

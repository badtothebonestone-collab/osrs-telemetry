import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import task_policy


class TaskPolicyTest(unittest.TestCase):
    def test_loads_named_inventory_full_strategies(self):
        policies = task_policy.load_task_policies()

        bank = policies["woodcutting_bank"]
        self.assertEqual(bank.task, "woodcutting")
        self.assertEqual(bank.inventoryExpectation, task_policy.InventoryExpectation.MUST_HAVE_SPACE)
        self.assertEqual(bank.fullInventoryStrategy, task_policy.InventoryFullStrategy.NEEDS_SERVICE)
        self.assertEqual(bank.resourceDisposition, task_policy.ResourceDisposition.BANK)
        self.assertEqual(bank.serviceTypeNeeded, "bank_full")

        deposit = policies["woodcutting_deposit"]
        self.assertEqual(deposit.fullInventoryStrategy, task_policy.InventoryFullStrategy.NEEDS_SERVICE)
        self.assertEqual(deposit.resourceDisposition, task_policy.ResourceDisposition.BANK)
        self.assertEqual(deposit.serviceTypeNeeded, "bank_deposit")

        firemake = policies["woodcutting_firemake"]
        self.assertEqual(firemake.fullInventoryStrategy, task_policy.InventoryFullStrategy.PROCESS_INVENTORY)
        self.assertEqual(firemake.resourceDisposition, task_policy.ResourceDisposition.BURN)
        self.assertEqual(firemake.processTypeNeeded, "firemaking")

        drop = policies["woodcutting_drop"]
        self.assertEqual(drop.fullInventoryStrategy, task_policy.InventoryFullStrategy.PROCESS_INVENTORY)
        self.assertEqual(drop.resourceDisposition, task_policy.ResourceDisposition.DROP)
        self.assertEqual(drop.processTypeNeeded, "drop")

        combat = policies["combat_default"]
        self.assertEqual(combat.inventoryExpectation, task_policy.InventoryExpectation.MAY_START_FULL)
        self.assertEqual(combat.fullInventoryStrategy, task_policy.InventoryFullStrategy.CONTINUE_TASK)
        self.assertEqual(combat.resourceDisposition, task_policy.ResourceDisposition.KEEP)

        observe = policies["observe_only"]
        self.assertEqual(observe.fullInventoryStrategy, task_policy.InventoryFullStrategy.OBSERVE_ONLY)

    def test_resolves_default_woodcutting_policy_explicitly(self):
        policy = task_policy.resolve_task_policy(None, task="woodcutting", profile="woodcutting")

        self.assertEqual(policy.name, "woodcutting_bank")
        self.assertEqual(policy.fullInventoryStrategy, task_policy.InventoryFullStrategy.NEEDS_SERVICE)
        self.assertEqual(policy.serviceTypeNeeded, "bank_full")

    def test_policy_payload_reports_inventory_strategy(self):
        payload = task_policy.resolve_task_policy("woodcutting_drop").to_dict()

        self.assertEqual(payload["fullInventoryStrategy"], "process_inventory")
        self.assertEqual(payload["resourceDisposition"], "drop")


if __name__ == "__main__":
    unittest.main()

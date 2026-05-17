import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import task_state
import task_policy


class TaskStateTest(unittest.TestCase):
    def test_task_state_result_schema_and_capability_normalization(self):
        state = task_state.TaskState(
            task="woodcutting",
            phase=task_state.TaskPhase.TARGET_SELECTED,
            confidence=0.84,
            reason="reachable target selected",
            activeIntent=task_state.TaskIntent.TARGET_SELECTED,
            selectedTargetKey="tree-1",
            requiredCapabilities=["inventoryDeltas", "target.best"],
            missingCapabilities=["inventoryDeltas", "animationFrame"],
            blockingConditions=[],
            observationNeeds=[{"capability": "inventoryDeltas", "status": "optional"}],
            goalProgress={"displayedGoalProgress": 1, "goalCount": 5},
        )

        payload = task_state.TaskStateResult(state=state).to_dict()

        self.assertEqual(payload["schema"], "generic_task_state.v1")
        self.assertEqual(payload["phase"], "target_selected")
        self.assertEqual(payload["activeIntent"], "target_selected")
        self.assertEqual(payload["selectedTargetKey"], "tree-1")
        self.assertEqual(payload["requiredCapabilities"], ["inventory.deltas", "target.best"])
        self.assertEqual(payload["missingCapabilities"], ["inventory.deltas", "activity.animation_frame"])
        self.assertEqual(payload["observationNeeds"][0]["capability"], "inventory.deltas")
        self.assertTrue(payload["noActionEmitted"])

    def test_maps_woodcutting_target_available_to_target_selected(self):
        decision = {
            "task": "woodcutting",
            "phase": "target_available",
            "confidence": 0.84,
            "goalProgress": {"displayedGoalProgress": 1, "goalCount": 5},
            "currentContextSummary": {"bestTarget": {"targetKey": "tree-1", "id": 1278}},
            "missingCapabilities": ["fullPathfinding"],
            "observationNeeds": [{"capability": "animationFrame", "status": "optional"}],
            "blockingConditions": [],
            "noActionEmitted": True,
        }

        payload = task_state.from_brain_decision(decision).to_dict()

        self.assertEqual(payload["phase"], "target_selected")
        self.assertEqual(payload["activeIntent"], "continue_current_target")
        self.assertEqual(payload["selectedTargetKey"], "tree-1")
        self.assertEqual(payload["activeIntentTarget"]["targetKey"], "tree-1")
        self.assertEqual(payload["missingCapabilities"], ["navigation.full_pathfinding"])
        self.assertEqual(payload["observationNeeds"][0]["capability"], "activity.animation_frame")

    def test_maps_inventory_full_goal_complete_and_no_candidates(self):
        best = {"name": "Oak tree", "id": 10820, "directReachability": "reachable"}
        inventory_full = task_state.from_brain_decision(
            {
                "task": "woodcutting",
                "phase": "inventory_full",
                "blockingConditions": ["inventory is full"],
                "currentContextSummary": {"bestTarget": best},
            },
            policy=task_policy.resolve_task_policy("woodcutting_bank"),
        ).to_dict()
        self.assertEqual(inventory_full["phase"], "inventory_full")
        self.assertEqual(inventory_full["activeIntent"], "needs_service")
        self.assertEqual(inventory_full["serviceTypeNeeded"], "bank_full")
        self.assertIsNone(inventory_full["selectedTargetKey"])
        self.assertIsNone(inventory_full["activeIntentTarget"])
        self.assertEqual(inventory_full["availableTarget"]["id"], 10820)
        self.assertEqual(inventory_full["previousIntentTarget"]["id"], 10820)
        self.assertIn("inventory is full", inventory_full["blockingConditions"])

        goal_complete = task_state.from_brain_decision(
            {"task": "woodcutting", "phase": "goal_complete", "currentContextSummary": {"bestTarget": best}}
        ).to_dict()
        self.assertEqual(goal_complete["phase"], "goal_complete")
        self.assertEqual(goal_complete["activeIntent"], "none")
        self.assertIsNone(goal_complete["selectedTargetKey"])
        self.assertIsNone(goal_complete["activeIntentTarget"])
        self.assertEqual(task_state.from_brain_decision({"task": "woodcutting", "phase": "no_target_observed"}).state.phase, task_state.TaskPhase.NEEDS_MORE_CONTEXT)
        self.assertEqual(task_state.from_brain_decision({"task": "woodcutting", "phase": "blocked_or_unreachable"}).state.phase, task_state.TaskPhase.BLOCKED)

    def test_inventory_full_policy_can_process_inventory_instead_of_service(self):
        best = {"targetKey": "tree-1", "name": "Tree", "id": 1278, "directReachability": "reachable"}

        payload = task_state.from_brain_decision(
            {
                "task": "woodcutting",
                "phase": "inventory_full",
                "blockingConditions": ["inventory is full"],
                "currentContextSummary": {"bestTarget": best},
            },
            policy=task_policy.resolve_task_policy("woodcutting_firemake"),
        ).to_dict()

        self.assertEqual(payload["phase"], "inventory_full")
        self.assertEqual(payload["activeIntent"], "process_inventory")
        self.assertEqual(payload["processTypeNeeded"], "firemaking")
        self.assertEqual(payload["resourceDisposition"], "burn")
        self.assertIsNone(payload["selectedTargetKey"])
        self.assertIsNone(payload["activeIntentTarget"])
        self.assertEqual(payload["previousIntentTarget"]["targetKey"], "tree-1")

        drop_payload = task_state.from_brain_decision(
            {
                "task": "woodcutting",
                "phase": "inventory_full",
                "blockingConditions": ["inventory is full"],
                "currentContextSummary": {"bestTarget": best},
            },
            policy=task_policy.resolve_task_policy("woodcutting_drop"),
        ).to_dict()
        self.assertEqual(drop_payload["activeIntent"], "process_inventory")
        self.assertEqual(drop_payload["processTypeNeeded"], "drop")
        self.assertEqual(drop_payload["resourceDisposition"], "drop")

    def test_inventory_full_policy_can_continue_task_or_observe(self):
        best = {"targetKey": "target-1", "name": "Goblin", "id": 101, "directReachability": "reachable"}

        combat = task_state.from_brain_decision(
            {
                "task": "combat",
                "phase": "inventory_full",
                "blockingConditions": ["inventory is full"],
                "currentContextSummary": {"bestTarget": best},
            },
            policy=task_policy.resolve_task_policy("combat_default"),
        ).to_dict()
        self.assertEqual(combat["activeIntent"], "continue_task")
        self.assertEqual(combat["phase"], "target_selected")
        self.assertEqual(combat["selectedTargetKey"], "target-1")
        self.assertEqual(combat["activeIntentTarget"]["targetKey"], "target-1")
        self.assertNotIn("inventory is full", combat["blockingConditions"])

        observe = task_state.from_brain_decision(
            {
                "task": "observe",
                "phase": "inventory_full",
                "blockingConditions": ["inventory is full"],
                "currentContextSummary": {"bestTarget": best},
            },
            policy=task_policy.resolve_task_policy("observe_only"),
        ).to_dict()
        self.assertEqual(observe["phase"], "observe")
        self.assertEqual(observe["activeIntent"], "observe")
        self.assertIsNone(observe["activeIntentTarget"])
        self.assertNotIn("inventory is full", observe["blockingConditions"])


if __name__ == "__main__":
    unittest.main()

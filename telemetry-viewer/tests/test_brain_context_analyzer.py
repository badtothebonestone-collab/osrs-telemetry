import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import brain_core
from analyzers import brain_context_analyzer


class BrainContextAnalyzerTest(unittest.TestCase):
    def test_brain_context_emits_generic_target_state(self):
        response = {
            "schema": "context_response.v1",
            "status": "PASS",
            "latestTick": 1,
            "baseline": {"player": {"worldX": 3220, "worldY": 3241, "plane": 0, "sceneX": 48, "sceneY": 47}},
            "bestCandidates": {"tree": {"targetName": "Tree", "id": 1278, "targetLiveState": "live_assumed", "distanceTiles": 1, "onScreen": True, "geometryAvailable": True, "aimPoint": {"canvasX": 10, "canvasY": 20}, "navigation": {"directReachability": "reachable"}}},
            "nearestCandidates": {"tree": {"targetName": "Tree", "id": 1278, "targetLiveState": "live_assumed", "distanceTiles": 1, "onScreen": True, "geometryAvailable": True, "aimPoint": {"canvasX": 10, "canvasY": 20}, "navigation": {"directReachability": "reachable"}}},
            "reachabilitySummary": {"tree": {"candidateCount": 1, "reachableCount": 1, "blockedCount": 0, "unknownCount": 0}},
            "inventory": {"items": [{"slot": 0, "itemId": 1511, "quantity": 1}], "inventorySignature": "sig-a", "inventorySlotCount": 28},
            "activity": {"apparentState": "idle"},
            "warnings": [],
            "missingCapabilities": [],
        }
        state = brain_core.default_state("woodcutting", 5)

        context = brain_context_analyzer.evaluate_brain_context(response, state, task="woodcutting", goal_count=5, max_events=3)

        self.assertTrue(context.decision["noActionEmitted"])
        self.assertEqual(context.decision["genericTaskState"]["phase"], "target_selected")
        self.assertEqual(context.decision["genericTaskState"]["missingCapabilities"], [])
        self.assertIn("brainBaselineEstablished", context.status_fields)

    def test_brain_context_passes_task_policy_to_generic_state(self):
        response = {
            "schema": "context_response.v1",
            "status": "PASS",
            "latestTick": 1,
            "baseline": {"player": {"worldX": 3220, "worldY": 3241, "plane": 0, "sceneX": 48, "sceneY": 47}},
            "bestCandidates": {"tree": {"targetName": "Tree", "id": 1278, "targetLiveState": "live_assumed", "distanceTiles": 1, "onScreen": True, "geometryAvailable": True, "aimPoint": {"canvasX": 10, "canvasY": 20}, "navigation": {"directReachability": "reachable"}}},
            "nearestCandidates": {"tree": {"targetName": "Tree", "id": 1278, "targetLiveState": "live_assumed", "distanceTiles": 1, "onScreen": True, "geometryAvailable": True, "aimPoint": {"canvasX": 10, "canvasY": 20}, "navigation": {"directReachability": "reachable"}}},
            "reachabilitySummary": {"tree": {"candidateCount": 1, "reachableCount": 1, "blockedCount": 0, "unknownCount": 0}},
            "inventory": {"known": True, "freeSlots": 0, "filledSlots": 28, "inventoryFull": True},
            "activity": {"apparentState": "idle"},
            "warnings": [],
            "missingCapabilities": [],
        }
        state = brain_core.default_state("woodcutting", 5)

        context = brain_context_analyzer.evaluate_brain_context(
            response,
            state,
            task="woodcutting",
            goal_count=5,
            max_events=3,
            task_policy="woodcutting_drop",
        )

        self.assertEqual(context.decision["genericTaskState"]["activeIntent"], "process_inventory")
        self.assertEqual(context.decision["genericTaskState"]["processTypeNeeded"], "drop")
        self.assertEqual(context.decision["genericTaskState"]["resourceDisposition"], "drop")

    def test_process_inventory_policy_ignores_stale_target_domain_when_inventory_is_fresh(self):
        response = {
            "schema": "context_response.v1",
            "status": "PASS",
            "latestTick": 1,
            "freshness": {"freshByTicks": False, "freshByMillis": True},
            "baseline": {"player": {"worldX": 3220, "worldY": 3241, "plane": 0, "sceneX": 48, "sceneY": 47}},
            "reachabilitySummary": {"tree": {"candidateCount": 0, "reachableCount": 0, "blockedCount": 0, "unknownCount": 0}},
            "inventory": {
                "known": True,
                "freeSlots": 0,
                "filledSlots": 28,
                "inventoryFull": True,
                "inventorySignature": "full-logs",
                "items": [{"slot": slot, "itemId": 1511, "quantity": 1} for slot in range(28)],
            },
            "activity": {"apparentState": "idle"},
            "warnings": [],
            "missingCapabilities": [],
        }
        state = brain_core.default_state("woodcutting", 5)

        context = brain_context_analyzer.evaluate_brain_context(
            response,
            state,
            task="woodcutting",
            goal_count=5,
            max_events=3,
            task_policy="woodcutting_drop",
        )

        self.assertIn(context.status, {"PASS", "WARN"})
        self.assertEqual(context.decision["phase"], "inventory_full")
        self.assertEqual(context.decision["genericTaskState"]["activeIntent"], "process_inventory")
        self.assertEqual(context.decision["freshnessDomains"]["processInventoryFreshness"], "fresh")
        self.assertNotIn("context freshness failed", context.decision["blockingConditions"])


if __name__ == "__main__":
    unittest.main()

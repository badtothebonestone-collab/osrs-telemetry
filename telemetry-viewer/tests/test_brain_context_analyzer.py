import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import brain_core
from analyzers import brain_context_analyzer


class BrainContextAnalyzerTest(unittest.TestCase):
    def test_brain_context_has_no_action_fields(self):
        response = {
            "schema": "context_response.v1",
            "status": "PASS",
            "latestTick": 1,
            "bestCandidates": {"tree": {"targetName": "Tree", "id": 1278, "targetLiveState": "live_assumed", "navigation": {"directReachability": "reachable"}}},
            "nearestCandidates": {"tree": {"targetName": "Tree", "id": 1278}},
            "inventory": {"items": [{"slot": 0, "itemId": 1511, "quantity": 1}], "inventorySignature": "sig-a", "inventorySlotCount": 28},
            "activity": {"apparentState": "idle"},
            "warnings": [],
            "missingCapabilities": [],
        }
        state = brain_core.default_state("woodcutting", 5)

        context = brain_context_analyzer.evaluate_brain_context(response, state, task="woodcutting", goal_count=5, max_events=3)

        keys = " ".join(str(key).lower() for key in context.decision.keys())
        for forbidden in ("click", "mouse", "keyboard", "menu", "invoke", "execute"):
            self.assertNotIn(forbidden, keys)
        self.assertTrue(context.decision["noActionEmitted"])
        self.assertIn("brainBaselineEstablished", context.status_fields)


if __name__ == "__main__":
    unittest.main()


import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from analyzers import post_bank_reacquisition_analyzer
from analyzers.live_state import BankOperationContext, BankUiContext, TargetContext


def tree_target() -> dict:
    return {
        "objectKey": "oak-1",
        "targetName": "Oak tree",
        "targetType": "sceneObject",
        "classId": "tree",
        "worldX": 3201,
        "worldY": 3201,
        "plane": 0,
        "navigation": {"directReachability": "reachable"},
    }


class PostBankReacquisitionAnalyzerTest(unittest.TestCase):
    def test_banking_complete_with_bank_open_defers_resource_reacquisition(self):
        context = post_bank_reacquisition_analyzer.analyze_post_bank_reacquisition_context(
            "woodcutting_bank",
            bank_operation_context=BankOperationContext(banking_complete=True, source_tick=42),
            bank_ui_context=BankUiContext(bank_open=True, bank_readable=True, source_tick=42),
            target_context=TargetContext(raw_best_target=tree_target(), candidate_count=1, source_tick=42),
            source_tick=42,
        )

        payload = context.to_dict()
        self.assertTrue(payload["postBankReacquisitionNeeded"])
        self.assertTrue(payload["bankUiStillOpen"])
        self.assertFalse(payload["worldViewReady"])
        self.assertFalse(payload["resourceTargetReacquisitionAllowed"])
        self.assertFalse(payload["resourceTargetAvailable"])
        self.assertEqual(payload["reason"], "bank_ui_still_open")
        self.assertNotIn("target.candidates", payload["missingCapabilities"])

    def test_banking_complete_with_bank_closed_and_tree_visible_allows_targeting(self):
        context = post_bank_reacquisition_analyzer.analyze_post_bank_reacquisition_context(
            "woodcutting_bank",
            bank_operation_context=BankOperationContext(banking_complete=True, source_tick=43),
            bank_ui_context=BankUiContext(bank_open=False, bank_readable=False, source_tick=43),
            target_context=TargetContext(raw_best_target=tree_target(), candidate_count=1, source_tick=43),
            source_tick=43,
        )

        payload = context.to_dict()
        self.assertTrue(payload["postBankReacquisitionNeeded"])
        self.assertFalse(payload["bankUiStillOpen"])
        self.assertTrue(payload["worldViewReady"])
        self.assertTrue(payload["resourceTargetReacquisitionAllowed"])
        self.assertTrue(payload["resourceTargetAvailable"])
        self.assertEqual(payload["reason"], "resource_target_visible")

    def test_banking_complete_with_bank_closed_and_no_target_reports_no_resource_target(self):
        context = post_bank_reacquisition_analyzer.analyze_post_bank_reacquisition_context(
            "woodcutting_bank",
            bank_operation_context=BankOperationContext(banking_complete=True, source_tick=44),
            bank_ui_context=BankUiContext(bank_open=False, bank_readable=False, source_tick=44),
            target_context=TargetContext(raw_best_target=None, candidate_count=0, source_tick=44),
            source_tick=44,
        )

        payload = context.to_dict()
        self.assertTrue(payload["postBankReacquisitionNeeded"])
        self.assertTrue(payload["worldViewReady"])
        self.assertTrue(payload["resourceTargetReacquisitionAllowed"])
        self.assertFalse(payload["resourceTargetAvailable"])
        self.assertEqual(payload["reason"], "no_resource_target_observed")
        self.assertIn("target.candidates", payload["missingCapabilities"])


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from analyzers import close_bank_analyzer
from analyzers.live_state import BankOperationContext, BankUiContext, PostBankReacquisitionContext


class CloseBankAnalyzerTest(unittest.TestCase):
    def test_banking_complete_with_bank_open_and_close_button_is_ready(self):
        context = close_bank_analyzer.analyze_close_bank_context(
            "woodcutting_bank",
            bank_ui_context=BankUiContext(
                bank_open=True,
                bank_readable=True,
                close_button_available=True,
                bank_close_button_visible=True,
                close_button_widget={"groupId": 12, "childId": 3},
                close_button_bounds={"x": 480, "y": 20, "width": 16, "height": 16},
                source_tick=42,
            ),
            bank_operation_context=BankOperationContext(banking_complete=True, source_tick=42),
            post_bank_reacquisition_context=PostBankReacquisitionContext(
                post_bank_reacquisition_needed=True,
                bank_ui_still_open=True,
                reason="bank_ui_still_open",
                source_tick=42,
            ),
            source_tick=42,
        )

        payload = context.to_dict()
        self.assertEqual(payload["status"], "PASS")
        self.assertTrue(payload["closeBankNeeded"])
        self.assertTrue(payload["closeBankReady"])
        self.assertTrue(payload["bankOpen"])
        self.assertTrue(payload["bankingComplete"])
        self.assertTrue(payload["closeButtonVisible"])
        self.assertTrue(payload["closeButtonAvailable"])
        self.assertEqual(payload["closeButtonWidget"]["groupId"], 12)
        self.assertEqual(payload["closeButtonBounds"]["width"], 16)
        self.assertEqual(payload["reason"], "close_button_available")

    def test_banking_complete_with_bank_open_and_missing_close_button_warns(self):
        context = close_bank_analyzer.analyze_close_bank_context(
            "woodcutting_bank",
            bank_ui_context=BankUiContext(
                bank_open=True,
                bank_readable=True,
                close_button_available=False,
                bank_close_button_visible=False,
                source_tick=43,
            ),
            bank_operation_context=BankOperationContext(banking_complete=True, source_tick=43),
            post_bank_reacquisition_context=PostBankReacquisitionContext(
                post_bank_reacquisition_needed=True,
                bank_ui_still_open=True,
                reason="bank_ui_still_open",
                source_tick=43,
            ),
            source_tick=43,
        )

        payload = context.to_dict()
        self.assertEqual(payload["status"], "WARN")
        self.assertTrue(payload["closeBankNeeded"])
        self.assertFalse(payload["closeBankReady"])
        self.assertEqual(payload["reason"], "close_button_missing")
        self.assertIn("bank_ui.close_button", payload["missingCapabilities"])
        self.assertTrue(payload["warnings"])

    def test_keyboard_close_possible_makes_bank_close_ready_without_button_widget(self):
        context = close_bank_analyzer.analyze_close_bank_context(
            "woodcutting_bank",
            bank_ui_context=BankUiContext(
                bank_open=True,
                bank_readable=True,
                close_button_available=None,
                bank_close_button_visible=None,
                keyboard_close_possible=True,
                source_tick=44,
            ),
            bank_operation_context=BankOperationContext(banking_complete=True, source_tick=44),
            post_bank_reacquisition_context=PostBankReacquisitionContext(
                post_bank_reacquisition_needed=True,
                bank_ui_still_open=True,
                reason="bank_ui_still_open",
                source_tick=44,
            ),
            source_tick=44,
        )

        payload = context.to_dict()
        self.assertEqual(payload["status"], "PASS")
        self.assertTrue(payload["closeBankNeeded"])
        self.assertTrue(payload["closeBankReady"])
        self.assertTrue(payload["keyboardClosePossible"])
        self.assertEqual(payload["reason"], "bank_ui_still_open")
        self.assertEqual(payload["missingCapabilities"], [])

    def test_bank_closed_means_close_not_needed(self):
        context = close_bank_analyzer.analyze_close_bank_context(
            "woodcutting_bank",
            bank_ui_context=BankUiContext(bank_open=False, bank_readable=False, source_tick=45),
            bank_operation_context=BankOperationContext(banking_complete=True, source_tick=45),
            post_bank_reacquisition_context=PostBankReacquisitionContext(
                post_bank_reacquisition_needed=True,
                bank_ui_still_open=False,
                world_view_ready=True,
                source_tick=45,
            ),
            source_tick=45,
        )

        payload = context.to_dict()
        self.assertEqual(payload["status"], "PASS")
        self.assertFalse(payload["closeBankNeeded"])
        self.assertFalse(payload["closeBankReady"])
        self.assertFalse(payload["bankOpen"])
        self.assertTrue(payload["bankingComplete"])
        self.assertEqual(payload["reason"], "close_not_needed")


if __name__ == "__main__":
    unittest.main()

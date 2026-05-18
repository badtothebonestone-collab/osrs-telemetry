import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_close_bank_context.py"
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_close_bank_context as diagnostic


class CloseBankDiagnosticTest(unittest.TestCase):
    def test_build_from_daemon_summarizes_close_bank_context(self):
        payload = diagnostic.build_from_daemon(
            {
                "brain": {
                    "genericTaskState": {"phase": "waiting_for_world_view", "activeIntent": "close_service_context"},
                    "bankOperationContext": {"bankingComplete": True},
                    "bankUiContext": {"bankOpen": True, "closeButtonVisible": True, "closeButtonAvailable": True},
                    "closeBankContext": {
                        "closeBankNeeded": True,
                        "closeBankReady": True,
                        "bankOpen": True,
                        "bankingComplete": True,
                        "closeButtonVisible": True,
                        "closeButtonAvailable": True,
                        "keyboardClosePossible": None,
                        "reason": "close_button_available",
                        "warnings": [],
                        "missingCapabilities": [],
                    },
                }
            }
        )

        self.assertTrue(payload["bankOpen"])
        self.assertTrue(payload["bankingComplete"])
        self.assertTrue(payload["closeBankNeeded"])
        self.assertTrue(payload["closeBankReady"])
        self.assertTrue(payload["closeButtonVisible"])
        self.assertTrue(payload["closeButtonAvailable"])
        self.assertEqual(payload["keyboardClosePossible"], None)
        self.assertEqual(payload["reason"], "close_button_available")
        self.assertEqual(payload["nextPhase"], "waiting_for_world_view")
        self.assertEqual(payload["activeIntent"], "close_service_context")

    def test_human_output_names_expected_fields(self):
        text = diagnostic.format_human(
            {
                "source": "daemon-memory",
                "daemonReachable": True,
                "closeBankContextPresent": True,
                "bankOpen": True,
                "bankingComplete": True,
                "closeBankNeeded": True,
                "closeBankReady": True,
                "closeButtonVisible": True,
                "closeButtonAvailable": True,
                "keyboardClosePossible": None,
                "reason": "close_button_available",
                "nextPhase": "waiting_for_world_view",
                "activeIntent": "close_service_context",
                "warnings": [],
                "missingCapabilities": [],
            }
        )

        self.assertIn("Bank open: yes", text)
        self.assertIn("Banking complete: yes", text)
        self.assertIn("Close bank needed: yes", text)
        self.assertIn("Close bank ready: yes", text)
        self.assertIn("Close button visible: yes", text)
        self.assertIn("Close button available: yes", text)
        self.assertIn("Keyboard close possible: unknown", text)
        self.assertIn("Reason: close_button_available", text)
        self.assertIn("Active intent: close_service_context", text)

    def test_json_cli_stdout_only_when_daemon_not_reachable(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--from-daemon",
                    "--daemon-url",
                    "http://127.0.0.1:1",
                    "--timeout",
                    "0.01",
                    "--json",
                ],
                cwd=temp,
                capture_output=True,
                text=True,
                check=False,
            )
            after = set(os.listdir(temp))

        self.assertNotEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["daemonReachable"])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

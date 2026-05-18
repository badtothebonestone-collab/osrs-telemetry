import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_post_bank_reacquisition_context.py"
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_post_bank_reacquisition_context as diagnostic


class PostBankReacquisitionDiagnosticTest(unittest.TestCase):
    def test_build_from_daemon_summarizes_post_bank_context(self):
        payload = diagnostic.build_from_daemon(
            {
                "brain": {
                    "genericTaskState": {"phase": "waiting_for_world_view", "activeIntent": "wait_for_world_view"},
                    "bankOperationContext": {"bankingComplete": True},
                    "bankUiContext": {"bankOpen": True},
                    "postBankReacquisitionContext": {
                        "postBankReacquisitionNeeded": True,
                        "bankUiStillOpen": True,
                        "worldViewReady": False,
                        "resourceTargetReacquisitionAllowed": False,
                        "resourceTargetAvailable": False,
                        "reason": "bank_ui_still_open",
                        "warnings": [],
                        "missingCapabilities": [],
                    },
                }
            }
        )

        self.assertTrue(payload["bankingComplete"])
        self.assertTrue(payload["bankOpen"])
        self.assertFalse(payload["worldViewReady"])
        self.assertFalse(payload["resourceTargetReacquisitionAllowed"])
        self.assertFalse(payload["resourceTargetAvailable"])
        self.assertEqual(payload["reason"], "bank_ui_still_open")
        self.assertEqual(payload["nextPhase"], "waiting_for_world_view")
        self.assertEqual(payload["activeIntent"], "wait_for_world_view")

    def test_human_output_names_expected_fields(self):
        text = diagnostic.format_human(
            {
                "source": "daemon-memory",
                "daemonReachable": True,
                "postBankReacquisitionContextPresent": True,
                "bankingComplete": True,
                "bankOpen": True,
                "worldViewReady": False,
                "resourceTargetReacquisitionAllowed": False,
                "resourceTargetAvailable": False,
                "reason": "bank_ui_still_open",
                "nextPhase": "waiting_for_world_view",
                "activeIntent": "wait_for_world_view",
                "warnings": [],
                "missingCapabilities": [],
            }
        )

        self.assertIn("Banking complete: yes", text)
        self.assertIn("Bank open: yes", text)
        self.assertIn("World view ready: no", text)
        self.assertIn("Resource target reacquisition allowed: no", text)
        self.assertIn("Resource target available: no", text)
        self.assertIn("Reason: bank_ui_still_open", text)
        self.assertIn("Active intent: wait_for_world_view", text)

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

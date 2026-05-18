import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_bank_ui_context.py"
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_bank_ui_context as diagnostic


class BankUiDiagnosticTest(unittest.TestCase):
    def test_build_from_daemon_summarizes_bank_ui_context(self):
        payload = diagnostic.build_from_daemon(
            {
                "brain": {
                    "bankUiContext": {
                        "bankOpen": True,
                        "bankReadable": True,
                        "bankPinOpen": False,
                        "topLevelInterfaceId": 12,
                        "bankRootVisible": True,
                        "bankContainerVisible": True,
                        "bankInventoryVisible": True,
                        "depositInventoryButtonVisible": True,
                        "inventorySummary": {"freeSlots": 0, "occupiedSlots": 28},
                        "bankSummary": {"occupiedSlots": 14, "uniqueItemCount": 3},
                    }
                },
                "bankOpen": True,
            }
        )

        self.assertTrue(payload["bankOpen"])
        self.assertTrue(payload["bankReadable"])
        self.assertFalse(payload["bankPinOpen"])
        self.assertEqual(payload["topLevelInterfaceId"], 12)
        self.assertEqual(payload["inventoryOccupiedSlots"], 28)
        self.assertEqual(payload["bankOccupiedSlots"], 14)
        self.assertEqual(payload["bankUniqueItemCount"], 3)

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

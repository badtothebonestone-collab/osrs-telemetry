import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_bank_operation_context.py"
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_bank_operation_context as diagnostic


class BankOperationDiagnosticTest(unittest.TestCase):
    def test_build_from_daemon_summarizes_bank_operation_context(self):
        payload = diagnostic.build_from_daemon(
            {
                "brain": {
                    "genericTaskState": {"phase": "service_open", "activeIntent": "bank_operation_pending"},
                    "bankUiContext": {"bankOpen": True, "bankReadable": True, "bankPinOpen": False},
                    "bankOperationContext": {
                        "operationNeeded": True,
                        "operationType": "deposit_inventory",
                        "resourceItemsHeld": 28,
                        "resourceItemSlots": list(range(28)),
                        "resourceItemQuantity": 28,
                        "nonResourceItemsHeld": 0,
                        "inventoryFreeSlots": 0,
                        "depositInventoryAvailable": True,
                        "bankingComplete": False,
                        "completionReason": "resources_still_held",
                        "warnings": [],
                        "missingCapabilities": [],
                    },
                }
            }
        )

        self.assertTrue(payload["bankOpen"])
        self.assertTrue(payload["bankReadable"])
        self.assertFalse(payload["bankPinOpen"])
        self.assertTrue(payload["operationNeeded"])
        self.assertEqual(payload["operationType"], "deposit_inventory")
        self.assertEqual(payload["resourceItemsHeld"], 28)
        self.assertEqual(payload["resourceItemQuantity"], 28)
        self.assertFalse(payload["bankingComplete"])
        self.assertEqual(payload["nextPhase"], "service_open")
        self.assertEqual(payload["activeIntent"], "bank_operation_pending")

    def test_human_output_names_expected_fields(self):
        text = diagnostic.format_human(
            {
                "source": "daemon-memory",
                "daemonReachable": True,
                "bankOpen": True,
                "bankReadable": True,
                "bankPinOpen": False,
                "operationNeeded": True,
                "operationType": "deposit_resources",
                "resourceItemsHeld": 2,
                "resourceItemSlots": [3, 4],
                "resourceItemQuantity": 2,
                "nonResourceItemsHeld": 1,
                "inventoryFreeSlots": 25,
                "depositInventoryAvailable": False,
                "bankingComplete": False,
                "completionReason": "resources_still_held",
                "activeIntent": "bank_operation_pending",
                "nextPhase": "service_open",
                "warnings": [],
                "missingCapabilities": [],
            }
        )

        self.assertIn("Operation needed: yes", text)
        self.assertIn("Operation type: deposit_resources", text)
        self.assertIn("Resource items held: 2", text)
        self.assertIn("Resource item slots: 3, 4", text)
        self.assertIn("Next phase: service_open", text)

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

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_return_to_resource_context.py"
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_return_to_resource_context as diagnostic


class ReturnToResourceDiagnosticTest(unittest.TestCase):
    def test_build_from_daemon_summarizes_return_context(self):
        payload = diagnostic.build_from_daemon(
            {
                "brain": {
                    "genericTaskState": {"phase": "target_selected", "activeIntent": "select_target"},
                    "bankOperationContext": {"bankingComplete": True},
                    "returnToResourceContext": {
                        "returnNeeded": True,
                        "returnReady": True,
                        "resourceTargetAvailable": True,
                        "bestResourceTarget": {"targetName": "Oak tree", "classId": "tree"},
                        "resourcePathingNeeded": False,
                        "inventoryFreeSlots": 15,
                        "warnings": [],
                        "missingCapabilities": [],
                    },
                },
                "inventoryFreeSlots": 15,
            }
        )

        self.assertTrue(payload["bankingComplete"])
        self.assertEqual(payload["inventoryFreeSlots"], 15)
        self.assertTrue(payload["returnNeeded"])
        self.assertTrue(payload["returnReady"])
        self.assertTrue(payload["resourceTargetAvailable"])
        self.assertEqual(payload["bestResourceTarget"], "Oak tree")
        self.assertEqual(payload["nextPhase"], "target_selected")
        self.assertEqual(payload["activeIntent"], "select_target")

    def test_human_output_names_expected_fields(self):
        text = diagnostic.format_human(
            {
                "source": "daemon-memory",
                "daemonReachable": True,
                "returnToResourceContextPresent": True,
                "bankingComplete": True,
                "inventoryFreeSlots": 15,
                "resourceTargetAvailable": True,
                "bestResourceTarget": "Oak tree",
                "resourcePathingNeeded": False,
                "returnNeeded": True,
                "returnReady": True,
                "nextPhase": "target_selected",
                "activeIntent": "select_target",
                "warnings": [],
                "missingCapabilities": [],
            }
        )

        self.assertIn("Banking complete: yes", text)
        self.assertIn("Inventory free slots: 15", text)
        self.assertIn("Resource target available: yes", text)
        self.assertIn("Best resource target: Oak tree", text)
        self.assertIn("Return needed: yes", text)
        self.assertIn("Return ready: yes", text)
        self.assertIn("Active intent: select_target", text)

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

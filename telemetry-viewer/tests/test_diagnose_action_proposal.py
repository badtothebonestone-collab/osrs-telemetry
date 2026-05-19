import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_action_proposal.py"
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_action_proposal as diagnostic


class DiagnoseActionProposalTest(unittest.TestCase):
    def test_build_from_status_returns_schema_and_action(self):
        payload = diagnostic.build_from_status(
            {
                "brain": {
                    "genericTaskState": {
                        "phase": "target_selected",
                        "activeIntent": "select_target",
                        "activeIntentTarget": {"targetName": "Oak tree", "classId": "tree", "aimPoint": {"canvasX": 100, "canvasY": 120}},
                    },
                    "inventoryContext": {"inventoryFull": False, "freeSlots": 15},
                    "bankUiContext": {"bankOpen": False},
                }
            }
        )

        self.assertEqual(payload["schema"], "action_proposal_diagnostic.v1")
        self.assertEqual(payload["proposedAction"], "select_resource_target")
        self.assertEqual(payload["suggestedClickPoint"], {"x": 100, "y": 120})

    def test_human_output_prints_expected_fields(self):
        text = diagnostic.format_human(
            {
                "status": "PASS",
                "proposedAction": "select_resource_target",
                "targetKind": "resource",
                "targetName": "Oak tree",
                "confidence": 0.9,
                "suggestedClickPoint": {"x": 100, "y": 120},
                "reason": "resource_target_visible",
                "warnings": [],
                "missingCapabilities": [],
            }
        )

        self.assertIn("ACTION PROPOSAL - PASS", text)
        self.assertIn("Proposed action: select_resource_target", text)
        self.assertIn("Click point: 100,120", text)

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
        self.assertEqual(payload["schema"], "action_proposal_diagnostic.v1")
        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_resource_return_context.py"
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_resource_return_context as diagnostic


class ResourceReturnDiagnosticTest(unittest.TestCase):
    def test_build_from_daemon_reports_remembered_return_destination(self):
        payload = diagnostic.build_from_daemon(
            {
                "brain": {
                    "genericTaskState": {"phase": "return_to_resource", "activeIntent": "return_to_resource_area"},
                    "bankOperationContext": {"bankingComplete": True},
                    "bankUiContext": {"bankOpen": False},
                    "resourceReturnContext": {
                        "returnDestinationNeeded": True,
                        "returnDestinationAvailable": True,
                        "returnDestinationTile": {"worldX": 3156, "worldY": 3237, "plane": 0},
                        "returnDestinationSource": "last_resource_target",
                        "resourceMemoryValid": True,
                        "resourceMemoryAgeTicks": 25,
                        "resourceTargetCurrentlyVisible": False,
                        "reason": "using_remembered_resource_area",
                    },
                    "noActionEmitted": True,
                }
            }
        )

        self.assertEqual(payload["schema"], "resource_return_context_diagnostic.v1")
        self.assertTrue(payload["bankingComplete"])
        self.assertFalse(payload["bankOpen"])
        self.assertTrue(payload["returnDestinationNeeded"])
        self.assertTrue(payload["returnDestinationAvailable"])
        self.assertEqual(payload["returnDestinationSource"], "last_resource_target")
        self.assertEqual(payload["reason"], "using_remembered_resource_area")
        self.assertEqual(payload["nextPhase"], "return_to_resource")
        self.assertEqual(payload["activeIntent"], "return_to_resource_area")

    def test_human_output_prints_expected_fields(self):
        text = diagnostic.format_human(
            {
                "schema": "resource_return_context_diagnostic.v1",
                "status": "PASS",
                "bankingComplete": True,
                "bankOpen": False,
                "resourceTargetCurrentlyVisible": False,
                "resourceMemoryValid": True,
                "resourceMemoryAgeTicks": 25,
                "returnDestinationNeeded": True,
                "returnDestinationAvailable": True,
                "returnDestinationTile": {"worldX": 3156, "worldY": 3237, "plane": 0},
                "returnDestinationSource": "last_resource_target",
                "reason": "using_remembered_resource_area",
                "nextPhase": "return_to_resource",
                "activeIntent": "return_to_resource_area",
                "warnings": [],
                "missingCapabilities": [],
            }
        )

        self.assertIn("RESOURCE RETURN CONTEXT - PASS", text)
        self.assertIn("Banking complete: yes", text)
        self.assertIn("Return destination available: yes", text)
        self.assertIn("Return destination tile: 3156,3237,0", text)
        self.assertIn("Active intent: return_to_resource_area", text)

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
        self.assertEqual(payload["schema"], "resource_return_context_diagnostic.v1")
        self.assertFalse(payload["daemonReachable"])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

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
                        "activeIntentTarget": {"targetName": "Tree", "classId": "tree", "id": 1278, "aimPoint": {"canvasX": 100, "canvasY": 120}},
                    },
                    "inventoryContext": {"inventoryFull": False, "freeSlots": 15},
                    "bankUiContext": {"bankOpen": False},
                }
                ,
                "inputGeometry": {
                    "inputGeometryAvailable": True,
                    "canvasScreenOrigin": {"x": 1000, "y": 2000},
                    "canvasSize": {"width": 800, "height": 600},
                    "displayScale": {"x": 2.0, "y": 2.0},
                },
            }
        )

        self.assertEqual(payload["schema"], "action_proposal_diagnostic.v1")
        self.assertEqual(payload["proposedAction"], "select_resource_target")
        self.assertEqual(payload["suggestedClickPoint"], {"x": 100, "y": 120})
        self.assertEqual(payload["clickPointSpace"], "canvas")
        self.assertEqual(payload["resolvedScreenClickPoint"], {"x": 1200, "y": 2240})
        self.assertEqual(payload["targetExplanation"]["name"], "Tree")

    def test_human_output_prints_expected_fields(self):
        text = diagnostic.format_human(
            {
                "status": "PASS",
                "proposedAction": "select_resource_target",
                "targetKind": "resource",
                "targetName": "Oak tree",
                "confidence": 0.9,
                "suggestedClickPoint": {"x": 100, "y": 120},
                "clickPointSpace": "canvas",
                "resolvedScreenClickPoint": {"x": 1200, "y": 2240},
                "clickPointResolution": {"method": "dynamic_input_geometry"},
                "inputGeometry": {
                    "inputGeometryAvailable": True,
                    "canvasScreenOrigin": {"x": 1000, "y": 2000},
                    "canvasSize": {"width": 800, "height": 600},
                },
                "reason": "resource_target_visible",
                "warnings": [],
                "missingCapabilities": [],
                "targetExplanation": {
                    "name": "Oak tree",
                    "id": 10820,
                    "classId": "tree",
                    "world": {"worldX": 3200, "worldY": 3201, "plane": 0},
                    "onScreen": True,
                    "geometryAvailable": True,
                    "aimPoint": {"x": 100, "y": 120},
                    "aimPointSource": "clickboxBounds",
                    "freshness": "fresh",
                    "stale": False,
                    "acceptedReasons": ["profileMatch"],
                    "rejectedReasons": [],
                },
            }
        )

        self.assertIn("ACTION PROPOSAL - PASS", text)
        self.assertIn("Proposed action: select_resource_target", text)
        self.assertIn("Canvas click point: 100,120", text)
        self.assertIn("Resolved screen click point: 1200,2240", text)
        self.assertIn("Input geometry available: yes", text)
        self.assertIn("Selected target:", text)
        self.assertIn("accepted reasons: profileMatch", text)

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

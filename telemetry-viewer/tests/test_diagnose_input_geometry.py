import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_input_geometry.py"
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_input_geometry as diagnostic


class DiagnoseInputGeometryTest(unittest.TestCase):
    def test_build_from_status_returns_schema_and_geometry(self):
        payload = diagnostic.build_from_status(
            {
                "inputGeometry": {
                    "geometryAvailable": True,
                    "canvasScreenX": 100,
                    "canvasScreenY": 200,
                    "canvasWidth": 800,
                    "canvasHeight": 600,
                }
            }
        )

        self.assertEqual(payload["schema"], "input_geometry_diagnostic.v1")
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["canvasScreenOrigin"], {"x": 100, "y": 200})

    def test_human_output_prints_expected_fields(self):
        text = diagnostic.format_human(
            {
                "status": "PASS",
                "inputGeometryAvailable": True,
                "canvasScreenOrigin": {"x": 100, "y": 200},
                "canvasSize": {"width": 800, "height": 600},
                "clientWindowBounds": {"x": 90, "y": 190, "width": 900, "height": 700},
                "displayScale": {"x": 2.0, "y": 2.0},
                "reason": "available",
            }
        )

        self.assertIn("INPUT GEOMETRY - PASS", text)
        self.assertIn("available: yes", text)
        self.assertIn("canvas origin: {'x': 100, 'y': 200}", text)

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
        self.assertEqual(payload["schema"], "input_geometry_diagnostic.v1")
        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

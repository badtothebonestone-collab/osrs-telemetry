import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_mouse_movement.py"
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_mouse_movement as diagnostic


class DiagnoseMouseMovementTest(unittest.TestCase):
    def test_build_diagnostic_contains_schema_and_plan(self):
        payload = diagnostic.build_diagnostic(
            start_x=0,
            start_y=0,
            target_x=100,
            target_y=120,
            target_radius=8,
            profile="wind_mouse",
            seed=123,
        )

        self.assertEqual(payload["schema"], "mouse_movement_diagnostic.v1")
        self.assertEqual(payload["profileName"], "wind_mouse")
        self.assertEqual(payload["validationStatus"], "PASS")
        self.assertIn("pointCount", payload)

    def test_human_output_prints_expected_fields(self):
        payload = diagnostic.build_diagnostic(
            start_x=0,
            start_y=0,
            target_x=100,
            target_y=120,
            target_radius=8,
            profile="linear_debug",
            seed=None,
        )
        text = diagnostic.format_human(payload)

        self.assertIn("MOUSE MOVEMENT - PASS", text)
        self.assertIn("Profile: linear_debug", text)
        self.assertIn("Click point:", text)

    def test_json_cli_writes_no_files(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--start-x",
                    "0",
                    "--start-y",
                    "0",
                    "--target-x",
                    "100",
                    "--target-y",
                    "120",
                    "--target-radius",
                    "8",
                    "--profile",
                    "linear_debug",
                    "--json",
                ],
                cwd=temp,
                capture_output=True,
                text=True,
                check=False,
            )
            after = set(os.listdir(temp))

        self.assertEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema"], "mouse_movement_diagnostic.v1")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

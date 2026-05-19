import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_woodcut_bank_scenarios.py"
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_woodcut_bank_scenarios as scenarios


EXPECTED_SCENARIOS = [
    "collecting_resources",
    "inventory_full_needs_service",
    "pathing_to_service",
    "service_ready_bank_closed",
    "bank_open_resources_held",
    "bank_open_after_deposit",
    "bank_closed_return_memory",
    "bank_closed_tree_visible",
    "bank_closed_no_memory_no_target",
    "bank_pin_blocked",
    "retained_booth_blocks_deposit",
    "remembered_return_cross_plane",
]


class WoodcutBankScenarioSuiteTest(unittest.TestCase):
    def test_each_scenario_returns_expected_pass(self):
        report = scenarios.build_suite_report()

        self.assertEqual(report["schema"], "woodcut_bank_scenario_suite.v1")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["passCount"], len(EXPECTED_SCENARIOS))
        self.assertEqual(report["warnCount"], 0)
        self.assertEqual(report["failCount"], 0)
        self.assertEqual([result["name"] for result in report["scenarioResults"]], EXPECTED_SCENARIOS)
        for result in report["scenarioResults"]:
            with self.subTest(result["name"]):
                self.assertEqual(result["status"], "PASS")
                self.assertIn(result["actualStage"], result["expectedStages"])

    def test_list_includes_all_scenario_names(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--list"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0)
        for name in EXPECTED_SCENARIOS:
            self.assertIn(name, completed.stdout)

    def test_scenario_runs_only_requested_scenario(self):
        report = scenarios.build_suite_report(["bank_closed_return_memory"])

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["passCount"], 1)
        self.assertEqual(len(report["scenarioResults"]), 1)
        result = report["scenarioResults"][0]
        self.assertEqual(result["name"], "bank_closed_return_memory")
        self.assertEqual(result["actualStage"], "return_to_resource")
        self.assertEqual(result["actualActiveIntent"], "return_to_resource_area")

    def test_json_contains_schema_and_counts(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema"], "woodcut_bank_scenario_suite.v1")
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["passCount"], len(EXPECTED_SCENARIOS))
        self.assertEqual(payload["warnCount"], 0)
        self.assertEqual(payload["failCount"], 0)
        self.assertEqual(len(payload["scenarioResults"]), len(EXPECTED_SCENARIOS))

    def test_cli_writes_no_files(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--json", "--scenario", "bank_open_resources_held"],
                cwd=temp,
                capture_output=True,
                text=True,
                check=False,
            )
            after = set(os.listdir(temp))

        self.assertEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema"], "woodcut_bank_scenario_suite.v1")
        self.assertEqual(payload["scenarioResults"][0]["name"], "bank_open_resources_held")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_task_policy


class DiagnoseTaskPolicyTest(unittest.TestCase):
    def test_policy_matrix_for_inventory_full(self):
        cases = {
            "woodcutting_bank": ("inventory_full", "needs_service", True, True, False, "bank", None),
            "woodcutting_firemake": ("inventory_full", "process_inventory", True, False, True, None, "firemaking"),
            "woodcutting_drop": ("inventory_full", "process_inventory", True, False, True, None, "drop"),
            "combat_default": ("target_selected", "continue_task", False, False, False, None, None),
            "observe_only": ("observe", "observe", True, False, False, None, None),
        }

        for policy, expected in cases.items():
            with self.subTest(policy=policy):
                diagnostic = diagnose_task_policy.build_policy_diagnostic(
                    policy_name=policy,
                    task="woodcutting" if policy != "combat_default" else "combat",
                    inventory_full=True,
                    resource_count=28,
                    goal_count=5,
                )
                phase, intent, clear_target, service_runs, process_runs, service_type, process_type = expected
                self.assertEqual(diagnostic["expectedGenericPhase"], phase)
                self.assertEqual(diagnostic["expectedActiveIntent"], intent)
                self.assertEqual(diagnostic["targetShouldBeCleared"], clear_target)
                self.assertEqual(diagnostic["serviceAnalyzerShouldRun"], service_runs)
                self.assertEqual(diagnostic["processInventoryAnalyzerShouldRun"], process_runs)
                self.assertEqual(diagnostic["serviceTypeNeeded"], service_type)
                self.assertEqual(diagnostic["processTypeNeeded"], process_type)
                self.assertIn("serviceContext", diagnostic)
                self.assertIn("processInventoryContext", diagnostic)
                self.assertTrue(diagnostic["noActionEmitted"])

    def test_non_full_woodcutting_policies_keep_target_selection(self):
        for policy in ("woodcutting_bank", "woodcutting_firemake", "woodcutting_drop"):
            with self.subTest(policy=policy):
                diagnostic = diagnose_task_policy.build_policy_diagnostic(
                    policy_name=policy,
                    task="woodcutting",
                    inventory_full=False,
                    resource_count=5,
                    goal_count=5,
                )
                self.assertEqual(diagnostic["expectedGenericPhase"], "target_selected")
                self.assertEqual(diagnostic["expectedActiveIntent"], "continue_current_target")
                self.assertFalse(diagnostic["targetShouldBeCleared"])
                self.assertFalse(diagnostic["serviceAnalyzerShouldRun"])
                self.assertFalse(diagnostic["processInventoryAnalyzerShouldRun"])

    def test_json_cli_prints_stdout_only_and_creates_no_files(self):
        script = VIEWER_DIR / "diagnose_task_policy.py"
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--policy",
                    "woodcutting_firemake",
                    "--task",
                    "woodcutting",
                    "--inventory-full",
                    "true",
                    "--resource-count",
                    "28",
                    "--goal-count",
                    "5",
                    "--json",
                ],
                cwd=tmp,
                capture_output=True,
                text=True,
                check=False,
            )
            files = list(Path(tmp).iterdir())

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["selectedPolicy"], "woodcutting_firemake")
        self.assertEqual(payload["expectedActiveIntent"], "process_inventory")
        self.assertTrue(payload["processInventoryContext"]["processRequired"])
        self.assertEqual(files, [])

    def test_diagnostic_reports_service_and_process_context(self):
        bank = diagnose_task_policy.build_policy_diagnostic(
            policy_name="woodcutting_bank",
            task="woodcutting",
            inventory_full=True,
            resource_count=28,
            goal_count=5,
            service_candidates=[{"targetType": "sceneObject", "classId": "bank_booth", "targetName": "Bank booth"}],
        )
        firemake = diagnose_task_policy.build_policy_diagnostic(
            policy_name="woodcutting_firemake",
            task="woodcutting",
            inventory_full=True,
            resource_count=28,
            goal_count=5,
            inventory_items=[{"slot": 0, "itemId": 1511, "quantity": 27}, {"slot": 1, "itemId": 590, "quantity": 1}],
        )

        self.assertTrue(bank["serviceAnalyzerShouldRun"])
        self.assertTrue(bank["serviceCandidateExists"])
        self.assertEqual(bank["serviceContext"]["bestServiceCandidate"]["classId"], "bank_booth")
        self.assertEqual(bank["serviceContext"]["candidateCountsByType"], {"bank_booth": 1})
        self.assertEqual(bank["serviceContext"]["candidateCount"], 1)
        self.assertTrue(firemake["processInventoryAnalyzerShouldRun"])
        self.assertTrue(firemake["processInventoryContext"]["resourcesAvailable"])
        self.assertEqual(firemake["processInventoryContext"]["tinderboxStatus"], "present")


if __name__ == "__main__":
    unittest.main()

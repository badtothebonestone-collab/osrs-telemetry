import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_task_transition.py"
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_task_transition as transitions


class TaskTransitionDiagnosticTest(unittest.TestCase):
    def test_woodcutting_not_full_selects_tree(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_bank", "woodcutting_not_full")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "target_selected")
        self.assertEqual(payload["actualActiveIntent"], "continue_current_target")
        self.assertEqual(payload["overlaySelectedMarker"]["classId"], "tree")
        self.assertEqual(payload["overlaySelectedMarkerExpectation"], "selected_tree")

    def test_bank_full_without_service_waits_for_service_context(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_bank", "service_missing")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "inventory_full")
        self.assertEqual(payload["actualActiveIntent"], "needs_service")
        self.assertTrue(payload["serviceAnalyzerRuns"])
        self.assertFalse(payload["processInventoryAnalyzerRuns"])
        self.assertEqual(payload["navigationContextSummary"]["navigationReason"], "service_target_missing")
        self.assertIsNone(payload["overlaySelectedMarker"])

    def test_bank_full_with_service_selects_service_and_navigation_destination(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_bank", "service_visible")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "inventory_full")
        self.assertEqual(payload["actualActiveIntent"], "needs_service")
        self.assertTrue(payload["serviceAnalyzerRuns"])
        self.assertEqual(payload["serviceContextSummary"]["best"], "Bank booth")
        self.assertEqual(payload["navigationContextSummary"]["targetKind"], "service")
        self.assertTrue(payload["navigationContextSummary"]["navigationNeeded"])
        self.assertEqual(payload["overlaySelectedMarker"]["classId"], "bank_booth")
        self.assertEqual(payload["overlaySelectedMarkerExpectation"], "selected_service")

    def test_service_visible_not_arrived_stays_needs_service(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_bank", "service_visible_not_arrived")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "inventory_full")
        self.assertEqual(payload["actualActiveIntent"], "needs_service")
        self.assertFalse(payload["serviceContextSummary"]["serviceReady"])
        self.assertEqual(payload["overlaySelectedMarker"]["classId"], "bank_booth")

    def test_service_visible_arrived_becomes_service_available(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_bank", "service_visible_arrived")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "service_available")
        self.assertEqual(payload["actualActiveIntent"], "service_available")
        self.assertTrue(payload["serviceContextSummary"]["serviceReady"])
        self.assertEqual(payload["serviceContextSummary"]["serviceReadyReason"], "arrived_at_service")
        self.assertEqual(payload["overlaySelectedMarker"]["classId"], "bank_booth")

    def test_service_ready_bank_closed_stays_service_available(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_bank", "service_ready_bank_closed")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "service_available")
        self.assertEqual(payload["actualActiveIntent"], "service_available")
        self.assertTrue(payload["serviceContextSummary"]["serviceReady"])
        self.assertFalse(payload["bankUiContextSummary"]["bankOpen"])
        self.assertFalse(payload["bankUiContextSummary"]["bankReadable"])

    def test_service_ready_readable_bank_becomes_service_open(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_bank", "service_open")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "service_open")
        self.assertEqual(payload["actualActiveIntent"], "bank_operation_pending")
        self.assertTrue(payload["bankUiContextSummary"]["bankOpen"])
        self.assertTrue(payload["bankUiContextSummary"]["bankReadable"])
        self.assertTrue(payload["bankOperationContextSummary"]["operationNeeded"])
        self.assertEqual(payload["bankOperationContextSummary"]["operationType"], "deposit_inventory")
        self.assertEqual(payload["bankOperationContextSummary"]["resourceItemQuantity"], 28)

    def test_service_ready_readable_bank_without_logs_completes_service(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_bank", "service_complete")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "service_complete")
        self.assertEqual(payload["actualActiveIntent"], "resume_resource_collection")
        self.assertTrue(payload["bankUiContextSummary"]["bankReadable"])
        self.assertFalse(payload["bankOperationContextSummary"]["operationNeeded"])
        self.assertTrue(payload["bankOperationContextSummary"]["bankingComplete"])
        self.assertEqual(payload["bankOperationContextSummary"]["completionReason"], "no_resource_items_held")

    def test_bank_pin_open_becomes_blocked_user_resolution(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_bank", "bank_pin_required")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "blocked")
        self.assertEqual(payload["actualActiveIntent"], "needs_user_resolution")
        self.assertTrue(payload["bankUiContextSummary"]["bankPinOpen"])
        self.assertIn("bank_pin_required", payload["blockingConditions"])

    def test_firemake_ready_reports_process_context_without_service(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_firemake", "firemake_ready")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "inventory_full")
        self.assertEqual(payload["actualActiveIntent"], "process_inventory")
        self.assertFalse(payload["serviceAnalyzerRuns"])
        self.assertTrue(payload["processInventoryAnalyzerRuns"])
        self.assertEqual(payload["processContextSummary"]["processTypeNeeded"], "firemaking")
        self.assertEqual(payload["processContextSummary"]["tinderboxStatus"], "present")
        self.assertIsNone(payload["overlaySelectedMarker"])

    def test_firemake_missing_tinderbox_reports_missing_context(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_firemake", "firemake_ready", tinderbox_present=False)

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualActiveIntent"], "process_inventory")
        self.assertEqual(payload["processContextSummary"]["tinderboxStatus"], "missing")
        self.assertTrue(payload["processInventoryAnalyzerRuns"])

    def test_firemake_no_tree_candidates_still_reports_process_inventory(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_firemake", "firemake_no_tree_candidates")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "inventory_full")
        self.assertEqual(payload["actualActiveIntent"], "process_inventory")
        self.assertEqual(payload["inventoryFreshness"], "fresh")
        self.assertEqual(payload["processInventoryFreshness"], "fresh")
        self.assertIn(payload["targetCandidateFreshness"], {"stale", "unknown"})
        self.assertIsNone(payload["overlaySelectedMarker"])

    def test_firemake_live_style_fail_context_no_candidates_allows_process_inventory(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_firemake", "firemake_full_inventory_no_candidates_live_style")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "inventory_full")
        self.assertEqual(payload["actualActiveIntent"], "process_inventory")
        self.assertTrue(payload["processInventoryAnalyzerRuns"])
        self.assertEqual(payload["processContextSummary"]["processTypeNeeded"], "firemaking")
        self.assertEqual(payload["requiredContextDomains"], ["inventory", "process_inventory"])
        self.assertEqual(payload["missingRequiredContextDomains"], [])
        self.assertIn("target.candidates", payload["optionalMissingContextDomains"])
        self.assertFalse(payload["targetCandidatesRequired"])

    def test_drop_ready_reports_drop_context(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_drop", "drop_ready")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualActiveIntent"], "process_inventory")
        self.assertEqual(payload["processContextSummary"]["processTypeNeeded"], "drop")
        self.assertFalse(payload["serviceAnalyzerRuns"])
        self.assertIsNone(payload["overlaySelectedMarker"])

    def test_drop_no_tree_candidates_still_reports_process_inventory(self):
        payload = transitions.evaluate_transition_scenario("woodcutting_drop", "drop_no_tree_candidates")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "inventory_full")
        self.assertEqual(payload["actualActiveIntent"], "process_inventory")
        self.assertEqual(payload["processContextSummary"]["processTypeNeeded"], "drop")
        self.assertEqual(payload["inventoryFreshness"], "fresh")
        self.assertIsNone(payload["overlaySelectedMarker"])

    def test_combat_full_inventory_keeps_active_target(self):
        payload = transitions.evaluate_transition_scenario("combat_default", "combat_full_inventory")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "target_selected")
        self.assertEqual(payload["actualActiveIntent"], "continue_task")
        self.assertFalse(payload["serviceAnalyzerRuns"])
        self.assertFalse(payload["processInventoryAnalyzerRuns"])
        self.assertEqual(payload["overlaySelectedMarker"]["classId"], "tree")

    def test_observe_only_inventory_full_has_no_service_or_process(self):
        payload = transitions.evaluate_transition_scenario("observe_only", "woodcutting_inventory_full")

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["actualPhase"], "observe")
        self.assertEqual(payload["actualActiveIntent"], "observe")
        self.assertFalse(payload["serviceAnalyzerRuns"])
        self.assertFalse(payload["processInventoryAnalyzerRuns"])
        self.assertIsNone(payload["overlaySelectedMarker"])

    def test_json_cli_prints_stdout_only_and_writes_no_files(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--policy",
                    "woodcutting_bank",
                    "--scenario",
                    "service_visible",
                    "--json",
                ],
                cwd=temp,
                capture_output=True,
                text=True,
                check=False,
            )
            after = set(os.listdir(temp))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(before, after)

    def test_daemon_observer_summarizes_status_only(self):
        payload = transitions.build_from_daemon(
            {
                "brainTaskPolicy": "woodcutting_bank",
                "brain": {
                    "genericTaskState": {"phase": "inventory_full", "activeIntent": "needs_service"},
                    "serviceContext": {"serviceNeeded": True, "serviceTypeNeeded": "bank", "candidateCount": 1, "bestServiceCandidate": {"targetName": "Bank booth", "classId": "bank_booth"}},
                    "processInventoryContext": {"processRequired": False},
                    "navigationIntentContext": {"navigationNeeded": True, "navigationReason": "service_target_available", "targetKind": "service", "destinationTarget": {"targetName": "Bank booth"}},
                    "noActionEmitted": True,
                },
            },
            policy_name="woodcutting_bank",
        )

        self.assertEqual(payload["source"], "daemon-memory")
        self.assertEqual(payload["actualPhase"], "inventory_full")
        self.assertEqual(payload["actualActiveIntent"], "needs_service")
        self.assertEqual(payload["serviceContextSummary"]["best"], "Bank booth")
        self.assertTrue(payload["navigationContextSummary"]["navigationNeeded"])
        self.assertTrue(payload["noActionEmitted"])


if __name__ == "__main__":
    unittest.main()

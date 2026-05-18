import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_cycle_history.py"
sys.path.insert(0, str(VIEWER_DIR))

import cycle_history
import diagnose_cycle_history as diagnostic


def entry(
    tick: int,
    stage: str,
    *,
    phase: str | None = None,
    active_intent: str | None = None,
    selected_target_name: str | None = None,
    bank_open: bool | None = None,
    banking_complete: bool | None = None,
    service_ready: bool | None = None,
    close_bank_needed: bool | None = None,
    resource_target_available: bool | None = None,
) -> dict:
    return {
        "tick": tick,
        "cycleStage": stage,
        "phase": phase or stage,
        "activeIntent": active_intent or stage,
        "reason": stage,
        "selectedTargetName": selected_target_name,
        "selectedTargetType": "tree" if selected_target_name else None,
        "inventoryFreeSlots": 23,
        "inventoryFull": False,
        "serviceReady": service_ready,
        "bankOpen": bank_open,
        "bankReadable": None,
        "operationNeeded": None,
        "bankingComplete": banking_complete,
        "closeBankNeeded": close_bank_needed,
        "postBankReason": None,
        "returnReason": None,
        "resourceTargetAvailable": resource_target_available,
        "warningCount": 0,
        "missingCapabilityCount": 0,
    }


class CycleHistoryTest(unittest.TestCase):
    def test_history_appends_on_stage_change(self):
        tracker = cycle_history.CycleHistoryTracker(capacity=10)
        self.assertTrue(tracker.update(entry(1, "collecting_resources")))
        self.assertTrue(tracker.update(entry(2, "inventory_full")))

        summary = tracker.summary()
        self.assertEqual(summary["cycleHistoryCount"], 2)
        self.assertEqual(summary["currentCycleStage"], "inventory_full")
        self.assertEqual(summary["lastCycleStage"], "collecting_resources")
        self.assertEqual(summary["transitionCount"], 1)
        self.assertEqual(summary["lastCycleTransitionReason"], "inventory_full")

    def test_history_does_not_append_when_state_unchanged(self):
        tracker = cycle_history.CycleHistoryTracker(capacity=10)
        tracker.update(entry(1, "collecting_resources"))
        self.assertFalse(tracker.update(entry(5, "collecting_resources")))

        summary = tracker.summary()
        self.assertEqual(summary["cycleHistoryCount"], 1)
        self.assertEqual(summary["currentCycleStageStableForTicks"], 4)

    def test_history_appends_on_bank_open_flip(self):
        tracker = cycle_history.CycleHistoryTracker(capacity=10)
        tracker.update(entry(10, "service_ready", bank_open=False))
        appended = tracker.update(entry(11, "service_ready", bank_open=True))

        self.assertTrue(appended)
        self.assertEqual(tracker.summary()["cycleHistoryCount"], 2)

    def test_history_appends_on_banking_complete_flip(self):
        tracker = cycle_history.CycleHistoryTracker(capacity=10)
        tracker.update(entry(20, "bank_operation_pending", banking_complete=False))
        appended = tracker.update(entry(21, "bank_operation_pending", banking_complete=True))

        self.assertTrue(appended)
        self.assertEqual(tracker.summary()["cycleHistoryCount"], 2)

    def test_history_is_capped(self):
        tracker = cycle_history.CycleHistoryTracker(capacity=3)
        for tick in range(6):
            tracker.update(entry(tick, f"stage_{tick}"))

        summary = tracker.summary(tail=10)
        self.assertEqual(summary["cycleHistoryCount"], 3)
        self.assertEqual([row["cycleStage"] for row in summary["cycleHistory"]], ["stage_3", "stage_4", "stage_5"])

    def test_build_entry_from_cycle_payload_keeps_compact_fields(self):
        row = cycle_history.entry_from_cycle_payload(
            {
                "cycleStage": "close_bank_needed",
                "phase": "waiting_for_world_view",
                "activeIntent": "close_service_context",
                "reason": "bank_ui_still_open",
                "serviceReady": False,
                "bankOpen": True,
                "bankReadable": True,
                "bankOperation": {"operationNeeded": False, "bankingComplete": True},
                "closeBankNeeded": True,
                "postBank": {"reason": "bank_ui_still_open"},
                "returnToResource": {"reason": "no_resource_target_observed", "resourceTargetAvailable": False},
                "inventory": {"freeSlots": 15, "inventoryFull": False},
                "overlay": {"selected": {"targetName": "Oak tree", "classId": "tree"}},
                "warnings": ["one"],
                "missingCapabilities": ["target.candidates"],
            },
            tick=42,
            timestamp="2026-05-18T22:00:00Z",
        )

        self.assertEqual(row["tick"], 42)
        self.assertEqual(row["cycleStage"], "close_bank_needed")
        self.assertEqual(row["selectedTargetName"], "Oak tree")
        self.assertTrue(row["bankingComplete"])
        self.assertEqual(row["warningCount"], 1)
        self.assertEqual(row["missingCapabilityCount"], 1)

    def test_history_records_return_to_resource_area_transition_reason(self):
        row = cycle_history.entry_from_cycle_payload(
            {
                "cycleStage": "return_to_resource",
                "phase": "return_to_resource",
                "activeIntent": "return_to_resource_area",
                "reason": "using_remembered_resource_area",
                "bankOpen": False,
                "bankOperation": {"bankingComplete": True},
                "resourceReturn": {"reason": "using_remembered_resource_area"},
                "returnToResource": {"reason": "no_resource_target_observed", "resourceTargetAvailable": False},
                "inventory": {"freeSlots": 15, "inventoryFull": False},
            },
            tick=51,
        )

        self.assertEqual(row["activeIntent"], "return_to_resource_area")
        self.assertEqual(row["returnReason"], "using_remembered_resource_area")

    def test_diagnostic_prints_transition_tail(self):
        payload = {
            "schema": "cycle_history_diagnostic.v1",
            "status": "PASS",
            "current": {"cycleStage": "close_bank_needed", "phase": "waiting_for_world_view", "activeIntent": "close_service_context", "reason": "bank_ui_still_open"},
            "cycleHistoryCount": 2,
            "transitionCount": 1,
            "currentCycleStageStableForTicks": 3,
            "lastCycleStage": "bank_operation_pending",
            "lastStageChangeTick": 10,
            "lastWarningSummary": {"warningCount": 0, "missingCapabilityCount": 0},
            "cycleHistory": [
                {"tick": 9, "cycleStage": "bank_operation_pending", "reason": "deposit_inventory"},
                {"tick": 10, "cycleStage": "close_bank_needed", "previousCycleStage": "bank_operation_pending", "reason": "bank_ui_still_open", "transition": True},
            ],
            "warnings": [],
            "missingCapabilities": [],
        }
        text = diagnostic.format_human(payload)

        self.assertIn("WOODCUT BANK CYCLE HISTORY", text)
        self.assertIn("Stage: close_bank_needed", text)
        self.assertIn("tick 10: bank_operation_pending -> close_bank_needed reason=bank_ui_still_open", text)
        self.assertIn("Total history entries: 2", text)

    def test_json_contains_schema_and_recent_entries(self):
        payload = diagnostic.build_from_daemon(
            {
                "cycleHistory": {
                    "currentCycleStage": "close_bank_needed",
                    "cycleHistoryCount": 1,
                    "transitionCount": 0,
                    "cycleHistory": [entry(1, "close_bank_needed")],
                }
            },
            tail=5,
        )

        self.assertEqual(payload["schema"], "cycle_history_diagnostic.v1")
        self.assertEqual(payload["current"]["cycleStage"], "close_bank_needed")
        self.assertEqual(len(payload["cycleHistory"]), 1)

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
        self.assertEqual(payload["schema"], "cycle_history_diagnostic.v1")
        self.assertFalse(payload["daemonReachable"])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

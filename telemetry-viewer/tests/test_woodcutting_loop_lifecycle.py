import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import context_service
import task_script_api
import telemetry_ui
import update_project_knowledge
import woodcutting_loop_lifecycle


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def woodcutting(*, free_slots: int, logs_gained: int = 1, phase: str = "chopping") -> dict:
    return {
        "schema": "woodcutting_lifecycle.v1",
        "status": "PASS",
        "phase": "inventory_full" if free_slots == 0 else phase,
        "confidence": 0.95,
        "inventory": {
            "freeSlotsStart": free_slots + logs_gained,
            "freeSlotsEnd": free_slots,
            "normalLogsStart": 0,
            "normalLogsEnd": logs_gained,
            "normalLogsGained": logs_gained,
            "inventoryFull": free_slots == 0,
        },
        "clicks": {"freshChopClickCount": 1},
        "animation": {"activeSnapshotCount": 3},
        "current": {"freeSlots": free_slots, "inventoryFull": free_slots == 0},
        "cycles": [{"cycle": 1}],
    }


def full_loop_woodcutting() -> dict:
    return {
        "schema": "woodcutting_lifecycle.v1",
        "status": "WARN",
        "phase": "idle",
        "confidence": 0.7,
        "inventory": {
            "freeSlotsStart": 28,
            "freeSlotsEnd": 28,
            "normalLogsStart": 0,
            "normalLogsEnd": 0,
            "normalLogsGained": 0,
            "inventoryFull": False,
        },
        "clicks": {"freshChopClickCount": 8},
        "animation": {"activeSnapshotCount": 13},
        "current": {"freeSlots": 28, "inventoryFull": False},
        "cycles": [{"cycleIndex": 1, "logsGained": 12}, {"cycleIndex": 2, "logsGained": 16}],
        "warnings": ["No positive normal log gain was found."],
    }


def traversal(start: str, end: str, route_name: str) -> dict:
    return {
        "schema": "traversal_lifecycle.v1",
        "status": "PASS",
        "routeName": route_name,
        "phase": "arrived",
        "confidence": 0.9,
        "start": {"areaLabel": start},
        "end": {"areaLabel": end},
        "routeSegmentCount": 5,
        "successfulSegmentCount": 5,
    }


def full_loop_traversal() -> dict:
    payload = traversal("woodcutting_area", "woodcutting_area", "route_unknown")
    payload["rawSteps"] = [
        {"stepIndex": 1, "postcondition": {"areaBefore": "woodcutting_area", "areaAfter": "bank_area"}, "action": "Open", "targetName": "Door"},
        {"stepIndex": 2, "postcondition": {"areaBefore": "bank_area", "areaAfter": "woodcutting_area"}, "action": "Climb-down", "targetName": "Staircase"},
    ]
    return payload


def banking_deposit() -> dict:
    return {
        "schema": "banking_lifecycle.v1",
        "status": "PASS",
        "phase": "complete",
        "confidence": 0.95,
        "bankLikeInterface": "bank",
        "bank": {"openSeen": True, "containerAvailable": True, "bankUiPresent": True},
        "inventory": {"freeSlotsBefore": 0, "freeSlotsAfter": 16, "normalLogsBefore": 16, "normalLogsAfter": 0},
        "deposit": {
            "detected": True,
            "items": [{"id": 1511, "name": "Logs", "quantity": 16, "confirmationLevel": "bank_container_delta_confirmed"}],
            "totalDepositedCount": 16,
            "confirmationLevel": "bank_container_delta_confirmed",
        },
        "bankContainerDeltaAvailable": True,
        "depositConfirmationLevel": "bank_container_delta_confirmed",
    }


def bank_delta_deposit() -> dict:
    payload = banking_deposit()
    payload["phase"] = "bank_open"
    payload["deposit"] = {"detected": False, "items": [], "totalDepositedCount": 0, "confirmationLevel": "none"}
    payload["actions"] = {"depositActionCount": 1}
    payload["bank"]["changedItems"] = [{"id": 1511, "name": "Logs", "before": 153, "after": 181, "delta": 28}]
    payload["depositConfirmationLevel"] = "widget_action_confirmed"
    return payload


def interruption(*, resumed: bool) -> dict:
    return {
        "schema": "interruption_lifecycle.v1",
        "status": "PASS",
        "interruptionDetected": True,
        "interruptionType": "combat",
        "primaryCause": "mugger_attack",
        "taskResumed": resumed,
        "confidence": 0.95,
        "combat": {"combatObserved": True, "hitsplatsSeen": 3},
    }


class WoodcuttingLoopLifecycleTest(unittest.TestCase):
    def compact(self, **kwargs):
        return woodcutting_loop_lifecycle.compact_lifecycle(woodcutting_loop_lifecycle.analyze_data(**kwargs))

    def test_woodcutting_inventory_full_next_route_to_bank(self):
        compact = self.compact(woodcutting_lifecycle=woodcutting(free_slots=0))
        self.assertEqual(compact["loopState"], "inventory_full")
        self.assertEqual(compact["nextExpectedPhase"], "route_to_bank")

    def test_woodcutting_near_full_next_continue_cutting(self):
        compact = self.compact(woodcutting_lifecycle=woodcutting(free_slots=1))
        self.assertEqual(compact["loopState"], "cutting")
        self.assertEqual(compact["nextExpectedPhase"], "continue_cutting")

    def test_route_to_bank_next_banking_deposit(self):
        compact = self.compact(traversal_lifecycle=traversal("woodcutting_area", "bank_area", "woodcutting_area_to_bank"))
        self.assertEqual(compact["loopState"], "routing_to_bank")
        self.assertEqual(compact["nextExpectedPhase"], "banking_deposit")

    def test_banking_deposit_next_route_to_woodcutting_area(self):
        compact = self.compact(banking_lifecycle=banking_deposit())
        self.assertEqual(compact["loopState"], "deposit_complete")
        self.assertEqual(compact["nextExpectedPhase"], "route_to_woodcutting_area")
        self.assertTrue(task_script_api.did_deposit_logs({"woodcutting_loop_lifecycle": woodcutting_loop_lifecycle.analyze_data(banking_lifecycle=banking_deposit())}))

    def test_route_to_trees_next_resume_cutting(self):
        compact = self.compact(traversal_lifecycle=traversal("bank_area", "woodcutting_area", "Bank_to_Woodcutting_area"))
        self.assertEqual(compact["loopState"], "routing_to_trees")
        self.assertEqual(compact["nextExpectedPhase"], "resume_cutting")

    def test_interruption_without_resume_next_recover(self):
        compact = self.compact(woodcutting_lifecycle=woodcutting(free_slots=5), interruption_lifecycle=interruption(resumed=False))
        self.assertEqual(compact["loopState"], "interrupted")
        self.assertEqual(compact["nextExpectedPhase"], "recover_or_resume_task")

    def test_interruption_with_resume_next_continue_current_phase(self):
        compact = self.compact(woodcutting_lifecycle=woodcutting(free_slots=1), interruption_lifecycle=interruption(resumed=True))
        self.assertEqual(compact["loopState"], "resumed_cutting")
        self.assertEqual(compact["nextExpectedPhase"], "continue_current_phase")

    def test_loop_lifecycle_combines_multiple_partial_inputs(self):
        lifecycle = woodcutting_loop_lifecycle.analyze_data(
            woodcutting_lifecycle=woodcutting(free_slots=0),
            traversal_lifecycle=traversal("woodcutting_area", "bank_area", "woodcutting_area_to_bank"),
        )
        phases = [item["phase"] for item in lifecycle["detectedPhases"]]
        self.assertIn("inventory_full", phases)
        self.assertIn("routing_to_bank", phases)

    def test_full_loop_single_recording_uses_cycle_and_bank_delta_evidence(self):
        compact = self.compact(
            woodcutting_lifecycle=full_loop_woodcutting(),
            banking_lifecycle=bank_delta_deposit(),
            traversal_lifecycle=full_loop_traversal(),
            interruption_lifecycle=interruption(resumed=True),
        )
        self.assertEqual(compact["loopState"], "complete")
        self.assertEqual(compact["nextExpectedPhase"], "continue_current_phase")
        self.assertIn("routing_to_bank", compact["detectedPhases"])
        self.assertIn("routing_to_trees", compact["detectedPhases"])
        self.assertEqual(compact["normalLogsGained"], 28)
        self.assertTrue(compact["inventoryFull"])
        self.assertTrue(compact["depositComplete"])
        self.assertTrue(compact["depositedLogs"])
        self.assertEqual(compact["routeLegCount"], 2)
        route_legs = {item["phase"]: item for item in compact["routeLegs"]}
        self.assertEqual(route_legs["route_to_bank"]["routeName"], "woodcutting_area_to_bank")
        self.assertEqual(route_legs["route_to_trees"]["routeName"], "Bank_to_Woodcutting_area")

    def test_context_recording_summary_returns_loop_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = root / "20260607_120000_loop"
            lifecycle = woodcutting_loop_lifecycle.analyze_data(woodcutting_lifecycle=woodcutting(free_slots=0))
            write_json(recording / "summary.json", {"recording_id": recording.name, "woodcutting_loop_lifecycle": lifecycle})
            payload = context_service.recording_summary_payload(recording.name, root=root)
        self.assertEqual(payload["woodcuttingLoopNextExpectedPhase"], "route_to_bank")

    def test_task_script_api_exposes_next_expected_phase(self):
        lifecycle = woodcutting_loop_lifecycle.analyze_data(woodcutting_lifecycle=woodcutting(free_slots=0))
        source = {"woodcutting_loop_lifecycle": lifecycle}
        self.assertEqual(task_script_api.get_next_expected_phase(source), "route_to_bank")
        self.assertTrue(task_script_api.should_route_to_bank(source))

    def test_ui_analyzer_command_includes_loop_lifecycle(self):
        command = telemetry_ui.build_analyzer_command(Path("recordings/test"), telemetry_ui.default_config())
        self.assertIn("--woodcutting-loop-lifecycle", command)

    def test_knowledge_updater_indexes_loop_capability(self):
        model = update_project_knowledge.build_project_knowledge()
        capability_ids = {item["id"] for item in model["capabilities"]}
        gap_ids = {item["id"] for item in model["gaps"]}
        loop_capability = next(item for item in model["capabilities"] if item["id"] == "woodcutting_loop_lifecycle")
        self.assertIn("woodcutting_loop_lifecycle", capability_ids)
        self.assertIn("20260607_171427_Wood_cutting_attacked", loop_capability["evidenceRecordings"])
        self.assertNotIn("full_woodcutting_loop_fixture", gap_ids)


if __name__ == "__main__":
    unittest.main()

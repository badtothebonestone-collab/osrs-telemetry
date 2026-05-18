import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_woodcut_bank_cycle.py"
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_woodcut_bank_cycle as diagnostic

DEFAULT_TARGET = object()


def target(name: str = "Tree", class_id: str = "tree") -> dict:
    return {"targetName": name, "classId": class_id, "worldX": 3156, "worldY": 3237}


def status_for(
    *,
    phase: str = "target_selected",
    active_intent: str = "continue_current_target",
    inventory_full: bool = False,
    free_slots: int = 23,
    resource_held: int = 5,
    active_target: dict | None | object = DEFAULT_TARGET,
    service: dict | None = None,
    pathing: dict | None = None,
    bank_ui: dict | None = None,
    bank_operation: dict | None = None,
    close_bank: dict | None = None,
    post_bank: dict | None = None,
    return_context: dict | None = None,
    blocking: list[str] | None = None,
    overlay_target: dict | None = None,
) -> dict:
    active_target = target() if active_target is DEFAULT_TARGET else active_target
    brain = {
        "task": "woodcutting",
        "genericTaskState": {
            "task": "woodcutting",
            "phase": phase,
            "activeIntent": active_intent,
            "activeIntentTarget": active_target,
            "blockingConditions": blocking or [],
        },
        "goalProgress": {"displayedGoalProgress": 2, "goalCount": 5},
        "inventoryContext": {
            "inventoryFull": inventory_full,
            "freeSlots": free_slots,
            "progress": {"currentHeldCount": resource_held},
        },
        "serviceContext": service or {},
        "pathingContext": pathing or {},
        "bankUiContext": bank_ui or {},
        "bankOperationContext": bank_operation or {},
        "closeBankContext": close_bank or {},
        "postBankReacquisitionContext": post_bank or {},
        "returnToResourceContext": return_context or {},
        "requiredContextDomains": [],
        "missingRequiredContextDomains": [],
        "optionalMissingContextDomains": [],
        "noActionEmitted": True,
    }
    if overlay_target is not None:
        brain["intentOverlayContext"] = {
            "selectedMarker": overlay_target,
            "summary": {"intentMarkerCount": 1, "pathMarkersEmitted": 0},
        }
    return {
        "brainTask": "woodcutting",
        "brainTaskPolicy": "woodcutting_bank",
        "activeMissionPreset": "woodcut_bank",
        "brainProgress": {"displayedGoalProgress": 2, "goalCount": 5},
        "inventoryFull": inventory_full,
        "inventoryFreeSlots": free_slots,
        "brain": brain,
    }


class WoodcutBankCycleDiagnosticTest(unittest.TestCase):
    def assert_stage(self, expected: str, **kwargs):
        payload = diagnostic.build_from_daemon(status_for(**kwargs))
        self.assertEqual(payload["cycleStage"], expected)
        return payload

    def test_inventory_not_full_with_target_selected_is_collecting_resources(self):
        payload = self.assert_stage("collecting_resources", inventory_full=False, active_target=target())
        self.assertEqual(payload["activeTask"], "woodcutting")
        self.assertEqual(payload["taskPolicy"], "woodcutting_bank")
        self.assertEqual(payload["missionPreset"], "woodcut_bank")
        self.assertEqual(payload["progress"]["displayedGoalProgress"], 2)

    def test_inventory_full_with_service_not_ready_is_needs_service_or_pathing(self):
        self.assert_stage(
            "needs_service",
            phase="inventory_full",
            active_intent="needs_service",
            inventory_full=True,
            free_slots=0,
            service={"serviceNeeded": True, "serviceReady": False},
            active_target=None,
        )
        self.assert_stage(
            "pathing_to_service",
            phase="inventory_full",
            active_intent="needs_service",
            inventory_full=True,
            free_slots=0,
            service={"serviceNeeded": True, "serviceReady": False},
            pathing={"pathingNeeded": True, "pathCompleted": False},
            active_target=None,
        )

    def test_service_ready_with_bank_closed_is_service_ready(self):
        self.assert_stage(
            "service_ready",
            phase="service_available",
            active_intent="service_available",
            inventory_full=True,
            free_slots=0,
            service={"serviceNeeded": True, "serviceReady": True},
            bank_ui={"bankOpen": False},
            active_target=None,
        )

    def test_bank_readable_with_logs_held_is_bank_operation_pending(self):
        payload = self.assert_stage(
            "bank_operation_pending",
            phase="service_open",
            active_intent="bank_operation_pending",
            inventory_full=True,
            free_slots=0,
            bank_ui={"bankOpen": True, "bankReadable": True},
            bank_operation={"operationNeeded": True, "operationType": "deposit_inventory", "resourceItemsHeld": 28},
            active_target=None,
        )
        self.assertEqual(payload["bankOperation"]["operationType"], "deposit_inventory")
        self.assertEqual(payload["resourceItemsHeld"], 28)

    def test_banking_complete_with_bank_open_and_close_needed_is_close_bank_needed(self):
        self.assert_stage(
            "close_bank_needed",
            phase="waiting_for_world_view",
            active_intent="close_service_context",
            free_slots=15,
            resource_held=0,
            bank_ui={"bankOpen": True, "bankReadable": True},
            bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
            close_bank={"closeBankNeeded": True, "closeBankReady": True},
            active_target=None,
        )

    def test_generic_context_fail_blocker_does_not_hide_close_bank_needed(self):
        payload = self.assert_stage(
            "close_bank_needed",
            phase="waiting_for_world_view",
            active_intent="close_service_context",
            free_slots=15,
            resource_held=0,
            bank_ui={"bankOpen": True, "bankReadable": True, "bankPinOpen": False},
            bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
            close_bank={"closeBankNeeded": True, "closeBankReady": True, "reason": "bank_ui_still_open"},
            post_bank={"reason": "bank_ui_still_open", "resourceTargetReacquisitionAllowed": False},
            blocking=["context status is FAIL"],
            active_target=None,
        )
        self.assertEqual(payload["reason"], "bank_ui_still_open")

    def test_banking_complete_with_bank_open_and_post_bank_reason_waits_for_world_view(self):
        self.assert_stage(
            "waiting_for_world_view",
            phase="waiting_for_world_view",
            active_intent="wait_for_world_view",
            free_slots=15,
            resource_held=0,
            bank_ui={"bankOpen": True},
            bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
            post_bank={"reason": "bank_ui_still_open", "resourceTargetReacquisitionAllowed": False},
            active_target=None,
        )

    def test_banking_complete_with_bank_closed_and_no_resource_target_returns_to_resource(self):
        self.assert_stage(
            "return_to_resource",
            phase="needs_more_context",
            active_intent="select_target",
            free_slots=15,
            resource_held=0,
            bank_ui={"bankOpen": False},
            bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
            post_bank={"resourceTargetReacquisitionAllowed": True, "reason": "no_resource_target_observed"},
            return_context={"returnNeeded": True, "returnReady": False, "resourceTargetAvailable": False},
            active_target=None,
        )

    def test_resource_target_selected_after_banking_complete(self):
        payload = self.assert_stage(
            "resource_target_selected",
            phase="target_selected",
            active_intent="select_target",
            free_slots=15,
            resource_held=0,
            bank_ui={"bankOpen": False},
            bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
            return_context={"returnNeeded": True, "returnReady": True, "resourceTargetAvailable": True, "bestResourceTarget": target("Oak tree")},
            active_target=target("Oak tree"),
            overlay_target={"markerType": "selected_target", "targetName": "Oak tree", "classId": "tree"},
        )
        self.assertEqual(payload["returnToResource"]["bestResourceTarget"], "Oak tree")
        self.assertEqual(payload["overlay"]["selected"]["targetName"], "Oak tree")

    def test_bank_pin_is_blocked(self):
        payload = self.assert_stage(
            "blocked",
            phase="blocked",
            active_intent="needs_user_resolution",
            bank_ui={"bankOpen": True, "bankPinOpen": True},
            active_target=None,
        )
        self.assertIn("bank_pin_required", payload["warnings"])

    def test_json_output_includes_expected_schema_and_fields(self):
        payload = diagnostic.build_from_daemon(status_for())
        self.assertEqual(payload["schema"], "woodcut_bank_cycle_diagnostic.v1")
        self.assertIn("cycleStage", payload)
        self.assertIn("inventory", payload)
        self.assertIn("service", payload)
        self.assertIn("bank", payload)
        self.assertIn("returnToResource", payload)
        self.assertIn("overlay", payload)

    def test_task_policy_falls_back_to_generic_task_policy_name(self):
        status = status_for()
        status.pop("brainTaskPolicy")
        status["brain"]["genericTaskState"]["taskPolicy"] = {"name": "woodcutting_bank"}
        payload = diagnostic.build_from_daemon(status)
        self.assertEqual(payload["taskPolicy"], "woodcutting_bank")

    def test_human_output_names_expected_sections(self):
        text = diagnostic.format_human(diagnostic.build_from_daemon(status_for()))
        self.assertIn("WOODCUT BANK CYCLE -", text)
        self.assertIn("Cycle:", text)
        self.assertIn("Inventory:", text)
        self.assertIn("Service:", text)
        self.assertIn("Bank:", text)
        self.assertIn("Return:", text)
        self.assertIn("Overlay:", text)

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
        self.assertEqual(payload["schema"], "woodcut_bank_cycle_diagnostic.v1")
        self.assertFalse(payload["daemonReachable"])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

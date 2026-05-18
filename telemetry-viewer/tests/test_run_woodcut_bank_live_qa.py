import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "run_woodcut_bank_live_qa.py"
sys.path.insert(0, str(VIEWER_DIR))

import run_woodcut_bank_live_qa as live_qa


def snapshot_response(game_state: str = "LOGGED_IN") -> dict:
    return {
        "schema": "plugin_snapshot_response.v1",
        "status": "PASS",
        "latestTick": 123,
        "payloads": {
            "baseline": {
                "gameState": game_state,
                "player": {"worldX": 3190, "worldY": 3246, "plane": 0},
            }
        },
        "warnings": [],
        "missingCapabilities": [],
    }


def status_for(
    *,
    phase: str = "target_selected",
    active_intent: str = "continue_current_target",
    active_target: dict | None = None,
    inventory_full: bool = False,
    free_slots: int = 15,
    bank_ui: dict | None = None,
    bank_operation: dict | None = None,
    close_bank: dict | None = None,
    post_bank: dict | None = None,
    return_context: dict | None = None,
    resource_return: dict | None = None,
    missing_required: list[str] | None = None,
    optional_missing: list[str] | None = None,
) -> dict:
    if active_target is None and active_intent in {"continue_current_target", "target_selected", "wait_for_result"}:
        active_target = {"targetName": "Oak tree", "classId": "tree", "worldX": 3219, "worldY": 3206, "plane": 0}
    brain = {
        "task": "woodcutting",
        "genericTaskState": {
            "phase": phase,
            "activeIntent": active_intent,
            "activeIntentTarget": active_target,
            "blockingConditions": [],
        },
        "goalProgress": {"displayedGoalProgress": 2, "goalCount": 5},
        "inventoryContext": {"inventoryFull": inventory_full, "freeSlots": free_slots, "progress": {"currentHeldCount": 0}},
        "serviceContext": {"serviceNeeded": False, "bestServiceCandidate": active_target or {}},
        "pathingContext": {"pathingNeeded": False, "pathCompleted": False},
        "bankUiContext": bank_ui or {"bankOpen": False, "bankReadable": False, "bankPinOpen": False},
        "bankOperationContext": bank_operation or {"bankingComplete": False, "operationNeeded": False, "resourceItemsHeld": 0},
        "closeBankContext": close_bank or {"closeBankNeeded": False, "closeBankReady": False},
        "postBankReacquisitionContext": post_bank or {"reason": "not_applicable", "resourceTargetReacquisitionAllowed": False},
        "returnToResourceContext": return_context or {"returnNeeded": False, "returnReady": False, "resourceTargetAvailable": False},
        "resourceReturnContext": resource_return or {
            "resourceMemoryValid": True,
            "returnDestinationNeeded": False,
            "returnDestinationAvailable": False,
            "reason": "not_applicable",
        },
        "intentOverlayContext": {
            "selectedMarker": active_target or {},
            "summary": {"intentMarkerCount": 1, "pathMarkersEmitted": 0},
        },
        "requiredContextDomains": ["inventory"],
        "missingRequiredContextDomains": missing_required or [],
        "optionalMissingContextDomains": optional_missing or [],
        "noActionEmitted": True,
        "warnings": [],
    }
    return {
        "schema": "context_status.v1",
        "status": "ok",
        "liveCoreDaemonActive": True,
        "inputSourceActive": "plugin-snapshot",
        "dailyMode": "snapshot-no-files",
        "noFileDaily": True,
        "compactPacketFilesRequired": False,
        "compactPacketFilesWriting": False,
        "brainTaskPolicy": "woodcutting_bank",
        "currentCycleStageStableForTicks": 4,
        "brain": brain,
        "warnings": [],
    }


def args(**overrides) -> Namespace:
    values = {
        "daemon_url": "http://daemon",
        "snapshot_url": "http://snapshot",
        "latest_session": False,
        "json": False,
        "timeout": 0.01,
        "skip_daemon_check": False,
        "tail": 20,
        "strict": False,
    }
    values.update(overrides)
    return Namespace(**values)


class RunWoodcutBankLiveQaTest(unittest.TestCase):
    def test_endpoint_unreachable_is_fail(self):
        report = live_qa.build_report(
            args(),
            post_json_func=mock.Mock(side_effect=OSError("snapshot down")),
            fetch_json_func=mock.Mock(return_value={}),
            processes=[],
        )

        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["endpoint"]["snapshotReachable"])
        self.assertTrue(any("snapshot endpoint unreachable" in failure for failure in report["failures"]))

    def test_daemon_unreachable_is_fail(self):
        report = live_qa.build_report(
            args(),
            post_json_func=mock.Mock(return_value=snapshot_response()),
            fetch_json_func=mock.Mock(side_effect=OSError("daemon down")),
            processes=[],
        )

        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["endpoint"]["snapshotReachable"])
        self.assertFalse(report["endpoint"]["daemonReachable"])
        self.assertTrue(any("daemon unreachable" in failure for failure in report["failures"]))

    def test_logged_in_bank_open_deferral_is_warn_not_fail(self):
        status = status_for(
            phase="waiting_for_world_view",
            active_intent="close_service_context",
            active_target=None,
            bank_ui={"bankOpen": True, "bankReadable": True, "bankPinOpen": False},
            bank_operation={"bankingComplete": True, "operationNeeded": False, "resourceItemsHeld": 0},
            close_bank={"closeBankNeeded": True, "closeBankReady": True, "reason": "close_button_available"},
            post_bank={"reason": "bank_ui_still_open", "resourceTargetReacquisitionAllowed": False},
            missing_required=["target.candidates", "target.freshness"],
        )
        report = live_qa.build_report(
            args(),
            post_json_func=mock.Mock(return_value=snapshot_response()),
            fetch_json_func=mock.Mock(side_effect=[{"status": "PASS"}, status, {"status": "FAIL", "missingRequiredContextDomains": ["target.candidates", "target.freshness"]}, status["brain"]]),
            processes=[],
        )

        self.assertEqual(report["status"], "WARN")
        self.assertEqual(report["cycle"]["cycleStage"], "close_bank_needed")
        self.assertFalse(any("target.candidates" in failure for failure in report["failures"]))
        self.assertTrue(any("bank UI is still open" in warning for warning in report["warnings"]))

    def test_valid_resource_return_destination_with_no_targets_is_warn_not_fail(self):
        resource_return = {
            "resourceMemoryValid": True,
            "returnDestinationNeeded": True,
            "returnDestinationAvailable": True,
            "returnDestinationTile": {"worldX": 3219, "worldY": 3206, "plane": 0},
            "returnDestinationSource": "last_resource_target",
            "reason": "using_remembered_resource_area",
        }
        status = status_for(
            phase="return_to_resource",
            active_intent="return_to_resource_area",
            active_target={"targetName": "Resource return", "classId": "resource_return", "worldX": 3219, "worldY": 3206, "plane": 0},
            bank_ui={"bankOpen": False, "bankReadable": False, "bankPinOpen": False},
            bank_operation={"bankingComplete": True, "operationNeeded": False, "operationType": "none", "resourceItemsHeld": 0},
            post_bank={"reason": "no_resource_target_observed", "resourceTargetReacquisitionAllowed": True},
            return_context={"returnNeeded": True, "returnReady": False, "resourceTargetAvailable": False},
            resource_return=resource_return,
            missing_required=["target.candidates", "target.freshness"],
        )
        report = live_qa.build_report(
            args(),
            post_json_func=mock.Mock(return_value=snapshot_response()),
            fetch_json_func=mock.Mock(side_effect=[{"status": "PASS"}, status, {"status": "FAIL", "missingRequiredContextDomains": ["target.candidates", "target.freshness"]}, status["brain"]]),
            processes=[],
        )

        self.assertEqual(report["status"], "WARN")
        self.assertEqual(report["cycle"]["cycleStage"], "return_to_resource")
        self.assertTrue(report["inventoryResource"]["returnDestinationAvailable"])
        self.assertFalse(report["failures"])
        self.assertTrue(any("remembered resource return destination" in warning for warning in report["warnings"]))

    def test_normal_collecting_state_passes(self):
        status = status_for()
        report = live_qa.build_report(
            args(),
            post_json_func=mock.Mock(return_value=snapshot_response()),
            fetch_json_func=mock.Mock(side_effect=[{"status": "PASS"}, status, {"status": "PASS"}, status["brain"]]),
            processes=[],
        )

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["cycle"]["cycleStage"], "collecting_resources")
        self.assertEqual(report["overlay"]["selected"], "Oak tree")

    def test_json_output_contains_schema_and_summaries(self):
        status = status_for()
        report = live_qa.build_report(
            args(),
            post_json_func=mock.Mock(return_value=snapshot_response()),
            fetch_json_func=mock.Mock(side_effect=[{"status": "PASS"}, status, {"status": "PASS"}, status["brain"]]),
            processes=[],
        )

        self.assertEqual(report["schema"], "woodcut_bank_live_qa.v1")
        self.assertIn("endpoint", report)
        self.assertIn("cycle", report)
        self.assertIn("diagnostics", report)
        self.assertIn("historyTail", report)

    def test_cli_json_unreachable_writes_no_files(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--snapshot-url",
                    "http://127.0.0.1:1",
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
        self.assertEqual(payload["schema"], "woodcut_bank_live_qa.v1")
        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

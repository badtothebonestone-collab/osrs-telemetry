import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import run_daily_gauntlet as gauntlet


class RunDailyGauntletTest(unittest.TestCase):
    def test_detects_duplicate_live_daemons(self):
        report = gauntlet.detect_process_conflicts(
            [
                {"pid": 1, "commandLine": "python telemetry-viewer/live_core_daemon.py --context-port 8890"},
                {"pid": 2, "commandLine": "python telemetry-viewer/live_core_daemon.py --context-port 8891"},
            ]
        )

        self.assertEqual(report["status"], "WARN")
        self.assertEqual(report["liveCoreDaemonCount"], 2)
        self.assertTrue(any("multiple live_core_daemon.py" in warning for warning in report["warnings"]))

    def test_detects_daemon_and_legacy_processor_conflict(self):
        report = gauntlet.detect_process_conflicts(
            [
                {"pid": 1, "commandLine": "python telemetry-viewer/live_core_daemon.py"},
                {"pid": 2, "commandLine": "python telemetry-viewer/live_target_processor.py --follow"},
            ]
        )

        self.assertEqual(report["status"], "WARN")
        self.assertTrue(any("live_target_processor.py" in warning for warning in report["warnings"]))

    def test_detects_separate_context_service_conflict(self):
        report = gauntlet.detect_process_conflicts(
            [
                {"pid": 1, "commandLine": "python telemetry-viewer/live_core_daemon.py"},
                {"pid": 2, "commandLine": "python telemetry-viewer/context_service.py --port 8890"},
            ]
        )

        self.assertEqual(report["status"], "WARN")
        self.assertTrue(any("context_service.py" in warning for warning in report["warnings"]))

    def test_clean_process_set_passes(self):
        report = gauntlet.detect_process_conflicts(
            [{"pid": 1, "commandLine": "python telemetry-viewer/live_core_daemon.py"}]
        )

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["warnings"], [])

    def test_daily_pass_fixture(self):
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {
                "liveCoreDaemonActive": True,
                "inputSourceActive": "compact-packets",
                "writeDebugLiveFiles": False,
                "brainTaskPolicy": "woodcutting_bank",
                "compactPacketsRecent": True,
                "brainProgress": {
                    "displayedGoalProgress": 4,
                    "goalCount": 5,
                    "matchedSlotDetails": [{"slot": 9, "itemId": 1511, "counted": True}],
                    "currentSnapshotValid": True,
                },
            },
            {"status": "PASS", "bestCandidates": {"tree": {"targetName": "Tree"}}},
            {"goalProgress": {"displayedGoalProgress": 4, "goalCount": 5}, "noActionEmitted": True},
        )

        self.assertEqual(result["failures"], [])
        self.assertEqual(result["warnings"], [])

    def test_warns_when_task_policy_missing_or_unknown(self):
        missing = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {"liveCoreDaemonActive": True, "inputSourceActive": "compact-packets", "compactPacketsRecent": True},
        )
        unknown = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {
                "liveCoreDaemonActive": True,
                "inputSourceActive": "compact-packets",
                "compactPacketsRecent": True,
                "brainTaskPolicy": "mystery_policy",
            },
        )

        self.assertTrue(any("task policy missing" in warning for warning in missing["warnings"]))
        self.assertTrue(any("unknown task policy" in warning for warning in unknown["warnings"]))

    def test_report_includes_active_task_policy(self):
        args = gauntlet.parse_args(["--daily-mode", "compact-packets"])
        brain = {
            "genericTaskState": {"phase": "inventory_full", "activeIntent": "needs_service"},
            "serviceContext": {"serviceNeeded": True},
            "processInventoryContext": {"processRequired": False},
            "navigationIntentContext": {"navigationNeeded": True},
            "goalProgress": {},
            "noActionEmitted": True,
        }
        with mock.patch.object(
            gauntlet,
            "fetch_json",
            side_effect=[
                {"status": "PASS"},
                {"liveCoreDaemonActive": True, "inputSourceActive": "compact-packets", "compactPacketsRecent": True, "brainTaskPolicy": "woodcutting_drop"},
                brain,
            ],
        ):
            with mock.patch.object(gauntlet, "post_json", return_value={"status": "PASS"}):
                report = gauntlet.build_report(args, processes=[])

        self.assertEqual(report["activeTaskPolicy"], "woodcutting_drop")
        self.assertEqual(report["genericPhase"], "inventory_full")
        self.assertEqual(report["activeIntent"], "needs_service")
        self.assertTrue(report["serviceNeeded"])
        self.assertFalse(report["processNeeded"])
        self.assertTrue(report["navigationNeeded"])
        self.assertTrue(report["noActionEmitted"])

    def test_process_inventory_phase_does_not_fail_for_optional_missing_tree_candidates(self):
        brain = {
            "genericTaskState": {"phase": "inventory_full", "activeIntent": "process_inventory"},
            "serviceContext": {"serviceNeeded": False},
            "processInventoryContext": {"processRequired": True, "processTypeNeeded": "firemaking"},
            "navigationIntentContext": {"navigationNeeded": False},
            "requiredContextDomains": ["inventory", "process_inventory"],
            "missingRequiredContextDomains": [],
            "optionalMissingContextDomains": ["target.candidates"],
            "goalProgress": {},
            "noActionEmitted": True,
        }
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {
                "liveCoreDaemonActive": True,
                "inputSourceActive": "plugin-snapshot",
                "dailyMode": "snapshot-no-files",
                "noFileDaily": True,
                "compactPacketFilesRequired": False,
                "compactPacketFilesWriting": False,
                "brainTaskPolicy": "woodcutting_firemake",
                "candidateCount": 0,
                "requiredContextDomains": ["inventory", "process_inventory"],
                "missingRequiredContextDomains": [],
                "optionalMissingContextDomains": ["target.candidates"],
            },
            {
                "status": "FAIL",
                "missingCapabilities": ["target.candidates"],
                "requiredContextDomains": ["inventory", "process_inventory"],
                "missingRequiredContextDomains": [],
                "optionalMissingContextDomains": ["target.candidates"],
            },
            brain,
            daily_mode="snapshot-no-files",
        )

        self.assertFalse(any("daily context endpoint returned FAIL" in failure for failure in result["failures"]))
        self.assertTrue(any("optional" in warning and "context endpoint returned FAIL" in warning for warning in result["warnings"]))

    def test_bank_open_after_banking_complete_defers_missing_target_candidates(self):
        brain = {
            "genericTaskState": {"phase": "waiting_for_world_view", "activeIntent": "close_service_context"},
            "bankOperationContext": {"bankingComplete": True, "completionReason": "no_resource_items_held"},
            "bankUiContext": {"bankOpen": True, "closeButtonAvailable": True, "closeButtonVisible": True},
            "postBankReacquisitionContext": {
                "postBankReacquisitionNeeded": True,
                "bankUiStillOpen": True,
                "resourceTargetReacquisitionAllowed": False,
                "reason": "bank_ui_still_open",
            },
            "closeBankContext": {
                "closeBankNeeded": True,
                "closeBankReady": True,
                "reason": "close_button_available",
            },
            "requiredContextDomains": ["inventory", "bank_operation", "bank_ui", "post_bank_reacquisition", "close_bank"],
            "missingRequiredContextDomains": ["target.candidates", "target.freshness"],
            "optionalMissingContextDomains": [],
            "goalProgress": {},
            "noActionEmitted": True,
        }
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {
                "liveCoreDaemonActive": True,
                "inputSourceActive": "plugin-snapshot",
                "dailyMode": "snapshot-no-files",
                "noFileDaily": True,
                "compactPacketFilesRequired": False,
                "compactPacketFilesWriting": False,
                "brainTaskPolicy": "woodcutting_bank",
                "candidateCount": 0,
                "bankingComplete": True,
                "bankOpen": True,
                "postBankReacquisitionReason": "bank_ui_still_open",
                "closeBankNeeded": True,
                "closeBankReady": True,
                "closeBankReason": "close_button_available",
            },
            {
                "status": "FAIL",
                "missingCapabilities": ["target.candidates"],
                "requiredContextDomains": ["inventory", "bank_operation", "bank_ui", "post_bank_reacquisition", "close_bank"],
                "missingRequiredContextDomains": ["target.candidates", "target.freshness"],
            },
            brain,
            daily_mode="snapshot-no-files",
        )

        self.assertFalse(any("daily context endpoint returned FAIL" in failure for failure in result["failures"]))
        self.assertTrue(any("bank UI is still open" in warning for warning in result["warnings"]))

    def test_close_bank_needed_defers_missing_target_candidates(self):
        self.assertTrue(
            gauntlet.post_bank_target_reacquisition_deferred(
                {
                    "brain": {
                        "bankOperationContext": {"bankingComplete": True},
                        "bankUiContext": {"bankOpen": True},
                        "closeBankContext": {
                            "closeBankNeeded": True,
                            "closeBankReady": True,
                            "reason": "close_button_available",
                        },
                    }
                }
            )
        )

    def test_transition_summary_includes_pathing_context(self):
        brain = {
            "genericTaskState": {"phase": "inventory_full", "activeIntent": "needs_service"},
            "navigationIntentContext": {"navigationNeeded": True},
            "pathingContext": {
                "pathingNeeded": True,
                "localReachability": "reachable",
                "pathLengthTiles": 4,
                "destinationTile": {"worldX": 3208, "worldY": 3219, "plane": 0},
                "nextWaypointTile": {"worldX": 3201, "worldY": 3200, "plane": 0},
            },
            "noActionEmitted": True,
        }

        summary = gauntlet.transition_summary_from({}, brain)

        self.assertTrue(summary["pathingNeeded"])
        self.assertEqual(summary["pathingReachability"], "reachable")
        self.assertEqual(summary["pathingPathLengthTiles"], 4)
        self.assertEqual(summary["pathingDestinationTile"]["worldX"], 3208)
        self.assertEqual(summary["pathingNextWaypointTile"]["worldX"], 3201)

    def test_fails_if_pathing_required_but_context_missing(self):
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {"liveCoreDaemonActive": True, "inputSourceActive": "compact-packets", "compactPacketsRecent": True, "brainTaskPolicy": "woodcutting_bank"},
            {"status": "PASS"},
            {
                "genericTaskState": {"phase": "inventory_full", "activeIntent": "needs_service"},
                "navigationIntentContext": {"navigationNeeded": True},
                "pathingNeeded": True,
                "goalProgress": {},
                "noActionEmitted": True,
            },
        )

        self.assertTrue(any("pathing context" in failure for failure in result["failures"]))

    def test_process_inventory_does_not_fail_when_pathing_not_needed(self):
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {"liveCoreDaemonActive": True, "inputSourceActive": "compact-packets", "compactPacketsRecent": True, "brainTaskPolicy": "woodcutting_firemake"},
            {"status": "PASS"},
            {
                "genericTaskState": {"phase": "inventory_full", "activeIntent": "process_inventory"},
                "processInventoryContext": {"processRequired": True, "processTypeNeeded": "firemaking"},
                "pathingContext": {"pathingNeeded": False, "reason": "not_needed_for_process_inventory"},
                "goalProgress": {},
                "noActionEmitted": True,
            },
        )

        self.assertFalse(any("pathing context" in failure for failure in result["failures"]))

    def test_detects_policy_task_analyzer_runtime_json_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "interaction_geometry" / "live"
            live_dir.mkdir(parents=True)
            (live_dir / "task_state.json").write_text("{}", encoding="utf-8")
            (live_dir / "analyzer_output.json").write_text("{}", encoding="utf-8")
            (live_dir / "policy_history.jsonl").write_text("{}", encoding="utf-8")

            report = gauntlet.runtime_policy_file_report(str(session))

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["unexpectedRuntimeFileCount"], 3)
        self.assertTrue(any("task_state.json" in warning for warning in report["warnings"]))

    def test_detects_invalid_progress_without_retention(self):
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {
                "liveCoreDaemonActive": True,
                "inputSourceActive": "compact-packets",
                "brainProgress": {
                    "currentSnapshotValid": False,
                    "progressRetainedFromPrevious": False,
                    "lastValidProgressTick": 10,
                },
            },
        )

        self.assertTrue(any("invalid inventory snapshot" in failure for failure in result["failures"]))

    def test_daily_requires_compact_packets_input(self):
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {"liveCoreDaemonActive": True, "inputSourceActive": "plugin-snapshot"},
        )

        self.assertTrue(any("compact-packets" in failure for failure in result["failures"]))

    def test_snapshot_no_file_allows_plugin_snapshot_and_no_compact_files(self):
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {
                "liveCoreDaemonActive": True,
                "inputSourceActive": "plugin-snapshot",
                "dailyMode": "snapshot-no-files",
                "noFileDaily": True,
                "compactPacketFilesRequired": False,
                "compactPacketFilesWriting": False,
                "pluginSnapshotHealth": {"status": "PASS"},
            },
            daily_mode="snapshot-no-files",
        )

        self.assertEqual(result["failures"], [])

    def test_snapshot_no_file_fails_if_compact_files_are_required_or_writing(self):
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {
                "liveCoreDaemonActive": True,
                "inputSourceActive": "plugin-snapshot",
                "dailyMode": "snapshot-no-files",
                "noFileDaily": True,
                "compactPacketFilesRequired": True,
                "compactPacketFilesWriting": True,
            },
            daily_mode="snapshot-no-files",
        )

        self.assertTrue(any("compact packet files" in failure for failure in result["failures"]))

    def test_snapshot_no_file_detects_live_packet_growth(self):
        message = gauntlet.live_packet_growth_failure(
            {"count": 1, "bytes": 100},
            {"count": 1, "bytes": 150},
        )

        self.assertEqual(message, "compact packet files are growing in snapshot no-file daily")

    def test_raw_recording_flags_fail_daily(self):
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {
                "liveCoreDaemonActive": True,
                "inputSourceActive": "compact-packets",
                "rawTickRecordingEnabled": True,
                "rawEventRecordingEnabled": True,
                "frameRecordingEnabled": True,
            },
        )

        self.assertTrue(any("raw tick" in failure for failure in result["failures"]))
        self.assertTrue(any("raw event" in failure for failure in result["failures"]))
        self.assertTrue(any("frame recording" in failure for failure in result["failures"]))

    def test_screenshot_crop_and_perception_flags_fail_daily(self):
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {
                "liveCoreDaemonActive": True,
                "inputSourceActive": "compact-packets",
                "captureScreenshots": True,
                "cropCaptureEnabled": "true",
                "perceptionCaptureEnabled": True,
            },
        )

        self.assertTrue(any("screenshot capture" in failure for failure in result["failures"]))
        self.assertTrue(any("crop capture" in failure for failure in result["failures"]))
        self.assertTrue(any("perception capture" in failure for failure in result["failures"]))

    def test_overlay_stale_fails_when_enabled(self):
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {
                "liveCoreDaemonActive": True,
                "inputSourceActive": "compact-packets",
                "overlayStateWritten": True,
                "overlayStateFresh": False,
            },
        )

        self.assertTrue(any("overlay state" in failure for failure in result["failures"]))

    def test_detects_counted_slot_without_item_id(self):
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {
                "liveCoreDaemonActive": True,
                "inputSourceActive": "compact-packets",
                "brainProgress": {
                    "matchedSlotDetails": [{"slot": 9, "itemId": None, "counted": True}],
                    "currentSnapshotValid": True,
                },
            },
        )

        self.assertTrue(any("itemId" in failure for failure in result["failures"]))

    def test_read_only_context_field_names_do_not_fail_gauntlet(self):
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {"liveCoreDaemonActive": True, "inputSourceActive": "compact-packets"},
            {"status": "PASS", "navigation": {"interactionRadiusTiles": 2, "clickbox": {"x": 1}}},
            {"goalProgress": {}, "noActionEmitted": True},
        )

        self.assertEqual(result["failures"], [])


if __name__ == "__main__":
    unittest.main()

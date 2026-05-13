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

    def test_fails_when_brain_does_not_report_no_action_emitted(self):
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {"liveCoreDaemonActive": True, "inputSourceActive": "compact-packets", "compactPacketsRecent": True, "brainTaskPolicy": "woodcutting_bank"},
            {"status": "PASS"},
            {"goalProgress": {}, "noActionEmitted": False},
        )

        self.assertTrue(any("noActionEmitted" in failure for failure in result["failures"]))

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

    def test_detects_action_like_fields(self):
        result = gauntlet.evaluate_daemon_payloads(
            {"status": "PASS"},
            {"liveCoreDaemonActive": True, "inputSourceActive": "compact-packets"},
            {"status": "PASS", "clickTarget": {"x": 1}},
            {"goalProgress": {}, "noActionEmitted": True},
        )

        self.assertTrue(any("action/input/menu" in failure for failure in result["failures"]))


if __name__ == "__main__":
    unittest.main()

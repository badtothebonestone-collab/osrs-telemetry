import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import live_control_panel as panel
import check_live_setup as setup


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class LiveControlPanelHelpersTest(unittest.TestCase):
    def test_latest_session_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "session-a"
            second = root / "session-b"
            write_json(first / "manifest.json", {"active": False})
            write_json(second / "manifest.json", {"active": False})
            old = time.time() - 100
            os.utime(first / "manifest.json", (old, old))
            newest = time.time()
            os.utime(second / "manifest.json", (newest, newest))
            self.assertEqual(panel.latest_session_path(str(root)), second)

    def test_build_live_processor_command_defaults_to_strict_compact_packets(self):
        options = panel.LivePanelOptions(profile="woodcutting", window_ticks=10, limit=100)
        command = panel.build_live_processor_command(options, supports_liveness=True)
        self.assertEqual(command[0], sys.executable)
        self.assertIn("telemetry-viewer\\live_target_processor.py", command)
        self.assertIn("--input-source", command)
        self.assertEqual(command[command.index("--input-source") + 1], "compact-packets")
        self.assertIn("--require-compact-packets", command)
        self.assertIn("--liveness-mode", command)
        self.assertEqual(command[command.index("--profile") + 1], "woodcutting")
        self.assertEqual(command[command.index("--window-ticks") + 1], "10")
        self.assertEqual(command[command.index("--limit") + 1], "100")
        self.assertEqual(command[command.index("--overlay-debug-target-limit") + 1], "10")

    def test_build_live_processor_strict_compact_command(self):
        options = panel.LivePanelOptions(require_compact_packets=True, input_source="auto")
        command = panel.build_live_processor_command(options, supports_liveness=True)
        self.assertEqual(command[command.index("--input-source") + 1], "compact-packets")
        self.assertIn("--require-compact-packets", command)

    def test_compact_stream_label_and_warning(self):
        self.assertIn(panel.COMPACT_STREAM_EXPERIMENTAL_LABEL, panel.INPUT_SOURCES)
        self.assertEqual(panel.normalize_input_source(panel.COMPACT_STREAM_EXPERIMENTAL_LABEL), "compact-stream")
        self.assertIn("experimental", panel.stream_mode_warning(panel.COMPACT_STREAM_EXPERIMENTAL_LABEL).lower())

    def test_config_doctor_command(self):
        command = panel.build_config_doctor_command("daily", fix_suggestions=True, check_processes=True)
        self.assertEqual(command[:2], [sys.executable, "telemetry-viewer\\live_config_doctor.py"])
        self.assertIn("--latest-session", command)
        self.assertEqual(command[command.index("--mode") + 1], "daily")
        self.assertIn("--fix-suggestions", command)
        self.assertIn("--check-processes", command)
        self.assertEqual(panel.doctor_mode_key("Plugin Snapshot Experimental"), "plugin_snapshot_experimental")
        self.assertEqual(panel.doctor_mode_key("Daily Snapshot No-File"), "snapshot_no_file")

    def test_daily_gauntlet_command(self):
        command = panel.build_daily_gauntlet_command(daemon_url="http://127.0.0.1:8890")
        self.assertEqual(command[:2], [sys.executable, "telemetry-viewer\\run_daily_gauntlet.py"])
        self.assertIn("--latest-session", command)
        self.assertIn("--daemon-url", command)
        self.assertEqual(command[command.index("--daemon-url") + 1], "http://127.0.0.1:8890")
        self.assertEqual(command[command.index("--daily-mode") + 1], "snapshot-no-files")
        self.assertIn("--strict", command)
        self.assertIn("--check-processes", command)

    def test_daily_and_advanced_button_labels_are_separated(self):
        self.assertEqual(
            panel.DAILY_ACTION_LABELS,
            (
                "Apply Daily Live Preset",
                "Start RuneLite Dev",
                "Start Daily Live Stable Compact",
                "Start Daily Live Snapshot No-File EXPERIMENTAL",
                "Stop All",
                "Config Doctor",
                "Daily Gauntlet",
                "Open Latest Session Folder",
            ),
        )
        self.assertFalse(any("Legacy" in label for label in panel.DAILY_ACTION_LABELS))
        self.assertTrue(any("Snapshot No-File" in label and "EXPERIMENTAL" in label for label in panel.DAILY_ACTION_LABELS))
        self.assertTrue(any(label.endswith("Legacy Live Processor") for label in panel.ADVANCED_ACTION_LABELS))
        self.assertTrue(any(label.endswith("Legacy Context Service") for label in panel.ADVANCED_ACTION_LABELS))
        self.assertTrue(any(label.endswith("Legacy Human Dashboard") for label in panel.ADVANCED_ACTION_LABELS))
        self.assertTrue(any("plugin-snapshot" in label.lower() and "EXPERIMENTAL" in label for label in panel.ADVANCED_ACTION_LABELS))
        self.assertTrue(any("compact-stream" in label.lower() and "EXPERIMENTAL" in label for label in panel.ADVANCED_ACTION_LABELS))
        self.assertTrue(any("Debug Audit" in label for label in panel.ADVANCED_ACTION_LABELS))
        self.assertTrue(any("Inspector" in label for label in panel.ADVANCED_ACTION_LABELS))
        self.assertTrue(any("Batch Builders" in label for label in panel.ADVANCED_ACTION_LABELS))

    def test_runtime_control_payload_is_safe_and_policy_driven(self):
        payload = panel.build_runtime_control_payload(
            task_policy="woodcutting_firemake",
            goal_count="5",
            observe_only=False,
            reset_brain_state=True,
            overlay_mode="intent",
            overlay_backup_candidates=2,
        )

        self.assertEqual(payload["taskPolicy"], "woodcutting_firemake")
        self.assertEqual(payload["goalCount"], 5)
        self.assertFalse(payload["observeOnly"])
        self.assertTrue(payload["resetBrainState"])
        self.assertEqual(payload["overlayMode"], "intent")
        for forbidden in ("click", "mouse", "keyboard", "menu", "invoke", "execute", "walk", "interact"):
            self.assertFalse(any(forbidden in key.lower() for key in payload))

    def test_mission_preset_payload_is_safe(self):
        payload = panel.build_mission_preset_payload("woodcut_firemake", goal_count="5", reset_brain_state=True)

        self.assertEqual(payload["missionPreset"], "woodcut_firemake")
        self.assertEqual(payload["goalCount"], 5)
        self.assertTrue(payload["resetBrainState"])
        self.assertTrue(payload["brainEnabled"])
        self.assertEqual(payload["overlayMode"], "intent")
        self.assertEqual(payload["overlayBackupCandidates"], 2)
        for forbidden in ("click", "mouse", "keyboard", "menu", "invoke", "execute", "walk", "interact"):
            self.assertFalse(any(forbidden in key.lower() for key in payload))

    def test_runtime_control_endpoint_url_uses_daemon_port(self):
        self.assertEqual(panel.runtime_control_endpoint_url(8890), "http://127.0.0.1:8890/control")

    def test_mission_status_parses_health_status_control_payloads(self):
        mission = panel.build_mission_control_status(
            health={"liveCoreDaemonActive": True, "overlayStateWritten": True},
            status={
                "dailyMode": "snapshot-no-files",
                "inputSourceActive": "plugin-snapshot",
                "noFileDaily": True,
                "brain": {
                    "task": "woodcutting",
                    "genericTaskState": {"phase": "inventory_full", "activeIntent": "process_inventory"},
                    "goalProgress": {"displayedGoalProgress": 3, "goalCount": 5},
                    "currentContextSummary": {"inventory": {"inventoryFull": True}},
                    "serviceContext": {"serviceNeeded": False},
                    "processInventoryContext": {"processRequired": True},
                    "navigationIntentContext": {"navigationNeeded": False},
                    "warnings": ["no tree candidates currently observed"],
                    "noActionEmitted": True,
                },
                "stabilizedIntentTargetLabel": "none",
            },
            control={
                "state": {
                    "activeTask": "woodcutting",
                    "taskPolicy": "woodcutting_firemake",
                    "activeMissionPreset": "woodcut_firemake",
                    "goalCount": 5,
                }
            },
        )

        self.assertEqual(mission["daemonHealth"], "PASS")
        self.assertEqual(mission["dailyMode"], "snapshot-no-files")
        self.assertEqual(mission["inputSource"], "plugin-snapshot")
        self.assertEqual(mission["activeTask"], "woodcutting")
        self.assertEqual(mission["taskPolicy"], "woodcutting_firemake")
        self.assertEqual(mission["activeMissionPreset"], "woodcut_firemake")
        self.assertEqual(mission["genericPhase"], "inventory_full")
        self.assertEqual(mission["activeIntent"], "process_inventory")
        self.assertEqual(mission["progress"], "3/5")
        self.assertEqual(mission["inventoryFull"], "yes")
        self.assertEqual(mission["processNeeded"], "yes")
        self.assertEqual(mission["actionSafety"], "PASS")
        self.assertEqual(mission["latestWarningCount"], 1)

    def test_mission_status_handles_daemon_unavailable(self):
        mission = panel.build_mission_control_status(health=None, status=None, control=None, error="connection refused")

        self.assertEqual(mission["daemonHealth"], "FAIL")
        self.assertEqual(mission["daemonStatus"], "daemon not reachable")
        self.assertIn("start Snapshot No-File", mission["suggestedNextStep"])

    def test_quick_policy_payloads_are_safe(self):
        cases = {
            "bank": "woodcut_bank",
            "firemake": "woodcut_firemake",
            "drop": "woodcut_drop",
            "combat": "combat_default",
            "observe": "observe_only",
        }
        for quick_name, preset_name in cases.items():
            with self.subTest(quick_name=quick_name):
                payload = panel.build_quick_policy_payload(quick_name, goal_count="5")
                self.assertEqual(payload["missionPreset"], preset_name)
                self.assertEqual(payload["goalCount"], 5)
                self.assertNotIn("resetBrainState", payload)
                for forbidden in ("click", "mouse", "keyboard", "menu", "invoke", "execute", "walk", "interact"):
                    self.assertFalse(any(forbidden in key.lower() for key in payload))

    def test_reset_baseline_payload_is_reset_only(self):
        self.assertEqual(panel.build_reset_baseline_payload(), {"resetBrainState": True})

    def test_preset_request_body_and_endpoint_url(self):
        self.assertEqual(panel.preset_request_body("DAILY_LIVE")["preset"], "DAILY_LIVE")
        self.assertEqual(panel.preset_request_body("DAILY_SNAPSHOT_NO_FILE")["preset"], "DAILY_SNAPSHOT_NO_FILE")
        self.assertEqual(panel.preset_endpoint_url("/presets"), "http://127.0.0.1:8893/presets")

    def test_plugin_snapshot_preset_command_is_experimental(self):
        options = panel.LivePanelOptions(input_source="plugin-snapshot", require_compact_packets=False)
        command = panel.build_live_processor_command(options, supports_liveness=True)
        self.assertEqual(command[command.index("--input-source") + 1], "plugin-snapshot")
        self.assertIn("--plugin-snapshot-tier", command)
        self.assertEqual(command[command.index("--plugin-snapshot-tier") + 1], "hot")
        self.assertIn("--plugin-snapshot-fallback", command)
        self.assertIn("experimental", panel.stream_mode_warning(panel.PLUGIN_SNAPSHOT_EXPERIMENTAL_LABEL).lower())

    def test_daily_preset_command_remains_compact_packets(self):
        options = panel.normal_live_options("woodcutting")
        command = panel.build_live_processor_command(options, supports_liveness=True)
        self.assertEqual(command[command.index("--input-source") + 1], "compact-packets")
        self.assertIn("--require-compact-packets", command)
        self.assertNotIn("--plugin-snapshot-tier", command)

    def test_live_processor_command_can_omit_liveness_flags(self):
        options = panel.LivePanelOptions()
        command = panel.build_live_processor_command(options, supports_liveness=False)
        self.assertNotIn("--liveness-mode", command)
        self.assertNotIn("--liveness-budget-ms", command)

    def test_context_service_and_dashboard_commands(self):
        service_command = panel.build_context_service_command(8890)
        dashboard_command = panel.build_dashboard_command(1)
        self.assertEqual(service_command[:2], [sys.executable, "telemetry-viewer\\context_service.py"])
        self.assertIn("--port", service_command)
        self.assertEqual(service_command[service_command.index("--port") + 1], "8890")
        self.assertEqual(dashboard_command[:2], [sys.executable, "telemetry-viewer\\live_context_query.py"])
        self.assertIn("--watch-human", dashboard_command)
        self.assertIn("--events", dashboard_command)

    def test_live_core_daemon_command_uses_streamlined_daily_defaults(self):
        command = panel.build_live_core_daemon_command(panel.normal_live_options("woodcutting"))
        self.assertEqual(command[:2], [sys.executable, "telemetry-viewer\\live_core_daemon.py"])
        self.assertIn("--daily-mode", command)
        self.assertEqual(command[command.index("--daily-mode") + 1], "compact-packets")
        self.assertIn("--input-source", command)
        self.assertEqual(command[command.index("--input-source") + 1], "compact-packets")
        self.assertNotIn("telemetry-viewer\\live_target_processor.py", command)
        self.assertNotIn("plugin-snapshot", command)
        self.assertNotIn("compact-stream", command)
        self.assertNotIn("--write-debug-live-files", command)
        self.assertIn("--write-overlay-state", command)
        self.assertIn("--overlay-mode", command)
        self.assertEqual(command[command.index("--overlay-mode") + 1], "intent")
        self.assertIn("--overlay-backup-candidates", command)
        self.assertEqual(command[command.index("--overlay-backup-candidates") + 1], "2")
        self.assertIn("--overlay-debug-target-limit", command)
        self.assertEqual(command[command.index("--overlay-debug-target-limit") + 1], "10")
        self.assertIn("--human-dashboard", command)
        self.assertIn("--brain-task", command)
        self.assertEqual(command[command.index("--brain-task") + 1], "woodcutting")
        self.assertIn("--goal-count", command)
        self.assertEqual(command[command.index("--goal-count") + 1], "5")
        self.assertNotIn("--poll-interval", command)
        self.assertNotIn("compact-stream", command)

    def test_snapshot_no_file_daemon_command_uses_plugin_snapshot(self):
        command = panel.build_live_core_daemon_command(panel.snapshot_no_file_options("woodcutting"))
        self.assertEqual(command[:2], [sys.executable, "telemetry-viewer\\live_core_daemon.py"])
        self.assertIn("--daily-mode", command)
        self.assertEqual(command[command.index("--daily-mode") + 1], "snapshot-no-files")
        self.assertEqual(command[command.index("--input-source") + 1], "plugin-snapshot")
        self.assertIn("--plugin-snapshot-tier", command)
        self.assertEqual(command[command.index("--plugin-snapshot-tier") + 1], "hot")
        self.assertNotIn("--plugin-snapshot-fallback", command)
        self.assertNotIn("--write-debug-live-files", command)
        self.assertNotIn("--require-compact-packets", command)

    def test_live_core_daemon_overlay_state_is_optional(self):
        options = panel.normal_live_options("woodcutting")
        options.write_overlay_state = False
        command = panel.build_live_core_daemon_command(options)
        self.assertNotIn("--write-overlay-state", command)
        self.assertNotIn("--overlay-debug-target-limit", command)

    def test_stream_missing_projection_warning_logic(self):
        warning = panel.stream_incomplete_warning(
            {
                "inputSourceActive": "compact-stream",
                "candidateCount": 0,
                "compactStreamMissingRequiredTypesForLatestTick": ["live_projection_packet.v1"],
            }
        )
        self.assertEqual(warning, "Stream incomplete. Switch to compact-packets.")
        self.assertEqual(panel.stream_incomplete_warning({"inputSourceActive": "compact-packets", "candidateCount": 0}), "")

    def test_normal_live_stack_commands_are_strict_compact(self):
        options = panel.normal_live_options("woodcutting")
        stack = panel.build_normal_live_stack_commands(options, supports_liveness=True)
        names = [name for name, _command, _log_name in stack]
        self.assertEqual(names, ["Check Live Setup", "Live Processor", "Context Service", "Human Dashboard"])
        live_command = stack[1][1]
        self.assertEqual(live_command[live_command.index("--input-source") + 1], "compact-packets")
        self.assertIn("--require-compact-packets", live_command)
        self.assertIn("--liveness-mode", live_command)

    def test_mock_brain_and_debug_audit_commands(self):
        mock = panel.build_mock_brain_command(goal_count=5, watch=True, interval=1)
        audit = panel.build_debug_audit_command("broad_qa")
        self.assertEqual(mock[:2], [sys.executable, "telemetry-viewer\\mock_brain_rehearsal.py"])
        self.assertIn("--watch", mock)
        self.assertIn("--goal-count", mock)
        self.assertEqual(mock[mock.index("--goal-count") + 1], "5")
        self.assertEqual(audit[:2], [sys.executable, "telemetry-viewer\\run_target_geometry_pipeline.py"])
        self.assertIn("--latest-with-frames", audit)

    def test_event_timeline_commands(self):
        dashboard_events = panel.build_dashboard_events_command(1.5)
        timeline = panel.build_event_timeline_command(20)
        self.assertIn("--watch-human", dashboard_events)
        self.assertIn("--events", dashboard_events)
        self.assertEqual(dashboard_events[dashboard_events.index("--events") + 1], "10")
        self.assertIn("--events-only", timeline)
        self.assertEqual(timeline[timeline.index("--events") + 1], "20")

    def test_safe_load_json_keeps_previous_on_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            previous = {"latestTick": 5}
            self.assertEqual(panel.safe_load_json(path, previous), previous)
            path.write_text("{not-json", encoding="utf-8")
            self.assertEqual(panel.safe_load_json(path, previous), previous)
            write_json(path, {"latestTick": 9})
            self.assertEqual(panel.safe_load_json(path, previous)["latestTick"], 9)

    def test_status_snapshot_reads_live_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            write_json(
                session / "interaction_geometry" / "live" / "live_status.json",
                {
                    "latestTickProcessed": 12,
                    "inputSourceActive": "compact-packets",
                    "candidateCount": 4,
                    "budgetExceeded": False,
                    "writeFailureCount": 0,
                    "compactPacketsAvailable": True,
                    "compactPacketLatestSegment": str(session / "live_packets" / "live-000001.ndjson"),
                },
            )
            write_json(session / "live_packets" / "live_packet_index.json", {"latestTick": 12, "activeSegment": "live-000001.ndjson"})
            (session / "live_packets" / "latest_segment.txt").write_text("live-000001.ndjson", encoding="utf-8")
            (session / "live_packets" / "live-000001.ndjson").write_text('{"packetType":"live_baseline_packet.v1"}\n', encoding="utf-8")
            write_json(
                session / "manifest.json",
                {
                    "recordingMode": "LIVE_COMPACT_ONLY",
                    "rawTickRecordingEnabled": False,
                    "frameRecordingEnabled": False,
                },
            )
            write_json(
                session / "interaction_geometry" / "live" / "overlay_debug_state.json",
                {"latestEventSummary": "Inventory changed: +1 item 1511", "latestEventTick": 13},
            )
            snapshot = panel.status_snapshot(session)
            self.assertEqual(snapshot["latestTick"], 12)
            self.assertEqual(snapshot["inputSourceActive"], "compact-packets")
            self.assertEqual(snapshot["candidateCount"], 4)
            self.assertTrue(snapshot["compactPacketsAvailable"])
            self.assertEqual(snapshot["recordingMode"], "LIVE_COMPACT_ONLY")
            self.assertFalse(snapshot["rawTickRecordingEnabled"])
            self.assertEqual(snapshot["latestEventSummary"], "Inventory changed: +1 item 1511")
            self.assertEqual(snapshot["latestEventTick"], 13)

    def test_status_snapshot_reports_incomplete_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            write_json(
                session / "interaction_geometry" / "live" / "live_status.json",
                {
                    "latestTickProcessed": 12,
                    "inputSourceActive": "compact-stream",
                    "candidateCount": 0,
                    "compactStreamMissingRequiredTypesForLatestTick": ["live_baseline_packet.v1", "live_projection_packet.v1"],
                },
            )
            snapshot = panel.status_snapshot(session)
            self.assertEqual(snapshot["streamIncompleteWarning"], "Stream incomplete. Switch to compact-packets.")

    def test_status_snapshot_warns_when_stream_file_mirror_is_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            write_json(
                session / "manifest.json",
                {
                    "recordingMode": "LIVE_COMPACT_ONLY",
                    "compactPacketRecordingEnabled": True,
                    "compactLiveStreamEnabled": True,
                    "compactLiveStreamAlsoWriteFiles": False,
                    "compactLivePacketFilesEnabled": False,
                },
            )
            snapshot = panel.status_snapshot(session)
            self.assertFalse(snapshot["compactPacketsAvailable"])
            self.assertIn("Stream also writes files", snapshot["compactFileBridgeWarning"])
            self.assertEqual(snapshot["compactChecklist"]["Emit compact live packets"], "yes")
            self.assertEqual(snapshot["compactChecklist"]["Stream also writes files"], "no")
            self.assertEqual(snapshot["compactChecklist"]["Latest segment exists"], "no")

    def test_check_live_setup_reports_missing_segment_with_stream_mirror_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            session.mkdir(parents=True, exist_ok=True)
            write_json(
                session / "manifest.json",
                {
                    "recordingMode": "LIVE_COMPACT_ONLY",
                    "compactPacketRecordingEnabled": True,
                    "compactLiveStreamEnabled": True,
                    "compactLiveStreamAlsoWriteFiles": False,
                    "compactLivePacketFilesEnabled": False,
                },
            )
            payload = setup.check_live_setup(session, require_compact_packets=True)
            text = " ".join(
                list(payload.get("warnings") or [])
                + list(payload.get("failures") or [])
                + [str(check.get("message")) for check in payload.get("checks") or []]
            )
            self.assertIn("Stream also writes files", text)
            self.assertIn("compact-stream experimental", text)
            self.assertEqual(payload["compactLiveStreamEnabled"], True)
            self.assertEqual(payload["compactLiveStreamAlsoWriteFiles"], False)

    def test_compact_packet_status_and_stale_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            segment = session / "live_packets" / "live-000001.ndjson"
            write_json(
                session / "live_packets" / "live_packet_index.json",
                {"latestTick": 42, "latestSequence": 99, "activeSegment": "live-000001.ndjson"},
            )
            (session / "live_packets" / "latest_segment.txt").write_text("live-000001.ndjson", encoding="utf-8")
            segment.write_text('{"packetType":"live_baseline_packet.v1"}\n', encoding="utf-8")
            now = time.time()
            for path in (session, session / "live_packets" / "live_packet_index.json", session / "live_packets" / "latest_segment.txt", segment):
                os.utime(path, (now, now))
            status = panel.compact_packet_status(session, now=now)
            self.assertTrue(status["available"])
            self.assertTrue(status["recent"])
            self.assertEqual(status["latestTick"], 42)
            self.assertEqual(panel.stale_session_warning(session, now=now), "")
            stale_now = now + panel.COMPACT_PACKET_STALE_SECONDS + 10
            self.assertIn("stale", panel.stale_session_warning(session, now=stale_now).lower())

    def test_context_request_body(self):
        body = panel.build_context_request_body(max_candidates=2)
        self.assertEqual(body["schema"], "context_request.v1")
        self.assertEqual(body["task"], "woodcutting")
        self.assertEqual(body["maxCandidates"], 2)
        self.assertEqual(body["maxEvents"], 5)
        self.assertIn("best:tree", body["needs"])
        self.assertIn("events", body["needs"])
        self.assertIn("navigation_readiness", body["needs"])

    def test_tool_registry_json_is_valid(self):
        registry_path = VIEWER_DIR / "tool_registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        self.assertEqual(registry["schema"], "osrs_telemetry_tool_registry.v1")
        tools = registry["tools"]
        self.assertTrue(any(tool["name"] == "live_control_panel.py" for tool in tools))
        self.assertTrue(any(tool["name"] == "live_core_daemon.py" and tool["category"] == "daily" for tool in tools))
        self.assertTrue(any(tool["name"] == "live_target_processor.py" and tool["category"] == "legacy_file_pipeline" for tool in tools))
        self.assertTrue(any(tool["category"] == "experimental" and "EXPERIMENTAL" in tool["purpose"] for tool in tools))
        for tool in tools:
            self.assertIn("dailyRequired", tool)
            self.assertIn("normalCommand", tool)
            self.assertIn("safeToHideInUi", tool)


if __name__ == "__main__":
    unittest.main()

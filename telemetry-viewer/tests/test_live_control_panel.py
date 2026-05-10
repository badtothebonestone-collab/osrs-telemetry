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

    def test_build_live_processor_command_defaults_to_auto(self):
        options = panel.LivePanelOptions(profile="woodcutting", input_source="auto", window_ticks=10, limit=100)
        command = panel.build_live_processor_command(options, supports_liveness=True)
        self.assertEqual(command[0], sys.executable)
        self.assertIn("telemetry-viewer\\live_target_processor.py", command)
        self.assertIn("--input-source", command)
        self.assertEqual(command[command.index("--input-source") + 1], "auto")
        self.assertIn("--liveness-mode", command)
        self.assertEqual(command[command.index("--profile") + 1], "woodcutting")
        self.assertEqual(command[command.index("--window-ticks") + 1], "10")
        self.assertEqual(command[command.index("--limit") + 1], "100")

    def test_build_live_processor_strict_compact_command(self):
        options = panel.LivePanelOptions(require_compact_packets=True, input_source="auto")
        command = panel.build_live_processor_command(options, supports_liveness=True)
        self.assertEqual(command[command.index("--input-source") + 1], "compact-packets")
        self.assertIn("--require-compact-packets", command)

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
        self.assertTrue(any(tool["category"] == "debug_audit" for tool in tools))


if __name__ == "__main__":
    unittest.main()

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
            snapshot = panel.status_snapshot(session)
            self.assertEqual(snapshot["latestTick"], 12)
            self.assertEqual(snapshot["inputSourceActive"], "compact-packets")
            self.assertEqual(snapshot["candidateCount"], 4)
            self.assertTrue(snapshot["compactPacketsAvailable"])

    def test_context_request_body(self):
        body = panel.build_context_request_body(max_candidates=2)
        self.assertEqual(body["schema"], "context_request.v1")
        self.assertEqual(body["task"], "woodcutting")
        self.assertEqual(body["maxCandidates"], 2)
        self.assertIn("best:tree", body["needs"])
        self.assertIn("navigation_readiness", body["needs"])


if __name__ == "__main__":
    unittest.main()

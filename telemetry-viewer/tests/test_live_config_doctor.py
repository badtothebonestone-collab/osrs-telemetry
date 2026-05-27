import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import live_config_doctor as doctor


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_session(
    root: Path,
    *,
    input_source: str = "plugin-snapshot",
    recording_mode: str = "LIVE_COMPACT_ONLY",
    raw_ticks: bool = False,
    raw_events: bool = False,
    frames: bool = False,
    screenshots: bool = False,
    crops: bool = False,
    perception: bool = False,
    window_ticks: int = 10,
    candidate_output_window: str = "latest",
    liveness_mode: str = "delta",
    overlay_target_limit: int = 10,
    budget_exceeded: bool = False,
    write_failures: int = 0,
    compact_stream_enabled: bool = False,
    compact_packets: bool = False,
    compact_live_packet_files_enabled: bool | None = None,
    plugin_tier: str | None = None,
    plugin_field_mode: str | None = None,
    plugin_refs: int | None = None,
) -> Path:
    session = root / "session"
    live_dir = session / "interaction_geometry" / "live"
    packet_dir = session / "live_packets"
    write_json(
        session / "manifest.json",
        {
            "recordingMode": recording_mode,
            "rawTickRecordingEnabled": raw_ticks,
            "rawEventRecordingEnabled": raw_events,
            "frameRecordingEnabled": frames,
            "captureScreenshots": screenshots,
            "cropCaptureEnabled": crops,
            "perceptionCaptureEnabled": perception,
            "compactLiveStreamEnabled": compact_stream_enabled,
            "compactLivePacketFilesEnabled": compact_packets if compact_live_packet_files_enabled is None else compact_live_packet_files_enabled,
            "compactPacketRecordingEnabled": True,
        },
    )
    status = {
        "schema": "live_status.v1",
        "inputSourceActive": input_source,
        "recordingMode": recording_mode,
        "rawTickRecordingEnabled": raw_ticks,
        "rawEventRecordingEnabled": raw_events,
        "frameRecordingEnabled": frames,
        "captureScreenshots": screenshots,
        "cropCaptureEnabled": crops,
        "perceptionCaptureEnabled": perception,
        "windowTicks": window_ticks,
        "candidateOutputWindow": candidate_output_window,
        "livenessMode": liveness_mode,
        "budgetExceeded": budget_exceeded,
        "writeFailureCount": write_failures,
        "latestTickProcessed": 10,
        "compactLiveStreamEnabled": compact_stream_enabled,
        "compactLivePacketFilesEnabled": compact_packets if compact_live_packet_files_enabled is None else compact_live_packet_files_enabled,
    }
    if plugin_tier is not None:
        status["pluginSnapshotTier"] = plugin_tier
    if plugin_field_mode is not None:
        status["pluginSnapshotProjectionFieldMode"] = plugin_field_mode
    if plugin_refs is not None:
        status["pluginSnapshotMaxProjectionRefs"] = plugin_refs
    write_json(live_dir / "live_status.json", status)
    write_json(live_dir / "live_performance_summary.json", {"schema": "live_performance_summary.v1"})
    write_json(live_dir / "live_context_index.json", {"schema": "live_context_index.v1", "latestTick": 10})
    write_json(
        live_dir / "overlay_debug_state.json",
        {
            "schema": "telemetry_overlay_debug_state.v1",
            "latestTick": 10,
            "summary": {"targetLimit": overlay_target_limit},
            "collisionWindow": {"available": True},
            "targets": [{"rank": 1}],
        },
    )
    if compact_packets:
        segment = packet_dir / "live-000001.ndjson"
        segment.parent.mkdir(parents=True, exist_ok=True)
        segment.write_text('{"packetType":"live_baseline_packet.v1","tick":10}\n', encoding="utf-8")
        (packet_dir / "latest_segment.txt").write_text("live-000001.ndjson", encoding="utf-8")
        write_json(packet_dir / "live_packet_index.json", {"latestTick": 10, "latestSequence": 20, "activeSegment": "live-000001.ndjson"})
    now = time.time()
    for path in (
        session,
        session / "manifest.json",
        live_dir / "live_status.json",
        live_dir / "live_context_index.json",
        live_dir / "overlay_debug_state.json",
        *(
            (
                packet_dir / "live_packet_index.json",
                packet_dir / "latest_segment.txt",
                packet_dir / "live-000001.ndjson",
            )
            if compact_packets
            else ()
        ),
    ):
        os.utime(path, (now, now))
    return session


def evaluate(session: Path, mode: str = "daily") -> dict:
    with mock.patch.object(doctor, "context_service_health", return_value={"available": False}):
        with mock.patch.object(doctor, "plugin_snapshot_health", return_value={"available": False}):
            return doctor.evaluate_live_config(session, mode=mode, now=time.time())


class LiveConfigDoctorTest(unittest.TestCase):
    def test_daily_mode_passes_with_plugin_snapshot_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate(make_session(Path(tmp)), "daily")

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["summary"]["inputSourceActive"], "plugin-snapshot")
        self.assertTrue(report["summary"]["livePacketsRuntimeRemoved"])

    def test_daily_mode_warns_with_legacy_packet_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate(make_session(Path(tmp), input_source="compact-packets", plugin_tier="hot"), "daily")

        self.assertEqual(report["status"], "WARN")
        self.assertIn("daily_input_source", [issue["code"] for issue in report["issues"]])

    def test_daily_mode_accepts_live_core_daemon_memory_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), input_source="plugin-snapshot", plugin_tier="hot")
            daemon_health = {
                "available": True,
                "status": "ok",
                "latestTick": 10,
                "service": "live_core_daemon",
                "liveCoreDaemonActive": True,
                "inputSourceActive": "plugin-snapshot",
                "writeDebugLiveFiles": False,
                "overlayStateWritten": True,
                "candidateCount": 4,
                "health": {
                    "service": "live_core_daemon",
                    "liveCoreDaemonActive": True,
                    "inputSourceActive": "plugin-snapshot",
                    "writeDebugLiveFiles": False,
                    "overlayStateWritten": True,
                    "latestTick": 10,
                },
            }
            with mock.patch.object(doctor, "context_service_health", return_value=daemon_health):
                with mock.patch.object(doctor, "plugin_snapshot_health", return_value={"available": False}):
                    report = doctor.evaluate_live_config(session, mode="daily", now=time.time())

        self.assertEqual(report["summary"]["liveCoreDaemonActive"], True)
        self.assertNotIn("daily_plugin_snapshot_input", [issue["code"] for issue in report["issues"]])

    def test_daily_mode_warns_when_daemon_writes_debug_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            daemon_health = {
                "available": True,
                "status": "ok",
                "latestTick": 10,
                "service": "live_core_daemon",
                "liveCoreDaemonActive": True,
                "inputSourceActive": "plugin-snapshot",
                "writeDebugLiveFiles": True,
                "overlayStateWritten": True,
                "candidateCount": 4,
                "health": {
                    "service": "live_core_daemon",
                    "liveCoreDaemonActive": True,
                    "inputSourceActive": "plugin-snapshot",
                    "writeDebugLiveFiles": True,
                    "overlayStateWritten": True,
                    "latestTick": 10,
                },
            }
            with mock.patch.object(doctor, "context_service_health", return_value=daemon_health):
                with mock.patch.object(doctor, "plugin_snapshot_health", return_value={"available": False}):
                    report = doctor.evaluate_live_config(session, mode="daily", now=time.time())

        self.assertIn("daily_daemon_debug_writes", [issue["code"] for issue in report["issues"]])

    def test_daily_mode_warns_with_compact_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate(make_session(Path(tmp), input_source="compact-stream", compact_stream_enabled=True), "daily")

        codes = [issue["code"] for issue in report["issues"]]
        self.assertEqual(report["status"], "WARN")
        self.assertIn("daily_input_source", codes)
        self.assertIn("daily_compact_stream", codes)
        self.assertEqual(report["presetRecommended"], "DAILY_SNAPSHOT_NO_FILE")
        self.assertIn("Click Apply Daily Snapshot No-File Preset.", report["fixSuggestions"])

    def test_daily_mode_warns_with_large_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate(make_session(Path(tmp), window_ticks=100), "daily")

        self.assertIn("daily_window_ticks", [issue["code"] for issue in report["issues"]])

    def test_daily_mode_warns_with_large_overlay_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate(make_session(Path(tmp), overlay_target_limit=50), "daily")

        self.assertIn("daily_overlay_limit", [issue["code"] for issue in report["issues"]])

    def test_daily_mode_warns_with_screenshot_crop_or_perception_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate(make_session(Path(tmp), screenshots=True, crops=True, perception=True), "daily")

        codes = [issue["code"] for issue in report["issues"]]
        self.assertIn("daily_screenshotsEnabled", codes)
        self.assertIn("daily_cropCaptureEnabled", codes)
        self.assertIn("daily_perceptionCaptureEnabled", codes)

    def test_debug_audit_mode_allows_raw_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate(
                make_session(Path(tmp), recording_mode="DEBUG_RECORDING", raw_ticks=True, raw_events=True, frames=True),
                "debug_audit",
            )

        codes = [issue["code"] for issue in report["issues"]]
        self.assertIn("debug_audit_disk_growth", codes)
        self.assertFalse(any(code.startswith("daily_raw") for code in codes))

    def test_plugin_snapshot_mode_checks_endpoint_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(
                Path(tmp),
                input_source="plugin-snapshot",
                plugin_tier="hot",
                plugin_field_mode="compact",
                plugin_refs=100,
            )
            with mock.patch.object(doctor, "context_service_health", return_value={"available": False}):
                with mock.patch.object(
                    doctor,
                    "plugin_snapshot_health",
                    return_value={"available": True, "status": "PASS", "latestTick": 10, "cachedPacketTypes": []},
                ):
                    report = doctor.evaluate_live_config(session, mode="plugin_snapshot_experimental", now=time.time())

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["pluginSnapshot"]["status"], "PASS")

    def test_snapshot_no_file_mode_passes_without_compact_packet_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(
                Path(tmp),
                input_source="plugin-snapshot",
                compact_packets=False,
                compact_live_packet_files_enabled=False,
                plugin_tier="hot",
                plugin_field_mode="compact",
                plugin_refs=100,
            )
            daemon_health = {
                "available": True,
                "status": "ok",
                "service": "live_core_daemon",
                "liveCoreDaemonActive": True,
                "inputSourceActive": "plugin-snapshot",
                "dailyMode": "snapshot-no-files",
                "noFileDaily": True,
                "compactPacketFilesRequired": False,
                "compactPacketFilesWriting": False,
                "writeDebugLiveFiles": False,
                "candidateCount": 4,
                "health": {
                    "service": "live_core_daemon",
                    "liveCoreDaemonActive": True,
                    "inputSourceActive": "plugin-snapshot",
                    "dailyMode": "snapshot-no-files",
                    "noFileDaily": True,
                    "compactPacketFilesRequired": False,
                    "compactPacketFilesWriting": False,
                    "latestTick": 10,
                },
            }
            with mock.patch.object(doctor, "context_service_health", return_value=daemon_health):
                with mock.patch.object(
                    doctor,
                    "plugin_snapshot_health",
                    return_value={"available": True, "status": "PASS", "latestTick": 10},
                ):
                    report = doctor.evaluate_live_config(session, mode="snapshot_no_file", now=time.time())

        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["summary"]["compactPacketFilesRequired"])
        self.assertFalse(report["summary"]["compactPacketFilesWriting"])
        self.assertEqual(report["summary"]["inputSourceActive"], "plugin-snapshot")

    def test_snapshot_no_file_mode_warns_if_legacy_packet_files_remain(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(
                Path(tmp),
                input_source="plugin-snapshot",
                compact_packets=True,
                compact_live_packet_files_enabled=True,
                plugin_tier="hot",
                plugin_field_mode="compact",
                plugin_refs=100,
            )
            with mock.patch.object(doctor, "context_service_health", return_value={"available": False}):
                with mock.patch.object(
                    doctor,
                    "plugin_snapshot_health",
                    return_value={"available": True, "status": "PASS", "latestTick": 10},
                ):
                    report = doctor.evaluate_live_config(session, mode="snapshot_no_file", now=time.time())

        self.assertIn("legacy_live_packets_present", [issue["code"] for issue in report["issues"]])

    def test_json_output_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            output = io.StringIO()
            with mock.patch.object(doctor, "context_service_health", return_value={"available": False}):
                with redirect_stdout(output):
                    code = doctor.main(["--session", str(session), "--mode", "daily", "--json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["schema"], "live_config_doctor.v1")
        self.assertEqual(payload["status"], "PASS")


if __name__ == "__main__":
    unittest.main()

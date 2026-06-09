import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import input_trace_joiner
import input_trace_recorder
import analyze_manual_recording
import manual_recorder
import telemetry_ui


class FakeInputBackend:
    name = "fake"

    def __init__(self, events):
        self.events = events
        self.started = False
        self.stopped = False

    def start(self, callback):
        self.started = True
        for event in self.events:
            callback(dict(event))

    def stop(self):
        self.stopped = True


class FakePollingApi:
    name = "fake_polling_api"

    def __init__(self, samples):
        self.samples = samples
        self.index = -1
        self.current = samples[0] if samples else {"screen_x": 0, "screen_y": 0}

    def cursor_position(self):
        self.index = min(self.index + 1, len(self.samples) - 1)
        self.current = self.samples[self.index]
        return {"screen_x": self.current.get("screen_x", 0), "screen_y": self.current.get("screen_y", 0)}

    def button_down(self, vk_code):
        buttons = self.current.get("buttons") or {}
        return bool(buttons.get(vk_code) or buttons.get(input_trace_recorder.key_name(vk_code)) or buttons.get("left"))

    def key_down(self, vk_code):
        keys = self.current.get("keys") or {}
        return bool(keys.get(vk_code) or keys.get(input_trace_recorder.key_name(vk_code)))

    def modifier_state(self):
        return {"shift": False, "ctrl": False, "alt": False}


class FakeRecorder:
    def __init__(self, *args, **kwargs):
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        return {"schema": "input_trace_summary.v1", "status": "WARN", "eventCount": 0}


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


class InputTraceRecordingTest(unittest.TestCase):
    def test_polling_backend_event_model_with_fake_samples(self):
        samples = [
            {"screen_x": 0, "screen_y": 0},
            {"screen_x": 10, "screen_y": 0, "buttons": {0x01: True}, "keys": {0x25: True}},
            {"screen_x": 12, "screen_y": 1, "buttons": {0x01: False}, "keys": {0x25: False}},
        ]
        backend = input_trace_recorder.PollingInputBackend(
            sample_ms=1,
            mouse_move_min_px=1,
            capture_mouse=True,
            capture_keyboard=True,
            api=FakePollingApi(samples),
        )
        events = []
        backend.start(events.append)
        time.sleep(0.02)
        backend.stop()
        kinds = [event["kind"] for event in events]
        self.assertIn("mouse_move", kinds)
        self.assertIn("mouse_down", kinds)
        self.assertIn("mouse_up", kinds)
        self.assertIn("click", kinds)
        self.assertIn("key_down", kinds)
        self.assertIn("key_up", kinds)

    def test_input_event_serialization_and_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeInputBackend(
                [
                    {"kind": "mouse_move", "screen_x": 100, "screen_y": 200},
                    {"kind": "click", "button": "left", "screen_x": 110, "screen_y": 205},
                ]
            )
            recorder = input_trace_recorder.InputTraceRecorder(
                tmp,
                session_id="s1",
                recording_id="r1",
                backend=backend,
                capture_window_context=False,
                telemetry_provider=lambda: {"latest_tick": 12, "latest_export_sequence": 34},
                started_monotonic=0.0,
            )
            recorder.start()
            summary = recorder.stop()

            self.assertTrue(backend.started)
            self.assertTrue(backend.stopped)
            self.assertGreaterEqual(summary["eventCount"], 4)
            events = input_trace_recorder.load_input_events(Path(tmp) / "input_events.jsonl")
            click = next(event for event in events if event["kind"] == "click")
            self.assertEqual(click["schema"], "input_event.v1")
            self.assertEqual(click["nearest_tick"], 12)
            self.assertEqual(click["nearest_export_sequence"], 34)

    def test_smoke_test_success_with_fake_move_and_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = input_trace_recorder.PollingInputBackend(
                sample_ms=1,
                mouse_move_min_px=1,
                capture_mouse=True,
                capture_keyboard=False,
                api=FakePollingApi(
                    [
                        {"screen_x": 0, "screen_y": 0},
                        {"screen_x": 20, "screen_y": 0, "buttons": {0x01: True}},
                        {"screen_x": 20, "screen_y": 0, "buttons": {0x01: False}},
                    ]
                ),
            )
            result = input_trace_recorder.run_smoke_test(tmp, backend_obj=backend, duration=0.03, sample_ms=1, json_output=True)
            self.assertTrue(result["success"])
            self.assertGreaterEqual(result["eventCounts"]["moves"], 1)
            self.assertGreaterEqual(result["eventCounts"]["clicks"], 1)

    def test_smoke_test_failure_when_only_start_stop_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = input_trace_recorder.run_smoke_test(
                tmp,
                backend_obj=FakeInputBackend([]),
                duration=0.0,
                sample_ms=1,
                json_output=True,
            )
            self.assertFalse(result["success"])
            self.assertEqual(result["captureStatus"], "backend_started_but_no_events")

    def test_join_click_event_to_nearest_telemetry_snapshot(self):
        input_events = [
            {"kind": "click", "elapsed_seconds": 1.5, "event_seq": 1, "canvas_x": 100, "canvas_y": 120, "button": "left"}
        ]
        telemetry_events = [
            {
                "event_type": "source_snapshot",
                "elapsed_seconds": 1.0,
                "latest_tick": 10,
                "high_value_fields": {
                    "latest_tick": 10,
                    "nearby_objects": [
                        {
                            "effectiveName": "Tree",
                            "effectiveId": 1276,
                            "effectiveActions": ["Chop down"],
                            "aimPoint": {"x": 98, "y": 121},
                        }
                    ],
                },
            },
            {"event_type": "source_snapshot", "elapsed_seconds": 2.0, "latest_tick": 11, "high_value_fields": {"latest_tick": 11}},
        ]
        joined, summary, _classifications, _action_summary, _target_quality, _target_summary, _menu_rows, _menu_summary, *_rest = input_trace_joiner.join_events(input_events, telemetry_events, [])
        self.assertEqual(summary["clickCount"], 1)
        self.assertEqual(summary["targetRelativeClickCount"], 1)
        self.assertEqual(joined[0]["nearestTelemetryBefore"]["tick"], 10)
        self.assertAlmostEqual(joined[0]["clickAnalysis"]["targetRelative"]["dx"], 2.0)

    def test_hover_duration_before_click(self):
        input_event = {"kind": "click", "elapsed_seconds": 3.0}
        snapshots = [
            {"elapsed_seconds": 1.0, "hover": {"topTarget": "Tree", "topOption": "Chop down"}},
            {"elapsed_seconds": 2.0, "hover": {"topTarget": "Tree", "topOption": "Chop down"}},
        ]
        result = input_trace_joiner.hover_duration_before_click(input_event, snapshots, {"effectiveName": "Tree"})
        self.assertEqual(result["durationMs"], 2000.0)
        self.assertTrue(result["actionVisible"])

    def test_repeated_telemetry_click_separate_from_os_click(self):
        input_event = {"kind": "click", "elapsed_seconds": 1.0, "screen_x": 10, "screen_y": 20}
        before = {"menu": {"lastMenuOptionClicked": {"option": "Chop down", "target": "Tree", "clientTick": 5}}}
        result = input_trace_joiner.click_analysis(input_event, before, before, [])
        self.assertEqual(result["osInputClickEvent"], input_event)
        self.assertEqual(result["telemetryObservedClickHistory"]["option"], "Chop down")

    def test_ui_command_construction_includes_input_flags(self):
        config = telemetry_ui.default_config()
        config.update(
            {
                "capture_input": True,
                "capture_mouse": True,
                "capture_keyboard": True,
                "input_backend": "polling",
                "join_input_telemetry": True,
                "camera_behavior_analysis": True,
                "arduino_enabled": True,
                "arduino_port": "COM9",
                "arduino_passthrough_mode": "mirror",
                "arduino_live_mirror": True,
                "vm_mouse_mapping": True,
            }
        )
        command = telemetry_ui.build_recorder_command(config, stop_file="stop.flag", marker_file="markers.txt")
        self.assertIn("--capture-input", command)
        self.assertIn("--capture-keyboard", command)
        self.assertIn("--prefer-polling-input", command)
        self.assertIn("--input-preflight", command)
        self.assertIn("--join-input-telemetry", command)
        self.assertIn("--camera-behavior", command)
        self.assertIn("--arduino", command)
        self.assertIn("--arduino-port", command)
        self.assertIn("--arduino-live-mirror", command)
        self.assertIn("--vm-mouse-mapping", command)

    def test_analyzer_writes_input_camera_and_arduino_summaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            recording.mkdir()
            write_jsonl(
                recording / "events.jsonl",
                [
                    {"event_type": "recording_start", "elapsed_seconds": 0, "session_id": "s1"},
                    {"event_type": "source_snapshot", "elapsed_seconds": 1.0, "latest_tick": 10, "high_value_fields": {"cameraYaw": 10, "cameraPitch": 20}},
                    {"event_type": "source_snapshot", "elapsed_seconds": 1.5, "latest_tick": 11, "high_value_fields": {"cameraYaw": 40, "cameraPitch": 25}},
                    {"event_type": "source_snapshot", "elapsed_seconds": 2.0, "latest_tick": 12, "high_value_fields": {"cameraYaw": 40, "cameraPitch": 25}},
                    {"event_type": "recording_stop", "elapsed_seconds": 3, "duration_seconds": 3},
                ],
            )
            write_jsonl(recording / "input_events.jsonl", [{"kind": "click", "elapsed_seconds": 1.6, "event_seq": 1, "screen_x": 10, "screen_y": 20}])
            write_jsonl(recording / "arduino_events.jsonl", [{"kind": "connect", "elapsed_seconds": 1.0, "event_seq": 1}])
            summary = analyze_manual_recording.update_outputs(recording)
            self.assertIn("input_trace", summary)
            self.assertTrue((recording / "input_trace_summary.json").exists())
            self.assertTrue((recording / "input_action_summary.json").exists())
            self.assertTrue((recording / "input_action_classifications.jsonl").exists())
            self.assertTrue((recording / "camera_behavior_summary.json").exists())
            self.assertTrue((recording / "arduino_trace_summary.json").exists())
            self.assertTrue((recording / "joined_input_telemetry.jsonl").exists())

    def test_analyzer_classifies_start_stop_only_input_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            recording.mkdir()
            write_jsonl(
                recording / "events.jsonl",
                [
                    {"event_type": "recording_start", "elapsed_seconds": 0, "session_id": "s1"},
                    {"event_type": "recording_stop", "elapsed_seconds": 1, "duration_seconds": 1},
                ],
            )
            write_jsonl(
                recording / "input_events.jsonl",
                [
                    {"kind": "capture_start", "source_backend": "windows_hook", "backend_requested": "auto"},
                    {"kind": "capture_stop", "source_backend": "windows_hook"},
                ],
            )
            summary = analyze_manual_recording.update_outputs(recording)
            self.assertEqual(summary["input_trace"]["captureStatus"], "hook_backend_no_events")
            self.assertIn("Input capture started", summary["input_trace"]["message"])

    def test_manual_recorder_preflight_failure_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "input_trace_recorder.run_smoke_test",
                return_value={"success": False, "reason": "missing mouse movement", "eventCounts": {"moves": 0, "downs": 0, "clicks": 0}},
            ):
                code = manual_recorder.main(
                    [
                        "--label",
                        "preflight_fail",
                        "--out-dir",
                        str(Path(tmp) / "recordings"),
                        "--capture-input",
                        "--input-preflight",
                        "--fail-if-input-preflight-fails",
                    ]
                )
            self.assertEqual(code, 3)
            self.assertFalse((Path(tmp) / "recordings").exists())

    def test_manual_recorder_preflight_warning_continue_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stop_file = root / "stop.flag"
            stop_file.write_text("stop\n", encoding="utf-8")
            with mock.patch(
                "input_trace_recorder.run_smoke_test",
                return_value={"success": False, "reason": "missing mouse movement", "eventCounts": {"moves": 0, "downs": 0, "clicks": 0}},
            ), mock.patch("input_trace_recorder.InputTraceRecorder", FakeRecorder):
                code = manual_recorder.main(
                    [
                        "--label",
                        "preflight_warn",
                        "--out-dir",
                        str(root / "recordings"),
                        "--until-stopped",
                        "--stop-file",
                        str(stop_file),
                        "--poll-interval-ms",
                        "1",
                        "--capture-input",
                        "--input-preflight",
                    ]
                )
            self.assertEqual(code, 0)
            recording = telemetry_ui.latest_recording_dir(root)
            summary = json.loads((recording / "summary.json").read_text(encoding="utf-8"))
            self.assertFalse(summary["input_preflight"]["success"])

    def test_manual_recorder_writes_arduino_probe_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stop_file = root / "stop.flag"
            stop_file.write_text("stop\n", encoding="utf-8")
            probe = {
                "schema": "arduino_probe_result.v1",
                "status": "PASS",
                "success": True,
                "classification": "arduino_probe_verified",
                "reason": "unit probe",
            }
            with mock.patch("arduino_mirror_verifier.run_probe", return_value=probe):
                code = manual_recorder.main(
                    [
                        "--label",
                        "probe_status",
                        "--out-dir",
                        str(root / "recordings"),
                        "--until-stopped",
                        "--stop-file",
                        str(stop_file),
                        "--poll-interval-ms",
                        "1",
                        "--arduino-probe",
                        "--require-arduino-probe-verified",
                    ]
                )
            self.assertEqual(code, 0)
            recording = telemetry_ui.latest_recording_dir(root)
            manifest = json.loads((recording / "manifest.json").read_text(encoding="utf-8"))
            summary = json.loads((recording / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["arduino"]["probe"]["classification"], "arduino_probe_verified")
            self.assertEqual(summary["arduino_probe"]["classification"], "arduino_probe_verified")


if __name__ == "__main__":
    unittest.main()

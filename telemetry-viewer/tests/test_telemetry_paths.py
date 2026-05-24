import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telemetry_paths import (  # noqa: E402
    classify_frame_state,
    find_newest_live_session,
    find_newest_session,
    list_event_files,
    list_tick_files,
    raw_recording_unavailable_message,
    resolve_frame_path,
)


def utc_timestamp(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


class TelemetryPathsTest(unittest.TestCase):
    def test_segmented_tick_and_event_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "sessions" / "segmented"
            tick_file = session / "ticks" / "ticks-000001.jsonl"
            event_file = session / "events" / "events-000001.jsonl"
            tick_file.parent.mkdir(parents=True)
            event_file.parent.mkdir(parents=True)
            tick_file.write_text("{}\n", encoding="utf-8")
            event_file.write_text("{}\n", encoding="utf-8")

            self.assertEqual(list_tick_files(session), [tick_file])
            self.assertEqual(list_event_files(session), [event_file])

    def test_legacy_flat_tick_and_event_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "sessions" / "legacy"
            session.mkdir(parents=True)
            tick_file = session / "ticks.jsonl"
            event_file = session / "events.jsonl"
            tick_file.write_text("{}\n", encoding="utf-8")
            event_file.write_text("{}\n", encoding="utf-8")

            self.assertEqual(list_tick_files(session), [tick_file])
            self.assertEqual(list_event_files(session), [event_file])

    def test_frame_path_resolves_relative_to_session_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "sessions" / "frames"
            frame_path = resolve_frame_path(session, "frames/frame-tick-00000001.jpg")

            self.assertEqual(frame_path, (session / "frames" / "frame-tick-00000001.jpg").resolve())

    def test_frame_state_classifies_pending_expired_and_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "sessions" / "frame-state"
            frames = session / "frames"
            frames.mkdir(parents=True)

            recent_missing = {
                "framePath": "frames/frame-tick-00000001.jpg",
                "frameCaptureStatus": "QUEUED",
                "timestampUtc": utc_timestamp(),
            }
            recent_state = classify_frame_state(
                session,
                recent_missing,
                is_latest=True,
                active_session=True,
            )

            self.assertFalse(recent_state["frameExists"])
            self.assertTrue(recent_state["framePending"])
            self.assertFalse(recent_state["frameExpiredOrMissing"])

            old_missing = {
                "framePath": "frames/frame-tick-00000002.jpg",
                "frameCaptureStatus": "QUEUED",
                "timestampUtc": utc_timestamp(timedelta(seconds=-30)),
            }
            old_state = classify_frame_state(
                session,
                old_missing,
                is_latest=True,
                active_session=True,
            )

            self.assertFalse(old_state["frameExists"])
            self.assertFalse(old_state["framePending"])
            self.assertTrue(old_state["frameExpiredOrMissing"])

            existing_frame = frames / "frame-tick-00000003.jpg"
            existing_frame.write_bytes(b"fake image")
            existing = {
                "framePath": "frames/frame-tick-00000003.jpg",
                "frameCaptureStatus": "QUEUED",
                "timestampUtc": utc_timestamp(timedelta(seconds=-30)),
            }
            existing_state = classify_frame_state(
                session,
                existing,
                is_latest=False,
                active_session=False,
            )

            self.assertTrue(existing_state["frameExists"])
            self.assertFalse(existing_state["framePending"])
            self.assertFalse(existing_state["frameExpiredOrMissing"])

    def test_find_newest_session_considers_compact_packet_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            old = root / "old-debug"
            live = root / "live-compact"
            old.mkdir(parents=True)
            live_index = live / "live_packets" / "live_packet_index.json"
            live_index.parent.mkdir(parents=True)
            (old / "manifest.json").write_text("{}", encoding="utf-8")
            live_index.write_text("{}", encoding="utf-8")

            self.assertEqual(find_newest_session(root), live)

    def test_find_newest_live_session_ignores_newer_empty_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            empty_new = root / "new-empty"
            live_old = root / "old-live"
            empty_new.mkdir(parents=True)
            (empty_new / "manifest.json").write_text("{}", encoding="utf-8")
            live_output = live_old / "interaction_geometry" / "live" / "overlay_debug_state.json"
            live_output.parent.mkdir(parents=True)
            live_output.write_text("{}", encoding="utf-8")

            self.assertEqual(find_newest_live_session(root), live_old)

    def test_raw_recording_unavailable_message_mentions_compact_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            session.mkdir()
            (session / "manifest.json").write_text('{"recordingMode":"LIVE_COMPACT_ONLY"}', encoding="utf-8")
            message = raw_recording_unavailable_message(session)
            self.assertIn("LIVE_COMPACT_ONLY", message)
            self.assertIn("DEBUG_RECORDING", message)


if __name__ == "__main__":
    unittest.main()

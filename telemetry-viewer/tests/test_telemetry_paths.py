import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telemetry_paths import (  # noqa: E402
    classify_frame_state,
    list_event_files,
    list_tick_files,
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


if __name__ == "__main__":
    unittest.main()

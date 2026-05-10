import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "check_live_setup.py"
sys.path.insert(0, str(VIEWER_DIR))

import check_live_setup


def write_packet_segment(session: Path) -> None:
    live_dir = session / "live_packets"
    live_dir.mkdir(parents=True, exist_ok=True)
    segment = live_dir / "live-000001.ndjson"
    packets = [
        {
            "schema": "osrs_telemetry_live_packet.v1",
            "packetType": "live_baseline_packet.v1",
            "sessionId": "fake",
            "tick": 7,
            "sequence": 42,
            "timestampUtc": "2026-01-01T00:00:00Z",
            "payload": {"tick": 7},
        },
        {
            "schema": "osrs_telemetry_live_packet.v1",
            "packetType": "live_navigation_packet.v1",
            "sessionId": "fake",
            "tick": 7,
            "sequence": 43,
            "timestampUtc": "2026-01-01T00:00:00Z",
            "payload": {"collision": {"collisionKnown": True}},
        },
        {
            "schema": "osrs_telemetry_live_packet.v1",
            "packetType": "live_collision_window_packet.v1",
            "sessionId": "fake",
            "tick": 7,
            "sequence": 44,
            "timestampUtc": "2026-01-01T00:00:00Z",
            "payload": {
                "collisionKnown": True,
                "windowRadius": 24,
                "width": 3,
                "height": 3,
                "flags": [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
                "collisionWindowTileCount": 9,
                "collisionWindowHash": "win",
            },
        },
    ]
    segment.write_text("".join(json.dumps(packet) + "\n" for packet in packets), encoding="utf-8")
    (live_dir / "latest_segment.txt").write_text("live-000001.ndjson\n", encoding="utf-8")
    (live_dir / "live_packet_index.json").write_text(
        json.dumps(
            {
                "schema": "live_packet_index.v1",
                "activeSegment": "live-000001.ndjson",
                "latestSegment": "live-000001.ndjson",
                "latestTick": 7,
                "latestSequence": 44,
                "retentionBytes": 512 * 1024 * 1024,
                "retentionSegments": 16,
                "retentionTicks": 5000,
                "segments": [
                    {
                        "path": "live-000001.ndjson",
                        "firstSequence": 42,
                        "lastSequence": 44,
                        "firstTick": 7,
                        "lastTick": 7,
                        "bytes": segment.stat().st_size,
                        "packetCountsByType": {
                            "live_baseline_packet.v1": 1,
                            "live_navigation_packet.v1": 1,
                            "live_collision_window_packet.v1": 1,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


class CheckLiveSetupTest(unittest.TestCase):
    def test_passes_with_compact_packets(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            session.mkdir()
            write_packet_segment(session)
            payload = check_live_setup.check_live_setup(session, require_compact_packets=True)
            self.assertEqual(payload["status"], "PASS")
            self.assertTrue(payload["compactPacketsAvailable"])
            self.assertTrue(payload["compactPacketsRecent"])
            self.assertEqual(payload["compactPacketLatestSequence"], 44)
            self.assertTrue(payload["collisionWindowAvailable"])
            self.assertEqual(payload["latestCollisionWindowTick"], 7)

    def test_warns_without_compact_packets_when_not_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            (session / "ticks").mkdir(parents=True)
            (session / "ticks" / "ticks-000001.jsonl").write_text("{}\n", encoding="utf-8")
            payload = check_live_setup.check_live_setup(session, require_compact_packets=False)
            self.assertEqual(payload["status"], "WARN")
            self.assertFalse(payload["compactPacketsAvailable"])
            self.assertTrue(payload["rawTicksAvailable"])

    def test_json_cli_outputs_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            session.mkdir()
            write_packet_segment(session)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--session", str(session), "--require-compact-packets", "--json"],
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema"], "live_setup_check.v1")
            self.assertEqual(payload["status"], "PASS")


if __name__ == "__main__":
    unittest.main()

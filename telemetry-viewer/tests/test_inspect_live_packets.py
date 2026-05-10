import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inspect_live_packets import list_live_packet_files, summarize_live_packets  # noqa: E402


class InspectLivePacketsTest(unittest.TestCase):
    def test_summarizes_synthetic_packets(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "live_packets"
            live_dir.mkdir(parents=True)
            packet_file = live_dir / "live-000001.ndjson"
            packet_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "schema": "osrs_telemetry_live_packet.v1",
                                "packetType": "live_baseline_packet.v1",
                                "sessionId": "session",
                                "tick": 10,
                                "sequence": 1,
                                "timestampUtc": "2026-05-09T00:00:00Z",
                                "payload": {},
                            }
                        ),
                        json.dumps(
                            {
                                "schema": "osrs_telemetry_live_packet.v1",
                                "packetType": "live_writer_health_packet.v1",
                                "sessionId": "session",
                                "tick": 11,
                                "sequence": 2,
                                "timestampUtc": "2026-05-09T00:00:01Z",
                                "payload": {},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = summarize_live_packets(session)

            self.assertEqual(summary["fileCount"], 1)
            self.assertEqual(summary["latestTick"], 11)
            self.assertEqual(summary["latestSequence"], 2)
            self.assertEqual(summary["packetTypeCounts"]["live_baseline_packet.v1"], 1)
            self.assertEqual(summary["packetTypeCounts"]["live_writer_health_packet.v1"], 1)
            self.assertTrue(summary["expectedEnvelopeSchemaPresent"])

    def test_counts_malformed_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "live_packets"
            live_dir.mkdir(parents=True)
            (live_dir / "live-000001.ndjson").write_text(
                '{"schema":"osrs_telemetry_live_packet.v1","packetType":"live_baseline_packet.v1","tick":1,"sequence":1}\n'
                "{not json}\n",
                encoding="utf-8",
            )

            summary = summarize_live_packets(session)

            self.assertEqual(summary["malformedLines"], 1)
            self.assertEqual(summary["packetTypeCounts"]["live_baseline_packet.v1"], 1)

    def test_missing_live_packet_directory_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            session.mkdir()

            self.assertEqual(list_live_packet_files(session), [])

            summary = summarize_live_packets(session)

            self.assertEqual(summary["fileCount"], 0)
            self.assertEqual(summary["packetTypeCounts"], {})

    def test_summary_uses_index_without_scanning_old_segments(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "live_packets"
            live_dir.mkdir(parents=True)
            old_segment = live_dir / "live-000001.ndjson"
            old_segment.write_text("{malformed old segment}\n", encoding="utf-8")
            index = {
                "schema": "live_packet_index.v1",
                "sessionPath": str(session),
                "enabled": True,
                "activeSegment": "live-000001.ndjson",
                "latestSegment": "live-000001.ndjson",
                "segments": [
                    {
                        "path": "live-000001.ndjson",
                        "firstSequence": 1,
                        "lastSequence": 2,
                        "firstTick": 10,
                        "lastTick": 11,
                        "bytes": 20,
                        "packetCountsByType": {"live_baseline_packet.v1": 2},
                    }
                ],
                "latestTick": 11,
                "latestSequence": 2,
                "totalBytes": 20,
                "retentionBytes": 512,
                "retentionSegments": 16,
                "retentionTicks": 5000,
                "prunedCount": 0,
            }
            (live_dir / "live_packet_index.json").write_text(json.dumps(index), encoding="utf-8")

            summary = summarize_live_packets(session)

            self.assertTrue(summary["usedIndex"])
            self.assertEqual(summary["malformedLines"], 0)
            self.assertEqual(summary["packetTypeCounts"]["live_baseline_packet.v1"], 2)

    def test_latest_only_scans_latest_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "live_packets"
            live_dir.mkdir(parents=True)
            (live_dir / "live-000001.ndjson").write_text("{malformed old segment}\n", encoding="utf-8")
            (live_dir / "live-000002.ndjson").write_text(
                json.dumps(
                    {
                        "schema": "osrs_telemetry_live_packet.v1",
                        "packetType": "live_writer_health_packet.v1",
                        "sessionId": "session",
                        "tick": 20,
                        "sequence": 3,
                        "timestampUtc": "2026-05-09T00:00:02Z",
                        "payload": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (live_dir / "latest_segment.txt").write_text("live-000002.ndjson\n", encoding="utf-8")

            summary = summarize_live_packets(session, latest_only=True)

            self.assertFalse(summary["usedIndex"])
            self.assertEqual(summary["malformedLines"], 0)
            self.assertEqual(summary["fileCount"], 1)
            self.assertEqual(summary["latestTick"], 20)
            self.assertEqual(summary["packetTypeCounts"]["live_writer_health_packet.v1"], 1)


if __name__ == "__main__":
    unittest.main()

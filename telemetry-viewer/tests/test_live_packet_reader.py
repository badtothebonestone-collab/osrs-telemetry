import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live_packet_reader import (  # noqa: E402
    iter_live_packets,
    latest_segment_path,
    list_live_packet_files,
    read_index,
)


def packet(packet_type: str, tick: int, sequence: int) -> str:
    return json.dumps(
        {
            "schema": "osrs_telemetry_live_packet.v1",
            "packetType": packet_type,
            "sessionId": "session",
            "tick": tick,
            "sequence": sequence,
            "timestampUtc": "2026-05-09T00:00:00Z",
            "payload": {},
        }
    )


class LivePacketReaderTest(unittest.TestCase):
    def test_reads_index_and_segment_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "live_packets"
            live_dir.mkdir(parents=True)
            segment = live_dir / "live-000001.ndjson"
            segment.write_text(packet("live_baseline_packet.v1", 1, 1) + "\n", encoding="utf-8")
            (live_dir / "live_packet_index.json").write_text(
                json.dumps(
                    {
                        "schema": "live_packet_index.v1",
                        "segments": [{"path": "live-000001.ndjson"}],
                        "activeSegment": "live-000001.ndjson",
                    }
                ),
                encoding="utf-8",
            )

            self.assertIsNotNone(read_index(session))
            self.assertEqual(list_live_packet_files(session), [segment])
            self.assertEqual(latest_segment_path(session), segment)

    def test_latest_pointer_follows_rollover(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "live_packets"
            live_dir.mkdir(parents=True)
            first = live_dir / "live-000001.ndjson"
            second = live_dir / "live-000002.ndjson"
            first.write_text(packet("live_baseline_packet.v1", 1, 1) + "\n", encoding="utf-8")
            second.write_text(packet("live_baseline_packet.v1", 2, 2) + "\n", encoding="utf-8")
            pointer = live_dir / "latest_segment.txt"
            pointer.write_text("live-000001.ndjson\n", encoding="utf-8")

            self.assertEqual(latest_segment_path(session), first)

            pointer.write_text("live-000002.ndjson\n", encoding="utf-8")

            self.assertEqual(latest_segment_path(session), second)

    def test_iter_packets_filters_and_tolerates_partial_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "live_packets"
            live_dir.mkdir(parents=True)
            segment = live_dir / "live-000001.ndjson"
            segment.write_text(
                packet("live_baseline_packet.v1", 1, 1)
                + "\n"
                + packet("live_writer_health_packet.v1", 2, 2)
                + "\n"
                + '{"schema":"osrs_telemetry_live_packet.v1"',
                encoding="utf-8",
            )

            results = list(
                iter_live_packets(
                    [segment],
                    packet_type="live_writer_health_packet.v1",
                    since_sequence=1,
                )
            )

            self.assertEqual(len(results), 1)
            self.assertIsNone(results[0].error)
            self.assertEqual(results[0].record["sequence"], 2)


if __name__ == "__main__":
    unittest.main()

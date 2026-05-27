import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import maintenance  # noqa: E402


class MaintenanceTest(unittest.TestCase):
    def test_live_packets_report_finds_legacy_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp) / ".osrs-telemetry" / "sessions"
            legacy = sessions / "s1" / "live_packets" / "live-000001.ndjson"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("{}\n", encoding="utf-8")
            (sessions / "s1" / "script_authoring_context" / "keep.json").parent.mkdir(parents=True)
            (sessions / "s1" / "script_authoring_context" / "keep.json").write_text("{}", encoding="utf-8")

            report = maintenance.live_packets_report(sessions)

        self.assertEqual(report["legacyLivePacketFileCount"], 1)
        self.assertTrue(report["cleanupRecommended"])
        self.assertTrue(report["livePacketsRuntimeRemoved"])
        self.assertFalse(report["livePacketWriterActive"])

    def test_prune_dry_run_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp) / ".osrs-telemetry" / "sessions"
            legacy = sessions / "s1" / "live_packets" / "live-000001.ndjson"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("{}\n", encoding="utf-8")

            result = maintenance.prune_legacy_live_packets(sessions, apply=False)

            self.assertTrue(legacy.exists())
            self.assertTrue(result["dryRun"])
            self.assertEqual(result["deletedCount"], 0)
            self.assertEqual(result["candidateCount"], 1)

    def test_prune_apply_only_deletes_legacy_packet_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp) / ".osrs-telemetry" / "sessions"
            legacy = sessions / "s1" / "live_packets" / "live-000001.ndjson"
            keep = sessions / "s1" / "script_authoring_context" / "context.json"
            legacy.parent.mkdir(parents=True)
            keep.parent.mkdir(parents=True)
            legacy.write_text("{}\n", encoding="utf-8")
            keep.write_text("{}", encoding="utf-8")

            result = maintenance.prune_legacy_live_packets(sessions, apply=True)

            self.assertFalse(legacy.exists())
            self.assertTrue(keep.exists())
            self.assertTrue(result["apply"])
            self.assertEqual(result["deletedCount"], 1)


if __name__ == "__main__":
    unittest.main()

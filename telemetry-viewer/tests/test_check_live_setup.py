import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "check_live_setup.py"
sys.path.insert(0, str(VIEWER_DIR))

import check_live_setup  # noqa: E402


def write_legacy_packet_segment(session: Path) -> None:
    live_dir = session / "live_packets"
    live_dir.mkdir(parents=True, exist_ok=True)
    (live_dir / "live-000001.ndjson").write_text("{}\n", encoding="utf-8")
    (live_dir / "latest_segment.txt").write_text("live-000001.ndjson\n", encoding="utf-8")
    (live_dir / "live_packet_index.json").write_text("{}", encoding="utf-8")


class CheckLiveSetupTest(unittest.TestCase):
    def test_reports_legacy_packets_without_treating_them_as_live_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            session.mkdir()
            write_legacy_packet_segment(session)
            with mock.patch.object(check_live_setup, "plugin_snapshot_check", return_value={"enabled": False}):
                payload = check_live_setup.check_live_setup(session)

        self.assertEqual(payload["status"], "WARN")
        self.assertTrue(payload["livePacketsRuntimeRemoved"])
        self.assertFalse(payload["livePacketWriterActive"])
        self.assertTrue(payload["legacyLivePackets"]["legacyLivePacketFilesPresent"])
        self.assertIn("legacy live packet files remain", "\n".join(payload["warnings"]))

    def test_explicit_bounded_json_is_not_legacy_packet_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            keep = session / "script_authoring_context" / "context.json"
            keep.parent.mkdir(parents=True)
            keep.write_text("{}", encoding="utf-8")
            with mock.patch.object(check_live_setup, "plugin_snapshot_check", return_value={"enabled": False}):
                payload = check_live_setup.check_live_setup(session)

        self.assertFalse(payload["legacyLivePackets"]["legacyLivePacketFilesPresent"])
        self.assertEqual(payload["legacyLivePackets"]["legacyLivePacketFileCount"], 0)

    def test_json_cli_outputs_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            session.mkdir()
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--session", str(session), "--json"],
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema"], "live_setup_check.v1")
            self.assertTrue(payload["livePacketsRuntimeRemoved"])


if __name__ == "__main__":
    unittest.main()

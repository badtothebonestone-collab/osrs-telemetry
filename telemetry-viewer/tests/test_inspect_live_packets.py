import json
import subprocess
import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "inspect_live_packets.py"


class InspectLivePacketsTest(unittest.TestCase):
    def test_tool_is_retired_with_replacement_guidance(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--latest-session", "--summary"],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "legacy_live_packet_inspection_retired.v1")
        self.assertEqual(payload["status"], "FAIL")
        self.assertTrue(payload["livePacketsRuntimeRemoved"])
        self.assertFalse(payload["livePacketWriterActive"])
        self.assertIn("context_service.py --query current-debug-context", payload["replacement"]["currentState"])


if __name__ == "__main__":
    unittest.main()

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_service_context


class DiagnoseServiceContextTest(unittest.TestCase):
    def test_builds_daemon_summary_from_status(self):
        payload = diagnose_service_context.build_from_daemon(
            {
                "activeProfile": "woodcutting",
                "brainTaskPolicy": "woodcutting_bank",
                "profileCandidateCount": 1,
                "broadCandidateCount": 2,
                "serviceCandidateInputCount": 1,
                "serviceCandidateVisibility": "available",
                "brain": {
                    "noActionEmitted": True,
                    "serviceContext": {
                        "serviceNeeded": True,
                        "serviceTypeNeeded": "bank",
                        "candidateCount": 1,
                        "candidateCountsByType": {"bank_booth": 1},
                        "bestServiceCandidate": {
                            "targetType": "sceneObject",
                            "classId": "bank_booth",
                            "targetName": "Bank booth",
                            "worldX": 3207,
                            "worldY": 3215,
                            "plane": 2,
                            "interactionRadiusTiles": 2,
                            "clickbox": {"x": 1, "y": 2, "w": 3, "h": 4},
                        },
                    },
                },
            }
        )

        self.assertTrue(payload["serviceNeeded"])
        self.assertEqual(payload["serviceCandidateInputCount"], 1)
        self.assertEqual(payload["bestServiceCandidate"]["targetName"], "Bank booth")
        self.assertEqual(payload["bestServiceCandidate"]["interactionRadiusTiles"], 2)
        self.assertEqual(payload["bestServiceCandidate"]["clickbox"]["w"], 3)
        self.assertEqual(payload["candidatesByType"], {"bank_booth": 1})
        self.assertEqual(payload["serviceCandidateVisibility"], "available")

    def test_json_prints_stdout_only_without_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = set(os.listdir(tmp))
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = diagnose_service_context.main(["--json"])
            after = set(os.listdir(tmp))

        self.assertEqual(code, 0)
        self.assertEqual(before, after)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["schema"], diagnose_service_context.SCHEMA)
        self.assertFalse(payload["daemonReachable"])


if __name__ == "__main__":
    unittest.main()

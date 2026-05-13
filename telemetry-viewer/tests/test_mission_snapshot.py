import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import mission_snapshot


def sample_payloads(*, unsafe: bool = False, no_action: bool = True) -> tuple[dict, dict, dict]:
    health = {"status": "PASS", "liveCoreDaemonActive": True}
    status = {
        "status": "PASS",
        "dailyMode": "snapshot-no-files",
        "inputSourceActive": "plugin-snapshot",
        "noFileDaily": True,
        "writeDebugLiveFiles": False,
        "overlayStateWritten": True,
        "candidateCount": 0,
        "requiredContextDomains": ["inventory", "process_inventory"],
        "missingRequiredContextDomains": [],
        "optionalMissingContextDomains": ["target.candidates"],
        "brain": {
            "task": "woodcutting",
            "genericTaskState": {
                "phase": "inventory_full",
                "activeIntent": "process_inventory",
                "noActionEmitted": True,
            },
            "goalProgress": {
                "displayedGoalProgress": 3,
                "goalCount": 5,
                "currentHeldCount": 28,
                "baselineHeldCount": 25,
            },
            "currentContextSummary": {
                "inventory": {"inventoryFull": True, "freeSlots": 0},
            },
            "serviceContext": {"serviceNeeded": False},
            "processInventoryContext": {"processRequired": True, "processTypeNeeded": "firemaking"},
            "navigationIntentContext": {"navigationNeeded": False},
            "requiredContextDomains": ["inventory", "process_inventory"],
            "missingRequiredContextDomains": [],
            "optionalMissingContextDomains": ["target.candidates"],
            "warnings": ["no tree candidates currently observed"],
            "noActionEmitted": no_action,
        },
        "overlayDebug": {
            "markers": [
                {
                    "markerType": "diagnostic",
                    "label": "Process inventory: firemaking",
                    "selected": True,
                }
            ]
        },
    }
    if unsafe:
        status["brain"]["clickCommand"] = {"x": 1, "y": 2}
    control = {
        "status": "PASS",
        "state": {
            "activeTask": "woodcutting",
            "activeMissionPreset": "woodcut_firemake",
            "taskPolicy": "woodcutting_firemake",
            "goalCount": 5,
            "observeOnly": False,
            "brainEnabled": True,
            "overlayEnabled": True,
            "overlayMode": "intent",
            "overlayBackupCandidates": 2,
        },
        "noActionEmitted": True,
    }
    return health, status, control


def fake_fetcher(health: dict, status: dict, control: dict):
    def fetch(url: str, timeout: float = 3.0) -> dict:
        if url.endswith("/health"):
            return health
        if url.endswith("/status"):
            return status
        if url.endswith("/control"):
            return control
        raise AssertionError(f"unexpected url: {url}")

    return fetch


class MissionSnapshotTest(unittest.TestCase):
    def test_default_prints_human_summary_and_writes_no_files(self):
        health, status, control = sample_payloads()
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            with mock.patch.object(mission_snapshot, "fetch_json", side_effect=fake_fetcher(health, status, control)):
                with contextlib.redirect_stdout(io.StringIO()) as stdout:
                    code = mission_snapshot.main(["--daemon-url", "http://127.0.0.1:8890"])
            after = set(os.listdir(temp))

        self.assertEqual(code, 0)
        text = stdout.getvalue()
        self.assertIn("MISSION SNAPSHOT", text)
        self.assertIn("Policy: woodcutting_firemake", text)
        self.assertIn("Active intent: process_inventory", text)
        self.assertEqual(before, after)

    def test_json_prints_one_object_to_stdout_only(self):
        health, status, control = sample_payloads()
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            with mock.patch.object(mission_snapshot, "fetch_json", side_effect=fake_fetcher(health, status, control)):
                with contextlib.redirect_stdout(io.StringIO()) as stdout:
                    code = mission_snapshot.main(["--json"])
            after = set(os.listdir(temp))

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["schema"], "mission_snapshot.v1")
        self.assertEqual(payload["missionPreset"], "woodcut_firemake")
        self.assertEqual(before, after)

    def test_output_writes_exactly_one_json_file_and_no_ndjson(self):
        health, status, control = sample_payloads()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "mission_snapshot.json"
            with mock.patch.object(mission_snapshot, "fetch_json", side_effect=fake_fetcher(health, status, control)):
                with contextlib.redirect_stdout(io.StringIO()) as stdout:
                    code = mission_snapshot.main(["--output", str(output)])
            files = sorted(path.name for path in Path(temp).iterdir())
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(files, ["mission_snapshot.json"])
        self.assertNotIn(".ndjson", "".join(files))
        self.assertEqual(payload["taskPolicy"], "woodcutting_firemake")
        self.assertIn(str(output), stdout.getvalue())

    def test_handles_daemon_unavailable_clearly(self):
        def failing_fetch(_url: str, timeout: float = 3.0) -> dict:
            raise OSError("daemon down")

        with mock.patch.object(mission_snapshot, "fetch_json", side_effect=failing_fetch):
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = mission_snapshot.main(["--daemon-url", "http://127.0.0.1:8890"])

        self.assertEqual(code, 1)
        self.assertIn("daemon endpoint unavailable", stdout.getvalue())

    def test_warns_if_no_action_emitted_false(self):
        health, status, control = sample_payloads(no_action=False)
        payload = mission_snapshot.build_snapshot(health, status, control, daemon_url="http://127.0.0.1:8890")

        self.assertEqual(payload["status"], "FAIL")
        self.assertFalse(payload["noActionEmitted"])
        self.assertTrue(any("noActionEmitted" in warning for warning in payload["warnings"]))

    def test_detects_unsafe_action_like_fields(self):
        health, status, control = sample_payloads(unsafe=True)
        payload = mission_snapshot.build_snapshot(health, status, control, daemon_url="http://127.0.0.1:8890")

        self.assertEqual(payload["status"], "FAIL")
        self.assertTrue(any("action-like" in warning for warning in payload["warnings"]))
        self.assertTrue(any("clickCommand" in path for path in payload["unsafeFieldPaths"]))

    def test_summarizes_health_status_and_control(self):
        health, status, control = sample_payloads()
        payload = mission_snapshot.build_snapshot(health, status, control, daemon_url="http://127.0.0.1:8890")

        self.assertEqual(payload["healthStatus"], "PASS")
        self.assertEqual(payload["dailyMode"], "snapshot-no-files")
        self.assertEqual(payload["inputSourceActive"], "plugin-snapshot")
        self.assertEqual(payload["activeTask"], "woodcutting")
        self.assertEqual(payload["missionPreset"], "woodcut_firemake")
        self.assertEqual(payload["taskPolicy"], "woodcutting_firemake")
        self.assertEqual(payload["goalCount"], 5)
        self.assertEqual(payload["genericPhase"], "inventory_full")
        self.assertEqual(payload["processNeeded"], True)
        self.assertEqual(payload["processType"], "firemaking")
        self.assertEqual(payload["selectedOverlayMarker"]["label"], "Process inventory: firemaking")


if __name__ == "__main__":
    unittest.main()

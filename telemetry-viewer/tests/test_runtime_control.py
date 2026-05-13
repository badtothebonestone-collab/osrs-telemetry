import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "control_live_daemon.py"
sys.path.insert(0, str(VIEWER_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import live_core_daemon as daemon
import live_target_processor as live
import runtime_control
from test_live_core_daemon import make_args, snapshot_with_logs


def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=1) as response:
        return json.loads(response.read().decode("utf-8"))


class RuntimeControlModelTest(unittest.TestCase):
    def test_state_defaults_and_safe_update(self):
        state = runtime_control.RuntimeControlState(activeTask="woodcutting", taskPolicy="woodcutting_bank", goalCount=5)

        result = runtime_control.apply_control_command(
            state,
            {"taskPolicy": "woodcutting_firemake", "goalCount": 7, "observeOnly": False, "overlayBackupCandidates": 1},
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(state.taskPolicy, "woodcutting_firemake")
        self.assertEqual(state.goalCount, 7)
        self.assertEqual(state.overlayBackupCandidates, 1)
        self.assertTrue(result.noActionEmitted)

    def test_rejects_unknown_policy_and_action_like_fields(self):
        state = runtime_control.RuntimeControlState(activeTask="woodcutting", taskPolicy="woodcutting_bank", goalCount=5)

        unknown = runtime_control.apply_control_command(state, {"taskPolicy": "not_a_policy"})
        unsafe = runtime_control.apply_control_command(state, {"clickTarget": {"x": 1}, "taskPolicy": "woodcutting_drop"})

        self.assertEqual(unknown.status, "FAIL")
        self.assertIn("taskPolicy", unknown.rejectedFields)
        self.assertEqual(unsafe.status, "FAIL")
        self.assertIn("clickTarget", unsafe.rejectedFields)
        self.assertEqual(state.taskPolicy, "woodcutting_bank")

    def test_reset_request_is_marked_without_persistence(self):
        state = runtime_control.RuntimeControlState(activeTask="woodcutting", taskPolicy="woodcutting_bank", goalCount=5)

        result = runtime_control.apply_control_command(state, {"resetBrainState": True})

        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.resetBrainState)
        self.assertTrue(state.resetBaselineRequested)


class RuntimeControlDaemonEndpointTest(unittest.TestCase):
    def test_get_and_post_control_update_daemon_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            args = make_args(session, "--input-source", "plugin-snapshot", "--human-dashboard", "--goal-count", "5")
            response = snapshot_with_logs(session, 1, [0, 1, 2])
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()
                core.start_context_server()
                try:
                    base = f"http://127.0.0.1:{args.context_port}"
                    current = json.loads(urllib.request.urlopen(f"{base}/control", timeout=1).read().decode("utf-8"))
                    changed = post_json(f"{base}/control", {"taskPolicy": "woodcutting_firemake", "goalCount": 9})
                    after = json.loads(urllib.request.urlopen(f"{base}/control", timeout=1).read().decode("utf-8"))
                finally:
                    core.stop_context_server()

        self.assertEqual(current["state"]["taskPolicy"], "woodcutting_bank")
        self.assertEqual(changed["status"], "PASS")
        self.assertEqual(changed["state"]["taskPolicy"], "woodcutting_firemake")
        self.assertEqual(after["state"]["goalCount"], 9)

    def test_action_like_control_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            args = make_args(session, "--input-source", "plugin-snapshot", "--human-dashboard", "--goal-count", "5")
            core = daemon.LiveCoreDaemon(session, args)
            core.start_context_server()
            try:
                base = f"http://127.0.0.1:{args.context_port}"
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    post_json(f"{base}/control", {"walkTo": {"x": 1}, "taskPolicy": "woodcutting_drop"})
            finally:
                core.stop_context_server()

        body = raised.exception.read().decode("utf-8")
        payload = json.loads(body)
        self.assertEqual(payload["status"], "FAIL")
        self.assertIn("walkTo", payload["rejectedFields"])

    def test_runtime_policy_is_used_without_restart_and_reset_applies_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, list(range(28)))
            args = make_args(session, "--input-source", "plugin-snapshot", "--human-dashboard", "--goal-count", "5")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.start_context_server()
                try:
                    base = f"http://127.0.0.1:{args.context_port}"
                    post_json(f"{base}/control", {"taskPolicy": "woodcutting_firemake", "resetBrainState": True})
                    core.poll_once()
                finally:
                    core.stop_context_server()

        self.assertEqual(core.runtime_control.taskPolicy, "woodcutting_firemake")
        self.assertFalse(core.runtime_control.resetBaselineRequested)
        self.assertEqual(core.state.brain_decision["genericTaskState"]["activeIntent"], "process_inventory")
        self.assertTrue(core.brain_reset_applied)

    def test_control_cli_prints_json_to_stdout_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            args = make_args(session, "--input-source", "plugin-snapshot", "--human-dashboard", "--goal-count", "5")
            core = daemon.LiveCoreDaemon(session, args)
            core.start_context_server()
            try:
                before = set(os.listdir(tmp))
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "--daemon-url",
                        f"http://127.0.0.1:{args.context_port}",
                        "--set-policy",
                        "woodcutting_firemake",
                        "--goal-count",
                        "5",
                        "--reset-brain-state",
                        "--json",
                    ],
                    cwd=tmp,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                after = set(os.listdir(tmp))
            finally:
                core.stop_context_server()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["state"]["taskPolicy"], "woodcutting_firemake")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from tools import run_traced_dev_cycle as dev_cycle


class FakeDeps:
    def __init__(
        self,
        *,
        readiness: dict | list[dict],
        trace_records: list[dict] | None = None,
        auto_login_results: dict | list[dict] | None = None,
    ):
        self.readiness_sequence = list(readiness) if isinstance(readiness, list) else [readiness]
        if auto_login_results is None:
            self.auto_login_sequence = [{"status": "recovered_loaded_scene", "loadedSceneVerified": True}]
        else:
            self.auto_login_sequence = list(auto_login_results) if isinstance(auto_login_results, list) else [auto_login_results]
        self.readiness_calls = 0
        self.auto_login_calls = 0
        self.trace_records = trace_records or []
        self.commands: list[list[str]] = []
        self.launches: list[object] = []
        self.sleep_calls: list[float] = []
        self.now = 0.0

    def run_command(self, command, *, timeout=30.0, cwd=dev_cycle.PROJECT_ROOT):
        command = [str(item) for item in command]
        self.commands.append(command)
        joined = " ".join(command)
        if "context_service.py" in joined and "--pipeline-health" in command:
            return dev_cycle.CommandResult(
                command,
                0,
                json.dumps(
                    {
                        "status": "PASS",
                        "livePacketWriterActive": False,
                        "livePacketsRuntimeRemoved": True,
                        "ndjsonRuntimeRemoved": True,
                        "jsonlRuntimeRemoved": True,
                    }
                ),
                "",
            )
        if ("context_service.py" in joined and "--ensure-loaded-scene" in command) or "recover.py" in joined:
            index = min(self.auto_login_calls, len(self.auto_login_sequence) - 1)
            self.auto_login_calls += 1
            payload = dict(self.auto_login_sequence[index])
            returncode = int(payload.pop("_returncode", 0))
            stderr = str(payload.pop("_stderr", ""))
            stdout = str(payload.pop("_stdout", json.dumps(payload)))
            return dev_cycle.CommandResult(command, returncode, stdout, stderr)
        if "diagnose_live_readiness.py" in joined:
            index = min(self.readiness_calls, len(self.readiness_sequence) - 1)
            self.readiness_calls += 1
            readiness = self.readiness_sequence[index]
            return dev_cycle.CommandResult(command, 0 if readiness.get("ready") else 1, json.dumps(readiness), "")
        if "execute_next_action.py" in joined:
            if "--nav-trace-output" in command:
                trace_path = Path(command[command.index("--nav-trace-output") + 1])
                trace_path.parent.mkdir(parents=True, exist_ok=True)
                trace_path.write_text("".join(json.dumps(record) + "\n" for record in self.trace_records), encoding="utf-8")
            return dev_cycle.CommandResult(command, 0, json.dumps({"status": "PASS"}), "")
        return dev_cycle.CommandResult(command, 0, "{}", "")

    def launch_process(self, command, *, cwd=dev_cycle.PROJECT_ROOT):
        self.launches.append(command)
        return {"started": True, "reason": "started", "pid": 123, "command": command}

    def fetch_json(self, url, *, timeout=3.0):
        if str(url).endswith("/status"):
            return {"status": "ok", "latestTick": 42, "sessionPath": "session-a"}
        if str(url).endswith("/health"):
            return {"status": "PASS", "latestTick": 42}
        return {}

    def detect_window(self, _filter):
        return {"matchedWindowTitle": "RuneLite"}

    def detect_processes(self):
        return []

    def sleep(self, _seconds):
        self.sleep_calls.append(float(_seconds))
        self.now += float(_seconds)
        return None

    def monotonic(self):
        return self.now

    def as_deps(self):
        return dev_cycle.RuntimeDeps(
            run_command=self.run_command,
            launch_process=self.launch_process,
            fetch_json=self.fetch_json,
            detect_window=self.detect_window,
            detect_processes=self.detect_processes,
            sleep=self.sleep,
            monotonic=self.monotonic,
        )


def write_config(path: Path, trace_path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "daemon_url": "http://127.0.0.1:8890",
                "snapshot_url": "http://127.0.0.1:8893",
                "backend": "arduino",
                "arduino_port": "COM9",
                "launch_runelite_if_missing": False,
                "start_daemon_if_missing": False,
                "trace_output_path": str(trace_path),
                "max_actions": 1,
                "max_runtime_seconds": 30,
                "max_wait_seconds": 5,
                "watch_poll_seconds": 1,
            }
        ),
        encoding="utf-8",
    )


def blocked_login_readiness() -> dict:
    return {
        "status": "FAIL",
        "ready": False,
        "manualLoginRequired": True,
        "unknownScreen": False,
        "livenessState": "login_screen",
        "loadedSceneProof": {"loadedSceneVerified": False, "gameState": "LOGIN_SCREEN"},
        "daemon": {"latestTick": None, "sessionPath": "session-a"},
        "actionReadiness": {"executionAllowed": False},
        "actionExecution": {"allowed": False},
        "blockers": [],
        "livenessRecoveryRecommended": True,
    }


def ready_readiness(*, tick: int = 43) -> dict:
    return {
        "status": "PASS",
        "ready": True,
        "manualLoginRequired": False,
        "unknownScreen": False,
        "livenessState": "loaded_scene",
        "loadedSceneProof": {"loadedSceneVerified": True, "gameState": "LOGGED_IN"},
        "daemon": {"latestTick": tick, "sessionPath": "session-a"},
        "actionReadiness": {"executionAllowed": True},
        "actionExecution": {"allowed": True},
        "blockers": [],
    }


class RunTracedDevCycleTest(unittest.TestCase):
    def test_trace_summary_counts_reasons_and_flags_missing_reason(self):
        records = [
            {
                "_line": 1,
                "schema": "navigation_decision_trace.v1",
                "decision": "wait",
                "reason": "player_still_moving_to_clicked_waypoint",
                "observed": {"nextActionAllowed": False},
            },
            {
                "_line": 2,
                "schema": "navigation_decision_trace.v1",
                "decision": "click",
                "reason": "",
                "chosenSubgoal": {"targetTile": {"worldX": 3200, "worldY": 3201, "plane": 0}},
            },
        ]

        summary = dev_cycle.summarize_trace(records)

        self.assertEqual(summary["decisionCount"], 2)
        self.assertEqual(summary["decisionCounts"]["wait"], 1)
        self.assertEqual(summary["decisionCounts"]["click"], 1)
        self.assertEqual(summary["reasonCounts"]["missing"], 1)
        self.assertEqual(summary["firstSuspiciousDecision"]["issue"], "missing_reason_string")

    def test_dry_run_reports_manual_login_without_running_executor(self):
        readiness = {
            "status": "FAIL",
            "ready": False,
            "manualLoginRequired": True,
            "unknownScreen": False,
            "livenessState": "login_screen",
            "loadedSceneProof": {"loadedSceneVerified": False, "gameState": "LOGIN_SCREEN"},
            "daemon": {"latestTick": None, "sessionPath": "session-a"},
            "actionReadiness": {"executionAllowed": False},
            "actionExecution": {"allowed": False},
            "blockers": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "dev_cycle.local.json"
            trace_path = Path(tmp) / "navigation_decisions.jsonl"
            write_config(config_path, trace_path)
            fake = FakeDeps(readiness=readiness)

            payload = dev_cycle.run_dev_cycle(dev_cycle.parse_args(["--dry-run", "--config", str(config_path)]), deps=fake.as_deps())

        self.assertEqual(payload["status"], "BLOCKED")
        self.assertEqual(payload["blocker"]["category"], "manual_login_required")
        self.assertTrue(any("diagnose_live_readiness.py" in " ".join(command) for command in fake.commands))
        self.assertFalse(any("execute_next_action.py" in " ".join(command) for command in fake.commands))
        self.assertIn("--nav-trace", payload["wouldRunCommand"])

    def test_run_mode_delegates_to_existing_executor_and_summarizes_new_trace(self):
        readiness = {
            "status": "PASS",
            "ready": True,
            "manualLoginRequired": False,
            "unknownScreen": False,
            "livenessState": "loaded_scene",
            "loadedSceneProof": {"loadedSceneVerified": True, "gameState": "LOGGED_IN"},
            "daemon": {"latestTick": 42, "sessionPath": "session-a"},
            "actionReadiness": {"executionAllowed": True},
            "actionExecution": {"allowed": True},
            "blockers": [],
        }
        trace_record = {
            "schema": "navigation_decision_trace.v1",
            "decision": "wait",
            "reason": "player_still_moving_to_clicked_waypoint",
            "observed": {"observedResult": "service_navigation_clicked_waiting", "nextActionAllowed": False},
            "distances": {"distanceDelta": -1.0, "distanceImproving": True},
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "dev_cycle.local.json"
            trace_path = Path(tmp) / "navigation_decisions.jsonl"
            write_config(config_path, trace_path)
            fake = FakeDeps(readiness=readiness, trace_records=[trace_record])

            payload = dev_cycle.run_dev_cycle(dev_cycle.parse_args(["--run", "--config", str(config_path)]), deps=fake.as_deps())

        self.assertEqual(payload["status"], "PASS")
        self.assertTrue(payload["ready"])
        self.assertTrue(any("execute_next_action.py" in " ".join(command) for command in fake.commands))
        self.assertEqual(payload["trace"]["newDecisionCount"], 1)
        self.assertEqual(payload["trace"]["decisionCounts"], {"wait": 1})
        self.assertIsNone(payload["trace"]["firstSuspiciousDecision"])

    def test_watch_dry_run_polls_until_ready_without_executor(self):
        blocked = {
            "status": "FAIL",
            "ready": False,
            "manualLoginRequired": True,
            "unknownScreen": False,
            "livenessState": "login_screen",
            "loadedSceneProof": {"loadedSceneVerified": False, "gameState": "LOGIN_SCREEN"},
            "daemon": {"latestTick": None, "sessionPath": "session-a"},
            "actionReadiness": {"executionAllowed": False},
            "actionExecution": {"allowed": False},
            "blockers": [],
        }
        ready = {
            "status": "PASS",
            "ready": True,
            "manualLoginRequired": False,
            "unknownScreen": False,
            "livenessState": "loaded_scene",
            "loadedSceneProof": {"loadedSceneVerified": True, "gameState": "LOGGED_IN"},
            "daemon": {"latestTick": 43, "sessionPath": "session-a"},
            "actionReadiness": {"executionAllowed": True},
            "actionExecution": {"allowed": True},
            "blockers": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "dev_cycle.local.json"
            trace_path = Path(tmp) / "navigation_decisions.jsonl"
            write_config(config_path, trace_path)
            fake = FakeDeps(readiness=[blocked, ready])

            payload = dev_cycle.run_dev_cycle(dev_cycle.parse_args(["--dry-run", "--watch", "--config", str(config_path)]), deps=fake.as_deps())

        self.assertEqual(payload["status"], "PASS")
        self.assertTrue(payload["ready"])
        self.assertEqual(fake.readiness_calls, 2)
        self.assertEqual(fake.sleep_calls, [1.0])
        self.assertFalse(any("execute_next_action.py" in " ".join(command) for command in fake.commands))
        self.assertEqual(len(payload["watch"]["events"]), 2)
        self.assertFalse(payload["watch"]["timedOut"])
        self.assertIn("--nav-trace", payload["wouldRunCommand"])

    def test_watch_run_delegates_after_manual_login_clears(self):
        blocked = {
            "status": "FAIL",
            "ready": False,
            "manualLoginRequired": True,
            "unknownScreen": False,
            "livenessState": "login_screen",
            "loadedSceneProof": {"loadedSceneVerified": False, "gameState": "LOGIN_SCREEN"},
            "daemon": {"latestTick": None, "sessionPath": "session-a"},
            "actionReadiness": {"executionAllowed": False},
            "actionExecution": {"allowed": False},
            "blockers": [],
        }
        ready = {
            "status": "PASS",
            "ready": True,
            "manualLoginRequired": False,
            "unknownScreen": False,
            "livenessState": "loaded_scene",
            "loadedSceneProof": {"loadedSceneVerified": True, "gameState": "LOGGED_IN"},
            "daemon": {"latestTick": 44, "sessionPath": "session-a"},
            "actionReadiness": {"executionAllowed": True},
            "actionExecution": {"allowed": True},
            "blockers": [],
        }
        trace_record = {
            "schema": "navigation_decision_trace.v1",
            "decision": "advance",
            "reason": "service_navigation_reached_node",
            "observed": {"observedResult": "service_navigation_reached_node", "nextActionAllowed": True},
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "dev_cycle.local.json"
            trace_path = Path(tmp) / "navigation_decisions.jsonl"
            write_config(config_path, trace_path)
            fake = FakeDeps(readiness=[blocked, ready], trace_records=[trace_record])

            payload = dev_cycle.run_dev_cycle(dev_cycle.parse_args(["--run", "--watch", "--config", str(config_path)]), deps=fake.as_deps())

        joined_commands = [" ".join(command) for command in fake.commands]
        execute_index = next(index for index, command in enumerate(joined_commands) if "execute_next_action.py" in command)
        readiness_indexes = [index for index, command in enumerate(joined_commands) if "diagnose_live_readiness.py" in command]
        self.assertEqual(payload["status"], "PASS")
        self.assertTrue(payload["ready"])
        self.assertGreater(execute_index, readiness_indexes[-1])
        self.assertEqual(payload["trace"]["newDecisionCount"], 1)
        self.assertEqual(payload["trace"]["decisionCounts"], {"advance": 1})

    def test_watch_run_invokes_auto_login_for_manual_login_then_resumes_polling(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "dev_cycle.local.json"
            trace_path = Path(tmp) / "navigation_decisions.jsonl"
            write_config(config_path, trace_path)
            fake = FakeDeps(readiness=[blocked_login_readiness(), ready_readiness()])

            payload = dev_cycle.run_dev_cycle(dev_cycle.parse_args(["--run", "--watch", "--config", str(config_path)]), deps=fake.as_deps())

        joined_commands = [" ".join(command) for command in fake.commands]
        auto_index = next(index for index, command in enumerate(joined_commands) if "context_service.py" in command and "--ensure-loaded-scene" in command)
        readiness_indexes = [index for index, command in enumerate(joined_commands) if "diagnose_live_readiness.py" in command]
        execute_index = next(index for index, command in enumerate(joined_commands) if "execute_next_action.py" in command)
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(fake.auto_login_calls, 1)
        self.assertGreater(auto_index, readiness_indexes[0])
        self.assertGreater(readiness_indexes[-1], auto_index)
        self.assertGreater(execute_index, readiness_indexes[-1])
        self.assertEqual(payload["autoLogin"]["attempts"][0]["status"], "recovered_loaded_scene")

    def test_watch_run_auto_login_success_allows_executor_when_readiness_becomes_safe(self):
        trace_record = {
            "schema": "navigation_decision_trace.v1",
            "decision": "wait",
            "reason": "player_still_moving_to_clicked_waypoint",
            "observed": {"observedResult": "service_navigation_clicked_waiting", "nextActionAllowed": False},
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "dev_cycle.local.json"
            trace_path = Path(tmp) / "navigation_decisions.jsonl"
            write_config(config_path, trace_path)
            fake = FakeDeps(readiness=[blocked_login_readiness(), ready_readiness()], trace_records=[trace_record])

            payload = dev_cycle.run_dev_cycle(dev_cycle.parse_args(["--run", "--watch", "--config", str(config_path)]), deps=fake.as_deps())

        self.assertEqual(payload["status"], "PASS")
        self.assertTrue(any("execute_next_action.py" in " ".join(command) for command in fake.commands))
        self.assertEqual(payload["trace"]["newDecisionCount"], 1)
        self.assertEqual(payload["trace"]["decisionCounts"], {"wait": 1})

    def test_watch_run_auto_login_failure_reports_blocker_without_executor(self):
        failed_recovery = {
            "status": "manual_login_required",
            "loadedSceneVerified": False,
            "blocker": "manual_login_required",
            "_returncode": 2,
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "dev_cycle.local.json"
            trace_path = Path(tmp) / "navigation_decisions.jsonl"
            write_config(config_path, trace_path)
            fake = FakeDeps(readiness=[blocked_login_readiness()], auto_login_results=failed_recovery)

            payload = dev_cycle.run_dev_cycle(dev_cycle.parse_args(["--run", "--watch", "--config", str(config_path)]), deps=fake.as_deps())

        self.assertEqual(payload["status"], "BLOCKED")
        self.assertEqual(payload["blocker"]["category"], "auto_login_failed")
        self.assertEqual(payload["blocker"]["scriptBlocker"], "manual_login_required")
        self.assertEqual(fake.auto_login_calls, 1)
        self.assertFalse(any("execute_next_action.py" in " ".join(command) for command in fake.commands))

    def test_watch_run_auto_login_missing_reports_discovery_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "dev_cycle.local.json"
            trace_path = Path(tmp) / "navigation_decisions.jsonl"
            write_config(config_path, trace_path)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["auto_login_command"] = []
            config_path.write_text(json.dumps(config), encoding="utf-8")
            fake = FakeDeps(readiness=[blocked_login_readiness()])

            payload = dev_cycle.run_dev_cycle(dev_cycle.parse_args(["--run", "--watch", "--config", str(config_path)]), deps=fake.as_deps())

        self.assertEqual(payload["status"], "BLOCKED")
        self.assertEqual(payload["blocker"]["category"], "auto_login_missing")
        self.assertIn("configured but empty", payload["blocker"]["reason"])
        self.assertEqual(fake.auto_login_calls, 0)
        self.assertFalse(any("execute_next_action.py" in " ".join(command) for command in fake.commands))

    def test_watch_run_auto_login_max_attempts_reached_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "dev_cycle.local.json"
            trace_path = Path(tmp) / "navigation_decisions.jsonl"
            write_config(config_path, trace_path)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["auto_login_max_attempts"] = 1
            config_path.write_text(json.dumps(config), encoding="utf-8")
            fake = FakeDeps(readiness=[blocked_login_readiness(), blocked_login_readiness()])

            payload = dev_cycle.run_dev_cycle(dev_cycle.parse_args(["--run", "--watch", "--config", str(config_path)]), deps=fake.as_deps())

        self.assertEqual(payload["status"], "BLOCKED")
        self.assertEqual(payload["blocker"]["category"], "auto_login_max_attempts_reached")
        self.assertEqual(fake.auto_login_calls, 1)
        self.assertFalse(any("execute_next_action.py" in " ".join(command) for command in fake.commands))

    def test_watch_dry_run_reports_auto_login_without_invoking_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "dev_cycle.local.json"
            trace_path = Path(tmp) / "navigation_decisions.jsonl"
            write_config(config_path, trace_path)
            fake = FakeDeps(readiness=[blocked_login_readiness(), ready_readiness()])

            payload = dev_cycle.run_dev_cycle(dev_cycle.parse_args(["--dry-run", "--watch", "--use-auto-login", "--config", str(config_path)]), deps=fake.as_deps())

        self.assertEqual(payload["status"], "PASS")
        self.assertTrue(payload["autoLogin"]["enabled"])
        self.assertFalse(payload["autoLogin"]["invokeAllowed"])
        self.assertEqual(fake.auto_login_calls, 0)
        self.assertTrue(payload["watch"]["events"][0]["autoLoginWouldAttempt"])
        self.assertFalse(any("execute_next_action.py" in " ".join(command) for command in fake.commands))

    def test_auto_login_logs_redact_secret_like_command_and_output(self):
        recovery = {
            "status": "manual_login_required",
            "loadedSceneVerified": False,
            "blocker": "manual_login_required",
            "_returncode": 2,
            "_stdout": "password=fake-secret-value\nstatus=manual_login_required",
            "_stderr": "token=fake-secret-value",
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "dev_cycle.local.json"
            trace_path = Path(tmp) / "navigation_decisions.jsonl"
            write_config(config_path, trace_path)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["auto_login_command"] = ["python", "recover.py", "--password", "fake-secret-value"]
            config_path.write_text(json.dumps(config), encoding="utf-8")
            fake = FakeDeps(readiness=[blocked_login_readiness()], auto_login_results=recovery)

            payload = dev_cycle.run_dev_cycle(dev_cycle.parse_args(["--run", "--watch", "--config", str(config_path)]), deps=fake.as_deps())

        rendered = json.dumps(payload)
        self.assertNotIn("fake-secret-value", rendered)
        self.assertIn("<redacted>", rendered)
        self.assertIn("<redacted sensitive line>", rendered)

    def test_watch_timeout_reports_last_blocker_without_executor(self):
        blocked = {
            "status": "FAIL",
            "ready": False,
            "manualLoginRequired": True,
            "unknownScreen": False,
            "livenessState": "login_screen",
            "loadedSceneProof": {"loadedSceneVerified": False, "gameState": "LOGIN_SCREEN"},
            "daemon": {"latestTick": None, "sessionPath": "session-a"},
            "actionReadiness": {"executionAllowed": False},
            "actionExecution": {"allowed": False},
            "blockers": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "dev_cycle.local.json"
            trace_path = Path(tmp) / "navigation_decisions.jsonl"
            write_config(config_path, trace_path)
            fake = FakeDeps(readiness=[blocked])

            payload = dev_cycle.run_dev_cycle(
                dev_cycle.parse_args(["--run", "--watch", "--no-auto-login", "--max-wait-seconds", "2", "--poll-seconds", "1", "--config", str(config_path)]),
                deps=fake.as_deps(),
            )

        self.assertEqual(payload["status"], "BLOCKED")
        self.assertEqual(payload["blocker"]["category"], "watch_timeout")
        self.assertEqual(payload["blocker"]["lastBlocker"]["category"], "manual_login_required")
        self.assertTrue(payload["watch"]["timedOut"])
        self.assertEqual(fake.sleep_calls, [1.0, 1.0])
        self.assertFalse(any("execute_next_action.py" in " ".join(command) for command in fake.commands))


if __name__ == "__main__":
    unittest.main()

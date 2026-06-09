import json
import io
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import bot_eval_runner


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def make_full_loop_recording(root: Path) -> Path:
    recording = root / "recording"
    loop = {
        "schema": "woodcutting_loop_lifecycle.v1",
        "status": "PASS",
        "loopState": "complete",
        "confidence": 0.95,
        "currentPhase": {"phase": "complete"},
        "nextExpectedPhase": {"phase": "continue_current_phase"},
        "woodcutting": {
            "status": "PASS",
            "phase": "idle",
            "normalLogsGained": 28,
            "cycleLogsGained": 28,
            "inventoryFull": True,
            "inventoryFilledDuringLoop": True,
            "freshChopClickCount": 4,
            "activeSnapshotCount": 8,
        },
        "banking": {
            "status": "PASS",
            "phase": "bank_open",
            "confidence": 0.95,
            "bankLikeInterface": "bank",
            "bankOpenSeen": True,
            "bankContainerAvailable": True,
            "bankContainerDeltaAvailable": True,
            "depositDetected": True,
            "depositedItems": [{"id": 1511, "name": "Logs", "quantity": 28}],
            "depositedItemCount": 28,
            "depositConfirmationLevel": "bank_container_delta_confirmed",
            "missingCapabilities": [],
            "warnings": [],
        },
        "routes": {
            "status": "PASS",
            "direction": "multi_leg_loop",
            "routeLegs": [
                {
                    "phase": "route_to_bank",
                    "routeName": "woodcutting_area_to_bank",
                    "fromArea": "woodcutting_area",
                    "toArea": "bank_area",
                    "status": "PASS",
                    "matched": True,
                },
                {
                    "phase": "route_to_trees",
                    "routeName": "Bank_to_Woodcutting_area",
                    "fromArea": "bank_area",
                    "toArea": "woodcutting_area",
                    "status": "PASS",
                    "matched": True,
                },
            ],
        },
        "interruptions": {"status": "PASS", "interruptionDetected": False, "taskResumed": True},
        "warnings": [],
        "missingCapabilities": [],
    }
    write_json(recording / "woodcutting_loop_lifecycle.json", loop)
    write_json(recording / "banking_lifecycle.json", loop["banking"])
    write_json(recording / "woodcutting_lifecycle.json", loop["woodcutting"])
    write_json(recording / "combat_damage_summary.json", {"schema": "combat_damage_summary.v1", "status": "PASS", "combatObserved": False})
    write_json(recording / "human_click_profile.json", {"schema": "human_click_profile.v1", "status": "PASS", "recordingCount": 1})
    return recording


def make_live_session(root: Path) -> Path:
    session = root / "sessions" / "20260607_210000"
    live = session / "interaction_geometry" / "live"
    generated = datetime.now(timezone.utc).isoformat()
    write_json(
        live / "live_status.json",
        {
            "schema": "live_status.v1",
            "generatedAtUtc": generated,
            "pluginSnapshotLatestTick": 123,
            "latestExportSeq": 456,
            "profile": "woodcutting",
            "clientTickHot": {
                "gameState": "LOGGED_IN",
                "wallTimeMillis": int(datetime.now(timezone.utc).timestamp() * 1000),
                "gameTickAtSample": 123,
            },
            "worldModelObjectTotal": 12,
        },
    )
    write_json(
        live / "live_context_index.json",
        {
            "schema": "live_context_index.v1",
            "generatedAtUtc": generated,
            "latestTick": 123,
            "activeProfile": "woodcutting",
            "bestCandidateByClassId": {
                "tree": {
                    "name": "Tree",
                    "classId": "tree",
                    "qualityScore": 100,
                    "targetType": "sceneObject",
                    "onScreen": True,
                    "geometryAvailable": True,
                    "aimPoint": {"x": 200, "y": 150},
                }
            },
        },
    )
    write_json(
        live / "live_baseline_state.json",
        {
            "schema": "live_baseline_state.v1",
            "generatedAtUtc": generated,
            "latestTick": 123,
            "gameState": "LOGGED_IN",
            "player": {"worldX": 3200, "worldY": 3200, "plane": 0},
            "sceneCache": {"presentObjectCount": 12},
            "inputGeometry": {
                "schema": "input_geometry.v1",
                "geometryAvailable": True,
                "reason": "available",
                "canvasScreenX": 100,
                "canvasScreenY": 200,
                "canvasWidth": 800,
                "canvasHeight": 600,
                "clientWindowX": 90,
                "clientWindowY": 180,
                "clientWindowWidth": 840,
                "clientWindowHeight": 650,
                "isCanvasShowing": True,
                "isClientFocused": True,
            },
        },
    )
    return session.parent


def fake_fetcher(payloads: dict[str, dict]):
    calls: list[tuple[str, float]] = []

    def fetch(url: str, timeout: float) -> dict:
        calls.append((url, timeout))
        payload = payloads.get(url)
        if payload is None:
            return {"ok": False, "url": url, "elapsedMs": round(timeout * 1000, 3), "payload": {}, "error": "TimeoutError: timed out"}
        return {"ok": True, "url": url, "elapsedMs": 1.0, "payload": payload, "error": None}

    fetch.calls = calls
    return fetch


def fake_poster(payload: dict | None = None):
    calls: list[tuple[str, dict, float]] = []

    def post(url: str, request_payload: dict, timeout: float) -> dict:
        calls.append((url, request_payload, timeout))
        response_payload = payload or {
            "schema": "runtime_control_result.v1",
            "status": "PASS",
            "acceptedFields": ["missionPreset", "taskPolicy", "goalCount", "brainEnabled", "observeOnly", "resetBrainState"],
            "state": {
                "schema": "runtime_control.v1",
                "activeTask": "woodcutting",
                "taskPolicy": "woodcutting_bank",
                "goalCount": None,
                "brainEnabled": True,
                "observeOnly": False,
            },
        }
        return {"ok": True, "url": url, "elapsedMs": 1.0, "payload": response_payload, "error": None}

    post.calls = calls
    return post


def focused_runelite_window_status() -> dict:
    return {
        "schema": "runelite_window_status.v1",
        "status": "PASS",
        "available": True,
        "runeliteWindowMatched": True,
        "foregroundWindowTitle": "RuneLite - Unit Test",
        "foregroundHwnd": 1001,
        "matchedWindow": {
            "hwnd": 1001,
            "title": "RuneLite - Unit Test",
            "visible": True,
            "minimized": False,
            "foreground": True,
            "windowRect": {"x": 90, "y": 180, "width": 840, "height": 650, "left": 90, "top": 180, "right": 930, "bottom": 830},
            "clientRect": {"x": 100, "y": 200, "width": 800, "height": 600, "left": 100, "top": 200, "right": 900, "bottom": 800},
            "screenToClientAvailable": True,
            "clientToScreenAvailable": True,
            "screenClientRoundTrip": {
                "clientPoint": {"x": 10, "y": 10},
                "screenPoint": {"x": 110, "y": 210},
                "roundTripClientPoint": {"x": 10, "y": 10},
            },
            "dpi": 96,
            "dpiScale": 1.0,
        },
        "matchedWindowCount": 1,
        "warnings": [],
    }


def focused_runelite_window_patch():
    return patch("input_control.input_geometry.find_runelite_window", return_value=focused_runelite_window_status())


class BotEvalRunnerTest(unittest.TestCase):
    def test_replay_full_loop_writes_decision_action_and_postcondition_traces(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = make_full_loop_recording(root)
            summary = bot_eval_runner.run_evaluation(
                recording=recording,
                output_root=root / "bot_runs",
                task="woodcutting_loop",
                max_actions=100,
            )

            self.assertEqual(summary["schema"], "bot_eval_summary.v1")
            self.assertEqual(summary["status"], "PASS")
            self.assertEqual(summary["decisionMismatchCount"], 0)
            self.assertGreaterEqual(summary["decisionCount"], 6)
            artifacts = summary["artifacts"]
            for key in ("manifest", "decisions", "actions", "observations", "postconditions", "summary"):
                self.assertTrue(Path(artifacts[key]).exists(), key)

    def test_replay_full_loop_uses_lifecycle_state_for_phase_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = make_full_loop_recording(root)
            summary = bot_eval_runner.run_evaluation(recording=recording, output_root=root / "bot_runs")
            decisions = [
                json.loads(line)
                for line in Path(summary["artifacts"]["decisions"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            by_phase = {item["phase"]: item for item in decisions}

            self.assertEqual(by_phase["inventory_full"]["decisionChosen"], "bank")
            self.assertEqual(by_phase["banking_deposit"]["decisionChosen"], "deposit")
            self.assertEqual(by_phase["deposit_complete"]["decisionChosen"], "return_to_resource")
            self.assertEqual(by_phase["route_to_trees"]["decisionChosen"], "return_to_resource")
            self.assertEqual(by_phase["resumed_cutting"]["decisionChosen"], "collect")

    def test_replay_mode_never_sends_live_input_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = make_full_loop_recording(root)
            summary = bot_eval_runner.run_evaluation(recording=recording, output_root=root / "bot_runs")
            actions = [
                json.loads(line)
                for line in Path(summary["artifacts"]["actions"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertTrue(actions)
            self.assertTrue(all(item["inputMode"] == "no_live_input" for item in actions))
            self.assertTrue(all(item["commandSent"] is None for item in actions))

    def test_context_health_timeout_produces_fail_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetch = fake_fetcher({})
            readiness = bot_eval_runner.check_live_readiness(
                fetcher=fetch,
                sessions_root=Path(tmp) / "missing_sessions",
                timeout=0.123,
            )

            self.assertEqual(readiness["schema"], "bot_live_readiness.v1")
            self.assertEqual(readiness["status"], "FAIL")
            self.assertFalse(readiness["contextServiceReachable"])
            self.assertIn("context_health_unreachable_or_unresponsive", readiness["rootCause"])
            self.assertTrue(any(call[1] == 0.123 for call in fetch.calls))
            self.assertFalse(any(call[0].endswith("/status") for call in fetch.calls))

    def test_stale_telemetry_produces_warn_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_root = make_live_session(Path(tmp))
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 99_999},
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS"},
                }
            )
            with focused_runelite_window_patch():
                readiness = bot_eval_runner.check_live_readiness(fetcher=fetch, sessions_root=sessions_root)

            self.assertEqual(readiness["status"], "WARN")
            self.assertTrue(readiness["contextServiceReachable"])
            self.assertFalse(readiness["telemetryFresh"])
            self.assertEqual(readiness["rootCause"], "telemetry_stale")

    def test_readiness_pass_permits_bounded_eval_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_root = make_live_session(Path(tmp))
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 100, "latestTick": 123},
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS"},
                }
            )
            with focused_runelite_window_patch():
                readiness = bot_eval_runner.check_live_readiness(fetcher=fetch, sessions_root=sessions_root)

            self.assertEqual(readiness["status"], "PASS")
            self.assertTrue(readiness["telemetryFresh"])
            self.assertTrue(readiness["routeTemplateLoaded"])
            self.assertTrue(readiness["taskStateReadable"])
            self.assertTrue(readiness["liveEvalCanStart"])

    def test_readiness_uses_bounded_status_fallback_when_health_lacks_scene_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_root = make_live_session(Path(tmp))
            live_status = next(sessions_root.iterdir()) / "interaction_geometry" / "live" / "live_status.json"
            payload = json.loads(live_status.read_text(encoding="utf-8"))
            payload.update({"clientTickHot": {}, "worldModelObjectTotal": None})
            write_json(live_status, payload)
            baseline_path = next(sessions_root.iterdir()) / "interaction_geometry" / "live" / "live_baseline_state.json"
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            baseline.pop("gameState", None)
            baseline["player"] = {}
            baseline["sceneCache"] = {}
            write_json(baseline_path, baseline)
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {
                        "schema": "context_health.v1",
                        "status": "ok",
                        "latestTick": 500,
                        "liveFreshness": {"latestTick": 500, "freshByTicks": True, "freshByMillis": True},
                    },
                    "http://127.0.0.1:8890/status": {
                        "schema": "context_status.v1",
                        "status": "ok",
                        "latestTick": 500,
                        "brain": {
                            "latestTick": 500,
                            "freshnessDomains": {"inventoryFreshness": "fresh"},
                            "inventoryContext": {"freeSlots": 0, "inventoryFull": True},
                            "serviceRouteContext": {
                                "status": "PASS",
                                "sourceTick": 500,
                                "routeAvailable": True,
                                "currentNodeId": "lumbridge_castle_bank",
                            },
                        },
                    },
                    "http://127.0.0.1:8893/health": {
                        "schema": "snapshot_health.v1",
                        "status": "PASS",
                        "latestTick": 500,
                        "cacheWallClockFresh": True,
                    },
                }
            )

            with focused_runelite_window_patch():
                readiness = bot_eval_runner.check_live_readiness(fetcher=fetch, sessions_root=sessions_root, timeout=0.25)

            status_call = next(call for call in fetch.calls if call[0].endswith("/status"))
            self.assertGreaterEqual(status_call[1], bot_eval_runner.DEFAULT_STATUS_DIAGNOSTIC_TIMEOUT_SECONDS)
            self.assertEqual(readiness["status"], "PASS")
            self.assertTrue(readiness["gameClientLoaded"])
            self.assertTrue(readiness["liveEvalCanStart"])
            self.assertEqual(readiness["endpointChecks"]["daemonStatus"]["ok"], True)

    def test_input_geometry_check_reports_canvas_window_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_root = make_live_session(Path(tmp))
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 100, "latestTick": 123},
                    "http://127.0.0.1:8890/status": {"schema": "context_status.v1", "status": "ok", "latestTick": 123},
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS", "latestTick": 123},
                }
            )
            with patch("input_control.input_geometry.find_runelite_window", return_value={"runeliteWindowMatched": False, "matchedWindow": None}):
                summary = bot_eval_runner.run_input_geometry_check(fetcher=fetch, sessions_root=sessions_root)

            self.assertEqual(summary["schema"], "bot_input_geometry_check.v1")
            self.assertEqual(summary["status"], "PASS")
            self.assertTrue(summary["loadedSceneProof"]["loadedSceneVerified"])
            geometry = summary["inputGeometry"]
            self.assertEqual(geometry["status"], "PASS")
            self.assertEqual(geometry["canvasWidth"], 800)
            self.assertEqual(geometry["canvasHeight"], 600)
            self.assertIsNotNone(geometry["clientRect"])
            self.assertTrue(geometry["screenToClientAvailable"])
            self.assertTrue(geometry["clientToScreenAvailable"])

    def test_input_geometry_check_uses_status_diagnostic_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_root = make_live_session(Path(tmp))
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 100, "latestTick": 123},
                    "http://127.0.0.1:8890/status": {"schema": "context_status.v1", "status": "ok", "latestTick": 123},
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS", "latestTick": 123},
                }
            )
            with patch("input_control.input_geometry.find_runelite_window", return_value={"runeliteWindowMatched": False, "matchedWindow": None}):
                summary = bot_eval_runner.run_input_geometry_check(fetcher=fetch, sessions_root=sessions_root, timeout=0.75)

            status_call = next(call for call in fetch.calls if call[0].endswith("/status"))
            self.assertGreaterEqual(status_call[1], bot_eval_runner.DEFAULT_STATUS_DIAGNOSTIC_TIMEOUT_SECONDS)
            self.assertEqual(summary["statusDiagnosticTimeoutSeconds"], bot_eval_runner.DEFAULT_STATUS_DIAGNOSTIC_TIMEOUT_SECONDS)

    def test_input_geometry_check_loaded_scene_uses_status_request_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_root = make_live_session(Path(tmp))
            request_started = 1_000.0
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 100, "latestTick": 123},
                    "http://127.0.0.1:8890/status": {
                        "schema": "context_status.v1",
                        "status": "ok",
                        "latestTick": 123,
                        "worldModelObjectTotal": 12,
                        "clientTickHot": {
                            "gameState": "LOGGED_IN",
                            "wallTimeMillis": int((request_started - 0.5) * 1000),
                        },
                    },
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS", "latestTick": 123},
                }
            )
            with patch("input_control.input_geometry.find_runelite_window", return_value={"runeliteWindowMatched": False, "matchedWindow": None}):
                summary = bot_eval_runner.run_input_geometry_check(fetcher=fetch, sessions_root=sessions_root, now=request_started)

            self.assertEqual(summary["status"], "PASS")
            self.assertTrue(summary["loadedSceneProof"]["loadedSceneVerified"])

    def test_loaded_scene_proof_accepts_fresh_live_context_over_stale_hot_tick(self):
        now = 1_000.0
        status = {
            "schema": "context_status.v1",
            "status": "ok",
            "latestTick": 500,
            "clientTickHot": {
                "gameState": "LOGIN_SCREEN",
                "wallTimeMillis": int((now - 30.0) * 1000),
            },
            "brain": {
                "latestTick": 500,
                "freshnessDomains": {"inventoryFreshness": "fresh"},
                "inventoryContext": {"freeSlots": 0, "inventoryFull": True},
                "serviceRouteContext": {
                    "status": "PASS",
                    "sourceTick": 500,
                    "routeAvailable": True,
                    "currentNodeId": "lumbridge_castle_bank",
                },
            },
        }

        blockers = bot_eval_runner._loaded_scene_blockers(status, now=now)

        self.assertEqual(blockers, [])
        self.assertTrue(bot_eval_runner._game_client_loaded(status, now=now))

    def test_input_geometry_check_accepts_fresh_live_context_over_stale_hot_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = 1_000.0
            sessions_root = Path(tmp) / "missing_sessions"
            status_payload = {
                "schema": "context_status.v1",
                "status": "ok",
                "latestTick": 500,
                "clientTickHot": {
                    "gameState": "LOGIN_SCREEN",
                    "wallTimeMillis": int((now - 30.0) * 1000),
                },
                "inputGeometry": {
                    "schema": "input_geometry.v1",
                    "status": "PASS",
                    "geometryAvailable": True,
                    "canvasScreenOrigin": {"x": 100, "y": 200},
                    "canvasSize": {"width": 800, "height": 600},
                    "sourceCanvasSize": {"width": 800, "height": 600},
                    "clientWindowBounds": {"x": 90, "y": 180, "width": 840, "height": 650},
                    "isCanvasShowing": True,
                    "isClientFocused": True,
                    "sourceTick": 500,
                },
                "brain": {
                    "latestTick": 500,
                    "freshnessDomains": {"inventoryFreshness": "fresh"},
                    "inventoryContext": {"freeSlots": 0, "inventoryFull": True},
                    "serviceRouteContext": {
                        "status": "PASS",
                        "sourceTick": 500,
                        "routeAvailable": True,
                        "currentNodeId": "lumbridge_castle_bank",
                    },
                },
            }
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 100, "latestTick": 500},
                    "http://127.0.0.1:8890/status": status_payload,
                    "http://127.0.0.1:8893/health": {
                        "schema": "snapshot_health.v1",
                        "status": "PASS",
                        "latestTick": 500,
                        "cacheWallClockFresh": True,
                    },
                }
            )
            with patch("input_control.input_geometry.find_runelite_window", return_value={"runeliteWindowMatched": False, "matchedWindow": None}):
                summary = bot_eval_runner.run_input_geometry_check(fetcher=fetch, sessions_root=sessions_root, now=now)

            self.assertEqual(summary["status"], "PASS")
            self.assertTrue(summary["loadedSceneProof"]["loadedSceneVerified"])
            self.assertTrue(summary["inputGeometryPass"])
            self.assertEqual(summary["loadedSceneProof"]["blockers"], [])

    def test_live_action_does_not_start_executor_when_geometry_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions_root = make_live_session(root)
            baseline_path = sessions_root / "20260607_210000" / "interaction_geometry" / "live" / "live_baseline_state.json"
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            baseline["inputGeometry"]["canvasWidth"] = 0
            baseline["inputGeometry"]["canvasHeight"] = 0
            write_json(baseline_path, baseline)
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 100, "latestTick": 123},
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS", "latestTick": 123},
                }
            )

            def forbidden_runner(*_args, **_kwargs):
                raise AssertionError("executor should not run when geometry fails")

            with patch("input_control.input_geometry.find_runelite_window", return_value={"runeliteWindowMatched": False, "matchedWindow": None}):
                summary = bot_eval_runner.run_live_action(
                    output_root=root / "bot_runs",
                    sessions_root=sessions_root,
                    fetcher=fetch,
                    command_runner=forbidden_runner,
                    runtime_control_poster=fake_poster(),
                    record_everything=False,
                    analyze_after=False,
                    require_readiness_pass=True,
                )

            self.assertEqual(summary["status"], "FAIL")
            self.assertFalse(summary["liveInputExecuted"])
            self.assertIn("input_geometry_canvas_missing", summary["errors"])

    def test_readiness_uses_context_health_live_freshness_when_disk_age_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_sessions = Path(tmp) / "missing_sessions"
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {
                        "schema": "context_health.v1",
                        "status": "ok",
                        "sessionPath": "C:/sessions/live",
                        "latestTick": 217,
                        "liveFreshness": {"liveFileAgeMillis": 804.653, "freshByMillis": True},
                        "clientTickHot": {"gameState": "LOGGED_IN"},
                    },
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS", "latestTick": 217},
                }
            )

            readiness = bot_eval_runner.check_live_readiness(fetcher=fetch, sessions_root=missing_sessions)

            self.assertEqual(readiness["status"], "PASS")
            self.assertEqual(readiness["telemetryAgeMs"], 804)
            self.assertTrue(readiness["telemetryFresh"])
            self.assertTrue(readiness["liveEvalCanStart"])

    def test_readiness_accepts_fresh_plugin_snapshot_when_live_files_are_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_root = make_live_session(Path(tmp))
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {
                        "schema": "context_health.v1",
                        "status": "ok",
                        "latestTick": 1810,
                        "liveFreshness": {
                            "latestTick": 1810,
                            "candidateTick": 1810,
                            "tickDelta": 0,
                            "liveFileAgeMillis": 11_009,
                            "freshByTicks": True,
                            "freshByMillis": False,
                        },
                        "inputSourceActive": "plugin-snapshot",
                        "clientTickHot": {"gameState": "LOGGED_IN"},
                    },
                    "http://127.0.0.1:8893/health": {
                        "schema": "plugin_snapshot_health.v1",
                        "status": "PASS",
                        "latestTick": 1829,
                        "cacheWallClockFresh": True,
                        "maxCacheAgeMillis": 622,
                    },
                }
            )

            with focused_runelite_window_patch():
                readiness = bot_eval_runner.check_live_readiness(fetcher=fetch, sessions_root=sessions_root)

            self.assertEqual(readiness["status"], "PASS")
            self.assertTrue(readiness["telemetryFresh"])
            self.assertEqual(readiness["telemetryAgeMs"], 622)
            self.assertEqual(readiness["telemetryFreshnessSource"], "plugin_snapshot_cache_wall_clock_fresh")
            self.assertTrue(readiness["liveEvalCanStart"])
            self.assertTrue(any("plugin_snapshot_cache_wall_clock_fresh" in note for note in readiness["notes"]))

    def test_readiness_allows_plugin_snapshot_fallback_when_context_health_times_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_root = make_live_session(Path(tmp))
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8893/health": {
                        "schema": "plugin_snapshot_health.v1",
                        "status": "PASS",
                        "latestTick": 1831,
                        "cacheWallClockFresh": True,
                        "maxCacheAgeMillis": 83,
                        "clientTickHot": {"gameState": "LOGGED_IN"},
                    },
                }
            )

            with focused_runelite_window_patch():
                readiness = bot_eval_runner.check_live_readiness(fetcher=fetch, sessions_root=sessions_root, no_input=False)

            self.assertEqual(readiness["status"], "PASS")
            self.assertFalse(readiness["contextServiceReachable"])
            self.assertTrue(readiness["contextFallbackActive"])
            self.assertTrue(readiness["telemetryFresh"])
            self.assertTrue(readiness["loadedSceneReady"])
            self.assertTrue(readiness["liveEvalCanStart"])
            self.assertIsNone(readiness["rootCause"])
            self.assertTrue(any("plugin snapshot executor fallback" in note for note in readiness["notes"]))

    def test_login_screen_hot_state_blocks_live_eval_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions_root = make_live_session(root)
            live_status = next((sessions_root).iterdir()) / "interaction_geometry" / "live" / "live_status.json"
            payload = json.loads(live_status.read_text(encoding="utf-8"))
            payload.update(
                {
                    "pluginSnapshotLatestTick": 514,
                    "clientTickHot": {
                        "gameState": "LOGIN_SCREEN",
                        "wallTimeMillis": 0,
                        "gameTickAtSample": 514,
                    },
                    "screenClassification": "login_screen_or_disconnected_dialog",
                    "worldModelObjectTotal": 0,
                }
            )
            write_json(live_status, payload)
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 100, "latestTick": 514},
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS"},
                }
            )

            with focused_runelite_window_patch():
                readiness = bot_eval_runner.check_live_readiness(fetcher=fetch, sessions_root=sessions_root)

            self.assertEqual(readiness["status"], "FAIL")
            self.assertEqual(readiness["rootCause"], "loaded_scene_not_ready")
            self.assertFalse(readiness["loadedSceneReady"])
            self.assertFalse(readiness["liveEvalCanStart"])
            self.assertIn("client_tick_hot_game_state_login_screen", readiness["loadedSceneBlockers"])
            self.assertTrue(any("loaded_scene_not_ready" in error for error in readiness["errors"]))

    def test_tick_only_freshness_does_not_prove_loaded_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {
                        "schema": "context_health.v1",
                        "status": "ok",
                        "latestTick": 6161,
                        "liveFreshness": {"latestTick": 6161, "candidateTick": 6161, "tickDelta": 0, "freshByTicks": True},
                    },
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS", "latestTick": 6161},
                }
            )

            readiness = bot_eval_runner.check_live_readiness(fetcher=fetch, sessions_root=Path(tmp) / "missing_sessions")

            self.assertEqual(readiness["status"], "FAIL")
            self.assertEqual(readiness["rootCause"], "loaded_scene_not_ready")
            self.assertFalse(readiness["loadedSceneReady"])
            self.assertFalse(readiness["liveEvalCanStart"])

    def test_live_smoke_writes_readiness_and_sends_no_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions_root = make_live_session(root)
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 100, "latestTick": 123},
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS"},
                }
            )
            summary = bot_eval_runner.run_live_smoke(
                output_root=root / "bot_runs",
                duration=0,
                sessions_root=sessions_root,
                fetcher=fetch,
                no_input=True,
                dry_run_actions=True,
            )

            self.assertIn(summary["status"], {"PASS", "WARN"})
            self.assertEqual(summary["mode"], "live_smoke")
            self.assertEqual(summary["actionCommandsSent"], 0)
            self.assertFalse(summary["liveInputExecuted"])
            self.assertTrue(Path(summary["artifacts"]["readiness"]).exists())
            self.assertTrue(Path(summary["artifacts"]["observations"]).exists())
            self.assertTrue(Path(summary["artifacts"]["actions"]).exists())

    def test_live_without_execute_actions_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with patch("sys.stdout", output):
                code = bot_eval_runner.main(["--live", "--duration", "0", "--out-dir", str(Path(tmp) / "bot_runs"), "--json"])

            self.assertEqual(code, 1)
            summary = json.loads(output.getvalue())
            self.assertEqual(summary["status"], "FAIL")
            self.assertEqual(summary["mode"], "live_requires_execute_actions")
            self.assertFalse(summary["liveInputExecuted"])
            self.assertEqual(summary["actionCommandsSent"], 0)
            self.assertIn("live_requires_execute_actions", summary["errors"])
            self.assertIn(bot_eval_runner.LIVE_NOT_REAL_ACTION_WARNING, summary["warnings"])

    def test_live_action_rejects_conflicting_dry_run_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with patch("sys.stdout", output):
                code = bot_eval_runner.main(
                    [
                        "--live",
                        "--execute-actions",
                        "--dry-run-actions",
                        "--duration",
                        "0",
                        "--out-dir",
                        str(Path(tmp) / "bot_runs"),
                        "--json",
                    ]
                )

            self.assertEqual(code, 1)
            summary = json.loads(output.getvalue())
            self.assertEqual(summary["status"], "FAIL")
            self.assertEqual(summary["mode"], "live_action_conflicting_dry_run_flags")
            self.assertFalse(summary["liveInputExecuted"])
            self.assertIn("live_action_conflicting_dry_run_flags", summary["errors"])
            self.assertIn(bot_eval_runner.LIVE_NOT_REAL_ACTION_WARNING, summary["warnings"])

    def test_live_smoke_missing_context_service_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetch = fake_fetcher({})
            summary = bot_eval_runner.run_live_smoke(
                output_root=Path(tmp) / "bot_runs",
                duration=0,
                fetcher=fetch,
            )

            self.assertEqual(summary["status"], "FAIL")
            self.assertFalse(summary["readiness"]["contextServiceReachable"])
            self.assertTrue(summary["errors"])

    def test_manual_loaded_scene_wait_returns_pass_when_loaded_scene_appears(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions_root = make_live_session(root)
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 100, "latestTick": 123},
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS"},
                }
            )

            with focused_runelite_window_patch():
                summary = bot_eval_runner.wait_for_manual_loaded_scene_ready(
                    root / "bot_runs" / "manual_wait",
                    timeout_seconds=0,
                    sessions_root=sessions_root,
                    fetcher=fetch,
                    sleep_func=lambda _seconds: None,
                )

            self.assertEqual(summary["status"], "PASS")
            self.assertEqual(summary["reason"], "manual_loaded_scene_detected")
            self.assertTrue(Path(summary["tracePath"]).exists())

    def test_manual_loaded_scene_wait_timeout_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions_root = make_live_session(root)
            live_status = next((sessions_root).iterdir()) / "interaction_geometry" / "live" / "live_status.json"
            payload = json.loads(live_status.read_text(encoding="utf-8"))
            payload.update(
                {
                    "clientTickHot": {"gameState": "LOGIN_SCREEN", "wallTimeMillis": 0},
                    "screenClassification": "login_screen_or_disconnected_dialog",
                    "worldModelObjectTotal": 0,
                }
            )
            write_json(live_status, payload)
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 100, "latestTick": 514},
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS"},
                }
            )

            summary = bot_eval_runner.wait_for_manual_loaded_scene_ready(
                root / "bot_runs" / "manual_wait",
                timeout_seconds=0,
                sessions_root=sessions_root,
                fetcher=fetch,
                sleep_func=lambda _seconds: None,
            )

            self.assertEqual(summary["status"], "FAIL")
            self.assertEqual(summary["reason"], "manual_loaded_scene_timeout")
            self.assertEqual(summary["readiness"]["rootCause"], "loaded_scene_not_ready")

    def test_live_executor_command_uses_real_arduino_execution(self):
        command = bot_eval_runner.build_live_executor_command(
            duration=1200,
            max_actions=300,
            arduino_port="COM6",
            debug_dir=Path("debug"),
        )

        joined = " ".join(command)
        self.assertIn("execute_next_action.py", joined)
        self.assertIn("--execute", command)
        self.assertIn("--loop", command)
        self.assertIn("--timeout", command)
        self.assertIn("15", command)
        self.assertIn("--backend", command)
        self.assertIn("arduino", command)
        self.assertIn("--focus-runelite", command)
        self.assertIn("--arduino-port", command)
        self.assertIn("COM6", command)
        self.assertNotIn("--dry-run", command)
        self.assertNotIn("--no-input", command)

    def test_preflight_checks_wiring_without_running_bot(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = bot_eval_runner.run_preflight(output_root=Path(tmp) / "bot_runs", arduino_port="COM6")

        self.assertEqual(summary["schema"], "bot_live_preflight.v1")
        self.assertEqual(summary["mode"], "preflight")
        self.assertTrue(summary["preflightOnly"])
        self.assertFalse(summary["liveInputExecuted"])
        self.assertIn(summary["status"], {"PASS", "WARN"})
        self.assertFalse(summary["mandatoryFailures"])
        self.assertEqual(summary["checks"]["recoveryPathAvailable"]["status"], "PASS")
        self.assertEqual(summary["checks"]["readinessPathAvailable"]["status"], "PASS")
        self.assertEqual(summary["checks"]["executorAvailable"]["status"], "PASS")
        self.assertIn("--live", summary["nextLiveCommand"])
        self.assertNotIn("--dry-run", summary["nextLiveCommand"])

    def test_live_loop_runtime_control_clears_one_inventory_goal_count(self):
        post = fake_poster()

        result = bot_eval_runner.configure_live_loop_runtime_control(poster=post)

        self.assertEqual(result["status"], "PASS")
        self.assertIsNone(result["goalCount"])
        self.assertEqual(result["taskPolicy"], "woodcutting_bank")
        self.assertTrue(post.calls)
        payload = post.calls[0][1]
        self.assertEqual(payload["missionPreset"], "woodcut_bank")
        self.assertIsNone(payload["goalCount"])
        self.assertTrue(payload["brainEnabled"])
        self.assertFalse(payload["observeOnly"])

    def test_loaded_scene_recovery_command_uses_existing_context_path(self):
        command = bot_eval_runner.build_loaded_scene_recovery_command(
            arduino_port="COM6",
            max_total_seconds=12,
            max_attempts_per_state=4,
        )

        joined = " ".join(command)
        self.assertIn("context_service.py", joined)
        self.assertIn("--ensure-loaded-scene", command)
        self.assertIn("--arduino-port", command)
        self.assertIn("COM6", command)
        self.assertIn("--liveness-max-total-seconds", command)
        self.assertIn("12.0", command)
        self.assertIn("--liveness-max-attempts-per-state", command)
        self.assertIn("4", command)

    def test_live_action_auto_recovery_writes_artifacts_and_does_not_execute_when_scene_unloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions_root = make_live_session(root)
            live_status = next((sessions_root).iterdir()) / "interaction_geometry" / "live" / "live_status.json"
            payload = json.loads(live_status.read_text(encoding="utf-8"))
            payload.update(
                {
                    "clientTickHot": {"gameState": "LOGIN_SCREEN", "wallTimeMillis": 0},
                    "screenClassification": "login_screen_or_disconnected_dialog",
                    "worldModelObjectTotal": 0,
                }
            )
            write_json(live_status, payload)
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 100, "latestTick": 514},
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS"},
                }
            )

            def fake_recovery(command, *, cwd, stdout, stderr, text, timeout):
                stdout.write(
                    json.dumps(
                        {
                            "schema": "liveness_recovery.v1",
                            "status": "unsafe",
                            "loadedSceneVerified": False,
                            "blocker": "disconnected_loop",
                            "recoveryFailureClass": "disconnected_loop",
                            "recoveryFailureReason": "safe recovery clicks were sent, but the client returned to the disconnected dialog",
                            "disconnectedLoopDetected": True,
                            "relaunchRequired": True,
                            "relaunchAttempted": True,
                            "launchMode": "dev_gradle_run",
                            "launchModeReason": "command uses the Gradle/dev RuneLite run path",
                            "launchModeWarnings": ["dev launch"],
                            "relaunchCommand": "cmd /c .\\gradlew.bat --no-daemon run",
                            "startGameCommand": "cmd /c .\\gradlew.bat --no-daemon run",
                            "startGameCommandSource": "discovered_gradle_wrapper",
                            "relaunchResult": {"status": "PASS", "relaunchSucceeded": True, "launchedProcessPid": 1234},
                            "relaunchSucceeded": True,
                            "loadedSceneAfterRelaunch": False,
                            "initialState": {"state": "disconnected_dialog"},
                            "finalState": {"state": "disconnected_dialog"},
                            "recoveryStateMachine": {
                                "schema": "liveness_recovery_state_machine.v1",
                                "failureClassification": {"failureClass": "disconnected_loop", "reason": "loop"},
                                "clickAttempts": [
                                    {
                                        "attemptIndex": 0,
                                        "stateBefore": "disconnected_dialog",
                                        "selectedRecoveryAction": "disconnected_ok",
                                        "stateAfter": "disconnected_dialog",
                                        "transitionSuccess": False,
                                        "clickEvidence": {"name": "disconnected_ok", "clickResult": "PASS"},
                                    }
                                ],
                                "relaunchAttempts": [
                                    {
                                        "attemptIndex": 1,
                                        "stateBefore": "relaunch_required",
                                        "selectedRecoveryAction": "relaunch_client",
                                        "startGameCommand": "cmd /c .\\gradlew.bat --no-daemon run",
                                        "startGameCommandSource": "discovered_gradle_wrapper",
                                        "relaunchResult": {"status": "PASS", "relaunchSucceeded": True, "launchedProcessPid": 1234},
                                        "stateAfter": "disconnected_dialog",
                                        "transitionSuccess": False,
                                        "reason": "stale_login_screen_after_relaunch",
                                    }
                                ],
                            },
                            "actionsTaken": [
                                {
                                    "action": "run_bootstrap_recovery",
                                    "state": "disconnected_dialog",
                                    "clickedCandidateDetails": [{"name": "disconnected_ok", "clickResult": "PASS"}],
                                }
                            ],
                        }
                    )
                )
                return subprocess.CompletedProcess(command, 1)

            def unexpected_executor(*args, **kwargs):
                raise AssertionError("executor should not run when recovery/readiness fails")

            summary = bot_eval_runner.run_live_action(
                output_root=root / "bot_runs",
                duration=1,
                max_actions=1,
                record_everything=False,
                analyze_after=False,
                sessions_root=sessions_root,
                fetcher=fetch,
                command_runner=unexpected_executor,
                recovery_runner=fake_recovery,
                runtime_control_poster=fake_poster(),
                auto_recover_loaded_scene=True,
            )

            self.assertEqual(summary["status"], "FAIL")
            self.assertFalse(summary["liveInputExecuted"])
            self.assertEqual(summary["actionCommandsSent"], 0)
            self.assertEqual(summary["recovery"]["status"], "unsafe")
            self.assertEqual(summary["recovery"]["blocker"], "disconnected_loop")
            self.assertEqual(summary["recovery"]["recoveryFailureClass"], "disconnected_loop")
            self.assertTrue(summary["recovery"]["relaunchRequired"])
            self.assertTrue(summary["recovery"]["relaunchAttempted"])
            self.assertEqual(summary["recovery"]["launchMode"], "dev_gradle_run")
            self.assertEqual(summary["recovery"]["startGameCommandSource"], "discovered_gradle_wrapper")
            self.assertFalse(summary["recovery"]["loadedSceneAfterRelaunch"])
            self.assertEqual(summary["readiness"]["rootCause"], "loaded_scene_not_ready")
            self.assertTrue(Path(summary["artifacts"]["recoveryAttempts"]).exists())
            self.assertTrue(Path(summary["artifacts"]["recoverySummary"]).exists())
            self.assertTrue(Path(summary["artifacts"]["latestRecoveryState"]).exists())
            attempts = [
                json.loads(line)
                for line in Path(summary["artifacts"]["recoveryAttempts"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(attempts[0]["selectedRecoveryAction"], "disconnected_ok")
            self.assertEqual(attempts[0]["recoveryFailureClass"], "disconnected_loop")
            self.assertTrue(any(item.get("selectedRecoveryAction") == "relaunch_client" for item in attempts))

    def test_live_action_fake_runner_writes_required_traces(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions_root = make_live_session(root)
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 100, "latestTick": 123},
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS"},
                }
            )

            def fake_runner(command, *, cwd, stdout, stderr, text, timeout):
                payload = {
                    "schema": "input_control_execution_loop_result.v1",
                    "status": "PASS",
                    "reason": "stop_after_lifecycle_cycles",
                    "executedActionCount": 1,
                    "actionResults": [
                        {
                            "schema": "input_control_execution_result.v1",
                            "status": "PASS",
                            "proposedAction": "select_resource_target",
                            "executed": True,
                            "backend": "arduino",
                            "proposal": {"sourceTick": 123, "selectedTarget": {"name": "Tree", "qualityTier": "strong"}},
                            "expectedResult": {"expectedSignal": "resource_progress"},
                            "observedResult": {"observedSignals": ["resource_progress"]},
                            "verificationStatus": "PASS",
                            "commands": [{"command": "CLICK"}],
                        }
                    ],
                    "loopSummary": {"lifecycleCyclesCompleted": 1, "postServiceLogsCollected": 1},
                    "warnings": [],
                }
                stdout.write(json.dumps(payload))
                return subprocess.CompletedProcess(command, 0)

            with focused_runelite_window_patch():
                summary = bot_eval_runner.run_live_action(
                    output_root=root / "bot_runs",
                    duration=1,
                    max_actions=1,
                    record_everything=False,
                    analyze_after=False,
                    sessions_root=sessions_root,
                    fetcher=fetch,
                    command_runner=fake_runner,
                    runtime_control_poster=fake_poster(),
                )

            self.assertEqual(summary["mode"], "live_action")
            self.assertEqual(summary["status"], "PASS")
            self.assertTrue(summary["liveInputExecuted"])
            self.assertTrue(summary["loopComplete"])
            artifacts = summary["artifacts"]
            for key in ("manifest", "readiness", "decisions", "candidates", "actions", "observations", "postconditions", "summary", "executorPayload"):
                self.assertTrue(Path(artifacts[key]).exists(), key)

    def test_live_action_no_executable_action_is_hard_blocker_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions_root = make_live_session(root)
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 100, "latestTick": 123},
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS"},
                }
            )

            def fake_runner(command, *, cwd, stdout, stderr, text, timeout):
                payload = {
                    "schema": "input_control_execution_loop_result.v1",
                    "status": "WARN",
                    "reason": "max_runtime_reached",
                    "executedActionCount": 0,
                    "actionResults": [],
                    "lifecycleState": {
                        "currentState": "blocked",
                        "lastAction": "wait_for_context",
                        "lastActionTick": 639,
                        "reason": "no_executable_action",
                        "warnings": ["no executable action from current context"],
                    },
                    "loopSummary": {
                        "candidatesEvaluated": 0,
                        "proposedActions": 0,
                        "actionsAttempted": 0,
                        "actionsExecuted": 0,
                        "lastLifecycleSampleTick": 639,
                        "stopReason": "max_runtime_reached",
                    },
                    "warnings": [],
                }
                stdout.write(json.dumps(payload))
                return subprocess.CompletedProcess(command, 0)

            with focused_runelite_window_patch():
                summary = bot_eval_runner.run_live_action(
                    output_root=root / "bot_runs",
                    duration=1,
                    max_actions=1,
                    record_everything=False,
                    analyze_after=False,
                    sessions_root=sessions_root,
                    fetcher=fetch,
                    command_runner=fake_runner,
                    runtime_control_poster=fake_poster(),
                )

            self.assertEqual(summary["status"], "FAIL")
            self.assertFalse(summary["liveInputExecuted"])
            self.assertEqual(summary["actionCommandsSent"], 0)
            self.assertEqual(summary["executorBlocker"]["blocker"], "no_executable_action")
            self.assertIn("executor_blocker=no_executable_action", summary["errors"])
            decisions = [
                json.loads(line)
                for line in Path(summary["artifacts"]["decisions"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            actions = [
                json.loads(line)
                for line in Path(summary["artifacts"]["actions"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            candidates = [
                json.loads(line)
                for line in Path(summary["artifacts"]["candidates"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            postconditions = [
                json.loads(line)
                for line in Path(summary["artifacts"]["postconditions"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(decisions[0]["blocker"]["blocker"], "no_executable_action")
            self.assertEqual(candidates[0]["blocker"]["blocker"], "no_executable_action")
            self.assertFalse(candidates[0]["executable"])
            self.assertFalse(actions[0]["executed"])
            self.assertEqual(postconditions[0]["status"], "FAIL")

    def test_live_action_preserves_route_reentry_blocker_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions_root = make_live_session(root)
            fetch = fake_fetcher(
                {
                    "http://127.0.0.1:8890/health": {"schema": "context_health.v1", "status": "ok", "sourceAgeMs": 100, "latestTick": 123},
                    "http://127.0.0.1:8893/health": {"schema": "snapshot_health.v1", "status": "PASS"},
                }
            )

            def fake_runner(command, *, cwd, stdout, stderr, text, timeout):
                payload = {
                    "schema": "input_control_execution_loop_result.v1",
                    "status": "FAIL",
                    "reason": "route_guide_no_same_plane_reentry",
                    "executedActionCount": 0,
                    "actionResults": [
                        {
                            "schema": "input_control_execution_result.v1",
                            "status": "FAIL",
                            "proposedAction": "wait_for_context",
                            "executed": False,
                            "proposal": {
                                "reason": "no_executable_action",
                                "targetExplanation": {
                                    "likelyReason": "expected Bottom floor direct transition was missed or not used; plane-1 recovery is not demonstrated",
                                    "suggestedFixture": "record a short plane-1 Staircase recovery from 3206,3229,1",
                                    "safeState": "no click sent because route guide lacks same-plane proof",
                                    "contextActionFallback": {
                                        "schema": "context_action_fallback.v1",
                                        "status": "WARN",
                                        "originalAction": "wait_for_context",
                                        "originalReason": "route_guide_no_same_plane_reentry",
                                        "reason": "context_action_proposal_not_executable",
                                    }
                                },
                            },
                            "observedResult": {
                                "schema": "action_observation.v1",
                                "observedResult": "route_guide_no_same_plane_reentry",
                                "resultOutcome": "blocked",
                                "verificationStatus": "BLOCKED",
                            },
                            "warnings": ["current player floor is not represented by a demonstrated same-plane route guide step"],
                        }
                    ],
                    "lifecycleState": {
                        "currentState": "blocked",
                        "lastAction": "wait_for_context",
                        "lastActionTick": 640,
                        "reason": "route_guide_no_same_plane_reentry",
                    },
                    "loopSummary": {
                        "candidatesEvaluated": 1,
                        "proposedActions": 1,
                        "actionsAttempted": 0,
                        "actionsExecuted": 0,
                        "lastLifecycleSampleTick": 640,
                        "stopReason": "route_guide_no_same_plane_reentry",
                    },
                    "warnings": ["current player floor is not represented by a demonstrated same-plane route guide step"],
                }
                stdout.write(json.dumps(payload))
                return subprocess.CompletedProcess(command, 0)

            with focused_runelite_window_patch():
                summary = bot_eval_runner.run_live_action(
                    output_root=root / "bot_runs",
                    duration=1,
                    max_actions=1,
                    record_everything=False,
                    analyze_after=False,
                    sessions_root=sessions_root,
                    fetcher=fetch,
                    command_runner=fake_runner,
                    runtime_control_poster=fake_poster(),
                )

            self.assertEqual(summary["status"], "FAIL")
            self.assertFalse(summary["liveInputExecuted"])
            self.assertEqual(summary["executorBlocker"]["blocker"], "route_guide_no_same_plane_reentry")
            self.assertIn("executor_blocker=route_guide_no_same_plane_reentry", summary["errors"])
            candidates = [
                json.loads(line)
                for line in Path(summary["artifacts"]["candidates"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(candidates[0]["blocker"], "route_guide_no_same_plane_reentry")
            self.assertEqual(
                candidates[0]["likelyReason"],
                "expected Bottom floor direct transition was missed or not used; plane-1 recovery is not demonstrated",
            )


if __name__ == "__main__":
    unittest.main()

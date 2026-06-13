import inspect
import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import liveness_recovery_core as recovery
import run_runelite_bootstrap as bootstrap
import start_game_command


def snapshot(game_state="LOGIN_SCREEN", *, object_total=0, hot_age_ms=25, baseline_state=None, world_state=None, source_event=None):
    baseline = {"gameState": baseline_state or game_state}
    hot = {
        "schema": "client_tick_hot.v1",
        "gameState": game_state,
        "postMenuSort": {
            "topOption": "Walk here",
            "topTarget": "",
            "topType": "WALK",
            "gameState": game_state,
        },
        "latency": {"ageMillis": hot_age_ms, "postMenuSortAgeMillis": hot_age_ms},
    }
    if source_event:
        hot["sourceEvent"] = source_event
        hot["sampleSource"] = source_event
    payload = {
        "status": "PASS",
        "latestTick": 42,
        "payloads": {
            "baseline": baseline,
            "world_model_summary": {
                "schema": "world_model_summary.v1",
                "metadata": {"gameState": world_state or game_state},
                "objects": {"total": object_total},
            },
            "client_tick_hot": hot,
        },
    }
    return payload


def loaded_snapshot(*, hot_age_ms=25):
    return snapshot("LOGGED_IN", object_total=12, hot_age_ms=hot_age_ms)


def pop_or_last(values):
    return values.pop(0) if len(values) > 1 else values[0]


def daemon_status():
    return {
        "schema": "live_core_status.v1",
        "status": "PASS",
        "latestTick": 42,
        "sessionPath": "C:/sessions/live",
        "clientTickHot": {
            "schema": "client_tick_hot.v1",
            "gameState": "LOGGED_IN",
            "latency": {"ageMillis": 25, "postMenuSortAgeMillis": 25},
        },
    }


def window(title="RuneLite"):
    return {
        "matchedWindowTitle": title,
        "windowBounds": {"x": 0, "y": 0, "width": 900, "height": 700},
        "warnings": [],
    }


def recovery_state(name, *, loaded=False, game_state="LOGIN_SCREEN", detected_buttons=None):
    return {
        "state": name,
        "loadedSceneVerified": bool(loaded),
        "detectedButtons": [{"name": item} for item in (detected_buttons or [])],
        "loadedSceneProof": {
            "loadedSceneVerified": bool(loaded),
            "gameState": game_state,
            "clientTickHotFresh": True,
            "worldModelObjectTotal": 12 if loaded else 0,
        },
    }


def candidate(name, source):
    return bootstrap.StartupButtonCandidate(
        name=name,
        source=source,
        screen_point={"x": 450, "y": 350},
        canvas_point=None,
        confidence=0.92,
        reason=f"test {name}",
    )


def clicked_candidate(
    name,
    *,
    before="disconnected_dialog",
    after="disconnected_dialog",
    transition=False,
    proof=None,
):
    item = candidate(name, "disconnected_dialog" if name == "disconnected_ok" else "saved_account_play_panel").to_dict()
    item.update(
        {
            "targetValidationStatus": "PASS",
            "targetInsideRuneLiteWindow": True,
            "targetInsideSafeClickRegion": True,
            "beforeVisualState": before,
            "afterVisualState": after,
            "beforeHotGameState": "LOGIN_SCREEN",
            "afterHotGameState": "LOGIN_SCREEN" if after != "loaded_scene" else "LOGGED_IN",
            "expectedNextStates": recovery._expected_next_states_for_button(name),
            "expectedTransitionSatisfied": bool(transition),
            "transitionResult": "expected_transition_satisfied" if transition else "expected_transition_not_observed",
            "clickResult": "PASS",
        }
    )
    click_proof = {
        "status": "PASS",
        "inputPathUsed": "HumanInputController/ArduinoHIDBackend",
        "inputBackend": "arduino",
        "targetPoint": {"x": 450, "y": 350},
        "cursorBefore": {"x": 440, "y": 340},
        "cursorAfterMove": {"x": 450, "y": 350},
        "cursorAfterClick": {"x": 450, "y": 350},
        "cursorTargetDistance": 0,
        "cursorAtTarget": True,
        "mouseMoveSent": True,
        "mouseDownSent": True,
        "mouseUpSent": True,
        "clickSent": True,
        "arduinoAcks": ["OK MOVE", "OK MOUSE_DOWN", "OK MOUSE_UP"],
        "fullClickSequenceVerified": True,
        "windowFocusVerified": True,
    }
    click_proof.update(proof or {})
    item["clickDetails"] = click_proof
    return item


class LivenessRecoveryCoreTest(unittest.TestCase):
    def setUp(self):
        recovery._LAST_SUCCESS = None

    def test_stale_logged_in_with_no_world_objects_classifies_stale_no_scene(self):
        state = recovery.classify_state(
            snapshot_payload=snapshot("LOGGED_IN", baseline_state="LOGGED_IN", world_state="LOGIN_SCREEN", object_total=0),
            daemon_status=daemon_status(),
            window=window(),
            candidates=[],
        )

        self.assertEqual(state["state"], "stale_logged_in_no_scene")
        self.assertTrue(state["knownRecoverableState"])
        self.assertFalse(state["loadedSceneVerified"])

    def test_loaded_scene_proof_requires_fresh_client_tick_hot(self):
        fresh = recovery.loaded_scene_proof(loaded_snapshot(hot_age_ms=25), reachable=True)
        stale = recovery.loaded_scene_proof(loaded_snapshot(hot_age_ms=5000), reachable=True)

        self.assertTrue(fresh["loadedSceneVerified"])
        self.assertTrue(fresh["clientTickHotFresh"])
        self.assertFalse(stale["loadedSceneVerified"])
        self.assertFalse(stale["clientTickHotFresh"])

    def test_loaded_scene_proof_rejects_game_state_changed_only_hot_sample(self):
        proof = recovery.loaded_scene_proof(
            snapshot("LOGGED_IN", object_total=12, hot_age_ms=25, source_event="GameStateChanged"),
            reachable=True,
        )

        self.assertFalse(proof["loadedSceneVerified"])
        self.assertFalse(proof["clientTickHotFresh"])
        self.assertEqual(proof["gameState"], "LOGGED_IN")
        self.assertEqual(proof["worldModelObjectTotal"], 12)

    def test_login_screen_with_disconnected_candidate_classifies_dialog(self):
        state = recovery.classify_state(
            snapshot_payload=snapshot("LOGIN_SCREEN", object_total=0),
            daemon_status=daemon_status(),
            window=window(),
            candidates=[candidate("disconnected_ok", "disconnected_dialog")],
        )

        self.assertEqual(state["state"], "disconnected_dialog")
        self.assertTrue(state["knownRecoverableState"])

    def test_saved_account_play_now_recovers_by_bootstrap_arduino_path(self):
        snapshots = [snapshot("LOGIN_SCREEN"), loaded_snapshot()]
        button_calls = {"count": 0}
        bootstrap_calls = []

        def buttons(_payload, _window, **_kwargs):
            button_calls["count"] += 1
            if button_calls["count"] == 1:
                return [candidate("play_now", "saved_account_play_panel")], []
            return [], []

        def run_bootstrap(args):
            bootstrap_calls.append(args)
            return {
                "schema": bootstrap.SCHEMA,
                "status": "PASS",
                "loadedSceneVerified": True,
                "clickedCandidates": [{"name": "play_now"}],
                "daemon": {"reachable": True, "startedOrReused": "reused"},
            }

        payload = recovery.ensure_loaded_scene(
            snapshot_url="http://snapshot",
            daemon_url="http://daemon",
            arduino_port="COM6",
            fetch_snapshot_func=lambda *_args, **_kwargs: pop_or_last(snapshots),
            fetch_daemon_status_func=lambda *_args, **_kwargs: daemon_status(),
            window_finder=lambda _filters: window(),
            button_candidates_func=buttons,
            run_bootstrap_recovery_func=run_bootstrap,
            sleep_func=lambda _seconds: None,
            allow_relaunch=False,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "recovered_loaded_scene")
        self.assertEqual(len(bootstrap_calls), 1)
        self.assertEqual(bootstrap_calls[0].startup_backend, "arduino")
        self.assertEqual(payload["actionsTaken"][0]["clickedCandidates"], ["play_now"])
        self.assertEqual(payload["actionsTaken"][-1]["action"], "loaded_scene_stability_check")
        self.assertEqual(payload["actionsTaken"][-1]["status"], "PASS")
        self.assertTrue(payload["recoveryAttempted"])
        self.assertTrue(payload["autologinRecoveryAttempted"])
        self.assertTrue(payload["savedAccountDetected"])
        self.assertTrue(payload["playNowAttempted"])
        self.assertFalse(payload["manualLoginRequiredOnlyAfterRecovery"])

    def test_login_screen_runs_bootstrap_ladder_before_manual_login_required(self):
        bootstrap_calls = []

        def run_bootstrap(args):
            bootstrap_calls.append(args)
            return {
                "schema": bootstrap.SCHEMA,
                "status": "WARN",
                "loadedSceneVerified": False,
                "startupStage": "blocked_user_login_required",
                "clickedCandidates": [],
                "stages": [{"stage": "waiting_for_logged_in", "status": "WARN", "reason": "no safe startup button candidates"}],
                "snapshot": {"gameState": "LOGIN_SCREEN", "loadedSceneVerified": False, "worldModelObjectTotal": 0},
            }

        payload = recovery.ensure_loaded_scene(
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN", object_total=0, hot_age_ms=5000),
            fetch_daemon_status_func=lambda *_args, **_kwargs: {},
            window_finder=lambda _filters: window(),
            button_candidates_func=lambda *_args, **_kwargs: ([], []),
            run_bootstrap_recovery_func=run_bootstrap,
            sleep_func=lambda _seconds: None,
            allow_relaunch=False,
            use_cache=False,
        )

        self.assertEqual(len(bootstrap_calls), 1)
        self.assertEqual(payload["status"], "manual_login_required")
        self.assertEqual(payload["blocker"], "manual_login_required_after_recovery")
        self.assertEqual(payload["recoveryFailureClass"], "login_surface_no_saved_account")
        self.assertTrue(payload["recoveryAttempted"])
        self.assertTrue(payload["autologinRecoveryAttempted"])
        self.assertTrue(payload["manualLoginRequiredOnlyAfterRecovery"])
        self.assertTrue(payload["visibleButtonScanAttempted"])
        self.assertEqual(payload["visibleButtonsFound"], [])
        self.assertEqual(payload["recoveryActionsTried"], ["run_bootstrap_recovery"])
        self.assertEqual(payload["recoveryResult"]["failureClass"], "login_surface_no_saved_account")
        self.assertEqual(payload["finalLoginSurface"], "login_screen")
        self.assertFalse(payload["savedAccountDetected"])
        self.assertFalse(payload["playNowAttempted"])

    def test_saved_account_visible_without_play_now_attempt_is_explicit_bug_signal(self):
        def buttons(_payload, _window, **_kwargs):
            return [candidate("play_now", "saved_account_play_panel")], []

        def run_bootstrap(_args):
            return {
                "schema": bootstrap.SCHEMA,
                "status": "WARN",
                "loadedSceneVerified": False,
                "startupStage": "waiting_for_logged_in",
                "clickedCandidates": [],
                "snapshot": {"gameState": "LOGIN_SCREEN", "loadedSceneVerified": False, "worldModelObjectTotal": 0},
            }

        payload = recovery.ensure_loaded_scene(
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN", object_total=0, hot_age_ms=5000),
            fetch_daemon_status_func=lambda *_args, **_kwargs: {},
            window_finder=lambda _filters: window(),
            button_candidates_func=buttons,
            run_bootstrap_recovery_func=run_bootstrap,
            sleep_func=lambda _seconds: None,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "unsafe")
        self.assertEqual(payload["blocker"], "saved_account_play_now_not_attempted")
        self.assertEqual(payload["recoveryFailureClass"], "saved_account_play_now_not_attempted")
        self.assertTrue(payload["recoveryAttempted"])
        self.assertTrue(payload["autologinRecoveryAttempted"])
        self.assertTrue(payload["savedAccountDetected"])
        self.assertFalse(payload["playNowAttempted"])

    def test_saved_account_candidate_retry_prefers_play_now_after_click_here_failure(self):
        snapshots = [snapshot("LOGIN_SCREEN", object_total=0, hot_age_ms=5000), loaded_snapshot(), loaded_snapshot()]
        bootstrap_calls = []
        button_candidates = [
            candidate("click_here_to_play", "calibrated_screen").to_dict(),
            candidate("play_now", "saved_account_play_panel").to_dict(),
        ]

        def run_bootstrap(args):
            bootstrap_calls.append(args)
            if len(bootstrap_calls) == 1:
                return {
                    "schema": bootstrap.SCHEMA,
                    "status": "WARN",
                    "loadedSceneVerified": False,
                    "startupStage": "blocked_user_login_required",
                    "buttonCandidates": button_candidates,
                    "clickedCandidates": [candidate("click_here_to_play", "calibrated_screen").to_dict()],
                    "snapshot": {"gameState": "LOGIN_SCREEN", "loadedSceneVerified": False, "worldModelObjectTotal": 0},
                }
            return {
                "schema": bootstrap.SCHEMA,
                "status": "PASS",
                "loadedSceneVerified": True,
                "startupStage": "loaded_scene_ready",
                "buttonCandidates": button_candidates,
                "clickedCandidates": [candidate("play_now", "saved_account_play_panel").to_dict()],
                "snapshot": {"gameState": "LOGGED_IN", "loadedSceneVerified": True, "worldModelObjectTotal": 12},
            }

        payload = recovery.ensure_loaded_scene(
            fetch_snapshot_func=lambda *_args, **_kwargs: pop_or_last(snapshots),
            fetch_daemon_status_func=lambda *_args, **_kwargs: daemon_status(),
            window_finder=lambda _filters: window(),
            button_candidates_func=lambda *_args, **_kwargs: (
                [candidate("click_here_to_play", "calibrated_screen")],
                [],
            ),
            run_bootstrap_recovery_func=run_bootstrap,
            sleep_func=lambda _seconds: None,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "recovered_loaded_scene")
        self.assertEqual(len(bootstrap_calls), 2)
        self.assertFalse(getattr(bootstrap_calls[0], "prefer_saved_account_play_now", False))
        self.assertTrue(getattr(bootstrap_calls[1], "prefer_saved_account_play_now", False))
        self.assertEqual(payload["actionsTaken"][1]["state"], "saved_account_play_now_retry")
        self.assertEqual(payload["actionsTaken"][1]["clickedCandidates"], ["play_now"])
        self.assertTrue(payload["savedAccountDetected"])
        self.assertTrue(payload["playNowAttempted"])

    def test_transient_loaded_scene_must_survive_stability_check(self):
        snapshots = [snapshot("LOGIN_SCREEN"), loaded_snapshot(), snapshot("LOGIN_SCREEN", object_total=0)]

        def buttons(_payload, _window, **_kwargs):
            return [candidate("play_now", "saved_account_play_panel")], []

        def run_bootstrap(_args):
            return {
                "schema": bootstrap.SCHEMA,
                "status": "PASS",
                "loadedSceneVerified": True,
                "clickedCandidates": [{"name": "play_now", "clickResult": "PASS"}],
                "daemon": {"reachable": True, "startedOrReused": "reused"},
            }

        payload = recovery.ensure_loaded_scene(
            snapshot_url="http://snapshot",
            daemon_url="http://daemon",
            arduino_port="COM6",
            fetch_snapshot_func=lambda *_args, **_kwargs: pop_or_last(snapshots),
            fetch_daemon_status_func=lambda *_args, **_kwargs: daemon_status(),
            window_finder=lambda _filters: window(),
            button_candidates_func=buttons,
            run_bootstrap_recovery_func=run_bootstrap,
            resolve_start_game_command_func=lambda: {"status": "FAIL", "reason": "relaunch_command_missing", "command": "", "commandSource": "none"},
            sleep_func=lambda _seconds: None,
            allow_relaunch=False,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "unsafe")
        self.assertFalse(payload["loadedSceneVerified"])
        self.assertEqual(payload["actionsTaken"][-1]["action"], "loaded_scene_stability_check")
        self.assertEqual(payload["actionsTaken"][-1]["status"], "FAIL")
        self.assertIn("loaded scene proof disappeared during stability check", payload["warnings"])

    def test_disconnected_play_now_loop_is_classified_when_relaunch_disabled(self):
        button_calls = {"count": 0}

        def buttons(_payload, _window, **_kwargs):
            button_calls["count"] += 1
            return [candidate("disconnected_ok", "disconnected_dialog")], []

        def run_bootstrap(_args):
            return {
                "schema": bootstrap.SCHEMA,
                "status": "WARN",
                "loadedSceneVerified": False,
                "startupStage": "blocked_user_login_required",
                "snapshot": {
                    "gameState": "LOGIN_SCREEN",
                    "loadedSceneVerified": False,
                    "worldModelObjectTotal": 0,
                    "screenClassification": "login_screen_or_disconnected_dialog",
                },
                "clickedCandidates": [
                    {
                        **candidate("disconnected_ok", "disconnected_dialog").to_dict(),
                        "clickResult": "PASS",
                        "expectedStateAfterClick": "login_screen_or_saved_account",
                    },
                    {
                        **candidate("play_now", "saved_account_play_panel").to_dict(),
                        "clickResult": "PASS",
                        "expectedStateAfterClick": "loading_or_logged_in",
                    },
                ],
                "stages": [
                    {"stage": "click_disconnected_ok_candidate", "status": "PASS", "reason": "test"},
                    {"stage": "click_play_now_candidate", "status": "PASS", "reason": "test"},
                ],
                "daemon": {"reachable": False, "startedOrReused": "blocked_until_logged_in"},
            }

        payload = recovery.ensure_loaded_scene(
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN", object_total=0, hot_age_ms=5000),
            fetch_daemon_status_func=lambda *_args, **_kwargs: {},
            window_finder=lambda _filters: window(),
            button_candidates_func=buttons,
            run_bootstrap_recovery_func=run_bootstrap,
            sleep_func=lambda _seconds: None,
            allow_relaunch=False,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "unsafe")
        self.assertEqual(payload["blocker"], "disconnected_loop")
        self.assertEqual(payload["recoveryFailureClass"], "disconnected_loop")
        machine = payload["recoveryStateMachine"]
        self.assertEqual(machine["failureClassification"]["failureClass"], "disconnected_loop")
        self.assertEqual([item["selectedRecoveryAction"] for item in machine["clickAttempts"]], ["disconnected_ok", "play_now"])
        self.assertTrue(payload["visibleSafeButtonClicked"])

    def test_disconnected_loop_triggers_start_game_relaunch(self):
        snapshots = [snapshot("LOGIN_SCREEN", object_total=0, hot_age_ms=5000), snapshot("LOGIN_SCREEN", object_total=0, hot_age_ms=5000), loaded_snapshot()]
        launched = []

        def buttons(_payload, _window, **_kwargs):
            return [candidate("disconnected_ok", "disconnected_dialog")], []

        def run_bootstrap(_args):
            return {
                "schema": bootstrap.SCHEMA,
                "status": "WARN",
                "loadedSceneVerified": False,
                "startupStage": "blocked_user_login_required",
                "snapshot": {"gameState": "LOGIN_SCREEN", "loadedSceneVerified": False, "worldModelObjectTotal": 0},
                "clickedCandidates": [
                    {
                        **candidate("disconnected_ok", "disconnected_dialog").to_dict(),
                        "clickResult": "PASS",
                        "expectedStateAfterClick": "login_screen_or_saved_account",
                    },
                    {
                        **candidate("play_now", "saved_account_play_panel").to_dict(),
                        "clickResult": "PASS",
                        "expectedStateAfterClick": "loading_or_logged_in",
                    },
                ],
                "daemon": {"reachable": False, "startedOrReused": "blocked_until_logged_in"},
            }

        def resolve_command():
            return {
                "schema": "start_game_command_resolution.v1",
                "status": "PASS",
                "command": "cmd /c .\\gradlew.bat --no-daemon run",
                "commandSource": "discovered_gradle_wrapper",
                "cwd": "C:/repo",
                "shell": True,
            }

        def launch(command_info):
            launched.append(command_info)
            return {
                "schema": "start_game_launch_result.v1",
                "status": "PASS",
                "reason": "launched",
                "relaunchAttempted": True,
                "relaunchSucceeded": True,
                "command": command_info["command"],
                "commandSource": command_info["commandSource"],
                "launchedProcessPid": 1234,
            }

        payload = recovery.ensure_loaded_scene(
            fetch_snapshot_func=lambda *_args, **_kwargs: pop_or_last(snapshots),
            fetch_daemon_status_func=lambda *_args, **_kwargs: daemon_status(),
            window_finder=lambda _filters: window(),
            button_candidates_func=buttons,
            run_bootstrap_recovery_func=run_bootstrap,
            resolve_start_game_command_func=resolve_command,
            launch_start_game_func=launch,
            sleep_func=lambda _seconds: None,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "recovered_loaded_scene")
        self.assertTrue(payload["disconnectedLoopDetected"])
        self.assertTrue(payload["relaunchRequired"])
        self.assertTrue(payload["relaunchAttempted"])
        self.assertTrue(payload["loadedSceneAfterRelaunch"])
        self.assertEqual(payload["startGameCommandSource"], "discovered_gradle_wrapper")
        self.assertEqual(payload["launchedProcessPid"], 1234)
        self.assertEqual(len(launched), 1)
        actions = [item["action"] for item in payload["actionsTaken"]]
        self.assertIn("relaunch_client", actions)
        self.assertEqual(payload["recoveryStateMachine"]["relaunchAttempts"][-1]["selectedRecoveryAction"], "wait_for_loaded_scene_after_relaunch")

    def test_gradle_start_game_command_is_classified_as_dev_launch(self):
        mode = start_game_command.classify_launch_mode("cmd /c .\\gradlew.bat --no-daemon run", command_source="discovered_gradle_wrapper")

        self.assertEqual(mode["launchMode"], "dev_gradle_run")
        self.assertFalse(mode["authenticatedLaunchLikely"])
        self.assertTrue(mode["warnings"])

    def test_default_relaunch_resolver_uses_start_game_command_module(self):
        source = inspect.getsource(recovery._resolve_start_game_command)

        self.assertIn("start_game_command", source)
        self.assertIn("resolve_start_game_command", source)

    def test_dev_gradle_relaunch_login_screen_is_not_treated_as_authenticated_recovery(self):
        times = iter([0.0, 0.0, 0.1, 2.0, 2.0])

        def buttons(_payload, _window, **_kwargs):
            return [candidate("disconnected_ok", "disconnected_dialog")], []

        def run_bootstrap(_args):
            return {
                "schema": bootstrap.SCHEMA,
                "status": "WARN",
                "loadedSceneVerified": False,
                "clickedCandidates": [
                    {**candidate("disconnected_ok", "disconnected_dialog").to_dict(), "clickResult": "PASS"},
                    {**candidate("play_now", "saved_account_play_panel").to_dict(), "clickResult": "PASS"},
                ],
            }

        payload = recovery.ensure_loaded_scene(
            max_total_ms=1000,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN", object_total=0, hot_age_ms=5000),
            fetch_daemon_status_func=lambda *_args, **_kwargs: {},
            window_finder=lambda _filters: window(),
            button_candidates_func=buttons,
            run_bootstrap_recovery_func=run_bootstrap,
            resolve_start_game_command_func=lambda: {
                "status": "PASS",
                "command": "cmd /c .\\gradlew.bat --no-daemon run",
                "commandSource": "discovered_gradle_wrapper",
                "cwd": "C:/repo",
                "shell": True,
                "launchMode": "dev_gradle_run",
                "launchModeWarnings": ["dev launch"],
            },
            launch_start_game_func=lambda _info: {"status": "PASS", "reason": "launched", "relaunchAttempted": True, "relaunchSucceeded": True},
            sleep_func=lambda _seconds: None,
            monotonic_func=lambda: next(times, 2.0),
            use_cache=False,
        )

        self.assertEqual(payload["status"], "unsafe")
        self.assertEqual(payload["blocker"], "dev_launch_not_loaded")
        self.assertEqual(payload["launchMode"], "dev_gradle_run")
        self.assertTrue(payload["loginScreenAfterRelaunch"])
        self.assertTrue(any("Gradle/dev launch path" in warning for warning in payload["warnings"]))

    def test_relaunch_command_missing_is_explicit(self):
        def buttons(_payload, _window, **_kwargs):
            return [candidate("disconnected_ok", "disconnected_dialog")], []

        def run_bootstrap(_args):
            return {
                "schema": bootstrap.SCHEMA,
                "status": "WARN",
                "loadedSceneVerified": False,
                "clickedCandidates": [
                    {**candidate("disconnected_ok", "disconnected_dialog").to_dict(), "clickResult": "PASS"},
                    {**candidate("play_now", "saved_account_play_panel").to_dict(), "clickResult": "PASS"},
                ],
            }

        payload = recovery.ensure_loaded_scene(
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN", object_total=0, hot_age_ms=5000),
            fetch_daemon_status_func=lambda *_args, **_kwargs: {},
            window_finder=lambda _filters: window(),
            button_candidates_func=buttons,
            run_bootstrap_recovery_func=run_bootstrap,
            resolve_start_game_command_func=lambda: {"status": "FAIL", "reason": "relaunch_command_missing", "command": "", "commandSource": "none"},
            sleep_func=lambda _seconds: None,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "unsafe")
        self.assertEqual(payload["blocker"], "relaunch_command_missing")
        self.assertTrue(payload["relaunchRequired"])
        self.assertFalse(payload["relaunchAttempted"])

    def test_authenticated_live_start_missing_blocks_relaunch_before_manual_login(self):
        def run_bootstrap(_args):
            return {
                "schema": bootstrap.SCHEMA,
                "status": "WARN",
                "loadedSceneVerified": False,
                "startupStage": "blocked_user_login_required",
                "clickedCandidates": [],
                "snapshot": {"gameState": "LOGIN_SCREEN", "loadedSceneVerified": False, "worldModelObjectTotal": 0},
            }

        payload = recovery.ensure_loaded_scene(
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN", object_total=0, hot_age_ms=5000),
            fetch_daemon_status_func=lambda *_args, **_kwargs: {},
            window_finder=lambda _filters: window(),
            button_candidates_func=lambda *_args, **_kwargs: ([], []),
            run_bootstrap_recovery_func=run_bootstrap,
            resolve_start_game_command_func=lambda: {
                "status": "FAIL",
                "reason": "authenticated_live_start_missing",
                "command": "",
                "commandSource": "live_start_missing",
                "devStartCommand": "cmd /c .\\gradlew.bat --no-daemon run",
                "devStartCommandSource": "ui_config:game_launch_command",
                "liveStartCommand": "",
                "liveStartCommandSource": "none",
                "launchMode": "unknown",
                "launchModeWarnings": ["configure Jagex Launcher"],
                "discoveredCandidates": [],
            },
            sleep_func=lambda _seconds: None,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "unsafe")
        self.assertEqual(payload["blocker"], "authenticated_live_start_missing")
        self.assertTrue(payload["relaunchRequired"])
        self.assertFalse(payload["relaunchAttempted"])
        self.assertEqual(payload["devStartCommand"], "cmd /c .\\gradlew.bat --no-daemon run")
        self.assertEqual(payload["liveStartCommand"], "")
        self.assertIn("configure Jagex Launcher", payload["launchModeWarnings"])

    def test_visible_button_after_relaunch_is_clicked_before_failure(self):
        times = iter([0.0] * 20 + [2.0])
        bootstrap_calls = []

        def buttons(_payload, _window, **_kwargs):
            return [candidate("disconnected_ok", "disconnected_dialog")], []

        def run_bootstrap(args):
            bootstrap_calls.append(args)
            return {
                "schema": bootstrap.SCHEMA,
                "status": "WARN",
                "loadedSceneVerified": False,
                "clickedCandidates": [
                    {**candidate("disconnected_ok", "disconnected_dialog").to_dict(), "clickResult": "PASS"},
                    {**candidate("play_now", "saved_account_play_panel").to_dict(), "clickResult": "PASS"},
                ],
            }

        payload = recovery.ensure_loaded_scene(
            max_total_ms=1000,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN", object_total=0, hot_age_ms=5000),
            fetch_daemon_status_func=lambda *_args, **_kwargs: {},
            window_finder=lambda _filters: window(),
            button_candidates_func=buttons,
            run_bootstrap_recovery_func=run_bootstrap,
            resolve_start_game_command_func=lambda: {"status": "PASS", "command": "cmd /c run", "commandSource": "test", "cwd": "C:/repo", "shell": True},
            launch_start_game_func=lambda _info: {"status": "PASS", "reason": "launched", "relaunchAttempted": True, "relaunchSucceeded": True},
            sleep_func=lambda _seconds: None,
            monotonic_func=lambda: next(times, 2.0),
            use_cache=False,
        )

        self.assertEqual(payload["status"], "unsafe")
        self.assertEqual(payload["blocker"], "disconnected_loop")
        self.assertEqual(len(bootstrap_calls), 2)
        self.assertTrue(payload["visibleSafeButtonClicked"])
        self.assertEqual(payload["attempts"]["post_relaunch_visible_button_recovery"], 1)
        self.assertTrue(payload["relaunchAttempted"])
        self.assertFalse(payload["loadedSceneAfterRelaunch"])

    def test_play_now_no_transition_is_classified(self):
        def buttons(_payload, _window, **_kwargs):
            return [candidate("play_now", "saved_account_play_panel")], []

        def run_bootstrap(_args):
            return {
                "schema": bootstrap.SCHEMA,
                "status": "WARN",
                "loadedSceneVerified": False,
                "startupStage": "blocked_user_login_required",
                "snapshot": {"gameState": "LOGIN_SCREEN", "loadedSceneVerified": False, "worldModelObjectTotal": 0},
                "clickedCandidates": [
                    {
                        **candidate("play_now", "saved_account_play_panel").to_dict(),
                        "clickResult": "PASS",
                        "expectedStateAfterClick": "loading_or_logged_in",
                    }
                ],
                "daemon": {"reachable": False, "startedOrReused": "blocked_until_logged_in"},
            }

        payload = recovery.ensure_loaded_scene(
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN", object_total=0, hot_age_ms=5000),
            fetch_daemon_status_func=lambda *_args, **_kwargs: {},
            window_finder=lambda _filters: window(),
            button_candidates_func=buttons,
            run_bootstrap_recovery_func=run_bootstrap,
            sleep_func=lambda _seconds: None,
            allow_relaunch=False,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "unsafe")
        self.assertEqual(payload["blocker"], "visible_button_no_transition")
        self.assertEqual(payload["recoveryFailureClass"], "visible_button_no_transition")
        self.assertTrue(payload["visibleSafeButtonClicked"])

    def test_disconnected_ok_to_login_surface_is_transition_success_not_no_transition(self):
        initial = recovery_state("disconnected_dialog", detected_buttons=["disconnected_ok"])
        final = recovery_state("login_screen")
        actions = [
            {
                "action": "run_bootstrap_recovery",
                "clickedCandidateDetails": [
                    clicked_candidate(
                        "disconnected_ok",
                        before="disconnected_dialog",
                        after="login_screen",
                        transition=True,
                    )
                ],
                "buttonCandidates": [],
            }
        ]

        machine = recovery.build_recovery_state_machine(initial_state=initial, final_state=final, actions_taken=actions)
        classification = recovery.classify_recovery_failure(initial_state=initial, final_state=final, actions_taken=actions)

        self.assertTrue(machine["clickAttempts"][0]["transitionSuccess"])
        self.assertEqual(machine["clickAttempts"][0]["expectedNextStates"][0], "login_screen")
        self.assertNotEqual(classification["failureClass"], "visible_button_no_transition")

    def test_missing_mouse_down_reports_incomplete_click_sequence(self):
        initial = recovery_state("saved_account_play_now", detected_buttons=["play_now"])
        final = recovery_state("saved_account_play_now", detected_buttons=["play_now"])
        actions = [
            {
                "action": "run_bootstrap_recovery",
                "clickedCandidateDetails": [
                    clicked_candidate(
                        "play_now",
                        before="saved_account_play_now",
                        after="saved_account_play_now",
                        proof={"mouseDownSent": False, "clickSent": False, "fullClickSequenceVerified": False},
                    )
                ],
            }
        ]

        classification = recovery.classify_recovery_failure(initial_state=initial, final_state=final, actions_taken=actions)

        self.assertEqual(classification["failureClass"], "incomplete_click_sequence")

    def test_missing_cursor_grounding_reports_click_not_grounded(self):
        initial = recovery_state("saved_account_play_now", detected_buttons=["play_now"])
        final = recovery_state("saved_account_play_now", detected_buttons=["play_now"])
        actions = [
            {
                "action": "run_bootstrap_recovery",
                "clickedCandidateDetails": [
                    clicked_candidate(
                        "play_now",
                        before="saved_account_play_now",
                        after="saved_account_play_now",
                        proof={
                            "cursorAtTarget": False,
                            "cursorTargetDistance": 41,
                            "fullClickSequenceVerified": False,
                        },
                    )
                ],
            }
        ]

        classification = recovery.classify_recovery_failure(initial_state=initial, final_state=final, actions_taken=actions)

        self.assertEqual(classification["failureClass"], "visible_button_click_not_grounded")

    def test_runelite_not_focused_reports_window_not_focused(self):
        initial = recovery_state("saved_account_play_now", detected_buttons=["play_now"])
        final = recovery_state("saved_account_play_now", detected_buttons=["play_now"])
        actions = [
            {
                "action": "run_bootstrap_recovery",
                "clickedCandidateDetails": [
                    clicked_candidate(
                        "play_now",
                        before="saved_account_play_now",
                        after="saved_account_play_now",
                        proof={"windowFocusVerified": False},
                    )
                ],
            }
        ]

        classification = recovery.classify_recovery_failure(initial_state=initial, final_state=final, actions_taken=actions)

        self.assertEqual(classification["failureClass"], "recovery_click_window_not_focused")

    def test_no_visual_or_hot_change_after_full_click_reports_no_transition(self):
        initial = recovery_state("disconnected_dialog", detected_buttons=["disconnected_ok"])
        final = recovery_state("disconnected_dialog", detected_buttons=["disconnected_ok"])
        actions = [
            {
                "action": "run_bootstrap_recovery",
                "clickedCandidateDetails": [
                    clicked_candidate(
                        "disconnected_ok",
                        before="disconnected_dialog",
                        after="disconnected_dialog",
                    )
                ],
            }
        ]

        classification = recovery.classify_recovery_failure(initial_state=initial, final_state=final, actions_taken=actions)

        self.assertEqual(classification["failureClass"], "visible_button_no_transition")

    def test_login_surface_no_saved_account_triggers_start_game_relaunch(self):
        launched = []
        times = iter([0.0, 0.0, 2.0])

        def run_bootstrap(_args):
            return {
                "schema": bootstrap.SCHEMA,
                "status": "WARN",
                "loadedSceneVerified": False,
                "startupStage": "blocked_user_login_required",
                "clickedCandidates": [],
                "snapshot": {"gameState": "LOGIN_SCREEN", "loadedSceneVerified": False, "worldModelObjectTotal": 0},
            }

        payload = recovery.ensure_loaded_scene(
            max_total_ms=1000,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN", object_total=0, hot_age_ms=5000),
            fetch_daemon_status_func=lambda *_args, **_kwargs: {},
            window_finder=lambda _filters: window(),
            button_candidates_func=lambda *_args, **_kwargs: ([], []),
            run_bootstrap_recovery_func=run_bootstrap,
            resolve_start_game_command_func=lambda: {
                "status": "PASS",
                "command": "cmd /c .\\gradlew.bat --no-daemon run",
                "commandSource": "discovered_gradle_wrapper",
                "cwd": "C:/repo",
                "shell": True,
                "launchMode": "dev_gradle_run",
            },
            launch_start_game_func=lambda info: launched.append(info) or {"status": "PASS", "reason": "launched", "relaunchAttempted": True, "relaunchSucceeded": True},
            sleep_func=lambda _seconds: None,
            monotonic_func=lambda: next(times, 2.0),
            use_cache=False,
        )

        self.assertEqual(payload["status"], "unsafe")
        self.assertTrue(payload["relaunchRequired"])
        self.assertTrue(payload["relaunchAttempted"])
        self.assertEqual(payload["actionsTaken"][1]["action"], "relaunch_required")
        self.assertEqual(payload["actionsTaken"][1]["state"], "login_surface_no_saved_account")
        self.assertEqual(len(launched), 1)

    def test_credential_screen_stops_for_manual_login(self):
        payload = recovery.ensure_loaded_scene(
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("PASSWORD_REQUIRED", object_total=0),
            fetch_daemon_status_func=lambda *_args, **_kwargs: {},
            window_finder=lambda _filters: window("Jagex Launcher"),
            button_candidates_func=lambda *_args, **_kwargs: ([], []),
            sleep_func=lambda _seconds: None,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "manual_login_required")
        self.assertEqual(payload["blocker"], "manual_login_required")
        self.assertEqual(payload["actionsTaken"], [])

    def test_unknown_screen_does_not_guess_click(self):
        debug_requested = []

        def buttons(_payload, _window, **kwargs):
            debug_requested.append(bool(kwargs.get("save_debug_screenshot")))
            return [], []

        payload = recovery.ensure_loaded_scene(
            fetch_snapshot_func=lambda *_args, **_kwargs: {"status": "PASS", "latestTick": 42, "payloads": {"baseline": {}}},
            fetch_daemon_status_func=lambda *_args, **_kwargs: {},
            window_finder=lambda _filters: window(),
            button_candidates_func=buttons,
            sleep_func=lambda _seconds: None,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "unknown_screen")
        self.assertEqual(payload["blocker"], "unknown_screen")
        self.assertEqual([item["action"] for item in payload["actionsTaken"]], ["capture_unknown_screen_debug"])
        self.assertIn(True, debug_requested)

    def test_loaded_scene_with_daemon_down_starts_daemon(self):
        daemon_calls = {"count": 0}
        starts = []

        def daemon_fetch(*_args, **_kwargs):
            daemon_calls["count"] += 1
            if daemon_calls["count"] == 1:
                raise OSError("daemon down")
            return daemon_status()

        payload = recovery.ensure_loaded_scene(
            fetch_snapshot_func=lambda *_args, **_kwargs: loaded_snapshot(),
            fetch_daemon_status_func=daemon_fetch,
            window_finder=lambda _filters: window(),
            button_candidates_func=lambda *_args, **_kwargs: ([], []),
            start_daemon_func=lambda *, execute: starts.append(execute) or {"started": True, "pid": 123},
            sleep_func=lambda _seconds: None,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "recovered_loaded_scene")
        self.assertEqual(starts, [True])
        self.assertEqual(payload["actionsTaken"][0]["action"], "start_or_rebind_daemon")

    def test_loaded_scene_accepts_daemon_health_fallback(self):
        payload = recovery.ensure_loaded_scene(
            fetch_snapshot_func=lambda *_args, **_kwargs: loaded_snapshot(),
            fetch_daemon_status_func=lambda *_args, **_kwargs: {
                "schema": "context_health.v1",
                "status": "ok",
                "daemonEndpoint": "health",
                "latestTick": 42,
                "sessionPath": "C:/sessions/live",
            },
            window_finder=lambda _filters: window(),
            button_candidates_func=lambda *_args, **_kwargs: ([], []),
            sleep_func=lambda _seconds: None,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "loaded_scene_ready")
        self.assertTrue(payload["daemonFresh"])
        self.assertTrue(payload["loadedSceneVerified"])

    def test_recovery_clicks_refuse_non_arduino_backend(self):
        payload = recovery.ensure_loaded_scene(
            backend="pyautogui",
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN", object_total=0),
            fetch_daemon_status_func=lambda *_args, **_kwargs: daemon_status(),
            window_finder=lambda _filters: window(),
            button_candidates_func=lambda *_args, **_kwargs: ([candidate("play_now", "saved_account_play_panel")], []),
            sleep_func=lambda _seconds: None,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "unsafe")
        self.assertEqual(payload["blocker"], "software_input_not_allowed_for_liveness_recovery")
        self.assertEqual(payload["actionsTaken"], [])

    def test_budget_timeout_returns_concise_blocker(self):
        ticks = iter([0.0, 200.0, 200.0])

        payload = recovery.ensure_loaded_scene(
            max_total_ms=1,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN", object_total=0),
            fetch_daemon_status_func=lambda *_args, **_kwargs: daemon_status(),
            window_finder=lambda _filters: window(),
            button_candidates_func=lambda *_args, **_kwargs: ([candidate("click_here_to_play", "welcome_panel")], []),
            run_bootstrap_recovery_func=lambda _args: {"status": "WARN", "loadedSceneVerified": False, "clickedCandidates": []},
            sleep_func=lambda _seconds: None,
            monotonic_func=lambda: next(ticks),
            allow_relaunch=False,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["blocker"], "liveness_recovery_timeout")


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import liveness_recovery_core as recovery
import run_runelite_bootstrap as bootstrap


def snapshot(game_state="LOGIN_SCREEN", *, object_total=0, hot_age_ms=25, baseline_state=None, world_state=None):
    baseline = {"gameState": baseline_state or game_state}
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
            "client_tick_hot": {
                "schema": "client_tick_hot.v1",
                "gameState": game_state,
                "postMenuSort": {
                    "topOption": "Walk here",
                    "topTarget": "",
                    "topType": "WALK",
                    "gameState": game_state,
                },
                "latency": {"ageMillis": hot_age_ms, "postMenuSortAgeMillis": hot_age_ms},
            },
        },
    }
    return payload


def loaded_snapshot(*, hot_age_ms=25):
    return snapshot("LOGGED_IN", object_total=12, hot_age_ms=hot_age_ms)


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


def candidate(name, source):
    return bootstrap.StartupButtonCandidate(
        name=name,
        source=source,
        screen_point={"x": 450, "y": 350},
        canvas_point=None,
        confidence=0.92,
        reason=f"test {name}",
    )


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
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshots.pop(0),
            fetch_daemon_status_func=lambda *_args, **_kwargs: daemon_status(),
            window_finder=lambda _filters: window(),
            button_candidates_func=buttons,
            run_bootstrap_recovery_func=run_bootstrap,
            sleep_func=lambda _seconds: None,
            use_cache=False,
        )

        self.assertEqual(payload["status"], "recovered_loaded_scene")
        self.assertEqual(len(bootstrap_calls), 1)
        self.assertEqual(bootstrap_calls[0].startup_backend, "arduino")
        self.assertEqual(payload["actionsTaken"][0]["clickedCandidates"], ["play_now"])

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
            use_cache=False,
        )

        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["blocker"], "liveness_recovery_timeout")


if __name__ == "__main__":
    unittest.main()

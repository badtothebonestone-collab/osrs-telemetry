import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "run_runelite_bootstrap.py"
sys.path.insert(0, str(VIEWER_DIR))

import run_runelite_bootstrap as bootstrap


def snapshot(game_state="LOGIN_SCREEN", *, geometry=True):
    baseline = {"gameState": game_state}
    if geometry:
        baseline["inputGeometry"] = {
            "schema": "input_geometry.v1",
            "geometryAvailable": True,
            "reason": "available",
            "canvasScreenX": 1000,
            "canvasScreenY": 2000,
            "canvasWidth": 800,
            "canvasHeight": 600,
            "sourceCanvasWidth": 800,
            "sourceCanvasHeight": 600,
            "displayScaleX": 1.0,
            "displayScaleY": 1.0,
        }
    return {
        "status": "PASS",
        "latestTick": 42,
        "payloads": {"baseline": baseline},
    }


def unusable_snapshot():
    return {"status": "FAIL", "latestTick": -1, "payloads": {"baseline": {}}}


class FakeBackend:
    name = "fake"

    def __init__(self):
        self.focused = False
        self.clicks = []

    def current_position(self):
        return (0, 0)

    def focus_window(self):
        self.focused = True
        return True

    def move_and_click(self, plan, *, button="left"):
        self.clicks.append({"x": plan.click_point.x, "y": plan.click_point.y, "button": button})


class FakeWindowFinder:
    def __init__(self, title="RuneLite", bounds=None):
        self.title = title
        self.bounds = bounds or {"x": 10, "y": 20, "width": 900, "height": 700}

    def __call__(self, _filters):
        return {
            "matchedWindowTitle": self.title,
            "windowBounds": dict(self.bounds),
            "focused": False,
            "focusMethod": "mock",
            "warnings": [],
        }


class RuneLiteBootstrapTest(unittest.TestCase):
    def test_no_world_switching_candidates_generated(self):
        payload = bootstrap.run_bootstrap(
            bootstrap.parse_args(["--skip-runelite-launch", "--dry-run", "--print-candidates"]),
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN"),
            backend=FakeBackend(),
            window_finder=FakeWindowFinder(),
            sleep_func=lambda _seconds: None,
        )

        names = {candidate["name"] for candidate in payload["buttonCandidates"]}
        self.assertIn("click_here_to_play", names)
        self.assertNotIn("world_switch", names)
        self.assertNotIn("world_380", names)

    def test_logged_in_snapshot_skips_clicks(self):
        backend = FakeBackend()
        args = bootstrap.parse_args(["--skip-runelite-launch", "--execute"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGGED_IN"),
            backend=backend,
            window_finder=FakeWindowFinder(),
            daemon_reachable_func=lambda *_args, **_kwargs: False,
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(payload["status"], "PASS")
        self.assertTrue(payload["snapshot"]["loggedIn"])
        self.assertEqual(payload["clickedCandidates"], [])
        self.assertEqual(backend.clicks, [])

    def test_launch_runelite_dry_run_still_launches_process_but_not_clicks(self):
        launches = []
        backend = FakeBackend()
        args = bootstrap.parse_args(["--launch-runelite", "--dry-run"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN"),
            backend=backend,
            window_finder=FakeWindowFinder(),
            launch_func=lambda command, *, execute: launches.append((command, execute)) or {"runeliteLaunched": True, "pid": 123, "command": command},
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(launches, [(".\\gradlew.bat run", True)])
        self.assertTrue(payload["launch"]["runeliteLaunched"])
        self.assertEqual(backend.clicks, [])

    def test_launch_execute_stops_existing_dev_client_before_launch(self):
        stops = []
        launches = []
        args = bootstrap.parse_args(["--launch-runelite", "--execute"])

        bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGGED_IN"),
            backend=FakeBackend(),
            window_finder=FakeWindowFinder(),
            launch_func=lambda command, *, execute: launches.append((command, execute)) or {"runeliteLaunched": True, "pid": 123, "command": command},
            stop_existing_func=lambda *, execute: stops.append(execute) or {"stopped": 2, "warnings": []},
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(stops, [True])
        self.assertEqual(launches, [(".\\gradlew.bat run", True)])

    def test_endpoint_unavailable_reports_waiting_for_snapshot_or_window(self):
        args = bootstrap.parse_args(["--skip-runelite-launch", "--dry-run", "--timeout-seconds", "0.01"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("endpoint down")),
            backend=FakeBackend(),
            window_finder=FakeWindowFinder(),
            sleep_func=lambda _seconds: None,
        )

        self.assertIn(payload["startupStage"], {"waiting_for_snapshot", "waiting_for_window"})
        self.assertFalse(payload["snapshot"]["snapshotReachable"])

    def test_endpoint_not_logged_in_builds_candidates(self):
        args = bootstrap.parse_args(["--skip-runelite-launch", "--dry-run"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN"),
            backend=FakeBackend(),
            window_finder=FakeWindowFinder(),
            sleep_func=lambda _seconds: None,
        )

        self.assertTrue(payload["buttonCandidates"])
        self.assertEqual(payload["buttonCandidates"][0]["source"], "canvas_percent")
        self.assertEqual(payload["buttonCandidates"][0]["screenPoint"], {"x": 1400, "y": 2342})

    def test_tiny_window_without_geometry_waits_instead_of_clicking_percent_fallback(self):
        backend = FakeBackend()
        args = bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--timeout-seconds", "0.01"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: unusable_snapshot(),
            backend=backend,
            window_finder=FakeWindowFinder("RuneLite Launcher", {"x": 10, "y": 20, "width": 200, "height": 274}),
            sleep_func=lambda _seconds: None,
            monotonic_func=iter([0.0, 0.02]).__next__,
        )

        self.assertEqual(payload["clickedCandidates"], [])
        self.assertEqual(backend.clicks, [])
        self.assertIn(payload["startupStage"], {"waiting_for_logged_in", "failed_timeout"})

    def test_click_here_to_play_window_fallback_targets_welcome_play_panel(self):
        candidates = bootstrap.candidates_from_window(
            {"windowBounds": {"x": 4000, "y": 100, "width": 1834, "height": 1350}}
        )
        click_here = next(candidate for candidate in candidates if candidate.name == "click_here_to_play")

        self.assertEqual(click_here.screen_point, {"x": 4972, "y": 945})

    def test_execute_clicks_play_now_candidate_then_repolls_when_snapshot_unavailable(self):
        backend = FakeBackend()
        calls = {"count": 0}

        def fetch(_url, **_kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("endpoint not ready")
            return snapshot("LOGGED_IN")

        payload = bootstrap.run_bootstrap(
            bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--max-startup-clicks", "3"]),
            fetch_snapshot_func=fetch,
            backend=backend,
            window_finder=FakeWindowFinder("RuneLite Launcher"),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual([candidate["name"] for candidate in payload["clickedCandidates"]], ["play_now", "click_here_to_play"])
        self.assertTrue(payload["snapshot"]["loggedIn"])

    def test_execute_clicks_click_here_after_play_now_when_snapshot_still_unusable(self):
        backend = FakeBackend()
        snapshots = [unusable_snapshot(), unusable_snapshot(), unusable_snapshot(), snapshot("LOGGED_IN")]
        windows = [FakeWindowFinder("RuneLite Launcher"), FakeWindowFinder("RuneLite"), FakeWindowFinder("RuneLite")]

        payload = bootstrap.run_bootstrap(
            bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--max-startup-clicks", "3"]),
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshots.pop(0),
            backend=backend,
            window_finder=lambda filters: windows.pop(0)(filters),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual([candidate["name"] for candidate in payload["clickedCandidates"]], ["play_now", "click_here_to_play"])
        self.assertTrue(payload["snapshot"]["loggedIn"])

    def test_play_now_waits_for_server_transition_before_final_click(self):
        backend = FakeBackend()
        sleeps = []
        snapshots = [unusable_snapshot(), unusable_snapshot(), unusable_snapshot(), snapshot("LOGGED_IN")]
        windows = [FakeWindowFinder("RuneLite Launcher"), FakeWindowFinder("RuneLite"), FakeWindowFinder("RuneLite")]

        payload = bootstrap.run_bootstrap(
            bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--max-startup-clicks", "3"]),
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshots.pop(0),
            backend=backend,
            window_finder=lambda filters: windows.pop(0)(filters),
            sleep_func=lambda seconds: sleeps.append(seconds),
        )

        self.assertEqual([candidate["name"] for candidate in payload["clickedCandidates"]], ["play_now", "click_here_to_play"])
        self.assertGreaterEqual(sleeps[0], 7.0)

    def test_logged_in_after_play_now_still_clicks_final_play_panel(self):
        backend = FakeBackend()
        snapshots = [unusable_snapshot(), snapshot("LOGGED_IN"), snapshot("LOGGED_IN"), snapshot("LOGGED_IN")]

        payload = bootstrap.run_bootstrap(
            bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--max-startup-clicks", "3"]),
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshots.pop(0),
            backend=backend,
            window_finder=FakeWindowFinder("RuneLite"),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual([candidate["name"] for candidate in payload["clickedCandidates"]], ["play_now", "click_here_to_play"])
        self.assertTrue(payload["snapshot"]["loggedIn"])

    def test_execute_clicks_play_now_when_snapshot_reachable_but_not_game_ready(self):
        backend = FakeBackend()
        snapshots = [unusable_snapshot(), snapshot("LOGGED_IN"), snapshot("LOGGED_IN"), snapshot("LOGGED_IN")]

        payload = bootstrap.run_bootstrap(
            bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--max-startup-clicks", "3"]),
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshots.pop(0),
            backend=backend,
            window_finder=FakeWindowFinder("RuneLite Launcher"),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual([candidate["name"] for candidate in payload["clickedCandidates"]], ["play_now", "click_here_to_play"])
        self.assertTrue(payload["snapshot"]["loggedIn"])

    def test_execute_clicks_click_here_to_play_candidate_then_repolls_when_snapshot_reachable(self):
        backend = FakeBackend()
        snapshots = [snapshot("LOGIN_SCREEN"), snapshot("LOGGED_IN")]

        payload = bootstrap.run_bootstrap(
            bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--max-startup-clicks", "3"]),
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshots.pop(0),
            backend=backend,
            window_finder=FakeWindowFinder(),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(len(backend.clicks), 1)
        self.assertEqual(payload["clickedCandidates"][0]["name"], "click_here_to_play")
        self.assertTrue(payload["snapshot"]["loggedIn"])

    def test_move_to_secondary_monitor_calls_window_preparer(self):
        placements = []
        backend = FakeBackend()
        args = bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--move-to-secondary-monitor"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGGED_IN"),
            backend=backend,
            window_finder=FakeWindowFinder(),
            window_preparer=lambda filters, options: placements.append((filters, options)) or {
                "matchedWindowTitle": "RuneLite",
                "windowBounds": {"x": 100, "y": 100, "width": 800, "height": 600},
                "originalWindowBounds": {"x": 10, "y": 20, "width": 900, "height": 700},
                "finalWindowBounds": {"x": 100, "y": 100, "width": 800, "height": 600},
                "focused": False,
                "focusMethod": "mock",
                "placement": {"status": "PASS", "monitorTarget": 1, "method": "pygetwindow"},
                "warnings": [],
            },
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(placements[0][1]["move_to_secondary"], True)
        self.assertEqual(payload["window"]["placement"]["status"], "PASS")
        self.assertEqual(payload["window"]["finalWindowBounds"]["x"], 100)

    def test_dry_run_does_not_click(self):
        backend = FakeBackend()
        args = bootstrap.parse_args(["--skip-runelite-launch", "--dry-run"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN"),
            backend=backend,
            window_finder=FakeWindowFinder(),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(payload["status"], "WARN")
        self.assertEqual(payload["clickedCandidates"], [])
        self.assertEqual(backend.clicks, [])

    def test_execute_clicks_at_most_one_candidate_per_stage(self):
        backend = FakeBackend()
        snapshots = [snapshot("LOGIN_SCREEN"), snapshot("LOGGED_IN")]
        args = bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--max-startup-clicks", "3"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshots.pop(0),
            backend=backend,
            window_finder=FakeWindowFinder(),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(len(backend.clicks), 1)
        self.assertEqual(len(payload["clickedCandidates"]), 1)
        self.assertTrue(payload["snapshot"]["loggedIn"])

    def test_stops_after_logged_in(self):
        backend = FakeBackend()
        args = bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--max-startup-clicks", "3"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGGED_IN"),
            backend=backend,
            window_finder=FakeWindowFinder(),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(len(backend.clicks), 0)
        self.assertEqual(payload["startupStage"], "logged_in")

    def test_stops_on_user_login_required_stage(self):
        backend = FakeBackend()
        args = bootstrap.parse_args(["--skip-runelite-launch", "--execute"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("PASSWORD_REQUIRED", geometry=False),
            backend=backend,
            window_finder=FakeWindowFinder("Jagex Launcher"),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(payload["startupStage"], "blocked_user_login_required")
        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(backend.clicks, [])

    def test_max_click_limit_enforced(self):
        backend = FakeBackend()
        args = bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--max-startup-clicks", "1", "--timeout-seconds", "1"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN"),
            backend=backend,
            window_finder=FakeWindowFinder(),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(len(backend.clicks), 1)
        self.assertEqual(payload["startupStage"], "blocked_user_login_required")
        self.assertIn("max startup clicks reached", payload["warnings"])

    def test_daemon_already_listening_is_reused(self):
        starts = []
        args = bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--start-daemon"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGGED_IN"),
            backend=FakeBackend(),
            window_finder=FakeWindowFinder(),
            daemon_reachable_func=lambda *_args, **_kwargs: True,
            start_daemon_func=lambda *_args, **_kwargs: starts.append("started"),
            sleep_func=lambda _seconds: None,
        )

        self.assertTrue(payload["daemon"]["reachable"])
        self.assertEqual(payload["daemon"]["startedOrReused"], "reused")
        self.assertEqual(starts, [])

    def test_json_cli_stdout_only_when_endpoint_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--skip-runelite-launch",
                    "--dry-run",
                    "--timeout-seconds",
                    "0.01",
                    "--json",
                ],
                cwd=temp,
                capture_output=True,
                text=True,
                check=False,
            )
            after = set(os.listdir(temp))

        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema"], "runelite_bootstrap.v1")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

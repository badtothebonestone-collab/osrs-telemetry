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


def snapshot(game_state="LOGIN_SCREEN", *, geometry=True, top_option=None):
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
    payload = {
        "status": "PASS",
        "latestTick": 42,
        "payloads": {"baseline": baseline},
    }
    if top_option is not None:
        payload["clientTickHot"] = {
            "schema": "client_tick_hot.v1",
            "hoverMenu": {
                "topOption": top_option,
                "topTarget": "",
                "topType": "CC_OP" if top_option == "Play" else "WALK",
            },
        }
    return payload


def world_model_snapshot(*, baseline_state="LOGGED_IN", world_model_state="LOGIN_SCREEN", object_total=0):
    payload = snapshot(baseline_state)
    payload["payloads"]["world_model_summary"] = {
        "schema": "world_model_summary.v1",
        "metadata": {"gameState": world_model_state},
        "objects": {"total": object_total},
    }
    return payload


def loaded_scene_snapshot(*, top_option="Walk here"):
    payload = snapshot("LOGGED_IN", top_option=top_option)
    payload["payloads"]["world_model_summary"] = {
        "schema": "world_model_summary.v1",
        "metadata": {"gameState": "LOGGED_IN"},
        "objects": {"total": 67},
    }
    return payload


def unusable_snapshot():
    return {"status": "FAIL", "latestTick": -1, "payloads": {"baseline": {}}}


def geometry_only_snapshot():
    return {"status": "PASS", "latestTick": 42, "payloads": {"baseline": {"inputGeometry": {"geometryAvailable": True}}}}


class FakeBackend:
    name = "fake"

    def __init__(self):
        self.focused = False
        self.clicks = []
        self.position = (0, 0)
        self.command_trace = []
        self.last_movement_trace = None

    def current_position(self):
        return self.position

    def focus_window(self):
        self.focused = True
        return True

    def _record(self, command):
        self.command_trace.append({"status": "PASS", "command": command, "ack": f"OK {command}"})

    def status(self):
        return {
            "status": "PASS",
            "commandCount": len(self.command_trace),
            "commandTrace": list(self.command_trace),
            "lastCommandTrace": self.command_trace[-1] if self.command_trace else None,
        }

    def move(self, plan):
        self.position = (int(plan.click_point.x), int(plan.click_point.y))
        self.last_movement_trace = {
            "chunkCount": 1,
            "cursorPositionAfter": {"x": self.position[0], "y": self.position[1]},
        }
        self._record("MOVE")

    def mouse_down(self, *, button="left"):
        self._record("MOUSE_DOWN")

    def mouse_up(self, *, button="left"):
        self._record("MOUSE_UP")
        self.clicks.append({"x": self.position[0], "y": self.position[1], "button": button, "method": "down_up"})

    def click_at(self, x, y, *, button="left", hold_ms=0):
        self.position = (int(x), int(y))
        self.last_movement_trace = {
            "chunkCount": 1,
            "cursorPositionAfter": {"x": self.position[0], "y": self.position[1]},
        }
        self._record("MOVE")
        self._record("CLICK")
        self.clicks.append({"x": self.position[0], "y": self.position[1], "button": button, "method": "direct_click"})

    def move_and_click(self, plan, *, button="left"):
        self.move(plan)
        self.mouse_down(button=button)
        self.mouse_up(button=button)


class FakeArduinoStartupBackend(FakeBackend):
    name = "arduino"
    arduino_hid_backend = True
    requires_arming = True
    armed = True


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


class FakeClock:
    def __init__(self, start=0.0, step=1.0):
        self.value = float(start)
        self.step = float(step)

    def __call__(self):
        current = self.value
        self.value += self.step
        return current


class RuneLiteBootstrapTest(unittest.TestCase):
    def setUp(self):
        self.template_dir = tempfile.TemporaryDirectory()
        self._old_template_dir = bootstrap.BOOTSTRAP_TEMPLATE_DIR
        bootstrap.BOOTSTRAP_TEMPLATE_DIR = Path(self.template_dir.name)

    def tearDown(self):
        bootstrap.BOOTSTRAP_TEMPLATE_DIR = self._old_template_dir
        self.template_dir.cleanup()

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

    def test_startup_backend_defaults_to_arduino(self):
        args = bootstrap.parse_args(["--skip-runelite-launch", "--dry-run"])

        self.assertEqual(args.startup_backend, "arduino")

    def test_arduino_bootstrap_focuses_matched_window_before_click(self):
        focused = []
        old_focus = bootstrap.focus_matched_os_window

        def fake_focus(window, *, execute):
            focused.append({"window": dict(window or {}), "execute": execute})
            return {
                "focused": True,
                "focusMethod": "mock_focus",
                "foregroundTitle": "RuneLite",
                "warnings": [],
            }

        bootstrap.focus_matched_os_window = fake_focus
        try:
            payload = bootstrap.run_bootstrap(
                bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--print-candidates"]),
                fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN"),
                backend=FakeArduinoStartupBackend(),
                window_finder=FakeWindowFinder(title="RuneLite"),
                vision_candidate_func=lambda *_args, **_kwargs: ([], []),
                disconnected_candidate_func=lambda *_args, **_kwargs: (
                    bootstrap.StartupButtonCandidate(
                        name="disconnected_ok",
                        source="disconnected_dialog",
                        screen_point={"x": 50, "y": 60},
                        canvas_point=None,
                        confidence=0.92,
                        reason="recognized disconnected dialog OK button",
                    ),
                    [],
                ),
                sleep_func=lambda _seconds: None,
                monotonic_func=FakeClock(step=999).__call__,
            )
        finally:
            bootstrap.focus_matched_os_window = old_focus

        self.assertEqual(payload["window"]["focused"], True)
        self.assertEqual(focused[0]["window"]["matchedWindowTitle"], "RuneLite")
        self.assertEqual(payload["clickedCandidates"][0]["name"], "disconnected_ok")

    def test_arduino_bootstrap_refuses_click_when_runelite_focus_not_confirmed(self):
        old_focus = bootstrap.focus_matched_os_window

        def fake_focus(window, *, execute):
            return {
                "focused": False,
                "focusMethod": "mock_focus",
                "foregroundTitle": "Task Manager",
                "warnings": ["window focus not confirmed; foreground='Task Manager'"],
            }

        bootstrap.focus_matched_os_window = fake_focus
        try:
            payload = bootstrap.run_bootstrap(
                bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--max-startup-clicks", "1"]),
                fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN"),
                backend=FakeArduinoStartupBackend(),
                window_finder=FakeWindowFinder(title="RuneLite"),
                vision_candidate_func=lambda *_args, **_kwargs: ([], []),
                window_title_at_point_func=lambda _point: {"available": True, "title": "Task Manager"},
                sleep_func=lambda _seconds: None,
                monotonic_func=FakeClock(step=999).__call__,
            )
        finally:
            bootstrap.focus_matched_os_window = old_focus

        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(payload["startupStage"], "blocked_window_focus_required")
        self.assertIn("foreground_window_not_allowed", payload["failures"])
        self.assertEqual(payload["clickedCandidates"], [])

    def test_arduino_bootstrap_allows_click_when_target_point_is_runelite_window(self):
        old_focus = bootstrap.focus_matched_os_window

        def fake_focus(window, *, execute):
            return {
                "focused": False,
                "focusMethod": "mock_focus",
                "foregroundTitle": "Task Manager",
                "warnings": ["window focus not confirmed; foreground='Task Manager'"],
            }

        snapshots = [snapshot("LOGIN_SCREEN"), loaded_scene_snapshot()]
        bootstrap.focus_matched_os_window = fake_focus
        try:
            payload = bootstrap.run_bootstrap(
                bootstrap.parse_args([
                    "--skip-runelite-launch",
                    "--execute",
                    "--recover-loaded-scene",
                    "--verify-loaded-scene",
                    "--max-startup-clicks",
                    "1",
                ]),
                fetch_snapshot_func=lambda *_args, **_kwargs: snapshots.pop(0),
                backend=FakeArduinoStartupBackend(),
                window_finder=FakeWindowFinder(title="RuneLite"),
                vision_candidate_func=lambda *_args, **_kwargs: ([], []),
                window_title_at_point_func=lambda _point: {"available": True, "title": "RuneLite"},
                sleep_func=lambda _seconds: None,
            )
        finally:
            bootstrap.focus_matched_os_window = old_focus

        self.assertEqual(payload["clickedCandidates"][0]["name"], "click_here_to_play")
        self.assertEqual(payload["clickedCandidates"][0]["clickDetails"]["preClickFocus"]["targetWindowAtPoint"]["title"], "RuneLite")
        self.assertTrue(payload["loadedSceneVerified"])

    def test_startup_movement_region_expands_for_nearby_cursor(self):
        region = {"x": 16, "y": 72, "width": 2243, "height": 1585}

        expanded = bootstrap.startup_movement_region(region, (0, 768), max_padding_px=64)

        self.assertEqual(expanded["x"], 0)
        self.assertEqual(expanded["width"], 2259)
        self.assertEqual(expanded["startupOriginalAllowedRegion"], region)

    def test_startup_movement_region_does_not_expand_for_far_cursor(self):
        region = {"x": 100, "y": 100, "width": 500, "height": 400}

        expanded = bootstrap.startup_movement_region(region, (0, 768), max_padding_px=32)

        self.assertEqual(expanded, region)

    def test_startup_movement_region_can_allow_bounded_corridor_from_current_cursor(self):
        region = {"x": 26, "y": 72, "width": 2223, "height": 1575}

        expanded = bootstrap.startup_movement_region(region, (3253, 1695), max_padding_px=192, max_corridor_px=4096)

        self.assertEqual(expanded["x"], 26)
        self.assertEqual(expanded["width"], 3227)
        self.assertTrue(expanded["startupMovementCorridorToWindow"])
        self.assertEqual(expanded["startupOriginalAllowedRegion"], region)

    def test_saved_account_candidate_wins_over_disconnected_false_positive(self):
        play = bootstrap.StartupButtonCandidate(
            name="play_now",
            source="saved_account_play_panel",
            screen_point={"x": 100, "y": 100},
            canvas_point=None,
            confidence=0.9,
            reason="recognized saved-account Play Now button",
        )
        disconnected = bootstrap.StartupButtonCandidate(
            name="disconnected_ok",
            source="disconnected_dialog",
            screen_point={"x": 100, "y": 200},
            canvas_point=None,
            confidence=0.92,
            reason="recognized disconnected dialog OK button",
        )

        candidates, warnings = bootstrap.button_candidates(
            snapshot("LOGIN_SCREEN"),
            {"matchedWindowTitle": "RuneLite", "windowBounds": {"x": 0, "y": 0, "width": 900, "height": 700}},
            vision_candidate_func=lambda *_args, **_kwargs: ([], []),
            saved_account_candidate_func=lambda *_args, **_kwargs: (play, []),
            disconnected_candidate_func=lambda *_args, **_kwargs: (disconnected, []),
        )

        self.assertEqual([candidate.name for candidate in candidates], ["play_now"])
        self.assertEqual(warnings, [])

    def test_click_here_visual_candidate_wins_over_broad_play_now_detection(self):
        click_here = bootstrap.StartupButtonCandidate(
            name="click_here_to_play",
            source="welcome_panel",
            screen_point={"x": 100, "y": 100},
            canvas_point=None,
            confidence=0.88,
            reason="recognized Click here to play welcome panel",
        )
        play = bootstrap.StartupButtonCandidate(
            name="play_now",
            source="saved_account_play_panel",
            screen_point={"x": 100, "y": 100},
            canvas_point=None,
            confidence=0.90,
            reason="recognized saved-account Play Now button",
        )

        candidates, warnings = bootstrap.button_candidates(
            world_model_snapshot(baseline_state="LOGGED_IN", world_model_state="LOGIN_SCREEN", object_total=0),
            {"matchedWindowTitle": "RuneLite", "windowHandle": 123, "windowBounds": {"x": 0, "y": 0, "width": 900, "height": 700}},
            vision_candidate_func=lambda *_args, **_kwargs: ([], []),
            click_here_candidate_func=lambda *_args, **_kwargs: (click_here, []),
            saved_account_candidate_func=lambda *_args, **_kwargs: (play, []),
            disconnected_candidate_func=lambda *_args, **_kwargs: (None, []),
        )

        self.assertEqual([candidate.name for candidate in candidates], ["click_here_to_play"])
        self.assertEqual(warnings, [])

    def test_stale_logged_in_login_screen_allows_disconnected_dialog(self):
        disconnected = bootstrap.StartupButtonCandidate(
            name="disconnected_ok",
            source="disconnected_dialog",
            screen_point={"x": 100, "y": 200},
            canvas_point=None,
            confidence=0.92,
            reason="recognized disconnected dialog OK button",
        )

        candidates, warnings = bootstrap.button_candidates(
            world_model_snapshot(baseline_state="LOGGED_IN", world_model_state="LOGIN_SCREEN", object_total=0),
            {"matchedWindowTitle": "RuneLite", "windowBounds": {"x": 0, "y": 0, "width": 900, "height": 700}},
            vision_candidate_func=lambda *_args, **_kwargs: ([], []),
            saved_account_candidate_func=lambda *_args, **_kwargs: (None, []),
            disconnected_candidate_func=lambda *_args, **_kwargs: (disconnected, []),
        )

        self.assertEqual([candidate.name for candidate in candidates], ["disconnected_ok"])
        self.assertEqual(warnings, [])

    def test_saved_account_candidate_suppresses_stale_disconnected_false_positive(self):
        play = bootstrap.StartupButtonCandidate(
            name="play_now",
            source="saved_account_play_panel",
            screen_point={"x": 100, "y": 100},
            canvas_point=None,
            confidence=0.9,
            reason="recognized saved-account Play Now button",
        )
        disconnected = bootstrap.StartupButtonCandidate(
            name="disconnected_ok",
            source="disconnected_dialog",
            screen_point={"x": 100, "y": 200},
            canvas_point=None,
            confidence=0.92,
            reason="recognized disconnected dialog OK button",
        )

        candidates, warnings = bootstrap.button_candidates(
            world_model_snapshot(baseline_state="LOGGED_IN", world_model_state="LOGIN_SCREEN", object_total=0),
            {"matchedWindowTitle": "RuneLite", "windowBounds": {"x": 0, "y": 0, "width": 900, "height": 700}},
            vision_candidate_func=lambda *_args, **_kwargs: ([], []),
            saved_account_candidate_func=lambda *_args, **_kwargs: (play, []),
            disconnected_candidate_func=lambda *_args, **_kwargs: (disconnected, []),
        )

        self.assertEqual([candidate.name for candidate in candidates], ["play_now"])
        self.assertTrue(any("Play Now suppressed stale disconnected" in warning for warning in warnings))

    def test_stale_logged_in_without_recognized_button_does_not_use_percent_fallback(self):
        candidates, warnings = bootstrap.button_candidates(
            world_model_snapshot(baseline_state="LOGGED_IN", world_model_state="LOGIN_SCREEN", object_total=0),
            {"matchedWindowTitle": "RuneLite", "windowHandle": 123, "windowBounds": {"x": 0, "y": 0, "width": 900, "height": 700}},
            vision_candidate_func=lambda *_args, **_kwargs: ([], []),
            saved_account_candidate_func=lambda *_args, **_kwargs: (None, ["no play"]),
            disconnected_candidate_func=lambda *_args, **_kwargs: (None, ["no ok"]),
        )

        self.assertEqual(candidates, [])
        self.assertTrue(any("requires a recognized disconnected OK" in warning for warning in warnings))

    def test_logged_in_without_loaded_scene_proof_does_not_use_percent_fallback(self):
        candidates, warnings = bootstrap.button_candidates(
            world_model_snapshot(baseline_state="LOGGED_IN", world_model_state="LOGGED_IN", object_total=0),
            {"matchedWindowTitle": "RuneLite", "windowHandle": 123, "windowBounds": {"x": 0, "y": 0, "width": 900, "height": 700}},
            vision_candidate_func=lambda *_args, **_kwargs: ([], []),
        )

        self.assertEqual(candidates, [])
        self.assertTrue(any("without loaded-scene proof" in warning for warning in warnings))

    def test_click_candidate_uses_arduino_human_input_controller(self):
        backend = FakeArduinoStartupBackend()
        candidate = bootstrap.StartupButtonCandidate(
            name="click_here_to_play",
            source="calibrated_screen",
            screen_point={"x": 50, "y": 60},
            canvas_point=None,
            confidence=1.0,
            reason="test",
        )

        result = bootstrap.click_candidate(
            candidate,
            backend=backend,
            movement_profile="linear_debug",
            input_profile="steady",
            live_input_backend_required=True,
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(backend.clicks), 1)
        self.assertEqual(result["humanInput"]["liveInputBackend"], "arduino")
        self.assertTrue(result["humanInput"]["liveInputBackendRequired"])

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

        self.assertEqual(payload["status"], "WARN")
        self.assertTrue(payload["snapshot"]["loggedIn"])
        self.assertEqual(payload["clickedCandidates"], [])
        self.assertEqual(backend.clicks, [])

    def test_visible_click_here_vetoes_loaded_scene_state(self):
        summary = bootstrap.snapshot_summary(loaded_scene_snapshot(), reachable=True)
        candidate = bootstrap.StartupButtonCandidate(
            name="click_here_to_play",
            source="welcome_panel",
            screen_point={"x": 500, "y": 500},
            canvas_point=None,
            confidence=0.88,
            reason="recognized Click here to play welcome panel",
        )

        updated = bootstrap.apply_visual_loaded_scene_veto(summary, [candidate], [])
        state = bootstrap.bootstrap_state_from_signals(
            summary=updated,
            window={"matchedWindowTitle": "RuneLite"},
            candidates=[candidate],
        )

        self.assertFalse(updated["loadedSceneVerified"])
        self.assertTrue(updated["visualBootstrapSurfacePresent"])
        self.assertEqual(state["state"], "click_here_to_play")
        self.assertFalse(bootstrap.bootstrap_goal_reached(updated, verify_loaded_scene=True))

    def test_execute_clicks_visible_click_here_before_accepting_loaded_scene(self):
        backend = FakeBackend()
        candidate = bootstrap.StartupButtonCandidate(
            name="click_here_to_play",
            source="welcome_panel",
            screen_point={"x": 500, "y": 500},
            canvas_point=None,
            confidence=0.88,
            reason="recognized Click here to play welcome panel",
        )
        calls = {"count": 0}

        def click_here(_window, **_kwargs):
            return (candidate, []) if calls["count"] < 2 else (None, [])

        def fetch(_url, **_kwargs):
            calls["count"] += 1
            return loaded_scene_snapshot()

        payload = bootstrap.run_bootstrap(
            bootstrap.parse_args([
                "--skip-runelite-launch",
                "--execute",
                "--recover-loaded-scene",
                "--verify-loaded-scene",
                "--max-startup-clicks",
                "2",
            ]),
            fetch_snapshot_func=fetch,
            backend=backend,
            window_finder=FakeWindowFinder("RuneLite", {"x": 0, "y": 0, "width": 900, "height": 700}),
            vision_candidate_func=lambda *_args, **_kwargs: ([], []),
            click_here_candidate_func=click_here,
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(payload["clickedCandidates"][0]["name"], "click_here_to_play")
        self.assertTrue(payload["loadedSceneVerified"])
        self.assertEqual(payload["startupStage"], "loaded_scene")

    def test_click_here_still_visible_after_click_is_not_loaded_scene(self):
        backend = FakeBackend()
        candidate = bootstrap.StartupButtonCandidate(
            name="click_here_to_play",
            source="welcome_panel",
            screen_point={"x": 500, "y": 500},
            canvas_point=None,
            confidence=0.88,
            reason="recognized Click here to play welcome panel",
        )

        payload = bootstrap.run_bootstrap(
            bootstrap.parse_args([
                "--skip-runelite-launch",
                "--execute",
                "--recover-loaded-scene",
                "--verify-loaded-scene",
                "--max-startup-clicks",
                "1",
                "--timeout-seconds",
                "0.01",
            ]),
            fetch_snapshot_func=lambda *_args, **_kwargs: loaded_scene_snapshot(),
            backend=backend,
            window_finder=FakeWindowFinder("RuneLite", {"x": 0, "y": 0, "width": 900, "height": 700}),
            vision_candidate_func=lambda *_args, **_kwargs: ([], []),
            click_here_candidate_func=lambda _window, **_kwargs: (candidate, []),
            sleep_func=lambda _seconds: None,
            monotonic_func=FakeClock(step=999).__call__,
        )

        self.assertEqual(payload["clickedCandidates"][0]["name"], "click_here_to_play")
        self.assertFalse(payload["loadedSceneVerified"])
        self.assertEqual(payload["bootstrapState"]["state"], "click_here_to_play")
        self.assertTrue(payload["bootstrapRecommended"])

    def test_world_model_login_screen_overrides_stale_logged_in_baseline(self):
        summary = bootstrap.snapshot_summary(world_model_snapshot(), reachable=True)

        self.assertFalse(summary["loggedIn"])
        self.assertEqual(summary["gameState"], "LOGIN_SCREEN")
        self.assertEqual(summary["baselineGameState"], "LOGGED_IN")
        self.assertEqual(summary["worldModelObjectTotal"], 0)
        self.assertEqual(summary["screenClassification"], "login_screen_or_disconnected_dialog")

    def test_disconnected_dialog_candidate_uses_runelite_window_bounds(self):
        try:
            from PIL import Image, ImageDraw
        except ImportError:  # pragma: no cover
            self.skipTest("Pillow unavailable")

        image = Image.new("RGB", (1000, 800), (20, 20, 20))
        draw = ImageDraw.Draw(image)
        draw.rectangle((220, 250, 780, 590), fill=(88, 55, 50), outline=(5, 5, 5), width=8)
        draw.rectangle((380, 445, 620, 535), fill=(58, 34, 32), outline=(0, 0, 0), width=10)
        draw.text((485, 475), "Ok", fill=(240, 240, 240))

        candidate, warnings = bootstrap.disconnected_dialog_candidate(
            {"matchedWindowTitle": "RuneLite - Test", "windowBounds": {"x": 0, "y": 0, "width": 1000, "height": 800}},
            screenshot_func=lambda: image,
        )

        self.assertEqual(warnings, [])
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.name, "disconnected_ok")
        self.assertEqual(candidate.screen_point, {"x": 500, "y": 488})
        self.assertEqual(candidate.source, "disconnected_dialog")

    def test_disconnected_dialog_candidate_uses_canvas_surface_bounds(self):
        try:
            from PIL import Image, ImageDraw
        except ImportError:  # pragma: no cover
            self.skipTest("Pillow unavailable")

        image = Image.new("RGB", (1400, 900), (20, 20, 20))
        draw = ImageDraw.Draw(image)
        draw.rectangle((460, 250, 1140, 590), fill=(88, 55, 50), outline=(5, 5, 5), width=8)
        draw.rectangle((680, 445, 920, 535), fill=(58, 34, 32), outline=(0, 0, 0), width=10)

        candidate, warnings = bootstrap.disconnected_dialog_candidate(
            {"matchedWindowTitle": "RuneLite - Test", "windowBounds": {"x": 0, "y": 0, "width": 1400, "height": 900}},
            screenshot_func=lambda: image,
            surface_bounds={"x": 300, "y": 0, "width": 1000, "height": 800},
        )

        self.assertEqual(warnings, [])
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.screen_point, {"x": 800, "y": 488})
        self.assertEqual(candidate.coordinate_scale["source"], "input_geometry_canvas")

    def test_click_candidate_rejects_unsafe_titlebar_target(self):
        backend = FakeBackend()
        candidate = bootstrap.StartupButtonCandidate(
            name="click_here_to_play",
            source="template",
            screen_point={"x": 50, "y": 15},
            canvas_point=None,
            confidence=0.9,
            reason="test",
            target_validation=bootstrap.validate_bootstrap_click_point(
                {"x": 50, "y": 15},
                {"matchedWindowTitle": "RuneLite", "physicalWindowBounds": {"x": 0, "y": 0, "width": 800, "height": 600}},
            ),
        )

        result = bootstrap.click_candidate(
            candidate,
            backend=backend,
            movement_profile="linear_debug",
            input_profile="steady",
        )

        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(backend.clicks, [])
        self.assertIn("titlebar", result["warning"])

    def test_execute_clicks_disconnected_ok_before_generic_login_candidate(self):
        backend = FakeBackend()
        snapshots = [world_model_snapshot(), snapshot("LOGIN_SCREEN")]
        candidate = bootstrap.StartupButtonCandidate(
            name="disconnected_ok",
            source="disconnected_dialog",
            screen_point={"x": 500, "y": 488},
            canvas_point=None,
            confidence=0.92,
            reason="test disconnected dialog",
        )

        payload = bootstrap.run_bootstrap(
            bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--max-startup-clicks", "1", "--timeout-seconds", "0.01"]),
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshots.pop(0),
            backend=backend,
            window_finder=FakeWindowFinder("RuneLite"),
            sleep_func=lambda _seconds: None,
            disconnected_candidate_func=lambda _window: (candidate, []),
        )

        self.assertEqual(payload["clickedCandidates"][0]["name"], "disconnected_ok")
        self.assertEqual(payload["clickedCandidates"][0]["candidateMethod"], "disconnected_dialog")
        self.assertEqual(backend.clicks, [{"x": 500, "y": 488, "button": "left", "method": "down_up"}])

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
        self.assertEqual(payload["buttonCandidates"][0]["candidateMethod"], "percent_fallback")
        self.assertEqual(payload["buttonCandidates"][0]["screenPoint"], {"x": 1400, "y": 2402})
        self.assertIn("templateStatus", payload)

    def test_template_dir_and_confidence_are_passed_to_vision(self):
        args = bootstrap.parse_args([
            "--skip-runelite-launch",
            "--dry-run",
            "--template-dir",
            "custom_templates",
            "--template-confidence",
            "0.91",
        ])
        seen = []

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN"),
            backend=FakeBackend(),
            window_finder=FakeWindowFinder(),
            vision_candidate_func=lambda template_dir, **kwargs: seen.append((str(template_dir), kwargs.get("confidence"))) or ([], []),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(seen, [("custom_templates", 0.91)])
        self.assertEqual(payload["templateStatus"]["confidence"], 0.91)

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

        self.assertEqual(click_here.screen_point, {"x": 4972, "y": 1004})

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
        snapshots = [geometry_only_snapshot(), snapshot("LOGGED_IN"), snapshot("LOGGED_IN"), snapshot("LOGGED_IN")]

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

    def test_saved_account_preference_chooses_play_now_on_runelite_login_screen(self):
        candidates = [
            bootstrap.StartupButtonCandidate(
                name="click_here_to_play",
                source="calibrated_screen",
                screen_point={"x": 10, "y": 20},
                canvas_point=None,
                confidence=0.95,
                reason="welcome play button",
            ),
            bootstrap.StartupButtonCandidate(
                name="play_now",
                source="saved_account_play_panel",
                screen_point={"x": 30, "y": 40},
                canvas_point=None,
                confidence=0.90,
                reason="saved account play panel",
            ),
        ]

        chosen = bootstrap.choose_candidate(
            candidates,
            snapshot_reachable=True,
            window={"matchedWindowTitle": "RuneLite"},
            prefer_saved_account_play_now=True,
        )

        self.assertEqual(chosen.name, "play_now")

    def test_default_runelite_login_preference_keeps_click_here_first(self):
        candidates = [
            bootstrap.StartupButtonCandidate(
                name="click_here_to_play",
                source="calibrated_screen",
                screen_point={"x": 10, "y": 20},
                canvas_point=None,
                confidence=0.95,
                reason="welcome play button",
            ),
            bootstrap.StartupButtonCandidate(
                name="play_now",
                source="saved_account_play_panel",
                screen_point={"x": 30, "y": 40},
                canvas_point=None,
                confidence=0.90,
                reason="saved account play panel",
            ),
        ]

        chosen = bootstrap.choose_candidate(
            candidates,
            snapshot_reachable=True,
            window={"matchedWindowTitle": "RuneLite"},
            prefer_saved_account_play_now=False,
        )

        self.assertEqual(chosen.name, "click_here_to_play")

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

    def test_logged_in_with_play_top_option_clicks_final_play_panel(self):
        backend = FakeBackend()
        snapshots = [snapshot("LOGGED_IN", top_option="Play"), snapshot("LOGGED_IN", top_option="Walk here")]
        args = bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--max-startup-clicks", "3"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshots.pop(0),
            backend=backend,
            window_finder=FakeWindowFinder("RuneLite"),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(payload["clickedCandidates"][0]["name"], "click_here_to_play")
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

    def test_jagex_launcher_automation_blocked_by_default(self):
        backend = FakeBackend()
        args = bootstrap.parse_args(["--skip-runelite-launch", "--execute"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN"),
            backend=backend,
            window_finder=FakeWindowFinder("Jagex Launcher"),
            sleep_func=lambda _seconds: None,
        )

        self.assertEqual(payload["startupStage"], "blocked_user_login_required")
        self.assertEqual(payload["loginRecoveryMode"], "manual_required")
        self.assertFalse(payload["launcherAutomationAllowed"])
        self.assertEqual(payload["launcherAutomationBlockedReason"], "jagex_launcher_automation_disabled_by_default")
        self.assertEqual(backend.clicks, [])

    def test_jagex_launcher_automation_requires_explicit_flag(self):
        backend = FakeBackend()
        snapshots = [snapshot("LOGIN_SCREEN"), snapshot("LOGGED_IN")]
        args = bootstrap.parse_args(["--skip-runelite-launch", "--execute", "--allow-jagex-launcher-automation"])

        payload = bootstrap.run_bootstrap(
            args,
            fetch_snapshot_func=lambda *_args, **_kwargs: snapshots.pop(0),
            backend=backend,
            window_finder=FakeWindowFinder("Jagex Launcher"),
            sleep_func=lambda _seconds: None,
        )

        self.assertTrue(payload["launcherAutomationAllowed"])
        self.assertEqual(payload["loginRecoveryMode"], "launcher_allowed")
        self.assertEqual(len(backend.clicks), 1)

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

    def test_repeated_visible_button_tries_direct_click_then_dead_surface(self):
        backend = FakeArduinoStartupBackend()
        old_focus = bootstrap.focus_matched_os_window

        def fake_focus(window, *, execute):
            return {
                "focused": True,
                "focusMethod": "mock_focus",
                "foregroundTitle": "RuneLite",
                "warnings": [],
            }

        bootstrap.focus_matched_os_window = fake_focus
        try:
            payload = bootstrap.run_bootstrap(
                bootstrap.parse_args([
                    "--skip-runelite-launch",
                    "--execute",
                    "--recover-loaded-scene",
                    "--verify-loaded-scene",
                    "--max-startup-clicks",
                    "3",
                    "--timeout-seconds",
                    "10",
                ]),
                fetch_snapshot_func=lambda *_args, **_kwargs: snapshot("LOGIN_SCREEN"),
                backend=backend,
                window_finder=FakeWindowFinder(title="RuneLite"),
                vision_candidate_func=lambda *_args, **_kwargs: ([], []),
                disconnected_candidate_func=lambda *_args, **_kwargs: (
                    bootstrap.StartupButtonCandidate(
                        name="disconnected_ok",
                        source="disconnected_dialog",
                        screen_point={"x": 50, "y": 60},
                        canvas_point=None,
                        confidence=0.92,
                        reason="recognized disconnected dialog OK button",
                    ),
                    [],
                ),
                window_title_at_point_func=lambda _point: {"available": True, "title": "RuneLite"},
                sleep_func=lambda _seconds: None,
                monotonic_func=FakeClock(step=0.1).__call__,
            )
        finally:
            bootstrap.focus_matched_os_window = old_focus

        self.assertEqual(payload["startupStage"], "stale_dead_runelite_instance")
        self.assertIn("stale_dead_runelite_instance", payload["failures"])
        methods = [item["clickDetails"]["recoveryInputMethod"] for item in payload["clickedCandidates"]]
        self.assertEqual(methods, ["move_and_down_up", "direct_click"])
        self.assertEqual([item["method"] for item in backend.clicks], ["down_up", "direct_click"])

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

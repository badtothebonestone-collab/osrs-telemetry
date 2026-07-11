from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from osrs_bot.input_coordinator import InputCoordinator
from osrs_bot.login import (
    LoginCandidate,
    LoginPromptHelper,
    RuneLiteWindow,
    TEMPLATE_DIR,
    detect_login_surfaces,
)
from osrs_bot.model import (
    InventoryObservation,
    Observation,
    PlayerObservation,
    ScreenBounds,
    ScreenPoint,
    WidgetObservation,
    WorldPoint,
)


def observation(
    game_state: str,
    tick: int,
    *,
    loaded: bool = False,
    pid: int | None = 4242,
) -> Observation:
    timestamp = datetime.now(timezone.utc)
    session_id = "login-session"
    frame_id = f"test-frame-{tick}"
    return Observation(
        player=PlayerObservation(),
        location=WorldPoint(3192, 3244, 0) if loaded else None,
        plane=0 if loaded else None,
        inventory=InventoryObservation(),
        nearby_objects=(),
        menus=(),
        widgets=WidgetObservation(),
        canvas_bounds=ScreenBounds(100, 100, 1000, 700),
        game_state=game_state,
        timestamp=timestamp,
        tick=tick,
        status="PASS",
        fresh=True,
        cache_wall_clock_fresh=True,
        scene_playable=loaded,
        session_id=session_id,
        client_focused=True,
        client_process_id=pid,
        assembled_at=timestamp,
        frame_id=frame_id,
        geometry_frame_id=frame_id,
        source_coherent=True,
        menu_fresh=True,
        menu_source_tick=tick,
        menu_timestamp=timestamp,
        menu_session_id=session_id,
        menu_process_id=pid,
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeObservations:
    def __init__(self, values: list[Observation], *, repeat_last: bool = False) -> None:
        self.values = list(values)
        self.repeat_last = repeat_last
        self.last: Observation | None = None

    def fetch(self) -> Observation:
        if self.values:
            self.last = self.values.pop(0)
            return self.last
        if self.repeat_last and self.last is not None:
            return self.last
        raise AssertionError("unexpected observation fetch")


class FakeBackend:
    def __init__(
        self,
        *,
        fail_at: str | None = None,
        start: tuple[int, int] = (150, 500),
    ) -> None:
        self.calls: list[object] = []
        self.fail_at = fail_at
        self.position = start
        self.records: list[dict[str, object]] = []
        self.sequence = 0

    def _call(self, name: str, value: object | None = None) -> None:
        self.calls.append(name if value is None else (name, value))
        if self.fail_at == name:
            raise RuntimeError(f"failed at {name}")

    def _record(self, command: str, *, fail_name: str | None = None) -> None:
        self.sequence += 1
        failed = fail_name is not None and self.fail_at == fail_name
        self.records.append(
            {
                "schema": "arduino_command_evidence.v1",
                "commandId": f"cmd-{self.sequence:08d}",
                "sequence": self.sequence,
                "command": command,
                "status": "REJECTED" if failed else "PASS",
                "writeOk": True,
                "ackReceived": True,
                "accepted": not failed,
                "firmwareAck": {
                    "responseToken": "OK" if not failed else "ERR",
                    "payloadToken": command,
                },
                "error": f"failed at {fail_name}" if failed else None,
                "timeoutClassification": None,
                "retryCount": 0,
            }
        )
        if failed:
            raise RuntimeError(f"failed at {fail_name}")

    def _begin_command_ledger(self) -> None:
        self.records = []

    def _command_evidence(self) -> dict[str, object]:
        failed = sum(record["status"] != "PASS" for record in self.records)
        missing = sum(not record["ackReceived"] for record in self.records)
        return {
            "schema": "arduino_command_ledger.v1",
            "records": [dict(record) for record in self.records],
            "unresolvedCount": 0,
            "failedCount": failed,
            "ackMissingCount": missing,
        }

    def _end_command_ledger(self) -> dict[str, object]:
        return self._command_evidence()

    def _connect(self) -> None:
        self._call("connect")

    def _current_position(self) -> tuple[int, int]:
        self._call("current_position")
        return self.position

    def _arm(self) -> dict[str, object]:
        self._call("arm")
        self._record("ARM", fail_name="arm")
        return {}

    def _assert_foreground(self, titles: list[str], *, expected_pid: int) -> None:
        self._call("foreground", expected_pid)

    def _move_relative(self, dx: int, dy: int) -> dict[str, object]:
        self._call("move", {"dx": dx, "dy": dy})
        self._record("MOVE", fail_name="move")
        self.position = (self.position[0] + dx, self.position[1] + dy)
        return {}

    def _mouse_down(self, *, button: str) -> None:
        self._call("mouse_down")
        self._record("MOUSE_DOWN", fail_name="mouse_down")

    def _mouse_up(self, *, button: str) -> None:
        self._call("mouse_up")
        self._record("MOUSE_UP", fail_name="mouse_up")

    def _press(self, key: str) -> None:
        self._call("press", key)
        self._record("KEY_PRESS", fail_name="press")

    def _stop_all(self) -> dict[str, object]:
        self._call("stop_all")
        self._record("STOP_ALL", fail_name="stop_all")
        return {}

    def _disarm(self) -> dict[str, object]:
        self._call("disarm")
        self._record("DISARM", fail_name="disarm")
        return {}

    def _firmware_status(self) -> dict[str, object]:
        self._call("firmware_status")
        self._record("STATUS", fail_name="firmware_status")
        return {"armed": False, "keysDown": 0, "mouseButtonsDown": 0}

    def _close(self) -> None:
        self._call("close")


WINDOW = RuneLiteWindow(77, 4242, "RuneLite - test", ScreenBounds(100, 100, 1000, 700))
PLAY = LoginCandidate("play_now", ScreenPoint(500, 340), ScreenBounds(420, 300, 160, 80), 0.98)
WELCOME = LoginCandidate(
    "click_here_to_play", ScreenPoint(500, 480), ScreenBounds(380, 440, 240, 80), 0.97
)
DISCONNECTED = LoginCandidate(
    "disconnected_ok", ScreenPoint(500, 427), ScreenBounds(400, 396, 200, 63), 0.92
)


def build_helper(
    source: FakeObservations,
    detector,
    *,
    backends: list[FakeBackend] | None = None,
    fail_at: str | None = None,
    cursor_start: tuple[int, int] = (150, 500),
    clock: FakeClock | None = None,
) -> LoginPromptHelper:
    clock = clock or FakeClock()
    collection = backends if backends is not None else []

    def backend_factory() -> FakeBackend:
        backend = FakeBackend(fail_at=fail_at, start=cursor_start)
        collection.append(backend)
        return backend

    coordinator = InputCoordinator(
        backend_factory,
        sleep=clock.sleep,
        pointer_timestep_seconds=0.02,
    )
    return LoginPromptHelper(
        source,
        coordinator,
        window_finder=lambda pid: WINDOW,
        focus_window=lambda window: True,
        point_owner=lambda window, point: window.client_bounds.contains(point),
        screenshot=lambda bounds: Image.new("RGB", (bounds.width, bounds.height), (20, 20, 20)),
        detector=detector,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        poll_seconds=0.1,
        transition_seconds=0.3,
    )


class LoginDetectionTests(unittest.TestCase):
    def test_detects_each_genuine_supported_template(self) -> None:
        placements = {
            "play_now": (470, 330),
            "click_here_to_play": (400, 500),
        }
        for name, placement in placements.items():
            with self.subTest(name=name):
                screenshot = Image.new("RGB", (1200, 800), (55, 18, 14))
                with Image.open(Path(TEMPLATE_DIR) / f"{name}.png") as template:
                    screenshot.paste(template.convert("RGB"), placement)
                candidates = detect_login_surfaces(screenshot)
                self.assertEqual([candidate.name for candidate in candidates], [name])
                self.assertGreaterEqual(candidates[0].confidence, 0.90)

    def test_unknown_or_credential_like_surface_is_never_a_candidate(self) -> None:
        screenshot = Image.new("RGB", (1200, 800), (25, 25, 25))
        # Input-box-like rectangles are intentionally not actionable evidence.
        for x in range(400, 800):
            for y in range(300, 340):
                screenshot.putpixel((x, y), (220, 220, 220))
        self.assertEqual(detect_login_surfaces(screenshot), ())

    def test_detects_narrow_idle_disconnect_dialog_geometry(self) -> None:
        screenshot = Image.new("RGB", (1000, 700), (55, 18, 14))
        for x in range(250, 750):
            for y in range(238, 483):
                screenshot.putpixel((x, y), (60, 38, 32))
        center_x, center_y = 500, 427
        for x in range(center_x - 120, center_x + 120):
            for y in range(center_y - 45, center_y + 45):
                screenshot.putpixel((x, y), (55, 42, 35))
        for x in range(center_x - 120, center_x + 120):
            for y in (*range(center_y - 45, center_y - 30), *range(center_y + 30, center_y + 45)):
                screenshot.putpixel((x, y), (15, 10, 8))
        for x in range(center_x - 12, center_x + 13, 4):
            for y in range(center_y - 10, center_y + 11):
                screenshot.putpixel((x, y), (235, 220, 185))

        candidates = detect_login_surfaces(screenshot)

        self.assertEqual([candidate.name for candidate in candidates], ["disconnected_ok"])

    def test_flat_login_surface_is_not_a_disconnect_dialog(self) -> None:
        screenshot = Image.new("RGB", (1000, 700), (55, 38, 32))
        self.assertEqual(detect_login_surfaces(screenshot), ())

    def test_detects_play_now_at_the_live_high_dpi_scale(self) -> None:
        screenshot = Image.new("RGB", (2219, 1573), (55, 18, 14))
        with Image.open(Path(TEMPLATE_DIR) / "play_now.png") as template:
            scaled = template.convert("RGB").resize(
                (round(template.width * 1.17), round(template.height * 1.17)),
                Image.Resampling.BILINEAR,
            )
            screenshot.paste(scaled, (966, 760))

        candidates = detect_login_surfaces(screenshot)

        self.assertEqual([candidate.name for candidate in candidates], ["play_now"])


class LoginHelperTests(unittest.TestCase):
    def test_disconnect_then_two_prompt_path_is_bounded_and_verified(self) -> None:
        source = FakeObservations(
            [
                observation("LOGIN_SCREEN", 1),
                observation("LOGIN_SCREEN", 2),
                observation("LOGIN_SCREEN", 3),
                observation("LOGGING_IN", 4),
                observation("LOGGED_IN", 5),
                observation("LOGGED_IN", 6, loaded=True),
                observation("LOGGED_IN", 7, loaded=True),
                observation("LOGGED_IN", 8, loaded=True),
            ]
        )
        detections = iter((
            (DISCONNECTED,), (DISCONNECTED,), (),
            (PLAY,), (PLAY,), (WELCOME,), (WELCOME,), (), (),
        ))
        backends: list[FakeBackend] = []
        helper = build_helper(source, lambda image: next(detections), backends=backends)

        result = helper.run(timeout_seconds=10)

        self.assertTrue(result.successful)
        self.assertEqual(
            [click.name for click in result.clicks],
            ["disconnected_ok", "play_now", "click_here_to_play"],
        )
        self.assertEqual(3, len(backends))
        for backend in backends:
            self.assertIn("stop_all", backend.calls)
            self.assertIn("disarm", backend.calls)

    def test_two_prompt_path_uses_only_arduino_and_verifies_loaded_scene(self) -> None:
        source = FakeObservations(
            [
                observation("LOGIN_SCREEN", 1),
                observation("LOGGING_IN", 2),
                observation("LOGGED_IN", 3),
                observation("LOGGED_IN", 4, loaded=True),
                observation("LOGGED_IN", 5, loaded=True),
                observation("LOGGED_IN", 6, loaded=True),
            ]
        )
        detections = iter(((PLAY,), (PLAY,), (WELCOME,), (WELCOME,), (), ()))
        backends: list[FakeBackend] = []
        helper = build_helper(source, lambda image: next(detections), backends=backends)

        result = helper.run(timeout_seconds=10)

        self.assertTrue(result.successful)
        self.assertEqual([click.name for click in result.clicks], ["play_now", "click_here_to_play"])
        self.assertEqual(len(backends), 2)
        for backend in backends:
            self.assertIn("mouse_down", backend.calls)
            self.assertIn("mouse_up", backend.calls)
            self.assertIn("stop_all", backend.calls)
            self.assertIn("disarm", backend.calls)
            self.assertIn("firmware_status", backend.calls)
            self.assertEqual(backend.calls[-1], "close")
        self.assertTrue(all(click.receipt.successful for click in result.clicks))
        self.assertTrue(
            all(
                click.receipt.firmware_status
                and click.receipt.firmware_status.safe
                for click in result.clicks
            )
        )

    def test_loaded_scene_returns_without_hardware_connection(self) -> None:
        backends: list[FakeBackend] = []
        helper = build_helper(
            FakeObservations([
                observation("LOGGED_IN", 9, loaded=True),
                observation("LOGGED_IN", 10, loaded=True),
            ]),
            lambda image: (),
            backends=backends,
        )
        result = helper.run()
        self.assertTrue(result.successful)
        self.assertEqual(backends, [])

    def test_loaded_telemetry_does_not_skip_a_visible_welcome_prompt(self) -> None:
        source = FakeObservations(
            [
                observation("LOGGED_IN", 9, loaded=True),
                observation("LOGGED_IN", 10, loaded=True),
                observation("LOGGED_IN", 11, loaded=True),
                observation("LOGGED_IN", 12, loaded=True),
            ]
        )
        detections = iter(((WELCOME,), (WELCOME,), (), ()))
        backends: list[FakeBackend] = []
        helper = build_helper(source, lambda image: next(detections), backends=backends)

        result = helper.run()

        self.assertTrue(result.successful)
        self.assertEqual([click.name for click in result.clicks], ["click_here_to_play"])
        self.assertEqual(1, len(backends))

    def test_missing_pid_fails_closed_without_hardware(self) -> None:
        backends: list[FakeBackend] = []
        helper = build_helper(
            FakeObservations([observation("LOGIN_SCREEN", 1, pid=None)]),
            lambda image: (PLAY,),
            backends=backends,
        )
        result = helper.run()
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.reason, "telemetry_client_process_id_unavailable")
        self.assertEqual(backends, [])

    def test_unknown_surface_fails_without_hardware(self) -> None:
        backends: list[FakeBackend] = []
        helper = build_helper(
            FakeObservations([observation("LOGIN_SCREEN", 1)], repeat_last=True),
            lambda image: (),
            backends=backends,
        )
        result = helper.run(timeout_seconds=5)
        self.assertEqual(result.reason, "no_supported_authenticated_prompt")
        self.assertEqual(backends, [])

    def test_ambiguous_surface_fails_without_hardware(self) -> None:
        backends: list[FakeBackend] = []
        helper = build_helper(
            FakeObservations([observation("LOGIN_SCREEN", 1)]),
            lambda image: (PLAY, WELCOME),
            backends=backends,
        )
        result = helper.run()
        self.assertEqual(result.reason, "ambiguous_supported_prompts")
        self.assertEqual(backends, [])

    def test_candidate_outside_client_fails_without_hardware(self) -> None:
        outside = LoginCandidate("play_now", ScreenPoint(1200, 340), ScreenBounds(1150, 300, 100, 80), 0.99)
        backends: list[FakeBackend] = []
        helper = build_helper(
            FakeObservations([observation("LOGIN_SCREEN", 1)]),
            lambda image: (outside,),
            backends=backends,
        )
        result = helper.run()
        self.assertEqual(result.reason, "candidate_outside_exact_runelite_client")
        self.assertEqual(backends, [])

    def test_cursor_must_start_inside_the_verified_runelite_canvas(self) -> None:
        backends: list[FakeBackend] = []
        helper = build_helper(
            FakeObservations([observation("LOGIN_SCREEN", 1)]),
            lambda image: (PLAY,),
            backends=backends,
            cursor_start=(1500, 500),
        )

        result = helper.run()

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("cursor_start_outside_verified", result.reason)
        self.assertNotIn("mouse_down", backends[0].calls)
        self.assertIn("stop_all", backends[0].calls)
        self.assertIn("firmware_status", backends[0].calls)
        self.assertFalse(result.clicks[-1].sent)

    def test_click_failure_still_stops_and_disarms(self) -> None:
        backends: list[FakeBackend] = []
        helper = build_helper(
            FakeObservations([observation("LOGIN_SCREEN", 1)]),
            lambda image: (PLAY,),
            backends=backends,
            fail_at="mouse_up",
        )
        result = helper.run()
        self.assertEqual(result.status, "ERROR")
        self.assertIn("login_click_error", result.reason)
        self.assertIn("stop_all", backends[0].calls)
        self.assertIn("disarm", backends[0].calls)
        self.assertEqual(backends[0].calls[-1], "close")
        self.assertFalse(result.clicks[-1].receipt.successful)

    def test_prompt_disappearing_after_move_blocks_click_and_cleans_up(self) -> None:
        backends: list[FakeBackend] = []
        detections = iter(((PLAY,), ()))
        helper = build_helper(
            FakeObservations([observation("LOGIN_SCREEN", 1)]),
            lambda image: next(detections),
            backends=backends,
        )
        result = helper.run()
        self.assertEqual(result.status, "BLOCKED")
        self.assertIn("prompt disappeared", result.reason)
        self.assertNotIn("mouse_down", backends[0].calls)
        self.assertIn("stop_all", backends[0].calls)
        self.assertIn("disarm", backends[0].calls)

    def test_cleanup_failure_overrides_prior_connected_attempt_result(self) -> None:
        for failure in ("stop_all", "disarm"):
            with self.subTest(failure=failure):
                backends: list[FakeBackend] = []
                helper = build_helper(
                    FakeObservations([observation("LOGIN_SCREEN", 1)]),
                    lambda image: (PLAY,),
                    backends=backends,
                    fail_at=failure,
                )
                result = helper.run()
                self.assertEqual(result.status, "ERROR")
                self.assertIn(f"{failure}_failed", result.reason)
                self.assertFalse(result.clicks[-1].receipt.successful)

    def test_unchanged_prompt_is_clicked_only_once(self) -> None:
        source = FakeObservations([observation("LOGIN_SCREEN", 1)], repeat_last=True)
        backends: list[FakeBackend] = []
        helper = build_helper(source, lambda image: (PLAY,), backends=backends)
        result = helper.run(timeout_seconds=5)
        self.assertEqual(result.reason, "play_now_did_not_transition")
        self.assertEqual(len(backends), 1)
        self.assertEqual(len(result.clicks), 1)


if __name__ == "__main__":
    unittest.main()

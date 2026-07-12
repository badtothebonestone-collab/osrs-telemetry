from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

from osrs_bot import login as login_module
from osrs_bot.input_coordinator import (
    InputCoordinator,
    InputFailureKind,
    InputReceipt,
)
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


class FakeWinFunction:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class FakeDpiUser32:
    def __init__(
        self,
        *,
        per_monitor_v2: bool,
        setter_result: bool = True,
        per_monitor_v2_after_set: bool = True,
    ) -> None:
        self.per_monitor_v2 = per_monitor_v2
        self.setter_result = setter_result
        self.per_monitor_v2_after_set = per_monitor_v2_after_set
        self.setter_calls = 0
        self.GetThreadDpiAwarenessContext = FakeWinFunction(lambda: 1234)
        self.AreDpiAwarenessContextsEqual = FakeWinFunction(
            lambda _active, _expected: self.per_monitor_v2
        )
        self.SetProcessDpiAwarenessContext = FakeWinFunction(self._set_context)

    def _set_context(self, _context) -> bool:
        self.setter_calls += 1
        if self.setter_result:
            self.per_monitor_v2 = self.per_monitor_v2_after_set
        return self.setter_result


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
        foreground_hwnd: int = 77,
        change_position_on_call: int | None = None,
        window_handoff_callback=None,
    ) -> None:
        self.calls: list[object] = []
        self.fail_at = fail_at
        self.position = start
        self.foreground_hwnd = foreground_hwnd
        self.change_position_on_call = change_position_on_call
        self.window_handoff_callback = window_handoff_callback
        self.owned_transition_pending: str | None = None
        self.position_call_count = 0
        self.armed = False
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
        self.position_call_count += 1
        if self.position_call_count == self.change_position_on_call:
            self.position = (self.position[0] + 1, self.position[1])
        return self.position

    def _arm(self) -> dict[str, object]:
        self._call("arm")
        self._record("ARM", fail_name="arm")
        self.armed = True
        return {}

    def _assert_foreground(self, titles: list[str], *, expected_pid: int) -> dict[str, int]:
        self._call("foreground", expected_pid)
        return {"pid": expected_pid, "hwnd": self.foreground_hwnd}

    def _window_info_at_point(self, point: tuple[int, int]) -> dict[str, int]:
        self._call("point_owner", point)
        return {"pid": 4242, "hwnd": 77}

    def _reposition_window_for_cursor(
        self,
        *,
        expected_pid: int,
        expected_hwnd: int,
        cursor: tuple[int, int],
        movement_bounds: tuple[int, int, int, int],
        inset_px: int,
    ) -> dict[str, object]:
        self._call("window_handoff")
        x, y, width, height = movement_bounds
        cursor_x, cursor_y = cursor
        dx = 0
        dy = 0
        if cursor_x < x + inset_px:
            dx = cursor_x - (x + inset_px)
        elif cursor_x >= x + width - inset_px:
            dx = cursor_x - (x + width - inset_px - 1)
        if cursor_y < y + inset_px:
            dy = cursor_y - (y + inset_px)
        elif cursor_y >= y + height - inset_px:
            dy = cursor_y - (y + height - inset_px - 1)
        evidence: dict[str, object] = {
            "schema": "cursor_window_handoff.v1",
            "expectedPid": expected_pid,
            "expectedHwnd": expected_hwnd,
            "cursor": {"x": cursor_x, "y": cursor_y},
            "oldMovementBounds": {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            },
            "newMovementBounds": {
                "x": x + dx,
                "y": y + dy,
                "width": width,
                "height": height,
            },
            "repositioned": True,
            "cursorUnchanged": True,
            "buttonsUpConfirmed": True,
            "foregroundConfirmed": True,
            "pointOwnerConfirmed": True,
        }
        if self.window_handoff_callback is not None:
            self.window_handoff_callback(evidence)
        return evidence

    def _verify_window_geometry(
        self,
        *,
        expected_pid: int,
        expected_hwnd: int,
        expected_outer_bounds: tuple[int, int, int, int] | None,
        expected_client_bounds: tuple[int, int, int, int] | None,
        required_inner_bounds: tuple[int, int, int, int],
    ) -> dict[str, object]:
        self._call("window_geometry")
        def payload(
            values: tuple[int, int, int, int] | None,
        ) -> dict[str, int] | None:
            if values is None:
                return None
            x, y, width, height = values
            return {"x": x, "y": y, "width": width, "height": height}

        actual_outer = expected_outer_bounds or expected_client_bounds
        actual_client = expected_client_bounds or required_inner_bounds
        return {
            "schema": "cursor_window_geometry.v1",
            "expectedPid": expected_pid,
            "expectedHwnd": expected_hwnd,
            "expectedOuterBounds": payload(expected_outer_bounds),
            "expectedClientBounds": payload(expected_client_bounds),
            "requiredInnerBounds": payload(required_inner_bounds),
            "actualOuterBounds": payload(actual_outer),
            "actualClientBounds": payload(actual_client),
            "outerMatches": (
                None if expected_outer_bounds is None else True
            ),
            "clientMatches": (
                None if expected_client_bounds is None else True
            ),
            "innerContainedByClient": True,
        }

    def _verify_physical_mouse_quiet(self) -> dict[str, object]:
        self._call("physical_mouse_quiet")
        if self.owned_transition_pending is not None:
            raise RuntimeError("owned Arduino mouse transition was not consumed")
        return {
            "schema": "physical_mouse_quiet.v1",
            "buttonsUp": True,
            "activityClear": True,
        }

    def _consume_owned_mouse_transition(self, button: str) -> dict[str, object]:
        self._call("consume_owned_mouse", button)
        consumed = self.owned_transition_pending == button
        self.owned_transition_pending = None
        return {
            "schema": "owned_mouse_transition.v1",
            "button": button,
            "ownedTransitionConsumed": consumed,
            "buttonsUp": True,
            "activityClear": True,
        }

    def _move_relative(self, dx: int, dy: int) -> dict[str, object]:
        self._call("move", {"dx": dx, "dy": dy})
        self._record("MOVE", fail_name="move")
        self.position = (self.position[0] + dx, self.position[1] + dy)
        return {}

    def _mouse_down(self, *, button: str) -> None:
        self._call("mouse_down")
        self.owned_transition_pending = button
        self._record("MOUSE_DOWN", fail_name="mouse_down")

    def _mouse_up(self, *, button: str) -> None:
        self._call("mouse_up")
        self._record("MOUSE_UP", fail_name="mouse_up")

    def _press(self, key: str, hold_millis: int = 50) -> None:
        self._call("press", key)
        self._record("KEY_PRESS", fail_name="press")

    def _stop_all(self) -> dict[str, object]:
        self._call("stop_all")
        self._record("STOP_ALL", fail_name="stop_all")
        self.armed = False
        return {}

    def _disarm(self) -> dict[str, object]:
        self._call("disarm")
        self._record("DISARM", fail_name="disarm")
        self.armed = False
        return {}

    def _firmware_status(self) -> dict[str, object]:
        self._call("firmware_status")
        self._record("STATUS", fail_name="firmware_status")
        return {"armed": self.armed, "keysDown": 0, "mouseButtonsDown": 0}

    def _close(self) -> None:
        self._call("close")


WINDOW = RuneLiteWindow(
    77,
    4242,
    "RuneLite - test",
    ScreenBounds(100, 100, 1000, 700),
    ScreenBounds(88, 88, 1024, 724),
)
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
    coordinator: InputCoordinator | None = None,
    backends: list[FakeBackend] | None = None,
    fail_at: str | None = None,
    cursor_start: tuple[int, int] = (150, 500),
    foreground_hwnd: int = 77,
    cursor_change_calls: list[int | None] | None = None,
    window_finder=None,
    window_handoff_callback=None,
    loaded_scene_detector=None,
    clock: FakeClock | None = None,
) -> LoginPromptHelper:
    clock = clock or FakeClock()
    collection = backends if backends is not None else []
    pending_cursor_changes = list(cursor_change_calls or ())

    def backend_factory() -> FakeBackend:
        backend = FakeBackend(
            fail_at=fail_at,
            start=cursor_start,
            foreground_hwnd=foreground_hwnd,
            change_position_on_call=(
                pending_cursor_changes.pop(0)
                if pending_cursor_changes
                else None
            ),
            window_handoff_callback=window_handoff_callback,
        )
        collection.append(backend)
        return backend

    if coordinator is None:
        coordinator = InputCoordinator(
            backend_factory,
            sleep=clock.sleep,
            pointer_timestep_seconds=0.02,
        )
    return LoginPromptHelper(
        source,
        coordinator,
        window_finder=window_finder or (lambda pid: WINDOW),
        focus_window=lambda window: True,
        point_owner=lambda window, point: window.client_bounds.contains(point),
        screenshot=lambda bounds: Image.new("RGB", (bounds.width, bounds.height), (20, 20, 20)),
        detector=detector,
        loaded_scene_detector=loaded_scene_detector or detector,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        poll_seconds=0.1,
        transition_seconds=0.3,
    )


def safely_unsent_cursor_receipt(transaction_id: str) -> InputReceipt:
    return InputReceipt(
        transaction_id=transaction_id,
        mode="pointer",
        intent_ids=(f"{transaction_id}-intent",),
        status="BLOCKED",
        reason="cursor_window_repositioned_reobserve_required",
        connected=False,
        arm_acknowledged=False,
        stop_all_acknowledged=False,
        disarm_acknowledged=False,
        firmware_status_acknowledged=False,
        firmware_status=None,
        commands=(),
        unresolved_command_count=0,
        failed_command_count=0,
        ack_missing_count=0,
        ledger_complete=True,
        ledger_closed=True,
        backend_closed=True,
        failure_kind=InputFailureKind.CURSOR_STATE_INVALIDATED,
    )


class LoginDetectionTests(unittest.TestCase):
    def test_anchor_mask_matches_original_first_anchor_and_hit_gate(self) -> None:
        width, height = 20, 15
        anchors = tuple((x, y) for y in range(3) for x in range(3))
        mask = bytearray(width * height)
        first_dark_origin = (1, 1)
        accepted_origin = (10, 8)
        for index, (anchor_x, anchor_y) in enumerate(anchors):
            if index != 0:
                x = first_dark_origin[0] + anchor_x
                y = first_dark_origin[1] + anchor_y
                mask[y * width + x] = 1
            if index != len(anchors) - 1:
                x = accepted_origin[0] + anchor_x
                y = accepted_origin[1] + anchor_y
                mask[y * width + x] = 1
        image = Image.frombytes("L", (width, height), bytes(mask))
        origin_width = width - max(x for x, _y in anchors)
        origin_height = height - max(y for _x, y in anchors)
        expected = {
            (origin_x, origin_y)
            for origin_y in range(origin_height)
            for origin_x in range(origin_width)
            if mask[(origin_y + anchors[0][1]) * width + origin_x + anchors[0][0]]
            and sum(
                bool(mask[(origin_y + anchor_y) * width + origin_x + anchor_x])
                for anchor_x, anchor_y in anchors
            )
            >= len(anchors) - 1
        }
        expected_anchor_candidate_count = sum(
            sum(
                bool(mask[(origin_y + anchor_y) * width + origin_x + anchor_x])
                for anchor_x, anchor_y in anchors
            )
            >= len(anchors) - 1
            for origin_y in range(origin_height)
            for origin_x in range(origin_width)
        )
        expected_first_candidate_count = sum(
            bool(
                mask[
                    (origin_y + anchors[0][1]) * width
                    + origin_x
                    + anchors[0][0]
                ]
            )
            for origin_y in range(origin_height)
            for origin_x in range(origin_width)
        )

        (
            candidate_origins,
            _anchor_scores,
            candidate_count,
            first_candidate_count,
        ) = (
            login_module._anchor_candidate_origins(
                image,
                anchors,
                origin_width=origin_width,
                origin_height=origin_height,
            )
        )
        actual = set(candidate_origins)

        self.assertEqual(expected, actual)
        self.assertEqual(expected_anchor_candidate_count, candidate_count)
        self.assertEqual(expected_first_candidate_count, first_candidate_count)
        self.assertEqual(
            tuple(sorted(candidate_origins, key=lambda point: (point[1], point[0]))),
            candidate_origins,
        )
        self.assertNotIn(first_dark_origin, candidate_origins)
        self.assertIn(accepted_origin, candidate_origins)

    def test_dense_anchor_candidates_fail_closed_before_scoring(self) -> None:
        width, height = 200, 150
        anchors = tuple((x, y) for y in range(3) for x in range(3))
        mask = Image.new("L", (width, height), 1)

        with self.assertRaisesRegex(
            login_module.LoginSafetyError,
            "anchor candidates exceed the bounded limit",
        ):
            login_module._anchor_candidate_origins(
                mask,
                anchors,
                origin_width=width - 2,
                origin_height=height - 2,
            )

    def test_nonbinary_first_anchor_is_not_admitted_or_counted(self) -> None:
        anchors = tuple((x, y) for y in range(3) for x in range(3))
        image = Image.frombytes("L", (3, 3), bytes((2, *([1] * 7), 0)))

        origins, _scores, candidate_count, first_candidate_count = (
            login_module._anchor_candidate_origins(
                image,
                anchors,
                origin_width=1,
                origin_height=1,
            )
        )

        self.assertEqual((), origins)
        self.assertEqual(1, candidate_count)
        self.assertEqual(0, first_candidate_count)

    def test_first_anchor_candidate_density_fails_closed_before_set_growth(self) -> None:
        width, height = 360, 120
        origin_width, origin_height = 150, 100
        anchors = (
            (0, 0),
            (200, 0),
            (200, 10),
            (200, 20),
            (200, 30),
            (200, 40),
            (200, 50),
            (200, 60),
            (200, 70),
        )
        mask = bytearray(width * height)
        for y in range(origin_height):
            for x in range(origin_width):
                mask[y * width + x] = 1
        image = Image.frombytes("L", (width, height), bytes(mask))

        with self.assertRaisesRegex(
            login_module.LoginSafetyError,
            "first-anchor candidates exceed the bounded limit",
        ):
            login_module._anchor_candidate_origins(
                image,
                anchors,
                origin_width=origin_width,
                origin_height=origin_height,
                first_candidate_limit=10_000,
            )

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

    def test_template_activation_point_preserves_post_move_label_proof(self) -> None:
        cases = (
            ("play_now", (470, 330)),
            ("click_here_to_play", (400, 500)),
        )
        for name, placement in cases:
            with Image.open(Path(TEMPLATE_DIR) / f"{name}.png") as opened:
                templates = login_module._scaled_templates(opened.convert("RGB"))
            for template in templates:
                with self.subTest(name=name, size=template.size):
                    screenshot = Image.new("RGB", (1400, 1000), (55, 18, 14))
                    screenshot.paste(template, placement)

                    before = detect_login_surfaces(screenshot)

                    self.assertEqual(1, len(before))
                    candidate = before[0]
                    self.assertEqual(name, candidate.name)
                    self.assertLess(
                        candidate.point.x,
                        candidate.match_bounds.x
                        + candidate.match_bounds.width // 4,
                    )
                    self.assertGreater(
                        candidate.point.y,
                        candidate.match_bounds.y
                        + candidate.match_bounds.height * 3 // 4,
                    )

                    occluded = screenshot.copy()
                    painter = ImageDraw.Draw(occluded)
                    painter.rectangle(
                        (
                            candidate.point.x - 2,
                            candidate.point.y - 2,
                            candidate.point.x + 93,
                            candidate.point.y + 93,
                        ),
                        fill=(250, 250, 250),
                    )
                    painter.rectangle(
                        (
                            candidate.point.x + 4,
                            candidate.point.y + 4,
                            candidate.point.x + 87,
                            candidate.point.y + 87,
                        ),
                        fill=(25, 80, 220),
                    )

                    after = detect_login_surfaces(occluded)

                    self.assertEqual(1, len(after))
                    self.assertEqual(name, after[0].name)
                    self.assertTrue(
                        LoginPromptHelper._same_candidate(candidate, after[0])
                    )

    def test_unknown_or_credential_like_surface_is_never_a_candidate(self) -> None:
        screenshot = Image.new("RGB", (1200, 800), (25, 25, 25))
        self.assertEqual(detect_login_surfaces(screenshot), ())

        # Input-box-like rectangles are intentionally not actionable evidence.
        for x in range(400, 800):
            for y in range(300, 340):
                screenshot.putpixel((x, y), (220, 220, 220))
        with self.assertRaisesRegex(
            login_module.LoginSafetyError,
            "anchor candidates exceed the bounded limit",
        ):
            detect_login_surfaces(screenshot)

    def test_full_detector_preserves_cross_template_ambiguity(self) -> None:
        screenshot = Image.new("RGB", (1200, 800), (55, 18, 14))
        placements = {
            "play_now": (470, 330),
            "click_here_to_play": (400, 500),
        }
        expected_bounds: dict[str, tuple[int, int, int, int]] = {}
        for name, placement in placements.items():
            with Image.open(Path(TEMPLATE_DIR) / f"{name}.png") as template:
                image = template.convert("RGB")
                screenshot.paste(image, placement)
                expected_bounds[name] = (*placement, image.width, image.height)

        candidates = detect_login_surfaces(screenshot)

        self.assertEqual(
            {"play_now", "click_here_to_play"},
            {candidate.name for candidate in candidates},
        )
        self.assertEqual(
            expected_bounds,
            {
                candidate.name: (
                    candidate.match_bounds.x,
                    candidate.match_bounds.y,
                    candidate.match_bounds.width,
                    candidate.match_bounds.height,
                )
                for candidate in candidates
            },
        )

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
        candidate = candidates[0]
        self.assertLess(
            candidate.point.x,
            candidate.match_bounds.x + candidate.match_bounds.width // 4,
        )
        self.assertGreater(candidate.point.y, center_y - 30)
        self.assertLess(candidate.point.y, center_y + 30)

        occluded = screenshot.copy()
        cursor = ImageDraw.Draw(occluded)
        x, y = candidate.point.x, candidate.point.y
        cursor.polygon(
            (
                (x, y),
                (x + 2, y + 48),
                (x + 13, y + 36),
                (x + 27, y + 62),
                (x + 38, y + 56),
                (x + 24, y + 31),
                (x + 42, y + 30),
            ),
            fill=(245, 245, 245),
        )
        cursor.polygon(
            (
                (x + 4, y + 7),
                (x + 5, y + 39),
                (x + 14, y + 29),
                (x + 28, y + 54),
                (x + 32, y + 52),
                (x + 18, y + 26),
                (x + 34, y + 26),
            ),
            fill=(15, 15, 15),
        )
        after = detect_login_surfaces(occluded)

        self.assertEqual(1, len(after))
        self.assertTrue(LoginPromptHelper._same_candidate(candidate, after[0]))

    def test_loaded_scene_fallback_excludes_login_screen_disconnect_heuristic(self) -> None:
        screenshot = Image.new("RGB", (1000, 700), (55, 18, 14))
        for x in range(250, 750):
            for y in range(238, 483):
                screenshot.putpixel((x, y), (60, 38, 32))
        center_x, center_y = 500, 427
        for x in range(center_x - 120, center_x + 120):
            for y in range(center_y - 45, center_y + 45):
                screenshot.putpixel((x, y), (55, 42, 35))
        for x in range(center_x - 120, center_x + 120):
            for y in (
                *range(center_y - 45, center_y - 30),
                *range(center_y + 30, center_y + 45),
            ):
                screenshot.putpixel((x, y), (15, 10, 8))
        for x in range(center_x - 12, center_x + 13, 4):
            for y in range(center_y - 10, center_y + 11):
                screenshot.putpixel((x, y), (235, 220, 185))

        self.assertEqual(
            (),
            login_module.detect_loaded_scene_login_surfaces(screenshot),
        )

    def test_flat_login_surface_is_not_a_disconnect_dialog(self) -> None:
        screenshot = Image.new("RGB", (1000, 700), (55, 38, 32))
        self.assertEqual(detect_login_surfaces(screenshot), ())

    def test_asymmetric_welcome_panel_is_not_a_disconnect_dialog(self) -> None:
        screenshot = Image.new("RGB", (1000, 700), (55, 18, 14))
        for x in range(250, 750):
            for y in range(238, 483):
                screenshot.putpixel((x, y), (60, 38, 32))
        center_x, center_y = 500, 427
        for x in range(center_x - 120, center_x + 120):
            for y in range(center_y - 45, center_y + 45):
                screenshot.putpixel((x, y), (55, 42, 35))
        for x in range(center_x - 120, center_x + 120):
            for y in range(center_y - 45, center_y - 30):
                screenshot.putpixel((x, y), (5, 5, 5))
            for y in range(center_y + 30, center_y + 45):
                screenshot.putpixel((x, y), (68, 62, 56))
        for x in range(center_x - 12, center_x + 13, 4):
            for y in range(center_y - 10, center_y + 11):
                screenshot.putpixel((x, y), (235, 220, 185))

        self.assertEqual(detect_login_surfaces(screenshot), ())

    def test_cursor_contaminated_disconnect_border_remains_detectable(self) -> None:
        screenshot = Image.new("RGB", (1000, 700), (55, 18, 14))
        for x in range(250, 750):
            for y in range(238, 483):
                screenshot.putpixel((x, y), (60, 38, 32))
        center_x, center_y = 500, 427
        for x in range(center_x - 120, center_x + 120):
            for y in range(center_y - 45, center_y + 45):
                screenshot.putpixel((x, y), (55, 42, 35))
        for x in range(center_x - 120, center_x + 120):
            for y in range(center_y - 45, center_y - 30):
                screenshot.putpixel((x, y), (15, 10, 8))
            for y in range(center_y + 30, center_y + 45):
                screenshot.putpixel((x, y), (235, 220, 185))
        for x in range(center_x + 105, center_x + 120):
            for y in range(center_y - 45, center_y + 30):
                screenshot.putpixel((x, y), (15, 10, 8))
        for x in range(center_x - 40, center_x + 41, 8):
            for y in range(center_y - 10, center_y + 11):
                screenshot.putpixel((x, y), (235, 220, 185))

        candidates = detect_login_surfaces(screenshot)

        self.assertEqual(["disconnected_ok"], [item.name for item in candidates])

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
        self.assertEqual(
            (966, 760, scaled.width, scaled.height),
            (
                candidates[0].match_bounds.x,
                candidates[0].match_bounds.y,
                candidates[0].match_bounds.width,
                candidates[0].match_bounds.height,
            ),
        )

    def test_template_matcher_scans_each_search_zone_once_across_scales(self) -> None:
        screenshot = Image.new("RGB", (1200, 800), (20, 20, 20))
        with Image.open(Path(TEMPLATE_DIR) / "click_here_to_play.png") as opened:
            template = opened.convert("RGB")
        x1, y1, x2, y2 = login_module._SEARCH_ZONES["click_here_to_play"]
        zone = (
            round(screenshot.width * x1),
            round(screenshot.height * y1),
            round(screenshot.width * x2),
            round(screenshot.height * y2),
        )
        bright_calls = 0
        original_bright = login_module._bright

        def counting_bright(pixel: tuple[int, int, int]) -> bool:
            nonlocal bright_calls
            bright_calls += 1
            return original_bright(pixel)

        original_bright_mask = login_module._bright_mask
        with (
            patch.object(login_module, "_bright", side_effect=counting_bright),
            patch.object(
                login_module,
                "_bright_mask",
                wraps=original_bright_mask,
            ) as bright_mask,
        ):
            match = login_module._best_template_match(screenshot, template, zone)

        scaled_template_work = sum(
            needle.width * needle.height
            + len(range(0, needle.height, max(1, needle.height // 20)))
            * len(range(0, needle.width, max(1, needle.width // 32)))
            for needle in login_module._scaled_templates(template)
        )
        self.assertIsNone(match)
        bright_mask.assert_called_once()
        self.assertEqual(scaled_template_work, bright_calls)

    def test_pillow_bright_mask_matches_scalar_predicate_exactly(self) -> None:
        values = (0, 114, 115, 144, 145, 164, 165, 190, 255)
        pixels = [
            (red, green, blue)
            for red in values
            for green in values
            for blue in values
        ]
        image = Image.new("RGB", (len(pixels), 1))
        image.putdata(pixels)

        mask = login_module._bright_mask(image)

        self.assertEqual("L", mask.mode)
        self.assertEqual(
            [int(login_module._bright(pixel)) for pixel in pixels],
            list(mask.get_flattened_data()),
        )

    def test_oversized_template_search_zone_fails_closed(self) -> None:
        screenshot = Image.new("RGB", (3000, 2000), (20, 20, 20))
        with Image.open(Path(TEMPLATE_DIR) / "play_now.png") as opened:
            template = opened.convert("RGB")

        with self.assertRaisesRegex(
            login_module.LoginSafetyError,
            "exceeds the bounded limit",
        ):
            login_module._best_template_match(
                screenshot,
                template,
                (0, 0, 2500, 1800),
            )


class DpiAwarenessTests(unittest.TestCase):
    def setUp(self) -> None:
        login_module._DPI_AWARENESS_SET = False

    def tearDown(self) -> None:
        login_module._DPI_AWARENESS_SET = False

    def _enable_with(self, user32: object) -> None:
        with (
            patch.object(login_module.os, "name", "nt"),
            patch.object(
                login_module.ctypes,
                "windll",
                SimpleNamespace(user32=user32),
            ),
        ):
            login_module._enable_windows_dpi_awareness()

    def test_login_and_input_backend_share_canonical_win32_structures(self) -> None:
        self.assertIs(login_module._WinPoint, login_module.wintypes.POINT)
        self.assertIs(login_module._WinRect, login_module.wintypes.RECT)

    def test_existing_per_monitor_v2_context_is_verified_without_resetting_it(self) -> None:
        user32 = FakeDpiUser32(per_monitor_v2=True)

        self._enable_with(user32)

        self.assertTrue(login_module._DPI_AWARENESS_SET)
        self.assertEqual(0, user32.setter_calls)

    def test_successful_setter_is_followed_by_active_context_verification(self) -> None:
        user32 = FakeDpiUser32(per_monitor_v2=False)

        self._enable_with(user32)

        self.assertTrue(login_module._DPI_AWARENESS_SET)
        self.assertEqual(1, user32.setter_calls)

    def test_cached_success_does_not_skip_current_thread_verification(self) -> None:
        user32 = FakeDpiUser32(per_monitor_v2=True)
        self._enable_with(user32)
        user32.per_monitor_v2 = False
        user32.setter_result = False

        with self.assertRaisesRegex(
            login_module.LoginSafetyError,
            "per-monitor-v2 DPI awareness could not be established",
        ):
            self._enable_with(user32)

        self.assertFalse(login_module._DPI_AWARENESS_SET)
        self.assertEqual(1, user32.setter_calls)

    def test_failed_or_ineffective_setter_fails_closed_and_remains_retryable(self) -> None:
        user32 = FakeDpiUser32(per_monitor_v2=False, setter_result=False)

        for expected_calls in (1, 2):
            with self.assertRaisesRegex(
                login_module.LoginSafetyError,
                "per-monitor-v2 DPI awareness could not be established",
            ):
                self._enable_with(user32)
            self.assertFalse(login_module._DPI_AWARENESS_SET)
            self.assertEqual(expected_calls, user32.setter_calls)

    def test_setter_success_without_active_per_monitor_v2_still_fails(self) -> None:
        user32 = FakeDpiUser32(
            per_monitor_v2=False,
            setter_result=True,
            per_monitor_v2_after_set=False,
        )

        with self.assertRaisesRegex(
            login_module.LoginSafetyError,
            "per-monitor-v2 DPI awareness could not be established",
        ):
            self._enable_with(user32)

        self.assertFalse(login_module._DPI_AWARENESS_SET)
        self.assertEqual(1, user32.setter_calls)

    def test_missing_context_verification_api_fails_closed(self) -> None:
        user32 = SimpleNamespace(
            SetProcessDpiAwarenessContext=FakeWinFunction(lambda _context: True)
        )

        with self.assertRaisesRegex(
            login_module.LoginSafetyError,
            "per-monitor-v2 DPI awareness APIs are unavailable",
        ):
            self._enable_with(user32)
        self.assertFalse(login_module._DPI_AWARENESS_SET)


class ClientCaptureTests(unittest.TestCase):
    def test_capture_reverifies_dpi_context_and_uses_exact_device_bounds(self) -> None:
        image = Image.new("RGBA", (1000, 700), (20, 20, 20, 255))
        with (
            patch.object(login_module, "_enable_windows_dpi_awareness") as awareness,
            patch.object(login_module.ImageGrab, "grab", return_value=image) as grab,
        ):
            captured = login_module.capture_client(WINDOW.client_bounds)

        awareness.assert_called_once_with()
        grab.assert_called_once_with(bbox=(100, 100, 1100, 800), all_screens=True)
        self.assertEqual("RGB", captured.mode)
        self.assertEqual((1000, 700), captured.size)

    def test_capture_size_mismatch_fails_before_detection(self) -> None:
        with (
            patch.object(login_module, "_enable_windows_dpi_awareness"),
            patch.object(
                login_module.ImageGrab,
                "grab",
                return_value=Image.new("RGB", (999, 700)),
            ),
        ):
            with self.assertRaisesRegex(
                login_module.LoginSafetyError,
                "screenshot dimensions do not match exact Win32 bounds",
            ):
                login_module.capture_client(WINDOW.client_bounds)


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

    def test_loaded_scene_proof_refreshes_frames_aged_during_detection(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(seconds=3)
        first_old = replace(
            observation("LOGGED_IN", 9, loaded=True),
            timestamp=old,
            assembled_at=old,
        )
        second_old = replace(
            observation("LOGGED_IN", 11, loaded=True),
            timestamp=old,
            assembled_at=old,
        )
        backends: list[FakeBackend] = []
        helper = build_helper(
            FakeObservations(
                [
                    first_old,
                    observation("LOGGED_IN", 10, loaded=True),
                    second_old,
                    observation("LOGGED_IN", 12, loaded=True),
                ]
            ),
            lambda _image: (),
            backends=backends,
        )

        result = helper.run()

        self.assertTrue(result.successful)
        self.assertEqual("loaded_scene_verified", result.reason)
        self.assertEqual([], backends)

    def test_loaded_scene_candidate_cap_uses_exact_read_only_fallback(self) -> None:
        primary_calls = 0
        fallback_calls = 0

        def capped(_image: Image.Image) -> tuple[LoginCandidate, ...]:
            nonlocal primary_calls
            primary_calls += 1
            raise login_module.LoginCandidateLimitError(
                "login template anchor candidates exceed the bounded limit"
            )

        def exhaustive(_image: Image.Image) -> tuple[LoginCandidate, ...]:
            nonlocal fallback_calls
            fallback_calls += 1
            return ()

        backends: list[FakeBackend] = []
        helper = build_helper(
            FakeObservations(
                [
                    observation("LOGGED_IN", 9, loaded=True),
                    observation("LOGGED_IN", 10, loaded=True),
                ]
            ),
            capped,
            loaded_scene_detector=exhaustive,
            backends=backends,
        )

        result = helper.run()

        self.assertTrue(result.successful)
        self.assertEqual(2, primary_calls)
        self.assertEqual(2, fallback_calls)
        self.assertEqual([], backends)

    def test_login_screen_candidate_cap_never_uses_loaded_scene_fallback(self) -> None:
        fallback_calls = 0

        def capped(_image: Image.Image) -> tuple[LoginCandidate, ...]:
            raise login_module.LoginCandidateLimitError(
                "login template anchor candidates exceed the bounded limit"
            )

        def exhaustive(_image: Image.Image) -> tuple[LoginCandidate, ...]:
            nonlocal fallback_calls
            fallback_calls += 1
            return ()

        helper = build_helper(
            FakeObservations([observation("LOGIN_SCREEN", 1)]),
            capped,
            loaded_scene_detector=exhaustive,
        )

        result = helper.run()

        self.assertFalse(result.successful)
        self.assertIn("LoginCandidateLimitError", result.reason)
        self.assertEqual(0, fallback_calls)

    def test_loaded_scene_fallback_candidate_cannot_authorize_input(self) -> None:
        def capped(_image: Image.Image) -> tuple[LoginCandidate, ...]:
            raise login_module.LoginCandidateLimitError(
                "login template anchor candidates exceed the bounded limit"
            )

        backends: list[FakeBackend] = []
        helper = build_helper(
            FakeObservations([observation("LOGGED_IN", 9, loaded=True)]),
            capped,
            loaded_scene_detector=lambda _image: (WELCOME,),
            backends=backends,
        )

        result = helper.run()

        self.assertFalse(result.successful)
        self.assertIn(
            "loaded-scene fallback found a supported prompt but cannot authorize input",
            result.reason,
        )
        self.assertEqual([], backends)

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

    def test_cursor_window_handoff_blocks_after_one_stale_geometry_retry(self) -> None:
        backends: list[FakeBackend] = []
        helper = build_helper(
            FakeObservations(
                [
                    observation("LOGIN_SCREEN", 1),
                    observation("LOGIN_SCREEN", 2),
                ]
            ),
            lambda image: (PLAY,),
            backends=backends,
            cursor_start=(1500, 500),
        )

        result = helper.run()

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("repositioned_reobserve_required", result.reason)
        self.assertEqual(2, len(backends))
        self.assertTrue(all("mouse_down" not in backend.calls for backend in backends))
        self.assertTrue(all("window_handoff" in backend.calls for backend in backends))
        self.assertTrue(all("connect" not in backend.calls for backend in backends))
        self.assertTrue(all("stop_all" not in backend.calls for backend in backends))
        self.assertEqual([1, 2], [click.source_tick for click in result.clicks])
        self.assertTrue(all(click.receipt.safely_unsent for click in result.clicks))
        self.assertTrue(all(not click.receipt.commands for click in result.clicks))

    def test_login_reobserves_shifted_window_after_safely_unsent_handoff(self) -> None:
        source = FakeObservations(
            [
                observation("LOGIN_SCREEN", 0),
                observation("LOGIN_SCREEN", 0),
                observation("LOGGING_IN", 3),
                observation("LOGGED_IN", 4, loaded=True),
                observation("LOGGED_IN", 5, loaded=True),
            ]
        )
        detections = iter(((PLAY,), (PLAY,), (PLAY,), (), ()))
        backends: list[FakeBackend] = []
        current_window = [WINDOW]

        def apply_handoff(evidence: dict[str, object]) -> None:
            old = current_window[0]
            translated = evidence["newMovementBounds"]
            assert isinstance(translated, dict)
            client = ScreenBounds(
                translated["x"],
                translated["y"],
                translated["width"],
                translated["height"],
            )
            dx = client.x - old.client_bounds.x
            dy = client.y - old.client_bounds.y
            current_window[0] = RuneLiteWindow(
                old.hwnd,
                old.pid,
                old.title,
                client,
                ScreenBounds(
                    old.outer_bounds.x + dx,
                    old.outer_bounds.y + dy,
                    old.outer_bounds.width,
                    old.outer_bounds.height,
                ),
            )

        helper = build_helper(
            source,
            lambda image: next(detections),
            backends=backends,
            cursor_start=(1500, 500),
            window_finder=lambda pid: current_window[0],
            window_handoff_callback=apply_handoff,
        )

        result = helper.run(max_clicks=1, timeout_seconds=10)

        self.assertTrue(result.successful)
        self.assertEqual(2, len(result.clicks))
        first, second = result.clicks
        self.assertTrue(first.receipt.safely_unsent)
        self.assertIs(
            first.receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertEqual((), first.receipt.commands)
        self.assertFalse(first.receipt.connected)
        self.assertTrue(first.receipt.backend_closed)
        self.assertTrue(second.receipt.successful)
        self.assertEqual([0, 0], [first.source_tick, second.source_tick])
        self.assertEqual(2, len(backends))
        self.assertIn("window_handoff", backends[0].calls)
        self.assertNotIn("connect", backends[0].calls)
        self.assertNotIn("mouse_down", backends[0].calls)
        self.assertIn("connect", backends[1].calls)
        self.assertIn("mouse_down", backends[1].calls)
        self.assertIn("stop_all", backends[1].calls)
        self.assertIn("disarm", backends[1].calls)

    def test_login_prompt_remains_safe_when_pregame_canvas_is_unavailable(self) -> None:
        source = FakeObservations(
            [
                replace(observation("LOGIN_SCREEN", 1), canvas_bounds=None),
                observation("LOGGING_IN", 2),
                observation("LOGGED_IN", 3, loaded=True),
                observation("LOGGED_IN", 4, loaded=True),
                observation("LOGGED_IN", 5, loaded=True),
            ]
        )
        detections = iter(((PLAY,), (PLAY,), (), ()))
        backends: list[FakeBackend] = []
        helper = build_helper(source, lambda image: next(detections), backends=backends)

        result = helper.run(timeout_seconds=10)

        self.assertTrue(result.successful)
        self.assertEqual(["play_now"], [click.name for click in result.clicks])
        self.assertTrue(result.clicks[0].receipt.successful)
        self.assertIn("stop_all", backends[0].calls)
        self.assertIn("disarm", backends[0].calls)

    def test_login_reacquires_cursor_from_exact_window_border(self) -> None:
        source = FakeObservations(
            [
                observation("LOGIN_SCREEN", 1),
                observation("LOGGING_IN", 2),
                observation("LOGGED_IN", 3, loaded=True),
                observation("LOGGED_IN", 4, loaded=True),
                observation("LOGGED_IN", 5, loaded=True),
            ]
        )
        detections = iter(((PLAY,), (PLAY,), (), ()))
        backends: list[FakeBackend] = []
        helper = build_helper(
            source,
            lambda image: next(detections),
            backends=backends,
            cursor_start=(1103, 500),
        )

        result = helper.run(timeout_seconds=10)

        self.assertTrue(result.successful)
        self.assertEqual(["play_now"], [click.name for click in result.clicks])
        self.assertTrue(result.clicks[0].receipt.successful)
        self.assertIn(("move", {"dx": -1, "dy": 0}), backends[0].calls)
        self.assertTrue(
            result.clicks[0].receipt.firmware_status
            and result.clicks[0].receipt.firmware_status.safe
        )

    def test_login_reobserves_once_after_safe_cursor_interference(self) -> None:
        source = FakeObservations(
            [
                observation("LOGIN_SCREEN", 1),
                observation("LOGIN_SCREEN", 2),
                observation("LOGGING_IN", 3),
                observation("LOGGED_IN", 4, loaded=True),
                observation("LOGGED_IN", 5, loaded=True),
            ]
        )
        detections = iter(((PLAY,), (PLAY,), (PLAY,), (PLAY,), (), ()))
        backends: list[FakeBackend] = []
        helper = build_helper(
            source,
            lambda image: next(detections),
            backends=backends,
            cursor_start=(600, 440),
            cursor_change_calls=[6, None],
        )

        result = helper.run(max_clicks=1, timeout_seconds=10)

        self.assertTrue(result.successful)
        self.assertEqual(2, len(result.clicks))
        self.assertFalse(result.clicks[0].sent)
        self.assertIs(
            result.clicks[0].receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertTrue(result.clicks[1].sent)
        self.assertEqual(2, len(backends))
        self.assertNotIn("mouse_down", backends[0].calls)
        self.assertIn("mouse_down", backends[1].calls)
        self.assertTrue(all("stop_all" in backend.calls for backend in backends))
        self.assertTrue(all("disarm" in backend.calls for backend in backends))

    def test_login_safely_unsent_replans_with_fresh_window_scan_at_tick_zero(self) -> None:
        source = FakeObservations(
            [
                observation("LOGIN_SCREEN", 0),
                observation("LOGIN_SCREEN", 0),
            ]
        )
        coordinator = InputCoordinator(lambda: FakeBackend())
        first = safely_unsent_cursor_receipt("input-safe-unsent-1")
        second = safely_unsent_cursor_receipt("input-safe-unsent-2")
        self.assertTrue(first.safely_unsent)
        helper = build_helper(
            source,
            lambda image: (PLAY,),
            coordinator=coordinator,
        )

        with patch.object(
            coordinator,
            "execute_pointer",
            side_effect=(first, second),
        ) as execute_pointer:
            result = helper.run(max_clicks=1, timeout_seconds=10)

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual(2, execute_pointer.call_count)
        self.assertEqual([0, 0], [click.source_tick for click in result.clicks])
        self.assertTrue(all(not click.sent for click in result.clicks))
        self.assertTrue(all(click.receipt.backend_closed for click in result.clicks))
        self.assertIn("repositioned_reobserve_required", result.reason)

    def test_login_pins_exact_hwnd_even_when_cursor_starts_inside_client(self) -> None:
        source = FakeObservations([observation("LOGIN_SCREEN", 1)])
        backends: list[FakeBackend] = []
        helper = build_helper(
            source,
            lambda image: (PLAY,),
            backends=backends,
            cursor_start=(500, 340),
            foreground_hwnd=88,
        )

        result = helper.run(timeout_seconds=10)

        self.assertFalse(result.successful)
        self.assertIn("pointer_foreground_hwnd_mismatch", result.reason)
        self.assertFalse(any(
            isinstance(call, tuple) and call[0] == "move"
            for call in backends[0].calls
        ))
        self.assertNotIn("mouse_down", backends[0].calls)
        self.assertNotIn("connect", backends[0].calls)
        self.assertNotIn("stop_all", backends[0].calls)
        self.assertNotIn("disarm", backends[0].calls)
        self.assertTrue(result.clicks[0].receipt.safely_unsent)

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

    def test_prompt_ambiguity_after_move_blocks_click_and_cleans_up(self) -> None:
        backends: list[FakeBackend] = []
        detections = iter(((PLAY,), (PLAY, WELCOME)))
        helper = build_helper(
            FakeObservations([observation("LOGIN_SCREEN", 1)]),
            lambda image: next(detections),
            backends=backends,
        )

        result = helper.run()

        self.assertEqual(result.status, "BLOCKED")
        self.assertIn("ambiguous", result.reason)
        self.assertNotIn("mouse_down", backends[0].calls)
        self.assertIn("stop_all", backends[0].calls)
        self.assertIn("disarm", backends[0].calls)

    def test_prompt_identity_or_geometry_change_after_move_blocks_click(self) -> None:
        point_shifted = replace(
            PLAY,
            point=ScreenPoint(PLAY.point.x + 5, PLAY.point.y),
        )
        bounds_shifted = replace(
            PLAY,
            match_bounds=ScreenBounds(
                PLAY.match_bounds.x + 7,
                PLAY.match_bounds.y,
                PLAY.match_bounds.width,
                PLAY.match_bounds.height,
            ),
        )
        for label, refreshed in (
            ("identity", WELCOME),
            ("point", point_shifted),
            ("bounds", bounds_shifted),
        ):
            with self.subTest(label=label):
                backends: list[FakeBackend] = []
                detections = iter(((PLAY,), (refreshed,)))
                helper = build_helper(
                    FakeObservations([observation("LOGIN_SCREEN", 1)]),
                    lambda image: next(detections),
                    backends=backends,
                )

                result = helper.run()

                self.assertEqual(result.status, "BLOCKED")
                self.assertIn("prompt changed", result.reason)
                self.assertNotIn("mouse_down", backends[0].calls)
                self.assertTrue(
                    result.clicks[-1].receipt.firmware_status
                    and result.clicks[-1].receipt.firmware_status.safe
                )

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

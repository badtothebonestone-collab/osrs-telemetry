from __future__ import annotations

import ctypes
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

import osrs_bot.arduino as arduino_module
from osrs_bot.arduino import (
    ArduinoHIDError,
    _ArduinoHIDTransport,
)


class _FakeSerial:
    def __init__(
        self,
        responses: list[bytes] | None = None,
        *,
        write_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.write_error = write_error
        self.close_error = close_error
        self.writes: list[bytes] = []

    def write(self, value: bytes) -> None:
        if self.write_error is not None:
            raise self.write_error
        self.writes.append(value)

    def flush(self) -> None:
        return None

    def readline(self) -> bytes:
        return self.responses.pop(0) if self.responses else b""

    def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error


def _backend(serial: _FakeSerial | None = None) -> _ArduinoHIDTransport:
    backend = _ArduinoHIDTransport(port="COM-test", serial_lock_enabled=False)
    backend._serial = serial
    return backend


class _FakeWinFunction:
    def __init__(self, callback) -> None:  # type: ignore[no-untyped-def]
        self.callback = callback
        self.argtypes = None
        self.restype = None
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args):  # type: ignore[no-untyped-def]
        self.calls.append(args)
        return self.callback(*args)


def _pointer_value(value: object) -> int:
    raw = getattr(value, "value", value)
    return int(raw)


class _FakeCursorUser32:
    def __init__(
        self,
        *,
        context: int = 1234,
        setter_effective: bool = True,
    ) -> None:
        self._default_context = context
        self._thread_context = threading.local()
        self.context = context
        self.setter_effective = setter_effective
        self.setter_threads: list[int] = []
        self.GetThreadDpiAwarenessContext = _FakeWinFunction(
            lambda: self.context
        )
        self.AreDpiAwarenessContextsEqual = _FakeWinFunction(
            lambda left, right: _pointer_value(left) == _pointer_value(right)
        )
        self.SetThreadDpiAwarenessContext = _FakeWinFunction(
            self._set_context
        )
        self.GetCursorPos = _FakeWinFunction(self._get_cursor_pos)
        self.WindowFromPoint = _FakeWinFunction(lambda _point: 77)
        self.GetAncestor = _FakeWinFunction(lambda child, _kind: child)
        self.GetWindowThreadProcessId = _FakeWinFunction(
            self._get_window_thread_process_id
        )

    @property
    def context(self) -> int:
        return int(getattr(self._thread_context, "value", self._default_context))

    @context.setter
    def context(self, value: int) -> None:
        self._thread_context.value = int(value)

    def _set_context(self, context: object) -> int:
        self.setter_threads.append(threading.get_ident())
        previous = self.context
        if self.setter_effective:
            self.context = _pointer_value(context)
        return previous

    def _get_cursor_pos(self, point_pointer: object) -> bool:
        point = point_pointer._obj  # type: ignore[attr-defined]
        per_monitor_v2 = (-4) & ((1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1)
        if self.context == per_monitor_v2:
            point.x, point.y = 3510, 2145
        else:
            point.x, point.y = 2006, 1226
        return True

    @staticmethod
    def _get_window_thread_process_id(
        _hwnd: object, pid_pointer: object
    ) -> int:
        pid_pointer._obj.value = 321  # type: ignore[attr-defined]
        return 1


class _FakeWindowHandoffUser32(_FakeCursorUser32):
    def __init__(
        self,
        *,
        expected_pid: int = 1968,
        expected_hwnd: int = 328854,
        actual_pid: int | None = None,
        window_bounds: tuple[int, int, int, int] = (1179, 472, 2243, 1585),
        foreground_hwnd: int | None = None,
        visible: bool = True,
        top_level: bool = True,
        iconic: bool = False,
        held_buttons: tuple[int, ...] = (),
        button_states: dict[int, int] | None = None,
        button_state_sequences: dict[int, list[int]] | None = None,
        window_rect_fail_after: int | None = None,
        client_bounds: tuple[int, int, int, int] = (
            1191,
            472,
            2219,
            1573,
        ),
        get_client_rect_succeeds: bool = True,
        client_to_screen_succeeds: bool = True,
    ) -> None:
        per_monitor_v2 = (-4) & (
            (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
        )
        super().__init__(context=per_monitor_v2)
        self.expected_pid = expected_pid
        self.expected_hwnd = expected_hwnd
        self.actual_pid = expected_pid if actual_pid is None else actual_pid
        self.window_rect = (
            window_bounds[0],
            window_bounds[1],
            window_bounds[0] + window_bounds[2],
            window_bounds[1] + window_bounds[3],
        )
        self.foreground_hwnd = (
            expected_hwnd if foreground_hwnd is None else foreground_hwnd
        )
        self.root_hwnd = expected_hwnd if top_level else expected_hwnd + 7
        self.visible = visible
        self.iconic = iconic
        self.held_buttons = set(held_buttons)
        self.button_states = dict(button_states or {})
        self.button_state_sequences = {
            key: list(values)
            for key, values in (button_state_sequences or {}).items()
        }
        self.window_rect_fail_after = window_rect_fail_after
        self.window_rect_reads = 0
        self.client_bounds = client_bounds
        self.get_client_rect_succeeds = get_client_rect_succeeds
        self.client_to_screen_succeeds = client_to_screen_succeeds

        self.GetForegroundWindow = _FakeWinFunction(
            lambda: self.foreground_hwnd
        )
        self.GetWindowThreadProcessId = _FakeWinFunction(
            self._get_handoff_window_thread_process_id
        )
        self.IsWindow = _FakeWinFunction(
            lambda hwnd: _pointer_value(hwnd) == self.expected_hwnd
        )
        self.IsWindowVisible = _FakeWinFunction(
            lambda hwnd: self.visible
            and _pointer_value(hwnd) == self.expected_hwnd
        )
        self.GetAncestor = _FakeWinFunction(self._get_ancestor)
        self.IsIconic = _FakeWinFunction(lambda _hwnd: self.iconic)
        self.GetWindowRect = _FakeWinFunction(self._get_window_rect)
        self.GetClientRect = _FakeWinFunction(self._get_client_rect)
        self.ClientToScreen = _FakeWinFunction(self._client_to_screen)
        self.GetAsyncKeyState = _FakeWinFunction(self._get_async_key_state)

    def _get_async_key_state(self, vk: object) -> int:
        key = int(vk)
        sequence = self.button_state_sequences.get(key)
        if sequence:
            return sequence.pop(0)
        return self.button_states.get(
            key, 0x8000 if key in self.held_buttons else 0
        )

    def _get_handoff_window_thread_process_id(
        self, hwnd: object, pid_pointer: object
    ) -> int:
        value = _pointer_value(hwnd)
        pid_pointer._obj.value = (  # type: ignore[attr-defined]
            self.actual_pid
            if value == self.expected_hwnd
            else self.expected_pid + 1
        )
        return 1

    def _get_ancestor(self, hwnd: object, _kind: object) -> int:
        value = _pointer_value(hwnd)
        return self.root_hwnd if value == self.expected_hwnd else value

    def _get_window_rect(self, _hwnd: object, rect_pointer: object) -> bool:
        self.window_rect_reads += 1
        if (
            self.window_rect_fail_after is not None
            and self.window_rect_reads > self.window_rect_fail_after
        ):
            return False
        rect = rect_pointer._obj  # type: ignore[attr-defined]
        rect.left, rect.top, rect.right, rect.bottom = self.window_rect
        return True

    def _get_client_rect(self, _hwnd: object, rect_pointer: object) -> bool:
        if not self.get_client_rect_succeeds:
            return False
        rect = rect_pointer._obj  # type: ignore[attr-defined]
        _x, _y, width, height = self.client_bounds
        rect.left = 0
        rect.top = 0
        rect.right = width
        rect.bottom = height
        return True

    def _client_to_screen(self, _hwnd: object, point_pointer: object) -> bool:
        if not self.client_to_screen_succeeds:
            return False
        point = point_pointer._obj  # type: ignore[attr-defined]
        x, y, _width, _height = self.client_bounds
        point.x += x
        point.y += y
        return True

class CursorDpiAwarenessTests(unittest.TestCase):
    def test_cursor_sample_establishes_thread_device_pixel_context_first(self) -> None:
        user32 = _FakeCursorUser32()

        with patch.object(arduino_module.os, "name", "nt"):
            position = arduino_module._cursor_position(user32)

        self.assertEqual((3510, 2145), position)
        self.assertEqual(1, len(user32.SetThreadDpiAwarenessContext.calls))
        self.assertGreaterEqual(
            len(user32.GetThreadDpiAwarenessContext.calls), 2
        )

    def test_each_cursor_sample_reverifies_current_thread_context(self) -> None:
        user32 = _FakeCursorUser32()

        with patch.object(arduino_module.os, "name", "nt"):
            self.assertEqual((3510, 2145), arduino_module._cursor_position(user32))
            user32.context = 1234
            self.assertEqual((3510, 2145), arduino_module._cursor_position(user32))

        self.assertEqual(2, len(user32.SetThreadDpiAwarenessContext.calls))

    def test_existing_per_monitor_v2_context_skips_thread_setter(self) -> None:
        per_monitor_v2 = (-4) & (
            (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
        )
        user32 = _FakeCursorUser32(context=per_monitor_v2)

        with patch.object(arduino_module.os, "name", "nt"):
            self.assertEqual((3510, 2145), arduino_module._cursor_position(user32))

        self.assertEqual([], user32.SetThreadDpiAwarenessContext.calls)

    def test_fresh_worker_thread_establishes_its_own_device_pixel_context(self) -> None:
        user32 = _FakeCursorUser32()
        positions: list[tuple[int, int]] = []

        with patch.object(arduino_module.os, "name", "nt"):
            positions.append(arduino_module._cursor_position(user32))
            worker = threading.Thread(
                target=lambda: positions.append(
                    arduino_module._cursor_position(user32)
                )
            )
            worker.start()
            worker.join(timeout=2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual([(3510, 2145), (3510, 2145)], positions)
        self.assertEqual(2, len(set(user32.setter_threads)))

    def test_point_ownership_reverifies_the_same_device_pixel_context(self) -> None:
        user32 = _FakeCursorUser32()

        with patch.object(arduino_module.os, "name", "nt"):
            point = arduino_module._cursor_position(user32)
            user32.context = 1234
            owner = arduino_module._window_info_at_point(point, user32)

        self.assertEqual((3510, 2145), point)
        self.assertEqual({"available": True, "hwnd": 77, "pid": 321}, owner)
        self.assertEqual(2, len(user32.SetThreadDpiAwarenessContext.calls))
        self.assertGreaterEqual(
            len(user32.GetThreadDpiAwarenessContext.calls), 3
        )

    def test_ineffective_thread_context_change_fails_before_cursor_read(self) -> None:
        user32 = _FakeCursorUser32(setter_effective=False)

        with patch.object(arduino_module.os, "name", "nt"):
            with self.assertRaisesRegex(
                ArduinoHIDError,
                "per-monitor-v2 cursor DPI awareness could not be established",
            ):
                arduino_module._cursor_position(user32)

        self.assertEqual([], user32.GetCursorPos.calls)

    def test_get_cursor_pos_failure_never_falls_back_to_origin(self) -> None:
        per_monitor_v2 = (-4) & (
            (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
        )
        user32 = _FakeCursorUser32(context=per_monitor_v2)
        user32.GetCursorPos = _FakeWinFunction(lambda _point: False)

        with patch.object(arduino_module.os, "name", "nt"):
            with self.assertRaisesRegex(
                ArduinoHIDError,
                "Windows GetCursorPos failed",
            ):
                arduino_module._cursor_position(user32)

        self.assertEqual(1, len(user32.GetCursorPos.calls))


class PhysicalMouseQuietTests(unittest.TestCase):
    @staticmethod
    def _verify(user32: _FakeWindowHandoffUser32) -> dict[str, object]:
        with patch.object(arduino_module.os, "name", "nt"):
            return arduino_module._verify_physical_mouse_quiet(user32)

    def test_all_five_buttons_quiet_returns_positive_evidence(self) -> None:
        user32 = _FakeWindowHandoffUser32()

        with patch.object(arduino_module.time, "sleep") as dwell:
            result = self._verify(user32)

        self.assertEqual(
            {
                "schema": "physical_mouse_quiet.v1",
                "buttonsUp": True,
                "activityClear": True,
                "historicalActivityConsumed": False,
                "sampleCount": 3,
            },
            result,
        )
        self.assertEqual(
            [(0x01,), (0x02,), (0x04,), (0x05,), (0x06,)] * 3,
            user32.GetAsyncKeyState.calls,
        )
        self.assertEqual(2, dwell.call_count)
        self.assertEqual([], user32.GetCursorPos.calls)

    def test_initial_historical_low_is_consumed_before_clear_dwell(self) -> None:
        user32 = _FakeWindowHandoffUser32(
            button_state_sequences={0x05: [0x0001, 0x0000, 0x0000]}
        )

        with patch.object(arduino_module.time, "sleep") as dwell:
            result = self._verify(user32)

        self.assertIs(True, result["historicalActivityConsumed"])
        self.assertIs(True, result["buttonsUp"])
        self.assertIs(True, result["activityClear"])
        self.assertEqual(3, result["sampleCount"])
        self.assertEqual(2, dwell.call_count)

    def test_held_button_rejects_quiet_evidence(self) -> None:
        user32 = _FakeWindowHandoffUser32(held_buttons=(0x02,))

        with self.assertRaisesRegex(ArduinoHIDError, "held during quiet baseline"):
            self._verify(user32)

    def test_new_low_bit_during_dwell_is_rejected(self) -> None:
        user32 = _FakeWindowHandoffUser32(
            button_state_sequences={0x05: [0x0000, 0x0001]}
        )

        with patch.object(arduino_module.time, "sleep"):
            with self.assertRaisesRegex(ArduinoHIDError, "during quiet dwell"):
                self._verify(user32)

    def test_new_high_bit_during_later_dwell_sample_is_rejected(self) -> None:
        user32 = _FakeWindowHandoffUser32(
            button_state_sequences={0x02: [0x0000, 0x0000, 0x8000]}
        )

        with patch.object(arduino_module.time, "sleep"):
            with self.assertRaisesRegex(ArduinoHIDError, "during quiet dwell"):
                self._verify(user32)

    def test_transport_quiet_method_delegates_without_serial_input(self) -> None:
        serial = _FakeSerial()
        backend = _backend(serial)
        expected = {"schema": "physical_mouse_quiet.v1"}
        with patch.object(
            arduino_module,
            "_verify_physical_mouse_quiet",
            return_value=expected,
        ) as helper:
            result = backend._verify_physical_mouse_quiet()

        self.assertIs(expected, result)
        helper.assert_called_once_with()
        self.assertEqual([], serial.writes)


class PhysicalMouseReleasedSampleTests(unittest.TestCase):
    @staticmethod
    def _verify(user32: _FakeWindowHandoffUser32) -> dict[str, object]:
        with patch.object(arduino_module.os, "name", "nt"):
            return arduino_module._verify_physical_mouse_buttons_released(
                user32
            )

    def test_single_clear_sample_returns_positive_evidence_without_waiting(
        self,
    ) -> None:
        user32 = _FakeWindowHandoffUser32()

        with patch.object(arduino_module.time, "sleep") as sleeper:
            result = self._verify(user32)

        self.assertEqual(
            {
                "schema": "physical_mouse_buttons_released.v1",
                "buttonsUp": True,
                "activityClear": True,
            },
            result,
        )
        self.assertEqual(
            [(0x01,), (0x02,), (0x04,), (0x05,), (0x06,)],
            user32.GetAsyncKeyState.calls,
        )
        sleeper.assert_not_called()

    def test_single_sample_rejects_held_or_pressed_button_activity(self) -> None:
        cases = (
            _FakeWindowHandoffUser32(held_buttons=(0x01,)),
            _FakeWindowHandoffUser32(button_states={0x06: 0x0001}),
        )
        for user32 in cases:
            with self.subTest(states=user32.button_states):
                with self.assertRaisesRegex(
                    ArduinoHIDError,
                    "held or pressed during cursor reacquisition",
                ):
                    self._verify(user32)

    def test_transport_release_sample_delegates_without_serial_input(
        self,
    ) -> None:
        serial = _FakeSerial()
        backend = _backend(serial)
        expected = {"schema": "physical_mouse_buttons_released.v1"}
        with patch.object(
            arduino_module,
            "_verify_physical_mouse_buttons_released",
            return_value=expected,
        ) as helper:
            result = backend._verify_physical_mouse_buttons_released()

        self.assertIs(expected, result)
        helper.assert_called_once_with()
        self.assertEqual([], serial.writes)


class VirtualDesktopGeometryTests(unittest.TestCase):
    @staticmethod
    def _user32(metrics: dict[int, int]) -> _FakeCursorUser32:
        user32 = _FakeCursorUser32()
        user32.GetSystemMetrics = _FakeWinFunction(
            lambda index: metrics[int(index)]
        )
        return user32

    @staticmethod
    def _sample(user32: _FakeCursorUser32) -> dict[str, object]:
        with patch.object(arduino_module.os, "name", "nt"):
            return arduino_module._virtual_desktop_bounds(user32)

    def test_negative_origin_preserves_every_valid_virtual_desktop_corner(
        self,
    ) -> None:
        user32 = self._user32(
            {76: -1920, 77: -1080, 78: 5760, 79: 3240}
        )

        result = self._sample(user32)

        self.assertEqual("virtual_desktop_geometry.v1", result["schema"])
        self.assertEqual("device_pixels_pm_v2", result["coordinateSpace"])
        self.assertEqual(
            {
                "x": -1920,
                "y": -1080,
                "width": 5760,
                "height": 3240,
                "right": 3840,
                "bottom": 2160,
            },
            result["bounds"],
        )
        bounds = result["bounds"]
        assert isinstance(bounds, dict)
        corners = (
            (-1920, -1080),
            (3839, -1080),
            (-1920, 2159),
            (3839, 2159),
        )
        for x, y in corners:
            with self.subTest(corner=(x, y)):
                self.assertLessEqual(bounds["x"], x)
                self.assertLess(x, bounds["right"])
                self.assertLessEqual(bounds["y"], y)
                self.assertLess(y, bounds["bottom"])
        self.assertEqual(
            [(76,), (77,), (78,), (79,)],
            user32.GetSystemMetrics.calls,
        )

    def test_zero_virtual_desktop_dimensions_fail_closed(self) -> None:
        for metrics in (
            {76: -1920, 77: 0, 78: 0, 79: 1080},
            {76: 0, 77: -1080, 78: 1920, 79: 0},
        ):
            with self.subTest(metrics=metrics):
                with self.assertRaisesRegex(
                    ArduinoHIDError,
                    "virtual desktop has invalid physical dimensions",
                ):
                    self._sample(self._user32(metrics))

    def test_missing_virtual_desktop_api_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            ArduinoHIDError,
            "Windows GetSystemMetrics is unavailable",
        ):
            self._sample(_FakeCursorUser32())

    def test_transport_virtual_desktop_delegates_without_serial_input(
        self,
    ) -> None:
        serial = _FakeSerial()
        backend = _backend(serial)
        expected = {"schema": "virtual_desktop_geometry.v1"}
        with patch.object(
            arduino_module,
            "_virtual_desktop_bounds",
            return_value=expected,
        ) as helper:
            result = backend._virtual_desktop_bounds()

        self.assertIs(expected, result)
        helper.assert_called_once_with()
        self.assertEqual([], serial.writes)


class OwnedMouseTransitionTests(unittest.TestCase):
    @staticmethod
    def _consume(
        user32: _FakeWindowHandoffUser32,
        button: str = "left",
    ) -> dict[str, object]:
        with patch.object(arduino_module.os, "name", "nt"):
            return arduino_module._consume_owned_mouse_transition(
                button=button,
                user32=user32,
            )

    def test_owned_low_bit_is_consumed_then_all_buttons_are_clear(self) -> None:
        user32 = _FakeWindowHandoffUser32(
            button_state_sequences={0x01: [0x0001, 0x0000]}
        )

        with patch.object(arduino_module.time, "sleep") as settle:
            result = self._consume(user32)

        self.assertEqual(
            {
                "schema": "owned_mouse_transition.v1",
                "button": "left",
                "ownedTransitionConsumed": True,
                "buttonsUp": True,
                "activityClear": True,
            },
            result,
        )
        self.assertEqual(
            [
                (arduino_module._OWNED_MOUSE_TRANSITION_SETTLE_SECONDS,),
                (arduino_module._OWNED_MOUSE_TRANSITION_SETTLE_SECONDS,),
            ],
            [record.args for record in settle.call_args_list],
        )
        self.assertEqual(15, len(user32.GetAsyncKeyState.calls))

    def test_already_consumed_owned_low_bit_is_valid(self) -> None:
        user32 = _FakeWindowHandoffUser32()

        with patch.object(arduino_module.time, "sleep"):
            result = self._consume(user32, "right")

        self.assertEqual("right", result["button"])
        self.assertIs(False, result["ownedTransitionConsumed"])
        self.assertIs(True, result["activityClear"])

    def test_transient_owned_high_then_low_then_two_clear_samples_passes(self) -> None:
        user32 = _FakeWindowHandoffUser32(
            button_state_sequences={
                0x01: [0x8000, 0x0001, 0x0000, 0x0000]
            }
        )

        with patch.object(arduino_module.time, "sleep") as settle:
            result = self._consume(user32)

        self.assertIs(True, result["ownedTransitionConsumed"])
        self.assertIs(True, result["buttonsUp"])
        self.assertIs(True, result["activityClear"])
        self.assertEqual(3, settle.call_count)

    def test_transient_owned_high_without_visible_low_bit_can_settle(self) -> None:
        user32 = _FakeWindowHandoffUser32(
            button_state_sequences={0x01: [0x8000, 0x0000, 0x0000]}
        )

        with patch.object(arduino_module.time, "sleep") as settle:
            result = self._consume(user32)

        self.assertIs(False, result["ownedTransitionConsumed"])
        self.assertIs(True, result["buttonsUp"])
        self.assertIs(True, result["activityClear"])
        self.assertEqual(2, settle.call_count)

    def test_owned_release_may_settle_after_old_hundred_millisecond_boundary(
        self,
    ) -> None:
        self.assertEqual(
            0.50,
            arduino_module._OWNED_MOUSE_TRANSITION_TIMEOUT_SECONDS,
        )
        user32 = _FakeWindowHandoffUser32(
            button_state_sequences={
                0x01: [0x8000] * 30 + [0x0000, 0x0000]
            }
        )
        now = [0.0]

        def monotonic() -> float:
            return now[0]

        def settle(seconds: float) -> None:
            now[0] += seconds

        with (
            patch.object(arduino_module.time, "monotonic", monotonic),
            patch.object(arduino_module.time, "sleep", settle),
        ):
            result = self._consume(user32)

        self.assertIs(True, result["buttonsUp"])
        self.assertIs(True, result["activityClear"])
        self.assertGreater(now[0], 0.10)
        self.assertLessEqual(
            now[0],
            arduino_module._OWNED_MOUSE_TRANSITION_TIMEOUT_SECONDS,
        )

    def test_delayed_owned_high_resets_clear_streak(self) -> None:
        user32 = _FakeWindowHandoffUser32(
            button_state_sequences={
                0x01: [0x0000, 0x8000, 0x0000, 0x0000]
            }
        )

        with patch.object(arduino_module.time, "sleep") as settle:
            result = self._consume(user32)

        self.assertIs(False, result["ownedTransitionConsumed"])
        self.assertIs(True, result["activityClear"])
        self.assertEqual(3, settle.call_count)

    def test_late_owned_high_resets_one_clear_sample_in_extended_window(
        self,
    ) -> None:
        user32 = _FakeWindowHandoffUser32(
            button_state_sequences={
                0x01: [0x8000] * 25
                + [0x0000, 0x8000, 0x0000, 0x0000]
            }
        )
        now = [0.0]

        def monotonic() -> float:
            return now[0]

        def settle(seconds: float) -> None:
            now[0] += seconds

        with (
            patch.object(arduino_module.time, "monotonic", monotonic),
            patch.object(arduino_module.time, "sleep", settle),
        ):
            result = self._consume(user32)

        self.assertIs(True, result["buttonsUp"])
        self.assertIs(True, result["activityClear"])
        self.assertGreater(now[0], 0.25)
        self.assertLess(
            now[0],
            arduino_module._OWNED_MOUSE_TRANSITION_TIMEOUT_SECONDS,
        )

    def test_persistent_owned_high_bit_times_out(self) -> None:
        user32 = _FakeWindowHandoffUser32(button_states={0x01: 0x8000})
        now = [0.0]
        sleeps: list[float] = []

        def monotonic() -> float:
            return now[0]

        def settle(seconds: float) -> None:
            sleeps.append(seconds)
            now[0] += seconds

        with (
            patch.object(arduino_module.time, "monotonic", monotonic),
            patch.object(arduino_module.time, "sleep", settle),
            self.assertRaisesRegex(ArduinoHIDError, "did not settle before deadline"),
        ):
            self._consume(user32)

        self.assertGreaterEqual(len(sleeps), 49)
        self.assertLessEqual(len(sleeps), 51)
        self.assertTrue(all(
            0.0 < delay <= arduino_module._OWNED_MOUSE_TRANSITION_SETTLE_SECONDS
            for delay in sleeps
        ))
        self.assertAlmostEqual(
            arduino_module._OWNED_MOUSE_TRANSITION_TIMEOUT_SECONDS,
            now[0],
            places=6,
        )

    def test_other_button_low_and_high_bits_are_rejected(self) -> None:
        cases = (
            (
                _FakeWindowHandoffUser32(
                    button_state_sequences={0x02: [0x0001]}
                ),
                "unowned physical mouse activity",
            ),
            (
                _FakeWindowHandoffUser32(
                    button_state_sequences={0x04: [0x8000]}
                ),
                "unowned physical mouse activity",
            ),
        )
        for user32, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ArduinoHIDError, reason):
                    self._consume(user32)

    def test_delayed_owned_transition_is_consumed_before_final_clear(self) -> None:
        user32 = _FakeWindowHandoffUser32(
            button_state_sequences={0x01: [0x0000, 0x0001, 0x0000]}
        )

        with patch.object(arduino_module.time, "sleep") as settle:
            result = self._consume(user32)

        self.assertIs(True, result["ownedTransitionConsumed"])
        self.assertEqual(3, settle.call_count)

    def test_delayed_other_button_transition_is_rejected(self) -> None:
        user32 = _FakeWindowHandoffUser32(
            button_state_sequences={0x02: [0x0000, 0x0001]}
        )

        with patch.object(arduino_module.time, "sleep") as settle:
            with self.assertRaisesRegex(
                ArduinoHIDError, "unowned physical mouse activity"
            ):
                self._consume(user32)

        settle.assert_called_once()

    def test_late_other_button_transition_rejects_before_extended_deadline(
        self,
    ) -> None:
        user32 = _FakeWindowHandoffUser32(
            button_state_sequences={
                0x01: [0x8000] * 40,
                0x02: [0x0000] * 25 + [0x0001],
            }
        )
        now = [0.0]

        def monotonic() -> float:
            return now[0]

        def settle(seconds: float) -> None:
            now[0] += seconds

        with (
            patch.object(arduino_module.time, "monotonic", monotonic),
            patch.object(arduino_module.time, "sleep", settle),
            self.assertRaisesRegex(
                ArduinoHIDError,
                "unowned physical mouse activity",
            ),
        ):
            self._consume(user32)

        self.assertGreater(now[0], 0.10)
        self.assertLess(
            now[0],
            arduino_module._OWNED_MOUSE_TRANSITION_TIMEOUT_SECONDS,
        )

    def test_transport_owned_transition_delegates_without_serial_input(self) -> None:
        serial = _FakeSerial()
        backend = _backend(serial)
        expected = {"schema": "owned_mouse_transition.v1"}
        with patch.object(
            arduino_module,
            "_consume_owned_mouse_transition",
            return_value=expected,
        ) as helper:
            result = backend._consume_owned_mouse_transition("left")

        self.assertIs(expected, result)
        helper.assert_called_once_with(button="left")
        self.assertEqual([], serial.writes)


class WindowGeometryTests(unittest.TestCase):
    LIVE_OUTER = (1179, 472, 2243, 1585)
    LIVE_CLIENT = (1191, 472, 2219, 1573)
    LIVE_CANVAS = (1199, 520, 2151, 1519)

    @staticmethod
    def _verify(
        user32: _FakeWindowHandoffUser32,
        *,
        expected_outer: tuple[int, int, int, int] | None,
        expected_client: tuple[int, int, int, int] | None,
        required_inner: tuple[int, int, int, int],
    ) -> dict[str, object]:
        with patch.object(arduino_module.os, "name", "nt"):
            return arduino_module._verify_window_geometry(
                expected_pid=user32.expected_pid,
                expected_hwnd=user32.expected_hwnd,
                expected_outer_bounds=expected_outer,
                expected_client_bounds=expected_client,
                required_inner_bounds=required_inner,
                user32=user32,
            )

    def test_exact_live_outer_client_canvas_geometry(self) -> None:
        user32 = _FakeWindowHandoffUser32(
            window_bounds=self.LIVE_OUTER,
            client_bounds=self.LIVE_CLIENT,
        )

        result = self._verify(
            user32,
            expected_outer=self.LIVE_OUTER,
            expected_client=self.LIVE_CLIENT,
            required_inner=self.LIVE_CANVAS,
        )

        self.assertEqual("cursor_window_geometry.v1", result["schema"])
        self.assertEqual(1968, result["expectedPid"])
        self.assertEqual(328854, result["expectedHwnd"])
        self.assertEqual(
            {
                "x": 1179,
                "y": 472,
                "width": 2243,
                "height": 1585,
                "right": 3422,
                "bottom": 2057,
            },
            result["actualOuterBounds"],
        )
        self.assertEqual(
            {
                "x": 1191,
                "y": 472,
                "width": 2219,
                "height": 1573,
                "right": 3410,
                "bottom": 2045,
            },
            result["actualClientBounds"],
        )
        self.assertEqual(
            {
                "x": 1199,
                "y": 520,
                "width": 2151,
                "height": 1519,
                "right": 3350,
                "bottom": 2039,
            },
            result["requiredInnerBounds"],
        )
        self.assertIs(True, result["outerMatches"])
        self.assertIs(True, result["clientMatches"])
        self.assertIs(True, result["innerContainedByClient"])
        self.assertEqual([], user32.GetCursorPos.calls)
        self.assertEqual([], user32.GetAsyncKeyState.calls)

    def test_outer_only_and_client_only_expectations_are_nullable(self) -> None:
        user32 = _FakeWindowHandoffUser32()
        outer_only = self._verify(
            user32,
            expected_outer=self.LIVE_OUTER,
            expected_client=None,
            required_inner=self.LIVE_CANVAS,
        )
        self.assertIs(True, outer_only["outerMatches"])
        self.assertIsNone(outer_only["expectedClientBounds"])
        self.assertIsNone(outer_only["clientMatches"])

        user32 = _FakeWindowHandoffUser32()
        client_only = self._verify(
            user32,
            expected_outer=None,
            expected_client=self.LIVE_CLIENT,
            required_inner=(2300, 1281, 10, 10),
        )
        self.assertIsNone(client_only["expectedOuterBounds"])
        self.assertIsNone(client_only["outerMatches"])
        self.assertIs(True, client_only["clientMatches"])
        self.assertIs(True, client_only["innerContainedByClient"])

    def test_negative_outer_client_and_inner_coordinates_are_preserved(self) -> None:
        outer = (-1820, -80, 1304, 760)
        client = (-1812, -64, 1280, 720)
        inner = (-1800, -50, 100, 100)
        user32 = _FakeWindowHandoffUser32(
            window_bounds=outer,
            client_bounds=client,
        )

        result = self._verify(
            user32,
            expected_outer=outer,
            expected_client=client,
            required_inner=inner,
        )

        self.assertEqual(-1820, result["actualOuterBounds"]["x"])
        self.assertEqual(-1812, result["actualClientBounds"]["x"])
        self.assertEqual(-1800, result["requiredInnerBounds"]["x"])
        self.assertIs(True, result["outerMatches"])
        self.assertIs(True, result["clientMatches"])
        self.assertIs(True, result["innerContainedByClient"])

    def test_mismatches_and_uncontained_inner_are_reported_without_mutation(self) -> None:
        user32 = _FakeWindowHandoffUser32()

        result = self._verify(
            user32,
            expected_outer=(1180, 472, 2243, 1585),
            expected_client=(1192, 472, 2219, 1573),
            required_inner=(1179, 472, 10, 10),
        )

        self.assertIs(False, result["outerMatches"])
        self.assertIs(False, result["clientMatches"])
        self.assertIs(False, result["innerContainedByClient"])

    def test_foreground_and_pid_mismatches_fail_closed(self) -> None:
        cases = (
            (
                _FakeWindowHandoffUser32(foreground_hwnd=99001),
                "foreground root HWND changed",
            ),
            (
                _FakeWindowHandoffUser32(actual_pid=1969),
                "belongs to a different PID",
            ),
        )
        for user32, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ArduinoHIDError, reason):
                    self._verify(
                        user32,
                        expected_outer=self.LIVE_OUTER,
                        expected_client=self.LIVE_CLIENT,
                        required_inner=self.LIVE_CANVAS,
                    )

    def test_window_geometry_api_failures_are_explicit(self) -> None:
        cases = (
            (
                _FakeWindowHandoffUser32(window_rect_fail_after=0),
                "GetWindowRect failed",
            ),
            (
                _FakeWindowHandoffUser32(
                    get_client_rect_succeeds=False
                ),
                "GetClientRect failed",
            ),
            (
                _FakeWindowHandoffUser32(
                    client_to_screen_succeeds=False
                ),
                "ClientToScreen failed",
            ),
        )
        for user32, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ArduinoHIDError, reason):
                    self._verify(
                        user32,
                        expected_outer=self.LIVE_OUTER,
                        expected_client=self.LIVE_CLIENT,
                        required_inner=self.LIVE_CANVAS,
                    )

    def test_window_geometry_arguments_are_strict(self) -> None:
        user32 = _FakeWindowHandoffUser32()
        cases = (
            ({"expected_pid": True}, "positive PID and HWND"),
            ({"expected_outer_bounds": [1179, 472, 2243, 1585]}, "exactly 4 integers"),
            ({"expected_client_bounds": (1191, 472, 0, 1573)}, "dimensions must be positive"),
            ({"required_inner_bounds": None}, "required inner bounds are required"),
            ({"required_inner_bounds": (1199, 520, 0, 1519)}, "dimensions must be positive"),
        )
        for overrides, reason in cases:
            arguments = {
                "expected_pid": user32.expected_pid,
                "expected_hwnd": user32.expected_hwnd,
                "expected_outer_bounds": self.LIVE_OUTER,
                "expected_client_bounds": self.LIVE_CLIENT,
                "required_inner_bounds": self.LIVE_CANVAS,
                "user32": user32,
            }
            arguments.update(overrides)
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ArduinoHIDError, reason):
                    arduino_module._verify_window_geometry(**arguments)

    def test_transport_window_geometry_method_delegates_without_input(self) -> None:
        serial = _FakeSerial()
        backend = _backend(serial)
        expected = {"schema": "cursor_window_geometry.v1"}
        with patch.object(
            arduino_module,
            "_verify_window_geometry",
            return_value=expected,
        ) as helper:
            result = backend._verify_window_geometry(
                expected_pid=1968,
                expected_hwnd=328854,
                expected_outer_bounds=self.LIVE_OUTER,
                expected_client_bounds=self.LIVE_CLIENT,
                required_inner_bounds=self.LIVE_CANVAS,
            )

        self.assertIs(expected, result)
        helper.assert_called_once_with(
            expected_pid=1968,
            expected_hwnd=328854,
            expected_outer_bounds=self.LIVE_OUTER,
            expected_client_bounds=self.LIVE_CLIENT,
            required_inner_bounds=self.LIVE_CANVAS,
        )
        self.assertEqual([], serial.writes)


class InputLeaseTests(unittest.TestCase):
    def test_lease_blocks_second_owner_before_serial_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = _ArduinoHIDTransport(
                port="COM-input-lease-test",
                serial_lock_enabled=True,
                serial_lock_dir=temporary,
                serial_lock_timeout_ms=0,
                serial_owner="first-owner",
            )
            second = _ArduinoHIDTransport(
                port="COM-input-lease-test",
                serial_lock_enabled=True,
                serial_lock_dir=temporary,
                serial_lock_timeout_ms=0,
                serial_owner="second-owner",
            )
            try:
                first._acquire_input_lease()

                self.assertIsNone(first._serial)
                self.assertIsNotNone(first._serial_lock)
                with self.assertRaisesRegex(
                    ArduinoHIDError,
                    "already owned",
                ):
                    second._acquire_input_lease()
                self.assertIsNone(second._serial)
                self.assertIsNone(second._serial_lock)

                held_lease = first._serial_lock
                first.serial_factory = lambda *args, **kwargs: _FakeSerial()
                first._connect()
                self.assertIs(held_lease, first._serial_lock)
                self.assertIsNotNone(first._serial)

                first._close()
                second._acquire_input_lease()
                self.assertIsNone(second._serial)
                self.assertIsNotNone(second._serial_lock)
            finally:
                first._close()
                second._close()


class ArduinoCommandLedgerTests(unittest.TestCase):
    def test_success_has_one_stable_redacted_terminal_record(self) -> None:
        serial = _FakeSerial([b"PONG\n"])
        backend = _backend(serial)
        backend._begin_command_ledger()

        self.assertEqual("PONG", backend._send("PING", expected_token="PONG"))
        evidence = backend._end_command_ledger()

        self.assertEqual(0, evidence["unresolvedCount"])
        self.assertEqual(0, evidence["failedCount"])
        self.assertEqual(0, evidence["ackMissingCount"])
        self.assertEqual(1, len(evidence["records"]))
        record = evidence["records"][0]
        self.assertEqual("cmd-00000001", record["commandId"])
        self.assertEqual(1, record["sequence"])
        self.assertEqual("PING", record["command"])
        self.assertEqual("PASS", record["status"])
        self.assertTrue(record["ackReceived"])
        self.assertTrue(record["accepted"])
        self.assertEqual(
            {"responseToken": "PONG", "payloadToken": "PONG"},
            record["firmwareAck"],
        )

    def test_transaction_ledger_does_not_truncate_long_command_sequences(self) -> None:
        backend = _backend(_FakeSerial([b"PONG\n"] * 40))
        backend._begin_command_ledger()

        for _ in range(40):
            backend._send("PING", expected_token="PONG")
        evidence = backend._end_command_ledger()

        self.assertEqual(40, len(evidence["records"]))
        self.assertEqual("cmd-00000001", evidence["records"][0]["commandId"])
        self.assertEqual("cmd-00000040", evidence["records"][-1]["commandId"])
        self.assertEqual(0, evidence["unresolvedCount"])
        self.assertLessEqual(len(backend._status.command_trace), 32)

    def test_arm_token_is_never_exposed_in_command_evidence_or_errors(self) -> None:
        secret = "do-not-publish-this-session-token"
        backend = _backend(_FakeSerial([b"OK ARM\n"]))
        backend._begin_command_ledger()

        backend._send(f"ARM {secret}", expected_token="ARM")
        evidence = backend._end_command_ledger()

        encoded = json.dumps(evidence, sort_keys=True)
        self.assertNotIn(secret, encoded)
        self.assertEqual("ARM", evidence["records"][0]["command"])

    def test_timeout_and_best_effort_cleanup_retains_terminal_evidence(self) -> None:
        backend = _backend(_FakeSerial([b"", b"OK STOP_ALL\n"]))
        backend._begin_command_ledger()

        with self.assertRaisesRegex(ArduinoHIDError, "timed out"):
            backend._send("PING", expected_token="PONG")
        evidence = backend._end_command_ledger()

        self.assertEqual("ACK_TIMEOUT_OR_READ_FAIL", evidence["records"][0]["status"])
        self.assertFalse(evidence["records"][0]["ackReceived"])
        self.assertEqual("STOP_ALL", evidence["records"][1]["command"])
        self.assertEqual("PASS", evidence["records"][1]["status"])
        self.assertEqual(
            {"responseToken": "OK", "payloadToken": "STOP_ALL"},
            evidence["records"][1]["firmwareAck"],
        )
        self.assertEqual(0, evidence["unresolvedCount"])
        self.assertEqual(1, evidence["failedCount"])
        self.assertGreaterEqual(evidence["ackMissingCount"], 1)

    def test_symbolic_key_uses_firmware_wire_spelling(self) -> None:
        serial = _FakeSerial([b"OK KEY_PRESS\n"])
        backend = _backend(serial)
        backend._status.armed = True
        backend._begin_command_ledger()

        backend._press("RIGHT", 10)
        evidence = backend._end_command_ledger()

        self.assertEqual([b"KEY_PRESS right 10\n"], serial.writes)
        self.assertEqual("PASS", evidence["records"][0]["status"])

    def test_rejected_key_retains_acknowledged_emergency_cleanup_evidence(self) -> None:
        serial = _FakeSerial([b"ERR BAD_ARGS KEY_PRESS\n", b"OK STOP_ALL\n"])
        backend = _backend(serial)
        backend._status.armed = True
        backend._begin_command_ledger()

        with self.assertRaisesRegex(ArduinoHIDError, "rejected KEY_PRESS"):
            backend._press("RIGHT")
        evidence = backend._end_command_ledger()

        self.assertEqual(["REJECTED", "PASS"], [r["status"] for r in evidence["records"]])
        self.assertEqual(["KEY_PRESS", "STOP_ALL"], [r["command"] for r in evidence["records"]])
        self.assertTrue(all(r["ackReceived"] for r in evidence["records"]))
        self.assertEqual(0, evidence["unresolvedCount"])

    def test_rejection_and_write_failure_are_terminal_failure_records(self) -> None:
        rejected = _backend(_FakeSerial([b"ERR refused\n"]))
        rejected._begin_command_ledger()
        with self.assertRaisesRegex(ArduinoHIDError, "rejected STOP_ALL"):
            rejected._send("STOP_ALL", expected_token="STOP_ALL")
        rejection_evidence = rejected._end_command_ledger()
        self.assertEqual("REJECTED", rejection_evidence["records"][0]["status"])
        self.assertTrue(rejection_evidence["records"][0]["ackReceived"])
        self.assertFalse(rejection_evidence["records"][0]["accepted"])
        self.assertEqual(0, rejection_evidence["unresolvedCount"])

        failed = _backend(_FakeSerial(write_error=TimeoutError("write timed out")))
        failed._begin_command_ledger()
        with self.assertRaisesRegex(ArduinoHIDError, "write failed"):
            failed._send("PING", expected_token="PONG")
        failure_evidence = failed._end_command_ledger()
        self.assertEqual("WRITE_FAIL", failure_evidence["records"][0]["status"])
        self.assertFalse(failure_evidence["records"][0]["ackReceived"])
        self.assertEqual(0, failure_evidence["unresolvedCount"])

    def test_not_armed_rejection_never_retries_state_changing_command(self) -> None:
        serial = _FakeSerial([b"ERR NOT_ARMED\n", b"OK STOP_ALL\n"])
        backend = _backend(serial)
        backend._status.armed = True
        backend._begin_command_ledger()

        with self.assertRaisesRegex(ArduinoHIDError, "rejected MOVE"):
            backend._move_relative(1, 0)
        evidence = backend._end_command_ledger()

        self.assertEqual(1, serial.writes.count(b"MOVE 1 0\n"))
        self.assertEqual(1, serial.writes.count(b"STOP_ALL\n"))
        self.assertFalse(any(write.startswith(b"ARM ") for write in serial.writes))
        self.assertEqual("REJECTED", evidence["records"][0]["status"])
        self.assertEqual("MOVE", evidence["records"][0]["command"])
        self.assertFalse(backend._status.armed)

    def test_send_never_implicitly_opens_a_serial_session(self) -> None:
        backend = _backend(None)

        with self.assertRaisesRegex(ArduinoHIDError, "connect explicitly"):
            backend._send("PING", expected_token="PONG")

    def test_transport_close_failure_is_explicit_after_state_is_cleared(self) -> None:
        backend = _backend(_FakeSerial(close_error=OSError("port close failed")))
        backend._status.connected = True
        backend._status.armed = True

        with self.assertRaisesRegex(ArduinoHIDError, "transport close failed"):
            backend._close()

        self.assertIsNone(backend._serial)
        self.assertFalse(backend._status.connected)
        self.assertFalse(backend._status.armed)
        self.assertIn("serial_close_failed", backend._status.last_error or "")

    def test_relative_transport_rejects_zero_and_out_of_policy_steps(self) -> None:
        backend = _backend(_FakeSerial())
        backend._status.armed = True

        with self.assertRaisesRegex(ArduinoHIDError, "change at least one axis"):
            backend._move_relative(0, 0)
        with self.assertRaisesRegex(ArduinoHIDError, "20px axis limit"):
            backend._move_relative(21, 0)

        self.assertEqual([], backend._serial.writes)

    def test_nested_ledgers_are_rejected_and_end_returns_a_snapshot(self) -> None:
        backend = _backend(_FakeSerial())
        backend._begin_command_ledger()
        with self.assertRaisesRegex(ArduinoHIDError, "already active"):
            backend._begin_command_ledger()

        evidence = backend._end_command_ledger()
        self.assertEqual([], evidence["records"])
        self.assertEqual(evidence, backend._command_evidence())


if __name__ == "__main__":
    unittest.main()

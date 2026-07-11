from __future__ import annotations

import ctypes
import json
import threading
import unittest
from unittest.mock import patch

import osrs_bot.arduino as arduino_module
from osrs_bot.arduino import ArduinoHIDError, _ArduinoHIDTransport


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

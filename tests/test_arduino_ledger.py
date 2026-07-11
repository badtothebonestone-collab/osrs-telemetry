from __future__ import annotations

import json
import unittest

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

    def test_timeout_and_best_effort_cleanup_remain_explicitly_unresolved(self) -> None:
        backend = _backend(_FakeSerial([b"", b"OK STOP_ALL\n"]))
        backend._begin_command_ledger()

        with self.assertRaisesRegex(ArduinoHIDError, "timed out"):
            backend._send("PING", expected_token="PONG")
        evidence = backend._end_command_ledger()

        self.assertEqual("ACK_TIMEOUT_OR_READ_FAIL", evidence["records"][0]["status"])
        self.assertFalse(evidence["records"][0]["ackReceived"])
        self.assertEqual("STOP_ALL", evidence["records"][1]["command"])
        self.assertEqual("ACK_READ", evidence["records"][1]["status"])
        self.assertEqual(1, evidence["unresolvedCount"])
        self.assertGreaterEqual(evidence["ackMissingCount"], 1)

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

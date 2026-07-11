from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError, replace
from typing import Any

from osrs_bot.input_coordinator import (
    ApprovedKeyIntent,
    ApprovedPointerIntent,
    CommandEvidence,
    FirmwareSafetyStatus,
    InputCoordinator,
    InputPurpose,
    InputReceipt,
    InputValidation,
    MouseButton,
    PointerActivationDecision,
)
from osrs_bot.model import ScreenBounds, ScreenPoint


_TERMINAL = {
    "PASS",
    "WRITE_FAIL",
    "ACK_TIMEOUT_OR_READ_FAIL",
    "REJECTED",
    "REJECTED_RETRYABLE",
    "UNEXPECTED_RESPONSE",
}


class FakeBackend:
    def __init__(
        self,
        *,
        start: tuple[int, int] = (10, 10),
        fail_commands: set[str] | None = None,
        missing_ack_commands: set[str] | None = None,
        pending_commands: set[str] | None = None,
        unsafe_status: dict[str, Any] | None = None,
        connect_fails: bool = False,
        close_fails: bool = False,
        end_ledger_fails: bool = False,
        end_ledger_truncates: bool = False,
        snapshot_drops_prefix_at: int | None = None,
        cursor_diverges: bool = False,
        divergent_move_count: int = 0,
    ) -> None:
        self.position = start
        self.fail_commands = set(fail_commands or ())
        self.missing_ack_commands = set(missing_ack_commands or ())
        self.pending_commands = set(pending_commands or ())
        self.final_status = {
            "armed": False,
            "keysDown": 0,
            "mouseButtonsDown": 0,
            **(unsafe_status or {}),
        }
        self.connect_fails = connect_fails
        self.close_fails = close_fails
        self.end_ledger_fails = end_ledger_fails
        self.end_ledger_truncates = end_ledger_truncates
        self.snapshot_drops_prefix_at = snapshot_drops_prefix_at
        self.snapshot_prefix_dropped = False
        self.cursor_diverges = cursor_diverges
        self.divergent_move_count = divergent_move_count
        self.events: list[str] = []
        self.records: list[dict[str, Any]] = []
        self.sequence = 0
        self.ledger_active = False

    def _begin_command_ledger(self) -> None:
        self.events.append("ledger_begin")
        self.records = []
        self.ledger_active = True

    def _command_evidence(self) -> dict[str, Any]:
        self.events.append("ledger_snapshot")
        records = self.records
        if (
            self.snapshot_drops_prefix_at == len(self.records)
            and not self.snapshot_prefix_dropped
            and self.records
        ):
            records = self.records[-1:]
            self.snapshot_prefix_dropped = True
        unresolved = sum(record["status"] not in _TERMINAL for record in records)
        failed = sum(record["status"] != "PASS" for record in records)
        missing = sum(not record["ackReceived"] for record in records)
        return {
            "schema": "arduino_command_ledger.v1",
            "records": [dict(record) for record in records],
            "unresolvedCount": unresolved,
            "failedCount": failed,
            "ackMissingCount": missing,
        }

    def _end_command_ledger(self) -> dict[str, Any]:
        self.events.append("ledger_end")
        if self.end_ledger_fails:
            raise RuntimeError("ledger close failed")
        evidence = self._command_evidence()
        if self.end_ledger_truncates:
            evidence = {
                "schema": "arduino_command_ledger.v1",
                "records": [],
                "unresolvedCount": 0,
                "failedCount": 0,
                "ackMissingCount": 0,
            }
        self.ledger_active = False
        return evidence

    def _connect(self) -> None:
        self.events.append("connect")
        if self.connect_fails:
            raise RuntimeError("connect failed")

    def _arm(self) -> dict[str, Any]:
        self.events.append("arm")
        self._record("ARM")
        return {}

    def _current_position(self) -> tuple[int, int]:
        self.events.append(f"position:{self.position[0]},{self.position[1]}")
        return self.position

    def _move_relative(self, dx: int, dy: int) -> dict[str, Any]:
        self.events.append(f"move:{dx},{dy}")
        self._record("MOVE")
        extra = 1 if self.cursor_diverges or self.divergent_move_count > 0 else 0
        if self.divergent_move_count > 0:
            self.divergent_move_count -= 1
        self.position = (self.position[0] + dx + extra, self.position[1] + dy)
        return {"firmwareAck": "OK MOVE"}

    def _assert_foreground(
        self,
        allowed_titles: tuple[str, ...] | list[str],
        *,
        expected_pid: int | None = None,
    ) -> dict[str, Any]:
        self.events.append(f"foreground:{expected_pid}")
        if tuple(allowed_titles) != ("RuneLite",) or expected_pid != 321:
            raise RuntimeError("foreground mismatch")
        return {"pid": expected_pid}

    def _mouse_down(self, *, button: str = "left") -> None:
        self.events.append(f"mouse_down:{button}")
        self._record("MOUSE_DOWN")

    def _mouse_up(self, *, button: str = "left") -> None:
        self.events.append(f"mouse_up:{button}")
        self._record("MOUSE_UP")

    def _press(self, key: str) -> None:
        self.events.append(f"press:{key}")
        self._record("KEY_PRESS")

    def _stop_all(self) -> dict[str, Any]:
        self.events.append("stop_all")
        self._record("STOP_ALL")
        return {}

    def _disarm(self) -> dict[str, Any]:
        self.events.append("disarm")
        self._record("DISARM")
        return {}

    def _firmware_status(self) -> dict[str, Any]:
        self.events.append("firmware_status")
        self._record("STATUS")
        return dict(self.final_status)

    def _close(self) -> None:
        self.events.append("close")
        if self.close_fails:
            raise RuntimeError("close failed")

    def _record(self, command: str) -> None:
        self.sequence += 1
        if command in self.pending_commands:
            status = "PENDING"
            write_ok = True
            ack_received = False
            accepted = False
            error = None
        elif command in self.missing_ack_commands:
            status = "ACK_TIMEOUT_OR_READ_FAIL"
            write_ok = True
            ack_received = False
            accepted = False
            error = "ack missing"
        elif command in self.fail_commands:
            status = "REJECTED"
            write_ok = True
            ack_received = True
            accepted = False
            error = (
                "rejected ARM super-secret-session-token"
                if command == "ARM"
                else "firmware rejected command"
            )
        else:
            status = "PASS"
            write_ok = True
            ack_received = True
            accepted = True
            error = None
        record = {
            "schema": "arduino_command_evidence.v1",
            "commandId": f"cmd-{self.sequence:08d}",
            "sequence": self.sequence,
            "command": command,
            "status": status,
            "writeOk": write_ok,
            "ackReceived": ack_received,
            "accepted": accepted,
            "firmwareAck": (
                {"responseToken": "OK", "payloadToken": command}
                if ack_received
                else None
            ),
            "error": error,
            "timeoutClassification": (
                "serial_timeout_waiting_for_ack"
                if command in self.missing_ack_commands
                else None
            ),
            "retryCount": 0,
        }
        self.records.append(record)
        if command in self.fail_commands:
            raise RuntimeError(error)


BOUNDS = ScreenBounds(0, 0, 100, 100)


def pointer_intent(
    *,
    intent_id: str = "object-1",
    purpose: InputPurpose = InputPurpose.GAMEPLAY_OBJECT,
    target: ScreenPoint = ScreenPoint(12, 10),
    button: MouseButton = MouseButton.LEFT,
) -> ApprovedPointerIntent:
    return ApprovedPointerIntent(
        intent_id=intent_id,
        purpose=purpose,
        target=target,
        movement_bounds=BOUNDS,
        target_bounds=BOUNDS,
        expected_pid=321,
        button=button,
    )


def coordinator(backend: FakeBackend) -> InputCoordinator:
    return InputCoordinator(
        lambda: backend,
        sleep=lambda _seconds: None,
        pointer_timestep_seconds=0.02,
    )


class InputCoordinatorTests(unittest.TestCase):
    def test_pointer_success_has_ordered_wire_evidence_and_safe_cleanup(self) -> None:
        backend = FakeBackend()
        validated_at: list[tuple[int, int]] = []

        def validate(_intent: ApprovedPointerIntent) -> InputValidation:
            backend.events.append("validator")
            validated_at.append(backend.position)
            return InputValidation.allow("fresh hover and target identity match")

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(), validate=validate
        )

        self.assertTrue(receipt.successful)
        self.assertEqual(validated_at, [(12, 10)])
        self.assertEqual(
            [command.command for command in receipt.commands],
            [
                "ARM",
                "MOVE",
                "MOUSE_DOWN",
                "MOUSE_UP",
                "STOP_ALL",
                "DISARM",
                "STATUS",
            ],
        )
        self.assertLess(
            backend.events.index("validator"),
            backend.events.index("mouse_down:left"),
        )
        self.assertEqual(
            [event for event in backend.events if event in {"stop_all", "disarm", "firmware_status", "ledger_end", "close"}],
            ["stop_all", "disarm", "firmware_status", "ledger_end", "close"],
        )
        encoded = json.dumps(receipt.to_dict(), sort_keys=True)
        self.assertIn('"successful": true', encoded)
        self.assertFalse(hasattr(receipt, "__dict__"))
        self.assertFalse(hasattr(receipt.commands[0], "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            receipt.status = "ERROR"  # type: ignore[misc]

    def test_key_validator_runs_after_arm_and_immediately_before_key(self) -> None:
        backend = FakeBackend()
        intent = ApprovedKeyIntent(
            "escape-dialogue",
            InputPurpose.GAMEPLAY_KEY,
            "esc",
            321,
        )

        def validate(_intent: ApprovedKeyIntent) -> InputValidation:
            backend.events.append("key_validator")
            return InputValidation.allow("fresh dialogue still present")

        receipt = coordinator(backend).execute_key(intent, validate=validate)

        self.assertTrue(receipt.successful)
        self.assertLess(backend.events.index("arm"), backend.events.index("key_validator"))
        self.assertLess(backend.events.index("key_validator"), backend.events.index("press:ESC"))
        self.assertEqual(
            [command.command for command in receipt.commands],
            ["ARM", "KEY_PRESS", "STOP_ALL", "DISARM", "STATUS"],
        )

    def test_denied_fresh_validator_blocks_activation_but_still_proves_cleanup(self) -> None:
        backend = FakeBackend()
        receipt = coordinator(backend).execute_pointer(
            pointer_intent(),
            validate=lambda _intent: InputValidation.deny("hover changed"),
        )

        self.assertEqual(receipt.status, "BLOCKED")
        self.assertFalse(receipt.successful)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_context_menu_is_one_transaction_with_two_fresh_validators(self) -> None:
        backend = FakeBackend()
        open_intent = pointer_intent(
            intent_id="context-open",
            purpose=InputPurpose.CONTEXT_MENU,
            target=ScreenPoint(12, 10),
            button=MouseButton.RIGHT,
        )

        def resolve_row() -> ApprovedPointerIntent:
            backend.events.append("resolve_row")
            return pointer_intent(
                intent_id="context-row",
                purpose=InputPurpose.CONTEXT_ROW,
                target=ScreenPoint(12, 12),
            )

        receipt = coordinator(backend).execute_context_menu(
            open_intent,
            validate_hover=lambda _intent: self._validation_event(
                backend, "hover_validator"
            ),
            resolve_row=resolve_row,
            validate_row=lambda _intent: self._validation_event(
                backend, "row_validator"
            ),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual(receipt.intent_ids, ("context-open", "context-row"))
        self.assertEqual(backend.events.count("connect"), 1)
        self.assertEqual(backend.events.count("arm"), 1)
        self.assertFalse(receipt.context_cancel_attempted)
        self.assertLess(backend.events.index("hover_validator"), backend.events.index("mouse_down:right"))
        self.assertLess(backend.events.index("mouse_up:right"), backend.events.index("resolve_row"))
        self.assertLess(backend.events.index("row_validator"), backend.events.index("mouse_down:left"))

    def test_context_failure_after_right_click_records_esc_cancellation(self) -> None:
        backend = FakeBackend()
        open_intent = pointer_intent(
            intent_id="context-open",
            purpose=InputPurpose.CONTEXT_MENU,
            button=MouseButton.RIGHT,
        )

        def fail_resolver() -> ApprovedPointerIntent:
            raise RuntimeError("menu sample unavailable")

        receipt = coordinator(backend).execute_context_menu(
            open_intent,
            validate_hover=lambda _intent: InputValidation.allow(),
            resolve_row=fail_resolver,
            validate_row=lambda _intent: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertEqual(receipt.status, "BLOCKED")
        self.assertIn("menu sample unavailable", receipt.reason)
        self.assertTrue(receipt.context_cancel_attempted)
        self.assertTrue(receipt.context_cancel_acknowledged)
        commands = [command.command for command in receipt.commands]
        self.assertLess(commands.index("KEY_PRESS"), commands.index("STOP_ALL"))
        self.assertLess(backend.events.index("press:ESC"), backend.events.index("stop_all"))

    def test_context_cancel_ack_failure_is_explicit(self) -> None:
        backend = FakeBackend(missing_ack_commands={"KEY_PRESS"})
        open_intent = pointer_intent(
            intent_id="context-open",
            purpose=InputPurpose.CONTEXT_MENU,
            button=MouseButton.RIGHT,
        )
        receipt = coordinator(backend).execute_context_menu(
            open_intent,
            validate_hover=lambda _intent: InputValidation.allow(),
            resolve_row=lambda: (_ for _ in ()).throw(RuntimeError("no row")),
            validate_row=lambda _intent: InputValidation.allow(),
        )

        self.assertEqual(receipt.status, "ERROR")
        self.assertTrue(receipt.context_cancel_attempted)
        self.assertFalse(receipt.context_cancel_acknowledged)
        self.assertGreater(receipt.ack_missing_count, 0)

    def test_connect_exception_still_attempts_all_cleanup_operations(self) -> None:
        backend = FakeBackend(connect_fails=True)
        receipt = coordinator(backend).execute_key(
            ApprovedKeyIntent("key", InputPurpose.GAMEPLAY_KEY, "ESC", 321),
            validate=lambda _intent: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertFalse(receipt.connected)
        self.assertEqual(
            [event for event in backend.events if event in {"stop_all", "disarm", "firmware_status", "close"}],
            ["stop_all", "disarm", "firmware_status", "close"],
        )

    def test_cursor_divergence_aborts_before_click_and_cleans_up(self) -> None:
        backend = FakeBackend(cursor_diverges=True)
        receipt = coordinator(backend).execute_pointer(
            pointer_intent(), validate=lambda _intent: InputValidation.allow()
        )

        self.assertFalse(receipt.successful)
        self.assertIn("cursor_feedback", receipt.reason)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.stop_all_acknowledged)

    def test_feedback_divergence_is_corrected_by_a_bounded_replan(self) -> None:
        backend = FakeBackend(divergent_move_count=1)
        receipt = coordinator(backend).execute_pointer(
            pointer_intent(), validate=lambda _intent: InputValidation.allow()
        )

        self.assertTrue(receipt.successful)
        self.assertEqual(backend.position, (12, 10))
        self.assertEqual(
            [command.command for command in receipt.commands].count("MOVE"), 2
        )

    def test_login_pointer_refuses_a_start_outside_verified_transit_bounds(self) -> None:
        backend = FakeBackend(start=(150, 10))
        login_intent = pointer_intent(
            purpose=InputPurpose.LOGIN_PROMPT,
            target=ScreenPoint(12, 10),
        )
        receipt = coordinator(backend).execute_pointer(
            login_intent, validate=lambda _intent: InputValidation.allow()
        )

        self.assertEqual(receipt.status, "BLOCKED")
        self.assertIn("cursor_start_outside", receipt.reason)
        self.assertFalse(any(event.startswith("move:") for event in backend.events))

    def test_adaptive_pointer_chooses_direct_left_from_fresh_evidence(self) -> None:
        backend = FakeBackend()

        def decide(_intent: ApprovedPointerIntent) -> PointerActivationDecision:
            backend.events.append("activation_decision")
            return PointerActivationDecision.direct("fresh default option matches")

        receipt = coordinator(backend).execute_adaptive_pointer(
            pointer_intent(),
            decide_activation=decide,
            resolve_row=lambda: (_ for _ in ()).throw(
                AssertionError("context resolver must not run")
            ),
            validate_row=lambda _intent: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertIn("mouse_down:left", backend.events)
        self.assertNotIn("mouse_down:right", backend.events)
        self.assertLess(
            backend.events.index("activation_decision"),
            backend.events.index("mouse_down:left"),
        )

    def test_adaptive_pointer_context_branch_stays_in_one_transaction(self) -> None:
        backend = FakeBackend()
        receipt = coordinator(backend).execute_adaptive_pointer(
            pointer_intent(),
            decide_activation=lambda _intent: PointerActivationDecision.context(
                "fresh menu requires exact lower row"
            ),
            resolve_row=lambda: pointer_intent(
                intent_id="fresh-row",
                purpose=InputPurpose.CONTEXT_ROW,
                target=ScreenPoint(12, 12),
            ),
            validate_row=lambda _intent: InputValidation.allow(
                "fresh exact row retained"
            ),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual(receipt.intent_ids, ("object-1", "fresh-row"))
        self.assertEqual(backend.events.count("connect"), 1)
        self.assertEqual(backend.events.count("arm"), 1)
        self.assertIn("mouse_down:right", backend.events)
        self.assertIn("mouse_down:left", backend.events)

    def test_each_wire_ack_failure_is_unsuccessful_and_cleanup_continues(self) -> None:
        for command in (
            "ARM",
            "MOVE",
            "MOUSE_DOWN",
            "MOUSE_UP",
            "STOP_ALL",
            "DISARM",
            "STATUS",
        ):
            with self.subTest(command=command):
                backend = FakeBackend(fail_commands={command})
                receipt = coordinator(backend).execute_pointer(
                    pointer_intent(),
                    validate=lambda _intent: InputValidation.allow(),
                )
                self.assertFalse(receipt.successful)
                self.assertEqual(receipt.status, "ERROR")
                self.assertGreater(receipt.failed_command_count, 0)
                self.assertIn("stop_all", backend.events)
                self.assertIn("disarm", backend.events)
                self.assertIn("firmware_status", backend.events)
                self.assertEqual(backend.events[-1], "close")

    def test_missing_ack_and_unresolved_host_command_are_both_fail_closed(self) -> None:
        cases = (
            (FakeBackend(missing_ack_commands={"MOVE"}), "missing"),
            (FakeBackend(pending_commands={"MOVE"}), "pending"),
        )
        for backend, label in cases:
            with self.subTest(label=label):
                receipt = coordinator(backend).execute_pointer(
                    pointer_intent(),
                    validate=lambda _intent: InputValidation.allow(),
                )
                self.assertFalse(receipt.successful)
                self.assertGreater(receipt.ack_missing_count, 0)
                if label == "pending":
                    self.assertGreater(receipt.unresolved_command_count, 0)

    def test_missing_ack_on_each_cleanup_or_status_command_is_fail_closed(self) -> None:
        for command in ("STOP_ALL", "DISARM", "STATUS"):
            with self.subTest(command=command):
                backend = FakeBackend(missing_ack_commands={command})
                receipt = coordinator(backend).execute_key(
                    ApprovedKeyIntent(
                        "key", InputPurpose.GAMEPLAY_KEY, "ESC", 321
                    ),
                    validate=lambda _intent: InputValidation.allow(),
                )
                self.assertFalse(receipt.successful)
                self.assertEqual(receipt.status, "ERROR")
                self.assertGreater(receipt.ack_missing_count, 0)
                self.assertIn("stop_all", backend.events)
                self.assertIn("disarm", backend.events)
                self.assertIn("firmware_status", backend.events)

    def test_each_unsafe_or_malformed_final_status_is_unsuccessful(self) -> None:
        statuses = (
            {"armed": True},
            {"keysDown": 1},
            {"mouseButtonsDown": 1},
            {"armed": "false"},
            {"keysDown": "0"},
            {"mouseButtonsDown": None},
        )
        for status in statuses:
            with self.subTest(status=status):
                backend = FakeBackend(unsafe_status=status)
                receipt = coordinator(backend).execute_key(
                    ApprovedKeyIntent(
                        "key", InputPurpose.GAMEPLAY_KEY, "ESC", 321
                    ),
                    validate=lambda _intent: InputValidation.allow(),
                )
                self.assertFalse(receipt.successful)
                self.assertEqual(receipt.status, "ERROR")

    def test_ledger_close_and_backend_close_failures_are_unsuccessful(self) -> None:
        for backend in (
            FakeBackend(end_ledger_fails=True),
            FakeBackend(end_ledger_truncates=True),
            FakeBackend(snapshot_drops_prefix_at=2),
            FakeBackend(close_fails=True),
        ):
            with self.subTest(backend=backend):
                receipt = coordinator(backend).execute_key(
                    ApprovedKeyIntent(
                        "key", InputPurpose.GAMEPLAY_KEY, "ESC", 321
                    ),
                    validate=lambda _intent: InputValidation.allow(),
                )
                self.assertFalse(receipt.successful)
                self.assertEqual(receipt.status, "ERROR")
                self.assertNotEqual("input_transaction_succeeded", receipt.reason)

    def test_receipt_redacts_arm_token_from_wire_error(self) -> None:
        backend = FakeBackend(fail_commands={"ARM"})
        receipt = coordinator(backend).execute_key(
            ApprovedKeyIntent("key", InputPurpose.GAMEPLAY_KEY, "ESC", 321),
            validate=lambda _intent: InputValidation.allow(),
        )

        encoded = json.dumps(receipt.to_dict(), sort_keys=True)
        self.assertNotIn("super-secret-session-token", encoded)
        self.assertIn("<redacted>", encoded)

    def test_intent_validation_rejects_unbounded_or_wrong_lane_values(self) -> None:
        with self.assertRaises(ValueError):
            ApprovedPointerIntent(
                "bad",
                InputPurpose.GAMEPLAY_OBJECT,
                ScreenPoint(150, 10),
                BOUNDS,
                BOUNDS,
                321,
            )
        with self.assertRaises(ValueError):
            ApprovedKeyIntent("bad", InputPurpose.GAMEPLAY_KEY, "secret text", 321)
        with self.assertRaises(ValueError):
            ApprovedPointerIntent(
                "bad",
                InputPurpose.GAMEPLAY_KEY,
                ScreenPoint(10, 10),
                BOUNDS,
                BOUNDS,
                321,
            )

    def test_numeric_configuration_rejects_nan_and_infinity(self) -> None:
        backend = FakeBackend()
        for invalid in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(pointer_timestep=invalid):
                with self.assertRaises(ValueError):
                    InputCoordinator(
                        lambda: backend,
                        pointer_timestep_seconds=invalid,
                    )
            with self.subTest(click_hold=invalid):
                with self.assertRaises(ValueError):
                    InputCoordinator(
                        lambda: backend,
                        click_hold_seconds=invalid,
                    )

    def test_public_receipt_evidence_and_status_types_are_deeply_immutable(self) -> None:
        backend = FakeBackend()
        receipt = coordinator(backend).execute_key(
            ApprovedKeyIntent("key", InputPurpose.GAMEPLAY_KEY, "ESC", 321),
            validate=lambda _intent: InputValidation.allow(),
        )

        self.assertIsInstance(receipt, InputReceipt)
        self.assertIsInstance(receipt.commands[0], CommandEvidence)
        self.assertIsInstance(receipt.firmware_status, FirmwareSafetyStatus)
        self.assertFalse(hasattr(receipt, "__dict__"))
        self.assertFalse(hasattr(receipt.commands[0], "__dict__"))
        self.assertFalse(hasattr(receipt.firmware_status, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            receipt.commands[0].status = "FAIL"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            receipt.firmware_status.armed = True  # type: ignore[union-attr,misc]
        with self.assertRaises(ValueError):
            CommandEvidence(
                "cmd-00000001",
                1,
                "MOVE",
                "PASS",
                True,
                True,
                True,
            )
        with self.assertRaises((TypeError, ValueError)):
            FirmwareSafetyStatus(False, True, 0)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            replace(receipt, failed_command_count=1)

        fabricated = replace(receipt, commands=())
        self.assertFalse(fabricated.wire_proof_complete)
        self.assertFalse(fabricated.successful)
        self.assertTrue(receipt.wire_proof_complete)

    @staticmethod
    def _validation_event(
        backend: FakeBackend, event: str
    ) -> InputValidation:
        backend.events.append(event)
        return InputValidation.allow()


if __name__ == "__main__":
    unittest.main()

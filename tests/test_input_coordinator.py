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
from osrs_bot.pointer import plan_pointer_motion


_TERMINAL = {
    "PASS",
    "WRITE_FAIL",
    "ACK_TIMEOUT_OR_READ_FAIL",
    "REJECTED",
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
        device_pixel_scale: float = 1.0,
        no_effect_x_move_count: int = 0,
        no_effect_y_move_count: int = 0,
        no_effect_x_move_indices: set[int] | None = None,
        no_effect_y_move_indices: set[int] | None = None,
    ) -> None:
        self.position = start
        self.fail_commands = set(fail_commands or ())
        self.missing_ack_commands = set(missing_ack_commands or ())
        self.pending_commands = set(pending_commands or ())
        self.armed = False
        self.status_overrides = dict(unsafe_status or {})
        self.connect_fails = connect_fails
        self.close_fails = close_fails
        self.end_ledger_fails = end_ledger_fails
        self.end_ledger_truncates = end_ledger_truncates
        self.snapshot_drops_prefix_at = snapshot_drops_prefix_at
        self.snapshot_prefix_dropped = False
        self.cursor_diverges = cursor_diverges
        self.divergent_move_count = divergent_move_count
        self.device_pixel_scale = device_pixel_scale
        self.no_effect_x_move_count = no_effect_x_move_count
        self.no_effect_y_move_count = no_effect_y_move_count
        self.no_effect_x_move_indices = set(no_effect_x_move_indices or ())
        self.no_effect_y_move_indices = set(no_effect_y_move_indices or ())
        self.move_call_count = 0
        self.positions: list[tuple[int, int]] = [start]
        self.key_presses: list[tuple[str, int]] = []
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
        self.armed = True
        return {}

    def _current_position(self) -> tuple[int, int]:
        self.events.append(f"position:{self.position[0]},{self.position[1]}")
        return self.position

    def _move_relative(self, dx: int, dy: int) -> dict[str, Any]:
        self.events.append(f"move:{dx},{dy}")
        self._record("MOVE")
        self.move_call_count += 1
        if dx and (
            self.no_effect_x_move_count > 0
            or self.move_call_count in self.no_effect_x_move_indices
        ):
            dx = 0
            self.no_effect_x_move_count = max(0, self.no_effect_x_move_count - 1)
        if dy and (
            self.no_effect_y_move_count > 0
            or self.move_call_count in self.no_effect_y_move_indices
        ):
            dy = 0
            self.no_effect_y_move_count = max(0, self.no_effect_y_move_count - 1)
        if self.device_pixel_scale != 1.0:
            dx = self._scaled_delta(dx)
            dy = self._scaled_delta(dy)
        extra_x = 1 if self.divergent_move_count > 0 else 0
        extra_y = 1 if self.cursor_diverges else 0
        if self.divergent_move_count > 0:
            self.divergent_move_count -= 1
        self.position = (
            self.position[0] + dx + extra_x,
            self.position[1] + dy + extra_y,
        )
        self.positions.append(self.position)
        return {"firmwareAck": "OK MOVE"}

    def _scaled_delta(self, delta: int) -> int:
        magnitude = round(abs(delta) * self.device_pixel_scale)
        return magnitude if delta > 0 else -magnitude

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

    def _press(self, key: str, hold_millis: int = 50) -> None:
        self.key_presses.append((key, hold_millis))
        self.events.append(f"press:{key}")
        self._record("KEY_PRESS")

    def _stop_all(self) -> dict[str, Any]:
        self.events.append("stop_all")
        self._record("STOP_ALL")
        self.armed = False
        return {}

    def _disarm(self) -> dict[str, Any]:
        self.events.append("disarm")
        self._record("DISARM")
        self.armed = False
        return {}

    def _firmware_status(self) -> dict[str, Any]:
        self.events.append("firmware_status")
        self._record("STATUS")
        return {
            "armed": self.armed,
            "keysDown": 0,
            "mouseButtonsDown": 0,
            **self.status_overrides,
        }

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
    target_bounds: ScreenBounds | None = None,
    button: MouseButton = MouseButton.LEFT,
) -> ApprovedPointerIntent:
    return ApprovedPointerIntent(
        intent_id=intent_id,
        purpose=purpose,
        target=target,
        movement_bounds=BOUNDS,
        target_bounds=(
            target_bounds
            if target_bounds is not None
            else ScreenBounds(target.x, target.y, 1, 1)
        ),
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

        def validate(
            _intent: ApprovedPointerIntent,
            actual: ScreenPoint,
        ) -> InputValidation:
            backend.events.append("validator")
            validated_at.append((actual.x, actual.y))
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
                "STATUS",
                "MOVE",
                "MOVE",
                "STATUS",
                "STATUS",
                "STATUS",
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
            [
                "firmware_status",
                "firmware_status",
                "firmware_status",
                "firmware_status",
                "stop_all",
                "disarm",
                "firmware_status",
                "ledger_end",
                "close",
            ],
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
            "camera-turn",
            InputPurpose.GAMEPLAY_KEY,
            "right",
            321,
            250,
        )

        def validate(_intent: ApprovedKeyIntent) -> InputValidation:
            backend.events.append("key_validator")
            return InputValidation.allow("fresh dialogue still present")

        receipt = coordinator(backend).execute_key(intent, validate=validate)

        self.assertTrue(receipt.successful)
        self.assertLess(backend.events.index("arm"), backend.events.index("key_validator"))
        self.assertLess(backend.events.index("key_validator"), backend.events.index("press:RIGHT"))
        self.assertEqual([("RIGHT", 250)], backend.key_presses)
        self.assertEqual(
            [command.command for command in receipt.commands],
            [
                "ARM",
                "STATUS",
                "STATUS",
                "STATUS",
                "KEY_PRESS",
                "STOP_ALL",
                "DISARM",
                "STATUS",
            ],
        )

    def test_denied_fresh_validator_blocks_activation_but_still_proves_cleanup(self) -> None:
        backend = FakeBackend()
        receipt = coordinator(backend).execute_pointer(
            pointer_intent(),
            validate=lambda _intent, _actual: InputValidation.deny("hover changed"),
        )

        self.assertEqual(receipt.status, "BLOCKED")
        self.assertFalse(receipt.successful)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_watchdog_disarm_during_validation_rearms_before_activation(self) -> None:
        backend = FakeBackend()
        validation_count = 0

        def validate(
            _intent: ApprovedPointerIntent,
            _actual: ScreenPoint,
        ) -> InputValidation:
            nonlocal validation_count
            validation_count += 1
            backend.events.append("slow_validator_completed")
            if validation_count == 1:
                backend.armed = False
            return InputValidation.allow("fresh evidence survived validation")

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(), validate=validate
        )

        self.assertTrue(receipt.successful)
        commands = [command.command for command in receipt.commands]
        self.assertEqual(2, validation_count)
        self.assertEqual(2, commands.count("ARM"))
        self.assertEqual(7, commands.count("STATUS"))
        second_arm = [index for index, name in enumerate(commands) if name == "ARM"][1]
        self.assertLess(second_arm, commands.index("MOUSE_DOWN"))
        self.assertEqual(0, receipt.failed_command_count)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_second_watchdog_expiry_during_revalidation_blocks_without_click(self) -> None:
        backend = FakeBackend()
        validation_count = 0

        def validate(
            _intent: ApprovedPointerIntent,
            _actual: ScreenPoint,
        ) -> InputValidation:
            nonlocal validation_count
            validation_count += 1
            backend.armed = False
            return InputValidation.allow("validator completed")

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(), validate=validate
        )

        self.assertFalse(receipt.successful)
        self.assertEqual(2, validation_count)
        self.assertIn("after_firmware_revalidation", receipt.reason)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_key_watchdog_rearm_forces_fresh_revalidation(self) -> None:
        backend = FakeBackend()
        intent = ApprovedKeyIntent(
            "escape-dialogue",
            InputPurpose.GAMEPLAY_KEY,
            "ESC",
            321,
        )
        validation_count = 0

        def validate(_intent: ApprovedKeyIntent) -> InputValidation:
            nonlocal validation_count
            validation_count += 1
            if validation_count == 1:
                backend.armed = False
            return InputValidation.allow("dialogue still present")

        receipt = coordinator(backend).execute_key(intent, validate=validate)

        self.assertTrue(receipt.successful)
        self.assertEqual(2, validation_count)
        commands = [command.command for command in receipt.commands]
        self.assertEqual(2, commands.count("ARM"))
        self.assertLess(
            [index for index, name in enumerate(commands) if name == "ARM"][1],
            commands.index("KEY_PRESS"),
        )
        self.assertEqual(0, receipt.failed_command_count)

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
            validate_hover=lambda _intent, _actual: self._validation_event(
                backend, "hover_validator"
            ),
            resolve_row=resolve_row,
            validate_row=lambda _intent, _actual: self._validation_event(
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

    def test_watchdog_disarm_during_context_resolver_rearms_before_row_move(self) -> None:
        backend = FakeBackend()
        open_intent = pointer_intent(
            intent_id="context-open",
            purpose=InputPurpose.CONTEXT_MENU,
            target=ScreenPoint(12, 10),
            button=MouseButton.RIGHT,
        )

        def resolve_row() -> ApprovedPointerIntent:
            backend.armed = False
            return pointer_intent(
                intent_id="context-row",
                purpose=InputPurpose.CONTEXT_ROW,
                target=ScreenPoint(12, 12),
            )

        receipt = coordinator(backend).execute_context_menu(
            open_intent,
            validate_hover=lambda _intent, _actual: InputValidation.allow(),
            resolve_row=resolve_row,
            validate_row=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        commands = [command.command for command in receipt.commands]
        self.assertEqual(2, commands.count("ARM"))
        right_up = commands.index("MOUSE_UP")
        second_arm = [index for index, name in enumerate(commands) if name == "ARM"][1]
        row_move = commands.index("MOVE", right_up + 1)
        self.assertLess(right_up, second_arm)
        self.assertLess(second_arm, row_move)
        self.assertEqual(0, receipt.failed_command_count)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_context_hover_watchdog_rearm_forces_fresh_revalidation(self) -> None:
        backend = FakeBackend()
        open_intent = pointer_intent(
            intent_id="context-open",
            purpose=InputPurpose.CONTEXT_MENU,
            button=MouseButton.RIGHT,
        )
        hover_count = 0

        def validate_hover(
            _intent: ApprovedPointerIntent,
            _actual: ScreenPoint,
        ) -> InputValidation:
            nonlocal hover_count
            hover_count += 1
            if hover_count == 1:
                backend.armed = False
            return InputValidation.allow("hover remains exact")

        receipt = coordinator(backend).execute_context_menu(
            open_intent,
            validate_hover=validate_hover,
            resolve_row=lambda: pointer_intent(
                intent_id="context-row",
                purpose=InputPurpose.CONTEXT_ROW,
                target=ScreenPoint(12, 12),
            ),
            validate_row=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual(2, hover_count)
        self.assertEqual(
            2,
            [command.command for command in receipt.commands].count("ARM"),
        )

    def test_context_row_watchdog_rearm_forces_fresh_revalidation(self) -> None:
        backend = FakeBackend()
        open_intent = pointer_intent(
            intent_id="context-open",
            purpose=InputPurpose.CONTEXT_MENU,
            button=MouseButton.RIGHT,
        )
        row_count = 0

        def validate_row(
            _intent: ApprovedPointerIntent,
            _actual: ScreenPoint,
        ) -> InputValidation:
            nonlocal row_count
            row_count += 1
            if row_count == 1:
                backend.armed = False
            return InputValidation.allow("row remains exact")

        receipt = coordinator(backend).execute_context_menu(
            open_intent,
            validate_hover=lambda _intent, _actual: InputValidation.allow(),
            resolve_row=lambda: pointer_intent(
                intent_id="context-row",
                purpose=InputPurpose.CONTEXT_ROW,
                target=ScreenPoint(12, 12),
            ),
            validate_row=validate_row,
        )

        self.assertTrue(receipt.successful)
        self.assertEqual(2, row_count)
        self.assertEqual(
            2,
            [command.command for command in receipt.commands].count("ARM"),
        )

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
            validate_hover=lambda _intent, _actual: InputValidation.allow(),
            resolve_row=fail_resolver,
            validate_row=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertEqual(receipt.status, "BLOCKED")
        self.assertIn("menu sample unavailable", receipt.reason)
        self.assertTrue(receipt.context_cancel_attempted)
        self.assertTrue(receipt.context_cancel_acknowledged)
        commands = [command.command for command in receipt.commands]
        self.assertLess(commands.index("KEY_PRESS"), commands.index("STOP_ALL"))
        self.assertLess(backend.events.index("press:ESC"), backend.events.index("stop_all"))

    def test_disarmed_context_cancel_rearms_before_escape(self) -> None:
        backend = FakeBackend()
        open_intent = pointer_intent(
            intent_id="context-open",
            purpose=InputPurpose.CONTEXT_MENU,
            button=MouseButton.RIGHT,
        )

        def fail_resolver() -> ApprovedPointerIntent:
            backend.armed = False
            raise RuntimeError("menu sample unavailable")

        receipt = coordinator(backend).execute_context_menu(
            open_intent,
            validate_hover=lambda _intent, _actual: InputValidation.allow(),
            resolve_row=fail_resolver,
            validate_row=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertTrue(receipt.context_cancel_acknowledged)
        commands = [command.command for command in receipt.commands]
        self.assertEqual(2, commands.count("ARM"))
        second_arm = [index for index, name in enumerate(commands) if name == "ARM"][1]
        self.assertLess(second_arm, commands.index("KEY_PRESS"))
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_partial_context_right_click_still_attempts_escape_cancellation(self) -> None:
        open_intent = pointer_intent(
            intent_id="context-open",
            purpose=InputPurpose.CONTEXT_MENU,
            button=MouseButton.RIGHT,
        )
        for label, backend in (
            ("release_rejected", FakeBackend(fail_commands={"MOUSE_UP"})),
            (
                "release_ack_missing",
                FakeBackend(missing_ack_commands={"MOUSE_UP"}),
            ),
        ):
            with self.subTest(label=label):
                receipt = coordinator(backend).execute_context_menu(
                    open_intent,
                    validate_hover=lambda _intent, _actual: InputValidation.allow(),
                    resolve_row=lambda: (_ for _ in ()).throw(
                        AssertionError("row resolver must not run")
                    ),
                    validate_row=lambda _intent, _actual: InputValidation.allow(),
                )

                self.assertEqual("ERROR", receipt.status)
                self.assertTrue(receipt.context_cancel_attempted)
                self.assertTrue(receipt.context_cancel_acknowledged)
                commands = [command.command for command in receipt.commands]
                self.assertLess(
                    commands.index("MOUSE_DOWN"), commands.index("KEY_PRESS")
                )
                self.assertLess(
                    commands.index("KEY_PRESS"), commands.index("STOP_ALL")
                )
                self.assertTrue(
                    receipt.firmware_status and receipt.firmware_status.safe
                )

    def test_rejected_context_button_down_does_not_send_escape(self) -> None:
        backend = FakeBackend(fail_commands={"MOUSE_DOWN"})
        open_intent = pointer_intent(
            intent_id="context-open",
            purpose=InputPurpose.CONTEXT_MENU,
            button=MouseButton.RIGHT,
        )

        receipt = coordinator(backend).execute_context_menu(
            open_intent,
            validate_hover=lambda _intent, _actual: InputValidation.allow(),
            resolve_row=lambda: (_ for _ in ()).throw(
                AssertionError("row resolver must not run")
            ),
            validate_row=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("ERROR", receipt.status)
        self.assertFalse(receipt.context_cancel_attempted)
        self.assertNotIn("KEY_PRESS", [command.command for command in receipt.commands])
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_context_cancel_ack_failure_is_explicit(self) -> None:
        backend = FakeBackend(missing_ack_commands={"KEY_PRESS"})
        open_intent = pointer_intent(
            intent_id="context-open",
            purpose=InputPurpose.CONTEXT_MENU,
            button=MouseButton.RIGHT,
        )
        receipt = coordinator(backend).execute_context_menu(
            open_intent,
            validate_hover=lambda _intent, _actual: InputValidation.allow(),
            resolve_row=lambda: (_ for _ in ()).throw(RuntimeError("no row")),
            validate_row=lambda _intent, _actual: InputValidation.allow(),
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
            pointer_intent(),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("cursor_feedback", receipt.reason)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.stop_all_acknowledged)

    def test_feedback_divergence_is_corrected_by_a_bounded_replan(self) -> None:
        backend = FakeBackend(divergent_move_count=1)
        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(14, 10)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual(backend.position, (14, 10))
        self.assertEqual(
            [command.command for command in receipt.commands].count("MOVE"), 3
        )

    def test_initial_axis_no_effect_uses_one_larger_bounded_probe(self) -> None:
        backend = FakeBackend(start=(50, 50), no_effect_x_move_count=1)
        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(54, 54)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual((54, 54), backend.position)
        moves = [event for event in backend.events if event.startswith("move:")]
        self.assertEqual("move:1,1", moves[0])
        self.assertEqual("move:2,1", moves[1])
        self.assertIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_persistent_initial_axis_no_effect_blocks_before_click(self) -> None:
        backend = FakeBackend(start=(50, 50), no_effect_x_move_count=2)
        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(54, 54)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("cursor_feedback_no_effect_x", receipt.reason)
        self.assertEqual(
            ["move:1,1", "move:2,1"],
            [event for event in backend.events if event.startswith("move:")],
        )
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_one_calibrated_axis_no_effect_uses_a_bounded_replan(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            no_effect_x_move_indices={3},
        )
        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(70, 70)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual((70, 70), backend.position)
        self.assertGreaterEqual(backend.move_call_count, 4)
        self.assertIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_consecutive_calibrated_axis_no_effect_blocks_before_click(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            no_effect_x_move_indices={3, 4},
        )
        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(70, 70)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("cursor_feedback_no_effect_x", receipt.reason)
        self.assertEqual(4, backend.move_call_count)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_intermittent_no_effect_events_share_a_transaction_cap(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            no_effect_x_move_indices=set(range(3, 40, 2)),
        )
        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(80, 80)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn(
            "cursor_feedback_no_effect_transaction_limit_exceeded",
            receipt.reason,
        )
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_device_pixel_scaling_accepts_settled_login_region_endpoint(self) -> None:
        backend = FakeBackend(
            start=(1427, 911),
            device_pixel_scale=1.75,
        )
        intent = ApprovedPointerIntent(
            intent_id="login-play-now",
            purpose=InputPurpose.LOGIN_PROMPT,
            target=ScreenPoint(1361, 861),
            movement_bounds=ScreenBounds(252, 52, 2219, 1573),
            target_bounds=ScreenBounds(1218, 812, 287, 99),
            expected_pid=321,
        )
        validated_at: list[ScreenPoint] = []

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, actual: (
                validated_at.append(actual) or InputValidation.allow()
            ),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual([ScreenPoint(1427, 909)], validated_at)
        self.assertTrue(intent.target_bounds.contains(validated_at[0]))
        self.assertEqual(
            1,
            sum(event.startswith("move:") for event in backend.events),
        )
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_already_stable_target_region_uses_zero_step_plan(self) -> None:
        backend = FakeBackend(start=(11, 10))
        intent = pointer_intent(
            target=ScreenPoint(12, 10),
            target_bounds=ScreenBounds(9, 7, 7, 7),
        )
        validated_at: list[ScreenPoint] = []

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, actual: (
                validated_at.append(actual) or InputValidation.allow()
            ),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual([ScreenPoint(11, 10)], validated_at)
        self.assertEqual(0, sum(event.startswith("move:") for event in backend.events))

    def test_scaled_long_gameplay_move_reaches_narrow_safe_region(self) -> None:
        backend = FakeBackend(
            start=(100, 100),
            device_pixel_scale=1.75,
        )
        bounds = ScreenBounds(0, 0, 1000, 800)
        intent = ApprovedPointerIntent(
            intent_id="scaled-gameplay",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(500, 400),
            movement_bounds=bounds,
            target_bounds=ScreenBounds(497, 397, 7, 7),
            expected_pid=321,
        )
        validated_at: list[ScreenPoint] = []
        plans = []

        def recording_planner(*args, **kwargs):  # type: ignore[no-untyped-def]
            plan = plan_pointer_motion(*args, **kwargs)
            plans.append(plan)
            return plan

        receipt = InputCoordinator(
            lambda: backend,
            pointer_planner=recording_planner,
            sleep=lambda _seconds: None,
            pointer_timestep_seconds=0.02,
        ).execute_pointer(
            intent,
            validate=lambda _intent, actual: (
                validated_at.append(actual) or InputValidation.allow()
            ),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual(1, len(validated_at))
        self.assertTrue(intent.target_bounds.contains(validated_at[0]))
        self.assertTrue(
            all(bounds.contains(ScreenPoint(*point)) for point in backend.positions)
        )
        self.assertLessEqual(len(plans), 64)
        self.assertLessEqual(
            [command.command for command in receipt.commands].count("MOVE"),
            512,
        )

    def test_insufficient_initial_transfer_headroom_sends_no_move(self) -> None:
        backend = FakeBackend(start=(3, 5), device_pixel_scale=1.75)
        bounds = ScreenBounds(0, 0, 10, 10)
        intent = ApprovedPointerIntent(
            intent_id="no-probe-headroom",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(9, 5),
            movement_bounds=bounds,
            target_bounds=ScreenBounds(9, 5, 1, 1),
            expected_pid=321,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("transfer_headroom_insufficient", receipt.reason)
        self.assertEqual(0, sum(event.startswith("move:") for event in backend.events))
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_scaled_edge_move_aborts_before_leaving_verified_bounds(self) -> None:
        backend = FakeBackend(start=(9, 5), device_pixel_scale=1.75)
        bounds = ScreenBounds(0, 0, 10, 10)
        intent = ApprovedPointerIntent(
            intent_id="scaled-edge",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(1, 5),
            movement_bounds=bounds,
            target_bounds=ScreenBounds(1, 5, 1, 1),
            expected_pid=321,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn(
            "cursor_bidirectional_transfer_headroom_insufficient",
            receipt.reason,
        )
        self.assertTrue(
            all(bounds.contains(ScreenPoint(*point)) for point in backend.positions)
        )
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_bidirectional_envelope_blocks_edge_reversal_or_cross_axis_drift(self) -> None:
        bounds = ScreenBounds(0, 0, 10, 10)
        cases = (
            ("reversal", FakeBackend(start=(8, 5)), ScreenPoint(1, 5)),
            (
                "cross-axis",
                FakeBackend(start=(5, 8), cursor_diverges=True),
                ScreenPoint(1, 8),
            ),
        )
        for label, backend, target in cases:
            with self.subTest(label=label):
                intent = ApprovedPointerIntent(
                    intent_id=f"edge-{label}",
                    purpose=InputPurpose.GAMEPLAY_OBJECT,
                    target=target,
                    movement_bounds=bounds,
                    target_bounds=ScreenBounds(target.x, target.y, 1, 1),
                    expected_pid=321,
                )
                receipt = coordinator(backend).execute_pointer(
                    intent,
                    validate=lambda _intent, _actual: InputValidation.allow(),
                )
                self.assertFalse(receipt.successful)
                self.assertIn("transfer_headroom_insufficient", receipt.reason)
                self.assertEqual(
                    0,
                    sum(event.startswith("move:") for event in backend.events),
                )
                self.assertTrue(
                    receipt.firmware_status and receipt.firmware_status.safe
                )

    def test_unsupported_transfer_gain_aborts_without_activation(self) -> None:
        backend = FakeBackend(start=(50, 50), device_pixel_scale=5.0)
        intent = pointer_intent(
            target=ScreenPoint(80, 50),
            target_bounds=ScreenBounds(77, 47, 7, 7),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("cursor_transfer_gain_exceeded_x", receipt.reason)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_max_supported_transfer_gain_reaches_safe_region(self) -> None:
        backend = FakeBackend(start=(20, 50), device_pixel_scale=4.0)
        intent = pointer_intent(
            target=ScreenPoint(80, 50),
            target_bounds=ScreenBounds(77, 47, 7, 7),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertTrue(intent.target_bounds.contains(ScreenPoint(*backend.position)))
        self.assertTrue(
            all(BOUNDS.contains(ScreenPoint(*point)) for point in backend.positions)
        )

    def test_feedback_plan_and_step_caps_fail_closed(self) -> None:
        intent = pointer_intent(
            target=ScreenPoint(40, 10),
            target_bounds=ScreenBounds(37, 7, 7, 7),
        )
        for label, kwargs, expected in (
            (
                "plan",
                {"max_correction_plans": 0},
                "cursor_feedback_correction_limit_exceeded",
            ),
            (
                "step",
                {"max_pointer_steps": 1, "max_correction_plans": 2},
                "pointer motion exceeds the total step limit",
            ),
        ):
            with self.subTest(label=label):
                backend = FakeBackend()
                receipt = InputCoordinator(
                    lambda: backend,
                    sleep=lambda _seconds: None,
                    pointer_timestep_seconds=0.02,
                    **kwargs,
                ).execute_pointer(
                    intent,
                    validate=lambda _intent, _actual: InputValidation.allow(),
                )
                self.assertFalse(receipt.successful)
                self.assertIn(expected, receipt.reason)
                self.assertNotIn("mouse_down:left", backend.events)
                self.assertTrue(
                    receipt.firmware_status and receipt.firmware_status.safe
                )

    def test_feedback_caps_are_shared_across_adaptive_transaction_moves(self) -> None:
        backend = FakeBackend()
        main = pointer_intent(target=ScreenPoint(12, 10))
        row = pointer_intent(
            intent_id="row",
            purpose=InputPurpose.CONTEXT_ROW,
            target=ScreenPoint(12, 12),
        )
        receipt = InputCoordinator(
            lambda: backend,
            sleep=lambda _seconds: None,
            pointer_timestep_seconds=0.02,
            max_correction_plans=2,
        ).execute_adaptive_pointer(
            main,
            decide_activation=lambda _intent, _actual: (
                PointerActivationDecision.context("exact lower option")
            ),
            resolve_row=lambda: row,
            validate_row=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn(
            "pointer transaction exceeds the total feedback plan limit",
            receipt.reason,
        )
        self.assertEqual(
            3,
            [command.command for command in receipt.commands].count("MOVE"),
        )
        self.assertTrue(receipt.context_cancel_attempted)
        self.assertTrue(receipt.context_cancel_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_cursor_change_after_validation_blocks_activation_and_cleans_up(self) -> None:
        backend = FakeBackend()
        intent = pointer_intent(
            target_bounds=ScreenBounds(9, 7, 8, 8),
        )

        def validate(
            _intent: ApprovedPointerIntent,
            actual: ScreenPoint,
        ) -> InputValidation:
            backend.position = (actual.x + 1, actual.y)
            return InputValidation.allow("fresh target retained")

        receipt = coordinator(backend).execute_pointer(intent, validate=validate)

        self.assertFalse(receipt.successful)
        self.assertIn("cursor_changed_after_pointer_validation", receipt.reason)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_login_pointer_refuses_a_start_outside_verified_transit_bounds(self) -> None:
        backend = FakeBackend(start=(150, 10))
        login_intent = pointer_intent(
            purpose=InputPurpose.LOGIN_PROMPT,
            target=ScreenPoint(12, 10),
        )
        receipt = coordinator(backend).execute_pointer(
            login_intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual(receipt.status, "BLOCKED")
        self.assertIn("cursor_start_outside", receipt.reason)
        self.assertFalse(any(event.startswith("move:") for event in backend.events))

    def test_adaptive_pointer_chooses_direct_left_from_fresh_evidence(self) -> None:
        backend = FakeBackend()

        def decide(
            _intent: ApprovedPointerIntent,
            _actual: ScreenPoint,
        ) -> PointerActivationDecision:
            backend.events.append("activation_decision")
            return PointerActivationDecision.direct("fresh default option matches")

        receipt = coordinator(backend).execute_adaptive_pointer(
            pointer_intent(),
            decide_activation=decide,
            resolve_row=lambda: (_ for _ in ()).throw(
                AssertionError("context resolver must not run")
            ),
            validate_row=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertIn("mouse_down:left", backend.events)
        self.assertNotIn("mouse_down:right", backend.events)
        self.assertLess(
            backend.events.index("activation_decision"),
            backend.events.index("mouse_down:left"),
        )

    def test_adaptive_watchdog_revalidation_uses_second_activation_decision(self) -> None:
        backend = FakeBackend()
        decision_count = 0

        def decide(
            _intent: ApprovedPointerIntent,
            _actual: ScreenPoint,
        ) -> PointerActivationDecision:
            nonlocal decision_count
            decision_count += 1
            if decision_count == 1:
                backend.armed = False
                return PointerActivationDecision.context("first sample")
            return PointerActivationDecision.direct("fresh second sample")

        receipt = coordinator(backend).execute_adaptive_pointer(
            pointer_intent(),
            decide_activation=decide,
            resolve_row=lambda: (_ for _ in ()).throw(
                AssertionError("stale context decision must not govern")
            ),
            validate_row=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual(2, decision_count)
        self.assertIn("mouse_down:left", backend.events)
        self.assertNotIn("mouse_down:right", backend.events)
        self.assertEqual(
            2,
            [command.command for command in receipt.commands].count("ARM"),
        )

    def test_adaptive_pointer_context_branch_stays_in_one_transaction(self) -> None:
        backend = FakeBackend()
        receipt = coordinator(backend).execute_adaptive_pointer(
            pointer_intent(),
            decide_activation=lambda _intent, _actual: PointerActivationDecision.context(
                "fresh menu requires exact lower row"
            ),
            resolve_row=lambda: pointer_intent(
                intent_id="fresh-row",
                purpose=InputPurpose.CONTEXT_ROW,
                target=ScreenPoint(12, 12),
            ),
            validate_row=lambda _intent, _actual: InputValidation.allow(
                "fresh exact row retained"
            ),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual(receipt.intent_ids, ("object-1", "fresh-row"))
        self.assertEqual(backend.events.count("connect"), 1)
        self.assertEqual(backend.events.count("arm"), 1)
        self.assertIn("mouse_down:right", backend.events)
        self.assertIn("mouse_down:left", backend.events)

    def test_partial_adaptive_right_click_still_attempts_escape_cancellation(self) -> None:
        backend = FakeBackend(fail_commands={"MOUSE_UP"})

        receipt = coordinator(backend).execute_adaptive_pointer(
            pointer_intent(),
            decide_activation=lambda _intent, _actual: (
                PointerActivationDecision.context("exact lower row")
            ),
            resolve_row=lambda: (_ for _ in ()).throw(
                AssertionError("row resolver must not run")
            ),
            validate_row=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("ERROR", receipt.status)
        self.assertTrue(receipt.context_cancel_attempted)
        self.assertTrue(receipt.context_cancel_acknowledged)
        commands = [command.command for command in receipt.commands]
        self.assertLess(commands.index("MOUSE_DOWN"), commands.index("KEY_PRESS"))
        self.assertLess(commands.index("KEY_PRESS"), commands.index("STOP_ALL"))
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_rejected_adaptive_button_down_does_not_send_escape(self) -> None:
        backend = FakeBackend(fail_commands={"MOUSE_DOWN"})

        receipt = coordinator(backend).execute_adaptive_pointer(
            pointer_intent(),
            decide_activation=lambda _intent, _actual: (
                PointerActivationDecision.context("exact lower row")
            ),
            resolve_row=lambda: (_ for _ in ()).throw(
                AssertionError("row resolver must not run")
            ),
            validate_row=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("ERROR", receipt.status)
        self.assertFalse(receipt.context_cancel_attempted)
        self.assertNotIn("KEY_PRESS", [command.command for command in receipt.commands])
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

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
                    validate=lambda _intent, _actual: InputValidation.allow(),
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
                    validate=lambda _intent, _actual: InputValidation.allow(),
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
        for hold_millis in (0, 251, True):
            with self.subTest(hold_millis=hold_millis):
                with self.assertRaises(ValueError):
                    ApprovedKeyIntent(
                        "bad-hold",
                        InputPurpose.GAMEPLAY_KEY,
                        "RIGHT",
                        321,
                        hold_millis,
                    )
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
        with self.assertRaises(ValueError):
            InputCoordinator(
                lambda: backend,
                max_correction_plans=64,
            )
        with self.assertRaises(ValueError):
            InputCoordinator(
                lambda: backend,
                max_pointer_steps=513,
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

from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError, replace
from typing import Any

from osrs_bot.arduino import ArduinoHIDError
from osrs_bot.input_coordinator import (
    ApprovedKeyIntent,
    ApprovedPointerIntent,
    CommandEvidence,
    FirmwareSafetyStatus,
    InputCoordinator,
    InputFailureKind,
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
        delayed_x_move_indices: set[int] | None = None,
        delayed_y_move_indices: set[int] | None = None,
        release_delayed_x_on_position_call: int | None = None,
        release_delayed_y_on_position_call: int | None = None,
        foreground_hwnds: list[int] | None = None,
        point_owner_hwnds: list[int] | None = None,
        position_error: Exception | None = None,
        position_samples: list[tuple[int, int]] | None = None,
        window_handoff_error: Exception | None = None,
        window_handoff_evidence_overrides: dict[str, Any] | None = None,
        window_geometry_evidence_overrides: dict[str, Any] | None = None,
        physical_mouse_errors: list[Exception | None] | None = None,
        physical_mouse_evidence_overrides: dict[str, Any] | None = None,
        owned_transition_error: Exception | None = None,
        input_lease_error: Exception | None = None,
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
        self.delayed_x_move_indices = set(delayed_x_move_indices or ())
        self.delayed_y_move_indices = set(delayed_y_move_indices or ())
        self.delayed_x = 0
        self.delayed_y = 0
        self.release_delayed_x_on_position_call = release_delayed_x_on_position_call
        self.release_delayed_y_on_position_call = release_delayed_y_on_position_call
        self.position_call_count = 0
        self.move_call_count = 0
        self.foreground_hwnds = list(foreground_hwnds or ())
        self.point_owner_hwnds = list(point_owner_hwnds or ())
        self.position_error = position_error
        self.position_samples = list(position_samples or ())
        self.window_handoff_error = window_handoff_error
        self.window_handoff_evidence_overrides = dict(
            window_handoff_evidence_overrides or {}
        )
        self.window_geometry_evidence_overrides = dict(
            window_geometry_evidence_overrides or {}
        )
        self.physical_mouse_errors = list(physical_mouse_errors or ())
        self.physical_mouse_evidence_overrides = dict(
            physical_mouse_evidence_overrides or {}
        )
        self.owned_transition_error = owned_transition_error
        self.input_lease_error = input_lease_error
        self.owned_transition_pending: str | None = None
        self.last_window_handoff: dict[str, Any] | None = None
        self.last_foreground_hwnd = 77
        self.last_point_owner_hwnd = 77
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

    def _acquire_input_lease(self) -> None:
        self.events.append("input_lease")
        if self.input_lease_error is not None:
            raise self.input_lease_error

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
        if self.position_error is not None:
            raise self.position_error
        self.position_call_count += 1
        if self.position_samples:
            self.position = self.position_samples.pop(0)
            self.positions.append(self.position)
        release_x = (
            self.delayed_x
            if self.position_call_count == self.release_delayed_x_on_position_call
            else 0
        )
        release_y = (
            self.delayed_y
            if self.position_call_count == self.release_delayed_y_on_position_call
            else 0
        )
        if release_x or release_y:
            self.position = (
                self.position[0] + self._scaled_delta(release_x),
                self.position[1] + self._scaled_delta(release_y),
            )
            self.delayed_x -= release_x
            self.delayed_y -= release_y
            self.positions.append(self.position)
        self.events.append(f"position:{self.position[0]},{self.position[1]}")
        return self.position

    def _move_relative(self, dx: int, dy: int) -> dict[str, Any]:
        self.events.append(f"move:{dx},{dy}")
        self._record("MOVE")
        self.move_call_count += 1
        if dx and self.move_call_count in self.delayed_x_move_indices:
            self.delayed_x += dx
            dx = 0
        elif self.delayed_x:
            dx += self.delayed_x
            self.delayed_x = 0
        if dy and self.move_call_count in self.delayed_y_move_indices:
            self.delayed_y += dy
            dy = 0
        elif self.delayed_y:
            dy += self.delayed_y
            self.delayed_y = 0
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
        if self.foreground_hwnds:
            self.last_foreground_hwnd = self.foreground_hwnds.pop(0)
        return {"pid": expected_pid, "hwnd": self.last_foreground_hwnd}

    def _window_info_at_point(self, point: tuple[int, int]) -> dict[str, Any]:
        self.events.append(f"point_owner:{point[0]},{point[1]}")
        if self.point_owner_hwnds:
            self.last_point_owner_hwnd = self.point_owner_hwnds.pop(0)
        return {"pid": 321, "hwnd": self.last_point_owner_hwnd}

    def _reposition_window_for_cursor(
        self,
        *,
        expected_pid: int,
        expected_hwnd: int,
        cursor: tuple[int, int],
        movement_bounds: tuple[int, int, int, int],
        inset_px: int,
    ) -> dict[str, Any]:
        self.events.append("window_handoff")
        if self.window_handoff_error is not None:
            raise self.window_handoff_error
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
        if dx == 0 and dy == 0:
            dx = 1
        evidence: dict[str, Any] = {
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
        evidence.update(self.window_handoff_evidence_overrides)
        self.last_window_handoff = evidence
        return evidence

    def _verify_window_geometry(
        self,
        *,
        expected_pid: int,
        expected_hwnd: int,
        expected_outer_bounds: tuple[int, int, int, int] | None,
        expected_client_bounds: tuple[int, int, int, int] | None,
        required_inner_bounds: tuple[int, int, int, int],
    ) -> dict[str, Any]:
        self.events.append("window_geometry")
        def payload(
            values: tuple[int, int, int, int] | None,
        ) -> dict[str, int] | None:
            if values is None:
                return None
            x, y, width, height = values
            return {"x": x, "y": y, "width": width, "height": height}

        actual_outer = expected_outer_bounds or expected_client_bounds
        actual_client = expected_client_bounds or required_inner_bounds
        evidence: dict[str, Any] = {
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
        evidence.update(self.window_geometry_evidence_overrides)
        return evidence

    def _verify_physical_mouse_quiet(self) -> dict[str, Any]:
        self.events.append("physical_mouse_quiet")
        if self.owned_transition_pending is not None:
            raise ArduinoHIDError("owned Arduino mouse transition was not consumed")
        if self.physical_mouse_errors:
            error = self.physical_mouse_errors.pop(0)
            if error is not None:
                raise error
        evidence: dict[str, Any] = {
            "schema": "physical_mouse_quiet.v1",
            "buttonsUp": True,
            "activityClear": True,
        }
        evidence.update(self.physical_mouse_evidence_overrides)
        return evidence

    def _consume_owned_mouse_transition(self, button: str) -> dict[str, Any]:
        self.events.append(f"consume_owned_mouse:{button}")
        if self.owned_transition_error is not None:
            raise self.owned_transition_error
        consumed = self.owned_transition_pending == button
        self.owned_transition_pending = None
        return {
            "schema": "owned_mouse_transition.v1",
            "button": button,
            "ownedTransitionConsumed": consumed,
            "buttonsUp": True,
            "activityClear": True,
        }

    def _mouse_down(self, *, button: str = "left") -> None:
        self.events.append(f"mouse_down:{button}")
        self.owned_transition_pending = button
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
    def test_reacquisition_bounds_are_strictly_scoped_and_contain_movement(self) -> None:
        with self.assertRaisesRegex(ValueError, "initial login or gameplay"):
            ApprovedPointerIntent(
                intent_id="context-reacquire",
                purpose=InputPurpose.CONTEXT_ROW,
                target=ScreenPoint(12, 10),
                movement_bounds=BOUNDS,
                target_bounds=ScreenBounds(12, 10, 1, 1),
                expected_pid=321,
                reacquisition_bounds=ScreenBounds(0, 0, 120, 120),
            )
        with self.assertRaisesRegex(ValueError, "must contain movement_bounds"):
            ApprovedPointerIntent(
                intent_id="small-reacquire",
                purpose=InputPurpose.LOGIN_PROMPT,
                target=ScreenPoint(12, 10),
                movement_bounds=BOUNDS,
                target_bounds=ScreenBounds(12, 10, 1, 1),
                expected_pid=321,
                reacquisition_bounds=ScreenBounds(1, 1, 98, 98),
            )

    def test_manual_cursor_position_inside_canvas_is_resampled_each_action(self) -> None:
        backend = FakeBackend(start=(20, 20), device_pixel_scale=2.0)
        first = pointer_intent(
            intent_id="first-position",
            target=ScreenPoint(70, 70),
            target_bounds=ScreenBounds(67, 67, 7, 7),
        )
        second = pointer_intent(
            intent_id="manual-position",
            target=ScreenPoint(25, 25),
            target_bounds=ScreenBounds(22, 22, 7, 7),
        )

        first_receipt = coordinator(backend).execute_pointer(
            first,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )
        backend.position = (85, 15)
        backend.positions.append(backend.position)
        second_receipt = coordinator(backend).execute_pointer(
            second,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(first_receipt.successful)
        self.assertTrue(second_receipt.successful)
        self.assertTrue(second.target_bounds.contains(ScreenPoint(*backend.position)))

    def test_physical_mouse_activity_blocks_before_serial_connect(self) -> None:
        backend = FakeBackend(
            physical_mouse_errors=[
                ArduinoHIDError(
                    "physical mouse button held or pressed during cursor window handoff"
                )
            ]
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertTrue(receipt.safely_unsent)
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertIn("physical_mouse_not_quiet_pointer_preflight", receipt.reason)
        self.assertNotIn("connect", backend.events)
        self.assertFalse(any(
            event.startswith(("move:", "mouse_down:"))
            for event in backend.events
        ))

    def test_physical_mouse_activity_during_transaction_blocks_activation(self) -> None:
        backend = FakeBackend(
            physical_mouse_errors=[
                None,
                None,
                ArduinoHIDError(
                    "physical mouse button held or pressed during cursor window handoff"
                ),
            ]
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertFalse(receipt.safely_unsent)
        self.assertIn("physical_mouse_not_quiet_before_pointer_validation", receipt.reason)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_outer_chrome_reacquires_live_shaped_login_cursor_with_headroom(self) -> None:
        backend = FakeBackend(
            start=(3421, 1594),
            device_pixel_scale=2.25,
        )
        client = ScreenBounds(1191, 472, 2219, 1573)
        outer = ScreenBounds(1167, 460, 2267, 1609)
        intent = ApprovedPointerIntent(
            intent_id="login-border-reacquire",
            purpose=InputPurpose.LOGIN_PROMPT,
            target=ScreenPoint(2300, 1281),
            movement_bounds=client,
            target_bounds=ScreenBounds(2220, 1230, 160, 102),
            expected_pid=321,
            reacquisition_bounds=outer,
        )
        validated: list[ScreenPoint] = []

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, actual: (
                validated.append(actual) or InputValidation.allow()
            ),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual("move:-1,0", next(
            event for event in backend.events if event.startswith("move:")
        ))
        self.assertTrue(all(
            outer.contains(ScreenPoint(*point)) for point in backend.positions
        ))
        self.assertEqual(1, len(validated))
        self.assertTrue(intent.target_bounds.contains(validated[0]))
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_exact_outer_window_edge_blocks_without_movement(self) -> None:
        backend = FakeBackend(
            start=(3421, 1594),
            device_pixel_scale=2.25,
        )
        intent = ApprovedPointerIntent(
            intent_id="login-edge-block",
            purpose=InputPurpose.LOGIN_PROMPT,
            target=ScreenPoint(2300, 1281),
            movement_bounds=ScreenBounds(1191, 472, 2219, 1573),
            target_bounds=ScreenBounds(2220, 1230, 160, 102),
            expected_pid=321,
            reacquisition_bounds=ScreenBounds(1179, 472, 2243, 1585),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("outer_headroom_insufficient", receipt.reason)
        self.assertIs(receipt.failure_kind, InputFailureKind.NONE)
        self.assertFalse(any(
            event.startswith("move:") for event in backend.events
        ))
        self.assertFalse(any(
            event.startswith("mouse_down") for event in backend.events
        ))
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_external_live_cursor_repositions_window_then_requires_reobserve(self) -> None:
        client = ScreenBounds(1191, 472, 2219, 1573)
        outer = ScreenBounds(1179, 472, 2243, 1585)
        target = ScreenPoint(2300, 1281)
        target_bounds = ScreenBounds(2220, 1230, 160, 102)
        first_backend = FakeBackend(
            start=(3446, 1631),
            physical_mouse_evidence_overrides={
                "historicalActivityConsumed": True,
                "sampleCount": 3,
            },
        )
        stale_intent = ApprovedPointerIntent(
            intent_id="login-external-handoff-stale",
            purpose=InputPurpose.LOGIN_PROMPT,
            target=target,
            movement_bounds=client,
            target_bounds=target_bounds,
            expected_pid=321,
            expected_hwnd=77,
            reacquisition_bounds=outer,
        )

        first_receipt = coordinator(first_backend).execute_pointer(
            stale_intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", first_receipt.status)
        self.assertIn("repositioned_reobserve_required", first_receipt.reason)
        self.assertIs(
            first_receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertTrue(first_receipt.safely_unsent)
        self.assertFalse(first_receipt.connected)
        self.assertEqual((), first_receipt.commands)
        self.assertIn("window_handoff", first_backend.events)
        self.assertNotIn("connect", first_backend.events)
        self.assertLess(
            first_backend.events.index("input_lease"),
            first_backend.events.index("window_handoff"),
        )
        self.assertFalse(any(
            event.startswith(("move:", "mouse_down:"))
            for event in first_backend.events
        ))
        self.assertNotIn("stop_all", first_backend.events)
        self.assertNotIn("disarm", first_backend.events)
        self.assertEqual(
            {
                "x": 1260,
                "y": 472,
                "width": 2219,
                "height": 1573,
            },
            (first_backend.last_window_handoff or {}).get(
                "newMovementBounds"
            ),
        )

        dx = 69
        fresh_client = ScreenBounds(client.x + dx, client.y, client.width, client.height)
        fresh_outer = ScreenBounds(outer.x + dx, outer.y, outer.width, outer.height)
        fresh_intent = ApprovedPointerIntent(
            intent_id="login-external-handoff-fresh",
            purpose=InputPurpose.LOGIN_PROMPT,
            target=ScreenPoint(target.x + dx, target.y),
            movement_bounds=fresh_client,
            target_bounds=ScreenBounds(
                target_bounds.x + dx,
                target_bounds.y,
                target_bounds.width,
                target_bounds.height,
            ),
            expected_pid=321,
            expected_hwnd=77,
            reacquisition_bounds=fresh_outer,
        )
        second_backend = FakeBackend(start=(3446, 1631))
        second_receipt = coordinator(second_backend).execute_pointer(
            fresh_intent,
            validate=lambda _intent, actual: (
                InputValidation.allow()
                if fresh_intent.target_bounds.contains(actual)
                else InputValidation.deny("fresh target mismatch")
            ),
        )

        self.assertTrue(second_receipt.successful)
        self.assertFalse(second_receipt.safely_unsent)
        self.assertNotIn("window_handoff", second_backend.events)
        self.assertLess(
            second_backend.events.index("input_lease"),
            second_backend.events.index("connect"),
        )
        self.assertTrue(all(
            fresh_client.contains(ScreenPoint(*point))
            for point in second_backend.positions
        ))

    def test_input_lease_contention_cannot_mutate_window(self) -> None:
        backend = FakeBackend(
            start=(3446, 1631),
            input_lease_error=ArduinoHIDError(
                "Arduino serial port COM6 is already owned"
            ),
        )
        intent = ApprovedPointerIntent(
            intent_id="contended-window-handoff",
            purpose=InputPurpose.LOGIN_PROMPT,
            target=ScreenPoint(2300, 1281),
            movement_bounds=ScreenBounds(1191, 472, 2219, 1573),
            target_bounds=ScreenBounds(2220, 1230, 160, 102),
            expected_pid=321,
            expected_hwnd=77,
            reacquisition_bounds=ScreenBounds(1179, 472, 2243, 1585),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.safely_unsent)
        self.assertFalse(receipt.connected)
        self.assertEqual((), receipt.commands)
        self.assertIn("already owned", receipt.reason)
        self.assertIn("input_lease", backend.events)
        self.assertNotIn("window_handoff", backend.events)
        self.assertNotIn("connect", backend.events)
        self.assertTrue(receipt.ledger_closed)
        self.assertTrue(receipt.backend_closed)

    def test_window_handoff_requires_stationary_cursor_and_complete_proof(self) -> None:
        intent = ApprovedPointerIntent(
            intent_id="stationary-window-handoff",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(150, 150),
            movement_bounds=ScreenBounds(100, 100, 200, 200),
            target_bounds=ScreenBounds(147, 147, 7, 7),
            expected_pid=321,
            expected_hwnd=77,
            reacquisition_bounds=ScreenBounds(90, 90, 220, 220),
        )
        moving = FakeBackend(
            start=(320, 150),
            position_samples=[(320, 150), (321, 150)],
        )
        moving_receipt = coordinator(moving).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )
        self.assertTrue(moving_receipt.safely_unsent)
        self.assertIn("not_settled", moving_receipt.reason)
        self.assertNotIn("window_handoff", moving.events)
        self.assertNotIn("connect", moving.events)

        incomplete = FakeBackend(
            start=(320, 150),
            window_handoff_evidence_overrides={
                "pointOwnerConfirmed": False
            },
        )
        incomplete_receipt = coordinator(incomplete).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )
        self.assertTrue(incomplete_receipt.safely_unsent)
        self.assertIs(
            incomplete_receipt.failure_kind,
            InputFailureKind.NONE,
        )
        self.assertIn("post_mutation_evidence_unproved", incomplete_receipt.reason)
        self.assertIn("evidence_invalid", incomplete_receipt.reason)
        self.assertNotIn("connect", incomplete.events)
        self.assertFalse(any(
            event.startswith(("move:", "mouse_down:"))
            for event in incomplete.events
        ))

        post_mutation_error = ArduinoHIDError("point owner proof failed")
        post_mutation_error.window_mutation_attempted = True
        unproved = FakeBackend(
            start=(320, 150),
            window_handoff_error=post_mutation_error,
        )
        unproved_receipt = coordinator(unproved).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )
        self.assertTrue(unproved_receipt.safely_unsent)
        self.assertIs(
            unproved_receipt.failure_kind,
            InputFailureKind.NONE,
        )
        self.assertIn("post_mutation_unproved", unproved_receipt.reason)
        self.assertNotIn("connect", unproved.events)

    def test_stale_client_geometry_blocks_before_serial_or_pointer_motion(self) -> None:
        client = ScreenBounds(90, 90, 220, 220)
        backend = FakeBackend(
            start=(150, 150),
            window_geometry_evidence_overrides={
                "actualOuterBounds": {
                    "x": 159,
                    "y": 90,
                    "width": 220,
                    "height": 220,
                },
                "outerMatches": False,
            },
        )
        intent = ApprovedPointerIntent(
            intent_id="stale-client-geometry",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(160, 160),
            movement_bounds=ScreenBounds(100, 100, 200, 200),
            target_bounds=ScreenBounds(157, 157, 7, 7),
            expected_pid=321,
            reacquisition_bounds=client,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertTrue(receipt.safely_unsent)
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertIn("geometry_changed_reobserve_required", receipt.reason)
        self.assertIn("window_geometry", backend.events)
        self.assertNotIn("connect", backend.events)
        self.assertFalse(any(
            event.startswith(("move:", "mouse_down:"))
            for event in backend.events
        ))

    def test_one_pixel_awt_native_origin_quantization_allows_gameplay(self) -> None:
        client = ScreenBounds(90, 90, 220, 220)
        backend = FakeBackend(
            start=(150, 150),
            window_geometry_evidence_overrides={
                "actualOuterBounds": {
                    "x": 91,
                    "y": 89,
                    "width": 220,
                    "height": 220,
                },
                "outerMatches": False,
            },
        )
        intent = ApprovedPointerIntent(
            intent_id="awt-native-origin-quantization",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(160, 160),
            movement_bounds=ScreenBounds(100, 100, 200, 200),
            target_bounds=ScreenBounds(157, 157, 7, 7),
            expected_pid=321,
            reacquisition_bounds=client,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertIn("window_geometry", backend.events)
        self.assertIn("connect", backend.events)
        self.assertIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_gameplay_quantization_rejects_two_pixels_or_any_resize(self) -> None:
        incompatible = (
            {"x": 92, "y": 90, "width": 220, "height": 220},
            {"x": 90, "y": 90, "width": 221, "height": 220},
            {"x": 90, "y": 90, "width": 220, "height": 219},
        )
        for actual_outer in incompatible:
            with self.subTest(actual_outer=actual_outer):
                backend = FakeBackend(
                    start=(150, 150),
                    window_geometry_evidence_overrides={
                        "actualOuterBounds": actual_outer,
                        "outerMatches": False,
                    },
                )
                intent = ApprovedPointerIntent(
                    intent_id="reject-broad-geometry-tolerance",
                    purpose=InputPurpose.GAMEPLAY_OBJECT,
                    target=ScreenPoint(160, 160),
                    movement_bounds=ScreenBounds(100, 100, 200, 200),
                    target_bounds=ScreenBounds(157, 157, 7, 7),
                    expected_pid=321,
                    reacquisition_bounds=ScreenBounds(90, 90, 220, 220),
                )

                receipt = coordinator(backend).execute_pointer(
                    intent,
                    validate=lambda _intent, _actual: InputValidation.allow(),
                )

                self.assertEqual("BLOCKED", receipt.status)
                self.assertTrue(receipt.safely_unsent)
                self.assertIn(
                    "geometry_changed_reobserve_required",
                    receipt.reason,
                )
                self.assertNotIn("connect", backend.events)

    def test_quantized_outer_never_bypasses_canvas_containment(self) -> None:
        backend = FakeBackend(
            start=(150, 150),
            window_geometry_evidence_overrides={
                "actualOuterBounds": {
                    "x": 91,
                    "y": 90,
                    "width": 220,
                    "height": 220,
                },
                "actualClientBounds": {
                    "x": 140,
                    "y": 140,
                    "width": 20,
                    "height": 20,
                },
                "outerMatches": False,
                "innerContainedByClient": False,
            },
        )
        intent = ApprovedPointerIntent(
            intent_id="quantized-outer-still-requires-canvas",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(160, 160),
            movement_bounds=ScreenBounds(100, 100, 200, 200),
            target_bounds=ScreenBounds(157, 157, 7, 7),
            expected_pid=321,
            reacquisition_bounds=ScreenBounds(90, 90, 220, 220),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertTrue(receipt.safely_unsent)
        self.assertIn("geometry_changed_reobserve_required", receipt.reason)
        self.assertNotIn("connect", backend.events)

    def test_login_keeps_exact_outer_geometry_under_one_pixel_drift(self) -> None:
        backend = FakeBackend(
            start=(150, 150),
            window_geometry_evidence_overrides={
                "actualOuterBounds": {
                    "x": 91,
                    "y": 90,
                    "width": 220,
                    "height": 220,
                },
                "outerMatches": False,
            },
        )
        intent = ApprovedPointerIntent(
            intent_id="login-exact-native-geometry",
            purpose=InputPurpose.LOGIN_PROMPT,
            target=ScreenPoint(160, 160),
            movement_bounds=ScreenBounds(100, 100, 200, 200),
            target_bounds=ScreenBounds(157, 157, 7, 7),
            expected_pid=321,
            expected_hwnd=77,
            reacquisition_bounds=ScreenBounds(90, 90, 220, 220),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertTrue(receipt.safely_unsent)
        self.assertIn("geometry_changed_reobserve_required", receipt.reason)
        self.assertNotIn("connect", backend.events)

    def test_final_point_owner_mismatch_blocks_activation(self) -> None:
        backend = FakeBackend(
            start=(10, 10),
            point_owner_hwnds=[77, 88],
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("point_owner_mismatch", receipt.reason)
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_reacquisition_requires_the_pinned_foreground_window_to_own_cursor(self) -> None:
        client = ScreenBounds(100, 100, 200, 200)
        outer = ScreenBounds(80, 80, 240, 240)
        intent = ApprovedPointerIntent(
            intent_id="owned-reacquire",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(150, 150),
            movement_bounds=client,
            target_bounds=ScreenBounds(147, 147, 7, 7),
            expected_pid=321,
            expected_hwnd=77,
            reacquisition_bounds=outer,
        )

        wrong_start_owner = FakeBackend(
            start=(305, 150), point_owner_hwnds=[88]
        )
        start_receipt = coordinator(wrong_start_owner).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )
        self.assertFalse(start_receipt.successful)
        self.assertIn("point_owner_mismatch", start_receipt.reason)
        self.assertIs(
            start_receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertFalse(any(
            event.startswith("move:") for event in wrong_start_owner.events
        ))

        owner_changes_after_move = FakeBackend(
            start=(305, 150), point_owner_hwnds=[77, 77, 88]
        )
        changed_receipt = coordinator(owner_changes_after_move).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )
        self.assertFalse(changed_receipt.successful)
        self.assertIn("point_owner_mismatch", changed_receipt.reason)
        self.assertEqual(
            1,
            sum(
                event.startswith("move:")
                for event in owner_changes_after_move.events
            ),
        )
        self.assertFalse(any(
            event.startswith("mouse_down")
            for event in owner_changes_after_move.events
        ))
        self.assertTrue(
            changed_receipt.firmware_status
            and changed_receipt.firmware_status.safe
        )

    def test_reacquisition_gap_and_step_caps_share_the_exact_boundary(self) -> None:
        client = ScreenBounds(100, 100, 200, 200)
        outer = ScreenBounds(50, 50, 350, 300)
        intent = ApprovedPointerIntent(
            intent_id="reacquire-cap",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(291, 150),
            movement_bounds=client,
            target_bounds=ScreenBounds(288, 147, 7, 7),
            expected_pid=321,
            reacquisition_bounds=outer,
        )

        exact = FakeBackend(start=(363, 150))
        exact_receipt = coordinator(exact).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )
        self.assertTrue(exact_receipt.successful)
        self.assertEqual(
            72,
            sum(event.startswith("move:") for event in exact.events),
        )

        too_far = FakeBackend(start=(364, 150))
        far_receipt = coordinator(too_far).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )
        self.assertFalse(far_receipt.successful)
        self.assertIn("gap_exceeded", far_receipt.reason)
        self.assertFalse(any(
            event.startswith("move:") for event in too_far.events
        ))

    def test_reacquisition_reconciles_one_delayed_report_without_a_second_move(self) -> None:
        client = ScreenBounds(100, 100, 200, 200)
        outer = ScreenBounds(80, 80, 240, 240)
        intent = ApprovedPointerIntent(
            intent_id="reacquire-delayed",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(291, 150),
            movement_bounds=client,
            target_bounds=ScreenBounds(288, 147, 7, 7),
            expected_pid=321,
            reacquisition_bounds=outer,
        )
        delayed = FakeBackend(
            start=(305, 150),
            delayed_x_move_indices={1},
            release_delayed_x_on_position_call=3,
        )
        validated: list[ScreenPoint] = []

        receipt = coordinator(delayed).execute_pointer(
            intent,
            validate=lambda _intent, actual: (
                validated.append(actual) or InputValidation.allow()
            ),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual([ScreenPoint(291, 150)], validated)
        self.assertTrue(all(
            outer.contains(ScreenPoint(*point)) for point in delayed.positions
        ))
        self.assertLess(
            delayed.events.index("position:304,150"),
            delayed.events.index("move:-1,0", delayed.events.index("move:-1,0") + 1),
        )

        unresolved = FakeBackend(
            start=(305, 150),
            delayed_x_move_indices={1},
        )
        blocked = coordinator(unresolved).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )
        self.assertFalse(blocked.successful)
        self.assertIn("reacquisition_no_effect", blocked.reason)
        self.assertEqual(
            1,
            sum(event.startswith("move:") for event in unresolved.events),
        )
        self.assertFalse(any(
            event.startswith("mouse_down") for event in unresolved.events
        ))
        self.assertTrue(blocked.firmware_status and blocked.firmware_status.safe)

    def test_pointer_hwnd_remains_pinned_after_reacquisition(self) -> None:
        backend = FakeBackend(start=(305, 150))
        intent = ApprovedPointerIntent(
            intent_id="reacquire-window-switch",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(291, 150),
            movement_bounds=ScreenBounds(100, 100, 200, 200),
            target_bounds=ScreenBounds(288, 147, 7, 7),
            expected_pid=321,
            reacquisition_bounds=ScreenBounds(80, 80, 240, 240),
        )
        validator_calls = 0

        def switch_window(
            _intent: ApprovedPointerIntent, _actual: ScreenPoint
        ) -> InputValidation:
            nonlocal validator_calls
            validator_calls += 1
            backend.last_foreground_hwnd = 88
            return InputValidation.allow()

        receipt = coordinator(backend).execute_pointer(
            intent, validate=switch_window
        )

        self.assertFalse(receipt.successful)
        self.assertEqual(1, validator_calls)
        self.assertIn("foreground_window_changed", receipt.reason)
        self.assertFalse(any(
            event.startswith("mouse_down") for event in backend.events
        ))
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_reacquisition_still_requires_ordinary_fresh_validator(self) -> None:
        backend = FakeBackend(start=(305, 150))
        intent = ApprovedPointerIntent(
            intent_id="reacquire-validator-veto",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(291, 150),
            movement_bounds=ScreenBounds(100, 100, 200, 200),
            target_bounds=ScreenBounds(288, 147, 7, 7),
            expected_pid=321,
            reacquisition_bounds=ScreenBounds(80, 80, 240, 240),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.deny(
                "hover evidence changed"
            ),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("fresh_input_validation_denied", receipt.reason)
        self.assertIs(receipt.failure_kind, InputFailureKind.NONE)
        self.assertFalse(any(
            event.startswith("mouse_down") for event in backend.events
        ))
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_reacquisition_rejects_corner_inside_outer_envelope(self) -> None:
        client = ScreenBounds(100, 100, 200, 200)
        outer = ScreenBounds(90, 90, 220, 220)
        backend = FakeBackend(start=(305, 305))
        intent = ApprovedPointerIntent(
            intent_id="reacquire-corner",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(150, 150),
            movement_bounds=client,
            target_bounds=ScreenBounds(147, 147, 7, 7),
            expected_pid=321,
            reacquisition_bounds=outer,
        )
        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )
        self.assertFalse(receipt.successful)
        self.assertIn("requires_one_outside_axis", receipt.reason)
        self.assertFalse(any(
            event.startswith("mouse_down") for event in backend.events
        ))
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

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
        self.assertIn("consume_owned_mouse:left", backend.events)
        self.assertIsNone(backend.owned_transition_pending)
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

    def test_unproved_owned_transition_after_click_is_terminal(self) -> None:
        backend = FakeBackend(
            owned_transition_error=ArduinoHIDError(
                "physical mouse changed during owned-transition settle"
            )
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("ERROR", receipt.status)
        self.assertIs(receipt.failure_kind, InputFailureKind.NONE)
        self.assertIn("owned_mouse_transition_unproved", receipt.reason)
        self.assertIn("mouse_down:left", backend.events)
        self.assertIn("mouse_up:left", backend.events)
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

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

    def test_dpi_cursor_failure_sends_no_input_and_proves_safe_cleanup(self) -> None:
        backend = FakeBackend(
            position_error=ArduinoHIDError(
                "Windows per-monitor-v2 cursor DPI awareness could not be "
                "established"
            )
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("cursor DPI awareness could not be established", receipt.reason)
        self.assertFalse(any(event.startswith("move:") for event in backend.events))
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertNotIn("mouse_up:left", backend.events)
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)
        self.assertEqual(0, receipt.unresolved_command_count)
        self.assertTrue(receipt.ledger_closed)
        self.assertTrue(receipt.backend_closed)

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
        self.assertIs(receipt.failure_kind, InputFailureKind.NONE)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_one_delayed_same_direction_report_uses_prior_command_credit(self) -> None:
        backend = FakeBackend(
            start=(150, 150),
            device_pixel_scale=4.0,
            delayed_x_move_indices={2},
        )
        intent = ApprovedPointerIntent(
            intent_id="delayed-coalesced",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(170, 150),
            movement_bounds=ScreenBounds(0, 0, 300, 300),
            target_bounds=ScreenBounds(167, 147, 20, 7),
            expected_pid=321,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual((178, 150), backend.position)
        self.assertIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_intermediate_delayed_report_is_polled_before_another_move(self) -> None:
        backend = FakeBackend(
            start=(100, 250),
            delayed_x_move_indices={2},
            release_delayed_x_on_position_call=5,
        )
        intent = ApprovedPointerIntent(
            intent_id="intermediate-delayed-report",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(400, 250),
            movement_bounds=ScreenBounds(0, 0, 500, 500),
            target_bounds=ScreenBounds(397, 247, 7, 7),
            expected_pid=321,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        move_indices = [
            index
            for index, event in enumerate(backend.events)
            if event.startswith("move:")
        ]
        self.assertGreaterEqual(len(move_indices), 3)
        between_second_and_third = backend.events[
            move_indices[1] + 1 : move_indices[2]
        ]
        self.assertGreaterEqual(
            sum(
                event.startswith("position:")
                for event in between_second_and_third
            ),
            2,
        )
        self.assertIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_delayed_poll_cannot_mask_wrong_initial_direction(self) -> None:
        class MaskedInitialDirectionBackend(FakeBackend):
            def _current_position(self) -> tuple[int, int]:
                self.position_call_count += 1
                samples = {
                    1: (100, 100),
                    2: (99, 100),
                    3: (101, 101),
                }
                self.position = samples.get(
                    self.position_call_count, self.position
                )
                self.positions.append(self.position)
                self.events.append(
                    f"position:{self.position[0]},{self.position[1]}"
                )
                return self.position

        backend = MaskedInitialDirectionBackend(start=(100, 100))

        receipt = coordinator(backend).execute_pointer(
            ApprovedPointerIntent(
                intent_id="masked-initial-direction",
                purpose=InputPurpose.GAMEPLAY_OBJECT,
                target=ScreenPoint(104, 104),
                movement_bounds=ScreenBounds(0, 0, 300, 300),
                target_bounds=ScreenBounds(104, 104, 1, 1),
                expected_pid=321,
            ),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn(
            "cursor_feedback_direction_mismatch_x:initial_sample",
            receipt.reason,
        )
        self.assertEqual(2, backend.position_call_count)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_delayed_poll_cannot_mask_wrong_incremental_direction(self) -> None:
        class MaskedDelayedDirectionBackend(FakeBackend):
            def _current_position(self) -> tuple[int, int]:
                self.position_call_count += 1
                samples = {
                    1: (100, 100),
                    2: (100, 102),
                    3: (101, 101),
                }
                self.position = samples.get(
                    self.position_call_count, self.position
                )
                self.positions.append(self.position)
                self.events.append(
                    f"position:{self.position[0]},{self.position[1]}"
                )
                return self.position

        backend = MaskedDelayedDirectionBackend(start=(100, 100))

        receipt = coordinator(backend).execute_pointer(
            ApprovedPointerIntent(
                intent_id="masked-delayed-direction",
                purpose=InputPurpose.GAMEPLAY_OBJECT,
                target=ScreenPoint(104, 104),
                movement_bounds=ScreenBounds(0, 0, 300, 300),
                target_bounds=ScreenBounds(104, 104, 1, 1),
                expected_pid=321,
            ),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn(
            "cursor_feedback_direction_mismatch_y:delayed_sample",
            receipt.reason,
        )
        self.assertEqual(3, backend.position_call_count)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_delayed_report_still_blocks_above_combined_transfer_budget(self) -> None:
        class ExcessiveDelayedBackend(FakeBackend):
            def _move_relative(self, dx: int, dy: int) -> dict[str, Any]:
                result = super()._move_relative(dx, dy)
                if self.move_call_count == 3:
                    self.position = (self.position[0] + 1, self.position[1])
                    self.positions[-1] = self.position
                return result

        backend = ExcessiveDelayedBackend(
            start=(150, 150),
            device_pixel_scale=4.0,
            delayed_x_move_indices={2},
        )
        intent = ApprovedPointerIntent(
            intent_id="delayed-excessive",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(170, 150),
            movement_bounds=ScreenBounds(0, 0, 300, 300),
            target_bounds=ScreenBounds(167, 147, 21, 7),
            expected_pid=321,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertEqual(
            "cursor_transfer_gain_exceeded_x:commanded=2:observed=25:"
            "delayed=4:plan=3:step=3",
            receipt.reason,
        )
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_delayed_report_can_arrive_during_other_axis_move(self) -> None:
        backend = FakeBackend(
            start=(100, 100),
            delayed_x_move_indices={2},
        )
        intent = ApprovedPointerIntent(
            intent_id="delayed-zero-current-axis",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(105, 137),
            movement_bounds=ScreenBounds(0, 0, 250, 250),
            target_bounds=ScreenBounds(105, 137, 1, 1),
            expected_pid=321,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual((105, 137), backend.position)
        self.assertIn("move:0,1", backend.events)
        self.assertIn("mouse_down:left", backend.events)

    def test_narrow_scaled_target_accepts_one_coalesced_final_report(self) -> None:
        backend = FakeBackend(
            start=(2141, 1177),
            device_pixel_scale=2.25,
            delayed_y_move_indices={8},
        )
        intent = ApprovedPointerIntent(
            intent_id="delayed-live-row-shape",
            purpose=InputPurpose.CONTEXT_ROW,
            target=ScreenPoint(2141, 1280),
            movement_bounds=ScreenBounds(1199, 520, 2151, 1519),
            target_bounds=ScreenBounds(2138, 1277, 7, 7),
            expected_pid=321,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual((2141, 1280), backend.position)
        self.assertEqual(9, backend.move_call_count)
        self.assertIn("mouse_down:left", backend.events)

    def test_delayed_report_can_arrive_during_plan_settle(self) -> None:
        backend = FakeBackend(
            start=(100, 100),
            delayed_x_move_indices={1},
            release_delayed_x_on_position_call=3,
        )
        intent = ApprovedPointerIntent(
            intent_id="delayed-plan-settle",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(101, 100),
            movement_bounds=ScreenBounds(0, 0, 250, 250),
            target_bounds=ScreenBounds(101, 100, 1, 1),
            expected_pid=321,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual((101, 100), backend.position)
        self.assertEqual(1, backend.move_call_count)
        self.assertIn("mouse_down:left", backend.events)

    def test_unresolved_delayed_command_blocks_activation(self) -> None:
        backend = FakeBackend(
            start=(100, 100),
            device_pixel_scale=4.0,
            delayed_x_move_indices={5},
        )
        intent = ApprovedPointerIntent(
            intent_id="unresolved-delayed",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(200, 100),
            movement_bounds=ScreenBounds(0, 0, 300, 300),
            target_bounds=ScreenBounds(184, 97, 20, 7),
            expected_pid=321,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("unresolved_delayed_command", receipt.reason)
        self.assertNotIn("mouse_down:left", backend.events)

    def test_combined_delayed_headroom_blocks_before_next_move(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            device_pixel_scale=4.0,
            delayed_x_move_indices={2},
        )
        intent = pointer_intent(
            target=ScreenPoint(70, 50),
            target_bounds=ScreenBounds(67, 47, 20, 7),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("transfer_envelope_would_leave_bounds", receipt.reason)
        self.assertEqual(2, backend.move_call_count)
        self.assertNotIn("mouse_down:left", backend.events)

    def test_opposite_delayed_command_is_rejected_before_transport(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "cursor_feedback_delayed_direction_mismatch_x",
        ):
            InputCoordinator._assert_delayed_command_compatible(
                delayed=4,
                commanded=-1,
                axis="x",
            )

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

    def test_tight_margin_recovers_one_axis_at_a_time_away_from_edge(self) -> None:
        bounds = ScreenBounds(0, 0, 100, 100)
        backend = FakeBackend(start=(13, 50))
        intent = ApprovedPointerIntent(
            intent_id="sequential-headroom-recovery",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(50, 70),
            movement_bounds=bounds,
            target_bounds=ScreenBounds(50, 70, 1, 1),
            expected_pid=321,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        moves = [event for event in backend.events if event.startswith("move:")]
        self.assertEqual("move:1,0", moves[0])
        self.assertEqual((50, 70), backend.position)
        self.assertTrue(all(bounds.contains(ScreenPoint(*p)) for p in backend.positions))

    def test_tied_tight_margins_recover_deterministically_across_axes(self) -> None:
        bounds = ScreenBounds(0, 0, 100, 100)
        backend = FakeBackend(start=(13, 13))
        intent = ApprovedPointerIntent(
            intent_id="tied-sequential-headroom-recovery",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(50, 50),
            movement_bounds=bounds,
            target_bounds=ScreenBounds(50, 50, 1, 1),
            expected_pid=321,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        moves = [event for event in backend.events if event.startswith("move:")]
        self.assertEqual(["move:1,0", "move:0,1"], moves[:2])
        self.assertEqual((50, 50), backend.position)
        self.assertTrue(all(bounds.contains(ScreenPoint(*p)) for p in backend.positions))

    def test_live_tight_margin_shape_recovers_inside_canvas(self) -> None:
        canvas = ScreenBounds(1199, 520, 2151, 1519)
        backend = FakeBackend(
            start=(1213, 1064),
            device_pixel_scale=2.25,
        )
        intent = ApprovedPointerIntent(
            intent_id="live-sequential-headroom-recovery",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(1649, 988),
            movement_bounds=canvas,
            target_bounds=ScreenBounds(1646, 985, 7, 7),
            expected_pid=321,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        moves = [event for event in backend.events if event.startswith("move:")]
        self.assertEqual("move:1,0", moves[0])
        self.assertLessEqual(len(moves), 512)
        self.assertTrue(intent.target_bounds.contains(ScreenPoint(*backend.position)))
        self.assertTrue(all(canvas.contains(ScreenPoint(*p)) for p in backend.positions))
        self.assertIn("mouse_down:left", backend.events)

    def test_opposite_tied_margins_do_not_claim_directional_recovery(self) -> None:
        backend = FakeBackend(start=(13, 50))
        intent = ApprovedPointerIntent(
            intent_id="opposite-tied-headroom",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(20, 70),
            movement_bounds=ScreenBounds(0, 0, 27, 100),
            target_bounds=ScreenBounds(20, 70, 1, 1),
            expected_pid=321,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("bidirectional_transfer_headroom_insufficient", receipt.reason)
        self.assertEqual(0, backend.move_call_count)

    def test_tight_margin_does_not_move_toward_the_nearest_edge(self) -> None:
        backend = FakeBackend(start=(13, 50))
        intent = ApprovedPointerIntent(
            intent_id="unsafe-headroom-direction",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(5, 70),
            movement_bounds=ScreenBounds(0, 0, 100, 100),
            target_bounds=ScreenBounds(5, 70, 1, 1),
            expected_pid=321,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("bidirectional_transfer_headroom_insufficient", receipt.reason)
        self.assertEqual(0, backend.move_call_count)
        self.assertNotIn("mouse_down:left", backend.events)

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
        self.assertEqual(
            "cursor_transfer_gain_exceeded_x:commanded=1:observed=5:"
            "delayed=0:plan=1:step=1",
            receipt.reason,
        )
        self.assertIs(receipt.failure_kind, InputFailureKind.NONE)
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
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
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
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
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

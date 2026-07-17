from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError, replace
from typing import Any

from osrs_bot.arduino import ArduinoHIDError
from osrs_bot.input_capabilities import InputCapabilities
from osrs_bot.input_coordinator import (
    ApprovedCameraHoldIntent,
    ApprovedCameraZoomIntent,
    ApprovedCursorRecoveryIntent,
    ApprovedKeyIntent,
    ApprovedPointerIntent,
    CommandEvidence,
    CursorFeedbackEvidence,
    CursorInvalidationCause,
    DelayedCursorFeedbackEvent,
    FirmwareSafetyStatus,
    InputCoordinator,
    InputFailureKind,
    InputPurpose,
    InputReceipt,
    InputValidation,
    MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT,
    MouseButton,
    PointerActivationDecision,
)
from osrs_bot.model import ScreenBounds, ScreenPoint
from osrs_bot.observability import (
    MAX_DURATION_MILLIS,
    ObservabilityEvidence,
    TimingPhase,
    WaitState,
)
from osrs_bot.pointer import gameplay_pointer_safe_bounds, plan_pointer_motion


_TERMINAL = {
    "PASS",
    "WRITE_FAIL",
    "ACK_TIMEOUT_OR_READ_FAIL",
    "REJECTED",
    "UNEXPECTED_RESPONSE",
}


def expanded_input_capabilities() -> InputCapabilities:
    return InputCapabilities.from_negotiation(
        {"protocol": "arduino_hid.v2", "version": "2.0.0"},
        {
            "schema": "input_capabilities.v2",
            "protocol": "arduino_hid.v2",
            "firmwareVersion": "2.0.0",
            "pointer": True,
            "mouse": True,
            "relativeMove": True,
            "maxMoveDelta": 20,
            "buttons": "left,right,middle",
            "buttonDownUp": True,
            "click": True,
            "maxClickHoldMs": 250,
            "keyboard": True,
            "keys": "basic",
            "keyPress": True,
            "maxKeyPressMs": 250,
            "holdKeys": True,
            "maxHoldKeysMs": 250,
            "cameraKeyHold": True,
            "cameraKeys": "left,right,up,down",
            "maxCameraHoldMs": 600,
            "wheel": True,
            "maxWheelStep": 3,
            "arm": True,
            "watchdog": True,
            "watchdogMs": 1000,
            "stopAll": True,
            "disarm": True,
            "status": True,
            "resetSafe": True,
        },
        {
            "armed": False,
            "keysDown": 0,
            "mouseButtonsDown": 0,
            "watchdogMs": 1000,
        },
    )


def legacy_input_capabilities() -> InputCapabilities:
    return InputCapabilities.from_negotiation(
        {"protocol": "arduino_hid.v1", "version": "1.0.0"},
        {
            "mouse": True,
            "keyboard": True,
            "relativeMove": True,
            "buttons": "left,right,middle",
            "keys": "basic",
            "holdKeys": True,
            "watchdog": True,
            "stopAll": True,
            "resetSafe": True,
        },
        {
            "armed": False,
            "keysDown": 0,
            "mouseButtonsDown": 0,
            "watchdogMs": 1000,
        },
    )


class FakeBackend:
    def __init__(
        self,
        *,
        start: tuple[int, int] = (10, 10),
        fail_commands: set[str] | None = None,
        missing_ack_commands: set[str] | None = None,
        pending_commands: set[str] | None = None,
        unsafe_status: dict[str, Any] | None = None,
        status_overrides_sequence: list[dict[str, Any]] | None = None,
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
        window_geometry_evidence_overrides: dict[str, Any] | None = None,
        window_geometry_evidence_sequence: list[dict[str, Any]] | None = None,
        virtual_desktop_bounds: tuple[int, int, int, int] = (-4000, -3000, 8000, 6000),
        virtual_desktop_bounds_sequence: list[tuple[int, int, int, int]] | None = None,
        foreground_errors: list[Exception | None] | None = None,
        physical_mouse_errors: list[Exception | None] | None = None,
        physical_mouse_release_errors: list[Exception | None] | None = None,
        physical_mouse_historical_calls: set[int] | None = None,
        physical_mouse_evidence_overrides: dict[str, Any] | None = None,
        physical_mouse_release_evidence_overrides: dict[str, Any] | None = None,
        owned_transition_error: Exception | None = None,
        input_lease_error: Exception | None = None,
        input_capabilities: InputCapabilities | None = None,
    ) -> None:
        self.position = start
        self.fail_commands = set(fail_commands or ())
        self.missing_ack_commands = set(missing_ack_commands or ())
        self.pending_commands = set(pending_commands or ())
        self.armed = False
        self.status_overrides = dict(unsafe_status or {})
        self.status_overrides_sequence = [
            dict(overrides) for overrides in (status_overrides_sequence or ())
        ]
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
        self.window_geometry_evidence_overrides = dict(
            window_geometry_evidence_overrides or {}
        )
        self.window_geometry_evidence_sequence = [
            dict(evidence)
            for evidence in (window_geometry_evidence_sequence or ())
        ]
        self.virtual_desktop_bounds = virtual_desktop_bounds
        self.virtual_desktop_bounds_sequence = list(
            virtual_desktop_bounds_sequence or ()
        )
        self.foreground_errors = list(foreground_errors or ())
        self.physical_mouse_errors = list(physical_mouse_errors or ())
        self.physical_mouse_release_errors = list(
            physical_mouse_release_errors or ()
        )
        self.physical_mouse_historical_calls = set(
            physical_mouse_historical_calls or ()
        )
        self.physical_mouse_call_count = 0
        self.physical_mouse_evidence_overrides = dict(
            physical_mouse_evidence_overrides or {}
        )
        self.physical_mouse_release_evidence_overrides = dict(
            physical_mouse_release_evidence_overrides or {}
        )
        self.owned_transition_error = owned_transition_error
        self.input_lease_error = input_lease_error
        self.input_capabilities = input_capabilities or expanded_input_capabilities()
        self.owned_transition_pending: str | None = None
        self.last_foreground_hwnd = 77
        self.last_point_owner_hwnd = 77
        self.positions: list[tuple[int, int]] = [start]
        self.key_presses: list[tuple[str, int]] = []
        self.camera_holds: list[tuple[str, int]] = []
        self.wheel_amounts: list[int] = []
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

    def _input_capabilities(self) -> InputCapabilities:
        self.events.append("input_capabilities")
        return self.input_capabilities

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
        if self.foreground_errors:
            error = self.foreground_errors.pop(0)
            if error is not None:
                raise error
        if self.foreground_hwnds:
            self.last_foreground_hwnd = self.foreground_hwnds.pop(0)
        return {"pid": expected_pid, "hwnd": self.last_foreground_hwnd}

    def _window_info_at_point(self, point: tuple[int, int]) -> dict[str, Any]:
        self.events.append(f"point_owner:{point[0]},{point[1]}")
        if self.point_owner_hwnds:
            self.last_point_owner_hwnd = self.point_owner_hwnds.pop(0)
        return {"pid": 321, "hwnd": self.last_point_owner_hwnd}

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
        overrides = self.window_geometry_evidence_overrides
        if self.window_geometry_evidence_sequence:
            overrides = self.window_geometry_evidence_sequence.pop(0)
        evidence.update(overrides)
        return evidence

    def _virtual_desktop_bounds(self) -> dict[str, Any]:
        self.events.append("virtual_desktop")
        if self.virtual_desktop_bounds_sequence:
            self.virtual_desktop_bounds = self.virtual_desktop_bounds_sequence.pop(0)
        x, y, width, height = self.virtual_desktop_bounds
        return {
            "schema": "virtual_desktop_geometry.v1",
            "coordinateSpace": "device_pixels_pm_v2",
            "bounds": {"x": x, "y": y, "width": width, "height": height},
        }

    def _verify_physical_mouse_quiet(self) -> dict[str, Any]:
        self.events.append("physical_mouse_quiet")
        self.physical_mouse_call_count += 1
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
            "historicalActivityConsumed": (
                self.physical_mouse_call_count
                in self.physical_mouse_historical_calls
            ),
            "sampleCount": 3,
        }
        evidence.update(self.physical_mouse_evidence_overrides)
        return evidence

    def _verify_physical_mouse_buttons_released(self) -> dict[str, Any]:
        self.events.append("physical_mouse_buttons_released")
        if self.physical_mouse_release_errors:
            error = self.physical_mouse_release_errors.pop(0)
            if error is not None:
                raise error
        evidence: dict[str, Any] = {
            "schema": "physical_mouse_buttons_released.v1",
            "buttonsUp": True,
            "activityClear": True,
        }
        evidence.update(self.physical_mouse_release_evidence_overrides)
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

    def _camera_hold(self, direction: str, hold_millis: int) -> dict[str, Any]:
        self.camera_holds.append((direction, hold_millis))
        self.events.append(f"camera_hold:{direction},{hold_millis}")
        self._record("CAMERA_HOLD")
        return {
            "direction": direction,
            "requestedDurationMs": hold_millis,
            "appliedDurationMs": hold_millis,
        }

    def _wheel(self, amount: int) -> dict[str, Any]:
        self.wheel_amounts.append(amount)
        self.events.append(f"wheel:{amount}")
        self._record("WHEEL")
        return {"requestedAmount": amount, "appliedAmount": amount}

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
        overrides = self.status_overrides
        if self.status_overrides_sequence:
            overrides = self.status_overrides_sequence.pop(0)
        return {
            "armed": self.armed,
            "keysDown": 0,
            "mouseButtonsDown": 0,
            **overrides,
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
VIEWPORT = ScreenBounds(-16, -16, 132, 132)
REACQUISITION_CANVAS = ScreenBounds(100, 100, 200, 200)
REACQUISITION_SAFE = gameplay_pointer_safe_bounds(REACQUISITION_CANVAS)
REACQUISITION_OUTER = ScreenBounds(80, 80, 240, 240)
VIRTUAL_DESKTOP = ScreenBounds(0, 0, 400, 400)


def viewport_around_safe(bounds: ScreenBounds) -> ScreenBounds:
    return ScreenBounds(
        bounds.x - 16,
        bounds.y - 16,
        bounds.width + 32,
        bounds.height + 32,
    )


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
        canvas_bounds=VIEWPORT,
        viewport_bounds=VIEWPORT,
    )


def reacquisition_intent(
    *,
    intent_id: str = "external-cursor",
    purpose: InputPurpose = InputPurpose.GAMEPLAY_OBJECT,
) -> ApprovedPointerIntent:
    return ApprovedPointerIntent(
        intent_id=intent_id,
        purpose=purpose,
        target=ScreenPoint(150, 150),
        movement_bounds=REACQUISITION_SAFE,
        target_bounds=ScreenBounds(147, 147, 7, 7),
        expected_pid=321,
        expected_hwnd=77,
        reacquisition_bounds=REACQUISITION_OUTER,
        canvas_bounds=REACQUISITION_CANVAS,
        viewport_bounds=REACQUISITION_CANVAS,
    )


def camera_hold_intent() -> ApprovedCameraHoldIntent:
    return ApprovedCameraHoldIntent(
        intent_id="camera-hold-1",
        purpose=InputPurpose.CAMERA_HOLD,
        direction="right",
        expected_pid=321,
        hold_millis=600,
        before_yaw=2_000,
        before_pitch=900,
        before_zoom=300,
        source_geometry_frame_id="camera-frame-1",
    )


def camera_zoom_intent() -> ApprovedCameraZoomIntent:
    return ApprovedCameraZoomIntent(
        intent_id="camera-zoom-1",
        purpose=InputPurpose.CAMERA_ZOOM,
        amount=1,
        expected_pid=321,
        expected_hwnd=77,
        expected_outer_bounds=REACQUISITION_OUTER,
        expected_native_outer_bounds=REACQUISITION_OUTER,
        expected_native_client_bounds=REACQUISITION_CANVAS,
        canvas_bounds=REACQUISITION_CANVAS,
        viewport_bounds=REACQUISITION_CANVAS,
        pointer_safe_bounds=REACQUISITION_SAFE,
        before_yaw=2_000,
        before_pitch=900,
        before_zoom=300,
        source_geometry_frame_id="camera-frame-1",
    )


def cursor_recovery_intent(
    *, recovery_id: str = "cursor-recovery-1"
) -> ApprovedCursorRecoveryIntent:
    native_client = ScreenBounds(90, 90, 220, 220)
    return ApprovedCursorRecoveryIntent(
        recovery_id=recovery_id,
        expected_pid=321,
        expected_hwnd=77,
        expected_outer_bounds=REACQUISITION_OUTER,
        expected_native_outer_bounds=REACQUISITION_OUTER,
        expected_native_client_bounds=native_client,
        canvas_bounds=REACQUISITION_CANVAS,
        viewport_bounds=REACQUISITION_CANVAS,
        pointer_safe_bounds=REACQUISITION_SAFE,
    )


def coordinator(backend: FakeBackend) -> InputCoordinator:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    return InputCoordinator(
        lambda: backend,
        sleep=sleep,
        monotonic=lambda: now[0],
        pointer_timestep_seconds=0.02,
    )


class InputCoordinatorTests(unittest.TestCase):
    def assert_complete_cleanup(self, receipt: InputReceipt) -> None:
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status_acknowledged)
        self.assertIsNotNone(receipt.firmware_status)
        assert receipt.firmware_status is not None
        self.assertFalse(receipt.firmware_status.armed)
        self.assertEqual(0, receipt.firmware_status.keys_down)
        self.assertEqual(0, receipt.firmware_status.mouse_buttons_down)
        self.assertEqual(0, receipt.unresolved_command_count)
        self.assertEqual(0, receipt.failed_command_count)
        self.assertEqual(0, receipt.ack_missing_count)
        self.assertTrue(receipt.ledger_complete)
        self.assertTrue(receipt.ledger_closed)
        self.assertTrue(receipt.backend_closed)
        commands = [command.command for command in receipt.commands]
        stop_index = commands.index("STOP_ALL")
        disarm_index = commands.index("DISARM", stop_index + 1)
        status_index = commands.index("STATUS", disarm_index + 1)
        self.assertLess(stop_index, disarm_index)
        self.assertLess(disarm_index, status_index)
        self.assertEqual(status_index, len(commands) - 1)

    def assert_safe_cursor_reacquisition(
        self,
        receipt: InputReceipt,
        backend: FakeBackend,
        *,
        cursor_before: tuple[int, int],
    ) -> None:
        self.assertEqual("BLOCKED", receipt.status)
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertEqual("cursor_reacquired_reobserve_required", receipt.reason)
        self.assertTrue(receipt.connected)
        self.assertTrue(receipt.arm_acknowledged)
        self.assertFalse(receipt.successful)
        self.assertFalse(receipt.safely_unsent)
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)
        self.assertEqual(0, receipt.unresolved_command_count)
        self.assertEqual(0, receipt.failed_command_count)
        self.assertEqual(0, receipt.ack_missing_count)
        self.assertTrue(receipt.ledger_closed)
        self.assertTrue(receipt.backend_closed)
        self.assertIsNone(receipt.observability.wait_state)
        self.assertNotIn(
            WaitState.ARDUINO_COMMAND_FAILED,
            receipt.observability.observed_wait_states,
        )
        self.assertFalse(any(
            command.command in {"MOUSE_DOWN", "MOUSE_UP", "KEY_PRESS"}
            for command in receipt.commands
        ))
        self.assertIn("MOVE", [command.command for command in receipt.commands])
        evidence = receipt.cursor_reacquisition
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertTrue(evidence.completed)
        self.assertTrue(evidence.no_activation_sent)
        self.assertTrue(evidence.geometry_unchanged)
        self.assertEqual(ScreenPoint(*cursor_before), evidence.cursor_before)
        self.assertEqual(evidence.before_geometry, evidence.after_geometry)
        self.assertIsNotNone(evidence.cursor_after)
        assert evidence.cursor_after is not None
        self.assertTrue(evidence.neutral_bounds.contains(evidence.cursor_after))
        self.assertEqual(evidence.cursor_after, ScreenPoint(*backend.position))
        self.assertIn("close", backend.events)

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
                canvas_bounds=VIEWPORT,
                viewport_bounds=VIEWPORT,
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

    def test_pointer_motion_metadata_is_bounded_and_validated(self) -> None:
        intent = replace(
            pointer_intent(),
            motion_seed="  run-seed-7  ",
            motion_decision_id="decision-19",
            motion_context="precise_object",
        )

        self.assertEqual("run-seed-7", intent.motion_seed)
        self.assertEqual("decision-19", intent.motion_decision_id)
        self.assertEqual("precise_object", intent.motion_context)
        shaped = replace(
            intent,
            motion_target_bounds=ScreenBounds(10, 8, 20, 18),
        )
        self.assertTrue(shaped.motion_target_bounds.contains(shaped.target))
        with self.assertRaisesRegex(ValueError, "contained by movement_bounds"):
            replace(intent, motion_target_bounds=ScreenBounds(-1, 0, 20, 20))
        with self.assertRaisesRegex(ValueError, "inside motion_target_bounds"):
            replace(intent, motion_target_bounds=ScreenBounds(0, 0, 5, 5))
        with self.assertRaises(TypeError):
            replace(intent, motion_seed=True)
        with self.assertRaises(ValueError):
            replace(intent, motion_decision_id="")
        with self.assertRaises(ValueError):
            replace(intent, motion_context=" ")

    def test_builtin_seeded_planner_is_reproducible_and_receipt_is_bounded(self) -> None:
        bounds = ScreenBounds(0, 0, 1000, 800)
        intent = ApprovedPointerIntent(
            intent_id="seeded-long-pointer",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(500, 400),
            movement_bounds=gameplay_pointer_safe_bounds(bounds),
            target_bounds=ScreenBounds(497, 397, 7, 7),
            expected_pid=321,
            canvas_bounds=bounds,
            viewport_bounds=bounds,
            motion_seed=5,
            motion_decision_id="tree-activation-5",
            motion_context="broad_walk",
        )

        def execute_once() -> InputReceipt:
            backend = FakeBackend(
                start=(100, 100),
                device_pixel_scale=1.75,
            )
            now = [0.0]

            def sleep(seconds: float) -> None:
                now[0] += seconds

            receipt = InputCoordinator(
                lambda: backend,
                sleep=sleep,
                monotonic=lambda: now[0],
                pointer_timestep_seconds=0.02,
            ).execute_pointer(
                intent,
                validate=lambda _intent, _actual: InputValidation.allow(),
            )
            self.assertTrue(receipt.successful)
            return receipt

        first = execute_once()
        second = execute_once()
        evidence = first.pointer_motion

        self.assertEqual(evidence, second.pointer_motion)
        self.assertGreater(evidence.plan_count, 0)
        self.assertGreater(evidence.executed_step_count, 0)
        self.assertGreaterEqual(
            evidence.planned_step_count,
            evidence.executed_step_count,
        )
        self.assertEqual("5", evidence.seed)
        self.assertEqual("tree-activation-5", evidence.decision_id)
        self.assertEqual("broad_walk", evidence.context)
        self.assertEqual("cubic_bezier", evidence.style)
        self.assertEqual(2, len(evidence.control_points))
        self.assertEqual(ScreenPoint(100, 100), evidence.requested_start)
        self.assertEqual(intent.target, evidence.requested_target)
        self.assertIsNotNone(evidence.settled_target)
        assert evidence.settled_target is not None
        self.assertTrue(intent.target_bounds.contains(evidence.settled_target))
        payload = first.to_dict()["pointerMotion"]
        self.assertEqual("5", payload["seed"])
        self.assertEqual("tree-activation-5", payload["decisionId"])
        self.assertEqual(2, len(payload["controlPoints"]))
        self.assertEqual(
            {
                "dx": evidence.settled_target.x - evidence.last_planned_target.x,
                "dy": evidence.settled_target.y - evidence.last_planned_target.y,
            },
            payload["settledCorrection"],
        )
        self.assertEqual(1, payload["intentionalLegCount"])
        self.assertEqual(
            evidence.plan_count - 1,
            payload["correctionPlanCount"],
        )

    def test_all_safe_inset_edges_and_corners_contain_linear_and_curved_paths(self) -> None:
        targets = (
            ScreenPoint(0, 0),
            ScreenPoint(99, 0),
            ScreenPoint(0, 99),
            ScreenPoint(99, 99),
            ScreenPoint(0, 50),
            ScreenPoint(99, 50),
            ScreenPoint(50, 0),
            ScreenPoint(50, 99),
        )
        styles: set[str] = set()
        for index, target in enumerate(targets):
            for style in ("linear", "curved"):
                with self.subTest(target=target, style=style):
                    backend = FakeBackend(start=(50, 50))
                    seeded = style == "curved"
                    intent = replace(
                        pointer_intent(
                            intent_id=f"safe-perimeter-{index}-{style}",
                            target=target,
                            target_bounds=ScreenBounds(target.x, target.y, 1, 1),
                        ),
                        motion_seed=index + 100 if seeded else None,
                        motion_decision_id=(
                            f"safe-perimeter-decision-{index}" if seeded else None
                        ),
                    )

                    receipt = coordinator(backend).execute_pointer(
                        intent,
                        validate=lambda _intent, _actual: InputValidation.allow(),
                    )

                    self.assertTrue(receipt.successful, receipt.reason)
                    self.assertEqual(1, receipt.pointer_motion.plan_count)
                    self.assertEqual(0, receipt.pointer_motion.correction_plan_count)
                    self.assertTrue(BOUNDS.contains(receipt.pointer_motion.settled_target))
                    self.assertTrue(
                        all(BOUNDS.contains(ScreenPoint(*point)) for point in backend.positions)
                    )
                    self.assertTrue(
                        all(BOUNDS.contains(sample.point) for sample in receipt.cursor_samples)
                    )
                    self.assertEqual(
                        [sample.to_dict() for sample in receipt.cursor_samples],
                        receipt.to_dict()["cursorSamples"],
                    )
                    self.assertTrue(
                        all(BOUNDS.contains(point) for point in receipt.pointer_motion.control_points)
                    )
                    self.assertEqual(
                        "cubic_bezier" if seeded else "linear",
                        receipt.pointer_motion.style,
                    )
                    styles.add(receipt.pointer_motion.style or "")
        self.assertIn("linear", styles)
        self.assertIn("cubic_bezier", styles)

    def test_cursor_reacquisition_lane_is_movement_only_with_separate_budget(self) -> None:
        backend = FakeBackend(
            start=(20, 200),
            virtual_desktop_bounds=(0, 0, 400, 400),
            device_pixel_scale=0.5,
        )
        coordinator_under_test = InputCoordinator(
            lambda: backend,
            sleep=lambda _seconds: None,
            pointer_timestep_seconds=0.02,
            max_correction_plans=0,
            max_reacquisition_correction_plans=4,
        )

        receipt = coordinator_under_test.execute_cursor_reacquisition(
            cursor_recovery_intent()
        )

        self.assert_safe_cursor_reacquisition(
            receipt,
            backend,
            cursor_before=(20, 200),
        )
        self.assertIs(
            receipt.cursor_invalidation_cause,
            CursorInvalidationCause.CURSOR_REACQUIRED,
        )
        self.assertGreater(receipt.pointer_motion.correction_plan_count, 0)
        self.assertLessEqual(receipt.pointer_motion.correction_plan_count, 4)
        self.assertFalse(
            any(
                command.command in {"MOUSE_DOWN", "MOUSE_UP", "KEY_PRESS"}
                for command in receipt.commands
            )
        )

        recovery_pointer_intent = ApprovedPointerIntent(
            intent_id="illegal-click-capable-recovery",
            purpose=InputPurpose.CURSOR_REACQUISITION,
            target=REACQUISITION_SAFE.center,
            movement_bounds=REACQUISITION_SAFE,
            target_bounds=ScreenBounds(
                REACQUISITION_SAFE.center.x,
                REACQUISITION_SAFE.center.y,
                1,
                1,
            ),
            expected_pid=321,
            expected_hwnd=77,
            canvas_bounds=REACQUISITION_CANVAS,
            viewport_bounds=REACQUISITION_CANVAS,
        )
        with self.assertRaisesRegex(ValueError, "movement-only"):
            coordinator_under_test.execute_pointer(
                recovery_pointer_intent,
                validate=lambda _intent, _actual: InputValidation.allow(),
            )

    def test_legacy_injected_pointer_planner_keeps_old_signature(self) -> None:
        backend = FakeBackend(start=(10, 10))
        calls: list[tuple[ScreenPoint, ScreenPoint, ScreenBounds]] = []

        def legacy_planner(
            start: ScreenPoint,
            target: ScreenPoint,
            bounds: ScreenBounds,
            *,
            timestep_seconds: float,
            limits: object,
        ):
            calls.append((start, target, bounds))
            return plan_pointer_motion(
                start,
                target,
                bounds,
                timestep_seconds=timestep_seconds,
                limits=limits,  # type: ignore[arg-type]
            )

        intent = replace(
            pointer_intent(),
            motion_seed=77,
            motion_decision_id="legacy-compatible",
            motion_context="object",
        )
        receipt = InputCoordinator(
            lambda: backend,
            pointer_planner=legacy_planner,
            sleep=lambda _seconds: None,
            pointer_timestep_seconds=0.02,
        ).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertTrue(calls)
        self.assertEqual("linear", receipt.pointer_motion.style)
        self.assertEqual("77", receipt.pointer_motion.seed)
        self.assertEqual("legacy-compatible", receipt.pointer_motion.decision_id)

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

    def test_cursor_moving_before_first_move_requires_fresh_reobservation(self) -> None:
        backend = FakeBackend(
            start=(20, 20),
            position_samples=[(20, 20), (21, 20)],
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(
                target=ScreenPoint(70, 70),
                target_bounds=ScreenBounds(67, 67, 7, 7),
            ),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertIn("cursor_changed_before_pointer_motion", receipt.reason)
        self.assertFalse(any(
            event.startswith(("move:", "mouse_down:"))
            for event in backend.events
        ))
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_late_cursor_change_after_quiescence_blocks_before_first_move(self) -> None:
        backend = FakeBackend(
            start=(20, 20),
            position_samples=[(20, 20), (20, 20), (21, 20)],
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(
                target=ScreenPoint(70, 70),
                target_bounds=ScreenBounds(67, 67, 7, 7),
            ),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertIn("cursor_changed_before_pointer_motion", receipt.reason)
        self.assertFalse(any(
            event.startswith(("move:", "mouse_down:"))
            for event in backend.events
        ))
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_physical_mouse_activity_blocks_before_serial_connect(self) -> None:
        backend = FakeBackend(
            physical_mouse_errors=[
                ArduinoHIDError(
                    "physical mouse button held or pressed during pointer preflight"
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

    def test_button_activity_during_cursor_quiescence_blocks_before_move(self) -> None:
        backend = FakeBackend(
            physical_mouse_historical_calls={3},
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertFalse(receipt.safely_unsent)
        self.assertIn(
            "physical_mouse_activity_since_prior_proof_after_pointer_quiescence",
            receipt.reason,
        )
        self.assertFalse(any(
            event.startswith(("move:", "mouse_down:"))
            for event in backend.events
        ))
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_button_activity_after_preflight_blocks_before_first_move(self) -> None:
        backend = FakeBackend(
            physical_mouse_historical_calls={2},
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn(
            "physical_mouse_activity_since_prior_proof_before_pointer_motion",
            receipt.reason,
        )
        self.assertFalse(any(
            event.startswith(("move:", "mouse_down:"))
            for event in backend.events
        ))
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_post_recovery_pinned_retry_rejects_historical_physical_activity(self) -> None:
        outer = ScreenBounds(-30, -30, 180, 180)
        backend = FakeBackend(physical_mouse_historical_calls={1})
        intent = replace(
            pointer_intent(intent_id="post-recovery-physical-activity"),
            expected_hwnd=77,
            reacquisition_bounds=outer,
            expected_native_outer_bounds=outer,
            expected_native_client_bounds=VIEWPORT,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIs(
            receipt.cursor_invalidation_cause,
            CursorInvalidationCause.PHYSICAL_INPUT_ACTIVITY,
        )
        self.assertIn(
            "physical_mouse_activity_since_prior_proof_pointer_preflight",
            receipt.reason,
        )
        self.assertNotIn("connect", backend.events)
        self.assertEqual(0, backend.move_call_count)
        self.assertFalse(
            any(
                event.startswith(("mouse_down:", "mouse_up:", "press:"))
                for event in backend.events
            )
        )
    def test_physical_mouse_activity_during_transaction_blocks_activation(self) -> None:
        backend = FakeBackend(
            physical_mouse_errors=[
                None,
                None,
                None,
                ArduinoHIDError(
                    "physical mouse button held or pressed during pointer transaction"
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

    def test_cursor_already_inside_canvas_skips_reacquisition(self) -> None:
        backend = FakeBackend(
            start=(150, 150),
            virtual_desktop_bounds=(0, 0, 400, 400),
        )
        validated: list[ScreenPoint] = []

        receipt = coordinator(backend).execute_pointer(
            reacquisition_intent(intent_id="already-inside"),
            validate=lambda _intent, actual: (
                validated.append(actual) or InputValidation.allow()
            ),
        )

        self.assertTrue(receipt.successful)
        self.assertIsNone(receipt.cursor_reacquisition)
        self.assertEqual([ScreenPoint(150, 150)], validated)
        self.assertNotIn("virtual_desktop", backend.events)
        self.assertFalse(any(event.startswith("move:") for event in backend.events))
        self.assertIn("mouse_down:left", backend.events)

    def test_cursor_immediately_outside_each_edge_keeps_geometry_unchanged(self) -> None:
        cases = {
            "left": (99, 200),
            "right": (300, 200),
            "top": (200, 99),
            "bottom": (200, 300),
        }
        for label, start in cases.items():
            with self.subTest(edge=label):
                backend = FakeBackend(
                    start=start,
                    virtual_desktop_bounds=(0, 0, 400, 400),
                )
                validated: list[ScreenPoint] = []

                receipt = coordinator(backend).execute_pointer(
                    reacquisition_intent(intent_id=f"outside-{label}"),
                    validate=lambda _intent, actual: (
                        validated.append(actual) or InputValidation.allow()
                    ),
                )

                self.assert_safe_cursor_reacquisition(
                    receipt, backend, cursor_before=start
                )
                self.assertEqual([], validated)
                self.assertGreater(
                    sum(event.startswith("move:") for event in backend.events),
                    0,
                )

    def test_successful_no_click_reacquisition_crosses_foreign_surface(self) -> None:
        start = (20, 200)
        backend = FakeBackend(
            start=start,
            virtual_desktop_bounds=(0, 0, 400, 400),
        )
        validator_calls = 0

        def stale_validator(
            _intent: ApprovedPointerIntent,
            _actual: ScreenPoint,
        ) -> InputValidation:
            nonlocal validator_calls
            validator_calls += 1
            return InputValidation.allow()

        receipt = coordinator(backend).execute_pointer(
            reacquisition_intent(intent_id="foreign-surface"),
            validate=stale_validator,
        )

        self.assert_safe_cursor_reacquisition(
            receipt, backend, cursor_before=start
        )
        self.assertEqual(0, validator_calls)
        self.assertTrue(any(
            not REACQUISITION_OUTER.contains(ScreenPoint(*point))
            for point in backend.positions
        ))
        self.assertTrue(all(
            VIRTUAL_DESKTOP.contains(ScreenPoint(*point))
            for point in backend.positions
        ))
        entered_canvas = False
        for point in map(lambda value: ScreenPoint(*value), backend.positions):
            if REACQUISITION_CANVAS.contains(point):
                entered_canvas = True
            elif entered_canvas:
                self.fail(f"cursor left canvas after entry at {point}")
        self.assertTrue(entered_canvas)
        owner_samples = [
            event for event in backend.events if event.startswith("point_owner:")
        ]
        self.assertTrue(owner_samples)
        for event in owner_samples:
            x_text, y_text = event.split(":", 1)[1].split(",", 1)
            self.assertTrue(
                REACQUISITION_CANVAS.contains(
                    ScreenPoint(int(x_text), int(y_text))
                )
            )

    def test_cursor_reacquisition_lease_contention_is_safely_unsent(self) -> None:
        backend = FakeBackend(
            start=(20, 200),
            input_lease_error=ArduinoHIDError(
                "Arduino serial port COM6 is already owned"
            ),
        )

        receipt = coordinator(backend).execute_pointer(
            reacquisition_intent(intent_id="contended-reacquisition"),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.safely_unsent)
        self.assertFalse(receipt.connected)
        self.assertEqual((), receipt.commands)
        self.assertIn("already owned", receipt.reason)
        self.assertIn("input_lease", backend.events)
        self.assertNotIn("virtual_desktop", backend.events)
        self.assertNotIn("connect", backend.events)
        self.assertFalse(any(event.startswith("move:") for event in backend.events))
        self.assertTrue(receipt.ledger_closed)
        self.assertTrue(receipt.backend_closed)
        self.assertIsNone(receipt.observability.wait_state)
        self.assertNotIn(
            WaitState.ARDUINO_COMMAND_FAILED,
            receipt.observability.observed_wait_states,
        )

    def test_cursor_reacquisition_requires_runelite_foreground(self) -> None:
        backend = FakeBackend(
            start=(20, 200),
            foreground_errors=[ArduinoHIDError("RuneLite is not foreground")],
        )

        receipt = coordinator(backend).execute_pointer(
            reacquisition_intent(intent_id="foreground-required"),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertTrue(receipt.safely_unsent)
        self.assertIn("runelite_foreground_required_for_cursor_recovery", receipt.reason)
        self.assertIn("not foreground", receipt.reason)
        self.assertNotIn("connect", backend.events)
        self.assertFalse(any(event.startswith("move:") for event in backend.events))

    def test_cursor_reacquisition_rejects_cursor_still_moving(self) -> None:
        start = (20, 200)
        backend = FakeBackend(
            start=start,
            position_samples=[start, (21, 200)],
        )

        receipt = coordinator(backend).execute_pointer(
            reacquisition_intent(intent_id="cursor-still-moving"),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertTrue(receipt.safely_unsent)
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertIn("cursor_reacquisition_not_stationary_before_connect", receipt.reason)
        self.assertNotIn("connect", backend.events)
        self.assertFalse(any(event.startswith("move:") for event in backend.events))

    def test_physical_button_held_during_reacquisition_cleans_up(self) -> None:
        backend = FakeBackend(
            start=(20, 200),
            virtual_desktop_bounds=(0, 0, 400, 400),
            physical_mouse_release_errors=[
                None,
                None,
                None,
                ArduinoHIDError("physical mouse button held"),
            ],
        )

        receipt = coordinator(backend).execute_pointer(
            reacquisition_intent(intent_id="held-button-mid-move"),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertIn("physical_mouse_buttons_not_released", receipt.reason)
        self.assertGreater(
            sum(event.startswith("move:") for event in backend.events), 0
        )
        self.assertFalse(any(
            command.command in {"MOUSE_DOWN", "MOUSE_UP", "KEY_PRESS"}
            for command in receipt.commands
        ))
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)
        self.assertEqual(0, receipt.unresolved_command_count)

    def test_geometry_drift_during_reacquisition_fails_with_safe_cleanup(self) -> None:
        drift = {
            "actualOuterBounds": {
                "x": 81,
                "y": 80,
                "width": 240,
                "height": 240,
            },
            "outerMatches": False,
        }
        backend = FakeBackend(
            start=(20, 200),
            virtual_desktop_bounds=(0, 0, 400, 400),
            window_geometry_evidence_sequence=[{}, {}, {}, {}, drift],
        )

        receipt = coordinator(backend).execute_pointer(
            reacquisition_intent(intent_id="geometry-drift"),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertIn("geometry_changed_reobserve_required", receipt.reason)
        self.assertGreater(
            sum(event.startswith("move:") for event in backend.events), 0
        )
        self.assertFalse(any(
            command.command in {"MOUSE_DOWN", "MOUSE_UP", "KEY_PRESS"}
            for command in receipt.commands
        ))
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)
        self.assertTrue(receipt.ledger_closed)
        self.assertTrue(receipt.backend_closed)

    def test_completed_reacquisition_survives_cleanup_and_close_failures(self) -> None:
        cases: tuple[tuple[str, dict[str, Any], str], ...] = (
            (
                "stop-all",
                {"fail_commands": {"STOP_ALL"}},
                "stop_all_failed",
            ),
            (
                "disarm",
                {"fail_commands": {"DISARM"}},
                "disarm_failed",
            ),
            (
                "unsafe-final-status",
                {
                    "status_overrides_sequence": [
                        {},
                        {},
                        {"armed": True},
                    ]
                },
                "firmware_status_not_safe",
            ),
            (
                "ledger-close",
                {"end_ledger_fails": True},
                "command_ledger_close_failed",
            ),
            (
                "backend-close",
                {"close_fails": True},
                "backend_close_failed",
            ),
        )
        for label, backend_kwargs, expected_error in cases:
            with self.subTest(failure=label):
                backend = FakeBackend(
                    start=(20, 200),
                    virtual_desktop_bounds=(0, 0, 400, 400),
                    **backend_kwargs,
                )

                receipt = coordinator(backend).execute_pointer(
                    reacquisition_intent(intent_id=f"cleanup-{label}"),
                    validate=lambda _intent, _actual: InputValidation.allow(),
                )

                self.assertEqual("ERROR", receipt.status)
                self.assertEqual(
                    "cursor_reacquired_reobserve_required",
                    receipt.reason,
                )
                self.assertIs(
                    receipt.failure_kind,
                    InputFailureKind.CURSOR_STATE_INVALIDATED,
                )
                evidence = receipt.cursor_reacquisition
                self.assertIsNotNone(evidence)
                assert evidence is not None
                self.assertTrue(evidence.completed)
                self.assertTrue(evidence.no_activation_sent)
                self.assertTrue(evidence.geometry_unchanged)
                self.assertEqual(
                    evidence.before_geometry,
                    evidence.after_geometry,
                )
                self.assertFalse(any(
                    command.command in {"MOUSE_DOWN", "MOUSE_UP", "KEY_PRESS"}
                    for command in receipt.commands
                ))
                self.assertIn(
                    expected_error,
                    " | ".join(receipt.errors),
                )
                self.assertIn("stop_all", backend.events)
                self.assertIn("disarm", backend.events)
                self.assertIn("firmware_status", backend.events)
                self.assertIn("ledger_end", backend.events)
                self.assertIn("close", backend.events)
                with self.assertRaises(FrozenInstanceError):
                    receipt.status = "PASS"  # type: ignore[misc]

                if label == "stop-all":
                    self.assertFalse(receipt.stop_all_acknowledged)
                    self.assertTrue(receipt.disarm_acknowledged)
                    self.assertEqual(1, receipt.failed_command_count)
                elif label == "disarm":
                    self.assertTrue(receipt.stop_all_acknowledged)
                    self.assertFalse(receipt.disarm_acknowledged)
                    self.assertEqual(1, receipt.failed_command_count)
                elif label == "unsafe-final-status":
                    self.assertTrue(receipt.firmware_status_acknowledged)
                    self.assertIsNotNone(receipt.firmware_status)
                    assert receipt.firmware_status is not None
                    self.assertFalse(receipt.firmware_status.safe)
                elif label == "ledger-close":
                    self.assertFalse(receipt.ledger_complete)
                    self.assertFalse(receipt.ledger_closed)
                    self.assertTrue(receipt.backend_closed)
                else:
                    self.assertTrue(receipt.ledger_complete)
                    self.assertTrue(receipt.ledger_closed)
                    self.assertFalse(receipt.backend_closed)

    def test_stale_client_geometry_blocks_before_serial_or_pointer_motion(self) -> None:
        client = ScreenBounds(90, 90, 220, 220)
        backend = FakeBackend(
            start=(150, 150),
            window_geometry_evidence_overrides={
                "actualOuterBounds": {
                    "x": 92,
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
            movement_bounds=ScreenBounds(116, 116, 168, 168),
            target_bounds=ScreenBounds(157, 157, 7, 7),
            expected_pid=321,
            reacquisition_bounds=client,
            canvas_bounds=ScreenBounds(100, 100, 200, 200),
            viewport_bounds=ScreenBounds(100, 100, 200, 200),
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
            window_geometry_evidence_sequence=[
                {
                    "actualOuterBounds": {
                        "x": 91,
                        "y": 89,
                        "width": 220,
                        "height": 220,
                    },
                    "outerMatches": False,
                },
                {},
            ],
        )
        intent = ApprovedPointerIntent(
            intent_id="awt-native-origin-quantization",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(160, 160),
            movement_bounds=ScreenBounds(116, 116, 168, 168),
            target_bounds=ScreenBounds(157, 157, 7, 7),
            expected_pid=321,
            reacquisition_bounds=client,
            canvas_bounds=ScreenBounds(100, 100, 200, 200),
            viewport_bounds=ScreenBounds(100, 100, 200, 200),
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

    def test_post_recovery_pinned_pid_hwnd_and_native_geometry_drift_are_terminal(self) -> None:
        class ChangedPidBackend(FakeBackend):
            def _assert_foreground(
                self,
                allowed_titles: tuple[str, ...] | list[str],
                *,
                expected_pid: int | None = None,
            ) -> dict[str, Any]:
                if self.move_call_count:
                    raise RuntimeError("foreground PID changed")
                return super()._assert_foreground(
                    allowed_titles,
                    expected_pid=expected_pid,
                )

        class ChangedHwndBackend(FakeBackend):
            def _assert_foreground(
                self,
                allowed_titles: tuple[str, ...] | list[str],
                *,
                expected_pid: int | None = None,
            ) -> dict[str, Any]:
                evidence = super()._assert_foreground(
                    allowed_titles,
                    expected_pid=expected_pid,
                )
                if self.move_call_count:
                    evidence = {**evidence, "hwnd": 88}
                return evidence

        class ChangedNativeGeometryBackend(FakeBackend):
            def _verify_window_geometry(self, **kwargs: Any) -> dict[str, Any]:
                evidence = super()._verify_window_geometry(**kwargs)
                if self.move_call_count:
                    client = VIEWPORT
                    evidence.update(
                        actualClientBounds={
                            "x": client.x + 1,
                            "y": client.y,
                            "width": client.width,
                            "height": client.height,
                        },
                        clientMatches=False,
                    )
                return evidence

        outer = ScreenBounds(-30, -30, 180, 180)
        intent = replace(
            pointer_intent(
                intent_id="post-recovery-exact-binding",
                target=ScreenPoint(40, 10),
                target_bounds=ScreenBounds(37, 7, 7, 7),
            ),
            expected_hwnd=77,
            reacquisition_bounds=outer,
            expected_native_outer_bounds=outer,
            expected_native_client_bounds=VIEWPORT,
        )
        cases = (
            ("pid", ChangedPidBackend(), CursorInvalidationCause.IDENTITY_CHANGED),
            ("hwnd", ChangedHwndBackend(), CursorInvalidationCause.IDENTITY_CHANGED),
            (
                "native_geometry",
                ChangedNativeGeometryBackend(),
                CursorInvalidationCause.GEOMETRY_CHANGED,
            ),
        )

        for label, backend, expected_cause in cases:
            with self.subTest(label=label):
                receipt = coordinator(backend).execute_pointer(
                    intent,
                    validate=lambda _intent, _actual: InputValidation.allow(),
                )

                self.assertEqual("BLOCKED", receipt.status)
                self.assertIs(receipt.cursor_invalidation_cause, expected_cause)
                self.assertGreaterEqual(backend.move_call_count, 1)
                self.assertNotIn("mouse_down:left", backend.events)
                self.assertNotIn("mouse_up:left", backend.events)
                self.assert_complete_cleanup(receipt)

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
                    movement_bounds=ScreenBounds(116, 116, 168, 168),
                    target_bounds=ScreenBounds(157, 157, 7, 7),
                    expected_pid=321,
                    reacquisition_bounds=ScreenBounds(90, 90, 220, 220),
                    canvas_bounds=ScreenBounds(100, 100, 200, 200),
                    viewport_bounds=ScreenBounds(100, 100, 200, 200),
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
            movement_bounds=ScreenBounds(116, 116, 168, 168),
            target_bounds=ScreenBounds(157, 157, 7, 7),
            expected_pid=321,
            reacquisition_bounds=ScreenBounds(90, 90, 220, 220),
            canvas_bounds=ScreenBounds(100, 100, 200, 200),
            viewport_bounds=ScreenBounds(100, 100, 200, 200),
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
        self.assertIs(
            receipt.cursor_invalidation_cause,
            CursorInvalidationCause.POINT_OWNER_MISMATCH,
        )
        self.assertTrue(receipt.cursor_samples)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assert_complete_cleanup(receipt)

    def test_all_four_virtual_desktop_corners_are_reacquired(self) -> None:
        cases = {
            "top-left": (0, 0),
            "top-right": (399, 0),
            "bottom-left": (0, 399),
            "bottom-right": (399, 399),
        }
        for label, start in cases.items():
            with self.subTest(corner=label):
                backend = FakeBackend(
                    start=start,
                    virtual_desktop_bounds=(0, 0, 400, 400),
                )

                receipt = coordinator(backend).execute_pointer(
                    reacquisition_intent(intent_id=f"corner-{label}"),
                    validate=lambda _intent, _actual: InputValidation.allow(),
                )

                self.assert_safe_cursor_reacquisition(
                    receipt, backend, cursor_before=start
                )
                self.assertTrue(all(
                    VIRTUAL_DESKTOP.contains(ScreenPoint(*point))
                    for point in backend.positions
                ))

    def test_foreign_owner_is_tolerated_only_until_canvas_entry(self) -> None:
        backend = FakeBackend(
            start=(20, 200),
            virtual_desktop_bounds=(0, 0, 400, 400),
            point_owner_hwnds=[88],
        )

        receipt = coordinator(backend).execute_pointer(
            reacquisition_intent(intent_id="foreign-owner-until-canvas"),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertIn("point_owner_mismatch", receipt.reason)
        self.assertGreater(
            sum(event.startswith("move:") for event in backend.events), 0
        )
        owner_events = [
            event for event in backend.events if event.startswith("point_owner:")
        ]
        self.assertEqual(1, len(owner_events))
        owner_x, owner_y = owner_events[0].split(":", 1)[1].split(",", 1)
        self.assertTrue(
            REACQUISITION_CANVAS.contains(
                ScreenPoint(int(owner_x), int(owner_y))
            )
        )
        self.assertFalse(any(
            command.command in {"MOUSE_DOWN", "MOUSE_UP", "KEY_PRESS"}
            for command in receipt.commands
        ))
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_foreground_loss_during_reacquisition_stops_and_cleans_up(self) -> None:
        backend = FakeBackend(
            start=(20, 200),
            virtual_desktop_bounds=(0, 0, 400, 400),
            foreground_hwnds=[77] * 9 + [88],
        )

        receipt = coordinator(backend).execute_pointer(
            reacquisition_intent(intent_id="foreground-lost-during-move"),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIn("pointer_foreground_hwnd_mismatch", receipt.reason)
        self.assertGreater(
            sum(event.startswith("move:") for event in backend.events), 0
        )
        self.assertFalse(any(
            command.command in {"MOUSE_DOWN", "MOUSE_UP", "KEY_PRESS"}
            for command in receipt.commands
        ))
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_delayed_reacquisition_feedback_still_discards_stale_intent(self) -> None:
        start = (305, 150)
        backend = FakeBackend(
            start=start,
            virtual_desktop_bounds=(0, 0, 400, 400),
            delayed_x_move_indices={1},
            release_delayed_x_on_position_call=14,
        )
        validator_calls = 0

        def stale_validator(
            _intent: ApprovedPointerIntent,
            _actual: ScreenPoint,
        ) -> InputValidation:
            nonlocal validator_calls
            validator_calls += 1
            return InputValidation.allow()

        receipt = coordinator(backend).execute_pointer(
            reacquisition_intent(intent_id="delayed-reacquisition"),
            validate=stale_validator,
        )

        self.assert_safe_cursor_reacquisition(
            receipt, backend, cursor_before=start
        )
        self.assertEqual(0, validator_calls)
        self.assertEqual(1, receipt.cursor_feedback.wait_count)
        self.assertEqual(1, receipt.cursor_feedback.settled_count)

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
        commands = [command.command for command in receipt.commands]
        self.assertEqual("ARM", commands[0])
        self.assertEqual(
            ["MOUSE_DOWN", "MOUSE_UP", "STOP_ALL", "DISARM", "STATUS"],
            commands[-5:],
        )
        self.assertEqual(1, commands.count("MOVE"))
        self.assertEqual(1, receipt.pointer_motion.plan_count)
        self.assertEqual(0, receipt.pointer_motion.correction_plan_count)
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
        open_intent = replace(
            pointer_intent(
                intent_id="context-open",
                purpose=InputPurpose.CONTEXT_MENU,
                target=ScreenPoint(12, 10),
                button=MouseButton.RIGHT,
            ),
            motion_seed=111,
            motion_decision_id="open-decision",
            motion_context="context_open",
        )
        row_intent = replace(
            pointer_intent(
                intent_id="context-row",
                purpose=InputPurpose.CONTEXT_ROW,
                target=ScreenPoint(12, 12),
            ),
            motion_seed=222,
            motion_decision_id="row-decision",
            motion_context="context_row",
        )

        def resolve_row() -> ApprovedPointerIntent:
            backend.events.append("resolve_row")
            return row_intent

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
        pointer = receipt.pointer_motion
        payload = receipt.to_dict()["pointerMotion"]
        self.assertEqual(2, payload["intentionalLegCount"])
        self.assertEqual(
            pointer.plan_count - 2,
            payload["correctionPlanCount"],
        )
        self.assertEqual(
            {
                "dx": pointer.settled_target.x - pointer.last_planned_target.x,
                "dy": pointer.settled_target.y - pointer.last_planned_target.y,
            },
            payload["settledCorrection"],
        )
        self.assertEqual(row_intent.target, pointer.requested_target)
        self.assertEqual("context_row", pointer.context)
        self.assertEqual("222", pointer.seed)
        self.assertEqual("row-decision", pointer.decision_id)
        self.assertEqual(backend.events.count("connect"), 1)
        self.assertEqual(backend.events.count("arm"), 1)
        self.assertFalse(receipt.context_cancel_attempted)
        self.assertLess(backend.events.index("hover_validator"), backend.events.index("mouse_down:right"))
        self.assertLess(backend.events.index("mouse_up:right"), backend.events.index("resolve_row"))
        self.assertLess(backend.events.index("row_validator"), backend.events.index("mouse_down:left"))

    def test_context_receipt_preserves_delayed_feedback_diagnostics(self) -> None:
        backend = FakeBackend(
            delayed_x_move_indices={1},
            release_delayed_x_on_position_call=13,
        )
        open_intent = pointer_intent(
            intent_id="context-open-delayed",
            purpose=InputPurpose.CONTEXT_MENU,
            target=ScreenPoint(11, 10),
            button=MouseButton.RIGHT,
        )

        receipt = coordinator(backend).execute_context_menu(
            open_intent,
            validate_hover=lambda _intent, _actual: InputValidation.allow(),
            resolve_row=lambda: pointer_intent(
                intent_id="context-row-delayed",
                purpose=InputPurpose.CONTEXT_ROW,
                target=ScreenPoint(11, 12),
            ),
            validate_row=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful, receipt.reason)
        self.assertEqual(
            ("context-open-delayed", "context-row-delayed"),
            receipt.intent_ids,
        )
        self.assertEqual(1, receipt.cursor_feedback.wait_count)
        self.assertEqual(1, receipt.cursor_feedback.settled_count)
        self.assertEqual(10, receipt.cursor_feedback.max_extra_polls)
        self.assertEqual("settled", receipt.cursor_feedback.last_wait.outcome)
        self.assertEqual(
            receipt.cursor_feedback.to_dict(),
            receipt.to_dict()["cursorFeedback"],
        )

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
        self.assertIs(
            receipt.observability.wait_state,
            WaitState.ARDUINO_COMMAND_FAILED,
        )
        self.assertIn(
            WaitState.ARDUINO_COMMAND_FAILED,
            receipt.observability.observed_wait_states,
        )
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
            [command.command for command in receipt.commands].count("MOVE"), 2
        )
        self.assertLessEqual(receipt.pointer_motion.correction_plan_count, 1)

    def test_initial_delayed_axis_settles_without_a_second_move(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            delayed_x_move_indices={1},
            release_delayed_x_on_position_call=5,
        )
        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 51)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful, receipt.reason)
        self.assertEqual(
            ["move:1,1"],
            [event for event in backend.events if event.startswith("move:")],
        )
        self.assertIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_report_at_arrival_deadline_stabilizes_before_total_deadline(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            delayed_x_move_indices={1},
            release_delayed_x_on_position_call=13,
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 50)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful, receipt.reason)
        self.assertEqual(
            ["move:1,0"],
            [event for event in backend.events if event.startswith("move:")],
        )
        evidence = receipt.cursor_feedback
        self.assertEqual(1, evidence.wait_count)
        self.assertEqual(1, evidence.settled_count)
        self.assertEqual(10, evidence.max_extra_polls)
        self.assertEqual(240, evidence.max_elapsed_millis)
        self.assertIsNotNone(evidence.last_wait)
        assert evidence.last_wait is not None
        self.assertEqual("settled", evidence.last_wait.outcome)
        self.assertEqual(200, evidence.last_wait.complete_effect_millis)
        self.assertEqual(240, evidence.last_wait.elapsed_millis)
        self.assertIn("mouse_down:left", backend.events)

    def test_report_after_arrival_deadline_cannot_qualify(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            delayed_x_move_indices={1},
            release_delayed_x_on_position_call=14,
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 50)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertEqual("BLOCKED", receipt.status)
        self.assertIn("cursor_feedback_move_effect_unresolved", receipt.reason)
        self.assertIn("elapsed_ms=200", receipt.reason)
        self.assertEqual(1, backend.move_call_count)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertEqual(1, receipt.cursor_feedback.wait_count)
        self.assertEqual(0, receipt.cursor_feedback.settled_count)
        self.assertEqual(
            "effect_unresolved",
            receipt.cursor_feedback.last_wait.outcome,
        )

    def test_slow_ack_cannot_bypass_absolute_effect_deadline(self) -> None:
        now = [0.0]

        class SlowAckBackend(FakeBackend):
            def _move_relative(self, dx: int, dy: int) -> dict[str, Any]:
                result = super()._move_relative(dx, dy)
                now[0] += 0.25
                return result

        backend = SlowAckBackend(start=(50, 50))

        def sleep(seconds: float) -> None:
            now[0] += seconds

        receipt = InputCoordinator(
            lambda: backend,
            sleep=sleep,
            monotonic=lambda: now[0],
            pointer_timestep_seconds=0.02,
        ).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 50)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIn(
            "cursor_feedback_move_effect_not_observed_by_arrival_deadline",
            receipt.reason,
        )
        self.assertEqual(270, receipt.cursor_feedback.max_elapsed_millis)
        self.assertEqual("rejected", receipt.cursor_feedback.last_wait.outcome)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_reacquisition_slow_ack_cannot_bypass_effect_deadline(self) -> None:
        now = [0.0]

        class SlowAckBackend(FakeBackend):
            def _move_relative(self, dx: int, dy: int) -> dict[str, Any]:
                result = super()._move_relative(dx, dy)
                now[0] += 0.25
                return result

        backend = SlowAckBackend(
            start=(3421, 1594),
            device_pixel_scale=2.25,
        )
        intent = ApprovedPointerIntent(
            intent_id="login-slow-ack-reacquire",
            purpose=InputPurpose.LOGIN_PROMPT,
            target=ScreenPoint(2300, 1281),
            movement_bounds=ScreenBounds(1191, 472, 2219, 1573),
            target_bounds=ScreenBounds(2220, 1230, 160, 102),
            expected_pid=321,
            reacquisition_bounds=ScreenBounds(1167, 460, 2267, 1609),
        )

        def sleep(seconds: float) -> None:
            now[0] += seconds

        receipt = InputCoordinator(
            lambda: backend,
            sleep=sleep,
            monotonic=lambda: now[0],
            pointer_timestep_seconds=0.02,
        ).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIn(
            "cursor_feedback_move_effect_not_observed_by_arrival_deadline",
            receipt.reason,
        )
        self.assertEqual(1, backend.move_call_count)
        self.assertNotIn("mouse_down:left", backend.events)

    def test_feedback_sleep_overshoot_aborts_before_another_move(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            delayed_x_move_indices={1},
        )
        now = [0.0]
        sleep_calls = [0]

        def sleep(seconds: float) -> None:
            sleep_calls[0] += 1
            now[0] += seconds + (0.30 if sleep_calls[0] == 4 else 0.0)

        receipt = InputCoordinator(
            lambda: backend,
            sleep=sleep,
            monotonic=lambda: now[0],
            pointer_timestep_seconds=0.02,
        ).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 50)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("cursor_feedback_move_effect_unresolved", receipt.reason)
        self.assertEqual(1, backend.move_call_count)
        self.assertNotIn("mouse_down:left", backend.events)
        event = receipt.cursor_feedback.last_wait
        self.assertIsNotNone(event)
        assert event is not None
        self.assertGreater(event.elapsed_millis, 240)
        self.assertEqual("effect_unresolved", event.outcome)

    def test_effect_first_seen_after_deadline_is_retained_as_rejected(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            delayed_x_move_indices={1},
            release_delayed_x_on_position_call=6,
        )
        now = [0.0]
        sleep_calls = [0]

        def sleep(seconds: float) -> None:
            sleep_calls[0] += 1
            now[0] += seconds + (0.16 if sleep_calls[0] == 4 else 0.0)

        receipt = InputCoordinator(
            lambda: backend,
            sleep=sleep,
            monotonic=lambda: now[0],
            pointer_timestep_seconds=0.02,
        ).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 50)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIn(
            "cursor_feedback_move_effect_not_observed_by_arrival_deadline",
            receipt.reason,
        )
        event = receipt.cursor_feedback.last_wait
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual("rejected", event.outcome)
        self.assertEqual(220, event.complete_effect_millis)
        self.assertEqual(ScreenPoint(51, 50), event.last)
        self.assertNotIn("mouse_down:left", backend.events)

    def test_staggered_two_axis_effect_requires_both_axes_and_stability(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            delayed_x_move_indices={1},
            delayed_y_move_indices={1},
            release_delayed_x_on_position_call=10,
            release_delayed_y_on_position_call=12,
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 51)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful, receipt.reason)
        self.assertEqual(
            ["move:1,1"],
            [event for event in backend.events if event.startswith("move:")],
        )
        event = receipt.cursor_feedback.last_wait
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual("settled", event.outcome)
        self.assertEqual(140, event.first_effect_millis)
        self.assertEqual(180, event.complete_effect_millis)
        self.assertEqual(220, event.elapsed_millis)

    def test_partial_ordinary_sample_retains_true_first_effect_time(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            delayed_y_move_indices={1},
            release_delayed_y_on_position_call=10,
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 51)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful, receipt.reason)
        event = receipt.cursor_feedback.last_wait
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(20, event.first_effect_millis)
        self.assertEqual(140, event.complete_effect_millis)

    def test_unstable_cursor_until_total_deadline_is_retained(self) -> None:
        class UnstableBackend(FakeBackend):
            def _current_position(self) -> tuple[int, int]:
                point = super()._current_position()
                if self.position_call_count in {14, 15}:
                    self.position = (point[0] + 1, point[1])
                    self.positions.append(self.position)
                    return self.position
                return point

        backend = UnstableBackend(
            start=(50, 50),
            delayed_x_move_indices={1},
            release_delayed_x_on_position_call=13,
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 50)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIn("cursor_feedback_move_stability_unresolved", receipt.reason)
        event = receipt.cursor_feedback.last_wait
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual("stability_unresolved", event.outcome)
        self.assertEqual(200, event.complete_effect_millis)
        self.assertEqual(240, event.elapsed_millis)
        self.assertNotIn("mouse_down:left", backend.events)

    def test_extended_feedback_rejects_changed_point_owner(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            delayed_x_move_indices={1},
            point_owner_hwnds=[77, 99],
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 50)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIn("point_owner_mismatch", receipt.reason)
        self.assertIs(
            receipt.cursor_invalidation_cause,
            CursorInvalidationCause.POINT_OWNER_MISMATCH,
        )
        self.assertNotIn("mouse_down:left", backend.events)

    def test_extended_feedback_retains_initial_point_owner_loss(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            delayed_x_move_indices={1},
            point_owner_hwnds=[99],
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 50)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIn("point_owner_mismatch", receipt.reason)
        self.assertIs(
            receipt.cursor_invalidation_cause,
            CursorInvalidationCause.POINT_OWNER_MISMATCH,
        )
        self.assertNotIn("mouse_down:left", backend.events)

    def test_final_feedback_change_retains_offending_cursor_sample(self) -> None:
        class FinalChangeBackend(FakeBackend):
            def _current_position(self) -> tuple[int, int]:
                point = super()._current_position()
                if self.position_call_count == 16:
                    self.position = (point[0] + 1, point[1])
                    self.positions.append(self.position)
                    return self.position
                return point

        backend = FinalChangeBackend(
            start=(50, 50),
            delayed_x_move_indices={1},
            release_delayed_x_on_position_call=13,
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 50)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIn("cursor_changed_after_delayed_cursor_feedback", receipt.reason)
        event = receipt.cursor_feedback.last_wait
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual("rejected", event.outcome)
        self.assertEqual(ScreenPoint(52, 50), event.last)
        self.assertNotIn("mouse_down:left", backend.events)

    def test_new_button_activity_rejects_after_delayed_cursor_settles(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            delayed_x_move_indices={1},
            release_delayed_x_on_position_call=10,
            physical_mouse_errors=(
                None,
                None,
                None,
                RuntimeError("external button activity"),
            ),
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 50)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn(
            "physical_mouse_not_quiet_after_delayed_cursor_feedback",
            receipt.reason,
        )
        self.assertEqual(1, receipt.cursor_feedback.wait_count)
        self.assertEqual(0, receipt.cursor_feedback.settled_count)
        self.assertEqual("rejected", receipt.cursor_feedback.last_wait.outcome)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.ledger_closed)
        self.assertTrue(receipt.backend_closed)
        commands = [command.command for command in receipt.commands]
        move_index = commands.index("MOVE")
        stop_index = commands.index("STOP_ALL", move_index + 1)
        self.assertEqual(
            [],
            commands[move_index + 1 : stop_index],
        )

    def test_late_wrong_direction_is_rejected_with_retained_feedback(self) -> None:
        class LateWrongDirectionBackend(FakeBackend):
            def _current_position(self) -> tuple[int, int]:
                point = super()._current_position()
                if self.position_call_count == 10:
                    self.position = (point[0] - 1, point[1])
                    self.positions.append(self.position)
                    return self.position
                return point

        backend = LateWrongDirectionBackend(
            start=(50, 50),
            no_effect_x_move_indices={1},
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 50)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("cursor_feedback_direction_mismatch_x", receipt.reason)
        self.assertEqual("rejected", receipt.cursor_feedback.last_wait.outcome)
        self.assertEqual(ScreenPoint(49, 50), receipt.cursor_feedback.last_wait.last)
        self.assertEqual(1, backend.move_call_count)
        self.assertNotIn("mouse_down:left", backend.events)

    def test_same_direction_external_like_motion_keeps_fresh_veto(self) -> None:
        class SameDirectionMotionBackend(FakeBackend):
            def _current_position(self) -> tuple[int, int]:
                point = super()._current_position()
                if self.position_call_count == 11:
                    self.position = (point[0] + 1, point[1])
                    self.positions.append(self.position)
                    return self.position
                return point

        backend = SameDirectionMotionBackend(
            start=(50, 50),
            delayed_x_move_indices={1},
            release_delayed_x_on_position_call=10,
        )

        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 50)),
            validate=lambda _intent, _actual: InputValidation.deny(
                "fresh semantics changed"
            ),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("fresh semantics changed", receipt.reason)
        self.assertEqual(1, receipt.cursor_feedback.wait_count)
        self.assertEqual(1, receipt.cursor_feedback.settled_count)
        self.assertEqual("settled", receipt.cursor_feedback.last_wait.outcome)
        self.assertGreaterEqual(backend.move_call_count, 2)
        self.assertNotIn("mouse_down:left", backend.events)

    def test_persistent_initial_axis_no_effect_never_stacks_credit(self) -> None:
        backend = FakeBackend(start=(50, 50), no_effect_x_move_count=2)
        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(54, 54)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("cursor_feedback_move_effect_unresolved", receipt.reason)
        self.assertIn("before_x=50:before_y=50", receipt.reason)
        self.assertIn("extra_polls=8:elapsed_ms=200:plan=1:step=1", receipt.reason)
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertEqual(1, backend.move_call_count)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)
        self.assertEqual(1, receipt.cursor_feedback.wait_count)
        self.assertEqual(0, receipt.cursor_feedback.settled_count)
        self.assertEqual(8, receipt.cursor_feedback.max_extra_polls)
        self.assertEqual(200, receipt.cursor_feedback.max_elapsed_millis)
        self.assertEqual(
            "effect_unresolved",
            receipt.cursor_feedback.last_wait.outcome,
        )

    def test_one_delayed_same_direction_report_clears_during_settle(self) -> None:
        backend = FakeBackend(
            start=(150, 150),
            device_pixel_scale=4.0,
            delayed_x_move_indices={2},
            release_delayed_x_on_position_call=8,
        )
        intent = ApprovedPointerIntent(
            intent_id="delayed-coalesced",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(170, 150),
            movement_bounds=ScreenBounds(16, 16, 268, 268),
            target_bounds=ScreenBounds(167, 147, 20, 7),
            expected_pid=321,
            canvas_bounds=ScreenBounds(0, 0, 300, 300),
            viewport_bounds=ScreenBounds(0, 0, 300, 300),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertTrue(intent.target_bounds.contains(ScreenPoint(*backend.position)))
        self.assertIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_intermediate_delayed_report_is_polled_before_another_move(self) -> None:
        backend = FakeBackend(
            start=(100, 250),
            delayed_x_move_indices={2},
            release_delayed_x_on_position_call=7,
        )
        intent = ApprovedPointerIntent(
            intent_id="intermediate-delayed-report",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(400, 250),
            movement_bounds=ScreenBounds(16, 16, 468, 468),
            target_bounds=ScreenBounds(397, 247, 7, 7),
            expected_pid=321,
            canvas_bounds=ScreenBounds(0, 0, 500, 500),
            viewport_bounds=ScreenBounds(0, 0, 500, 500),
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

    def test_calibrated_delayed_report_settles_before_another_move(self) -> None:
        backend = FakeBackend(
            start=(500, 500),
            device_pixel_scale=1.75,
            delayed_x_move_indices={3},
            release_delayed_x_on_position_call=9,
        )
        intent = ApprovedPointerIntent(
            intent_id="calibrated-delayed-plan-settle",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(610, 500),
            movement_bounds=ScreenBounds(16, 16, 1168, 968),
            target_bounds=ScreenBounds(607, 497, 7, 7),
            expected_pid=321,
            canvas_bounds=ScreenBounds(0, 0, 1200, 1000),
            viewport_bounds=ScreenBounds(0, 0, 1200, 1000),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful, receipt.reason)
        move_indices = [
            index
            for index, event in enumerate(backend.events)
            if event.startswith("move:")
        ]
        self.assertGreaterEqual(len(move_indices), 4)
        between_third_and_fourth = backend.events[
            move_indices[2] + 1 : move_indices[3]
        ]
        self.assertGreaterEqual(
            sum(
                event.startswith("position:")
                for event in between_third_and_fourth
            ),
            3,
        )
        self.assertIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_calibrated_persistent_no_effect_never_stacks_another_move(self) -> None:
        backend = FakeBackend(
            start=(500, 500),
            device_pixel_scale=1.75,
            no_effect_x_move_indices={3, 4},
        )
        intent = ApprovedPointerIntent(
            intent_id="calibrated-persistent-no-effect",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(610, 500),
            movement_bounds=ScreenBounds(16, 16, 1168, 968),
            target_bounds=ScreenBounds(607, 497, 7, 7),
            expected_pid=321,
            canvas_bounds=ScreenBounds(0, 0, 1200, 1000),
            viewport_bounds=ScreenBounds(0, 0, 1200, 1000),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("cursor_feedback_move_effect_unresolved", receipt.reason)
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertEqual(3, backend.move_call_count)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.stop_all_acknowledged)
        self.assertTrue(receipt.disarm_acknowledged)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)
        self.assertEqual(1, receipt.cursor_feedback.wait_count)
        self.assertEqual(0, receipt.cursor_feedback.settled_count)

    def test_delayed_poll_cannot_mask_wrong_initial_direction(self) -> None:
        class MaskedInitialDirectionBackend(FakeBackend):
            def _current_position(self) -> tuple[int, int]:
                self.position_call_count += 1
                samples = {
                    1: (100, 100),
                    2: (100, 100),
                    3: (100, 100),
                    4: (99, 100),
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
                movement_bounds=ScreenBounds(16, 16, 268, 268),
                target_bounds=ScreenBounds(104, 104, 1, 1),
                expected_pid=321,
                canvas_bounds=ScreenBounds(0, 0, 300, 300),
                viewport_bounds=ScreenBounds(0, 0, 300, 300),
            ),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn(
            "cursor_feedback_direction_mismatch_x:initial_sample",
            receipt.reason,
        )
        self.assertEqual(4, backend.position_call_count)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_delayed_poll_cannot_mask_wrong_incremental_direction(self) -> None:
        class MaskedDelayedDirectionBackend(FakeBackend):
            def _current_position(self) -> tuple[int, int]:
                self.position_call_count += 1
                samples = {
                    1: (100, 100),
                    2: (100, 100),
                    3: (100, 100),
                    4: (100, 102),
                    5: (101, 101),
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
                movement_bounds=ScreenBounds(16, 16, 268, 268),
                target_bounds=ScreenBounds(104, 104, 1, 1),
                expected_pid=321,
                canvas_bounds=ScreenBounds(0, 0, 300, 300),
                viewport_bounds=ScreenBounds(0, 0, 300, 300),
            ),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn(
            "cursor_feedback_direction_mismatch_y:delayed_sample",
            receipt.reason,
        )
        self.assertEqual(5, backend.position_call_count)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_delayed_settle_still_blocks_above_transfer_budget(self) -> None:
        class ExcessiveDelayedBackend(FakeBackend):
            def __init__(self) -> None:
                super().__init__(start=(150, 150))
                self.pending_dx = 0

            def _move_relative(self, dx: int, dy: int) -> dict[str, Any]:
                if self.move_call_count == 0:
                    self.events.append(f"move:{dx},{dy}")
                    self._record("MOVE")
                    self.move_call_count += 1
                    self.pending_dx = dx
                    return {"firmwareAck": "OK MOVE"}
                return super()._move_relative(dx, dy)

            def _current_position(self) -> tuple[int, int]:
                point = super()._current_position()
                if self.position_call_count == 5 and self.pending_dx:
                    self.position = (
                        point[0]
                        + self.pending_dx
                        * (MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT + 1),
                        point[1],
                    )
                    self.positions.append(self.position)
                return self.position

        backend = ExcessiveDelayedBackend()
        intent = ApprovedPointerIntent(
            intent_id="delayed-excessive",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(170, 150),
            movement_bounds=ScreenBounds(16, 16, 268, 268),
            target_bounds=ScreenBounds(167, 147, 21, 7),
            expected_pid=321,
            canvas_bounds=ScreenBounds(0, 0, 300, 300),
            viewport_bounds=ScreenBounds(0, 0, 300, 300),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("cursor_transfer_gain_exceeded_x", receipt.reason)
        self.assertIs(
            receipt.cursor_invalidation_cause,
            CursorInvalidationCause.UNSUPPORTED_TRANSFER_GAIN,
        )
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_delayed_x_settles_before_other_axis_continues(self) -> None:
        backend = FakeBackend(
            start=(100, 100),
            delayed_x_move_indices={2},
            release_delayed_x_on_position_call=8,
        )
        intent = ApprovedPointerIntent(
            intent_id="delayed-zero-current-axis",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(105, 137),
            movement_bounds=ScreenBounds(16, 16, 218, 218),
            target_bounds=ScreenBounds(105, 137, 1, 1),
            expected_pid=321,
            canvas_bounds=ScreenBounds(0, 0, 250, 250),
            viewport_bounds=ScreenBounds(0, 0, 250, 250),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual((105, 137), backend.position)
        self.assertLessEqual(receipt.pointer_motion.correction_plan_count, 2)
        self.assertIn("mouse_down:left", backend.events)

    def test_narrow_scaled_target_accepts_final_report_at_settle(self) -> None:
        backend = FakeBackend(
            start=(2141, 1177),
            device_pixel_scale=2.25,
            delayed_y_move_indices={8},
            release_delayed_y_on_position_call=17,
        )
        intent = ApprovedPointerIntent(
            intent_id="delayed-live-row-shape",
            purpose=InputPurpose.CONTEXT_ROW,
            target=ScreenPoint(2141, 1280),
            movement_bounds=ScreenBounds(1215, 536, 2119, 1487),
            target_bounds=ScreenBounds(2138, 1277, 7, 7),
            expected_pid=321,
            canvas_bounds=ScreenBounds(1199, 520, 2151, 1519),
            viewport_bounds=ScreenBounds(1199, 520, 2151, 1519),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertTrue(intent.target_bounds.contains(ScreenPoint(*backend.position)))
        self.assertLessEqual(receipt.pointer_motion.correction_plan_count, 2)
        self.assertIn("mouse_down:left", backend.events)

    def test_delayed_report_can_arrive_during_plan_settle(self) -> None:
        backend = FakeBackend(
            start=(100, 100),
            delayed_x_move_indices={1},
            release_delayed_x_on_position_call=6,
        )
        plans = []

        def recording_planner(*args, **kwargs):  # type: ignore[no-untyped-def]
            plan = plan_pointer_motion(*args, **kwargs)
            plans.append(plan)
            return plan

        intent = ApprovedPointerIntent(
            intent_id="delayed-plan-settle",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(101, 100),
            movement_bounds=ScreenBounds(16, 16, 218, 218),
            target_bounds=ScreenBounds(101, 100, 1, 1),
            expected_pid=321,
            canvas_bounds=ScreenBounds(0, 0, 250, 250),
            viewport_bounds=ScreenBounds(0, 0, 250, 250),
        )

        receipt = InputCoordinator(
            lambda: backend,
            pointer_planner=recording_planner,
            sleep=lambda _seconds: None,
            pointer_timestep_seconds=0.02,
        ).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        self.assertEqual((101, 100), backend.position)
        self.assertEqual(1, backend.move_call_count)
        self.assertEqual(2, len(plans))
        self.assertEqual((), plans[-1].steps)
        self.assertEqual(plans[-1].start, plans[-1].target)
        self.assertIn("mouse_down:left", backend.events)

    def test_unresolved_delayed_command_blocks_activation(self) -> None:
        backend = FakeBackend(
            start=(100, 100),
            device_pixel_scale=4.0,
            delayed_x_move_indices={1},
        )
        intent = ApprovedPointerIntent(
            intent_id="unresolved-delayed",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(200, 100),
            movement_bounds=ScreenBounds(0, 0, 300, 300),
            target_bounds=ScreenBounds(184, 97, 20, 7),
            expected_pid=321,
            canvas_bounds=ScreenBounds(-16, -16, 332, 332),
            viewport_bounds=ScreenBounds(-16, -16, 332, 332),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("cursor_feedback_move_effect_unresolved", receipt.reason)
        self.assertNotIn("mouse_down:left", backend.events)

    def test_unresolved_delayed_credit_blocks_before_headroom_replan(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            device_pixel_scale=4.0,
            delayed_x_move_indices={1},
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
        self.assertIn("cursor_feedback_move_effect_unresolved", receipt.reason)
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertEqual(1, backend.move_call_count)
        self.assertNotIn("mouse_down:left", backend.events)

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
        self.assertEqual(1, len(validated_at))
        self.assertTrue(intent.target_bounds.contains(validated_at[0]))
        self.assertLessEqual(receipt.pointer_motion.plan_count, 3)
        self.assertLessEqual(receipt.pointer_motion.correction_plan_count, 2)
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
            canvas_bounds=viewport_around_safe(bounds),
            viewport_bounds=viewport_around_safe(bounds),
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
        self.assertLessEqual(len(plans), 3)
        self.assertLessEqual(receipt.pointer_motion.correction_plan_count, 2)
        self.assertLessEqual(
            [command.command for command in receipt.commands].count("MOVE"),
            512,
        )

    def test_long_inverse_scaled_live_shape_uses_safe_calibrated_waypoint(self) -> None:
        bounds = ScreenBounds(1241, 261, 2151, 1519)
        backend = FakeBackend(
            start=(2257, 348),
            device_pixel_scale=1.0 / 1.75,
            virtual_desktop_bounds=(0, 0, 4000, 2200),
        )
        intent = ApprovedPointerIntent(
            intent_id="inverse-scaled-live-shape",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(1338, 298),
            movement_bounds=bounds,
            target_bounds=ScreenBounds(1335, 295, 7, 7),
            expected_pid=321,
            canvas_bounds=viewport_around_safe(bounds),
            viewport_bounds=viewport_around_safe(bounds),
            motion_seed=418766215160290826,
            motion_decision_id="inverse-scaled-live-shape:3442:9",
            motion_context="object",
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful, receipt.reason)
        self.assertTrue(intent.target_bounds.contains(ScreenPoint(*backend.position)))
        self.assertLessEqual(receipt.pointer_motion.executed_step_count, 512)
        self.assertTrue(all(bounds.contains(ScreenPoint(*p)) for p in backend.positions))
        self.assertIsNotNone(receipt.pointer_motion.transfer_gain_upper_x)
        assert receipt.pointer_motion.transfer_gain_upper_x is not None
        self.assertGreater(receipt.pointer_motion.transfer_gain_upper_x, 0.0)
        self.assertLessEqual(
            receipt.pointer_motion.transfer_gain_upper_x,
            MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT,
        )
        self.assertLessEqual(receipt.pointer_motion.plan_count, 3)
        self.assertLessEqual(receipt.pointer_motion.correction_plan_count, 2)
        self.assertEqual(
            receipt.pointer_motion.transfer_gain_upper_x,
            receipt.to_dict()["pointerMotion"]["learnedTransferGainUpper"]["x"],
        )
        self.assertIsNone(receipt.pointer_motion.transfer_transit_waypoint)

    def test_transfer_gain_upper_retains_larger_observed_response(self) -> None:
        low = InputCoordinator._updated_transfer_gain_upper(None, 10, 5)
        self.assertIsNotNone(low)
        assert low is not None
        high = InputCoordinator._updated_transfer_gain_upper(low, 10, 15)

        self.assertIsNotNone(high)
        assert high is not None
        self.assertGreater(high, low)
        self.assertEqual(
            min(
                MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT,
                (15 + 0.5) / 10 * 1.10,
            ),
            high,
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
            canvas_bounds=viewport_around_safe(bounds),
            viewport_bounds=viewport_around_safe(bounds),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn(
            "relative_move_supported_transfer_would_leave_viewport",
            receipt.reason,
        )
        self.assertEqual(0, sum(event.startswith("move:") for event in backend.events))
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_feedback_outside_padded_viewport_after_acknowledged_move_is_typed(self) -> None:
        backend = FakeBackend(start=(95, 50), device_pixel_scale=5.0)
        bounds = BOUNDS
        intent = ApprovedPointerIntent(
            intent_id="scaled-edge",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(99, 50),
            movement_bounds=bounds,
            target_bounds=ScreenBounds(99, 50, 1, 1),
            expected_pid=321,
            canvas_bounds=viewport_around_safe(bounds),
            viewport_bounds=viewport_around_safe(bounds),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn("cursor_left_verified_movement_bounds", receipt.reason)
        self.assertIs(
            receipt.cursor_invalidation_cause,
            CursorInvalidationCause.OUTSIDE_PADDED_VIEWPORT,
        )
        self.assertEqual(1, backend.move_call_count)
        self.assertFalse(bounds.contains(ScreenPoint(*backend.position)))
        self.assertTrue(
            any(not bounds.contains(sample.point) for sample in receipt.cursor_samples)
        )
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertNotIn("mouse_up:left", backend.events)
        self.assertFalse(any(event.startswith("press:") for event in backend.events))
        self.assert_complete_cleanup(receipt)

    def test_settled_sample_outside_inset_is_typed_before_owner_lookup(self) -> None:
        class SettledOutsideBackend(FakeBackend):
            def _current_position(self) -> tuple[int, int]:
                point = super()._current_position()
                if self.position_call_count == 5:
                    self.position = (200, point[1])
                    self.positions.append(self.position)
                return self.position

            def _window_info_at_point(self, point: tuple[int, int]) -> dict[str, Any]:
                if point[0] == 200:
                    return {}  # owner evidence is unavailable outside the inset
                return super()._window_info_at_point(point)

        backend = SettledOutsideBackend(start=(10, 10))
        receipt = coordinator(backend).execute_pointer(
            pointer_intent(target=ScreenPoint(12, 10)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIs(
            receipt.cursor_invalidation_cause,
            CursorInvalidationCause.OUTSIDE_PADDED_VIEWPORT,
        )
        self.assertIn("cursor_left_verified_movement_bounds", receipt.reason)
        self.assertNotIn("mouse_down:left", backend.events)

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
            canvas_bounds=viewport_around_safe(bounds),
            viewport_bounds=viewport_around_safe(bounds),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        moves = [event for event in backend.events if event.startswith("move:")]
        self.assertTrue(moves)
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
            canvas_bounds=viewport_around_safe(bounds),
            viewport_bounds=viewport_around_safe(bounds),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        moves = [event for event in backend.events if event.startswith("move:")]
        self.assertTrue(moves)
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
            canvas_bounds=viewport_around_safe(canvas),
            viewport_bounds=viewport_around_safe(canvas),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful)
        moves = [event for event in backend.events if event.startswith("move:")]
        self.assertTrue(moves)
        self.assertLessEqual(len(moves), 512)
        self.assertLessEqual(receipt.pointer_motion.plan_count, 3)
        self.assertLessEqual(receipt.pointer_motion.correction_plan_count, 2)
        self.assertTrue(intent.target_bounds.contains(ScreenPoint(*backend.position)))
        self.assertTrue(all(canvas.contains(ScreenPoint(*p)) for p in backend.positions))
        self.assertIn("mouse_down:left", backend.events)

    def test_narrow_safe_inset_uses_directed_inward_motion(self) -> None:
        backend = FakeBackend(start=(13, 50))
        intent = ApprovedPointerIntent(
            intent_id="opposite-tied-headroom",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(20, 70),
            movement_bounds=ScreenBounds(0, 0, 27, 100),
            target_bounds=ScreenBounds(20, 70, 1, 1),
            expected_pid=321,
            canvas_bounds=ScreenBounds(-16, -16, 59, 132),
            viewport_bounds=ScreenBounds(-16, -16, 59, 132),
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful, receipt.reason)
        self.assertTrue(intent.target_bounds.contains(ScreenPoint(*backend.position)))
        self.assertLessEqual(receipt.pointer_motion.correction_plan_count, 2)
        self.assertTrue(
            all(intent.movement_bounds.contains(ScreenPoint(*point)) for point in backend.positions)
        )

    def test_tight_margin_does_not_move_toward_the_nearest_edge(self) -> None:
        backend = FakeBackend(start=(13, 50))
        intent = ApprovedPointerIntent(
            intent_id="unsafe-headroom-direction",
            purpose=InputPurpose.GAMEPLAY_OBJECT,
            target=ScreenPoint(5, 70),
            movement_bounds=ScreenBounds(0, 0, 100, 100),
            target_bounds=ScreenBounds(5, 70, 1, 1),
            expected_pid=321,
            canvas_bounds=VIEWPORT,
            viewport_bounds=VIEWPORT,
        )

        receipt = coordinator(backend).execute_pointer(
            intent,
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertFalse(receipt.successful)
        self.assertIn(
            "relative_move_supported_transfer_would_leave_viewport",
            receipt.reason,
        )
        self.assertLessEqual(backend.move_call_count, 1)
        self.assertTrue(
            all(intent.movement_bounds.contains(ScreenPoint(*point)) for point in backend.positions)
        )
        self.assertNotIn("mouse_down:left", backend.events)

    def test_direction_reversal_and_cross_axis_feedback_are_typed(self) -> None:
        class ReversingBackend(FakeBackend):
            def _move_relative(self, dx: int, dy: int) -> dict[str, Any]:
                return super()._move_relative(-dx, -dy)

        bounds = BOUNDS
        cases = (
            (
                "reversal",
                ReversingBackend(start=(50, 50)),
                ScreenPoint(40, 50),
                CursorInvalidationCause.UNEXPECTED_DIRECTION,
            ),
            (
                "cross-axis",
                FakeBackend(start=(50, 50), cursor_diverges=True),
                ScreenPoint(40, 50),
                CursorInvalidationCause.UNEXPECTED_CROSS_AXIS,
            ),
        )
        for label, backend, target, expected_cause in cases:
            with self.subTest(label=label):
                intent = ApprovedPointerIntent(
                    intent_id=f"edge-{label}",
                    purpose=InputPurpose.GAMEPLAY_OBJECT,
                    target=target,
                    movement_bounds=bounds,
                    target_bounds=ScreenBounds(target.x, target.y, 1, 1),
                    expected_pid=321,
                    canvas_bounds=viewport_around_safe(bounds),
                    viewport_bounds=viewport_around_safe(bounds),
                )
                receipt = coordinator(backend).execute_pointer(
                    intent,
                    validate=lambda _intent, _actual: InputValidation.allow(),
                )
                self.assertFalse(receipt.successful)
                self.assertIs(receipt.cursor_invalidation_cause, expected_cause)
                self.assertEqual(
                    1,
                    sum(event.startswith("move:") for event in backend.events),
                )
                self.assertNotIn("mouse_down:left", backend.events)
                self.assertNotIn("mouse_up:left", backend.events)
                self.assertEqual(
                    ScreenPoint(*backend.position),
                    receipt.cursor_samples[-1].point,
                )
                self.assert_complete_cleanup(receipt)

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
        self.assertIs(
            receipt.failure_kind,
            InputFailureKind.CURSOR_STATE_INVALIDATED,
        )
        self.assertIs(
            receipt.cursor_invalidation_cause,
            CursorInvalidationCause.UNSUPPORTED_TRANSFER_GAIN,
        )
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertEqual(
            ScreenPoint(*backend.position),
            receipt.cursor_samples[-1].point,
        )
        self.assert_complete_cleanup(receipt)

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
                backend = FakeBackend(
                    device_pixel_scale=2.0 if label == "plan" else 1.0
                )
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
        backend = FakeBackend(device_pixel_scale=2.0)
        main = pointer_intent(
            target=ScreenPoint(40, 10),
            target_bounds=ScreenBounds(39, 9, 3, 3),
        )
        row = pointer_intent(
            intent_id="row",
            purpose=InputPurpose.CONTEXT_ROW,
            target=ScreenPoint(40, 40),
            target_bounds=ScreenBounds(39, 39, 3, 3),
        )
        leg_plan_counts = [0, 0]

        def recording_planner(*args, **kwargs):  # type: ignore[no-untyped-def]
            leg = 1 if "mouse_down:right" in backend.events else 0
            leg_plan_counts[leg] += 1
            return plan_pointer_motion(*args, **kwargs)

        receipt = InputCoordinator(
            lambda: backend,
            pointer_planner=recording_planner,
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

        self.assertTrue(receipt.successful, receipt.reason)
        self.assertEqual([2, 2], leg_plan_counts)
        self.assertEqual(2, receipt.pointer_motion.correction_plan_count)
        self.assertEqual(4, receipt.pointer_motion.plan_count)
        self.assertFalse(receipt.context_cancel_attempted)
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

    def test_login_external_cursor_reacquires_without_activation(self) -> None:
        start = (20, 200)
        backend = FakeBackend(
            start=start,
            virtual_desktop_bounds=(0, 0, 400, 400),
        )
        validator_calls = 0

        def stale_login_validator(
            _intent: ApprovedPointerIntent,
            _actual: ScreenPoint,
        ) -> InputValidation:
            nonlocal validator_calls
            validator_calls += 1
            return InputValidation.allow()

        receipt = coordinator(backend).execute_pointer(
            reacquisition_intent(
                intent_id="login-external-cursor",
                purpose=InputPurpose.LOGIN_PROMPT,
            ),
            validate=stale_login_validator,
        )

        self.assert_safe_cursor_reacquisition(
            receipt, backend, cursor_before=start
        )
        self.assertEqual(0, validator_calls)

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
        with self.assertRaises(TypeError):
            InputCoordinator(
                lambda: backend,
                monotonic=None,  # type: ignore[arg-type]
            )

    def test_invalid_feedback_clock_blocks_before_move_and_cleans_up(self) -> None:
        backend = FakeBackend()
        receipt = InputCoordinator(
            lambda: backend,
            sleep=lambda _seconds: None,
            monotonic=lambda: float("nan"),
        ).execute_pointer(
            pointer_intent(),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertEqual("ERROR", receipt.status)
        self.assertIn("cursor_feedback_clock_invalid", receipt.reason)
        self.assertEqual(0, backend.move_call_count)
        self.assertNotIn("mouse_down:left", backend.events)
        self.assertTrue(receipt.firmware_status and receipt.firmware_status.safe)

    def test_public_receipt_evidence_and_status_types_are_deeply_immutable(self) -> None:
        backend = FakeBackend()
        receipt = coordinator(backend).execute_key(
            ApprovedKeyIntent("key", InputPurpose.GAMEPLAY_KEY, "ESC", 321),
            validate=lambda _intent: InputValidation.allow(),
        )

        self.assertIsInstance(receipt, InputReceipt)
        self.assertIsInstance(receipt.commands[0], CommandEvidence)
        self.assertIsInstance(receipt.firmware_status, FirmwareSafetyStatus)
        self.assertIsInstance(receipt.cursor_feedback, CursorFeedbackEvidence)
        self.assertFalse(hasattr(receipt, "__dict__"))
        self.assertFalse(hasattr(receipt.commands[0], "__dict__"))
        self.assertFalse(hasattr(receipt.firmware_status, "__dict__"))
        self.assertFalse(hasattr(receipt.cursor_feedback, "__dict__"))
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
        with self.assertRaises(TypeError):
            replace(receipt, cursor_feedback={})  # type: ignore[arg-type]

        event = DelayedCursorFeedbackEvent(
            plan=1,
            step=1,
            command_dx=1,
            command_dy=0,
            before=ScreenPoint(10, 10),
            last=ScreenPoint(11, 10),
            extra_polls=3,
            elapsed_millis=100,
            first_effect_millis=60,
            complete_effect_millis=60,
            outcome="settled",
        )
        feedback = CursorFeedbackEvidence(
            wait_count=1,
            settled_count=1,
            max_extra_polls=3,
            max_elapsed_millis=100,
            last_wait=event,
        )
        self.assertFalse(hasattr(event, "__dict__"))
        self.assertEqual("settled", feedback.to_dict()["lastWait"]["outcome"])
        with self.assertRaises(FrozenInstanceError):
            event.outcome = "rejected"  # type: ignore[misc]
        with self.assertRaises(ValueError):
            replace(event, extra_polls=0)
        with self.assertRaises(ValueError):
            replace(event, plan=65)
        with self.assertRaises(ValueError):
            replace(event, step=513)
        with self.assertRaises(ValueError):
            replace(event, last=ScreenPoint(9, 11))
        with self.assertRaises(ValueError):
            replace(
                event,
                last=ScreenPoint(11, 11),
                complete_effect_millis=None,
                outcome="effect_unresolved",
            )
        with self.assertRaises(ValueError):
            CursorFeedbackEvidence(wait_count=1, settled_count=2, last_wait=event)
        with self.assertRaises(ValueError):
            CursorFeedbackEvidence(wait_count=0, last_wait=event)
        with self.assertRaises(ValueError):
            replace(event, first_effect_millis=None)
        with self.assertRaises(ValueError):
            replace(event, complete_effect_millis=None)
        with self.assertRaises(ValueError):
            replace(event, complete_effect_millis=40)
        with self.assertRaises(ValueError):
            replace(event, complete_effect_millis=201)
        with self.assertRaises(ValueError):
            CursorFeedbackEvidence(
                wait_count=1,
                settled_count=0,
                max_extra_polls=3,
                max_elapsed_millis=100,
                last_wait=event,
            )
        rejected = replace(event, outcome="rejected")
        with self.assertRaises(ValueError):
            CursorFeedbackEvidence(
                wait_count=1,
                settled_count=1,
                max_extra_polls=3,
                max_elapsed_millis=100,
                last_wait=rejected,
            )
        with self.assertRaises(ValueError):
            CursorFeedbackEvidence(
                wait_count=2,
                settled_count=1,
                max_extra_polls=3,
                max_elapsed_millis=100,
                last_wait=event,
            )
        with self.assertRaises(ValueError):
            CursorFeedbackEvidence(
                wait_count=5,
                settled_count=0,
                max_extra_polls=3,
                max_elapsed_millis=100,
                last_wait=rejected,
            )
        with self.assertRaises(ValueError):
            CursorFeedbackEvidence(
                wait_count=1,
                settled_count=1,
                max_extra_polls=10,
                max_elapsed_millis=999,
                last_wait=event,
            )
        with self.assertRaises(TypeError):
            replace(event, before=ScreenPoint(True, 10))
        with self.assertRaises(TypeError):
            replace(event, last=ScreenPoint(11, "10"))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            replace(receipt, cursor_feedback=feedback)
        with self.assertRaises(ValueError):
            replace(receipt, mode="pointer", cursor_feedback=feedback)

        pointer_receipt = coordinator(FakeBackend()).execute_pointer(
            pointer_intent(),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )
        move_count = sum(
            command.command == "MOVE" and command.successful
            for command in pointer_receipt.commands
        )
        with self.assertRaises(ValueError):
            replace(
                pointer_receipt,
                cursor_feedback=CursorFeedbackEvidence(
                    wait_count=1,
                    settled_count=1,
                    max_extra_polls=3,
                    max_elapsed_millis=100,
                    last_wait=replace(event, step=move_count + 1),
                ),
            )

        fabricated = replace(receipt, commands=())
        self.assertFalse(fabricated.wire_proof_complete)
        self.assertFalse(fabricated.successful)
        self.assertTrue(receipt.wire_proof_complete)

    def test_receipt_adds_bounded_immutable_input_timing_and_busy_state(self) -> None:
        backend = FakeBackend()
        evidence_now = [0.0]
        observed: list[WaitState | None] = []

        def evidence_clock() -> float:
            evidence_now[0] += 0.005
            return evidence_now[0]

        receipt = InputCoordinator(
            lambda: backend,
            evidence_clock=evidence_clock,
            wait_state_observer=observed.append,
        ).execute_key(
            ApprovedKeyIntent("timed-key", InputPurpose.GAMEPLAY_KEY, "ESC", 321),
            validate=lambda _intent: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful, receipt.reason)
        self.assertIsInstance(receipt.observability, ObservabilityEvidence)
        self.assertIsNone(receipt.observability.wait_state)
        self.assertEqual(
            (WaitState.INPUT_TRANSACTION_BUSY,),
            receipt.observability.observed_wait_states,
        )
        self.assertEqual(WaitState.INPUT_TRANSACTION_BUSY, observed[0])
        self.assertIsNone(observed[-1])
        for phase in (
            TimingPhase.INPUT_LEASE_ACQUISITION,
            TimingPhase.ARDUINO_CONNECT_NEGOTIATE_ARM,
            TimingPhase.SERIAL_WRITE_ACKNOWLEDGEMENT,
            TimingPhase.FINAL_CLEANUP,
        ):
            aggregate = receipt.observability.timing.for_phase(phase)
            self.assertIsNotNone(aggregate, phase)
            assert aggregate is not None
            self.assertGreaterEqual(aggregate.count, 1)
            self.assertGreaterEqual(aggregate.total_millis, 0)
            self.assertLessEqual(aggregate.max_millis, MAX_DURATION_MILLIS)
            self.assertLessEqual(aggregate.last_millis, MAX_DURATION_MILLIS)
        self.assertEqual(
            receipt.observability.to_dict(),
            receipt.to_dict()["observability"],
        )
        self.assertFalse(hasattr(receipt.observability, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            receipt.observability.wait_state = (  # type: ignore[misc]
                WaitState.ARDUINO_COMMAND_FAILED
            )
        with self.assertRaises(TypeError):
            replace(receipt, observability={})  # type: ignore[arg-type]

        # Existing command mappings deliberately omit the additive duration
        # fields and remain readable with explicit None defaults.
        self.assertTrue(receipt.commands)
        self.assertTrue(
            all(command.write_duration_millis is None for command in receipt.commands)
        )
        self.assertTrue(
            all(
                command.acknowledgement_duration_millis is None
                for command in receipt.commands
            )
        )

    def test_transport_durations_are_aggregated_exactly_when_available(self) -> None:
        class TimedBackend(FakeBackend):
            def _record(self, command: str) -> None:
                super()._record(command)
                self.records[-1]["writeDurationMillis"] = 2
                self.records[-1]["acknowledgementDurationMillis"] = 3

        backend = TimedBackend()
        receipt = coordinator(backend).execute_key(
            ApprovedKeyIntent("timed-wire", InputPurpose.GAMEPLAY_KEY, "ESC", 321),
            validate=lambda _intent: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful, receipt.reason)
        self.assertTrue(receipt.commands)
        self.assertTrue(
            all(command.write_duration_millis == 2 for command in receipt.commands)
        )
        self.assertTrue(
            all(
                command.acknowledgement_duration_millis == 3
                for command in receipt.commands
            )
        )
        serial = receipt.observability.timing.for_phase(
            TimingPhase.SERIAL_WRITE_ACKNOWLEDGEMENT
        )
        self.assertIsNotNone(serial)
        assert serial is not None
        self.assertEqual(len(receipt.commands), serial.count)
        self.assertEqual(5 * len(receipt.commands), serial.total_millis)
        self.assertEqual(5, serial.max_millis)

    def test_malformed_additive_transport_timing_cannot_change_input_result(self) -> None:
        class MalformedTimingBackend(FakeBackend):
            def _record(self, command: str) -> None:
                super()._record(command)
                self.records[-1]["writeDurationMillis"] = "secret raw text"
                self.records[-1]["acknowledgementDurationMillis"] = (
                    MAX_DURATION_MILLIS + 1
                )

        receipt = coordinator(MalformedTimingBackend()).execute_key(
            ApprovedKeyIntent(
                "malformed-timing",
                InputPurpose.GAMEPLAY_KEY,
                "ESC",
                321,
            ),
            validate=lambda _intent: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful, receipt.reason)
        self.assertTrue(
            all(command.write_duration_millis is None for command in receipt.commands)
        )
        self.assertTrue(
            all(
                command.acknowledgement_duration_millis is None
                for command in receipt.commands
            )
        )
        self.assertNotIn(
            "secret raw text",
            json.dumps(receipt.to_dict(), sort_keys=True),
        )

    def test_delayed_cursor_feedback_has_distinct_passive_wait_state(self) -> None:
        backend = FakeBackend(
            start=(50, 50),
            delayed_x_move_indices={1},
            release_delayed_x_on_position_call=13,
        )
        now = [0.0]
        observed: list[WaitState | None] = []

        def sleep(seconds: float) -> None:
            now[0] += seconds

        receipt = InputCoordinator(
            lambda: backend,
            sleep=sleep,
            monotonic=lambda: now[0],
            evidence_clock=lambda: now[0],
            wait_state_observer=observed.append,
        ).execute_pointer(
            pointer_intent(target=ScreenPoint(51, 50)),
            validate=lambda _intent, _actual: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful, receipt.reason)
        self.assertIn(WaitState.CURSOR_FEEDBACK_SETTLING, observed)
        self.assertEqual(WaitState.INPUT_TRANSACTION_BUSY, observed[0])
        self.assertIsNone(observed[-1])
        self.assertEqual(
            (
                WaitState.INPUT_TRANSACTION_BUSY,
                WaitState.CURSOR_FEEDBACK_SETTLING,
            ),
            receipt.observability.observed_wait_states,
        )
        pointer = receipt.observability.timing.for_phase(
            TimingPhase.POINTER_PLANNING_FEEDBACK_SETTLEMENT
        )
        self.assertIsNotNone(pointer)
        assert pointer is not None
        self.assertGreaterEqual(pointer.total_millis, 0)

    def test_camera_hold_uses_v2_capability_and_exact_activation_boundary(self) -> None:
        backend = FakeBackend()

        receipt = coordinator(backend).execute_camera_hold(
            camera_hold_intent(),
            validate=lambda _intent: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful, receipt.reason)
        self.assertEqual([("right", 600)], backend.camera_holds)
        self.assertEqual("camera_hold", receipt.mode)
        commands = [command.command for command in receipt.commands]
        self.assertEqual("ARM", commands[0])
        self.assertEqual(1, commands.count("CAMERA_HOLD"))
        self.assertEqual(["STOP_ALL", "DISARM", "STATUS"], commands[-3:])
        self.assertEqual(1, len(receipt.required_capabilities))
        self.assertEqual(
            "camera_key_hold",
            receipt.required_capabilities[0].operation.value,
        )
        self.assertIsNotNone(receipt.negotiated_capabilities)
        assert receipt.negotiated_capabilities is not None
        self.assertEqual(
            "arduino_hid.v2",
            receipt.negotiated_capabilities.protocol_version,
        )
        boundary = receipt.activation_boundary
        self.assertIsNotNone(boundary)
        assert boundary is not None
        self.assertTrue(boundary.attempted)
        self.assertTrue(boundary.acknowledged)
        self.assertEqual("right", boundary.direction)
        self.assertEqual(600, boundary.requested_duration_millis)
        self.assertEqual(600, boundary.applied_duration_millis)
        self.assertIsNotNone(boundary.command_sequence)
        self.assert_complete_cleanup(receipt)

    def test_camera_hold_capability_absence_blocks_before_activation(self) -> None:
        backend = FakeBackend(input_capabilities=legacy_input_capabilities())

        receipt = coordinator(backend).execute_camera_hold(
            camera_hold_intent(),
            validate=lambda _intent: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIs(InputFailureKind.CAPABILITY_UNAVAILABLE, receipt.failure_kind)
        self.assertEqual([], backend.camera_holds)
        self.assertNotIn("CAMERA_HOLD", [item.command for item in receipt.commands])
        self.assertIsNone(receipt.activation_boundary)
        self.assert_complete_cleanup(receipt)

    def test_camera_zoom_is_stationary_exact_and_fully_cleaned(self) -> None:
        backend = FakeBackend(start=(150, 150))

        receipt = coordinator(backend).execute_camera_zoom(
            camera_zoom_intent(),
            validate=lambda _intent: InputValidation.allow(),
        )

        self.assertTrue(receipt.successful, receipt.reason)
        self.assertEqual([1], backend.wheel_amounts)
        self.assertEqual([], [event for event in backend.events if event.startswith("move:")])
        self.assertNotIn("MOUSE_DOWN", [item.command for item in receipt.commands])
        self.assertNotIn("KEY_PRESS", [item.command for item in receipt.commands])
        boundary = receipt.activation_boundary
        self.assertIsNotNone(boundary)
        assert boundary is not None
        self.assertEqual(77, boundary.expected_hwnd)
        self.assertEqual(ScreenPoint(150, 150), boundary.cursor_point)
        self.assertEqual(1, boundary.requested_wheel_amount)
        self.assertEqual(1, boundary.applied_wheel_amount)
        self.assert_complete_cleanup(receipt)

    def test_camera_zoom_capability_absence_blocks_before_activation(self) -> None:
        backend = FakeBackend(
            start=(150, 150),
            input_capabilities=legacy_input_capabilities(),
        )

        receipt = coordinator(backend).execute_camera_zoom(
            camera_zoom_intent(),
            validate=lambda _intent: InputValidation.allow(),
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertIs(InputFailureKind.CAPABILITY_UNAVAILABLE, receipt.failure_kind)
        self.assertEqual([], backend.wheel_amounts)
        self.assertNotIn("WHEEL", [item.command for item in receipt.commands])
        self.assertIsNone(receipt.activation_boundary)
        self.assert_complete_cleanup(receipt)

    def test_camera_zoom_physical_or_owner_failure_sends_no_wheel(self) -> None:
        cases = (
            (
                "physical",
                FakeBackend(
                    start=(150, 150),
                    physical_mouse_evidence_overrides={"activityClear": False},
                ),
            ),
            (
                "owner",
                FakeBackend(start=(150, 150), point_owner_hwnds=[88]),
            ),
            (
                "outside",
                FakeBackend(start=(REACQUISITION_SAFE.x - 1, 150)),
            ),
            (
                "hwnd_changed",
                FakeBackend(start=(150, 150), foreground_hwnds=[77, 88]),
            ),
            (
                "pid_or_focus_changed",
                FakeBackend(
                    start=(150, 150),
                    foreground_errors=[None, RuntimeError("foreground PID changed")],
                ),
            ),
            (
                "geometry_changed",
                FakeBackend(
                    start=(150, 150),
                    window_geometry_evidence_sequence=[
                        {},
                        {"outerMatches": False},
                    ],
                ),
            ),
        )
        for name, backend in cases:
            with self.subTest(name=name):
                receipt = coordinator(backend).execute_camera_zoom(
                    camera_zoom_intent(),
                    validate=lambda _intent: InputValidation.allow(),
                )
                self.assertIn(receipt.status, {"BLOCKED", "ERROR"})
                self.assertEqual([], backend.wheel_amounts)
                self.assertNotIn("WHEEL", [item.command for item in receipt.commands])
                self.assert_complete_cleanup(receipt)

    def test_wait_observer_cannot_change_input_result_or_leak_failure_text(self) -> None:
        backend = FakeBackend(missing_ack_commands={"KEY_PRESS"})
        observed: list[WaitState | None] = []

        def observer(state: WaitState | None) -> None:
            observed.append(state)
            raise RuntimeError("observer-secret-text")

        receipt = InputCoordinator(
            lambda: backend,
            wait_state_observer=observer,
        ).execute_key(
            ApprovedKeyIntent("ack-fail", InputPurpose.GAMEPLAY_KEY, "ESC", 321),
            validate=lambda _intent: InputValidation.allow(),
        )

        self.assertEqual("ERROR", receipt.status)
        self.assertGreater(receipt.ack_missing_count, 0)
        self.assertNotIn(
            "observer-secret-text",
            json.dumps(receipt.to_dict(), sort_keys=True),
        )
        self.assertEqual(WaitState.INPUT_TRANSACTION_BUSY, observed[0])
        self.assertIsNone(observed[-1])
        self.assertIn(
            WaitState.ARDUINO_COMMAND_FAILED,
            receipt.observability.observed_wait_states,
        )
        self.assertIs(
            receipt.observability.wait_state,
            WaitState.ARDUINO_COMMAND_FAILED,
        )
        self.assertNotIn(WaitState.ARDUINO_COMMAND_FAILED, observed)

    @staticmethod
    def _validation_event(
        backend: FakeBackend, event: str
    ) -> InputValidation:
        backend.events.append(event)
        return InputValidation.allow()


if __name__ == "__main__":
    unittest.main()

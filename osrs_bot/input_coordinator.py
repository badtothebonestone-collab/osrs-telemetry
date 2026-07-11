from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, TypeAlias

from .model import ScreenBounds, ScreenPoint
from .pointer import (
    DEFAULT_POINTER_MOTION_LIMITS,
    PointerMotionLimits,
    PointerMotionPlan,
    plan_pointer_motion,
)


COMMAND_LEDGER_SCHEMA = "arduino_command_ledger.v1"
MAX_LEDGER_ENTRIES = 2_048
MAX_POINTER_STEPS = 512
MAX_POINTER_FEEDBACK_PLANS = 64
MAX_FEEDBACK_PLAN_AXIS_DELTA = 64
MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT = 4
CURSOR_TRANSFER_HEADROOM_DEVICE_PX_PER_HID_COUNT = 8
MAX_CONSECUTIVE_AXIS_NO_EFFECT_RETRIES = 1
MAX_TRANSACTION_NO_EFFECT_EVENTS = 8
DEFAULT_POINTER_TIMESTEP_SECONDS = 0.02
DEFAULT_CLICK_HOLD_SECONDS = 0.06
_COMMAND_ID = re.compile(r"^cmd-[0-9]{8,}$")
_SAFE_KEY = re.compile(r"^[A-Z0-9_]{1,16}$")
_ARM_SECRET = re.compile(r"(?i)(\bARM\s+)(\S+)")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _clean_text(value: object, *, fallback: str = "") -> str:
    text = str(value if value is not None else fallback).strip()
    return _ARM_SECRET.sub(r"\1<redacted>", text)


def _validate_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > 128:
        raise ValueError(f"{field_name} must not exceed 128 characters")
    return normalized


def _validate_bounds(bounds: object, field_name: str) -> ScreenBounds:
    if not isinstance(bounds, ScreenBounds):
        raise TypeError(f"{field_name} must be ScreenBounds")
    if not all(
        _is_int(value)
        for value in (bounds.x, bounds.y, bounds.width, bounds.height)
    ):
        raise TypeError(f"{field_name} coordinates and dimensions must be integers")
    if bounds.width <= 0 or bounds.height <= 0:
        raise ValueError(f"{field_name} dimensions must be positive")
    return bounds


def _bounds_contains_bounds(outer: ScreenBounds, inner: ScreenBounds) -> bool:
    return (
        outer.contains(ScreenPoint(inner.x, inner.y))
        and outer.contains(
            ScreenPoint(inner.x + inner.width - 1, inner.y + inner.height - 1)
        )
    )


class InputPurpose(str, Enum):
    GAMEPLAY_OBJECT = "gameplay_object"
    GAMEPLAY_WIDGET = "gameplay_widget"
    CONTEXT_MENU = "context_menu"
    CONTEXT_ROW = "context_row"
    LOGIN_PROMPT = "login_prompt"
    GAMEPLAY_KEY = "gameplay_key"


class MouseButton(str, Enum):
    LEFT = "left"
    RIGHT = "right"


class PointerActivation(str, Enum):
    DIRECT_LEFT = "direct_left"
    CONTEXT_MENU = "context_menu"


@dataclass(frozen=True, slots=True)
class ApprovedPointerIntent:
    """A statically approved pointer activation awaiting fresh validation.

    ``movement_bounds`` is the complete verified transit region.  The stricter
    ``target_bounds`` proves that the activation point belongs to the intended
    RuneLite surface (canvas, widget region, context row, or login client).
    """

    intent_id: str
    purpose: InputPurpose
    target: ScreenPoint
    movement_bounds: ScreenBounds
    target_bounds: ScreenBounds
    expected_pid: int
    button: MouseButton = MouseButton.LEFT

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "intent_id", _validate_identifier(self.intent_id, "intent_id")
        )
        if not isinstance(self.purpose, InputPurpose):
            raise TypeError("purpose must be InputPurpose")
        if self.purpose is InputPurpose.GAMEPLAY_KEY:
            raise ValueError("a pointer intent cannot use the gameplay_key purpose")
        if not isinstance(self.target, ScreenPoint):
            raise TypeError("target must be ScreenPoint")
        if not _is_int(self.target.x) or not _is_int(self.target.y):
            raise TypeError("target coordinates must be integers")
        movement = _validate_bounds(self.movement_bounds, "movement_bounds")
        target_bounds = _validate_bounds(self.target_bounds, "target_bounds")
        if not _bounds_contains_bounds(movement, target_bounds):
            raise ValueError("target_bounds must be contained by movement_bounds")
        if not target_bounds.contains(self.target):
            raise ValueError("target must be inside target_bounds")
        if not _is_int(self.expected_pid) or self.expected_pid <= 0:
            raise ValueError("expected_pid must be a positive integer")
        if not isinstance(self.button, MouseButton):
            raise TypeError("button must be MouseButton")


@dataclass(frozen=True, slots=True)
class ApprovedKeyIntent:
    intent_id: str
    purpose: InputPurpose
    key: str
    expected_pid: int
    hold_millis: int = 50

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "intent_id", _validate_identifier(self.intent_id, "intent_id")
        )
        if self.purpose is not InputPurpose.GAMEPLAY_KEY:
            raise ValueError("a key intent must use the gameplay_key purpose")
        if not isinstance(self.key, str):
            raise TypeError("key must be a string")
        normalized = self.key.strip().upper()
        if not _SAFE_KEY.fullmatch(normalized):
            raise ValueError("key must be one bounded symbolic key name")
        object.__setattr__(self, "key", normalized)
        if not _is_int(self.expected_pid) or self.expected_pid <= 0:
            raise ValueError("expected_pid must be a positive integer")
        if not _is_int(self.hold_millis) or not 1 <= self.hold_millis <= 250:
            raise ValueError("hold_millis must be between 1 and 250")


@dataclass(frozen=True, slots=True)
class InputValidation:
    allowed: bool
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("allowed must be bool")
        object.__setattr__(self, "reason", _clean_text(self.reason))
        if not self.reason:
            raise ValueError("reason must be non-empty")

    @classmethod
    def allow(cls, reason: str = "fresh_input_evidence_valid") -> "InputValidation":
        return cls(True, reason)

    @classmethod
    def deny(cls, reason: str) -> "InputValidation":
        return cls(False, reason)


@dataclass(frozen=True, slots=True)
class PointerActivationDecision:
    validation: InputValidation
    activation: PointerActivation | None

    def __post_init__(self) -> None:
        if not isinstance(self.validation, InputValidation):
            raise TypeError("validation must be InputValidation")
        if self.validation.allowed:
            if not isinstance(self.activation, PointerActivation):
                raise ValueError("an allowed activation decision requires an activation")
        elif self.activation is not None:
            raise ValueError("a denied activation decision must not select an activation")

    @classmethod
    def direct(cls, reason: str) -> "PointerActivationDecision":
        return cls(InputValidation.allow(reason), PointerActivation.DIRECT_LEFT)

    @classmethod
    def context(cls, reason: str) -> "PointerActivationDecision":
        return cls(InputValidation.allow(reason), PointerActivation.CONTEXT_MENU)

    @classmethod
    def deny(cls, reason: str) -> "PointerActivationDecision":
        return cls(InputValidation.deny(reason), None)


PointerValidator: TypeAlias = Callable[
    [ApprovedPointerIntent, ScreenPoint], InputValidation
]
KeyValidator: TypeAlias = Callable[[ApprovedKeyIntent], InputValidation]
ContextRowResolver: TypeAlias = Callable[[], ApprovedPointerIntent]
PointerPlanner: TypeAlias = Callable[..., PointerMotionPlan]
PointerActivationValidator: TypeAlias = Callable[
    [ApprovedPointerIntent, ScreenPoint], PointerActivationDecision
]


@dataclass(frozen=True, slots=True)
class CommandEvidence:
    command_id: str
    sequence: int
    command: str
    status: str
    write_ok: bool
    ack_received: bool
    accepted: bool
    response_token: str | None = None
    payload_token: str | None = None
    error: str | None = None
    timeout_classification: str | None = None
    retry_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.command_id, str) or not _COMMAND_ID.fullmatch(
            self.command_id
        ):
            raise ValueError("command_id must be a stable command ledger identifier")
        if not _is_int(self.sequence) or self.sequence <= 0:
            raise ValueError("sequence must be a positive integer")
        command = _clean_text(self.command).upper()
        status = _clean_text(self.status).upper()
        if not command or not status:
            raise ValueError("command and status must be non-empty")
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "status", status)
        for field_name in ("write_ok", "ack_received", "accepted"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be bool")
        for field_name in ("response_token", "payload_token"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string or None")
        if self.ack_received and self.response_token is None and self.payload_token is None:
            raise ValueError("acknowledged command must retain final firmware ACK evidence")
        if not self.ack_received and (
            self.response_token is not None or self.payload_token is not None
        ):
            raise ValueError("unacknowledged command cannot contain firmware ACK tokens")
        if self.accepted and (
            self.status != "PASS" or not self.write_ok or not self.ack_received
        ):
            raise ValueError("accepted command must be a written, acknowledged PASS")
        for field_name in ("error", "timeout_classification"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string or None")
            if value is not None:
                object.__setattr__(self, field_name, _clean_text(value) or None)
        if not _is_int(self.retry_count) or self.retry_count < 0:
            raise ValueError("retry_count must be a non-negative integer")

    @property
    def successful(self) -> bool:
        return (
            self.status == "PASS"
            and self.write_ok
            and self.ack_received
            and self.accepted
            and self.error is None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "commandId": self.command_id,
            "sequence": self.sequence,
            "command": self.command,
            "status": self.status,
            "writeOk": self.write_ok,
            "ackReceived": self.ack_received,
            "accepted": self.accepted,
            "firmwareAck": (
                {
                    "responseToken": self.response_token,
                    "payloadToken": self.payload_token,
                }
                if self.ack_received
                else None
            ),
            "error": self.error,
            "timeoutClassification": self.timeout_classification,
            "retryCount": self.retry_count,
        }


@dataclass(frozen=True, slots=True)
class FirmwareSafetyStatus:
    armed: bool
    keys_down: int
    mouse_buttons_down: int

    def __post_init__(self) -> None:
        if not isinstance(self.armed, bool):
            raise TypeError("armed must be bool")
        if not _is_int(self.keys_down) or self.keys_down < 0:
            raise ValueError("keys_down must be a non-negative integer")
        if not _is_int(self.mouse_buttons_down) or self.mouse_buttons_down < 0:
            raise ValueError("mouse_buttons_down must be a non-negative integer")

    @property
    def safe(self) -> bool:
        return (
            self.armed is False
            and self.keys_down == 0
            and self.mouse_buttons_down == 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "armed": self.armed,
            "keysDown": self.keys_down,
            "mouseButtonsDown": self.mouse_buttons_down,
        }


def _wire_proof_complete(commands: tuple[CommandEvidence, ...]) -> bool:
    names = tuple(command.command for command in commands)
    try:
        arm_index = names.index("ARM")
        stop_index = max(index for index, name in enumerate(names) if name == "STOP_ALL")
        disarm_index = max(index for index, name in enumerate(names) if name == "DISARM")
        status_index = max(index for index, name in enumerate(names) if name == "STATUS")
    except (ValueError, StopIteration):
        return False
    activation_complete = (
        "KEY_PRESS" in names[arm_index + 1 : stop_index]
        or (
            "MOUSE_DOWN" in names[arm_index + 1 : stop_index]
            and "MOUSE_UP" in names[arm_index + 1 : stop_index]
        )
    )
    return (
        activation_complete
        and arm_index < stop_index < disarm_index < status_index
        and status_index == len(names) - 1
    )


@dataclass(frozen=True, slots=True)
class InputReceipt:
    transaction_id: str
    mode: str
    intent_ids: tuple[str, ...]
    status: str
    reason: str
    connected: bool
    arm_acknowledged: bool
    stop_all_acknowledged: bool
    disarm_acknowledged: bool
    firmware_status_acknowledged: bool
    firmware_status: FirmwareSafetyStatus | None
    commands: tuple[CommandEvidence, ...]
    unresolved_command_count: int
    failed_command_count: int
    ack_missing_count: int
    ledger_complete: bool
    ledger_closed: bool
    backend_closed: bool
    context_cancel_attempted: bool = False
    context_cancel_acknowledged: bool = False
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "transaction_id",
            _validate_identifier(self.transaction_id, "transaction_id"),
        )
        if self.mode not in {"pointer", "key", "context_menu", "adaptive_pointer"}:
            raise ValueError("mode is unsupported")
        if not isinstance(self.intent_ids, tuple) or not self.intent_ids:
            raise TypeError("intent_ids must be a non-empty tuple")
        object.__setattr__(
            self,
            "intent_ids",
            tuple(
                _validate_identifier(intent_id, "intent_id")
                for intent_id in self.intent_ids
            ),
        )
        if self.status not in {"PASS", "BLOCKED", "ERROR"}:
            raise ValueError("status is unsupported")
        reason = _clean_text(self.reason)
        if not reason:
            raise ValueError("reason must be non-empty")
        object.__setattr__(self, "reason", reason)
        for field_name in (
            "connected",
            "arm_acknowledged",
            "stop_all_acknowledged",
            "disarm_acknowledged",
            "firmware_status_acknowledged",
            "ledger_complete",
            "ledger_closed",
            "backend_closed",
            "context_cancel_attempted",
            "context_cancel_acknowledged",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be bool")
        if self.firmware_status is not None and not isinstance(
            self.firmware_status, FirmwareSafetyStatus
        ):
            raise TypeError("firmware_status must be FirmwareSafetyStatus or None")
        if not isinstance(self.commands, tuple) or not all(
            isinstance(command, CommandEvidence) for command in self.commands
        ):
            raise TypeError("commands must be a tuple of CommandEvidence values")
        if len(self.commands) > MAX_LEDGER_ENTRIES:
            raise ValueError("commands exceed the bounded receipt ledger limit")
        sequences = tuple(command.sequence for command in self.commands)
        if sequences != tuple(sorted(sequences)) or len(set(sequences)) != len(
            sequences
        ):
            raise ValueError("commands must have strictly ordered unique sequences")
        if len({command.command_id for command in self.commands}) != len(
            self.commands
        ):
            raise ValueError("commands must have unique command identifiers")
        for field_name in (
            "unresolved_command_count",
            "failed_command_count",
            "ack_missing_count",
        ):
            value = getattr(self, field_name)
            if not _is_int(value) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if not isinstance(self.errors, tuple) or not all(
            isinstance(error, str) and error for error in self.errors
        ):
            raise TypeError("errors must be a tuple of non-empty strings")
        object.__setattr__(
            self, "errors", tuple(_clean_text(error) for error in self.errors)
        )
        calculated_unresolved = sum(
            command.status not in {
                "PASS",
                "REJECTED",
                "UNEXPECTED_RESPONSE",
                "WRITE_FAIL",
                "ACK_TIMEOUT_OR_READ_FAIL",
            }
            for command in self.commands
        )
        calculated_failed = sum(
            command.status != "PASS" for command in self.commands
        )
        calculated_missing = sum(
            not command.ack_received for command in self.commands
        )
        if (
            self.unresolved_command_count != calculated_unresolved
            or self.failed_command_count != calculated_failed
            or self.ack_missing_count != calculated_missing
        ):
            raise ValueError("receipt command counters do not match its ledger")

    @property
    def wire_proof_complete(self) -> bool:
        return _wire_proof_complete(self.commands)

    @property
    def successful(self) -> bool:
        return (
            self.status == "PASS"
            and self.connected
            and self.arm_acknowledged
            and self.stop_all_acknowledged
            and self.disarm_acknowledged
            and self.firmware_status_acknowledged
            and self.firmware_status is not None
            and self.firmware_status.safe
            and self.unresolved_command_count == 0
            and self.failed_command_count == 0
            and self.ack_missing_count == 0
            and self.ledger_complete
            and self.ledger_closed
            and self.backend_closed
            and self.wire_proof_complete
            and not self.errors
            and all(command.successful for command in self.commands)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "input_transaction_receipt.v1",
            "transactionId": self.transaction_id,
            "mode": self.mode,
            "intentIds": list(self.intent_ids),
            "status": self.status,
            "reason": self.reason,
            "successful": self.successful,
            "wireProofComplete": self.wire_proof_complete,
            "connected": self.connected,
            "armAcknowledged": self.arm_acknowledged,
            "stopAllAcknowledged": self.stop_all_acknowledged,
            "disarmAcknowledged": self.disarm_acknowledged,
            "firmwareStatusAcknowledged": self.firmware_status_acknowledged,
            "firmwareStatus": (
                self.firmware_status.to_dict()
                if self.firmware_status is not None
                else None
            ),
            "commands": [command.to_dict() for command in self.commands],
            "unresolvedCommandCount": self.unresolved_command_count,
            "failedCommandCount": self.failed_command_count,
            "ackMissingCount": self.ack_missing_count,
            "ledgerComplete": self.ledger_complete,
            "ledgerClosed": self.ledger_closed,
            "backendClosed": self.backend_closed,
            "contextCancelAttempted": self.context_cancel_attempted,
            "contextCancelAcknowledged": self.context_cancel_acknowledged,
            "errors": list(self.errors),
        }


class _Backend(Protocol):
    def _begin_command_ledger(self) -> None: ...

    def _command_evidence(self) -> dict[str, Any]: ...

    def _end_command_ledger(self) -> dict[str, Any]: ...

    def _connect(self) -> None: ...

    def _arm(self) -> dict[str, Any]: ...

    def _current_position(self) -> tuple[int, int]: ...

    def _move_relative(self, dx: int, dy: int) -> dict[str, Any]: ...

    def _assert_foreground(
        self,
        allowed_titles: list[str] | tuple[str, ...],
        *,
        expected_pid: int | None = None,
    ) -> dict[str, Any]: ...

    def _mouse_down(self, *, button: str = "left") -> None: ...

    def _mouse_up(self, *, button: str = "left") -> None: ...

    def _press(self, key: str, hold_millis: int = 50) -> None: ...

    def _stop_all(self) -> dict[str, Any]: ...

    def _disarm(self) -> dict[str, Any]: ...

    def _firmware_status(self) -> dict[str, Any]: ...

    def _close(self) -> None: ...


class _TransactionAbort(RuntimeError):
    def __init__(self, reason: str, *, blocked: bool = False) -> None:
        super().__init__(_clean_text(reason, fallback="input_transaction_aborted"))
        self.blocked = blocked


def _command_from_mapping(raw: object) -> CommandEvidence:
    if not isinstance(raw, Mapping):
        raise ValueError("command evidence record must be an object")
    command_id = raw.get("commandId")
    if not isinstance(command_id, str) or not _COMMAND_ID.fullmatch(command_id):
        raise ValueError("command evidence has an invalid commandId")
    sequence = raw.get("sequence")
    if not _is_int(sequence) or sequence <= 0:
        raise ValueError("command evidence has an invalid sequence")
    command = _clean_text(raw.get("command"), fallback="UNKNOWN").upper()
    if not command:
        raise ValueError("command evidence has an empty command")
    status = _clean_text(raw.get("status"), fallback="PENDING").upper()
    bool_fields: dict[str, bool] = {}
    for source, destination in (
        ("writeOk", "write_ok"),
        ("ackReceived", "ack_received"),
        ("accepted", "accepted"),
    ):
        value = raw.get(source)
        if not isinstance(value, bool):
            raise ValueError(f"command evidence {source} must be bool")
        bool_fields[destination] = value
    firmware_ack = raw.get("firmwareAck")
    response_token: str | None = None
    payload_token: str | None = None
    if firmware_ack is not None:
        if not isinstance(firmware_ack, Mapping):
            raise ValueError("firmwareAck must be an object or null")
        response = firmware_ack.get("responseToken")
        payload = firmware_ack.get("payloadToken")
        response_token = _clean_text(response) or None
        payload_token = _clean_text(payload) or None
    retry_count = raw.get("retryCount", 0)
    if not _is_int(retry_count) or retry_count < 0:
        raise ValueError("retryCount must be a non-negative integer")
    error = _clean_text(raw.get("error")) or None
    timeout = _clean_text(raw.get("timeoutClassification")) or None
    return CommandEvidence(
        command_id=command_id,
        sequence=sequence,
        command=command,
        status=status,
        write_ok=bool_fields["write_ok"],
        ack_received=bool_fields["ack_received"],
        accepted=bool_fields["accepted"],
        response_token=response_token,
        payload_token=payload_token,
        error=error,
        timeout_classification=timeout,
        retry_count=retry_count,
    )


@dataclass(slots=True)
class _EvidenceSnapshot:
    commands: tuple[CommandEvidence, ...]
    unresolved_count: int
    failed_count: int
    ack_missing_count: int


def _evidence_from_mapping(
    raw: object, *, max_entries: int
) -> _EvidenceSnapshot:
    if not isinstance(raw, Mapping):
        raise ValueError("command ledger evidence must be an object")
    if raw.get("schema") != COMMAND_LEDGER_SCHEMA:
        raise ValueError("command ledger evidence has an unsupported schema")
    records = raw.get("records")
    if not isinstance(records, list):
        raise ValueError("command ledger records must be a list")
    if len(records) > max_entries:
        raise ValueError(
            f"command ledger exceeded the bounded limit of {max_entries} records"
        )
    commands = tuple(_command_from_mapping(record) for record in records)
    sequences = tuple(command.sequence for command in commands)
    identifiers = tuple(command.command_id for command in commands)
    if sequences != tuple(sorted(sequences)) or len(set(sequences)) != len(sequences):
        raise ValueError("command ledger sequence must be strictly ordered and unique")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("command ledger commandId values must be unique")

    counters: dict[str, int] = {}
    for field in ("unresolvedCount", "failedCount", "ackMissingCount"):
        value = raw.get(field)
        if not _is_int(value) or value < 0:
            raise ValueError(f"command ledger {field} must be a non-negative integer")
        counters[field] = value
    calculated_unresolved = sum(
        command.status not in {
            "PASS",
            "REJECTED",
            "UNEXPECTED_RESPONSE",
            "WRITE_FAIL",
            "ACK_TIMEOUT_OR_READ_FAIL",
        }
        for command in commands
    )
    calculated_failed = sum(command.status != "PASS" for command in commands)
    calculated_missing = sum(not command.ack_received for command in commands)
    if counters["unresolvedCount"] != calculated_unresolved:
        raise ValueError("command ledger unresolvedCount does not match its records")
    if counters["failedCount"] != calculated_failed:
        raise ValueError("command ledger failedCount does not match its records")
    if counters["ackMissingCount"] != calculated_missing:
        raise ValueError("command ledger ackMissingCount does not match its records")
    return _EvidenceSnapshot(
        commands=commands,
        unresolved_count=counters["unresolvedCount"],
        failed_count=counters["failedCount"],
        ack_missing_count=counters["ackMissingCount"],
    )


class _Transaction:
    def __init__(
        self,
        backend: _Backend,
        *,
        max_ledger_entries: int,
    ) -> None:
        self.backend = backend
        self.max_ledger_entries = max_ledger_entries
        self.snapshot = _EvidenceSnapshot((), 0, 0, 0)
        self.errors: list[str] = []
        self.ledger_complete = True
        self.pointer_plan_count = 0
        self.pointer_step_count = 0
        self.pointer_no_effect_count = 0

    def add_error(self, reason: object) -> None:
        text = _clean_text(reason, fallback="unknown_input_error")
        if text and text not in self.errors:
            self.errors.append(text)

    def sync(self) -> bool:
        try:
            candidate = _evidence_from_mapping(
                self.backend._command_evidence(),
                max_entries=self.max_ledger_entries,
            )
            prior = self.snapshot.commands
            if (
                len(candidate.commands) < len(prior)
                or candidate.commands[: len(prior)] != prior
            ):
                raise ValueError(
                    "command ledger is not an append-only monotonic superset"
                )
            self.snapshot = candidate
            return True
        except Exception as error:  # noqa: BLE001 - fail closed at evidence boundary
            self.ledger_complete = False
            self.add_error(
                f"command_evidence_invalid: {type(error).__name__}: {error}"
            )
            return False

    def invoke(
        self,
        expected_command: str,
        operation: Callable[[], Any],
    ) -> tuple[bool, Any | None]:
        before_ids = {command.command_id for command in self.snapshot.commands}
        value: Any | None = None
        operation_error: Exception | None = None
        try:
            value = operation()
        except Exception as error:  # noqa: BLE001 - receipt preserves the failure
            operation_error = error
        evidence_ok = self.sync()
        new_commands = tuple(
            command
            for command in self.snapshot.commands
            if command.command_id not in before_ids
        )
        expected = expected_command.upper()
        matching = tuple(command for command in new_commands if command.command == expected)
        acknowledged = bool(
            evidence_ok
            and matching
            and all(command.successful for command in new_commands)
            and matching[-1].successful
        )
        if operation_error is not None:
            self.add_error(
                f"{expected.lower()}_failed: {type(operation_error).__name__}: {operation_error}"
            )
            acknowledged = False
        elif not matching:
            self.add_error(f"{expected.lower()}_ack_evidence_missing")
        elif not acknowledged:
            self.add_error(f"{expected.lower()}_not_acknowledged")
        return acknowledged, value


@dataclass(slots=True)
class _TransactionState:
    transaction_id: str
    mode: str
    intent_ids: tuple[str, ...]
    connected: bool = False
    arm_acknowledged: bool = False
    stop_all_acknowledged: bool = False
    disarm_acknowledged: bool = False
    firmware_status_acknowledged: bool = False
    firmware_status: FirmwareSafetyStatus | None = None
    ledger_closed: bool = False
    backend_closed: bool = False
    context_cancel_attempted: bool = False
    context_cancel_acknowledged: bool = False
    body_status: str = "ERROR"
    body_reason: str = "input_transaction_not_executed"


class InputCoordinator:
    """Own exactly one Arduino connection from explicit connect through proof.

    Callers submit immutable, already-approved intents and narrow fresh-evidence
    callbacks.  The transport is never exposed to those callbacks.  Every
    connected attempt ends with acknowledged STOP_ALL, DISARM, wire STATUS,
    ledger reconciliation, and close.
    """

    def __init__(
        self,
        backend_factory: Callable[[], _Backend],
        *,
        pointer_planner: PointerPlanner = plan_pointer_motion,
        pointer_limits: PointerMotionLimits = DEFAULT_POINTER_MOTION_LIMITS,
        pointer_timestep_seconds: float = DEFAULT_POINTER_TIMESTEP_SECONDS,
        click_hold_seconds: float = DEFAULT_CLICK_HOLD_SECONDS,
        max_pointer_steps: int = MAX_POINTER_STEPS,
        max_correction_plans: int = MAX_POINTER_FEEDBACK_PLANS - 1,
        max_ledger_entries: int = MAX_LEDGER_ENTRIES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not callable(backend_factory):
            raise TypeError("backend_factory must be callable")
        if not callable(pointer_planner):
            raise TypeError("pointer_planner must be callable")
        if not isinstance(pointer_limits, PointerMotionLimits):
            raise TypeError("pointer_limits must be PointerMotionLimits")
        if isinstance(pointer_timestep_seconds, bool) or not isinstance(
            pointer_timestep_seconds, (int, float)
        ):
            raise TypeError("pointer_timestep_seconds must be a positive number")
        if not math.isfinite(float(pointer_timestep_seconds)) or float(
            pointer_timestep_seconds
        ) <= 0.0:
            raise ValueError("pointer_timestep_seconds must be finite and positive")
        if isinstance(click_hold_seconds, bool) or not isinstance(
            click_hold_seconds, (int, float)
        ):
            raise TypeError("click_hold_seconds must be a non-negative number")
        if not math.isfinite(float(click_hold_seconds)) or float(
            click_hold_seconds
        ) < 0.0:
            raise ValueError("click_hold_seconds must be finite and non-negative")
        if (
            not _is_int(max_pointer_steps)
            or not 1 <= max_pointer_steps <= MAX_POINTER_STEPS
        ):
            raise ValueError(
                f"max_pointer_steps must be between 1 and {MAX_POINTER_STEPS}"
            )
        if (
            not _is_int(max_correction_plans)
            or not 0 <= max_correction_plans < MAX_POINTER_FEEDBACK_PLANS
        ):
            raise ValueError(
                "max_correction_plans must be between 0 and "
                f"{MAX_POINTER_FEEDBACK_PLANS - 1}"
            )
        if (
            not _is_int(max_ledger_entries)
            or not 16 <= max_ledger_entries <= MAX_LEDGER_ENTRIES
        ):
            raise ValueError(
                f"max_ledger_entries must be between 16 and {MAX_LEDGER_ENTRIES}"
            )
        self._backend_factory = backend_factory
        self._pointer_planner = pointer_planner
        self._pointer_limits = pointer_limits
        self._pointer_timestep_seconds = float(pointer_timestep_seconds)
        self._click_hold_seconds = float(click_hold_seconds)
        self._max_pointer_steps = max_pointer_steps
        self._max_correction_plans = max_correction_plans
        self._max_ledger_entries = max_ledger_entries
        self._sleep = sleep
        self._transaction_lock = threading.Lock()
        self._transaction_sequence = 0

    @classmethod
    def for_arduino_port(
        cls,
        arduino_port: str,
        *,
        serial_owner: str = "osrs-input-coordinator",
        **coordinator_options: Any,
    ) -> "InputCoordinator":
        """Build the sole production Arduino backend factory.

        Gameplay and login callers receive only the coordinator and therefore
        cannot open an independent serial session.
        """

        if not isinstance(arduino_port, str) or not arduino_port.strip():
            raise ValueError("arduino_port must be a non-empty string")
        owner = _validate_identifier(serial_owner, "serial_owner")
        port = arduino_port.strip()
        from .arduino import _ArduinoHIDTransport

        return cls(
            lambda: _ArduinoHIDTransport(
                port=port,
                serial_owner=owner,
                fail_closed=True,
            ),
            **coordinator_options,
        )

    def execute_pointer(
        self,
        intent: ApprovedPointerIntent,
        *,
        validate: PointerValidator,
    ) -> InputReceipt:
        if not isinstance(intent, ApprovedPointerIntent):
            raise TypeError("intent must be ApprovedPointerIntent")
        if not callable(validate):
            raise TypeError("validate must be callable")

        def body(transaction: _Transaction) -> None:
            actual = self._move(transaction, intent)
            self._validate_pointer(
                transaction,
                intent,
                actual,
                validate,
            )
            self._click(
                transaction,
                intent.button,
                intent=intent,
                actual=actual,
            )

        return self._execute("pointer", (intent.intent_id,), body)

    def execute_key(
        self,
        intent: ApprovedKeyIntent,
        *,
        validate: KeyValidator,
    ) -> InputReceipt:
        if not isinstance(intent, ApprovedKeyIntent):
            raise TypeError("intent must be ApprovedKeyIntent")
        if not callable(validate):
            raise TypeError("validate must be callable")

        def body(transaction: _Transaction) -> None:
            def validate_key() -> InputValidation:
                self._assert_foreground(
                    transaction.backend, intent.expected_pid
                )
                decision = validate(intent)
                self._assert_foreground(
                    transaction.backend, intent.expected_pid
                )
                return decision

            self._validated_under_firmware_lease(
                transaction,
                validate_key,
                self._require_validation,
            )
            self._assert_foreground(transaction.backend, intent.expected_pid)
            self._assert_firmware_armed(
                transaction, phase="before_key_activation"
            )
            acknowledged, _ = transaction.invoke(
                "KEY_PRESS",
                lambda: transaction.backend._press(intent.key, intent.hold_millis),
            )
            if not acknowledged:
                raise _TransactionAbort("key_press_not_acknowledged")

        return self._execute("key", (intent.intent_id,), body)

    def execute_context_menu(
        self,
        open_intent: ApprovedPointerIntent,
        *,
        validate_hover: PointerValidator,
        resolve_row: ContextRowResolver,
        validate_row: PointerValidator,
    ) -> InputReceipt:
        if not isinstance(open_intent, ApprovedPointerIntent):
            raise TypeError("open_intent must be ApprovedPointerIntent")
        if (
            open_intent.purpose is not InputPurpose.CONTEXT_MENU
            or open_intent.button is not MouseButton.RIGHT
        ):
            raise ValueError("open_intent must be a right-button context_menu intent")
        if not all(callable(callback) for callback in (validate_hover, resolve_row, validate_row)):
            raise TypeError("context callbacks must be callable")

        row_id: list[str] = []

        def body(transaction: _Transaction) -> None:
            def mark_menu_possibly_open() -> None:
                state.context_cancel_attempted = False
                menu_open[0] = True

            actual = self._move(transaction, open_intent)
            self._validate_pointer(
                transaction,
                open_intent,
                actual,
                validate_hover,
            )
            self._click(
                transaction,
                MouseButton.RIGHT,
                intent=open_intent,
                actual=actual,
                on_down_written=mark_menu_possibly_open,
            )

            try:
                row_intent = resolve_row()
            except Exception as error:  # evidence resolver blocks, then ESCs
                raise _TransactionAbort(
                    f"context_row_resolution_blocked: {error}", blocked=True
                ) from error
            if not isinstance(row_intent, ApprovedPointerIntent):
                raise _TransactionAbort(
                    "context row resolver did not return ApprovedPointerIntent",
                    blocked=True,
                )
            if (
                row_intent.purpose is not InputPurpose.CONTEXT_ROW
                or row_intent.button is not MouseButton.LEFT
                or row_intent.expected_pid != open_intent.expected_pid
            ):
                raise _TransactionAbort(
                    "resolved context row intent is not compatible with the open menu",
                    blocked=True,
                )
            row_id.append(row_intent.intent_id)
            actual = self._move(transaction, row_intent)
            self._validate_pointer(
                transaction,
                row_intent,
                actual,
                validate_row,
            )
            self._click(
                transaction,
                MouseButton.LEFT,
                intent=row_intent,
                actual=actual,
            )
            menu_open[0] = False

        state = _TransactionState("", "context_menu", (open_intent.intent_id,))
        menu_open = [False]
        receipt = self._execute(
            "context_menu",
            (open_intent.intent_id,),
            body,
            external_state=state,
            context_menu_open=menu_open,
            context_pid=open_intent.expected_pid,
        )
        if not row_id:
            return receipt
        # The receipt stays immutable; rebuild it with the dynamically resolved
        # row intent identifier after the single transaction has closed.
        return InputReceipt(
            transaction_id=receipt.transaction_id,
            mode=receipt.mode,
            intent_ids=(open_intent.intent_id, row_id[0]),
            status=receipt.status,
            reason=receipt.reason,
            connected=receipt.connected,
            arm_acknowledged=receipt.arm_acknowledged,
            stop_all_acknowledged=receipt.stop_all_acknowledged,
            disarm_acknowledged=receipt.disarm_acknowledged,
            firmware_status_acknowledged=receipt.firmware_status_acknowledged,
            firmware_status=receipt.firmware_status,
            commands=receipt.commands,
            unresolved_command_count=receipt.unresolved_command_count,
            failed_command_count=receipt.failed_command_count,
            ack_missing_count=receipt.ack_missing_count,
            ledger_complete=receipt.ledger_complete,
            ledger_closed=receipt.ledger_closed,
            backend_closed=receipt.backend_closed,
            context_cancel_attempted=receipt.context_cancel_attempted,
            context_cancel_acknowledged=receipt.context_cancel_acknowledged,
            errors=receipt.errors,
        )

    def execute_adaptive_pointer(
        self,
        intent: ApprovedPointerIntent,
        *,
        decide_activation: PointerActivationValidator,
        resolve_row: ContextRowResolver,
        validate_row: PointerValidator,
    ) -> InputReceipt:
        """Move once, then select direct-left or exact context-row activation.

        The decision callback runs on fresh post-move evidence.  A context
        branch remains inside this same connection, arm, ledger, receipt, and
        cleanup transaction.
        """

        if not isinstance(intent, ApprovedPointerIntent):
            raise TypeError("intent must be ApprovedPointerIntent")
        if intent.purpose not in {
            InputPurpose.GAMEPLAY_OBJECT,
            InputPurpose.GAMEPLAY_WIDGET,
        }:
            raise ValueError(
                "adaptive pointer intent must target a gameplay object or widget"
            )
        if not all(
            callable(callback)
            for callback in (decide_activation, resolve_row, validate_row)
        ):
            raise TypeError("adaptive pointer callbacks must be callable")

        row_id: list[str] = []
        menu_open = [False]
        state = _TransactionState("", "adaptive_pointer", (intent.intent_id,))

        def body(transaction: _Transaction) -> None:
            def mark_menu_possibly_open() -> None:
                menu_open[0] = True

            actual = self._move(transaction, intent)

            def validate_activation() -> PointerActivationDecision:
                self._assert_foreground(
                    transaction.backend, intent.expected_pid
                )
                self._assert_cursor_stable_in_target(
                    transaction.backend,
                    intent,
                    actual,
                    phase="before_activation_validation",
                )
                decision = decide_activation(intent, actual)
                self._assert_foreground(
                    transaction.backend, intent.expected_pid
                )
                self._assert_cursor_stable_in_target(
                    transaction.backend,
                    intent,
                    actual,
                    phase="after_activation_validation",
                )
                return decision

            decision = self._validated_under_firmware_lease(
                transaction,
                validate_activation,
                self._require_pointer_activation_decision,
            )
            if decision.activation is PointerActivation.DIRECT_LEFT:
                self._click(
                    transaction,
                    MouseButton.LEFT,
                    intent=intent,
                    actual=actual,
                )
                return
            if decision.activation is not PointerActivation.CONTEXT_MENU:
                raise _TransactionAbort("activation validator selected no input")

            self._click(
                transaction,
                MouseButton.RIGHT,
                intent=intent,
                actual=actual,
                on_down_written=mark_menu_possibly_open,
            )
            try:
                row_intent = resolve_row()
            except Exception as error:  # evidence resolver blocks, then ESCs
                raise _TransactionAbort(
                    f"context_row_resolution_blocked: {error}", blocked=True
                ) from error
            if not isinstance(row_intent, ApprovedPointerIntent):
                raise _TransactionAbort(
                    "context row resolver did not return ApprovedPointerIntent",
                    blocked=True,
                )
            if (
                row_intent.purpose is not InputPurpose.CONTEXT_ROW
                or row_intent.button is not MouseButton.LEFT
                or row_intent.expected_pid != intent.expected_pid
            ):
                raise _TransactionAbort(
                    "resolved context row intent is not compatible with the open menu",
                    blocked=True,
                )
            row_id.append(row_intent.intent_id)
            actual = self._move(transaction, row_intent)
            self._validate_pointer(
                transaction,
                row_intent,
                actual,
                validate_row,
            )
            self._click(
                transaction,
                MouseButton.LEFT,
                intent=row_intent,
                actual=actual,
            )
            menu_open[0] = False

        receipt = self._execute(
            "adaptive_pointer",
            (intent.intent_id,),
            body,
            external_state=state,
            context_menu_open=menu_open,
            context_pid=intent.expected_pid,
        )
        return (
            replace(receipt, intent_ids=(intent.intent_id, row_id[0]))
            if row_id
            else receipt
        )

    def _execute(
        self,
        mode: str,
        intent_ids: tuple[str, ...],
        body: Callable[[_Transaction], None],
        *,
        external_state: _TransactionState | None = None,
        context_menu_open: list[bool] | None = None,
        context_pid: int | None = None,
    ) -> InputReceipt:
        if not self._transaction_lock.acquire(blocking=False):
            raise RuntimeError("InputCoordinator already owns an active transaction")
        try:
            self._transaction_sequence += 1
            transaction_id = f"input-{self._transaction_sequence:08d}"
            state = external_state or _TransactionState(
                transaction_id, mode, intent_ids
            )
            state.transaction_id = transaction_id
            state.mode = mode
            state.intent_ids = intent_ids
            backend: _Backend | None = None
            transaction: _Transaction | None = None
            ledger_started = False
            connection_attempted = False
            try:
                backend = self._backend_factory()
                transaction = _Transaction(
                    backend, max_ledger_entries=self._max_ledger_entries
                )
                backend._begin_command_ledger()
                ledger_started = True
                if not transaction.sync() or transaction.snapshot.commands:
                    raise _TransactionAbort("command ledger did not begin empty")
                connection_attempted = True
                backend._connect()
                state.connected = True
                state.arm_acknowledged, _ = transaction.invoke("ARM", backend._arm)
                if not state.arm_acknowledged:
                    raise _TransactionAbort("arm_not_acknowledged")
                body(transaction)
                state.body_status = "PASS"
                state.body_reason = "input_transaction_executed"
            except _TransactionAbort as error:
                state.body_status = "BLOCKED" if error.blocked else "ERROR"
                state.body_reason = str(error)
                if transaction is not None:
                    transaction.add_error(str(error))
            except Exception as error:  # noqa: BLE001 - fail closed, then clean up
                state.body_status = "ERROR"
                state.body_reason = _clean_text(
                    f"{type(error).__name__}: {error}",
                    fallback="input_transaction_failed",
                )
                if transaction is not None:
                    transaction.add_error(state.body_reason)
            finally:
                if transaction is not None and (
                    state.connected or connection_attempted
                ):
                    if context_menu_open and context_menu_open[0]:
                        state.context_cancel_attempted = True
                        try:
                            if context_pid is None:
                                raise RuntimeError("context PID unavailable")
                            self._assert_foreground(backend, context_pid)
                            self._ensure_firmware_armed(transaction)
                            self._assert_foreground(backend, context_pid)
                            self._assert_firmware_armed(
                                transaction, phase="before_context_cancel"
                            )
                            state.context_cancel_acknowledged, _ = transaction.invoke(
                                "KEY_PRESS", lambda: backend._press("ESC")
                            )
                            if not state.context_cancel_acknowledged:
                                transaction.add_error(
                                    "context_cancel_not_acknowledged"
                                )
                        except Exception as error:  # noqa: BLE001
                            transaction.add_error(
                                f"context_cancel_failed: {type(error).__name__}: {error}"
                            )
                    state.stop_all_acknowledged, _ = transaction.invoke(
                        "STOP_ALL", backend._stop_all
                    )
                    state.disarm_acknowledged, _ = transaction.invoke(
                        "DISARM", backend._disarm
                    )
                    (
                        state.firmware_status_acknowledged,
                        raw_status,
                    ) = transaction.invoke("STATUS", backend._firmware_status)
                    if state.firmware_status_acknowledged:
                        try:
                            state.firmware_status = self._firmware_status(raw_status)
                            if not state.firmware_status.safe:
                                transaction.add_error("firmware_status_not_safe")
                        except Exception as error:  # noqa: BLE001
                            transaction.add_error(
                                f"firmware_status_invalid: {type(error).__name__}: {error}"
                            )
                            state.firmware_status_acknowledged = False
                if transaction is not None and ledger_started:
                    try:
                        ended_snapshot = _evidence_from_mapping(
                            backend._end_command_ledger(),
                            max_entries=self._max_ledger_entries,
                        )
                        if ended_snapshot != transaction.snapshot:
                            raise ValueError(
                                "final command ledger is not the exact monotonic snapshot"
                            )
                        transaction.snapshot = ended_snapshot
                        state.ledger_closed = True
                    except Exception as error:  # noqa: BLE001
                        transaction.ledger_complete = False
                        transaction.add_error(
                            f"command_ledger_close_failed: {type(error).__name__}: {error}"
                        )
                if backend is not None:
                    try:
                        backend._close()
                        state.backend_closed = True
                    except Exception as error:  # noqa: BLE001
                        if transaction is not None:
                            transaction.add_error(
                                f"backend_close_failed: {type(error).__name__}: {error}"
                            )
            return self._receipt(state, transaction)
        finally:
            self._transaction_lock.release()

    def _move(
        self, transaction: _Transaction, intent: ApprovedPointerIntent
    ) -> ScreenPoint:
        backend = transaction.backend
        self._ensure_firmware_armed(transaction)
        self._assert_foreground(backend, intent.expected_pid)
        start = self._current_position(backend)
        if not intent.movement_bounds.contains(start):
            raise _TransactionAbort(
                "cursor_start_outside_verified_movement_bounds", blocked=True
            )
        actual = start
        x_calibrated = False
        y_calibrated = False
        x_no_effect_retries = 0
        y_no_effect_retries = 0
        x_delayed_command = 0
        y_delayed_command = 0
        for plan_index in range(self._max_correction_plans + 1):
            if transaction.pointer_plan_count >= self._max_correction_plans + 1:
                raise _TransactionAbort(
                    "pointer transaction exceeds the total feedback plan limit"
                )
            x_in_target = (
                intent.target_bounds.x
                <= actual.x
                < intent.target_bounds.x + intent.target_bounds.width
            )
            y_in_target = (
                intent.target_bounds.y
                <= actual.y
                < intent.target_bounds.y + intent.target_bounds.height
            )
            command_dx = self._feedback_axis_command(
                remaining=0 if x_in_target else intent.target.x - actual.x,
                coordinate=actual.x,
                lower=intent.movement_bounds.x,
                upper=(
                    intent.movement_bounds.x
                    + intent.movement_bounds.width
                    - 1
                ),
                calibrated=x_calibrated,
                no_effect_retries=x_no_effect_retries,
                axis="x",
            )
            command_dy = self._feedback_axis_command(
                remaining=0 if y_in_target else intent.target.y - actual.y,
                coordinate=actual.y,
                lower=intent.movement_bounds.y,
                upper=(
                    intent.movement_bounds.y
                    + intent.movement_bounds.height
                    - 1
                ),
                calibrated=y_calibrated,
                no_effect_retries=y_no_effect_retries,
                axis="y",
            )
            command_dx, command_dy = self._clamp_feedback_waypoint_to_envelope(
                actual,
                command_dx,
                command_dy,
                intent.movement_bounds,
            )
            command_target = ScreenPoint(
                actual.x + command_dx,
                actual.y + command_dy,
            )
            plan = self._pointer_planner(
                actual,
                command_target,
                intent.movement_bounds,
                timestep_seconds=self._pointer_timestep_seconds,
                limits=self._pointer_limits,
            )
            if not isinstance(plan, PointerMotionPlan):
                raise _TransactionAbort("pointer planner returned an invalid plan")
            if (
                plan.start != actual
                or plan.target != command_target
                or plan.bounds != intent.movement_bounds
            ):
                raise _TransactionAbort(
                    "pointer plan does not match the feedback waypoint"
                )
            self._assert_plan_transfer_envelope(
                actual,
                plan,
                intent.movement_bounds,
            )
            if (
                transaction.pointer_step_count + len(plan.steps)
                > self._max_pointer_steps
            ):
                raise _TransactionAbort("pointer motion exceeds the total step limit")
            transaction.pointer_plan_count += 1

            for step in plan.steps:
                self._assert_foreground(backend, intent.expected_pid)
                if not intent.movement_bounds.contains(actual):
                    raise _TransactionAbort("cursor_left_verified_movement_bounds")
                self._assert_delayed_command_compatible(
                    delayed=x_delayed_command,
                    commanded=step.dx,
                    axis="x",
                )
                self._assert_delayed_command_compatible(
                    delayed=y_delayed_command,
                    commanded=step.dy,
                    axis="y",
                )
                self._assert_transfer_headroom(
                    actual,
                    step.dx + x_delayed_command,
                    step.dy + y_delayed_command,
                    intent.movement_bounds,
                )
                before = actual
                acknowledged, _ = transaction.invoke(
                    "MOVE",
                    lambda step=step: backend._move_relative(
                        step.dx, step.dy
                    ),
                )
                if not acknowledged:
                    raise _TransactionAbort("move_not_acknowledged")
                transaction.pointer_step_count += 1
                self._sleep(plan.timestep_seconds)
                actual = self._current_position(backend)
                self._assert_foreground(backend, intent.expected_pid)
                if not intent.movement_bounds.contains(actual):
                    raise _TransactionAbort("cursor_left_verified_movement_bounds")
                (
                    x_calibrated,
                    x_no_effect_retries,
                    x_delayed_command,
                ) = self._validate_axis_transfer(
                    transaction=transaction,
                    commanded=step.dx,
                    observed=actual.x - before.x,
                    calibrated=x_calibrated,
                    no_effect_retries=x_no_effect_retries,
                    delayed_command=x_delayed_command,
                    axis="x",
                )
                (
                    y_calibrated,
                    y_no_effect_retries,
                    y_delayed_command,
                ) = self._validate_axis_transfer(
                    transaction=transaction,
                    commanded=step.dy,
                    observed=actual.y - before.y,
                    calibrated=y_calibrated,
                    no_effect_retries=y_no_effect_retries,
                    delayed_command=y_delayed_command,
                    axis="y",
                )

            # Each planner trajectory ends at rest.  Give cursor feedback one
            # full deterministic timestep to settle before deciding whether a
            # bounded correction trajectory is needed.
            self._sleep(plan.timestep_seconds)
            settled = self._current_position(backend)
            self._assert_foreground(backend, intent.expected_pid)
            if settled != actual:
                (
                    x_calibrated,
                    x_no_effect_retries,
                    x_delayed_command,
                ) = self._validate_axis_transfer(
                    transaction=transaction,
                    commanded=0,
                    observed=settled.x - actual.x,
                    calibrated=x_calibrated,
                    no_effect_retries=x_no_effect_retries,
                    delayed_command=x_delayed_command,
                    axis="x",
                )
                (
                    y_calibrated,
                    y_no_effect_retries,
                    y_delayed_command,
                ) = self._validate_axis_transfer(
                    transaction=transaction,
                    commanded=0,
                    observed=settled.y - actual.y,
                    calibrated=y_calibrated,
                    no_effect_retries=y_no_effect_retries,
                    delayed_command=y_delayed_command,
                    axis="y",
                )
            actual = settled
            if not intent.movement_bounds.contains(actual):
                raise _TransactionAbort("cursor_left_verified_movement_bounds")
            # Device-pixel coordinates and integer Arduino HID deltas need not
            # share a one-pixel lattice (for example at 175% display scaling).
            # Accept only a fully settled plan endpoint inside the caller's
            # pre-verified target region. A zero-step plan can prove an already
            # stable point; a transient mid-trajectory crossing cannot.
            if intent.target_bounds.contains(actual):
                if x_delayed_command != 0 or y_delayed_command != 0:
                    raise _TransactionAbort(
                        "cursor_feedback_unresolved_delayed_command"
                    )
                break
            if plan_index >= self._max_correction_plans:
                raise _TransactionAbort(
                    "cursor_feedback_correction_limit_exceeded"
                )

        final = self._current_position(backend)
        if not intent.movement_bounds.contains(final):
            raise _TransactionAbort("cursor_final_position_outside_verified_bounds")
        if not intent.target_bounds.contains(final):
            raise _TransactionAbort("cursor_target_outside_verified_target_bounds")
        return final

    @staticmethod
    def _feedback_axis_command(
        *,
        remaining: int,
        coordinate: int,
        lower: int,
        upper: int,
        calibrated: bool,
        no_effect_retries: int,
        axis: str,
    ) -> int:
        if remaining == 0:
            return 0
        direction = 1 if remaining > 0 else -1
        if not calibrated:
            magnitude = 1 + no_effect_retries
        else:
            magnitude = max(
                1,
                abs(remaining) // MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT,
            )
            magnitude = max(magnitude, 1 + no_effect_retries)
            magnitude = min(magnitude, MAX_FEEDBACK_PLAN_AXIS_DELTA)
        clearance = upper - coordinate if direction > 0 else coordinate - lower
        safe_magnitude = (
            clearance // CURSOR_TRANSFER_HEADROOM_DEVICE_PX_PER_HID_COUNT
        )
        if safe_magnitude < 1:
            raise _TransactionAbort(
                f"cursor_transfer_headroom_insufficient_{axis}"
            )
        return direction * min(magnitude, safe_magnitude)

    @staticmethod
    def _clamp_feedback_waypoint_to_envelope(
        actual: ScreenPoint,
        dx: int,
        dy: int,
        bounds: ScreenBounds,
    ) -> tuple[int, int]:
        active_axes = int(dx != 0) + int(dy != 0)
        if active_axes == 0:
            return 0, 0
        margins = (
            actual.x - bounds.x,
            bounds.x + bounds.width - 1 - actual.x,
            actual.y - bounds.y,
            bounds.y + bounds.height - 1 - actual.y,
        )
        command_budget = (
            min(margins) // CURSOR_TRANSFER_HEADROOM_DEVICE_PX_PER_HID_COUNT
        )
        if command_budget < active_axes:
            raise _TransactionAbort(
                "cursor_bidirectional_transfer_headroom_insufficient"
            )
        per_axis_limit = command_budget // active_axes

        def clamp(delta: int) -> int:
            if delta == 0:
                return 0
            magnitude = min(abs(delta), per_axis_limit)
            return magnitude if delta > 0 else -magnitude

        return clamp(dx), clamp(dy)

    @staticmethod
    def _assert_plan_transfer_envelope(
        actual: ScreenPoint,
        plan: PointerMotionPlan,
        bounds: ScreenBounds,
    ) -> None:
        command_path = sum(
            max(abs(step.dx), abs(step.dy)) for step in plan.steps
        )
        required = (
            command_path * CURSOR_TRANSFER_HEADROOM_DEVICE_PX_PER_HID_COUNT
        )
        margins = (
            actual.x - bounds.x,
            bounds.x + bounds.width - 1 - actual.x,
            actual.y - bounds.y,
            bounds.y + bounds.height - 1 - actual.y,
        )
        if required > min(margins):
            raise _TransactionAbort(
                "pointer_plan_transfer_envelope_would_leave_bounds"
            )

    @staticmethod
    def _assert_transfer_headroom(
        actual: ScreenPoint,
        dx: int,
        dy: int,
        bounds: ScreenBounds,
    ) -> None:
        required = (
            max(abs(dx), abs(dy))
            * CURSOR_TRANSFER_HEADROOM_DEVICE_PX_PER_HID_COUNT
        )
        margins = (
            actual.x - bounds.x,
            bounds.x + bounds.width - 1 - actual.x,
            actual.y - bounds.y,
            bounds.y + bounds.height - 1 - actual.y,
        )
        if required > min(margins):
            raise _TransactionAbort(
                "relative_move_bidirectional_transfer_envelope_would_leave_bounds"
            )

    @staticmethod
    def _assert_delayed_command_compatible(
        *,
        delayed: int,
        commanded: int,
        axis: str,
    ) -> None:
        if (
            delayed != 0
            and commanded != 0
            and (delayed > 0) != (commanded > 0)
        ):
            raise _TransactionAbort(
                f"cursor_feedback_delayed_direction_mismatch_{axis}"
            )

    @staticmethod
    def _validate_axis_transfer(
        *,
        transaction: _Transaction,
        commanded: int,
        observed: int,
        calibrated: bool,
        no_effect_retries: int,
        delayed_command: int,
        axis: str,
    ) -> tuple[bool, int, int]:
        if commanded == 0:
            if delayed_command == 0:
                if observed != 0:
                    raise _TransactionAbort(
                        f"cursor_feedback_uncommanded_axis_{axis}"
                    )
                return calibrated, no_effect_retries, 0
            if observed == 0:
                return calibrated, no_effect_retries, delayed_command
            if (delayed_command > 0) != (observed > 0):
                raise _TransactionAbort(
                    f"cursor_feedback_delayed_direction_mismatch_{axis}"
                )
            if (
                abs(observed)
                > abs(delayed_command) * MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT
            ):
                raise _TransactionAbort(
                    f"cursor_transfer_gain_exceeded_{axis}:"
                    f"commanded=0:observed={observed}:"
                    f"delayed={delayed_command}:"
                    f"plan={transaction.pointer_plan_count}:"
                    f"step={transaction.pointer_step_count}"
                )
            return True, 0, 0
        if observed == 0:
            transaction.pointer_no_effect_count += 1
            if (
                transaction.pointer_no_effect_count
                > MAX_TRANSACTION_NO_EFFECT_EVENTS
            ):
                raise _TransactionAbort(
                    "cursor_feedback_no_effect_transaction_limit_exceeded"
                )
            if no_effect_retries < MAX_CONSECUTIVE_AXIS_NO_EFFECT_RETRIES:
                return False, no_effect_retries + 1, commanded
            raise _TransactionAbort(f"cursor_feedback_no_effect_{axis}")
        if (commanded > 0) != (observed > 0):
            raise _TransactionAbort(
                f"cursor_feedback_direction_mismatch_{axis}"
            )
        if delayed_command != 0 and (delayed_command > 0) != (commanded > 0):
            raise _TransactionAbort(
                f"cursor_feedback_delayed_direction_mismatch_{axis}"
            )
        supported_command = abs(commanded) + abs(delayed_command)
        if (
            abs(observed)
            > supported_command * MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT
        ):
            raise _TransactionAbort(
                f"cursor_transfer_gain_exceeded_{axis}:"
                f"commanded={commanded}:observed={observed}:"
                f"delayed={delayed_command}:"
                f"plan={transaction.pointer_plan_count}:"
                f"step={transaction.pointer_step_count}"
            )
        return True, 0, 0

    def _validate_pointer(
        self,
        transaction: _Transaction,
        intent: ApprovedPointerIntent,
        actual: ScreenPoint,
        validate: PointerValidator,
    ) -> None:
        backend = transaction.backend

        def validate_pointer() -> InputValidation:
            # Deliberately after all movement and immediately before activation.
            self._assert_foreground(backend, intent.expected_pid)
            self._assert_cursor_stable_in_target(
                backend,
                intent,
                actual,
                phase="before_pointer_validation",
            )
            decision = validate(intent, actual)
            self._assert_foreground(backend, intent.expected_pid)
            self._assert_cursor_stable_in_target(
                backend,
                intent,
                actual,
                phase="after_pointer_validation",
            )
            return decision

        self._validated_under_firmware_lease(
            transaction,
            validate_pointer,
            self._require_validation,
        )

    def _assert_cursor_stable_in_target(
        self,
        backend: _Backend,
        intent: ApprovedPointerIntent,
        actual: ScreenPoint,
        *,
        phase: str,
    ) -> None:
        current = self._current_position(backend)
        if current != actual:
            raise _TransactionAbort(f"cursor_changed_{phase}")
        if not intent.movement_bounds.contains(current):
            raise _TransactionAbort(f"cursor_left_verified_bounds_{phase}")
        if not intent.target_bounds.contains(current):
            raise _TransactionAbort(f"cursor_left_verified_target_{phase}")

    def _click(
        self,
        transaction: _Transaction,
        button: MouseButton,
        *,
        intent: ApprovedPointerIntent | None = None,
        actual: ScreenPoint | None = None,
        on_down_written: Callable[[], None] | None = None,
    ) -> None:
        self._assert_firmware_armed(
            transaction, phase="before_pointer_activation"
        )
        if intent is not None or actual is not None:
            if intent is None or actual is None:
                raise _TransactionAbort("pointer activation context is incomplete")
            self._assert_foreground(transaction.backend, intent.expected_pid)
            self._assert_cursor_stable_in_target(
                transaction.backend,
                intent,
                actual,
                phase="after_firmware_lease_check",
            )
        before_ids = {
            command.command_id for command in transaction.snapshot.commands
        }
        down_acknowledged, _ = transaction.invoke(
            "MOUSE_DOWN",
            lambda: transaction.backend._mouse_down(button=button.value),
        )
        if on_down_written is not None and any(
            command.command_id not in before_ids
            and command.command == "MOUSE_DOWN"
            and command.write_ok
            and command.status != "REJECTED"
            for command in transaction.snapshot.commands
        ):
            on_down_written()
        if down_acknowledged:
            self._sleep(self._click_hold_seconds)
        # Always attempt the matching release.  STOP_ALL remains the final
        # independent release path if either command loses its acknowledgement.
        up_acknowledged, _ = transaction.invoke(
            "MOUSE_UP",
            lambda: transaction.backend._mouse_up(button=button.value),
        )
        if not down_acknowledged or not up_acknowledged:
            raise _TransactionAbort("mouse_click_not_fully_acknowledged")

    def _ensure_firmware_armed(self, transaction: _Transaction) -> bool:
        acknowledged, raw_status = transaction.invoke(
            "STATUS", transaction.backend._firmware_status
        )
        if not acknowledged:
            raise _TransactionAbort("preactivation_status_not_acknowledged")
        try:
            status = self._firmware_status(raw_status)
        except (TypeError, ValueError) as error:
            raise _TransactionAbort(
                f"preactivation_status_invalid: {error}"
            ) from error
        if status.keys_down != 0 or status.mouse_buttons_down != 0:
            raise _TransactionAbort("preactivation_firmware_holds_input")
        if status.armed:
            return False

        arm_acknowledged, _ = transaction.invoke(
            "ARM", transaction.backend._arm
        )
        if not arm_acknowledged:
            raise _TransactionAbort("preactivation_rearm_not_acknowledged")
        acknowledged, raw_status = transaction.invoke(
            "STATUS", transaction.backend._firmware_status
        )
        if not acknowledged:
            raise _TransactionAbort(
                "preactivation_rearm_status_not_acknowledged"
            )
        try:
            status = self._firmware_status(raw_status)
        except (TypeError, ValueError) as error:
            raise _TransactionAbort(
                f"preactivation_rearm_status_invalid: {error}"
            ) from error
        if (
            not status.armed
            or status.keys_down != 0
            or status.mouse_buttons_down != 0
        ):
            raise _TransactionAbort("preactivation_rearm_not_safe")
        return True

    def _assert_firmware_armed(
        self, transaction: _Transaction, *, phase: str
    ) -> None:
        acknowledged, raw_status = transaction.invoke(
            "STATUS", transaction.backend._firmware_status
        )
        if not acknowledged:
            raise _TransactionAbort(f"{phase}_status_not_acknowledged")
        try:
            status = self._firmware_status(raw_status)
        except (TypeError, ValueError) as error:
            raise _TransactionAbort(f"{phase}_status_invalid: {error}") from error
        if (
            not status.armed
            or status.keys_down != 0
            or status.mouse_buttons_down != 0
        ):
            raise _TransactionAbort(f"{phase}_firmware_not_safe")

    def _validated_under_firmware_lease(
        self,
        transaction: _Transaction,
        validate: Callable[[], Any],
        require: Callable[[object], None],
    ) -> Any:
        self._ensure_firmware_armed(transaction)
        decision = validate()
        require(decision)
        if self._ensure_firmware_armed(transaction):
            decision = validate()
            require(decision)
            self._assert_firmware_armed(
                transaction, phase="after_firmware_revalidation"
            )
        return decision

    def _require_pointer_activation_decision(self, decision: object) -> None:
        if not isinstance(decision, PointerActivationDecision):
            raise _TransactionAbort(
                "activation validator returned an invalid result"
            )
        self._require_validation(decision.validation)

    @staticmethod
    def _require_validation(decision: object) -> None:
        if not isinstance(decision, InputValidation):
            raise _TransactionAbort("validator returned an invalid result")
        if not decision.allowed:
            raise _TransactionAbort(
                f"fresh_input_validation_denied: {decision.reason}", blocked=True
            )

    @staticmethod
    def _assert_foreground(backend: _Backend, expected_pid: int) -> None:
        backend._assert_foreground(("RuneLite",), expected_pid=expected_pid)

    @staticmethod
    def _current_position(backend: _Backend) -> ScreenPoint:
        raw = backend._current_position()
        if (
            not isinstance(raw, tuple)
            or len(raw) != 2
            or not _is_int(raw[0])
            or not _is_int(raw[1])
        ):
            raise _TransactionAbort("backend cursor position is invalid")
        return ScreenPoint(raw[0], raw[1])

    @staticmethod
    def _firmware_status(raw: object) -> FirmwareSafetyStatus:
        if not isinstance(raw, Mapping):
            raise ValueError("firmware STATUS payload must be an object")
        armed = raw.get("armed")
        keys_down = raw.get("keysDown")
        buttons_down = raw.get("mouseButtonsDown")
        if not isinstance(armed, bool):
            raise ValueError("firmware STATUS armed must be bool")
        if not _is_int(keys_down) or keys_down < 0:
            raise ValueError("firmware STATUS keysDown must be a non-negative integer")
        if not _is_int(buttons_down) or buttons_down < 0:
            raise ValueError(
                "firmware STATUS mouseButtonsDown must be a non-negative integer"
            )
        return FirmwareSafetyStatus(armed, keys_down, buttons_down)

    @staticmethod
    def _receipt(
        state: _TransactionState, transaction: _Transaction | None
    ) -> InputReceipt:
        snapshot = (
            transaction.snapshot
            if transaction is not None
            else _EvidenceSnapshot((), 0, 0, 0)
        )
        errors = tuple(transaction.errors if transaction is not None else ())
        cleanup_ok = (
            state.stop_all_acknowledged
            and state.disarm_acknowledged
            and state.firmware_status_acknowledged
            and state.firmware_status is not None
            and state.firmware_status.safe
            and state.ledger_closed
            and state.backend_closed
            and snapshot.unresolved_count == 0
            and snapshot.failed_count == 0
            and snapshot.ack_missing_count == 0
            and all(command.successful for command in snapshot.commands)
            and transaction is not None
            and transaction.ledger_complete
        )
        wire_proof_ok = _wire_proof_complete(snapshot.commands)
        if (
            state.body_status == "PASS"
            and cleanup_ok
            and wire_proof_ok
            and not errors
        ):
            status = "PASS"
            reason = "input_transaction_succeeded"
        elif state.body_status == "BLOCKED" and cleanup_ok:
            status = "BLOCKED"
            reason = state.body_reason
        else:
            status = "ERROR"
            reason = (
                errors[0]
                if errors
                else (
                    "wire_proof_incomplete"
                    if state.body_status == "PASS" and not wire_proof_ok
                    else state.body_reason
                )
            )
        return InputReceipt(
            transaction_id=state.transaction_id,
            mode=state.mode,
            intent_ids=state.intent_ids,
            status=status,
            reason=reason,
            connected=state.connected,
            arm_acknowledged=state.arm_acknowledged,
            stop_all_acknowledged=state.stop_all_acknowledged,
            disarm_acknowledged=state.disarm_acknowledged,
            firmware_status_acknowledged=state.firmware_status_acknowledged,
            firmware_status=state.firmware_status,
            commands=snapshot.commands,
            unresolved_command_count=snapshot.unresolved_count,
            failed_command_count=snapshot.failed_count,
            ack_missing_count=snapshot.ack_missing_count,
            ledger_complete=(
                transaction.ledger_complete if transaction is not None else False
            ),
            ledger_closed=state.ledger_closed,
            backend_closed=state.backend_closed,
            context_cancel_attempted=state.context_cancel_attempted,
            context_cancel_acknowledged=state.context_cancel_acknowledged,
            errors=errors,
        )

from __future__ import annotations

import math
from pathlib import Path
import re
import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, TypeAlias

from .input_capabilities import (
    InputCapabilities,
    InputOperation,
    RequiredInputCapabilities,
)
from .model import ScreenBounds, ScreenPoint
from .observability import (
    MAX_DURATION_MILLIS,
    ObservabilityEvidence,
    TimingEvidence,
    TimingPhase,
    WaitState,
    safe_elapsed_millis,
)
from .pointer import (
    DEFAULT_POINTER_MOTION_LIMITS,
    PointerMotionLimits,
    PointerMotionPlan,
    gameplay_pointer_safe_bounds,
    plan_pointer_motion,
)


COMMAND_LEDGER_SCHEMA = "arduino_command_ledger.v1"
MAX_LEDGER_ENTRIES = 2_048
MAX_POINTER_STEPS = 512
MAX_POINTER_FEEDBACK_PLANS = 64
MAX_GAMEPLAY_FEEDBACK_CORRECTION_PLANS = 2
MAX_CURSOR_REACQUISITION_FEEDBACK_CORRECTION_PLANS = (
    MAX_POINTER_FEEDBACK_PLANS - 1
)
MAX_FEEDBACK_PLAN_AXIS_DELTA = 64
MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT = 4
CURSOR_TRANSFER_HEADROOM_DEVICE_PX_PER_HID_COUNT = 8
MIN_TRANSFER_GAIN_SAMPLE_HID_COUNT = 4
TRANSFER_GAIN_ESTIMATE_HEADROOM = 1.10
CURSOR_REACQUISITION_NEUTRAL_INSET_DEVICE_PX = 64
CURSOR_REACQUISITION_NEUTRAL_RADIUS_DEVICE_PX = 8
AWT_NATIVE_OUTER_ORIGIN_TOLERANCE_DEVICE_PX = 1
DEFAULT_POINTER_TIMESTEP_SECONDS = 0.02
DEFAULT_CLICK_HOLD_SECONDS = 0.06
DELAYED_CURSOR_FEEDBACK_POLL_SECONDS = 0.02
DELAYED_CURSOR_FEEDBACK_ARRIVAL_TIMEOUT_SECONDS = 0.20
DELAYED_CURSOR_FEEDBACK_TOTAL_TIMEOUT_SECONDS = 0.24
DELAYED_CURSOR_FEEDBACK_STABLE_SAMPLES = 2
DELAYED_CURSOR_FEEDBACK_MAX_EXTRA_POLLS = 10
MAX_CURSOR_POSITION_SAMPLES = MAX_POINTER_STEPS * (
    DELAYED_CURSOR_FEEDBACK_MAX_EXTRA_POLLS + 4
) + 32
MIN_PHYSICAL_MOUSE_QUIET_SAMPLE_COUNT = 3
_COMMAND_ID = re.compile(r"^cmd-[0-9]{8,}$")
_SAFE_KEY = re.compile(r"^[A-Z0-9_]{1,16}$")
_ARM_SECRET = re.compile(r"(?i)(\bARM\s+)(\S+)")
MAX_CAMERA_HOLD_MILLIS = 600
MAX_CAMERA_WHEEL_STEP = 3


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


def read_arduino_lease_status(
    arduino_port: str,
    *,
    lock_dir: str | Path | None = None,
    now_millis: int | None = None,
) -> dict[str, Any]:
    """Inspect the shared serial lease without acquiring it or opening hardware.

    A stale or malformed lock record is reported as recoverable because the real
    coordinator would remove it while acquiring the same lease.  This function
    deliberately leaves the record untouched; its result is operator status,
    never permission to bypass the coordinator's authoritative acquisition.
    """

    port = _validate_identifier(arduino_port, "arduino_port")
    from .arduino import (
        DEFAULT_SERIAL_LOCK_STALE_MS,
        ArduinoSerialPortLock,
        _load_lock_payload,
        _pid_running,
    )

    probe = ArduinoSerialPortLock(
        port,
        owner="operator-read-only-lease-probe",
        lock_dir=lock_dir,
        timeout_ms=0,
    )
    path = probe.path
    if not path.exists():
        return {
            "schema": "arduino_lease_status.v1",
            "port": port,
            "status": "AVAILABLE",
            "available": True,
            "owner": None,
            "ownerPid": None,
            "ownerRunning": None,
            "ageMillis": None,
            "lockPath": str(path),
            "reason": "no serial lease record exists",
        }

    payload = _load_lock_payload(path)
    try:
        owner_pid = int(payload.get("pid"))
    except (TypeError, ValueError):
        owner_pid = None
    try:
        created_at = int(payload.get("createdAtMillis"))
    except (TypeError, ValueError):
        created_at = None
    current_millis = (
        int(round(time.time() * 1000))
        if now_millis is None
        else int(now_millis)
    )
    age_millis = (
        None if created_at is None else max(0, current_millis - created_at)
    )
    owner_running = _pid_running(owner_pid)
    record_valid = (
        payload.get("schema") == "arduino_serial_lock.v1"
        and str(payload.get("port") or "").casefold() == port.casefold()
        and owner_pid is not None
        and created_at is not None
    )
    recoverable = (
        not record_valid
        or (
            not owner_running
            and age_millis is not None
            and age_millis > DEFAULT_SERIAL_LOCK_STALE_MS
        )
    )
    if recoverable:
        status = "AVAILABLE_RECOVERABLE_RECORD"
        available = True
        reason = "the real coordinator can remove this stale or invalid lease record"
    elif owner_running:
        status = "OWNED"
        available = False
        reason = "another live owner holds the shared serial lease"
    else:
        status = "RESERVED"
        available = False
        reason = "a recent lease record is retained until its stale interval expires"
    return {
        "schema": "arduino_lease_status.v1",
        "port": port,
        "status": status,
        "available": available,
        "owner": _clean_text(payload.get("owner")) or None,
        "ownerPid": owner_pid,
        "ownerRunning": owner_running,
        "ageMillis": age_millis,
        "lockPath": str(path),
        "reason": reason,
    }


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


def _outer_origin_quantization_compatible(
    expected: ScreenBounds,
    actual: ScreenBounds,
) -> bool:
    """Reconcile one AWT/native origin pixel without accepting a resize."""

    return (
        actual.width == expected.width
        and actual.height == expected.height
        and abs(actual.x - expected.x)
        <= AWT_NATIVE_OUTER_ORIGIN_TOLERANCE_DEVICE_PX
        and abs(actual.y - expected.y)
        <= AWT_NATIVE_OUTER_ORIGIN_TOLERANCE_DEVICE_PX
    )


class InputPurpose(str, Enum):
    GAMEPLAY_OBJECT = "gameplay_object"
    GAMEPLAY_WIDGET = "gameplay_widget"
    CONTEXT_MENU = "context_menu"
    CONTEXT_ROW = "context_row"
    LOGIN_PROMPT = "login_prompt"
    GAMEPLAY_KEY = "gameplay_key"
    CAMERA_HOLD = "camera_hold"
    CAMERA_ZOOM = "camera_zoom"
    CURSOR_REACQUISITION = "cursor_reacquisition"


class InputFailureKind(str, Enum):
    NONE = "none"
    CURSOR_STATE_INVALIDATED = "cursor_state_invalidated"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"


class CursorInvalidationCause(str, Enum):
    CURSOR_REACQUIRED = "cursor_reacquired"
    UNEXPECTED_DIRECTION = "unexpected_direction"
    UNSUPPORTED_TRANSFER_GAIN = "unsupported_transfer_gain"
    UNEXPECTED_CROSS_AXIS = "unexpected_cross_axis"
    OUTSIDE_PADDED_VIEWPORT = "outside_padded_viewport"
    POINT_OWNER_MISMATCH = "point_owner_mismatch"
    FEEDBACK_UNRESOLVED = "feedback_unresolved"
    IDENTITY_CHANGED = "identity_changed"
    GEOMETRY_CHANGED = "geometry_changed"
    PHYSICAL_INPUT_ACTIVITY = "physical_input_activity"
    OTHER = "other"

    @property
    def recovery_eligible(self) -> bool:
        return self in {
            CursorInvalidationCause.UNEXPECTED_DIRECTION,
            CursorInvalidationCause.UNSUPPORTED_TRANSFER_GAIN,
            CursorInvalidationCause.UNEXPECTED_CROSS_AXIS,
            CursorInvalidationCause.OUTSIDE_PADDED_VIEWPORT,
            CursorInvalidationCause.POINT_OWNER_MISMATCH,
        }


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
    expected_hwnd: int | None = None
    button: MouseButton = MouseButton.LEFT
    reacquisition_bounds: ScreenBounds | None = None
    canvas_bounds: ScreenBounds | None = None
    viewport_bounds: ScreenBounds | None = None
    expected_native_outer_bounds: ScreenBounds | None = None
    expected_native_client_bounds: ScreenBounds | None = None
    motion_target_bounds: ScreenBounds | None = None
    motion_seed: int | str | None = None
    motion_decision_id: str | None = None
    motion_context: str = "default"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "intent_id", _validate_identifier(self.intent_id, "intent_id")
        )
        if not isinstance(self.purpose, InputPurpose):
            raise TypeError("purpose must be InputPurpose")
        if self.purpose in {
            InputPurpose.GAMEPLAY_KEY,
            InputPurpose.CAMERA_HOLD,
            InputPurpose.CAMERA_ZOOM,
        }:
            raise ValueError("a pointer intent cannot use a key or wheel purpose")
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
        canvas = (
            movement
            if self.canvas_bounds is None
            else _validate_bounds(self.canvas_bounds, "canvas_bounds")
        )
        viewport = (
            None
            if self.viewport_bounds is None
            else _validate_bounds(self.viewport_bounds, "viewport_bounds")
        )
        if not _bounds_contains_bounds(canvas, movement):
            raise ValueError("canvas_bounds must contain movement_bounds")
        if viewport is not None:
            if not _bounds_contains_bounds(canvas, viewport):
                raise ValueError("canvas_bounds must contain viewport_bounds")
            if not _bounds_contains_bounds(viewport, movement):
                raise ValueError("viewport_bounds must contain movement_bounds")
        gameplay_purposes = {
            InputPurpose.GAMEPLAY_OBJECT,
            InputPurpose.GAMEPLAY_WIDGET,
            InputPurpose.CONTEXT_MENU,
            InputPurpose.CONTEXT_ROW,
        }
        if self.purpose in gameplay_purposes:
            if viewport is None:
                raise ValueError(
                    "ordinary gameplay pointer intents require viewport_bounds"
                )
            if movement != gameplay_pointer_safe_bounds(viewport):
                raise ValueError(
                    "ordinary gameplay movement_bounds must equal the engine "
                    "pointer-safe inset"
                )
        if self.motion_target_bounds is not None:
            motion_target = _validate_bounds(
                self.motion_target_bounds, "motion_target_bounds"
            )
            if not _bounds_contains_bounds(movement, motion_target):
                raise ValueError(
                    "motion_target_bounds must be contained by movement_bounds"
                )
            if not motion_target.contains(self.target):
                raise ValueError("target must be inside motion_target_bounds")
        if self.reacquisition_bounds is not None:
            reacquisition = _validate_bounds(
                self.reacquisition_bounds, "reacquisition_bounds"
            )
            if self.purpose not in {
                InputPurpose.LOGIN_PROMPT,
                InputPurpose.GAMEPLAY_OBJECT,
                InputPurpose.GAMEPLAY_WIDGET,
                InputPurpose.CURSOR_REACQUISITION,
            }:
                raise ValueError(
                    "reacquisition_bounds are limited to initial login or gameplay targets"
                )
            if not _bounds_contains_bounds(reacquisition, movement):
                raise ValueError(
                    "reacquisition_bounds must contain movement_bounds"
                )
            if not _bounds_contains_bounds(reacquisition, canvas):
                raise ValueError(
                    "reacquisition_bounds must contain canvas_bounds"
                )
        native_outer = self.expected_native_outer_bounds
        native_client = self.expected_native_client_bounds
        if (native_outer is None) != (native_client is None):
            raise ValueError(
                "expected native outer/client bounds must be supplied together"
            )
        if native_outer is not None and native_client is not None:
            native_outer = _validate_bounds(
                native_outer, "expected_native_outer_bounds"
            )
            native_client = _validate_bounds(
                native_client, "expected_native_client_bounds"
            )
            if not _bounds_contains_bounds(native_outer, native_client):
                raise ValueError(
                    "expected native outer bounds must contain the client"
                )
            if not _bounds_contains_bounds(native_client, canvas):
                raise ValueError(
                    "expected native client bounds must contain canvas_bounds"
                )
        if not _is_int(self.expected_pid) or self.expected_pid <= 0:
            raise ValueError("expected_pid must be a positive integer")
        if self.expected_hwnd is not None and (
            not _is_int(self.expected_hwnd) or self.expected_hwnd <= 0
        ):
            raise ValueError("expected_hwnd must be a positive integer when provided")
        if not isinstance(self.button, MouseButton):
            raise TypeError("button must be MouseButton")
        if isinstance(self.motion_seed, bool) or not isinstance(
            self.motion_seed, (int, str, type(None))
        ):
            raise TypeError("motion_seed must be an integer, string, or None")
        if self.motion_seed is not None:
            seed_text = str(self.motion_seed).strip()
            if not seed_text or len(seed_text) > 128:
                raise ValueError("motion_seed must have a bounded representation")
            if isinstance(self.motion_seed, str):
                object.__setattr__(self, "motion_seed", seed_text)
        if self.motion_decision_id is not None:
            object.__setattr__(
                self,
                "motion_decision_id",
                _validate_identifier(
                    self.motion_decision_id,
                    "motion_decision_id",
                ),
            )
        object.__setattr__(
            self,
            "motion_context",
            _validate_identifier(self.motion_context, "motion_context"),
        )


@dataclass(frozen=True, slots=True)
class ApprovedCursorRecoveryIntent:
    """Geometry-only authorization for one movement-only neutral reacquisition."""

    recovery_id: str
    expected_pid: int
    expected_hwnd: int
    expected_outer_bounds: ScreenBounds
    expected_native_outer_bounds: ScreenBounds
    expected_native_client_bounds: ScreenBounds
    canvas_bounds: ScreenBounds
    viewport_bounds: ScreenBounds
    pointer_safe_bounds: ScreenBounds

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "recovery_id",
            _validate_identifier(self.recovery_id, "recovery_id"),
        )
        if not _is_int(self.expected_pid) or self.expected_pid <= 0:
            raise ValueError("expected_pid must be a positive integer")
        if not _is_int(self.expected_hwnd) or self.expected_hwnd <= 0:
            raise ValueError("expected_hwnd must be a positive integer")
        outer = _validate_bounds(self.expected_outer_bounds, "expected_outer_bounds")
        native_outer = _validate_bounds(
            self.expected_native_outer_bounds,
            "expected_native_outer_bounds",
        )
        native_client = _validate_bounds(
            self.expected_native_client_bounds,
            "expected_native_client_bounds",
        )
        canvas = _validate_bounds(self.canvas_bounds, "canvas_bounds")
        viewport = _validate_bounds(self.viewport_bounds, "viewport_bounds")
        safe = _validate_bounds(self.pointer_safe_bounds, "pointer_safe_bounds")
        if not _outer_origin_quantization_compatible(outer, native_outer):
            raise ValueError("expected outer/native outer geometry is incompatible")
        if not _bounds_contains_bounds(native_outer, native_client):
            raise ValueError("native outer bounds must contain native client bounds")
        if not _bounds_contains_bounds(native_client, canvas):
            raise ValueError("native client bounds must contain canvas_bounds")
        if not _bounds_contains_bounds(canvas, viewport):
            raise ValueError("canvas_bounds must contain viewport_bounds")
        if not _bounds_contains_bounds(viewport, safe):
            raise ValueError("viewport_bounds must contain pointer_safe_bounds")
        if safe != gameplay_pointer_safe_bounds(viewport):
            raise ValueError("pointer_safe_bounds does not match the engine inset")


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

    @property
    def required_capabilities(self) -> RequiredInputCapabilities:
        return RequiredInputCapabilities.generic_key_press(self.hold_millis)


@dataclass(frozen=True, slots=True)
class ApprovedCameraHoldIntent:
    """One semantic camera-direction hold; never a raw key-down transaction."""

    intent_id: str
    purpose: InputPurpose
    direction: str
    expected_pid: int
    hold_millis: int
    before_yaw: int
    before_pitch: int | None
    before_zoom: int | None
    source_geometry_frame_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "intent_id",
            _validate_identifier(self.intent_id, "intent_id"),
        )
        if self.purpose is not InputPurpose.CAMERA_HOLD:
            raise ValueError("camera hold intent must use camera_hold purpose")
        direction = str(self.direction).strip().lower()
        if direction not in {"left", "right", "up", "down"}:
            raise ValueError("camera direction must be left, right, up, or down")
        object.__setattr__(self, "direction", direction)
        if not _is_int(self.expected_pid) or self.expected_pid <= 0:
            raise ValueError("expected_pid must be a positive integer")
        if (
            not _is_int(self.hold_millis)
            or not 1 <= self.hold_millis <= MAX_CAMERA_HOLD_MILLIS
        ):
            raise ValueError(
                f"hold_millis must be between 1 and {MAX_CAMERA_HOLD_MILLIS}"
            )
        if not _is_int(self.before_yaw) or not 0 <= self.before_yaw < 16_384:
            raise ValueError("before_yaw must be a valid camera yaw")
        if self.before_pitch is not None and (
            not _is_int(self.before_pitch) or self.before_pitch < 0
        ):
            raise ValueError("before_pitch must be non-negative or None")
        if self.before_zoom is not None and (
            not _is_int(self.before_zoom) or self.before_zoom < 0
        ):
            raise ValueError("before_zoom must be non-negative or None")
        object.__setattr__(
            self,
            "source_geometry_frame_id",
            _validate_identifier(
                self.source_geometry_frame_id,
                "source_geometry_frame_id",
            ),
        )

    @property
    def required_capabilities(self) -> RequiredInputCapabilities:
        return RequiredInputCapabilities.camera_hold(
            self.direction,
            self.hold_millis,
        )


@dataclass(frozen=True, slots=True)
class ApprovedCameraZoomIntent:
    """One semantic bounded zoom while the cursor is already over world view."""

    intent_id: str
    purpose: InputPurpose
    amount: int
    expected_pid: int
    expected_hwnd: int | None
    expected_outer_bounds: ScreenBounds
    expected_native_outer_bounds: ScreenBounds | None
    expected_native_client_bounds: ScreenBounds | None
    canvas_bounds: ScreenBounds
    viewport_bounds: ScreenBounds
    pointer_safe_bounds: ScreenBounds
    before_yaw: int
    before_pitch: int | None
    before_zoom: int
    source_geometry_frame_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "intent_id",
            _validate_identifier(self.intent_id, "intent_id"),
        )
        if self.purpose is not InputPurpose.CAMERA_ZOOM:
            raise ValueError("camera zoom intent must use camera_zoom purpose")
        if (
            not _is_int(self.amount)
            or self.amount == 0
            or abs(self.amount) > MAX_CAMERA_WHEEL_STEP
        ):
            raise ValueError(
                f"amount must be a nonzero signed value within {MAX_CAMERA_WHEEL_STEP}"
            )
        if not _is_int(self.expected_pid) or self.expected_pid <= 0:
            raise ValueError("expected_pid must be a positive integer")
        if self.expected_hwnd is not None and (
            not _is_int(self.expected_hwnd) or self.expected_hwnd <= 0
        ):
            raise ValueError("expected_hwnd must be positive or None")
        outer = _validate_bounds(self.expected_outer_bounds, "expected_outer_bounds")
        native_outer = self.expected_native_outer_bounds
        native_client = self.expected_native_client_bounds
        if (native_outer is None) != (native_client is None):
            raise ValueError(
                "expected native outer/client bounds must be supplied together"
            )
        if native_outer is not None and native_client is not None:
            native_outer = _validate_bounds(
                native_outer,
                "expected_native_outer_bounds",
            )
            native_client = _validate_bounds(
                native_client,
                "expected_native_client_bounds",
            )
        canvas = _validate_bounds(self.canvas_bounds, "canvas_bounds")
        viewport = _validate_bounds(self.viewport_bounds, "viewport_bounds")
        safe = _validate_bounds(self.pointer_safe_bounds, "pointer_safe_bounds")
        if native_outer is not None and native_client is not None:
            if not _outer_origin_quantization_compatible(outer, native_outer):
                raise ValueError("expected outer/native outer geometry is incompatible")
            if not _bounds_contains_bounds(native_outer, native_client):
                raise ValueError("native outer bounds must contain native client bounds")
            if not _bounds_contains_bounds(native_client, canvas):
                raise ValueError("native client bounds must contain canvas_bounds")
        elif not _bounds_contains_bounds(outer, canvas):
            raise ValueError("expected_outer_bounds must contain canvas_bounds")
        if not _bounds_contains_bounds(canvas, viewport):
            raise ValueError("canvas_bounds must contain viewport_bounds")
        if not _bounds_contains_bounds(viewport, safe):
            raise ValueError("viewport_bounds must contain pointer_safe_bounds")
        if safe != gameplay_pointer_safe_bounds(viewport):
            raise ValueError("pointer_safe_bounds does not match the engine inset")
        if not _is_int(self.before_yaw) or not 0 <= self.before_yaw < 16_384:
            raise ValueError("before_yaw must be a valid camera yaw")
        if self.before_pitch is not None and (
            not _is_int(self.before_pitch) or self.before_pitch < 0
        ):
            raise ValueError("before_pitch must be non-negative or None")
        if not _is_int(self.before_zoom) or self.before_zoom < 0:
            raise ValueError("before_zoom must be non-negative")
        object.__setattr__(
            self,
            "source_geometry_frame_id",
            _validate_identifier(
                self.source_geometry_frame_id,
                "source_geometry_frame_id",
            ),
        )

    @property
    def required_capabilities(self) -> RequiredInputCapabilities:
        return RequiredInputCapabilities.camera_zoom(self.amount)


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
CameraHoldValidator: TypeAlias = Callable[
    [ApprovedCameraHoldIntent], InputValidation
]
CameraZoomValidator: TypeAlias = Callable[
    [ApprovedCameraZoomIntent], InputValidation
]
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
    write_duration_millis: int | None = None
    acknowledgement_duration_millis: int | None = None

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
        for field_name in (
            "write_duration_millis",
            "acknowledgement_duration_millis",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not _is_int(value) or not 0 <= value <= MAX_DURATION_MILLIS
            ):
                raise ValueError(
                    f"{field_name} must be a bounded non-negative integer or None"
                )

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
            "writeDurationMillis": self.write_duration_millis,
            "acknowledgementDurationMillis": (
                self.acknowledgement_duration_millis
            ),
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


@dataclass(frozen=True, slots=True)
class InputActivationBoundary:
    """Typed evidence captured at the sole production activation boundary."""

    operation: InputOperation
    command: str
    expected_pid: int
    attempted: bool
    acknowledged: bool
    command_sequence: int | None = None
    expected_hwnd: int | None = None
    direction: str | None = None
    requested_duration_millis: int | None = None
    applied_duration_millis: int | None = None
    requested_wheel_amount: int | None = None
    applied_wheel_amount: int | None = None
    cursor_point: ScreenPoint | None = None
    source_geometry_frame_id: str | None = None
    before_yaw: int | None = None
    before_pitch: int | None = None
    before_zoom: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, InputOperation):
            raise TypeError("operation must be InputOperation")
        command = _clean_text(self.command).upper()
        if not command:
            raise ValueError("activation command must be non-empty")
        object.__setattr__(self, "command", command)
        if not _is_int(self.expected_pid) or self.expected_pid <= 0:
            raise ValueError("expected_pid must be a positive integer")
        if self.expected_hwnd is not None and (
            not _is_int(self.expected_hwnd) or self.expected_hwnd <= 0
        ):
            raise ValueError("expected_hwnd must be positive or None")
        for field_name in ("attempted", "acknowledged"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be bool")
        if self.acknowledged and not self.attempted:
            raise ValueError("acknowledged activation must have been attempted")
        if self.command_sequence is not None and (
            not _is_int(self.command_sequence) or self.command_sequence <= 0
        ):
            raise ValueError("command_sequence must be positive or None")
        if self.acknowledged and self.command_sequence is None:
            raise ValueError("acknowledged activation requires command sequence")
        if self.direction is not None and self.direction not in {
            "left",
            "right",
            "up",
            "down",
        }:
            raise ValueError("direction is unsupported")
        for field_name in (
            "requested_duration_millis",
            "applied_duration_millis",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not _is_int(value) or not 1 <= value <= MAX_CAMERA_HOLD_MILLIS
            ):
                raise ValueError(f"{field_name} is outside the camera hold bound")
        for field_name in ("requested_wheel_amount", "applied_wheel_amount"):
            value = getattr(self, field_name)
            if value is not None and (
                not _is_int(value)
                or value == 0
                or abs(value) > MAX_CAMERA_WHEEL_STEP
            ):
                raise ValueError(f"{field_name} is outside the wheel bound")
        if self.cursor_point is not None and not isinstance(
            self.cursor_point,
            ScreenPoint,
        ):
            raise TypeError("cursor_point must be ScreenPoint or None")
        if self.source_geometry_frame_id is not None:
            object.__setattr__(
                self,
                "source_geometry_frame_id",
                _validate_identifier(
                    self.source_geometry_frame_id,
                    "source_geometry_frame_id",
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "input_activation_boundary.v1",
            "operation": self.operation.value,
            "command": self.command,
            "expectedPid": self.expected_pid,
            "expectedHwnd": self.expected_hwnd,
            "attempted": self.attempted,
            "acknowledged": self.acknowledged,
            "commandSequence": self.command_sequence,
            "direction": self.direction,
            "requestedDurationMillis": self.requested_duration_millis,
            "appliedDurationMillis": self.applied_duration_millis,
            "requestedWheelAmount": self.requested_wheel_amount,
            "appliedWheelAmount": self.applied_wheel_amount,
            "cursorPoint": (
                {"x": self.cursor_point.x, "y": self.cursor_point.y}
                if self.cursor_point is not None
                else None
            ),
            "sourceGeometryFrameId": self.source_geometry_frame_id,
            "beforeYaw": self.before_yaw,
            "beforePitch": self.before_pitch,
            "beforeZoom": self.before_zoom,
        }


@dataclass(frozen=True, slots=True)
class CameraInputVerificationEvidence:
    """Additive post-transaction camera verification attached by runtime."""

    kind: str
    status: str
    reason: str
    observed_tick: int | None = None
    before_yaw: int | None = None
    after_yaw: int | None = None
    before_pitch: int | None = None
    after_pitch: int | None = None
    before_zoom: int | None = None
    after_zoom: int | None = None
    before_geometry_frame_id: str | None = None
    after_geometry_frame_id: str | None = None
    ui_state_unchanged: bool | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"camera_pose_changed", "camera_zoom_changed"}:
            raise ValueError("camera verification kind is unsupported")
        if self.status not in {"pending", "pass", "fail"}:
            raise ValueError("camera verification status is unsupported")
        object.__setattr__(
            self,
            "reason",
            _validate_identifier(self.reason, "camera verification reason"),
        )
        if self.observed_tick is not None and (
            not _is_int(self.observed_tick) or self.observed_tick < 0
        ):
            raise ValueError("observed_tick must be non-negative or None")
        if self.ui_state_unchanged is not None and not isinstance(
            self.ui_state_unchanged,
            bool,
        ):
            raise TypeError("ui_state_unchanged must be bool or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "camera_input_verification.v1",
            "kind": self.kind,
            "status": self.status,
            "reason": self.reason,
            "observedTick": self.observed_tick,
            "beforeYaw": self.before_yaw,
            "afterYaw": self.after_yaw,
            "beforePitch": self.before_pitch,
            "afterPitch": self.after_pitch,
            "beforeZoom": self.before_zoom,
            "afterZoom": self.after_zoom,
            "beforeGeometryFrameId": self.before_geometry_frame_id,
            "afterGeometryFrameId": self.after_geometry_frame_id,
            "uiStateUnchanged": self.ui_state_unchanged,
        }


@dataclass(frozen=True, slots=True)
class DelayedCursorFeedbackEvent:
    plan: int
    step: int
    command_dx: int
    command_dy: int
    before: ScreenPoint
    last: ScreenPoint
    extra_polls: int
    elapsed_millis: int
    first_effect_millis: int | None
    complete_effect_millis: int | None
    outcome: str

    def __post_init__(self) -> None:
        if (
            not _is_int(self.plan)
            or not 1 <= self.plan <= MAX_POINTER_FEEDBACK_PLANS
        ):
            raise ValueError("plan is outside the bounded pointer-plan limit")
        if not _is_int(self.step) or not 1 <= self.step <= MAX_POINTER_STEPS:
            raise ValueError("step is outside the bounded MOVE limit")
        if not _is_int(self.command_dx) or not _is_int(self.command_dy):
            raise TypeError("command deltas must be integers")
        if self.command_dx == 0 and self.command_dy == 0:
            raise ValueError("a delayed cursor event requires a nonzero command")
        if not isinstance(self.before, ScreenPoint) or not isinstance(
            self.last, ScreenPoint
        ):
            raise TypeError("before and last must be ScreenPoint values")
        for name in ("before", "last"):
            point = getattr(self, name)
            if not _is_int(point.x) or not _is_int(point.y):
                raise TypeError(f"{name} coordinates must be integers")
        if (
            not _is_int(self.extra_polls)
            or not 0 <= self.extra_polls <= DELAYED_CURSOR_FEEDBACK_MAX_EXTRA_POLLS
        ):
            raise ValueError("extra_polls is outside the bounded feedback limit")
        if not _is_int(self.elapsed_millis) or self.elapsed_millis < 0:
            raise ValueError("elapsed_millis must be a non-negative integer")
        for name in ("first_effect_millis", "complete_effect_millis"):
            value = getattr(self, name)
            if value is not None and (not _is_int(value) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or None")
            if value is not None and value > self.elapsed_millis:
                raise ValueError(f"{name} cannot exceed elapsed_millis")
        if (
            self.complete_effect_millis is not None
            and self.first_effect_millis is None
        ):
            raise ValueError("complete effect evidence requires first effect evidence")
        if (
            self.first_effect_millis is not None
            and self.complete_effect_millis is not None
            and self.first_effect_millis > self.complete_effect_millis
        ):
            raise ValueError("first effect evidence cannot follow complete effect")
        if self.outcome not in {
            "settled",
            "effect_unresolved",
            "stability_unresolved",
            "rejected",
        }:
            raise ValueError("outcome is unsupported")
        if self.outcome == "effect_unresolved" and self.complete_effect_millis is not None:
            raise ValueError("unresolved effect cannot carry complete effect evidence")
        if self.outcome in {"settled", "stability_unresolved"}:
            if self.complete_effect_millis is None:
                raise ValueError(f"{self.outcome} requires complete effect evidence")
            if self.complete_effect_millis > int(
                DELAYED_CURSOR_FEEDBACK_ARRIVAL_TIMEOUT_SECONDS * 1000
            ):
                raise ValueError("complete effect evidence exceeds the arrival deadline")
        if (
            self.outcome == "settled"
            and self.elapsed_millis
            > int(DELAYED_CURSOR_FEEDBACK_TOTAL_TIMEOUT_SECONDS * 1000)
        ):
            raise ValueError("settled feedback exceeds the total deadline")
        if (
            self.outcome == "settled"
            and self.extra_polls
            < DELAYED_CURSOR_FEEDBACK_STABLE_SAMPLES + 1
        ):
            raise ValueError("settled feedback omits its stable samples")
        if self.outcome != "rejected":
            observed_x = self.last.x - self.before.x
            observed_y = self.last.y - self.before.y
            for axis, commanded, observed in (
                ("x", self.command_dx, observed_x),
                ("y", self.command_dy, observed_y),
            ):
                if commanded == 0:
                    if observed != 0:
                        raise ValueError(
                            f"non-rejected feedback moved uncommanded {axis}"
                        )
                    continue
                if observed == 0:
                    # The acknowledged effect may have been observed earlier,
                    # followed by a stationary manual takeover back to before.
                    continue
                if (commanded > 0) != (observed > 0):
                    raise ValueError(
                        f"non-rejected feedback reversed commanded {axis}"
                    )
                if (
                    abs(observed)
                    > abs(commanded) * MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT
                ):
                    raise ValueError(
                        f"non-rejected feedback exceeded {axis} transfer gain"
                    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "step": self.step,
            "command": {"dx": self.command_dx, "dy": self.command_dy},
            "before": {"x": self.before.x, "y": self.before.y},
            "last": {"x": self.last.x, "y": self.last.y},
            "extraPolls": self.extra_polls,
            "elapsedMillis": self.elapsed_millis,
            "firstEffectMillis": self.first_effect_millis,
            "completeEffectMillis": self.complete_effect_millis,
            "outcome": self.outcome,
        }


@dataclass(frozen=True, slots=True)
class CursorFeedbackEvidence:
    wait_count: int = 0
    settled_count: int = 0
    max_extra_polls: int = 0
    max_elapsed_millis: int = 0
    last_wait: DelayedCursorFeedbackEvent | None = None

    def __post_init__(self) -> None:
        for name in (
            "wait_count",
            "settled_count",
            "max_extra_polls",
            "max_elapsed_millis",
        ):
            value = getattr(self, name)
            if not _is_int(value) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.settled_count > self.wait_count:
            raise ValueError("settled_count cannot exceed wait_count")
        if self.max_extra_polls > DELAYED_CURSOR_FEEDBACK_MAX_EXTRA_POLLS:
            raise ValueError("max_extra_polls exceeds the bounded feedback limit")
        if self.last_wait is not None and not isinstance(
            self.last_wait, DelayedCursorFeedbackEvent
        ):
            raise TypeError("last_wait must be DelayedCursorFeedbackEvent or None")
        if self.wait_count == 0:
            if (
                self.settled_count != 0
                or self.max_extra_polls != 0
                or self.max_elapsed_millis != 0
                or self.last_wait is not None
            ):
                raise ValueError("empty cursor feedback evidence must be all-zero")
        elif self.last_wait is None:
            raise ValueError("nonempty cursor feedback evidence requires last_wait")
        elif (
            self.max_extra_polls < self.last_wait.extra_polls
            or self.max_elapsed_millis < self.last_wait.elapsed_millis
        ):
            raise ValueError("cursor feedback maxima cannot omit the last wait")
        elif self.wait_count == 1 and (
            self.max_extra_polls != self.last_wait.extra_polls
            or self.max_elapsed_millis != self.last_wait.elapsed_millis
        ):
            raise ValueError("single-wait maxima must equal the retained wait")
        else:
            expected_settled = (
                self.wait_count
                if self.last_wait.outcome == "settled"
                else self.wait_count - 1
            )
            if self.settled_count != expected_settled:
                raise ValueError(
                    "settled_count is inconsistent with the terminal wait"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "waitCount": self.wait_count,
            "settledCount": self.settled_count,
            "maxExtraPolls": self.max_extra_polls,
            "maxElapsedMillis": self.max_elapsed_millis,
            "lastWait": (
                self.last_wait.to_dict() if self.last_wait is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class CursorPositionSample:
    sequence: int
    phase: str
    point: ScreenPoint
    plan: int
    step: int

    def __post_init__(self) -> None:
        if not _is_int(self.sequence) or self.sequence < 1:
            raise ValueError("cursor sample sequence must be positive")
        object.__setattr__(
            self,
            "phase",
            _validate_identifier(self.phase, "cursor sample phase"),
        )
        if not isinstance(self.point, ScreenPoint):
            raise TypeError("cursor sample point must be ScreenPoint")
        if not _is_int(self.plan) or self.plan < 0:
            raise ValueError("cursor sample plan must be non-negative")
        if not _is_int(self.step) or self.step < 0:
            raise ValueError("cursor sample step must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "phase": self.phase,
            "point": {"x": self.point.x, "y": self.point.y},
            "plan": self.plan,
            "step": self.step,
        }


@dataclass(frozen=True, slots=True)
class PointerMotionEvidence:
    """Bounded summary of pointer plans actually accepted by a transaction."""

    plan_count: int = 0
    planned_step_count: int = 0
    executed_step_count: int = 0
    requested_start: ScreenPoint | None = None
    requested_target: ScreenPoint | None = None
    last_planned_target: ScreenPoint | None = None
    settled_target: ScreenPoint | None = None
    direct_distance_px: float = 0.0
    planned_path_length_px: float = 0.0
    planned_duration_seconds: float = 0.0
    style: str | None = None
    context: str | None = None
    seed: str | None = None
    decision_id: str | None = None
    control_points: tuple[ScreenPoint, ...] = ()
    correction_plan_count: int | None = None
    transfer_gain_upper_x: float | None = None
    transfer_gain_upper_y: float | None = None
    transfer_transit_waypoint: ScreenPoint | None = None
    movement_bounds: ScreenBounds | None = None
    activation_bounds: ScreenBounds | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "plan_count",
            "planned_step_count",
            "executed_step_count",
        ):
            value = getattr(self, field_name)
            if not _is_int(value) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.plan_count > MAX_POINTER_FEEDBACK_PLANS:
            raise ValueError("plan_count exceeds the bounded feedback plan limit")
        if self.planned_step_count > MAX_POINTER_FEEDBACK_PLANS * MAX_POINTER_STEPS:
            raise ValueError("planned_step_count exceeds its bounded diagnostic limit")
        if self.executed_step_count > MAX_POINTER_STEPS:
            raise ValueError("executed_step_count exceeds the transaction step limit")
        if self.executed_step_count > self.planned_step_count:
            raise ValueError("executed steps cannot exceed planned steps")
        if self.correction_plan_count is not None:
            if (
                not _is_int(self.correction_plan_count)
                or not 0 <= self.correction_plan_count <= self.plan_count
            ):
                raise ValueError(
                    "correction_plan_count must be between zero and plan_count"
                )
        for field_name in (
            "requested_start",
            "requested_target",
            "last_planned_target",
            "settled_target",
            "transfer_transit_waypoint",
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, ScreenPoint):
                raise TypeError(f"{field_name} must be ScreenPoint or None")
        for field_name in ("movement_bounds", "activation_bounds"):
            value = getattr(self, field_name)
            if value is not None:
                _validate_bounds(value, field_name)
        if (
            self.movement_bounds is not None
            and self.activation_bounds is not None
            and not _bounds_contains_bounds(
                self.movement_bounds, self.activation_bounds
            )
        ):
            raise ValueError("movement_bounds must contain activation_bounds")
        for field_name in (
            "direct_distance_px",
            "planned_path_length_px",
            "planned_duration_seconds",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0.0
            ):
                raise ValueError(f"{field_name} must be finite and non-negative")
        for field_name in (
            "transfer_gain_upper_x",
            "transfer_gain_upper_y",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 < float(value) <= MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT
            ):
                raise ValueError(
                    f"{field_name} must be a finite supported gain or None"
                )
        for field_name in ("style", "context", "seed", "decision_id"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 128
            ):
                raise ValueError(f"{field_name} must be bounded text or None")
        if not isinstance(self.control_points, tuple) or any(
            not isinstance(point, ScreenPoint) for point in self.control_points
        ):
            raise TypeError("control_points must be a tuple of ScreenPoint values")
        if len(self.control_points) > 2:
            raise ValueError("control_points exceeds the bounded summary limit")
        if self.plan_count == 0:
            if any(
                value is not None
                for value in (
                    self.requested_start,
                    self.requested_target,
                    self.last_planned_target,
                    self.settled_target,
                    self.style,
                    self.context,
                    self.seed,
                    self.decision_id,
                    self.transfer_gain_upper_x,
                    self.transfer_gain_upper_y,
                    self.transfer_transit_waypoint,
                    self.movement_bounds,
                    self.activation_bounds,
                )
            ) or any(
                (
                    self.planned_step_count,
                    self.executed_step_count,
                    self.direct_distance_px,
                    self.planned_path_length_px,
                    self.planned_duration_seconds,
                    len(self.control_points),
                )
            ):
                raise ValueError("empty pointer motion evidence must be empty")
        elif (
            self.requested_start is None
            or self.requested_target is None
            or self.last_planned_target is None
            or self.style is None
            or self.context is None
        ):
            raise ValueError("nonempty pointer motion evidence is incomplete")

    @staticmethod
    def _point(point: ScreenPoint | None) -> dict[str, int] | None:
        return None if point is None else {"x": point.x, "y": point.y}

    def to_dict(self) -> dict[str, Any]:
        correction = (
            None
            if self.last_planned_target is None or self.settled_target is None
            else {
                "dx": self.settled_target.x - self.last_planned_target.x,
                "dy": self.settled_target.y - self.last_planned_target.y,
            }
        )
        correction_plan_count = (
            max(0, self.plan_count - 1)
            if self.correction_plan_count is None
            else self.correction_plan_count
        )
        return {
            "planCount": self.plan_count,
            "correctionPlanCount": correction_plan_count,
            "intentionalLegCount": max(0, self.plan_count - correction_plan_count),
            "plannedStepCount": self.planned_step_count,
            "executedStepCount": self.executed_step_count,
            "requestedStart": self._point(self.requested_start),
            "requestedTarget": self._point(self.requested_target),
            "lastPlannedTarget": self._point(self.last_planned_target),
            "settledTarget": self._point(self.settled_target),
            "settledCorrection": correction,
            "directDistancePx": self.direct_distance_px,
            "plannedPathLengthPx": self.planned_path_length_px,
            "plannedDurationSeconds": self.planned_duration_seconds,
            "style": self.style,
            "context": self.context,
            "seed": self.seed,
            "decisionId": self.decision_id,
            "controlPoints": [
                {"x": point.x, "y": point.y}
                for point in self.control_points
            ],
            "learnedTransferGainUpper": {
                "x": self.transfer_gain_upper_x,
                "y": self.transfer_gain_upper_y,
            },
            "transferTransitWaypoint": self._point(
                self.transfer_transit_waypoint
            ),
            "movementBounds": (
                RuneLiteGeometryEvidence._bounds_dict(self.movement_bounds)
                if self.movement_bounds is not None
                else None
            ),
            "activationBounds": (
                RuneLiteGeometryEvidence._bounds_dict(self.activation_bounds)
                if self.activation_bounds is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class RuneLiteGeometryEvidence:
    """Exact native geometry pinned to one RuneLite PID/root HWND."""

    expected_pid: int
    expected_hwnd: int
    outer_bounds: ScreenBounds
    client_bounds: ScreenBounds
    canvas_bounds: ScreenBounds

    def __post_init__(self) -> None:
        if not _is_int(self.expected_pid) or self.expected_pid <= 0:
            raise ValueError("expected_pid must be a positive integer")
        if not _is_int(self.expected_hwnd) or self.expected_hwnd <= 0:
            raise ValueError("expected_hwnd must be a positive integer")
        outer = _validate_bounds(self.outer_bounds, "outer_bounds")
        client = _validate_bounds(self.client_bounds, "client_bounds")
        canvas = _validate_bounds(self.canvas_bounds, "canvas_bounds")
        if not _bounds_contains_bounds(outer, client):
            raise ValueError("outer_bounds must contain client_bounds")
        if not _bounds_contains_bounds(client, canvas):
            raise ValueError("client_bounds must contain canvas_bounds")

    @staticmethod
    def _bounds_dict(bounds: ScreenBounds) -> dict[str, int]:
        return {
            "x": bounds.x,
            "y": bounds.y,
            "width": bounds.width,
            "height": bounds.height,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "expectedPid": self.expected_pid,
            "expectedHwnd": self.expected_hwnd,
            "outerBounds": self._bounds_dict(self.outer_bounds),
            "clientBounds": self._bounds_dict(self.client_bounds),
            "canvasBounds": self._bounds_dict(self.canvas_bounds),
        }


@dataclass(frozen=True, slots=True)
class CursorReacquisitionEvidence:
    """Retained proof for a movement-only external-cursor transaction."""

    coordinate_space: str
    virtual_desktop_bounds: ScreenBounds
    neutral_bounds: ScreenBounds
    cursor_before: ScreenPoint
    before_geometry: RuneLiteGeometryEvidence
    cursor_after: ScreenPoint | None = None
    after_geometry: RuneLiteGeometryEvidence | None = None
    completed: bool = False
    no_activation_sent: bool = True

    def __post_init__(self) -> None:
        if self.coordinate_space != "device_pixels_pm_v2":
            raise ValueError("coordinate_space must be device_pixels_pm_v2")
        desktop = _validate_bounds(
            self.virtual_desktop_bounds, "virtual_desktop_bounds"
        )
        neutral = _validate_bounds(self.neutral_bounds, "neutral_bounds")
        if not isinstance(self.cursor_before, ScreenPoint):
            raise TypeError("cursor_before must be ScreenPoint")
        if not desktop.contains(self.cursor_before):
            raise ValueError("cursor_before must be inside virtual_desktop_bounds")
        if not _bounds_contains_bounds(
            desktop, self.before_geometry.canvas_bounds
        ):
            raise ValueError("canvas must be inside virtual_desktop_bounds")
        if not _bounds_contains_bounds(
            self.before_geometry.canvas_bounds, neutral
        ):
            raise ValueError("neutral_bounds must be inside the canvas")
        if self.cursor_after is not None and not isinstance(
            self.cursor_after, ScreenPoint
        ):
            raise TypeError("cursor_after must be ScreenPoint or None")
        if self.after_geometry is not None and not isinstance(
            self.after_geometry, RuneLiteGeometryEvidence
        ):
            raise TypeError(
                "after_geometry must be RuneLiteGeometryEvidence or None"
            )
        if not isinstance(self.completed, bool):
            raise TypeError("completed must be bool")
        if not isinstance(self.no_activation_sent, bool):
            raise TypeError("no_activation_sent must be bool")
        if self.completed:
            if self.cursor_after is None or self.after_geometry is None:
                raise ValueError(
                    "completed reacquisition requires final cursor and geometry"
                )
            if not neutral.contains(self.cursor_after):
                raise ValueError("completed cursor must be inside neutral_bounds")
            if self.after_geometry != self.before_geometry:
                raise ValueError("completed RuneLite geometry must be unchanged")
            if not self.no_activation_sent:
                raise ValueError("cursor reacquisition cannot send activation")

    @property
    def geometry_unchanged(self) -> bool:
        return bool(
            self.completed
            and self.after_geometry is not None
            and self.after_geometry == self.before_geometry
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "cursor_reacquisition.v1",
            "coordinateSpace": self.coordinate_space,
            "virtualDesktopBounds": RuneLiteGeometryEvidence._bounds_dict(
                self.virtual_desktop_bounds
            ),
            "neutralBounds": RuneLiteGeometryEvidence._bounds_dict(
                self.neutral_bounds
            ),
            "cursorBefore": {
                "x": self.cursor_before.x,
                "y": self.cursor_before.y,
            },
            "cursorAfter": (
                {
                    "x": self.cursor_after.x,
                    "y": self.cursor_after.y,
                }
                if self.cursor_after is not None
                else None
            ),
            "beforeGeometry": self.before_geometry.to_dict(),
            "afterGeometry": (
                self.after_geometry.to_dict()
                if self.after_geometry is not None
                else None
            ),
            "completed": self.completed,
            "geometryUnchanged": self.geometry_unchanged,
            "noActivationSent": self.no_activation_sent,
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
        any(
            name in {"KEY_PRESS", "CAMERA_HOLD", "WHEEL"}
            for name in names[arm_index + 1 : stop_index]
        )
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
    required_capabilities: tuple[RequiredInputCapabilities, ...] = ()
    negotiated_capabilities: InputCapabilities | None = None
    activation_boundary: InputActivationBoundary | None = None
    camera_verification: CameraInputVerificationEvidence | None = None
    failure_kind: InputFailureKind = InputFailureKind.NONE
    cursor_invalidation_cause: CursorInvalidationCause | None = None
    context_cancel_attempted: bool = False
    context_cancel_acknowledged: bool = False
    errors: tuple[str, ...] = ()
    cursor_feedback: CursorFeedbackEvidence = CursorFeedbackEvidence()
    pointer_motion: PointerMotionEvidence = PointerMotionEvidence()
    pointer_geometry: RuneLiteGeometryEvidence | None = None
    cursor_samples: tuple[CursorPositionSample, ...] = ()
    cursor_reacquisition: CursorReacquisitionEvidence | None = None
    observability: ObservabilityEvidence = ObservabilityEvidence()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "transaction_id",
            _validate_identifier(self.transaction_id, "transaction_id"),
        )
        if self.mode not in {
            "pointer",
            "key",
            "camera_hold",
            "camera_zoom",
            "context_menu",
            "adaptive_pointer",
        }:
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
        if not isinstance(self.failure_kind, InputFailureKind):
            raise TypeError("failure_kind must be an InputFailureKind")
        if self.cursor_invalidation_cause is not None and not isinstance(
            self.cursor_invalidation_cause, CursorInvalidationCause
        ):
            raise TypeError(
                "cursor_invalidation_cause must be CursorInvalidationCause or None"
            )
        if (
            self.cursor_invalidation_cause is not None
            and self.failure_kind is not InputFailureKind.CURSOR_STATE_INVALIDATED
        ):
            raise ValueError(
                "cursor invalidation cause requires cursor_state_invalidated"
            )
        if self.status == "PASS" and self.failure_kind is not InputFailureKind.NONE:
            raise ValueError("a successful receipt cannot carry a failure kind")
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
        if not isinstance(self.required_capabilities, tuple) or not all(
            isinstance(requirement, RequiredInputCapabilities)
            for requirement in self.required_capabilities
        ):
            raise TypeError(
                "required_capabilities must be a tuple of RequiredInputCapabilities"
            )
        if self.negotiated_capabilities is not None and not isinstance(
            self.negotiated_capabilities,
            InputCapabilities,
        ):
            raise TypeError(
                "negotiated_capabilities must be InputCapabilities or None"
            )
        if self.activation_boundary is not None and not isinstance(
            self.activation_boundary,
            InputActivationBoundary,
        ):
            raise TypeError(
                "activation_boundary must be InputActivationBoundary or None"
            )
        if self.camera_verification is not None and not isinstance(
            self.camera_verification,
            CameraInputVerificationEvidence,
        ):
            raise TypeError(
                "camera_verification must be CameraInputVerificationEvidence or None"
            )
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
        if not isinstance(self.cursor_feedback, CursorFeedbackEvidence):
            raise TypeError("cursor_feedback must be CursorFeedbackEvidence")
        if not isinstance(self.pointer_motion, PointerMotionEvidence):
            raise TypeError("pointer_motion must be PointerMotionEvidence")
        if self.pointer_geometry is not None and not isinstance(
            self.pointer_geometry, RuneLiteGeometryEvidence
        ):
            raise TypeError("pointer_geometry must be RuneLiteGeometryEvidence or None")
        if not isinstance(self.cursor_samples, tuple) or not all(
            isinstance(sample, CursorPositionSample)
            for sample in self.cursor_samples
        ):
            raise TypeError("cursor_samples must be a tuple of CursorPositionSample")
        if len(self.cursor_samples) > MAX_CURSOR_POSITION_SAMPLES:
            raise ValueError("cursor_samples exceeds its bounded limit")
        if tuple(sample.sequence for sample in self.cursor_samples) != tuple(
            range(1, len(self.cursor_samples) + 1)
        ):
            raise ValueError("cursor_samples must have contiguous sequence values")
        if self.cursor_reacquisition is not None and not isinstance(
            self.cursor_reacquisition, CursorReacquisitionEvidence
        ):
            raise TypeError(
                "cursor_reacquisition must be CursorReacquisitionEvidence or None"
            )
        if not isinstance(self.observability, ObservabilityEvidence):
            raise TypeError("observability must be ObservabilityEvidence")
        successful_move_count = sum(
            command.command == "MOVE" and command.successful
            for command in self.commands
        )
        if self.cursor_reacquisition is not None:
            activation_commands = {
                "MOUSE_DOWN",
                "MOUSE_UP",
                "CLICK",
                "KEY_DOWN",
                "KEY_UP",
                "KEY_PRESS",
                "HOLD_KEYS",
                "CAMERA_HOLD",
                "WHEEL",
            }
            if any(
                command.command in activation_commands
                for command in self.commands
            ):
                raise ValueError(
                    "cursor reacquisition evidence cannot accompany activation"
                )
            if self.cursor_reacquisition.completed and (
                self.status not in {"BLOCKED", "ERROR"}
                or self.failure_kind
                is not InputFailureKind.CURSOR_STATE_INVALIDATED
                or self.cursor_invalidation_cause
                is not CursorInvalidationCause.CURSOR_REACQUIRED
                or not self.connected
                or not self.arm_acknowledged
                or (
                    successful_move_count < 1
                    and self.pointer_motion.plan_count < 1
                )
            ):
                raise ValueError(
                    "completed cursor reacquisition requires a connected, "
                    "movement-only typed invalidation"
                )
        if self.mode == "key" and self.cursor_feedback.wait_count != 0:
            raise ValueError("key receipts cannot carry cursor feedback waits")
        if self.mode == "key" and self.pointer_motion.plan_count != 0:
            raise ValueError("key receipts cannot carry pointer motion evidence")
        if self.cursor_feedback.wait_count > successful_move_count:
            raise ValueError("cursor feedback waits exceed acknowledged MOVEs")
        if self.cursor_feedback.last_wait is not None and (
            self.cursor_feedback.last_wait.step > successful_move_count
            or self.cursor_feedback.last_wait.step
            < self.cursor_feedback.wait_count
        ):
            raise ValueError("cursor feedback step contradicts the MOVE ledger")
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
    def safely_unsent(self) -> bool:
        """Prove that a blocked preflight never connected to the Arduino.

        Lease, focus, or geometry failures before serial connect have no
        firmware cleanup to perform; an empty ledger and closed backend still
        prove that no automated input was possible.
        """

        return bool(
            self.status == "BLOCKED"
            and not self.connected
            and not self.arm_acknowledged
            and not self.stop_all_acknowledged
            and not self.disarm_acknowledged
            and not self.firmware_status_acknowledged
            and self.firmware_status is None
            and not self.commands
            and self.unresolved_command_count == 0
            and self.failed_command_count == 0
            and self.ack_missing_count == 0
            and self.ledger_complete
            and self.ledger_closed
            and self.backend_closed
            and not self.context_cancel_attempted
            and not self.context_cancel_acknowledged
            and self.cursor_feedback.wait_count == 0
        )

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
            and self.cursor_feedback.wait_count
            == self.cursor_feedback.settled_count
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
            "failureKind": self.failure_kind.value,
            "cursorInvalidationCause": (
                self.cursor_invalidation_cause.value
                if self.cursor_invalidation_cause is not None
                else None
            ),
            "successful": self.successful,
            "safelyUnsent": self.safely_unsent,
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
            "requiredCapabilities": [
                requirement.to_dict()
                for requirement in self.required_capabilities
            ],
            "negotiatedCapabilities": (
                self.negotiated_capabilities.to_dict()
                if self.negotiated_capabilities is not None
                else None
            ),
            "activationBoundary": (
                self.activation_boundary.to_dict()
                if self.activation_boundary is not None
                else None
            ),
            "cameraVerification": (
                self.camera_verification.to_dict()
                if self.camera_verification is not None
                else None
            ),
            "contextCancelAttempted": self.context_cancel_attempted,
            "contextCancelAcknowledged": self.context_cancel_acknowledged,
            "cursorFeedback": self.cursor_feedback.to_dict(),
            "pointerMotion": self.pointer_motion.to_dict(),
            "pointerGeometry": (
                self.pointer_geometry.to_dict()
                if self.pointer_geometry is not None
                else None
            ),
            "cursorSamples": [sample.to_dict() for sample in self.cursor_samples],
            "cursorReacquisition": (
                self.cursor_reacquisition.to_dict()
                if self.cursor_reacquisition is not None
                else None
            ),
            "observability": self.observability.to_dict(),
            "errors": list(self.errors),
        }


class _Backend(Protocol):
    def _begin_command_ledger(self) -> None: ...

    def _acquire_input_lease(self) -> None: ...

    def _command_evidence(self) -> dict[str, Any]: ...

    def _end_command_ledger(self) -> dict[str, Any]: ...

    def _connect(self) -> None: ...

    def _arm(self) -> dict[str, Any]: ...

    def _input_capabilities(self) -> InputCapabilities: ...

    def _current_position(self) -> tuple[int, int]: ...

    def _move_relative(self, dx: int, dy: int) -> dict[str, Any]: ...

    def _assert_foreground(
        self,
        allowed_titles: list[str] | tuple[str, ...],
        *,
        expected_pid: int | None = None,
    ) -> dict[str, Any]: ...

    def _window_info_at_point(self, point: tuple[int, int]) -> dict[str, Any]: ...

    def _verify_physical_mouse_quiet(self) -> dict[str, Any]: ...

    def _verify_physical_mouse_buttons_released(self) -> dict[str, Any]: ...

    def _consume_owned_mouse_transition(
        self, button: str
    ) -> dict[str, Any]: ...

    def _verify_window_geometry(
        self,
        *,
        expected_pid: int,
        expected_hwnd: int,
        expected_outer_bounds: tuple[int, int, int, int] | None,
        expected_client_bounds: tuple[int, int, int, int] | None,
        required_inner_bounds: tuple[int, int, int, int],
    ) -> dict[str, Any]: ...

    def _virtual_desktop_bounds(self) -> dict[str, Any]: ...

    def _mouse_down(self, *, button: str = "left") -> None: ...

    def _mouse_up(self, *, button: str = "left") -> None: ...

    def _press(self, key: str, hold_millis: int = 50) -> None: ...

    def _camera_hold(self, direction: str, hold_millis: int) -> dict[str, Any]: ...

    def _wheel(self, amount: int) -> dict[str, Any]: ...

    def _stop_all(self) -> dict[str, Any]: ...

    def _disarm(self) -> dict[str, Any]: ...

    def _firmware_status(self) -> dict[str, Any]: ...

    def _close(self) -> None: ...


class _TransactionAbort(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        blocked: bool = False,
        failure_kind: InputFailureKind = InputFailureKind.NONE,
        cursor_invalidation_cause: CursorInvalidationCause | None = None,
    ) -> None:
        super().__init__(_clean_text(reason, fallback="input_transaction_aborted"))
        self.blocked = blocked
        if not isinstance(failure_kind, InputFailureKind):
            raise TypeError("failure_kind must be an InputFailureKind")
        self.failure_kind = failure_kind
        if cursor_invalidation_cause is not None and not isinstance(
            cursor_invalidation_cause, CursorInvalidationCause
        ):
            raise TypeError(
                "cursor_invalidation_cause must be CursorInvalidationCause or None"
            )
        self.cursor_invalidation_cause = cursor_invalidation_cause


def _cursor_state_invalidated(
    reason: str,
    cause: CursorInvalidationCause = CursorInvalidationCause.OTHER,
) -> _TransactionAbort:
    return _TransactionAbort(
        reason,
        blocked=True,
        failure_kind=InputFailureKind.CURSOR_STATE_INVALIDATED,
        cursor_invalidation_cause=cause,
    )


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
        write_duration_millis=_optional_transport_duration(
            raw.get("writeDurationMillis")
        ),
        acknowledgement_duration_millis=_optional_transport_duration(
            raw.get("acknowledgementDurationMillis")
        ),
    )


def _optional_transport_duration(value: object) -> int | None:
    """Ignore malformed additive timing without changing command semantics."""

    if not _is_int(value) or not 0 <= value <= MAX_DURATION_MILLIS:
        return None
    return value


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
        evidence_clock: Callable[[], float],
    ) -> None:
        self.backend = backend
        self.max_ledger_entries = max_ledger_entries
        self.evidence_clock = evidence_clock
        self.snapshot = _EvidenceSnapshot((), 0, 0, 0)
        self.errors: list[str] = []
        self.ledger_complete = True
        self.timing = TimingEvidence()
        self.observed_wait_states: list[WaitState] = []
        self.pointer_plan_count = 0
        self.pointer_step_count = 0
        self.pointer_hwnd: int | None = None
        self.pointer_planned_step_count = 0
        self.pointer_requested_start: ScreenPoint | None = None
        self.pointer_requested_target: ScreenPoint | None = None
        self.pointer_last_planned_target: ScreenPoint | None = None
        self.pointer_settled_target: ScreenPoint | None = None
        self.pointer_direct_distance_px = 0.0
        self.pointer_planned_path_length_px = 0.0
        self.pointer_planned_duration_seconds = 0.0
        self.pointer_style: str | None = None
        self.pointer_context: str | None = None
        self.pointer_seed: str | None = None
        self.pointer_decision_id: str | None = None
        self.pointer_control_points: tuple[ScreenPoint, ...] = ()
        self.pointer_transfer_gain_upper_x: float | None = None
        self.pointer_transfer_gain_upper_y: float | None = None
        self.pointer_transfer_transit_waypoint: ScreenPoint | None = None
        self.pointer_movement_bounds: ScreenBounds | None = None
        self.pointer_activation_bounds: ScreenBounds | None = None
        self.pointer_leg_count = 0
        self.pointer_last_intent_id: str | None = None
        self.pointer_summary_intent_id: str | None = None
        self.cursor_feedback_wait_count = 0
        self.cursor_feedback_settled_count = 0
        self.cursor_feedback_max_extra_polls = 0
        self.cursor_feedback_max_elapsed_millis = 0
        self.last_cursor_feedback_wait: DelayedCursorFeedbackEvent | None = None
        self.cursor_position_samples: list[CursorPositionSample] = []
        self.pointer_geometry: RuneLiteGeometryEvidence | None = None
        self.negotiated_capabilities: InputCapabilities | None = None
        self.activation_boundary: InputActivationBoundary | None = None

    def timing_now(self) -> float | None:
        """Sample the diagnostic-only clock without influencing execution."""

        try:
            value = self.evidence_clock()
        except Exception:  # noqa: BLE001 - evidence cannot control input
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    def record_elapsed(
        self,
        phase: TimingPhase,
        started_at: float | None,
    ) -> None:
        finished_at = self.timing_now()
        if started_at is None or finished_at is None:
            return
        self.record_duration(
            phase,
            safe_elapsed_millis(started_at, finished_at),
        )

    def record_duration(
        self,
        phase: TimingPhase,
        duration_millis: int,
    ) -> None:
        try:
            self.timing = self.timing.record(phase, duration_millis)
        except Exception:  # noqa: BLE001 - evidence cannot control input
            return

    def record_wait_state(self, state: WaitState) -> None:
        if state not in self.observed_wait_states:
            self.observed_wait_states.append(state)

    def add_error(self, reason: object) -> None:
        text = _clean_text(reason, fallback="unknown_input_error")
        if text and text not in self.errors:
            self.errors.append(text)

    def record_activation_boundary(
        self,
        boundary: InputActivationBoundary,
    ) -> None:
        if not isinstance(boundary, InputActivationBoundary):
            raise TypeError("boundary must be InputActivationBoundary")
        self.activation_boundary = boundary

    def record_cursor_feedback_wait(
        self,
        event: DelayedCursorFeedbackEvent,
    ) -> None:
        if not isinstance(event, DelayedCursorFeedbackEvent):
            raise TypeError("event must be DelayedCursorFeedbackEvent")
        self.cursor_feedback_wait_count += 1
        if event.outcome == "settled":
            self.cursor_feedback_settled_count += 1
        self.cursor_feedback_max_extra_polls = max(
            self.cursor_feedback_max_extra_polls,
            event.extra_polls,
        )
        self.cursor_feedback_max_elapsed_millis = max(
            self.cursor_feedback_max_elapsed_millis,
            event.elapsed_millis,
        )
        self.last_cursor_feedback_wait = event

    def record_cursor_position(self, point: ScreenPoint, phase: str) -> None:
        if not isinstance(point, ScreenPoint):
            raise TypeError("cursor position sample must be ScreenPoint")
        if len(self.cursor_position_samples) >= MAX_CURSOR_POSITION_SAMPLES:
            raise _TransactionAbort("cursor position sample limit exceeded")
        self.cursor_position_samples.append(
            CursorPositionSample(
                sequence=len(self.cursor_position_samples) + 1,
                phase=phase,
                point=point,
                plan=self.pointer_plan_count,
                step=self.pointer_step_count,
            )
        )

    def cursor_feedback_evidence(self) -> CursorFeedbackEvidence:
        return CursorFeedbackEvidence(
            wait_count=self.cursor_feedback_wait_count,
            settled_count=self.cursor_feedback_settled_count,
            max_extra_polls=self.cursor_feedback_max_extra_polls,
            max_elapsed_millis=self.cursor_feedback_max_elapsed_millis,
            last_wait=self.last_cursor_feedback_wait,
        )

    def record_pointer_plan(
        self,
        plan: PointerMotionPlan,
        intent: ApprovedPointerIntent,
    ) -> None:
        if not isinstance(plan, PointerMotionPlan):
            raise TypeError("plan must be PointerMotionPlan")
        if not isinstance(intent, ApprovedPointerIntent):
            raise TypeError("intent must be ApprovedPointerIntent")
        new_intentional_leg = intent.intent_id != self.pointer_last_intent_id
        if new_intentional_leg:
            self.pointer_leg_count += 1
            self.pointer_last_intent_id = intent.intent_id
            self.pointer_summary_intent_id = intent.intent_id
            # Keep the trajectory-detail fields coherent with the most recent
            # intentional leg. Aggregate plan/step/path totals remain scoped
            # to the complete transaction, while target, context, seed,
            # decision, style, and controls all describe this same leg.
            self.pointer_requested_start = plan.start
            self.pointer_requested_target = intent.target
            self.pointer_direct_distance_px = math.hypot(
                intent.target.x - plan.start.x,
                intent.target.y - plan.start.y,
            )
            self.pointer_context = intent.motion_context
            self.pointer_seed = (
                None if intent.motion_seed is None else str(intent.motion_seed)
            )
            self.pointer_decision_id = intent.motion_decision_id
            self.pointer_style = plan.path_style
            self.pointer_control_points = plan.control_points
        self.pointer_last_planned_target = plan.target
        self.pointer_movement_bounds = intent.movement_bounds
        self.pointer_activation_bounds = intent.target_bounds
        self.pointer_planned_step_count += len(plan.steps)
        self.pointer_planned_path_length_px += plan.path_length_px
        self.pointer_planned_duration_seconds += plan.duration_seconds
        if self.pointer_style is None or (
            intent.intent_id == self.pointer_summary_intent_id
            and self.pointer_style != "cubic_bezier"
            and plan.path_style == "cubic_bezier"
        ):
            self.pointer_style = plan.path_style
            self.pointer_control_points = plan.control_points

    def record_pointer_settled(self, point: ScreenPoint) -> None:
        if not isinstance(point, ScreenPoint):
            raise TypeError("point must be ScreenPoint")
        self.pointer_settled_target = point

    def record_pointer_transfer_gain(self, axis: str, gain_upper: float) -> None:
        if axis not in {"x", "y"}:
            raise ValueError("pointer transfer axis must be x or y")
        if (
            isinstance(gain_upper, bool)
            or not isinstance(gain_upper, (int, float))
            or not math.isfinite(float(gain_upper))
            or not 0.0 < float(gain_upper) <= MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT
        ):
            raise ValueError("pointer transfer gain must be finite and supported")
        setattr(
            self,
            f"pointer_transfer_gain_upper_{axis}",
            round(float(gain_upper), 6),
        )

    def record_pointer_transfer_transit_waypoint(
        self,
        waypoint: ScreenPoint,
    ) -> None:
        if not isinstance(waypoint, ScreenPoint):
            raise TypeError("pointer transfer transit waypoint must be ScreenPoint")
        self.pointer_transfer_transit_waypoint = waypoint

    def pointer_motion_evidence(self) -> PointerMotionEvidence:
        return PointerMotionEvidence(
            plan_count=self.pointer_plan_count,
            planned_step_count=self.pointer_planned_step_count,
            executed_step_count=self.pointer_step_count,
            requested_start=self.pointer_requested_start,
            requested_target=self.pointer_requested_target,
            last_planned_target=self.pointer_last_planned_target,
            settled_target=self.pointer_settled_target,
            direct_distance_px=self.pointer_direct_distance_px,
            planned_path_length_px=self.pointer_planned_path_length_px,
            planned_duration_seconds=self.pointer_planned_duration_seconds,
            style=self.pointer_style,
            context=self.pointer_context,
            seed=self.pointer_seed,
            decision_id=self.pointer_decision_id,
            control_points=self.pointer_control_points,
            transfer_gain_upper_x=self.pointer_transfer_gain_upper_x,
            transfer_gain_upper_y=self.pointer_transfer_gain_upper_y,
            transfer_transit_waypoint=self.pointer_transfer_transit_waypoint,
            movement_bounds=self.pointer_movement_bounds,
            activation_bounds=self.pointer_activation_bounds,
            correction_plan_count=max(
                0,
                self.pointer_plan_count - self.pointer_leg_count,
            ),
        )

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
        operation_started_at = self.timing_now()
        try:
            value = operation()
        except Exception as error:  # noqa: BLE001 - receipt preserves the failure
            operation_error = error
        operation_finished_at = self.timing_now()
        evidence_ok = self.sync()
        new_commands = tuple(
            command
            for command in self.snapshot.commands
            if command.command_id not in before_ids
        )
        expected = expected_command.upper()
        matching = tuple(command for command in new_commands if command.command == expected)
        exact_transport_durations = any(
            command.write_duration_millis is not None
            or command.acknowledgement_duration_millis is not None
            for command in new_commands
        )
        if exact_transport_durations:
            for command in new_commands:
                self.record_duration(
                    TimingPhase.SERIAL_WRITE_ACKNOWLEDGEMENT,
                    min(
                        MAX_DURATION_MILLIS,
                        (command.write_duration_millis or 0)
                        + (command.acknowledgement_duration_millis or 0),
                    ),
                )
        elif operation_started_at is not None and operation_finished_at is not None:
            self.record_duration(
                TimingPhase.SERIAL_WRITE_ACKNOWLEDGEMENT,
                safe_elapsed_millis(
                    operation_started_at,
                    operation_finished_at,
                ),
            )
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
    required_capabilities: tuple[RequiredInputCapabilities, ...] = ()
    negotiated_capabilities: InputCapabilities | None = None
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
    failure_kind: InputFailureKind = InputFailureKind.NONE
    cursor_invalidation_cause: CursorInvalidationCause | None = None
    cursor_reacquisition: CursorReacquisitionEvidence | None = None
    arduino_command_failed: bool = False


@dataclass(frozen=True, slots=True)
class _CursorReacquisitionPlan:
    intent: ApprovedPointerIntent
    evidence: CursorReacquisitionEvidence


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
        max_correction_plans: int = MAX_GAMEPLAY_FEEDBACK_CORRECTION_PLANS,
        max_reacquisition_correction_plans: int = (
            MAX_CURSOR_REACQUISITION_FEEDBACK_CORRECTION_PLANS
        ),
        max_ledger_entries: int = MAX_LEDGER_ENTRIES,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        evidence_clock: Callable[[], float] = time.monotonic,
        wait_state_observer: Callable[[WaitState | None], None] | None = None,
    ) -> None:
        if not callable(backend_factory):
            raise TypeError("backend_factory must be callable")
        if not callable(pointer_planner):
            raise TypeError("pointer_planner must be callable")
        if not callable(monotonic):
            raise TypeError("monotonic must be callable")
        if not callable(evidence_clock):
            raise TypeError("evidence_clock must be callable")
        if wait_state_observer is not None and not callable(wait_state_observer):
            raise TypeError("wait_state_observer must be callable or None")
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
            or not 0
            <= max_correction_plans
            <= MAX_GAMEPLAY_FEEDBACK_CORRECTION_PLANS
        ):
            raise ValueError(
                "max_correction_plans must be between 0 and "
                f"{MAX_GAMEPLAY_FEEDBACK_CORRECTION_PLANS}"
            )
        if (
            not _is_int(max_reacquisition_correction_plans)
            or not 0
            <= max_reacquisition_correction_plans
            <= MAX_CURSOR_REACQUISITION_FEEDBACK_CORRECTION_PLANS
        ):
            raise ValueError(
                "max_reacquisition_correction_plans must be between 0 and "
                f"{MAX_CURSOR_REACQUISITION_FEEDBACK_CORRECTION_PLANS}"
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
        self._pointer_planner_is_builtin = pointer_planner is plan_pointer_motion
        self._pointer_limits = pointer_limits
        self._pointer_timestep_seconds = float(pointer_timestep_seconds)
        self._click_hold_seconds = float(click_hold_seconds)
        self._max_pointer_steps = max_pointer_steps
        self._max_correction_plans = max_correction_plans
        self._max_reacquisition_correction_plans = (
            max_reacquisition_correction_plans
        )
        self._max_ledger_entries = max_ledger_entries
        self._sleep = sleep
        self._monotonic = monotonic
        self._evidence_clock = evidence_clock
        self._wait_state_observer = wait_state_observer
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
        if intent.purpose is InputPurpose.CURSOR_REACQUISITION:
            raise ValueError(
                "cursor reacquisition is movement-only and cannot use execute_pointer"
            )
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

        return self._execute(
            "pointer",
            (intent.intent_id,),
            body,
            pointer_preflight=intent,
            required_capabilities=(
                RequiredInputCapabilities.pointer_click(
                    button=intent.button.value,
                    hold_ms=max(1, round(self._click_hold_seconds * 1000)),
                ),
            ),
        )

    def execute_cursor_reacquisition(
        self,
        recovery: ApprovedCursorRecoveryIntent,
    ) -> InputReceipt:
        """Run one forced movement-only neutral-canvas recovery transaction.

        The request contains geometry only.  It cannot carry a gameplay target,
        validator, button, or key and therefore cannot continue the invalidated
        semantic intent after movement.
        """

        if not isinstance(recovery, ApprovedCursorRecoveryIntent):
            raise TypeError("recovery must be ApprovedCursorRecoveryIntent")
        neutral = self._neutral_cursor_bounds(recovery.canvas_bounds)
        if not _bounds_contains_bounds(recovery.pointer_safe_bounds, neutral):
            raise ValueError(
                "neutral canvas region is outside the padded gameplay viewport"
            )
        preflight = ApprovedPointerIntent(
            intent_id=recovery.recovery_id,
            purpose=InputPurpose.CURSOR_REACQUISITION,
            target=neutral.center,
            movement_bounds=recovery.pointer_safe_bounds,
            target_bounds=neutral,
            expected_pid=recovery.expected_pid,
            expected_hwnd=recovery.expected_hwnd,
            reacquisition_bounds=recovery.expected_outer_bounds,
            canvas_bounds=recovery.canvas_bounds,
            viewport_bounds=recovery.viewport_bounds,
            expected_native_outer_bounds=recovery.expected_native_outer_bounds,
            expected_native_client_bounds=recovery.expected_native_client_bounds,
            motion_context="cursor_reacquisition",
        )

        def no_stale_body(_transaction: _Transaction) -> None:
            raise _TransactionAbort(
                "forced cursor reacquisition did not enter its movement-only lane",
                blocked=True,
            )

        return self._execute(
            "pointer",
            (preflight.intent_id,),
            no_stale_body,
            pointer_preflight=preflight,
            force_cursor_reacquisition=True,
            required_capabilities=(
                RequiredInputCapabilities.pointer_move(),
            ),
        )

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

        return self._execute(
            "key",
            (intent.intent_id,),
            body,
            required_capabilities=(intent.required_capabilities,),
        )

    def execute_camera_hold(
        self,
        intent: ApprovedCameraHoldIntent,
        *,
        validate: CameraHoldValidator,
    ) -> InputReceipt:
        if not isinstance(intent, ApprovedCameraHoldIntent):
            raise TypeError("intent must be ApprovedCameraHoldIntent")
        if not callable(validate):
            raise TypeError("validate must be callable")

        def body(transaction: _Transaction) -> None:
            def validate_camera_hold() -> InputValidation:
                self._assert_foreground(transaction.backend, intent.expected_pid)
                decision = validate(intent)
                self._assert_foreground(transaction.backend, intent.expected_pid)
                return decision

            self._validated_under_firmware_lease(
                transaction,
                validate_camera_hold,
                self._require_validation,
            )
            self._assert_foreground(transaction.backend, intent.expected_pid)
            self._assert_firmware_armed(
                transaction,
                phase="before_camera_hold_activation",
            )
            boundary = InputActivationBoundary(
                operation=InputOperation.CAMERA_KEY_HOLD,
                command="CAMERA_HOLD",
                expected_pid=intent.expected_pid,
                attempted=True,
                acknowledged=False,
                direction=intent.direction,
                requested_duration_millis=intent.hold_millis,
                source_geometry_frame_id=intent.source_geometry_frame_id,
                before_yaw=intent.before_yaw,
                before_pitch=intent.before_pitch,
                before_zoom=intent.before_zoom,
            )
            transaction.record_activation_boundary(boundary)
            acknowledged, raw_ack = transaction.invoke(
                "CAMERA_HOLD",
                lambda: transaction.backend._camera_hold(
                    intent.direction,
                    intent.hold_millis,
                ),
            )
            sequence = self._last_command_sequence(transaction, "CAMERA_HOLD")
            applied = (
                raw_ack.get("appliedDurationMs")
                if isinstance(raw_ack, Mapping)
                else None
            )
            transaction.record_activation_boundary(
                replace(
                    boundary,
                    acknowledged=acknowledged,
                    command_sequence=sequence,
                    applied_duration_millis=(
                        applied if _is_int(applied) else None
                    ),
                )
            )
            if not acknowledged or applied != intent.hold_millis:
                raise _TransactionAbort("camera_hold_not_exactly_acknowledged")

        return self._execute(
            "camera_hold",
            (intent.intent_id,),
            body,
            required_capabilities=(intent.required_capabilities,),
        )

    def execute_camera_zoom(
        self,
        intent: ApprovedCameraZoomIntent,
        *,
        validate: CameraZoomValidator,
    ) -> InputReceipt:
        if not isinstance(intent, ApprovedCameraZoomIntent):
            raise TypeError("intent must be ApprovedCameraZoomIntent")
        if not callable(validate):
            raise TypeError("validate must be callable")

        def body(transaction: _Transaction) -> None:
            def validate_camera_zoom() -> InputValidation:
                self._assert_camera_zoom_boundary(
                    transaction,
                    intent,
                    phase="before_camera_zoom_validation",
                )
                decision = validate(intent)
                self._assert_camera_zoom_boundary(
                    transaction,
                    intent,
                    phase="after_camera_zoom_validation",
                )
                return decision

            self._validated_under_firmware_lease(
                transaction,
                validate_camera_zoom,
                self._require_validation,
            )
            self._assert_firmware_armed(
                transaction,
                phase="before_camera_zoom_activation",
            )
            cursor = self._assert_camera_zoom_boundary(
                transaction,
                intent,
                phase="camera_zoom_activation_boundary",
            )
            boundary = InputActivationBoundary(
                operation=InputOperation.CAMERA_ZOOM,
                command="WHEEL",
                expected_pid=intent.expected_pid,
                expected_hwnd=transaction.pointer_hwnd,
                attempted=True,
                acknowledged=False,
                requested_wheel_amount=intent.amount,
                cursor_point=cursor,
                source_geometry_frame_id=intent.source_geometry_frame_id,
                before_yaw=intent.before_yaw,
                before_pitch=intent.before_pitch,
                before_zoom=intent.before_zoom,
            )
            transaction.record_activation_boundary(boundary)
            acknowledged, raw_ack = transaction.invoke(
                "WHEEL",
                lambda: transaction.backend._wheel(intent.amount),
            )
            sequence = self._last_command_sequence(transaction, "WHEEL")
            applied = (
                raw_ack.get("appliedAmount")
                if isinstance(raw_ack, Mapping)
                else None
            )
            transaction.record_activation_boundary(
                replace(
                    boundary,
                    acknowledged=acknowledged,
                    command_sequence=sequence,
                    applied_wheel_amount=(applied if _is_int(applied) else None),
                )
            )
            if not acknowledged or applied != intent.amount:
                raise _TransactionAbort("camera_zoom_not_exactly_acknowledged")

        return self._execute(
            "camera_zoom",
            (intent.intent_id,),
            body,
            zoom_preflight=intent,
            required_capabilities=(intent.required_capabilities,),
        )

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
            pointer_preflight=open_intent,
            required_capabilities=(
                RequiredInputCapabilities.pointer_click(
                    button=MouseButton.RIGHT.value,
                    hold_ms=max(1, round(self._click_hold_seconds * 1000)),
                ),
                RequiredInputCapabilities.pointer_click(
                    button=MouseButton.LEFT.value,
                    hold_ms=max(1, round(self._click_hold_seconds * 1000)),
                ),
                RequiredInputCapabilities.generic_key_press(50),
            ),
        )
        if not row_id:
            return receipt
        # The receipt stays immutable; replace only the dynamically resolved
        # row identifier so every transaction diagnostic remains intact.
        return replace(
            receipt,
            intent_ids=(open_intent.intent_id, row_id[0]),
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
                self._assert_pointer_foreground(transaction, intent)
                self._assert_cursor_stable_in_target(
                    transaction,
                    intent,
                    actual,
                    phase="before_activation_validation",
                )
                decision = decide_activation(intent, actual)
                self._assert_pointer_foreground(transaction, intent)
                self._assert_cursor_stable_in_target(
                    transaction,
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
            pointer_preflight=intent,
            required_capabilities=(
                RequiredInputCapabilities.pointer_click(
                    button=MouseButton.LEFT.value,
                    hold_ms=max(1, round(self._click_hold_seconds * 1000)),
                ),
                RequiredInputCapabilities.pointer_click(
                    button=MouseButton.RIGHT.value,
                    hold_ms=max(1, round(self._click_hold_seconds * 1000)),
                ),
                RequiredInputCapabilities.generic_key_press(50),
            ),
        )
        return (
            replace(receipt, intent_ids=(intent.intent_id, row_id[0]))
            if row_id
            else receipt
        )

    def _notify_wait_state(
        self,
        state: WaitState | None,
        transaction: _Transaction | None = None,
        *,
        notify_observer: bool = True,
    ) -> None:
        """Publish passive state without granting the observer any authority."""

        if transaction is not None and state is not None:
            transaction.record_wait_state(state)
        if not notify_observer or self._wait_state_observer is None:
            return
        try:
            self._wait_state_observer(state)
        except Exception:  # noqa: BLE001 - presentation cannot control input
            return

    def _execute(
        self,
        mode: str,
        intent_ids: tuple[str, ...],
        body: Callable[[_Transaction], None],
        *,
        external_state: _TransactionState | None = None,
        context_menu_open: list[bool] | None = None,
        context_pid: int | None = None,
        pointer_preflight: ApprovedPointerIntent | None = None,
        force_cursor_reacquisition: bool = False,
        zoom_preflight: ApprovedCameraZoomIntent | None = None,
        required_capabilities: tuple[RequiredInputCapabilities, ...] = (),
    ) -> InputReceipt:
        if not isinstance(required_capabilities, tuple) or not all(
            isinstance(requirement, RequiredInputCapabilities)
            for requirement in required_capabilities
        ):
            raise TypeError(
                "required_capabilities must be a tuple of typed requirements"
            )
        if not self._transaction_lock.acquire(blocking=False):
            raise RuntimeError("InputCoordinator already owns an active transaction")
        self._notify_wait_state(WaitState.INPUT_TRANSACTION_BUSY)
        try:
            self._transaction_sequence += 1
            transaction_id = f"input-{self._transaction_sequence:08d}"
            state = external_state or _TransactionState(
                transaction_id, mode, intent_ids
            )
            state.transaction_id = transaction_id
            state.mode = mode
            state.intent_ids = intent_ids
            state.required_capabilities = required_capabilities
            state.negotiated_capabilities = None
            backend: _Backend | None = None
            transaction: _Transaction | None = None
            reacquisition_plan: _CursorReacquisitionPlan | None = None
            ledger_started = False
            connection_attempted = False
            try:
                try:
                    backend = self._backend_factory()
                except Exception:
                    state.arduino_command_failed = True
                    raise
                transaction = _Transaction(
                    backend,
                    max_ledger_entries=self._max_ledger_entries,
                    evidence_clock=self._evidence_clock,
                )
                transaction.record_wait_state(WaitState.INPUT_TRANSACTION_BUSY)
                backend._begin_command_ledger()
                ledger_started = True
                # The same cross-process lease covers preflight and the later
                # Arduino session. Another host process therefore cannot move
                # the cursor through a competing coordinator transaction.
                lease_started_at = transaction.timing_now()
                try:
                    backend._acquire_input_lease()
                except Exception as error:
                    raise _TransactionAbort(
                        "input_process_lease_unavailable: "
                        f"{type(error).__name__}: {error}",
                        blocked=True,
                    ) from error
                finally:
                    transaction.record_elapsed(
                        TimingPhase.INPUT_LEASE_ACQUISITION,
                        lease_started_at,
                    )
                if not transaction.sync() or transaction.snapshot.commands:
                    raise _TransactionAbort("command ledger did not begin empty")
                if pointer_preflight is not None:
                    reacquisition_plan = self._prepare_cursor_reacquisition(
                        transaction,
                        pointer_preflight,
                        force=force_cursor_reacquisition,
                    )
                    if reacquisition_plan is not None:
                        state.cursor_reacquisition = reacquisition_plan.evidence
                connect_started_at = transaction.timing_now()
                try:
                    connection_attempted = True
                    backend._connect()
                    state.connected = True
                    state.arm_acknowledged, _ = transaction.invoke(
                        "ARM", backend._arm
                    )
                    if not state.arm_acknowledged:
                        state.arduino_command_failed = True
                        raise _TransactionAbort("arm_not_acknowledged")
                    try:
                        negotiated = backend._input_capabilities()
                    except Exception as error:
                        raise _TransactionAbort(
                            "input_capability_negotiation_unavailable: "
                            f"{type(error).__name__}: {error}",
                            blocked=True,
                            failure_kind=(
                                InputFailureKind.CAPABILITY_UNAVAILABLE
                            ),
                        ) from error
                    if not isinstance(negotiated, InputCapabilities):
                        raise _TransactionAbort(
                            "input_capability_negotiation_invalid",
                            blocked=True,
                            failure_kind=(
                                InputFailureKind.CAPABILITY_UNAVAILABLE
                            ),
                        )
                    transaction.negotiated_capabilities = negotiated
                    state.negotiated_capabilities = negotiated
                    for requirement in required_capabilities:
                        missing = requirement.missing_reason(negotiated)
                        if missing is not None:
                            raise _TransactionAbort(
                                "required_input_capability_unavailable: "
                                f"{missing}",
                                blocked=True,
                                failure_kind=(
                                    InputFailureKind.CAPABILITY_UNAVAILABLE
                                ),
                            )
                except _TransactionAbort:
                    raise
                except Exception:
                    state.arduino_command_failed = True
                    raise
                finally:
                    transaction.record_elapsed(
                        TimingPhase.ARDUINO_CONNECT_NEGOTIATE_ARM,
                        connect_started_at,
                    )
                if zoom_preflight is not None:
                    # Wheel follows the full negotiated transaction envelope.
                    # Native geometry, stationary cursor ownership, and
                    # physical-input quiet are still proved before activation,
                    # while every blocked branch can now complete STOP_ALL,
                    # DISARM, STATUS, ledger close, and backend close.
                    self._prepare_camera_zoom_preflight(
                        transaction,
                        zoom_preflight,
                    )
                if reacquisition_plan is not None:
                    state.cursor_reacquisition = self._reacquire_external_cursor(
                        transaction,
                        reacquisition_plan,
                    )
                    raise _cursor_state_invalidated(
                        "cursor_reacquired_reobserve_required",
                        CursorInvalidationCause.CURSOR_REACQUIRED,
                    )
                body(transaction)
                state.body_status = "PASS"
                state.body_reason = "input_transaction_executed"
            except _TransactionAbort as error:
                state.body_status = "BLOCKED" if error.blocked else "ERROR"
                state.body_reason = str(error)
                state.failure_kind = error.failure_kind
                state.cursor_invalidation_cause = error.cursor_invalidation_cause
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
                cleanup_started_at = (
                    transaction.timing_now() if transaction is not None else None
                )
                if transaction is not None and (
                    state.connected or connection_attempted
                ):
                    if context_menu_open and context_menu_open[0]:
                        state.context_cancel_attempted = True
                        try:
                            if context_pid is None:
                                raise RuntimeError("context PID unavailable")
                            self._assert_pinned_pointer_foreground(
                                transaction, context_pid
                            )
                            self._ensure_firmware_armed(transaction)
                            self._assert_pinned_pointer_foreground(
                                transaction, context_pid
                            )
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
                if transaction is not None:
                    transaction.record_elapsed(
                        TimingPhase.FINAL_CLEANUP,
                        cleanup_started_at,
                    )
            return self._receipt(state, transaction)
        finally:
            self._notify_wait_state(None)
            self._transaction_lock.release()

    @staticmethod
    def _last_command_sequence(
        transaction: _Transaction,
        command: str,
    ) -> int | None:
        expected = str(command).strip().upper()
        matching = tuple(
            item.sequence
            for item in transaction.snapshot.commands
            if item.command == expected
        )
        return matching[-1] if matching else None

    def _prepare_camera_zoom_preflight(
        self,
        transaction: _Transaction,
        intent: ApprovedCameraZoomIntent,
    ) -> None:
        """Prove a stationary cursor/world-window boundary before serial open."""

        try:
            self._require_physical_mouse_quiet(
                transaction.backend,
                phase="camera_zoom_preflight",
            )
            foreground = self._assert_foreground(
                transaction.backend,
                intent.expected_pid,
            )
            pinned_hwnd = self._verified_foreground_hwnd(
                foreground,
                expected_hwnd=intent.expected_hwnd,
            )
            geometry = self._require_window_geometry(
                transaction.backend,
                expected_pid=intent.expected_pid,
                expected_hwnd=pinned_hwnd,
                expected_outer_bounds=(
                    intent.expected_native_outer_bounds
                    if intent.expected_native_outer_bounds is not None
                    else intent.expected_outer_bounds
                ),
                expected_client_bounds=intent.expected_native_client_bounds,
                required_inner_bounds=intent.canvas_bounds,
                allow_outer_origin_quantization=(
                    intent.expected_native_outer_bounds is None
                ),
            )
            cursor = self._current_position(
                transaction,
                phase="camera_zoom_preflight_cursor",
            )
            if not intent.pointer_safe_bounds.contains(cursor):
                raise _TransactionAbort(
                    "camera_zoom_cursor_outside_approved_world_viewport",
                    blocked=True,
                )
            self._assert_point_owned_by_window(
                transaction.backend,
                cursor,
                expected_pid=intent.expected_pid,
                expected_hwnd=pinned_hwnd,
                reason_prefix="camera_zoom_preflight",
            )
        except _TransactionAbort as error:
            raise _TransactionAbort(
                f"camera_zoom_preflight_blocked: {error}",
                blocked=True,
            ) from error
        transaction.pointer_hwnd = pinned_hwnd
        transaction.pointer_geometry = geometry

    def _assert_camera_zoom_boundary(
        self,
        transaction: _Transaction,
        intent: ApprovedCameraZoomIntent,
        *,
        phase: str,
    ) -> ScreenPoint:
        try:
            self._require_physical_mouse_quiet(
                transaction.backend,
                phase=phase,
            )
            foreground = self._assert_foreground(
                transaction.backend,
                intent.expected_pid,
            )
            if transaction.pointer_hwnd is None:
                raise _TransactionAbort(
                    "camera_zoom_hwnd_not_pinned",
                    blocked=True,
                )
            pinned_hwnd = self._verified_foreground_hwnd(
                foreground,
                expected_hwnd=transaction.pointer_hwnd,
            )
            if transaction.pointer_geometry is None:
                raise _TransactionAbort(
                    "camera_zoom_geometry_not_pinned",
                    blocked=True,
                )
            self._require_unchanged_window_geometry(
                transaction.backend,
                transaction.pointer_geometry,
                reason=f"camera_zoom_geometry_changed:{phase}",
            )
            cursor = self._current_position(
                transaction,
                phase=f"camera_zoom_{phase}",
            )
            if not intent.pointer_safe_bounds.contains(cursor):
                raise _TransactionAbort(
                    f"camera_zoom_cursor_outside_approved_world_viewport:{phase}",
                    blocked=True,
                )
            self._assert_point_owned_by_window(
                transaction.backend,
                cursor,
                expected_pid=intent.expected_pid,
                expected_hwnd=pinned_hwnd,
                reason_prefix=f"camera_zoom_{phase}",
            )
            return cursor
        except _TransactionAbort as error:
            raise _TransactionAbort(
                f"camera_zoom_activation_blocked: {error}",
                blocked=True,
            ) from error

    def _prepare_cursor_reacquisition(
        self,
        transaction: _Transaction,
        intent: ApprovedPointerIntent,
        *,
        force: bool = False,
    ) -> _CursorReacquisitionPlan | None:
        """Capture stationary RuneLite geometry and plan no-click ingress.

        No window API is reachable from this path. A cursor already inside the
        verified canvas proceeds normally. Any cursor outside it is handled by
        one connected Arduino movement-only transaction whose original intent
        is always discarded afterward.
        """

        backend = transaction.backend
        pinned_native_geometry = (
            intent.expected_hwnd is not None
            and intent.expected_native_outer_bounds is not None
            and intent.expected_native_client_bounds is not None
        )
        self._require_physical_mouse_quiet(
            backend,
            phase="pointer_preflight",
            # Initial ingress may consume a manual placement event.  A retry
            # carrying the recovery-pinned HWND/native geometry may not: any
            # physical activity after reacquisition is terminal.
            allow_historical_activity=not force and not pinned_native_geometry,
        )
        if (
            intent.purpose is not InputPurpose.LOGIN_PROMPT
            and intent.reacquisition_bounds is None
            and not force
        ):
            return None
        start = self._current_position(
            transaction,
            phase="pointer_preflight_start",
        )
        try:
            foreground = self._assert_foreground(
                backend, intent.expected_pid
            )
        except _TransactionAbort as error:
            raise _cursor_state_invalidated(
                f"pointer_foreground_identity_invalid: {error}",
                CursorInvalidationCause.IDENTITY_CHANGED,
            ) from error
        except Exception as error:
            raise _cursor_state_invalidated(
                "runelite_foreground_required_for_cursor_recovery: "
                f"{type(error).__name__}: {error}",
                CursorInvalidationCause.IDENTITY_CHANGED,
            ) from error
        pinned_hwnd = self._verified_foreground_hwnd(
            foreground,
            expected_hwnd=intent.expected_hwnd,
        )
        authoritative_canvas = intent.canvas_bounds or intent.movement_bounds
        expected_outer = (
            intent.expected_native_outer_bounds
            if pinned_native_geometry
            else intent.reacquisition_bounds
        )
        expected_client = (
            intent.expected_native_client_bounds
            if pinned_native_geometry
            else (
                authoritative_canvas
                if intent.purpose is InputPurpose.LOGIN_PROMPT
                else None
            )
        )
        geometry = self._require_window_geometry(
            backend,
            expected_pid=intent.expected_pid,
            expected_hwnd=pinned_hwnd,
            expected_outer_bounds=expected_outer,
            expected_client_bounds=expected_client,
            required_inner_bounds=authoritative_canvas,
            allow_outer_origin_quantization=(
                not pinned_native_geometry
                and intent.purpose in {
                InputPurpose.GAMEPLAY_OBJECT,
                InputPurpose.GAMEPLAY_WIDGET,
                InputPurpose.CONTEXT_MENU,
                InputPurpose.CONTEXT_ROW,
                }
            ),
        )
        transaction.pointer_geometry = geometry
        if not force and intent.movement_bounds.contains(start):
            return None

        self._sleep(self._pointer_timestep_seconds)
        settled = self._current_position(
            transaction,
            phase="pointer_preflight_stationary",
        )
        try:
            settled_foreground = self._assert_foreground(
                backend, intent.expected_pid
            )
        except Exception as error:
            raise _cursor_state_invalidated(
                "cursor_reacquisition_foreground_changed_before_connect: "
                f"{type(error).__name__}: {error}",
                CursorInvalidationCause.IDENTITY_CHANGED,
            ) from error
        self._assert_same_foreground_window(settled_foreground, pinned_hwnd)
        if settled != start:
            raise _cursor_state_invalidated(
                "cursor_reacquisition_not_stationary_before_connect",
                CursorInvalidationCause.PHYSICAL_INPUT_ACTIVITY,
            )
        self._require_physical_mouse_quiet(
            backend,
            phase="cursor_reacquisition_before_connect",
        )
        desktop = self._require_virtual_desktop_bounds(backend)
        if not desktop.contains(start):
            raise _cursor_state_invalidated(
                "cursor_outside_verified_virtual_desktop",
                CursorInvalidationCause.GEOMETRY_CHANGED,
            )
        if not _bounds_contains_bounds(desktop, geometry.canvas_bounds):
            raise _cursor_state_invalidated(
                "runelite_canvas_outside_verified_virtual_desktop",
                CursorInvalidationCause.GEOMETRY_CHANGED,
            )
        neutral = self._neutral_cursor_bounds(geometry.canvas_bounds)
        if (
            intent.viewport_bounds is not None
            and not _bounds_contains_bounds(intent.movement_bounds, neutral)
        ):
            raise _cursor_state_invalidated(
                "neutral_canvas_region_outside_padded_viewport",
                CursorInvalidationCause.GEOMETRY_CHANGED,
            )
        ingress_intent = ApprovedPointerIntent(
            intent_id=f"{intent.intent_id}:cursor-reacquire",
            purpose=InputPurpose.CURSOR_REACQUISITION,
            target=neutral.center,
            movement_bounds=desktop,
            target_bounds=neutral,
            expected_pid=intent.expected_pid,
            expected_hwnd=pinned_hwnd,
            button=intent.button,
        )
        evidence = CursorReacquisitionEvidence(
            coordinate_space="device_pixels_pm_v2",
            virtual_desktop_bounds=desktop,
            neutral_bounds=neutral,
            cursor_before=start,
            before_geometry=geometry,
        )
        return _CursorReacquisitionPlan(ingress_intent, evidence)

    @staticmethod
    def _require_physical_mouse_quiet(
        backend: _Backend,
        *,
        phase: str,
        allow_historical_activity: bool = False,
    ) -> None:
        try:
            raw = backend._verify_physical_mouse_quiet()
        except Exception as error:
            raise _cursor_state_invalidated(
                f"physical_mouse_not_quiet_{phase}: "
                f"{type(error).__name__}: {error}",
                CursorInvalidationCause.PHYSICAL_INPUT_ACTIVITY,
            ) from error
        if (
            not isinstance(raw, Mapping)
            or raw.get("schema") != "physical_mouse_quiet.v1"
            or raw.get("buttonsUp") is not True
            or raw.get("activityClear") is not True
            or not isinstance(raw.get("historicalActivityConsumed"), bool)
            or not _is_int(raw.get("sampleCount"))
            or raw["sampleCount"] < MIN_PHYSICAL_MOUSE_QUIET_SAMPLE_COUNT
        ):
            raise _cursor_state_invalidated(
                f"physical_mouse_quiet_evidence_invalid_{phase}",
                CursorInvalidationCause.PHYSICAL_INPUT_ACTIVITY,
            )
        if (
            raw["historicalActivityConsumed"] is True
            and not allow_historical_activity
        ):
            raise _cursor_state_invalidated(
                f"physical_mouse_activity_since_prior_proof_{phase}",
                CursorInvalidationCause.PHYSICAL_INPUT_ACTIVITY,
            )

    @staticmethod
    def _require_physical_mouse_buttons_released(
        backend: _Backend,
        *,
        phase: str,
    ) -> None:
        try:
            raw = backend._verify_physical_mouse_buttons_released()
        except Exception as error:
            raise _cursor_state_invalidated(
                f"physical_mouse_buttons_not_released_{phase}: "
                f"{type(error).__name__}: {error}",
                CursorInvalidationCause.PHYSICAL_INPUT_ACTIVITY,
            ) from error
        if (
            not isinstance(raw, Mapping)
            or raw.get("schema") != "physical_mouse_buttons_released.v1"
            or raw.get("buttonsUp") is not True
            or raw.get("activityClear") is not True
        ):
            raise _cursor_state_invalidated(
                f"physical_mouse_button_evidence_invalid_{phase}",
                CursorInvalidationCause.PHYSICAL_INPUT_ACTIVITY,
            )

    @staticmethod
    def _require_virtual_desktop_bounds(backend: _Backend) -> ScreenBounds:
        try:
            raw = backend._virtual_desktop_bounds()
        except Exception as error:
            raise _cursor_state_invalidated(
                "virtual_desktop_geometry_unavailable: "
                f"{type(error).__name__}: {error}",
                CursorInvalidationCause.GEOMETRY_CHANGED,
            ) from error
        try:
            if not isinstance(raw, Mapping):
                raise ValueError("virtual desktop evidence is not an object")
            if raw.get("schema") != "virtual_desktop_geometry.v1":
                raise ValueError("virtual desktop evidence schema is unsupported")
            if raw.get("coordinateSpace") != "device_pixels_pm_v2":
                raise ValueError("virtual desktop coordinate space is unsupported")
            bounds = raw.get("bounds")
            if not isinstance(bounds, Mapping):
                raise ValueError("virtual desktop bounds are unavailable")
            return _validate_bounds(
                ScreenBounds(
                    bounds.get("x"),
                    bounds.get("y"),
                    bounds.get("width"),
                    bounds.get("height"),
                ),
                "virtual_desktop_bounds",
            )
        except Exception as error:
            raise _cursor_state_invalidated(
                "virtual_desktop_geometry_invalid: "
                f"{type(error).__name__}: {error}",
                CursorInvalidationCause.GEOMETRY_CHANGED,
            ) from error

    @staticmethod
    def _neutral_cursor_bounds(canvas: ScreenBounds) -> ScreenBounds:
        center = canvas.center
        radius = min(
            CURSOR_REACQUISITION_NEUTRAL_RADIUS_DEVICE_PX,
            max(1, (canvas.width - 1) // 4),
            max(1, (canvas.height - 1) // 4),
        )
        neutral = ScreenBounds(
            center.x - radius,
            center.y - radius,
            radius * 2 + 1,
            radius * 2 + 1,
        )
        if not _bounds_contains_bounds(canvas, neutral):
            raise _cursor_state_invalidated(
                "runelite_canvas_has_no_neutral_cursor_region",
                CursorInvalidationCause.GEOMETRY_CHANGED,
            )
        minimum_margin = min(
            CURSOR_REACQUISITION_NEUTRAL_INSET_DEVICE_PX,
            max(1, min(canvas.width, canvas.height) // 4),
        )
        margins = (
            neutral.x - canvas.x,
            canvas.x + canvas.width - (neutral.x + neutral.width),
            neutral.y - canvas.y,
            canvas.y + canvas.height - (neutral.y + neutral.height),
        )
        if min(margins) < minimum_margin:
            raise _cursor_state_invalidated(
                "runelite_canvas_neutral_region_lacks_safe_inset",
                CursorInvalidationCause.GEOMETRY_CHANGED,
            )
        return neutral

    @staticmethod
    def _consume_owned_mouse_transition(
        backend: _Backend,
        button: MouseButton,
    ) -> None:
        try:
            raw = backend._consume_owned_mouse_transition(button.value)
        except Exception as error:
            raise _TransactionAbort(
                "owned_mouse_transition_unproved_after_activation: "
                f"{type(error).__name__}: {error}"
            ) from error
        if (
            not isinstance(raw, Mapping)
            or raw.get("schema") != "owned_mouse_transition.v1"
            or raw.get("button") != button.value
            or not isinstance(raw.get("ownedTransitionConsumed"), bool)
            or raw.get("buttonsUp") is not True
            or raw.get("activityClear") is not True
        ):
            raise _TransactionAbort(
                "owned_mouse_transition_evidence_invalid_after_activation"
            )

    @staticmethod
    def _require_window_geometry(
        backend: _Backend,
        *,
        expected_pid: int,
        expected_hwnd: int,
        expected_outer_bounds: ScreenBounds | None,
        expected_client_bounds: ScreenBounds | None,
        required_inner_bounds: ScreenBounds,
        allow_outer_origin_quantization: bool,
    ) -> RuneLiteGeometryEvidence:
        def coordinates(
            bounds: ScreenBounds | None,
        ) -> tuple[int, int, int, int] | None:
            if bounds is None:
                return None
            return (bounds.x, bounds.y, bounds.width, bounds.height)

        try:
            raw = backend._verify_window_geometry(
                expected_pid=expected_pid,
                expected_hwnd=expected_hwnd,
                expected_outer_bounds=coordinates(expected_outer_bounds),
                expected_client_bounds=coordinates(expected_client_bounds),
                required_inner_bounds=(
                    required_inner_bounds.x,
                    required_inner_bounds.y,
                    required_inner_bounds.width,
                    required_inner_bounds.height,
                ),
            )
        except Exception as error:
            raise _cursor_state_invalidated(
                "cursor_window_geometry_proof_blocked: "
                f"{type(error).__name__}: {error}",
                CursorInvalidationCause.GEOMETRY_CHANGED,
            ) from error

        try:
            if not isinstance(raw, Mapping):
                raise ValueError("geometry evidence is not an object")
            if raw.get("schema") != "cursor_window_geometry.v1":
                raise ValueError("geometry evidence schema is unsupported")
            if raw.get("expectedPid") != expected_pid:
                raise ValueError("geometry evidence PID changed")
            if raw.get("expectedHwnd") != expected_hwnd:
                raise ValueError("geometry evidence HWND changed")

            def bounds(field: str) -> ScreenBounds:
                value = raw.get(field)
                if not isinstance(value, Mapping):
                    raise ValueError(f"{field} is not an object")
                return _validate_bounds(
                    ScreenBounds(
                        value.get("x"),
                        value.get("y"),
                        value.get("width"),
                        value.get("height"),
                    ),
                    field,
                )

            def optional_bounds(field: str) -> ScreenBounds | None:
                return None if raw.get(field) is None else bounds(field)

            reported_outer = optional_bounds("expectedOuterBounds")
            reported_client = optional_bounds("expectedClientBounds")
            reported_inner = bounds("requiredInnerBounds")
            actual_outer = bounds("actualOuterBounds")
            actual_client = bounds("actualClientBounds")
            outer_matches = raw.get("outerMatches")
            client_matches = raw.get("clientMatches")
            inner_contained = raw.get("innerContainedByClient")
            if reported_outer != expected_outer_bounds:
                raise ValueError("reported expected outer bounds changed")
            if reported_client != expected_client_bounds:
                raise ValueError("reported expected client bounds changed")
            if reported_inner != required_inner_bounds:
                raise ValueError("reported required inner bounds changed")
            if outer_matches != (
                None
                if expected_outer_bounds is None
                else actual_outer == expected_outer_bounds
            ):
                raise ValueError("outer geometry proof contradicts actual bounds")
            if client_matches != (
                None
                if expected_client_bounds is None
                else actual_client == expected_client_bounds
            ):
                raise ValueError("client geometry proof contradicts actual bounds")
            actual_contains_inner = _bounds_contains_bounds(
                actual_client, required_inner_bounds
            )
            if not _bounds_contains_bounds(actual_outer, actual_client):
                raise ValueError("native outer bounds do not contain the client")
            if inner_contained is not actual_contains_inner:
                raise ValueError("inner containment proof contradicts actual bounds")
            outer_origin_quantization_compatible = bool(
                expected_outer_bounds is not None
                and _outer_origin_quantization_compatible(
                    expected_outer_bounds,
                    actual_outer,
                )
            )
            # Gameplay's outer envelope is published from AWT logical bounds,
            # which can quantize one device pixel away from native
            # GetWindowRect on a scaled display. Login also supplies an exact
            # native client rectangle and remains exact.
            if (
                (
                    outer_matches is False
                    and not (
                        allow_outer_origin_quantization
                        and expected_client_bounds is None
                        and outer_origin_quantization_compatible
                    )
                )
                or client_matches is False
                or not inner_contained
            ):
                raise _cursor_state_invalidated(
                    "cursor_window_geometry_changed_reobserve_required",
                    CursorInvalidationCause.GEOMETRY_CHANGED,
                )
            return RuneLiteGeometryEvidence(
                expected_pid=expected_pid,
                expected_hwnd=expected_hwnd,
                outer_bounds=actual_outer,
                client_bounds=actual_client,
                canvas_bounds=required_inner_bounds,
            )
        except _TransactionAbort:
            raise
        except Exception as error:
            raise _cursor_state_invalidated(
                "cursor_window_geometry_evidence_invalid: "
                f"{type(error).__name__}: {error}",
                CursorInvalidationCause.GEOMETRY_CHANGED,
            ) from error

    def _require_unchanged_window_geometry(
        self,
        backend: _Backend,
        before: RuneLiteGeometryEvidence,
        *,
        reason: str = "runelite_geometry_changed_during_cursor_reacquisition",
    ) -> RuneLiteGeometryEvidence:
        after = self._require_window_geometry(
            backend,
            expected_pid=before.expected_pid,
            expected_hwnd=before.expected_hwnd,
            expected_outer_bounds=before.outer_bounds,
            expected_client_bounds=before.client_bounds,
            required_inner_bounds=before.canvas_bounds,
            allow_outer_origin_quantization=False,
        )
        if after != before:
            raise _cursor_state_invalidated(
                reason,
                CursorInvalidationCause.GEOMETRY_CHANGED,
            )
        return after

    def _ordinary_gameplay_movement_guard(
        self,
        transaction: _Transaction,
        intent: ApprovedPointerIntent,
    ) -> Callable[[ScreenPoint, str], None]:
        geometry = transaction.pointer_geometry

        def guard(point: ScreenPoint, phase: str) -> None:
            self._assert_pointer_foreground(transaction, intent)
            hwnd = transaction.pointer_hwnd
            if hwnd is None:
                raise _cursor_state_invalidated(
                    f"pointer_owner_binding_unavailable:{phase}",
                    CursorInvalidationCause.IDENTITY_CHANGED,
                )
            if geometry is not None:
                self._require_unchanged_window_geometry(
                    transaction.backend,
                    geometry,
                    reason=f"runelite_geometry_changed_during_pointer_motion:{phase}",
                )
            self._assert_point_owned_by_window(
                transaction.backend,
                point,
                expected_pid=intent.expected_pid,
                expected_hwnd=hwnd,
                reason_prefix=f"ordinary_pointer_{phase}",
            )

        return guard

    def _reacquire_external_cursor(
        self,
        transaction: _Transaction,
        plan: _CursorReacquisitionPlan,
    ) -> CursorReacquisitionEvidence:
        intent = plan.intent
        evidence = plan.evidence
        before_geometry = evidence.before_geometry
        entered_canvas = [False]

        self._assert_firmware_armed(
            transaction,
            phase="before_cursor_reacquisition",
        )

        def movement_guard(point: ScreenPoint, phase: str) -> None:
            self._assert_pointer_foreground(transaction, intent)
            current_desktop = self._require_virtual_desktop_bounds(
                transaction.backend
            )
            if current_desktop != evidence.virtual_desktop_bounds:
                raise _cursor_state_invalidated(
                    "virtual_desktop_geometry_changed_during_cursor_reacquisition",
                    CursorInvalidationCause.GEOMETRY_CHANGED,
                )
            if not current_desktop.contains(point):
                raise _cursor_state_invalidated(
                    "cursor_left_verified_virtual_desktop",
                    CursorInvalidationCause.OTHER,
                )
            self._require_physical_mouse_buttons_released(
                transaction.backend,
                phase=phase,
            )
            self._require_unchanged_window_geometry(
                transaction.backend,
                before_geometry,
            )
            inside_canvas = before_geometry.canvas_bounds.contains(point)
            if entered_canvas[0] and not inside_canvas:
                raise _cursor_state_invalidated(
                    "cursor_left_canvas_after_reacquisition_entry",
                    CursorInvalidationCause.OTHER,
                )
            if inside_canvas:
                entered_canvas[0] = True
                self._assert_point_owned_by_window(
                    transaction.backend,
                    point,
                    expected_pid=before_geometry.expected_pid,
                    expected_hwnd=before_geometry.expected_hwnd,
                    reason_prefix="cursor_reacquisition",
                )

        actual = self._move(
            transaction,
            intent,
            expected_start=evidence.cursor_before,
            movement_guard=movement_guard,
            allow_foreign_transit=True,
            strict_transfer_envelope=False,
            max_correction_plans=self._max_reacquisition_correction_plans,
        )
        if not entered_canvas[0] or not evidence.neutral_bounds.contains(actual):
            raise _cursor_state_invalidated(
                "cursor_reacquisition_did_not_reach_neutral_canvas_region",
                CursorInvalidationCause.OTHER,
            )
        self._require_physical_mouse_quiet(
            transaction.backend,
            phase="after_cursor_reacquisition",
        )
        final = self._current_position(
            transaction,
            phase="cursor_reacquisition_final",
        )
        movement_guard(final, "cursor_reacquisition_final")
        if final != actual:
            raise _cursor_state_invalidated(
                "cursor_not_stable_after_reacquisition",
                CursorInvalidationCause.PHYSICAL_INPUT_ACTIVITY,
            )
        final_geometry = self._require_unchanged_window_geometry(
            transaction.backend,
            before_geometry,
        )
        return replace(
            evidence,
            cursor_after=final,
            after_geometry=final_geometry,
            completed=True,
        )

    def _move(
        self,
        transaction: _Transaction,
        intent: ApprovedPointerIntent,
        *,
        expected_start: ScreenPoint | None = None,
        movement_guard: Callable[[ScreenPoint, str], None] | None = None,
        allow_foreign_transit: bool = False,
        strict_transfer_envelope: bool = True,
        max_correction_plans: int | None = None,
    ) -> ScreenPoint:
        started_at = transaction.timing_now()
        correction_limit = (
            self._max_correction_plans
            if max_correction_plans is None
            else max_correction_plans
        )
        if (
            not _is_int(correction_limit)
            or correction_limit < 0
            or correction_limit >= MAX_POINTER_FEEDBACK_PLANS
        ):
            raise ValueError("pointer correction plan limit is invalid")
        if movement_guard is None and intent.purpose in {
            InputPurpose.GAMEPLAY_OBJECT,
            InputPurpose.GAMEPLAY_WIDGET,
            InputPurpose.CONTEXT_MENU,
            InputPurpose.CONTEXT_ROW,
        }:
            movement_guard = self._ordinary_gameplay_movement_guard(
                transaction,
                intent,
            )
        try:
            return self._move_unmeasured(
                transaction,
                intent,
                expected_start=expected_start,
                movement_guard=movement_guard,
                allow_foreign_transit=allow_foreign_transit,
                strict_transfer_envelope=strict_transfer_envelope,
                max_correction_plans=correction_limit,
            )
        finally:
            transaction.record_elapsed(
                TimingPhase.POINTER_PLANNING_FEEDBACK_SETTLEMENT,
                started_at,
            )

    @staticmethod
    def _movement_bounds_invalidation(
        intent: ApprovedPointerIntent,
        reason: str,
    ) -> _TransactionAbort:
        cause = (
            CursorInvalidationCause.OUTSIDE_PADDED_VIEWPORT
            if intent.purpose
            in {
                InputPurpose.GAMEPLAY_OBJECT,
                InputPurpose.GAMEPLAY_WIDGET,
                InputPurpose.CONTEXT_MENU,
                InputPurpose.CONTEXT_ROW,
            }
            else CursorInvalidationCause.OTHER
        )
        return _cursor_state_invalidated(reason, cause)

    def _move_unmeasured(
        self,
        transaction: _Transaction,
        intent: ApprovedPointerIntent,
        *,
        expected_start: ScreenPoint | None = None,
        movement_guard: Callable[[ScreenPoint, str], None] | None = None,
        allow_foreign_transit: bool = False,
        strict_transfer_envelope: bool = True,
        max_correction_plans: int = MAX_GAMEPLAY_FEEDBACK_CORRECTION_PLANS,
    ) -> ScreenPoint:
        backend = transaction.backend
        self._ensure_firmware_armed(transaction)
        self._assert_pointer_foreground(transaction, intent)
        self._require_physical_mouse_quiet(
            backend, phase="before_pointer_motion"
        )
        start = self._current_position(
            transaction,
            phase="pointer_motion_start",
        )
        self._sleep(self._pointer_timestep_seconds)
        settled_start = self._current_position(
            transaction,
            phase="pointer_motion_quiescence",
        )
        self._assert_pointer_foreground(transaction, intent)
        if settled_start != start:
            raise _cursor_state_invalidated(
                "cursor_changed_before_pointer_motion",
                CursorInvalidationCause.PHYSICAL_INPUT_ACTIVITY,
            )
        self._require_physical_mouse_quiet(
            backend, phase="after_pointer_quiescence"
        )
        final_start = self._current_position(
            transaction,
            phase="pointer_motion_final_start",
        )
        self._assert_pointer_foreground(transaction, intent)
        if final_start != settled_start:
            raise _cursor_state_invalidated(
                "cursor_changed_before_pointer_motion",
                CursorInvalidationCause.PHYSICAL_INPUT_ACTIVITY,
            )
        start = final_start
        if expected_start is not None and start != expected_start:
            raise _cursor_state_invalidated(
                "cursor_changed_after_reacquisition_preflight",
                CursorInvalidationCause.PHYSICAL_INPUT_ACTIVITY,
            )
        if not intent.movement_bounds.contains(start):
            raise self._movement_bounds_invalidation(
                intent,
                "cursor_start_outside_verified_movement_bounds"
            )
        if movement_guard is not None:
            movement_guard(start, "cursor_reacquisition_start")
        actual = start
        x_calibrated = False
        y_calibrated = False
        x_gain_upper: float | None = None
        y_gain_upper: float | None = None
        x_gain_estimate: float | None = None
        y_gain_estimate: float | None = None
        x_inverse_gain_candidate = False
        y_inverse_gain_candidate = False
        transit_target = (
            None
            if strict_transfer_envelope
            else self._feedback_transit_waypoint(
                start,
                intent.target,
                intent.movement_bounds,
            )
        )
        transit_active = False
        transit_completed = False
        transit_origin = start
        for plan_index in range(max_correction_plans + 1):
            if transaction.pointer_plan_count >= MAX_POINTER_FEEDBACK_PLANS:
                raise _TransactionAbort(
                    "pointer transaction exceeds the total feedback plan limit"
                )
            primary_gain_upper = (
                x_gain_upper
                if abs(intent.target.x - start.x)
                >= abs(intent.target.y - start.y)
                else y_gain_upper
            )
            primary_inverse_gain_candidate = (
                x_inverse_gain_candidate
                if abs(intent.target.x - start.x)
                >= abs(intent.target.y - start.y)
                else y_inverse_gain_candidate
            )
            if (
                transit_target is not None
                and not transit_active
                and not transit_completed
                and (
                    primary_inverse_gain_candidate
                    or (
                        primary_gain_upper is not None
                        and primary_gain_upper < 1.0
                    )
                )
            ):
                refined_transit_target = self._feedback_transit_waypoint(
                    actual,
                    intent.target,
                    intent.movement_bounds,
                )
                if refined_transit_target is not None:
                    transit_target = refined_transit_target
                    transit_origin = actual
                    transit_active = True
                    transaction.record_pointer_transfer_transit_waypoint(
                        transit_target
                    )
            if transit_active and transit_target is not None:
                x_at_transit = self._feedback_transit_axis_reached(
                    transit_origin.x,
                    transit_target.x,
                    actual.x,
                )
                y_at_transit = self._feedback_transit_axis_reached(
                    transit_origin.y,
                    transit_target.y,
                    actual.y,
                )
                if x_at_transit and y_at_transit:
                    transit_active = False
                    transit_completed = True
            active_target = (
                transit_target
                if transit_active and transit_target is not None
                else intent.target
            )
            if not transit_active and plan_index == max_correction_plans:
                def inverse_gain_target(
                    current: int,
                    target: int,
                    lower: int,
                    upper: int,
                    gain_estimate: float | None,
                ) -> int:
                    if gain_estimate is None or gain_estimate >= 1.0:
                        return target
                    if target > current:
                        return upper
                    if target < current:
                        return lower
                    return target

                active_target = ScreenPoint(
                    inverse_gain_target(
                        actual.x,
                        intent.target.x,
                        intent.target_bounds.x,
                        intent.target_bounds.x + intent.target_bounds.width - 1,
                        x_gain_estimate,
                    ),
                    inverse_gain_target(
                        actual.y,
                        intent.target.y,
                        intent.target_bounds.y,
                        intent.target_bounds.y + intent.target_bounds.height - 1,
                        y_gain_estimate,
                    ),
                )
            x_calibrated_at_plan_start = x_calibrated
            y_calibrated_at_plan_start = y_calibrated
            if transit_active:
                x_in_target = self._feedback_transit_axis_reached(
                    transit_origin.x,
                    active_target.x,
                    actual.x,
                )
                y_in_target = self._feedback_transit_axis_reached(
                    transit_origin.y,
                    active_target.y,
                    actual.y,
                )
            else:
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
                remaining=0 if x_in_target else active_target.x - actual.x,
                coordinate=actual.x,
                lower=intent.movement_bounds.x,
                upper=(
                    intent.movement_bounds.x
                    + intent.movement_bounds.width
                    - 1
                ),
                calibrated=x_calibrated,
                transfer_gain_upper=x_gain_upper,
                transfer_gain_estimate=x_gain_estimate,
                axis="x",
            )
            command_dy = self._feedback_axis_command(
                remaining=0 if y_in_target else active_target.y - actual.y,
                coordinate=actual.y,
                lower=intent.movement_bounds.y,
                upper=(
                    intent.movement_bounds.y
                    + intent.movement_bounds.height
                    - 1
                ),
                calibrated=y_calibrated,
                transfer_gain_upper=y_gain_upper,
                transfer_gain_estimate=y_gain_estimate,
                axis="y",
            )
            command_target = ScreenPoint(
                actual.x + command_dx,
                actual.y + command_dy,
            )
            plan_actual_start = actual
            planner_options: dict[str, Any] = {
                "timestep_seconds": self._pointer_timestep_seconds,
                "limits": self._pointer_limits,
            }
            if self._pointer_planner_is_builtin:
                planner_options.update(
                    seed=intent.motion_seed,
                    decision_id=intent.motion_decision_id,
                    # Feedback calibration can require intermediate waypoints
                    # outside the final activation region. Preserve that region
                    # for the final plan without misrepresenting an intermediate
                    # waypoint as a valid activation target.
                    target_bounds=(
                        intent.motion_target_bounds or intent.target_bounds
                        if (intent.motion_target_bounds or intent.target_bounds).contains(
                            command_target
                        )
                        else None
                    ),
                    context=intent.motion_context,
                )
            plan = self._pointer_planner(
                actual,
                command_target,
                intent.movement_bounds,
                **planner_options,
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
            if (
                transaction.pointer_step_count + len(plan.steps)
                > self._max_pointer_steps
            ):
                raise _TransactionAbort("pointer motion exceeds the total step limit")
            transaction.pointer_plan_count += 1
            transaction.record_pointer_plan(plan, intent)
            plan_interrupted_by_feedback_wait = False

            for step in plan.steps:
                self._assert_pointer_foreground(transaction, intent)
                if not intent.movement_bounds.contains(actual):
                    raise self._movement_bounds_invalidation(
                        intent,
                        "cursor_left_verified_movement_bounds"
                    )
                if movement_guard is not None:
                    movement_guard(actual, "cursor_reacquisition_before_move")
                if strict_transfer_envelope:
                    self._assert_supported_transfer_headroom(
                        actual,
                        step.dx,
                        step.dy,
                        intent.viewport_bounds or intent.movement_bounds,
                        x_gain_upper=x_gain_upper,
                        y_gain_upper=y_gain_upper,
                    )
                else:
                    self._assert_directed_transfer_headroom(
                        actual,
                        step.dx,
                        step.dy,
                        intent.movement_bounds,
                    )
                before = actual
                feedback_started_at = self._feedback_now()
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
                actual = self._current_position(
                    transaction,
                    phase="pointer_feedback_initial",
                )
                actual_sampled_at = self._feedback_now()
                if actual_sampled_at < feedback_started_at:
                    raise _TransactionAbort("cursor_feedback_clock_regressed")
                first_effect_millis = (
                    self._feedback_elapsed_millis(
                        feedback_started_at,
                        actual_sampled_at,
                    )
                    if actual != before
                    else None
                )
                self._assert_pointer_foreground(transaction, intent)
                if not intent.movement_bounds.contains(actual):
                    raise self._movement_bounds_invalidation(
                        intent,
                        "cursor_left_verified_movement_bounds"
                    )
                if movement_guard is not None:
                    movement_guard(actual, "pointer_feedback_initial")
                if (
                    (step.dx != 0 and actual.x == before.x)
                    or (step.dy != 0 and actual.y == before.y)
                ) and actual_sampled_at < (
                    feedback_started_at
                    + DELAYED_CURSOR_FEEDBACK_ARRIVAL_TIMEOUT_SECONDS
                ):
                    self._assert_feedback_sample_delta(
                        transaction=transaction,
                        commanded=step.dx,
                        delayed_command=0,
                        observed=actual.x - before.x,
                        axis="x",
                        phase="initial_sample",
                    )
                    self._assert_feedback_sample_delta(
                        transaction=transaction,
                        commanded=step.dy,
                        delayed_command=0,
                        observed=actual.y - before.y,
                        axis="y",
                        phase="initial_sample",
                    )
                    # A HID report may reach Windows just after the ordinary
                    # timestep.  Poll once more without sending another MOVE;
                    # all normal direction, gain, bounds, and foreground
                    # checks still apply to the combined observation below.
                    initial_sample = actual
                    self._sleep(
                        min(
                            plan.timestep_seconds,
                            feedback_started_at
                            + DELAYED_CURSOR_FEEDBACK_ARRIVAL_TIMEOUT_SECONDS
                            - actual_sampled_at,
                        )
                    )
                    actual = self._current_position(
                        transaction,
                        phase="pointer_feedback_delayed",
                    )
                    actual_sampled_at = self._feedback_now()
                    if actual_sampled_at < feedback_started_at:
                        raise _TransactionAbort("cursor_feedback_clock_regressed")
                    if first_effect_millis is None and actual != before:
                        first_effect_millis = self._feedback_elapsed_millis(
                            feedback_started_at,
                            actual_sampled_at,
                        )
                    self._assert_pointer_foreground(transaction, intent)
                    if not intent.movement_bounds.contains(actual):
                        raise self._movement_bounds_invalidation(
                            intent,
                            "cursor_left_verified_movement_bounds"
                        )
                    if movement_guard is not None:
                        movement_guard(
                            actual,
                            "cursor_reacquisition_delayed_sample",
                        )
                    self._assert_feedback_sample_delta(
                        transaction=transaction,
                        commanded=step.dx,
                        delayed_command=0,
                        observed=actual.x - initial_sample.x,
                        axis="x",
                        phase="delayed_sample",
                    )
                    self._assert_feedback_sample_delta(
                        transaction=transaction,
                        commanded=step.dy,
                        delayed_command=0,
                        observed=actual.y - initial_sample.y,
                        axis="y",
                        phase="delayed_sample",
                    )
                effect_complete = self._cursor_move_effect_complete(
                    transaction=transaction,
                    commanded_x=step.dx,
                    commanded_y=step.dy,
                    observed_x=actual.x - before.x,
                    observed_y=actual.y - before.y,
                )
                if effect_complete:
                    self._require_cursor_effect_observed_by_arrival_deadline(
                        transaction=transaction,
                        before=before,
                        actual=actual,
                        commanded_x=step.dx,
                        commanded_y=step.dy,
                        feedback_started_at=feedback_started_at,
                        actual_sampled_at=actual_sampled_at,
                        first_effect_millis=first_effect_millis,
                    )
                if not effect_complete:
                    actual = self._await_delayed_cursor_feedback(
                        transaction=transaction,
                        intent=intent,
                        feedback_bounds=intent.movement_bounds,
                        before=before,
                        actual=actual,
                        commanded_x=step.dx,
                        commanded_y=step.dy,
                        feedback_started_at=feedback_started_at,
                        actual_sampled_at=actual_sampled_at,
                        first_effect_millis=first_effect_millis,
                        require_point_owner=not allow_foreign_transit,
                        movement_guard=movement_guard,
                    )
                    if step.dx != 0 and actual.x != before.x:
                        if x_gain_estimate is None:
                            x_gain_estimate = (
                                abs(actual.x - before.x) / abs(step.dx)
                            )
                        x_inverse_gain_candidate = (
                            abs(actual.x - before.x) <= abs(step.dx)
                        )
                    if step.dy != 0 and actual.y != before.y:
                        if y_gain_estimate is None:
                            y_gain_estimate = (
                                abs(actual.y - before.y) / abs(step.dy)
                            )
                        y_inverse_gain_candidate = (
                            abs(actual.y - before.y) <= abs(step.dy)
                        )
                    x_calibrated, x_gain_upper = self._validate_axis_transfer(
                        transaction=transaction,
                        commanded=step.dx,
                        observed=actual.x - before.x,
                        calibrated=x_calibrated,
                        gain_upper=x_gain_upper,
                        delayed_command=0,
                        axis="x",
                    )
                    y_calibrated, y_gain_upper = self._validate_axis_transfer(
                        transaction=transaction,
                        commanded=step.dy,
                        observed=actual.y - before.y,
                        calibrated=y_calibrated,
                        gain_upper=y_gain_upper,
                        delayed_command=0,
                        axis="y",
                    )
                    # The old trajectory was computed without the late cursor
                    # effect. Never execute its remainder; replan from the
                    # fully settled observed point.
                    plan_interrupted_by_feedback_wait = True
                    break
                if step.dx != 0 and actual.x != before.x:
                    if x_gain_estimate is None:
                        x_gain_estimate = (
                            abs(actual.x - before.x) / abs(step.dx)
                        )
                    x_inverse_gain_candidate = (
                        abs(actual.x - before.x) <= abs(step.dx)
                    )
                if step.dy != 0 and actual.y != before.y:
                    if y_gain_estimate is None:
                        y_gain_estimate = (
                            abs(actual.y - before.y) / abs(step.dy)
                        )
                    y_inverse_gain_candidate = (
                        abs(actual.y - before.y) <= abs(step.dy)
                    )
                x_calibrated, x_gain_upper = self._validate_axis_transfer(
                    transaction=transaction,
                    commanded=step.dx,
                    observed=actual.x - before.x,
                    calibrated=x_calibrated,
                    gain_upper=x_gain_upper,
                    delayed_command=0,
                    axis="x",
                )
                y_calibrated, y_gain_upper = self._validate_axis_transfer(
                    transaction=transaction,
                    commanded=step.dy,
                    observed=actual.y - before.y,
                    calibrated=y_calibrated,
                    gain_upper=y_gain_upper,
                    delayed_command=0,
                    axis="y",
                )
                transfer_changed_from_initial_plan = bool(
                    (
                        step.dx != 0
                        and not x_calibrated_at_plan_start
                        and abs(actual.x - before.x) > abs(step.dx)
                    )
                    or (
                        step.dy != 0
                        and not y_calibrated_at_plan_start
                        and abs(actual.y - before.y) > abs(step.dy)
                    )
                )
                if transfer_changed_from_initial_plan:
                    # The remaining command-space waypoints were planned for
                    # unit transfer. Preserve the observed sample, discard the
                    # stale remainder, and spend one bounded correction replan
                    # using the measured transfer estimate.
                    plan_interrupted_by_feedback_wait = True
                    break

            if not plan_interrupted_by_feedback_wait:
                # Each ordinary planner trajectory ends at rest. Give cursor
                # feedback one deterministic timestep before deciding whether
                # a bounded correction trajectory is needed.
                self._sleep(plan.timestep_seconds)
                settled = self._current_position(
                    transaction,
                    phase="pointer_plan_settled",
                )
                if not intent.movement_bounds.contains(settled):
                    raise self._movement_bounds_invalidation(
                        intent,
                        "cursor_left_verified_movement_bounds",
                    )
                self._assert_pointer_foreground(transaction, intent)
                if movement_guard is not None:
                    movement_guard(
                        settled,
                        "cursor_reacquisition_plan_settled",
                    )
                x_calibrated, x_gain_upper = self._validate_axis_transfer(
                    transaction=transaction,
                    commanded=0,
                    observed=settled.x - actual.x,
                    calibrated=x_calibrated,
                    gain_upper=x_gain_upper,
                    delayed_command=0,
                    axis="x",
                )
                y_calibrated, y_gain_upper = self._validate_axis_transfer(
                    transaction=transaction,
                    commanded=0,
                    observed=settled.y - actual.y,
                    calibrated=y_calibrated,
                    gain_upper=y_gain_upper,
                    delayed_command=0,
                    axis="y",
                )
                total_command_x = command_target.x - plan_actual_start.x
                total_command_y = command_target.y - plan_actual_start.y
                total_observed_x = settled.x - plan_actual_start.x
                total_observed_y = settled.y - plan_actual_start.y
                if (
                    total_command_x != 0
                    and total_observed_x != 0
                    and (total_command_x > 0) == (total_observed_x > 0)
                ):
                    x_gain_estimate = (
                        abs(total_observed_x) / abs(total_command_x)
                    )
                if (
                    total_command_y != 0
                    and total_observed_y != 0
                    and (total_command_y > 0) == (total_observed_y > 0)
                ):
                    y_gain_estimate = (
                        abs(total_observed_y) / abs(total_command_y)
                    )
                actual = settled
            if not intent.movement_bounds.contains(actual):
                raise self._movement_bounds_invalidation(
                    intent,
                    "cursor_left_verified_movement_bounds"
                )
            if plan_interrupted_by_feedback_wait:
                # Settlement proves only the acknowledged MOVE, not completion
                # of the discarded trajectory. Require a fresh correction (or
                # zero-step confirmation) plan before activation.
                if plan_index >= max_correction_plans:
                    raise _cursor_state_invalidated(
                        "cursor_feedback_correction_limit_exceeded",
                        CursorInvalidationCause.FEEDBACK_UNRESOLVED,
                    )
                continue
            # Device-pixel coordinates and integer Arduino HID deltas need not
            # share a one-pixel lattice (for example at 175% display scaling).
            # Accept only a fully settled plan endpoint inside the caller's
            # pre-verified target region. A zero-step plan can prove an already
            # stable point; a transient mid-trajectory crossing cannot.
            if intent.target_bounds.contains(actual):
                break
            if plan_index >= max_correction_plans:
                raise _cursor_state_invalidated(
                    "cursor_feedback_correction_limit_exceeded",
                    CursorInvalidationCause.FEEDBACK_UNRESOLVED,
                )

        final = self._current_position(
            transaction,
            phase="pointer_move_final",
        )
        if not intent.movement_bounds.contains(final):
            raise self._movement_bounds_invalidation(
                intent,
                "cursor_final_position_outside_verified_bounds",
            )
        if movement_guard is not None:
            movement_guard(final, "cursor_reacquisition_move_final")
        if not intent.target_bounds.contains(final):
            raise _TransactionAbort("cursor_target_outside_verified_target_bounds")
        transaction.record_pointer_settled(final)
        return final

    @staticmethod
    def _feedback_axis_command(
        *,
        remaining: int,
        coordinate: int,
        lower: int,
        upper: int,
        calibrated: bool,
        transfer_gain_upper: float | None,
        transfer_gain_estimate: float | None,
        axis: str,
    ) -> int:
        if remaining == 0:
            return 0
        direction = 1 if remaining > 0 else -1
        if not calibrated:
            magnitude = abs(remaining)
        elif transfer_gain_estimate is not None:
            magnitude = max(
                1,
                math.ceil(abs(remaining) / transfer_gain_estimate),
            )
        elif transfer_gain_upper is not None:
            magnitude = max(
                1,
                math.ceil(abs(remaining) / transfer_gain_upper),
            )
        else:
            magnitude = max(
                1,
                abs(remaining) // MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT,
            )
        clearance = upper - coordinate if direction > 0 else coordinate - lower
        safe_magnitude = clearance
        if safe_magnitude < 1:
            raise _TransactionAbort(
                f"cursor_transfer_headroom_insufficient_{axis}"
            )
        return direction * min(magnitude, safe_magnitude)

    @staticmethod
    def _feedback_transit_axis_reached(
        start: int,
        waypoint: int,
        actual: int,
    ) -> bool:
        if abs(actual - waypoint) <= MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT:
            return True
        if waypoint > start:
            return actual >= waypoint
        if waypoint < start:
            return actual <= waypoint
        return True

    @staticmethod
    def _feedback_transit_waypoint(
        start: ScreenPoint,
        target: ScreenPoint,
        bounds: ScreenBounds,
    ) -> ScreenPoint | None:
        """Earn transfer headroom without abandoning the verified canvas.

        A long move parallel to a nearby canvas edge otherwise collapses into
        tiny correction plans: the all-axis envelope is correctly limited by
        that edge even though most remaining distance is perpendicular to it.
        A single interior turn point makes progress while increasing the tight
        margin, then spends that margin while approaching the final target.
        """

        dx = target.x - start.x
        dy = target.y - start.y
        if max(abs(dx), abs(dy)) < MAX_FEEDBACK_PLAN_AXIS_DELTA * 2:
            return None

        lower_x = bounds.x
        upper_x = bounds.x + bounds.width - 1
        lower_y = bounds.y
        upper_y = bounds.y + bounds.height - 1

        if abs(dx) >= abs(dy):
            near_start = start.y - lower_y
            near_target = target.y - lower_y
            far_start = upper_y - start.y
            far_target = upper_y - target.y
            use_lower = near_start + near_target <= far_start + far_target
            start_margin = near_start if use_lower else far_start
            target_margin = near_target if use_lower else far_target
            maximum_margin = (bounds.height - 1) // 2
            required_margin = math.ceil(
                (abs(dx) + start_margin + target_margin) / 2.0
            )
            turn_margin = min(
                maximum_margin,
                required_margin
                + 2 * MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT,
            )
            if turn_margin <= start_margin + MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT:
                return None
            primary_progress = min(abs(dx), turn_margin - start_margin)
            turn_x = start.x + (primary_progress if dx > 0 else -primary_progress)
            turn_y = (
                lower_y + turn_margin
                if use_lower
                else upper_y - turn_margin
            )
        else:
            near_start = start.x - lower_x
            near_target = target.x - lower_x
            far_start = upper_x - start.x
            far_target = upper_x - target.x
            use_lower = near_start + near_target <= far_start + far_target
            start_margin = near_start if use_lower else far_start
            target_margin = near_target if use_lower else far_target
            maximum_margin = (bounds.width - 1) // 2
            required_margin = math.ceil(
                (abs(dy) + start_margin + target_margin) / 2.0
            )
            turn_margin = min(
                maximum_margin,
                required_margin
                + 2 * MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT,
            )
            if turn_margin <= start_margin + MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT:
                return None
            primary_progress = min(abs(dy), turn_margin - start_margin)
            turn_y = start.y + (primary_progress if dy > 0 else -primary_progress)
            turn_x = (
                lower_x + turn_margin
                if use_lower
                else upper_x - turn_margin
            )

        waypoint = ScreenPoint(turn_x, turn_y)
        return waypoint if bounds.contains(waypoint) else None

    @staticmethod
    def _clamp_feedback_waypoint_to_envelope(
        actual: ScreenPoint,
        dx: int,
        dy: int,
        bounds: ScreenBounds,
        *,
        diagonal_uses_full_budget: bool = False,
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
        if command_budget < (
            1 if diagonal_uses_full_budget else active_axes
        ):
            tight_margin = min(margins)
            recover_x = bool(
                dx != 0
                and (
                    (
                        dx > 0
                        and margins[0] == tight_margin
                        and margins[1] > tight_margin
                    )
                    or (
                        dx < 0
                        and margins[1] == tight_margin
                        and margins[0] > tight_margin
                    )
                )
            )
            recover_y = bool(
                dy != 0
                and (
                    (
                        dy > 0
                        and margins[2] == tight_margin
                        and margins[3] > tight_margin
                    )
                    or (
                        dy < 0
                        and margins[3] == tight_margin
                        and margins[2] > tight_margin
                    )
                )
            )
            if command_budget >= 1 and (recover_x or recover_y):
                # Preserve the sum-based four-sided envelope when a diagonal
                # unit probe cannot fit. Move one axis only, and only in a
                # direction that increases a currently tight canvas margin;
                # actual feedback must then earn room for the other axis.
                if recover_x:
                    magnitude = min(abs(dx), command_budget)
                    return (magnitude if dx > 0 else -magnitude), 0
                magnitude = min(abs(dy), command_budget)
                return 0, (magnitude if dy > 0 else -magnitude)
            raise _TransactionAbort(
                "cursor_bidirectional_transfer_headroom_insufficient"
            )
        def clamp(delta: int) -> int:
            if delta == 0:
                return 0
            # A diagonal command consumes the Chebyshev path length, not the
            # sum of its axes. The complete planned path is independently
            # checked below. Preserve the established sequential-axis
            # recovery everywhere except an explicit inverse-gain transit.
            per_axis_limit = (
                command_budget
                if diagonal_uses_full_budget
                else command_budget // active_axes
            )
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
    def _assert_directed_transfer_headroom(
        actual: ScreenPoint,
        dx: int,
        dy: int,
        bounds: ScreenBounds,
    ) -> None:
        """Contain no-click ingress while permitting an inward corner probe."""

        maximum_effect = MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT
        endpoint = ScreenPoint(
            actual.x + dx * maximum_effect,
            actual.y + dy * maximum_effect,
        )
        if not bounds.contains(endpoint):
            raise _TransactionAbort(
                "cursor_reacquisition_directed_transfer_would_leave_virtual_desktop"
            )

    @staticmethod
    def _assert_supported_transfer_headroom(
        actual: ScreenPoint,
        dx: int,
        dy: int,
        bounds: ScreenBounds,
        *,
        x_gain_upper: float | None,
        y_gain_upper: float | None,
    ) -> None:
        """Keep a supported directed response inside the proven viewport.

        Gameplay command-space plans remain wholly inside the stricter padded
        inset. The surrounding viewport padding is the uncertainty reserve for
        each next HID report until that axis has a measured gain upper bound.
        Every resulting actual sample is still required to remain in the inset.
        """

        def effect(delta: int, gain_upper: float | None) -> int:
            if delta == 0:
                return 0
            gain = (
                float(MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT)
                if gain_upper is None
                else gain_upper
            )
            magnitude = math.ceil(abs(delta) * gain)
            return magnitude if delta > 0 else -magnitude

        endpoint = ScreenPoint(
            actual.x + effect(dx, x_gain_upper),
            actual.y + effect(dy, y_gain_upper),
        )
        if not bounds.contains(endpoint):
            raise _TransactionAbort(
                "relative_move_supported_transfer_would_leave_viewport"
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

    def _feedback_now(self) -> float:
        raw = self._monotonic()
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise _TransactionAbort("cursor_feedback_clock_invalid")
        value = float(raw)
        if not math.isfinite(value):
            raise _TransactionAbort("cursor_feedback_clock_invalid")
        return value

    def _cursor_move_effect_complete(
        self,
        *,
        transaction: _Transaction,
        commanded_x: int,
        commanded_y: int,
        observed_x: int,
        observed_y: int,
    ) -> bool:
        for axis, commanded, observed in (
            ("x", commanded_x, observed_x),
            ("y", commanded_y, observed_y),
        ):
            if commanded == 0:
                if observed != 0:
                    raise _cursor_state_invalidated(
                        f"cursor_feedback_uncommanded_axis_{axis}",
                        CursorInvalidationCause.UNEXPECTED_CROSS_AXIS,
                    )
                continue
            if observed == 0:
                continue
            if (commanded > 0) != (observed > 0):
                raise _cursor_state_invalidated(
                    f"cursor_feedback_direction_mismatch_{axis}",
                    CursorInvalidationCause.UNEXPECTED_DIRECTION,
                )
            if (
                abs(observed)
                > abs(commanded) * MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT
            ):
                raise _cursor_state_invalidated(
                    f"cursor_transfer_gain_exceeded_{axis}:"
                    f"commanded={commanded}:observed={observed}:delayed=0:"
                    f"plan={transaction.pointer_plan_count}:"
                    f"step={transaction.pointer_step_count}",
                    CursorInvalidationCause.UNSUPPORTED_TRANSFER_GAIN,
                )
        return bool(
            (commanded_x == 0 or observed_x != 0)
            and (commanded_y == 0 or observed_y != 0)
        )

    @staticmethod
    def _feedback_elapsed_millis(started_at: float, now: float) -> int:
        return max(0, int(round((now - started_at) * 1000.0)))

    @staticmethod
    def _cursor_feedback_failure_reason(
        prefix: str,
        event: DelayedCursorFeedbackEvent,
    ) -> str:
        return (
            f"{prefix}:command_x={event.command_dx}:"
            f"command_y={event.command_dy}:"
            f"before_x={event.before.x}:before_y={event.before.y}:"
            f"last_x={event.last.x}:last_y={event.last.y}:"
            f"extra_polls={event.extra_polls}:"
            f"elapsed_ms={event.elapsed_millis}:"
            f"plan={event.plan}:step={event.step}"
        )

    def _require_cursor_effect_observed_by_arrival_deadline(
        self,
        *,
        transaction: _Transaction,
        before: ScreenPoint,
        actual: ScreenPoint,
        commanded_x: int,
        commanded_y: int,
        feedback_started_at: float,
        actual_sampled_at: float,
        first_effect_millis: int | None,
    ) -> None:
        """Reject an effect whose first complete observation missed 200 ms.

        The clock begins before the serial MOVE call so firmware ACK latency is
        part of the proof. A sample taken after the deadline cannot establish
        that the Windows cursor effect arrived in time, even when it is visible.
        """

        if actual_sampled_at < feedback_started_at:
            raise _TransactionAbort("cursor_feedback_clock_regressed")
        arrival_deadline = (
            feedback_started_at
            + DELAYED_CURSOR_FEEDBACK_ARRIVAL_TIMEOUT_SECONDS
        )
        if actual_sampled_at <= arrival_deadline + 1e-9:
            return
        elapsed_millis = self._feedback_elapsed_millis(
            feedback_started_at,
            actual_sampled_at,
        )
        event = DelayedCursorFeedbackEvent(
            plan=transaction.pointer_plan_count,
            step=transaction.pointer_step_count,
            command_dx=commanded_x,
            command_dy=commanded_y,
            before=before,
            last=actual,
            extra_polls=0,
            elapsed_millis=elapsed_millis,
            first_effect_millis=(
                first_effect_millis
                if first_effect_millis is not None
                else elapsed_millis
            ),
            complete_effect_millis=elapsed_millis,
            outcome="rejected",
        )
        transaction.record_cursor_feedback_wait(event)
        raise _cursor_state_invalidated(
            self._cursor_feedback_failure_reason(
                "cursor_feedback_move_effect_not_observed_by_arrival_deadline",
                event,
            ),
            CursorInvalidationCause.FEEDBACK_UNRESOLVED,
        )

    def _await_delayed_cursor_feedback(
        self,
        *,
        transaction: _Transaction,
        intent: ApprovedPointerIntent,
        feedback_bounds: ScreenBounds,
        before: ScreenPoint,
        actual: ScreenPoint,
        commanded_x: int,
        commanded_y: int,
        feedback_started_at: float,
        actual_sampled_at: float,
        first_effect_millis: int | None,
        require_point_owner: bool = True,
        movement_guard: Callable[[ScreenPoint, str], None] | None = None,
    ) -> ScreenPoint:
        self._notify_wait_state(
            WaitState.CURSOR_FEEDBACK_SETTLING,
            transaction,
        )
        try:
            return self._await_delayed_cursor_feedback_unobserved(
                transaction=transaction,
                intent=intent,
                feedback_bounds=feedback_bounds,
                before=before,
                actual=actual,
                commanded_x=commanded_x,
                commanded_y=commanded_y,
                feedback_started_at=feedback_started_at,
                actual_sampled_at=actual_sampled_at,
                first_effect_millis=first_effect_millis,
                require_point_owner=require_point_owner,
                movement_guard=movement_guard,
            )
        finally:
            self._notify_wait_state(
                WaitState.INPUT_TRANSACTION_BUSY,
                transaction,
            )

    def _await_delayed_cursor_feedback_unobserved(
        self,
        *,
        transaction: _Transaction,
        intent: ApprovedPointerIntent,
        feedback_bounds: ScreenBounds,
        before: ScreenPoint,
        actual: ScreenPoint,
        commanded_x: int,
        commanded_y: int,
        feedback_started_at: float,
        actual_sampled_at: float,
        first_effect_millis: int | None,
        require_point_owner: bool = True,
        movement_guard: Callable[[ScreenPoint, str], None] | None = None,
    ) -> ScreenPoint:
        """Await one ACKed MOVE without ever stacking another command.

        The complete two-axis effect must appear by the absolute arrival
        deadline. Two subsequent identical whole-cursor samples must also fit
        inside the total deadline. A fresh physical-button quiet proof and one
        final unchanged cursor sample then close the handoff before replanning.
        """

        first_now = self._feedback_now()
        if (
            actual_sampled_at < feedback_started_at
            or first_now < actual_sampled_at
        ):
            raise _TransactionAbort("cursor_feedback_clock_regressed")
        complete_effect_millis: int | None = None
        stable_samples = 0
        extra_polls = 0
        last = actual
        elapsed_millis = self._feedback_elapsed_millis(
            feedback_started_at,
            first_now,
        )
        arrival_deadline = (
            feedback_started_at
            + DELAYED_CURSOR_FEEDBACK_ARRIVAL_TIMEOUT_SECONDS
        )
        total_deadline = (
            feedback_started_at
            + DELAYED_CURSOR_FEEDBACK_TOTAL_TIMEOUT_SECONDS
        )
        settled = False
        last_clock = first_now
        pinned_hwnd = transaction.pointer_hwnd
        if pinned_hwnd is None:
            raise _TransactionAbort(
                "pointer_foreground_hwnd_unavailable",
                blocked=True,
            )

        try:
            if not feedback_bounds.contains(actual):
                raise self._movement_bounds_invalidation(
                    intent,
                    "cursor_left_verified_movement_bounds",
                )
            if require_point_owner:
                self._assert_point_owned_by_window(
                    transaction.backend,
                    actual,
                    expected_pid=intent.expected_pid,
                    expected_hwnd=pinned_hwnd,
                    reason_prefix="cursor_feedback",
                )
            if movement_guard is not None:
                movement_guard(
                    actual,
                    "cursor_reacquisition_feedback_initial",
                )
            for poll_index in range(
                1,
                DELAYED_CURSOR_FEEDBACK_MAX_EXTRA_POLLS + 1,
            ):
                now = self._feedback_now()
                if now < last_clock:
                    raise _TransactionAbort("cursor_feedback_clock_regressed")
                if now >= total_deadline:
                    break
                self._sleep(
                    min(
                        DELAYED_CURSOR_FEEDBACK_POLL_SECONDS,
                        total_deadline - now,
                    )
                )
                sample = self._current_position(
                    transaction,
                    phase="pointer_feedback_poll",
                )
                sampled_at = self._feedback_now()
                if sampled_at < now:
                    raise _TransactionAbort("cursor_feedback_clock_regressed")
                last_clock = sampled_at
                extra_polls = poll_index
                elapsed_millis = self._feedback_elapsed_millis(
                    feedback_started_at,
                    sampled_at,
                )
                previous = last
                last = sample
                if not feedback_bounds.contains(sample):
                    raise self._movement_bounds_invalidation(
                        intent,
                        "cursor_left_verified_movement_bounds",
                    )
                self._assert_pointer_foreground(transaction, intent)
                if require_point_owner:
                    self._assert_point_owned_by_window(
                        transaction.backend,
                        sample,
                        expected_pid=intent.expected_pid,
                        expected_hwnd=pinned_hwnd,
                        reason_prefix="cursor_feedback",
                    )
                if movement_guard is not None:
                    movement_guard(
                        sample,
                        "cursor_reacquisition_feedback_poll",
                    )
                if first_effect_millis is None and sample != before:
                    first_effect_millis = elapsed_millis
                complete = self._cursor_move_effect_complete(
                    transaction=transaction,
                    commanded_x=commanded_x,
                    commanded_y=commanded_y,
                    observed_x=sample.x - before.x,
                    observed_y=sample.y - before.y,
                )
                if complete and complete_effect_millis is None:
                    if sampled_at > arrival_deadline + 1e-9:
                        complete_effect_millis = elapsed_millis
                        late_event = DelayedCursorFeedbackEvent(
                            plan=transaction.pointer_plan_count,
                            step=transaction.pointer_step_count,
                            command_dx=commanded_x,
                            command_dy=commanded_y,
                            before=before,
                            last=last,
                            extra_polls=extra_polls,
                            elapsed_millis=elapsed_millis,
                            first_effect_millis=(
                                first_effect_millis
                                if first_effect_millis is not None
                                else elapsed_millis
                            ),
                            complete_effect_millis=complete_effect_millis,
                            outcome="rejected",
                        )
                        raise _cursor_state_invalidated(
                            self._cursor_feedback_failure_reason(
                                "cursor_feedback_move_effect_not_observed_by_arrival_deadline",
                                late_event,
                            ),
                            CursorInvalidationCause.FEEDBACK_UNRESOLVED,
                        )
                    complete_effect_millis = elapsed_millis
                if complete_effect_millis is not None:
                    stable_samples = (
                        stable_samples + 1 if sample == previous else 0
                    )
                    if (
                        stable_samples >= DELAYED_CURSOR_FEEDBACK_STABLE_SAMPLES
                        and sampled_at <= total_deadline + 1e-9
                    ):
                        settled = True
                        break
                else:
                    stable_samples = 0
                if (
                    sampled_at >= arrival_deadline - 1e-9
                    and complete_effect_millis is None
                ):
                    break
        except Exception:
            event = DelayedCursorFeedbackEvent(
                plan=transaction.pointer_plan_count,
                step=transaction.pointer_step_count,
                command_dx=commanded_x,
                command_dy=commanded_y,
                before=before,
                last=last,
                extra_polls=extra_polls,
                elapsed_millis=elapsed_millis,
                first_effect_millis=first_effect_millis,
                complete_effect_millis=complete_effect_millis,
                outcome="rejected",
            )
            transaction.record_cursor_feedback_wait(event)
            raise

        if not settled:
            outcome = (
                "effect_unresolved"
                if complete_effect_millis is None
                else "stability_unresolved"
            )
            event = DelayedCursorFeedbackEvent(
                plan=transaction.pointer_plan_count,
                step=transaction.pointer_step_count,
                command_dx=commanded_x,
                command_dy=commanded_y,
                before=before,
                last=last,
                extra_polls=extra_polls,
                elapsed_millis=elapsed_millis,
                first_effect_millis=first_effect_millis,
                complete_effect_millis=complete_effect_millis,
                outcome=outcome,
            )
            transaction.record_cursor_feedback_wait(event)
            prefix = (
                "cursor_feedback_move_effect_unresolved"
                if outcome == "effect_unresolved"
                else "cursor_feedback_move_stability_unresolved"
            )
            raise _cursor_state_invalidated(
                self._cursor_feedback_failure_reason(prefix, event),
                CursorInvalidationCause.FEEDBACK_UNRESOLVED,
            )

        rejected_last = last
        try:
            self._require_physical_mouse_quiet(
                transaction.backend,
                phase="after_delayed_cursor_feedback",
            )
            final = self._current_position(
                transaction,
                phase="pointer_feedback_final",
            )
            rejected_last = final
            if not feedback_bounds.contains(final):
                raise self._movement_bounds_invalidation(
                    intent,
                    "cursor_left_verified_movement_bounds",
                )
            self._assert_pointer_foreground(transaction, intent)
            if require_point_owner:
                self._assert_point_owned_by_window(
                    transaction.backend,
                    final,
                    expected_pid=intent.expected_pid,
                    expected_hwnd=pinned_hwnd,
                    reason_prefix="cursor_feedback",
                )
            if movement_guard is not None:
                movement_guard(
                    final,
                    "cursor_reacquisition_feedback_final",
                )
            if final != last:
                raise _cursor_state_invalidated(
                    "cursor_changed_after_delayed_cursor_feedback",
                    CursorInvalidationCause.FEEDBACK_UNRESOLVED,
                )
        except Exception:
            event = DelayedCursorFeedbackEvent(
                plan=transaction.pointer_plan_count,
                step=transaction.pointer_step_count,
                command_dx=commanded_x,
                command_dy=commanded_y,
                before=before,
                last=rejected_last,
                extra_polls=extra_polls,
                elapsed_millis=elapsed_millis,
                first_effect_millis=first_effect_millis,
                complete_effect_millis=complete_effect_millis,
                outcome="rejected",
            )
            transaction.record_cursor_feedback_wait(event)
            raise

        event = DelayedCursorFeedbackEvent(
            plan=transaction.pointer_plan_count,
            step=transaction.pointer_step_count,
            command_dx=commanded_x,
            command_dy=commanded_y,
            before=before,
            last=final,
            extra_polls=extra_polls,
            elapsed_millis=elapsed_millis,
            first_effect_millis=first_effect_millis,
            complete_effect_millis=complete_effect_millis,
            outcome="settled",
        )
        transaction.record_cursor_feedback_wait(event)
        return final

    @staticmethod
    def _validate_axis_transfer(
        *,
        transaction: _Transaction,
        commanded: int,
        observed: int,
        calibrated: bool,
        gain_upper: float | None,
        delayed_command: int,
        axis: str,
    ) -> tuple[bool, float | None]:
        if commanded != 0 and delayed_command != 0:
            raise _TransactionAbort(
                f"cursor_feedback_unresolved_delayed_command_before_move_{axis}"
            )
        if commanded == 0:
            if delayed_command == 0:
                if observed != 0:
                    raise _cursor_state_invalidated(
                        f"cursor_feedback_uncommanded_axis_{axis}",
                        CursorInvalidationCause.UNEXPECTED_CROSS_AXIS,
                    )
                return calibrated, gain_upper
            if observed == 0:
                return calibrated, gain_upper
            if (delayed_command > 0) != (observed > 0):
                raise _cursor_state_invalidated(
                    f"cursor_feedback_delayed_direction_mismatch_{axis}",
                    CursorInvalidationCause.UNEXPECTED_DIRECTION,
                )
            if (
                abs(observed)
                > abs(delayed_command) * MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT
            ):
                raise _cursor_state_invalidated(
                    f"cursor_transfer_gain_exceeded_{axis}:"
                    f"commanded=0:observed={observed}:"
                    f"delayed={delayed_command}:"
                    f"plan={transaction.pointer_plan_count}:"
                    f"step={transaction.pointer_step_count}",
                    CursorInvalidationCause.UNSUPPORTED_TRANSFER_GAIN,
                )
            gain_upper = InputCoordinator._updated_transfer_gain_upper(
                gain_upper,
                delayed_command,
                observed,
            )
            if gain_upper is not None:
                transaction.record_pointer_transfer_gain(axis, gain_upper)
            return True, gain_upper
        if observed == 0:
            return calibrated, gain_upper
        if (commanded > 0) != (observed > 0):
            raise _cursor_state_invalidated(
                f"cursor_feedback_direction_mismatch_{axis}",
                CursorInvalidationCause.UNEXPECTED_DIRECTION,
            )
        if (
            abs(observed)
            > abs(commanded) * MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT
        ):
            raise _cursor_state_invalidated(
                f"cursor_transfer_gain_exceeded_{axis}:"
                f"commanded={commanded}:observed={observed}:"
                f"delayed={delayed_command}:"
                f"plan={transaction.pointer_plan_count}:"
                f"step={transaction.pointer_step_count}",
                CursorInvalidationCause.UNSUPPORTED_TRANSFER_GAIN,
            )
        gain_upper = InputCoordinator._updated_transfer_gain_upper(
            gain_upper,
            commanded,
            observed,
        )
        if gain_upper is not None:
            transaction.record_pointer_transfer_gain(axis, gain_upper)
        return True, gain_upper

    @staticmethod
    def _updated_transfer_gain_upper(
        prior: float | None,
        commanded: int,
        observed: int,
    ) -> float | None:
        if (
            abs(commanded) < MIN_TRANSFER_GAIN_SAMPLE_HID_COUNT
            or observed == 0
        ):
            return prior
        candidate = min(
            float(MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT),
            (
                (abs(observed) + 0.5)
                / abs(commanded)
                * TRANSFER_GAIN_ESTIMATE_HEADROOM
            ),
        )
        # Never understate a larger supported response already observed in
        # this transaction. A lower later ratio may be only step-size or
        # acceleration variation, so it cannot safely narrow the upper value.
        return candidate if prior is None else max(prior, candidate)

    @staticmethod
    def _assert_feedback_sample_delta(
        *,
        transaction: _Transaction,
        commanded: int,
        delayed_command: int,
        observed: int,
        axis: str,
        phase: str,
    ) -> None:
        if commanded != 0 and delayed_command != 0:
            raise _TransactionAbort(
                f"cursor_feedback_unresolved_delayed_command_before_move_{axis}"
            )
        supported = commanded if commanded != 0 else delayed_command
        if supported == 0:
            if observed != 0:
                raise _cursor_state_invalidated(
                    f"cursor_feedback_uncommanded_axis_{axis}:{phase}",
                    CursorInvalidationCause.UNEXPECTED_CROSS_AXIS,
                )
            return
        if observed == 0:
            return
        if (supported > 0) != (observed > 0):
            raise _cursor_state_invalidated(
                f"cursor_feedback_direction_mismatch_{axis}:{phase}",
                CursorInvalidationCause.UNEXPECTED_DIRECTION,
            )
        supported_magnitude = abs(supported)
        if (
            abs(observed)
            > supported_magnitude * MAX_SUPPORTED_DEVICE_PX_PER_HID_COUNT
        ):
            raise _cursor_state_invalidated(
                f"cursor_transfer_gain_exceeded_{axis}:"
                f"phase={phase}:commanded={commanded}:"
                f"observed={observed}:delayed={delayed_command}:"
                f"plan={transaction.pointer_plan_count}:"
                f"step={transaction.pointer_step_count}",
                CursorInvalidationCause.UNSUPPORTED_TRANSFER_GAIN,
            )

    def _validate_pointer(
        self,
        transaction: _Transaction,
        intent: ApprovedPointerIntent,
        actual: ScreenPoint,
        validate: PointerValidator,
    ) -> None:
        def validate_pointer() -> InputValidation:
            # Deliberately after all movement and immediately before activation.
            self._assert_pointer_foreground(transaction, intent)
            self._assert_cursor_stable_in_target(
                transaction,
                intent,
                actual,
                phase="before_pointer_validation",
            )
            decision = validate(intent, actual)
            self._assert_pointer_foreground(transaction, intent)
            self._assert_cursor_stable_in_target(
                transaction,
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
        transaction: _Transaction,
        intent: ApprovedPointerIntent,
        actual: ScreenPoint,
        *,
        phase: str,
    ) -> None:
        self._require_physical_mouse_quiet(
            transaction.backend, phase=phase
        )
        current = self._current_position(
            transaction,
            phase=f"pointer_activation_{phase}",
        )
        if not intent.movement_bounds.contains(current):
            raise self._movement_bounds_invalidation(
                intent,
                f"cursor_left_verified_bounds_{phase}",
            )
        if current != actual:
            raise _cursor_state_invalidated(
                f"cursor_changed_{phase}",
                CursorInvalidationCause.PHYSICAL_INPUT_ACTIVITY,
            )
        if not intent.target_bounds.contains(current):
            raise _cursor_state_invalidated(
                f"cursor_left_verified_target_{phase}",
                CursorInvalidationCause.FEEDBACK_UNRESOLVED,
            )
        if transaction.pointer_geometry is not None:
            self._require_unchanged_window_geometry(
                transaction.backend,
                transaction.pointer_geometry,
                reason=f"runelite_geometry_changed_before_activation:{phase}",
            )
        if transaction.pointer_hwnd is None:
            raise _TransactionAbort(
                "pointer_foreground_hwnd_unavailable", blocked=True
            )
        self._assert_point_owned_by_window(
            transaction.backend,
            current,
            expected_pid=intent.expected_pid,
            expected_hwnd=transaction.pointer_hwnd,
            reason_prefix=f"cursor_{phase}",
        )

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
            self._assert_pointer_foreground(transaction, intent)
            self._assert_cursor_stable_in_target(
                transaction,
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
        if up_acknowledged:
            self._consume_owned_mouse_transition(
                transaction.backend, button
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
        try:
            rearmed_capabilities = transaction.backend._input_capabilities()
        except Exception as error:
            raise _TransactionAbort(
                "preactivation_rearm_capabilities_unavailable: "
                f"{type(error).__name__}: {error}"
            ) from error
        if (
            transaction.negotiated_capabilities is None
            or rearmed_capabilities != transaction.negotiated_capabilities
        ):
            raise _TransactionAbort(
                "preactivation_rearm_capabilities_changed"
            )
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
    def _assert_foreground(
        backend: _Backend, expected_pid: int
    ) -> Mapping[str, Any]:
        info = backend._assert_foreground(
            ("RuneLite",), expected_pid=expected_pid
        )
        if not isinstance(info, Mapping):
            raise _TransactionAbort("foreground_window_evidence_unavailable")
        return info

    @staticmethod
    def _verified_foreground_hwnd(
        info: Mapping[str, Any], *, expected_hwnd: int | None
    ) -> int:
        hwnd = info.get("hwnd")
        if not _is_int(hwnd) or hwnd <= 0:
            raise _cursor_state_invalidated(
                "pointer_foreground_hwnd_unavailable",
                CursorInvalidationCause.IDENTITY_CHANGED,
            )
        if expected_hwnd is not None and hwnd != expected_hwnd:
            raise _cursor_state_invalidated(
                "pointer_foreground_hwnd_mismatch",
                CursorInvalidationCause.IDENTITY_CHANGED,
            )
        return hwnd

    @staticmethod
    def _assert_same_foreground_window(
        info: Mapping[str, Any], expected_hwnd: int
    ) -> None:
        if info.get("hwnd") != expected_hwnd:
            raise _cursor_state_invalidated(
                "pointer_foreground_window_changed",
                CursorInvalidationCause.IDENTITY_CHANGED,
            )

    def _assert_pointer_foreground(
        self,
        transaction: _Transaction,
        intent: ApprovedPointerIntent,
    ) -> Mapping[str, Any]:
        try:
            info = self._assert_foreground(
                transaction.backend, intent.expected_pid
            )
        except _TransactionAbort as error:
            if error.failure_kind is InputFailureKind.CURSOR_STATE_INVALIDATED:
                raise
            raise _cursor_state_invalidated(
                f"pointer_foreground_identity_invalid: {error}",
                CursorInvalidationCause.IDENTITY_CHANGED,
            ) from error
        except Exception as error:
            raise _cursor_state_invalidated(
                "pointer_foreground_identity_invalid: "
                f"{type(error).__name__}: {error}",
                CursorInvalidationCause.IDENTITY_CHANGED,
            ) from error
        hwnd = self._verified_foreground_hwnd(
            info, expected_hwnd=intent.expected_hwnd
        )
        if transaction.pointer_hwnd is None:
            transaction.pointer_hwnd = hwnd
        else:
            self._assert_same_foreground_window(
                info, transaction.pointer_hwnd
            )
        return info

    def _assert_pinned_pointer_foreground(
        self,
        transaction: _Transaction,
        expected_pid: int,
    ) -> Mapping[str, Any]:
        info = self._assert_foreground(transaction.backend, expected_pid)
        if transaction.pointer_hwnd is None:
            raise _TransactionAbort(
                "pointer_foreground_hwnd_unavailable", blocked=True
            )
        self._assert_same_foreground_window(
            info, transaction.pointer_hwnd
        )
        return info

    @staticmethod
    def _assert_point_owned_by_window(
        backend: _Backend,
        point: ScreenPoint,
        *,
        expected_pid: int,
        expected_hwnd: int,
        reason_prefix: str = "cursor_reacquisition",
    ) -> None:
        info = backend._window_info_at_point((point.x, point.y))
        if not isinstance(info, Mapping):
            raise _cursor_state_invalidated(
                f"{reason_prefix}_point_owner_unavailable",
                CursorInvalidationCause.POINT_OWNER_MISMATCH,
            )
        if (
            info.get("hwnd") != expected_hwnd
            or info.get("pid") != expected_pid
        ):
            raise _cursor_state_invalidated(
                f"{reason_prefix}_point_owner_mismatch",
                CursorInvalidationCause.POINT_OWNER_MISMATCH,
            )

    @staticmethod
    def _current_position(
        transaction: _Transaction,
        *,
        phase: str,
    ) -> ScreenPoint:
        raw = transaction.backend._current_position()
        if (
            not isinstance(raw, tuple)
            or len(raw) != 2
            or not _is_int(raw[0])
            or not _is_int(raw[1])
        ):
            raise _TransactionAbort("backend cursor position is invalid")
        point = ScreenPoint(raw[0], raw[1])
        transaction.record_cursor_position(point, phase)
        return point

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
        safely_unsent_ok = (
            not state.connected
            and not state.arm_acknowledged
            and not state.stop_all_acknowledged
            and not state.disarm_acknowledged
            and not state.firmware_status_acknowledged
            and state.firmware_status is None
            and not snapshot.commands
            and snapshot.unresolved_count == 0
            and snapshot.failed_count == 0
            and snapshot.ack_missing_count == 0
            and state.ledger_closed
            and state.backend_closed
            and not state.context_cancel_attempted
            and not state.context_cancel_acknowledged
            and transaction is not None
            and transaction.ledger_complete
        )
        wire_proof_ok = _wire_proof_complete(snapshot.commands)
        arduino_command_failed = bool(
            state.arduino_command_failed
            or snapshot.unresolved_count > 0
            or snapshot.failed_count > 0
            or snapshot.ack_missing_count > 0
        )
        if (
            state.body_status == "PASS"
            and cleanup_ok
            and wire_proof_ok
            and not errors
        ):
            status = "PASS"
            reason = "input_transaction_succeeded"
        elif state.body_status == "BLOCKED" and (
            cleanup_ok or safely_unsent_ok
        ):
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
            required_capabilities=state.required_capabilities,
            negotiated_capabilities=state.negotiated_capabilities,
            activation_boundary=(
                transaction.activation_boundary
                if transaction is not None
                else None
            ),
            failure_kind=state.failure_kind,
            cursor_invalidation_cause=state.cursor_invalidation_cause,
            context_cancel_attempted=state.context_cancel_attempted,
            context_cancel_acknowledged=state.context_cancel_acknowledged,
            errors=errors,
            cursor_feedback=(
                transaction.cursor_feedback_evidence()
                if transaction is not None
                else CursorFeedbackEvidence()
            ),
            pointer_motion=(
                transaction.pointer_motion_evidence()
                if transaction is not None
                else PointerMotionEvidence()
            ),
            cursor_reacquisition=state.cursor_reacquisition,
            pointer_geometry=(
                transaction.pointer_geometry
                if transaction is not None
                else None
            ),
            cursor_samples=(
                tuple(transaction.cursor_position_samples)
                if transaction is not None
                else ()
            ),
            observability=(
                ObservabilityEvidence(
                    timing=transaction.timing,
                    wait_state=(
                        WaitState.ARDUINO_COMMAND_FAILED
                        if arduino_command_failed
                        else None
                    ),
                    observed_wait_states=tuple(
                        dict.fromkeys(
                            (
                                *transaction.observed_wait_states,
                                *(
                                    (WaitState.ARDUINO_COMMAND_FAILED,)
                                    if arduino_command_failed
                                    else ()
                                ),
                            )
                        )
                    ),
                )
                if transaction is not None
                else ObservabilityEvidence(
                    wait_state=(
                        WaitState.ARDUINO_COMMAND_FAILED
                        if arduino_command_failed
                        else None
                    ),
                    observed_wait_states=(
                        (WaitState.ARDUINO_COMMAND_FAILED,)
                        if arduino_command_failed
                        else ()
                    ),
                )
            ),
        )

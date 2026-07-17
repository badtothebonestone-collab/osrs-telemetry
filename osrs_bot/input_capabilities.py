from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import re
from typing import Any


LEGACY_PROTOCOL = "arduino_hid.v1"
EXPANDED_PROTOCOL = "arduino_hid.v2"
LEGACY_SCHEMA = "input_capabilities.v1"
EXPANDED_SCHEMA = "input_capabilities.v2"

LEGACY_MAX_MOVE_DELTA = 20
MAX_SHORT_HOLD_MS = 250
MAX_CAMERA_HOLD_MS = 600
MAX_WHEEL_STEP = 3

_SUPPORTED_PROTOCOLS = frozenset({LEGACY_PROTOCOL, EXPANDED_PROTOCOL})
_BUTTONS = ("left", "right", "middle")
_PRODUCTION_CLICK_BUTTONS = frozenset({"left", "right"})
_CAMERA_DIRECTIONS = ("left", "right", "up", "down")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")


class InputCapabilityError(ValueError):
    """A negotiated capability payload or typed requirement is invalid."""


class InputOperation(str, Enum):
    POINTER_MOVE = "pointer_move"
    POINTER_CLICK = "pointer_click"
    GENERIC_KEY_PRESS = "generic_key_press"
    CAMERA_KEY_HOLD = "camera_key_hold"
    CAMERA_ZOOM = "camera_zoom"
    CLEANUP = "cleanup"


def _require_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InputCapabilityError(f"{name} must be a mapping")
    return value


def _require_bool(mapping: Mapping[str, object], key: str) -> bool:
    if key not in mapping:
        raise InputCapabilityError(f"missing capability field: {key}")
    value = mapping[key]
    if not isinstance(value, bool):
        raise InputCapabilityError(f"capability field {key} must be bool")
    return value


def _require_int(
    mapping: Mapping[str, object],
    key: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if key not in mapping:
        raise InputCapabilityError(f"missing capability field: {key}")
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputCapabilityError(f"capability field {key} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        interval = f"{minimum}..{maximum}" if maximum is not None else f">={minimum}"
        raise InputCapabilityError(
            f"capability field {key} must be in {interval}; got {value}"
        )
    return value


def _require_status_count(mapping: Mapping[str, object], key: str) -> int:
    if key not in mapping:
        raise InputCapabilityError(f"missing status field: {key}")
    value = mapping[key]
    # The established line parser represents wire 0/1 as bool. Accept that
    # exact normalized form for held-input counters, but no strings/floats.
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, int) or value < 0:
        raise InputCapabilityError(f"status field {key} must be a non-negative integer")
    return value


def _require_text(mapping: Mapping[str, object], key: str, *, source: str) -> str:
    if key not in mapping:
        raise InputCapabilityError(f"missing {source} field: {key}")
    value = mapping[key]
    if not isinstance(value, str) or not value.strip():
        raise InputCapabilityError(f"{source} field {key} must be non-empty text")
    if value != value.strip():
        raise InputCapabilityError(f"{source} field {key} must not contain outer whitespace")
    return value


def _parse_tokens(
    value: object,
    *,
    field: str,
    allowed: tuple[str, ...],
    allow_none: bool = False,
) -> frozenset[str]:
    if not isinstance(value, str):
        raise InputCapabilityError(f"capability field {field} must be comma-separated text")
    if allow_none and value in {"", "none"}:
        return frozenset()
    tokens = value.split(",")
    if not tokens or any(not token or token != token.strip() for token in tokens):
        raise InputCapabilityError(f"capability field {field} is malformed")
    if len(tokens) != len(set(tokens)):
        raise InputCapabilityError(f"capability field {field} contains duplicates")
    unknown = [token for token in tokens if token not in allowed]
    if unknown:
        raise InputCapabilityError(
            f"capability field {field} contains unsupported value: {unknown[0]}"
        )
    return frozenset(tokens)


def _ordered(values: frozenset[str], order: tuple[str, ...]) -> list[str]:
    return [value for value in order if value in values]


@dataclass(frozen=True, slots=True)
class InputCapabilities:
    schema_version: str
    protocol_version: str
    firmware_version: str
    pointer: bool
    mouse: bool
    relative_move: bool
    max_move_delta: int
    buttons: frozenset[str]
    button_down_up: bool
    click: bool
    max_click_hold_ms: int
    keyboard: bool
    key_set: str
    key_press: bool
    max_key_press_ms: int
    hold_keys: bool
    max_hold_keys_ms: int
    camera_key_hold: bool
    camera_keys: frozenset[str]
    max_camera_hold_ms: int
    wheel: bool
    max_wheel_step: int
    arm: bool
    watchdog: bool
    watchdog_ms: int
    stop_all: bool
    disarm: bool
    status: bool
    reset_safe: bool

    def __post_init__(self) -> None:
        expected_schema = {
            LEGACY_PROTOCOL: LEGACY_SCHEMA,
            EXPANDED_PROTOCOL: EXPANDED_SCHEMA,
        }.get(self.protocol_version)
        if expected_schema is None:
            raise InputCapabilityError(
                f"unsupported input protocol: {self.protocol_version or 'missing'}"
            )
        if self.schema_version != expected_schema:
            raise InputCapabilityError(
                f"schema/protocol mismatch: {self.schema_version!r} for {self.protocol_version}"
            )
        if not isinstance(self.firmware_version, str) or not _VERSION.fullmatch(
            self.firmware_version
        ):
            raise InputCapabilityError("firmware_version must be a semantic version")

        boolean_fields = (
            "pointer",
            "mouse",
            "relative_move",
            "button_down_up",
            "click",
            "keyboard",
            "key_press",
            "hold_keys",
            "camera_key_hold",
            "wheel",
            "arm",
            "watchdog",
            "stop_all",
            "disarm",
            "status",
            "reset_safe",
        )
        for field_name in boolean_fields:
            if not isinstance(getattr(self, field_name), bool):
                raise InputCapabilityError(f"{field_name} must be bool")

        buttons = frozenset(self.buttons)
        camera_keys = frozenset(self.camera_keys)
        if not buttons.issubset(_BUTTONS):
            raise InputCapabilityError("buttons contains an unsupported button")
        if not camera_keys.issubset(_CAMERA_DIRECTIONS):
            raise InputCapabilityError("camera_keys contains a non-camera direction")
        object.__setattr__(self, "buttons", buttons)
        object.__setattr__(self, "camera_keys", camera_keys)
        if self.key_set != "basic":
            raise InputCapabilityError("key_set must be 'basic'")

        numeric_fields = (
            "max_move_delta",
            "max_click_hold_ms",
            "max_key_press_ms",
            "max_hold_keys_ms",
            "max_camera_hold_ms",
            "max_wheel_step",
            "watchdog_ms",
        )
        for field_name in numeric_fields:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise InputCapabilityError(f"{field_name} must be a non-negative integer")

        self._validate_dependency("relative_move", self.relative_move, self.max_move_delta)
        self._validate_dependency(
            "button_down_up/click",
            self.button_down_up or self.click,
            self.max_click_hold_ms,
        )
        self._validate_dependency("key_press", self.key_press, self.max_key_press_ms)
        self._validate_dependency("hold_keys", self.hold_keys, self.max_hold_keys_ms)
        self._validate_dependency(
            "camera_key_hold", self.camera_key_hold, self.max_camera_hold_ms
        )
        self._validate_dependency("wheel", self.wheel, self.max_wheel_step)
        self._validate_dependency("watchdog", self.watchdog, self.watchdog_ms)

        if self.max_move_delta > LEGACY_MAX_MOVE_DELTA:
            raise InputCapabilityError("max_move_delta exceeds the supported 20-count limit")
        if self.max_click_hold_ms > MAX_SHORT_HOLD_MS:
            raise InputCapabilityError("max_click_hold_ms exceeds the supported 250 ms limit")
        if self.max_key_press_ms > MAX_SHORT_HOLD_MS:
            raise InputCapabilityError("max_key_press_ms exceeds the supported 250 ms limit")
        if self.max_hold_keys_ms > MAX_SHORT_HOLD_MS:
            raise InputCapabilityError("max_hold_keys_ms exceeds the supported 250 ms limit")
        if self.max_camera_hold_ms > MAX_CAMERA_HOLD_MS:
            raise InputCapabilityError("max_camera_hold_ms exceeds the v2 600 ms limit")
        if self.max_wheel_step > MAX_WHEEL_STEP:
            raise InputCapabilityError("max_wheel_step exceeds the supported magnitude 3")
        if self.camera_key_hold:
            if not self.camera_keys:
                raise InputCapabilityError("camera_key_hold requires camera_keys")
            if self.max_camera_hold_ms >= self.watchdog_ms:
                raise InputCapabilityError(
                    "max_camera_hold_ms must be strictly below watchdog_ms"
                )
        elif self.camera_keys:
            raise InputCapabilityError("camera_keys must be empty when camera_key_hold is false")

    @staticmethod
    def _validate_dependency(name: str, enabled: bool, limit: int) -> None:
        if enabled and limit <= 0:
            raise InputCapabilityError(f"{name} requires a positive negotiated limit")
        if not enabled and limit != 0:
            raise InputCapabilityError(f"{name} limit must be zero when unsupported")

    @classmethod
    def from_negotiation(
        cls,
        identity: Mapping[str, object],
        capabilities: Mapping[str, object],
        status: Mapping[str, object],
    ) -> "InputCapabilities":
        identity = _require_mapping(identity, "identity")
        capabilities = _require_mapping(capabilities, "capabilities")
        status = _require_mapping(status, "status")

        protocol = _require_text(identity, "protocol", source="identity")
        if protocol not in _SUPPORTED_PROTOCOLS:
            raise InputCapabilityError(f"unsupported input protocol: {protocol}")
        firmware = _require_text(identity, "version", source="identity")
        if not _VERSION.fullmatch(firmware):
            raise InputCapabilityError("identity field version must be a semantic version")

        armed = status.get("armed")
        if not isinstance(armed, bool):
            raise InputCapabilityError("status field armed must be bool")
        _require_status_count(status, "keysDown")
        _require_status_count(status, "mouseButtonsDown")
        status_watchdog = _require_int(status, "watchdogMs", minimum=1)

        if protocol == LEGACY_PROTOCOL:
            return cls._from_v1(firmware, capabilities, status_watchdog)
        return cls._from_v2(firmware, capabilities, status_watchdog)

    @classmethod
    def _from_v1(
        cls,
        firmware: str,
        capabilities: Mapping[str, object],
        status_watchdog: int,
    ) -> "InputCapabilities":
        required_true = (
            "mouse",
            "keyboard",
            "relativeMove",
            "holdKeys",
            "watchdog",
            "stopAll",
            "resetSafe",
        )
        for key in required_true:
            if not _require_bool(capabilities, key):
                raise InputCapabilityError(f"legacy protocol requires capability: {key}")
        buttons = _parse_tokens(
            capabilities.get("buttons"), field="buttons", allowed=_BUTTONS
        )
        if not _PRODUCTION_CLICK_BUTTONS.issubset(buttons):
            raise InputCapabilityError("legacy buttons must include left and right")
        if capabilities.get("keys") != "basic":
            raise InputCapabilityError("legacy capability field keys must be 'basic'")

        return cls(
            schema_version=LEGACY_SCHEMA,
            protocol_version=LEGACY_PROTOCOL,
            firmware_version=firmware,
            pointer=True,
            mouse=True,
            relative_move=True,
            max_move_delta=LEGACY_MAX_MOVE_DELTA,
            buttons=buttons,
            button_down_up=True,
            click=True,
            max_click_hold_ms=MAX_SHORT_HOLD_MS,
            keyboard=True,
            key_set="basic",
            key_press=True,
            max_key_press_ms=MAX_SHORT_HOLD_MS,
            hold_keys=True,
            max_hold_keys_ms=MAX_SHORT_HOLD_MS,
            camera_key_hold=False,
            camera_keys=frozenset(),
            max_camera_hold_ms=0,
            wheel=False,
            max_wheel_step=0,
            arm=True,
            watchdog=True,
            watchdog_ms=status_watchdog,
            stop_all=True,
            disarm=True,
            status=True,
            reset_safe=True,
        )

    @classmethod
    def _from_v2(
        cls,
        firmware: str,
        capabilities: Mapping[str, object],
        status_watchdog: int,
    ) -> "InputCapabilities":
        if _require_text(capabilities, "schema", source="capability") != EXPANDED_SCHEMA:
            raise InputCapabilityError("v2 capability schema must be input_capabilities.v2")
        caps_protocol = _require_text(capabilities, "protocol", source="capability")
        if caps_protocol != EXPANDED_PROTOCOL:
            raise InputCapabilityError(
                f"identity/CAPS protocol mismatch: {EXPANDED_PROTOCOL} != {caps_protocol}"
            )
        caps_firmware = _require_text(
            capabilities, "firmwareVersion", source="capability"
        )
        if caps_firmware != firmware:
            raise InputCapabilityError(
                f"identity/CAPS firmware mismatch: {firmware} != {caps_firmware}"
            )

        pointer = _require_bool(capabilities, "pointer")
        mouse = _require_bool(capabilities, "mouse")
        relative_move = _require_bool(capabilities, "relativeMove")
        max_move_delta = _require_int(
            capabilities, "maxMoveDelta", maximum=LEGACY_MAX_MOVE_DELTA
        )
        buttons = _parse_tokens(
            capabilities.get("buttons"), field="buttons", allowed=_BUTTONS
        )
        button_down_up = _require_bool(capabilities, "buttonDownUp")
        click = _require_bool(capabilities, "click")
        max_click_hold_ms = _require_int(
            capabilities, "maxClickHoldMs", maximum=MAX_SHORT_HOLD_MS
        )
        keyboard = _require_bool(capabilities, "keyboard")
        key_set = _require_text(capabilities, "keys", source="capability")
        if key_set != "basic":
            raise InputCapabilityError("v2 capability field keys must be 'basic'")
        key_press = _require_bool(capabilities, "keyPress")
        max_key_press_ms = _require_int(
            capabilities, "maxKeyPressMs", maximum=MAX_SHORT_HOLD_MS
        )
        hold_keys = _require_bool(capabilities, "holdKeys")
        max_hold_keys_ms = _require_int(
            capabilities, "maxHoldKeysMs", maximum=MAX_SHORT_HOLD_MS
        )
        camera_key_hold = _require_bool(capabilities, "cameraKeyHold")
        if "cameraKeys" not in capabilities:
            raise InputCapabilityError("missing capability field: cameraKeys")
        camera_keys = _parse_tokens(
            capabilities["cameraKeys"],
            field="cameraKeys",
            allowed=_CAMERA_DIRECTIONS,
            allow_none=not camera_key_hold,
        )
        max_camera_hold_ms = _require_int(
            capabilities, "maxCameraHoldMs", maximum=MAX_CAMERA_HOLD_MS
        )
        wheel = _require_bool(capabilities, "wheel")
        max_wheel_step = _require_int(
            capabilities, "maxWheelStep", maximum=MAX_WHEEL_STEP
        )
        arm = _require_bool(capabilities, "arm")
        watchdog = _require_bool(capabilities, "watchdog")
        watchdog_ms = _require_int(capabilities, "watchdogMs", minimum=1)
        stop_all = _require_bool(capabilities, "stopAll")
        disarm = _require_bool(capabilities, "disarm")
        status_cap = _require_bool(capabilities, "status")
        reset_safe = _require_bool(capabilities, "resetSafe")

        if watchdog_ms != status_watchdog:
            raise InputCapabilityError(
                f"CAPS/STATUS watchdog mismatch: {watchdog_ms} != {status_watchdog}"
            )
        baseline = {
            "pointer": pointer,
            "mouse": mouse,
            "relativeMove": relative_move,
            "buttonDownUp": button_down_up,
            "click": click,
            "keyboard": keyboard,
            "keyPress": key_press,
            "holdKeys": hold_keys,
            "arm": arm,
            "watchdog": watchdog,
            "stopAll": stop_all,
            "disarm": disarm,
            "status": status_cap,
            "resetSafe": reset_safe,
        }
        missing = next((name for name, enabled in baseline.items() if not enabled), None)
        if missing is not None:
            raise InputCapabilityError(f"v2 protocol requires capability: {missing}")
        if not _PRODUCTION_CLICK_BUTTONS.issubset(buttons):
            raise InputCapabilityError("v2 buttons must include left and right")

        return cls(
            schema_version=EXPANDED_SCHEMA,
            protocol_version=EXPANDED_PROTOCOL,
            firmware_version=firmware,
            pointer=pointer,
            mouse=mouse,
            relative_move=relative_move,
            max_move_delta=max_move_delta,
            buttons=buttons,
            button_down_up=button_down_up,
            click=click,
            max_click_hold_ms=max_click_hold_ms,
            keyboard=keyboard,
            key_set=key_set,
            key_press=key_press,
            max_key_press_ms=max_key_press_ms,
            hold_keys=hold_keys,
            max_hold_keys_ms=max_hold_keys_ms,
            camera_key_hold=camera_key_hold,
            camera_keys=camera_keys,
            max_camera_hold_ms=max_camera_hold_ms,
            wheel=wheel,
            max_wheel_step=max_wheel_step,
            arm=arm,
            watchdog=watchdog,
            watchdog_ms=watchdog_ms,
            stop_all=stop_all,
            disarm=disarm,
            status=status_cap,
            reset_safe=reset_safe,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema_version,
            "protocolVersion": self.protocol_version,
            "firmwareVersion": self.firmware_version,
            "pointer": self.pointer,
            "mouse": self.mouse,
            "relativeMove": self.relative_move,
            "maxMoveDelta": self.max_move_delta,
            "buttons": _ordered(self.buttons, _BUTTONS),
            "buttonDownUp": self.button_down_up,
            "click": self.click,
            "maxClickHoldMs": self.max_click_hold_ms,
            "keyboard": self.keyboard,
            "keys": self.key_set,
            "keyPress": self.key_press,
            "maxKeyPressMs": self.max_key_press_ms,
            "holdKeys": self.hold_keys,
            "maxHoldKeysMs": self.max_hold_keys_ms,
            "cameraKeyHold": self.camera_key_hold,
            "cameraKeys": _ordered(self.camera_keys, _CAMERA_DIRECTIONS),
            "maxCameraHoldMs": self.max_camera_hold_ms,
            "wheel": self.wheel,
            "maxWheelStep": self.max_wheel_step,
            "arm": self.arm,
            "watchdog": self.watchdog,
            "watchdogMs": self.watchdog_ms,
            "stopAll": self.stop_all,
            "disarm": self.disarm,
            "status": self.status,
            "resetSafe": self.reset_safe,
        }


@dataclass(frozen=True, slots=True)
class CapabilityValidation:
    allowed: bool
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("allowed must be bool")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("reason must be non-empty text")

    def to_dict(self) -> dict[str, object]:
        return {"allowed": self.allowed, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class RequiredInputCapabilities:
    operation: InputOperation
    button: str | None = None
    click_hold_ms: int | None = None
    key_press_ms: int | None = None
    camera_direction: str | None = None
    camera_hold_ms: int | None = None
    wheel_amount: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, InputOperation):
            try:
                object.__setattr__(self, "operation", InputOperation(self.operation))
            except (TypeError, ValueError):
                raise InputCapabilityError("operation must be a supported InputOperation") from None

        populated = {
            "button": self.button is not None,
            "click_hold_ms": self.click_hold_ms is not None,
            "key_press_ms": self.key_press_ms is not None,
            "camera_direction": self.camera_direction is not None,
            "camera_hold_ms": self.camera_hold_ms is not None,
            "wheel_amount": self.wheel_amount is not None,
        }
        expected = {
            InputOperation.POINTER_MOVE: frozenset(),
            InputOperation.POINTER_CLICK: frozenset({"button", "click_hold_ms"}),
            InputOperation.GENERIC_KEY_PRESS: frozenset({"key_press_ms"}),
            InputOperation.CAMERA_KEY_HOLD: frozenset(
                {"camera_direction", "camera_hold_ms"}
            ),
            InputOperation.CAMERA_ZOOM: frozenset({"wheel_amount"}),
            InputOperation.CLEANUP: frozenset(),
        }[self.operation]
        actual = frozenset(name for name, present in populated.items() if present)
        if actual != expected:
            raise InputCapabilityError(
                f"{self.operation.value} requires exactly: {','.join(sorted(expected)) or 'no parameters'}"
            )

        if self.button is not None and self.button not in _PRODUCTION_CLICK_BUTTONS:
            raise InputCapabilityError("button must be left or right")
        if self.click_hold_ms is not None:
            self._require_bounded_int(
                self.click_hold_ms, "click_hold_ms", MAX_SHORT_HOLD_MS
            )
        if self.key_press_ms is not None:
            self._require_bounded_int(
                self.key_press_ms, "key_press_ms", MAX_SHORT_HOLD_MS
            )
        if (
            self.camera_direction is not None
            and self.camera_direction not in _CAMERA_DIRECTIONS
        ):
            raise InputCapabilityError(
                "camera_direction must be left, right, up, or down"
            )
        if self.camera_hold_ms is not None:
            self._require_bounded_int(
                self.camera_hold_ms, "camera_hold_ms", MAX_CAMERA_HOLD_MS
            )
        if self.wheel_amount is not None:
            if (
                isinstance(self.wheel_amount, bool)
                or not isinstance(self.wheel_amount, int)
                or self.wheel_amount == 0
                or abs(self.wheel_amount) > MAX_WHEEL_STEP
            ):
                raise InputCapabilityError(
                    f"wheel_amount must be nonzero and within -{MAX_WHEEL_STEP}..{MAX_WHEEL_STEP}"
                )

    @staticmethod
    def _require_bounded_int(value: object, name: str, maximum: int) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= maximum
        ):
            raise InputCapabilityError(f"{name} must be between 1 and {maximum}")

    @classmethod
    def pointer_move(cls) -> "RequiredInputCapabilities":
        return cls(InputOperation.POINTER_MOVE)

    @classmethod
    def pointer_click(
        cls, *, button: str = "left", hold_ms: int = 50
    ) -> "RequiredInputCapabilities":
        return cls(
            InputOperation.POINTER_CLICK,
            button=button,
            click_hold_ms=hold_ms,
        )

    @classmethod
    def generic_key_press(cls, hold_ms: int) -> "RequiredInputCapabilities":
        return cls(InputOperation.GENERIC_KEY_PRESS, key_press_ms=hold_ms)

    @classmethod
    def camera_hold(
        cls, direction: str, duration_ms: int
    ) -> "RequiredInputCapabilities":
        return cls(
            InputOperation.CAMERA_KEY_HOLD,
            camera_direction=direction,
            camera_hold_ms=duration_ms,
        )

    @classmethod
    def camera_zoom(cls, amount: int) -> "RequiredInputCapabilities":
        return cls(InputOperation.CAMERA_ZOOM, wheel_amount=amount)

    @classmethod
    def cleanup(cls) -> "RequiredInputCapabilities":
        return cls(InputOperation.CLEANUP)

    def missing_reason(self, capabilities: InputCapabilities) -> str | None:
        if not isinstance(capabilities, InputCapabilities):
            raise TypeError("capabilities must be InputCapabilities")

        if self.operation is InputOperation.CLEANUP:
            checks = (
                (capabilities.stop_all, "stop_all"),
                (capabilities.disarm, "disarm"),
                (capabilities.status, "status"),
                (capabilities.reset_safe, "reset_safe"),
            )
        else:
            checks = (
                (capabilities.arm, "arm"),
                (capabilities.watchdog, "watchdog"),
                (capabilities.stop_all, "stop_all"),
                (capabilities.disarm, "disarm"),
                (capabilities.status, "status"),
                (capabilities.reset_safe, "reset_safe"),
            )
        for available, name in checks:
            if not available:
                return f"missing_capability:{name}"

        if self.operation is InputOperation.CLEANUP:
            return None
        if self.operation in {InputOperation.POINTER_MOVE, InputOperation.POINTER_CLICK}:
            for available, name in (
                (capabilities.pointer, "pointer"),
                (capabilities.mouse, "mouse"),
                (capabilities.relative_move, "relative_move"),
            ):
                if not available:
                    return f"missing_capability:{name}"
            if capabilities.max_move_delta <= 0:
                return "invalid_limit:max_move_delta"
        if self.operation is InputOperation.POINTER_CLICK:
            if not capabilities.button_down_up:
                return "missing_capability:button_down_up"
            assert self.button is not None
            assert self.click_hold_ms is not None
            if self.button not in capabilities.buttons:
                return f"unsupported_button:{self.button}"
            if self.click_hold_ms > capabilities.max_click_hold_ms:
                return (
                    "click_hold_exceeds_negotiated_max:"
                    f"requested={self.click_hold_ms},max={capabilities.max_click_hold_ms}"
                )
        elif self.operation is InputOperation.GENERIC_KEY_PRESS:
            if not capabilities.keyboard:
                return "missing_capability:keyboard"
            if not capabilities.key_press:
                return "missing_capability:key_press"
            assert self.key_press_ms is not None
            if self.key_press_ms > capabilities.max_key_press_ms:
                return (
                    "key_press_exceeds_negotiated_max:"
                    f"requested={self.key_press_ms},max={capabilities.max_key_press_ms}"
                )
        elif self.operation is InputOperation.CAMERA_KEY_HOLD:
            if not capabilities.keyboard:
                return "missing_capability:keyboard"
            if not capabilities.camera_key_hold:
                return "missing_capability:camera_key_hold"
            assert self.camera_direction is not None
            assert self.camera_hold_ms is not None
            if self.camera_direction not in capabilities.camera_keys:
                return f"unsupported_camera_direction:{self.camera_direction}"
            if self.camera_hold_ms > capabilities.max_camera_hold_ms:
                return (
                    "camera_hold_exceeds_negotiated_max:"
                    f"requested={self.camera_hold_ms},max={capabilities.max_camera_hold_ms}"
                )
        elif self.operation is InputOperation.CAMERA_ZOOM:
            if not capabilities.mouse:
                return "missing_capability:mouse"
            if not capabilities.wheel:
                return "missing_capability:wheel"
            assert self.wheel_amount is not None
            if abs(self.wheel_amount) > capabilities.max_wheel_step:
                return (
                    "wheel_amount_exceeds_negotiated_max:"
                    f"requested={self.wheel_amount},max={capabilities.max_wheel_step}"
                )
        return None

    def validate(self, capabilities: InputCapabilities) -> CapabilityValidation:
        reason = self.missing_reason(capabilities)
        if reason is None:
            return CapabilityValidation(True, "required_input_capabilities_satisfied")
        return CapabilityValidation(False, reason)

    def require(self, capabilities: InputCapabilities) -> None:
        reason = self.missing_reason(capabilities)
        if reason is not None:
            raise InputCapabilityError(reason)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": "required_input_capabilities.v1",
            "operation": self.operation.value,
        }
        if self.button is not None:
            payload["button"] = self.button
        if self.click_hold_ms is not None:
            payload["clickHoldMs"] = self.click_hold_ms
        if self.key_press_ms is not None:
            payload["keyPressMs"] = self.key_press_ms
        if self.camera_direction is not None:
            payload["cameraDirection"] = self.camera_direction
        if self.camera_hold_ms is not None:
            payload["cameraHoldMs"] = self.camera_hold_ms
        if self.wheel_amount is not None:
            payload["wheelAmount"] = self.wheel_amount
        return payload


__all__ = [
    "CapabilityValidation",
    "EXPANDED_PROTOCOL",
    "InputCapabilities",
    "InputCapabilityError",
    "InputOperation",
    "LEGACY_PROTOCOL",
    "MAX_CAMERA_HOLD_MS",
    "MAX_SHORT_HOLD_MS",
    "MAX_WHEEL_STEP",
    "RequiredInputCapabilities",
]

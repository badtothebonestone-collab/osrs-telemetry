from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import math
import re
from urllib.parse import urlsplit

from .behavior import BehaviorConfig, DEFAULT_BEHAVIOR_CONFIG
from .camera import CameraKeyCapabilities


MAX_REQUEST_TIMEOUT_SECONDS = 30.0
MAX_POLL_SECONDS = 5.0
MAX_OBSERVATIONS = 500_000
MAX_ACTIONS = 500
MAX_RUNTIME_SECONDS = 90_000.0
MAX_VERIFICATION_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Validated machine/session settings; never task facts or safety policy."""

    endpoint: str = "http://127.0.0.1:8893"
    auth_token: str | None = field(default=None, repr=False)
    request_timeout_seconds: float = 3.0
    arduino_port: str | None = None
    poll_seconds: float = 0.25
    max_observations: int = 4_800
    max_actions: int = 100
    max_runtime_seconds: float = 1_200.0
    verification_timeout_seconds: float = 75.0
    behavior: BehaviorConfig = DEFAULT_BEHAVIOR_CONFIG
    camera_key_capabilities: CameraKeyCapabilities = CameraKeyCapabilities(
        max_hold_millis=600,
        source="arduino_hid.v2.negotiated_contract",
    )

    def __post_init__(self) -> None:
        _validate_endpoint(self.endpoint)
        _validate_optional_text("auth_token", self.auth_token, maximum_length=4_096)
        _validate_optional_text("arduino_port", self.arduino_port, maximum_length=128)
        _finite_bound(
            "request_timeout_seconds",
            self.request_timeout_seconds,
            maximum=MAX_REQUEST_TIMEOUT_SECONDS,
        )
        _finite_bound("poll_seconds", self.poll_seconds, maximum=MAX_POLL_SECONDS)
        _integer_bound("max_observations", self.max_observations, maximum=MAX_OBSERVATIONS)
        _integer_bound("max_actions", self.max_actions, maximum=MAX_ACTIONS)
        _finite_bound(
            "max_runtime_seconds",
            self.max_runtime_seconds,
            maximum=MAX_RUNTIME_SECONDS,
        )
        _finite_bound(
            "verification_timeout_seconds",
            self.verification_timeout_seconds,
            maximum=MAX_VERIFICATION_TIMEOUT_SECONDS,
        )
        if self.verification_timeout_seconds > self.max_runtime_seconds:
            raise ValueError(
                "verification_timeout_seconds must not exceed max_runtime_seconds"
            )
        if not isinstance(self.behavior, BehaviorConfig):
            raise TypeError("behavior must be BehaviorConfig")
        if not isinstance(self.camera_key_capabilities, CameraKeyCapabilities):
            raise TypeError(
                "camera_key_capabilities must be CameraKeyCapabilities"
            )

    def validated_for_mode(self, *, execute: bool) -> RuntimeConfig:
        """Return this immutable config after enforcing mode-specific requirements."""

        if not isinstance(execute, bool):
            raise TypeError("execute must be a bool")
        if execute and self.arduino_port is None:
            raise ValueError("execute mode requires arduino_port")
        return self


def _validate_endpoint(endpoint: object) -> None:
    if (
        not isinstance(endpoint, str)
        or endpoint != endpoint.strip()
        or not endpoint
        or "\\" in endpoint
        or any(
            character.isspace()
            or ord(character) < 32
            or ord(character) == 127
            for character in endpoint
        )
    ):
        raise ValueError("endpoint must be a non-empty HTTP(S) origin")
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError as error:
        raise ValueError("endpoint must be a valid HTTP(S) origin") from error
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.hostname is None
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or port is None
        or not 1 <= port <= 65_535
    ):
        raise ValueError("endpoint must be an HTTP(S) origin with an explicit port")

    hostname = parts.hostname
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if (
            len(hostname) > 253
            or any(
                re.fullmatch(
                    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
                    label,
                )
                is None
                for label in hostname.split(".")
            )
        ):
            raise ValueError("endpoint hostname is invalid") from None


def _validate_optional_text(field_name: str, value: object, *, maximum_length: int) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum_length
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(
            f"{field_name} must be non-empty, trimmed text of at most {maximum_length} characters"
        )


def _finite_bound(field_name: str, value: object, *, maximum: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be finite and in the range (0, {maximum}]")
    try:
        numeric_value = float(value)
    except (OverflowError, ValueError):
        raise ValueError(
            f"{field_name} must be finite and in the range (0, {maximum}]"
        ) from None
    if not math.isfinite(numeric_value) or not 0.0 < numeric_value <= maximum:
        raise ValueError(f"{field_name} must be finite and in the range (0, {maximum}]")


def _integer_bound(field_name: str, value: object, *, maximum: int) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        or value > maximum
    ):
        raise ValueError(f"{field_name} must be an integer in the range [1, {maximum}]")


DEFAULT_RUNTIME_CONFIG = RuntimeConfig()

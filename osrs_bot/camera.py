"""Pure bounded camera-control primitives.

This module deliberately contains no input transport.  It turns authoritative
pose/error evidence into a bounded hold recommendation that the existing task
and InputCoordinator may choose to execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import atan2, ceil, isfinite, pi
from statistics import median

from .model import CAMERA_YAW_UNITS, WorldPoint


CAMERA_DIRECTIONS = frozenset({"left", "right", "up", "down"})
YAW_DIRECTIONS = frozenset({"left", "right"})
PITCH_DIRECTIONS = frozenset({"up", "down"})


class CameraCorrectionPhase(str, Enum):
    """The two movement opportunities in one camera-acquisition episode."""

    COARSE = "coarse"
    FINE = "fine"


@dataclass(frozen=True, slots=True)
class CameraKeyCapabilities:
    """Camera-key limits supplied by the active device/protocol capability."""

    max_hold_millis: int = 250
    source: str = "arduino_hid.v1.current_protocol"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_hold_millis, int)
            or isinstance(self.max_hold_millis, bool)
            or self.max_hold_millis <= 0
        ):
            raise ValueError("max_hold_millis must be a positive integer")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty string")


@dataclass(frozen=True, slots=True)
class CameraResponseSample:
    """One acknowledged key hold and its verified pose response."""

    direction: str
    requested_hold_millis: int
    observed_yaw_delta: int = 0
    observed_pitch_delta: int = 0
    before_yaw: int | None = None
    after_yaw: int | None = None
    before_pitch: int | None = None
    after_pitch: int | None = None
    pose_limit: bool = False
    no_effect: bool = False
    overshoot: bool = False

    def __post_init__(self) -> None:
        if self.direction not in CAMERA_DIRECTIONS:
            raise ValueError("direction must be left, right, up, or down")
        if (
            not isinstance(self.requested_hold_millis, int)
            or isinstance(self.requested_hold_millis, bool)
            or self.requested_hold_millis <= 0
        ):
            raise ValueError("requested_hold_millis must be a positive integer")
        for field_name in ("observed_yaw_delta", "observed_pitch_delta"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{field_name} must be an integer")
        for field_name in ("before_yaw", "after_yaw"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value < CAMERA_YAW_UNITS
            ):
                raise ValueError(
                    f"{field_name} must be a fixed-point yaw or None"
                )
        for field_name in ("before_pitch", "after_pitch"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(f"{field_name} must be non-negative or None")
        for field_name in ("pose_limit", "no_effect", "overshoot"):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be a bool")

    @property
    def observed_axis_delta(self) -> int:
        if self.direction in YAW_DIRECTIONS:
            return self.observed_yaw_delta
        return self.observed_pitch_delta

    @property
    def moves_in_expected_direction(self) -> bool:
        delta = self.observed_axis_delta
        if self.direction in {"right", "up"}:
            return delta > 0
        return delta < 0

    @property
    def effective_no_effect(self) -> bool:
        return self.no_effect or self.observed_axis_delta == 0

    @property
    def rate_units_per_millis(self) -> float | None:
        if (
            self.pose_limit
            or self.effective_no_effect
            or not self.moves_in_expected_direction
        ):
            return None
        return abs(self.observed_axis_delta) / self.requested_hold_millis


@dataclass(frozen=True, slots=True)
class CameraResponseModel:
    """A bounded immutable collection of retained camera receipts."""

    samples: tuple[CameraResponseSample, ...] = ()
    max_samples: int = 32

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_samples, int)
            or isinstance(self.max_samples, bool)
            or self.max_samples <= 0
        ):
            raise ValueError("max_samples must be a positive integer")
        if not isinstance(self.samples, tuple) or any(
            not isinstance(sample, CameraResponseSample)
            for sample in self.samples
        ):
            raise TypeError("samples must be a tuple of CameraResponseSample")
        if len(self.samples) > self.max_samples:
            object.__setattr__(
                self,
                "samples",
                self.samples[-self.max_samples :],
            )

    def record(self, sample: CameraResponseSample) -> "CameraResponseModel":
        if not isinstance(sample, CameraResponseSample):
            raise TypeError("sample must be CameraResponseSample")
        retained = (*self.samples, sample)[-self.max_samples :]
        return CameraResponseModel(retained, self.max_samples)

    def median_rate(self, direction: str) -> float | None:
        _require_direction(direction)
        rates = tuple(
            rate
            for sample in self.samples
            if sample.direction == direction
            and (rate := sample.rate_units_per_millis) is not None
        )
        return None if not rates else float(median(rates))

    def pitch_direction_blocked(
        self,
        direction: str,
        current_pitch: int | None,
    ) -> bool:
        """Whether the latest matching hold proved an unchanged pitch limit."""

        if direction not in PITCH_DIRECTIONS:
            raise ValueError("pitch direction must be up or down")
        if current_pitch is None:
            return False
        if (
            not isinstance(current_pitch, int)
            or isinstance(current_pitch, bool)
            or current_pitch < 0
        ):
            raise ValueError("current_pitch must be non-negative or None")
        for sample in reversed(self.samples):
            if sample.direction != direction:
                continue
            result_pitch = (
                sample.after_pitch
                if sample.after_pitch is not None
                else sample.before_pitch
            )
            if result_pitch != current_pitch:
                return False
            return sample.pose_limit or sample.effective_no_effect
        return False


def normalize_yaw(yaw: int) -> int:
    if not isinstance(yaw, int) or isinstance(yaw, bool):
        raise ValueError("yaw must be an integer")
    return yaw % CAMERA_YAW_UNITS


def shortest_yaw_error(current_yaw: int, desired_yaw: int) -> int:
    """Return the signed shortest fixed-point arc from current to desired."""

    current = normalize_yaw(current_yaw)
    desired = normalize_yaw(desired_yaw)
    half_turn = CAMERA_YAW_UNITS // 2
    return (desired - current + half_turn) % CAMERA_YAW_UNITS - half_turn


def world_bearing_yaw(source: WorldPoint, target: WorldPoint) -> int | None:
    """Return the engine's fixed-point bearing from source to target."""

    if not isinstance(source, WorldPoint) or not isinstance(target, WorldPoint):
        raise TypeError("source and target must be WorldPoint")
    dx = target.x - source.x
    dy = target.y - source.y
    if dx == 0 and dy == 0:
        return None
    return round(
        (atan2(dx, -dy) % (2 * pi))
        * CAMERA_YAW_UNITS
        / (2 * pi)
    ) % CAMERA_YAW_UNITS


def desired_camera_yaw(source: WorldPoint, target: WorldPoint) -> int | None:
    bearing = world_bearing_yaw(source, target)
    if bearing is None:
        return None
    return (bearing + CAMERA_YAW_UNITS // 2) % CAMERA_YAW_UNITS


def yaw_error_to_world_target(
    source: WorldPoint,
    target: WorldPoint,
    current_yaw: int,
) -> int | None:
    desired = desired_camera_yaw(source, target)
    if desired is None:
        return None
    return shortest_yaw_error(current_yaw, desired)


def yaw_direction_for_error(
    error_units: int | None,
    *,
    deadband_units: int = 0,
) -> str | None:
    if (
        not isinstance(deadband_units, int)
        or isinstance(deadband_units, bool)
        or deadband_units < 0
    ):
        raise ValueError("deadband_units must be a non-negative integer")
    if error_units is not None and (
        not isinstance(error_units, int) or isinstance(error_units, bool)
    ):
        raise ValueError("error_units must be an integer or None")
    if error_units is None or abs(error_units) <= deadband_units:
        return None
    return "right" if error_units > 0 else "left"


def select_camera_hold_millis(
    remaining_error_units: int | float,
    direction: str,
    phase: CameraCorrectionPhase,
    capabilities: CameraKeyCapabilities,
    *,
    response_model: CameraResponseModel | None = None,
    minimum_hold_millis: int = 80,
    requested_max_millis: int | None = None,
    fallback_rate_units_per_millis: float = 4.5,
    deadband_units: int = 0,
) -> int:
    """Choose a deterministic hold, returning zero for an in-band error."""

    _require_direction(direction)
    if not isinstance(capabilities, CameraKeyCapabilities):
        raise TypeError("capabilities must be CameraKeyCapabilities")
    try:
        phase = CameraCorrectionPhase(phase)
    except (TypeError, ValueError) as exc:
        raise ValueError("phase must be coarse or fine") from exc
    if (
        isinstance(remaining_error_units, bool)
        or not isinstance(remaining_error_units, (int, float))
        or not isfinite(float(remaining_error_units))
    ):
        raise ValueError("remaining_error_units must be finite")
    if (
        not isinstance(minimum_hold_millis, int)
        or isinstance(minimum_hold_millis, bool)
        or minimum_hold_millis <= 0
    ):
        raise ValueError("minimum_hold_millis must be a positive integer")
    if (
        not isinstance(deadband_units, int)
        or isinstance(deadband_units, bool)
        or deadband_units < 0
    ):
        raise ValueError("deadband_units must be a non-negative integer")
    if (
        isinstance(fallback_rate_units_per_millis, bool)
        or not isinstance(fallback_rate_units_per_millis, (int, float))
        or not isfinite(float(fallback_rate_units_per_millis))
        or fallback_rate_units_per_millis <= 0
    ):
        raise ValueError("fallback rate must be finite and positive")
    if response_model is not None and not isinstance(
        response_model,
        CameraResponseModel,
    ):
        raise TypeError("response_model must be CameraResponseModel or None")

    capability_max = capabilities.max_hold_millis
    if requested_max_millis is not None:
        if (
            not isinstance(requested_max_millis, int)
            or isinstance(requested_max_millis, bool)
            or requested_max_millis <= 0
        ):
            raise ValueError("requested_max_millis must be positive or None")
        capability_max = min(capability_max, requested_max_millis)

    magnitude = abs(float(remaining_error_units))
    if magnitude <= deadband_units:
        return 0

    measured_rate = (
        response_model.median_rate(direction)
        if response_model is not None
        else None
    )
    rate = measured_rate or float(fallback_rate_units_per_millis)
    correction_fraction = (
        0.85 if phase is CameraCorrectionPhase.COARSE else 0.55
    )
    estimated = ceil((magnitude / rate) * correction_fraction)
    lower_bound = min(minimum_hold_millis, capability_max)
    return max(lower_bound, min(estimated, capability_max))


def proves_yaw_overshoot(
    before_error_units: int,
    after_error_units: int,
    *,
    pose_result_fresh: bool,
    geometry_changed: bool,
    deadband_units: int = 0,
) -> bool:
    """Require fresh changed-geometry evidence of an out-of-band sign cross."""

    for field_name, value in (
        ("before_error_units", before_error_units),
        ("after_error_units", after_error_units),
        ("deadband_units", deadband_units),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{field_name} must be an integer")
    if deadband_units < 0:
        raise ValueError("deadband_units must be non-negative")
    if not isinstance(pose_result_fresh, bool) or not isinstance(
        geometry_changed,
        bool,
    ):
        raise ValueError("freshness flags must be bools")
    if not pose_result_fresh or not geometry_changed:
        return False
    if (
        abs(before_error_units) <= deadband_units
        or abs(after_error_units) <= deadband_units
    ):
        return False
    return (before_error_units > 0) != (after_error_units > 0)


def yaw_reversal_allowed(
    previous_direction: str | None,
    proposed_direction: str,
    *,
    overshoot_proved: bool,
) -> bool:
    """Forbid a left/right reversal until a fresh overshoot was proven."""

    if previous_direction is not None and previous_direction not in YAW_DIRECTIONS:
        raise ValueError("previous_direction must be left, right, or None")
    if proposed_direction not in YAW_DIRECTIONS:
        raise ValueError("proposed_direction must be left or right")
    if not isinstance(overshoot_proved, bool):
        raise ValueError("overshoot_proved must be a bool")
    return (
        previous_direction is None
        or previous_direction == proposed_direction
        or overshoot_proved
    )


def _require_direction(direction: str) -> None:
    if direction not in CAMERA_DIRECTIONS:
        raise ValueError("direction must be left, right, up, or down")

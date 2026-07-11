from __future__ import annotations

from dataclasses import dataclass
import math

from .model import ScreenBounds, ScreenPoint


FIRMWARE_MAX_DELTA_PX = 20


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a finite positive number")
    try:
        normalized = float(value)
    except OverflowError as error:
        raise ValueError(f"{field_name} must be a finite positive number") from error
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field_name} must be a finite positive number")
    return normalized


def _validate_point(point: object, field_name: str) -> ScreenPoint:
    if not isinstance(point, ScreenPoint):
        raise TypeError(f"{field_name} must be a ScreenPoint")
    if not _is_int(point.x) or not _is_int(point.y):
        raise TypeError(f"{field_name} coordinates must be integers")
    return point


def _validate_bounds(bounds: object) -> ScreenBounds:
    if not isinstance(bounds, ScreenBounds):
        raise TypeError("bounds must be ScreenBounds")
    if not all(
        _is_int(value)
        for value in (bounds.x, bounds.y, bounds.width, bounds.height)
    ):
        raise TypeError("bounds coordinates and dimensions must be integers")
    if bounds.width <= 0 or bounds.height <= 0:
        raise ValueError("bounds dimensions must be positive")
    return bounds


@dataclass(frozen=True, slots=True)
class PointerMotionLimits:
    """Finite Chebyshev velocity/acceleration caps for pointer motion.

    Chebyshev units match the firmware's independent per-axis MOVE limits. The
    planner may use less than these caps when it needs room to brake exactly at
    the requested target.
    """

    max_velocity_px_per_second: float = 1_000.0
    max_acceleration_px_per_second_squared: float = 20_000.0
    max_step_delta_px: int = FIRMWARE_MAX_DELTA_PX

    def __post_init__(self) -> None:
        velocity = _positive_finite(
            self.max_velocity_px_per_second,
            "max_velocity_px_per_second",
        )
        acceleration = _positive_finite(
            self.max_acceleration_px_per_second_squared,
            "max_acceleration_px_per_second_squared",
        )
        if not _is_int(self.max_step_delta_px):
            raise TypeError("max_step_delta_px must be an integer")
        if not 1 <= self.max_step_delta_px <= FIRMWARE_MAX_DELTA_PX:
            raise ValueError(
                f"max_step_delta_px must be between 1 and {FIRMWARE_MAX_DELTA_PX}"
            )
        object.__setattr__(self, "max_velocity_px_per_second", velocity)
        object.__setattr__(
            self,
            "max_acceleration_px_per_second_squared",
            acceleration,
        )


DEFAULT_POINTER_MOTION_LIMITS = PointerMotionLimits()


@dataclass(frozen=True, slots=True)
class PointerDelta:
    """One relative firmware MOVE delta."""

    dx: int
    dy: int

    def __post_init__(self) -> None:
        if not _is_int(self.dx) or not _is_int(self.dy):
            raise TypeError("pointer deltas must be integers")
        if self.dx == 0 and self.dy == 0:
            raise ValueError("pointer delta must move at least one axis")
        if abs(self.dx) > FIRMWARE_MAX_DELTA_PX or abs(self.dy) > FIRMWARE_MAX_DELTA_PX:
            raise ValueError(
                f"pointer delta exceeds firmware limit of {FIRMWARE_MAX_DELTA_PX} pixels per axis"
            )


def _discrete_caps(
    timestep_seconds: float,
    limits: PointerMotionLimits,
) -> tuple[int, int]:
    velocity_distance = limits.max_velocity_px_per_second * timestep_seconds
    acceleration_distance = (
        limits.max_acceleration_px_per_second_squared
        * timestep_seconds
        * timestep_seconds
    )
    if not math.isfinite(velocity_distance) or not math.isfinite(acceleration_distance):
        raise ValueError("timestep and motion caps must have a finite product")

    # The small tolerance prevents an exactly integral physical cap from being
    # rounded down because of binary floating-point representation.
    velocity_cap = min(
        limits.max_step_delta_px,
        math.floor(velocity_distance + 1e-12),
    )
    acceleration_cap = math.floor(acceleration_distance + 1e-12)
    if velocity_cap < 1:
        raise ValueError(
            "timestep and velocity cap cannot express a one-pixel relative step"
        )
    if acceleration_cap < 1:
        raise ValueError(
            "timestep and acceleration cap cannot express a one-pixel velocity change"
        )
    return velocity_cap, acceleration_cap


def _minimum_stopping_distance(speed: int, acceleration_cap: int) -> int:
    """Return distance required after this tick to brake to rest."""

    distance = 0
    next_speed = speed
    while next_speed > acceleration_cap:
        next_speed -= acceleration_cap
        distance += next_speed
    return distance


def _axis_schedule(
    distance: int,
    velocity_cap: int,
    acceleration_cap: int,
) -> tuple[int, ...]:
    if distance == 0:
        return ()

    remaining = distance
    previous_speed = 0
    schedule: list[int] = []
    while remaining:
        minimum_speed = max(1, previous_speed - acceleration_cap)
        maximum_speed = min(
            velocity_cap,
            previous_speed + acceleration_cap,
            remaining,
        )

        selected_speed: int | None = None
        for speed in range(maximum_speed, minimum_speed - 1, -1):
            after_step = remaining - speed
            if after_step == 0:
                if speed <= acceleration_cap:
                    selected_speed = speed
                    break
                continue
            if after_step >= _minimum_stopping_distance(speed, acceleration_cap):
                selected_speed = speed
                break

        if selected_speed is None:  # pragma: no cover - guarded by integer caps
            raise RuntimeError("no bounded pointer schedule exists")
        schedule.append(selected_speed)
        remaining -= selected_speed
        previous_speed = selected_speed

    return tuple(schedule)


def _centered(schedule: tuple[int, ...], length: int) -> tuple[int, ...]:
    before = (length - len(schedule)) // 2
    after = length - len(schedule) - before
    return (0,) * before + schedule + (0,) * after


@dataclass(frozen=True, slots=True)
class PointerMotionPlan:
    """An immutable, transport-free sequence of bounded relative movements."""

    start: ScreenPoint
    target: ScreenPoint
    bounds: ScreenBounds
    timestep_seconds: float
    limits: PointerMotionLimits
    steps: tuple[PointerDelta, ...]

    def __post_init__(self) -> None:
        start = _validate_point(self.start, "start")
        target = _validate_point(self.target, "target")
        bounds = _validate_bounds(self.bounds)
        timestep = _positive_finite(self.timestep_seconds, "timestep_seconds")
        if not isinstance(self.limits, PointerMotionLimits):
            raise TypeError("limits must be PointerMotionLimits")
        if not isinstance(self.steps, tuple) or not all(
            isinstance(step, PointerDelta) for step in self.steps
        ):
            raise TypeError("steps must be a tuple of PointerDelta values")
        if not bounds.contains(start):
            raise ValueError("start must be inside verified bounds")
        if not bounds.contains(target):
            raise ValueError("target must be inside verified bounds")

        if start == target and self.steps:
            raise ValueError("a stationary plan must not contain movement steps")
        if start != target and not self.steps:
            raise ValueError("a moving plan must contain movement steps")

        if not self.steps:
            object.__setattr__(self, "timestep_seconds", timestep)
            return

        velocity_cap, acceleration_cap = _discrete_caps(timestep, self.limits)

        x = start.x
        y = start.y
        previous_dx = 0
        previous_dy = 0
        for step in self.steps:
            if abs(step.dx) > velocity_cap or abs(step.dy) > velocity_cap:
                raise ValueError("pointer step exceeds the configured velocity cap")
            if (
                abs(step.dx - previous_dx) > acceleration_cap
                or abs(step.dy - previous_dy) > acceleration_cap
            ):
                raise ValueError("pointer step exceeds the configured acceleration cap")
            if (
                abs(step.dx) > self.limits.max_step_delta_px
                or abs(step.dy) > self.limits.max_step_delta_px
            ):
                raise ValueError("pointer step exceeds the configured relative-step cap")
            x += step.dx
            y += step.dy
            if not bounds.contains(ScreenPoint(x, y)):
                raise ValueError("pointer plan leaves verified bounds")
            previous_dx = step.dx
            previous_dy = step.dy

        if self.steps and (
            abs(previous_dx) > acceleration_cap
            or abs(previous_dy) > acceleration_cap
        ):
            raise ValueError("pointer plan cannot decelerate to rest within its cap")
        if ScreenPoint(x, y) != target:
            raise ValueError("pointer plan does not arrive exactly at target")
        object.__setattr__(self, "timestep_seconds", timestep)

    @property
    def positions(self) -> tuple[ScreenPoint, ...]:
        x = self.start.x
        y = self.start.y
        positions: list[ScreenPoint] = []
        for step in self.steps:
            x += step.dx
            y += step.dy
            positions.append(ScreenPoint(x, y))
        return tuple(positions)

    @property
    def duration_seconds(self) -> float:
        return len(self.steps) * self.timestep_seconds

    @property
    def peak_velocity_px_per_second(self) -> float:
        return max(
            (max(abs(step.dx), abs(step.dy)) for step in self.steps),
            default=0,
        ) / self.timestep_seconds

    @property
    def peak_acceleration_px_per_second_squared(self) -> float:
        previous_dx = 0
        previous_dy = 0
        peak_delta = 0
        for step in self.steps:
            peak_delta = max(
                peak_delta,
                abs(step.dx - previous_dx),
                abs(step.dy - previous_dy),
            )
            previous_dx = step.dx
            previous_dy = step.dy
        peak_delta = max(peak_delta, abs(previous_dx), abs(previous_dy))
        return peak_delta / (self.timestep_seconds * self.timestep_seconds)


def plan_pointer_motion(
    start: ScreenPoint,
    target: ScreenPoint,
    bounds: ScreenBounds,
    *,
    timestep_seconds: float,
    limits: PointerMotionLimits = DEFAULT_POINTER_MOTION_LIMITS,
) -> PointerMotionPlan:
    """Plan deterministic monotone motion inside caller-verified bounds.

    The result contains relative deltas only; it neither imports nor invokes a
    serial or operating-system input transport.
    """

    start = _validate_point(start, "start")
    target = _validate_point(target, "target")
    bounds = _validate_bounds(bounds)
    timestep = _positive_finite(timestep_seconds, "timestep_seconds")
    if not isinstance(limits, PointerMotionLimits):
        raise TypeError("limits must be PointerMotionLimits")
    if not bounds.contains(start):
        raise ValueError("start must be inside verified bounds")
    if not bounds.contains(target):
        raise ValueError("target must be inside verified bounds")

    delta_x = target.x - start.x
    delta_y = target.y - start.y
    if delta_x == 0 and delta_y == 0:
        return PointerMotionPlan(
            start=start,
            target=target,
            bounds=bounds,
            timestep_seconds=timestep,
            limits=limits,
            steps=(),
        )

    velocity_cap, acceleration_cap = _discrete_caps(timestep, limits)
    x_schedule = _axis_schedule(abs(delta_x), velocity_cap, acceleration_cap)
    y_schedule = _axis_schedule(abs(delta_y), velocity_cap, acceleration_cap)
    step_count = max(len(x_schedule), len(y_schedule))
    if step_count == 0:
        steps: tuple[PointerDelta, ...] = ()
    else:
        x_schedule = _centered(x_schedule, step_count)
        y_schedule = _centered(y_schedule, step_count)
        x_sign = 1 if delta_x >= 0 else -1
        y_sign = 1 if delta_y >= 0 else -1
        steps = tuple(
            PointerDelta(x_sign * dx, y_sign * dy)
            for dx, dy in zip(x_schedule, y_schedule, strict=True)
        )

    return PointerMotionPlan(
        start=start,
        target=target,
        bounds=bounds,
        timestep_seconds=timestep,
        limits=limits,
        steps=steps,
    )

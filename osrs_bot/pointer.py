from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math

from .model import ScreenBounds, ScreenPoint


FIRMWARE_MAX_DELTA_PX = 20
GAMEPLAY_POINTER_SAFE_INSET_DEVICE_PX = 16


def gameplay_pointer_safe_bounds(viewport: ScreenBounds) -> ScreenBounds:
    """Return the fixed device-pixel inset used by ordinary gameplay motion.

    ``viewport`` is already expressed in the authoritative Win32 device-pixel
    coordinate space.  The inset is therefore applied exactly once and is not
    rescaled for display DPI.  A viewport too small to retain the full padding
    is unusable rather than silently weakening the containment contract.
    """

    viewport = _validate_bounds(viewport)
    inset = GAMEPLAY_POINTER_SAFE_INSET_DEVICE_PX
    if viewport.width <= inset * 2 or viewport.height <= inset * 2:
        raise ValueError("gameplay viewport is too small for the pointer-safe inset")
    return ScreenBounds(
        viewport.x + inset,
        viewport.y + inset,
        viewport.width - inset * 2,
        viewport.height - inset * 2,
    )


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
class PointerTrajectoryConfig:
    """Engine-owned bounds for controlled pointer trajectory variation.

    Variation is derived from explicit seed material with a local hash stream;
    it never reads or mutates process-global random state.  The unchanged
    :class:`PointerMotionPlan` validation remains the final authority for every
    generated path.
    """

    minimum_curve_distance_px: int = 12
    minimum_curvature_fraction: float = 0.035
    maximum_curvature_fraction: float = 0.14
    maximum_curve_offset_px: float = 72.0
    minimum_speed_scale: float = 0.58
    maximum_speed_scale: float = 0.92
    speed_variation_fraction: float = 0.12
    precise_target_threshold_px: int = 10
    broad_target_threshold_px: int = 36

    def __post_init__(self) -> None:
        if not _is_int(self.minimum_curve_distance_px):
            raise TypeError("minimum_curve_distance_px must be an integer")
        if self.minimum_curve_distance_px < 1:
            raise ValueError("minimum_curve_distance_px must be positive")
        for field_name in (
            "minimum_curvature_fraction",
            "maximum_curvature_fraction",
            "maximum_curve_offset_px",
            "minimum_speed_scale",
            "maximum_speed_scale",
        ):
            value = _positive_finite(getattr(self, field_name), field_name)
            object.__setattr__(self, field_name, value)
        variation = self.speed_variation_fraction
        if isinstance(variation, bool) or not isinstance(variation, (int, float)):
            raise TypeError("speed_variation_fraction must be a finite number")
        variation = float(variation)
        if not math.isfinite(variation) or not 0.0 <= variation <= 0.5:
            raise ValueError(
                "speed_variation_fraction must be between zero and 0.5"
            )
        object.__setattr__(self, "speed_variation_fraction", variation)
        if self.minimum_curvature_fraction > self.maximum_curvature_fraction:
            raise ValueError(
                "minimum_curvature_fraction must not exceed maximum_curvature_fraction"
            )
        if not 0.0 < self.minimum_speed_scale <= self.maximum_speed_scale <= 1.0:
            raise ValueError("speed scales must satisfy 0 < minimum <= maximum <= 1")
        for field_name in (
            "precise_target_threshold_px",
            "broad_target_threshold_px",
        ):
            value = getattr(self, field_name)
            if not _is_int(value):
                raise TypeError(f"{field_name} must be an integer")
            if value < 1:
                raise ValueError(f"{field_name} must be positive")
        if self.precise_target_threshold_px >= self.broad_target_threshold_px:
            raise ValueError(
                "precise_target_threshold_px must be below broad_target_threshold_px"
            )


DEFAULT_POINTER_TRAJECTORY_CONFIG = PointerTrajectoryConfig()


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
    trajectory_seed: str | None = None
    decision_id: str | None = None
    context: str = "default"
    path_style: str = "linear"
    control_points: tuple[ScreenPoint, ...] = ()
    target_bounds: ScreenBounds | None = None

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
        for field_name in ("trajectory_seed", "decision_id"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 128
            ):
                raise ValueError(f"{field_name} must be bounded non-empty text or None")
        if (
            not isinstance(self.context, str)
            or not self.context.strip()
            or len(self.context) > 64
        ):
            raise ValueError("context must be bounded non-empty text")
        if self.path_style not in {
            "stationary",
            "linear",
            "seeded_linear",
            "cubic_bezier",
        }:
            raise ValueError("path_style is unsupported")
        if not isinstance(self.control_points, tuple) or any(
            not isinstance(point, ScreenPoint) for point in self.control_points
        ):
            raise TypeError("control_points must be a tuple of ScreenPoint values")
        if len(self.control_points) > 2:
            raise ValueError("at most two control_points are allowed")
        if self.path_style == "cubic_bezier" and len(self.control_points) != 2:
            raise ValueError("cubic_bezier paths require exactly two control points")
        if self.path_style != "cubic_bezier" and self.control_points:
            raise ValueError("only cubic_bezier paths may carry control points")
        if any(not bounds.contains(point) for point in self.control_points):
            raise ValueError("control points must stay inside verified bounds")
        if self.target_bounds is not None:
            target_bounds = _validate_bounds(self.target_bounds)
            if not _bounds_contains_bounds(bounds, target_bounds):
                raise ValueError("target_bounds must stay inside verified bounds")
            if not target_bounds.contains(target):
                raise ValueError("target must be inside target_bounds")
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

    @property
    def movement_distance_px(self) -> float:
        return math.hypot(self.target.x - self.start.x, self.target.y - self.start.y)

    @property
    def path_length_px(self) -> float:
        previous = self.start
        total = 0.0
        for position in self.positions:
            total += math.hypot(position.x - previous.x, position.y - previous.y)
            previous = position
        return total

    @property
    def approach_angle_degrees(self) -> float | None:
        if not self.steps:
            return None
        final = self.steps[-1]
        return math.degrees(math.atan2(final.dy, final.dx))

    @property
    def summary(self) -> dict[str, object]:
        """Return a bounded transport-free diagnostic summary."""

        return {
            "style": self.path_style,
            "seed": self.trajectory_seed,
            "decisionId": self.decision_id,
            "context": self.context,
            "stepCount": len(self.steps),
            "durationSeconds": self.duration_seconds,
            "movementDistancePx": self.movement_distance_px,
            "pathLengthPx": self.path_length_px,
            "approachAngleDegrees": self.approach_angle_degrees,
            "controlPoints": tuple(
                (point.x, point.y) for point in self.control_points
            ),
        }


def _bounds_contains_bounds(outer: ScreenBounds, inner: ScreenBounds) -> bool:
    return bool(
        outer.contains(ScreenPoint(inner.x, inner.y))
        and outer.contains(
            ScreenPoint(
                inner.x + inner.width - 1,
                inner.y + inner.height - 1,
            )
        )
    )


def _bounded_text(value: object, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be bounded non-empty text")
    return value.strip()


def _normalized_seed(seed: int | str | None) -> str | None:
    if seed is None:
        return None
    if isinstance(seed, bool) or not isinstance(seed, (int, str)):
        raise TypeError("seed must be an integer, string, or None")
    text = str(seed).strip()
    if not text or len(text) > 128:
        raise ValueError("seed must have a bounded non-empty representation")
    return text


def _seed_unit(material: str, label: str) -> float:
    digest = hashlib.blake2b(
        f"{material}|{label}".encode("utf-8"),
        digest_size=8,
        person=b"osrs-ptr",
    ).digest()
    return int.from_bytes(digest, "big") / float((1 << 64) - 1)


def _linear_steps(
    delta_x: int,
    delta_y: int,
    velocity_cap: int,
    acceleration_cap: int,
) -> tuple[PointerDelta, ...]:
    x_schedule = _axis_schedule(abs(delta_x), velocity_cap, acceleration_cap)
    y_schedule = _axis_schedule(abs(delta_y), velocity_cap, acceleration_cap)
    step_count = max(len(x_schedule), len(y_schedule))
    if step_count == 0:
        return ()
    x_schedule = _centered(x_schedule, step_count)
    y_schedule = _centered(y_schedule, step_count)
    x_sign = 1 if delta_x >= 0 else -1
    y_sign = 1 if delta_y >= 0 else -1
    return tuple(
        PointerDelta(x_sign * dx, y_sign * dy)
        for dx, dy in zip(x_schedule, y_schedule, strict=True)
    )


def _target_size_px(target_bounds: ScreenBounds | None) -> int:
    return (
        1
        if target_bounds is None
        else max(1, min(target_bounds.width, target_bounds.height))
    )


def _scaled_caps(
    velocity_cap: int,
    acceleration_cap: int,
    distance: float,
    target_size: int,
    config: PointerTrajectoryConfig,
    material: str,
) -> tuple[int, int]:
    target_span = (
        config.broad_target_threshold_px - config.precise_target_threshold_px
    )
    size_factor = min(
        1.0,
        max(
            0.0,
            (target_size - config.precise_target_threshold_px) / target_span,
        ),
    )
    distance_factor = min(1.0, distance / 400.0)
    scale_position = min(
        1.0,
        0.18 + 0.52 * distance_factor + 0.30 * size_factor,
    )
    speed_scale = config.minimum_speed_scale + (
        config.maximum_speed_scale - config.minimum_speed_scale
    ) * scale_position
    speed_scale *= 1.0 + (
        _seed_unit(material, "speed") - 0.5
    ) * config.speed_variation_fraction
    speed_scale = min(
        config.maximum_speed_scale,
        max(config.minimum_speed_scale, speed_scale),
    )
    # Leave deliberate headroom for perpendicular curve velocity and
    # acceleration; PointerMotionPlan still verifies against the full caps.
    return (
        max(1, min(velocity_cap, math.floor(velocity_cap * speed_scale))),
        max(
            1,
            min(
                acceleration_cap,
                math.floor(acceleration_cap * min(speed_scale, 0.72)),
            ),
        ),
    )


def _bezier_coordinate(
    start: float,
    control_one: float,
    control_two: float,
    target: float,
    progress: float,
) -> float:
    inverse = 1.0 - progress
    return (
        inverse * inverse * inverse * start
        + 3.0 * inverse * inverse * progress * control_one
        + 3.0 * inverse * progress * progress * control_two
        + progress * progress * progress * target
    )


def _curved_steps(
    start: ScreenPoint,
    target: ScreenPoint,
    *,
    progress_schedule: tuple[int, ...],
    dominant_distance: int,
    control_one: tuple[float, float],
    control_two: tuple[float, float],
) -> tuple[PointerDelta, ...]:
    positions: list[ScreenPoint] = []
    travelled = 0
    for progress_step in progress_schedule:
        travelled += progress_step
        progress = travelled / dominant_distance
        position = ScreenPoint(
            round(
                _bezier_coordinate(
                    start.x,
                    control_one[0],
                    control_two[0],
                    target.x,
                    progress,
                )
            ),
            round(
                _bezier_coordinate(
                    start.y,
                    control_one[1],
                    control_two[1],
                    target.y,
                    progress,
                )
            ),
        )
        if not positions or position != positions[-1]:
            positions.append(position)
    if not positions or positions[-1] != target:
        positions.append(target)

    previous = start
    steps: list[PointerDelta] = []
    for position in positions:
        if position != previous:
            steps.append(PointerDelta(position.x - previous.x, position.y - previous.y))
            previous = position
    return tuple(steps)


def plan_pointer_motion(
    start: ScreenPoint,
    target: ScreenPoint,
    bounds: ScreenBounds,
    *,
    timestep_seconds: float,
    limits: PointerMotionLimits = DEFAULT_POINTER_MOTION_LIMITS,
    seed: int | str | None = None,
    decision_id: str | None = None,
    target_bounds: ScreenBounds | None = None,
    context: str = "default",
    trajectory_config: PointerTrajectoryConfig = DEFAULT_POINTER_TRAJECTORY_CONFIG,
) -> PointerMotionPlan:
    """Plan exact bounded motion inside caller-verified bounds.

    Calls without seed material retain the original deterministic monotone
    policy. Supplying a seed or decision id enables controlled cubic variation
    that is reproducible from those values and remains subject to the same
    immutable safety validation. The result contains relative deltas only; it
    neither imports nor invokes a serial or operating-system input transport.
    """

    start = _validate_point(start, "start")
    target = _validate_point(target, "target")
    bounds = _validate_bounds(bounds)
    timestep = _positive_finite(timestep_seconds, "timestep_seconds")
    if not isinstance(limits, PointerMotionLimits):
        raise TypeError("limits must be PointerMotionLimits")
    if not isinstance(trajectory_config, PointerTrajectoryConfig):
        raise TypeError("trajectory_config must be PointerTrajectoryConfig")
    if not bounds.contains(start):
        raise ValueError("start must be inside verified bounds")
    if not bounds.contains(target):
        raise ValueError("target must be inside verified bounds")
    normalized_seed = _normalized_seed(seed)
    normalized_decision = (
        None
        if decision_id is None
        else _bounded_text(decision_id, "decision_id", 128)
    )
    normalized_context = _bounded_text(context, "context", 64)
    if target_bounds is not None:
        target_bounds = _validate_bounds(target_bounds)
        if not _bounds_contains_bounds(bounds, target_bounds):
            raise ValueError("target_bounds must stay inside verified bounds")
        if not target_bounds.contains(target):
            raise ValueError("target must be inside target_bounds")

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
            trajectory_seed=normalized_seed,
            decision_id=normalized_decision,
            context=normalized_context,
            path_style="stationary",
            target_bounds=target_bounds,
        )

    velocity_cap, acceleration_cap = _discrete_caps(timestep, limits)
    variation_requested = normalized_seed is not None or normalized_decision is not None
    if not variation_requested:
        return PointerMotionPlan(
            start=start,
            target=target,
            bounds=bounds,
            timestep_seconds=timestep,
            limits=limits,
            steps=_linear_steps(
                delta_x,
                delta_y,
                velocity_cap,
                acceleration_cap,
            ),
            context=normalized_context,
            target_bounds=target_bounds,
        )

    recorded_seed = normalized_seed if normalized_seed is not None else "0"
    material = "|".join(
        (
            recorded_seed,
            normalized_decision or "",
            normalized_context,
            f"{start.x},{start.y}",
            f"{target.x},{target.y}",
            (
                "none"
                if target_bounds is None
                else (
                    f"{target_bounds.x},{target_bounds.y},"
                    f"{target_bounds.width},{target_bounds.height}"
                )
            ),
        )
    )
    distance = math.hypot(delta_x, delta_y)
    effective_velocity, effective_acceleration = _scaled_caps(
        velocity_cap,
        acceleration_cap,
        distance,
        _target_size_px(target_bounds),
        trajectory_config,
        material,
    )
    seeded_linear = _linear_steps(
        delta_x,
        delta_y,
        effective_velocity,
        effective_acceleration,
    )
    fallback = PointerMotionPlan(
        start=start,
        target=target,
        bounds=bounds,
        timestep_seconds=timestep,
        limits=limits,
        steps=seeded_linear,
        trajectory_seed=recorded_seed,
        decision_id=normalized_decision,
        context=normalized_context,
        path_style="seeded_linear",
        target_bounds=target_bounds,
    )
    if distance < trajectory_config.minimum_curve_distance_px:
        return fallback

    dominant_distance = max(abs(delta_x), abs(delta_y))
    progress_schedule = _axis_schedule(
        dominant_distance,
        effective_velocity,
        effective_acceleration,
    )
    direction_length = max(distance, 1.0)
    normal_x = -delta_y / direction_length
    normal_y = delta_x / direction_length
    curvature_fraction = trajectory_config.minimum_curvature_fraction + (
        trajectory_config.maximum_curvature_fraction
        - trajectory_config.minimum_curvature_fraction
    ) * _seed_unit(material, "curvature")
    curve_offset = min(
        trajectory_config.maximum_curve_offset_px,
        distance * curvature_fraction,
    )
    curve_sign = -1.0 if _seed_unit(material, "side") < 0.5 else 1.0
    first_fraction = 0.24 + 0.14 * _seed_unit(material, "control-one")
    second_fraction = 0.62 + 0.14 * _seed_unit(material, "control-two")
    first_weight = 0.72 + 0.28 * _seed_unit(material, "offset-one")
    second_weight = 0.40 + 0.48 * _seed_unit(material, "offset-two")

    for reduction in (1.0, 0.75, 0.5, 0.25):
        offset = curve_sign * curve_offset * reduction
        control_one_raw = (
            start.x + delta_x * first_fraction + normal_x * offset * first_weight,
            start.y + delta_y * first_fraction + normal_y * offset * first_weight,
        )
        control_two_raw = (
            start.x + delta_x * second_fraction + normal_x * offset * second_weight,
            start.y + delta_y * second_fraction + normal_y * offset * second_weight,
        )
        control_points = (
            ScreenPoint(round(control_one_raw[0]), round(control_one_raw[1])),
            ScreenPoint(round(control_two_raw[0]), round(control_two_raw[1])),
        )
        if any(not bounds.contains(point) for point in control_points):
            continue
        try:
            return PointerMotionPlan(
                start=start,
                target=target,
                bounds=bounds,
                timestep_seconds=timestep,
                limits=limits,
                steps=_curved_steps(
                    start,
                    target,
                    progress_schedule=progress_schedule,
                    dominant_distance=dominant_distance,
                    control_one=control_one_raw,
                    control_two=control_two_raw,
                ),
                trajectory_seed=recorded_seed,
                decision_id=normalized_decision,
                context=normalized_context,
                path_style="cubic_bezier",
                control_points=control_points,
                target_bounds=target_bounds,
            )
        except ValueError:
            # A rounded curve can violate one discrete cap even when the
            # continuous shape is safe. Reduce curvature; never relax caps.
            continue
    return fallback

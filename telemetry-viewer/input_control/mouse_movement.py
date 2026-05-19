from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any


SCHEMA = "mouse_movement_plan.v1"
SUPPORTED_PROFILES = {"instant_test", "linear_debug", "smooth_bezier", "fitts_guided", "wind_mouse"}


@dataclass(frozen=True)
class MousePoint:
    x: int
    y: int
    timestamp_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"x": self.x, "y": self.y}
        if self.timestamp_ms is not None:
            payload["timestampMs"] = self.timestamp_ms
        return payload


@dataclass(frozen=True)
class MouseTarget:
    x: int
    y: int
    radius_px: int = 4
    width_px: int | None = None
    height_px: int | None = None
    label: str = "target"
    source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "radiusPx": self.radius_px,
            "widthPx": self.width_px,
            "heightPx": self.height_px,
            "label": self.label,
            "source": self.source,
        }


@dataclass(frozen=True)
class MouseMovementProfile:
    name: str = "linear_debug"
    min_duration_ms: int = 80
    max_duration_ms: int = 900
    base_speed_px_per_sec: int = 900
    curve_type: str = "linear"
    waypoint_count: int = 24
    overshoot_allowed: bool = False
    correction_step_allowed: bool = True
    endpoint_jitter_px: int = 0
    path_jitter_px: int = 0
    sample_interval_ms: int = 10
    seed: int | None = None
    gravity: float = 9.0
    wind: float = 3.0
    max_velocity: float = 15.0
    wind_damping_distance: float = 12.0
    max_iterations: int = 500


@dataclass
class MouseMovementPlan:
    schema: str
    profile_name: str
    start: MousePoint
    target: MouseTarget
    duration_ms: int
    points: list[MousePoint]
    click_point: MousePoint
    path_length_px: float
    estimated_difficulty: float
    warnings: list[str] = field(default_factory=list)
    validation_status: str = "PASS"

    def to_dict(self, *, include_points: bool = True) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "profileName": self.profile_name,
            "start": self.start.to_dict(),
            "target": self.target.to_dict(),
            "durationMs": self.duration_ms,
            "points": [point.to_dict() for point in self.points] if include_points else [],
            "pointCount": len(self.points),
            "clickPoint": self.click_point.to_dict(),
            "pathLengthPx": round(self.path_length_px, 3),
            "estimatedDifficulty": round(self.estimated_difficulty, 3),
            "warnings": list(self.warnings),
            "validationStatus": self.validation_status,
        }


def resolve_profile(profile: str | MouseMovementProfile | None, *, seed: int | None = None) -> MouseMovementProfile:
    if isinstance(profile, MouseMovementProfile):
        return profile if seed is None else MouseMovementProfile(**{**profile.__dict__, "seed": seed})
    name = str(profile or "linear_debug")
    if name not in SUPPORTED_PROFILES:
        raise ValueError(f"unsupported movement profile: {name}")
    defaults: dict[str, Any] = {"name": name, "seed": seed}
    if name == "instant_test":
        defaults.update(min_duration_ms=0, max_duration_ms=0, base_speed_px_per_sec=100000, waypoint_count=1, sample_interval_ms=1)
    elif name == "linear_debug":
        defaults.update(min_duration_ms=80, max_duration_ms=300, base_speed_px_per_sec=1200, waypoint_count=16, sample_interval_ms=10)
    elif name == "smooth_bezier":
        defaults.update(min_duration_ms=140, max_duration_ms=700, base_speed_px_per_sec=850, waypoint_count=28, path_jitter_px=3, endpoint_jitter_px=1, curve_type="bezier")
    elif name == "fitts_guided":
        defaults.update(min_duration_ms=120, max_duration_ms=900, base_speed_px_per_sec=700, waypoint_count=30, path_jitter_px=2, endpoint_jitter_px=1, curve_type="fitts")
    elif name == "wind_mouse":
        defaults.update(min_duration_ms=120, max_duration_ms=1100, base_speed_px_per_sec=850, endpoint_jitter_px=1, overshoot_allowed=True, curve_type="wind")
    return MouseMovementProfile(**defaults)


def distance(start: MousePoint, target: MouseTarget) -> float:
    return math.hypot(float(target.x - start.x), float(target.y - start.y))


def estimate_difficulty(distance_px: float, width_px: float) -> float:
    width = max(1.0, float(width_px))
    return math.log2(max(0.0, float(distance_px)) / width + 1.0)


def estimate_fitts_duration_ms(
    distance_px: float,
    target_width_px: float,
    *,
    min_duration_ms: int,
    max_duration_ms: int,
    intercept_ms: float = 80.0,
    slope_ms: float = 95.0,
) -> int:
    duration = intercept_ms + slope_ms * estimate_difficulty(distance_px, target_width_px)
    return int(max(min_duration_ms, min(max_duration_ms, round(duration))))


def _click_point(target: MouseTarget, profile: MouseMovementProfile, rng: random.Random) -> MousePoint:
    jitter = max(0, int(profile.endpoint_jitter_px))
    if jitter <= 0:
        return MousePoint(target.x, target.y)
    for _ in range(20):
        x = int(round(target.x + rng.uniform(-jitter, jitter)))
        y = int(round(target.y + rng.uniform(-jitter, jitter)))
        if math.hypot(x - target.x, y - target.y) <= max(1, target.radius_px):
            return MousePoint(x, y)
    return MousePoint(target.x, target.y)


def _duration(start: MousePoint, target: MouseTarget, profile: MouseMovementProfile) -> tuple[int, float]:
    dist = distance(start, target)
    width = target.width_px or max(1, target.radius_px * 2)
    difficulty = estimate_difficulty(dist, width)
    if profile.name in {"fitts_guided", "smooth_bezier", "wind_mouse"}:
        return estimate_fitts_duration_ms(dist, width, min_duration_ms=profile.min_duration_ms, max_duration_ms=profile.max_duration_ms), difficulty
    if profile.name == "instant_test":
        return 0, difficulty
    duration = int((dist / max(1, profile.base_speed_px_per_sec)) * 1000)
    return int(max(profile.min_duration_ms, min(profile.max_duration_ms, duration))), difficulty


def _with_timestamps(points: list[MousePoint], duration_ms: int) -> list[MousePoint]:
    if not points:
        return []
    if len(points) == 1:
        return [MousePoint(points[0].x, points[0].y, 0)]
    output: list[MousePoint] = []
    for index, point in enumerate(points):
        timestamp = int(round((duration_ms * index) / (len(points) - 1)))
        output.append(MousePoint(point.x, point.y, timestamp))
    return output


def _dedupe(points: list[MousePoint]) -> list[MousePoint]:
    output: list[MousePoint] = []
    for point in points:
        if not output or output[-1].x != point.x or output[-1].y != point.y:
            output.append(point)
    return output


def _linear_points(start: MousePoint, click: MousePoint, count: int) -> list[MousePoint]:
    steps = max(1, count)
    return [
        MousePoint(
            int(round(start.x + (click.x - start.x) * i / steps)),
            int(round(start.y + (click.y - start.y) * i / steps)),
        )
        for i in range(steps + 1)
    ]


def _bezier_points(start: MousePoint, click: MousePoint, profile: MouseMovementProfile, rng: random.Random) -> list[MousePoint]:
    count = max(4, profile.waypoint_count)
    mid_x = (start.x + click.x) / 2.0
    mid_y = (start.y + click.y) / 2.0
    dx = click.x - start.x
    dy = click.y - start.y
    length = math.hypot(dx, dy) or 1.0
    perpendicular_x = -dy / length
    perpendicular_y = dx / length
    bend = rng.uniform(-0.18, 0.18) * length
    control_x = mid_x + perpendicular_x * bend
    control_y = mid_y + perpendicular_y * bend
    points: list[MousePoint] = []
    for i in range(count + 1):
        t = i / count
        x = (1 - t) ** 2 * start.x + 2 * (1 - t) * t * control_x + t**2 * click.x
        y = (1 - t) ** 2 * start.y + 2 * (1 - t) * t * control_y + t**2 * click.y
        if 0 < i < count and profile.path_jitter_px:
            x += rng.uniform(-profile.path_jitter_px, profile.path_jitter_px)
            y += rng.uniform(-profile.path_jitter_px, profile.path_jitter_px)
        points.append(MousePoint(int(round(x)), int(round(y))))
    return points


def _wind_points(start: MousePoint, click: MousePoint, target: MouseTarget, profile: MouseMovementProfile, rng: random.Random) -> tuple[list[MousePoint], list[str]]:
    warnings: list[str] = []
    x = float(start.x)
    y = float(start.y)
    vx = 0.0
    vy = 0.0
    wind_x = 0.0
    wind_y = 0.0
    max_velocity = max(1.0, float(profile.max_velocity))
    damping = math.sqrt(3.0)
    randomness_scale = math.sqrt(5.0)
    points = [MousePoint(start.x, start.y)]
    previous = (start.x, start.y)
    for _ in range(max(8, profile.max_iterations)):
        dx = float(click.x) - x
        dy = float(click.y) - y
        dist = math.hypot(dx, dy)
        if dist <= max(1.0, target.radius_px):
            break
        wind_limit = min(float(profile.wind), dist)
        if dist > profile.wind_damping_distance:
            wind_x = wind_x / damping + rng.uniform(-wind_limit, wind_limit) / randomness_scale
            wind_y = wind_y / damping + rng.uniform(-wind_limit, wind_limit) / randomness_scale
        else:
            wind_x /= damping
            wind_y /= damping
            max_velocity = max(2.0, max_velocity / damping)
        gravity_x = profile.gravity * dx / max(dist, 0.001)
        gravity_y = profile.gravity * dy / max(dist, 0.001)
        vx += wind_x + gravity_x
        vy += wind_y + gravity_y
        velocity = math.hypot(vx, vy)
        if velocity > max_velocity:
            clipped = rng.uniform(max_velocity * 0.55, max_velocity)
            vx = vx / velocity * clipped
            vy = vy / velocity * clipped
        x += vx
        y += vy
        rounded = (int(round(x)), int(round(y)))
        if rounded != previous:
            points.append(MousePoint(rounded[0], rounded[1]))
            previous = rounded
    else:
        warnings.append("wind_mouse max iterations reached")
    if points[-1].x != click.x or points[-1].y != click.y:
        if profile.correction_step_allowed:
            points.extend(_linear_points(points[-1], click, 2)[1:])
        else:
            points.append(click)
    return points, warnings


def _path_length(points: list[MousePoint]) -> float:
    total = 0.0
    for left, right in zip(points, points[1:]):
        total += math.hypot(right.x - left.x, right.y - left.y)
    return total


def _validate(points: list[MousePoint], click: MousePoint, target: MouseTarget) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not points:
        return "FAIL", ["movement plan has no points"]
    if points[-1].x != click.x or points[-1].y != click.y:
        warnings.append("final point differs from click point")
    if math.hypot(click.x - target.x, click.y - target.y) > max(1, target.radius_px):
        warnings.append("click point outside target radius")
        return "FAIL", warnings
    return ("WARN" if warnings else "PASS"), warnings


def plan_mouse_movement(
    start: MousePoint | tuple[int, int],
    target: MouseTarget | dict[str, Any],
    profile: str | MouseMovementProfile | None = None,
) -> MouseMovementPlan:
    if not isinstance(start, MousePoint):
        start = MousePoint(int(start[0]), int(start[1]))
    if not isinstance(target, MouseTarget):
        target = MouseTarget(
            x=int(target.get("x")),
            y=int(target.get("y")),
            radius_px=int(target.get("radiusPx", target.get("radius_px", 4))),
            width_px=target.get("widthPx") or target.get("width_px"),
            height_px=target.get("heightPx") or target.get("height_px"),
            label=str(target.get("label") or "target"),
            source=str(target.get("source") or "unknown"),
        )
    resolved = resolve_profile(profile)
    rng = random.Random(resolved.seed)
    click = _click_point(target, resolved, rng)
    duration, difficulty = _duration(start, target, resolved)
    warnings: list[str] = []
    if resolved.name == "instant_test":
        points = [start, click]
    elif resolved.name == "linear_debug":
        count = max(2, int(duration / max(1, resolved.sample_interval_ms)))
        points = _linear_points(start, click, count)
    elif resolved.name in {"smooth_bezier", "fitts_guided"}:
        points = _bezier_points(start, click, resolved, rng)
    elif resolved.name == "wind_mouse":
        points, warnings = _wind_points(start, click, target, resolved, rng)
    else:
        raise ValueError(f"unsupported movement profile: {resolved.name}")
    points = _dedupe(points)
    if points[0].x != start.x or points[0].y != start.y:
        points.insert(0, start)
    if points[-1].x != click.x or points[-1].y != click.y:
        points.append(click)
    validation, validation_warnings = _validate(points, click, target)
    warnings.extend(validation_warnings)
    points = _with_timestamps(points, duration)
    return MouseMovementPlan(
        schema=SCHEMA,
        profile_name=resolved.name,
        start=points[0],
        target=target,
        duration_ms=duration,
        points=points,
        click_point=points[-1],
        path_length_px=_path_length(points),
        estimated_difficulty=difficulty,
        warnings=list(dict.fromkeys(warnings)),
        validation_status=validation,
    )

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import random
import secrets
from typing import Iterable

from .camera import CameraKeyCapabilities
from .model import ScreenBounds, ScreenPoint, TargetGeometry


MAX_BEHAVIOR_SEED = (1 << 63) - 1


def _bounded_number(
    value: object,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
        raise ValueError(
            f"{field_name} must be in the range [{minimum}, {maximum}]"
        )
    return normalized


def _bounded_int(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(
            f"{field_name} must be in the range [{minimum}, {maximum}]"
        )
    return value


@dataclass(frozen=True, slots=True)
class BehaviorConfig:
    """One bounded engine-owned configuration for controlled variation."""

    seed: int | None = None
    route_lookahead_points: int = 12
    route_max_click_tiles: int = 30
    route_open_click_floor_tiles: int = 10
    route_corridor_radius_tiles: float = 2.0
    route_recovery_radius_tiles: float = 4.0
    route_turn_limit_degrees: float = 85.0
    camera_edge_margin_px: int = 72
    camera_deadband_px: int = 28
    camera_edge_hysteresis_px: int = 6
    camera_yaw_deadband_units: int = 32
    camera_min_visible_ratio: float = 0.55
    camera_well_framed_ratio: float = 0.82
    camera_hold_min_millis: int = 80
    camera_hold_max_millis: int = 600
    camera_route_leading_allowance_px: int = 48
    camera_object_shape_margin_px: int = 44
    camera_max_corrections: int = 2
    camera_yaw_full_correction_units: int = 4096
    camera_pitch_valid_min: int = 0
    camera_pitch_valid_max: int = 2048
    camera_screen_pitch_units_per_px: float = 2.0
    camera_zoom_desired_min: int = 320
    camera_zoom_desired_max: int = 448
    camera_wheel_step: int = 1
    resource_camera_suppression_ticks: int = 96
    aim_inset_px: int = 4
    aim_grid_size: int = 7
    aim_max_candidates: int = 32
    aim_strong_candidate_fraction: float = 0.45
    aim_repeat_radius_px: int = 9
    aim_history_size: int = 8
    aim_parallel_edge_clearance_floor_px: int = 8
    aim_parallel_edge_cost_weight: float = 6.0
    pre_move_delay_range: tuple[float, float] = (0.015, 0.075)
    settle_delay_range: tuple[float, float] = (0.045, 0.135)
    pre_click_delay_range: tuple[float, float] = (0.025, 0.095)
    post_action_delay_range: tuple[float, float] = (0.055, 0.180)
    route_pause_range: tuple[float, float] = (0.025, 0.120)

    def __post_init__(self) -> None:
        if self.seed is not None:
            _bounded_int(
                self.seed,
                "seed",
                minimum=0,
                maximum=MAX_BEHAVIOR_SEED,
            )
        for field_name, minimum, maximum in (
            ("route_lookahead_points", 1, 16),
            ("route_max_click_tiles", 4, 64),
            ("route_open_click_floor_tiles", 1, 30),
            ("camera_edge_margin_px", 8, 400),
            ("camera_deadband_px", 0, 200),
            ("camera_edge_hysteresis_px", 0, 200),
            ("camera_yaw_deadband_units", 0, 256),
            # The controller's effective ceiling is supplied separately by
            # the active input capability.  These are policy bounds, not a
            # duplicate of today's 250 ms Arduino protocol ceiling.
            ("camera_hold_min_millis", 20, 5000),
            ("camera_hold_max_millis", 20, 5000),
            ("camera_route_leading_allowance_px", 0, 200),
            ("camera_object_shape_margin_px", 0, 200),
            ("camera_max_corrections", 1, 2),
            ("camera_yaw_full_correction_units", 1, 8192),
            ("camera_pitch_valid_min", 0, 4096),
            ("camera_pitch_valid_max", 0, 4096),
            ("camera_zoom_desired_min", 1, 4096),
            ("camera_zoom_desired_max", 1, 4096),
            ("camera_wheel_step", 1, 3),
            ("resource_camera_suppression_ticks", 1, 256),
            ("aim_inset_px", 1, 32),
            ("aim_grid_size", 3, 15),
            ("aim_max_candidates", 1, 64),
            ("aim_repeat_radius_px", 1, 64),
            ("aim_history_size", 1, 32),
            ("aim_parallel_edge_clearance_floor_px", 1, 128),
        ):
            _bounded_int(
                getattr(self, field_name),
                field_name,
                minimum=minimum,
                maximum=maximum,
            )
        if self.route_open_click_floor_tiles > self.route_max_click_tiles:
            raise ValueError(
                "route_open_click_floor_tiles cannot exceed route_max_click_tiles"
            )
        if self.camera_hold_min_millis > self.camera_hold_max_millis:
            raise ValueError(
                "camera_hold_min_millis cannot exceed camera_hold_max_millis"
            )
        if self.camera_zoom_desired_min > self.camera_zoom_desired_max:
            raise ValueError(
                "camera_zoom_desired_min cannot exceed camera_zoom_desired_max"
            )
        if self.camera_pitch_valid_min > self.camera_pitch_valid_max:
            raise ValueError(
                "camera_pitch_valid_min cannot exceed camera_pitch_valid_max"
            )
        if self.camera_route_leading_allowance_px > self.camera_edge_margin_px:
            raise ValueError(
                "camera_route_leading_allowance_px cannot exceed camera_edge_margin_px"
            )
        if self.camera_edge_hysteresis_px > self.camera_edge_margin_px:
            raise ValueError(
                "camera_edge_hysteresis_px cannot exceed camera_edge_margin_px"
            )
        for field_name, minimum, maximum in (
            ("route_corridor_radius_tiles", 0.5, 16.0),
            ("route_recovery_radius_tiles", 0.5, 16.0),
            ("route_turn_limit_degrees", 1.0, 120.0),
            ("camera_min_visible_ratio", 0.0, 1.0),
            ("camera_well_framed_ratio", 0.0, 1.0),
            ("camera_screen_pitch_units_per_px", 0.1, 32.0),
            ("aim_strong_candidate_fraction", 0.05, 1.0),
            ("aim_parallel_edge_cost_weight", 0.0, 50.0),
        ):
            _bounded_number(
                getattr(self, field_name),
                field_name,
                minimum=minimum,
                maximum=maximum,
            )
        if self.route_recovery_radius_tiles < self.route_corridor_radius_tiles:
            raise ValueError(
                "route_recovery_radius_tiles cannot be below "
                "route_corridor_radius_tiles"
            )
        if self.camera_well_framed_ratio < self.camera_min_visible_ratio:
            raise ValueError(
                "camera_well_framed_ratio cannot be below camera_min_visible_ratio"
            )
        for field_name in (
            "pre_move_delay_range",
            "settle_delay_range",
            "pre_click_delay_range",
            "post_action_delay_range",
            "route_pause_range",
        ):
            value = getattr(self, field_name)
            if (
                not isinstance(value, tuple)
                or len(value) != 2
                or any(isinstance(item, bool) for item in value)
            ):
                raise TypeError(f"{field_name} must be a two-number tuple")
            low = _bounded_number(
                value[0], field_name, minimum=0.0, maximum=2.0
            )
            high = _bounded_number(
                value[1], field_name, minimum=0.0, maximum=2.0
            )
            if low > high:
                raise ValueError(f"{field_name} minimum cannot exceed maximum")


DEFAULT_BEHAVIOR_CONFIG = BehaviorConfig()


def classify_camera_zoom(
    zoom3d: int | None,
    config: BehaviorConfig = DEFAULT_BEHAVIOR_CONFIG,
) -> str:
    """Classify observed RuneLite zoom without granting zoom input authority.

    RuneLite's projection scale grows with ``zoom3d``: larger values are
    closer, and smaller values show more scene.  This diagnostic is therefore
    intentionally separate from camera actions; it only makes a tunable
    moderate band visible in EngineFrame.
    """

    if not isinstance(config, BehaviorConfig):
        raise TypeError("config must be BehaviorConfig")
    if zoom3d is None:
        return "unavailable"
    if isinstance(zoom3d, bool) or not isinstance(zoom3d, int) or zoom3d < 0:
        raise ValueError("zoom3d must be a non-negative integer or None")
    if zoom3d < config.camera_zoom_desired_min:
        return "too_far"
    if zoom3d > config.camera_zoom_desired_max:
        return "too_close"
    return "moderate"


@dataclass(frozen=True, slots=True)
class AimCandidate:
    point: ScreenPoint
    score: float
    boundary_clearance_px: float
    cursor_distance_px: float | None
    competing_clearance_px: float | None
    parallel_edge_transfer_cost: float

    def __post_init__(self) -> None:
        if not isinstance(self.point, ScreenPoint):
            raise TypeError("point must be ScreenPoint")
        for field_name in (
            "score",
            "boundary_clearance_px",
            "cursor_distance_px",
            "competing_clearance_px",
            "parallel_edge_transfer_cost",
        ):
            value = getattr(self, field_name)
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{field_name} must be finite or None")


@dataclass(frozen=True, slots=True)
class AimDecision:
    geometry_source: str
    shape_bounds: ScreenBounds
    inset_bounds: ScreenBounds
    candidates: tuple[AimCandidate, ...]
    selected: AimCandidate
    decision_id: str
    seed: int
    rejected_reasons: tuple[str, ...] = ()
    shape_polygon: tuple[ScreenPoint, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.geometry_source, str) or not self.geometry_source:
            raise ValueError("geometry_source must be non-empty")
        if not isinstance(self.shape_bounds, ScreenBounds) or not isinstance(
            self.inset_bounds, ScreenBounds
        ):
            raise TypeError("shape bounds must be ScreenBounds")
        if not self.candidates or self.selected not in self.candidates:
            raise ValueError("selected must be one of the candidates")
        if not isinstance(self.decision_id, str) or not self.decision_id:
            raise ValueError("decision_id must be non-empty")
        if not isinstance(self.shape_polygon, tuple) or any(
            not isinstance(point, ScreenPoint) for point in self.shape_polygon
        ):
            raise TypeError("shape_polygon must be a tuple of ScreenPoint values")
        _bounded_int(
            self.seed,
            "seed",
            minimum=0,
            maximum=MAX_BEHAVIOR_SEED,
        )


@dataclass(frozen=True, slots=True)
class TimingDecision:
    decision_id: str
    seed: int
    pre_move_delay_seconds: float
    settle_delay_seconds: float
    pre_click_delay_seconds: float
    post_action_delay_seconds: float
    route_pause_seconds: float


@dataclass(frozen=True, slots=True)
class CameraFramingDecision:
    classification: str
    desired_region: ScreenBounds
    target_point: ScreenPoint | None
    action: str
    hold_millis: int
    route_direction_bias: str
    correction_distance_px: float
    framing_context: str = "interaction"
    source_tick: int | None = None
    geometry_frame_id: str | None = None
    target_bounds: ScreenBounds | None = None
    edge_clearance_px: float | None = None
    required_edge_margin_px: int = 0
    lookahead_points: tuple[ScreenPoint, ...] = ()
    lookahead_bounds: ScreenBounds | None = None
    yaw_error_units: int | None = None
    screen_correction_x_px: float = 0.0
    screen_correction_y_px: float = 0.0
    visible_area_ratio: float | None = None
    zoom_classification: str = "unavailable"
    zoom_required_but_unavailable: bool = False
    pitch_valid: bool | None = None


def point_in_polygon(point: ScreenPoint, polygon: tuple[ScreenPoint, ...]) -> bool:
    """Return polygon containment, including an exact point on an edge."""

    if len(polygon) < 3:
        return False
    inside = False
    prior = polygon[-1]
    for current in polygon:
        if _point_on_segment(point, prior, current):
            return True
        crosses = (current.y > point.y) != (prior.y > point.y)
        if crosses:
            x_crossing = (
                (prior.x - current.x)
                * (point.y - current.y)
                / (prior.y - current.y)
                + current.x
            )
            if point.x < x_crossing:
                inside = not inside
        prior = current
    return inside


def _point_on_segment(
    point: ScreenPoint, first: ScreenPoint, second: ScreenPoint
) -> bool:
    cross = (point.y - first.y) * (second.x - first.x) - (
        point.x - first.x
    ) * (second.y - first.y)
    if cross != 0:
        return False
    return (
        min(first.x, second.x) <= point.x <= max(first.x, second.x)
        and min(first.y, second.y) <= point.y <= max(first.y, second.y)
    )


def polygon_bounds(polygon: tuple[ScreenPoint, ...]) -> ScreenBounds:
    if len(polygon) < 3:
        raise ValueError("polygon must contain at least three points")
    left = min(point.x for point in polygon)
    top = min(point.y for point in polygon)
    right = max(point.x for point in polygon)
    bottom = max(point.y for point in polygon)
    if right <= left or bottom <= top:
        raise ValueError("polygon must have nonzero area")
    return ScreenBounds(left, top, right - left + 1, bottom - top + 1)


def _distance_to_segment(
    point: ScreenPoint, first: ScreenPoint, second: ScreenPoint
) -> float:
    dx = second.x - first.x
    dy = second.y - first.y
    if dx == 0 and dy == 0:
        return math.hypot(point.x - first.x, point.y - first.y)
    projection = (
        (point.x - first.x) * dx + (point.y - first.y) * dy
    ) / (dx * dx + dy * dy)
    projection = max(0.0, min(1.0, projection))
    nearest_x = first.x + projection * dx
    nearest_y = first.y + projection * dy
    return math.hypot(point.x - nearest_x, point.y - nearest_y)


def _polygon_clearance(
    point: ScreenPoint, polygon: tuple[ScreenPoint, ...]
) -> float:
    return min(
        _distance_to_segment(point, polygon[index - 1], polygon[index])
        for index in range(len(polygon))
    )


def _bounds_clearance(point: ScreenPoint, bounds: ScreenBounds) -> float:
    return float(
        min(
            point.x - bounds.x,
            bounds.x + bounds.width - 1 - point.x,
            point.y - bounds.y,
            bounds.y + bounds.height - 1 - point.y,
        )
    )


def _parallel_edge_transfer_cost(
    point: ScreenPoint,
    cursor: ScreenPoint | None,
    canvas_bounds: ScreenBounds,
    *,
    clearance_floor_px: int,
) -> float:
    """Estimate long-move pressure from the edge orthogonal to travel.

    Relative HID transfer is bounded against all four canvas edges.  A point
    close to the top or bottom therefore makes a long horizontal approach much
    more expensive even when the point itself is valid.  The symmetric rule
    applies to vertical approaches near the left or right edge.  This is a
    ranking hint only; authoritative geometry and the final input envelope
    remain the safety authorities.
    """

    if cursor is None:
        return 0.0
    dx = abs(point.x - cursor.x)
    dy = abs(point.y - cursor.y)
    if dx >= dy:
        orthogonal_clearance = min(
            point.y - canvas_bounds.y,
            canvas_bounds.y + canvas_bounds.height - 1 - point.y,
        )
        dominant_distance = dx
    else:
        orthogonal_clearance = min(
            point.x - canvas_bounds.x,
            canvas_bounds.x + canvas_bounds.width - 1 - point.x,
        )
        dominant_distance = dy
    return float(dominant_distance) / max(
        clearance_floor_px,
        orthogonal_clearance,
    )


def _bounds_distance(point: ScreenPoint, bounds: ScreenBounds) -> float:
    dx = max(bounds.x - point.x, 0, point.x - (bounds.x + bounds.width - 1))
    dy = max(bounds.y - point.y, 0, point.y - (bounds.y + bounds.height - 1))
    return math.hypot(dx, dy)


def _intersect_bounds(first: ScreenBounds, second: ScreenBounds) -> ScreenBounds:
    left = max(first.x, second.x)
    top = max(first.y, second.y)
    right = min(first.x + first.width, second.x + second.width)
    bottom = min(first.y + first.height, second.y + second.height)
    if right <= left or bottom <= top:
        raise ValueError("bounds do not intersect")
    return ScreenBounds(left, top, right - left, bottom - top)


def _points_bounds(points: tuple[ScreenPoint, ...]) -> ScreenBounds:
    left = min(point.x for point in points)
    top = min(point.y for point in points)
    right = max(point.x for point in points)
    bottom = max(point.y for point in points)
    return ScreenBounds(left, top, right - left + 1, bottom - top + 1)


def _nested_bounds_clearance(inner: ScreenBounds, outer: ScreenBounds) -> float:
    """Return positive containment clearance or a negative clipping depth."""

    clearances = (
        inner.x - outer.x,
        outer.x + outer.width - (inner.x + inner.width),
        inner.y - outer.y,
        outer.y + outer.height - (inner.y + inner.height),
    )
    return float(min(clearances))


def _expand_route_leading_edge(
    desired: ScreenBounds,
    viewport: ScreenBounds,
    *,
    screen_dx: int,
    screen_dy: int,
    allowance: int,
) -> ScreenBounds:
    """Permit farther walk targets near, but not against, the travel edge."""

    left = desired.x
    top = desired.y
    right = desired.x + desired.width
    bottom = desired.y + desired.height
    if screen_dx < 0:
        left = max(viewport.x, left - allowance)
    elif screen_dx > 0:
        right = min(viewport.x + viewport.width, right + allowance)
    if screen_dy < 0:
        top = max(viewport.y, top - allowance)
    elif screen_dy > 0:
        bottom = min(viewport.y + viewport.height, bottom + allowance)
    return ScreenBounds(left, top, right - left, bottom - top)


class BehaviorPolicy:
    """Run-scoped seeded policy with bounded history and no global RNG state."""

    def __init__(
        self,
        config: BehaviorConfig = DEFAULT_BEHAVIOR_CONFIG,
        *,
        camera_capabilities: CameraKeyCapabilities = CameraKeyCapabilities(),
    ) -> None:
        if not isinstance(config, BehaviorConfig):
            raise TypeError("config must be BehaviorConfig")
        if not isinstance(camera_capabilities, CameraKeyCapabilities):
            raise TypeError("camera_capabilities must be CameraKeyCapabilities")
        self.config = config
        self.camera_capabilities = camera_capabilities
        self.seed = config.seed if config.seed is not None else secrets.randbits(63)
        self._history: dict[str, list[ScreenPoint]] = {}

    def derived_seed(self, decision_id: str, channel: str) -> int:
        if not isinstance(decision_id, str) or not decision_id:
            raise ValueError("decision_id must be non-empty")
        if not isinstance(channel, str) or not channel:
            raise ValueError("channel must be non-empty")
        digest = hashlib.sha256(
            f"{self.seed}:{channel}:{decision_id}".encode("utf-8")
        ).digest()
        return int.from_bytes(digest[:8], "big") & MAX_BEHAVIOR_SEED

    def _rng(self, decision_id: str, channel: str) -> random.Random:
        return random.Random(self.derived_seed(decision_id, channel))

    def timing(
        self,
        decision_id: str,
        *,
        pointer_distance_px: float = 0.0,
        target_extent_px: float = 1.0,
        camera_moved: bool = False,
        menu_opened: bool = False,
        route_move: bool = False,
    ) -> TimingDecision:
        rng = self._rng(decision_id, "timing")

        def choose(bounds: tuple[float, float]) -> float:
            return rng.uniform(float(bounds[0]), float(bounds[1]))

        precision = max(0.0, min(1.0, 1.0 - target_extent_px / 80.0))
        distance_factor = max(0.0, min(1.0, pointer_distance_px / 900.0))
        settle = choose(self.config.settle_delay_range) * (
            0.82 + 0.38 * precision + (0.18 if camera_moved else 0.0)
        )
        pre_click = choose(self.config.pre_click_delay_range) * (
            0.80 + 0.42 * precision + (0.18 if menu_opened else 0.0)
        )
        post_action = choose(self.config.post_action_delay_range) * (
            0.85 + 0.22 * distance_factor + (0.15 if menu_opened else 0.0)
        )
        return TimingDecision(
            decision_id=decision_id,
            seed=self.derived_seed(decision_id, "timing"),
            pre_move_delay_seconds=round(
                choose(self.config.pre_move_delay_range)
                * (0.75 + 0.35 * distance_factor),
                4,
            ),
            settle_delay_seconds=round(settle, 4),
            pre_click_delay_seconds=round(pre_click, 4),
            post_action_delay_seconds=round(post_action, 4),
            route_pause_seconds=round(
                choose(self.config.route_pause_range) if route_move else 0.0,
                4,
            ),
        )

    def select_aim_point(
        self,
        geometry: TargetGeometry,
        canvas_bounds: ScreenBounds,
        *,
        target_key: str,
        decision_id: str,
        cursor: ScreenPoint | None = None,
        excluded_bounds: Iterable[ScreenBounds] = (),
        competing_bounds: Iterable[ScreenBounds] = (),
    ) -> AimDecision:
        if not isinstance(geometry, TargetGeometry):
            raise TypeError("geometry must be TargetGeometry")
        if not isinstance(canvas_bounds, ScreenBounds):
            raise TypeError("canvas_bounds must be ScreenBounds")
        polygon = tuple(getattr(geometry, "screen_polygon", ()))
        source = getattr(geometry, "geometry_source", None) or "bounds"
        if source in {"clickbox", "convex_hull", "canvas_tile"} and len(polygon) < 3:
            raise ValueError(
                "authoritative interaction geometry is missing its polygon"
            )
        shape_bounds = (
            polygon_bounds(polygon)
            if len(polygon) >= 3
            else geometry.screen_bounds
        )
        if shape_bounds is None:
            raise ValueError("authoritative target shape is unavailable")
        visible_bounds = _intersect_bounds(shape_bounds, canvas_bounds)
        maximum_inset = max(
            0,
            (min(visible_bounds.width, visible_bounds.height) - 1) // 2,
        )
        inset = min(self.config.aim_inset_px, maximum_inset)
        exclusions = tuple(excluded_bounds)
        competitors = tuple(competing_bounds)
        for value in (*exclusions, *competitors):
            if not isinstance(value, ScreenBounds):
                raise TypeError("excluded and competing bounds must be ScreenBounds")

        prior = tuple(self._history.get(target_key, ()))
        rejected: set[str] = set()

        def candidates_for_inset(
            inset_value: int,
        ) -> tuple[ScreenBounds, list[AimCandidate]]:
            inset_bounds_value = ScreenBounds(
                visible_bounds.x + inset_value,
                visible_bounds.y + inset_value,
                max(1, visible_bounds.width - 2 * inset_value),
                max(1, visible_bounds.height - 2 * inset_value),
            )
            grid = self.config.aim_grid_size
            points: list[ScreenPoint] = []
            for row in range(grid):
                y = inset_bounds_value.y + min(
                    inset_bounds_value.height - 1,
                    ((2 * row + 1) * inset_bounds_value.height) // (2 * grid),
                )
                for column in range(grid):
                    x = inset_bounds_value.x + min(
                        inset_bounds_value.width - 1,
                        ((2 * column + 1) * inset_bounds_value.width) // (2 * grid),
                    )
                    point = ScreenPoint(x, y)
                    if point not in points:
                        points.append(point)
            if (
                geometry.screen_point is not None
                and geometry.screen_point not in points
            ):
                points.append(geometry.screen_point)

            found: list[AimCandidate] = []
            for point in points:
                if not canvas_bounds.contains(point):
                    rejected.add("outside_viewport")
                    continue
                if polygon and not point_in_polygon(point, polygon):
                    rejected.add("outside_authoritative_shape")
                    continue
                if not polygon and not visible_bounds.contains(point):
                    rejected.add("outside_authoritative_shape")
                    continue
                clearance = (
                    _polygon_clearance(point, polygon)
                    if polygon
                    else _bounds_clearance(point, visible_bounds)
                )
                if clearance + 1e-9 < inset_value:
                    rejected.add("inside_edge_inset")
                    continue
                if any(bounds.contains(point) for bounds in exclusions):
                    rejected.add("overlapping_ui")
                    continue
                if any(bounds.contains(point) for bounds in competitors):
                    rejected.add("competing_target_overlap")
                    continue
                cursor_distance = (
                    math.hypot(point.x - cursor.x, point.y - cursor.y)
                    if cursor is not None
                    else None
                )
                competing_clearance = (
                    min(_bounds_distance(point, bounds) for bounds in competitors)
                    if competitors
                    else None
                )
                repeat_distance = (
                    min(
                        math.hypot(point.x - old.x, point.y - old.y)
                        for old in prior
                    )
                    if prior
                    else float(self.config.aim_repeat_radius_px * 2)
                )
                repeat_penalty = max(
                    0.0,
                    self.config.aim_repeat_radius_px - repeat_distance,
                )
                parallel_edge_cost = _parallel_edge_transfer_cost(
                    point,
                    cursor,
                    canvas_bounds,
                    clearance_floor_px=(
                        self.config.aim_parallel_edge_clearance_floor_px
                    ),
                )
                score = (
                    clearance * 4.0
                    + min(competing_clearance or 24.0, 48.0) * 0.35
                    + min(repeat_distance, 36.0) * 0.55
                    - (cursor_distance or 0.0) * 0.012
                    - repeat_penalty * 3.0
                    - parallel_edge_cost
                    * self.config.aim_parallel_edge_cost_weight
                )
                found.append(
                    AimCandidate(
                        point=point,
                        score=round(score, 4),
                        boundary_clearance_px=round(clearance, 3),
                        cursor_distance_px=(
                            round(cursor_distance, 3)
                            if cursor_distance is not None
                            else None
                        ),
                        competing_clearance_px=(
                            round(competing_clearance, 3)
                            if competing_clearance is not None
                            else None
                        ),
                        parallel_edge_transfer_cost=round(
                            parallel_edge_cost,
                            4,
                        ),
                    )
                )
            return inset_bounds_value, found

        inset_attempts = tuple(
            dict.fromkeys(
                value
                for value in (inset, inset // 2, 2, 1, 0)
                if 0 <= value <= inset
            )
        )
        candidates: list[AimCandidate] = []
        inset_bounds = visible_bounds
        selected_inset = inset
        for selected_inset in inset_attempts:
            inset_bounds, candidates = candidates_for_inset(selected_inset)
            if candidates:
                break
        if not candidates:
            raise ValueError(
                "authoritative target shape contains no safe inset aim candidates"
            )
        if selected_inset < inset:
            rejected.add("adaptive_inset_reduced")
        candidates.sort(key=lambda candidate: (-candidate.score, candidate.point.y, candidate.point.x))
        candidates = candidates[: self.config.aim_max_candidates]
        strong_count = max(
            1,
            math.ceil(
                len(candidates) * self.config.aim_strong_candidate_fraction
            ),
        )
        strong = candidates[:strong_count]
        floor = strong[-1].score
        weights = [max(0.05, candidate.score - floor + 1.0) for candidate in strong]
        rng = self._rng(decision_id, "aim")
        selected = rng.choices(strong, weights=weights, k=1)[0]
        history = self._history.setdefault(target_key, [])
        history.append(selected.point)
        del history[: -self.config.aim_history_size]
        return AimDecision(
            geometry_source=source,
            shape_bounds=shape_bounds,
            inset_bounds=inset_bounds,
            candidates=tuple(candidates),
            selected=selected,
            decision_id=decision_id,
            seed=self.derived_seed(decision_id, "aim"),
            rejected_reasons=tuple(sorted(rejected)),
            shape_polygon=polygon,
        )

    def classify_camera(
        self,
        geometry: TargetGeometry,
        viewport: ScreenBounds,
        *,
        decision_id: str,
        route_dx: int = 0,
        route_dy: int = 0,
        player_point: ScreenPoint | None = None,
        framing_context: str = "interaction",
        lookahead_points: tuple[ScreenPoint, ...] = (),
        yaw_error_units: int | None = None,
        source_tick: int | None = None,
        geometry_frame_id: str | None = None,
        camera_zoom: int | None = None,
        camera_pitch: int | None = None,
    ) -> CameraFramingDecision:
        if not isinstance(geometry, TargetGeometry):
            raise TypeError("geometry must be TargetGeometry")
        if not isinstance(viewport, ScreenBounds):
            raise TypeError("viewport must be ScreenBounds")
        if player_point is not None and not isinstance(player_point, ScreenPoint):
            raise TypeError("player_point must be ScreenPoint or None")
        if framing_context not in {"route", "interaction"}:
            raise ValueError("framing_context must be route or interaction")
        if not isinstance(lookahead_points, tuple) or any(
            not isinstance(item, ScreenPoint) for item in lookahead_points
        ):
            raise TypeError("lookahead_points must be a tuple of ScreenPoint values")
        if len(lookahead_points) > self.config.route_lookahead_points:
            raise ValueError("lookahead_points exceeds the configured route lookahead")
        if yaw_error_units is not None and (
            not isinstance(yaw_error_units, int) or isinstance(yaw_error_units, bool)
        ):
            raise TypeError("yaw_error_units must be an integer or None")
        if source_tick is not None and (
            not isinstance(source_tick, int)
            or isinstance(source_tick, bool)
            or source_tick < 0
        ):
            raise ValueError("source_tick must be nonnegative or None")
        if geometry_frame_id is not None and (
            not isinstance(geometry_frame_id, str) or not geometry_frame_id.strip()
        ):
            raise ValueError("geometry_frame_id must be non-empty or None")
        if camera_pitch is not None and (
            not isinstance(camera_pitch, int)
            or isinstance(camera_pitch, bool)
            or camera_pitch < 0
        ):
            raise ValueError("camera_pitch must be nonnegative or None")
        zoom_classification = classify_camera_zoom(camera_zoom, self.config)
        pitch_valid = bool(
            camera_pitch is not None
            and self.config.camera_pitch_valid_min
            <= camera_pitch
            <= self.config.camera_pitch_valid_max
        )
        point = geometry.screen_point
        bias = _direction_label(route_dx, route_dy)
        margin_x = min(
            self.config.camera_edge_margin_px,
            max(8, viewport.width // 3),
        )
        margin_y = min(
            self.config.camera_edge_margin_px,
            max(8, viewport.height // 3),
        )
        # Place the destination opposite its projected direction from the
        # player so the viewport retains useful space beyond it. Fall back to
        # world-route signs when the optional player projection is unavailable.
        projected_dx = point.x - player_point.x if point and player_point else 0
        projected_dy = point.y - player_point.y if point and player_point else 0
        horizontal_basis = projected_dx or route_dx
        bias_x = (
            -int(math.copysign(viewport.width * 0.08, horizontal_basis))
            if horizontal_basis
            else 0
        )
        if projected_dy:
            bias_y = -int(math.copysign(viewport.height * 0.06, projected_dy))
        else:
            # World north projects upward in the conventional fallback view.
            bias_y = (
                int(math.copysign(viewport.height * 0.06, route_dy))
                if route_dy
                else 0
            )
        desired_width = max(1, viewport.width - 2 * margin_x)
        desired_height = max(1, viewport.height - 2 * margin_y)
        desired = ScreenBounds(
            viewport.x + margin_x + bias_x,
            viewport.y + margin_y + bias_y,
            desired_width,
            desired_height,
        )
        desired = _intersect_bounds(desired, viewport)
        screen_dy = projected_dy or -route_dy
        if framing_context == "route":
            desired = _expand_route_leading_edge(
                desired,
                viewport,
                screen_dx=horizontal_basis,
                screen_dy=screen_dy,
                allowance=self.config.camera_route_leading_allowance_px,
            )
        route_points = tuple(dict.fromkeys((point, *lookahead_points))) if point else lookahead_points
        lookahead_bounds = _points_bounds(route_points) if route_points else None
        ratio = geometry.visible_area_ratio
        target_bounds = geometry.screen_bounds
        required_edge_margin = max(
            0,
            (
                self.config.camera_edge_margin_px
                - self.config.camera_route_leading_allowance_px
                if framing_context == "route"
                else self.config.camera_object_shape_margin_px
            ),
        )
        shape_is_clipped = (
            framing_context == "interaction"
            and target_bounds is not None
            and _nested_bounds_clearance(target_bounds, viewport) < 0
        )
        partial_interaction_shape = bool(
            framing_context == "interaction"
            and target_bounds is not None
            and (
                shape_is_clipped
                or target_bounds.width
                > max(1, viewport.width - 2 * required_edge_margin)
                or target_bounds.height
                > max(1, viewport.height - 2 * required_edge_margin)
            )
        )
        large_partial_interaction_shape = bool(
            partial_interaction_shape
            and target_bounds is not None
            and (
                target_bounds.width * 2 >= viewport.width
                or target_bounds.height * 2 >= viewport.height
            )
        )
        edge_clearance = (
            _bounds_clearance(point, viewport)
            if framing_context == "route" and point is not None
            else (
                (
                    _bounds_clearance(point, viewport)
                    if shape_is_clipped and point is not None
                    else _nested_bounds_clearance(target_bounds, viewport)
                )
                if target_bounds is not None
                else (_bounds_clearance(point, viewport) if point is not None else None)
            )
        )
        correction_vectors: list[tuple[float, float]] = []
        if point is not None:
            correction_vectors.append(_point_correction_vector(point, desired))
        correction_vectors.extend(
            _point_correction_vector(item, desired) for item in lookahead_points
        )
        if framing_context == "interaction" and target_bounds is not None:
            safe_margin_x = min(
                required_edge_margin,
                max(0, (viewport.width - 1) // 2),
            )
            safe_margin_y = min(
                required_edge_margin,
                max(0, (viewport.height - 1) // 2),
            )
            safe_shape_region = ScreenBounds(
                viewport.x + safe_margin_x,
                viewport.y + safe_margin_y,
                max(1, viewport.width - 2 * safe_margin_x),
                max(1, viewport.height - 2 * safe_margin_y),
            )
            if (
                shape_is_clipped
                or target_bounds.width > safe_shape_region.width
                or target_bounds.height > safe_shape_region.height
            ):
                # Tall RuneLite clickboxes can remain clipped at a canvas edge
                # as pitch changes even after a safe interior aim point has
                # moved into the desired region.  Require a useful portion of
                # that shape inside the inset viewport instead of asking for
                # impossible full-bounds containment.
                useful_width = min(
                    target_bounds.width,
                    safe_shape_region.width,
                    max(1, required_edge_margin),
                )
                useful_height = min(
                    target_bounds.height,
                    safe_shape_region.height,
                    max(1, required_edge_margin),
                )
                correction_vectors.append(
                    _bounds_overlap_correction_vector(
                        target_bounds,
                        safe_shape_region,
                        minimum_width=useful_width,
                        minimum_height=useful_height,
                    )
                )
            else:
                correction_vectors.append(
                    _bounds_containment_correction_vector(
                        target_bounds,
                        safe_shape_region,
                    )
                )
        correction_x, correction_y = max(
            correction_vectors,
            key=lambda item: math.hypot(*item),
            default=(0.0, 0.0),
        )
        correction = math.hypot(correction_x, correction_y)
        if (
            not geometry.available
            or not geometry.on_screen
            or not geometry.visible
            or not geometry.actionable
            or point is None
        ):
            classification = "not_visible"
        elif (
            ratio is not None
            and ratio < self.config.camera_min_visible_ratio
            and not large_partial_interaction_shape
        ):
            classification = "obscured_or_contradictory"
        elif (
            (
                edge_clearance is not None
                and (
                    edge_clearance < 0
                    or edge_clearance
                    < max(
                        0,
                        required_edge_margin
                        - self.config.camera_edge_hysteresis_px,
                    )
                )
            )
            or correction > self.config.camera_deadband_px
        ):
            classification = "barely_visible"
        elif ratio is None or ratio < self.config.camera_well_framed_ratio:
            classification = "usable"
        else:
            classification = "well_framed"

        if classification == "well_framed" or (
            classification == "usable"
            and correction <= self.config.camera_deadband_px
        ):
            action = "none"
            hold = 0
        else:
            action = "reframe"
            scale = (
                (
                    1.0
                    if yaw_error_units is None
                    else max(
                        0.35,
                        min(
                            1.0,
                            abs(yaw_error_units)
                            / self.config.camera_yaw_full_correction_units,
                        ),
                    )
                )
                if classification == "not_visible"
                else max(
                    0.0,
                    min(1.0, correction / max(1.0, viewport.width * 0.45)),
                )
            )
            effective_max = min(
                self.config.camera_hold_max_millis,
                self.camera_capabilities.max_hold_millis,
            )
            effective_min = min(
                self.config.camera_hold_min_millis,
                effective_max,
            )
            base = effective_min + round(
                scale
                * (
                    effective_max
                    - effective_min
                )
            )
            hold = max(
                effective_min,
                min(effective_max, base),
            )
        zoom_required_but_unavailable = bool(
            action != "none"
            and target_bounds is not None
            and (
                (
                    zoom_classification == "too_close"
                    and (
                        target_bounds.width > desired.width
                        or target_bounds.height > desired.height
                    )
                    and classification
                    in {"obscured_or_contradictory", "barely_visible"}
                )
                or (
                    zoom_classification == "too_far"
                    and not geometry.actionable
                    and target_bounds.width <= 8
                    and target_bounds.height <= 8
                )
            )
        )
        if zoom_required_but_unavailable:
            action = "zoom_required_but_unavailable"
            hold = 0
        return CameraFramingDecision(
            classification=classification,
            desired_region=desired,
            target_point=point,
            action=action,
            hold_millis=hold,
            route_direction_bias=bias,
            correction_distance_px=round(correction, 3),
            framing_context=framing_context,
            source_tick=source_tick,
            geometry_frame_id=geometry_frame_id,
            target_bounds=target_bounds,
            edge_clearance_px=(
                None if edge_clearance is None else round(edge_clearance, 3)
            ),
            required_edge_margin_px=required_edge_margin,
            lookahead_points=route_points,
            lookahead_bounds=lookahead_bounds,
            yaw_error_units=yaw_error_units,
            screen_correction_x_px=round(correction_x, 3),
            screen_correction_y_px=round(correction_y, 3),
            visible_area_ratio=ratio,
            zoom_classification=zoom_classification,
            zoom_required_but_unavailable=zoom_required_but_unavailable,
            pitch_valid=pitch_valid,
        )


def _distance_to_bounds(point: ScreenPoint, bounds: ScreenBounds) -> float:
    if bounds.contains(point):
        return 0.0
    return _bounds_distance(point, bounds)


def _point_correction_vector(
    point: ScreenPoint,
    bounds: ScreenBounds,
) -> tuple[float, float]:
    """Return the smallest signed translation that puts a point in bounds."""

    right = bounds.x + bounds.width - 1
    bottom = bounds.y + bounds.height - 1
    dx = float(bounds.x - point.x) if point.x < bounds.x else 0.0
    if point.x > right:
        dx = float(right - point.x)
    dy = float(bounds.y - point.y) if point.y < bounds.y else 0.0
    if point.y > bottom:
        dy = float(bottom - point.y)
    return dx, dy


def _bounds_containment_correction_vector(
    inner: ScreenBounds,
    outer: ScreenBounds,
) -> tuple[float, float]:
    """Return a signed translation that moves an interaction shape inward."""

    inner_right = inner.x + inner.width - 1
    inner_bottom = inner.y + inner.height - 1
    outer_right = outer.x + outer.width - 1
    outer_bottom = outer.y + outer.height - 1
    if inner.width > outer.width:
        dx = float(outer.center.x - inner.center.x)
    elif inner.x < outer.x:
        dx = float(outer.x - inner.x)
    elif inner_right > outer_right:
        dx = float(outer_right - inner_right)
    else:
        dx = 0.0
    if inner.height > outer.height:
        dy = float(outer.center.y - inner.center.y)
    elif inner.y < outer.y:
        dy = float(outer.y - inner.y)
    elif inner_bottom > outer_bottom:
        dy = float(outer_bottom - inner_bottom)
    else:
        dy = 0.0
    return dx, dy


def _bounds_overlap_correction_vector(
    inner: ScreenBounds,
    outer: ScreenBounds,
    *,
    minimum_width: int,
    minimum_height: int,
) -> tuple[float, float]:
    """Translate bounds enough to expose a useful portion inside ``outer``."""

    inner_right = inner.x + inner.width - 1
    inner_bottom = inner.y + inner.height - 1
    outer_right = outer.x + outer.width - 1
    outer_bottom = outer.y + outer.height - 1
    dx = 0.0
    if inner_right < outer.x + minimum_width - 1:
        dx = float(outer.x + minimum_width - 1 - inner_right)
    elif inner.x > outer_right - minimum_width + 1:
        dx = float(outer_right - minimum_width + 1 - inner.x)
    dy = 0.0
    if inner_bottom < outer.y + minimum_height - 1:
        dy = float(outer.y + minimum_height - 1 - inner_bottom)
    elif inner.y > outer_bottom - minimum_height + 1:
        dy = float(outer_bottom - minimum_height + 1 - inner.y)
    return dx, dy


def _direction_label(dx: int, dy: int) -> str:
    horizontal = "east" if dx > 0 else ("west" if dx < 0 else "")
    vertical = "north" if dy > 0 else ("south" if dy < 0 else "")
    if horizontal and vertical:
        return f"{vertical}_{horizontal}"
    return horizontal or vertical or "stationary"

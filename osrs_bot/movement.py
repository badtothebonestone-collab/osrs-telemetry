from __future__ import annotations

from dataclasses import dataclass
from math import acos, ceil, degrees, hypot

from .definition import (
    FixedRoute,
    FixedRouteStep,
    RoutePointClassification,
    RouteStepKind,
)
from .model import WorldPoint


_EPSILON = 1e-9


def _require_finite_positive(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a positive finite number")
    numeric = float(value)
    if numeric <= 0.0 or numeric == float("inf") or numeric != numeric:
        raise ValueError(f"{field_name} must be a positive finite number")


@dataclass(frozen=True, slots=True)
class RouteCandidateSupport:
    """Fresh scene facts for one definition-owned route point."""

    route_index: int
    plane_supported: bool
    scene_supported: bool
    collision_supported: bool
    projectable: bool
    ui_clear: bool
    camera_adjustable: bool = False
    shortcut_clear: bool = True

    def __post_init__(self) -> None:
        if (
            isinstance(self.route_index, bool)
            or not isinstance(self.route_index, int)
            or self.route_index < 0
        ):
            raise ValueError("route_index must be a nonnegative integer")
        for field_name in (
            "plane_supported",
            "scene_supported",
            "collision_supported",
            "projectable",
            "ui_clear",
            "camera_adjustable",
            "shortcut_clear",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be a boolean")


@dataclass(frozen=True, slots=True)
class RouteSelectionLimits:
    corridor_limit_tiles: float = 2.0
    max_click_distance_tiles: float = 30.0
    open_click_floor_tiles: float = 10.0
    max_skipped_turn_degrees: float = 85.0
    backtrack_tolerance_tiles: float = 0.75
    zigzag_deviation_tiles: float = 0.75

    def __post_init__(self) -> None:
        for field_name in (
            "corridor_limit_tiles",
            "max_click_distance_tiles",
            "open_click_floor_tiles",
            "max_skipped_turn_degrees",
            "backtrack_tolerance_tiles",
            "zigzag_deviation_tiles",
        ):
            _require_finite_positive(field_name, getattr(self, field_name))
        if self.max_skipped_turn_degrees > 180.0:
            raise ValueError("max_skipped_turn_degrees must not exceed 180")
        if self.open_click_floor_tiles > self.max_click_distance_tiles:
            raise ValueError(
                "open_click_floor_tiles must not exceed max_click_distance_tiles"
            )


@dataclass(frozen=True, slots=True)
class RouteProgress:
    distance_along_route: float
    remaining_distance: float
    lateral_deviation: float
    signed_lateral_deviation: float
    segment_index: int
    projected_x: float
    projected_y: float
    plane: int
    progress_delta: float | None = None
    backtracking: bool = False
    zigzagging: bool = False


@dataclass(frozen=True, slots=True)
class RouteCandidateRejection:
    route_index: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RouteTargetSelection:
    progress: RouteProgress
    selected_index: int | None
    selected_step: FixedRouteStep | None
    requested_tile_distance: float
    expected_progress: float
    skipped_guidance_indices: tuple[int, ...]
    mandatory_next_index: int | None
    fallback_reason: str | None
    maximum_shortcut_deviation: float | None
    rejections: tuple[RouteCandidateRejection, ...]

    @property
    def remaining_route_distance(self) -> float:
        return self.progress.remaining_distance


@dataclass(frozen=True, slots=True)
class _RouteSegment:
    step_index: int
    start: WorldPoint
    end: WorldPoint
    start_distance: float
    end_distance: float

    @property
    def length(self) -> float:
        return self.end_distance - self.start_distance


@dataclass(frozen=True, slots=True)
class _Projection:
    distance: float
    signed_distance: float
    along: float
    x: float
    y: float
    segment_index: int


def _route_geometry(route: FixedRoute) -> tuple[tuple[_RouteSegment, ...], tuple[float, ...]]:
    segments: list[_RouteSegment] = []
    cumulative: list[float] = []
    state = route.start_anchor
    distance = 0.0
    for index, step in enumerate(route.steps):
        length = hypot(step.location.x - state.x, step.location.y - state.y)
        segment = _RouteSegment(index, state, step.location, distance, distance + length)
        segments.append(segment)
        distance += length
        cumulative.append(distance)
        if step.kind is RouteStepKind.OBJECT:
            assert step.expected_plane is not None
            state = WorldPoint(step.location.x, step.location.y, step.expected_plane)
        else:
            state = step.location
    return tuple(segments), tuple(cumulative)


def _project(point: WorldPoint, segment: _RouteSegment) -> _Projection:
    dx = segment.end.x - segment.start.x
    dy = segment.end.y - segment.start.y
    length_squared = float(dx * dx + dy * dy)
    if length_squared <= _EPSILON:
        x = float(segment.start.x)
        y = float(segment.start.y)
        signed = 0.0
        along = segment.start_distance
    else:
        raw_t = (
            (point.x - segment.start.x) * dx + (point.y - segment.start.y) * dy
        ) / length_squared
        t = min(1.0, max(0.0, raw_t))
        x = segment.start.x + t * dx
        y = segment.start.y + t * dy
        signed = (dx * (point.y - y) - dy * (point.x - x)) / segment.length
        along = segment.start_distance + t * segment.length
    distance = hypot(point.x - x, point.y - y)
    return _Projection(distance, signed, along, x, y, segment.step_index)


def route_progress(
    route: FixedRoute,
    current_location: WorldPoint,
    *,
    previous: RouteProgress | None = None,
    limits: RouteSelectionLimits = RouteSelectionLimits(),
) -> RouteProgress:
    """Project a player location onto the definition-owned route polyline."""

    if not isinstance(route, FixedRoute):
        raise ValueError("route must be a FixedRoute")
    if not isinstance(current_location, WorldPoint):
        raise ValueError("current_location must be a WorldPoint")
    if previous is not None and not isinstance(previous, RouteProgress):
        raise ValueError("previous must be a RouteProgress or None")
    if not isinstance(limits, RouteSelectionLimits):
        raise ValueError("limits must be RouteSelectionLimits")

    segments, cumulative = _route_geometry(route)
    projections = tuple(
        _project(current_location, segment)
        for segment in segments
        if segment.start.plane == current_location.plane
        and segment.end.plane == current_location.plane
    )
    if not projections:
        raise ValueError("current_location plane does not occur on the route")

    if previous is None:
        best = min(projections, key=lambda item: (item.distance, -item.along))
    else:
        best = min(
            projections,
            key=lambda item: (
                item.distance,
                abs(item.along - previous.distance_along_route),
                -item.along,
            ),
        )

    total_distance = cumulative[-1]
    delta = None if previous is None else best.along - previous.distance_along_route
    backtracking = delta is not None and delta < -limits.backtrack_tolerance_tiles
    zigzagging = (
        previous is not None
        and abs(previous.signed_lateral_deviation) >= limits.zigzag_deviation_tiles
        and abs(best.signed_distance) >= limits.zigzag_deviation_tiles
        and previous.signed_lateral_deviation * best.signed_distance < 0.0
    )
    return RouteProgress(
        distance_along_route=best.along,
        remaining_distance=max(0.0, total_distance - best.along),
        lateral_deviation=best.distance,
        signed_lateral_deviation=best.signed_distance,
        segment_index=best.segment_index,
        projected_x=best.x,
        projected_y=best.y,
        plane=current_location.plane,
        progress_delta=delta,
        backtracking=backtracking,
        zigzagging=zigzagging,
    )


def _turn_angle_degrees(
    route: FixedRoute,
    segments: tuple[_RouteSegment, ...],
    vertex_index: int,
) -> float:
    if vertex_index < 0 or vertex_index + 1 >= len(segments):
        return 0.0
    before = segments[vertex_index]
    after = segments[vertex_index + 1]
    if before.end.plane != after.end.plane:
        return 0.0
    first_x = before.end.x - before.start.x
    first_y = before.end.y - before.start.y
    second_x = after.end.x - after.start.x
    second_y = after.end.y - after.start.y
    first_length = hypot(first_x, first_y)
    second_length = hypot(second_x, second_y)
    if first_length <= _EPSILON or second_length <= _EPSILON:
        return 0.0
    cosine = (first_x * second_x + first_y * second_y) / (first_length * second_length)
    return degrees(acos(min(1.0, max(-1.0, cosine))))


def _point_to_segment_distance(x: float, y: float, segment: _RouteSegment) -> float:
    dx = segment.end.x - segment.start.x
    dy = segment.end.y - segment.start.y
    length_squared = float(dx * dx + dy * dy)
    if length_squared <= _EPSILON:
        return hypot(x - segment.start.x, y - segment.start.y)
    t = ((x - segment.start.x) * dx + (y - segment.start.y) * dy) / length_squared
    t = min(1.0, max(0.0, t))
    return hypot(x - (segment.start.x + t * dx), y - (segment.start.y + t * dy))


def _maximum_shortcut_deviation(
    current: WorldPoint,
    target: WorldPoint,
    segments: tuple[_RouteSegment, ...],
    progress_distance: float,
    target_distance: float,
) -> float:
    relevant = tuple(
        segment
        for segment in segments
        if segment.start.plane == current.plane
        and segment.end.plane == current.plane
        and segment.end_distance >= progress_distance - _EPSILON
        and segment.start_distance <= target_distance + _EPSILON
    )
    if not relevant:
        return float("inf")
    click_distance = hypot(target.x - current.x, target.y - current.y)
    sample_count = max(2, ceil(click_distance * 2.0))
    maximum = 0.0
    for sample in range(sample_count + 1):
        fraction = sample / sample_count
        x = current.x + fraction * (target.x - current.x)
        y = current.y + fraction * (target.y - current.y)
        deviation = min(_point_to_segment_distance(x, y, segment) for segment in relevant)
        maximum = max(maximum, deviation)
    return maximum


def _pending_mandatory_index(
    route: FixedRoute,
    current: WorldPoint,
    cumulative: tuple[float, ...],
    progress_floor: float,
) -> int | None:
    mandatory = {
        RoutePointClassification.MANDATORY_TRANSITION,
        RoutePointClassification.MANDATORY_TURN,
        RoutePointClassification.ARRIVAL_POINT,
    }
    for index, step in enumerate(route.steps):
        if step.classification not in mandatory:
            continue
        target_progress = cumulative[index]
        if step.kind is RouteStepKind.OBJECT:
            if step.expected_plane == current.plane and target_progress <= progress_floor + _EPSILON:
                continue
            if target_progress > progress_floor + _EPSILON or step.location.plane == current.plane:
                return index
            continue
        if (
            step.location.plane == current.plane
            and current.distance_to(step.location) <= step.arrival_radius
            and target_progress - progress_floor <= step.arrival_radius
        ):
            continue
        if target_progress > progress_floor + _EPSILON:
            return index
    return None


def _support_reasons(support: RouteCandidateSupport | None) -> list[str]:
    if support is None:
        return ["missing_support_facts"]
    reasons: list[str] = []
    if not support.plane_supported:
        reasons.append("plane_unsupported")
    if not support.scene_supported:
        reasons.append("scene_unsupported")
    if not support.collision_supported:
        reasons.append("collision_unsupported")
    if not support.projectable and not support.camera_adjustable:
        reasons.append("not_projectable")
    if not support.ui_clear:
        reasons.append("ui_blocked")
    return reasons


def select_farthest_useful_route_target(
    route: FixedRoute,
    current_location: WorldPoint,
    candidate_support: tuple[RouteCandidateSupport, ...],
    *,
    previous_progress: RouteProgress | None = None,
    limits: RouteSelectionLimits = RouteSelectionLimits(),
) -> RouteTargetSelection:
    """Choose the farthest safe, forward walk point without crossing a route barrier."""

    if type(candidate_support) is not tuple:
        raise ValueError("candidate_support must be a tuple")
    if any(not isinstance(item, RouteCandidateSupport) for item in candidate_support):
        raise ValueError("candidate_support must contain RouteCandidateSupport values")
    support_by_index = {item.route_index: item for item in candidate_support}
    if len(support_by_index) != len(candidate_support):
        raise ValueError("candidate_support route indices must be unique")
    if any(index >= len(route.steps) for index in support_by_index):
        raise ValueError("candidate_support route_index is outside the route")

    progress = route_progress(
        route,
        current_location,
        previous=previous_progress,
        limits=limits,
    )
    segments, cumulative = _route_geometry(route)
    progress_floor = progress.distance_along_route
    if previous_progress is not None:
        progress_floor = max(progress_floor, previous_progress.distance_along_route)
    mandatory_next = _pending_mandatory_index(
        route,
        current_location,
        cumulative,
        progress_floor,
    )

    valid: list[tuple[float, int, float, float]] = []
    reentry_indices: set[int] = set()
    rejections: list[RouteCandidateRejection] = []
    for index, step in enumerate(route.steps):
        reasons: list[str] = []
        support = support_by_index.get(index)
        target_progress = cumulative[index]
        if step.kind is not RouteStepKind.WALK:
            reasons.append("mandatory_transition_action")
        if step.location.plane != current_location.plane:
            reasons.append("wrong_plane")
        if mandatory_next is not None and index > mandatory_next:
            reasons.append("mandatory_point_not_skippable")

        reasons.extend(_support_reasons(support))
        requested_distance = float(current_location.distance_to(step.location))
        if requested_distance > limits.max_click_distance_tiles:
            reasons.append("outside_click_range")
        if (
            support is not None
            and not support.shortcut_clear
            and requested_distance > 4.0
        ):
            reasons.append("shortcut_unsupported")

        if index > 0:
            skipped_turn = any(
                route.steps[turn_index].classification
                is RoutePointClassification.NORMAL_GUIDANCE
                and cumulative[turn_index] > progress_floor + _EPSILON
                and _turn_angle_degrees(route, segments, turn_index)
                > limits.max_skipped_turn_degrees
                for turn_index in range(index)
            )
            if skipped_turn:
                reasons.append("turn_limit")

        shortcut_deviation = _maximum_shortcut_deviation(
            current_location,
            step.location,
            segments,
            progress.distance_along_route,
            target_progress,
        )
        corridor_recovery = bool(
            progress.lateral_deviation > limits.corridor_limit_tiles
            and requested_distance <= 4.0
            and shortcut_deviation
            <= progress.lateral_deviation + _EPSILON
        )
        # A player may finish an interaction just beyond the start/end of a
        # route segment.  In that case the polyline projection is already at
        # the segment endpoint even though the player is still several tiles
        # outside the corridor.  Permit only a short, definition-owned reentry
        # click; ordinary candidates must still advance route progress.
        reentry_recovery = bool(
            corridor_recovery
            and target_progress <= progress.distance_along_route + _EPSILON
            and (
                previous_progress is None
                or target_progress
                >= previous_progress.distance_along_route
                - limits.backtrack_tolerance_tiles
            )
        )
        if (
            target_progress <= progress.distance_along_route + _EPSILON
            and not reentry_recovery
        ):
            reasons.append("not_forward")
        if (
            previous_progress is not None
            and target_progress <= progress_floor + _EPSILON
            and progress.backtracking
            and not reentry_recovery
        ):
            reasons.append("backtracking")
        if (
            shortcut_deviation > limits.corridor_limit_tiles
            and not corridor_recovery
        ):
            reasons.append("corridor_violation")

        unique_reasons = tuple(dict.fromkeys(reasons))
        if unique_reasons:
            rejections.append(RouteCandidateRejection(index, unique_reasons))
        else:
            valid.append((target_progress, index, requested_distance, shortcut_deviation))
            if reentry_recovery:
                reentry_indices.add(index)

    if not valid:
        if progress.lateral_deviation > limits.corridor_limit_tiles:
            fallback = "outside_route_corridor"
        elif (
            mandatory_next is not None
            and route.steps[mandatory_next].classification
            is RoutePointClassification.MANDATORY_TRANSITION
            and cumulative[mandatory_next]
            <= progress_floor + route.steps[mandatory_next].arrival_radius
        ):
            fallback = "mandatory_transition_required"
        else:
            fallback = "no_supported_forward_candidate"
        return RouteTargetSelection(
            progress=progress,
            selected_index=None,
            selected_step=None,
            requested_tile_distance=0.0,
            expected_progress=progress.distance_along_route,
            skipped_guidance_indices=(),
            mandatory_next_index=mandatory_next,
            fallback_reason=fallback,
            maximum_shortcut_deviation=None,
            rejections=tuple(rejections),
        )

    selected_progress, selected_index, requested_distance, shortcut_deviation = max(valid)
    skipped = tuple(
        index
        for index, step in enumerate(route.steps[:selected_index])
        if step.classification is RoutePointClassification.NORMAL_GUIDANCE
        and cumulative[index] > progress_floor + _EPSILON
    )
    fallback = (
        "route_reentry_correction_required"
        if selected_index in reentry_indices
        else (
            "short_correction_required"
            if requested_distance <= 4.0
            else (
                "medium_movement_required"
                if requested_distance < limits.open_click_floor_tiles
                else None
            )
        )
    )
    return RouteTargetSelection(
        progress=progress,
        selected_index=selected_index,
        selected_step=route.steps[selected_index],
        requested_tile_distance=requested_distance,
        expected_progress=selected_progress,
        skipped_guidance_indices=skipped,
        mandatory_next_index=mandatory_next,
        fallback_reason=fallback,
        maximum_shortcut_deviation=shortcut_deviation,
        rejections=tuple(rejections),
    )


__all__ = [
    "RouteCandidateRejection",
    "RouteCandidateSupport",
    "RouteProgress",
    "RouteSelectionLimits",
    "RouteTargetSelection",
    "route_progress",
    "select_farthest_useful_route_target",
]

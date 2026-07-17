from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import re
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .model import Action, Observation, ScreenBounds, ScreenPoint, WorldPoint

if TYPE_CHECKING:
    from .verification import VerificationResult


_MAX_TILE_PROJECTIONS = 16
_MAX_PRIORITY_OBJECT_IDS = 32
_MAX_PRIORITY_OBJECT_KEYS = 32
_MAX_PRIORITY_OBJECT_KEY_LENGTH = 256
_MAX_OBSERVATION_RADIUS_TILES = 104
_MAX_OBSERVATION_OBJECTS = 4_096
_MAX_OBSERVATION_PURPOSE_LENGTH = 64
_MAX_ROUTE_REJECTION_REASONS = 16
_MAX_CAMERA_CONTEXT_LENGTH = 64
_MAX_CAMERA_GEOMETRY_FRAME_ID_LENGTH = 256
_MAX_CAMERA_YAW_ERROR_UNITS = 16_384
_MAX_CAMERA_CORRECTION_ATTEMPTS = 64
_MAX_CAMERA_CUMULATIVE_HOLD_MILLIS = 60_000
_MAX_CAMERA_MARGIN_PX = 16_384
_REJECTION_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_OBSERVATION_PURPOSE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class TaskStatus(str, Enum):
    RUNNING = "running"
    COMPLETE = "complete"
    BLOCKED = "blocked"


class CameraAcquisitionState(str, Enum):
    """Typed lifecycle for one target-locked camera acquisition episode."""

    IDLE = "idle"
    STABILIZING = "stabilizing"
    COARSE = "coarse"
    FINE = "fine"
    READY = "ready"
    ZOOM_REQUIRED_BUT_UNAVAILABLE = "zoom_required_but_unavailable"
    NON_IMPROVING = "non_improving"
    EXHAUSTED = "exhausted"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True)
class ObservationRequest:
    tile_projections: tuple[tuple[str, WorldPoint], ...] = ()
    priority_object_ids: tuple[int, ...] = ()
    priority_object_keys: tuple[str, ...] = ()
    center_world_location: WorldPoint | None = None
    radius_tiles: int | None = None
    max_objects: int | None = None
    max_projection_objects: int | None = None
    purpose: str = "unspecified"

    def __post_init__(self) -> None:
        if not isinstance(self.tile_projections, tuple):
            raise ValueError("tile_projections must be a tuple")
        if len(self.tile_projections) > _MAX_TILE_PROJECTIONS:
            raise ValueError(
                f"at most {_MAX_TILE_PROJECTIONS} tile projections are allowed"
            )

        labels: set[str] = set()
        for projection in self.tile_projections:
            if not isinstance(projection, tuple) or len(projection) != 2:
                raise ValueError(
                    "each tile projection must be a (label, WorldPoint) tuple"
                )
            label, point = projection
            if not isinstance(label, str) or not label.strip():
                raise ValueError("tile projection labels must be non-empty strings")
            if label in labels:
                raise ValueError("tile projection labels must be unique")
            if not isinstance(point, WorldPoint):
                raise ValueError("tile projection values must be WorldPoint instances")
            labels.add(label)

        if not isinstance(self.priority_object_ids, tuple):
            raise ValueError("priority_object_ids must be a tuple")
        if len(self.priority_object_ids) > _MAX_PRIORITY_OBJECT_IDS:
            raise ValueError(
                f"at most {_MAX_PRIORITY_OBJECT_IDS} priority object IDs are allowed"
            )
        if len(set(self.priority_object_ids)) != len(self.priority_object_ids):
            raise ValueError("priority_object_ids must be unique")
        for object_id in self.priority_object_ids:
            if (
                isinstance(object_id, bool)
                or not isinstance(object_id, int)
                or object_id <= 0
            ):
                raise ValueError("priority object IDs must be positive integers")

        if not isinstance(self.priority_object_keys, tuple):
            raise ValueError("priority_object_keys must be a tuple")
        if len(self.priority_object_keys) > _MAX_PRIORITY_OBJECT_KEYS:
            raise ValueError(
                f"at most {_MAX_PRIORITY_OBJECT_KEYS} priority object keys are allowed"
            )
        if len(set(self.priority_object_keys)) != len(self.priority_object_keys):
            raise ValueError("priority_object_keys must be unique")
        for object_key in self.priority_object_keys:
            if (
                not isinstance(object_key, str)
                or not object_key.strip()
                or len(object_key) > _MAX_PRIORITY_OBJECT_KEY_LENGTH
            ):
                raise ValueError(
                    "priority object keys must be non-empty strings of at most "
                    f"{_MAX_PRIORITY_OBJECT_KEY_LENGTH} characters"
                )

        if self.center_world_location is not None and not isinstance(
            self.center_world_location, WorldPoint
        ):
            raise ValueError("center_world_location must be WorldPoint or None")
        if self.radius_tiles is not None:
            _validate_positive_bounded_int(
                self.radius_tiles,
                "radius_tiles",
                _MAX_OBSERVATION_RADIUS_TILES,
            )
        if self.max_objects is not None:
            _validate_bounded_nonnegative_int(
                self.max_objects,
                "max_objects",
                _MAX_OBSERVATION_OBJECTS,
            )
        if self.max_projection_objects is not None:
            _validate_bounded_nonnegative_int(
                self.max_projection_objects,
                "max_projection_objects",
                _MAX_OBSERVATION_OBJECTS,
            )
            if (
                self.max_objects is not None
                and self.max_projection_objects > self.max_objects
            ):
                raise ValueError(
                    "max_projection_objects must not exceed max_objects"
                )
        if (
            not isinstance(self.purpose, str)
            or _OBSERVATION_PURPOSE.fullmatch(self.purpose) is None
            or len(self.purpose) > _MAX_OBSERVATION_PURPOSE_LENGTH
        ):
            raise ValueError(
                "purpose must be a lowercase identifier of at most "
                f"{_MAX_OBSERVATION_PURPOSE_LENGTH} characters"
            )


@dataclass(frozen=True, slots=True)
class TaskProgressSnapshot:
    label: str
    current: int
    total: int

    def __post_init__(self) -> None:
        _validate_nonempty_text(self.label, "label")
        _validate_nonnegative_int(self.current, "current")
        _validate_nonnegative_int(self.total, "total")
        if self.current > self.total:
            raise ValueError("current must not exceed total")


@dataclass(frozen=True, slots=True)
class TargetEvidence:
    key: str
    name: str
    object_id: int | None
    action: str | None
    source_tick: int
    geometry_frame_id: str | None
    point: ScreenPoint | None
    bounds: ScreenBounds | None
    world_location: WorldPoint | None = None
    distance: int | None = None

    def __post_init__(self) -> None:
        _validate_nonempty_text(self.key, "key")
        _validate_nonempty_text(self.name, "name")
        if self.object_id is not None:
            _validate_nonnegative_int(self.object_id, "object_id")
        if self.action is not None:
            _validate_nonempty_text(self.action, "action")
        _validate_nonnegative_int(self.source_tick, "source_tick")
        if self.geometry_frame_id is not None:
            _validate_nonempty_text(self.geometry_frame_id, "geometry_frame_id")
        if self.point is not None:
            _validate_screen_point(self.point)
        if self.bounds is not None:
            _validate_screen_bounds(self.bounds)
        if self.world_location is not None and not isinstance(
            self.world_location, WorldPoint
        ):
            raise ValueError("world_location must be WorldPoint or None")
        if self.distance is not None:
            _validate_nonnegative_int(self.distance, "distance")


@dataclass(frozen=True, slots=True)
class RouteCandidateRejectionEvidence:
    step_id: str
    rejection_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_nonempty_text(self.step_id, "step_id")
        if len(self.step_id) > 128:
            raise ValueError("step_id exceeds the bounded diagnostic text limit")
        if not isinstance(self.rejection_codes, tuple) or not self.rejection_codes:
            raise ValueError("rejection_codes must be a nonempty tuple")
        if len(self.rejection_codes) > _MAX_ROUTE_REJECTION_REASONS:
            raise ValueError("rejection_codes exceeds the bounded reason limit")
        if len(set(self.rejection_codes)) != len(self.rejection_codes):
            raise ValueError("rejection_codes must be unique")
        for code in self.rejection_codes:
            if not isinstance(code, str) or _REJECTION_CODE.fullmatch(code) is None:
                raise ValueError(
                    "rejection codes must be lowercase identifiers of at most 64 characters"
                )


@dataclass(frozen=True, slots=True)
class RouteDecisionEvidence:
    progress_tiles: float
    remaining_tiles: float
    lateral_deviation_tiles: float
    selected_step_id: str | None
    selected_location: WorldPoint | None
    requested_distance_tiles: float
    expected_progress_tiles: float
    actual_progress_tiles: float | None = None
    skipped_guidance_points: tuple[str, ...] = ()
    mandatory_next_step_id: str | None = None
    fallback_reason: str | None = None
    backtracking: bool = False
    zigzagging: bool = False
    projected_route_points: tuple[ScreenPoint, ...] = ()
    projected_route_labels: tuple[str, ...] = ()
    mandatory_route_points: tuple[ScreenPoint, ...] = ()
    skipped_route_points: tuple[ScreenPoint, ...] = ()
    selected_screen_point: ScreenPoint | None = None
    candidate_rejections: tuple[RouteCandidateRejectionEvidence, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "progress_tiles",
            "remaining_tiles",
            "lateral_deviation_tiles",
            "requested_distance_tiles",
            "expected_progress_tiles",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise ValueError(f"{field_name} must be a nonnegative number or None")
        if self.actual_progress_tiles is not None and (
            isinstance(self.actual_progress_tiles, bool)
            or not isinstance(self.actual_progress_tiles, (int, float))
            or not math.isfinite(float(self.actual_progress_tiles))
        ):
            raise ValueError("actual_progress_tiles must be finite numeric or None")
        if self.selected_step_id is not None:
            _validate_nonempty_text(self.selected_step_id, "selected_step_id")
        if self.selected_location is not None and not isinstance(
            self.selected_location, WorldPoint
        ):
            raise ValueError("selected_location must be WorldPoint or None")
        if not isinstance(self.skipped_guidance_points, tuple) or any(
            not isinstance(value, str) or not value
            for value in self.skipped_guidance_points
        ):
            raise ValueError("skipped_guidance_points must be a tuple of text")
        for field_name in ("mandatory_next_step_id", "fallback_reason"):
            value = getattr(self, field_name)
            if value is not None:
                _validate_nonempty_text(value, field_name)
        if not isinstance(self.backtracking, bool) or not isinstance(
            self.zigzagging, bool
        ):
            raise ValueError("route flags must be bool")
        for field_name in (
            "projected_route_points",
            "mandatory_route_points",
            "skipped_route_points",
        ):
            points = getattr(self, field_name)
            if not isinstance(points, tuple) or any(
                not isinstance(point, ScreenPoint) for point in points
            ):
                raise ValueError(f"{field_name} must be a tuple of ScreenPoint values")
            if len(points) > _MAX_TILE_PROJECTIONS:
                raise ValueError(f"{field_name} exceeds the bounded projection limit")
        if not isinstance(self.projected_route_labels, tuple) or any(
            not isinstance(value, str) or not value
            for value in self.projected_route_labels
        ):
            raise ValueError("projected_route_labels must be a tuple of text")
        if len(self.projected_route_labels) != len(self.projected_route_points):
            raise ValueError("projected route labels and points must have equal length")
        if self.selected_screen_point is not None:
            _validate_screen_point(self.selected_screen_point)
        if not isinstance(self.candidate_rejections, tuple) or any(
            not isinstance(value, RouteCandidateRejectionEvidence)
            for value in self.candidate_rejections
        ):
            raise ValueError(
                "candidate_rejections must be a tuple of RouteCandidateRejectionEvidence"
            )
        if len(self.candidate_rejections) > _MAX_TILE_PROJECTIONS:
            raise ValueError("candidate_rejections exceeds the bounded projection limit")
        rejection_steps = tuple(value.step_id for value in self.candidate_rejections)
        if len(set(rejection_steps)) != len(rejection_steps):
            raise ValueError("candidate_rejections step IDs must be unique")


@dataclass(frozen=True, slots=True)
class CameraDecisionEvidence:
    classification: str
    desired_region: ScreenBounds | None
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
    correction_attempt: int = 0
    correction_limit: int = 0
    cumulative_hold_millis: int = 0
    screen_correction_x_px: float = 0.0
    screen_correction_y_px: float = 0.0
    acquisition_state: CameraAcquisitionState = CameraAcquisitionState.IDLE
    episode_id: str | None = None
    locked_target_key: str | None = None
    locked_target_kind: str | None = None
    desired_yaw: int | None = None
    desired_yaw_min: int | None = None
    desired_yaw_max: int | None = None
    desired_pitch: int | None = None
    desired_pitch_min: int | None = None
    desired_pitch_max: int | None = None
    pitch_error_units: int | None = None
    pitch_valid: bool = False
    visible_area_ratio: float | None = None
    zoom_classification: str = "unavailable"
    zoom_required_but_unavailable: bool = False
    capability_max_hold_millis: int = 250
    response_sample_count: int = 0
    calibrated_yaw_units_per_millis: float | None = None
    calibrated_pitch_units_per_millis: float | None = None
    last_observed_yaw_delta: int | None = None
    last_observed_pitch_delta: int | None = None
    last_response_no_effect: bool = False
    pitch_limit_direction: str | None = None
    overshoot_proven: bool = False
    retained_reason: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "classification",
            "action",
            "route_direction_bias",
            "framing_context",
        ):
            value = getattr(self, field_name)
            _validate_nonempty_text(value, field_name)
            if len(value) > _MAX_CAMERA_CONTEXT_LENGTH:
                raise ValueError(f"{field_name} exceeds the bounded text limit")
        if self.desired_region is not None:
            _validate_screen_bounds(self.desired_region)
        if self.target_point is not None:
            _validate_screen_point(self.target_point)
        if self.source_tick is not None:
            _validate_nonnegative_int(self.source_tick, "source_tick")
        if self.geometry_frame_id is not None:
            _validate_nonempty_text(self.geometry_frame_id, "geometry_frame_id")
            if len(self.geometry_frame_id) > _MAX_CAMERA_GEOMETRY_FRAME_ID_LENGTH:
                raise ValueError("geometry_frame_id exceeds the bounded text limit")
        if self.target_bounds is not None:
            _validate_screen_bounds(self.target_bounds)
        if self.lookahead_bounds is not None:
            _validate_screen_bounds(self.lookahead_bounds)
        _validate_nonnegative_int(self.hold_millis, "hold_millis")
        if (
            isinstance(self.correction_distance_px, bool)
            or not isinstance(self.correction_distance_px, (int, float))
            or not math.isfinite(float(self.correction_distance_px))
            or self.correction_distance_px < 0
        ):
            raise ValueError("correction_distance_px must be nonnegative")
        if self.edge_clearance_px is not None and (
            isinstance(self.edge_clearance_px, bool)
            or not isinstance(self.edge_clearance_px, (int, float))
            or not math.isfinite(float(self.edge_clearance_px))
            or self.edge_clearance_px < -_MAX_CAMERA_MARGIN_PX
            or self.edge_clearance_px > _MAX_CAMERA_MARGIN_PX
        ):
            raise ValueError("edge_clearance_px must be a bounded signed number")
        _validate_nonnegative_int(
            self.required_edge_margin_px, "required_edge_margin_px"
        )
        if self.required_edge_margin_px > _MAX_CAMERA_MARGIN_PX:
            raise ValueError("required_edge_margin_px exceeds the bounded margin limit")
        if not isinstance(self.lookahead_points, tuple) or any(
            not isinstance(point, ScreenPoint) for point in self.lookahead_points
        ):
            raise ValueError("lookahead_points must be a tuple of ScreenPoint values")
        if len(self.lookahead_points) > _MAX_TILE_PROJECTIONS:
            raise ValueError("lookahead_points exceeds the bounded projection limit")
        for point in self.lookahead_points:
            _validate_screen_point(point)
        if self.yaw_error_units is not None:
            _validate_int(self.yaw_error_units, "yaw_error_units")
            if abs(self.yaw_error_units) > _MAX_CAMERA_YAW_ERROR_UNITS:
                raise ValueError("yaw_error_units exceeds the bounded camera range")
        for field_name in (
            "screen_correction_x_px",
            "screen_correction_y_px",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or abs(float(value)) > _MAX_CAMERA_MARGIN_PX
            ):
                raise ValueError(f"{field_name} must be a bounded signed number")
        for field_name in (
            "correction_attempt",
            "correction_limit",
            "cumulative_hold_millis",
        ):
            _validate_nonnegative_int(getattr(self, field_name), field_name)
        if self.correction_attempt > _MAX_CAMERA_CORRECTION_ATTEMPTS:
            raise ValueError("correction_attempt exceeds the bounded attempt limit")
        if self.correction_limit > _MAX_CAMERA_CORRECTION_ATTEMPTS:
            raise ValueError("correction_limit exceeds the bounded attempt limit")
        if self.correction_limit and self.correction_attempt > self.correction_limit:
            raise ValueError("correction_attempt must not exceed correction_limit")
        if self.cumulative_hold_millis > _MAX_CAMERA_CUMULATIVE_HOLD_MILLIS:
            raise ValueError("cumulative_hold_millis exceeds the bounded duration limit")
        if not isinstance(self.acquisition_state, CameraAcquisitionState):
            raise ValueError("acquisition_state must be CameraAcquisitionState")
        for field_name in (
            "episode_id",
            "locked_target_key",
            "locked_target_kind",
            "retained_reason",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _validate_nonempty_text(value, field_name)
                if len(value) > _MAX_CAMERA_GEOMETRY_FRAME_ID_LENGTH:
                    raise ValueError(f"{field_name} exceeds the bounded text limit")
        for field_name in (
            "desired_yaw",
            "desired_yaw_min",
            "desired_yaw_max",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _validate_nonnegative_int(value, field_name)
                if value >= 16_384:
                    raise ValueError(f"{field_name} must be a fixed-point camera yaw")
        for field_name in (
            "desired_pitch",
            "desired_pitch_min",
            "desired_pitch_max",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _validate_nonnegative_int(value, field_name)
        if (
            self.desired_pitch_min is not None
            and self.desired_pitch_max is not None
            and self.desired_pitch_min > self.desired_pitch_max
        ):
            raise ValueError("desired_pitch_min cannot exceed desired_pitch_max")
        if self.pitch_error_units is not None:
            _validate_int(self.pitch_error_units, "pitch_error_units")
        if not isinstance(self.pitch_valid, bool):
            raise ValueError("pitch_valid must be bool")
        if self.visible_area_ratio is not None and (
            isinstance(self.visible_area_ratio, bool)
            or not isinstance(self.visible_area_ratio, (int, float))
            or not math.isfinite(float(self.visible_area_ratio))
            or not 0.0 <= float(self.visible_area_ratio) <= 1.0
        ):
            raise ValueError("visible_area_ratio must be in [0, 1] or None")
        if self.zoom_classification not in {
            "unavailable",
            "too_far",
            "moderate",
            "too_close",
        }:
            raise ValueError("zoom_classification is unsupported")
        if not isinstance(self.zoom_required_but_unavailable, bool):
            raise ValueError("zoom_required_but_unavailable must be bool")
        _validate_nonnegative_int(
            self.capability_max_hold_millis,
            "capability_max_hold_millis",
        )
        if self.capability_max_hold_millis <= 0:
            raise ValueError("capability_max_hold_millis must be positive")
        _validate_nonnegative_int(self.response_sample_count, "response_sample_count")
        if self.response_sample_count > 64:
            raise ValueError("response_sample_count exceeds the bounded sample limit")
        for field_name in (
            "calibrated_yaw_units_per_millis",
            "calibrated_pitch_units_per_millis",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ValueError(f"{field_name} must be positive and finite or None")
        for field_name in (
            "last_observed_yaw_delta",
            "last_observed_pitch_delta",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _validate_int(value, field_name)
        for field_name in (
            "last_response_no_effect",
            "overshoot_proven",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be bool")
        if self.pitch_limit_direction not in {None, "up", "down"}:
            raise ValueError("pitch_limit_direction must be up, down, or None")


@dataclass(frozen=True, slots=True)
class TargetingDecisionEvidence:
    geometry_source: str
    shape_bounds: ScreenBounds
    inset_region: ScreenBounds
    candidate_points: tuple[ScreenPoint, ...]
    selected_point: ScreenPoint
    selected_score: float
    previous_points: tuple[ScreenPoint, ...]
    decision_id: str
    seed: int
    rejected_reasons: tuple[str, ...] = ()
    shape_polygon: tuple[ScreenPoint, ...] = ()

    def __post_init__(self) -> None:
        _validate_nonempty_text(self.geometry_source, "geometry_source")
        _validate_screen_bounds(self.shape_bounds)
        _validate_screen_bounds(self.inset_region)
        if not self.candidate_points:
            raise ValueError("candidate_points must be nonempty")
        for point in (*self.candidate_points, *self.previous_points, self.selected_point):
            _validate_screen_point(point)
        if self.selected_point not in self.candidate_points:
            raise ValueError("selected_point must be one of candidate_points")
        if not isinstance(self.selected_score, (int, float)):
            raise ValueError("selected_score must be numeric")
        _validate_nonempty_text(self.decision_id, "decision_id")
        _validate_nonnegative_int(self.seed, "seed")
        if not isinstance(self.rejected_reasons, tuple) or any(
            not isinstance(reason, str) or not reason for reason in self.rejected_reasons
        ):
            raise ValueError("rejected_reasons must be a tuple of text")
        if not isinstance(self.shape_polygon, tuple) or any(
            not isinstance(point, ScreenPoint) for point in self.shape_polygon
        ):
            raise ValueError("shape_polygon must be a tuple of ScreenPoint values")
        if len(self.shape_polygon) > 256:
            raise ValueError("shape_polygon exceeds its bounded diagnostic limit")


@dataclass(frozen=True, slots=True)
class TimingDecisionEvidence:
    decision_id: str
    seed: int
    pre_move_delay_seconds: float
    settle_delay_seconds: float
    pre_click_delay_seconds: float
    post_action_delay_seconds: float
    route_pause_seconds: float

    def __post_init__(self) -> None:
        _validate_nonempty_text(self.decision_id, "decision_id")
        _validate_nonnegative_int(self.seed, "seed")
        for field_name in (
            "pre_move_delay_seconds",
            "settle_delay_seconds",
            "pre_click_delay_seconds",
            "post_action_delay_seconds",
            "route_pause_seconds",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{field_name} must be nonnegative")


@dataclass(frozen=True, slots=True)
class RejectedCandidateEvidence:
    target: TargetEvidence
    rejection_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.target, TargetEvidence):
            raise ValueError("target must be TargetEvidence")
        if not isinstance(self.rejection_codes, tuple) or not self.rejection_codes:
            raise ValueError("rejection_codes must be a nonempty tuple")
        if len(set(self.rejection_codes)) != len(self.rejection_codes):
            raise ValueError("rejection_codes must be unique")
        for code in self.rejection_codes:
            if not isinstance(code, str) or _REJECTION_CODE.fullmatch(code) is None:
                raise ValueError(
                    "rejection codes must be lowercase identifiers of at most 64 characters"
                )


@dataclass(frozen=True, slots=True)
class DecisionEvidence:
    selected: TargetEvidence | None = None
    eligible: tuple[TargetEvidence, ...] = ()
    rejected: tuple[RejectedCandidateEvidence, ...] = ()
    route: RouteDecisionEvidence | None = None
    camera: CameraDecisionEvidence | None = None
    targeting: TargetingDecisionEvidence | None = None
    timing: TimingDecisionEvidence | None = None

    def __post_init__(self) -> None:
        if self.selected is not None and not isinstance(self.selected, TargetEvidence):
            raise ValueError("selected must be TargetEvidence or None")
        if not isinstance(self.eligible, tuple) or any(
            not isinstance(target, TargetEvidence) for target in self.eligible
        ):
            raise ValueError("eligible must be a tuple of TargetEvidence values")
        if not isinstance(self.rejected, tuple) or any(
            not isinstance(candidate, RejectedCandidateEvidence)
            for candidate in self.rejected
        ):
            raise ValueError(
                "rejected must be a tuple of RejectedCandidateEvidence values"
            )

        eligible_keys = tuple(target.key for target in self.eligible)
        rejected_keys = tuple(candidate.target.key for candidate in self.rejected)
        if len(set(eligible_keys)) != len(eligible_keys):
            raise ValueError("eligible target keys must be unique")
        if len(set(rejected_keys)) != len(rejected_keys):
            raise ValueError("rejected target keys must be unique")
        if set(eligible_keys).intersection(rejected_keys):
            raise ValueError("a target cannot be both eligible and rejected")
        if self.selected is not None and self.selected not in self.eligible:
            raise ValueError("selected target must be one of the eligible targets")
        for field_name, expected_type in (
            ("route", RouteDecisionEvidence),
            ("camera", CameraDecisionEvidence),
            ("targeting", TargetingDecisionEvidence),
            ("timing", TimingDecisionEvidence),
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, expected_type):
                raise ValueError(
                    f"{field_name} must be {expected_type.__name__} or None"
                )


@dataclass(frozen=True, slots=True)
class Decision:
    state: str
    reason: str
    action: Action
    evidence: DecisionEvidence = field(default_factory=DecisionEvidence)

    def __post_init__(self) -> None:
        _validate_nonempty_text(self.state, "state")
        _validate_nonempty_text(self.reason, "reason")
        if not isinstance(self.evidence, DecisionEvidence):
            raise ValueError("evidence must be DecisionEvidence")


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_id: str
    status: TaskStatus
    state: str
    blocker: str | None = None
    definition_id: str | None = None
    profile_id: str | None = None
    progress: TaskProgressSnapshot | None = None
    route_step: str | None = None
    route_progress: TaskProgressSnapshot | None = None
    cycle_progress: TaskProgressSnapshot | None = None

    def __post_init__(self) -> None:
        _validate_nonempty_text(self.task_id, "task_id")
        if not isinstance(self.status, TaskStatus):
            raise ValueError("status must be a TaskStatus")
        _validate_nonempty_text(self.state, "state")
        if self.blocker is not None:
            _validate_nonempty_text(self.blocker, "blocker")
        if self.definition_id is not None:
            _validate_nonempty_text(self.definition_id, "definition_id")
        if self.profile_id is not None:
            _validate_nonempty_text(self.profile_id, "profile_id")
        if self.progress is not None and not isinstance(
            self.progress, TaskProgressSnapshot
        ):
            raise ValueError("progress must be TaskProgressSnapshot or None")
        if self.route_step is not None:
            _validate_nonempty_text(self.route_step, "route_step")
        for field_name in ("route_progress", "cycle_progress"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, TaskProgressSnapshot):
                raise ValueError(
                    f"{field_name} must be TaskProgressSnapshot or None"
                )
        if self.route_step is not None and self.route_progress is None:
            raise ValueError("route_step requires route_progress")


@runtime_checkable
class Task(Protocol):
    def observation_request(self) -> ObservationRequest:
        ...

    def decide(self, observation: Observation) -> Decision:
        ...

    def apply_verification(self, result: "VerificationResult") -> None:
        ...

    def discard_pending_action(
        self, reason: str, *, target_invalidated: bool = True
    ) -> None:
        """Forget an action that was proven unsent so fresh evidence can replan."""

        ...

    def snapshot(self) -> TaskSnapshot:
        ...


def _validate_nonempty_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _validate_nonnegative_int(value: object, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _validate_bounded_nonnegative_int(
    value: object,
    field_name: str,
    maximum: int,
) -> None:
    _validate_nonnegative_int(value, field_name)
    if value > maximum:
        raise ValueError(f"{field_name} must not exceed {maximum}")


def _validate_positive_bounded_int(
    value: object,
    field_name: str,
    maximum: int,
) -> None:
    _validate_bounded_nonnegative_int(value, field_name, maximum)
    if value == 0:
        raise ValueError(f"{field_name} must be positive")


def _validate_int(value: object, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")


def _validate_screen_point(value: object) -> None:
    if not isinstance(value, ScreenPoint):
        raise ValueError("point must be ScreenPoint or None")
    _validate_int(value.x, "point.x")
    _validate_int(value.y, "point.y")


def _validate_screen_bounds(value: object) -> None:
    if not isinstance(value, ScreenBounds):
        raise ValueError("bounds must be ScreenBounds or None")
    _validate_int(value.x, "bounds.x")
    _validate_int(value.y, "bounds.y")
    if (
        not isinstance(value.width, int)
        or isinstance(value.width, bool)
        or value.width <= 0
        or not isinstance(value.height, int)
        or isinstance(value.height, bool)
        or value.height <= 0
    ):
        raise ValueError("bounds width and height must be positive integers")

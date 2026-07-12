from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .model import Action, Observation, ScreenBounds, ScreenPoint, WorldPoint

if TYPE_CHECKING:
    from .verification import VerificationResult


_MAX_TILE_PROJECTIONS = 16
_REJECTION_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class TaskStatus(str, Enum):
    RUNNING = "running"
    COMPLETE = "complete"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ObservationRequest:
    tile_projections: tuple[tuple[str, WorldPoint], ...] = ()

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

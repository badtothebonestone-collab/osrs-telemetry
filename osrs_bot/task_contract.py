from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .model import Action, Observation, WorldPoint

if TYPE_CHECKING:
    from .verification import VerificationResult


_MAX_TILE_PROJECTIONS = 16


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
class Decision:
    state: str
    reason: str
    action: Action

    def __post_init__(self) -> None:
        _validate_nonempty_text(self.state, "state")
        _validate_nonempty_text(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_id: str
    status: TaskStatus
    state: str
    blocker: str | None = None

    def __post_init__(self) -> None:
        _validate_nonempty_text(self.task_id, "task_id")
        if not isinstance(self.status, TaskStatus):
            raise ValueError("status must be a TaskStatus")
        _validate_nonempty_text(self.state, "state")
        if self.blocker is not None:
            _validate_nonempty_text(self.blocker, "blocker")


@runtime_checkable
class Task(Protocol):
    def observation_request(self) -> ObservationRequest:
        ...

    def decide(self, observation: Observation) -> Decision:
        ...

    def apply_verification(self, result: "VerificationResult") -> None:
        ...

    def snapshot(self) -> TaskSnapshot:
        ...


def _validate_nonempty_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")

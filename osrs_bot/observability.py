from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any


TIMING_EVIDENCE_SCHEMA = "engine_phase_timing.v1"
OBSERVABILITY_EVIDENCE_SCHEMA = "engine_observability.v1"

# Diagnostics are intentionally bounded.  A single phase sample cannot claim
# more than one day, and aggregate evidence cannot grow without limit.
MAX_DURATION_MILLIS = 86_400_000
MAX_AGGREGATE_COUNT = 1_000_000
MAX_TOTAL_DURATION_MILLIS = MAX_DURATION_MILLIS * MAX_AGGREGATE_COUNT


class WaitState(str, Enum):
    WAITING_FOR_NEXT_SCENE_UPDATE = "WAITING_FOR_NEXT_SCENE_UPDATE"
    WAITING_FOR_SOURCE_COHERENCE = "WAITING_FOR_SOURCE_COHERENCE"
    ENDPOINT_BACKPRESSURE = "ENDPOINT_BACKPRESSURE"
    INPUT_TRANSACTION_BUSY = "INPUT_TRANSACTION_BUSY"
    CURSOR_FEEDBACK_SETTLING = "CURSOR_FEEDBACK_SETTLING"
    ARDUINO_HEALTH_STALE = "ARDUINO_HEALTH_STALE"
    ARDUINO_COMMAND_FAILED = "ARDUINO_COMMAND_FAILED"
    SENSOR_STALE = "SENSOR_STALE"
    PRESENTATION_FRAME_STALE = "PRESENTATION_FRAME_STALE"


class TimingPhase(str, Enum):
    OBSERVATION_REQUEST_FETCH = "observation_request_fetch"
    ENDPOINT_BACKPRESSURE_WAIT = "endpoint_backpressure_wait"
    SOURCE_COHERENCE_FRESHNESS_WAIT = "source_coherence_freshness_wait"
    TASK_DECISION = "task_decision"
    SAFETY_GATE_EVALUATION = "safety_gate_evaluation"
    INPUT_LEASE_ACQUISITION = "input_lease_acquisition"
    ARDUINO_CONNECT_NEGOTIATE_ARM = "arduino_connect_negotiate_arm"
    POINTER_PLANNING_FEEDBACK_SETTLEMENT = (
        "pointer_planning_feedback_settlement"
    )
    SERIAL_WRITE_ACKNOWLEDGEMENT = "serial_write_acknowledgement"
    POST_ACTION_FRESH_OBSERVATION_WAIT = (
        "post_action_fresh_observation_wait"
    )
    SEMANTIC_OR_CAMERA_VERIFICATION = "semantic_or_camera_verification"
    FINAL_CLEANUP = "final_cleanup"


def safe_elapsed_millis(
    started_monotonic: object,
    finished_monotonic: object | None = None,
) -> int:
    """Return sanitized elapsed milliseconds suitable for public evidence.

    Invalid or non-finite inputs become zero.  Negative elapsed time is
    clamped to zero and excessive elapsed time is clamped to the documented
    per-sample bound.  Inputs are never converted to text or retained.
    """

    if finished_monotonic is None:
        finished_monotonic = time.monotonic()
    if not _is_finite_number(started_monotonic) or not _is_finite_number(
        finished_monotonic
    ):
        return 0
    elapsed_seconds = finished_monotonic - started_monotonic
    if elapsed_seconds <= 0:
        return 0
    maximum_seconds = MAX_DURATION_MILLIS / 1_000
    if elapsed_seconds >= maximum_seconds:
        return MAX_DURATION_MILLIS
    return int(elapsed_seconds * 1_000)


@dataclass(frozen=True, slots=True)
class DurationAggregate:
    count: int = 0
    total_millis: int = 0
    max_millis: int = 0
    last_millis: int = 0

    def __post_init__(self) -> None:
        _require_bounded_integer(
            "count",
            self.count,
            maximum=MAX_AGGREGATE_COUNT,
        )
        _require_bounded_integer(
            "total_millis",
            self.total_millis,
            maximum=MAX_TOTAL_DURATION_MILLIS,
        )
        _require_bounded_integer(
            "max_millis",
            self.max_millis,
            maximum=MAX_DURATION_MILLIS,
        )
        _require_bounded_integer(
            "last_millis",
            self.last_millis,
            maximum=MAX_DURATION_MILLIS,
        )
        if self.count == 0:
            if any(
                value != 0
                for value in (
                    self.total_millis,
                    self.max_millis,
                    self.last_millis,
                )
            ):
                raise ValueError("an empty aggregate must contain only zeroes")
            return
        if self.max_millis > self.total_millis:
            raise ValueError("max_millis cannot exceed total_millis")
        if self.last_millis > self.max_millis:
            raise ValueError("last_millis cannot exceed max_millis")
        if self.total_millis > self.count * self.max_millis:
            raise ValueError("total_millis is inconsistent with count and max_millis")

    def add(self, duration_millis: int) -> "DurationAggregate":
        _require_bounded_integer(
            "duration_millis",
            duration_millis,
            maximum=MAX_DURATION_MILLIS,
        )
        if self.count == MAX_AGGREGATE_COUNT:
            raise ValueError("aggregate count would exceed the public bound")
        total_millis = self.total_millis + duration_millis
        if total_millis > MAX_TOTAL_DURATION_MILLIS:
            raise ValueError("aggregate total would exceed the public bound")
        return DurationAggregate(
            count=self.count + 1,
            total_millis=total_millis,
            max_millis=max(self.max_millis, duration_millis),
            last_millis=duration_millis,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "count": self.count,
            "totalMillis": self.total_millis,
            "maxMillis": self.max_millis,
            "lastMillis": self.last_millis,
        }


@dataclass(frozen=True, slots=True)
class TimingEvidence:
    aggregates: tuple[tuple[TimingPhase, DurationAggregate], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.aggregates, tuple):
            raise TypeError("aggregates must be an immutable tuple")
        phase_positions = {phase: index for index, phase in enumerate(TimingPhase)}
        seen: set[TimingPhase] = set()
        previous_position = -1
        for entry in self.aggregates:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise TypeError("each aggregate entry must be a two-item tuple")
            phase, aggregate = entry
            if not isinstance(phase, TimingPhase):
                raise TypeError("aggregate phase must be TimingPhase")
            if not isinstance(aggregate, DurationAggregate):
                raise TypeError("aggregate value must be DurationAggregate")
            if phase in seen:
                raise ValueError("aggregate phases must be unique")
            position = phase_positions[phase]
            if position <= previous_position:
                raise ValueError("aggregates must follow TimingPhase order")
            seen.add(phase)
            previous_position = position

    def record(
        self,
        phase: TimingPhase,
        duration_millis: int,
    ) -> "TimingEvidence":
        if not isinstance(phase, TimingPhase):
            raise TypeError("phase must be TimingPhase")
        updated = {
            existing_phase: aggregate
            for existing_phase, aggregate in self.aggregates
        }
        updated[phase] = updated.get(phase, DurationAggregate()).add(
            duration_millis
        )
        return TimingEvidence(_ordered_aggregates(updated))

    def merge(self, other: "TimingEvidence") -> "TimingEvidence":
        if not isinstance(other, TimingEvidence):
            raise TypeError("other must be TimingEvidence")
        combined = {
            phase: aggregate for phase, aggregate in self.aggregates
        }
        for phase, later in other.aggregates:
            earlier = combined.get(phase)
            if earlier is None or earlier.count == 0:
                combined[phase] = later
                continue
            if later.count == 0:
                continue
            count = earlier.count + later.count
            total_millis = earlier.total_millis + later.total_millis
            if count > MAX_AGGREGATE_COUNT:
                raise ValueError("merged aggregate count exceeds the public bound")
            if total_millis > MAX_TOTAL_DURATION_MILLIS:
                raise ValueError("merged aggregate total exceeds the public bound")
            combined[phase] = DurationAggregate(
                count=count,
                total_millis=total_millis,
                max_millis=max(earlier.max_millis, later.max_millis),
                last_millis=later.last_millis,
            )
        return TimingEvidence(_ordered_aggregates(combined))

    def for_phase(self, phase: TimingPhase) -> DurationAggregate | None:
        if not isinstance(phase, TimingPhase):
            raise TypeError("phase must be TimingPhase")
        for existing_phase, aggregate in self.aggregates:
            if existing_phase is phase:
                return aggregate
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": TIMING_EVIDENCE_SCHEMA,
            "phases": [
                {"phase": phase.value, **aggregate.to_dict()}
                for phase, aggregate in self.aggregates
            ],
        }


@dataclass(frozen=True, slots=True)
class ObservabilityEvidence:
    timing: TimingEvidence = TimingEvidence()
    wait_state: WaitState | None = None
    wait_elapsed_millis: int = 0
    observed_wait_states: tuple[WaitState, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.timing, TimingEvidence):
            raise TypeError("timing must be TimingEvidence")
        if self.wait_state is not None and not isinstance(
            self.wait_state, WaitState
        ):
            raise TypeError("wait_state must be WaitState or None")
        _require_bounded_integer(
            "wait_elapsed_millis",
            self.wait_elapsed_millis,
            maximum=MAX_DURATION_MILLIS,
        )
        if self.wait_state is None and self.wait_elapsed_millis != 0:
            raise ValueError("wait_elapsed_millis requires an active wait_state")
        if not isinstance(self.observed_wait_states, tuple):
            raise TypeError("observed_wait_states must be an immutable tuple")
        if not all(
            isinstance(state, WaitState) for state in self.observed_wait_states
        ):
            raise TypeError("observed_wait_states must contain only WaitState values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": OBSERVABILITY_EVIDENCE_SCHEMA,
            "timing": self.timing.to_dict(),
            "waitState": (
                None if self.wait_state is None else self.wait_state.value
            ),
            "waitElapsedMillis": self.wait_elapsed_millis,
            "observedWaitStates": [
                state.value for state in self.observed_wait_states
            ],
        }


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _require_bounded_integer(
    name: str,
    value: object,
    *,
    maximum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} must be between 0 and {maximum}")


def _ordered_aggregates(
    aggregates: dict[TimingPhase, DurationAggregate],
) -> tuple[tuple[TimingPhase, DurationAggregate], ...]:
    return tuple(
        (phase, aggregates[phase])
        for phase in TimingPhase
        if phase in aggregates
    )

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .model import Observation, Verification, VerificationKind


class VerificationStatus(str, Enum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    reason: str

    @property
    def pending(self) -> bool:
        return self.status is VerificationStatus.PENDING

    @property
    def passed(self) -> bool:
        return self.status is VerificationStatus.PASS

    @property
    def failed(self) -> bool:
        return self.status is VerificationStatus.FAIL


@dataclass(frozen=True)
class Verifier:
    max_observation_age_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.max_observation_age_seconds < 0:
            raise ValueError("max_observation_age_seconds must be non-negative")

    def evaluate(
        self, specification: Verification, observation: Observation
    ) -> VerificationResult:
        if specification.kind is VerificationKind.NONE:
            return _pass("verification_not_required")

        invalid_reason = _invalid_specification_reason(specification)
        if invalid_reason is not None:
            return _fail(invalid_reason)

        if observation.session_id != specification.source_session_id:
            return _fail("session_changed")

        if observation.tick <= specification.before_tick:
            return _pending("awaiting_later_observation")
        if observation.tick > specification.deadline_tick:
            return _fail("deadline_exceeded")
        if not self._observation_usable(observation):
            if observation.tick == specification.deadline_tick:
                return _fail("deadline_exceeded")
            return _pending("observation_not_usable")
        if observation.widgets.bank_pin_open:
            return _fail("bank_pin_open")

        outcome = _successful_outcome(specification, observation)
        if outcome is not None:
            return _pass(outcome)
        if observation.tick == specification.deadline_tick:
            return _fail("deadline_exceeded")
        return _pending("condition_not_met")

    def _observation_usable(self, observation: Observation) -> bool:
        if observation.status != "PASS":
            return False
        if not observation.fresh or not observation.cache_wall_clock_fresh:
            return False
        if not observation.loaded_scene:
            return False
        try:
            age = observation.age_seconds
            return observation.timestamp_not_future and age <= self.max_observation_age_seconds
        except (AttributeError, TypeError, ValueError, OverflowError):
            return False


def _invalid_specification_reason(specification: Verification) -> str | None:
    if not isinstance(specification.kind, VerificationKind):
        return "unsupported_verification"
    if specification.deadline_tick <= specification.before_tick:
        return "invalid_deadline"
    if not specification.source_session_id:
        return "invalid_session_baseline"
    if specification.kind in {
        VerificationKind.LOG_GAINED,
        VerificationKind.LOGS_DEPOSITED,
    }:
        if specification.before_log_count is None or specification.before_log_count < 0:
            return "invalid_log_baseline"
        if (specification.kind is VerificationKind.LOGS_DEPOSITED
                and specification.before_log_count == 0):
            return "invalid_log_baseline"
    if specification.kind is VerificationKind.MOVED_CLOSER:
        if (specification.before_location is None
                or specification.target_location is None
                or specification.target_radius is None
                or specification.target_radius < 0):
            return "invalid_movement_baseline"
    if (specification.kind is VerificationKind.PLANE_CHANGED
            and (specification.expected_plane is None
                 or specification.before_location is None
                 or specification.before_location.plane == specification.expected_plane)):
        return "invalid_plane_baseline"
    if (specification.kind is VerificationKind.ROUTE_TRANSITION_READY
            and (specification.expected_plane is None
                 or specification.before_location is None
                 or specification.before_location.plane == specification.expected_plane)):
        return "invalid_plane_baseline"
    if specification.kind in {
        VerificationKind.BANK_OPEN,
        VerificationKind.LOGS_DEPOSITED,
        VerificationKind.BANK_CLOSED,
    } and specification.expected_plane is None:
        return "invalid_plane_baseline"
    return None


def _successful_outcome(
    specification: Verification, observation: Observation
) -> str | None:
    if specification.kind is VerificationKind.LOG_GAINED:
        if (
            observation.inventory.known
            and observation.inventory.log_count > specification.before_log_count
        ):
            return "log_gained"
        return None

    if specification.kind is VerificationKind.MOVED_CLOSER:
        if observation.location is None:
            return None
        current_distance = observation.location.distance_to(specification.target_location)
        if current_distance <= specification.target_radius:
            return "arrived"
        return None

    if specification.kind is VerificationKind.PLANE_CHANGED:
        return ("plane_changed"
                if observation.plane == specification.expected_plane else None)

    if specification.kind is VerificationKind.ROUTE_TRANSITION_READY:
        if observation.plane == specification.expected_plane:
            return "plane_changed"
        widgets = observation.widgets
        direction = (
            "up"
            if specification.expected_plane > specification.before_location.plane
            else "down"
        )
        if (
            widgets.dialogue_active
            and widgets.dialogue_type == "options"
            and "climb" in widgets.dialogue_prompt.lower()
            and any(
                option.visible and f"climb {direction}" in option.text.lower()
                for option in widgets.dialogue_options
            )
        ):
            return "dialogue_open"
        return None

    if specification.kind is VerificationKind.BANK_OPEN:
        if (
            observation.plane == specification.expected_plane
            and observation.widgets.bank_known
            and observation.widgets.bank_open
            and observation.widgets.bank_readable
        ):
            return "bank_open"
        return None

    if specification.kind is VerificationKind.LOGS_DEPOSITED:
        if (
            observation.plane == specification.expected_plane
            and observation.widgets.bank_known
            and observation.widgets.bank_open
            and observation.widgets.bank_readable
            and observation.inventory.known
            and observation.inventory.log_count == 0
        ):
            return "logs_deposited"
        return None

    if specification.kind is VerificationKind.BANK_CLOSED:
        return (
            "bank_closed"
            if observation.plane == specification.expected_plane
            and observation.widgets.bank_known
            and not observation.widgets.bank_open
            else None
        )

    return None


def _pending(reason: str) -> VerificationResult:
    return VerificationResult(VerificationStatus.PENDING, reason)


def _pass(reason: str) -> VerificationResult:
    return VerificationResult(VerificationStatus.PASS, reason)


def _fail(reason: str) -> VerificationResult:
    return VerificationResult(VerificationStatus.FAIL, reason)

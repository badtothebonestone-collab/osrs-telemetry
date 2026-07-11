from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .model import Observation, VerificationKind, VerificationSpec, WorldPoint


class VerificationStatus(str, Enum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"


class OutcomeKind(str, Enum):
    ITEM_QUANTITY_INCREASED = "item_quantity_increased"
    ITEM_QUANTITY_EQUALS = "item_quantity_equals"
    MOVED_CLOSER = "moved_closer"
    ARRIVED = "arrived"
    PLANE_CHANGED = "plane_changed"
    INTERFACE_OPENED = "interface_opened"
    INTERFACE_CLOSED = "interface_closed"
    DIALOGUE_OPTION_APPEARED = "dialogue_option_appeared"


@dataclass(frozen=True)
class Outcome:
    kind: OutcomeKind
    observed_tick: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OutcomeKind):
            raise ValueError("kind must be an OutcomeKind")
        if not _is_nonnegative_integer(self.observed_tick):
            raise ValueError("observed_tick must be a non-negative integer")


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    reason: str
    outcome: Outcome | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, VerificationStatus):
            raise ValueError("status must be a VerificationStatus")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        if self.status is VerificationStatus.PASS and not isinstance(
            self.outcome, Outcome
        ):
            raise ValueError("PASS requires a typed Outcome")
        if self.status is not VerificationStatus.PASS and self.outcome is not None:
            raise ValueError("only PASS may carry an Outcome")

    @property
    def pending(self) -> bool:
        return self.status is VerificationStatus.PENDING

    @property
    def passed(self) -> bool:
        return self.status is VerificationStatus.PASS and self.outcome is not None

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
        self, specification: VerificationSpec, observation: Observation
    ) -> VerificationResult:
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


def _invalid_specification_reason(specification: VerificationSpec) -> str | None:
    if not isinstance(specification, VerificationSpec):
        return "invalid_verification_specification"
    if not isinstance(specification.kind, VerificationKind):
        return "unsupported_verification"
    if (
        not _is_integer(specification.before_tick)
        or not _is_integer(specification.deadline_tick)
        or specification.deadline_tick <= specification.before_tick
    ):
        return "invalid_deadline"
    if not isinstance(specification.source_session_id, str) or not specification.source_session_id:
        return "invalid_session_baseline"

    if specification.kind is VerificationKind.ITEM_QUANTITY_INCREASED:
        if not _is_positive_integer(specification.item_id):
            return "invalid_item_id"
        if not _is_nonnegative_integer(specification.before_quantity):
            return "invalid_quantity_baseline"

    if specification.kind is VerificationKind.ITEM_QUANTITY_EQUALS:
        if not _is_positive_integer(specification.item_id):
            return "invalid_item_id"
        if not _is_nonnegative_integer(specification.expected_quantity):
            return "invalid_expected_quantity"
        if specification.interface_name not in {None, "bank"}:
            return "unsupported_interface"
        if (
            specification.interface_name == "bank"
            and not _is_nonnegative_integer(specification.expected_plane)
        ):
            return "invalid_interface_plane"

    if specification.kind is VerificationKind.MOVED_CLOSER:
        if (
            not isinstance(specification.before_location, WorldPoint)
            or not isinstance(specification.target_location, WorldPoint)
            or not _is_nonnegative_integer(specification.target_radius)
        ):
            return "invalid_movement_baseline"

    if specification.kind in {
        VerificationKind.PLANE_CHANGED,
        VerificationKind.ROUTE_TRANSITION,
    }:
        if (
            not isinstance(specification.before_location, WorldPoint)
            or not _is_nonnegative_integer(specification.expected_plane)
            or specification.before_location.plane == specification.expected_plane
        ):
            return "invalid_plane_baseline"
        if specification.kind is VerificationKind.ROUTE_TRANSITION and (
            not isinstance(specification.dialogue_prompt_contains, str)
            or not specification.dialogue_prompt_contains.strip()
            or not isinstance(specification.dialogue_option_contains, str)
            or not specification.dialogue_option_contains.strip()
        ):
            return "invalid_dialogue_expectation"

    if specification.kind in {
        VerificationKind.INTERFACE_OPENED,
        VerificationKind.INTERFACE_CLOSED,
    }:
        if specification.interface_name != "bank":
            return "unsupported_interface"
        if not _is_nonnegative_integer(specification.expected_plane):
            return "invalid_interface_plane"

    return None


def _successful_outcome(
    specification: VerificationSpec, observation: Observation
) -> Outcome | None:
    if specification.kind is VerificationKind.ITEM_QUANTITY_INCREASED:
        if (
            observation.inventory.known
            and observation.inventory.quantity(specification.item_id)
            > specification.before_quantity
        ):
            return Outcome(OutcomeKind.ITEM_QUANTITY_INCREASED, observation.tick)
        return None

    if specification.kind is VerificationKind.ITEM_QUANTITY_EQUALS:
        if (
            not observation.inventory.known
            or observation.inventory.quantity(specification.item_id)
            != specification.expected_quantity
        ):
            return None
        if specification.interface_name == "bank" and not (
            observation.plane == specification.expected_plane
            and observation.widgets.bank_known
            and observation.widgets.bank_open
            and observation.widgets.bank_readable
        ):
            return None
        return Outcome(OutcomeKind.ITEM_QUANTITY_EQUALS, observation.tick)

    if specification.kind is VerificationKind.MOVED_CLOSER:
        if observation.location is None:
            return None
        current_distance = observation.location.distance_to(specification.target_location)
        if current_distance <= specification.target_radius:
            return Outcome(OutcomeKind.ARRIVED, observation.tick)
        before_distance = specification.before_location.distance_to(
            specification.target_location
        )
        if current_distance < before_distance:
            return Outcome(OutcomeKind.MOVED_CLOSER, observation.tick)
        return None

    if specification.kind is VerificationKind.PLANE_CHANGED:
        if observation.plane == specification.expected_plane:
            return Outcome(OutcomeKind.PLANE_CHANGED, observation.tick)
        return None

    if specification.kind is VerificationKind.ROUTE_TRANSITION:
        if observation.plane == specification.expected_plane:
            return Outcome(OutcomeKind.PLANE_CHANGED, observation.tick)
        widgets = observation.widgets
        if (
            widgets.dialogue_active
            and widgets.dialogue_type == "options"
            and specification.dialogue_prompt_contains.lower()
            in widgets.dialogue_prompt.lower()
            and any(
                option.visible
                and specification.dialogue_option_contains.lower()
                in option.text.lower()
                for option in widgets.dialogue_options
            )
        ):
            return Outcome(OutcomeKind.DIALOGUE_OPTION_APPEARED, observation.tick)
        return None

    if specification.kind is VerificationKind.INTERFACE_OPENED:
        if (
            specification.interface_name == "bank"
            and observation.plane == specification.expected_plane
            and observation.widgets.bank_known
            and observation.widgets.bank_open
            and observation.widgets.bank_readable
        ):
            return Outcome(OutcomeKind.INTERFACE_OPENED, observation.tick)
        return None

    if specification.kind is VerificationKind.INTERFACE_CLOSED:
        if (
            specification.interface_name == "bank"
            and observation.plane == specification.expected_plane
            and observation.widgets.bank_known
            and not observation.widgets.bank_open
        ):
            return Outcome(OutcomeKind.INTERFACE_CLOSED, observation.tick)
        return None

    return None


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_positive_integer(value: object) -> bool:
    return _is_integer(value) and value > 0


def _is_nonnegative_integer(value: object) -> bool:
    return _is_integer(value) and value >= 0


def _pending(reason: str) -> VerificationResult:
    return VerificationResult(VerificationStatus.PENDING, reason)


def _pass(outcome: Outcome) -> VerificationResult:
    return VerificationResult(VerificationStatus.PASS, outcome.kind.value, outcome)


def _fail(reason: str) -> VerificationResult:
    return VerificationResult(VerificationStatus.FAIL, reason)

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .model import (
    BANK_INTERFACE_NAME,
    CAMERA_YAW_UNITS,
    Observation,
    VerificationKind,
    VerificationSpec,
    WorldPoint,
)


MAX_CAMERA_ZOOM_STEP = 3


class VerificationStatus(str, Enum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"


class VerificationFailureKind(str, Enum):
    INVALID_SPECIFICATION = "invalid_specification"
    SESSION_CHANGED = "session_changed"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    OBSERVATION_UNUSABLE_AT_DEADLINE = "observation_unusable_at_deadline"
    CONDITION_UNMET_AT_DEADLINE = "condition_unmet_at_deadline"
    ITEM_QUANTITY_UNCHANGED_AT_DEADLINE = (
        "item_quantity_unchanged_at_deadline"
    )
    BANK_PIN_OPEN = "bank_pin_open"
    CAMERA_IDENTITY_CHANGED = "camera_identity_changed"
    CAMERA_POSE_CHANGED_DURING_ZOOM = "camera_pose_changed_during_zoom"
    CAMERA_UI_STATE_CHANGED = "camera_ui_state_changed"
    CAMERA_ZOOM_DIRECTION_CONTRADICTED = (
        "camera_zoom_direction_contradicted"
    )
    CAMERA_ZOOM_UNCHANGED_AT_DEADLINE = (
        "camera_zoom_unchanged_at_deadline"
    )
    CAMERA_ZOOM_EVIDENCE_UNAVAILABLE_AT_DEADLINE = (
        "camera_zoom_evidence_unavailable_at_deadline"
    )
    RUNTIME_FAILURE = "runtime_failure"


class OutcomeKind(str, Enum):
    ITEM_QUANTITY_INCREASED = "item_quantity_increased"
    ITEM_QUANTITY_EQUALS = "item_quantity_equals"
    MOVED_CLOSER = "moved_closer"
    ARRIVED = "arrived"
    PLANE_CHANGED = "plane_changed"
    INTERFACE_OPENED = "interface_opened"
    INTERFACE_CLOSED = "interface_closed"
    DIALOGUE_OPTION_APPEARED = "dialogue_option_appeared"
    CAMERA_POSE_CHANGED = "camera_pose_changed"
    CAMERA_ZOOM_CHANGED = "camera_zoom_changed"


@dataclass(frozen=True, slots=True)
class CameraPoseResult:
    camera_key: str
    before_yaw: int | None
    after_yaw: int | None
    yaw_delta: int | None
    before_pitch: int | None
    after_pitch: int | None
    pitch_delta: int | None
    before_geometry_frame_id: str
    after_geometry_frame_id: str

    def __post_init__(self) -> None:
        if self.camera_key not in {"left", "right", "up", "down"}:
            raise ValueError("camera_key must be a supported camera direction")
        for field_name in ("before_yaw", "after_yaw"):
            value = getattr(self, field_name)
            if value is not None and (
                not _is_integer(value) or not 0 <= value < CAMERA_YAW_UNITS
            ):
                raise ValueError(f"{field_name} must be a valid camera yaw or None")
        if self.yaw_delta is not None and (
            not _is_integer(self.yaw_delta)
            or not -CAMERA_YAW_UNITS // 2
            <= self.yaw_delta
            <= CAMERA_YAW_UNITS // 2
        ):
            raise ValueError("yaw_delta must be a bounded signed camera delta")
        for field_name in ("before_pitch", "after_pitch"):
            value = getattr(self, field_name)
            if value is not None and (
                not _is_integer(value) or value < 0
            ):
                raise ValueError(f"{field_name} must be nonnegative or None")
        if self.pitch_delta is not None and not _is_integer(self.pitch_delta):
            raise ValueError("pitch_delta must be an integer or None")
        for field_name in (
            "before_geometry_frame_id",
            "after_geometry_frame_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty")
        if self.before_geometry_frame_id == self.after_geometry_frame_id:
            raise ValueError("camera result requires a changed geometry frame")


@dataclass(frozen=True, slots=True)
class CameraUiState:
    bank_known: bool
    bank_open: bool
    bank_pin_open: bool
    bank_readable: bool
    dialogue_active: bool
    dialogue_type: str
    text_input_active: bool

    def __post_init__(self) -> None:
        for field_name in (
            "bank_known",
            "bank_open",
            "bank_pin_open",
            "bank_readable",
            "dialogue_active",
            "text_input_active",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be bool")
        if not isinstance(self.dialogue_type, str) or not self.dialogue_type.strip():
            raise ValueError("dialogue_type must be non-empty")
        if self.bank_pin_open:
            raise ValueError("camera zoom evidence cannot retain an open bank PIN")
        if self.bank_open:
            raise ValueError("camera zoom evidence cannot retain an open bank")
        if self.dialogue_active:
            raise ValueError("camera zoom evidence cannot retain an active dialogue")
        if self.text_input_active:
            raise ValueError("camera zoom evidence cannot retain active text input")


@dataclass(frozen=True, slots=True)
class CameraZoomResult:
    wheel_amount: int
    before_zoom: int
    after_zoom: int
    zoom_delta: int
    before_yaw: int
    after_yaw: int
    before_pitch: int
    after_pitch: int
    before_process_id: int
    after_process_id: int
    before_location: WorldPoint
    after_location: WorldPoint
    source_session_id: str
    before_geometry_frame_id: str
    after_geometry_frame_id: str
    before_ui_state: CameraUiState
    after_ui_state: CameraUiState

    def __post_init__(self) -> None:
        if (
            not _is_integer(self.wheel_amount)
            or self.wheel_amount == 0
            or abs(self.wheel_amount) > MAX_CAMERA_ZOOM_STEP
        ):
            raise ValueError("wheel_amount must be non-zero and within the zoom bound")
        for field_name in ("before_zoom", "after_zoom"):
            if not _is_nonnegative_integer(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be a non-negative integer")
        if (
            not _is_integer(self.zoom_delta)
            or self.zoom_delta != self.after_zoom - self.before_zoom
            or self.zoom_delta == 0
            or (self.wheel_amount > 0) != (self.zoom_delta > 0)
        ):
            raise ValueError("zoom_delta must match the requested wheel direction")
        for field_name in ("before_yaw", "after_yaw"):
            value = getattr(self, field_name)
            if not _is_integer(value) or not 0 <= value < CAMERA_YAW_UNITS:
                raise ValueError(f"{field_name} must be a valid camera yaw")
        for field_name in ("before_pitch", "after_pitch"):
            if not _is_nonnegative_integer(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be non-negative")
        if self.before_yaw != self.after_yaw or self.before_pitch != self.after_pitch:
            raise ValueError("camera zoom result requires unchanged yaw and pitch")
        for field_name in ("before_process_id", "after_process_id"):
            if not _is_positive_integer(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be positive")
        if self.before_process_id != self.after_process_id:
            raise ValueError("camera zoom result requires an unchanged process")
        for field_name in ("before_location", "after_location"):
            if not isinstance(getattr(self, field_name), WorldPoint):
                raise TypeError(f"{field_name} must be a WorldPoint")
        if self.before_location != self.after_location:
            raise ValueError("camera zoom result requires an unchanged player location")
        if not isinstance(self.source_session_id, str) or not self.source_session_id.strip():
            raise ValueError("source_session_id must be non-empty")
        for field_name in (
            "before_geometry_frame_id",
            "after_geometry_frame_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty")
        if self.before_geometry_frame_id == self.after_geometry_frame_id:
            raise ValueError("camera zoom result requires a changed geometry frame")
        if not isinstance(self.before_ui_state, CameraUiState) or not isinstance(
            self.after_ui_state, CameraUiState
        ):
            raise TypeError("camera zoom UI evidence must be CameraUiState")
        if self.before_ui_state != self.after_ui_state:
            raise ValueError("camera zoom result requires unchanged UI state")


@dataclass(frozen=True, slots=True)
class Outcome:
    kind: OutcomeKind
    observed_tick: int
    camera_pose_result: CameraPoseResult | None = None
    camera_zoom_result: CameraZoomResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OutcomeKind):
            raise ValueError("kind must be an OutcomeKind")
        if not _is_nonnegative_integer(self.observed_tick):
            raise ValueError("observed_tick must be a non-negative integer")
        if self.camera_pose_result is not None and (
            self.kind is not OutcomeKind.CAMERA_POSE_CHANGED
            or not isinstance(self.camera_pose_result, CameraPoseResult)
        ):
            raise ValueError(
                "camera_pose_result is only valid for a camera pose outcome"
            )
        if self.camera_zoom_result is not None and (
            self.kind is not OutcomeKind.CAMERA_ZOOM_CHANGED
            or not isinstance(self.camera_zoom_result, CameraZoomResult)
        ):
            raise ValueError(
                "camera_zoom_result is only valid for a camera zoom outcome"
            )
        if self.camera_pose_result is not None and self.camera_zoom_result is not None:
            raise ValueError("an outcome may carry only one camera result")


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: VerificationStatus
    reason: str
    outcome: Outcome | None = None
    failure_kind: VerificationFailureKind | None = None

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
        if self.status is VerificationStatus.FAIL and not isinstance(
            self.failure_kind, VerificationFailureKind
        ):
            raise ValueError("FAIL requires a typed VerificationFailureKind")
        if self.status is not VerificationStatus.FAIL and self.failure_kind is not None:
            raise ValueError("only FAIL may carry a VerificationFailureKind")

    @property
    def pending(self) -> bool:
        return self.status is VerificationStatus.PENDING

    @property
    def passed(self) -> bool:
        return self.status is VerificationStatus.PASS and self.outcome is not None

    @property
    def failed(self) -> bool:
        return self.status is VerificationStatus.FAIL


@dataclass(frozen=True, slots=True)
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
            return _fail(
                invalid_reason,
                VerificationFailureKind.INVALID_SPECIFICATION,
            )

        if observation.session_id != specification.source_session_id:
            return _fail(
                "session_changed",
                VerificationFailureKind.SESSION_CHANGED,
            )

        if observation.tick <= specification.before_tick:
            return _pending("awaiting_later_observation")
        if observation.tick > specification.deadline_tick:
            return _fail(
                "deadline_exceeded",
                VerificationFailureKind.DEADLINE_EXCEEDED,
            )
        if not self._observation_usable(observation):
            if observation.tick == specification.deadline_tick:
                return _fail(
                    "observation_unusable_at_deadline",
                    VerificationFailureKind.OBSERVATION_UNUSABLE_AT_DEADLINE,
                )
            return _pending("observation_not_usable")
        if specification.kind is VerificationKind.CAMERA_ZOOM_CHANGED:
            invalidation = _camera_zoom_invalidation(specification, observation)
            if invalidation is not None:
                return invalidation
        if observation.widgets.bank_pin_open:
            return _fail(
                "bank_pin_open",
                VerificationFailureKind.BANK_PIN_OPEN,
            )

        outcome = _successful_outcome(specification, observation)
        if outcome is not None:
            return _pass(outcome)
        if observation.tick == specification.deadline_tick:
            if specification.kind is VerificationKind.CAMERA_ZOOM_CHANGED:
                if observation.camera_zoom == specification.before_camera_zoom:
                    return _fail(
                        "camera_zoom_unchanged_at_deadline",
                        VerificationFailureKind.CAMERA_ZOOM_UNCHANGED_AT_DEADLINE,
                    )
                return _fail(
                    "camera_zoom_evidence_unavailable_at_deadline",
                    VerificationFailureKind.CAMERA_ZOOM_EVIDENCE_UNAVAILABLE_AT_DEADLINE,
                )
            if specification.kind is VerificationKind.ITEM_QUANTITY_INCREASED:
                if not observation.inventory.known:
                    return _fail(
                        "item_quantity_unavailable_at_deadline",
                        VerificationFailureKind.OBSERVATION_UNUSABLE_AT_DEADLINE,
                    )
                if (
                    observation.inventory.quantity(specification.item_id)
                    == specification.before_quantity
                ):
                    return _fail(
                        "item_quantity_unchanged_at_deadline",
                        VerificationFailureKind.ITEM_QUANTITY_UNCHANGED_AT_DEADLINE,
                    )
            return _fail(
                "condition_unmet_at_deadline",
                VerificationFailureKind.CONDITION_UNMET_AT_DEADLINE,
            )
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
        if specification.interface_name not in {None, BANK_INTERFACE_NAME}:
            return "unsupported_interface"
        if (
            specification.interface_name == BANK_INTERFACE_NAME
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
        if specification.interface_name != BANK_INTERFACE_NAME:
            return "unsupported_interface"
        if not _is_nonnegative_integer(specification.expected_plane):
            return "invalid_interface_plane"

    if specification.kind is VerificationKind.CAMERA_POSE_CHANGED:
        if not isinstance(specification.before_location, WorldPoint):
            return "invalid_camera_location_baseline"
        if (
            not _is_integer(specification.before_camera_yaw)
            or not 0 <= specification.before_camera_yaw < CAMERA_YAW_UNITS
        ):
            return "invalid_camera_yaw_baseline"
        if (
            not isinstance(specification.before_geometry_frame_id, str)
            or not specification.before_geometry_frame_id.strip()
        ):
            return "invalid_camera_geometry_frame_baseline"
        if specification.camera_key not in {"left", "right", "up", "down"}:
            return "invalid_camera_key"
        if specification.camera_key in {"up", "down"} and not _is_nonnegative_integer(
            specification.before_camera_pitch
        ):
            return "invalid_camera_pitch_baseline"

    if specification.kind is VerificationKind.CAMERA_ZOOM_CHANGED:
        if not isinstance(specification.before_location, WorldPoint):
            return "invalid_camera_zoom_location_baseline"
        if (
            not _is_integer(specification.before_camera_yaw)
            or not 0 <= specification.before_camera_yaw < CAMERA_YAW_UNITS
        ):
            return "invalid_camera_zoom_yaw_baseline"
        if not _is_nonnegative_integer(specification.before_camera_pitch):
            return "invalid_camera_zoom_pitch_baseline"
        if not _is_nonnegative_integer(specification.before_camera_zoom):
            return "invalid_camera_zoom_baseline"
        if (
            not _is_integer(specification.camera_zoom_amount)
            or specification.camera_zoom_amount == 0
            or abs(specification.camera_zoom_amount) > MAX_CAMERA_ZOOM_STEP
        ):
            return "invalid_camera_zoom_amount"
        if not _is_positive_integer(specification.before_process_id):
            return "invalid_camera_zoom_process_baseline"
        if (
            not isinstance(specification.before_geometry_frame_id, str)
            or not specification.before_geometry_frame_id.strip()
        ):
            return "invalid_camera_zoom_geometry_frame_baseline"
        for field_name in (
            "before_bank_known",
            "before_bank_open",
            "before_bank_pin_open",
            "before_bank_readable",
            "before_dialogue_active",
            "before_text_input_active",
        ):
            if not isinstance(getattr(specification, field_name), bool):
                return "invalid_camera_zoom_ui_baseline"
        if (
            specification.before_bank_open
            or specification.before_bank_pin_open
            or specification.before_dialogue_active
            or specification.before_text_input_active
        ):
            return "unsafe_camera_zoom_ui_baseline"
        if (
            not isinstance(specification.before_dialogue_type, str)
            or not specification.before_dialogue_type.strip()
        ):
            return "invalid_camera_zoom_dialogue_baseline"

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
        if specification.interface_name == BANK_INTERFACE_NAME and not (
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
            specification.interface_name == BANK_INTERFACE_NAME
            and observation.plane == specification.expected_plane
            and observation.widgets.bank_known
            and observation.widgets.bank_open
            and observation.widgets.bank_readable
        ):
            return Outcome(OutcomeKind.INTERFACE_OPENED, observation.tick)
        return None

    if specification.kind is VerificationKind.INTERFACE_CLOSED:
        if (
            specification.interface_name == BANK_INTERFACE_NAME
            and observation.plane == specification.expected_plane
            and observation.widgets.bank_known
            and not observation.widgets.bank_open
        ):
            return Outcome(OutcomeKind.INTERFACE_CLOSED, observation.tick)
        return None

    if specification.kind is VerificationKind.CAMERA_POSE_CHANGED:
        if (
            observation.location != specification.before_location
            or observation.geometry_frame_id is None
            or observation.geometry_frame_id
            == specification.before_geometry_frame_id
        ):
            return None
        if specification.camera_key in {"left", "right"}:
            if observation.camera_yaw is None:
                return None
            before = specification.before_camera_yaw
            assert before is not None
            if specification.camera_key == "right":
                delta = (observation.camera_yaw - before) % CAMERA_YAW_UNITS
            else:
                delta = (before - observation.camera_yaw) % CAMERA_YAW_UNITS
            if 0 < delta <= CAMERA_YAW_UNITS // 2:
                return Outcome(
                    OutcomeKind.CAMERA_POSE_CHANGED,
                    observation.tick,
                    _camera_pose_result(specification, observation),
                )
        else:
            if observation.camera_pitch is None:
                return None
            before_pitch = specification.before_camera_pitch
            assert before_pitch is not None
            if (
                specification.camera_key == "up"
                and observation.camera_pitch > before_pitch
            ) or (
                specification.camera_key == "down"
                and observation.camera_pitch < before_pitch
            ):
                return Outcome(
                    OutcomeKind.CAMERA_POSE_CHANGED,
                    observation.tick,
                    _camera_pose_result(specification, observation),
                )
        return None

    if specification.kind is VerificationKind.CAMERA_ZOOM_CHANGED:
        if (
            observation.geometry_frame_id is None
            or observation.geometry_frame_id == specification.before_geometry_frame_id
            or observation.camera_zoom is None
            or observation.camera_yaw is None
            or observation.camera_pitch is None
        ):
            return None
        before_zoom = specification.before_camera_zoom
        amount = specification.camera_zoom_amount
        assert before_zoom is not None
        assert amount is not None
        delta = observation.camera_zoom - before_zoom
        if delta != 0 and (amount > 0) == (delta > 0):
            return Outcome(
                OutcomeKind.CAMERA_ZOOM_CHANGED,
                observation.tick,
                camera_zoom_result=_camera_zoom_result(specification, observation),
            )
        return None

    return None


def _camera_pose_result(
    specification: VerificationSpec,
    observation: Observation,
) -> CameraPoseResult:
    before_yaw = specification.before_camera_yaw
    after_yaw = observation.camera_yaw
    yaw_delta = None
    if before_yaw is not None and after_yaw is not None:
        yaw_delta = (
            after_yaw - before_yaw + CAMERA_YAW_UNITS // 2
        ) % CAMERA_YAW_UNITS - CAMERA_YAW_UNITS // 2
    before_pitch = specification.before_camera_pitch
    after_pitch = observation.camera_pitch
    pitch_delta = (
        after_pitch - before_pitch
        if before_pitch is not None and after_pitch is not None
        else None
    )
    assert specification.camera_key is not None
    assert specification.before_geometry_frame_id is not None
    assert observation.geometry_frame_id is not None
    return CameraPoseResult(
        camera_key=specification.camera_key,
        before_yaw=before_yaw,
        after_yaw=after_yaw,
        yaw_delta=yaw_delta,
        before_pitch=before_pitch,
        after_pitch=after_pitch,
        pitch_delta=pitch_delta,
        before_geometry_frame_id=specification.before_geometry_frame_id,
        after_geometry_frame_id=observation.geometry_frame_id,
    )


def _camera_zoom_invalidation(
    specification: VerificationSpec,
    observation: Observation,
) -> VerificationResult | None:
    if (
        observation.client_process_id != specification.before_process_id
        or observation.location != specification.before_location
    ):
        return _fail(
            "camera_identity_changed",
            VerificationFailureKind.CAMERA_IDENTITY_CHANGED,
        )
    if (
        observation.camera_yaw is not None
        and observation.camera_yaw != specification.before_camera_yaw
    ) or (
        observation.camera_pitch is not None
        and observation.camera_pitch != specification.before_camera_pitch
    ):
        return _fail(
            "camera_pose_changed_during_zoom",
            VerificationFailureKind.CAMERA_POSE_CHANGED_DURING_ZOOM,
        )
    widgets = observation.widgets
    actual_ui = (
        widgets.bank_known,
        widgets.bank_open,
        widgets.bank_pin_open,
        widgets.bank_readable,
        widgets.dialogue_active,
        widgets.dialogue_type,
        getattr(observation, "text_input_active", None),
    )
    expected_ui = (
        specification.before_bank_known,
        specification.before_bank_open,
        specification.before_bank_pin_open,
        specification.before_bank_readable,
        specification.before_dialogue_active,
        specification.before_dialogue_type,
        specification.before_text_input_active,
    )
    if actual_ui != expected_ui:
        return _fail(
            "camera_ui_state_changed",
            VerificationFailureKind.CAMERA_UI_STATE_CHANGED,
        )
    if observation.camera_zoom is not None:
        before_zoom = specification.before_camera_zoom
        amount = specification.camera_zoom_amount
        assert before_zoom is not None
        assert amount is not None
        delta = observation.camera_zoom - before_zoom
        if delta != 0 and (amount > 0) != (delta > 0):
            return _fail(
                "camera_zoom_direction_contradicted",
                VerificationFailureKind.CAMERA_ZOOM_DIRECTION_CONTRADICTED,
            )
    return None


def _camera_zoom_result(
    specification: VerificationSpec,
    observation: Observation,
) -> CameraZoomResult:
    assert specification.camera_zoom_amount is not None
    assert specification.before_camera_zoom is not None
    assert observation.camera_zoom is not None
    assert specification.before_camera_yaw is not None
    assert observation.camera_yaw is not None
    assert specification.before_camera_pitch is not None
    assert observation.camera_pitch is not None
    assert specification.before_process_id is not None
    assert observation.client_process_id is not None
    assert specification.before_location is not None
    assert observation.location is not None
    assert specification.source_session_id is not None
    assert specification.before_geometry_frame_id is not None
    assert observation.geometry_frame_id is not None
    before_ui_state = _camera_ui_state_from_specification(specification)
    after_ui_state = _camera_ui_state_from_observation(observation)
    return CameraZoomResult(
        wheel_amount=specification.camera_zoom_amount,
        before_zoom=specification.before_camera_zoom,
        after_zoom=observation.camera_zoom,
        zoom_delta=observation.camera_zoom - specification.before_camera_zoom,
        before_yaw=specification.before_camera_yaw,
        after_yaw=observation.camera_yaw,
        before_pitch=specification.before_camera_pitch,
        after_pitch=observation.camera_pitch,
        before_process_id=specification.before_process_id,
        after_process_id=observation.client_process_id,
        before_location=specification.before_location,
        after_location=observation.location,
        source_session_id=specification.source_session_id,
        before_geometry_frame_id=specification.before_geometry_frame_id,
        after_geometry_frame_id=observation.geometry_frame_id,
        before_ui_state=before_ui_state,
        after_ui_state=after_ui_state,
    )


def _camera_ui_state_from_specification(
    specification: VerificationSpec,
) -> CameraUiState:
    assert specification.before_bank_known is not None
    assert specification.before_bank_open is not None
    assert specification.before_bank_pin_open is not None
    assert specification.before_bank_readable is not None
    assert specification.before_dialogue_active is not None
    assert specification.before_dialogue_type is not None
    assert specification.before_text_input_active is not None
    return CameraUiState(
        bank_known=specification.before_bank_known,
        bank_open=specification.before_bank_open,
        bank_pin_open=specification.before_bank_pin_open,
        bank_readable=specification.before_bank_readable,
        dialogue_active=specification.before_dialogue_active,
        dialogue_type=specification.before_dialogue_type,
        text_input_active=specification.before_text_input_active,
    )


def _camera_ui_state_from_observation(observation: Observation) -> CameraUiState:
    widgets = observation.widgets
    text_input_active = getattr(observation, "text_input_active", None)
    assert isinstance(text_input_active, bool)
    return CameraUiState(
        bank_known=widgets.bank_known,
        bank_open=widgets.bank_open,
        bank_pin_open=widgets.bank_pin_open,
        bank_readable=widgets.bank_readable,
        dialogue_active=widgets.dialogue_active,
        dialogue_type=widgets.dialogue_type,
        text_input_active=text_input_active,
    )


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


def _fail(
    reason: str,
    failure_kind: VerificationFailureKind,
) -> VerificationResult:
    return VerificationResult(
        VerificationStatus.FAIL,
        reason,
        failure_kind=failure_kind,
    )

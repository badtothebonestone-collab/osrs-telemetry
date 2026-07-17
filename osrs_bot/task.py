from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from heapq import nsmallest
from math import hypot

from .behavior import (
    AimDecision,
    BehaviorPolicy,
    CameraFramingDecision,
    TimingDecision,
)
from .camera import (
    CameraCorrectionPhase,
    CameraResponseModel,
    CameraResponseSample,
    desired_camera_yaw,
    proves_yaw_overshoot,
    select_camera_hold_millis,
    yaw_error_to_world_target,
    yaw_reversal_allowed,
)
from .definition import (
    FixedRoute,
    FixedRouteStep,
    RoutePointClassification,
)
from .model import (
    Action,
    ActionKind,
    BANK_INTERFACE_NAME,
    CAMERA_YAW_UNITS,
    CameraConstraint,
    CameraZoomConstraint,
    CLOSE_BANK_WIDGET_KEY,
    DEPOSIT_INVENTORY_WIDGET_KEY,
    DialogueOption,
    DialogueOptionConstraint,
    InterfaceConstraint,
    InventoryConstraint,
    NearbyObject,
    Observation,
    ScreenBounds,
    ScreenPoint,
    TargetGeometry,
    TaskConstraints,
    VerificationKind,
    VerificationSpec,
    WidgetTarget,
    WorldPoint,
)
from .movement import (
    RouteCandidateSupport,
    RouteProgress,
    RouteSelectionLimits,
    RouteTargetSelection,
    route_progress,
    select_farthest_useful_route_target,
)
from .profile import DEFAULT_BINDING, BoundProfile
from .task_contract import (
    CameraAcquisitionState,
    CameraDecisionEvidence,
    Decision,
    DecisionEvidence,
    ObservationRequest,
    RejectedCandidateEvidence,
    RouteCandidateRejectionEvidence,
    RouteDecisionEvidence,
    TargetEvidence,
    TargetingDecisionEvidence,
    TimingDecisionEvidence,
    TargetContinuityEvidence,
    TaskProgressSnapshot,
    TaskSnapshot,
    TaskStatus,
)
from .verification import (
    OutcomeKind,
    VerificationFailureKind,
    VerificationResult,
)


WOODCUT_BANK_TASK_ID = "woodcut_bank"
WOODCUT_BANK_TASK_DISPLAY_NAME = "Woodcut ordinary Trees and bank one inventory"
CAMERA_RECOVERY_HOLD_MILLIS = 250
RESOURCE_NO_YIELD_MAX_RETRIES = 1
CAMERA_NON_IMPROVING_CORRECTION_LIMIT = 2
TARGET_INCOMPLETE_OMISSION_WAIT_FRAMES = 2
MAX_TARGET_CANDIDATES = 64
MAX_TARGET_REJECTION_EVIDENCE = 32
TARGET_QUERY_RADIUS_TILES = 4
TARGET_QUERY_MAX_OBJECTS = 16
TARGET_QUERY_MAX_PROJECTION_OBJECTS = 8
DISCOVERY_QUERY_MAX_OBJECTS = 64
DISCOVERY_QUERY_MAX_PROJECTION_OBJECTS = 32
ROUTE_QUERY_MAX_OBJECTS = 24
ROUTE_QUERY_MAX_PROJECTION_OBJECTS = 16
_AIM_OCCLUSION_CELL_PIXELS = 128
_AIM_OCCLUSION_MAX_CELLS_PER_BOUNDS = 256
CAMERA_FRAMING_CLASSIFICATION_RANK = {
    "not_visible": 0,
    "obscured_or_contradictory": 1,
    "barely_visible": 2,
    "usable": 3,
    "well_framed": 4,
}


class TaskPhase(str, Enum):
    FIND_TREE = "find_tree"
    CHOP = "chop"
    VERIFY_LOGS = "verify_logs"
    NAVIGATE_TO_BANK = "navigate_to_bank"
    OPEN_BANK = "open_bank"
    DEPOSIT_LOGS = "deposit_logs"
    VERIFY_DEPOSIT = "verify_deposit"
    CLOSE_BANK = "close_bank"
    NAVIGATE_TO_TREES = "navigate_to_trees"
    STAIR_DIALOGUE = "stair_dialogue"
    COMPLETE = "complete"
    BLOCKED = "blocked"


@dataclass
class TaskProgress:
    phase: TaskPhase = TaskPhase.FIND_TREE
    route_index: int = 0
    target_key: str | None = None
    pending: VerificationSpec | None = None
    cycles_completed: int = 0
    failures: list[str] = field(default_factory=list)
    resume_phase: TaskPhase | None = None
    blocked_from_phase: TaskPhase | None = None


@dataclass
class CameraAcquisitionEpisode:
    """Task-owned lock and bounded state for one camera acquisition."""

    episode_id: str
    context: str
    logical_target_id: str
    locked_target_key: str
    locked_target_id: int
    locked_target_name: str
    locked_target_kind: str
    locked_target_action: str
    locked_target_location: WorldPoint
    route_index: int | None
    source_session_id: str
    source_process_id: int | None
    canvas_bounds: ScreenBounds | None
    viewport_bounds: ScreenBounds
    client_window_bounds: ScreenBounds | None
    started_tick: int
    state: CameraAcquisitionState = CameraAcquisitionState.STABILIZING
    actions_sent: int = 0
    zoom_actions_sent: int = 0
    last_verified_direction: str | None = None
    last_yaw_error_units: int | None = None
    overshoot_proven: bool = False
    pitch_limit_direction: str | None = None
    pitch_limit_pose: int | None = None
    desired_yaw: int | None = None
    desired_pitch: int | None = None
    desired_pitch_min: int | None = None
    desired_pitch_max: int | None = None
    pitch_error_units: int | None = None
    retained_reason: str | None = None


@dataclass
class TargetContinuityLock:
    """Exact immutable identity retained across bounded incomplete omissions."""

    context: str
    key: str
    object_id: int
    name: str
    kind: str
    action: str
    location: WorldPoint
    locked_tick: int
    last_seen_tick: int
    incomplete_omission_frames: int = 0
    retained_reason: str | None = None

class WoodcutBankTask:
    """Explicit woodcut/bank FSM bound to one validated task/site profile."""

    def __init__(
        self,
        binding: BoundProfile = DEFAULT_BINDING,
        *,
        behavior: BehaviorPolicy | None = None,
    ) -> None:
        if not isinstance(binding, BoundProfile):
            raise TypeError("binding must be a validated BoundProfile")
        if len(binding.definition.resource.produced_item_ids) != 1:
            raise ValueError("WoodcutBankTask requires exactly one produced item ID")
        self.binding = binding
        self.definition = binding.definition
        if behavior is not None and not isinstance(behavior, BehaviorPolicy):
            raise TypeError("behavior must be BehaviorPolicy or None")
        self.behavior = behavior or BehaviorPolicy()
        self._produced_item_id = next(
            iter(binding.definition.resource.produced_item_ids)
        )
        self.progress = TaskProgress()
        self._movement_verified = False
        self._route_settle_location: WorldPoint | None = None
        self._route_settle_since_tick: int | None = None
        self._camera_recovery_step_id: str | None = None
        self._camera_recovery_attempts = 0
        self._camera_recovery_total_hold_millis = 0
        self._camera_pitch_suppressed_step_id: str | None = None
        self._route_projection_wait_since_tick: int | None = None
        self._pending_camera_step_id: str | None = None
        self._pending_camera_hold_millis: int | None = None
        self._camera_progress_baseline: CameraFramingDecision | None = None
        self._camera_progress_best: CameraFramingDecision | None = None
        self._camera_non_improving_corrections = 0
        self._camera_episode: CameraAcquisitionEpisode | None = None
        self._camera_response_model = CameraResponseModel()
        self._pending_camera_yaw_error: int | None = None
        self._pending_camera_pitch_error: int | None = None
        self._camera_zoom_attempts = 0
        self._pending_camera_zoom_step_id: str | None = None
        self._last_camera_response: CameraResponseSample | None = None
        self._restart_reconciled_without_cycle_credit = False
        self._next_resource_suppression: tuple[str, str] | None = None
        self._resource_camera_suppressions: dict[str, int] = {}
        self._resource_no_yield_retries = 0
        self._route_diagnostics_id: str | None = None
        self._last_route_selection: RouteTargetSelection | None = None
        self._last_route_progress: RouteProgress | None = None
        self._last_route_actual_progress_delta: float | None = None
        self._pending_route_start_progress: RouteProgress | None = None
        self._decision_sequence = 0
        self._last_camera_framing: CameraFramingDecision | None = None
        self._last_aim_decision: AimDecision | None = None
        self._last_timing_decision: TimingDecision | None = None
        self._aim_history: dict[str, list[ScreenPoint]] = {}
        self._route_projected_points: tuple[ScreenPoint, ...] = ()
        self._route_projected_labels: tuple[str, ...] = ()
        self._route_mandatory_points: tuple[ScreenPoint, ...] = ()
        self._route_skipped_points: tuple[ScreenPoint, ...] = ()
        self._route_selected_screen_point: ScreenPoint | None = None
        self._route_candidate_rejections: tuple[
            RouteCandidateRejectionEvidence, ...
        ] = ()
        self._last_observation_location: WorldPoint | None = None
        self._target_lock: TargetContinuityLock | None = None
        self._last_target_unlock_reason: str | None = None
        self._last_resource_selection_metrics: dict[str, int] = {
            "scene_objects": 0,
            "indexed_candidates": 0,
            "identity_evaluations": 0,
            "ambiguity_queries": 0,
            "ranked_candidates": 0,
            "rejection_evidence": 0,
        }

    @property
    def last_route_selection(self) -> RouteTargetSelection | None:
        return self._last_route_selection

    @property
    def last_route_progress(self) -> RouteProgress | None:
        return self._last_route_progress

    @property
    def last_route_actual_progress_delta(self) -> float | None:
        return self._last_route_actual_progress_delta

    @property
    def last_resource_selection_metrics(self) -> dict[str, int]:
        return dict(self._last_resource_selection_metrics)

    def observation_request(self) -> ObservationRequest:
        """Plan one phase-specific, explicitly anchored observation query."""
        route = self._current_fixed_route()
        projections = (
            tuple(
                (step.target_key, step.location)
                for _, step in self._route_projection_steps(route)
            )
            if route is not None
            else ()
        )
        locked = self._target_lock
        if locked is not None:
            return ObservationRequest(
                tile_projections=projections,
                priority_object_ids=(locked.object_id,) if locked.object_id > 0 else (),
                priority_object_keys=(locked.key,),
                center_world_location=locked.location,
                radius_tiles=TARGET_QUERY_RADIUS_TILES,
                max_objects=TARGET_QUERY_MAX_OBJECTS,
                max_projection_objects=TARGET_QUERY_MAX_PROJECTION_OBJECTS,
                purpose=f"{locked.context}_target_verification",
            )

        phase = self._effective_phase()
        priority_ids = self._priority_object_ids_for_observation()
        if phase is TaskPhase.FIND_TREE:
            work_area = self.definition.resource.work_area
            return ObservationRequest(
                priority_object_ids=priority_ids,
                center_world_location=(
                    self._last_observation_location or work_area.anchor
                ),
                radius_tiles=max(1, min(32, work_area.radius)),
                max_objects=DISCOVERY_QUERY_MAX_OBJECTS,
                max_projection_objects=DISCOVERY_QUERY_MAX_PROJECTION_OBJECTS,
                purpose="resource_discovery",
            )
        if phase is TaskPhase.OPEN_BANK:
            return ObservationRequest(
                priority_object_ids=priority_ids,
                center_world_location=self.definition.bank.anchor,
                radius_tiles=TARGET_QUERY_RADIUS_TILES,
                max_objects=TARGET_QUERY_MAX_OBJECTS,
                max_projection_objects=TARGET_QUERY_MAX_PROJECTION_OBJECTS,
                purpose="bank_acquisition",
            )
        if route is not None:
            step = (
                route.steps[self.progress.route_index]
                if 0 <= self.progress.route_index < len(route.steps)
                else None
            )
            exact_transition = bool(step is not None and not step.is_walk)
            return ObservationRequest(
                tile_projections=projections,
                priority_object_ids=priority_ids,
                center_world_location=(
                    step.location
                    if step is not None
                    else self._last_observation_location
                ),
                radius_tiles=(
                    TARGET_QUERY_RADIUS_TILES if exact_transition else 16
                ),
                max_objects=(
                    TARGET_QUERY_MAX_OBJECTS
                    if exact_transition
                    else ROUTE_QUERY_MAX_OBJECTS
                ),
                max_projection_objects=(
                    TARGET_QUERY_MAX_PROJECTION_OBJECTS
                    if exact_transition
                    else ROUTE_QUERY_MAX_PROJECTION_OBJECTS
                ),
                purpose=(
                    "route_transition_acquisition"
                    if exact_transition
                    else "route_lookahead"
                ),
            )

        center = self._last_observation_location
        if phase in {
            TaskPhase.DEPOSIT_LOGS,
            TaskPhase.VERIFY_DEPOSIT,
            TaskPhase.CLOSE_BANK,
        }:
            center = self.definition.bank.anchor
        return ObservationRequest(
            priority_object_ids=priority_ids,
            center_world_location=center,
            radius_tiles=TARGET_QUERY_RADIUS_TILES if center is not None else None,
            max_objects=0,
            max_projection_objects=0,
            purpose="phase_state_verification",
        )

    def _effective_phase(self) -> TaskPhase:
        return (
            self.progress.resume_phase
            if self.progress.phase is TaskPhase.STAIR_DIALOGUE
            and self.progress.resume_phase is not None
            else self.progress.phase
        )

    def _priority_object_ids_for_observation(self) -> tuple[int, ...]:
        phase = self._effective_phase()
        if phase in {
            TaskPhase.FIND_TREE,
            TaskPhase.CHOP,
            TaskPhase.VERIFY_LOGS,
        }:
            return tuple(sorted(self.definition.resource.selector.object_ids))
        if phase is TaskPhase.OPEN_BANK:
            return tuple(sorted(self.definition.bank.selector.object_ids))
        if phase not in {
            TaskPhase.NAVIGATE_TO_BANK,
            TaskPhase.NAVIGATE_TO_TREES,
        }:
            return ()

        route = self._current_fixed_route()
        if (
            route is None
            or self.progress.route_index < 0
            or self.progress.route_index >= len(route.steps)
        ):
            return ()
        for step in route.steps[self.progress.route_index :]:
            if step.classification is RoutePointClassification.MANDATORY_TRANSITION:
                assert step.object_id is not None
                return (step.object_id,)
        return ()

    def snapshot(self) -> TaskSnapshot:
        if self.progress.phase is TaskPhase.COMPLETE:
            status = TaskStatus.COMPLETE
        elif self.progress.phase is TaskPhase.BLOCKED:
            status = TaskStatus.BLOCKED
        else:
            status = TaskStatus.RUNNING
        blocker = (
            self.progress.failures[-1]
            if status is TaskStatus.BLOCKED and self.progress.failures
            else None
        )
        route_step, route_progress = self._route_context_snapshot()
        cycle_progress = self._cycle_progress_snapshot()
        return TaskSnapshot(
            task_id=WOODCUT_BANK_TASK_ID,
            status=status,
            state=self.progress.phase.value,
            blocker=blocker,
            definition_id=self.definition.definition_id,
            profile_id=self.binding.profile.profile_id,
            progress=route_progress or cycle_progress,
            route_step=route_step,
            route_progress=route_progress,
            cycle_progress=cycle_progress,
            target_continuity=self._target_continuity_snapshot(),
        )

    def decide(self, observation: Observation) -> Decision:
        if self.progress.phase in {TaskPhase.COMPLETE, TaskPhase.BLOCKED}:
            return self._wait(observation, f"task is {self.progress.phase.value}")

        if self.progress.pending is not None:
            return self._wait(observation, "waiting for external action verification")

        if not observation.loaded_scene:
            return self._wait(observation, "waiting for a fresh loaded-scene observation")
        if observation.plane is None or observation.location is None:
            return self._wait(observation, "player location is incomplete")
        if not observation.session_id:
            return self._wait(observation, "session identity is unavailable")
        if not observation.menu_fresh:
            return self._wait(observation, "client menu evidence is stale")
        if observation.menu_client_tick is None:
            return self._wait(observation, "client menu sample is unavailable")
        if observation.location.plane != observation.plane:
            return self._wait(observation, "player plane and location disagree")
        self._last_observation_location = observation.location
        camera_environment_failure = self._camera_episode_environment_failure(
            observation
        )
        if camera_environment_failure is not None:
            assert self._camera_episode is not None
            self._camera_episode.state = CameraAcquisitionState.INVALIDATED
            self._camera_episode.retained_reason = camera_environment_failure
            return self._block(
                observation,
                camera_environment_failure,
                evidence=self._evidence_with_camera(DecisionEvidence()),
            )
        inventory_already_verified_for_return = self.progress.phase in {
            TaskPhase.CLOSE_BANK,
            TaskPhase.NAVIGATE_TO_TREES,
        }
        if (
            not observation.inventory.known
            and not inventory_already_verified_for_return
        ):
            return self._wait(observation, "inventory is not observable")

        held_ids = {
            item.item_id for item in observation.inventory.items if item.quantity > 0
        }
        if (
            self.definition.inventory.require_only_allowed_items
            and not held_ids.issubset(self.definition.inventory.allowed_item_ids)
        ):
            return self._block(
                observation, "inventory violates the selected task definition"
            )

        if self.progress.phase == TaskPhase.FIND_TREE:
            return self._find_tree(observation)
        if self.progress.phase == TaskPhase.CHOP:
            return self._chop(observation)
        if self.progress.phase in {TaskPhase.VERIFY_LOGS, TaskPhase.VERIFY_DEPOSIT}:
            return self._block(observation, "verification phase has no pending verification")
        if self.progress.phase == TaskPhase.NAVIGATE_TO_BANK:
            return self._navigate(observation, self.definition.route_to_bank)
        if self.progress.phase == TaskPhase.OPEN_BANK:
            return self._open_bank(observation)
        if self.progress.phase == TaskPhase.DEPOSIT_LOGS:
            return self._deposit_logs(observation)
        if self.progress.phase == TaskPhase.CLOSE_BANK:
            return self._close_bank(observation)
        if self.progress.phase == TaskPhase.NAVIGATE_TO_TREES:
            return self._navigate(observation, self.definition.route_to_resource)
        if self.progress.phase == TaskPhase.STAIR_DIALOGUE:
            return self._choose_stair_direction(observation)
        return self._block(observation, "unknown task phase")

    def apply_verification(self, result: VerificationResult) -> None:
        """Apply the sole external verifier's result to the pending action."""
        pending = self.progress.pending
        if pending is None:
            raise RuntimeError("no action verification is pending")
        self.progress.pending = None

        if not result.passed or result.outcome is None:
            if pending.kind in {
                VerificationKind.CAMERA_POSE_CHANGED,
                VerificationKind.CAMERA_ZOOM_CHANGED,
            }:
                # A failed pose verification cannot establish that the saved
                # framing baseline caused any later projection change.
                self._camera_progress_baseline = None
            if (
                pending.kind is VerificationKind.CAMERA_POSE_CHANGED
                and pending.camera_key in {"up", "down"}
                and result.failure_kind
                is VerificationFailureKind.CONDITION_UNMET_AT_DEADLINE
                and self._pending_camera_step_id is not None
                and self._pending_camera_step_id == self._camera_recovery_step_id
            ):
                # Pitch support is telemetry- and client-dependent. A bounded
                # vertical hold that produced no pose delta is safe to abandon
                # after its transaction cleanup; suppress pitch for this exact
                # target and let the next fresh decision use only a remaining
                # causally supported axis or the exact actionable geometry.
                self._record_camera_no_effect_response(pending)
                self._camera_pitch_suppressed_step_id = self._pending_camera_step_id
                self._record_completed_camera_hold()
                self._camera_recovery_attempts += 1
                if self._camera_episode is not None:
                    self._camera_episode.actions_sent = (
                        self._camera_recovery_attempts
                        + self._camera_zoom_attempts
                    )
                    self._camera_episode.state = CameraAcquisitionState.FINE
                    self._camera_episode.pitch_limit_direction = pending.camera_key
                    self._camera_episode.pitch_limit_pose = pending.before_camera_pitch
                    self._camera_episode.retained_reason = (
                        "unchanged pose proved the pitch limit"
                    )
                self._pending_camera_step_id = None
                return
            if (
                pending.kind is VerificationKind.ITEM_QUANTITY_INCREASED
                and self.progress.phase is TaskPhase.VERIFY_LOGS
                and result.failure_kind
                is VerificationFailureKind.ITEM_QUANTITY_UNCHANGED_AT_DEADLINE
                and self._resource_no_yield_retries
                < RESOURCE_NO_YIELD_MAX_RETRIES
                and isinstance(self.progress.target_key, str)
                and bool(self.progress.target_key)
            ):
                self._resource_no_yield_retries += 1
                self._next_resource_suppression = (
                    self.progress.target_key,
                    "resource_no_yield",
                )
                self.progress.target_key = None
                self.progress.phase = TaskPhase.FIND_TREE
                if self._target_lock is not None:
                    self._clear_target_lock(
                        "resource produced no yield and entered bounded suppression"
                    )
                return
            if pending.kind is VerificationKind.CAMERA_POSE_CHANGED:
                self._pending_camera_step_id = None
                self._pending_camera_hold_millis = None
                self._pending_camera_yaw_error = None
                self._pending_camera_pitch_error = None
            elif pending.kind is VerificationKind.CAMERA_ZOOM_CHANGED:
                self._pending_camera_zoom_step_id = None
            self._set_blocked(f"verification failed: {result.reason}")
            return

        outcome = result.outcome.kind

        if pending.kind is VerificationKind.ITEM_QUANTITY_INCREASED:
            if outcome is not OutcomeKind.ITEM_QUANTITY_INCREASED:
                return self._block_verification_outcome(pending, outcome)
            self.progress.target_key = None
            self.progress.phase = TaskPhase.FIND_TREE
            self._resource_no_yield_retries = 0
            if self._target_lock is not None:
                self._clear_target_lock("resource interaction gained the produced item")
            return
        if pending.kind is VerificationKind.MOVED_CLOSER:
            if outcome not in {OutcomeKind.MOVED_CLOSER, OutcomeKind.ARRIVED}:
                return self._block_verification_outcome(pending, outcome)
            self._movement_verified = True
            self._route_settle_location = None
            self._route_settle_since_tick = None
            if self._target_lock is not None:
                self._clear_target_lock("route movement received fresh progress proof")
            return
        if pending.kind is VerificationKind.CAMERA_POSE_CHANGED:
            if outcome is not OutcomeKind.CAMERA_POSE_CHANGED:
                return self._block_verification_outcome(pending, outcome)
            if (
                self._pending_camera_step_id is None
                or self._pending_camera_step_id != self._camera_recovery_step_id
            ):
                self._set_blocked("camera pose proof arrived outside route recovery")
                return
            self._record_camera_pose_response(pending, result)
            self._record_completed_camera_hold()
            self._camera_recovery_attempts += 1
            if self._camera_episode is not None:
                self._camera_episode.actions_sent = (
                    self._camera_recovery_attempts + self._camera_zoom_attempts
                )
                self._camera_episode.state = CameraAcquisitionState.FINE
            self._pending_camera_step_id = None
            return
        if pending.kind is VerificationKind.CAMERA_ZOOM_CHANGED:
            if outcome is not OutcomeKind.CAMERA_ZOOM_CHANGED:
                return self._block_verification_outcome(pending, outcome)
            if (
                self._pending_camera_zoom_step_id is None
                or self._pending_camera_zoom_step_id
                != self._camera_recovery_step_id
            ):
                self._set_blocked("camera zoom proof arrived outside camera acquisition")
                return
            self._camera_zoom_attempts += 1
            if self._camera_episode is not None:
                self._camera_episode.zoom_actions_sent = self._camera_zoom_attempts
                self._camera_episode.actions_sent = (
                    self._camera_recovery_attempts + self._camera_zoom_attempts
                )
                self._camera_episode.state = CameraAcquisitionState.STABILIZING
                self._camera_episode.retained_reason = (
                    "fresh signed zoom response retained"
                )
            self._pending_camera_zoom_step_id = None
            return
        if pending.kind is VerificationKind.ROUTE_TRANSITION:
            if outcome is OutcomeKind.DIALOGUE_OPTION_APPEARED:
                if self._target_lock is not None:
                    self._clear_target_lock(
                        "route object produced the expected transition dialogue"
                    )
                self.progress.resume_phase = self.progress.phase
                self.progress.phase = TaskPhase.STAIR_DIALOGUE
                return
            if outcome is not OutcomeKind.PLANE_CHANGED:
                return self._block_verification_outcome(pending, outcome)
            self.progress.route_index += 1
            if self._target_lock is not None:
                self._clear_target_lock("route transition received fresh plane proof")
            route = self._current_route()
            if route is not None and self.progress.route_index >= len(route):
                self._finish_route()
            return
        if pending.kind is VerificationKind.PLANE_CHANGED:
            if outcome is not OutcomeKind.PLANE_CHANGED:
                return self._block_verification_outcome(pending, outcome)
            if self.progress.phase is not TaskPhase.STAIR_DIALOGUE or self.progress.resume_phase is None:
                self._set_blocked("plane proof arrived outside stair dialogue")
                return
            self.progress.phase = self.progress.resume_phase
            self.progress.resume_phase = None
            self.progress.route_index += 1
            if self._target_lock is not None:
                self._clear_target_lock("dialogue route transition received plane proof")
            route = self._current_route()
            if route is not None and self.progress.route_index >= len(route):
                self._finish_route()
            return
        if pending.kind is VerificationKind.INTERFACE_OPENED:
            if outcome is not OutcomeKind.INTERFACE_OPENED:
                return self._block_verification_outcome(pending, outcome)
            self.progress.phase = TaskPhase.DEPOSIT_LOGS
            if self._target_lock is not None:
                self._clear_target_lock("bank interface open was freshly verified")
            return
        if pending.kind is VerificationKind.ITEM_QUANTITY_EQUALS:
            if outcome is not OutcomeKind.ITEM_QUANTITY_EQUALS:
                return self._block_verification_outcome(pending, outcome)
            self.progress.phase = TaskPhase.CLOSE_BANK
            return
        if pending.kind is VerificationKind.INTERFACE_CLOSED:
            if outcome is not OutcomeKind.INTERFACE_CLOSED:
                return self._block_verification_outcome(pending, outcome)
            self.progress.phase = TaskPhase.NAVIGATE_TO_TREES
            self.progress.route_index = 0
            return
        self._set_blocked(f"unsupported verification result: {pending.kind.value}")

    def discard_pending_action(
        self, reason: str, *, target_invalidated: bool = True
    ) -> None:
        """Discard one proposal that the input boundary proved was never sent.

        This is not a failed verification: no activation occurred, so there is
        no game-state effect to verify.  Restore only the explicit phase that
        can safely produce a new proposal from a fresh observation.
        """

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("discard reason must be non-empty text")
        if not isinstance(target_invalidated, bool):
            raise TypeError("target_invalidated must be bool")
        pending = self.progress.pending
        if pending is None:
            raise RuntimeError("no pending action can be discarded")

        discardable_kinds = frozenset(
            {
                VerificationKind.ITEM_QUANTITY_INCREASED,
                VerificationKind.ITEM_QUANTITY_EQUALS,
                VerificationKind.MOVED_CLOSER,
                VerificationKind.PLANE_CHANGED,
                VerificationKind.INTERFACE_OPENED,
                VerificationKind.INTERFACE_CLOSED,
                VerificationKind.ROUTE_TRANSITION,
                VerificationKind.CAMERA_POSE_CHANGED,
                VerificationKind.CAMERA_ZOOM_CHANGED,
            }
        )
        if pending.kind not in discardable_kinds:
            raise RuntimeError(
                f"unsupported unsent verification kind: {pending.kind.value}"
            )

        self.progress.pending = None
        if pending.kind is VerificationKind.ITEM_QUANTITY_INCREASED:
            if target_invalidated:
                if self.progress.target_key is not None:
                    self._next_resource_suppression = (
                        self.progress.target_key,
                        "preactivation_target_invalidated",
                    )
                self.progress.target_key = None
                self.progress.phase = TaskPhase.FIND_TREE
            else:
                # Cursor-state recovery invalidates the semantic target too,
                # but unlike target-evidence failure it does not suppress that
                # target. Fresh recognition may select it again legitimately.
                self.progress.target_key = None
                self.progress.phase = TaskPhase.FIND_TREE
            if self._target_lock is not None:
                self._clear_target_lock(
                    "unsent resource activation discarded target continuity"
                )
        elif pending.kind is VerificationKind.ITEM_QUANTITY_EQUALS:
            self.progress.phase = TaskPhase.DEPOSIT_LOGS
        elif pending.kind in {
            VerificationKind.MOVED_CLOSER,
            VerificationKind.ROUTE_TRANSITION,
            VerificationKind.PLANE_CHANGED,
            VerificationKind.INTERFACE_OPENED,
        }:
            if self._target_lock is not None:
                self._clear_target_lock(
                    "unsent object or route activation discarded target continuity"
                )
        elif pending.kind is VerificationKind.CAMERA_POSE_CHANGED:
            self._pending_camera_step_id = None
            self._pending_camera_hold_millis = None
            self._pending_camera_yaw_error = None
            self._pending_camera_pitch_error = None
            self._camera_progress_baseline = None
        elif pending.kind is VerificationKind.CAMERA_ZOOM_CHANGED:
            self._pending_camera_zoom_step_id = None
            self._camera_progress_baseline = None

    def _block_verification_outcome(
        self, pending: VerificationSpec, outcome: OutcomeKind
    ) -> None:
        self._set_blocked(
            f"unexpected {outcome.value} outcome for {pending.kind.value}"
        )

    def _find_tree(self, observation: Observation) -> Decision:
        if self._target_lock is not None:
            self._clear_target_lock(
                "resource discovery began without an active target episode"
            )
        suppression = self._next_resource_suppression
        self._next_resource_suppression = None
        suppressed_key = suppression[0] if suppression is not None else None
        suppression_code = suppression[1] if suppression is not None else None
        if self.progress.cycles_completed >= self.binding.profile.cycle_goal:
            self.progress.phase = TaskPhase.COMPLETE
            return self._wait(observation, "profile cycle goal is complete")
        work_area = self.definition.resource.work_area
        if observation.inventory.full:
            if (
                self.definition.inventory.require_produced_item_when_full
                and observation.inventory.quantity(self._produced_item_id) == 0
            ):
                return self._block(
                    observation,
                    "inventory is full without the definition's produced item",
                )
            outside_work_area = bool(
                observation.plane != work_area.anchor.plane
                or observation.location.distance_to(work_area.anchor)
                > work_area.radius
            )
            # The configured work area intentionally overlaps the first part
            # of the bank route.  A fresh full-inventory run can therefore be
            # several validated steps along the route while still being
            # "inside" the radial resource area.  Prefer exact definition-
            # owned route evidence there; only preserve the normal freshly-
            # filled transition at step zero.
            resume_index = self._bank_route_resume_index(observation)
            if resume_index == 0 and not outside_work_area:
                resume_index = None
            self.progress.phase = TaskPhase.NAVIGATE_TO_BANK
            self.progress.route_index = 0 if resume_index is None else resume_index
            if resume_index is not None:
                self.progress.target_key = None
                self.progress.pending = None
                self._restart_reconciled_without_cycle_credit = True
                self._movement_verified = False
                self._route_settle_location = None
                self._route_settle_since_tick = None
                self._reset_camera_recovery()
                return self._wait(
                    observation,
                    "reobserved full inventory on the validated bank route",
                )
            return self._wait(observation, "inventory is full; fixed bank route selected")
        return_resume_index = self._return_route_resume_index(observation)
        bank = self.definition.bank
        inside_bank_area = bool(
            observation.plane == bank.anchor.plane
            and observation.location.distance_to(bank.anchor)
            <= bank.interaction_radius
        )
        if (
            return_resume_index is not None
            and return_resume_index < len(self.definition.route_to_resource.steps) - 1
            and not inside_bank_area
        ):
            self.progress.target_key = None
            self.progress.pending = None
            self.progress.phase = TaskPhase.NAVIGATE_TO_TREES
            self.progress.route_index = return_resume_index
            self._restart_reconciled_without_cycle_credit = True
            self._movement_verified = False
            self._route_settle_location = None
            self._route_settle_since_tick = None
            self._reset_camera_recovery()
            return self._wait(
                observation,
                "reobserved empty inventory on the validated return route",
            )
        if (
            observation.plane != work_area.anchor.plane
            or observation.location.distance_to(work_area.anchor) > work_area.radius
        ):
            resume_index = return_resume_index
            inventory_empty = bool(
                observation.inventory.known
                and observation.inventory.occupied_slots == 0
                and not observation.inventory.items
            )
            if (
                inventory_empty
                and inside_bank_area
                and not observation.widgets.bank_known
            ):
                # Inside the bank UI's interaction area, bank_open=false is not
                # closure proof unless bank_known is also true. Do not let an
                # overlapping route step bypass that uncertainty.
                resume_index = None
            if (
                inventory_empty
                and inside_bank_area
                and observation.widgets.bank_pin_open
            ):
                return self._block(
                    observation,
                    "bank PIN handling is out of scope",
                )
            if (
                inventory_empty
                and observation.widgets.bank_known
                and inside_bank_area
                and (
                    observation.widgets.bank_open
                    or resume_index is None
                )
            ):
                self.progress.target_key = None
                self.progress.pending = None
                self.progress.phase = (
                    TaskPhase.CLOSE_BANK
                    if observation.widgets.bank_open
                    else TaskPhase.NAVIGATE_TO_TREES
                )
                self.progress.route_index = 0
                self._restart_reconciled_without_cycle_credit = True
                self._movement_verified = False
                self._route_settle_location = None
                self._route_settle_since_tick = None
                self._reset_camera_recovery()
                reason = (
                    "reobserved empty inventory with an open bank at the "
                    "validated bank interaction area"
                    if observation.widgets.bank_open
                    else "reobserved empty inventory at the validated bank "
                    "return-route start"
                )
                return self._wait(observation, reason)
            if resume_index is not None:
                self.progress.target_key = None
                self.progress.pending = None
                self.progress.phase = TaskPhase.NAVIGATE_TO_TREES
                self.progress.route_index = resume_index
                self._restart_reconciled_without_cycle_credit = True
                self._movement_verified = False
                self._route_settle_location = None
                self._route_settle_since_tick = None
                self._reset_camera_recovery()
                return self._wait(
                    observation,
                    "reobserved empty inventory on the validated return route",
                )
            return self._block(observation, "player is outside the supported work area")

        candidates, rejected = self._classify_trees(observation)
        if suppressed_key is not None:
            assert suppression_code is not None
            suppressed = tuple(
                target for target in candidates if target.key == suppressed_key
            )
            candidates = tuple(
                target for target in candidates if target.key != suppressed_key
            )
            rejected += tuple(
                (target, (suppression_code,))
                for target in suppressed
            )
        self._prune_resource_camera_suppressions(observation.tick)
        recently_failed = []
        retained = []
        for target in candidates:
            if target.key not in self._resource_camera_suppressions:
                retained.append(target)
                continue
            if self._resource_selection_rank(observation, target)[0] == 0:
                # Fresh geometry can make a formerly difficult exact target
                # directly usable before the bounded suppression expires.
                self._resource_camera_suppressions.pop(target.key, None)
                retained.append(target)
                continue
            recently_failed.append(target)
        candidates = tuple(retained)
        rejected += tuple(
            (target, ("camera_framing_recently_failed",))
            for target in recently_failed
        )
        evidence = self._object_decision_evidence(
            observation,
            action=self.definition.resource.selector.action,
            eligible=candidates,
            rejected=rejected,
        )
        if not candidates:
            return self._wait(
                observation,
                (
                    "waiting for bounded recent camera-failure suppression"
                    if recently_failed
                    else "no exact configured resource identity is observed for acquisition"
                ),
                evidence=evidence,
            )
        selected = candidates[0]
        if not self._lock_target(
            observation,
            selected,
            action=self.definition.resource.selector.action,
            context="resource",
        ):
            return self._block(
                observation,
                "resource target lock rejected contradictory immutable identity",
                evidence=self._object_decision_evidence(
                    observation,
                    action=self.definition.resource.selector.action,
                    rejected=((selected, ("contradictory_target_identity",)),),
                ),
            )
        self.progress.target_key = selected.key
        self.progress.phase = TaskPhase.CHOP
        return self._wait(
            observation,
            "selected best fresh exact configured resource",
            evidence=self._object_decision_evidence(
                observation,
                action=self.definition.resource.selector.action,
                selected=selected,
                eligible=candidates,
                rejected=rejected,
            ),
        )

    def _return_route_resume_index(
        self, observation: Observation
    ) -> int | None:
        if observation.inventory.occupied_slots != 0 or observation.inventory.items:
            return None
        return self._polyline_route_resume_index(
            self.definition.route_to_resource,
            observation,
        )

    def _bank_route_resume_index(
        self, observation: Observation
    ) -> int | None:
        if not observation.inventory.full:
            return None
        return self._polyline_route_resume_index(
            self.definition.route_to_bank,
            observation,
        )

    def _polyline_route_resume_index(
        self,
        route: FixedRoute,
        observation: Observation,
    ) -> int | None:
        """Recover a fresh task from bounded definition-owned route geometry."""

        exact_matches = [
            index
            for index, step in enumerate(route.steps)
            if observation.plane == step.location.plane
            and observation.location.distance_to(step.location)
            <= step.arrival_radius
        ]
        if exact_matches:
            return max(exact_matches)
        try:
            progress = route_progress(
                route,
                observation.location,
                limits=self._route_selection_limits(),
            )
        except ValueError:
            return None
        if (
            progress.distance_along_route <= 1e-9
            or
            progress.lateral_deviation
            > self.behavior.config.route_recovery_radius_tiles
        ):
            return None
        return progress.segment_index

    def _chop(self, observation: Observation) -> Decision:
        if observation.inventory.full:
            self.progress.target_key = None
            self.progress.phase = TaskPhase.NAVIGATE_TO_BANK
            self.progress.route_index = 0
            if self._target_lock is not None:
                self._clear_target_lock("inventory filled before target activation")
            return self._wait(observation, "inventory filled before the chop")

        target = observation.object_by_key(self.progress.target_key)
        if target is None and self._target_lock is not None:
            return self._handle_locked_target_omission(observation)
        target_rejections = (
            self._tree_identity_rejection_codes(target)
            if target is not None
            else ()
        )
        if target is not None and self._target_lock is None:
            # Preserve direct-constructor and older receipt behavior by
            # establishing continuity only after the legacy exact row appears.
            self._lock_target(
                observation,
                target,
                action=self.definition.resource.selector.action,
                context="resource",
            )
        elif target is not None and not self._touch_target_lock(observation, target):
            return self._block(
                observation,
                "locked resource returned contradictory immutable identity",
                evidence=self._object_decision_evidence(
                    observation,
                    action=self.definition.resource.selector.action,
                    rejected=((target, ("contradictory_target_identity",)),),
                ),
            )
        if (
            target is not None
            and target_rejections == ("action_unavailable",)
            and self._census_is_explicitly_incomplete(
                self._scene_census(observation)
            )
        ):
            if self._target_lock is not None:
                self._target_lock.retained_reason = (
                    "same immutable target retained while an incomplete row "
                    "omits transient action readiness"
                )
            return self._wait(
                observation,
                "waiting for complete action readiness on the locked resource",
                evidence=self._object_decision_evidence(
                    observation,
                    action=self.definition.resource.selector.action,
                    rejected=(
                        (
                            target,
                            target_rejections
                            + ("incomplete_census_for_activation",),
                        ),
                    ),
                ),
            )
        if target is None or target_rejections:
            evidence = (
                self._object_decision_evidence(
                    observation,
                    action=self.definition.resource.selector.action,
                    rejected=((target, target_rejections),),
                )
                if target is not None
                else DecisionEvidence()
            )
            self.progress.target_key = None
            self.progress.phase = TaskPhase.FIND_TREE
            self._reset_camera_recovery()
            if self._target_lock is not None:
                self._clear_target_lock(
                    "fresh target identity no longer matches the resource contract"
                )
            return self._wait(
                observation,
                "selected resource is no longer exactly actionable",
                evidence=evidence,
            )

        camera = self._maybe_reframe_object(
            observation,
            target,
            action=self.definition.resource.selector.action,
        )
        if camera is not None:
            return camera

        geometry_rejections = self._geometry_rejection_codes(target)
        selector_rows: dict[str, NearbyObject] = {}
        for object_id in sorted(self.definition.resource.selector.object_ids):
            for candidate in observation.objects_by_id(object_id):
                if not self._tree_identity_rejection_codes(candidate):
                    selector_rows[candidate.key] = candidate
        aim_occluded = target.key in self._tree_aim_occluded_keys(
            tuple(selector_rows[key] for key in sorted(selector_rows))
        )
        if geometry_rejections or aim_occluded:
            codes = geometry_rejections or ("aim_point_occluded",)
            return self._wait(
                observation,
                "waiting for fresh actionable resource geometry after camera acquisition",
                evidence=self._object_decision_evidence(
                    observation,
                    action=self.definition.resource.selector.action,
                    rejected=((target, codes),),
                ),
            )

        verification = VerificationSpec(
            VerificationKind.ITEM_QUANTITY_INCREASED,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick
                + self.definition.verification.resource_deadline_ticks
            ),
            item_id=self._produced_item_id,
            before_quantity=observation.inventory.quantity(self._produced_item_id),
            source_session_id=observation.session_id,
        )
        decision = self._emit_action(
            observation,
            ActionKind.INTERACT_OBJECT,
            f"{self.definition.resource.selector.action} configured resource",
            "interact with exact configured resource",
            self.definition.resource.selector.action,
            verification,
            pending_phase=TaskPhase.VERIFY_LOGS,
            target=target,
            evidence=self._evidence_with_camera(
                self._object_decision_evidence(
                    observation,
                    action=self.definition.resource.selector.action,
                    selected=target,
                    eligible=(target,),
                )
            ),
        )
        self._reset_camera_recovery()
        return decision

    def _navigate(
        self, observation: Observation, route: FixedRoute
    ) -> Decision:
        self._prepare_route_diagnostics(route)
        steps = route.steps
        if self._movement_verified:
            if self._route_settle_location != observation.location:
                self._route_settle_location = observation.location
                self._route_settle_since_tick = observation.tick
                return self._wait(observation, "waiting for player location to settle")
            assert self._route_settle_since_tick is not None
            if (
                observation.tick - self._route_settle_since_tick
                < self.definition.verification.route_stable_ticks
            ):
                return self._wait(observation, "waiting for player location to settle")
            if self._pending_route_start_progress is not None:
                observed_progress = route_progress(
                    route,
                    observation.location,
                    previous=self._pending_route_start_progress,
                    limits=self._route_selection_limits(),
                )
                self._last_route_actual_progress_delta = observed_progress.progress_delta
                self._last_route_progress = observed_progress
                self._pending_route_start_progress = None
            self._movement_verified = False
            self._route_settle_location = None
            self._route_settle_since_tick = None

        if self.progress.route_index >= len(steps):
            self._finish_route()
            return self._wait(observation, "fixed route complete")

        step = steps[self.progress.route_index]
        if observation.plane != step.location.plane:
            return self._block(observation, f"wrong plane for route step {step.step_id}")

        if step.is_walk:
            if observation.location.distance_to(step.location) <= step.arrival_radius:
                self._reset_camera_recovery()
                self.progress.route_index += 1
                if self.progress.route_index >= len(steps):
                    self._finish_route()
                return self._wait(observation, f"arrived at route step {step.step_id}")

            current_target = observation.object_by_key(step.target_key)
            if (
                current_target is not None
                and not self._walk_projection_identity_matches(current_target, step)
            ):
                return self._block(
                    observation,
                    f"route projection identity mismatch for {step.step_id}",
                    evidence=self._object_decision_evidence(
                        observation,
                        action=step.action,
                        rejected=(
                            (
                                current_target,
                                self._walk_projection_rejection_codes(current_target, step),
                            ),
                        ),
                    ),
                )

            candidate_support = self._route_candidate_support(route, observation)
            selection = select_farthest_useful_route_target(
                route,
                observation.location,
                candidate_support,
                previous_progress=self._last_route_progress,
                limits=self._route_selection_limits(),
            )
            self._last_route_selection = selection
            supported_indices = {item.route_index for item in candidate_support}
            self._route_candidate_rejections = tuple(
                RouteCandidateRejectionEvidence(
                    step_id=route.steps[item.route_index].step_id,
                    rejection_codes=item.reasons,
                )
                for item in selection.rejections
                if item.route_index in supported_indices
            )
            self._last_route_progress = selection.progress
            self._capture_route_overlay(route, observation, selection)
            episode = self._camera_episode
            if episode is not None and episode.context == "route":
                locked_target = observation.object_by_key(
                    episode.locked_target_key
                )
                if locked_target is None:
                    return self._handle_locked_target_omission(observation)
                if not self._touch_target_lock(observation, locked_target):
                    episode.state = CameraAcquisitionState.INVALIDATED
                    episode.retained_reason = (
                        "locked route target returned contradictory immutable identity"
                    )
                    return self._block(
                        observation,
                        episode.retained_reason,
                        evidence=self._evidence_with_camera(
                            self._object_decision_evidence(
                                observation,
                                action=episode.locked_target_action,
                                rejected=((locked_target, ("contradictory_target_identity",)),),
                            )
                        ),
                    )
                locked_support = next(
                    (
                        item
                        for item in candidate_support
                        if item.route_index == episode.route_index
                    ),
                    None,
                )
                locked_shortcut_rejected = any(
                    rejection.route_index == episode.route_index
                    and "shortcut_unsupported" in rejection.reasons
                    for rejection in selection.rejections
                )
                locked_safe = bool(
                    locked_support is not None
                    and locked_support.plane_supported
                    and locked_support.scene_supported
                    and locked_support.collision_supported
                    and locked_support.ui_clear
                    # The selector permits a short correction without
                    # positive shortcut evidence. Reuse its fresh verdict for
                    # the locked target so the same safe episode is not
                    # recreated on every observation.
                    and not locked_shortcut_rejected
                    and (
                        locked_support.projectable
                        or locked_support.camera_adjustable
                    )
                )
                if not locked_safe:
                    # Fresh route support is an explicit unsafe-target release
                    # condition. Replanning may now select another exact
                    # definition-owned route point without oscillating.
                    self._reset_camera_recovery()
                    if self._target_lock is not None:
                        self._clear_target_lock(
                            "fresh route support invalidated the locked shortcut"
                        )
                    episode = None
            if episode is not None and episode.context == "route":
                locked_index = episode.route_index
                if (
                    locked_index is None
                    or locked_index < self.progress.route_index
                    or locked_index >= len(steps)
                ):
                    episode.state = CameraAcquisitionState.INVALIDATED
                    episode.retained_reason = "locked route target left the active route"
                    return self._block(
                        observation,
                        episode.retained_reason,
                        evidence=self._evidence_with_camera(DecisionEvidence()),
                    )
                selected_index = locked_index
                step = steps[selected_index]
                target = observation.object_by_key(episode.locked_target_key)
                if target is None:
                    return self._handle_locked_target_omission(observation)
                if not self._camera_episode_target_matches(episode, target):
                    episode.state = CameraAcquisitionState.INVALIDATED
                    episode.retained_reason = "locked route target lost authoritative identity"
                    return self._block(
                        observation,
                        episode.retained_reason,
                        evidence=self._evidence_with_camera(
                            self._object_decision_evidence(
                                observation,
                                action=episode.locked_target_action,
                                rejected=((target, ("contradictory_target_identity",)),),
                            )
                        ),
                    )
            elif selection.selected_index is None or selection.selected_step is None:
                legacy_geometry = (
                    current_target.geometry if current_target is not None else None
                )
                if (
                    current_target is None
                    or legacy_geometry is None
                    or legacy_geometry.collision_supported is not None
                    or legacy_geometry.scene_supported is not None
                    or legacy_geometry.shortcut_clear is not None
                ):
                    self._route_projection_wait_since_tick = None
                    return self._wait(
                        observation,
                        f"waiting for supported route lookahead from {step.step_id}",
                    )
                selected_index = self.progress.route_index
                target = current_target
            else:
                selected_index = selection.selected_index
                step = selection.selected_step
                target = observation.object_by_key(step.target_key)
            if target is None:
                self._route_projection_wait_since_tick = None
                return self._wait(
                    observation,
                    f"waiting for route projection {step.step_id}",
                )
            if not self._walk_projection_identity_matches(target, step):
                return self._block(
                    observation,
                    f"route projection identity mismatch for {step.step_id}",
                    evidence=self._object_decision_evidence(
                        observation,
                        action=step.action,
                        rejected=((target, self._walk_projection_rejection_codes(target, step)),),
                    ),
                )
            framing = self._classify_route_camera(observation, step, target)
            if framing is not None and framing.action != "none":
                return self._recover_route_projection(
                    observation,
                    step,
                    target,
                    framing=framing,
                    route_index=selected_index,
                )
            if not self._has_geometry(target):
                return self._recover_route_projection(
                    observation,
                    step,
                    target,
                    framing=framing,
                    route_index=selected_index,
                )
            self.progress.route_index = selected_index
            self._pending_route_start_progress = selection.progress
            verification = VerificationSpec(
                VerificationKind.MOVED_CLOSER,
                before_tick=observation.tick,
                deadline_tick=(
                    observation.tick
                    + self.definition.verification.movement_deadline_ticks
                ),
                before_location=observation.location,
                target_location=step.location,
                source_session_id=observation.session_id,
                target_radius=step.arrival_radius,
            )
            decision = self._emit_action(
                observation, ActionKind.WALK, f"Walk to {step.step_id}",
                f"walk selected route target {step.step_id}", "Walk here",
                verification, target=target,
                evidence=self._evidence_with_camera(
                    self._object_decision_evidence(
                        observation,
                        action=step.action,
                        selected=target,
                        eligible=(target,),
                    )
                ),
            )
            self._reset_camera_recovery()
            return decision

        if self._target_lock is not None and self._target_lock.context == "route":
            locked_route_object = observation.object_by_key(self._target_lock.key)
            if locked_route_object is None:
                return self._handle_locked_target_omission(observation)
            if not self._touch_target_lock(observation, locked_route_object):
                return self._block(
                    observation,
                    "locked route object returned contradictory immutable identity",
                    evidence=self._object_decision_evidence(
                        observation,
                        action=step.action,
                        rejected=((locked_route_object, ("contradictory_target_identity",)),),
                    ),
                )

        route_targets, rejected, route_actions = self._classify_route_objects(
            observation, step
        )
        if not route_targets:
            return self._wait(
                observation,
                f"waiting for strict route object {step.step_id}",
                evidence=self._object_decision_evidence(
                    observation,
                    action=step.action,
                    rejected=rejected,
                    candidate_actions=route_actions,
                ),
            )
        target = route_targets[0]
        if self._target_lock is not None and self._target_lock.context == "route":
            locked = observation.object_by_key(self._target_lock.key)
            if locked is None:
                return self._handle_locked_target_omission(observation)
            if locked not in route_targets:
                return self._block(
                    observation,
                    "locked route object no longer satisfies the exact route contract",
                    evidence=self._object_decision_evidence(
                        observation,
                        action=step.action,
                        rejected=((locked, ("contradictory_target_identity",)),),
                    ),
                )
            target = locked
        if (
            self._camera_episode is not None
            and self._camera_episode.context == "interaction"
        ):
            locked = observation.object_by_key(
                self._camera_episode.locked_target_key
            )
            if locked is None or not self._camera_episode_target_matches(
                self._camera_episode,
                locked,
            ):
                self._camera_episode.state = CameraAcquisitionState.INVALIDATED
                self._camera_episode.retained_reason = (
                    "locked route object became invalid during camera acquisition"
                )
                decision = self._wait(
                    observation,
                    self._camera_episode.retained_reason,
                    evidence=self._evidence_with_camera(DecisionEvidence()),
                )
                self._reset_camera_recovery()
                return decision
            target = locked
        route_option = route_actions.get(target.key)
        if route_option is None:
            return self._block(
                observation,
                f"route action unavailable for {step.step_id}",
                evidence=self._object_decision_evidence(
                    observation,
                    action=step.action,
                    rejected=((target, ("action_unavailable",)), *rejected),
                ),
            )
        camera = self._maybe_reframe_object(
            observation,
            target,
            action=route_option,
        )
        if camera is not None:
            return camera
        route_geometry_rejections = self._geometry_rejection_codes(target)
        if route_geometry_rejections:
            return self._wait(
                observation,
                f"waiting for fresh actionable route-object geometry {step.step_id}",
                evidence=self._object_decision_evidence(
                    observation,
                    action=route_option,
                    rejected=((target, route_geometry_rejections),),
                    candidate_actions=route_actions,
                ),
            )
        verification = VerificationSpec(
            VerificationKind.ROUTE_TRANSITION,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick + self.definition.verification.action_deadline_ticks
            ),
            before_location=observation.location,
            expected_plane=step.expected_plane,
            source_session_id=observation.session_id,
            dialogue_prompt_contains=(
                self.definition.verification.transition_dialogue_prompt_contains
            ),
            dialogue_option_contains=(
                self.definition.verification.transition_up_option_contains
                if step.expected_plane > observation.plane
                else self.definition.verification.transition_down_option_contains
            ),
        )
        decision = self._emit_action(
            observation, ActionKind.INTERACT_OBJECT,
            f"{route_option} {step.object_name}",
            f"interact with fixed route step {step.step_id}", route_option,
            verification, target=target,
            evidence=self._evidence_with_camera(
                self._object_decision_evidence(
                    observation,
                    action=route_option,
                    selected=target,
                    eligible=route_targets,
                    rejected=rejected,
                    candidate_actions=route_actions,
                )
            ),
        )
        self._reset_camera_recovery()
        return decision

    def _recover_route_projection(
        self,
        observation: Observation,
        step: FixedRouteStep,
        target: NearbyObject,
        *,
        framing: CameraFramingDecision | None = None,
        route_index: int | None = None,
    ) -> Decision:
        # Proactive framing can require camera movement even when the
        # projection geometry itself is otherwise valid.  Evidence still
        # needs a concrete reason before it may classify the candidate as
        # rejected.
        projection_rejection_codes = self._geometry_rejection_codes(target) or (
            "camera_reframe_required",
        )
        if self._camera_episode is None:
            self._start_camera_episode(
                observation,
                target,
                context="route",
                logical_target_id=step.step_id,
                action=step.action,
                route_index=route_index,
            )
        elif (
            self._camera_episode.logical_target_id != step.step_id
            or not self._camera_episode_target_matches(
                self._camera_episode,
                target,
            )
        ):
            self._camera_episode.state = CameraAcquisitionState.INVALIDATED
            self._camera_episode.retained_reason = (
                "camera controller refused an alternate route target during an active episode"
            )
            return self._block(
                observation,
                self._camera_episode.retained_reason,
                evidence=self._evidence_with_camera(DecisionEvidence()),
            )
        self._update_camera_episode_goal(
            observation,
            step.location,
            framing,
        )
        if framing is not None and framing.zoom_required_but_unavailable:
            return self._camera_zoom_decision(
                observation,
                target,
                framing,
                logical_target_id=step.step_id,
                action=step.action,
                evidence=self._object_decision_evidence(
                    observation,
                    action=step.action,
                    rejected=((target, projection_rejection_codes),),
                ),
            )
        if framing is not None and framing.pitch_valid is False:
            if observation.camera_pitch is None:
                return self._wait(
                    observation,
                    f"waiting for a valid camera pitch for route projection {step.step_id}",
                    evidence=self._evidence_with_camera(
                        self._object_decision_evidence(
                            observation,
                            action=step.action,
                            rejected=((target, projection_rejection_codes),),
                        )
                    ),
                )
            assert self._camera_episode is not None
            self._camera_episode.state = CameraAcquisitionState.INVALIDATED
            self._camera_episode.retained_reason = (
                "camera pitch is outside the supported range"
            )
            return self._block(
                observation,
                self._camera_episode.retained_reason,
                evidence=self._evidence_with_camera(
                    self._object_decision_evidence(
                        observation,
                        action=step.action,
                        rejected=((target, projection_rejection_codes),),
                    )
                ),
            )
        if framing is not None:
            self._consume_camera_framing_progress(framing)
        if self._route_projection_wait_since_tick is None:
            self._route_projection_wait_since_tick = observation.tick
            return self._wait(
                observation,
                f"waiting for stable route projection {step.step_id}",
                evidence=self._evidence_with_camera(
                    self._object_decision_evidence(
                        observation,
                        action=step.action,
                        rejected=((target, projection_rejection_codes),),
                    )
                ),
            )
        if observation.tick <= self._route_projection_wait_since_tick:
            return self._wait(
                observation,
                f"waiting for a later route projection {step.step_id}",
                evidence=self._evidence_with_camera(
                    self._object_decision_evidence(
                        observation,
                        action=step.action,
                        rejected=((target, projection_rejection_codes),),
                    )
                ),
            )
        correction_limit_reached = (
            self._camera_recovery_attempts
            >= self.behavior.config.camera_max_corrections
        )
        recovery_stalled = (
            not correction_limit_reached
            and self._camera_framing_progress_stalled()
        )
        if correction_limit_reached or recovery_stalled:
            if self._camera_episode is not None:
                self._camera_episode.state = (
                    CameraAcquisitionState.NON_IMPROVING
                    if recovery_stalled
                    else CameraAcquisitionState.EXHAUSTED
                )
                self._camera_episode.retained_reason = (
                    "bounded camera response did not improve framing"
                    if recovery_stalled
                    else "coarse and fine camera actions were exhausted"
                )
            rejection_codes = (
                ("camera_framing_non_improving",)
                if recovery_stalled
                else projection_rejection_codes
            )
            reason = (
                f"camera framing made no progress for route projection {step.step_id}"
                if recovery_stalled
                else f"camera recovery exhausted for route projection {step.step_id}"
            )
            return self._block(
                observation,
                reason,
                evidence=self._evidence_with_camera(
                    self._object_decision_evidence(
                        observation,
                        action=step.action,
                        rejected=((target, rejection_codes),),
                    )
                ),
            )
        if observation.camera_yaw is None or observation.geometry_frame_id is None:
            return self._block(
                observation,
                f"camera pose unavailable for route projection {step.step_id}",
                evidence=self._evidence_with_camera(
                    self._object_decision_evidence(
                        observation,
                        action=step.action,
                        rejected=((target, projection_rejection_codes),),
                    )
                ),
            )
        usable_pitch = self._usable_camera_pitch(
            step.step_id,
            observation.camera_pitch,
        )
        direction = self._camera_correction_direction(
            observation.location,
            step.location,
            observation.camera_yaw,
            usable_pitch,
            framing,
        )
        if direction is None and framing is not None and framing.action != "none":
            direction = self._camera_search_direction(
                step.target_key,
                camera_pitch=usable_pitch,
            )
        if direction is None:
            return self._block(
                observation,
                f"route projection {step.step_id} is unavailable at the aligned camera yaw",
                evidence=self._evidence_with_camera(
                    self._object_decision_evidence(
                        observation,
                        action=step.action,
                        rejected=((target, projection_rejection_codes),),
                    )
                ),
            )
        veto_reason = self._camera_direction_veto_reason(
            direction,
            current_pitch=observation.camera_pitch,
        )
        if veto_reason is not None:
            assert self._camera_episode is not None
            self._camera_episode.state = CameraAcquisitionState.NON_IMPROVING
            self._camera_episode.retained_reason = veto_reason
            self._camera_non_improving_corrections = (
                CAMERA_NON_IMPROVING_CORRECTION_LIMIT
            )
            return self._wait(
                observation,
                veto_reason,
                evidence=self._evidence_with_camera(
                    self._object_decision_evidence(
                        observation,
                        action=step.action,
                        rejected=((target, projection_rejection_codes),),
                    )
                ),
            )
        hold_millis, yaw_error, pitch_error = self._select_camera_hold(
            observation,
            step.location,
            framing,
            direction,
        )
        if framing is not None:
            framing = replace(framing, hold_millis=hold_millis)
            self._last_camera_framing = framing
        assert self._camera_episode is not None
        self._camera_episode.state = (
            CameraAcquisitionState.COARSE
            if self._camera_recovery_attempts == 0
            else CameraAcquisitionState.FINE
        )
        camera_decision_id = self._next_behavior_decision_id(
            "camera",
            step.target_key,
            observation.tick,
        )
        timing = self.behavior.timing(
            camera_decision_id,
            camera_moved=True,
        )
        self._last_timing_decision = timing
        verification = VerificationSpec(
            VerificationKind.CAMERA_POSE_CHANGED,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick
                + self.definition.verification.action_deadline_ticks
            ),
            before_location=observation.location,
            source_session_id=observation.session_id,
            before_camera_yaw=observation.camera_yaw,
            before_camera_pitch=observation.camera_pitch,
            before_geometry_frame_id=observation.geometry_frame_id,
            camera_key=direction,
        )
        constraint = CameraConstraint(
            target_key=step.target_key,
            target_location=step.location,
            source_location=observation.location,
            source_geometry_frame_id=observation.geometry_frame_id,
            before_yaw=observation.camera_yaw,
            direction=direction,
            hold_millis=hold_millis,
            before_pitch=observation.camera_pitch,
            desired_region=(framing.desired_region if framing is not None else None),
            framing_classification=(
                framing.classification if framing is not None else "not_visible"
            ),
        )
        self.progress.pending = verification
        self._pending_camera_step_id = step.step_id
        self._pending_camera_hold_millis = hold_millis
        self._pending_camera_yaw_error = yaw_error
        self._pending_camera_pitch_error = pitch_error
        self._camera_progress_baseline = framing
        return Decision(
            self.progress.phase.value,
            (
                f"turn camera {direction} for route projection {step.step_id} "
                f"({self._camera_recovery_attempts + 1}/"
                f"{self.behavior.config.camera_max_corrections})"
            ),
            Action(
                ActionKind.CAMERA_HOLD,
                f"Turn camera toward {step.step_id}",
                observation.tick,
                option=f"Turn camera {direction}",
                target_key=target.key,
                target_name=target.name,
                target_id=target.object_id,
                key=direction,
                key_hold_millis=hold_millis,
                verification=verification,
                target_param0=target.scene_x,
                target_param1=target.scene_y,
                source_session_id=observation.session_id,
                task_constraints=TaskConstraints(camera=constraint),
                decision_id=camera_decision_id,
                behavior_seed=self.behavior.seed,
                pre_move_delay_seconds=timing.pre_move_delay_seconds,
                post_action_delay_seconds=timing.post_action_delay_seconds,
            ),
            self._evidence_with_camera_timing(
                self._object_decision_evidence(
                    observation,
                    action=f"Turn camera {direction}",
                    selected=target,
                    eligible=(target,),
                ),
                timing,
                camera_action=direction,
            ),
        )

    def _classify_route_camera(
        self,
        observation: Observation,
        step: FixedRouteStep,
        target: NearbyObject,
    ) -> CameraFramingDecision | None:
        if observation.viewport_bounds is None and target.geometry.actionable:
            self._last_camera_framing = None
            return None
        viewport = observation.viewport_bounds or observation.canvas_bounds
        if viewport is None:
            self._last_camera_framing = None
            return None
        lookahead_points = self._route_camera_lookahead_points(
            observation,
            step,
        )
        yaw_error = self._camera_yaw_error(
            observation.location,
            step.location,
            observation.camera_yaw,
        ) if observation.camera_yaw is not None else None
        framing = self.behavior.classify_camera(
            target.geometry,
            viewport,
            decision_id=(
                f"camera-frame:{step.step_id}:{observation.tick}:"
                f"{self._camera_recovery_attempts}"
            ),
            route_dx=step.location.x - observation.location.x,
            route_dy=step.location.y - observation.location.y,
            player_point=observation.player_screen_point,
            framing_context="route",
            lookahead_points=lookahead_points,
            yaw_error_units=yaw_error,
            source_tick=observation.tick,
            geometry_frame_id=observation.geometry_frame_id,
            camera_zoom=observation.camera_zoom,
            camera_pitch=observation.camera_pitch,
        )
        self._last_camera_framing = framing
        return framing

    def _evidence_with_camera_timing(
        self,
        evidence: DecisionEvidence,
        timing: TimingDecision,
        *,
        camera_action: str,
    ) -> DecisionEvidence:
        return DecisionEvidence(
            selected=evidence.selected,
            eligible=evidence.eligible,
            rejected=evidence.rejected,
            route=self._route_decision_evidence(),
            camera=self._camera_decision_evidence(action_override=camera_action),
            timing=TimingDecisionEvidence(
                decision_id=timing.decision_id,
                seed=timing.seed,
                pre_move_delay_seconds=timing.pre_move_delay_seconds,
                settle_delay_seconds=timing.settle_delay_seconds,
                pre_click_delay_seconds=timing.pre_click_delay_seconds,
                post_action_delay_seconds=timing.post_action_delay_seconds,
                route_pause_seconds=timing.route_pause_seconds,
            ),
        )

    def _evidence_with_camera(
        self,
        evidence: DecisionEvidence,
        *,
        action_override: str | None = None,
    ) -> DecisionEvidence:
        """Attach the latest bounded framing facts to a wait or blocker."""

        return DecisionEvidence(
            selected=evidence.selected,
            eligible=evidence.eligible,
            rejected=evidence.rejected,
            route=evidence.route or self._route_decision_evidence(),
            camera=self._camera_decision_evidence(
                action_override=action_override
            ),
            targeting=evidence.targeting,
            timing=evidence.timing,
        )

    def _camera_zoom_decision(
        self,
        observation: Observation,
        target: NearbyObject,
        framing: CameraFramingDecision,
        *,
        logical_target_id: str,
        action: str,
        evidence: DecisionEvidence,
    ) -> Decision:
        """Request one semantic bounded wheel step for the locked target."""

        episode = self._camera_episode
        if episode is None:
            raise RuntimeError("camera zoom requires an active acquisition episode")

        unavailable_reason: str | None = None
        if self._camera_zoom_attempts >= 1:
            unavailable_reason = (
                "zoom remains outside the safe range after one bounded attempt"
            )
        elif framing.zoom_classification not in {"too_far", "too_close"}:
            unavailable_reason = "required camera zoom direction is unavailable"
        elif (
            observation.camera_yaw is None
            or observation.camera_pitch is None
            or observation.camera_zoom is None
            or observation.geometry_frame_id is None
            or observation.client_process_id is None
            or observation.client_process_id <= 0
            or observation.canvas_bounds is None
            or observation.viewport_bounds is None
            or observation.client_window_bounds is None
        ):
            unavailable_reason = "authoritative camera zoom geometry is unavailable"
        elif observation.text_input_active is not False:
            unavailable_reason = "authoritative text-input safety is unavailable"
        elif (
            not observation.widgets.bank_known
            or observation.widgets.bank_open
            or observation.widgets.bank_pin_open
            or observation.widgets.dialogue_active
        ):
            unavailable_reason = "interface state is unsafe for camera zoom"

        if unavailable_reason is not None:
            episode.state = CameraAcquisitionState.ZOOM_REQUIRED_BUT_UNAVAILABLE
            episode.retained_reason = unavailable_reason
            return self._block(
                observation,
                unavailable_reason,
                evidence=self._evidence_with_camera(evidence),
            )

        assert observation.camera_yaw is not None
        assert observation.camera_zoom is not None
        assert observation.geometry_frame_id is not None
        amount = self.behavior.config.camera_wheel_step
        if framing.zoom_classification == "too_close":
            amount = -amount
        direction_label = "in" if amount > 0 else "out"
        widgets = observation.widgets
        verification = VerificationSpec(
            VerificationKind.CAMERA_ZOOM_CHANGED,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick
                + self.definition.verification.action_deadline_ticks
            ),
            before_location=observation.location,
            source_session_id=observation.session_id,
            before_camera_yaw=observation.camera_yaw,
            before_camera_pitch=observation.camera_pitch,
            before_geometry_frame_id=observation.geometry_frame_id,
            before_camera_zoom=observation.camera_zoom,
            camera_zoom_amount=amount,
            before_process_id=observation.client_process_id,
            before_bank_known=widgets.bank_known,
            before_bank_open=widgets.bank_open,
            before_bank_pin_open=widgets.bank_pin_open,
            before_bank_readable=widgets.bank_readable,
            before_dialogue_active=widgets.dialogue_active,
            before_dialogue_type=widgets.dialogue_type,
            before_text_input_active=observation.text_input_active,
        )
        constraint = CameraZoomConstraint(
            target_key=target.key,
            target_location=target.location,
            source_location=observation.location,
            source_geometry_frame_id=observation.geometry_frame_id,
            before_yaw=observation.camera_yaw,
            before_pitch=observation.camera_pitch,
            before_zoom=observation.camera_zoom,
            amount=amount,
            desired_zoom_min=self.behavior.config.camera_zoom_desired_min,
            desired_zoom_max=self.behavior.config.camera_zoom_desired_max,
            target_id=target.object_id,
            target_name=target.name,
            target_kind=target.kind,
            target_action=action,
        )
        decision_id = self._next_behavior_decision_id(
            "camera-zoom",
            target.key,
            observation.tick,
        )
        timing = self.behavior.timing(decision_id, camera_moved=True)
        self._last_timing_decision = timing
        self.progress.pending = verification
        self._pending_camera_zoom_step_id = logical_target_id
        episode.state = CameraAcquisitionState.STABILIZING
        episode.retained_reason = (
            f"one bounded zoom-{direction_label} step requested for locked target"
        )
        return Decision(
            self.progress.phase.value,
            f"zoom camera {direction_label} for locked target {target.name}",
            Action(
                ActionKind.CAMERA_ZOOM,
                f"Zoom camera {direction_label} for {target.name}",
                observation.tick,
                option=f"Zoom camera {direction_label}",
                target_key=target.key,
                target_name=target.name,
                target_id=target.object_id,
                verification=verification,
                target_param0=target.scene_x,
                target_param1=target.scene_y,
                source_session_id=observation.session_id,
                task_constraints=TaskConstraints(camera_zoom=constraint),
                decision_id=decision_id,
                behavior_seed=self.behavior.seed,
                pre_move_delay_seconds=timing.pre_move_delay_seconds,
                post_action_delay_seconds=timing.post_action_delay_seconds,
            ),
            self._evidence_with_camera_timing(
                evidence,
                timing,
                camera_action=f"zoom_{direction_label}",
            ),
        )

    def _maybe_reframe_object(
        self,
        observation: Observation,
        target: NearbyObject,
        *,
        action: str,
    ) -> Decision | None:
        if (
            observation.viewport_bounds is None
            or target.location is None
            or observation.camera_yaw is None
            or observation.geometry_frame_id is None
        ):
            return None
        framing = self.behavior.classify_camera(
            target.geometry,
            observation.viewport_bounds,
            decision_id=(
                f"camera-object:{target.key}:{observation.tick}:"
                f"{self._camera_recovery_attempts}"
            ),
            route_dx=target.location.x - observation.location.x,
            route_dy=target.location.y - observation.location.y,
            player_point=observation.player_screen_point,
            framing_context="interaction",
            yaw_error_units=self._camera_yaw_error(
                observation.location,
                target.location,
                observation.camera_yaw,
            ),
            source_tick=observation.tick,
            geometry_frame_id=observation.geometry_frame_id,
            camera_zoom=observation.camera_zoom,
            camera_pitch=observation.camera_pitch,
        )
        self._last_camera_framing = framing
        self._consume_camera_framing_progress(framing)
        if not framing.pitch_valid:
            if observation.camera_pitch is None:
                return self._wait(
                    observation,
                    f"waiting for a valid camera pitch for {target.name}",
                    evidence=self._evidence_with_camera(
                        self._object_decision_evidence(
                            observation,
                            action=action,
                            selected=target,
                            eligible=(target,),
                        )
                    ),
                )
            return self._block(
                observation,
                f"camera pitch is outside the supported range for {target.name}",
                evidence=self._evidence_with_camera(
                    self._object_decision_evidence(
                        observation,
                        action=action,
                        selected=target,
                        eligible=(target,),
                    )
                ),
            )
        if framing.action == "none":
            if self._camera_episode is not None:
                self._update_camera_episode_goal(
                    observation,
                    target.location,
                    framing,
                )
                self._camera_episode.state = CameraAcquisitionState.READY
                self._camera_episode.retained_reason = "fresh framing goal satisfied"
            return None
        if self._camera_episode is None:
            episode = self._start_camera_episode(
                observation,
                target,
                context="interaction",
                logical_target_id=target.key,
                action=action,
                route_index=(
                    self.progress.route_index
                    if self.progress.phase
                    in {TaskPhase.NAVIGATE_TO_BANK, TaskPhase.NAVIGATE_TO_TREES}
                    else None
                ),
            )
            self._update_camera_episode_goal(
                observation,
                target.location,
                framing,
            )
            needs_stable_wait = self._route_projection_wait_since_tick is None
            if needs_stable_wait:
                self._route_projection_wait_since_tick = observation.tick
            if framing.zoom_required_but_unavailable:
                return self._camera_zoom_decision(
                    observation,
                    target,
                    framing,
                    logical_target_id=target.key,
                    action=action,
                    evidence=self._object_decision_evidence(
                        observation,
                        action=action,
                        selected=target,
                        eligible=(target,),
                    ),
                )
            if needs_stable_wait:
                return self._wait(
                    observation,
                    f"waiting for stable proactive framing of {target.name}",
                    evidence=self._evidence_with_camera(
                        self._object_decision_evidence(
                            observation,
                            action=action,
                            selected=target,
                            eligible=(target,),
                        )
                    ),
                )
        if (
            self._camera_episode.logical_target_id != target.key
            or not self._camera_episode_target_matches(
                self._camera_episode,
                target,
            )
        ):
            self._camera_episode.state = CameraAcquisitionState.INVALIDATED
            self._camera_episode.retained_reason = (
                "camera controller refused an alternate interaction target during an active episode"
            )
            return self._block(
                observation,
                self._camera_episode.retained_reason,
                evidence=self._evidence_with_camera(DecisionEvidence()),
            )
        self._update_camera_episode_goal(
            observation,
            target.location,
            framing,
        )
        if framing.zoom_required_but_unavailable:
            return self._camera_zoom_decision(
                observation,
                target,
                framing,
                logical_target_id=target.key,
                action=action,
                evidence=self._object_decision_evidence(
                    observation,
                    action=action,
                    selected=target,
                    eligible=(target,),
                ),
            )
        if self._route_projection_wait_since_tick is None:
            self._route_projection_wait_since_tick = observation.tick
            return self._wait(
                observation,
                f"waiting for stable proactive framing of {target.name}",
                evidence=self._evidence_with_camera(
                    self._object_decision_evidence(
                        observation,
                        action=action,
                        selected=target,
                        eligible=(target,),
                    )
                ),
            )
        if observation.tick <= self._route_projection_wait_since_tick:
            return self._wait(
                observation,
                f"waiting for a later framing sample for {target.name}",
                evidence=self._evidence_with_camera(
                    self._object_decision_evidence(
                        observation,
                        action=action,
                        selected=target,
                        eligible=(target,),
                    )
                ),
            )
        correction_limit_reached = (
            self._camera_recovery_attempts
            >= self.behavior.config.camera_max_corrections
        )
        recovery_stalled = (
            not correction_limit_reached
            and self._camera_framing_progress_stalled()
        )
        if correction_limit_reached or recovery_stalled:
            if self._camera_episode is not None:
                self._camera_episode.state = (
                    CameraAcquisitionState.NON_IMPROVING
                    if recovery_stalled
                    else CameraAcquisitionState.EXHAUSTED
                )
                self._camera_episode.retained_reason = (
                    "bounded camera response did not improve framing"
                    if recovery_stalled
                    else "coarse and fine camera actions were exhausted"
                )
            rejection_code = (
                "camera_framing_non_improving"
                if recovery_stalled
                else "camera_framing_exhausted"
            )
            if (
                self.progress.phase is TaskPhase.CHOP
                and self.progress.target_key == target.key
            ):
                candidates, rejected = self._classify_trees(observation)
                alternatives = tuple(
                    candidate
                    for candidate in candidates
                    if candidate.key != target.key
                )
                if alternatives:
                    exhausted_rejected = tuple(
                        item for item in rejected if item[0].key != target.key
                    ) + ((target, (rejection_code,)),)
                    evidence = self._evidence_with_camera(
                        self._object_decision_evidence(
                            observation,
                            action=action,
                            eligible=alternatives,
                            rejected=exhausted_rejected,
                        )
                    )
                    self._next_resource_suppression = (
                        target.key,
                        rejection_code,
                    )
                    self._resource_camera_suppressions[target.key] = (
                        observation.tick
                        + self.behavior.config.resource_camera_suppression_ticks
                    )
                    self.progress.target_key = None
                    self.progress.phase = TaskPhase.FIND_TREE
                    self._reset_camera_recovery()
                    if self._target_lock is not None:
                        self._clear_target_lock(
                            "bounded camera failure released the resource for suppressed replanning"
                        )
                    return self._wait(
                        observation,
                        (
                            f"camera framing made no progress for {target.name}; "
                            "reselecting an exact alternate"
                            if recovery_stalled
                            else f"camera framing exhausted for {target.name}; "
                            "reselecting an exact alternate"
                        ),
                        evidence=evidence,
                    )
            return self._block(
                observation,
                (
                    f"camera framing made no progress for {target.name}"
                    if recovery_stalled
                    else f"camera framing exhausted for {target.name}"
                ),
                evidence=self._evidence_with_camera(
                    self._object_decision_evidence(
                        observation,
                        action=action,
                        selected=target,
                        eligible=(target,),
                    )
                ),
            )
        usable_pitch = self._usable_camera_pitch(
            target.key,
            observation.camera_pitch,
        )
        direction = self._camera_correction_direction(
            observation.location,
            target.location,
            observation.camera_yaw,
            usable_pitch,
            framing,
        )
        if direction is None and framing.action != "none":
            if not self._has_geometry(target) or framing.target_point is None:
                direction = self._camera_search_direction(
                    target.key,
                    camera_pitch=usable_pitch,
                )
        if direction is None:
            assert self._camera_episode is not None
            self._camera_episode.state = CameraAcquisitionState.NON_IMPROVING
            self._camera_episode.retained_reason = (
                "no supported yaw or pitch correction can satisfy the framing goal"
            )
            self._camera_non_improving_corrections = (
                CAMERA_NON_IMPROVING_CORRECTION_LIMIT
            )
            return self._wait(
                observation,
                self._camera_episode.retained_reason,
                evidence=self._evidence_with_camera(
                    self._object_decision_evidence(
                        observation,
                        action=action,
                        selected=target,
                        eligible=(target,),
                    )
                ),
            )
        veto_reason = self._camera_direction_veto_reason(
            direction,
            current_pitch=observation.camera_pitch,
        )
        if veto_reason is not None:
            assert self._camera_episode is not None
            self._camera_episode.state = CameraAcquisitionState.NON_IMPROVING
            self._camera_episode.retained_reason = veto_reason
            self._camera_non_improving_corrections = (
                CAMERA_NON_IMPROVING_CORRECTION_LIMIT
            )
            return self._wait(
                observation,
                veto_reason,
                evidence=self._evidence_with_camera(
                    self._object_decision_evidence(
                        observation,
                        action=action,
                        selected=target,
                        eligible=(target,),
                    )
                ),
            )
        hold_millis, yaw_error, pitch_error = self._select_camera_hold(
            observation,
            target.location,
            framing,
            direction,
        )
        framing = replace(framing, hold_millis=hold_millis)
        self._last_camera_framing = framing
        assert self._camera_episode is not None
        self._camera_episode.state = (
            CameraAcquisitionState.COARSE
            if self._camera_recovery_attempts == 0
            else CameraAcquisitionState.FINE
        )
        decision_id = self._next_behavior_decision_id(
            "camera",
            target.key,
            observation.tick,
        )
        timing = self.behavior.timing(
            decision_id,
            camera_moved=True,
        )
        self._last_timing_decision = timing
        verification = VerificationSpec(
            VerificationKind.CAMERA_POSE_CHANGED,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick
                + self.definition.verification.action_deadline_ticks
            ),
            before_location=observation.location,
            source_session_id=observation.session_id,
            before_camera_yaw=observation.camera_yaw,
            before_camera_pitch=observation.camera_pitch,
            before_geometry_frame_id=observation.geometry_frame_id,
            camera_key=direction,
        )
        constraint = CameraConstraint(
            target_key=target.key,
            target_location=target.location,
            source_location=observation.location,
            source_geometry_frame_id=observation.geometry_frame_id,
            before_yaw=observation.camera_yaw,
            direction=direction,
            hold_millis=hold_millis,
            before_pitch=observation.camera_pitch,
            desired_region=framing.desired_region,
            framing_classification=framing.classification,
            target_id=target.object_id,
            target_name=target.name,
            target_kind=target.kind,
            target_action=action,
        )
        self.progress.pending = verification
        self._pending_camera_step_id = target.key
        self._pending_camera_hold_millis = hold_millis
        self._pending_camera_yaw_error = yaw_error
        self._pending_camera_pitch_error = pitch_error
        self._camera_progress_baseline = framing
        return Decision(
            self.progress.phase.value,
            f"proactively frame {target.name} with camera {direction}",
            Action(
                ActionKind.CAMERA_HOLD,
                f"Frame {target.name}",
                observation.tick,
                option=f"Turn camera {direction}",
                target_key=target.key,
                target_name=target.name,
                target_id=target.object_id,
                key=direction,
                key_hold_millis=hold_millis,
                verification=verification,
                target_param0=target.scene_x,
                target_param1=target.scene_y,
                source_session_id=observation.session_id,
                task_constraints=TaskConstraints(camera=constraint),
                decision_id=decision_id,
                behavior_seed=self.behavior.seed,
                pre_move_delay_seconds=timing.pre_move_delay_seconds,
                post_action_delay_seconds=timing.post_action_delay_seconds,
            ),
            self._evidence_with_camera_timing(
                self._object_decision_evidence(
                    observation,
                    action=f"Turn camera {direction}",
                    selected=target,
                    eligible=(target,),
                ),
                timing,
                camera_action=direction,
            ),
        )

    @staticmethod
    def _camera_yaw_error(
        source: WorldPoint,
        target: WorldPoint,
        camera_yaw: int,
    ) -> int | None:
        return yaw_error_to_world_target(source, target, camera_yaw)

    def _camera_correction_direction(
        self,
        source: WorldPoint,
        target: WorldPoint,
        camera_yaw: int,
        camera_pitch: int | None,
        framing: CameraFramingDecision | None,
    ) -> str | None:
        yaw_error = self._camera_yaw_error(source, target, camera_yaw)
        yaw_direction = (
            None
            if yaw_error is None
            or abs(yaw_error) <= self.behavior.config.camera_yaw_deadband_units
            else ("right" if yaw_error > 0 else "left")
        )
        if self._camera_recovery_attempts == 0 and yaw_direction is not None:
            # The episode's single coarse correction is anchored to the
            # player-to-locked-target world bearing. Screen correction is
            # reserved for the optional fine step from fresh geometry.
            return yaw_direction
        if framing is not None and framing.classification == "not_visible":
            # A clipped or stale off-screen polygon can retain a numerical
            # screen vector, but it cannot prove which yaw direction will
            # bring the target back.  Use the definition-owned world bearing;
            # callers retain their bounded seeded search when it is unknown.
            return yaw_direction
        pitch_direction: str | None = None
        vertical_error = 0
        horizontal_error = 0
        if framing is not None:
            screen_dx = framing.screen_correction_x_px
            screen_dy = framing.screen_correction_y_px
            horizontal_error = abs(screen_dx)
            vertical_error = abs(screen_dy)
            if camera_pitch is not None and screen_dy:
                # RuneLite's screen projection moves upward when the camera
                # pitch increases (the UP key) and downward when pitch
                # decreases (the DOWN key).  The correction vector describes
                # where the projected geometry itself must move, so the key
                # mapping is intentionally the inverse of the signed screen
                # error.
                pitch_direction = "down" if screen_dy > 0 else "up"
            if (
                pitch_direction is not None
                and vertical_error > max(
                    horizontal_error,
                    self.behavior.config.camera_deadband_px,
                )
            ):
                return pitch_direction
            if horizontal_error > 0:
                # Live RuneLite projection evidence shows the arrow-key label
                # and horizontal screen motion have the same sign: RIGHT
                # moves exact geometry right and LEFT moves it left.
                return "right" if screen_dx > 0 else "left"
            if pitch_direction is not None and vertical_error > 0:
                return pitch_direction
            if horizontal_error > 0 or vertical_error > 0:
                # A signed screen correction is stronger evidence than the
                # world bearing.  If its only remaining axis is unsupported,
                # report that fact to the caller instead of substituting an
                # unrelated yaw correction.
                return None

        if framing is not None and camera_pitch is not None:
            point = framing.target_point
            desired = framing.desired_region
            if point is not None:
                if point.y < desired.y:
                    pitch_direction = "down"
                    vertical_error = desired.y - point.y
                elif point.y >= desired.y + desired.height:
                    pitch_direction = "up"
                    vertical_error = point.y - (desired.y + desired.height - 1)
                if point.x < desired.x:
                    horizontal_error = desired.x - point.x
                elif point.x >= desired.x + desired.width:
                    horizontal_error = point.x - (desired.x + desired.width - 1)
            if (
                framing.classification == "obscured_or_contradictory"
                and (yaw_error is None or abs(yaw_error) <= self.behavior.config.camera_yaw_deadband_units)
            ):
                return pitch_direction or "up"
        return yaw_direction or pitch_direction

    def _camera_direction_veto_reason(
        self,
        direction: str,
        *,
        current_pitch: int | None,
    ) -> str | None:
        if direction in {"up", "down"} and self._camera_response_model.pitch_direction_blocked(
            direction,
            current_pitch,
        ):
            return f"unchanged pose already proved the {direction} pitch limit"
        episode = self._camera_episode
        if (
            direction in {"left", "right"}
            and episode is not None
            and episode.last_verified_direction in {"left", "right"}
            and not yaw_reversal_allowed(
                episode.last_verified_direction,
                direction,
                overshoot_proved=episode.overshoot_proven,
            )
        ):
            return "fresh evidence did not prove the requested yaw reversal"
        return None

    def _usable_camera_pitch(
        self,
        logical_target_id: str,
        current_pitch: int | None,
    ) -> int | None:
        if self._camera_pitch_suppressed_step_id != logical_target_id:
            return current_pitch
        episode = self._camera_episode
        if (
            episode is not None
            and episode.pitch_limit_pose is not None
            and current_pitch is not None
            and current_pitch != episode.pitch_limit_pose
        ):
            self._camera_pitch_suppressed_step_id = None
            episode.pitch_limit_direction = None
            episode.pitch_limit_pose = None
            episode.retained_reason = "fresh pose change cleared the old pitch limit"
            return current_pitch
        return None

    def _camera_control_errors(
        self,
        observation: Observation,
        target: WorldPoint,
        framing: CameraFramingDecision | None,
    ) -> tuple[int | None, int | None]:
        yaw_error = (
            self._camera_yaw_error(
                observation.location,
                target,
                observation.camera_yaw,
            )
            if observation.location is not None
            and observation.camera_yaw is not None
            else None
        )
        pitch_error = None
        if framing is not None and observation.camera_pitch is not None:
            pitch_error = round(
                -framing.screen_correction_y_px
                * self.behavior.config.camera_screen_pitch_units_per_px
            )
        return yaw_error, pitch_error

    def _update_camera_episode_goal(
        self,
        observation: Observation,
        target: WorldPoint,
        framing: CameraFramingDecision | None,
    ) -> None:
        episode = self._camera_episode
        if episode is None or observation.location is None:
            return
        episode.desired_yaw = desired_camera_yaw(
            observation.location,
            target,
        )
        _, pitch_error = self._camera_control_errors(
            observation,
            target,
            framing,
        )
        episode.pitch_error_units = pitch_error
        if observation.camera_pitch is None or pitch_error is None:
            episode.desired_pitch = None
            episode.desired_pitch_min = None
            episode.desired_pitch_max = None
            return
        desired_pitch = max(
            self.behavior.config.camera_pitch_valid_min,
            min(
                self.behavior.config.camera_pitch_valid_max,
                observation.camera_pitch + pitch_error,
            ),
        )
        pitch_band = round(
            self.behavior.config.camera_deadband_px
            * self.behavior.config.camera_screen_pitch_units_per_px
        )
        episode.desired_pitch = desired_pitch
        episode.desired_pitch_min = max(
            self.behavior.config.camera_pitch_valid_min,
            desired_pitch - pitch_band,
        )
        episode.desired_pitch_max = min(
            self.behavior.config.camera_pitch_valid_max,
            desired_pitch + pitch_band,
        )

    def _select_camera_hold(
        self,
        observation: Observation,
        target: WorldPoint,
        framing: CameraFramingDecision | None,
        direction: str,
    ) -> tuple[int, int | None, int | None]:
        yaw_error, pitch_error = self._camera_control_errors(
            observation,
            target,
            framing,
        )
        if direction in {"left", "right"}:
            control_error = yaw_error or 0
            if (
                self._camera_recovery_attempts > 0
                and framing is not None
                and abs(framing.screen_correction_x_px)
                > self.behavior.config.camera_deadband_px
            ):
                viewport = observation.viewport_bounds or observation.canvas_bounds
                if viewport is not None:
                    control_error = round(
                        framing.screen_correction_x_px
                        * self.behavior.config.camera_yaw_full_correction_units
                        / max(1.0, viewport.width * 0.45)
                    )
            deadband = self.behavior.config.camera_yaw_deadband_units
            fallback_rate = 4.5
        else:
            control_error = pitch_error or 0
            deadband = round(
                self.behavior.config.camera_deadband_px
                * self.behavior.config.camera_screen_pitch_units_per_px
            )
            fallback_rate = 1.0
        phase = (
            CameraCorrectionPhase.COARSE
            if self._camera_recovery_attempts == 0
            else CameraCorrectionPhase.FINE
        )
        hold = select_camera_hold_millis(
            control_error,
            direction,
            phase,
            self.behavior.camera_capabilities,
            response_model=self._camera_response_model,
            minimum_hold_millis=self.behavior.config.camera_hold_min_millis,
            requested_max_millis=self.behavior.config.camera_hold_max_millis,
            fallback_rate_units_per_millis=fallback_rate,
            deadband_units=deadband,
        )
        if hold == 0:
            hold = min(
                self.behavior.config.camera_hold_min_millis,
                self.behavior.camera_capabilities.max_hold_millis,
                self.behavior.config.camera_hold_max_millis,
            )
        return hold, yaw_error, pitch_error

    def _camera_search_direction(
        self,
        target_key: str,
        *,
        camera_pitch: int | None,
    ) -> str:
        """Search safely when an off-screen target has no usable world bearing."""

        seed = self.behavior.derived_seed(
            f"camera-search:{target_key}",
            "camera-search",
        )
        primary_yaw = "right" if seed & 1 else "left"
        if self._camera_recovery_attempts == 0:
            return primary_yaw
        if (
            camera_pitch is not None
            and not self._camera_response_model.pitch_direction_blocked(
                "up",
                camera_pitch,
            )
        ):
            return "up"
        # A same-tile target has no world bearing. Repeating the seeded yaw is
        # bounded and causal; reversing it without overshoot evidence is not.
        return primary_yaw

    def _consume_camera_framing_progress(
        self,
        current: CameraFramingDecision,
    ) -> bool | None:
        """Consume one verified correction against fresh projection geometry."""

        prior = self._camera_progress_baseline
        if prior is None:
            return None
        if (
            prior.source_tick is None
            or current.source_tick is None
            or current.source_tick <= prior.source_tick
            or prior.geometry_frame_id is None
            or current.geometry_frame_id is None
            or current.geometry_frame_id == prior.geometry_frame_id
        ):
            return None

        self._camera_progress_baseline = None
        best = self._camera_progress_best or prior
        improved = self._camera_framing_improved(best, current)
        if improved:
            self._camera_progress_best = current
            self._camera_non_improving_corrections = 0
        else:
            if self._camera_progress_best is None:
                self._camera_progress_best = prior
            self._camera_non_improving_corrections = min(
                min(
                    CAMERA_NON_IMPROVING_CORRECTION_LIMIT,
                    self.behavior.config.camera_max_corrections,
                ),
                self._camera_non_improving_corrections + 1,
            )
        return improved

    def _camera_framing_improved(
        self,
        prior: CameraFramingDecision,
        current: CameraFramingDecision,
    ) -> bool:
        pixel_hysteresis = max(
            1,
            self.behavior.config.camera_edge_hysteresis_px,
        )
        yaw_hysteresis = max(
            1,
            self.behavior.config.camera_yaw_deadband_units,
        )
        prior_rank = CAMERA_FRAMING_CLASSIFICATION_RANK.get(
            prior.classification,
            -1,
        )
        current_rank = CAMERA_FRAMING_CLASSIFICATION_RANK.get(
            current.classification,
            -1,
        )
        if current_rank > prior_rank:
            return True
        if current_rank < prior_rank:
            return False
        if (
            current.correction_distance_px - prior.correction_distance_px
            >= pixel_hysteresis
        ):
            return False
        if (
            prior.edge_clearance_px is not None
            and current.edge_clearance_px is not None
            and prior.edge_clearance_px - current.edge_clearance_px
            >= pixel_hysteresis
        ):
            return False
        if (
            prior.correction_distance_px - current.correction_distance_px
            >= pixel_hysteresis
        ):
            return True
        if (
            prior.yaw_error_units is not None
            and current.yaw_error_units is not None
            and abs(prior.yaw_error_units) - abs(current.yaw_error_units)
            >= yaw_hysteresis
        ):
            return True
        if (
            prior.edge_clearance_px is not None
            and current.edge_clearance_px is not None
            and current.edge_clearance_px - prior.edge_clearance_px
            >= pixel_hysteresis
        ):
            return True
        if prior.target_point is not None and current.target_point is not None:
            correction_length = hypot(
                prior.screen_correction_x_px,
                prior.screen_correction_y_px,
            )
            if correction_length > 0:
                motion_x = current.target_point.x - prior.target_point.x
                motion_y = current.target_point.y - prior.target_point.y
                projected_motion = (
                    motion_x * prior.screen_correction_x_px
                    + motion_y * prior.screen_correction_y_px
                ) / correction_length
                if projected_motion >= pixel_hysteresis:
                    return True
        return False

    def _camera_framing_progress_stalled(self) -> bool:
        limit = min(
            CAMERA_NON_IMPROVING_CORRECTION_LIMIT,
            self.behavior.config.camera_max_corrections,
        )
        return self._camera_non_improving_corrections >= limit

    @staticmethod
    def _camera_turn_direction(
        source: WorldPoint,
        target: WorldPoint,
        camera_yaw: int,
    ) -> str | None:
        error = WoodcutBankTask._camera_yaw_error(source, target, camera_yaw)
        if error in {None, 0}:
            return None
        return "right" if error > 0 else "left"

    def _start_camera_episode(
        self,
        observation: Observation,
        target: NearbyObject,
        *,
        context: str,
        logical_target_id: str,
        action: str,
        route_index: int | None = None,
    ) -> CameraAcquisitionEpisode:
        effective_viewport = observation.viewport_bounds or observation.canvas_bounds
        if effective_viewport is None or target.location is None:
            raise ValueError("camera episode requires viewport and target location")
        if not observation.session_id:
            raise ValueError("camera episode requires session identity")
        if self._camera_recovery_step_id != logical_target_id:
            self._reset_camera_recovery()
        lock_context = (
            "resource"
            if self.progress.phase in {TaskPhase.CHOP, TaskPhase.VERIFY_LOGS}
            else "bank"
            if self.progress.phase is TaskPhase.OPEN_BANK
            else "route"
        )
        if not self._lock_target(
            observation,
            target,
            action=action,
            context=lock_context,
        ):
            raise RuntimeError(
                "camera acquisition refused contradictory target continuity"
            )
        episode = CameraAcquisitionEpisode(
            episode_id=(
                f"camera:{context}:{logical_target_id}:"
                f"{observation.session_id}:{observation.tick}"
            ),
            context=context,
            logical_target_id=logical_target_id,
            locked_target_key=target.key,
            locked_target_id=target.object_id,
            locked_target_name=target.name,
            locked_target_kind=target.kind,
            locked_target_action=action,
            locked_target_location=target.location,
            route_index=route_index,
            source_session_id=observation.session_id,
            source_process_id=observation.client_process_id,
            canvas_bounds=observation.canvas_bounds,
            viewport_bounds=effective_viewport,
            client_window_bounds=observation.client_window_bounds,
            started_tick=observation.tick,
        )
        self._camera_episode = episode
        self._camera_recovery_step_id = logical_target_id
        return episode

    def _camera_episode_environment_failure(
        self,
        observation: Observation,
    ) -> str | None:
        episode = self._camera_episode
        if episode is None:
            return None
        if observation.session_id != episode.source_session_id:
            return "camera acquisition session changed"
        if observation.client_process_id != episode.source_process_id:
            return "camera acquisition process identity changed"
        if observation.canvas_bounds != episode.canvas_bounds:
            return "camera acquisition canvas geometry changed"
        if (
            observation.viewport_bounds or observation.canvas_bounds
        ) != episode.viewport_bounds:
            return "camera acquisition viewport geometry changed"
        if observation.client_window_bounds != episode.client_window_bounds:
            return "camera acquisition client-window geometry changed"
        return None

    @staticmethod
    def _camera_episode_target_matches(
        episode: CameraAcquisitionEpisode,
        target: NearbyObject,
    ) -> bool:
        return bool(
            target.key == episode.locked_target_key
            and target.object_id == episode.locked_target_id
            and target.name == episode.locked_target_name
            and target.kind == episode.locked_target_kind
            and target.location == episode.locked_target_location
            and target.supports(episode.locked_target_action)
        )

    def _reset_camera_recovery(self) -> None:
        self._camera_episode = None
        self._camera_recovery_step_id = None
        self._camera_recovery_attempts = 0
        self._camera_recovery_total_hold_millis = 0
        self._camera_pitch_suppressed_step_id = None
        self._route_projection_wait_since_tick = None
        self._pending_camera_step_id = None
        self._pending_camera_hold_millis = None
        self._pending_camera_yaw_error = None
        self._pending_camera_pitch_error = None
        self._camera_zoom_attempts = 0
        self._pending_camera_zoom_step_id = None
        self._camera_progress_baseline = None
        self._camera_progress_best = None
        self._camera_non_improving_corrections = 0

    def _record_completed_camera_hold(self) -> None:
        if self._pending_camera_hold_millis is not None:
            self._camera_recovery_total_hold_millis += self._pending_camera_hold_millis
        self._pending_camera_hold_millis = None
        self._pending_camera_yaw_error = None
        self._pending_camera_pitch_error = None

    def _record_camera_pose_response(
        self,
        pending: VerificationSpec,
        result: VerificationResult,
    ) -> None:
        pose = (
            result.outcome.camera_pose_result
            if result.outcome is not None
            else None
        )
        hold = self._pending_camera_hold_millis
        direction = pending.camera_key
        if pose is None or hold is None or direction is None:
            return
        overshoot = False
        after_error: int | None = None
        episode = self._camera_episode
        if (
            direction in {"left", "right"}
            and self._pending_camera_yaw_error is not None
            and episode is not None
            and pose.after_yaw is not None
            and pending.before_location is not None
        ):
            after_error = self._camera_yaw_error(
                pending.before_location,
                episode.locked_target_location,
                pose.after_yaw,
            )
            if after_error is not None:
                overshoot = proves_yaw_overshoot(
                    self._pending_camera_yaw_error,
                    after_error,
                    pose_result_fresh=(
                        result.outcome is not None
                        and result.outcome.observed_tick > pending.before_tick
                    ),
                    geometry_changed=(
                        pose.before_geometry_frame_id
                        != pose.after_geometry_frame_id
                    ),
                    deadband_units=self.behavior.config.camera_yaw_deadband_units,
                )
        sample = CameraResponseSample(
            direction=direction,
            requested_hold_millis=hold,
            observed_yaw_delta=pose.yaw_delta or 0,
            observed_pitch_delta=pose.pitch_delta or 0,
            before_yaw=pose.before_yaw,
            after_yaw=pose.after_yaw,
            before_pitch=pose.before_pitch,
            after_pitch=pose.after_pitch,
            overshoot=overshoot,
        )
        self._camera_response_model = self._camera_response_model.record(sample)
        self._last_camera_response = sample
        if episode is not None:
            episode.last_verified_direction = direction
            episode.last_yaw_error_units = after_error
            episode.overshoot_proven = overshoot
            episode.retained_reason = (
                "fresh changed-geometry response proved yaw overshoot"
                if overshoot
                else "fresh changed-geometry camera response retained"
            )

    def _record_camera_no_effect_response(
        self,
        pending: VerificationSpec,
    ) -> None:
        hold = self._pending_camera_hold_millis
        direction = pending.camera_key
        if hold is None or direction is None:
            return
        sample = CameraResponseSample(
            direction=direction,
            requested_hold_millis=hold,
            before_yaw=pending.before_camera_yaw,
            after_yaw=pending.before_camera_yaw,
            before_pitch=pending.before_camera_pitch,
            after_pitch=pending.before_camera_pitch,
            pose_limit=direction in {"up", "down"},
            no_effect=True,
        )
        self._camera_response_model = self._camera_response_model.record(sample)
        self._last_camera_response = sample

    def _choose_stair_direction(self, observation: Observation) -> Decision:
        route = self._current_route()
        if route is None or self.progress.route_index >= len(route):
            return self._block(observation, "stair dialogue has no route step")
        step = route[self.progress.route_index]
        if step.expected_plane is None:
            return self._block(observation, "stair route direction is unknown")
        widgets = observation.widgets
        expectations = self.definition.verification
        if (
            not widgets.dialogue_active
            or widgets.dialogue_type != "options"
            or expectations.transition_dialogue_prompt_contains.lower()
            not in widgets.dialogue_prompt.lower()
            or not widgets.dialogue_number_keys
            or widgets.dialogue_client_tick is None
        ):
            return self._block(
                observation, "expected route-transition dialogue is unavailable"
            )
        direction = "up" if step.expected_plane > observation.plane else "down"
        option_contains = (
            expectations.transition_up_option_contains
            if direction == "up"
            else expectations.transition_down_option_contains
        )
        matches = []
        eligible_evidence = []
        rejected_evidence = []
        allowed_keys = {str(value) for value in range(1, 10)}
        for option in widgets.dialogue_options:
            codes = []
            if not option.visible:
                codes.append("not_visible")
            if option_contains.lower() not in option.text.lower():
                codes.append("text_mismatch")
            if option.key not in allowed_keys:
                codes.append("key_unavailable")
            target_evidence = self._dialogue_target_evidence(observation, option)
            if codes:
                rejected_evidence.append(
                    RejectedCandidateEvidence(target_evidence, tuple(codes))
                )
            else:
                matches.append(option)
                eligible_evidence.append(target_evidence)
        dialogue_evidence = DecisionEvidence(
            eligible=tuple(eligible_evidence),
            rejected=tuple(rejected_evidence),
        )
        if len(matches) != 1:
            return self._block(
                observation,
                f"exact {direction} route-transition option is unavailable",
                evidence=dialogue_evidence,
            )
        selected = matches[0]
        selected_evidence = self._dialogue_target_evidence(observation, selected)
        verification = VerificationSpec(
            VerificationKind.PLANE_CHANGED,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick + expectations.action_deadline_ticks
            ),
            before_location=observation.location,
            expected_plane=step.expected_plane,
            source_session_id=observation.session_id,
        )
        self.progress.pending = verification
        return Decision(
            self.progress.phase.value,
            f"choose exact {direction} route-transition option",
            Action(
                ActionKind.PRESS_KEY,
                f"Choose {selected.text}",
                observation.tick,
                option=selected.text,
                target_key=f"dialogue:{selected.index}",
                target_name=selected.text,
                target_id=selected.index,
                key=selected.key,
                verification=verification,
                source_session_id=observation.session_id,
                source_dialogue_client_tick=widgets.dialogue_client_tick,
                task_constraints=TaskConstraints(
                    dialogue=DialogueOptionConstraint(
                        prompt_contains=(
                            expectations.transition_dialogue_prompt_contains
                        ),
                        option_text=selected.text,
                        option_index=selected.index,
                        option_key=selected.key,
                    )
                ),
            ),
            DecisionEvidence(
                selected=selected_evidence,
                eligible=tuple(eligible_evidence),
                rejected=tuple(rejected_evidence),
            ),
        )

    def _open_bank(self, observation: Observation) -> Decision:
        bank = self.definition.bank
        selector = bank.selector
        if not observation.widgets.bank_known:
            return self._wait(observation, "bank state is not observable")
        if observation.widgets.bank_pin_open:
            return self._block(observation, "bank PIN handling is out of scope")
        if observation.widgets.bank_open:
            if not observation.widgets.bank_readable:
                return self._wait(observation, "bank is open but not readable")
            self.progress.phase = TaskPhase.DEPOSIT_LOGS
            if self._target_lock is not None:
                self._clear_target_lock("bank was already open and readable")
            return self._wait(observation, "bank is already open and readable")
        if observation.plane != bank.anchor.plane:
            return self._block(
                observation, "bank may only be opened on its configured plane"
            )

        if self._target_lock is not None and self._target_lock.context == "bank":
            locked_bank = observation.object_by_key(self._target_lock.key)
            if locked_bank is None:
                return self._handle_locked_target_omission(observation)
            if not self._touch_target_lock(observation, locked_bank):
                return self._block(
                    observation,
                    "locked bank target returned contradictory immutable identity",
                    evidence=self._object_decision_evidence(
                        observation,
                        action=selector.action,
                        rejected=((locked_bank, ("contradictory_target_identity",)),),
                    ),
                )

        targets, rejected = self._classify_bank_objects(observation)
        if not targets:
            return self._block(
                observation,
                "exact configured bank is unavailable",
                evidence=self._object_decision_evidence(
                    observation,
                    action=selector.action,
                    rejected=rejected,
                ),
            )
        target = targets[0]
        if self._target_lock is not None and self._target_lock.context == "bank":
            locked = observation.object_by_key(self._target_lock.key)
            if locked is None:
                return self._handle_locked_target_omission(observation)
            if locked not in targets:
                return self._block(
                    observation,
                    "locked bank target no longer satisfies the exact bank contract",
                    evidence=self._object_decision_evidence(
                        observation,
                        action=selector.action,
                        rejected=((locked, ("contradictory_target_identity",)),),
                    ),
                )
            target = locked
        if (
            self._camera_episode is not None
            and self._camera_episode.context == "interaction"
        ):
            locked = observation.object_by_key(
                self._camera_episode.locked_target_key
            )
            if locked is None or not self._camera_episode_target_matches(
                self._camera_episode,
                locked,
            ):
                self._camera_episode.state = CameraAcquisitionState.INVALIDATED
                self._camera_episode.retained_reason = (
                    "locked bank target became invalid during camera acquisition"
                )
                decision = self._wait(
                    observation,
                    self._camera_episode.retained_reason,
                    evidence=self._evidence_with_camera(DecisionEvidence()),
                )
                self._reset_camera_recovery()
                return decision
            target = locked
        camera = self._maybe_reframe_object(
            observation,
            target,
            action=selector.action,
        )
        if camera is not None:
            return camera
        bank_geometry_rejections = self._geometry_rejection_codes(target)
        if bank_geometry_rejections:
            return self._wait(
                observation,
                "waiting for fresh actionable bank geometry after camera acquisition",
                evidence=self._object_decision_evidence(
                    observation,
                    action=selector.action,
                    rejected=((target, bank_geometry_rejections),),
                ),
            )
        verification = VerificationSpec(
            VerificationKind.INTERFACE_OPENED,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick
                + self.definition.verification.action_deadline_ticks
            ),
            before_location=observation.location,
            expected_plane=bank.anchor.plane,
            source_session_id=observation.session_id,
            interface_name=BANK_INTERFACE_NAME,
        )
        decision = self._emit_action(
            observation,
            ActionKind.INTERACT_OBJECT,
            "Open configured bank",
            "open exact configured bank",
            selector.action,
            verification,
            target=target,
            evidence=self._evidence_with_camera(
                self._object_decision_evidence(
                    observation,
                    action=selector.action,
                    selected=target,
                    eligible=targets,
                    rejected=rejected,
                )
            ),
            task_constraints=TaskConstraints(
                interface=InterfaceConstraint(
                    BANK_INTERFACE_NAME, bank.anchor.plane, False
                )
            ),
        )
        self._reset_camera_recovery()
        return decision

    def _deposit_logs(self, observation: Observation) -> Decision:
        bank_plane = self.definition.bank.anchor.plane
        inventory_rule = self.definition.inventory
        if not observation.widgets.bank_known:
            return self._wait(observation, "bank state is not observable")
        if observation.widgets.bank_pin_open:
            return self._block(observation, "bank PIN handling is out of scope")
        if observation.plane != bank_plane:
            return self._block(
                observation, "items may only be deposited on the configured bank plane"
            )
        if not observation.widgets.bank_open or not observation.widgets.bank_readable:
            return self._block(observation, "readable bank must remain open before deposit")
        held = [item for item in observation.inventory.items if item.quantity > 0]
        if (
            inventory_rule.require_nonempty_deposit
            and (
                not held
                or not any(
                    item.item_id in inventory_rule.deposit_item_ids
                    for item in held
                )
            )
        ):
            return self._block(observation, "there are no configured items to deposit")
        if any(
            item.item_id not in inventory_rule.deposit_item_ids for item in held
        ):
            return self._block(
                observation, "deposit inventory violates the task definition"
            )

        target = observation.widgets.deposit_inventory
        if (
            target is None
            or target.name != DEPOSIT_INVENTORY_WIDGET_KEY
            or not target.visible
        ):
            evidence = DecisionEvidence()
            if target is not None:
                codes = []
                if target.name != DEPOSIT_INVENTORY_WIDGET_KEY:
                    codes.append("name_mismatch")
                if not target.visible:
                    codes.append("not_visible")
                evidence = self._single_rejected_evidence(
                    self._widget_target_evidence(
                        observation,
                        DEPOSIT_INVENTORY_WIDGET_KEY,
                        target,
                        "Deposit inventory",
                    ),
                    tuple(codes),
                )
            return self._block(
                observation,
                "deposit-inventory widget is unavailable",
                evidence=evidence,
            )
        point = target.screen_point
        if point is None:
            return self._block(
                observation,
                "deposit-inventory widget has no geometry",
                evidence=self._single_rejected_evidence(
                    self._widget_target_evidence(
                        observation,
                        DEPOSIT_INVENTORY_WIDGET_KEY,
                        target,
                        "Deposit inventory",
                    ),
                    ("screen_point_unavailable",),
                ),
            )

        verification = VerificationSpec(
            VerificationKind.ITEM_QUANTITY_EQUALS,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick
                + self.definition.verification.action_deadline_ticks
            ),
            item_id=self._produced_item_id,
            expected_quantity=(
                self.definition.verification.deposit_expected_quantity
            ),
            expected_plane=bank_plane,
            source_session_id=observation.session_id,
            interface_name=BANK_INTERFACE_NAME,
        )
        return self._emit_action(
            observation, ActionKind.CLICK_WIDGET, "Deposit inventory",
            "deposit all configured items",
            DEPOSIT_INVENTORY_WIDGET_KEY,
            verification,
            pending_phase=TaskPhase.VERIFY_DEPOSIT,
            target_key=DEPOSIT_INVENTORY_WIDGET_KEY,
            target_name=target.name,
            screen_point=point,
            evidence=self._single_selected_evidence(
                self._widget_target_evidence(
                    observation,
                    DEPOSIT_INVENTORY_WIDGET_KEY,
                    target,
                    "Deposit inventory",
                )
            ),
            task_constraints=TaskConstraints(
                inventory=InventoryConstraint(inventory_rule.deposit_item_ids),
                interface=InterfaceConstraint(
                    BANK_INTERFACE_NAME,
                    bank_plane,
                    True,
                    require_readable=True,
                ),
            ),
        )

    def _close_bank(self, observation: Observation) -> Decision:
        bank_plane = self.definition.bank.anchor.plane
        if not observation.widgets.bank_known:
            return self._wait(observation, "bank state is not observable")
        if observation.widgets.bank_pin_open:
            return self._block(observation, "bank PIN handling is out of scope")
        if observation.plane != bank_plane:
            return self._block(
                observation, "bank may only be closed on its configured plane"
            )
        if not observation.widgets.bank_open:
            self.progress.phase = TaskPhase.NAVIGATE_TO_TREES
            self.progress.route_index = 0
            return self._wait(observation, "bank is already closed")
        verification = VerificationSpec(
            VerificationKind.INTERFACE_CLOSED,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick
                + self.definition.verification.action_deadline_ticks
            ),
            expected_plane=bank_plane,
            source_session_id=observation.session_id,
            interface_name=BANK_INTERFACE_NAME,
        )
        target = observation.widgets.close_bank
        point = (
            target.screen_point
            if target is not None
            and target.name == CLOSE_BANK_WIDGET_KEY
            and target.visible
            else None
        )
        if point is None:
            rejected_widget: tuple[RejectedCandidateEvidence, ...] = ()
            if target is not None:
                codes = []
                if target.name != CLOSE_BANK_WIDGET_KEY:
                    codes.append("name_mismatch")
                if not target.visible:
                    codes.append("not_visible")
                if target.screen_point is None:
                    codes.append("screen_point_unavailable")
                rejected_widget = (
                    RejectedCandidateEvidence(
                        self._widget_target_evidence(
                            observation,
                            CLOSE_BANK_WIDGET_KEY,
                            target,
                            "Close bank",
                        ),
                        tuple(codes or ("input_unavailable",)),
                    ),
                )
            if not observation.widgets.keyboard_close_possible:
                return self._block(
                    observation,
                    "close-bank input is unavailable",
                    evidence=DecisionEvidence(rejected=rejected_widget),
                )
            self.progress.pending = verification
            target_evidence = TargetEvidence(
                key="close_bank_keyboard",
                name="Close bank",
                object_id=0,
                action="Escape",
                source_tick=observation.tick,
                geometry_frame_id=observation.geometry_frame_id,
                point=None,
                bounds=None,
            )
            return Decision(
                self.progress.phase.value,
                "close bank with verified Escape support",
                Action(
                    ActionKind.PRESS_KEY,
                    "Close bank with Escape",
                    observation.tick,
                    option="Close bank",
                    target_key="close_bank_keyboard",
                    target_name="Close bank",
                    target_id=0,
                    key="escape",
                    verification=verification,
                    source_session_id=observation.session_id,
                    task_constraints=TaskConstraints(
                        interface=InterfaceConstraint(
                            BANK_INTERFACE_NAME,
                            bank_plane,
                            True,
                            require_keyboard_close=True,
                        )
                    ),
                ),
                DecisionEvidence(
                    selected=target_evidence,
                    eligible=(target_evidence,),
                    rejected=rejected_widget,
                ),
            )

        return self._emit_action(
            observation, ActionKind.CLICK_WIDGET, "Close bank",
            "close bank before return route",
            CLOSE_BANK_WIDGET_KEY,
            verification,
            target_key=CLOSE_BANK_WIDGET_KEY,
            target_name=target.name,
            screen_point=point,
            evidence=self._single_selected_evidence(
                self._widget_target_evidence(
                    observation,
                    CLOSE_BANK_WIDGET_KEY,
                    target,
                    "Close bank",
                )
            ),
            task_constraints=TaskConstraints(
                interface=InterfaceConstraint(BANK_INTERFACE_NAME, bank_plane, True)
            ),
        )

    def _emit_action(
        self,
        observation: Observation,
        kind: ActionKind,
        label: str,
        reason: str,
        option: str,
        verification: VerificationSpec,
        *,
        pending_phase: TaskPhase | None = None,
        target: NearbyObject | None = None,
        target_key: str | None = None,
        target_name: str | None = None,
        screen_point=None,
        evidence: DecisionEvidence | None = None,
        task_constraints: TaskConstraints | None = None,
    ) -> Decision:
        if target is not None:
            target_key, target_name = target.key, target.name
            screen_point = target.geometry.screen_point
        if target is not None and kind is ActionKind.INTERACT_OBJECT:
            if self.progress.phase in {TaskPhase.CHOP, TaskPhase.VERIFY_LOGS}:
                lock_context = "resource"
            elif self.progress.phase is TaskPhase.OPEN_BANK:
                lock_context = "bank"
            else:
                lock_context = "route"
            if not self._lock_target(
                observation,
                target,
                action=option,
                context=lock_context,
            ):
                return self._block(
                    observation,
                    "object activation rejected contradictory target continuity",
                    evidence=self._object_decision_evidence(
                        observation,
                        action=option,
                        rejected=((target, ("contradictory_target_identity",)),),
                    ),
                )
            census_rejections = self._activation_census_rejection_codes(
                observation,
                target,
            )
            if census_rejections:
                self._target_lock.retained_reason = (
                    "activation denied until raw census coverage is complete"
                )
                return self._wait(
                    observation,
                    "object activation denied by explicitly incomplete or contradictory census evidence",
                    evidence=self._evidence_with_camera(
                        self._object_decision_evidence(
                            observation,
                            action=option,
                            rejected=((target, census_rejections),),
                        )
                    ),
                )
        decision_id = self._next_behavior_decision_id(
            kind.value,
            target_key or target_name or option,
            observation.tick,
        )
        aim_decision: AimDecision | None = None
        previous_points: tuple[ScreenPoint, ...] = ()
        geometry = self._action_target_geometry(
            observation,
            kind,
            target,
            target_key,
            screen_point,
        )
        if geometry is not None and kind in {
            ActionKind.INTERACT_OBJECT,
            ActionKind.WALK,
            ActionKind.CLICK_WIDGET,
        }:
            canvas = (
                observation.viewport_bounds
                if kind in {ActionKind.INTERACT_OBJECT, ActionKind.WALK}
                and observation.viewport_bounds is not None
                else observation.canvas_bounds
            )
            if canvas is not None:
                history_key = target_key or target_name or option
                previous_points = tuple(self._aim_history.get(history_key, ()))
                competing = tuple(
                    candidate.geometry.screen_bounds
                    for candidate in observation.nearby_objects
                    if target is not None
                    and candidate.key != target.key
                    and candidate.geometry.visible
                    and candidate.geometry.actionable
                    and candidate.geometry.screen_bounds is not None
                    and (
                        candidate.object_id == target.object_id
                        or candidate.name == target.name
                    )
                )
                excluded = (
                    (observation.menu_bounds,)
                    if observation.menu_open and observation.menu_bounds is not None
                    else ()
                )
                try:
                    aim_decision = self.behavior.select_aim_point(
                        geometry,
                        canvas,
                        target_key=history_key,
                        decision_id=decision_id,
                        cursor=observation.menu_mouse_screen_point,
                        excluded_bounds=excluded,
                        competing_bounds=competing,
                    )
                except ValueError:
                    if geometry.screen_polygon or geometry.geometry_source in {
                        "clickbox",
                        "convex_hull",
                        "canvas_tile",
                    }:
                        return self._wait(
                            observation,
                            f"waiting for a safe inset aim candidate for {history_key}",
                            evidence=evidence,
                        )
                if aim_decision is not None:
                    screen_point = aim_decision.selected.point
                    history = self._aim_history.setdefault(history_key, [])
                    history.append(screen_point)
                    del history[: -self.behavior.config.aim_history_size]
                    self._last_aim_decision = aim_decision

        extent = 1.0
        if geometry is not None and geometry.screen_bounds is not None:
            extent = float(
                min(geometry.screen_bounds.width, geometry.screen_bounds.height)
            )
        pointer_distance = 0.0
        if screen_point is not None and observation.menu_mouse_screen_point is not None:
            pointer_distance = (
                (screen_point.x - observation.menu_mouse_screen_point.x) ** 2
                + (screen_point.y - observation.menu_mouse_screen_point.y) ** 2
            ) ** 0.5
        timing = self.behavior.timing(
            decision_id,
            pointer_distance_px=pointer_distance,
            target_extent_px=extent,
            camera_moved=self._last_camera_framing is not None
            and self._last_camera_framing.action != "none",
            menu_opened=observation.menu_open,
            route_move=kind is ActionKind.WALK,
        )
        self._last_timing_decision = timing
        action = Action(
            kind, label, observation.tick, option=option, target_key=target_key,
            target_name=target_name,
            target_id=target.object_id if target is not None else None,
            screen_point=screen_point, verification=verification,
            source_menu_client_tick=observation.menu_client_tick,
            target_param0=target.scene_x if target is not None else None,
            target_param1=target.scene_y if target is not None else None,
            source_session_id=observation.session_id,
            task_constraints=task_constraints or TaskConstraints(),
            decision_id=decision_id,
            behavior_seed=self.behavior.seed,
            pre_move_delay_seconds=(
                timing.pre_move_delay_seconds + timing.route_pause_seconds
            ),
            settle_delay_seconds=timing.settle_delay_seconds,
            pre_click_delay_seconds=timing.pre_click_delay_seconds,
            post_action_delay_seconds=timing.post_action_delay_seconds,
        )
        if evidence is None and target is not None:
            evidence = self._object_decision_evidence(
                observation,
                action=option,
                selected=target,
                eligible=(target,),
            )
        evidence = self._evidence_with_behavior(
            evidence or DecisionEvidence(),
            target=target,
            selected_point=screen_point,
            aim=aim_decision,
            previous_points=previous_points,
            timing=timing,
        )
        emitted_phase = pending_phase or self.progress.phase
        decision = Decision(
            emitted_phase.value,
            reason,
            action,
            evidence,
        )
        # Commit phase and verification together only after aim selection and
        # all immutable action/evidence construction succeeded.  In
        # particular, a fresh authoritative shape may contain no safe inset
        # point after UI/competitor exclusion.  That is a non-action wait, not
        # permission to enter a verification-only phase without a verifier.
        if pending_phase is not None:
            self.progress.phase = pending_phase
        self.progress.pending = verification
        return decision

    def _next_behavior_decision_id(
        self, channel: str, target: str, tick: int
    ) -> str:
        self._decision_sequence += 1
        return (
            f"{self.progress.phase.value}:{channel}:{target}:{tick}:"
            f"{self._decision_sequence}"
        )[:128]

    @staticmethod
    def _action_target_geometry(
        observation: Observation,
        kind: ActionKind,
        target: NearbyObject | None,
        target_key: str | None,
        screen_point: ScreenPoint | None,
    ) -> TargetGeometry | None:
        if target is not None:
            return target.geometry
        if kind is not ActionKind.CLICK_WIDGET:
            return None
        widgets = {
            DEPOSIT_INVENTORY_WIDGET_KEY: observation.widgets.deposit_inventory,
            CLOSE_BANK_WIDGET_KEY: observation.widgets.close_bank,
        }
        widget = widgets.get(target_key)
        if widget is None or widget.screen_bounds is None or screen_point is None:
            return None
        return TargetGeometry(
            available=True,
            on_screen=True,
            visible=widget.visible,
            actionable=widget.visible,
            screen_point=screen_point,
            screen_bounds=widget.screen_bounds,
            geometry_source="widget_bounds",
            visible_area_ratio=1.0,
        )

    def _evidence_with_behavior(
        self,
        evidence: DecisionEvidence,
        *,
        target: NearbyObject | None,
        selected_point: ScreenPoint | None,
        aim: AimDecision | None,
        previous_points: tuple[ScreenPoint, ...],
        timing: TimingDecision,
    ) -> DecisionEvidence:
        selected = evidence.selected
        eligible = evidence.eligible
        if selected is not None and selected_point is not None:
            selected = TargetEvidence(
                key=selected.key,
                name=selected.name,
                object_id=selected.object_id,
                action=selected.action,
                source_tick=selected.source_tick,
                geometry_frame_id=selected.geometry_frame_id,
                point=selected_point,
                bounds=selected.bounds,
                world_location=selected.world_location,
                distance=selected.distance,
            )
            eligible = tuple(
                selected if candidate.key == selected.key else candidate
                for candidate in eligible
            )
        targeting = (
            TargetingDecisionEvidence(
                geometry_source=aim.geometry_source,
                shape_bounds=aim.shape_bounds,
                inset_region=aim.inset_bounds,
                candidate_points=tuple(
                    candidate.point for candidate in aim.candidates
                ),
                selected_point=aim.selected.point,
                selected_score=aim.selected.score,
                previous_points=previous_points,
                decision_id=aim.decision_id,
                seed=aim.seed,
                rejected_reasons=aim.rejected_reasons,
                shape_polygon=aim.shape_polygon,
            )
            if aim is not None
            else None
        )
        return DecisionEvidence(
            selected=selected,
            eligible=eligible,
            rejected=evidence.rejected,
            route=self._route_decision_evidence(),
            camera=self._camera_decision_evidence(),
            targeting=targeting,
            timing=TimingDecisionEvidence(
                decision_id=timing.decision_id,
                seed=timing.seed,
                pre_move_delay_seconds=timing.pre_move_delay_seconds,
                settle_delay_seconds=timing.settle_delay_seconds,
                pre_click_delay_seconds=timing.pre_click_delay_seconds,
                post_action_delay_seconds=timing.post_action_delay_seconds,
                route_pause_seconds=timing.route_pause_seconds,
            ),
        )

    def _route_decision_evidence(self) -> RouteDecisionEvidence | None:
        selection = self._last_route_selection
        if selection is None:
            return None
        route = self._current_fixed_route()
        skipped = (
            tuple(route.steps[index].step_id for index in selection.skipped_guidance_indices)
            if route is not None
            else ()
        )
        mandatory = (
            route.steps[selection.mandatory_next_index].step_id
            if route is not None and selection.mandatory_next_index is not None
            else None
        )
        return RouteDecisionEvidence(
            progress_tiles=round(selection.progress.distance_along_route, 3),
            remaining_tiles=round(selection.progress.remaining_distance, 3),
            lateral_deviation_tiles=round(selection.progress.lateral_deviation, 3),
            selected_step_id=(
                selection.selected_step.step_id
                if selection.selected_step is not None
                else None
            ),
            selected_location=(
                selection.selected_step.location
                if selection.selected_step is not None
                else None
            ),
            requested_distance_tiles=round(selection.requested_tile_distance, 3),
            expected_progress_tiles=round(selection.expected_progress, 3),
            actual_progress_tiles=(
                round(self._last_route_actual_progress_delta, 3)
                if self._last_route_actual_progress_delta is not None
                else None
            ),
            skipped_guidance_points=skipped,
            mandatory_next_step_id=mandatory,
            fallback_reason=selection.fallback_reason,
            backtracking=selection.progress.backtracking,
            zigzagging=selection.progress.zigzagging,
            projected_route_points=self._route_projected_points,
            projected_route_labels=self._route_projected_labels,
            mandatory_route_points=self._route_mandatory_points,
            skipped_route_points=self._route_skipped_points,
            selected_screen_point=self._route_selected_screen_point,
            candidate_rejections=self._route_candidate_rejections,
        )

    def _capture_route_overlay(
        self,
        route: FixedRoute,
        observation: Observation,
        selection: RouteTargetSelection,
    ) -> None:
        points: list[ScreenPoint] = []
        labels: list[str] = []
        mandatory: list[ScreenPoint] = []
        skipped: list[ScreenPoint] = []
        selected: ScreenPoint | None = None
        skipped_indices = set(selection.skipped_guidance_indices)
        for index, step in self._route_projection_steps(route):
            target = observation.object_by_key(step.target_key)
            point = target.geometry.screen_point if target is not None else None
            if point is None:
                continue
            points.append(point)
            labels.append(f"{step.step_id}:{step.classification.value}")
            if step.classification is not RoutePointClassification.NORMAL_GUIDANCE:
                mandatory.append(point)
            if index in skipped_indices:
                skipped.append(point)
            if index == selection.selected_index:
                selected = point
        self._route_projected_points = tuple(points)
        self._route_projected_labels = tuple(labels)
        self._route_mandatory_points = tuple(mandatory)
        self._route_skipped_points = tuple(skipped)
        self._route_selected_screen_point = selected

    def _camera_decision_evidence(
        self, *, action_override: str | None = None
    ) -> CameraDecisionEvidence | None:
        framing = self._last_camera_framing
        if framing is None:
            return None
        scheduled_hold = framing.hold_millis if action_override is not None else 0
        correction_attempt = self._camera_recovery_attempts + (
            1 if action_override is not None else 0
        )
        episode = self._camera_episode
        last_response = self._last_camera_response
        desired_yaw = episode.desired_yaw if episode is not None else None
        yaw_band = self.behavior.config.camera_yaw_deadband_units
        action_direction = (
            action_override
            if action_override in {"left", "right", "up", "down"}
            else None
        )
        yaw_rate = (
            self._camera_response_model.median_rate(action_direction)
            if action_direction in {"left", "right"}
            else None
        )
        pitch_rate = (
            self._camera_response_model.median_rate(action_direction)
            if action_direction in {"up", "down"}
            else None
        )
        return CameraDecisionEvidence(
            classification=framing.classification,
            desired_region=framing.desired_region,
            target_point=framing.target_point,
            action=action_override or framing.action,
            hold_millis=framing.hold_millis,
            route_direction_bias=framing.route_direction_bias,
            correction_distance_px=framing.correction_distance_px,
            framing_context=framing.framing_context,
            source_tick=framing.source_tick,
            geometry_frame_id=framing.geometry_frame_id,
            target_bounds=framing.target_bounds,
            edge_clearance_px=framing.edge_clearance_px,
            required_edge_margin_px=framing.required_edge_margin_px,
            lookahead_points=framing.lookahead_points,
            lookahead_bounds=framing.lookahead_bounds,
            yaw_error_units=framing.yaw_error_units,
            screen_correction_x_px=framing.screen_correction_x_px,
            screen_correction_y_px=framing.screen_correction_y_px,
            correction_attempt=min(
                correction_attempt,
                self.behavior.config.camera_max_corrections,
            ),
            correction_limit=self.behavior.config.camera_max_corrections,
            cumulative_hold_millis=(
                self._camera_recovery_total_hold_millis + scheduled_hold
            ),
            acquisition_state=(
                episode.state
                if episode is not None
                else CameraAcquisitionState.IDLE
            ),
            episode_id=(episode.episode_id if episode is not None else None),
            locked_target_key=(
                episode.locked_target_key if episode is not None else None
            ),
            locked_target_kind=(
                episode.locked_target_kind if episode is not None else None
            ),
            desired_yaw=desired_yaw,
            desired_yaw_min=(
                None
                if desired_yaw is None
                else (desired_yaw - yaw_band) % CAMERA_YAW_UNITS
            ),
            desired_yaw_max=(
                None
                if desired_yaw is None
                else (desired_yaw + yaw_band) % CAMERA_YAW_UNITS
            ),
            desired_pitch=(
                episode.desired_pitch if episode is not None else None
            ),
            desired_pitch_min=(
                episode.desired_pitch_min if episode is not None else None
            ),
            desired_pitch_max=(
                episode.desired_pitch_max if episode is not None else None
            ),
            pitch_error_units=(
                episode.pitch_error_units if episode is not None else None
            ),
            pitch_valid=bool(framing.pitch_valid),
            visible_area_ratio=framing.visible_area_ratio,
            zoom_classification=framing.zoom_classification,
            zoom_required_but_unavailable=(
                framing.zoom_required_but_unavailable
            ),
            capability_max_hold_millis=(
                self.behavior.camera_capabilities.max_hold_millis
            ),
            response_sample_count=len(self._camera_response_model.samples),
            calibrated_yaw_units_per_millis=yaw_rate,
            calibrated_pitch_units_per_millis=pitch_rate,
            last_observed_yaw_delta=(
                last_response.observed_yaw_delta
                if last_response is not None
                else None
            ),
            last_observed_pitch_delta=(
                last_response.observed_pitch_delta
                if last_response is not None
                else None
            ),
            last_response_no_effect=(
                last_response.effective_no_effect
                if last_response is not None
                else False
            ),
            pitch_limit_direction=(
                episode.pitch_limit_direction if episode is not None else None
            ),
            overshoot_proven=(
                episode.overshoot_proven if episode is not None else False
            ),
            retained_reason=(
                episode.retained_reason if episode is not None else None
            ),
        )

    def _current_fixed_route(self) -> FixedRoute | None:
        phase = (
            self.progress.resume_phase
            if self.progress.phase is TaskPhase.STAIR_DIALOGUE
            else self.progress.phase
        )
        if phase == TaskPhase.NAVIGATE_TO_BANK:
            return self.definition.route_to_bank
        if phase == TaskPhase.NAVIGATE_TO_TREES:
            return self.definition.route_to_resource
        return None

    def _current_route(self) -> tuple[FixedRouteStep, ...] | None:
        route = self._current_fixed_route()
        return route.steps if route is not None else None

    def _route_projection_steps(
        self, route: FixedRoute
    ) -> tuple[tuple[int, FixedRouteStep], ...]:
        if self.progress.route_index < 0 or self.progress.route_index >= len(route.steps):
            return ()
        projections: list[tuple[int, FixedRouteStep]] = []
        for index in range(self.progress.route_index, len(route.steps)):
            step = route.steps[index]
            if not step.is_walk:
                break
            projections.append((index, step))
            if len(projections) >= self.behavior.config.route_lookahead_points:
                break
            # A stale cursor can begin on a mandatory turn that current
            # polyline progress has already passed. Include that current point
            # for evidence, then continue only as far as the next barrier.
            if (
                index > self.progress.route_index
                and step.classification
                is not RoutePointClassification.NORMAL_GUIDANCE
            ):
                break
        return tuple(projections)

    def _route_camera_lookahead_points(
        self,
        observation: Observation,
        selected_step: FixedRouteStep,
    ) -> tuple[ScreenPoint, ...]:
        """Return fresh projected route context through the next route barrier."""

        route = self._current_fixed_route()
        if route is None:
            return ()
        selected_target = observation.object_by_key(selected_step.target_key)
        selected_point = (
            selected_target.geometry.screen_point
            if selected_target is not None
            else None
        )
        capacity = self.behavior.config.route_lookahead_points - 1
        if capacity <= 0:
            return ()
        points: list[ScreenPoint] = []
        for _, step in self._route_projection_steps(route):
            target = observation.object_by_key(step.target_key)
            point = target.geometry.screen_point if target is not None else None
            if (
                point is None
                or point == selected_point
                or point in points
            ):
                continue
            points.append(point)
            if len(points) >= capacity:
                break
        return tuple(points)

    def _route_selection_limits(self) -> RouteSelectionLimits:
        config = self.behavior.config
        return RouteSelectionLimits(
            corridor_limit_tiles=config.route_corridor_radius_tiles,
            max_click_distance_tiles=float(config.route_max_click_tiles),
            open_click_floor_tiles=float(config.route_open_click_floor_tiles),
            max_skipped_turn_degrees=config.route_turn_limit_degrees,
        )

    def _route_candidate_support(
        self,
        route: FixedRoute,
        observation: Observation,
    ) -> tuple[RouteCandidateSupport, ...]:
        supports: list[RouteCandidateSupport] = []
        for index, step in self._route_projection_steps(route):
            target = observation.object_by_key(step.target_key)
            identity_matches = self._walk_projection_identity_matches(target, step)
            legacy_current = index == self.progress.route_index
            geometry = target.geometry if target is not None else None
            has_geometry = bool(
                identity_matches
                and target is not None
                and self._has_geometry(target)
            )
            scene_supported = bool(
                identity_matches
                and geometry is not None
                and (
                    geometry.scene_supported
                    if geometry.scene_supported is not None
                    else legacy_current or geometry.available
                )
            )
            collision_supported = bool(
                identity_matches
                and geometry is not None
                and (
                    geometry.collision_supported
                    if geometry.collision_supported is not None
                    else legacy_current
                )
            )
            shortcut_clear = bool(
                identity_matches
                and geometry is not None
                and (
                    geometry.shortcut_clear
                    if geometry.shortcut_clear is not None
                    else legacy_current or geometry.collision_supported is True
                )
            )
            point = geometry.screen_point if geometry is not None else None
            viewport = observation.viewport_bounds or observation.canvas_bounds
            ui_clear = bool(
                identity_matches
                and not (
                    point is not None
                    and observation.menu_open
                    and observation.menu_bounds is not None
                    and observation.menu_bounds.contains(point)
                )
            )
            supports.append(
                RouteCandidateSupport(
                    route_index=index,
                    plane_supported=bool(
                        identity_matches and observation.plane == step.location.plane
                    ),
                    scene_supported=scene_supported,
                    collision_supported=collision_supported,
                    projectable=has_geometry,
                    ui_clear=ui_clear,
                    camera_adjustable=bool(identity_matches and not has_geometry),
                    shortcut_clear=shortcut_clear,
                )
            )
        return tuple(supports)

    def _prepare_route_diagnostics(self, route: FixedRoute) -> None:
        if self._route_diagnostics_id == route.route_id:
            return
        self._route_diagnostics_id = route.route_id
        self._last_route_selection = None
        self._last_route_progress = None
        self._last_route_actual_progress_delta = None
        self._pending_route_start_progress = None
        self._route_candidate_rejections = ()

    def _finish_route(self) -> None:
        if self._target_lock is not None:
            self._clear_target_lock("fixed route completed")
        if self.progress.phase == TaskPhase.NAVIGATE_TO_BANK:
            self.progress.phase = TaskPhase.OPEN_BANK
        elif self.progress.phase == TaskPhase.NAVIGATE_TO_TREES:
            if self._restart_reconciled_without_cycle_credit:
                self._restart_reconciled_without_cycle_credit = False
                self.progress.route_index = 0
                self.progress.phase = TaskPhase.FIND_TREE
                return
            self.progress.cycles_completed += 1
            self.progress.phase = (
                TaskPhase.COMPLETE
                if self.progress.cycles_completed >= self.binding.profile.cycle_goal
                else TaskPhase.FIND_TREE
            )

    def _contextual_progress_phase(self) -> TaskPhase | None:
        return (
            self.progress.resume_phase
            if self.progress.phase is TaskPhase.STAIR_DIALOGUE
            else (
                self.progress.blocked_from_phase
                if self.progress.phase is TaskPhase.BLOCKED
                and self.progress.blocked_from_phase is not None
                else self.progress.phase
            )
        )

    def _route_context_snapshot(
        self,
    ) -> tuple[str | None, TaskProgressSnapshot | None]:
        phase = self._contextual_progress_phase()
        if phase is TaskPhase.NAVIGATE_TO_BANK:
            route = self.definition.route_to_bank
        elif phase is TaskPhase.NAVIGATE_TO_TREES:
            route = self.definition.route_to_resource
        else:
            return None, None
        route_index = self.progress.route_index
        route_step = (
            route.steps[route_index].step_id
            if 0 <= route_index < len(route.steps)
            else None
        )
        return route_step, TaskProgressSnapshot(
            route.route_id,
            route_index,
            len(route.steps),
        )

    def _cycle_progress_snapshot(self) -> TaskProgressSnapshot:
        return TaskProgressSnapshot(
            "cycles",
            self.progress.cycles_completed,
            self.binding.profile.cycle_goal,
        )

    def _target_continuity_snapshot(self) -> TargetContinuityEvidence:
        lock = self._target_lock
        if lock is None:
            return TargetContinuityEvidence(
                last_unlock_reason=self._last_target_unlock_reason
            )
        return TargetContinuityEvidence(
            locked_target_key=lock.key,
            locked_tick=lock.locked_tick,
            last_seen_tick=lock.last_seen_tick,
            incomplete_omission_frames=lock.incomplete_omission_frames,
            retention_reason=lock.retained_reason,
            last_unlock_reason=self._last_target_unlock_reason,
        )

    @staticmethod
    def _target_lock_matches(
        lock: TargetContinuityLock,
        target: NearbyObject,
    ) -> bool:
        return bool(
            target.key == lock.key
            and target.object_id == lock.object_id
            and target.name == lock.name
            and target.kind == lock.kind
            and target.location == lock.location
        )

    def _lock_target(
        self,
        observation: Observation,
        target: NearbyObject,
        *,
        action: str,
        context: str,
    ) -> bool:
        if target.location is None:
            return False
        current = self._target_lock
        if current is not None and current.key == target.key:
            if not self._target_lock_matches(current, target):
                current.retained_reason = (
                    "same stable key returned contradictory immutable identity"
                )
                return False
            current.last_seen_tick = observation.tick
            current.incomplete_omission_frames = 0
            current.retained_reason = "fresh exact identity retained"
            return True
        if current is not None:
            self._clear_target_lock(
                "deterministic phase selection replaced the previous target"
            )
        self._target_lock = TargetContinuityLock(
            context=context,
            key=target.key,
            object_id=target.object_id,
            name=target.name,
            kind=target.kind,
            action=action,
            location=target.location,
            locked_tick=observation.tick,
            last_seen_tick=observation.tick,
            retained_reason="selected from fresh exact identity",
        )
        return True

    def _clear_target_lock(self, reason: str) -> None:
        self._last_target_unlock_reason = reason
        self._target_lock = None

    def _touch_target_lock(
        self,
        observation: Observation,
        target: NearbyObject,
    ) -> bool:
        lock = self._target_lock
        if lock is None:
            return False
        if not self._target_lock_matches(lock, target):
            lock.retained_reason = (
                "same stable key returned contradictory immutable identity"
            )
            return False
        lock.last_seen_tick = observation.tick
        lock.incomplete_omission_frames = 0
        lock.retained_reason = "fresh exact identity retained"
        return True

    @staticmethod
    def _scene_census(observation: Observation) -> object | None:
        return getattr(observation, "scene_census", None)

    def _target_omission_status(
        self,
        observation: Observation,
    ) -> tuple[str, str, str]:
        lock = self._target_lock
        if lock is None:
            return (
                "legacy_unknown",
                "target lock is unavailable for omission continuity",
                "target_lock_unavailable",
            )
        census = self._scene_census(observation)
        conflicts = tuple(
            getattr(census, "conflicting_duplicate_keys", ()) or ()
        )
        if lock.key in conflicts:
            reason = (
                "locked target was quarantined because duplicate rows "
                "contradicted immutable identity"
            )
            lock.retained_reason = reason
            return "contradictory", reason, "contradictory_duplicate_identity"

        requested_priority_keys = tuple(
            getattr(census, "requested_priority_object_keys", ()) or ()
        )
        returned_priority_keys = tuple(
            getattr(census, "returned_priority_object_keys", ()) or ()
        )
        exact_priority_absence = bool(
            getattr(census, "priority_absence_eligible", None) is True
            and lock.key in requested_priority_keys
            and lock.key not in returned_priority_keys
        )
        # Diagnostic completeness is insufficient for negative proof because
        # legacy v1 censuses did not prove authoritative anchor coverage. A
        # capped response may still prove one exact requested key absent from
        # the complete raw census through the separate priority policy.
        if (
            getattr(census, "authoritative_absence_eligible", None) is True
            or exact_priority_absence
        ):
            reason = (
                "locked target is absent from an authoritative exact-priority census"
            )
            lock.retained_reason = reason
            return "authoritative_absence", reason, "authoritative_target_absence"

        lock.incomplete_omission_frames = min(
            lock.incomplete_omission_frames + 1,
            TARGET_INCOMPLETE_OMISSION_WAIT_FRAMES + 1,
        )
        explicit_incomplete = self._census_is_explicitly_incomplete(census)
        if (
            lock.incomplete_omission_frames
            > TARGET_INCOMPLETE_OMISSION_WAIT_FRAMES
        ):
            reason = (
                "locked target remains retained after the bounded incomplete-frame "
                "wait budget; authoritative absence is required before unlock"
            )
            code = "target_omission_wait_exhausted"
        elif explicit_incomplete:
            reason = (
                "locked target omitted by an explicitly incomplete census; retained "
                f"for continuity frame {lock.incomplete_omission_frames}/"
                f"{TARGET_INCOMPLETE_OMISSION_WAIT_FRAMES}"
            )
            code = "incomplete_census_target_omitted"
        else:
            reason = (
                "locked target omitted while census authority is unknown; retained "
                f"for continuity frame {lock.incomplete_omission_frames}/"
                f"{TARGET_INCOMPLETE_OMISSION_WAIT_FRAMES}"
            )
            code = "unknown_census_target_omitted"
        lock.retained_reason = reason
        return "retained", reason, code

    @staticmethod
    def _census_is_explicitly_incomplete(census: object | None) -> bool:
        if census is None or getattr(census, "metadata_present", False) is not True:
            return False
        return bool(
            getattr(census, "complete", None) is False
            or getattr(census, "scene_coverage_complete", None) is False
            or getattr(census, "source_cap_hit", None) is True
        )

    def _locked_target_rejection_evidence(
        self,
        observation: Observation,
        code: str,
    ) -> DecisionEvidence:
        lock = self._target_lock
        if lock is None:
            return DecisionEvidence()
        target = TargetEvidence(
            key=lock.key,
            name=lock.name,
            object_id=lock.object_id,
            action=lock.action,
            source_tick=observation.tick,
            geometry_frame_id=observation.geometry_frame_id,
            point=None,
            bounds=None,
            world_location=lock.location,
            distance=(
                observation.location.distance_to(lock.location)
                if observation.location is not None
                else None
            ),
        )
        return DecisionEvidence(
            rejected=(RejectedCandidateEvidence(target, (code,)),)
        )

    def _handle_locked_target_omission(
        self,
        observation: Observation,
    ) -> Decision:
        lock = self._target_lock
        if lock is None:
            return self._wait(observation, "target lock is unavailable")
        status, reason, code = self._target_omission_status(observation)
        evidence = self._evidence_with_camera(
            self._locked_target_rejection_evidence(observation, code)
        )
        if status == "contradictory":
            return self._block(observation, reason, evidence=evidence)
        if status == "authoritative_absence":
            context = lock.context
            if context == "resource":
                self.progress.target_key = None
                self.progress.phase = TaskPhase.FIND_TREE
            self._reset_camera_recovery()
            self._clear_target_lock(reason)
            return self._wait(
                observation,
                f"{reason}; target unlocked for deterministic replanning",
                evidence=evidence,
            )
        if self._camera_episode is not None:
            self._camera_episode.retained_reason = reason
        if code == "target_omission_wait_exhausted":
            return self._block(observation, reason, evidence=evidence)
        return self._wait(observation, reason, evidence=evidence)

    def _activation_census_rejection_codes(
        self,
        observation: Observation,
        target: NearbyObject,
    ) -> tuple[str, ...]:
        census = self._scene_census(observation)
        if census is None or getattr(census, "metadata_present", False) is not True:
            return ("census_authority_unknown_for_activation",)
        conflicts = tuple(
            getattr(census, "conflicting_duplicate_keys", ()) or ()
        )
        if target.key in conflicts:
            return ("contradictory_duplicate_identity",)
        if (
            getattr(census, "complete", None) is not True
            or getattr(census, "scene_coverage_complete", None) is not True
        ):
            return ("incomplete_census_for_activation",)
        return ()

    @staticmethod
    def _target_evidence(
        observation: Observation,
        target: NearbyObject,
        action: str | None,
    ) -> TargetEvidence:
        return TargetEvidence(
            key=target.key,
            name=target.name,
            object_id=target.object_id,
            action=action,
            source_tick=observation.tick,
            geometry_frame_id=observation.geometry_frame_id,
            point=target.geometry.screen_point,
            bounds=target.geometry.screen_bounds,
            world_location=target.location,
            distance=target.distance,
        )

    def _object_decision_evidence(
        self,
        observation: Observation,
        *,
        action: str,
        selected: NearbyObject | None = None,
        eligible: tuple[NearbyObject, ...] = (),
        rejected: tuple[tuple[NearbyObject, tuple[str, ...]], ...] = (),
        candidate_actions: dict[str, str] | None = None,
    ) -> DecisionEvidence:
        actions = candidate_actions or {}

        def target_evidence(target: NearbyObject) -> TargetEvidence:
            return self._target_evidence(
                observation,
                target,
                actions.get(target.key, action),
            )

        eligible_evidence = tuple(target_evidence(target) for target in eligible)
        selected_evidence = target_evidence(selected) if selected is not None else None
        ordered_rejected = nsmallest(
            MAX_TARGET_REJECTION_EVIDENCE,
            rejected,
            key=lambda item: item[0].key,
        )
        rejected_evidence = tuple(
            RejectedCandidateEvidence(target_evidence(target), codes)
            for target, codes in ordered_rejected
        )
        return DecisionEvidence(
            selected=selected_evidence,
            eligible=eligible_evidence,
            rejected=rejected_evidence,
        )

    @staticmethod
    def _widget_target_evidence(
        observation: Observation,
        key: str,
        target: WidgetTarget,
        action: str,
    ) -> TargetEvidence:
        return TargetEvidence(
            key=key,
            name=target.name,
            object_id=None,
            action=action,
            source_tick=observation.tick,
            geometry_frame_id=observation.geometry_frame_id,
            point=target.screen_point,
            bounds=target.screen_bounds,
        )

    @staticmethod
    def _dialogue_target_evidence(
        observation: Observation, option: DialogueOption
    ) -> TargetEvidence:
        return TargetEvidence(
            key=f"dialogue:{option.index}",
            name=option.text,
            object_id=option.index,
            action=option.text,
            source_tick=observation.tick,
            geometry_frame_id=observation.geometry_frame_id,
            point=None,
            bounds=None,
        )

    @staticmethod
    def _single_selected_evidence(target: TargetEvidence) -> DecisionEvidence:
        return DecisionEvidence(selected=target, eligible=(target,))

    @staticmethod
    def _single_rejected_evidence(
        target: TargetEvidence, codes: tuple[str, ...]
    ) -> DecisionEvidence:
        return DecisionEvidence(
            rejected=(RejectedCandidateEvidence(target, codes),)
        )

    def _classify_trees(
        self, observation: Observation
    ) -> tuple[
        tuple[NearbyObject, ...],
        tuple[tuple[NearbyObject, tuple[str, ...]], ...],
    ]:
        selector_ids = tuple(sorted(self.definition.resource.selector.object_ids))
        indexed_by_key: dict[str, NearbyObject] = {}
        objects_by_id = getattr(observation, "objects_by_id", None)
        if callable(objects_by_id):
            for object_id in selector_ids:
                for target in objects_by_id(object_id):
                    indexed_by_key[target.key] = target
        else:
            for target in observation.nearby_objects:
                if target.object_id in selector_ids:
                    indexed_by_key[target.key] = target
        indexed_candidates = tuple(
            indexed_by_key[key] for key in sorted(indexed_by_key)
        )

        acquirable: list[NearbyObject] = []
        rejected: list[tuple[NearbyObject, tuple[str, ...]]] = []
        for target in indexed_candidates:
            codes = self._tree_identity_rejection_codes(target)
            if codes:
                rejected.append((target, self._tree_rejection_codes(target)))
            else:
                acquirable.append(target)

        # Preserve a bounded deterministic diagnostic sample of irrelevant
        # rows without running selection, camera scoring, or ambiguity work on
        # the entire scene.
        rejected_keys = set(indexed_by_key)
        remaining_evidence = max(
            0,
            MAX_TARGET_REJECTION_EVIDENCE - len(rejected),
        )
        irrelevant: list[NearbyObject] = []
        if remaining_evidence:
            irrelevant = nsmallest(
                remaining_evidence,
                (
                    target
                    for target in observation.nearby_objects
                    if target.key not in rejected_keys
                ),
                key=lambda target: target.key,
            )
            rejected.extend(
                (target, self._tree_rejection_codes(target))
                for target in irrelevant
            )

        occluded_keys = self._tree_aim_occluded_keys(tuple(acquirable))
        eligible: list[NearbyObject] = []
        for target in acquirable:
            if (
                not self._has_geometry(target)
                or target.key not in occluded_keys
            ):
                eligible.append(target)
            else:
                rejected.append((target, ("aim_point_occluded",)))

        ranked = nsmallest(
            MAX_TARGET_CANDIDATES,
            eligible,
            key=lambda target: self._resource_selection_rank(
                observation,
                target,
            ),
        )
        ranked_keys = {target.key for target in ranked}
        rejected.extend(
            (target, ("candidate_budget_exceeded",))
            for target in eligible
            if target.key not in ranked_keys
        )
        self._last_resource_selection_metrics = {
            "scene_objects": len(observation.nearby_objects),
            "indexed_candidates": len(indexed_candidates),
            "identity_evaluations": len(indexed_candidates) + len(irrelevant),
            "ambiguity_queries": sum(
                1 for target in acquirable if self._has_geometry(target)
            ),
            "ranked_candidates": len(ranked),
            "rejection_evidence": min(
                len(rejected),
                MAX_TARGET_REJECTION_EVIDENCE,
            ),
        }
        return tuple(ranked), tuple(rejected)

    def _resource_selection_rank(
        self,
        observation: Observation,
        target: NearbyObject,
    ) -> tuple[int, int, float, float, str]:
        """Prefer exact resources that need no camera action before distance."""

        distance = observation.location.distance_to(target.location)
        if not self._has_geometry(target) or target.location is None:
            return (2, 5, float("inf"), distance, target.key)
        if observation.viewport_bounds is None:
            return (0, 0, 0.0, distance, target.key)
        yaw_error = (
            self._camera_yaw_error(
                observation.location,
                target.location,
                observation.camera_yaw,
            )
            if observation.camera_yaw is not None
            else None
        )
        framing = self.behavior.classify_camera(
            target.geometry,
            observation.viewport_bounds,
            decision_id=(
                f"resource-rank:{target.key}:{observation.tick}:"
                f"{observation.geometry_frame_id or 'no-geometry-frame'}"
            ),
            route_dx=target.location.x - observation.location.x,
            route_dy=target.location.y - observation.location.y,
            player_point=observation.player_screen_point,
            framing_context="interaction",
            yaw_error_units=yaw_error,
            source_tick=observation.tick,
            geometry_frame_id=observation.geometry_frame_id,
        )
        return (
            0 if framing.action == "none" else 1,
            -CAMERA_FRAMING_CLASSIFICATION_RANK.get(
                framing.classification,
                -1,
            ),
            framing.correction_distance_px,
            distance,
            target.key,
        )

    def _prune_resource_camera_suppressions(self, tick: int) -> None:
        self._resource_camera_suppressions = {
            key: expires_tick
            for key, expires_tick in self._resource_camera_suppressions.items()
            if expires_tick > tick
        }

    def _classify_route_objects(
        self, observation: Observation, step: FixedRouteStep
    ) -> tuple[
        tuple[NearbyObject, ...],
        tuple[tuple[NearbyObject, tuple[str, ...]], ...],
        dict[str, str],
    ]:
        eligible: list[NearbyObject] = []
        rejected: list[tuple[NearbyObject, tuple[str, ...]]] = []
        actions: dict[str, str] = {}
        indexed = (
            observation.objects_by_id(step.object_id)
            if step.object_id is not None
            else ()
        )
        indexed_keys = {target.key for target in indexed}
        diagnostic_rows = nsmallest(
            MAX_TARGET_REJECTION_EVIDENCE,
            (
                target
                for target in observation.nearby_objects
                if target.key not in indexed_keys
            ),
            key=lambda target: target.key,
        )
        for target in (*indexed, *diagnostic_rows):
            codes = []
            if target.object_id != step.object_id:
                codes.append("object_id_mismatch")
            if target.name != step.object_name:
                codes.append("name_mismatch")
            if target.location is None:
                codes.append("location_unavailable")
            else:
                if target.location.plane != step.location.plane:
                    codes.append("wrong_plane")
                elif target.location.distance_to(step.location) > step.arrival_radius:
                    codes.append("outside_step_radius")
                if observation.location.distance_to(target.location) > step.arrival_radius:
                    codes.append("outside_interaction_radius")
            route_option = self._route_option(target, step)
            if route_option is None:
                codes.append("action_unavailable")
            if codes:
                rejected.append((target, tuple(codes)))
            else:
                eligible.append(target)
                assert route_option is not None
                actions[target.key] = route_option
        eligible.sort(
            key=lambda target: (
                observation.location.distance_to(target.location),
                target.key,
            )
        )
        return tuple(eligible), tuple(rejected), actions

    def _classify_bank_objects(
        self, observation: Observation
    ) -> tuple[
        tuple[NearbyObject, ...],
        tuple[tuple[NearbyObject, tuple[str, ...]], ...],
    ]:
        bank = self.definition.bank
        selector = bank.selector
        eligible: list[NearbyObject] = []
        rejected: list[tuple[NearbyObject, tuple[str, ...]]] = []
        indexed_by_key: dict[str, NearbyObject] = {}
        for object_id in sorted(selector.object_ids):
            for target in observation.objects_by_id(object_id):
                indexed_by_key[target.key] = target
        diagnostic_rows = nsmallest(
            MAX_TARGET_REJECTION_EVIDENCE,
            (
                target
                for target in observation.nearby_objects
                if target.key not in indexed_by_key
            ),
            key=lambda target: target.key,
        )
        for target in (
            *(indexed_by_key[key] for key in sorted(indexed_by_key)),
            *diagnostic_rows,
        ):
            codes = []
            if target.object_id not in selector.object_ids:
                codes.append("object_id_not_supported")
            if target.name != selector.name:
                codes.append("name_mismatch")
            if target.location != bank.anchor:
                codes.append("location_mismatch")
            if not target.supports(selector.action):
                codes.append("action_unavailable")
            if (
                target.location is not None
                and observation.location.distance_to(target.location)
                > bank.interaction_radius
            ):
                codes.append("outside_interaction_radius")
            if codes:
                rejected.append((target, tuple(codes)))
            else:
                eligible.append(target)
        eligible.sort(key=lambda target: target.key)
        return tuple(eligible), tuple(rejected)

    @staticmethod
    def _route_option(target: NearbyObject, step: FixedRouteStep) -> str | None:
        if target.supports(step.action):
            return step.action
        return next(
            (option for option in target.actions if option in step.allowed_actions),
            None,
        )

    def _walk_projection_identity_matches(
        self, target: NearbyObject | None, step: FixedRouteStep
    ) -> bool:
        return bool(
            target is not None
            and target.key == step.target_key
            and target.object_id == 0
            and target.name == step.target_key
            and target.kind == "NAVIGATION_TILE"
            and target.actions == ("Walk here",)
            and target.location == step.location
            and target.scene_x is not None
            and target.scene_y is not None
        )

    @staticmethod
    def _walk_projection_rejection_codes(
        target: NearbyObject, step: FixedRouteStep
    ) -> tuple[str, ...]:
        codes = []
        if target.key != step.target_key:
            codes.append("key_mismatch")
        if target.object_id != 0:
            codes.append("object_id_mismatch")
        if target.name != step.target_key:
            codes.append("name_mismatch")
        if target.kind != "NAVIGATION_TILE":
            codes.append("kind_mismatch")
        if target.actions != ("Walk here",):
            codes.append("actions_mismatch")
        if target.location != step.location:
            codes.append("location_mismatch")
        if target.scene_x is None or target.scene_y is None:
            codes.append("scene_coordinates_unavailable")
        return tuple(codes)

    def _is_exact_walk_projection(
        self, target: NearbyObject | None, step: FixedRouteStep
    ) -> bool:
        return bool(
            self._walk_projection_identity_matches(target, step)
            and target is not None
            and self._has_geometry(target)
        )

    def _is_actionable_tree(self, target: NearbyObject) -> bool:
        return not self._tree_rejection_codes(target)

    def _tree_rejection_codes(self, target: NearbyObject) -> tuple[str, ...]:
        return (
            *self._tree_identity_rejection_codes(target),
            *self._geometry_rejection_codes(target),
        )

    def _tree_identity_rejection_codes(
        self, target: NearbyObject
    ) -> tuple[str, ...]:
        selector = self.definition.resource.selector
        work_area = self.definition.resource.work_area
        codes = []
        if target.object_id not in selector.object_ids:
            codes.append("object_id_not_supported")
        if target.name != selector.name:
            codes.append("name_mismatch")
        if not target.supports(selector.action):
            codes.append("action_unavailable")
        if target.location is None:
            codes.append("location_unavailable")
        else:
            if target.location.plane != work_area.anchor.plane:
                codes.append("wrong_plane")
            elif target.location.distance_to(work_area.anchor) > work_area.radius:
                codes.append("outside_work_area")
        if target.scene_x is None or target.scene_y is None:
            codes.append("scene_coordinates_unavailable")
        return tuple(codes)

    @staticmethod
    def _tree_aim_is_unambiguous(
        target: NearbyObject, actionable: list[NearbyObject]
    ) -> bool:
        # Polygon-backed targets are resolved against fresh, inset geometry in
        # the engine-owned aim policy. A competing object over the historical
        # canonical point must not hide other safe portions of a large shape.
        if target.geometry.screen_polygon:
            return True
        point = target.geometry.screen_point
        if point is None:
            return False
        return target.key not in WoodcutBankTask._tree_aim_occluded_keys(
            tuple(actionable)
        )

    @staticmethod
    def _tree_aim_occluded_keys(
        actionable: tuple[NearbyObject, ...],
    ) -> set[str]:
        """Index candidate bounds into bounded screen cells for point queries."""

        by_cell: dict[tuple[int, int], list[NearbyObject]] = {}
        broad: list[NearbyObject] = []
        cell_size = _AIM_OCCLUSION_CELL_PIXELS
        for candidate in actionable:
            bounds = candidate.geometry.screen_bounds
            if bounds is None:
                continue
            min_x = bounds.x // cell_size
            max_x = (bounds.x + bounds.width - 1) // cell_size
            min_y = bounds.y // cell_size
            max_y = (bounds.y + bounds.height - 1) // cell_size
            cell_count = (max_x - min_x + 1) * (max_y - min_y + 1)
            if cell_count > _AIM_OCCLUSION_MAX_CELLS_PER_BOUNDS:
                broad.append(candidate)
                continue
            for cell_x in range(min_x, max_x + 1):
                for cell_y in range(min_y, max_y + 1):
                    by_cell.setdefault((cell_x, cell_y), []).append(candidate)

        occluded: set[str] = set()
        for target in actionable:
            if target.geometry.screen_polygon:
                continue
            point = target.geometry.screen_point
            if point is None:
                continue
            candidates = (
                *by_cell.get((point.x // cell_size, point.y // cell_size), ()),
                *broad,
            )
            seen: set[str] = set()
            for other in candidates:
                if other.key in seen:
                    continue
                seen.add(other.key)
                if (
                    other.key != target.key
                    and other.geometry.screen_bounds is not None
                    and other.geometry.screen_bounds.contains(point)
                ):
                    occluded.add(target.key)
                    break
        return occluded

    @staticmethod
    def _has_geometry(target: NearbyObject) -> bool:
        geometry = target.geometry
        point = geometry.screen_point
        return bool(
            geometry.available
            and geometry.on_screen
            and geometry.visible
            and geometry.actionable
            and point is not None
            and (
                geometry.geometry_source
                not in {"clickbox", "convex_hull", "canvas_tile"}
                or bool(geometry.screen_polygon)
            )
            and (
                geometry.screen_bounds is None
                or geometry.screen_bounds.contains(point)
            )
        )

    @staticmethod
    def _geometry_rejection_codes(target: NearbyObject) -> tuple[str, ...]:
        geometry = target.geometry
        codes = []
        if not geometry.available:
            codes.append("geometry_unavailable")
        if not geometry.on_screen:
            codes.append("off_screen")
        if not geometry.visible:
            codes.append("not_visible")
        if not geometry.actionable:
            codes.append("not_actionable")
        if (
            geometry.geometry_source in {"clickbox", "convex_hull", "canvas_tile"}
            and not geometry.screen_polygon
        ):
            codes.append("authoritative_polygon_missing")
        if geometry.screen_point is None:
            codes.append("screen_point_unavailable")
        elif (
            geometry.screen_bounds is not None
            and not geometry.screen_bounds.contains(geometry.screen_point)
        ):
            codes.append("screen_point_outside_bounds")
        return tuple(codes)

    def _block(
        self,
        observation: Observation,
        reason: str,
        *,
        evidence: DecisionEvidence | None = None,
    ) -> Decision:
        self._set_blocked(reason)
        self.progress.pending = None
        return self._wait(observation, reason, evidence=evidence)

    def _set_blocked(self, reason: str) -> None:
        source_phase = (
            self.progress.resume_phase
            if self.progress.phase is TaskPhase.STAIR_DIALOGUE
            and self.progress.resume_phase is not None
            else self.progress.phase
        )
        if source_phase is not TaskPhase.BLOCKED:
            self.progress.blocked_from_phase = source_phase
        self.progress.phase = TaskPhase.BLOCKED
        self.progress.failures.append(reason)

    def _wait(
        self,
        observation: Observation,
        reason: str,
        *,
        evidence: DecisionEvidence | None = None,
    ) -> Decision:
        return Decision(
            self.progress.phase.value,
            reason,
            Action(
                ActionKind.WAIT,
                "Wait",
                observation.tick,
                verification=self.progress.pending,
            ),
            evidence or DecisionEvidence(),
        )

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Protocol

from .action import (
    CoordinatedActionInterface,
    ExecutionResult,
    UnsentActionDisposition,
)
from .configuration import DEFAULT_RUNTIME_CONFIG, RuntimeConfig
from .engine_frame import (
    EngineFrame,
    EngineFramePublisher,
    EngineStage,
    ObservationReference,
)
from .input_coordinator import (
    CameraInputVerificationEvidence,
    CursorInvalidationCause,
    InputCoordinator,
    InputFailureKind,
    InputReceipt,
)
from .model import (
    Action,
    ActionKind,
    Observation,
    SceneCensusEvidence,
    ScreenBounds,
    VerificationKind,
    VerificationSpec,
)
from .observation import ObservationClient
from .observability import (
    ObservabilityEvidence,
    TimingEvidence,
    TimingPhase,
    WaitState,
    safe_elapsed_millis,
)
from .task_contract import Decision, ObservationRequest, Task, TaskSnapshot, TaskStatus
from .verification import (
    VerificationFailureKind,
    VerificationResult,
    VerificationStatus,
    Verifier,
)


LIVE_FOCUS_HANDOFF_SECONDS = 60.0
MAX_CONSECUTIVE_UNSENT_REPLANS = 1
_PREACTIVATION_COMMANDS = frozenset(
    {"STOP_ALL", "PING", "IDENTIFY", "CAPS", "STATUS", "ARM", "MOVE", "DISARM"}
)


def _fetch_observation_request(
    client: ObservationClient,
    request: ObservationRequest,
) -> Observation:
    """Use the typed query-plan API while preserving legacy test/client seams."""
    planned_fetch = getattr(client, "fetch_planned", None)
    if callable(planned_fetch):
        return planned_fetch(request)
    observation = client.fetch(
        request.tile_projections,
        request.priority_object_ids,
    )
    # A legacy client can honor only tile projections and priority IDs.  If the
    # task requested any newer coverage or response-shaping field, the returned
    # census is still readable but cannot truthfully prove coverage or absence
    # for the dropped plan.
    if (
        request.priority_object_keys
        or request.center_world_location is not None
        or request.radius_tiles is not None
        or request.max_objects is not None
        or request.max_projection_objects is not None
    ):
        census = getattr(observation, "scene_census", SceneCensusEvidence())
        observation = replace(
            observation,
            scene_census=replace(
                census,
                complete=None,
                authoritative_absence_eligible=False,
                priority_absence_eligible=False,
                scene_coverage_complete=None,
            ),
        )
    return observation


def _may_replan_unsent_action(
    execution: ExecutionResult,
    consecutive_replans: int,
    *,
    cursor_recovery_used: bool = False,
    cursor_retry_pending: bool = False,
) -> bool:
    """Accept only a fully cleaned, pre-activation target-lifecycle rejection."""

    receipt = execution.receipt
    common = bool(
        not execution.activation_attempted
        and execution.status == "BLOCKED"
        and receipt is not None
        and all(
            command.command in _PREACTIVATION_COMMANDS
            for command in receipt.commands
        )
    )
    if not common or receipt is None:
        return False
    if cursor_retry_pending:
        # Recovery authorizes exactly one fresh executable retry.  Any unsent
        # result from that retry is the second failure and is terminal,
        # regardless of whether its immediate cause is cursor or semantics.
        return False
    if (
        execution.unsent_disposition
        is UnsentActionDisposition.CURSOR_STATE_INVALIDATED
    ):
        cause = receipt.cursor_invalidation_cause
        return bool(
            not cursor_recovery_used
            and
            receipt.failure_kind is InputFailureKind.CURSOR_STATE_INVALIDATED
            and execution.cleanup_confirmed
            and receipt.pointer_geometry is not None
            and (
                (cause is CursorInvalidationCause.CURSOR_REACQUIRED
                 and _completed_cursor_reacquisition(receipt))
                or (cause is not None and cause.recovery_eligible)
            )
        )
    return bool(
        consecutive_replans < MAX_CONSECUTIVE_UNSENT_REPLANS
        and
        execution.unsent_disposition
        in {
            UnsentActionDisposition.TARGET_EVIDENCE_INVALIDATED,
            UnsentActionDisposition.CAMERA_FRAMING_SATISFIED,
        }
        and receipt.failure_kind is InputFailureKind.NONE
        and (execution.cleanup_confirmed or receipt.safely_unsent)
    )


def _receipt_cleanup_confirmed(receipt: InputReceipt) -> bool:
    return bool(
        receipt.stop_all_acknowledged
        and receipt.disarm_acknowledged
        and receipt.firmware_status_acknowledged
        and receipt.firmware_status is not None
        and receipt.firmware_status.safe
        and receipt.unresolved_command_count == 0
        and receipt.failed_command_count == 0
        and receipt.ack_missing_count == 0
        and receipt.ledger_complete
        and receipt.ledger_closed
        and receipt.backend_closed
        and all(command.successful for command in receipt.commands)
    )


def _completed_cursor_reacquisition(receipt: InputReceipt) -> bool:
    evidence = receipt.cursor_reacquisition
    return bool(
        receipt.status == "BLOCKED"
        and receipt.failure_kind is InputFailureKind.CURSOR_STATE_INVALIDATED
        and receipt.cursor_invalidation_cause
        is CursorInvalidationCause.CURSOR_REACQUIRED
        and _receipt_cleanup_confirmed(receipt)
        and evidence is not None
        and evidence.completed
        and evidence.geometry_unchanged
        and evidence.no_activation_sent
        and receipt.pointer_geometry is not None
        and evidence.before_geometry == receipt.pointer_geometry
        and evidence.after_geometry == receipt.pointer_geometry
        and all(
            command.command in _PREACTIVATION_COMMANDS
            for command in receipt.commands
        )
    )


def _recovery_matches_invalidation(
    invalidation: InputReceipt,
    recovery: InputReceipt,
) -> bool:
    return bool(
        _completed_cursor_reacquisition(recovery)
        and invalidation.pointer_geometry is not None
        and recovery.pointer_geometry == invalidation.pointer_geometry
    )


def _verification_after_input(
    specification: VerificationSpec,
    post_move_tick: int | None,
) -> VerificationSpec:
    """Start the tick budget after the final pre-activation observation.

    Pointer movement and fresh hover revalidation can span several game ticks.
    Those ticks happen before the click or key press and therefore cannot count
    against the bounded post-action verification window.
    """

    if post_move_tick is None or post_move_tick <= specification.before_tick:
        return specification
    tick_budget = specification.deadline_tick - specification.before_tick
    return replace(
        specification,
        before_tick=post_move_tick,
        deadline_tick=post_move_tick + tick_budget,
    )


def _attach_camera_verification(
    execution: ExecutionResult,
    specification: VerificationSpec,
    result: VerificationResult,
) -> ExecutionResult:
    """Retain typed post-activation camera proof on the immutable receipt."""

    if specification.kind not in {
        VerificationKind.CAMERA_POSE_CHANGED,
        VerificationKind.CAMERA_ZOOM_CHANGED,
    } or execution.receipt is None:
        return execution

    outcome = result.outcome
    pose = outcome.camera_pose_result if outcome is not None else None
    zoom = outcome.camera_zoom_result if outcome is not None else None
    evidence = CameraInputVerificationEvidence(
        kind=specification.kind.value,
        status=result.status.value,
        reason=result.reason,
        observed_tick=(outcome.observed_tick if outcome is not None else None),
        before_yaw=(
            pose.before_yaw
            if pose is not None
            else (
                zoom.before_yaw
                if zoom is not None
                else specification.before_camera_yaw
            )
        ),
        after_yaw=(
            pose.after_yaw
            if pose is not None
            else (zoom.after_yaw if zoom is not None else None)
        ),
        before_pitch=(
            pose.before_pitch
            if pose is not None
            else (
                zoom.before_pitch
                if zoom is not None
                else specification.before_camera_pitch
            )
        ),
        after_pitch=(
            pose.after_pitch
            if pose is not None
            else (zoom.after_pitch if zoom is not None else None)
        ),
        before_zoom=(
            zoom.before_zoom
            if zoom is not None
            else specification.before_camera_zoom
        ),
        after_zoom=(zoom.after_zoom if zoom is not None else None),
        before_geometry_frame_id=(
            pose.before_geometry_frame_id
            if pose is not None
            else (
                zoom.before_geometry_frame_id
                if zoom is not None
                else specification.before_geometry_frame_id
            )
        ),
        after_geometry_frame_id=(
            pose.after_geometry_frame_id
            if pose is not None
            else (
                zoom.after_geometry_frame_id if zoom is not None else None
            )
        ),
        ui_state_unchanged=(
            zoom.before_ui_state == zoom.after_ui_state
            if zoom is not None
            else None
        ),
    )
    return replace(
        execution,
        receipt=replace(
            execution.receipt,
            camera_verification=evidence,
        ),
    )


class RuntimeControlState(str, Enum):
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    SAFE_STOP_REQUESTED = "safe_stop_requested"


@dataclass(frozen=True, slots=True)
class RuntimeControlSnapshot:
    state: RuntimeControlState


@dataclass(frozen=True, slots=True)
class RuntimeBoundary:
    safe_stop_requested: bool
    pause_observed: bool
    timed_out: bool


class RuntimeControl:
    """Cooperative lifecycle requests observed only at safe runtime boundaries."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._pause_requested = False
        self._paused_at_boundary = False
        self._safe_stop_requested = False

    def request_pause(self) -> RuntimeControlSnapshot:
        with self._condition:
            if not self._safe_stop_requested:
                self._pause_requested = True
            self._condition.notify_all()
            return self._snapshot_unlocked()

    def resume(self) -> RuntimeControlSnapshot:
        with self._condition:
            if not self._safe_stop_requested:
                self._pause_requested = False
            self._condition.notify_all()
            return self._snapshot_unlocked()

    def request_safe_stop(self) -> RuntimeControlSnapshot:
        with self._condition:
            self._safe_stop_requested = True
            self._pause_requested = False
            self._condition.notify_all()
            return self._snapshot_unlocked()

    def snapshot(self) -> RuntimeControlSnapshot:
        with self._condition:
            return self._snapshot_unlocked()

    def wait_for_state(
        self, state: RuntimeControlState, timeout: float | None = None
    ) -> bool:
        if not isinstance(state, RuntimeControlState):
            raise TypeError("state must be RuntimeControlState")
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout < 0
        ):
            raise ValueError("timeout must be non-negative or None")
        with self._condition:
            return self._condition.wait_for(
                lambda: self._snapshot_unlocked().state is state,
                timeout=None if timeout is None else float(timeout),
            )

    def wait_at_boundary(
        self, *, timeout_seconds: float | None = None
    ) -> RuntimeBoundary:
        """Acknowledge pause/stop only between indivisible engine units."""

        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds < 0
        ):
            raise ValueError("timeout_seconds must be non-negative or None")
        with self._condition:
            pause_observed = False
            timed_out = False
            deadline = (
                None
                if timeout_seconds is None
                else time.monotonic() + float(timeout_seconds)
            )
            if self._pause_requested and not self._safe_stop_requested:
                pause_observed = True
                self._paused_at_boundary = True
                self._condition.notify_all()
                while self._pause_requested and not self._safe_stop_requested:
                    remaining = (
                        None
                        if deadline is None
                        else max(0.0, deadline - time.monotonic())
                    )
                    notified = self._condition.wait(
                        timeout=remaining
                    )
                    if not notified and self._pause_requested:
                        timed_out = True
                        break
                self._paused_at_boundary = False
                self._condition.notify_all()
            return RuntimeBoundary(
                self._safe_stop_requested,
                pause_observed,
                timed_out,
            )

    def _snapshot_unlocked(self) -> RuntimeControlSnapshot:
        if self._safe_stop_requested:
            state = RuntimeControlState.SAFE_STOP_REQUESTED
        elif self._paused_at_boundary:
            state = RuntimeControlState.PAUSED
        elif self._pause_requested:
            state = RuntimeControlState.PAUSE_REQUESTED
        else:
            state = RuntimeControlState.RUNNING
        return RuntimeControlSnapshot(state)


@dataclass(frozen=True, slots=True)
class RuntimeStatistics:
    active: bool
    status: str
    reason: str | None
    observations: int
    actions: int
    last_tick: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "status": self.status,
            "reason": self.reason,
            "observations": self.observations,
            "actions": self.actions,
            "lastTick": self.last_tick,
        }


class _ActionInterface(Protocol):
    def execute(self, action: Action, observation: Observation) -> ExecutionResult: ...

    def recover_cursor(
        self,
        observation: Observation,
        invalidated_receipt: InputReceipt,
    ) -> InputReceipt: ...


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    status: str
    reason: str
    task_snapshot: TaskSnapshot
    observations: int
    actions: int
    last_tick: int | None
    decision: Decision | None = None
    execution: ExecutionResult | None = None
    engine_frame: EngineFrame | None = None

    @property
    def successful(self) -> bool:
        return self.status in {"COMPLETE", "DRY_RUN", "SAFE_STOPPED"}

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status,
            "reason": self.reason,
            "state": self.task_snapshot.state,
            "taskId": self.task_snapshot.task_id,
            "taskStatus": self.task_snapshot.status.value,
            "blocker": self.task_snapshot.blocker,
            "observations": self.observations,
            "actions": self.actions,
            "lastTick": self.last_tick,
        }
        if self.decision is not None:
            action = self.decision.action
            payload["decision"] = {
                "state": self.decision.state,
                "reason": self.decision.reason,
                "action": {
                    "kind": action.kind.value,
                    "label": action.label,
                    "sourceTick": action.source_tick,
                    "option": action.option,
                    "targetKey": action.target_key,
                    "targetName": action.target_name,
                    "targetId": action.target_id,
                    "targetParam0": action.target_param0,
                    "targetParam1": action.target_param1,
                    "key": action.key,
                    "keyHoldMillis": action.key_hold_millis,
                    "screenPoint": (
                        None
                        if action.screen_point is None
                        else {"x": action.screen_point.x, "y": action.screen_point.y}
                    ),
                    "verification": (
                        None
                        if action.verification is None
                        else action.verification.kind.value
                    ),
                },
            }
        if self.execution is not None:
            payload["execution"] = {
                "status": self.execution.status,
                "reason": self.execution.reason,
                "unsentDisposition": self.execution.unsent_disposition.value,
                "activationAttempted": self.execution.activation_attempted,
                "preMoveTick": self.execution.pre_move_tick,
                "postMoveTick": self.execution.post_move_tick,
                "stopAllConfirmed": self.execution.stop_all_confirmed,
                "disarmConfirmed": self.execution.disarm_confirmed,
                "cleanupConfirmed": self.execution.cleanup_confirmed,
                "observability": self.execution.observability.to_dict(),
                "receipt": (
                    self.execution.receipt.to_dict()
                    if self.execution.receipt is not None
                    else None
                ),
            }
        payload["engineFrame"] = (
            self.engine_frame.to_dict() if self.engine_frame is not None else None
        )
        return payload


class TaskRuntime:
    """Bounded orchestrator for any task implementing the shared task contract."""

    def __init__(
        self,
        client: ObservationClient,
        task: Task,
        verifier: Verifier,
        action_interface: _ActionInterface | None = None,
        *,
        configuration: RuntimeConfig = DEFAULT_RUNTIME_CONFIG,
        frame_publisher: EngineFramePublisher | None = None,
        control: RuntimeControl | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        evidence_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(configuration, RuntimeConfig):
            raise TypeError("configuration must be a validated RuntimeConfig")
        self._client = client
        self._task = task
        self._verifier = verifier
        self._action_interface = action_interface
        self._configuration = configuration
        self._poll_seconds = configuration.poll_seconds
        self._max_observations = configuration.max_observations
        self._max_actions = configuration.max_actions
        self._max_runtime_seconds = configuration.max_runtime_seconds
        self._verification_timeout_seconds = (
            configuration.verification_timeout_seconds
        )
        self._sleep = sleep
        self._clock = clock
        if not callable(evidence_clock):
            raise TypeError("evidence_clock must be callable")
        self._evidence_clock = evidence_clock
        if frame_publisher is not None and not isinstance(
            frame_publisher, EngineFramePublisher
        ):
            raise TypeError("frame_publisher must be EngineFramePublisher or None")
        self._frame_publisher = frame_publisher or EngineFramePublisher()
        if control is not None and not isinstance(control, RuntimeControl):
            raise TypeError("control must be RuntimeControl or None")
        self._control = control
        self._frame_observation: Observation | None = None
        self._frame_decision: Decision | None = None
        self._frame_execution: ExecutionResult | None = None
        self._frame_verification: VerificationResult | None = None
        self._frame_pending: VerificationSpec | None = None
        self._frame_publish_error: str | None = None
        self._frame_stage = EngineStage.STARTING
        self._timing = TimingEvidence()
        self._wait_state: WaitState | None = None
        self._wait_timing_phase: TimingPhase | None = None
        self._wait_started: object = None
        self._observed_wait_states: tuple[WaitState, ...] = ()
        self._statistics_lock = threading.Lock()
        self._statistics = RuntimeStatistics(False, "IDLE", None, 0, 0, None)

    @property
    def frame_publisher(self) -> EngineFramePublisher:
        return self._frame_publisher

    def statistics(self) -> RuntimeStatistics:
        with self._statistics_lock:
            return self._statistics

    def record_worker_failure(self, reason: str) -> RuntimeStatistics:
        """Close statistics if an unexpected exception escapes run()."""

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be non-empty text")
        current = self.statistics()
        self._update_statistics(
            False,
            "ERROR",
            reason,
            current.observations,
            current.actions,
            current.last_tick,
        )
        return self.statistics()

    def run(
        self,
        *,
        execute: bool = False,
        expected_process_id: int | None = None,
        expected_session_id: str | None = None,
    ) -> RuntimeResult:
        if (expected_process_id is None) != (expected_session_id is None):
            raise ValueError(
                "expected_process_id and expected_session_id must be supplied together"
            )
        if expected_process_id is not None and (
            not isinstance(expected_process_id, int)
            or isinstance(expected_process_id, bool)
            or expected_process_id <= 0
        ):
            raise ValueError("expected_process_id must be positive or None")
        if expected_session_id is not None and (
            not isinstance(expected_session_id, str)
            or not expected_session_id.strip()
        ):
            raise ValueError("expected_session_id must be non-empty or None")
        self._update_statistics(True, "RUNNING", None, 0, 0, None)
        self._reset_frame_state()
        self._publish_frame(EngineStage.STARTING)
        if execute and self._action_interface is None:
            return self._result(
                "ERROR", "live execution requires an action interface", 0, 0, None
            )

        observations = 0
        actions = 0
        last_tick: int | None = None
        last_decision: Decision | None = None
        consecutive_unsent_replans = 0
        cursor_recovery_used = False
        cursor_retry_pending = False
        run_identity: tuple[int, str] | None = (
            (expected_process_id, expected_session_id)
            if expected_process_id is not None and expected_session_id is not None
            else None
        )
        cursor_replan_after: tuple[
            int,
            str | None,
            int,
            ScreenBounds | None,
            ScreenBounds | None,
            ScreenBounds | None,
        ] | None = None
        cursor_recovery_binding: tuple[
            int,
            str | None,
            ScreenBounds | None,
            ScreenBounds | None,
            ScreenBounds | None,
        ] | None = None
        runtime_deadline = self._clock() + self._max_runtime_seconds
        focus_deadline: float | None = (
            self._clock() + LIVE_FOCUS_HANDOFF_SECONDS
        )
        focus_was_observed = False

        def control_boundary() -> RuntimeBoundary:
            if self._control is None:
                return RuntimeBoundary(False, False, False)
            remaining = max(0.0, runtime_deadline - self._clock())
            return self._control.wait_at_boundary(
                timeout_seconds=remaining
            )

        boundary = control_boundary()
        if boundary.timed_out:
            return self._result(
                "LIMIT",
                "runtime limit reached while paused",
                observations,
                actions,
                last_tick,
                last_decision,
            )
        if boundary.safe_stop_requested:
            return self._result(
                "SAFE_STOPPED",
                "safe stop requested before observation",
                observations,
                actions,
                last_tick,
                last_decision,
            )

        while observations < self._max_observations and self._clock() <= runtime_deadline:
            boundary = control_boundary()
            if boundary.timed_out:
                return self._result(
                    "LIMIT",
                    "runtime limit reached while paused",
                    observations,
                    actions,
                    last_tick,
                    last_decision,
                )
            if boundary.safe_stop_requested:
                return self._result(
                    "SAFE_STOPPED",
                    "safe stop acknowledged at an observation boundary",
                    observations,
                    actions,
                    last_tick,
                    last_decision,
                )
            try:
                observation = self._fetch()
            except Exception as error:  # endpoint and schema failures are terminal
                return self._result(
                    "ERROR",
                    f"observation failed: {type(error).__name__}: {error}",
                    observations,
                    actions,
                    last_tick,
                    last_decision,
                )
            observations += 1
            last_tick = observation.tick
            observed_identity = (
                (observation.client_process_id, observation.session_id)
                if observation.client_process_id is not None
                and observation.client_process_id > 0
                and observation.session_id
                else None
            )
            if run_identity is not None and observed_identity != run_identity:
                return self._result(
                    "BLOCKED",
                    "RuneLite PID/session identity changed during the active run",
                    observations,
                    actions,
                    last_tick,
                    last_decision,
                )
            if run_identity is None and observed_identity is not None and (
                observation.loaded_scene
                and observation.fresh
                and observation.cache_wall_clock_fresh
                and observation.source_coherent
            ):
                run_identity = observed_identity
            if cursor_recovery_binding is not None:
                (
                    recovery_pid,
                    recovery_session,
                    recovery_canvas,
                    recovery_viewport,
                    recovery_window,
                ) = cursor_recovery_binding
                if (
                    observation.client_process_id != recovery_pid
                    or observation.session_id != recovery_session
                ):
                    return self._result(
                        "BLOCKED",
                        "cursor recovery binding identity changed",
                        observations,
                        actions,
                        last_tick,
                        last_decision,
                    )
                if (
                    observation.canvas_bounds != recovery_canvas
                    or observation.viewport_bounds != recovery_viewport
                    or observation.client_window_bounds != recovery_window
                ):
                    return self._result(
                        "BLOCKED",
                        "RuneLite geometry changed after cursor reacquisition",
                        observations,
                        actions,
                        last_tick,
                        last_decision,
                    )
            self._update_statistics(
                True, "RUNNING", None, observations, actions, last_tick
            )
            self._frame_observation = observation
            self._publish_frame(EngineStage.OBSERVED)
            boundary = control_boundary()
            if boundary.timed_out:
                return self._result(
                    "LIMIT",
                    "runtime limit reached while paused",
                    observations,
                    actions,
                    last_tick,
                    last_decision,
                )
            if boundary.safe_stop_requested:
                return self._result(
                    "SAFE_STOPPED",
                    "safe stop acknowledged before task decision",
                    observations,
                    actions,
                    last_tick,
                    last_decision,
                )
            if boundary.pause_observed:
                # A pause can make a previously fresh observation stale. It is
                # diagnostic evidence only; refetch before task state changes.
                continue
            if cursor_replan_after is not None:
                (
                    expected_pid,
                    expected_session,
                    minimum_tick,
                    expected_canvas,
                    expected_viewport,
                    expected_window,
                ) = cursor_replan_after
                if (
                    observation.client_process_id != expected_pid
                    or observation.session_id != expected_session
                ):
                    return self._result(
                        "BLOCKED",
                        "cursor replan observation identity changed",
                        observations,
                        actions,
                        last_tick,
                        last_decision,
                    )
                if observation.tick <= minimum_tick:
                    self._set_wait_state(
                        WaitState.WAITING_FOR_NEXT_SCENE_UPDATE,
                        timing_phase=TimingPhase.SOURCE_COHERENCE_FRESHNESS_WAIT,
                    )
                    self._sleep(self._poll_seconds)
                    continue
                if (
                    observation.canvas_bounds != expected_canvas
                    or observation.viewport_bounds != expected_viewport
                    or observation.client_window_bounds != expected_window
                ):
                    return self._result(
                        "BLOCKED",
                        "RuneLite geometry changed after cursor reacquisition",
                        observations,
                        actions,
                        last_tick,
                        last_decision,
                    )
                if not observation.loaded_scene:
                    # Cursor ingress invalidates the action that was recognized
                    # before movement. A newer tick is not sufficient evidence
                    # to rebuild it: recognition may resume only from the same
                    # freshness/coherence contract used by live safety.
                    self._set_wait_state(
                        self._source_wait_state(observation),
                        timing_phase=TimingPhase.SOURCE_COHERENCE_FRESHNESS_WAIT,
                    )
                    self._sleep(self._poll_seconds)
                    continue
                cursor_replan_after = None
            focus_ready = bool(
                observation.client_focused
                and observation.client_process_id is not None
                and observation.client_process_id > 0
            )
            if execute and not focus_ready:
                self._set_wait_state(None)
                now = self._clock()
                if focus_deadline is None:
                    focus_deadline = now + LIVE_FOCUS_HANDOFF_SECONDS
                if now > focus_deadline:
                    return self._result(
                        "BLOCKED",
                        (
                            "telemetry-owning RuneLite client did not regain focus "
                            "within the bounded recovery window"
                            if focus_was_observed
                            else "telemetry-owning RuneLite client was not focused "
                            "during the live handoff"
                        ),
                        observations,
                        actions,
                        last_tick,
                        last_decision,
                    )
                self._sleep(self._poll_seconds)
                continue
            if execute:
                focus_was_observed = True
                focus_deadline = None
            self._set_wait_state(None)
            decision_started = self._evidence_now()
            try:
                decision = self._task.decide(observation)
            except Exception as error:
                return self._result(
                    "ERROR",
                    f"task decision failed: {type(error).__name__}: {error}",
                    observations,
                    actions,
                    last_tick,
                    last_decision,
                )
            finally:
                self._record_phase(
                    TimingPhase.TASK_DECISION,
                    decision_started,
                    self._evidence_now(),
                )
            last_decision = decision
            self._frame_decision = decision
            self._frame_pending = decision.action.verification

            task_snapshot, snapshot_error = self._read_task_snapshot()
            if snapshot_error is not None:
                return self._result(
                    "ERROR",
                    f"task snapshot failed: {snapshot_error}",
                    observations,
                    actions,
                    last_tick,
                    decision,
                    task_snapshot=self._snapshot_failure(snapshot_error),
                )
            assert task_snapshot is not None
            self._publish_frame(EngineStage.DECIDED, task_snapshot=task_snapshot)
            if task_snapshot.status is TaskStatus.COMPLETE:
                return self._result(
                    "COMPLETE",
                    decision.reason,
                    observations,
                    actions,
                    last_tick,
                    decision,
                    task_snapshot=task_snapshot,
                )
            if task_snapshot.status is TaskStatus.BLOCKED:
                return self._result(
                    "BLOCKED",
                    task_snapshot.blocker or decision.reason,
                    observations,
                    actions,
                    last_tick,
                    decision,
                    task_snapshot=task_snapshot,
                )
            if decision.action.kind is ActionKind.WAIT:
                self._set_wait_state(
                    self._source_wait_state(observation),
                    timing_phase=TimingPhase.SOURCE_COHERENCE_FRESHNESS_WAIT,
                )
                self._sleep(self._poll_seconds)
                continue

            # Executable actions without verification are invalid task output.
            # Reject before either dry-run success or any interface invocation.
            if decision.action.verification is None:
                failure_reason = "action omitted verification"
                transition_error = self._apply_failure(failure_reason)
                reason = "executable action omitted verification"
                if transition_error is not None:
                    reason += f"; task failure transition failed: {transition_error}"
                return self._result(
                    "ERROR",
                    reason,
                    observations,
                    actions,
                    last_tick,
                    decision,
                )
            if not execute:
                return self._result(
                    "DRY_RUN",
                    "first executable action was proposed but not sent",
                    observations,
                    actions,
                    last_tick,
                    decision,
                )
            if actions >= self._max_actions:
                return self._result(
                    "LIMIT",
                    "action limit reached",
                    observations,
                    actions,
                    last_tick,
                    decision,
                )

            assert self._action_interface is not None
            self._set_wait_state(WaitState.INPUT_TRANSACTION_BUSY)
            try:
                execution = self._action_interface.execute(decision.action, observation)
            except Exception as error:
                failure_reason = (
                    f"action interface failed: {type(error).__name__}: {error}"
                )
                transition_error = self._apply_failure(failure_reason)
                reason = "action interface raised before a safe result"
                if transition_error is not None:
                    reason += f"; task failure transition failed: {transition_error}"
                return self._result(
                    "BLOCKED",
                    reason,
                    observations,
                    actions,
                    last_tick,
                    decision,
                )
            finally:
                self._set_wait_state(None, publish=False)
            self._merge_timing(execution.observability.timing)
            actions += 1
            self._update_statistics(
                True, "RUNNING", None, observations, actions, last_tick
            )
            self._frame_execution = execution
            verification = decision.action.verification
            if execution.sent:
                consecutive_unsent_replans = 0
                if cursor_retry_pending:
                    cursor_retry_pending = False
                verification = _verification_after_input(
                    verification,
                    execution.post_move_tick,
                )
                self._frame_pending = verification
            elif execution.activation_attempted:
                self._publish_frame(
                    EngineStage.EXECUTED,
                    task_snapshot=task_snapshot,
                )
                failure_reason = (
                    "post-activation execution proof failed: "
                    f"{execution.status.lower()}: {execution.reason}"
                )
                transition_error = self._apply_failure(failure_reason)
                reason = (
                    "action activation was attempted but execution proof "
                    f"failed: {execution.reason}"
                )
                if transition_error is not None:
                    reason += (
                        "; task failure transition failed: "
                        f"{transition_error}"
                    )
                return self._result(
                    "BLOCKED",
                    reason,
                    observations,
                    actions,
                    last_tick,
                    decision,
                    execution,
                )
            elif _may_replan_unsent_action(
                execution,
                consecutive_unsent_replans,
                cursor_recovery_used=cursor_recovery_used,
                cursor_retry_pending=cursor_retry_pending,
            ):
                try:
                    self._task.discard_pending_action(
                        execution.reason,
                        target_invalidated=(
                            execution.unsent_disposition
                            is UnsentActionDisposition.TARGET_EVIDENCE_INVALIDATED
                        ),
                    )
                except Exception as error:
                    failure_reason = (
                        "unsent action replan failed: "
                        f"{type(error).__name__}: {error}"
                    )
                    transition_error = self._apply_failure(failure_reason)
                    reason = "task could not discard an unsent action"
                    if transition_error is not None:
                        reason += (
                            "; task failure transition failed: "
                            f"{transition_error}"
                        )
                    return self._result(
                        "BLOCKED",
                        reason,
                        observations,
                        actions,
                        last_tick,
                        decision,
                        execution,
                    )
                cursor_invalidation = (
                    execution.unsent_disposition
                    is UnsentActionDisposition.CURSOR_STATE_INVALIDATED
                )
                if cursor_invalidation:
                    cursor_recovery_used = True
                else:
                    consecutive_unsent_replans += 1
                self._frame_pending = None
                # Preserve the invalidating transaction and its complete
                # cleanup receipt before any separate recovery is attempted.
                self._publish_frame(EngineStage.EXECUTED)
                if cursor_invalidation:
                    invalidation_receipt = execution.receipt
                    assert invalidation_receipt is not None
                    cause = invalidation_receipt.cursor_invalidation_cause
                    if cause is CursorInvalidationCause.CURSOR_REACQUIRED:
                        recovery_receipt = invalidation_receipt
                    else:
                        self._set_wait_state(WaitState.INPUT_TRANSACTION_BUSY)
                        try:
                            recovery_receipt = (
                                self._action_interface.recover_cursor(
                                    observation,
                                    invalidation_receipt,
                                )
                            )
                        except Exception as error:
                            return self._result(
                                "BLOCKED",
                                (
                                    "movement-only cursor recovery failed before "
                                    "a safe receipt: "
                                    f"{type(error).__name__}: {error}"
                                ),
                                observations,
                                actions,
                                last_tick,
                                decision,
                                execution,
                            )
                        finally:
                            self._set_wait_state(None, publish=False)
                        if not isinstance(recovery_receipt, InputReceipt):
                            return self._result(
                                "BLOCKED",
                                "movement-only cursor recovery returned no receipt",
                                observations,
                                actions,
                                last_tick,
                                decision,
                                execution,
                            )
                        recovery_execution = ExecutionResult(
                            action=decision.action,
                            pre_move_tick=observation.tick,
                            local_status="ERROR",
                            local_reason="cursor_recovery_receipt_unavailable",
                            receipt=recovery_receipt,
                            unsent_disposition=(
                                UnsentActionDisposition.CURSOR_STATE_INVALIDATED
                            ),
                            activation_attempted=False,
                            observability=recovery_receipt.observability,
                        )
                        self._merge_timing(
                            recovery_receipt.observability.timing
                        )
                        self._frame_execution = recovery_execution
                        self._publish_frame(EngineStage.EXECUTED)
                        if not _recovery_matches_invalidation(
                            invalidation_receipt,
                            recovery_receipt,
                        ):
                            return self._result(
                                "BLOCKED",
                                (
                                    "movement-only cursor recovery did not prove "
                                    "unchanged geometry and complete cleanup"
                                ),
                                observations,
                                actions,
                                last_tick,
                                decision,
                                recovery_execution,
                            )
                    assert observation.client_process_id is not None
                    cursor_replan_after = (
                        observation.client_process_id,
                        observation.session_id,
                        max(
                            observation.tick,
                            execution.post_move_tick or observation.tick,
                        ),
                        observation.canvas_bounds,
                        observation.viewport_bounds,
                        observation.client_window_bounds,
                    )
                    cursor_recovery_binding = (
                        observation.client_process_id,
                        observation.session_id,
                        observation.canvas_bounds,
                        observation.viewport_bounds,
                        observation.client_window_bounds,
                    )
                    cursor_retry_pending = True
                continue
            self._publish_frame(EngineStage.EXECUTED, task_snapshot=task_snapshot)
            if not execution.sent:
                failure_reason = (
                    f"execution {execution.status.lower()}: {execution.reason}"
                )
                transition_error = self._apply_failure(failure_reason)
                reason = f"action was not sent: {execution.reason}"
                if transition_error is not None:
                    reason += f"; task failure transition failed: {transition_error}"
                return self._result(
                    "BLOCKED",
                    reason,
                    observations,
                    actions,
                    last_tick,
                    decision,
                    execution,
                )

            deadline = self._clock() + self._verification_timeout_seconds
            verification_done = False
            self._set_wait_state(
                WaitState.WAITING_FOR_NEXT_SCENE_UPDATE,
                timing_phase=TimingPhase.POST_ACTION_FRESH_OBSERVATION_WAIT,
            )
            while (
                observations < self._max_observations
                and self._clock() <= deadline
                and self._clock() <= runtime_deadline
            ):
                self._sleep(self._poll_seconds)
                try:
                    candidate = self._fetch()
                except Exception as error:
                    failure_reason = (
                        "verification observation failed: "
                        f"{type(error).__name__}: {error}"
                    )
                    transition_error = self._apply_failure(failure_reason)
                    reason = "verification observation failed"
                    if transition_error is not None:
                        reason += (
                            f"; task failure transition failed: {transition_error}"
                        )
                    return self._result(
                        "BLOCKED",
                        reason,
                        observations,
                        actions,
                        last_tick,
                        decision,
                        execution,
                    )
                observations += 1
                last_tick = candidate.tick
                self._update_statistics(
                    True, "RUNNING", None, observations, actions, last_tick
                )
                self._frame_observation = candidate
                self._publish_frame(EngineStage.OBSERVED)
                if not (
                    candidate.fresh
                    and candidate.cache_wall_clock_fresh
                    and candidate.source_coherent
                ):
                    self._set_wait_state(
                        self._source_wait_state(candidate),
                        timing_phase=TimingPhase.POST_ACTION_FRESH_OBSERVATION_WAIT,
                    )
                verification_started = self._evidence_now()
                try:
                    result = self._verifier.evaluate(verification, candidate)
                except Exception as error:
                    failure_reason = f"verifier failed: {type(error).__name__}: {error}"
                    transition_error = self._apply_failure(failure_reason)
                    reason = "verifier raised before proving the action"
                    if transition_error is not None:
                        reason += (
                            f"; task failure transition failed: {transition_error}"
                        )
                    return self._result(
                        "BLOCKED",
                        reason,
                        observations,
                        actions,
                        last_tick,
                        decision,
                        execution,
                    )
                finally:
                    self._record_phase(
                        TimingPhase.SEMANTIC_OR_CAMERA_VERIFICATION,
                        verification_started,
                        self._evidence_now(),
                    )
                if not isinstance(result, VerificationResult):
                    failure_reason = "verifier returned an invalid result"
                    transition_error = self._apply_failure(failure_reason)
                    reason = failure_reason
                    if transition_error is not None:
                        reason += (
                            f"; task failure transition failed: {transition_error}"
                        )
                    return self._result(
                        "BLOCKED",
                        reason,
                        observations,
                        actions,
                        last_tick,
                        decision,
                        execution,
                    )
                execution = _attach_camera_verification(
                    execution,
                    verification,
                    result,
                )
                self._frame_execution = execution
                self._frame_verification = result
                self._publish_frame(EngineStage.VERIFYING)
                if result.status is VerificationStatus.PENDING:
                    self._set_wait_state(
                        self._source_wait_state(candidate),
                        timing_phase=TimingPhase.POST_ACTION_FRESH_OBSERVATION_WAIT,
                    )
                    continue
                self._set_wait_state(None)
                if result.status is VerificationStatus.PASS and not result.passed:
                    failure_reason = "verifier pass omitted a typed outcome"
                    transition_error = self._apply_failure(failure_reason)
                    reason = failure_reason
                    if transition_error is not None:
                        reason += (
                            f"; task failure transition failed: {transition_error}"
                        )
                    return self._result(
                        "BLOCKED",
                        reason,
                        observations,
                        actions,
                        last_tick,
                        decision,
                        execution,
                    )

                try:
                    self._task.apply_verification(result)
                except Exception as error:
                    return self._result(
                        "ERROR",
                        "task verification transition failed: "
                        f"{type(error).__name__}: {error}",
                        observations,
                        actions,
                        last_tick,
                        decision,
                        execution,
                    )
                self._frame_pending = None
                post_verification_snapshot, snapshot_error = (
                    self._read_task_snapshot()
                )
                if snapshot_error is not None:
                    return self._result(
                        "ERROR",
                        "task snapshot failed after verification: "
                        f"{snapshot_error}",
                        observations,
                        actions,
                        last_tick,
                        decision,
                        execution,
                        task_snapshot=self._snapshot_failure(snapshot_error),
                    )
                assert post_verification_snapshot is not None
                self._publish_frame(
                    EngineStage.VERIFIED,
                    task_snapshot=post_verification_snapshot,
                )
                verification_done = True
                if result.failed:
                    if post_verification_snapshot.status is TaskStatus.BLOCKED:
                        return self._result(
                            "BLOCKED",
                            post_verification_snapshot.blocker
                            or f"verification failed: {result.reason}",
                            observations,
                            actions,
                            last_tick,
                            decision,
                            execution,
                            task_snapshot=post_verification_snapshot,
                        )
                    recoverable_failure = (
                        result.failure_kind
                        is VerificationFailureKind.ITEM_QUANTITY_UNCHANGED_AT_DEADLINE
                        or (
                            result.failure_kind
                            is VerificationFailureKind.CONDITION_UNMET_AT_DEADLINE
                            and verification.kind
                            is VerificationKind.CAMERA_POSE_CHANGED
                            and verification.camera_key in {"up", "down"}
                        )
                    )
                    if (
                        recoverable_failure
                        and post_verification_snapshot.status
                        is TaskStatus.RUNNING
                    ):
                        break
                    return self._result(
                        "ERROR",
                        "task accepted a non-recoverable verification failure",
                        observations,
                        actions,
                        last_tick,
                        decision,
                        execution,
                        task_snapshot=post_verification_snapshot,
                    )
                break

            if not verification_done:
                failure_reason = "verification wall-clock or observation limit exceeded"
                transition_error = self._apply_failure(failure_reason)
                reason = "verification did not complete within bounded limits"
                if transition_error is not None:
                    reason += f"; task failure transition failed: {transition_error}"
                return self._result(
                    "BLOCKED",
                    reason,
                    observations,
                    actions,
                    last_tick,
                    decision,
                    execution,
                )
            boundary = control_boundary()
            if boundary.timed_out:
                return self._result(
                    "LIMIT",
                    "runtime limit reached while paused",
                    observations,
                    actions,
                    last_tick,
                    decision,
                    execution,
                )
            if boundary.safe_stop_requested:
                return self._result(
                    "SAFE_STOPPED",
                    "safe stop acknowledged after action verification",
                    observations,
                    actions,
                    last_tick,
                    decision,
                    execution,
                )

        reason = (
            "runtime limit reached"
            if self._clock() > runtime_deadline
            else "observation limit reached"
        )
        return self._result(
            "LIMIT", reason, observations, actions, last_tick, last_decision
        )

    def _update_statistics(
        self,
        active: bool,
        status: str,
        reason: str | None,
        observations: int,
        actions: int,
        last_tick: int | None,
    ) -> None:
        with self._statistics_lock:
            self._statistics = RuntimeStatistics(
                active,
                status,
                reason,
                observations,
                actions,
                last_tick,
            )

    def _reset_frame_state(self) -> None:
        self._frame_observation = None
        self._frame_decision = None
        self._frame_execution = None
        self._frame_verification = None
        self._frame_pending = None
        self._frame_publish_error = None
        self._frame_stage = EngineStage.STARTING
        self._timing = TimingEvidence()
        self._wait_state = None
        self._wait_timing_phase = None
        self._wait_started = None
        self._observed_wait_states = ()

    def _publish_frame(
        self,
        stage: EngineStage,
        *,
        task_snapshot: TaskSnapshot | None = None,
        blocker: str | None = None,
    ) -> EngineFrame | None:
        try:
            self._frame_stage = stage
            snapshot = task_snapshot
            if snapshot is None:
                snapshot, snapshot_error = self._read_task_snapshot()
                if snapshot_error is not None:
                    snapshot = self._snapshot_failure(snapshot_error)
            assert snapshot is not None
            execution = self._frame_execution
            receipt = execution.receipt if execution is not None else None
            checks = (
                tuple(getattr(execution, "safety_checks", ()))
                if execution is not None
                else ()
            )
            observation = (
                ObservationReference.from_observation(
                    self._frame_observation,
                    behavior_config=self._configuration.behavior,
                )
                if self._frame_observation is not None
                else None
            )
            return self._frame_publisher.publish(
                stage=stage,
                task=snapshot,
                observation=observation,
                decision=self._frame_decision,
                safety_checks=checks,
                pending_verification=self._frame_pending,
                last_verification=self._frame_verification,
                last_execution_status=(
                    execution.status if execution is not None else None
                ),
                last_execution_reason=(
                    execution.reason if execution is not None else None
                ),
                last_execution_activation_attempted=(
                    execution.activation_attempted
                    if execution is not None
                    else False
                ),
                last_execution_receipt=receipt,
                blocker=blocker if blocker is not None else snapshot.blocker,
                observability=self._observability_evidence(),
            )
        except Exception as error:  # diagnostics never gain control authority
            self._frame_publish_error = f"{type(error).__name__}: {error}"
            return None

    def _fetch(self) -> Observation:
        started = self._evidence_now()
        try:
            request = self._task.observation_request()
            return _fetch_observation_request(self._client, request)
        finally:
            self._record_phase(
                TimingPhase.OBSERVATION_REQUEST_FETCH,
                started,
                self._evidence_now(),
            )

    def record_wait_state(self, state: WaitState | None) -> None:
        """Accept owner-produced passive progress without gaining authority."""

        try:
            if state is not None and not isinstance(state, WaitState):
                raise TypeError("state must be WaitState or None")
            self._set_wait_state(state)
        except Exception:
            # A diagnostic callback cannot affect task, safety, or input flow.
            return

    def _set_wait_state(
        self,
        state: WaitState | None,
        *,
        timing_phase: TimingPhase | None = None,
        publish: bool = True,
    ) -> None:
        if state is not None and not isinstance(state, WaitState):
            raise TypeError("state must be WaitState or None")
        if timing_phase is not None and not isinstance(timing_phase, TimingPhase):
            raise TypeError("timing_phase must be TimingPhase or None")
        if state is self._wait_state and timing_phase is self._wait_timing_phase:
            return
        now = self._evidence_now()
        if self._wait_state is not None and self._wait_timing_phase is not None:
            self._record_phase(
                self._wait_timing_phase,
                self._wait_started,
                now,
            )
        self._wait_state = state
        self._wait_timing_phase = timing_phase if state is not None else None
        self._wait_started = now if state is not None else None
        if state is not None and state not in self._observed_wait_states:
            self._observed_wait_states += (state,)
        if publish:
            self._publish_frame(self._frame_stage)

    def _observability_evidence(self) -> ObservabilityEvidence:
        elapsed = (
            safe_elapsed_millis(self._wait_started, self._evidence_now())
            if self._wait_state is not None
            else 0
        )
        return ObservabilityEvidence(
            timing=self._timing,
            wait_state=self._wait_state,
            wait_elapsed_millis=elapsed,
            observed_wait_states=self._observed_wait_states,
        )

    def _evidence_now(self) -> object:
        try:
            return self._evidence_clock()
        except Exception:
            return float("nan")

    def _record_phase(
        self,
        phase: TimingPhase,
        started: object,
        finished: object,
    ) -> None:
        try:
            self._timing = self._timing.record(
                phase,
                safe_elapsed_millis(started, finished),
            )
        except Exception:
            return

    def _merge_timing(self, timing: TimingEvidence) -> None:
        try:
            self._timing = self._timing.merge(timing)
        except Exception:
            return

    @staticmethod
    def _source_wait_state(observation: Observation) -> WaitState:
        if not observation.source_coherent:
            return WaitState.WAITING_FOR_SOURCE_COHERENCE
        return WaitState.WAITING_FOR_NEXT_SCENE_UPDATE

    def _apply_failure(self, reason: str) -> str | None:
        result = VerificationResult(
            VerificationStatus.FAIL,
            reason,
            failure_kind=VerificationFailureKind.RUNTIME_FAILURE,
        )
        try:
            self._task.apply_verification(result)
        except Exception as error:
            return f"{type(error).__name__}: {error}"
        self._frame_verification = result
        self._frame_pending = None
        return None

    def _read_task_snapshot(self) -> tuple[TaskSnapshot | None, str | None]:
        try:
            snapshot = self._task.snapshot()
        except Exception as error:
            return None, f"{type(error).__name__}: {error}"
        if not isinstance(snapshot, TaskSnapshot):
            return None, "task returned an invalid TaskSnapshot"
        return snapshot, None

    @staticmethod
    def _snapshot_failure(reason: str) -> TaskSnapshot:
        return TaskSnapshot(
            "task_snapshot_unavailable",
            TaskStatus.BLOCKED,
            "snapshot_error",
            reason,
        )

    def _result(
        self,
        status: str,
        reason: str,
        observations: int,
        actions: int,
        last_tick: int | None,
        decision: Decision | None = None,
        execution: ExecutionResult | None = None,
        *,
        task_snapshot: TaskSnapshot | None = None,
    ) -> RuntimeResult:
        self._set_wait_state(None, publish=False)
        snapshot = task_snapshot
        if snapshot is None:
            snapshot, snapshot_error = self._read_task_snapshot()
            if snapshot_error is not None:
                snapshot = self._snapshot_failure(snapshot_error)
                status = "ERROR"
                reason = f"{reason}; task snapshot failed: {snapshot_error}"
        assert snapshot is not None
        if decision is not None:
            self._frame_decision = decision
        if execution is not None:
            self._frame_execution = execution
        blocker = snapshot.blocker
        if blocker is None and status in {"ERROR", "BLOCKED", "LIMIT"}:
            blocker = reason
        terminal_frame = self._publish_frame(
            EngineStage.TERMINAL,
            task_snapshot=snapshot,
            blocker=blocker,
        )
        if terminal_frame is None:
            terminal_frame = self._frame_publisher.latest()
        self._update_statistics(
            False,
            status,
            reason,
            observations,
            actions,
            last_tick,
        )
        return RuntimeResult(
            status=status,
            reason=reason,
            task_snapshot=snapshot,
            observations=observations,
            actions=actions,
            last_tick=last_tick,
            decision=decision,
            execution=self._frame_execution,
            engine_frame=terminal_frame,
        )


def build_runtime(
    client: ObservationClient,
    task: Task,
    *,
    configuration: RuntimeConfig,
    execute: bool,
    control: RuntimeControl | None = None,
) -> TaskRuntime:
    """Compose the existing dry/live engine without moving authority upward."""

    configuration.validated_for_mode(execute=execute)
    if execute:
        return build_live_runtime(
            client,
            task,
            configuration=configuration,
            control=control,
        )
    return TaskRuntime(
        client,
        task,
        Verifier(),
        configuration=configuration,
        control=control,
    )


def build_live_runtime(
    client: ObservationClient,
    task: Task,
    *,
    configuration: RuntimeConfig,
    control: RuntimeControl | None = None,
) -> TaskRuntime:
    from .safety import SafetyGate

    configuration.validated_for_mode(execute=True)
    assert configuration.arduino_port is not None
    safety = SafetyGate()
    runtime_ref: list[TaskRuntime] = []

    def record_wait_state(state: WaitState | None) -> None:
        if runtime_ref:
            runtime_ref[0].record_wait_state(state)

    coordinator = InputCoordinator.for_arduino_port(
        configuration.arduino_port,
        serial_owner="osrs-gameplay-runtime",
        wait_state_observer=record_wait_state,
    )
    def observe() -> Observation:
        request = task.observation_request()
        return _fetch_observation_request(client, request)
    action_interface = CoordinatedActionInterface(
        coordinator,
        safety,
        observe,
        wait_state_observer=record_wait_state,
    )
    runtime = TaskRuntime(
        client,
        task,
        Verifier(),
        action_interface,
        configuration=configuration,
        control=control,
    )
    runtime_ref.append(runtime)
    return runtime

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Protocol

from .action import CoordinatedActionInterface, ExecutionResult
from .configuration import DEFAULT_RUNTIME_CONFIG, RuntimeConfig
from .engine_frame import (
    EngineFrame,
    EngineFramePublisher,
    EngineStage,
    ObservationReference,
)
from .input_coordinator import InputCoordinator
from .model import Action, ActionKind, Observation, VerificationSpec
from .observation import ObservationClient
from .task_contract import Decision, Task, TaskSnapshot, TaskStatus
from .verification import VerificationResult, VerificationStatus, Verifier


LIVE_FOCUS_HANDOFF_SECONDS = 15.0


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
                "preMoveTick": self.execution.pre_move_tick,
                "postMoveTick": self.execution.post_move_tick,
                "stopAllConfirmed": self.execution.stop_all_confirmed,
                "disarmConfirmed": self.execution.disarm_confirmed,
                "cleanupConfirmed": self.execution.cleanup_confirmed,
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

    def run(self, *, execute: bool = False) -> RuntimeResult:
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
        runtime_deadline = self._clock() + self._max_runtime_seconds
        focus_deadline = self._clock() + LIVE_FOCUS_HANDOFF_SECONDS

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
            if execute and (
                not observation.client_focused
                or observation.client_process_id is None
                or observation.client_process_id <= 0
            ):
                if self._clock() > focus_deadline:
                    return self._result(
                        "BLOCKED",
                        "telemetry-owning RuneLite client was not focused during the live handoff",
                        observations,
                        actions,
                        last_tick,
                        last_decision,
                    )
                self._sleep(self._poll_seconds)
                continue
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
            actions += 1
            self._update_statistics(
                True, "RUNNING", None, observations, actions, last_tick
            )
            self._frame_execution = execution
            verification = decision.action.verification
            if execution.sent:
                verification = _verification_after_input(
                    verification,
                    execution.post_move_tick,
                )
                self._frame_pending = verification
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
                self._frame_verification = result
                self._publish_frame(EngineStage.VERIFYING)
                if result.status is VerificationStatus.PENDING:
                    continue
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
                self._publish_frame(EngineStage.VERIFIED)
                verification_done = True
                if result.failed:
                    return self._result(
                        "BLOCKED",
                        f"verification failed: {result.reason}",
                        observations,
                        actions,
                        last_tick,
                        decision,
                        execution,
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

    def _publish_frame(
        self,
        stage: EngineStage,
        *,
        task_snapshot: TaskSnapshot | None = None,
        blocker: str | None = None,
    ) -> EngineFrame | None:
        try:
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
                ObservationReference.from_observation(self._frame_observation)
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
                last_execution_receipt=receipt,
                blocker=blocker if blocker is not None else snapshot.blocker,
            )
        except Exception as error:  # diagnostics never gain control authority
            self._frame_publish_error = f"{type(error).__name__}: {error}"
            return None

    def _fetch(self) -> Observation:
        request = self._task.observation_request()
        return self._client.fetch(request.tile_projections)

    def _apply_failure(self, reason: str) -> str | None:
        result = VerificationResult(VerificationStatus.FAIL, reason)
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
    coordinator = InputCoordinator.for_arduino_port(
        configuration.arduino_port,
        serial_owner="osrs-gameplay-runtime",
    )
    observe = lambda: client.fetch(task.observation_request().tile_projections)
    action_interface = CoordinatedActionInterface(coordinator, safety, observe)
    return TaskRuntime(
        client,
        task,
        Verifier(),
        action_interface,
        configuration=configuration,
        control=control,
    )

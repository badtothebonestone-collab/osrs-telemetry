from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol

from .action import ArduinoActionInterface, ExecutionResult
from .model import Action, ActionKind, Observation
from .observation import ObservationClient
from .task_contract import Decision, Task, TaskSnapshot, TaskStatus
from .verification import VerificationResult, VerificationStatus, Verifier


DEFAULT_MAX_OBSERVATIONS = 4800
DEFAULT_MAX_ACTIONS = 80
DEFAULT_MAX_RUNTIME_SECONDS = 1200.0
DEFAULT_VERIFICATION_TIMEOUT_SECONDS = 75.0
LIVE_FOCUS_HANDOFF_SECONDS = 15.0


class _ActionInterface(Protocol):
    def execute(self, action: Action, observation: Observation) -> ExecutionResult: ...


@dataclass(frozen=True)
class RuntimeResult:
    status: str
    reason: str
    task_snapshot: TaskSnapshot
    observations: int
    actions: int
    last_tick: int | None
    decision: Decision | None = None
    execution: ExecutionResult | None = None

    @property
    def successful(self) -> bool:
        return self.status in {"COMPLETE", "DRY_RUN"}

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
            }
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
        poll_seconds: float = 0.25,
        max_observations: int = DEFAULT_MAX_OBSERVATIONS,
        max_actions: int = DEFAULT_MAX_ACTIONS,
        max_runtime_seconds: float = DEFAULT_MAX_RUNTIME_SECONDS,
        verification_timeout_seconds: float = DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if poll_seconds < 0:
            raise ValueError("poll_seconds must be non-negative")
        if max_observations <= 0 or max_actions <= 0:
            raise ValueError("runtime limits must be positive")
        if verification_timeout_seconds <= 0:
            raise ValueError("verification_timeout_seconds must be positive")
        if max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be positive")
        self._client = client
        self._task = task
        self._verifier = verifier
        self._action_interface = action_interface
        self._poll_seconds = poll_seconds
        self._max_observations = max_observations
        self._max_actions = max_actions
        self._max_runtime_seconds = max_runtime_seconds
        self._verification_timeout_seconds = verification_timeout_seconds
        self._sleep = sleep
        self._clock = clock

    def run(self, *, execute: bool = False) -> RuntimeResult:
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

        while observations < self._max_observations and self._clock() <= runtime_deadline:
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
                try:
                    result = self._verifier.evaluate(
                        decision.action.verification, candidate
                    )
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

        reason = (
            "runtime limit reached"
            if self._clock() > runtime_deadline
            else "observation limit reached"
        )
        return self._result(
            "LIMIT", reason, observations, actions, last_tick, last_decision
        )

    def _fetch(self) -> Observation:
        request = self._task.observation_request()
        return self._client.fetch(request.tile_projections)

    def _apply_failure(self, reason: str) -> str | None:
        result = VerificationResult(VerificationStatus.FAIL, reason)
        try:
            self._task.apply_verification(result)
        except Exception as error:
            return f"{type(error).__name__}: {error}"
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
        return RuntimeResult(
            status=status,
            reason=reason,
            task_snapshot=snapshot,
            observations=observations,
            actions=actions,
            last_tick=last_tick,
            decision=decision,
            execution=execution,
        )


def build_live_runtime(
    client: ObservationClient,
    task: Task,
    *,
    arduino_port: str,
    poll_seconds: float = 0.25,
    max_observations: int = DEFAULT_MAX_OBSERVATIONS,
    max_actions: int = DEFAULT_MAX_ACTIONS,
    max_runtime_seconds: float = DEFAULT_MAX_RUNTIME_SECONDS,
    verification_timeout_seconds: float = DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
) -> TaskRuntime:
    from .arduino import ArduinoHIDBackend
    from .safety import SafetyGate

    safety = SafetyGate()
    backend = ArduinoHIDBackend(port=arduino_port, fail_closed=True)
    observe = lambda: client.fetch(task.observation_request().tile_projections)
    action_interface = ArduinoActionInterface(backend, safety, observe)
    return TaskRuntime(
        client,
        task,
        Verifier(),
        action_interface,
        poll_seconds=poll_seconds,
        max_observations=max_observations,
        max_actions=max_actions,
        max_runtime_seconds=max_runtime_seconds,
        verification_timeout_seconds=verification_timeout_seconds,
    )

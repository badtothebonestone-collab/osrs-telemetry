from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from .configuration import DEFAULT_RUNTIME_CONFIG, RuntimeConfig
from .definition import LUMBRIDGE_WEST_TREES_V1
from .demonstration import InspectionResult, inspect_demonstration, record_live
from .engine_frame import EngineFrame
from .observation import ObservationClient
from .profile import (
    DEFAULT_PROFILE,
    BoundProfile,
    profile_contract,
    validate_profile_values,
)
from .runtime import (
    RuntimeControl,
    RuntimeControlState,
    RuntimeResult,
    RuntimeStatistics,
    TaskRuntime,
    build_runtime,
)
from .task import (
    WOODCUT_BANK_TASK_DISPLAY_NAME,
    WOODCUT_BANK_TASK_ID,
    WoodcutBankTask,
)


APPLICATION_SCHEMA = "engine_application.v1"
CATALOG_SCHEMA = "engine_catalog.v1"
SUPPORTED_TASK_ID = WOODCUT_BANK_TASK_ID


class ApplicationError(RuntimeError):
    pass


class LifecycleState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    SAFE_STOP_REQUESTED = "safe_stop_requested"
    DEMONSTRATING = "demonstrating"
    DEMONSTRATION_STOP_REQUESTED = "demonstration_stop_requested"
    COMPLETE = "complete"
    STOPPED = "stopped"
    BLOCKED = "blocked"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TaskDescriptor:
    task_id: str
    display_name: str
    definition_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "displayName": self.display_name,
            "definitionIds": list(self.definition_ids),
        }


@dataclass(frozen=True, slots=True)
class DefinitionDescriptor:
    definition_id: str
    display_name: str
    version: int
    resource_name: str
    resource_ids: tuple[int, ...]
    bank_name: str
    profile_selectable_resource: bool = False
    profile_selectable_bank: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "definitionId": self.definition_id,
            "displayName": self.display_name,
            "version": self.version,
            "resource": {
                "name": self.resource_name,
                "objectIds": list(self.resource_ids),
                "profileSelectable": self.profile_selectable_resource,
            },
            "bank": {
                "name": self.bank_name,
                "profileSelectable": self.profile_selectable_bank,
            },
        }


@dataclass(frozen=True, slots=True)
class DemonstrationReference:
    path: Path
    valid: bool
    status: str
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "valid": self.valid,
            "status": self.status,
            "errors": list(self.errors),
        }


TASK_DESCRIPTOR = TaskDescriptor(
    SUPPORTED_TASK_ID,
    WOODCUT_BANK_TASK_DISPLAY_NAME,
    (LUMBRIDGE_WEST_TREES_V1.definition_id,),
)
DEFINITION_DESCRIPTOR = DefinitionDescriptor(
    LUMBRIDGE_WEST_TREES_V1.definition_id,
    LUMBRIDGE_WEST_TREES_V1.display_name,
    LUMBRIDGE_WEST_TREES_V1.version,
    LUMBRIDGE_WEST_TREES_V1.resource.selector.name,
    tuple(sorted(LUMBRIDGE_WEST_TREES_V1.resource.selector.object_ids)),
    LUMBRIDGE_WEST_TREES_V1.bank.selector.name,
)


@dataclass(frozen=True, slots=True)
class ApplicationSnapshot:
    lifecycle: LifecycleState
    run_id: str | None
    capture_id: str | None
    active_run_id: str | None
    active_capture_id: str | None
    execute_requested: bool
    profile_id: str | None
    runtime_control: RuntimeControlState | None
    engine_frame: EngineFrame | None
    runtime_statistics: RuntimeStatistics | None
    blockers: tuple[str, ...]
    recent_demonstration: DemonstrationReference | None
    started_at: datetime | None
    finished_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": APPLICATION_SCHEMA,
            "lifecycle": self.lifecycle.value,
            "runId": self.run_id,
            "captureId": self.capture_id,
            "activeRunId": self.active_run_id,
            "activeCaptureId": self.active_capture_id,
            "executeRequested": self.execute_requested,
            "profileId": self.profile_id,
            "runtimeControl": (
                self.runtime_control.value
                if self.runtime_control is not None
                else None
            ),
            "engineFrame": (
                self.engine_frame.to_dict()
                if self.engine_frame is not None
                else None
            ),
            "runStatistics": (
                self.runtime_statistics.to_dict()
                if self.runtime_statistics is not None
                else None
            ),
            "blockers": list(self.blockers),
            "recentDemonstration": (
                None
                if self.recent_demonstration is None
                else self.recent_demonstration.to_dict()
            ),
            "startedAtUtc": (
                self.started_at.isoformat() if self.started_at is not None else None
            ),
            "finishedAtUtc": (
                self.finished_at.isoformat()
                if self.finished_at is not None
                else None
            ),
        }


class _RuntimeFactory(Protocol):
    def __call__(
        self,
        client: ObservationClient,
        binding: BoundProfile,
        configuration: RuntimeConfig,
        execute: bool,
        control: RuntimeControl,
    ) -> TaskRuntime: ...


class _DemonstrationRunner(Protocol):
    def __call__(self, name: str, client: ObservationClient, **values: Any) -> Path: ...


def _default_runtime_factory(
    client: ObservationClient,
    binding: BoundProfile,
    configuration: RuntimeConfig,
    execute: bool,
    control: RuntimeControl,
) -> TaskRuntime:
    task = WoodcutBankTask(binding)
    return build_runtime(
        client,
        task,
        configuration=configuration,
        execute=execute,
        control=control,
    )


class EngineApplication:
    """Thin composition facade; all task, safety, input and verification stay below it."""

    def __init__(
        self,
        *,
        configuration: RuntimeConfig = DEFAULT_RUNTIME_CONFIG,
        client: ObservationClient | None = None,
        runtime_factory: _RuntimeFactory = _default_runtime_factory,
        demonstration_runner: _DemonstrationRunner = record_live,
        demonstration_inspector: Callable[[Path | str], InspectionResult] = inspect_demonstration,
    ) -> None:
        if not isinstance(configuration, RuntimeConfig):
            raise TypeError("configuration must be RuntimeConfig")
        if not callable(runtime_factory):
            raise TypeError("runtime_factory must be callable")
        if not callable(demonstration_runner):
            raise TypeError("demonstration_runner must be callable")
        if not callable(demonstration_inspector):
            raise TypeError("demonstration_inspector must be callable")
        self._configuration = configuration
        self._client = client or ObservationClient(
            configuration.endpoint,
            auth_token=configuration.auth_token,
            timeout_seconds=configuration.request_timeout_seconds,
        )
        self._runtime_factory = runtime_factory
        self._demonstration_runner = demonstration_runner
        self._demonstration_inspector = demonstration_inspector
        self._lock = threading.RLock()
        self._next_run_id = 1
        self._next_capture_id = 1
        self._run_id: str | None = None
        self._run_thread: threading.Thread | None = None
        self._runtime: TaskRuntime | None = None
        self._control: RuntimeControl | None = None
        self._run_result: RuntimeResult | None = None
        self._run_error: str | None = None
        self._binding: BoundProfile | None = None
        self._execute_requested = False
        self._started_at: datetime | None = None
        self._finished_at: datetime | None = None
        self._capture_id: str | None = None
        self._demo_thread: threading.Thread | None = None
        self._demo_stop: threading.Event | None = None
        self._demo_path: Path | None = None
        self._demo_inspection: InspectionResult | None = None
        self._demo_error: str | None = None
        self._last_operation: str | None = None

    @staticmethod
    def list_tasks() -> tuple[TaskDescriptor, ...]:
        return (TASK_DESCRIPTOR,)

    @staticmethod
    def list_definitions(task_id: str = SUPPORTED_TASK_ID) -> tuple[DefinitionDescriptor, ...]:
        if task_id != SUPPORTED_TASK_ID:
            raise ApplicationError(f"unsupported task_id: {task_id!r}")
        return (DEFINITION_DESCRIPTOR,)

    @staticmethod
    def profile_contract(
        task_id: str = SUPPORTED_TASK_ID,
        definition_id: str = LUMBRIDGE_WEST_TREES_V1.definition_id,
    ) -> dict[str, Any]:
        if task_id != SUPPORTED_TASK_ID:
            raise ApplicationError(f"unsupported task_id: {task_id!r}")
        if definition_id != LUMBRIDGE_WEST_TREES_V1.definition_id:
            raise ApplicationError(f"unsupported definition_id: {definition_id!r}")
        return profile_contract()

    @staticmethod
    def validate_profile(values: Mapping[str, object]) -> BoundProfile:
        return validate_profile_values(values)

    @staticmethod
    def catalog() -> dict[str, Any]:
        return {
            "schema": CATALOG_SCHEMA,
            "tasks": [value.to_dict() for value in EngineApplication.list_tasks()],
            "definitions": [
                value.to_dict()
                for value in EngineApplication.list_definitions()
            ],
            "profile": EngineApplication.profile_contract(),
        }

    def start(
        self,
        *,
        task_id: str = SUPPORTED_TASK_ID,
        profile_values: Mapping[str, object] | None = None,
        execute: bool = False,
    ) -> ApplicationSnapshot:
        if task_id != SUPPORTED_TASK_ID:
            raise ApplicationError(f"unsupported task_id: {task_id!r}")
        if not isinstance(execute, bool):
            raise TypeError("execute must be bool")
        values = (
            {
                "profileId": DEFAULT_PROFILE.profile_id,
                "definitionId": DEFAULT_PROFILE.definition_id,
                "cycleGoal": DEFAULT_PROFILE.cycle_goal,
            }
            if profile_values is None
            else profile_values
        )
        binding = validate_profile_values(values)
        self._configuration.validated_for_mode(execute=execute)
        with self._lock:
            self._require_idle_unlocked()
            control = RuntimeControl()
            runtime = self._runtime_factory(
                self._client,
                binding,
                self._configuration,
                bool(execute),
                control,
            )
            if not isinstance(runtime, TaskRuntime):
                raise TypeError("runtime_factory must return TaskRuntime")
            run_id = f"run-{self._next_run_id:06d}"
            self._next_run_id += 1
            self._run_id = run_id
            self._runtime = runtime
            self._control = control
            self._run_result = None
            self._run_error = None
            self._binding = binding
            self._execute_requested = bool(execute)
            self._last_operation = "run"
            self._started_at = datetime.now(timezone.utc)
            self._finished_at = None
            worker = threading.Thread(
                target=self._run_worker,
                args=(run_id, runtime, bool(execute)),
                name=f"osrs-engine-{run_id}",
                daemon=False,
            )
            self._run_thread = worker
            worker.start()
            return self._snapshot_unlocked()

    def request_pause(self, run_id: str) -> ApplicationSnapshot:
        with self._lock:
            control = self._require_active_run_unlocked(run_id)
            control.request_pause()
            return self._snapshot_unlocked()

    def resume(self, run_id: str) -> ApplicationSnapshot:
        with self._lock:
            control = self._require_active_run_unlocked(run_id)
            control.resume()
            return self._snapshot_unlocked()

    def request_safe_stop(self, run_id: str) -> ApplicationSnapshot:
        with self._lock:
            if run_id != self._run_id or self._last_operation != "run":
                raise ApplicationError("run_id does not identify the current run")
            if self._run_thread is not None and self._run_thread.is_alive():
                assert self._control is not None
                self._control.request_safe_stop()
            elif self._run_result is None or self._run_result.status != "SAFE_STOPPED":
                raise ApplicationError("the current run is not active")
            return self._snapshot_unlocked()

    def wait(self, run_id: str, timeout: float | None = None) -> ApplicationSnapshot:
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout < 0
        ):
            raise ValueError("timeout must be non-negative or None")
        with self._lock:
            if (
                run_id != self._run_id
                or self._last_operation != "run"
                or self._run_thread is None
            ):
                raise ApplicationError("run_id does not identify the current run")
            worker = self._run_thread
        worker.join(None if timeout is None else float(timeout))
        with self._lock:
            if run_id != self._run_id or self._last_operation != "run":
                raise ApplicationError("run_id is no longer the current operation")
            return self._snapshot_unlocked()

    def read_engine_frame(self) -> EngineFrame | None:
        with self._lock:
            runtime = self._runtime
        return None if runtime is None else runtime.frame_publisher.latest()

    def read_statistics(self) -> RuntimeStatistics | None:
        with self._lock:
            runtime = self._runtime
        return None if runtime is None else runtime.statistics()

    def read_blockers(self) -> tuple[str, ...]:
        with self._lock:
            frame = (
                self._runtime.frame_publisher.latest()
                if self._runtime is not None
                else None
            )
            return self._blockers_unlocked(frame)

    def begin_demonstration(
        self,
        name: str,
        *,
        output_root: Path = Path("demo_runs"),
        duration_seconds: float = 60.0,
        poll_seconds: float = 0.05,
        annotations: tuple[str, ...] = (),
        screenshots_enabled: bool = True,
    ) -> ApplicationSnapshot:
        with self._lock:
            self._require_idle_unlocked()
            capture_id = f"demo-{self._next_capture_id:06d}"
            self._next_capture_id += 1
            stop = threading.Event()
            self._capture_id = capture_id
            self._demo_stop = stop
            self._demo_path = None
            self._demo_inspection = None
            self._demo_error = None
            self._execute_requested = False
            self._binding = None
            self._last_operation = "demo"
            self._started_at = datetime.now(timezone.utc)
            self._finished_at = None
            worker = threading.Thread(
                target=self._demo_worker,
                args=(
                    capture_id,
                    name,
                    Path(output_root),
                    duration_seconds,
                    poll_seconds,
                    tuple(annotations),
                    bool(screenshots_enabled),
                    stop,
                ),
                name=f"osrs-demo-{capture_id}",
                daemon=False,
            )
            self._demo_thread = worker
            worker.start()
            return self._snapshot_unlocked()

    def end_demonstration(
        self, capture_id: str, *, timeout: float | None = 10.0
    ) -> ApplicationSnapshot:
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout < 0
        ):
            raise ValueError("timeout must be non-negative or None")
        with self._lock:
            if (
                capture_id != self._capture_id
                or self._last_operation != "demo"
                or self._demo_thread is None
            ):
                raise ApplicationError(
                    "capture_id does not identify the current demonstration"
                )
            worker = self._demo_thread
            if worker.is_alive():
                assert self._demo_stop is not None
                self._demo_stop.set()
        worker.join(None if timeout is None else float(timeout))
        with self._lock:
            if capture_id != self._capture_id or self._last_operation != "demo":
                raise ApplicationError(
                    "capture_id is no longer the current operation"
                )
            return self._snapshot_unlocked()

    def snapshot(self) -> ApplicationSnapshot:
        with self._lock:
            return self._snapshot_unlocked()

    def _run_worker(
        self, run_id: str, runtime: TaskRuntime, execute: bool
    ) -> None:
        result: RuntimeResult | None = None
        error: str | None = None
        try:
            result = runtime.run(execute=execute)
        except BaseException as raised:
            error = f"{type(raised).__name__}: {raised}"
            runtime.record_worker_failure(error)
        with self._lock:
            if self._run_id == run_id:
                self._run_result = result
                self._run_error = error
                self._finished_at = datetime.now(timezone.utc)

    def _demo_worker(
        self,
        capture_id: str,
        name: str,
        output_root: Path,
        duration_seconds: float,
        poll_seconds: float,
        annotations: tuple[str, ...],
        screenshots_enabled: bool,
        stop: threading.Event,
    ) -> None:
        path: Path | None = None
        inspection: InspectionResult | None = None
        error: str | None = None
        try:
            path = self._demonstration_runner(
                name,
                self._client,
                output_root=output_root,
                duration_seconds=duration_seconds,
                poll_seconds=poll_seconds,
                annotations=annotations,
                screenshots_enabled=screenshots_enabled,
                stop_requested=stop.is_set,
            )
            inspection = self._demonstration_inspector(path)
            if not inspection.valid:
                error = "demonstration inspection failed"
        except BaseException as raised:
            error = f"{type(raised).__name__}: {raised}"
        with self._lock:
            if self._capture_id == capture_id:
                self._demo_path = path
                self._demo_inspection = inspection
                self._demo_error = error
                self._finished_at = datetime.now(timezone.utc)

    def _require_idle_unlocked(self) -> None:
        if self._run_thread is not None and self._run_thread.is_alive():
            raise ApplicationError("an engine run is already active")
        if self._demo_thread is not None and self._demo_thread.is_alive():
            raise ApplicationError("a demonstration capture is already active")

    def _require_active_run_unlocked(self, run_id: str) -> RuntimeControl:
        if run_id != self._run_id:
            raise ApplicationError("run_id does not identify the current run")
        if self._run_thread is None or not self._run_thread.is_alive():
            raise ApplicationError("the current run is not active")
        assert self._control is not None
        return self._control

    def _snapshot_unlocked(self) -> ApplicationSnapshot:
        frame = (
            self._runtime.frame_publisher.latest()
            if self._runtime is not None
            else None
        )
        statistics = self._runtime.statistics() if self._runtime is not None else None
        control_state = self._control.snapshot().state if self._control else None
        return ApplicationSnapshot(
            lifecycle=self._lifecycle_unlocked(control_state, frame),
            run_id=self._run_id,
            capture_id=self._capture_id,
            active_run_id=(
                self._run_id
                if self._run_thread is not None and self._run_thread.is_alive()
                else None
            ),
            active_capture_id=(
                self._capture_id
                if self._demo_thread is not None and self._demo_thread.is_alive()
                else None
            ),
            execute_requested=self._execute_requested,
            profile_id=(
                self._binding.profile.profile_id if self._binding is not None else None
            ),
            runtime_control=control_state,
            engine_frame=frame,
            runtime_statistics=statistics,
            blockers=self._blockers_unlocked(frame),
            recent_demonstration=(
                DemonstrationReference(
                    self._demo_path,
                    self._demo_inspection.valid,
                    self._demo_inspection.status,
                    self._demo_inspection.errors,
                )
                if self._demo_path is not None
                and self._demo_inspection is not None
                else None
            ),
            started_at=self._started_at,
            finished_at=self._finished_at,
        )

    def _lifecycle_unlocked(
        self,
        control_state: RuntimeControlState | None,
        frame: EngineFrame | None,
    ) -> LifecycleState:
        if self._demo_thread is not None and self._demo_thread.is_alive():
            return (
                LifecycleState.DEMONSTRATION_STOP_REQUESTED
                if self._demo_stop is not None and self._demo_stop.is_set()
                else LifecycleState.DEMONSTRATING
            )
        if self._run_thread is not None and self._run_thread.is_alive():
            if control_state is RuntimeControlState.PAUSED:
                return LifecycleState.PAUSED
            if control_state is RuntimeControlState.PAUSE_REQUESTED:
                return LifecycleState.PAUSE_REQUESTED
            if control_state is RuntimeControlState.SAFE_STOP_REQUESTED:
                return LifecycleState.SAFE_STOP_REQUESTED
            if self._runtime is not None and frame is None:
                return LifecycleState.STARTING
            return LifecycleState.RUNNING
        if self._last_operation == "demo":
            if self._demo_error:
                return LifecycleState.ERROR
            if self._demo_inspection is not None:
                return LifecycleState.COMPLETE
            return LifecycleState.IDLE
        if self._run_error:
            return LifecycleState.ERROR
        if self._run_result is None:
            return LifecycleState.IDLE
        if self._run_result.status in {"COMPLETE", "DRY_RUN"}:
            return LifecycleState.COMPLETE
        if self._run_result.status == "SAFE_STOPPED":
            return LifecycleState.STOPPED
        if self._run_result.status in {"BLOCKED", "LIMIT"}:
            return LifecycleState.BLOCKED
        return LifecycleState.ERROR

    def _blockers_unlocked(self, frame: EngineFrame | None) -> tuple[str, ...]:
        values: list[str] = []
        if self._last_operation == "run":
            if frame is not None and frame.blocker:
                values.append(frame.blocker)
            if self._run_result is not None and self._run_result.status in {
                "ERROR",
                "BLOCKED",
                "LIMIT",
            }:
                values.append(self._run_result.reason)
            if self._run_error:
                values.append(self._run_error)
        elif self._demo_error:
            values.append(self._demo_error)
        return tuple(dict.fromkeys(values))

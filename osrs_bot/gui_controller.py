from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue

from .application import ApplicationSnapshot, EngineApplication, LifecycleState
from .engine_frame import EngineFrame
from .gui_settings import GuiSettings, GuiSettingsStore


MAX_EVENT_HISTORY = 300
_TERMINAL_RUN_STATES = {
    LifecycleState.COMPLETE,
    LifecycleState.STOPPED,
    LifecycleState.BLOCKED,
    LifecycleState.ERROR,
}


class GuiControllerError(RuntimeError):
    pass


class GuiControllerBusyError(GuiControllerError):
    pass


class GuiControllerClosedError(GuiControllerError):
    pass


@dataclass(frozen=True, slots=True)
class OperationTicket:
    channel: str
    generation: int
    operation: str


@dataclass(frozen=True, slots=True)
class GuiEvent:
    sequence: int
    occurred_at: datetime
    kind: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class GuiControllerSnapshot:
    application: ApplicationSnapshot
    engine_frame: EngineFrame | None
    catalog: Mapping[str, object]
    settings: GuiSettings
    events: tuple[GuiEvent, ...]
    blockers: tuple[str, ...]
    busy_operations: tuple[str, ...]
    pending_mode: str | None
    close_requested: bool
    close_ready: bool
    close_terminal_failure: bool
    operation_results: tuple[tuple[str, object], ...]

    def result(self, name: str) -> object | None:
        return dict(self.operation_results).get(name)


@dataclass(frozen=True, slots=True)
class _OperationValue:
    payload: object | None = None
    result_key: str | None = None
    application: ApplicationSnapshot | None = None
    settings_patch: tuple[tuple[str, object], ...] = ()
    close_ready: bool = False
    close_terminal_failure: bool = False
    message: str | None = None


@dataclass(frozen=True, slots=True)
class _WorkerResult:
    ticket: OperationTicket
    value: _OperationValue | None
    error: str | None
    final: bool


_Reporter = Callable[[_OperationValue], None]
_Worker = Callable[[_Reporter], _OperationValue]


class GuiController:
    """Toolkit-independent asynchronous adapter over one EngineApplication."""

    def __init__(
        self,
        application: EngineApplication,
        *,
        settings_store: GuiSettingsStore | None = None,
    ) -> None:
        self._application = application
        self._settings_store = settings_store or GuiSettingsStore()
        self._lock = threading.RLock()
        self._queue: Queue[_WorkerResult] = Queue()
        self._workers: set[threading.Thread] = set()
        self._generations: dict[str, int] = {}
        self._busy: dict[str, int] = {}
        self._operation_errors: dict[str, str] = {}
        self._results: dict[str, object] = {}
        self._events: deque[GuiEvent] = deque(maxlen=MAX_EVENT_HISTORY)
        self._event_sequence = 0
        self._pending_mode: str | None = None
        self._close_requested = False
        self._close_ready = False
        self._close_terminal_failure = False

        catalog = application.catalog()
        if not isinstance(catalog, Mapping):
            raise TypeError("EngineApplication.catalog() must return a mapping")
        self._catalog = dict(catalog)
        self._settings = self._settings_store.load(
            self._catalog, application.validate_profile
        )
        initial = application.snapshot()
        if not isinstance(initial, ApplicationSnapshot):
            raise TypeError(
                "EngineApplication.snapshot() must return ApplicationSnapshot"
            )
        self._application_snapshot = initial
        self._engine_frame: EngineFrame | None = None
        self._frame_signature: tuple[object, ...] | None = None
        self._apply_application_snapshot_unlocked(initial, announce=False)
        self._record_event_unlocked("controller", "READY", "GUI controller ready")

    @property
    def catalog(self) -> Mapping[str, object]:
        return self._catalog

    @property
    def settings(self) -> GuiSettings:
        with self._lock:
            return self._settings

    def snapshot(self) -> GuiControllerSnapshot:
        with self._lock:
            blockers = list(self._application_snapshot.blockers)
            if self._engine_frame is not None and self._engine_frame.blocker:
                blockers.append(self._engine_frame.blocker)
            blockers.extend(self._operation_errors.values())
            return GuiControllerSnapshot(
                application=self._application_snapshot,
                engine_frame=self._engine_frame,
                catalog=self._catalog,
                settings=self._settings,
                events=tuple(self._events),
                blockers=tuple(dict.fromkeys(blockers)),
                busy_operations=tuple(sorted(self._busy)),
                pending_mode=self._pending_mode,
                close_requested=self._close_requested,
                close_ready=self._close_ready,
                close_terminal_failure=self._close_terminal_failure,
                operation_results=tuple(sorted(self._results.items())),
            )

    def profile_values(self, profile_id: str | None = None) -> dict[str, object]:
        with self._lock:
            selected = profile_id or self._settings.profile_id
            return self._settings_store.profile_values(self._catalog, selected)

    def runtime_configuration(self) -> object:
        """Return the facade-owned immutable limits used by Start confirmation."""

        return self._application.runtime_configuration()

    def validate_profile(
        self, values: Mapping[str, object] | None = None
    ) -> tuple[bool, str]:
        candidate = dict(values or self.profile_values())
        try:
            self._application.validate_profile(candidate)
        except Exception as error:
            return False, str(error)
        return True, "Profile is valid."

    def save_preferences(
        self,
        *,
        profile_id: str | None = None,
        arduino_port: str | None = None,
        overlay_enabled: bool | None = None,
        geometry: str | None = None,
        last_demo_directory: str | None = None,
        update_geometry: bool = False,
        update_last_demo_directory: bool = False,
    ) -> GuiSettings:
        updates: dict[str, object] = {}
        if profile_id is not None:
            updates["profile_id"] = profile_id
        if arduino_port is not None:
            updates["arduino_port"] = arduino_port
        if overlay_enabled is not None:
            updates["overlay_enabled"] = overlay_enabled
        if update_geometry:
            updates["geometry"] = geometry
        if update_last_demo_directory:
            updates["last_demo_directory"] = last_demo_directory
        with self._lock:
            proposed = self._settings.with_updates(**updates)
            try:
                persisted = self._settings_store.save(
                    proposed, self._catalog, self._application.validate_profile
                )
            except Exception as error:
                message = _error_text(error)
                self._operation_errors["settings"] = message
                self._record_event_unlocked("settings", "BLOCKED", message)
                raise
            self._settings = persisted
            self._operation_errors.pop("settings", None)
            return persisted

    def set_window_geometry(self, geometry: str | None) -> GuiSettings:
        return self.save_preferences(geometry=geometry, update_geometry=True)

    def set_last_demo_directory(self, path: Path | str | None) -> GuiSettings:
        value = None if path is None else str(path)
        return self.save_preferences(
            last_demo_directory=value,
            update_last_demo_directory=True,
        )

    def record_event(self, kind: str, message: str, *, status: str = "INFO") -> None:
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("kind must be non-empty")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be non-empty")
        if not isinstance(status, str) or not status.strip():
            raise ValueError("status must be non-empty")
        with self._lock:
            self._record_event_unlocked(kind.strip(), status.strip(), message.strip())

    def request_refresh(self) -> OperationTicket:
        with self._lock:
            existing = self._busy.get("refresh")
            if existing is not None:
                return OperationTicket("refresh", existing, "refresh-state")
        return self._submit(
            "refresh",
            "refresh-state",
            lambda _report: _OperationValue(application=self._application.snapshot()),
            quiet=True,
            allow_during_close=True,
        )

    def refresh_connection(self) -> OperationTicket:
        return self._facade_result(
            "connection",
            "refresh-connection",
            "connection",
            self._application.refresh_connection,
            replace_existing=True,
        )

    def apply_startup_preferences(self) -> OperationTicket:
        """Reapply only revalidated harmless preferences through facade owners."""

        with self._lock:
            port = self._settings.arduino_port
            overlay_enabled = self._settings.overlay_enabled

        def worker(_report: _Reporter) -> _OperationValue:
            if port:
                self._application.set_arduino_port(port)
            self._application.set_overlay_enabled(overlay_enabled)
            return _OperationValue(
                payload={
                    "arduinoPort": port,
                    "overlay": self._application.overlay_snapshot(),
                },
                result_key="startupPreferences",
            )

        return self._submit(
            "startup-preferences",
            "apply-startup-preferences",
            worker,
        )

    def launch_or_connect_runelite(self) -> OperationTicket:
        return self._facade_result(
            "connection",
            "launch-or-connect-runelite",
            "connection",
            self._application.launch_or_connect_runelite,
        )

    def login_or_recover(
        self, arduino_port: str | None = None
    ) -> OperationTicket:
        if arduino_port is not None and not isinstance(arduino_port, str):
            raise TypeError("arduino_port must be a string or None")
        with self._lock:
            selected_port = (
                self._settings.arduino_port
                if arduino_port is None
                else arduino_port.strip()
            )

        def worker(_report: _Reporter) -> _OperationValue:
            settings_patch: tuple[tuple[str, object], ...] = ()
            if selected_port:
                self._application.set_arduino_port(selected_port)
                settings_patch = (("arduino_port", selected_port),)
            recovery = self._application.login_or_recover()
            return _OperationValue(
                payload={
                    "recovery": recovery,
                    "connection": self._application.refresh_connection(),
                },
                result_key="connection",
                settings_patch=settings_patch,
            )

        return self._submit("connection", "login-or-recover", worker)

    def request_arduino_readiness(self, arduino_port: str) -> OperationTicket:
        if not isinstance(arduino_port, str) or not arduino_port.strip():
            raise ValueError("Arduino readiness requires a port")
        port = arduino_port.strip()
        return self._facade_result(
            "arduino-readiness",
            "arduino-readiness",
            "arduinoReadiness",
            lambda: self._application.arduino_readiness(port),
            replace_existing=True,
        )

    def start_observe(
        self, profile_values: Mapping[str, object] | None = None
    ) -> OperationTicket:
        return self._start(profile_values, execute=False, arduino_port=None)

    def start_live(
        self,
        arduino_port: str,
        profile_values: Mapping[str, object] | None = None,
    ) -> OperationTicket:
        if not isinstance(arduino_port, str) or not arduino_port.strip():
            raise ValueError("Start Live requires an Arduino port")
        return self._start(
            profile_values, execute=True, arduino_port=arduino_port.strip()
        )

    def request_pause(self) -> OperationTicket:
        run_id = self._current_run_id()
        # This is a constant-time RuntimeControl flag transition.  Signal it in
        # the GUI callback before spawning the result-adapter worker so the
        # foreground change caused by clicking Pause cannot outrun the request.
        requested = self._application.request_pause(run_id)
        return self._submit(
            "run-command",
            "pause",
            lambda _report: _OperationValue(application=requested),
        )

    def resume(self) -> OperationTicket:
        run_id = self._current_run_id()

        def worker(_report: _Reporter) -> _OperationValue:
            self._application.prepare_live_handoff()
            return _OperationValue(application=self._application.resume(run_id))

        return self._submit(
            "run-command",
            "resume",
            worker,
        )

    def request_safe_stop(self) -> OperationTicket:
        run_id = self._current_run_id()
        # Safe Stop is likewise only an immediate cooperative control signal;
        # transaction completion and cleanup remain on the engine worker.
        requested = self._application.request_safe_stop(run_id)
        return self._submit(
            "run-command",
            "safe-stop",
            lambda _report: _OperationValue(application=requested),
            replace_existing=True,
        )

    def start_demonstration(
        self,
        name: str,
        *,
        output_root: Path = Path("demo_runs"),
        duration_seconds: float = 60.0,
        screenshots_enabled: bool = True,
    ) -> OperationTicket:
        with self._lock:
            self._require_idle_mode_unlocked("demonstration")
            self._pending_mode = "demonstration"

        def worker(_report: _Reporter) -> _OperationValue:
            return _OperationValue(
                application=self._application.begin_demonstration(
                    name,
                    output_root=output_root,
                    duration_seconds=duration_seconds,
                    screenshots_enabled=screenshots_enabled,
                ),
                settings_patch=(("last_demo_directory", str(output_root)),),
            )

        try:
            return self._submit("mode-start", "start-demonstration", worker)
        except Exception:
            with self._lock:
                self._pending_mode = None
            raise

    def stop_demonstration(self, *, timeout: float = 10.0) -> OperationTicket:
        capture_id = self._current_capture_id()
        return self._submit(
            "demo-command",
            "stop-demonstration",
            lambda _report: _OperationValue(
                application=self._application.end_demonstration(
                    capture_id, timeout=timeout
                )
            ),
        )

    def inspect_demonstration(self, path: Path | str) -> OperationTicket:
        artifact = Path(path)
        return self._facade_result(
            "demonstration-inspection",
            "inspect-demonstration",
            "demonstrationInspection",
            lambda: self._application.inspect_demonstration(artifact),
            settings_patch=(("last_demo_directory", str(artifact)),),
        )

    def set_overlay_enabled(self, enabled: bool) -> OperationTicket:
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be bool")

        def worker(_report: _Reporter) -> _OperationValue:
            self._application.set_overlay_enabled(enabled)
            return _OperationValue(
                payload=self._application.overlay_snapshot(),
                result_key="overlay",
                settings_patch=(("overlay_enabled", enabled),),
            )

        return self._submit("overlay", "set-overlay", worker)

    def request_diagnostics(self) -> OperationTicket:
        return self._facade_result(
            "diagnostics",
            "diagnostics",
            "diagnostics",
            self._application.diagnostics,
            replace_existing=True,
        )

    def run_quick_self_test(self) -> OperationTicket:
        return self._facade_result(
            "self-test",
            "quick-self-test",
            "quickSelfTest",
            self._application.run_quick_self_test,
        )

    def run_golden_replay(self) -> OperationTicket:
        return self._facade_result(
            "golden-replay",
            "golden-replay",
            "goldenReplay",
            self._application.run_golden_replay,
        )

    def request_close(self, *, timeout: float = 120.0) -> OperationTicket:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
        ):
            raise ValueError("timeout must be positive")
        with self._lock:
            if self._close_ready:
                raise GuiControllerClosedError("frontend shutdown is already complete")
            self._close_requested = True
            self._record_event_unlocked(
                "close", "REQUESTED", "Cooperative frontend shutdown requested"
            )

        def worker(report: _Reporter) -> _OperationValue:
            deadline = time.monotonic() + float(timeout)
            with self._lock:
                siblings = tuple(
                    candidate
                    for candidate in self._workers
                    if candidate is not threading.current_thread()
                )
            if siblings:
                report(
                    _OperationValue(
                        message=(
                            "Waiting for active frontend work before Safe Stop"
                        )
                    )
                )
            for sibling in siblings:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "frontend work did not finish before the close deadline"
                    )
                sibling.join(remaining)
                if sibling.is_alive():
                    raise TimeoutError(
                        "frontend work did not finish before the close deadline"
                    )
            current = self._application.snapshot()
            final = current
            if current.active_run_id is not None:
                run_id = current.active_run_id
                try:
                    requested = self._application.request_safe_stop(run_id)
                except Exception:
                    refreshed = self._application.snapshot()
                    if (
                        refreshed.run_id != run_id
                        or refreshed.active_run_id is not None
                        or refreshed.lifecycle not in _TERMINAL_RUN_STATES
                    ):
                        raise
                    final = refreshed
                else:
                    report(
                        _OperationValue(
                            application=requested,
                            message=(
                                "Safe Stop requested; awaiting transaction "
                                "and cleanup"
                            ),
                        )
                    )
                    remaining = max(0.0, deadline - time.monotonic())
                    final = self._application.wait(run_id, timeout=remaining)
                if final.active_run_id is not None:
                    raise TimeoutError(
                        "Safe Stop is still awaiting transaction, verification, "
                        "or cleanup"
                    )
                if final.lifecycle not in _TERMINAL_RUN_STATES:
                    raise RuntimeError(
                        f"run ended in unresolved lifecycle {final.lifecycle.value}"
                    )
            elif current.active_capture_id is not None:
                capture_id = current.active_capture_id
                report(
                    _OperationValue(
                        application=current,
                        message="Stopping and finalizing the active demonstration",
                    )
                )
                try:
                    final = self._application.end_demonstration(
                        capture_id,
                        timeout=max(0.0, deadline - time.monotonic()),
                    )
                except Exception:
                    refreshed = self._application.snapshot()
                    if (
                        refreshed.capture_id != capture_id
                        or refreshed.active_capture_id is not None
                    ):
                        raise
                    final = refreshed
                if final.active_capture_id is not None:
                    raise TimeoutError(
                        "demonstration finalization is still in progress"
                    )
            remaining = max(0.0, deadline - time.monotonic())
            self._application.shutdown_frontend(timeout=remaining)
            terminal_failure = final.lifecycle in {
                LifecycleState.BLOCKED,
                LifecycleState.ERROR,
            }
            return _OperationValue(
                application=final,
                close_ready=True,
                close_terminal_failure=terminal_failure,
                message=(
                    "Frontend shutdown reached a terminal failure"
                    if terminal_failure
                    else "Frontend shutdown completed safely"
                ),
            )

        return self._submit(
            "close",
            "close",
            worker,
            allow_during_close=True,
        )

    def drain_results(self, *, limit: int | None = None) -> GuiControllerSnapshot:
        if limit is not None and (
            not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0
        ):
            raise ValueError("limit must be a positive integer or None")
        processed = 0
        while limit is None or processed < limit:
            try:
                result = self._queue.get_nowait()
            except Empty:
                break
            processed += 1
            with self._lock:
                current_generation = self._generations.get(result.ticket.channel)
                if current_generation != result.ticket.generation:
                    continue
                if result.final:
                    if (
                        self._busy.get(result.ticket.channel)
                        == result.ticket.generation
                    ):
                        self._busy.pop(result.ticket.channel, None)
                if result.error is not None:
                    self._operation_errors[result.ticket.channel] = result.error
                    if result.ticket.operation in {
                        "start-observe",
                        "start-live",
                        "start-demonstration",
                    }:
                        self._pending_mode = None
                    self._record_event_unlocked(
                        result.ticket.operation, "BLOCKED", result.error
                    )
                    continue
                assert result.value is not None
                try:
                    self._apply_value_unlocked(result.ticket.operation, result.value)
                except Exception as error:
                    message = _error_text(error)
                    self._operation_errors[result.ticket.channel] = message
                    self._record_event_unlocked(
                        result.ticket.operation, "BLOCKED", message
                    )
                    continue
                if result.final:
                    self._operation_errors.pop(result.ticket.channel, None)
                    if result.ticket.operation != "refresh-state":
                        message = result.value.message or (
                            f"{result.ticket.operation} completed"
                        )
                        self._record_event_unlocked(
                            result.ticket.operation, "COMPLETE", message
                        )
                elif result.value.message:
                    self._record_event_unlocked(
                        result.ticket.operation, "RUNNING", result.value.message
                    )
        with self._lock:
            self._workers = {worker for worker in self._workers if worker.is_alive()}
        return self.snapshot()

    def wait_for_idle(self, timeout: float = 5.0) -> GuiControllerSnapshot:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout < 0
        ):
            raise ValueError("timeout must be non-negative")
        deadline = time.monotonic() + float(timeout)
        while True:
            state = self.drain_results()
            with self._lock:
                workers = tuple(self._workers)
                busy = bool(self._busy)
            if not workers and not busy:
                return state
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return state
            for worker in workers:
                if worker is not threading.current_thread():
                    worker.join(min(0.02, remaining))

    def join_workers(self, timeout: float = 5.0) -> bool:
        state = self.wait_for_idle(timeout)
        del state
        with self._lock:
            return not self._workers and not self._busy

    def _start(
        self,
        profile_values: Mapping[str, object] | None,
        *,
        execute: bool,
        arduino_port: str | None,
    ) -> OperationTicket:
        values = dict(profile_values or self.profile_values())
        with self._lock:
            self._require_idle_mode_unlocked("run")
            self._pending_mode = "run"
        operation = "start-live" if execute else "start-observe"

        def worker(_report: _Reporter) -> _OperationValue:
            self._application.validate_profile(values)
            settings_patch: list[tuple[str, object]] = []
            profile_id = values.get("profileId")
            if isinstance(profile_id, str):
                settings_patch.append(("profile_id", profile_id))
            if execute:
                assert arduino_port is not None
                self._application.set_arduino_port(arduino_port)
                settings_patch.append(("arduino_port", arduino_port))
                self._application.prepare_live_handoff()
            started = self._application.start(
                profile_values=values,
                execute=execute,
            )
            return _OperationValue(
                application=started, settings_patch=tuple(settings_patch)
            )

        try:
            return self._submit("mode-start", operation, worker)
        except Exception:
            with self._lock:
                self._pending_mode = None
            raise

    def _facade_result(
        self,
        channel: str,
        operation: str,
        result_key: str,
        call: Callable[[], object],
        *,
        settings_patch: tuple[tuple[str, object], ...] = (),
        replace_existing: bool = False,
    ) -> OperationTicket:
        return self._submit(
            channel,
            operation,
            lambda _report: _OperationValue(
                payload=call(),
                result_key=result_key,
                settings_patch=settings_patch,
            ),
            replace_existing=replace_existing,
        )

    def _submit(
        self,
        channel: str,
        operation: str,
        worker: _Worker,
        *,
        replace_existing: bool = False,
        quiet: bool = False,
        allow_during_close: bool = False,
    ) -> OperationTicket:
        with self._lock:
            if self._close_ready:
                raise GuiControllerClosedError("frontend shutdown is complete")
            if self._close_requested and not allow_during_close:
                raise GuiControllerClosedError("frontend shutdown is in progress")
            if channel in self._busy and not replace_existing:
                raise GuiControllerBusyError(f"{channel} operation is already running")
            generation = self._generations.get(channel, 0) + 1
            self._generations[channel] = generation
            self._busy[channel] = generation
            ticket = OperationTicket(channel, generation, operation)
            if not quiet:
                self._record_event_unlocked(
                    operation, "REQUESTED", f"{operation} requested"
                )

            def report(value: _OperationValue) -> None:
                self._queue.put(_WorkerResult(ticket, value, None, False))

            def run() -> None:
                try:
                    value = worker(report)
                except Exception as error:
                    self._queue.put(
                        _WorkerResult(ticket, None, _error_text(error), True)
                    )
                else:
                    self._queue.put(_WorkerResult(ticket, value, None, True))

            thread = threading.Thread(
                target=run,
                name=f"osrs-gui-{operation}-{generation}",
                daemon=False,
            )
            self._workers.add(thread)
            thread.start()
            return ticket

    def _apply_value_unlocked(
        self, operation: str, value: _OperationValue
    ) -> None:
        if value.application is not None:
            self._apply_application_snapshot_unlocked(value.application, announce=True)
        if value.result_key is not None:
            self._results[value.result_key] = value.payload
        if value.settings_patch:
            updates = dict(value.settings_patch)
            proposed = self._settings.with_updates(**updates)
            self._settings = self._settings_store.save(
                proposed, self._catalog, self._application.validate_profile
            )
        if operation in {"start-observe", "start-live", "start-demonstration"}:
            self._pending_mode = None
        if value.close_ready:
            self._close_ready = True
            self._close_terminal_failure = value.close_terminal_failure

    def _apply_application_snapshot_unlocked(
        self, snapshot: ApplicationSnapshot, *, announce: bool
    ) -> None:
        if not isinstance(snapshot, ApplicationSnapshot):
            raise TypeError("facade operation did not return ApplicationSnapshot")
        previous = getattr(self, "_application_snapshot", None)
        if previous is not None and snapshot.run_id != previous.run_id:
            self._engine_frame = None
            self._frame_signature = None
        self._application_snapshot = snapshot
        frame = snapshot.engine_frame
        if frame is not None:
            if not isinstance(frame, EngineFrame):
                raise TypeError("application snapshot engine_frame must be EngineFrame")
            if (
                self._engine_frame is None
                or frame.sequence > self._engine_frame.sequence
            ):
                self._engine_frame = frame
                signature = (
                    frame.stage,
                    frame.task.status,
                    frame.task.state,
                    frame.blocker,
                    frame.last_execution_status,
                    frame.pending_verification,
                )
                if announce and signature != self._frame_signature:
                    self._record_event_unlocked(
                        "engine-frame",
                        "RUNNING",
                        f"EngineFrame {frame.sequence}: {frame.stage.value}",
                    )
                self._frame_signature = signature
        if (
            previous is not None
            and announce
            and snapshot.lifecycle is not previous.lifecycle
        ):
            self._record_event_unlocked(
                "lifecycle",
                snapshot.lifecycle.value.upper(),
                f"Lifecycle: {snapshot.lifecycle.value}",
            )

    def _require_idle_mode_unlocked(self, requested: str) -> None:
        if self._pending_mode is not None:
            raise GuiControllerBusyError(
                f"{self._pending_mode} startup is already in progress"
            )
        if self._application_snapshot.active_run_id is not None:
            raise GuiControllerBusyError(
                f"cannot start {requested} while an engine run is active"
            )
        if self._application_snapshot.active_capture_id is not None:
            raise GuiControllerBusyError(
                f"cannot start {requested} while a demonstration is active"
            )

    def _current_run_id(self) -> str:
        with self._lock:
            run_id = self._application_snapshot.active_run_id
        if run_id is None:
            raise GuiControllerError("there is no active run")
        return run_id

    def _current_capture_id(self) -> str:
        with self._lock:
            capture_id = self._application_snapshot.active_capture_id
        if capture_id is None:
            raise GuiControllerError("there is no active demonstration")
        return capture_id

    def _record_event_unlocked(self, kind: str, status: str, message: str) -> None:
        self._event_sequence += 1
        self._events.append(
            GuiEvent(
                self._event_sequence,
                datetime.now(timezone.utc),
                kind,
                status,
                message,
            )
        )


def _error_text(error: Exception) -> str:
    detail = str(error).strip()
    return f"{type(error).__name__}: {detail}" if detail else type(error).__name__

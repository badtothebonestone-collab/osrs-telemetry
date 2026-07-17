from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from .engine_frame import ENGINE_FRAME_SCHEMA, EngineFramePublisher
from .input_coordinator import InputCoordinator, read_arduino_lease_status
from .login import (
    LoginPromptHelper,
    RuneLiteWindow,
    find_runelite_window,
    focus_exact_window,
)
from .model import Observation, ScreenBounds, ScreenPoint
from .observation import ObservationClient, RESPONSE_SCHEMA, SENSOR_FRAME_SCHEMA
from .profile import profile_contract


OPERATOR_PREFLIGHT_SCHEMA = "operator_preflight.v1"
OPERATOR_DIAGNOSTICS_SCHEMA = "operator_diagnostics.v1"
APPLICATION_SCHEMA = "engine_application.v2"
MAX_OPERATOR_ERROR_LENGTH = 2_000
MAX_PROCESS_OUTPUT_TAIL_BYTES = 32_768
MAX_ARTIFACT_DIRECTORIES = 4_096
RETAINED_LAYOUT_CANVAS_SIZE = (2_151, 1_519)
RETAINED_LAYOUT_CLIENT_SIZE = (2_243, 1_585)


class ConnectionState(str, Enum):
    CONNECTED = "connected"
    NOT_FOUND = "not_found"
    BLOCKED = "blocked"
    ERROR = "error"


class RuneLiteLaunchState(str, Enum):
    CONNECTED = "connected"
    LAUNCHED = "launched"
    STARTING = "starting"
    BLOCKED = "blocked"
    FAILED = "failed"


class OverlayState(str, Enum):
    DISABLED = "disabled"
    STARTING = "starting"
    ACTIVE = "active"
    STOPPING = "stopping"
    FAILED = "failed"


class ProcessStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    NOT_RUN = "NOT_RUN"


@dataclass(frozen=True, slots=True)
class ConnectionSnapshot:
    state: ConnectionState
    captured_at: datetime
    endpoint_healthy: bool
    runelite_found: bool
    process_id: int | None
    session_id: str | None
    exact_process_binding: bool
    loaded_scene: bool
    game_state: str
    foreground: bool
    coherent_fresh_observation: bool
    cursor_inside_client: bool | None
    layout_supported: bool | None
    canvas_bounds: ScreenBounds | None
    client_bounds: ScreenBounds | None
    blocker: str | None = None
    diagnostic: str | None = None
    source_tick: int | None = None
    source_captured_at: datetime | None = None
    max_source_age_millis: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": OPERATOR_PREFLIGHT_SCHEMA,
            "state": self.state.value,
            "capturedAtUtc": self.captured_at.isoformat(),
            "endpointHealthy": self.endpoint_healthy,
            "runeLiteFound": self.runelite_found,
            "processId": self.process_id,
            "sessionId": self.session_id,
            "exactProcessBinding": self.exact_process_binding,
            "loadedScene": self.loaded_scene,
            "gameState": self.game_state,
            "foreground": self.foreground,
            "coherentFreshObservation": self.coherent_fresh_observation,
            "cursorInsideClient": self.cursor_inside_client,
            "layoutSupported": self.layout_supported,
            "canvasBounds": _bounds_dict(self.canvas_bounds),
            "clientBounds": _bounds_dict(self.client_bounds),
            "blocker": self.blocker,
            "diagnostic": self.diagnostic,
            "sourceTick": self.source_tick,
            "sourceCapturedAtUtc": (
                self.source_captured_at.isoformat()
                if self.source_captured_at is not None
                else None
            ),
            "maxSourceAgeMillis": self.max_source_age_millis,
        }


@dataclass(frozen=True, slots=True)
class RuneLiteLaunchResult:
    state: RuneLiteLaunchState
    reason: str
    launched: bool
    connection: ConnectionSnapshot
    log_path: Path | None = None

    @property
    def successful(self) -> bool:
        return self.state in {
            RuneLiteLaunchState.CONNECTED,
            RuneLiteLaunchState.LAUNCHED,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "reason": self.reason,
            "launched": self.launched,
            "successful": self.successful,
            "logPath": None if self.log_path is None else str(self.log_path),
            "connection": self.connection.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class SessionRecoveryResult:
    status: str
    reason: str
    loaded_scene: bool
    elapsed_seconds: float
    click_count: int
    cleanup_status: str

    @property
    def successful(self) -> bool:
        return self.status == "PASS" and self.loaded_scene

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "loadedScene": self.loaded_scene,
            "elapsedSeconds": round(self.elapsed_seconds, 3),
            "clickCount": self.click_count,
            "cleanupStatus": self.cleanup_status,
            "successful": self.successful,
        }


@dataclass(frozen=True, slots=True)
class ArduinoReadiness:
    selected_port: str | None
    available_ports: tuple[str, ...]
    port_available: bool
    lease_status: str
    lease_available: bool | None
    lease_owner: str | None
    lease_owner_pid: int | None
    lease_reason: str
    captured_at: datetime | None = None
    max_age_millis: int = 2_000

    def __post_init__(self) -> None:
        if self.captured_at is not None and not isinstance(
            self.captured_at, datetime
        ):
            raise TypeError("captured_at must be a datetime or None")
        if (
            not isinstance(self.max_age_millis, int)
            or isinstance(self.max_age_millis, bool)
            or self.max_age_millis < 0
        ):
            raise ValueError("max_age_millis must be a non-negative integer")

    @property
    def ready(self) -> bool:
        return self.port_available and self.lease_available is True

    def to_dict(self) -> dict[str, Any]:
        return {
            "selectedPort": self.selected_port,
            "availablePorts": list(self.available_ports),
            "portAvailable": self.port_available,
            "leaseStatus": self.lease_status,
            "leaseAvailable": self.lease_available,
            "leaseOwner": self.lease_owner,
            "leaseOwnerPid": self.lease_owner_pid,
            "leaseReason": self.lease_reason,
            "ready": self.ready,
            "capturedAtUtc": (
                self.captured_at.isoformat()
                if self.captured_at is not None
                else None
            ),
            "maxAgeMillis": self.max_age_millis,
        }


@dataclass(frozen=True, slots=True)
class OverlaySnapshot:
    state: OverlayState
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state.value, "error": self.error}


@dataclass(frozen=True, slots=True)
class ProcessResult:
    status: ProcessStatus
    command: tuple[str, ...]
    exit_code: int | None
    elapsed_seconds: float
    output_tail: str
    log_path: Path

    @property
    def passed(self) -> bool:
        return self.status is ProcessStatus.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "command": list(self.command),
            "exitCode": self.exit_code,
            "elapsedSeconds": round(self.elapsed_seconds, 3),
            "summary": _concise_process_summary(self),
            "logPath": str(self.log_path),
        }


@dataclass(frozen=True, slots=True)
class OperatorDiagnostics:
    captured_at: datetime
    commit: str | None
    dirty: bool | None
    application_schema: str
    profile_schema: str
    engine_frame_schema: str
    endpoint_schema: str
    sensor_frame_schema: str
    python_version: str
    java_available: bool
    java_version: str | None
    gradle_available: bool
    gradle_version: str | None
    latest_proof_path: Path | None
    latest_demonstration_path: Path | None
    available_arduino_ports: tuple[str, ...]
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": OPERATOR_DIAGNOSTICS_SCHEMA,
            "capturedAtUtc": self.captured_at.isoformat(),
            "commit": self.commit,
            "dirty": self.dirty,
            "applicationSchema": self.application_schema,
            "profileSchema": self.profile_schema,
            "engineFrameSchema": self.engine_frame_schema,
            "endpointSchema": self.endpoint_schema,
            "sensorFrameSchema": self.sensor_frame_schema,
            "pythonVersion": self.python_version,
            "javaAvailable": self.java_available,
            "javaVersion": self.java_version,
            "gradleAvailable": self.gradle_available,
            "gradleVersion": self.gradle_version,
            "latestProofPath": (
                None if self.latest_proof_path is None else str(self.latest_proof_path)
            ),
            "latestDemonstrationPath": (
                None
                if self.latest_demonstration_path is None
                else str(self.latest_demonstration_path)
            ),
            "availableArduinoPorts": list(self.available_arduino_ports),
            "errors": list(self.errors),
        }


class _ObservationSource(Protocol):
    def fetch(self) -> Observation: ...


class _Overlay(Protocol):
    def start(self, *, timeout_seconds: float = 3.0) -> None: ...

    def stop(self, *, timeout_seconds: float = 3.0) -> None: ...


class _LaunchHandle(Protocol):
    def poll(self) -> int | None: ...


class _ProcessRunner(Protocol):
    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        log_path: Path,
    ) -> ProcessResult: ...


class PassiveOverlayOwner:
    """Own one existing passive overlay without acquiring control authority."""

    def __init__(
        self,
        factory: Callable[
            [EngineFramePublisher, bool, Callable[[], object] | None, str | None],
            _Overlay,
        ],
    ) -> None:
        if not callable(factory):
            raise TypeError("overlay factory must be callable")
        self._factory = factory
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._overlay: _Overlay | None = None
        self._publisher: EngineFramePublisher | None = None
        self._presentation_provider: Callable[[], object] | None = None
        self._bound_run_id: str | None = None
        self._state = OverlayState.DISABLED
        self._error: str | None = None

    def snapshot(self) -> OverlaySnapshot:
        with self._state_lock:
            return OverlaySnapshot(self._state, self._error)

    def enable(
        self,
        publisher: EngineFramePublisher,
        *,
        show_rejected: bool = False,
        timeout_seconds: float = 3.0,
        presentation_provider: Callable[[], object] | None = None,
        bound_run_id: str | None = None,
    ) -> OverlaySnapshot:
        if not isinstance(publisher, EngineFramePublisher):
            raise TypeError("publisher must be EngineFramePublisher")
        with self._operation_lock:
            if (
                self._overlay is not None
                and self._publisher is publisher
                and self._presentation_provider is presentation_provider
                and self._bound_run_id == bound_run_id
                and self.snapshot().state is OverlayState.ACTIVE
            ):
                return self.snapshot()
            if self._overlay is not None:
                stopped = self._disable_unlocked(timeout_seconds)
                if stopped.state is OverlayState.FAILED:
                    return stopped
            self._set_state(OverlayState.STARTING, None)
            overlay: _Overlay | None = None
            try:
                overlay = self._factory(
                    publisher,
                    bool(show_rejected),
                    presentation_provider,
                    bound_run_id,
                )
                overlay.start(timeout_seconds=float(timeout_seconds))
            except Exception as error:
                cleanup_error: str | None = None
                if overlay is not None:
                    try:
                        overlay.stop(timeout_seconds=min(1.0, float(timeout_seconds)))
                    except Exception as raised:
                        cleanup_error = _error_text(raised)
                self._overlay = overlay if cleanup_error is not None else None
                self._publisher = publisher if cleanup_error is not None else None
                self._presentation_provider = (
                    presentation_provider if cleanup_error is not None else None
                )
                self._bound_run_id = bound_run_id if cleanup_error is not None else None
                detail = _error_text(error)
                if cleanup_error is not None:
                    detail = f"{detail}; cleanup: {cleanup_error}"
                self._set_state(OverlayState.FAILED, detail)
            else:
                self._overlay = overlay
                self._publisher = publisher
                self._presentation_provider = presentation_provider
                self._bound_run_id = bound_run_id
                self._set_state(OverlayState.ACTIVE, None)
            return self.snapshot()

    def disable(self, *, timeout_seconds: float = 3.0) -> OverlaySnapshot:
        with self._operation_lock:
            return self._disable_unlocked(timeout_seconds)

    def _disable_unlocked(self, timeout_seconds: float) -> OverlaySnapshot:
        overlay = self._overlay
        if overlay is None:
            self._publisher = None
            self._presentation_provider = None
            self._bound_run_id = None
            self._set_state(OverlayState.DISABLED, None)
            return self.snapshot()
        self._set_state(OverlayState.STOPPING, None)
        try:
            overlay.stop(timeout_seconds=float(timeout_seconds))
        except Exception as error:
            self._set_state(OverlayState.FAILED, _error_text(error))
            return self.snapshot()
        else:
            self._overlay = None
            self._publisher = None
            self._presentation_provider = None
            self._bound_run_id = None
            self._set_state(OverlayState.DISABLED, None)
        return self.snapshot()

    def _set_state(self, state: OverlayState, error: str | None) -> None:
        with self._state_lock:
            self._state = state
            self._error = error


class OperatorServices:
    """Bounded operator facilities over one shared ObservationClient.

    These services launch or inspect existing owners.  They never select a task
    target, run safety or verification, open Arduino transport directly, or
    synthesize input.
    """

    def __init__(
        self,
        client: _ObservationSource,
        *,
        repo_root: Path | str | None = None,
        window_finder: Callable[[int], RuneLiteWindow] = find_runelite_window,
        window_focuser: Callable[[RuneLiteWindow], bool] = focus_exact_window,
        window_scanner: Callable[[], tuple[RuneLiteWindow, ...]] | None = None,
        process_checker: Callable[[int], bool] | None = None,
        cursor_reader: Callable[[], ScreenPoint | None] | None = None,
        layout_checker: Callable[[Observation], bool | None] | None = None,
        serial_port_lister: Callable[[], Sequence[str]] | None = None,
        lease_reader: Callable[[str], Mapping[str, Any]] = read_arduino_lease_status,
        coordinator_factory: Callable[[str], Any] | None = None,
        login_helper_factory: Callable[[Any, Any], Any] = LoginPromptHelper,
        overlay_factory: Callable[
            [EngineFramePublisher, bool, Callable[[], object] | None, str | None],
            _Overlay,
        ]
        | None = None,
        process_runner: _ProcessRunner | None = None,
        plugin_launcher: Callable[[Path, Path], _LaunchHandle] | None = None,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not callable(getattr(client, "fetch", None)):
            raise TypeError("client must expose fetch()")
        self._client = client
        self._repo_root = (
            Path(__file__).resolve().parents[1]
            if repo_root is None
            else Path(repo_root).resolve()
        )
        self._window_finder = window_finder
        self._window_focuser = window_focuser
        self._window_scanner = window_scanner or (
            lambda: _discover_runelite_windows(self._window_finder)
        )
        self._process_checker = process_checker or _process_is_running
        self._cursor_reader = cursor_reader or _read_cursor
        self._layout_checker = layout_checker or retained_layout_supported
        self._serial_port_lister = serial_port_lister or _available_serial_ports
        self._lease_reader = lease_reader
        self._coordinator_factory = coordinator_factory or (
            lambda port: InputCoordinator.for_arduino_port(
                port, serial_owner="osrs-operator-login"
            )
        )
        self._login_helper_factory = login_helper_factory
        self._process_runner = process_runner or run_bounded_process
        self._plugin_launcher = plugin_launcher or _launch_plugin
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._sleep = sleep
        self._launch_lock = threading.Lock()
        self._launch_handle: _LaunchHandle | None = None
        self._launch_log_path: Path | None = None
        self._overlay_owner = PassiveOverlayOwner(
            overlay_factory or _default_overlay_factory
        )

    @property
    def client(self) -> _ObservationSource:
        return self._client

    def focus_runelite_for_live_handoff(
        self, *, timeout_seconds: float = 2.0
    ) -> ConnectionSnapshot:
        """Focus only the exact telemetry-owning RuneLite window, then prove it.

        This is bounded operator setup, not gameplay input.  It reuses the
        retained focus-only helper and requires the plugin to report the same
        exact PID/session as foreground before returning.
        """

        timeout = _bounded_seconds(timeout_seconds, "timeout_seconds", 5.0)
        current = self.connection_status()
        if current.process_id is None or not current.exact_process_binding:
            raise RuntimeError(
                "live_handoff_exact_runelite_process_binding_unavailable"
            )
        window = self._window_finder(current.process_id)
        if window.pid != current.process_id:
            raise RuntimeError("live_handoff_runelite_pid_changed")
        if not self._window_focuser(window):
            raise RuntimeError("live_handoff_runelite_focus_failed")

        deadline = self._monotonic() + timeout
        latest = current
        while True:
            latest = self.connection_status()
            if (
                latest.process_id == current.process_id
                and latest.session_id == current.session_id
                and latest.exact_process_binding
                and latest.foreground
            ):
                return latest
            if self._monotonic() >= deadline:
                raise RuntimeError(
                    "live_handoff_runelite_foreground_not_proven"
                )
            self._sleep(0.05)

    def connection_status(self) -> ConnectionSnapshot:
        captured_at = self._aware_now()
        try:
            observation = self._client.fetch()
        except Exception as error:
            candidates, scan_error = self._scan_windows()
            process_id = candidates[0].pid if len(candidates) == 1 else None
            client_bounds = (
                candidates[0].client_bounds if len(candidates) == 1 else None
            )
            cursor_inside = self._cursor_inside(client_bounds)
            diagnostic = _error_text(error)
            if scan_error:
                diagnostic = f"{diagnostic}; window scan: {scan_error}"
            return ConnectionSnapshot(
                ConnectionState.BLOCKED if candidates else ConnectionState.NOT_FOUND,
                captured_at,
                False,
                bool(candidates),
                process_id,
                None,
                False,
                False,
                "UNKNOWN",
                False,
                False,
                cursor_inside,
                None,
                None,
                client_bounds,
                "snapshot_endpoint_unavailable",
                diagnostic,
            )

        process_id = observation.client_process_id
        process_running = bool(
            process_id is not None and self._process_checker(process_id)
        )
        window: RuneLiteWindow | None = None
        window_error: str | None = None
        if process_id is not None:
            try:
                window = self._window_finder(process_id)
            except Exception as error:
                window_error = _error_text(error)
        exact_binding = bool(
            window is not None
            and process_id is not None
            and window.pid == process_id
        )
        client_bounds = (
            window.client_bounds
            if exact_binding and window is not None
            else observation.client_window_bounds
        )
        coherent_fresh = bool(
            observation.fresh
            and observation.cache_wall_clock_fresh
            and observation.source_coherent
            and observation.timestamp_not_future
            and not observation.missing_capabilities
        )
        layout_supported: bool | None = None
        layout_error: str | None = None
        try:
            raw_layout_supported = self._layout_checker(observation)
            layout_supported = (
                raw_layout_supported
                if isinstance(raw_layout_supported, bool)
                else None
            )
        except Exception as error:
            layout_error = _error_text(error)
        blocker: str | None
        state: ConnectionState
        if process_id is None:
            state = ConnectionState.BLOCKED
            blocker = "telemetry_client_process_id_unavailable"
        elif not exact_binding:
            state = ConnectionState.BLOCKED
            blocker = "exact_runelite_process_binding_unavailable"
        else:
            state = ConnectionState.CONNECTED
            blocker = None if observation.loaded_scene else "loaded_scene_not_ready"
        diagnostics = tuple(
            value
            for value in (
                window_error,
                layout_error,
                None
                if observation.loaded_scene
                else (
                    f"status={observation.status}; gameState={observation.game_state}; "
                    f"warnings={list(observation.warnings)}; "
                    f"missing={list(observation.missing_capabilities)}"
                ),
            )
            if value
        )
        return ConnectionSnapshot(
            state,
            captured_at,
            True,
            process_running or window is not None,
            process_id,
            observation.session_id,
            exact_binding,
            observation.loaded_scene,
            observation.game_state,
            observation.client_focused,
            coherent_fresh,
            self._cursor_inside(client_bounds),
            layout_supported,
            observation.canvas_bounds,
            client_bounds,
            blocker,
            "; ".join(diagnostics) or None,
            observation.tick,
            observation.timestamp,
            observation.max_source_age_millis,
        )

    def launch_or_connect_runelite(
        self,
        *,
        timeout_seconds: float = 45.0,
        poll_seconds: float = 0.5,
    ) -> RuneLiteLaunchResult:
        timeout = _bounded_seconds(timeout_seconds, "timeout_seconds", 120.0)
        poll = _bounded_seconds(poll_seconds, "poll_seconds", 5.0)
        with self._launch_lock:
            current = self.connection_status()
            if current.state is ConnectionState.CONNECTED:
                return RuneLiteLaunchResult(
                    RuneLiteLaunchState.CONNECTED,
                    "connected to the existing telemetry-owning RuneLite client",
                    False,
                    current,
                    self._launch_log_path,
                )
            if current.endpoint_healthy or current.runelite_found:
                return RuneLiteLaunchResult(
                    RuneLiteLaunchState.BLOCKED,
                    "an existing RuneLite or telemetry surface was found; duplicate launch refused",
                    False,
                    current,
                    self._launch_log_path,
                )

            launched_now = False
            handle = self._launch_handle
            if handle is None or handle.poll() is not None:
                log_path = self._new_log_path("runelite_launch")
                try:
                    handle = self._plugin_launcher(self._repo_root, log_path)
                except Exception as error:
                    return RuneLiteLaunchResult(
                        RuneLiteLaunchState.FAILED,
                        f"RuneLite launch failed: {_error_text(error)}",
                        False,
                        current,
                        log_path,
                    )
                self._launch_handle = handle
                self._launch_log_path = log_path
                launched_now = True

            deadline = self._monotonic() + timeout
            while True:
                current = self.connection_status()
                if current.state is ConnectionState.CONNECTED:
                    return RuneLiteLaunchResult(
                        (
                            RuneLiteLaunchState.LAUNCHED
                            if launched_now
                            else RuneLiteLaunchState.CONNECTED
                        ),
                        "RuneLite endpoint and exact process binding are ready",
                        launched_now,
                        current,
                        self._launch_log_path,
                    )
                return_code = handle.poll()
                if return_code is not None:
                    return RuneLiteLaunchResult(
                        RuneLiteLaunchState.FAILED,
                        f"RuneLite launch process exited with code {return_code}",
                        launched_now,
                        current,
                        self._launch_log_path,
                    )
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return RuneLiteLaunchResult(
                        RuneLiteLaunchState.STARTING,
                        "RuneLite launch is still running; endpoint is not ready yet",
                        launched_now,
                        current,
                        self._launch_log_path,
                    )
                self._sleep(min(poll, remaining))

    def recover_session(
        self,
        arduino_port: str,
        *,
        max_clicks: int = 3,
        timeout_seconds: float = 30.0,
    ) -> SessionRecoveryResult:
        if not isinstance(arduino_port, str) or not arduino_port.strip():
            raise ValueError("arduino_port must be non-empty text")
        if not isinstance(max_clicks, int) or isinstance(max_clicks, bool):
            raise ValueError("max_clicks must be an integer")
        click_limit = max(0, min(3, max_clicks))
        timeout = _bounded_seconds(timeout_seconds, "timeout_seconds", 60.0)
        started = self._monotonic()
        try:
            coordinator = self._coordinator_factory(arduino_port.strip())
            helper = self._login_helper_factory(self._client, coordinator)
            result = helper.run(
                max_clicks=click_limit,
                timeout_seconds=timeout,
            )
        except Exception as error:
            return SessionRecoveryResult(
                "ERROR",
                _error_text(error),
                False,
                max(0.0, self._monotonic() - started),
                0,
                "UNRESOLVED",
            )
        clicks = tuple(getattr(result, "clicks", ()))
        if not clicks:
            cleanup_status = "NOT_REQUIRED"
        elif all(
            bool(
                getattr(click.receipt, "successful", False)
                or getattr(click.receipt, "safely_unsent", False)
            )
            for click in clicks
        ):
            cleanup_status = "COMPLETE"
        else:
            cleanup_status = "UNRESOLVED"
        return SessionRecoveryResult(
            str(getattr(result, "status", "ERROR")),
            str(getattr(result, "reason", "login helper returned no reason")),
            bool(getattr(result, "loaded_scene", False)),
            float(getattr(result, "elapsed_seconds", self._monotonic() - started)),
            len(clicks),
            cleanup_status,
        )

    def arduino_readiness(self, arduino_port: str | None) -> ArduinoReadiness:
        try:
            captured_at = self._aware_now()
        except Exception:
            # Additive presentation age cannot change the readiness probe.
            captured_at = None
        port_error: str | None = None
        try:
            ports = tuple(
                sorted(
                    {
                        str(value).strip()
                        for value in self._serial_port_lister()
                        if str(value).strip()
                    },
                    key=str.casefold,
                )
            )
        except Exception as error:
            ports = ()
            port_error = _error_text(error)
        selected = arduino_port.strip() if isinstance(arduino_port, str) and arduino_port.strip() else None
        port_available = bool(
            selected is not None
            and any(value.casefold() == selected.casefold() for value in ports)
        )
        if selected is None:
            return ArduinoReadiness(
                None,
                ports,
                False,
                "UNKNOWN",
                None,
                None,
                None,
                port_error or "no Arduino port is selected",
                captured_at=captured_at,
            )
        try:
            raw = self._lease_reader(selected)
            lease_status = str(raw.get("status") or "UNKNOWN")
            lease_available_raw = raw.get("available")
            lease_available = (
                lease_available_raw if isinstance(lease_available_raw, bool) else None
            )
            owner = raw.get("owner")
            owner_pid = raw.get("ownerPid")
            return ArduinoReadiness(
                selected,
                ports,
                port_available,
                lease_status,
                lease_available,
                str(owner) if owner else None,
                int(owner_pid) if isinstance(owner_pid, int) else None,
                str(raw.get("reason") or "lease status did not provide a reason"),
                captured_at=captured_at,
            )
        except Exception as error:
            return ArduinoReadiness(
                selected,
                ports,
                port_available,
                "UNKNOWN",
                None,
                None,
                None,
                "; ".join(value for value in (port_error, _error_text(error)) if value),
                captured_at=captured_at,
            )

    def enable_overlay(
        self,
        publisher: EngineFramePublisher,
        *,
        show_rejected: bool = False,
        timeout_seconds: float = 3.0,
        presentation_provider: Callable[[], object] | None = None,
        bound_run_id: str | None = None,
    ) -> OverlaySnapshot:
        return self._overlay_owner.enable(
            publisher,
            show_rejected=show_rejected,
            timeout_seconds=timeout_seconds,
            presentation_provider=presentation_provider,
            bound_run_id=bound_run_id,
        )

    def disable_overlay(self, *, timeout_seconds: float = 3.0) -> OverlaySnapshot:
        return self._overlay_owner.disable(timeout_seconds=timeout_seconds)

    def overlay_status(self) -> OverlaySnapshot:
        return self._overlay_owner.snapshot()

    def collect_diagnostics(self) -> OperatorDiagnostics:
        captured_at = self._aware_now()
        folder = self._diagnostic_folder(captured_at)
        errors: list[str] = []
        commit_result = self._run_process(
            ("git", "rev-parse", "HEAD"),
            timeout_seconds=10.0,
            log_path=folder / "git_commit.log",
        )
        commit = (
            _last_nonempty_line(commit_result.output_tail)
            if commit_result.passed
            else None
        )
        if not commit_result.passed:
            errors.append(f"git commit: {_concise_process_summary(commit_result)}")
        dirty_result = self._run_process(
            ("git", "status", "--porcelain", "--untracked-files=normal"),
            timeout_seconds=10.0,
            log_path=folder / "git_status.log",
        )
        dirty = None if not dirty_result.passed else bool(_command_output(dirty_result).strip())
        if not dirty_result.passed:
            errors.append(f"git status: {_concise_process_summary(dirty_result)}")
        java_result = self._run_process(
            ("java", "-version"),
            timeout_seconds=10.0,
            log_path=folder / "java_version.log",
        )
        java_version = _version_line(java_result.output_tail, "version") if java_result.passed else None
        gradle_wrapper = self._repo_root / "gradlew.bat"
        if gradle_wrapper.is_file():
            gradle_result = self._run_process(
                _cmd_wrapper(gradle_wrapper, "--version", "--no-daemon"),
                timeout_seconds=30.0,
                log_path=folder / "gradle_version.log",
            )
        else:
            gradle_result = ProcessResult(
                ProcessStatus.NOT_RUN,
                (),
                None,
                0.0,
                "Gradle wrapper is missing",
                folder / "gradle_version.log",
            )
        gradle_version = _version_line(gradle_result.output_tail, "Gradle") if gradle_result.passed else None
        try:
            profile_schema = str(profile_contract().get("schema") or "UNKNOWN")
        except Exception as error:
            profile_schema = "UNKNOWN"
            errors.append(f"profile schema: {_error_text(error)}")
        try:
            ports = tuple(
                sorted(
                    {str(value).strip() for value in self._serial_port_lister() if str(value).strip()},
                    key=str.casefold,
                )
            )
        except Exception as error:
            ports = ()
            errors.append(f"Arduino port enumeration: {_error_text(error)}")
        return OperatorDiagnostics(
            captured_at,
            commit,
            dirty,
            APPLICATION_SCHEMA,
            profile_schema,
            ENGINE_FRAME_SCHEMA,
            RESPONSE_SCHEMA,
            SENSOR_FRAME_SCHEMA,
            sys.version.splitlines()[0],
            java_result.passed,
            java_version,
            gradle_result.passed,
            gradle_version,
            _latest_artifact_directory(
                self._repo_root / "_run_proofs", recursive=True
            ),
            _latest_artifact_directory(
                self._repo_root / "demo_runs", recursive=False
            ),
            ports,
            tuple(errors),
        )

    def run_quick_self_test(self, *, timeout_seconds: float = 120.0) -> ProcessResult:
        timeout = _bounded_seconds(timeout_seconds, "timeout_seconds", 300.0)
        return self._run_process(
            (
                sys.executable,
                "-m",
                "unittest",
                "-q",
                "tests.test_application",
                "tests.test_engine_frame",
                "tests.test_gui_controller",
                "tests.test_operator_services",
            ),
            timeout_seconds=timeout,
            log_path=self._new_log_path("quick_self_test"),
        )

    def run_golden_replay(self, *, timeout_seconds: float = 120.0) -> ProcessResult:
        timeout = _bounded_seconds(timeout_seconds, "timeout_seconds", 300.0)
        return self._run_process(
            _cmd_wrapper(self._repo_root / "run.cmd", "replay"),
            timeout_seconds=timeout,
            log_path=self._new_log_path("golden_replay"),
        )

    def _run_process(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
        log_path: Path,
    ) -> ProcessResult:
        return self._process_runner(
            command,
            cwd=self._repo_root,
            timeout_seconds=timeout_seconds,
            log_path=log_path,
        )

    def _scan_windows(self) -> tuple[tuple[RuneLiteWindow, ...], str | None]:
        try:
            return tuple(self._window_scanner()), None
        except Exception as error:
            return (), _error_text(error)

    def _cursor_inside(self, bounds: ScreenBounds | None) -> bool | None:
        if bounds is None:
            return None
        try:
            point = self._cursor_reader()
        except Exception:
            return None
        return None if point is None else bounds.contains(point)

    def _aware_now(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime):
            raise TypeError("now() must return datetime")
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    def _diagnostic_folder(self, stamp: datetime) -> Path:
        folder = (
            self._repo_root
            / "_run_proofs"
            / "gui_diagnostics"
            / stamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        )
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _new_log_path(self, stem: str) -> Path:
        return self._diagnostic_folder(self._aware_now()) / f"{stem}.log"


def run_bounded_process(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    log_path: Path,
) -> ProcessResult:
    values = tuple(str(value) for value in command)
    if not values or any(not value for value in values):
        raise ValueError("command must contain non-empty arguments")
    timeout = _bounded_seconds(timeout_seconds, "timeout_seconds", 300.0)
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    status = ProcessStatus.ERROR
    exit_code: int | None = None
    try:
        with log_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"command: {subprocess.list2cmdline(list(values))}\n")
            handle.flush()
            process = subprocess.Popen(
                values,
                cwd=str(cwd),
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                ),
            )
            try:
                exit_code = process.wait(timeout=timeout)
                status = ProcessStatus.PASS if exit_code == 0 else ProcessStatus.FAIL
            except subprocess.TimeoutExpired:
                status = ProcessStatus.TIMEOUT
                _terminate_process_tree(process)
                exit_code = process.poll()
                handle.write(f"\nTIMEOUT after {timeout:g} seconds\n")
    except Exception as error:
        elapsed = max(0.0, time.monotonic() - started)
        try:
            with log_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"\nERROR: {_error_text(error)}\n")
        except OSError:
            pass
        return ProcessResult(
            ProcessStatus.ERROR,
            values,
            exit_code,
            elapsed,
            _read_tail(log_path),
            log_path,
        )
    return ProcessResult(
        status,
        values,
        exit_code,
        max(0.0, time.monotonic() - started),
        _read_tail(log_path),
        log_path,
    )


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ("taskkill", "/PID", str(process.pid), "/T", "/F"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    if process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=2.0)
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
                process.wait(timeout=2.0)
            except (OSError, subprocess.SubprocessError):
                pass


def _launch_plugin(repo_root: Path, log_path: Path) -> _LaunchHandle:
    wrapper = repo_root / "run.cmd"
    if not wrapper.is_file():
        raise FileNotFoundError(f"public wrapper not found: {wrapper}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = _cmd_wrapper(wrapper, "plugin")
    with log_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"command: {subprocess.list2cmdline(list(command))}\n")
        handle.write("output: suppressed to keep the long-lived client log bounded\n")
    return subprocess.Popen(
        command,
        cwd=str(repo_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        ),
    )


def _default_overlay_factory(
    publisher: EngineFramePublisher,
    show_rejected: bool,
    presentation_provider: Callable[[], object] | None,
    bound_run_id: str | None,
) -> _Overlay:
    from .debug_overlay import DebugOverlay

    return DebugOverlay(
        publisher,
        show_rejected=show_rejected,
        presentation_provider=presentation_provider,
        bound_run_id=bound_run_id,
    )


def _available_serial_ports() -> tuple[str, ...]:
    try:
        from serial.tools import list_ports

        return tuple(
            sorted(
                {str(port.device).strip() for port in list_ports.comports() if str(port.device).strip()},
                key=str.casefold,
            )
        )
    except (ImportError, OSError):
        return ()


def retained_layout_supported(observation: Observation) -> bool | None:
    """Describe whether telemetry matches the one physically proven GUI layout.

    This is presentation-only operator evidence.  It does not participate in
    SafetyGate, InputCoordinator, target selection, or action authorization.
    Missing geometry stays unknown; geometry that is present must match both
    retained 175% fixed-client dimensions exactly.
    """

    if not isinstance(observation, Observation):
        raise TypeError("observation must be Observation")
    canvas = observation.canvas_bounds
    client = observation.client_window_bounds
    if canvas is None or client is None:
        return None
    return (
        (canvas.width, canvas.height) == RETAINED_LAYOUT_CANVAS_SIZE
        and (client.width, client.height) == RETAINED_LAYOUT_CLIENT_SIZE
    )


def _process_is_running(pid: int) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # type: ignore[attr-defined]
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_cursor() -> ScreenPoint | None:
    if os.name != "nt":
        return None

    class _Point(ctypes.Structure):
        _fields_ = (("x", ctypes.c_long), ("y", ctypes.c_long))

    point = _Point()
    try:
        ok = ctypes.windll.user32.GetCursorPos(ctypes.byref(point))  # type: ignore[attr-defined]
    except Exception:
        return None
    return ScreenPoint(int(point.x), int(point.y)) if ok else None


def _discover_runelite_windows(
    window_finder: Callable[[int], RuneLiteWindow],
) -> tuple[RuneLiteWindow, ...]:
    if os.name != "nt":
        return ()
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    pids: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    def visit(hwnd: int, _value: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0 or length > 512:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if "runelite" not in buffer.value.casefold():
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value > 0 and int(pid.value) not in pids:
            pids.append(int(pid.value))
        return len(pids) < 16

    user32.EnumWindows(callback_type(visit), 0)
    windows: list[RuneLiteWindow] = []
    for pid in pids:
        try:
            window = window_finder(pid)
        except Exception:
            continue
        if window not in windows:
            windows.append(window)
    return tuple(windows)


def _latest_artifact_directory(root: Path, *, recursive: bool) -> Path | None:
    if not root.is_dir():
        return None
    if not recursive:
        newest_direct: tuple[int, Path] | None = None
        try:
            candidates = tuple(
                child
                for child in root.iterdir()
                if child.is_dir() and not child.is_symlink()
            )
        except OSError:
            return None
        for candidate in candidates[:MAX_ARTIFACT_DIRECTORIES]:
            try:
                children = tuple(candidate.iterdir())
                files = tuple(
                    child
                    for child in children
                    if child.is_file() and not child.is_symlink()
                )
                stamp = max(
                    (child.stat().st_mtime_ns for child in files),
                    default=candidate.stat().st_mtime_ns,
                )
            except OSError:
                continue
            if newest_direct is None or stamp > newest_direct[0]:
                newest_direct = (stamp, candidate)
        return None if newest_direct is None else newest_direct[1]
    newest: tuple[int, Path] | None = None
    pending = [root]
    visited = 0
    while pending and visited < MAX_ARTIFACT_DIRECTORIES:
        current = pending.pop()
        try:
            children = tuple(current.iterdir())
        except OSError:
            continue
        visited += 1
        files = [child for child in children if child.is_file() and not child.is_symlink()]
        if files:
            try:
                stamp = max(child.stat().st_mtime_ns for child in files)
            except OSError:
                stamp = 0
            if newest is None or stamp > newest[0]:
                newest = (stamp, current)
        pending.extend(
            child
            for child in children
            if child.is_dir() and not child.is_symlink()
        )
    return None if newest is None else newest[1]


def _cmd_wrapper(wrapper: Path, *arguments: str) -> tuple[str, ...]:
    if os.name == "nt":
        return ("cmd.exe", "/d", "/c", str(wrapper), *arguments)
    return (str(wrapper), *arguments)


def _bounded_seconds(value: object, name: str, maximum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 < float(value) <= maximum
    ):
        raise ValueError(f"{name} must be in the range (0, {maximum:g}]")
    return float(value)


def _bounds_dict(bounds: ScreenBounds | None) -> dict[str, int] | None:
    if bounds is None:
        return None
    return {
        "x": bounds.x,
        "y": bounds.y,
        "width": bounds.width,
        "height": bounds.height,
    }


def _error_text(error: BaseException) -> str:
    value = f"{type(error).__name__}: {error}".strip()
    return value[:MAX_OPERATOR_ERROR_LENGTH]


def _read_tail(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - MAX_PROCESS_OUTPUT_TAIL_BYTES))
            raw = handle.read(MAX_PROCESS_OUTPUT_TAIL_BYTES)
    except OSError:
        return ""
    return raw.decode("utf-8", errors="replace").strip()


def _command_output(result: ProcessResult) -> str:
    lines = result.output_tail.splitlines()
    return "\n".join(
        line for line in lines if not line.casefold().startswith("command:")
    ).strip()


def _last_nonempty_line(value: str) -> str | None:
    lines = [line.strip() for line in _command_output_text(value).splitlines() if line.strip()]
    return lines[-1] if lines else None


def _command_output_text(value: str) -> str:
    return "\n".join(
        line for line in value.splitlines() if not line.casefold().startswith("command:")
    ).strip()


def _version_line(value: str, marker: str) -> str | None:
    lines = [line.strip() for line in _command_output_text(value).splitlines() if line.strip()]
    for line in lines:
        if marker.casefold() in line.casefold():
            return line
    return lines[0] if lines else None


def _concise_process_summary(result: ProcessResult) -> str:
    last = _last_nonempty_line(result.output_tail)
    suffix = f": {last}" if last else ""
    return f"{result.status.value} (exit={result.exit_code}){suffix}"[:1_000]

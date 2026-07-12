from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from .application import LifecycleState
from .engine_frame import CleanupEvidence, EngineFrame

if TYPE_CHECKING:
    from .operator_services import ConnectionSnapshot


class PresentationState(str, Enum):
    """Frontend-only classification; it never controls engine behavior."""

    CONNECTING = "CONNECTING"
    READY = "READY"
    OBSERVING = "OBSERVING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"
    SAFE_STOPPED = "SAFE_STOPPED"
    DISCONNECTED = "DISCONNECTED"
    STALE = "STALE"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class FrontendPresentation:
    """Immutable presentation derived from existing application facts."""

    state: PresentationState
    lifecycle: LifecycleState
    run_id: str | None
    frame_run_id: str | None
    expected_process_id: int | None
    expected_session_id: str | None
    execute_requested: bool
    runtime_status: str | None
    runtime_reason: str | None
    source_frame_sequence: int | None
    source_frame_stage: str | None
    source_frame_status: str | None
    source_tick: int | None
    connection_source_tick: int | None
    connection_last_updated_at: datetime | None
    connection_age_seconds: float | None
    source_process_id: int | None
    source_session_id: str | None
    process_id: int | None
    session_id: str | None
    last_updated_at: datetime | None
    age_seconds: float | None
    max_source_age_millis: int | None
    current: bool
    historical: bool
    terminal_summary: bool
    geometry_allowed: bool
    start_live_allowed: bool
    blockers: tuple[str, ...]
    source_blocker: str | None
    connection_blocker: str | None
    diagnostic: str | None
    terminal_reason: str | None
    terminal_outcome: object | None
    cleanup: CleanupEvidence | None
    reconnect_guidance: str | None


_ACTIVE_LIFECYCLES = {
    LifecycleState.STARTING,
    LifecycleState.RUNNING,
    LifecycleState.PAUSE_REQUESTED,
    LifecycleState.PAUSED,
    LifecycleState.SAFE_STOP_REQUESTED,
}
_TERMINAL_STATES = {
    PresentationState.COMPLETE,
    PresentationState.BLOCKED,
    PresentationState.SAFE_STOPPED,
}


def classify_frontend_presentation(
    *,
    lifecycle: LifecycleState,
    run_id: str | None,
    frame_run_id: str | None,
    expected_process_id: int | None = None,
    expected_session_id: str | None = None,
    execute_requested: bool,
    frame: EngineFrame | None,
    connection: ConnectionSnapshot | None,
    runtime_status: str | None = None,
    runtime_reason: str | None = None,
    blockers: tuple[str, ...] = (),
    now: datetime,
) -> FrontendPresentation:
    """Classify display state without adding lifecycle or control authority.

    ``frame_run_id`` is the existing run association held by the frontend.  It
    deliberately reuses the application run ID rather than introducing another
    token.  ``now`` is injected so wall-clock aging is deterministic in tests.
    """

    if not isinstance(lifecycle, LifecycleState):
        raise TypeError("lifecycle must be LifecycleState")
    if not isinstance(execute_requested, bool):
        raise TypeError("execute_requested must be bool")
    current_time = _aware_utc(now, "now")

    observation = frame.observation if frame is not None else None
    frame_updated_at = (
        _aware_utc(observation.captured_at, "frame source timestamp")
        if observation is not None
        else None
    )
    connection_updated_at = (
        getattr(connection, "source_captured_at", None)
        if connection is not None
        else None
    )
    connection_updated_at = (
        _aware_utc(connection_updated_at, "connection source timestamp")
        if connection_updated_at is not None
        else None
    )
    source_updated_at = frame_updated_at or connection_updated_at
    age_seconds = (
        max(0.0, (current_time - source_updated_at).total_seconds())
        if source_updated_at is not None
        else None
    )
    frame_age_seconds = (
        max(0.0, (current_time - frame_updated_at).total_seconds())
        if frame_updated_at is not None
        else None
    )
    connection_age_seconds = (
        max(0.0, (current_time - connection_updated_at).total_seconds())
        if connection_updated_at is not None
        else None
    )
    frame_max_age_millis = _max_source_age_millis(observation)
    connection_max_age_millis = _max_source_age_millis(connection)
    available_limits = tuple(
        value
        for value in (frame_max_age_millis, connection_max_age_millis)
        if value is not None
    )
    max_source_age_millis = min(available_limits) if available_limits else None
    frame_age_within_contract = _age_within_contract(
        frame_age_seconds, frame_max_age_millis
    )
    connection_age_within_contract = _age_within_contract(
        connection_age_seconds, connection_max_age_millis
    )

    process_id = getattr(connection, "process_id", None)
    session_id = getattr(connection, "session_id", None)
    source_process_id = observation.process_id if observation is not None else None
    source_session_id = observation.session_id if observation is not None else None
    connection_present = connection is not None
    connection_structurally_live = bool(
        connection_present
        and connection.endpoint_healthy
        and connection.runelite_found
        and connection.exact_process_binding
        and process_id is not None
        and session_id is not None
    )
    connection_loaded = bool(connection_present and connection.loaded_scene)
    connection_contract_fresh = bool(
        connection_structurally_live
        and connection_loaded
        and connection.coherent_fresh_observation
        and connection_age_within_contract
    )
    frame_contract_fresh = bool(
        observation is not None
        and observation.loaded_scene
        and observation.fresh
        and observation.cache_wall_clock_fresh
        and observation.source_coherent
        and frame_age_within_contract
    )
    identity_matches = bool(
        observation is None
        or (
            process_id is not None
            and session_id is not None
            and source_process_id == process_id
            and source_session_id == session_id
        )
    )
    expected_identity_matches = bool(
        (expected_process_id is None and expected_session_id is None)
        or (
            expected_process_id is not None
            and expected_session_id is not None
            and process_id == expected_process_id
            and session_id == expected_session_id
            and (
                observation is None
                or (
                    source_process_id == expected_process_id
                    and source_session_id == expected_session_id
                )
            )
        )
    )
    frame_run_matches = bool(
        frame is not None
        and frame_run_id is not None
        and run_id is not None
        and frame_run_id == run_id
    )

    source_blocker = frame.blocker if frame is not None else None
    task_blocker = frame.task.blocker if frame is not None else None
    connection_blocker = getattr(connection, "blocker", None)
    diagnostic = getattr(connection, "diagnostic", None)
    all_blockers = _dedupe_text(
        (*blockers, source_blocker, task_blocker, connection_blocker)
    )

    normalized_runtime = _normalized_status(runtime_status)
    source_status = (
        frame.task.status.value if frame is not None else None
    )
    normalized_source = _normalized_status(source_status)
    terminal_state = _terminal_state(
        lifecycle, normalized_runtime, normalized_source
    )
    error_terminal = bool(
        lifecycle is LifecycleState.ERROR or normalized_runtime == "ERROR"
    )
    terminal_reason = (
        runtime_reason or source_blocker or task_blocker
        if terminal_state is not None or error_terminal
        else None
    )
    terminal_outcome = (
        frame.last_verification.outcome
        if terminal_state is not None
        and frame is not None
        and frame.last_verification is not None
        else None
    )
    cleanup = frame.cleanup if frame is not None else None

    disconnected = bool(
        (connection_present and not connection_structurally_live)
        or (not connection_present and frame is not None)
        or (
            observation is not None
            and not identity_matches
            and lifecycle in _ACTIVE_LIFECYCLES
        )
        or (lifecycle in _ACTIVE_LIFECYCLES and not expected_identity_matches)
        or (
            (lifecycle in _ACTIVE_LIFECYCLES or frame is not None)
            and connection_present
            and not connection_loaded
        )
    )
    run_mismatch = bool(frame is not None and not frame_run_matches)
    historical_identity = bool(
        observation is not None
        and not identity_matches
        and lifecycle not in _ACTIVE_LIFECYCLES
    )
    stale = bool(
        (connection_structurally_live and connection_loaded and not connection_contract_fresh)
        or (observation is not None and not frame_contract_fresh)
    )

    recoverable_terminal_error = bool(
        error_terminal
        and lifecycle not in _ACTIVE_LIFECYCLES
        and connection_contract_fresh
    )

    if disconnected:
        state = PresentationState.DISCONNECTED
    elif run_mismatch:
        state = PresentationState.STALE
    elif (
        historical_identity and connection_contract_fresh
    ) or recoverable_terminal_error:
        state = PresentationState.READY
    elif error_terminal:
        state = PresentationState.ERROR
    elif terminal_state is not None and not historical_identity:
        state = terminal_state
    elif stale:
        state = PresentationState.STALE
    elif lifecycle is LifecycleState.STARTING:
        state = PresentationState.CONNECTING
    elif lifecycle in {LifecycleState.PAUSED, LifecycleState.PAUSE_REQUESTED}:
        state = PresentationState.PAUSED
    elif lifecycle in {LifecycleState.RUNNING, LifecycleState.SAFE_STOP_REQUESTED}:
        state = (
            PresentationState.RUNNING
            if execute_requested
            else PresentationState.OBSERVING
        )
    elif connection_contract_fresh:
        state = PresentationState.READY
    elif all_blockers and lifecycle is LifecycleState.BLOCKED:
        state = PresentationState.BLOCKED
    else:
        state = PresentationState.CONNECTING

    terminal_summary = terminal_state is not None or error_terminal
    current = bool(
        frame is not None
        and frame_run_matches
        and identity_matches
        and frame_contract_fresh
        and connection_contract_fresh
        and state
        in {
            PresentationState.OBSERVING,
            PresentationState.RUNNING,
            PresentationState.PAUSED,
        }
    )
    historical = bool(frame is not None and not current)
    geometry_allowed = bool(
        current
        and state in {PresentationState.OBSERVING, PresentationState.RUNNING}
    )
    active_run = lifecycle in _ACTIVE_LIFECYCLES
    start_live_allowed = bool(
        connection_contract_fresh
        and not active_run
        and state
        not in {
            PresentationState.CONNECTING,
            PresentationState.DISCONNECTED,
            PresentationState.STALE,
            PresentationState.ERROR,
        }
    )

    reconnect_guidance = _reconnect_guidance(state)
    return FrontendPresentation(
        state=state,
        lifecycle=lifecycle,
        run_id=run_id,
        frame_run_id=frame_run_id,
        expected_process_id=expected_process_id,
        expected_session_id=expected_session_id,
        execute_requested=execute_requested,
        runtime_status=runtime_status,
        runtime_reason=runtime_reason,
        source_frame_sequence=frame.sequence if frame is not None else None,
        source_frame_stage=frame.stage.value if frame is not None else None,
        source_frame_status=source_status,
        source_tick=(
            observation.source_tick
            if observation is not None
            else getattr(connection, "source_tick", None)
        ),
        connection_source_tick=getattr(connection, "source_tick", None),
        connection_last_updated_at=connection_updated_at,
        connection_age_seconds=connection_age_seconds,
        source_process_id=source_process_id,
        source_session_id=source_session_id,
        process_id=process_id,
        session_id=session_id,
        last_updated_at=source_updated_at,
        age_seconds=age_seconds,
        max_source_age_millis=max_source_age_millis,
        current=current,
        historical=historical,
        terminal_summary=terminal_summary,
        geometry_allowed=geometry_allowed,
        start_live_allowed=start_live_allowed,
        blockers=all_blockers,
        source_blocker=source_blocker,
        connection_blocker=connection_blocker,
        diagnostic=diagnostic,
        terminal_reason=terminal_reason,
        terminal_outcome=terminal_outcome,
        cleanup=cleanup,
        reconnect_guidance=reconnect_guidance,
    )


def _terminal_state(
    lifecycle: LifecycleState,
    runtime_status: str | None,
    source_status: str | None,
) -> PresentationState | None:
    if runtime_status == "SAFE_STOPPED" or lifecycle is LifecycleState.STOPPED:
        return PresentationState.SAFE_STOPPED
    if (
        runtime_status in {"COMPLETE", "DRY_RUN"}
        or source_status == "COMPLETE"
        or lifecycle is LifecycleState.COMPLETE
    ):
        return PresentationState.COMPLETE
    if (
        runtime_status in {"BLOCKED", "LIMIT"}
        or source_status == "BLOCKED"
        or lifecycle is LifecycleState.BLOCKED
    ):
        return PresentationState.BLOCKED
    return None


def _max_source_age_millis(value: object) -> int | None:
    raw = getattr(value, "max_source_age_millis", None)
    if raw is None:
        return None
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        return None
    return raw


def _age_within_contract(
    age_seconds: float | None,
    max_source_age_millis: int | None,
) -> bool:
    return bool(
        age_seconds is not None
        and (
            max_source_age_millis is None
            or age_seconds * 1_000.0 <= max_source_age_millis
        )
    )


def _normalized_status(value: object) -> str | None:
    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip()
    return text.upper() if text else None


def _aware_utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _dedupe_text(values: tuple[Any, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        if text not in result:
            result.append(text)
    return tuple(result)


def _reconnect_guidance(state: PresentationState) -> str | None:
    if state is PresentationState.DISCONNECTED:
        return (
            "Launch or reconnect RuneLite, then wait for one fresh coherent "
            "loaded Observation bound to the current PID/session."
        )
    if state is PresentationState.STALE:
        return (
            "Refresh the RuneLite connection and wait for a fresh coherent "
            "loaded Observation before starting live."
        )
    if state is PresentationState.CONNECTING:
        return "Wait for an exact RuneLite PID/session and a fresh loaded Observation."
    if state is PresentationState.ERROR:
        return "Review the retained diagnostic, then reconnect or restart explicitly."
    return None

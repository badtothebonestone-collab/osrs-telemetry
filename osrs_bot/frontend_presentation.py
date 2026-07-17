from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from .application import LifecycleState
from .engine_frame import CleanupEvidence, EngineFrame
from .observability import MAX_DURATION_MILLIS, WaitState

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
    WAITING_FOR_NEXT_SCENE_UPDATE = WaitState.WAITING_FOR_NEXT_SCENE_UPDATE.value
    WAITING_FOR_SOURCE_COHERENCE = WaitState.WAITING_FOR_SOURCE_COHERENCE.value
    ENDPOINT_BACKPRESSURE = WaitState.ENDPOINT_BACKPRESSURE.value
    INPUT_TRANSACTION_BUSY = WaitState.INPUT_TRANSACTION_BUSY.value
    CURSOR_FEEDBACK_SETTLING = WaitState.CURSOR_FEEDBACK_SETTLING.value
    ARDUINO_HEALTH_STALE = WaitState.ARDUINO_HEALTH_STALE.value
    ARDUINO_COMMAND_FAILED = WaitState.ARDUINO_COMMAND_FAILED.value
    SENSOR_STALE = WaitState.SENSOR_STALE.value
    PRESENTATION_FRAME_STALE = WaitState.PRESENTATION_FRAME_STALE.value
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
    wait_elapsed_millis: int | None = None
    display_state: PresentationState | None = None


PRESENTATION_HYSTERESIS_MILLIS = 500
_DEBOUNCED_DISPLAY_STATES = frozenset(
    {
        PresentationState.ARDUINO_HEALTH_STALE,
        PresentationState.SENSOR_STALE,
        PresentationState.PRESENTATION_FRAME_STALE,
    }
)


@dataclass(frozen=True, slots=True)
class PresentationHysteresisState:
    """Immutable GUI-only debounce memory with no engine authority."""

    run_id: str | None = None
    displayed_state: PresentationState | None = None
    pending_state: PresentationState | None = None
    pending_since_monotonic: float | None = None


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
_CURRENT_SOURCE_WAIT_STATES = {
    PresentationState.ENDPOINT_BACKPRESSURE,
    PresentationState.INPUT_TRANSACTION_BUSY,
    PresentationState.CURSOR_FEEDBACK_SETTLING,
    PresentationState.ARDUINO_HEALTH_STALE,
    PresentationState.ARDUINO_COMMAND_FAILED,
}
_ARDUINO_HEALTH_PRESENTABLE_STATES = {
    PresentationState.CONNECTING,
    PresentationState.READY,
    PresentationState.OBSERVING,
    PresentationState.RUNNING,
    PresentationState.PAUSED,
}


def apply_arduino_health_age(
    presentation: FrontendPresentation,
    readiness: object,
    *,
    now: datetime,
) -> FrontendPresentation:
    """Apply passive Arduino age from explicit timestamped readiness only.

    This never changes a safety/start/geometry gate and never classifies from a
    reason string.  Legacy readiness values without ``captured_at`` remain
    readable and cannot be labeled stale without evidence.
    """

    if not isinstance(presentation, FrontendPresentation):
        raise TypeError("presentation must be FrontendPresentation")
    current_time = _aware_utc(now, "Arduino presentation time")
    captured_at = getattr(readiness, "captured_at", None)
    max_age_millis = getattr(readiness, "max_age_millis", None)
    if captured_at is None or max_age_millis is None:
        return presentation
    if not isinstance(captured_at, datetime):
        return presentation
    if (
        not isinstance(max_age_millis, int)
        or isinstance(max_age_millis, bool)
        or max_age_millis < 0
    ):
        return presentation
    captured_time = _aware_utc(captured_at, "Arduino readiness timestamp")
    age_millis = min(
        MAX_DURATION_MILLIS,
        int(max(0.0, (current_time - captured_time).total_seconds()) * 1_000),
    )
    if (
        age_millis <= max_age_millis
        or presentation.state not in _ARDUINO_HEALTH_PRESENTABLE_STATES
    ):
        return presentation
    return replace(
        presentation,
        state=PresentationState.ARDUINO_HEALTH_STALE,
        display_state=PresentationState.ARDUINO_HEALTH_STALE,
        wait_elapsed_millis=age_millis,
        reconnect_guidance=_reconnect_guidance(
            PresentationState.ARDUINO_HEALTH_STALE
        ),
    )


def apply_presentation_hysteresis(
    presentation: FrontendPresentation,
    history: PresentationHysteresisState,
    *,
    now_monotonic: float,
) -> tuple[FrontendPresentation, PresentationHysteresisState]:
    """Debounce only passive display faults while preserving exact state.

    ``presentation.state`` and every gate remain untouched.  Only
    ``display_state`` may retain the preceding display classification for a
    fixed 500 ms.  Command/ACK failures and expected wait states bypass the
    delay.  A run-ID change drops all prior display memory immediately.
    """

    if not isinstance(presentation, FrontendPresentation):
        raise TypeError("presentation must be FrontendPresentation")
    if not isinstance(history, PresentationHysteresisState):
        raise TypeError("history must be PresentationHysteresisState")
    if (
        isinstance(now_monotonic, bool)
        or not isinstance(now_monotonic, (int, float))
        or not math.isfinite(now_monotonic)
        or now_monotonic < 0
    ):
        raise ValueError("now_monotonic must be a finite non-negative number")
    exact_state = presentation.state
    now_value = float(now_monotonic)

    if history.run_id != presentation.run_id:
        updated = PresentationHysteresisState(
            run_id=presentation.run_id,
            displayed_state=exact_state,
        )
        return replace(presentation, display_state=exact_state), updated

    if exact_state not in _DEBOUNCED_DISPLAY_STATES:
        updated = PresentationHysteresisState(
            run_id=presentation.run_id,
            displayed_state=exact_state,
        )
        return replace(presentation, display_state=exact_state), updated

    if history.displayed_state in _DEBOUNCED_DISPLAY_STATES:
        updated = PresentationHysteresisState(
            run_id=presentation.run_id,
            displayed_state=exact_state,
        )
        return replace(presentation, display_state=exact_state), updated

    pending_since = history.pending_since_monotonic
    if pending_since is None:
        pending_since = now_value
    elapsed_millis = max(0.0, now_value - pending_since) * 1_000.0
    if elapsed_millis >= PRESENTATION_HYSTERESIS_MILLIS:
        updated = PresentationHysteresisState(
            run_id=presentation.run_id,
            displayed_state=exact_state,
        )
        return replace(presentation, display_state=exact_state), updated

    displayed_state = history.displayed_state or exact_state
    updated = PresentationHysteresisState(
        run_id=presentation.run_id,
        displayed_state=displayed_state,
        pending_state=exact_state,
        pending_since_monotonic=pending_since,
    )
    return replace(presentation, display_state=displayed_state), updated


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
    observability = getattr(frame, "observability", None)
    active_wait_state = getattr(observability, "wait_state", None)
    if not isinstance(active_wait_state, WaitState):
        active_wait_state = None
    wait_elapsed_millis = (
        getattr(observability, "wait_elapsed_millis", None)
        if active_wait_state is not None
        else None
    )
    if (
        not isinstance(wait_elapsed_millis, int)
        or isinstance(wait_elapsed_millis, bool)
        or wait_elapsed_millis < 0
    ):
        wait_elapsed_millis = None
    receipt = frame.last_execution_receipt if frame is not None else None
    arduino_command_failed = bool(
        active_wait_state is WaitState.ARDUINO_COMMAND_FAILED
        or _receipt_has_command_failure(receipt)
    )

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
    connection_sensor_stale = bool(
        connection_structurally_live
        and connection_loaded
        and not connection_age_within_contract
    )
    source_coherence_wait = bool(
        connection_structurally_live
        and connection_loaded
        and connection_age_within_contract
        and not connection_contract_fresh
    )
    presentation_frame_stale = bool(
        observation is not None
        and connection_contract_fresh
        and not frame_contract_fresh
    )

    recoverable_terminal_error = bool(
        error_terminal
        and lifecycle not in _ACTIVE_LIFECYCLES
        and connection_contract_fresh
    )

    if arduino_command_failed:
        state = PresentationState.ARDUINO_COMMAND_FAILED
    elif disconnected:
        state = PresentationState.DISCONNECTED
    elif run_mismatch:
        state = PresentationState.PRESENTATION_FRAME_STALE
    elif (
        historical_identity and connection_contract_fresh
    ) or recoverable_terminal_error:
        state = PresentationState.READY
    elif error_terminal:
        state = PresentationState.ERROR
    elif terminal_state is not None and not historical_identity:
        state = terminal_state
    elif active_wait_state is not None:
        state = PresentationState[active_wait_state.name]
    elif connection_sensor_stale:
        state = PresentationState.SENSOR_STALE
    elif source_coherence_wait:
        state = PresentationState.WAITING_FOR_SOURCE_COHERENCE
    elif presentation_frame_stale:
        state = PresentationState.PRESENTATION_FRAME_STALE
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
            *_CURRENT_SOURCE_WAIT_STATES,
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
            PresentationState.ERROR,
            PresentationState.WAITING_FOR_NEXT_SCENE_UPDATE,
            PresentationState.WAITING_FOR_SOURCE_COHERENCE,
            PresentationState.ENDPOINT_BACKPRESSURE,
            PresentationState.INPUT_TRANSACTION_BUSY,
            PresentationState.CURSOR_FEEDBACK_SETTLING,
            PresentationState.ARDUINO_HEALTH_STALE,
            PresentationState.ARDUINO_COMMAND_FAILED,
            PresentationState.SENSOR_STALE,
            PresentationState.PRESENTATION_FRAME_STALE,
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
        wait_elapsed_millis=wait_elapsed_millis,
        display_state=state,
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


def _receipt_has_command_failure(receipt: object) -> bool:
    """Use typed receipt/ledger facts only; never classify from message text."""

    if receipt is None:
        return False
    receipt_observability = getattr(receipt, "observability", None)
    if (
        getattr(receipt_observability, "wait_state", None)
        is WaitState.ARDUINO_COMMAND_FAILED
    ):
        return True
    for field_name in ("failed_command_count", "ack_missing_count"):
        value = getattr(receipt, field_name, 0)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return True
    commands = getattr(receipt, "commands", ())
    if not isinstance(commands, tuple):
        return False
    return any(
        getattr(command, "status", None)
        in {
            "REJECTED",
            "UNEXPECTED_RESPONSE",
            "WRITE_FAIL",
            "ACK_TIMEOUT_OR_READ_FAIL",
        }
        for command in commands
    )


def _reconnect_guidance(state: PresentationState) -> str | None:
    if state is PresentationState.DISCONNECTED:
        return (
            "Launch or reconnect RuneLite, then wait for one fresh coherent "
            "loaded Observation bound to the current PID/session."
        )
    if state is PresentationState.WAITING_FOR_NEXT_SCENE_UPDATE:
        return "Wait for the next fresh loaded-scene update from the bound source."
    if state is PresentationState.WAITING_FOR_SOURCE_COHERENCE:
        return "Wait for the bound telemetry sources to become coherent."
    if state is PresentationState.ENDPOINT_BACKPRESSURE:
        return "Wait for the bounded telemetry endpoint retry to complete."
    if state is PresentationState.INPUT_TRANSACTION_BUSY:
        return "The existing input transaction is still in progress."
    if state is PresentationState.CURSOR_FEEDBACK_SETTLING:
        return "Wait for bounded cursor feedback settlement to finish."
    if state is PresentationState.ARDUINO_HEALTH_STALE:
        return "Refresh Arduino readiness; no command failure has been inferred."
    if state is PresentationState.ARDUINO_COMMAND_FAILED:
        return "Review the retained command ledger and acknowledgement failure."
    if state is PresentationState.SENSOR_STALE:
        return (
            "Wait for a fresh coherent loaded Observation from the bound sensor "
            "before starting live."
        )
    if state is PresentationState.PRESENTATION_FRAME_STALE:
        return "Wait for the GUI to receive a current EngineFrame for this run."
    if state is PresentationState.CONNECTING:
        return "Wait for an exact RuneLite PID/session and a fresh loaded Observation."
    if state is PresentationState.ERROR:
        return "Review the retained diagnostic, then reconnect or restart explicitly."
    return None

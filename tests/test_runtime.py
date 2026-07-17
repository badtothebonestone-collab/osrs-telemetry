from __future__ import annotations

import ast
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from osrs_bot.action import ExecutionResult, UnsentActionDisposition
from osrs_bot.configuration import DEFAULT_RUNTIME_CONFIG, RuntimeConfig
from osrs_bot.engine_frame import EngineFramePublisher, EngineStage
from osrs_bot.input_coordinator import (
    CommandEvidence,
    CursorInvalidationCause,
    CursorReacquisitionEvidence,
    FirmwareSafetyStatus,
    InputFailureKind,
    InputReceipt,
    RuneLiteGeometryEvidence,
)
from osrs_bot.model import (
    Action,
    ActionKind,
    InventoryItem,
    InventoryObservation,
    MenuEntry,
    NearbyObject,
    Observation,
    PlayerObservation,
    SceneCensusEvidence,
    ScreenBounds,
    ScreenPoint,
    TargetGeometry,
    VerificationKind,
    VerificationSpec,
    WidgetObservation,
    WorldPoint,
)
from osrs_bot.observation import (
    ObservationBackpressureError,
    ObservationWorldModelHandoffError,
)
from osrs_bot.observability import TimingPhase, WaitState
from osrs_bot.runtime import (
    RuntimeControl,
    RuntimeControlState,
    TaskRuntime,
    _attach_camera_verification,
    _fetch_observation_request,
)
from osrs_bot.safety import SafetyGate
from osrs_bot.task_contract import (
    Decision,
    ObservationRequest,
    TaskSnapshot,
    TaskStatus,
    VerificationDisposition,
)
from osrs_bot.verification import (
    CameraUiState,
    CameraZoomResult,
    Outcome,
    OutcomeKind,
    VerificationFailureKind,
    VerificationResult,
    VerificationStatus,
    Verifier,
)


_CURSOR_CANVAS = ScreenBounds(100, 100, 765, 503)
_CURSOR_VIEWPORT = ScreenBounds(104, 104, 757, 495)
_CURSOR_CLIENT = ScreenBounds(88, 72, 789, 543)
_CURSOR_OUTER = ScreenBounds(80, 60, 805, 570)
_CURSOR_GEOMETRY = RuneLiteGeometryEvidence(
    expected_pid=1234,
    expected_hwnd=0x12345,
    outer_bounds=_CURSOR_OUTER,
    client_bounds=_CURSOR_CLIENT,
    canvas_bounds=_CURSOR_CANVAS,
)


def _command(sequence: int, name: str) -> CommandEvidence:
    return CommandEvidence(
        command_id=f"cmd-{sequence:08d}",
        sequence=sequence,
        command=name,
        status="PASS",
        write_ok=True,
        ack_received=True,
        accepted=True,
        response_token="OK",
        payload_token=name,
    )


def _successful_receipt() -> InputReceipt:
    commands = tuple(
        _command(sequence, name)
        for sequence, name in enumerate(
            ("ARM", "MOUSE_DOWN", "MOUSE_UP", "STOP_ALL", "DISARM", "STATUS"),
            start=1,
        )
    )
    return InputReceipt(
        transaction_id="input-00000001",
        mode="pointer",
        intent_ids=("runtime-test",),
        status="PASS",
        reason="input_transaction_succeeded",
        connected=True,
        arm_acknowledged=True,
        stop_all_acknowledged=True,
        disarm_acknowledged=True,
        firmware_status_acknowledged=True,
        firmware_status=FirmwareSafetyStatus(False, 0, 0),
        commands=commands,
        unresolved_command_count=0,
        failed_command_count=0,
        ack_missing_count=0,
        ledger_complete=True,
        ledger_closed=True,
        backend_closed=True,
    )


def _sent_execution(
    action: Action,
    pre_tick: int,
    post_tick: int | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        action=action,
        pre_move_tick=pre_tick,
        local_status="ERROR",
        local_reason="coordinator_receipt_unavailable",
        post_move_tick=post_tick,
        receipt=_successful_receipt(),
    )


def _blocked_execution(action: Action, tick: int, reason: str) -> ExecutionResult:
    return ExecutionResult(
        action=action,
        pre_move_tick=tick,
        local_status="BLOCKED",
        local_reason=reason,
    )


def _post_activation_error_execution(
    action: Action,
    tick: int,
    reason: str,
) -> ExecutionResult:
    receipt = replace(
        _successful_receipt(),
        status="ERROR",
        reason=reason,
        errors=(reason,),
    )
    return ExecutionResult(
        action=action,
        pre_move_tick=tick,
        local_status="ERROR",
        local_reason=reason,
        post_move_tick=tick + 1,
        receipt=receipt,
        activation_attempted=True,
    )


def _safe_unsent_execution(
    action: Action,
    tick: int,
    reason: str,
    *,
    disposition: UnsentActionDisposition = (
        UnsentActionDisposition.TARGET_EVIDENCE_INVALIDATED
    ),
    activation_commands: bool = False,
    complete_cleanup: bool = True,
    receipt_failure_kind: InputFailureKind | None = None,
    disconnected_preflight: bool = False,
    cursor_cause: CursorInvalidationCause | None = None,
    pointer_geometry: RuneLiteGeometryEvidence | None = None,
    cursor_reacquisition: CursorReacquisitionEvidence | None = None,
) -> ExecutionResult:
    command_names = () if disconnected_preflight else (
        ("ARM", "MOVE", "MOUSE_DOWN", "MOUSE_UP", "STOP_ALL", "DISARM", "STATUS")
        if activation_commands
        else ("ARM", "MOVE", "STOP_ALL", "DISARM", "STATUS")
    )
    commands = tuple(
        _command(sequence, name)
        for sequence, name in enumerate(command_names, start=1)
    )
    cursor_invalidation = (
        disposition is UnsentActionDisposition.CURSOR_STATE_INVALIDATED
    )
    effective_failure_kind = (
        receipt_failure_kind
        if receipt_failure_kind is not None
        else (
            InputFailureKind.CURSOR_STATE_INVALIDATED
            if cursor_invalidation
            else InputFailureKind.NONE
        )
    )
    if (
        cursor_invalidation
        and effective_failure_kind is InputFailureKind.CURSOR_STATE_INVALIDATED
        and cursor_cause is None
    ):
        cursor_cause = CursorInvalidationCause.UNEXPECTED_DIRECTION
    if cursor_invalidation and pointer_geometry is None:
        pointer_geometry = _CURSOR_GEOMETRY
    receipt = InputReceipt(
        transaction_id="input-00000002",
        mode="adaptive_pointer",
        intent_ids=("runtime-unsent-test",),
        status="BLOCKED",
        reason=reason,
        connected=not disconnected_preflight,
        arm_acknowledged=not disconnected_preflight,
        stop_all_acknowledged=(
            complete_cleanup and not disconnected_preflight
        ),
        disarm_acknowledged=not disconnected_preflight,
        firmware_status_acknowledged=not disconnected_preflight,
        firmware_status=(
            None
            if disconnected_preflight
            else FirmwareSafetyStatus(False, 0, 0)
        ),
        commands=commands,
        unresolved_command_count=0,
        failed_command_count=0,
        ack_missing_count=0,
        ledger_complete=True,
        ledger_closed=True,
        backend_closed=True,
        failure_kind=effective_failure_kind,
        cursor_invalidation_cause=(
            cursor_cause
            if effective_failure_kind
            is InputFailureKind.CURSOR_STATE_INVALIDATED
            else None
        ),
        pointer_geometry=(
            pointer_geometry if cursor_invalidation else None
        ),
        cursor_reacquisition=cursor_reacquisition,
        errors=(reason,),
    )
    return ExecutionResult(
        action=action,
        pre_move_tick=tick,
        local_status="BLOCKED",
        local_reason=reason,
        post_move_tick=tick + 1,
        receipt=receipt,
        unsent_disposition=disposition,
    )


def _completed_reacquisition_receipt(
    geometry: RuneLiteGeometryEvidence = _CURSOR_GEOMETRY,
    *,
    transaction_id: str = "input-cursor-recovery",
    complete_cleanup: bool = True,
) -> InputReceipt:
    neutral = ScreenBounds(
        geometry.canvas_bounds.x + 320,
        geometry.canvas_bounds.y + 210,
        120,
        80,
    )
    cursor_before = ScreenPoint(10, 10)
    cursor_after = ScreenPoint(neutral.x + 10, neutral.y + 10)
    evidence = CursorReacquisitionEvidence(
        coordinate_space="device_pixels_pm_v2",
        virtual_desktop_bounds=ScreenBounds(0, 0, 1920, 1080),
        neutral_bounds=neutral,
        cursor_before=cursor_before,
        before_geometry=geometry,
        cursor_after=cursor_after,
        after_geometry=geometry,
        completed=True,
        no_activation_sent=True,
    )
    commands = tuple(
        _command(sequence, name)
        for sequence, name in enumerate(
            ("ARM", "MOVE", "STOP_ALL", "DISARM", "STATUS"),
            start=1,
        )
    )
    return InputReceipt(
        transaction_id=transaction_id,
        mode="pointer",
        intent_ids=("cursor-recovery-test",),
        status="BLOCKED",
        reason="cursor_reacquired_reobserve_required",
        connected=True,
        arm_acknowledged=True,
        stop_all_acknowledged=complete_cleanup,
        disarm_acknowledged=complete_cleanup,
        firmware_status_acknowledged=complete_cleanup,
        firmware_status=FirmwareSafetyStatus(False, 0, 0),
        commands=commands,
        unresolved_command_count=0,
        failed_command_count=0,
        ack_missing_count=0,
        ledger_complete=True,
        ledger_closed=True,
        backend_closed=True,
        failure_kind=InputFailureKind.CURSOR_STATE_INVALIDATED,
        cursor_invalidation_cause=CursorInvalidationCause.CURSOR_REACQUIRED,
        pointer_geometry=geometry,
        cursor_reacquisition=evidence,
        errors=("cursor_reacquired_reobserve_required",),
    )


def _observation(tick: int) -> Observation:
    timestamp = datetime.now(timezone.utc)
    session_id = "runtime-session"
    process_id = 1234
    frame_id = f"test-frame-{tick}"
    return Observation(
        player=PlayerObservation(),
        location=WorldPoint(3192, 3244, 0),
        plane=0,
        inventory=InventoryObservation(known=True),
        nearby_objects=(),
        menus=(),
        widgets=WidgetObservation(),
        canvas_bounds=None,
        game_state="LOGGED_IN",
        timestamp=timestamp,
        tick=tick,
        status="PASS",
        fresh=True,
        cache_wall_clock_fresh=True,
        scene_playable=True,
        session_id=session_id,
        client_focused=True,
        client_process_id=process_id,
        assembled_at=timestamp,
        frame_id=frame_id,
        geometry_frame_id=frame_id,
        source_coherent=True,
        menu_fresh=True,
        menu_source_tick=tick,
        menu_timestamp=timestamp,
        menu_session_id=session_id,
        menu_process_id=process_id,
        scene_census=SceneCensusEvidence(
            metadata_present=True,
            complete=True,
            scene_coverage_complete=True,
            authoritative_absence_eligible=True,
            priority_absence_eligible=True,
        ),
    )


def _cursor_observation(tick: int, **changes: object) -> Observation:
    values: dict[str, object] = {
        "canvas_bounds": _CURSOR_CANVAS,
        "viewport_bounds": _CURSOR_VIEWPORT,
        "client_window_bounds": _CURSOR_CLIENT,
    }
    values.update(changes)
    return replace(_observation(tick), **values)


class _Client:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.requests: list[tuple[tuple[str, WorldPoint], ...]] = []
        self.priority_requests: list[tuple[int, ...]] = []

    def fetch(self, tiles, priority_object_ids=()):
        self.requests.append(tuple(tiles))
        self.priority_requests.append(tuple(priority_object_ids))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class LegacyObservationPlanFallbackTests(unittest.TestCase):
    def test_dropped_anchor_and_exact_key_revoke_census_authority(self) -> None:
        source = _observation(10)
        client = _Client(source)
        request = ObservationRequest(
            priority_object_ids=(1276,),
            priority_object_keys=("tree:exact",),
            center_world_location=WorldPoint(3200, 3200, 0),
            radius_tiles=4,
            max_objects=16,
            max_projection_objects=8,
            purpose="locked_target_verification",
        )

        result = _fetch_observation_request(client, request)

        self.assertIsNone(result.scene_census.complete)
        self.assertIsNone(result.scene_census.scene_coverage_complete)
        self.assertFalse(result.scene_census.authoritative_absence_eligible)
        self.assertFalse(result.scene_census.priority_absence_eligible)
        self.assertEqual((1276,), client.priority_requests[0])


class _Task:
    def __init__(
        self,
        decisions: list[Decision],
        *,
        projections: tuple[tuple[str, WorldPoint], ...] = (),
        priority_object_ids: tuple[int, ...] = (),
    ) -> None:
        self.decisions = list(decisions)
        self.projections = projections
        self.priority_object_ids = priority_object_ids
        self.applied: list[VerificationResult] = []
        self.discarded: list[str] = []
        self.discard_policies: list[bool] = []
        self.decide_calls = 0
        self.status = TaskStatus.RUNNING
        self.state = "ready"
        self.blocker: str | None = None

    def observation_request(self) -> ObservationRequest:
        return ObservationRequest(self.projections, self.priority_object_ids)

    def decide(self, _observation: Observation) -> Decision:
        self.decide_calls += 1
        decision = self.decisions.pop(0)
        self.state = decision.state
        if decision.state == "complete":
            self.status = TaskStatus.COMPLETE
        elif decision.state == "blocked":
            self.status = TaskStatus.BLOCKED
            self.blocker = decision.reason
        return decision

    def apply_verification(self, result: VerificationResult) -> None:
        self.applied.append(result)
        if result.status is VerificationStatus.FAIL:
            self.status = TaskStatus.BLOCKED
            self.state = "blocked"
            self.blocker = result.reason
        elif result.status is VerificationStatus.PASS:
            self.status = TaskStatus.RUNNING
            self.state = "verified"

    def discard_pending_action(
        self, reason: str, *, target_invalidated: bool = True
    ) -> None:
        self.discarded.append(reason)
        self.discard_policies.append(target_invalidated)
        self.state = "replan"

    def snapshot(self) -> TaskSnapshot:
        return TaskSnapshot("fake-task", self.status, self.state, self.blocker)


class _RecoveringVerificationTask(_Task):
    def apply_verification(self, result: VerificationResult):
        if (
            result.failure_kind
            is VerificationFailureKind.ITEM_QUANTITY_UNCHANGED_AT_DEADLINE
        ):
            self.applied.append(result)
            self.status = TaskStatus.RUNNING
            self.state = "recovered"
            return VerificationDisposition.RECOVERED
        return super().apply_verification(result)


class _RecoveringCameraVerificationTask(_Task):
    def apply_verification(self, result: VerificationResult):
        if (
            result.failure_kind
            is VerificationFailureKind.CONDITION_UNMET_AT_DEADLINE
        ):
            self.applied.append(result)
            self.status = TaskStatus.RUNNING
            self.state = "camera_pitch_suppressed"
            return VerificationDisposition.RECOVERED
        return super().apply_verification(result)


class _FaultyFailureTask(_Task):
    def __init__(self, decisions: list[Decision], *, complete: bool = False) -> None:
        super().__init__(decisions)
        self._complete = complete

    def apply_verification(self, result: VerificationResult) -> None:
        self.applied.append(result)
        self.status = TaskStatus.COMPLETE if self._complete else TaskStatus.RUNNING
        self.state = "complete" if self._complete else "incorrectly_running"


class _Verifier:
    def __init__(self, result: VerificationResult | None) -> None:
        self.result = result

    def evaluate(self, _specification, _observation):
        return self.result


class _ActionInterface:
    def __init__(
        self,
        result: ExecutionResult,
        recovery_result: InputReceipt | Exception | None = None,
    ) -> None:
        self.result = result
        self.calls: list[tuple[Action, Observation]] = []
        self.recovery_result = recovery_result
        self.recovery_calls: list[tuple[Observation, InputReceipt]] = []

    def execute(self, action: Action, observation: Observation) -> ExecutionResult:
        self.calls.append((action, observation))
        return self.result

    def recover_cursor(
        self,
        observation: Observation,
        invalidated_receipt: InputReceipt,
    ) -> InputReceipt:
        self.recovery_calls.append((observation, invalidated_receipt))
        if isinstance(self.recovery_result, Exception):
            raise self.recovery_result
        if self.recovery_result is not None:
            return self.recovery_result
        assert invalidated_receipt.pointer_geometry is not None
        return _completed_reacquisition_receipt(
            invalidated_receipt.pointer_geometry
        )


class _SequencedActionInterface:
    def __init__(
        self,
        *results: ExecutionResult,
        recovery_results: tuple[InputReceipt | Exception, ...] = (),
    ) -> None:
        self.results = list(results)
        self.calls: list[tuple[Action, Observation]] = []
        self.recovery_results = list(recovery_results)
        self.recovery_calls: list[tuple[Observation, InputReceipt]] = []

    def execute(self, action: Action, observation: Observation) -> ExecutionResult:
        self.calls.append((action, observation))
        return self.results.pop(0)

    def recover_cursor(
        self,
        observation: Observation,
        invalidated_receipt: InputReceipt,
    ) -> InputReceipt:
        self.recovery_calls.append((observation, invalidated_receipt))
        if self.recovery_results:
            result = self.recovery_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        assert invalidated_receipt.pointer_geometry is not None
        return _completed_reacquisition_receipt(
            invalidated_receipt.pointer_geometry
        )


class _RaisingActionInterface:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, _action: Action, _observation: Observation) -> ExecutionResult:
        self.calls += 1
        raise RuntimeError("serial boundary failed")


class _BrokenSnapshotTask(_Task):
    def snapshot(self) -> TaskSnapshot:
        raise RuntimeError("snapshot storage failed")


_PROBE_TARGET = WorldPoint(3200, 3200, 0)
_PROBE_POINT = ScreenPoint(250, 200)
_PROBE_BOUNDS = ScreenBounds(0, 0, 765, 503)


def _probe_target() -> NearbyObject:
    return NearbyObject(
        key="probe:waypoint",
        object_id=0,
        name="probe:waypoint",
        kind="NAVIGATION_TILE",
        actions=("Walk here",),
        location=_PROBE_TARGET,
        distance=2,
        geometry=TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            screen_point=_PROBE_POINT,
            screen_bounds=ScreenBounds(230, 180, 40, 40),
        ),
        scene_x=40,
        scene_y=41,
    )


def _probe_observation(tick: int, location: WorldPoint) -> Observation:
    return replace(
        _observation(tick),
        location=location,
        plane=location.plane,
        nearby_objects=(_probe_target(),),
        canvas_bounds=_PROBE_BOUNDS,
        viewport_bounds=_PROBE_BOUNDS,
        menu_client_tick=2000 + tick,
        menu_mouse_screen_point=_PROBE_POINT,
    )


class _WaypointProbeTask:
    """Non-woodcut structural task used only to prove the engine seam."""

    def __init__(self) -> None:
        self.status = TaskStatus.RUNNING
        self.state = "select_waypoint"
        self.last_result: VerificationResult | None = None

    def observation_request(self) -> ObservationRequest:
        return ObservationRequest((("probe:waypoint", _PROBE_TARGET),))

    def decide(self, observation: Observation) -> Decision:
        if self.status is TaskStatus.COMPLETE:
            return _wait(observation.tick, "complete")
        verification = VerificationSpec(
            VerificationKind.MOVED_CLOSER,
            before_tick=observation.tick,
            deadline_tick=observation.tick + 4,
            before_location=observation.location,
            target_location=_PROBE_TARGET,
            target_radius=0,
            source_session_id=observation.session_id,
        )
        self.state = "verify_waypoint"
        return Decision(
            self.state,
            "walk to exact probe waypoint",
            Action(
                ActionKind.WALK,
                "Walk to probe waypoint",
                observation.tick,
                option="Walk here",
                target_key="probe:waypoint",
                target_name="probe:waypoint",
                target_id=0,
                screen_point=_PROBE_POINT,
                verification=verification,
                source_menu_client_tick=observation.menu_client_tick,
                target_param0=40,
                target_param1=41,
                source_session_id=observation.session_id,
            ),
        )

    def apply_verification(self, result: VerificationResult) -> None:
        self.last_result = result
        if result.passed:
            self.status = TaskStatus.COMPLETE
            self.state = "complete"
        elif result.failed:
            self.status = TaskStatus.BLOCKED
            self.state = "blocked"

    def snapshot(self) -> TaskSnapshot:
        blocker = (
            self.last_result.reason
            if self.status is TaskStatus.BLOCKED and self.last_result is not None
            else None
        )
        return TaskSnapshot("waypoint-probe", self.status, self.state, blocker)


class _SafetyCheckedTransportStub:
    def __init__(self) -> None:
        self.gate = SafetyGate(max_observation_age_seconds=10.0)
        self.calls = 0

    def execute(self, action: Action, observation: Observation) -> ExecutionResult:
        self.calls += 1
        pre = self.gate.validate_pre_move(action, observation)
        if not pre.allowed:
            return _blocked_execution(action, observation.tick, pre.reason)
        post_tick = observation.tick + 1
        post = replace(
            observation,
            tick=post_tick,
            frame_id=f"test-frame-{post_tick}",
            geometry_frame_id=f"test-frame-{post_tick}",
            menus=(MenuEntry("Walk here", "", "WALK", 0),),
            menu_client_tick=(observation.menu_client_tick or 0) + 1,
            menu_source_tick=post_tick,
            menu_mouse_screen_point=action.screen_point,
        )
        after_move = self.gate.validate_post_move(action, post)
        if not after_move.allowed:
            return _blocked_execution(action, observation.tick, after_move.reason)
        return _sent_execution(action, observation.tick, post_tick)


class _IncrementingClient:
    def __init__(self) -> None:
        self.tick = 0

    def fetch(self, _tiles, _priority_object_ids=()) -> Observation:
        self.tick += 1
        return _observation(self.tick)


class _LongCycleTask:
    """Budget model: 28 chops plus 36 route/bank/return actions."""

    def __init__(self, action_count: int = 64) -> None:
        self.action_count = action_count
        self.completed = 0
        self.status = TaskStatus.RUNNING
        self.state = "ready"
        self.blocker: str | None = None

    def observation_request(self) -> ObservationRequest:
        return ObservationRequest()

    def decide(self, observation: Observation) -> Decision:
        if self.completed >= self.action_count:
            self.status = TaskStatus.COMPLETE
            self.state = "complete"
            return _wait(observation.tick, "complete")
        verification = VerificationSpec(
            VerificationKind.ITEM_QUANTITY_INCREASED,
            before_tick=observation.tick,
            deadline_tick=observation.tick + 20,
            item_id=1511,
            before_quantity=self.completed,
            source_session_id="runtime-session",
        )
        self.state = "verify_action"
        action = Action(
            ActionKind.INTERACT_OBJECT,
            f"cycle action {self.completed + 1}",
            observation.tick,
            verification=verification,
        )
        return Decision(self.state, "advance supported cycle", action)

    def apply_verification(self, result: VerificationResult) -> None:
        if result.status is VerificationStatus.PASS:
            self.completed += 1
            self.state = "ready"
        elif result.status is VerificationStatus.FAIL:
            self.status = TaskStatus.BLOCKED
            self.state = "blocked"
            self.blocker = result.reason

    def snapshot(self) -> TaskSnapshot:
        return TaskSnapshot("long-cycle-test", self.status, self.state, self.blocker)


class _PendingThenPassVerifier:
    def __init__(self, pending_per_action: int = 5) -> None:
        self.pending_per_action = pending_per_action
        self.calls = 0

    def evaluate(self, _specification, observation: Observation) -> VerificationResult:
        self.calls += 1
        if self.calls % (self.pending_per_action + 1):
            return VerificationResult(VerificationStatus.PENDING, "pathing")
        return VerificationResult(
            VerificationStatus.PASS,
            "verified",
            Outcome(OutcomeKind.ITEM_QUANTITY_INCREASED, observation.tick),
        )


class _AlwaysSentInterface:
    def execute(self, action: Action, observation: Observation) -> ExecutionResult:
        return _sent_execution(action, observation.tick, observation.tick + 1)


class _RecordingPublisher(EngineFramePublisher):
    def __init__(self) -> None:
        super().__init__()
        self.frames = []

    def publish(self, **values):
        frame = super().publish(**values)
        self.frames.append(frame)
        return frame


def _wait(tick: int, state: str = "waiting") -> Decision:
    return Decision(state, "wait", Action(ActionKind.WAIT, "Wait", tick))


def _executable(tick: int) -> tuple[Decision, VerificationSpec]:
    verification = VerificationSpec(
        VerificationKind.ITEM_QUANTITY_INCREASED,
        before_tick=tick,
        deadline_tick=tick + 8,
        item_id=1511,
        before_quantity=0,
        source_session_id="runtime-session",
    )
    action = Action(
        ActionKind.INTERACT_OBJECT,
        "Chop ordinary Tree",
        tick,
        option="Chop down",
        target_key="tree",
        target_name="Tree",
        target_id=1276,
        verification=verification,
    )
    return Decision("verify_action", "chop", action), verification


def _camera_executable(
    tick: int,
    *,
    key: str = "up",
) -> tuple[Decision, VerificationSpec]:
    verification = VerificationSpec(
        VerificationKind.CAMERA_POSE_CHANGED,
        before_tick=tick,
        deadline_tick=tick + 8,
        before_location=WorldPoint(3192, 3244, 0),
        source_session_id="runtime-session",
        before_camera_yaw=15_538,
        before_camera_pitch=3_064,
        before_geometry_frame_id=f"test-frame-{tick}",
        camera_key=key,
    )
    action = Action(
        ActionKind.CAMERA_HOLD,
        "Frame Tree",
        tick,
        option=f"Turn camera {key}",
        target_key="tree",
        target_name="Tree",
        target_id=1276,
        key=key,
        key_hold_millis=106,
        verification=verification,
    )
    return Decision("chop", f"camera {key}", action), verification


class TaskRuntimeTests(unittest.TestCase):
    def test_camera_zoom_verification_is_retained_on_immutable_receipt(self) -> None:
        location = WorldPoint(3192, 3244, 0)
        specification = VerificationSpec(
            VerificationKind.CAMERA_ZOOM_CHANGED,
            before_tick=10,
            deadline_tick=18,
            before_location=location,
            source_session_id="runtime-session",
            before_camera_yaw=1_000,
            before_camera_pitch=900,
            before_geometry_frame_id="camera-before",
            before_camera_zoom=200,
            camera_zoom_amount=1,
            before_process_id=1234,
        )
        ui = CameraUiState(
            bank_known=True,
            bank_open=False,
            bank_pin_open=False,
            bank_readable=False,
            dialogue_active=False,
            dialogue_type="none",
            text_input_active=False,
        )
        zoom = CameraZoomResult(
            wheel_amount=1,
            before_zoom=200,
            after_zoom=216,
            zoom_delta=16,
            before_yaw=1_000,
            after_yaw=1_000,
            before_pitch=900,
            after_pitch=900,
            before_process_id=1234,
            after_process_id=1234,
            before_location=location,
            after_location=location,
            source_session_id="runtime-session",
            before_geometry_frame_id="camera-before",
            after_geometry_frame_id="camera-after",
            before_ui_state=ui,
            after_ui_state=ui,
        )
        result = VerificationResult(
            VerificationStatus.PASS,
            "camera_zoom_changed",
            Outcome(
                OutcomeKind.CAMERA_ZOOM_CHANGED,
                11,
                camera_zoom_result=zoom,
            ),
        )
        action = Action(ActionKind.CAMERA_ZOOM, "Zoom camera", 10)
        execution = _sent_execution(action, 10, 10)

        attached = _attach_camera_verification(
            execution,
            specification,
            result,
        )

        self.assertIsNot(attached, execution)
        self.assertIsNotNone(attached.receipt)
        assert attached.receipt is not None
        evidence = attached.receipt.camera_verification
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual("camera_zoom_changed", evidence.kind)
        self.assertEqual("pass", evidence.status)
        self.assertEqual(200, evidence.before_zoom)
        self.assertEqual(216, evidence.after_zoom)
        self.assertEqual("camera-before", evidence.before_geometry_frame_id)
        self.assertEqual("camera-after", evidence.after_geometry_frame_id)
        self.assertIs(True, evidence.ui_state_unchanged)

    def test_passive_freshness_wait_states_are_exact_and_not_arduino_failures(self) -> None:
        cases = (
            (
                replace(_observation(10), source_coherent=False),
                WaitState.WAITING_FOR_SOURCE_COHERENCE,
            ),
            (
                replace(_observation(10), fresh=False),
                WaitState.WAITING_FOR_NEXT_SCENE_UPDATE,
            ),
        )
        for observation, expected in cases:
            with self.subTest(expected=expected.value):
                publisher = _RecordingPublisher()
                runtime = TaskRuntime(
                    _Client(observation),
                    _Task([_wait(10)]),
                    _Verifier(None),
                    configuration=replace(DEFAULT_RUNTIME_CONFIG, max_observations=1),
                    frame_publisher=publisher,
                    sleep=lambda _seconds: None,
                )

                runtime.run()

                observed = tuple(
                    frame.observability.wait_state
                    for frame in publisher.frames
                    if frame.observability.wait_state is not None
                )
                self.assertIn(expected, observed)
                self.assertNotIn(WaitState.ARDUINO_COMMAND_FAILED, observed)

    def test_retryable_endpoint_backpressure_waits_without_spending_observation_budget(self) -> None:
        publisher = _RecordingPublisher()
        sleeps: list[float] = []
        client = _Client(
            ObservationBackpressureError(503, "endpoint_busy"),
            _observation(10),
        )
        runtime = TaskRuntime(
            client,
            _Task([_wait(10)]),
            _Verifier(None),
            configuration=replace(DEFAULT_RUNTIME_CONFIG, max_observations=1),
            frame_publisher=publisher,
            sleep=sleeps.append,
        )

        result = runtime.run()

        self.assertEqual("LIMIT", result.status)
        self.assertEqual(1, result.observations)
        self.assertEqual([], client.results)
        self.assertGreaterEqual(len(sleeps), 1)
        states = tuple(
            frame.observability.wait_state for frame in publisher.frames
        )
        self.assertIn(WaitState.ENDPOINT_BACKPRESSURE, states)
        backpressure_timing = result.engine_frame.observability.timing.for_phase(
            TimingPhase.ENDPOINT_BACKPRESSURE_WAIT
        )
        self.assertIsNotNone(backpressure_timing)
        self.assertEqual(1, backpressure_timing.count)

    def test_verification_backpressure_waits_for_a_fresh_candidate(self) -> None:
        decision, _verification = _executable(10)
        passed = VerificationResult(
            VerificationStatus.PASS,
            "verified",
            Outcome(OutcomeKind.ITEM_QUANTITY_INCREASED, 11),
        )
        publisher = _RecordingPublisher()
        task = _Task([decision])
        runtime = TaskRuntime(
            _Client(
                _observation(10),
                ObservationBackpressureError(503, "endpoint_busy"),
                _observation(11),
            ),
            task,
            _Verifier(passed),
            _ActionInterface(_sent_execution(decision.action, 10, 11)),
            configuration=replace(DEFAULT_RUNTIME_CONFIG, max_observations=2),
            frame_publisher=publisher,
            sleep=lambda _seconds: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual(2, result.observations)
        self.assertEqual([passed], task.applied)
        self.assertIn(
            WaitState.ENDPOINT_BACKPRESSURE,
            tuple(
                frame.observability.wait_state
                for frame in publisher.frames
            ),
        )
        self.assertIsNotNone(
            result.engine_frame.observability.timing.for_phase(
                TimingPhase.ENDPOINT_BACKPRESSURE_WAIT
            )
        )

    def test_endpoint_backpressure_storm_is_bounded_and_terminal(self) -> None:
        client = _Client(
            *[
                ObservationBackpressureError(503, "endpoint_busy")
                for _ in range(9)
            ]
        )
        publisher = _RecordingPublisher()
        runtime = TaskRuntime(
            client,
            _Task([_wait(10)]),
            _Verifier(None),
            frame_publisher=publisher,
            sleep=lambda _seconds: None,
        )

        result = runtime.run()

        self.assertEqual("ERROR", result.status)
        self.assertEqual(0, result.observations)
        self.assertIn("8-retry budget", result.reason)
        self.assertEqual([], client.results)
        self.assertIn(
            WaitState.ENDPOINT_BACKPRESSURE,
            tuple(
                frame.observability.wait_state
                for frame in publisher.frames
            ),
        )

    def test_world_model_handoff_storm_is_bounded_without_counting_observations(
        self,
    ) -> None:
        client = _Client(
            *[
                ObservationWorldModelHandoffError(("scene_object_census",))
                for _ in range(9)
            ]
        )
        publisher = _RecordingPublisher()
        task = _Task([_wait(10)])
        runtime = TaskRuntime(
            client,
            task,
            _Verifier(None),
            frame_publisher=publisher,
            sleep=lambda _seconds: None,
        )

        result = runtime.run()

        self.assertEqual("ERROR", result.status)
        self.assertEqual(0, result.observations)
        self.assertEqual(0, result.actions)
        self.assertEqual(0, task.decide_calls)
        self.assertIn("8-retry budget", result.reason)
        self.assertEqual([], client.results)
        self.assertIn(
            WaitState.WAITING_FOR_SOURCE_COHERENCE,
            tuple(
                frame.observability.wait_state
                for frame in publisher.frames
            ),
        )

    def test_verification_retries_world_model_handoff_without_reexecuting(self) -> None:
        decision, _verification = _executable(10)
        task = _Task([decision, _wait(12, "complete")])
        interface = _ActionInterface(_sent_execution(decision.action, 10, 10))
        gained_log = replace(
            _observation(11),
            inventory=InventoryObservation(
                items=(InventoryItem(slot=0, item_id=1511, quantity=1),),
                occupied_slots=1,
                free_slots=27,
                known=True,
            ),
        )
        publisher = _RecordingPublisher()
        runtime = TaskRuntime(
            _Client(
                _observation(10),
                ObservationWorldModelHandoffError(("scene_object_census",)),
                gained_log,
                _observation(12),
            ),
            task,
            Verifier(max_observation_age_seconds=10.0),
            interface,
            configuration=replace(DEFAULT_RUNTIME_CONFIG, max_observations=3),
            frame_publisher=publisher,
            sleep=lambda _seconds: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual(3, result.observations)
        self.assertEqual(1, result.actions)
        self.assertEqual(1, len(interface.calls))
        self.assertEqual(1, len(task.applied))
        self.assertTrue(task.applied[0].passed)
        self.assertIn(
            WaitState.WAITING_FOR_SOURCE_COHERENCE,
            tuple(
                frame.observability.wait_state
                for frame in publisher.frames
            ),
        )

    def test_verification_world_model_handoff_storm_is_bounded_without_reexecuting(
        self,
    ) -> None:
        decision, _verification = _executable(10)
        task = _Task([decision])
        execution = _sent_execution(decision.action, 10, 10)
        interface = _ActionInterface(execution)
        client = _Client(
            _observation(10),
            *[
                ObservationWorldModelHandoffError(
                    ("scene_object_census",)
                )
                for _ in range(9)
            ],
        )
        runtime = TaskRuntime(
            client,
            task,
            _Verifier(None),
            interface,
            sleep=lambda _seconds: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual(1, result.observations)
        self.assertEqual(1, result.actions)
        self.assertEqual(1, task.decide_calls)
        self.assertEqual(1, len(interface.calls))
        self.assertIs(interface.calls[0][0], decision.action)
        self.assertEqual(10, interface.calls[0][1].tick)
        self.assertIs(execution, result.execution)
        self.assertEqual([], client.results)
        self.assertEqual(1, len(task.applied))
        failure = task.applied[0]
        self.assertIs(VerificationStatus.FAIL, failure.status)
        self.assertIs(
            VerificationFailureKind.RUNTIME_FAILURE,
            failure.failure_kind,
        )
        self.assertIn("verification observation remained", failure.reason)
        self.assertIn("8-retry budget", failure.reason)
        self.assertEqual(failure.reason, result.reason)
        self.assertIs(TaskStatus.BLOCKED, task.status)
        self.assertEqual("blocked", task.state)
        self.assertEqual(failure.reason, task.blocker)
        self.assertIs(TaskStatus.BLOCKED, result.task_snapshot.status)
        self.assertEqual(failure.reason, result.task_snapshot.blocker)

    def test_runtime_records_additive_phase_timing_without_control_clock_reads(self) -> None:
        evidence_value = 0.0

        def evidence_clock() -> float:
            nonlocal evidence_value
            evidence_value += 0.001
            return evidence_value

        publisher = _RecordingPublisher()
        runtime = TaskRuntime(
            _Client(_observation(10)),
            _Task([_wait(10)]),
            _Verifier(None),
            configuration=replace(DEFAULT_RUNTIME_CONFIG, max_observations=1),
            frame_publisher=publisher,
            sleep=lambda _seconds: None,
            clock=lambda: 0.0,
            evidence_clock=evidence_clock,
        )

        result = runtime.run()

        timing = result.engine_frame.observability.timing
        observation_timing = timing.for_phase(TimingPhase.OBSERVATION_REQUEST_FETCH)
        decision_timing = timing.for_phase(TimingPhase.TASK_DECISION)
        wait_timing = timing.for_phase(
            TimingPhase.SOURCE_COHERENCE_FRESHNESS_WAIT
        )
        self.assertIsNotNone(observation_timing)
        self.assertIsNotNone(decision_timing)
        self.assertIsNotNone(wait_timing)
        self.assertGreaterEqual(observation_timing.total_millis, 0)
        self.assertGreaterEqual(decision_timing.total_millis, 0)
        self.assertGreaterEqual(wait_timing.total_millis, 0)

    def test_failed_evidence_clock_cannot_change_runtime_result(self) -> None:
        runtime = TaskRuntime(
            _Client(_observation(10)),
            _Task([_wait(10)]),
            _Verifier(None),
            configuration=replace(DEFAULT_RUNTIME_CONFIG, max_observations=1),
            sleep=lambda _seconds: None,
            evidence_clock=lambda: (_ for _ in ()).throw(
                RuntimeError("diagnostic clock failed")
            ),
        )

        result = runtime.run()

        self.assertEqual("LIMIT", result.status)
        self.assertEqual(1, result.observations)
        fetch = result.engine_frame.observability.timing.for_phase(
            TimingPhase.OBSERVATION_REQUEST_FETCH
        )
        self.assertIsNotNone(fetch)
        self.assertEqual(0, fetch.total_millis)

    def test_input_busy_is_published_as_passive_state_before_execution(self) -> None:
        decision, _verification = _executable(10)
        execution = _sent_execution(decision.action, 10, 11)
        passed = VerificationResult(
            VerificationStatus.PASS,
            "verified",
            Outcome(OutcomeKind.ITEM_QUANTITY_INCREASED, 11),
        )
        publisher = _RecordingPublisher()
        runtime = TaskRuntime(
            _Client(_observation(10), _observation(11)),
            _Task([decision]),
            _Verifier(passed),
            _ActionInterface(execution),
            frame_publisher=publisher,
            sleep=lambda _seconds: None,
        )

        runtime.run(execute=True)

        states = tuple(
            frame.observability.wait_state for frame in publisher.frames
        )
        self.assertIn(WaitState.INPUT_TRANSACTION_BUSY, states)
        self.assertNotIn(WaitState.ARDUINO_COMMAND_FAILED, states)
        terminal_timing = publisher.frames[-1].observability.timing
        self.assertIsNotNone(
            terminal_timing.for_phase(
                TimingPhase.POST_ACTION_FRESH_OBSERVATION_WAIT
            )
        )
        self.assertIsNotNone(
            terminal_timing.for_phase(
                TimingPhase.SEMANTIC_OR_CAMERA_VERIFICATION
            )
        )

    def test_non_woodcut_task_uses_real_runtime_safety_and_verifier(self) -> None:
        task = _WaypointProbeTask()
        transport = _SafetyCheckedTransportStub()
        runtime = TaskRuntime(
            _Client(
                _probe_observation(10, WorldPoint(3198, 3200, 0)),
                _probe_observation(11, _PROBE_TARGET),
                _probe_observation(12, _PROBE_TARGET),
                _probe_observation(13, _PROBE_TARGET),
            ),
            task,
            Verifier(max_observation_age_seconds=10.0),
            transport,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual(1, transport.calls)
        self.assertIsNotNone(task.last_result)
        self.assertEqual(OutcomeKind.ARRIVED, task.last_result.outcome.kind)
        self.assertEqual("waypoint-probe", result.task_snapshot.task_id)

    def test_snapshot_failure_returns_bounded_error(self) -> None:
        task = _BrokenSnapshotTask([_wait(10)])
        runtime = TaskRuntime(
            _Client(_observation(10)), task, _Verifier(None), sleep=lambda _: None
        )

        result = runtime.run()

        self.assertEqual("ERROR", result.status)
        self.assertIn("snapshot storage failed", result.reason)
        self.assertEqual("task_snapshot_unavailable", result.task_snapshot.task_id)
        self.assertEqual("snapshot_error", result.task_snapshot.state)

    def test_dry_run_stops_at_first_executable_action(self) -> None:
        decision, _ = _executable(10)
        client = _Client(_observation(10))
        task = _Task(
            [decision],
            projections=(("route:x", WorldPoint(1, 2, 0)),),
            priority_object_ids=(1276,),
        )
        runtime = TaskRuntime(client, task, _Verifier(None), sleep=lambda _: None)

        result = runtime.run(execute=False)

        self.assertEqual("DRY_RUN", result.status)
        self.assertEqual(0, result.actions)
        self.assertEqual(task.projections, client.requests[0])
        self.assertEqual(task.priority_object_ids, client.priority_requests[0])
        payload = result.to_dict()
        self.assertEqual("verify_action", payload["state"])
        self.assertEqual("fake-task", payload["taskId"])
        self.assertEqual("running", payload["taskStatus"])
        self.assertIsNone(payload["blocker"])
        self.assertEqual("verify_action", payload["decision"]["state"])
        self.assertEqual(
            "interact_object", payload["decision"]["action"]["kind"]
        )

    def test_failed_execution_applies_one_typed_failure(self) -> None:
        decision, _ = _executable(10)
        task = _Task([decision])
        execution = _blocked_execution(decision.action, 10, "hover_menu_mismatch")
        interface = _ActionInterface(execution)
        runtime = TaskRuntime(
            _Client(_observation(10)),
            task,
            _Verifier(None),
            interface,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual(1, len(task.applied))
        self.assertIs(task.applied[0].status, VerificationStatus.FAIL)
        self.assertEqual(
            "execution blocked: hover_menu_mismatch", task.applied[0].reason
        )
        self.assertIsNone(task.applied[0].outcome)
        self.assertEqual(1, len(interface.calls))
        self.assertIsNone(result.engine_frame.pending_verification)
        self.assertIs(result.engine_frame.last_verification.status, VerificationStatus.FAIL)

    def test_post_activation_proof_failure_blocks_without_unsent_claim_or_retry(
        self,
    ) -> None:
        decision, _ = _executable(10)
        reason = "owned_mouse_transition_unproved_after_activation"
        task = _Task([decision])
        execution = _post_activation_error_execution(
            decision.action,
            10,
            reason,
        )
        interface = _ActionInterface(execution)
        runtime = TaskRuntime(
            _Client(_observation(10)),
            task,
            _Verifier(None),
            interface,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("action activation was attempted", result.reason)
        self.assertNotIn("action was not sent", result.reason)
        self.assertEqual(1, len(interface.calls))
        self.assertEqual([], task.discarded)
        self.assertEqual(1, len(task.applied))
        self.assertEqual(
            "post-activation execution proof failed: error: " + reason,
            task.applied[0].reason,
        )
        self.assertTrue(result.execution.activation_attempted)
        self.assertTrue(result.to_dict()["execution"]["activationAttempted"])
        self.assertTrue(result.engine_frame.last_execution_activation_attempted)
        self.assertTrue(
            result.to_dict()["engineFrame"]["lastExecution"][
                "activationAttempted"
            ]
        )

    def test_safe_pre_activation_hover_mismatch_reobserves_once(self) -> None:
        first, _ = _executable(10)
        complete = Decision(
            "complete",
            "fresh evidence selected no further action",
            Action(ActionKind.WAIT, "Wait", 11),
        )
        task = _Task([first, complete])
        reason = "fresh_input_validation_denied: hover_menu_mismatch"
        interface = _ActionInterface(
            _safe_unsent_execution(first.action, 10, reason)
        )
        runtime = TaskRuntime(
            _Client(_observation(10), _observation(11)),
            task,
            _Verifier(None),
            interface,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual([reason], task.discarded)
        self.assertEqual([], task.applied)
        self.assertEqual(1, result.actions)

    def test_second_consecutive_unsent_hover_mismatch_blocks(self) -> None:
        first, _ = _executable(10)
        second, _ = _executable(11)
        task = _Task([first, second])
        reason = "fresh_input_validation_denied: hover_menu_mismatch"
        interface = _SequencedActionInterface(
            _safe_unsent_execution(first.action, 10, reason),
            _safe_unsent_execution(second.action, 11, reason),
        )
        runtime = TaskRuntime(
            _Client(_observation(10), _observation(11)),
            task,
            _Verifier(None),
            interface,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual([reason], task.discarded)
        self.assertEqual(1, len(task.applied))
        self.assertIs(task.applied[0].status, VerificationStatus.FAIL)
        self.assertEqual(2, result.actions)

    def test_eligible_cursor_invalidation_recovers_once_then_replans_from_strictly_newer_evidence(self) -> None:
        first, _ = _executable(10)
        second, _ = _executable(12)
        complete = Decision(
            "complete",
            "fresh retry verified",
            Action(ActionKind.WAIT, "Wait", 14),
        )
        reason = "cursor_feedback_direction_reversed"
        passed = VerificationResult(
            VerificationStatus.PASS,
            "verified after fresh replan",
            Outcome(OutcomeKind.ITEM_QUANTITY_INCREASED, 13),
        )
        task = _Task([first, second, complete])
        interface = _SequencedActionInterface(
            _safe_unsent_execution(
                first.action,
                10,
                reason,
                disposition=UnsentActionDisposition.CURSOR_STATE_INVALIDATED,
                cursor_cause=CursorInvalidationCause.UNEXPECTED_DIRECTION,
            ),
            _sent_execution(second.action, 12, 12),
        )
        publisher = _RecordingPublisher()
        runtime = TaskRuntime(
            _Client(
                _cursor_observation(10),
                _cursor_observation(10),
                _cursor_observation(11),
                _cursor_observation(12),
                _cursor_observation(13),
                _cursor_observation(14),
            ),
            task,
            _Verifier(passed),
            interface,
            frame_publisher=publisher,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual([reason], task.discarded)
        self.assertEqual([False], task.discard_policies)
        self.assertEqual(2, result.actions)
        self.assertEqual(6, result.observations)
        self.assertEqual([10, 12], [call[1].tick for call in interface.calls])
        self.assertEqual(1, len(interface.recovery_calls))
        receipt_ids = tuple(
            frame.last_execution_receipt.transaction_id
            for frame in publisher.frames
            if frame.last_execution_receipt is not None
        )
        self.assertIn("input-00000002", receipt_ids)
        self.assertIn("input-cursor-recovery", receipt_ids)
        pre_replan_receipts = {
            frame.last_execution_receipt.transaction_id:
            frame.last_execution_receipt
            for frame in publisher.frames
            if frame.last_execution_receipt is not None
            and frame.last_execution_receipt.transaction_id
            in {"input-00000002", "input-cursor-recovery"}
        }
        self.assertEqual(
            {"input-00000002", "input-cursor-recovery"},
            set(pre_replan_receipts),
        )
        for receipt in pre_replan_receipts.values():
            names = {command.command for command in receipt.commands}
            self.assertTrue(
                names.isdisjoint({"MOUSE_DOWN", "MOUSE_UP", "KEY_PRESS"})
            )

    def test_each_eligible_cursor_cause_gets_exactly_one_fresh_retry(self) -> None:
        causes = (
            CursorInvalidationCause.UNEXPECTED_DIRECTION,
            CursorInvalidationCause.UNSUPPORTED_TRANSFER_GAIN,
            CursorInvalidationCause.UNEXPECTED_CROSS_AXIS,
            CursorInvalidationCause.OUTSIDE_PADDED_VIEWPORT,
            CursorInvalidationCause.POINT_OWNER_MISMATCH,
        )
        for cause in causes:
            with self.subTest(cause=cause.value):
                first, _ = _executable(10)
                retry, _ = _executable(12)
                complete = Decision(
                    "complete",
                    "fresh retry verified",
                    Action(ActionKind.WAIT, "Wait", 14),
                )
                passed = VerificationResult(
                    VerificationStatus.PASS,
                    "verified after typed cursor recovery",
                    Outcome(OutcomeKind.ITEM_QUANTITY_INCREASED, 13),
                )
                reason = f"typed_cursor_invalidation:{cause.value}"
                task = _Task([first, retry, complete])
                interface = _SequencedActionInterface(
                    _safe_unsent_execution(
                        first.action,
                        10,
                        reason,
                        disposition=(
                            UnsentActionDisposition.CURSOR_STATE_INVALIDATED
                        ),
                        cursor_cause=cause,
                    ),
                    _sent_execution(retry.action, 12, 12),
                )
                runtime = TaskRuntime(
                    _Client(
                        _cursor_observation(10),
                        _cursor_observation(12),
                        _cursor_observation(13),
                        _cursor_observation(14),
                    ),
                    task,
                    _Verifier(passed),
                    interface,
                    sleep=lambda _: None,
                )

                result = runtime.run(execute=True)

                self.assertEqual("COMPLETE", result.status)
                self.assertEqual([reason], task.discarded)
                self.assertEqual([False], task.discard_policies)
                self.assertEqual([10, 12], [call[1].tick for call in interface.calls])
                self.assertEqual(1, len(interface.recovery_calls))
                recovery = interface.recovery_calls[0][1]
                self.assertTrue(
                    all(
                        command.command
                        not in {"MOUSE_DOWN", "MOUSE_UP", "KEY_PRESS"}
                        for command in recovery.commands
                    )
                )

    def test_satisfied_camera_framing_reobserves_once_without_failure(self) -> None:
        first, _ = _executable(10)
        complete = Decision(
            "complete",
            "fresh camera geometry selected no further action",
            Action(ActionKind.WAIT, "Wait", 11),
        )
        reason = "camera_projection_already_actionable"
        task = _Task([first, complete])
        interface = _ActionInterface(
            _safe_unsent_execution(
                first.action,
                10,
                reason,
                disposition=(
                    UnsentActionDisposition.CAMERA_FRAMING_SATISFIED
                ),
            )
        )
        runtime = TaskRuntime(
            _Client(_observation(10), _observation(11)),
            task,
            _Verifier(None),
            interface,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual([reason], task.discarded)
        self.assertEqual([False], task.discard_policies)
        self.assertEqual([], task.applied)
        self.assertEqual(1, result.actions)

    def test_already_reacquired_receipt_discards_stale_intent_without_second_recovery(self) -> None:
        first, _ = _executable(10)
        complete = Decision(
            "complete",
            "newer cursor evidence selected no further action",
            Action(ActionKind.WAIT, "Wait", 12),
        )

        class RecordingTask(_Task):
            def __init__(self) -> None:
                super().__init__([first, complete])
                self.decision_ticks: list[int] = []

            def decide(self, observation: Observation) -> Decision:
                self.decision_ticks.append(observation.tick)
                return super().decide(observation)

        reason = "cursor_reacquired_reobserve_required"
        task = RecordingTask()
        completed = _completed_reacquisition_receipt()
        assert completed.cursor_reacquisition is not None
        execution = _safe_unsent_execution(
            first.action,
            10,
            reason,
            disposition=UnsentActionDisposition.CURSOR_STATE_INVALIDATED,
            cursor_cause=CursorInvalidationCause.CURSOR_REACQUIRED,
            cursor_reacquisition=completed.cursor_reacquisition,
        )
        assert execution.receipt is not None
        self.assertFalse(execution.receipt.safely_unsent)
        self.assertTrue(execution.cleanup_confirmed)
        interface = _ActionInterface(execution)
        runtime = TaskRuntime(
            _Client(
                _cursor_observation(10),
                _cursor_observation(10),
                _cursor_observation(11),
                _cursor_observation(12),
            ),
            task,
            _Verifier(None),
            interface,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual([10, 12], task.decision_ticks)
        self.assertEqual([reason], task.discarded)
        self.assertEqual([False], task.discard_policies)
        self.assertEqual(4, result.observations)
        self.assertEqual(1, result.actions)
        self.assertEqual(1, len(interface.calls))
        self.assertEqual(0, len(interface.recovery_calls))
        self.assertEqual(_CURSOR_CANVAS, interface.calls[0][1].canvas_bounds)
        self.assertEqual(_CURSOR_VIEWPORT, interface.calls[0][1].viewport_bounds)
        self.assertEqual(_CURSOR_CLIENT, interface.calls[0][1].client_window_bounds)

    def test_cursor_reacquisition_waits_for_fresh_coherent_newer_observation(self) -> None:
        reason = "cursor_reacquired_reobserve_required"
        invalid_shapes = {
            "source_not_fresh": {"fresh": False},
            "wall_clock_not_fresh": {"cache_wall_clock_fresh": False},
            "source_incoherent": {"source_coherent": False},
        }

        def geometry_observation(tick: int, **changes: object) -> Observation:
            return _cursor_observation(tick, **changes)

        for label, changes in invalid_shapes.items():
            with self.subTest(label=label):
                first, _ = _executable(10)
                complete = Decision(
                    "complete",
                    "fresh coherent cursor evidence selected no further action",
                    Action(ActionKind.WAIT, "Wait", 13),
                )

                class RecordingTask(_Task):
                    def __init__(self) -> None:
                        super().__init__([first, complete])
                        self.decision_ticks: list[int] = []

                    def decide(self, observation: Observation) -> Decision:
                        self.decision_ticks.append(observation.tick)
                        return super().decide(observation)

                task = RecordingTask()
                execution = _safe_unsent_execution(
                    first.action,
                    10,
                    reason,
                    disposition=(
                        UnsentActionDisposition.CURSOR_STATE_INVALIDATED
                    ),
                )
                interface = _ActionInterface(execution)
                runtime = TaskRuntime(
                    _Client(
                        geometry_observation(10),
                        geometry_observation(12, **changes),
                        geometry_observation(13),
                    ),
                    task,
                    _Verifier(None),
                    interface,
                    sleep=lambda _: None,
                )

                result = runtime.run(execute=True)

                self.assertEqual("COMPLETE", result.status)
                self.assertEqual([10, 13], task.decision_ticks)
                self.assertEqual([reason], task.discarded)
                self.assertEqual(3, result.observations)
                self.assertEqual(1, result.actions)
                self.assertEqual(1, len(interface.calls))
                self.assertEqual(1, len(interface.recovery_calls))

    def test_cursor_reacquisition_blocks_if_canvas_viewport_or_window_geometry_changes(self) -> None:
        first_observation = _cursor_observation(10)
        changes = {
            "canvas": {
                "canvas_bounds": ScreenBounds(
                    _CURSOR_CANVAS.x + 1,
                    _CURSOR_CANVAS.y,
                    _CURSOR_CANVAS.width,
                    _CURSOR_CANVAS.height,
                )
            },
            "viewport": {
                "viewport_bounds": ScreenBounds(
                    _CURSOR_VIEWPORT.x + 1,
                    _CURSOR_VIEWPORT.y,
                    _CURSOR_VIEWPORT.width,
                    _CURSOR_VIEWPORT.height,
                )
            },
            "client": {
                "client_window_bounds": ScreenBounds(
                    _CURSOR_CLIENT.x,
                    _CURSOR_CLIENT.y,
                    _CURSOR_CLIENT.width - 1,
                    _CURSOR_CLIENT.height,
                )
            },
        }

        for label, replacement in changes.items():
            with self.subTest(label=label):
                first, _ = _executable(10)
                reason = "cursor_reacquired_reobserve_required"
                task = _Task([first])
                execution = _safe_unsent_execution(
                    first.action,
                    10,
                    reason,
                    disposition=UnsentActionDisposition.CURSOR_STATE_INVALIDATED,
                )
                interface = _ActionInterface(execution)
                changed_values = {
                    "canvas_bounds": _CURSOR_CANVAS,
                    "viewport_bounds": _CURSOR_VIEWPORT,
                    "client_window_bounds": _CURSOR_CLIENT,
                }
                changed_values.update(replacement)
                changed = replace(_observation(12), **changed_values)
                runtime = TaskRuntime(
                    _Client(first_observation, changed),
                    task,
                    _Verifier(None),
                    interface,
                    sleep=lambda _: None,
                )

                result = runtime.run(execute=True)

                self.assertEqual("BLOCKED", result.status)
                self.assertEqual(
                    "RuneLite geometry changed after cursor reacquisition",
                    result.reason,
                )
                self.assertEqual([reason], task.discarded)
                self.assertEqual([False], task.discard_policies)
                self.assertEqual(1, task.decide_calls)
                self.assertEqual(1, result.actions)
                self.assertEqual(2, result.observations)
                self.assertEqual(1, len(interface.calls))
                self.assertEqual(1, len(interface.recovery_calls))

    def test_identity_geometry_and_physical_cursor_invalidations_are_terminal(self) -> None:
        cases = (
            CursorInvalidationCause.IDENTITY_CHANGED,
            CursorInvalidationCause.GEOMETRY_CHANGED,
            CursorInvalidationCause.PHYSICAL_INPUT_ACTIVITY,
        )
        for cause in cases:
            with self.subTest(cause=cause.value):
                first, _ = _executable(10)
                reason = f"terminal_cursor_invalidation:{cause.value}"
                task = _Task([first])
                interface = _ActionInterface(
                    _safe_unsent_execution(
                        first.action,
                        10,
                        reason,
                        disposition=(
                            UnsentActionDisposition.CURSOR_STATE_INVALIDATED
                        ),
                        cursor_cause=cause,
                    )
                )
                runtime = TaskRuntime(
                    _Client(_cursor_observation(10)),
                    task,
                    _Verifier(None),
                    interface,
                    sleep=lambda _: None,
                )

                result = runtime.run(execute=True)

                self.assertEqual("BLOCKED", result.status)
                self.assertIn(reason, result.reason)
                self.assertEqual([], task.discarded)
                self.assertEqual(1, result.actions)
                self.assertEqual(1, result.observations)
                self.assertEqual(1, len(interface.calls))
                self.assertEqual(0, len(interface.recovery_calls))

    def test_second_consecutive_cursor_state_invalidation_blocks(self) -> None:
        first, _ = _executable(10)
        second, _ = _executable(12)
        reason = "cursor_changed_after_pointer_validation"
        repeated = "cursor_left_padded_viewport_again"
        task = _Task([first, second])
        interface = _SequencedActionInterface(
            _safe_unsent_execution(
                first.action,
                10,
                reason,
                disposition=UnsentActionDisposition.CURSOR_STATE_INVALIDATED,
                cursor_cause=CursorInvalidationCause.UNEXPECTED_CROSS_AXIS,
            ),
            _safe_unsent_execution(
                second.action,
                12,
                repeated,
                disposition=UnsentActionDisposition.CURSOR_STATE_INVALIDATED,
                cursor_cause=CursorInvalidationCause.OUTSIDE_PADDED_VIEWPORT,
            ),
        )
        runtime = TaskRuntime(
            _Client(_cursor_observation(10), _cursor_observation(12)),
            task,
            _Verifier(None),
            interface,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("BLOCKED", result.status)
        self.assertIn(repeated, result.reason)
        self.assertEqual([reason], task.discarded)
        self.assertEqual([False], task.discard_policies)
        self.assertEqual(2, result.actions)
        self.assertEqual(1, len(interface.recovery_calls))
        payload = result.to_dict()["execution"]
        self.assertEqual(
            "cursor_state_invalidated", payload["unsentDisposition"]
        )
        self.assertEqual(
            "cursor_state_invalidated",
            payload["receipt"]["failureKind"],
        )

    def test_post_recovery_semantic_failure_is_terminal_without_generic_replan(self) -> None:
        first, _ = _executable(10)
        retry, _ = _executable(12)
        initial_reason = "cursor_feedback_uncommanded_axis_y"
        retry_reason = "fresh_input_validation_denied: hover_menu_mismatch"
        task = _Task([first, retry])
        interface = _SequencedActionInterface(
            _safe_unsent_execution(
                first.action,
                10,
                initial_reason,
                disposition=UnsentActionDisposition.CURSOR_STATE_INVALIDATED,
                cursor_cause=CursorInvalidationCause.UNEXPECTED_CROSS_AXIS,
            ),
            _safe_unsent_execution(
                retry.action,
                12,
                retry_reason,
                disposition=UnsentActionDisposition.TARGET_EVIDENCE_INVALIDATED,
            ),
        )

        result = TaskRuntime(
            _Client(_cursor_observation(10), _cursor_observation(12)),
            task,
            _Verifier(None),
            interface,
            sleep=lambda _: None,
        ).run(execute=True)

        self.assertEqual("BLOCKED", result.status)
        self.assertIn(retry_reason, result.reason)
        self.assertEqual([initial_reason], task.discarded)
        self.assertEqual([False], task.discard_policies)
        self.assertEqual(2, len(interface.calls))
        self.assertEqual(1, len(interface.recovery_calls))

    def test_recovery_geometry_binding_survives_passive_wait_decision(self) -> None:
        first, _ = _executable(10)
        wait = Decision(
            "retry_wait",
            "fresh recognition is still waiting for a pointer target",
            Action(ActionKind.WAIT, "Wait", 12),
        )
        never_reached, _ = _executable(13)
        reason = "cursor_feedback_direction_mismatch_x"
        task = _Task([first, wait, never_reached])
        interface = _ActionInterface(
            _safe_unsent_execution(
                first.action,
                10,
                reason,
                disposition=UnsentActionDisposition.CURSOR_STATE_INVALIDATED,
                cursor_cause=CursorInvalidationCause.UNEXPECTED_DIRECTION,
            )
        )
        changed_canvas = ScreenBounds(
            _CURSOR_CANVAS.x + 1,
            _CURSOR_CANVAS.y,
            _CURSOR_CANVAS.width,
            _CURSOR_CANVAS.height,
        )

        result = TaskRuntime(
            _Client(
                _cursor_observation(10),
                _cursor_observation(12),
                _cursor_observation(13, canvas_bounds=changed_canvas),
            ),
            task,
            _Verifier(None),
            interface,
            sleep=lambda _: None,
        ).run(execute=True)

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual(
            "RuneLite geometry changed after cursor reacquisition",
            result.reason,
        )
        self.assertEqual(2, task.decide_calls)
        self.assertEqual(1, len(interface.calls))
        self.assertEqual(1, len(interface.recovery_calls))

    def test_cursor_recovery_requires_pre_activation_cleanup(self) -> None:
        cases = {
            "incomplete_cleanup": {"complete_cleanup": False},
            "activation_command": {"activation_commands": True},
        }
        for label, options in cases.items():
            with self.subTest(label=label):
                first, _ = _executable(10)
                task = _Task([first])
                interface = _ActionInterface(
                    _safe_unsent_execution(
                        first.action,
                        10,
                        label,
                        disposition=(
                            UnsentActionDisposition.CURSOR_STATE_INVALIDATED
                        ),
                        cursor_cause=(
                            CursorInvalidationCause.UNSUPPORTED_TRANSFER_GAIN
                        ),
                        **options,
                    )
                )
                result = TaskRuntime(
                    _Client(_cursor_observation(10)),
                    task,
                    _Verifier(None),
                    interface,
                    sleep=lambda _: None,
                ).run(execute=True)

                self.assertEqual("BLOCKED", result.status)
                self.assertEqual([], task.discarded)
                self.assertEqual(0, len(interface.recovery_calls))

    def test_failed_cleanup_activation_or_geometry_mismatch_during_recovery_is_terminal(self) -> None:
        mismatched_geometry = replace(
            _CURSOR_GEOMETRY,
            expected_hwnd=_CURSOR_GEOMETRY.expected_hwnd + 1,
        )
        first_template, _ = _executable(10)
        activation_recovery = _safe_unsent_execution(
            first_template.action,
            10,
            "activation_in_recovery",
            disposition=UnsentActionDisposition.CURSOR_STATE_INVALIDATED,
            activation_commands=True,
            cursor_cause=CursorInvalidationCause.UNEXPECTED_DIRECTION,
        ).receipt
        assert activation_recovery is not None
        recovery_receipts = {
            "cleanup": _completed_reacquisition_receipt(
                complete_cleanup=False
            ),
            "activation": activation_recovery,
            "geometry": _completed_reacquisition_receipt(
                mismatched_geometry
            ),
        }
        for label, recovery_receipt in recovery_receipts.items():
            with self.subTest(label=label):
                first, _ = _executable(10)
                reason = "eligible_initial_invalidation"
                task = _Task([first])
                interface = _ActionInterface(
                    _safe_unsent_execution(
                        first.action,
                        10,
                        reason,
                        disposition=(
                            UnsentActionDisposition.CURSOR_STATE_INVALIDATED
                        ),
                        cursor_cause=(
                            CursorInvalidationCause.POINT_OWNER_MISMATCH
                        ),
                    ),
                    recovery_result=recovery_receipt,
                )
                result = TaskRuntime(
                    _Client(_cursor_observation(10)),
                    task,
                    _Verifier(None),
                    interface,
                    sleep=lambda _: None,
                ).run(execute=True)

                self.assertEqual("BLOCKED", result.status)
                self.assertIn(
                    "did not prove unchanged geometry and complete cleanup",
                    result.reason,
                )
                self.assertEqual([reason], task.discarded)
                self.assertEqual(1, len(interface.recovery_calls))

    def test_unsent_replan_requires_typed_disposition_cleanup_and_no_activation(self) -> None:
        reason = "fresh_input_validation_denied: hover_menu_mismatch"
        factories = (
            lambda action: _safe_unsent_execution(
                action,
                10,
                reason,
                disposition=UnsentActionDisposition.NONE,
            ),
            lambda action: _safe_unsent_execution(
                action,
                10,
                reason,
                complete_cleanup=False,
            ),
            lambda action: _safe_unsent_execution(
                action,
                10,
                reason,
                activation_commands=True,
            ),
            lambda action: _safe_unsent_execution(
                action,
                10,
                reason,
                disposition=UnsentActionDisposition.CURSOR_STATE_INVALIDATED,
                receipt_failure_kind=InputFailureKind.NONE,
            ),
            lambda action: replace(
                _blocked_execution(action, 10, reason),
                unsent_disposition=(
                    UnsentActionDisposition.CURSOR_STATE_INVALIDATED
                ),
            ),
        )
        for factory in factories:
            with self.subTest(factory=factory):
                decision, _ = _executable(10)
                task = _Task([decision])
                runtime = TaskRuntime(
                    _Client(_observation(10)),
                    task,
                    _Verifier(None),
                    _ActionInterface(factory(decision.action)),
                    sleep=lambda _: None,
                )

                result = runtime.run(execute=True)

                self.assertEqual("BLOCKED", result.status)
                self.assertEqual([], task.discarded)
                self.assertEqual(1, len(task.applied))

    def test_action_interface_exception_applies_one_typed_failure(self) -> None:
        decision, _ = _executable(10)
        task = _Task([decision])
        interface = _RaisingActionInterface()
        runtime = TaskRuntime(
            _Client(_observation(10)),
            task,
            _Verifier(None),
            interface,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual(1, interface.calls)
        self.assertEqual(1, len(task.applied))
        self.assertIs(task.applied[0].status, VerificationStatus.FAIL)
        self.assertIn("serial boundary failed", task.applied[0].reason)

    def test_executable_without_verification_never_reaches_interface(self) -> None:
        decision = Decision(
            "unsafe_output",
            "task forgot verification",
            Action(ActionKind.INTERACT_OBJECT, "Unsafe action", 10),
        )
        task = _Task([decision])
        interface = _ActionInterface(
            _sent_execution(decision.action, 10)
        )
        runtime = TaskRuntime(
            _Client(_observation(10)),
            task,
            _Verifier(None),
            interface,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("ERROR", result.status)
        self.assertEqual([], interface.calls)
        self.assertEqual(0, result.actions)
        self.assertEqual(1, len(task.applied))
        self.assertIs(task.applied[0].status, VerificationStatus.FAIL)
        self.assertEqual("action omitted verification", task.applied[0].reason)

    def test_live_mode_waits_for_focus_before_mutating_the_task(self) -> None:
        decision, _ = _executable(11)
        task = _Task([decision])
        execution = _blocked_execution(decision.action, 11, "test stop")
        runtime = TaskRuntime(
            _Client(
                replace(_observation(10), client_focused=False),
                _observation(11),
            ),
            task,
            _Verifier(None),
            _ActionInterface(execution),
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual(1, task.decide_calls)
        self.assertEqual(2, result.observations)

    def test_live_mode_gives_each_transient_focus_loss_a_bounded_recovery_window(self) -> None:
        decision, _ = _executable(10)
        complete = _wait(13, "complete")
        task = _Task([decision, complete])
        execution = _sent_execution(decision.action, 10, 12)
        passed = VerificationResult(
            VerificationStatus.PASS,
            "arrived",
            Outcome(OutcomeKind.MOVED_CLOSER, 12),
        )
        clock_values = iter((0.0, 1.0, 20.0, 21.0, 21.1, 21.2, 21.3, 21.4))
        runtime = TaskRuntime(
            _Client(
                _observation(10),
                replace(_observation(11), client_focused=False),
                _observation(12),
                _observation(13),
            ),
            task,
            _Verifier(passed),
            _ActionInterface(execution),
            sleep=lambda _: None,
            clock=lambda: next(clock_values),
            configuration=replace(
                DEFAULT_RUNTIME_CONFIG,
                max_runtime_seconds=100.0,
            ),
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual(1, result.actions)
        self.assertEqual([passed], task.applied)

    def test_sent_action_requires_external_typed_verification(self) -> None:
        decision, _ = _executable(10)
        complete = _wait(12, "complete")
        task = _Task([decision, complete])
        execution = _sent_execution(decision.action, 10, 11)
        passed = VerificationResult(
            VerificationStatus.PASS,
            "log_gained",
            Outcome(OutcomeKind.ITEM_QUANTITY_INCREASED, 11),
        )
        runtime = TaskRuntime(
            _Client(_observation(10), _observation(11), _observation(12)),
            task,
            _Verifier(passed),
            _ActionInterface(execution),
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual([passed], task.applied)
        self.assertEqual(1, result.actions)
        self.assertEqual(3, result.observations)
        self.assertIs(result.task_snapshot.status, TaskStatus.COMPLETE)

    def test_verification_tick_budget_starts_after_pre_activation_revalidation(self) -> None:
        decision, original = _executable(10)
        task = _Task([decision, _wait(20, "complete")])
        execution = _sent_execution(decision.action, 10, 13)
        gained_log = replace(
            _observation(19),
            inventory=InventoryObservation(
                items=(InventoryItem(slot=0, item_id=1511, quantity=1),),
                occupied_slots=1,
                free_slots=27,
                known=True,
            ),
        )
        publisher = _RecordingPublisher()
        runtime = TaskRuntime(
            _Client(_observation(10), gained_log, _observation(20)),
            task,
            Verifier(max_observation_age_seconds=10.0),
            _ActionInterface(execution),
            frame_publisher=publisher,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        executed = next(
            frame for frame in publisher.frames if frame.stage is EngineStage.EXECUTED
        )
        self.assertEqual(13, executed.pending_verification.before_tick)
        self.assertEqual(21, executed.pending_verification.deadline_tick)
        self.assertEqual(10, original.before_tick)
        self.assertEqual(18, original.deadline_tick)

    def test_missing_post_move_tick_keeps_original_verification_window(self) -> None:
        decision, original = _executable(10)
        complete = _wait(12, "complete")
        task = _Task([decision, complete])
        execution = _sent_execution(decision.action, 10, None)
        passed = VerificationResult(
            VerificationStatus.PASS,
            "log_gained",
            Outcome(OutcomeKind.ITEM_QUANTITY_INCREASED, 11),
        )
        publisher = _RecordingPublisher()
        runtime = TaskRuntime(
            _Client(_observation(10), _observation(11), _observation(12)),
            task,
            _Verifier(passed),
            _ActionInterface(execution),
            frame_publisher=publisher,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        executed = next(
            frame for frame in publisher.frames if frame.stage is EngineStage.EXECUTED
        )
        self.assertIs(original, executed.pending_verification)

    def test_rebased_walk_window_accepts_the_observed_late_arrival(self) -> None:
        before = WorldPoint(3195, 3248, 0)
        target = WorldPoint(3200, 3238, 0)
        arrived = WorldPoint(3200, 3239, 0)
        verification = VerificationSpec(
            VerificationKind.MOVED_CLOSER,
            before_tick=817,
            deadline_tick=825,
            before_location=before,
            target_location=target,
            target_radius=1,
            source_session_id="runtime-session",
        )
        action = Action(
            ActionKind.WALK,
            "Walk to west_approach_bridge",
            817,
            verification=verification,
        )
        task = _Task([
            Decision("navigate_to_bank", "walk fixed route step", action),
            _wait(827, "complete"),
        ])
        publisher = _RecordingPublisher()
        runtime = TaskRuntime(
            _Client(
                replace(_observation(817), location=before),
                replace(_observation(825), location=before),
                replace(_observation(826), location=arrived),
                replace(_observation(827), location=arrived),
            ),
            task,
            Verifier(max_observation_age_seconds=10.0),
            _ActionInterface(_sent_execution(action, 817, 820)),
            frame_publisher=publisher,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual(OutcomeKind.ARRIVED, task.applied[0].outcome.kind)
        self.assertEqual(826, task.applied[0].outcome.observed_tick)
        executed = next(
            frame for frame in publisher.frames if frame.stage is EngineStage.EXECUTED
        )
        effective = executed.pending_verification
        self.assertEqual((820, 828), (effective.before_tick, effective.deadline_tick))
        self.assertEqual(before, effective.before_location)
        self.assertEqual(target, effective.target_location)
        self.assertEqual(1, effective.target_radius)
        self.assertEqual("runtime-session", effective.source_session_id)

    def test_rebased_walk_window_still_fails_at_its_effective_deadline(self) -> None:
        before = WorldPoint(3195, 3248, 0)
        target = WorldPoint(3200, 3238, 0)
        verification = VerificationSpec(
            VerificationKind.MOVED_CLOSER,
            before_tick=817,
            deadline_tick=825,
            before_location=before,
            target_location=target,
            target_radius=1,
            source_session_id="runtime-session",
        )
        action = Action(ActionKind.WALK, "Walk", 817, verification=verification)
        task = _Task([
            Decision("navigate_to_bank", "walk fixed route step", action),
        ])
        runtime = TaskRuntime(
            _Client(
                replace(_observation(817), location=before),
                replace(_observation(825), location=before),
                replace(_observation(828), location=before),
            ),
            task,
            Verifier(max_observation_age_seconds=10.0),
            _ActionInterface(_sent_execution(action, 817, 820)),
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual("condition_unmet_at_deadline", task.applied[0].reason)
        self.assertIs(
            VerificationFailureKind.CONDITION_UNMET_AT_DEADLINE,
            task.applied[0].failure_kind,
        )
        self.assertEqual(828, result.last_tick)

    def test_engine_frame_retains_real_receipt_outcome_and_terminal_state(self) -> None:
        decision, verification = _executable(10)
        complete = _wait(12, "complete")
        task = _Task([decision, complete])
        execution = _sent_execution(decision.action, 10, 11)
        passed = VerificationResult(
            VerificationStatus.PASS,
            "log_gained",
            Outcome(OutcomeKind.ITEM_QUANTITY_INCREASED, 11),
        )
        publisher = _RecordingPublisher()
        runtime = TaskRuntime(
            _Client(_observation(10), _observation(11), _observation(12)),
            task,
            _Verifier(passed),
            _ActionInterface(execution),
            frame_publisher=publisher,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        stages = tuple(frame.stage for frame in publisher.frames)
        self.assertIs(EngineStage.STARTING, stages[0])
        self.assertIsNone(publisher.frames[0].observation)
        self.assertIsNone(publisher.frames[0].decision)
        self.assertIn(EngineStage.OBSERVED, stages)
        self.assertIn(EngineStage.DECIDED, stages)
        self.assertIn(EngineStage.EXECUTED, stages)
        self.assertIn(EngineStage.VERIFYING, stages)
        self.assertIn(EngineStage.VERIFIED, stages)
        self.assertIs(EngineStage.TERMINAL, stages[-1])
        self.assertIs(result.engine_frame, publisher.latest())
        self.assertIs(result.execution, execution)
        self.assertIs(result.engine_frame.last_execution_receipt, execution.receipt)
        self.assertIs(result.engine_frame.last_verification, passed)
        self.assertIsNone(result.engine_frame.pending_verification)
        self.assertTrue(result.engine_frame.cleanup.safe)
        self.assertEqual(12, result.engine_frame.observation.source_tick)
        self.assertIs(result.engine_frame.task.status, TaskStatus.COMPLETE)
        payload = result.to_dict()
        self.assertTrue(payload["execution"]["receipt"]["wireProofComplete"])
        self.assertEqual("terminal", payload["engineFrame"]["stage"])

    def test_failed_verification_is_applied_once_and_blocks(self) -> None:
        decision, _ = _executable(10)
        task = _Task([decision])
        execution = _sent_execution(decision.action, 10, 11)
        failed = VerificationResult(
            VerificationStatus.FAIL,
            "deadline_exceeded",
            failure_kind=VerificationFailureKind.DEADLINE_EXCEEDED,
        )
        runtime = TaskRuntime(
            _Client(_observation(10), _observation(11)),
            task,
            _Verifier(failed),
            _ActionInterface(execution),
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual([failed], task.applied)
        self.assertEqual("deadline_exceeded", result.task_snapshot.blocker)

    def test_typed_item_unchanged_failure_may_reobserve_after_task_recovery(self) -> None:
        decision, _ = _executable(10)
        complete = Decision(
            "complete",
            "resource retry later completed",
            Action(ActionKind.WAIT, "Wait", 12),
        )
        task = _RecoveringVerificationTask([decision, complete])
        failed = VerificationResult(
            VerificationStatus.FAIL,
            "item_quantity_unchanged_at_deadline",
            failure_kind=(
                VerificationFailureKind.ITEM_QUANTITY_UNCHANGED_AT_DEADLINE
            ),
        )
        runtime = TaskRuntime(
            _Client(_observation(10), _observation(11), _observation(12)),
            task,
            _Verifier(failed),
            _ActionInterface(_sent_execution(decision.action, 10, 11)),
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual([failed], task.applied)
        self.assertEqual(1, result.actions)

    def test_vertical_camera_noop_may_reobserve_after_task_suppresses_pitch(self) -> None:
        decision, _ = _camera_executable(10)
        complete = Decision(
            "complete",
            "camera fallback later completed",
            Action(ActionKind.WAIT, "Wait", 12),
        )
        task = _RecoveringCameraVerificationTask([decision, complete])
        failed = VerificationResult(
            VerificationStatus.FAIL,
            "condition_unmet_at_deadline",
            failure_kind=VerificationFailureKind.CONDITION_UNMET_AT_DEADLINE,
        )
        interface = _ActionInterface(_sent_execution(decision.action, 10, 11))
        runtime = TaskRuntime(
            _Client(_observation(10), _observation(11), _observation(12)),
            task,
            _Verifier(failed),
            interface,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual([failed], task.applied)
        self.assertEqual("complete", result.task_snapshot.state)
        self.assertEqual(1, result.actions)
        self.assertEqual(1, len(interface.calls))

    def test_nonrecoverable_or_complete_failure_transition_is_contract_error(self) -> None:
        failures = (
            VerificationResult(
                VerificationStatus.FAIL,
                "session_changed",
                failure_kind=VerificationFailureKind.SESSION_CHANGED,
            ),
            VerificationResult(
                VerificationStatus.FAIL,
                "item_quantity_unchanged_at_deadline",
                failure_kind=(
                    VerificationFailureKind.ITEM_QUANTITY_UNCHANGED_AT_DEADLINE
                ),
            ),
            VerificationResult(
                VerificationStatus.FAIL,
                "condition_unmet_at_deadline",
                failure_kind=(
                    VerificationFailureKind.CONDITION_UNMET_AT_DEADLINE
                ),
            ),
        )
        for failure, complete_after_failure in (
            (failures[0], False),
            (failures[1], True),
            (failures[2], False),
        ):
            with self.subTest(
                failure=failure.failure_kind,
                complete=complete_after_failure,
            ):
                decision, _ = _executable(10)
                task = _FaultyFailureTask(
                    [decision],
                    complete=complete_after_failure,
                )
                runtime = TaskRuntime(
                    _Client(_observation(10), _observation(11)),
                    task,
                    _Verifier(failure),
                    _ActionInterface(_sent_execution(decision.action, 10, 11)),
                    sleep=lambda _: None,
                )

                result = runtime.run(execute=True)

                self.assertEqual("ERROR", result.status)
                self.assertIn("non-recoverable", result.reason)

    def test_observation_failure_is_reported_without_action(self) -> None:
        task = _Task([_wait(1)])
        runtime = TaskRuntime(
            _Client(RuntimeError("endpoint down")),
            task,
            _Verifier(None),
            sleep=lambda _: None,
        )

        result = runtime.run()

        self.assertEqual("ERROR", result.status)
        self.assertIn("endpoint down", result.reason)
        self.assertEqual(0, result.actions)
        self.assertEqual([], task.applied)

    def test_active_run_blocks_before_decision_when_pid_or_session_changes(self) -> None:
        first = _observation(1)
        changed = replace(
            _observation(2),
            client_process_id=4321,
            session_id="replacement-session",
        )
        task = _Task([_wait(1), _wait(2)])
        runtime = TaskRuntime(
            _Client(first, changed),
            task,
            _Verifier(None),
            configuration=replace(DEFAULT_RUNTIME_CONFIG, max_observations=2),
            sleep=lambda _: None,
        )

        result = runtime.run(
            execute=False,
            expected_process_id=1234,
            expected_session_id="runtime-session",
        )

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("PID/session identity changed", result.reason)
        self.assertEqual(2, result.observations)
        self.assertEqual(1, task.decide_calls)
        self.assertEqual(0, result.actions)

    def test_waiting_is_bounded_by_observation_limit(self) -> None:
        task = _Task([_wait(1), _wait(2)])
        runtime = TaskRuntime(
            _Client(_observation(1), _observation(2)),
            task,
            _Verifier(None),
            configuration=replace(DEFAULT_RUNTIME_CONFIG, max_observations=2),
            sleep=lambda _: None,
        )

        result = runtime.run()

        self.assertEqual("LIMIT", result.status)
        self.assertEqual(2, result.observations)

    def test_default_budget_fits_the_supported_full_cycle(self) -> None:
        task = _LongCycleTask()
        runtime = TaskRuntime(
            _IncrementingClient(),
            task,
            _PendingThenPassVerifier(),
            _AlwaysSentInterface(),
            sleep=lambda _: None,
            clock=lambda: 0.0,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual(64, result.actions)
        self.assertGreater(result.observations, 240)
        self.assertEqual(64, task.completed)

    def test_safe_stop_before_observation_never_fetches_or_decides(self) -> None:
        control = RuntimeControl()
        control.request_safe_stop()
        client = _Client(_observation(10))
        task = _Task([_wait(10)])
        runtime = TaskRuntime(
            client,
            task,
            _Verifier(None),
            control=control,
            sleep=lambda _: None,
        )

        result = runtime.run()

        self.assertEqual("SAFE_STOPPED", result.status)
        self.assertTrue(result.successful)
        self.assertEqual([], client.requests)
        self.assertEqual(0, task.decide_calls)
        self.assertEqual("SAFE_STOPPED", runtime.statistics().status)
        self.assertFalse(runtime.statistics().active)

    def test_pause_after_observation_discards_it_and_refetches(self) -> None:
        control = RuntimeControl()

        class PausePublisher(_RecordingPublisher):
            requested = False

            def publish(self, **values):
                frame = super().publish(**values)
                if frame.stage is EngineStage.OBSERVED and not self.requested:
                    self.requested = True
                    control.request_pause()
                return frame

        decision, _ = _executable(11)
        task = _Task([decision])
        runtime = TaskRuntime(
            _Client(_observation(10), _observation(11)),
            task,
            _Verifier(None),
            frame_publisher=PausePublisher(),
            control=control,
            sleep=lambda _: None,
        )
        results: list[object] = []
        worker = threading.Thread(target=lambda: results.append(runtime.run()))
        worker.start()

        self.assertTrue(control.wait_for_state(RuntimeControlState.PAUSED, 1.0))
        self.assertEqual(0, task.decide_calls)
        control.resume()
        worker.join(2.0)

        self.assertFalse(worker.is_alive())
        result = results[0]
        self.assertEqual("DRY_RUN", result.status)
        self.assertEqual(2, result.observations)
        self.assertEqual(1, task.decide_calls)
        self.assertEqual(11, result.decision.action.source_tick)

    def test_stop_requested_during_decide_finishes_action_verification(self) -> None:
        control = RuntimeControl()
        decision, _ = _executable(10)

        class StopDuringDecisionTask(_Task):
            def decide(self, observation: Observation) -> Decision:
                result = super().decide(observation)
                control.request_safe_stop()
                return result

        task = StopDuringDecisionTask([decision])
        execution = _sent_execution(decision.action, 10, 11)
        interface = _ActionInterface(execution)
        passed = VerificationResult(
            VerificationStatus.PASS,
            "log gained",
            Outcome(OutcomeKind.ITEM_QUANTITY_INCREASED, 11),
        )
        runtime = TaskRuntime(
            _Client(_observation(10), _observation(11)),
            task,
            _Verifier(passed),
            interface,
            control=control,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("SAFE_STOPPED", result.status)
        self.assertEqual(1, result.actions)
        self.assertEqual([passed], task.applied)
        self.assertEqual(1, len(interface.calls))
        self.assertIs(result.execution, execution)
        self.assertTrue(result.execution.cleanup_confirmed)
        self.assertIsNone(result.engine_frame.pending_verification)
        self.assertEqual("SAFE_STOPPED", runtime.statistics().status)

    def test_pause_requested_during_decide_waits_for_action_verification(self) -> None:
        control = RuntimeControl()
        decision, _ = _executable(10)

        class PauseDuringDecisionTask(_Task):
            def decide(self, observation: Observation) -> Decision:
                result = super().decide(observation)
                control.request_pause()
                return result

        task = PauseDuringDecisionTask([decision])
        execution = _sent_execution(decision.action, 10, 11)
        interface = _ActionInterface(execution)
        passed = VerificationResult(
            VerificationStatus.PASS,
            "log gained",
            Outcome(OutcomeKind.ITEM_QUANTITY_INCREASED, 11),
        )
        runtime = TaskRuntime(
            _Client(_observation(10), _observation(11)),
            task,
            _Verifier(passed),
            interface,
            control=control,
            sleep=lambda _: None,
        )
        results: list[object] = []
        worker = threading.Thread(target=lambda: results.append(runtime.run(execute=True)))
        worker.start()

        self.assertTrue(control.wait_for_state(RuntimeControlState.PAUSED, 1.0))
        self.assertEqual([passed], task.applied)
        self.assertEqual(1, len(interface.calls))
        self.assertTrue(execution.cleanup_confirmed)
        self.assertIsNone(runtime.frame_publisher.latest().pending_verification)
        control.request_safe_stop()
        worker.join(2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual("SAFE_STOPPED", results[0].status)

    def test_safe_stop_wakes_a_paused_runtime(self) -> None:
        control = RuntimeControl()
        control.request_pause()
        runtime = TaskRuntime(
            _Client(_observation(10)),
            _Task([_wait(10)]),
            _Verifier(None),
            control=control,
            sleep=lambda _: None,
        )
        results: list[object] = []
        worker = threading.Thread(target=lambda: results.append(runtime.run()))
        worker.start()

        self.assertTrue(control.wait_for_state(RuntimeControlState.PAUSED, 1.0))
        control.request_safe_stop()
        worker.join(2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual("SAFE_STOPPED", results[0].status)

    def test_pause_cannot_extend_the_hard_runtime_bound(self) -> None:
        control = RuntimeControl()
        control.request_pause()
        configuration = RuntimeConfig(
            poll_seconds=0.01,
            max_observations=2,
            max_actions=1,
            max_runtime_seconds=0.05,
            verification_timeout_seconds=0.02,
        )
        runtime = TaskRuntime(
            _Client(_observation(10)),
            _Task([_wait(10)]),
            _Verifier(None),
            configuration=configuration,
            control=control,
            sleep=lambda _: None,
        )

        started = time.monotonic()
        result = runtime.run()

        self.assertEqual("LIMIT", result.status)
        self.assertIn("while paused", result.reason)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_runtime_has_no_concrete_task_or_progress_dependency(self) -> None:
        runtime_path = Path(__file__).parents[1] / "osrs_bot" / "runtime.py"
        source = runtime_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        forbidden_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and (
                node.module == "osrs_bot.task"
                or (node.level > 0 and node.module == "task")
            )
        ]
        progress_accesses = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "progress"
        ]
        self.assertEqual([], forbidden_imports)
        self.assertEqual([], progress_accesses)
        self.assertNotIn("TaskPhase", source)
        self.assertNotIn("WoodcutBankTask", source)


if __name__ == "__main__":
    unittest.main()

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
    FirmwareSafetyStatus,
    InputReceipt,
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
    ScreenBounds,
    ScreenPoint,
    TargetGeometry,
    VerificationKind,
    VerificationSpec,
    WidgetObservation,
    WorldPoint,
)
from osrs_bot.runtime import RuntimeControl, RuntimeControlState, TaskRuntime
from osrs_bot.safety import SafetyGate
from osrs_bot.task_contract import (
    Decision,
    ObservationRequest,
    TaskSnapshot,
    TaskStatus,
)
from osrs_bot.verification import (
    Outcome,
    OutcomeKind,
    VerificationResult,
    VerificationStatus,
    Verifier,
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
) -> ExecutionResult:
    command_names = (
        ("ARM", "MOVE", "MOUSE_DOWN", "MOUSE_UP", "STOP_ALL", "DISARM", "STATUS")
        if activation_commands
        else ("ARM", "MOVE", "STOP_ALL", "DISARM", "STATUS")
    )
    commands = tuple(
        _command(sequence, name)
        for sequence, name in enumerate(command_names, start=1)
    )
    receipt = InputReceipt(
        transaction_id="input-00000002",
        mode="adaptive_pointer",
        intent_ids=("runtime-unsent-test",),
        status="BLOCKED",
        reason=reason,
        connected=True,
        arm_acknowledged=True,
        stop_all_acknowledged=complete_cleanup,
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
    )


class _Client:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.requests: list[tuple[tuple[str, WorldPoint], ...]] = []

    def fetch(self, tiles):
        self.requests.append(tuple(tiles))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _Task:
    def __init__(
        self,
        decisions: list[Decision],
        *,
        projections: tuple[tuple[str, WorldPoint], ...] = (),
    ) -> None:
        self.decisions = list(decisions)
        self.projections = projections
        self.applied: list[VerificationResult] = []
        self.discarded: list[str] = []
        self.decide_calls = 0
        self.status = TaskStatus.RUNNING
        self.state = "ready"
        self.blocker: str | None = None

    def observation_request(self) -> ObservationRequest:
        return ObservationRequest(self.projections)

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

    def discard_pending_action(self, reason: str) -> None:
        self.discarded.append(reason)
        self.state = "replan"

    def snapshot(self) -> TaskSnapshot:
        return TaskSnapshot("fake-task", self.status, self.state, self.blocker)


class _Verifier:
    def __init__(self, result: VerificationResult | None) -> None:
        self.result = result

    def evaluate(self, _specification, _observation):
        return self.result


class _ActionInterface:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.calls: list[tuple[Action, Observation]] = []

    def execute(self, action: Action, observation: Observation) -> ExecutionResult:
        self.calls.append((action, observation))
        return self.result


class _SequencedActionInterface:
    def __init__(self, *results: ExecutionResult) -> None:
        self.results = list(results)
        self.calls: list[tuple[Action, Observation]] = []

    def execute(self, action: Action, observation: Observation) -> ExecutionResult:
        self.calls.append((action, observation))
        return self.results.pop(0)


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

    def fetch(self, _tiles) -> Observation:
        self.tick += 1
        return _observation(self.tick)


class _LongCycleTask:
    """Budget model: 28 chops plus 24 route/bank/return actions."""

    def __init__(self, action_count: int = 52) -> None:
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


class TaskRuntimeTests(unittest.TestCase):
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
            [decision], projections=(("route:x", WorldPoint(1, 2, 0)),)
        )
        runtime = TaskRuntime(client, task, _Verifier(None), sleep=lambda _: None)

        result = runtime.run(execute=False)

        self.assertEqual("DRY_RUN", result.status)
        self.assertEqual(0, result.actions)
        self.assertEqual(task.projections, client.requests[0])
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
        self.assertEqual("deadline_exceeded", task.applied[0].reason)
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
        failed = VerificationResult(VerificationStatus.FAIL, "deadline_exceeded")
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
        self.assertEqual(52, result.actions)
        self.assertGreater(result.observations, 240)
        self.assertEqual(52, task.completed)

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

from __future__ import annotations

import ast
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from osrs_bot.action import ExecutionResult
from osrs_bot.configuration import DEFAULT_RUNTIME_CONFIG
from osrs_bot.model import (
    Action,
    ActionKind,
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
from osrs_bot.runtime import TaskRuntime
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
            return ExecutionResult("BLOCKED", pre.reason, action, observation.tick)
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
            return ExecutionResult("BLOCKED", after_move.reason, action, observation.tick)
        return ExecutionResult(
            "SENT",
            "safety_checked_transport_stub",
            action,
            observation.tick,
            post_tick,
            stop_all_confirmed=True,
            disarm_confirmed=True,
        )


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
        return ExecutionResult(
            "SENT",
            "action_sent",
            action,
            observation.tick,
            observation.tick + 1,
            stop_all_confirmed=True,
            disarm_confirmed=True,
        )


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
        execution = ExecutionResult(
            "BLOCKED", "hover_menu_mismatch", decision.action, 10
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
        self.assertEqual(1, len(task.applied))
        self.assertIs(task.applied[0].status, VerificationStatus.FAIL)
        self.assertEqual(
            "execution blocked: hover_menu_mismatch", task.applied[0].reason
        )
        self.assertIsNone(task.applied[0].outcome)
        self.assertEqual(1, len(interface.calls))

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
            ExecutionResult("SENT", "should_not_run", decision.action, 10)
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
        execution = ExecutionResult("BLOCKED", "test stop", decision.action, 11)
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
        execution = ExecutionResult(
            "SENT",
            "action_sent",
            decision.action,
            10,
            11,
            stop_all_confirmed=True,
            disarm_confirmed=True,
        )
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

    def test_failed_verification_is_applied_once_and_blocks(self) -> None:
        decision, _ = _executable(10)
        task = _Task([decision])
        execution = ExecutionResult(
            "SENT", "action_sent", decision.action, 10, 11
        )
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

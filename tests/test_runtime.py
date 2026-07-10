from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from osrs_bot.action import ExecutionResult
from osrs_bot.model import (
    Action,
    ActionKind,
    Decision,
    InventoryObservation,
    Observation,
    PlayerObservation,
    TaskPhase,
    TaskProgress,
    Verification,
    VerificationKind,
    WidgetObservation,
    WorldPoint,
)
from osrs_bot.runtime import TaskRuntime
from osrs_bot.verification import VerificationResult, VerificationStatus


def _observation(tick: int) -> Observation:
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
        timestamp=datetime.now(timezone.utc),
        tick=tick,
        status="PASS",
        fresh=True,
        cache_wall_clock_fresh=True,
        scene_playable=True,
        client_focused=True,
        client_process_id=1234,
    )


class _Client:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.requests = []

    def fetch(self, tiles):
        self.requests.append(tuple(tiles))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _Task:
    def __init__(self, decisions, *, projections=()) -> None:
        self.decisions = list(decisions)
        self.projections = projections
        self.progress = TaskProgress()
        self.applied = []
        self.decide_calls = 0

    def requested_tile_projections(self):
        return self.projections

    def decide(self, observation):
        self.decide_calls += 1
        return self.decisions.pop(0)

    def apply_verification(self, passed, reason):
        self.applied.append((passed, reason))
        self.progress.pending = None
        self.progress.phase = TaskPhase.COMPLETE if passed else TaskPhase.BLOCKED


class _Verifier:
    def __init__(self, result) -> None:
        self.result = result

    def evaluate(self, specification, observation):
        return self.result


class _ActionInterface:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = []

    def execute(self, action, observation):
        self.calls.append((action, observation))
        return self.result


class _IncrementingClient:
    def __init__(self) -> None:
        self.tick = 0

    def fetch(self, _tiles):
        self.tick += 1
        return _observation(self.tick)


class _LongCycleTask:
    """Budget model: 28 chops plus 24 route/bank/return actions."""

    def __init__(self, action_count: int = 52) -> None:
        self.action_count = action_count
        self.completed = 0
        self.progress = TaskProgress()

    def requested_tile_projections(self):
        return ()

    def decide(self, observation):
        if self.completed >= self.action_count:
            self.progress.phase = TaskPhase.COMPLETE
            return _wait(observation.tick, TaskPhase.COMPLETE)
        verification = Verification(
            VerificationKind.LOG_GAINED,
            before_tick=observation.tick,
            deadline_tick=observation.tick + 20,
            before_log_count=self.completed,
            source_session_id="session-1",
        )
        self.progress.phase = TaskPhase.VERIFY_LOGS
        self.progress.pending = verification
        action = Action(
            ActionKind.INTERACT_OBJECT,
            f"cycle action {self.completed + 1}",
            observation.tick,
            verification=verification,
        )
        return Decision(TaskPhase.VERIFY_LOGS, "advance supported cycle", action)

    def apply_verification(self, passed, _reason):
        self.progress.pending = None
        if passed:
            self.completed += 1
        else:
            self.progress.phase = TaskPhase.BLOCKED


class _PendingThenPassVerifier:
    def __init__(self, pending_per_action: int = 5) -> None:
        self.pending_per_action = pending_per_action
        self.calls = 0

    def evaluate(self, _specification, _observation):
        self.calls += 1
        if self.calls % (self.pending_per_action + 1):
            return VerificationResult(VerificationStatus.PENDING, "pathing")
        return VerificationResult(VerificationStatus.PASS, "verified")


class _AlwaysSentInterface:
    def execute(self, action, observation):
        return ExecutionResult(
            "SENT", "action_sent", action, observation.tick, observation.tick + 1,
            stop_all_confirmed=True, disarm_confirmed=True,
        )


def _wait(tick: int, phase: TaskPhase = TaskPhase.FIND_TREE) -> Decision:
    return Decision(phase, "wait", Action(ActionKind.WAIT, "Wait", tick))


def _executable(tick: int) -> tuple[Decision, Verification]:
    verification = Verification(
        VerificationKind.LOG_GAINED,
        before_tick=tick,
        deadline_tick=tick + 8,
        before_log_count=0,
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
    return Decision(TaskPhase.VERIFY_LOGS, "chop", action), verification


class TaskRuntimeTests(unittest.TestCase):
    def test_dry_run_stops_at_first_executable_action(self) -> None:
        decision, _ = _executable(10)
        client = _Client(_observation(10))
        task = _Task([decision], projections=(("route:x", WorldPoint(1, 2, 0)),))
        runtime = TaskRuntime(client, task, _Verifier(None), sleep=lambda _: None)

        result = runtime.run(execute=False)

        self.assertEqual("DRY_RUN", result.status)
        self.assertEqual(0, result.actions)
        self.assertEqual(task.projections, client.requests[0])
        self.assertEqual("interact_object", result.to_dict()["decision"]["action"]["kind"])

    def test_failed_execution_blocks_pending_task(self) -> None:
        decision, verification = _executable(10)
        task = _Task([decision])
        task.progress.phase = TaskPhase.VERIFY_LOGS
        task.progress.pending = verification
        execution = ExecutionResult("BLOCKED", "hover_menu_mismatch", decision.action, 10)
        interface = _ActionInterface(execution)
        runtime = TaskRuntime(
            _Client(_observation(10)), task, _Verifier(None), interface,
            sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual([(False, "execution blocked: hover_menu_mismatch")], task.applied)
        self.assertEqual(1, len(interface.calls))

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

    def test_sent_action_requires_external_verification(self) -> None:
        decision, verification = _executable(10)
        complete = _wait(12, TaskPhase.COMPLETE)
        task = _Task([decision, complete])
        task.progress.phase = TaskPhase.VERIFY_LOGS
        task.progress.pending = verification
        execution = ExecutionResult(
            "SENT", "action_sent", decision.action, 10, 11,
            stop_all_confirmed=True, disarm_confirmed=True,
        )
        verifier = _Verifier(VerificationResult(VerificationStatus.PASS, "log_gained"))
        runtime = TaskRuntime(
            _Client(_observation(10), _observation(11), _observation(12)),
            task, verifier, _ActionInterface(execution), sleep=lambda _: None,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual([(True, "log_gained")], task.applied)
        self.assertEqual(1, result.actions)
        self.assertEqual(3, result.observations)

    def test_observation_failure_is_reported_without_action(self) -> None:
        task = _Task([_wait(1)])
        runtime = TaskRuntime(
            _Client(RuntimeError("endpoint down")), task, _Verifier(None),
            sleep=lambda _: None,
        )

        result = runtime.run()

        self.assertEqual("ERROR", result.status)
        self.assertIn("endpoint down", result.reason)
        self.assertEqual(0, result.actions)

    def test_waiting_is_bounded_by_observation_limit(self) -> None:
        task = _Task([_wait(1), _wait(2)])
        runtime = TaskRuntime(
            _Client(_observation(1), _observation(2)), task, _Verifier(None),
            max_observations=2, sleep=lambda _: None,
        )

        result = runtime.run()

        self.assertEqual("LIMIT", result.status)
        self.assertEqual(2, result.observations)

    def test_default_budget_fits_the_supported_full_cycle(self) -> None:
        task = _LongCycleTask()
        runtime = TaskRuntime(
            _IncrementingClient(), task, _PendingThenPassVerifier(),
            _AlwaysSentInterface(), sleep=lambda _: None, clock=lambda: 0.0,
        )

        result = runtime.run(execute=True)

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual(52, result.actions)
        self.assertGreater(result.observations, 240)
        self.assertEqual(52, task.completed)


if __name__ == "__main__":
    unittest.main()

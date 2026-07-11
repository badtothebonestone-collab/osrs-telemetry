from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from osrs_bot.model import Action, ActionKind, Observation, WorldPoint
from osrs_bot.task_contract import (
    Decision,
    ObservationRequest,
    Task,
    TaskSnapshot,
    TaskStatus,
)
from osrs_bot.verification import VerificationResult, VerificationStatus


def _wait_action() -> Action:
    return Action(kind=ActionKind.WAIT, label="wait", source_tick=7)


class _FakeTask:
    def __init__(self) -> None:
        self.verification: VerificationResult | None = None

    def observation_request(self) -> ObservationRequest:
        return ObservationRequest((("target", WorldPoint(3200, 3201, 0)),))

    def decide(self, observation: Observation) -> Decision:
        return Decision(state="waiting", reason="awaiting_observation", action=_wait_action())

    def apply_verification(self, result: VerificationResult) -> None:
        self.verification = result

    def snapshot(self) -> TaskSnapshot:
        return TaskSnapshot("fake-task", TaskStatus.RUNNING, "waiting")


class TaskContractTests(unittest.TestCase):
    def test_contract_values_are_frozen(self) -> None:
        request = ObservationRequest((("target", WorldPoint(3200, 3201, 0)),))
        decision = Decision("waiting", "no_action_needed", _wait_action())
        snapshot = TaskSnapshot("task-1", TaskStatus.RUNNING, "waiting")

        with self.assertRaises(FrozenInstanceError):
            request.tile_projections = ()  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            decision.state = "complete"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            snapshot.status = TaskStatus.COMPLETE  # type: ignore[misc]
        self.assertFalse(hasattr(request, "__dict__"))
        self.assertFalse(hasattr(decision, "__dict__"))
        self.assertFalse(hasattr(snapshot, "__dict__"))
        self.assertFalse(hasattr(decision.action, "__dict__"))

    def test_observation_request_accepts_up_to_sixteen_unique_projections(self) -> None:
        projections = tuple(
            (f"tile-{index}", WorldPoint(3200 + index, 3201, 0))
            for index in range(16)
        )

        self.assertEqual(projections, ObservationRequest(projections).tile_projections)

    def test_observation_request_rejects_invalid_structure_and_values(self) -> None:
        invalid_requests = (
            [("target", WorldPoint(3200, 3201, 0))],
            (("target",),),
            (("", WorldPoint(3200, 3201, 0)),),
            (("   ", WorldPoint(3200, 3201, 0)),),
            (
                ("target", WorldPoint(3200, 3201, 0)),
                ("target", WorldPoint(3201, 3201, 0)),
            ),
            (("target", (3200, 3201, 0)),),
            tuple(
                (f"tile-{index}", WorldPoint(3200 + index, 3201, 0))
                for index in range(17)
            ),
        )

        for value in invalid_requests:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ObservationRequest(value)  # type: ignore[arg-type]

    def test_decision_requires_nonempty_state_and_reason(self) -> None:
        for state, reason in (("", "reason"), ("   ", "reason"), ("state", ""), ("state", "   ")):
            with self.subTest(state=state, reason=reason):
                with self.assertRaises(ValueError):
                    Decision(state, reason, _wait_action())

    def test_task_snapshot_validates_text_and_status(self) -> None:
        invalid_snapshots = (
            ("", TaskStatus.RUNNING, "state", None),
            ("task", TaskStatus.RUNNING, "", None),
            ("task", TaskStatus.BLOCKED, "state", "   "),
            ("task", "running", "state", None),
        )

        for task_id, status, state, blocker in invalid_snapshots:
            with self.subTest(
                task_id=task_id, status=status, state=state, blocker=blocker
            ):
                with self.assertRaises(ValueError):
                    TaskSnapshot(task_id, status, state, blocker)  # type: ignore[arg-type]

    def test_runtime_protocol_accepts_a_structural_task(self) -> None:
        task = _FakeTask()

        self.assertIsInstance(task, Task)
        self.assertNotIsInstance(object(), Task)
        result = VerificationResult(VerificationStatus.FAIL, "blocked")
        task.apply_verification(result)
        self.assertIs(result, task.verification)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from osrs_bot.model import (
    Action,
    ActionKind,
    Observation,
    ScreenBounds,
    ScreenPoint,
    WorldPoint,
)
from osrs_bot.task_contract import (
    Decision,
    DecisionEvidence,
    ObservationRequest,
    RejectedCandidateEvidence,
    Task,
    TargetContinuityEvidence,
    TargetEvidence,
    TaskProgressSnapshot,
    TaskSnapshot,
    TaskStatus,
)
from osrs_bot.verification import (
    VerificationFailureKind,
    VerificationResult,
    VerificationStatus,
)


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

    def discard_pending_action(
        self, reason: str, *, target_invalidated: bool = True
    ) -> None:
        if not reason:
            raise ValueError("reason is required")
        if not isinstance(target_invalidated, bool):
            raise TypeError("target_invalidated is required")

    def snapshot(self) -> TaskSnapshot:
        return TaskSnapshot("fake-task", TaskStatus.RUNNING, "waiting")


class TaskContractTests(unittest.TestCase):
    def test_contract_values_are_frozen(self) -> None:
        request = ObservationRequest((("target", WorldPoint(3200, 3201, 0)),))
        target = TargetEvidence(
            "target",
            "Tree",
            1276,
            "Chop down",
            7,
            "geometry-7",
            ScreenPoint(1200, 800),
            ScreenBounds(1190, 790, 40, 40),
            world_location=WorldPoint(3195, 3248, 0),
            distance=2,
        )
        rejected = RejectedCandidateEvidence(target, ("aim_point_occluded",))
        evidence = DecisionEvidence(selected=target, eligible=(target,))
        progress = TaskProgressSnapshot("cycles", 0, 1)
        continuity = TargetContinuityEvidence(
            locked_target_key="target",
            locked_tick=5,
            last_seen_tick=7,
            incomplete_omission_frames=1,
            retention_reason="explicitly incomplete census retained target",
        )
        decision = Decision("waiting", "no_action_needed", _wait_action(), evidence)
        snapshot = TaskSnapshot(
            "task-1",
            TaskStatus.RUNNING,
            "waiting",
            definition_id="definition-1",
            profile_id="profile-1",
            progress=progress,
            route_step="west_path_1",
            route_progress=TaskProgressSnapshot("route", 1, 3),
            cycle_progress=progress,
            target_continuity=continuity,
        )

        with self.assertRaises(FrozenInstanceError):
            request.tile_projections = ()  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            decision.state = "complete"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            snapshot.status = TaskStatus.COMPLETE  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            evidence.selected = None  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            target.action = "Walk here"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            rejected.rejection_codes = ()  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            progress.current = 1  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            continuity.last_seen_tick = 8  # type: ignore[misc]
        self.assertFalse(hasattr(request, "__dict__"))
        self.assertFalse(hasattr(decision, "__dict__"))
        self.assertFalse(hasattr(snapshot, "__dict__"))
        self.assertFalse(hasattr(evidence, "__dict__"))
        self.assertFalse(hasattr(target, "__dict__"))
        self.assertFalse(hasattr(rejected, "__dict__"))
        self.assertFalse(hasattr(progress, "__dict__"))
        self.assertFalse(hasattr(continuity, "__dict__"))
        self.assertFalse(hasattr(decision.action, "__dict__"))
        self.assertEqual(WorldPoint(3195, 3248, 0), target.world_location)
        self.assertEqual(2, target.distance)
        self.assertEqual("west_path_1", snapshot.route_step)
        self.assertEqual(progress, snapshot.cycle_progress)

    def test_decision_and_snapshot_defaults_preserve_generic_tasks(self) -> None:
        decision = Decision("waiting", "no_action_needed", _wait_action())
        snapshot = TaskSnapshot("task-1", TaskStatus.RUNNING, "waiting")

        self.assertEqual(DecisionEvidence(), decision.evidence)
        self.assertIsNone(snapshot.definition_id)
        self.assertIsNone(snapshot.profile_id)
        self.assertIsNone(snapshot.progress)
        self.assertIsNone(snapshot.route_step)
        self.assertIsNone(snapshot.route_progress)
        self.assertIsNone(snapshot.cycle_progress)

    def test_diagnostic_evidence_validates_shape_and_membership(self) -> None:
        target = TargetEvidence(
            "target",
            "Tree",
            1276,
            "Chop down",
            7,
            "geometry-7",
            ScreenPoint(-120, 800),
            ScreenBounds(-200, 700, 200, 200),
        )
        other = TargetEvidence(
            "other", "Tree", 1276, "Chop down", 7, None, None, None
        )

        self.assertEqual(
            (target,),
            DecisionEvidence(selected=target, eligible=(target,)).eligible,
        )
        self.assertEqual(
            ("aim_point_occluded",),
            RejectedCandidateEvidence(
                other, ("aim_point_occluded",)
            ).rejection_codes,
        )
        self.assertEqual(1, TaskProgressSnapshot("cycles", 1, 1).current)

        invalid_factories = (
            lambda: TaskProgressSnapshot("", 0, 1),
            lambda: TaskProgressSnapshot("cycles", True, 1),
            lambda: TaskProgressSnapshot("cycles", 2, 1),
            lambda: TargetEvidence("", "Tree", 1276, "Chop", 7, None, None, None),
            lambda: TargetEvidence("target", "Tree", -1, "Chop", 7, None, None, None),
            lambda: TargetEvidence("target", "Tree", 1, "Chop", -1, None, None, None),
            lambda: TargetEvidence(
                "target", "Tree", 1, "Chop", 7, None, None, None,
                world_location=(3195, 3248, 0),  # type: ignore[arg-type]
            ),
            lambda: TargetEvidence(
                "target", "Tree", 1, "Chop", 7, None, None, None,
                distance=-1,
            ),
            lambda: RejectedCandidateEvidence(target, ()),
            lambda: RejectedCandidateEvidence(target, ("Not Stable",)),
            lambda: DecisionEvidence(selected=target),
            lambda: DecisionEvidence(eligible=(target, target)),
            lambda: DecisionEvidence(
                eligible=(target,),
                rejected=(RejectedCandidateEvidence(target, ("not_visible",)),),
            ),
            lambda: TaskSnapshot(
                "task", TaskStatus.RUNNING, "state", route_step="step-only"
            ),
            lambda: TaskSnapshot(
                "task", TaskStatus.RUNNING, "state", route_progress="route"
            ),
            lambda: TargetContinuityEvidence(locked_target_key="target"),
            lambda: TargetContinuityEvidence(incomplete_omission_frames=1),
            lambda: TargetContinuityEvidence(
                locked_target_key="target",
                locked_tick=7,
                last_seen_tick=6,
                retention_reason="retained",
            ),
            lambda: TaskSnapshot(
                "task", TaskStatus.RUNNING, "state", target_continuity="target"
            ),
        )
        for factory in invalid_factories:
            with self.subTest(factory=factory):
                with self.assertRaises(ValueError):
                    factory()

    def test_observation_request_accepts_up_to_sixteen_unique_projections(self) -> None:
        projections = tuple(
            (f"tile-{index}", WorldPoint(3200 + index, 3201, 0))
            for index in range(16)
        )

        self.assertEqual(projections, ObservationRequest(projections).tile_projections)

    def test_observation_request_accepts_bounded_unique_priority_object_ids(self) -> None:
        object_ids = tuple(range(1, 33))

        self.assertEqual(
            object_ids,
            ObservationRequest(priority_object_ids=object_ids).priority_object_ids,
        )

        for invalid in ([1276], (1276, 1276), (0,), (-1,), (True,), ("1276",), tuple(range(1, 34))):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    ObservationRequest(priority_object_ids=invalid)  # type: ignore[arg-type]

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
        result = VerificationResult(
            VerificationStatus.FAIL,
            "blocked",
            failure_kind=VerificationFailureKind.RUNTIME_FAILURE,
        )
        task.apply_verification(result)
        self.assertIs(result, task.verification)


if __name__ == "__main__":
    unittest.main()

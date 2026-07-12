from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

from osrs_bot.engine_frame import (
    CleanupEvidence,
    EngineFramePublisher,
    EngineStage,
    ObservationReference,
)
from osrs_bot.input_coordinator import (
    CommandEvidence,
    FirmwareSafetyStatus,
    InputReceipt,
)
from osrs_bot.model import (
    Action,
    ActionKind,
    BANK_INTERFACE_NAME,
    ScreenBounds,
    ScreenPoint,
    VerificationKind,
    VerificationSpec,
    WorldPoint,
)
from osrs_bot.safety import SafetyCheck
from osrs_bot.task_contract import (
    Decision,
    DecisionEvidence,
    RejectedCandidateEvidence,
    TargetEvidence,
    TaskProgressSnapshot,
    TaskSnapshot,
    TaskStatus,
)
from osrs_bot.verification import (
    Outcome,
    OutcomeKind,
    VerificationFailureKind,
    VerificationResult,
    VerificationStatus,
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


def _receipt() -> InputReceipt:
    names = (
        "ARM",
        "MOUSE_DOWN",
        "MOUSE_UP",
        "STOP_ALL",
        "DISARM",
        "STATUS",
    )
    return InputReceipt(
        transaction_id="input-00000001",
        mode="pointer",
        intent_ids=("tree",),
        status="PASS",
        reason="input_transaction_succeeded",
        connected=True,
        arm_acknowledged=True,
        stop_all_acknowledged=True,
        disarm_acknowledged=True,
        firmware_status_acknowledged=True,
        firmware_status=FirmwareSafetyStatus(False, 0, 0),
        commands=tuple(_command(index, name) for index, name in enumerate(names, 1)),
        unresolved_command_count=0,
        failed_command_count=0,
        ack_missing_count=0,
        ledger_complete=True,
        ledger_closed=True,
        backend_closed=True,
    )


def _target(key: str, x: int) -> TargetEvidence:
    return TargetEvidence(
        key=key,
        name="Tree",
        object_id=1276,
        action="Chop down",
        source_tick=100,
        geometry_frame_id="geometry-100",
        point=ScreenPoint(x, 80),
        bounds=ScreenBounds(x - 5, 75, 11, 11),
    )


class EngineFrameTests(unittest.TestCase):
    def test_publisher_retains_one_deeply_immutable_diagnostic_truth(self) -> None:
        selected = _target("tree:selected", 100)
        eligible = _target("tree:eligible", 140)
        rejected = _target("tree:rejected", 180)
        evidence = DecisionEvidence(
            selected=selected,
            eligible=(selected, eligible),
            rejected=(
                RejectedCandidateEvidence(rejected, ("geometry_unavailable",)),
            ),
        )
        specification = VerificationSpec(
            VerificationKind.ITEM_QUANTITY_INCREASED,
            before_tick=100,
            deadline_tick=110,
            item_id=1511,
            before_quantity=0,
            source_session_id="session-1",
        )
        decision = Decision(
            "verify_logs",
            "interact with exact configured resource",
            Action(
                ActionKind.INTERACT_OBJECT,
                "Chop configured resource",
                100,
                option="Chop down",
                target_key=selected.key,
                target_name=selected.name,
                target_id=selected.object_id,
                screen_point=selected.point,
                verification=specification,
                source_session_id="session-1",
            ),
            evidence=evidence,
        )
        snapshot = TaskSnapshot(
            "woodcut_bank",
            TaskStatus.RUNNING,
            "verify_logs",
            definition_id="lumbridge_west_trees_v1",
            profile_id="default_woodcut_one_cycle_v1",
            progress=TaskProgressSnapshot("route", 3, 19),
        )
        observation = ObservationReference(
            100,
            datetime.now(timezone.utc),
            "frame-100",
            "geometry-100",
            "session-1",
            1234,
            ScreenBounds(0, 0, 765, 503),
        )
        verification = VerificationResult(
            VerificationStatus.PASS,
            "item_quantity_increased",
            Outcome(OutcomeKind.ITEM_QUANTITY_INCREASED, 101),
        )
        publisher = EngineFramePublisher()

        frame = publisher.publish(
            stage=EngineStage.VERIFIED,
            task=snapshot,
            observation=observation,
            decision=decision,
            safety_checks=(SafetyCheck("pre_move", "pre_move_safe", True),),
            pending_verification=specification,
            last_verification=verification,
            last_execution_status="SENT",
            last_execution_reason="action_sent",
            last_execution_activation_attempted=True,
            last_execution_receipt=_receipt(),
        )

        self.assertEqual(1, frame.sequence)
        self.assertIs(frame, publisher.latest())
        self.assertIs(selected, frame.selected_target)
        self.assertEqual((selected, eligible), frame.eligible_targets)
        self.assertEqual(("geometry_unavailable",), frame.rejected_targets[0].rejection_codes)
        self.assertTrue(frame.cleanup.safe)
        payload = frame.to_dict()
        self.assertEqual("engine_frame.v1", payload["schema"])
        self.assertEqual("lumbridge_west_trees_v1", payload["task"]["definitionId"])
        self.assertEqual("tree:selected", payload["selectedTarget"]["key"])
        self.assertEqual("item_quantity_increased", payload["lastVerification"]["outcome"]["kind"])
        self.assertIsNone(payload["lastVerification"]["failureKind"])
        self.assertIn("cameraYaw", payload["observation"])
        self.assertIn("keyHoldMillis", payload["decision"]["action"])
        self.assertEqual(1511, payload["pendingVerification"]["itemId"])
        self.assertEqual(0, payload["pendingVerification"]["beforeQuantity"])
        self.assertTrue(payload["lastExecution"]["activationAttempted"])
        self.assertTrue(payload["cleanup"]["safe"])
        with self.assertRaises(FrozenInstanceError):
            frame.stage = EngineStage.TERMINAL  # type: ignore[misc]
        self.assertFalse(hasattr(frame, "__dict__"))

    def test_activation_attempted_requires_a_boolean(self) -> None:
        with self.assertRaises(TypeError):
            EngineFramePublisher().publish(
                stage=EngineStage.EXECUTED,
                task=TaskSnapshot("probe", TaskStatus.RUNNING, "ready"),
                last_execution_activation_attempted=1,  # type: ignore[arg-type]
            )

    def test_typed_verification_failure_is_serialized(self) -> None:
        failure = VerificationResult(
            VerificationStatus.FAIL,
            "item_quantity_unchanged_at_deadline",
            failure_kind=(
                VerificationFailureKind.ITEM_QUANTITY_UNCHANGED_AT_DEADLINE
            ),
        )
        frame = EngineFramePublisher().publish(
            stage=EngineStage.VERIFIED,
            task=TaskSnapshot("woodcut_bank", TaskStatus.RUNNING, "find_tree"),
            last_verification=failure,
        )

        payload = frame.to_dict()["lastVerification"]
        self.assertEqual(
            "item_quantity_unchanged_at_deadline",
            payload["failureKind"],
        )
        self.assertIsNone(payload["outcome"])

    def test_sequence_is_monotonic_and_wait_returns_only_newer_frames(self) -> None:
        publisher = EngineFramePublisher()
        snapshot = TaskSnapshot("probe", TaskStatus.RUNNING, "ready")

        first = publisher.publish(stage=EngineStage.OBSERVED, task=snapshot)
        self.assertIs(first, publisher.wait_for_newer(0, timeout=0))
        self.assertIsNone(publisher.wait_for_newer(first.sequence, timeout=0))
        second = publisher.publish(stage=EngineStage.TERMINAL, task=snapshot)

        self.assertEqual(first.sequence + 1, second.sequence)
        self.assertIs(second, publisher.wait_for_newer(first.sequence, timeout=0))

    def test_cleanup_is_derived_from_authoritative_receipt_not_status_text(self) -> None:
        safe = CleanupEvidence.from_receipt(_receipt())
        absent = CleanupEvidence.from_receipt(None)

        self.assertTrue(safe.safe)
        self.assertFalse(absent.attempted)
        self.assertFalse(absent.safe)

    def test_pending_verification_serializes_every_condition_needed_to_inspect_it(self) -> None:
        snapshot = TaskSnapshot("probe", TaskStatus.RUNNING, "verify")
        route = VerificationSpec(
            VerificationKind.ROUTE_TRANSITION,
            before_tick=10,
            deadline_tick=20,
            before_location=WorldPoint(3205, 3208, 1),
            target_location=WorldPoint(3205, 3208, 2),
            expected_plane=2,
            target_radius=1,
            source_session_id="session-1",
            dialogue_prompt_contains="which floor",
            dialogue_option_contains="climb up",
        )
        interface = VerificationSpec(
            VerificationKind.INTERFACE_OPENED,
            before_tick=20,
            deadline_tick=25,
            expected_plane=2,
            source_session_id="session-1",
            interface_name=BANK_INTERFACE_NAME,
        )
        publisher = EngineFramePublisher()

        route_payload = publisher.publish(
            stage=EngineStage.DECIDED,
            task=snapshot,
            pending_verification=route,
        ).to_dict()["pendingVerification"]
        interface_payload = publisher.publish(
            stage=EngineStage.DECIDED,
            task=snapshot,
            pending_verification=interface,
        ).to_dict()["pendingVerification"]

        self.assertEqual(
            {"x": 3205, "y": 3208, "plane": 1},
            route_payload["beforeLocation"],
        )
        self.assertEqual(2, route_payload["expectedPlane"])
        self.assertEqual(1, route_payload["targetRadius"])
        self.assertEqual("which floor", route_payload["dialoguePromptContains"])
        self.assertEqual("climb up", route_payload["dialogueOptionContains"])
        self.assertEqual(BANK_INTERFACE_NAME, interface_payload["interfaceName"])
        self.assertEqual(2, interface_payload["expectedPlane"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .input_coordinator import InputReceipt
from .model import (
    Action,
    Observation,
    ScreenBounds,
    ScreenPoint,
    VerificationSpec,
    WorldPoint,
)
from .safety import SafetyCheck
from .task_contract import (
    Decision,
    RejectedCandidateEvidence,
    TargetEvidence,
    TaskSnapshot,
)
from .verification import VerificationResult


class EngineStage(str, Enum):
    STARTING = "starting"
    OBSERVED = "observed"
    DECIDED = "decided"
    EXECUTED = "executed"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class ObservationReference:
    source_tick: int
    captured_at: datetime
    frame_id: str | None
    geometry_frame_id: str | None
    session_id: str | None
    process_id: int | None
    canvas_bounds: ScreenBounds | None
    camera_yaw: int | None = None
    camera_pitch: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_tick, int) or isinstance(self.source_tick, bool):
            raise TypeError("source_tick must be an integer")
        if not isinstance(self.captured_at, datetime):
            raise TypeError("captured_at must be a datetime")
        for name in ("frame_id", "geometry_frame_id", "session_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be non-empty or None")
        if self.process_id is not None and (
            not isinstance(self.process_id, int)
            or isinstance(self.process_id, bool)
            or self.process_id <= 0
        ):
            raise ValueError("process_id must be positive or None")
        if self.canvas_bounds is not None and not isinstance(
            self.canvas_bounds, ScreenBounds
        ):
            raise TypeError("canvas_bounds must be ScreenBounds or None")
        for name in ("camera_yaw", "camera_pitch"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(f"{name} must be non-negative or None")

    @classmethod
    def from_observation(cls, observation: Observation) -> "ObservationReference":
        if not isinstance(observation, Observation):
            raise TypeError("observation must be Observation")
        return cls(
            source_tick=observation.tick,
            captured_at=observation.timestamp,
            frame_id=observation.frame_id,
            geometry_frame_id=observation.geometry_frame_id,
            session_id=observation.session_id,
            process_id=observation.client_process_id,
            canvas_bounds=observation.canvas_bounds,
            camera_yaw=observation.camera_yaw,
            camera_pitch=observation.camera_pitch,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceTick": self.source_tick,
            "capturedAtUtc": self.captured_at.isoformat(),
            "frameId": self.frame_id,
            "geometryFrameId": self.geometry_frame_id,
            "sessionId": self.session_id,
            "processId": self.process_id,
            "canvasBounds": _bounds_dict(self.canvas_bounds),
            "cameraYaw": self.camera_yaw,
            "cameraPitch": self.camera_pitch,
        }


@dataclass(frozen=True, slots=True)
class CleanupEvidence:
    attempted: bool
    stop_all_acknowledged: bool
    disarm_acknowledged: bool
    status_acknowledged: bool
    firmware_disarmed: bool
    zero_held_keys: bool
    zero_held_mouse_buttons: bool
    zero_unresolved_commands: bool
    ledger_closed: bool
    backend_closed: bool

    @classmethod
    def from_receipt(cls, receipt: InputReceipt | None) -> "CleanupEvidence":
        if receipt is None:
            return cls(False, False, False, False, False, False, False, False, False, False)
        status = receipt.firmware_status
        return cls(
            attempted=bool(
                receipt.connected
                or receipt.commands
                or receipt.stop_all_acknowledged
                or receipt.disarm_acknowledged
                or receipt.firmware_status_acknowledged
            ),
            stop_all_acknowledged=receipt.stop_all_acknowledged,
            disarm_acknowledged=receipt.disarm_acknowledged,
            status_acknowledged=receipt.firmware_status_acknowledged,
            firmware_disarmed=bool(status is not None and not status.armed),
            zero_held_keys=bool(status is not None and status.keys_down == 0),
            zero_held_mouse_buttons=bool(
                status is not None and status.mouse_buttons_down == 0
            ),
            zero_unresolved_commands=(
                receipt.unresolved_command_count == 0
                and receipt.failed_command_count == 0
                and receipt.ack_missing_count == 0
            ),
            ledger_closed=receipt.ledger_complete and receipt.ledger_closed,
            backend_closed=receipt.backend_closed,
        )

    @property
    def safe(self) -> bool:
        return (
            self.attempted
            and self.stop_all_acknowledged
            and self.disarm_acknowledged
            and self.status_acknowledged
            and self.firmware_disarmed
            and self.zero_held_keys
            and self.zero_held_mouse_buttons
            and self.zero_unresolved_commands
            and self.ledger_closed
            and self.backend_closed
        )

    def to_dict(self) -> dict[str, bool]:
        return {
            "attempted": self.attempted,
            "safe": self.safe,
            "stopAllAcknowledged": self.stop_all_acknowledged,
            "disarmAcknowledged": self.disarm_acknowledged,
            "statusAcknowledged": self.status_acknowledged,
            "firmwareDisarmed": self.firmware_disarmed,
            "zeroHeldKeys": self.zero_held_keys,
            "zeroHeldMouseButtons": self.zero_held_mouse_buttons,
            "zeroUnresolvedCommands": self.zero_unresolved_commands,
            "ledgerClosed": self.ledger_closed,
            "backendClosed": self.backend_closed,
        }


@dataclass(frozen=True, slots=True)
class EngineFrame:
    sequence: int
    published_at: datetime
    stage: EngineStage
    task: TaskSnapshot
    observation: ObservationReference | None = None
    decision: Decision | None = None
    safety_checks: tuple[SafetyCheck, ...] = ()
    pending_verification: VerificationSpec | None = None
    last_verification: VerificationResult | None = None
    last_execution_status: str | None = None
    last_execution_reason: str | None = None
    last_execution_receipt: InputReceipt | None = None
    cleanup: CleanupEvidence = CleanupEvidence(
        False, False, False, False, False, False, False, False, False, False
    )
    blocker: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence <= 0:
            raise ValueError("sequence must be a positive integer")
        if not isinstance(self.published_at, datetime):
            raise TypeError("published_at must be a datetime")
        if not isinstance(self.stage, EngineStage):
            raise TypeError("stage must be EngineStage")
        if not isinstance(self.task, TaskSnapshot):
            raise TypeError("task must be TaskSnapshot")
        if self.observation is not None and not isinstance(
            self.observation, ObservationReference
        ):
            raise TypeError("observation must be ObservationReference or None")
        if self.decision is not None and not isinstance(self.decision, Decision):
            raise TypeError("decision must be Decision or None")
        if not isinstance(self.safety_checks, tuple) or not all(
            isinstance(check, SafetyCheck) for check in self.safety_checks
        ):
            raise TypeError("safety_checks must be a tuple of SafetyCheck values")
        if self.pending_verification is not None and not isinstance(
            self.pending_verification, VerificationSpec
        ):
            raise TypeError("pending_verification must be VerificationSpec or None")
        if self.last_verification is not None and not isinstance(
            self.last_verification, VerificationResult
        ):
            raise TypeError("last_verification must be VerificationResult or None")
        if self.last_execution_receipt is not None and not isinstance(
            self.last_execution_receipt, InputReceipt
        ):
            raise TypeError("last_execution_receipt must be InputReceipt or None")
        if not isinstance(self.cleanup, CleanupEvidence):
            raise TypeError("cleanup must be CleanupEvidence")
        for name in ("last_execution_status", "last_execution_reason", "blocker"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be non-empty or None")

    @property
    def selected_target(self) -> TargetEvidence | None:
        return self.decision.evidence.selected if self.decision is not None else None

    @property
    def eligible_targets(self) -> tuple[TargetEvidence, ...]:
        return self.decision.evidence.eligible if self.decision is not None else ()

    @property
    def rejected_targets(self) -> tuple[RejectedCandidateEvidence, ...]:
        return self.decision.evidence.rejected if self.decision is not None else ()

    def to_dict(self) -> dict[str, Any]:
        outcome = (
            self.last_verification.outcome
            if self.last_verification is not None
            else None
        )
        return {
            "schema": "engine_frame.v1",
            "sequence": self.sequence,
            "publishedAtUtc": self.published_at.isoformat(),
            "stage": self.stage.value,
            "task": _task_dict(self.task),
            "observation": (
                self.observation.to_dict() if self.observation is not None else None
            ),
            "decision": _decision_dict(self.decision),
            "selectedTarget": _target_dict(self.selected_target),
            "eligibleCandidates": [
                _target_dict(target) for target in self.eligible_targets
            ],
            "rejectedCandidates": [
                {
                    "target": _target_dict(rejected.target),
                    "rejectionCodes": list(rejected.rejection_codes),
                }
                for rejected in self.rejected_targets
            ],
            "safetyChecks": [
                {"stage": check.stage, "code": check.code, "allowed": check.allowed}
                for check in self.safety_checks
            ],
            "pendingVerification": _verification_spec_dict(
                self.pending_verification
            ),
            "lastVerification": (
                None
                if self.last_verification is None
                else {
                    "status": self.last_verification.status.value,
                    "reason": self.last_verification.reason,
                    "outcome": (
                        None
                        if outcome is None
                        else {
                            "kind": outcome.kind.value,
                            "observedTick": outcome.observed_tick,
                        }
                    ),
                }
            ),
            "lastExecution": {
                "status": self.last_execution_status,
                "reason": self.last_execution_reason,
                "receipt": (
                    self.last_execution_receipt.to_dict()
                    if self.last_execution_receipt is not None
                    else None
                ),
            },
            "cleanup": self.cleanup.to_dict(),
            "blocker": self.blocker,
        }


class EngineFramePublisher:
    """Atomic latest-frame publication with no control authority or history."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._latest: EngineFrame | None = None
        self._sequence = 0

    def publish(
        self,
        *,
        stage: EngineStage,
        task: TaskSnapshot,
        observation: ObservationReference | None = None,
        decision: Decision | None = None,
        safety_checks: tuple[SafetyCheck, ...] = (),
        pending_verification: VerificationSpec | None = None,
        last_verification: VerificationResult | None = None,
        last_execution_status: str | None = None,
        last_execution_reason: str | None = None,
        last_execution_receipt: InputReceipt | None = None,
        blocker: str | None = None,
    ) -> EngineFrame:
        with self._condition:
            self._sequence += 1
            frame = EngineFrame(
                sequence=self._sequence,
                published_at=datetime.now(timezone.utc),
                stage=stage,
                task=task,
                observation=observation,
                decision=decision,
                safety_checks=safety_checks,
                pending_verification=pending_verification,
                last_verification=last_verification,
                last_execution_status=last_execution_status,
                last_execution_reason=last_execution_reason,
                last_execution_receipt=last_execution_receipt,
                cleanup=CleanupEvidence.from_receipt(last_execution_receipt),
                blocker=blocker,
            )
            self._latest = frame
            self._condition.notify_all()
            return frame

    def latest(self) -> EngineFrame | None:
        with self._condition:
            return self._latest

    def wait_for_newer(
        self, sequence: int, timeout: float | None = None
    ) -> EngineFrame | None:
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise ValueError("sequence must be a non-negative integer")
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative or None")
        with self._condition:
            self._condition.wait_for(
                lambda: self._latest is not None
                and self._latest.sequence > sequence,
                timeout=timeout,
            )
            if self._latest is None or self._latest.sequence <= sequence:
                return None
            return self._latest


def _bounds_dict(bounds: ScreenBounds | None) -> dict[str, int] | None:
    if bounds is None:
        return None
    return {
        "x": bounds.x,
        "y": bounds.y,
        "width": bounds.width,
        "height": bounds.height,
    }


def _point_dict(point: ScreenPoint | None) -> dict[str, int] | None:
    return None if point is None else {"x": point.x, "y": point.y}


def _target_dict(target: TargetEvidence | None) -> dict[str, Any] | None:
    if target is None:
        return None
    return {
        "key": target.key,
        "name": target.name,
        "objectId": target.object_id,
        "action": target.action,
        "sourceTick": target.source_tick,
        "geometryFrameId": target.geometry_frame_id,
        "point": _point_dict(target.point),
        "bounds": _bounds_dict(target.bounds),
    }


def _task_dict(task: TaskSnapshot) -> dict[str, Any]:
    progress = task.progress
    return {
        "taskId": task.task_id,
        "status": task.status.value,
        "state": task.state,
        "definitionId": task.definition_id,
        "profileId": task.profile_id,
        "progress": (
            None
            if progress is None
            else {
                "label": progress.label,
                "current": progress.current,
                "total": progress.total,
            }
        ),
        "blocker": task.blocker,
    }


def _decision_dict(decision: Decision | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    return {
        "state": decision.state,
        "reason": decision.reason,
        "action": _action_dict(decision.action),
    }


def _action_dict(action: Action) -> dict[str, Any]:
    return {
        "kind": action.kind.value,
        "label": action.label,
        "sourceTick": action.source_tick,
        "option": action.option,
        "targetKey": action.target_key,
        "targetName": action.target_name,
        "targetId": action.target_id,
        "key": action.key,
        "keyHoldMillis": action.key_hold_millis,
        "point": _point_dict(action.screen_point),
    }


def _verification_spec_dict(specification: VerificationSpec | None) -> dict[str, Any] | None:
    if specification is None:
        return None
    return {
        "kind": specification.kind.value,
        "beforeTick": specification.before_tick,
        "deadlineTick": specification.deadline_tick,
        "sourceSessionId": specification.source_session_id,
        "itemId": specification.item_id,
        "beforeQuantity": specification.before_quantity,
        "expectedQuantity": specification.expected_quantity,
        "beforeLocation": _world_point_dict(specification.before_location),
        "targetLocation": _world_point_dict(specification.target_location),
        "expectedPlane": specification.expected_plane,
        "targetRadius": specification.target_radius,
        "interfaceName": specification.interface_name,
        "dialoguePromptContains": specification.dialogue_prompt_contains,
        "dialogueOptionContains": specification.dialogue_option_contains,
        "beforeCameraYaw": specification.before_camera_yaw,
        "beforeGeometryFrameId": specification.before_geometry_frame_id,
        "cameraKey": specification.camera_key,
    }


def _world_point_dict(point: WorldPoint | None) -> dict[str, int] | None:
    if point is None:
        return None
    return {"x": point.x, "y": point.y, "plane": point.plane}

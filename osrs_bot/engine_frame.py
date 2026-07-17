from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from collections.abc import Callable
from typing import Any

from .behavior import (
    BehaviorConfig,
    DEFAULT_BEHAVIOR_CONFIG,
    classify_camera_zoom,
)
from .input_coordinator import InputReceipt
from .model import (
    Action,
    InventoryItem,
    InventoryObservation,
    Observation,
    ObservationPipelineEvidence,
    SceneCensusEvidence,
    ScreenBounds,
    ScreenPoint,
    VerificationSpec,
    WorldPoint,
)
from .observability import ObservabilityEvidence
from .safety import SafetyCheck
from .task_contract import (
    Decision,
    RejectedCandidateEvidence,
    TargetContinuityEvidence,
    TargetEvidence,
    TaskProgressSnapshot,
    TaskSnapshot,
)
from .verification import VerificationResult


ENGINE_FRAME_SCHEMA = "engine_frame.v1"


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
    viewport_bounds: ScreenBounds | None = None
    player_screen_point: ScreenPoint | None = None
    camera_yaw: int | None = None
    camera_pitch: int | None = None
    camera_zoom: int | None = None
    camera_zoom_classification: str = "unavailable"
    camera_zoom_desired_min: int = (
        DEFAULT_BEHAVIOR_CONFIG.camera_zoom_desired_min
    )
    camera_zoom_desired_max: int = (
        DEFAULT_BEHAVIOR_CONFIG.camera_zoom_desired_max
    )
    game_state: str | None = None
    loaded_scene: bool = False
    client_focused: bool = False
    text_input_active: bool | None = None
    fresh: bool = False
    cache_wall_clock_fresh: bool = False
    source_coherent: bool = False
    player_location: WorldPoint | None = None
    player_plane: int | None = None
    inventory: InventoryObservation | None = None
    max_source_age_millis: int = 2_000
    scene_census: SceneCensusEvidence = SceneCensusEvidence()
    pipeline: ObservationPipelineEvidence = ObservationPipelineEvidence()

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
        for name in ("canvas_bounds", "viewport_bounds"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, ScreenBounds):
                raise TypeError(f"{name} must be ScreenBounds or None")
        if self.player_screen_point is not None and not isinstance(
            self.player_screen_point, ScreenPoint
        ):
            raise TypeError("player_screen_point must be ScreenPoint or None")
        for name in ("camera_yaw", "camera_pitch", "camera_zoom"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(f"{name} must be non-negative or None")
        if self.camera_zoom_classification not in {
            "unavailable",
            "too_far",
            "moderate",
            "too_close",
        }:
            raise ValueError("camera_zoom_classification is unsupported")
        for name in ("camera_zoom_desired_min", "camera_zoom_desired_max"):
            value = getattr(self, name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1 <= value <= 4096
            ):
                raise ValueError(f"{name} must be between 1 and 4096")
        if self.camera_zoom_desired_min > self.camera_zoom_desired_max:
            raise ValueError(
                "camera_zoom_desired_min cannot exceed camera_zoom_desired_max"
            )
        if self.game_state is not None and (
            not isinstance(self.game_state, str) or not self.game_state.strip()
        ):
            raise ValueError("game_state must be non-empty or None")
        for name in (
            "loaded_scene",
            "client_focused",
            "fresh",
            "cache_wall_clock_fresh",
            "source_coherent",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be bool")
        if self.text_input_active is not None and not isinstance(
            self.text_input_active, bool
        ):
            raise TypeError("text_input_active must be bool or None")
        if self.player_location is not None and not isinstance(
            self.player_location, WorldPoint
        ):
            raise TypeError("player_location must be WorldPoint or None")
        if self.player_plane is not None and (
            not isinstance(self.player_plane, int)
            or isinstance(self.player_plane, bool)
            or self.player_plane < 0
        ):
            raise ValueError("player_plane must be non-negative or None")
        if self.inventory is not None:
            if not isinstance(self.inventory, InventoryObservation):
                raise TypeError("inventory must be InventoryObservation or None")
            if not isinstance(self.inventory.items, tuple) or not all(
                isinstance(item, InventoryItem) for item in self.inventory.items
            ):
                raise TypeError(
                    "inventory items must be an immutable tuple of InventoryItem values"
                )
        if (
            not isinstance(self.max_source_age_millis, int)
            or isinstance(self.max_source_age_millis, bool)
            or self.max_source_age_millis < 0
        ):
            raise ValueError("max_source_age_millis must be a non-negative integer")
        if not isinstance(self.scene_census, SceneCensusEvidence):
            raise TypeError("scene_census must be SceneCensusEvidence")
        if not isinstance(self.pipeline, ObservationPipelineEvidence):
            raise TypeError("pipeline must be ObservationPipelineEvidence")

    @classmethod
    def from_observation(
        cls,
        observation: Observation,
        *,
        behavior_config: BehaviorConfig = DEFAULT_BEHAVIOR_CONFIG,
    ) -> "ObservationReference":
        if not isinstance(observation, Observation):
            raise TypeError("observation must be Observation")
        if not isinstance(behavior_config, BehaviorConfig):
            raise TypeError("behavior_config must be BehaviorConfig")
        return cls(
            source_tick=observation.tick,
            captured_at=observation.timestamp,
            frame_id=observation.frame_id,
            geometry_frame_id=observation.geometry_frame_id,
            session_id=observation.session_id,
            process_id=observation.client_process_id,
            canvas_bounds=observation.canvas_bounds,
            viewport_bounds=observation.viewport_bounds,
            player_screen_point=observation.player_screen_point,
            camera_yaw=observation.camera_yaw,
            camera_pitch=observation.camera_pitch,
            camera_zoom=observation.camera_zoom,
            camera_zoom_classification=classify_camera_zoom(
                observation.camera_zoom,
                behavior_config,
            ),
            camera_zoom_desired_min=(
                behavior_config.camera_zoom_desired_min
            ),
            camera_zoom_desired_max=(
                behavior_config.camera_zoom_desired_max
            ),
            game_state=observation.game_state,
            loaded_scene=observation.loaded_scene,
            client_focused=observation.client_focused,
            text_input_active=observation.text_input_active,
            fresh=observation.fresh,
            cache_wall_clock_fresh=observation.cache_wall_clock_fresh,
            source_coherent=observation.source_coherent,
            player_location=observation.location,
            player_plane=observation.plane,
            inventory=observation.inventory,
            max_source_age_millis=observation.max_source_age_millis,
            scene_census=observation.scene_census,
            pipeline=observation.pipeline,
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
            "viewportBounds": _bounds_dict(self.viewport_bounds),
            "playerScreenPoint": _point_dict(self.player_screen_point),
            "cameraYaw": self.camera_yaw,
            "cameraPitch": self.camera_pitch,
            "cameraZoom3d": self.camera_zoom,
            "cameraZoomClassification": self.camera_zoom_classification,
            "desiredCameraZoomRange": {
                "min": self.camera_zoom_desired_min,
                "max": self.camera_zoom_desired_max,
            },
            "gameState": self.game_state,
            "loadedScene": self.loaded_scene,
            "clientFocused": self.client_focused,
            "textInputActive": self.text_input_active,
            "fresh": self.fresh,
            "cacheWallClockFresh": self.cache_wall_clock_fresh,
            "sourceCoherent": self.source_coherent,
            "playerLocation": _world_point_dict(self.player_location),
            "playerPlane": self.player_plane,
            "inventory": _inventory_dict(self.inventory),
            "maxSourceAgeMillis": self.max_source_age_millis,
            "sceneCensus": _scene_census_dict(self.scene_census),
            "observationPipeline": _observation_pipeline_dict(self.pipeline),
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
    last_execution_activation_attempted: bool = False
    last_execution_receipt: InputReceipt | None = None
    cleanup: CleanupEvidence = CleanupEvidence(
        False, False, False, False, False, False, False, False, False, False
    )
    blocker: str | None = None
    observability: ObservabilityEvidence = ObservabilityEvidence()

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
        if not isinstance(self.last_execution_activation_attempted, bool):
            raise TypeError("last_execution_activation_attempted must be bool")
        if not isinstance(self.cleanup, CleanupEvidence):
            raise TypeError("cleanup must be CleanupEvidence")
        if not isinstance(self.observability, ObservabilityEvidence):
            raise TypeError("observability must be ObservabilityEvidence")
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
        route = self.decision.evidence.route if self.decision is not None else None
        camera = self.decision.evidence.camera if self.decision is not None else None
        targeting = (
            self.decision.evidence.targeting if self.decision is not None else None
        )
        timing = self.decision.evidence.timing if self.decision is not None else None
        pointer = (
            self.last_execution_receipt.pointer_motion
            if self.last_execution_receipt is not None
            else None
        )
        return {
            "schema": ENGINE_FRAME_SCHEMA,
            "sequence": self.sequence,
            "publishedAtUtc": self.published_at.isoformat(),
            "stage": self.stage.value,
            "task": _task_dict(self.task),
            "observation": (
                self.observation.to_dict() if self.observation is not None else None
            ),
            "decision": _decision_dict(self.decision),
            "route": _route_decision_dict(route),
            "camera": _camera_decision_dict(camera),
            "targeting": _targeting_decision_dict(targeting),
            "pointer": pointer.to_dict() if pointer is not None else None,
            "timing": _timing_decision_dict(timing),
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
                    "failureKind": (
                        self.last_verification.failure_kind.value
                        if self.last_verification.failure_kind is not None
                        else None
                    ),
                    "outcome": (
                        None
                        if outcome is None
                        else {
                            "kind": outcome.kind.value,
                            "observedTick": outcome.observed_tick,
                            "cameraPoseResult": (
                                None
                                if outcome.camera_pose_result is None
                                else {
                                    "cameraKey": outcome.camera_pose_result.camera_key,
                                    "beforeYaw": outcome.camera_pose_result.before_yaw,
                                    "afterYaw": outcome.camera_pose_result.after_yaw,
                                    "yawDelta": outcome.camera_pose_result.yaw_delta,
                                    "beforePitch": outcome.camera_pose_result.before_pitch,
                                    "afterPitch": outcome.camera_pose_result.after_pitch,
                                    "pitchDelta": outcome.camera_pose_result.pitch_delta,
                                    "beforeGeometryFrameId": (
                                        outcome.camera_pose_result.before_geometry_frame_id
                                    ),
                                    "afterGeometryFrameId": (
                                        outcome.camera_pose_result.after_geometry_frame_id
                                    ),
                                    "geometryFrameChanged": True,
                                }
                            ),
                            "cameraZoomResult": _camera_zoom_result_dict(
                                outcome.camera_zoom_result
                            ),
                        }
                    ),
                }
            ),
            "lastExecution": {
                "status": self.last_execution_status,
                "reason": self.last_execution_reason,
                "activationAttempted": self.last_execution_activation_attempted,
                "receipt": (
                    self.last_execution_receipt.to_dict()
                    if self.last_execution_receipt is not None
                    else None
                ),
            },
            "cleanup": self.cleanup.to_dict(),
            "blocker": self.blocker,
            "observability": self.observability.to_dict(),
        }


class EngineFramePublisher:
    """Atomic latest-frame publication with no control authority or history."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._latest: EngineFrame | None = None
        self._sequence = 0
        self._next_listener_id = 1
        self._listeners: dict[int, Callable[[EngineFrame], None]] = {}

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
        last_execution_activation_attempted: bool = False,
        last_execution_receipt: InputReceipt | None = None,
        blocker: str | None = None,
        observability: ObservabilityEvidence = ObservabilityEvidence(),
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
                last_execution_activation_attempted=(
                    last_execution_activation_attempted
                ),
                last_execution_receipt=last_execution_receipt,
                cleanup=CleanupEvidence.from_receipt(last_execution_receipt),
                blocker=blocker,
                observability=observability,
            )
            self._latest = frame
            listeners = tuple(self._listeners.values())
            self._condition.notify_all()
        for listener in listeners:
            try:
                listener(frame)
            except Exception:
                # Evidence and presentation listeners are diagnostic only.
                # Their failure must never alter engine control or publication.
                continue
        return frame

    def subscribe(
        self, listener: Callable[[EngineFrame], None]
    ) -> Callable[[], None]:
        """Observe every future immutable frame without acquiring control authority."""

        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._condition:
            listener_id = self._next_listener_id
            self._next_listener_id += 1
            self._listeners[listener_id] = listener

        def unsubscribe() -> None:
            with self._condition:
                self._listeners.pop(listener_id, None)

        return unsubscribe

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


def _scene_census_dict(value: SceneCensusEvidence) -> dict[str, Any]:
    payload = {
        "schema": value.schema,
        "sourceSchema": value.source_schema,
        "metadataPresent": value.metadata_present,
        "complete": value.complete,
        "authoritativeAbsenceEligible": value.authoritative_absence_eligible,
        "priorityAbsenceEligible": value.priority_absence_eligible,
        "sceneCoverageComplete": value.scene_coverage_complete,
        "count": value.count,
        "returned": value.returned,
        "responseCapHit": value.response_cap_hit,
        "sourceCapHit": value.source_cap_hit,
        "centerWorldLocation": _world_point_dict(value.center_world_location),
        "anchorSource": value.anchor_source,
        "radiusTiles": value.radius_tiles,
        "requestedTileCount": value.requested_tile_count,
        "scannedTileSlots": value.scanned_tile_slots,
        "scannedTiles": value.scanned_tiles,
        "missingTileCount": value.missing_tile_count,
        "discoveredObjectCount": value.discovered_object_count,
        "sourceDuplicateObjectCount": value.source_duplicate_object_count,
        "sourceContradictoryDuplicateCount": (
            value.source_contradictory_duplicate_count
        ),
        "indexedObjectCount": value.indexed_object_count,
        "enrichedObjectCount": value.enriched_object_count,
        "projectedObjectCount": value.projected_object_count,
        "requestedPriorityObjectIds": list(value.requested_priority_object_ids),
        "requestedPriorityObjectKeys": list(value.requested_priority_object_keys),
        "reportedPriorityObjectIds": list(value.reported_priority_object_ids),
        "returnedPriorityObjectIds": list(value.returned_priority_object_ids),
        "priorityObjectsComplete": value.priority_objects_complete,
        "reportedPriorityObjectKeys": list(value.reported_priority_object_keys),
        "returnedPriorityObjectKeys": list(value.returned_priority_object_keys),
        "priorityKeysComplete": value.priority_keys_complete,
        "duplicateRowCount": value.duplicate_row_count,
        "duplicateGroupCount": value.duplicate_group_count,
        "conflictingDuplicateKeys": list(value.conflicting_duplicate_keys),
        "omittedUnnamedCount": value.omitted_unnamed_count,
        "parsedObjectCount": value.parsed_object_count,
    }
    if not value.metadata_present:
        for key in (
            "duplicateRowCount",
            "duplicateGroupCount",
            "omittedUnnamedCount",
            "parsedObjectCount",
        ):
            if payload[key] == 0:
                payload.pop(key)
    return {
        key: item
        for key, item in payload.items()
        if item is not None and item != []
    }


def _observation_pipeline_dict(
    value: ObservationPipelineEvidence,
) -> dict[str, Any]:
    payload = {
        "schema": value.schema,
        "sourceSchema": value.source_schema,
        "requestId": value.request_id,
        "querySequence": value.query_sequence,
        "queryPurpose": value.query_purpose,
        "sourceTick": value.source_tick,
        "clientTick": value.client_tick,
        "sessionId": value.session_id,
        "processId": value.process_id,
        "geometryFrameId": value.geometry_frame_id,
        "rawCacheKey": value.raw_cache_key,
        "responseBytes": value.response_bytes,
        "httpMillis": value.http_millis,
        "decodeMillis": value.decode_millis,
        "parseMillis": value.parse_millis,
        "indexMillis": value.index_millis,
        "serviceTimingMillis": value.service_timing_millis,
        "cacheHit": value.cache_hit,
        "cacheMiss": value.cache_miss,
        "cacheEntries": value.cache_entries,
        "cacheHits": value.cache_hits,
        "cacheMisses": value.cache_misses,
        "refreshSequence": value.refresh_sequence,
        "refreshReason": value.refresh_reason,
        "refreshDurationMillis": value.refresh_duration_millis,
        "queryDurationMillis": value.query_duration_millis,
        "worldModelAgeMillis": value.world_model_age_millis,
        "maxResponseBytes": value.max_response_bytes,
        "requestedProjectionRefs": value.requested_projection_refs,
        "effectiveProjectionRefs": value.effective_projection_refs,
        "projectionRefsBeforeCap": value.projection_refs_before_cap,
        "projectionRefsAfterCap": value.projection_refs_after_cap,
        "trimmedProjectionRefs": value.trimmed_projection_refs,
        "projectionRefsCapped": value.projection_refs_capped,
        "serializationPasses": value.serialization_passes,
        "serializedBytesReusedForWrite": (
            value.serialized_bytes_reused_for_write
        ),
        "operationCounts": dict(value.operation_counts),
        "queryDiagnostics": None if value.query_diagnostics_schema is None else {
            "schema": value.query_diagnostics_schema,
            "lane": value.query_lane,
            "requestStatus": value.query_status,
            "requestCoalesced": value.request_coalesced,
            "workExecuted": value.work_executed,
            "timeoutMillis": value.timeout_millis,
            "queueWaitMillis": value.queue_wait_millis,
            "executionMillis": value.execution_millis,
            "activeRequestCount": value.active_request_count,
            "pendingRequestCount": value.pending_request_count,
            "maxDepth": value.max_queue_depth,
            "submittedCount": value.submitted_request_count,
            "executedCount": value.executed_request_count,
            "coalescedCount": value.coalesced_request_count,
            "supersededCount": value.superseded_request_count,
            "timedOutCount": value.timed_out_request_count,
            "expiredBeforeExecutionCount": (
                value.expired_before_execution_count
            ),
            "lateResultCount": value.late_result_count,
            "failedCount": value.failed_request_count,
            "lastQueueWaitMillis": value.last_queue_wait_millis,
            "maxQueueWaitMillis": value.max_queue_wait_millis,
            "lastExecutionMillis": value.last_execution_millis,
            "maxExecutionMillis": value.max_execution_millis,
        },
        "endpointQueueDiagnostics": (
            None
            if value.endpoint_queue_schema is None
            else {
                "schema": value.endpoint_queue_schema,
                "workerLimit": value.endpoint_worker_limit,
                "pendingCapacity": value.endpoint_pending_capacity,
                "activeWorkerCount": value.endpoint_active_worker_count,
                "pendingRequestCount": value.endpoint_pending_request_count,
                "pendingRemainingCapacity": (
                    value.endpoint_pending_remaining_capacity
                ),
                "largestWorkerCount": value.endpoint_largest_worker_count,
                "completedRequestCount": (
                    value.endpoint_completed_request_count
                ),
                "executionRejectionCount": (
                    value.endpoint_execution_rejection_count
                ),
                "rejectionPolicy": value.endpoint_rejection_policy,
                "snapshotRequestActive": (
                    value.endpoint_snapshot_request_active
                ),
                "snapshotBusyRejectionCount": (
                    value.endpoint_busy_rejection_count
                ),
                "executorState": value.endpoint_executor_state,
            }
        ),
    }
    for key in ("queryDiagnostics", "endpointQueueDiagnostics"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            payload[key] = {
                nested_key: nested_value
                for nested_key, nested_value in nested.items()
                if nested_value is not None
            }
    return {
        key: item
        for key, item in payload.items()
        if item is not None and item != {}
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
        "worldLocation": _world_point_dict(target.world_location),
        "distance": target.distance,
    }


def _task_dict(task: TaskSnapshot) -> dict[str, Any]:
    return {
        "taskId": task.task_id,
        "status": task.status.value,
        "state": task.state,
        "definitionId": task.definition_id,
        "profileId": task.profile_id,
        "progress": _progress_dict(task.progress),
        "routeStep": task.route_step,
        "routeProgress": _progress_dict(task.route_progress),
        "cycleProgress": _progress_dict(task.cycle_progress),
        "targetContinuity": _target_continuity_dict(task.target_continuity),
        "blocker": task.blocker,
    }


def _target_continuity_dict(
    evidence: TargetContinuityEvidence | None,
) -> dict[str, Any] | None:
    if evidence is None:
        return None
    return {
        "lockedTargetKey": evidence.locked_target_key,
        "lockedTick": evidence.locked_tick,
        "lastSeenTick": evidence.last_seen_tick,
        "incompleteOmissionFrames": evidence.incomplete_omission_frames,
        "retentionReason": evidence.retention_reason,
        "lastUnlockReason": evidence.last_unlock_reason,
    }


def _progress_dict(
    progress: TaskProgressSnapshot | None,
) -> dict[str, Any] | None:
    if progress is None:
        return None
    return {
        "label": progress.label,
        "current": progress.current,
        "total": progress.total,
    }


def _inventory_dict(
    inventory: InventoryObservation | None,
) -> dict[str, Any] | None:
    if inventory is None:
        return None
    return {
        "known": inventory.known,
        "slotCount": inventory.slot_count,
        "occupiedSlots": inventory.occupied_slots,
        "freeSlots": inventory.free_slots,
        "items": [
            {
                "slot": item.slot,
                "itemId": item.item_id,
                "quantity": item.quantity,
                "name": item.name,
            }
            for item in inventory.items
        ],
    }


def _decision_dict(decision: Decision | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    return {
        "state": decision.state,
        "reason": decision.reason,
        "action": _action_dict(decision.action),
        "route": _route_decision_dict(decision.evidence.route),
        "camera": _camera_decision_dict(decision.evidence.camera),
        "targeting": _targeting_decision_dict(decision.evidence.targeting),
        "timing": _timing_decision_dict(decision.evidence.timing),
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
        "decisionId": action.decision_id,
        "behaviorSeed": action.behavior_seed,
        "preMoveDelaySeconds": action.pre_move_delay_seconds,
        "settleDelaySeconds": action.settle_delay_seconds,
        "preClickDelaySeconds": action.pre_click_delay_seconds,
        "postActionDelaySeconds": action.post_action_delay_seconds,
    }


def _route_decision_dict(value: Any | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "currentProgressTiles": value.progress_tiles,
        "remainingDistanceTiles": value.remaining_tiles,
        "corridorDeviationTiles": value.lateral_deviation_tiles,
        "selectedRouteTarget": value.selected_step_id,
        "selectedWorldPoint": _world_point_dict(value.selected_location),
        "requestedTileDistance": value.requested_distance_tiles,
        "expectedProgressTiles": value.expected_progress_tiles,
        "actualProgressTiles": value.actual_progress_tiles,
        "skippedGuidancePoints": list(value.skipped_guidance_points),
        "mandatoryNextTransition": value.mandatory_next_step_id,
        "fallbackReason": value.fallback_reason,
        "backtracking": value.backtracking,
        "zigzagging": value.zigzagging,
        "projectedRoutePoints": [
            _point_dict(point) for point in value.projected_route_points
        ],
        "projectedRouteLabels": list(value.projected_route_labels),
        "mandatoryRoutePoints": [
            _point_dict(point) for point in value.mandatory_route_points
        ],
        "skippedRoutePoints": [
            _point_dict(point) for point in value.skipped_route_points
        ],
        "selectedScreenPoint": _point_dict(value.selected_screen_point),
        "candidateRejections": [
            {
                "stepId": rejection.step_id,
                "rejectionCodes": list(rejection.rejection_codes),
            }
            for rejection in value.candidate_rejections
        ],
    }


def _camera_decision_dict(value: Any | None) -> dict[str, Any] | None:
    if value is None:
        return None
    edge_clearance = value.edge_clearance_px
    margin_shortfall = (
        None
        if edge_clearance is None
        else max(0.0, float(value.required_edge_margin_px) - float(edge_clearance))
    )
    return {
        "framingClassification": value.classification,
        "framingContext": value.framing_context,
        "sourceTick": value.source_tick,
        "geometryFrameId": value.geometry_frame_id,
        "desiredFramingRegion": _bounds_dict(value.desired_region),
        "targetScreenPosition": _point_dict(value.target_point),
        "targetShapeBounds": _bounds_dict(value.target_bounds),
        "cameraAction": value.action,
        "holdDurationMillis": value.hold_millis,
        "routeDirectionBias": value.route_direction_bias,
        "correctionDistancePx": value.correction_distance_px,
        "edgeClearancePx": edge_clearance,
        "requiredEdgeMarginPx": value.required_edge_margin_px,
        "marginShortfallPx": margin_shortfall,
        "lookaheadPoints": [
            _point_dict(point) for point in value.lookahead_points
        ],
        "lookaheadPointCount": len(value.lookahead_points),
        "lookaheadBounds": _bounds_dict(value.lookahead_bounds),
        "yawErrorUnits": value.yaw_error_units,
        "screenCorrection": {
            "x": value.screen_correction_x_px,
            "y": value.screen_correction_y_px,
        },
        "correctionAttempt": value.correction_attempt,
        "correctionLimit": value.correction_limit,
        "cumulativeHoldMillis": value.cumulative_hold_millis,
        "acquisitionState": value.acquisition_state.value,
        "episodeId": value.episode_id,
        "lockedTargetKey": value.locked_target_key,
        "lockedTargetKind": value.locked_target_kind,
        "desiredYaw": value.desired_yaw,
        "desiredYawRange": {
            "min": value.desired_yaw_min,
            "max": value.desired_yaw_max,
        },
        "desiredPitch": value.desired_pitch,
        "desiredPitchRange": {
            "min": value.desired_pitch_min,
            "max": value.desired_pitch_max,
        },
        "pitchErrorUnits": value.pitch_error_units,
        "pitchValid": value.pitch_valid,
        "visibleAreaRatio": value.visible_area_ratio,
        "zoomClassification": value.zoom_classification,
        "zoomRequiredButUnavailable": value.zoom_required_but_unavailable,
        "capabilityMaxHoldMillis": value.capability_max_hold_millis,
        "responseModel": {
            "sampleCount": value.response_sample_count,
            "yawUnitsPerMillis": value.calibrated_yaw_units_per_millis,
            "pitchUnitsPerMillis": value.calibrated_pitch_units_per_millis,
        },
        "lastResponse": {
            "yawDelta": value.last_observed_yaw_delta,
            "pitchDelta": value.last_observed_pitch_delta,
            "noEffect": value.last_response_no_effect,
            "pitchLimitDirection": value.pitch_limit_direction,
            "overshootProven": value.overshoot_proven,
        },
        "retainedReason": value.retained_reason,
    }


def _targeting_decision_dict(value: Any | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "authoritativeGeometrySource": value.geometry_source,
        "targetShapeBounds": _bounds_dict(value.shape_bounds),
        "insetAimRegion": _bounds_dict(value.inset_region),
        "candidatePoints": [_point_dict(point) for point in value.candidate_points],
        "candidatePointCount": len(value.candidate_points),
        "selectedPoint": _point_dict(value.selected_point),
        "selectedCandidateScore": value.selected_score,
        "previousSelectedPoints": [
            _point_dict(point) for point in value.previous_points
        ],
        "selectionSeed": value.seed,
        "decisionId": value.decision_id,
        "rejectedPointReasons": list(value.rejected_reasons),
        "authoritativePolygon": [
            _point_dict(point) for point in value.shape_polygon
        ],
    }


def _timing_decision_dict(value: Any | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "decisionId": value.decision_id,
        "selectionSeed": value.seed,
        "preMoveDelaySeconds": value.pre_move_delay_seconds,
        "settleDelaySeconds": value.settle_delay_seconds,
        "preClickDelaySeconds": value.pre_click_delay_seconds,
        "postActionDelaySeconds": value.post_action_delay_seconds,
        "routePauseSeconds": value.route_pause_seconds,
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
        "beforeCameraPitch": specification.before_camera_pitch,
        "beforeGeometryFrameId": specification.before_geometry_frame_id,
        "cameraKey": specification.camera_key,
        "beforeCameraZoom3d": specification.before_camera_zoom,
        "cameraZoomAmount": specification.camera_zoom_amount,
        "beforeProcessId": specification.before_process_id,
        "beforeBankKnown": specification.before_bank_known,
        "beforeBankOpen": specification.before_bank_open,
        "beforeBankPinOpen": specification.before_bank_pin_open,
        "beforeBankReadable": specification.before_bank_readable,
        "beforeDialogueActive": specification.before_dialogue_active,
        "beforeDialogueType": specification.before_dialogue_type,
        "beforeTextInputActive": specification.before_text_input_active,
    }


def _camera_zoom_result_dict(value: Any | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "wheelAmount": value.wheel_amount,
        "beforeZoom3d": value.before_zoom,
        "afterZoom3d": value.after_zoom,
        "zoom3dDelta": value.zoom_delta,
        "beforeYaw": value.before_yaw,
        "afterYaw": value.after_yaw,
        "yawUnchanged": value.before_yaw == value.after_yaw,
        "beforePitch": value.before_pitch,
        "afterPitch": value.after_pitch,
        "pitchUnchanged": value.before_pitch == value.after_pitch,
        "beforeProcessId": value.before_process_id,
        "afterProcessId": value.after_process_id,
        "processUnchanged": value.before_process_id == value.after_process_id,
        "beforeLocation": _world_point_dict(value.before_location),
        "afterLocation": _world_point_dict(value.after_location),
        "playerLocationUnchanged": value.before_location == value.after_location,
        "sourceSessionId": value.source_session_id,
        "beforeGeometryFrameId": value.before_geometry_frame_id,
        "afterGeometryFrameId": value.after_geometry_frame_id,
        "geometryFrameChanged": (
            value.before_geometry_frame_id != value.after_geometry_frame_id
        ),
        "beforeUiState": _camera_ui_state_dict(value.before_ui_state),
        "afterUiState": _camera_ui_state_dict(value.after_ui_state),
        "uiStateUnchanged": value.before_ui_state == value.after_ui_state,
    }


def _camera_ui_state_dict(value: Any) -> dict[str, Any]:
    return {
        "bankKnown": value.bank_known,
        "bankOpen": value.bank_open,
        "bankPinOpen": value.bank_pin_open,
        "bankReadable": value.bank_readable,
        "dialogueActive": value.dialogue_active,
        "dialogueType": value.dialogue_type,
        "textInputActive": value.text_input_active,
    }


def _world_point_dict(point: WorldPoint | None) -> dict[str, int] | None:
    if point is None:
        return None
    return {"x": point.x, "y": point.y, "plane": point.plane}

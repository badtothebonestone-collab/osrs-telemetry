from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class AnalyzerContractFields:
    @property
    def missingCapabilities(self) -> list[str]:
        return self.missing_capabilities

    @property
    def sourceTick(self) -> int | None:
        return self.source_tick

    @property
    def retainedFromPrevious(self) -> bool:
        return self.retained_from_previous

    @property
    def timingMillis(self) -> float | None:
        return self.timing_millis

    def contract_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "warnings": list(self.warnings),
            "missingCapabilities": list(self.missing_capabilities),
            "sourceTick": self.source_tick,
            "retainedFromPrevious": self.retained_from_previous,
            "timingMillis": self.timing_millis,
        }


@dataclass
class AnalyzerWarning:
    message: str
    code: str | None = None
    severity: str = "WARN"
    capability: str | None = None


@dataclass
class AnalyzerTiming:
    timing_millis: float | None = None
    source_tick: int | None = None


@dataclass
class CapabilityStatus:
    name: str
    status: str = "unavailable"
    reason: str | None = None
    optional: bool = False


@dataclass
class MissingCapability:
    name: str
    reason: str | None = None
    optional: bool = False


@dataclass
class AnalyzerResult(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None


@dataclass
class LiveInputSnapshot:
    source: str | None = None
    session_path: str | None = None
    latest_tick: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class LiveSourceStatus:
    input_source_active: str | None = None
    fallback_reason: str | None = None
    fresh: bool | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlayerContext:
    world_x: int | None = None
    world_y: int | None = None
    plane: int | None = None
    scene_x: int | None = None
    scene_y: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class InventoryContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    inventory: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)
    progress_result: Any = None
    matched_slots: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TargetContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    profile_candidates: list[dict[str, Any]] = field(default_factory=list)
    broad_candidates: list[dict[str, Any]] = field(default_factory=list)
    loaded_service_scene: list[dict[str, Any]] = field(default_factory=list)
    service_candidate_inputs: list[dict[str, Any]] = field(default_factory=list)
    raw_best_target: dict[str, Any] | None = None
    nearest_target: dict[str, Any] | None = None
    top_candidates: list[dict[str, Any]] = field(default_factory=list)
    candidate_count: int = 0
    profile_candidate_count: int = 0
    broad_candidate_count: int = 0
    loaded_service_scene_count: int = 0
    service_candidate_input_count: int = 0
    service_candidate_visibility: str | None = None


@dataclass
class NavigationContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    collision_known: bool | None = None
    collision_window_available: bool | None = None
    collision_window_fresh: bool | None = None
    collision_window_radius: int | None = None
    collision_window_center_world: dict[str, Any] | None = None
    collision_window_plane: int | None = None
    collision_window_age_ticks: int | None = None
    collision_window_tiles: dict[str, Any] | None = None
    collision_window_bounds: dict[str, Any] | None = None
    collision_window_missing_reason: str | None = None
    reachable_count: int = 0
    blocked_count: int = 0
    unknown_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class NavigationIntentContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    navigation_needed: bool = False
    navigation_reason: str = "local_navigation_only"
    target_kind: str = "none"
    destination_target: dict[str, Any] | None = None
    distance_tiles: int | float | None = None
    direct_reachability: str | None = None
    path_length_tiles: int | float | None = None
    collision_window_available: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.contract_payload(),
            "navigationNeeded": self.navigation_needed,
            "navigationReason": self.navigation_reason,
            "targetKind": self.target_kind,
            "destinationTarget": self.destination_target,
            "distanceTiles": self.distance_tiles,
            "directReachability": self.direct_reachability,
            "pathLengthTiles": self.path_length_tiles,
            "collisionWindowAvailable": self.collision_window_available,
        }


@dataclass
class PathingContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    pathing_needed: bool = False
    destination: dict[str, Any] | None = None
    destination_tile: dict[str, Any] | None = None
    destination_world_x: int | None = None
    destination_world_y: int | None = None
    destination_plane: int | None = None
    destination_scene_x: int | None = None
    destination_scene_y: int | None = None
    destination_tile_source: str | None = None
    local_reachability: str = "unknown"
    path_length_tiles: int | None = None
    next_waypoint_tile: dict[str, Any] | None = None
    final_approach_tile: dict[str, Any] | str | None = None
    final_approach_tile_source: str | None = None
    final_approach_candidate_count: int | None = None
    rejected_approach_tile_reasons: dict[str, int] = field(default_factory=dict)
    final_approach_tile_used: bool | None = None
    path_target_tile: dict[str, Any] | None = None
    path_target_tile_source: str | None = None
    predicted_path_tiles: list[dict[str, Any]] = field(default_factory=list)
    predicted_step_count: int | None = None
    predicted_path_count: int | None = None
    predicted_path_displayed_count: int | None = None
    predicted_path_available_count: int | None = None
    path_was_capped: bool = False
    path_display_was_capped: bool = False
    overlay_predicted_path_limit: int | None = None
    diagonal_step_count: int = 0
    cardinal_step_count: int = 0
    path_segments_valid: bool | None = None
    invalid_path_segment_count: int = 0
    invalid_path_segments: list[dict[str, Any]] = field(default_factory=list)
    first_invalid_path_segment: dict[str, Any] | None = None
    predicted_run_segments: list[dict[str, Any]] = field(default_factory=list)
    predicted_movement_model: str = "cardinal_only"
    predicted_movement_notes: list[str] = field(default_factory=lambda: ["Predicted local path; exact server movement may differ."])
    prediction_confidence: float | None = None
    path_cap_tiles: int | None = None
    exact_destination_reached: bool | None = None
    final_approach_substituted: bool | None = None
    approach_candidates_tested: int | None = None
    approach_candidates_rejected_by_blocked_side: int = 0
    approach_candidates_rejected_by_no_line_of_sight: int = 0
    selected_approach_reason: str | None = None
    approach_quality: str | None = None
    side_access_valid: bool | None = None
    line_of_sight_to_target: bool | None = None
    skipped_run_tiles: list[dict[str, Any]] = field(default_factory=list)
    run_behavior: str = "unknown"
    reason: str = "not_needed"
    pathing_millis: float | None = None
    path_nodes_expanded: int = 0
    pathing_budget_exceeded: bool = False
    collision_window_available: bool | None = None
    collision_window_fresh: bool | None = None
    collision_window_radius: int | None = None
    collision_window_center_world: dict[str, Any] | None = None
    collision_window_plane: int | None = None
    collision_window_age_ticks: int | None = None
    destination_inside_collision_window: bool | None = None
    destination_plane_matches: bool | None = None
    collision_window_missing_reason: str | None = None
    path_intent_key: str | None = None
    destination_target_key: str | None = None
    path_intent_retained: bool = False
    path_stable_for_ticks: int | None = None
    path_started_tick: int | None = None
    path_last_updated_tick: int | None = None
    movement_state: str = "unknown"
    retention_reason: str | None = None
    switch_reason: str | None = None
    arrived_at_final_approach: bool = False
    arrived_near_destination: bool = False
    distance_to_final_approach: int | None = None
    distance_to_destination: int | None = None
    distance_to_path_target: int | None = None
    arrived_stable_for_ticks: int = 0
    arrival_reason: str | None = None
    service_ready: bool = False
    service_ready_reason: str | None = None
    service_ready_stable_for_ticks: int = 0
    path_completed: bool = False
    path_completion_reason: str | None = None
    retained_path_after_arrival: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.contract_payload(),
            "pathingNeeded": self.pathing_needed,
            "destination": self.destination,
            "destinationTile": self.destination_tile,
            "destinationWorldX": self.destination_world_x,
            "destinationWorldY": self.destination_world_y,
            "destinationPlane": self.destination_plane,
            "destinationSceneX": self.destination_scene_x,
            "destinationSceneY": self.destination_scene_y,
            "destinationTileSource": self.destination_tile_source,
            "localReachability": self.local_reachability,
            "pathLengthTiles": self.path_length_tiles,
            "nextWaypointTile": self.next_waypoint_tile,
            "finalApproachTile": self.final_approach_tile,
            "finalApproachTileSource": self.final_approach_tile_source,
            "finalApproachCandidateCount": self.final_approach_candidate_count,
            "rejectedApproachTileReasons": dict(self.rejected_approach_tile_reasons),
            "finalApproachTileUsed": self.final_approach_tile_used,
            "pathTargetTile": self.path_target_tile,
            "pathTargetTileSource": self.path_target_tile_source,
            "predictedPathTiles": list(self.predicted_path_tiles),
            "predictedStepCount": self.predicted_step_count,
            "predictedPathCount": self.predicted_path_count,
            "predictedPathDisplayedCount": self.predicted_path_displayed_count,
            "predictedPathAvailableCount": self.predicted_path_available_count,
            "pathWasCapped": self.path_was_capped,
            "pathDisplayWasCapped": self.path_display_was_capped,
            "overlayPredictedPathLimit": self.overlay_predicted_path_limit,
            "diagonalStepCount": self.diagonal_step_count,
            "cardinalStepCount": self.cardinal_step_count,
            "pathSegmentsValid": self.path_segments_valid,
            "invalidPathSegmentCount": self.invalid_path_segment_count,
            "invalidPathSegments": list(self.invalid_path_segments),
            "firstInvalidPathSegment": self.first_invalid_path_segment,
            "predictedRunSegments": list(self.predicted_run_segments),
            "predictedMovementModel": self.predicted_movement_model,
            "predictedMovementNotes": list(self.predicted_movement_notes),
            "predictionConfidence": self.prediction_confidence,
            "pathCapTiles": self.path_cap_tiles,
            "exactDestinationReached": self.exact_destination_reached,
            "finalApproachSubstituted": self.final_approach_substituted,
            "approachCandidatesTested": self.approach_candidates_tested,
            "approachCandidatesRejectedByBlockedSide": self.approach_candidates_rejected_by_blocked_side,
            "approachCandidatesRejectedByNoLineOfSight": self.approach_candidates_rejected_by_no_line_of_sight,
            "selectedApproachReason": self.selected_approach_reason,
            "approachQuality": self.approach_quality,
            "sideAccessValid": self.side_access_valid,
            "lineOfSightToTarget": self.line_of_sight_to_target,
            "skippedRunTiles": list(self.skipped_run_tiles),
            "runBehavior": self.run_behavior,
            "reason": self.reason,
            "pathingMillis": self.pathing_millis,
            "pathNodesExpanded": self.path_nodes_expanded,
            "pathingBudgetExceeded": self.pathing_budget_exceeded,
            "collisionWindowAvailable": self.collision_window_available,
            "collisionWindowFresh": self.collision_window_fresh,
            "collisionWindowRadius": self.collision_window_radius,
            "collisionWindowCenterWorld": self.collision_window_center_world,
            "collisionWindowPlane": self.collision_window_plane,
            "collisionWindowAgeTicks": self.collision_window_age_ticks,
            "destinationInsideCollisionWindow": self.destination_inside_collision_window,
            "destinationPlaneMatches": self.destination_plane_matches,
            "collisionWindowMissingReason": self.collision_window_missing_reason,
            "pathIntentKey": self.path_intent_key,
            "destinationTargetKey": self.destination_target_key,
            "pathIntentRetained": self.path_intent_retained,
            "pathStableForTicks": self.path_stable_for_ticks,
            "pathStartedTick": self.path_started_tick,
            "pathLastUpdatedTick": self.path_last_updated_tick,
            "movementState": self.movement_state,
            "retentionReason": self.retention_reason,
            "switchReason": self.switch_reason,
            "arrivedAtFinalApproach": self.arrived_at_final_approach,
            "arrivedNearDestination": self.arrived_near_destination,
            "distanceToFinalApproach": self.distance_to_final_approach,
            "distanceToDestination": self.distance_to_destination,
            "distanceToPathTarget": self.distance_to_path_target,
            "arrivedStableForTicks": self.arrived_stable_for_ticks,
            "arrivalReason": self.arrival_reason,
            "serviceReady": self.service_ready,
            "serviceReadyReason": self.service_ready_reason,
            "serviceReadyStableForTicks": self.service_ready_stable_for_ticks,
            "pathCompleted": self.path_completed,
            "pathCompletionReason": self.path_completion_reason,
            "retainedPathAfterArrival": self.retained_path_after_arrival,
        }


@dataclass
class ActivityContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    current_activity: str = "unknown"
    recent_task_signals: list[str] = field(default_factory=list)
    liveness: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntentOverlayContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    overlay: dict[str, Any] = field(default_factory=dict)
    markers: list[dict[str, Any]] = field(default_factory=list)
    selected_marker: dict[str, Any] | None = None
    backup_markers: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BrainContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    decision: dict[str, Any] = field(default_factory=dict)
    updated_state: dict[str, Any] = field(default_factory=dict)
    status_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class ServiceContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    service_required: bool = False
    service_type_needed: str | None = None
    best_service_candidate: dict[str, Any] | None = None
    nearest_service_candidate: dict[str, Any] | None = None
    service_candidates: list[dict[str, Any]] = field(default_factory=list)
    candidates_by_type: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    candidate_counts_by_type: dict[str, int] = field(default_factory=dict)
    candidate_counts_by_service_group: dict[str, int] = field(default_factory=dict)
    candidate_count: int = 0
    visible_primary_service_target_count: int = 0
    visible_deposit_service_target_count: int = 0
    source_stage_counts: dict[str, dict[str, Any]] = field(default_factory=dict)
    memory_lifecycle: dict[str, Any] = field(default_factory=dict)
    reachable_count: int | None = None
    unknown_reachability_count: int | None = None
    reason: str | None = None
    service_target_retained: bool = False
    retained_service_target_name: str | None = None
    retained_service_missing_ticks: int | None = None
    retained_service_candidate_count: int = 0
    retained_best_service_candidate: dict[str, Any] | None = None
    retained_service_age_ticks: int | None = None
    preferred_service_types_seen: list[str] = field(default_factory=list)
    preferred_service_types_recently_seen: list[str] = field(default_factory=list)
    missing_preferred_reason: str | None = None
    selected_service_target_source: str | None = None
    primary_service_visible: bool = False
    primary_service_retained: bool = False
    deposit_fallback_allowed: bool = True
    selected_service_group: str | None = None
    logic_error: bool = False
    service_switch_reason: str | None = None
    service_candidate_dropped_reason: str | None = None
    service_ready: bool = False
    service_ready_reason: str | None = None
    service_ready_stable_for_ticks: int = 0
    selected_service_target_name: str | None = None
    selected_service_target_tile: dict[str, Any] | None = None
    distance_to_service_target: int | None = None
    arrived_at_final_approach: bool = False
    arrived_near_destination: bool = False
    distance_to_final_approach: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.contract_payload(),
            "serviceRequired": self.service_required,
            "serviceNeeded": self.service_required,
            "serviceTypeNeeded": self.service_type_needed,
            "bestServiceCandidate": self.best_service_candidate,
            "nearestServiceCandidate": self.nearest_service_candidate,
            "serviceCandidates": list(self.service_candidates),
            "candidatesByType": {key: list(value) for key, value in self.candidates_by_type.items()},
            "candidateCountsByType": dict(self.candidate_counts_by_type),
            "candidateCountsByServiceGroup": dict(self.candidate_counts_by_service_group),
            "candidateCount": self.candidate_count,
            "visiblePrimaryServiceTargetCount": self.visible_primary_service_target_count,
            "visibleDepositServiceTargetCount": self.visible_deposit_service_target_count,
            "sourceStageCounts": {key: dict(value) for key, value in self.source_stage_counts.items()},
            "memoryLifecycle": dict(self.memory_lifecycle),
            "reachableCount": self.reachable_count,
            "unknownReachabilityCount": self.unknown_reachability_count,
            "reason": self.reason,
            "serviceTargetRetained": self.service_target_retained,
            "retainedServiceTargetName": self.retained_service_target_name,
            "retainedServiceMissingTicks": self.retained_service_missing_ticks,
            "retainedServiceCandidateCount": self.retained_service_candidate_count,
            "retainedBestServiceCandidate": self.retained_best_service_candidate,
            "retainedServiceAgeTicks": self.retained_service_age_ticks,
            "preferredServiceTypesSeen": list(self.preferred_service_types_seen),
            "preferredServiceTypesRecentlySeen": list(self.preferred_service_types_recently_seen),
            "missingPreferredReason": self.missing_preferred_reason,
            "selectedServiceTargetSource": self.selected_service_target_source,
            "primaryServiceVisible": self.primary_service_visible,
            "primaryServiceRetained": self.primary_service_retained,
            "depositFallbackAllowed": self.deposit_fallback_allowed,
            "selectedServiceGroup": self.selected_service_group,
            "logicError": self.logic_error,
            "serviceSwitchReason": self.service_switch_reason,
            "serviceCandidateDroppedReason": self.service_candidate_dropped_reason,
            "serviceReady": self.service_ready,
            "serviceReadyReason": self.service_ready_reason,
            "serviceReadyStableForTicks": self.service_ready_stable_for_ticks,
            "selectedServiceTargetName": self.selected_service_target_name,
            "selectedServiceTargetTile": self.selected_service_target_tile,
            "distanceToServiceTarget": self.distance_to_service_target,
            "arrivedAtFinalApproach": self.arrived_at_final_approach,
            "arrivedNearDestination": self.arrived_near_destination,
            "distanceToFinalApproach": self.distance_to_final_approach,
        }


@dataclass
class ProcessInventoryContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    process_required: bool = False
    process_type_needed: str | None = None
    resource_disposition: str | None = None
    resources_available: bool = False
    held_resource_count: int | None = None
    service_type_needed: str | None = None
    tinderbox_present: bool | None = None
    tinderbox_status: str | None = None
    inventory_items_known: bool | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.contract_payload(),
            "processRequired": self.process_required,
            "processTypeNeeded": self.process_type_needed,
            "resourceDisposition": self.resource_disposition,
            "resourcesAvailable": self.resources_available,
            "heldResourceCount": self.held_resource_count,
            "serviceTypeNeeded": self.service_type_needed,
            "tinderboxPresent": self.tinderbox_present,
            "tinderboxStatus": self.tinderbox_status,
            "inventoryItemsKnown": self.inventory_items_known,
            "reason": self.reason,
        }


@dataclass
class BankUiContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    bank_open: bool | None = None
    bank_pin_open: bool | None = None
    bank_readable: bool = False
    bank_container_readable: bool = False
    bank_inventory_readable: bool = False
    deposit_inventory_available: bool | None = None
    close_button_available: bool | None = None
    top_level_interface_id: int | None = None
    bank_root_visible: bool | None = None
    bank_container_visible: bool | None = None
    bank_inventory_visible: bool | None = None
    deposit_inventory_button_visible: bool | None = None
    bank_close_button_visible: bool | None = None
    inventory_summary: dict[str, Any] = field(default_factory=dict)
    bank_summary: dict[str, Any] = field(default_factory=dict)
    service_ready: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.contract_payload(),
            "bankOpen": self.bank_open,
            "bankPinOpen": self.bank_pin_open,
            "bankReadable": self.bank_readable,
            "bankContainerReadable": self.bank_container_readable,
            "bankInventoryReadable": self.bank_inventory_readable,
            "depositInventoryAvailable": self.deposit_inventory_available,
            "closeButtonAvailable": self.close_button_available,
            "topLevelInterfaceId": self.top_level_interface_id,
            "bankRootVisible": self.bank_root_visible,
            "bankContainerVisible": self.bank_container_visible,
            "bankInventoryVisible": self.bank_inventory_visible,
            "depositInventoryButtonVisible": self.deposit_inventory_button_visible,
            "bankCloseButtonVisible": self.bank_close_button_visible,
            "inventorySummary": dict(self.inventory_summary),
            "bankSummary": dict(self.bank_summary),
            "serviceReady": self.service_ready,
            "reason": self.reason,
        }


@dataclass
class LiveAnalysisResult:
    input_snapshot: LiveInputSnapshot | None = None
    source_status: LiveSourceStatus | None = None
    player: PlayerContext | None = None
    inventory: InventoryContext | None = None
    targets: TargetContext | None = None
    navigation: NavigationContext | None = None
    navigation_intent: NavigationIntentContext | None = None
    pathing: PathingContext | None = None
    activity: ActivityContext | None = None
    service: ServiceContext | None = None
    process_inventory: ProcessInventoryContext | None = None
    bank_ui: BankUiContext | None = None
    intent_overlay: IntentOverlayContext | None = None
    brain: BrainContext | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

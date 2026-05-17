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
    service_candidate_inputs: list[dict[str, Any]] = field(default_factory=list)
    raw_best_target: dict[str, Any] | None = None
    nearest_target: dict[str, Any] | None = None
    top_candidates: list[dict[str, Any]] = field(default_factory=list)
    candidate_count: int = 0
    profile_candidate_count: int = 0
    broad_candidate_count: int = 0
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
    predicted_path_tiles: list[dict[str, Any]] = field(default_factory=list)
    predicted_step_count: int | None = None
    predicted_path_count: int | None = None
    predicted_path_displayed_count: int | None = None
    path_was_capped: bool = False
    diagonal_step_count: int = 0
    cardinal_step_count: int = 0
    predicted_run_segments: list[dict[str, Any]] = field(default_factory=list)
    predicted_movement_model: str = "cardinal_only"
    predicted_movement_notes: list[str] = field(default_factory=lambda: ["Predicted local path; exact server movement may differ."])
    prediction_confidence: float | None = None
    path_cap_tiles: int | None = None
    exact_destination_reached: bool | None = None
    final_approach_substituted: bool | None = None
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
            "predictedPathTiles": list(self.predicted_path_tiles),
            "predictedStepCount": self.predicted_step_count,
            "predictedPathCount": self.predicted_path_count,
            "predictedPathDisplayedCount": self.predicted_path_displayed_count,
            "pathWasCapped": self.path_was_capped,
            "diagonalStepCount": self.diagonal_step_count,
            "cardinalStepCount": self.cardinal_step_count,
            "predictedRunSegments": list(self.predicted_run_segments),
            "predictedMovementModel": self.predicted_movement_model,
            "predictedMovementNotes": list(self.predicted_movement_notes),
            "predictionConfidence": self.prediction_confidence,
            "pathCapTiles": self.path_cap_tiles,
            "exactDestinationReached": self.exact_destination_reached,
            "finalApproachSubstituted": self.final_approach_substituted,
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
    candidate_count: int = 0
    reachable_count: int | None = None
    unknown_reachability_count: int | None = None
    reason: str | None = None

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
            "candidateCount": self.candidate_count,
            "reachableCount": self.reachable_count,
            "unknownReachabilityCount": self.unknown_reachability_count,
            "reason": self.reason,
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
    intent_overlay: IntentOverlayContext | None = None
    brain: BrainContext | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

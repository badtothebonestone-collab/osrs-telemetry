from __future__ import annotations

import time
from copy import deepcopy
from collections import Counter, deque
from dataclasses import dataclass, is_dataclass
from typing import Any

import capabilities
import navigation_reachability

from analyzers.live_state import PathingContext


DEFAULT_MAX_PREDICTED_TILES = 24
DEFAULT_MAX_NODES = 512
DEFAULT_BUDGET_MILLIS = 5.0
ARRIVAL_READY_STABLE_TICKS = 2
SERVICE_READY_GRACE_TICKS = 4
PREDICTION_NOTE = "Predicted local path; exact server movement may differ."
MOVEMENT_MODELS = {"cardinal_only", "osrs_like_predicted", "diagonal_guarded"}
OSRS_LIKE_DIRECTIONS = (
    (-1, 0),
    (1, 0),
    (0, -1),
    (0, 1),
    (-1, -1),
    (1, -1),
    (-1, 1),
    (1, 1),
)
CARDINAL_DIRECTIONS = tuple((dx, dy) for dx, dy, _source, _dest in navigation_reachability.DIRECTIONS)


@dataclass
class ApproachCandidate:
    tile: tuple[int, int]
    tile_kind: str
    direction_index: int
    side_access_valid: bool | None = None
    line_of_sight_to_target: bool | None = None
    side_access_reason: str | None = None


@dataclass
class LocalPathResult:
    reachability: str
    reason: str
    scene_path: list[tuple[int, int]]
    expanded: int
    budget_exceeded: bool
    exact_destination_reached: bool
    final_approach_scene_tile: tuple[int, int] | None = None
    final_approach_tile_source: str | None = None
    final_approach_candidate_count: int = 0
    rejected_approach_tile_reasons: dict[str, int] | None = None
    path_target_scene_tile: tuple[int, int] | None = None
    path_target_tile_source: str | None = None
    approach_candidates_tested: int = 0
    approach_candidates_rejected_by_blocked_side: int = 0
    approach_candidates_rejected_by_no_line_of_sight: int = 0
    selected_approach_reason: str | None = None
    approach_quality: str | None = None
    side_access_valid: bool | None = None
    line_of_sight_to_target: bool | None = None
    invalid_path_segments: list[dict[str, Any]] | None = None
    frontier_distance_before: int | None = None
    frontier_distance_after_estimate: int | None = None
    progress_score: int | None = None


@dataclass
class PathIntentState:
    active_path_intent_key: str | None = None
    active_phase_key: str | None = None
    destination_target_key: str | None = None
    destination_tile: dict[str, Any] | None = None
    final_approach_tile: dict[str, Any] | str | None = None
    next_waypoint_tile: dict[str, Any] | None = None
    predicted_path_tiles: list[dict[str, Any]] | None = None
    retained_path_fields: dict[str, Any] | None = None
    path_started_tick: int | None = None
    last_updated_tick: int | None = None
    stable_for_ticks: int = 0
    retention_reason: str | None = None
    switch_reason: str | None = None
    last_player_tile: dict[str, Any] | None = None
    last_player_tile_changed_tick: int | None = None
    pending_switch_key: str | None = None
    pending_switch_ticks: int = 0
    switch_debounce_ticks: int = 2
    arrival_key: str | None = None
    arrived_stable_for_ticks: int = 0
    service_ready: bool = False
    service_ready_stable_for_ticks: int = 0
    service_ready_started_tick: int | None = None
    service_ready_last_tick: int | None = None

    def clear(self, *, reason: str | None = None) -> None:
        self.active_path_intent_key = None
        self.active_phase_key = None
        self.destination_target_key = None
        self.destination_tile = None
        self.final_approach_tile = None
        self.next_waypoint_tile = None
        self.predicted_path_tiles = None
        self.retained_path_fields = None
        self.path_started_tick = None
        self.last_updated_tick = None
        self.stable_for_ticks = 0
        self.retention_reason = None
        self.switch_reason = reason
        self.pending_switch_key = None
        self.pending_switch_ticks = 0
        self.arrival_key = None
        self.arrived_stable_for_ticks = 0
        self.service_ready = False
        self.service_ready_stable_for_ticks = 0
        self.service_ready_started_tick = None
        self.service_ready_last_tick = None

def context_value(context: Any, snake_key: str, camel_key: str | None = None, default: Any = None) -> Any:
    if context is None:
        return default
    camel_key = camel_key or snake_key
    if isinstance(context, dict):
        if snake_key in context:
            return context.get(snake_key)
        return context.get(camel_key, default)
    if hasattr(context, snake_key):
        return getattr(context, snake_key)
    if hasattr(context, camel_key):
        return getattr(context, camel_key)
    if is_dataclass(context):
        return getattr(context, snake_key, default)
    return default


def int_value(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def target_payload(target: Any) -> dict[str, Any] | None:
    if not isinstance(target, dict) or not target:
        return None
    return dict(target)


def source_tick_from(*contexts: Any) -> int | None:
    for context in contexts:
        value = context_value(context, "source_tick", "sourceTick")
        if isinstance(value, int):
            return value
    return None


def collision_payload(navigation_context: Any) -> dict[str, Any] | None:
    normalized = context_value(navigation_context, "collision_window_tiles", "collisionWindowTiles")
    if isinstance(normalized, dict) and isinstance(normalized.get("flags"), list):
        return normalized
    raw = context_value(navigation_context, "raw")
    if not isinstance(raw, dict):
        raw = navigation_context if isinstance(navigation_context, dict) else {}
    for key in ("collisionWindow", "collision_window", "collision"):
        value = raw.get(key)
        if isinstance(value, dict) and isinstance(value.get("flags"), list):
            return value
    if isinstance(raw.get("flags"), list):
        return raw
    return None


def collision_window_is_available(navigation_context: Any) -> bool | None:
    value = context_value(navigation_context, "collision_window_available", "collisionWindowAvailable")
    if isinstance(value, bool):
        return value
    raw = context_value(navigation_context, "raw")
    if isinstance(raw, dict):
        value = raw.get("collisionWindowAvailable")
        if isinstance(value, bool):
            return value
    return None


def collision_window_field(navigation_context: Any, snake_key: str, camel_key: str) -> Any:
    value = context_value(navigation_context, snake_key, camel_key)
    if value is not None:
        return value
    raw = context_value(navigation_context, "raw")
    if isinstance(raw, dict):
        return raw.get(camel_key)
    return None


def collision_window_fresh(navigation_context: Any) -> bool | None:
    value = collision_window_field(navigation_context, "collision_window_fresh", "collisionWindowFresh")
    return value if isinstance(value, bool) else None


def collision_window_age_ticks(navigation_context: Any) -> int | None:
    return int_value(collision_window_field(navigation_context, "collision_window_age_ticks", "collisionWindowAgeTicks"))


def collision_window_radius(navigation_context: Any, window: navigation_reachability.CollisionWindow | None) -> int | None:
    value = int_value(collision_window_field(navigation_context, "collision_window_radius", "collisionWindowRadius"))
    if value is not None:
        return value
    return window.radius if window is not None else None


def collision_window_center_world(navigation_context: Any) -> dict[str, Any] | None:
    value = collision_window_field(navigation_context, "collision_window_center_world", "collisionWindowCenterWorld")
    return dict(value) if isinstance(value, dict) else None


def collision_window_plane(navigation_context: Any, window: navigation_reachability.CollisionWindow | None) -> int | None:
    value = int_value(collision_window_field(navigation_context, "collision_window_plane", "collisionWindowPlane"))
    if value is not None:
        return value
    return window.plane if window is not None else None


def collision_window_missing_reason(navigation_context: Any, payload: dict[str, Any] | None, window: navigation_reachability.CollisionWindow | None) -> str | None:
    value = collision_window_field(navigation_context, "collision_window_missing_reason", "collisionWindowMissingReason")
    if isinstance(value, str) and value:
        return value
    if payload is None:
        return "collision_window_missing"
    if window is None:
        return "collision_window_payload_without_flags"
    if collision_window_fresh(navigation_context) is False:
        return "collision_window_stale"
    return None


def player_field(player_context: Any, snake_key: str, camel_key: str) -> int | None:
    value = context_value(player_context, snake_key, camel_key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raw = context_value(player_context, "raw")
    if isinstance(raw, dict):
        raw_value = raw.get(camel_key)
        if isinstance(raw_value, int) and not isinstance(raw_value, bool):
            return raw_value
    return None


def tile_dict(world_x: int | None, world_y: int | None, plane: int | None) -> dict[str, Any] | None:
    if world_x is None or world_y is None or plane is None:
        return None
    return {"worldX": world_x, "worldY": world_y, "plane": plane}


def scene_to_world(
    scene_x: int,
    scene_y: int,
    *,
    player_world_x: int | None,
    player_world_y: int | None,
    player_scene_x: int | None,
    player_scene_y: int | None,
    plane: int | None,
) -> dict[str, Any] | None:
    if player_world_x is None or player_world_y is None or player_scene_x is None or player_scene_y is None or plane is None:
        return None
    return {
        "worldX": player_world_x + (scene_x - player_scene_x),
        "worldY": player_world_y + (scene_y - player_scene_y),
        "plane": plane,
    }


def target_scene_from_world(
    target: dict[str, Any],
    *,
    player_world_x: int | None,
    player_world_y: int | None,
    player_scene_x: int | None,
    player_scene_y: int | None,
) -> tuple[int | None, int | None]:
    scene_x = int_value(target.get("sceneX"))
    scene_y = int_value(target.get("sceneY"))
    if scene_x is not None and scene_y is not None:
        return scene_x, scene_y
    world_x = int_value(target.get("worldX"))
    world_y = int_value(target.get("worldY"))
    if None in (world_x, world_y, player_world_x, player_world_y, player_scene_x, player_scene_y):
        return None, None
    assert world_x is not None and world_y is not None and player_world_x is not None and player_world_y is not None
    assert player_scene_x is not None and player_scene_y is not None
    return player_scene_x + (world_x - player_world_x), player_scene_y + (world_y - player_world_y)


def destination_from_navigation_intent(navigation_intent_context: Any) -> dict[str, Any] | None:
    target = context_value(navigation_intent_context, "destination_target", "destinationTarget")
    return target_payload(target)


def target_label(target: dict[str, Any] | None) -> str:
    if not isinstance(target, dict) or not target:
        return "none"
    return str(target.get("targetName") or target.get("name") or target.get("classId") or "target")


def tile_key(tile: dict[str, Any] | None) -> str | None:
    if not isinstance(tile, dict):
        return None
    world_x = int_value(tile.get("worldX"))
    world_y = int_value(tile.get("worldY"))
    plane = int_value(tile.get("plane"))
    if world_x is None or world_y is None or plane is None:
        return None
    return f"{world_x}:{world_y}:{plane}"


def player_tile(player_context: Any) -> dict[str, Any] | None:
    return tile_dict(
        player_field(player_context, "world_x", "worldX"),
        player_field(player_context, "world_y", "worldY"),
        player_field(player_context, "plane", "plane"),
    )


def destination_target_key(destination: dict[str, Any] | None) -> str | None:
    if not isinstance(destination, dict) or not destination:
        return None
    for key in ("objectKey", "targetKey", "candidateKey"):
        value = destination.get(key)
        if value:
            return f"{key}:{value}"
    value = destination.get("hash")
    if value is not None:
        return f"hash:{value}"
    target_type = destination.get("targetType") or destination.get("target_type") or ""
    class_id = destination.get("classId") or destination.get("class_id") or ""
    object_id = destination.get("id")
    world_x = destination.get("worldX")
    world_y = destination.get("worldY")
    plane = destination.get("plane")
    if object_id is not None and world_x is not None and world_y is not None and plane is not None:
        return f"id-world:{object_id}:{world_x}:{world_y}:{plane}:{target_type}:{class_id}"
    scene_x = destination.get("sceneX")
    scene_y = destination.get("sceneY")
    if object_id is not None and scene_x is not None and scene_y is not None and plane is not None:
        return f"id-scene:{object_id}:{scene_x}:{scene_y}:{plane}"
    return f"{target_type}:{class_id}:{world_x}:{world_y}:{plane}"


def phase_key(generic_task_state: Any) -> str:
    phase = context_value(generic_task_state, "phase", "phase", "unknown")
    active_intent = context_value(generic_task_state, "active_intent", "activeIntent", "unknown")
    task = context_value(generic_task_state, "task", "task", "unknown")
    return f"{task}:{phase}:{active_intent}"


def build_path_intent_key(
    *,
    destination: dict[str, Any] | None,
    destination_tile: dict[str, Any] | None,
    generic_task_state: Any,
) -> str | None:
    target_key = destination_target_key(destination)
    tile = tile_key(destination_tile)
    if not target_key and not tile:
        return None
    target_type = destination.get("targetType") if isinstance(destination, dict) else None
    class_id = destination.get("classId") if isinstance(destination, dict) else None
    return "|".join(
        str(part)
        for part in (
            phase_key(generic_task_state),
            target_key or "target:none",
            tile or "tile:none",
            target_type or "",
            class_id or "",
        )
    )


def infer_movement_state(
    *,
    state: PathIntentState | None,
    current_player_tile: dict[str, Any] | None,
    activity_context: Any,
    tick: int | None,
    recent_ticks: int = 2,
) -> str:
    explicit = str(context_value(activity_context, "current_activity", "currentActivity", "") or "").lower()
    raw = context_value(activity_context, "raw")
    raw_activity = raw.get("activity") if isinstance(raw, dict) and isinstance(raw.get("activity"), dict) else {}
    raw_is_moving = raw_activity.get("isMoving") if isinstance(raw_activity, dict) else None
    if explicit == "moving" or raw_is_moving is True:
        return "moving"
    if state is None or current_player_tile is None:
        return "unknown"
    current_key = tile_key(current_player_tile)
    previous_key = tile_key(state.last_player_tile)
    if current_key and previous_key and current_key != previous_key:
        return "moving"
    if tick is not None and state.last_player_tile_changed_tick is not None:
        if max(0, int(tick) - int(state.last_player_tile_changed_tick)) <= recent_ticks:
            return "recently_moved"
    if current_key:
        return "stationary"
    return "unknown"


def update_player_movement_state(state: PathIntentState | None, current_player_tile: dict[str, Any] | None, tick: int | None) -> None:
    if state is None or current_player_tile is None:
        return
    current_key = tile_key(current_player_tile)
    previous_key = tile_key(state.last_player_tile)
    if current_key and previous_key and current_key != previous_key:
        state.last_player_tile_changed_tick = tick
    elif current_key and previous_key is None:
        state.last_player_tile_changed_tick = tick
    state.last_player_tile = deepcopy(current_player_tile)


RETAINED_PATH_ATTRIBUTES = (
    "destination",
    "destination_tile",
    "destination_world_x",
    "destination_world_y",
    "destination_plane",
    "destination_scene_x",
    "destination_scene_y",
    "destination_tile_source",
    "local_reachability",
    "path_length_tiles",
    "next_waypoint_tile",
    "final_approach_tile",
    "final_approach_tile_source",
    "final_approach_candidate_count",
    "rejected_approach_tile_reasons",
    "final_approach_tile_used",
    "path_target_tile",
    "path_target_tile_source",
    "predicted_path_tiles",
    "predicted_step_count",
    "predicted_path_count",
    "predicted_path_displayed_count",
    "predicted_path_available_count",
    "path_was_capped",
    "path_display_was_capped",
    "overlay_predicted_path_limit",
    "diagonal_step_count",
    "cardinal_step_count",
    "path_segments_valid",
    "invalid_path_segment_count",
    "invalid_path_segments",
    "first_invalid_path_segment",
    "predicted_run_segments",
    "predicted_movement_model",
    "predicted_movement_notes",
    "prediction_confidence",
    "path_cap_tiles",
    "exact_destination_reached",
    "final_approach_substituted",
    "approach_candidates_tested",
    "approach_candidates_rejected_by_blocked_side",
    "approach_candidates_rejected_by_no_line_of_sight",
    "selected_approach_reason",
    "approach_quality",
    "side_access_valid",
    "line_of_sight_to_target",
    "skipped_run_tiles",
    "run_behavior",
    "reason",
    "arrived_at_final_approach",
    "arrived_near_destination",
    "distance_to_final_approach",
    "distance_to_destination",
    "distance_to_path_target",
    "arrived_stable_for_ticks",
    "arrival_reason",
    "service_ready",
    "service_ready_reason",
    "service_ready_stable_for_ticks",
    "path_completed",
    "path_completion_reason",
    "retained_path_after_arrival",
)


def snapshot_path_fields(context: PathingContext) -> dict[str, Any]:
    return {name: deepcopy(getattr(context, name)) for name in RETAINED_PATH_ATTRIBUTES}


def apply_retained_path_fields(context: PathingContext, state: PathIntentState) -> None:
    for name, value in (state.retained_path_fields or {}).items():
        setattr(context, name, deepcopy(value))


def arrived_at_final_approach(current_player_tile: dict[str, Any] | None, state: PathIntentState | None) -> bool:
    if state is None or not isinstance(state.final_approach_tile, dict):
        return False
    return tile_key(current_player_tile) == tile_key(state.final_approach_tile)


def tile_distance(left: dict[str, Any] | None, right: dict[str, Any] | None) -> int | None:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    left_x = int_value(left.get("worldX"))
    left_y = int_value(left.get("worldY"))
    left_plane = int_value(left.get("plane"))
    right_x = int_value(right.get("worldX"))
    right_y = int_value(right.get("worldY"))
    right_plane = int_value(right.get("plane"))
    if None in (left_x, left_y, left_plane, right_x, right_y, right_plane):
        return None
    if left_plane != right_plane:
        return None
    assert left_x is not None and left_y is not None and right_x is not None and right_y is not None
    return max(abs(left_x - right_x), abs(left_y - right_y))


def approach_radius_tiles(destination: dict[str, Any] | None) -> int:
    if not isinstance(destination, dict):
        return 1
    for key in ("approachRadiusTiles", "interactionRadiusTiles", "navigationInteractionRadiusTiles"):
        value = int_value(destination.get(key))
        if value is not None:
            return max(0, min(8, value))
    return 1


def destination_is_route_transition(destination: dict[str, Any] | None) -> bool:
    if not isinstance(destination, dict):
        return False
    if destination.get("navigationObstacle") is True:
        return True
    for key in ("routeObjectKind", "classId", "class_id", "targetKind", "target_kind"):
        value = str(destination.get(key) or "").lower()
        if value in {"route_transition", "service_route_transition", "service_route"}:
            return True
    return False


def destination_allows_service_ready(destination: dict[str, Any] | None, target_kind: str) -> bool:
    if target_kind != "service":
        return False
    return not destination_is_route_transition(destination)


def update_arrival_context(
    context: PathingContext,
    *,
    state: PathIntentState | None,
    current_player_tile: dict[str, Any] | None,
    movement_state: str,
    intent_key: str | None,
    tick: int | None,
    service_ready_allowed: bool,
) -> None:
    context.distance_to_final_approach = tile_distance(
        current_player_tile,
        context.final_approach_tile if isinstance(context.final_approach_tile, dict) else None,
    )
    context.distance_to_destination = tile_distance(current_player_tile, context.destination_tile)
    context.distance_to_path_target = tile_distance(current_player_tile, context.path_target_tile)
    context.arrived_at_final_approach = context.distance_to_final_approach == 0
    context.arrived_near_destination = (
        context.distance_to_destination is not None
        and context.distance_to_destination <= approach_radius_tiles(context.destination)
        and movement_state != "moving"
    )
    arrived_at_path_target = context.distance_to_path_target == 0 if context.distance_to_path_target is not None else False
    arrival_candidate = bool(context.arrived_at_final_approach or context.arrived_near_destination or arrived_at_path_target)
    if context.arrived_at_final_approach:
        base_reason = "arrived_at_final_approach"
    elif arrived_at_path_target:
        base_reason = "arrived_at_path_target"
    elif context.arrived_near_destination:
        base_reason = "arrived_near_destination"
    else:
        base_reason = "not_arrived"

    if state is None:
        context.arrival_reason = base_reason
        context.arrived_stable_for_ticks = 1 if arrival_candidate and movement_state != "moving" else 0
        return

    if not arrival_candidate:
        state.arrival_key = None
        state.arrived_stable_for_ticks = 0
        state.service_ready = False
        state.service_ready_stable_for_ticks = 0
        state.service_ready_started_tick = None
        context.arrived_stable_for_ticks = 0
        context.service_ready = False
        context.service_ready_reason = "not_arrived"
        context.arrival_reason = base_reason
        return

    arrival_key = "|".join(
        part
        for part in (
            intent_key or "intent:none",
            tile_key(context.final_approach_tile if isinstance(context.final_approach_tile, dict) else None) or "final:none",
            tile_key(context.path_target_tile) or "path_target:none",
            tile_key(context.destination_tile) or "destination:none",
        )
    )
    if state.arrival_key == arrival_key:
        state.arrived_stable_for_ticks = max(1, state.arrived_stable_for_ticks + 1)
    else:
        state.arrival_key = arrival_key
        state.arrived_stable_for_ticks = 1
        state.service_ready_started_tick = None
        state.service_ready_stable_for_ticks = 0
        state.service_ready = False
    context.arrived_stable_for_ticks = state.arrived_stable_for_ticks

    if movement_state == "moving":
        context.arrival_reason = "arrival_tentative_player_moving"
        context.service_ready = False
        context.service_ready_reason = "waiting_for_player_to_stop"
        state.service_ready = False
        state.service_ready_stable_for_ticks = 0
        return

    if not service_ready_allowed:
        state.service_ready = False
        state.service_ready_last_tick = None
        state.service_ready_started_tick = None
        state.service_ready_stable_for_ticks = 0
        context.arrival_reason = base_reason if state.arrived_stable_for_ticks >= ARRIVAL_READY_STABLE_TICKS else "arrival_stabilizing"
        context.service_ready = False
        context.service_ready_reason = "arrival_not_service_target"
        context.service_ready_stable_for_ticks = 0
        return

    if state.arrived_stable_for_ticks < ARRIVAL_READY_STABLE_TICKS:
        context.arrival_reason = "arrival_stabilizing"
        context.service_ready = False
        context.service_ready_reason = "arrival_stabilizing"
        return

    state.service_ready = True
    state.service_ready_last_tick = tick
    if state.service_ready_started_tick is None:
        state.service_ready_started_tick = tick
    state.service_ready_stable_for_ticks = max(1, state.service_ready_stable_for_ticks + 1)
    context.arrival_reason = base_reason
    context.service_ready = True
    context.service_ready_reason = "arrived_at_service"
    context.service_ready_stable_for_ticks = state.service_ready_stable_for_ticks


def complete_path_after_arrival(context: PathingContext) -> None:
    context.path_completed = True
    context.path_completion_reason = "arrived_at_service"
    context.retained_path_after_arrival = True
    context.pathing_needed = False
    context.reason = "arrived_at_service"
    context.next_waypoint_tile = None
    context.path_length_tiles = 0


def service_ready_grace_available(state: PathIntentState, current_player_tile: dict[str, Any] | None, tick: int | None) -> bool:
    if not state.service_ready or not isinstance(state.destination_tile, dict):
        return False
    if tick is not None and state.service_ready_last_tick is not None:
        if int(tick) - int(state.service_ready_last_tick) > SERVICE_READY_GRACE_TICKS:
            return False
    distance = tile_distance(current_player_tile, state.destination_tile)
    return distance is not None and distance <= 1


def store_path_intent_state(
    state: PathIntentState,
    context: PathingContext,
    *,
    intent_key: str | None,
    destination_key: str | None,
    current_phase_key: str,
    tick: int | None,
    switch_reason: str | None,
) -> None:
    if not intent_key:
        state.clear(reason=switch_reason)
        return
    if state.active_path_intent_key == intent_key:
        state.stable_for_ticks = max(1, state.stable_for_ticks + 1)
    else:
        state.stable_for_ticks = 1
        state.path_started_tick = tick
    state.active_path_intent_key = intent_key
    state.active_phase_key = current_phase_key
    state.destination_target_key = destination_key
    state.destination_tile = deepcopy(context.destination_tile)
    state.final_approach_tile = deepcopy(context.final_approach_tile)
    state.next_waypoint_tile = deepcopy(context.next_waypoint_tile)
    state.predicted_path_tiles = deepcopy(context.predicted_path_tiles)
    state.retained_path_fields = snapshot_path_fields(context)
    state.last_updated_tick = tick
    state.retention_reason = None
    state.switch_reason = switch_reason
    state.pending_switch_key = None
    state.pending_switch_ticks = 0


def stabilize_path_intent(
    context: PathingContext,
    *,
    path_intent_state: PathIntentState | None,
    generic_task_state: Any,
    current_player_tile: dict[str, Any] | None,
    movement_state: str,
    tick: int | None,
    service_ready_allowed: bool = True,
) -> PathingContext:
    destination_key = destination_target_key(context.destination)
    current_phase_key = phase_key(generic_task_state)
    intent_key = build_path_intent_key(
        destination=context.destination,
        destination_tile=context.destination_tile,
        generic_task_state=generic_task_state,
    )
    if path_intent_state is None:
        update_arrival_context(
            context,
            state=None,
            current_player_tile=current_player_tile,
            movement_state=movement_state,
            intent_key=intent_key,
            tick=tick,
            service_ready_allowed=service_ready_allowed,
        )
        annotate_path_intent(
            context,
            state=None,
            intent_key=intent_key,
            destination_key=destination_key,
            movement_state=movement_state,
            retained=False,
            retention_reason=None,
            switch_reason=None,
            tick=tick,
        )
        return context

    def finish(retained: bool, retention_reason: str | None, switch_reason: str | None, key: str | None = None, dest_key: str | None = None) -> PathingContext:
        update_arrival_context(
            context,
            state=path_intent_state,
            current_player_tile=current_player_tile,
            movement_state=movement_state,
            intent_key=key if key is not None else intent_key,
            tick=tick,
            service_ready_allowed=service_ready_allowed,
        )
        if context.service_ready:
            complete_path_after_arrival(context)
        annotate_path_intent(
            context,
            state=path_intent_state,
            intent_key=key if key is not None else intent_key,
            destination_key=dest_key if dest_key is not None else destination_key,
            movement_state=movement_state,
            retained=retained,
            retention_reason=retention_reason,
            switch_reason=switch_reason,
            tick=tick,
        )
        update_player_movement_state(path_intent_state, current_player_tile, tick)
        return context

    if not context.pathing_needed or not context.destination or not intent_key:
        if (
            not context.destination
            and path_intent_state.active_path_intent_key
            and path_intent_state.retained_path_fields
            and service_ready_grace_available(path_intent_state, current_player_tile, tick)
        ):
            apply_retained_path_fields(context, path_intent_state)
            context.service_ready = True
            context.service_ready_reason = "remembered_service_target_nearby"
            context.arrival_reason = "remembered_service_target_nearby"
            context.arrived_near_destination = True
            context.distance_to_destination = tile_distance(current_player_tile, context.destination_tile)
            context.path_completed = True
            context.path_completion_reason = "arrived_at_service"
            context.retained_path_after_arrival = True
            context.reason = "arrived_at_service"
            return finish(
                True,
                "remembered_service_ready_grace",
                None,
                key=path_intent_state.active_path_intent_key,
                dest_key=path_intent_state.destination_target_key,
            )
        switch_reason = context.reason or "pathing_not_needed"
        path_intent_state.clear(reason=switch_reason)
        return finish(False, None, switch_reason)

    if context.local_reachability == "blocked":
        path_intent_state.clear(reason="path_blocked")
        return finish(False, None, "path_blocked")

    moving = movement_state in {"moving", "recently_moved"}
    active_key = path_intent_state.active_path_intent_key
    if active_key == intent_key:
        if moving and path_intent_state.retained_path_fields:
            path_intent_state.stable_for_ticks = max(1, path_intent_state.stable_for_ticks + 1)
            path_intent_state.last_updated_tick = tick
            path_intent_state.retention_reason = "player_moving_same_destination"
            path_intent_state.switch_reason = None
            apply_retained_path_fields(context, path_intent_state)
            return finish(True, "player_moving_same_destination", None, key=active_key, dest_key=path_intent_state.destination_target_key)
        store_path_intent_state(
            path_intent_state,
            context,
            intent_key=intent_key,
            destination_key=destination_key,
            current_phase_key=current_phase_key,
            tick=tick,
            switch_reason=None,
        )
        return finish(False, None, None)

    if active_key and path_intent_state.active_phase_key == current_phase_key and moving and path_intent_state.retained_path_fields:
        if path_intent_state.pending_switch_key == intent_key:
            path_intent_state.pending_switch_ticks += 1
        else:
            path_intent_state.pending_switch_key = intent_key
            path_intent_state.pending_switch_ticks = 1
        if path_intent_state.pending_switch_ticks < max(1, int(path_intent_state.switch_debounce_ticks)):
            path_intent_state.stable_for_ticks = max(1, path_intent_state.stable_for_ticks + 1)
            path_intent_state.last_updated_tick = tick
            path_intent_state.retention_reason = "candidate_switch_debounce"
            apply_retained_path_fields(context, path_intent_state)
            return finish(True, "candidate_switch_debounce", None, key=active_key, dest_key=path_intent_state.destination_target_key)
        store_path_intent_state(
            path_intent_state,
            context,
            intent_key=intent_key,
            destination_key=destination_key,
            current_phase_key=current_phase_key,
            tick=tick,
            switch_reason="destination_changed_after_debounce",
        )
        return finish(False, None, "destination_changed_after_debounce")

    switch_reason = "new_path_intent" if not active_key else "path_intent_key_changed"
    store_path_intent_state(
        path_intent_state,
        context,
        intent_key=intent_key,
        destination_key=destination_key,
        current_phase_key=current_phase_key,
        tick=tick,
        switch_reason=switch_reason,
    )
    return finish(False, None, switch_reason)


def annotate_path_intent(
    context: PathingContext,
    *,
    state: PathIntentState | None,
    intent_key: str | None,
    destination_key: str | None,
    movement_state: str,
    retained: bool,
    retention_reason: str | None,
    switch_reason: str | None,
    tick: int | None,
) -> None:
    context.path_intent_key = intent_key
    context.destination_target_key = destination_key
    context.path_intent_retained = retained
    context.movement_state = movement_state
    context.retention_reason = retention_reason
    context.switch_reason = switch_reason
    if state is not None:
        context.path_stable_for_ticks = state.stable_for_ticks or (1 if intent_key else 0)
        context.path_started_tick = state.path_started_tick
        context.path_last_updated_tick = state.last_updated_tick or tick


def base_context(
    *,
    started: float,
    source_tick: int | None,
    pathing_needed: bool,
    destination: dict[str, Any] | None,
    reason: str,
    status: str = "PASS",
    warnings: list[str] | None = None,
    missing_capabilities: list[str] | None = None,
    local_reachability: str = "unknown",
    pathing_budget_exceeded: bool = False,
    path_nodes_expanded: int = 0,
    collision_window_available: bool | None = None,
    collision_window_fresh: bool | None = None,
    collision_window_radius: int | None = None,
    collision_window_center_world: dict[str, Any] | None = None,
    collision_window_plane: int | None = None,
    collision_window_age_ticks: int | None = None,
    destination_inside_collision_window: bool | None = None,
    destination_plane_matches: bool | None = None,
    collision_window_missing_reason: str | None = None,
) -> PathingContext:
    elapsed = (time.perf_counter() - started) * 1000.0
    return PathingContext(
        status=status,
        warnings=list(warnings or []),
        missing_capabilities=capabilities.normalize_capability_names(missing_capabilities or []),
        source_tick=source_tick,
        timing_millis=elapsed,
        pathing_needed=pathing_needed,
        destination=destination,
        local_reachability=local_reachability,
        reason=reason,
        pathing_millis=elapsed,
        path_nodes_expanded=path_nodes_expanded,
        pathing_budget_exceeded=pathing_budget_exceeded,
        collision_window_available=collision_window_available,
        collision_window_fresh=collision_window_fresh,
        collision_window_radius=collision_window_radius,
        collision_window_center_world=collision_window_center_world,
        collision_window_plane=collision_window_plane,
        collision_window_age_ticks=collision_window_age_ticks,
        destination_inside_collision_window=destination_inside_collision_window,
        destination_plane_matches=destination_plane_matches,
        collision_window_missing_reason=collision_window_missing_reason,
    )


def reconstruct_path(parent: dict[tuple[int, int], tuple[int, int] | None], end: tuple[int, int]) -> list[tuple[int, int]]:
    path = [end]
    current = end
    while parent.get(current) is not None:
        current = parent[current]  # type: ignore[assignment]
        path.append(current)
    path.reverse()
    return path


def normalize_movement_model(value: str) -> str:
    return value if value in MOVEMENT_MODELS else "unknown"


def direction_order_for_model(model: str) -> tuple[tuple[int, int], ...]:
    if model in {"osrs_like_predicted", "diagonal_guarded"}:
        return OSRS_LIKE_DIRECTIONS
    return CARDINAL_DIRECTIONS


def is_tile_passable_for_movement(window: navigation_reachability.CollisionWindow, x: int, y: int) -> bool:
    return navigation_reachability.tile_walkable(window, x, y)


def can_move_cardinal(window: navigation_reachability.CollisionWindow, x: int, y: int, nx: int, ny: int) -> bool:
    dx = nx - x
    dy = ny - y
    if abs(dx) + abs(dy) != 1:
        return False
    return navigation_reachability.step_allowed(window, x, y, nx, ny)


def can_move_diagonal(window: navigation_reachability.CollisionWindow, x: int, y: int, nx: int, ny: int) -> bool:
    dx = nx - x
    dy = ny - y
    if abs(dx) != 1 or abs(dy) != 1:
        return False
    horizontal = (x + dx, y)
    vertical = (x, y + dy)
    return (
        is_tile_passable_for_movement(window, nx, ny)
        and can_move_cardinal(window, x, y, horizontal[0], horizontal[1])
        and can_move_cardinal(window, x, y, vertical[0], vertical[1])
        and can_move_cardinal(window, horizontal[0], horizontal[1], nx, ny)
        and can_move_cardinal(window, vertical[0], vertical[1], nx, ny)
    )


def cardinal_edge_clear(window: navigation_reachability.CollisionWindow, x: int, y: int, nx: int, ny: int) -> bool:
    dx = nx - x
    dy = ny - y
    for dir_x, dir_y, source_block, dest_block in navigation_reachability.DIRECTIONS:
        if dx == dir_x and dy == dir_y:
            return (
                navigation_reachability.contains(window, x, y)
                and navigation_reachability.contains(window, nx, ny)
                and (navigation_reachability.flag_at(window, x, y) & source_block) == 0
                and (navigation_reachability.flag_at(window, nx, ny) & dest_block) == 0
            )
    return False


def step_allowed_for_model(window: navigation_reachability.CollisionWindow, x: int, y: int, nx: int, ny: int, model: str) -> bool:
    dx = nx - x
    dy = ny - y
    if abs(dx) + abs(dy) == 1:
        return can_move_cardinal(window, x, y, nx, ny)
    if model in {"osrs_like_predicted", "diagonal_guarded"} and abs(dx) == 1 and abs(dy) == 1:
        return can_move_diagonal(window, x, y, nx, ny)
    return False


def path_segment_payload(start: tuple[int, int], end: tuple[int, int], reason: str) -> dict[str, Any]:
    return {
        "from": {"sceneX": start[0], "sceneY": start[1]},
        "to": {"sceneX": end[0], "sceneY": end[1]},
        "reason": reason,
    }


def validate_path_segments(
    window: navigation_reachability.CollisionWindow | None,
    scene_path: list[tuple[int, int]],
    movement_model: str,
    *,
    diagnostic_cap: int = 12,
) -> dict[str, Any]:
    invalid: list[dict[str, Any]] = []
    if window is None or len(scene_path) <= 1:
        return {"valid": True, "invalidCount": 0, "invalidSegments": [], "firstInvalidSegment": None}
    for start, end in zip(scene_path, scene_path[1:]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        reason: str | None = None
        if dx == 0 and dy == 0:
            reason = "zero_length_step"
        elif abs(dx) > 1 or abs(dy) > 1:
            reason = "non_adjacent_step"
        elif abs(dx) + abs(dy) == 1:
            if not can_move_cardinal(window, start[0], start[1], end[0], end[1]):
                reason = "blocked_cardinal_step"
        elif abs(dx) == 1 and abs(dy) == 1:
            if movement_model not in {"osrs_like_predicted", "diagonal_guarded"}:
                reason = "diagonal_not_allowed"
            elif not can_move_diagonal(window, start[0], start[1], end[0], end[1]):
                reason = "blocked_diagonal_step"
        else:
            reason = "illegal_step"
        if reason:
            invalid.append(path_segment_payload(start, end, reason))
    capped = invalid[: max(0, int(diagnostic_cap))]
    return {
        "valid": not invalid,
        "invalidCount": len(invalid),
        "invalidSegments": capped,
        "firstInvalidSegment": invalid[0] if invalid else None,
    }


def target_uses_approach_tile(destination: dict[str, Any] | None) -> bool:
    if not isinstance(destination, dict):
        return False
    target_type = str(destination.get("targetType") or destination.get("target_type") or "")
    class_id = str(destination.get("classId") or destination.get("class_id") or "")
    if target_type in {"sceneObject", "npc", "groundItem"}:
        return True
    return class_id in {
        "bank_service",
        "banker",
        "bank_booth",
        "bank_chest",
        "deposit_box",
        "deposit_chest",
        "tree",
        "rock",
        "npc",
        "ground_item",
    }


def target_prefers_direct_side_access(destination: dict[str, Any] | None) -> bool:
    if not isinstance(destination, dict):
        return False
    target_type = str(destination.get("targetType") or destination.get("target_type") or "")
    class_id = str(destination.get("classId") or destination.get("class_id") or "")
    service_type = str(destination.get("serviceCandidateType") or destination.get("serviceClassId") or "")
    name = str(destination.get("targetName") or destination.get("name") or "").lower()
    if target_type != "sceneObject":
        return False
    if service_type in {"bank_service", "bank_booth", "bank_chest", "deposit_box", "deposit_chest"}:
        return True
    if class_id in {"bank_service", "bank_booth", "bank_chest", "deposit_box", "deposit_chest"}:
        return True
    return any(token in name for token in ("bank booth", "bank chest", "deposit box", "deposit chest", "bank deposit box"))


def target_footprint_size(destination: dict[str, Any] | None) -> tuple[int, int]:
    if not isinstance(destination, dict):
        return 1, 1
    width = None
    height = None
    for key in ("sizeX", "footprintWidth", "objectSizeX", "width"):
        width = int_value(destination.get(key))
        if width is not None:
            break
    for key in ("sizeY", "footprintHeight", "objectSizeY", "height"):
        height = int_value(destination.get(key))
        if height is not None:
            break
    return max(1, min(4, width or 1)), max(1, min(4, height or 1))


def target_footprint_tiles(destination_scene: tuple[int, int], destination: dict[str, Any] | None) -> set[tuple[int, int]]:
    width, height = target_footprint_size(destination)
    base_x, base_y = destination_scene
    return {(base_x + dx, base_y + dy) for dx in range(width) for dy in range(height)}


def evaluate_approach_side_access(
    window: navigation_reachability.CollisionWindow,
    *,
    candidate_tile: tuple[int, int],
    footprint: set[tuple[int, int]],
) -> tuple[bool | None, bool | None, str]:
    diagonal_touch = False
    blocked_cardinal = False
    for target_tile in footprint:
        dx = target_tile[0] - candidate_tile[0]
        dy = target_tile[1] - candidate_tile[1]
        if abs(dx) + abs(dy) == 1:
            if cardinal_edge_clear(window, candidate_tile[0], candidate_tile[1], target_tile[0], target_tile[1]):
                return True, True, "direct_side_access"
            blocked_cardinal = True
        elif abs(dx) == 1 and abs(dy) == 1:
            diagonal_touch = True
    if blocked_cardinal:
        return False, False, "blocked_side_access"
    if diagonal_touch:
        return None, None, "diagonal_only_side_access_unknown"
    return None, None, "not_adjacent_to_target_footprint"


def approach_directions_for_model(model: str) -> tuple[tuple[int, int], ...]:
    if model in {"osrs_like_predicted", "diagonal_guarded"}:
        return OSRS_LIKE_DIRECTIONS
    return CARDINAL_DIRECTIONS


def approach_tile_candidates(
    window: navigation_reachability.CollisionWindow,
    *,
    destination_scene: tuple[int, int],
    destination: dict[str, Any] | None,
    movement_model: str,
) -> tuple[list[ApproachCandidate], dict[str, int]]:
    footprint = target_footprint_tiles(destination_scene, destination)
    rejected: Counter[str] = Counter()
    candidates: list[ApproachCandidate] = []
    seen: set[tuple[int, int]] = set()
    directions = approach_directions_for_model(movement_model)
    for footprint_tile in sorted(footprint):
        for direction_index, (dx, dy) in enumerate(directions):
            tile = (footprint_tile[0] + dx, footprint_tile[1] + dy)
            if tile in seen:
                continue
            seen.add(tile)
            if tile in footprint:
                rejected["inside_target_footprint"] += 1
                continue
            if not navigation_reachability.contains(window, tile[0], tile[1]):
                rejected["outside_collision_window"] += 1
                continue
            if not navigation_reachability.tile_walkable(window, tile[0], tile[1]):
                rejected["tile_blocked"] += 1
                continue
            tile_kind = "diagonal" if abs(dx) == 1 and abs(dy) == 1 else "cardinal"
            side_access, line_of_sight, side_reason = evaluate_approach_side_access(
                window,
                candidate_tile=tile,
                footprint=footprint,
            )
            candidates.append(
                ApproachCandidate(
                    tile=tile,
                    tile_kind=tile_kind,
                    direction_index=direction_index,
                    side_access_valid=side_access,
                    line_of_sight_to_target=line_of_sight,
                    side_access_reason=side_reason,
                )
            )
    return candidates, dict(rejected)


def bfs_to_goals(
    window: navigation_reachability.CollisionWindow,
    *,
    start: tuple[int, int],
    goals: set[tuple[int, int]],
    movement_model: str,
    max_nodes: int,
    budget_millis: float,
    started: float,
) -> tuple[str, list[tuple[int, int]], int, bool]:
    if not goals:
        return "blocked", [], 0, False
    if start in goals:
        return "reachable", [start], 1, False
    queue = deque([start])
    parent: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    expanded = 0
    directions = direction_order_for_model(movement_model)
    while queue:
        if expanded >= max_nodes or (time.perf_counter() - started) * 1000.0 > budget_millis:
            return "unknown", [], expanded, True
        x, y = queue.popleft()
        expanded += 1
        for dx, dy in directions:
            next_tile = (x + dx, y + dy)
            if next_tile in parent or not navigation_reachability.contains(window, next_tile[0], next_tile[1]):
                continue
            if not step_allowed_for_model(window, x, y, next_tile[0], next_tile[1], movement_model):
                continue
            parent[next_tile] = (x, y)
            if next_tile in goals:
                return "reachable", reconstruct_path(parent, next_tile), expanded, False
            queue.append(next_tile)
    return "blocked", [], expanded, False


def local_waypoint_fallback_path(
    window: navigation_reachability.CollisionWindow,
    *,
    start: tuple[int, int],
    destination: tuple[int, int],
    movement_model: str,
    max_steps: int = 4,
) -> list[tuple[int, int]]:
    if not navigation_reachability.contains(window, start[0], start[1]):
        return []
    if not navigation_reachability.tile_walkable(window, start[0], start[1]):
        return []
    path = [start]
    visited = {start}
    current = start
    for _step in range(max(1, int(max_steps))):
        current_score = (
            max(abs(destination[0] - current[0]), abs(destination[1] - current[1])),
            abs(destination[0] - current[0]) + abs(destination[1] - current[1]),
        )
        best_score: tuple[int, int, int, int, int] | None = None
        best_tile: tuple[int, int] | None = None
        for index, (dx, dy) in enumerate(direction_order_for_model(movement_model)):
            next_tile = (current[0] + dx, current[1] + dy)
            if next_tile in visited:
                continue
            if not navigation_reachability.contains(window, next_tile[0], next_tile[1]):
                continue
            if not step_allowed_for_model(window, current[0], current[1], next_tile[0], next_tile[1], movement_model):
                continue
            distance_score = (
                max(abs(destination[0] - next_tile[0]), abs(destination[1] - next_tile[1])),
                abs(destination[0] - next_tile[0]) + abs(destination[1] - next_tile[1]),
            )
            if distance_score >= current_score:
                continue
            diagonal_penalty = 1 if abs(dx) == 1 and abs(dy) == 1 else 0
            score = (*distance_score, diagonal_penalty, index, next_tile[0] * 256 + next_tile[1])
            if best_score is None or score < best_score:
                best_score = score
                best_tile = next_tile
        if best_tile is None:
            break
        path.append(best_tile)
        visited.add(best_tile)
        current = best_tile
        if current == destination:
            break
    return path if len(path) > 1 else []


def local_frontier_path_to_external_destination(
    window: navigation_reachability.CollisionWindow,
    *,
    start: tuple[int, int],
    destination: tuple[int, int],
    movement_model: str,
    max_steps: int = 12,
    max_nodes: int = 2048,
    budget_millis: float = 10.0,
    started: float | None = None,
) -> tuple[list[tuple[int, int]], int, int | None, int | None, int | None, bool]:
    if not navigation_reachability.contains(window, start[0], start[1]):
        return [], 0, None, None, None, False
    if not navigation_reachability.tile_walkable(window, start[0], start[1]):
        return [], 0, None, None, None, False
    start_cheb = max(abs(destination[0] - start[0]), abs(destination[1] - start[1]))
    start_manhattan = abs(destination[0] - start[0]) + abs(destination[1] - start[1])
    queue = deque([start])
    parent: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    depth: dict[tuple[int, int], int] = {start: 0}
    best_tile: tuple[int, int] | None = None
    best_score: tuple[int, int, int, int, int] | None = None
    expanded = 0
    budget_exceeded = False
    directions = direction_order_for_model(movement_model)
    start_time = started if started is not None else time.perf_counter()
    while queue:
        if expanded >= max(1, int(max_nodes)) or (time.perf_counter() - start_time) * 1000.0 > max(0.1, float(budget_millis)):
            budget_exceeded = True
            break
        current = queue.popleft()
        expanded += 1
        current_depth = depth[current]
        for dx, dy in directions:
            next_tile = (current[0] + dx, current[1] + dy)
            if next_tile in parent:
                continue
            if not navigation_reachability.contains(window, next_tile[0], next_tile[1]):
                continue
            if not step_allowed_for_model(window, current[0], current[1], next_tile[0], next_tile[1], movement_model):
                continue
            next_depth = current_depth + 1
            if next_depth > max(1, int(max_steps)):
                continue
            parent[next_tile] = current
            depth[next_tile] = next_depth
            tile_cheb = max(abs(destination[0] - next_tile[0]), abs(destination[1] - next_tile[1]))
            tile_manhattan = abs(destination[0] - next_tile[0]) + abs(destination[1] - next_tile[1])
            cheb_progress = start_cheb - tile_cheb
            manhattan_progress = start_manhattan - tile_manhattan
            if cheb_progress > 0 or manhattan_progress > 0:
                score = (
                    cheb_progress,
                    manhattan_progress,
                    next_depth,
                    -tile_cheb,
                    -(next_tile[0] * 256 + next_tile[1]),
                )
                if best_score is None or score > best_score:
                    best_score = score
                    best_tile = next_tile
            queue.append(next_tile)
    if best_tile is None:
        return [], expanded, start_cheb, None, None, budget_exceeded
    path = reconstruct_path(parent, best_tile)
    after_cheb = max(abs(destination[0] - best_tile[0]), abs(destination[1] - best_tile[1]))
    return path if len(path) > 1 else [], expanded, start_cheb, after_cheb, start_cheb - after_cheb, budget_exceeded


def local_boundary_path_for_unreachable_destination(
    window: navigation_reachability.CollisionWindow,
    *,
    start: tuple[int, int],
    destination: tuple[int, int],
    movement_model: str,
    max_nodes: int = 2048,
    budget_millis: float = 8.0,
    started: float | None = None,
) -> tuple[list[tuple[int, int]], int, int | None, int | None, int | None, bool]:
    if not navigation_reachability.contains(window, start[0], start[1]):
        return [], 0, None, None, None, False
    if not navigation_reachability.tile_walkable(window, start[0], start[1]):
        return [], 0, None, None, None, False
    start_cheb = max(abs(destination[0] - start[0]), abs(destination[1] - start[1]))
    start_manhattan = abs(destination[0] - start[0]) + abs(destination[1] - start[1])
    queue = deque([start])
    parent: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    depth: dict[tuple[int, int], int] = {start: 0}
    best_tile: tuple[int, int] | None = None
    best_score: tuple[int, int, int, int, int, int] | None = None
    expanded = 0
    budget_exceeded = False
    directions = direction_order_for_model(movement_model)
    start_time = started if started is not None else time.perf_counter()
    while queue:
        if expanded >= max(1, int(max_nodes)) or (time.perf_counter() - start_time) * 1000.0 > max(0.1, float(budget_millis)):
            budget_exceeded = True
            break
        current = queue.popleft()
        expanded += 1
        current_depth = depth[current]
        is_boundary = (
            current[0] in {window.min_scene_x, window.max_scene_x}
            or current[1] in {window.min_scene_y, window.max_scene_y}
        )
        if is_boundary and current_depth > 0:
            tile_cheb = max(abs(destination[0] - current[0]), abs(destination[1] - current[1]))
            tile_manhattan = abs(destination[0] - current[0]) + abs(destination[1] - current[1])
            cheb_progress = start_cheb - tile_cheb
            manhattan_progress = start_manhattan - tile_manhattan
            if cheb_progress > 0 or manhattan_progress > 0:
                score = (
                    cheb_progress,
                    manhattan_progress,
                    -tile_cheb,
                    -tile_manhattan,
                    -current_depth,
                    -(current[0] * 256 + current[1]),
                )
                if best_score is None or score > best_score:
                    best_score = score
                    best_tile = current
        for dx, dy in directions:
            next_tile = (current[0] + dx, current[1] + dy)
            if next_tile in parent:
                continue
            if not navigation_reachability.contains(window, next_tile[0], next_tile[1]):
                continue
            if not step_allowed_for_model(window, current[0], current[1], next_tile[0], next_tile[1], movement_model):
                continue
            parent[next_tile] = current
            depth[next_tile] = current_depth + 1
            queue.append(next_tile)
    if best_tile is None:
        return [], expanded, start_cheb, None, None, budget_exceeded
    path = reconstruct_path(parent, best_tile)
    after_cheb = max(abs(destination[0] - best_tile[0]), abs(destination[1] - best_tile[1]))
    return path if len(path) > 1 else [], expanded, start_cheb, after_cheb, start_cheb - after_cheb, budget_exceeded


def budget_exceeded_local_path_result(
    window: navigation_reachability.CollisionWindow,
    *,
    start: tuple[int, int],
    destination: tuple[int, int],
    movement_model: str,
    expanded: int,
    final_approach_candidate_count: int = 0,
    rejected_approach_tile_reasons: dict[str, int] | None = None,
) -> LocalPathResult:
    fallback_path, frontier_expanded, distance_before, distance_after, progress_score, _frontier_budget = local_frontier_path_to_external_destination(
        window,
        start=start,
        destination=destination,
        movement_model=movement_model,
        max_steps=12,
        max_nodes=max(64, min(2048, max(1, int(expanded or 0)))),
        budget_millis=8.0,
    )
    selected_reason = "budget_exceeded_local_frontier" if fallback_path else None
    if not fallback_path:
        fallback_path = local_waypoint_fallback_path(
            window,
            start=start,
            destination=destination,
            movement_model=movement_model,
        )
        selected_reason = "budget_exceeded_local_waypoint" if fallback_path else None
        distance_before = max(abs(destination[0] - start[0]), abs(destination[1] - start[1]))
        if fallback_path:
            last = fallback_path[-1]
            distance_after = max(abs(destination[0] - last[0]), abs(destination[1] - last[1]))
            progress_score = distance_before - distance_after
    path_target = fallback_path[-1] if len(fallback_path) > 1 else None
    return LocalPathResult(
        "unknown",
        "pathing_budget_exceeded",
        fallback_path,
        max(expanded, frontier_expanded, max(0, len(fallback_path) - 1)),
        True,
        False,
        final_approach_candidate_count=final_approach_candidate_count,
        rejected_approach_tile_reasons=rejected_approach_tile_reasons,
        path_target_scene_tile=path_target,
        path_target_tile_source="local_waypoint_fallback" if path_target is not None else None,
        selected_approach_reason=selected_reason if path_target is not None else None,
        frontier_distance_before=distance_before,
        frontier_distance_after_estimate=distance_after,
        progress_score=progress_score,
    )


def local_scene_path(
    window: navigation_reachability.CollisionWindow,
    *,
    start: tuple[int, int],
    destination: tuple[int, int],
    destination_payload: dict[str, Any] | None,
    movement_model: str,
    max_nodes: int,
    budget_millis: float,
    started: float,
    require_approach_tile: bool = False,
) -> LocalPathResult:
    if not navigation_reachability.contains(window, start[0], start[1]):
        return LocalPathResult("unknown", "player_outside_collision_window", [], 0, False, False)
    if not navigation_reachability.contains(window, destination[0], destination[1]):
        fallback_path, frontier_expanded, distance_before, distance_after, progress_score, frontier_budget = local_frontier_path_to_external_destination(
            window,
            start=start,
            destination=destination,
            movement_model=movement_model,
            max_steps=12,
            max_nodes=max_nodes,
            budget_millis=budget_millis,
            started=started,
        )
        selected_reason = "goal_directed_local_frontier" if fallback_path else None
        if not fallback_path:
            fallback_path = local_waypoint_fallback_path(
                window,
                start=start,
                destination=destination,
                movement_model=movement_model,
                max_steps=12,
            )
            selected_reason = "destination_outside_collision_window_frontier" if fallback_path else None
            distance_before = max(abs(destination[0] - start[0]), abs(destination[1] - start[1]))
            if fallback_path:
                last = fallback_path[-1]
                distance_after = max(abs(destination[0] - last[0]), abs(destination[1] - last[1]))
                progress_score = distance_before - distance_after
        path_target = fallback_path[-1] if len(fallback_path) > 1 else None
        return LocalPathResult(
            "unknown",
            "destination_outside_collision_window",
            fallback_path,
            max(frontier_expanded, max(0, len(fallback_path) - 1)),
            frontier_budget,
            False,
            path_target_scene_tile=path_target,
            path_target_tile_source="local_frontier_waypoint" if path_target is not None else None,
            selected_approach_reason=selected_reason if path_target is not None else None,
            frontier_distance_before=distance_before,
            frontier_distance_after_estimate=distance_after,
            progress_score=progress_score,
        )
    if not navigation_reachability.tile_walkable(window, start[0], start[1]):
        return LocalPathResult("blocked", "player_tile_blocked", [], 1, False, False)

    exact_expanded = 0
    if not require_approach_tile:
        exact_goals: set[tuple[int, int]] = set()
        if is_tile_passable_for_movement(window, destination[0], destination[1]):
            exact_goals.add(destination)
        exact_reachability, exact_path, exact_expanded, exact_budget = bfs_to_goals(
            window,
            start=start,
            goals=exact_goals,
            movement_model=movement_model,
            max_nodes=max_nodes,
            budget_millis=budget_millis,
            started=started,
        )
        if exact_budget:
            return budget_exceeded_local_path_result(
                window,
                start=start,
                destination=destination,
                movement_model=movement_model,
                expanded=exact_expanded,
            )
        if exact_reachability == "reachable":
            final_approach_scene_tile = exact_path[-2] if len(exact_path) >= 2 else exact_path[-1]
            return LocalPathResult(
                "reachable",
                "local_path_found",
                exact_path,
                exact_expanded,
                False,
                True,
                final_approach_scene_tile=final_approach_scene_tile,
                final_approach_tile_source="path_before_destination",
                path_target_scene_tile=destination,
                path_target_tile_source="exact_destination_tile",
            )

    candidates, rejected = approach_tile_candidates(
        window,
        destination_scene=destination,
        destination=destination_payload,
        movement_model=movement_model,
    )
    if not candidates:
        return LocalPathResult(
            "blocked",
            "destination_blocked",
            [],
            max(1, exact_expanded),
            False,
            False,
            final_approach_candidate_count=0,
            rejected_approach_tile_reasons=rejected,
        )

    best_score: tuple[int, int, int, int, int, int] | None = None
    best_path: list[tuple[int, int]] = []
    best_candidate: ApproachCandidate | None = None
    best_suspect_score: tuple[int, int, int, int, int, int] | None = None
    best_suspect_path: list[tuple[int, int]] = []
    best_suspect_candidate: ApproachCandidate | None = None
    total_expanded = exact_expanded
    rejected_counter: Counter[str] = Counter(rejected)
    prefer_direct_side_access = target_prefers_direct_side_access(destination_payload)
    for candidate in candidates:
        if total_expanded >= max_nodes:
            return budget_exceeded_local_path_result(
                window,
                start=start,
                destination=destination,
                movement_model=movement_model,
                expanded=total_expanded,
                final_approach_candidate_count=len(candidates),
                rejected_approach_tile_reasons=dict(rejected_counter),
            )
        remaining_nodes = max(1, max_nodes - total_expanded)
        reachability, path, expanded, budget = bfs_to_goals(
            window,
            start=start,
            goals={candidate.tile},
            movement_model=movement_model,
            max_nodes=remaining_nodes,
            budget_millis=budget_millis,
            started=started,
        )
        total_expanded += expanded
        if budget:
            return budget_exceeded_local_path_result(
                window,
                start=start,
                destination=destination,
                movement_model=movement_model,
                expanded=total_expanded,
                final_approach_candidate_count=len(candidates),
                rejected_approach_tile_reasons=dict(rejected_counter),
            )
        if reachability != "reachable":
            rejected_counter["unreachable"] += 1
            continue
        path_length = max(0, len(path) - 1)
        tile_kind_preference = 0 if candidate.tile_kind == "cardinal" else 1
        player_distance = abs(candidate.tile[0] - start[0]) + abs(candidate.tile[1] - start[1])
        side_preference = 0 if candidate.side_access_valid is True else 1
        if prefer_direct_side_access:
            score = (side_preference, path_length, tile_kind_preference, player_distance, candidate.direction_index, candidate.tile[0] * 256 + candidate.tile[1])
        else:
            score = (path_length, tile_kind_preference, side_preference, player_distance, candidate.direction_index, candidate.tile[0] * 256 + candidate.tile[1])
        if candidate.side_access_valid is False:
            rejected_counter["blocked_side_access"] += 1
            rejected_counter["no_line_of_sight"] += 1
            if best_suspect_score is None or score < best_suspect_score:
                best_suspect_score = score
                best_suspect_path = path
                best_suspect_candidate = candidate
            continue
        if best_score is None or score < best_score:
            best_score = score
            best_path = path
            best_candidate = candidate

    if best_candidate is None:
        if best_suspect_candidate is not None:
            return LocalPathResult(
                "unknown",
                "approach_side_access_blocked",
                best_suspect_path,
                total_expanded,
                False,
                False,
                final_approach_scene_tile=best_suspect_candidate.tile,
                final_approach_tile_source="local_collision_approach_candidate",
                final_approach_candidate_count=len(candidates),
                rejected_approach_tile_reasons=dict(rejected_counter),
                path_target_scene_tile=best_suspect_candidate.tile,
                path_target_tile_source="final_approach_tile",
                approach_candidates_tested=len(candidates),
                approach_candidates_rejected_by_blocked_side=rejected_counter.get("blocked_side_access", 0),
                approach_candidates_rejected_by_no_line_of_sight=rejected_counter.get("no_line_of_sight", 0),
                selected_approach_reason="suspect_blocked_side_access",
                approach_quality="suspect_outside_wall",
                side_access_valid=False,
                line_of_sight_to_target=False,
            )
        fallback_path, frontier_expanded, distance_before, distance_after, progress_score, frontier_budget = local_boundary_path_for_unreachable_destination(
            window,
            start=start,
            destination=destination,
            movement_model=movement_model,
            max_nodes=max_nodes,
            budget_millis=8.0,
        )
        path_target = fallback_path[-1] if len(fallback_path) > 1 else None
        if path_target is not None:
            return LocalPathResult(
                "unknown",
                "destination_inside_window_boundary_handoff",
                fallback_path,
                max(total_expanded, frontier_expanded, max(0, len(fallback_path) - 1)),
                frontier_budget,
                False,
                final_approach_candidate_count=len(candidates),
                rejected_approach_tile_reasons=dict(rejected_counter),
                path_target_scene_tile=path_target,
                path_target_tile_source="local_frontier_waypoint",
                selected_approach_reason="blocked_destination_boundary_frontier",
                frontier_distance_before=distance_before,
                frontier_distance_after_estimate=distance_after,
                progress_score=progress_score,
                approach_candidates_tested=len(candidates),
                approach_candidates_rejected_by_blocked_side=rejected_counter.get("blocked_side_access", 0),
                approach_candidates_rejected_by_no_line_of_sight=rejected_counter.get("no_line_of_sight", 0),
            )
        return LocalPathResult(
            "blocked",
            "no_local_path",
            [],
            total_expanded,
            False,
            False,
            final_approach_candidate_count=len(candidates),
            rejected_approach_tile_reasons=dict(rejected_counter),
            approach_candidates_tested=len(candidates),
            approach_candidates_rejected_by_blocked_side=rejected_counter.get("blocked_side_access", 0),
            approach_candidates_rejected_by_no_line_of_sight=rejected_counter.get("no_line_of_sight", 0),
        )
    if prefer_direct_side_access and best_candidate.side_access_valid is not True:
        return LocalPathResult(
            "unknown",
            "approach_side_access_blocked",
            best_path,
            total_expanded,
            False,
            False,
            final_approach_scene_tile=best_candidate.tile,
            final_approach_tile_source="local_collision_approach_candidate",
            final_approach_candidate_count=len(candidates),
            rejected_approach_tile_reasons=dict(rejected_counter),
            path_target_scene_tile=best_candidate.tile,
            path_target_tile_source="final_approach_tile",
            approach_candidates_tested=len(candidates),
            approach_candidates_rejected_by_blocked_side=rejected_counter.get("blocked_side_access", 0),
            approach_candidates_rejected_by_no_line_of_sight=rejected_counter.get("no_line_of_sight", 0),
            selected_approach_reason="suspect_side_access_unknown",
            approach_quality="suspect_outside_wall",
            side_access_valid=best_candidate.side_access_valid,
            line_of_sight_to_target=best_candidate.line_of_sight_to_target,
        )
    selected_reason = (
        "reachable_direct_side_access"
        if best_candidate.side_access_valid is True
        else "reachable_side_access_unknown"
        if best_candidate.side_access_valid is None
        else "reachable"
    )
    approach_quality = "direct_side_access" if best_candidate.side_access_valid is True else "side_access_unknown"
    return LocalPathResult(
        "reachable",
        "approach_tile_found",
        best_path,
        total_expanded,
        False,
        False,
        final_approach_scene_tile=best_candidate.tile,
        final_approach_tile_source="local_collision_approach_candidate",
        final_approach_candidate_count=len(candidates),
        rejected_approach_tile_reasons=dict(rejected_counter),
        path_target_scene_tile=best_candidate.tile,
        path_target_tile_source="final_approach_tile",
        approach_candidates_tested=len(candidates),
        approach_candidates_rejected_by_blocked_side=rejected_counter.get("blocked_side_access", 0),
        approach_candidates_rejected_by_no_line_of_sight=rejected_counter.get("no_line_of_sight", 0),
        selected_approach_reason=selected_reason,
        approach_quality=approach_quality,
        side_access_valid=best_candidate.side_access_valid,
        line_of_sight_to_target=best_candidate.line_of_sight_to_target,
    )


def path_step_counts(scene_path: list[tuple[int, int]]) -> tuple[int, int]:
    cardinal_steps = 0
    diagonal_steps = 0
    for previous, current in zip(scene_path, scene_path[1:]):
        dx = abs(current[0] - previous[0])
        dy = abs(current[1] - previous[1])
        if dx == 1 and dy == 1:
            diagonal_steps += 1
        elif dx + dy == 1:
            cardinal_steps += 1
    return cardinal_steps, diagonal_steps


def analyze_pathing_context(
    *,
    player_context: Any = None,
    navigation_context: Any = None,
    navigation_intent_context: Any = None,
    service_context: Any = None,
    process_inventory_context: Any = None,
    target_context: Any = None,
    activity_context: Any = None,
    generic_task_state: Any = None,
    path_intent_state: PathIntentState | None = None,
    source_tick: int | None = None,
    max_predicted_tiles: int = DEFAULT_MAX_PREDICTED_TILES,
    max_nodes: int = DEFAULT_MAX_NODES,
    budget_millis: float = DEFAULT_BUDGET_MILLIS,
    movement_model: str = "osrs_like_predicted",
) -> PathingContext:
    started = time.perf_counter()
    normalized_movement_model = normalize_movement_model(str(movement_model or "cardinal_only"))
    search_movement_model = normalized_movement_model if normalized_movement_model != "unknown" else "cardinal_only"
    tick = source_tick if source_tick is not None else source_tick_from(navigation_intent_context, navigation_context, service_context, process_inventory_context, target_context)
    navigation_needed = bool(context_value(navigation_intent_context, "navigation_needed", "navigationNeeded"))
    navigation_reason = str(context_value(navigation_intent_context, "navigation_reason", "navigationReason") or "")
    target_kind = str(context_value(navigation_intent_context, "target_kind", "targetKind") or "none")
    destination = destination_from_navigation_intent(navigation_intent_context)
    service_ready_allowed = destination_allows_service_ready(destination, target_kind)
    generic_task_state = generic_task_state if generic_task_state is not None else {}
    current_player_tile = player_tile(player_context)
    movement_state = infer_movement_state(
        state=path_intent_state,
        current_player_tile=current_player_tile,
        activity_context=activity_context,
        tick=tick,
    )

    def finalize(context: PathingContext) -> PathingContext:
        return stabilize_path_intent(
            context,
            path_intent_state=path_intent_state,
            generic_task_state=generic_task_state,
            current_player_tile=current_player_tile,
            movement_state=movement_state,
            tick=tick,
            service_ready_allowed=service_ready_allowed,
        )

    if target_kind == "process_inventory":
        return finalize(base_context(started=started, source_tick=tick, pathing_needed=False, destination=None, reason="not_needed_for_process_inventory"))
    if navigation_reason == "service_target_missing":
        return finalize(base_context(started=started, source_tick=tick, pathing_needed=False, destination=None, reason="service_target_missing", status="WARN", warnings=["service target missing; pathing waits for destination context"]))
    if not navigation_needed and target_kind in {"none", "process_inventory"}:
        return finalize(base_context(started=started, source_tick=tick, pathing_needed=False, destination=None, reason="not_needed_for_current_phase"))
    if not navigation_needed and navigation_reason == "target_reachable":
        return finalize(base_context(started=started, source_tick=tick, pathing_needed=False, destination=destination, reason="target_reachable", local_reachability="reachable"))
    if not destination:
        return finalize(base_context(
            started=started,
            source_tick=tick,
            pathing_needed=bool(navigation_needed),
            destination=None,
            reason="destination_missing",
            status="WARN" if navigation_needed else "PASS",
            warnings=["navigation intent did not provide a destination target"] if navigation_needed else [],
            missing_capabilities=["target.candidates"] if navigation_needed else [],
        ))

    player_world_x = player_field(player_context, "world_x", "worldX")
    player_world_y = player_field(player_context, "world_y", "worldY")
    player_plane = player_field(player_context, "plane", "plane")
    player_scene_x = player_field(player_context, "scene_x", "sceneX")
    player_scene_y = player_field(player_context, "scene_y", "sceneY")
    target_plane = int_value(destination.get("plane"))
    target_scene_x, target_scene_y = target_scene_from_world(
        destination,
        player_world_x=player_world_x,
        player_world_y=player_world_y,
        player_scene_x=player_scene_x,
        player_scene_y=player_scene_y,
    )
    destination_tile = tile_dict(int_value(destination.get("worldX")), int_value(destination.get("worldY")), target_plane)
    payload = collision_payload(navigation_context)
    window = navigation_reachability.parse_collision_window(payload)
    available_value = collision_window_is_available(navigation_context)
    window_available = bool(window) if available_value is None else bool(available_value and window is not None)
    window_fresh = collision_window_fresh(navigation_context)
    window_radius = collision_window_radius(navigation_context, window)
    window_center = collision_window_center_world(navigation_context)
    window_plane = collision_window_plane(navigation_context, window)
    window_age_ticks = collision_window_age_ticks(navigation_context)
    window_missing_reason = collision_window_missing_reason(navigation_context, payload, window)
    if target_plane is not None and player_plane is not None:
        destination_plane_matches = player_plane == target_plane
    elif target_plane is not None and window_plane is not None:
        destination_plane_matches = window_plane == target_plane
    else:
        destination_plane_matches = None

    if window is not None and target_scene_x is not None and target_scene_y is not None:
        destination_inside_window = navigation_reachability.contains(window, target_scene_x, target_scene_y)
    else:
        destination_inside_window = None

    if window is None or not window_available:
        reason = window_missing_reason or "collision_window_missing"
        warning = {
            "collision_window_payload_without_flags": "collision window payload has no flag grid; pathing preview is unknown",
            "collision_window_stale": "local collision window is stale; pathing preview is unknown",
        }.get(reason, "local collision window unavailable; pathing preview is unknown")
        context = base_context(
            started=started,
            source_tick=tick,
            pathing_needed=True,
            destination=destination,
            reason=reason,
            status="WARN",
            warnings=[warning],
            missing_capabilities=["navigation.local_collision_window"],
            collision_window_available=window_available,
            collision_window_fresh=window_fresh,
            collision_window_radius=window_radius,
            collision_window_center_world=window_center,
            collision_window_plane=window_plane,
            collision_window_age_ticks=window_age_ticks,
            destination_inside_collision_window=destination_inside_window,
            destination_plane_matches=destination_plane_matches,
            collision_window_missing_reason=reason,
        )
        context.destination_tile = destination_tile
        context.destination_tile_source = "target_world_tile" if destination_tile else None
        context.destination_world_x = int_value(destination.get("worldX"))
        context.destination_world_y = int_value(destination.get("worldY"))
        context.destination_plane = target_plane
        context.destination_scene_x = target_scene_x
        context.destination_scene_y = target_scene_y
        return finalize(context)
    if window_fresh is False:
        context = base_context(
            started=started,
            source_tick=tick,
            pathing_needed=True,
            destination=destination,
            reason="collision_window_stale",
            status="WARN",
            warnings=["local collision window is stale; pathing preview is unknown"],
            missing_capabilities=["navigation.local_collision_window"],
            collision_window_available=window_available,
            collision_window_fresh=window_fresh,
            collision_window_radius=window_radius,
            collision_window_center_world=window_center,
            collision_window_plane=window_plane,
            collision_window_age_ticks=window_age_ticks,
            destination_inside_collision_window=destination_inside_window,
            destination_plane_matches=destination_plane_matches,
            collision_window_missing_reason="collision_window_stale",
        )
        context.destination_tile = destination_tile
        context.destination_tile_source = "target_world_tile" if destination_tile else None
        context.destination_world_x = int_value(destination.get("worldX"))
        context.destination_world_y = int_value(destination.get("worldY"))
        context.destination_plane = target_plane
        context.destination_scene_x = target_scene_x
        context.destination_scene_y = target_scene_y
        return finalize(context)
    if player_scene_x is None:
        player_scene_x = window.player_scene_x
    if player_scene_y is None:
        player_scene_y = window.player_scene_y
    if player_plane is None:
        player_plane = window.plane
    if target_plane is not None and player_plane is not None:
        destination_plane_matches = player_plane == target_plane
    if window is not None and target_scene_x is not None and target_scene_y is not None:
        destination_inside_window = navigation_reachability.contains(window, target_scene_x, target_scene_y)
    if player_plane is not None and target_plane is not None and player_plane != target_plane:
        context = base_context(
            started=started,
            source_tick=tick,
            pathing_needed=True,
            destination=destination,
            reason="destination_plane_mismatch",
            status="WARN",
            warnings=["destination is on a different plane from the local collision window/player"],
            missing_capabilities=[],
            collision_window_available=window_available,
            collision_window_fresh=window_fresh,
            collision_window_radius=window_radius,
            collision_window_center_world=window_center,
            collision_window_plane=window_plane,
            collision_window_age_ticks=window_age_ticks,
            destination_inside_collision_window=destination_inside_window,
            destination_plane_matches=False,
            collision_window_missing_reason=window_missing_reason,
        )
        context.destination_tile = destination_tile
        context.destination_tile_source = "target_world_tile" if destination_tile else None
        context.destination_world_x = int_value(destination.get("worldX"))
        context.destination_world_y = int_value(destination.get("worldY"))
        context.destination_plane = target_plane
        context.destination_scene_x = target_scene_x
        context.destination_scene_y = target_scene_y
        return finalize(context)
    if None in (player_scene_x, player_scene_y, target_scene_x, target_scene_y, player_plane, target_plane):
        context = base_context(
            started=started,
            source_tick=tick,
            pathing_needed=True,
            destination=destination,
            reason="missing_pathing_tiles",
            status="WARN",
            warnings=["player or destination tile is incomplete for local path preview"],
            missing_capabilities=["navigation.local_collision_window"],
            collision_window_available=window_available,
            collision_window_fresh=window_fresh,
            collision_window_radius=window_radius,
            collision_window_center_world=window_center,
            collision_window_plane=window_plane,
            collision_window_age_ticks=window_age_ticks,
            destination_inside_collision_window=destination_inside_window,
            destination_plane_matches=destination_plane_matches,
            collision_window_missing_reason=window_missing_reason,
        )
        context.destination_tile = destination_tile
        context.destination_tile_source = "target_world_tile" if destination_tile else None
        context.destination_world_x = int_value(destination.get("worldX"))
        context.destination_world_y = int_value(destination.get("worldY"))
        context.destination_plane = target_plane
        context.destination_scene_x = target_scene_x
        context.destination_scene_y = target_scene_y
        return finalize(context)

    assert player_scene_x is not None and player_scene_y is not None and target_scene_x is not None and target_scene_y is not None
    assert player_plane is not None
    local_path = local_scene_path(
        window,
        start=(player_scene_x, player_scene_y),
        destination=(target_scene_x, target_scene_y),
        destination_payload=destination,
        movement_model=search_movement_model,
        max_nodes=max(1, int(max_nodes)),
        budget_millis=max(0.1, float(budget_millis)),
        started=started,
        require_approach_tile=target_uses_approach_tile(destination),
    )
    reachability = local_path.reachability
    reason = local_path.reason
    scene_path = local_path.scene_path
    segment_validation = validate_path_segments(window, scene_path, search_movement_model)
    if scene_path and not segment_validation["valid"] and reachability == "reachable":
        reachability = "unknown"
        reason = "invalid_path_segment"
    expanded = local_path.expanded
    budget_exceeded = local_path.budget_exceeded
    exact_destination_reached = local_path.exact_destination_reached
    output_reason = reason
    if reachability == "reachable":
        output_reason = "path_reachable"
    elif reason == "no_local_path":
        output_reason = "destination_inside_window_but_no_path"
    elif reason in {"destination_blocked", "player_tile_blocked"}:
        output_reason = "path_blocked"
    missing: list[str] = []
    warnings: list[str] = []
    if reason == "destination_outside_collision_window":
        missing.append("navigation.global_pathfinding")
        warnings.append("destination is outside the local collision window")
    elif reason == "player_outside_collision_window":
        missing.append("navigation.local_collision_window")
        warnings.append("player is outside the local collision window")
    elif reason == "pathing_budget_exceeded":
        warnings.append("pathing search budget exceeded")
    elif reason == "destination_inside_window_boundary_handoff":
        missing.append("navigation.global_pathfinding")
        warnings.append("local target is blocked inside the collision window; routing to a reachable boundary handoff tile")
    elif reason == "invalid_path_segment":
        warnings.append("predicted path contains a movement segment blocked by collision data")
    elif reason == "approach_side_access_blocked":
        warnings.append("approach tile is reachable, but target-side access appears blocked or outside-wall")
    elif reachability == "blocked":
        warnings.append("no reachable local path found inside the collision window")
    if reachability == "reachable":
        if target_uses_approach_tile(destination) and local_path.final_approach_tile_source != "local_collision_approach_candidate":
            missing.append("navigation.interaction_tile")
        missing.append("activity.explicit_movement_state")
    status = "PASS" if reachability == "reachable" else "WARN"
    path_cap_tiles = max(0, int(max_predicted_tiles))
    predicted_tiles = [
        scene_to_world(
            scene_x,
            scene_y,
            player_world_x=player_world_x,
            player_world_y=player_world_y,
            player_scene_x=player_scene_x,
            player_scene_y=player_scene_y,
            plane=player_plane,
        )
        for scene_x, scene_y in scene_path[1:]
    ]
    predicted_tiles = [tile for tile in predicted_tiles if tile is not None][:path_cap_tiles]
    full_predicted_tiles = [
        scene_to_world(
            scene_x,
            scene_y,
            player_world_x=player_world_x,
            player_world_y=player_world_y,
            player_scene_x=player_scene_x,
            player_scene_y=player_scene_y,
            plane=player_plane,
        )
        for scene_x, scene_y in scene_path[1:]
    ]
    full_predicted_tiles = [tile for tile in full_predicted_tiles if tile is not None]
    cardinal_steps, diagonal_steps = path_step_counts(scene_path)
    path_was_capped = len(full_predicted_tiles) > len(predicted_tiles)
    final_approach: dict[str, Any] | str | None = None
    path_target_tile: dict[str, Any] | None = None
    if scene_path:
        if local_path.final_approach_scene_tile is not None:
            final_approach = scene_to_world(
                local_path.final_approach_scene_tile[0],
                local_path.final_approach_scene_tile[1],
                player_world_x=player_world_x,
                player_world_y=player_world_y,
                player_scene_x=player_scene_x,
                player_scene_y=player_scene_y,
                plane=player_plane,
            )
        elif full_predicted_tiles:
            final_approach = full_predicted_tiles[-1]
        else:
            final_approach = "unknown"
        if local_path.path_target_scene_tile is not None:
            path_target_tile = scene_to_world(
                local_path.path_target_scene_tile[0],
                local_path.path_target_scene_tile[1],
                player_world_x=player_world_x,
                player_world_y=player_world_y,
                player_scene_x=player_scene_x,
                player_scene_y=player_scene_y,
                plane=player_plane,
            )
    final_approach_tile_used = bool(reachability == "reachable" and local_path.path_target_tile_source == "final_approach_tile")
    if final_approach_tile_used is False and local_path.path_target_tile_source == "final_approach_tile" and isinstance(final_approach, dict):
        final_approach_tile_used = True
    reported_exact_destination_reached = exact_destination_reached if reachability == "reachable" else False
    final_approach_substituted = bool(final_approach_tile_used and not reported_exact_destination_reached)
    route_mode = None
    goal_directed_fallback_active = False
    fallback_goal = None
    fallback_approach_node = None
    local_frontier_waypoint = path_target_tile if local_path.path_target_tile_source == "local_frontier_waypoint" else None
    if reason == "destination_outside_collision_window" and local_frontier_waypoint:
        service_route = context_value(service_context, "service_route_context", "serviceRouteContext")
        service_route = service_route if isinstance(service_route, dict) else {}
        route_mode = service_route.get("routeMode") or "local_frontier_to_service"
        goal_directed_fallback_active = route_mode == "goal_directed_fallback" or bool(service_route.get("goalDirectedFallback"))
        fallback_goal = service_route.get("selectedServiceAnchor") if isinstance(service_route.get("selectedServiceAnchor"), dict) else None
        fallback_approach_node = service_route.get("selectedApproachNode") if isinstance(service_route.get("selectedApproachNode"), dict) else None
    elapsed = (time.perf_counter() - started) * 1000.0
    return finalize(PathingContext(
        status=status,
        warnings=warnings,
        missing_capabilities=capabilities.normalize_capability_names(missing),
        source_tick=tick,
        timing_millis=elapsed,
        pathing_needed=True,
        destination=destination,
        destination_tile=destination_tile,
        destination_world_x=int_value(destination.get("worldX")),
        destination_world_y=int_value(destination.get("worldY")),
        destination_plane=target_plane,
        destination_scene_x=target_scene_x,
        destination_scene_y=target_scene_y,
        destination_tile_source="target_world_tile" if destination_tile else None,
        local_reachability=reachability,
        path_length_tiles=(len(scene_path) - 1) if scene_path else None,
        next_waypoint_tile=predicted_tiles[0] if predicted_tiles else None,
        final_approach_tile=final_approach,
        final_approach_tile_source=local_path.final_approach_tile_source,
        final_approach_candidate_count=local_path.final_approach_candidate_count,
        rejected_approach_tile_reasons=dict(local_path.rejected_approach_tile_reasons or {}),
        final_approach_tile_used=final_approach_tile_used,
        path_target_tile=path_target_tile,
        path_target_tile_source=local_path.path_target_tile_source,
        predicted_path_tiles=predicted_tiles,
        predicted_step_count=(len(scene_path) - 1) if scene_path else None,
        predicted_path_count=len(full_predicted_tiles),
        predicted_path_displayed_count=len(predicted_tiles),
        predicted_path_available_count=len(full_predicted_tiles),
        path_was_capped=path_was_capped,
        path_display_was_capped=path_was_capped,
        diagonal_step_count=diagonal_steps,
        cardinal_step_count=cardinal_steps,
        path_segments_valid=segment_validation["valid"],
        invalid_path_segment_count=segment_validation["invalidCount"],
        invalid_path_segments=segment_validation["invalidSegments"],
        first_invalid_path_segment=segment_validation["firstInvalidSegment"],
        predicted_run_segments=[],
        predicted_movement_model=normalized_movement_model,
        predicted_movement_notes=[PREDICTION_NOTE],
        prediction_confidence=0.75 if reachability == "reachable" else 0.25 if reachability == "unknown" else 0.55,
        path_cap_tiles=path_cap_tiles,
        exact_destination_reached=reported_exact_destination_reached,
        final_approach_substituted=final_approach_substituted,
        approach_candidates_tested=local_path.approach_candidates_tested or local_path.final_approach_candidate_count,
        approach_candidates_rejected_by_blocked_side=local_path.approach_candidates_rejected_by_blocked_side,
        approach_candidates_rejected_by_no_line_of_sight=local_path.approach_candidates_rejected_by_no_line_of_sight,
        selected_approach_reason=local_path.selected_approach_reason,
        approach_quality=local_path.approach_quality,
        side_access_valid=local_path.side_access_valid,
        line_of_sight_to_target=local_path.line_of_sight_to_target,
        skipped_run_tiles=[],
        run_behavior="unknown",
        reason=output_reason,
        pathing_millis=elapsed,
        path_nodes_expanded=expanded,
        pathing_budget_exceeded=budget_exceeded,
        collision_window_available=window_available,
        collision_window_fresh=window_fresh,
        collision_window_radius=window_radius,
        collision_window_center_world=window_center,
        collision_window_plane=window_plane,
        collision_window_age_ticks=window_age_ticks,
        destination_inside_collision_window=destination_inside_window,
        destination_plane_matches=destination_plane_matches,
        collision_window_missing_reason=window_missing_reason,
        route_mode=route_mode,
        goal_directed_fallback_active=goal_directed_fallback_active,
        fallback_goal=fallback_goal,
        fallback_approach_node=fallback_approach_node,
        local_frontier_waypoint=local_frontier_waypoint,
        frontier_distance_before=local_path.frontier_distance_before,
        frontier_distance_after_estimate=local_path.frontier_distance_after_estimate,
        progress_score=local_path.progress_score,
    ))

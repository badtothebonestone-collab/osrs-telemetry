from __future__ import annotations

import time
from collections import deque
from dataclasses import is_dataclass
from typing import Any

import capabilities
import navigation_reachability

from analyzers.live_state import PathingContext


DEFAULT_MAX_PREDICTED_TILES = 10
DEFAULT_MAX_NODES = 512
DEFAULT_BUDGET_MILLIS = 5.0
PREDICTION_NOTE = "Predicted local path; exact server movement may differ."

SAFE_TARGET_KEYS = (
    "targetKey",
    "objectKey",
    "candidateKey",
    "key",
    "markerType",
    "targetType",
    "classId",
    "serviceCandidateType",
    "name",
    "targetName",
    "id",
    "hash",
    "kind",
    "layer",
    "worldX",
    "worldY",
    "plane",
    "sceneX",
    "sceneY",
    "localX",
    "localY",
    "aimPoint",
    "bounds",
    "qualityScore",
    "qualityTier",
    "distanceTiles",
    "directReachability",
    "targetLiveState",
    "liveness",
    "navigation",
)


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


def sanitized_target(target: Any) -> dict[str, Any] | None:
    if not isinstance(target, dict) or not target:
        return None
    clean: dict[str, Any] = {}
    for key in SAFE_TARGET_KEYS:
        value = target.get(key)
        if value is None:
            continue
        if key == "navigation" and isinstance(value, dict):
            navigation = {
                "directReachability": value.get("directReachability"),
                "pathLengthTiles": value.get("pathLengthTiles"),
                "targetInCollisionWindow": value.get("targetInCollisionWindow"),
            }
            clean[key] = {item_key: item_value for item_key, item_value in navigation.items() if item_value is not None}
        else:
            clean[key] = value
    return clean


def source_tick_from(*contexts: Any) -> int | None:
    for context in contexts:
        value = context_value(context, "source_tick", "sourceTick")
        if isinstance(value, int):
            return value
    return None


def collision_payload(navigation_context: Any) -> dict[str, Any] | None:
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
    return sanitized_target(target)


def target_label(target: dict[str, Any] | None) -> str:
    if not isinstance(target, dict) or not target:
        return "none"
    return str(target.get("targetName") or target.get("name") or target.get("classId") or "target")


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
    )


def reconstruct_path(parent: dict[tuple[int, int], tuple[int, int] | None], end: tuple[int, int]) -> list[tuple[int, int]]:
    path = [end]
    current = end
    while parent.get(current) is not None:
        current = parent[current]  # type: ignore[assignment]
        path.append(current)
    path.reverse()
    return path


def local_scene_path(
    window: navigation_reachability.CollisionWindow,
    *,
    start: tuple[int, int],
    destination: tuple[int, int],
    max_nodes: int,
    budget_millis: float,
    started: float,
) -> tuple[str, str, list[tuple[int, int]], int, bool]:
    if not navigation_reachability.contains(window, start[0], start[1]):
        return "unknown", "player_outside_collision_window", [], 0, False
    if not navigation_reachability.contains(window, destination[0], destination[1]):
        return "unknown", "destination_outside_collision_window", [], 0, False
    if not navigation_reachability.tile_walkable(window, start[0], start[1]):
        return "blocked", "player_tile_blocked", [], 1, False

    goals: set[tuple[int, int]] = set()
    if navigation_reachability.tile_walkable(window, destination[0], destination[1]):
        goals.add(destination)
    else:
        for dx, dy, _source, _dest in navigation_reachability.DIRECTIONS:
            neighbor = (destination[0] + dx, destination[1] + dy)
            if navigation_reachability.tile_walkable(window, neighbor[0], neighbor[1]):
                goals.add(neighbor)
    if not goals:
        return "blocked", "destination_blocked", [], 1, False
    if start in goals:
        return "reachable", "already_at_destination", [start], 1, False

    queue = deque([start])
    parent: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    expanded = 0
    while queue:
        if expanded >= max_nodes or (time.perf_counter() - started) * 1000.0 > budget_millis:
            return "unknown", "pathing_budget_exceeded", [], expanded, True
        x, y = queue.popleft()
        expanded += 1
        for dx, dy, _source, _dest in navigation_reachability.DIRECTIONS:
            next_tile = (x + dx, y + dy)
            if next_tile in parent or not navigation_reachability.contains(window, next_tile[0], next_tile[1]):
                continue
            if not navigation_reachability.step_allowed(window, x, y, next_tile[0], next_tile[1]):
                continue
            parent[next_tile] = (x, y)
            if next_tile in goals:
                return "reachable", "local_path_found", reconstruct_path(parent, next_tile), expanded, False
            queue.append(next_tile)
    return "blocked", "no_local_path", [], expanded, False


def analyze_pathing_context(
    *,
    player_context: Any = None,
    navigation_context: Any = None,
    navigation_intent_context: Any = None,
    service_context: Any = None,
    process_inventory_context: Any = None,
    target_context: Any = None,
    source_tick: int | None = None,
    max_predicted_tiles: int = DEFAULT_MAX_PREDICTED_TILES,
    max_nodes: int = DEFAULT_MAX_NODES,
    budget_millis: float = DEFAULT_BUDGET_MILLIS,
    movement_model: str = "cardinal_only",
) -> PathingContext:
    started = time.perf_counter()
    tick = source_tick if source_tick is not None else source_tick_from(navigation_intent_context, navigation_context, service_context, process_inventory_context, target_context)
    navigation_needed = bool(context_value(navigation_intent_context, "navigation_needed", "navigationNeeded"))
    navigation_reason = str(context_value(navigation_intent_context, "navigation_reason", "navigationReason") or "")
    target_kind = str(context_value(navigation_intent_context, "target_kind", "targetKind") or "none")
    destination = destination_from_navigation_intent(navigation_intent_context)

    if target_kind == "process_inventory":
        return base_context(started=started, source_tick=tick, pathing_needed=False, destination=None, reason="not_needed_for_process_inventory")
    if navigation_reason == "service_target_missing":
        return base_context(started=started, source_tick=tick, pathing_needed=False, destination=None, reason="service_target_missing", status="WARN", warnings=["service target missing; pathing waits for destination context"])
    if not navigation_needed and target_kind in {"none", "process_inventory"}:
        return base_context(started=started, source_tick=tick, pathing_needed=False, destination=None, reason="not_needed_for_current_phase")
    if not navigation_needed and navigation_reason == "target_reachable":
        return base_context(started=started, source_tick=tick, pathing_needed=False, destination=destination, reason="target_reachable", local_reachability="reachable")
    if not destination:
        return base_context(
            started=started,
            source_tick=tick,
            pathing_needed=bool(navigation_needed),
            destination=None,
            reason="destination_missing",
            status="WARN" if navigation_needed else "PASS",
            warnings=["navigation intent did not provide a destination target"] if navigation_needed else [],
            missing_capabilities=["target.candidates"] if navigation_needed else [],
        )

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
    if window is None or collision_window_is_available(navigation_context) is False:
        context = base_context(
            started=started,
            source_tick=tick,
            pathing_needed=True,
            destination=destination,
            reason="collision_window_missing",
            status="WARN",
            warnings=["local collision window unavailable; pathing preview is unknown"],
            missing_capabilities=["navigation.local_collision_window"],
        )
        context.destination_tile = destination_tile
        context.destination_tile_source = "target_world_tile" if destination_tile else None
        context.destination_world_x = int_value(destination.get("worldX"))
        context.destination_world_y = int_value(destination.get("worldY"))
        context.destination_plane = target_plane
        context.destination_scene_x = target_scene_x
        context.destination_scene_y = target_scene_y
        return context
    if player_scene_x is None:
        player_scene_x = window.player_scene_x
    if player_scene_y is None:
        player_scene_y = window.player_scene_y
    if player_plane is None:
        player_plane = window.plane
    if None in (player_scene_x, player_scene_y, target_scene_x, target_scene_y, player_plane, target_plane) or player_plane != target_plane:
        context = base_context(
            started=started,
            source_tick=tick,
            pathing_needed=True,
            destination=destination,
            reason="missing_pathing_tiles",
            status="WARN",
            warnings=["player or destination tile is incomplete for local path preview"],
            missing_capabilities=["navigation.local_collision_window"],
        )
        context.destination_tile = destination_tile
        context.destination_tile_source = "target_world_tile" if destination_tile else None
        context.destination_world_x = int_value(destination.get("worldX"))
        context.destination_world_y = int_value(destination.get("worldY"))
        context.destination_plane = target_plane
        context.destination_scene_x = target_scene_x
        context.destination_scene_y = target_scene_y
        return context

    assert player_scene_x is not None and player_scene_y is not None and target_scene_x is not None and target_scene_y is not None
    assert player_plane is not None
    reachability, reason, scene_path, expanded, budget_exceeded = local_scene_path(
        window,
        start=(player_scene_x, player_scene_y),
        destination=(target_scene_x, target_scene_y),
        max_nodes=max(1, int(max_nodes)),
        budget_millis=max(0.1, float(budget_millis)),
        started=started,
    )
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
    elif reachability == "blocked":
        warnings.append("no reachable local path found inside the collision window")
    if reachability == "reachable":
        missing.append("navigation.interaction_tile")
        missing.append("activity.explicit_movement_state")
    status = "PASS" if reachability == "reachable" else "WARN"
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
    predicted_tiles = [tile for tile in predicted_tiles if tile is not None][: max(0, int(max_predicted_tiles))]
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
    final_approach = full_predicted_tiles[-2] if len(full_predicted_tiles) >= 2 else ("unknown" if reachability == "reachable" else None)
    elapsed = (time.perf_counter() - started) * 1000.0
    return PathingContext(
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
        predicted_path_tiles=predicted_tiles,
        predicted_step_count=(len(scene_path) - 1) if scene_path else None,
        predicted_run_segments=[],
        predicted_movement_model=movement_model if movement_model in {"cardinal_only", "diagonal_guarded"} else "unknown",
        predicted_movement_notes=[PREDICTION_NOTE],
        prediction_confidence=0.75 if reachability == "reachable" else 0.25 if reachability == "unknown" else 0.55,
        reason=reason,
        pathing_millis=elapsed,
        path_nodes_expanded=expanded,
        pathing_budget_exceeded=budget_exceeded,
    )

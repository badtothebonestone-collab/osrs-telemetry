from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass, is_dataclass
from typing import Any

import capabilities
import navigation_reachability

from analyzers.live_state import PathingContext


DEFAULT_MAX_PREDICTED_TILES = 24
DEFAULT_MAX_NODES = 512
DEFAULT_BUDGET_MILLIS = 5.0
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


def step_allowed_for_model(window: navigation_reachability.CollisionWindow, x: int, y: int, nx: int, ny: int, model: str) -> bool:
    dx = nx - x
    dy = ny - y
    if abs(dx) + abs(dy) == 1:
        return can_move_cardinal(window, x, y, nx, ny)
    if model in {"osrs_like_predicted", "diagonal_guarded"} and abs(dx) == 1 and abs(dy) == 1:
        return can_move_diagonal(window, x, y, nx, ny)
    return False


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
            candidates.append(ApproachCandidate(tile=tile, tile_kind=tile_kind, direction_index=direction_index))
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
        return LocalPathResult("unknown", "destination_outside_collision_window", [], 0, False, False)
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
            return LocalPathResult("unknown", "pathing_budget_exceeded", [], exact_expanded, True, False)
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

    best_score: tuple[int, int, int, int, int] | None = None
    best_path: list[tuple[int, int]] = []
    best_candidate: ApproachCandidate | None = None
    total_expanded = exact_expanded
    rejected_counter: Counter[str] = Counter(rejected)
    for candidate in candidates:
        if total_expanded >= max_nodes:
            return LocalPathResult(
                "unknown",
                "pathing_budget_exceeded",
                [],
                total_expanded,
                True,
                False,
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
            return LocalPathResult(
                "unknown",
                "pathing_budget_exceeded",
                [],
                total_expanded,
                True,
                False,
                final_approach_candidate_count=len(candidates),
                rejected_approach_tile_reasons=dict(rejected_counter),
            )
        if reachability != "reachable":
            rejected_counter["unreachable"] += 1
            continue
        path_length = max(0, len(path) - 1)
        tile_kind_preference = 0 if candidate.tile_kind == "cardinal" else 1
        player_distance = abs(candidate.tile[0] - start[0]) + abs(candidate.tile[1] - start[1])
        score = (path_length, tile_kind_preference, player_distance, candidate.direction_index, candidate.tile[0] * 256 + candidate.tile[1])
        if best_score is None or score < best_score:
            best_score = score
            best_path = path
            best_candidate = candidate

    if best_candidate is None:
        return LocalPathResult(
            "blocked",
            "no_local_path",
            [],
            total_expanded,
            False,
            False,
            final_approach_candidate_count=len(candidates),
            rejected_approach_tile_reasons=dict(rejected_counter),
        )
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
        return context
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
        return context
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
        return context
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
        return context

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
    if reachability == "reachable":
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
    final_approach_substituted = bool(reachability == "reachable" and final_approach_tile_used and not exact_destination_reached)
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
        path_was_capped=path_was_capped,
        diagonal_step_count=diagonal_steps,
        cardinal_step_count=cardinal_steps,
        predicted_run_segments=[],
        predicted_movement_model=normalized_movement_model,
        predicted_movement_notes=[PREDICTION_NOTE],
        prediction_confidence=0.75 if reachability == "reachable" else 0.25 if reachability == "unknown" else 0.55,
        path_cap_tiles=path_cap_tiles,
        exact_destination_reached=exact_destination_reached if reachability == "reachable" else False,
        final_approach_substituted=final_approach_substituted,
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
    )

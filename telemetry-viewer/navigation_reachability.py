from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any


BLOCK_MOVEMENT_NORTH = 2
BLOCK_MOVEMENT_EAST = 8
BLOCK_MOVEMENT_SOUTH = 32
BLOCK_MOVEMENT_WEST = 128
BLOCK_MOVEMENT_OBJECT = 256
BLOCK_MOVEMENT_FLOOR_DECORATION = 262144
BLOCK_MOVEMENT_FLOOR = 2097152
BLOCK_MOVEMENT_FULL = 2359552
FULL_TILE_BLOCK_MASK = (
    BLOCK_MOVEMENT_OBJECT
    | BLOCK_MOVEMENT_FLOOR_DECORATION
    | BLOCK_MOVEMENT_FLOOR
    | BLOCK_MOVEMENT_FULL
)

DIRECTIONS = (
    (0, -1, BLOCK_MOVEMENT_SOUTH, BLOCK_MOVEMENT_NORTH),
    (1, 0, BLOCK_MOVEMENT_EAST, BLOCK_MOVEMENT_WEST),
    (0, 1, BLOCK_MOVEMENT_NORTH, BLOCK_MOVEMENT_SOUTH),
    (-1, 0, BLOCK_MOVEMENT_WEST, BLOCK_MOVEMENT_EAST),
)


@dataclass
class CollisionWindow:
    plane: int | None
    player_scene_x: int | None
    player_scene_y: int | None
    min_scene_x: int
    max_scene_x: int
    min_scene_y: int
    max_scene_y: int
    width: int
    height: int
    flags: list[list[int]]
    hash: str | None = None
    radius: int | None = None


def as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_collision_window(value: dict | None) -> CollisionWindow | None:
    if not isinstance(value, dict):
        return None
    rows = value.get("flags")
    if not isinstance(rows, list) or not rows:
        return None
    min_x = as_int(value.get("minSceneX"))
    min_y = as_int(value.get("minSceneY"))
    width = as_int(value.get("width"))
    height = as_int(value.get("height"))
    if min_x is None or min_y is None:
        return None
    normalized_rows: list[list[int]] = []
    for row in rows:
        if not isinstance(row, list):
            return None
        normalized_rows.append([int(item) if isinstance(item, int) and not isinstance(item, bool) else 0 for item in row])
    height = height if height is not None else len(normalized_rows)
    width = width if width is not None else max((len(row) for row in normalized_rows), default=0)
    if width <= 0 or height <= 0:
        return None
    return CollisionWindow(
        plane=as_int(value.get("plane")),
        player_scene_x=as_int(value.get("playerSceneX")),
        player_scene_y=as_int(value.get("playerSceneY")),
        min_scene_x=min_x,
        max_scene_x=as_int(value.get("maxSceneX")) if as_int(value.get("maxSceneX")) is not None else min_x + width - 1,
        min_scene_y=min_y,
        max_scene_y=as_int(value.get("maxSceneY")) if as_int(value.get("maxSceneY")) is not None else min_y + height - 1,
        width=width,
        height=height,
        flags=normalized_rows,
        hash=value.get("collisionWindowHash") or value.get("windowHash"),
        radius=as_int(value.get("windowRadius")),
    )


def contains(window: CollisionWindow, scene_x: int | None, scene_y: int | None) -> bool:
    return (
        scene_x is not None
        and scene_y is not None
        and window.min_scene_x <= scene_x <= window.max_scene_x
        and window.min_scene_y <= scene_y <= window.max_scene_y
    )


def flag_at(window: CollisionWindow, scene_x: int, scene_y: int) -> int:
    row_index = scene_y - window.min_scene_y
    column_index = scene_x - window.min_scene_x
    if row_index < 0 or row_index >= len(window.flags):
        return 0
    row = window.flags[row_index]
    if column_index < 0 or column_index >= len(row):
        return 0
    return row[column_index]


def tile_walkable(window: CollisionWindow, scene_x: int, scene_y: int) -> bool:
    if not contains(window, scene_x, scene_y):
        return False
    return (flag_at(window, scene_x, scene_y) & FULL_TILE_BLOCK_MASK) == 0


def step_allowed(window: CollisionWindow, x: int, y: int, nx: int, ny: int) -> bool:
    if not tile_walkable(window, nx, ny):
        return False
    dx = nx - x
    dy = ny - y
    for dir_x, dir_y, source_block, dest_block in DIRECTIONS:
        if dx == dir_x and dy == dir_y:
            return (flag_at(window, x, y) & source_block) == 0 and (flag_at(window, nx, ny) & dest_block) == 0
    return False


def goal_tiles(window: CollisionWindow, target_x: int, target_y: int, *, interaction_radius: int = 1) -> set[tuple[int, int]]:
    goals: set[tuple[int, int]] = set()
    if tile_walkable(window, target_x, target_y):
        goals.add((target_x, target_y))
    radius = max(1, min(3, interaction_radius))
    for distance in range(1, radius + 1):
        for x in range(target_x - distance, target_x + distance + 1):
            for y in range(target_y - distance, target_y + distance + 1):
                if max(abs(x - target_x), abs(y - target_y)) != distance:
                    continue
                if tile_walkable(window, x, y):
                    goals.add((x, y))
    return goals


def reachability_for_target(
    window_payload: dict | None,
    *,
    player_scene_x: int | None,
    player_scene_y: int | None,
    player_plane: int | None,
    target_scene_x: int | None,
    target_scene_y: int | None,
    target_plane: int | None,
    interaction_radius: int = 1,
    max_checked_tiles: int = 4096,
) -> dict:
    window = parse_collision_window(window_payload)
    missing = []
    warnings = []
    evidence = []
    if window is None:
        return {
            "reachable": None,
            "directReachability": "unknown",
            "pathLengthTiles": None,
            "checkedTiles": 0,
            "reason": "collision window unavailable",
            "confidence": 0.0,
            "warnings": ["collision window unavailable"],
            "missingNavigationFields": ["collisionWindow"],
            "conservativeMode": True,
        }
    player_scene_x = player_scene_x if player_scene_x is not None else window.player_scene_x
    player_scene_y = player_scene_y if player_scene_y is not None else window.player_scene_y
    if player_scene_x is None or player_scene_y is None:
        missing.append("playerTile")
    if target_scene_x is None or target_scene_y is None:
        missing.append("targetTile")
    same_plane = player_plane is not None and target_plane is not None and player_plane == target_plane
    if not same_plane:
        missing.append("samePlane" if player_plane is not None and target_plane is not None else "plane")
    if missing:
        return {
            "reachable": None,
            "directReachability": "unknown",
            "pathLengthTiles": None,
            "checkedTiles": 0,
            "reason": "missing navigation fields",
            "confidence": 0.0,
            "warnings": warnings,
            "missingNavigationFields": sorted(set(missing)),
            "conservativeMode": True,
        }
    assert player_scene_x is not None and player_scene_y is not None and target_scene_x is not None and target_scene_y is not None
    if not contains(window, player_scene_x, player_scene_y):
        return {
            "reachable": None,
            "directReachability": "unknown",
            "pathLengthTiles": None,
            "checkedTiles": 0,
            "reason": "player outside collision window",
            "confidence": 0.0,
            "warnings": ["player outside collision window"],
            "missingNavigationFields": ["playerInCollisionWindow"],
            "conservativeMode": True,
        }
    if not contains(window, target_scene_x, target_scene_y):
        return {
            "reachable": None,
            "directReachability": "unknown",
            "pathLengthTiles": None,
            "checkedTiles": 0,
            "reason": "target outside collision window",
            "confidence": 0.1,
            "warnings": ["target outside collision window"],
            "missingNavigationFields": ["targetInCollisionWindow"],
            "conservativeMode": True,
        }
    interaction_radius = max(1, min(3, int(interaction_radius)))
    goals = goal_tiles(window, target_scene_x, target_scene_y, interaction_radius=interaction_radius)
    if (player_scene_x, player_scene_y) in goals:
        player_distance_to_target = max(abs(player_scene_x - target_scene_x), abs(player_scene_y - target_scene_y))
        evidence.append(
            "player is on target tile"
            if (player_scene_x, player_scene_y) == (target_scene_x, target_scene_y)
            else "player is already on a reachable adjacent interaction tile"
            if player_distance_to_target <= 1
            else "player is already on a reachable nearby interaction tile"
        )
        return {
            "reachable": True,
            "directReachability": "reachable",
            "pathLengthTiles": 0,
            "checkedTiles": 1,
            "reason": "player is already on or near a reachable target interaction tile",
            "confidence": 0.9 if player_distance_to_target <= 1 else 0.7,
            "warnings": warnings,
            "missingNavigationFields": [],
            "reachabilityEvidence": evidence,
            "conservativeMode": True,
        }
    if not tile_walkable(window, player_scene_x, player_scene_y):
        return {
            "reachable": False,
            "directReachability": "blocked",
            "pathLengthTiles": None,
            "checkedTiles": 1,
            "reason": "player tile is blocked in collision window",
            "confidence": 0.7,
            "warnings": warnings,
            "missingNavigationFields": [],
            "conservativeMode": True,
        }
    if not goals:
        return {
            "reachable": False,
            "directReachability": "blocked",
            "pathLengthTiles": None,
            "checkedTiles": 1,
            "reason": "target tile and nearby interaction tiles are blocked in collision window",
            "confidence": 0.7,
            "warnings": warnings,
            "missingNavigationFields": [],
            "conservativeMode": True,
        }

    queue = deque([(player_scene_x, player_scene_y, 0)])
    visited = {(player_scene_x, player_scene_y)}
    checked = 0
    while queue and checked < max_checked_tiles:
        x, y, distance = queue.popleft()
        checked += 1
        for dx, dy, _source_block, _dest_block in DIRECTIONS:
            nx = x + dx
            ny = y + dy
            if (nx, ny) in visited or not contains(window, nx, ny):
                continue
            if not step_allowed(window, x, y, nx, ny):
                continue
            next_distance = distance + 1
            if (nx, ny) in goals:
                goal_distance_to_target = max(abs(nx - target_scene_x), abs(ny - target_scene_y))
                if (nx, ny) == (target_scene_x, target_scene_y):
                    evidence.append("reachable target tile found")
                elif goal_distance_to_target <= 1:
                    evidence.append("reachable adjacent interaction tile found")
                else:
                    evidence.append("reachable nearby interaction tile found from expanded object interaction radius")
                evidence.append("4-direction BFS found a local path to target or adjacent tile")
                return {
                    "reachable": True,
                    "directReachability": "reachable",
                    "pathLengthTiles": next_distance,
                    "checkedTiles": checked,
                    "reason": "local collision window path found",
                    "confidence": 0.85 if goal_distance_to_target <= 1 else 0.68,
                    "warnings": warnings,
                    "missingNavigationFields": [],
                    "reachabilityEvidence": evidence,
                    "conservativeMode": True,
                }
            visited.add((nx, ny))
            queue.append((nx, ny, next_distance))

    if checked >= max_checked_tiles:
        return {
            "reachable": None,
            "directReachability": "unknown",
            "pathLengthTiles": None,
            "checkedTiles": checked,
            "reason": "reachability search budget exhausted",
            "confidence": 0.2,
            "warnings": ["reachability search budget exhausted"],
            "missingNavigationFields": ["largerSearchBudget"],
            "conservativeMode": True,
        }
    return {
        "reachable": False,
        "directReachability": "blocked",
        "pathLengthTiles": None,
        "checkedTiles": checked,
        "reason": "no 4-direction local path found inside collision window",
        "confidence": 0.75,
        "warnings": warnings,
        "missingNavigationFields": [],
        "reachabilityEvidence": ["4-direction BFS exhausted local collision window without finding a path"],
        "conservativeMode": True,
    }

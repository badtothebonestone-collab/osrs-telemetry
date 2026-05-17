from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


VIEWER_DIR = Path(__file__).resolve().parent
if str(VIEWER_DIR) not in sys.path:
    sys.path.insert(0, str(VIEWER_DIR))

from analyzers import pathing_analyzer
from analyzers.live_state import NavigationContext, NavigationIntentContext, PlayerContext


SCHEMA = "pathing_matrix_diagnostic.v1"
FULL_BLOCK = 256


def collision_window(width: int = 5, height: int = 5, *, blocked: set[tuple[int, int]] | None = None) -> dict[str, Any]:
    blocked = set(blocked or set())
    rows: list[list[int]] = []
    for y in range(height):
        row: list[int] = []
        for x in range(width):
            row.append(FULL_BLOCK if (x, y) in blocked else 0)
        rows.append(row)
    return {
        "collisionWindowAvailable": True,
        "collisionWindow": {
            "plane": 0,
            "playerSceneX": 1,
            "playerSceneY": 1,
            "minSceneX": 0,
            "minSceneY": 0,
            "width": width,
            "height": height,
            "flags": rows,
        },
    }


def player(*, scene_x: int = 1, scene_y: int = 1, world_x: int = 100, world_y: int = 100, plane: int = 0) -> PlayerContext:
    return PlayerContext(world_x=world_x, world_y=world_y, plane=plane, scene_x=scene_x, scene_y=scene_y)


def destination(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "targetType": "tile",
        "classId": "tile",
        "targetName": "Destination tile",
        "worldX": 102,
        "worldY": 100,
        "plane": 0,
        "sceneX": 3,
        "sceneY": 1,
        "distanceTiles": 2,
    }
    value.update(overrides)
    return value


def navigation_intent(target: dict[str, Any]) -> NavigationIntentContext:
    return NavigationIntentContext(
        navigation_needed=True,
        navigation_reason="service_target_available",
        target_kind="service",
        destination_target=target,
        source_tick=1,
    )


def context_result(
    *,
    player_context: PlayerContext | None = None,
    navigation_context: NavigationContext,
    target: dict[str, Any],
    max_predicted_tiles: int = pathing_analyzer.DEFAULT_MAX_PREDICTED_TILES,
    movement_model: str = "osrs_like_predicted",
) -> dict[str, Any]:
    context = pathing_analyzer.analyze_pathing_context(
        player_context=player_context or player(),
        navigation_context=navigation_context,
        navigation_intent_context=navigation_intent(target),
        movement_model=movement_model,
        max_predicted_tiles=max_predicted_tiles,
    )
    payload = context.to_dict()
    return {
        "movementModel": payload.get("predictedMovementModel"),
        "destinationTile": payload.get("destinationTile"),
        "finalApproachTile": payload.get("finalApproachTile"),
        "nextWaypointTile": payload.get("nextWaypointTile"),
        "localReachability": payload.get("localReachability"),
        "pathLengthTiles": payload.get("pathLengthTiles"),
        "predictedPathTiles": payload.get("predictedPathTiles") or [],
        "diagonalStepCount": payload.get("diagonalStepCount"),
        "cardinalStepCount": payload.get("cardinalStepCount"),
        "predictedPathCount": payload.get("predictedPathCount"),
        "predictedPathDisplayedCount": payload.get("predictedPathDisplayedCount"),
        "predictedPathCap": payload.get("pathCapTiles"),
        "pathWasCapped": payload.get("pathWasCapped"),
        "status": payload.get("status"),
        "reason": payload.get("reason"),
        "missingCapabilities": payload.get("missingCapabilities") or [],
        "warnings": payload.get("warnings") or [],
    }


def scenario_straight_cardinal_path() -> dict[str, Any]:
    return context_result(
        navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
        target=destination(),
    )


def scenario_diagonal_shortcut_available() -> dict[str, Any]:
    return context_result(
        navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
        target=destination(worldX=102, worldY=102, sceneX=3, sceneY=3),
    )


def scenario_diagonal_blocked_by_corner() -> dict[str, Any]:
    return context_result(
        navigation_context=NavigationContext(
            collision_window_available=True,
            raw=collision_window(blocked={(2, 1), (1, 2), (0, 1), (1, 0), (0, 0), (0, 2), (2, 0)}),
        ),
        target=destination(worldX=102, worldY=102, sceneX=3, sceneY=3),
    )


def scenario_object_destination_uses_final_approach() -> dict[str, Any]:
    return context_result(
        navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(blocked={(3, 1)})),
        target=destination(targetType="sceneObject", classId="bank_booth", targetName="Bank booth"),
    )


def scenario_destination_outside_collision_window() -> dict[str, Any]:
    return context_result(
        navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
        target=destination(worldX=119, worldY=119, sceneX=20, sceneY=20),
    )


def scenario_destination_plane_mismatch() -> dict[str, Any]:
    return context_result(
        navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
        target=destination(plane=2),
    )


def scenario_path_blocked() -> dict[str, Any]:
    return context_result(
        player_context=player(scene_x=0, scene_y=1),
        navigation_context=NavigationContext(
            collision_window_available=True,
            raw=collision_window(width=3, height=3, blocked={(1, 0), (1, 1), (1, 2)}),
        ),
        target=destination(worldX=102, worldY=100, sceneX=2, sceneY=1),
    )


def scenario_path_cap_applied() -> dict[str, Any]:
    return context_result(
        navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=20, height=3)),
        target=destination(worldX=118, worldY=100, sceneX=19, sceneY=1),
        max_predicted_tiles=4,
    )


SCENARIOS = {
    "straight_cardinal_path": scenario_straight_cardinal_path,
    "diagonal_shortcut_available": scenario_diagonal_shortcut_available,
    "diagonal_blocked_by_corner": scenario_diagonal_blocked_by_corner,
    "object_destination_uses_final_approach": scenario_object_destination_uses_final_approach,
    "destination_outside_collision_window": scenario_destination_outside_collision_window,
    "destination_plane_mismatch": scenario_destination_plane_mismatch,
    "path_blocked": scenario_path_blocked,
    "path_cap_applied": scenario_path_cap_applied,
}


def run_matrix() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name, scenario in SCENARIOS.items():
        result = scenario()
        result["scenario"] = name
        results.append(result)
    return results


def tile_label(tile: Any) -> str:
    if not isinstance(tile, dict) or not tile:
        return "none"
    return f"{tile.get('worldX')},{tile.get('worldY')},{tile.get('plane')}"


def path_label(path: Any) -> str:
    if not isinstance(path, list) or not path:
        return "none"
    return " -> ".join(tile_label(tile) for tile in path if isinstance(tile, dict))


def format_human(results: list[dict[str, Any]]) -> str:
    lines = ["PATHING QA MATRIX", ""]
    for result in results:
        lines.extend(
            [
                f"Scenario: {result.get('scenario')}",
                f"  Status: {result.get('status')}",
                f"  Reason: {result.get('reason')}",
                f"  Movement model: {result.get('movementModel')}",
                f"  Destination tile: {tile_label(result.get('destinationTile'))}",
                f"  Final approach tile: {tile_label(result.get('finalApproachTile'))}",
                f"  Next waypoint: {tile_label(result.get('nextWaypointTile'))}",
                f"  Local reachability: {result.get('localReachability')}",
                f"  Path length: {result.get('pathLengthTiles') if result.get('pathLengthTiles') is not None else 'unknown'}",
                f"  Diagonal steps: {result.get('diagonalStepCount')}",
                f"  Cardinal steps: {result.get('cardinalStepCount')}",
                f"  Path cap: {result.get('predictedPathCap')}",
                f"  Path was capped: {str(bool(result.get('pathWasCapped'))).lower()}",
                f"  Predicted path: {path_label(result.get('predictedPathTiles'))}",
            ]
        )
        missing = result.get("missingCapabilities") if isinstance(result.get("missingCapabilities"), list) else []
        if missing:
            lines.append(f"  Missing: {', '.join(str(item) for item in missing)}")
        warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
        if warnings:
            lines.append(f"  Warnings: {'; '.join(str(item) for item in warnings)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run synthetic read-only pathing prediction QA scenarios.")
    parser.add_argument("--json", action="store_true", help="Print one JSON object to stdout only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results = run_matrix()
    if args.json:
        print(json.dumps({"schema": SCHEMA, "scenarios": results}, indent=2, sort_keys=False))
    else:
        print(format_human(results), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

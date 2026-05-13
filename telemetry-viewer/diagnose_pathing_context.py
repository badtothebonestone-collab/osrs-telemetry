from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any


SCHEMA = "pathing_context_diagnostic.v1"


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload)
    return decoded if isinstance(decoded, dict) else {}


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


def build_from_daemon(status: dict[str, Any]) -> dict[str, Any]:
    brain = status.get("brain") if isinstance(status.get("brain"), dict) else {}
    pathing = brain.get("pathingContext") if isinstance(brain.get("pathingContext"), dict) else {}
    return {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "daemonReachable": True,
        "pathingContextPresent": bool(pathing),
        "pathingNeeded": pathing.get("pathingNeeded") if pathing else status.get("pathingNeeded"),
        "destinationTile": pathing.get("destinationTile") if pathing else status.get("pathingDestinationTile"),
        "nextWaypointTile": pathing.get("nextWaypointTile") if pathing else status.get("pathingNextWaypointTile"),
        "predictedPathTiles": pathing.get("predictedPathTiles", []) if pathing else [],
        "movementModel": pathing.get("predictedMovementModel") if pathing else None,
        "localReachability": pathing.get("localReachability") if pathing else status.get("pathingLocalReachability"),
        "pathLengthTiles": pathing.get("pathLengthTiles") if pathing else status.get("pathingPathLengthTiles"),
        "pathingMillis": pathing.get("pathingMillis") if pathing else status.get("pathingMillis"),
        "pathNodesExpanded": pathing.get("pathNodesExpanded") if pathing else status.get("pathNodesExpanded"),
        "pathingBudgetExceeded": pathing.get("pathingBudgetExceeded") if pathing else status.get("pathingBudgetExceeded"),
        "missingCapabilities": pathing.get("missingCapabilities", []) if pathing else [],
        "warnings": pathing.get("warnings", []) if pathing else ["daemon brain state did not expose pathing context"],
        "noActionEmitted": brain.get("noActionEmitted", True),
    }


def tile_label(tile: Any) -> str:
    if not isinstance(tile, dict) or not tile:
        return "none"
    return f"{tile.get('worldX')},{tile.get('worldY')},{tile.get('plane')}"


def format_human(payload: dict[str, Any]) -> str:
    tiles = payload.get("predictedPathTiles") if isinstance(payload.get("predictedPathTiles"), list) else []
    preview = " -> ".join(tile_label(tile) for tile in tiles[:10] if isinstance(tile, dict)) or "none"
    lines = [
        "PATHING CONTEXT DIAGNOSTIC",
        "",
        f"Source: {payload.get('source')}",
        f"Daemon reachable: {'yes' if payload.get('daemonReachable') else 'no'}",
        f"Pathing context present: {'yes' if payload.get('pathingContextPresent') else 'no'}",
        f"Pathing needed: {'yes' if payload.get('pathingNeeded') else 'no'}",
        f"Destination tile: {tile_label(payload.get('destinationTile'))}",
        f"Next waypoint: {tile_label(payload.get('nextWaypointTile'))}",
        f"Local reachability: {payload.get('localReachability') or 'unknown'}",
        f"Path length: {payload.get('pathLengthTiles') if payload.get('pathLengthTiles') is not None else 'unknown'}",
        f"Movement model: {payload.get('movementModel') or 'unknown'}",
        f"Predicted path: {preview}",
        f"Pathing millis: {payload.get('pathingMillis') if payload.get('pathingMillis') is not None else 'unknown'}",
        f"Nodes expanded: {payload.get('pathNodesExpanded') if payload.get('pathNodesExpanded') is not None else 'unknown'}",
        f"Budget exceeded: {str(payload.get('pathingBudgetExceeded')).lower()}",
    ]
    missing = payload.get("missingCapabilities") if isinstance(payload.get("missingCapabilities"), list) else []
    if missing:
        lines.append(f"Missing: {', '.join(str(item) for item in missing)}")
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(["", "Warnings:"])
    if warnings:
        for warning in warnings:
            lines.append(f"  {warning}")
    else:
        lines.append("  none")
    lines.extend(["", f"noActionEmitted: {str(payload.get('noActionEmitted')).lower()}"])
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only pathing context diagnostic. Prints to stdout only.")
    parser.add_argument("--from-daemon", action="store_true", help="Read current live daemon memory/status.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.from_daemon:
        payload = {
            "schema": SCHEMA,
            "source": "not_requested",
            "daemonReachable": False,
            "pathingContextPresent": False,
            "warnings": ["pass --from-daemon to read live daemon pathing context"],
            "noActionEmitted": True,
        }
    else:
        try:
            payload = build_from_daemon(fetch_json(daemon_status_url(args.daemon_url), timeout=args.timeout))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            payload = {
                "schema": SCHEMA,
                "source": "daemon-memory",
                "daemonReachable": False,
                "pathingContextPresent": False,
                "warnings": [f"daemon status unavailable: {type(error).__name__}: {error}"],
                "noActionEmitted": True,
            }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print(format_human(payload), end="")
    return 0 if payload.get("daemonReachable") or not args.from_daemon else 1


if __name__ == "__main__":
    raise SystemExit(main())

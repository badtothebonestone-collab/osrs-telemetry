from __future__ import annotations

import argparse
import json
import os
import sys

from .configuration import DEFAULT_RUNTIME_CONFIG, RuntimeConfig
from .observation import ObservationClient


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m osrs_bot",
        description="Read the RuneLite sensor or run through EngineApplication.",
    )
    subparsers = parser.add_subparsers(dest="command")
    observe = subparsers.add_parser("observe")
    observe.add_argument("--endpoint", default=DEFAULT_RUNTIME_CONFIG.endpoint)
    observe.add_argument(
        "--auth-token",
        default=os.environ.get("OSRS_TELEMETRY_SNAPSHOT_AUTH_TOKEN"),
    )
    observe.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_RUNTIME_CONFIG.request_timeout_seconds,
    )
    subparsers.add_parser(
        "task",
        add_help=False,
        help="compatibility alias for osrs_bot.application_cli run",
    )
    return parser


def _observation_summary(observation) -> dict[str, object]:
    location = observation.location
    return {
        "status": observation.status,
        "loadedScene": observation.loaded_scene,
        "scenePlayable": observation.scene_playable,
        "fresh": observation.fresh,
        "cacheWallClockFresh": observation.cache_wall_clock_fresh,
        "sourceCoherent": observation.source_coherent,
        "sourceCapturedAtUtc": observation.timestamp.isoformat(),
        "assembledAtUtc": (
            observation.assembled_at.isoformat()
            if observation.assembled_at is not None
            else None
        ),
        "sourceAgeSeconds": observation.age_seconds,
        "frameId": observation.frame_id,
        "geometryFrameId": observation.geometry_frame_id,
        "menuFresh": observation.menu_fresh,
        "menuSourceTick": observation.menu_source_tick,
        "gameState": observation.game_state,
        "clientFocused": observation.client_focused,
        "clientProcessId": observation.client_process_id,
        "tick": observation.tick,
        "sessionId": observation.session_id,
        "location": (
            None
            if location is None
            else {"x": location.x, "y": location.y, "plane": location.plane}
        ),
        "inventory": {
            "known": observation.inventory.known,
            "occupiedSlots": observation.inventory.occupied_slots,
            "freeSlots": observation.inventory.free_slots,
            "items": [
                {
                    "slot": item.slot,
                    "itemId": item.item_id,
                    "quantity": item.quantity,
                    "name": item.name,
                }
                for item in observation.inventory.items
            ],
        },
        "nearbyObjects": len(observation.nearby_objects),
        "menuEntries": len(observation.menus),
        "bankOpen": observation.widgets.bank_open,
        "warnings": list(observation.warnings),
        "missingCapabilities": list(observation.missing_capabilities),
    }


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "task":
        from .application_cli import main as application_main

        return application_main(["run", *arguments[1:]])

    parser = _parser()
    args = parser.parse_args(arguments)
    if args.command is None:
        args = parser.parse_args(["observe"])
    try:
        configuration = RuntimeConfig(
            endpoint=args.endpoint,
            auth_token=args.auth_token,
            request_timeout_seconds=args.timeout_seconds,
        ).validated_for_mode(execute=False)
    except (TypeError, ValueError) as error:
        parser.error(str(error))
    client = ObservationClient(
        configuration.endpoint,
        auth_token=configuration.auth_token,
        timeout_seconds=configuration.request_timeout_seconds,
    )
    try:
        observation = client.fetch()
    except Exception as error:
        print(
            json.dumps(
                {"status": "ERROR", "reason": f"{type(error).__name__}: {error}"},
                indent=2,
            )
        )
        return 2
    print(json.dumps(_observation_summary(observation), indent=2))
    return 0 if observation.loaded_scene else 2


if __name__ == "__main__":
    raise SystemExit(main())

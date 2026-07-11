from __future__ import annotations

import argparse
import json
import os
import sys

from .observation import ObservationClient
from .runtime import (
    DEFAULT_MAX_ACTIONS,
    DEFAULT_MAX_OBSERVATIONS,
    DEFAULT_MAX_RUNTIME_SECONDS,
    DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
    TaskRuntime,
    build_live_runtime,
)
from .task import WoodcutBankTask
from .verification import Verifier


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m osrs_bot",
        description="Read the RuneLite sensor or run the one supported Lumbridge task.",
    )
    subparsers = parser.add_subparsers(dest="command")
    for name in ("observe", "task"):
        child = subparsers.add_parser(name)
        child.add_argument("--endpoint", default="http://127.0.0.1:8893")
        child.add_argument("--auth-token", default=os.environ.get("OSRS_TELEMETRY_SNAPSHOT_AUTH_TOKEN"))
        child.add_argument("--timeout-seconds", type=float, default=3.0)
    task = subparsers.choices["task"]
    task.add_argument("--execute", action="store_true", help="send verified actions through Arduino HID")
    task.add_argument("--arduino-port", default=os.environ.get("OSRS_TELEMETRY_ARDUINO_PORT"))
    task.add_argument("--poll-seconds", type=float, default=0.25)
    task.add_argument("--max-observations", type=int, default=DEFAULT_MAX_OBSERVATIONS)
    task.add_argument("--max-actions", type=int, default=DEFAULT_MAX_ACTIONS)
    task.add_argument("--max-runtime-seconds", type=float, default=DEFAULT_MAX_RUNTIME_SECONDS)
    task.add_argument(
        "--verification-timeout-seconds",
        type=float,
        default=DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
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
            "ordinaryLogs": observation.inventory.log_count,
        },
        "nearbyObjects": len(observation.nearby_objects),
        "menuEntries": len(observation.menus),
        "bankOpen": observation.widgets.bank_open,
        "warnings": list(observation.warnings),
        "missingCapabilities": list(observation.missing_capabilities),
    }


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    command = args.command or "observe"
    if args.command is None:
        args = parser.parse_args(["observe"])
    client = ObservationClient(
        args.endpoint, auth_token=args.auth_token,
        timeout_seconds=args.timeout_seconds,
    )

    if command == "observe":
        try:
            observation = client.fetch()
        except Exception as error:
            print(json.dumps({"status": "ERROR", "reason": f"{type(error).__name__}: {error}"}, indent=2))
            return 2
        print(json.dumps(_observation_summary(observation), indent=2))
        return 0 if observation.loaded_scene else 2

    if args.execute and not args.arduino_port:
        parser.error("task --execute requires --arduino-port or OSRS_TELEMETRY_ARDUINO_PORT")
    task = WoodcutBankTask()
    if args.execute:
        print(
            "Live mode: focus the telemetry-owning RuneLite window within 15 seconds.",
            file=sys.stderr,
        )
        runtime = build_live_runtime(
            client, task, arduino_port=args.arduino_port,
            poll_seconds=args.poll_seconds,
            max_observations=args.max_observations,
            max_actions=args.max_actions,
            max_runtime_seconds=args.max_runtime_seconds,
            verification_timeout_seconds=args.verification_timeout_seconds,
        )
    else:
        runtime = TaskRuntime(
            client, task, Verifier(),
            poll_seconds=args.poll_seconds,
            max_observations=args.max_observations,
            max_actions=args.max_actions,
            max_runtime_seconds=args.max_runtime_seconds,
            verification_timeout_seconds=args.verification_timeout_seconds,
        )
    result = runtime.run(execute=args.execute)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.successful else 2


if __name__ == "__main__":
    raise SystemExit(main())

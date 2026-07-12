from __future__ import annotations

import argparse
import json
import os
import sys

from .configuration import DEFAULT_RUNTIME_CONFIG, RuntimeConfig
from .observation import ObservationClient
from .profile import DEFAULT_BINDING
from .runtime import TaskRuntime, build_live_runtime
from .task import WoodcutBankTask
from .verification import Verifier


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m osrs_bot",
        description="Read the RuneLite sensor or run the selected OSRS task binding.",
    )
    subparsers = parser.add_subparsers(dest="command")
    for name in ("observe", "task"):
        child = subparsers.add_parser(name)
        child.add_argument("--endpoint", default=DEFAULT_RUNTIME_CONFIG.endpoint)
        child.add_argument("--auth-token", default=os.environ.get("OSRS_TELEMETRY_SNAPSHOT_AUTH_TOKEN"))
        child.add_argument(
            "--timeout-seconds",
            type=float,
            default=DEFAULT_RUNTIME_CONFIG.request_timeout_seconds,
        )
    task = subparsers.choices["task"]
    task.add_argument("--execute", action="store_true", help="send verified actions through Arduino HID")
    task.add_argument(
        "--overlay",
        action="store_true",
        help="show the passive read-only EngineFrame diagnostic overlay",
    )
    task.add_argument(
        "--overlay-show-rejected",
        action="store_true",
        help="also outline rejected candidates (requires --overlay)",
    )
    task.add_argument("--arduino-port", default=os.environ.get("OSRS_TELEMETRY_ARDUINO_PORT"))
    task.add_argument(
        "--poll-seconds", type=float, default=DEFAULT_RUNTIME_CONFIG.poll_seconds
    )
    task.add_argument(
        "--max-observations",
        type=int,
        default=DEFAULT_RUNTIME_CONFIG.max_observations,
    )
    task.add_argument(
        "--max-actions", type=int, default=DEFAULT_RUNTIME_CONFIG.max_actions
    )
    task.add_argument(
        "--max-runtime-seconds",
        type=float,
        default=DEFAULT_RUNTIME_CONFIG.max_runtime_seconds,
    )
    task.add_argument(
        "--verification-timeout-seconds",
        type=float,
        default=DEFAULT_RUNTIME_CONFIG.verification_timeout_seconds,
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
    parser = _parser()
    args = parser.parse_args(argv)
    command = args.command or "observe"
    if args.command is None:
        args = parser.parse_args(["observe"])
    config_values = {
        "endpoint": args.endpoint,
        "auth_token": args.auth_token,
        "request_timeout_seconds": args.timeout_seconds,
    }
    if command == "task":
        config_values.update(
            arduino_port=args.arduino_port,
            poll_seconds=args.poll_seconds,
            max_observations=args.max_observations,
            max_actions=args.max_actions,
            max_runtime_seconds=args.max_runtime_seconds,
            verification_timeout_seconds=args.verification_timeout_seconds,
        )
    try:
        configuration = RuntimeConfig(**config_values)
        configuration.validated_for_mode(
            execute=bool(command == "task" and args.execute)
        )
    except (TypeError, ValueError) as error:
        parser.error(str(error))
    if command == "task" and args.overlay_show_rejected and not args.overlay:
        parser.error("--overlay-show-rejected requires --overlay")
    client = ObservationClient(
        configuration.endpoint,
        auth_token=configuration.auth_token,
        timeout_seconds=configuration.request_timeout_seconds,
    )

    if command == "observe":
        try:
            observation = client.fetch()
        except Exception as error:
            print(json.dumps({"status": "ERROR", "reason": f"{type(error).__name__}: {error}"}, indent=2))
            return 2
        print(json.dumps(_observation_summary(observation), indent=2))
        return 0 if observation.loaded_scene else 2

    task = WoodcutBankTask(DEFAULT_BINDING)
    if args.execute:
        print(
            "Live mode: focus the telemetry-owning RuneLite window within 15 seconds.",
            file=sys.stderr,
        )
        runtime = build_live_runtime(
            client, task, configuration=configuration
        )
    else:
        runtime = TaskRuntime(
            client, task, Verifier(),
            configuration=configuration,
        )
    overlay = None
    if args.overlay:
        try:
            from .debug_overlay import DebugOverlay

            overlay = DebugOverlay(
                runtime.frame_publisher,
                show_rejected=args.overlay_show_rejected,
            )
            overlay.start()
        except Exception as error:  # diagnostics never alter engine control
            print(
                f"Diagnostic overlay unavailable: {type(error).__name__}: {error}",
                file=sys.stderr,
            )
            overlay = None
    try:
        result = runtime.run(execute=args.execute)
    finally:
        if overlay is not None:
            try:
                overlay.stop()
            except Exception as error:
                print(
                    f"Diagnostic overlay cleanup warning: {type(error).__name__}: {error}",
                    file=sys.stderr,
                )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.successful else 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace

from .application import EngineApplication, LifecycleState, SUPPORTED_TASK_ID
from .configuration import DEFAULT_RUNTIME_CONFIG, RuntimeConfig
from .definition import TaskSiteDefinition
from .profile import DEFAULT_PROFILE
from .task_authoring import load_task_definition


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m osrs_bot.application_cli",
        description="Inspect or run the thin engine application facade.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("catalog")
    schema = commands.add_parser("profile-schema")
    schema.add_argument("--task-id", default=SUPPORTED_TASK_ID)
    schema.add_argument("--definition-id")
    schema.add_argument("--definition-file")
    validate = commands.add_parser("validate-profile")
    run = commands.add_parser("run")
    for child in (validate, run):
        child.add_argument("--profile-id", default=DEFAULT_PROFILE.profile_id)
        child.add_argument("--definition-id")
        child.add_argument("--definition-file")
        child.add_argument("--cycle-goal", type=int, default=DEFAULT_PROFILE.cycle_goal)
        child.add_argument(
            "--no-cycle-goal",
            action="store_true",
            help="disable the cycle stop condition; another stop goal is required",
        )
        child.add_argument("--item-quantity-goal", type=int)
        child.add_argument("--inventories-banked-goal", type=int)
        child.add_argument("--duration-seconds", type=float)
        child.add_argument("--start-at-utc")
        child.add_argument("--stop-at-utc")
        child.add_argument("--stop-when-inventory-full", action="store_true")
        child.add_argument("--profile-max-actions", type=int)
        child.add_argument("--no-reconcile-on-start", action="store_true")
    run.add_argument("--execute", action="store_true")
    run.add_argument(
        "--overlay",
        action="store_true",
        help="show the passive read-only EngineFrame diagnostic overlay",
    )
    run.add_argument(
        "--overlay-show-rejected",
        action="store_true",
        help="also outline rejected candidates (requires --overlay)",
    )
    run.add_argument("--endpoint", default=DEFAULT_RUNTIME_CONFIG.endpoint)
    run.add_argument(
        "--auth-token",
        default=os.environ.get("OSRS_TELEMETRY_SNAPSHOT_AUTH_TOKEN"),
    )
    run.add_argument("--timeout-seconds", type=float, default=3.0)
    run.add_argument("--arduino-port", default=os.environ.get("OSRS_TELEMETRY_ARDUINO_PORT"))
    run.add_argument("--poll-seconds", type=float, default=DEFAULT_RUNTIME_CONFIG.poll_seconds)
    run.add_argument("--max-observations", type=int, default=DEFAULT_RUNTIME_CONFIG.max_observations)
    run.add_argument("--max-actions", type=int, default=DEFAULT_RUNTIME_CONFIG.max_actions)
    run.add_argument("--max-runtime-seconds", type=float, default=DEFAULT_RUNTIME_CONFIG.max_runtime_seconds)
    run.add_argument(
        "--verification-timeout-seconds",
        type=float,
        default=DEFAULT_RUNTIME_CONFIG.verification_timeout_seconds,
    )
    run.add_argument(
        "--behavior-seed",
        type=int,
        default=DEFAULT_RUNTIME_CONFIG.behavior.seed,
        help="reproduce bounded route, aim, pointer, camera, and timing decisions",
    )
    return parser


def _load_explicit_definition(args: argparse.Namespace) -> TaskSiteDefinition | None:
    path = getattr(args, "definition_file", None)
    return None if path is None else load_task_definition(path)


def _resolved_definition_id(
    args: argparse.Namespace,
    definition: TaskSiteDefinition | None,
) -> str:
    requested = getattr(args, "definition_id", None)
    if definition is None:
        return DEFAULT_PROFILE.definition_id if requested is None else requested
    if requested is not None and requested != definition.definition_id:
        raise ValueError(
            "--definition-id does not match --definition-file: "
            f"{requested!r} != {definition.definition_id!r}"
        )
    return definition.definition_id


def _profile_values(
    args: argparse.Namespace,
    definition: TaskSiteDefinition | None = None,
) -> dict[str, object]:
    return {
        "profileId": args.profile_id,
        "definitionId": _resolved_definition_id(args, definition),
        "cycleGoal": None if args.no_cycle_goal else args.cycle_goal,
        "itemQuantityGoal": args.item_quantity_goal,
        "inventoriesBankedGoal": args.inventories_banked_goal,
        "durationSeconds": args.duration_seconds,
        "startAtUtc": args.start_at_utc,
        "stopAtUtc": args.stop_at_utc,
        "stopWhenInventoryFull": bool(args.stop_when_inventory_full),
        "maxActions": args.profile_max_actions,
        "reconcileOnStart": not args.no_reconcile_on_start,
    }


def _report_overlay_status(application: EngineApplication) -> None:
    try:
        overlay = application.overlay_snapshot()
    except Exception as error:  # diagnostics never alter engine control
        print(
            f"Diagnostic overlay status warning: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return
    if overlay.error:
        print(f"Diagnostic overlay unavailable: {overlay.error}", file=sys.stderr)


def _run_application(
    args: argparse.Namespace,
    application: EngineApplication,
    definition: TaskSiteDefinition | None = None,
) -> int:
    overlay_requested = bool(args.overlay)
    try:
        if overlay_requested:
            application.set_overlay_enabled(
                True,
                show_rejected=bool(args.overlay_show_rejected),
            )
        if args.execute:
            print(
                "Live mode: focus the telemetry-owning RuneLite window within 15 seconds.",
                file=sys.stderr,
            )
        try:
            started = application.start(
                profile_values=_profile_values(args, definition),
                definition=definition,
                execute=args.execute,
            )
            if overlay_requested:
                _report_overlay_status(application)
            run_id = started.run_id
            assert run_id is not None
            finished = application.wait(run_id)
        except KeyboardInterrupt:
            interrupted = application.snapshot()
            active_run_id = interrupted.active_run_id
            if active_run_id is None:
                if interrupted.run_id is None:
                    print(
                        json.dumps(
                            {
                                "status": "INTERRUPTED",
                                "reason": "run was interrupted before a worker started",
                            },
                            indent=2,
                        ),
                        file=sys.stderr,
                    )
                    return 130
                finished = interrupted
            else:
                application.request_safe_stop(active_run_id)
                finished = application.wait(active_run_id)
        print(json.dumps(finished.to_dict(), indent=2, sort_keys=True))
        return (
            0
            if finished.lifecycle in {LifecycleState.COMPLETE, LifecycleState.STOPPED}
            else 2
        )
    finally:
        if overlay_requested:
            try:
                cleanup = application.set_overlay_enabled(False)
            except Exception as error:  # diagnostics never alter engine control
                print(
                    "Diagnostic overlay cleanup warning: "
                    f"{type(error).__name__}: {error}",
                    file=sys.stderr,
                )
            else:
                if cleanup.error:
                    print(
                        f"Diagnostic overlay cleanup warning: {cleanup.error}",
                        file=sys.stderr,
                    )


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "run" and args.overlay_show_rejected and not args.overlay:
        parser.error("--overlay-show-rejected requires --overlay")
    try:
        if args.command == "catalog":
            print(json.dumps(EngineApplication.catalog(), indent=2, sort_keys=True))
            return 0
        definition = _load_explicit_definition(args)
        if args.command == "profile-schema":
            payload = EngineApplication.profile_contract(
                args.task_id,
                _resolved_definition_id(args, definition),
                definition=definition,
            )
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        if args.command == "validate-profile":
            binding = EngineApplication.validate_profile(
                _profile_values(args, definition),
                definition,
            )
            print(
                json.dumps(
                    {
                        "status": "VALID",
                        "profileId": binding.profile.profile_id,
                        "definitionId": binding.definition.definition_id,
                        "cycleGoal": binding.profile.cycle_goal,
                        "itemQuantityGoal": binding.profile.item_quantity_goal,
                        "inventoriesBankedGoal": (
                            binding.profile.inventories_banked_goal
                        ),
                        "durationSeconds": binding.profile.duration_seconds,
                        "startAtUtc": (
                            binding.profile.start_at_utc.isoformat()
                            if binding.profile.start_at_utc is not None
                            else None
                        ),
                        "stopAtUtc": (
                            binding.profile.stop_at_utc.isoformat()
                            if binding.profile.stop_at_utc is not None
                            else None
                        ),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        configuration = RuntimeConfig(
            endpoint=args.endpoint,
            auth_token=args.auth_token,
            request_timeout_seconds=args.timeout_seconds,
            arduino_port=args.arduino_port,
            poll_seconds=args.poll_seconds,
            max_observations=args.max_observations,
            max_actions=args.max_actions,
            max_runtime_seconds=args.max_runtime_seconds,
            verification_timeout_seconds=args.verification_timeout_seconds,
            behavior=replace(
                DEFAULT_RUNTIME_CONFIG.behavior,
                seed=args.behavior_seed,
            ),
        )
        application = EngineApplication(configuration=configuration)
        return _run_application(args, application, definition)
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(
            json.dumps(
                {"status": "ERROR", "reason": f"{type(error).__name__}: {error}"},
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

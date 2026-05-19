from __future__ import annotations

import argparse
import json
from typing import Any

from input_control.executor import LOOP_SCHEMA, backend_from_name, execute_action_loop, execute_next_action


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and optionally execute one input action from daemon context.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--snapshot-url", default="http://127.0.0.1:8893")
    parser.add_argument("--backend", choices=["pyautogui", "pydirectinput"], default="pyautogui")
    parser.add_argument("--movement-profile", choices=["instant_test", "linear_debug", "smooth_bezier", "fitts_guided", "wind_mouse"], default="linear_debug")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--focus-runelite", dest="focus_runelite", action="store_true")
    parser.add_argument("--no-focus-runelite", dest="focus_runelite", action="store_false")
    parser.set_defaults(focus_runelite=None)
    parser.add_argument("--window-title-filter", default="RuneLite")
    parser.add_argument("--verify-after-action", action="store_true")
    parser.add_argument("--after-action-wait-ms", type=int, default=500)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--max-actions", type=int, default=1)
    parser.add_argument("--max-runtime-seconds", type=float, default=30.0)
    parser.add_argument("--cooldown-ms", type=int, default=1200)
    parser.add_argument("--action-timeout-ms", type=int, default=5000)
    parser.add_argument("--result-timeout-ms", type=int, default=15000)
    parser.add_argument("--poll-interval-ms", type=int, default=250)
    parser.add_argument("--stop-on-warn", action="store_true")
    parser.add_argument("--stop-on-fail", action="store_true")
    return parser.parse_args(argv)


def apply_focus_default(args: argparse.Namespace) -> argparse.Namespace:
    if args.focus_runelite is None:
        args.focus_runelite = bool(args.execute and args.backend == "pyautogui")
    return args


def format_human(payload: dict[str, Any]) -> str:
    if payload.get("schema") == LOOP_SCHEMA:
        lines = [
            f"EXECUTE ACTION LOOP - {payload.get('status') or 'UNKNOWN'}",
            "",
            f"Mode: {'dry-run' if payload.get('dryRun') else 'execute'}",
            f"Executed actions: {payload.get('executedActionCount', 0)} / {payload.get('maxActions', 'unknown')}",
            f"Reason: {payload.get('reason') or 'unknown'}",
            "",
            "Actions:",
        ]
        action_results = payload.get("actionResults") if isinstance(payload.get("actionResults"), list) else []
        if action_results:
            for index, action_result in enumerate(action_results, start=1):
                proposal = action_result.get("proposal") if isinstance(action_result.get("proposal"), dict) else {}
                lifecycle = action_result.get("lifecycleState") if isinstance(action_result.get("lifecycleState"), dict) else {}
                observed = action_result.get("observedResult") if isinstance(action_result.get("observedResult"), dict) else {}
                commands = action_result.get("commands") if isinstance(action_result.get("commands"), list) else []
                lines.extend(
                    [
                        f"  {index}. {action_result.get('proposedAction') or 'none'} -> {proposal.get('targetName') or 'none'}",
                        f"     command: {commands[0] if commands else 'none'}",
                        f"     expected: {(action_result.get('expectedResult') or {}).get('resultType') if isinstance(action_result.get('expectedResult'), dict) else 'unknown'}",
                        f"     observed: {observed.get('observedResult') or 'unknown'}",
                        f"     outcome: {observed.get('resultOutcome') or lifecycle.get('resultOutcome') or 'unknown'} complete={observed.get('resultComplete') if observed.get('resultComplete') is not None else lifecycle.get('resultComplete')}",
                        f"     lifecycle: {lifecycle.get('currentState') or 'unknown'} reason={lifecycle.get('reason') or 'unknown'}",
                    ]
                )
        else:
            lines.append("  none")
        warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
        lines.extend(["", "Warnings:"])
        lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
        return "\n".join(lines).rstrip() + "\n"
    proposal = payload.get("proposal") if isinstance(payload.get("proposal"), dict) else {}
    movement = payload.get("movementPlan") if isinstance(payload.get("movementPlan"), dict) else {}
    resolution = payload.get("clickPointResolution") if isinstance(payload.get("clickPointResolution"), dict) else {}
    lifecycle = payload.get("lifecycleState") if isinstance(payload.get("lifecycleState"), dict) else {}
    observed = payload.get("observedResult") if isinstance(payload.get("observedResult"), dict) else {}
    lines = [
        f"EXECUTE NEXT ACTION - {payload.get('status') or 'UNKNOWN'}",
        "",
        f"Mode: {'execute' if payload.get('executed') else 'dry-run'}",
        f"Backend: {payload.get('backend') or 'unknown'}",
        f"Movement profile: {payload.get('movementProfile') or 'unknown'}",
        "",
        "Proposal:",
        f"  Action: {proposal.get('proposedAction') or payload.get('proposedAction')}",
        f"  Target: {proposal.get('targetName') or 'none'}",
        f"  Reason: {proposal.get('reason') or 'unknown'}",
        f"  Click point space: {proposal.get('clickPointSpace') or 'unknown'}",
        f"  Canvas click point: {proposal.get('suggestedClickPoint') or 'none'}",
        f"  Screen click point: {proposal.get('resolvedScreenClickPoint') or resolution.get('screenClickPoint') or 'none'}",
        f"  Conversion: {resolution.get('method') or 'unknown'}",
        f"  Key action: {proposal.get('keyAction') or 'none'}",
        "",
        "Movement:",
        f"  Duration ms: {movement.get('durationMs', 'n/a')}",
        f"  Point count: {movement.get('pointCount', 'n/a')}",
        f"  Click point: {movement.get('clickPoint', 'n/a')}",
        "",
        "Lifecycle:",
        f"  State: {lifecycle.get('currentState') or 'unknown'}",
        f"  Expected: {(payload.get('expectedResult') or {}).get('resultType') if isinstance(payload.get('expectedResult'), dict) else 'unknown'}",
        f"  Observed: {observed.get('observedResult') or 'unknown'}",
        f"  Signals: {', '.join(str(item) for item in (observed.get('observedSignals') or lifecycle.get('observedSignals') or [])) or 'none'}",
        f"  Outcome: {observed.get('resultOutcome') or lifecycle.get('resultOutcome') or 'unknown'} | complete={observed.get('resultComplete') if observed.get('resultComplete') is not None else lifecycle.get('resultComplete')}",
        f"  Next action allowed: {observed.get('nextActionAllowed') if observed.get('nextActionAllowed') is not None else lifecycle.get('nextActionAllowed')}",
        f"  Verification: {payload.get('verificationStatus') or 'unknown'}",
        f"  Next allowed: {payload.get('nextAllowedAt') or 'unknown'}",
        "",
        "Commands:",
    ]
    commands = payload.get("commands") if isinstance(payload.get("commands"), list) else []
    lines.extend(f"  {command}" for command in commands) if commands else lines.append("  none")
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(["", "Warnings:"])
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        args.execute = False
    apply_focus_default(args)
    backend = backend_from_name(
        args.backend,
        focus_runelite=args.focus_runelite,
        window_title_filter=args.window_title_filter,
    )
    result = execute_action_loop(args.daemon_url, args, backend=backend) if args.loop else execute_next_action(args.daemon_url, args, backend=backend)
    payload = result.to_dict()
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_human(payload), end="")
    return 0 if payload.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

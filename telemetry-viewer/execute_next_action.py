from __future__ import annotations

import argparse
import json
from typing import Any

from input_control.executor import backend_from_name, execute_next_action


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and optionally execute one input action from daemon context.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--snapshot-url", default="http://127.0.0.1:8893")
    parser.add_argument("--backend", choices=["pyautogui", "pydirectinput"], default="pyautogui")
    parser.add_argument("--movement-profile", choices=["instant_test", "linear_debug", "smooth_bezier", "fitts_guided", "wind_mouse"], default="linear_debug")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--focus-runelite", action="store_true")
    parser.add_argument("--window-title-filter", default="RuneLite")
    parser.add_argument("--verify-after-action", action="store_true")
    parser.add_argument("--after-action-wait-ms", type=int, default=500)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--timeout", type=float, default=3.0)
    return parser.parse_args(argv)


def format_human(payload: dict[str, Any]) -> str:
    proposal = payload.get("proposal") if isinstance(payload.get("proposal"), dict) else {}
    movement = payload.get("movementPlan") if isinstance(payload.get("movementPlan"), dict) else {}
    resolution = payload.get("clickPointResolution") if isinstance(payload.get("clickPointResolution"), dict) else {}
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
    backend = backend_from_name(
        args.backend,
        focus_runelite=args.focus_runelite,
        window_title_filter=args.window_title_filter,
    )
    result = execute_next_action(args.daemon_url, args, backend=backend)
    payload = result.to_dict()
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_human(payload), end="")
    return 0 if payload.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

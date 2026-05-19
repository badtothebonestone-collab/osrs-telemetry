from __future__ import annotations

import argparse
import json
from typing import Any

from input_control.mouse_movement import MouseMovementProfile, MousePoint, MouseTarget, plan_mouse_movement


SCHEMA = "mouse_movement_diagnostic.v1"


def build_diagnostic(
    *,
    start_x: int,
    start_y: int,
    target_x: int,
    target_y: int,
    target_radius: int,
    profile: str,
    seed: int | None,
    include_points: bool = False,
) -> dict[str, Any]:
    plan = plan_mouse_movement(
        MousePoint(start_x, start_y),
        MouseTarget(target_x, target_y, radius_px=target_radius, label="diagnostic target", source="cli"),
        MouseMovementProfile(name=profile, seed=seed) if seed is not None else profile,
    )
    payload = plan.to_dict(include_points=include_points)
    payload["schema"] = SCHEMA
    payload["validationStatus"] = plan.validation_status
    return payload


def format_human(payload: dict[str, Any]) -> str:
    click = payload.get("clickPoint") if isinstance(payload.get("clickPoint"), dict) else {}
    lines = [
        f"MOUSE MOVEMENT - {payload.get('validationStatus') or 'UNKNOWN'}",
        "",
        f"Profile: {payload.get('profileName') or 'unknown'}",
        f"Duration ms: {payload.get('durationMs')}",
        f"Point count: {payload.get('pointCount')}",
        f"Click point: {click.get('x')},{click.get('y')}" if click else "Click point: unknown",
        f"Path length px: {payload.get('pathLengthPx')}",
        f"Difficulty: {payload.get('estimatedDifficulty')}",
        "",
        "Warnings:",
    ]
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    if payload.get("points"):
        lines.extend(["", "Points:"])
        lines.extend(f"  {point.get('x')},{point.get('y')} @ {point.get('timestampMs', 0)}ms" for point in payload["points"])
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pure mouse movement planner diagnostic. Prints to stdout only.")
    parser.add_argument("--start-x", type=int, required=True)
    parser.add_argument("--start-y", type=int, required=True)
    parser.add_argument("--target-x", type=int, required=True)
    parser.add_argument("--target-y", type=int, required=True)
    parser.add_argument("--target-radius", type=int, default=4)
    parser.add_argument("--profile", choices=["instant_test", "linear_debug", "smooth_bezier", "fitts_guided", "wind_mouse"], default="linear_debug")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--print-points", action="store_true")
    parser.add_argument("--screen-width", type=int)
    parser.add_argument("--screen-height", type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_diagnostic(
        start_x=args.start_x,
        start_y=args.start_y,
        target_x=args.target_x,
        target_y=args.target_y,
        target_radius=args.target_radius,
        profile=args.profile,
        seed=args.seed,
        include_points=args.print_points,
    )
    if args.screen_width is not None and args.screen_height is not None:
        click = payload.get("clickPoint") if isinstance(payload.get("clickPoint"), dict) else {}
        if click and not (0 <= int(click.get("x", -1)) <= args.screen_width and 0 <= int(click.get("y", -1)) <= args.screen_height):
            payload["validationStatus"] = "FAIL"
            payload.setdefault("warnings", []).append("click point outside declared screen")
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_human(payload), end="")
    return 0 if payload.get("validationStatus") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

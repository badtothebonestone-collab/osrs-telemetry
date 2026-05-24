from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any

from input_control.action_proposal import build_action_proposal
from input_control.diagnostics import point_label, tile_label


SCHEMA = "action_proposal_diagnostic.v1"


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload)
    return decoded if isinstance(decoded, dict) else {}


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


def build_from_status(status: dict[str, Any]) -> dict[str, Any]:
    proposal = build_action_proposal(status).to_dict()
    proposal["schema"] = SCHEMA
    return proposal


def unavailable_payload(error: Exception | str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "FAIL",
        "proposedAction": "none",
        "targetKind": "none",
        "targetName": None,
        "reason": "daemon_unavailable",
        "confidence": 0.0,
        "warnings": [str(error)],
        "missingCapabilities": ["daemon.status"],
    }


def format_human(payload: dict[str, Any]) -> str:
    input_geometry = payload.get("inputGeometry") if isinstance(payload.get("inputGeometry"), dict) else {}
    resolution = payload.get("clickPointResolution") if isinstance(payload.get("clickPointResolution"), dict) else {}
    explanation = payload.get("targetExplanation") if isinstance(payload.get("targetExplanation"), dict) else {}
    explanation_freshness = explanation.get("freshness") if isinstance(explanation.get("freshness"), dict) else {}
    geometry_available = input_geometry.get("inputGeometryAvailable")
    lines = [
        f"ACTION PROPOSAL - {payload.get('status') or 'UNKNOWN'}",
        "",
        f"Proposed action: {payload.get('proposedAction') or 'none'}",
        f"Target kind: {payload.get('targetKind') or 'none'}",
        f"Target: {payload.get('targetName') or 'none'}",
        f"Confidence: {payload.get('confidence') if payload.get('confidence') is not None else 'unknown'}",
        f"Reason: {payload.get('reason') or 'unknown'}",
        f"Click point space: {payload.get('clickPointSpace') or 'unknown'}",
        f"Canvas click point: {point_label(payload.get('suggestedClickPoint'))}",
        f"Resolved screen click point: {point_label(payload.get('resolvedScreenClickPoint'))}",
        f"Input geometry available: {'yes' if geometry_available else 'no'}",
        f"Canvas origin: {point_label(input_geometry.get('canvasScreenOrigin'))}",
        f"Canvas size: {input_geometry.get('canvasSize') or 'unknown'}",
        f"Source canvas size: {input_geometry.get('sourceCanvasSize') or 'unknown'}",
        f"Geometry source/fallback: {resolution.get('method') or 'unknown'}",
        f"World tile: {tile_label(payload.get('suggestedWorldTile') or payload.get('targetTile'))}",
        f"Key action: {payload.get('keyAction') or 'none'}",
        f"Executable: {payload.get('executable')}",
    "",
    ]
    if explanation:
        lines.extend(
            [
                "Selected target:",
                f"  name: {explanation.get('name') or 'unknown'}",
                f"  id: {explanation.get('id') if explanation.get('id') is not None else 'unknown'}",
                f"  class: {explanation.get('classId') or 'unknown'}",
                f"  score: {explanation.get('score') if explanation.get('score') is not None else 'unknown'}",
                f"  screen: {point_label(explanation.get('screen'))}",
                f"  world: {tile_label(explanation.get('world'))}",
                f"  onScreen: {explanation.get('onScreen')}",
                f"  geometryAvailable: {explanation.get('geometryAvailable')}",
                f"  uiBlocked: {explanation.get('uiBlocked')}",
                f"  aim point: {point_label(explanation.get('aimPoint'))}",
                f"  aim source: {explanation.get('aimPointSource') or 'unknown'}",
                f"  freshness: {explanation_freshness.get('status') or explanation.get('freshness') or 'unknown'}",
                f"  stale: {explanation.get('stale')}",
                f"  accepted reasons: {', '.join(explanation.get('acceptedReasons') or []) or 'none'}",
                f"  rejected/demoted reasons: {', '.join(explanation.get('rejectedReasons') or []) or 'none'}",
                "",
            ]
        )
    lines.extend([
        "Warnings:",
    ])
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    if warnings:
        lines.extend(f"  WARN: {warning}" for warning in warnings)
    else:
        lines.append("  none")
    missing = payload.get("missingCapabilities") if isinstance(payload.get("missingCapabilities"), list) else []
    lines.extend(["", f"Missing capabilities: {', '.join(str(item) for item in missing) if missing else 'none'}"])
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only action proposal diagnostic. Prints to stdout only.")
    parser.add_argument("--from-daemon", action="store_true")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.from_daemon:
        payload = {
            "schema": SCHEMA,
            "status": "FAIL",
            "proposedAction": "none",
            "targetKind": "none",
            "reason": "from_daemon_required",
            "warnings": ["pass --from-daemon to read live daemon status"],
            "missingCapabilities": ["daemon.status"],
        }
        print(json.dumps(payload, indent=2) if args.json else format_human(payload), end="")
        return 1
    try:
        payload = build_from_status(fetch_json(daemon_status_url(args.daemon_url), timeout=args.timeout))
        code = 0
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        payload = unavailable_payload(f"{type(error).__name__}: {error}")
        code = 1
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_human(payload), end="")
    return code if payload.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

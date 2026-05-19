from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any

from input_control.input_geometry import input_geometry_from_status


SCHEMA = "input_geometry_diagnostic.v1"


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload)
    return decoded if isinstance(decoded, dict) else {}


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


def build_from_status(status: dict[str, Any]) -> dict[str, Any]:
    geometry = input_geometry_from_status(status)
    payload = dict(geometry)
    payload["schema"] = SCHEMA
    return payload


def unavailable_payload(error: Exception | str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "FAIL",
        "inputGeometryAvailable": False,
        "reason": "daemon_unavailable",
        "warnings": [str(error)],
        "missingCapabilities": ["daemon.status"],
    }


def format_human(payload: dict[str, Any]) -> str:
    lines = [
        f"INPUT GEOMETRY - {payload.get('status') or 'UNKNOWN'}",
        f"  available: {'yes' if payload.get('inputGeometryAvailable') else 'no'}",
        f"  canvas origin: {payload.get('canvasScreenOrigin') or 'unknown'}",
        f"  canvas size: {payload.get('canvasSize') or 'unknown'}",
        f"  source canvas size: {payload.get('sourceCanvasSize') or 'unknown'}",
        f"  client window: {payload.get('clientWindowBounds') or 'unknown'}",
        f"  display scale: {payload.get('displayScale') or 'unknown'}",
        f"  reason: {payload.get('reason') or 'unknown'}",
    ]
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    if warnings:
        lines.append("  warnings:")
        lines.extend(f"    WARN: {warning}" for warning in warnings)
    missing = payload.get("missingCapabilities") if isinstance(payload.get("missingCapabilities"), list) else []
    if missing:
        lines.append(f"  missing capabilities: {', '.join(str(item) for item in missing)}")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only input geometry diagnostic. Prints to stdout only.")
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
            "inputGeometryAvailable": False,
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

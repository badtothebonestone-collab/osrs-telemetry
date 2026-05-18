from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any


SCHEMA = "resource_return_context_diagnostic.v1"


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload)
    return decoded if isinstance(decoded, dict) else {}


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


def bool_label(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "unknown"


def value_label(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def tile_label(tile: Any) -> str:
    if not isinstance(tile, dict):
        return "none"
    if tile.get("worldX") is None or tile.get("worldY") is None or tile.get("plane") is None:
        return "none"
    return f"{tile.get('worldX')},{tile.get('worldY')},{tile.get('plane')}"


def build_from_daemon(status: dict[str, Any]) -> dict[str, Any]:
    brain = status.get("brain") if isinstance(status.get("brain"), dict) else {}
    generic = brain.get("genericTaskState") if isinstance(brain.get("genericTaskState"), dict) else {}
    bank_operation = brain.get("bankOperationContext") if isinstance(brain.get("bankOperationContext"), dict) else {}
    bank_ui = brain.get("bankUiContext") if isinstance(brain.get("bankUiContext"), dict) else {}
    context = brain.get("resourceReturnContext") if isinstance(brain.get("resourceReturnContext"), dict) else {}
    return {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "daemonReachable": True,
        "resourceReturnContextPresent": bool(context),
        "bankingComplete": bank_operation.get("bankingComplete", status.get("bankingComplete")),
        "bankOpen": bank_ui.get("bankOpen", status.get("bankOpen")),
        "resourceTargetCurrentlyVisible": context.get("resourceTargetCurrentlyVisible", status.get("resourceTargetCurrentlyVisible")),
        "resourceMemoryValid": context.get("resourceMemoryValid", status.get("resourceMemoryValid")),
        "resourceMemoryAgeTicks": context.get("resourceMemoryAgeTicks", status.get("resourceMemoryAgeTicks")),
        "returnDestinationNeeded": context.get("returnDestinationNeeded", status.get("resourceReturnDestinationNeeded")),
        "returnDestinationAvailable": context.get("returnDestinationAvailable", status.get("resourceReturnDestinationAvailable")),
        "returnDestinationTile": context.get("returnDestinationTile", status.get("resourceReturnDestinationTile")),
        "returnDestinationSource": context.get("returnDestinationSource", status.get("resourceReturnDestinationSource")),
        "reason": context.get("reason", status.get("resourceReturnReason")),
        "nextPhase": generic.get("phase", status.get("brainPhase")),
        "activeIntent": generic.get("activeIntent", status.get("activeIntent")),
        "missingCapabilities": list(context.get("missingCapabilities") or status.get("resourceReturnMissingCapabilities") or []),
        "warnings": list(context.get("warnings") or status.get("resourceReturnWarnings") or []),
        "status": context.get("status", status.get("resourceReturnStatus")),
        "noActionEmitted": brain.get("noActionEmitted", True),
    }


def unavailable_payload(error: Exception | str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "daemonReachable": False,
        "resourceReturnContextPresent": False,
        "status": "FAIL",
        "warnings": [str(error)],
        "missingCapabilities": ["daemon.status"],
        "noActionEmitted": True,
    }


def format_human(payload: dict[str, Any]) -> str:
    missing = payload.get("missingCapabilities") if isinstance(payload.get("missingCapabilities"), list) else []
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines = [
        f"RESOURCE RETURN CONTEXT - {payload.get('status') or 'UNKNOWN'}",
        "",
        f"Source: {payload.get('source')}",
        f"Daemon reachable: {bool_label(payload.get('daemonReachable'))}",
        f"Context present: {bool_label(payload.get('resourceReturnContextPresent'))}",
        f"Banking complete: {bool_label(payload.get('bankingComplete'))}",
        f"Bank open: {bool_label(payload.get('bankOpen'))}",
        f"Resource target currently visible: {bool_label(payload.get('resourceTargetCurrentlyVisible'))}",
        f"Resource memory valid: {bool_label(payload.get('resourceMemoryValid'))}",
        f"Resource memory age ticks: {value_label(payload.get('resourceMemoryAgeTicks'))}",
        f"Return destination needed: {bool_label(payload.get('returnDestinationNeeded'))}",
        f"Return destination available: {bool_label(payload.get('returnDestinationAvailable'))}",
        f"Return destination tile: {tile_label(payload.get('returnDestinationTile'))}",
        f"Return destination source: {payload.get('returnDestinationSource') or 'none'}",
        f"Reason: {payload.get('reason') or 'unknown'}",
        f"Next phase: {payload.get('nextPhase') or 'unknown'}",
        f"Active intent: {payload.get('activeIntent') or 'unknown'}",
        "",
        f"Missing capabilities: {', '.join(str(item) for item in missing) if missing else 'none'}",
        "Warnings:",
    ]
    if warnings:
        lines.extend(f"  {warning}" for warning in warnings)
    else:
        lines.append("  none")
    lines.extend(["", f"noActionEmitted: {str(payload.get('noActionEmitted')).lower()}"])
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only resource return destination diagnostic. Prints to stdout only.")
    parser.add_argument("--from-daemon", action="store_true", help="Read current live daemon memory/status.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true", help="Print compact JSON to stdout only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.from_daemon:
        payload = {
            "schema": SCHEMA,
            "source": "not_requested",
            "daemonReachable": False,
            "resourceReturnContextPresent": False,
            "status": "FAIL",
            "warnings": ["pass --from-daemon to read live daemon resource return context"],
            "missingCapabilities": ["daemon.status"],
            "noActionEmitted": True,
        }
        print(json.dumps(payload, indent=2) if args.json else format_human(payload), end="")
        return 1
    try:
        payload = build_from_daemon(fetch_json(daemon_status_url(args.daemon_url), timeout=args.timeout))
        code = 0
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        payload = unavailable_payload(f"{type(error).__name__}: {error}")
        code = 1
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_human(payload), end="")
    return code if payload.get("status") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any


SCHEMA = "return_to_resource_context_diagnostic.v1"


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


def value_or_unknown(value: Any) -> Any:
    return value if value is not None else "unknown"


def target_label(target: Any) -> str:
    if not isinstance(target, dict) or not target:
        return "none"
    return str(target.get("targetName") or target.get("name") or target.get("classId") or target.get("objectKey") or "target")


def build_from_daemon(status: dict[str, Any]) -> dict[str, Any]:
    brain = status.get("brain") if isinstance(status.get("brain"), dict) else {}
    generic = brain.get("genericTaskState") if isinstance(brain.get("genericTaskState"), dict) else {}
    bank_operation = brain.get("bankOperationContext") if isinstance(brain.get("bankOperationContext"), dict) else {}
    context = brain.get("returnToResourceContext") if isinstance(brain.get("returnToResourceContext"), dict) else {}
    best_target = context.get("bestResourceTarget", status.get("returnBestResourceTarget"))
    return {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "daemonReachable": True,
        "returnToResourceContextPresent": bool(context),
        "bankingComplete": bank_operation.get("bankingComplete", status.get("bankingComplete")),
        "inventoryFreeSlots": context.get("inventoryFreeSlots", status.get("returnInventoryFreeSlots", status.get("inventoryFreeSlots"))),
        "resourceTargetAvailable": context.get("resourceTargetAvailable", status.get("returnResourceTargetAvailable")),
        "bestResourceTarget": target_label(best_target),
        "resourcePathingNeeded": context.get("resourcePathingNeeded", status.get("returnResourcePathingNeeded")),
        "returnNeeded": context.get("returnNeeded", status.get("returnToResourceNeeded")),
        "returnReady": context.get("returnReady", status.get("returnToResourceReady")),
        "nextPhase": generic.get("phase", status.get("brainPhase")),
        "activeIntent": generic.get("activeIntent"),
        "missingCapabilities": list(context.get("missingCapabilities") or status.get("returnToResourceMissingCapabilities") or []),
        "warnings": list(context.get("warnings") or status.get("returnToResourceWarnings") or []),
        "status": context.get("status", status.get("returnToResourceStatus")),
        "reason": context.get("reason", status.get("returnToResourceReason")),
        "noActionEmitted": brain.get("noActionEmitted", True),
    }


def unavailable_payload(error: Exception | str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "daemonReachable": False,
        "returnToResourceContextPresent": False,
        "warnings": [str(error)],
        "missingCapabilities": ["daemon.status"],
        "noActionEmitted": True,
    }


def format_human(payload: dict[str, Any]) -> str:
    lines = [
        "RETURN-TO-RESOURCE CONTEXT DIAGNOSTIC",
        "",
        f"Source: {payload.get('source')}",
        f"Daemon reachable: {bool_label(payload.get('daemonReachable'))}",
        f"Return context present: {bool_label(payload.get('returnToResourceContextPresent'))}",
        f"Status: {payload.get('status') or 'unknown'}",
        f"Reason: {payload.get('reason') or 'unknown'}",
        f"Banking complete: {bool_label(payload.get('bankingComplete'))}",
        f"Inventory free slots: {value_or_unknown(payload.get('inventoryFreeSlots'))}",
        f"Resource target available: {bool_label(payload.get('resourceTargetAvailable'))}",
        f"Best resource target: {payload.get('bestResourceTarget') or 'none'}",
        f"Resource pathing needed: {bool_label(payload.get('resourcePathingNeeded'))}",
        f"Return needed: {bool_label(payload.get('returnNeeded'))}",
        f"Return ready: {bool_label(payload.get('returnReady'))}",
        f"Next phase: {payload.get('nextPhase') or 'unknown'}",
        f"Active intent: {payload.get('activeIntent') or 'unknown'}",
    ]
    missing = payload.get("missingCapabilities") if isinstance(payload.get("missingCapabilities"), list) else []
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(["", f"Missing capabilities: {', '.join(str(item) for item in missing) if missing else 'none'}", "Warnings:"])
    if warnings:
        lines.extend(f"  {warning}" for warning in warnings)
    else:
        lines.append("  none")
    lines.extend(["", f"noActionEmitted: {str(payload.get('noActionEmitted')).lower()}"])
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only return-to-resource context diagnostic. Prints to stdout only.")
    parser.add_argument("--from-daemon", action="store_true", help="Read current live daemon memory/status.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.from_daemon:
        payload = {
            "schema": SCHEMA,
            "source": "not_requested",
            "daemonReachable": False,
            "returnToResourceContextPresent": False,
            "warnings": ["pass --from-daemon to read live daemon return-to-resource context"],
            "noActionEmitted": True,
        }
        print(json.dumps(payload, indent=2) if args.json else format_human(payload), end="")
        return 1
    try:
        status = fetch_json(daemon_status_url(args.daemon_url), timeout=args.timeout)
        payload = build_from_daemon(status)
        code = 0
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        payload = unavailable_payload(f"{type(error).__name__}: {error}")
        code = 1
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_human(payload), end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

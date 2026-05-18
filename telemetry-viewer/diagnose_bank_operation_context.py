from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any


SCHEMA = "bank_operation_context_diagnostic.v1"


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


def build_from_daemon(status: dict[str, Any]) -> dict[str, Any]:
    brain = status.get("brain") if isinstance(status.get("brain"), dict) else {}
    generic = brain.get("genericTaskState") if isinstance(brain.get("genericTaskState"), dict) else {}
    bank_ui = brain.get("bankUiContext") if isinstance(brain.get("bankUiContext"), dict) else {}
    operation = brain.get("bankOperationContext") if isinstance(brain.get("bankOperationContext"), dict) else {}
    return {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "daemonReachable": True,
        "bankOperationContextPresent": bool(operation),
        "bankOpen": bank_ui.get("bankOpen", status.get("bankOpen")),
        "bankReadable": operation.get("bankReadable", bank_ui.get("bankReadable", status.get("bankReadable"))),
        "bankPinOpen": bank_ui.get("bankPinOpen", status.get("bankPinOpen")),
        "operationNeeded": operation.get("operationNeeded", status.get("bankOperationNeeded")),
        "operationType": operation.get("operationType", status.get("bankOperationType")),
        "resourceItemsHeld": operation.get("resourceItemsHeld", status.get("bankResourceItemsHeld")),
        "resourceItemSlots": list(operation.get("resourceItemSlots") or status.get("bankResourceItemSlots") or []),
        "resourceItemQuantity": operation.get("resourceItemQuantity", status.get("bankResourceItemQuantity")),
        "nonResourceItemsHeld": operation.get("nonResourceItemsHeld", status.get("bankNonResourceItemsHeld")),
        "inventoryFreeSlots": operation.get("inventoryFreeSlots", status.get("inventoryFreeSlots")),
        "depositInventoryAvailable": operation.get("depositInventoryAvailable", status.get("depositInventoryButtonVisible")),
        "bankingComplete": operation.get("bankingComplete", status.get("bankingComplete")),
        "completionReason": operation.get("completionReason", status.get("bankOperationCompletionReason")),
        "nextPhase": generic.get("phase", status.get("brainPhase")),
        "activeIntent": generic.get("activeIntent"),
        "missingCapabilities": list(operation.get("missingCapabilities") or status.get("bankOperationMissingCapabilities") or []),
        "warnings": list(operation.get("warnings") or status.get("bankOperationWarnings") or []),
        "status": operation.get("status", status.get("bankOperationStatus")),
        "reason": operation.get("reason", status.get("bankOperationReason")),
        "noActionEmitted": brain.get("noActionEmitted", True),
    }


def unavailable_payload(error: Exception | str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "daemonReachable": False,
        "bankOperationContextPresent": False,
        "warnings": [str(error)],
        "missingCapabilities": ["daemon.status"],
        "noActionEmitted": True,
    }


def format_human(payload: dict[str, Any]) -> str:
    slots = payload.get("resourceItemSlots") if isinstance(payload.get("resourceItemSlots"), list) else []
    lines = [
        "BANK OPERATION CONTEXT DIAGNOSTIC",
        "",
        f"Source: {payload.get('source')}",
        f"Daemon reachable: {bool_label(payload.get('daemonReachable'))}",
        f"Bank operation context present: {bool_label(payload.get('bankOperationContextPresent'))}",
        f"Status: {payload.get('status') or 'unknown'}",
        f"Reason: {payload.get('reason') or 'unknown'}",
        f"Bank open: {bool_label(payload.get('bankOpen'))}",
        f"Bank readable: {bool_label(payload.get('bankReadable'))}",
        f"Bank pin open: {bool_label(payload.get('bankPinOpen'))}",
        f"Operation needed: {bool_label(payload.get('operationNeeded'))}",
        f"Operation type: {payload.get('operationType') or 'unknown'}",
        f"Resource items held: {value_or_unknown(payload.get('resourceItemsHeld'))}",
        f"Resource item slots: {', '.join(str(slot) for slot in slots) if slots else 'none'}",
        f"Resource item quantity: {value_or_unknown(payload.get('resourceItemQuantity'))}",
        f"Non-resource items held: {value_or_unknown(payload.get('nonResourceItemsHeld'))}",
        f"Inventory free slots: {value_or_unknown(payload.get('inventoryFreeSlots'))}",
        f"Deposit inventory available: {bool_label(payload.get('depositInventoryAvailable'))}",
        f"Banking complete: {bool_label(payload.get('bankingComplete'))}",
        f"Completion reason: {payload.get('completionReason') or 'unknown'}",
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
    parser = argparse.ArgumentParser(description="Read-only bank operation context diagnostic. Prints to stdout only.")
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
            "bankOperationContextPresent": False,
            "warnings": ["pass --from-daemon to read live daemon bank operation context"],
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

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any


SCHEMA = "bank_ui_context_diagnostic.v1"


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


def build_from_daemon(status: dict[str, Any]) -> dict[str, Any]:
    brain = status.get("brain") if isinstance(status.get("brain"), dict) else {}
    bank_ui = brain.get("bankUiContext") if isinstance(brain.get("bankUiContext"), dict) else {}
    inventory_summary = bank_ui.get("inventorySummary") if isinstance(bank_ui.get("inventorySummary"), dict) else {}
    bank_summary = bank_ui.get("bankSummary") if isinstance(bank_ui.get("bankSummary"), dict) else {}
    close_button_visible = bank_ui.get("closeButtonVisible")
    if close_button_visible is None:
        close_button_visible = bank_ui.get("bankCloseButtonVisible", status.get("closeButtonVisible", status.get("bankCloseButtonVisible")))
    return {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "daemonReachable": True,
        "bankUiContextPresent": bool(bank_ui),
        "bankOpen": bank_ui.get("bankOpen", status.get("bankOpen")),
        "bankReadable": bank_ui.get("bankReadable", status.get("bankReadable")),
        "bankPinOpen": bank_ui.get("bankPinOpen", status.get("bankPinOpen")),
        "topLevelInterfaceId": bank_ui.get("topLevelInterfaceId", status.get("bankTopLevelInterfaceId")),
        "bankRootVisible": bank_ui.get("bankRootVisible", status.get("bankRootVisible")),
        "bankContainerVisible": bank_ui.get("bankContainerVisible", status.get("bankContainerVisible")),
        "bankInventoryVisible": bank_ui.get("bankInventoryVisible", status.get("bankInventoryVisible")),
        "depositInventoryButtonVisible": bank_ui.get("depositInventoryButtonVisible", status.get("depositInventoryButtonVisible")),
        "closeButtonVisible": close_button_visible,
        "bankCloseButtonVisible": close_button_visible,
        "inventoryFreeSlots": inventory_summary.get("freeSlots", status.get("inventoryFreeSlots")),
        "inventoryOccupiedSlots": inventory_summary.get("occupiedSlots", status.get("inventoryOccupiedSlots")),
        "inventoryMatchingResourceCount": inventory_summary.get("matchingResourceCount", status.get("inventoryMatchingResourceCount")),
        "bankOccupiedSlots": bank_summary.get("occupiedSlots", status.get("bankOccupiedSlots")),
        "bankUniqueItemCount": bank_summary.get("uniqueItemCount", status.get("bankUniqueItemCount")),
        "inventorySummary": inventory_summary,
        "bankSummary": bank_summary,
        "missingCapabilities": list(bank_ui.get("missingCapabilities") or status.get("bankUiMissingCapabilities") or []),
        "warnings": list(bank_ui.get("warnings") or status.get("bankUiWarnings") or []),
        "status": bank_ui.get("status", status.get("bankUiStatus")),
        "reason": bank_ui.get("reason", status.get("bankUiReason")),
        "noActionEmitted": brain.get("noActionEmitted", True),
    }


def unavailable_payload(error: Exception | str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "daemonReachable": False,
        "bankUiContextPresent": False,
        "warnings": [str(error)],
        "missingCapabilities": ["daemon.status"],
        "noActionEmitted": True,
    }


def format_human(payload: dict[str, Any]) -> str:
    lines = [
        "BANK UI CONTEXT DIAGNOSTIC",
        "",
        f"Source: {payload.get('source')}",
        f"Daemon reachable: {bool_label(payload.get('daemonReachable'))}",
        f"Bank UI context present: {bool_label(payload.get('bankUiContextPresent'))}",
        f"Status: {payload.get('status') or 'unknown'}",
        f"Reason: {payload.get('reason') or 'unknown'}",
        f"Bank open: {bool_label(payload.get('bankOpen'))}",
        f"Bank readable: {bool_label(payload.get('bankReadable'))}",
        f"Bank pin open: {bool_label(payload.get('bankPinOpen'))}",
        f"Top-level interface id: {payload.get('topLevelInterfaceId') if payload.get('topLevelInterfaceId') is not None else 'unknown'}",
        f"Bank root visible: {bool_label(payload.get('bankRootVisible'))}",
        f"Bank container visible: {bool_label(payload.get('bankContainerVisible'))}",
        f"Bank inventory visible: {bool_label(payload.get('bankInventoryVisible'))}",
        f"Deposit inventory button visible: {bool_label(payload.get('depositInventoryButtonVisible'))}",
        f"Close button visible: {bool_label(payload.get('closeButtonVisible'))}",
        f"Inventory free/occupied: {payload.get('inventoryFreeSlots') if payload.get('inventoryFreeSlots') is not None else 'unknown'}/"
        f"{payload.get('inventoryOccupiedSlots') if payload.get('inventoryOccupiedSlots') is not None else 'unknown'}",
        f"Matching resource count: {payload.get('inventoryMatchingResourceCount') if payload.get('inventoryMatchingResourceCount') is not None else 'unknown'}",
        f"Bank occupied slots: {payload.get('bankOccupiedSlots') if payload.get('bankOccupiedSlots') is not None else 'unknown'}",
        f"Bank unique item count: {payload.get('bankUniqueItemCount') if payload.get('bankUniqueItemCount') is not None else 'unknown'}",
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
    parser = argparse.ArgumentParser(description="Read-only bank UI context diagnostic. Prints to stdout only.")
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
            "bankUiContextPresent": False,
            "warnings": ["pass --from-daemon to read live daemon bank UI context"],
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

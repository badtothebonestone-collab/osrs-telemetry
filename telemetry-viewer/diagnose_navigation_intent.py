from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any


SCHEMA = "navigation_intent_diagnostic.v1"


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload)
    return decoded if isinstance(decoded, dict) else {}


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


def build_from_daemon(status: dict[str, Any], *, task: str, policy: str) -> dict[str, Any]:
    brain = status.get("brain") if isinstance(status.get("brain"), dict) else {}
    navigation = brain.get("navigationIntentContext") if isinstance(brain.get("navigationIntentContext"), dict) else {}
    generic = brain.get("genericTaskState") if isinstance(brain.get("genericTaskState"), dict) else {}
    service = brain.get("serviceContext") if isinstance(brain.get("serviceContext"), dict) else {}
    process = brain.get("processInventoryContext") if isinstance(brain.get("processInventoryContext"), dict) else {}
    return {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "task": task,
        "policy": policy,
        "daemonReachable": True,
        "navigationIntentPresent": bool(navigation),
        "activeIntent": generic.get("activeIntent"),
        "phase": generic.get("phase"),
        "navigationIntentContext": navigation or None,
        "serviceContext": service or None,
        "processInventoryContext": process or None,
        "navigationNeeded": navigation.get("navigationNeeded") if navigation else None,
        "navigationReason": navigation.get("navigationReason") if navigation else "not_reported",
        "targetKind": navigation.get("targetKind") if navigation else None,
        "destinationTarget": navigation.get("destinationTarget") if navigation else None,
        "directReachability": navigation.get("directReachability") if navigation else None,
        "collisionWindowAvailable": navigation.get("collisionWindowAvailable") if navigation else None,
        "missingCapabilities": navigation.get("missingCapabilities", []) if navigation else [],
        "warnings": navigation.get("warnings", []) if navigation else ["daemon brain state did not expose navigation intent context"],
        "noActionEmitted": True,
    }


def target_label(target: dict[str, Any] | None) -> str:
    if not isinstance(target, dict) or not target:
        return "none"
    name = target.get("targetName") or target.get("name") or target.get("classId") or "target"
    target_id = target.get("id")
    if target_id is not None:
        return f"{name} {target_id}"
    return str(name)


def format_human(payload: dict[str, Any]) -> str:
    nav = payload.get("navigationIntentContext") if isinstance(payload.get("navigationIntentContext"), dict) else {}
    lines = [
        "NAVIGATION INTENT DIAGNOSTIC",
        "",
        f"Source: {payload.get('source')}",
        f"Task: {payload.get('task')}",
        f"Policy: {payload.get('policy')}",
        f"Active intent: {payload.get('activeIntent') or 'unknown'}",
        f"Navigation context present: {'yes' if payload.get('navigationIntentPresent') else 'no'}",
        "",
        "Navigation context:",
        f"  needed: {'yes' if payload.get('navigationNeeded') else 'no'}",
        f"  reason: {payload.get('navigationReason')}",
        f"  target kind: {payload.get('targetKind') or 'none'}",
        f"  destination: {target_label(payload.get('destinationTarget'))}",
        f"  reachability: {payload.get('directReachability') or 'unknown'}",
        f"  collision window: {'available' if payload.get('collisionWindowAvailable') else 'missing' if payload.get('collisionWindowAvailable') is False else 'unknown'}",
    ]
    if nav.get("distanceTiles") is not None:
        lines.append(f"  distance: {nav.get('distanceTiles')} tiles")
    missing = payload.get("missingCapabilities") if isinstance(payload.get("missingCapabilities"), list) else []
    if missing:
        lines.append(f"  missing: {', '.join(str(item) for item in missing)}")
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(["", "Warnings:"])
    if warnings:
        for warning in warnings:
            lines.append(f"  {warning}")
    else:
        lines.append("  none")
    lines.extend(["", f"noActionEmitted: {str(payload.get('noActionEmitted')).lower()}"])
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only navigation intent diagnostic. Prints to stdout only.")
    parser.add_argument("--from-daemon", action="store_true", help="Read current live daemon memory/status.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--task", default="woodcutting")
    parser.add_argument("--policy", default="woodcutting_bank")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.from_daemon:
        payload = {
            "schema": SCHEMA,
            "source": "not_requested",
            "task": args.task,
            "policy": args.policy,
            "daemonReachable": False,
            "navigationIntentPresent": False,
            "warnings": ["pass --from-daemon to read live daemon navigation intent context"],
            "noActionEmitted": True,
        }
    else:
        try:
            payload = build_from_daemon(fetch_json(daemon_status_url(args.daemon_url), timeout=args.timeout), task=args.task, policy=args.policy)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            payload = {
                "schema": SCHEMA,
                "source": "daemon-memory",
                "task": args.task,
                "policy": args.policy,
                "daemonReachable": False,
                "navigationIntentPresent": False,
                "warnings": [f"daemon status unavailable: {type(error).__name__}: {error}"],
                "noActionEmitted": True,
            }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print(format_human(payload), end="")
    return 0 if payload.get("daemonReachable") or not args.from_daemon else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any

from input_control.action_lifecycle import DIAGNOSTIC_SCHEMA, build_lifecycle_diagnostic


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload)
    return decoded if isinstance(decoded, dict) else {}


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


def unavailable_payload(error: Exception | str) -> dict[str, Any]:
    return {
        "schema": DIAGNOSTIC_SCHEMA,
        "status": "FAIL",
        "cycleStage": "unknown",
        "phase": "unknown",
        "activeIntent": "unknown",
        "lifecycleState": {
            "currentState": "blocked",
            "lastAction": None,
            "expectedResult": None,
            "observedResult": None,
            "attempts": 0,
            "maxAttempts": 1,
            "reason": "daemon_unavailable",
            "warnings": [str(error)],
        },
        "lastAction": None,
        "expectedResult": None,
        "observedResult": None,
        "cooldown": {},
        "attempts": 0,
        "reason": "daemon_unavailable",
        "warnings": [str(error)],
        "missingCapabilities": ["daemon.status"],
    }


def format_human(payload: dict[str, Any]) -> str:
    lifecycle = payload.get("lifecycleState") if isinstance(payload.get("lifecycleState"), dict) else {}
    cooldown = payload.get("cooldown") if isinstance(payload.get("cooldown"), dict) else {}
    expected = payload.get("expectedResult") if isinstance(payload.get("expectedResult"), dict) else {}
    observed = payload.get("observedResult") if isinstance(payload.get("observedResult"), dict) else {}
    lines = [
        f"ACTION LIFECYCLE - {payload.get('status') or 'UNKNOWN'}",
        "",
        "Current:",
        f"  cycleStage: {payload.get('cycleStage') or 'unknown'}",
        f"  phase: {payload.get('phase') or 'unknown'}",
        f"  activeIntent: {payload.get('activeIntent') or 'unknown'}",
        "",
        "Lifecycle:",
        f"  state: {lifecycle.get('currentState') or 'unknown'}",
        f"  last action: {payload.get('lastAction') or lifecycle.get('lastAction') or 'none'}",
        f"  expected: {expected.get('resultType') or 'unknown'}",
        f"  observed: {observed.get('observedResult') or 'unknown'}",
        f"  cooldown: {cooldown.get('cooldownUntilUtc') or cooldown.get('cooldownUntilTick') or 'none'}",
        f"  attempts: {payload.get('attempts') if payload.get('attempts') is not None else lifecycle.get('attempts', 0)}",
        f"  reason: {payload.get('reason') or lifecycle.get('reason') or 'unknown'}",
        "",
        "Warnings:",
    ]
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    missing = payload.get("missingCapabilities") if isinstance(payload.get("missingCapabilities"), list) else []
    lines.extend(["", f"Missing capabilities: {', '.join(str(item) for item in missing) if missing else 'none'}"])
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only input action lifecycle diagnostic. Prints to stdout only.")
    parser.add_argument("--from-daemon", action="store_true")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.from_daemon:
        payload = unavailable_payload("pass --from-daemon to read live daemon status")
        print(json.dumps(payload, indent=2) if args.json else format_human(payload), end="")
        return 1
    try:
        payload = build_lifecycle_diagnostic(fetch_json(daemon_status_url(args.daemon_url), timeout=args.timeout))
        code = 0
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        payload = unavailable_payload(f"{type(error).__name__}: {error}")
        code = 1
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_human(payload), end="")
    return code if payload.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

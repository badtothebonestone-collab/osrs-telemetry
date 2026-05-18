from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any


SCHEMA = "cycle_history_diagnostic.v1"


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload)
    return decoded if isinstance(decoded, dict) else {}


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _tail(rows: list[Any], count: int) -> list[dict[str, Any]]:
    clean = [dict(row) for row in rows if isinstance(row, dict)]
    if count <= 0:
        return []
    return clean[-count:]


def build_from_daemon(status: dict[str, Any], *, tail: int = 20, transitions_only: bool = False) -> dict[str, Any]:
    history = _dict(status.get("cycleHistory"))
    rows = _tail(_list(history.get("cycleHistory") or status.get("cycleHistoryTail")), tail)
    if transitions_only:
        rows = [row for row in rows if row.get("transition")]
    current = rows[-1] if rows else {
        "cycleStage": history.get("currentCycleStage") or status.get("currentCycleStage"),
        "phase": status.get("brainPhase"),
        "activeIntent": _dict(_dict(status.get("brain")).get("genericTaskState")).get("activeIntent"),
        "reason": history.get("lastCycleTransitionReason") or status.get("lastCycleTransitionReason"),
    }
    return {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "daemonReachable": True,
        "status": "PASS",
        "current": {
            "cycleStage": current.get("cycleStage") or history.get("currentCycleStage") or status.get("currentCycleStage"),
            "phase": current.get("phase") or status.get("brainPhase"),
            "activeIntent": current.get("activeIntent"),
            "reason": current.get("reason") or history.get("lastCycleTransitionReason") or status.get("lastCycleTransitionReason"),
        },
        "cycleHistoryCount": history.get("cycleHistoryCount", status.get("cycleHistoryCount", len(rows))),
        "transitionCount": history.get("transitionCount", status.get("cycleTransitionCount", 0)),
        "currentCycleStageStableForTicks": history.get(
            "currentCycleStageStableForTicks",
            status.get("currentCycleStageStableForTicks"),
        ),
        "lastCycleStage": history.get("lastCycleStage", status.get("lastCycleStage")),
        "lastCycleTransitionReason": history.get("lastCycleTransitionReason", status.get("lastCycleTransitionReason")),
        "lastStageChangeTick": history.get("lastStageChangeTick", status.get("lastCycleStageChangeTick")),
        "lastWarningSummary": history.get("lastWarningSummary", status.get("cycleLastWarningSummary") or {}),
        "cycleHistory": rows,
        "warnings": [],
        "missingCapabilities": [],
    }


def unavailable_payload(error: Exception | str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "daemonReachable": False,
        "status": "FAIL",
        "current": {},
        "cycleHistory": [],
        "warnings": [str(error)],
        "missingCapabilities": ["daemon.status"],
    }


def format_transition(row: dict[str, Any]) -> str:
    tick = row.get("tick")
    previous = row.get("previousCycleStage")
    current = row.get("cycleStage")
    reason = row.get("reason") or "unknown"
    target = row.get("selectedTargetName")
    if previous:
        text = f"  tick {tick}: {previous} -> {current} reason={reason}"
    else:
        text = f"  tick {tick}: {current} reason={reason}"
    if target:
        text += f" target={target}"
    return text


def format_human(payload: dict[str, Any]) -> str:
    current = _dict(payload.get("current"))
    warning_summary = _dict(payload.get("lastWarningSummary"))
    lines = [
        "WOODCUT BANK CYCLE HISTORY",
        "",
        "Current:",
        f"  Stage: {current.get('cycleStage') or 'unknown'}",
        f"  Phase: {current.get('phase') or 'unknown'}",
        f"  Active intent: {current.get('activeIntent') or 'unknown'}",
        f"  Reason: {current.get('reason') or 'unknown'}",
        "",
        f"Total history entries: {payload.get('cycleHistoryCount', 0)}",
        f"Transition count: {payload.get('transitionCount', 0)}",
        f"Current stage duration ticks: {payload.get('currentCycleStageStableForTicks') if payload.get('currentCycleStageStableForTicks') is not None else 'unknown'}",
        f"Last stage change tick: {payload.get('lastStageChangeTick') if payload.get('lastStageChangeTick') is not None else 'unknown'}",
        f"Last warning/missing-capability summary: warnings={warning_summary.get('warningCount', 'unknown')} missing={warning_summary.get('missingCapabilityCount', 'unknown')}",
        "",
        "Recent transitions:",
    ]
    rows = [row for row in _list(payload.get("cycleHistory")) if isinstance(row, dict)]
    if rows:
        lines.extend(format_transition(row) for row in rows)
    else:
        lines.append("  none")
    warnings = [str(item) for item in _list(payload.get("warnings"))]
    missing = [str(item) for item in _list(payload.get("missingCapabilities"))]
    lines.extend(["", "Warnings:"])
    if warnings:
        lines.extend(f"  WARN: {warning}" for warning in warnings)
    else:
        lines.append("  none")
    lines.append(f"Missing capabilities: {', '.join(missing) if missing else 'none'}")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only woodcut bank cycle history diagnostic. Prints to stdout only.")
    parser.add_argument("--from-daemon", action="store_true", help="Read current live daemon memory/status.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--tail", type=int, default=20)
    parser.add_argument("--transitions-only", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.from_daemon:
        payload = {
            "schema": SCHEMA,
            "source": "not_requested",
            "daemonReachable": False,
            "status": "FAIL",
            "current": {},
            "cycleHistory": [],
            "warnings": ["pass --from-daemon to read live daemon cycle history"],
            "missingCapabilities": ["daemon.status"],
        }
        print(json.dumps(payload, indent=2) if args.json else format_human(payload), end="")
        return 1
    try:
        payload = build_from_daemon(
            fetch_json(daemon_status_url(args.daemon_url), timeout=args.timeout),
            tail=max(0, int(args.tail)),
            transitions_only=bool(args.transitions_only),
        )
        code = 0
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        payload = unavailable_payload(f"{type(error).__name__}: {error}")
        code = 1
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_human(payload), end="")
    return code if payload.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

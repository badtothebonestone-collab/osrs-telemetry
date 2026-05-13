from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

import task_policy


def parse_bool(value: str) -> bool:
    lowered = str(value).strip().lower()
    if lowered in {"true", "1", "yes", "on"}:
        return True
    if lowered in {"false", "0", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def control_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/control"


def request_json(url: str, *, payload: dict[str, Any] | None = None, timeout: float = 2.0) -> dict[str, Any]:
    if payload is None:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8", errors="replace"))
    else:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8", errors="replace"))
    return decoded if isinstance(decoded, dict) else {}


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if args.set_policy:
        payload["taskPolicy"] = args.set_policy
    if args.goal_count is not None:
        payload["goalCount"] = args.goal_count
    if args.observe_only is not None:
        payload["observeOnly"] = args.observe_only
    if args.reset_brain_state:
        payload["resetBrainState"] = True
    return payload


def print_human(payload: dict[str, Any]) -> None:
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    print(f"status: {payload.get('status', 'unknown')}")
    print(f"task policy: {state.get('taskPolicy', 'unknown')}")
    print(f"goal count: {state.get('goalCount')}")
    print(f"observe only: {state.get('observeOnly')}")
    print(f"brain enabled: {state.get('brainEnabled')}")
    print(f"overlay: {state.get('overlayMode')} backups={state.get('overlayBackupCandidates')}")
    rejected = payload.get("rejectedFields") or []
    if rejected:
        print("rejected fields: " + ", ".join(str(item) for item in rejected))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only runtime control for live_core_daemon.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--get", action="store_true", help="Read current runtime control state.")
    parser.add_argument("--set-policy", choices=task_policy.policy_names())
    parser.add_argument("--goal-count", type=int)
    parser.add_argument("--observe-only", type=parse_bool)
    parser.add_argument("--reset-brain-state", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    url = control_url(args.daemon_url)
    payload = build_payload(args)
    try:
        response = request_json(url, payload=None if args.get or not payload else payload)
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8", errors="replace")
        try:
            response = json.loads(text)
        except json.JSONDecodeError:
            response = {"status": "FAIL", "error": text or str(error)}
        if args.json:
            print(json.dumps(response, indent=2, sort_keys=False))
        else:
            print_human(response if isinstance(response, dict) else {"status": "FAIL"})
        return 1
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        response = {"status": "FAIL", "error": str(error), "noActionEmitted": True}
        if args.json:
            print(json.dumps(response, indent=2, sort_keys=False))
        else:
            print_human(response)
        return 1

    if args.json:
        print(json.dumps(response, indent=2, sort_keys=False))
    else:
        print_human(response)
    return 0 if response.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

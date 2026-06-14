#!/usr/bin/env python3
"""Validate recovery milestone JSON responses from stdin."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


FORBIDDEN_FIELD_NAMES = {
    "action",
    "actions",
    "click",
    "command",
    "commands",
    "execute",
    "input",
    "interact",
    "interaction",
    "keyboard",
    "menu",
    "mouse",
    "movement",
    "target",
}

FORBIDDEN_RESPONSE_TEXT = {
    "action",
    "anti-detect",
    "antidetect",
    "click",
    "command",
    "execute",
    "gameplay command",
    "input",
    "interact",
    "interaction",
    "keyboard",
    "menu",
    "mouse",
    "movement",
    "target",
}

REQUIRED_FIELDS = {
    "recovery_state_baseline.v1": {"schema", "status", "warnings", "missingFields", "player", "inventory", "sourceFiles"},
    "context_response.v1": {"schema", "ok", "errors", "warnings", "generatedAtUtc"},
}


def load_payload(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, ["invalid_json"]
    if not isinstance(payload, dict):
        return None, ["json_not_object"]
    return payload, []


def iter_items(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from iter_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_items(child)


def iter_strings(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from iter_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_strings(child)
    elif isinstance(value, str):
        yield value


def validate_payload(payload: dict[str, Any], expected_schema: str) -> list[str]:
    errors: list[str] = []
    schema = payload.get("schema")
    if schema != expected_schema:
        errors.append("schema_mismatch")

    for field in sorted(REQUIRED_FIELDS.get(expected_schema, set())):
        if field not in payload:
            errors.append(f"missing_required_field:{field}")

    if payload.get("status") == "FAIL":
        errors.append("status_fail")

    if expected_schema == "recovery_state_baseline.v1" and payload.get("status") != "PASS":
        errors.append("state_baseline_not_pass")

    if expected_schema == "context_response.v1" and payload.get("ok") is not True:
        errors.append("context_response_not_ok")

    for key, _child in iter_items(payload):
        if str(key).lower() in FORBIDDEN_FIELD_NAMES:
            errors.append("forbidden_field")
            break

    if expected_schema == "context_response.v1":
        for text in iter_strings(payload):
            lowered = text.lower()
            if any(term in lowered for term in FORBIDDEN_RESPONSE_TEXT):
                errors.append("forbidden_response_text")
                break

    return sorted(set(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate recovery milestone JSON response.")
    parser.add_argument("--schema", required=True, choices=sorted(REQUIRED_FIELDS))
    args = parser.parse_args(argv)

    text = sys.stdin.read().strip()
    payload, errors = load_payload(text)
    if payload is not None:
        errors.extend(validate_payload(payload, args.schema))

    if errors:
        print("FAIL " + ",".join(sorted(set(errors))))
        return 1
    print(f"PASS {args.schema}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

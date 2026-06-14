"""Read-only adapter from plugin snapshot payloads to recovered baseline checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import context_boundary
import recovery_diagnostics
from state_baseline import state_baseline_payload


STACK_CONSUMPTION_SCHEMA = "live_payload_stack_consumption.v1"


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _number_value(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _payload_names(snapshot: dict[str, Any]) -> list[str]:
    return sorted(_dict_value(snapshot.get("payloads")).keys())


def _scene_object_count(snapshot: dict[str, Any], baseline: dict[str, Any]) -> int | float | None:
    source = _dict_value(baseline.get("source"))
    scene_delta = _dict_value(_dict_value(snapshot.get("payloads")).get("scene_delta"))
    scene_capture = _dict_value(scene_delta.get("sceneCaptureSummary"))
    for value in (
        source.get("sceneObjectsSeen"),
        source.get("sceneObjectsCaptured"),
        scene_capture.get("sceneObjectsSeen"),
        scene_capture.get("sceneObjectsCaptured"),
    ):
        number = _number_value(value)
        if number is not None:
            return number
    return None


def plugin_snapshot_to_state_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a minimal state-baseline context from a plugin snapshot response."""
    payloads = _dict_value(snapshot.get("payloads"))
    baseline = _dict_value(payloads.get("baseline"))
    inventory = _dict_value(payloads.get("inventory"))
    activity = _dict_value(payloads.get("activity"))
    baseline_context = dict(baseline)
    if inventory and "inventory" not in baseline_context:
        baseline_context["inventory"] = inventory

    status_baseline = {
        "gameState": baseline.get("gameState"),
    }
    status = {
        "schema": "plugin_snapshot_status_adapter.v1",
        "generatedAtUtc": _first_present(snapshot.get("generatedAtUtc"), baseline.get("generatedAtUtc"), baseline.get("timestampUtc")),
        "latestTick": _first_present(snapshot.get("latestTick"), baseline.get("latestTick"), baseline.get("tick")),
        "gameState": baseline.get("gameState"),
        "loggedIn": baseline.get("loggedIn"),
        "baseline": status_baseline,
    }

    warnings = [str(warning) for warning in _list_value(snapshot.get("warnings")) if warning]
    missing_fields: list[str] = []
    if not baseline:
        missing_fields.append("baseline")
    if not status:
        missing_fields.append("status")

    return {
        "status": status,
        "baseline": baseline_context,
        "activity": activity,
        "warnings": warnings,
        "missingFields": missing_fields,
        "sourceFiles": [
            {
                "name": "baseline",
                "path": "plugin-snapshot:/payloads/baseline",
                "exists": bool(baseline),
                "modifiedUtc": snapshot.get("generatedAtUtc"),
                "sizeBytes": None,
            },
            {
                "name": "status",
                "path": "plugin-snapshot:/health",
                "exists": bool(status),
                "modifiedUtc": snapshot.get("generatedAtUtc"),
                "sizeBytes": None,
            },
            {
                "name": "activity",
                "path": "plugin-snapshot:/payloads/activity",
                "exists": bool(activity),
                "modifiedUtc": snapshot.get("generatedAtUtc"),
                "sizeBytes": None,
            },
        ],
    }


def stack_consumption_payload(snapshot: dict[str, Any], *, state_stale_ms: int = 5000) -> dict[str, Any]:
    """Run the recovered read-only R1/R2/R3/R4-style checks over a snapshot."""
    context = plugin_snapshot_to_state_context(snapshot)
    args = SimpleNamespace(state_stale_ms=state_stale_ms)
    state = state_baseline_payload(context, args)
    compact = context_boundary.compact_context_response(
        state,
        {"schema": context_boundary.REQUEST_SCHEMA, "needs": ["state", "player", "inventory", "activity", "liveness", "source"], "responseMode": "compact"},
    )
    diagnostic = recovery_diagnostics.evaluate_context(compact)

    baseline = _dict_value(_dict_value(snapshot.get("payloads")).get("baseline"))
    scene_count = _scene_object_count(snapshot, baseline)
    observation_evidence = {
        "sceneObjectCount": scene_count,
        "baselinePlayerPresent": bool(_dict_value(baseline.get("player"))),
    }
    observation_diagnostic = recovery_diagnostics.evaluate_observation_readiness(compact, observation_evidence)

    return {
        "schema": STACK_CONSUMPTION_SCHEMA,
        "snapshotSchema": snapshot.get("schema"),
        "snapshotStatus": snapshot.get("status"),
        "payloadNames": _payload_names(snapshot),
        "stateBaseline": state,
        "compactContext": compact,
        "diagnostic": diagnostic,
        "observationDiagnostic": observation_diagnostic,
        "consumedByRecoveredStack": diagnostic.get("ok") is True,
        "observationReady": observation_diagnostic.get("ok") is True,
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description="Adapt a plugin snapshot into recovered read-only stack checks.")
    parser.add_argument("--snapshot", required=True, help="Path to plugin_snapshot_response.v1 JSON.")
    parser.add_argument("--out", required=True, help="Path for stack consumption JSON.")
    parser.add_argument("--state-stale-ms", type=int, default=5000)
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    output_path = Path(args.out)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload = stack_consumption_payload(snapshot, state_stale_ms=args.state_stale_ms)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

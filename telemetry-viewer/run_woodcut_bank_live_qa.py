from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any, Callable

import diagnose_bank_operation_context
import diagnose_bank_ui_context
import diagnose_close_bank_context
import diagnose_cycle_history
import diagnose_pathing_context
import diagnose_post_bank_reacquisition_context
import diagnose_resource_return_context
import diagnose_return_to_resource_context
import diagnose_service_context
import diagnose_woodcut_bank_cycle
import run_daily_gauntlet


SCHEMA = "woodcut_bank_live_qa.v1"


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload)
    return decoded if isinstance(decoded, dict) else {}


def post_json(url: str, payload: dict[str, Any], timeout: float = 3.0) -> dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    return decoded if isinstance(decoded, dict) else {}


def daemon_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


def snapshot_endpoint(snapshot_url: str) -> str:
    url = snapshot_url.rstrip("/")
    return url if url.endswith("/snapshot") else url + "/snapshot"


def snapshot_request() -> dict[str, Any]:
    return {
        "schema": "plugin_snapshot_request.v1",
        "needs": ["baseline", "writer_health"],
        "maxAgeTicks": 5,
        "responseMode": "compact",
    }


def context_request() -> dict[str, Any]:
    return run_daily_gauntlet.context_request()


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_strings(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


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


def target_label(target: Any) -> str:
    if not isinstance(target, dict) or not target:
        return "none"
    return str(target.get("label") or target.get("targetName") or target.get("name") or target.get("classId") or "target")


def compact_status(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": payload.get("status"),
        "warnings": list_strings(payload.get("warnings")),
        "missingCapabilities": list_strings(payload.get("missingCapabilities")),
    }


def build_snapshot_summary(snapshot_payload: dict[str, Any], *, reachable: bool, error: str | None = None) -> dict[str, Any]:
    baseline = dict_value(dict_value(snapshot_payload.get("payloads")).get("baseline"))
    player = dict_value(baseline.get("player"))
    game_state = first_present(baseline.get("gameState"), snapshot_payload.get("gameState"))
    return {
        "snapshotReachable": reachable,
        "snapshotStatus": snapshot_payload.get("status") if reachable else "FAIL",
        "loggedIn": game_state == "LOGGED_IN" if game_state is not None else False,
        "gameState": game_state,
        "latestTick": snapshot_payload.get("latestTick"),
        "player": {
            "worldX": player.get("worldX"),
            "worldY": player.get("worldY"),
            "plane": player.get("plane"),
        },
        "error": error,
    }


def build_endpoint_summary(
    *,
    snapshot_payload: dict[str, Any],
    snapshot_reachable: bool,
    daemon_reachable: bool,
    snapshot_error: str | None = None,
    daemon_error: str | None = None,
) -> dict[str, Any]:
    endpoint = build_snapshot_summary(snapshot_payload, reachable=snapshot_reachable, error=snapshot_error)
    endpoint.update(
        {
            "daemonReachable": daemon_reachable,
            "daemonError": daemon_error,
        }
    )
    return endpoint


def cycle_summary(cycle: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": cycle.get("status"),
        "cycleStage": cycle.get("cycleStage"),
        "phase": cycle.get("phase"),
        "activeIntent": cycle.get("activeIntent"),
        "reason": cycle.get("reason"),
        "stableForTicks": cycle.get("currentCycleStageStableForTicks"),
        "previousStage": cycle.get("previousCycleStage"),
        "lastTransitionReason": cycle.get("lastCycleTransitionReason"),
    }


def inventory_resource_summary(cycle: dict[str, Any]) -> dict[str, Any]:
    inventory = dict_value(cycle.get("inventory"))
    bank_operation = dict_value(cycle.get("bankOperation"))
    resource_return = dict_value(cycle.get("resourceReturn"))
    return {
        "inventoryFull": inventory.get("inventoryFull"),
        "freeSlots": inventory.get("freeSlots"),
        "resourceItemsHeld": first_present(bank_operation.get("resourceItemsHeld"), inventory.get("resourceItemsHeld")),
        "resourceMemoryValid": resource_return.get("resourceMemoryValid"),
        "resourceMemoryAgeTicks": resource_return.get("resourceMemoryAgeTicks"),
        "returnDestinationAvailable": resource_return.get("returnDestinationAvailable"),
        "returnDestinationTile": resource_return.get("returnDestinationTile"),
    }


def service_path_summary(cycle: dict[str, Any], pathing_diag: dict[str, Any]) -> dict[str, Any]:
    service = dict_value(cycle.get("service"))
    pathing = dict_value(cycle.get("pathing"))
    return {
        "selectedServiceTarget": service.get("targetName"),
        "serviceReady": service.get("serviceReady"),
        "pathingNeeded": pathing.get("pathingNeeded"),
        "pathCompleted": pathing.get("pathCompleted"),
        "pathSegmentsValid": pathing_diag.get("pathSegmentsValid"),
        "approachQuality": pathing_diag.get("approachQuality"),
        "pathLengthTiles": pathing.get("pathLengthTiles"),
    }


def bank_summary(cycle: dict[str, Any]) -> dict[str, Any]:
    bank = dict_value(cycle.get("bank"))
    bank_operation = dict_value(cycle.get("bankOperation"))
    close_bank = dict_value(cycle.get("closeBank"))
    return {
        "bankOpen": bank.get("bankOpen"),
        "bankReadable": bank.get("bankReadable"),
        "bankPinOpen": bank.get("bankPinOpen"),
        "operationNeeded": bank_operation.get("operationNeeded"),
        "operationType": bank_operation.get("operationType"),
        "bankingComplete": bank_operation.get("bankingComplete"),
        "closeBankNeeded": close_bank.get("closeBankNeeded"),
        "closeBankReady": close_bank.get("closeBankReady"),
    }


def return_summary(cycle: dict[str, Any]) -> dict[str, Any]:
    post_bank = dict_value(cycle.get("postBank"))
    return_context = dict_value(cycle.get("returnToResource"))
    resource_return = dict_value(cycle.get("resourceReturn"))
    return {
        "postBankReason": post_bank.get("reason"),
        "resourceTargetReacquisitionAllowed": post_bank.get("resourceTargetReacquisitionAllowed"),
        "returnToResourceReason": return_context.get("reason"),
        "resourceReturnReason": resource_return.get("reason"),
    }


def overlay_summary(cycle: dict[str, Any]) -> dict[str, Any]:
    overlay = dict_value(cycle.get("overlay"))
    selected = dict_value(overlay.get("selected"))
    return {
        "selected": target_label(selected),
        "markerCount": overlay.get("markerCount"),
        "pathMarkerCount": overlay.get("pathMarkerCount"),
    }


def semantic_deferral_reason(cycle: dict[str, Any], gauntlet_report: dict[str, Any]) -> str | None:
    bank = dict_value(cycle.get("bank"))
    bank_operation = dict_value(cycle.get("bankOperation"))
    post_bank = dict_value(cycle.get("postBank"))
    close_bank = dict_value(cycle.get("closeBank"))
    resource_return = dict_value(cycle.get("resourceReturn"))
    if (
        bank_operation.get("bankingComplete") is True
        and bank.get("bankOpen") is True
        and (
            post_bank.get("reason") == "bank_ui_still_open"
            or post_bank.get("resourceTargetReacquisitionAllowed") is False
            or close_bank.get("closeBankNeeded") is True
        )
    ):
        return "target candidates deferred because bank UI is still open after banking complete"
    if (
        bank_operation.get("bankingComplete") is True
        and bank.get("bankOpen") is False
        and resource_return.get("returnDestinationAvailable") is True
        and resource_return.get("reason") == "using_remembered_resource_area"
    ):
        return "target candidates absent, using remembered resource return destination"
    return run_daily_gauntlet.post_bank_target_reacquisition_deferred_reason(
        dict_value(gauntlet_report.get("daemonStatus")),
        dict_value(gauntlet_report.get("brain")),
        dict_value(gauntlet_report.get("context")),
    )


def expected_target_missing_only(failures: list[str], missing_required: list[str]) -> bool:
    target_domains = {"target.candidates", "target.freshness"}
    if missing_required and any(domain not in target_domains for domain in missing_required):
        return False
    if not failures:
        return bool(missing_required)
    return all("target.candidates" in failure or "target.freshness" in failure or "daily context endpoint returned FAIL" in failure for failure in failures)


def classify_report(
    endpoint: dict[str, Any],
    cycle: dict[str, Any],
    gauntlet_report: dict[str, Any],
) -> tuple[str, list[str], list[str]]:
    warnings: list[str] = []
    failures: list[str] = []
    if endpoint.get("snapshotReachable") is not True:
        failures.append("snapshot endpoint unreachable")
    elif endpoint.get("loggedIn") is not True:
        failures.append(f"game not logged in: {endpoint.get('gameState') or 'unknown'}")
    if endpoint.get("daemonReachable") is not True:
        failures.append("daemon unreachable")

    cycle_warnings = list_strings(cycle.get("warnings"))
    cycle_missing_required = list_strings(cycle.get("missingRequiredContextDomains"))
    cycle_missing = list_strings(cycle.get("missingCapabilities"))
    gauntlet_warnings = list_strings(gauntlet_report.get("warnings"))
    gauntlet_failures = list_strings(gauntlet_report.get("failures"))
    gauntlet_missing_required = list_strings(gauntlet_report.get("missingRequiredContextDomains"))
    deferral = semantic_deferral_reason(cycle, gauntlet_report)
    if deferral and deferral not in warnings:
        warnings.append(deferral)

    hard_cycle_missing = [domain for domain in cycle_missing_required if domain not in {"target.candidates", "target.freshness"}]
    if cycle.get("status") == "FAIL" and (not deferral or hard_cycle_missing):
        failures.append("cycle diagnostic FAIL")
    if hard_cycle_missing:
        failures.extend(f"missing required context domain: {domain}" for domain in hard_cycle_missing)

    hard_gauntlet_failures = gauntlet_failures
    if deferral and expected_target_missing_only(gauntlet_failures, gauntlet_missing_required):
        hard_gauntlet_failures = []
        if deferral not in warnings:
            warnings.append(deferral)
    failures.extend(hard_gauntlet_failures)

    if deferral and expected_target_missing_only([], cycle_missing_required):
        if deferral not in warnings:
            warnings.append(deferral)
    warnings.extend(cycle_warnings)
    warnings.extend(gauntlet_warnings)
    warnings.extend(f"optional capability/context: {item}" for item in cycle_missing if item not in cycle_missing_required)
    warnings = list(dict.fromkeys(warnings))
    failures = list(dict.fromkeys(failures))
    return ("FAIL" if failures else "WARN" if warnings else "PASS", warnings, failures)


def diagnostic_payloads(status: dict[str, Any], *, tail: int) -> dict[str, Any]:
    return {
        "cycle": diagnose_woodcut_bank_cycle.build_from_daemon(status),
        "history": diagnose_cycle_history.build_from_daemon(status, tail=tail),
        "service": diagnose_service_context.build_from_daemon(status),
        "pathing": diagnose_pathing_context.build_from_daemon(status),
        "bankUi": diagnose_bank_ui_context.build_from_daemon(status),
        "bankOperation": diagnose_bank_operation_context.build_from_daemon(status),
        "postBankReacquisition": diagnose_post_bank_reacquisition_context.build_from_daemon(status),
        "closeBank": diagnose_close_bank_context.build_from_daemon(status),
        "returnToResource": diagnose_return_to_resource_context.build_from_daemon(status),
        "resourceReturn": diagnose_resource_return_context.build_from_daemon(status),
    }


def build_gauntlet_summary(
    daemon_health: dict[str, Any],
    daemon_status: dict[str, Any],
    context_payload: dict[str, Any],
    brain_payload: dict[str, Any],
) -> dict[str, Any]:
    evaluation = run_daily_gauntlet.evaluate_daemon_payloads(
        daemon_health,
        daemon_status,
        context_payload,
        brain_payload,
        daily_mode="snapshot-no-files",
    )
    warnings = list_strings(evaluation.get("warnings"))
    failures = list_strings(evaluation.get("failures"))
    transition = run_daily_gauntlet.transition_summary_from(daemon_status, brain_payload)
    return {
        "status": "FAIL" if failures else "WARN" if warnings else "PASS",
        "warnings": warnings,
        "failures": failures,
        "missingRequiredContextDomains": transition.get("missingRequiredContextDomains") or [],
        "optionalMissingContextDomains": transition.get("optionalMissingContextDomains") or [],
        "requiredContextDomains": transition.get("requiredContextDomains") or [],
        "daemonStatus": daemon_status,
        "context": context_payload,
        "brain": brain_payload,
        "transitionSummary": transition,
    }


def build_report(
    args: argparse.Namespace,
    *,
    post_json_func: Callable[[str, dict[str, Any], float], dict[str, Any]] = post_json,
    fetch_json_func: Callable[[str, float], dict[str, Any]] = fetch_json,
    processes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    snapshot_payload: dict[str, Any] = {}
    snapshot_reachable = False
    snapshot_error: str | None = None
    try:
        snapshot_payload = post_json_func(snapshot_endpoint(args.snapshot_url), snapshot_request(), float(args.timeout))
        snapshot_reachable = True
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        snapshot_error = f"{type(error).__name__}: {error}"

    daemon_health: dict[str, Any] = {}
    daemon_status: dict[str, Any] = {}
    context_payload: dict[str, Any] = {}
    brain_payload: dict[str, Any] = {}
    daemon_reachable = False
    daemon_error: str | None = None
    if not getattr(args, "skip_daemon_check", False):
        try:
            daemon_health = fetch_json_func(daemon_url(args.daemon_url, "/health"), float(args.timeout))
            daemon_status = fetch_json_func(daemon_url(args.daemon_url, "/status"), float(args.timeout))
            try:
                context_payload = post_json_func(daemon_url(args.daemon_url, "/context"), context_request(), float(args.timeout))
            except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
                context_payload = {}
            try:
                brain_payload = fetch_json_func(daemon_url(args.daemon_url, "/brain?task=woodcutting"), float(args.timeout))
            except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
                brain_payload = {}
            daemon_reachable = True
        except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
            daemon_error = f"{type(error).__name__}: {error}"
    else:
        daemon_reachable = True

    endpoint = build_endpoint_summary(
        snapshot_payload=snapshot_payload,
        snapshot_reachable=snapshot_reachable,
        daemon_reachable=daemon_reachable,
        snapshot_error=snapshot_error,
        daemon_error=daemon_error,
    )

    diagnostics: dict[str, Any] = {}
    if daemon_status:
        diagnostics = diagnostic_payloads(daemon_status, tail=max(0, int(args.tail)))
    else:
        diagnostics = {"cycle": diagnose_woodcut_bank_cycle.unavailable_payload(daemon_error or "daemon status unavailable"), "history": {}}
    cycle = dict_value(diagnostics.get("cycle"))
    history = dict_value(diagnostics.get("history"))
    process_report = run_daily_gauntlet.detect_process_conflicts(processes if processes is not None else run_daily_gauntlet.list_processes())
    gauntlet_report = (
        build_gauntlet_summary(daemon_health, daemon_status, context_payload, brain_payload)
        if daemon_reachable and daemon_status
        else {"status": "FAIL", "warnings": [], "failures": ["daemon unavailable"], "missingRequiredContextDomains": []}
    )
    if processes is not None:
        gauntlet_report["processes"] = process_report

    status, warnings, failures = classify_report(endpoint, cycle, gauntlet_report)
    warnings.extend(process_report.get("warnings") or [])
    warnings = list(dict.fromkeys(warnings))
    status = "FAIL" if failures else "WARN" if warnings else status
    return {
        "schema": SCHEMA,
        "status": status,
        "endpoint": endpoint,
        "cycle": cycle_summary(cycle),
        "inventoryResource": inventory_resource_summary(cycle),
        "servicePath": service_path_summary(cycle, dict_value(diagnostics.get("pathing"))),
        "bank": bank_summary(cycle),
        "return": return_summary(cycle),
        "overlay": overlay_summary(cycle),
        "gauntlet": {
            "status": gauntlet_report.get("status"),
            "warnings": list_strings(gauntlet_report.get("warnings")),
            "failures": list_strings(gauntlet_report.get("failures")),
        },
        "diagnostics": {key: compact_status(value) for key, value in diagnostics.items() if isinstance(value, dict) and key != "history"},
        "historyTail": list(dict_value(history).get("cycleHistory") or []),
        "warnings": warnings,
        "failures": failures,
        "noActionEmitted": True,
    }


def format_history_tail(rows: list[Any]) -> list[str]:
    if not rows:
        return ["  none"]
    lines: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
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
        lines.append(text)
    return lines or ["  none"]


def format_human(report: dict[str, Any]) -> str:
    endpoint = dict_value(report.get("endpoint"))
    cycle = dict_value(report.get("cycle"))
    inventory = dict_value(report.get("inventoryResource"))
    service_path = dict_value(report.get("servicePath"))
    bank = dict_value(report.get("bank"))
    return_summary_payload = dict_value(report.get("return"))
    overlay = dict_value(report.get("overlay"))
    gauntlet = dict_value(report.get("gauntlet"))
    lines = [
        f"WOODCUT BANK LIVE QA - {report.get('status') or 'UNKNOWN'}",
        "",
        "Endpoint:",
        f"  Snapshot reachable: {bool_label(endpoint.get('snapshotReachable'))}",
        f"  Logged in: {bool_label(endpoint.get('loggedIn'))} ({endpoint.get('gameState') or 'unknown'})",
        f"  Daemon reachable: {bool_label(endpoint.get('daemonReachable'))}",
        "",
        "Cycle:",
        f"  Stage: {cycle.get('cycleStage') or 'unknown'}",
        f"  Phase: {cycle.get('phase') or 'unknown'}",
        f"  Active intent: {cycle.get('activeIntent') or 'unknown'}",
        f"  Reason: {cycle.get('reason') or 'unknown'}",
        f"  Stable for ticks: {value_label(cycle.get('stableForTicks'))}",
        "",
        "Inventory/resource:",
        f"  Inventory full/free: {bool_label(inventory.get('inventoryFull'))} / {value_label(inventory.get('freeSlots'))}",
        f"  Resource held: {value_label(inventory.get('resourceItemsHeld'))}",
        f"  Resource memory valid: {bool_label(inventory.get('resourceMemoryValid'))}",
        f"  Return destination available: {bool_label(inventory.get('returnDestinationAvailable'))}",
        "",
        "Service/path:",
        f"  Selected service target: {service_path.get('selectedServiceTarget') or 'none'}",
        f"  Service ready: {bool_label(service_path.get('serviceReady'))}",
        f"  Pathing needed/completed: {bool_label(service_path.get('pathingNeeded'))} / {bool_label(service_path.get('pathCompleted'))}",
        f"  Path segments valid: {bool_label(service_path.get('pathSegmentsValid'))}",
        f"  Approach quality: {service_path.get('approachQuality') or 'unknown'}",
        "",
        "Bank:",
        f"  Open: {bool_label(bank.get('bankOpen'))}",
        f"  Readable: {bool_label(bank.get('bankReadable'))}",
        f"  Pin open: {bool_label(bank.get('bankPinOpen'))}",
        f"  Operation needed: {bool_label(bank.get('operationNeeded'))} ({bank.get('operationType') or 'unknown'})",
        f"  Banking complete: {bool_label(bank.get('bankingComplete'))}",
        f"  Close needed/ready: {bool_label(bank.get('closeBankNeeded'))} / {bool_label(bank.get('closeBankReady'))}",
        "",
        "Return:",
        f"  Post-bank reason: {return_summary_payload.get('postBankReason') or 'unknown'}",
        f"  Reacquisition allowed: {bool_label(return_summary_payload.get('resourceTargetReacquisitionAllowed'))}",
        f"  Return-to-resource reason: {return_summary_payload.get('returnToResourceReason') or 'unknown'}",
        f"  Resource-return reason: {return_summary_payload.get('resourceReturnReason') or 'unknown'}",
        "",
        "Overlay:",
        f"  Selected: {overlay.get('selected') or 'none'}",
        f"  Markers/path: {value_label(overlay.get('markerCount'))} / {value_label(overlay.get('pathMarkerCount'))}",
        "",
        "Gauntlet:",
        f"  Status: {gauntlet.get('status') or 'unknown'}",
    ]
    gauntlet_warnings = list_strings(gauntlet.get("warnings"))
    if gauntlet_warnings:
        lines.extend(f"  WARN: {warning}" for warning in gauntlet_warnings)
    else:
        lines.append("  Warnings: none")
    lines.extend(["", "Cycle history:"])
    lines.extend(format_history_tail(list(report.get("historyTail") or [])))
    lines.extend(["", "Warnings:"])
    warnings = list_strings(report.get("warnings"))
    failures = list_strings(report.get("failures"))
    if not warnings and not failures:
        lines.append("  none")
    for failure in failures:
        lines.append(f"  FAIL: {failure}")
    for warning in warnings:
        lines.append(f"  WARN: {warning}")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only full woodcut_bank live QA runner. Prints to stdout only.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--snapshot-url", default="http://127.0.0.1:8893")
    parser.add_argument("--latest-session", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--skip-daemon-check", action="store_true")
    parser.add_argument("--tail", type=int, default=20)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    print(json.dumps(report, indent=2, sort_keys=False) if args.json else format_human(report), end="")
    return 1 if report.get("status") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())

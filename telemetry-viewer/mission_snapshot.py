from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "mission_snapshot.v1"
DEFAULT_DAEMON_URL = "http://127.0.0.1:8890"

def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def endpoint_url(daemon_url: str, endpoint: str) -> str:
    return daemon_url.rstrip("/") + "/" + endpoint.lstrip("/")


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8", errors="replace"))
    return decoded if isinstance(decoded, dict) else {}


def nested_dict(value: Any, *keys: str) -> dict[str, Any]:
    for key in keys:
        current = value.get(key) if isinstance(value, dict) else None
        if isinstance(current, dict):
            return current
    return {}


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return None


def compact_progress(progress: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(progress, dict):
        return {}
    return {
        "displayedGoalProgress": progress.get("displayedGoalProgress"),
        "goalCount": progress.get("goalCount"),
        "currentHeldCount": first_present(progress.get("currentHeldCount"), progress.get("heldResourceCount")),
        "baselineHeldCount": progress.get("baselineHeldCount"),
        "source": progress.get("source") or progress.get("progressSource"),
        "complete": progress.get("complete"),
    }


def selected_overlay_marker(status: dict[str, Any], brain: dict[str, Any]) -> dict[str, Any] | None:
    overlay = status.get("overlayDebug") if isinstance(status.get("overlayDebug"), dict) else {}
    markers = overlay.get("markers") if isinstance(overlay.get("markers"), list) else []
    for marker in markers:
        if isinstance(marker, dict) and (marker.get("selected") is True or marker.get("markerType") in {"selected_target", "service_target"}):
            return compact_marker(marker)
    generic = brain.get("genericTaskState") if isinstance(brain.get("genericTaskState"), dict) else {}
    target = generic.get("activeIntentTarget") if isinstance(generic.get("activeIntentTarget"), dict) else None
    return compact_marker(target) if target else None


def compact_marker(marker: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(marker, dict) or not marker:
        return None
    keys = (
        "markerType",
        "label",
        "classId",
        "targetType",
        "targetName",
        "name",
        "id",
        "worldX",
        "worldY",
        "plane",
        "selected",
        "role",
    )
    return {key: marker.get(key) for key in keys if marker.get(key) is not None}


def process_counts_from(status: dict[str, Any]) -> dict[str, Any]:
    processes = status.get("processes") if isinstance(status.get("processes"), dict) else {}
    result = {}
    for key in ("liveCoreDaemonCount", "liveTargetProcessorCount", "contextServiceCount"):
        if key in processes:
            result[key] = processes.get(key)
        elif key in status:
            result[key] = status.get(key)
    return result


def collect_warnings(*payloads: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for item in payload.get("warnings") or []:
            if item:
                warnings.append(str(item))
    return list(dict.fromkeys(warnings))


def build_snapshot(
    health: dict[str, Any],
    status: dict[str, Any],
    control: dict[str, Any],
    *,
    daemon_url: str,
    include_raw: bool = False,
) -> dict[str, Any]:
    health = health if isinstance(health, dict) else {}
    status = status if isinstance(status, dict) else {}
    control = control if isinstance(control, dict) else {}
    brain = status.get("brain") if isinstance(status.get("brain"), dict) else {}
    generic = brain.get("genericTaskState") if isinstance(brain.get("genericTaskState"), dict) else {}
    control_state = control.get("state") if isinstance(control.get("state"), dict) else {}
    service = brain.get("serviceContext") if isinstance(brain.get("serviceContext"), dict) else {}
    process = brain.get("processInventoryContext") if isinstance(brain.get("processInventoryContext"), dict) else {}
    navigation = brain.get("navigationIntentContext") if isinstance(brain.get("navigationIntentContext"), dict) else {}
    context = brain.get("currentContextSummary") if isinstance(brain.get("currentContextSummary"), dict) else {}
    inventory = context.get("inventory") if isinstance(context.get("inventory"), dict) else {}
    progress = compact_progress(brain.get("goalProgress") if isinstance(brain.get("goalProgress"), dict) else status.get("brainProgress"))

    warnings = collect_warnings(health, status, control, brain, service, process, navigation)
    no_action = bool(brain.get("noActionEmitted", control.get("noActionEmitted", True)))

    daily_mode = status.get("dailyMode")
    no_file_daily = bool_value(status.get("noFileDaily"))
    write_debug_live_files = bool_value(status.get("writeDebugLiveFiles"))
    compact_required = bool_value(status.get("compactPacketFilesRequired"))
    if daily_mode == "snapshot-no-files" and no_file_daily is False:
        warnings.append("noFileDaily is false in snapshot-no-files mode")
    if write_debug_live_files is True:
        warnings.append("writeDebugLiveFiles is true in daily mode")
    if daily_mode == "snapshot-no-files" and compact_required is True:
        warnings.append("compact packet files are required in snapshot-no-files mode")

    snapshot_status = "PASS"
    if warnings:
        snapshot_status = "WARN"

    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "status": snapshot_status,
        "generatedAtUtc": utc_now(),
        "daemonUrl": daemon_url,
        "healthStatus": health.get("status"),
        "dailyMode": daily_mode,
        "inputSourceActive": status.get("inputSourceActive"),
        "noFileDaily": no_file_daily,
        "writeDebugLiveFiles": bool(write_debug_live_files),
        "overlayStateWritten": bool(status.get("overlayStateWritten")),
        "activeTask": first_present(control_state.get("activeTask"), brain.get("task"), status.get("brainTask")),
        "missionPreset": first_present(control_state.get("activeMissionPreset"), status.get("missionPreset")),
        "taskPolicy": first_present(control_state.get("taskPolicy"), status.get("brainTaskPolicy"), status.get("taskPolicy")),
        "goalCount": first_present(control_state.get("goalCount"), status.get("brainGoalCount"), progress.get("goalCount")),
        "genericPhase": generic.get("phase") or status.get("genericPhase"),
        "activeIntent": generic.get("activeIntent") or status.get("activeIntent"),
        "progress": progress,
        "inventoryFull": bool_value(inventory.get("inventoryFull")),
        "serviceNeeded": service.get("serviceNeeded", status.get("serviceNeeded")),
        "processNeeded": process.get("processRequired", status.get("processInventoryNeeded")),
        "processType": process.get("processTypeNeeded", status.get("processTypeNeeded")),
        "navigationNeeded": navigation.get("navigationNeeded", status.get("navigationIntentNeeded")),
        "requiredContextDomains": brain.get("requiredContextDomains", status.get("requiredContextDomains", [])),
        "missingRequiredContextDomains": brain.get("missingRequiredContextDomains", status.get("missingRequiredContextDomains", [])),
        "optionalMissingContextDomains": brain.get("optionalMissingContextDomains", status.get("optionalMissingContextDomains", [])),
        "noActionEmitted": no_action,
        "warningCount": len(warnings),
        "topWarnings": warnings[:8],
        "warnings": warnings,
        "processCounts": process_counts_from(status),
        "selectedOverlayMarker": selected_overlay_marker(status, brain),
    }
    if include_raw:
        payload["raw"] = {"health": health, "status": status, "control": control}
    return payload


def unavailable_snapshot(daemon_url: str, error: Exception) -> dict[str, Any]:
    message = f"daemon endpoint unavailable: {type(error).__name__}: {error}"
    return {
        "schema": SCHEMA,
        "status": "FAIL",
        "generatedAtUtc": utc_now(),
        "daemonUrl": daemon_url,
        "healthStatus": "unavailable",
        "warnings": [message],
        "warningCount": 1,
        "topWarnings": [message],
        "noActionEmitted": True,
    }


def fetch_snapshot(daemon_url: str, timeout: float, *, include_raw: bool = False) -> dict[str, Any]:
    health = fetch_json(endpoint_url(daemon_url, "/health"), timeout=timeout)
    status = fetch_json(endpoint_url(daemon_url, "/status"), timeout=timeout)
    control = fetch_json(endpoint_url(daemon_url, "/control"), timeout=timeout)
    return build_snapshot(health, status, control, daemon_url=daemon_url, include_raw=include_raw)


def format_human(payload: dict[str, Any]) -> str:
    progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
    marker = payload.get("selectedOverlayMarker") if isinstance(payload.get("selectedOverlayMarker"), dict) else {}
    lines = [
        "MISSION SNAPSHOT",
        "",
        f"Status: {payload.get('status')}",
        f"Daemon: {payload.get('daemonUrl')}",
        f"Health: {payload.get('healthStatus')}",
        f"Daily mode: {payload.get('dailyMode') or 'unknown'}",
        f"Input source: {payload.get('inputSourceActive') or 'unknown'}",
        f"No-file daily: {str(payload.get('noFileDaily')).lower()}",
        f"Debug writes: {'on' if payload.get('writeDebugLiveFiles') else 'off'}",
        f"Overlay state: {'written' if payload.get('overlayStateWritten') else 'not written'}",
        "",
        f"Task: {payload.get('activeTask') or 'unknown'}",
        f"Mission preset: {payload.get('missionPreset') or 'none'}",
        f"Policy: {payload.get('taskPolicy') or 'unknown'}",
        f"Goal count: {payload.get('goalCount')}",
        f"Phase: {payload.get('genericPhase') or 'unknown'}",
        f"Active intent: {payload.get('activeIntent') or 'unknown'}",
        f"Progress: {progress.get('displayedGoalProgress')} / {progress.get('goalCount')}",
        f"Inventory full: {str(payload.get('inventoryFull')).lower()}",
        f"Service needed: {str(payload.get('serviceNeeded')).lower()}",
        f"Process needed: {str(payload.get('processNeeded')).lower()}",
        f"Process type: {payload.get('processType') or 'none'}",
        f"Navigation needed: {str(payload.get('navigationNeeded')).lower()}",
        f"Required context: {', '.join(payload.get('requiredContextDomains') or []) or 'none'}",
        f"Missing required: {', '.join(payload.get('missingRequiredContextDomains') or []) or 'none'}",
        f"Optional missing: {', '.join(payload.get('optionalMissingContextDomains') or []) or 'none'}",
        f"Selected overlay marker: {marker.get('label') or marker.get('targetName') or marker.get('name') or marker.get('classId') or 'none'}",
        f"noActionEmitted: {str(payload.get('noActionEmitted')).lower()}",
        "",
        f"Warnings ({payload.get('warningCount', 0)}):",
    ]
    warnings = payload.get("topWarnings") if isinstance(payload.get("topWarnings"), list) else []
    if warnings:
        for warning in warnings:
            lines.append(f"  {warning}")
    else:
        lines.append("  none")
    return "\n".join(lines).rstrip() + "\n"


def write_output(path_text: str, payload: dict[str, Any]) -> Path:
    path = Path(path_text).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-shot read-only mission snapshot diagnostic.")
    parser.add_argument("--daemon-url", default=DEFAULT_DAEMON_URL)
    parser.add_argument("--json", action="store_true", help="Print one JSON object to stdout.")
    parser.add_argument("--output", help="Optional explicit path for one JSON snapshot file.")
    parser.add_argument("--include-raw", action="store_true", help="Include raw /health, /status, and /control payloads in the one-shot output.")
    parser.add_argument("--timeout", type=float, default=3.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = fetch_snapshot(args.daemon_url, args.timeout, include_raw=bool(args.include_raw))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        payload = unavailable_snapshot(args.daemon_url, error)

    output_path = write_output(args.output, payload) if args.output else None
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print(format_human(payload), end="")
    if output_path:
        print(f"Wrote mission snapshot: {output_path}")
    return 0 if payload.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

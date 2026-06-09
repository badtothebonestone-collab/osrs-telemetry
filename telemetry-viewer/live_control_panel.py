from __future__ import annotations

import argparse
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox, ttk

from live_context_format import format_context_human
from telemetry_paths import find_newest_session, get_sessions_dir
from input_control.action_proposal import build_action_proposal
from input_control.action_lifecycle import build_lifecycle_diagnostic
from input_control.diagnostics import point_label
import live_config_doctor
import mission_presets
import task_policy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIEWER_DIR = PROJECT_ROOT / "telemetry-viewer"
MAX_LOG_LINES = 1000
READ_ONLY_TEXT = "Read-only telemetry launcher. Starts local tools and shows context without controlling the game."
PROFILES = ("woodcutting", "broad_qa", "navigation_qa", "npc_qa", "ground_item_qa", "ui_qa")
PLUGIN_SNAPSHOT_EXPERIMENTAL_LABEL = "plugin-snapshot"
INPUT_SOURCES = (PLUGIN_SNAPSHOT_EXPERIMENTAL_LABEL,)
LIVENESS_MODES = ("off", "basic", "delta", "full")
WORKFLOW_MODES = ("Daily", "Daily Snapshot No-File", "Visual QA", "Debug Audit", "Plugin Snapshot Experimental")
DAILY_ACTION_LABELS = (
    "Apply Daily Live Preset",
    "Start RuneLite Dev",
    "Start Daily Live Snapshot No-File",
    "Legacy Packet Cleanup Report",
    "Stop All",
    "Config Doctor",
    "Daily Gauntlet",
    "Open Latest Session Folder",
)
ADVANCED_ACTION_LABELS = (
    "Advanced: Legacy File Stack",
    "Advanced: Legacy Live Processor",
    "Advanced: Legacy Context Service",
    "Advanced: Legacy Human Dashboard",
    "Advanced: Plugin-Snapshot Testing EXPERIMENTAL",
    "Advanced: Retired Compact Stream Notice",
    "Advanced: Debug Audit Tools",
    "Advanced: Inspectors",
    "Advanced: Batch Builders",
    "Advanced: Human Dashboard Events",
    "Advanced: Event Timeline",
    "Advanced: Mock Brain Rehearsal",
    "Advanced: Request Context Once",
    "Advanced: Health Check",
    "Advanced: Stop Selected",
    "Advanced: Clear Log",
)
WORKFLOW_PRESETS = {
    "DAILY_LIVE": ("Daily Live", "daily"),
    "DAILY_SNAPSHOT_NO_FILE": ("Daily Snapshot No-File", "snapshot_no_file"),
    "VISUAL_QA": ("Visual QA", "visual_qa"),
    "DEBUG_AUDIT": ("Debug Audit", "debug_audit"),
    "PLUGIN_SNAPSHOT_EXPERIMENTAL": ("Plugin Snapshot Experimental", "plugin_snapshot_experimental"),
}
MISSION_PRESETS = tuple(mission_presets.preset_names())
SESSION_STALE_SECONDS = 15 * 60
COMPACT_PACKET_STALE_SECONDS = 2 * 60
REQUIRED_STREAM_PACKET_TYPES = {"live_baseline_packet.v1", "live_projection_packet.v1"}


@dataclass
class LivePanelOptions:
    profile: str = "woodcutting"
    mode: str = "Daily"
    daily_mode: str = "snapshot-no-files"
    input_source: str = "plugin-snapshot"
    liveness_mode: str = "delta"
    window_ticks: int = 10
    limit: int = 100
    overlay_debug_target_limit: int = 10
    port: int = 8890
    interval: float = 1.0
    goal_count: int | None = 5
    task_policy: str = "woodcutting_bank"
    observe_only: bool = False
    require_compact_packets: bool = False
    no_ui_targets: bool = True
    write_overlay_state: bool = True
    benchmark: bool = True
    summary: bool = True


def python_command(script: str, *args: str) -> list[str]:
    return [sys.executable, script, *args]


def command_preview(command: list[str]) -> str:
    return subprocess.list2cmdline([str(part) for part in command])


def normalize_input_source(value: str) -> str:
    if value == PLUGIN_SNAPSHOT_EXPERIMENTAL_LABEL:
        return "plugin-snapshot"
    return "plugin-snapshot"


def stream_mode_warning(input_source: str) -> str:
    normalized = normalize_input_source(input_source)
    if normalized == "plugin-snapshot":
        return "Plugin snapshot is the live source; live packet archives are retired."
    return ""


def script_supports_flag(script: Path, flag: str) -> bool:
    try:
        return flag in script.read_text(encoding="utf-8")
    except OSError:
        return False


def build_runelite_command() -> list[str]:
    # Legacy dev-panel launcher only. Broad UI/recovery/bot Start Game flows use start_game_command.py.
    if os.name == "nt":
        return ["cmd.exe", "/c", "gradlew.bat", "run"]
    return ["./gradlew", "run"]


def build_check_live_setup_command(require_compact_packets: bool = False) -> list[str]:
    return python_command("telemetry-viewer\\check_live_setup.py", "--latest-session")


def build_inspect_packets_command() -> list[str]:
    return python_command("telemetry-viewer\\maintenance.py", "--live-packets-report")


def doctor_mode_key(label: str) -> str:
    mapping = {
        "Daily": "daily",
        "Normal Live": "daily",
        "Daily Snapshot No-File": "snapshot_no_file",
        "Visual QA": "visual_qa",
        "Debug Audit": "debug_audit",
        "Plugin Snapshot Experimental": "plugin_snapshot_experimental",
    }
    return mapping.get(label, "daily")


def build_config_doctor_command(
    mode: str = "daily",
    *,
    fix_suggestions: bool = True,
    check_processes: bool = False,
) -> list[str]:
    command = python_command("telemetry-viewer\\live_config_doctor.py", "--latest-session", "--mode", mode)
    if fix_suggestions:
        command.append("--fix-suggestions")
    if check_processes:
        command.append("--check-processes")
    return command


def build_daily_gauntlet_command(
    *,
    strict: bool = True,
    check_processes: bool = True,
    daemon_url: str = "http://127.0.0.1:8890",
    daily_mode: str = "snapshot-no-files",
) -> list[str]:
    command = python_command("telemetry-viewer\\run_daily_gauntlet.py", "--latest-session", "--daemon-url", daemon_url)
    command.extend(["--daily-mode", daily_mode])
    if strict:
        command.append("--strict")
    if check_processes:
        command.append("--check-processes")
    return command


def preset_request_body(preset: str) -> dict:
    return {"schema": "telemetry_preset_request.v1", "preset": preset}


def preset_endpoint_url(path: str, *, host: str = "127.0.0.1", port: int = 8893) -> str:
    return f"http://{host}:{int(port)}{path}"


def runtime_control_endpoint_url(port: int, *, host: str = "127.0.0.1") -> str:
    return f"http://{host}:{int(port)}/control"


def daemon_endpoint_url(port: int, path: str, *, host: str = "127.0.0.1") -> str:
    suffix = path if path.startswith("/") else f"/{path}"
    return f"http://{host}:{int(port)}{suffix}"


def build_runtime_control_payload(
    *,
    task_policy: str,
    goal_count: int | str | None,
    observe_only: bool,
    reset_brain_state: bool = False,
    brain_enabled: bool = True,
    overlay_mode: str = "intent",
    overlay_backup_candidates: int | str = 2,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "taskPolicy": str(task_policy or "woodcutting_bank"),
        "observeOnly": bool(observe_only),
        "brainEnabled": bool(brain_enabled),
        "overlayMode": str(overlay_mode or "intent"),
    }
    if goal_count not in (None, ""):
        payload["goalCount"] = max(0, int(goal_count))
    if reset_brain_state:
        payload["resetBrainState"] = True
    payload["overlayBackupCandidates"] = max(0, int(overlay_backup_candidates))
    return payload


def build_mission_preset_payload(
    mission_preset: str,
    *,
    goal_count: int | str | None = 5,
    reset_brain_state: bool = False,
    brain_enabled: bool = True,
    overlay_mode: str = "intent",
    overlay_backup_candidates: int | str = 2,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "missionPreset": str(mission_preset or "observe_only"),
        "brainEnabled": bool(brain_enabled),
        "overlayMode": str(overlay_mode or "intent"),
        "overlayBackupCandidates": max(0, int(overlay_backup_candidates)),
    }
    if goal_count not in (None, ""):
        payload["goalCount"] = max(0, int(goal_count))
    if reset_brain_state:
        payload["resetBrainState"] = True
    return payload


def build_reset_baseline_payload() -> dict[str, bool]:
    return {"resetBrainState": True}


QUICK_MISSION_PRESETS = {
    "bank": "woodcut_bank",
    "woodcut_bank": "woodcut_bank",
    "firemake": "woodcut_firemake",
    "woodcut_firemake": "woodcut_firemake",
    "drop": "woodcut_drop",
    "woodcut_drop": "woodcut_drop",
    "combat": "combat_default",
    "combat_default": "combat_default",
    "observe": "observe_only",
    "observe_only": "observe_only",
}


def build_quick_policy_payload(name: str, *, goal_count: int | str | None = 5) -> dict[str, Any]:
    key = str(name or "").strip().lower().replace(" ", "_").replace("-", "_")
    preset_name = QUICK_MISSION_PRESETS.get(key, "observe_only")
    return build_mission_preset_payload(
        preset_name,
        goal_count=goal_count,
        brain_enabled=True,
        overlay_mode="intent",
        overlay_backup_candidates=2,
    )


def request_runtime_control(port: int, payload: dict[str, Any] | None = None, *, timeout: float = 1.5) -> dict:
    url = runtime_control_endpoint_url(port)
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


def request_daemon_get(port: int, path: str, *, timeout: float = 1.0) -> dict:
    with urllib.request.urlopen(daemon_endpoint_url(port, path), timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8", errors="replace"))
    return decoded if isinstance(decoded, dict) else {}


def _bool_label(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _compact_target_label(target: dict | None) -> str | None:
    if not isinstance(target, dict) or not target:
        return None
    name = target.get("targetName") or target.get("name") or target.get("classId") or target.get("targetType") or "target"
    target_id = target.get("id")
    return f"{name} {target_id}" if target_id is not None else str(name)


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _value_or_unknown(value: Any) -> Any:
    return value if value is not None else "unknown"


def _action_proposal_summary(status: dict[str, Any]) -> dict[str, Any]:
    try:
        proposal = build_action_proposal(status).to_dict()
    except Exception as error:  # noqa: BLE001
        return {
            "actionProposalStatus": "WARN",
            "proposedAction": "unknown",
            "actionTarget": "none",
            "actionConfidence": "unknown",
            "actionReason": f"proposal unavailable: {type(error).__name__}",
            "actionClickPoint": "none",
            "actionMovementProfile": "linear_debug",
            "lastExecutionResult": "unknown",
        }
    return {
        "actionProposalStatus": proposal.get("status") or "unknown",
        "proposedAction": proposal.get("proposedAction") or "none",
        "actionTarget": proposal.get("targetName") or "none",
        "actionConfidence": _value_or_unknown(proposal.get("confidence")),
        "actionReason": proposal.get("reason") or "unknown",
        "actionClickPoint": point_label(proposal.get("suggestedClickPoint")),
        "actionMovementProfile": status.get("inputControlMovementProfile") or "linear_debug",
        "lastExecutionResult": status.get("lastInputControlStatus") or "unknown",
    }


def _action_lifecycle_summary(status: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = build_lifecycle_diagnostic(status)
    except Exception as error:  # noqa: BLE001
        return {
            "lifecycleState": "unknown",
            "lifecycleLastAction": "none",
            "lifecycleCooldown": "unknown",
            "lifecycleVerification": "unknown",
            "lifecycleAttempts": "unknown",
            "lifecycleReason": f"lifecycle unavailable: {type(error).__name__}",
            "lifecycleWaitingFor": "unknown",
            "lifecycleObservedSignals": "none",
            "lifecycleElapsed": "unknown",
            "lifecycleResultOutcome": "unknown",
            "lifecycleNextActionAllowed": "unknown",
        }
    lifecycle = payload.get("lifecycleState") if isinstance(payload.get("lifecycleState"), dict) else {}
    observed = payload.get("observedResult") if isinstance(payload.get("observedResult"), dict) else {}
    cooldown = payload.get("cooldown") if isinstance(payload.get("cooldown"), dict) else {}
    return {
        "lifecycleState": lifecycle.get("currentState") or "unknown",
        "lifecycleLastAction": payload.get("lastAction") or lifecycle.get("lastAction") or "none",
        "lifecycleCooldown": cooldown.get("cooldownUntilUtc") or cooldown.get("cooldownUntilTick") or "none",
        "lifecycleVerification": observed.get("verificationStatus") or "unknown",
        "lifecycleAttempts": payload.get("attempts") if payload.get("attempts") is not None else lifecycle.get("attempts", "unknown"),
        "lifecycleReason": payload.get("reason") or lifecycle.get("reason") or "unknown",
        "lifecycleWaitingFor": payload.get("expectedSignal") or lifecycle.get("expectedSignal") or observed.get("expectedSignal") or "unknown",
        "lifecycleObservedSignals": ", ".join(str(item) for item in (payload.get("observedSignals") or lifecycle.get("observedSignals") or observed.get("observedSignals") or [])) or "none",
        "lifecycleElapsed": f"{payload.get('elapsedTicks') if payload.get('elapsedTicks') is not None else lifecycle.get('elapsedTicks')} ticks / {payload.get('elapsedMillis') if payload.get('elapsedMillis') is not None else lifecycle.get('elapsedMillis')} ms",
        "lifecycleResultOutcome": payload.get("resultOutcome") or lifecycle.get("resultOutcome") or observed.get("resultOutcome") or "unknown",
        "lifecycleNextActionAllowed": payload.get("nextActionAllowed") if payload.get("nextActionAllowed") is not None else lifecycle.get("nextActionAllowed", "unknown"),
    }


def _compact_target_from(*targets: Any) -> str:
    for candidate in targets:
        label = _compact_target_label(candidate if isinstance(candidate, dict) else None)
        if label:
            return label
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return "none"


def _target_name_from(*targets: Any) -> str:
    for candidate in targets:
        if isinstance(candidate, dict) and candidate:
            value = candidate.get("targetName") or candidate.get("name") or candidate.get("classId") or candidate.get("targetType")
            if value:
                return str(value)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return "none"


def _collect_string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _warning_count(status: dict[str, Any], brain: dict[str, Any], contexts: list[dict[str, Any]]) -> int:
    warnings: list[str] = []
    for source in (status.get("warnings"), brain.get("warnings")):
        warnings.extend(_collect_string_list(source))
    for context in contexts:
        warnings.extend(_collect_string_list(context.get("warnings")))
    return len(list(dict.fromkeys(warnings)))


def _missing_capability_count(status: dict[str, Any], brain: dict[str, Any], contexts: list[dict[str, Any]]) -> int:
    missing: list[str] = []
    for key in ("missingRequiredContextDomains", "optionalMissingContextDomains"):
        missing.extend(_collect_string_list(status.get(key)))
        missing.extend(_collect_string_list(brain.get(key)))
    for context in contexts:
        missing.extend(_collect_string_list(context.get("missingCapabilities")))
    for key in (
        "bankUiMissingCapabilities",
        "bankOperationMissingCapabilities",
        "closeBankMissingCapabilities",
        "postBankReacquisitionMissingCapabilities",
        "returnToResourceMissingCapabilities",
        "resourceReturnMissingCapabilities",
    ):
        missing.extend(_collect_string_list(status.get(key)))
    return len(list(dict.fromkeys(missing)))


def _progress_label(brain: dict, status: dict, control_state: dict) -> str:
    progress = brain.get("goalProgress") if isinstance(brain.get("goalProgress"), dict) else {}
    if not progress:
        progress = status.get("brainProgress") if isinstance(status.get("brainProgress"), dict) else {}
    value = (
        progress.get("displayedGoalProgress")
        if progress.get("displayedGoalProgress") is not None
        else progress.get("gainedSinceStart")
    )
    goal = progress.get("goalCount") if progress.get("goalCount") is not None else control_state.get("goalCount")
    if value is None and progress.get("currentHeldCount") is not None:
        value = progress.get("currentHeldCount")
    if value is None and goal is None:
        return "unknown"
    if goal is None:
        return str(value)
    return f"{value}/{goal}"


def build_mission_control_status(
    *,
    health: dict | None,
    status: dict | None,
    control: dict | None,
    error: str | None = None,
    gauntlet_status: str | None = None,
) -> dict[str, Any]:
    if error:
        return {
            "daemonHealth": "FAIL",
            "daemonStatus": "daemon not reachable",
            "dailyMode": "unknown",
            "inputSource": "unknown",
            "activeTask": "unknown",
            "activeMissionPreset": "unknown",
            "taskPolicy": "unknown",
            "goalCount": "unknown",
            "genericPhase": "unknown",
            "activeIntent": "unknown",
            "noActionEmitted": "unknown",
            "progress": "unknown",
            "inventoryFull": "unknown",
            "serviceNeeded": "unknown",
            "processNeeded": "unknown",
            "navigationNeeded": "unknown",
            "selectedOverlayMarker": "none",
            "selectedTargetSummary": "none",
            "cycleStage": "unknown",
            "cycleStableForTicks": "unknown",
            "lastTransitionReason": "unknown",
            "inventoryFreeSlots": "unknown",
            "serviceTarget": "none",
            "serviceReady": "unknown",
            "pathingNeeded": "unknown",
            "pathCompleted": "unknown",
            "bankOpen": "unknown",
            "bankReadable": "unknown",
            "bankPinOpen": "unknown",
            "operationNeeded": "unknown",
            "operationType": "unknown",
            "bankingComplete": "unknown",
            "closeBankNeeded": "unknown",
            "closeBankReady": "unknown",
            "postBankReason": "unknown",
            "returnToResourceReason": "unknown",
            "resourceReturnReason": "unknown",
            "returnDestinationAvailable": "unknown",
            "liveQaStatus": "unknown",
            "actionProposalStatus": "unknown",
            "proposedAction": "unknown",
            "actionTarget": "none",
            "actionConfidence": "unknown",
            "actionReason": "daemon unavailable",
            "actionClickPoint": "none",
            "actionMovementProfile": "linear_debug",
            "lastExecutionResult": "unknown",
            "lifecycleState": "unknown",
            "lifecycleLastAction": "none",
            "lifecycleCooldown": "unknown",
            "lifecycleVerification": "unknown",
            "lifecycleAttempts": "unknown",
            "lifecycleReason": "daemon unavailable",
            "lifecycleWaitingFor": "unknown",
            "lifecycleObservedSignals": "none",
            "lifecycleElapsed": "unknown",
            "lifecycleResultOutcome": "unknown",
            "lifecycleNextActionAllowed": "unknown",
            "latestWarningCount": 0,
            "missingCapabilityCount": 0,
            "noFileStatus": "WARN",
            "policyStatus": "WARN",
            "overlayStatus": "unknown",
            "gauntletStatus": gauntlet_status or "unknown",
            "suggestedNextStep": "daemon not reachable; start Snapshot No-File",
        }
    health = health if isinstance(health, dict) else {}
    status = status if isinstance(status, dict) else {}
    control = control if isinstance(control, dict) else {}
    control_state = control.get("state") if isinstance(control.get("state"), dict) else {}
    brain = status.get("brain") if isinstance(status.get("brain"), dict) else {}
    generic = brain.get("genericTaskState") if isinstance(brain.get("genericTaskState"), dict) else {}
    context = brain.get("currentContextSummary") if isinstance(brain.get("currentContextSummary"), dict) else {}
    inventory_summary = _dict_value(context.get("inventory"))
    inventory_context = _dict_value(brain.get("inventoryContext"))
    raw_inventory = _dict_value(inventory_context.get("inventory"))
    service = _dict_value(brain.get("serviceContext"))
    pathing = _dict_value(brain.get("pathingContext"))
    bank_ui = _dict_value(brain.get("bankUiContext"))
    bank_operation = _dict_value(brain.get("bankOperationContext"))
    close_bank = _dict_value(brain.get("closeBankContext"))
    post_bank = _dict_value(brain.get("postBankReacquisitionContext"))
    return_context = _dict_value(brain.get("returnToResourceContext"))
    resource_return = _dict_value(brain.get("resourceReturnContext"))
    process = _dict_value(brain.get("processInventoryContext"))
    navigation = _dict_value(brain.get("navigationIntentContext"))
    active_target = generic.get("activeIntentTarget") if isinstance(generic.get("activeIntentTarget"), dict) else None
    selected_marker = status.get("stabilizedIntentTargetLabel") or _compact_target_label(active_target) or status.get("stabilizedIntentTarget") or "none"
    contexts = [service, pathing, bank_ui, bank_operation, close_bank, post_bank, return_context, resource_return, process, navigation]
    daily_mode = status.get("dailyMode") or health.get("dailyMode") or "unknown"
    input_source = status.get("inputSourceActive") or health.get("inputSourceActive") or "unknown"
    no_file_ok = daily_mode == "snapshot-no-files" and input_source == "plugin-snapshot" and status.get("noFileDaily") is not False
    policy_name = control_state.get("taskPolicy") or status.get("brainTaskPolicy") or "unknown"
    cycle_history = _dict_value(status.get("cycleHistory"))
    inventory_full = _first_present(
        inventory_context.get("inventoryFull"),
        raw_inventory.get("inventoryFull"),
        inventory_summary.get("inventoryFull"),
        status.get("inventoryFull"),
        status.get("returnInventoryFull"),
    )
    inventory_free_slots = _first_present(
        inventory_context.get("freeSlots"),
        inventory_context.get("inventoryFreeSlots"),
        raw_inventory.get("freeSlots"),
        inventory_summary.get("freeSlots"),
        status.get("inventoryFreeSlots"),
        status.get("returnInventoryFreeSlots"),
        status.get("bankOperationInventoryFreeSlots"),
    )
    service_target = _target_name_from(
        service.get("bestServiceCandidate"),
        service.get("bestServiceTarget"),
        service.get("target"),
        status.get("selectedServiceTargetName"),
    )
    action_summary = _action_proposal_summary(status)
    lifecycle_summary = _action_lifecycle_summary(status)
    mission = {
        "daemonHealth": "PASS" if health.get("liveCoreDaemonActive") else "WARN",
        "daemonStatus": "running" if health.get("liveCoreDaemonActive") else "stopped",
        "dailyMode": daily_mode,
        "inputSource": input_source,
        "activeTask": control_state.get("activeTask") or brain.get("task") or "unknown",
        "activeMissionPreset": control_state.get("activeMissionPreset") or "unknown",
        "taskPolicy": policy_name,
        "goalCount": control_state.get("goalCount") if control_state.get("goalCount") is not None else "observe",
        "genericPhase": generic.get("phase") or status.get("brainPhase") or "unknown",
        "activeIntent": generic.get("activeIntent") or "unknown",
        "noActionEmitted": _bool_label(brain.get("noActionEmitted")),
        "progress": _progress_label(brain, status, control_state),
        "inventoryFull": _bool_label(inventory_full),
        "inventoryFreeSlots": _value_or_unknown(inventory_free_slots),
        "serviceNeeded": _bool_label(service.get("serviceNeeded") if "serviceNeeded" in service else status.get("serviceNeeded")),
        "serviceTarget": service_target,
        "serviceReady": _bool_label(_first_present(service.get("serviceReady"), pathing.get("serviceReady"), status.get("serviceReady"))),
        "pathingNeeded": _bool_label(_first_present(pathing.get("pathingNeeded"), status.get("pathingNeeded"))),
        "pathCompleted": _bool_label(_first_present(pathing.get("pathCompleted"), status.get("pathingCompleted"), status.get("pathCompleted"))),
        "bankOpen": _bool_label(_first_present(bank_ui.get("bankOpen"), close_bank.get("bankOpen"), status.get("bankOpen"))),
        "bankReadable": _bool_label(_first_present(bank_ui.get("bankReadable"), status.get("bankReadable"))),
        "bankPinOpen": _bool_label(_first_present(bank_ui.get("bankPinOpen"), status.get("bankPinOpen"))),
        "operationNeeded": _bool_label(_first_present(bank_operation.get("operationNeeded"), status.get("bankOperationNeeded"))),
        "operationType": _value_or_unknown(_first_present(bank_operation.get("operationType"), status.get("bankOperationType"))),
        "bankingComplete": _bool_label(_first_present(bank_operation.get("bankingComplete"), status.get("bankingComplete"))),
        "closeBankNeeded": _bool_label(_first_present(close_bank.get("closeBankNeeded"), status.get("closeBankNeeded"))),
        "closeBankReady": _bool_label(_first_present(close_bank.get("closeBankReady"), status.get("closeBankReady"))),
        "postBankReason": _value_or_unknown(_first_present(post_bank.get("reason"), status.get("postBankReacquisitionReason"))),
        "returnToResourceReason": _value_or_unknown(_first_present(return_context.get("reason"), status.get("returnToResourceReason"))),
        "resourceReturnReason": _value_or_unknown(_first_present(resource_return.get("reason"), status.get("resourceReturnReason"))),
        "returnDestinationAvailable": _bool_label(_first_present(resource_return.get("returnDestinationAvailable"), status.get("resourceReturnDestinationAvailable"))),
        "processNeeded": _bool_label(process.get("processRequired") if "processRequired" in process else status.get("processInventoryNeeded")),
        "navigationNeeded": _bool_label(navigation.get("navigationNeeded") if "navigationNeeded" in navigation else status.get("navigationIntentNeeded")),
        "selectedOverlayMarker": selected_marker,
        "selectedTargetSummary": selected_marker,
        "cycleStage": _value_or_unknown(_first_present(status.get("currentCycleStage"), cycle_history.get("currentCycleStage"))),
        "cycleStableForTicks": _value_or_unknown(_first_present(status.get("currentCycleStageStableForTicks"), cycle_history.get("currentCycleStageStableForTicks"))),
        "lastTransitionReason": _value_or_unknown(_first_present(status.get("lastCycleTransitionReason"), cycle_history.get("lastCycleTransitionReason"))),
        "liveQaStatus": _value_or_unknown(_first_present(status.get("woodcutBankLiveQaStatus"), status.get("liveQaStatus"), status.get("cycleDiagnosticStatus"))),
        "latestWarningCount": _warning_count(status, brain, contexts),
        "missingCapabilityCount": _missing_capability_count(status, brain, contexts),
        "noFileStatus": "PASS" if no_file_ok else ("WARN" if daily_mode == "snapshot-no-files" else "n/a"),
        "policyStatus": "PASS" if policy_name in task_policy.policy_names() else "WARN",
        "overlayStatus": "PASS" if health.get("overlayStateWritten") or status.get("overlayStateWritten") else "off",
        "gauntletStatus": gauntlet_status or "unknown",
        "suggestedNextStep": "",
    }
    mission.update(action_summary)
    mission.update(lifecycle_summary)
    return mission


def format_mission_control_status(mission: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Daemon: {mission.get('daemonHealth')} - {mission.get('daemonStatus')}",
            f"Daily mode: {mission.get('dailyMode')} | input: {mission.get('inputSource')} | no-file: {mission.get('noFileStatus')}",
            f"Task: {mission.get('activeTask')} | preset: {mission.get('activeMissionPreset')} | policy: {mission.get('taskPolicy')} | goal: {mission.get('goalCount')}",
            "Cycle:",
            f"  Stage: {mission.get('cycleStage')} | phase: {mission.get('genericPhase')} | intent: {mission.get('activeIntent')}",
            f"  Stable for ticks: {mission.get('cycleStableForTicks')} | last transition: {mission.get('lastTransitionReason')}",
            f"  Selected: {mission.get('selectedTargetSummary')}",
            "Inventory:",
            f"  Full/free: {mission.get('inventoryFull')} / {mission.get('inventoryFreeSlots')} | progress: {mission.get('progress')}",
            f"  Process: {mission.get('processNeeded')} | navigation: {mission.get('navigationNeeded')}",
            "Service / Path:",
            f"  Target: {mission.get('serviceTarget')} | needed: {mission.get('serviceNeeded')} | ready: {mission.get('serviceReady')}",
            f"  Pathing: needed={mission.get('pathingNeeded')} complete={mission.get('pathCompleted')}",
            "Bank:",
            f"  Open/readable/pin: {mission.get('bankOpen')} / {mission.get('bankReadable')} / {mission.get('bankPinOpen')}",
            f"  Operation: needed={mission.get('operationNeeded')} type={mission.get('operationType')} complete={mission.get('bankingComplete')}",
            f"  Close: needed={mission.get('closeBankNeeded')} ready={mission.get('closeBankReady')}",
            "Return:",
            f"  Post-bank: {mission.get('postBankReason')}",
            f"  Return-to-resource: {mission.get('returnToResourceReason')}",
            f"  Resource return: {mission.get('resourceReturnReason')} | destination available={mission.get('returnDestinationAvailable')}",
            "Action Proposal:",
            f"  Action: {mission.get('proposedAction')} | target: {mission.get('actionTarget')} | confidence: {mission.get('actionConfidence')}",
            f"  Reason: {mission.get('actionReason')} | click: {mission.get('actionClickPoint')}",
            f"  Movement: {mission.get('actionMovementProfile')} | last result: {mission.get('lastExecutionResult')}",
            "Action Lifecycle:",
            f"  State: {mission.get('lifecycleState')} | last: {mission.get('lifecycleLastAction')} | verification: {mission.get('lifecycleVerification')}",
            f"  Waiting for: {mission.get('lifecycleWaitingFor')} | signals: {mission.get('lifecycleObservedSignals')}",
            f"  Outcome: {mission.get('lifecycleResultOutcome')} | elapsed: {mission.get('lifecycleElapsed')} | next allowed: {mission.get('lifecycleNextActionAllowed')}",
            f"  Cooldown: {mission.get('lifecycleCooldown')} | attempts: {mission.get('lifecycleAttempts')} | reason: {mission.get('lifecycleReason')}",
            "Health:",
            f"  Overlay: {mission.get('overlayStatus')} | live QA: {mission.get('liveQaStatus')} | gauntlet: {mission.get('gauntletStatus')}",
            f"  Warnings/missing: {mission.get('latestWarningCount')} / {mission.get('missingCapabilityCount')} | noActionEmitted: {mission.get('noActionEmitted')}",
            mission.get("suggestedNextStep") or "",
        ]
    ).strip()


def request_preset_endpoint(path: str, preset: str, *, timeout: float = 1.0) -> dict:
    body = json.dumps(preset_request_body(preset), separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        preset_endpoint_url(path),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=max(0.001, timeout)) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    return payload if isinstance(payload, dict) else {}


def build_live_processor_command(options: LivePanelOptions, *, supports_liveness: bool = True) -> list[str]:
    input_source = "plugin-snapshot"
    command = python_command(
        "telemetry-viewer\\live_target_processor.py",
        "--latest-session",
        "--input-source",
        input_source,
    )
    command.extend(
        [
            "--profile",
            options.profile,
            "--follow",
            "--latency-mode",
            "realtime",
        ]
    )
    if supports_liveness:
        command.extend(["--liveness-mode", options.liveness_mode, "--liveness-budget-ms", "20"])
    command.extend(
        [
            "--no-startup-backfill",
            "--max-new-ticks-per-update",
            "1",
            "--candidate-output-window",
            "latest",
            "--window-ticks",
            str(options.window_ticks),
            "--limit",
            str(options.limit),
            "--overlay-debug-target-limit",
            str(options.overlay_debug_target_limit),
        ]
    )
    if options.no_ui_targets:
        command.append("--no-ui-targets")
    command.extend(["--emit-world-targets", "candidates", "--drain-backlog-on-overrun"])
    if options.summary:
        command.append("--summary")
    if options.benchmark:
        command.append("--benchmark")
    command.extend([
        "--plugin-snapshot-tier",
        "hot",
        "--plugin-snapshot-projection-field-mode",
        "compact",
        "--plugin-snapshot-fallback",
        "none",
    ])
    return command


def build_context_service_command(port: int) -> list[str]:
    return python_command("telemetry-viewer\\context_service.py", "--latest-session", "--port", str(port))


def build_live_core_daemon_command(options: LivePanelOptions) -> list[str]:
    daily_mode = "snapshot-no-files"
    input_source = "plugin-snapshot"
    command = python_command(
        "telemetry-viewer\\live_core_daemon.py",
        "--latest-session",
        "--profile",
        options.profile,
        "--daily-mode",
        daily_mode,
        "--input-source",
        input_source,
        "--context-port",
        str(options.port),
    )
    if options.write_overlay_state:
        command.extend([
            "--write-overlay-state",
            "--overlay-mode",
            "intent",
            "--overlay-backup-candidates",
            "2",
            "--overlay-debug-target-limit",
            str(min(options.overlay_debug_target_limit, 10)),
        ])
    command.extend(["--human-dashboard", "--brain-task", "woodcutting"])
    command.extend(["--task-policy", options.task_policy])
    if options.goal_count is not None:
        command.extend(["--goal-count", str(options.goal_count)])
    if options.summary:
        command.append("--summary")
    if options.benchmark:
        command.append("--benchmark")
    if daily_mode == "snapshot-no-files":
        command.extend(["--plugin-snapshot-tier", "hot"])
    return command


def localhost_port_is_listening(port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def should_poll_daemon_status(context_service_running: bool, live_core_daemon_running: bool, daemon_port_listening: bool) -> bool:
    return bool(context_service_running or live_core_daemon_running or daemon_port_listening)


def build_dashboard_command(interval: float) -> list[str]:
    return python_command(
        "telemetry-viewer\\live_context_query.py",
        "--latest-session",
        "--task",
        "woodcutting",
        "--watch-human",
        "--interval",
        str(interval),
        "--events",
        "5",
    )


def build_dashboard_events_command(interval: float) -> list[str]:
    return python_command(
        "telemetry-viewer\\live_context_query.py",
        "--latest-session",
        "--task",
        "woodcutting",
        "--watch-human",
        "--interval",
        str(interval),
        "--events",
        "10",
    )


def build_event_timeline_command(events: int = 20) -> list[str]:
    return python_command(
        "telemetry-viewer\\live_context_query.py",
        "--latest-session",
        "--events-only",
        "--events",
        str(events),
    )


def build_mock_brain_command(goal_count: int = 5, *, watch: bool = False, interval: float = 1.0) -> list[str]:
    command = python_command(
        "telemetry-viewer\\mock_brain_rehearsal.py",
        "--task",
        "woodcutting",
        "--goal-count",
        str(goal_count),
        "--human",
    )
    if watch:
        command.extend(["--watch", "--interval", str(interval)])
    return command


def build_debug_audit_command(profile: str = "broad_qa") -> list[str]:
    return python_command(
        "telemetry-viewer\\run_target_geometry_pipeline.py",
        "--latest-session",
        "--latest-with-frames",
        "25",
        "--profile",
        profile,
        "--limit",
        "2000",
        "--open-inspector",
    )


def build_inspector_command(session: Path | None) -> list[str]:
    command = python_command("telemetry-viewer\\target_geometry_inspector.py", "--live")
    if session is not None:
        command.extend(["--session", str(session)])
    return command


def build_context_once_command() -> list[str]:
    return python_command("telemetry-viewer\\live_context_query.py", "--latest-session", "--task", "woodcutting", "--human")


def build_context_request_body(max_candidates: int = 1) -> dict:
    return {
        "schema": "context_request.v1",
        "task": "woodcutting",
        "needs": [
            "baseline",
            "best:tree",
            "nearest:tree",
            "inventory",
            "activity",
            "liveness",
            "navigation_readiness",
            "events",
            "diagnostics",
        ],
        "maxCandidates": max_candidates,
        "maxEvents": 5,
        "responseMode": "compact",
    }


def normal_live_options(profile: str = "woodcutting") -> LivePanelOptions:
    return LivePanelOptions(
        profile=profile,
        mode="Daily",
        daily_mode="snapshot-no-files",
        input_source="plugin-snapshot",
        liveness_mode="delta",
        window_ticks=10,
        limit=100,
        overlay_debug_target_limit=10,
        port=8890,
        interval=1.0,
        goal_count=5,
        task_policy="woodcutting_bank",
        require_compact_packets=False,
        no_ui_targets=True,
        write_overlay_state=True,
        benchmark=True,
        summary=True,
    )


def snapshot_no_file_options(profile: str = "woodcutting") -> LivePanelOptions:
    return LivePanelOptions(
        profile=profile,
        mode="Daily Snapshot No-File",
        daily_mode="snapshot-no-files",
        input_source="plugin-snapshot",
        liveness_mode="delta",
        window_ticks=10,
        limit=100,
        overlay_debug_target_limit=10,
        port=8890,
        interval=1.0,
        goal_count=5,
        task_policy="woodcutting_bank",
        require_compact_packets=False,
        no_ui_targets=True,
        write_overlay_state=True,
        benchmark=True,
        summary=True,
    )


def build_normal_live_stack_commands(options: LivePanelOptions, *, supports_liveness: bool = True) -> list[tuple[str, list[str], str]]:
    stack_options = LivePanelOptions(
        profile=options.profile,
        mode="Daily",
        daily_mode="snapshot-no-files",
        input_source="plugin-snapshot",
        liveness_mode=options.liveness_mode or "delta",
        window_ticks=options.window_ticks,
        limit=options.limit,
        overlay_debug_target_limit=min(options.overlay_debug_target_limit, 10),
        port=options.port,
        interval=options.interval,
        goal_count=options.goal_count,
        task_policy=options.task_policy,
        observe_only=options.observe_only,
        require_compact_packets=False,
        no_ui_targets=options.no_ui_targets,
        write_overlay_state=options.write_overlay_state,
        benchmark=options.benchmark,
        summary=options.summary,
    )
    return [
        ("Check Live Setup", build_check_live_setup_command(require_compact_packets=False), "Setup/Packet tools"),
        ("Live Processor", build_live_processor_command(stack_options, supports_liveness=supports_liveness), "Live Processor"),
        ("Context Service", build_context_service_command(stack_options.port), "Context Service"),
        ("Human Dashboard", build_dashboard_command(stack_options.interval), "Dashboard"),
    ]


def safe_load_json(path: Path, previous: dict | None = None) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return previous or {}
        value = json.loads(text)
        return value if isinstance(value, dict) else previous or {}
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return previous or {}


def latest_session_path(sessions_dir: str | None = None) -> Path | None:
    return find_newest_session(get_sessions_dir(sessions_dir))


def _path_age_seconds(path: Path, now: float | None = None) -> float | None:
    try:
        modified = path.stat().st_mtime
    except OSError:
        return None
    return max(0.0, (time.time() if now is None else now) - modified)


def compact_packet_status(session: Path | None, *, now: float | None = None, stale_seconds: int = COMPACT_PACKET_STALE_SECONDS) -> dict:
    if session is None:
        return {"runtimeRemoved": True, "writerActive": False, "available": False, "recent": False, "warning": "No latest session found."}
    packet_dir = session / "live_packets"
    files = sorted(list(packet_dir.glob("live-*.ndjson")) + list(packet_dir.glob("live-*.jsonl"))) if packet_dir.exists() else []
    total_bytes = 0
    ages = []
    for path in files:
        try:
            total_bytes += int(path.stat().st_size)
        except OSError:
            continue
        age = _path_age_seconds(path, now)
        if age is not None:
            ages.append(age)
    age_seconds = min(ages) if ages else None
    warning = "Live packet archive is retired."
    if files:
        warning = f"Legacy live packet archives remain ({len(files)} files, {round(total_bytes / (1024 * 1024), 2)} MB)."
    return {
        "runtimeRemoved": True,
        "writerActive": False,
        "available": False,
        "recent": False,
        "legacyLivePacketFilesPresent": bool(files),
        "legacyLivePacketFileCount": len(files),
        "legacyLivePacketTotalBytes": total_bytes,
        "legacyLivePacketTotalMb": round(total_bytes / (1024 * 1024), 3),
        "warning": warning,
        "indexPath": None,
        "latestSegment": str(files[-1]) if files else None,
        "latestTick": None,
        "latestSequence": None,
        "ageSeconds": age_seconds,
    }


def stale_session_warning(
    session: Path | None,
    *,
    now: float | None = None,
    max_session_age_seconds: int = SESSION_STALE_SECONDS,
    max_packet_age_seconds: int = COMPACT_PACKET_STALE_SECONDS,
) -> str:
    if session is None:
        return "No latest session found. Start RuneLite dev and verify the plugin snapshot endpoint."
    packet = compact_packet_status(session, now=now, stale_seconds=max_packet_age_seconds)
    activity_ages = [
        age
        for age in (
            _path_age_seconds(session, now),
        )
        if isinstance(age, (int, float))
    ]
    session_age = min(activity_ages) if activity_ages else None
    if session_age is not None and session_age > max_session_age_seconds:
        minutes = int(session_age // 60)
        return f"Latest session folder is {minutes} minutes old; confirm this is the intended session."
    return ""


def stream_incomplete_warning(status: dict) -> str:
    return ""


def first_present(*values):
    for value in values:
        if value is not None:
            return value
    return None


def yes_no_unknown(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def compact_file_bridge_warning(snapshot: dict) -> str:
    return ""


def status_snapshot(session: Path | None, previous: dict | None = None) -> dict:
    if session is None:
        return previous or {}
    previous = previous or {}
    live_dir = session / "interaction_geometry" / "live"
    status = safe_load_json(live_dir / "live_status.json", previous.get("status") if isinstance(previous.get("status"), dict) else None)
    performance = safe_load_json(
        live_dir / "live_performance_summary.json",
        previous.get("performance") if isinstance(previous.get("performance"), dict) else None,
    )
    context = safe_load_json(
        live_dir / "live_context_index.json",
        previous.get("context") if isinstance(previous.get("context"), dict) else None,
    )
    overlay = safe_load_json(
        live_dir / "overlay_debug_state.json",
        previous.get("overlayDebug") if isinstance(previous.get("overlayDebug"), dict) else None,
    )
    manifest = safe_load_json(
        session / "manifest.json",
        previous.get("manifest") if isinstance(previous.get("manifest"), dict) else None,
    )
    packet_status = compact_packet_status(session)
    latest_segment = packet_status.get("latestSegment")
    compact_packets_available = bool(packet_status.get("available"))
    compact_packet_recording_enabled = False
    compact_live_packet_files_enabled = False
    compact_live_stream_enabled = False
    compact_live_stream_also_write_files = False
    snapshot = {
        "status": status,
        "performance": performance,
        "context": context,
        "packetIndex": {},
        "overlayDebug": overlay,
        "manifest": manifest,
        "latestTick": status.get("latestTickProcessed") or status.get("lastProcessedTick") or status.get("latestTick") or context.get("latestTick"),
        "inputSourceActive": status.get("inputSourceActive"),
        "candidateCount": status.get("candidateCount"),
        "budgetExceeded": status.get("budgetExceeded"),
        "writeFailures": status.get("writeFailureCount"),
        "compactPacketsAvailable": compact_packets_available,
        "compactPacketsRecent": packet_status.get("recent"),
        "livePacketsRuntimeRemoved": packet_status.get("runtimeRemoved"),
        "livePacketWriterActive": packet_status.get("writerActive"),
        "legacyLivePacketFilesPresent": packet_status.get("legacyLivePacketFilesPresent"),
        "legacyLivePacketFileCount": packet_status.get("legacyLivePacketFileCount"),
        "legacyLivePacketTotalMb": packet_status.get("legacyLivePacketTotalMb"),
        "latestSegment": latest_segment,
        "latestSegmentExists": bool(packet_status.get("available")),
        "recordingMode": status.get("recordingMode") or manifest.get("recordingMode"),
        "compactPacketRecordingEnabled": compact_packet_recording_enabled,
        "compactLivePacketFilesEnabled": compact_live_packet_files_enabled,
        "compactLiveStreamEnabled": compact_live_stream_enabled,
        "compactLiveStreamAlsoWriteFiles": compact_live_stream_also_write_files,
        "rawTickRecordingEnabled": status.get("rawTickRecordingEnabled") if status.get("rawTickRecordingEnabled") is not None else manifest.get("rawTickRecordingEnabled"),
        "frameRecordingEnabled": status.get("frameRecordingEnabled") if status.get("frameRecordingEnabled") is not None else manifest.get("frameRecordingEnabled"),
        "latestEventSummary": overlay.get("latestEventSummary"),
        "latestEventTick": overlay.get("latestEventTick"),
        "compactStreamMissingRequiredTypesForLatestTick": status.get("compactStreamMissingRequiredTypesForLatestTick") or [],
        "streamIncompleteWarning": stream_incomplete_warning(status),
    }
    snapshot["compactFileBridgeWarning"] = compact_file_bridge_warning(snapshot)
    snapshot["compactChecklist"] = {
        "Live packet archive retired": "yes",
        "Live packet writer active": yes_no_unknown(False),
        "Legacy files present": yes_no_unknown(packet_status.get("legacyLivePacketFilesPresent")),
        "Plugin snapshot input": yes_no_unknown(snapshot.get("inputSourceActive") == "plugin-snapshot"),
    }
    return snapshot


class ManagedProcess:
    def __init__(self, name: str, command: list[str], log_name: str, process: subprocess.Popen):
        self.name = name
        self.command = command
        self.log_name = log_name
        self.process = process
        self.started_at = datetime.now()
        self.exit_code: int | None = None

    @property
    def pid(self) -> int | None:
        return self.process.pid

    def running(self) -> bool:
        code = self.process.poll()
        if code is not None:
            self.exit_code = code
            return False
        return True


class LiveControlPanel:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("OSRS Telemetry Live Control Panel")
        self.root.geometry("1180x780")
        self.latest_session: Path | None = latest_session_path()
        self.previous_snapshot: dict = {}
        self.processes: dict[str, ManagedProcess] = {}
        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.context_poll_inflight = False
        self.log_widgets: dict[str, tk.Text] = {}
        self.context_status_var = tk.StringVar(value="context: unknown")
        self.doctor_status_var = tk.StringVar(value="config doctor: unknown")
        self.doctor_warnings_var = tk.StringVar(value="")
        self.session_var = tk.StringVar(value=str(self.latest_session) if self.latest_session else "No session found")
        self.packet_status_var = tk.StringVar(value="live packet archive: retired")
        self.latest_tick_var = tk.StringVar(value="latest tick: unknown")
        self.mode_var = tk.StringVar(value="Daily")
        self.profile_var = tk.StringVar(value="woodcutting")
        self.input_source_var = tk.StringVar(value="plugin-snapshot")
        self.liveness_var = tk.StringVar(value="delta")
        self.window_ticks_var = tk.StringVar(value="10")
        self.limit_var = tk.StringVar(value="100")
        self.port_var = tk.StringVar(value="8890")
        self.interval_var = tk.StringVar(value="1")
        self.goal_count_var = tk.StringVar(value="5")
        self.mission_preset_var = tk.StringVar(value="woodcut_bank")
        self.task_policy_var = tk.StringVar(value="woodcutting_bank")
        self.observe_only_var = tk.BooleanVar(value=False)
        self.require_compact_var = tk.BooleanVar(value=False)
        self.no_ui_targets_var = tk.BooleanVar(value=True)
        self.write_overlay_state_var = tk.BooleanVar(value=True)
        self.benchmark_var = tk.BooleanVar(value=True)
        self.summary_var = tk.BooleanVar(value=True)
        self.open_inspector_var = tk.BooleanVar(value=False)
        self.stream_warning_var = tk.StringVar(value="")
        self.runtime_control_status_var = tk.StringVar(value="runtime control: unknown")
        self.mission_status_var = tk.StringVar(value="Daemon: WARN - daemon not reachable\nstart Snapshot No-File")
        self.gauntlet_status_var = tk.StringVar(value="gauntlet: unknown")
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_latest_session(log=False)
        self.root.after(100, self.process_log_queue)
        self.root.after(1000, self.poll_status)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(outer, text=READ_ONLY_TEXT, foreground="#064e3b").pack(anchor=tk.W, pady=(0, 8))

        top = ttk.Frame(outer)
        top.pack(fill=tk.X)
        session_frame = ttk.LabelFrame(top, text="Session", padding=8)
        session_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Label(session_frame, textvariable=self.session_var, wraplength=760).grid(row=0, column=0, columnspan=4, sticky=tk.W)
        ttk.Button(session_frame, text="Refresh latest session", command=self.refresh_latest_session).grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        ttk.Button(session_frame, text="Open Session Folder", command=self.open_session_folder).grid(row=1, column=1, sticky=tk.W, pady=(6, 0), padx=(6, 0))
        ttk.Label(session_frame, textvariable=self.packet_status_var).grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(6, 0))
        ttk.Label(session_frame, textvariable=self.latest_tick_var).grid(row=3, column=0, columnspan=2, sticky=tk.W)
        ttk.Label(session_frame, textvariable=self.context_status_var).grid(row=3, column=2, columnspan=2, sticky=tk.W)
        ttk.Label(session_frame, textvariable=self.doctor_status_var).grid(row=4, column=0, columnspan=2, sticky=tk.W)
        ttk.Label(session_frame, textvariable=self.doctor_warnings_var, foreground="#b45309", wraplength=760).grid(row=5, column=0, columnspan=4, sticky=tk.W)
        session_frame.columnconfigure(3, weight=1)

        options = ttk.LabelFrame(outer, text="Advanced Defaults", padding=8)
        self._option_row(options, 0, "Mode", ttk.Combobox(options, textvariable=self.mode_var, values=WORKFLOW_MODES, width=18, state="readonly"))
        self._option_row(options, 1, "Profile", ttk.Combobox(options, textvariable=self.profile_var, values=PROFILES, width=18, state="readonly"))
        input_source_box = ttk.Combobox(options, textvariable=self.input_source_var, values=INPUT_SOURCES, width=24, state="readonly")
        input_source_box.bind("<<ComboboxSelected>>", lambda _event: self.update_stream_warning())
        self._option_row(options, 2, "Input source", input_source_box)
        self._option_row(options, 3, "Liveness", ttk.Combobox(options, textvariable=self.liveness_var, values=LIVENESS_MODES, width=18, state="readonly"))
        self._entry_row(options, 4, "Window ticks", self.window_ticks_var)
        self._entry_row(options, 5, "Limit", self.limit_var)
        self._entry_row(options, 6, "Port", self.port_var)
        self._entry_row(options, 7, "Dashboard interval", self.interval_var)
        ttk.Checkbutton(options, text="Legacy packet report only", variable=self.require_compact_var).grid(row=8, column=0, columnspan=2, sticky=tk.W)
        ttk.Checkbutton(options, text="No UI targets", variable=self.no_ui_targets_var).grid(row=9, column=0, columnspan=2, sticky=tk.W)
        ttk.Checkbutton(options, text="Overlay state", variable=self.write_overlay_state_var).grid(row=10, column=0, sticky=tk.W)
        ttk.Checkbutton(options, text="Summary", variable=self.summary_var).grid(row=10, column=1, sticky=tk.W)
        ttk.Checkbutton(options, text="Benchmark", variable=self.benchmark_var).grid(row=11, column=0, sticky=tk.W)
        ttk.Checkbutton(options, text="Open inspector URL", variable=self.open_inspector_var).grid(row=11, column=1, sticky=tk.W)
        ttk.Label(options, textvariable=self.stream_warning_var, foreground="#b45309", wraplength=260).grid(row=12, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))

        daily_frame = ttk.LabelFrame(outer, text="Mission Control", padding=8)
        daily_frame.pack(fill=tk.X, pady=(8, 4))
        ttk.Label(daily_frame, textvariable=self.mission_status_var, justify=tk.LEFT, wraplength=1080).grid(row=0, column=0, columnspan=4, sticky=tk.W, padx=3, pady=(0, 6))
        daily_buttons = [
            (DAILY_ACTION_LABELS[0], lambda: self.apply_workflow_preset("DAILY_LIVE")),
            (DAILY_ACTION_LABELS[1], self.start_runelite),
            (DAILY_ACTION_LABELS[2], self.start_live_core_daemon),
            (DAILY_ACTION_LABELS[3], self.inspect_packets),
            (DAILY_ACTION_LABELS[4], self.stop_all),
            (DAILY_ACTION_LABELS[5], self.config_doctor),
            (DAILY_ACTION_LABELS[6], self.daily_gauntlet),
            (DAILY_ACTION_LABELS[7], self.open_session_folder),
        ]
        for index, (label, command) in enumerate(daily_buttons):
            ttk.Button(daily_frame, text=label, command=command).grid(row=1 + index // 4, column=index % 4, sticky=tk.EW, padx=3, pady=3)
        for column in range(4):
            daily_frame.columnconfigure(column, weight=1)
        runtime_frame = ttk.LabelFrame(daily_frame, text="Runtime Brain Control", padding=6)
        runtime_frame.grid(row=3, column=0, columnspan=4, sticky=tk.EW, pady=(8, 0))
        ttk.Label(runtime_frame, text="Mission preset").grid(row=0, column=0, sticky=tk.W, padx=(0, 4))
        ttk.Combobox(
            runtime_frame,
            textvariable=self.mission_preset_var,
            values=MISSION_PRESETS,
            width=20,
            state="readonly",
        ).grid(row=0, column=1, sticky=tk.W, padx=(0, 8))
        ttk.Button(runtime_frame, text="Apply Mission Preset", command=self.apply_mission_preset).grid(row=0, column=2, sticky=tk.EW, padx=3)
        ttk.Label(runtime_frame, text="Goal").grid(row=0, column=3, sticky=tk.W, padx=(0, 4))
        ttk.Entry(runtime_frame, textvariable=self.goal_count_var, width=8).grid(row=0, column=4, sticky=tk.W, padx=(0, 8))
        ttk.Checkbutton(runtime_frame, text="Observe only", variable=self.observe_only_var).grid(row=0, column=5, sticky=tk.W, padx=(0, 8))
        ttk.Button(runtime_frame, text="Reset Brain Baseline", command=self.reset_brain_baseline).grid(row=0, column=6, sticky=tk.EW, padx=3)
        ttk.Label(runtime_frame, text="Task policy").grid(row=1, column=0, sticky=tk.W, padx=(0, 4), pady=(6, 0))
        ttk.Combobox(
            runtime_frame,
            textvariable=self.task_policy_var,
            values=tuple(task_policy.policy_names()),
            width=22,
            state="readonly",
        ).grid(row=1, column=1, sticky=tk.W, padx=(0, 8), pady=(6, 0))
        ttk.Button(runtime_frame, text="Apply Runtime Control", command=self.apply_runtime_control).grid(row=1, column=2, sticky=tk.EW, padx=3, pady=(6, 0))
        quick_buttons = [
            ("Woodcut Bank", lambda: self.apply_quick_policy("bank")),
            ("Woodcut Firemake", lambda: self.apply_quick_policy("firemake")),
            ("Woodcut Drop", lambda: self.apply_quick_policy("drop")),
            ("Combat Default", lambda: self.apply_quick_policy("combat")),
            ("Observe Only", lambda: self.apply_quick_policy("observe")),
        ]
        for index, (label, command) in enumerate(quick_buttons):
            ttk.Button(runtime_frame, text=label, command=command).grid(row=2, column=index, sticky=tk.EW, padx=3, pady=(6, 0))
        ttk.Label(runtime_frame, textvariable=self.runtime_control_status_var).grid(row=3, column=0, columnspan=4, sticky=tk.W, pady=(4, 0))
        ttk.Label(runtime_frame, textvariable=self.gauntlet_status_var).grid(row=3, column=4, columnspan=3, sticky=tk.W, pady=(4, 0))
        runtime_frame.columnconfigure(6, weight=1)

        advanced_frame = ttk.LabelFrame(outer, text="Advanced / Legacy / Experimental", padding=8)
        advanced_frame.pack(fill=tk.X, pady=(4, 8))
        advanced_buttons = [
            (ADVANCED_ACTION_LABELS[0], self.start_normal_live_stack),
            (ADVANCED_ACTION_LABELS[1], self.start_live_processor),
            (ADVANCED_ACTION_LABELS[2], self.start_context_service),
            (ADVANCED_ACTION_LABELS[3], self.start_dashboard),
            (ADVANCED_ACTION_LABELS[4], self.start_plugin_snapshot_testing),
            (ADVANCED_ACTION_LABELS[5], self.start_compact_stream_testing),
            (ADVANCED_ACTION_LABELS[6], self.start_debug_audit_tools),
            (ADVANCED_ACTION_LABELS[7], self.start_inspector),
            (ADVANCED_ACTION_LABELS[8], self.start_debug_audit_tools),
            (ADVANCED_ACTION_LABELS[9], self.start_dashboard_events),
            (ADVANCED_ACTION_LABELS[10], self.start_event_timeline),
            (ADVANCED_ACTION_LABELS[11], self.start_mock_brain),
            (ADVANCED_ACTION_LABELS[12], self.context_once),
            (ADVANCED_ACTION_LABELS[13], self.health_check),
            (ADVANCED_ACTION_LABELS[14], self.stop_selected),
            (ADVANCED_ACTION_LABELS[15], self.clear_current_log),
        ]
        for index, (label, command) in enumerate(advanced_buttons):
            ttk.Button(advanced_frame, text=label, command=command).grid(row=index // 5, column=index % 5, sticky=tk.EW, padx=3, pady=3)
        for column in range(5):
            advanced_frame.columnconfigure(column, weight=1)

        options.pack(fill=tk.X, pady=(0, 8))

        middle = ttk.PanedWindow(outer, orient=tk.VERTICAL)
        middle.pack(fill=tk.BOTH, expand=True)

        process_frame = ttk.LabelFrame(middle, text="Processes", padding=6)
        self.process_tree = ttk.Treeview(process_frame, columns=("status", "pid", "started", "exit"), show="tree headings", height=7)
        self.process_tree.heading("#0", text="Name")
        self.process_tree.heading("status", text="Status")
        self.process_tree.heading("pid", text="PID")
        self.process_tree.heading("started", text="Start time")
        self.process_tree.heading("exit", text="Exit code")
        self.process_tree.pack(fill=tk.BOTH, expand=True)
        middle.add(process_frame, weight=1)

        log_frame = ttk.LabelFrame(middle, text="Logs", padding=6)
        self.notebook = ttk.Notebook(log_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        for name in ("Live Daemon", "Live Processor", "Context Service", "Dashboard", "Inspector", "Setup/Packet tools", "RuneLite"):
            self._add_log_tab(name)
        middle.add(log_frame, weight=4)

    def _option_row(self, parent: ttk.Frame, row: int, label: str, widget: ttk.Widget) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        widget.grid(row=row, column=1, sticky=tk.EW, pady=2)

    def _entry_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        ttk.Entry(parent, textvariable=variable, width=20).grid(row=row, column=1, sticky=tk.EW, pady=2)

    def update_stream_warning(self) -> None:
        self.stream_warning_var.set(stream_mode_warning(self.input_source_var.get()))

    def _add_log_tab(self, name: str) -> None:
        frame = ttk.Frame(self.notebook)
        text = tk.Text(frame, height=14, wrap=tk.WORD)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.notebook.add(frame, text=name)
        self.log_widgets[name] = text

    def options(self) -> LivePanelOptions:
        mode = self.mode_var.get()
        return LivePanelOptions(
            profile=self.profile_var.get(),
            mode=mode,
            daily_mode="snapshot-no-files",
            input_source=normalize_input_source(self.input_source_var.get()),
            liveness_mode=self.liveness_var.get(),
            window_ticks=self._int_var(self.window_ticks_var, 10),
            limit=self._int_var(self.limit_var, 100),
            port=self._int_var(self.port_var, 8890),
            interval=self._float_var(self.interval_var, 1.0),
            goal_count=None if self.observe_only_var.get() else self._int_var(self.goal_count_var, 5),
            task_policy=self.task_policy_var.get() or "woodcutting_bank",
            observe_only=bool(self.observe_only_var.get()),
            require_compact_packets=bool(self.require_compact_var.get()),
            no_ui_targets=bool(self.no_ui_targets_var.get()),
            write_overlay_state=bool(self.write_overlay_state_var.get()),
            benchmark=bool(self.benchmark_var.get()),
            summary=bool(self.summary_var.get()),
        )

    def _int_var(self, variable: tk.StringVar, default: int) -> int:
        try:
            return max(0, int(variable.get()))
        except ValueError:
            return default

    def _float_var(self, variable: tk.StringVar, default: float) -> float:
        try:
            return max(0.1, float(variable.get()))
        except ValueError:
            return default

    def refresh_latest_session(self, log: bool = True) -> None:
        self.latest_session = latest_session_path()
        self.session_var.set(str(self.latest_session) if self.latest_session else "No session found")
        if log:
            self.log("Setup/Packet tools", f"Latest session: {self.session_var.get()}")
        self.poll_status()

    def open_session_folder(self) -> None:
        if not self.latest_session:
            self.log("Setup/Packet tools", "No session folder available.")
            return
        try:
            os.startfile(str(self.latest_session))  # type: ignore[attr-defined]
        except OSError as exc:
            self.log("Setup/Packet tools", f"Could not open session folder: {exc}")

    def start_runelite(self) -> None:
        self.start_process("RuneLite Dev", build_runelite_command(), "RuneLite")

    def apply_normal_live_defaults(self) -> None:
        options = normal_live_options(self.profile_var.get())
        self.mode_var.set(options.mode)
        self.input_source_var.set(options.input_source)
        self.liveness_var.set(options.liveness_mode)
        self.window_ticks_var.set(str(options.window_ticks))
        self.limit_var.set(str(options.limit))
        self.port_var.set(str(options.port))
        self.interval_var.set(str(options.interval))
        self.goal_count_var.set("" if options.goal_count is None else str(options.goal_count))
        self.mission_preset_var.set("woodcut_bank")
        self.task_policy_var.set(options.task_policy)
        self.observe_only_var.set(options.observe_only)
        self.require_compact_var.set(options.require_compact_packets)
        self.no_ui_targets_var.set(options.no_ui_targets)
        self.write_overlay_state_var.set(options.write_overlay_state)
        self.summary_var.set(options.summary)
        self.benchmark_var.set(options.benchmark)
        self.update_stream_warning()

    def apply_snapshot_no_file_defaults(self) -> None:
        options = snapshot_no_file_options(self.profile_var.get())
        self.mode_var.set(options.mode)
        self.input_source_var.set(PLUGIN_SNAPSHOT_EXPERIMENTAL_LABEL)
        self.liveness_var.set(options.liveness_mode)
        self.window_ticks_var.set(str(options.window_ticks))
        self.limit_var.set(str(options.limit))
        self.port_var.set(str(options.port))
        self.interval_var.set(str(options.interval))
        self.goal_count_var.set("" if options.goal_count is None else str(options.goal_count))
        self.mission_preset_var.set("woodcut_bank")
        self.task_policy_var.set(options.task_policy)
        self.observe_only_var.set(options.observe_only)
        self.require_compact_var.set(options.require_compact_packets)
        self.no_ui_targets_var.set(options.no_ui_targets)
        self.write_overlay_state_var.set(options.write_overlay_state)
        self.summary_var.set(options.summary)
        self.benchmark_var.set(options.benchmark)
        self.update_stream_warning()

    def apply_control_panel_preset_defaults(self, preset: str) -> None:
        profile = self.profile_var.get() or "woodcutting"
        if preset == "DAILY_LIVE":
            self.apply_normal_live_defaults()
            self.profile_var.set(profile)
            return
        if preset == "DAILY_SNAPSHOT_NO_FILE":
            self.apply_snapshot_no_file_defaults()
            self.profile_var.set(profile)
            self.log("Setup/Packet tools", "Daily Snapshot No-File uses the plugin snapshot endpoint; live packet archives are retired.")
            return
        if preset == "VISUAL_QA":
            self.mode_var.set("Visual QA")
            self.input_source_var.set("plugin-snapshot")
            self.liveness_var.set("delta")
            self.window_ticks_var.set("10")
            self.limit_var.set("100")
            self.port_var.set("8890")
            self.interval_var.set("1.0")
            self.require_compact_var.set(True)
            self.no_ui_targets_var.set(True)
            self.write_overlay_state_var.set(True)
            self.summary_var.set(True)
            self.benchmark_var.set(True)
            self.open_inspector_var.set(True)
            self.update_stream_warning()
            return
        if preset == "DEBUG_AUDIT":
            self.mode_var.set("Debug Audit")
            self.input_source_var.set("plugin-snapshot")
            self.liveness_var.set("full")
            self.window_ticks_var.set("25")
            self.limit_var.set("500")
            self.require_compact_var.set(False)
            self.write_overlay_state_var.set(True)
            self.summary_var.set(True)
            self.benchmark_var.set(True)
            self.open_inspector_var.set(True)
            self.update_stream_warning()
            self.log("Setup/Packet tools", "Debug Audit preset is disk-heavy and should not be used as the realtime daily stack.")
            return
        if preset == "PLUGIN_SNAPSHOT_EXPERIMENTAL":
            self.mode_var.set("Plugin Snapshot Experimental")
            self.input_source_var.set(PLUGIN_SNAPSHOT_EXPERIMENTAL_LABEL)
            self.liveness_var.set("delta")
            self.window_ticks_var.set("10")
            self.limit_var.set("100")
            self.require_compact_var.set(False)
            self.no_ui_targets_var.set(True)
            self.write_overlay_state_var.set(True)
            self.summary_var.set(True)
            self.benchmark_var.set(True)
            self.update_stream_warning()

    def apply_workflow_preset(self, preset: str) -> None:
        self.apply_control_panel_preset_defaults(preset)
        label = WORKFLOW_PRESETS.get(preset, (preset, "daily"))[0]
        self.log("Setup/Packet tools", f"Applied control-panel defaults for {label}.")
        threading.Thread(target=self._preset_preview_worker, args=(preset,), daemon=True).start()

    def _preset_preview_worker(self, preset: str) -> None:
        try:
            preview = request_preset_endpoint("/preset/preview", preset)
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
            self.log_queue.put(("Setup/Packet tools", f"Preset endpoint unavailable: {exc}"))
            self.log_queue.put(("Setup/Packet tools", "Enable the Plugin Snapshot Bridge / Preset Endpoint in RuneLite config, or apply the preset from RuneLite's Workflow Presets section."))
            self.root.after(0, self.config_doctor)
            return
        self.root.after(0, lambda: self._confirm_and_apply_preset(preset, preview))

    def _confirm_and_apply_preset(self, preset: str, preview: dict) -> None:
        changes = [change for change in preview.get("changes", []) if isinstance(change, dict) and change.get("changed")]
        label = WORKFLOW_PRESETS.get(preset, (preset, "daily"))[0]
        if not changes:
            self.log("Setup/Packet tools", f"{label} preset already matches whitelisted telemetry config.")
            self.config_doctor()
            return
        preview_lines = [
            f"{change.get('key')}: {change.get('oldValue')} -> {change.get('newValue')}"
            for change in changes[:12]
        ]
        if len(changes) > 12:
            preview_lines.append(f"...and {len(changes) - 12} more")
        message = "Apply telemetry preset changes?\n\n" + "\n".join(preview_lines)
        if not messagebox.askyesno(f"Apply {label} Preset", message):
            self.log("Setup/Packet tools", f"{label} preset apply cancelled after preview.")
            return
        threading.Thread(target=self._preset_apply_worker, args=(preset,), daemon=True).start()

    def _preset_apply_worker(self, preset: str) -> None:
        try:
            response = request_preset_endpoint("/preset/apply", preset)
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
            self.log_queue.put(("Setup/Packet tools", f"Preset apply failed: {exc}"))
            return
        status = response.get("status", "unknown")
        changed = sum(1 for change in response.get("changes", []) if isinstance(change, dict) and change.get("changed"))
        label = WORKFLOW_PRESETS.get(preset, (preset, "daily"))[0]
        self.log_queue.put(("Setup/Packet tools", f"{label} preset apply: {status}; changed {changed} whitelisted telemetry settings."))
        self.root.after(0, self.config_doctor)

    def start_normal_live_stack(self) -> None:
        self.apply_normal_live_defaults()
        if stale_session_warning(self.latest_session) and not self.is_process_running("RuneLite Dev"):
            self.start_runelite()
        self.log("Setup/Packet tools", "Starting normal live stack with plugin snapshot/WorldModel. Legacy packet archives are ignored.")
        threading.Thread(target=self._normal_live_stack_worker, daemon=True).start()

    def _normal_live_stack_worker(self) -> None:
        session = latest_session_path()
        if session is None:
            self.log_queue.put(("Setup/Packet tools", "No latest session found. Starting sidecars anyway; plugin snapshot health will decide readiness."))
        else:
            self.log_queue.put(("Setup/Packet tools", f"Latest session: {session}"))
        self.root.after(0, self.refresh_latest_session)
        self.root.after(0, self._start_normal_live_sidecars)

    def _start_normal_live_sidecars(self) -> None:
        supports_liveness = script_supports_flag(VIEWER_DIR / "live_target_processor.py", "--liveness-mode")
        for name, command, log_name in build_normal_live_stack_commands(self.options(), supports_liveness=supports_liveness):
            if name == "Context Service" and localhost_port_is_listening(self.options().port):
                self.log("Context Service", f"Port {self.options().port} is already in use on 127.0.0.1. Reusing the existing listener; use Health Check to verify it.")
                continue
            self.start_process(name, command, log_name)

    def restart_live_stack(self) -> None:
        for name in ("Live Core Daemon", "Live Processor", "Context Service", "Human Dashboard", "Human Dashboard Events", "Event Timeline", "Mock Brain Rehearsal"):
            self.stop_process(name)
        self.root.after(1000, self.start_normal_live_stack)

    def start_live_core_daemon(self) -> None:
        self.apply_normal_live_defaults()
        port = self.options().port
        if localhost_port_is_listening(port) and not self.is_process_running("Live Core Daemon"):
            self.log("Live Daemon", f"Port {port} is already in use on 127.0.0.1. Stop the existing context service/daemon or choose another port.")
            return
        self.start_process("Live Core Daemon", build_live_core_daemon_command(self.options()), "Live Daemon")

    def start_snapshot_no_file_daemon(self) -> None:
        self.apply_snapshot_no_file_defaults()
        port = self.options().port
        if localhost_port_is_listening(port) and not self.is_process_running("Live Core Daemon"):
            self.log("Live Daemon", f"Port {port} is already in use on 127.0.0.1. Stop the existing context service/daemon or choose another port.")
            return
        self.log("Live Daemon", "Starting Daily Snapshot No-File mode. It uses the plugin snapshot endpoint; live packet archives are retired.")
        self.start_process("Live Core Daemon", build_live_core_daemon_command(self.options()), "Live Daemon")

    def stop_live_core_daemon(self) -> None:
        self.stop_process("Live Core Daemon")

    def check_live_setup(self) -> None:
        self.start_process("Check Live Setup", build_check_live_setup_command(self.require_compact_var.get()), "Setup/Packet tools")

    def config_doctor(self) -> None:
        command = build_config_doctor_command(doctor_mode_key(self.mode_var.get()), fix_suggestions=True, check_processes=True)
        self.start_process("Config Doctor", command, "Setup/Packet tools")

    def daily_gauntlet(self) -> None:
        options = self.options()
        command = build_daily_gauntlet_command(daemon_url=f"http://127.0.0.1:{options.port}", daily_mode="snapshot-no-files")
        self.gauntlet_status_var.set("gauntlet: running")
        self.start_process("Daily Gauntlet", command, "Setup/Packet tools")

    def build_runtime_control_payload_from_ui(self, *, reset_brain_state: bool = False) -> dict[str, Any]:
        return build_runtime_control_payload(
            task_policy=self.task_policy_var.get() or "woodcutting_bank",
            goal_count=self.goal_count_var.get(),
            observe_only=bool(self.observe_only_var.get()),
            reset_brain_state=reset_brain_state,
            brain_enabled=True,
            overlay_mode="intent",
            overlay_backup_candidates=2,
        )

    def apply_runtime_control(self) -> None:
        try:
            payload = self.build_runtime_control_payload_from_ui(reset_brain_state=False)
        except ValueError as exc:
            self.runtime_control_status_var.set(f"runtime control: invalid input ({exc})")
            return
        threading.Thread(target=self._runtime_control_worker, args=(payload,), daemon=True).start()

    def build_mission_preset_payload_from_ui(self, *, reset_brain_state: bool = False) -> dict[str, Any]:
        return build_mission_preset_payload(
            self.mission_preset_var.get() or "observe_only",
            goal_count=self.goal_count_var.get(),
            reset_brain_state=reset_brain_state,
            brain_enabled=True,
            overlay_mode="intent",
            overlay_backup_candidates=2,
        )

    def apply_mission_preset(self) -> None:
        try:
            payload = self.build_mission_preset_payload_from_ui(reset_brain_state=False)
        except ValueError as exc:
            self.runtime_control_status_var.set(f"runtime control: invalid input ({exc})")
            return
        preset_name = payload.get("missionPreset")
        try:
            fields = mission_presets.runtime_control_fields_for_preset(str(preset_name), goal_count=payload.get("goalCount"))
        except (KeyError, TypeError, ValueError):
            fields = {}
        if fields:
            self.task_policy_var.set(str(fields.get("taskPolicy") or self.task_policy_var.get()))
            self.observe_only_var.set(bool(fields.get("observeOnly")))
        threading.Thread(target=self._runtime_control_worker, args=(payload,), daemon=True).start()

    def reset_brain_baseline(self) -> None:
        threading.Thread(target=self._runtime_control_worker, args=(build_reset_baseline_payload(),), daemon=True).start()

    def apply_quick_policy(self, quick_name: str) -> None:
        try:
            payload = build_quick_policy_payload(quick_name, goal_count=self.goal_count_var.get())
        except ValueError as exc:
            self.runtime_control_status_var.set(f"runtime control: invalid input ({exc})")
            return
        preset_name = str(payload.get("missionPreset") or "observe_only")
        self.mission_preset_var.set(preset_name)
        try:
            fields = mission_presets.runtime_control_fields_for_preset(preset_name, goal_count=payload.get("goalCount"))
        except (KeyError, TypeError, ValueError):
            fields = {}
        if fields:
            self.task_policy_var.set(str(fields.get("taskPolicy") or self.task_policy_var.get()))
            self.observe_only_var.set(bool(fields.get("observeOnly")))
        threading.Thread(target=self._runtime_control_worker, args=(payload,), daemon=True).start()

    def _runtime_control_worker(self, payload: dict[str, Any]) -> None:
        port = self.options().port
        try:
            response = request_runtime_control(port, payload, timeout=2.0)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            self.log_queue.put(("__runtime_control__", f"runtime control: unavailable ({exc})"))
            return
        state = response.get("state") if isinstance(response.get("state"), dict) else {}
        status = response.get("status", "unknown")
        preset = state.get("activeMissionPreset") or "none"
        policy = state.get("taskPolicy", "unknown")
        goal = state.get("goalCount")
        observe = state.get("observeOnly")
        self.log_queue.put(("__runtime_control__", f"runtime control: {status}; preset={preset} policy={policy} goal={goal} observe={observe}"))

    def inspect_packets(self) -> None:
        self.start_process("Inspect Compact Packets", build_inspect_packets_command(), "Setup/Packet tools")

    def start_live_processor(self) -> None:
        supports_liveness = script_supports_flag(VIEWER_DIR / "live_target_processor.py", "--liveness-mode")
        command = build_live_processor_command(self.options(), supports_liveness=supports_liveness)
        self.start_process("Live Processor", command, "Live Processor")

    def start_plugin_snapshot_testing(self) -> None:
        self.mode_var.set("Plugin Snapshot Experimental")
        self.input_source_var.set(PLUGIN_SNAPSHOT_EXPERIMENTAL_LABEL)
        self.require_compact_var.set(False)
        self.update_stream_warning()
        self.log("Live Processor", "Plugin-snapshot testing uses the current live path; live packet archives are retired.")
        self.start_live_processor()

    def start_compact_stream_testing(self) -> None:
        self.log("Live Processor", "Compact-stream testing is retired with the live packet archive. Use plugin-snapshot/Knowledge Fabric queries instead.")

    def start_context_service(self) -> None:
        port = self.options().port
        if localhost_port_is_listening(port):
            self.log("Context Service", f"Port {port} is already in use on 127.0.0.1. If context is unavailable, stop the existing process or choose another port.")
            return
        self.start_process("Context Service", build_context_service_command(port), "Context Service")

    def start_dashboard(self) -> None:
        self.start_process("Human Dashboard", build_dashboard_command(self.options().interval), "Dashboard")

    def start_dashboard_events(self) -> None:
        self.start_process("Human Dashboard Events", build_dashboard_events_command(self.options().interval), "Dashboard")

    def start_event_timeline(self) -> None:
        self.start_process("Event Timeline", build_event_timeline_command(20), "Dashboard")

    def start_mock_brain(self) -> None:
        self.start_process("Mock Brain Rehearsal", build_mock_brain_command(watch=True, interval=self.options().interval), "Dashboard")

    def start_debug_audit_tools(self) -> None:
        self.log("Setup/Packet tools", "Debug audit tools expect DEBUG_RECORDING sessions with raw ticks/frames. Normal live sessions intentionally omit those files.")
        self.start_process("Debug Audit Tools", build_debug_audit_command("broad_qa"), "Setup/Packet tools")

    def start_inspector(self) -> None:
        command = build_inspector_command(self.latest_session)
        self.start_process("Live Inspector", command, "Inspector")
        if self.open_inspector_var.get():
            self.root.after(1000, lambda: webbrowser.open("http://127.0.0.1:8800/"))

    def context_once(self) -> None:
        threading.Thread(target=self._context_once_worker, daemon=True).start()

    def _context_once_worker(self) -> None:
        port = self.options().port
        url = f"http://127.0.0.1:{port}/context"
        body = json.dumps(build_context_request_body()).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        self.log_queue.put(("Setup/Packet tools", f"POST {url}"))
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.log_queue.put(("Setup/Packet tools", format_context_human(payload, compact=True)))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            self.log_queue.put(("Setup/Packet tools", f"Context service request failed: {exc}"))
            self.log_queue.put(("Setup/Packet tools", "Fallback command: " + command_preview(build_context_once_command())))

    def health_check(self) -> None:
        threading.Thread(target=self._health_check_worker, daemon=True).start()

    def _health_check_worker(self) -> None:
        port = self.options().port
        url = f"http://127.0.0.1:{port}/health"
        self.log_queue.put(("Setup/Packet tools", f"GET {url}"))
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                text = response.read().decode("utf-8")
            self.log_queue.put(("Setup/Packet tools", text))
        except (OSError, urllib.error.URLError) as exc:
            self.log_queue.put(("Setup/Packet tools", f"Health check failed: {exc}"))

    def start_process(self, name: str, command: list[str], log_name: str) -> None:
        existing = self.processes.get(name)
        if existing and existing.running():
            self.log(log_name, f"{name} is already running (PID {existing.pid}).")
            return
        self.log(log_name, "> " + command_preview(command))
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            self.log(log_name, f"Failed to start {name}: {exc}")
            return
        entry = ManagedProcess(name, command, log_name, process)
        self.processes[name] = entry
        self.update_process_tree()
        threading.Thread(target=self._read_process_output, args=(entry,), daemon=True).start()

    def _read_process_output(self, entry: ManagedProcess) -> None:
        if entry.process.stdout is not None:
            for line in entry.process.stdout:
                self.log_queue.put((entry.log_name, line.rstrip()))
                if entry.name == "Daily Gauntlet":
                    upper = line.upper()
                    if "FAIL" in upper:
                        self.log_queue.put(("__gauntlet_status__", "gauntlet: FAIL"))
                    elif "WARN" in upper:
                        self.log_queue.put(("__gauntlet_status__", "gauntlet: WARN"))
                    elif "PASS" in upper:
                        self.log_queue.put(("__gauntlet_status__", "gauntlet: PASS"))
        code = entry.process.wait()
        entry.exit_code = code
        if entry.name == "Daily Gauntlet" and code != 0:
            self.log_queue.put(("__gauntlet_status__", f"gauntlet: FAIL exit={code}"))
        self.log_queue.put((entry.log_name, f"{entry.name} exited with code {code}"))
        self.log_queue.put(("__process__", "update"))

    def stop_selected(self) -> None:
        selected = self.process_tree.selection()
        if not selected:
            return
        for item in selected:
            name = self.process_tree.item(item, "text")
            self.stop_process(name)

    def stop_all(self) -> None:
        for name in list(self.processes):
            self.stop_process(name)

    def stop_process(self, name: str) -> None:
        entry = self.processes.get(name)
        if not entry or not entry.running():
            return
        self.log(entry.log_name, f"Stopping {name} (PID {entry.pid})")
        if os.name == "nt" and entry.pid:
            try:
                subprocess.run(["taskkill", "/PID", str(entry.pid), "/T", "/F"], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                entry.process.terminate()
        else:
            entry.process.terminate()
        self.update_process_tree()

    def clear_current_log(self) -> None:
        tab = self.notebook.select()
        for name, widget in self.log_widgets.items():
            if str(widget.master) == tab:
                widget.delete("1.0", tk.END)
                return

    def log(self, log_name: str, message: str) -> None:
        widget = self.log_widgets.get(log_name)
        if widget is None:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        widget.insert(tk.END, f"[{timestamp}] {message}\n")
        line_count = int(float(widget.index("end-1c")))
        if line_count > MAX_LOG_LINES:
            widget.delete("1.0", f"{line_count - MAX_LOG_LINES + 1}.0")
        widget.see(tk.END)

    def process_log_queue(self) -> None:
        while True:
            try:
                log_name, message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if log_name == "__process__":
                self.update_process_tree()
            elif log_name == "__context_status__":
                self.context_status_var.set(message)
            elif log_name == "__runtime_control__":
                self.runtime_control_status_var.set(message)
                self.log("Live Daemon", message)
            elif log_name == "__mission_status__":
                self.mission_status_var.set(message)
            elif log_name == "__gauntlet_status__":
                self.gauntlet_status_var.set(message)
            else:
                self.log(log_name, message)
        self.root.after(100, self.process_log_queue)

    def update_process_tree(self) -> None:
        existing = set(self.process_tree.get_children())
        for name, entry in self.processes.items():
            running = entry.running()
            values = (
                "running" if running else "stopped",
                entry.pid or "",
                entry.started_at.strftime("%H:%M:%S"),
                "" if entry.exit_code is None else entry.exit_code,
            )
            if name in existing:
                self.process_tree.item(name, text=name, values=values)
            else:
                self.process_tree.insert("", tk.END, iid=name, text=name, values=values)
        for item in list(existing):
            if item not in self.processes:
                self.process_tree.delete(item)

    def poll_status(self) -> None:
        self.previous_snapshot = status_snapshot(self.latest_session, self.previous_snapshot)
        snapshot = self.previous_snapshot
        stale_warning = stale_session_warning(self.latest_session)
        stream_warning = snapshot.get("streamIncompleteWarning") or ""
        file_bridge_warning = snapshot.get("compactFileBridgeWarning") or ""
        checklist = snapshot.get("compactChecklist") or {}
        checklist_text = "; ".join(f"{name}={value}" for name, value in checklist.items())
        self.latest_tick_var.set(f"latest tick: {snapshot.get('latestTick') or 'unknown'}")
        self.packet_status_var.set(
            "live packet archive: retired; "
            f"legacyFiles={'present' if snapshot.get('legacyLivePacketFilesPresent') else 'none'}; "
            f"input={snapshot.get('inputSourceActive') or 'unknown'}; "
            f"candidates={snapshot.get('candidateCount') if snapshot.get('candidateCount') is not None else 'unknown'}; "
            f"budgetExceeded={snapshot.get('budgetExceeded')}; "
            f"writeFailures={snapshot.get('writeFailures')}; "
            f"recording={snapshot.get('recordingMode') or 'unknown'}; "
            f"rawTicks={snapshot.get('rawTickRecordingEnabled')}; "
            f"frames={snapshot.get('frameRecordingEnabled')}; "
            f"event={snapshot.get('latestEventSummary') or 'none'}"
            + (f"; checks: {checklist_text}" if checklist_text else "")
            + (f"; warning={file_bridge_warning}" if file_bridge_warning else "")
            + (f"; warning={stream_warning}" if stream_warning else "")
            + (f"; warning={stale_warning}" if stale_warning else "")
        )
        port = self.options().port
        if should_poll_daemon_status(
            self.is_process_running("Context Service"),
            self.is_process_running("Live Core Daemon"),
            localhost_port_is_listening(port),
        ):
            if not self.context_poll_inflight:
                self.context_poll_inflight = True
                threading.Thread(target=self._context_status_worker, daemon=True).start()
        else:
            self.context_status_var.set("context: service stopped")
        doctor = live_config_doctor.evaluate_live_config(
            self.latest_session,
            mode=doctor_mode_key(self.mode_var.get()),
            context_port=self.options().port,
            check_context_service=True,
        )
        self.doctor_status_var.set(f"config doctor: {doctor.get('status', 'unknown')}")
        top_warnings = doctor.get("topWarnings") or []
        self.doctor_warnings_var.set(" | ".join(str(item) for item in top_warnings[:3]))
        self.update_process_tree()
        self.root.after(2000, self.poll_status)

    def is_process_running(self, name: str) -> bool:
        entry = self.processes.get(name)
        return bool(entry and entry.running())

    def _context_status_worker(self) -> None:
        port = self.options().port
        url = f"http://127.0.0.1:{port}/context"
        body = json.dumps(build_context_request_body()).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            health = request_daemon_get(port, "/health", timeout=0.75)
            status_payload = request_daemon_get(port, "/status", timeout=0.75)
            control = request_runtime_control(port, None, timeout=0.75)
            mission = build_mission_control_status(
                health=health,
                status=status_payload,
                control=control,
                gauntlet_status=self.gauntlet_status_var.get().replace("gauntlet: ", "", 1),
            )
            self.log_queue.put(("__mission_status__", format_mission_control_status(mission)))
            with urllib.request.urlopen(request, timeout=0.75) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.log_queue.put(("__context_status__", f"context: {payload.get('status', 'unknown')} tick={payload.get('latestTick', 'unknown')}"))
            state = control.get("state") if isinstance(control.get("state"), dict) else {}
            if state:
                self.log_queue.put(
                    (
                        "__runtime_control__",
                        f"runtime control: preset={state.get('activeMissionPreset') or 'none'} policy={state.get('taskPolicy')} goal={state.get('goalCount')} observe={state.get('observeOnly')}",
                    )
                )
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            self.log_queue.put(("__context_status__", f"context: unavailable ({exc})"))
            mission = build_mission_control_status(health=None, status=None, control=None, error=str(exc))
            self.log_queue.put(("__mission_status__", format_mission_control_status(mission)))
        finally:
            self.context_poll_inflight = False

    def on_close(self) -> None:
        running = [name for name, entry in self.processes.items() if entry.running()]
        if running:
            answer = messagebox.askyesnocancel("Stop helper processes?", "Stop running helper processes?")
            if answer is None:
                return
            if answer:
                self.stop_all()
        self.root.destroy()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only OSRS telemetry live control panel.")
    parser.add_argument("--auto-start-normal-live", action="store_true", help="Open the panel and start the daily streamlined live daemon.")
    parser.add_argument("--profile", default="woodcutting", choices=PROFILES, help="Default target profile.")
    parser.add_argument(
        "--mode",
        default="normal-live",
        choices=("normal-live", "daily", "daily-snapshot-no-file", "visual-qa", "debug-audit", "plugin-snapshot-experimental"),
        help="Initial workflow mode.",
    )
    return parser.parse_args(argv)


def _mode_label(value: str) -> str:
    mapping = {
        "normal-live": "Daily",
        "daily": "Daily",
        "daily-snapshot-no-file": "Daily Snapshot No-File",
        "visual-qa": "Visual QA",
        "debug-audit": "Debug Audit",
        "plugin-snapshot-experimental": "Plugin Snapshot Experimental",
    }
    return mapping.get(value, "Daily")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = tk.Tk()
    panel = LiveControlPanel(root)
    panel.profile_var.set(args.profile)
    panel.mode_var.set(_mode_label(args.mode))
    if args.mode in {"normal-live", "daily"}:
        panel.apply_normal_live_defaults()
        panel.profile_var.set(args.profile)
    elif args.mode == "daily-snapshot-no-file":
        panel.apply_snapshot_no_file_defaults()
        panel.profile_var.set(args.profile)
    if args.auto_start_normal_live:
        root.after(500, panel.start_live_core_daemon)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import banking_lifecycle
import human_click_profile
import interruption_lifecycle
import combat_damage_summary
import route_demonstration
import route_monitor
import woodcutting_loop_lifecycle
import woodcutting_lifecycle
import task_policy
from input_control import click_planner


TASK_SCRIPT_SCHEMA = "task_script.v1"
TASK_SCRIPT_SPEC_SCHEMA = "task_script_api_spec.v1"
TASK_SCRIPT_VALIDATION_SCHEMA = "task_script_validation.v1"
TASK_SCRIPT_PLAN_SCHEMA = "task_script_plan.v1"
TASK_SCRIPT_EXPLANATION_SCHEMA = "task_script_explanation.v1"
TASK_TEMPLATE_SUGGESTION_SCHEMA = "task_template_suggestion.v1"
TASK_SCRIPT_EVIDENCE_PLAN_SCHEMA = "task_script_evidence_plan.v1"
TASK_RUNTIME_EVIDENCE_SCHEMA = "task_runtime_evidence.v1"
TASK_RUNTIME_EVIDENCE_COMPARISON_SCHEMA = "task_runtime_evidence_comparison.v1"
TASK_FAILURE_CLASSIFICATION_SCHEMA = "task_failure_classification.v1"
TASK_STEP_READINESS_SCHEMA = "task_step_readiness.v1"
TASK_RUN_READINESS_SCHEMA = "task_run_readiness.v1"

CANONICAL_PIPELINE = [
    "action proposal",
    "readiness",
    "hover/menu proof",
    "HumanInputController",
    "ArduinoHIDBackend",
    "input integrity",
    "lifecycle verification",
]

PHASE_AWARE_INPUT_POLICY = {
    "operator_phase": "Computer Use/manual/debug input may create operatorInjectedEvents; these do not fail the script.",
    "pre_live_phase": "STOP_ALL/DISARM/STATUS, reset or rebaseline input integrity, verify Arduino/Raw Input/COM status.",
    "live_action_phase": "All live input must go through HumanInputController -> ArduinoHIDBackend; injected/lower-IL deltas or direct backend bypasses are hard blockers.",
    "post_live_phase": "STOP_ALL/DISARM/STATUS, report injected/lower-IL deltas only for the live action window.",
}

EXTERNAL_KNOWLEDGE_POLICY = {
    "advisoryOnly": True,
    "liveTruth": "RuneLite / 8893 / WorldModel / 8890",
    "cacheFirst": True,
    "explicitRefreshOnly": True,
    "hotExecutorExternalCallsAllowed": False,
    "mustNotOverrideFreshLiveEvidence": True,
}

FAILURE_CLASSIFICATIONS = [
    "code/data truth bug",
    "coordinate_transform_error",
    "arduino_movement_error",
    "target_aimpoint_error",
    "target/hover/menu mismatch",
    "stale liveness/plugin bug",
    "game-state/user-login blocker",
    "external knowledge/cache miss",
    "operator-phase injected-input noise",
    "runtime file/disk issue",
]

BOUNDED_OPERATOR_REQUESTS = {
    "collect": "request_bounded_live_step",
    "interact": "request_bounded_live_step",
    "walk_to": "request_bounded_live_step",
    "bank": "request_bounded_live_step",
    "deposit": "request_bounded_live_step",
    "close_bank": "request_bounded_live_step",
    "return_to_resource": "request_bounded_live_step",
    "wait_for_evidence": "request_watcher_step",
    "recover_loaded_scene": "request_liveness_recovery",
    "repeat_until": "request_watcher_step",
}

RUNTIME_EVIDENCE_VARIABLES: dict[str, dict[str, Any]] = {
    "inventory": {
        "description": "Inventory occupancy and item set.",
        "liveSources": ["brain.inventoryContext", "actionNeed.inventoryFreeSlots", "list_seen_inventory_items"],
        "proves": ["free slot changes", "held item changes", "full-inventory gates"],
    },
    "resourceCount": {
        "description": "Held matching resource count for the active task.",
        "liveSources": ["brain.goalProgress.heldResourceCount", "actionNeed.resourceCount", "inventorySummary.resourceCount"],
        "proves": ["collection progress", "deposit completion"],
    },
    "bankOpen": {
        "description": "Bank interface open/closed state.",
        "liveSources": ["brain.bankUiContext.bankOpen", "status.bankOpen", "genericTaskState.bankOpen"],
        "proves": ["service opened", "bank closed"],
    },
    "bankState": {
        "description": "Compact active bank/deposit-box interface state.",
        "liveSources": ["context_service needs=bank_state", "brain.bankUiContext", "banking_lifecycle.bank"],
        "proves": ["bank-like interface open", "bank container availability", "direct banking UI evidence"],
    },
    "bankingLifecycle": {
        "description": "Compact banking lifecycle status, confidence, and warnings.",
        "liveSources": ["context_service needs=banking_lifecycle", "banking_lifecycle.json"],
        "proves": ["banking phase", "direct vs inferred banking confidence"],
    },
    "inventoryDelta": {
        "description": "Inventory before/after item and free-slot changes.",
        "liveSources": ["context_service needs=inventory_delta", "banking_lifecycle.inventory"],
        "proves": ["items deposited or withdrawn from inventory perspective"],
    },
    "depositResult": {
        "description": "Script-friendly deposit completion summary.",
        "liveSources": ["context_service needs=deposit_result", "banking_lifecycle.deposit"],
        "proves": ["deposit completion", "deposited item ids and quantities", "confirmation level"],
    },
    "combatState": {
        "description": "Compact combat state, hostile targeting, hitsplat, and health evidence.",
        "liveSources": ["context_service needs=combat_state", "combat_state live cache", "interruption_lifecycle.combat"],
        "proves": ["combat started", "NPC targeted player", "hitsplats/health events were observed"],
    },
    "interruptionLifecycle": {
        "description": "Compact task interruption status, cause, resume, and missing capability evidence.",
        "liveSources": ["context_service needs=interruption_lifecycle", "interruption_lifecycle.json"],
        "proves": ["task stopped/resumed", "combat/message/stat cause classification", "unknown cause with explicit missing data"],
    },
    "combatDamageSummary": {
        "description": "Compact damage taken/dealt, primary opponent, HP, hitsplat, actor-death, and task-resume evidence.",
        "liveSources": ["context_service needs=combat_damage_summary", "combat_damage_summary.json", "interruption_lifecycle.combatDamageSummary"],
        "proves": ["damage amount totals", "primary opponent", "HP changed", "hostile actor death", "task resumed after combat"],
    },
    "woodcuttingLoopLifecycle": {
        "description": "Compact woodcutting task-loop phase and next expected phase.",
        "liveSources": ["context_service needs=woodcutting_loop", "woodcutting_loop_lifecycle.json"],
        "proves": ["current task phase", "next phase choice", "woodcutting/banking/route/interruption loop progress"],
    },
    "routeDemonstrationGuide": {
        "description": "Demonstrated route guide extracted from successful Record Everything route recordings.",
        "liveSources": ["route_guides/*.route_guide.json", "route_demonstration.py"],
        "proves": ["recorded path order", "reached/skipped guide points", "expected stair/service interaction steps", "camera hints before route interactions"],
    },
    "menuOptionClicked": {
        "description": "Client-accepted click action after a live input.",
        "liveSources": ["clientTickHot.lastMenuOptionClicked", "action_trace.clientTick.lastMenuOptionClickedAfter"],
        "proves": ["the clicked menu action matched the intended action"],
    },
    "hoverTarget": {
        "description": "PostMenuSort hover target/action before a click.",
        "liveSources": ["clientTickHot.postMenuSort", "clientTickHot.hoverMenu", "action_input_visibility.hoverConfirmationEvidence"],
        "proves": ["cursor was over the intended target/action before live click"],
    },
    "location": {
        "description": "Player world tile/plane.",
        "liveSources": ["playerLocation", "baseline.playerLocation", "WorldModel player state"],
        "proves": ["navigation progress", "return-to-resource progress"],
    },
    "routeProgress": {
        "description": "Service route/pathing node, step, and waypoint state.",
        "liveSources": ["brain.serviceRouteContext", "brain.pathingContext", "status.serviceRouteStepStatus"],
        "proves": ["route advance", "route transition", "waypoint progress"],
    },
    "routeMonitor": {
        "description": "Compact route monitor/readiness state.",
        "liveSources": ["context_service needs=route_monitor", "route_monitor_status.json", "route_history_summary.json"],
        "proves": ["route state", "current/next route segment", "off-route state", "remaining route progress"],
    },
    "phaseIntent": {
        "description": "Current task phase, cycle stage, active intent, and proposed action.",
        "liveSources": ["brain.genericTaskState", "readiness.actionNeed", "action_proposal"],
        "proves": ["cycle stage transitions", "one-action-then-wait lifecycle"],
    },
    "humanClickProfile": {
        "description": "Compact human click/camera profile for target-relative click planning guidance.",
        "liveSources": ["knowledge_base/human_click_profile.json", "context_service needs=human_click_profile"],
        "proves": ["expected click landing variance", "menu-row usage", "camera-before-click frequency"],
    },
    "loadedScene": {
        "description": "Loaded-scene liveness proof.",
        "liveSources": ["readiness.loadedSceneProof", "clientTickHot.gameState", "WorldModel object counts"],
        "proves": ["safe baseline before live action"],
    },
    "inputIntegrity": {
        "description": "Input integrity phase counts and backend bypass status.",
        "liveSources": ["input_integrity_status", "action_input_visibility.input_integrity_status"],
        "proves": ["operator events separated from live action deltas", "directBackendBypassCount remains 0"],
    },
}

PRIMITIVE_RUNTIME_EXPECTATIONS: dict[str, list[dict[str, str]]] = {
    "collect": [
        {"variable": "hoverTarget", "expectedChange": "hover top option/target matches the resource action before click"},
        {"variable": "menuOptionClicked", "expectedChange": "MenuOptionClicked records the expected collect action or resource progress follows"},
        {"variable": "inventory", "expectedChange": "free slots decrease or matching resource item appears"},
        {"variable": "resourceCount", "expectedChange": "held resource count increases"},
        {"variable": "phaseIntent", "expectedChange": "phase remains collection or waits for result after one action"},
    ],
    "interact": [
        {"variable": "hoverTarget", "expectedChange": "hover/menu evidence matches target and action"},
        {"variable": "menuOptionClicked", "expectedChange": "accepted menu action matches requested interaction"},
        {"variable": "phaseIntent", "expectedChange": "intent advances or waits for post-interaction proof"},
    ],
    "walk_to": [
        {"variable": "location", "expectedChange": "player world tile moves toward the destination"},
        {"variable": "routeProgress", "expectedChange": "route node/step/waypoint state advances"},
        {"variable": "phaseIntent", "expectedChange": "navigation intent remains active until position proof arrives"},
    ],
    "bank": [
        {"variable": "routeProgress", "expectedChange": "route reaches service target or service object is visible"},
        {"variable": "hoverTarget", "expectedChange": "hover/menu evidence matches bank/service action"},
        {"variable": "menuOptionClicked", "expectedChange": "accepted menu action opens the service"},
        {"variable": "bankOpen", "expectedChange": "bankOpen becomes true"},
        {"variable": "phaseIntent", "expectedChange": "phase moves from needs_service toward banking"},
    ],
    "deposit": [
        {"variable": "bankOpen", "expectedChange": "bank is open before deposit and may stay open after deposit"},
        {"variable": "bankState", "expectedChange": "bank-like UI is directly observed and container availability is known"},
        {"variable": "menuOptionClicked", "expectedChange": "accepted menu action or UI command matches deposit inventory"},
        {"variable": "inventory", "expectedChange": "resource slots clear"},
        {"variable": "inventoryDelta", "expectedChange": "deposited item quantities decrease from inventory"},
        {"variable": "depositResult", "expectedChange": "depositComplete true with deposited item ids/quantities"},
        {"variable": "bankingLifecycle", "expectedChange": "banking lifecycle reaches complete with usable confidence"},
        {"variable": "resourceCount", "expectedChange": "held resource count drops to zero"},
        {"variable": "phaseIntent", "expectedChange": "bankingComplete becomes true or close_bank becomes needed"},
    ],
    "close_bank": [
        {"variable": "bankOpen", "expectedChange": "bankOpen becomes false"},
        {"variable": "phaseIntent", "expectedChange": "phase advances toward return_to_resource"},
    ],
    "return_to_resource": [
        {"variable": "location", "expectedChange": "player world tile/plane moves toward resource area"},
        {"variable": "routeProgress", "expectedChange": "return route/pathing advances"},
        {"variable": "phaseIntent", "expectedChange": "active intent returns to resource collection"},
    ],
    "wait_for_evidence": [
        {"variable": "phaseIntent", "expectedChange": "watched evidence is re-read without live input"},
    ],
    "recover_loaded_scene": [
        {"variable": "loadedScene", "expectedChange": "loaded scene, client tick, and world model become fresh"},
        {"variable": "inputIntegrity", "expectedChange": "pre-live baseline is reset or re-read before any live action"},
    ],
    "repeat_until": [
        {"variable": "phaseIntent", "expectedChange": "loop condition is rechecked from fresh live state each iteration"},
        {"variable": "inventory", "expectedChange": "loop condition can observe inventory_full or available slots"},
    ],
}

PRIMITIVE_SPECS: dict[str, dict[str, Any]] = {
    "collect": {
        "description": "Select a live resource candidate from an existing target profile.",
        "required": ["targetProfile"],
        "optional": ["targetClass", "resource", "action", "evidence", "until"],
        "emitsActionProposal": "select_resource_target",
        "engineIntent": "resource_object_action",
        "defaultEvidence": ["resource_candidate_live", "safe_aimpoint_pass", "hover_menu_contains_resource_action", "resource_progress_or_inventory_delta"],
    },
    "interact": {
        "description": "Interact with a live object/NPC/widget already proven by candidate/readiness evidence.",
        "required": ["target"],
        "optional": ["action", "targetClass", "serviceType", "routeId", "evidence"],
        "emitsActionProposal": "interact_service_route_object",
        "engineIntent": "service_object_action",
        "defaultEvidence": ["target_candidate_live", "hover_menu_matches_action", "MenuOptionClicked_expected"],
    },
    "walk_to": {
        "description": "Ask the existing navigation/route stack for a bounded waypoint action.",
        "required": ["destination"],
        "optional": ["routeId", "arrivalRadiusTiles", "evidence"],
        "emitsActionProposal": "navigate_to_service",
        "engineIntent": "navigation_waypoint_action",
        "defaultEvidence": ["path_frontier_available", "waypoint_projected", "position_or_route_progress"],
    },
    "bank": {
        "description": "Navigate to and open a bank/deposit service through service-route evidence.",
        "required": [],
        "optional": ["serviceType", "routeId", "evidence"],
        "emitsActionProposal": "open_service",
        "engineIntent": "service_object_action",
        "defaultEvidence": ["service_candidate_live", "bank_ui_open"],
    },
    "deposit": {
        "description": "Run the existing bank operation analyzer action.",
        "required": [],
        "optional": ["operation", "evidence"],
        "emitsActionProposal": "deposit_inventory",
        "engineIntent": "bank_operation_action",
        "defaultEvidence": ["bank_ui_open", "deposit_inventory_available", "no_resource_items_held"],
    },
    "close_bank": {
        "description": "Close the bank interface through existing close-bank context.",
        "required": [],
        "optional": ["evidence"],
        "emitsActionProposal": "close_bank",
        "engineIntent": "close_bank_action",
        "defaultEvidence": ["close_bank_ready", "bank_ui_closed"],
    },
    "return_to_resource": {
        "description": "Return from service to the remembered resource/worksite area.",
        "required": [],
        "optional": ["resourceProfile", "routeId", "resourceArea", "evidence"],
        "emitsActionProposal": "return_to_resource_area",
        "engineIntent": "return_to_resource_area",
        "defaultEvidence": ["return_destination_available", "worksite_reacquired", "resource_candidate_live"],
    },
    "wait_for_evidence": {
        "description": "Wait for live evidence rather than issuing input.",
        "required": ["evidence"],
        "optional": ["timeoutTicks"],
        "emitsActionProposal": "wait_for_context",
        "engineIntent": "evidence_wait",
        "defaultEvidence": [],
    },
    "recover_loaded_scene": {
        "description": "Request bounded liveness recovery such as ensure_loaded_scene.",
        "required": [],
        "optional": ["when", "evidence"],
        "emitsActionProposal": "none",
        "engineIntent": "liveness_recovery",
        "defaultEvidence": ["loaded_scene_verified", "client_tick_fresh", "world_model_fresh"],
    },
    "repeat_until": {
        "description": "Bounded control flow over high-level primitives.",
        "required": ["condition", "maxIterations", "steps"],
        "optional": ["evidence"],
        "emitsActionProposal": "wait_for_context",
        "engineIntent": "bounded_loop",
        "defaultEvidence": ["loop_condition_rechecked_from_live_state"],
    },
}

ALLOWED_PRIMITIVES = tuple(PRIMITIVE_SPECS.keys())
RAW_INPUT_PRIMITIVES = {
    "click",
    "raw_click",
    "mouse_click",
    "mouseDown",
    "mouseUp",
    "mouse_down",
    "mouse_up",
    "keyDown",
    "keyUp",
    "key_down",
    "key_up",
    "press",
    "type",
    "pyautogui",
    "pydirectinput",
    "arduino_write",
}
RAW_INPUT_FIELDS = {
    "screenPoint",
    "clickPoint",
    "plannedScreenPoint",
    "canvasPoint",
    "mouseDown",
    "mouseUp",
    "keyDown",
    "keyUp",
    "key",
    "keys",
    "hotkey",
    "pixels",
}
RAW_INPUT_PRIMITIVE_NAMES = {item.lower() for item in RAW_INPUT_PRIMITIVES}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_banking_lifecycle(source: Any) -> dict[str, Any]:
    if isinstance(source, dict):
        if source.get("schema") == banking_lifecycle.SCHEMA_VERSION:
            return source
        lifecycle = source.get("banking_lifecycle") or source.get("bankingLifecycle")
        if isinstance(lifecycle, dict):
            return lifecycle
        if any(key in source for key in ("baseline", "status", "activity", "bank_ui", "bankUi", "bank")):
            return banking_lifecycle.analyze_context(source)
        return {}
    if source is None:
        return {}
    path = Path(str(source)).expanduser()
    if path.is_dir():
        lifecycle = _read_json(path / "banking_lifecycle.json")
        if lifecycle:
            return lifecycle
        summary = _read_json(path / "summary.json")
        if isinstance(summary.get("banking_lifecycle"), dict):
            return summary["banking_lifecycle"]
        return banking_lifecycle.analyze_recording(path)
    if path.name == "banking_lifecycle.json":
        return _read_json(path)
    value = _read_json(path)
    if isinstance(value.get("banking_lifecycle"), dict):
        return value["banking_lifecycle"]
    if value.get("schema") == banking_lifecycle.SCHEMA_VERSION:
        return value
    return {}


def get_banking_lifecycle(source: Any) -> dict[str, Any]:
    """Return compact script-facing banking lifecycle data from a recording, summary, context, or lifecycle dict."""
    lifecycle = _load_banking_lifecycle(source)
    return banking_lifecycle.compact_lifecycle(lifecycle) if lifecycle else {
        "schema": banking_lifecycle.SCHEMA_VERSION,
        "status": "FAIL",
        "phase": "unknown",
        "confidence": 0.0,
        "missingCapabilities": ["banking.lifecycle"],
        "warnings": ["banking lifecycle source was not available"],
    }


def get_bank_state(source: Any) -> dict[str, Any]:
    lifecycle = get_banking_lifecycle(source)
    return {
        "bankOpen": lifecycle.get("bankOpenSeen"),
        "depositBoxOpen": lifecycle.get("depositBoxOpenSeen"),
        "activeBankLikeInterface": lifecycle.get("bankLikeInterface"),
        "bankWidgetRootSeen": lifecycle.get("bankWidgetRootSeen"),
        "bankContainerAvailable": lifecycle.get("bankContainerAvailable"),
        "bankContainerDeltaAvailable": lifecycle.get("bankContainerDeltaAvailable"),
        "bankUiPresent": lifecycle.get("bankUiPresent"),
        "bankUiSnapshotCount": lifecycle.get("bankUiSnapshotCount"),
        "bankUiFreshness": lifecycle.get("bankUiFreshness"),
        "confidence": lifecycle.get("confidence"),
        "missingCapabilities": lifecycle.get("missingCapabilities") or [],
        "warnings": lifecycle.get("warnings") or [],
    }


def is_bank_open(source: Any) -> bool | None:
    return get_bank_state(source).get("bankOpen")


def is_deposit_box_open(source: Any) -> bool | None:
    return get_bank_state(source).get("depositBoxOpen")


def get_active_bank_like_interface(source: Any) -> str | None:
    return get_bank_state(source).get("activeBankLikeInterface")


def get_inventory_delta(source: Any) -> dict[str, Any]:
    lifecycle = get_banking_lifecycle(source)
    return {
        "freeSlotsBefore": lifecycle.get("freeSlotsBefore"),
        "freeSlotsAfter": lifecycle.get("freeSlotsAfter"),
        "freeSlotDelta": lifecycle.get("freeSlotDelta"),
        "normalLogsBefore": lifecycle.get("normalLogsBefore"),
        "normalLogsAfter": lifecycle.get("normalLogsAfter"),
        "depositedItems": lifecycle.get("depositedItems") or [],
        "withdrawnItems": lifecycle.get("withdrawnItems") or [],
    }


def get_deposit_result(source: Any) -> dict[str, Any]:
    lifecycle = get_banking_lifecycle(source)
    items = lifecycle.get("depositedItems") or []
    return {
        "depositComplete": bool(lifecycle.get("depositDetected")),
        "depositedItems": items,
        "totalDepositedCount": lifecycle.get("depositedItemCount") or 0,
        "depositConfirmationLevel": lifecycle.get("depositConfirmationLevel"),
        "bankContainerDeltaAvailable": lifecycle.get("bankContainerDeltaAvailable"),
        "inventoryFreeSlotsAfter": lifecycle.get("freeSlotsAfter"),
        "confidence": lifecycle.get("confidence"),
        "missingCapabilities": lifecycle.get("missingCapabilities") or [],
        "warnings": lifecycle.get("warnings") or [],
    }


def get_deposited_items(source: Any) -> list[dict[str, Any]]:
    return list(get_deposit_result(source).get("depositedItems") or [])


def did_deposit_item(source: Any, item_id: int) -> bool:
    return any(_dict(item).get("id") == item_id and (_dict(item).get("quantity") or 0) > 0 for item in get_deposited_items(source))


def get_banking_missing_capabilities(source: Any) -> list[str]:
    return list(get_banking_lifecycle(source).get("missingCapabilities") or [])


def _load_woodcutting_lifecycle(source: Any) -> dict[str, Any]:
    if isinstance(source, dict):
        if source.get("schema") == woodcutting_lifecycle.SCHEMA_VERSION:
            return source
        lifecycle = source.get("woodcutting_lifecycle") or source.get("woodcuttingLifecycle")
        if isinstance(lifecycle, dict):
            return lifecycle
        return {}
    if source is None:
        return {}
    path = Path(str(source)).expanduser()
    if path.is_dir():
        lifecycle = _read_json(path / "woodcutting_lifecycle.json")
        if lifecycle:
            return lifecycle
        summary = _read_json(path / "summary.json")
        if isinstance(summary.get("woodcutting_lifecycle"), dict):
            return summary["woodcutting_lifecycle"]
        return woodcutting_lifecycle.analyze_recording(path)
    if path.name == "woodcutting_lifecycle.json":
        return _read_json(path)
    value = _read_json(path)
    if isinstance(value.get("woodcutting_lifecycle"), dict):
        return value["woodcutting_lifecycle"]
    if value.get("schema") == woodcutting_lifecycle.SCHEMA_VERSION:
        return value
    return {}


def get_woodcutting_lifecycle(source: Any) -> dict[str, Any]:
    lifecycle = _load_woodcutting_lifecycle(source)
    return woodcutting_lifecycle.compact_lifecycle(lifecycle) if lifecycle else {
        "schema": woodcutting_lifecycle.SCHEMA_VERSION,
        "status": "FAIL",
        "phase": "unknown",
        "confidence": 0.0,
        "warnings": ["woodcutting lifecycle source was not available"],
        "missingCapabilities": ["woodcutting.lifecycle"],
    }


def _load_route_monitor_status(source: Any) -> dict[str, Any]:
    if isinstance(source, dict):
        monitor = source.get("route_monitor") or source.get("routeMonitor") or source.get("route_monitor_status") or source.get("routeMonitorStatus")
        if isinstance(monitor, dict):
            return monitor
        history = source.get("route_history") or source.get("routeHistory") or source.get("route_history_summary") or source.get("routeHistorySummary")
        if isinstance(history, dict):
            return _route_status_from_history(history)
        if source.get("schema") == route_monitor.SCHEMA_VERSION or source.get("routeState") is not None:
            return source
        return {}
    if source is None:
        return {}
    path = Path(str(source)).expanduser()
    if path.is_dir():
        for name in ("route_monitor_status.json", "route_session_state.json"):
            monitor = _read_json(path / name)
            if monitor:
                return monitor
        history = _read_json(path / "route_history_summary.json")
        if history:
            return _route_status_from_history(history)
        summary = _read_json(path / "summary.json")
        if isinstance(summary.get("route_monitor"), dict):
            return summary["route_monitor"]
        if isinstance(summary.get("routeMonitorStatus"), dict):
            return summary["routeMonitorStatus"]
        if isinstance(summary.get("route_history"), dict):
            return _route_status_from_history(summary["route_history"])
        return {}
    value = _read_json(path)
    if value.get("schema") == route_monitor.SCHEMA_VERSION or value.get("routeState") is not None:
        return value
    if isinstance(value.get("route_monitor"), dict):
        return value["route_monitor"]
    if isinstance(value.get("routeMonitorStatus"), dict):
        return value["routeMonitorStatus"]
    return {}


def _route_status_from_history(history: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": route_monitor.SCHEMA_VERSION,
        "status": history.get("status"),
        "routeName": history.get("routeName"),
        "templateRevision": history.get("templateRevision"),
        "routeState": history.get("routeState"),
        "currentArea": history.get("currentArea"),
        "currentSegmentIndex": history.get("currentSegmentIndex"),
        "currentSegmentLabel": history.get("currentSegmentLabel"),
        "nextExpectedSegment": history.get("nextExpectedSegment"),
        "completedSegmentCount": history.get("completedSegmentCount"),
        "remainingSegmentCount": history.get("remainingSegmentCount"),
        "offRoute": history.get("offRoute"),
        "freshness": history.get("freshness"),
        "warnings": history.get("warnings") or [],
        "missingCapabilities": history.get("missingCapabilities") or [],
    }


def get_route_monitor_status(source: Any) -> dict[str, Any]:
    status = _load_route_monitor_status(source)
    if not status:
        return {
            "schema": route_monitor.SCHEMA_VERSION,
            "status": "FAIL",
            "routeState": "unknown",
            "offRoute": None,
            "warnings": ["route monitor status source was not available"],
            "missingCapabilities": ["route_monitor"],
        }
    return route_monitor.compact_status(status)


def get_route_state(source: Any) -> str | None:
    return get_route_monitor_status(source).get("routeState")


def get_current_route_segment(source: Any) -> dict[str, Any]:
    return _dict(get_route_monitor_status(source).get("currentSegment"))


def get_next_route_segment(source: Any) -> dict[str, Any]:
    return _dict(get_route_monitor_status(source).get("nextExpectedSegment"))


def is_off_route(source: Any) -> bool | None:
    return get_route_monitor_status(source).get("offRoute")


def _compact_route_interaction(step: dict[str, Any]) -> dict[str, Any]:
    target = _dict(step.get("target"))
    postcondition = _dict(step.get("postcondition"))
    return {
        "orderIndex": step.get("orderIndex"),
        "segmentIndex": step.get("segmentIndex"),
        "action": step.get("action"),
        "targetName": target.get("name") or step.get("targetName"),
        "targetId": target.get("id") or target.get("targetId"),
        "world": _dict(step.get("world")),
        "planeBefore": step.get("planeBefore"),
        "planeAfter": step.get("planeAfter"),
        "targetQuality": step.get("targetQuality"),
        "postcondition": {
            "type": postcondition.get("type"),
            "planeChanged": postcondition.get("planeChanged"),
            "positionChanged": postcondition.get("positionChanged"),
            "afterWorld": postcondition.get("afterWorld"),
        },
        "cameraHintCount": len(step.get("cameraHints") or []),
        "sourceRecording": step.get("sourceRecording"),
    }


def get_route_demonstration_guide(route_name: str, guide_dir: str | Path | None = None) -> dict[str, Any]:
    """Return compact demonstrated route-guide data for script/bot decisions."""
    guide = route_demonstration.load_route_guide(route_name, root=guide_dir)
    if not guide:
        return {
            "schema": "route_demonstration_guide_compact.v1",
            "status": "FAIL",
            "routeName": route_name,
            "routeGuideLoaded": False,
            "warnings": [f"route guide was not available: {route_name}"],
            "missingCapabilities": ["route_demonstration_guide"],
        }
    path_points = [item for item in guide.get("pathPoints") or [] if isinstance(item, dict)]
    interactions = [item for item in guide.get("interactionSteps") or [] if isinstance(item, dict)]
    plane_changes = [item for item in guide.get("planeChanges") or [] if isinstance(item, dict)]
    return {
        "schema": "route_demonstration_guide_compact.v1",
        "status": guide.get("status") or "WARN",
        "routeGuideLoaded": True,
        "routeName": guide.get("routeName") or route_name,
        "sourceRecordings": guide.get("sourceRecordings") or [],
        "startArea": guide.get("startArea"),
        "endArea": guide.get("endArea"),
        "pathPointCount": len(path_points),
        "interactionStepCount": len(interactions),
        "planeChangeCount": len(plane_changes),
        "firstPoint": _dict(path_points[0]).get("world") if path_points else None,
        "lastPoint": _dict(path_points[-1]).get("world") if path_points else None,
        "interactionSteps": [_compact_route_interaction(step) for step in interactions[:12]],
        "cameraHintCount": len(guide.get("cameraHints") or []),
        "warnings": guide.get("warnings") or [],
        "missingCapabilities": [],
    }


def get_route_guide_progress(
    route_name: str,
    current_world: dict[str, Any],
    guide_dir: str | Path | None = None,
    *,
    reached_tolerance_tiles: int = 2,
) -> dict[str, Any]:
    """Resolve current player position against the demonstrated route guide."""
    guide = route_demonstration.load_route_guide(route_name, root=guide_dir)
    if not guide:
        return {
            "schema": "route_guide_progress.v1",
            "status": "FAIL",
            "routeGuideLoaded": False,
            "routeGuideName": route_name,
            "currentWorld": _dict(current_world),
            "blocker": "route_guide_missing",
            "warnings": [f"route guide was not available: {route_name}"],
            "missingCapabilities": ["route_demonstration_guide"],
        }
    progress = route_demonstration.resolve_progress(
        guide,
        current_world,
        reached_tolerance_tiles=reached_tolerance_tiles,
    )
    if progress.get("routeGuideLoaded") is not True:
        progress.setdefault("missingCapabilities", []).append("playerWorldPosition")
    return progress


def get_route_guide_reentry(
    route_name: str,
    current_world: dict[str, Any],
    guide_dir: str | Path | None = None,
    *,
    max_same_plane_distance: float | None = None,
    reached_tolerance_tiles: int = 2,
) -> dict[str, Any]:
    """Resolve route-guide re-entry evidence for intermediate or wrong-floor states."""
    guide = route_demonstration.load_route_guide(route_name, root=guide_dir)
    if not guide:
        return {
            "schema": "route_guide_reentry.v1",
            "status": "FAIL",
            "routeGuideLoaded": False,
            "routeGuideName": route_name,
            "routeGuideReentryAttempted": True,
            "currentWorld": _dict(current_world),
            "blocker": "route_guide_missing",
            "warnings": [f"route guide was not available: {route_name}"],
            "missingCapabilities": ["route_demonstration_guide"],
        }
    reentry = route_demonstration.resolve_reentry(
        guide,
        current_world,
        max_same_plane_distance=max_same_plane_distance,
        reached_tolerance_tiles=reached_tolerance_tiles,
    )
    if reentry.get("routeGuideLoaded") is not True:
        reentry.setdefault("missingCapabilities", []).append("playerWorldPosition")
    return reentry


def _load_interruption_lifecycle(source: Any) -> dict[str, Any]:
    if isinstance(source, dict):
        if source.get("schema") == interruption_lifecycle.SCHEMA_VERSION:
            return source
        lifecycle = source.get("interruption_lifecycle") or source.get("interruptionLifecycle")
        if isinstance(lifecycle, dict):
            return lifecycle
        if any(key in source for key in ("combat_state", "combatState", "combat", "woodcutting_lifecycle", "woodcuttingLifecycle")):
            return interruption_lifecycle.analyze_context(source)
        return {}
    if source is None:
        return {}
    path = Path(str(source)).expanduser()
    if path.is_dir():
        lifecycle = _read_json(path / "interruption_lifecycle.json")
        if lifecycle:
            return lifecycle
        summary = _read_json(path / "summary.json")
        if isinstance(summary.get("interruption_lifecycle"), dict):
            return summary["interruption_lifecycle"]
        return interruption_lifecycle.analyze_recording(path)
    if path.name == "interruption_lifecycle.json":
        return _read_json(path)
    value = _read_json(path)
    if isinstance(value.get("interruption_lifecycle"), dict):
        return value["interruption_lifecycle"]
    if value.get("schema") == interruption_lifecycle.SCHEMA_VERSION:
        return value
    return {}


def get_interruption_lifecycle(source: Any) -> dict[str, Any]:
    lifecycle = _load_interruption_lifecycle(source)
    return interruption_lifecycle.compact_lifecycle(lifecycle) if lifecycle else {
        "schema": "interruption_lifecycle_compact.v1",
        "status": "FAIL",
        "interruptionDetected": False,
        "interruptionType": "unknown",
        "primaryCause": "unknown",
        "confidence": 0.0,
        "missingCapabilities": ["interruption.lifecycle"],
        "warnings": ["interruption lifecycle source was not available"],
    }


def get_combat_state(source: Any) -> dict[str, Any]:
    lifecycle = _load_interruption_lifecycle(source)
    combat = _dict(lifecycle.get("combat"))
    return {
        "inCombat": bool(combat.get("combatObserved")),
        "combatObserved": bool(combat.get("combatObserved")),
        "npcTargetedPlayer": bool(combat.get("npcTargetedPlayer")),
        "playerTargetedNpc": bool(combat.get("playerTargetedNpc")),
        "hitsplatsSeen": combat.get("hitsplatsSeen") or 0,
        "playerHealthChanged": bool(combat.get("playerHealthChanged")),
        "hostileNpcs": combat.get("hostileNpcs") or [],
        "actorsInteractingWithPlayer": combat.get("actorsInteractingWithPlayer") or [],
        "playerTargets": combat.get("playerTargets") or [],
        "missingCapabilities": lifecycle.get("missingCapabilities") or ["combat_state"],
        "warnings": lifecycle.get("warnings") or [],
    }


def is_in_combat(source: Any) -> bool:
    return bool(get_combat_state(source).get("combatObserved"))


def was_task_interrupted(source: Any) -> bool:
    return bool(get_interruption_lifecycle(source).get("interruptionDetected"))


def get_interruption_cause(source: Any) -> str | None:
    return get_interruption_lifecycle(source).get("primaryCause")


def get_recent_hitsplats(source: Any) -> list[dict[str, Any]]:
    return list(_load_interruption_lifecycle(source).get("combat", {}).get("recentHitsplats") or [])


def get_recent_stat_changes(source: Any) -> list[dict[str, Any]]:
    return list(_load_interruption_lifecycle(source).get("statChanges") or [])


def get_recent_game_messages(source: Any) -> list[dict[str, Any]]:
    return list(_load_interruption_lifecycle(source).get("messages") or [])


def _load_combat_damage_summary(source: Any) -> dict[str, Any]:
    if isinstance(source, dict):
        if source.get("schema") in {combat_damage_summary.SCHEMA_VERSION, combat_damage_summary.COMPACT_SCHEMA_VERSION}:
            return source
        damage = source.get("combat_damage_summary") or source.get("combatDamageSummary")
        if isinstance(damage, dict):
            return damage
        if any(key in source for key in ("combat_state", "combatState", "combat", "interruption_lifecycle", "interruptionLifecycle")):
            return combat_damage_summary.analyze_context(source)
        return {}
    if source is None:
        return {}
    path = Path(str(source)).expanduser()
    if path.is_dir():
        damage = _read_json(path / "combat_damage_summary.json")
        if damage:
            return damage
        summary = _read_json(path / "summary.json")
        if isinstance(summary.get("combat_damage_summary"), dict):
            return summary["combat_damage_summary"]
        interruption = _load_interruption_lifecycle(path)
        return combat_damage_summary.analyze_recording(path) if interruption else {}
    if path.name == "combat_damage_summary.json":
        return _read_json(path)
    value = _read_json(path)
    if value.get("schema") in {combat_damage_summary.SCHEMA_VERSION, combat_damage_summary.COMPACT_SCHEMA_VERSION}:
        return value
    if isinstance(value.get("combat_damage_summary"), dict):
        return value["combat_damage_summary"]
    return {}


def get_combat_damage_summary(source: Any) -> dict[str, Any]:
    damage = _load_combat_damage_summary(source)
    if not damage:
        return {
            "schema": combat_damage_summary.COMPACT_SCHEMA_VERSION,
            "status": "FAIL",
            "combatObserved": False,
            "primaryOpponent": {},
            "damageTakenTotal": None,
            "damageDealtTotal": None,
            "hitsplatCount": 0,
            "missingCapabilities": ["combat.damageSummary"],
            "warnings": ["combat damage summary source was not available"],
        }
    if damage.get("schema") == combat_damage_summary.COMPACT_SCHEMA_VERSION:
        return damage
    return combat_damage_summary.compact_summary(damage)


def get_damage_taken(source: Any) -> dict[str, Any]:
    compact = get_combat_damage_summary(source)
    return {
        "total": compact.get("damageTakenTotal"),
        "hitsplats": compact.get("damageTakenHitsplats") or 0,
        "hpChanged": bool(compact.get("hpChanged")),
        "hpBefore": compact.get("hpBefore"),
        "hpAfter": compact.get("hpAfter"),
        "warnings": compact.get("warnings") or [],
    }


def get_damage_dealt(source: Any) -> dict[str, Any]:
    compact = get_combat_damage_summary(source)
    full = _load_combat_damage_summary(source)
    return {
        "total": compact.get("damageDealtTotal"),
        "hitsplats": compact.get("damageDealtHitsplats") or 0,
        "targets": _dict(full.get("damageDealt")).get("targets") or [],
        "warnings": compact.get("warnings") or [],
    }


def get_primary_opponent(source: Any) -> dict[str, Any]:
    return _dict(get_combat_damage_summary(source).get("primaryOpponent"))


def did_take_damage(source: Any) -> bool:
    taken = get_damage_taken(source)
    total = taken.get("total")
    return bool((isinstance(total, (int, float)) and total > 0) or taken.get("hpChanged") or taken.get("hitsplats"))


def did_deal_damage(source: Any) -> bool:
    dealt = get_damage_dealt(source)
    total = dealt.get("total")
    return bool((isinstance(total, (int, float)) and total > 0) or dealt.get("hitsplats"))


def get_recent_combat_window(source: Any) -> dict[str, Any]:
    return _dict(get_combat_damage_summary(source).get("combatWindow"))


def _load_woodcutting_loop_lifecycle(source: Any) -> dict[str, Any]:
    if isinstance(source, dict):
        if source.get("schema") in {woodcutting_loop_lifecycle.SCHEMA_VERSION, woodcutting_loop_lifecycle.COMPACT_SCHEMA_VERSION}:
            return source
        lifecycle = source.get("woodcutting_loop_lifecycle") or source.get("woodcuttingLoopLifecycle") or source.get("woodcuttingLoop")
        if isinstance(lifecycle, dict):
            return lifecycle
        if any(
            key in source
            for key in (
                "woodcutting_lifecycle",
                "woodcuttingLifecycle",
                "banking_lifecycle",
                "bankingLifecycle",
                "traversal_lifecycle",
                "traversalLifecycle",
                "interruption_lifecycle",
                "interruptionLifecycle",
            )
        ):
            return woodcutting_loop_lifecycle.analyze_context(source)
        return {}
    if source is None:
        return {}
    path = Path(str(source)).expanduser()
    if path.is_dir():
        lifecycle = _read_json(path / "woodcutting_loop_lifecycle.json")
        if lifecycle:
            return lifecycle
        summary = _read_json(path / "summary.json")
        if isinstance(summary.get("woodcutting_loop_lifecycle"), dict):
            return summary["woodcutting_loop_lifecycle"]
        return woodcutting_loop_lifecycle.analyze_recording(path)
    if path.name == "woodcutting_loop_lifecycle.json":
        return _read_json(path)
    value = _read_json(path)
    if value.get("schema") in {woodcutting_loop_lifecycle.SCHEMA_VERSION, woodcutting_loop_lifecycle.COMPACT_SCHEMA_VERSION}:
        return value
    if isinstance(value.get("woodcutting_loop_lifecycle"), dict):
        return value["woodcutting_loop_lifecycle"]
    return {}


def get_woodcutting_loop_lifecycle(source: Any) -> dict[str, Any]:
    lifecycle = _load_woodcutting_loop_lifecycle(source)
    if not lifecycle:
        return {
            "schema": woodcutting_loop_lifecycle.COMPACT_SCHEMA_VERSION,
            "status": "FAIL",
            "loopState": "unknown",
            "currentPhase": "unknown",
            "nextExpectedPhase": "unknown",
            "confidence": 0.0,
            "missingCapabilities": ["woodcutting_loop_lifecycle"],
            "warnings": ["woodcutting loop lifecycle source was not available"],
        }
    if lifecycle.get("schema") == woodcutting_loop_lifecycle.COMPACT_SCHEMA_VERSION:
        return lifecycle
    return woodcutting_loop_lifecycle.compact_lifecycle(lifecycle)


def get_current_task_phase(source: Any) -> str | None:
    return get_woodcutting_loop_lifecycle(source).get("currentPhase")


def get_next_expected_phase(source: Any) -> str | None:
    return get_woodcutting_loop_lifecycle(source).get("nextExpectedPhase")


def is_inventory_full_for_woodcutting(source: Any) -> bool:
    lifecycle = get_woodcutting_loop_lifecycle(source)
    return bool(lifecycle.get("inventoryFull"))


def did_deposit_logs(source: Any) -> bool:
    lifecycle = get_woodcutting_loop_lifecycle(source)
    if lifecycle.get("depositedLogs") is True:
        return True
    for item in lifecycle.get("depositedItems") or []:
        record = _dict(item)
        item_id = record.get("id") or record.get("itemId")
        try:
            item_id_int = int(item_id)
        except (TypeError, ValueError):
            item_id_int = None
        name = _norm(record.get("name"))
        qty = record.get("quantity") or record.get("count") or 0
        if (item_id_int in {1511, 1521} or "logs" in name) and qty:
            return True
    return False


def should_route_to_bank(source: Any) -> bool:
    return get_next_expected_phase(source) == "route_to_bank"


def should_route_to_trees(source: Any) -> bool:
    return get_next_expected_phase(source) == "route_to_woodcutting_area"


def was_interrupted(source: Any) -> bool:
    return bool(get_woodcutting_loop_lifecycle(source).get("interruptionDetected")) or was_task_interrupted(source)


def did_resume_after_interruption(source: Any) -> bool:
    return bool(get_woodcutting_loop_lifecycle(source).get("taskResumed") or get_interruption_lifecycle(source).get("taskResumed"))


def did_task_resume(source: Any) -> bool:
    return did_resume_after_interruption(source)


def get_human_click_profile(source: Any = None) -> dict[str, Any]:
    profile = human_click_profile.load_profile(source)
    if not profile:
        return {
            "schema": "human_click_profile_compact.v1",
            "status": "FAIL",
            "recordingCount": 0,
            "warnings": ["human click profile source was not available"],
            "missingCapabilities": ["human_click_profile"],
        }
    return human_click_profile.compact_profile(profile)


def get_task_click_profile(activity: str, source: Any = None) -> dict[str, Any]:
    profile = human_click_profile.load_profile(source)
    if not profile:
        return {
            "schema": "human_click_profile_task_bucket.v1",
            "status": "FAIL",
            "activity": activity,
            "warnings": ["human click profile source was not available"],
            "missingCapabilities": ["human_click_profile"],
        }
    return human_click_profile.compact_profile(profile, activity=activity).get("taskProfile") or {
        "schema": "human_click_profile_task_bucket.v1",
        "activity": activity,
        "recordingCount": 0,
        "warnings": [f"activity bucket not found: {activity}"],
    }


def get_click_landing_profile(activity: str | None = None, source: Any = None) -> dict[str, Any]:
    profile = human_click_profile.load_profile(source)
    compact = human_click_profile.compact_profile(profile, activity=activity) if profile else {}
    return _dict(compact.get("landing"))


def get_camera_action_profile(activity: str | None = None, source: Any = None) -> dict[str, Any]:
    profile = human_click_profile.load_profile(source)
    compact = human_click_profile.compact_profile(profile, activity=activity) if profile else {}
    return _dict(compact.get("camera"))


def _runtime_variable_from_source(source: dict[str, Any], name: str) -> Any:
    runtime = _dict(source.get("taskScriptRuntimeEvidence") or source.get("runtimeEvidence"))
    data = _dict(runtime.get("data"))
    variables = _dict(data.get("runtimeVariables") or source.get("runtimeVariables"))
    variable = _dict(variables.get(name))
    if "value" in variable:
        return variable.get("value")
    return source.get(name)


def get_click_planning_context(activity: str | None = None, source: Any = None) -> dict[str, Any]:
    source_dict = _dict(source)
    action_visibility = _dict(
        source_dict.get("actionInputVisibility")
        or source_dict.get("actionInputVisibilitySummary")
        or _dict(_dict(source_dict.get("action_input_visibility")).get("data"))
    )
    target = _dict(source_dict.get("target") or action_visibility.get("plannedTarget"))
    action = _first_present(source_dict.get("action"), action_visibility.get("plannedAction"))
    route_status = _dict(source_dict.get("routeMonitor") or _runtime_variable_from_source(source_dict, "routeMonitor"))
    if not route_status and source is not None:
        route_status = get_route_monitor_status(source)
    loop = _dict(source_dict.get("woodcuttingLoopLifecycle") or _runtime_variable_from_source(source_dict, "woodcuttingLoopLifecycle"))
    if not loop and source is not None:
        loop = get_woodcutting_loop_lifecycle(source)
    deposit = _dict(source_dict.get("depositResult") or _runtime_variable_from_source(source_dict, "depositResult"))
    if not deposit and source is not None:
        deposit = get_deposit_result(source)
    bank_state = _dict(source_dict.get("bankState") or _runtime_variable_from_source(source_dict, "bankState"))
    if not bank_state and source is not None:
        bank_state = get_bank_state(source)
    profile = _dict(source_dict.get("humanClickProfile") or _runtime_variable_from_source(source_dict, "humanClickProfile"))
    if not profile:
        profile = get_human_click_profile()
    activity_bucket = click_planner.normalize_activity(activity, {
        "woodcuttingLoopLifecycle": loop,
        "bankState": bank_state,
        "depositResult": deposit,
        "routeMonitor": route_status,
    })
    warnings = []
    missing = []
    if not target:
        missing.append("target")
        warnings.append("live target/readiness evidence is not available")
    if not profile or profile.get("status") == "FAIL":
        missing.append("human_click_profile")
        warnings.append("human click profile is not available")
    return {
        "schema": click_planner.CONTEXT_SCHEMA,
        "status": "WARN" if missing else "PASS",
        "activity": activity_bucket,
        "action": action,
        "target": target,
        "actionInputVisibility": action_visibility,
        "routeMonitor": route_status,
        "woodcuttingLoopLifecycle": loop,
        "depositResult": deposit,
        "bankState": bank_state,
        "humanClickProfile": profile,
        "warnings": warnings,
        "missingCapabilities": missing,
    }


def get_human_click_plan(target: Any = None, action: str | None = None, activity: str | None = None, source: Any = None) -> dict[str, Any]:
    context = get_click_planning_context(activity=activity, source=source)
    if isinstance(target, dict):
        context["target"] = target
    if action:
        context["action"] = action
    return click_planner.build_click_plan(
        context,
        target=_dict(context.get("target")),
        action=context.get("action"),
        activity=context.get("activity"),
        human_profile=_dict(context.get("humanClickProfile")),
    )


def get_next_click_plan(source: Any = None) -> dict[str, Any]:
    context = get_click_planning_context(source=source)
    action = context.get("action")
    loop_next_raw = _dict(context.get("woodcuttingLoopLifecycle")).get("nextExpectedPhase")
    loop_next = _dict(loop_next_raw).get("phase") if isinstance(loop_next_raw, dict) else loop_next_raw
    if not action:
        if loop_next == "route_to_bank":
            action = "route_to_bank"
        elif loop_next == "banking_deposit":
            action = "Deposit"
        elif loop_next == "route_to_woodcutting_area":
            action = "route_to_woodcutting_area"
        elif loop_next in {"resume_cutting", "continue_cutting", "continue_current_phase"}:
            action = "Chop down"
        else:
            action = "unknown"
        context["action"] = action
    return get_human_click_plan(
        target=_dict(context.get("target")),
        action=str(action),
        activity=context.get("activity"),
        source=context,
    )


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str:
    return "" if value is None else str(value)


def _norm(value: Any) -> str:
    return _str(value).strip().lower()


def _error(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _warning(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _json_signature(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _nested_get(value: Any, path: list[str]) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_nested(value: Any, paths: list[list[str]]) -> Any:
    for path in paths:
        found = _nested_get(value, path)
        if found is not None:
            return found
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _primitive_name(step: dict[str, Any]) -> str:
    text = str(step.get("primitive") or step.get("op") or "").strip()
    lower = text.lower()
    if text in PRIMITIVE_SPECS:
        return text
    if lower in PRIMITIVE_SPECS:
        return lower
    return text


def _loads_script(script: dict[str, Any] | str | Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if isinstance(script, dict):
        return deepcopy(script), []
    if isinstance(script, Path):
        try:
            return _loads_script(script.read_text(encoding="utf-8"))
        except OSError as error:
            return {}, [_error("$", "runtime_file_disk_issue", f"could not read script: {error}")]
    if isinstance(script, str):
        text = script.strip()
        if not text:
            return {}, [_error("$", "missing_script", "script is empty")]
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            return {}, [_error("$", "invalid_json", f"script is not valid JSON: {error}")]
        if isinstance(value, dict):
            return value, []
        return {}, [_error("$", "invalid_script_type", "script JSON must be an object")]
    return {}, [_error("$", "invalid_script_type", "script must be a dict, JSON string, or path")]


def _step_path(parent: str, index: int) -> str:
    return f"{parent}.steps[{index}]" if parent else f"steps[{index}]"


def _evidence_list(step: dict[str, Any], primitive: str) -> list[str]:
    evidence = step.get("evidence")
    if isinstance(evidence, str):
        return [evidence]
    if isinstance(evidence, list):
        return [str(item) for item in evidence if str(item or "").strip()]
    return list(PRIMITIVE_SPECS[primitive].get("defaultEvidence") or [])


def _validate_step(step: Any, path: str, errors: list[dict[str, str]], warnings: list[dict[str, str]]) -> None:
    if not isinstance(step, dict):
        errors.append(_error(path, "invalid_step_type", "step must be an object"))
        return
    primitive = str(step.get("primitive") or step.get("op") or "").strip()
    primitive_norm = _primitive_name(step)
    if primitive_norm in RAW_INPUT_PRIMITIVES or primitive_norm.lower() in RAW_INPUT_PRIMITIVE_NAMES:
        errors.append(_error(f"{path}.primitive", "raw_input_bypass_forbidden", "raw input primitives bypass tracing and are not allowed"))
        return
    if primitive_norm not in PRIMITIVE_SPECS:
        errors.append(_error(f"{path}.primitive", "unknown_primitive", f"unknown primitive: {primitive or '<missing>'}"))
        return
    for key in sorted(RAW_INPUT_FIELDS.intersection(step.keys())):
        errors.append(_error(f"{path}.{key}", "raw_input_field_forbidden", "high-level scripts may not carry raw screen/canvas/key input fields"))
    spec = PRIMITIVE_SPECS[primitive_norm]
    for field in spec.get("required", []):
        value = step.get(field)
        missing = value is None or value == "" or value == []
        if missing:
            errors.append(_error(f"{path}.{field}", "missing_required_field", f"{primitive_norm} requires {field}"))
    evidence = step.get("evidence")
    if evidence is not None and not isinstance(evidence, (str, list)):
        errors.append(_error(f"{path}.evidence", "invalid_evidence", "evidence must be a string or list of strings"))
    if primitive_norm == "wait_for_evidence":
        if not _evidence_list(step, primitive_norm):
            errors.append(_error(f"{path}.evidence", "missing_evidence", "wait_for_evidence needs at least one evidence gate"))
    if primitive_norm == "repeat_until":
        max_iterations = step.get("maxIterations")
        if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations < 1 or max_iterations > 100:
            errors.append(_error(f"{path}.maxIterations", "unbounded_loop_forbidden", "repeat_until requires maxIterations between 1 and 100"))
        children = step.get("steps")
        if not isinstance(children, list) or not children:
            errors.append(_error(f"{path}.steps", "missing_loop_steps", "repeat_until requires a non-empty steps list"))
        else:
            for index, child in enumerate(children):
                _validate_step(child, _step_path(path, index), errors, warnings)
    if primitive_norm in {"collect", "interact", "bank", "deposit", "close_bank", "return_to_resource"} and not _evidence_list(step, primitive_norm):
        warnings.append(_warning(f"{path}.evidence", "default_evidence_applied", f"{primitive_norm} will use default evidence gates"))
    if step.get("allowExternalRefreshInLivePath") is True:
        errors.append(_error(f"{path}.allowExternalRefreshInLivePath", "external_refresh_in_hot_path_forbidden", "external refresh is not allowed in the live executor path"))
    if primitive_norm == "walk_to" and isinstance(step.get("destination"), dict):
        destination = _dict(step.get("destination"))
        if {"screenX", "screenY", "canvasX", "canvasY"}.intersection(destination.keys()):
            errors.append(_error(f"{path}.destination", "screen_coordinate_destination_forbidden", "walk_to destinations must be world/route/service labels, not screen or canvas points"))
    if step.get("routeId"):
        warnings.append(_warning(f"{path}.routeId", "static_route_is_advisory", "routeId is a static/session prior until live route evidence verifies it"))


def validate_task_script(script: dict[str, Any] | str | Path) -> dict[str, Any]:
    payload, load_errors = _loads_script(script)
    errors = list(load_errors)
    warnings: list[dict[str, str]] = []
    if payload:
        schema = payload.get("schema")
        if schema and schema != TASK_SCRIPT_SCHEMA:
            warnings.append(_warning("$.schema", "unexpected_schema", f"expected {TASK_SCRIPT_SCHEMA}, got {schema}"))
        if not str(payload.get("name") or "").strip():
            errors.append(_error("$.name", "missing_name", "script needs a stable name"))
        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append(_error("$.steps", "missing_steps", "script needs a non-empty steps list"))
        else:
            for index, step in enumerate(steps):
                _validate_step(step, _step_path("$", index), errors, warnings)
        if payload.get("allowExternalRefreshInLivePath") is True:
            errors.append(_error("$.allowExternalRefreshInLivePath", "external_refresh_in_hot_path_forbidden", "external refresh is not allowed in the live executor path"))
        if payload.get("rawInputAllowed") is True:
            errors.append(_error("$.rawInputAllowed", "raw_input_bypass_forbidden", "raw arbitrary input is not part of the task script API"))
    return {
        "schema": TASK_SCRIPT_VALIDATION_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "generatedAtUtc": utc_now(),
        "valid": not errors,
        "scriptName": payload.get("name") if payload else None,
        "primitiveCount": len(_list(payload.get("steps"))) if payload else 0,
        "allowedPrimitives": list(ALLOWED_PRIMITIVES),
        "errors": errors,
        "warnings": warnings,
        "noLiveInput": True,
        "rawInputBypassToolsExposed": False,
        "directBackendBypassCountRequired": 0,
        "externalKnowledgePolicy": EXTERNAL_KNOWLEDGE_POLICY,
    }


def _policy_for(script: dict[str, Any]) -> task_policy.TaskPolicy:
    return task_policy.resolve_task_policy(
        script.get("policy") or script.get("taskPolicy"),
        task=script.get("task"),
        profile=script.get("profile"),
    )


def _primitive_target(step: dict[str, Any], primitive: str) -> dict[str, Any]:
    if primitive == "collect":
        return {
            "profile": step.get("targetProfile"),
            "targetClass": step.get("targetClass"),
            "resource": step.get("resource"),
            "action": step.get("action") or "Chop down",
        }
    if primitive == "walk_to":
        return {"destination": step.get("destination"), "routeId": step.get("routeId"), "arrivalRadiusTiles": step.get("arrivalRadiusTiles")}
    if primitive == "bank":
        return {"serviceType": step.get("serviceType") or "bank", "routeId": step.get("routeId")}
    if primitive == "deposit":
        return {"operation": step.get("operation") or "deposit_inventory"}
    if primitive == "return_to_resource":
        return {"resourceProfile": step.get("resourceProfile"), "resourceArea": step.get("resourceArea"), "routeId": step.get("routeId")}
    if primitive == "interact":
        return {"target": step.get("target"), "targetClass": step.get("targetClass"), "action": step.get("action"), "routeId": step.get("routeId")}
    if primitive == "recover_loaded_scene":
        return {"when": step.get("when") or "loaded_scene_not_verified"}
    if primitive == "wait_for_evidence":
        return {"evidence": _evidence_list(step, primitive), "timeoutTicks": step.get("timeoutTicks")}
    return {}


def _action_for_step(step: dict[str, Any], primitive: str) -> str:
    if primitive == "interact":
        text = " ".join([_norm(step.get("target")), _norm(step.get("targetClass")), _norm(step.get("action")), _norm(step.get("serviceType"))])
        if any(token in text for token in ("bank", "deposit", "service")):
            return "open_service"
        if any(token in text for token in ("tree", "oak", "willow", "chop")):
            return "select_resource_target"
    return str(PRIMITIVE_SPECS[primitive]["emitsActionProposal"])


def _runtime_evidence_for_primitive(primitive: str) -> dict[str, Any]:
    expectations = [dict(item) for item in PRIMITIVE_RUNTIME_EXPECTATIONS.get(primitive, [])]
    variables = list(dict.fromkeys(str(item.get("variable")) for item in expectations if item.get("variable")))
    return {
        "variables": variables,
        "expectedChanges": expectations,
        "variableSources": {name: RUNTIME_EVIDENCE_VARIABLES.get(name, {}) for name in variables},
        "readOnlyQueries": [
            "get_current_debug_context",
            "get_action_input_visibility",
            "get_latest_action_trace",
            "list_seen_inventory_items",
        ],
        "changeProofRule": "compare before/after snapshots from live RuneLite/8893/WorldModel/8890; external facts never prove live change",
    }


def _compile_step(step: dict[str, Any], path: str, index: int) -> dict[str, Any]:
    primitive = _primitive_name(step)
    spec = PRIMITIVE_SPECS[primitive]
    compiled: dict[str, Any] = {
        "stepIndex": index,
        "sourcePath": path,
        "primitive": primitive,
        "engineIntent": spec.get("engineIntent"),
        "actionProposalAction": _action_for_step(step, primitive),
        "boundedOperatorRequest": BOUNDED_OPERATOR_REQUESTS[primitive],
        "target": _primitive_target(step, primitive),
        "requiredEvidence": _evidence_list(step, primitive),
        "readinessGate": {
            "query": "get_action_input_visibility" if primitive not in {"wait_for_evidence", "recover_loaded_scene"} else "get_current_debug_context",
            "mustPassBeforeLiveInput": primitive not in {"wait_for_evidence", "recover_loaded_scene", "repeat_until"},
            "directBackendBypassCountMustRemain": 0,
        },
        "canonicalPipeline": CANONICAL_PIPELINE if primitive not in {"wait_for_evidence", "recover_loaded_scene", "repeat_until"} else [],
        "failureClassificationHints": FAILURE_CLASSIFICATIONS,
        "liveTruthSources": ["RuneLite 8893", "WorldModel", "8890 daemon", "client_tick_hot"],
        "externalFactsMayEnrich": True,
        "externalFactsAdvisoryOnly": True,
        "runtimeEvidence": _runtime_evidence_for_primitive(primitive),
    }
    if primitive == "recover_loaded_scene":
        compiled["livenessRecovery"] = {
            "requestedAction": "ensure_loaded_scene",
            "manualLoginRequiredIsBlocker": True,
            "knownFlowsShouldUse": "request_liveness_recovery",
        }
    if primitive == "repeat_until":
        children = _list(step.get("steps"))
        compiled["condition"] = step.get("condition")
        compiled["maxIterations"] = step.get("maxIterations")
        compiled["steps"] = [_compile_step(child, _step_path(path, child_index), child_index) for child_index, child in enumerate(children) if isinstance(child, dict)]
    return compiled


def _flatten_plan_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for step in steps:
        flattened.append(step)
        flattened.extend(_flatten_plan_steps(_list(step.get("steps"))))
    return flattened


def _runtime_evidence_plan_from_steps(flattened_steps: list[dict[str, Any]]) -> dict[str, Any]:
    step_evidence = []
    variable_names: list[str] = []
    for step in flattened_steps:
        runtime = _dict(step.get("runtimeEvidence"))
        variables = [str(item) for item in _list(runtime.get("variables")) if str(item or "").strip()]
        variable_names.extend(variables)
        step_evidence.append(
            {
                "sourcePath": step.get("sourcePath"),
                "primitive": step.get("primitive"),
                "actionProposalAction": step.get("actionProposalAction"),
                "variables": variables,
                "expectedChanges": _list(runtime.get("expectedChanges")),
                "readOnlyQueries": _list(runtime.get("readOnlyQueries")),
            }
        )
    covered = list(dict.fromkeys(variable_names))
    important = [
        "inventory",
        "resourceCount",
        "bankOpen",
        "menuOptionClicked",
        "hoverTarget",
        "location",
        "routeProgress",
        "phaseIntent",
    ]
    missing = [name for name in important if name not in covered]
    return {
        "schema": TASK_SCRIPT_EVIDENCE_PLAN_SCHEMA,
        "variableCatalog": RUNTIME_EVIDENCE_VARIABLES,
        "requiredWoodcutBankLifecycleVariables": important,
        "coveredVariables": covered,
        "missingLifecycleVariables": missing,
        "stepEvidence": step_evidence,
        "snapshotProtocol": {
            "before": "capture get_current_debug_context and get_action_input_visibility before a bounded live step",
            "after": "capture the same queries after tick/state proof",
            "compare": "assert expectedChanges using live variables only",
        },
        "noLiveInput": True,
    }


def compile_task_script(script: dict[str, Any] | str | Path) -> dict[str, Any]:
    payload, load_errors = _loads_script(script)
    validation = validate_task_script(payload if payload else script)
    if load_errors and not validation.get("errors"):
        validation["errors"] = load_errors
        validation["valid"] = False
        validation["status"] = "FAIL"
    if validation.get("status") != "PASS":
        return {
            "schema": TASK_SCRIPT_PLAN_SCHEMA,
            "status": "FAIL",
            "generatedAtUtc": utc_now(),
            "validation": validation,
            "actionPlan": [],
            "noLiveInput": True,
        }
    policy = _policy_for(payload)
    steps = [_compile_step(step, _step_path("$", index), index) for index, step in enumerate(_list(payload.get("steps"))) if isinstance(step, dict)]
    flattened = _flatten_plan_steps(steps)
    action_intents = list(dict.fromkeys(str(step.get("actionProposalAction")) for step in flattened if step.get("actionProposalAction") not in {None, ""}))
    data = {
        "script": {
            "schema": payload.get("schema") or TASK_SCRIPT_SCHEMA,
            "name": payload.get("name"),
            "description": payload.get("description"),
            "profile": payload.get("profile") or policy.profile,
            "task": payload.get("task") or policy.task,
        },
        "taskPolicy": policy.to_dict(),
        "actionPlan": steps,
        "flattenedActionPlan": flattened,
        "actionProposalActions": action_intents,
        "runtimeEvidencePlan": _runtime_evidence_plan_from_steps(flattened),
        "executorContract": {
            "usesExistingExecutorOnly": True,
            "liveInputBackend": "HumanInputController -> ArduinoHIDBackend",
            "directBackendBypassCountMustRemain": 0,
            "rawArbitraryInputToolsAllowed": False,
            "boundedOperatorRequestsAllowed": list(dict.fromkeys(BOUNDED_OPERATOR_REQUESTS.values())),
            "canonicalPipeline": CANONICAL_PIPELINE,
        },
        "phaseAwareInputIntegrityPolicy": PHASE_AWARE_INPUT_POLICY,
        "externalKnowledgePolicy": EXTERNAL_KNOWLEDGE_POLICY,
        "failureClassificationPolicy": FAILURE_CLASSIFICATIONS,
        "noLiveInput": True,
    }
    return {
        "schema": TASK_SCRIPT_PLAN_SCHEMA,
        "status": "PASS",
        "generatedAtUtc": utc_now(),
        "validation": validation,
        "data": data,
        "noLiveInput": True,
    }


def explain_script_plan(script: dict[str, Any] | str | Path) -> dict[str, Any]:
    plan = compile_task_script(script)
    data = {
        "summary": "High-level task primitives compile into existing profile/action proposal intents; live motor control stays in the canonical Arduino-backed executor pipeline.",
        "validation": plan.get("validation"),
        "plan": plan.get("data") or {"actionPlan": []},
        "runtimeEvidencePlan": _dict(plan.get("data")).get("runtimeEvidencePlan"),
        "primitiveReference": PRIMITIVE_SPECS,
        "canonicalPipeline": CANONICAL_PIPELINE,
        "phaseAwareInputIntegrityPolicy": PHASE_AWARE_INPUT_POLICY,
        "externalKnowledgePolicy": EXTERNAL_KNOWLEDGE_POLICY,
        "failureClassificationPolicy": FAILURE_CLASSIFICATIONS,
        "readinessChecklist": [
            "loaded scene verified from RuneLite/8893/WorldModel/8890",
            "actionReadiness PASS for the proposed live action",
            "hover/menu evidence matches the intended target/action",
            "input integrity reset or rebaseline completed before live action",
            "directBackendBypassCount remains 0",
            "post-live lifecycle proof observed",
        ],
        "noLiveInput": True,
    }
    return {
        "schema": TASK_SCRIPT_EXPLANATION_SCHEMA,
        "status": plan.get("status", "WARN"),
        "generatedAtUtc": utc_now(),
        "data": data,
        "noLiveInput": True,
    }


def build_task_script_evidence_plan(script: dict[str, Any] | str | Path) -> dict[str, Any]:
    plan = compile_task_script(script)
    if plan.get("status") != "PASS":
        return {
            "schema": TASK_SCRIPT_EVIDENCE_PLAN_SCHEMA,
            "status": "FAIL",
            "generatedAtUtc": utc_now(),
            "validation": plan.get("validation"),
            "data": {},
            "noLiveInput": True,
        }
    plan_data = _dict(plan.get("data"))
    evidence_plan = _dict(plan_data.get("runtimeEvidencePlan"))
    return {
        "schema": TASK_SCRIPT_EVIDENCE_PLAN_SCHEMA,
        "status": "PASS",
        "generatedAtUtc": utc_now(),
        "validation": plan.get("validation"),
        "data": {
            **evidence_plan,
            "script": plan_data.get("script"),
            "taskPolicy": plan_data.get("taskPolicy"),
            "actionProposalActions": plan_data.get("actionProposalActions"),
        },
        "noLiveInput": True,
    }


def _runtime_data(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = _dict(payload)
    if payload.get("schema") == TASK_RUNTIME_EVIDENCE_SCHEMA:
        return _dict(payload.get("data"))
    if "runtimeVariables" in payload:
        return payload
    return _dict(payload.get("data"))


def _runtime_variables(payload: dict[str, Any] | None) -> dict[str, Any]:
    return _dict(_runtime_data(payload).get("runtimeVariables"))


def _observed_variable(wrapper: Any) -> bool:
    wrapper = _dict(wrapper)
    if isinstance(wrapper.get("observed"), bool):
        return bool(wrapper.get("observed"))
    return wrapper.get("value") is not None


def _variable_value(wrapper: Any) -> Any:
    return _dict(wrapper).get("value")


def _comparison_variable_names(before: dict[str, Any], after: dict[str, Any], primitive: str | None, script: dict[str, Any] | str | Path | None) -> list[str]:
    names: list[str] = []
    if script is not None:
        plan = build_task_script_evidence_plan(script)
        names.extend(str(item) for item in _list(_dict(plan.get("data")).get("coveredVariables")) if str(item or "").strip())
    primitive_name = _norm(primitive)
    if primitive_name in PRIMITIVE_RUNTIME_EXPECTATIONS:
        names.extend(str(item.get("variable")) for item in PRIMITIVE_RUNTIME_EXPECTATIONS[primitive_name] if item.get("variable"))
    names.extend(str(name) for name in before.keys())
    names.extend(str(name) for name in after.keys())
    return list(dict.fromkeys(name for name in names if name))


def _input_integrity_hard_blockers(after_variables: dict[str, Any]) -> list[str]:
    value = _variable_value(after_variables.get("inputIntegrity"))
    phase = _dict(_dict(value).get("phaseCounts"))
    live_phase = _dict(phase.get("live_action_phase"))
    blockers: list[str] = []
    if live_phase.get("hardBlocker") is True:
        blockers.append("live_action_input_integrity_hard_blocker")
    for key in ("injectedEventsDelta", "lowerIlInjectedEventsDelta", "directBackendBypassCountDelta"):
        delta = live_phase.get(key)
        if isinstance(delta, (int, float)) and delta > 0:
            blockers.append(f"{key}_nonzero")
    current = _dict(_dict(value).get("current"))
    direct = current.get("directBackendBypassCount")
    if isinstance(direct, (int, float)) and direct > 0:
        blockers.append("directBackendBypassCount_nonzero")
    return list(dict.fromkeys(blockers))


def compare_task_runtime_evidence_snapshots(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    script: dict[str, Any] | str | Path | None = None,
    primitive: str | None = None,
) -> dict[str, Any]:
    before_variables = _runtime_variables(before)
    after_variables = _runtime_variables(after)
    variable_names = _comparison_variable_names(before_variables, after_variables, primitive, script)
    primitive_name = _norm(primitive)
    expected_variables = []
    if primitive_name in PRIMITIVE_RUNTIME_EXPECTATIONS:
        expected_variables = list(dict.fromkeys(str(item.get("variable")) for item in PRIMITIVE_RUNTIME_EXPECTATIONS[primitive_name] if item.get("variable")))
    comparisons = []
    changed_variables = []
    missing_after = []
    for name in variable_names:
        before_wrapper = _dict(before_variables.get(name))
        after_wrapper = _dict(after_variables.get(name))
        before_observed = _observed_variable(before_wrapper)
        after_observed = _observed_variable(after_wrapper)
        before_value = _variable_value(before_wrapper)
        after_value = _variable_value(after_wrapper)
        changed = _json_signature(before_value) != _json_signature(after_value)
        if changed:
            changed_variables.append(name)
        if name in expected_variables and not after_observed:
            missing_after.append(name)
        comparisons.append(
            {
                "variable": name,
                "beforeObserved": before_observed,
                "afterObserved": after_observed,
                "changed": changed,
                "beforeValue": before_value,
                "afterValue": after_value,
                "source": after_wrapper.get("source") or before_wrapper.get("source"),
                "expectedForPrimitive": name in expected_variables,
            }
        )
    expected_changed = [name for name in expected_variables if name in changed_variables]
    unexpected_unchanged = [name for name in expected_variables if name not in changed_variables]
    after_data = _runtime_data(after)
    readiness_summary = _dict(after_data.get("readinessSummary"))
    manual_login = readiness_summary.get("manualLoginRequired") is True
    runtime_integrity = _dict(after_data.get("runtimeEvidenceIntegrity"))
    variable_integrity = _dict(runtime_integrity.get("variableIntegrity"))
    proof_blocked_after = [
        name
        for name in expected_variables
        if _dict(after_variables.get(name)).get("observed") is True
        and _dict(variable_integrity.get(name)).get("proofEligibleNow") is False
    ]
    hard_blockers = _input_integrity_hard_blockers(after_variables)
    warnings = []
    if manual_login:
        warnings.append("manual_login_required")
    warnings.extend(f"expected_variable_missing_after:{name}" for name in missing_after)
    warnings.extend(f"expected_variable_not_proof_eligible_after:{name}" for name in proof_blocked_after)
    if primitive_name and expected_variables and not expected_changed:
        warnings.append("no_expected_variable_changed")
    if not primitive_name and not changed_variables:
        warnings.append("no_runtime_variable_changed")
    expected_changed_proof_eligible = [name for name in expected_changed if name not in proof_blocked_after]
    blockers = list(hard_blockers)
    status = "FAIL" if blockers else "WARN" if warnings else "PASS"
    return {
        "schema": TASK_RUNTIME_EVIDENCE_COMPARISON_SCHEMA,
        "status": status,
        "generatedAtUtc": utc_now(),
        "primitive": primitive_name or None,
        "data": {
            "variableComparisons": comparisons,
            "changedVariables": changed_variables,
            "unchangedVariables": [name for name in variable_names if name not in changed_variables],
            "expectedVariables": expected_variables,
            "expectedVariablesChanged": expected_changed,
            "expectedVariablesChangedAndProofEligible": expected_changed_proof_eligible,
            "expectedVariablesUnchanged": unexpected_unchanged,
            "missingExpectedVariablesAfter": missing_after,
            "expectedVariablesProofBlockedAfter": proof_blocked_after,
            "runtimeEvidenceIntegrityAfter": runtime_integrity or None,
            "inputIntegrityHardBlockers": hard_blockers,
            "manualLoginRequired": manual_login,
            "liveValidationPossibleAfter": after_data.get("liveValidationPossibleNow"),
            "comparisonRule": "PASS requires after-snapshot evidence for expected variables and no live-action input-integrity hard blockers; external facts never prove live change",
            "noLiveInput": True,
        },
        "warnings": warnings,
        "blockers": blockers,
        "externalKnowledgePolicy": EXTERNAL_KNOWLEDGE_POLICY,
        "noLiveInput": True,
    }


def _evidence_bundle(
    evidence: dict[str, Any] | None,
    *,
    current_blocker: dict[str, Any] | None = None,
    debug_context: dict[str, Any] | None = None,
    runtime_evidence: dict[str, Any] | None = None,
    comparison: dict[str, Any] | None = None,
    action_input_visibility: dict[str, Any] | None = None,
    action_trace: dict[str, Any] | None = None,
    external_knowledge: dict[str, Any] | None = None,
    error_text: str | None = None,
) -> dict[str, Any]:
    bundle = deepcopy(_dict(evidence))
    schema = bundle.get("schema")
    if schema == TASK_RUNTIME_EVIDENCE_SCHEMA:
        bundle = {"runtimeEvidence": bundle}
    elif schema == TASK_RUNTIME_EVIDENCE_COMPARISON_SCHEMA:
        bundle = {"comparison": bundle}
    elif schema == "knowledge_fabric_current_blocker_explanation.v1":
        bundle = {"currentBlocker": bundle}
    elif schema == "knowledge_fabric_current_debug_context.v1":
        bundle = {"debugContext": bundle}
    elif schema == "action_input_visibility_context.v1":
        bundle = {"actionInputVisibility": bundle}
    elif schema == "action_trace.v2" or bundle.get("actionTraceSchema") == "action_trace.v2":
        bundle = {"actionTrace": bundle}
    overrides = {
        "currentBlocker": current_blocker,
        "debugContext": debug_context,
        "runtimeEvidence": runtime_evidence,
        "comparison": comparison,
        "actionInputVisibility": action_input_visibility,
        "actionTrace": action_trace,
        "externalKnowledge": external_knowledge,
        "errorText": error_text,
    }
    for key, value in overrides.items():
        if value is not None:
            bundle[key] = value
    return bundle


def _bundle_text(bundle: dict[str, Any]) -> str:
    return json.dumps(bundle, default=str, sort_keys=True).lower()


def _input_integrity_value(bundle: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        _first_nested(bundle, [["actionInputVisibility", "data", "input_integrity_status"], ["actionInputVisibility", "input_integrity_status"]]),
        _first_nested(bundle, [["runtimeEvidence", "data", "runtimeVariables", "inputIntegrity", "value"]]),
        _first_nested(bundle, [["currentBlocker", "data", "evidence", "inputIntegrity"]]),
        _first_nested(bundle, [["debugContext", "data", "inputIntegrity"], ["debugContext", "data", "readiness", "inputIntegrity"]]),
        _first_nested(bundle, [["actionTrace", "inputIntegrityPhaseReport"]]),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            if candidate.get("schema") == "input_integrity_phase_report.v1":
                return {"phaseCounts": candidate}
            return candidate
    return {}


def _num(value: Any) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _input_integrity_assessment(bundle: dict[str, Any]) -> dict[str, Any]:
    value = _input_integrity_value(bundle)
    phase = _dict(value.get("phaseCounts"))
    operator = _dict(phase.get("operator_phase"))
    live = _dict(phase.get("live_action_phase"))
    current = _dict(value.get("current")) or value
    operator_injected = _num(operator.get("operatorInjectedEvents"))
    operator_lower = _num(operator.get("operatorLowerIlInjectedEvents"))
    live_injected = _num(live.get("injectedEventsDelta"))
    live_lower = _num(live.get("lowerIlInjectedEventsDelta"))
    live_direct_delta = _num(live.get("directBackendBypassCountDelta"))
    direct_current = _num(current.get("directBackendBypassCount"))
    live_hard = bool(live.get("hardBlocker") is True or live_injected > 0 or live_lower > 0 or live_direct_delta > 0 or direct_current > 0)
    phase_present = bool(phase)
    current_injected = _num(current.get("injectedEvents"))
    current_lower = _num(current.get("lowerIlInjectedEvents"))
    operator_noise = bool((operator_injected > 0 or operator_lower > 0 or (not phase_present and (current_injected > 0 or current_lower > 0))) and not live_hard)
    blockers = []
    if live.get("hardBlocker") is True:
        blockers.append("live_action_input_integrity_hard_blocker")
    if live_injected > 0:
        blockers.append("live_action_injected_delta_nonzero")
    if live_lower > 0:
        blockers.append("live_action_lower_il_delta_nonzero")
    if live_direct_delta > 0:
        blockers.append("live_action_direct_backend_bypass_delta_nonzero")
    if direct_current > 0:
        blockers.append("directBackendBypassCount_nonzero")
    return {
        "phaseEvidencePresent": phase_present,
        "operatorInjectedEvents": int(operator_injected),
        "operatorLowerIlInjectedEvents": int(operator_lower),
        "currentInjectedEvents": int(current_injected),
        "currentLowerIlInjectedEvents": int(current_lower),
        "liveActionInjectedEventsDelta": int(live_injected),
        "liveActionLowerIlInjectedEventsDelta": int(live_lower),
        "liveActionDirectBackendBypassCountDelta": int(live_direct_delta),
        "directBackendBypassCount": int(direct_current) if direct_current else 0,
        "operatorNoiseOnly": operator_noise,
        "liveActionHardBlocker": live_hard,
        "hardBlockers": blockers,
    }


LOGIN_STATE_TOKENS = {"login_screen", "manual_login_required", "credential_required", "credentialrequired", "blocked_user_login_required"}


def _login_token_in_structured_value(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return _norm(value) in LOGIN_STATE_TOKENS
    if isinstance(value, list):
        return any(_login_token_in_structured_value(item) for item in value)
    if isinstance(value, dict):
        signal_keys = {
            "blocker",
            "blockedReason",
            "blockReason",
            "category",
            "code",
            "gameState",
            "livenessState",
            "primaryBlockerCategory",
            "reason",
            "state",
            "status",
        }
        return any(
            _login_token_in_structured_value(value.get(key))
            for key in signal_keys
            if key in value
        )
    return False


def _manual_login_signal(bundle: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    bool_paths = [
        ["runtimeEvidence", "data", "readinessSummary", "manualLoginRequired"],
        ["runtimeEvidence", "data", "runtimeVariables", "loadedScene", "value", "manualLoginRequired"],
        ["debugContext", "data", "readiness", "manualLoginRequired"],
        ["debugContext", "data", "manualLoginRequired"],
        ["actionInputVisibility", "data", "readiness", "manualLoginRequired"],
        ["actionInputVisibility", "data", "readinessActionEvidence", "manualLoginRequired"],
        ["currentBlocker", "data", "evidence", "bootstrapState", "manualLoginRequired"],
    ]
    state_paths = [
        ["runtimeEvidence", "data", "readinessSummary", "livenessState"],
        ["runtimeEvidence", "data", "readinessSummary", "loadedSceneProof", "gameState"],
        ["debugContext", "data", "readiness", "livenessState"],
        ["debugContext", "data", "readiness", "loadedSceneProof", "gameState"],
        ["debugContext", "data", "livenessState"],
        ["debugContext", "data", "gameState"],
        ["actionInputVisibility", "data", "readiness", "livenessState"],
        ["actionInputVisibility", "data", "readiness", "loadedSceneProof", "gameState"],
        ["actionInputVisibility", "data", "readinessActionEvidence", "livenessState"],
        ["currentBlocker", "data", "evidence", "bootstrapState", "state"],
        ["currentBlocker", "data", "evidence", "bootstrapState", "gameState"],
    ]
    blocker_paths = [
        ["runtimeEvidence", "blockers"],
        ["debugContext", "blockers"],
        ["debugContext", "data", "blockers"],
        ["actionInputVisibility", "blockers"],
        ["actionInputVisibility", "data", "inputBlockEvidence"],
        ["actionInputVisibility", "data", "actionReadiness", "blockers"],
        ["currentBlocker", "blockers"],
        ["currentBlocker", "data", "primaryBlockerCategory"],
        ["currentBlocker", "data", "blocker"],
        ["currentBlocker", "data", "status"],
        ["errorText"],
    ]
    evidence: dict[str, Any] = {"boolSignals": [], "stateSignals": [], "blockerSignals": []}
    for path in bool_paths:
        value = _nested_get(bundle, path)
        if value is True:
            evidence["boolSignals"].append({"path": ".".join(path), "value": value})
    for path in state_paths:
        value = _nested_get(bundle, path)
        if _login_token_in_structured_value(value):
            evidence["stateSignals"].append({"path": ".".join(path), "value": value})
    for path in blocker_paths:
        value = _nested_get(bundle, path)
        if _login_token_in_structured_value(value):
            evidence["blockerSignals"].append({"path": ".".join(path), "value": value})
    return any(evidence.values()), evidence


def _classification_candidate(
    classification: str,
    *,
    confidence: str,
    summary: str,
    evidence_path: str,
    evidence_value: Any,
    recommended_next_query: str,
    recommended_next_step: str,
    hard_blocker: bool = False,
) -> dict[str, Any]:
    return {
        "classification": classification,
        "confidence": confidence,
        "summary": summary,
        "evidence": {"path": evidence_path, "value": evidence_value},
        "recommendedNextQuery": recommended_next_query,
        "recommendedNextStep": recommended_next_step,
        "hardBlocker": hard_blocker,
    }


def _candidate_priority(candidate: dict[str, Any]) -> int:
    if candidate.get("hardBlocker"):
        return 100
    priorities = {
        "game-state/user-login blocker": 90,
        "stale liveness/plugin bug": 82,
        "runtime file/disk issue": 78,
        "coordinate_transform_error": 74,
        "arduino_movement_error": 73,
        "target_aimpoint_error": 72,
        "target/hover/menu mismatch": 71,
        "external knowledge/cache miss": 60,
        "code/data truth bug": 50,
        "operator-phase injected-input noise": 10,
    }
    return priorities.get(str(candidate.get("classification")), 0)


def classify_task_failure(
    evidence: dict[str, Any] | None = None,
    *,
    current_blocker: dict[str, Any] | None = None,
    debug_context: dict[str, Any] | None = None,
    runtime_evidence: dict[str, Any] | None = None,
    comparison: dict[str, Any] | None = None,
    action_input_visibility: dict[str, Any] | None = None,
    action_trace: dict[str, Any] | None = None,
    external_knowledge: dict[str, Any] | None = None,
    error_text: str | None = None,
) -> dict[str, Any]:
    bundle = _evidence_bundle(
        evidence,
        current_blocker=current_blocker,
        debug_context=debug_context,
        runtime_evidence=runtime_evidence,
        comparison=comparison,
        action_input_visibility=action_input_visibility,
        action_trace=action_trace,
        external_knowledge=external_knowledge,
        error_text=error_text,
    )
    text = _bundle_text(bundle)
    input_assessment = _input_integrity_assessment(bundle)
    candidates: list[dict[str, Any]] = []

    if input_assessment["liveActionHardBlocker"]:
        candidates.append(
            _classification_candidate(
                "code/data truth bug",
                confidence="high",
                summary="The live action window contains injected/lower-IL input or direct backend bypass evidence; live motor control is no longer proven Arduino-only.",
                evidence_path="inputIntegrity.live_action_phase",
                evidence_value=input_assessment,
                recommended_next_query="get_action_input_visibility",
                recommended_next_step="STOP_ALL, DISARM, STATUS, then inspect the live-action input-integrity baseline and executor backend path before any further live action.",
                hard_blocker=True,
            )
        )
    elif input_assessment["operatorNoiseOnly"]:
        candidates.append(
            _classification_candidate(
                "operator-phase injected-input noise",
                confidence="high" if input_assessment["phaseEvidencePresent"] else "medium",
                summary="Injected/lower-IL events are present outside the live action delta window and should be treated as operator/debug noise, not script failure.",
                evidence_path="inputIntegrity.operator_phase",
                evidence_value=input_assessment,
                recommended_next_query="get_action_input_visibility",
                recommended_next_step="Before live action, run the pre-live STOP_ALL/DISARM/STATUS and input-integrity reset or rebaseline.",
            )
        )

    manual_login, manual_login_evidence = _manual_login_signal(bundle)
    if manual_login:
        candidates.append(
            _classification_candidate(
                "game-state/user-login blocker",
                confidence="high",
                summary="Live evidence indicates a login or credential-required state; do not run gameplay actions.",
                evidence_path="readiness.manualLoginRequired/livenessState",
                evidence_value={
                    "runtimeEvidence": _first_nested(bundle, [["runtimeEvidence", "data", "readinessSummary"]]),
                    "debugContext": _first_nested(bundle, [["debugContext", "data", "readiness"]]),
                    "currentBlocker": _first_nested(bundle, [["currentBlocker", "data", "primaryBlockerCategory"]]),
                    "structuredSignals": manual_login_evidence,
                },
                recommended_next_query="get_current_debug_context",
                recommended_next_step="Request manual login/credential handling, then re-query loaded-scene readiness before any live action.",
            )
        )

    liveness_state = _first_nested(bundle, [["debugContext", "data", "livenessState"], ["runtimeEvidence", "data", "readinessSummary", "livenessState"]])
    loaded_scene = _first_nested(bundle, [["debugContext", "data", "loadedSceneVerified"], ["debugContext", "data", "readiness", "loadedSceneProof", "loadedSceneVerified"]])
    blocker_category = _first_nested(bundle, [["currentBlocker", "data", "primaryBlockerCategory"]])
    if (
        loaded_scene is False
        or _first_nested(bundle, [["debugContext", "data", "livenessRecoveryRecommended"]]) is True
        or blocker_category in {"login/liveness", "plugin/daemon freshness"}
        or any(token in text for token in ("loaded scene is not verified", "world model unavailable", "plugin_snapshot_no_packets", "daemon_session_missing", "daemon_latest_tick_missing", "client_tick_stale"))
    ):
        candidates.append(
            _classification_candidate(
                "stale liveness/plugin bug",
                confidence="high" if not manual_login else "medium",
                summary="Loaded-scene, daemon, plugin, or client-tick evidence is stale or missing.",
                evidence_path="liveness/daemon/readiness",
                evidence_value={"livenessState": liveness_state, "loadedSceneVerified": loaded_scene, "primaryBlockerCategory": blocker_category},
                recommended_next_query="get_current_debug_context",
                recommended_next_step="Use ensure_loaded_scene/request_liveness_recovery when the screen is known-safe; do not rediscover login/disconnect flows manually.",
            )
        )

    if any(token in text for token in ("no telemetry session selected", "could not read", "filenotfound", "permission denied", "oserror", "missing file", "path does not exist", "disk")):
        candidates.append(
            _classification_candidate(
                "runtime file/disk issue",
                confidence="medium",
                summary="The evidence points to a missing/unreadable runtime file, session, or disk-backed artifact.",
                evidence_path="runtime/file",
                evidence_value=bundle.get("errorText") or _first_nested(bundle, [["currentBlocker", "warnings"], ["debugContext", "warnings"]]),
                recommended_next_query="get_pipeline_health",
                recommended_next_step="Inspect session/path binding and disk-backed debug artifacts before changing gameplay logic.",
            )
        )

    trace = _dict(bundle.get("actionTrace"))
    trace_classification = _norm(
        trace.get("clickFailureBucket")
        or trace.get("finalClassification")
        or _first_nested(bundle, [["actionInputVisibility", "data", "clickFailureBucket"]])
    )
    if "coordinate_transform_error" in text or trace_classification == "coordinate_transform_error" or "cursor_start_outside_allowed_region" in text:
        candidates.append(
            _classification_candidate(
                "coordinate_transform_error",
                confidence="high",
                summary="The requested physical/screen point or allowed-region state appears wrong; inspect coordinate conversion and cursor staging before blaming Arduino movement.",
                evidence_path="actionTrace.clickFailureBucket/coordinateTrace",
                evidence_value=trace.get("clickFailureBucket") or trace_classification,
                recommended_next_query="get_action_input_visibility",
                recommended_next_step="Fix coordinate conversion, display scale, or canvas-to-screen projection if the requested physical point is wrong.",
            )
        )
    if "arduino_movement_error" in text or trace_classification == "arduino_movement_error" or any(token in text for token in ("move_chunk_no_effect", "serial_timeout", "rawinput_seen_cursor_no_move", "positionerrorpx")):
        candidates.append(
            _classification_candidate(
                "arduino_movement_error",
                confidence="high" if "arduino_movement_error" in text else "medium",
                summary="The requested physical point appears plausible, but cursor movement/firmware evidence is suspect.",
                evidence_path="actionTrace.humanInput/mouseMove",
                evidence_value=trace.get("humanInput") or trace.get("mouseMove") or trace_classification,
                recommended_next_query="get_action_input_visibility",
                recommended_next_step="Inspect Arduino calibration, closed-loop movement trace, firmware acknowledgements, and cursor landing error.",
            )
        )
    safe_aimpoint = _first_nested(bundle, [["actionInputVisibility", "data", "plannedTarget", "safeAimPoint"], ["actionTrace", "selectedTarget", "safeAimPoint"]])
    safe_aimpoint_status = _norm(_dict(safe_aimpoint).get("status"))
    safe_aimpoint_failed = bool(
        safe_aimpoint_status in {"fail", "blocked", "unsafe"}
        or _dict(safe_aimpoint).get("actionable") is False
        or _dict(safe_aimpoint).get("validButUnsafe") is True
    )
    if "target_aimpoint_error" in text or safe_aimpoint_failed or any(token in text for token in ("target_outside_allowed_region", "aimpoint invalid", "insideinteractableregion\": false", "uiblocked\": true")):
        candidates.append(
            _classification_candidate(
                "target_aimpoint_error",
                confidence="medium",
                summary="Target selection may be valid, but the chosen aimpoint is unsafe, blocked, or not inside the interactable region.",
                evidence_path="plannedTarget.safeAimPoint",
                evidence_value=safe_aimpoint,
                recommended_next_query="get_action_input_visibility",
                recommended_next_step="Inspect safe aimpoint sampling, clickable hull, UI blocking, and target candidate geometry.",
            )
        )
    if any(token in text for token in ("hover_mismatch", "menu_mismatch", "clicked_direct_menu_mismatch", "menu_flip_mismatch", "menuoptionclicked mismatch")):
        candidates.append(
            _classification_candidate(
                "target/hover/menu mismatch",
                confidence="high",
                summary="Cursor/hover/menu proof does not match the intended target or accepted MenuOptionClicked action.",
                evidence_path="hover/menu/MenuOptionClicked",
                evidence_value={
                    "hover": _first_nested(bundle, [["actionInputVisibility", "data", "hoverConfirmationEvidence"]]),
                    "menuOptionClicked": _first_nested(bundle, [["actionInputVisibility", "data", "menuOptionClickedEvidence"], ["runtimeEvidence", "data", "runtimeVariables", "menuOptionClicked", "value"]]),
                    "traceClassification": trace_classification,
                },
                recommended_next_query="get_latest_action_trace",
                recommended_next_step="Inspect target, hover confirmation, menu action, and clicked option evidence; fix candidate or aimpoint logic if cursor landed correctly.",
            )
        )

    comparison_payload = _dict(bundle.get("comparison"))
    comparison_warnings = [str(item) for item in _list(comparison_payload.get("warnings"))]
    comparison_blockers = [str(item) for item in _list(comparison_payload.get("blockers"))]
    if any(token in " ".join(comparison_blockers) for token in ("live_action", "injected", "lower", "directBackendBypassCount")):
        candidates.append(
            _classification_candidate(
                "code/data truth bug",
                confidence="high",
                summary="The before/after comparison reports a live-action input-integrity hard blocker.",
                evidence_path="comparison.blockers",
                evidence_value=comparison_blockers,
                recommended_next_query="compare_task_script_runtime_evidence",
                recommended_next_step="STOP_ALL, DISARM, STATUS, then inspect input integrity baselines and executor backend evidence before any further live action.",
                hard_blocker=True,
            )
        )
    if any(token in " ".join(comparison_warnings + comparison_blockers) for token in ("no_expected_variable_changed", "no_runtime_variable_changed", "expected_variable_missing_after")):
        candidates.append(
            _classification_candidate(
                "code/data truth bug",
                confidence="medium",
                summary="Before/after live evidence did not prove the expected lifecycle variable changed.",
                evidence_path="comparison.warnings/blockers",
                evidence_value={"warnings": comparison_warnings, "blockers": comparison_blockers},
                recommended_next_query="compare_task_script_runtime_evidence",
                recommended_next_step="Inspect whether the primitive expectation, analyzer field, or lifecycle truth source is wrong before patching behavior.",
            )
        )
    external_misses = _first_nested(bundle, [["debugContext", "data", "dataQualityReport", "data", "externalCacheMisses"], ["externalKnowledge", "externalCacheMisses"]])
    if _list(external_misses) or any(token in text for token in ("external knowledge/cache miss", "cache_miss", "external cache miss", "wiki cache miss")):
        candidates.append(
            _classification_candidate(
                "external knowledge/cache miss",
                confidence="medium",
                summary="Advisory external OSRS knowledge is missing or stale; live gameplay truth is not affected.",
                evidence_path="externalKnowledge",
                evidence_value=external_misses or bundle.get("externalKnowledge"),
                recommended_next_query="external_knowledge_status",
                recommended_next_step="Use cache-first lookup or explicit refresh for authoring enrichment only; do not override fresh RuneLite evidence.",
            )
        )

    deduped: list[dict[str, Any]] = []
    seen = set()
    for candidate in candidates:
        key = (candidate.get("classification"), candidate.get("summary"))
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    deduped.sort(key=_candidate_priority, reverse=True)
    primary = deduped[0] if deduped else None
    blockers = list(input_assessment["hardBlockers"])
    if any(candidate.get("hardBlocker") for candidate in deduped):
        blockers.append("failure_classification_hard_blocker")
    if primary and primary.get("classification") == "game-state/user-login blocker":
        blockers.append("manual_login_required")
    status = "FAIL" if any(candidate.get("hardBlocker") for candidate in deduped) else "PASS" if primary is None or primary.get("classification") == "operator-phase injected-input noise" else "WARN"
    return {
        "schema": TASK_FAILURE_CLASSIFICATION_SCHEMA,
        "status": status,
        "generatedAtUtc": utc_now(),
        "primaryClassification": primary.get("classification") if primary else None,
        "primarySummary": primary.get("summary") if primary else "No failure signal found in supplied evidence.",
        "confidence": primary.get("confidence") if primary else "low",
        "classificationCandidates": deduped,
        "secondaryClassifications": [candidate.get("classification") for candidate in deduped[1:]],
        "inputIntegrityAssessment": input_assessment,
        "blockers": list(dict.fromkeys(blockers)),
        "recommendedNextQuery": primary.get("recommendedNextQuery") if primary else "get_current_debug_context",
        "recommendedNextStep": primary.get("recommendedNextStep") if primary else "Gather current debug context, action input visibility, and runtime evidence before patching.",
        "failureClassificationPolicy": FAILURE_CLASSIFICATIONS,
        "phaseAwareInputIntegrityPolicy": PHASE_AWARE_INPUT_POLICY,
        "externalKnowledgePolicy": EXTERNAL_KNOWLEDGE_POLICY,
        "noLiveInput": True,
    }


def _step_index(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _select_plan_step(steps: list[dict[str, Any]], *, step_index: Any = None, primitive: str | None = None) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    index = _step_index(step_index)
    if index is not None:
        for step in steps:
            if step.get("stepIndex") == index:
                return step, []
        errors.append(_error("$.stepIndex", "step_index_not_found", f"compiled script does not contain stepIndex {index}"))
        return None, errors
    primitive_name = _norm(primitive)
    if primitive_name:
        for step in steps:
            if _norm(step.get("primitive")) == primitive_name:
                return step, []
        errors.append(_error("$.primitive", "primitive_not_found", f"compiled script does not contain primitive {primitive_name}"))
        return None, errors
    return (steps[0], []) if steps else (None, [_error("$.steps", "no_compiled_steps", "compiled script has no steps")])


def _payload_data(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = _dict(payload)
    return _dict(payload.get("data")) if isinstance(payload.get("data"), dict) else payload


def _navigation_trace_evidence(navigation_decision_trace: dict[str, Any] | None) -> dict[str, Any]:
    payload = _dict(navigation_decision_trace)
    data = _payload_data(navigation_decision_trace)
    pathing = _dict(data.get("pathingFrontier"))
    frontier_diagnosis = _dict(pathing.get("frontierDiagnosis"))
    trace_present = data.get("tracePresent")
    latest_action_trace_count = data.get("latestActionTraceCount")
    trace_missing_reason = None
    if trace_present is False:
        trace_missing_reason = (
            "latest_action_trace_missing"
            if latest_action_trace_count == 0
            else "navigation_decision_trace_records_missing"
        )
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "source": _first_present(data.get("source"), payload.get("source")),
        "diagnosticOnly": data.get("diagnosticOnly"),
        "blockingEligible": data.get("blockingEligible"),
        "warnings": _list(payload.get("warnings")),
        "tracePresent": trace_present,
        "traceMissingReason": trace_missing_reason,
        "latestActionTraceCount": latest_action_trace_count,
        "decisionCount": data.get("decisionCount"),
        "firstSuspiciousDecision": data.get("firstSuspiciousDecision"),
        "latestDecision": data.get("latestDecision"),
        "routeContext": data.get("routeContext"),
        "pathingFrontierStatus": frontier_diagnosis.get("frontierStatus"),
        "pathingFrontierReason": frontier_diagnosis.get("frontierReason"),
        "pathingFrontierDiagnosis": frontier_diagnosis,
        "pathingPlayerLocation": pathing.get("playerLocation"),
        "routeContextCanGuideDiagnosis": frontier_diagnosis.get("routeContextCanGuideDiagnosis"),
        "diagnosisRules": data.get("diagnosisRules"),
        "noLiveInput": True,
    }


def _step_expected_variables(step: dict[str, Any]) -> list[str]:
    runtime = _dict(step.get("runtimeEvidence"))
    return [str(item) for item in _list(runtime.get("variables")) if str(item or "").strip()]


def _expected_runtime_variable_proof(
    runtime_data: dict[str, Any],
    expected_variables: list[str],
) -> dict[str, Any]:
    integrity = _dict(runtime_data.get("runtimeEvidenceIntegrity"))
    variable_integrity = _dict(integrity.get("variableIntegrity"))
    proof_eligible: list[str] = []
    advisory: list[str] = []
    proof_blocked: list[str] = []
    unknown: list[str] = []
    selected_integrity: dict[str, Any] = {}
    for name in expected_variables:
        item = _dict(variable_integrity.get(name))
        if item:
            selected_integrity[name] = item
        proof_value = item.get("proofEligibleNow")
        advisory_value = item.get("advisoryOnly") is True
        if proof_value is True:
            proof_eligible.append(name)
        elif proof_value is False:
            proof_blocked.append(name)
            if advisory_value:
                advisory.append(name)
        else:
            unknown.append(name)
    status = "WARN" if proof_blocked else "UNKNOWN" if expected_variables and not variable_integrity else "PASS"
    return {
        "schema": "expected_runtime_variable_proof.v1",
        "status": status,
        "runtimeEvidenceIntegritySchema": integrity.get("schema"),
        "runtimeEvidenceIntegrityStatus": integrity.get("status"),
        "expectedRuntimeVariables": expected_variables,
        "proofEligibleExpectedRuntimeVariables": proof_eligible,
        "advisoryExpectedRuntimeVariables": advisory,
        "proofBlockedExpectedRuntimeVariables": proof_blocked,
        "unknownProofExpectedRuntimeVariables": unknown,
        "proofBlockers": integrity.get("proofBlockers"),
        "variableIntegrity": selected_integrity,
        "rule": "A bounded request should not be made when expected proof variables are explicitly advisory-only.",
        "noLiveInput": True,
    }


def _runtime_variable_value(runtime_variables: dict[str, Any], name: str) -> Any:
    return _variable_value(runtime_variables.get(name))


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in {"true", "yes", "1"}:
            return True
        if lower in {"false", "no", "0"}:
            return False
    return None


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _infer_next_task_primitive(runtime_evidence: dict[str, Any] | None, action_input_visibility: dict[str, Any] | None, failure_classification: dict[str, Any] | None) -> dict[str, Any]:
    runtime_data = _runtime_data(runtime_evidence)
    variables = _runtime_variables(runtime_evidence)
    readiness_summary = _dict(runtime_data.get("readinessSummary"))
    visibility_data = _payload_data(action_input_visibility)
    visibility_readiness = _dict(visibility_data.get("readiness"))
    failure = _dict(failure_classification)
    inventory = _dict(_runtime_variable_value(variables, "inventory"))
    phase_intent = _dict(_runtime_variable_value(variables, "phaseIntent"))
    route_progress = _dict(_runtime_variable_value(variables, "routeProgress"))
    route_monitor_value = _dict(_runtime_variable_value(variables, "routeMonitor"))
    woodcutting_loop_value = _dict(_runtime_variable_value(variables, "woodcuttingLoopLifecycle"))
    interruption_value = _dict(_runtime_variable_value(variables, "interruptionLifecycle"))
    deposit_result = _dict(_runtime_variable_value(variables, "depositResult"))
    loaded_scene = _dict(_runtime_variable_value(variables, "loadedScene"))
    resource_count = _numeric(_runtime_variable_value(variables, "resourceCount"))
    bank_open = _boolish(_runtime_variable_value(variables, "bankOpen"))
    free_slots = _numeric(inventory.get("freeSlots"))
    inventory_full = _boolish(inventory.get("inventoryFull"))
    loop_next_raw = woodcutting_loop_value.get("nextExpectedPhase")
    loop_next = _norm(_dict(loop_next_raw).get("phase") if isinstance(loop_next_raw, dict) else loop_next_raw)
    loop_current_raw = woodcutting_loop_value.get("currentPhase")
    loop_current = _norm(_dict(loop_current_raw).get("phase") if isinstance(loop_current_raw, dict) else loop_current_raw)
    loop_state = _norm(woodcutting_loop_value.get("loopState"))
    off_route = _boolish(route_monitor_value.get("offRoute"))
    route_state = _norm(route_monitor_value.get("routeState"))
    interrupted = _boolish(interruption_value.get("interruptionDetected"))
    task_resumed = _boolish(interruption_value.get("taskResumed"))
    deposit_complete = _boolish(deposit_result.get("depositComplete"))
    manual_login = bool(
        readiness_summary.get("manualLoginRequired") is True
        or visibility_readiness.get("manualLoginRequired") is True
        or failure.get("primaryClassification") == "game-state/user-login blocker"
        or "manual_login_required" in _list(failure.get("blockers"))
    )
    loaded_scene_verified = _first_present(
        _dict(readiness_summary.get("loadedSceneProof")).get("loadedSceneVerified"),
        loaded_scene.get("loadedSceneVerified"),
        _dict(visibility_readiness.get("loadedSceneProof")).get("loadedSceneVerified"),
    )
    planned_action = _norm(visibility_data.get("plannedAction") or readiness_summary.get("proposedAction") or phase_intent.get("proposedAction"))
    current_intent = _norm(readiness_summary.get("currentIntent") or phase_intent.get("currentIntent") or phase_intent.get("activeIntent"))
    phase_text = _norm(phase_intent.get("phase") or phase_intent.get("cycleStage"))
    reason = "fallback_collect_until_live_evidence_selects_another_phase"
    primitive = "collect"
    confidence = "low"
    if manual_login or loaded_scene_verified is not True:
        primitive = "recover_loaded_scene"
        reason = "manual_login_or_loaded_scene_not_verified"
        confidence = "high"
    elif off_route is True or route_state == "off_route":
        primitive = "wait_for_evidence"
        reason = "route_monitor_reports_off_route"
        confidence = "high"
    elif interrupted is True and task_resumed is not True:
        primitive = "wait_for_evidence"
        reason = "task_interrupted_without_resume_evidence"
        confidence = "high"
    elif loop_next == "route_to_bank":
        primitive = "bank"
        reason = "woodcutting_loop_next_expected_phase_route_to_bank"
        confidence = "high"
    elif loop_next == "banking_deposit":
        primitive = "deposit" if bank_open is True else "bank"
        reason = "woodcutting_loop_next_expected_phase_banking_deposit"
        confidence = "high"
    elif loop_next == "route_to_woodcutting_area":
        primitive = "return_to_resource"
        reason = "woodcutting_loop_next_expected_phase_route_to_woodcutting_area"
        confidence = "high"
    elif loop_next in {"resume_cutting", "continue_cutting", "continue_current_phase"}:
        primitive = "collect"
        reason = "woodcutting_loop_next_expected_phase_collect_or_continue"
        confidence = "high"
    elif deposit_complete is True:
        primitive = "return_to_resource"
        reason = "deposit_result_complete_route_back_to_resource"
        confidence = "high"
    elif bank_open is True and (resource_count is None or resource_count > 0):
        primitive = "deposit"
        reason = "bank_open_with_resources_or_unknown_resource_count"
        confidence = "medium"
    elif bank_open is True and resource_count == 0:
        primitive = "close_bank"
        reason = "bank_open_and_no_resources_observed"
        confidence = "medium"
    elif "return" in current_intent or "return" in phase_text or planned_action == "return_to_resource_area":
        primitive = "return_to_resource"
        reason = "return_intent_or_phase_observed"
        confidence = "medium"
    elif inventory_full is True or free_slots == 0 or "route_transition" in current_intent or "service" in planned_action or "bank" in planned_action:
        primitive = "bank"
        reason = "inventory_full_or_service_route_intent"
        confidence = "medium"
    elif resource_count == 0 and "resource" in planned_action:
        primitive = "collect"
        reason = "resource_action_planned_with_no_resources_observed"
        confidence = "medium"
    return {
        "primitive": primitive,
        "reason": reason,
        "confidence": confidence,
        "evidence": {
            "manualLoginRequired": manual_login,
            "loadedSceneVerified": loaded_scene_verified,
            "bankOpen": bank_open,
            "resourceCount": resource_count,
            "inventoryFull": inventory_full,
            "freeSlots": free_slots,
            "woodcuttingLoopState": loop_state or None,
            "woodcuttingLoopCurrentPhase": loop_current or None,
            "woodcuttingLoopNextExpectedPhase": loop_next or None,
            "routeMonitorState": route_state or None,
            "offRoute": off_route,
            "interruptionDetected": interrupted,
            "taskResumed": task_resumed,
            "depositComplete": deposit_complete,
            "currentIntent": current_intent or None,
            "phase": phase_text or None,
            "plannedAction": planned_action or None,
            "routeProgress": route_progress,
        },
    }


def _readiness_blocker_codes(*sections: Any) -> list[str]:
    codes: list[str] = []
    for section in sections:
        for item in _list(section):
            if isinstance(item, dict):
                code = item.get("code") or item.get("staleReason") or item.get("message")
                if code is not None:
                    codes.append(str(code))
            elif item is not None:
                codes.append(str(item))
    return codes


def _lifecycle_evidence_integrity(runtime_data: dict[str, Any], variables: dict[str, Any], visibility_data: dict[str, Any], failure: dict[str, Any]) -> dict[str, Any]:
    readiness_summary = _dict(runtime_data.get("readinessSummary"))
    visibility_readiness = _dict(visibility_data.get("readiness"))
    loaded_scene = _dict(_runtime_variable_value(variables, "loadedScene"))
    loaded_scene_proof = _dict(readiness_summary.get("loadedSceneProof"))
    visibility_loaded_scene_proof = _dict(visibility_readiness.get("loadedSceneProof"))
    liveness_state = _norm(
        readiness_summary.get("livenessState")
        or loaded_scene.get("livenessState")
        or loaded_scene_proof.get("gameState")
        or visibility_readiness.get("livenessState")
        or visibility_loaded_scene_proof.get("gameState")
    )
    loaded_scene_verified = _first_present(
        loaded_scene_proof.get("loadedSceneVerified"),
        loaded_scene.get("loadedSceneVerified"),
        visibility_loaded_scene_proof.get("loadedSceneVerified"),
        _dict(visibility_data.get("livenessRecoveryActions")).get("loadedSceneVerified"),
    )
    manual_login = bool(
        readiness_summary.get("manualLoginRequired") is True
        or visibility_readiness.get("manualLoginRequired") is True
        or failure.get("primaryClassification") == "game-state/user-login blocker"
        or "manual_login_required" in _list(failure.get("blockers"))
    )
    action_readiness = _dict(visibility_data.get("actionReadiness")) or _dict(_dict(visibility_data.get("readiness")).get("actionReadiness"))
    blocker_codes = _readiness_blocker_codes(readiness_summary.get("blockers"), action_readiness.get("blockers"))
    loaded_scene_unverified = loaded_scene_verified is not True
    liveness_unverified = bool(
        manual_login
        or loaded_scene_unverified
        or "login" in liveness_state
        or liveness_state in {"unknown", "disconnected", "loading", "not_logged_in"}
    )
    advisory_fields: list[str] = []
    for name in ("inventory", "resourceCount", "bankOpen", "routeProgress", "phaseIntent"):
        wrapper = _dict(variables.get(name))
        if wrapper.get("observed") is True or _runtime_variable_value(variables, name) is not None:
            advisory_fields.append(name)
    if visibility_data.get("plannedAction"):
        advisory_fields.append("plannedAction")
    if visibility_data.get("plannedTarget"):
        advisory_fields.append("plannedTarget")

    warnings: list[str] = []
    if liveness_unverified:
        warnings.append("lifecycle_liveness_not_verified")
    if loaded_scene_unverified:
        warnings.append("loaded_scene_proof_missing_or_unverified")
    if manual_login:
        warnings.append("manual_login_lifecycle_context_non_executable")
    if liveness_unverified and "routeProgress" in advisory_fields:
        warnings.append("route_progress_present_while_liveness_unverified")
    if liveness_unverified and "phaseIntent" in advisory_fields:
        warnings.append("phase_intent_present_while_liveness_unverified")
    if liveness_unverified and "plannedAction" in advisory_fields:
        warnings.append("planned_action_present_while_liveness_unverified")
    if any("client_tick_hot_stale" in _norm(code) or "client_tick_hot" in _norm(code) for code in blocker_codes):
        warnings.append("client_tick_hot_stale_for_lifecycle")

    advisory_only = bool(liveness_unverified and advisory_fields)
    return {
        "schema": "task_lifecycle_evidence_integrity.v1",
        "status": "WARN" if warnings else "PASS",
        "loadedSceneVerified": loaded_scene_verified,
        "manualLoginRequired": manual_login,
        "livenessState": liveness_state or None,
        "liveTruthUsableForGameplay": bool(not liveness_unverified),
        "advisoryOnlyUntilLoadedSceneVerified": advisory_only,
        "advisoryLifecycleFields": list(dict.fromkeys(advisory_fields)) if advisory_only else [],
        "readinessBlockerCodes": list(dict.fromkeys(blocker_codes)),
        "warnings": list(dict.fromkeys(warnings)),
        "rule": "When loaded scene proof is missing or unverified, or manual login is required, route/phase/planned-action context may explain stale intent but must not authorize gameplay.",
        "noLiveInput": True,
    }


def assess_task_step_readiness(
    script: dict[str, Any] | str | Path,
    *,
    step_index: Any = None,
    primitive: str | None = None,
    runtime_evidence: dict[str, Any] | None = None,
    action_input_visibility: dict[str, Any] | None = None,
    failure_classification: dict[str, Any] | None = None,
    navigation_decision_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = compile_task_script(script)
    if plan.get("status") != "PASS":
        return {
            "schema": TASK_STEP_READINESS_SCHEMA,
            "status": "FAIL",
            "generatedAtUtc": utc_now(),
            "validation": plan.get("validation"),
            "errors": _list(_dict(plan.get("validation")).get("errors")),
            "noLiveInput": True,
        }
    plan_data = _dict(plan.get("data"))
    steps = [_dict(item) for item in _list(plan_data.get("flattenedActionPlan")) if isinstance(item, dict)]
    selected_step, selection_errors = _select_plan_step(steps, step_index=step_index, primitive=primitive)
    if selected_step is None:
        return {
            "schema": TASK_STEP_READINESS_SCHEMA,
            "status": "FAIL",
            "generatedAtUtc": utc_now(),
            "errors": selection_errors,
            "data": {"compiledStepCount": len(steps), "noLiveInput": True},
            "noLiveInput": True,
        }
    primitive_name = _norm(selected_step.get("primitive"))
    bounded_request = str(selected_step.get("boundedOperatorRequest") or BOUNDED_OPERATOR_REQUESTS.get(primitive_name) or "")
    live_capable = primitive_name not in {"wait_for_evidence", "recover_loaded_scene", "repeat_until"}
    watcher_or_recovery = not live_capable
    runtime_data = _runtime_data(runtime_evidence)
    visibility_data = _payload_data(action_input_visibility)
    failure = failure_classification or classify_task_failure(
        {
            "runtimeEvidence": runtime_evidence or {},
            "actionInputVisibility": action_input_visibility or {},
            "navigationDecisionTrace": navigation_decision_trace or {},
        }
    )
    nav_data = _payload_data(navigation_decision_trace)
    navigation_evidence = _navigation_trace_evidence(navigation_decision_trace)
    readiness = _dict(visibility_data.get("actionReadiness")) or _dict(_dict(visibility_data.get("readiness")).get("actionReadiness"))
    readiness_status = readiness.get("status")
    execution_allowed = readiness.get("executionAllowed") is True
    readiness_blockers = _list(readiness.get("blockers"))
    readiness_warnings = _list(readiness.get("warnings"))
    readiness_summary = _dict(runtime_data.get("readinessSummary"))
    visibility_readiness = _dict(visibility_data.get("readiness"))
    manual_login = bool(
        readiness_summary.get("manualLoginRequired") is True
        or visibility_readiness.get("manualLoginRequired") is True
        or failure.get("primaryClassification") == "game-state/user-login blocker"
        or "manual_login_required" in _list(failure.get("blockers"))
    )
    loaded_scene = _first_present(
        _dict(readiness_summary.get("loadedSceneProof")).get("loadedSceneVerified"),
        _dict(visibility_readiness.get("loadedSceneProof")).get("loadedSceneVerified"),
        _dict(visibility_data.get("livenessRecoveryActions")).get("loadedSceneVerified"),
    )
    input_assessment = _dict(failure.get("inputIntegrityAssessment"))
    live_input_hard_blocker = bool(input_assessment.get("liveActionHardBlocker") is True or failure.get("status") == "FAIL")
    direct_bypass = input_assessment.get("directBackendBypassCount")
    expected_variables = _step_expected_variables(selected_step)
    expected_variable_proof = _expected_runtime_variable_proof(runtime_data, expected_variables)
    proof_blocked_expected = [
        str(item)
        for item in _list(expected_variable_proof.get("proofBlockedExpectedRuntimeVariables"))
        if str(item or "").strip()
    ]
    missing_now = [str(item) for item in _list(runtime_data.get("coveredVariablesMissingNow"))]
    missing_expected = [name for name in expected_variables if name in missing_now]
    nav_suspicious = _dict(nav_data.get("firstSuspiciousDecision"))
    nav_blocking_eligible = nav_data.get("blockingEligible") is not False
    blockers: list[str] = []
    warnings: list[str] = []
    if manual_login:
        blockers.append("manual_login_required")
    if live_input_hard_blocker:
        blockers.append("live_action_input_integrity_hard_blocker")
    if direct_bypass not in {None, 0}:
        blockers.append("directBackendBypassCount_nonzero")
    if live_capable and not execution_allowed:
        blockers.append("action_readiness_not_pass")
    if proof_blocked_expected:
        blockers.append("expected_runtime_variable_not_proof_eligible")
    if primitive_name == "recover_loaded_scene" and manual_login:
        blockers.append("manual_login_blocks_liveness_recovery")
    if primitive_name == "recover_loaded_scene" and loaded_scene is True:
        warnings.append("loaded_scene_already_verified")
    if primitive_name in {"walk_to", "return_to_resource"} and nav_suspicious and nav_blocking_eligible:
        blockers.append("suspicious_navigation_decision_trace")
    elif primitive_name in {"walk_to", "return_to_resource"} and nav_suspicious:
        warnings.append("diagnostic_navigation_decision_trace_not_blocking")
    warnings.extend(f"missing_runtime_variable:{name}" for name in missing_expected)
    warnings.extend(f"expected_runtime_variable_not_proof_eligible:{name}" for name in proof_blocked_expected)
    warnings.extend(str(item) for item in readiness_warnings)
    request_allowed = bool(not blockers)
    if live_capable:
        request_allowed = bool(request_allowed and execution_allowed)
    elif primitive_name == "recover_loaded_scene":
        recovery = _dict(visibility_data.get("livenessRecoveryActions"))
        request_allowed = bool(request_allowed and (loaded_scene is not True) and recovery.get("available") is not False)
    status = "FAIL" if live_input_hard_blocker else "PASS" if request_allowed and not warnings else "WARN"
    readiness_rule = (
        "live-capable primitive requires actionReadiness.executionAllowed=true, clean live-action input integrity, no manual login, and canonical executor path"
        if live_capable
        else "watcher/recovery primitive may request only the named bounded operator path and still respects manual-login and input-integrity blockers"
    )
    return {
        "schema": TASK_STEP_READINESS_SCHEMA,
        "status": status,
        "generatedAtUtc": utc_now(),
        "data": {
            "script": plan_data.get("script"),
            "selectedStep": selected_step,
            "compiledStepCount": len(steps),
            "primitive": primitive_name,
            "boundedOperatorRequest": bounded_request,
            "requestAllowedNow": request_allowed,
            "requestType": "bounded_operator_request" if bounded_request else None,
            "liveCapablePrimitive": live_capable,
            "watcherOrRecoveryPrimitive": watcher_or_recovery,
            "expectedRuntimeVariables": expected_variables,
            "missingExpectedRuntimeVariablesNow": missing_expected,
            "runtimeEvidenceIntegrity": runtime_data.get("runtimeEvidenceIntegrity"),
            "expectedRuntimeVariableProof": expected_variable_proof,
            "proofEligibleExpectedRuntimeVariablesNow": expected_variable_proof.get("proofEligibleExpectedRuntimeVariables"),
            "advisoryExpectedRuntimeVariablesNow": expected_variable_proof.get("advisoryExpectedRuntimeVariables"),
            "proofBlockedExpectedRuntimeVariablesNow": proof_blocked_expected,
            "readinessRule": readiness_rule,
            "actionReadiness": readiness,
            "actionReadinessStatus": readiness_status,
            "actionExecutionAllowed": execution_allowed,
            "readinessBlockers": readiness_blockers,
            "plannedAction": visibility_data.get("plannedAction"),
            "plannedTarget": visibility_data.get("plannedTarget"),
            "plannedScreenPoint": visibility_data.get("plannedScreenPoint"),
            "hoverConfirmationEvidence": visibility_data.get("hoverConfirmationEvidence"),
            "menuOptionClickedEvidence": visibility_data.get("menuOptionClickedEvidence"),
            "inputIntegrityAssessment": input_assessment,
            "failureClassification": {
                "schema": failure.get("schema"),
                "status": failure.get("status"),
                "primaryClassification": failure.get("primaryClassification"),
                "blockers": failure.get("blockers"),
            },
            "navigationDecisionTrace": navigation_evidence,
            "canonicalPipeline": CANONICAL_PIPELINE if live_capable else [],
            "preLivePhaseChecklist": [
                "STOP_ALL",
                "DISARM",
                "STATUS",
                "request_input_integrity_reset or rebaseline",
                "verify Arduino/Raw Input/COM status",
            ] if live_capable else [],
            "postLivePhaseChecklist": ["STOP_ALL", "DISARM", "STATUS"] if live_capable else [],
            "externalKnowledgePolicy": EXTERNAL_KNOWLEDGE_POLICY,
            "rawInputBypassToolsExposed": False,
            "noLiveInput": True,
        },
        "warnings": list(dict.fromkeys(warnings)),
        "blockers": list(dict.fromkeys(blockers)),
        "failureClassificationPolicy": FAILURE_CLASSIFICATIONS,
        "phaseAwareInputIntegrityPolicy": PHASE_AWARE_INPUT_POLICY,
        "noLiveInput": True,
    }


def assess_task_run_readiness(
    script: dict[str, Any] | str | Path,
    *,
    runtime_evidence: dict[str, Any] | None = None,
    action_input_visibility: dict[str, Any] | None = None,
    failure_classification: dict[str, Any] | None = None,
    navigation_decision_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = compile_task_script(script)
    if plan.get("status") != "PASS":
        return {
            "schema": TASK_RUN_READINESS_SCHEMA,
            "status": "FAIL",
            "generatedAtUtc": utc_now(),
            "validation": plan.get("validation"),
            "errors": _list(_dict(plan.get("validation")).get("errors")),
            "noLiveInput": True,
        }
    plan_data = _dict(plan.get("data"))
    steps = [_dict(item) for item in _list(plan_data.get("flattenedActionPlan")) if isinstance(item, dict)]
    failure = failure_classification or classify_task_failure(
        {
            "runtimeEvidence": runtime_evidence or {},
            "actionInputVisibility": action_input_visibility or {},
            "navigationDecisionTrace": navigation_decision_trace or {},
        }
    )
    inference = _infer_next_task_primitive(runtime_evidence, action_input_visibility, failure)
    next_primitive = str(inference.get("primitive") or "collect")
    next_readiness = assess_task_step_readiness(
        script,
        primitive=next_primitive,
        runtime_evidence=runtime_evidence,
        action_input_visibility=action_input_visibility,
        failure_classification=failure,
        navigation_decision_trace=navigation_decision_trace,
    )
    step_summaries = [
        {
            "sourcePath": step.get("sourcePath"),
            "stepIndex": step.get("stepIndex"),
            "primitive": step.get("primitive"),
            "actionProposalAction": step.get("actionProposalAction"),
            "boundedOperatorRequest": step.get("boundedOperatorRequest"),
            "target": step.get("target"),
            "expectedRuntimeVariables": _step_expected_variables(step),
            "liveCapablePrimitive": step.get("primitive") not in {"wait_for_evidence", "recover_loaded_scene", "repeat_until"},
            "isInferredNextStep": _norm(step.get("primitive")) == _norm(next_primitive),
        }
        for step in steps
    ]
    runtime_data = _runtime_data(runtime_evidence)
    variables = _runtime_variables(runtime_evidence)
    visibility_data = _payload_data(action_input_visibility)
    navigation_evidence = _navigation_trace_evidence(navigation_decision_trace)
    readiness_summary = _dict(runtime_data.get("readinessSummary"))
    lifecycle_integrity = _lifecycle_evidence_integrity(runtime_data, variables, visibility_data, failure)
    global_blockers = list(dict.fromkeys(_list(next_readiness.get("blockers")) + _list(failure.get("blockers"))))
    global_warnings = list(dict.fromkeys(_list(next_readiness.get("warnings")) + _list(failure.get("warnings")) + _list(lifecycle_integrity.get("warnings"))))
    request_allowed = bool(_dict(next_readiness.get("data")).get("requestAllowedNow") is True)
    status = (
        "FAIL"
        if next_readiness.get("status") == "FAIL" or failure.get("status") == "FAIL"
        else "PASS" if request_allowed and not global_blockers and not global_warnings else "WARN"
    )
    return {
        "schema": TASK_RUN_READINESS_SCHEMA,
        "status": status,
        "generatedAtUtc": utc_now(),
        "data": {
            "script": plan_data.get("script"),
            "compiledStepCount": len(steps),
            "stepSummaries": step_summaries,
            "inferredNextPrimitive": inference,
            "currentLifecycle": {
                "inferredNextPrimitive": next_primitive,
                "inferenceReason": inference.get("reason"),
                "inferenceConfidence": inference.get("confidence"),
                "inferenceEvidence": inference.get("evidence"),
                "evidenceIntegrity": lifecycle_integrity,
                "readinessSummary": readiness_summary,
                "inventory": _runtime_variable_value(variables, "inventory"),
                "resourceCount": _runtime_variable_value(variables, "resourceCount"),
                "bankOpen": _runtime_variable_value(variables, "bankOpen"),
                "routeProgress": _runtime_variable_value(variables, "routeProgress"),
                "routeMonitor": _runtime_variable_value(variables, "routeMonitor"),
                "phaseIntent": _runtime_variable_value(variables, "phaseIntent"),
                "woodcuttingLoopLifecycle": _runtime_variable_value(variables, "woodcuttingLoopLifecycle"),
                "interruptionLifecycle": _runtime_variable_value(variables, "interruptionLifecycle"),
                "depositResult": _runtime_variable_value(variables, "depositResult"),
            },
            "nextStepReadiness": next_readiness,
            "requestAllowedNow": request_allowed,
            "boundedOperatorRequest": _dict(next_readiness.get("data")).get("boundedOperatorRequest"),
            "canonicalPipeline": CANONICAL_PIPELINE,
            "rawInputBypassToolsExposed": False,
            "directBackendBypassCountMustRemain": 0,
            "actionInputVisibilityEvidence": {
                "schema": action_input_visibility.get("schema") if isinstance(action_input_visibility, dict) else None,
                "status": action_input_visibility.get("status") if isinstance(action_input_visibility, dict) else None,
                "plannedAction": visibility_data.get("plannedAction"),
                "plannedTarget": visibility_data.get("plannedTarget"),
                "plannedScreenPoint": visibility_data.get("plannedScreenPoint"),
                "coordinateConversionTrace": visibility_data.get("coordinateConversionTrace"),
                "displayScaleApplied": visibility_data.get("displayScaleApplied"),
                "displayScaleReason": visibility_data.get("displayScaleReason"),
                "arduinoCalibrationStatus": visibility_data.get("arduinoCalibrationStatus"),
                "humanInputController": visibility_data.get("humanInputController"),
                "cursorMovementTrace": visibility_data.get("cursorMovementTrace"),
                "hoverConfirmationEvidence": visibility_data.get("hoverConfirmationEvidence"),
                "menuOptionClickedEvidence": visibility_data.get("menuOptionClickedEvidence"),
                "input_integrity_status": visibility_data.get("input_integrity_status"),
                "inputBlockEvidence": visibility_data.get("inputBlockEvidence"),
                "directBackendBypassCount": visibility_data.get("directBackendBypassCount"),
                "lastClickProof": visibility_data.get("lastClickProof"),
                "lastMovementProof": visibility_data.get("lastMovementProof"),
                "blockedReason": visibility_data.get("blockedReason"),
                "actionReadiness": visibility_data.get("actionReadiness"),
                "latestActionTraceSummary": visibility_data.get("latestActionTraceSummary"),
                "latestDebugBundle": visibility_data.get("latestDebugBundle"),
                "livenessRecoveryActions": visibility_data.get("livenessRecoveryActions"),
                "boundedWatcherDecisions": visibility_data.get("boundedWatcherDecisions"),
                "target_view_state": visibility_data.get("target_view_state"),
                "serviceResourceRouteCandidateState": visibility_data.get("serviceResourceRouteCandidateState"),
                "readinessActionEvidence": visibility_data.get("readinessActionEvidence"),
            },
            "failureClassification": {
                "schema": failure.get("schema"),
                "status": failure.get("status"),
                "primaryClassification": failure.get("primaryClassification"),
                "blockers": failure.get("blockers"),
            },
            "navigationDecisionTrace": navigation_evidence,
            "lifecycleRule": "The inferred next primitive is advisory; bounded live action still requires task_step_readiness.requestAllowedNow and fresh live evidence.",
            "noLiveInput": True,
        },
        "warnings": global_warnings,
        "blockers": global_blockers,
        "phaseAwareInputIntegrityPolicy": PHASE_AWARE_INPUT_POLICY,
        "externalKnowledgePolicy": EXTERNAL_KNOWLEDGE_POLICY,
        "noLiveInput": True,
    }


def woodcut_bank_template() -> dict[str, Any]:
    return {
        "schema": TASK_SCRIPT_SCHEMA,
        "name": "woodcut_bank",
        "description": "Collect woodcutting resources, bank them, close the bank, and return to the resource area through the existing engine.",
        "profile": "woodcutting",
        "task": "woodcutting",
        "policy": "woodcutting_bank",
        "externalKnowledge": {
            "use": "advisory_static_enrichment_only",
            "sourceExamples": ["OSRS Wiki item/object IDs", "skill requirements", "area labels", "route/service priors"],
            "cacheFirst": True,
            "hotExecutorExternalCallsAllowed": False,
        },
        "steps": [
            {
                "primitive": "recover_loaded_scene",
                "when": "loaded_scene_stale",
                "evidence": ["loaded_scene_verified", "client_tick_fresh", "world_model_fresh"],
            },
            {
                "primitive": "repeat_until",
                "condition": "inventory_full",
                "maxIterations": 28,
                "evidence": ["inventory_state_rechecked_from_live_context"],
                "steps": [
                    {
                        "primitive": "collect",
                        "targetProfile": "woodcutting",
                        "targetClass": "tree",
                        "action": "Chop down",
                        "evidence": [
                            "resource_candidate_live",
                            "safe_aimpoint_pass",
                            "hover_menu_contains_Chop_down",
                            "MenuOptionClicked_Chop_down_or_resource_progress",
                        ],
                    },
                    {
                        "primitive": "wait_for_evidence",
                        "evidence": ["resource_progress", "inventory_free_slots_decrease", "target_depleted_or_next_candidate"],
                        "timeoutTicks": 12,
                    },
                ],
            },
            {
                "primitive": "bank",
                "serviceType": "bank",
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "evidence": ["service_candidate_live", "route_or_waypoint_progress", "bank_ui_open"],
            },
            {
                "primitive": "deposit",
                "operation": "deposit_inventory",
                "evidence": ["bank_ui_open", "deposit_inventory_available", "no_resource_items_held", "banking_complete"],
            },
            {
                "primitive": "close_bank",
                "evidence": ["close_bank_ready", "bank_ui_closed"],
            },
            {
                "primitive": "return_to_resource",
                "resourceProfile": "woodcutting",
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "evidence": ["return_destination_available", "worksite_reacquired", "resource_candidate_live"],
            },
            {
                "primitive": "wait_for_evidence",
                "evidence": ["action_readiness_pass", "resource_target_visible"],
                "timeoutTicks": 10,
            },
        ],
    }


def suggest_task_template(task_description: str | None = None, *, profile: str | None = None) -> dict[str, Any]:
    text = f"{task_description or ''} {profile or ''}".lower()
    if any(token in text for token in ("woodcut", "tree", "logs", "bank", "deposit")):
        template = woodcut_bank_template()
        template_name = "woodcut_bank"
        reason = "description_matches_woodcut_bank"
    else:
        template = {
            "schema": TASK_SCRIPT_SCHEMA,
            "name": "new_task",
            "description": task_description or "New high-level task",
            "profile": profile or "observe",
            "task": profile or "observe",
            "policy": task_policy.default_policy_name(profile=profile),
            "steps": [
                {"primitive": "recover_loaded_scene", "when": "loaded_scene_stale"},
                {"primitive": "wait_for_evidence", "evidence": ["loaded_scene_verified", "action_readiness_pass"], "timeoutTicks": 10},
            ],
        }
        template_name = "generic_observe_first"
        reason = "generic_safe_observe_template"
    validation = validate_task_script(template)
    plan = compile_task_script(template)
    return {
        "schema": TASK_TEMPLATE_SUGGESTION_SCHEMA,
        "status": validation["status"],
        "generatedAtUtc": utc_now(),
        "data": {
            "templateName": template_name,
            "reason": reason,
            "template": template,
            "validation": validation,
            "compiledPlanSummary": {
                "schema": plan.get("schema"),
                "status": plan.get("status"),
                "actionProposalActions": _dict(plan.get("data")).get("actionProposalActions"),
                "noLiveInput": True,
            },
            "source": {
                "type": "built_in_template",
                "provenance": "telemetry-viewer/task_script_api.py",
                "externalKnowledge": EXTERNAL_KNOWLEDGE_POLICY,
            },
        },
        "noLiveInput": True,
    }


def script_api_spec() -> dict[str, Any]:
    return {
        "schema": TASK_SCRIPT_SPEC_SCHEMA,
        "generatedAtUtc": utc_now(),
        "scriptSchema": TASK_SCRIPT_SCHEMA,
        "allowedPrimitives": list(ALLOWED_PRIMITIVES),
        "primitives": PRIMITIVE_SPECS,
        "canonicalPipeline": CANONICAL_PIPELINE,
        "boundedOperatorRequests": BOUNDED_OPERATOR_REQUESTS,
        "runtimeEvidenceVariables": RUNTIME_EVIDENCE_VARIABLES,
        "primitiveRuntimeExpectations": PRIMITIVE_RUNTIME_EXPECTATIONS,
        "runtimeEvidenceComparisonSchema": TASK_RUNTIME_EVIDENCE_COMPARISON_SCHEMA,
        "failureClassificationSchema": TASK_FAILURE_CLASSIFICATION_SCHEMA,
        "stepReadinessSchema": TASK_STEP_READINESS_SCHEMA,
        "taskRunReadinessSchema": TASK_RUN_READINESS_SCHEMA,
        "forbiddenRawInputPrimitives": sorted(RAW_INPUT_PRIMITIVES),
        "forbiddenRawInputFields": sorted(RAW_INPUT_FIELDS),
        "phaseAwareInputIntegrityPolicy": PHASE_AWARE_INPUT_POLICY,
        "externalKnowledgePolicy": EXTERNAL_KNOWLEDGE_POLICY,
        "failureClassifications": FAILURE_CLASSIFICATIONS,
        "scriptFacingHelpers": [
            "get_bank_state",
            "get_banking_lifecycle",
            "is_bank_open",
            "is_deposit_box_open",
            "get_active_bank_like_interface",
            "get_inventory_delta",
            "get_deposit_result",
            "get_deposited_items",
            "did_deposit_item",
            "get_banking_missing_capabilities",
            "get_woodcutting_lifecycle",
            "get_route_monitor_status",
            "get_route_state",
            "get_current_route_segment",
            "get_next_route_segment",
            "is_off_route",
            "get_route_demonstration_guide",
            "get_route_guide_progress",
            "get_route_guide_reentry",
            "get_human_click_profile",
            "get_task_click_profile",
            "get_click_landing_profile",
            "get_camera_action_profile",
            "get_click_planning_context",
            "get_human_click_plan",
            "get_next_click_plan",
            "get_combat_state",
            "is_in_combat",
            "get_interruption_lifecycle",
            "was_task_interrupted",
            "get_interruption_cause",
            "get_combat_damage_summary",
            "get_damage_taken",
            "get_damage_dealt",
            "get_primary_opponent",
            "did_take_damage",
            "did_deal_damage",
            "get_recent_combat_window",
            "get_recent_hitsplats",
            "get_recent_stat_changes",
            "get_recent_game_messages",
            "get_woodcutting_loop_lifecycle",
            "get_current_task_phase",
            "get_next_expected_phase",
            "is_inventory_full_for_woodcutting",
            "did_deposit_logs",
            "should_route_to_bank",
            "should_route_to_trees",
            "was_interrupted",
            "did_resume_after_interruption",
            "did_task_resume",
        ],
        "example": woodcut_bank_template(),
    }

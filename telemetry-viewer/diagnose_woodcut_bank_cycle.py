from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any


SCHEMA = "woodcut_bank_cycle_diagnostic.v1"


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload)
    return decoded if isinstance(decoded, dict) else {}


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


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


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "on", "ready", "open", "visible", "available", "complete"}:
            return True
        if text in {"false", "no", "0", "off", "not_ready", "closed", "hidden", "unavailable", "incomplete"}:
            return False
    return None


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_strings(value: Any) -> list[str]:
    return [str(item) for item in as_list(value) if item is not None]


def get_path(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def target_label(target: dict[str, Any] | None) -> str | None:
    if not isinstance(target, dict) or not target:
        return None
    return str(
        target.get("targetName")
        or target.get("name")
        or target.get("label")
        or target.get("classId")
        or target.get("targetType")
        or target.get("id")
        or "target"
    )


def compact_target(target: Any) -> dict[str, Any] | None:
    if not isinstance(target, dict) or not target:
        return None
    keys = (
        "targetName",
        "name",
        "targetType",
        "classId",
        "type",
        "group",
        "id",
        "objectKey",
        "worldX",
        "worldY",
        "plane",
        "distanceTiles",
        "navigationStatus",
        "navigationReason",
    )
    compact = {key: target.get(key) for key in keys if target.get(key) is not None}
    compact["label"] = target_label(target)
    return compact


def selected_overlay_from(brain: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    overlay = dict_value(brain.get("intentOverlayContext"))
    selected = dict_value(overlay.get("selectedMarker") or overlay.get("selectedTarget"))
    markers = as_list(overlay.get("markers"))
    if not selected:
        selected = next((marker for marker in markers if isinstance(marker, dict) and marker.get("markerType") == "selected_target"), {})
    if not selected:
        selected_label = status.get("stabilizedIntentTargetLabel") or status.get("stabilizedIntentTarget")
        if selected_label:
            selected = {"targetName": selected_label}
    return {
        "selected": compact_target(selected),
        "markerCount": first_present(overlay.get("intentMarkerCount"), get_path(overlay, "summary", "intentMarkerCount"), len(markers) if markers else None),
        "pathMarkerCount": first_present(
            overlay.get("pathMarkersEmitted"),
            get_path(overlay, "summary", "pathMarkersEmitted"),
            get_path(overlay, "summary", "pathingMarkerCount"),
        ),
    }


def service_target_from(service: dict[str, Any], generic: dict[str, Any]) -> dict[str, Any] | None:
    return compact_target(
        service.get("bestServiceCandidate")
        or service.get("bestServiceTarget")
        or service.get("target")
        or generic.get("activeIntentTarget")
    )


def progress_from(status: dict[str, Any], brain: dict[str, Any]) -> dict[str, Any]:
    progress = dict_value(brain.get("goalProgress")) or dict_value(status.get("brainProgress"))
    return {
        "displayedGoalProgress": progress.get("displayedGoalProgress"),
        "goalCount": progress.get("goalCount", status.get("brainGoalCount")),
        "currentHeldCount": progress.get("currentHeldCount"),
        "resourceGroup": progress.get("resourceGroup"),
        "complete": progress.get("complete"),
        "baselineEstablished": progress.get("baselineEstablished"),
    }


def inventory_from(status: dict[str, Any], brain: dict[str, Any], bank_operation: dict[str, Any], return_context: dict[str, Any]) -> dict[str, Any]:
    inventory = dict_value(brain.get("inventoryContext"))
    raw_inventory = dict_value(inventory.get("inventory"))
    progress = dict_value(inventory.get("progress"))
    free_slots = first_present(
        inventory.get("freeSlots"),
        inventory.get("inventoryFreeSlots"),
        raw_inventory.get("freeSlots"),
        bank_operation.get("inventoryFreeSlots"),
        return_context.get("inventoryFreeSlots"),
        status.get("inventoryFreeSlots"),
        status.get("returnInventoryFreeSlots"),
    )
    full = first_present(
        inventory.get("inventoryFull"),
        raw_inventory.get("inventoryFull"),
        status.get("inventoryFull"),
        status.get("returnInventoryFull"),
    )
    occupied = first_present(
        inventory.get("occupiedSlots"),
        inventory.get("filledSlots"),
        raw_inventory.get("occupiedSlots"),
        raw_inventory.get("filledSlots"),
        None if free_slots is None else max(0, 28 - int(free_slots)) if isinstance(free_slots, int) else None,
    )
    resource_held = first_present(
        bank_operation.get("resourceItemsHeld"),
        status.get("bankResourceItemsHeld"),
        progress.get("currentHeldCount"),
        status.get("currentHeldResourceCount"),
    )
    resource_quantity = first_present(
        bank_operation.get("resourceItemQuantity"),
        status.get("bankResourceItemQuantity"),
        progress.get("currentHeldCount"),
    )
    return {
        "inventoryFull": as_bool(full),
        "freeSlots": free_slots,
        "occupiedSlots": occupied,
        "resourceItemsHeld": resource_held,
        "resourceItemQuantity": resource_quantity,
    }


def status_from(warnings: list[str], missing_capabilities: list[str], missing_required: list[str]) -> str:
    if missing_required or "daemon.status" in missing_capabilities:
        return "FAIL"
    if warnings or missing_capabilities:
        return "WARN"
    return "PASS"


def classify_cycle_stage(payload: dict[str, Any]) -> str:
    generic = dict_value(payload.get("generic"))
    inventory = dict_value(payload.get("inventory"))
    service = dict_value(payload.get("service"))
    pathing = dict_value(payload.get("pathing"))
    bank = dict_value(payload.get("bank"))
    bank_operation = dict_value(payload.get("bankOperation"))
    close_bank = dict_value(payload.get("closeBank"))
    post_bank = dict_value(payload.get("postBank"))
    return_context = dict_value(payload.get("returnToResource"))
    resource_return = dict_value(payload.get("resourceReturn"))
    overlay = dict_value(payload.get("overlay"))
    selected_overlay = dict_value(overlay.get("selected"))

    blocking = list_strings(generic.get("blockingConditions"))
    bank_pin_open = as_bool(bank.get("bankPinOpen")) is True
    user_resolution_blocked = (
        generic.get("activeIntent") == "needs_user_resolution"
        or any(str(item) in {"bank_pin_required", "needs_user_resolution"} or "user" in str(item).lower() for item in blocking)
    )
    if bank_pin_open or user_resolution_blocked:
        return "blocked"

    bank_readable = as_bool(bank.get("bankReadable")) is True
    resource_items_held = bank_operation.get("resourceItemsHeld", inventory.get("resourceItemsHeld"))
    try:
        held_number = int(resource_items_held) if resource_items_held is not None else None
    except (TypeError, ValueError):
        held_number = None
    if bank_readable and held_number is not None and held_number > 0:
        return "bank_operation_pending"

    banking_complete = as_bool(bank_operation.get("bankingComplete")) is True
    bank_open = as_bool(bank.get("bankOpen"))
    close_bank_needed = as_bool(close_bank.get("closeBankNeeded"))
    if banking_complete and bank_open is True and close_bank_needed is None and post_bank.get("reason") != "bank_ui_still_open":
        return "banking_complete"
    if close_bank_needed is True:
        return "close_bank_needed"
    if post_bank.get("reason") == "bank_ui_still_open":
        return "waiting_for_world_view"
    if banking_complete and bank_open is False:
        target_available = as_bool(return_context.get("resourceTargetAvailable")) is True
        return_destination_available = as_bool(resource_return.get("returnDestinationAvailable")) is True
        active_target = dict_value(generic.get("activeIntentTarget"))
        selected_resource = (selected_overlay.get("classId") or active_target.get("classId")) in {"tree", "woodcutting_tree"}
        if target_available or selected_resource:
            return "resource_target_selected"
        if return_destination_available:
            return "return_to_resource"
        return "return_to_resource"

    service_ready = as_bool(service.get("serviceReady")) is True or as_bool(pathing.get("serviceReady")) is True
    if service_ready and bank_open is False:
        return "service_ready"
    if as_bool(pathing.get("pathingNeeded")) is True:
        return "pathing_to_service"
    if as_bool(inventory.get("inventoryFull")) is True and as_bool(service.get("serviceNeeded")) is True:
        return "needs_service"
    if as_bool(inventory.get("inventoryFull")) is True:
        return "inventory_full"

    active_target = dict_value(generic.get("activeIntentTarget"))
    active_class = active_target.get("classId") or selected_overlay.get("classId")
    active_intent = generic.get("activeIntent")
    if active_class in {"tree", "woodcutting_tree"} and as_bool(inventory.get("inventoryFull")) is not True:
        return "collecting_resources"
    if active_intent in {"target_selected", "continue_current_target", "continue_task", "select_target"} and as_bool(inventory.get("inventoryFull")) is not True:
        return "collecting_resources"
    return "unknown"


def build_from_daemon(status: dict[str, Any]) -> dict[str, Any]:
    brain = dict_value(status.get("brain"))
    generic = dict_value(brain.get("genericTaskState"))
    service_context = dict_value(brain.get("serviceContext"))
    pathing_context = dict_value(brain.get("pathingContext"))
    bank_ui = dict_value(brain.get("bankUiContext"))
    bank_operation = dict_value(brain.get("bankOperationContext"))
    close_bank = dict_value(brain.get("closeBankContext"))
    post_bank = dict_value(brain.get("postBankReacquisitionContext"))
    return_context = dict_value(brain.get("returnToResourceContext"))
    resource_return = dict_value(brain.get("resourceReturnContext"))
    history = dict_value(status.get("cycleHistory"))
    policy_payload = dict_value(generic.get("taskPolicy") or brain.get("taskPolicy"))
    progress = progress_from(status, brain)
    inventory = inventory_from(status, brain, bank_operation, return_context)
    service_target = service_target_from(service_context, generic)
    overlay = selected_overlay_from(brain, status)
    missing_required = list_strings(brain.get("missingRequiredContextDomains") or status.get("missingRequiredContextDomains"))
    optional_missing = list_strings(brain.get("optionalMissingContextDomains") or status.get("optionalMissingContextDomains"))
    if as_bool(resource_return.get("returnDestinationAvailable")) is True:
        missing_required = [domain for domain in missing_required if domain not in {"target.candidates", "target.freshness"}]
        for domain in ("target.candidates", "target.freshness"):
            if domain not in optional_missing:
                optional_missing.append(domain)
    missing_capabilities = sorted(
        set(
            missing_required
            + list_strings(service_context.get("missingCapabilities"))
            + list_strings(bank_ui.get("missingCapabilities"))
            + list_strings(bank_operation.get("missingCapabilities"))
            + list_strings(close_bank.get("missingCapabilities"))
            + list_strings(post_bank.get("missingCapabilities"))
            + list_strings(return_context.get("missingCapabilities"))
            + list_strings(resource_return.get("missingCapabilities"))
        )
    )
    warnings = list_strings(status.get("warnings")) + list_strings(brain.get("warnings"))
    for context in (service_context, bank_ui, bank_operation, close_bank, post_bank, return_context, resource_return):
        warnings.extend(list_strings(context.get("warnings")))
    if as_bool(bank_ui.get("bankPinOpen")) is True and "bank_pin_required" not in warnings:
        warnings.append("bank_pin_required")
    if optional_missing:
        warnings.append("optional context missing: " + ", ".join(optional_missing))
    service_ready = first_present(service_context.get("serviceReady"), pathing_context.get("serviceReady"), status.get("serviceReady"))
    pathing_needed = first_present(pathing_context.get("pathingNeeded"), status.get("pathingNeeded"))
    path_completed = first_present(pathing_context.get("pathCompleted"), status.get("pathingCompleted"))
    bank_open = first_present(bank_ui.get("bankOpen"), close_bank.get("bankOpen"), status.get("bankOpen"))
    bank_readable = first_present(bank_ui.get("bankReadable"), status.get("bankReadable"))
    bank_pin_open = first_present(bank_ui.get("bankPinOpen"), status.get("bankPinOpen"))
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "daemonReachable": True,
        "activeTask": generic.get("task") or brain.get("task") or status.get("brainTask") or status.get("activeTask"),
        "taskPolicy": status.get("brainTaskPolicy") or status.get("taskPolicy") or policy_payload.get("name"),
        "missionPreset": status.get("activeMissionPreset") or status.get("missionPreset"),
        "phase": generic.get("phase") or brain.get("phase") or status.get("genericPhase") or status.get("brainPhase"),
        "activeIntent": generic.get("activeIntent") or status.get("activeIntent"),
        "reason": None,
        "currentCycleStageStableForTicks": first_present(
            history.get("currentCycleStageStableForTicks"),
            status.get("currentCycleStageStableForTicks"),
        ),
        "previousCycleStage": first_present(history.get("lastCycleStage"), status.get("lastCycleStage")),
        "lastCycleTransitionReason": first_present(
            history.get("lastCycleTransitionReason"),
            status.get("lastCycleTransitionReason"),
        ),
        "progress": progress,
        "inventory": inventory,
        "inventoryFull": inventory.get("inventoryFull"),
        "inventoryFreeSlots": inventory.get("freeSlots"),
        "resourceItemsHeld": inventory.get("resourceItemsHeld"),
        "service": {
            "target": service_target,
            "targetName": target_label(service_target),
            "targetType": service_target.get("classId") if isinstance(service_target, dict) else None,
            "serviceNeeded": first_present(service_context.get("serviceNeeded"), status.get("serviceNeeded")),
            "serviceReady": service_ready,
            "serviceReadyReason": first_present(service_context.get("serviceReadyReason"), pathing_context.get("serviceReadyReason"), status.get("serviceReadyReason")),
        },
        "serviceReady": service_ready,
        "pathing": {
            "pathingNeeded": pathing_needed,
            "pathCompleted": path_completed,
            "destination": compact_target(pathing_context.get("destination") or pathing_context.get("destinationTarget")),
            "pathLengthTiles": first_present(pathing_context.get("pathLengthTiles"), status.get("pathingPathLengthTiles")),
            "nextWaypointTile": pathing_context.get("nextWaypointTile") or status.get("pathingNextWaypointTile"),
        },
        "pathingNeeded": pathing_needed,
        "pathCompleted": path_completed,
        "bank": {
            "bankOpen": bank_open,
            "bankReadable": bank_readable,
            "bankPinOpen": bank_pin_open,
            "topLevelInterfaceId": bank_ui.get("topLevelInterfaceId") or status.get("bankTopLevelInterfaceId"),
        },
        "bankOpen": bank_open,
        "bankReadable": bank_readable,
        "bankPinOpen": bank_pin_open,
        "bankOperation": {
            "operationNeeded": first_present(bank_operation.get("operationNeeded"), status.get("bankOperationNeeded")),
            "operationType": first_present(bank_operation.get("operationType"), status.get("bankOperationType")),
            "resourceItemsHeld": first_present(bank_operation.get("resourceItemsHeld"), status.get("bankResourceItemsHeld"), inventory.get("resourceItemsHeld")),
            "resourceItemQuantity": first_present(bank_operation.get("resourceItemQuantity"), status.get("bankResourceItemQuantity"), inventory.get("resourceItemQuantity")),
            "bankingComplete": first_present(bank_operation.get("bankingComplete"), status.get("bankingComplete")),
            "completionReason": first_present(bank_operation.get("completionReason"), status.get("bankOperationCompletionReason")),
        },
        "closeBank": {
            "closeBankNeeded": first_present(close_bank.get("closeBankNeeded"), status.get("closeBankNeeded")),
            "closeBankReady": first_present(close_bank.get("closeBankReady"), status.get("closeBankReady")),
            "closeButtonAvailable": first_present(close_bank.get("closeButtonAvailable"), status.get("closeBankCloseButtonAvailable")),
            "reason": first_present(close_bank.get("reason"), status.get("closeBankReason")),
        },
        "closeBankNeeded": first_present(close_bank.get("closeBankNeeded"), status.get("closeBankNeeded")),
        "closeBankReady": first_present(close_bank.get("closeBankReady"), status.get("closeBankReady")),
        "postBank": {
            "reason": first_present(post_bank.get("reason"), status.get("postBankReacquisitionReason")),
            "worldViewReady": first_present(post_bank.get("worldViewReady"), status.get("postBankWorldViewReady")),
            "resourceTargetReacquisitionAllowed": first_present(
                post_bank.get("resourceTargetReacquisitionAllowed"),
                status.get("postBankResourceTargetReacquisitionAllowed"),
            ),
            "resourceTargetAvailable": first_present(post_bank.get("resourceTargetAvailable"), status.get("postBankResourceTargetAvailable")),
        },
        "returnToResource": {
            "returnNeeded": first_present(return_context.get("returnNeeded"), status.get("returnToResourceNeeded")),
            "returnReady": first_present(return_context.get("returnReady"), status.get("returnToResourceReady")),
            "resourceTargetAvailable": first_present(return_context.get("resourceTargetAvailable"), status.get("returnResourceTargetAvailable")),
            "bestResourceTarget": target_label(return_context.get("bestResourceTarget") if isinstance(return_context.get("bestResourceTarget"), dict) else status.get("returnBestResourceTarget")),
            "resourcePathingNeeded": first_present(return_context.get("resourcePathingNeeded"), status.get("returnResourcePathingNeeded")),
            "reason": first_present(return_context.get("reason"), status.get("returnToResourceReason")),
        },
        "resourceReturn": {
            "returnDestinationNeeded": first_present(resource_return.get("returnDestinationNeeded"), status.get("resourceReturnDestinationNeeded")),
            "returnDestinationAvailable": first_present(resource_return.get("returnDestinationAvailable"), status.get("resourceReturnDestinationAvailable")),
            "returnDestinationTile": first_present(resource_return.get("returnDestinationTile"), status.get("resourceReturnDestinationTile")),
            "returnDestinationSource": first_present(resource_return.get("returnDestinationSource"), status.get("resourceReturnDestinationSource")),
            "resourceMemoryValid": first_present(resource_return.get("resourceMemoryValid"), status.get("resourceMemoryValid")),
            "resourceMemoryAgeTicks": first_present(resource_return.get("resourceMemoryAgeTicks"), status.get("resourceMemoryAgeTicks")),
            "reason": first_present(resource_return.get("reason"), status.get("resourceReturnReason")),
        },
        "overlay": overlay,
        "generic": {
            "activeIntentTarget": compact_target(generic.get("activeIntentTarget")),
            "blockingConditions": list_strings(generic.get("blockingConditions")),
        },
        "requiredContextDomains": list_strings(brain.get("requiredContextDomains") or status.get("requiredContextDomains")),
        "missingRequiredContextDomains": missing_required,
        "optionalMissingContextDomains": optional_missing,
        "missingCapabilities": missing_capabilities,
        "warnings": list(dict.fromkeys(warnings)),
        "noActionEmitted": first_present(brain.get("noActionEmitted"), status.get("noActionEmitted"), True),
    }
    payload["cycleStage"] = classify_cycle_stage(payload)
    payload["reason"] = cycle_reason(payload)
    payload["status"] = status_from(payload["warnings"], payload["missingCapabilities"], missing_required)
    payload.pop("generic", None)
    return payload


def cycle_reason(payload: dict[str, Any]) -> str:
    stage = payload.get("cycleStage")
    if stage == "blocked":
        return "bank_pin_required" if as_bool(payload.get("bankPinOpen")) is True else "blocked_condition"
    if stage == "bank_operation_pending":
        return str(dict_value(payload.get("bankOperation")).get("operationType") or "resources_still_held")
    if stage == "close_bank_needed":
        return str(dict_value(payload.get("closeBank")).get("reason") or "close_bank_needed")
    if stage == "waiting_for_world_view":
        return str(dict_value(payload.get("postBank")).get("reason") or "waiting_for_world_view")
    if stage == "return_to_resource":
        return str(
            dict_value(payload.get("resourceReturn")).get("reason")
            or dict_value(payload.get("returnToResource")).get("reason")
            or dict_value(payload.get("postBank")).get("reason")
            or "return_to_resource"
        )
    if stage == "resource_target_selected":
        return "resource_target_available"
    if stage == "service_ready":
        return str(dict_value(payload.get("service")).get("serviceReadyReason") or "service_ready")
    if stage == "pathing_to_service":
        return "pathing_to_service"
    if stage == "needs_service":
        return "inventory_full_needs_service"
    if stage == "inventory_full":
        return "inventory_full"
    if stage == "collecting_resources":
        return "resource_target_selected"
    if stage == "banking_complete":
        return str(dict_value(payload.get("bankOperation")).get("completionReason") or "banking_complete")
    return "unknown"


def unavailable_payload(error: Exception | str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "daemonReachable": False,
        "status": "FAIL",
        "cycleStage": "unknown",
        "phase": None,
        "activeIntent": None,
        "warnings": [str(error)],
        "missingCapabilities": ["daemon.status"],
        "noActionEmitted": True,
    }


def format_human(payload: dict[str, Any]) -> str:
    inventory = dict_value(payload.get("inventory"))
    service = dict_value(payload.get("service"))
    pathing = dict_value(payload.get("pathing"))
    bank = dict_value(payload.get("bank"))
    bank_operation = dict_value(payload.get("bankOperation"))
    close_bank = dict_value(payload.get("closeBank"))
    post_bank = dict_value(payload.get("postBank"))
    return_context = dict_value(payload.get("returnToResource"))
    resource_return = dict_value(payload.get("resourceReturn"))
    overlay = dict_value(payload.get("overlay"))
    selected = dict_value(overlay.get("selected"))
    progress = dict_value(payload.get("progress"))
    lines = [
        f"WOODCUT BANK CYCLE - {payload.get('status') or 'UNKNOWN'}",
        "",
        "Cycle:",
        f"  Stage: {payload.get('cycleStage') or 'unknown'}",
        f"  Phase: {payload.get('phase') or 'unknown'}",
        f"  Active intent: {payload.get('activeIntent') or 'unknown'}",
        f"  Reason: {payload.get('reason') or 'unknown'}",
        f"  Stable for ticks: {value_label(payload.get('currentCycleStageStableForTicks'))}",
        f"  Previous stage: {payload.get('previousCycleStage') or 'unknown'}",
        f"  Last transition: {payload.get('lastCycleTransitionReason') or 'unknown'}",
        "",
        "Inventory:",
        f"  Free slots: {value_label(inventory.get('freeSlots'))}",
        f"  Resource held: {value_label(inventory.get('resourceItemsHeld'))}",
        f"  Progress: {value_label(progress.get('displayedGoalProgress'))} / {value_label(progress.get('goalCount'))}",
        "",
        "Service:",
        f"  Target: {service.get('targetName') or 'none'}",
        f"  Ready: {bool_label(service.get('serviceReady'))}",
        f"  Pathing: needed={bool_label(pathing.get('pathingNeeded'))} complete={bool_label(pathing.get('pathCompleted'))}",
        "",
        "Bank:",
        f"  Open: {bool_label(bank.get('bankOpen'))}",
        f"  Readable: {bool_label(bank.get('bankReadable'))}",
        f"  Operation: {bank_operation.get('operationType') or 'none'} needed={bool_label(bank_operation.get('operationNeeded'))}",
        f"  Complete: {bool_label(bank_operation.get('bankingComplete'))}",
        f"  Close needed: {bool_label(close_bank.get('closeBankNeeded'))} ready={bool_label(close_bank.get('closeBankReady'))}",
        "",
        "Return:",
        f"  World ready: {bool_label(post_bank.get('worldViewReady'))}",
        f"  Resource target: {return_context.get('bestResourceTarget') or 'none'} available={bool_label(return_context.get('resourceTargetAvailable'))}",
        f"  Reacquisition: allowed={bool_label(post_bank.get('resourceTargetReacquisitionAllowed'))} reason={post_bank.get('reason') or 'unknown'}",
        f"  Return destination: available={bool_label(resource_return.get('returnDestinationAvailable'))} source={resource_return.get('returnDestinationSource') or 'none'} tile={value_label(resource_return.get('returnDestinationTile'))}",
        "",
        "Overlay:",
        f"  Selected: {selected.get('label') or selected.get('targetName') or 'none'}",
        f"  Markers/path: markers={value_label(overlay.get('markerCount'))} path={value_label(overlay.get('pathMarkerCount'))}",
        "",
        "Cycle history:",
        "  Run: python telemetry-viewer\\diagnose_cycle_history.py --from-daemon --daemon-url http://127.0.0.1:8890",
        "",
        "Warnings:",
    ]
    warnings = list_strings(payload.get("warnings"))
    failures = list_strings(payload.get("missingRequiredContextDomains"))
    if not warnings and not failures:
        lines.append("  none")
    for failure in failures:
        lines.append(f"  FAIL: missing required context {failure}")
    for warning in warnings:
        lines.append(f"  WARN: {warning}")
    missing = list_strings(payload.get("missingCapabilities"))
    lines.extend(["", f"Missing capabilities: {', '.join(missing) if missing else 'none'}"])
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only full woodcut bank cycle diagnostic. Prints to stdout only.")
    parser.add_argument("--from-daemon", action="store_true", help="Read current live daemon memory/status.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true", help="Print compact JSON to stdout only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.from_daemon:
        payload = {
            "schema": SCHEMA,
            "source": "not_requested",
            "daemonReachable": False,
            "status": "FAIL",
            "cycleStage": "unknown",
            "warnings": ["pass --from-daemon to read live daemon woodcut bank cycle context"],
            "missingCapabilities": ["daemon.status"],
            "noActionEmitted": True,
        }
        print(json.dumps(payload, indent=2) if args.json else format_human(payload), end="")
        return 1
    try:
        payload = build_from_daemon(fetch_json(daemon_status_url(args.daemon_url), timeout=args.timeout))
        code = 0
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        payload = unavailable_payload(f"{type(error).__name__}: {error}")
        code = 1
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_human(payload), end="")
    return code if payload.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

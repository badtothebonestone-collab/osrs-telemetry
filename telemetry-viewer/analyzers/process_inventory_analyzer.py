from __future__ import annotations

import time
from typing import Any

import capabilities
import task_policy as task_policy_module

from analyzers.live_state import InventoryContext, ProcessInventoryContext

TINDERBOX_ITEM_IDS = {590}


def held_resource_count(inventory: InventoryContext | dict[str, Any] | None) -> int | None:
    if isinstance(inventory, InventoryContext):
        progress = inventory.progress if isinstance(inventory.progress, dict) else {}
        raw_inventory = inventory.inventory if isinstance(inventory.inventory, dict) else {}
    elif isinstance(inventory, dict):
        progress = inventory.get("progress") if isinstance(inventory.get("progress"), dict) else inventory
        raw_inventory = inventory.get("inventory") if isinstance(inventory.get("inventory"), dict) else {}
    else:
        progress = {}
        raw_inventory = {}
    for key in ("currentHeldCount", "currentHeldResourceCount", "heldResourceCount"):
        value = progress.get(key)
        if isinstance(value, int):
            return value
    resource_counts = raw_inventory.get("resourceCounts") if isinstance(raw_inventory.get("resourceCounts"), dict) else {}
    logs = resource_counts.get("woodcutting_logs") if isinstance(resource_counts.get("woodcutting_logs"), dict) else {}
    value = logs.get("count")
    return value if isinstance(value, int) else None


def inventory_items(inventory: InventoryContext | dict[str, Any] | None) -> tuple[list[dict[str, Any]] | None, bool]:
    if isinstance(inventory, InventoryContext):
        raw_inventory = inventory.inventory if isinstance(inventory.inventory, dict) else {}
    elif isinstance(inventory, dict):
        raw_inventory = inventory.get("inventory") if isinstance(inventory.get("inventory"), dict) else inventory
    else:
        raw_inventory = {}
    items = raw_inventory.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)], True
    nested = raw_inventory.get("inventory") if isinstance(raw_inventory.get("inventory"), dict) else {}
    items = nested.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)], True
    return None, False


def tinderbox_status_for_inventory(inventory: InventoryContext | dict[str, Any] | None) -> tuple[bool | None, str, list[str], list[str]]:
    items, known = inventory_items(inventory)
    if not known:
        return None, "unknown", ["inventory.items"], ["tinderbox presence unknown because inventory item list is unavailable"]
    present = any(item.get("itemId") in TINDERBOX_ITEM_IDS for item in items or [])
    if present:
        return True, "present", [], []
    return False, "missing", [], ["tinderbox not observed in inventory"]


def analyze_process_inventory_context(
    policy: task_policy_module.TaskPolicy | dict[str, Any] | str | None,
    inventory: InventoryContext | dict[str, Any] | None,
    *,
    source_tick: int | None = None,
) -> ProcessInventoryContext:
    started = time.perf_counter()
    resolved = task_policy_module.resolve_task_policy(policy)
    if resolved.fullInventoryStrategy != task_policy_module.InventoryFullStrategy.PROCESS_INVENTORY:
        return ProcessInventoryContext(
            status="PASS",
            source_tick=source_tick,
            timing_millis=(time.perf_counter() - started) * 1000.0,
            process_required=False,
            resources_available=False,
            tinderbox_status="not_required",
            reason="task policy does not require inventory processing context",
        )

    held_count = held_resource_count(inventory)
    resources_available = bool(held_count and held_count > 0)
    disposition = str(task_policy_module.enum_value(resolved.resourceDisposition) or "")
    process_type = resolved.processTypeNeeded or disposition
    missing: list[str] = []
    warnings: list[str] = []
    tinderbox_present = None
    tinderbox_status = "not_required"
    inventory_items_known = None
    if disposition == "burn" or process_type == "firemaking":
        tinderbox_present, tinderbox_status, tinder_missing, tinder_warnings = tinderbox_status_for_inventory(inventory)
        missing.extend(tinder_missing)
        warnings.extend(tinder_warnings)
        inventory_items_known = tinderbox_status != "unknown"
    if not resources_available:
        warnings.append("no held resources available for inventory processing context")
    return ProcessInventoryContext(
        status="PASS" if resources_available and tinderbox_status not in {"missing", "unknown"} else "WARN",
        warnings=warnings,
        missing_capabilities=capabilities.normalize_capability_names(missing),
        source_tick=source_tick,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        process_required=True,
        process_type_needed=process_type,
        resource_disposition=disposition,
        resources_available=resources_available,
        held_resource_count=held_count,
        service_type_needed=None,
        tinderbox_present=tinderbox_present,
        tinderbox_status=tinderbox_status,
        inventory_items_known=inventory_items_known,
        reason="task policy requires inventory processing context",
    )

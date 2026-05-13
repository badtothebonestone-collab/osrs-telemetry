from __future__ import annotations

import time
from typing import Any

import task_policy as task_policy_module

from analyzers.live_state import InventoryContext, ProcessInventoryContext


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
            reason="task policy does not require inventory processing context",
        )

    held_count = held_resource_count(inventory)
    resources_available = bool(held_count and held_count > 0)
    return ProcessInventoryContext(
        status="PASS" if resources_available else "WARN",
        warnings=[] if resources_available else ["no held resources available for inventory processing context"],
        missing_capabilities=[],
        source_tick=source_tick,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        process_required=True,
        process_type_needed=resolved.processTypeNeeded or task_policy_module.enum_value(resolved.resourceDisposition),
        resource_disposition=str(task_policy_module.enum_value(resolved.resourceDisposition) or ""),
        resources_available=resources_available,
        held_resource_count=held_count,
        service_type_needed=None,
        reason="task policy requires inventory processing context",
    )

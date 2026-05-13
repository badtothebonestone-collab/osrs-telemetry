from __future__ import annotations

import time
from typing import Any

import capabilities
import resource_progress

from analyzers.live_state import InventoryContext


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _latest_tick(response: dict[str, Any]) -> int | None:
    status = response.get("status") if isinstance(response.get("status"), dict) else {}
    for value in (
        response.get("latestTick"),
        status.get("lastProcessedTick"),
        status.get("latestTickProcessed"),
        status.get("latestTick"),
    ):
        number = _as_int(value)
        if number is not None:
            return number
    return None


def _inventory_signature(inventory: dict[str, Any], items: Any) -> str | None:
    signature = inventory.get("inventorySignature") or inventory.get("signature")
    if signature:
        return str(signature)
    normalized = resource_progress.normalize_items(items)
    if normalized is None:
        return None
    return "|".join(f"{item.get('slot')}:{item.get('itemId')}:{item.get('quantity')}" for item in normalized)


def _slot_count(inventory: dict[str, Any]) -> int | None:
    for key in ("inventorySlotCount", "slotCount"):
        value = _as_int(inventory.get(key))
        if value is not None:
            return value
    return None


def analyze_inventory(
    *,
    response: dict[str, Any],
    inventory: dict[str, Any],
    progress_state: resource_progress.ResourceProgressState,
    resource_definition: resource_progress.ResourceDefinition,
    goal_count: int | None,
) -> InventoryContext:
    """Normalize inventory and delegate daily progress math to resource_progress."""
    started = time.perf_counter()
    inventory = inventory if isinstance(inventory, dict) else {}
    items = inventory.get("items")
    items_present = isinstance(items, list)
    if inventory.get("itemsKnown") is False or inventory.get("itemListAvailable") is False:
        items_present = False
    resource_counts = inventory.get("resourceCounts") if isinstance(inventory.get("resourceCounts"), dict) else {}
    snapshot = resource_progress.build_inventory_snapshot(
        session_path=str(response.get("sessionPath") or progress_state.session_path or "in_memory"),
        latest_tick=_latest_tick(response),
        inventory_signature=_inventory_signature(inventory, items),
        inventory_slot_count=_slot_count(inventory),
        items=items,
        resource_counts=resource_counts,
        items_present=items_present,
    )
    result = resource_progress.initialize_or_update_progress(progress_state, snapshot, resource_definition, goal_count)
    source_tick = snapshot.latest_tick
    missing_capabilities: list[str] = []
    if not items_present and not resource_counts:
        missing_capabilities.append("inventory.items")
        missing_capabilities.append("inventory.resource_counts")
    elif not items_present:
        missing_capabilities.append("inventory.items")
    if not inventory.get("recentItemDeltas"):
        missing_capabilities.append("inventory.deltas")
    progress_payload = {
        "resourceGroup": resource_definition.id,
        "goalCount": goal_count,
        "baselineHeldCount": result.baseline_held_count,
        "currentHeldCount": result.current_held_count,
        "displayedGoalProgress": result.displayed_goal_progress,
        "goalComplete": result.goal_complete,
        "progressSource": result.source,
        "reason": result.reason,
        "matchedSlots": result.matched_slots,
        "matchedSlotDetails": result.matched_slot_details,
        "matchedItemIds": result.matched_item_ids,
        "currentSnapshotValid": result.current_snapshot_valid,
        "progressRetainedFromPrevious": result.progress_retained_from_previous,
        "warnings": result.warnings,
    }
    warnings = list(result.warnings)
    if not result.current_snapshot_valid:
        warnings.append(result.reason or "inventory snapshot unavailable")
    retained = bool(result.progress_retained_from_previous)
    return InventoryContext(
        status="WARN" if warnings or missing_capabilities else "PASS",
        warnings=warnings,
        missing_capabilities=capabilities.normalize_capability_names(missing_capabilities),
        source_tick=source_tick,
        retained_from_previous=retained,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        inventory=dict(inventory),
        progress=progress_payload,
        progress_result=result,
        matched_slots=list(result.matched_slot_details),
    )

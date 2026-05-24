from __future__ import annotations

import time
from typing import Any

import capabilities
import resource_progress
import task_policy as task_policy_module

from analyzers.live_state import BankOperationContext, BankUiContext, InventoryContext


OP_DEPOSIT_RESOURCES = "deposit_resources"
OP_DEPOSIT_INVENTORY = "deposit_inventory"
OP_WITHDRAW_RESOURCES = "withdraw_resources"
OP_NONE = "none"
OP_UNKNOWN = "unknown"


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


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "open", "visible"}:
            return True
        if text in {"false", "no", "0", "closed", "hidden"}:
            return False
    return None


def _context_value(context: Any, snake_key: str, camel_key: str | None = None, default: Any = None) -> Any:
    if context is None:
        return default
    camel_key = camel_key or snake_key
    if isinstance(context, dict):
        if snake_key in context:
            return context.get(snake_key)
        return context.get(camel_key, default)
    if hasattr(context, snake_key):
        return getattr(context, snake_key)
    if hasattr(context, camel_key):
        return getattr(context, camel_key)
    return default


def _resource_definition(value: resource_progress.ResourceDefinition | dict[str, Any] | None) -> resource_progress.ResourceDefinition:
    if isinstance(value, resource_progress.ResourceDefinition):
        return value
    payload = value if isinstance(value, dict) else {}
    item_ids = payload.get("itemIds", payload.get("item_ids", ()))
    normalized_ids = tuple(item_id for item_id in (_as_int(item) for item in item_ids or []) if item_id is not None)
    return resource_progress.ResourceDefinition(
        str(payload.get("id") or payload.get("resourceGroup") or "resources"),
        normalized_ids,
        str(payload.get("displayName") or payload.get("display_name") or "resources"),
    )


def _inventory_payload(inventory_context: InventoryContext | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(inventory_context, InventoryContext):
        return inventory_context.inventory if isinstance(inventory_context.inventory, dict) else {}
    if isinstance(inventory_context, dict):
        nested = inventory_context.get("inventory")
        return nested if isinstance(nested, dict) else inventory_context
    return {}


def _progress_payload(inventory_context: InventoryContext | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(inventory_context, InventoryContext):
        return inventory_context.progress if isinstance(inventory_context.progress, dict) else {}
    if isinstance(inventory_context, dict):
        progress = inventory_context.get("progress")
        return progress if isinstance(progress, dict) else inventory_context
    return {}


def _inventory_items(raw_inventory: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    items = raw_inventory.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)], True
    nested = raw_inventory.get("inventory") if isinstance(raw_inventory.get("inventory"), dict) else {}
    items = nested.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)], True
    return [], False


def _bank_inventory_slot_widgets(bank_ui_context: Any) -> dict[int, dict[str, Any]]:
    values = _context_value(bank_ui_context, "inventory_slots", "inventorySlots", [])
    if not isinstance(values, list) or not values:
        values = _context_value(bank_ui_context, "inventory_slot_widgets", "inventorySlotWidgets", [])
    if not isinstance(values, list):
        return {}
    widgets: dict[int, dict[str, Any]] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        slot = _as_int(item.get("slot"))
        item_id = _as_int(item.get("itemId"))
        quantity = _as_int(item.get("quantity"))
        if slot is None or item_id is None or quantity is None or item_id <= 0 or quantity <= 0:
            continue
        widgets[slot] = dict(item)
    return widgets


def _slot_bounds(widget: dict[str, Any]) -> dict[str, Any]:
    bounds = widget.get("bounds")
    if isinstance(bounds, dict) and bounds:
        payload = dict(bounds)
        payload.setdefault("source", "bank_inventory_slot_widget_canvas")
        return payload
    nested = widget.get("widget")
    if isinstance(nested, dict) and isinstance(nested.get("bounds"), dict):
        payload = dict(nested["bounds"])
        payload.setdefault("source", "bank_inventory_slot_widget_canvas")
        return payload
    return {}


def _first_int(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = _as_int(payload.get(key))
        if value is not None:
            return value
    return None


def _inventory_free_slots(raw_inventory: dict[str, Any], bank_ui_context: Any) -> int | None:
    value = _first_int(raw_inventory, ("freeSlots", "inventoryFreeSlots"))
    if value is not None:
        return value
    summary = _context_value(bank_ui_context, "inventory_summary", "inventorySummary", {})
    if isinstance(summary, dict):
        return _first_int(summary, ("freeSlots", "inventoryFreeSlots"))
    return None


def _inventory_occupied_slots(raw_inventory: dict[str, Any], bank_ui_context: Any) -> int | None:
    value = _first_int(raw_inventory, ("occupiedSlots", "filledSlots"))
    if value is not None:
        return value
    summary = _context_value(bank_ui_context, "inventory_summary", "inventorySummary", {})
    if isinstance(summary, dict):
        return _first_int(summary, ("occupiedSlots", "filledSlots"))
    return None


def _inventory_full(raw_inventory: dict[str, Any], free_slots: int | None) -> bool | None:
    full = _as_bool(raw_inventory.get("inventoryFull"))
    if full is not None:
        return full
    return free_slots == 0 if free_slots is not None else None


def _held_count_fallback(
    inventory_context: InventoryContext | dict[str, Any] | None,
    raw_inventory: dict[str, Any],
    bank_ui_context: Any,
    definition: resource_progress.ResourceDefinition,
) -> int | None:
    progress = _progress_payload(inventory_context)
    for key in ("currentHeldCount", "currentHeldResourceCount", "heldResourceCount"):
        value = _as_int(progress.get(key))
        if value is not None:
            return max(0, value)
    summary = _context_value(bank_ui_context, "inventory_summary", "inventorySummary", {})
    if isinstance(summary, dict):
        value = _as_int(summary.get("matchingResourceCount"))
        if value is not None:
            return max(0, value)
    resource_counts = raw_inventory.get("resourceCounts") if isinstance(raw_inventory.get("resourceCounts"), dict) else {}
    count_result = resource_progress.count_from_resource_counts(resource_counts, definition) if resource_counts else None
    if count_result and count_result.get("known"):
        return _as_int(count_result.get("count"))
    return None


def _inventory_resource_summary(
    inventory_context: InventoryContext | dict[str, Any] | None,
    bank_ui_context: Any,
    definition: resource_progress.ResourceDefinition,
) -> dict[str, Any]:
    raw_inventory = _inventory_payload(inventory_context)
    items, items_known = _inventory_items(raw_inventory)
    if not items_known:
        summary = _context_value(bank_ui_context, "inventory_summary", "inventorySummary", {})
        if isinstance(summary, dict):
            items, items_known = _inventory_items(summary)
    target_ids = set(definition.item_ids)
    slot_widgets = _bank_inventory_slot_widgets(bank_ui_context)
    if items_known:
        resource_slots: list[int] = []
        resource_slot_bounds: list[dict[str, Any]] = []
        resource_item_widgets: list[dict[str, Any]] = []
        resource_quantity = 0
        resource_items_held = 0
        non_resource_items_held = 0
        for item in items:
            item_id = resource_progress.item_id(item)
            quantity = resource_progress.item_quantity(item)
            if item_id is None or quantity <= 0:
                continue
            if item_id in target_ids:
                slot = resource_progress.item_slot(item)
                if slot is not None:
                    resource_slots.append(slot)
                    widget = slot_widgets.get(slot)
                    if isinstance(widget, dict):
                        resource_item_widgets.append(dict(widget))
                        bounds = _slot_bounds(widget)
                        if bounds:
                            resource_slot_bounds.append(bounds)
                resource_quantity += quantity
                resource_items_held += 1
            else:
                non_resource_items_held += 1
        return {
            "itemsKnown": True,
            "resourceItemsHeld": resource_items_held,
            "resourceItemSlots": sorted(resource_slots),
            "resourceItemSlotBounds": resource_slot_bounds,
            "resourceItemWidgets": resource_item_widgets,
            "resourceItemQuantity": resource_quantity,
            "resourceDisplayName": definition.display_name,
            "nonResourceItemsHeld": non_resource_items_held,
        }
    fallback_count = _held_count_fallback(inventory_context, raw_inventory, bank_ui_context, definition)
    occupied = _inventory_occupied_slots(raw_inventory, bank_ui_context)
    non_resource_items_held = None
    if fallback_count is not None and occupied is not None:
        non_resource_items_held = max(0, int(occupied) - int(fallback_count))
    return {
        "itemsKnown": False,
        "resourceItemsHeld": fallback_count,
        "resourceItemSlots": [],
        "resourceItemSlotBounds": [],
        "resourceItemWidgets": [],
        "resourceItemQuantity": fallback_count,
        "resourceDisplayName": definition.display_name,
        "nonResourceItemsHeld": non_resource_items_held,
    }


def _source_tick(source_tick: int | None, *contexts: Any) -> int | None:
    if source_tick is not None:
        return source_tick
    for context in contexts:
        value = _context_value(context, "source_tick", "sourceTick")
        tick = _as_int(value)
        if tick is not None:
            return tick
    return None


def analyze_bank_operation_context(
    policy: task_policy_module.TaskPolicy | dict[str, Any] | str | None,
    *,
    bank_ui_context: BankUiContext | dict[str, Any] | None,
    inventory_context: InventoryContext | dict[str, Any] | None,
    resource_definition: resource_progress.ResourceDefinition | dict[str, Any] | None,
    current_task_state: dict[str, Any] | None = None,
    source_tick: int | None = None,
) -> BankOperationContext:
    started = time.perf_counter()
    resolved_policy = task_policy_module.resolve_task_policy(policy)
    definition = _resource_definition(resource_definition)
    tick = _source_tick(source_tick, bank_ui_context, inventory_context)
    raw_inventory = _inventory_payload(inventory_context)
    free_slots = _inventory_free_slots(raw_inventory, bank_ui_context)
    inventory_full = _inventory_full(raw_inventory, free_slots)
    deposit_available = _as_bool(_context_value(bank_ui_context, "deposit_inventory_available", "depositInventoryAvailable"))
    if deposit_available is None:
        deposit_available = _as_bool(_context_value(bank_ui_context, "deposit_inventory_button_visible", "depositInventoryButtonVisible"))
    bank_readable = bool(_context_value(bank_ui_context, "bank_readable", "bankReadable", False))
    bank_pin_open = _as_bool(_context_value(bank_ui_context, "bank_pin_open", "bankPinOpen"))
    missing: list[str] = []
    warnings: list[str] = []

    if resolved_policy.fullInventoryStrategy != task_policy_module.InventoryFullStrategy.NEEDS_SERVICE or resolved_policy.resourceDisposition != task_policy_module.ResourceDisposition.BANK:
        return BankOperationContext(
            status="PASS",
            source_tick=tick,
            timing_millis=(time.perf_counter() - started) * 1000.0,
            operation_needed=False,
            operation_type=OP_NONE,
            inventory_free_slots=free_slots,
            inventory_full=inventory_full,
            deposit_inventory_available=deposit_available,
            bank_readable=bank_readable,
            banking_complete=False,
            completion_reason="policy_does_not_require_bank_operation",
            reason="task policy does not require bank operation context",
        )

    if bank_ui_context is None:
        missing.append("bank_ui.telemetry")
        warnings.append("bank operation is waiting for bank UI context")
    elif isinstance(bank_ui_context, dict):
        missing.extend(str(item) for item in bank_ui_context.get("missingCapabilities") or [] if item)

    if bank_pin_open:
        warnings.append("bank_pin_required")
        return BankOperationContext(
            status="WARN",
            warnings=list(dict.fromkeys(warnings)),
            missing_capabilities=capabilities.normalize_capability_names(missing),
            source_tick=tick,
            timing_millis=(time.perf_counter() - started) * 1000.0,
            operation_needed=False,
            operation_type=OP_UNKNOWN,
            inventory_free_slots=free_slots,
            inventory_full=inventory_full,
            deposit_inventory_available=deposit_available,
            bank_readable=False,
            banking_complete=False,
            completion_reason="bank_pin_required",
            reason="bank pin requires user resolution before banking can continue",
        )

    resource_summary = _inventory_resource_summary(inventory_context, bank_ui_context, definition)
    resource_items_held = _as_int(resource_summary.get("resourceItemsHeld"))
    resource_item_quantity = _as_int(resource_summary.get("resourceItemQuantity"))
    resource_item_slots = [slot for slot in resource_summary.get("resourceItemSlots", []) if isinstance(slot, int)]
    resource_item_slot_bounds = [dict(item) for item in resource_summary.get("resourceItemSlotBounds", []) if isinstance(item, dict)]
    resource_item_widgets = [dict(item) for item in resource_summary.get("resourceItemWidgets", []) if isinstance(item, dict)]
    resource_display_name = str(resource_summary.get("resourceDisplayName") or definition.display_name or "resources")
    non_resource_items_held = _as_int(resource_summary.get("nonResourceItemsHeld"))
    items_known = bool(resource_summary.get("itemsKnown"))

    if resource_item_quantity is not None and resource_item_quantity <= 0:
        return BankOperationContext(
            status="PASS" if not missing else "WARN",
            warnings=list(dict.fromkeys(warnings)),
            missing_capabilities=capabilities.normalize_capability_names(missing),
            source_tick=tick,
            timing_millis=(time.perf_counter() - started) * 1000.0,
            operation_needed=False,
            operation_type=OP_NONE,
            resource_items_held=resource_items_held if resource_items_held is not None else 0,
            resource_item_slots=resource_item_slots,
            resource_item_slot_bounds=resource_item_slot_bounds,
            resource_item_widgets=resource_item_widgets,
            resource_item_quantity=0,
            resource_display_name=resource_display_name,
            non_resource_items_held=non_resource_items_held,
            inventory_free_slots=free_slots,
            inventory_full=inventory_full,
            deposit_inventory_available=deposit_available,
            deposit_would_clear_resource_inventory=False,
            bank_readable=bank_readable,
            banking_complete=True,
            completion_reason="no_resource_items_held",
            reason="banking complete because no target resources remain in inventory",
        )

    if not bank_readable:
        warnings.append("waiting_for_readable_bank")
        return BankOperationContext(
            status="WARN",
            warnings=list(dict.fromkeys(warnings)),
            missing_capabilities=capabilities.normalize_capability_names(missing),
            source_tick=tick,
            timing_millis=(time.perf_counter() - started) * 1000.0,
            operation_needed=False,
            operation_type=OP_UNKNOWN,
            inventory_free_slots=free_slots,
            inventory_full=inventory_full,
            deposit_inventory_available=deposit_available,
            bank_readable=False,
            banking_complete=False,
            completion_reason="waiting_for_readable_bank",
            reason="bank operation waits until bank UI is readable",
        )

    if resource_item_quantity is None:
        missing.append("inventory.items")
        missing.append("inventory.resource_counts")
        warnings.append("resource inventory state unknown")
        return BankOperationContext(
            status="WARN",
            warnings=list(dict.fromkeys(warnings)),
            missing_capabilities=capabilities.normalize_capability_names(missing),
            source_tick=tick,
            timing_millis=(time.perf_counter() - started) * 1000.0,
            operation_needed=False,
            operation_type=OP_UNKNOWN,
            resource_items_held=resource_items_held,
            resource_item_slots=resource_item_slots,
            resource_item_slot_bounds=resource_item_slot_bounds,
            resource_item_widgets=resource_item_widgets,
            resource_item_quantity=resource_item_quantity,
            resource_display_name=resource_display_name,
            non_resource_items_held=non_resource_items_held,
            inventory_free_slots=free_slots,
            inventory_full=inventory_full,
            deposit_inventory_available=deposit_available,
            bank_readable=True,
            banking_complete=False,
            completion_reason="resource_inventory_unknown",
            reason="resource inventory state is unknown",
        )

    operation_type = OP_UNKNOWN
    if deposit_available is True:
        operation_type = OP_DEPOSIT_INVENTORY
    elif items_known or resource_item_slots:
        operation_type = OP_DEPOSIT_RESOURCES
    else:
        warnings.append("resource item slots unknown for selective deposit")

    return BankOperationContext(
        status="PASS" if not warnings and not missing else "WARN",
        warnings=list(dict.fromkeys(warnings)),
        missing_capabilities=capabilities.normalize_capability_names(missing),
        source_tick=tick,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        operation_needed=True,
        operation_type=operation_type,
        resource_items_held=resource_items_held,
        resource_item_slots=resource_item_slots,
        resource_item_slot_bounds=resource_item_slot_bounds,
        resource_item_widgets=resource_item_widgets,
        resource_item_quantity=resource_item_quantity,
        resource_display_name=resource_display_name,
        non_resource_items_held=non_resource_items_held,
        inventory_free_slots=free_slots,
        inventory_full=inventory_full,
        deposit_inventory_available=deposit_available,
        deposit_would_clear_resource_inventory=operation_type == OP_DEPOSIT_INVENTORY,
        bank_readable=True,
        banking_complete=False,
        completion_reason="resources_still_held",
        reason="target resources remain in inventory after bank UI became readable",
    )

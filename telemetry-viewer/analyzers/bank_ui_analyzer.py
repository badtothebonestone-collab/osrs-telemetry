from __future__ import annotations

import time
from typing import Any

import capabilities
import task_policy as task_policy_module

from analyzers.live_state import BankUiContext


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "open", "visible"}:
            return True
        if text in {"false", "no", "0", "closed", "hidden"}:
            return False
    return None


def _int_value(value: Any) -> int | None:
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


def _summary_from_inventory_context(inventory_context: Any) -> dict[str, Any]:
    inventory = _context_value(inventory_context, "inventory", default={})
    if not isinstance(inventory, dict):
        return {}
    summary: dict[str, Any] = {}
    for target, keys in {
        "freeSlots": ("freeSlots", "inventoryFreeSlots"),
        "occupiedSlots": ("occupiedSlots", "filledSlots"),
        "slotCount": ("slotCount", "inventorySlotCount"),
        "itemCount": ("itemCount", "totalItemQuantity"),
    }.items():
        value = next((_int_value(inventory.get(key)) for key in keys if _int_value(inventory.get(key)) is not None), None)
        if value is not None:
            summary[target] = value
    progress = _context_value(inventory_context, "progress", default={})
    if isinstance(progress, dict):
        held = _int_value(progress.get("currentHeldCount"))
        if held is not None:
            summary.setdefault("matchingResourceCount", held)
    return summary


def _normalize_summary(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    normalized = dict(summary)
    unique_ids = normalized.get("uniqueItemIds")
    if isinstance(unique_ids, list):
        normalized["uniqueItemIds"] = list(unique_ids)
        normalized["uniqueItemCount"] = len({str(item) for item in unique_ids})
    elif normalized.get("uniqueItemCount") is None:
        totals = normalized.get("totalQuantityByItemId")
        if isinstance(totals, dict):
            normalized["uniqueItemCount"] = len(totals)
    return normalized


def _service_ready(service_context: Any, pathing_context: Any) -> bool:
    service_ready = _context_value(service_context, "service_ready", "serviceReady")
    if isinstance(service_ready, bool):
        return service_ready
    pathing_ready = _context_value(pathing_context, "service_ready", "serviceReady")
    return bool(pathing_ready) if isinstance(pathing_ready, bool) else False


def analyze_bank_ui_context(
    policy: task_policy_module.TaskPolicy | dict[str, Any] | str | None,
    *,
    bank_ui_payload: dict[str, Any] | None,
    inventory_context: Any = None,
    service_context: Any = None,
    pathing_context: Any = None,
    source_tick: int | None = None,
) -> BankUiContext:
    started = time.perf_counter()
    resolved_policy = task_policy_module.resolve_task_policy(policy)
    service_ready = _service_ready(service_context, pathing_context)
    payload = bank_ui_payload if isinstance(bank_ui_payload, dict) else {}
    missing: list[str] = []
    warnings: list[str] = []

    if not payload:
        missing.append("bank_ui.telemetry")
        warnings.append("bank UI telemetry unavailable")

    bank_pin_open = _boolish(payload.get("bankPinOpen"))
    bank_root_visible = _boolish(payload.get("bankRootVisible"))
    bank_container_visible = _boolish(payload.get("bankContainerVisible"))
    bank_inventory_visible = _boolish(payload.get("bankInventoryVisible"))
    deposit_button_visible = _boolish(payload.get("depositInventoryButtonVisible"))
    close_button_visible = _boolish(payload.get("bankCloseButtonVisible"))

    bank_open = _boolish(payload.get("bankOpen"))
    if bank_open is None and payload:
        visible_values = (bank_root_visible, bank_container_visible, bank_inventory_visible)
        if any(value is True for value in visible_values):
            bank_open = True
        elif any(value is False for value in visible_values):
            bank_open = False

    bank_container_readable = bool(bank_container_visible)
    bank_inventory_readable = bool(bank_inventory_visible)
    bank_readable = bool(bank_open and not bank_pin_open and (bank_container_readable or bank_inventory_readable))
    if bank_pin_open:
        warnings.append("bank_pin_required")
        bank_readable = False

    inventory_summary = _normalize_summary(payload.get("inventorySummary"))
    if not inventory_summary:
        inventory_summary = _summary_from_inventory_context(inventory_context)
    bank_summary = _normalize_summary(payload.get("bankSummary"))

    status = "PASS"
    if missing or warnings:
        status = "WARN"
    if resolved_policy.fullInventoryStrategy != task_policy_module.InventoryFullStrategy.NEEDS_SERVICE:
        reason = "policy_does_not_require_service_ui"
    elif bank_pin_open:
        reason = "bank_pin_required"
    elif bank_readable:
        reason = "bank_readable"
    elif bank_open is False:
        reason = "bank_closed"
    elif not payload:
        reason = "bank_ui_payload_missing"
    else:
        reason = "bank_ui_state_observed"

    return BankUiContext(
        status=status,
        warnings=warnings,
        missing_capabilities=capabilities.normalize_capability_names(missing),
        source_tick=source_tick,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        bank_open=bank_open,
        bank_pin_open=bank_pin_open,
        bank_readable=bank_readable,
        bank_container_readable=bank_container_readable,
        bank_inventory_readable=bank_inventory_readable,
        deposit_inventory_available=deposit_button_visible,
        close_button_available=close_button_visible,
        top_level_interface_id=_int_value(payload.get("topLevelInterfaceId")),
        bank_root_visible=bank_root_visible,
        bank_container_visible=bank_container_visible,
        bank_inventory_visible=bank_inventory_visible,
        deposit_inventory_button_visible=deposit_button_visible,
        bank_close_button_visible=close_button_visible,
        inventory_summary=inventory_summary,
        bank_summary=bank_summary,
        service_ready=service_ready,
        reason=reason,
    )

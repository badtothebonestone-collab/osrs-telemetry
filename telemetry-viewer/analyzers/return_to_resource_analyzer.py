from __future__ import annotations

import time
from typing import Any

import capabilities
import task_policy as task_policy_module

from analyzers.live_state import BankOperationContext, InventoryContext, ReturnToResourceContext, TargetContext


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "complete", "available", "ready"}:
            return True
        if text in {"false", "no", "0", "incomplete", "unavailable", "not_ready"}:
            return False
    return None


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


def _inventory_payload(context: InventoryContext | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(context, InventoryContext):
        return context.inventory if isinstance(context.inventory, dict) else {}
    if isinstance(context, dict):
        nested = context.get("inventory")
        return nested if isinstance(nested, dict) else context
    return {}


def _free_slots(
    inventory_context: InventoryContext | dict[str, Any] | None,
    bank_operation_context: BankOperationContext | dict[str, Any] | None,
) -> int | None:
    inventory = _inventory_payload(inventory_context)
    for value in (
        inventory.get("freeSlots"),
        inventory.get("inventoryFreeSlots"),
        _context_value(bank_operation_context, "inventory_free_slots", "inventoryFreeSlots"),
    ):
        slots = _as_int(value)
        if slots is not None:
            return slots
    return None


def _inventory_full(inventory_context: InventoryContext | dict[str, Any] | None, free_slots: int | None) -> bool | None:
    inventory = _inventory_payload(inventory_context)
    full = _as_bool(inventory.get("inventoryFull"))
    if full is not None:
        return full
    if free_slots is not None:
        return free_slots <= 0
    return None


def _best_resource_target(target_context: TargetContext | dict[str, Any] | None) -> dict[str, Any] | None:
    for key_pair in (
        ("raw_best_target", "rawBestTarget"),
        ("nearest_target", "nearestTarget"),
    ):
        target = _context_value(target_context, key_pair[0], key_pair[1])
        if isinstance(target, dict) and target and _is_resource_target(target):
            return dict(target)
    for key in ("top_candidates", "topCandidates", "profile_candidates", "profileCandidates"):
        candidates = _context_value(target_context, key, key)
        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate and _is_resource_target(candidate):
                    return dict(candidate)
    return None


def _is_resource_target(target: dict[str, Any] | None) -> bool:
    if not isinstance(target, dict):
        return False
    class_id = str(target.get("classId") or target.get("targetClass") or "").strip().lower()
    target_type = str(target.get("targetType") or target.get("type") or "").strip().lower()
    name = str(target.get("targetName") or target.get("name") or "").strip().lower()
    if class_id in {"resource_return", "service_route_anchor", "path_tile", "destination", "unknown"}:
        return False
    if target_type in {"tile", "path_tile", "service_route_anchor", "resource_return"}:
        return False
    if class_id in {"tree", "woodcutting_tree"}:
        return True
    return target_type == "sceneobject" and "tree" in name


def _target_reachability(target: dict[str, Any] | None) -> str | None:
    if not isinstance(target, dict):
        return None
    navigation = target.get("navigation") if isinstance(target.get("navigation"), dict) else {}
    value = target.get("directReachability") or navigation.get("directReachability") or target.get("reachability")
    return str(value).lower() if value is not None else None


def _target_pathing_needed(target: dict[str, Any] | None) -> bool:
    reachability = _target_reachability(target)
    if reachability in {"reachable", "adjacent", "direct"}:
        return False
    return bool(target)


def _source_tick(source_tick: int | None, *contexts: Any) -> int | None:
    if source_tick is not None:
        return source_tick
    for context in contexts:
        value = _context_value(context, "source_tick", "sourceTick")
        tick = _as_int(value)
        if tick is not None:
            return tick
    return None


def analyze_return_to_resource_context(
    policy: task_policy_module.TaskPolicy | dict[str, Any] | str | None,
    *,
    bank_operation_context: BankOperationContext | dict[str, Any] | None,
    inventory_context: InventoryContext | dict[str, Any] | None,
    target_context: TargetContext | dict[str, Any] | None,
    current_task_state: dict[str, Any] | None = None,
    source_tick: int | None = None,
) -> ReturnToResourceContext:
    started = time.perf_counter()
    resolved_policy = task_policy_module.resolve_task_policy(policy)
    tick = _source_tick(source_tick, bank_operation_context, inventory_context, target_context)
    free_slots = _free_slots(inventory_context, bank_operation_context)
    inventory_full = _inventory_full(inventory_context, free_slots)
    banking_complete = bool(_context_value(bank_operation_context, "banking_complete", "bankingComplete", False))
    missing: list[str] = []
    warnings: list[str] = []

    if resolved_policy.fullInventoryStrategy != task_policy_module.InventoryFullStrategy.NEEDS_SERVICE or resolved_policy.resourceDisposition != task_policy_module.ResourceDisposition.BANK:
        return ReturnToResourceContext(
            status="PASS",
            source_tick=tick,
            timing_millis=(time.perf_counter() - started) * 1000.0,
            inventory_free_slots=free_slots,
            inventory_full=inventory_full,
            banking_complete=banking_complete,
            reason="policy_does_not_require_return_to_resource",
        )

    if bank_operation_context is None:
        missing.append("bank_operation.context")
        warnings.append("return-to-resource is waiting for bank operation context")
        return ReturnToResourceContext(
            status="WARN",
            warnings=warnings,
            missing_capabilities=capabilities.normalize_capability_names(missing),
            source_tick=tick,
            timing_millis=(time.perf_counter() - started) * 1000.0,
            inventory_free_slots=free_slots,
            inventory_full=inventory_full,
            banking_complete=False,
            reason="bank_operation_context_missing",
        )

    if not banking_complete:
        return ReturnToResourceContext(
            status="PASS",
            source_tick=tick,
            timing_millis=(time.perf_counter() - started) * 1000.0,
            inventory_free_slots=free_slots,
            inventory_full=inventory_full,
            banking_complete=False,
            reason="waiting_for_banking_complete",
        )

    if inventory_full is True or free_slots == 0:
        warnings.append("inventory still appears full after banking complete")
        return ReturnToResourceContext(
            status="WARN",
            warnings=warnings,
            missing_capabilities=capabilities.normalize_capability_names(missing),
            source_tick=tick,
            timing_millis=(time.perf_counter() - started) * 1000.0,
            return_needed=False,
            return_ready=False,
            service_complete=True,
            inventory_free_slots=free_slots,
            inventory_full=inventory_full,
            banking_complete=True,
            reason="inventory_still_full",
        )

    target = _best_resource_target(target_context)
    target_available = bool(target)
    if not target_available:
        missing.append("target.candidates")
        warnings.append("no resource target observed after banking complete")

    return ReturnToResourceContext(
        status="PASS" if target_available else "WARN",
        warnings=list(dict.fromkeys(warnings)),
        missing_capabilities=capabilities.normalize_capability_names(missing),
        source_tick=tick,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        return_needed=True,
        return_ready=target_available,
        service_complete=True,
        reason="resource_target_available" if target_available else "no_resource_target_observed",
        resource_target_available=target_available,
        best_resource_target=target,
        resource_pathing_needed=_target_pathing_needed(target),
        inventory_free_slots=free_slots,
        inventory_full=inventory_full,
        banking_complete=True,
    )

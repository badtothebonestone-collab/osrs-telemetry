from __future__ import annotations

import time
from dataclasses import is_dataclass
from typing import Any

import capabilities
import task_policy as task_policy_module

from analyzers.live_state import NavigationIntentContext


SERVICE_TARGET_AVAILABLE = "service_target_available"
SERVICE_TARGET_MISSING = "service_target_missing"
TARGET_REACHABLE = "target_reachable"
TARGET_UNREACHABLE = "target_unreachable"
LOCAL_NAVIGATION_ONLY = "local_navigation_only"
FULL_PATHFINDING_MISSING = "full_pathfinding_missing"

TARGET_KIND_SERVICE = "service"
TARGET_KIND_RESOURCE = "resource"
TARGET_KIND_PROCESS_INVENTORY = "process_inventory"
TARGET_KIND_NONE = "none"

def context_value(context: Any, snake_key: str, camel_key: str | None = None, default: Any = None) -> Any:
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
    if is_dataclass(context):
        return getattr(context, snake_key, default)
    return default


def source_tick_from(*contexts: Any) -> int | None:
    for context in contexts:
        value = context_value(context, "source_tick", "sourceTick")
        if isinstance(value, int):
            return value
    return None


def target_payload(target: Any) -> dict[str, Any] | None:
    if not isinstance(target, dict) or not target:
        return None
    return dict(target)


def target_distance(target: dict[str, Any] | None) -> int | float | None:
    if not isinstance(target, dict):
        return None
    value = target.get("distanceTiles")
    return value if isinstance(value, (int, float)) else None


def target_reachability(target: dict[str, Any] | None) -> str | None:
    if not isinstance(target, dict):
        return None
    direct = target.get("directReachability")
    if direct is None and isinstance(target.get("navigation"), dict):
        direct = target["navigation"].get("directReachability")
    return str(direct) if direct is not None else None


def target_path_length(target: dict[str, Any] | None) -> int | float | None:
    if not isinstance(target, dict):
        return None
    value = target.get("pathLengthTiles")
    if value is None and isinstance(target.get("navigation"), dict):
        value = target["navigation"].get("pathLengthTiles")
    return value if isinstance(value, (int, float)) else None


def collision_window_available(navigation_context: Any) -> bool | None:
    value = context_value(navigation_context, "collision_window_available", "collisionWindowAvailable")
    if isinstance(value, bool):
        return value
    raw = context_value(navigation_context, "raw")
    if isinstance(raw, dict) and isinstance(raw.get("collisionWindowAvailable"), bool):
        return raw.get("collisionWindowAvailable")
    return None


def active_intent_from(generic_task_state: Any) -> str:
    if not isinstance(generic_task_state, dict):
        return ""
    return str(generic_task_state.get("activeIntent") or generic_task_state.get("phase") or "")


def active_target_from(generic_task_state: Any, target_context: Any) -> dict[str, Any] | None:
    if isinstance(generic_task_state, dict) and isinstance(generic_task_state.get("activeIntentTarget"), dict):
        return generic_task_state["activeIntentTarget"]
    target = context_value(target_context, "raw_best_target", "rawBestTarget")
    return target if isinstance(target, dict) else None


def with_target_fields(
    *,
    policy: task_policy_module.TaskPolicy,
    navigation_reason: str,
    target_kind: str,
    destination: dict[str, Any] | None,
    navigation_context: Any,
    source_tick: int | None,
    started: float,
    navigation_needed: bool,
    warnings: list[str] | None = None,
    missing_capabilities: list[str] | None = None,
    status: str | None = None,
) -> NavigationIntentContext:
    clean_destination = target_payload(destination)
    direct = target_reachability(clean_destination)
    missing = list(missing_capabilities or [])
    notes = list(warnings or [])
    collision_available = collision_window_available(navigation_context)
    path_length = target_path_length(clean_destination)
    if collision_available is False:
        missing.append("navigation.local_collision_window")
    if clean_destination and path_length is None and direct in {None, "unknown"}:
        missing.append("navigation.full_pathfinding")
    if navigation_reason == TARGET_UNREACHABLE and not notes:
        notes.append("target appears unreachable in the current local navigation context")
    if navigation_reason == FULL_PATHFINDING_MISSING and not notes:
        notes.append("local navigation context is not enough to determine reachability")
    if status is None:
        status = "WARN" if notes or navigation_reason in {TARGET_UNREACHABLE, FULL_PATHFINDING_MISSING, SERVICE_TARGET_MISSING} else "PASS"
    return NavigationIntentContext(
        status=status,
        warnings=notes,
        missing_capabilities=capabilities.normalize_capability_names(missing),
        source_tick=source_tick,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        navigation_needed=navigation_needed,
        navigation_reason=navigation_reason,
        target_kind=target_kind,
        destination_target=clean_destination,
        distance_tiles=target_distance(clean_destination),
        direct_reachability=direct,
        path_length_tiles=path_length,
        collision_window_available=collision_available,
    )


def service_navigation_context(
    *,
    policy: task_policy_module.TaskPolicy,
    service_context: Any,
    navigation_context: Any,
    source_tick: int | None,
    started: float,
) -> NavigationIntentContext:
    destination = context_value(service_context, "best_service_candidate", "bestServiceCandidate")
    if not isinstance(destination, dict) or not destination:
        return with_target_fields(
            policy=policy,
            navigation_reason=SERVICE_TARGET_MISSING,
            target_kind=TARGET_KIND_NONE,
            destination=None,
            navigation_context=navigation_context,
            source_tick=source_tick,
            started=started,
            navigation_needed=False,
            warnings=["service target missing; waiting for service target context"],
            missing_capabilities=["target.candidates"],
            status="WARN",
        )
    direct = target_reachability(destination)
    if direct in {"blocked", "unreachable"}:
        return with_target_fields(
            policy=policy,
            navigation_reason=TARGET_UNREACHABLE,
            target_kind=TARGET_KIND_SERVICE,
            destination=destination,
            navigation_context=navigation_context,
            source_tick=source_tick,
            started=started,
            navigation_needed=True,
        )
    return with_target_fields(
        policy=policy,
        navigation_reason=SERVICE_TARGET_AVAILABLE,
        target_kind=TARGET_KIND_SERVICE,
        destination=destination,
        navigation_context=navigation_context,
        source_tick=source_tick,
        started=started,
        navigation_needed=True,
    )


def resource_navigation_context(
    *,
    policy: task_policy_module.TaskPolicy,
    destination: dict[str, Any] | None,
    navigation_context: Any,
    source_tick: int | None,
    started: float,
) -> NavigationIntentContext:
    direct = target_reachability(destination)
    if direct in {"blocked", "unreachable"}:
        return with_target_fields(
            policy=policy,
            navigation_reason=TARGET_UNREACHABLE,
            target_kind=TARGET_KIND_RESOURCE,
            destination=destination,
            navigation_context=navigation_context,
            source_tick=source_tick,
            started=started,
            navigation_needed=True,
        )
    if direct in {"reachable", "adjacent"}:
        return with_target_fields(
            policy=policy,
            navigation_reason=TARGET_REACHABLE,
            target_kind=TARGET_KIND_RESOURCE,
            destination=destination,
            navigation_context=navigation_context,
            source_tick=source_tick,
            started=started,
            navigation_needed=False,
        )
    if destination:
        return with_target_fields(
            policy=policy,
            navigation_reason=FULL_PATHFINDING_MISSING,
            target_kind=TARGET_KIND_RESOURCE,
            destination=destination,
            navigation_context=navigation_context,
            source_tick=source_tick,
            started=started,
            navigation_needed=True,
        )
    return with_target_fields(
        policy=policy,
        navigation_reason=LOCAL_NAVIGATION_ONLY,
        target_kind=TARGET_KIND_NONE,
        destination=None,
        navigation_context=navigation_context,
        source_tick=source_tick,
        started=started,
        navigation_needed=False,
    )


def analyze_navigation_intent(
    policy: task_policy_module.TaskPolicy | dict[str, Any] | str | None,
    *,
    player_context: Any = None,
    target_context: Any = None,
    service_context: Any = None,
    process_inventory_context: Any = None,
    navigation_context: Any = None,
    generic_task_state: dict[str, Any] | None = None,
    source_tick: int | None = None,
) -> NavigationIntentContext:
    started = time.perf_counter()
    resolved_policy = task_policy_module.resolve_task_policy(policy)
    tick = source_tick if source_tick is not None else source_tick_from(service_context, process_inventory_context, navigation_context, target_context, player_context)
    active_intent = active_intent_from(generic_task_state)
    strategy = resolved_policy.fullInventoryStrategy
    service_required = bool(context_value(service_context, "service_required", "serviceRequired"))
    process_required = bool(context_value(process_inventory_context, "process_required", "processRequired"))
    service_intent_requested = active_intent in {"needs_service", "navigate_to_service", "service_available"}

    if strategy == task_policy_module.InventoryFullStrategy.NEEDS_SERVICE and (service_required or service_intent_requested) and active_intent in {"", "needs_service", "navigate_to_service", "service_available"}:
        return service_navigation_context(
            policy=resolved_policy,
            service_context=service_context,
            navigation_context=navigation_context,
            source_tick=tick,
            started=started,
        )

    if strategy == task_policy_module.InventoryFullStrategy.PROCESS_INVENTORY and process_required and active_intent in {"", "process_inventory"}:
        return with_target_fields(
            policy=resolved_policy,
            navigation_reason=LOCAL_NAVIGATION_ONLY,
            target_kind=TARGET_KIND_PROCESS_INVENTORY,
            destination=None,
            navigation_context=navigation_context,
            source_tick=tick,
            started=started,
            navigation_needed=False,
        )

    if active_intent in {"target_selected", "continue_current_target", "continue_task", "select_target", "wait_for_result", "return_to_resource_area", "navigate_to_resource_area"}:
        return resource_navigation_context(
            policy=resolved_policy,
            destination=active_target_from(generic_task_state, target_context),
            navigation_context=navigation_context,
            source_tick=tick,
            started=started,
        )

    return with_target_fields(
        policy=resolved_policy,
        navigation_reason=LOCAL_NAVIGATION_ONLY,
        target_kind=TARGET_KIND_NONE,
        destination=None,
        navigation_context=navigation_context,
        source_tick=tick,
        started=started,
        navigation_needed=False,
    )

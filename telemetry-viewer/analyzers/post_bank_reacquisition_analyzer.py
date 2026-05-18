from __future__ import annotations

import time
from typing import Any

import capabilities
import task_policy as task_policy_module

from analyzers.live_state import BankOperationContext, BankUiContext, PostBankReacquisitionContext, TargetContext


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "open", "visible", "ready"}:
            return True
        if text in {"false", "no", "0", "closed", "hidden", "not_ready"}:
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


def _source_tick(source_tick: int | None, *contexts: Any) -> int | None:
    if source_tick is not None:
        return source_tick
    for context in contexts:
        tick = _as_int(_context_value(context, "source_tick", "sourceTick"))
        if tick is not None:
            return tick
    return None


def _best_resource_target(target_context: TargetContext | dict[str, Any] | None) -> dict[str, Any] | None:
    for key_pair in (
        ("raw_best_target", "rawBestTarget"),
        ("nearest_target", "nearestTarget"),
    ):
        target = _context_value(target_context, key_pair[0], key_pair[1])
        if isinstance(target, dict) and target:
            return dict(target)
    for key in ("top_candidates", "topCandidates", "profile_candidates", "profileCandidates"):
        candidates = _context_value(target_context, key, key)
        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate:
                    return dict(candidate)
    return None


def analyze_post_bank_reacquisition_context(
    policy: task_policy_module.TaskPolicy | dict[str, Any] | str | None,
    *,
    bank_operation_context: BankOperationContext | dict[str, Any] | None,
    bank_ui_context: BankUiContext | dict[str, Any] | None,
    target_context: TargetContext | dict[str, Any] | None,
    current_task_state: dict[str, Any] | None = None,
    source_tick: int | None = None,
) -> PostBankReacquisitionContext:
    started = time.perf_counter()
    resolved_policy = task_policy_module.resolve_task_policy(policy)
    tick = _source_tick(source_tick, bank_operation_context, bank_ui_context, target_context)
    timing = lambda: (time.perf_counter() - started) * 1000.0
    if resolved_policy.fullInventoryStrategy != task_policy_module.InventoryFullStrategy.NEEDS_SERVICE or resolved_policy.resourceDisposition != task_policy_module.ResourceDisposition.BANK:
        return PostBankReacquisitionContext(
            status="PASS",
            source_tick=tick,
            timing_millis=timing(),
            reason="not_applicable",
        )

    banking_complete = _as_bool(_context_value(bank_operation_context, "banking_complete", "bankingComplete", False)) is True
    if not banking_complete:
        return PostBankReacquisitionContext(
            status="PASS",
            source_tick=tick,
            timing_millis=timing(),
            reason="not_applicable",
        )

    bank_open = _as_bool(_context_value(bank_ui_context, "bank_open", "bankOpen"))
    if bank_open is True:
        return PostBankReacquisitionContext(
            status="PASS",
            source_tick=tick,
            timing_millis=timing(),
            post_bank_reacquisition_needed=True,
            bank_ui_still_open=True,
            world_view_ready=False,
            resource_target_reacquisition_allowed=False,
            resource_target_available=False,
            reason="bank_ui_still_open",
        )

    if bank_open is None:
        return PostBankReacquisitionContext(
            status="WARN",
            warnings=["bank UI open/closed state unknown after banking complete"],
            missing_capabilities=capabilities.normalize_capability_names(["bank_ui.bankOpen"]),
            source_tick=tick,
            timing_millis=timing(),
            post_bank_reacquisition_needed=True,
            bank_ui_still_open=False,
            world_view_ready=None,
            resource_target_reacquisition_allowed=False,
            resource_target_available=False,
            reason="waiting_for_world_view",
        )

    target = _best_resource_target(target_context)
    target_available = bool(target)
    missing: list[str] = []
    warnings: list[str] = []
    if not target_available:
        missing.append("target.candidates")
        warnings.append("no resource target observed after bank UI closed")

    return PostBankReacquisitionContext(
        status="PASS" if target_available else "WARN",
        warnings=warnings,
        missing_capabilities=capabilities.normalize_capability_names(missing),
        source_tick=tick,
        timing_millis=timing(),
        post_bank_reacquisition_needed=True,
        bank_ui_still_open=False,
        world_view_ready=True,
        resource_target_reacquisition_allowed=True,
        resource_target_available=target_available,
        reason="resource_target_visible" if target_available else "no_resource_target_observed",
    )

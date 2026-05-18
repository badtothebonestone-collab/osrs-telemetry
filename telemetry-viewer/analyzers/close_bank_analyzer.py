from __future__ import annotations

import time
from typing import Any

import capabilities
import task_policy as task_policy_module

from analyzers.live_state import BankOperationContext, BankUiContext, CloseBankContext, PostBankReacquisitionContext


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "open", "visible", "ready", "available"}:
            return True
        if text in {"false", "no", "0", "closed", "hidden", "not_ready", "unavailable"}:
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


def _dict_value(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict) and value:
            return dict(value)
    return {}


def _source_tick(source_tick: int | None, *contexts: Any) -> int | None:
    if source_tick is not None:
        return source_tick
    for context in contexts:
        tick = _as_int(_context_value(context, "source_tick", "sourceTick"))
        if tick is not None:
            return tick
    return None


def _close_button_available(bank_ui_context: BankUiContext | dict[str, Any] | None) -> tuple[bool | None, bool | None]:
    available = _as_bool(_context_value(bank_ui_context, "close_button_available", "closeButtonAvailable"))
    visible = _as_bool(_context_value(bank_ui_context, "bank_close_button_visible", "closeButtonVisible"))
    if visible is None:
        visible = _as_bool(_context_value(bank_ui_context, "bankCloseButtonVisible", "bankCloseButtonVisible"))
    if available is None and visible is True:
        available = True
    return available, visible


def analyze_close_bank_context(
    policy: task_policy_module.TaskPolicy | dict[str, Any] | str | None,
    *,
    bank_ui_context: BankUiContext | dict[str, Any] | None,
    bank_operation_context: BankOperationContext | dict[str, Any] | None,
    post_bank_reacquisition_context: PostBankReacquisitionContext | dict[str, Any] | None = None,
    current_task_state: dict[str, Any] | None = None,
    source_tick: int | None = None,
) -> CloseBankContext:
    started = time.perf_counter()
    resolved_policy = task_policy_module.resolve_task_policy(policy)
    tick = _source_tick(source_tick, bank_ui_context, bank_operation_context, post_bank_reacquisition_context)
    timing = lambda: (time.perf_counter() - started) * 1000.0

    bank_open = _as_bool(_context_value(bank_ui_context, "bank_open", "bankOpen"))
    banking_complete = _as_bool(_context_value(bank_operation_context, "banking_complete", "bankingComplete", False)) is True
    close_button_available, close_button_visible = _close_button_available(bank_ui_context)
    close_button_widget = _dict_value(
        _context_value(bank_ui_context, "close_button_widget", "closeButtonWidget"),
        _context_value(bank_ui_context, "bank_close_button_widget", "bankCloseButtonWidget"),
    )
    close_button_bounds = _dict_value(
        _context_value(bank_ui_context, "close_button_bounds", "closeButtonBounds"),
        _context_value(bank_ui_context, "bank_close_button_bounds", "bankCloseButtonBounds"),
        close_button_widget.get("bounds") if isinstance(close_button_widget, dict) else None,
    )
    keyboard_close_possible = None
    for snake_key, camel_key in (
        ("keyboard_close_possible", "keyboardClosePossible"),
        ("escape_close_possible", "escapeClosePossible"),
        ("top_level_closable", "topLevelClosable"),
        ("top_level_interface_closable", "topLevelInterfaceClosable"),
    ):
        keyboard_close_possible = _as_bool(_context_value(bank_ui_context, snake_key, camel_key))
        if keyboard_close_possible is not None:
            break

    base_kwargs = {
        "source_tick": tick,
        "timing_millis": timing(),
        "bank_open": bank_open,
        "banking_complete": banking_complete,
        "close_button_visible": close_button_visible,
        "close_button_available": close_button_available,
        "close_button_widget": close_button_widget,
        "close_button_bounds": close_button_bounds,
        "keyboard_close_possible": keyboard_close_possible,
    }
    if resolved_policy.fullInventoryStrategy != task_policy_module.InventoryFullStrategy.NEEDS_SERVICE or resolved_policy.resourceDisposition != task_policy_module.ResourceDisposition.BANK:
        return CloseBankContext(reason="close_not_needed", **base_kwargs)

    if bank_open is False:
        return CloseBankContext(reason="close_not_needed", **base_kwargs)

    if not banking_complete:
        return CloseBankContext(reason="close_not_needed", **base_kwargs)

    post_bank_needed = _as_bool(_context_value(post_bank_reacquisition_context, "post_bank_reacquisition_needed", "postBankReacquisitionNeeded"))
    post_bank_reason = _context_value(post_bank_reacquisition_context, "reason", "reason")
    if bank_open is True:
        if close_button_available is True:
            return CloseBankContext(
                close_bank_needed=True,
                close_bank_ready=True,
                reason="close_button_available",
                **base_kwargs,
            )
        if keyboard_close_possible is True:
            return CloseBankContext(
                close_bank_needed=True,
                close_bank_ready=True,
                reason="bank_ui_still_open",
                **base_kwargs,
            )
        return CloseBankContext(
            status="WARN",
            warnings=[
                "bank close button not observed after banking complete"
                if post_bank_needed or post_bank_reason == "bank_ui_still_open"
                else "bank close button not observed"
            ],
            missing_capabilities=capabilities.normalize_capability_names(["bank_ui.close_button"]),
            close_bank_needed=True,
            close_bank_ready=False,
            reason="close_button_missing",
            **base_kwargs,
        )

    return CloseBankContext(
        status="WARN",
        warnings=["bank open/closed state unknown after banking complete"],
        missing_capabilities=capabilities.normalize_capability_names(["bank_ui.bankOpen"]),
        close_bank_needed=True,
        close_bank_ready=False,
        reason="unknown",
        **base_kwargs,
    )

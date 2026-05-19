from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .action_proposal import ActionProposal, build_action_proposal


SCHEMA = "action_lifecycle.v1"
DIAGNOSTIC_SCHEMA = "action_lifecycle_diagnostic.v1"

WAITING_INTENTS = {"wait_for_result", "wait_for_resource_result"}
WAITING_PHASES = {"wait_for_result", "waiting_for_result"}


@dataclass
class ActionLifecycleState:
    current_state: str = "idle"
    last_action: str | None = None
    last_action_tick: int | None = None
    last_execution_time_utc: str | None = None
    wait_started_tick: int | None = None
    wait_started_utc: str | None = None
    wait_reason: str | None = None
    expected_signal: str | None = None
    observed_signals: list[str] = field(default_factory=list)
    result_complete: bool = False
    result_outcome: str = "unknown"
    elapsed_ticks: int | None = None
    elapsed_millis: int | None = None
    timeout_ticks: int | None = None
    timeout_millis: int | None = None
    next_action_allowed: bool = False
    expected_result: dict[str, Any] | None = None
    observed_result: dict[str, Any] | None = None
    cooldown_until_tick: int | None = None
    cooldown_until_utc: str | None = None
    attempts: int = 0
    max_attempts: int = 1
    reason: str = "not_applicable"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "currentState": self.current_state,
            "lastAction": self.last_action,
            "lastActionTick": self.last_action_tick,
            "lastExecutionTimeUtc": self.last_execution_time_utc,
            "waitStartedTick": self.wait_started_tick,
            "waitStartedUtc": self.wait_started_utc,
            "waitReason": self.wait_reason,
            "expectedSignal": self.expected_signal,
            "observedSignals": list(self.observed_signals),
            "resultComplete": self.result_complete,
            "resultOutcome": self.result_outcome,
            "elapsedTicks": self.elapsed_ticks,
            "elapsedMillis": self.elapsed_millis,
            "timeoutTicks": self.timeout_ticks,
            "timeoutMillis": self.timeout_millis,
            "nextActionAllowed": self.next_action_allowed,
            "expectedResult": self.expected_result,
            "observedResult": self.observed_result,
            "cooldownUntilTick": self.cooldown_until_tick,
            "cooldownUntilUtc": self.cooldown_until_utc,
            "attempts": self.attempts,
            "maxAttempts": self.max_attempts,
            "reason": self.reason,
            "warnings": list(self.warnings),
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_after_ms(ms: int) -> str:
    value = datetime.now(timezone.utc) + timedelta(milliseconds=max(0, int(ms)))
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "open", "ready", "available", "complete"}:
            return True
        if text in {"false", "no", "0", "closed", "not_ready", "unavailable", "incomplete"}:
            return False
    return None


def _int(value: Any) -> int | None:
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


def _status_brain(status: dict[str, Any]) -> dict[str, Any]:
    return _dict(status.get("brain")) or status


def _source_tick(status: dict[str, Any]) -> int | None:
    for key in ("latestTick", "lastProcessedTick", "latestTickProcessed", "sourceTick"):
        value = _int(status.get(key))
        if value is not None:
            return value
    nested = _dict(status.get("status"))
    for key in ("latestTick", "lastProcessedTick", "latestTickProcessed", "sourceTick"):
        value = _int(nested.get(key))
        if value is not None:
            return value
    return None


def generic_state(status: dict[str, Any]) -> dict[str, Any]:
    brain = _status_brain(status)
    return _dict(brain.get("genericTaskState"))


def phase_and_intent(status: dict[str, Any]) -> tuple[str | None, str | None]:
    generic = generic_state(status)
    phase = generic.get("phase") or status.get("phase") or status.get("brainPhase")
    intent = generic.get("activeIntent") or status.get("activeIntent")
    return (str(phase) if phase is not None else None, str(intent) if intent is not None else None)


def is_waiting_for_result(status: dict[str, Any]) -> bool:
    phase, intent = phase_and_intent(status)
    return (phase in WAITING_PHASES) or (intent in WAITING_INTENTS)


def context_value(status: dict[str, Any], context_name: str, key: str, *fallback_keys: str) -> Any:
    brain = _status_brain(status)
    context = _dict(brain.get(context_name))
    if key in context:
        return context.get(key)
    for fallback in fallback_keys:
        if fallback in context:
            return context.get(fallback)
        if fallback in status:
            return status.get(fallback)
    return status.get(key)


def _context(status: dict[str, Any], context_name: str) -> dict[str, Any]:
    brain = _status_brain(status)
    value = brain.get(context_name)
    if isinstance(value, dict):
        return value
    value = status.get(context_name)
    return value if isinstance(value, dict) else {}


def _inventory_progress(status: dict[str, Any]) -> dict[str, Any]:
    inventory = _context(status, "inventoryContext")
    progress = inventory.get("progress")
    if isinstance(progress, dict):
        return progress
    progress = status.get("brainProgress")
    return progress if isinstance(progress, dict) else {}


def _held_resource_count(status: dict[str, Any]) -> int | None:
    progress = _inventory_progress(status)
    for key in ("currentHeldCount", "currentHeldResourceCount", "heldResourceCount"):
        value = _int(progress.get(key))
        if value is not None:
            return value
    for key in ("brainCurrentHeldCount", "inventoryMatchingResourceCount", "heldResourceCount"):
        value = _int(status.get(key))
        if value is not None:
            return value
    return None


def _goal_progress_count(status: dict[str, Any]) -> int | None:
    progress = _inventory_progress(status)
    for key in ("displayedGoalProgress", "goalProgress", "currentGoalProgress"):
        value = _int(progress.get(key))
        if value is not None:
            return value
    progress = status.get("brainProgress")
    if isinstance(progress, dict):
        for key in ("displayedGoalProgress", "goalProgress", "currentGoalProgress"):
            value = _int(progress.get(key))
            if value is not None:
                return value
    return _int(status.get("displayedGoalProgress"))


def _inventory_free_slots(status: dict[str, Any]) -> int | None:
    inventory = _context(status, "inventoryContext")
    for key in ("freeSlots", "inventoryFreeSlots"):
        value = _int(inventory.get(key))
        if value is not None:
            return value
        value = _int(status.get(key))
        if value is not None:
            return value
    return None


def _inventory_signature(status: dict[str, Any]) -> str | None:
    progress = _inventory_progress(status)
    for key in ("currentInventorySignature", "inventorySignature"):
        value = progress.get(key)
        if value is not None:
            return str(value)
    value = status.get("brainCurrentInventorySignature") or status.get("inventorySignature")
    return str(value) if value is not None else None


def _activity_context(status: dict[str, Any]) -> dict[str, Any]:
    for key in ("activityContext", "activity"):
        value = _context(status, key)
        if value:
            return value
    return {}


def _activity_current(status: dict[str, Any]) -> str:
    activity = _activity_context(status)
    raw = _dict(activity.get("raw"))
    payload = _dict(raw.get("activityState") or raw.get("activity"))
    value = (
        activity.get("currentActivity")
        or activity.get("current_activity")
        or activity.get("apparentState")
        or payload.get("apparentState")
        or payload.get("state")
        or status.get("activityCurrentState")
        or "unknown"
    )
    return str(value).lower()


def _activity_recent_signals(status: dict[str, Any]) -> list[str]:
    activity = _activity_context(status)
    signals = activity.get("recentTaskSignals") or activity.get("recent_task_signals") or status.get("activityRecentTaskSignals") or []
    if not isinstance(signals, list):
        signals = [signals]
    raw = _dict(activity.get("raw"))
    woodcutting = _dict(raw.get("woodcuttingState") or activity.get("woodcuttingState"))
    if str(woodcutting.get("woodcuttingState") or "").lower() == "target_depleted":
        signals = [*signals, "target depleted recently"]
    return [str(signal).lower() for signal in signals if signal is not None]


def _blocking_conditions(status: dict[str, Any]) -> list[str]:
    value = generic_state(status).get("blockingConditions") or status.get("blockingConditions") or []
    if not isinstance(value, list):
        value = [value]
    return [str(item) for item in value if item]


def _service_ready(status: dict[str, Any]) -> bool | None:
    return _bool(context_value(status, "serviceContext", "serviceReady", "serviceReady"))


def _path_metric(status: dict[str, Any]) -> float | None:
    for key in (
        "distanceToDestination",
        "distanceToPathTarget",
        "navigationIntentDistanceTiles",
        "distanceToServiceTarget",
    ):
        value = context_value(status, "pathingContext", key, key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def _player_tile_key(status: dict[str, Any]) -> str | None:
    player = _context(status, "playerContext") or _context(status, "player")
    tile = player.get("tile") or player.get("worldTile") or status.get("playerTile") or status.get("playerWorldTile")
    if isinstance(tile, dict):
        x = tile.get("x") or tile.get("worldX")
        y = tile.get("y") or tile.get("worldY")
        plane = tile.get("plane")
        return f"{x},{y},{plane}" if x is not None and y is not None else None
    return str(tile) if tile is not None else None


def proposal_action_id(proposal: ActionProposal) -> str:
    tick = proposal.source_tick if proposal.source_tick is not None else "unknown"
    target = proposal.target_name or proposal.target_kind or "none"
    return f"{tick}:{proposal.proposed_action}:{target}"


def expected_result_for_action(action: str) -> dict[str, Any]:
    if action == "select_resource_target":
        return {
            "action": action,
            "resultType": "wait_for_result_or_activity",
            "expectedSignal": "inventory_or_progress_or_activity_or_depletion",
            "description": "phase or intent enters wait_for_result, activity changes, or progress eventually changes",
        }
    if action == "open_service":
        return {
            "action": action,
            "resultType": "bank_ui_open",
            "expectedSignal": "bank_open_or_readable",
            "description": "bankOpen=true or a service UI becomes visible",
        }
    if action in {"deposit_inventory", "deposit_resources"}:
        return {
            "action": action,
            "resultType": "resources_deposited",
            "expectedSignal": "resource_items_cleared_or_banking_complete",
            "description": "resourceItemsHeld decreases or bankingComplete=true",
        }
    if action == "close_bank":
        return {
            "action": action,
            "resultType": "bank_closed",
            "expectedSignal": "bank_open_false",
            "description": "bankOpen=false",
        }
    if action == "return_to_resource_area":
        return {
            "action": action,
            "resultType": "resource_return_progress",
            "expectedSignal": "movement_or_resource_target_visible",
            "description": "movement, path, position, or resource return state changes",
        }
    if action == "navigate_to_service":
        return {
            "action": action,
            "resultType": "service_navigation_progress",
            "expectedSignal": "movement_or_service_ready",
            "description": "movement, path, position, or service distance changes",
        }
    return {
        "action": action,
        "resultType": "none",
        "expectedSignal": "none",
        "description": "no action result expected",
    }


def _resource_items_held(status: dict[str, Any]) -> int | None:
    value = context_value(status, "bankOperationContext", "resourceItemsHeld", "bankOperationResourceItemsHeld")
    return _int(value)


def _bank_open(status: dict[str, Any]) -> bool | None:
    return _bool(context_value(status, "bankUiContext", "bankOpen", "bankOpen"))


def _banking_complete(status: dict[str, Any]) -> bool | None:
    return _bool(context_value(status, "bankOperationContext", "bankingComplete", "bankingComplete"))


def _timeout_reached(elapsed_ms: int | None, timeout_ms: int | None, elapsed_ticks: int | None, timeout_ticks: int | None) -> bool:
    if timeout_ms is not None and elapsed_ms is not None and elapsed_ms >= timeout_ms:
        return True
    return timeout_ticks is not None and elapsed_ticks is not None and elapsed_ticks >= timeout_ticks


def _completion_payload(
    action: str,
    before_status: dict[str, Any],
    after_status: dict[str, Any],
    *,
    elapsed_ms: int | None = None,
    timeout_ms: int | None = None,
    wait_started_tick: int | None = None,
    timeout_ticks: int | None = None,
) -> dict[str, Any]:
    expected = expected_result_for_action(action)
    phase, intent = phase_and_intent(after_status)
    after_tick = _source_tick(after_status)
    start_tick = wait_started_tick if wait_started_tick is not None else _source_tick(before_status)
    elapsed_ticks = after_tick - start_tick if after_tick is not None and start_tick is not None else None
    return {
        "action": action,
        "verificationStatus": "WARN",
        "observedResult": "waiting",
        "phase": phase,
        "activeIntent": intent,
        "expectedSignal": expected.get("expectedSignal"),
        "observedSignals": [],
        "resultComplete": False,
        "resultOutcome": "still_waiting",
        "elapsedTicks": elapsed_ticks,
        "elapsedMillis": elapsed_ms,
        "timeoutTicks": timeout_ticks,
        "timeoutMillis": timeout_ms,
        "nextActionAllowed": False,
        "warnings": [],
    }


def _finish(
    observed: dict[str, Any],
    *,
    status: str,
    result: str,
    outcome: str,
    complete: bool,
    next_allowed: bool,
) -> dict[str, Any]:
    observed["verificationStatus"] = status
    observed["observedResult"] = result
    observed["resultOutcome"] = outcome
    observed["resultComplete"] = bool(complete)
    observed["nextActionAllowed"] = bool(next_allowed)
    return observed


def _add_signal(observed: dict[str, Any], signal: str) -> None:
    signals = observed.setdefault("observedSignals", [])
    if signal not in signals:
        signals.append(signal)


def verify_expected_result(
    action: str,
    before_status: dict[str, Any] | None,
    after_status: dict[str, Any] | None,
    *,
    elapsed_ms: int | None = None,
    timeout_ms: int | None = None,
    wait_started_tick: int | None = None,
    timeout_ticks: int | None = None,
) -> dict[str, Any]:
    before = before_status if isinstance(before_status, dict) else {}
    after = after_status if isinstance(after_status, dict) else {}
    observed = _completion_payload(
        action,
        before,
        after,
        elapsed_ms=elapsed_ms,
        timeout_ms=timeout_ms,
        wait_started_tick=wait_started_tick,
        timeout_ticks=timeout_ticks,
    )
    phase = observed.get("phase")
    intent = observed.get("activeIntent")
    timed_out = _timeout_reached(
        observed.get("elapsedMillis"),
        observed.get("timeoutMillis"),
        observed.get("elapsedTicks"),
        observed.get("timeoutTicks"),
    )
    if action == "select_resource_target":
        if phase == "blocked" or _blocking_conditions(after):
            _add_signal(observed, "blocked_phase")
            return _finish(observed, status="FAIL", result="interrupted", outcome="interrupted", complete=True, next_allowed=False)
        if _bank_open(after) is True or _service_ready(after) is True:
            _add_signal(observed, "unexpected_service_context")
            return _finish(observed, status="FAIL", result="interrupted", outcome="interrupted", complete=True, next_allowed=False)
        before_signature = _inventory_signature(before)
        after_signature = _inventory_signature(after)
        if before_signature is not None and after_signature is not None and before_signature != after_signature:
            _add_signal(observed, "inventory_changed")
        before_free = _inventory_free_slots(before)
        after_free = _inventory_free_slots(after)
        if before_free is not None and after_free is not None and before_free != after_free:
            _add_signal(observed, "inventory_free_slots_changed")
        before_held = _held_resource_count(before)
        after_held = _held_resource_count(after)
        if before_held is not None and after_held is not None and after_held > before_held:
            _add_signal(observed, "held_resource_count_increased")
        before_progress = _goal_progress_count(before)
        after_progress = _goal_progress_count(after)
        if before_progress is not None and after_progress is not None and after_progress > before_progress:
            _add_signal(observed, "resource_progress_increased")
        current_activity = _activity_current(after)
        if current_activity in {"interacting", "animating", "likely_busy"}:
            _add_signal(observed, f"activity_{current_activity}")
        if any("depleted" in signal for signal in _activity_recent_signals(after)):
            _add_signal(observed, "target_depleted_recently")
        if is_waiting_for_result(after):
            _add_signal(observed, "wait_for_result_state")
        signals = set(observed["observedSignals"])
        if "target_depleted_recently" in signals:
            return _finish(observed, status="PASS", result="target_depleted", outcome="depleted", complete=True, next_allowed=True)
        if "held_resource_count_increased" in signals or "inventory_changed" in signals or "inventory_free_slots_changed" in signals:
            return _finish(observed, status="PASS", result="inventory_changed", outcome="success", complete=True, next_allowed=True)
        if "resource_progress_increased" in signals:
            return _finish(observed, status="PASS", result="resource_progress_increased", outcome="progress", complete=True, next_allowed=True)
        if any(signal.startswith("activity_") for signal in signals):
            return _finish(observed, status="PASS", result="activity_progress", outcome="progress", complete=True, next_allowed=True)
        if timed_out:
            observed["warnings"].append("resource result timed out without progress")
            return _finish(observed, status="FAIL", result="no_change_timeout", outcome="no_change_timeout", complete=True, next_allowed=False)
        if "wait_for_result_state" in signals:
            return _finish(observed, status="PASS", result="wait_for_result", outcome="still_waiting", complete=False, next_allowed=False)
        observed["warnings"].append("resource result not observed yet")
        return _finish(observed, status="WARN", result="waiting", outcome="still_waiting", complete=False, next_allowed=False)
    if action == "open_service":
        bank_open = _bank_open(after)
        if bank_open is True or _bool(context_value(after, "bankUiContext", "bankRootVisible", "bankRootVisible")) is True:
            _add_signal(observed, "bank_open")
            return _finish(observed, status="PASS", result="service_open", outcome="success", complete=True, next_allowed=True)
        if timed_out:
            observed["warnings"].append("bank UI did not open before timeout")
            return _finish(observed, status="FAIL", result="no_change_timeout", outcome="no_change_timeout", complete=True, next_allowed=False)
        observed["warnings"].append("bank UI not open yet")
        return _finish(observed, status="WARN", result="waiting", outcome="still_waiting", complete=False, next_allowed=False)
    if action in {"deposit_inventory", "deposit_resources"}:
        before_held = _resource_items_held(before)
        after_held = _resource_items_held(after)
        if _banking_complete(after) is True or after_held == 0:
            _add_signal(observed, "banking_complete")
            observed["resourceItemsHeldBefore"] = before_held
            observed["resourceItemsHeldAfter"] = after_held
            return _finish(observed, status="PASS", result="banking_complete", outcome="success", complete=True, next_allowed=True)
        elif before_held is not None and after_held is not None and after_held < before_held:
            _add_signal(observed, "resource_count_decreased")
            observed["resourceItemsHeldBefore"] = before_held
            observed["resourceItemsHeldAfter"] = after_held
            return _finish(observed, status="PASS", result="resource_count_decreased", outcome="progress", complete=True, next_allowed=True)
        if timed_out:
            observed["warnings"].append("resource deposit result timed out")
            observed["resourceItemsHeldBefore"] = before_held
            observed["resourceItemsHeldAfter"] = after_held
            return _finish(observed, status="FAIL", result="no_change_timeout", outcome="no_change_timeout", complete=True, next_allowed=False)
        observed["warnings"].append("resource deposit result not observed yet")
        observed["resourceItemsHeldBefore"] = before_held
        observed["resourceItemsHeldAfter"] = after_held
        return _finish(observed, status="WARN", result="waiting", outcome="still_waiting", complete=False, next_allowed=False)
    if action == "close_bank":
        if _bank_open(after) is False:
            _add_signal(observed, "bank_closed")
            return _finish(observed, status="PASS", result="bank_closed", outcome="success", complete=True, next_allowed=True)
        if timed_out:
            observed["warnings"].append("bank UI remained open before timeout")
            return _finish(observed, status="FAIL", result="no_change_timeout", outcome="no_change_timeout", complete=True, next_allowed=False)
        observed["warnings"].append("bank UI still open")
        return _finish(observed, status="WARN", result="waiting", outcome="still_waiting", complete=False, next_allowed=False)
    if action in {"return_to_resource_area", "navigate_to_service"}:
        before_metric = _path_metric(before)
        after_metric = _path_metric(after)
        if before_metric is not None and after_metric is not None and after_metric != before_metric:
            _add_signal(observed, "path_distance_changed")
        before_tile = _player_tile_key(before)
        after_tile = _player_tile_key(after)
        if before_tile is not None and after_tile is not None and after_tile != before_tile:
            _add_signal(observed, "player_position_changed")
        if _service_ready(after) is True:
            _add_signal(observed, "service_ready")
        if _bool(context_value(after, "returnToResourceContext", "resourceTargetAvailable", "returnResourceTargetAvailable")) is True:
            _add_signal(observed, "resource_target_visible")
        if is_waiting_for_result(after) or str(intent or "").startswith(("navigate", "return", "move")):
            _add_signal(observed, "movement_or_wait_state")
        if observed["observedSignals"]:
            return _finish(observed, status="PASS", result="movement_or_wait_state", outcome="progress", complete=True, next_allowed=True)
        if timed_out:
            observed["warnings"].append("movement result timed out")
            return _finish(observed, status="FAIL", result="no_change_timeout", outcome="no_change_timeout", complete=True, next_allowed=False)
        observed["warnings"].append("movement result not observed yet")
        return _finish(observed, status="WARN", result="waiting", outcome="still_waiting", complete=False, next_allowed=False)
    return _finish(observed, status="PASS", result="not_applicable", outcome="success", complete=True, next_allowed=True)


def lifecycle_state_for_proposal(proposal: ActionProposal, *, max_attempts: int = 1) -> ActionLifecycleState:
    expected = expected_result_for_action(proposal.proposed_action)
    if proposal.proposed_action in {"none", "wait_for_context"} or not proposal.executable:
        return ActionLifecycleState(
            current_state="blocked",
            last_action=proposal.proposed_action,
            last_action_tick=proposal.source_tick,
            expected_result=expected,
            expected_signal=expected.get("expectedSignal"),
            attempts=0,
            max_attempts=max_attempts,
            reason=proposal.reason,
            warnings=list(proposal.warnings),
        )
    return ActionLifecycleState(
        current_state="proposed",
        last_action=proposal.proposed_action,
        last_action_tick=proposal.source_tick,
        expected_result=expected,
        expected_signal=expected.get("expectedSignal"),
        attempts=0,
        max_attempts=max_attempts,
        reason=proposal.reason,
        warnings=list(proposal.warnings),
    )


def lifecycle_after_execution(
    proposal: ActionProposal,
    *,
    executed: bool,
    dry_run: bool,
    before_status: dict[str, Any] | None = None,
    after_status: dict[str, Any] | None = None,
    cooldown_ms: int = 0,
    elapsed_ms: int | None = None,
    timeout_ms: int | None = None,
    attempts: int = 1,
    max_attempts: int = 1,
) -> ActionLifecycleState:
    expected = expected_result_for_action(proposal.proposed_action)
    observed = (
        verify_expected_result(
            proposal.proposed_action,
            before_status,
            after_status,
            elapsed_ms=elapsed_ms,
            timeout_ms=timeout_ms,
            wait_started_tick=proposal.source_tick,
        )
        if after_status is not None
        else None
    )
    state = "proposed" if dry_run else ("executed" if executed else "blocked")
    reason = proposal.reason
    result_complete = bool(observed.get("resultComplete")) if isinstance(observed, dict) else False
    result_outcome = str(observed.get("resultOutcome") or "unknown") if isinstance(observed, dict) else "unknown"
    next_action_allowed = bool(observed.get("nextActionAllowed")) if isinstance(observed, dict) else False
    if executed:
        if result_complete and result_outcome in {"success", "progress", "depleted"}:
            state = "verified"
            reason = result_outcome
        elif result_complete and result_outcome == "no_change_timeout":
            state = "timed_out"
            reason = "action_timeout"
        elif result_complete and result_outcome == "interrupted":
            state = "blocked"
            reason = "interrupted"
        elif proposal.proposed_action == "select_resource_target" and after_status is not None and is_waiting_for_result(after_status):
            state = "waiting_for_result"
            reason = "client_processing_previous_action"
        elif observed and observed.get("verificationStatus") == "PASS":
            state = "verified"
            reason = str(observed.get("observedResult") or "expected_result_verified")
        elif observed:
            state = "waiting_for_result"
            reason = "awaiting_expected_result"
    cooldown_until_utc = utc_after_ms(cooldown_ms) if cooldown_ms >= 0 else None
    return ActionLifecycleState(
        current_state=state,
        last_action=proposal.proposed_action,
        last_action_tick=proposal.source_tick,
        last_execution_time_utc=utc_now_iso() if executed else None,
        wait_started_tick=proposal.source_tick if executed and state == "waiting_for_result" else None,
        wait_started_utc=utc_now_iso() if executed and state == "waiting_for_result" else None,
        wait_reason=reason if state == "waiting_for_result" else None,
        expected_signal=expected.get("expectedSignal"),
        observed_signals=list(observed.get("observedSignals") or []) if isinstance(observed, dict) else [],
        result_complete=result_complete,
        result_outcome=result_outcome,
        elapsed_ticks=observed.get("elapsedTicks") if isinstance(observed, dict) else None,
        elapsed_millis=observed.get("elapsedMillis") if isinstance(observed, dict) else None,
        timeout_ticks=observed.get("timeoutTicks") if isinstance(observed, dict) else None,
        timeout_millis=observed.get("timeoutMillis") if isinstance(observed, dict) else None,
        next_action_allowed=next_action_allowed,
        expected_result=expected,
        observed_result=observed,
        cooldown_until_utc=cooldown_until_utc,
        attempts=attempts,
        max_attempts=max_attempts,
        reason=reason,
        warnings=list(proposal.warnings),
    )


def infer_lifecycle_state(status: dict[str, Any]) -> ActionLifecycleState:
    proposal = build_action_proposal(status)
    if is_waiting_for_result(status):
        expected = expected_result_for_action("select_resource_target")
        observed = verify_expected_result("select_resource_target", None, status)
        return ActionLifecycleState(
            current_state="waiting_for_result",
            last_action=None,
            last_action_tick=proposal.source_tick,
            expected_result=expected,
            observed_result=observed,
            expected_signal=expected.get("expectedSignal"),
            observed_signals=list(observed.get("observedSignals") or []),
            result_complete=bool(observed.get("resultComplete")),
            result_outcome=str(observed.get("resultOutcome") or "still_waiting"),
            elapsed_ticks=observed.get("elapsedTicks"),
            elapsed_millis=observed.get("elapsedMillis"),
            timeout_ticks=observed.get("timeoutTicks"),
            timeout_millis=observed.get("timeoutMillis"),
            next_action_allowed=bool(observed.get("nextActionAllowed")),
            reason="client_processing_previous_action",
        )
    return lifecycle_state_for_proposal(proposal)


def build_lifecycle_diagnostic(status: dict[str, Any]) -> dict[str, Any]:
    phase, intent = phase_and_intent(status)
    lifecycle = infer_lifecycle_state(status)
    warnings = list(lifecycle.warnings)
    diagnostic_status = "PASS"
    if lifecycle.current_state in {"waiting_for_result", "blocked", "timed_out"}:
        diagnostic_status = "WARN" if lifecycle.current_state != "timed_out" else "FAIL"
    return {
        "schema": DIAGNOSTIC_SCHEMA,
        "status": diagnostic_status,
        "cycleStage": status.get("currentCycleStage") or status.get("cycleStage") or "unknown",
        "phase": phase or "unknown",
        "activeIntent": intent or "unknown",
        "lifecycleState": lifecycle.to_dict(),
        "lastAction": lifecycle.last_action,
        "expectedResult": lifecycle.expected_result,
        "observedResult": lifecycle.observed_result,
        "waitStartedTick": lifecycle.wait_started_tick,
        "waitStartedUtc": lifecycle.wait_started_utc,
        "waitReason": lifecycle.wait_reason,
        "expectedSignal": lifecycle.expected_signal,
        "observedSignals": list(lifecycle.observed_signals),
        "resultComplete": lifecycle.result_complete,
        "resultOutcome": lifecycle.result_outcome,
        "elapsedTicks": lifecycle.elapsed_ticks,
        "elapsedMillis": lifecycle.elapsed_millis,
        "timeoutTicks": lifecycle.timeout_ticks,
        "timeoutMillis": lifecycle.timeout_millis,
        "nextActionAllowed": lifecycle.next_action_allowed,
        "cooldown": {
            "cooldownUntilTick": lifecycle.cooldown_until_tick,
            "cooldownUntilUtc": lifecycle.cooldown_until_utc,
        },
        "attempts": lifecycle.attempts,
        "reason": lifecycle.reason,
        "warnings": warnings,
        "missingCapabilities": [],
    }

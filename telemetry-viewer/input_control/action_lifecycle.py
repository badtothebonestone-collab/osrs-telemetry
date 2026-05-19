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


def proposal_action_id(proposal: ActionProposal) -> str:
    tick = proposal.source_tick if proposal.source_tick is not None else "unknown"
    target = proposal.target_name or proposal.target_kind or "none"
    return f"{tick}:{proposal.proposed_action}:{target}"


def expected_result_for_action(action: str) -> dict[str, Any]:
    if action == "select_resource_target":
        return {
            "action": action,
            "resultType": "wait_for_result_or_activity",
            "description": "phase or intent enters wait_for_result, activity changes, or progress eventually changes",
        }
    if action == "open_service":
        return {
            "action": action,
            "resultType": "bank_ui_open",
            "description": "bankOpen=true or a service UI becomes visible",
        }
    if action in {"deposit_inventory", "deposit_resources"}:
        return {
            "action": action,
            "resultType": "resources_deposited",
            "description": "resourceItemsHeld decreases or bankingComplete=true",
        }
    if action == "close_bank":
        return {
            "action": action,
            "resultType": "bank_closed",
            "description": "bankOpen=false",
        }
    if action == "return_to_resource_area":
        return {
            "action": action,
            "resultType": "resource_return_progress",
            "description": "movement, path, position, or resource return state changes",
        }
    if action == "navigate_to_service":
        return {
            "action": action,
            "resultType": "service_navigation_progress",
            "description": "movement, path, position, or service distance changes",
        }
    return {
        "action": action,
        "resultType": "none",
        "description": "no action result expected",
    }


def _resource_items_held(status: dict[str, Any]) -> int | None:
    value = context_value(status, "bankOperationContext", "resourceItemsHeld", "bankOperationResourceItemsHeld")
    return _int(value)


def _bank_open(status: dict[str, Any]) -> bool | None:
    return _bool(context_value(status, "bankUiContext", "bankOpen", "bankOpen"))


def _banking_complete(status: dict[str, Any]) -> bool | None:
    return _bool(context_value(status, "bankOperationContext", "bankingComplete", "bankingComplete"))


def verify_expected_result(action: str, before_status: dict[str, Any] | None, after_status: dict[str, Any] | None) -> dict[str, Any]:
    before = before_status if isinstance(before_status, dict) else {}
    after = after_status if isinstance(after_status, dict) else {}
    phase, intent = phase_and_intent(after)
    observed: dict[str, Any] = {
        "action": action,
        "verificationStatus": "WARN",
        "observedResult": "waiting",
        "phase": phase,
        "activeIntent": intent,
        "warnings": [],
    }
    if action == "select_resource_target":
        if is_waiting_for_result(after):
            observed.update({"verificationStatus": "PASS", "observedResult": "wait_for_result"})
        else:
            observed["warnings"].append("resource result not observed yet")
        return observed
    if action == "open_service":
        bank_open = _bank_open(after)
        if bank_open is True or _bool(context_value(after, "bankUiContext", "bankRootVisible", "bankRootVisible")) is True:
            observed.update({"verificationStatus": "PASS", "observedResult": "service_open"})
        else:
            observed["warnings"].append("bank UI not open yet")
        return observed
    if action in {"deposit_inventory", "deposit_resources"}:
        before_held = _resource_items_held(before)
        after_held = _resource_items_held(after)
        if _banking_complete(after) is True or after_held == 0:
            observed.update({"verificationStatus": "PASS", "observedResult": "banking_complete"})
        elif before_held is not None and after_held is not None and after_held < before_held:
            observed.update({"verificationStatus": "PASS", "observedResult": "resource_count_decreased"})
        else:
            observed["warnings"].append("resource deposit result not observed yet")
        observed["resourceItemsHeldBefore"] = before_held
        observed["resourceItemsHeldAfter"] = after_held
        return observed
    if action == "close_bank":
        if _bank_open(after) is False:
            observed.update({"verificationStatus": "PASS", "observedResult": "bank_closed"})
        else:
            observed["warnings"].append("bank UI still open")
        return observed
    if action in {"return_to_resource_area", "navigate_to_service"}:
        if is_waiting_for_result(after) or str(intent or "").startswith(("navigate", "return", "move")):
            observed.update({"verificationStatus": "PASS", "observedResult": "movement_or_wait_state"})
        else:
            observed["warnings"].append("movement result not observed yet")
        return observed
    observed.update({"verificationStatus": "PASS", "observedResult": "not_applicable"})
    return observed


def lifecycle_state_for_proposal(proposal: ActionProposal, *, max_attempts: int = 1) -> ActionLifecycleState:
    if proposal.proposed_action in {"none", "wait_for_context"} or not proposal.executable:
        return ActionLifecycleState(
            current_state="blocked",
            last_action=proposal.proposed_action,
            last_action_tick=proposal.source_tick,
            expected_result=expected_result_for_action(proposal.proposed_action),
            attempts=0,
            max_attempts=max_attempts,
            reason=proposal.reason,
            warnings=list(proposal.warnings),
        )
    return ActionLifecycleState(
        current_state="proposed",
        last_action=proposal.proposed_action,
        last_action_tick=proposal.source_tick,
        expected_result=expected_result_for_action(proposal.proposed_action),
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
    attempts: int = 1,
    max_attempts: int = 1,
) -> ActionLifecycleState:
    expected = expected_result_for_action(proposal.proposed_action)
    observed = verify_expected_result(proposal.proposed_action, before_status, after_status) if after_status is not None else None
    state = "proposed" if dry_run else ("executed" if executed else "blocked")
    reason = proposal.reason
    if executed:
        if proposal.proposed_action == "select_resource_target" and after_status is not None and is_waiting_for_result(after_status):
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
        return ActionLifecycleState(
            current_state="waiting_for_result",
            last_action=None,
            last_action_tick=proposal.source_tick,
            expected_result=expected_result_for_action("select_resource_target"),
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
        "cooldown": {
            "cooldownUntilTick": lifecycle.cooldown_until_tick,
            "cooldownUntilUtc": lifecycle.cooldown_until_utc,
        },
        "attempts": lifecycle.attempts,
        "reason": lifecycle.reason,
        "warnings": warnings,
        "missingCapabilities": [],
    }

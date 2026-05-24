from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import capabilities
import task_policy as task_policy_module


TASK_STATE_SCHEMA = "generic_task_state.v1"


class TaskPhase(str, Enum):
    OBSERVE = "observe"
    SELECT_TARGET = "select_target"
    TARGET_SELECTED = "target_selected"
    WAIT_FOR_RESULT = "wait_for_result"
    INVENTORY_FULL = "inventory_full"
    NAVIGATE_TO_SERVICE = "navigate_to_service"
    SERVICE_AVAILABLE = "service_available"
    SERVICE_OPEN = "service_open"
    SERVICE_INTERACTION_PENDING = "service_interaction_pending"
    SERVICE_COMPLETE = "service_complete"
    RETURN_TO_RESOURCE = "return_to_resource"
    GOAL_COMPLETE = "goal_complete"
    BLOCKED = "blocked"
    NEEDS_MORE_CONTEXT = "needs_more_context"
    UNKNOWN = "unknown"


class TaskIntent(str, Enum):
    OBSERVE = "observe"
    NONE = "none"
    SELECT_TARGET = "select_target"
    CONTINUE_CURRENT_TARGET = "continue_current_target"
    CONTINUE_TASK = "continue_task"
    TARGET_SELECTED = "target_selected"
    WAIT_FOR_RESULT = "wait_for_result"
    NEEDS_SERVICE = "needs_service"
    PROCESS_INVENTORY = "process_inventory"
    NAVIGATE_TO_SERVICE = "navigate_to_service"
    SERVICE_AVAILABLE = "service_available"
    SERVICE_OPEN = "service_open"
    SERVICE_INTERACTION_PENDING = "service_interaction_pending"
    BANK_OPERATION_PENDING = "bank_operation_pending"
    PROCESS_SERVICE_INVENTORY = "process_service_inventory"
    RESUME_RESOURCE_COLLECTION = "resume_resource_collection"
    NEEDS_USER_RESOLUTION = "needs_user_resolution"
    GOAL_COMPLETE = "goal_complete"
    BLOCKED = "blocked"
    NEEDS_MORE_CONTEXT = "needs_more_context"
    UNKNOWN = "unknown"


@dataclass
class TaskState:
    task: str
    phase: TaskPhase | str = TaskPhase.UNKNOWN
    previousPhase: TaskPhase | str | None = None
    confidence: float | None = None
    reason: str | None = None
    activeIntent: TaskIntent | str = TaskIntent.UNKNOWN
    selectedTargetKey: str | None = None
    activeIntentTarget: dict[str, Any] | None = None
    availableTarget: dict[str, Any] | None = None
    previousIntentTarget: dict[str, Any] | None = None
    requiredCapabilities: list[str] = field(default_factory=list)
    missingCapabilities: list[str] = field(default_factory=list)
    blockingConditions: list[str] = field(default_factory=list)
    observationNeeds: list[dict[str, Any]] = field(default_factory=list)
    goalProgress: dict[str, Any] = field(default_factory=dict)
    taskPolicy: dict[str, Any] = field(default_factory=dict)
    inventoryExpectation: str | None = None
    fullInventoryStrategy: str | None = None
    resourceDisposition: str | None = None
    serviceTypeNeeded: str | None = None
    processTypeNeeded: str | None = None
    noActionEmitted: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "phase": enum_value(self.phase),
            "previousPhase": enum_value(self.previousPhase),
            "confidence": self.confidence,
            "reason": self.reason,
            "activeIntent": enum_value(self.activeIntent),
            "selectedTargetKey": self.selectedTargetKey,
            "activeIntentTarget": self.activeIntentTarget,
            "availableTarget": self.availableTarget,
            "previousIntentTarget": self.previousIntentTarget,
            "requiredCapabilities": capabilities.normalize_capability_names(self.requiredCapabilities),
            "missingCapabilities": capabilities.normalize_capability_names(self.missingCapabilities),
            "blockingConditions": list(self.blockingConditions),
            "observationNeeds": normalize_observation_needs(self.observationNeeds),
            "goalProgress": dict(self.goalProgress),
            "taskPolicy": dict(self.taskPolicy),
            "inventoryExpectation": self.inventoryExpectation,
            "fullInventoryStrategy": self.fullInventoryStrategy,
            "resourceDisposition": self.resourceDisposition,
            "serviceTypeNeeded": self.serviceTypeNeeded,
            "processTypeNeeded": self.processTypeNeeded,
            "noActionEmitted": True,
        }


@dataclass
class TaskTransition:
    task: str
    previousPhase: TaskPhase | str | None
    nextPhase: TaskPhase | str
    reason: str | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "previousPhase": enum_value(self.previousPhase),
            "nextPhase": enum_value(self.nextPhase),
            "reason": self.reason,
            "confidence": self.confidence,
            "noActionEmitted": True,
        }


@dataclass
class TaskStateResult:
    state: TaskState
    transition: TaskTransition | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"schema": TASK_STATE_SCHEMA, **self.state.to_dict()}
        if self.transition is not None:
            payload["transition"] = self.transition.to_dict()
        return payload


def enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def normalize_observation_needs(needs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for need in needs or []:
        if not isinstance(need, dict):
            continue
        item = dict(need)
        if item.get("capability"):
            item["capability"] = capabilities.normalize_capability_name(item.get("capability"))
        key = (str(item.get("capability") or ""), str(item.get("status") or ""), str(item.get("reason") or ""))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    return normalized


def selected_target_key(target: dict[str, Any] | None) -> str | None:
    if not isinstance(target, dict) or not target:
        return None
    for key in ("targetKey", "objectKey", "candidateKey", "key"):
        value = target.get(key)
        if value:
            return str(value)
    if target.get("hash") is not None:
        return f"hash:{target.get('hash')}"
    if target.get("id") is not None:
        return f"id:{target.get('id')}"
    return None


def compact_target(target: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(target, dict) or not target:
        return None
    keys = (
        "targetKey",
        "objectKey",
        "candidateKey",
        "key",
        "classId",
        "name",
        "targetName",
        "id",
        "hash",
        "worldX",
        "worldY",
        "plane",
        "sceneX",
        "sceneY",
        "distanceTiles",
        "directReachability",
        "targetLiveState",
        "aimPoint",
    )
    return {key: target.get(key) for key in keys if target.get(key) is not None}


def phase_from_brain_decision(decision: dict[str, Any]) -> TaskPhase:
    phase = str(decision.get("phase") or "").lower()
    if phase == "goal_complete":
        return TaskPhase.GOAL_COMPLETE
    if phase == "inventory_full":
        return TaskPhase.INVENTORY_FULL
    if phase == "service_available":
        return TaskPhase.SERVICE_AVAILABLE
    if phase == "service_open":
        return TaskPhase.SERVICE_OPEN
    if phase == "service_complete":
        return TaskPhase.SERVICE_COMPLETE
    if phase == "return_to_resource":
        return TaskPhase.RETURN_TO_RESOURCE
    if phase == "blocked_or_unreachable":
        return TaskPhase.BLOCKED
    if phase in {"no_context", "stale_context", "no_target_observed", "missing_capability"}:
        return TaskPhase.NEEDS_MORE_CONTEXT
    if phase == "inventory_changed":
        best = decision.get("currentContextSummary", {}).get("bestTarget") if isinstance(decision.get("currentContextSummary"), dict) else None
        return TaskPhase.TARGET_SELECTED if selected_target_key(best) else TaskPhase.OBSERVE
    if phase in {"target_depleted", "waiting_for_respawn", "monitoring_progress", "likely_busy"}:
        return TaskPhase.WAIT_FOR_RESULT
    if phase == "target_available":
        best = decision.get("currentContextSummary", {}).get("bestTarget") if isinstance(decision.get("currentContextSummary"), dict) else None
        return TaskPhase.TARGET_SELECTED if selected_target_key(best) else TaskPhase.SELECT_TARGET
    if phase in {"setup_observing", "observe"}:
        return TaskPhase.OBSERVE
    return TaskPhase.UNKNOWN


def active_intent_for_phase(phase: TaskPhase) -> TaskIntent:
    return {
        TaskPhase.OBSERVE: TaskIntent.OBSERVE,
        TaskPhase.SELECT_TARGET: TaskIntent.SELECT_TARGET,
        TaskPhase.TARGET_SELECTED: TaskIntent.CONTINUE_CURRENT_TARGET,
        TaskPhase.WAIT_FOR_RESULT: TaskIntent.WAIT_FOR_RESULT,
        TaskPhase.INVENTORY_FULL: TaskIntent.NEEDS_SERVICE,
        TaskPhase.NAVIGATE_TO_SERVICE: TaskIntent.NAVIGATE_TO_SERVICE,
        TaskPhase.SERVICE_AVAILABLE: TaskIntent.SERVICE_AVAILABLE,
        TaskPhase.SERVICE_OPEN: TaskIntent.SERVICE_OPEN,
        TaskPhase.SERVICE_INTERACTION_PENDING: TaskIntent.SERVICE_INTERACTION_PENDING,
        TaskPhase.SERVICE_COMPLETE: TaskIntent.RESUME_RESOURCE_COLLECTION,
        TaskPhase.RETURN_TO_RESOURCE: TaskIntent.RESUME_RESOURCE_COLLECTION,
        TaskPhase.GOAL_COMPLETE: TaskIntent.NONE,
        TaskPhase.BLOCKED: TaskIntent.OBSERVE,
        TaskPhase.NEEDS_MORE_CONTEXT: TaskIntent.OBSERVE,
        TaskPhase.UNKNOWN: TaskIntent.OBSERVE,
    }.get(phase, TaskIntent.OBSERVE)


def phase_has_active_target(phase: TaskPhase) -> bool:
    return phase in {TaskPhase.SELECT_TARGET, TaskPhase.TARGET_SELECTED, TaskPhase.WAIT_FOR_RESULT}


def default_required_capabilities(phase: TaskPhase, policy: task_policy_module.TaskPolicy | None = None) -> list[str]:
    if phase in {TaskPhase.SELECT_TARGET, TaskPhase.TARGET_SELECTED, TaskPhase.WAIT_FOR_RESULT, TaskPhase.BLOCKED, TaskPhase.NEEDS_MORE_CONTEXT}:
        return [
            "inventory.items",
            "target.candidates",
            "target.best",
            "target.intent",
            "navigation.local_collision_window",
        ]
    if phase in {TaskPhase.INVENTORY_FULL, TaskPhase.GOAL_COMPLETE}:
        required = ["inventory.items"]
        if policy:
            required.extend(policy.requiredCapabilities)
        return capabilities.normalize_capability_names(required)
    if phase in {
        TaskPhase.SERVICE_AVAILABLE,
        TaskPhase.SERVICE_OPEN,
        TaskPhase.SERVICE_INTERACTION_PENDING,
        TaskPhase.SERVICE_COMPLETE,
        TaskPhase.RETURN_TO_RESOURCE,
    }:
        return capabilities.normalize_capability_names(["inventory.items", "bank_ui.telemetry"])
    return []


def reason_from_decision(decision: dict[str, Any], phase: TaskPhase) -> str:
    blocking = decision.get("blockingConditions") if isinstance(decision.get("blockingConditions"), list) else []
    if blocking:
        return str(blocking[0])
    source_phase = str(decision.get("phase") or "unknown")
    if phase == TaskPhase.TARGET_SELECTED:
        return "task-specific phase target_available mapped to selected target intent"
    return f"task-specific phase {source_phase} mapped to generic phase {phase.value}"


def inventory_full_state_for_policy(
    phase: TaskPhase,
    policy: task_policy_module.TaskPolicy,
    available: dict[str, Any] | None,
) -> tuple[TaskPhase, TaskIntent, dict[str, Any] | None, bool]:
    if phase != TaskPhase.INVENTORY_FULL:
        return phase, active_intent_for_phase(phase), available if phase_has_active_target(phase) else None, True
    strategy = policy.fullInventoryStrategy
    if strategy == task_policy_module.InventoryFullStrategy.NEEDS_SERVICE:
        return TaskPhase.INVENTORY_FULL, TaskIntent.NEEDS_SERVICE, None, True
    if strategy == task_policy_module.InventoryFullStrategy.PROCESS_INVENTORY:
        return TaskPhase.INVENTORY_FULL, TaskIntent.PROCESS_INVENTORY, None, True
    if strategy == task_policy_module.InventoryFullStrategy.CONTINUE_TASK:
        if available:
            return TaskPhase.TARGET_SELECTED, TaskIntent.CONTINUE_TASK, available, False
        return TaskPhase.OBSERVE, TaskIntent.CONTINUE_TASK, None, False
    if strategy == task_policy_module.InventoryFullStrategy.OBSERVE_ONLY:
        return TaskPhase.OBSERVE, TaskIntent.OBSERVE, None, False
    if strategy == task_policy_module.InventoryFullStrategy.STOP:
        return TaskPhase.BLOCKED, TaskIntent.NONE, None, True
    return TaskPhase.INVENTORY_FULL, TaskIntent.OBSERVE, None, True


def filter_inventory_full_blocking(blocking: list[str], *, inventory_full_blocks: bool) -> list[str]:
    if inventory_full_blocks:
        return blocking
    return [item for item in blocking if "inventory" not in item.lower() or "full" not in item.lower()]


def from_brain_decision(
    decision: dict[str, Any],
    previous_phase: str | None = None,
    policy: task_policy_module.TaskPolicy | dict[str, Any] | str | None = None,
) -> TaskStateResult:
    decision = decision if isinstance(decision, dict) else {}
    resolved_policy = task_policy_module.resolve_task_policy(
        policy or decision.get("taskPolicy"),
        task=decision.get("task"),
        profile=decision.get("profile"),
    )
    source_phase = phase_from_brain_decision(decision)
    best = decision.get("currentContextSummary", {}).get("bestTarget") if isinstance(decision.get("currentContextSummary"), dict) else None
    available = compact_target(best)
    phase, active_intent, active_target, inventory_full_blocks = inventory_full_state_for_policy(source_phase, resolved_policy, available)
    if source_phase != TaskPhase.INVENTORY_FULL and active_target is None:
        active_target = available if phase_has_active_target(phase) else None
    previous_target = compact_target(decision.get("previousIntentTarget")) or (available if source_phase in {TaskPhase.INVENTORY_FULL, TaskPhase.GOAL_COMPLETE} else None)
    blocking = [str(item) for item in decision.get("blockingConditions") or [] if item]
    blocking = filter_inventory_full_blocking(blocking, inventory_full_blocks=inventory_full_blocks)
    task_policy_payload = resolved_policy.to_dict()
    state = TaskState(
        task=str(decision.get("task") or "unknown"),
        phase=phase,
        previousPhase=previous_phase,
        confidence=decision.get("confidence") if isinstance(decision.get("confidence"), (int, float)) else None,
        reason=reason_from_decision(decision, phase),
        activeIntent=active_intent,
        selectedTargetKey=selected_target_key(active_target),
        activeIntentTarget=active_target,
        availableTarget=available,
        previousIntentTarget=previous_target,
        requiredCapabilities=default_required_capabilities(phase, resolved_policy),
        missingCapabilities=decision.get("missingCapabilities") if isinstance(decision.get("missingCapabilities"), list) else [],
        blockingConditions=blocking,
        observationNeeds=decision.get("observationNeeds") if isinstance(decision.get("observationNeeds"), list) else [],
        goalProgress=decision.get("goalProgress") if isinstance(decision.get("goalProgress"), dict) else {},
        taskPolicy=task_policy_payload,
        inventoryExpectation=task_policy_payload.get("inventoryExpectation"),
        fullInventoryStrategy=task_policy_payload.get("fullInventoryStrategy"),
        resourceDisposition=task_policy_payload.get("resourceDisposition"),
        serviceTypeNeeded=task_policy_payload.get("serviceTypeNeeded") if active_intent == TaskIntent.NEEDS_SERVICE else None,
        processTypeNeeded=task_policy_payload.get("processTypeNeeded") if active_intent == TaskIntent.PROCESS_INVENTORY else None,
        noActionEmitted=True,
    )
    transition = TaskTransition(task=state.task, previousPhase=previous_phase, nextPhase=phase, reason=state.reason, confidence=state.confidence)
    return TaskStateResult(state=state, transition=transition)

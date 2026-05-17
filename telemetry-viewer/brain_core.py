from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import resource_progress as rp
import capabilities
import task_state
import task_policy as task_policy_module
from analyzers import inventory_analyzer


BASE_DIR = Path(__file__).resolve().parent
DECISION_SCHEMA = "brain_decision.v1"
STATE_SCHEMA = "brain_state.v1"
CONTEXT_SCHEMA = "context_request.v1"
WATCH_REQUEST_SCHEMA = "context_watch_request.v1"
TASK_RESOURCES_SCHEMA = "task_resources.v1"
PROGRESS_REPAIR_WARNING = "invalid progress state corrected: unchanged baseline snapshot cannot imply gained progress"
RESET_PROGRESS_REPAIR_WARNING = "invalid progress state corrected after reset"
OLD_PROGRESS_HISTORY_WARNING = rp.OLD_CUMULATIVE_HISTORY_WARNING
PARTIAL_PROGRESS_REPAIR_WARNING = "invalid partial progress state repaired: previous inventory snapshot missing"
TASK_RESOURCES_PATH = BASE_DIR / "task_resources.json"
LIVE_STATES_AVAILABLE = {"live", "live_assumed"}
LIVE_STATES_UNAVAILABLE = {"depleted_or_stump", "recently_despawned", "stale"}
BUSY_WOODCUTTING_STATES = {"likely_chopping", "woodcutting_possible", "chopping"}
UNKNOWN_ACTIVITY_VALUES = {"", "unknown", "none", "null", "n/a", "na", "-1", "0"}
DEFAULT_NEEDS = [
    "baseline",
    "inventory",
    "activity",
    "liveness",
    "navigation_readiness",
    "best:tree",
    "nearest:tree",
    "reachability:tree",
    "events",
    "diagnostics",
    "watches",
]
OPTIONAL_CAPABILITIES = {
    "activity.animation_frame",
    "activity.explicit_movement_state",
    "navigation.full_pathfinding",
    "collisionGridPathing",
    "inventory.deltas",
    "plugin_snapshot.watch_values",
}
DEFAULT_TASK_RESOURCES = {
    "schema": TASK_RESOURCES_SCHEMA,
    "tasks": {
        "woodcutting": {
            "defaultResourceGroup": "woodcutting_logs",
            "resources": {
                "normal_logs": {"displayName": "Logs", "itemIds": [1511]},
                "oak_logs": {"displayName": "Oak logs", "itemIds": [1521]},
                "willow_logs": {"displayName": "Willow logs", "itemIds": [1519]},
                "maple_logs": {"displayName": "Maple logs", "itemIds": [1517]},
                "yew_logs": {"displayName": "Yew logs", "itemIds": [1515]},
                "magic_logs": {"displayName": "Magic logs", "itemIds": [1513]},
            },
            "resourceGroups": {
                "woodcutting_logs": {
                    "displayName": "Woodcutting logs",
                    "itemIds": [1511, 1521, 1519, 1517, 1515, 1513],
                    "resources": ["normal_logs", "oak_logs", "willow_logs", "maple_logs", "yew_logs", "magic_logs"],
                }
            },
        }
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_get(value: Any, path: str | list[str], default: Any = None) -> Any:
    parts = path.split(".") if isinstance(path, str) else path
    current = value
    for part in parts:
        if not isinstance(current, dict):
            return default
        current = current.get(part)
        if current is None:
            return default
    return current


def as_bool(value: Any) -> bool | None:
    if value is True or value is False:
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def text(value: Any, default: str = "unknown") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def canonical_capability(value: Any) -> str:
    return capabilities.normalize_capability_name(value)


def load_task_resources() -> dict:
    try:
        value = json.loads(TASK_RESOURCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_TASK_RESOURCES
    if not isinstance(value, dict) or value.get("schema") != TASK_RESOURCES_SCHEMA:
        return DEFAULT_TASK_RESOURCES
    return value


def task_resource_group(task: str, group_id: str | None = None, resources: dict | None = None) -> dict:
    resources = resources if isinstance(resources, dict) else load_task_resources()
    tasks = resources.get("tasks") if isinstance(resources.get("tasks"), dict) else {}
    task_def = tasks.get(task) if isinstance(tasks.get(task), dict) else {}
    group_id = group_id or task_def.get("defaultResourceGroup") or "woodcutting_logs"
    groups = task_def.get("resourceGroups") if isinstance(task_def.get("resourceGroups"), dict) else {}
    group = groups.get(group_id) if isinstance(groups.get(group_id), dict) else {}
    if group:
        return {"id": group_id, **group}
    if task == "woodcutting":
        return {"id": "woodcutting_logs", **DEFAULT_TASK_RESOURCES["tasks"]["woodcutting"]["resourceGroups"]["woodcutting_logs"]}
    return {"id": group_id, "displayName": group_id or "resources", "itemIds": []}


def primary_substate(substates: list[str]) -> str | None:
    priority = [
        "goal_count_reached",
        "resource_goal_reached",
        "recent_target_depletion_observed",
        "recent_inventory_change",
        "watchable_capability_missing",
        "interacting_unknown_not_busy",
        "activity_unknown",
        "liveness_assumed",
        "movement_unknown",
        "no_explicit_busy_evidence",
    ]
    for value in priority:
        if value in substates:
            return value
    return substates[0] if substates else None


def json_post(host: str, port: int, path: str, payload: dict, timeout: float) -> dict:
    url = f"http://{host}:{port}{path}"
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} returned non-object JSON")
    return value


def json_get(host: str, port: int, path: str, timeout: float) -> dict:
    url = f"http://{host}:{port}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} returned non-object JSON")
    return value


def build_context_request(task: str, max_candidates: int = 3, max_events: int = 10) -> dict:
    return {
        "schema": CONTEXT_SCHEMA,
        "task": task,
        "needs": list(DEFAULT_NEEDS),
        "maxCandidates": max(1, int(max_candidates)),
        "maxEvents": max(0, int(max_events)),
        "responseMode": "compact",
    }


def build_watch_request(task: str, watches: list[dict]) -> dict:
    return {
        "schema": WATCH_REQUEST_SCHEMA,
        "requestId": f"brain-core-{int(time.time() * 1000)}",
        "task": task,
        "watches": watches,
    }


def fetch_context(host: str, port: int, task: str, max_candidates: int, max_events: int, timeout: float) -> dict:
    return json_post(host, port, "/context", build_context_request(task, max_candidates, max_events), timeout)


def fetch_optional_endpoint(host: str, port: int, path: str, timeout: float) -> tuple[dict, str | None]:
    try:
        return json_get(host, port, path, timeout), None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
        return {}, str(exc)


def post_optional_watch_request(host: str, port: int, task: str, watches: list[dict], timeout: float) -> tuple[dict, str | None]:
    if not watches:
        return {}, None
    try:
        return json_post(host, port, "/watch-request", build_watch_request(task, watches), timeout), None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
        return {}, str(exc)


def is_unknown_activity_value(value: Any) -> bool:
    if value is None or value == -1:
        return True
    if isinstance(value, str):
        return value.strip().lower() in UNKNOWN_ACTIVITY_VALUES
    return False


def explicit_interacting_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("type", "name", "id", "index", "targetType", "targetName"):
            nested = explicit_interacting_value(value.get(key))
            if nested is not None:
                return nested
        return None
    if is_unknown_activity_value(value):
        return None
    return value if value else None


def active_animation_value(value: Any) -> Any:
    if value in (None, "", -1, "-1", 0, "0") or is_unknown_activity_value(value):
        return None
    return value


def best_target(response: dict, class_id: str = "tree") -> dict:
    candidate = safe_get(response, ["bestCandidates", class_id])
    if not isinstance(candidate, dict):
        candidate = safe_get(response, ["taskSummary", "bestTree"])
    return candidate if isinstance(candidate, dict) else {}


def nearest_target(response: dict, class_id: str = "tree") -> dict:
    candidate = safe_get(response, ["nearestCandidates", class_id])
    if not isinstance(candidate, dict):
        candidate = safe_get(response, ["taskSummary", "nearestTree"])
    return candidate if isinstance(candidate, dict) else {}


def candidate_navigation(candidate: dict) -> dict:
    navigation = candidate.get("navigation")
    return navigation if isinstance(navigation, dict) else {}


def candidate_reachability(candidate: dict) -> str:
    navigation = candidate_navigation(candidate)
    return str(navigation.get("directReachability") or candidate.get("directReachability") or "unknown")


def candidate_live_state(candidate: dict) -> str:
    return str(candidate.get("targetLiveState") or "unknown")


def candidate_has_aim(candidate: dict) -> bool:
    aim = candidate.get("aimPoint")
    return isinstance(aim, dict) and (aim.get("canvasX") is not None or aim.get("x") is not None) and (aim.get("canvasY") is not None or aim.get("y") is not None)


def candidate_name(candidate: dict) -> str:
    return text(candidate.get("targetName") or candidate.get("name"), "target")


def candidate_key(candidate: dict) -> str | None:
    if not candidate:
        return None
    for key in ("objectKey", "candidateKey", "hash"):
        if candidate.get(key) is not None:
            return f"{key}:{candidate.get(key)}"
    parts = [candidate.get("id"), candidate.get("worldX"), candidate.get("worldY"), candidate.get("plane"), candidate.get("classId")]
    if any(part is not None for part in parts):
        return ":".join(text(part, "") for part in parts)
    return None


def compact_candidate(candidate: dict) -> dict:
    if not candidate:
        return {}
    navigation = candidate_navigation(candidate)
    return {
        "key": candidate_key(candidate),
        "classId": candidate.get("classId"),
        "name": candidate.get("targetName") or candidate.get("name"),
        "id": candidate.get("id"),
        "worldX": candidate.get("worldX"),
        "worldY": candidate.get("worldY"),
        "plane": candidate.get("plane"),
        "sceneX": candidate.get("sceneX"),
        "sceneY": candidate.get("sceneY"),
        "distanceTiles": candidate.get("distanceTiles"),
        "onScreen": candidate.get("onScreen"),
        "geometryAvailable": candidate.get("geometryAvailable"),
        "aimPoint": candidate.get("aimPoint"),
        "targetLiveState": candidate.get("targetLiveState"),
        "livenessInterpretation": candidate.get("livenessInterpretation"),
        "directReachability": navigation.get("directReachability") or candidate.get("directReachability"),
        "pathLengthTiles": navigation.get("pathLengthTiles"),
        "targetInCollisionWindow": navigation.get("targetInCollisionWindow"),
        "reachabilityConfidence": navigation.get("reachabilityConfidence"),
    }


def inventory_summary(response: dict) -> dict:
    inventory = response.get("inventory")
    if not isinstance(inventory, dict):
        inventory = safe_get(response, "taskSummary.inventoryState")
    inventory = inventory if isinstance(inventory, dict) else {}
    items_list_present = isinstance(inventory.get("items"), list)
    items_known = (
        items_list_present
        and inventory.get("itemsKnown") is not False
        and inventory.get("itemListAvailable") is not False
    )
    items = inventory.get("items") if items_known else []
    resource_counts = inventory.get("resourceCounts") if isinstance(inventory.get("resourceCounts"), dict) else {}
    return {
        "known": inventory.get("known"),
        "freeSlots": inventory.get("freeSlots"),
        "filledSlots": inventory.get("filledSlots"),
        "inventoryFull": inventory.get("inventoryFull"),
        "changedRecently": inventory.get("changedRecently"),
        "totalItemQuantity": inventory.get("totalItemQuantity", inventory.get("itemCount")),
        "inventorySignature": inventory.get("inventorySignature") or inventory.get("signature"),
        "items": [item for item in items if isinstance(item, dict)],
        "itemsKnown": items_known,
        "itemListAvailable": items_known,
        "resourceCounts": resource_counts,
        "slotDiagnostics": inventory.get("slotDiagnostics") if isinstance(inventory.get("slotDiagnostics"), dict) else {},
    }


def activity_summary(response: dict) -> dict:
    activity = response.get("activity")
    if not isinstance(activity, dict):
        activity = safe_get(response, "taskSummary.activityState")
    woodcutting = response.get("woodcuttingState")
    if not isinstance(woodcutting, dict):
        woodcutting = safe_get(response, "taskSummary.woodcuttingState")
    activity = activity if isinstance(activity, dict) else {}
    woodcutting = woodcutting if isinstance(woodcutting, dict) else {}
    evidence = activity.get("evidence") if isinstance(activity.get("evidence"), list) else []
    return {
        "apparentState": activity.get("apparentState"),
        "woodcuttingState": woodcutting.get("woodcuttingState"),
        "animation": activity.get("animation"),
        "interacting": activity.get("interacting"),
        "isMoving": activity.get("isMoving"),
        "confidence": activity.get("confidence", woodcutting.get("confidence")),
        "evidence": evidence[:8],
    }


def recent_events(response: dict, limit: int) -> list[dict]:
    events = response.get("recentEvents")
    if not isinstance(events, list):
        events = response.get("events")
    if not isinstance(events, list):
        events = safe_get(response, "taskSummary.recentEvents")
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)][-max(0, limit):]


def event_key(event: dict) -> str:
    return f"{event.get('tick')}:{event.get('eventType')}:{event.get('summary')}"


def event_mentions(events: list[dict], *tokens: str) -> bool:
    lowered_tokens = tuple(token.lower() for token in tokens)
    for event in events:
        joined = " ".join(str(event.get(key) or "") for key in ("eventType", "summary", "severity")).lower()
        if any(token in joined for token in lowered_tokens):
            return True
    return False


def freshness_failed(response: dict) -> bool:
    freshness = response.get("freshness")
    if not isinstance(freshness, dict):
        freshness = safe_get(response, "taskSummary.freshness")
    if not isinstance(freshness, dict):
        return False
    return freshness.get("freshByTicks") is False or freshness.get("freshByMillis") is False


def freshness_status_from_value(value: Any) -> str | None:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"fresh", "stale", "unknown", "missing", "unavailable"}:
            return lowered
    if not isinstance(value, dict):
        return None
    status = value.get("status") or value.get("freshness")
    if isinstance(status, str):
        lowered = status.strip().lower()
        if lowered in {"fresh", "stale", "unknown", "missing", "unavailable"}:
            return lowered
    if value.get("freshByTicks") is False or value.get("freshByMillis") is False:
        return "stale"
    if value.get("fresh") is False:
        return "stale"
    if value.get("freshByTicks") is True or value.get("freshByMillis") is True or value.get("fresh") is True:
        return "fresh"
    return None


def domain_freshness_value(response: dict, *names: str) -> str | None:
    containers = [
        response.get("freshnessDomains"),
        response.get("domainFreshness"),
        safe_get(response, "taskSummary.freshnessDomains"),
        safe_get(response, "taskSummary.domainFreshness"),
    ]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for name in names:
            value = container.get(name)
            status = freshness_status_from_value(value)
            if status:
                return status
    return None


def inventory_snapshot_usable(inventory: dict) -> bool:
    if not isinstance(inventory, dict) or not inventory:
        return False
    known = as_bool(inventory.get("known"))
    if known is False:
        return False
    if isinstance(inventory.get("items"), list):
        return True
    if isinstance(inventory.get("resourceCounts"), dict):
        return True
    return any(key in inventory for key in ("inventoryFull", "freeSlots", "filledSlots", "inventorySignature", "signature"))


def candidate_count(response: dict, class_id: str = "tree") -> int | None:
    summary = safe_get(response, ["reachabilitySummary", class_id], {})
    count = as_int(summary.get("candidateCount")) if isinstance(summary, dict) else None
    if count is not None:
        return count
    candidates = response.get("candidates")
    if isinstance(candidates, list):
        return len(candidates)
    return None


def build_freshness_domains(
    response: dict,
    inventory: dict,
    *,
    stale: bool,
    best: dict,
    nearest: dict,
    policy: task_policy_module.TaskPolicy,
) -> dict[str, str]:
    inventory_status = domain_freshness_value(response, "inventory", "inventoryFreshness")
    if inventory_status is None:
        inventory_status = freshness_status_from_value(inventory.get("freshness") if isinstance(inventory, dict) else None)
    if inventory_status is None:
        inventory_status = "fresh" if inventory_snapshot_usable(inventory) else "unknown"

    target_status = domain_freshness_value(
        response,
        "target",
        "targets",
        "target.candidates",
        "targetCandidateFreshness",
    )
    if target_status is None:
        count = candidate_count(response)
        if stale:
            target_status = "stale"
        elif best or nearest or (count is not None and count > 0):
            target_status = "fresh"
        else:
            target_status = "unknown"

    service_status = domain_freshness_value(response, "service", "serviceFreshness")
    if service_status is None:
        service_status = target_status if policy.fullInventoryStrategy == task_policy_module.InventoryFullStrategy.NEEDS_SERVICE else "not_required"

    navigation_status = domain_freshness_value(response, "navigation", "navigationFreshness")
    if navigation_status is None:
        navigation = response.get("navigationReadiness") if isinstance(response.get("navigationReadiness"), dict) else {}
        navigation_status = "fresh" if navigation.get("collisionWindowAvailable") is True or navigation.get("collisionKnown") is True else "unknown"

    process_status = domain_freshness_value(response, "processInventory", "processInventoryFreshness")
    if process_status is None:
        process_status = inventory_status if policy.fullInventoryStrategy == task_policy_module.InventoryFullStrategy.PROCESS_INVENTORY else "not_required"

    return {
        "targetCandidateFreshness": target_status,
        "inventoryFreshness": inventory_status,
        "serviceFreshness": service_status,
        "navigationFreshness": navigation_status,
        "processInventoryFreshness": process_status,
    }


def inventory_policy_can_proceed_with_stale_targets(
    *,
    policy: task_policy_module.TaskPolicy,
    full_inventory: bool,
    freshness_domains: dict[str, str],
) -> bool:
    if not full_inventory:
        return False
    if freshness_domains.get("inventoryFreshness") != "fresh":
        return False
    return policy.fullInventoryStrategy in {
        task_policy_module.InventoryFullStrategy.NEEDS_SERVICE,
        task_policy_module.InventoryFullStrategy.PROCESS_INVENTORY,
    }


def dedupe_domains(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text_value = str(value or "").strip()
        if not text_value or text_value in seen:
            continue
        seen.add(text_value)
        result.append(text_value)
    return result


def context_domain_summary(
    decision: dict[str, Any],
    *,
    response: dict[str, Any] | None = None,
    policy: task_policy_module.TaskPolicy | dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    decision = decision if isinstance(decision, dict) else {}
    response = response if isinstance(response, dict) else {}
    generic = decision.get("genericTaskState") if isinstance(decision.get("genericTaskState"), dict) else {}
    resolved_policy = task_policy_module.resolve_task_policy(
        policy or decision.get("taskPolicy"),
        task=decision.get("task"),
        profile=decision.get("profile"),
    )
    phase = str(generic.get("phase") or decision.get("phase") or "")
    active_intent = str(generic.get("activeIntent") or "")
    required: list[str] = []
    optional: list[str] = []

    if active_intent == "process_inventory":
        required = ["inventory", "process_inventory"]
        optional = ["target.candidates", "target.freshness"]
    elif active_intent == "needs_service":
        required = ["inventory", "service"]
        optional = ["target.candidates", "service.target"]
    elif active_intent in {"select_target", "continue_current_target"} or phase in {"select_target", "target_selected"}:
        required = ["target.candidates", "target.freshness"]
        optional = ["navigation.full_pathfinding"]
    elif active_intent == "continue_task":
        required = ["target.candidates"]
        optional = ["inventory", "target.freshness"]
    elif phase in {"blocked", "needs_more_context"} and resolved_policy.fullInventoryStrategy not in {
        task_policy_module.InventoryFullStrategy.PROCESS_INVENTORY,
        task_policy_module.InventoryFullStrategy.NEEDS_SERVICE,
    }:
        required = ["target.candidates"]
        optional = ["target.freshness"]
    elif phase == "goal_complete":
        required = ["inventory"]
        optional = ["target.candidates"]
    elif phase == "observe":
        optional = ["inventory", "target.candidates"]

    required = dedupe_domains(required)
    optional = dedupe_domains(optional)

    freshness_domains = decision.get("freshnessDomains") if isinstance(decision.get("freshnessDomains"), dict) else {}
    current_context = decision.get("currentContextSummary") if isinstance(decision.get("currentContextSummary"), dict) else {}
    inventory = response.get("inventory") if isinstance(response.get("inventory"), dict) else current_context.get("inventory") if isinstance(current_context.get("inventory"), dict) else {}
    best = best_target(response) if response else current_context.get("bestTarget") if isinstance(current_context.get("bestTarget"), dict) else {}
    target_count = candidate_count(response) if response else None
    if target_count is None and best:
        target_count = 1

    inventory_missing = not inventory_snapshot_usable(inventory) or freshness_domains.get("inventoryFreshness") not in {None, "fresh"}
    target_candidates_missing = not best and (target_count is None or target_count <= 0)
    target_freshness_missing = freshness_domains.get("targetCandidateFreshness") not in {None, "fresh"}

    missing_required: list[str] = []
    optional_missing: list[str] = []
    if "inventory" in required and inventory_missing:
        missing_required.append("inventory")
    if "target.candidates" in required and target_candidates_missing:
        missing_required.append("target.candidates")
    if "target.freshness" in required and target_freshness_missing:
        missing_required.append("target.freshness")

    process_context = decision.get("processInventoryContext") if isinstance(decision.get("processInventoryContext"), dict) else {}
    if "process_inventory" in required and process_context and process_context.get("processRequired") is False:
        missing_required.append("process_inventory")
    service_context = decision.get("serviceContext") if isinstance(decision.get("serviceContext"), dict) else {}
    if "service" in required and service_context and service_context.get("serviceNeeded") is False:
        missing_required.append("service")

    if "inventory" in optional and inventory_missing:
        optional_missing.append("inventory")
    if "target.candidates" in optional and target_candidates_missing:
        optional_missing.append("target.candidates")
    if "target.freshness" in optional and target_freshness_missing:
        optional_missing.append("target.freshness")
    if "service.target" in optional and service_context and not service_context.get("bestServiceCandidate"):
        optional_missing.append("service.target")

    return {
        "requiredContextDomains": required,
        "missingRequiredContextDomains": dedupe_domains(missing_required),
        "optionalMissingContextDomains": dedupe_domains(optional_missing),
        "targetCandidatesRequired": "target.candidates" in required,
    }


def inventory_is_full(inventory: dict) -> bool:
    full = as_bool(inventory.get("inventoryFull"))
    if full is not None:
        return full
    free_slots = as_number(inventory.get("freeSlots"))
    return free_slots == 0 if free_slots is not None else False


def activity_busy_analysis(activity: dict) -> tuple[bool, list[str], list[str]]:
    apparent = str(activity.get("apparentState") or "").lower()
    woodcutting = str(activity.get("woodcuttingState") or "").lower()
    animation = activity.get("animation")
    interacting = activity.get("interacting")
    evidence = [str(item) for item in activity.get("evidence") or []]
    positive: list[str] = []
    substates: list[str] = []

    if explicit_interacting_value(interacting) is not None:
        positive.append("explicit interacting target present")
    elif any("explicit interacting target present" in item.lower() for item in evidence):
        positive.append("explicit interacting target present")
    elif any(item.lower().startswith("interacting=") and "unknown" not in item.lower() for item in evidence):
        positive.append("explicit interacting target present")
    elif apparent == "interacting":
        substates.append("activity_unknown")
        if any("unknown" in item.lower() for item in evidence):
            substates.append("interacting_unknown_not_busy")

    if active_animation_value(animation) is not None:
        positive.append("active animation present")
    elif any("active animation present" in item.lower() for item in evidence):
        positive.append("active animation present")
    elif animation is None:
        substates.append("activity_unknown")
    elif str(animation) in {"-1", "0"}:
        substates.append("no_explicit_busy_evidence")

    if woodcutting in BUSY_WOODCUTTING_STATES:
        positive.append(f"woodcutting state {woodcutting}")
    if apparent == "moving" or activity.get("isMoving") is True:
        substates.append("movement_observed")
    elif activity.get("isMoving") is None:
        substates.append("movement_unknown")
    if not positive:
        substates.append("no_explicit_busy_evidence")
    return bool(positive), positive, dedupe_strings(substates)


def target_is_available(candidate: dict) -> bool:
    if not candidate:
        return False
    reachability = candidate_reachability(candidate)
    distance = as_number(candidate.get("distanceTiles"))
    unknown_but_near = reachability == "unknown" and distance is not None and distance <= 2
    return (
        candidate.get("onScreen") is True
        and candidate.get("geometryAvailable") is True
        and candidate_has_aim(candidate)
        and candidate_live_state(candidate) in LIVE_STATES_AVAILABLE
        and (reachability == "reachable" or unknown_but_near)
    )


def target_is_unreachable(best: dict, nearest: dict, response: dict) -> bool:
    candidates = [candidate for candidate in (best, nearest) if candidate]
    reachability = safe_get(response, ["reachabilitySummary", "tree"], {})
    reachable_count = as_number(reachability.get("reachableCount")) if isinstance(reachability, dict) else None
    if any(candidate_reachability(candidate) == "reachable" for candidate in candidates) or (reachable_count is not None and reachable_count > 0):
        return False
    if candidates and all(candidate_reachability(candidate) == "blocked" for candidate in candidates):
        return True
    for candidate in candidates:
        if candidate_navigation(candidate).get("targetInCollisionWindow") is False and reachable_count in (None, 0):
            return True
    return False


def current_target_depleted(candidate: dict) -> bool:
    return candidate_live_state(candidate) in LIVE_STATES_UNAVAILABLE


def missing_capabilities(response: dict) -> list[str]:
    values: list[str] = []
    items = response.get("missingCapabilities")
    if isinstance(items, list):
        values.extend(canonical_capability(item) for item in items if item)
    task = response.get("taskSummary")
    if isinstance(task, dict) and isinstance(task.get("missingCapabilities"), list):
        values.extend(canonical_capability(item) for item in task["missingCapabilities"] if item)
    return sorted(set(values))


def suggested_watch_requests(response: dict) -> list[dict]:
    suggestions = response.get("suggestedWatchRequests")
    if not isinstance(suggestions, list):
        return []
    return [item for item in suggestions if isinstance(item, dict) and item.get("alias")]


def response_warnings(response: dict) -> list[str]:
    items = response.get("warnings")
    return [str(item) for item in items if item] if isinstance(items, list) else []


def capability_runtime_map(capabilities: dict) -> dict[str, dict]:
    items = capabilities.get("capabilities") if isinstance(capabilities, dict) else []
    if not isinstance(items, list):
        return {}
    return {str(item.get("id")): item for item in items if isinstance(item, dict) and item.get("id")}


def capability_observation_needs(capabilities: dict) -> list[dict]:
    needs: list[dict] = []
    for item in capability_runtime_map(capabilities).values():
        runtime = item.get("runtimeStatus")
        if runtime in {"missing", "stale", "unsupported"}:
            needs.append(
                {
                    "capability": item.get("id"),
                    "status": runtime,
                    "watchable": bool(item.get("watchable") or item.get("status") == "watchable"),
                    "reason": item.get("missingReason"),
                }
            )
    return needs


def inventory_item_id(item: dict) -> int | None:
    for key in ("itemId", "itemID", "id"):
        item_id = as_int(item.get(key))
        if item_id not in (None, -1, 0):
            return item_id
    return None


def inventory_item_quantity(item: dict) -> int:
    for key in ("quantity", "qty", "count"):
        quantity = as_int(item.get(key))
        if quantity is not None and quantity > 0:
            return quantity
    return 1


def inventory_item_slot(item: dict) -> int | None:
    return as_int(item.get("slot"))


def inventory_slot_diagnostics(inventory: dict) -> dict:
    if not isinstance(inventory, dict):
        return {"known": False, "warnings": ["inventory state missing"]}
    slot_count = as_int(inventory.get("inventorySlotCount"))
    if slot_count is None:
        slot_count = as_int(inventory.get("slotCount"))
    filled_slots = as_int(inventory.get("filledSlots"))
    free_slots = as_int(inventory.get("freeSlots"))
    items = inventory.get("items") if isinstance(inventory.get("items"), list) else []
    seen: dict[int, dict] = {}
    duplicate_slots: list[int] = []
    invalid_slots: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        slot = inventory_item_slot(item)
        item_id = inventory_item_id(item)
        if slot is None:
            invalid_slots.append({"slot": item.get("slot"), "itemId": item_id, "reason": "missing or non-integer slot"})
            continue
        if slot_count is not None and (slot < 0 or slot >= slot_count):
            invalid_slots.append({"slot": slot, "itemId": item_id, "reason": f"slot outside 0..{slot_count - 1}"})
            continue
        if slot in seen:
            duplicate_slots.append(slot)
        seen[slot] = item
    empty_or_missing = []
    if slot_count is not None and 0 <= slot_count <= 128:
        empty_or_missing = [slot for slot in range(slot_count) if slot not in seen]
    warnings: list[str] = []
    if invalid_slots:
        warnings.append("inventory contains invalid slot indexes")
    if duplicate_slots:
        warnings.append("inventory contains duplicate filled slot entries")
    if slot_count is not None and filled_slots is not None and free_slots is not None and filled_slots + free_slots != slot_count:
        warnings.append("inventory filledSlots + freeSlots does not equal inventorySlotCount")
    if filled_slots is not None and filled_slots != len([item for item in items if isinstance(item, dict) and inventory_item_id(item) is not None]):
        warnings.append("inventory filledSlots does not match inventory.items filled entries")
    return {
        "known": True,
        "inventorySlotCount": slot_count,
        "filledItemSlots": sorted(seen),
        "emptyOrMissingSlots": empty_or_missing,
        "duplicateSlots": sorted(set(duplicate_slots)),
        "invalidSlots": invalid_slots,
        "consistent": not warnings,
        "warnings": warnings,
    }


def count_inventory_items(inventory: dict, item_ids: list[int] | set[int]) -> dict:
    target_ids = {int(item_id) for item_id in item_ids if as_int(item_id) is not None}
    if not isinstance(inventory, dict):
        return {"known": False, "count": None, "matchedItems": [], "missingReason": "inventory state missing"}
    raw_items = inventory.get("items")
    items_present = inventory_items_present_for_progress(inventory, raw_items)
    resource_counts = inventory.get("resourceCounts") if isinstance(inventory.get("resourceCounts"), dict) else {}
    definition = rp.ResourceDefinition(id="resource", item_ids=tuple(sorted(target_ids)), display_name="resource")
    snapshot = rp.InventorySnapshot(
        session_path=None,
        latest_tick=None,
        inventory_signature=inventory.get("inventorySignature") or inventory.get("signature"),
        inventory_slot_count=inventory_slot_count_for_progress(inventory),
        items=tuple(item for item in (raw_items or []) if isinstance(item, dict)) if items_present else None,
        resource_counts=resource_counts,
    )
    result = rp.count_resource_items(snapshot, definition)
    if not result.get("known"):
        reason = "inventory item list missing"
        if inventory.get("known") is False:
            reason = "inventory state unknown"
        result["missingReason"] = result.get("missingReason") or reason
    return result


def inventory_items_present_for_progress(inventory: dict, raw_items: object | None = None) -> bool:
    raw_items = inventory.get("items") if raw_items is None else raw_items
    if not isinstance(raw_items, list):
        return False
    if inventory.get("itemsKnown") is False or inventory.get("itemListAvailable") is False:
        return False
    if raw_items:
        return True
    filled_slots = as_int(inventory.get("filledSlots"))
    if filled_slots is not None and filled_slots > 0:
        return bool(inventory.get("itemsKnown") is True or inventory.get("itemListAvailable") is True)
    return True


def inventory_items_for_progress(inventory: dict) -> list[dict] | None:
    if not isinstance(inventory, dict) or not isinstance(inventory.get("items"), list):
        return None
    items: list[dict] = []
    for item in inventory.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = inventory_item_id(item)
        if item_id is None:
            continue
        items.append({"slot": inventory_item_slot(item), "itemId": item_id, "quantity": inventory_item_quantity(item)})
    items.sort(key=lambda item: (item.get("slot") is None, item.get("slot"), item.get("itemId")))
    return items


def inventory_signature_for_progress(inventory: dict, current_items: list[dict] | None) -> str | None:
    signature = inventory.get("inventorySignature") or inventory.get("signature")
    if signature not in (None, ""):
        return str(signature)
    if not isinstance(current_items, list):
        return None
    normalized = [
        {
            "slot": inventory_item_slot(item),
            "itemId": inventory_item_id(item),
            "quantity": inventory_item_quantity(item),
        }
        for item in current_items
        if isinstance(item, dict)
    ]
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def inventory_tick_for_progress(response: dict) -> int | None:
    for value in (
        response.get("latestTick"),
        safe_get(response, "taskSummary.latestTick"),
        safe_get(response, "stateSummary.freshness.latestTick"),
    ):
        tick = as_int(value)
        if tick is not None:
            return tick
    return None


def inventory_slot_count_for_progress(inventory: dict) -> int | None:
    for key in ("inventorySlotCount", "slotCount", "capacity"):
        value = as_int(inventory.get(key))
        if value is not None:
            return value
    return None


def progress_counter_value(state: dict, key: str, group_id: str) -> int:
    progress = state.get("resourceProgress") if isinstance(state.get("resourceProgress"), dict) else {}
    value = as_int(progress.get(key))
    if value is not None:
        return value
    state_value = as_int(state.get(key))
    if state_value is not None:
        return state_value
    if key in {"observedGained", "displayedGoalProgress"}:
        return as_int((state.get("resourceGainedCounts") or {}).get(group_id)) or 0
    if key == "observedRemoved":
        return as_int((state.get("resourceLostCounts") or {}).get(group_id)) or 0
    return 0


def clear_unvalidated_progress_history(state: dict, reason: str = OLD_PROGRESS_HISTORY_WARNING) -> dict:
    updated = dict(state)
    group_id = str(updated.get("goalResourceGroup") or "woodcutting_logs")
    progress = dict(updated.get("resourceProgress") or {})
    for key in (
        "cumulativeGained",
        "cumulativeLostOrRemoved",
        "observedGained",
        "observedRemoved",
        "progressChangeCount",
    ):
        progress[key] = None
    progress["displayedGoalProgress"] = 0
    progress["goalComplete"] = False
    progress["postBaselineGainObserved"] = False
    progress["hasValidPostBaselineProgressHistory"] = False
    progress["progressStateRepaired"] = True
    progress["repairReason"] = reason
    warnings = list(progress.get("warnings") or [])
    if reason not in warnings:
        warnings.append(reason)
    progress["warnings"] = warnings
    updated["resourceProgress"] = progress
    updated["stateVersion"] = 3
    updated["resourceGainedCounts"] = {}
    updated["resourceLostCounts"] = {}
    updated["observedGained"] = None
    updated["observedRemoved"] = None
    updated["displayedGoalProgress"] = 0
    updated["postBaselineGainObserved"] = False
    updated["hasValidPostBaselineProgressHistory"] = False
    updated["progressChangeCount"] = None
    updated["goalComplete"] = False
    updated["goalPreviouslyReached"] = False
    repair_warnings = list(updated.get("progressHistoryRepairWarnings") or [])
    if reason not in repair_warnings:
        repair_warnings.append(reason)
    updated["progressHistoryRepairWarnings"] = repair_warnings
    return updated


def estimate_progress_with_resource_tracker(state: dict, response: dict, inventory: dict, goal_count: int | None, task: str = "woodcutting") -> dict:
    resource_group = task_resource_group(task)
    group_id = str(resource_group.get("id") or "woodcutting_logs")
    item_ids = tuple(int(item_id) for item_id in resource_group.get("itemIds", []) if as_int(item_id) is not None)
    definition = rp.ResourceDefinition(group_id, item_ids, str(resource_group.get("displayName") or "Woodcutting logs"))
    raw_inventory_items = inventory.get("items")
    items_present = inventory_items_present_for_progress(inventory, raw_inventory_items)
    current_items = tuple(item for item in raw_inventory_items if isinstance(item, dict)) if items_present else None
    normalized_current_items = rp.normalize_items(raw_inventory_items) if items_present else None
    current_signature = inventory_signature_for_progress(inventory, list(normalized_current_items) if normalized_current_items is not None else None)
    if not items_present and not (inventory.get("inventorySignature") or inventory.get("signature")):
        current_signature = None
    snapshot = rp.InventorySnapshot(
        session_path=state.get("sessionPath") or response.get("sessionPath") or "in_memory",
        latest_tick=inventory_tick_for_progress(response),
        inventory_signature=current_signature,
        inventory_slot_count=inventory_slot_count_for_progress(inventory),
        items=current_items,
        resource_counts=inventory.get("resourceCounts") if isinstance(inventory.get("resourceCounts"), dict) else {},
    )
    progress_record = dict(state.get("resourceProgress") or {})
    previous_signature = progress_record.get("previousInventorySignature") or progress_record.get("lastProcessedInventorySignature") or state.get("lastProcessedInventorySignature")
    previous_items_missing = (
        progress_record.get("baselineEstablished", state.get("baselineEstablished"))
        and state.get("previousInventoryItems") is None
        and progress_record.get("previousInventoryItems") is None
        and not progress_record.get("previousResourceSlots")
    )
    if previous_items_missing:
        previous_signature = None
    progress_state = rp.state_from_dict(
        {
            "schema": progress_record.get("schema"),
            "task": task,
            "sessionPath": state.get("sessionPath"),
            "goalCount": goal_count,
            "resourceGroup": group_id,
            "baselineEstablished": progress_record.get("baselineEstablished", state.get("baselineEstablished")),
            "baselineHeldCount": progress_record.get("baselineHeldCount", state.get("baselineHeldCount") if state.get("baselineHeldCount") is not None else (state.get("resourceBaselineCounts") or {}).get(group_id)),
            "baselineTick": progress_record.get("baselineTick", state.get("brainBaselineTick")),
            "baselineInventorySignature": progress_record.get("baselineInventorySignature") or safe_get(state, "inventoryBaseline.inventorySignature"),
            "currentHeldCount": progress_record.get("currentHeldCount", state.get("currentHeldCount") if state.get("currentHeldCount") is not None else (state.get("resourceCurrentCounts") or {}).get(group_id)),
            "previousInventorySignature": previous_signature,
            "previousInventoryTick": progress_record.get("previousInventoryTick") or progress_record.get("lastProcessedInventoryTick") or state.get("lastProcessedInventoryTick"),
            "previousResourceCount": progress_record.get("previousResourceCount", state.get("previousResourceCount")),
            "previousResourceSlots": progress_record.get("previousResourceSlots") or [],
            "observedGained": progress_record.get("observedGained", state.get("observedGained", (state.get("resourceGainedCounts") or {}).get(group_id, 0))),
            "observedRemoved": progress_record.get("observedRemoved", state.get("observedRemoved", (state.get("resourceLostCounts") or {}).get(group_id, 0))),
            "displayedGoalProgress": progress_record.get("displayedGoalProgress", state.get("displayedGoalProgress", (state.get("resourceGainedCounts") or {}).get(group_id, 0))),
            "goalComplete": progress_record.get("goalComplete", state.get("goalComplete")),
            "hasValidPostBaselineProgressHistory": progress_record.get("hasValidPostBaselineProgressHistory", state.get("hasValidPostBaselineProgressHistory")),
            "lastUpdateReason": progress_record.get("lastUpdateReason"),
            "repairWarnings": progress_record.get("repairWarnings") or state.get("progressHistoryRepairWarnings") or [],
            "progressInvalidSnapshotCount": progress_record.get("progressInvalidSnapshotCount", state.get("progressInvalidSnapshotCount")),
            "progressRetainedPreviousCount": progress_record.get("progressRetainedPreviousCount", state.get("progressRetainedPreviousCount")),
            "progressFlickerPreventedCount": progress_record.get("progressFlickerPreventedCount", state.get("progressFlickerPreventedCount")),
            "lastProgressInvalidReason": progress_record.get("lastProgressInvalidReason", state.get("lastProgressInvalidReason")),
            "lastProgressRetainedTick": progress_record.get("lastProgressRetainedTick", state.get("lastProgressRetainedTick")),
            "lastValidProgressTick": progress_record.get("lastValidProgressTick", state.get("lastValidProgressTick")),
            "lastValidInventorySignature": progress_record.get("lastValidInventorySignature", state.get("lastValidInventorySignature")),
        },
        task=task,
        goal_count=goal_count,
        resource_group=group_id,
    )
    if goal_count is None:
        count_result = rp.count_resource_items(snapshot, definition)
        held = count_result.get("count") if count_result.get("known") else None
        resource_progress = {
            "resourceGroup": group_id,
            "baselineHeldCount": None,
            "currentHeldCount": held,
            "previousResourceCount": None,
            "netChangeFromBaseline": None,
            "cumulativeGained": None,
            "cumulativeLostOrRemoved": None,
            "observedGained": None,
            "observedRemoved": None,
            "displayedGoalProgress": None,
            "goalCount": None,
            "goalComplete": False,
            "progressSource": count_result.get("source") if count_result.get("known") else "unknown",
            "matchedSlots": count_result.get("matchedSlots") or [],
            "matchedSlotDetails": count_result.get("matchedSlotDetails") or [],
            "matchedItemIds": count_result.get("matchedItemIds") or [],
            "previousInventoryItems": list(normalized_current_items or []),
            "progressDisabled": True,
            "hasValidPostBaselineProgressHistory": False,
        }
        return {
            "goalCount": None,
            "goalResourceGroup": group_id,
            "resourceDisplayName": definition.display_name,
            "resourceItemIds": list(item_ids),
            "logsGained": None,
            "gainedSinceBaseline": None,
            "cumulativeGained": None,
            "cumulativeLostOrRemoved": None,
            "observedGained": None,
            "observedRemoved": None,
            "displayedGoalProgress": None,
            "currentHeldResourceCount": held,
            "baselineResourceCount": None,
            "baselineHeldCount": None,
            "baselineEstablished": False,
            "previousResourceCount": None,
            "netResourceChange": None,
            "netChangeFromBaseline": None,
            "resourceBaselineCounts": {},
            "resourceCurrentCounts": {group_id: int(held or 0)} if count_result.get("known") else {},
            "resourceGainedCounts": {},
            "resourceLostCounts": {},
            "resourceDeltaGainedCounts": {},
            "resourceDeltaEventsSeen": list(state.get("resourceDeltaEventsSeen") or []),
            "matchedItems": count_result.get("matchedItems") or [],
            "matchedSlots": count_result.get("matchedSlots") or [],
            "matchedSlotDetails": count_result.get("matchedSlotDetails") or [],
            "matchedItemIds": count_result.get("matchedItemIds") or [],
            "currentInventoryItems": list(normalized_current_items or []),
            "previousInventoryItems": [],
            "baselineInventoryItems": [],
            "resourceProgress": resource_progress,
            "inventorySlotDiagnostics": inventory_slot_diagnostics(inventory),
            "complete": False,
            "known": bool(count_result.get("known")),
            "progressDisabled": True,
            "progressSource": count_result.get("source") if count_result.get("known") else "unknown",
            "reason": "progress disabled because no goal count was supplied",
            "evidence": ["inventory.items"] if count_result.get("known") and not count_result.get("summaryDerived") else [],
            "warnings": dedupe_strings(count_result.get("warnings") or []),
            "observations": [],
            "inventoryBaselineSignature": None,
            "currentInventorySignature": current_signature,
            "missingReason": count_result.get("missingReason") if not count_result.get("known") else None,
        }

    analyzer_inventory = dict(inventory)
    analyzer_inventory["items"] = list(current_items) if current_items is not None else raw_inventory_items
    analyzer_inventory["resourceCounts"] = snapshot.resource_counts
    analyzer_inventory["inventorySignature"] = current_signature
    analyzer_inventory["inventorySlotCount"] = snapshot.inventory_slot_count
    inventory_context = inventory_analyzer.analyze_inventory(
        response=response,
        inventory=analyzer_inventory,
        progress_state=progress_state,
        resource_definition=definition,
        goal_count=goal_count,
    )
    result = inventory_context.progress_result
    new_state = result.state
    result_warnings = list(result.warnings)
    progress_state_repaired = bool(result.progress_state_repaired)
    repair_reason = result.repair_reason
    invariant_violations = list(result.invariant_violations)
    if rp.OLD_CUMULATIVE_HISTORY_WARNING in new_state.repair_warnings:
        progress_state_repaired = True
        repair_reason = repair_reason or rp.OLD_CUMULATIVE_HISTORY_WARNING
        if repair_reason not in result_warnings:
            result_warnings.append(repair_reason)
    baseline_count = new_state.baseline_held_count
    held = result.current_held_count
    net_change = int(held) - int(baseline_count) if held is not None and baseline_count is not None else None
    resource_counts_current = {group_id: int(held or 0)} if held is not None else {}
    progress_value = result.displayed_goal_progress
    slot_details = result.matched_slot_details
    matched_items = [
        {"slot": item.get("slot"), "itemId": item.get("itemId"), "quantity": item.get("quantity")}
        for item in slot_details
        if item.get("itemId") is not None
    ]
    resource_progress = {
        **rp.state_to_dict(new_state),
        "baselineEstablished": new_state.baseline_established,
        "baselineHeldCount": baseline_count,
        "currentHeldCount": held,
        "previousResourceCount": held,
        "netChangeFromBaseline": net_change,
        "cumulativeGained": None,
        "cumulativeLostOrRemoved": None,
        "observedGained": None,
        "observedRemoved": None,
        "displayedGoalProgress": progress_value,
        "postBaselineGainObserved": False,
        "hasValidPostBaselineProgressHistory": False,
        "progressChangeCount": None,
        "progressStateRepaired": progress_state_repaired,
        "repairReason": repair_reason,
        "invariantViolations": invariant_violations,
        "progressRetainedFromPrevious": result.progress_retained_from_previous,
        "retainedReason": result.retained_reason,
        "retainedAgeTicks": result.retained_age_ticks,
        "progressDropReason": result.progress_drop_reason,
        "progressHeldReason": result.progress_held_reason,
        "progressInvalidSnapshotCount": result.progress_invalid_snapshot_count,
        "progressRetainedPreviousCount": result.progress_retained_previous_count,
        "progressFlickerPreventedCount": result.progress_flicker_prevented_count,
        "lastProgressInvalidReason": result.last_progress_invalid_reason,
        "lastProgressRetainedTick": result.last_progress_retained_tick,
        "lastValidProgressTick": result.last_valid_progress_tick,
        "lastValidInventorySignature": result.last_valid_inventory_signature,
        "currentSnapshotValid": result.current_snapshot_valid,
        "snapshotValidityMissing": result.snapshot_validity_missing,
        "previousInventorySnapshotAvailable": bool(new_state.last_inventory_signature),
        "goalCount": goal_count,
        "goalComplete": new_state.goal_complete,
        "progressSource": result.source,
        "matchedSlots": result.matched_slots,
        "matchedSlotDetails": slot_details,
        "matchedItemIds": result.matched_item_ids,
        "baselineInventorySignature": new_state.baseline_inventory_signature,
        "previousInventorySignature": new_state.last_inventory_signature,
        "currentInventorySignature": current_signature,
        "lastProcessedInventorySignature": new_state.last_inventory_signature,
        "lastProcessedInventoryTick": new_state.last_inventory_tick,
        "currentInventoryTick": snapshot.latest_tick,
        "inventorySlotCount": snapshot.inventory_slot_count,
        "duplicateSnapshot": result.duplicate_snapshot,
        "progressUpdateApplied": result.progress_update_applied,
        "progressUpdateReason": result.reason,
        "previousResourceSlots": [],
        "currentResourceSlots": result.matched_slots,
        "baselineResourceSlots": [],
        "slotDiff": {
            "available": False,
            "previousTotal": held,
            "currentTotal": held,
            "movedWithinInventory": False,
        },
        "baselineInventoryItems": [],
        "previousInventoryItems": list(normalized_current_items or []),
    }
    return {
        "goalCount": goal_count,
        "goalResourceGroup": group_id,
        "resourceDisplayName": definition.display_name,
        "resourceItemIds": list(item_ids),
        "logsGained": progress_value,
        "gainedSinceBaseline": progress_value,
        "cumulativeGained": None,
        "cumulativeLostOrRemoved": None,
        "observedGained": None,
        "observedRemoved": None,
        "displayedGoalProgress": progress_value,
        "postBaselineGainObserved": False,
        "hasValidPostBaselineProgressHistory": False,
        "progressChangeCount": None,
        "progressStateRepaired": progress_state_repaired,
        "repairReason": repair_reason,
        "invariantViolations": invariant_violations,
        "progressRetainedFromPrevious": result.progress_retained_from_previous,
        "retainedReason": result.retained_reason,
        "retainedAgeTicks": result.retained_age_ticks,
        "progressDropReason": result.progress_drop_reason,
        "progressHeldReason": result.progress_held_reason,
        "progressInvalidSnapshotCount": result.progress_invalid_snapshot_count,
        "progressRetainedPreviousCount": result.progress_retained_previous_count,
        "progressFlickerPreventedCount": result.progress_flicker_prevented_count,
        "lastProgressInvalidReason": result.last_progress_invalid_reason,
        "lastProgressRetainedTick": result.last_progress_retained_tick,
        "lastValidProgressTick": result.last_valid_progress_tick,
        "lastValidInventorySignature": result.last_valid_inventory_signature,
        "currentSnapshotValid": result.current_snapshot_valid,
        "snapshotValidityMissing": result.snapshot_validity_missing,
        "previousInventorySnapshotAvailable": bool(new_state.last_inventory_signature),
        "currentHeldResourceCount": held,
        "baselineResourceCount": baseline_count,
        "baselineHeldCount": baseline_count,
        "baselineEstablished": new_state.baseline_established,
        "baselineTick": new_state.baseline_tick,
        "previousResourceCount": held,
        "netResourceChange": net_change,
        "netChangeFromBaseline": net_change,
        "resourceBaselineCounts": {group_id: int(baseline_count)} if baseline_count is not None else {},
        "resourceCurrentCounts": resource_counts_current,
        "resourceGainedCounts": {},
        "resourceLostCounts": {},
        "resourceDeltaGainedCounts": {},
        "resourceDeltaEventsSeen": list(state.get("resourceDeltaEventsSeen") or []),
        "matchedItems": matched_items,
        "matchedSlots": result.matched_slots,
        "matchedSlotDetails": slot_details,
        "matchedItemIds": result.matched_item_ids,
        "currentInventoryItems": list(normalized_current_items or []),
        "previousInventoryItems": list(normalized_current_items or []),
        "baselineInventoryItems": [],
        "resourceProgress": resource_progress,
        "inventorySlotDiagnostics": inventory_slot_diagnostics(inventory),
        "complete": new_state.goal_complete,
        "known": held is not None,
        "progressSource": result.source,
        "reason": result.reason if not progress_state_repaired else f"{result.reason}; invalid prior progress state was corrected",
        "duplicateSnapshot": result.duplicate_snapshot,
        "progressUpdateApplied": result.progress_update_applied,
        "progressUpdateReason": result.reason,
        "evidence": ["resource_progress"],
        "warnings": dedupe_strings(result_warnings),
        "observations": dedupe_strings(result.observations),
        "inventoryBaselineSignature": new_state.baseline_inventory_signature,
        "currentInventorySignature": current_signature,
        "lastProcessedInventorySignature": new_state.last_inventory_signature,
        "lastProcessedInventoryTick": new_state.last_inventory_tick,
        "currentInventoryTick": snapshot.latest_tick,
        "missingReason": None if held is not None else ("inventory item list missing" if "inventoryItemsOrResourceCounts" in result.snapshot_validity_missing else "resource count unknown"),
    }


def estimate_progress(state: dict, response: dict, inventory: dict, events: list[dict], goal_count: int | None, task: str = "woodcutting") -> dict:
    return estimate_progress_with_resource_tracker(state, response, inventory, goal_count, task=task)


def default_state(task: str, goal_count: int | None) -> dict:
    now = utc_now()
    return {
        "schema": STATE_SCHEMA,
        "task": task,
        "goal": {"goalCount": goal_count},
        "startedAtUtc": now,
        "updatedAtUtc": now,
        "latestTick": None,
        "phase": "setup_observing",
        "substate": None,
        "confidence": 0.0,
        "inventoryBaseline": {},
        "currentInventory": {},
        "estimatedProgress": {"goalCount": goal_count, "logsGained": None, "known": False, "progressSource": "unknown", "reason": "not observed yet"},
        "goalComplete": False,
        "goalPreviouslyReached": False,
        "stateVersion": 3,
        "resourceProgress": {},
        "resourceBaselineCounts": {},
        "resourceCurrentCounts": {},
        "resourceGainedCounts": {},
        "resourceLostCounts": {},
        "baselineEstablished": False,
        "baselineHeldCount": None,
        "previousResourceCount": None,
        "observedGained": None,
        "observedRemoved": None,
        "displayedGoalProgress": 0,
        "postBaselineGainObserved": False,
        "hasValidPostBaselineProgressHistory": False,
        "progressChangeCount": None,
        "progressHistoryRepairWarnings": [],
        "brainBaselineTick": None,
        "goalResourceGroup": "woodcutting_logs" if task == "woodcutting" else None,
        "progressSource": "unknown",
        "lastInventorySignature": None,
        "lastProcessedInventorySignature": None,
        "lastProcessedInventoryTick": None,
        "lastProcessedSessionPath": None,
        "lastSeenTick": None,
        "baselineInventoryItems": None,
        "previousInventoryItems": None,
        "resourceDeltaGainedCounts": {},
        "resourceDeltaEventsSeen": [],
        "currentTargetMemory": {},
        "recentTargetMemory": [],
        "recentEventsSeen": [],
        "missingCapabilities": [],
        "activeWatchRequests": [],
        "observations": [],
        "noActionEmitted": True,
    }


def load_state(path: str | None, task: str, goal_count: int | None, reset: bool = False) -> dict:
    if not path or reset:
        return default_state(task, goal_count)
    state_path = expand_state_path(path)
    if state_path is None:
        return default_state(task, goal_count)
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_state(task, goal_count)
    if not isinstance(value, dict) or value.get("schema") != STATE_SCHEMA:
        return default_state(task, goal_count)
    if value.get("task") != task or safe_get(value, "goal.goalCount") != goal_count:
        return default_state(task, goal_count)
    state_version = as_int(value.get("stateVersion")) or 0
    value.setdefault("stateVersion", 3)
    value.setdefault("resourceProgress", {})
    value.setdefault("resourceLostCounts", {})
    value.setdefault("baselineEstablished", False)
    value.setdefault("observedGained", None)
    value.setdefault("observedRemoved", None)
    value.setdefault("displayedGoalProgress", 0)
    value.setdefault("postBaselineGainObserved", False)
    resource_progress = value.get("resourceProgress") if isinstance(value.get("resourceProgress"), dict) else {}
    group_id = str(value.get("goalResourceGroup") or resource_progress.get("resourceGroup") or ("woodcutting_logs" if task == "woodcutting" else "resources"))
    history_flag_present = (
        "hasValidPostBaselineProgressHistory" in value
        or "hasValidPostBaselineProgressHistory" in resource_progress
    )
    value.setdefault("hasValidPostBaselineProgressHistory", False)
    value.setdefault("progressChangeCount", None)
    value.setdefault("progressHistoryRepairWarnings", [])
    value.setdefault("brainBaselineTick", None)
    value.setdefault("lastProcessedInventorySignature", None)
    value.setdefault("lastProcessedInventoryTick", None)
    value.setdefault("lastProcessedSessionPath", None)
    value.setdefault("baselineInventoryItems", None)
    value.setdefault("previousInventoryItems", None)
    if (
        (state_version < 3 or not history_flag_present)
        and (
            progress_counter_value(value, "observedGained", group_id)
            or progress_counter_value(value, "observedRemoved", group_id)
            or progress_counter_value(value, "displayedGoalProgress", group_id)
        )
    ):
        value = clear_unvalidated_progress_history(value, OLD_PROGRESS_HISTORY_WARNING)
    return value


def state_file_path_warning(path: str | None) -> str | None:
    if not path or "%USERPROFILE%" not in path.upper():
        return None
    expanded = expand_state_path(path)
    if expanded is None:
        return "PowerShell does not expand %USERPROFILE%; use $env:USERPROFILE or Join-Path $env:USERPROFILE"
    return "state file path used %USERPROFILE%; PowerShell users should prefer $env:USERPROFILE or Join-Path $env:USERPROFILE"


def expand_state_path(path: str | None) -> Path | None:
    if not path:
        return None
    expanded = os.path.expandvars(path)
    if "%USERPROFILE%" in expanded.upper():
        home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
        if home:
            expanded = expanded.replace("%USERPROFILE%", home).replace("%userprofile%", home)
    if "%USERPROFILE%" in expanded.upper():
        return None
    return Path(expanded).expanduser()


def write_state(path: str | None, state: dict) -> None:
    if not path:
        return
    state_path = expand_state_path(path)
    if state_path is None:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp = state_path.with_name(f".{state_path.name}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=False)
        handle.write("\n")
        handle.flush()
    temp.replace(state_path)


def build_current_context_summary(response: dict, best: dict, inventory: dict, activity: dict) -> dict:
    player = safe_get(response, "baseline.player", {})
    navigation = candidate_navigation(best)
    return {
        "player": {
            "worldX": player.get("worldX") if isinstance(player, dict) else None,
            "worldY": player.get("worldY") if isinstance(player, dict) else None,
            "plane": player.get("plane") if isinstance(player, dict) else None,
            "sceneX": player.get("sceneX") if isinstance(player, dict) else None,
            "sceneY": player.get("sceneY") if isinstance(player, dict) else None,
        },
        "inventory": inventory,
        "activity": activity,
        "bestTarget": compact_candidate(best),
        "liveness": {
            "targetLiveState": best.get("targetLiveState") if best else None,
            "livenessInterpretation": best.get("livenessInterpretation") if best else None,
        },
        "reachability": {
            "directReachability": navigation.get("directReachability") or best.get("directReachability") if best else None,
            "pathLengthTiles": navigation.get("pathLengthTiles"),
            "targetInCollisionWindow": navigation.get("targetInCollisionWindow"),
        },
    }


def evaluate_brain(
    response: dict,
    state: dict,
    *,
    task: str,
    goal_count: int | None,
    max_events: int = 10,
    capabilities: dict | None = None,
    watch_response: dict | None = None,
    capability_error: str | None = None,
    watch_error: str | None = None,
    task_policy: task_policy_module.TaskPolicy | dict[str, Any] | str | None = None,
) -> tuple[dict, dict]:
    now = utc_now()
    resolved_policy = task_policy_module.resolve_task_policy(task_policy, task=task, profile=task)
    policy_payload = resolved_policy.to_dict()
    if not isinstance(response, dict) or response.get("schema") != "context_response.v1":
        decision = {
            "schema": DECISION_SCHEMA,
            "generatedAtUtc": now,
            "task": task,
            "goal": {"goalCount": goal_count},
            "contextStatus": "FAIL",
            "latestTick": None,
            "phase": "no_context",
            "substate": "no_context",
            "confidence": 1.0,
            "currentContextSummary": {},
            "progress": {"goalCount": goal_count, "logsGained": None, "known": False, "reason": "no valid context"},
            "observationNeeds": [{"capability": "context_response.v1", "status": "missing", "reason": "context service unavailable"}],
            "suggestedWatchRequests": [],
            "blockingConditions": ["context service unavailable or returned no valid response"],
            "missingCapabilities": ["context_response.v1"],
            "internalNextState": "wait_for_context",
            "taskPolicy": policy_payload,
            "noActionEmitted": True,
        }
        decision["genericTaskState"] = task_state.from_brain_decision(decision, policy=resolved_policy).to_dict()
        return decision, update_state_from_decision(state, decision, {}, [], watch_response)

    best = best_target(response)
    nearest = nearest_target(response)
    inventory = inventory_summary(response)
    activity = activity_summary(response)
    events = recent_events(response, max_events)
    missing = missing_capabilities(response)
    suggestions = suggested_watch_requests(response)
    capability_needs = capability_observation_needs(capabilities or {})
    warnings = response_warnings(response)
    blocking: list[str] = []
    substates: list[str] = []
    recent_task_signals: list[str] = []
    observations: list[str] = []

    stale = freshness_failed(response)
    full_inventory = inventory_is_full(inventory)
    freshness_domains = build_freshness_domains(
        response,
        inventory,
        stale=stale,
        best=best,
        nearest=nearest,
        policy=resolved_policy,
    )
    inventory_policy_can_proceed = inventory_policy_can_proceed_with_stale_targets(
        policy=resolved_policy,
        full_inventory=full_inventory,
        freshness_domains=freshness_domains,
    )
    busy, busy_evidence, activity_substates = activity_busy_analysis(activity)
    substates.extend(activity_substates)
    current_depleted = current_target_depleted(best) or current_target_depleted(nearest)
    recent_depletion = event_mentions(events, "target_depleted", "depleted", "stump", "despawned", "stale")
    inventory_changed = bool(inventory.get("changedRecently")) or event_mentions(events, "inventory_changed", "inventory changed")
    available = target_is_available(best) or target_is_available(nearest)
    reachability = safe_get(response, ["reachabilitySummary", "tree"], {})
    reachable_count = as_number(reachability.get("reachableCount")) if isinstance(reachability, dict) else None
    replacement_available = available or (reachable_count is not None and reachable_count > 0)
    unreachable = target_is_unreachable(best, nearest, response)
    no_target = not best and not nearest
    progress = estimate_progress(state, response, inventory, events, goal_count, task=task)
    observations.extend(progress.get("observations") or [])
    warnings.extend(progress.get("warnings") or [])

    if recent_depletion and replacement_available:
        substates.append("recent_target_depletion_observed")
        recent_task_signals.append("target depleted recently")
    if inventory_changed and not full_inventory:
        substates.append("recent_inventory_change")
        recent_task_signals.append("inventory changed recently")
    if candidate_live_state(best) == "live_assumed":
        substates.append("liveness_assumed")
    if not suggestions and any(item.get("watchable") for item in capability_needs):
        substates.append("watchable_capability_missing")
    substates = dedupe_strings(substates)

    required_missing = [
        item
        for item in missing
        if item not in OPTIONAL_CAPABILITIES
        and not item.startswith("watch:")
    ]

    progress_warning_set = set(progress.get("warnings") or [])
    reset_correction = bool({
        RESET_PROGRESS_REPAIR_WARNING,
        PROGRESS_REPAIR_WARNING,
        OLD_PROGRESS_HISTORY_WARNING,
        rp.BALANCED_CHURN_REPAIR_WARNING,
        rp.BASELINE_DRIFT_REPAIR_WARNING,
    }.intersection(progress_warning_set))
    has_valid_history = bool(progress.get("hasValidPostBaselineProgressHistory"))
    previously_reached = bool(state.get("goalComplete") or state.get("goalPreviouslyReached")) and not reset_correction and has_valid_history
    if not has_valid_history and progress.get("displayedGoalProgress") in (0, None):
        previously_reached = False
    goal_complete = bool(progress.get("complete") or previously_reached)
    if goal_complete:
        substates.append("goal_count_reached")
        progress["goalPreviouslyReached"] = previously_reached or bool(progress.get("complete"))
        if previously_reached and progress.get("currentHeldResourceCount") is not None and goal_count is not None and progress.get("currentHeldResourceCount") < goal_count:
            observations.append("goal was previously reached; current held count changed")

    phase = "unknown"
    confidence = 0.35
    policy_phase_can_ignore_context_fail = bool(inventory_policy_can_proceed and response.get("status") == "FAIL")
    if inventory_policy_can_proceed and freshness_domains.get("targetCandidateFreshness") != "fresh":
        if not best and not nearest:
            warnings.append("no tree candidates currently observed")
        warnings.append(f"target candidate freshness {freshness_domains.get('targetCandidateFreshness')}")
    if policy_phase_can_ignore_context_fail:
        warnings.append("context status is FAIL, but current inventory policy can proceed without target candidates")
    if stale and not inventory_policy_can_proceed:
        phase = "stale_context"
        confidence = 0.9
        blocking.append("context freshness failed")
    elif response.get("status") == "FAIL" and not policy_phase_can_ignore_context_fail:
        phase = "no_context"
        confidence = 0.7
        blocking.append("context status is FAIL")
    elif goal_complete:
        phase = "goal_complete"
        confidence = 0.95 if progress.get("progressSource") in {"inventory_snapshot_held_vs_baseline", "inventory_snapshot_slot_diff", "inventory_snapshot_baseline_diff", "inventory_delta_events"} else 0.85
        if progress.get("inventorySlotDiagnostics", {}).get("warnings"):
            confidence = min(confidence, 0.85)
    elif required_missing and not best and not inventory:
        phase = "missing_capability"
        confidence = 0.78
        blocking.append("required context capability is missing")
    elif full_inventory:
        phase = "inventory_full"
        confidence = 0.95
        blocking.append("inventory is full")
    elif current_depleted and not replacement_available:
        phase = "target_depleted"
        confidence = 0.84
        blocking.append("current target appears depleted/stale and no replacement is available")
    elif recent_depletion and not replacement_available:
        phase = "waiting_for_respawn"
        confidence = 0.82
        blocking.append("recent depletion observed and no reachable replacement is available")
    elif busy and (progress.get("progressSource") in {"inventory_snapshot_held_vs_baseline", "inventory_snapshot_slot_diff", "inventory_delta_events"} or events):
        phase = "monitoring_progress"
        confidence = 0.78
    elif busy:
        phase = "likely_busy"
        confidence = 0.74
    elif inventory_changed:
        phase = "inventory_changed"
        confidence = 0.76
    elif unreachable:
        phase = "blocked_or_unreachable"
        confidence = 0.8
        blocking.append("tree candidates exist but local reachability is blocked or outside the local window")
    elif available:
        phase = "target_available"
        confidence = 0.84
    elif no_target:
        phase = "no_target_observed"
        confidence = 0.84
        blocking.append("fresh context returned no tree candidates")
    elif required_missing:
        phase = "missing_capability"
        confidence = 0.65
        blocking.append("required context capability is missing")
    else:
        blocking.append("insufficient or conflicting observations")

    player = safe_get(response, "baseline.player", {})
    if isinstance(player, dict) and player.get("worldX") is not None:
        observations.append(f"player observed at {text(player.get('worldX'))},{text(player.get('worldY'))} plane {text(player.get('plane'))}")
    if inventory:
        observations.append(f"inventory observed: {text(inventory.get('freeSlots'))} free slots")
    if progress.get("currentHeldResourceCount") is not None:
        observations.append(f"{progress.get('resourceDisplayName', 'resources')} held: {progress.get('currentHeldResourceCount')}")
    if best:
        observations.append(f"best tree observed: {candidate_name(best)} {text(best.get('id'))}, reachability {candidate_reachability(best)}")
    if capability_error:
        warnings.append(f"capability registry unavailable: {capability_error}")
        capability_needs.append({"capability": "capability_registry", "status": "unavailable", "reason": capability_error})
    if watch_error:
        warnings.append(f"watch request failed: {watch_error}")

    observation_needs = build_observation_needs(missing, suggestions, capability_needs, progress)
    internal_next = internal_next_state_for(phase, progress, suggestions, resolved_policy)
    current_context = build_current_context_summary(response, best, inventory, activity)
    watch_response = watch_response if isinstance(watch_response, dict) and watch_response else {}

    context_status = response.get("status", "WARN")
    if policy_phase_can_ignore_context_fail and context_status == "FAIL":
        context_status = "WARN"

    decision = {
        "schema": DECISION_SCHEMA,
        "generatedAtUtc": now,
        "task": task,
        "goal": {"goalCount": goal_count},
        "contextStatus": context_status,
        "latestTick": response.get("latestTick"),
        "phase": phase,
        "substate": primary_substate(substates),
        "recentTaskSignals": dedupe_strings(recent_task_signals),
        "confidence": round(confidence, 2),
        "goalComplete": goal_complete,
        "goalProgress": {
            "resourceGroup": progress.get("goalResourceGroup"),
            "goalCount": progress.get("goalCount"),
            "baselineHeldCount": progress.get("baselineResourceCount"),
            "baselineEstablished": bool(progress.get("baselineEstablished")),
            "currentHeldCount": progress.get("currentHeldResourceCount"),
            "heldResourceCount": progress.get("currentHeldResourceCount"),
            "previousResourceCount": progress.get("previousResourceCount"),
            "netChangeFromBaseline": progress.get("netChangeFromBaseline", progress.get("netResourceChange")),
            "cumulativeGained": progress.get("cumulativeGained"),
            "cumulativeLostOrRemoved": progress.get("cumulativeLostOrRemoved"),
            "observedGained": progress.get("observedGained", progress.get("cumulativeGained")),
            "observedRemoved": progress.get("observedRemoved", progress.get("cumulativeLostOrRemoved")),
            "displayedGoalProgress": progress.get("displayedGoalProgress", progress.get("gainedSinceBaseline", progress.get("logsGained"))),
            "postBaselineGainObserved": bool(progress.get("postBaselineGainObserved")),
            "hasValidPostBaselineProgressHistory": bool(progress.get("hasValidPostBaselineProgressHistory")),
            "progressChangeCount": progress.get("progressChangeCount"),
            "progressStateRepaired": bool(progress.get("progressStateRepaired")),
            "repairReason": progress.get("repairReason"),
            "invariantViolations": progress.get("invariantViolations") or [],
            "progressRetainedFromPrevious": bool(progress.get("progressRetainedFromPrevious")),
            "retainedReason": progress.get("retainedReason"),
            "retainedAgeTicks": progress.get("retainedAgeTicks"),
            "progressDropReason": progress.get("progressDropReason"),
            "progressHeldReason": progress.get("progressHeldReason"),
            "progressInvalidSnapshotCount": progress.get("progressInvalidSnapshotCount"),
            "progressRetainedPreviousCount": progress.get("progressRetainedPreviousCount"),
            "progressFlickerPreventedCount": progress.get("progressFlickerPreventedCount"),
            "lastProgressInvalidReason": progress.get("lastProgressInvalidReason"),
            "lastProgressRetainedTick": progress.get("lastProgressRetainedTick"),
            "lastValidProgressTick": progress.get("lastValidProgressTick"),
            "lastValidInventorySignature": progress.get("lastValidInventorySignature"),
            "currentSnapshotValid": bool(progress.get("currentSnapshotValid")),
            "snapshotValidityMissing": progress.get("snapshotValidityMissing") or [],
            "previousInventorySnapshotAvailable": bool(progress.get("previousInventorySnapshotAvailable")),
            "currentInventorySignature": progress.get("currentInventorySignature"),
            "baselineTick": progress.get("baselineTick"),
            "gainedSinceStart": progress.get("gainedSinceBaseline", progress.get("logsGained")),
            "source": progress.get("progressSource"),
            "complete": goal_complete,
            "note": progress.get("reason"),
            "matchedSlots": progress.get("matchedSlots") or [],
            "matchedSlotDetails": progress.get("matchedSlotDetails") or [],
            "matchedItemIds": progress.get("matchedItemIds") or [],
            "lastProcessedInventorySignature": progress.get("lastProcessedInventorySignature"),
            "lastProcessedInventoryTick": progress.get("lastProcessedInventoryTick"),
            "duplicateSnapshot": bool(progress.get("duplicateSnapshot")),
            "progressUpdateApplied": bool(progress.get("progressUpdateApplied")),
            "progressUpdateReason": progress.get("progressUpdateReason"),
            "warnings": progress.get("warnings") or [],
            "progressDisabled": bool(progress.get("progressDisabled")),
        },
        "currentContextSummary": current_context,
        "freshnessDomains": freshness_domains,
        "progress": progress,
        "observationNeeds": observation_needs,
        "suggestedWatchRequests": suggestions,
        "watchRequest": compact_watch_response(watch_response),
        "blockingConditions": blocking,
        "missingCapabilities": sorted(set(canonical_capability(item) for item in missing)),
        "internalNextState": internal_next,
        "taskPolicy": policy_payload,
        "warnings": dedupe_strings(warnings),
        "observations": observations,
        "noActionEmitted": True,
    }
    decision["genericTaskState"] = task_state.from_brain_decision(decision, policy=resolved_policy).to_dict()
    decision.update(context_domain_summary(decision, response=response, policy=resolved_policy))
    return decision, update_state_from_decision(state, decision, inventory, events, watch_response)


def build_observation_needs(missing: list[str], suggestions: list[dict], capability_needs: list[dict], progress: dict) -> list[dict]:
    needs: list[dict] = []
    canonical_missing = [canonical_capability(item) for item in missing]
    if progress.get("progressDisabled"):
        if progress.get("missingReason"):
            needs.append(
                {
                    "capability": "inventory.items",
                    "status": "optional",
                    "reason": progress.get("missingReason"),
                    "suggestedWatchAlias": "inventory_summary",
                }
            )
    elif not progress.get("known"):
        missing_reason = str(progress.get("missingReason") or "")
        if "item list" in missing_reason or "item IDs" in str(progress.get("reason") or ""):
            needs.append(
                {
                    "capability": "inventory.items",
                    "status": "missing",
                    "reason": progress.get("reason") or "progress cannot be measured without inventory item IDs",
                    "suggestedWatchAlias": "inventory_summary",
                }
            )
        else:
            needs.append(
                {
                    "capability": "inventory.deltas",
                    "status": "recommended",
                    "reason": progress.get("reason") or "progress cannot be measured without inventory deltas",
                    "suggestedWatchAlias": "inventory_summary",
                }
            )
    elif progress.get("progressSource") in {"inventory_snapshot_held_vs_baseline", "inventory_snapshot_slot_diff", "inventory_snapshot_baseline_diff"}:
        needs.append(
            {
                "capability": "inventory.deltas",
                "status": "optional",
                "reason": "inventory deltas unavailable; progress is using inventory resource snapshots",
                "suggestedWatchAlias": "inventory_summary",
            }
        )
    for item in canonical_missing:
        if item in OPTIONAL_CAPABILITIES:
            needs.append({"capability": item, "status": "optional", "reason": "optional context precision"})
        elif item.startswith("watch:"):
            needs.append({"capability": item, "status": "watchable", "reason": "watch value is not active"})
        else:
            needs.append({"capability": item, "status": "missing", "reason": "context service reported missing capability"})
    for suggestion in suggestions:
        needs.append(
            {
                "capability": f"watch:{suggestion.get('alias')}",
                "status": "watch_recommended",
                "reason": "safe bounded watch is available",
                "suggestedWatchRequest": suggestion,
            }
        )
    for item in capability_needs:
        capability = canonical_capability(item.get("capability"))
        item = dict(item)
        item["capability"] = capability
        if capability not in {need.get("capability") for need in needs}:
            needs.append(item)
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for need in needs:
        key = (str(need.get("capability")), str(need.get("status")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(need)
    return deduped


def internal_next_state_for(phase: str, progress: dict, suggestions: list[dict], policy: task_policy_module.TaskPolicy | None = None) -> str:
    if phase == "no_context":
        return "wait_for_context"
    if phase == "stale_context":
        return "wait_for_fresh_context"
    if phase == "goal_complete":
        return "hold_goal_complete_state"
    if phase == "inventory_full":
        if policy and policy.fullInventoryStrategy == task_policy_module.InventoryFullStrategy.NEEDS_SERVICE:
            return "observe_service_context"
        if policy and policy.fullInventoryStrategy == task_policy_module.InventoryFullStrategy.PROCESS_INVENTORY:
            return "observe_inventory_processing_context"
        if policy and policy.fullInventoryStrategy == task_policy_module.InventoryFullStrategy.CONTINUE_TASK:
            return "continue_task_with_full_inventory"
        if policy and policy.fullInventoryStrategy == task_policy_module.InventoryFullStrategy.OBSERVE_ONLY:
            return "observe_inventory_full_condition"
        return "mark_inventory_full_condition"
    if phase in {"target_depleted", "waiting_for_respawn", "no_target_observed"}:
        return "observe_for_replacement_target"
    if phase == "blocked_or_unreachable":
        return "observe_navigation_and_alternatives"
    if phase in {"likely_busy", "monitoring_progress"}:
        return "monitor_inventory_and_liveness"
    if phase == "target_available":
        return "hold_target_available_state"
    if suggestions:
        return "request_or_review_missing_watches"
    return "continue_observing"


def compact_watch_response(response: dict) -> dict:
    if not response:
        return {}
    return {
        "schema": response.get("schema"),
        "acceptedCount": len(response.get("accepted") or []),
        "rejectedCount": len(response.get("rejected") or []),
        "activeWatchCount": len(response.get("activeWatches") or []),
        "warnings": response.get("warnings") or [],
        "requestWritten": response.get("requestWritten"),
    }


def update_state_from_decision(state: dict, decision: dict, inventory: dict, events: list[dict], watch_response: dict | None) -> dict:
    updated = dict(state)
    if updated.get("schema") != STATE_SCHEMA:
        updated = default_state(str(decision.get("task") or "woodcutting"), safe_get(decision, "goal.goalCount"))
    progress = decision.get("progress") if isinstance(decision.get("progress"), dict) else {}
    progress_disabled = bool(progress.get("progressDisabled"))
    if not updated.get("inventoryBaseline") and inventory:
        updated["inventoryBaseline"] = dict(inventory)
    current_target = safe_get(decision, "currentContextSummary.bestTarget", {})
    previous_target = updated.get("currentTargetMemory") if isinstance(updated.get("currentTargetMemory"), dict) else {}
    if previous_target and current_target and previous_target.get("key") != current_target.get("key"):
        memory = list(updated.get("recentTargetMemory") or [])
        memory.append(previous_target)
        updated["recentTargetMemory"] = memory[-10:]
    seen = list(updated.get("recentEventsSeen") or [])
    for event in events:
        key = event_key(event)
        if key not in seen:
            seen.append(key)
    active = list(updated.get("activeWatchRequests") or [])
    if isinstance(watch_response, dict) and watch_response.get("activeWatches"):
        active = watch_response.get("activeWatches") or active
    resource_progress = dict(progress.get("resourceProgress") or ({} if progress_disabled else updated.get("resourceProgress") or {}))
    resource_baseline_counts = dict(progress.get("resourceBaselineCounts") or ({} if progress_disabled else updated.get("resourceBaselineCounts") or {}))
    resource_gained_counts = {}
    resource_lost_counts = {}
    baseline_inventory_items = [] if progress_disabled else list(progress.get("baselineInventoryItems") or updated.get("baselineInventoryItems") or [])
    resource_progress = dict(resource_progress)
    baseline_established = bool(resource_progress.get("baselineEstablished") or progress.get("baselineEstablished"))
    last_processed_signature = progress.get("lastProcessedInventorySignature") or resource_progress.get("lastProcessedInventorySignature")
    last_processed_tick = progress.get("lastProcessedInventoryTick") if progress.get("lastProcessedInventoryTick") is not None else resource_progress.get("lastProcessedInventoryTick")
    baseline_tick = progress.get("baselineTick") if progress.get("baselineTick") is not None else resource_progress.get("baselineTick")
    has_valid_history = bool(progress.get("hasValidPostBaselineProgressHistory"))
    keep_goal_history = bool(has_valid_history and (progress.get("postBaselineGainObserved") or decision.get("goalComplete")))
    repair_warning_values = {
        PROGRESS_REPAIR_WARNING,
        RESET_PROGRESS_REPAIR_WARNING,
        OLD_PROGRESS_HISTORY_WARNING,
        PARTIAL_PROGRESS_REPAIR_WARNING,
        rp.PROGRESS_REPAIR_WARNING,
        rp.PARTIAL_PROGRESS_REPAIR_WARNING,
        rp.OLD_PROGRESS_HISTORY_WARNING,
        rp.INVALID_MATCHED_SLOT_WARNING,
        rp.BALANCED_CHURN_REPAIR_WARNING,
        rp.BASELINE_DRIFT_REPAIR_WARNING,
    }
    repair_warnings = [warning for warning in (progress.get("warnings") or []) if warning in repair_warning_values]
    current_snapshot_valid = bool(progress.get("currentSnapshotValid"))
    previous_inventory_items = (
        list(progress.get("currentInventoryItems") or updated.get("previousInventoryItems") or [])
        if current_snapshot_valid
        else list(updated.get("previousInventoryItems") or [])
    )
    updated.update(
        {
            "task": decision.get("task"),
            "goal": decision.get("goal"),
            "updatedAtUtc": decision.get("generatedAtUtc"),
            "latestTick": decision.get("latestTick"),
            "phase": decision.get("phase"),
            "substate": decision.get("substate"),
            "confidence": decision.get("confidence"),
            "currentInventory": dict(inventory),
            "estimatedProgress": progress,
            "goalComplete": bool(decision.get("goalComplete")),
            "goalPreviouslyReached": False if progress_disabled or not keep_goal_history else bool(decision.get("goalComplete") or updated.get("goalPreviouslyReached")),
            "stateVersion": 3,
            "resourceProgress": resource_progress,
            "resourceBaselineCounts": resource_baseline_counts,
            "resourceCurrentCounts": dict(progress.get("resourceCurrentCounts") or {}),
            "resourceGainedCounts": resource_gained_counts,
            "resourceLostCounts": resource_lost_counts,
            "baselineEstablished": False if progress_disabled else baseline_established,
            "baselineHeldCount": None if progress_disabled else progress.get("baselineHeldCount", progress.get("baselineResourceCount")),
            "previousResourceCount": None if progress_disabled else progress.get("previousResourceCount"),
            "observedGained": None,
            "observedRemoved": None,
            "displayedGoalProgress": 0 if progress_disabled else progress.get("displayedGoalProgress", progress.get("gainedSinceBaseline", 0)),
            "postBaselineGainObserved": False if progress_disabled else bool(progress.get("postBaselineGainObserved")),
            "hasValidPostBaselineProgressHistory": False if progress_disabled else has_valid_history,
            "progressChangeCount": None,
            "progressHistoryRepairWarnings": repair_warnings,
            "brainBaselineTick": None if progress_disabled else baseline_tick,
            "resourceDeltaGainedCounts": {},
            "resourceDeltaEventsSeen": [],
            "goalResourceGroup": progress.get("goalResourceGroup") or updated.get("goalResourceGroup"),
            "progressSource": progress.get("progressSource") or "unknown",
            "lastInventorySignature": inventory.get("inventorySignature"),
            "lastProcessedInventorySignature": None if progress_disabled else last_processed_signature,
            "lastProcessedInventoryTick": None if progress_disabled else last_processed_tick,
            "lastProcessedSessionPath": None if progress_disabled else updated.get("sessionPath"),
            "lastSeenTick": decision.get("latestTick"),
            "baselineInventoryItems": baseline_inventory_items,
            "previousInventoryItems": previous_inventory_items,
            "currentTargetMemory": current_target if isinstance(current_target, dict) else {},
            "recentEventsSeen": seen[-100:],
            "missingCapabilities": decision.get("missingCapabilities") or [],
            "activeWatchRequests": active,
            "observations": decision.get("observations") or [],
            "noActionEmitted": True,
        }
    )
    return updated


def brain_failure(task: str, goal_count: int | None, message: str, state: dict | None = None) -> tuple[dict, dict]:
    state = state if isinstance(state, dict) else default_state(task, goal_count)
    decision, updated = evaluate_brain(
        {},
        state,
        task=task,
        goal_count=goal_count,
        capabilities={},
        capability_error=message,
    )
    return decision, updated


def brain_once(args: argparse.Namespace, state: dict) -> tuple[dict, dict]:
    capabilities, capability_error = fetch_optional_endpoint(args.host, args.port, "/capabilities", args.timeout)
    _watches, watches_error = fetch_optional_endpoint(args.host, args.port, "/watches", args.timeout)
    capability_error = capability_error or watches_error
    selected_policy = getattr(args, "task_policy", None)
    try:
        context = fetch_context(args.host, args.port, args.task, args.max_candidates, args.max_events, args.timeout)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
        return brain_failure(args.task, args.goal_count, str(exc), state)
    preliminary_decision, _preliminary_state = evaluate_brain(
        context,
        state,
        task=args.task,
        goal_count=args.goal_count,
        max_events=args.max_events,
        capabilities=capabilities,
        capability_error=capability_error,
        task_policy=selected_policy,
    )
    watch_response: dict = {}
    watch_error: str | None = None
    if args.request_missing_watches and preliminary_decision.get("suggestedWatchRequests"):
        watch_response, watch_error = post_optional_watch_request(
            args.host,
            args.port,
            args.task,
            preliminary_decision.get("suggestedWatchRequests") or [],
            args.timeout,
        )
    return evaluate_brain(
        context,
        state,
        task=args.task,
        goal_count=args.goal_count,
        max_events=args.max_events,
        capabilities=capabilities,
        watch_response=watch_response,
        capability_error=capability_error,
        watch_error=watch_error,
        task_policy=selected_policy,
    )


def aim_label(candidate: dict) -> str:
    aim = candidate.get("aimPoint")
    if not isinstance(aim, dict):
        return "unknown"
    x = aim.get("canvasX", aim.get("x"))
    y = aim.get("canvasY", aim.get("y"))
    if x is None or y is None:
        return "unknown"
    try:
        return f"{float(x):.1f},{float(y):.1f}"
    except (TypeError, ValueError):
        return f"{x},{y}"


def progress_source_label(source: Any) -> str:
    labels = {
        "inventory_resource_counts": "inventory resource counts",
        "inventory_snapshot_items": "inventory snapshot item counts",
        "inventory_snapshot_held_vs_baseline": "inventory snapshot held-vs-baseline",
        "inventory_snapshot_slot_diff": "inventory snapshot slot diff",
        "inventory_snapshot_slot_movement": "inventory snapshot slot movement",
        "inventory_snapshot_baseline_diff": "inventory snapshot baseline diff",
        "inventory_delta_events": "inventory delta events",
        "baseline_initialized": "baseline initialized",
        "unchanged_snapshot": "unchanged snapshot",
        "retained_previous_progress": "retained previous progress",
        "stale_previous_valid_snapshot": "stale previous valid snapshot",
        "previous_snapshot_initialized": "previous snapshot initialized",
        "inventory_snapshot_invalid": "invalid inventory snapshot",
        "baseline_pending": "baseline pending",
        "unknown": "unknown",
    }
    return labels.get(str(source or "unknown"), text(source))


def activity_state_label(activity: dict) -> str:
    apparent = str(activity.get("apparentState") or "").lower()
    if active_animation_value(activity.get("animation")) is not None:
        return "animating"
    if explicit_interacting_value(activity.get("interacting")) is not None:
        return "interacting"
    if activity.get("isMoving") is True or apparent == "moving":
        return "moving"
    busy, _evidence, _substates = activity_busy_analysis(activity)
    if busy:
        return "likely_busy"
    if apparent in {"idle", "unknown", ""}:
        return "idle" if apparent == "idle" else "unknown"
    return apparent


def important_observation_needs(needs: list[dict], progress: dict) -> list[dict]:
    result: list[dict] = []
    for need in needs:
        if not isinstance(need, dict):
            continue
        capability = canonical_capability(need.get("capability"))
        status = str(need.get("status") or "")
        if capability == "inventory.deltas" and status == "optional" and progress.get("progressSource") in {
            "inventory_snapshot_held_vs_baseline",
            "inventory_snapshot_slot_diff",
            "inventory_snapshot_baseline_diff",
            "inventory_snapshot_items",
        }:
            continue
        if capability == "activity.animation_frame" and status == "optional":
            continue
        item = dict(need)
        item["capability"] = capability
        result.append(item)
    return result


def intent_label(value: Any) -> str:
    return text(value).replace("_", " ")


def policy_disposition_label(generic_state: dict[str, Any]) -> str | None:
    strategy = str(generic_state.get("fullInventoryStrategy") or "")
    disposition = str(generic_state.get("resourceDisposition") or "")
    if strategy == "needs_service":
        if disposition == "bank":
            return "bank resources"
        if disposition and disposition not in {"none", "unknown"}:
            return f"{disposition} resources"
        return "needs service"
    if strategy == "process_inventory":
        if disposition == "burn":
            return "burn resources"
        if disposition == "drop":
            return "drop resources"
        if disposition and disposition not in {"none", "unknown"}:
            return f"{disposition} resources"
        return "process inventory"
    if strategy == "continue_task":
        return "continue task"
    if strategy == "observe_only":
        return "observe only"
    return None


def target_context_label(target: dict | None) -> str:
    if not isinstance(target, dict) or not target:
        return "none"
    name = text(target.get("name") or target.get("targetName"), "target")
    target_id = text(target.get("id"), "")
    reachability = text(target.get("directReachability"), "unknown")
    if target_id:
        return f"{name} {target_id}, {reachability}"
    return f"{name}, {reachability}"


def format_human(decision: dict) -> str:
    task = str(decision.get("task") or "task").upper()
    status = decision.get("contextStatus") or "WARN"
    context = decision.get("currentContextSummary") if isinstance(decision.get("currentContextSummary"), dict) else {}
    player = context.get("player") if isinstance(context.get("player"), dict) else {}
    inventory = context.get("inventory") if isinstance(context.get("inventory"), dict) else {}
    best = context.get("bestTarget") if isinstance(context.get("bestTarget"), dict) else {}
    activity = context.get("activity") if isinstance(context.get("activity"), dict) else {}
    liveness = context.get("liveness") if isinstance(context.get("liveness"), dict) else {}
    reachability = context.get("reachability") if isinstance(context.get("reachability"), dict) else {}
    progress = decision.get("progress") if isinstance(decision.get("progress"), dict) else {}
    generic_state = decision.get("genericTaskState") if isinstance(decision.get("genericTaskState"), dict) else {}
    service_context = decision.get("serviceContext") if isinstance(decision.get("serviceContext"), dict) else {}
    process_context = decision.get("processInventoryContext") if isinstance(decision.get("processInventoryContext"), dict) else {}
    navigation_intent_context = decision.get("navigationIntentContext") if isinstance(decision.get("navigationIntentContext"), dict) else {}
    pathing_context = decision.get("pathingContext") if isinstance(decision.get("pathingContext"), dict) else {}
    active_intent = str(generic_state.get("activeIntent") or "")
    active_target = generic_state.get("activeIntentTarget") if isinstance(generic_state.get("activeIntentTarget"), dict) else None
    available_target = generic_state.get("availableTarget") if isinstance(generic_state.get("availableTarget"), dict) else None
    previous_target = generic_state.get("previousIntentTarget") if isinstance(generic_state.get("previousIntentTarget"), dict) else None
    goal_count = safe_get(decision, "goal.goalCount")
    observe_only = goal_count is None or progress.get("progressDisabled")
    lines = [
        f"{task} BRAIN CORE - {status}",
        "",
        "Goal:",
        f"  {'observing woodcutting context' if observe_only else 'collect ' + text(goal_count) + ' logs'}",
        "",
        "Phase:",
        f"  {text(decision.get('phase'))}",
        f"  confidence {float(decision.get('confidence') or 0.0):.2f}",
        "",
        "Current context:",
        f"  Player: {text(player.get('worldX'))},{text(player.get('worldY'))} plane {text(player.get('plane'))}",
        f"  Inventory: {text(inventory.get('freeSlots'))} free slots, {'full' if inventory_is_full(inventory) else 'not full'}",
        f"  Activity: {activity_state_label(activity)}",
    ]
    if active_intent:
        lines.append(f"  Active intent: {intent_label(active_intent)}")
    policy_label = policy_disposition_label(generic_state)
    if policy_label:
        lines.append(f"  Task policy: {policy_label}")
    service_needed = generic_state.get("serviceTypeNeeded")
    process_needed = generic_state.get("processTypeNeeded")
    if service_needed:
        lines.append(f"  Service needed: {text(service_needed)}")
        service_candidate = service_context.get("bestServiceCandidate") if isinstance(service_context.get("bestServiceCandidate"), dict) else None
        if service_candidate:
            lines.append(f"  Best service candidate: {target_context_label(service_candidate)}")
        elif service_context.get("serviceNeeded"):
            lines.append("  Service candidate: not observed")
            lines.append("  Missing/needed context: bank_service candidate")
    if process_needed:
        lines.append(f"  Process needed: {text(process_needed)}")
        if process_context.get("heldResourceCount") is not None:
            lines.append(f"  Held logs: {text(process_context.get('heldResourceCount'))}")
        if process_context.get("tinderboxStatus") not in (None, "not_required"):
            lines.append(f"  Tinderbox: {text(process_context.get('tinderboxStatus'))}")
        lines.append("  No service target required")
    if inventory_is_full(inventory) and str(generic_state.get("fullInventoryStrategy") or "") == "continue_task":
        lines.append("  Inventory full: expected/allowed")
    if navigation_intent_context:
        navigation_reason = str(navigation_intent_context.get("navigationReason") or "")
        should_show_navigation = bool(navigation_intent_context.get("navigationNeeded")) or navigation_reason in {
            "service_target_available",
            "service_target_missing",
            "target_unreachable",
            "full_pathfinding_missing",
        }
        if should_show_navigation:
            destination = navigation_intent_context.get("destinationTarget") if isinstance(navigation_intent_context.get("destinationTarget"), dict) else None
            lines.extend(["", "Navigation context:"])
            if destination:
                lines.append(f"  Destination: {target_context_label(destination)}")
            elif navigation_reason == "service_target_missing":
                lines.append("  waiting for service target context")
            else:
                lines.append("  Destination: none")
            if navigation_intent_context.get("distanceTiles") is not None:
                lines.append(f"  Distance: {text(navigation_intent_context.get('distanceTiles'))} tiles")
            if navigation_intent_context.get("directReachability") is not None:
                lines.append(f"  Reachability: {text(navigation_intent_context.get('directReachability'))}")
            collision_available = navigation_intent_context.get("collisionWindowAvailable")
            if collision_available is not None:
                lines.append(f"  Collision window: {'available' if collision_available else 'missing'}")
            nav_missing = navigation_intent_context.get("missingCapabilities") if isinstance(navigation_intent_context.get("missingCapabilities"), list) else []
            if nav_missing:
                lines.append(f"  Missing: {', '.join(str(item) for item in nav_missing)}")
    if pathing_context:
        lines.extend(["", "Pathing:"])
        if not pathing_context.get("pathingNeeded"):
            lines.append("  not needed for current phase")
        else:
            destination = pathing_context.get("destination") if isinstance(pathing_context.get("destination"), dict) else {}
            destination_label = target_context_label(destination) if destination else "none"
            lines.append("  Needed: yes")
            lines.append(f"  Destination: {destination_label}")
            destination_tile = pathing_context.get("destinationTile") if isinstance(pathing_context.get("destinationTile"), dict) else None
            if destination_tile:
                lines.append(f"  Destination tile: {text(destination_tile.get('worldX'))},{text(destination_tile.get('worldY'))},{text(destination_tile.get('plane'))}")
            final_approach_tile = pathing_context.get("finalApproachTile") if isinstance(pathing_context.get("finalApproachTile"), dict) else None
            if final_approach_tile:
                lines.append(f"  Final approach: {text(final_approach_tile.get('worldX'))},{text(final_approach_tile.get('worldY'))},{text(final_approach_tile.get('plane'))}")
            path_target_tile = pathing_context.get("pathTargetTile") if isinstance(pathing_context.get("pathTargetTile"), dict) else None
            if path_target_tile:
                lines.append(f"  Routed-to tile: {text(path_target_tile.get('worldX'))},{text(path_target_tile.get('worldY'))},{text(path_target_tile.get('plane'))}")
            lines.append(f"  Local reachability: {text(pathing_context.get('localReachability'))}")
            if pathing_context.get("pathLengthTiles") is not None:
                lines.append(f"  Path length: {text(pathing_context.get('pathLengthTiles'))} tiles")
            waypoint = pathing_context.get("nextWaypointTile") if isinstance(pathing_context.get("nextWaypointTile"), dict) else None
            if waypoint:
                lines.append(f"  Next waypoint: {text(waypoint.get('worldX'))},{text(waypoint.get('worldY'))},{text(waypoint.get('plane'))}")
            predicted = pathing_context.get("predictedPathTiles") if isinstance(pathing_context.get("predictedPathTiles"), list) else []
            if predicted:
                preview = []
                for tile in predicted[:8]:
                    if isinstance(tile, dict):
                        preview.append(f"{text(tile.get('worldX'))},{text(tile.get('worldY'))},{text(tile.get('plane'))}")
                if preview:
                    lines.append(f"  Predicted path: {' -> '.join(preview)}")
            if pathing_context.get("pathDisplayWasCapped") is not None:
                lines.append(
                    "  Path display: "
                    f"available={text(pathing_context.get('predictedPathAvailableCount'))}, "
                    f"displayed={text(pathing_context.get('predictedPathDisplayedCount'))}, "
                    f"capped={'yes' if pathing_context.get('pathDisplayWasCapped') else 'no'}"
                )
            if pathing_context.get("pathSegmentsValid") is False:
                lines.append(
                    "  Path warning: invalid segment "
                    f"({text((pathing_context.get('firstInvalidPathSegment') or {}).get('reason') if isinstance(pathing_context.get('firstInvalidPathSegment'), dict) else None)})"
                )
            if pathing_context.get("approachQuality"):
                lines.append(
                    "  Approach: "
                    f"{text(pathing_context.get('approachQuality'))}, "
                    f"reason={text(pathing_context.get('selectedApproachReason'))}"
                )
            lines.append(f"  Movement model: {text(pathing_context.get('predictedMovementModel'))}")
            if pathing_context.get("pathIntentRetained") is not None:
                retained = "yes" if pathing_context.get("pathIntentRetained") else "no"
                lines.append(
                    "  Path intent: "
                    f"retained={retained}, "
                    f"stableFor={text(pathing_context.get('pathStableForTicks'))}, "
                    f"movement={text(pathing_context.get('movementState'))}, "
                    f"switch={text(pathing_context.get('switchReason'))}"
                )
            notes = pathing_context.get("predictedMovementNotes") if isinstance(pathing_context.get("predictedMovementNotes"), list) else []
            if notes:
                lines.append(f"  Note: {text(notes[0])}")
    if active_target:
        lines.append(f"  Current target: {target_context_label(active_target)}, aim {aim_label(active_target)}")
    elif best and not active_intent:
        lines.extend(
            [
                f"  Current target: {text(best.get('name'))} {text(best.get('id'))}, {text(best.get('directReachability'))}, aim {aim_label(best)}",
            ]
        )
    elif active_intent in {"needs_service", "none", "observe"}:
        lines.append("  Current target: none")
    else:
        lines.append("  Current target: none")
    if previous_target:
        lines.append(f"  Previous target: {target_context_label(previous_target)}")
    if available_target and not active_target:
        lines.append(f"  Available target: {target_context_label(available_target)}")
    if best:
        lines.extend(
            [
                f"  Liveness: {text(liveness.get('livenessInterpretation') or liveness.get('targetLiveState'))}",
                f"  Reachability: {text(reachability.get('directReachability'))}, path length {text(reachability.get('pathLengthTiles'))}",
            ]
        )
    recent_signals = decision.get("recentTaskSignals") if isinstance(decision.get("recentTaskSignals"), list) else []
    if recent_signals:
        lines.extend(["", "Recent task signals:"])
        for signal in recent_signals:
            lines.append(f"  {signal}")
    lines.extend(["", "Progress:"])
    if observe_only:
        if progress.get("currentHeldResourceCount") is not None:
            lines.append(f"  held logs: {text(progress.get('currentHeldResourceCount'))}")
        lines.append("  disabled, no goal count set")
    else:
        if progress.get("progressSource") == "baseline_pending":
            if progress.get("currentHeldResourceCount") is not None:
                lines.append(f"  held logs: {text(progress.get('currentHeldResourceCount'))}")
            lines.extend(
                [
                    "  baseline pending",
                    f"  reason: {text(progress.get('reason'))}",
                ]
            )
        else:
            lines.extend(
                [
                    f"  held logs: {text(progress.get('currentHeldResourceCount'))}",
                    f"  baseline held logs: {text(progress.get('baselineHeldCount', progress.get('baselineResourceCount')))}",
                    f"  gained since start: {text(progress.get('gainedSinceBaseline', progress.get('logsGained')))} / {text(progress.get('goalCount'))}",
                ]
            )
            lost = progress.get("cumulativeLostOrRemoved")
            if lost not in (None, 0, "0") and progress.get("hasValidPostBaselineProgressHistory"):
                lines.append(f"  lost/removed since start: {text(lost)}")
            lines.extend(
                [
                    f"  goal reached: {'yes' if decision.get('goalComplete') else 'no'}",
                    f"  source: {progress_source_label(progress.get('progressSource'))}",
                    f"  note: {text(progress.get('reason'))}",
                ]
            )
    matched_slot_details = progress.get("matchedSlotDetails") if isinstance(progress.get("matchedSlotDetails"), list) else []
    matched_slots = [
        row.get("slot")
        for row in matched_slot_details
        if isinstance(row, dict) and row.get("counted") and row.get("itemId") is not None and row.get("slot") is not None
    ]
    if not matched_slot_details:
        matched_slots = progress.get("matchedSlots") if isinstance(progress.get("matchedSlots"), list) else []
    if matched_slots:
        lines.append(f"  matched slots: {', '.join(str(slot) for slot in matched_slots)}")
    progress_warnings = progress.get("warnings") if isinstance(progress.get("warnings"), list) else []
    for warning in progress_warnings[:3]:
        lines.append(f"  warning: {warning}")
    lines.extend(["", "Observation needs:"])
    needs = important_observation_needs(decision.get("observationNeeds") if isinstance(decision.get("observationNeeds"), list) else [], progress)
    if needs:
        for need in needs[:8]:
            if not isinstance(need, dict):
                continue
            lines.append(f"  {text(need.get('capability'))}: {text(need.get('status'))} - {text(need.get('reason'), '')}".rstrip())
    else:
        lines.append("  none")
    decision_warnings = decision.get("warnings") if isinstance(decision.get("warnings"), list) else []
    if decision_warnings:
        lines.extend(["", "Warnings:"])
        for warning in decision_warnings[:8]:
            lines.append(f"  {warning}")
    blocking = generic_state.get("blockingConditions") if isinstance(generic_state.get("blockingConditions"), list) else decision.get("blockingConditions") if isinstance(decision.get("blockingConditions"), list) else []
    lines.extend(["", "Blocking conditions:"])
    if blocking:
        for item in blocking:
            lines.append(f"  {item}")
    else:
        lines.append("  none")
    lines.extend(
        [
            "",
            "Internal next state:",
            f"  {text(decision.get('internalNextState'))}",
            "",
            "No action emitted.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def print_decision(decision: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(decision, indent=2, sort_keys=False))
    else:
        print(format_human(decision), end="")


def run_watch(args: argparse.Namespace, state: dict) -> int:
    try:
        while True:
            decision, state = brain_once(args, state)
            write_state(args.state_file, state)
            if args.json and not args.human:
                os.system("cls" if os.name == "nt" else "clear")
                print(json.dumps(decision, indent=2, sort_keys=False))
            else:
                os.system("cls" if os.name == "nt" else "clear")
                print(f"Updated: {utc_now()}")
                print(format_human(decision), end="")
            time.sleep(max(0.1, float(args.interval)))
    except KeyboardInterrupt:
        return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only external brain core MVP over context_service.py.")
    parser.add_argument("--host", default="127.0.0.1", help="Context service host. Default: 127.0.0.1.")
    parser.add_argument("--port", type=int, default=8890, help="Context service port. Default: 8890.")
    parser.add_argument("--task", default="woodcutting", help="Task to evaluate. Default: woodcutting.")
    parser.add_argument("--task-policy", choices=task_policy_module.policy_names(), default="woodcutting_bank", help="Read-only task policy for interpreting conditions such as full inventory.")
    parser.add_argument("--goal-count", type=int, default=None, help="Goal count for the task, e.g. logs to collect.")
    parser.add_argument("--watch", action="store_true", help="Refresh until Ctrl+C.")
    parser.add_argument("--interval", type=float, default=1.0, help="Watch refresh interval seconds. Default: 1.")
    parser.add_argument("--json", action="store_true", help="Print brain_decision.v1 JSON.")
    parser.add_argument("--human", action="store_true", help="Print human-readable output. This is the default unless --json is used.")
    parser.add_argument("--state-file", help="Optional brain_state.v1 persistence path.")
    parser.add_argument("--reset-state", action="store_true", help="Ignore and overwrite any existing state file.")
    parser.add_argument("--request-missing-watches", action="store_true", help="Send bounded read-only watch requests for suggested missing observations.")
    parser.add_argument("--max-candidates", type=int, default=3, help="Max candidates to request. Default: 3.")
    parser.add_argument("--max-events", type=int, default=10, help="Max recent events to request. Default: 10.")
    parser.add_argument("--timeout", type=float, default=3.0, help="HTTP timeout seconds. Default: 3.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    state = load_state(args.state_file, args.task, args.goal_count, reset=args.reset_state)
    if args.watch:
        return run_watch(args, state)
    decision, state = brain_once(args, state)
    write_state(args.state_file, state)
    print_decision(decision, json_output=args.json and not args.human)
    return 0 if decision.get("phase") != "no_context" else 1


if __name__ == "__main__":
    raise SystemExit(main())

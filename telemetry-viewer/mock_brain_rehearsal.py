from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


SCHEMA = "mock_brain_rehearsal.v1"
CONTEXT_SCHEMA = "context_request.v1"
DEFAULT_NEEDS = [
    "baseline",
    "best:tree",
    "nearest:tree",
    "inventory",
    "activity",
    "liveness",
    "navigation_readiness",
    "events",
    "watches",
    "diagnostics",
]
LIVE_STATES_AVAILABLE = {"live", "live_assumed"}
LIVE_STATES_UNAVAILABLE = {"depleted_or_stump", "recently_despawned", "stale"}
BUSY_ACTIVITY_STATES = {"animating", "interacting"}
BUSY_WOODCUTTING_STATES = {"likely_chopping", "woodcutting_possible", "chopping"}
UNKNOWN_ACTIVITY_VALUES = {"", "unknown", "none", "null", "n/a", "na", "-1", "0"}
TASK_EVENT_TYPES = {
    "best_candidate_changed",
    "nearest_candidate_changed",
    "candidate_count_changed",
    "target_liveness_changed",
    "target_depleted",
    "liveness_suppressed_candidate",
    "depleted_candidate_suppressed",
    "candidate_revived",
    "best_candidate_aim_point_changed",
    "inventory_changed",
    "inventory_free_slots_changed",
    "inventory_full_changed",
    "activity_state_changed",
    "woodcutting_state_changed",
    "player_animation_changed",
    "interacting_target_changed",
    "reachability_changed",
    "best_candidate_reachability_changed",
    "nearest_candidate_reachability_changed",
    "collision_window_availability_changed",
    "target_outside_collision_window",
}
SYSTEM_EVENT_TYPES = {
    "warning_status_changed",
    "source_cap_changed",
    "budget_exceeded_changed",
    "write_failures_changed",
    "input_source_changed",
    "compact_packet_fallback_changed",
    "live_freshness_changed",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_get(value: Any, path: str | list[str], default: Any = None) -> Any:
    parts = path.split(".") if isinstance(path, str) else path
    current = value
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return default
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


def text(value: Any, default: str = "unknown") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def order_substates(values: list[str]) -> list[str]:
    priority = [
        "recent_target_depletion_observed",
        "recent_inventory_change",
        "liveness_assumed",
        "candidate_temporarily_empty",
        "activity_unknown",
        "movement_unknown",
        "interacting_unknown_not_busy",
        "no_explicit_busy_evidence",
    ]
    rank = {value: index for index, value in enumerate(priority)}
    return sorted(dedupe(values), key=lambda value: rank.get(value, len(priority)))


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


def build_context_request(task: str, max_candidates: int = 3, max_events: int = 5) -> dict[str, Any]:
    return {
        "schema": CONTEXT_SCHEMA,
        "task": task,
        "needs": list(DEFAULT_NEEDS),
        "maxCandidates": max(1, int(max_candidates)),
        "maxEvents": max(0, int(max_events)),
        "responseMode": "compact",
    }


def fetch_context(host: str, port: int, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = f"http://{host}:{port}/context"
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("context service returned non-object JSON")
    return value


def post_watch_request(host: str, port: int, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = f"http://{host}:{port}/watch-request"
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("watch request endpoint returned non-object JSON")
    return value


def best_target(response: dict[str, Any], class_id: str = "tree") -> dict[str, Any]:
    candidate = safe_get(response, ["bestCandidates", class_id])
    if not isinstance(candidate, dict):
        candidate = safe_get(response, ["taskSummary", "bestTree"])
    if not isinstance(candidate, dict):
        candidate = safe_get(response, ["candidateSummary", "bestTree"])
    return candidate if isinstance(candidate, dict) else {}


def nearest_target(response: dict[str, Any], class_id: str = "tree") -> dict[str, Any]:
    candidate = safe_get(response, ["nearestCandidates", class_id])
    if not isinstance(candidate, dict):
        candidate = safe_get(response, ["taskSummary", "nearestTree"])
    if not isinstance(candidate, dict):
        candidate = safe_get(response, ["candidateSummary", "nearestTree"])
    return candidate if isinstance(candidate, dict) else {}


def candidate_navigation(candidate: dict[str, Any]) -> dict[str, Any]:
    nav = candidate.get("navigation")
    return nav if isinstance(nav, dict) else {}


def candidate_reachability(candidate: dict[str, Any]) -> str:
    nav = candidate_navigation(candidate)
    return str(nav.get("directReachability") or candidate.get("directReachability") or "unknown")


def candidate_live_state(candidate: dict[str, Any]) -> str:
    return str(candidate.get("targetLiveState") or "unknown")


def candidate_has_aim(candidate: dict[str, Any]) -> bool:
    aim = candidate.get("aimPoint")
    return isinstance(aim, dict) and (aim.get("canvasX") is not None or aim.get("x") is not None) and (aim.get("canvasY") is not None or aim.get("y") is not None)


def candidate_name(candidate: dict[str, Any]) -> str:
    return text(candidate.get("targetName") or candidate.get("name"), "target")


def candidate_location(candidate: dict[str, Any]) -> str:
    world_x = candidate.get("worldX")
    world_y = candidate.get("worldY")
    plane = candidate.get("plane")
    if world_x is None or world_y is None:
        return "unknown location"
    return f"{world_x},{world_y} plane {text(plane)}"


def candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    nav = candidate_navigation(candidate)
    return {
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
        "uiBlocked": candidate.get("uiBlocked"),
        "aimPoint": candidate.get("aimPoint"),
        "targetLiveState": candidate.get("targetLiveState"),
        "livenessInterpretation": candidate.get("livenessInterpretation"),
        "directReachability": nav.get("directReachability") or candidate.get("directReachability"),
        "pathLengthTiles": nav.get("pathLengthTiles"),
        "targetInCollisionWindow": nav.get("targetInCollisionWindow"),
        "reachabilityConfidence": nav.get("reachabilityConfidence"),
        "qualityTier": candidate.get("qualityTier"),
        "qualityScore": candidate.get("qualityScore"),
    }


def inventory_payload(response: dict[str, Any]) -> dict[str, Any]:
    inventory = response.get("inventory")
    if not isinstance(inventory, dict):
        inventory = response.get("inventoryState")
    if not isinstance(inventory, dict):
        inventory = safe_get(response, "taskSummary.inventoryState")
    return inventory if isinstance(inventory, dict) else {}


def activity_payload(response: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    activity = response.get("activity")
    if not isinstance(activity, dict):
        activity = response.get("activityState")
    if not isinstance(activity, dict):
        activity = safe_get(response, "taskSummary.activityState")
    woodcutting = response.get("woodcuttingState")
    if not isinstance(woodcutting, dict):
        woodcutting = safe_get(response, "taskSummary.woodcuttingState")
    return (activity if isinstance(activity, dict) else {}, woodcutting if isinstance(woodcutting, dict) else {})


def navigation_payload(response: dict[str, Any]) -> dict[str, Any]:
    nav = response.get("navigationReadiness")
    if not isinstance(nav, dict):
        nav = safe_get(response, "taskSummary.navigationReadiness")
    return nav if isinstance(nav, dict) else {}


def recent_events(response: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    events = response.get("recentEvents")
    if not isinstance(events, list):
        events = response.get("events")
    if not isinstance(events, list):
        events = safe_get(response, "taskSummary.recentEvents")
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)][-max(0, limit) :]


def freshness_failed(response: dict[str, Any]) -> bool:
    freshness = response.get("freshness")
    if not isinstance(freshness, dict):
        freshness = safe_get(response, "taskSummary.freshness")
    if not isinstance(freshness, dict):
        return False
    return freshness.get("freshByTicks") is False or freshness.get("freshByMillis") is False


def event_mentions(events: list[dict[str, Any]], *tokens: str) -> bool:
    lowered_tokens = tuple(token.lower() for token in tokens)
    for event in events:
        joined = " ".join(str(event.get(key) or "") for key in ("eventType", "summary", "severity")).lower()
        if any(token in joined for token in lowered_tokens):
            return True
    return False


def compact_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "tick": event.get("tick"),
        "eventType": event.get("eventType"),
        "severity": event.get("severity"),
        "summary": event.get("summary"),
    }


def event_group(event: dict[str, Any]) -> str:
    event_type = str(event.get("eventType") or "").lower()
    if event_type in SYSTEM_EVENT_TYPES:
        return "system"
    if event_type in TASK_EVENT_TYPES:
        return "task"
    summary = str(event.get("summary") or "").lower()
    if any(token in summary for token in ("budget", "write failure", "input source", "source cap", "freshness", "warning")):
        return "system"
    return "task"


def split_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    task_events: list[dict[str, Any]] = []
    system_events: list[dict[str, Any]] = []
    for event in events:
        if event_group(event) == "system":
            system_events.append(event)
        else:
            task_events.append(event)
    return task_events, system_events


def recent_target_depletion(events: list[dict[str, Any]]) -> bool:
    return event_mentions(events, "target_depleted", "depleted", "stump", "despawned", "stale")


def current_target_depleted(candidate: dict[str, Any]) -> bool:
    return candidate_live_state(candidate) in LIVE_STATES_UNAVAILABLE


def inventory_summary(response: dict[str, Any]) -> dict[str, Any]:
    inventory = inventory_payload(response)
    return {
        "known": inventory.get("known"),
        "freeSlots": inventory.get("freeSlots"),
        "filledSlots": inventory.get("filledSlots"),
        "inventoryFull": inventory.get("inventoryFull"),
        "changedRecently": inventory.get("changedRecently"),
        "totalItemQuantity": inventory.get("totalItemQuantity", inventory.get("itemCount")),
        "inventorySignature": inventory.get("inventorySignature") or inventory.get("signature"),
    }


def activity_summary(response: dict[str, Any]) -> dict[str, Any]:
    activity, woodcutting = activity_payload(response)
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


def navigation_summary(response: dict[str, Any], best: dict[str, Any]) -> dict[str, Any]:
    nav = navigation_payload(response)
    best_nav = candidate_navigation(best)
    return {
        "status": nav.get("status"),
        "collisionKnown": nav.get("collisionKnown"),
        "collisionWindowAvailable": nav.get("collisionWindowAvailable"),
        "reachabilityComputed": nav.get("reachabilityComputed"),
        "fullCollisionGridAvailable": nav.get("fullCollisionGridAvailable"),
        "directReachability": best_nav.get("directReachability") or best.get("directReachability"),
        "pathLengthTiles": best_nav.get("pathLengthTiles"),
        "targetInCollisionWindow": best_nav.get("targetInCollisionWindow"),
        "missingNavigationFields": best_nav.get("missingNavigationFields") or nav.get("missingCapabilities") or [],
    }


def current_target_state(candidate: dict[str, Any], available: bool) -> dict[str, Any]:
    if not candidate:
        return {"present": False, "summary": "no current target"}
    reachability = candidate_reachability(candidate)
    live_state = candidate_live_state(candidate)
    liveness = candidate.get("livenessInterpretation")
    state = {
        "present": True,
        "available": available,
        "name": candidate_name(candidate),
        "id": candidate.get("id"),
        "location": candidate_location(candidate),
        "distanceTiles": candidate.get("distanceTiles"),
        "reachability": reachability,
        "liveState": live_state,
        "livenessInterpretation": liveness,
        "aimPointAvailable": candidate_has_aim(candidate),
    }
    state["summary"] = (
        f"{candidate_name(candidate)} {text(candidate.get('id'))}, "
        f"{reachability_label(reachability)}, {liveness_label(live_state)}"
    )
    return state


def missing_capabilities(response: dict[str, Any]) -> list[str]:
    values: list[str] = []
    items = response.get("missingCapabilities")
    if isinstance(items, list):
        values.extend(str(item) for item in items if item)
    task = response.get("taskSummary")
    if isinstance(task, dict):
        task_missing = task.get("missingCapabilities")
        if isinstance(task_missing, list):
            values.extend(str(item) for item in task_missing if item)
    return sorted(set(values))


def suggested_watch_requests(response: dict[str, Any]) -> list[dict[str, Any]]:
    suggestions = response.get("suggestedWatchRequests")
    if not isinstance(suggestions, list):
        return []
    return [item for item in suggestions if isinstance(item, dict) and item.get("alias")]


def response_warnings(response: dict[str, Any]) -> list[str]:
    values: list[str] = []
    items = response.get("warnings")
    if isinstance(items, list):
        values.extend(str(item) for item in items if item)
    task = response.get("taskSummary")
    if isinstance(task, dict):
        task_warnings = task.get("warnings")
        if isinstance(task_warnings, list):
            values.extend(str(item) for item in task_warnings if item)
    seen: set[str] = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def target_is_available(candidate: dict[str, Any]) -> bool:
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


def target_is_unreachable(best: dict[str, Any], nearest: dict[str, Any], response: dict[str, Any]) -> bool:
    candidates = [candidate for candidate in (best, nearest) if candidate]
    reachability = safe_get(response, ["reachabilitySummary", "tree"], {})
    if not isinstance(reachability, dict):
        reachability = safe_get(response, "taskSummary.reachabilitySummary", {})
    reachable_count = as_number(reachability.get("reachableCount")) if isinstance(reachability, dict) else None
    if any(candidate_reachability(candidate) == "reachable" for candidate in candidates) or (reachable_count is not None and reachable_count > 0):
        return False
    if candidates and all(candidate_reachability(candidate) == "blocked" for candidate in candidates):
        return True
    for candidate in candidates:
        nav = candidate_navigation(candidate)
        if nav.get("targetInCollisionWindow") is False and reachable_count in (None, 0):
            return True
    return False


def inventory_is_full(summary: dict[str, Any]) -> bool:
    full = as_bool(summary.get("inventoryFull"))
    if full is not None:
        return full
    free_slots = as_number(summary.get("freeSlots"))
    return free_slots == 0 if free_slots is not None else False


def inventory_changed(summary: dict[str, Any], response: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    if as_bool(summary.get("changedRecently")) is True:
        return True
    deltas = response.get("recentInventoryDeltas")
    if isinstance(deltas, list) and deltas:
        return True
    return event_mentions(events, "inventory_changed", "inventory changed", "freeSlots changed")


def activity_busy_analysis(summary: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    apparent = str(summary.get("apparentState") or "").lower()
    woodcutting = str(summary.get("woodcuttingState") or "").lower()
    animation = summary.get("animation")
    interacting = summary.get("interacting")
    evidence = [str(item) for item in summary.get("evidence") or []]
    positive: list[str] = []
    substates: list[str] = []

    explicit_interacting = explicit_interacting_value(interacting)
    if explicit_interacting is not None:
        positive.append("explicit interacting target present")
    elif any("explicit interacting target present" in item.lower() for item in evidence):
        positive.append("explicit interacting target present")
    elif any(item.lower().startswith("interacting=") and "unknown" not in item.lower() for item in evidence):
        positive.append("explicit interacting target present")
    elif apparent == "interacting":
        substates.append("activity_unknown")
        if any("unknown" in item.lower() for item in evidence):
            substates.append("interacting_unknown_not_busy")

    active_animation = active_animation_value(animation)
    if active_animation is not None:
        positive.append("active animation present")
    elif any("active animation present" in item.lower() for item in evidence):
        positive.append("active animation present")
    elif any("local player animation=" in item.lower() and not item.rstrip().endswith(("-1", "0")) for item in evidence):
        positive.append("active animation present")
    elif animation is None:
        substates.append("activity_unknown")
    elif str(animation) in {"-1", "0"}:
        substates.append("no_explicit_busy_evidence")

    if woodcutting in BUSY_WOODCUTTING_STATES:
        positive.append(f"woodcutting state {woodcutting}")

    if apparent == "moving" or summary.get("isMoving") is True:
        substates.append("movement_observed")
    elif summary.get("isMoving") is None:
        substates.append("movement_unknown")

    if not positive:
        substates.append("no_explicit_busy_evidence")
    return bool(positive), positive, dedupe(substates)


def context_failure(task: str, goal_count: int | None, message: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "generatedAtUtc": utc_now(),
        "task": task,
        "goal": {"goalCount": goal_count},
        "contextStatus": "FAIL",
        "phase": "no_context",
        "substate": "no_context",
        "substates": ["no_context"],
        "confidence": 1.0,
        "observations": [],
        "blockingConditions": ["context service unavailable or returned no valid response"],
        "missingCapabilities": ["context_response.v1"],
        "watchableMissingCapabilities": [],
        "warnings": [message],
        "currentTargetState": {"present": False, "summary": "no current target"},
        "recentTaskSignals": [],
        "recentSystemSignals": [],
        "eventPriority": "task",
        "systemEventCount": 0,
        "bestTargetSummary": {},
        "inventorySummary": {},
        "activitySummary": {},
        "navigationSummary": {},
        "recentEvents": [],
        "noActionEmitted": True,
    }


def evaluate_response(
    response: dict[str, Any],
    task: str = "woodcutting",
    goal_count: int | None = None,
    max_events: int = 5,
    event_priority: str = "task",
) -> dict[str, Any]:
    if not isinstance(response, dict) or response.get("schema") != "context_response.v1":
        return context_failure(task, goal_count, "invalid context_response.v1 payload")

    event_priority = "all" if event_priority == "all" else "task"
    best = best_target(response, "tree")
    nearest = nearest_target(response, "tree")
    events = recent_events(response, max_events)
    task_events, system_events = split_events(events)
    inventory = inventory_summary(response)
    activity = activity_summary(response)
    navigation = navigation_summary(response, best)
    best_summary = candidate_summary(best) if best else {}
    context_status = str(response.get("status") or "WARN")
    missing = missing_capabilities(response)
    warnings = [warning for warning in response_warnings(response) if warning not in set(missing)]
    suggested_watches = suggested_watch_requests(response)
    observations: list[str] = []
    blocking: list[str] = []

    player = safe_get(response, "baseline.player")
    if not isinstance(player, dict):
        player = safe_get(response, "taskSummary.player")
    if isinstance(player, dict) and (player.get("worldX") is not None or player.get("sceneX") is not None):
        observations.append(
            f"player observed at {text(player.get('worldX'))},{text(player.get('worldY'))} plane {text(player.get('plane'))}"
        )

    if inventory:
        full_label = "full" if inventory_is_full(inventory) else "not full"
        observations.append(f"inventory observed: {text(inventory.get('freeSlots'))} free slots, {full_label}")
    if best:
        observations.append(
            f"best tree observed: {candidate_name(best)} {text(best.get('id'))}, "
            f"distance {text(best.get('distanceTiles'))}, reachability {candidate_reachability(best)}"
        )
    if task_events:
        observations.append(f"recent task signal observed: {task_events[-1].get('summary') or task_events[-1].get('eventType')}")

    stale = freshness_failed(response)
    full_inventory = inventory_is_full(inventory)
    inv_changed = inventory_changed(inventory, response, task_events)
    recent_depletion = recent_target_depletion(task_events)
    current_depleted = current_target_depleted(best) or current_target_depleted(nearest)
    selected_target = best if best else nearest
    available = target_is_available(best) or target_is_available(nearest)
    unreachable = target_is_unreachable(best, nearest, response)
    busy, busy_evidence, activity_substates = activity_busy_analysis(activity)
    activity["busyEvidence"] = busy_evidence
    activity["trueBusyEvidence"] = busy
    if not busy and str(activity.get("apparentState") or "").lower() in BUSY_ACTIVITY_STATES:
        activity["reportedApparentState"] = activity.get("apparentState")
        activity["apparentState"] = "unknown"
    no_target = not best and not nearest
    reachable_count = as_number(safe_get(response, ["reachabilitySummary", "tree", "reachableCount"], safe_get(response, "taskSummary.reachabilitySummary.reachableCount")))
    suppressed_count = as_number(safe_get(response, "liveness.suppressedCandidateCount", 0)) or 0
    replacement_available = available or (reachable_count is not None and reachable_count > 0)

    substates: list[str] = list(activity_substates)
    if recent_depletion and replacement_available:
        substates.append("recent_target_depletion_observed")
    if inv_changed and not full_inventory:
        substates.append("recent_inventory_change")
    if candidate_live_state(best) == "live_assumed":
        substates.append("liveness_assumed")
    if event_mentions(task_events, "candidate_revived"):
        substates.append("candidate_revived_observed")
    if event_mentions(task_events, "reachability_changed", "reachability changed"):
        substates.append("reachability_changed_observed")
    if no_target and not busy:
        substates.append("candidate_temporarily_empty")
    substates = order_substates(substates)

    phase = "unknown"
    confidence = 0.35
    if stale:
        phase = "stale_context"
        confidence = 0.9
        blocking.append("context freshness failed")
    elif full_inventory:
        phase = "inventory_full"
        confidence = 0.95
        blocking.append("inventory is full")
    elif current_depleted:
        phase = "target_depleted"
        confidence = 0.84
        blocking.append("current target appears depleted/stale")
    elif recent_depletion and not replacement_available and (reachable_count in (None, 0) or suppressed_count > 0):
        phase = "waiting_for_respawn"
        confidence = 0.82
        blocking.append("recent depletion evidence and no reachable replacement observed")
    elif recent_depletion and not replacement_available:
        phase = "target_depleted"
        confidence = 0.84
        blocking.append("recent target depletion observed and no valid replacement is available")
    elif busy:
        phase = "likely_busy"
        confidence = 0.75
    elif unreachable:
        phase = "target_unreachable"
        confidence = 0.8
        blocking.append("best or nearest target reachability is blocked/unknown outside the local window")
    elif available:
        phase = "target_available"
        confidence = 0.86
    elif no_target:
        phase = "no_target_observed"
        confidence = 0.85
        blocking.append("no tree target was returned by context service")
    elif not busy and player:
        phase = "likely_idle"
        confidence = 0.65
    else:
        blocking.append("insufficient or conflicting observations")

    if context_status == "FAIL" and phase != "stale_context":
        blocking.append("context status is FAIL")
        confidence = min(confidence, 0.5)
    if phase in {"target_available", "likely_idle", "no_target_observed"} and any(
        substate in substates for substate in ("activity_unknown", "interacting_unknown_not_busy", "movement_unknown")
    ):
        confidence = min(confidence, 0.72)

    current_state = current_target_state(selected_target, available)
    task_signal_items = [compact_event(event) for event in task_events]
    system_signal_items = [compact_event(event) for event in system_events] if event_priority == "all" else []

    return {
        "schema": SCHEMA,
        "generatedAtUtc": utc_now(),
        "task": task,
        "goal": {"goalCount": goal_count},
        "contextStatus": context_status,
        "phase": phase,
        "substate": substates[0] if substates else None,
        "substates": substates,
        "confidence": round(confidence, 2),
        "observations": observations,
        "blockingConditions": blocking,
        "missingCapabilities": sorted(set(str(item) for item in missing if item)),
        "watchableMissingCapabilities": [
            {
                "alias": item.get("alias"),
                "type": item.get("type"),
                "id": item.get("id"),
                "sampleMode": item.get("sampleMode"),
                "ttlTicks": item.get("ttlTicks"),
            }
            for item in suggested_watches
        ],
        "warnings": warnings,
        "currentTargetState": current_state,
        "recentTaskSignals": task_signal_items,
        "recentSystemSignals": system_signal_items,
        "eventPriority": event_priority,
        "systemEventCount": len(system_events),
        "bestTargetSummary": best_summary,
        "inventorySummary": inventory,
        "activitySummary": activity,
        "navigationSummary": navigation,
        "recentEvents": task_signal_items + system_signal_items,
        "noActionEmitted": True,
    }


def reachability_label(value: Any) -> str:
    return {
        "reachable": "reachable",
        "blocked": "blocked",
        "unknown": "unknown",
        None: "unknown",
    }.get(value, str(value))


def liveness_label(value: Any) -> str:
    return {
        "live": "live",
        "live_assumed": "assumed live",
        "depleted_or_stump": "depleted/stump",
        "recently_despawned": "recently disappeared",
        "stale": "stale",
        "unknown": "unknown",
        None: "unknown",
    }.get(value, str(value))


def aim_label(candidate: dict[str, Any]) -> str:
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


def format_human(result: dict[str, Any]) -> str:
    task = str(result.get("task") or "task").upper()
    status = result.get("contextStatus") or "WARN"
    best = result.get("bestTargetSummary") if isinstance(result.get("bestTargetSummary"), dict) else {}
    inventory = result.get("inventorySummary") if isinstance(result.get("inventorySummary"), dict) else {}
    activity = result.get("activitySummary") if isinstance(result.get("activitySummary"), dict) else {}
    navigation = result.get("navigationSummary") if isinstance(result.get("navigationSummary"), dict) else {}
    current_target = result.get("currentTargetState") if isinstance(result.get("currentTargetState"), dict) else {}
    task_signals = result.get("recentTaskSignals") if isinstance(result.get("recentTaskSignals"), list) else []
    system_signals = result.get("recentSystemSignals") if isinstance(result.get("recentSystemSignals"), list) else []
    system_count = int(result.get("systemEventCount") or 0)

    lines = [
        f"{task} REHEARSAL - {status}",
        "",
        f"Phase: {text(result.get('phase'))}",
        f"Substate: {', '.join(result.get('substates') or []) if result.get('substates') else 'none'}",
        f"Confidence: {float(result.get('confidence') or 0.0):.2f}",
        "",
        "Current target:",
        f"  {text(current_target.get('summary'), 'none')}",
        "",
        "Current state:",
        f"  Activity: {text(activity.get('apparentState'))} / {', '.join(activity.get('busyEvidence') or ['no explicit busy evidence'])}",
        f"  Woodcutting state: {text(activity.get('woodcuttingState'))}",
        f"  Inventory: {text(inventory.get('freeSlots'))} free slots, {'full' if inventory_is_full(inventory) else 'not full'}",
        "",
        "Observed:",
    ]
    if result.get("observations"):
        for observation in result["observations"]:
            lines.append(f"  {observation}")
    else:
        lines.append("  no usable context observed")

    if best:
        lines.extend(
            [
                f"  Best tree: {text(best.get('name'))} {text(best.get('id'))}, distance {text(best.get('distanceTiles'))}, "
                f"{reachability_label(best.get('directReachability'))}, aim {aim_label(best)}",
                f"  Liveness: {liveness_label(best.get('targetLiveState'))}",
                f"  Reachability: {reachability_label(navigation.get('directReachability'))}, path length {text(navigation.get('pathLengthTiles'))}",
            ]
        )
    lines.append("")
    if task_signals:
        lines.append("Recent task signals:")
        for event in task_signals[:5]:
            lines.append(f"  [tick {text(event.get('tick'))}] {event.get('summary') or event.get('eventType')}")
    else:
        lines.extend(["Recent task signals:", "  none"])
    lines.append("")
    if system_signals:
        lines.append("Recent system signals:")
        for event in system_signals[:5]:
            lines.append(f"  [tick {text(event.get('tick'))}] {event.get('summary') or event.get('eventType')}")
    elif system_count:
        lines.extend(["Recent system signals:", f"  {system_count} hidden; use --show-system-events to display them"])

    lines.extend(["", "Blocking conditions:"])
    for item in result.get("blockingConditions") or ["none"]:
        lines.append(f"  {item}")

    lines.append("")
    lines.append("Missing capabilities:")
    missing = result.get("missingCapabilities") or []
    if missing:
        for item in missing:
            lines.append(f"  {item}")
    else:
        lines.append("  none")

    watchable_missing = result.get("watchableMissingCapabilities") or []
    if watchable_missing:
        lines.append("")
        lines.append("Watchable missing fields:")
        for item in watchable_missing[:5]:
            lines.append(f"  {text(item.get('alias'))}: {text(item.get('id'))} ({text(item.get('type'))})")

    warnings = result.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in warnings[:10]:
            lines.append(f"  {warning}")
        if len(warnings) > 10:
            lines.append(f"  ... {len(warnings) - 10} more")

    lines.extend(["", "No action emitted."])
    return "\n".join(lines).rstrip() + "\n"


def format_watch_line(result: dict[str, Any]) -> str:
    best = result.get("bestTargetSummary") if isinstance(result.get("bestTargetSummary"), dict) else {}
    inventory = result.get("inventorySummary") if isinstance(result.get("inventorySummary"), dict) else {}
    signals = result.get("recentTaskSignals") if isinstance(result.get("recentTaskSignals"), list) else []
    event = signals[-1] if signals else {}
    return (
        f"phase={text(result.get('phase'))} substate={text(result.get('substate'), 'none')} status={text(result.get('contextStatus'))} "
        f"best={text(best.get('name'))} {text(best.get('id'))} reach={text(best.get('directReachability'))} "
        f"freeSlots={text(inventory.get('freeSlots'))} latestTaskSignal={text(event.get('summary'), 'none')}"
    )


def rehearsal_once(args: argparse.Namespace) -> dict[str, Any]:
    request = build_context_request(args.task, args.max_candidates, args.max_events)
    try:
        context = fetch_context(args.host, args.port, request, args.timeout)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
        return context_failure(args.task, args.goal_count, str(exc))
    event_priority = "all" if args.show_system_events or args.event_priority == "all" else "task"
    result = evaluate_response(context, task=args.task, goal_count=args.goal_count, max_events=args.max_events, event_priority=event_priority)
    if args.request_missing_watches and result.get("watchableMissingCapabilities"):
        watch_request = {
            "schema": "context_watch_request.v1",
            "requestId": f"mock-brain-{int(time.time() * 1000)}",
            "task": args.task,
            "watches": result["watchableMissingCapabilities"],
        }
        try:
            result["watchRequest"] = post_watch_request(args.host, args.port, watch_request, args.timeout)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
            result.setdefault("warnings", []).append(f"watch request failed: {exc}")
    return result


def print_result(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=False))
    else:
        print(format_human(result), end="")


def run_watch(args: argparse.Namespace) -> int:
    previous_phase: str | None = None
    try:
        while True:
            result = rehearsal_once(args)
            if not args.json:
                os.system("cls" if os.name == "nt" else "clear")
                print(f"Updated: {utc_now()}")
                if previous_phase and previous_phase != result.get("phase"):
                    print(f"Phase changed: {previous_phase} -> {result.get('phase')}")
                    print("")
                print(format_human(result), end="")
                print("")
                print(format_watch_line(result))
            else:
                os.system("cls" if os.name == "nt" else "clear")
                print(json.dumps(result, indent=2, sort_keys=False))
            previous_phase = str(result.get("phase") or "")
            time.sleep(max(0.1, float(args.interval)))
    except KeyboardInterrupt:
        return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only mock brain rehearsal over context_response.v1.")
    parser.add_argument("--host", default="127.0.0.1", help="Context service host.")
    parser.add_argument("--port", type=int, default=8890, help="Context service port.")
    parser.add_argument("--task", default="woodcutting", help="Task to rehearse.")
    parser.add_argument("--goal-count", type=int, default=None, help="Optional target count for the rehearsal goal.")
    parser.add_argument("--watch", action="store_true", help="Refresh the rehearsal view until Ctrl+C.")
    parser.add_argument("--interval", type=float, default=1.0, help="Watch refresh interval in seconds.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    parser.add_argument("--human", action="store_true", help="Print human-readable output. This is the default unless --json is used.")
    parser.add_argument("--max-events", type=int, default=5, help="Maximum recent events to request and show.")
    parser.add_argument("--max-candidates", type=int, default=3, help="Maximum candidates to request from the context service.")
    parser.add_argument("--timeout", type=float, default=3.0, help="Context service request timeout in seconds.")
    parser.add_argument("--show-system-events", action="store_true", help="Show recent system/health events in addition to task events.")
    parser.add_argument("--event-priority", choices=("task", "all"), default="task", help="Which event groups to include in output.")
    parser.add_argument("--request-missing-watches", action="store_true", help="Request suggested bounded read-only watches for missing watchable fields.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.watch:
        return run_watch(args)
    result = rehearsal_once(args)
    print_result(result, json_output=args.json and not args.human)
    return 0 if result.get("contextStatus") != "FAIL" or result.get("phase") != "no_context" else 1


if __name__ == "__main__":
    raise SystemExit(main())

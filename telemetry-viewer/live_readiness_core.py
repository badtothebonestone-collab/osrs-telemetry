from __future__ import annotations

import urllib.error
from pathlib import Path
from typing import Any, Callable

from candidate_core import (
    aim_point,
    build_report as build_candidate_report,
    selected_target_from_status,
    target_matches,
    target_summary,
)
from live_file_core import live_dir, overlay_targets, path_text, read_json
from live_session_core import (
    daemon_session_from_status,
    daemon_status_url,
    fetch_json,
    same_path,
)
from telemetry_paths import find_newest_live_session, find_newest_session, get_sessions_dir


SCHEMA = "live_readiness.v2"
RESOURCE_TARGET_ACTIONS = {"select_resource_target"}
RESOURCE_RECOVERY_ACTIONS = {"resource_view_recovery"}
NAVIGATION_ACTIONS = {"navigate_to_service", "return_to_resource_area"}
SERVICE_OBJECT_ACTIONS = {"open_service", "deposit_inventory", "deposit_resources", "close_bank"}
ROUTE_TRANSITION_ACTIONS = {"interact_service_route_object"}
INTERFACE_DIALOGUE_ACTIONS = {"interface_dialogue_choice"}
CLIENT_TICK_HOT_MAX_AGE_MILLIS = 1000
NO_ACTIVE_TARGET_PHASES = {
    "goal_complete",
    "none",
    "observe",
    "no_context",
    "stale_context",
    "needs_more_context",
}
WAITING_PHASES = {"wait_for_result", "waiting_for_result", "wait_for_resource_result"}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _latest_tick(status: dict[str, Any]) -> int | None:
    if isinstance(status.get("latestTick"), int):
        return status["latestTick"]
    brain = _dict(status.get("brain"))
    return brain.get("latestTick") if isinstance(brain.get("latestTick"), int) else None


def _target_check_value(selected: dict[str, Any], matched: dict[str, Any], key: str) -> Any:
    if matched.get(key) is not None:
        return matched.get(key)
    return selected.get(key)


def _has_aim_point(target: dict[str, Any]) -> bool:
    return bool(aim_point(target))


def _blocker(code: str, message: str, *, action: str | None = None, **extra: Any) -> dict[str, Any]:
    payload = {"code": code, "message": message}
    if action:
        payload["action"] = action
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _int_or_default(value: Any, default: int) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _plugin_snapshot_source(status: dict[str, Any]) -> dict[str, Any]:
    host = str(status.get("pluginSnapshotHost") or "127.0.0.1")
    port = _int_or_default(status.get("pluginSnapshotPort"), 8893)
    base = f"http://{host}:{port}"
    return {
        "host": host,
        "port": port,
        "healthUrl": base + "/health",
        "schemaUrl": base + "/schema",
        "url": base + "/snapshot",
        "snapshotUrl": base + "/snapshot",
    }


def _daemon_live_fresh(status: dict[str, Any]) -> bool:
    freshness = _dict(
        status.get("liveProcessorFreshness")
        or status.get("liveFreshness")
        or status.get("daemonFreshness")
    )
    if not freshness:
        return _latest_tick(status) is not None and status.get("liveProcessorFresh") is True
    fresh_by_ticks = freshness.get("freshByTicks")
    fresh_by_millis = freshness.get("freshByMillis")
    if fresh_by_ticks is None and fresh_by_millis is None:
        return _latest_tick(status) is not None
    return bool(fresh_by_ticks is not False and fresh_by_millis is not False)


def _snapshot_failure_warnings(status: dict[str, Any]) -> list[str]:
    failures = []
    for warning in [str(item) for item in status.get("warnings") or [] if item is not None]:
        lowered = warning.lower()
        if (
            "plugin snapshot request failed" in lowered
            or "snapshot endpoint unreachable" in lowered
            or ("plugin snapshot endpoint" in lowered and "failed" in lowered)
        ):
            failures.append(warning)
    return failures


def _current_snapshot_failures(status: dict[str, Any], failures: list[str]) -> tuple[list[str], list[str]]:
    if not failures:
        return [], []
    if status.get("pluginSnapshotAvailable") is False:
        return failures, []
    if _latest_tick(status) is None or not _daemon_live_fresh(status):
        return failures, []
    # Daemon /status can retain an old snapshot timeout warning after the live
    # in-memory stream has moved on. Do not let that historical warning override
    # fresh daemon truth in Snapshot No-File mode.
    return [], failures


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _game_state_from_hot(status: dict[str, Any], hot: dict[str, Any], hover: dict[str, Any]) -> str | None:
    brain = _dict(status.get("brain"))
    baseline = _dict(status.get("baseline") or brain.get("baseline"))
    for value in (
        status.get("gameState"),
        status.get("pluginGameState"),
        hot.get("gameState"),
        hover.get("gameState"),
        _dict(baseline.get("client")).get("gameState"),
        baseline.get("gameState"),
    ):
        text = _text(value)
        if text:
            return text
    return None


def _is_logged_in_game_state(game_state: str | None) -> bool | None:
    if not game_state:
        return None
    normalized = game_state.strip().upper()
    if normalized == "LOGGED_IN":
        return True
    if normalized in {
        "LOGIN_SCREEN",
        "LOGIN_SCREEN_AUTHENTICATOR",
        "LOGGING_IN",
        "HOPPING",
        "CONNECTION_LOST",
        "STARTING",
        "UNKNOWN",
    }:
        return False
    if "LOGIN" in normalized or "LOGGED_OUT" in normalized:
        return False
    return None


def _hot_stale_reason(
    status: dict[str, Any],
    *,
    available: bool,
    fresh_by_age: bool,
    game_state: str | None,
    is_logged_in: bool | None,
) -> str | None:
    warnings = " ".join(str(item).lower() for item in status.get("warnings") or [])
    if not available:
        if "endpoint unreachable" in warnings or "could not connect" in warnings:
            return "plugin_endpoint_not_reachable"
        if "no cached" in warnings or "no packets" in warnings:
            return "plugin_snapshot_no_packets"
        return "plugin_snapshot_no_packets"
    normalized_state = (game_state or "").strip().upper()
    if normalized_state == "LOGIN_SCREEN":
        return "login_screen"
    if is_logged_in is False:
        return "game_not_logged_in"
    if fresh_by_age:
        return None
    if is_logged_in is True:
        return "plugin_hot_state_not_advancing"
    if game_state:
        return "auto_logged_out_or_inactive"
    return "unknown"


def _hot_recovery_action(stale_reason: str | None) -> str:
    if stale_reason in {"login_screen", "game_not_logged_in", "auto_logged_out_or_inactive"}:
        return "run ensure_loaded_scene/bootstrap recovery, then restart/rebind daemon if needed"
    if stale_reason == "plugin_endpoint_not_reachable":
        return "run ensure_loaded_scene to recover RuneLite/PluginSnapshotEndpoint, then restart/rebind daemon if needed"
    if stale_reason == "plugin_snapshot_no_packets":
        return "run ensure_loaded_scene once, or wait for fresh plugin snapshot/client tick data after login"
    if stale_reason == "plugin_hot_state_not_advancing":
        return "run ensure_loaded_scene once to refocus/recover, then restart daemon if client ticks do not advance"
    if stale_reason == "daemon_snapshot_not_refreshing":
        return "restart/rebind the daemon to the current plugin session"
    if stale_reason == "game_state_changed_only":
        return "wait for fresh ClientTick/PostMenuSort hot-state evidence after the game-state transition"
    return "run ensure_loaded_scene once or wait for fresh client tick/menu samples"


def _intent_for_action(action: str | None, target_kind: str | None = None) -> str:
    action = str(action or "")
    target_kind = str(target_kind or "")
    if action in RESOURCE_TARGET_ACTIONS:
        return "resource_object_action"
    if action in RESOURCE_RECOVERY_ACTIONS:
        return "resource_view_recovery_action"
    if action in NAVIGATION_ACTIONS or target_kind == "path_tile":
        return "navigation_waypoint_action"
    if action in ROUTE_TRANSITION_ACTIONS:
        return "route_transition_action"
    if action in INTERFACE_DIALOGUE_ACTIONS:
        return "interface_dialogue_choice_action"
    if action in SERVICE_OBJECT_ACTIONS:
        return "service_object_action"
    if action == "camera_adjustment":
        return "camera_adjustment_action"
    return "unknown"


def _client_tick_hot_state(status: dict[str, Any]) -> dict[str, Any]:
    hot = _dict(status.get("clientTickHot"))
    latency = _dict(hot.get("latency"))
    hover = _dict(hot.get("postMenuSort")) or _dict(hot.get("hoverMenu"))
    age_millis = _int_or_none(latency.get("ageMillis"))
    latest_post_menu_age = _int_or_none(latency.get("postMenuSortAgeMillis"))
    if age_millis is None:
        age_millis = latest_post_menu_age
    last_click_age = _int_or_none(latency.get("lastClickAgeMillis"))
    snapshot_age = _int_or_none(status.get("pluginSnapshotAgeMillis") or status.get("snapshotAgeMillis"))
    daemon_tick_age = _int_or_none(status.get("daemonLatestTickAgeMillis") or status.get("latestTickAgeMillis"))
    game_state = _game_state_from_hot(status, hot, hover)
    is_logged_in = _is_logged_in_game_state(game_state)
    source_event = _text(hot.get("sourceEvent") or hot.get("sampleSource"))
    source_key = source_event.lower()
    game_state_changed_only = source_key == "gamestatechanged"
    explicit_available = status.get("clientTickHotAvailable")
    explicit_fresh = status.get("clientTickHotFresh")
    available = bool((bool(hot) and bool(hover)) or explicit_available is True or explicit_fresh is True)
    liveness_game_state_fresh = bool(
        bool(hot)
        and age_millis is not None
        and age_millis <= CLIENT_TICK_HOT_MAX_AGE_MILLIS
        and game_state
    )
    fresh_by_age = bool(
        available
        and (
            (explicit_fresh is True and not game_state_changed_only)
            or (age_millis is not None and age_millis <= CLIENT_TICK_HOT_MAX_AGE_MILLIS)
        )
    )
    action_hot_fresh = bool(fresh_by_age and not game_state_changed_only)
    stale_reason = _hot_stale_reason(
        status,
        available=available,
        fresh_by_age=action_hot_fresh,
        game_state=game_state,
        is_logged_in=is_logged_in,
    )
    if game_state_changed_only and is_logged_in is True:
        stale_reason = "game_state_changed_only"
    fresh = bool(action_hot_fresh and stale_reason is None)
    return {
        "available": available,
        "fresh": fresh,
        "clientTickHotFresh": fresh,
        "livenessGameStateFresh": liveness_game_state_fresh,
        "actionHotFresh": action_hot_fresh,
        "clientTickHotUsableForAction": bool(not game_state_changed_only and available),
        "clientTickHotSource": source_event or None,
        "sourceEvent": source_event or None,
        "sampleSource": source_event or None,
        "staleReason": stale_reason,
        "clientTickHotStaleReason": stale_reason,
        "recovery": _hot_recovery_action(stale_reason) if stale_reason else None,
        "gameState": game_state,
        "isLoggedIn": is_logged_in,
        "ageMillis": age_millis,
        "maxAgeMillis": CLIENT_TICK_HOT_MAX_AGE_MILLIS,
        "latestPostMenuSortAgeMs": latest_post_menu_age,
        "latestPostMenuSortAgeMillis": latest_post_menu_age,
        "lastMenuOptionClickedAgeMs": last_click_age,
        "lastMenuOptionClickedAgeMillis": last_click_age,
        "snapshotAgeMs": snapshot_age,
        "snapshotAgeMillis": snapshot_age,
        "daemonLatestTickAgeMs": daemon_tick_age,
        "daemonLatestTickAgeMillis": daemon_tick_age,
        "clientTick": hot.get("clientTick"),
        "gameTickAtSample": hot.get("gameTickAtSample"),
        "postMenuSortPresent": bool(hover),
        "topOption": hover.get("topOption") or hover.get("option"),
        "topTarget": hover.get("topTarget") or hover.get("target"),
    }


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict) and value:
            return value
    return {}


def _nested_dict(root: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = root
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _first_value(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _resource_count_from_status(status: dict[str, Any], brain: dict[str, Any]) -> int | None:
    progress = _first_dict(
        brain.get("goalProgress"),
        _nested_dict(brain, "genericTaskState", "goalProgress"),
        status.get("goalProgress"),
    )
    value = _first_value(
        progress.get("heldResourceCount"),
        progress.get("currentHeldCount"),
        status.get("resourceCount"),
        status.get("inventoryMatchingResourceCount"),
        status.get("bankResourceItemsHeld"),
    )
    return _int_or_none(value)


def _banking_complete(bank_operation: dict[str, Any]) -> bool:
    resource_items_held = _int_or_none(bank_operation.get("resourceItemsHeld"))
    resource_item_quantity = _int_or_none(bank_operation.get("resourceItemQuantity"))
    if (resource_items_held is not None and resource_items_held > 0) or (
        resource_item_quantity is not None and resource_item_quantity > 0
    ):
        return False
    if bank_operation.get("bankingComplete") is True:
        return True
    if bank_operation.get("operationNeeded") is False and resource_items_held == 0:
        return True
    return False


def _service_action_context_ready(status: dict[str, Any], service: dict[str, Any]) -> bool:
    if _first_value(service.get("serviceReady"), status.get("serviceReady")) is True:
        return True
    route_context = _first_dict(service.get("serviceRouteContext"), status.get("serviceRouteContext"))
    route_action_ready = _first_value(route_context.get("actionReady"), status.get("serviceRouteActionReady")) is True
    if not route_action_ready:
        return False
    if _first_dict(route_context.get("visibleServiceTarget"), route_context.get("selectedServiceObject"), route_context.get("visibleInteractionTarget")):
        return True
    return _first_value(
        route_context.get("serviceObjectInterceptReady"),
        status.get("serviceObjectInterceptReady"),
        status.get("serviceRouteObjectInterceptReady"),
    ) is True


def _action_need_state(
    status: dict[str, Any],
    *,
    action: str | None,
    current_intent: str,
    proposal_payload: dict[str, Any],
) -> dict[str, Any]:
    brain = _dict(status.get("brain"))
    generic = _dict(brain.get("genericTaskState"))
    inventory = _first_dict(brain.get("inventoryContext"), _nested_dict(brain, "currentContextSummary", "inventory"))
    service = _dict(brain.get("serviceContext"))
    bank_operation = _dict(brain.get("bankOperationContext"))
    phase = _text(_first_value(generic.get("phase"), status.get("phase"), status.get("brainPhase"))).lower()
    active_intent = _text(_first_value(generic.get("activeIntent"), status.get("activeIntent"))).lower()
    cycle_stage = _first_value(
        status.get("cycleStage"),
        status.get("finalCycleStage"),
        generic.get("cycleStage"),
        brain.get("cycleStage"),
    )
    free_slots = _int_or_none(_first_value(inventory.get("freeSlots"), status.get("inventoryFreeSlots")))
    inventory_full = _first_value(inventory.get("inventoryFull"), status.get("inventoryFull"))
    service_context_required = bool(
        service.get("serviceNeeded") is True
        or service.get("serviceRequired") is True
        or status.get("serviceNeeded") is True
    )
    resource_count = _resource_count_from_status(status, brain)
    service_needed_at_current_target = bool(
        service_context_required
        and resource_count is not None
        and resource_count > 0
        and not _banking_complete(bank_operation)
        and _service_action_context_ready(status, service)
    )
    needs_service = bool(
        phase in {"needs_service", "route_to_service", "pathing_to_service", "inventory_full"}
        or active_intent in {"needs_service", "route_to_service", "pathing_to_service", "inventory_full"}
        or inventory_full is True
        or free_slots == 0
        or service_needed_at_current_target
    )
    waiting_for_result = bool(phase in WAITING_PHASES or active_intent in WAITING_PHASES)
    progress = _first_dict(brain.get("goalProgress"), status.get("goalProgress"))
    goal_count = _int_or_none(progress.get("goalCount"))
    displayed_goal = _int_or_none(progress.get("displayedGoalProgress"))
    goal_complete = bool(
        phase == "goal_complete"
        or active_intent == "goal_complete"
        or progress.get("goalComplete") is True
        or (goal_count is not None and displayed_goal is not None and goal_count >= 0 and displayed_goal >= goal_count)
    )
    active_target = _dict(generic.get("activeIntentTarget"))
    no_active_target = bool(
        not active_target
        and (
            goal_complete
            or waiting_for_result
            or phase in NO_ACTIVE_TARGET_PHASES
            or active_intent in NO_ACTIVE_TARGET_PHASES
            or action in {"none", "wait_for_context", "wait_for_resource_result"}
        )
    )
    action_readiness_needed = bool(
        action not in {"none", "wait_for_context", "wait_for_resource_result"}
        and not goal_complete
        and not waiting_for_result
    )
    needs_next_target = bool(
        action_readiness_needed
        and current_intent == "resource_object_action"
        and not needs_service
        and free_slots != 0
    )
    return {
        "schema": "action_need.v1",
        "currentIntent": current_intent,
        "proposedAction": action,
        "cycleStage": cycle_stage,
        "phase": phase or None,
        "activeIntent": active_intent or None,
        "inventoryFreeSlots": free_slots,
        "resourceCount": resource_count,
        "needsNextTarget": needs_next_target,
        "needsService": needs_service,
        "serviceContextRequired": service_context_required,
        "waitingForResult": waiting_for_result,
        "goalComplete": goal_complete,
        "noActiveTarget": no_active_target,
        "actionReadinessNeeded": action_readiness_needed,
        "proposalExecutable": bool(proposal_payload.get("executable")),
        "bankingComplete": bank_operation.get("bankingComplete"),
    }


def _route_context_applicability(
    status: dict[str, Any],
    *,
    action_need: dict[str, Any],
    current_intent: str,
    action: str | None,
    selected_actionable: bool,
) -> dict[str, Any]:
    brain = _dict(status.get("brain"))
    service_route = _dict(brain.get("serviceRouteContext") or status.get("serviceRouteContext"))
    pathing = _dict(brain.get("pathingContext") or status.get("pathingContext"))
    route_context_present = bool(
        service_route
        or status.get("serviceRouteId")
        or status.get("serviceRouteCurrentNodeId")
        or status.get("serviceRouteNextEdgeType")
        or status.get("routeContext")
    )
    cycle_stage = str(action_need.get("cycleStage") or "").lower()
    phase = str(action_need.get("phase") or "").lower()
    active_intent = str(action_need.get("activeIntent") or "").lower()
    inventory_full = bool(
        _first_value(status.get("inventoryFull"), _dict(brain.get("inventoryContext")).get("inventoryFull")) is True
        or action_need.get("inventoryFreeSlots") == 0
    )
    needs_service = action_need.get("needsService") is True
    route_intents = {
        "navigation_waypoint_action",
        "route_transition_action",
        "service_object_action",
        "interface_dialogue_choice_action",
    }
    route_phases = {
        "needs_service",
        "route_to_service",
        "pathing_to_service",
        "inventory_full",
        "service",
        "return_to_resource",
        "return_to_resource_area",
    }
    active_route_action = bool(
        current_intent in route_intents
        or action in NAVIGATION_ACTIONS
        or action in SERVICE_OBJECT_ACTIONS
        or action in ROUTE_TRANSITION_ACTIONS
        or action in INTERFACE_DIALOGUE_ACTIONS
    )
    route_phase_active = bool(cycle_stage in route_phases or phase in route_phases or active_intent in route_phases)
    collecting_safe_resource = bool(
        route_context_present
        and not needs_service
        and not inventory_full
        and current_intent == "resource_object_action"
        and action == "select_resource_target"
        and selected_actionable
    )
    applicable = bool(route_context_present and (needs_service or inventory_full or active_route_action or route_phase_active))
    if collecting_safe_resource:
        applicable = False
        reason = "collecting_resources_resource_target_ready"
    elif not route_context_present:
        reason = "route_context_absent"
    elif applicable:
        reason = (
            "route_intent_active"
            if active_route_action
            else "service_needed"
            if needs_service
            else "inventory_full"
            if inventory_full
            else "route_phase_active"
        )
    else:
        reason = "route_context_not_required_for_current_intent"
    age = _first_value(
        status.get("routeContextAgeMs"),
        status.get("serviceRouteContextAgeMs"),
        service_route.get("ageMs"),
        pathing.get("ageMs"),
    )
    return {
        "routeContextPresent": route_context_present,
        "routeContextApplicable": applicable,
        "routeContextApplicabilityReason": reason,
        "routeContextWarningOnly": bool(route_context_present and not applicable),
        "staleRouteContextSuppressed": bool(route_context_present and not applicable),
        "routeContextSource": "daemon_status" if route_context_present else None,
        "routeContextAgeMs": age,
    }


def _safe_aimpoint_status(*targets: dict[str, Any]) -> str | None:
    for target in targets:
        safe = _dict(target.get("safeAimPoint"))
        if safe.get("status"):
            return str(safe.get("status"))
    return None


def _action_safety_evidence(
    *,
    proposal: Any,
    proposal_payload: dict[str, Any],
    proposal_target: dict[str, Any],
    selected_target: dict[str, Any],
    matched_target: dict[str, Any],
    client_tick_hot: dict[str, Any],
    freshness: dict[str, Any],
    proposal_action_target_source: str,
    proposal_actionability: str,
) -> dict[str, Any]:
    safe_status = _safe_aimpoint_status(proposal_target, selected_target, matched_target)
    source = proposal_action_target_source or ""
    live_target_source = source in {
        "live_resource_candidate",
        "live_projected_waypoint",
        "live_route_object",
        "live_service_object",
        "hover_discovered_object",
    }
    safe_aimpoint_ready = safe_status == "PASS" or bool(proposal_payload.get("suggestedClickPoint"))
    return {
        "schema": "action_safety_evidence.v1",
        "proposalExecutable": bool(getattr(proposal, "executable", False)),
        "proposalActionTargetSource": source or None,
        "proposalActionability": proposal_actionability or None,
        "selectedTargetFresh": freshness.get("stale") is not True,
        "safeAimPointStatus": safe_status,
        "safeAimPointReady": safe_aimpoint_ready,
        "clientTickHotFresh": bool(client_tick_hot.get("fresh")),
        "hoverConfirmationRequired": proposal_actionability == "needs_hover_confirmation",
        "hoverConfirmationDeferredToExecutor": proposal_actionability == "needs_hover_confirmation",
        "liveTargetSource": live_target_source,
        "canUseLiveTargetWithoutOverlayMarker": bool(
            getattr(proposal, "executable", False)
            and live_target_source
            and safe_aimpoint_ready
            and freshness.get("stale") is not True
        ),
    }


def _overlay_health_state(
    *,
    marker_count: int,
    overlay_exists: bool,
    overlay_age_seconds: Any,
    resource_target_required: bool,
    matched_target: dict[str, Any],
    action_need: dict[str, Any],
    action_safety_evidence: dict[str, Any],
) -> dict[str, Any]:
    marker_source_required = bool(
        resource_target_required
        and action_need.get("actionReadinessNeeded") is True
        and not action_safety_evidence.get("canUseLiveTargetWithoutOverlayMarker")
    )
    marker_count_expected = bool(resource_target_required and action_need.get("actionReadinessNeeded") is True)
    if marker_count > 0:
        zero_status = "markers_present"
    elif action_need.get("goalComplete") is True:
        zero_status = "expected_goal_complete"
        marker_count_expected = False
        marker_source_required = False
    elif action_need.get("waitingForResult") is True:
        zero_status = "expected_waiting_for_result"
        marker_count_expected = False
        marker_source_required = False
    elif not action_need.get("actionReadinessNeeded") or not resource_target_required:
        zero_status = "expected_no_active_target"
        marker_count_expected = False
        marker_source_required = False
    elif not overlay_exists:
        zero_status = "unexpected_source_stale"
    else:
        zero_status = "unexpected_collecting_needs_target"
    overlay_blocks = bool(marker_count == 0 and marker_source_required)
    if marker_count > 0 and resource_target_required and marker_source_required and not matched_target:
        overlay_blocks = True
    warning_only = bool((marker_count == 0 or (marker_count > 0 and not matched_target)) and not overlay_blocks)
    recovery_attempted = bool(
        marker_count == 0
        and resource_target_required
        and action_safety_evidence.get("canUseLiveTargetWithoutOverlayMarker")
    )
    recovery_result = "fallback_from_action_proposal" if recovery_attempted else "not_attempted"
    return {
        "schema": "overlay_health.v1",
        "markerCount": marker_count,
        "markerCountExpected": marker_count_expected,
        "markerCountExpectedReason": (
            "current action depends on overlay marker source"
            if marker_source_required
            else "lifecycle or live target safety evidence does not require overlay markers"
        ),
        "markerCountZeroStatus": zero_status,
        "overlayBlocksCurrentAction": overlay_blocks,
        "overlayWarningOnly": warning_only,
        "overlaySourceRequiredForCurrentAction": marker_source_required,
        "overlayExists": overlay_exists,
        "overlayAgeSeconds": overlay_age_seconds,
        "overlayRecoveryAttempted": recovery_attempted,
        "overlayRecoveryResult": recovery_result,
        "recoveredMarkerCount": 1 if recovery_attempted else 0,
        "overlayFallbackSource": "action_proposal" if recovery_attempted else None,
        "overlayNonBlockingReason": (
            "fresh live target evidence with safe aim point is available; hover/menu confirmation remains required"
            if recovery_attempted
            else None
        ),
    }


def _status_from_parts(*, blockers: list[Any] | None = None, warnings: list[Any] | None = None) -> str:
    if blockers:
        return "FAIL"
    if warnings:
        return "WARN"
    return "PASS"


def _status_payload_unavailable(error: Exception, *, daemon_url: str = "http://127.0.0.1:8890") -> dict[str, Any]:
    message = f"daemon status unavailable: {type(error).__name__}: {error}"
    blockers = [_blocker("daemon_status_unavailable", message, action="start or restart live_core_daemon.py")]
    payload = {
        "schema": SCHEMA,
        "status": "FAIL",
        "ready": False,
        "sourceUsed": "live_daemon",
        "daemonUrl": daemon_url,
        "snapshotUrl": _plugin_snapshot_source({})["snapshotUrl"],
        "contextSource": "daemon_unavailable",
        "fileSessionFallbackUsed": False,
        "freshnessSource": "daemon_unavailable",
        "profile": "woodcutting",
        "proposedAction": "unknown",
        "daemon": {"reachable": False, "latestTick": None, "sessionPath": None},
        "session": {},
        "sessions": {},
        "liveFiles": {},
        "candidates": {},
        "candidateSource": {},
        "highlighter": {},
        "overlay": {},
        "selectedTarget": None,
        "selectedTargetChecks": {},
        "currentIntent": "unknown",
        "actionReadiness": {
            "status": "FAIL",
            "executionAllowed": False,
            "intent": "unknown",
            "blockers": blockers,
            "warnings": [],
            "checks": {"daemonStatus": False},
            "checksSkippedAsNotApplicable": [],
            "missingCapabilities": ["daemon.status"],
        },
        "contextReadiness": {
            "status": "FAIL",
            "warnings": [],
            "applicableWarnings": [],
            "nonApplicableContextWarnings": [],
            "checksSkippedAsNotApplicable": [],
            "checks": {"daemonStatus": False},
        },
        "freshness": {},
        "inputGeometry": {},
        "actionExecution": {"allowed": False, "refusalReason": "daemon_status_unavailable"},
        "applicableWarnings": [],
        "nonApplicableContextWarnings": [],
        "checksSkippedAsNotApplicable": [],
        "staleFileSessionContext": False,
        "daemonSessionFresh": False,
        "pluginSnapshotFresh": False,
        "selectedResourceTargetFreshnessApplicable": False,
        "selectedResourceTargetFreshnessStatus": None,
        "readinessPassed": False,
        "blockers": blockers,
        "warnings": [],
        "missingCapabilities": ["daemon.status"],
        "requiredCapabilities": ["daemon.status"],
        "optionalCapabilities": ["plugin.snapshot"],
        "capabilities": {
            "daemonStatus": {"required": True, "available": False, "url": None},
            "pluginSnapshot": {"required": False, "available": None, **_plugin_snapshot_source({})},
        },
    }
    return payload


def build_readiness_report(
    *,
    daemon_url: str = "http://127.0.0.1:8890",
    timeout: float = 3.0,
    daemon_status: dict[str, Any] | None = None,
    fetch_json_func: Callable[..., dict[str, Any]] = fetch_json,
    sessions_dir: str | Path | None = None,
    profile: str = "woodcutting",
    proposed_action: str | None = None,
    top: int = 20,
) -> dict[str, Any]:
    warnings: list[str] = []
    non_applicable_context_warnings: list[str] = []
    blockers: list[dict[str, Any]] = []
    action_warnings: list[str] = []
    missing: list[str] = []
    daemon_reachable = True

    if daemon_status is None:
        try:
            status = fetch_json_func(daemon_status_url(daemon_url), timeout=timeout)
        except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as error:
            return _status_payload_unavailable(error, daemon_url=daemon_url)
    else:
        status = daemon_status if isinstance(daemon_status, dict) else {}

    if not status:
        daemon_reachable = False
        blockers.append(_blocker("daemon_status_empty", "daemon status response was empty", action="start or restart live_core_daemon.py"))
        missing.append("daemon.status")

    status_warnings = [str(item) for item in status.get("warnings") or [] if item is not None]
    input_source_active = str(status.get("inputSourceActive") or "").lower()
    plugin_snapshot_required = input_source_active == "plugin-snapshot"
    plugin_snapshot_source = _plugin_snapshot_source(status)
    source_metadata = _dict(status.get("sourceMetadata") or status.get("_liveQueryMetadata"))
    source_used = str(source_metadata.get("sourceUsed") or ("provided_daemon_status" if daemon_status is not None else "live_daemon"))
    context_source = str(source_metadata.get("contextSource") or ("live_daemon" if daemon_reachable else "daemon_unavailable"))
    file_session_fallback_used = bool(source_metadata.get("fileSessionFallbackUsed", False))
    freshness_source = str(
        source_metadata.get("freshnessSource")
        or ("daemon_status.liveProcessorFreshness" if _daemon_live_fresh(status) else "daemon_status")
    )
    effective_daemon_url = str(source_metadata.get("daemonUrl") or daemon_url)
    effective_snapshot_url = str(source_metadata.get("snapshotUrl") or plugin_snapshot_source["snapshotUrl"])
    raw_snapshot_failures = _snapshot_failure_warnings(status)
    snapshot_failures, suppressed_snapshot_failures = _current_snapshot_failures(status, raw_snapshot_failures)
    if plugin_snapshot_required and snapshot_failures:
        blockers.append(
            _blocker(
                "plugin_snapshot_source_not_ready",
                snapshot_failures[0],
                action=(
                    "restore the RuneLite PluginSnapshotEndpoint on localhost "
                    "or restart RuneLite/daemon with the bootstrap flow"
                ),
                sourceUrl=plugin_snapshot_source["url"],
                healthUrl=plugin_snapshot_source["healthUrl"],
            )
        )
        missing.append("plugin.snapshot")
    for warning in suppressed_snapshot_failures:
        non_applicable_context_warnings.append(
            f"historical plugin snapshot warning suppressed because daemon live status is fresh: {warning}"
        )

    root = get_sessions_dir(sessions_dir)
    latest_session = find_newest_session(root)
    latest_live_session = find_newest_live_session(root)
    daemon_session = daemon_session_from_status(status)
    candidate_report = build_candidate_report(
        session=latest_session,
        latest_session=True,
        sessions_dir=root,
        profile=profile,
        top=top,
        daemon_url=daemon_url,
        timeout=timeout,
        daemon_status=status,
    )
    sessions = _dict(candidate_report.get("sessions"))
    highlighter_session = Path(sessions["highlighterSessionPath"]).expanduser() if isinstance(sessions.get("highlighterSessionPath"), str) else None
    selected_target = selected_target_from_status(status)
    from action_proposal_core import build_action_proposal
    from input_control.input_geometry import input_geometry_from_status

    proposal = build_action_proposal(status)
    proposal_payload = proposal.to_dict()
    action = proposed_action or proposal.proposed_action
    current_intent = _intent_for_action(action, proposal.target_kind)
    resource_target_required = action in RESOURCE_TARGET_ACTIONS
    resource_recovery_required = action in RESOURCE_RECOVERY_ACTIONS
    navigation_target_required = action in NAVIGATION_ACTIONS or proposal.target_kind == "path_tile"
    service_object_required = action in SERVICE_OBJECT_ACTIONS
    route_transition_required = action in ROUTE_TRANSITION_ACTIONS
    interface_dialogue_required = action in INTERFACE_DIALOGUE_ACTIONS
    selected_resource_target_freshness_applicable = bool(resource_target_required or resource_recovery_required)
    proposal_target = _dict(proposal_payload.get("targetExplanation"))
    proposal_action_target_source = str(proposal_payload.get("actionTargetSource") or proposal_target.get("actionTargetSource") or "")
    proposal_actionability = str(proposal_payload.get("actionability") or proposal_target.get("actionability") or "")
    proposal_target_class = str(proposal_target.get("classId") or "").lower()
    proposal_resource_reacquired = (
        resource_target_required
        and proposal.target_kind == "resource"
        and proposal_payload.get("reason") == "post_service_resource_reacquired"
        and proposal_target_class in {"tree", "woodcutting_tree"}
    )
    if proposal_resource_reacquired:
        selected_target = proposal_target
    client_tick_hot_required = (
        plugin_snapshot_required
        and current_intent
        in {
            "resource_object_action",
            "resource_view_recovery_action",
            "navigation_waypoint_action",
            "service_object_action",
            "route_transition_action",
        }
    )
    client_tick_hot = _client_tick_hot_state(status)
    required_capabilities = ["daemon.status", "daemon.sessionPath", "daemon.latestTick", "input.geometry"]
    optional_capabilities = []
    if plugin_snapshot_required:
        required_capabilities.append("plugin.snapshot")
    else:
        optional_capabilities.append("plugin.snapshot")
    if client_tick_hot_required:
        required_capabilities.append("client_tick_hot")
    else:
        optional_capabilities.append("client_tick_hot")
    try:
        import liveness_recovery_core

        liveness_recovery = liveness_recovery_core.liveness_hint_from_daemon_status(status)
    except Exception as error:  # noqa: BLE001
        liveness_recovery = {
            "schema": "liveness_recovery_hint.v1",
            "livenessRecoveryAvailable": False,
            "livenessRecoveryRecommended": False,
            "livenessState": "unknown",
            "loadedSceneProof": {},
            "knownRecoverableState": False,
            "manualLoginRequired": False,
            "unknownScreen": False,
            "error": f"{type(error).__name__}: {error}",
        }
    if resource_target_required:
        required_capabilities.extend(
            [
                "overlay_debug_state.json",
                "highlighter.markers",
                "target.selected",
                "target.highlighterMatch",
                "target.geometry",
                "target.aimPoint",
                "target.onScreen",
            ]
        )
    if resource_recovery_required:
        required_capabilities.extend(["resource.candidates", "resource.projectionRecovery", "camera.controller"])
    if navigation_target_required:
        required_capabilities.extend(["route.waypoint", "navigation.intent"])
        optional_capabilities.extend(["overlay_debug_state.json", "highlighter.routeMarkers", "camera.controller"])
    if service_object_required:
        required_capabilities.extend(["service.target", "target.geometry", "target.aimPoint"])
    if route_transition_required:
        required_capabilities.extend(["route.transitionTarget", "target.geometry", "target.aimPoint"])
    if interface_dialogue_required:
        required_capabilities.extend(["dialogue_state", "dialogue.expectedOption", "service_route"])

    tick = _latest_tick(status)
    stale_file_session_context = bool(
        latest_session is not None
        and daemon_session is not None
        and not same_path(daemon_session, latest_session)
        and latest_live_session is not None
        and same_path(daemon_session, latest_live_session)
    )
    daemon_session_fresh = bool(
        daemon_reachable
        and bool(status)
        and daemon_session is not None
        and tick is not None
        and (latest_live_session is None or same_path(daemon_session, latest_live_session))
    )
    plugin_snapshot_fresh = bool(
        not snapshot_failures
        and (
            not plugin_snapshot_required
            or (
                input_source_active == "plugin-snapshot"
                and status.get("pluginSnapshotAvailable") is not False
                and tick is not None
            )
        )
    )
    if daemon_session is None:
        blockers.append(_blocker("daemon_session_missing", "daemon /status does not include sessionPath", action="start/restart daemon after RuneLite is logged in"))
        missing.append("daemon.sessionPath")
    if tick is None:
        blockers.append(_blocker("daemon_latest_tick_missing", "daemon /status does not include latestTick", action="wait for a live snapshot tick or restart daemon"))
        missing.append("daemon.latestTick")
    if client_tick_hot_required and not client_tick_hot["available"]:
        blockers.append(
            _blocker(
                "client_tick_hot_unavailable",
                "client-tick hot interaction state is unavailable",
                action="wait for RuneLite client ticks/PostMenuSort or restart/focus the dev client",
            )
        )
        missing.append("client_tick_hot")
    elif client_tick_hot_required and not client_tick_hot["fresh"]:
        age_text = "unknown" if client_tick_hot.get("ageMillis") is None else f"{client_tick_hot['ageMillis']} ms"
        stale_reason = client_tick_hot.get("staleReason") or "unknown"
        blockers.append(
            _blocker(
                "client_tick_hot_stale",
                f"client-tick hot interaction state is stale: age={age_text}; reason={stale_reason}",
                action=client_tick_hot.get("recovery") or "wait for fresh client tick/menu samples or refocus/restart RuneLite",
                ageMillis=client_tick_hot.get("ageMillis"),
                maxAgeMillis=client_tick_hot.get("maxAgeMillis"),
                staleReason=stale_reason,
                gameState=client_tick_hot.get("gameState"),
                isLoggedIn=client_tick_hot.get("isLoggedIn"),
                recovery=client_tick_hot.get("recovery"),
            )
        )
        missing.append("client_tick_hot.fresh")

    if latest_live_session is None:
        if resource_target_required:
            blockers.append(_blocker("latest_live_session_missing", "no session with live overlay/candidate outputs was found", action="start daemon with --write-overlay-state"))
            missing.append("session.liveOutputs")
        else:
            non_applicable_context_warnings.append("no session with live overlay/candidate outputs was found")
    elif daemon_session is not None and not same_path(daemon_session, latest_live_session):
        blockers.append(
            _blocker(
                "daemon_latest_live_session_mismatch",
                "daemon session does not match newest live overlay/candidate session",
                action="restart daemon after RuneLite creates the current live session",
            )
        )
        missing.append("session.match")

    if latest_session is not None and daemon_session is not None and not same_path(daemon_session, latest_session):
        if latest_live_session is not None and same_path(daemon_session, latest_live_session):
            non_applicable_context_warnings.append(
                "latest file session differs from daemon session; daemon/plugin live-output session is the current source of truth"
            )
        else:
            blockers.append(
                _blocker(
                    "daemon_latest_session_mismatch",
                    "daemon session does not match the newest telemetry session",
                    action="restart daemon after RuneLite is fully loaded",
                )
            )
            if "session.match" not in missing:
                missing.append("session.match")

    highlighter_live_dir = live_dir(highlighter_session)
    overlay_path = highlighter_live_dir / "overlay_debug_state.json" if highlighter_live_dir else None
    overlay_exists = bool(overlay_path and overlay_path.exists())
    overlay = read_json(overlay_path)
    markers = overlay_targets(overlay)
    matched_target = next((marker for marker in markers if target_matches(selected_target, marker)), {})
    if proposal_resource_reacquired and not matched_target:
        matched_target = dict(selected_target)
    selected_checks = _dict(candidate_report.get("selectedTargetChecks"))
    source_health = _dict(candidate_report.get("sourceHealth"))
    counts = _dict(candidate_report.get("counts"))
    selected_safe_aimpoint = (
        _dict(_dict(proposal_payload.get("targetExplanation")).get("safeAimPoint"))
        or _dict(_dict(selected_target or {}).get("safeAimPoint"))
        or _dict(_dict(matched_target or {}).get("safeAimPoint"))
    )
    action_need = _action_need_state(status, action=action, current_intent=current_intent, proposal_payload=proposal_payload)
    action_safety_evidence = _action_safety_evidence(
        proposal=proposal,
        proposal_payload=proposal_payload,
        proposal_target=proposal_target,
        selected_target=selected_target,
        matched_target=matched_target,
        client_tick_hot=client_tick_hot,
        freshness=_dict(candidate_report.get("freshness")),
        proposal_action_target_source=proposal_action_target_source,
        proposal_actionability=proposal_actionability,
    )
    overlay_health = _overlay_health_state(
        marker_count=len(markers),
        overlay_exists=overlay_exists,
        overlay_age_seconds=_dict(candidate_report.get("freshness")).get("highlighterOverlayAgeSeconds"),
        resource_target_required=resource_target_required,
        matched_target=matched_target,
        action_need=action_need,
        action_safety_evidence=action_safety_evidence,
    )

    if resource_target_required:
        if not overlay_exists and overlay_health.get("overlayBlocksCurrentAction"):
            blockers.append(
                _blocker(
                    "debug_overlay_json_missing",
                    f"debug overlay JSON missing: {path_text(overlay_path)}",
                    action="start daemon with --write-overlay-state and wait for overlay_debug_state.json",
                )
            )
            missing.append("overlay_debug_state.json")
        elif not overlay_exists:
            action_warnings.append(f"debug overlay JSON missing but not required for current action: {path_text(overlay_path)}")
        if not markers and overlay_health.get("overlayBlocksCurrentAction"):
            blockers.append(
                _blocker(
                    "highlighter_source_not_ready",
                    "highlighter source has no selected/candidate markers yet",
                    action="wait for overlay markers or restart the live target/daemon stack",
                )
            )
            missing.append("highlighter.markers")
        elif not markers:
            action_warnings.append(
                f"overlay/highlighter marker count is 0; {overlay_health.get('markerCountZeroStatus')}"
            )
        if not selected_target:
            blockers.append(_blocker("selected_target_missing", "daemon has no selected resource target", action="stand near valid Tree/Oak candidates and wait for target selection"))
            missing.append("target.selected")
        elif markers and not matched_target and overlay_health.get("overlayBlocksCurrentAction"):
            blockers.append(
                _blocker(
                    "selected_target_not_in_highlighter_source",
                    "daemon selected target is not present in the highlighter/overlay source",
                    action="wait for synchronized target/overlay output or restart daemon",
                )
            )
            missing.append("target.highlighterMatch")
        elif markers and not matched_target:
            action_warnings.append(
                "daemon selected target is not present in highlighter marker source; using live action safety evidence"
            )

        on_screen = _target_check_value(selected_target, matched_target, "onScreen")
        geometry_available = _target_check_value(selected_target, matched_target, "geometryAvailable")
        has_aim = _has_aim_point(selected_target) or _has_aim_point(matched_target) or bool(selected_checks.get("hasAimPoint"))
        ui_blocked = _target_check_value(selected_target, matched_target, "uiBlocked")
        if on_screen is False:
            blockers.append(_blocker("selected_target_offscreen", "selected target is not on screen", action="wait for an on-screen Tree/Oak target"))
            missing.append("target.onScreen")
        if geometry_available is False:
            blockers.append(_blocker("selected_target_geometry_missing", "selected target lacks usable geometry", action="wait for a target with clickbox/aim geometry"))
            missing.append("target.geometry")
        if not has_aim:
            blockers.append(_blocker("selected_target_aim_missing", "selected target has no aim point", action="wait for a target with clickbox/aim geometry"))
            missing.append("target.aimPoint")
        if ui_blocked is True:
            blockers.append(_blocker("selected_target_ui_blocked", "selected target is currently UI-blocked", action="clear blocking UI before executing"))
            missing.append("target.uiBlocked")
        proposal_missing = [str(item) for item in proposal_payload.get("missingCapabilities") or []]
        if action in RESOURCE_TARGET_ACTIONS and not proposal.executable:
            reason = str(proposal_payload.get("reason") or "resource target is not executable")
            warning_text = "; ".join(str(item) for item in proposal_payload.get("warnings") or []) or reason
            blockers.append(
                _blocker(
                    "selected_target_not_actionable",
                    f"selected target is not actionable: {warning_text}",
                    action="wait for a target with a safe visible aim point or allow reacquisition",
                    proposalReason=reason,
                )
            )
            capabilities = proposal_missing or ["safe_aimpoint", "click_point"]
            if "safe_aimpoint" not in capabilities:
                capabilities = ["safe_aimpoint", *capabilities]
            for capability in capabilities:
                missing.append(capability)
    elif resource_recovery_required:
        if not proposal.executable:
            blockers.append(
                _blocker(
                    "resource_projection_recovery_not_ready",
                    "resource projection recovery has no executable camera/input action",
                    action="wait for a recoverable resource projection failure or refresh target context",
                    proposalReason=proposal_payload.get("reason"),
                )
            )
            missing.extend(str(item) for item in proposal_payload.get("missingCapabilities") or ["resource.projectionRecovery"])
        if proposal_payload.get("warnings"):
            warnings.extend(str(item) for item in proposal_payload.get("warnings") or [])
    elif navigation_target_required:
        checks_missing: list[str] = []
        specific_target_blocker = False
        if proposal_actionability == "stale":
            blockers.append(
                _blocker(
                    "stale_target",
                    "navigation target is stale and cannot be executed",
                    action="refresh live route context and reacquire a projected waypoint",
                    proposalReason=proposal_payload.get("reason"),
                    actionTargetSource=proposal_action_target_source or None,
                    actionability=proposal_actionability,
                )
            )
            missing.append("target.freshness")
            specific_target_blocker = True
        elif proposal_actionability == "advisory_only" or proposal_action_target_source in {"static_route_prior", "route_context_goal"}:
            blockers.append(
                _blocker(
                    "static_target_not_executable",
                    "route target is an advisory/static prior, not a fresh executable waypoint",
                    action="refresh route context and project a live local waypoint before clicking",
                    proposalReason=proposal_payload.get("reason"),
                    actionTargetSource=proposal_action_target_source or None,
                    actionability=proposal_actionability or None,
                )
            )
            missing.append("route.liveProjection")
            specific_target_blocker = True
        elif proposal_actionability == "blocked" or str(proposal_actionability or "").startswith("blocked_"):
            blockers.append(
                _blocker(
                    "action_target_blocked",
                    "action target is currently blocked",
                    action="wait or reacquire a different live target",
                    proposalReason=proposal_payload.get("reason"),
                    actionTargetSource=proposal_action_target_source or None,
                    actionability=proposal_actionability,
                )
            )
            missing.append("route.waypoint")
            specific_target_blocker = True
        if not proposal.executable and not specific_target_blocker:
            checks_missing.append("route.waypoint")
        if not isinstance(proposal.target_tile, dict):
            checks_missing.append("route.waypoint")
        if checks_missing:
            blockers.append(
                _blocker(
                    "navigation_waypoint_not_ready",
                    "navigation intent has no executable waypoint",
                    action="wait for pathing/service route context to select a local waypoint",
                    proposalReason=proposal_payload.get("reason"),
                )
            )
            missing.extend(checks_missing)
        if proposal_payload.get("status") == "WARN":
            action_warnings.extend(str(item) for item in proposal_payload.get("warnings") or [])
    elif interface_dialogue_required:
        dialogue_state = _dict(status.get("dialogueState") or _dict(status.get("brain")).get("dialogueState"))
        if dialogue_state.get("active") is not True:
            blockers.append(
                _blocker(
                    "dialogue_state_not_active",
                    "dialogue action requires an active route-transition dialogue",
                    action="wait for the staircase prompt or click the route transition object first",
                    proposalReason=proposal_payload.get("reason"),
                )
            )
            missing.append("dialogue_state.active")
        if not proposal.executable:
            blockers.append(
                _blocker(
                    "dialogue_choice_not_ready",
                    "route-transition dialogue has no executable expected option",
                    action="wait for the correct dialogue options or reopen the route-transition prompt",
                    proposalReason=proposal_payload.get("reason"),
                )
            )
            missing.extend(str(item) for item in proposal_payload.get("missingCapabilities") or ["dialogue.expectedOption"])
        if proposal_payload.get("status") == "WARN":
            action_warnings.extend(str(item) for item in proposal_payload.get("warnings") or [])
    elif service_object_required or route_transition_required:
        if not proposal.executable:
            blockers.append(
                _blocker(
                    "action_target_not_ready",
                    "current intent target is not executable",
                    action="wait for a visible actionable service/transition target",
                    proposalReason=proposal_payload.get("reason"),
                )
            )
            missing.extend(str(item) for item in proposal_payload.get("missingCapabilities") or ["click_point"])
        if proposal_payload.get("status") == "WARN":
            action_warnings.extend(str(item) for item in proposal_payload.get("warnings") or [])
    elif action not in {"none", "wait_for_context", "wait_for_resource_result"} and not proposal.executable:
        blockers.append(
            _blocker(
                "action_proposal_not_executable",
                "current action proposal is not executable",
                action="wait for an executable action proposal",
                proposalReason=proposal_payload.get("reason"),
            )
        )
        missing.extend(str(item) for item in proposal_payload.get("missingCapabilities") or ["action.executable"])

    freshness = _dict(candidate_report.get("freshness"))
    if freshness.get("stale") and resource_target_required:
        blockers.append(
            _blocker(
                "candidate_data_stale",
                "; ".join(str(reason) for reason in freshness.get("staleReasons") or []) or "candidate data is stale",
                action="wait for fresh candidate tick or restart daemon",
            )
        )
        missing.append("target.freshness")
    elif freshness.get("stale"):
        selected_resource_target_freshness_status = str(freshness.get("targetCandidateFreshness") or "stale")
        non_applicable_context_warnings.append(
            (
                "; ".join(str(reason) for reason in freshness.get("staleReasons") or [])
                or f"selected resource target freshness is {selected_resource_target_freshness_status}"
            )
            + f"; not applicable while current intent is {current_intent}"
        )

    input_geometry = input_geometry_from_status(status)
    if not input_geometry.get("inputGeometryAvailable"):
        blockers.append(
            _blocker(
                "input_geometry_unavailable",
                f"RuneLite canvas/input geometry unavailable: {input_geometry.get('reason') or 'unknown'}",
                action="wait for RuneLite canvas geometry or focus/show the client window",
            )
        )
        missing.append("input.geometry")

    candidate_warnings = [str(item) for item in candidate_report.get("warnings") or []]
    if candidate_warnings:
        for warning in candidate_warnings:
            lowered = warning.lower()
            if "latest session differs from daemon action session" in lowered and stale_file_session_context:
                non_applicable_context_warnings.append(
                    "latest file session differs from daemon action session; file-based --latest-session tools may be stale"
                )
            elif "selected daemon target is not present in highlighter" in lowered and matched_target:
                continue
            elif (
                not selected_resource_target_freshness_applicable
                and (
                    "selected daemon target is not present in highlighter" in lowered
                    or "selected target is visible but not actionable" in lowered
                    or "selected target is not actionable" in lowered
                )
            ):
                non_applicable_context_warnings.append(f"{warning}; not applicable while current intent is {current_intent}")
            else:
                warnings.append(warning)

    selected_actionable = bool(proposal.executable) if action in RESOURCE_TARGET_ACTIONS else False
    route_context_applicability = _route_context_applicability(
        status,
        action_need=action_need,
        current_intent=current_intent,
        action=action,
        selected_actionable=selected_actionable,
    )
    if route_context_applicability.get("routeContextWarningOnly"):
        non_applicable_context_warnings.append(
            "service route context present but non-applicable while resource collection target is ready"
        )

    checks_skipped: list[str] = []
    if not resource_target_required:
        checks_skipped.extend(
            [
                "target.safeAimPoint",
                "target.onScreen",
            ]
        )
        if not resource_recovery_required:
            checks_skipped.extend(["target.selected", "target.highlighterMatch"])
        if not selected_resource_target_freshness_applicable:
            checks_skipped.extend(["target.candidateFreshness", "target.actionability"])
    if current_intent != "navigation_waypoint_action":
        checks_skipped.extend(["route.waypoint", "navigation.intent"])
    checks_skipped_unique = list(dict.fromkeys(checks_skipped))
    action_blockers = list(blockers)
    action_status = _status_from_parts(blockers=action_blockers, warnings=action_warnings)
    action_execution_allowed = bool(action_status != "FAIL" and action_need.get("actionReadinessNeeded"))
    applicable_warnings_unique = list(dict.fromkeys(warnings))
    non_applicable_context_warnings_unique = list(dict.fromkeys(non_applicable_context_warnings))
    all_context_warnings = list(dict.fromkeys([*applicable_warnings_unique, *non_applicable_context_warnings_unique]))
    context_status = _status_from_parts(warnings=all_context_warnings)
    status_value = "FAIL" if action_status == "FAIL" else "WARN" if all_context_warnings or action_warnings else "PASS"
    ready = action_status != "FAIL"
    missing_unique = list(dict.fromkeys(missing))
    action_warnings_unique = list(dict.fromkeys(action_warnings))
    blockers_codes = [str(blocker.get("code") or "readiness_blocker") for blocker in blockers]
    session_payload = {
        "latestSessionPath": path_text(latest_session),
        "latestLiveSessionPath": path_text(latest_live_session),
        "daemonSessionPath": path_text(daemon_session),
        "highlighterSessionPath": path_text(highlighter_session),
        "matchLatestLive": same_path(daemon_session, latest_live_session) if daemon_session and latest_live_session else False,
        "matchHighlighter": same_path(daemon_session, highlighter_session) if daemon_session and highlighter_session else False,
    }
    highlighter_payload = {
        "debugOverlayPath": path_text(overlay_path),
        "debugOverlayExists": overlay_exists,
        "debugOverlayAgeSeconds": freshness.get("highlighterOverlayAgeSeconds"),
        "markerCount": len(markers),
        "overlayStateWritten": source_health.get("overlayStateWritten"),
    }
    candidates_payload = {
        "candidateFilesExpected": source_health.get("candidateFilesExpected"),
        "daemonInMemoryCandidates": counts.get("daemonInMemoryCandidates"),
        "highlighterFileCandidates": counts.get("highlighterFileCandidates"),
        "highlighterMarkers": counts.get("highlighterMarkers"),
        "treeClassCandidates": counts.get("treeClassCandidates"),
        "knownChopCandidates": counts.get("knownChopCandidates"),
        "freshness": freshness,
    }
    live_files_payload = {
        "debugOverlayPath": path_text(overlay_path),
        "debugOverlayPresent": overlay_exists,
        "liveCandidatesExpected": source_health.get("candidateFilesExpected"),
        "latestCandidateFileAgeSeconds": freshness.get("latestCandidateFileAgeSeconds"),
        "highlighterOverlayAgeSeconds": freshness.get("highlighterOverlayAgeSeconds"),
    }
    selected_summary = target_summary(selected_target, profile=profile, source_session=daemon_session, source_tick=tick, status=status) if selected_target else None
    selected_highlighter_summary = target_summary(matched_target, profile=profile, source_session=highlighter_session, source_tick=tick, status=status) if matched_target else None
    if resource_target_required and not overlay_health.get("overlaySourceRequiredForCurrentAction"):
        for capability in ("overlay_debug_state.json", "highlighter.markers", "target.highlighterMatch"):
            if capability in required_capabilities:
                required_capabilities.remove(capability)
            if capability not in optional_capabilities:
                optional_capabilities.append(capability)
    return {
        "schema": SCHEMA,
        "status": status_value,
        "ready": ready,
        "sourceUsed": source_used,
        "daemonUrl": effective_daemon_url,
        "snapshotUrl": effective_snapshot_url,
        "contextSource": context_source,
        "fileSessionFallbackUsed": file_session_fallback_used,
        "freshnessSource": freshness_source,
        "profile": profile,
        "proposedAction": action,
        "currentIntent": current_intent,
        "daemon": {
            "reachable": daemon_reachable,
            "latestTick": tick,
            "sessionPath": path_text(daemon_session),
        },
        "session": session_payload,
        "sessions": session_payload,
        "liveFiles": live_files_payload,
        "highlighter": highlighter_payload,
        "overlay": highlighter_payload,
        "candidates": candidates_payload,
        "candidateSource": candidates_payload,
        "selectedTarget": selected_summary,
        "selectedHighlighterTarget": selected_highlighter_summary,
        "selectedTargetChecks": {
            "present": bool(selected_target),
            "inHighlighterSource": bool(matched_target) if markers else False,
            "onScreen": _target_check_value(selected_target, matched_target, "onScreen") if selected_target or matched_target else None,
            "geometryAvailable": _target_check_value(selected_target, matched_target, "geometryAvailable") if selected_target or matched_target else None,
            "hasAimPoint": _has_aim_point(selected_target) or _has_aim_point(matched_target) or bool(selected_checks.get("hasAimPoint")),
            "actionable": selected_actionable if action in RESOURCE_TARGET_ACTIONS else None,
            "safeAimPointStatus": selected_safe_aimpoint.get("status") if selected_safe_aimpoint else None,
            "uiBlocked": _target_check_value(selected_target, matched_target, "uiBlocked") if selected_target or matched_target else None,
            "stale": freshness.get("stale"),
        },
        "actionNeed": action_need,
        "livenessRecoveryRecommended": bool(liveness_recovery.get("livenessRecoveryRecommended")),
        "livenessRecoveryAvailable": bool(liveness_recovery.get("livenessRecoveryAvailable")),
        "livenessRecoveryLastResult": status.get("livenessRecoveryLastResult"),
        "livenessState": liveness_recovery.get("livenessState"),
        "loadedSceneProof": liveness_recovery.get("loadedSceneProof"),
        "knownRecoverableState": bool(liveness_recovery.get("knownRecoverableState")),
        "manualLoginRequired": bool(liveness_recovery.get("manualLoginRequired")),
        "unknownScreen": bool(liveness_recovery.get("unknownScreen")),
        **route_context_applicability,
        "overlayHealth": overlay_health,
        "actionSafetyEvidence": action_safety_evidence,
        "actionReadiness": {
            "status": action_status,
            "executionAllowed": action_execution_allowed,
            "intent": current_intent,
            "blockers": action_blockers,
            "warnings": action_warnings_unique,
            "checks": {
                "daemonReachable": daemon_reachable and bool(status),
                "daemonSessionKnown": daemon_session is not None,
                "latestTickKnown": tick is not None,
                "pluginSnapshotRequired": plugin_snapshot_required,
                "pluginSnapshotAvailable": False if snapshot_failures else status.get("pluginSnapshotAvailable"),
                "daemonSessionFresh": daemon_session_fresh,
                "pluginSnapshotFresh": plugin_snapshot_fresh,
                "inputGeometryAvailable": bool(input_geometry.get("inputGeometryAvailable")),
                "clientTickHotRequired": client_tick_hot_required,
                "clientTickHotAvailable": client_tick_hot["available"] if client_tick_hot_required else None,
                "clientTickHotFresh": client_tick_hot["fresh"] if client_tick_hot_required else None,
                "livenessGameStateFresh": client_tick_hot.get("livenessGameStateFresh") if client_tick_hot_required else None,
                "actionHotFresh": client_tick_hot.get("actionHotFresh") if client_tick_hot_required else None,
                "clientTickHotUsableForAction": client_tick_hot.get("clientTickHotUsableForAction") if client_tick_hot_required else None,
                "clientTickHotSource": client_tick_hot.get("clientTickHotSource") if client_tick_hot_required else None,
                "clientTickHotAgeMillis": client_tick_hot.get("ageMillis"),
                "clientTickHotMaxAgeMillis": client_tick_hot.get("maxAgeMillis") if client_tick_hot_required else None,
                "clientTickHotStaleReason": client_tick_hot.get("staleReason"),
                "livenessRecoveryRecommended": bool(liveness_recovery.get("livenessRecoveryRecommended")),
                "livenessState": liveness_recovery.get("livenessState"),
                "gameState": client_tick_hot.get("gameState"),
                "isLoggedIn": client_tick_hot.get("isLoggedIn"),
                "resourceTargetRequired": resource_target_required,
                "resourceProjectionRecoveryRequired": resource_recovery_required,
                "selectedResourceTargetFreshnessApplicable": selected_resource_target_freshness_applicable,
                "selectedResourceTargetFreshnessStatus": freshness.get("targetCandidateFreshness"),
                "selectedTargetInHighlighterSource": bool(matched_target) if resource_target_required else None,
                "navigationWaypointRequired": navigation_target_required,
                "navigationWaypointAvailable": isinstance(proposal.target_tile, dict) if navigation_target_required else None,
                "proposalExecutable": proposal.executable,
                "proposalActionTargetSource": proposal_action_target_source or None,
                "proposalActionability": proposal_actionability or None,
                "staleProposalDetected": bool(proposal_payload.get("staleProposalDetected")),
                "actionReadinessNeeded": action_need.get("actionReadinessNeeded"),
                "overlayBlocksCurrentAction": overlay_health.get("overlayBlocksCurrentAction"),
            },
            "checksSkippedAsNotApplicable": checks_skipped_unique,
            "missingCapabilities": missing_unique,
        },
        "contextReadiness": {
            "status": context_status,
            "warnings": all_context_warnings,
            "applicableWarnings": applicable_warnings_unique,
            "nonApplicableContextWarnings": non_applicable_context_warnings_unique,
            "checksSkippedAsNotApplicable": checks_skipped_unique,
            "checks": {
                "selectedResourceTargetPresent": bool(selected_target),
                "selectedResourceTargetInHighlighterSource": bool(matched_target) if markers else False,
                "highlighterMarkerCount": len(markers),
                "candidateFreshness": freshness.get("targetCandidateFreshness"),
                "selectedResourceTargetFreshnessApplicable": selected_resource_target_freshness_applicable,
                "selectedResourceTargetFreshnessStatus": freshness.get("targetCandidateFreshness"),
                "staleFileSessionContext": stale_file_session_context,
                "daemonSessionFresh": daemon_session_fresh,
                "pluginSnapshotFresh": plugin_snapshot_fresh,
                "livenessRecoveryRecommended": bool(liveness_recovery.get("livenessRecoveryRecommended")),
                "livenessState": liveness_recovery.get("livenessState"),
                "loadedSceneVerified": _dict(liveness_recovery.get("loadedSceneProof")).get("loadedSceneVerified"),
                "overlayHealth": overlay_health,
                "actionNeed": action_need,
            },
        },
        "applicableWarnings": applicable_warnings_unique,
        "nonApplicableContextWarnings": non_applicable_context_warnings_unique,
        "checksSkippedAsNotApplicable": checks_skipped_unique,
        "staleFileSessionContext": stale_file_session_context,
        "daemonSessionFresh": daemon_session_fresh,
        "pluginSnapshotFresh": plugin_snapshot_fresh,
        "selectedResourceTargetFreshnessApplicable": selected_resource_target_freshness_applicable,
        "selectedResourceTargetFreshnessStatus": freshness.get("targetCandidateFreshness"),
        "freshness": freshness,
        "inputGeometry": input_geometry,
        "clientTickHot": client_tick_hot,
        "actionExecution": {
            "allowed": action_execution_allowed,
            "refusalReason": blockers_codes[0] if blockers_codes else None,
            "requiresReadinessPass": True,
            "proposalStatus": proposal_payload.get("status"),
            "proposalReason": proposal_payload.get("reason"),
            "proposalExecutable": proposal_payload.get("executable"),
            "proposalActionTargetSource": proposal_action_target_source or None,
            "proposalActionability": proposal_actionability or None,
            "staleProposalDetected": bool(proposal_payload.get("staleProposalDetected")),
            "staleProposalSource": proposal_payload.get("staleProposalSource"),
            "proposalMissingCapabilities": proposal_payload.get("missingCapabilities") or [],
        },
        "capabilities": {
            "daemonStatus": {
                "required": True,
                "available": daemon_reachable and bool(status),
                "url": daemon_status_url(daemon_url),
            },
            "pluginSnapshot": {
                **plugin_snapshot_source,
                "required": plugin_snapshot_required,
                "available": False if snapshot_failures else status.get("pluginSnapshotAvailable"),
                "status": status.get("pluginSnapshotStatus"),
                "inputSourceActive": status.get("inputSourceActive"),
                "reason": snapshot_failures[0] if snapshot_failures else None,
                "suppressedWarnings": suppressed_snapshot_failures,
                "warningSuppressed": bool(suppressed_snapshot_failures),
            },
            "overlayDebug": {
                "required": bool(overlay_health.get("overlaySourceRequiredForCurrentAction")),
                "available": overlay_exists,
                "path": path_text(overlay_path),
                "markerCount": len(markers),
                "health": overlay_health,
            },
            "inputGeometry": {
                "required": True,
                "available": bool(input_geometry.get("inputGeometryAvailable")),
                "reason": input_geometry.get("reason"),
            },
            "clientTickHot": {
                "required": client_tick_hot_required,
                "available": client_tick_hot["available"],
                "fresh": client_tick_hot["fresh"],
                "livenessGameStateFresh": client_tick_hot.get("livenessGameStateFresh"),
                "actionHotFresh": client_tick_hot.get("actionHotFresh"),
                "usableForAction": client_tick_hot.get("clientTickHotUsableForAction"),
                "sourceEvent": client_tick_hot.get("sourceEvent"),
                "sampleSource": client_tick_hot.get("sampleSource"),
                "ageMillis": client_tick_hot.get("ageMillis"),
                "maxAgeMillis": client_tick_hot.get("maxAgeMillis"),
                "clientTick": client_tick_hot.get("clientTick"),
                "gameTickAtSample": client_tick_hot.get("gameTickAtSample"),
                "topOption": client_tick_hot.get("topOption"),
                "topTarget": client_tick_hot.get("topTarget"),
                "gameState": client_tick_hot.get("gameState"),
                "isLoggedIn": client_tick_hot.get("isLoggedIn"),
                "staleReason": client_tick_hot.get("staleReason"),
                "latestPostMenuSortAgeMillis": client_tick_hot.get("latestPostMenuSortAgeMillis"),
                "lastMenuOptionClickedAgeMillis": client_tick_hot.get("lastMenuOptionClickedAgeMillis"),
                "snapshotAgeMillis": client_tick_hot.get("snapshotAgeMillis"),
                "daemonLatestTickAgeMillis": client_tick_hot.get("daemonLatestTickAgeMillis"),
            },
        },
        "requiredCapabilities": list(dict.fromkeys(required_capabilities)),
        "optionalCapabilities": list(dict.fromkeys(optional_capabilities)),
        "readinessPassed": ready,
        "blockers": blockers,
        "warnings": all_context_warnings,
        "missingCapabilities": missing_unique,
    }

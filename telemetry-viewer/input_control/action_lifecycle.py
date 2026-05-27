from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .action_proposal import (
    ActionProposal,
    SERVICE_COMFORTABLE_EDGE_DISTANCE_PX,
    SERVICE_COMFORTABLE_REGION_FRACTION,
    SERVICE_MIN_EDGE_DISTANCE_PX,
    SERVICE_MIN_VISIBLE_AREA_PX,
    SERVICE_MIN_VISIBLE_AREA_RATIO,
    build_action_proposal,
)


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


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
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


def _player_context(status: dict[str, Any]) -> dict[str, Any]:
    player = _context(status, "playerContext") or _context(status, "player")
    if player:
        return player
    brain = _status_brain(status)
    summary = brain.get("currentContextSummary")
    if isinstance(summary, dict) and isinstance(summary.get("player"), dict):
        return summary["player"]
    summary = status.get("currentContextSummary")
    if isinstance(summary, dict) and isinstance(summary.get("player"), dict):
        return summary["player"]
    return {}


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


def _player_plane(status: dict[str, Any]) -> int | None:
    player = _player_context(status)
    tile = player.get("tile") or player.get("worldTile") or status.get("playerTile") or status.get("playerWorldTile")
    if isinstance(tile, dict):
        value = _int(tile.get("plane"))
        if value is not None:
            return value
    for key in ("plane", "playerPlane"):
        value = _int(player.get(key) if key in player else status.get(key))
        if value is not None:
            return value
    return None


def _route_step_index(status: dict[str, Any]) -> int | None:
    route = _context(status, "serviceRouteContext")
    value = _int(route.get("currentStepIndex"))
    if value is not None:
        return value
    return _int(status.get("serviceRouteCurrentStepIndex"))


def _transition_route_context(status: dict[str, Any]) -> dict[str, Any]:
    return _context(status, "returnRouteContext") or _context(status, "serviceRouteContext")


def _transition_route_step_index(status: dict[str, Any]) -> int | None:
    route = _transition_route_context(status)
    value = _int(route.get("currentStepIndex"))
    if value is not None:
        return value
    return _route_step_index(status)


def _is_return_transition(before: dict[str, Any], after: dict[str, Any]) -> bool:
    if _context(before, "returnRouteContext") or _context(after, "returnRouteContext"):
        return True
    for status in (before, after):
        phase, intent = phase_and_intent(status)
        if phase == "return_to_resource" or intent == "return_to_resource_area":
            return True
        if status.get("currentCycleStage") == "return_to_resource":
            return True
    return False


def _route_node_id(status: dict[str, Any]) -> str | None:
    route = _context(status, "serviceRouteContext")
    value = route.get("currentNodeId")
    if value is None:
        value = status.get("serviceRouteCurrentNodeId")
    return str(value) if value not in (None, "") else None


def _route_step_status(status: dict[str, Any]) -> str | None:
    route = _context(status, "serviceRouteContext")
    value = route.get("routeStepStatus")
    if value is None:
        value = status.get("serviceRouteStepStatus")
    return str(value) if value not in (None, "") else None


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


def _navigation_metrics(status: dict[str, Any]) -> dict[str, float]:
    values: dict[str, float] = {}
    metric_sources = (
        ("destination_distance", "pathingContext", "distanceToDestination", "distanceToDestination"),
        ("path_target_distance", "pathingContext", "distanceToPathTarget", "distanceToPathTarget"),
        ("navigation_intent_distance", "navigationIntentContext", "distanceTiles", "navigationIntentDistanceTiles"),
        ("service_distance", "serviceContext", "distanceToServiceTarget", "distanceToServiceTarget"),
        ("final_approach_distance", "serviceContext", "distanceToFinalApproach", "serviceDistanceToFinalApproach"),
    )
    for name, context_name, key, fallback in metric_sources:
        value = _float(context_value(status, context_name, key, fallback))
        if value is not None:
            values[name] = value
    # Older callers sometimes flatten pathing fields into the status payload.
    flat_aliases = {
        "destination_distance": ("distanceToDestination",),
        "path_target_distance": ("distanceToPathTarget",),
        "navigation_intent_distance": ("navigationIntentDistanceTiles",),
        "service_distance": ("distanceToServiceTarget",),
        "final_approach_distance": ("serviceDistanceToFinalApproach",),
    }
    for name, keys in flat_aliases.items():
        if name in values:
            continue
        for key in keys:
            value = _float(status.get(key))
            if value is not None:
                values[name] = value
                break
    return values


def _pathing_context(status: dict[str, Any]) -> dict[str, Any]:
    return _context(status, "pathingContext")


def _pathing_movement_active(status: dict[str, Any]) -> bool:
    pathing = _pathing_context(status)
    movement_state = str(pathing.get("movementState") or status.get("movementState") or "").strip().lower()
    if movement_state in {"moving", "recently_moved", "pathing", "walking"}:
        return True
    activity = _context(status, "activityContext") or _context(status, "activity")
    raw = activity.get("raw") if isinstance(activity.get("raw"), dict) else {}
    raw_activity = raw.get("activity") if isinstance(raw.get("activity"), dict) else {}
    return activity.get("isMoving") is True or raw_activity.get("isMoving") is True


def _tile_like_key(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, dict):
        return None
    world_x = _int(value.get("worldX") if value.get("worldX") is not None else value.get("x"))
    world_y = _int(value.get("worldY") if value.get("worldY") is not None else value.get("y"))
    plane = _int(value.get("plane"))
    if world_x is None or world_y is None:
        return None
    return (world_x, world_y, 0 if plane is None else plane)


def _local_destination_key(status: dict[str, Any]) -> tuple[int, int, int] | None:
    pathing = _pathing_context(status)
    for key in ("localDestination", "currentLocalDestination", "destinationTile", "pathTargetTile", "nextWaypointTile"):
        value = pathing.get(key)
        tile = _tile_like_key(value)
        if tile is not None:
            return tile
    for key in ("localDestination", "currentLocalDestination", "destinationTile"):
        tile = _tile_like_key(status.get(key))
        if tile is not None:
            return tile
    return None


def _service_route_action_ready(status: dict[str, Any]) -> bool | None:
    route = _context(status, "serviceRouteContext")
    value = _bool(route.get("actionReady"))
    if value is not None:
        return value
    return _bool(status.get("serviceRouteActionReady"))


def _player_tile_key(status: dict[str, Any]) -> str | None:
    player = _player_context(status)
    tile = player.get("tile") or player.get("worldTile") or status.get("playerTile") or status.get("playerWorldTile")
    if isinstance(tile, dict):
        x = tile.get("x") or tile.get("worldX")
        y = tile.get("y") or tile.get("worldY")
        plane = tile.get("plane")
        return f"{x},{y},{plane}" if x is not None and y is not None else None
    x = player.get("worldX") if "worldX" in player else player.get("x")
    y = player.get("worldY") if "worldY" in player else player.get("y")
    plane = player.get("plane")
    if x is not None and y is not None:
        return f"{x},{y},{plane}"
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
    if action == "resource_view_recovery":
        return {
            "action": action,
            "resultType": "projection_refresh",
            "expectedSignal": "resource_projection_refresh_or_reproposal",
            "description": "camera/view recovery runs without a world click, then resource projection is refreshed before any resource click",
        }
    if action == "service_view_recovery":
        return {
            "action": action,
            "resultType": "service_projection_refresh",
            "expectedSignal": "service_projection_refresh_or_reproposal",
            "description": "camera/view recovery runs without a service click, then service projection is refreshed before any Bank/Deposit click",
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
    if action == "interact_service_route_object":
        return {
            "action": action,
            "resultType": "service_route_transition_progress",
            "expectedSignal": "menu_click_plane_or_route_step_change",
            "description": "route object interaction produces a plane/location change or route step advance",
        }
    if action == "interface_dialogue_choice":
        return {
            "action": action,
            "resultType": "route_transition_dialogue_resolved",
            "expectedSignal": "dialogue_choice_plane_or_route_step_change",
            "description": "route-transition dialogue option produces a plane/location change or route step advance",
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


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _status_game_state(status: dict[str, Any]) -> str | None:
    brain = _status_brain(status)
    client_hot = _dict(status.get("clientTickHot") or brain.get("clientTickHot"))
    post_menu = _dict(client_hot.get("postMenuSort"))
    baseline = _dict(status.get("baseline") or brain.get("baseline") or status.get("liveBaseline"))
    world_model = _dict(status.get("worldModelSummary") or brain.get("worldModelSummary"))
    game_state = _first_value(
        status.get("gameState"),
        brain.get("gameState"),
        client_hot.get("gameState"),
        post_menu.get("gameState"),
        baseline.get("gameState"),
        world_model.get("gameState"),
    )
    return str(game_state) if game_state not in (None, "") else None


def _status_world_model_object_total(status: dict[str, Any]) -> int | None:
    brain = _status_brain(status)
    world_model = _dict(status.get("worldModelSummary") or brain.get("worldModelSummary"))
    metadata = _dict(world_model.get("metadata"))
    for value in (
        status.get("worldModelObjectTotal"),
        brain.get("worldModelObjectTotal"),
        world_model.get("objectTotal"),
        world_model.get("objectCount"),
        metadata.get("objectTotal"),
        metadata.get("objectCount"),
    ):
        parsed = _int(value)
        if parsed is not None:
            return parsed
    return None


def _loaded_scene_problem(status: dict[str, Any]) -> dict[str, Any]:
    bootstrap = _dict(status.get("bootstrapState") or _status_brain(status).get("bootstrapState"))
    freshness = _dict(status.get("liveProcessorFreshness") or _status_brain(status).get("liveProcessorFreshness"))
    loaded_verified = _bool(_first_value(status.get("loadedSceneVerified"), bootstrap.get("loadedSceneVerified")))
    game_state = _status_game_state(status)
    object_total = _status_world_model_object_total(status)
    tick_fresh = _bool(_first_value(
        status.get("clientTickHotFresh"),
        status.get("pluginSnapshotFresh"),
        freshness.get("freshByMillis"),
    ))
    reasons: list[str] = []
    if loaded_verified is False:
        reasons.append("loaded_scene_not_verified")
    if game_state and game_state != "LOGGED_IN":
        reasons.append(f"game_state_{game_state.lower()}")
    if object_total == 0:
        reasons.append("world_model_object_total_zero")
    if tick_fresh is False:
        reasons.append("live_scene_not_fresh")
    return {
        "blocking": bool(reasons),
        "reasons": reasons,
        "gameState": game_state,
        "loadedSceneVerified": loaded_verified,
        "worldModelObjectTotal": object_total,
        "clientTickHotFresh": tick_fresh,
    }


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


_PROJECTION_SENTINEL_THRESHOLD = 100000.0


def _projection_number_is_sentinel(value: Any) -> bool:
    number = _float(value)
    return number is not None and abs(number) >= _PROJECTION_SENTINEL_THRESHOLD


def _projection_point_is_sentinel(value: Any) -> bool:
    point = _dict(value)
    if not point:
        return False
    return any(
        _projection_number_is_sentinel(point.get(key))
        for key in ("x", "y", "canvasX", "canvasY")
    )


def _projection_bounds_is_sentinel(value: Any) -> bool:
    bounds = _dict(value)
    if not bounds:
        return False
    return any(
        _projection_number_is_sentinel(bounds.get(key))
        for key in ("x", "y", "canvasX", "canvasY")
    )


def _target_resource_projection_state(status: dict[str, Any]) -> dict[str, Any]:
    target: dict[str, Any] = {}
    for key in (
        "selectedHighlighterTarget",
        "selectedTarget",
        "selectedResourceTarget",
        "activeIntentTarget",
    ):
        value = status.get(key)
        if isinstance(value, dict) and value:
            target = value
            break
    if not target:
        generic = generic_state(status)
        active = generic.get("activeIntentTarget")
        if isinstance(active, dict):
            target = active
    projection = _dict(target.get("resourceProjectionStatus"))
    safe = _dict(target.get("safeAimPoint"))
    raw_aim = target.get("rawAimPoint") or target.get("aimPoint") or target.get("canvasAimPoint")
    if isinstance(safe.get("rawAimPoint"), dict):
        raw_aim = safe.get("rawAimPoint")
    bounds = (
        target.get("bounds")
        or target.get("canvasBounds")
        or target.get("canvasTileBounds")
        or safe.get("bounds")
    )
    projection_sentinel = bool(
        projection.get("projectionSentinel") is True
        or _projection_point_is_sentinel(raw_aim)
        or _projection_bounds_is_sentinel(bounds)
    )
    safe_available = bool(
        projection.get("safeAimPointAvailable") is True
        or safe.get("actionable") is True
        or str(safe.get("status") or "").upper() == "PASS"
    )
    classification = str(projection.get("classification") or "")
    if not classification:
        if projection_sentinel:
            classification = "projection_sentinel"
        elif safe_available:
            classification = "safe"
        elif not target:
            classification = "target_missing"
        elif _bool(target.get("geometryAvailable")) is False or str(target.get("geometryStatus") or "") == "missing":
            classification = "no_projection"
        elif safe and safe.get("actionable") is False:
            classification = str(safe.get("rejectionReason") or "no_safe_aimpoint")
        else:
            classification = "unknown"
    return {
        "targetPresent": bool(target),
        "targetName": target.get("targetName") or target.get("name"),
        "targetKey": target.get("targetKey") or target.get("objectKey"),
        "worldLocation": target.get("worldLocation")
        or {
            "worldX": target.get("worldX"),
            "worldY": target.get("worldY"),
            "plane": target.get("plane"),
        },
        "classification": classification,
        "safeAimPointAvailable": safe_available,
        "projectionSentinel": projection_sentinel,
        "safeAimPointReason": projection.get("safeAimPointReason") or safe.get("rejectionReason"),
        "canvasPoint": projection.get("canvasPoint") or raw_aim,
        "canvasBounds": projection.get("canvasBounds") or bounds,
    }


def _point_from_canvas_like(value: Any) -> tuple[float, float] | None:
    value = _dict(value)
    x = _float(value.get("canvasX", value.get("x")))
    y = _float(value.get("canvasY", value.get("y")))
    if x is None or y is None:
        return None
    return x, y


def _service_exposure_metrics(target: dict[str, Any], projection: dict[str, Any], safe: dict[str, Any]) -> dict[str, Any]:
    point = (
        _point_from_canvas_like(safe)
        or _point_from_canvas_like(projection.get("aimPoint"))
        or _point_from_canvas_like(projection.get("canvasPoint"))
        or _point_from_canvas_like(target.get("aimPoint"))
    )
    edge_distance = _float(
        safe.get("distanceToViewportEdgePx")
        if safe.get("distanceToViewportEdgePx") is not None
        else safe.get("distanceToCanvasEdgePx")
        if safe.get("distanceToCanvasEdgePx") is not None
        else projection.get("edgeDistancePx")
    )
    if edge_distance is None and point is not None:
        x, y = point
        edge_distance = min(x, 765.0 - x, y, 503.0 - y)
    area_px = _float(
        safe.get("clippedVisibleAreaPx")
        if safe.get("clippedVisibleAreaPx") is not None
        else projection.get("clippedVisibleAreaPx")
        if projection.get("clippedVisibleAreaPx") is not None
        else projection.get("visibleAreaPx")
    )
    ratio = _float(
        safe.get("clippedVisibleAreaRatio")
        if safe.get("clippedVisibleAreaRatio") is not None
        else projection.get("clippedVisibleAreaRatio")
        if projection.get("clippedVisibleAreaRatio") is not None
        else projection.get("visibleAreaRatio")
    )
    centrality = None
    comfortable_region = False
    if point is not None:
        x, y = point
        normalized_distance = max(abs(x - 382.5) / 382.5, abs(y - 251.5) / 251.5)
        centrality = max(0.0, min(1.0, 1.0 - normalized_distance))
        margin_x = 765.0 * (1.0 - SERVICE_COMFORTABLE_REGION_FRACTION) / 2.0
        margin_y = 503.0 * (1.0 - SERVICE_COMFORTABLE_REGION_FRACTION) / 2.0
        comfortable_region = bool(margin_x <= x <= 765.0 - margin_x and margin_y <= y <= 503.0 - margin_y)
    safe_available = bool(
        _projection_point_is_sentinel(safe.get("rawAimPoint") or projection.get("aimPoint") or target.get("aimPoint")) is False
        and (
            projection.get("actionableByCanvas") is True
            or safe.get("actionable") is True
            or str(safe.get("status") or "").upper() == "PASS"
        )
    )
    visible_area_ok = bool(area_px is None or area_px >= SERVICE_MIN_VISIBLE_AREA_PX) and bool(
        ratio is None or ratio >= SERVICE_MIN_VISIBLE_AREA_RATIO
    )
    edge_ok = bool(edge_distance is not None and edge_distance >= SERVICE_MIN_EDGE_DISTANCE_PX)
    if _bool(safe.get("rawCenterInsideViewport")) is False and edge_distance is not None and edge_distance < SERVICE_COMFORTABLE_EDGE_DISTANCE_PX:
        comfortable_region = False
    usable = bool(safe_available and visible_area_ok and edge_ok and comfortable_region and safe.get("uiBlocked") is not True)
    edge_sliver = bool(
        safe_available
        and (
            (edge_distance is not None and edge_distance < SERVICE_MIN_EDGE_DISTANCE_PX)
            or (area_px is not None and area_px < SERVICE_MIN_VISIBLE_AREA_PX)
            or (ratio is not None and ratio < SERVICE_MIN_VISIBLE_AREA_RATIO)
        )
    )
    return {
        "safeAimPointAvailable": safe_available,
        "usableExposureAvailable": usable,
        "edgeSliverVisible": edge_sliver,
        "visibleAreaPx": area_px,
        "visibleAreaRatio": ratio,
        "edgeDistancePx": edge_distance,
        "centralityScore": round(centrality, 3) if centrality is not None else None,
        "comfortableViewRegionMet": comfortable_region,
    }


def _target_service_projection_state(status: dict[str, Any]) -> dict[str, Any]:
    target: dict[str, Any] = {}
    brain = _status_brain(status)
    route = _dict(brain.get("serviceRouteContext") or status.get("serviceRouteContext"))
    service = _dict(brain.get("serviceContext") or status.get("serviceContext"))
    generic = generic_state(status)
    for value in (
        route.get("visibleServiceTarget"),
        route.get("selectedServiceObject"),
        service.get("bestServiceCandidate"),
        service.get("bestServiceTarget"),
        service.get("target"),
        generic.get("activeIntentTarget"),
        status.get("selectedTarget"),
        status.get("activeIntentTarget"),
    ):
        if isinstance(value, dict) and value:
            target = value
            break
    projection = _dict(target.get("projectionStatus") or target.get("projection"))
    safe = _dict(target.get("safeAimPoint"))
    raw_aim = (
        _dict(projection.get("aimPoint"))
        or _dict(projection.get("canvasLocation"))
        or target.get("rawAimPoint")
        or target.get("aimPoint")
        or target.get("canvasAimPoint")
    )
    if isinstance(safe.get("rawAimPoint"), dict):
        raw_aim = safe.get("rawAimPoint")
    projection_sentinel = _projection_point_is_sentinel(raw_aim)
    safe_available = bool(
        projection_sentinel is False
        and (
            projection.get("actionableByCanvas") is True
            or safe.get("actionable") is True
            or str(safe.get("status") or "").upper() == "PASS"
        )
    )
    exposure_metrics = _service_exposure_metrics(target, projection, safe)
    exposure_metrics["safeAimPointAvailable"] = bool(safe_available)
    classification = str(projection.get("classification") or "")
    if not classification:
        if projection_sentinel:
            classification = "projection_sentinel"
        elif safe_available:
            classification = "safe"
        elif not target:
            classification = "target_missing"
        elif projection.get("onScreen") is False or target.get("onScreen") is False:
            classification = "offscreen"
        elif _bool(target.get("geometryAvailable")) is False or str(target.get("geometryStatus") or "") == "missing":
            classification = "no_projection"
        elif safe and safe.get("actionable") is False:
            classification = str(safe.get("rejectionReason") or "no_safe_aimpoint")
        else:
            classification = "unknown"
    result = {
        "targetPresent": bool(target),
        "targetName": target.get("targetName") or target.get("name"),
        "targetKey": target.get("targetKey") or target.get("objectKey"),
        "worldLocation": target.get("worldLocation")
        or {
            "worldX": target.get("worldX"),
            "worldY": target.get("worldY"),
            "plane": target.get("plane"),
        },
        "classification": classification,
        "safeAimPointAvailable": safe_available,
        "projectionSentinel": projection_sentinel,
        "safeAimPointReason": projection.get("safeAimPointReason") or safe.get("rejectionReason"),
        "canvasPoint": projection.get("canvasPoint") or raw_aim,
        "canvasBounds": projection.get("canvasBounds") or target.get("bounds") or safe.get("bounds"),
    }
    result.update(exposure_metrics)
    result["safeAimPointAvailable"] = bool(safe_available)
    return result


def _projection_recovery_improved(before: dict[str, Any], after: dict[str, Any]) -> bool:
    if after.get("safeAimPointAvailable") is True:
        return True
    before_class = str(before.get("classification") or "")
    after_class = str(after.get("classification") or "")
    if before_class and after_class and before_class != after_class:
        if after_class not in {"projection_sentinel", "no_projection", "target_missing", "unknown"}:
            return True
    return before.get("projectionSentinel") is True and after.get("projectionSentinel") is False


def verify_expected_result(
    action: str,
    before_status: dict[str, Any] | None,
    after_status: dict[str, Any] | None,
    *,
    elapsed_ms: int | None = None,
    timeout_ms: int | None = None,
    wait_started_tick: int | None = None,
    timeout_ticks: int | None = None,
    progress_min_distance: float | None = None,
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
            observed["resourceProgressClassification"] = "resource_target_depleted_success"
            return _finish(observed, status="PASS", result="target_depleted", outcome="depleted", complete=True, next_allowed=True)
        if "held_resource_count_increased" in signals or "inventory_changed" in signals or "inventory_free_slots_changed" in signals:
            observed["resourceProgressClassification"] = "resource_delayed_inventory_success" if observed.get("elapsedMillis") else "resource_inventory_success"
            return _finish(observed, status="PASS", result="inventory_changed", outcome="success", complete=True, next_allowed=True)
        if "resource_progress_increased" in signals:
            observed["resourceProgressClassification"] = "resource_delayed_inventory_success" if observed.get("elapsedMillis") else "resource_progress_success"
            return _finish(observed, status="PASS", result="resource_progress_increased", outcome="progress", complete=True, next_allowed=True)
        if any(signal.startswith("activity_") for signal in signals):
            observed["resourceProgressClassification"] = "resource_animation_started_pending"
            return _finish(observed, status="PASS", result="activity_progress", outcome="progress", complete=True, next_allowed=True)
        if phase == "blocked" or _blocking_conditions(after):
            _add_signal(observed, "blocked_phase")
            return _finish(observed, status="FAIL", result="interrupted", outcome="interrupted", complete=True, next_allowed=False)
        if _bank_open(after) is True or _service_ready(after) is True:
            _add_signal(observed, "unexpected_service_context")
            return _finish(observed, status="FAIL", result="interrupted", outcome="interrupted", complete=True, next_allowed=False)
        if timed_out:
            observed["warnings"].append("resource result timed out without progress")
            observed["resourceProgressClassification"] = "resource_timeout_no_progress"
            return _finish(observed, status="FAIL", result="no_change_timeout", outcome="no_change_timeout", complete=True, next_allowed=False)
        if "wait_for_result_state" in signals:
            observed["resourceProgressClassification"] = "resource_click_confirmed_waiting"
            return _finish(observed, status="PASS", result="wait_for_result", outcome="still_waiting", complete=False, next_allowed=False)
        observed["warnings"].append("resource result not observed yet")
        observed["resourceProgressClassification"] = "resource_click_confirmed_waiting"
        return _finish(observed, status="WARN", result="waiting", outcome="still_waiting", complete=False, next_allowed=False)
    if action == "resource_view_recovery":
        before_projection = _target_resource_projection_state(before)
        after_projection = _target_resource_projection_state(after)
        observed["projectionBefore"] = before_projection
        observed["projectionAfter"] = after_projection
        observed["resourceProjectionRecovery"] = True
        if after_projection.get("safeAimPointAvailable") is True:
            _add_signal(observed, "resource_safe_aimpoint_available")
            observed["resourceProjectionRecoveryClassification"] = "resource_camera_reacquire_success"
            return _finish(observed, status="PASS", result="resource_camera_reacquire_success", outcome="progress", complete=True, next_allowed=True)
        if _projection_recovery_improved(before_projection, after_projection):
            _add_signal(observed, "resource_projection_improved")
            observed["resourceProjectionRecoveryClassification"] = "resource_projection_improved"
            return _finish(observed, status="PASS", result="resource_projection_improved", outcome="progress", complete=True, next_allowed=True)
        if timed_out:
            observed["warnings"].append(
                "resource projection recovery did not produce a safe aim point or improved projection"
            )
            observed["resourceProjectionRecoveryClassification"] = "resource_projection_recovery_failed"
            return _finish(observed, status="FAIL", result="resource_projection_recovery_failed", outcome="no_change_timeout", complete=True, next_allowed=False)
        observed["warnings"].append("resource projection recovery waiting for refreshed projection")
        observed["resourceProjectionRecoveryClassification"] = "resource_projection_recovery_waiting"
        return _finish(observed, status="WARN", result="resource_projection_recovery_waiting", outcome="still_waiting", complete=False, next_allowed=False)
    if action == "service_view_recovery":
        before_projection = _target_service_projection_state(before)
        after_projection = _target_service_projection_state(after)
        liveness_after = _loaded_scene_problem(after)
        after_proposal_action = None
        after_proposal_reason = None
        after_proposal_target = None
        try:
            after_proposal = build_action_proposal(after)
            after_proposal_action = after_proposal.proposed_action
            after_proposal_reason = after_proposal.reason
            after_proposal_target = after_proposal.target_name
        except Exception as error:  # noqa: BLE001
            observed["warnings"].append(f"post-camera service proposal rebuild failed: {type(error).__name__}: {error}")
        observed["projectionBefore"] = before_projection
        observed["projectionAfter"] = after_projection
        observed["postCameraProposal"] = {
            "proposedAction": after_proposal_action,
            "reason": after_proposal_reason,
            "targetName": after_proposal_target,
        }
        observed["livenessAfter"] = liveness_after
        observed["serviceViewRecovery"] = True
        if liveness_after.get("blocking"):
            _add_signal(observed, "loaded_scene_unavailable")
            observed["serviceViewRecoveryClassification"] = "service_view_recovery_liveness_lost"
            observed["warnings"].append(
                "service view recovery cannot be verified because the loaded game scene is no longer available"
            )
            return _finish(observed, status="FAIL", result="service_view_recovery_liveness_lost", outcome="interrupted", complete=True, next_allowed=False)
        post_camera_still_recovery = after_proposal_action == "service_view_recovery"
        if after_projection.get("usableExposureAvailable") is True and not post_camera_still_recovery:
            _add_signal(observed, "service_usable_exposure_available")
            _add_signal(observed, "service_safe_aimpoint_available")
            observed["serviceViewRecoveryClassification"] = "service_camera_reacquire_success"
            return _finish(observed, status="PASS", result="service_camera_reacquire_success", outcome="progress", complete=True, next_allowed=True)
        if after_projection.get("usableExposureAvailable") is True and post_camera_still_recovery:
            _add_signal(observed, "service_usable_exposure_available")
            observed["serviceViewRecoveryClassification"] = "service_recovery_still_required"
            observed["warnings"].append("post-camera proposal still requires service_view_recovery")
            if timed_out:
                return _finish(observed, status="FAIL", result="service_recovery_still_required", outcome="no_change_timeout", complete=True, next_allowed=False)
            return _finish(observed, status="WARN", result="service_recovery_still_required", outcome="still_waiting", complete=False, next_allowed=False)
        if after_projection.get("safeAimPointAvailable") is True:
            _add_signal(observed, "service_safe_aimpoint_available")
            if after_projection.get("edgeSliverVisible") is True:
                _add_signal(observed, "service_edge_sliver_only")
            observed["serviceViewRecoveryClassification"] = "service_insufficient_exposure"
            observed["warnings"].append("service target has a safe aimpoint but is not comfortably exposed")
            if timed_out:
                observed["serviceViewRecoveryPartialProgress"] = True
                observed["warnings"].append(
                    "service camera recovery improved the target view; another bounded camera primitive is allowed"
                )
                return _finish(observed, status="WARN", result="service_insufficient_exposure", outcome="progress", complete=True, next_allowed=True)
            return _finish(observed, status="WARN", result="service_insufficient_exposure", outcome="still_waiting", complete=False, next_allowed=False)
        if _projection_recovery_improved(before_projection, after_projection):
            _add_signal(observed, "service_projection_improved")
            observed["serviceViewRecoveryClassification"] = "service_projection_improved_insufficient_exposure"
            if timed_out:
                observed["serviceViewRecoveryPartialProgress"] = True
                observed["warnings"].append(
                    "service camera recovery improved projection but exposure is still below policy"
                )
                return _finish(observed, status="WARN", result="service_projection_improved_insufficient_exposure", outcome="progress", complete=True, next_allowed=True)
            return _finish(observed, status="WARN", result="service_projection_improved_insufficient_exposure", outcome="still_waiting", complete=False, next_allowed=False)
        if timed_out:
            observed["warnings"].append("service view recovery did not expose a safe service click point")
            observed["serviceViewRecoveryClassification"] = "service_view_recovery_failed"
            return _finish(observed, status="FAIL", result="service_view_recovery_failed", outcome="no_change_timeout", complete=True, next_allowed=False)
        observed["warnings"].append("service view recovery waiting for refreshed projection")
        observed["serviceViewRecoveryClassification"] = "service_view_recovery_waiting"
        return _finish(observed, status="WARN", result="service_view_recovery_waiting", outcome="still_waiting", complete=False, next_allowed=False)
    if action == "open_service":
        bank_open = _bank_open(after)
        if bank_open is True or _bool(context_value(after, "bankUiContext", "bankRootVisible", "bankRootVisible")) is True:
            _add_signal(observed, "bank_open")
            _add_signal(observed, "bank_ui_opened")
            return _finish(observed, status="PASS", result="service_open", outcome="success", complete=True, next_allowed=True)
        min_distance = 0.0 if progress_min_distance is None else max(0.0, float(progress_min_distance))
        before_metrics = _navigation_metrics(before)
        after_metrics = _navigation_metrics(after)
        decreased_metrics: list[str] = []
        changed_metrics: list[str] = []
        for name, before_metric in before_metrics.items():
            after_metric = after_metrics.get(name)
            if after_metric is None:
                continue
            if after_metric != before_metric:
                changed_metrics.append(name)
                _add_signal(observed, f"{name}_changed")
            if before_metric - after_metric >= min_distance and after_metric < before_metric:
                decreased_metrics.append(name)
                _add_signal(observed, f"{name}_decreased")
        before_tile = _player_tile_key(before)
        after_tile = _player_tile_key(after)
        if before_tile is not None and after_tile is not None and after_tile != before_tile:
            _add_signal(observed, "player_tile_changed")
        before_node = _route_node_id(before)
        after_node = _route_node_id(after)
        if before_node is not None and after_node is not None and after_node != before_node:
            _add_signal(observed, "route_node_changed")
            observed["routeNodeBefore"] = before_node
            observed["routeNodeAfter"] = after_node
        before_step_status = _route_step_status(before)
        after_step_status = _route_step_status(after)
        if before_step_status is not None and after_step_status is not None and after_step_status != before_step_status:
            _add_signal(observed, "route_step_status_changed")
            observed["routeStepStatusBefore"] = before_step_status
            observed["routeStepStatusAfter"] = after_step_status
        if _service_ready(after) is True:
            _add_signal(observed, "service_object_ready")
        if is_waiting_for_result(after) or str(intent or "").startswith(("open", "service", "bank", "navigate", "move")):
            _add_signal(observed, "movement_or_wait_state")
        signals = set(observed["observedSignals"])
        if "player_tile_changed" in signals or "route_node_changed" in signals or "route_step_status_changed" in signals or decreased_metrics:
            observed["warnings"].append("service object click is pathing toward service target; waiting for bank UI")
            return _finish(observed, status="WARN", result="service_object_pathing_to_object", outcome="still_waiting", complete=False, next_allowed=False)
        if "movement_or_wait_state" in signals or changed_metrics:
            if timed_out:
                observed["warnings"].append("service object click did not show movement or bank UI before timeout")
                return _finish(observed, status="FAIL", result="service_object_no_progress", outcome="no_change_timeout", complete=True, next_allowed=False)
            observed["warnings"].append("service object click confirmed; waiting for pathing or bank UI")
            return _finish(observed, status="WARN", result="service_object_click_confirmed", outcome="still_waiting", complete=False, next_allowed=False)
        if timed_out:
            observed["warnings"].append("bank UI did not open before timeout")
            return _finish(observed, status="FAIL", result="service_object_no_progress", outcome="no_change_timeout", complete=True, next_allowed=False)
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
        min_distance = 0.0 if progress_min_distance is None else max(0.0, float(progress_min_distance))
        before_metrics = _navigation_metrics(before)
        after_metrics = _navigation_metrics(after)
        decreased_metrics: list[str] = []
        changed_metrics: list[str] = []
        for name, before_metric in before_metrics.items():
            after_metric = after_metrics.get(name)
            if after_metric is None:
                continue
            if after_metric != before_metric:
                changed_metrics.append(name)
                _add_signal(observed, f"{name}_changed")
            if before_metric - after_metric >= min_distance and after_metric < before_metric:
                decreased_metrics.append(name)
                _add_signal(observed, f"{name}_decreased")
        before_tile = _player_tile_key(before)
        after_tile = _player_tile_key(after)
        if before_tile is not None and after_tile is not None and after_tile != before_tile:
            _add_signal(observed, "player_tile_changed")
        before_node = _route_node_id(before)
        after_node = _route_node_id(after)
        if before_node is not None and after_node is not None and after_node != before_node:
            _add_signal(observed, "route_node_changed")
            observed["routeNodeBefore"] = before_node
            observed["routeNodeAfter"] = after_node
        before_step_index = _route_step_index(before)
        after_step_index = _route_step_index(after)
        if before_step_index is not None and after_step_index is not None and after_step_index != before_step_index:
            _add_signal(observed, "route_step_index_changed")
            observed["routeStepIndexBefore"] = before_step_index
            observed["routeStepIndexAfter"] = after_step_index
        before_step_status = _route_step_status(before)
        after_step_status = _route_step_status(after)
        if before_step_status is not None and after_step_status is not None and after_step_status != before_step_status:
            _add_signal(observed, "route_step_status_changed")
            observed["routeStepStatusBefore"] = before_step_status
            observed["routeStepStatusAfter"] = after_step_status
        if _service_ready(after) is True:
            _add_signal(observed, "service_ready")
        if _service_route_action_ready(after) is True:
            _add_signal(observed, "route_object_reacquired")
        if _bool(context_value(after, "returnToResourceContext", "resourceTargetAvailable", "returnResourceTargetAvailable")) is True:
            _add_signal(observed, "resource_target_visible")
        if is_waiting_for_result(after) or str(intent or "").startswith(("navigate", "return", "move")):
            _add_signal(observed, "movement_or_wait_state")
        signals = set(observed["observedSignals"])
        if action == "navigate_to_service":
            if "service_ready" in signals:
                return _finish(observed, status="PASS", result="service_navigation_reached_node", outcome="progress", complete=True, next_allowed=True)
            if "route_object_reacquired" in signals:
                return _finish(observed, status="PASS", result="service_route_object_reacquired", outcome="progress", complete=True, next_allowed=True)
            if "player_tile_changed" in signals or "route_node_changed" in signals or "route_step_index_changed" in signals or decreased_metrics:
                return _finish(observed, status="PASS", result="service_navigation_progress", outcome="progress", complete=True, next_allowed=True)
            if "movement_or_wait_state" in signals or changed_metrics:
                if timed_out:
                    observed["warnings"].append("service navigation did not show tile movement or distance improvement before timeout")
                    return _finish(observed, status="FAIL", result="service_navigation_no_progress", outcome="no_change_timeout", complete=True, next_allowed=False)
                return _finish(observed, status="WARN", result="service_navigation_clicked_waiting", outcome="still_waiting", complete=False, next_allowed=False)
        else:
            if "resource_target_visible" in signals:
                return _finish(observed, status="PASS", result="resource_return_reached_node", outcome="progress", complete=True, next_allowed=True)
            if "player_tile_changed" in signals or decreased_metrics:
                return _finish(observed, status="PASS", result="resource_return_progress", outcome="progress", complete=True, next_allowed=True)
            if "movement_or_wait_state" in signals or changed_metrics:
                if timed_out:
                    observed["warnings"].append("resource return did not show tile movement or distance improvement before timeout")
                    return _finish(observed, status="FAIL", result="resource_return_no_progress", outcome="no_change_timeout", complete=True, next_allowed=False)
                return _finish(observed, status="WARN", result="resource_return_clicked_waiting", outcome="still_waiting", complete=False, next_allowed=False)
        if timed_out:
            observed["warnings"].append("movement result timed out")
            result_name = "service_navigation_stuck" if action == "navigate_to_service" else "resource_return_stuck"
            return _finish(observed, status="FAIL", result=result_name, outcome="no_change_timeout", complete=True, next_allowed=False)
        observed["warnings"].append("movement result not observed yet")
        return _finish(observed, status="WARN", result="waiting", outcome="still_waiting", complete=False, next_allowed=False)
    if action in {"interact_service_route_object", "interface_dialogue_choice"}:
        is_return_transition = _is_return_transition(before, after)
        before_plane = _player_plane(before)
        after_plane = _player_plane(after)
        if before_plane is not None and after_plane is not None and before_plane != after_plane:
            _add_signal(observed, "player_plane_changed")
        before_tile = _player_tile_key(before)
        after_tile = _player_tile_key(after)
        if before_tile is not None and after_tile is not None and before_tile != after_tile:
            _add_signal(observed, "player_position_changed")
        before_step = _transition_route_step_index(before)
        after_step = _transition_route_step_index(after)
        if before_step is not None and after_step is not None and before_step != after_step:
            _add_signal(observed, "route_step_changed")
        if _service_ready(after) is True:
            _add_signal(observed, "service_ready")
        dialogue_state = _context(after, "dialogueState")
        if action == "interact_service_route_object" and isinstance(dialogue_state, dict) and dialogue_state.get("active") is True:
            _add_signal(observed, "route_transition_dialogue_opened")
        before_destination = _local_destination_key(before)
        after_destination = _local_destination_key(after)
        if after_destination is not None and after_destination != before_destination:
            _add_signal(observed, "local_destination_changed")
            observed["localDestinationBefore"] = {"worldX": before_destination[0], "worldY": before_destination[1], "plane": before_destination[2]} if before_destination else None
            observed["localDestinationAfter"] = {"worldX": after_destination[0], "worldY": after_destination[1], "plane": after_destination[2]}
        if _pathing_movement_active(after):
            _add_signal(observed, "pathing_started")
        if is_waiting_for_result(after):
            _add_signal(observed, "movement_or_wait_state")
        if observed["observedSignals"]:
            signal_set = set(observed["observedSignals"])
            if action == "interact_service_route_object" and "route_transition_dialogue_opened" in signal_set:
                result_name = "return_transition_dialogue_opened" if is_return_transition else "route_transition_dialogue_opened"
                return _finish(observed, status="PASS", result=result_name, outcome="progress", complete=True, next_allowed=True)
            if action == "interface_dialogue_choice":
                result_name = "return_transition_dialogue_choice_selected" if is_return_transition else "route_transition_dialogue_choice_selected"
            elif is_return_transition and "player_plane_changed" in signal_set:
                result_name = "return_transition_plane_changed"
            elif "player_plane_changed" in signal_set or "route_step_changed" in signal_set or "service_ready" in signal_set:
                result_name = "route_transition_progress"
            elif signal_set.intersection({"player_position_changed", "local_destination_changed", "pathing_started", "movement_or_wait_state"}):
                result_name = "return_transition_pending" if is_return_transition else "route_transition_pending"
                observed["routeTransitionProgressClassification"] = result_name
                observed["warnings"].append("route transition has pending pathing evidence; waiting before retry")
                return _finish(observed, status="WARN", result=result_name, outcome="still_waiting", complete=False, next_allowed=False)
            else:
                result_name = "route_transition_progress"
            return _finish(observed, status="PASS", result=result_name, outcome="progress", complete=True, next_allowed=True)
        if timed_out:
            if action == "interface_dialogue_choice":
                observed["warnings"].append("dialogue choice did not produce a plane, location, or route-step change before timeout")
            else:
                observed["warnings"].append("route object interaction did not produce a plane, location, or route-step change before timeout")
            return _finish(observed, status="FAIL", result="no_change_timeout", outcome="no_change_timeout", complete=True, next_allowed=False)
        if action == "interface_dialogue_choice":
            observed["warnings"].append("dialogue choice result not observed yet")
        else:
            observed["warnings"].append("route object interaction result not observed yet")
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
    timeout_ticks: int | None = None,
    progress_min_distance: float | None = None,
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
            timeout_ticks=timeout_ticks,
            progress_min_distance=progress_min_distance,
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

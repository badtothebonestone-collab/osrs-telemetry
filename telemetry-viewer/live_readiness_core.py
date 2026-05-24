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
        return "run bootstrap/login helper, then restart/rebind daemon"
    if stale_reason == "plugin_endpoint_not_reachable":
        return "restore RuneLite PluginSnapshotEndpoint, then restart/rebind daemon"
    if stale_reason == "plugin_snapshot_no_packets":
        return "wait for live packets after login or restart RuneLite/daemon"
    if stale_reason == "plugin_hot_state_not_advancing":
        return "refocus RuneLite or restart the plugin/daemon if client ticks do not advance"
    if stale_reason == "daemon_snapshot_not_refreshing":
        return "restart/rebind the daemon to the current plugin session"
    return "wait for fresh client tick/menu samples or refocus/restart RuneLite"


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
    available = bool(hot) and bool(hover)
    fresh_by_age = bool(available and age_millis is not None and age_millis <= CLIENT_TICK_HOT_MAX_AGE_MILLIS)
    stale_reason = _hot_stale_reason(
        status,
        available=available,
        fresh_by_age=fresh_by_age,
        game_state=game_state,
        is_logged_in=is_logged_in,
    )
    fresh = bool(fresh_by_age and stale_reason is None)
    return {
        "available": available,
        "fresh": fresh,
        "clientTickHotFresh": fresh,
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


def _status_from_parts(*, blockers: list[Any] | None = None, warnings: list[Any] | None = None) -> str:
    if blockers:
        return "FAIL"
    if warnings:
        return "WARN"
    return "PASS"


def _status_payload_unavailable(error: Exception) -> dict[str, Any]:
    message = f"daemon status unavailable: {type(error).__name__}: {error}"
    blockers = [_blocker("daemon_status_unavailable", message, action="start or restart live_core_daemon.py")]
    payload = {
        "schema": SCHEMA,
        "status": "FAIL",
        "ready": False,
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
            "checks": {"daemonStatus": False},
        },
        "freshness": {},
        "inputGeometry": {},
        "actionExecution": {"allowed": False, "refusalReason": "daemon_status_unavailable"},
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
    blockers: list[dict[str, Any]] = []
    action_warnings: list[str] = []
    missing: list[str] = []
    daemon_reachable = True

    if daemon_status is None:
        try:
            status = fetch_json_func(daemon_status_url(daemon_url), timeout=timeout)
        except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as error:
            return _status_payload_unavailable(error)
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
    snapshot_failures = [
        warning
        for warning in status_warnings
        if "plugin snapshot request failed" in warning.lower()
        or "snapshot endpoint unreachable" in warning.lower()
        or "plugin snapshot endpoint" in warning.lower() and "failed" in warning.lower()
    ]
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
    proposal_target = _dict(proposal_payload.get("targetExplanation"))
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
            warnings.append("no session with live overlay/candidate outputs was found")
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
            warnings.append("newest session differs from daemon session, but newest live-output session matches daemon")
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

    if resource_target_required:
        if not overlay_exists:
            blockers.append(
                _blocker(
                    "debug_overlay_json_missing",
                    f"debug overlay JSON missing: {path_text(overlay_path)}",
                    action="start daemon with --write-overlay-state and wait for overlay_debug_state.json",
                )
            )
            missing.append("overlay_debug_state.json")
        if not markers:
            blockers.append(
                _blocker(
                    "highlighter_source_not_ready",
                    "highlighter source has no selected/candidate markers yet",
                    action="wait for overlay markers or restart the live target/daemon stack",
                )
            )
            missing.append("highlighter.markers")
        if not selected_target:
            blockers.append(_blocker("selected_target_missing", "daemon has no selected resource target", action="stand near valid Tree/Oak candidates and wait for target selection"))
            missing.append("target.selected")
        elif markers and not matched_target:
            blockers.append(
                _blocker(
                    "selected_target_not_in_highlighter_source",
                    "daemon selected target is not present in the highlighter/overlay source",
                    action="wait for synchronized target/overlay output or restart daemon",
                )
            )
            missing.append("target.highlighterMatch")

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
        if not proposal.executable:
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
    elif action not in {"none", "wait_for_context"} and not proposal.executable:
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
        warnings.append(
            "; ".join(str(reason) for reason in freshness.get("staleReasons") or []) or "resource candidate data is stale for non-resource intent"
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
        warnings.extend(candidate_warnings)

    warnings_unique = list(dict.fromkeys(warnings))
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
    if current_intent != "navigation_waypoint_action":
        checks_skipped.extend(["route.waypoint", "navigation.intent"])
    action_blockers = list(blockers)
    action_status = _status_from_parts(blockers=action_blockers, warnings=action_warnings)
    action_execution_allowed = action_status != "FAIL"
    context_status = _status_from_parts(warnings=warnings_unique)
    status_value = "FAIL" if action_status == "FAIL" else "WARN" if warnings or action_warnings else "PASS"
    ready = action_execution_allowed
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
    selected_actionable = bool(proposal.executable) if action in RESOURCE_TARGET_ACTIONS else None
    selected_safe_aimpoint = (
        _dict(_dict(proposal_payload.get("targetExplanation")).get("safeAimPoint"))
        or _dict(_dict(selected_summary or {}).get("safeAimPoint"))
        or _dict(_dict(selected_highlighter_summary or {}).get("safeAimPoint"))
    )
    return {
        "schema": SCHEMA,
        "status": status_value,
        "ready": ready,
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
            "actionable": selected_actionable,
            "safeAimPointStatus": selected_safe_aimpoint.get("status") if selected_safe_aimpoint else None,
            "uiBlocked": _target_check_value(selected_target, matched_target, "uiBlocked") if selected_target or matched_target else None,
            "stale": freshness.get("stale"),
        },
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
                "inputGeometryAvailable": bool(input_geometry.get("inputGeometryAvailable")),
                "clientTickHotRequired": client_tick_hot_required,
                "clientTickHotAvailable": client_tick_hot["available"] if client_tick_hot_required else None,
                "clientTickHotFresh": client_tick_hot["fresh"] if client_tick_hot_required else None,
                "clientTickHotAgeMillis": client_tick_hot.get("ageMillis"),
                "clientTickHotMaxAgeMillis": client_tick_hot.get("maxAgeMillis") if client_tick_hot_required else None,
                "clientTickHotStaleReason": client_tick_hot.get("staleReason"),
                "gameState": client_tick_hot.get("gameState"),
                "isLoggedIn": client_tick_hot.get("isLoggedIn"),
                "resourceTargetRequired": resource_target_required,
                "resourceProjectionRecoveryRequired": resource_recovery_required,
                "selectedTargetInHighlighterSource": bool(matched_target) if resource_target_required else None,
                "navigationWaypointRequired": navigation_target_required,
                "navigationWaypointAvailable": isinstance(proposal.target_tile, dict) if navigation_target_required else None,
                "proposalExecutable": proposal.executable,
            },
            "checksSkippedAsNotApplicable": list(dict.fromkeys(checks_skipped)),
            "missingCapabilities": missing_unique,
        },
        "contextReadiness": {
            "status": context_status,
            "warnings": warnings_unique,
            "checks": {
                "selectedResourceTargetPresent": bool(selected_target),
                "selectedResourceTargetInHighlighterSource": bool(matched_target) if markers else False,
                "highlighterMarkerCount": len(markers),
                "candidateFreshness": freshness.get("targetCandidateFreshness"),
            },
        },
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
            },
            "overlayDebug": {
                "required": resource_target_required,
                "available": overlay_exists,
                "path": path_text(overlay_path),
                "markerCount": len(markers),
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
        "warnings": warnings_unique,
        "missingCapabilities": missing_unique,
    }

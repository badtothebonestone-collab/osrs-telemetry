from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

import run_runelite_bootstrap as bootstrap


SCHEMA = "liveness_recovery_result.v1"
STATE_SCHEMA = "liveness_state.v1"
LOADED_SCENE_PROOF_SCHEMA = "loaded_scene_proof.v1"
CACHE_TTL_SECONDS = 5.0
CLIENT_TICK_HOT_MAX_AGE_MS = 1000
LOADED_SCENE_STABILITY_SECONDS = 3.0

RECOVERABLE_STATES = {
    "plugin_endpoint_down",
    "runelite_not_running",
    "stale_snapshot_no_packets",
    "stale_logged_in_no_scene",
    "disconnected_dialog",
    "saved_account_play_now",
    "click_here_to_play",
    "login_screen",
    "loading",
    "daemon_down",
    "daemon_stale",
}

MANUAL_STATES = {"credential_required", "login_screen"}
SAFE_VISIBLE_BUTTON_NAMES = {"disconnected_ok", "play_now", "click_here_to_play", "continue"}
BUTTON_EXPECTED_NEXT_STATES = {
    "disconnected_ok": ["login_screen", "saved_account_play_now", "click_here_to_play", "loading", "loaded_scene"],
    "play_now": ["logging_in", "loading", "click_here_to_play", "loaded_scene"],
    "click_here_to_play": ["logged_in", "loaded_scene"],
    "continue": ["login_screen", "saved_account_play_now", "click_here_to_play", "loading", "loaded_scene"],
}

_LAST_SUCCESS: dict[str, Any] | None = None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _expected_next_states_for_button(name: str) -> list[str]:
    return list(BUTTON_EXPECTED_NEXT_STATES.get(str(name or ""), ["unknown"]))


def _state_after_for_button_name(name: str) -> str | None:
    if name == "disconnected_ok":
        return "login_screen"
    if name == "play_now":
        return "loading"
    if name == "click_here_to_play":
        return "loaded_scene"
    if name == "continue":
        return "loading"
    return None


def _click_value(click: dict[str, Any], key: str) -> Any:
    if key in click:
        return click.get(key)
    details = _dict(click.get("clickDetails"))
    return details.get(key)


def _click_has_proof_fields(click: dict[str, Any]) -> bool:
    details = _dict(click.get("clickDetails"))
    for key in (
        "fullClickSequenceVerified",
        "mouseMoveSent",
        "mouseDownSent",
        "mouseUpSent",
        "clickSent",
        "cursorTargetDistance",
        "cursorAtTarget",
        "windowFocusVerified",
    ):
        if key in click or key in details:
            return True
    return False


def _click_target_valid(click: dict[str, Any]) -> bool:
    status = str(click.get("targetValidationStatus") or _click_value(click, "targetValidationStatus") or "").upper()
    if status:
        return status != "FAIL"
    if not _click_has_proof_fields(click):
        return True
    inside_window = click.get("targetInsideRuneLiteWindow")
    inside_safe = click.get("targetInsideSafeClickRegion")
    if inside_window is not None or inside_safe is not None:
        return bool(inside_window and inside_safe)
    return True


def _click_window_focused(click: dict[str, Any]) -> bool | None:
    focused = _click_value(click, "windowFocusVerified")
    if focused is not None:
        return bool(focused)
    details = _dict(click.get("clickDetails"))
    pre_focus = _dict(details.get("preClickFocus"))
    if "focused" in pre_focus:
        return bool(pre_focus.get("focused"))
    return None


def _click_proof_blocker(click: dict[str, Any]) -> str | None:
    if not _click_target_valid(click):
        return "recovery_click_target_invalid"
    if not _click_has_proof_fields(click):
        return None
    focused = _click_window_focused(click)
    if focused is False:
        return "recovery_click_window_not_focused"
    cursor_at_target = _click_value(click, "cursorAtTarget")
    if cursor_at_target is False:
        return "visible_button_click_not_grounded"
    full_sequence = _click_value(click, "fullClickSequenceVerified")
    if full_sequence is False:
        click_sent = bool(_click_value(click, "clickSent"))
        mouse_down = bool(_click_value(click, "mouseDownSent"))
        mouse_up = bool(_click_value(click, "mouseUpSent"))
        if not click_sent and (not mouse_down or not mouse_up):
            return "incomplete_click_sequence"
        return "visible_button_click_not_grounded"
    return None


def _expected_transition_satisfied(click: dict[str, Any], state_after: str, final_state: dict[str, Any]) -> bool:
    explicit = click.get("expectedTransitionSatisfied")
    if explicit is not None:
        return bool(explicit)
    name = str(click.get("name") or "")
    expected = set(_list(click.get("expectedNextStates")) or _expected_next_states_for_button(name))
    if state_after in expected:
        return True
    proof = _dict(final_state.get("loadedSceneProof"))
    return bool(proof.get("loadedSceneVerified") and "loaded_scene" in expected)


def _state_name(state: dict[str, Any] | None) -> str:
    return str(_dict(state).get("state") or "unknown")


def _int(value: Any) -> int | None:
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


def _now_ms(monotonic_func: Callable[[], float]) -> int:
    return int(float(monotonic_func()) * 1000.0)


def _snapshot_url(snapshot_url: str) -> str:
    text = str(snapshot_url or "").strip() or "http://127.0.0.1:8893"
    return text if text.endswith("/snapshot") else text.rstrip("/") + "/snapshot"


def _daemon_status_url(daemon_url: str) -> str:
    return str(daemon_url or "http://127.0.0.1:8890").rstrip("/") + "/status"


def _daemon_health_url(daemon_url: str) -> str:
    return str(daemon_url or "http://127.0.0.1:8890").rstrip("/") + "/health"


def fetch_daemon_status(daemon_url: str, *, timeout: float = 1.0) -> dict[str, Any]:
    status_error: Exception | None = None
    try:
        with urllib.request.urlopen(_daemon_status_url(daemon_url), timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8", errors="replace"))
        payload = decoded if isinstance(decoded, dict) else {}
        payload.setdefault("daemonEndpoint", "status")
        return payload
    except Exception as error:  # noqa: BLE001
        status_error = error
    try:
        with urllib.request.urlopen(_daemon_health_url(daemon_url), timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8", errors="replace"))
        payload = decoded if isinstance(decoded, dict) else {}
        payload["daemonEndpoint"] = "health"
        payload["statusEndpointError"] = f"{type(status_error).__name__}: {status_error}"
        return payload
    except Exception:
        if status_error is not None:
            raise status_error
        raise


def _client_tick_age_from_hot(hot: dict[str, Any]) -> int | None:
    latency = _dict(hot.get("latency"))
    for key in ("ageMillis", "postMenuSortAgeMillis", "latestPostMenuSortAgeMillis"):
        parsed = _int(latency.get(key) if key in latency else hot.get(key))
        if parsed is not None:
            return parsed
    return None


def _client_tick_fresh_from_hot(hot: dict[str, Any], *, max_age_ms: int = CLIENT_TICK_HOT_MAX_AGE_MS) -> tuple[bool, int | None]:
    if not hot:
        return False, None
    source = str(hot.get("sourceEvent") or hot.get("sampleSource") or "").strip().lower()
    if source == "gamestatechanged":
        return False, _client_tick_age_from_hot(hot)
    age = _client_tick_age_from_hot(hot)
    if age is None:
        return False, None
    return age <= max_age_ms, age


def loaded_scene_proof(snapshot_payload: dict[str, Any] | None, *, reachable: bool) -> dict[str, Any]:
    payload = snapshot_payload if isinstance(snapshot_payload, dict) else {}
    summary = bootstrap.snapshot_summary(payload, reachable=reachable)
    hot = bootstrap.snapshot_client_tick_hot(payload)
    client_tick_fresh, client_tick_age = _client_tick_fresh_from_hot(hot)
    payloads = _dict(payload.get("payloads"))
    baseline = _dict(payloads.get("baseline"))
    baseline_source = _dict(baseline.get("source"))
    source_scene_objects = _int(
        baseline_source.get("sceneObjectsSeen")
        or baseline_source.get("sceneObjectsCaptured")
        or baseline_source.get("objectTotal")
    )
    object_total = _int(summary.get("worldModelObjectTotal"))
    if object_total is None and source_scene_objects is not None:
        object_total = source_scene_objects
    source_scene_knowledge = baseline_source.get("sourceSceneKnowledgeComplete") is True
    world_model_available = bool(summary.get("worldModelAvailable")) or bool(
        source_scene_knowledge and object_total is not None and object_total > 0
    )
    latest_tick = _int(summary.get("latestTick"))
    latest_export_seq = _int(payload.get("latestExportSeq") or payload.get("exportSeq"))
    loaded = bool(
        summary.get("snapshotReachable")
        and str(summary.get("gameState") or "").upper() == "LOGGED_IN"
        and latest_tick is not None
        and summary.get("baselinePresent") is True
        and world_model_available
        and object_total is not None
        and object_total > 0
        and summary.get("clientTickHotPresent") is True
        and client_tick_fresh
        and not summary.get("finalPlayPanelPending")
    )
    return {
        "schema": LOADED_SCENE_PROOF_SCHEMA,
        "loadedSceneVerified": loaded,
        "snapshotReachable": bool(summary.get("snapshotReachable")),
        "snapshotFresh": reachable and latest_tick is not None,
        "gameState": summary.get("gameState"),
        "latestTick": latest_tick,
        "latestExportSeq": latest_export_seq,
        "baselinePresent": bool(summary.get("baselinePresent")),
        "clientTickHotPresent": bool(summary.get("clientTickHotPresent")),
        "clientTickHotFresh": client_tick_fresh,
        "clientTickHotAgeMillis": client_tick_age,
        "clientTickHotMaxAgeMillis": CLIENT_TICK_HOT_MAX_AGE_MS,
        "worldModelAvailable": world_model_available,
        "worldModelObjectTotal": object_total,
        "sourceSceneKnowledgeComplete": source_scene_knowledge,
        "sourceSceneObjectsSeen": source_scene_objects,
        "finalPlayPanelPending": bool(summary.get("finalPlayPanelPending")),
        "staleLoggedInNoScene": bool(summary.get("staleLoggedInNoScene")),
        "screenClassification": summary.get("screenClassification"),
    }


def _daemon_summary(daemon_status: dict[str, Any] | None, daemon_error: str | None = None) -> dict[str, Any]:
    status = daemon_status if isinstance(daemon_status, dict) else {}
    hot = _dict(status.get("clientTickHot"))
    hot_fresh, hot_age = _client_tick_fresh_from_hot(hot)
    latest_tick = _int(status.get("latestTick") or _dict(status.get("brain")).get("latestTick"))
    session_path = status.get("sessionPath")
    reachable = bool(status) and not daemon_error
    fresh = bool(reachable and latest_tick is not None and session_path and status.get("status") != "FAIL")
    return {
        "reachable": reachable,
        "fresh": fresh,
        "status": status.get("status"),
        "latestTick": latest_tick,
        "sessionPath": session_path,
        "clientTickHotFresh": hot_fresh,
        "clientTickHotAgeMillis": hot_age,
        "error": daemon_error,
    }


def classify_state(
    *,
    snapshot_payload: dict[str, Any] | None = None,
    snapshot_error: str | None = None,
    daemon_status: dict[str, Any] | None = None,
    daemon_error: str | None = None,
    window: dict[str, Any] | None = None,
    candidates: list[Any] | None = None,
) -> dict[str, Any]:
    reachable = snapshot_payload is not None and not snapshot_error
    summary = bootstrap.snapshot_summary(snapshot_payload, reachable=reachable, error=snapshot_error)
    candidate_objects = [item for item in (candidates or []) if item is not None]
    summary = bootstrap.apply_visual_loaded_scene_veto(summary, candidate_objects, [])
    proof = loaded_scene_proof(snapshot_payload, reachable=reachable)
    if summary.get("visualBootstrapSurfacePresent"):
        proof["loadedSceneVerified"] = False
        proof["finalPlayPanelPending"] = bool(summary.get("finalPlayPanelPending"))
    daemon = _daemon_summary(daemon_status, daemon_error)
    bootstrap_state = bootstrap.bootstrap_state_from_signals(summary=summary, window=window or {}, candidates=candidate_objects)
    state = str(bootstrap_state.get("state") or "unknown")
    if not reachable:
        state = "plugin_endpoint_down"
    if proof["loadedSceneVerified"]:
        if not daemon["reachable"]:
            state = "daemon_down"
        elif not daemon["fresh"]:
            state = "daemon_stale"
        else:
            state = "loaded_scene"
    elif state == "runelite_not_running":
        pass
    elif state in {"disconnected_dialog", "saved_account_play_now", "click_here_to_play", "credential_required"}:
        pass
    elif summary.get("staleLoggedInNoScene"):
        state = "stale_logged_in_no_scene"
    elif str(summary.get("gameState") or "").upper() == "LOGIN_SCREEN":
        state = "login_screen"
    elif summary.get("loggedIn"):
        state = "loading"
    elif reachable and not summary.get("clientTickHotPresent") and summary.get("baselinePresent"):
        state = "stale_snapshot_no_packets"
    known_recoverable = state in RECOVERABLE_STATES
    manual = state in MANUAL_STATES or state == "credential_required"
    unknown = state == "unknown"
    if state == "credential_required":
        next_step = "manual login/account action required"
        blocker = "manual_login_required"
    elif unknown:
        next_step = "capture one bounded screenshot/debug bundle and inspect the screen"
        blocker = "unknown_screen"
    elif state in {"daemon_down", "daemon_stale"}:
        next_step = "start or rebind live_core_daemon.py on 8890"
        blocker = "daemon_rebind_required"
    elif state == "loaded_scene":
        next_step = "continue"
        blocker = None
    elif known_recoverable:
        next_step = "run ensure_loaded_scene recovery ladder"
        blocker = None
    else:
        next_step = "manual login required if no safe saved-account surface is visible"
        blocker = "manual_login_required" if manual else None
    return {
        "schema": STATE_SCHEMA,
        "state": state,
        "snapshotReachable": reachable,
        "snapshotFresh": bool(proof.get("snapshotFresh")),
        "daemonReachable": daemon["reachable"],
        "daemonFresh": daemon["fresh"],
        "clientTickHotFresh": bool(proof.get("clientTickHotFresh")),
        "worldModelObjectTotal": proof.get("worldModelObjectTotal"),
        "currentSessionPath": daemon.get("sessionPath"),
        "loadedSceneVerified": bool(proof.get("loadedSceneVerified")),
        "loadedSceneProof": proof,
        "daemon": daemon,
        "bootstrapState": bootstrap_state,
        "detectedButtons": [item.to_dict() if hasattr(item, "to_dict") else item for item in candidate_objects],
        "knownRecoverableState": known_recoverable,
        "manualLoginRequired": manual,
        "unknownScreen": unknown,
        "blocker": blocker,
        "nextRecommendation": next_step,
    }


def liveness_hint_from_daemon_status(status: dict[str, Any] | None) -> dict[str, Any]:
    raw = status if isinstance(status, dict) else {}
    world = _dict(raw.get("worldModelSummary") or _dict(raw.get("worldModelPayloads")).get("world_model_summary"))
    objects = _dict(world.get("objects"))
    hot = _dict(raw.get("clientTickHot"))
    hot_fresh, hot_age = _client_tick_fresh_from_hot(hot)
    game_state = str(hot.get("gameState") or raw.get("gameState") or "").upper()
    object_total = _int(objects.get("total") or raw.get("worldModelObjectTotal"))
    loaded = bool(game_state == "LOGGED_IN" and hot_fresh and object_total is not None and object_total > 0)
    if loaded:
        state = "loaded_scene"
    elif game_state == "LOGIN_SCREEN":
        state = "login_screen"
    elif game_state == "LOGGED_IN" and (object_total is None or object_total <= 0 or not hot_fresh):
        state = "stale_logged_in_no_scene"
    elif not raw:
        state = "daemon_down"
    else:
        state = "loading"
    recoverable = state in RECOVERABLE_STATES
    manual = state in MANUAL_STATES
    return {
        "schema": "liveness_recovery_hint.v1",
        "livenessRecoveryAvailable": True,
        "livenessRecoveryRecommended": bool(not loaded and (recoverable or manual)),
        "livenessState": state,
        "loadedSceneProof": {
            "loadedSceneVerified": loaded,
            "gameState": game_state or None,
            "clientTickHotFresh": hot_fresh,
            "clientTickHotAgeMillis": hot_age,
            "worldModelObjectTotal": object_total,
        },
        "knownRecoverableState": recoverable,
        "manualLoginRequired": manual,
        "unknownScreen": state == "unknown",
        "nextRecommendation": (
            "run ensure_loaded_scene once"
            if recoverable
            else "manual login required"
            if manual
            else "continue"
            if loaded
            else "inspect current screen"
        ),
    }


def _inspect_state(
    *,
    snapshot_url: str,
    daemon_url: str,
    fetch_snapshot_func: Callable[..., dict[str, Any]],
    fetch_daemon_status_func: Callable[..., dict[str, Any]],
    window_finder: Callable[[list[str]], dict[str, Any]],
    button_candidates_func: Callable[..., tuple[list[Any], list[str]]],
    timeout: float,
    save_debug_screenshot: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    try:
        snapshot_payload = fetch_snapshot_func(snapshot_url, timeout=timeout)
        snapshot_error = None
    except Exception as error:  # noqa: BLE001
        snapshot_payload = None
        snapshot_error = f"{type(error).__name__}: {error}"
    try:
        daemon_status = fetch_daemon_status_func(daemon_url, timeout=timeout)
        daemon_error = None
    except Exception as error:  # noqa: BLE001
        daemon_status = None
        daemon_error = f"{type(error).__name__}: {error}"
    try:
        window = window_finder(bootstrap.title_filters("RuneLite"))
    except Exception as error:  # noqa: BLE001
        window = {"matchedWindowTitle": None, "warnings": [f"window finder failed: {type(error).__name__}: {error}"]}
    warnings.extend(str(item) for item in _list(_dict(window).get("warnings")))
    candidates: list[Any] = []
    if snapshot_payload is None or not loaded_scene_proof(snapshot_payload, reachable=True).get("loadedSceneVerified"):
        try:
            candidates, candidate_warnings = button_candidates_func(
                snapshot_payload,
                window,
                save_debug_screenshot=save_debug_screenshot,
                template_dir=bootstrap.BOOTSTRAP_TEMPLATE_DIR,
            )
            warnings.extend(str(item) for item in candidate_warnings)
        except Exception as error:  # noqa: BLE001
            warnings.append(f"button classifier failed: {type(error).__name__}: {error}")
    return classify_state(
        snapshot_payload=snapshot_payload,
        snapshot_error=snapshot_error,
        daemon_status=daemon_status,
        daemon_error=daemon_error,
        window=window,
        candidates=candidates,
    ), warnings


def _bootstrap_args(
    *,
    state: dict[str, Any],
    snapshot_url: str,
    daemon_url: str,
    backend: str,
    arduino_port: str | None,
    max_total_ms: int,
    max_attempts_per_state: int,
    allow_jagex_launcher: bool,
) -> argparse.Namespace:
    launch_flag = "--launch-runelite" if state.get("state") == "plugin_endpoint_down" and not _dict(state.get("bootstrapState")).get("runeLiteWindowBounds") else "--skip-runelite-launch"
    argv = [
        launch_flag,
        "--execute",
        "--recover-loaded-scene",
        "--verify-loaded-scene",
        "--start-daemon",
        "--timeout-seconds",
        str(max(1.0, float(max_total_ms) / 1000.0)),
        "--max-startup-clicks",
        str(max(1, int(max_attempts_per_state)) * 3),
        "--snapshot-url",
        snapshot_url,
        "--daemon-url",
        daemon_url,
        "--backend",
        backend,
        "--keep-existing-runelite",
    ]
    if arduino_port:
        argv.extend(["--arduino-port", arduino_port])
    if allow_jagex_launcher:
        argv.append("--allow-jagex-launcher-automation")
    else:
        argv.append("--no-jagex-launcher")
    args = bootstrap.parse_args(argv)
    setattr(args, "prefer_saved_account_play_now", _state_name(state) == "saved_account_play_now")
    return args


def _run_bootstrap_recovery(args: argparse.Namespace) -> dict[str, Any]:
    backend = bootstrap.build_startup_backend(args)
    startup_input = bootstrap.arm_startup_backend(args, backend)
    if startup_input.get("status") == "FAIL":
        cleanup = bootstrap.cleanup_startup_backend(backend)
        return {
            "schema": bootstrap.SCHEMA,
            "status": "FAIL",
            "startupInput": startup_input,
            "startupInputCleanup": cleanup,
            "failures": ["startup input backend failed"],
            "warnings": [str(startup_input.get("reason") or "startup input backend failed")],
        }
    try:
        payload = bootstrap.run_bootstrap(args, backend=backend)
    finally:
        cleanup = bootstrap.cleanup_startup_backend(backend)
    payload["startupInput"] = startup_input
    payload["startupInputCleanup"] = cleanup
    payload["startupBackend"] = getattr(backend, "name", backend.__class__.__name__)
    return payload


def _compact_click_details(details: dict[str, Any]) -> dict[str, Any]:
    if not details:
        return {}
    pre_focus = _dict(details.get("preClickFocus"))
    backend_status = _dict(details.get("backendStatus"))
    serial_trace = _dict(details.get("serialTrace") or backend_status.get("lastCommandTrace"))
    return {
        "status": details.get("status"),
        "warning": details.get("warning"),
        "timeoutClassification": details.get("timeoutClassification"),
        "retryRequiresScreenRecheck": details.get("retryRequiresScreenRecheck"),
        "inputBackend": details.get("inputBackend") or details.get("backend"),
        "inputPathUsed": details.get("inputPathUsed"),
        "command": details.get("command") or details.get("commandSent"),
        "arduinoAck": details.get("arduinoAck"),
        "arduinoAcks": details.get("arduinoAcks") or [],
        "arduinoCommandNames": details.get("arduinoCommandNames") or [],
        "commandTrace": details.get("commandTrace") or [],
        "serialTrace": serial_trace or None,
        "humanInput": details.get("humanInput"),
        "cursorBefore": details.get("cursorBefore"),
        "cursorAfterMove": details.get("cursorAfterMove"),
        "cursorAfterClick": details.get("cursorAfterClick"),
        "targetPoint": details.get("targetPoint"),
        "cursorTargetDistance": details.get("cursorTargetDistance"),
        "cursorBeforeTargetDistance": details.get("cursorBeforeTargetDistance"),
        "cursorAlreadyAtTarget": details.get("cursorAlreadyAtTarget"),
        "cursorAtTarget": details.get("cursorAtTarget"),
        "mouseMoveSent": details.get("mouseMoveSent"),
        "mouseDownSent": details.get("mouseDownSent"),
        "mouseUpSent": details.get("mouseUpSent"),
        "clickSent": details.get("clickSent"),
        "fullClickSequenceVerified": details.get("fullClickSequenceVerified"),
        "foregroundWindowTitle": details.get("foregroundWindowTitle"),
        "windowFocusVerified": details.get("windowFocusVerified"),
        "runeliteWindowFocused": details.get("runeliteWindowFocused"),
        "targetWindowAtPoint": pre_focus.get("targetWindowAtPoint"),
        "foregroundTitle": pre_focus.get("foregroundTitle"),
        "focusMethod": pre_focus.get("focusMethod"),
    }


def _compact_clicked_candidate(candidate: Any) -> dict[str, Any]:
    item = _dict(candidate)
    return {
        "name": item.get("name"),
        "source": item.get("source"),
        "candidateMethod": item.get("candidateMethod"),
        "screenPoint": item.get("screenPoint"),
        "targetPointLogical": item.get("targetPointLogical"),
        "targetPointPhysical": item.get("targetPointPhysical"),
        "buttonBoundsLogical": item.get("buttonBoundsLogical"),
        "buttonBoundsPhysical": item.get("buttonBoundsPhysical"),
        "targetValidationStatus": item.get("targetValidationStatus"),
        "targetInsideRuneLiteWindow": item.get("targetInsideRuneLiteWindow"),
        "targetInsideSafeClickRegion": item.get("targetInsideSafeClickRegion"),
        "expectedStateAfterClick": item.get("expectedStateAfterClick"),
        "expectedNextStates": item.get("expectedNextStates") or _expected_next_states_for_button(str(item.get("name") or "")),
        "beforeVisualState": item.get("beforeVisualState"),
        "afterVisualState": item.get("afterVisualState"),
        "beforeHotGameState": item.get("beforeHotGameState"),
        "afterHotGameState": item.get("afterHotGameState"),
        "loadedSceneVerifiedAfter": item.get("loadedSceneVerifiedAfter"),
        "visibleButtonsAfter": item.get("visibleButtonsAfter") or [],
        "visualTransitionObserved": item.get("visualTransitionObserved"),
        "hotStateTransitionObserved": item.get("hotStateTransitionObserved"),
        "expectedTransitionSatisfied": item.get("expectedTransitionSatisfied"),
        "transitionResult": item.get("transitionResult"),
        "clickResult": item.get("clickResult"),
        "reason": item.get("reason"),
        "clickDetails": _compact_click_details(_dict(item.get("clickDetails"))),
    }


def _compact_bootstrap_snapshot(recovery: dict[str, Any]) -> dict[str, Any]:
    snapshot = _dict(recovery.get("snapshot"))
    return {
        "gameState": snapshot.get("gameState"),
        "loggedIn": snapshot.get("loggedIn"),
        "loadedSceneVerified": snapshot.get("loadedSceneVerified"),
        "worldModelObjectTotal": snapshot.get("worldModelObjectTotal") or snapshot.get("objectTotal"),
        "screenClassification": snapshot.get("screenClassification"),
        "staleLoggedInNoScene": snapshot.get("staleLoggedInNoScene"),
        "finalPlayPanelPending": snapshot.get("finalPlayPanelPending"),
    }


def _bootstrap_action_from_recovery(
    recovery: dict[str, Any],
    *,
    state_name: str,
    prefer_saved_account_play_now: bool = False,
    retry_reason: str | None = None,
) -> dict[str, Any]:
    clicked_details = [
        _compact_clicked_candidate(item)
        for item in _list(recovery.get("clickedCandidates"))
        if isinstance(item, dict)
    ]
    bootstrap_state = _dict(recovery.get("bootstrapState"))
    button_candidates = [
        _compact_clicked_candidate(item)
        for item in _list(recovery.get("buttonCandidates"))
        if isinstance(item, dict)
    ]
    visible_buttons = sorted(
        {
            str(item.get("name") or "")
            for item in [*button_candidates, *clicked_details]
            if str(item.get("name") or "") in SAFE_VISIBLE_BUTTON_NAMES
        }
    )
    clicked_visible = [item for item in clicked_details if str(item.get("name") or "") in SAFE_VISIBLE_BUTTON_NAMES]
    action = {
        "action": "run_bootstrap_recovery",
        "state": state_name,
        "status": recovery.get("status"),
        "loadedSceneVerified": recovery.get("loadedSceneVerified"),
        "startupStage": recovery.get("startupStage"),
        "preferSavedAccountPlayNow": bool(prefer_saved_account_play_now),
        "visibleButtonScanAttempted": True,
        "visibleButtonsFound": visible_buttons,
        "visibleSafeButtonFound": bool(visible_buttons),
        "visibleSafeButtonClicked": bool(clicked_visible),
        "visibleButtonClickDetails": clicked_visible,
        "buttonCandidates": button_candidates,
        "clickedCandidates": [item.get("name") for item in clicked_details if item.get("name")],
        "clickedCandidateDetails": clicked_details,
        "bootstrapState": {
            "state": bootstrap_state.get("state"),
            "confidence": bootstrap_state.get("confidence"),
            "selectedBootstrapAction": bootstrap_state.get("selectedBootstrapAction"),
            "verificationResult": bootstrap_state.get("verificationResult"),
            "nextStep": bootstrap_state.get("nextStep"),
            "blocker": bootstrap_state.get("blocker"),
        },
        "snapshot": _compact_bootstrap_snapshot(recovery),
        "stages": [
            {
                "stage": _dict(stage).get("stage"),
                "status": _dict(stage).get("status"),
                "reason": _dict(stage).get("reason"),
            }
            for stage in _list(recovery.get("stages"))
            if isinstance(stage, dict)
        ],
        "daemon": recovery.get("daemon"),
        "failures": recovery.get("failures") or [],
    }
    if retry_reason:
        action["retryReason"] = retry_reason
    return action


def _bootstrap_recovery_has_candidate(recovery: dict[str, Any], name: str) -> bool:
    wanted = str(name or "").strip()
    if not wanted:
        return False
    for item in _list(recovery.get("buttonCandidates")):
        if str(_dict(item).get("name") or "") == wanted:
            return True
    for item in _list(_dict(recovery.get("bootstrapState")).get("detectedButtons")):
        if str(_dict(item).get("name") or "") == wanted:
            return True
    return False


def _bootstrap_recovery_clicked(recovery: dict[str, Any], name: str) -> bool:
    wanted = str(name or "").strip()
    if not wanted:
        return False
    return any(str(_dict(item).get("name") or "") == wanted for item in _list(recovery.get("clickedCandidates")))


def _clicked_candidates_from_actions(actions_taken: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clicked: list[dict[str, Any]] = []
    for action in actions_taken:
        details = _list(_dict(action).get("clickedCandidateDetails"))
        if details:
            clicked.extend(_dict(item) for item in details if isinstance(item, dict))
            continue
        for name in _list(_dict(action).get("clickedCandidates")):
            clicked.append({"name": name})
    return clicked


def _detected_button_names_from_state(state: dict[str, Any] | None) -> set[str]:
    names: set[str] = set()
    for item in _list(_dict(state).get("detectedButtons")):
        name = str(_dict(item).get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _button_candidate_names_from_actions(actions_taken: list[dict[str, Any]] | None) -> set[str]:
    names: set[str] = set()
    for action in actions_taken or []:
        for item in _list(_dict(action).get("buttonCandidates")):
            name = str(_dict(item).get("name") or "").strip()
            if name:
                names.add(name)
    return names


def _safe_visible_button_names_from_actions(actions_taken: list[dict[str, Any]] | None) -> set[str]:
    return {name for name in _button_candidate_names_from_actions(actions_taken) if name in SAFE_VISIBLE_BUTTON_NAMES}


def _safe_visible_clicked_from_actions(actions_taken: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [item for item in _clicked_candidates_from_actions(actions_taken or []) if str(item.get("name") or "") in SAFE_VISIBLE_BUTTON_NAMES]


def _recovery_action_metadata(
    *,
    status: str,
    initial_state: dict[str, Any],
    final_state: dict[str, Any],
    actions_taken: list[dict[str, Any]],
    relaunch_info: dict[str, Any],
    classification: dict[str, Any],
) -> dict[str, Any]:
    clicked_names = [str(item.get("name") or "") for item in _clicked_candidates_from_actions(actions_taken) if item.get("name")]
    clicked_set = set(clicked_names)
    detected_names = (
        _detected_button_names_from_state(initial_state)
        | _detected_button_names_from_state(final_state)
        | _button_candidate_names_from_actions(actions_taken)
    )
    visible_buttons_found = sorted(name for name in detected_names if name in SAFE_VISIBLE_BUTTON_NAMES)
    clicked_visible = _safe_visible_clicked_from_actions(actions_taken)
    recovery_actions: list[str] = []
    for action in actions_taken:
        item = _dict(action)
        action_name = str(item.get("action") or "").strip()
        if action_name:
            recovery_actions.append(action_name)
        recovery_actions.extend(str(name) for name in _list(item.get("clickedCandidates")) if name)
    seen_actions: list[str] = []
    for action in recovery_actions:
        if action not in seen_actions:
            seen_actions.append(action)
    final_state_name = _state_name(final_state)
    proof = _dict(final_state.get("loadedSceneProof"))
    game_state = str(proof.get("gameState") or "").upper()
    manual_login_status = status == "manual_login_required" or bool(final_state.get("manualLoginRequired"))
    autologin_attempted = any(str(_dict(action).get("action") or "") == "run_bootstrap_recovery" for action in actions_taken)
    launcher_attempted = bool(relaunch_info.get("relaunchAttempted")) or any(
        str(_dict(action).get("action") or "") == "relaunch_client" for action in actions_taken
    )
    wait_attempted = any(
        str(_dict(action).get("action") or "") in {
            "loaded_scene_stability_check",
            "wait_for_loaded_scene_after_relaunch",
        }
        for action in actions_taken
    )
    return {
        "recoveryAttempted": bool(actions_taken),
        "autologinRecoveryAttempted": autologin_attempted,
        "savedAccountDetected": bool("play_now" in detected_names or final_state_name == "saved_account_play_now" or _state_name(initial_state) == "saved_account_play_now"),
        "playNowAttempted": "play_now" in clicked_set,
        "disconnectedOkAttempted": "disconnected_ok" in clicked_set,
        "clickHereToPlayAttempted": "click_here_to_play" in clicked_set,
        "visibleButtonScanAttempted": bool(actions_taken),
        "visibleButtonsFound": visible_buttons_found,
        "visibleSafeButtonFound": bool(visible_buttons_found),
        "visibleSafeButtonClicked": bool(clicked_visible),
        "visibleButtonClickDetails": clicked_visible,
        "visibleButtonClickBlocked": bool(visible_buttons_found and not clicked_visible),
        "launcherRecoveryAttempted": launcher_attempted,
        "waitForLoadedSceneAttempted": wait_attempted,
        "manualLoginRequiredOnlyAfterRecovery": bool(status == "manual_login_required" and actions_taken),
        "recoveryActionsTried": seen_actions,
        "recoveryResult": {
            "status": status,
            "failureClass": classification.get("failureClass"),
            "reason": classification.get("reason"),
            "clickedCandidates": clicked_names,
        },
        "finalLoginSurface": final_state_name if manual_login_status or game_state == "LOGIN_SCREEN" else None,
        "finalHotGameState": proof.get("gameState"),
        "finalLoadedSceneVerified": bool(proof.get("loadedSceneVerified")),
        "finalReason": classification.get("reason"),
    }


def classify_recovery_failure(
    *,
    initial_state: dict[str, Any],
    final_state: dict[str, Any],
    actions_taken: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    actions = actions_taken or []
    clicked = _clicked_candidates_from_actions(actions)
    clicked_names = [str(item.get("name") or "") for item in clicked if item.get("name")]
    initial_name = _state_name(initial_state)
    final_name = _state_name(final_state)
    detected_names = (
        _detected_button_names_from_state(initial_state)
        | _detected_button_names_from_state(final_state)
        | _button_candidate_names_from_actions(actions)
    )
    safe_detected_names = sorted(name for name in detected_names if name in SAFE_VISIBLE_BUTTON_NAMES)
    safe_clicked = [item for item in clicked if str(item.get("name") or "") in SAFE_VISIBLE_BUTTON_NAMES]
    safe_clicked_names = [str(item.get("name") or "") for item in safe_clicked if item.get("name")]
    proof = _dict(final_state.get("loadedSceneProof"))
    game_state = str(proof.get("gameState") or "").upper()
    object_total = _int(proof.get("worldModelObjectTotal"))
    client_hot_fresh = bool(proof.get("clientTickHotFresh"))
    loaded = bool(proof.get("loadedSceneVerified"))
    evidence: list[str] = []
    if clicked_names:
        evidence.append("clicked=" + ",".join(clicked_names))
    if safe_detected_names:
        evidence.append("visibleButtons=" + ",".join(safe_detected_names))
    if game_state:
        evidence.append(f"gameState={game_state}")
    if object_total is not None:
        evidence.append(f"worldObjects={object_total}")
    evidence.append(f"clientTickHotFresh={client_hot_fresh}")
    evidence.append(f"initial={initial_name}")
    evidence.append(f"final={final_name}")

    if loaded:
        failure_class = "none"
        reason = "loaded_scene_verified"
    elif final_name == "credential_required":
        failure_class = "credentials_required"
        reason = "credential, account picker, or authenticator surface requires human action"
    elif (
        (initial_name == "saved_account_play_now" or final_name == "saved_account_play_now" or "play_now" in detected_names)
        and "play_now" not in clicked_names
    ):
        failure_class = "saved_account_play_now_not_attempted"
        reason = "saved-account Play Now was visible but was not attempted by the recovery ladder"
    elif safe_detected_names and not safe_clicked_names:
        failure_class = "visible_button_found_not_clicked"
        reason = "a safe visible recovery button was detected but no recovery click was sent"
    elif safe_clicked:
        proof_blocker = _click_proof_blocker(safe_clicked[-1])
        if proof_blocker:
            failure_class = proof_blocker
            reason_map = {
                "visible_button_click_not_grounded": "visible recovery button click was not proven at the button target",
                "incomplete_click_sequence": "visible recovery button click lacked a complete CLICK or MOUSE_DOWN/MOUSE_UP sequence",
                "recovery_click_window_not_focused": "RuneLite was not proven focused before the recovery click",
                "recovery_click_target_invalid": "visible recovery button target failed RuneLite/window safety validation",
            }
            reason = reason_map.get(proof_blocker, proof_blocker)
        elif "play_now" in safe_clicked_names and final_name == "disconnected_dialog":
            failure_class = "disconnected_loop"
            reason = "Play Now was clicked but the client returned to the disconnected dialog"
        elif final_name in {"disconnected_dialog", "login_screen", "saved_account_play_now", "click_here_to_play"} or game_state == "LOGIN_SCREEN":
            last_click = safe_clicked[-1]
            transition_ok = bool(last_click.get("expectedTransitionSatisfied"))
            if not transition_ok:
                failure_class = "visible_button_no_transition"
                reason = "safe visible recovery button click was sent, but no expected visual/client transition followed"
            else:
                failure_class = "loading_timeout"
                reason = "safe visible recovery button advanced to the next login/loading state but loaded-scene proof did not arrive"
        else:
            failure_class = "loading_timeout"
            reason = "safe visible recovery button click did not reach loaded-scene proof before the recovery budget expired"
    elif final_name == "login_screen" and "play_now" not in clicked_names and "disconnected_ok" not in clicked_names:
        failure_class = "login_surface_no_saved_account"
        reason = "login surface is present without a safe saved-account or disconnected recovery target"
    elif final_name in {"loading", "click_here_to_play"}:
        failure_class = "loading_timeout"
        reason = "client did not reach loaded-scene proof before the recovery budget expired"
    elif final_name == "stale_logged_in_no_scene" or (game_state == "LOGGED_IN" and (object_total is None or object_total <= 0)):
        failure_class = "logged_in_without_scene"
        reason = "client reports logged-in state but current world/player scene proof is unavailable"
    elif not client_hot_fresh:
        failure_class = "stale_hot_client"
        reason = "hot client sample is stale, so recovery cannot prove current loaded scene"
    else:
        failure_class = "unknown"
        reason = "loaded-scene proof is missing and no narrower recovery failure class matched"
    return {
        "schema": "liveness_recovery_failure_classification.v1",
        "failureClass": failure_class,
        "reason": reason,
        "initialState": initial_name,
        "finalState": final_name,
        "clickedCandidates": clicked_names,
        "loadedSceneVerified": loaded,
        "finalGameState": game_state or None,
        "worldModelObjectTotal": object_total,
        "clientTickHotFresh": client_hot_fresh,
        "evidence": evidence,
    }


def build_recovery_state_machine(
    *,
    initial_state: dict[str, Any],
    final_state: dict[str, Any],
    actions_taken: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    actions = actions_taken or []
    clicked = _clicked_candidates_from_actions(actions)
    clicks: list[dict[str, Any]] = []
    relaunch_attempts: list[dict[str, Any]] = []
    previous_state = _state_name(initial_state)
    for index, click in enumerate(clicked):
        name = str(click.get("name") or "unknown")
        before_from_click = str(click.get("beforeVisualState") or "").strip()
        if before_from_click:
            state_before = before_from_click
        elif name == "disconnected_ok":
            state_before = "disconnected_dialog"
        elif name == "play_now":
            state_before = "saved_account_visible" if previous_state in {"disconnected_dialog", "login_surface"} else previous_state
        elif name == "click_here_to_play":
            state_before = "click_here_to_play"
        else:
            state_before = previous_state
        expected = click.get("expectedStateAfterClick")
        expected_next_states = _list(click.get("expectedNextStates")) or _expected_next_states_for_button(name)
        click_details = _dict(click.get("clickDetails"))
        if click.get("afterVisualState"):
            state_after = str(click.get("afterVisualState"))
        elif index + 1 < len(clicked):
            state_after = _state_after_for_button_name(str(clicked[index + 1].get("name") or "")) or previous_state
        else:
            state_after = _state_name(final_state)
        transition_success = _expected_transition_satisfied(click, state_after, final_state)
        proof_blocker = _click_proof_blocker(click)
        clicks.append(
            {
                "schema": "recovery_attempt.v2",
                "attemptIndex": index,
                "stateBefore": state_before,
                "visibleButtonScanAttempted": True,
                "visibleButtonsFound": [name] if name in SAFE_VISIBLE_BUTTON_NAMES else [],
                "selectedRecoveryAction": name,
                "selectedButton": name,
                "buttonType": name,
                "screenPoint": click.get("screenPoint") or click.get("targetPointLogical"),
                "targetPoint": click.get("targetPointPhysical") or click.get("screenPoint") or click.get("targetPointLogical"),
                "targetRect": click.get("buttonBoundsPhysical") or click.get("buttonBoundsLogical"),
                "targetValidationStatus": click.get("targetValidationStatus"),
                "clickResult": click.get("clickResult"),
                "inputPathUsed": click_details.get("inputPathUsed"),
                "inputBackend": click_details.get("inputBackend"),
                "arduinoCommandAck": click_details.get("arduinoAck"),
                "arduinoAcks": click_details.get("arduinoAcks") or [],
                "beforeVisualState": click.get("beforeVisualState"),
                "afterVisualState": click.get("afterVisualState"),
                "beforeHotGameState": click.get("beforeHotGameState"),
                "afterHotGameState": click.get("afterHotGameState"),
                "cursorBefore": click_details.get("cursorBefore"),
                "cursorAfterMove": click_details.get("cursorAfterMove"),
                "cursorAfterClick": click_details.get("cursorAfterClick"),
                "cursorTargetDistance": click_details.get("cursorTargetDistance"),
                "cursorBeforeTargetDistance": click_details.get("cursorBeforeTargetDistance"),
                "cursorAlreadyAtTarget": click_details.get("cursorAlreadyAtTarget"),
                "foregroundWindowTitle": click_details.get("foregroundWindowTitle") or click_details.get("foregroundTitle"),
                "windowFocusVerified": click_details.get("windowFocusVerified"),
                "runeliteWindowFocused": click_details.get("runeliteWindowFocused"),
                "targetWindowAtPoint": click_details.get("targetWindowAtPoint"),
                "mouseMoveSent": click_details.get("mouseMoveSent"),
                "mouseDownSent": click_details.get("mouseDownSent"),
                "mouseUpSent": click_details.get("mouseUpSent"),
                "clickSent": click_details.get("clickSent"),
                "fullClickSequenceVerified": click_details.get("fullClickSequenceVerified"),
                "visualTransitionObserved": click.get("visualTransitionObserved"),
                "hotStateTransitionObserved": click.get("hotStateTransitionObserved"),
                "expectedTransitionSatisfied": transition_success,
                "expectedNextState": expected,
                "expectedNextStates": expected_next_states,
                "stateAfter": state_after,
                "transitionSuccess": transition_success,
                "transitionResult": click.get("transitionResult") or ("expected_transition_satisfied" if transition_success else "expected_transition_not_observed"),
                "blocker": proof_blocker,
                "reason": click.get("reason"),
                "clickEvidence": click,
            }
        )
        previous_state = str(state_after or expected or previous_state)
    for index, action in enumerate(actions):
        item = _dict(action)
        if item.get("action") not in {"relaunch_required", "relaunch_client", "wait_for_loaded_scene_after_relaunch"}:
            continue
        relaunch_attempts.append(
            {
                "attemptIndex": index,
                "stateBefore": item.get("state") or previous_state,
                "selectedRecoveryAction": item.get("action"),
                "startGameCommand": item.get("startGameCommand") or item.get("relaunchCommand"),
                "startGameCommandSource": item.get("startGameCommandSource"),
                "launchMode": item.get("launchMode"),
                "relaunchResult": item.get("result"),
                "stateAfter": item.get("stateAfter"),
                "transitionSuccess": bool(item.get("loadedSceneVerified") or item.get("transitionSuccess")),
                "reason": item.get("reason"),
            }
        )
    classification = classify_recovery_failure(initial_state=initial_state, final_state=final_state, actions_taken=actions)
    return {
        "schema": "liveness_recovery_state_machine.v1",
        "states": [
            {
                "label": "initial",
                "state": _state_name(initial_state),
                "loadedSceneVerified": bool(initial_state.get("loadedSceneVerified")),
                "loadedSceneProof": _dict(initial_state.get("loadedSceneProof")),
            },
            {
                "label": "final",
                "state": _state_name(final_state),
                "loadedSceneVerified": bool(final_state.get("loadedSceneVerified")),
                "loadedSceneProof": _dict(final_state.get("loadedSceneProof")),
            },
        ],
        "clickAttempts": clicks,
        "relaunchAttempts": relaunch_attempts,
        "failureClassification": classification,
        "visibleButtonScanAttempted": bool(actions),
        "visibleButtonsFound": sorted(
            (
                _detected_button_names_from_state(initial_state)
                | _detected_button_names_from_state(final_state)
                | _safe_visible_button_names_from_actions(actions)
            )
            & SAFE_VISIBLE_BUTTON_NAMES
        ),
        "transitionObserved": bool(
            (clicks or relaunch_attempts)
            and (
                _state_name(final_state) != _state_name(initial_state)
                or bool(_dict(final_state.get("loadedSceneProof")).get("loadedSceneVerified"))
            )
        ),
    }


def _result(
    *,
    status: str,
    initial_state: dict[str, Any],
    final_state: dict[str, Any],
    actions_taken: list[dict[str, Any]],
    started_ms: int,
    monotonic_func: Callable[[], float],
    attempts: dict[str, int] | None = None,
    blocker: str | None = None,
    manual_action_required: str | None = None,
    next_recommendation: str | None = None,
    warnings: list[str] | None = None,
    relaunch_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    proof = _dict(final_state.get("loadedSceneProof"))
    daemon = _dict(final_state.get("daemon"))
    relaunch = dict(relaunch_info or {})
    classification = classify_recovery_failure(
        initial_state=initial_state,
        final_state=final_state,
        actions_taken=actions_taken,
    )
    failure_class = str(classification.get("failureClass") or "unknown")
    effective_blocker = blocker
    if effective_blocker in {None, "loaded_scene_not_verified"} and failure_class not in {"none", "unknown"}:
        effective_blocker = failure_class
    state_machine = build_recovery_state_machine(
        initial_state=initial_state,
        final_state=final_state,
        actions_taken=actions_taken,
    )
    recovery_metadata = _recovery_action_metadata(
        status=status,
        initial_state=initial_state,
        final_state=final_state,
        actions_taken=actions_taken,
        relaunch_info=relaunch,
        classification=classification,
    )
    return {
        "schema": SCHEMA,
        "status": status,
        "initialState": initial_state,
        "finalState": final_state,
        "recoveryFailureClass": failure_class,
        "recoveryFailureReason": classification.get("reason"),
        "recoveryStateMachine": state_machine,
        "actionsTaken": actions_taken,
        "elapsedMs": max(0, _now_ms(monotonic_func) - started_ms),
        "attempts": attempts or {},
        "loadedSceneVerified": bool(proof.get("loadedSceneVerified")),
        "snapshotFresh": bool(proof.get("snapshotFresh")),
        "daemonFresh": bool(daemon.get("fresh")),
        "clientTickHotFresh": bool(proof.get("clientTickHotFresh")),
        "worldModelObjectTotal": proof.get("worldModelObjectTotal"),
        "currentSessionPath": daemon.get("sessionPath"),
        "disconnectedLoopDetected": bool(relaunch.get("disconnectedLoopDetected")),
        "relaunchRequired": bool(relaunch.get("relaunchRequired")),
        "relaunchAttempted": bool(relaunch.get("relaunchAttempted")),
        "launchMode": relaunch.get("launchMode"),
        "launchModeReason": relaunch.get("launchModeReason"),
        "launchModeWarnings": list(relaunch.get("launchModeWarnings") or []),
        "startGameCommand": relaunch.get("startGameCommand"),
        "startGameCommandSource": relaunch.get("startGameCommandSource"),
        "devStartCommand": relaunch.get("devStartCommand"),
        "devStartCommandSource": relaunch.get("devStartCommandSource"),
        "liveStartCommand": relaunch.get("liveStartCommand"),
        "liveStartCommandSource": relaunch.get("liveStartCommandSource"),
        "authenticatedLiveStartConfigured": bool(relaunch.get("authenticatedLiveStartConfigured")),
        "authenticatedLaunchLikely": bool(relaunch.get("authenticatedLaunchLikely")),
        "discoveredStartGameCandidates": list(relaunch.get("discoveredStartGameCandidates") or []),
        "relaunchCommand": relaunch.get("relaunchCommand") or relaunch.get("startGameCommand"),
        "relaunchResult": relaunch.get("relaunchResult"),
        "relaunchSucceeded": bool(relaunch.get("relaunchSucceeded")),
        "launchedProcessPid": relaunch.get("launchedProcessPid"),
        "loadedSceneAfterRelaunch": bool(
            relaunch.get("loadedSceneAfterRelaunch")
            or (relaunch.get("relaunchAttempted") and proof.get("loadedSceneVerified"))
        ),
        "loginScreenAfterRelaunch": bool(relaunch.get("loginScreenAfterRelaunch")),
        "finalHotGameState": proof.get("gameState"),
        "finalLoadedSceneVerified": bool(proof.get("loadedSceneVerified")),
        "finalWorldObjectCount": proof.get("worldModelObjectTotal"),
        "finalTick": proof.get("latestTick"),
        "finalExportSeq": proof.get("latestExportSeq"),
        "failureReason": effective_blocker,
        "manualActionRequired": manual_action_required,
        "blocker": effective_blocker,
        "rawBlocker": blocker,
        "nextRecommendation": next_recommendation or final_state.get("nextRecommendation"),
        "warnings": list(dict.fromkeys(warnings or [])),
        **recovery_metadata,
    }


def _cache_key(snapshot_url: str, daemon_url: str) -> str:
    return f"{_snapshot_url(snapshot_url)}|{str(daemon_url).rstrip('/')}"


def _cached_success(snapshot_url: str, daemon_url: str, monotonic_func: Callable[[], float]) -> dict[str, Any] | None:
    if not _LAST_SUCCESS:
        return None
    if _LAST_SUCCESS.get("cacheKey") != _cache_key(snapshot_url, daemon_url):
        return None
    age = float(monotonic_func()) - float(_LAST_SUCCESS.get("cachedAt") or 0.0)
    if age > CACHE_TTL_SECONDS:
        return None
    payload = dict(_LAST_SUCCESS.get("payload") or {})
    if payload:
        payload["cacheHit"] = True
        payload["actionsTaken"] = [*list(payload.get("actionsTaken") or []), {"action": "cache_hit", "ageSeconds": round(age, 3)}]
    return payload


def _remember_success(snapshot_url: str, daemon_url: str, payload: dict[str, Any], monotonic_func: Callable[[], float]) -> None:
    global _LAST_SUCCESS
    if payload.get("status") not in {"loaded_scene_ready", "recovered_loaded_scene"}:
        return
    _LAST_SUCCESS = {
        "cacheKey": _cache_key(snapshot_url, daemon_url),
        "cachedAt": float(monotonic_func()),
        "payload": dict(payload),
    }


def _resolve_start_game_command() -> dict[str, Any]:
    import start_game_command

    return start_game_command.resolve_start_game_command(prefer_authenticated=True)


def _launch_start_game(command_info: dict[str, Any]) -> dict[str, Any]:
    import start_game_command

    return start_game_command.launch_start_game(command_info, execute=True)


def _relaunch_failure_blocker(final_state: dict[str, Any]) -> str:
    proof = _dict(final_state.get("loadedSceneProof"))
    state = _state_name(final_state)
    game_state = str(proof.get("gameState") or "").upper()
    if state in {"disconnected_dialog", "login_screen", "saved_account_play_now"} or game_state == "LOGIN_SCREEN":
        return "stale_login_screen_after_relaunch"
    if not bool(proof.get("clientTickHotFresh")):
        return "stale_hot_client_after_relaunch"
    if state == "plugin_endpoint_down" or not final_state.get("snapshotReachable"):
        return "snapshot_unreachable_after_relaunch"
    if state == "daemon_down":
        return "daemon_down_after_relaunch"
    if state == "daemon_stale":
        return "daemon_stale_after_relaunch"
    return "relaunch_loaded_scene_not_verified"


def _confirm_stable_loaded_scene(
    *,
    final_state: dict[str, Any],
    snapshot_url: str,
    daemon_url: str,
    fetch_snapshot_func: Callable[..., dict[str, Any]],
    fetch_daemon_status_func: Callable[..., dict[str, Any]],
    window_finder: Callable[[list[str]], dict[str, Any]],
    button_candidates_func: Callable[..., tuple[list[Any], list[str]]],
    sleep_func: Callable[[float], None],
    warnings: list[str],
    actions: list[dict[str, Any]],
    state_label: str,
) -> dict[str, Any]:
    if not final_state.get("loadedSceneVerified"):
        return final_state
    sleep_func(LOADED_SCENE_STABILITY_SECONDS)
    stable_state, stable_warnings = _inspect_state(
        snapshot_url=snapshot_url,
        daemon_url=daemon_url,
        fetch_snapshot_func=fetch_snapshot_func,
        fetch_daemon_status_func=fetch_daemon_status_func,
        window_finder=window_finder,
        button_candidates_func=button_candidates_func,
        timeout=1.0,
    )
    warnings.extend(stable_warnings)
    stable = bool(stable_state.get("loadedSceneVerified") and _dict(stable_state.get("daemon")).get("fresh"))
    actions.append(
        {
            "action": "loaded_scene_stability_check",
            "state": state_label,
            "status": "PASS" if stable else "FAIL",
            "stableSeconds": LOADED_SCENE_STABILITY_SECONDS,
            "loadedSceneVerified": bool(stable_state.get("loadedSceneVerified")),
            "stateAfter": _state_name(stable_state),
            "reason": "loaded scene remained current" if stable else "loaded scene did not remain current after recovery",
        }
    )
    if not stable:
        warnings.append("loaded scene proof disappeared during stability check")
    return stable_state


def ensure_loaded_scene(
    *,
    daemon_url: str = "http://127.0.0.1:8890",
    snapshot_url: str = "http://127.0.0.1:8893",
    backend: str = "arduino",
    arduino_port: str | None = "COM6",
    max_total_ms: int = 120_000,
    max_attempts_per_state: int = 2,
    allow_jagex_launcher: bool = False,
    allow_credentials: bool = False,
    allow_relaunch: bool = True,
    use_cache: bool = True,
    fetch_snapshot_func: Callable[..., dict[str, Any]] = bootstrap.fetch_snapshot,
    fetch_daemon_status_func: Callable[..., dict[str, Any]] = fetch_daemon_status,
    window_finder: Callable[[list[str]], dict[str, Any]] = bootstrap.find_window,
    button_candidates_func: Callable[..., tuple[list[Any], list[str]]] = bootstrap.button_candidates,
    run_bootstrap_recovery_func: Callable[[argparse.Namespace], dict[str, Any]] = _run_bootstrap_recovery,
    start_daemon_func: Callable[..., dict[str, Any]] = bootstrap.start_daemon,
    resolve_start_game_command_func: Callable[[], dict[str, Any]] = _resolve_start_game_command,
    launch_start_game_func: Callable[[dict[str, Any]], dict[str, Any]] = _launch_start_game,
    sleep_func: Callable[[float], None] = time.sleep,
    monotonic_func: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    started_ms = _now_ms(monotonic_func)
    warnings: list[str] = []
    actions: list[dict[str, Any]] = []
    attempts: dict[str, int] = {}
    relaunch_info: dict[str, Any] = {
        "disconnectedLoopDetected": False,
        "relaunchRequired": False,
        "relaunchAttempted": False,
        "relaunchSucceeded": False,
        "loadedSceneAfterRelaunch": False,
        "loginScreenAfterRelaunch": False,
        "launchMode": None,
        "launchModeReason": None,
        "launchModeWarnings": [],
    }
    if use_cache:
        cached = _cached_success(snapshot_url, daemon_url, monotonic_func)
        if cached:
            return cached
    initial_state, inspect_warnings = _inspect_state(
        snapshot_url=snapshot_url,
        daemon_url=daemon_url,
        fetch_snapshot_func=fetch_snapshot_func,
        fetch_daemon_status_func=fetch_daemon_status_func,
        window_finder=window_finder,
        button_candidates_func=button_candidates_func,
        timeout=1.0,
    )
    warnings.extend(inspect_warnings)
    if initial_state.get("loadedSceneVerified") and _dict(initial_state.get("daemon")).get("fresh"):
        payload = _result(
            status="loaded_scene_ready",
            initial_state=initial_state,
            final_state=initial_state,
            actions_taken=actions,
            started_ms=started_ms,
            monotonic_func=monotonic_func,
            attempts=attempts,
            warnings=warnings,
            next_recommendation="continue",
        )
        _remember_success(snapshot_url, daemon_url, payload, monotonic_func)
        return payload
    state_name = str(initial_state.get("state") or "unknown")
    if state_name == "credential_required" or (
        initial_state.get("manualLoginRequired")
        and not allow_credentials
        and state_name != "login_screen"
    ):
        return _result(
            status="manual_login_required",
            initial_state=initial_state,
            final_state=initial_state,
            actions_taken=actions,
            started_ms=started_ms,
            monotonic_func=monotonic_func,
            attempts=attempts,
            blocker="manual_login_required",
            manual_action_required="Log in or clear the account/credential prompt manually inside the VM.",
            next_recommendation="manual login required; no credentials will be typed",
            warnings=warnings,
        )
    if state_name == "unknown":
        unknown_state, unknown_warnings = _inspect_state(
            snapshot_url=snapshot_url,
            daemon_url=daemon_url,
            fetch_snapshot_func=fetch_snapshot_func,
            fetch_daemon_status_func=fetch_daemon_status_func,
            window_finder=window_finder,
            button_candidates_func=button_candidates_func,
            timeout=1.0,
            save_debug_screenshot=True,
        )
        warnings.extend(unknown_warnings)
        actions.append({"action": "capture_unknown_screen_debug", "status": "attempted"})
        return _result(
            status="unknown_screen",
            initial_state=initial_state,
            final_state=unknown_state,
            actions_taken=actions,
            started_ms=started_ms,
            monotonic_func=monotonic_func,
            attempts=attempts,
            blocker="unknown_screen",
            manual_action_required="Inspect the RuneLite window before any click.",
            next_recommendation="unknown screen; no guess-click sent",
            warnings=warnings,
        )
    if backend != "arduino" and state_name not in {"daemon_down", "daemon_stale"}:
        return _result(
            status="unsafe",
            initial_state=initial_state,
            final_state=initial_state,
            actions_taken=actions,
            started_ms=started_ms,
            monotonic_func=monotonic_func,
            attempts=attempts,
            blocker="software_input_not_allowed_for_liveness_recovery",
            next_recommendation="retry with backend=arduino",
            warnings=[*warnings, "liveness recovery clicks require Arduino/HumanInputController"],
        )
    final_state = initial_state
    if state_name in {"daemon_down", "daemon_stale"}:
        attempts[state_name] = attempts.get(state_name, 0) + 1
        start = start_daemon_func(execute=True)
        actions.append({"action": "start_or_rebind_daemon", "state": state_name, "result": start})
        sleep_func(2.0)
    elif initial_state.get("knownRecoverableState"):
        attempts[state_name] = attempts.get(state_name, 0) + 1
        recovery_args = _bootstrap_args(
            state=initial_state,
            snapshot_url=snapshot_url,
            daemon_url=daemon_url,
            backend=backend,
            arduino_port=arduino_port,
            max_total_ms=max_total_ms,
            max_attempts_per_state=max_attempts_per_state,
            allow_jagex_launcher=allow_jagex_launcher,
        )
        recovery = run_bootstrap_recovery_func(recovery_args)
        actions.append(
            _bootstrap_action_from_recovery(
                recovery,
                state_name=state_name,
                prefer_saved_account_play_now=bool(getattr(recovery_args, "prefer_saved_account_play_now", False)),
            )
        )
        if "startup input backend failed" in [str(item) for item in _list(recovery.get("failures"))]:
            return _result(
                status="arduino_unavailable",
                initial_state=initial_state,
                final_state=initial_state,
                actions_taken=actions,
                started_ms=started_ms,
                monotonic_func=monotonic_func,
                attempts=attempts,
                blocker="arduino_unavailable",
                next_recommendation="check Arduino COM port/firmware, then retry",
                warnings=[*warnings, *[str(item) for item in _list(recovery.get("warnings"))]],
            )
        if (
            not bool(recovery.get("loadedSceneVerified"))
            and _bootstrap_recovery_has_candidate(recovery, "play_now")
            and not _bootstrap_recovery_clicked(recovery, "play_now")
        ):
            attempts["saved_account_play_now_retry"] = attempts.get("saved_account_play_now_retry", 0) + 1
            recovery_args = _bootstrap_args(
                state={**initial_state, "state": "saved_account_play_now"},
                snapshot_url=snapshot_url,
                daemon_url=daemon_url,
                backend=backend,
                arduino_port=arduino_port,
                max_total_ms=max_total_ms,
                max_attempts_per_state=1,
                allow_jagex_launcher=allow_jagex_launcher,
            )
            setattr(recovery_args, "prefer_saved_account_play_now", True)
            recovery = run_bootstrap_recovery_func(recovery_args)
            actions.append(
                _bootstrap_action_from_recovery(
                    recovery,
                    state_name="saved_account_play_now_retry",
                    prefer_saved_account_play_now=True,
                    retry_reason="Play Now candidate was visible but not attempted by the normal recovery order",
                )
            )
            if "startup input backend failed" in [str(item) for item in _list(recovery.get("failures"))]:
                return _result(
                    status="arduino_unavailable",
                    initial_state=initial_state,
                    final_state=initial_state,
                    actions_taken=actions,
                    started_ms=started_ms,
                    monotonic_func=monotonic_func,
                    attempts=attempts,
                    blocker="arduino_unavailable",
                    next_recommendation="check Arduino COM port/firmware, then retry",
                    warnings=[*warnings, *[str(item) for item in _list(recovery.get("warnings"))]],
                )
    final_state, final_warnings = _inspect_state(
        snapshot_url=snapshot_url,
        daemon_url=daemon_url,
        fetch_snapshot_func=fetch_snapshot_func,
        fetch_daemon_status_func=fetch_daemon_status_func,
        window_finder=window_finder,
        button_candidates_func=button_candidates_func,
        timeout=1.0,
    )
    warnings.extend(final_warnings)
    if final_state.get("loadedSceneVerified") and not _dict(final_state.get("daemon")).get("fresh"):
        attempts["daemon_rebind"] = attempts.get("daemon_rebind", 0) + 1
        start = start_daemon_func(execute=True)
        actions.append({"action": "start_or_rebind_daemon", "state": "loaded_scene_without_fresh_daemon", "result": start})
        sleep_func(2.0)
        final_state, final_warnings = _inspect_state(
            snapshot_url=snapshot_url,
            daemon_url=daemon_url,
            fetch_snapshot_func=fetch_snapshot_func,
            fetch_daemon_status_func=fetch_daemon_status_func,
            window_finder=window_finder,
            button_candidates_func=button_candidates_func,
            timeout=1.0,
        )
        warnings.extend(final_warnings)
    if actions and final_state.get("loadedSceneVerified") and _dict(final_state.get("daemon")).get("fresh"):
        attempts["loaded_scene_stability_check"] = attempts.get("loaded_scene_stability_check", 0) + 1
        final_state = _confirm_stable_loaded_scene(
            final_state=final_state,
            snapshot_url=snapshot_url,
            daemon_url=daemon_url,
            fetch_snapshot_func=fetch_snapshot_func,
            fetch_daemon_status_func=fetch_daemon_status_func,
            window_finder=window_finder,
            button_candidates_func=button_candidates_func,
            sleep_func=sleep_func,
            warnings=warnings,
            actions=actions,
            state_label="post_recovery_loaded_scene",
        )
    if allow_relaunch and not final_state.get("loadedSceneVerified"):
        classification = classify_recovery_failure(
            initial_state=initial_state,
            final_state=final_state,
            actions_taken=actions,
        )
        failure_class = str(classification.get("failureClass") or "unknown")
        relaunchable_failures = {
            "disconnected_loop",
            "loading_timeout",
            "play_now_no_transition",
            "visible_button_no_transition",
            "login_surface_no_saved_account",
            "logged_in_without_scene",
            "stale_hot_client",
        }
        if failure_class in relaunchable_failures:
            relaunch_info["disconnectedLoopDetected"] = failure_class == "disconnected_loop"
            relaunch_info["relaunchRequired"] = True
            attempts["relaunch_required"] = attempts.get("relaunch_required", 0) + 1
            command_info = resolve_start_game_command_func()
            launch_mode = str(command_info.get("launchMode") or "unknown")
            relaunch_info["startGameCommand"] = command_info.get("command")
            relaunch_info["startGameCommandSource"] = command_info.get("commandSource")
            relaunch_info["relaunchCommand"] = command_info.get("command")
            relaunch_info["launchMode"] = launch_mode
            relaunch_info["launchModeReason"] = command_info.get("launchModeReason")
            relaunch_info["launchModeWarnings"] = list(command_info.get("launchModeWarnings") or [])
            relaunch_info["devStartCommand"] = command_info.get("devStartCommand")
            relaunch_info["devStartCommandSource"] = command_info.get("devStartCommandSource")
            relaunch_info["liveStartCommand"] = command_info.get("liveStartCommand")
            relaunch_info["liveStartCommandSource"] = command_info.get("liveStartCommandSource")
            relaunch_info["authenticatedLiveStartConfigured"] = bool(command_info.get("authenticatedLiveStartConfigured"))
            relaunch_info["authenticatedLaunchLikely"] = bool(command_info.get("authenticatedLaunchLikely"))
            relaunch_info["discoveredStartGameCandidates"] = list(command_info.get("discoveredCandidates") or [])
            warnings.extend(str(item) for item in relaunch_info["launchModeWarnings"])
            actions.append(
                {
                    "action": "relaunch_required",
                    "state": failure_class,
                    "reason": classification.get("reason"),
                    "startGameCommand": command_info.get("command"),
                    "startGameCommandSource": command_info.get("commandSource"),
                    "launchMode": launch_mode,
                    "launchModeWarnings": list(command_info.get("launchModeWarnings") or []),
                    "stateAfter": "relaunch_required",
                }
            )
            if command_info.get("status") != "PASS":
                reason = str(command_info.get("reason") or "relaunch_command_missing")
                blocker = reason if reason in {"authenticated_live_start_missing", "authenticated_live_start_invalid"} else "relaunch_command_missing"
                relaunch_info["relaunchResult"] = {
                    "status": "FAIL",
                    "reason": reason,
                    "command": command_info.get("command"),
                    "commandSource": command_info.get("commandSource"),
                    "devStartCommand": command_info.get("devStartCommand"),
                    "liveStartCommand": command_info.get("liveStartCommand"),
                    "discoveredCandidates": list(command_info.get("discoveredCandidates") or []),
                    "nextRecommendation": command_info.get("nextRecommendation"),
                }
                return _result(
                    status="unsafe",
                    initial_state=initial_state,
                    final_state=final_state,
                    actions_taken=actions,
                    started_ms=started_ms,
                    monotonic_func=monotonic_func,
                    attempts=attempts,
                    blocker=blocker,
                    next_recommendation=(
                        command_info.get("nextRecommendation")
                        or "configure an authenticated live Start Game command or attach an already-loaded client"
                    ),
                    warnings=warnings,
                    relaunch_info=relaunch_info,
                )
            attempts["relaunching_client"] = attempts.get("relaunching_client", 0) + 1
            launch = launch_start_game_func(command_info)
            relaunch_info["relaunchAttempted"] = bool(launch.get("relaunchAttempted") or launch.get("status") == "PASS")
            relaunch_info["relaunchSucceeded"] = bool(launch.get("relaunchSucceeded"))
            relaunch_info["launchedProcessPid"] = launch.get("launchedProcessPid")
            relaunch_info["relaunchResult"] = launch
            actions.append(
                {
                    "action": "relaunch_client",
                    "state": "relaunch_required",
                    "status": launch.get("status"),
                    "reason": launch.get("reason"),
                    "startGameCommand": command_info.get("command"),
                    "startGameCommandSource": command_info.get("commandSource"),
                    "launchMode": launch_mode,
                    "launchModeWarnings": list(command_info.get("launchModeWarnings") or []),
                    "result": launch,
                    "stateAfter": "relaunching_client",
                }
            )
            if launch.get("status") != "PASS" or not launch.get("relaunchSucceeded"):
                return _result(
                    status="unsafe",
                    initial_state=initial_state,
                    final_state=final_state,
                    actions_taken=actions,
                    started_ms=started_ms,
                    monotonic_func=monotonic_func,
                    attempts=attempts,
                    blocker="relaunch_failed",
                    next_recommendation="inspect Start Game command and client process launch",
                    warnings=warnings,
                    relaunch_info=relaunch_info,
                )
            relaunch_poll_count = 0
            post_relaunch_visible_recovery_attempted = False
            relaunch_deadline = (float(started_ms) / 1000.0) + max(1.0, float(max_total_ms) / 1000.0)
            while monotonic_func() < relaunch_deadline:
                sleep_func(2.0)
                relaunch_poll_count += 1
                current_state, current_warnings = _inspect_state(
                    snapshot_url=snapshot_url,
                    daemon_url=daemon_url,
                    fetch_snapshot_func=fetch_snapshot_func,
                    fetch_daemon_status_func=fetch_daemon_status_func,
                    window_finder=window_finder,
                    button_candidates_func=button_candidates_func,
                    timeout=1.0,
                )
                warnings.extend(current_warnings)
                final_state = current_state
                post_relaunch_state = _state_name(final_state)
                if (
                    not final_state.get("loadedSceneVerified")
                    and not post_relaunch_visible_recovery_attempted
                    and post_relaunch_state in {"disconnected_dialog", "saved_account_play_now", "click_here_to_play"}
                ):
                    post_relaunch_visible_recovery_attempted = True
                    attempts["post_relaunch_visible_button_recovery"] = attempts.get("post_relaunch_visible_button_recovery", 0) + 1
                    remaining_ms = max(1000, int((relaunch_deadline - monotonic_func()) * 1000.0))
                    recovery_args = _bootstrap_args(
                        state=final_state,
                        snapshot_url=snapshot_url,
                        daemon_url=daemon_url,
                        backend=backend,
                        arduino_port=arduino_port,
                        max_total_ms=remaining_ms,
                        max_attempts_per_state=max(1, max_attempts_per_state),
                        allow_jagex_launcher=allow_jagex_launcher,
                    )
                    setattr(recovery_args, "prefer_saved_account_play_now", post_relaunch_state == "saved_account_play_now")
                    post_relaunch_recovery = run_bootstrap_recovery_func(recovery_args)
                    actions.append(
                        _bootstrap_action_from_recovery(
                            post_relaunch_recovery,
                            state_name=f"post_relaunch_{post_relaunch_state}",
                            prefer_saved_account_play_now=bool(getattr(recovery_args, "prefer_saved_account_play_now", False)),
                            retry_reason="Start Game relaunch produced a visible safe recovery surface",
                        )
                    )
                    if "startup input backend failed" in [str(item) for item in _list(post_relaunch_recovery.get("failures"))]:
                        return _result(
                            status="arduino_unavailable",
                            initial_state=initial_state,
                            final_state=final_state,
                            actions_taken=actions,
                            started_ms=started_ms,
                            monotonic_func=monotonic_func,
                            attempts=attempts,
                            blocker="arduino_unavailable",
                            next_recommendation="check Arduino COM port/firmware, then retry",
                            warnings=[*warnings, *[str(item) for item in _list(post_relaunch_recovery.get("warnings"))]],
                            relaunch_info=relaunch_info,
                        )
                    final_state, final_warnings = _inspect_state(
                        snapshot_url=snapshot_url,
                        daemon_url=daemon_url,
                        fetch_snapshot_func=fetch_snapshot_func,
                        fetch_daemon_status_func=fetch_daemon_status_func,
                        window_finder=window_finder,
                        button_candidates_func=button_candidates_func,
                        timeout=1.0,
                    )
                    warnings.extend(final_warnings)
                    post_relaunch_classification = classify_recovery_failure(
                        initial_state=initial_state,
                        final_state=final_state,
                        actions_taken=actions,
                    )
                    if post_relaunch_classification.get("failureClass") in {
                        "visible_button_no_transition",
                        "visible_button_found_not_clicked",
                        "saved_account_play_now_not_attempted",
                        "disconnected_loop",
                    }:
                        break
                if final_state.get("loadedSceneVerified") and not _dict(final_state.get("daemon")).get("fresh"):
                    attempts["daemon_rebind_after_relaunch"] = attempts.get("daemon_rebind_after_relaunch", 0) + 1
                    start = start_daemon_func(execute=True)
                    actions.append({"action": "start_or_rebind_daemon", "state": "loaded_scene_after_relaunch_without_fresh_daemon", "result": start})
                    sleep_func(2.0)
                    final_state, final_warnings = _inspect_state(
                        snapshot_url=snapshot_url,
                        daemon_url=daemon_url,
                        fetch_snapshot_func=fetch_snapshot_func,
                        fetch_daemon_status_func=fetch_daemon_status_func,
                        window_finder=window_finder,
                        button_candidates_func=button_candidates_func,
                        timeout=1.0,
                    )
                    warnings.extend(final_warnings)
                if final_state.get("loadedSceneVerified") and _dict(final_state.get("daemon")).get("fresh"):
                    attempts["loaded_scene_stability_check_after_relaunch"] = attempts.get("loaded_scene_stability_check_after_relaunch", 0) + 1
                    final_state = _confirm_stable_loaded_scene(
                        final_state=final_state,
                        snapshot_url=snapshot_url,
                        daemon_url=daemon_url,
                        fetch_snapshot_func=fetch_snapshot_func,
                        fetch_daemon_status_func=fetch_daemon_status_func,
                        window_finder=window_finder,
                        button_candidates_func=button_candidates_func,
                        sleep_func=sleep_func,
                        warnings=warnings,
                        actions=actions,
                        state_label="post_relaunch_loaded_scene",
                    )
                if final_state.get("loadedSceneVerified") and _dict(final_state.get("daemon")).get("fresh"):
                    relaunch_info["loadedSceneAfterRelaunch"] = True
                    actions.append(
                        {
                            "action": "wait_for_loaded_scene_after_relaunch",
                            "state": "relaunching_client",
                            "status": "PASS",
                            "polls": relaunch_poll_count,
                            "loadedSceneVerified": True,
                            "stateAfter": "loaded_scene_verified",
                            "reason": "loaded-scene proof became current after Start Game relaunch",
                        }
                    )
                    break
            if not relaunch_info.get("loadedSceneAfterRelaunch"):
                blocker_after_relaunch = _relaunch_failure_blocker(final_state)
                classification_after_relaunch = classify_recovery_failure(
                    initial_state=initial_state,
                    final_state=final_state,
                    actions_taken=actions,
                )
                if classification_after_relaunch.get("failureClass") in {
                    "visible_button_found_not_clicked",
                    "visible_button_no_transition",
                    "saved_account_play_now_not_attempted",
                    "disconnected_loop",
                    "credentials_required",
                }:
                    blocker_after_relaunch = str(classification_after_relaunch.get("failureClass"))
                proof_after_relaunch = _dict(final_state.get("loadedSceneProof"))
                state_after_relaunch = _state_name(final_state)
                game_state_after_relaunch = str(proof_after_relaunch.get("gameState") or "").upper()
                login_after_relaunch = bool(
                    state_after_relaunch in {"disconnected_dialog", "login_screen", "saved_account_play_now", "stale_logged_in_no_scene"}
                    or game_state_after_relaunch == "LOGIN_SCREEN"
                )
                relaunch_info["loginScreenAfterRelaunch"] = login_after_relaunch
                if launch_mode == "dev_gradle_run" and login_after_relaunch:
                    blocker_after_relaunch = "dev_launch_not_loaded"
                    warnings.append(
                        "Start Game uses a Gradle/dev launch path; it launched a client but did not produce authenticated loaded-scene proof."
                    )
                actions.append(
                    {
                        "action": "wait_for_loaded_scene_after_relaunch",
                        "state": "relaunching_client",
                        "status": "FAIL",
                        "polls": relaunch_poll_count,
                        "loadedSceneVerified": False,
                        "stateAfter": _state_name(final_state),
                        "launchMode": launch_mode,
                        "loginScreenAfterRelaunch": login_after_relaunch,
                        "reason": blocker_after_relaunch,
                    }
                )
                return _result(
                    status="unsafe",
                    initial_state=initial_state,
                    final_state=final_state,
                    actions_taken=actions,
                    started_ms=started_ms,
                    monotonic_func=monotonic_func,
                    attempts=attempts,
                    blocker=blocker_after_relaunch,
                    next_recommendation="inspect client/login/network state after Start Game relaunch",
                    warnings=warnings,
                    relaunch_info=relaunch_info,
                )
    elapsed = _now_ms(monotonic_func) - started_ms
    if final_state.get("loadedSceneVerified") and _dict(final_state.get("daemon")).get("fresh"):
        payload = _result(
            status="recovered_loaded_scene" if actions else "loaded_scene_ready",
            initial_state=initial_state,
            final_state=final_state,
            actions_taken=actions,
            started_ms=started_ms,
            monotonic_func=monotonic_func,
            attempts=attempts,
            warnings=warnings,
            next_recommendation="continue",
            relaunch_info=relaunch_info,
        )
        _remember_success(snapshot_url, daemon_url, payload, monotonic_func)
        return payload
    if elapsed >= int(max_total_ms):
        status = "timeout"
        blocker = "liveness_recovery_timeout"
    elif final_state.get("loadedSceneVerified") and not _dict(final_state.get("daemon")).get("fresh"):
        status = "daemon_rebind_failed"
        blocker = "daemon_rebind_failed"
    elif state_name == "plugin_endpoint_down" or not final_state.get("snapshotReachable"):
        status = "plugin_endpoint_down"
        blocker = "plugin_endpoint_down"
    elif final_state.get("manualLoginRequired"):
        status = "manual_login_required"
        blocker = "manual_login_required_after_recovery" if actions else "manual_login_required"
    elif final_state.get("unknownScreen"):
        status = "unknown_screen"
        blocker = "unknown_screen"
    else:
        status = "unsafe"
        blocker = str(final_state.get("blocker") or "loaded_scene_not_verified")
    return _result(
        status=status,
        initial_state=initial_state,
        final_state=final_state,
        actions_taken=actions,
        started_ms=started_ms,
        monotonic_func=monotonic_func,
        attempts=attempts,
        blocker=blocker,
        manual_action_required=(
            "Manual RuneLite recovery required after the safe visible-button scan found no usable recovery button."
            if status == "manual_login_required"
            else None
        ),
        next_recommendation=final_state.get("nextRecommendation"),
        warnings=warnings,
    )


def format_compact_result(payload: dict[str, Any]) -> str:
    initial = _dict(payload.get("initialState"))
    final = _dict(payload.get("finalState"))
    lines = [
        f"LIVENESS RECOVERY - {payload.get('status') or 'UNKNOWN'}",
        f"initial: {initial.get('state') or 'unknown'}",
        f"final: {final.get('state') or 'unknown'}",
        f"loaded scene: {payload.get('loadedSceneVerified')}",
        f"snapshot/client hot/daemon fresh: {payload.get('snapshotFresh')} / {payload.get('clientTickHotFresh')} / {payload.get('daemonFresh')}",
        f"world objects: {payload.get('worldModelObjectTotal')}",
        f"session: {payload.get('currentSessionPath') or 'unknown'}",
        f"actions: {len(_list(payload.get('actionsTaken')))}",
        f"relaunch: required={bool(payload.get('relaunchRequired'))} attempted={bool(payload.get('relaunchAttempted'))} loaded_after={bool(payload.get('loadedSceneAfterRelaunch'))}",
        f"blocker: {payload.get('blocker') or 'none'}",
        f"next: {payload.get('nextRecommendation') or 'none'}",
    ]
    warnings = [str(item) for item in _list(payload.get("warnings"))]
    if warnings:
        lines.append("warnings:")
        lines.extend(f"  WARN: {warning}" for warning in warnings[:8])
    return "\n".join(lines).rstrip() + "\n"

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

RECOVERABLE_STATES = {
    "plugin_endpoint_down",
    "runelite_not_running",
    "stale_snapshot_no_packets",
    "stale_logged_in_no_scene",
    "disconnected_dialog",
    "saved_account_play_now",
    "click_here_to_play",
    "loading",
    "daemon_down",
    "daemon_stale",
}

MANUAL_STATES = {"credential_required", "login_screen"}

_LAST_SUCCESS: dict[str, Any] | None = None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def fetch_daemon_status(daemon_url: str, *, timeout: float = 1.0) -> dict[str, Any]:
    with urllib.request.urlopen(_daemon_status_url(daemon_url), timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8", errors="replace"))
    return decoded if isinstance(decoded, dict) else {}


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
    object_total = _int(summary.get("worldModelObjectTotal"))
    latest_tick = _int(summary.get("latestTick"))
    loaded = bool(
        summary.get("snapshotReachable")
        and str(summary.get("gameState") or "").upper() == "LOGGED_IN"
        and latest_tick is not None
        and summary.get("baselinePresent") is True
        and summary.get("worldModelAvailable") is True
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
        "baselinePresent": bool(summary.get("baselinePresent")),
        "clientTickHotPresent": bool(summary.get("clientTickHotPresent")),
        "clientTickHotFresh": client_tick_fresh,
        "clientTickHotAgeMillis": client_tick_age,
        "clientTickHotMaxAgeMillis": CLIENT_TICK_HOT_MAX_AGE_MS,
        "worldModelAvailable": bool(summary.get("worldModelAvailable")),
        "worldModelObjectTotal": object_total,
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
    return bootstrap.parse_args(argv)


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
) -> dict[str, Any]:
    proof = _dict(final_state.get("loadedSceneProof"))
    daemon = _dict(final_state.get("daemon"))
    return {
        "schema": SCHEMA,
        "status": status,
        "initialState": initial_state,
        "finalState": final_state,
        "actionsTaken": actions_taken,
        "elapsedMs": max(0, _now_ms(monotonic_func) - started_ms),
        "attempts": attempts or {},
        "loadedSceneVerified": bool(proof.get("loadedSceneVerified")),
        "snapshotFresh": bool(proof.get("snapshotFresh")),
        "daemonFresh": bool(daemon.get("fresh")),
        "clientTickHotFresh": bool(proof.get("clientTickHotFresh")),
        "worldModelObjectTotal": proof.get("worldModelObjectTotal"),
        "currentSessionPath": daemon.get("sessionPath"),
        "manualActionRequired": manual_action_required,
        "blocker": blocker,
        "nextRecommendation": next_recommendation or final_state.get("nextRecommendation"),
        "warnings": list(dict.fromkeys(warnings or [])),
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
    use_cache: bool = True,
    fetch_snapshot_func: Callable[..., dict[str, Any]] = bootstrap.fetch_snapshot,
    fetch_daemon_status_func: Callable[..., dict[str, Any]] = fetch_daemon_status,
    window_finder: Callable[[list[str]], dict[str, Any]] = bootstrap.find_window,
    button_candidates_func: Callable[..., tuple[list[Any], list[str]]] = bootstrap.button_candidates,
    run_bootstrap_recovery_func: Callable[[argparse.Namespace], dict[str, Any]] = _run_bootstrap_recovery,
    start_daemon_func: Callable[..., dict[str, Any]] = bootstrap.start_daemon,
    sleep_func: Callable[[float], None] = time.sleep,
    monotonic_func: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    started_ms = _now_ms(monotonic_func)
    warnings: list[str] = []
    actions: list[dict[str, Any]] = []
    attempts: dict[str, int] = {}
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
    if state_name == "credential_required" or (initial_state.get("manualLoginRequired") and not allow_credentials):
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
            {
                "action": "run_bootstrap_recovery",
                "state": state_name,
                "status": recovery.get("status"),
                "loadedSceneVerified": recovery.get("loadedSceneVerified"),
                "clickedCandidates": [
                    _dict(item).get("name")
                    for item in _list(recovery.get("clickedCandidates"))
                    if isinstance(item, dict)
                ],
                "daemon": recovery.get("daemon"),
                "failures": recovery.get("failures") or [],
            }
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
        blocker = "manual_login_required"
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
        manual_action_required="Manual RuneLite recovery required." if status == "manual_login_required" else None,
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
        f"blocker: {payload.get('blocker') or 'none'}",
        f"next: {payload.get('nextRecommendation') or 'none'}",
    ]
    warnings = [str(item) for item in _list(payload.get("warnings"))]
    if warnings:
        lines.append("warnings:")
        lines.extend(f"  WARN: {warning}" for warning in warnings[:8])
    return "\n".join(lines).rstrip() + "\n"

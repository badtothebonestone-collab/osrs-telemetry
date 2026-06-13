from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import bootstrap_vision
import bootstrap_window
from input_control.backend_arduino_hid import ArduinoHIDBackend
from input_control.backend_pyautogui import PyAutoGuiBackend
from input_control.human_input_controller import HumanInputController
from input_control.input_geometry import normalize_input_geometry, resolve_screen_click_point
from input_control.mouse_movement import MousePoint, MouseTarget


SCHEMA = "runelite_bootstrap.v1"
BOOTSTRAP_STATE_SCHEMA = "runelite_bootstrap_state.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_TEMPLATE_DIR = Path(__file__).resolve().parent / "assets" / "bootstrap_templates"

DAILY_DAEMON_COMMAND = [
    sys.executable,
    "telemetry-viewer\\live_core_daemon.py",
    "--latest-session",
    "--profile",
    "woodcutting",
    "--daily-mode",
    "snapshot-no-files",
    "--input-source",
    "plugin-snapshot",
    "--plugin-snapshot-tier",
    "hot",
    "--preset",
    "woodcut_bank",
    "--goal-count",
    "5",
    "--context-port",
    "8890",
    "--write-overlay-state",
    "--overlay-mode",
    "intent",
    "--overlay-backup-candidates",
    "2",
    "--overlay-debug-target-limit",
    "32",
    "--human-dashboard",
    "--summary",
    "--benchmark",
]

BUTTON_ZONES = [
    ("click_here_to_play", 0.50, 0.67, 0.78, "center login/play button candidate"),
    ("play_now", 0.50, 0.64, 0.68, "lower centered launcher play button candidate"),
    ("continue", 0.50, 0.76, 0.58, "continue button candidate"),
]

WINDOW_PERCENT_ZONES = [
    ("click_here_to_play", 0.53, 0.67, 0.56, "window welcome play panel candidate"),
    ("play_now", 0.50, 0.46, 0.52, "window center play button candidate"),
    ("continue", 0.50, 0.72, 0.44, "window lower continue button candidate"),
]
DISCONNECTED_OK_ZONE = ("disconnected_ok", 0.50, 0.61, 0.92, "recognized disconnected dialog OK button")
TITLEBAR_SAFE_MARGIN_PX = 44

WINDOW_TITLE_HINTS = ["RuneLite", "RuneLite Launcher", "Jagex Launcher", "Java", "Old School RuneScape"]
CREDENTIAL_MARKERS = ("PASSWORD", "CREDENTIAL", "AUTHENTICATOR", "ACCOUNT_CONFIRM", "ACCOUNT_MANAGEMENT", "MFA", "TWO_FACTOR")
BOOTSTRAP_SURFACE_NAMES = {"disconnected_ok", "play_now", "click_here_to_play", "continue"}
BOOTSTRAP_VISUAL_SOURCES = {"template", "disconnected_dialog", "saved_account_play_panel", "welcome_panel"}
MAX_REPEAT_VISIBLE_BUTTON_CLICKS = 2


@dataclass(frozen=True)
class StartupButtonCandidate:
    name: str
    source: str
    screen_point: dict[str, int] | None
    canvas_point: dict[str, int] | None
    confidence: float
    reason: str
    button_bounds_logical: dict[str, int] | None = None
    button_bounds_physical: dict[str, int] | None = None
    target_point_logical: dict[str, int] | None = None
    target_point_physical: dict[str, int] | None = None
    coordinate_scale: dict[str, Any] | None = None
    target_validation: dict[str, Any] | None = None
    expected_state_after_click: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "screenPoint": self.screen_point,
            "canvasPoint": self.canvas_point,
            "confidence": self.confidence,
            "reason": self.reason,
            "candidateMethod": candidate_method(self.source),
            "buttonBoundsLogical": self.button_bounds_logical,
            "buttonBoundsPhysical": self.button_bounds_physical,
            "targetPointLogical": self.target_point_logical,
            "targetPointPhysical": self.target_point_physical or self.screen_point,
            "coordinateScale": self.coordinate_scale,
            "targetInsideRuneLiteWindow": bool(dict_value(self.target_validation).get("targetInsideRuneLiteWindow")),
            "targetInsideSafeClickRegion": bool(dict_value(self.target_validation).get("targetInsideSafeClickRegion")),
            "targetValidationStatus": dict_value(self.target_validation).get("targetValidationStatus"),
            "expectedStateAfterClick": self.expected_state_after_click,
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RuneLite dev bootstrap helper. Does not type credentials.")
    launch = parser.add_mutually_exclusive_group()
    launch.add_argument("--launch-runelite", action="store_true")
    launch.add_argument("--skip-runelite-launch", action="store_true")
    parser.add_argument("--gradle-command", default=".\\gradlew.bat run")
    parser.add_argument("--keep-existing-runelite", action="store_true")
    parser.add_argument("--snapshot-url", default="http://127.0.0.1:8893")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--window-title-filter", default="RuneLite")
    parser.add_argument("--startup-backend", choices=["arduino", "pyautogui"], default="arduino")
    parser.add_argument("--backend", dest="startup_backend", choices=["arduino", "pyautogui"], help="Alias for --startup-backend.")
    parser.add_argument("--arduino-port", help="Arduino serial bridge port for startup clicks, for example COM6.")
    parser.add_argument("--arduino-baud", type=int, default=115200)
    parser.add_argument("--arduino-handshake-timeout-ms", type=int, default=2000)
    parser.add_argument("--arduino-command-timeout-ms", type=int, default=2000)
    parser.add_argument("--arduino-session-token", default="auto")
    parser.add_argument("--input-profile", choices=["instant_debug", "steady", "natural", "manual_calibrated"], default="steady")
    parser.add_argument("--movement-profile", choices=["linear_debug", "wind_mouse", "instant_test"], default="linear_debug")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--poll-interval-ms", type=int, default=1000)
    parser.add_argument("--max-startup-clicks", type=int, default=4)
    parser.add_argument("--post-play-wait-ms", type=int, default=8000)
    parser.add_argument("--move-to-secondary-monitor", action="store_true")
    parser.add_argument("--monitor-index", type=int, default=1)
    parser.add_argument("--window-x", type=int)
    parser.add_argument("--window-y", type=int)
    parser.add_argument("--window-width", type=int)
    parser.add_argument("--window-height", type=int)
    parser.add_argument("--fallback-win-shift-arrow", action="store_true")
    parser.add_argument("--print-candidates", action="store_true")
    parser.add_argument("--save-debug-screenshot", action="store_true")
    parser.add_argument("--capture-debug-screenshots", dest="save_debug_screenshot", action="store_true", help="Alias for --save-debug-screenshot.")
    parser.add_argument("--template-confidence", type=float, default=0.85)
    parser.add_argument("--template-dir", default=str(BOOTSTRAP_TEMPLATE_DIR))
    parser.add_argument("--start-daemon", action="store_true")
    parser.add_argument("--run-live-qa", action="store_true")
    parser.add_argument("--ensure-loaded-scene", action="store_true", help="Run the compact reusable liveness recovery controller and exit.")
    parser.add_argument("--recover-loaded-scene", action="store_true", help="Recover through safe already-authenticated RuneLite screens until a loaded scene is available.")
    parser.add_argument("--verify-loaded-scene", action="store_true", help="Require loaded-scene proof, not just LOGGED_IN.")
    parser.add_argument("--no-jagex-launcher", action="store_true", help="Explicitly keep Jagex Launcher automation disabled.")
    parser.add_argument("--allow-jagex-launcher-automation", action="store_true", help="Unsafe/login-recovery override: allow startup clicks in Jagex Launcher.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_run:
        args.execute = False
    return args


def snapshot_endpoint(snapshot_url: str) -> str:
    url = snapshot_url.rstrip("/")
    return url if url.endswith("/snapshot") else url + "/snapshot"


def snapshot_request() -> dict[str, Any]:
    return {
        "schema": "plugin_snapshot_request.v1",
        "needs": ["baseline", "client_tick_hot", "writer_health", "world_model_summary"],
        "maxAgeTicks": 5,
        "responseMode": "compact",
        "worldModel": {"maxObjects": 20, "radiusTiles": 10, "includeProjection": False, "includeCollision": False},
    }


def post_json(url: str, payload: dict[str, Any], timeout: float = 3.0) -> dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8", errors="replace"))
    return decoded if isinstance(decoded, dict) else {}


def fetch_snapshot(snapshot_url: str, *, timeout: float = 3.0) -> dict[str, Any]:
    return post_json(snapshot_endpoint(snapshot_url), snapshot_request(), timeout=timeout)


def fetch_json(url: str, timeout: float = 1.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8", errors="replace"))
    return decoded if isinstance(decoded, dict) else {}


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def int_bounds(value: Any) -> dict[str, int] | None:
    bounds = dict_value(value)
    if not bounds:
        return None
    try:
        width = int(round(float(bounds.get("width") or 0)))
        height = int(round(float(bounds.get("height") or 0)))
        if width <= 0 or height <= 0:
            return None
        return {
            "x": int(round(float(bounds.get("x") or 0))),
            "y": int(round(float(bounds.get("y") or 0))),
            "width": width,
            "height": height,
        }
    except Exception:  # noqa: BLE001
        return None


def logical_window_bounds(window: dict[str, Any] | None) -> dict[str, int] | None:
    raw = dict_value(window)
    return int_bounds(raw.get("logicalWindowBounds")) or int_bounds(raw.get("windowBounds"))


def physical_window_bounds(window: dict[str, Any] | None) -> dict[str, int] | None:
    raw = dict_value(window)
    return int_bounds(raw.get("physicalWindowBounds")) or int_bounds(raw.get("windowBounds"))


def window_coordinate_scale(window: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict_value(window)
    scale = dict_value(raw.get("coordinateScale"))
    if scale:
        return dict(scale)
    logical = logical_window_bounds(window)
    physical = physical_window_bounds(window)
    if logical and physical:
        return bootstrap_window.coordinate_scale(logical, physical)
    return {"x": 1.0, "y": 1.0, "applied": False}


def point_in_bounds(point: dict[str, int] | None, bounds: dict[str, int] | None) -> bool:
    if not point or not bounds:
        return False
    x = int(point.get("x") or 0)
    y = int(point.get("y") or 0)
    return (
        int(bounds["x"]) <= x <= int(bounds["x"]) + int(bounds["width"])
        and int(bounds["y"]) <= y <= int(bounds["y"]) + int(bounds["height"])
    )


def bootstrap_safe_click_region(window: dict[str, Any] | None) -> dict[str, int] | None:
    bounds = physical_window_bounds(window)
    if not bounds:
        return None
    top_margin = min(max(0, int(bounds["height"]) - 1), TITLEBAR_SAFE_MARGIN_PX)
    return {
        "x": int(bounds["x"]),
        "y": int(bounds["y"]) + top_margin,
        "width": int(bounds["width"]),
        "height": max(1, int(bounds["height"]) - top_margin),
    }


def validate_bootstrap_click_point(point: dict[str, int] | None, window: dict[str, Any] | None) -> dict[str, Any]:
    raw_window = dict_value(window)
    bounds = physical_window_bounds(window)
    safe_region = bootstrap_safe_click_region(window)
    trusted_bounds = bool(raw_window.get("physicalWindowBounds") or raw_window.get("windowHandle"))
    if not trusted_bounds and bounds:
        return {
            "targetValidationStatus": "PASS",
            "targetValidationReason": "window_bounds_untrusted_for_unit_test_or_dry_run",
            "targetInsideRuneLiteWindow": True,
            "targetInsideSafeClickRegion": True,
            "runeLiteWindowBounds": bounds,
            "safeClickRegion": safe_region,
        }
    inside_window = point_in_bounds(point, bounds)
    inside_safe = point_in_bounds(point, safe_region)
    status = "PASS" if inside_window and inside_safe else "FAIL"
    reason = "inside_safe_runelite_window" if status == "PASS" else "target_outside_safe_runelite_window"
    if inside_window and not inside_safe:
        reason = "target_in_titlebar_or_unsafe_window_strip"
    return {
        "targetValidationStatus": status,
        "targetValidationReason": reason,
        "targetInsideRuneLiteWindow": inside_window,
        "targetInsideSafeClickRegion": inside_safe,
        "runeLiteWindowBounds": bounds,
        "safeClickRegion": safe_region,
    }


def window_percent_point(
    window: dict[str, Any] | None,
    x_pct: float,
    y_pct: float,
    *,
    button_box: tuple[float, float, float, float] | None = None,
    surface_bounds: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    raw_surface = dict_value(surface_bounds)
    physical = int_bounds(raw_surface.get("physicalBounds")) or int_bounds(surface_bounds) or physical_window_bounds(window)
    if not physical:
        return None
    logical = int_bounds(raw_surface.get("logicalBounds")) or (physical if surface_bounds else (logical_window_bounds(window) or physical))
    target_logical = {
        "x": int(round(float(logical["x"]) + float(logical["width"]) * x_pct)),
        "y": int(round(float(logical["y"]) + float(logical["height"]) * y_pct)),
    }
    target_physical = {
        "x": int(round(float(physical["x"]) + float(physical["width"]) * x_pct)),
        "y": int(round(float(physical["y"]) + float(physical["height"]) * y_pct)),
    }
    bounds_logical = None
    bounds_physical = None
    if button_box:
        left, top, right, bottom = button_box
        bounds_logical = {
            "x": int(round(float(logical["x"]) + float(logical["width"]) * left)),
            "y": int(round(float(logical["y"]) + float(logical["height"]) * top)),
            "width": max(1, int(round(float(logical["width"]) * (right - left)))),
            "height": max(1, int(round(float(logical["height"]) * (bottom - top)))),
        }
        bounds_physical = {
            "x": int(round(float(physical["x"]) + float(physical["width"]) * left)),
            "y": int(round(float(physical["y"]) + float(physical["height"]) * top)),
            "width": max(1, int(round(float(physical["width"]) * (right - left)))),
            "height": max(1, int(round(float(physical["height"]) * (bottom - top)))),
        }
    return {
        "targetPointLogical": target_logical,
        "targetPointPhysical": target_physical,
        "buttonBoundsLogical": bounds_logical,
        "buttonBoundsPhysical": bounds_physical,
        "coordinateScale": dict_value(raw_surface.get("coordinateScale"))
        or ({"x": 1.0, "y": 1.0, "applied": False, "source": "input_geometry_canvas"} if surface_bounds else window_coordinate_scale(window)),
        "targetValidation": validate_bootstrap_click_point(target_physical, window),
    }


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def snapshot_baseline(snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    payloads = dict_value(snapshot_payload.get("payloads"))
    return dict_value(payloads.get("baseline") or snapshot_payload.get("baseline"))


def snapshot_client_tick_hot(snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    payloads = dict_value(snapshot_payload.get("payloads"))
    return dict_value(payloads.get("client_tick_hot") or snapshot_payload.get("clientTickHot"))


def snapshot_world_model_summary(snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    payloads = dict_value(snapshot_payload.get("payloads"))
    world_model = dict_value(snapshot_payload.get("worldModel"))
    world_payloads = dict_value(world_model.get("payloads"))
    return dict_value(
        payloads.get("world_model_summary")
        or snapshot_payload.get("worldModelSummary")
        or world_payloads.get("world_model_summary")
    )


def snapshot_effective_game_state(snapshot_payload: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    baseline = snapshot_baseline(snapshot_payload)
    summary = snapshot_world_model_summary(snapshot_payload)
    metadata = dict_value(summary.get("metadata"))
    objects = dict_value(summary.get("objects"))
    world_model_game_state = metadata.get("gameState")
    baseline_game_state = baseline.get("gameState")
    object_total = objects.get("total")
    game_state = first_present(world_model_game_state, baseline_game_state, snapshot_payload.get("gameState"))
    return game_state, {
        "baselineGameState": baseline_game_state,
        "worldModelGameState": world_model_game_state,
        "worldModelObjectTotal": object_total,
    }


def snapshot_top_menu(snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    hot = snapshot_client_tick_hot(snapshot_payload)
    return dict_value(hot.get("hoverMenu") or hot.get("postMenuSort"))


def final_play_panel_pending(snapshot_payload: dict[str, Any] | None) -> bool:
    payload = snapshot_payload if isinstance(snapshot_payload, dict) else {}
    game_state, _game_state_sources = snapshot_effective_game_state(payload)
    if game_state != "LOGGED_IN":
        return False
    menu = snapshot_top_menu(payload)
    top_option = str(menu.get("topOption") or "").strip().lower()
    top_type = str(menu.get("topType") or "").strip().upper()
    if top_option != "play":
        return False
    return top_type in {"", "CC_OP", "WIDGET_TARGET", "RUNELITE"}


def snapshot_summary(snapshot_payload: dict[str, Any] | None, *, reachable: bool, error: str | None = None) -> dict[str, Any]:
    payload = snapshot_payload if isinstance(snapshot_payload, dict) else {}
    baseline = snapshot_baseline(payload)
    hot = snapshot_client_tick_hot(payload)
    game_state, game_state_sources = snapshot_effective_game_state(payload)
    menu = snapshot_top_menu(payload)
    object_total = game_state_sources.get("worldModelObjectTotal")
    latest_tick = payload.get("latestTick")
    try:
        latest_tick_known = int(latest_tick) >= 0
    except Exception:  # noqa: BLE001
        latest_tick_known = False
    baseline_present = bool(baseline)
    world_model_available = bool(snapshot_world_model_summary(payload))
    object_total_positive = False
    try:
        object_total_positive = int(object_total or 0) > 0
    except Exception:  # noqa: BLE001
        object_total_positive = False
    hot_present = bool(hot)
    final_play_pending = final_play_panel_pending(payload)
    loaded_scene_verified = bool(
        reachable
        and game_state == "LOGGED_IN"
        and latest_tick_known
        and baseline_present
        and world_model_available
        and object_total_positive
        and hot_present
        and not final_play_pending
    )
    stale_logged_in_no_scene = bool(
        str(game_state_sources.get("baselineGameState") or "").upper() == "LOGGED_IN"
        and not loaded_scene_verified
        and (
            str(game_state_sources.get("worldModelGameState") or "").upper() == "LOGIN_SCREEN"
            or not object_total_positive
            or not latest_tick_known
        )
    )
    screen_classification = "loaded_scene" if game_state == "LOGGED_IN" else "unknown"
    if loaded_scene_verified:
        screen_classification = "loaded_scene"
    elif stale_logged_in_no_scene:
        screen_classification = "stale_logged_in_no_scene"
    if game_state == "LOGIN_SCREEN" and object_total == 0:
        screen_classification = "login_screen_or_disconnected_dialog"
    elif game_state == "LOGIN_SCREEN":
        screen_classification = "login_screen"
    return {
        "snapshotReachable": reachable,
        "snapshotStatus": payload.get("status") if reachable else "FAIL",
        "loggedIn": game_state == "LOGGED_IN",
        "gameState": game_state,
        "baselineGameState": game_state_sources.get("baselineGameState"),
        "worldModelGameState": game_state_sources.get("worldModelGameState"),
        "worldModelObjectTotal": object_total,
        "worldModelAvailable": world_model_available,
        "baselinePresent": baseline_present,
        "clientTickHotPresent": hot_present,
        "loadedSceneVerified": loaded_scene_verified,
        "staleLoggedInNoScene": stale_logged_in_no_scene,
        "screenClassification": screen_classification,
        "latestTick": latest_tick,
        "inputGeometryAvailable": bool(dict_value(baseline.get("inputGeometry")).get("geometryAvailable")),
        "topOption": menu.get("topOption"),
        "topTarget": menu.get("topTarget"),
        "topType": menu.get("topType"),
        "finalPlayPanelPending": final_play_pending,
        "error": error,
    }


def user_login_required(snapshot_payload: dict[str, Any] | None, window: dict[str, Any] | None = None) -> bool:
    payload = snapshot_payload if isinstance(snapshot_payload, dict) else {}
    game_state, _game_state_sources = snapshot_effective_game_state(payload)
    game_state = str(game_state or "").upper()
    title = str(dict_value(window).get("matchedWindowTitle") or "").upper()
    if any(marker in game_state for marker in CREDENTIAL_MARKERS):
        return True
    if "PASSWORD" in title or "AUTHENTICATOR" in title or "ACCOUNT SETTINGS" in title:
        return True
    return False


def bootstrap_goal_reached(summary: dict[str, Any], *, verify_loaded_scene: bool = False) -> bool:
    if summary.get("visualBootstrapSurfacePresent"):
        return False
    if verify_loaded_scene:
        return bool(summary.get("loadedSceneVerified"))
    return bool(summary.get("loggedIn"))


def candidate_expected_state(name: str) -> str:
    if name == "disconnected_ok":
        return "login_screen_or_saved_account"
    if name == "play_now":
        return "loading_or_click_here_to_play"
    if name == "click_here_to_play":
        return "loaded_scene"
    if name == "continue":
        return "loading_or_loaded_scene"
    return "unknown"


def visual_bootstrap_surface_candidates(candidates: list[StartupButtonCandidate] | None) -> list[StartupButtonCandidate]:
    visible: list[StartupButtonCandidate] = []
    for candidate in candidates or []:
        if candidate.name not in BOOTSTRAP_SURFACE_NAMES:
            continue
        validation = dict_value(candidate.target_validation)
        if validation.get("targetValidationStatus") == "FAIL":
            continue
        if candidate.source in BOOTSTRAP_VISUAL_SOURCES or candidate.name == "click_here_to_play":
            visible.append(candidate)
    return visible


def apply_visual_loaded_scene_veto(
    summary: dict[str, Any],
    candidates: list[StartupButtonCandidate] | None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    visual_candidates = visual_bootstrap_surface_candidates(candidates)
    if not visual_candidates:
        return summary
    updated = dict(summary)
    updated["visualBootstrapSurfacePresent"] = True
    updated["visualBootstrapSurfaceCandidates"] = [candidate.name for candidate in visual_candidates]
    if any(candidate.name == "click_here_to_play" for candidate in visual_candidates):
        updated["visualPlayPanelPending"] = True
        updated["finalPlayPanelPending"] = True
        updated["screenClassification"] = "click_here_to_play"
    if updated.get("loadedSceneVerified"):
        updated["loadedSceneVerified"] = False
        updated["loadedSceneVisualVeto"] = True
        updated["screenClassification"] = "visual_bootstrap_surface"
        if warnings is not None:
            warnings.append("visible RuneLite bootstrap surface vetoes daemon loaded-scene proof")
    return updated


def bootstrap_state_from_signals(
    *,
    summary: dict[str, Any],
    window: dict[str, Any] | None,
    candidates: list[StartupButtonCandidate] | None = None,
    selected_candidate: StartupButtonCandidate | None = None,
    screenshot_path: str | None = None,
    canvas_bounds: dict[str, int] | None = None,
    blocker: str | None = None,
    verification_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidates = candidates or []
    candidate_names = {candidate.name for candidate in candidates}
    recognized_sources = {"template", "disconnected_dialog", "saved_account_play_panel", "welcome_panel"}
    recognized_candidate_names = {
        candidate.name
        for candidate in candidates
        if candidate.source in recognized_sources or (candidate.name == "click_here_to_play" and summary.get("finalPlayPanelPending"))
    }
    state = "unknown"
    confidence = 0.25
    next_step = "wait_or_capture_debug"
    if not dict_value(window).get("matchedWindowTitle"):
        state = "runelite_not_running"
        confidence = 0.75
        next_step = "launch RuneLite dev client"
    elif not summary.get("snapshotReachable"):
        state = "plugin_endpoint_down"
        confidence = 0.70
        next_step = "wait for 8893 or restart RuneLite plugin"
    elif user_login_required({"payloads": {"baseline": {"gameState": summary.get("gameState")}}}, window):
        state = "credential_required"
        confidence = 0.80
        next_step = "manual_login_required"
    elif "disconnected_ok" in recognized_candidate_names:
        state = "disconnected_dialog"
        confidence = max(float(candidate.confidence) for candidate in candidates if candidate.name == "disconnected_ok")
        next_step = "click disconnected OK"
    elif "play_now" in recognized_candidate_names:
        state = "saved_account_play_now"
        confidence = max(float(candidate.confidence) for candidate in candidates if candidate.name == "play_now")
        next_step = "click saved-account Play Now"
    elif "click_here_to_play" in recognized_candidate_names:
        state = "click_here_to_play"
        confidence = max(float(candidate.confidence) for candidate in candidates if candidate.name == "click_here_to_play")
        next_step = "click Click here to play"
    elif summary.get("loadedSceneVerified"):
        state = "loaded_scene"
        confidence = 0.95
        next_step = "rebind daemon and continue live workflow"
    elif summary.get("staleLoggedInNoScene"):
        state = "stale_logged_in_no_scene"
        confidence = 0.85
        next_step = "refresh screenshot/login state and recover loaded scene"
    else:
        game_state = str(summary.get("gameState") or "").upper()
        if game_state == "LOGIN_SCREEN":
            state = "login_screen"
            confidence = 0.70
            next_step = "manual_login_required if no safe saved-account button is detected"
        elif summary.get("loggedIn"):
            state = "loading"
            confidence = 0.55
            next_step = "wait for loaded-scene proof"
    selected = selected_candidate.to_dict() if selected_candidate else None
    bounds_physical = physical_window_bounds(window)
    bounds_logical = logical_window_bounds(window)
    return {
        "schema": BOOTSTRAP_STATE_SCHEMA,
        "state": state,
        "confidence": round(float(confidence), 3),
        "evidenceSources": [
            "plugin_health" if summary.get("snapshotReachable") else "plugin_endpoint_down",
            "snapshot_game_state",
            "world_model",
            "client_tick_hot",
            "baseline",
            "screenshot_classifier" if candidates else "no_screenshot_button",
            "window_title" if dict_value(window).get("matchedWindowTitle") else "window_not_found",
            "button_geometry" if candidates else "no_button_geometry",
        ],
        "runeLiteWindowBounds": bounds_physical,
        "logicalWindowBounds": bounds_logical,
        "runeLiteCanvasBounds": canvas_bounds,
        "screenshotPath": screenshot_path,
        "detectedButtons": [candidate.to_dict() for candidate in candidates],
        "selectedBootstrapAction": selected,
        "targetPointLogical": dict_value(selected).get("targetPointLogical") if selected else None,
        "targetPointPhysical": dict_value(selected).get("targetPointPhysical") if selected else None,
        "coordinateScale": window_coordinate_scale(window),
        "targetInsideRuneLiteWindow": bool(dict_value(selected).get("targetInsideRuneLiteWindow")) if selected else False,
        "targetInsideSafeClickRegion": bool(dict_value(selected).get("targetInsideSafeClickRegion")) if selected else False,
        "expectedStateAfterClick": dict_value(selected).get("expectedStateAfterClick") if selected else None,
        "verificationResult": verification_result or {},
        "blocker": blocker,
        "nextStep": next_step,
        "loadedSceneProof": {
            "gameState": summary.get("gameState"),
            "latestTick": summary.get("latestTick"),
            "baselinePresent": summary.get("baselinePresent"),
            "clientTickHotPresent": summary.get("clientTickHotPresent"),
            "worldModelAvailable": summary.get("worldModelAvailable"),
            "objectTotal": summary.get("worldModelObjectTotal"),
            "loadedSceneVerified": summary.get("loadedSceneVerified"),
            "staleLoggedInNoScene": summary.get("staleLoggedInNoScene"),
        },
    }


def launch_runelite(command: str, *, execute: bool) -> dict[str, Any]:
    if not execute:
        return {"runeliteLaunched": False, "launchMethod": "dry_run", "command": command, "pid": None}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(command, cwd=str(PROJECT_ROOT), shell=True, creationflags=creationflags)
        return {"runeliteLaunched": True, "launchMethod": "subprocess", "command": command, "pid": process.pid}
    except Exception as error:  # noqa: BLE001
        return {"runeliteLaunched": False, "launchMethod": "subprocess", "command": command, "pid": None, "error": f"{type(error).__name__}: {error}"}


def stop_existing_runelite_dev_clients(*, execute: bool) -> dict[str, Any]:
    if not execute:
        return {"stopped": 0, "warnings": [], "reason": "dry_run"}
    project = str(PROJECT_ROOT).replace("'", "''")
    script = f"""
$project = '{project}'
$processes = Get-CimInstance Win32_Process | Where-Object {{
  ($_.CommandLine -like '*com.osrstelemetry.TelemetryPluginTest*') -or
  ($_.CommandLine -like ('*' + $project + '*GradleWrapperMain run*')) -or
  ($_.CommandLine -like ('*' + $project + '*gradle-wrapper.jar*'))
}}
$ids = @($processes | ForEach-Object {{ $_.ProcessId }})
foreach ($id in $ids) {{
  try {{ Stop-Process -Id $id -Force -ErrorAction Stop }} catch {{ }}
}}
$ids -join ','
"""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stopped_ids = [item for item in (completed.stdout or "").strip().split(",") if item.strip()]
        warnings = []
        if completed.returncode != 0 and (completed.stderr or "").strip():
            warnings.append((completed.stderr or "existing RuneLite dev cleanup failed").strip())
        return {"stopped": len(stopped_ids), "processIds": stopped_ids, "warnings": warnings}
    except Exception as error:  # noqa: BLE001
        return {"stopped": 0, "warnings": [f"existing RuneLite dev cleanup failed: {type(error).__name__}: {error}"]}


def title_filters(window_title_filter: str) -> list[str]:
    return bootstrap_window.unique_title_filters(window_title_filter)


def window_prepare_options(args: argparse.Namespace, *, execute: bool) -> dict[str, Any]:
    return {
        "move_to_secondary": bool(args.move_to_secondary_monitor),
        "monitor_index": int(args.monitor_index),
        "window_x": args.window_x,
        "window_y": args.window_y,
        "window_width": args.window_width,
        "window_height": args.window_height,
        "fallback_win_shift_arrow": bool(args.fallback_win_shift_arrow),
        "execute": execute,
    }


def prepare_window(filters: list[str], options: dict[str, Any]) -> dict[str, Any]:
    return bootstrap_window.find_and_prepare_window(filters, **options)


def find_window(filters: list[str]) -> dict[str, Any]:
    try:
        bootstrap_window.enable_windows_dpi_awareness()
        import pygetwindow  # type: ignore
    except ImportError:
        return {
            "matchedWindowTitle": None,
            "windowBounds": None,
            "focused": False,
            "focusMethod": "unavailable",
            "warnings": ["pygetwindow unavailable; install with: pip install pygetwindow"],
        }
    lowered = [item.lower() for item in filters]
    windows = []
    try:
        windows = list(pygetwindow.getAllWindows())
    except Exception as error:  # noqa: BLE001
        return {
            "matchedWindowTitle": None,
            "windowBounds": None,
            "focused": False,
            "focusMethod": "pygetwindow",
            "warnings": [f"window enumeration failed: {type(error).__name__}: {error}"],
        }
    for window in windows:
        title = window.title or ""
        if any(hint in title.lower() for hint in lowered):
            handle = int(getattr(window, "_hWnd", 0) or 0)
            return bootstrap_window.enrich_window_geometry({
                "matchedWindowTitle": title,
                "windowHandle": handle,
                "windowBounds": {
                    "x": int(getattr(window, "left", 0)),
                    "y": int(getattr(window, "top", 0)),
                    "width": int(getattr(window, "width", 0)),
                    "height": int(getattr(window, "height", 0)),
                },
                "focused": False,
                "focusMethod": "pygetwindow_match",
                "warnings": [],
            }, handle)
    return {
        "matchedWindowTitle": None,
        "windowBounds": None,
        "focused": False,
        "focusMethod": "not_found",
        "warnings": ["RuneLite/Jagex window not found"],
    }


def focus_matched_os_window(window: dict[str, Any] | None, *, execute: bool) -> dict[str, Any]:
    if not execute:
        return {"focused": False, "focusMethod": "dry_run", "warnings": []}
    title = str((window or {}).get("matchedWindowTitle") or "")
    if not title:
        return {"focused": False, "focusMethod": "no_window", "warnings": ["window focus skipped: no matched window"]}
    handle = int(dict_value(window).get("windowHandle") or 0)
    try:
        bootstrap_window.enable_windows_dpi_awareness()
        import pygetwindow  # type: ignore
    except Exception as error:  # noqa: BLE001
        fallback = bootstrap_window.focus_window_handle(handle)
        fallback["warnings"] = [f"pygetwindow unavailable: {type(error).__name__}: {error}", *list(fallback.get("warnings") or [])]
        return fallback
    try:
        matches = list(pygetwindow.getAllWindows())
        target = None
        for item in matches:
            if handle and int(getattr(item, "_hWnd", 0) or 0) == handle:
                target = item
                break
        if target is None:
            for item in matches:
                if str(getattr(item, "title", "") or "") == title:
                    target = item
                    break
        if target is None:
            return {"focused": False, "focusMethod": "pygetwindow", "warnings": [f"window focus skipped: {title} not found"]}
        restore = getattr(target, "restore", None)
        if callable(restore):
            restore()
        activate = getattr(target, "activate", None)
        if callable(activate):
            activate()
        active = pygetwindow.getActiveWindow()
        active_title = str(getattr(active, "title", "") or "")
        focused = bool(active_title == title or (title and title.lower() in active_title.lower()))
        result = {
            "focused": focused,
            "focusMethod": "pygetwindow_activate",
            "foregroundTitle": active_title,
            "warnings": [] if focused else [f"window focus not confirmed; foreground={active_title!r}"],
        }
        if focused:
            return result
        fallback = bootstrap_window.focus_window_handle(handle)
        fallback_warnings = list(result.get("warnings") or []) + list(fallback.get("warnings") or [])
        fallback["warnings"] = list(dict.fromkeys(str(item) for item in fallback_warnings))
        return fallback
    except Exception as error:  # noqa: BLE001
        fallback = bootstrap_window.focus_window_handle(handle)
        fallback["warnings"] = [f"pygetwindow focus failed: {type(error).__name__}: {error}", *list(fallback.get("warnings") or [])]
        return fallback


def focus_window(backend: Any, *, execute: bool, window: dict[str, Any] | None = None) -> dict[str, Any]:
    if not execute:
        return {"focused": False, "focusMethod": "dry_run", "warnings": []}
    if getattr(backend, "name", "") == "arduino":
        return focus_matched_os_window(window, execute=execute)
    try:
        focused = bool(backend.focus_window()) if hasattr(backend, "focus_window") else False
        return {"focused": focused, "focusMethod": "input_backend", "warnings": [] if focused else ["window focus not confirmed"]}
    except Exception as error:  # noqa: BLE001
        return {"focused": False, "focusMethod": "input_backend", "warnings": [f"window focus failed: {type(error).__name__}: {error}"]}


def normalize_snapshot_geometry(snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    baseline = snapshot_baseline(snapshot_payload)
    geometry = dict_value(baseline.get("inputGeometry") or snapshot_payload.get("inputGeometry"))
    return normalize_input_geometry(geometry, source_tick=snapshot_payload.get("latestTick"))


def bootstrap_surface_bounds(snapshot_payload: dict[str, Any] | None, window: dict[str, Any] | None) -> dict[str, Any] | None:
    if isinstance(snapshot_payload, dict):
        geometry = normalize_snapshot_geometry(snapshot_payload)
        origin = dict_value(geometry.get("canvasScreenOrigin"))
        size = dict_value(geometry.get("canvasSize"))
        display_scale = dict_value(geometry.get("displayScale"))
        try:
            if geometry.get("inputGeometryAvailable") and origin and size:
                logical = {
                    "x": int(round(float(origin.get("x") or 0))),
                    "y": int(round(float(origin.get("y") or 0))),
                    "width": max(1, int(round(float(size.get("width") or 0)))),
                    "height": max(1, int(round(float(size.get("height") or 0)))),
                }
                scale_x = float(display_scale.get("x") or 1.0)
                scale_y = float(display_scale.get("y") or 1.0)
                physical = dict(logical)
                applied = False
                window_physical = physical_window_bounds(window)
                scaled = {
                    "x": int(round(float(logical["x"]) * scale_x)),
                    "y": int(round(float(logical["y"]) * scale_y)),
                    "width": max(1, int(round(float(logical["width"]) * scale_x))),
                    "height": max(1, int(round(float(logical["height"]) * scale_y))),
                }
                if (
                    window_physical
                    and (abs(scale_x - 1.0) > 0.01 or abs(scale_y - 1.0) > 0.01)
                    and scaled["x"] >= int(window_physical["x"]) - 24
                    and scaled["y"] >= int(window_physical["y"]) - 24
                    and scaled["x"] + scaled["width"] <= int(window_physical["x"]) + int(window_physical["width"]) + 24
                    and scaled["y"] + scaled["height"] <= int(window_physical["y"]) + int(window_physical["height"]) + 24
                ):
                    physical = scaled
                    applied = True
                return {
                    **physical,
                    "logicalBounds": logical,
                    "physicalBounds": physical,
                    "coordinateScale": {
                        "x": scale_x if applied else 1.0,
                        "y": scale_y if applied else 1.0,
                        "applied": applied,
                        "source": "input_geometry_canvas_scaled_to_physical" if applied else "input_geometry_canvas",
                    },
                }
        except Exception:  # noqa: BLE001
            pass
    return physical_window_bounds(window)


def candidates_from_geometry(snapshot_payload: dict[str, Any], window: dict[str, Any] | None = None) -> list[StartupButtonCandidate]:
    geometry = normalize_snapshot_geometry(snapshot_payload)
    if not geometry.get("inputGeometryAvailable"):
        return []
    source_size = dict_value(geometry.get("sourceCanvasSize") or geometry.get("canvasSize"))
    source_width = int(source_size.get("width") or 765)
    source_height = int(source_size.get("height") or 503)
    surface_bounds = bootstrap_surface_bounds(snapshot_payload, window)
    candidates: list[StartupButtonCandidate] = []
    for name, x_pct, y_pct, confidence, reason in BUTTON_ZONES:
        canvas_point = {"x": int(round(source_width * x_pct)), "y": int(round(source_height * y_pct))}
        point = window_percent_point(window, x_pct, y_pct, surface_bounds=surface_bounds) if surface_bounds and window else None
        if point:
            screen_point = point["targetPointPhysical"]
            target_point_logical = point["targetPointLogical"]
            target_point_physical = point["targetPointPhysical"]
            coordinate_scale = point["coordinateScale"]
            target_validation = point["targetValidation"]
        else:
            resolution = resolve_screen_click_point(canvas_point, click_point_space="canvas", input_geometry=geometry)
            screen_point = resolution.get("screenClickPoint") if isinstance(resolution, dict) and isinstance(resolution.get("screenClickPoint"), dict) else None
            target_point_logical = screen_point
            target_point_physical = screen_point
            coordinate_scale = None
            target_validation = None
        candidates.append(
            StartupButtonCandidate(
                name=name,
                source="canvas_percent",
                screen_point=screen_point,
                canvas_point=canvas_point,
                confidence=confidence,
                reason=reason,
                target_point_logical=target_point_logical,
                target_point_physical=target_point_physical,
                coordinate_scale=coordinate_scale,
                target_validation=target_validation,
                expected_state_after_click=candidate_expected_state(name),
            )
        )
    return candidates


def candidates_from_window(window: dict[str, Any] | None) -> list[StartupButtonCandidate]:
    bounds = physical_window_bounds(window)
    if not bounds:
        return []
    width = max(1, int(bounds.get("width") or 1))
    height = max(1, int(bounds.get("height") or 1))
    if width < 500 or height < 400:
        return []
    candidates = []
    for name, x_pct, y_pct, confidence, reason in WINDOW_PERCENT_ZONES:
        point = window_percent_point(window, x_pct, y_pct)
        if not point:
            continue
        candidates.append(
            StartupButtonCandidate(
                name=name,
                source="calibrated_screen",
                screen_point=point["targetPointPhysical"],
                canvas_point=None,
                confidence=confidence,
                reason=reason,
                target_point_logical=point["targetPointLogical"],
                target_point_physical=point["targetPointPhysical"],
                coordinate_scale=point["coordinateScale"],
                target_validation=point["targetValidation"],
                expected_state_after_click=candidate_expected_state(name),
            )
        )
    return candidates


def _default_screenshot() -> Any:
    try:
        from PIL import ImageGrab

        return ImageGrab.grab(all_screens=True)
    except Exception:  # noqa: BLE001
        pass
    import pyautogui  # type: ignore

    return pyautogui.screenshot()


def _mean_luma(image: Any) -> float:
    if image is None:
        return 0.0
    gray = image.convert("L") if hasattr(image, "convert") else image
    stat_pixels = list(gray.getdata()) if hasattr(gray, "getdata") else []
    return float(sum(stat_pixels)) / max(1, len(stat_pixels))


def _luma_stats(image: Any) -> dict[str, float]:
    if image is None:
        return {"mean": 0.0, "stddev": 0.0, "brightRatio": 0.0, "darkRatio": 0.0}
    gray = image.convert("L") if hasattr(image, "convert") else image
    pixels = list(gray.getdata()) if hasattr(gray, "getdata") else []
    if not pixels:
        return {"mean": 0.0, "stddev": 0.0, "brightRatio": 0.0, "darkRatio": 0.0}
    mean = float(sum(pixels)) / len(pixels)
    variance = float(sum((float(pixel) - mean) ** 2 for pixel in pixels)) / len(pixels)
    bright = sum(1 for pixel in pixels if int(pixel) >= 150)
    dark = sum(1 for pixel in pixels if int(pixel) <= 50)
    return {
        "mean": mean,
        "stddev": variance**0.5,
        "brightRatio": float(bright) / len(pixels),
        "darkRatio": float(dark) / len(pixels),
    }


def _crop(image: Any, box: tuple[int, int, int, int]) -> Any | None:
    try:
        return image.crop(box)
    except Exception:  # noqa: BLE001
        return None


def saved_account_play_candidate(
    window: dict[str, Any] | None,
    *,
    screenshot_func: Callable[[], Any] | None = None,
    surface_bounds: dict[str, int] | None = None,
) -> tuple[StartupButtonCandidate | None, list[str]]:
    bounds = int_bounds(surface_bounds) or physical_window_bounds(window)
    if not bounds or not window_looks_like_runelite(window):
        return None, []
    x = int(bounds.get("x") or 0)
    y = int(bounds.get("y") or 0)
    width = max(1, int(bounds.get("width") or 1))
    height = max(1, int(bounds.get("height") or 1))
    if width < 500 or height < 400:
        return None, []
    try:
        screenshot = (screenshot_func or _default_screenshot)()
    except Exception as error:  # noqa: BLE001
        return None, [f"saved-account screenshot unavailable: {type(error).__name__}: {error}"]
    button_region = _crop(
        screenshot,
        (
            int(round(x + width * 0.38)),
            int(round(y + height * 0.42)),
            int(round(x + width * 0.62)),
            int(round(y + height * 0.545)),
        ),
    )
    upper_label_region = _crop(
        screenshot,
        (
            int(round(x + width * 0.42)),
            int(round(y + height * 0.28)),
            int(round(x + width * 0.58)),
            int(round(y + height * 0.36)),
        ),
    )
    if not button_region or not upper_label_region:
        return None, ["saved-account Play Now screenshot crop unavailable"]
    button_stats = _luma_stats(button_region)
    label_stats = _luma_stats(upper_label_region)
    detected = (
        25.0 <= button_stats["mean"] <= 115.0
        and button_stats["stddev"] >= 20.0
        and button_stats["brightRatio"] >= 0.01
        and button_stats["darkRatio"] >= 0.15
        and label_stats["stddev"] >= 20.0
    )
    if not detected:
        return None, ["saved-account Play Now button not confidently recognized"]
    point = window_percent_point(window, 0.50, 0.485, button_box=(0.38, 0.42, 0.62, 0.545), surface_bounds=surface_bounds or bounds)
    if not point:
        return None, ["saved-account Play Now physical window bounds unavailable"]
    return (
        StartupButtonCandidate(
            name="play_now",
            source="saved_account_play_panel",
            screen_point=point["targetPointPhysical"],
            canvas_point=None,
            confidence=0.90,
            reason="recognized saved-account Play Now button",
            button_bounds_logical=point["buttonBoundsLogical"],
            button_bounds_physical=point["buttonBoundsPhysical"],
            target_point_logical=point["targetPointLogical"],
            target_point_physical=point["targetPointPhysical"],
            coordinate_scale=point["coordinateScale"],
            target_validation=point["targetValidation"],
            expected_state_after_click=candidate_expected_state("play_now"),
        ),
        [],
    )


def disconnected_dialog_candidate(
    window: dict[str, Any] | None,
    *,
    screenshot_func: Callable[[], Any] | None = None,
    surface_bounds: dict[str, int] | None = None,
) -> tuple[StartupButtonCandidate | None, list[str]]:
    bounds = int_bounds(surface_bounds) or physical_window_bounds(window)
    if not bounds or not window_looks_like_runelite(window):
        return None, []
    x = int(bounds.get("x") or 0)
    y = int(bounds.get("y") or 0)
    width = max(1, int(bounds.get("width") or 1))
    height = max(1, int(bounds.get("height") or 1))
    if width < 500 or height < 400:
        return None, []
    try:
        screenshot = (screenshot_func or _default_screenshot)()
    except Exception as error:  # noqa: BLE001
        return None, [f"disconnected dialog screenshot unavailable: {type(error).__name__}: {error}"]

    _name, x_pct, y_pct, confidence, reason = DISCONNECTED_OK_ZONE
    point = window_percent_point(window, x_pct, y_pct, button_box=(0.40, 0.565, 0.60, 0.655), surface_bounds=surface_bounds or bounds)
    if not point:
        return None, ["disconnected dialog physical window bounds unavailable"]
    center_x = int(point["targetPointPhysical"]["x"])
    center_y = int(point["targetPointPhysical"]["y"])
    dialog_region = _crop(
        screenshot,
        (
            int(round(x + width * 0.25)),
            int(round(y + height * 0.34)),
            int(round(x + width * 0.75)),
            int(round(y + height * 0.69)),
        ),
    )
    button_region = _crop(screenshot, (center_x - 120, center_y - 45, center_x + 120, center_y + 45))
    top_band = _crop(screenshot, (center_x - 120, center_y - 45, center_x + 120, center_y - 30))
    bottom_band = _crop(screenshot, (center_x - 120, center_y + 30, center_x + 120, center_y + 45))
    if not dialog_region or not button_region or not top_band or not bottom_band:
        return None, ["disconnected dialog screenshot crop unavailable"]

    dialog_luma = _mean_luma(dialog_region)
    button_luma = _mean_luma(button_region)
    button_stats = _luma_stats(button_region)
    border_luma = min(_mean_luma(top_band), _mean_luma(bottom_band))
    detected = (
        28.0 <= dialog_luma <= 120.0
        and 20.0 <= button_luma <= 115.0
        and border_luma < button_luma + 10.0
        and (button_stats["stddev"] >= 12.0 or button_stats["brightRatio"] >= 0.004)
    )
    if not detected:
        return None, ["disconnected dialog OK button not confidently recognized"]
    return (
        StartupButtonCandidate(
            name="disconnected_ok",
            source="disconnected_dialog",
            screen_point=point["targetPointPhysical"],
            canvas_point=None,
            confidence=confidence,
            reason=reason,
            button_bounds_logical=point["buttonBoundsLogical"],
            button_bounds_physical=point["buttonBoundsPhysical"],
            target_point_logical=point["targetPointLogical"],
            target_point_physical=point["targetPointPhysical"],
            coordinate_scale=point["coordinateScale"],
            target_validation=point["targetValidation"],
            expected_state_after_click=candidate_expected_state("disconnected_ok"),
        ),
        [],
    )


def click_here_to_play_candidate(
    window: dict[str, Any] | None,
    *,
    screenshot_func: Callable[[], Any] | None = None,
    surface_bounds: dict[str, Any] | None = None,
) -> tuple[StartupButtonCandidate | None, list[str]]:
    bounds = int_bounds(surface_bounds) or physical_window_bounds(window)
    if not bounds or not window_looks_like_runelite(window):
        return None, []
    x = int(bounds.get("x") or 0)
    y = int(bounds.get("y") or 0)
    width = max(1, int(bounds.get("width") or 1))
    height = max(1, int(bounds.get("height") or 1))
    if width < 500 or height < 400:
        return None, []
    try:
        screenshot = (screenshot_func or _default_screenshot)()
    except Exception as error:  # noqa: BLE001
        return None, [f"Click here to play screenshot unavailable: {type(error).__name__}: {error}"]
    button_region = _crop(
        screenshot,
        (
            int(round(x + width * 0.34)),
            int(round(y + height * 0.58)),
            int(round(x + width * 0.66)),
            int(round(y + height * 0.76)),
        ),
    )
    banner_region = _crop(
        screenshot,
        (
            int(round(x + width * 0.18)),
            int(round(y + height * 0.00)),
            int(round(x + width * 0.82)),
            int(round(y + height * 0.08)),
        ),
    )
    if not button_region or not banner_region:
        return None, ["Click here to play screenshot crop unavailable"]
    try:
        button_rgb = button_region.convert("RGB")
        pixels = list(button_rgb.getdata()) if hasattr(button_rgb, "getdata") else []
    except Exception:  # noqa: BLE001
        pixels = []
    if not pixels:
        return None, ["Click here to play button pixels unavailable"]
    count = max(1, len(pixels))
    red_mean = sum(int(pixel[0]) for pixel in pixels) / count
    green_mean = sum(int(pixel[1]) for pixel in pixels) / count
    blue_mean = sum(int(pixel[2]) for pixel in pixels) / count
    redish_ratio = sum(1 for red, green, blue in pixels if int(red) > 70 and int(red) > int(green) * 1.25 and int(red) > int(blue) * 1.25) / count
    banner_stats = _luma_stats(banner_region)
    detected = (
        redish_ratio >= 0.18
        and red_mean >= green_mean + 15.0
        and red_mean >= blue_mean + 20.0
        and banner_stats["brightRatio"] >= 0.12
    )
    if not detected:
        return None, []
    point = window_percent_point(window, 0.50, 0.67, button_box=(0.34, 0.58, 0.66, 0.76), surface_bounds=surface_bounds or bounds)
    if not point:
        return None, ["Click here to play physical window bounds unavailable"]
    return (
        StartupButtonCandidate(
            name="click_here_to_play",
            source="welcome_panel",
            screen_point=point["targetPointPhysical"],
            canvas_point=None,
            confidence=0.88,
            reason="recognized Click here to play welcome panel",
            button_bounds_logical=point["buttonBoundsLogical"],
            button_bounds_physical=point["buttonBoundsPhysical"],
            target_point_logical=point["targetPointLogical"],
            target_point_physical=point["targetPointPhysical"],
            coordinate_scale=point["coordinateScale"],
            target_validation=point["targetValidation"],
            expected_state_after_click=candidate_expected_state("click_here_to_play"),
        ),
        [],
    )


def candidate_method(source: str) -> str:
    if source == "template":
        return "template"
    if source == "disconnected_dialog":
        return "disconnected_dialog"
    if source == "saved_account_play_panel":
        return "saved_account_play_panel"
    if source == "welcome_panel":
        return "welcome_panel"
    if source in {"canvas_percent", "calibrated_screen"}:
        return "percent_fallback"
    return "heuristic"


def candidate_from_vision(candidate: bootstrap_vision.VisionButtonCandidate) -> StartupButtonCandidate:
    return StartupButtonCandidate(
        name=candidate.name,
        source=candidate.source,
        screen_point=candidate.screen_point,
        canvas_point=candidate.canvas_point,
        confidence=candidate.confidence,
        reason=candidate.reason,
    )


def attach_window_validation(candidate: StartupButtonCandidate, window: dict[str, Any] | None) -> StartupButtonCandidate:
    if not candidate.screen_point:
        return candidate
    validation = candidate.target_validation or validate_bootstrap_click_point(candidate.screen_point, window)
    return replace(
        candidate,
        target_point_physical=candidate.target_point_physical or dict(candidate.screen_point),
        target_point_logical=candidate.target_point_logical or dict(candidate.screen_point),
        coordinate_scale=candidate.coordinate_scale or window_coordinate_scale(window),
        target_validation=validation,
        expected_state_after_click=candidate.expected_state_after_click or candidate_expected_state(candidate.name),
    )


def button_candidates(
    snapshot_payload: dict[str, Any] | None,
    window: dict[str, Any] | None,
    *,
    save_debug_screenshot: bool = False,
    template_dir: Path = BOOTSTRAP_TEMPLATE_DIR,
    template_confidence: float = 0.85,
    vision_candidate_func: Callable[..., tuple[list[Any], list[str]]] = bootstrap_vision.template_candidates,
    saved_account_candidate_func: Callable[..., tuple[StartupButtonCandidate | None, list[str]]] = saved_account_play_candidate,
    disconnected_candidate_func: Callable[..., tuple[StartupButtonCandidate | None, list[str]]] = disconnected_dialog_candidate,
    click_here_candidate_func: Callable[..., tuple[StartupButtonCandidate | None, list[str]]] = click_here_to_play_candidate,
    final_play_pending: bool = False,
    clicked_names: list[str] | None = None,
) -> tuple[list[StartupButtonCandidate], list[str]]:
    vision_candidates, vision_warnings = vision_candidate_func(
        template_dir,
        save_debug_screenshot=save_debug_screenshot,
        confidence=template_confidence,
    )
    if vision_candidates:
        return [attach_window_validation(candidate_from_vision(candidate), window) for candidate in vision_candidates], vision_warnings
    pre_summary = snapshot_summary(snapshot_payload, reachable=isinstance(snapshot_payload, dict)) if isinstance(snapshot_payload, dict) else {}
    if pre_summary.get("loggedIn") and pre_summary.get("staleLoggedInNoScene") and not pre_summary.get("finalPlayPanelPending") and not final_play_pending:
        vision_warnings.append("LOGGED_IN without loaded-scene proof has no recognized play surface; refusing percent fallback")
        return [], vision_warnings
    if window_looks_like_runelite(window):
        summary = pre_summary
        surface_bounds = bootstrap_surface_bounds(snapshot_payload, window)
        window_has_handle = bool(dict_value(window).get("windowHandle"))
        already_clicked = set(clicked_names or [])
        prefer_disconnected_dialog = (
            not summary.get("loggedIn")
            and str(summary.get("baselineGameState") or "").upper() == "LOGGED_IN"
            and str(summary.get("worldModelGameState") or "").upper() == "LOGIN_SCREEN"
            and int(summary.get("worldModelObjectTotal") or 0) == 0
            and "disconnected_ok" not in already_clicked
        )

        def disconnected_candidate() -> StartupButtonCandidate | None:
            nonlocal vision_warnings
            if summary.get("loggedIn") or not (window_has_handle or disconnected_candidate_func is not disconnected_dialog_candidate):
                return None
            try:
                disconnected, disconnected_warnings = disconnected_candidate_func(window, surface_bounds=surface_bounds)
            except TypeError:
                disconnected, disconnected_warnings = disconnected_candidate_func(window)
            vision_warnings.extend(str(item) for item in disconnected_warnings)
            return disconnected

        def saved_account_candidate() -> StartupButtonCandidate | None:
            nonlocal vision_warnings
            if summary.get("loggedIn") or not (window_has_handle or saved_account_candidate_func is not saved_account_play_candidate):
                return None
            try:
                saved_account, saved_account_warnings = saved_account_candidate_func(window, surface_bounds=surface_bounds)
            except TypeError:
                saved_account, saved_account_warnings = saved_account_candidate_func(window)
            vision_warnings.extend(str(item) for item in saved_account_warnings)
            return saved_account

        def click_here_candidate() -> StartupButtonCandidate | None:
            nonlocal vision_warnings
            if not (window_has_handle or click_here_candidate_func is not click_here_to_play_candidate):
                return None
            try:
                click_here, click_here_warnings = click_here_candidate_func(window, surface_bounds=surface_bounds)
            except TypeError:
                click_here, click_here_warnings = click_here_candidate_func(window)
            vision_warnings.extend(str(item) for item in click_here_warnings)
            return click_here

        disconnected = disconnected_candidate()
        click_here = click_here_candidate()
        saved = saved_account_candidate() if not click_here and not (prefer_disconnected_dialog and disconnected) else None
        if click_here:
            candidate = click_here
        elif prefer_disconnected_dialog and disconnected:
            candidate = disconnected
        elif saved:
            candidate = saved
        elif disconnected:
            candidate = disconnected
        else:
            candidate = None
        if candidate:
            return [attach_window_validation(candidate, window)], vision_warnings
        if prefer_disconnected_dialog:
            vision_warnings.append(
                "stale LOGGED_IN/login-screen evidence requires a recognized disconnected OK or saved-account button before clicking"
            )
            return [], vision_warnings
    if isinstance(snapshot_payload, dict):
        candidates = candidates_from_geometry(snapshot_payload, window)
        if pre_summary.get("loggedIn") and not pre_summary.get("finalPlayPanelPending") and not final_play_pending:
            candidates = []
        if candidates:
            return [attach_window_validation(candidate, window) for candidate in candidates], vision_warnings
        if pre_summary.get("loggedIn") and not pre_summary.get("finalPlayPanelPending") and not final_play_pending:
            return [], vision_warnings
    return [attach_window_validation(candidate, window) for candidate in candidates_from_window(window)], vision_warnings


def window_title_text(window: dict[str, Any] | None) -> str:
    return str(dict_value(window).get("matchedWindowTitle") or "")


def window_title_at_screen_point(point: dict[str, Any] | tuple[int, int] | None) -> dict[str, Any]:
    point_dict = dict_value(point) if isinstance(point, dict) else {}
    if isinstance(point, tuple) and len(point) >= 2:
        point_dict = {"x": point[0], "y": point[1]}
    try:
        x = int(round(float(point_dict.get("x"))))
        y = int(round(float(point_dict.get("y"))))
    except (TypeError, ValueError):
        return {"available": False, "reason": "invalid_point"}
    if os.name != "nt":
        return {"available": False, "point": {"x": x, "y": y}, "reason": "non_windows"}
    try:
        import ctypes
        from ctypes import wintypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        user32 = ctypes.windll.user32
        hwnd = user32.WindowFromPoint(POINT(x, y))
        root = user32.GetAncestor(hwnd, 2) if hwnd else 0  # GA_ROOT
        handle = int(root or hwnd or 0)
        title = bootstrap_window.window_title_from_handle(handle)
        return {
            "available": bool(handle),
            "point": {"x": x, "y": y},
            "hwnd": int(hwnd or 0),
            "rootHwnd": int(root or 0),
            "title": title,
        }
    except Exception as error:  # noqa: BLE001
        return {"available": False, "point": {"x": x, "y": y}, "reason": f"{type(error).__name__}: {error}"}


def point_window_matches_allowed_title(point_info: dict[str, Any] | None, allowed_titles: list[str] | tuple[str, ...] | None) -> bool:
    if not allowed_titles:
        return False
    title = str((point_info or {}).get("title") or "").lower()
    return bool(title and any(str(item or "").lower() in title for item in allowed_titles))


def window_looks_like_launcher(window: dict[str, Any] | None) -> bool:
    title = window_title_text(window).lower()
    return "launcher" in title or "jagex" in title


def window_looks_like_jagex_launcher(window: dict[str, Any] | None) -> bool:
    title = window_title_text(window).lower()
    return "jagex" in title and "launcher" in title


def window_looks_like_runelite(window: dict[str, Any] | None) -> bool:
    return "runelite" in window_title_text(window).lower()


def preferred_candidate_names(
    snapshot_reachable: bool,
    window: dict[str, Any] | None = None,
    launcher_clicked: bool = False,
    *,
    prefer_saved_account_play_now: bool = False,
) -> list[str]:
    base = ["click_here_to_play", "continue", "play_now"] if snapshot_reachable or launcher_clicked else ["play_now", "continue", "click_here_to_play"]
    if window_looks_like_runelite(window):
        if prefer_saved_account_play_now and not launcher_clicked:
            return ["disconnected_ok", "play_now", "click_here_to_play", "continue"]
        if launcher_clicked:
            return ["disconnected_ok", "click_here_to_play", "continue", "play_now"]
        return ["disconnected_ok", *base]
    if snapshot_reachable or launcher_clicked:
        return base
    return base


def snapshot_has_game_context(snapshot_payload: dict[str, Any] | None) -> bool:
    if not isinstance(snapshot_payload, dict):
        return False
    summary = snapshot_summary(snapshot_payload, reachable=True)
    game_state = str(summary.get("gameState") or "").upper()
    return bool(game_state and game_state != "UNKNOWN")


def choose_candidate(
    candidates: list[StartupButtonCandidate],
    *,
    snapshot_reachable: bool,
    window: dict[str, Any] | None = None,
    launcher_clicked: bool = False,
    prefer_saved_account_play_now: bool = False,
) -> StartupButtonCandidate:
    preference = preferred_candidate_names(
        snapshot_reachable,
        window,
        launcher_clicked,
        prefer_saved_account_play_now=prefer_saved_account_play_now,
    )
    rank = {name: index for index, name in enumerate(preference)}
    return sorted(candidates, key=lambda item: (rank.get(item.name, 100), -float(item.confidence)))[0]


def final_play_click_pending(clicked: list[dict[str, Any]]) -> bool:
    launcher_clicked = any(item.get("name") in {"play_now", "continue"} for item in clicked)
    final_play_clicked = any(item.get("name") == "click_here_to_play" for item in clicked)
    return launcher_clicked and not final_play_clicked


def startup_movement_region(
    allowed_region: dict[str, Any] | None,
    start_position: tuple[int, int] | list[int] | None,
    *,
    max_padding_px: int = 64,
    max_corridor_px: int | None = None,
) -> dict[str, Any] | None:
    if not allowed_region or not start_position:
        return allowed_region
    try:
        start_x = int(start_position[0])
        start_y = int(start_position[1])
        left = int(allowed_region.get("x", 0))
        top = int(allowed_region.get("y", 0))
        right = left + int(allowed_region.get("width", 0))
        bottom = top + int(allowed_region.get("height", 0))
    except Exception:  # noqa: BLE001
        return allowed_region
    if left <= start_x <= right and top <= start_y <= bottom:
        return allowed_region
    padding = max(0, int(max_padding_px))
    if start_x < left and left - start_x <= padding:
        left = start_x
    elif start_x > right and start_x - right <= padding:
        right = start_x
    elif start_x < left or start_x > right:
        if max_corridor_px is None or min(abs(left - start_x), abs(start_x - right)) > max_corridor_px:
            return allowed_region
        left = min(left, start_x)
        right = max(right, start_x)
    if start_y < top and top - start_y <= padding:
        top = start_y
    elif start_y > bottom and start_y - bottom <= padding:
        bottom = start_y
    elif start_y < top or start_y > bottom:
        if max_corridor_px is None or min(abs(top - start_y), abs(start_y - bottom)) > max_corridor_px:
            return allowed_region
        top = min(top, start_y)
        bottom = max(bottom, start_y)
    return {
        "x": left,
        "y": top,
        "width": max(1, right - left),
        "height": max(1, bottom - top),
        "startupCursorPaddingPx": padding,
        "startupCursorCorridorMaxPx": max_corridor_px,
        "startupMovementCorridorToWindow": not (
            int(allowed_region.get("x", 0)) <= start_x <= int(allowed_region.get("x", 0)) + int(allowed_region.get("width", 0))
            and int(allowed_region.get("y", 0)) <= start_y <= int(allowed_region.get("y", 0)) + int(allowed_region.get("height", 0))
        ),
        "startupOriginalAllowedRegion": dict(allowed_region),
    }


def click_candidate(
    candidate: StartupButtonCandidate,
    *,
    backend: Any,
    movement_profile: str,
    input_profile: str = "steady",
    live_input_backend_required: bool = False,
    seed: int | None = None,
    allowed_region: dict[str, Any] | None = None,
    allowed_foreground_titles: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    backend_name = str(getattr(backend, "name", backend.__class__.__name__) or backend.__class__.__name__)
    input_path = "HumanInputController/ArduinoHIDBackend" if backend_name == "arduino" else f"HumanInputController/{backend.__class__.__name__}"

    def backend_status_payload() -> dict[str, Any]:
        status_func = getattr(backend, "status", None)
        if callable(status_func):
            try:
                status = status_func()
                return status if isinstance(status, dict) else {}
            except Exception as error:  # noqa: BLE001
                return {"statusError": f"{type(error).__name__}: {error}"}
        return {}

    if not candidate.screen_point:
        return {"status": "FAIL", "warning": "candidate has no screen point", "inputBackend": backend_name, "inputPathUsed": input_path}
    validation = dict_value(candidate.target_validation)
    if validation and validation.get("targetValidationStatus") == "FAIL":
        return {
            "status": "FAIL",
            "warning": str(validation.get("targetValidationReason") or "candidate outside safe RuneLite click region"),
            "targetValidation": validation,
            "inputBackend": backend_name,
            "inputPathUsed": input_path,
        }
    if allowed_region and not (
        int(allowed_region.get("x", 0)) <= int(candidate.screen_point["x"]) <= int(allowed_region.get("x", 0)) + int(allowed_region.get("width", 0))
        and int(allowed_region.get("y", 0)) <= int(candidate.screen_point["y"]) <= int(allowed_region.get("y", 0)) + int(allowed_region.get("height", 0))
    ):
        return {"status": "FAIL", "warning": "candidate outside allowed startup window", "inputBackend": backend_name, "inputPathUsed": input_path}
    effective_foreground_titles = ["RuneLite"] if allowed_foreground_titles is None else list(allowed_foreground_titles)
    start_position = backend.current_position()
    movement_region = startup_movement_region(allowed_region, start_position, max_padding_px=192, max_corridor_px=4096)
    configure_safety = getattr(backend, "configure_movement_safety", None)
    if callable(configure_safety) and movement_region:
        configure_safety(
            allowed_region=movement_region,
            allowed_foreground_titles=effective_foreground_titles,
            enabled=True,
            margin_px=0,
            max_chunk_px=12,
            tolerance_px=4,
            feedback_tolerance_px=10,
        )
    start = MousePoint(*start_position)
    target = MouseTarget(
        x=int(candidate.screen_point["x"]),
        y=int(candidate.screen_point["y"]),
        radius_px=8,
        label=candidate.name,
        source=candidate.source,
    )
    profile = movement_profile
    if seed is not None:
        from input_control.mouse_movement import MouseMovementProfile

        profile = MouseMovementProfile(name=movement_profile, seed=seed)
    controller = HumanInputController(
        backend,
        profile=input_profile,
        seed=seed,
        live_input_backend_required=live_input_backend_required,
        software_input_allowed=not live_input_backend_required,
    )
    plan = controller.plan_mouse_movement(start, target, profile)
    if plan.validation_status == "FAIL":
        return {"status": "FAIL", "warning": "; ".join(plan.warnings) or "movement plan invalid", "inputBackend": backend_name, "inputPathUsed": input_path}
    try:
        controller.move_and_click(plan, button="left")
    except Exception as error:  # noqa: BLE001
        trace = getattr(backend, "last_movement_trace", None)
        backend_status = backend_status_payload()
        serial_trace = dict_value(backend_status.get("lastCommandTrace"))
        timeout_classification = serial_trace.get("timeoutClassification")
        retry_policy = "retry_not_safe"
        if timeout_classification in {"serial_timeout_before_command", "serial_timeout_during_move", "serial_timeout_waiting_for_ack"}:
            retry_policy = "retry_requires_screen_recheck"
        elif timeout_classification == "serial_timeout_during_click":
            retry_policy = "retry_requires_screen_recheck"
        return {
            "status": "FAIL",
            "warning": f"{type(error).__name__}: {error}",
            "movementPlan": plan.to_dict(include_points=False),
            "movementTrace": trace if isinstance(trace, dict) else None,
            "humanInput": controller.metrics(),
            "serialTrace": serial_trace or None,
            "backendStatus": backend_status or None,
            "timeoutClassification": timeout_classification,
            "bootstrapRetryPolicy": retry_policy,
            "retryRequiresScreenRecheck": retry_policy == "retry_requires_screen_recheck",
            "inputBackend": backend_name,
            "inputPathUsed": input_path,
        }
    backend_status = backend_status_payload()
    serial_trace = dict_value(backend_status.get("lastCommandTrace"))
    return {
        "status": "PASS",
        "movementPlan": plan.to_dict(include_points=False),
        "humanInput": controller.metrics(),
        "backendStatus": backend_status or None,
        "serialTrace": serial_trace or None,
        "inputBackend": backend_name,
        "inputPathUsed": input_path,
        "command": serial_trace.get("commandSent") or serial_trace.get("command"),
        "arduinoAck": serial_trace.get("ack") or serial_trace.get("ackLine"),
    }


def allowed_region_for_startup_candidate(
    candidate: StartupButtonCandidate,
    *,
    snapshot_payload: dict[str, Any] | None,
    window: dict[str, Any] | None,
    launcher_automation_allowed: bool = False,
) -> dict[str, Any] | None:
    if candidate.source == "canvas_percent" and isinstance(snapshot_payload, dict):
        surface = bootstrap_surface_bounds(snapshot_payload, window)
        bounds = int_bounds(dict_value(surface).get("physicalBounds")) or int_bounds(surface)
        if bounds:
            return bounds
    if window_looks_like_runelite(window) or launcher_automation_allowed:
        bounds = physical_window_bounds(window)
        return dict(bounds) if bounds else None
    return None


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


def daemon_reachable(daemon_url: str, *, timeout: float = 1.0) -> bool:
    try:
        payload = fetch_json(daemon_status_url(daemon_url), timeout=timeout)
        return bool(payload)
    except Exception:  # noqa: BLE001
        return False


def start_daemon(*, execute: bool) -> dict[str, Any]:
    if not execute:
        return {"started": False, "pid": None, "reason": "dry_run"}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(DAILY_DAEMON_COMMAND, cwd=str(PROJECT_ROOT), creationflags=creationflags)
        return {"started": True, "pid": process.pid, "reason": "started"}
    except Exception as error:  # noqa: BLE001
        return {"started": False, "pid": None, "reason": f"{type(error).__name__}: {error}"}


def run_live_qa(daemon_url: str, *, execute: bool) -> dict[str, Any]:
    command = [sys.executable, "telemetry-viewer\\run_woodcut_bank_live_qa.py", "--daemon-url", daemon_url, "--tail", "20"]
    if not execute:
        return {"status": "unknown", "ran": False, "reason": "dry_run", "cycleStage": "unknown", "warnings": []}
    completed = subprocess.run(command, cwd=str(PROJECT_ROOT), capture_output=True, text=True, check=False)
    output = (completed.stdout or "") + (completed.stderr or "")
    status = "FAIL" if completed.returncode else "PASS"
    for line in output.splitlines():
        if line.startswith("WOODCUT BANK LIVE QA - "):
            status = line.rsplit(" - ", 1)[-1].strip()
            break
    cycle_stage = "unknown"
    warnings: list[str] = []
    for line in output.splitlines():
        text = line.strip()
        if text.startswith("Stage:"):
            cycle_stage = text.split(":", 1)[1].strip()
        if text.startswith("WARN:"):
            warnings.append(text[5:].strip())
    return {"status": status, "ran": True, "exitCode": completed.returncode, "cycleStage": cycle_stage, "warnings": warnings}


def add_stage(stages: list[dict[str, Any]], stage: str, *, status: str = "PASS", reason: str = "") -> None:
    stages.append({"stage": stage, "status": status, "reason": reason})


def run_bootstrap(
    args: argparse.Namespace,
    *,
    fetch_snapshot_func: Callable[..., dict[str, Any]] = fetch_snapshot,
    backend: Any | None = None,
    window_finder: Callable[[list[str]], dict[str, Any]] = find_window,
    window_preparer: Callable[[list[str], dict[str, Any]], dict[str, Any]] = prepare_window,
    daemon_reachable_func: Callable[..., bool] = daemon_reachable,
    start_daemon_func: Callable[..., dict[str, Any]] = start_daemon,
    live_qa_func: Callable[..., dict[str, Any]] = run_live_qa,
    launch_func: Callable[..., dict[str, Any]] = launch_runelite,
    stop_existing_func: Callable[..., dict[str, Any]] = stop_existing_runelite_dev_clients,
    vision_candidate_func: Callable[..., tuple[list[Any], list[str]]] = bootstrap_vision.template_candidates,
    disconnected_candidate_func: Callable[..., tuple[StartupButtonCandidate | None, list[str]]] = disconnected_dialog_candidate,
    click_here_candidate_func: Callable[..., tuple[StartupButtonCandidate | None, list[str]]] = click_here_to_play_candidate,
    window_title_at_point_func: Callable[[dict[str, Any] | tuple[int, int] | None], dict[str, Any]] = window_title_at_screen_point,
    sleep_func: Callable[[float], None] = time.sleep,
    monotonic_func: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    execute = bool(getattr(args, "execute", False))
    stages: list[dict[str, Any]] = []
    warnings: list[str] = []
    failures: list[str] = []
    clicked: list[dict[str, Any]] = []
    candidates: list[StartupButtonCandidate] = []
    launcher_automation_allowed = bool(getattr(args, "allow_jagex_launcher_automation", False))
    launcher_automation_blocked_reason = None if launcher_automation_allowed else "jagex_launcher_automation_disabled_by_default"
    login_recovery_mode = "launcher_allowed" if launcher_automation_allowed else "runelite_dev_only"
    verify_loaded_scene = bool(getattr(args, "verify_loaded_scene", False) or getattr(args, "recover_loaded_scene", False))
    launch_summary = {"runeliteLaunched": False, "launchMethod": "skipped", "command": args.gradle_command, "pid": None}
    cleanup_summary = {"stopped": 0, "warnings": []}

    backend = backend or PyAutoGuiBackend(
        focus_runelite=True,
        window_title_filter=args.window_title_filter,
    )

    if args.launch_runelite:
        add_stage(stages, "waiting_for_process", reason="launch requested")
        if execute and not getattr(args, "keep_existing_runelite", False):
            cleanup_summary = stop_existing_func(execute=True)
            warnings.extend(str(item) for item in cleanup_summary.get("warnings") or [])
            if cleanup_summary.get("stopped"):
                add_stage(stages, "waiting_for_process", reason=f"stopped {cleanup_summary.get('stopped')} existing dev process(es)")
        launch_summary = launch_func(args.gradle_command, execute=True)
        launch_summary["stoppedExisting"] = cleanup_summary.get("stopped", 0)
        if not launch_summary.get("runeliteLaunched") and launch_summary.get("error"):
            warnings.append(str(launch_summary["error"]))
    elif args.skip_runelite_launch:
        add_stage(stages, "waiting_for_process", reason="launch skipped")

    filters = title_filters(args.window_title_filter)
    placement_requested = bool(
        args.move_to_secondary_monitor
        or args.window_x is not None
        or args.window_y is not None
        or args.window_width is not None
        or args.window_height is not None
        or args.fallback_win_shift_arrow
    )
    if placement_requested:
        window = window_preparer(filters, window_prepare_options(args, execute=execute))
    else:
        window = window_finder(filters)
    add_stage(stages, "waiting_for_window", status="PASS" if window.get("matchedWindowTitle") else "WARN", reason=str(window.get("matchedWindowTitle") or "window not found"))
    warnings.extend(str(item) for item in window.get("warnings") or [])
    focus = focus_window(backend, execute=execute, window=window)
    warnings.extend(str(item) for item in focus.get("warnings") or [])
    window.update(focus)

    deadline = monotonic_func() + max(0.0, float(args.timeout_seconds))
    poll_seconds = max(0.05, float(args.poll_interval_ms) / 1000.0)
    snapshot_payload: dict[str, Any] | None = None
    snapshot_error: str | None = None
    startup_stage = "waiting_for_snapshot"
    clicks_remaining = max(0, int(args.max_startup_clicks))
    template_dir = Path(str(args.template_dir))
    template_status = bootstrap_vision.template_status(template_dir, confidence=float(args.template_confidence))
    last_selected_candidate: StartupButtonCandidate | None = None
    candidates: list[StartupButtonCandidate] = []

    while True:
        refreshed_window = (
            window_preparer(filters, window_prepare_options(args, execute=execute))
            if placement_requested
            else window_finder(filters)
        )
        if refreshed_window.get("matchedWindowTitle"):
            previous_title = window.get("matchedWindowTitle")
            previous_bounds = window.get("windowBounds")
            previous_focus = {key: window.get(key) for key in ("focused", "focusMethod", "foregroundTitle") if key in window}
            window.update(refreshed_window)
            if (
                previous_focus
                and previous_title == refreshed_window.get("matchedWindowTitle")
                and previous_bounds == refreshed_window.get("windowBounds")
                and not refreshed_window.get("focused")
            ):
                window.update(previous_focus)
            warnings = [warning for warning in warnings if warning != "RuneLite/Jagex window not found"]
            if previous_title != refreshed_window.get("matchedWindowTitle") or previous_bounds != refreshed_window.get("windowBounds"):
                add_stage(stages, "waiting_for_window", reason=str(refreshed_window.get("matchedWindowTitle")))
        elif not window.get("matchedWindowTitle"):
            warnings.extend(str(item) for item in refreshed_window.get("warnings") or [])
        try:
            snapshot_payload = fetch_snapshot_func(args.snapshot_url, timeout=3.0)
            snapshot_error = None
        except Exception as error:  # noqa: BLE001
            snapshot_payload = None
            snapshot_error = f"{type(error).__name__}: {error}"
        summary = snapshot_summary(snapshot_payload, reachable=snapshot_payload is not None, error=snapshot_error)
        candidates, candidate_warnings = button_candidates(
            snapshot_payload,
            window,
            save_debug_screenshot=bool(args.save_debug_screenshot),
            template_dir=template_dir,
            template_confidence=float(args.template_confidence),
            vision_candidate_func=vision_candidate_func,
            disconnected_candidate_func=disconnected_candidate_func,
            click_here_candidate_func=click_here_candidate_func,
            final_play_pending=final_play_click_pending(clicked),
            clicked_names=[str(item.get("name") or "") for item in clicked],
        )
        warnings.extend(str(item) for item in candidate_warnings)
        summary = apply_visual_loaded_scene_veto(summary, candidates, warnings)
        if summary["loggedIn"]:
            if candidates:
                startup_stage = "waiting_for_click_here_to_play"
                add_stage(stages, startup_stage, status="WARN", reason="visual bootstrap surface still visible; loaded scene not accepted")
            elif bootstrap_goal_reached(summary, verify_loaded_scene=verify_loaded_scene) and not final_play_click_pending(clicked) and not summary.get("finalPlayPanelPending"):
                startup_stage = "loaded_scene" if verify_loaded_scene else "logged_in"
                add_stage(stages, startup_stage, reason="loaded scene verified" if verify_loaded_scene else "snapshot reports LOGGED_IN")
                break
            else:
                startup_stage = "waiting_for_loaded_scene" if verify_loaded_scene and not summary.get("finalPlayPanelPending") else "waiting_for_click_here_to_play"
                add_stage(
                    stages,
                    startup_stage,
                    status="WARN",
                    reason="snapshot reports LOGGED_IN but loaded-scene proof is not available" if startup_stage == "waiting_for_loaded_scene" else "snapshot reports LOGGED_IN; final play panel still pending",
                )
        if snapshot_payload is not None and user_login_required(snapshot_payload, window):
            startup_stage = "blocked_user_login_required"
            login_recovery_mode = "manual_required"
            add_stage(stages, startup_stage, status="FAIL", reason="credential/account prompt suspected")
            failures.append("user login/account confirmation required")
            break
        if (
            not summary.get("loggedIn")
            and window_looks_like_jagex_launcher(window)
            and not launcher_automation_allowed
        ):
            startup_stage = "blocked_user_login_required"
            login_recovery_mode = "manual_required"
            reason = "Jagex Launcher automation disabled by default; manual login required"
            add_stage(stages, "blocked_jagex_launcher_automation", status="FAIL", reason=reason)
            failures.append("manual_login_required")
            warnings.append(reason)
            break
        if not execute:
            if args.launch_runelite and snapshot_payload is None and not candidates and monotonic_func() < deadline:
                add_stage(stages, "waiting_for_window", status="WARN", reason="dry-run waiting for launched client window or snapshot")
                sleep_func(poll_seconds)
                continue
            startup_stage = "waiting_for_snapshot" if snapshot_payload is None else "waiting_for_logged_in"
            add_stage(stages, startup_stage, status="WARN", reason="dry-run; startup candidates not clicked")
            break
        if not candidates:
            startup_stage = "waiting_for_snapshot" if snapshot_payload is None else "waiting_for_logged_in"
            add_stage(stages, startup_stage, status="WARN", reason="no safe startup button candidates")
            if summary.get("staleLoggedInNoScene") or (verify_loaded_scene and summary.get("loggedIn") and not summary.get("finalPlayPanelPending")):
                startup_stage = "blocked_bootstrap_surface_unrecognized"
                add_stage(stages, startup_stage, status="FAIL", reason="no recognized safe login/disconnect/play surface")
                failures.append("bootstrap_safe_surface_not_recognized")
                break
            if monotonic_func() >= deadline:
                startup_stage = "failed_timeout"
                failures.append("timeout waiting for safe startup candidate or LOGGED_IN")
                break
            sleep_func(poll_seconds)
            continue
        if len(clicked) >= clicks_remaining:
            startup_stage = "blocked_user_login_required"
            login_recovery_mode = "manual_required"
            warning = "max startup clicks reached"
            warnings.append(warning)
            add_stage(stages, startup_stage, status="WARN", reason=warning)
            break
        launcher_clicked = any(item.get("name") in {"play_now", "continue"} for item in clicked)
        candidate = choose_candidate(
            candidates,
            snapshot_reachable=snapshot_has_game_context(snapshot_payload),
            window=window,
            launcher_clicked=launcher_clicked,
            prefer_saved_account_play_now=bool(getattr(args, "prefer_saved_account_play_now", False)),
        )
        repeated_candidate_clicks = sum(
            1
            for item in clicked
            if str(item.get("name") or "") == candidate.name
            and str(item.get("source") or "") == candidate.source
        )
        if repeated_candidate_clicks >= MAX_REPEAT_VISIBLE_BUTTON_CLICKS:
            startup_stage = "visible_button_no_transition"
            warning = f"{candidate.name} remained visible after {repeated_candidate_clicks} safe click attempts"
            warnings.append(warning)
            failures.append("visible_button_no_transition")
            add_stage(stages, startup_stage, status="FAIL", reason=warning)
            break
        last_selected_candidate = candidate
        allowed_titles = ["RuneLite"] if window_looks_like_runelite(window) else ["Jagex Launcher"] if launcher_automation_allowed else None
        foreground_titles_for_movement = allowed_titles
        pre_click_focus: dict[str, Any] | None = None
        if getattr(backend, "name", "") == "arduino" and allowed_titles:
            click_focus = focus_window(backend, execute=execute, window=window)
            pre_click_focus = dict(click_focus)
            window.update(click_focus)
            warnings.extend(str(item) for item in click_focus.get("warnings") or [])
            target_window = window_title_at_point_func(candidate.screen_point)
            pre_click_focus["targetWindowAtPoint"] = target_window
            target_window_matches = point_window_matches_allowed_title(target_window, allowed_titles)
            add_stage(
                stages,
                "focus_before_bootstrap_click",
                status="PASS" if click_focus.get("focused") else "WARN" if target_window_matches else "FAIL",
                reason=(
                    str(click_focus.get("foregroundTitle") or click_focus.get("focusMethod") or "foreground unknown")
                    + (f"; targetWindow={target_window.get('title')}" if isinstance(target_window, dict) else "")
                ),
            )
            if not click_focus.get("focused") and target_window_matches:
                foreground_titles_for_movement = []
                warnings.append(
                    "window foreground focus not confirmed; target point is on the allowed RuneLite window, proceeding with point-window proof"
                )
            elif not click_focus.get("focused"):
                startup_stage = "blocked_window_focus_required"
                failures.append("foreground_window_not_allowed")
                break
        add_stage(stages, f"click_{candidate.name}_candidate", reason=candidate.reason)
        click_result = click_candidate(
            candidate,
            backend=backend,
            movement_profile=args.movement_profile,
            input_profile=args.input_profile,
            live_input_backend_required=getattr(backend, "name", "") == "arduino",
            seed=args.seed,
            allowed_region=allowed_region_for_startup_candidate(
                candidate,
                snapshot_payload=snapshot_payload,
                window=window,
                launcher_automation_allowed=launcher_automation_allowed,
            ),
            allowed_foreground_titles=foreground_titles_for_movement,
        )
        if pre_click_focus is not None:
            click_result["preClickFocus"] = pre_click_focus
        clicked_payload = candidate.to_dict()
        clicked_payload["clickResult"] = click_result.get("status")
        clicked_payload["clickDetails"] = click_result
        clicked.append(clicked_payload)
        if click_result.get("status") != "PASS":
            warnings.append(str(click_result.get("warning") or "startup candidate click failed"))
            if click_result.get("retryRequiresScreenRecheck") and len(clicked) < clicks_remaining and monotonic_func() < deadline:
                add_stage(
                    stages,
                    "serial_timeout_screen_recheck",
                    status="WARN",
                    reason=str(click_result.get("timeoutClassification") or "serial timeout; rechecking screen before retry"),
                )
                sleep_func(poll_seconds)
                try:
                    snapshot_payload = fetch_snapshot_func(args.snapshot_url, timeout=3.0)
                    summary = snapshot_summary(snapshot_payload, reachable=True)
                    candidates, candidate_warnings = button_candidates(
                        snapshot_payload,
                        window,
                        save_debug_screenshot=bool(args.save_debug_screenshot),
                        template_dir=template_dir,
                        template_confidence=float(args.template_confidence),
                        vision_candidate_func=vision_candidate_func,
                        disconnected_candidate_func=disconnected_candidate_func,
                        click_here_candidate_func=click_here_candidate_func,
                        final_play_pending=final_play_click_pending(clicked),
                        clicked_names=[str(item.get("name") or "") for item in clicked],
                    )
                    warnings.extend(str(item) for item in candidate_warnings)
                    summary = apply_visual_loaded_scene_veto(summary, candidates, warnings)
                    if bootstrap_goal_reached(summary, verify_loaded_scene=verify_loaded_scene) and not visual_bootstrap_surface_candidates(candidates):
                        startup_stage = "loaded_scene" if verify_loaded_scene else "logged_in"
                        add_stage(stages, startup_stage, reason="loaded scene verified after serial timeout screen recheck")
                        break
                    if candidates:
                        add_stage(
                            stages,
                            "serial_timeout_retry_safe",
                            status="WARN",
                            reason="safe bootstrap surface still visible after serial timeout; retrying bounded click",
                        )
                        continue
                    add_stage(stages, "serial_timeout_retry_blocked", status="FAIL", reason="screen recheck found no safe retry surface")
                except Exception as error:  # noqa: BLE001
                    snapshot_error = f"{type(error).__name__}: {error}"
                    add_stage(stages, "serial_timeout_retry_blocked", status="FAIL", reason=snapshot_error)
            startup_stage = "failed_timeout"
            failures.append("startup click failed")
            if "Jagex" in window_title_text(window):
                login_recovery_mode = "manual_required"
            break
        transition_wait_seconds = max(poll_seconds, float(getattr(args, "post_play_wait_ms", 8000)) / 1000.0)
        sleep_func(transition_wait_seconds if candidate.name in {"play_now", "continue"} else poll_seconds)
        try:
            snapshot_payload = fetch_snapshot_func(args.snapshot_url, timeout=3.0)
            summary = snapshot_summary(snapshot_payload, reachable=True)
            candidates, candidate_warnings = button_candidates(
                snapshot_payload,
                window,
                save_debug_screenshot=bool(args.save_debug_screenshot),
                template_dir=template_dir,
                template_confidence=float(args.template_confidence),
                vision_candidate_func=vision_candidate_func,
                disconnected_candidate_func=disconnected_candidate_func,
                click_here_candidate_func=click_here_candidate_func,
                final_play_pending=final_play_click_pending(clicked),
                clicked_names=[str(item.get("name") or "") for item in clicked],
            )
            warnings.extend(str(item) for item in candidate_warnings)
            summary = apply_visual_loaded_scene_veto(summary, candidates, warnings)
            if summary["loggedIn"]:
                if candidates:
                    startup_stage = "waiting_for_click_here_to_play"
                    add_stage(stages, startup_stage, status="WARN", reason="visual bootstrap surface still visible after click")
                elif bootstrap_goal_reached(summary, verify_loaded_scene=verify_loaded_scene) and not final_play_click_pending(clicked) and not summary.get("finalPlayPanelPending"):
                    startup_stage = "loaded_scene" if verify_loaded_scene else "logged_in"
                    add_stage(stages, startup_stage, reason="loaded scene verified after click" if verify_loaded_scene else "snapshot reports LOGGED_IN after click")
                    break
                else:
                    startup_stage = "waiting_for_loaded_scene" if verify_loaded_scene and not summary.get("finalPlayPanelPending") else "waiting_for_click_here_to_play"
                    add_stage(
                        stages,
                        startup_stage,
                        status="WARN",
                        reason="snapshot reports LOGGED_IN; waiting for loaded scene proof" if startup_stage == "waiting_for_loaded_scene" else "snapshot reports LOGGED_IN; waiting for final play panel",
                    )
        except Exception as error:  # noqa: BLE001
            snapshot_error = f"{type(error).__name__}: {error}"
        if monotonic_func() >= deadline:
            startup_stage = "failed_timeout"
            failures.append("timeout waiting for LOGGED_IN")
            break

    snapshot_out = snapshot_summary(snapshot_payload, reachable=snapshot_payload is not None, error=snapshot_error)
    if snapshot_payload is not None and (
        clicked or (snapshot_out.get("loadedSceneVerified") and not visual_bootstrap_surface_candidates(candidates))
    ):
        try:
            final_candidates, final_candidate_warnings = button_candidates(
                snapshot_payload,
                window,
                save_debug_screenshot=bool(args.save_debug_screenshot),
                template_dir=template_dir,
                template_confidence=float(args.template_confidence),
                vision_candidate_func=vision_candidate_func,
                disconnected_candidate_func=disconnected_candidate_func,
                click_here_candidate_func=click_here_candidate_func,
                final_play_pending=final_play_click_pending(clicked),
                clicked_names=[str(item.get("name") or "") for item in clicked],
            )
            warnings.extend(str(item) for item in final_candidate_warnings)
            candidates = final_candidates
        except Exception as error:  # noqa: BLE001
            warnings.append(f"final bootstrap visual classifier failed: {type(error).__name__}: {error}")
    snapshot_out = apply_visual_loaded_scene_veto(snapshot_out, candidates, warnings)
    daemon_summary = {"reachable": False, "startedOrReused": "not_requested", "pid": None}
    live_qa_summary = {"status": "unknown", "ran": False, "cycleStage": "unknown", "warnings": []}
    bootstrap_ready = bootstrap_goal_reached(snapshot_out, verify_loaded_scene=verify_loaded_scene)
    if args.start_daemon and bootstrap_ready:
        add_stage(stages, "start_daemon", reason="start daemon requested")
        if daemon_reachable_func(args.daemon_url, timeout=1.0):
            daemon_summary = {"reachable": True, "startedOrReused": "reused", "pid": None}
        else:
            daemon_start = start_daemon_func(execute=execute)
            daemon_summary = {
                "reachable": bool(daemon_start.get("started")),
                "startedOrReused": "started" if daemon_start.get("started") else "dry_run" if not execute else "failed",
                "pid": daemon_start.get("pid"),
                "reason": daemon_start.get("reason"),
            }
            if execute and daemon_start.get("started"):
                sleep_func(2.0)
    elif args.start_daemon and not bootstrap_ready:
        daemon_summary["startedOrReused"] = "blocked_until_logged_in"

    if args.run_live_qa and bootstrap_ready:
        add_stage(stages, "run_live_qa", reason="live QA requested")
        live_qa_summary = live_qa_func(args.daemon_url, execute=execute)
    elif args.run_live_qa:
        live_qa_summary = {"status": "unknown", "ran": False, "reason": "blocked_until_logged_in", "cycleStage": "unknown", "warnings": []}

    status = "PASS"
    if failures:
        status = "FAIL"
    elif not bootstrap_ready or warnings or not execute:
        status = "WARN"
    if live_qa_summary.get("status") == "FAIL":
        status = "FAIL"
    elif live_qa_summary.get("status") == "WARN" and status == "PASS":
        status = "WARN"

    screenshot_path = None
    if bool(args.save_debug_screenshot):
        debug_path = template_dir / "bootstrap_debug_screenshot.png"
        if debug_path.exists():
            screenshot_path = str(debug_path)
    bootstrap_state = bootstrap_state_from_signals(
        summary=snapshot_out,
        window=window,
        candidates=candidates,
        selected_candidate=last_selected_candidate,
        screenshot_path=screenshot_path,
        canvas_bounds=bootstrap_surface_bounds(snapshot_payload, window),
        blocker=failures[0] if failures else None,
        verification_result={
            "status": "PASS" if bootstrap_ready else "WARN",
            "loadedSceneVerified": snapshot_out.get("loadedSceneVerified"),
            "loggedIn": snapshot_out.get("loggedIn"),
            "startupStage": startup_stage,
        },
    )

    return {
        "schema": SCHEMA,
        "status": status,
        "bootstrapState": bootstrap_state,
        "loadedSceneVerified": bool(snapshot_out.get("loadedSceneVerified")),
        "loginScreenDetected": str(snapshot_out.get("gameState") or "").upper() == "LOGIN_SCREEN",
        "credentialRequired": login_recovery_mode == "manual_required" and bool(failures),
        "disconnectedDialogDetected": bootstrap_state.get("state") == "disconnected_dialog",
        "savedAccountPlayNowDetected": bootstrap_state.get("state") == "saved_account_play_now",
        "clickHereToPlayDetected": bootstrap_state.get("state") == "click_here_to_play",
        "staleLoggedInNoScene": bool(snapshot_out.get("staleLoggedInNoScene")),
        "bootstrapRecommended": not bootstrap_ready,
        "bootstrapSafeActionAvailable": bool(candidates and bootstrap_state.get("state") in {"disconnected_dialog", "saved_account_play_now", "click_here_to_play"}),
        "bootstrapLastAction": clicked[-1] if clicked else None,
        "bootstrapLastVerification": bootstrap_state.get("verificationResult"),
        "stages": stages,
        "startupStage": startup_stage,
        "launch": launch_summary,
        "window": window,
        "snapshot": snapshot_out,
        "templateStatus": template_status,
        "buttonCandidates": [candidate.to_dict() for candidate in candidates],
        "clickedCandidates": clicked,
        "daemon": daemon_summary,
        "liveQa": live_qa_summary,
        "launcherAutomationAllowed": launcher_automation_allowed,
        "launcherAutomationBlockedReason": launcher_automation_blocked_reason,
        "loginRecoveryMode": login_recovery_mode,
        "warnings": list(dict.fromkeys(warnings)),
        "failures": failures,
    }


def format_human(payload: dict[str, Any]) -> str:
    launch = dict_value(payload.get("launch"))
    window = dict_value(payload.get("window"))
    snapshot = dict_value(payload.get("snapshot"))
    daemon = dict_value(payload.get("daemon"))
    live_qa = dict_value(payload.get("liveQa"))
    template_status = dict_value(payload.get("templateStatus"))
    candidates = payload.get("buttonCandidates") if isinstance(payload.get("buttonCandidates"), list) else []
    clicked = payload.get("clickedCandidates") if isinstance(payload.get("clickedCandidates"), list) else []
    placement = dict_value(window.get("placement"))
    lines = [
        f"RUNELITE BOOTSTRAP - {payload.get('status') or 'UNKNOWN'}",
        "",
        "Launch:",
        f"  launched: {launch.get('runeliteLaunched')}",
        f"  process/window: {launch.get('pid') or 'none'} / {window.get('matchedWindowTitle') or 'none'}",
        "",
        "Window:",
        f"  matched title: {window.get('matchedWindowTitle') or 'none'}",
        f"  original bounds: {window.get('originalWindowBounds') or window.get('windowBounds') or 'unknown'}",
        f"  final bounds: {window.get('finalWindowBounds') or window.get('windowBounds') or 'unknown'}",
        f"  monitor target: {placement.get('monitorTarget') if placement else 'none'}",
        f"  placement result: {placement.get('status') if placement else 'unknown'} ({placement.get('method') if placement else 'unknown'})",
        f"  focus: {window.get('focused')} ({window.get('focusMethod') or 'unknown'})",
        "",
        "Startup:",
        f"  snapshot reachable: {snapshot.get('snapshotReachable')}",
        f"  gameState: {snapshot.get('gameState') or 'unknown'}",
        f"  candidates: {len(candidates)}",
        f"  startup stage: {payload.get('startupStage') or 'unknown'}",
        f"  clicks attempted: {len(clicked)}",
        f"  last clicked: {(clicked[-1].get('name') if clicked else 'none')}",
        f"  logged in: {snapshot.get('loggedIn')}",
        f"  template dir: {template_status.get('templateDir') or 'unknown'}",
        f"  templates found: {', '.join(template_status.get('found') or []) if template_status else 'unknown'}",
        f"  templates missing: {', '.join(template_status.get('missing') or []) if template_status else 'unknown'}",
        f"  login recovery mode: {payload.get('loginRecoveryMode') or 'unknown'}",
        f"  Jagex launcher automation allowed: {payload.get('launcherAutomationAllowed')}",
    ]
    if candidates:
        lines.extend(["", "Candidates:"])
        for candidate in candidates[:5]:
            lines.append(
                f"  {candidate.get('name')} {candidate.get('candidateMethod') or candidate.get('source')} screen={candidate.get('screenPoint')} confidence={candidate.get('confidence')}"
            )
    lines.extend(
        [
            "",
            "Daemon:",
            f"  reachable: {daemon.get('reachable')}",
            f"  started/reused: {daemon.get('startedOrReused')}",
            "",
            "Live QA:",
            f"  status: {live_qa.get('status')}",
            f"  cycle stage: {live_qa.get('cycleStage')}",
            f"  warnings: {len(live_qa.get('warnings') or [])}",
            "",
            "Warnings:",
        ]
    )
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    failures = payload.get("failures") if isinstance(payload.get("failures"), list) else []
    if failures:
        lines.extend(["", "Failures:"])
        lines.extend(f"  FAIL: {failure}" for failure in failures)
    return "\n".join(lines).rstrip() + "\n"


def build_startup_backend(args: argparse.Namespace) -> Any:
    if args.startup_backend == "pyautogui":
        return PyAutoGuiBackend(focus_runelite=True, window_title_filter=args.window_title_filter)
    return ArduinoHIDBackend(
        port=args.arduino_port,
        baud=args.arduino_baud,
        handshake_timeout_ms=args.arduino_handshake_timeout_ms,
        command_timeout_ms=args.arduino_command_timeout_ms,
        session_token=args.arduino_session_token,
        serial_owner=f"runelite_bootstrap:{os.getpid()}",
    )


def arm_startup_backend(args: argparse.Namespace, backend: Any) -> dict[str, Any]:
    status = {
        "backend": getattr(backend, "name", backend.__class__.__name__),
        "armed": False,
        "status": "SKIPPED",
        "reason": "not_arduino_or_not_execute",
    }
    if not args.execute or getattr(backend, "name", "") != "arduino":
        return status
    try:
        arm = getattr(backend, "arm", None)
        if not callable(arm):
            status.update({"status": "FAIL", "reason": "arduino_backend_missing_arm"})
            return status
        token = args.arduino_session_token if args.arduino_session_token != "auto" else None
        status["backendStatus"] = arm(token)
        status.update({"armed": True, "status": "PASS", "reason": "armed_for_startup_clicks"})
    except Exception as error:  # noqa: BLE001
        status.update({"status": "FAIL", "reason": f"{type(error).__name__}: {error}"})
    return status


def cleanup_startup_backend(backend: Any) -> dict[str, Any]:
    result = {
        "backend": getattr(backend, "name", backend.__class__.__name__),
        "stopAll": "not_applicable",
        "disarm": "not_applicable",
        "status": "PASS",
        "warnings": [],
    }
    if getattr(backend, "name", "") != "arduino":
        return result
    stop_all = getattr(backend, "stop_all", None)
    disarm = getattr(backend, "disarm", None)
    if callable(stop_all):
        try:
            stop_all()
            result["stopAll"] = "PASS"
        except Exception as error:  # noqa: BLE001
            result["status"] = "WARN"
            result["stopAll"] = "WARN"
            result["warnings"].append(f"stop_all failed: {type(error).__name__}: {error}")
    if callable(disarm):
        try:
            disarm()
            result["disarm"] = "PASS"
        except Exception as error:  # noqa: BLE001
            result["status"] = "WARN"
            result["disarm"] = "WARN"
            result["warnings"].append(f"disarm failed: {type(error).__name__}: {error}")
    close = getattr(backend, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001
            pass
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if getattr(args, "ensure_loaded_scene", False):
        import liveness_recovery_core

        payload = liveness_recovery_core.ensure_loaded_scene(
            daemon_url=args.daemon_url,
            snapshot_url=args.snapshot_url,
            backend=args.startup_backend,
            arduino_port=args.arduino_port,
            max_total_ms=int(max(1.0, float(args.timeout_seconds)) * 1000.0),
            max_attempts_per_state=max(1, int(args.max_startup_clicks or 1)),
            allow_jagex_launcher=bool(args.allow_jagex_launcher_automation),
            allow_credentials=False,
        )
        print(json.dumps(payload, indent=2, sort_keys=False) if args.json else liveness_recovery_core.format_compact_result(payload), end="")
        return 0 if payload.get("status") in {"loaded_scene_ready", "recovered_loaded_scene"} else 1
    backend = build_startup_backend(args)
    startup_input = arm_startup_backend(args, backend)
    if startup_input.get("status") == "FAIL":
        payload = {
            "schema": SCHEMA,
            "status": "FAIL",
            "startupInput": startup_input,
            "startupBackend": getattr(backend, "name", backend.__class__.__name__),
            "warnings": [str(startup_input.get("reason") or "startup backend failed")],
            "failures": ["startup input backend failed"],
        }
        cleanup = cleanup_startup_backend(backend)
        payload["startupInputCleanup"] = cleanup
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=False), end="")
        else:
            print(f"RuneLite bootstrap failed: {startup_input.get('reason') or 'startup input backend failed'}\n", end="")
        return 1
    try:
        payload = run_bootstrap(args, backend=backend)
    finally:
        cleanup = cleanup_startup_backend(backend)
    payload["startupBackend"] = getattr(backend, "name", backend.__class__.__name__)
    payload["startupInput"] = startup_input
    payload["startupInputCleanup"] = cleanup
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_human(payload), end="")
    return 0 if payload.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

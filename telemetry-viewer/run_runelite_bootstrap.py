from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import bootstrap_vision
import bootstrap_window
from input_control.backend_pyautogui import PyAutoGuiBackend
from input_control.input_geometry import normalize_input_geometry, resolve_screen_click_point
from input_control.mouse_movement import MousePoint, MouseTarget, plan_mouse_movement


SCHEMA = "runelite_bootstrap.v1"
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
    ("click_here_to_play", 0.50, 0.57, 0.78, "center login/play button candidate"),
    ("play_now", 0.50, 0.64, 0.68, "lower centered launcher play button candidate"),
    ("continue", 0.50, 0.76, 0.58, "continue button candidate"),
]

WINDOW_PERCENT_ZONES = [
    ("click_here_to_play", 0.53, 0.626, 0.56, "window welcome play panel candidate"),
    ("play_now", 0.50, 0.46, 0.52, "window center play button candidate"),
    ("continue", 0.50, 0.72, 0.44, "window lower continue button candidate"),
]

WINDOW_TITLE_HINTS = ["RuneLite", "RuneLite Launcher", "Jagex Launcher", "Java", "Old School RuneScape"]
CREDENTIAL_MARKERS = ("PASSWORD", "CREDENTIAL", "AUTHENTICATOR", "ACCOUNT_CONFIRM", "ACCOUNT_MANAGEMENT", "MFA", "TWO_FACTOR")


@dataclass(frozen=True)
class StartupButtonCandidate:
    name: str
    source: str
    screen_point: dict[str, int] | None
    canvas_point: dict[str, int] | None
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "screenPoint": self.screen_point,
            "canvasPoint": self.canvas_point,
            "confidence": self.confidence,
            "reason": self.reason,
            "candidateMethod": candidate_method(self.source),
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
    parser.add_argument("--movement-profile", choices=["linear_debug", "wind_mouse", "instant_test"], default="linear_debug")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--poll-interval-ms", type=int, default=1000)
    parser.add_argument("--max-startup-clicks", type=int, default=3)
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
    parser.add_argument("--template-confidence", type=float, default=0.85)
    parser.add_argument("--template-dir", default=str(BOOTSTRAP_TEMPLATE_DIR))
    parser.add_argument("--start-daemon", action="store_true")
    parser.add_argument("--run-live-qa", action="store_true")
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
        "needs": ["baseline", "client_tick_hot", "writer_health"],
        "maxAgeTicks": 5,
        "responseMode": "compact",
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


def snapshot_top_menu(snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    hot = snapshot_client_tick_hot(snapshot_payload)
    return dict_value(hot.get("hoverMenu") or hot.get("postMenuSort"))


def final_play_panel_pending(snapshot_payload: dict[str, Any] | None) -> bool:
    payload = snapshot_payload if isinstance(snapshot_payload, dict) else {}
    baseline = snapshot_baseline(payload)
    game_state = first_present(baseline.get("gameState"), payload.get("gameState"))
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
    game_state = first_present(baseline.get("gameState"), payload.get("gameState"))
    menu = snapshot_top_menu(payload)
    return {
        "snapshotReachable": reachable,
        "snapshotStatus": payload.get("status") if reachable else "FAIL",
        "loggedIn": game_state == "LOGGED_IN",
        "gameState": game_state,
        "latestTick": payload.get("latestTick"),
        "inputGeometryAvailable": bool(dict_value(baseline.get("inputGeometry")).get("geometryAvailable")),
        "topOption": menu.get("topOption"),
        "topTarget": menu.get("topTarget"),
        "topType": menu.get("topType"),
        "finalPlayPanelPending": final_play_panel_pending(payload),
        "error": error,
    }


def user_login_required(snapshot_payload: dict[str, Any] | None, window: dict[str, Any] | None = None) -> bool:
    payload = snapshot_payload if isinstance(snapshot_payload, dict) else {}
    baseline = snapshot_baseline(payload)
    game_state = str(first_present(baseline.get("gameState"), payload.get("gameState"), "")).upper()
    title = str(dict_value(window).get("matchedWindowTitle") or "").upper()
    if any(marker in game_state for marker in CREDENTIAL_MARKERS):
        return True
    if "PASSWORD" in title or "AUTHENTICATOR" in title or "ACCOUNT SETTINGS" in title:
        return True
    return False


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
            return {
                "matchedWindowTitle": title,
                "windowBounds": {
                    "x": int(getattr(window, "left", 0)),
                    "y": int(getattr(window, "top", 0)),
                    "width": int(getattr(window, "width", 0)),
                    "height": int(getattr(window, "height", 0)),
                },
                "focused": False,
                "focusMethod": "pygetwindow_match",
                "warnings": [],
            }
    return {
        "matchedWindowTitle": None,
        "windowBounds": None,
        "focused": False,
        "focusMethod": "not_found",
        "warnings": ["RuneLite/Jagex window not found"],
    }


def focus_window(backend: Any, *, execute: bool) -> dict[str, Any]:
    if not execute:
        return {"focused": False, "focusMethod": "dry_run", "warnings": []}
    try:
        focused = bool(backend.focus_window()) if hasattr(backend, "focus_window") else False
        return {"focused": focused, "focusMethod": "input_backend", "warnings": [] if focused else ["window focus not confirmed"]}
    except Exception as error:  # noqa: BLE001
        return {"focused": False, "focusMethod": "input_backend", "warnings": [f"window focus failed: {type(error).__name__}: {error}"]}


def normalize_snapshot_geometry(snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    baseline = snapshot_baseline(snapshot_payload)
    geometry = dict_value(baseline.get("inputGeometry") or snapshot_payload.get("inputGeometry"))
    return normalize_input_geometry(geometry, source_tick=snapshot_payload.get("latestTick"))


def candidates_from_geometry(snapshot_payload: dict[str, Any]) -> list[StartupButtonCandidate]:
    geometry = normalize_snapshot_geometry(snapshot_payload)
    if not geometry.get("inputGeometryAvailable"):
        return []
    source_size = dict_value(geometry.get("sourceCanvasSize") or geometry.get("canvasSize"))
    source_width = int(source_size.get("width") or 765)
    source_height = int(source_size.get("height") or 503)
    candidates: list[StartupButtonCandidate] = []
    for name, x_pct, y_pct, confidence, reason in BUTTON_ZONES:
        canvas_point = {"x": int(round(source_width * x_pct)), "y": int(round(source_height * y_pct))}
        resolution = resolve_screen_click_point(canvas_point, click_point_space="canvas", input_geometry=geometry)
        screen_point = resolution.get("screenClickPoint") if isinstance(resolution, dict) and isinstance(resolution.get("screenClickPoint"), dict) else None
        candidates.append(
            StartupButtonCandidate(
                name=name,
                source="canvas_percent",
                screen_point=screen_point,
                canvas_point=canvas_point,
                confidence=confidence,
                reason=reason,
            )
        )
    return candidates


def candidates_from_window(window: dict[str, Any] | None) -> list[StartupButtonCandidate]:
    bounds = dict_value(dict_value(window).get("windowBounds"))
    if not bounds:
        return []
    x = int(bounds.get("x") or 0)
    y = int(bounds.get("y") or 0)
    width = max(1, int(bounds.get("width") or 1))
    height = max(1, int(bounds.get("height") or 1))
    if width < 500 or height < 400:
        return []
    return [
        StartupButtonCandidate(
            name=name,
            source="calibrated_screen",
            screen_point={"x": int(round(x + width * x_pct)), "y": int(round(y + height * y_pct))},
            canvas_point=None,
            confidence=confidence,
            reason=reason,
        )
        for name, x_pct, y_pct, confidence, reason in WINDOW_PERCENT_ZONES
    ]


def candidate_method(source: str) -> str:
    if source == "template":
        return "template"
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


def button_candidates(
    snapshot_payload: dict[str, Any] | None,
    window: dict[str, Any] | None,
    *,
    save_debug_screenshot: bool = False,
    template_dir: Path = BOOTSTRAP_TEMPLATE_DIR,
    template_confidence: float = 0.85,
    vision_candidate_func: Callable[..., tuple[list[Any], list[str]]] = bootstrap_vision.template_candidates,
) -> tuple[list[StartupButtonCandidate], list[str]]:
    vision_candidates, vision_warnings = vision_candidate_func(
        template_dir,
        save_debug_screenshot=save_debug_screenshot,
        confidence=template_confidence,
    )
    if vision_candidates:
        return [candidate_from_vision(candidate) for candidate in vision_candidates], vision_warnings
    if isinstance(snapshot_payload, dict):
        candidates = candidates_from_geometry(snapshot_payload)
        if candidates:
            return candidates, vision_warnings
    return candidates_from_window(window), vision_warnings


def window_title_text(window: dict[str, Any] | None) -> str:
    return str(dict_value(window).get("matchedWindowTitle") or "")


def window_looks_like_launcher(window: dict[str, Any] | None) -> bool:
    title = window_title_text(window).lower()
    return "launcher" in title or "jagex" in title


def preferred_candidate_names(snapshot_reachable: bool, window: dict[str, Any] | None = None, launcher_clicked: bool = False) -> list[str]:
    if snapshot_reachable or launcher_clicked:
        return ["click_here_to_play", "continue", "play_now"]
    return ["play_now", "continue", "click_here_to_play"]


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
) -> StartupButtonCandidate:
    preference = preferred_candidate_names(snapshot_reachable, window, launcher_clicked)
    rank = {name: index for index, name in enumerate(preference)}
    return sorted(candidates, key=lambda item: (rank.get(item.name, 100), -float(item.confidence)))[0]


def final_play_click_pending(clicked: list[dict[str, Any]]) -> bool:
    launcher_clicked = any(item.get("name") in {"play_now", "continue"} for item in clicked)
    final_play_clicked = any(item.get("name") == "click_here_to_play" for item in clicked)
    return launcher_clicked and not final_play_clicked


def click_candidate(
    candidate: StartupButtonCandidate,
    *,
    backend: Any,
    movement_profile: str,
    seed: int | None = None,
) -> dict[str, Any]:
    if not candidate.screen_point:
        return {"status": "FAIL", "warning": "candidate has no screen point"}
    start = MousePoint(*backend.current_position())
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
    plan = plan_mouse_movement(start, target, profile)
    if plan.validation_status == "FAIL":
        return {"status": "FAIL", "warning": "; ".join(plan.warnings) or "movement plan invalid"}
    backend.move_and_click(plan, button="left")
    return {"status": "PASS", "movementPlan": plan.to_dict(include_points=False)}


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
    sleep_func: Callable[[float], None] = time.sleep,
    monotonic_func: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    execute = bool(getattr(args, "execute", False))
    stages: list[dict[str, Any]] = []
    warnings: list[str] = []
    failures: list[str] = []
    clicked: list[dict[str, Any]] = []
    candidates: list[StartupButtonCandidate] = []
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
    focus = focus_window(backend, execute=execute)
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

    while True:
        refreshed_window = (
            window_preparer(filters, window_prepare_options(args, execute=execute))
            if placement_requested
            else window_finder(filters)
        )
        if refreshed_window.get("matchedWindowTitle"):
            previous_title = window.get("matchedWindowTitle")
            previous_bounds = window.get("windowBounds")
            window.update(refreshed_window)
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
        if summary["loggedIn"]:
            if not final_play_click_pending(clicked) and not summary.get("finalPlayPanelPending"):
                startup_stage = "logged_in"
                add_stage(stages, "logged_in", reason="snapshot reports LOGGED_IN")
                break
            startup_stage = "waiting_for_click_here_to_play"
            add_stage(
                stages,
                startup_stage,
                status="WARN",
                reason="snapshot reports LOGGED_IN; final play panel still pending",
            )
        if snapshot_payload is not None and user_login_required(snapshot_payload, window):
            startup_stage = "blocked_user_login_required"
            add_stage(stages, startup_stage, status="FAIL", reason="credential/account prompt suspected")
            failures.append("user login/account confirmation required")
            break
        candidates, candidate_warnings = button_candidates(
            snapshot_payload,
            window,
            save_debug_screenshot=bool(args.save_debug_screenshot),
            template_dir=template_dir,
            template_confidence=float(args.template_confidence),
            vision_candidate_func=vision_candidate_func,
        )
        warnings.extend(str(item) for item in candidate_warnings)
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
            if monotonic_func() >= deadline:
                startup_stage = "failed_timeout"
                failures.append("timeout waiting for safe startup candidate or LOGGED_IN")
                break
            sleep_func(poll_seconds)
            continue
        if len(clicked) >= clicks_remaining:
            startup_stage = "blocked_user_login_required"
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
        )
        add_stage(stages, f"click_{candidate.name}_candidate", reason=candidate.reason)
        click_result = click_candidate(candidate, backend=backend, movement_profile=args.movement_profile, seed=args.seed)
        clicked_payload = candidate.to_dict()
        clicked_payload["clickResult"] = click_result.get("status")
        clicked.append(clicked_payload)
        if click_result.get("status") != "PASS":
            warnings.append(str(click_result.get("warning") or "startup candidate click failed"))
            startup_stage = "failed_timeout"
            failures.append("startup click failed")
            break
        transition_wait_seconds = max(poll_seconds, float(getattr(args, "post_play_wait_ms", 8000)) / 1000.0)
        sleep_func(transition_wait_seconds if candidate.name in {"play_now", "continue"} else poll_seconds)
        try:
            snapshot_payload = fetch_snapshot_func(args.snapshot_url, timeout=3.0)
            summary = snapshot_summary(snapshot_payload, reachable=True)
            if summary["loggedIn"]:
                if not final_play_click_pending(clicked) and not summary.get("finalPlayPanelPending"):
                    startup_stage = "logged_in"
                    add_stage(stages, "logged_in", reason="snapshot reports LOGGED_IN after click")
                    break
                startup_stage = "waiting_for_click_here_to_play"
                add_stage(
                    stages,
                    startup_stage,
                    status="WARN",
                    reason="snapshot reports LOGGED_IN; waiting for final play panel",
                )
        except Exception as error:  # noqa: BLE001
            snapshot_error = f"{type(error).__name__}: {error}"
        if monotonic_func() >= deadline:
            startup_stage = "failed_timeout"
            failures.append("timeout waiting for LOGGED_IN")
            break

    snapshot_out = snapshot_summary(snapshot_payload, reachable=snapshot_payload is not None, error=snapshot_error)
    daemon_summary = {"reachable": False, "startedOrReused": "not_requested", "pid": None}
    live_qa_summary = {"status": "unknown", "ran": False, "cycleStage": "unknown", "warnings": []}
    if args.start_daemon and snapshot_out.get("loggedIn"):
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
    elif args.start_daemon and not snapshot_out.get("loggedIn"):
        daemon_summary["startedOrReused"] = "blocked_until_logged_in"

    if args.run_live_qa and snapshot_out.get("loggedIn"):
        add_stage(stages, "run_live_qa", reason="live QA requested")
        live_qa_summary = live_qa_func(args.daemon_url, execute=execute)
    elif args.run_live_qa:
        live_qa_summary = {"status": "unknown", "ran": False, "reason": "blocked_until_logged_in", "cycleStage": "unknown", "warnings": []}

    status = "PASS"
    if failures:
        status = "FAIL"
    elif not snapshot_out.get("loggedIn") or warnings or not execute:
        status = "WARN"
    if live_qa_summary.get("status") == "FAIL":
        status = "FAIL"
    elif live_qa_summary.get("status") == "WARN" and status == "PASS":
        status = "WARN"

    return {
        "schema": SCHEMA,
        "status": status,
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    backend = PyAutoGuiBackend(focus_runelite=True, window_title_filter=args.window_title_filter)
    payload = run_bootstrap(args, backend=backend)
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_human(payload), end="")
    return 0 if payload.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

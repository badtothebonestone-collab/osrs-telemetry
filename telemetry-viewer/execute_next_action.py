from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Any

from input_control.backend_arduino_hid import DEFAULT_COMMAND_TIMEOUT_MS
from input_control.executor import LOOP_SCHEMA, backend_from_options, execute_action_loop, execute_next_action, run_camera_self_test


def bool_arg(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {value!r}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and optionally execute one input action from daemon context.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--snapshot-url", default="http://127.0.0.1:8893")
    parser.add_argument("--backend", choices=["arduino", "pyautogui", "pydirectinput"], default=None)
    parser.add_argument("--auto-recover-loaded-scene", action="store_true", help="Run ensure_loaded_scene once before failing stale liveness/client-tick checks.")
    parser.add_argument("--liveness-max-total-seconds", type=float, default=120.0, help="Maximum seconds for --auto-recover-loaded-scene.")
    parser.add_argument("--liveness-max-attempts-per-state", type=int, default=2, help="Maximum attempts per known liveness state during auto recovery.")
    parser.add_argument("--allow-jagex-launcher-automation", action="store_true", help="Allow Jagex Launcher automation during liveness recovery; credentials are still never typed.")
    parser.add_argument("--allow-software-input", action="store_true", help="Unsafe/debug override: allow software mouse/keyboard output for live execution.")
    parser.add_argument("--unsafe-allow-pyautogui-live", dest="unsafe_allow_pyautogui_live", action="store_true", help="Alias for --allow-software-input.")
    parser.add_argument("--arduino-port", help="Arduino serial bridge port, for example COM5.")
    parser.add_argument("--arduino-baud", type=int, default=115200)
    parser.add_argument("--arduino-require-monitor", action="store_true")
    parser.add_argument("--arduino-monitor-status-path")
    parser.add_argument("--arduino-monitor-status", dest="arduino_monitor_status_path")
    parser.add_argument("--arduino-monitor-max-age-ms", type=int, default=3000)
    parser.add_argument("--arduino-vid", default="VID_2341")
    parser.add_argument("--arduino-pid", default="PID_8036")
    parser.add_argument("--arduino-handshake-timeout-ms", type=int, default=2000)
    parser.add_argument("--arduino-command-timeout-ms", type=int, default=DEFAULT_COMMAND_TIMEOUT_MS)
    parser.add_argument("--arduino-session-token", default="auto")
    parser.add_argument("--arduino-fail-closed", dest="arduino_fail_closed", action="store_true", default=True)
    parser.add_argument("--no-arduino-fail-closed", dest="arduino_fail_closed", action="store_false")
    parser.add_argument(
        "--arduino-check",
        choices=["ping", "identify", "caps", "status", "stop-all", "disarm", "monitor", "port-health", "tiny-move", "usb-diagnostics"],
        help="Run a bounded Arduino backend check instead of a game action.",
    )
    parser.add_argument("--arduino-stop-all", action="store_true", help="Panic command: send STOP_ALL and exit.")
    parser.add_argument("--arduino-status", action="store_true", help="Query Arduino firmware STATUS and exit.")
    parser.add_argument("--arduino-port-health", action="store_true", help="Run a bounded PING/IDENTIFY/CAPS/STATUS serial health check and exit.")
    parser.add_argument("--arduino-identify", action="store_true", help="Query Arduino firmware IDENTIFY and exit.")
    parser.add_argument("--arduino-caps", action="store_true", help="Query Arduino firmware CAPS and exit.")
    parser.add_argument("--arduino-usb-diagnostics", action="store_true", help="Inspect guest-visible Arduino USB/COM state and print VMware .vmx autoconnect guidance.")
    parser.add_argument("--arduino-bootloader-port", help="Known Leonardo bootloader COM port, if observed during reset/upload.")
    parser.add_argument("--arduino-bootloader-vid", default="VID_2341")
    parser.add_argument("--arduino-bootloader-pid")
    parser.add_argument("--arduino-movement-diagnostics", action="store_true", help="Run a no-click/no-key Arduino MOVE path diagnostic and classify serial/raw-input/cursor movement.")
    parser.add_argument("--arduino-movement-diagnostic-delta", type=int, default=5, help="Relative MOVE delta used by --arduino-movement-diagnostics.")
    parser.add_argument("--arduino-pointer-calibration-test", action="store_true", help="Run a no-click closed-loop Arduino cursor calibration inside a safe allowed window.")
    parser.add_argument("--arduino-move-settle-ms", type=int, default=80, help="Closed-loop MOVE settle window before treating a chunk as delayed.")
    parser.add_argument("--arduino-move-poll-ms", type=int, default=10, help="Cursor/Raw Input poll interval during Arduino closed-loop movement.")
    parser.add_argument("--arduino-move-noeffect-timeout-ms", type=int, default=200, help="Maximum bounded wait before an ACKed MOVE chunk is classified as no-effect.")
    parser.add_argument("--arduino-move-noeffect-retries", type=int, default=2, help="Bounded retry count for ACKed MOVE chunks that produce no cursor feedback.")
    parser.add_argument("--arduino-min-effective-move-px", type=int, default=2, help="Minimum expected cursor movement used when classifying per-chunk Arduino feedback.")
    parser.add_argument("--arduino-retry-scale", type=float, default=1.25, help="Scale factor for safe retry MOVE chunks after no-effect feedback.")
    parser.add_argument("--arduino-move-max-consecutive-noeffect", type=int, default=3, help="Abort threshold for consecutive no-effect Arduino MOVE chunks.")
    parser.add_argument("--allowed-window", choices=["runelite", "calibration"], default="runelite", help="Window used for the Arduino pointer calibration allowed region.")
    parser.add_argument("--no-click", action="store_true", help="Compatibility flag for no-click calibration checks; pointer calibration never clicks.")
    parser.add_argument("--calibration-window-center", dest="calibration_window_center", action="store_true", default=True, help="Place the fallback calibration window near the screen center.")
    parser.add_argument("--calibration-window-near-cursor", dest="calibration_window_center", action="store_false", help="Place the fallback calibration window near the current cursor for diagnostics.")
    parser.add_argument("--calibration-staging-max-distance-px", type=int, default=150, help="Maximum no-click Arduino staging distance into the calibration region.")
    parser.add_argument("--arduino-pointer-calibration-path", help="Path for the persisted Arduino pointer calibration record.")
    parser.add_argument("--arduino-pointer-calibration-max-age-hours", type=float, default=8.0, help="Maximum age for a persisted Arduino pointer calibration record used by live movement.")
    parser.add_argument("--allow-uncalibrated-arduino-movement", action="store_true", help="Explicit override for Arduino RuneLite movement before pointer calibration has been reviewed.")
    parser.add_argument("--input-integrity-self-test", action="store_true", help="Run an Arduino monitor/arming/tiny-pulse self-test without game actions.")
    parser.add_argument("--input-integrity-self-test-no-move", action="store_true", help="Run the Arduino integrity self-test without sending MOVE/CLICK/KEY commands.")
    parser.add_argument("--show-input-integrity-overlay", action="store_true")
    parser.add_argument("--no-overlay", action="store_true", help="Do not show the input-integrity overlay, even if overlay flags are present.")
    parser.add_argument("--overlay-passive", dest="input_integrity_overlay_passive", action="store_true", default=True)
    parser.add_argument("--no-overlay-passive", dest="input_integrity_overlay_passive", action="store_false")
    parser.add_argument("--overlay-no-focus", dest="input_integrity_overlay_no_focus", action="store_true", default=True)
    parser.add_argument("--overlay-focusable", dest="input_integrity_overlay_no_focus", action="store_false")
    parser.add_argument("--close-overlay-after-test", dest="close_overlay_after_test", action="store_true", default=True)
    parser.add_argument("--keep-overlay-after-test", dest="close_overlay_after_test", action="store_false")
    parser.add_argument("--post-test-focus-target", choices=["runelite", "powershell", "desktop", "none"], default="powershell")
    parser.add_argument("--require-user-control-confirmation", action="store_true")
    parser.add_argument("--input-integrity-overlay-corner", choices=["top_left", "top_right", "bottom_left", "bottom_right"], default="top_right")
    parser.add_argument("--input-integrity-fail-on-injected", dest="input_integrity_fail_on_injected", action="store_true", default=True)
    parser.add_argument("--no-input-integrity-fail-on-injected", dest="input_integrity_fail_on_injected", action="store_false")
    parser.add_argument("--input-integrity-fail-on-bypass", dest="input_integrity_fail_on_bypass", action="store_true", default=True)
    parser.add_argument("--no-input-integrity-fail-on-bypass", dest="input_integrity_fail_on_bypass", action="store_false")
    parser.add_argument("--input-profile", choices=["instant_debug", "steady", "natural", "manual_calibrated"], default="instant_debug")
    parser.add_argument("--movement-profile", choices=["instant_test", "linear_debug", "smooth_bezier", "fitts_guided", "wind_mouse"], default="linear_debug")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--plan-next-click", action="store_true", help="Build a human-profile-informed click plan without executing input.")
    parser.add_argument("--print-click-plan", action="store_true", help="Print the click plan in human-readable form.")
    parser.add_argument("--dry-run-click-plan", action="store_true", help="Alias for --plan-next-click; never executes input.")
    parser.add_argument("--explain-target", action="store_true", help="Print the shared selected-target explanation when available.")
    parser.add_argument("--verify-coordinates", action="store_true", help="Resolve click coordinates without adding execution behavior.")
    parser.add_argument("--focus-runelite", dest="focus_runelite", action="store_true")
    parser.add_argument("--no-focus-runelite", dest="focus_runelite", action="store_false")
    parser.set_defaults(focus_runelite=None)
    parser.add_argument("--window-title-filter", default="RuneLite")
    parser.add_argument("--verify-after-action", action="store_true")
    parser.add_argument("--after-action-wait-ms", type=int, default=500)
    parser.add_argument("--hover-only", action="store_true", help="Move to the selected target and confirm hover/menu state without clicking.")
    parser.add_argument("--hover-confirm-target", action="store_true", help="Require fresh plugin hover menu to match the selected target before clicking.")
    parser.add_argument("--hover-confirm-timeout-ms", type=int, default=120)
    parser.add_argument("--hover-poll-ms", type=int, default=10)
    parser.add_argument("--hover-position-tolerance", type=int, default=3)
    parser.add_argument("--click-hold-ms", type=int, default=0)
    parser.add_argument("--client-tick-debug", action="store_true")
    parser.add_argument("--client-tick-tail", type=int, default=0)
    parser.add_argument("--menu-entry-limit", type=int, default=5)
    parser.add_argument("--require-clicked-menu-match", action="store_true")
    parser.add_argument("--record-client-hot", action="store_true")
    parser.add_argument("--client-hot-output", default="interaction_geometry/live/client_tick_hot.jsonl")
    parser.add_argument("--client-hot-window-ms", type=int, default=5000)
    parser.add_argument("--client-hot-max-samples", type=int, default=128)
    parser.add_argument("--nav-trace", action="store_true", help="Write compact navigation decision traces as bounded JSONL debug output.")
    parser.add_argument("--nav-trace-output", default="interaction_geometry/live/navigation_decisions.jsonl")
    parser.add_argument("--nav-trace-console", action="store_true", help="Print one concise console line per navigation decision.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--sessions-dir", help="Override telemetry sessions root for pre-action readiness checks.")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--max-actions", type=int, default=1)
    parser.add_argument("--max-runtime-seconds", type=float, default=30.0)
    parser.add_argument("--cooldown-ms", type=int, default=1200)
    parser.add_argument("--action-timeout-ms", type=int, default=5000)
    parser.add_argument("--result-timeout-ms", type=int, default=15000)
    parser.add_argument("--poll-interval-ms", type=int, default=250)
    parser.add_argument("--wait-for-ready", type=float, default=0.0, metavar="SEC", help="Wait for daemon/overlay/highlighter/input readiness before executing.")
    parser.add_argument("--stop-on-warn", action="store_true")
    parser.add_argument("--stop-on-fail", action="store_true")
    parser.add_argument("--stop-after-inventory-changes", type=int)
    parser.add_argument("--stop-when-inventory-full", action="store_true")
    parser.add_argument("--max-successful-actions", type=int)
    parser.add_argument("--max-timeouts", type=int)
    parser.add_argument("--final-reconcile-ms", type=int, default=0)
    parser.add_argument("--final-reconcile-game-ticks", type=int, default=0)
    parser.add_argument("--resource-reconcile-ms", type=int, default=0)
    parser.add_argument("--resource-reconcile-game-ticks", type=int, default=0)
    parser.add_argument("--post-click-progress-tail-ticks", type=int, default=0)
    parser.add_argument("--stop-after-lifecycle-cycles", type=int)
    parser.add_argument("--stop-after-service-cycles", type=int)
    parser.add_argument("--stop-after-post-service-logs", type=int)
    parser.add_argument("--max-total-actions", type=int)
    parser.add_argument("--max-wall-time-minutes", type=float)
    parser.add_argument("--max-consecutive-no-progress", type=int)
    parser.add_argument("--max-consecutive-timeouts", type=int)
    parser.add_argument("--nav-verify-game-ticks", type=int, default=0)
    parser.add_argument("--nav-verify-ms", type=int, default=0)
    parser.add_argument("--nav-progress-min-distance", type=float, default=0.0)
    parser.add_argument("--transition-verify-game-ticks", type=int, default=0)
    parser.add_argument("--transition-verify-ms", type=int, default=0)
    parser.add_argument("--transition-pending-game-ticks", type=int, default=0)
    parser.add_argument("--transition-retry-after-stall-ticks", type=int, default=0)
    parser.add_argument("--pacing-profile", choices=["instant_debug", "steady", "natural"], default="instant_debug")
    parser.add_argument("--target-switch-min-ms", type=int, default=0)
    parser.add_argument("--target-switch-max-ms", type=int, default=0)
    parser.add_argument("--post-resource-min-ms", type=int, default=0)
    parser.add_argument("--post-resource-max-ms", type=int, default=0)
    parser.add_argument("--occasional-idle-chance", type=float, default=0.0)
    parser.add_argument("--occasional-idle-min-ms", type=int, default=0)
    parser.add_argument("--occasional-idle-max-ms", type=int, default=0)
    parser.add_argument("--target-hover-failure-limit", type=int, default=3)
    parser.add_argument("--target-suppression-ms", type=int, default=2500)
    parser.add_argument("--clear-suppression-on-progress", dest="clear_suppression_on_progress", action="store_true", default=True)
    parser.add_argument("--no-clear-suppression-on-progress", dest="clear_suppression_on_progress", action="store_false")
    parser.add_argument("--max-candidate-reacquire-rounds", type=int, default=3)
    parser.add_argument("--max-waypoint-alternates", type=int, default=12)
    parser.add_argument("--max-hover-checks-per-waypoint", type=int, default=0)
    parser.add_argument("--max-navigation-reacquire-rounds", type=int, default=3)
    parser.add_argument("--max-camera-adjustments-per-route-step", type=int, default=0)
    parser.add_argument("--camera-adjust-ms", type=int, default=0)
    parser.add_argument("--camera-adjust-direction", choices=["auto", "left", "right"], default="auto")
    parser.add_argument("--camera-reacquire-ms", type=int, default=0)
    parser.add_argument("--camera-reacquire-waypoint", action="store_true", help="For occluded navigation waypoints, nudge camera and reproject the same world tile before clicking.")
    parser.add_argument("--camera-method", choices=["auto", "keyboard_arrows", "keyboard_wasd", "middle_mouse_drag"], default="auto")
    parser.add_argument("--camera-exposure-max-ms", type=int, default=0)
    parser.add_argument("--camera-sample-interval-ms", type=int, default=None)
    parser.add_argument("--camera-max-direction-switches", type=int, default=None)
    parser.add_argument("--camera-allow-diagonal", action="store_true")
    parser.add_argument("--camera-reacquire-timeout-ms", type=int, default=0)
    parser.add_argument("--camera-probe-ms", type=int, default=120)
    parser.add_argument("--camera-max-nudges", type=int, default=0)
    parser.add_argument("--camera-follow-target", dest="camera_follow_target", action="store_true", default=True)
    parser.add_argument("--no-camera-follow-target", dest="camera_follow_target", action="store_false")
    parser.add_argument("--camera-min-score-improvement", type=int, default=1)
    parser.add_argument("--camera-min-projection-delta-px", type=float, default=2.0)
    parser.add_argument("--camera-allow-pitch-adjust", action="store_true")
    parser.add_argument("--camera-debug-summary", action="store_true")
    parser.add_argument("--camera-self-test", action="store_true")
    parser.add_argument("--camera-test-return", action="store_true")
    parser.add_argument("--reject-edge-route-clicks", action="store_true", help="Reject route waypoint clicks that are too close to the viewport/canvas edge or too clipped to be safe.")
    parser.add_argument("--camera-reacquire-on-edge-projection", action="store_true", help="Use camera-guided waypoint reacquisition when a useful route tile projects poorly at the edge.")
    parser.add_argument("--route-click-edge-margin-px", type=int, default=12)
    parser.add_argument("--route-min-visible-area-ratio", type=float, default=0.45)
    parser.add_argument("--allow-minimap-navigation", action="store_true", help="Reserved navigation-only fallback; ignored until reliable minimap click telemetry is available.")
    parser.add_argument("--route-waypoint-lookahead-tiles", type=int, default=12)
    parser.add_argument("--route-waypoint-max-horizon-tiles", type=int, default=25)
    parser.add_argument("--min-route-progress-tiles", type=int, default=3)
    parser.add_argument("--max-route-waypoint-distance", type=int, default=30)
    parser.add_argument("--prefer-long-visible-waypoint", action="store_true")
    parser.add_argument("--route-waypoint-distance-mode", choices=["adaptive", "precise", "next"], default="adaptive")
    parser.add_argument("--nav-replan-while-moving", nargs="?", const=True, default=False, type=bool_arg)
    parser.add_argument("--nav-min-game-ticks-between-clicks", type=int, default=3)
    parser.add_argument("--nav-stuck-game-ticks", type=int, default=6)
    parser.add_argument("--nav-destination-arrival-distance", type=int, default=1)
    parser.add_argument("--no-safe-target-wait-ms", type=int, default=150)
    parser.add_argument("--suppressed-target-wait-ms", type=int, default=75)
    parser.add_argument("--capture-debug-screenshots", action="store_true", help="Capture sparse event-triggered visual debug bundles.")
    parser.add_argument("--screenshot-on-failure", action="store_true", help="Capture a debug bundle for execution failures and menu mismatches.")
    parser.add_argument("--screenshot-on-camera-recovery", action="store_true", help="Capture debug bundles around camera/reprojection recovery events.")
    parser.add_argument("--screenshot-on-timeout", action="store_true", help="Capture debug bundles for resource/navigation timeout events.")
    parser.add_argument("--screenshot-on-edge-reject", action="store_true", help="Capture debug bundles when an edge-clipped route click is rejected.")
    parser.add_argument("--screenshot-on-lifecycle-transition", action="store_true", help="Capture debug bundles for major lifecycle transitions.")
    parser.add_argument("--max-debug-screenshots", type=int, default=20)
    parser.add_argument("--debug-screenshot-dir")
    parser.add_argument("--summary-every-action", action="store_true")
    return parser.parse_args(argv)


def apply_focus_default(args: argparse.Namespace) -> argparse.Namespace:
    if args.dry_run_click_plan or args.print_click_plan:
        args.plan_next_click = True
    if args.plan_next_click:
        args.execute = False
        args.hover_only = False
    if getattr(args, "input_integrity_self_test_no_move", False):
        args.input_integrity_self_test = True
    if getattr(args, "no_overlay", False):
        args.show_input_integrity_overlay = False
    if args.hover_only:
        args.execute = False
    live_input_requested = bool(
        args.execute
        or args.hover_only
        or args.camera_self_test
        or args.input_integrity_self_test
        or getattr(args, "arduino_movement_diagnostics", False)
        or getattr(args, "arduino_pointer_calibration_test", False)
    )
    if args.backend is None:
        args.backend = (
            "arduino"
            if live_input_requested
            or args.arduino_check
            or args.arduino_stop_all
            or args.arduino_status
            or args.arduino_port_health
            or args.arduino_identify
            or args.arduino_caps
            or getattr(args, "arduino_usb_diagnostics", False)
            or getattr(args, "arduino_movement_diagnostics", False)
            or getattr(args, "arduino_pointer_calibration_test", False)
            else "pyautogui"
        )
    if args.unsafe_allow_pyautogui_live:
        args.allow_software_input = True
    if args.max_total_actions:
        args.max_actions = max(args.max_actions, int(args.max_total_actions))
        args.loop = True
    if args.max_wall_time_minutes:
        args.max_runtime_seconds = max(args.max_runtime_seconds, float(args.max_wall_time_minutes) * 60.0)
        args.loop = True
    if (args.stop_after_inventory_changes or args.stop_when_inventory_full or args.max_successful_actions or args.max_timeouts) and args.max_actions <= 1:
        args.max_actions = max(args.max_actions, int(args.stop_after_inventory_changes or args.max_successful_actions or 1))
        args.loop = True
    if args.stop_after_lifecycle_cycles or args.stop_after_service_cycles or args.stop_after_post_service_logs:
        args.loop = True
    if args.focus_runelite is None:
        args.focus_runelite = bool(live_input_requested and args.backend == "pyautogui")
    args.require_live_readiness = bool(args.execute or args.hover_only)
    args.live_input_backend_required = bool(live_input_requested)
    if args.arduino_stop_all:
        args.arduino_check = "stop-all"
    elif args.arduino_status:
        args.arduino_check = "status"
    elif args.arduino_port_health:
        args.arduino_check = "port-health"
    elif args.arduino_identify:
        args.arduino_check = "identify"
    elif args.arduino_caps:
        args.arduino_check = "caps"
    elif getattr(args, "arduino_usb_diagnostics", False):
        args.arduino_check = "usb-diagnostics"
    return args


def build_click_plan_payload(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[str] = []
    plan: dict[str, Any]
    fabric_status: dict[str, Any] = {}
    try:
        import knowledge_fabric

        fabric = knowledge_fabric.fabric_from_live(
            daemon_url=args.daemon_url,
            snapshot_url=args.snapshot_url,
            timeout=max(0.5, float(args.timeout or 3.0)),
        )
        fabric_status = fabric.status()
        response = fabric.human_click_plan()
        plan = response.get("data") if isinstance(response.get("data"), dict) else {}
        warnings.extend(response.get("warnings") or [])
    except Exception as error:  # noqa: BLE001
        import task_script_api

        plan = task_script_api.get_next_click_plan()
        warnings.append(f"live click-planning context unavailable: {type(error).__name__}: {error}")
    if not plan:
        plan = {
            "schema": "human_click_plan.v1",
            "status": "FAIL",
            "warnings": ["click plan could not be built"],
            "missingCapabilities": ["click_plan"],
        }
    return {
        "schema": "execute_next_action_click_plan.v1",
        "status": plan.get("status") or "WARN",
        "dryRun": True,
        "executed": False,
        "clickPlan": plan,
        "knowledgeFabricStatus": {
            "indexesBuilt": fabric_status.get("indexesBuilt"),
            "objectCount": (fabric_status.get("liveWorldIndex") or {}).get("spatialIndexSummary", {}).get("objectCount")
            if isinstance(fabric_status.get("liveWorldIndex"), dict)
            else None,
        },
        "warnings": list(dict.fromkeys(warnings + (plan.get("warnings") or []))),
    }


def format_click_plan_payload(payload: dict[str, Any]) -> str:
    plan = payload.get("clickPlan") if isinstance(payload.get("clickPlan"), dict) else {}
    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    readiness = plan.get("readiness") if isinstance(plan.get("readiness"), dict) else {}
    aim = plan.get("aim") if isinstance(plan.get("aim"), dict) else {}
    lines = [
        f"CLICK PLAN - {plan.get('status') or payload.get('status') or 'UNKNOWN'}",
        "",
        f"Task: {plan.get('task') or 'unknown'}",
        f"Action: {plan.get('action') or 'unknown'}",
        f"Target: {target.get('name') or 'unknown'} ({target.get('targetQuality') or 'unknown'})",
        f"Center/base point: {aim.get('basePoint') or 'none'}",
        f"Profile point: {aim.get('plannedPoint') or 'none'}",
        f"Offset: {aim.get('offset') or 'none'} source={aim.get('offsetSource') or 'unknown'}",
        f"Confidence: {plan.get('confidence') if plan.get('confidence') is not None else 'unknown'}",
        "",
        "Readiness:",
        f"  hover={readiness.get('hoverConfirmed')} menu={readiness.get('menuConfirmed')} visible={readiness.get('targetVisible')} geometry={readiness.get('geometryAvailable')}",
        f"  blockers={readiness.get('blockedReasons') or []}",
        "",
        "Reasons:",
    ]
    reasons = plan.get("reasons") if isinstance(plan.get("reasons"), list) else []
    lines.extend(f"  {reason}" for reason in reasons) if reasons else lines.append("  none")
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(["", "Warnings:"])
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    return "\n".join(lines).rstrip() + "\n"


def client_hot_records_from_payload(payload: dict[str, Any], *, max_samples: int = 128) -> list[dict[str, Any]]:
    results = payload.get("actionResults") if isinstance(payload.get("actionResults"), list) else [payload]
    records: list[dict[str, Any]] = []
    for index, result in enumerate(results, start=1):
        trace = result.get("actionTrace") if isinstance(result, dict) and isinstance(result.get("actionTrace"), dict) else {}
        client_tick = trace.get("clientTick") if isinstance(trace.get("clientTick"), dict) else {}
        for sample_kind, key in (
            ("accepted_hover", "acceptedHoverSample"),
            ("last_click_before", "lastMenuOptionClickedBefore"),
            ("last_click_after", "lastMenuOptionClickedAfter"),
        ):
            sample = client_tick.get(key)
            if isinstance(sample, dict):
                records.append(
                    {
                        "schema": "client_tick_hot_record.v1",
                        "actionIndex": index,
                        "sampleKind": sample_kind,
                        "sample": sample,
                    }
                )
        for sample in client_tick.get("rejectedHoverSamples") or []:
            if isinstance(sample, dict):
                records.append(
                    {
                        "schema": "client_tick_hot_record.v1",
                        "actionIndex": index,
                        "sampleKind": "rejected_hover",
                        "sample": sample.get("sample") if isinstance(sample.get("sample"), dict) else sample,
                        "reason": sample.get("reason"),
                    }
                )
    return records[-max(0, int(max_samples or 0)) :]


def maybe_record_client_hot(payload: dict[str, Any], args: argparse.Namespace) -> None:
    if not args.record_client_hot:
        return
    records = client_hot_records_from_payload(payload, max_samples=args.client_hot_max_samples)
    output = Path(args.client_hot_output)
    if not output.is_absolute():
        session_path = None
        readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
        session = readiness.get("session") if isinstance(readiness.get("session"), dict) else {}
        if isinstance(session.get("activeSessionPath"), str):
            session_path = Path(session["activeSessionPath"])
        output = (session_path / output if session_path else output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":"), sort_keys=False) + "\n")


def _session_path_from_action_payload(payload: dict[str, Any]) -> Path | None:
    candidates: list[dict[str, Any]] = []
    if isinstance(payload.get("readiness"), dict):
        candidates.append(payload["readiness"])
    for result in payload.get("actionResults") or []:
        if isinstance(result, dict) and isinstance(result.get("readiness"), dict):
            candidates.append(result["readiness"])
    for readiness in candidates:
        session = readiness.get("session") if isinstance(readiness.get("session"), dict) else {}
        for key in ("activeSessionPath", "sessionPath"):
            value = session.get(key)
            if isinstance(value, str) and value.strip():
                return Path(value)
    return None


def _session_path_from_daemon_url(daemon_url: str | None) -> Path | None:
    if not daemon_url:
        return None
    try:
        status_url = str(daemon_url).rstrip("/") + "/status"
        with urllib.request.urlopen(status_url, timeout=1.5) as response:
            status = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return None
    for key in ("activeSessionPath", "sessionPath"):
        value = status.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value)
    session = status.get("session") if isinstance(status.get("session"), dict) else {}
    for key in ("activeSessionPath", "sessionPath"):
        value = session.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value)
    return None


def _latest_action_result_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    results = payload.get("actionResults") if isinstance(payload.get("actionResults"), list) else None
    if results:
        for result in reversed(results):
            if isinstance(result, dict) and isinstance(result.get("actionTrace"), dict):
                return result
    if isinstance(payload.get("actionTrace"), dict):
        return payload
    return None


def persist_latest_action_trace(payload: dict[str, Any], *, daemon_url: str | None = None) -> dict[str, Any] | None:
    latest = _latest_action_result_payload(payload)
    if not isinstance(latest, dict):
        return None
    session_path = _session_path_from_action_payload(payload) or _session_path_from_daemon_url(daemon_url)
    if session_path is None:
        return None
    live_dir = session_path / "interaction_geometry" / "live"
    live_dir.mkdir(parents=True, exist_ok=True)
    path = live_dir / "last_action_trace.json"
    record = {
        "schema": "latest_action_trace_record.v1",
        "generatedAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "execute_next_action",
        "actionId": latest.get("actionId"),
        "status": latest.get("status"),
        "proposedAction": latest.get("proposedAction"),
        "executed": latest.get("executed"),
        "verificationStatus": latest.get("verificationStatus"),
        "observedResult": latest.get("observedResult"),
        "hoverConfirmation": latest.get("hoverConfirmation"),
        "inputIntegrityPhaseReport": (latest.get("actionTrace") or {}).get("inputIntegrityPhaseReport"),
        "actionTrace": latest.get("actionTrace"),
    }
    path.write_text(json.dumps(record, indent=2, sort_keys=False), encoding="utf-8")
    return {"schema": "latest_action_trace_persisted.v1", "path": str(path), "status": "PASS"}


def format_human(payload: dict[str, Any]) -> str:
    if payload.get("schema") == LOOP_SCHEMA:
        summary = payload.get("loopSummary") if isinstance(payload.get("loopSummary"), dict) else {}
        lines = [
            f"EXECUTE ACTION LOOP - {payload.get('status') or 'UNKNOWN'}",
            "",
            f"Mode: {'dry-run' if payload.get('dryRun') else 'execute'}",
            f"Executed actions: {payload.get('executedActionCount', 0)} / {payload.get('maxActions', 'unknown')}",
            f"Reason: {payload.get('reason') or 'unknown'}",
            "",
            "Summary:",
            f"  Proposed actions: {summary.get('proposedActions', payload.get('actionResultCount', 0))}",
            f"  Actual click attempts: {summary.get('actionsAttempted', payload.get('executedActionCount', 0))}",
            f"  Actions executed: {summary.get('actionsExecuted', payload.get('executedActionCount', 0))}",
            f"  Hover checks: {summary.get('hoverChecks', 0)}",
            f"  Skips: unsafe geometry={summary.get('skippedUnsafeGeometry', 0)} hover mismatch={summary.get('skippedHoverMismatch', 0)} stale client tick={summary.get('skippedStaleClientTick', 0)} suppressed targets={summary.get('targetsSuppressed', 0)} no-progress suppressions={summary.get('targetNoProgressSuppressions', 0)}",
            f"  Navigation: occluded waypoints={summary.get('waypointOccludedByObject', 0)} alternate attempts={summary.get('navigationAlternateAttempts', 0)} camera adjustments={summary.get('cameraAdjustments', 0)} edge rejects={summary.get('edgeRouteClicksRejected', 0)} edge camera={summary.get('cameraReacquireOnEdgeCount', 0)} volatile skips={summary.get('volatileHoverSkips', 0)} menu flips={summary.get('menuFlipMismatchCount', 0)}",
            f"  Route stability: motion waits={summary.get('navigationInProgressWaits', 0)} replan suppressed={summary.get('routeReplanSuppressedWhileMoving', 0)} oscillation={summary.get('routeOscillationDetections', 0)} backtracking={summary.get('routeBacktrackingDetections', 0)} barrier={summary.get('routeBarrierDetections', 0)}",
            f"  Navigation trace: entries={summary.get('navigationTraceEntries', 0)} path={summary.get('navigationTraceOutputPath') or 'console-only'}" if summary.get("navigationTraceEntries") else None,
            f"  Route transitions: attempts={summary.get('routeTransitionAttempts', 0)} firstTry={summary.get('routeTransitionFirstTrySuccesses', 0)} pending={summary.get('routeTransitionPending', 0)} retryRequired={summary.get('routeTransitionRetryRequired', 0)} retrySuccess={summary.get('routeTransitionRetrySuccesses', 0)} reconciled={summary.get('routeTransitionReconciledSuccesses', 0)} trueTimeouts={summary.get('routeTransitionTrueTimeouts', 0)}",
            f"  Successful actions: {summary.get('successfulActions', 0)}",
            f"  Timeouts: {summary.get('timeouts', 0)} unresolved={summary.get('unresolvedTimeouts', 0)} trueUnresolved={summary.get('trueUnresolvedTimeouts', summary.get('unresolvedTimeouts', 0))} classifications={summary.get('timeoutClassifications', {})}",
            f"  Timeout details: reasons={summary.get('timeoutReasons', {})} actions={summary.get('timeoutActionTypes', {})} intents={summary.get('timeoutsByIntent', {})} recoveredBy={summary.get('timeoutRecoveredBy', {})} resolvedByRetry={summary.get('resolvedByRetry', 0)} resolvedByLateEvidence={summary.get('resolvedByLateEvidence', 0)} pendingButSafe={summary.get('pendingButSafe', 0)}",
            f"  Delayed reconciliations: {summary.get('delayedProgressReconciliations', 0)} resource timeout recoveries={summary.get('resourceTimeoutReconciledSuccesses', 0)}",
            f"  Recoverable goal retries: {summary.get('recoverableFailuresAfterGoal', 0)}" if summary.get("goalReachedWithRecoverableFailures") else None,
            f"  Inventory changes: {summary.get('inventoryChanges', 0)}",
            f"  Inventory free slots: {summary.get('inventoryFreeSlotsStart', 'unknown')} -> {summary.get('inventoryFreeSlotsEnd', 'unknown')}",
            f"  Resource count: {summary.get('resourceCountStart', 'unknown')} -> {summary.get('resourceCountEnd', 'unknown')}",
            f"  Progress: {summary.get('progressStart', 'unknown')} -> {summary.get('progressEnd', 'unknown')}",
            f"  Lifecycle cycles: started={summary.get('lifecycleCyclesStarted', 0)} completed={summary.get('lifecycleCyclesCompleted', 0)} serviceComplete={summary.get('serviceCompleteEvents', 0)} returnComplete={summary.get('returnRoutesCompleted', 0)} post-service logs={summary.get('postServiceLogsCollected', 0)}",
            f"  Hover confirms: {summary.get('hoverConfirmSuccesses', 0)} pass / {summary.get('hoverConfirmFailures', 0)} fail",
            f"  Hover failures: cancel={summary.get('cancelHoverFailures', 0)} Walk here={summary.get('walkHereHoverFailures', 0)} stale={summary.get('staleHoverSamples', 0)} volatile={summary.get('volatileHoverFailures', 0)}",
            f"  Menu clicks: {summary.get('expectedMenuClicks', 0)} expected / {summary.get('walkHereClicks', 0)} Walk here / {summary.get('cancelClicks', 0)} Cancel",
            f"  Hover latency ms: min={summary.get('hoverLatencyMinMillis', 'n/a')} avg={summary.get('hoverLatencyAvgMillis', 'n/a')} max={summary.get('hoverLatencyMaxMillis', 'n/a')}",
            f"  Pacing delays ms: count={summary.get('pacingDelayCount', 0)} min={summary.get('pacingDelayMinMillis', 'n/a')} avg={summary.get('pacingDelayAvgMillis', 'n/a')} max={summary.get('pacingDelayMaxMillis', 'n/a')}",
            f"  Input profile: {summary.get('inputProfile') or 'unknown'} | mouse move avg ms={summary.get('averageMouseMoveMs', 'n/a')} click hold avg ms={summary.get('averageClickHoldMs', 'n/a')} reaction avg ms={summary.get('averageReactionDelayMs', 'n/a')}",
            f"  Live input: backend={(summary.get('liveInput') or {}).get('liveInputBackend') if isinstance(summary.get('liveInput'), dict) else 'unknown'} required={(summary.get('liveInput') or {}).get('liveInputBackendRequired') if isinstance(summary.get('liveInput'), dict) else 'unknown'} softwareAllowed={(summary.get('liveInput') or {}).get('softwareInputAllowed') if isinstance(summary.get('liveInput'), dict) else 'unknown'}",
            f"  Camera hold ms: min={summary.get('cameraHoldMinMs', 'n/a')} avg={summary.get('cameraHoldAvgMs', 'n/a')} max={summary.get('cameraHoldMaxMs', 'n/a')} switches={summary.get('cameraDirectionSwitches', 0)}",
            f"  Direct backend bypasses: {summary.get('directBackendBypassCount', 0)}",
            f"  Debug screenshots: captured={summary.get('debugScreenshotBundlesCaptured', 0)} failures={summary.get('debugScreenshotCaptureFailures', 0)} skippedByLimit={summary.get('debugScreenshotBundlesSkippedByLimit', 0)}",
            f"  Reacquire waits: {summary.get('targetReacquireWaits', 0)} ({summary.get('targetReacquireWaitMillis', 0)} ms)",
            f"  Final reconcile: {summary.get('finalReconcileResult') or 'none'} ({summary.get('finalReconcileMillis', 0)} ms, {summary.get('finalReconcileGameTicks', 0)} ticks)",
            f"  Final cycle stage: {summary.get('finalCycleStage') or 'unknown'}",
            f"  Final phase/intent: {summary.get('finalPhase') or 'unknown'} / {summary.get('finalActiveIntent') or 'unknown'}",
            f"  Final location: {summary.get('finalLocation') or 'unknown'} source={summary.get('finalLocationSource') or 'unknown'} confidence={summary.get('finalLocationConfidence') if summary.get('finalLocationConfidence') is not None else 'unknown'}",
            f"  Last observed signals: {', '.join(str(item) for item in (summary.get('lastObservedSignals') or [])) or 'none'}",
            "",
            "Actions:",
        ]
        lines = [line for line in lines if line is not None]
        action_results = payload.get("actionResults") if isinstance(payload.get("actionResults"), list) else []
        if action_results:
            for index, action_result in enumerate(action_results, start=1):
                proposal = action_result.get("proposal") if isinstance(action_result.get("proposal"), dict) else {}
                lifecycle = action_result.get("lifecycleState") if isinstance(action_result.get("lifecycleState"), dict) else {}
                observed = action_result.get("observedResult") if isinstance(action_result.get("observedResult"), dict) else {}
                commands = action_result.get("commands") if isinstance(action_result.get("commands"), list) else []
                lines.extend(
                    [
                        f"  {index}. {action_result.get('proposedAction') or 'none'} -> {proposal.get('targetName') or 'none'}",
                        f"     command: {commands[0] if commands else 'none'}",
                        f"     expected: {(action_result.get('expectedResult') or {}).get('resultType') if isinstance(action_result.get('expectedResult'), dict) else 'unknown'}",
                        f"     observed: {observed.get('observedResult') or 'unknown'}",
                        f"     outcome: {observed.get('resultOutcome') or lifecycle.get('resultOutcome') or 'unknown'} complete={observed.get('resultComplete') if observed.get('resultComplete') is not None else lifecycle.get('resultComplete')}",
                        f"     lifecycle: {lifecycle.get('currentState') or 'unknown'} reason={lifecycle.get('reason') or 'unknown'}",
                    ]
                )
        else:
            lines.append("  none")
        warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
        lines.extend(["", "Warnings:"])
        lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
        return "\n".join(lines).rstrip() + "\n"
    proposal = payload.get("proposal") if isinstance(payload.get("proposal"), dict) else {}
    movement = payload.get("movementPlan") if isinstance(payload.get("movementPlan"), dict) else {}
    resolution = payload.get("clickPointResolution") if isinstance(payload.get("clickPointResolution"), dict) else {}
    lifecycle = payload.get("lifecycleState") if isinstance(payload.get("lifecycleState"), dict) else {}
    observed = payload.get("observedResult") if isinstance(payload.get("observedResult"), dict) else {}
    readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
    action_readiness = readiness.get("actionReadiness") if isinstance(readiness.get("actionReadiness"), dict) else {}
    hover = payload.get("hoverConfirmation") if isinstance(payload.get("hoverConfirmation"), dict) else {}
    trace = payload.get("actionTrace") if isinstance(payload.get("actionTrace"), dict) else {}
    human_input = trace.get("humanInput") if isinstance(trace.get("humanInput"), dict) else {}
    reacquisition = trace.get("reacquisition") if isinstance(trace.get("reacquisition"), dict) else {}
    hover_sample = hover.get("sample") if isinstance(hover.get("sample"), dict) else hover.get("latestHoverMenu") if isinstance(hover.get("latestHoverMenu"), dict) else {}
    latest_match = hover.get("latestMatch") if isinstance(hover.get("latestMatch"), dict) else {}
    if not hover_sample and isinstance(latest_match.get("sample"), dict):
        hover_sample = latest_match.get("sample")
    explanation = proposal.get("targetExplanation") if isinstance(proposal.get("targetExplanation"), dict) else {}
    lines = [
        f"EXECUTE NEXT ACTION - {payload.get('status') or 'UNKNOWN'}",
        "",
        f"Mode: {'dry-run' if payload.get('dryRun') else 'execute'}",
        f"Backend: {payload.get('backend') or 'unknown'}",
        f"Movement profile: {payload.get('movementProfile') or 'unknown'}",
        f"Input profile: {human_input.get('profile') or 'unknown'}",
        "",
        "Proposal:",
        f"  Action: {proposal.get('proposedAction') or payload.get('proposedAction')}",
        f"  Target: {proposal.get('targetName') or 'none'}",
        f"  Reason: {proposal.get('reason') or 'unknown'}",
        f"  Click point space: {proposal.get('clickPointSpace') or 'unknown'}",
        f"  Canvas click point: {proposal.get('suggestedClickPoint') or 'none'}",
        f"  Screen click point: {proposal.get('resolvedScreenClickPoint') or resolution.get('screenClickPoint') or 'none'}",
        f"  Conversion: {resolution.get('method') or 'unknown'}",
        f"  Key action: {proposal.get('keyAction') or 'none'}",
        "",
        "Movement:",
        f"  Duration ms: {movement.get('durationMs', 'n/a')}",
        f"  Point count: {movement.get('pointCount', 'n/a')}",
        f"  Click point: {movement.get('clickPoint', 'n/a')}",
        f"  Governed avg move/click ms: {human_input.get('averageMouseMoveMs', 'n/a')} / {human_input.get('averageClickHoldMs', 'n/a')}",
        f"  Live input: backend={human_input.get('liveInputBackend') or 'unknown'} required={human_input.get('liveInputBackendRequired')} softwareAllowed={human_input.get('softwareInputAllowed')}",
        f"  Direct backend bypasses: {human_input.get('directBackendBypassCount', 0)}",
        "",
        "Lifecycle:",
        f"  State: {lifecycle.get('currentState') or 'unknown'}",
        f"  Expected: {(payload.get('expectedResult') or {}).get('resultType') if isinstance(payload.get('expectedResult'), dict) else 'unknown'}",
        f"  Observed: {observed.get('observedResult') or 'unknown'}",
        f"  Signals: {', '.join(str(item) for item in (observed.get('observedSignals') or lifecycle.get('observedSignals') or [])) or 'none'}",
        f"  Outcome: {observed.get('resultOutcome') or lifecycle.get('resultOutcome') or 'unknown'} | complete={observed.get('resultComplete') if observed.get('resultComplete') is not None else lifecycle.get('resultComplete')}",
        f"  Next action allowed: {observed.get('nextActionAllowed') if observed.get('nextActionAllowed') is not None else lifecycle.get('nextActionAllowed')}",
        f"  Verification: {payload.get('verificationStatus') or 'unknown'}",
        f"  Next allowed: {payload.get('nextAllowedAt') or 'unknown'}",
        "",
        "Hover confirmation:",
        f"  Status: {hover.get('status') or 'not requested'}",
        f"  Confirmed: {hover.get('confirmed') if hover.get('confirmed') is not None else 'unknown'}",
        f"  Latency ms: {hover.get('latencyMillis', 'n/a')}",
        f"  Reason: {hover.get('reason') or 'unknown'}",
        f"  Top menu: {hover_sample.get('topOption') or hover_sample.get('option') or 'unknown'} {hover_sample.get('topTarget') or hover_sample.get('target') or ''}".rstrip(),
        f"  Clicked menu: {((hover.get('lastMenuOptionClickedAfter') or {}).get('option') if isinstance(hover.get('lastMenuOptionClickedAfter'), dict) else None) or 'unknown'} {((hover.get('lastMenuOptionClickedAfter') or {}).get('target') if isinstance(hover.get('lastMenuOptionClickedAfter'), dict) else None) or ''}".rstrip(),
        f"  Click classification: {hover.get('clickClassification') or observed.get('menuClickClassification') or 'unknown'}",
        f"  Action classification: {observed.get('actionResultClassification') or 'unknown'}",
        "",
        "Camera reacquire:",
        f"  Attempts: {len(reacquisition.get('cameraExposureAttempts') or []) if isinstance(reacquisition.get('cameraExposureAttempts'), list) else 0}",
        f"  Reacquired by camera: {reacquisition.get('waypointReacquiredByCamera') if reacquisition.get('waypointReacquiredByCamera') is not None else 'unknown'}",
        "",
        "Pre-action readiness:",
        f"  Status: {readiness.get('status') or 'not checked'}",
        f"  Proposed action: {readiness.get('proposedAction') or 'unknown'}",
        f"  Current intent: {readiness.get('currentIntent') or 'unknown'}",
        f"  Action readiness: {action_readiness.get('status') or 'unknown'}",
        f"  Execution allowed: {action_readiness.get('executionAllowed') if action_readiness.get('executionAllowed') is not None else 'unknown'}",
        f"  Passed: {readiness.get('readinessPassed') if readiness.get('readinessPassed') is not None else 'unknown'}",
        "",
    ]
    if explanation:
        freshness = explanation.get("freshness") if isinstance(explanation.get("freshness"), dict) else {}
        lines.extend(
            [
                "Selected target:",
                f"  Name: {explanation.get('name') or 'unknown'}",
                f"  Object id: {explanation.get('objectId') if explanation.get('objectId') is not None else explanation.get('id', 'unknown')}",
                f"  Class/profile: {explanation.get('classId') or 'unknown'} / {explanation.get('profile') or 'unknown'} match={explanation.get('profileMatch')}",
                f"  Rank/score: {explanation.get('rank') if explanation.get('rank') is not None else 'unknown'} / {explanation.get('score') if explanation.get('score') is not None else 'unknown'}",
                f"  World: {explanation.get('worldLocation') or explanation.get('world') or 'unknown'}",
                f"  Aim point: {explanation.get('canvasAimPoint') or explanation.get('aimPoint') or 'unknown'}",
                f"  Safe aim point: {explanation.get('safeAimPoint') or 'none'}",
                f"  Geometry/on-screen: {explanation.get('geometryStatus') or explanation.get('geometryAvailable')} / {explanation.get('onScreenStatus') or explanation.get('onScreen')}",
                f"  Freshness: {freshness.get('status') or explanation.get('freshness') or 'unknown'}",
                f"  Accepted reasons: {', '.join(str(item) for item in (explanation.get('acceptedReasons') or [])) or 'none'}",
                f"  Rejected/demoted reasons: {', '.join(str(item) for item in (explanation.get('rejectedReasons') or [])) or 'none'}",
                "",
            ]
        )
    lines.append("Commands:")
    commands = payload.get("commands") if isinstance(payload.get("commands"), list) else []
    lines.extend(f"  {command}" for command in commands) if commands else lines.append("  none")
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(["", "Warnings:"])
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    return "\n".join(lines).rstrip() + "\n"


def format_camera_self_test(payload: dict[str, Any]) -> str:
    lines = [
        f"CAMERA SELF TEST - {payload.get('status') or 'UNKNOWN'}",
        "",
        f"Selected method: {payload.get('selectedMethod') or 'none'}",
        f"Calibration file: {payload.get('calibrationPath') or 'not written'}",
        "",
        "Methods:",
    ]
    for result in payload.get("methodResults") or []:
        if not isinstance(result, dict):
            continue
        lines.append(
            "  "
            + f"{result.get('method')}: status={result.get('status')} "
            + f"yawDelta={result.get('yawDelta')} pitchDelta={result.get('pitchDelta')} "
            + f"reason={result.get('reason') or 'none'}"
        )
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(["", "Warnings:"])
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    return "\n".join(lines).rstrip() + "\n"


def run_arduino_check(args: argparse.Namespace, backend: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "arduino_hid_check.v1",
        "check": args.arduino_check,
        "backend": getattr(backend, "name", backend.__class__.__name__),
        "status": "PASS",
        "result": None,
        "backendStatus": backend.status() if callable(getattr(backend, "status", None)) else None,
        "warnings": [],
    }
    try:
        if args.arduino_check == "ping":
            payload["result"] = backend.ping()
        elif args.arduino_check == "identify":
            payload["result"] = backend.identify()
        elif args.arduino_check == "caps":
            payload["result"] = backend.capabilities()
        elif args.arduino_check == "status":
            payload["result"] = backend.firmware_status()
        elif args.arduino_check == "stop-all":
            payload["result"] = backend.stop_all()
        elif args.arduino_check == "disarm":
            connect = getattr(backend, "connect", None)
            if callable(connect):
                connect()
            payload["result"] = backend.disarm()
        elif args.arduino_check == "monitor":
            from input_control.backend_arduino_hid import check_arduino_monitor_status

            payload["result"] = check_arduino_monitor_status(
                require_monitor=args.arduino_require_monitor,
                status_path=args.arduino_monitor_status_path,
                expected_vid=args.arduino_vid,
                expected_pid=args.arduino_pid,
                expected_com_port=args.arduino_port,
                max_event_age_ms=args.arduino_monitor_max_age_ms,
            )
            if args.arduino_require_monitor and not payload["result"].get("monitorPass"):
                payload["status"] = "FAIL"
        elif args.arduino_check == "port-health":
            health = getattr(backend, "port_health", None)
            if not callable(health):
                raise RuntimeError("backend does not support Arduino port health")
            payload["result"] = health()
            if payload["result"].get("portHealth") == "FAIL":
                payload["status"] = "FAIL"
        elif args.arduino_check == "usb-diagnostics":
            payload["result"] = run_arduino_usb_diagnostics(args)
            if payload["result"].get("status") == "FAIL":
                payload["status"] = "FAIL"
        elif args.arduino_check == "tiny-move":
            backend.arm(args.arduino_session_token if args.arduino_session_token != "auto" else None)
            try:
                backend.move_relative(1, 0, duration_ms=20)
                backend.move_relative(-1, 0, duration_ms=20)
                payload["result"] = "tiny movement pulse sent"
            finally:
                try:
                    backend.stop_all()
                except Exception as error:  # noqa: BLE001
                    payload["warnings"].append(f"stop_all failed: {type(error).__name__}: {error}")
                backend.disarm()
        payload["backendStatus"] = backend.status() if callable(getattr(backend, "status", None)) else None
    except Exception as error:  # noqa: BLE001
        payload["status"] = "FAIL"
        payload["warnings"].append(f"{type(error).__name__}: {error}")
        payload["backendStatus"] = backend.status() if callable(getattr(backend, "status", None)) else payload.get("backendStatus")
    return payload


def _cursor_delta(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, int]:
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    try:
        dx = int(after.get("x") or 0) - int(before.get("x") or 0)
        dy = int(after.get("y") or 0) - int(before.get("y") or 0)
    except (TypeError, ValueError):
        dx = 0
        dy = 0
    return {"dx": dx, "dy": dy}


def _movement_step_classification(step: dict[str, Any]) -> str:
    if not step.get("serialAckOk"):
        return "serial_fail"
    delta = step.get("inputIntegrityDelta") if isinstance(step.get("inputIntegrityDelta"), dict) else {}
    cursor_delta = step.get("windowsCursorDelta") if isinstance(step.get("windowsCursorDelta"), dict) else {}
    raw_mouse_delta = int(delta.get("rawInputMouseCountDelta") or 0)
    cursor_moved = bool(int(cursor_delta.get("dx") or 0) or int(cursor_delta.get("dy") or 0))
    if raw_mouse_delta > 0 and cursor_moved:
        return "serial_ok_rawinput_ok_cursor_ok"
    if raw_mouse_delta > 0 and not cursor_moved:
        return "serial_ok_rawinput_ok_cursor_no_move"
    if cursor_moved:
        return "serial_ok_cursor_ok_rawinput_missing"
    return "serial_ok_rawinput_no_event"


def _movement_diagnostic_classification(steps: list[dict[str, Any]], monitor_status: dict[str, Any] | None) -> tuple[str, list[str]]:
    if any(step.get("classification") == "serial_fail" for step in steps):
        return "serial_fail", []
    if monitor_status and not bool(monitor_status.get("monitorAvailable")):
        return "monitor_missing", []
    classifications = [str(step.get("classification") or "") for step in steps]
    if any(item == "serial_ok_rawinput_ok_cursor_no_move" for item in classifications):
        return "serial_ok_rawinput_ok_cursor_no_move", ["vmware_mouse_integration_blocking_possible"]
    cursor_ok_classes = {"serial_ok_rawinput_ok_cursor_ok", "serial_ok_cursor_ok_rawinput_missing"}
    if classifications and all(item in cursor_ok_classes for item in classifications):
        causes = ["rawinput_counter_coalesced_possible"] if any(item == "serial_ok_cursor_ok_rawinput_missing" for item in classifications) else []
        return "serial_ok_rawinput_ok_cursor_ok", causes
    if any(item == "serial_ok_rawinput_ok_cursor_ok" for item in classifications):
        return "serial_ok_rawinput_ok_cursor_ok", []
    if steps:
        return "serial_ok_rawinput_no_event", ["hid_report_format_issue_possible", "vmware_usb_hid_passthrough_issue_possible"]
    return "serial_fail", []


def _movement_reliability_classification(steps: list[dict[str, Any]]) -> tuple[str, list[str]]:
    if not steps:
        return "no_rawinput", ["no_movement_steps_ran"]
    if any(not bool(step.get("serialAckOk")) for step in steps if isinstance(step, dict)):
        return "focus_issue_possible", ["serial_ack_failed"]
    ok_steps = [step for step in steps if step.get("classification") == "serial_ok_rawinput_ok_cursor_ok"]
    cursor_ok_raw_missing = [step for step in steps if step.get("classification") == "serial_ok_cursor_ok_rawinput_missing"]
    raw_no_cursor = [step for step in steps if step.get("classification") == "serial_ok_rawinput_ok_cursor_no_move"]
    no_raw = [step for step in steps if step.get("classification") == "serial_ok_rawinput_no_event"]
    if len(ok_steps) + len(cursor_ok_raw_missing) == len(steps):
        causes = ["rawinput_counter_coalesced_possible"] if cursor_ok_raw_missing else []
        return "reliable", causes
    if ok_steps and (raw_no_cursor or no_raw):
        causes = []
        if raw_no_cursor:
            causes.append("vmware_integration_possible")
        if no_raw:
            causes.append("focus_issue_possible")
        return "intermittent_no_effect", list(dict.fromkeys(causes))
    if raw_no_cursor:
        return "rawinput_ok_cursor_no_move", ["vmware_integration_possible"]
    return "no_rawinput", ["hid_report_format_issue_possible", "vmware_usb_hid_passthrough_issue_possible"]


def _vmware_mouse_observations(args: argparse.Namespace) -> dict[str, Any]:
    service_script = (
        "$svc = Get-Service -Name VMTools -ErrorAction SilentlyContinue; "
        "if ($svc) { $svc | Select-Object Name,Status,StartType | ConvertTo-Json -Depth 4 }"
    )
    device_script = (
        "$devices = Get-PnpDevice -PresentOnly | "
        "Where-Object { $_.Class -in @('Mouse','HIDClass','Keyboard') -or $_.FriendlyName -match 'VMware|Arduino|Leonardo' -or $_.InstanceId -match 'VID_2341' } | "
        "Select-Object -First 80 Class,FriendlyName,InstanceId,Status; "
        "$devices | ConvertTo-Json -Depth 5"
    )
    devices = [item for item in _as_list(_powershell_json(device_script, timeout=6.0)) if isinstance(item, dict)]
    service = _powershell_json(service_script, timeout=4.0)
    service_status = (service or {}).get("Status") if isinstance(service, dict) else None
    arduino_devices = [item for item in devices if "VID_2341" in str(item.get("InstanceId") or "").upper() or "ARDUINO" in str(item.get("FriendlyName") or "").upper() or "LEONARDO" in str(item.get("FriendlyName") or "").upper()]
    vmware_devices = [item for item in devices if "VMWARE" in str(item.get("FriendlyName") or "").upper() or "VMWARE" in str(item.get("InstanceId") or "").upper()]
    mouse_devices = [item for item in devices if str(item.get("Class") or "").lower() == "mouse"]
    return {
        "schema": "vmware_mouse_observations.v1",
        "vmwareToolsService": service if isinstance(service, dict) else service,
        "vmwareToolsRunning": str(service_status).lower() == "running" or service_status == 4,
        "expectedArduinoComPort": getattr(args, "arduino_port", None),
        "presentInputDevices": devices,
        "arduinoHidDevices": arduino_devices,
        "vmwareInputDevices": vmware_devices,
        "mouseDevices": mouse_devices,
        "multipleMicePresent": len(mouse_devices) > 1,
    }


def run_arduino_movement_diagnostics(args: argparse.Namespace, backend: Any) -> dict[str, Any]:
    from input_control.arduino_monitor import InputIntegrityMonitor
    from input_control.input_integrity import input_integrity_delta

    status_path = Path(args.arduino_monitor_status_path or Path("interaction_geometry") / "live" / "input_integrity_status.json")
    backend_status_path = Path("interaction_geometry") / "live" / "arduino_backend_status.json"
    monitor = InputIntegrityMonitor(
        expected_vid=args.arduino_vid,
        expected_pid=args.arduino_pid,
        expected_com_port=args.arduino_port,
        live_input_backend="arduino",
        status_output=status_path,
        backend_status_path=backend_status_path,
        max_age_ms=args.arduino_monitor_max_age_ms,
        fail_on_injected=args.input_integrity_fail_on_injected,
        fail_on_bypass=args.input_integrity_fail_on_bypass,
    )
    delta_px = max(1, min(20, abs(int(getattr(args, "arduino_movement_diagnostic_delta", 5) or 5))))
    payload: dict[str, Any] = {
        "schema": "arduino_movement_diagnostics.v1",
        "status": "PASS",
        "backend": getattr(backend, "name", backend.__class__.__name__),
        "arduinoPort": args.arduino_port,
        "diagnosticDeltaPx": delta_px,
        "clickSent": False,
        "keySent": False,
        "stopAllBefore": None,
        "firmwareStatusBefore": None,
        "firmwareStatusArmed": None,
        "movementSteps": [],
        "monitorBefore": None,
        "monitorAfter": None,
        "vmwareMouseObservations": None,
        "classification": None,
        "reliabilityClassification": None,
        "possibleCauses": [],
        "stopAllFinal": False,
        "disarmed": False,
        "firmwareStatusAfter": None,
        "directBackendBypassCount": 0,
        "warnings": [],
    }

    def monitor_status(armed: bool) -> dict[str, Any]:
        status = monitor.status(
            require_monitor=False,
            arduino_armed=armed,
            direct_backend_bypass_count=0,
        )
        try:
            monitor.write_status(require_monitor=False, arduino_armed=armed, direct_backend_bypass_count=0)
        except Exception:  # noqa: BLE001
            pass
        return status

    def run_step(dx: int, dy: int) -> dict[str, Any]:
        before_status = monitor_status(True)
        cursor_before = _cursor_position()
        trace: dict[str, Any] = {
            "schema": "arduino_movement_diagnostic_step.v1",
            "requestedDelta": {"dx": int(dx), "dy": int(dy)},
            "cursorBefore": cursor_before,
            "cursorAfter": None,
            "windowsCursorDelta": None,
            "monitorBefore": before_status,
            "monitorAfter": None,
            "inputIntegrityDelta": None,
            "serialAckOk": False,
            "commandTrace": None,
            "settleTimeMs": None,
            "pollCount": 0,
            "firstCursorDeltaTimeMs": None,
            "firstRawInputDeltaTimeMs": None,
            "classification": None,
            "warnings": [],
        }
        try:
            move = getattr(backend, "diagnostic_move_relative", None)
            if callable(move):
                command_trace = move(int(dx), int(dy))
            else:
                backend.move_relative(int(dx), int(dy), duration_ms=0)
                command_trace = {
                    "schema": "arduino_move_command_trace.v1",
                    "commandSent": f"MOVE {int(dx)} {int(dy)}",
                    "firmwareAck": None,
                    "dx": int(dx),
                    "dy": int(dy),
                    "ackOk": True,
                }
            trace["commandTrace"] = command_trace
            trace["serialAckOk"] = bool(command_trace.get("ackOk", True))
        except Exception as error:  # noqa: BLE001
            trace["serialAckOk"] = False
            trace["warnings"].append(f"{type(error).__name__}: {error}")
        poll_ms = max(1, int(getattr(args, "arduino_move_poll_ms", 10) or 10))
        timeout_ms = max(60, int(getattr(args, "arduino_move_noeffect_timeout_ms", 200) or 200))
        max_polls = max(1, int((timeout_ms + poll_ms - 1) / poll_ms) + 1)
        cursor_after = cursor_before
        after_status = before_status
        delta = {}
        for poll_index in range(max_polls):
            if poll_index > 0:
                time.sleep(poll_ms / 1000.0)
            elapsed_ms = poll_index * poll_ms
            cursor_after = _cursor_position()
            after_status = monitor_status(bool(getattr(backend, "armed", False)))
            delta = input_integrity_delta(before_status, after_status)
            cursor_delta = _cursor_delta(cursor_before, cursor_after)
            raw_seen = bool(
                int(delta.get("rawInputMouseCountDelta") or 0) > 0
                or int(delta.get("rawInputMouseDxDelta") or 0) != 0
                or int(delta.get("rawInputMouseDyDelta") or 0) != 0
            )
            cursor_seen = bool(int(cursor_delta.get("dx") or 0) or int(cursor_delta.get("dy") or 0))
            trace["pollCount"] = poll_index + 1
            if cursor_seen and trace["firstCursorDeltaTimeMs"] is None:
                trace["firstCursorDeltaTimeMs"] = elapsed_ms
            if raw_seen and trace["firstRawInputDeltaTimeMs"] is None:
                trace["firstRawInputDeltaTimeMs"] = elapsed_ms
            if cursor_seen:
                break
        trace["settleTimeMs"] = int((trace.get("firstCursorDeltaTimeMs") if trace.get("firstCursorDeltaTimeMs") is not None else trace.get("firstRawInputDeltaTimeMs") if trace.get("firstRawInputDeltaTimeMs") is not None else timeout_ms) or 0)
        trace["cursorAfter"] = cursor_after
        trace["windowsCursorDelta"] = _cursor_delta(cursor_before, cursor_after)
        trace["monitorAfter"] = after_status
        trace["inputIntegrityDelta"] = delta
        trace["classification"] = _movement_step_classification(trace)
        return trace

    try:
        monitor.start()
        time.sleep(0.20)
        payload["stopAllBefore"] = backend.stop_all()
        payload["firmwareStatusBefore"] = backend.firmware_status()
        payload["monitorBefore"] = monitor_status(False)
        payload["vmwareMouseObservations"] = _vmware_mouse_observations(args)
        backend.arm(args.arduino_session_token if args.arduino_session_token != "auto" else None)
        _write_selftest_backend_status(args, backend, armed=True)
        payload["firmwareStatusArmed"] = backend.firmware_status()
        if not bool((payload["firmwareStatusArmed"] or {}).get("armed")):
            payload["classification"] = "firmware_disarmed"
            payload["status"] = "FAIL"
            return payload
        sequence: list[tuple[int, int]] = []
        for multiplier in (1, 2, 3):
            step = min(20, max(1, delta_px * multiplier))
            sequence.extend([(step, 0), (-step, 0), (0, step), (0, -step)])
        payload["diagnosticSequence"] = [{"dx": dx, "dy": dy} for dx, dy in sequence]
        for dx, dy in sequence:
            payload["movementSteps"].append(run_step(dx, dy))
            if not payload["movementSteps"][-1].get("serialAckOk"):
                break
        classification, causes = _movement_diagnostic_classification(payload["movementSteps"], payload.get("monitorBefore"))
        reliability, reliability_causes = _movement_reliability_classification(payload["movementSteps"])
        payload["classification"] = classification
        payload["reliabilityClassification"] = reliability
        payload["possibleCauses"] = list(dict.fromkeys([*causes, *reliability_causes]))
        if classification != "serial_ok_rawinput_ok_cursor_ok":
            payload["status"] = "FAIL"
        if reliability in {"intermittent_no_effect", "rawinput_ok_cursor_no_move", "no_rawinput", "focus_issue_possible"}:
            payload["status"] = "FAIL"
    except Exception as error:  # noqa: BLE001
        payload["status"] = "FAIL"
        payload["classification"] = payload.get("classification") or "serial_fail"
        payload["warnings"].append(f"{type(error).__name__}: {error}")
    finally:
        try:
            backend.stop_all()
            payload["stopAllFinal"] = True
        except Exception as error:  # noqa: BLE001
            payload["warnings"].append(f"stop_all failed: {type(error).__name__}: {error}")
        try:
            backend.disarm()
            payload["disarmed"] = True
        except Exception as error:  # noqa: BLE001
            payload["warnings"].append(f"disarm failed: {type(error).__name__}: {error}")
        try:
            _write_selftest_backend_status(args, backend, armed=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            payload["firmwareStatusAfter"] = backend.firmware_status()
        except Exception as error:  # noqa: BLE001
            payload["warnings"].append(f"status_after failed: {type(error).__name__}: {error}")
        try:
            payload["monitorAfter"] = monitor_status(False)
        except Exception as error:  # noqa: BLE001
            payload["warnings"].append(f"monitor_after failed: {type(error).__name__}: {error}")
        monitor.stop()
    status_after = payload.get("firmwareStatusAfter") if isinstance(payload.get("firmwareStatusAfter"), dict) else {}
    if status_after and (status_after.get("armed") or int(status_after.get("keysDown") or 0) != 0 or int(status_after.get("mouseButtonsDown") or 0) != 0):
        payload["status"] = "FAIL"
        payload["warnings"].append("firmware_status_not_safe_after_movement_diagnostics")
    return payload


def _input_integrity_status_payload(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    nested = result.get("inputIntegrityStatus")
    return nested if isinstance(nested, dict) else result


_OVERLAY_PROCESSES: dict[int, subprocess.Popen[Any]] = {}


def _foreground_window_info() -> dict[str, Any]:
    if sys.platform != "win32":
        return {"available": False}
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return {"available": True, "hwnd": int(hwnd), "title": buffer.value, "pid": int(pid.value)}
    except Exception as error:  # noqa: BLE001
        return {"available": False, "error": f"{type(error).__name__}: {error}"}


def _cursor_position() -> dict[str, int]:
    if sys.platform != "win32":
        return {"x": 0, "y": 0}
    try:
        point = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):  # type: ignore[attr-defined]
            return {"x": int(point.x), "y": int(point.y)}
    except Exception:  # noqa: BLE001
        pass
    return {"x": 0, "y": 0}


def _screen_size() -> dict[str, int]:
    if sys.platform != "win32":
        return {"width": 1024, "height": 768}
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        return {"width": int(user32.GetSystemMetrics(0)), "height": int(user32.GetSystemMetrics(1))}
    except Exception:  # noqa: BLE001
        return {"width": 1024, "height": 768}


def _enum_windows_matching(title_token: str) -> int | None:
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    matches: list[int] = []
    CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: Any, _lparam: Any) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if title_token.lower() in buffer.value.lower():
            matches.append(int(hwnd))
            return False
        return True

    user32.EnumWindows(CALLBACK(callback), 0)
    return matches[0] if matches else None


def _window_info_matching(title_token: str) -> dict[str, Any] | None:
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    matches: list[dict[str, Any]] = []
    CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    def callback(hwnd: Any, _lparam: Any) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if title_token.lower() not in title.lower():
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        matches.append(
            {
                "title": title,
                "pid": int(pid.value),
                "hwnd": int(hwnd),
                "bounds": {
                    "x": int(rect.left),
                    "y": int(rect.top),
                    "width": max(0, int(rect.right - rect.left)),
                    "height": max(0, int(rect.bottom - rect.top)),
                },
            }
        )
        return False

    user32.EnumWindows(CALLBACK(callback), 0)
    return matches[0] if matches else None


def _inset_region(bounds: dict[str, Any] | None, *, left: int = 0, top: int = 0, right: int = 0, bottom: int = 0) -> dict[str, int] | None:
    if not isinstance(bounds, dict):
        return None
    try:
        x = int(bounds.get("x"))
        y = int(bounds.get("y"))
        width = int(bounds.get("width"))
        height = int(bounds.get("height"))
    except (TypeError, ValueError):
        return None
    region = {
        "x": x + int(left),
        "y": y + int(top),
        "width": max(1, width - int(left) - int(right)),
        "height": max(1, height - int(top) - int(bottom)),
    }
    return region


def _point_inside_region(point: dict[str, Any] | None, region: dict[str, Any] | None) -> bool:
    if not isinstance(point, dict) or not isinstance(region, dict):
        return False
    try:
        x = int(point["x"])
        y = int(point["y"])
        left = int(region["x"])
        top = int(region["y"])
        right = left + int(region["width"])
        bottom = top + int(region["height"])
    except (KeyError, TypeError, ValueError):
        return False
    return left <= x <= right and top <= y <= bottom


def _expand_region(region: dict[str, Any] | None, inflate_px: int, *, screen: dict[str, Any] | None = None) -> dict[str, int] | None:
    if not isinstance(region, dict):
        return None
    try:
        inflate = max(0, int(inflate_px or 0))
        left = int(region["x"]) - inflate
        top = int(region["y"]) - inflate
        right = int(region["x"]) + int(region["width"]) + inflate
        bottom = int(region["y"]) + int(region["height"]) + inflate
    except (KeyError, TypeError, ValueError):
        return None
    if isinstance(screen, dict):
        try:
            left = max(0, left)
            top = max(0, top)
            right = min(int(screen["width"]), right)
            bottom = min(int(screen["height"]), bottom)
        except (KeyError, TypeError, ValueError):
            pass
    width = max(1, right - left)
    height = max(1, bottom - top)
    return {"x": left, "y": top, "width": width, "height": height}


def _nearest_point_inside_region(point: dict[str, Any] | None, region: dict[str, Any] | None, *, margin_px: int = 1) -> dict[str, int] | None:
    if not isinstance(point, dict) or not isinstance(region, dict):
        return None
    try:
        margin = max(0, int(margin_px or 0))
        left = int(region["x"]) + margin
        top = int(region["y"]) + margin
        right = int(region["x"]) + int(region["width"]) - margin
        bottom = int(region["y"]) + int(region["height"]) - margin
        if right < left:
            right = left
        if bottom < top:
            bottom = top
        return {"x": min(max(int(point["x"]), left), right), "y": min(max(int(point["y"]), top), bottom)}
    except (KeyError, TypeError, ValueError):
        return None


def _point_distance_px(a: dict[str, Any] | None, b: dict[str, Any] | None) -> int | None:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    try:
        return max(abs(int(a["x"]) - int(b["x"])), abs(int(a["y"]) - int(b["y"])))
    except (KeyError, TypeError, ValueError):
        return None


def _calibration_window_geometry(args: argparse.Namespace, cursor: dict[str, int], screen: dict[str, int], *, width: int = 560, height: int = 360) -> dict[str, int]:
    screen_width = max(width, int(screen.get("width") or width))
    screen_height = max(height, int(screen.get("height") or height))
    if bool(getattr(args, "calibration_window_center", True)):
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
    else:
        x = int(cursor["x"]) - width // 2
        y = int(cursor["y"]) - height // 2
    margin = 24
    max_x = max(margin, screen_width - width - margin)
    max_y = max(margin, screen_height - height - margin)
    x = max(margin, min(max_x, x))
    y = max(margin, min(max_y, y))
    return {"x": int(x), "y": int(y), "width": int(width), "height": int(height)}


def _region_points(region: dict[str, Any]) -> list[dict[str, int]]:
    x = int(region["x"])
    y = int(region["y"])
    width = int(region["width"])
    height = int(region["height"])
    cx = x + width // 2
    cy = y + height // 2
    return [
        {"x": cx, "y": cy},
        {"x": x + max(12, width // 4), "y": cy},
        {"x": x + min(width - 12, (width * 3) // 4), "y": cy},
        {"x": cx, "y": y + max(12, height // 4)},
        {"x": cx, "y": y + min(height - 12, (height * 3) // 4)},
    ]


def _restore_post_test_focus(target: str | None, *, window_title_filter: str = "RuneLite") -> dict[str, Any]:
    target = str(target or "none").strip().lower()
    result: dict[str, Any] = {
        "schema": "post_test_focus_recovery.v1",
        "target": target,
        "attempted": target != "none",
        "status": "PASS" if target == "none" else "WARN",
        "before": _foreground_window_info(),
        "after": None,
        "reason": "not_requested" if target == "none" else "unknown",
    }
    if target == "none" or sys.platform != "win32":
        result["after"] = _foreground_window_info()
        if sys.platform != "win32":
            result["reason"] = "windows_focus_api_unavailable"
        return result
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        hwnd = None
        if target == "powershell":
            hwnd = int(kernel32.GetConsoleWindow())
        elif target == "runelite":
            hwnd = _enum_windows_matching(window_title_filter or "RuneLite")
        elif target == "desktop":
            hwnd = int(user32.GetShellWindow())
        if not hwnd:
            result["reason"] = "target_window_not_found"
        else:
            SW_RESTORE = 9
            try:
                user32.ShowWindow(hwnd, SW_RESTORE)
            except Exception:  # noqa: BLE001
                pass
            ok = bool(user32.SetForegroundWindow(hwnd))
            time.sleep(0.25)
            result["status"] = "PASS" if ok else "WARN"
            result["reason"] = "foreground_restored" if ok else "set_foreground_returned_false"
            result["hwnd"] = int(hwnd)
    except Exception as error:  # noqa: BLE001
        result["status"] = "WARN"
        result["reason"] = f"{type(error).__name__}: {error}"
    result["after"] = _foreground_window_info()
    return result


def _start_input_integrity_overlay(args: argparse.Namespace) -> dict[str, Any]:
    status_path = args.arduino_monitor_status_path or str(Path("interaction_geometry") / "live" / "input_integrity_status.json")
    args.arduino_monitor_status_path = status_path
    script = Path(__file__).resolve().parent / "input_control" / "arduino_monitor.py"
    started_ms = int(time.time() * 1000)
    command = [
        sys.executable,
        str(script),
        "--show-overlay",
        "--status-output",
        status_path,
        "--corner",
        args.input_integrity_overlay_corner,
        "--vid",
        args.arduino_vid,
        "--pid",
        args.arduino_pid,
        "--live-backend",
        "arduino",
        "--poll-ms",
        "250",
    ]
    if getattr(args, "input_integrity_overlay_passive", True):
        command.append("--overlay-passive")
    if getattr(args, "input_integrity_overlay_no_focus", True):
        command.append("--overlay-no-focus")
    if args.arduino_port:
        command.extend(["--com-port", args.arduino_port])
    try:
        process = subprocess.Popen(command, cwd=str(Path(__file__).resolve().parents[1]))
        _OVERLAY_PROCESSES[int(process.pid)] = process
        fresh = False
        status_file = Path(status_path)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if process.poll() is not None:
                return {
                    "started": False,
                    "pid": process.pid,
                    "error": f"overlay process exited code {process.returncode}",
                    "statusPath": status_path,
                }
            try:
                decoded = json.loads(status_file.read_text(encoding="utf-8"))
                generated = int(decoded.get("generatedAtMillis") or 0) if isinstance(decoded, dict) else 0
                if generated >= started_ms:
                    fresh = True
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.1)
        return {
            "started": True,
            "pid": process.pid,
            "statusPath": status_path,
            "passive": bool(getattr(args, "input_integrity_overlay_passive", True)),
            "noFocus": bool(getattr(args, "input_integrity_overlay_no_focus", True)),
            "statusFresh": fresh,
        }
    except Exception as error:  # noqa: BLE001
        return {"started": False, "error": f"{type(error).__name__}: {error}", "statusPath": status_path}


def _stop_input_integrity_overlay(overlay: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(overlay, dict) or not overlay.get("started"):
        return {"attempted": False, "status": "not_started"}
    pid = overlay.get("pid")
    process = _OVERLAY_PROCESSES.pop(int(pid), None) if pid is not None else None
    if process is None:
        return {"attempted": True, "status": "unknown_process", "pid": pid}
    try:
        process.terminate()
        process.wait(timeout=1.0)
        return {"attempted": True, "status": "closed", "pid": pid}
    except Exception as error:  # noqa: BLE001
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass
        return {"attempted": True, "status": "forced", "pid": pid, "warning": f"{type(error).__name__}: {error}"}


def _write_selftest_backend_status(args: argparse.Namespace, backend: Any, *, armed: bool) -> None:
    try:
        path = Path("interaction_geometry") / "live" / "arduino_backend_status.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        status = backend.status() if callable(getattr(backend, "status", None)) else {}
        payload = {
            "schema": "arduino_backend_runtime_status.v1",
            "generatedAtMillis": int(time.time() * 1000),
            "liveInputBackend": getattr(backend, "name", backend.__class__.__name__),
            "arduinoArmed": bool(armed),
            "directBackendBypassCount": 0,
            "backendStatus": status if isinstance(status, dict) else None,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _write_selftest_integrity_status(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    try:
        path = Path(args.arduino_monitor_status_path or Path("interaction_geometry") / "live" / "input_integrity_status.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        monitor_after = _input_integrity_status_payload(payload.get("monitorAfter"))
        status = dict(monitor_after) if monitor_after else {
            "schema": "input_integrity_status.v1",
            "status": payload.get("status") or "WARN",
            "generatedAtMillis": int(time.time() * 1000),
        }
        status["firmwareSafety"] = payload.get("firmwareSafety")
        status["vmInputFocusSafety"] = payload.get("vmInputFocusSafety")
        status["postTestRecoveryCheck"] = payload.get("postTestRecoveryCheck")
        path.write_text(json.dumps(status, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        payload["finalInputIntegrityStatusPath"] = str(path)
    except Exception as error:  # noqa: BLE001
        payload.setdefault("warnings", []).append(f"final status write failed: {type(error).__name__}: {error}")


def _prompt_user_control_confirmation() -> dict[str, Any]:
    message = (
        "Arduino firmware is safe. Press Ctrl+Alt to release VMware input if needed. "
        "Confirm the VM accepts normal mouse clicks, then type YES to continue: "
    )
    try:
        answer = input(message)
    except Exception as error:  # noqa: BLE001
        return {"required": True, "confirmed": False, "status": "FAIL", "reason": f"{type(error).__name__}: {error}"}
    confirmed = str(answer or "").strip().upper() == "YES"
    return {
        "required": True,
        "confirmed": confirmed,
        "status": "PASS" if confirmed else "FAIL",
        "reason": "user_confirmed" if confirmed else "user_did_not_type_yes",
    }


def _powershell_json(script: str, *, timeout: float = 4.0) -> Any:
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        return json.loads(completed.stdout)
    except Exception:  # noqa: BLE001
        return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def run_arduino_usb_diagnostics(args: argparse.Namespace) -> dict[str, Any]:
    from input_control.input_integrity import build_vmware_autoconnect_recommendation, extract_usb_vid_pid, normalize_usb_token

    vid = normalize_usb_token(getattr(args, "arduino_vid", None) or "VID_2341", "VID")
    pid = normalize_usb_token(getattr(args, "arduino_pid", None), "PID") if getattr(args, "arduino_pid", None) else None
    boot_vid = normalize_usb_token(getattr(args, "arduino_bootloader_vid", None) or getattr(args, "arduino_vid", None) or "VID_2341", "VID")
    boot_pid = normalize_usb_token(getattr(args, "arduino_bootloader_pid", None), "PID") if getattr(args, "arduino_bootloader_pid", None) else None
    token = (vid or "VID_2341").replace("_", "[_&]")
    pnp_script = (
        "$devices = Get-PnpDevice -PresentOnly | "
        f"Where-Object {{ $_.InstanceId -match '{token}' -or $_.FriendlyName -match 'Arduino|Leonardo' }} | "
        "Select-Object Class,FriendlyName,InstanceId,Status; "
        "$devices | ConvertTo-Json -Depth 5"
    )
    pnp_devices = [item for item in _as_list(_powershell_json(pnp_script)) if isinstance(item, dict)]
    boards: list[dict[str, Any]] = []
    try:
        completed = subprocess.run(["arduino-cli", "board", "list", "--format", "json"], capture_output=True, text=True, timeout=4.0)
        if completed.returncode == 0 and completed.stdout.strip():
            decoded = json.loads(completed.stdout)
            boards = [item for item in _as_list(decoded) if isinstance(item, dict)]
    except Exception:  # noqa: BLE001
        boards = []
    discovered = []
    for device in pnp_devices:
        ids = extract_usb_vid_pid(str(device.get("InstanceId") or ""))
        row = dict(device)
        row["vid"] = ids.get("vid")
        row["pid"] = ids.get("pid")
        discovered.append(row)
        if ids.get("vid") == vid and not pid and ids.get("pid"):
            pid = ids.get("pid")
        if ids.get("vid") == boot_vid and ids.get("pid") and ids.get("pid") != pid and not boot_pid:
            boot_pid = ids.get("pid")
    recommendation = build_vmware_autoconnect_recommendation(
        sketch_vid=vid,
        sketch_pid=pid,
        bootloader_vid=boot_vid,
        bootloader_pid=boot_pid,
        broad_vid_ok=False,
    )
    warnings = list(recommendation.get("warnings") or [])
    if boot_pid is None:
        warnings.append("bootloader_pid_not_visible_until_reset_or_upload")
    if len({str(item.get("pid")) for item in discovered if item.get("vid") == vid and item.get("pid")}) > 1:
        warnings.append("multiple_arduino_like_pids_visible")
    return {
        "schema": "arduino_usb_passthrough_diagnostics.v1",
        "status": "PASS" if discovered else "WARN",
        "normalComPort": getattr(args, "arduino_port", None),
        "bootloaderComPort": getattr(args, "arduino_bootloader_port", None),
        "normalSketchVidPid": {"vid": vid, "pid": pid},
        "bootloaderVidPid": {"vid": boot_vid, "pid": boot_pid},
        "guestPnpArduinoDevices": discovered,
        "arduinoCliBoards": boards,
        "resetOrUploadPerformed": False,
        "vmwarePromptCanBeClickedFromGuest": False,
        "autoconnectRecommendation": recommendation,
        "warnings": list(dict.fromkeys(warnings)),
    }


def run_input_integrity_self_test(args: argparse.Namespace, backend: Any) -> dict[str, Any]:
    from input_control.backend_arduino_hid import check_arduino_monitor_status
    from input_control.input_integrity import build_firmware_safety, build_vm_input_focus_safety, input_integrity_delta

    overlay = (
        _start_input_integrity_overlay(args)
        if bool(getattr(args, "show_input_integrity_overlay", False)) and not bool(getattr(args, "no_overlay", False))
        else {"started": False, "statusPath": args.arduino_monitor_status_path, "passive": False, "noFocus": None}
    )
    no_move = bool(getattr(args, "input_integrity_self_test_no_move", False))
    payload: dict[str, Any] = {
        "schema": "input_integrity_self_test.v1",
        "status": "PASS",
        "testMode": "no_move" if no_move else "tiny_move",
        "backend": getattr(backend, "name", backend.__class__.__name__),
        "arduinoPort": args.arduino_port,
        "overlay": overlay,
        "overlayPassive": bool(overlay.get("passive")),
        "overlayNoFocus": bool(overlay.get("noFocus")) if overlay.get("noFocus") is not None else None,
        "monitorBefore": None,
        "monitorAfter": None,
        "inputIntegrityDelta": None,
        "stopAllBefore": None,
        "ping": None,
        "identify": None,
        "caps": None,
        "firmwareStatusBefore": None,
        "firmwareStatusAfter": None,
        "firmwareSafety": None,
        "vmInputFocusSafety": None,
        "postTestFocusRecovery": None,
        "postTestRecoveryCheck": "UNKNOWN",
        "userControlConfirmation": {"required": bool(getattr(args, "require_user_control_confirmation", False)), "confirmed": None},
        "continuationAllowed": False,
        "resetOrUploadPerformed": False,
        "armed": False,
        "disarmed": False,
        "tinyMoveSent": False,
        "noMoveSent": no_move,
        "backendStatus": backend.status() if callable(getattr(backend, "status", None)) else None,
        "warnings": [],
    }

    def read_monitor(require_armed: bool = False, bypass_count: int = 0, max_event_age_ms: int | None = None) -> dict[str, Any]:
        return check_arduino_monitor_status(
            require_monitor=args.arduino_require_monitor,
            status_path=args.arduino_monitor_status_path,
            expected_vid=args.arduino_vid,
            expected_pid=args.arduino_pid,
            expected_com_port=args.arduino_port,
            live_input_backend="arduino",
            arduino_armed=bool(getattr(backend, "armed", False)),
            software_input_allowed=False,
            direct_backend_bypass_count=bypass_count,
            fail_on_injected=args.input_integrity_fail_on_injected,
            fail_on_bypass=args.input_integrity_fail_on_bypass,
            require_armed=require_armed,
            max_event_age_ms=int(max_event_age_ms if max_event_age_ms is not None else args.arduino_monitor_max_age_ms),
        )

    armed = False
    armed_once = False
    try:
        payload["stopAllBefore"] = backend.stop_all()
        payload["ping"] = backend.ping()
        payload["identify"] = backend.identify()
        payload["caps"] = backend.capabilities()
        status_before = backend.firmware_status()
        payload["firmwareStatusBefore"] = status_before
        if status_before.get("armed") or int(status_before.get("keysDown") or 0) != 0 or int(status_before.get("mouseButtonsDown") or 0) != 0:
            raise RuntimeError("firmware_status_not_safe_before_self_test")
        before = read_monitor(require_armed=False, max_event_age_ms=86_400_000)
        payload["monitorBefore"] = before
        if args.arduino_require_monitor and not bool(before.get("monitorPass")):
            raise RuntimeError(str(before.get("monitorBlockReason") or "input_integrity_monitor_failed"))
        backend.arm(args.arduino_session_token if args.arduino_session_token != "auto" else None)
        armed = True
        armed_once = True
        payload["armed"] = True
        _write_selftest_backend_status(args, backend, armed=True)
        armed_monitor = read_monitor(require_armed=True, max_event_age_ms=86_400_000)
        payload["monitorArmed"] = armed_monitor
        if args.arduino_require_monitor and not bool(armed_monitor.get("monitorPass")):
            payload["status"] = "FAIL"
            raise RuntimeError(str(armed_monitor.get("monitorBlockReason") or "input_integrity_monitor_failed_after_arm"))
        if no_move:
            payload["noMoveSent"] = True
        else:
            backend.move_relative(6, 0, duration_ms=20)
            backend.move_relative(-6, 0, duration_ms=20)
            payload["tinyMoveSent"] = True
            time.sleep(max(0.35, min(0.75, float(args.arduino_monitor_max_age_ms or 3000) / 4000.0)))
        payload["stopAllAfterMove"] = backend.stop_all()
        armed = False
    except Exception as error:  # noqa: BLE001
        payload["status"] = "FAIL"
        payload["warnings"].append(f"{type(error).__name__}: {error}")
    finally:
        try:
            backend.stop_all()
            payload["stopAllFinal"] = True
        except Exception as error:  # noqa: BLE001
            payload["warnings"].append(f"stop_all failed: {type(error).__name__}: {error}")
        if armed_once or bool(getattr(backend, "armed", False)):
            try:
                backend.disarm()
                payload["disarmed"] = True
                _write_selftest_backend_status(args, backend, armed=False)
            except Exception as error:  # noqa: BLE001
                payload["status"] = "FAIL"
                payload["warnings"].append(f"disarm failed: {type(error).__name__}: {error}")
        try:
            payload["firmwareStatusAfter"] = backend.firmware_status()
        except Exception as error:  # noqa: BLE001
            payload["warnings"].append(f"status_after failed: {type(error).__name__}: {error}")
        if payload.get("tinyMoveSent"):
            time.sleep(max(1.0, min(2.0, float(args.arduino_monitor_max_age_ms or 3000) / 1500.0)))
        after = read_monitor(require_armed=False)
        payload["monitorAfter"] = after
        payload["backendStatus"] = backend.status() if callable(getattr(backend, "status", None)) else payload.get("backendStatus")
        _write_selftest_backend_status(args, backend, armed=False)
        if bool(getattr(args, "close_overlay_after_test", True)):
            payload["overlayClose"] = _stop_input_integrity_overlay(overlay)
        focus = _restore_post_test_focus(getattr(args, "post_test_focus_target", "powershell"), window_title_filter=getattr(args, "window_title_filter", "RuneLite"))
        payload["postTestFocusRecovery"] = focus
        focus_state = "normal" if focus.get("status") == "PASS" else "unknown"
        vm_focus_raw = {
            "vmInputFocusSafety": {
                "status": "PASS" if focus.get("status") == "PASS" else "WARN",
                "overlayFocusable": not bool(payload.get("overlayNoFocus")),
                "overlayClickThrough": bool(payload.get("overlayPassive")),
                "overlayTopmost": bool(overlay.get("started")),
                "monitorWindowActive": False,
                "foregroundWindowTitle": (focus.get("after") or {}).get("title") if isinstance(focus.get("after"), dict) else None,
                "foregroundProcess": (focus.get("after") or {}).get("pid") if isinstance(focus.get("after"), dict) else None,
                "postTestFocusTarget": getattr(args, "post_test_focus_target", "powershell"),
                "postTestFocusRecovery": focus.get("status"),
                "postTestInputState": focus_state,
                "warnings": [] if focus.get("status") == "PASS" else [str(focus.get("reason") or "focus_recovery_unknown")],
            }
        }
        payload["vmInputFocusSafety"] = build_vm_input_focus_safety(vm_focus_raw)
    before_status = _input_integrity_status_payload(payload.get("monitorBefore"))
    after_status = _input_integrity_status_payload(payload.get("monitorAfter"))
    delta = input_integrity_delta(before_status, after_status)
    payload["inputIntegrityDelta"] = delta
    injected_delta = int(delta.get("mouseInjectedCountDelta") or 0) + int(delta.get("keyboardInjectedCountDelta") or 0) + int(delta.get("lowerIlInjectedCountDelta") or 0)
    if args.input_integrity_fail_on_injected and injected_delta > 0:
        payload["status"] = "FAIL"
        payload["warnings"].append("injected_input_detected")
    if args.arduino_require_monitor and payload.get("tinyMoveSent") and int(delta.get("rawInputMouseCountDelta") or 0) <= 0:
        payload["status"] = "FAIL"
        payload["warnings"].append("arduino_raw_mouse_event_not_observed")
    status_after = payload.get("firmwareStatusAfter") if isinstance(payload.get("firmwareStatusAfter"), dict) else {}
    identity = payload.get("identify") if isinstance(payload.get("identify"), dict) else {}
    caps = payload.get("caps") if isinstance(payload.get("caps"), dict) else {}
    payload["firmwareSafety"] = build_firmware_safety(
        {
            "status": "OK" if status_after else "UNKNOWN",
            "protocol": identity.get("protocol"),
            "resetSafe": caps.get("resetSafe"),
            "stopAll": caps.get("stopAll"),
            "watchdog": caps.get("watchdog"),
            "watchdogMs": status_after.get("watchdogMs"),
            "armed": status_after.get("armed"),
            "keysDown": status_after.get("keysDown"),
            "mouseButtonsDown": status_after.get("mouseButtonsDown"),
        }
        if status_after
        else {}
    )
    if status_after and (status_after.get("armed") or int(status_after.get("keysDown") or 0) != 0 or int(status_after.get("mouseButtonsDown") or 0) != 0):
        payload["status"] = "FAIL"
        payload["warnings"].append("firmware_status_not_safe_after_self_test")
    vm_focus = payload.get("vmInputFocusSafety") if isinstance(payload.get("vmInputFocusSafety"), dict) else {}
    if vm_focus.get("status") == "WARN" and payload.get("status") == "PASS":
        payload["status"] = "WARN"
        payload["warnings"].append("post_test_focus_recovery_not_confirmed")
    if vm_focus.get("status") == "FAIL":
        payload["status"] = "FAIL"
        payload["warnings"].append("post_test_focus_recovery_failed")
    if bool(getattr(args, "require_user_control_confirmation", False)):
        confirmation = _prompt_user_control_confirmation()
        payload["userControlConfirmation"] = confirmation
        if not confirmation.get("confirmed"):
            if payload.get("status") == "PASS":
                payload["status"] = "WARN"
            payload["warnings"].append("user_control_confirmation_missing")
    else:
        payload["userControlConfirmation"] = {"required": False, "confirmed": None, "status": "SKIPPED"}
    payload["continuationAllowed"] = bool(payload.get("status") == "PASS" and not bool(getattr(args, "require_user_control_confirmation", False))) or bool(
        isinstance(payload.get("userControlConfirmation"), dict) and payload["userControlConfirmation"].get("confirmed")
    )
    payload["postTestRecoveryCheck"] = "PASS" if payload.get("status") == "PASS" else "FAIL" if payload.get("status") == "FAIL" else "WARN"
    _write_selftest_integrity_status(args, payload)
    return payload


def _calibration_window_context(args: argparse.Namespace) -> tuple[Any | None, dict[str, Any], dict[str, int], list[str]]:
    if getattr(args, "allowed_window", "runelite") == "runelite":
        window = _window_info_matching(getattr(args, "window_title_filter", "RuneLite") or "RuneLite")
        if window and isinstance(window.get("bounds"), dict):
            bounds = window["bounds"]
            # Keep well away from title bars, resize borders, side panels, and taskbar.
            region = _inset_region(bounds, left=80, top=120, right=80, bottom=100)
            if region:
                return None, {"type": "runelite", "window": window, "fallbackCalibrationWindow": False}, region, ["RuneLite"]
    try:
        import tkinter as tk
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"calibration_window_unavailable: {type(error).__name__}: {error}") from error
    cursor = _cursor_position()
    screen = _screen_size()
    geometry = _calibration_window_geometry(args, cursor, screen)
    width = int(geometry["width"])
    height = int(geometry["height"])
    x = int(geometry["x"])
    y = int(geometry["y"])
    root = tk.Tk()
    root.title("Arduino Cursor Calibration")
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    label = tk.Label(
        root,
        text="Arduino cursor calibration\nNo clicks or keys are sent",
        font=("Segoe UI", 13),
        padx=24,
        pady=24,
    )
    label.pack(expand=True, fill="both")
    root.update_idletasks()
    root.update()
    try:
        root.focus_force()
        root.lift()
        root.update()
    except Exception:  # noqa: BLE001
        pass
    bounds = {
        "x": int(root.winfo_rootx()),
        "y": int(root.winfo_rooty()),
        "width": int(root.winfo_width()),
        "height": int(root.winfo_height()),
    }
    region = _inset_region(bounds, left=80, top=80, right=80, bottom=80)
    if not region:
        root.destroy()
        raise RuntimeError("calibration_window_bounds_unavailable")
    return root, {"type": "calibration", "window": {"title": "Arduino Cursor Calibration", "bounds": bounds}, "fallbackCalibrationWindow": True}, region, ["Arduino Cursor Calibration"]


def _arduino_move_options(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "move_settle_ms": max(0, int(getattr(args, "arduino_move_settle_ms", 80) or 0)),
        "move_poll_ms": max(1, int(getattr(args, "arduino_move_poll_ms", 10) or 10)),
        "move_noeffect_timeout_ms": max(1, int(getattr(args, "arduino_move_noeffect_timeout_ms", 200) or 200)),
        "move_noeffect_retries": max(0, int(getattr(args, "arduino_move_noeffect_retries", 2) or 0)),
        "move_min_effective_px": max(1, int(getattr(args, "arduino_min_effective_move_px", 2) or 2)),
        "move_retry_scale": max(0.25, min(3.0, float(getattr(args, "arduino_retry_scale", 1.25) or 1.25))),
        "move_max_consecutive_noeffect": max(1, int(getattr(args, "arduino_move_max_consecutive_noeffect", 3) or 3)),
    }


def _calibration_movement_metrics(traces: list[Any]) -> dict[str, Any]:
    metrics = {
        "schema": "arduino_pointer_calibration_metrics.v1",
        "totalChunks": 0,
        "successfulChunks": 0,
        "retryChunks": 0,
        "noEffectChunks": 0,
        "consecutiveNoEffectChunks": 0,
        "maxConsecutiveNoEffectChunks": 0,
        "movementSuccessRate": 1.0,
        "maxPositionErrorPx": None,
        "finalPositionErrorPx": None,
        "classifications": {},
    }
    for trace in traces:
        if not isinstance(trace, dict):
            continue
        chunks = trace.get("movementChunks") if isinstance(trace.get("movementChunks"), list) else []
        trace_total = int(trace.get("totalChunks") or trace.get("chunkCount") or len(chunks) or 0)
        trace_success = int(trace.get("successfulChunks") or (trace_total if trace.get("status") == "PASS" and "successfulChunks" not in trace else 0))
        metrics["totalChunks"] += trace_total
        metrics["successfulChunks"] += trace_success
        metrics["retryChunks"] += int(trace.get("retryChunks") or 0)
        metrics["noEffectChunks"] += int(trace.get("noEffectChunks") or 0)
        metrics["consecutiveNoEffectChunks"] = int(trace.get("consecutiveNoEffectChunks") or metrics["consecutiveNoEffectChunks"] or 0)
        metrics["maxConsecutiveNoEffectChunks"] = max(
            int(metrics["maxConsecutiveNoEffectChunks"] or 0),
            int(trace.get("maxConsecutiveNoEffectChunks") or 0),
        )
        final_error = trace.get("finalPositionErrorPx", trace.get("positionErrorPx"))
        max_error = trace.get("maxPositionErrorPx", final_error)
        if max_error is not None:
            metrics["maxPositionErrorPx"] = max(int(metrics["maxPositionErrorPx"] or 0), int(max_error or 0))
        if final_error is not None:
            metrics["finalPositionErrorPx"] = int(final_error or 0)
        for chunk in trace.get("movementChunks") or []:
            if not isinstance(chunk, dict):
                continue
            classification = str(chunk.get("classification") or "unknown")
            classifications = metrics["classifications"]
            classifications[classification] = int(classifications.get(classification) or 0) + 1
    total = int(metrics["totalChunks"] or 0)
    metrics["movementSuccessRate"] = (float(metrics["successfulChunks"]) / float(total)) if total else 1.0
    return metrics


def _input_integrity_has_blocking_counts(monitor_payload: dict[str, Any] | None) -> tuple[bool, list[str]]:
    if not isinstance(monitor_payload, dict):
        return False, []
    integrity = monitor_payload.get("inputIntegrityStatus") if isinstance(monitor_payload.get("inputIntegrityStatus"), dict) else monitor_payload
    flags = integrity.get("injectionFlags") if isinstance(integrity.get("injectionFlags"), dict) else {}
    backend = integrity.get("backend") if isinstance(integrity.get("backend"), dict) else {}
    warnings: list[str] = []
    injected = int(flags.get("mouseInjectedCount") or 0) + int(flags.get("keyboardInjectedCount") or 0)
    lower = int(flags.get("mouseLowerIlInjectedCount") or 0) + int(flags.get("keyboardLowerIlInjectedCount") or 0)
    bypass = int(backend.get("directBackendBypassCount") or integrity.get("directBackendBypassCount") or 0)
    if injected:
        warnings.append("injected_input_detected_after_calibration")
    if lower:
        warnings.append("lower_il_injected_input_detected_after_calibration")
    if bypass:
        warnings.append("direct_backend_bypass_detected_after_calibration")
    return bool(warnings), warnings


POINTER_CALIBRATION_RECORD_SCHEMA = "arduino_pointer_calibration_record.v1"
POINTER_CALIBRATION_MIN_SUCCESS_RATE = 0.80
POINTER_CALIBRATION_MAX_FINAL_ERROR_PX = 6
POINTER_CALIBRATION_MAX_CONSECUTIVE_NOEFFECT = 3


def _safe_path_token(value: Any) -> str:
    text = str(value or "unknown").strip() or "unknown"
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in text)


def _pointer_calibration_path(args: argparse.Namespace) -> Path:
    configured = getattr(args, "arduino_pointer_calibration_path", None)
    if configured:
        return Path(configured)
    port = _safe_path_token(getattr(args, "arduino_port", None) or "unknown").upper()
    return Path("interaction_geometry") / "live" / f"arduino_pointer_calibration_{port}.json"


def _utc_now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _calibration_summary_payload(payload: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "schema",
        "status",
        "backend",
        "arduinoPort",
        "allowedWindow",
        "allowedRegion",
        "expandedStagingRegion",
        "calibrationWindow",
        "runeliteWindow",
        "fallbackCalibrationWindow",
        "targetPoints",
        "movementMetrics",
        "totalChunks",
        "successfulChunks",
        "retryChunks",
        "noEffectChunks",
        "consecutiveNoEffectChunks",
        "movementSuccessRate",
        "maxPositionErrorPx",
        "finalPositionErrorPx",
        "cursorLeftAllowedRegion",
        "clickSent",
        "keySent",
        "directBackendBypassCount",
        "firmwareStatusAfter",
        "monitorAfter",
        "foregroundWindowBefore",
        "foregroundWindowAfter",
        "warnings",
    )
    return {field: payload.get(field) for field in fields if field in payload}


def _build_pointer_calibration_record(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    return {
        "schema": POINTER_CALIBRATION_RECORD_SCHEMA,
        "status": payload.get("status"),
        "writtenAtUtc": _utc_now_text(),
        "writtenAtMillis": now_ms,
        "arduinoPort": payload.get("arduinoPort") or getattr(args, "arduino_port", None),
        "arduinoVid": getattr(args, "arduino_vid", None),
        "arduinoPid": getattr(args, "arduino_pid", None),
        "allowedWindow": payload.get("allowedWindow"),
        "movementMetrics": payload.get("movementMetrics"),
        "totalChunks": payload.get("totalChunks"),
        "successfulChunks": payload.get("successfulChunks"),
        "retryChunks": payload.get("retryChunks"),
        "noEffectChunks": payload.get("noEffectChunks"),
        "consecutiveNoEffectChunks": payload.get("consecutiveNoEffectChunks"),
        "movementSuccessRate": payload.get("movementSuccessRate"),
        "maxPositionErrorPx": payload.get("maxPositionErrorPx"),
        "finalPositionErrorPx": payload.get("finalPositionErrorPx"),
        "clickSent": bool(payload.get("clickSent")),
        "keySent": bool(payload.get("keySent")),
        "directBackendBypassCount": int(payload.get("directBackendBypassCount") or 0),
        "cursorLeftAllowedRegion": bool(payload.get("cursorLeftAllowedRegion")),
        "firmwareStatusAfter": payload.get("firmwareStatusAfter"),
        "monitorAfter": payload.get("monitorAfter"),
        "calibrationPayload": _calibration_summary_payload(payload),
    }


def _pointer_calibration_validation(args: argparse.Namespace, record: dict[str, Any], *, enforce_age: bool) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    if not isinstance(record, dict):
        return {"status": "FAIL", "blockers": ["calibration_record_not_json"], "warnings": warnings}
    if record.get("schema") != POINTER_CALIBRATION_RECORD_SCHEMA:
        blockers.append("calibration_record_schema_mismatch")
    if record.get("status") != "PASS":
        blockers.append("calibration_status_not_pass")
    expected_port = str(getattr(args, "arduino_port", None) or "").strip().upper()
    record_port = str(record.get("arduinoPort") or "").strip().upper()
    if expected_port and record_port and expected_port != record_port:
        blockers.append("calibration_port_mismatch")
    elif expected_port and not record_port:
        blockers.append("calibration_port_missing")
    now_ms = int(time.time() * 1000)
    written_ms = record.get("writtenAtMillis")
    if enforce_age:
        try:
            age_ms = now_ms - int(written_ms)
        except (TypeError, ValueError):
            blockers.append("calibration_written_time_missing")
        else:
            max_age_hours = float(getattr(args, "arduino_pointer_calibration_max_age_hours", 8.0) or 0)
            if max_age_hours > 0 and age_ms > max_age_hours * 60 * 60 * 1000:
                blockers.append("calibration_record_stale")
    total = int(record.get("totalChunks") or 0)
    if total <= 0:
        blockers.append("calibration_no_movement_chunks")
    success_rate = record.get("movementSuccessRate")
    try:
        success_value = float(success_rate)
    except (TypeError, ValueError):
        blockers.append("calibration_success_rate_missing")
    else:
        if success_value < POINTER_CALIBRATION_MIN_SUCCESS_RATE:
            blockers.append("calibration_success_rate_too_low")
    for field in ("finalPositionErrorPx", "maxPositionErrorPx"):
        value = record.get(field)
        if value is None:
            blockers.append(f"{field}_missing")
            continue
        try:
            if int(value) > POINTER_CALIBRATION_MAX_FINAL_ERROR_PX:
                blockers.append(f"{field}_too_large")
        except (TypeError, ValueError):
            blockers.append(f"{field}_invalid")
    if int(record.get("consecutiveNoEffectChunks") or 0) > POINTER_CALIBRATION_MAX_CONSECUTIVE_NOEFFECT:
        blockers.append("calibration_too_many_consecutive_no_effect_chunks")
    if bool(record.get("cursorLeftAllowedRegion")):
        blockers.append("calibration_cursor_left_allowed_region")
    if bool(record.get("clickSent")):
        blockers.append("calibration_sent_click")
    if bool(record.get("keySent")):
        blockers.append("calibration_sent_key")
    if int(record.get("directBackendBypassCount") or 0) != 0:
        blockers.append("calibration_backend_bypass_detected")
    firmware = record.get("firmwareStatusAfter") if isinstance(record.get("firmwareStatusAfter"), dict) else {}
    if firmware:
        if firmware.get("armed") or int(firmware.get("keysDown") or 0) != 0 or int(firmware.get("mouseButtonsDown") or 0) != 0:
            blockers.append("calibration_firmware_not_safe")
    else:
        blockers.append("calibration_firmware_status_missing")
    integrity_blocked, integrity_warnings = _input_integrity_has_blocking_counts(record.get("monitorAfter"))
    if integrity_blocked:
        blockers.extend(integrity_warnings)
    return {
        "status": "PASS" if not blockers else "FAIL",
        "blockers": blockers,
        "warnings": warnings,
        "path": str(_pointer_calibration_path(args)),
        "writtenAtUtc": record.get("writtenAtUtc"),
        "arduinoPort": record.get("arduinoPort"),
        "totalChunks": total,
        "successfulChunks": int(record.get("successfulChunks") or 0),
        "retryChunks": int(record.get("retryChunks") or 0),
        "noEffectChunks": int(record.get("noEffectChunks") or 0),
        "movementSuccessRate": record.get("movementSuccessRate"),
        "maxPositionErrorPx": record.get("maxPositionErrorPx"),
        "finalPositionErrorPx": record.get("finalPositionErrorPx"),
    }


def _persist_pointer_calibration_record(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any]:
    path = _pointer_calibration_path(args)
    record = _build_pointer_calibration_record(args, payload)
    validation = _pointer_calibration_validation(args, record, enforce_age=False)
    payload["calibrationPath"] = str(path)
    payload["calibrationValidation"] = validation
    if payload.get("status") != "PASS" or validation.get("status") != "PASS":
        payload["calibrationPersisted"] = False
        return validation
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        payload["calibrationPersisted"] = True
    except Exception as error:  # noqa: BLE001
        payload["status"] = "FAIL"
        payload["calibrationPersisted"] = False
        payload["warnings"].append(f"calibration_persist_failed: {type(error).__name__}: {error}")
        validation = {**validation, "status": "FAIL", "blockers": [*validation.get("blockers", []), "calibration_persist_failed"]}
        payload["calibrationValidation"] = validation
    return validation


def _load_pointer_calibration_for_live_movement(args: argparse.Namespace) -> dict[str, Any]:
    path = _pointer_calibration_path(args)
    if not path.exists():
        return {"status": "FAIL", "path": str(path), "blockers": ["calibration_record_missing"], "warnings": []}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001
        return {"status": "FAIL", "path": str(path), "blockers": [f"calibration_record_read_failed:{type(error).__name__}"], "warnings": []}
    validation = _pointer_calibration_validation(args, record, enforce_age=True)
    validation["path"] = str(path)
    validation["movementSafety"] = _live_movement_safety_from_calibration(record)
    return validation


def _valid_rect(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    try:
        x = int(value.get("x"))
        y = int(value.get("y"))
        width = int(value.get("width"))
        height = int(value.get("height"))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def _scale_rect_between_bounds(
    rect: dict[str, int],
    *,
    source_bounds: dict[str, int] | None,
    destination_bounds: dict[str, int] | None,
) -> tuple[dict[str, int], dict[str, Any] | None]:
    source = _valid_rect(source_bounds)
    destination = _valid_rect(destination_bounds)
    if source is None or destination is None:
        return rect, None
    scale_x = float(destination["width"]) / max(1.0, float(source["width"]))
    scale_y = float(destination["height"]) / max(1.0, float(source["height"]))
    transformed = {
        "x": int(round(float(destination["x"]) + (float(rect["x"]) - float(source["x"])) * scale_x)),
        "y": int(round(float(destination["y"]) + (float(rect["y"]) - float(source["y"])) * scale_y)),
        "width": max(1, int(round(float(rect["width"]) * scale_x))),
        "height": max(1, int(round(float(rect["height"]) * scale_y))),
    }
    transform = {
        "schema": "runelite_window_region_transform.v1",
        "sourceBounds": dict(source),
        "destinationBounds": dict(destination),
        "scaleX": scale_x,
        "scaleY": scale_y,
        "translated": source["x"] != destination["x"] or source["y"] != destination["y"],
        "scaled": abs(scale_x - 1.0) > 0.01 or abs(scale_y - 1.0) > 0.01,
    }
    return transformed, transform


def _live_movement_safety_from_calibration(record: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    payload = record.get("calibrationPayload") if isinstance(record.get("calibrationPayload"), dict) else {}
    allowed_window = str(record.get("allowedWindow") or payload.get("allowedWindow") or "").strip().lower()
    if allowed_window != "runelite":
        blockers.append("calibration_allowed_window_not_runelite")
    allowed_region = _valid_rect(payload.get("allowedRegion"))
    if allowed_region is None:
        blockers.append("calibration_allowed_region_missing")
    titles: list[str] = []
    runelite_window = payload.get("runeliteWindow") if isinstance(payload.get("runeliteWindow"), dict) else {}
    current_runelite_window = None
    coordinate_transform = None
    source_allowed_region = dict(allowed_region) if isinstance(allowed_region, dict) else None
    title = str(runelite_window.get("title") or "").strip()
    if title:
        titles.append(title)
    if allowed_region is not None and runelite_window:
        current_runelite_window = _window_info_matching(title or "RuneLite")
        current_bounds = current_runelite_window.get("bounds") if isinstance(current_runelite_window, dict) else None
        source_bounds = runelite_window.get("bounds") if isinstance(runelite_window.get("bounds"), dict) else None
        allowed_region, coordinate_transform = _scale_rect_between_bounds(
            allowed_region,
            source_bounds=_valid_rect(source_bounds),
            destination_bounds=_valid_rect(current_bounds),
        )
        if coordinate_transform and (coordinate_transform.get("scaled") or coordinate_transform.get("translated")):
            warnings.append("calibration_allowed_region_transformed_to_current_runelite_window")
    titles.append("RuneLite")
    return {
        "schema": "arduino_live_movement_safety_from_calibration.v1",
        "status": "PASS" if not blockers else "FAIL",
        "blockers": blockers,
        "warnings": warnings,
        "allowedWindow": allowed_window or None,
        "allowedRegion": allowed_region,
        "sourceAllowedRegion": source_allowed_region,
        "calibrationRuneliteWindow": runelite_window or None,
        "currentRuneliteWindow": current_runelite_window,
        "coordinateTransform": coordinate_transform,
        "allowedForegroundTitles": list(dict.fromkeys(titles)),
        "source": "pointer_calibration_record",
    }


def _configure_live_arduino_movement_safety(
    args: argparse.Namespace,
    backend: Any,
    calibration_status: dict[str, Any],
) -> dict[str, Any]:
    safety = calibration_status.get("movementSafety") if isinstance(calibration_status.get("movementSafety"), dict) else {}
    blockers = list(safety.get("blockers") or [])
    if safety.get("status") != "PASS":
        blockers.append("calibration_movement_safety_unavailable")
    configure = getattr(backend, "configure_movement_safety", None)
    if not callable(configure):
        blockers.append("backend_movement_safety_unavailable")
    allowed_region = _valid_rect(safety.get("allowedRegion"))
    if allowed_region is None:
        blockers.append("live_allowed_region_missing")
    if blockers:
        return {
            "schema": "arduino_live_movement_safety.v1",
            "status": "FAIL",
            "blockers": list(dict.fromkeys(blockers)),
            "warnings": list(safety.get("warnings") or []),
            "movementSafety": safety,
        }
    configured = configure(
        allowed_region=allowed_region,
        allowed_foreground_titles=list(safety.get("allowedForegroundTitles") or ["RuneLite"]),
        enabled=True,
        margin_px=0,
        move_settle_ms=int(getattr(args, "arduino_move_settle_ms", 80) or 80),
        move_poll_ms=int(getattr(args, "arduino_move_poll_ms", 10) or 10),
        move_noeffect_timeout_ms=int(getattr(args, "arduino_move_noeffect_timeout_ms", 200) or 200),
        move_noeffect_retries=int(getattr(args, "arduino_move_noeffect_retries", 2) or 2),
        move_min_effective_px=int(getattr(args, "arduino_min_effective_move_px", 2) or 2),
        move_retry_scale=float(getattr(args, "arduino_retry_scale", 1.25) or 1.25),
        move_max_consecutive_noeffect=int(getattr(args, "arduino_move_max_consecutive_noeffect", 3) or 3),
    )
    return {
        "schema": "arduino_live_movement_safety.v1",
        "status": "PASS",
        "blockers": [],
        "warnings": list(safety.get("warnings") or []),
        "configured": configured,
        "movementSafety": safety,
    }


def run_arduino_pointer_calibration_test(args: argparse.Namespace, backend: Any) -> dict[str, Any]:
    from input_control.backend_arduino_hid import check_arduino_monitor_status
    from input_control.arduino_monitor import InputIntegrityMonitor

    payload: dict[str, Any] = {
        "schema": "arduino_pointer_calibration_test.v1",
        "status": "PASS",
        "backend": getattr(backend, "name", backend.__class__.__name__),
        "arduinoPort": args.arduino_port,
        "allowedWindow": getattr(args, "allowed_window", "runelite"),
        "allowedRegion": None,
        "expandedStagingRegion": None,
        "nearestSafePointInsideAllowedRegion": None,
        "maxStagingDistancePx": max(0, int(getattr(args, "calibration_staging_max_distance_px", 150) or 0)),
        "stagingMoveAllowed": None,
        "stagingUsed": False,
        "stagingAbortReason": None,
        "stagingTrace": None,
        "cursorStartPosition": None,
        "calibrationWindow": None,
        "runeliteWindow": None,
        "targetPoints": [],
        "movementTraces": [],
        "movementMetrics": None,
        "totalChunks": 0,
        "successfulChunks": 0,
        "retryChunks": 0,
        "noEffectChunks": 0,
        "consecutiveNoEffectChunks": 0,
        "movementSuccessRate": None,
        "maxPositionErrorPx": None,
        "finalPositionErrorPx": None,
        "movementErrorPx": None,
        "maxMovementErrorPx": None,
        "cursorLeftAllowedRegion": False,
        "foregroundWindowBefore": _foreground_window_info(),
        "foregroundWindowAfter": None,
        "preCalibrationFocus": None,
        "clickSent": False,
        "keySent": False,
        "stopAllBefore": None,
        "stopAllFinal": None,
        "disarmed": False,
        "firmwareStatusAfter": None,
        "monitorBefore": None,
        "monitorAfter": None,
        "calibrationMonitorStarted": False,
        "userControlConfirmation": {"required": bool(getattr(args, "require_user_control_confirmation", False)), "confirmed": None},
        "directBackendBypassCount": 0,
        "warnings": [],
    }
    root = None
    move_options = _arduino_move_options(args)
    status_path = Path(args.arduino_monitor_status_path or Path("interaction_geometry") / "live" / "input_integrity_status.json")
    backend_status_path = Path("interaction_geometry") / "live" / "arduino_backend_status.json"
    should_start_local_monitor = bool(args.arduino_require_monitor or callable(getattr(backend, "diagnostic_move_relative", None)))
    local_monitor = (
        InputIntegrityMonitor(
            expected_vid=args.arduino_vid,
            expected_pid=args.arduino_pid,
            expected_com_port=args.arduino_port,
            live_input_backend="arduino",
            status_output=status_path,
            backend_status_path=backend_status_path,
            max_age_ms=args.arduino_monitor_max_age_ms,
            fail_on_injected=args.input_integrity_fail_on_injected,
            fail_on_bypass=args.input_integrity_fail_on_bypass,
        )
        if should_start_local_monitor
        else None
    )

    def read_monitor(require_armed: bool = False, max_event_age_ms: int | None = None) -> dict[str, Any]:
        if payload.get("calibrationMonitorStarted") and local_monitor is not None:
            status = local_monitor.status(
                require_monitor=args.arduino_require_monitor,
                arduino_armed=bool(getattr(backend, "armed", False)),
                direct_backend_bypass_count=0,
            )
            try:
                local_monitor.write_status(
                    require_monitor=args.arduino_require_monitor,
                    arduino_armed=bool(getattr(backend, "armed", False)),
                    direct_backend_bypass_count=0,
                )
            except Exception:  # noqa: BLE001
                pass
            return status
        return check_arduino_monitor_status(
            require_monitor=args.arduino_require_monitor,
            status_path=args.arduino_monitor_status_path,
            expected_vid=args.arduino_vid,
            expected_pid=args.arduino_pid,
            expected_com_port=args.arduino_port,
            live_input_backend="arduino",
            arduino_armed=bool(getattr(backend, "armed", False)),
            software_input_allowed=False,
            direct_backend_bypass_count=0,
            fail_on_injected=args.input_integrity_fail_on_injected,
            fail_on_bypass=args.input_integrity_fail_on_bypass,
            require_armed=require_armed,
            max_event_age_ms=int(max_event_age_ms if max_event_age_ms is not None else args.arduino_monitor_max_age_ms),
        )

    try:
        if local_monitor is not None:
            local_monitor.start()
            payload["calibrationMonitorStarted"] = True
            time.sleep(0.20)
        if str(getattr(args, "allowed_window", "runelite") or "").strip().lower() == "runelite":
            payload["preCalibrationFocus"] = _restore_post_test_focus(
                "runelite",
                window_title_filter=getattr(args, "window_title_filter", "RuneLite"),
            )
        root, window_context, allowed_region, foreground_titles = _calibration_window_context(args)
        payload["allowedRegion"] = allowed_region
        screen = _screen_size()
        max_staging_distance = int(payload["maxStagingDistancePx"])
        expanded_staging_region = _expand_region(allowed_region, max_staging_distance, screen=screen)
        payload["expandedStagingRegion"] = expanded_staging_region
        if window_context.get("type") == "runelite":
            payload["runeliteWindow"] = window_context.get("window")
        else:
            payload["calibrationWindow"] = window_context.get("window")
        payload["fallbackCalibrationWindow"] = bool(window_context.get("fallbackCalibrationWindow"))
        payload["targetPoints"] = _region_points(allowed_region)
        payload["stopAllBefore"] = backend.stop_all()
        payload["monitorBefore"] = read_monitor(require_armed=False, max_event_age_ms=86_400_000)
        if args.arduino_require_monitor and not bool(payload["monitorBefore"].get("monitorPass")):
            payload["status"] = "FAIL"
            raise RuntimeError(str(payload["monitorBefore"].get("monitorBlockReason") or "input_integrity_monitor_failed_before_calibration"))
        backend.arm(args.arduino_session_token if args.arduino_session_token != "auto" else None)
        configure = getattr(backend, "configure_movement_safety", None)
        if callable(configure):
            configure(
                allowed_region=allowed_region,
                allowed_foreground_titles=foreground_titles,
                enabled=True,
                margin_px=0,
                max_chunk_px=12,
                tolerance_px=4,
                feedback_tolerance_px=10,
                **move_options,
            )
        cursor_start = _cursor_position()
        payload["cursorStartPosition"] = cursor_start
        if not _point_inside_region(cursor_start, allowed_region):
            nearest = _nearest_point_inside_region(cursor_start, allowed_region, margin_px=2)
            distance = _point_distance_px(cursor_start, nearest)
            payload["nearestSafePointInsideAllowedRegion"] = nearest
            if not expanded_staging_region or not _point_inside_region(cursor_start, expanded_staging_region):
                payload["stagingMoveAllowed"] = False
                payload["stagingAbortReason"] = "cursor_outside_expanded_staging_region"
                payload["status"] = "FAIL"
                raise RuntimeError("manual_cursor_placement_required")
            if distance is None or distance > max_staging_distance:
                payload["stagingMoveAllowed"] = False
                payload["stagingAbortReason"] = "staging_distance_exceeds_limit"
                payload["status"] = "FAIL"
                raise RuntimeError("manual_cursor_placement_required")
            if nearest is None:
                payload["stagingMoveAllowed"] = False
                payload["stagingAbortReason"] = "nearest_safe_point_unavailable"
                payload["status"] = "FAIL"
                raise RuntimeError("manual_cursor_placement_required")
            payload["stagingMoveAllowed"] = True
            payload["stagingUsed"] = True
            staging_trace = backend.move_to_absolute(
                nearest,
                allowed_region=expanded_staging_region,
                allowed_foreground_titles=foreground_titles,
                max_chunk_px=8,
                tolerance_px=4,
                feedback_tolerance_px=10,
                margin_px=0,
                max_chunks=max(8, max_staging_distance // 4),
                monitor_status_reader=lambda: read_monitor(require_armed=True, max_event_age_ms=86_400_000),
                **move_options,
            )
            payload["stagingTrace"] = staging_trace
            if staging_trace.get("leftAllowedRegion"):
                payload["cursorLeftAllowedRegion"] = True
            if staging_trace.get("positionErrorPx") is not None:
                error = int(staging_trace.get("positionErrorPx") or 0)
                payload["movementErrorPx"] = error
                payload["maxMovementErrorPx"] = max(int(payload.get("maxMovementErrorPx") or 0), error)
        else:
            payload["stagingMoveAllowed"] = False
            payload["stagingAbortReason"] = "cursor_already_inside_allowed_region"
        for point in payload["targetPoints"]:
            trace = backend.move_to_absolute(
                point,
                allowed_region=allowed_region,
                allowed_foreground_titles=foreground_titles,
                max_chunk_px=12,
                tolerance_px=4,
                feedback_tolerance_px=10,
                margin_px=0,
                monitor_status_reader=lambda: read_monitor(require_armed=True, max_event_age_ms=86_400_000),
                **move_options,
            )
            payload["movementTraces"].append(trace)
            if trace.get("leftAllowedRegion"):
                payload["cursorLeftAllowedRegion"] = True
            if trace.get("positionErrorPx") is not None:
                error = int(trace.get("positionErrorPx") or 0)
                payload["movementErrorPx"] = error
                payload["maxMovementErrorPx"] = max(int(payload.get("maxMovementErrorPx") or 0), error)
        payload["foregroundWindowAfter"] = _foreground_window_info()
    except Exception as error:  # noqa: BLE001
        payload["status"] = "FAIL"
        payload["warnings"].append(f"{type(error).__name__}: {error}")
        trace = getattr(backend, "last_movement_trace", None)
        if isinstance(trace, dict) and trace not in payload["movementTraces"]:
            payload["movementTraces"].append(trace)
            payload["cursorLeftAllowedRegion"] = bool(payload["cursorLeftAllowedRegion"] or trace.get("leftAllowedRegion"))
            if trace.get("positionErrorPx") is not None:
                error_px = int(trace.get("positionErrorPx") or 0)
                payload["movementErrorPx"] = error_px
                payload["maxMovementErrorPx"] = max(int(payload.get("maxMovementErrorPx") or 0), error_px)
    finally:
        try:
            backend.stop_all()
            payload["stopAllFinal"] = True
        except Exception as error:  # noqa: BLE001
            payload["warnings"].append(f"stop_all failed: {type(error).__name__}: {error}")
            payload["stopAllFinal"] = False
        try:
            backend.disarm()
            payload["disarmed"] = True
        except Exception as error:  # noqa: BLE001
            payload["warnings"].append(f"disarm failed: {type(error).__name__}: {error}")
        try:
            payload["firmwareStatusAfter"] = backend.firmware_status()
        except Exception as error:  # noqa: BLE001
            payload["warnings"].append(f"status_after failed: {type(error).__name__}: {error}")
        try:
            payload["monitorAfter"] = read_monitor(require_armed=False)
        except Exception as error:  # noqa: BLE001
            payload["warnings"].append(f"monitor_after failed: {type(error).__name__}: {error}")
        if root is not None:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
        if local_monitor is not None:
            try:
                local_monitor.stop()
            except Exception:  # noqa: BLE001
                pass
        payload["foregroundWindowAfter"] = payload.get("foregroundWindowAfter") or _foreground_window_info()
    status_after = payload.get("firmwareStatusAfter") if isinstance(payload.get("firmwareStatusAfter"), dict) else {}
    if status_after and (status_after.get("armed") or int(status_after.get("keysDown") or 0) != 0 or int(status_after.get("mouseButtonsDown") or 0) != 0):
        payload["status"] = "FAIL"
        payload["warnings"].append("firmware_status_not_safe_after_pointer_calibration")
    if payload.get("cursorLeftAllowedRegion"):
        payload["status"] = "FAIL"
        payload["warnings"].append("cursor_left_allowed_region")
    metrics = _calibration_movement_metrics(payload.get("movementTraces") or [])
    payload["movementMetrics"] = metrics
    for key in (
        "totalChunks",
        "successfulChunks",
        "retryChunks",
        "noEffectChunks",
        "consecutiveNoEffectChunks",
        "movementSuccessRate",
        "maxPositionErrorPx",
        "finalPositionErrorPx",
    ):
        payload[key] = metrics.get(key)
    if metrics.get("finalPositionErrorPx") is not None:
        payload["movementErrorPx"] = metrics.get("finalPositionErrorPx")
    if metrics.get("maxPositionErrorPx") is not None:
        payload["maxMovementErrorPx"] = metrics.get("maxPositionErrorPx")
    integrity_blocked, integrity_warnings = _input_integrity_has_blocking_counts(payload.get("monitorAfter"))
    if integrity_blocked:
        payload["status"] = "FAIL"
        payload["warnings"].extend(integrity_warnings)
    if bool(getattr(args, "require_user_control_confirmation", False)):
        confirmation = _prompt_user_control_confirmation()
        payload["userControlConfirmation"] = confirmation
        if not confirmation.get("confirmed"):
            payload["status"] = "FAIL" if payload.get("status") == "FAIL" else "WARN"
            payload["warnings"].append("user_control_confirmation_missing")
    else:
        payload["userControlConfirmation"] = {"required": False, "confirmed": None, "status": "SKIPPED"}
    _persist_pointer_calibration_record(args, payload)
    return payload


def format_arduino_pointer_calibration(payload: dict[str, Any]) -> str:
    firmware = payload.get("firmwareStatusAfter") if isinstance(payload.get("firmwareStatusAfter"), dict) else {}
    after_monitor = payload.get("monitorAfter") if isinstance(payload.get("monitorAfter"), dict) else {}
    integrity = after_monitor.get("inputIntegrityStatus") if isinstance(after_monitor.get("inputIntegrityStatus"), dict) else after_monitor
    injections = integrity.get("injectionFlags") if isinstance(integrity.get("injectionFlags"), dict) else {}
    lower = int(injections.get("mouseLowerIlInjectedCount") or 0) + int(injections.get("keyboardLowerIlInjectedCount") or 0)
    lines = [
        f"ARDUINO POINTER CALIBRATION - {payload.get('status') or 'UNKNOWN'}",
        "",
        f"Backend: {payload.get('backend') or 'unknown'}",
        f"Port: {payload.get('arduinoPort') or 'unknown'}",
        f"Allowed window: {payload.get('allowedWindow') or 'unknown'} fallbackCalibration={payload.get('fallbackCalibrationWindow')}",
        f"Allowed region: {payload.get('allowedRegion') or 'unknown'}",
        f"Expanded staging region: {payload.get('expandedStagingRegion') or 'unknown'}",
        f"Cursor start: {payload.get('cursorStartPosition') or 'unknown'}",
        f"Staging: used={payload.get('stagingUsed')} allowed={payload.get('stagingMoveAllowed')} reason={payload.get('stagingAbortReason') or 'none'}",
        f"Target points: {len(payload.get('targetPoints') or [])}",
        f"Movement traces: {len(payload.get('movementTraces') or [])}",
        f"Chunks: total={payload.get('totalChunks', 0)} success={payload.get('successfulChunks', 0)} retries={payload.get('retryChunks', 0)} noEffect={payload.get('noEffectChunks', 0)} consecutiveNoEffect={payload.get('consecutiveNoEffectChunks', 0)}",
        f"Movement success rate: {payload.get('movementSuccessRate') if payload.get('movementSuccessRate') is not None else 'unknown'}",
        f"Max movement error px: {payload.get('maxMovementErrorPx') if payload.get('maxMovementErrorPx') is not None else 'unknown'}",
        f"Final movement error px: {payload.get('finalPositionErrorPx') if payload.get('finalPositionErrorPx') is not None else 'unknown'}",
        f"Cursor left allowed region: {payload.get('cursorLeftAllowedRegion')}",
        f"Click/key sent: {payload.get('clickSent')} / {payload.get('keySent')}",
        f"Firmware final: armed={firmware.get('armed')} keysDown={firmware.get('keysDown')} mouseButtonsDown={firmware.get('mouseButtonsDown')}",
        f"Monitor: {after_monitor.get('status') or 'unknown'} injectedMouse={injections.get('mouseInjectedCount', 0)} injectedKeyboard={injections.get('keyboardInjectedCount', 0)} lowerIL={lower}",
        f"Direct backend bypasses: {payload.get('directBackendBypassCount', 0)}",
        "",
        "Warnings:",
    ]
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    return "\n".join(lines).rstrip() + "\n"


def format_arduino_movement_diagnostics(payload: dict[str, Any]) -> str:
    firmware = payload.get("firmwareStatusAfter") if isinstance(payload.get("firmwareStatusAfter"), dict) else {}
    monitor_after = payload.get("monitorAfter") if isinstance(payload.get("monitorAfter"), dict) else {}
    steps = payload.get("movementSteps") if isinstance(payload.get("movementSteps"), list) else []
    lines = [
        f"ARDUINO MOVEMENT DIAGNOSTICS - {payload.get('status') or 'UNKNOWN'}",
        "",
        f"Port: {payload.get('arduinoPort') or 'unknown'}",
        f"Classification: {payload.get('classification') or 'unknown'}",
        f"Reliability: {payload.get('reliabilityClassification') or 'unknown'}",
        f"Possible causes: {', '.join(payload.get('possibleCauses') or []) or 'none'}",
        f"Click/key sent: {payload.get('clickSent')} / {payload.get('keySent')}",
        f"Firmware final: armed={firmware.get('armed')} keysDown={firmware.get('keysDown')} mouseButtonsDown={firmware.get('mouseButtonsDown')}",
        f"Monitor final: {monitor_after.get('status') or 'unknown'}",
        "",
        "Steps:",
    ]
    for step in steps:
        if not isinstance(step, dict):
            continue
        command = step.get("commandTrace") if isinstance(step.get("commandTrace"), dict) else {}
        delta = step.get("inputIntegrityDelta") if isinstance(step.get("inputIntegrityDelta"), dict) else {}
        cursor_delta = step.get("windowsCursorDelta") if isinstance(step.get("windowsCursorDelta"), dict) else {}
        lines.append(
            "  "
            + f"{command.get('commandSent') or step.get('requestedDelta')}: "
            + f"ack={command.get('firmwareAck') or command.get('ackOk')} "
            + f"rawMouseDelta={delta.get('rawInputMouseCountDelta', 0)} "
            + f"rawDxDy={delta.get('rawInputMouseDxDelta', 0)},{delta.get('rawInputMouseDyDelta', 0)} "
            + f"cursorDelta={cursor_delta.get('dx', 0)},{cursor_delta.get('dy', 0)} "
            + f"settleMs={step.get('settleTimeMs')} polls={step.get('pollCount')} "
            + f"class={step.get('classification')}"
        )
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(["", "Warnings:"])
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    return "\n".join(lines).rstrip() + "\n"


def format_input_integrity_self_test(payload: dict[str, Any]) -> str:
    before = _input_integrity_status_payload(payload.get("monitorBefore"))
    after = _input_integrity_status_payload(payload.get("monitorAfter"))
    flags = after.get("injectionFlags") if isinstance(after.get("injectionFlags"), dict) else {}
    detected = after.get("arduinoDetected") if isinstance(after.get("arduinoDetected"), dict) else {}
    delta = payload.get("inputIntegrityDelta") if isinstance(payload.get("inputIntegrityDelta"), dict) else {}
    overlay = payload.get("overlay") if isinstance(payload.get("overlay"), dict) else {}
    firmware_safety = payload.get("firmwareSafety") if isinstance(payload.get("firmwareSafety"), dict) else {}
    vm_focus = payload.get("vmInputFocusSafety") if isinstance(payload.get("vmInputFocusSafety"), dict) else {}
    focus_recovery = payload.get("postTestFocusRecovery") if isinstance(payload.get("postTestFocusRecovery"), dict) else {}
    lower = int(flags.get("mouseLowerIlInjectedCount") or 0) + int(flags.get("keyboardLowerIlInjectedCount") or 0)
    lines = [
        f"INPUT INTEGRITY SELF TEST - {payload.get('status') or 'UNKNOWN'}",
        "",
        f"Mode: {payload.get('testMode') or 'unknown'}",
        f"Backend: {payload.get('backend') or 'unknown'}",
        f"Arduino port: {payload.get('arduinoPort') or 'unknown'}",
        f"Overlay: {'started' if overlay.get('started') else 'not started'} passive={payload.get('overlayPassive')} noFocus={payload.get('overlayNoFocus')} status={overlay.get('statusPath') or 'unknown'}",
        f"Firmware safety: {firmware_safety.get('status') or 'unknown'}",
        f"VM focus safety: {vm_focus.get('status') or 'unknown'} state={vm_focus.get('postTestInputState') or 'unknown'}",
        f"Focus recovery: {focus_recovery.get('status') or 'unknown'} target={focus_recovery.get('target') or 'unknown'}",
        f"Armed/disarmed: {payload.get('armed')} / {payload.get('disarmed')}",
        f"Tiny move sent: {payload.get('tinyMoveSent')}",
        f"No-move test: {payload.get('noMoveSent')}",
        f"Raw Input mouse/keyboard: {detected.get('mousePresent')} / {detected.get('keyboardPresent')}",
        f"VID/PID matched: {detected.get('vidPidMatched')}",
        f"Injected mouse: {flags.get('mouseInjectedCount', 0)}",
        f"Injected keyboard: {flags.get('keyboardInjectedCount', 0)}",
        f"LowerIL: {lower}",
        f"Raw mouse delta: {delta.get('rawInputMouseCountDelta', 0)}",
        f"Post-test recovery: {payload.get('postTestRecoveryCheck') or 'unknown'}",
        f"Continuation allowed: {payload.get('continuationAllowed')}",
        "",
        "Warnings:",
    ]
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    return "\n".join(lines).rstrip() + "\n"


def format_arduino_check(payload: dict[str, Any]) -> str:
    status = payload.get("backendStatus") if isinstance(payload.get("backendStatus"), dict) else {}
    lines = [
        f"ARDUINO HID CHECK - {payload.get('status') or 'UNKNOWN'}",
        "",
        f"Check: {payload.get('check') or 'unknown'}",
        f"Backend: {payload.get('backend') or 'unknown'}",
        f"Port: {status.get('port') or 'unknown'}",
        f"Connected: {status.get('connected')}",
        f"Identified: {status.get('identified')}",
        f"Armed: {status.get('armed')}",
        f"Commands: {status.get('commandCount', 0)} ackFailures={status.get('ackFailures', 0)} timeouts={status.get('timeouts', 0)}",
        f"Result: {payload.get('result')}",
    ]
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(["", "Warnings:"])
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        args.execute = False
    apply_focus_default(args)
    if args.plan_next_click:
        payload = build_click_plan_payload(args)
        print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_click_plan_payload(payload), end="")
        return 0 if payload.get("status") != "FAIL" else 1
    backend = backend_from_options(args)
    overlay_info = None
    if args.show_input_integrity_overlay and not args.input_integrity_self_test:
        overlay_info = _start_input_integrity_overlay(args)
    if args.arduino_check:
        payload = run_arduino_check(args, backend)
        if overlay_info is not None:
            payload["inputIntegrityOverlay"] = overlay_info
        print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_arduino_check(payload), end="")
        return 0 if payload.get("status") != "FAIL" else 1
    if args.input_integrity_self_test:
        payload = run_input_integrity_self_test(args, backend)
        print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_input_integrity_self_test(payload), end="")
        return 0 if payload.get("status") != "FAIL" else 1
    if args.arduino_movement_diagnostics:
        payload = run_arduino_movement_diagnostics(args, backend)
        print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_arduino_movement_diagnostics(payload), end="")
        return 0 if payload.get("status") != "FAIL" else 1
    if args.arduino_pointer_calibration_test:
        payload = run_arduino_pointer_calibration_test(args, backend)
        print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_arduino_pointer_calibration(payload), end="")
        return 0 if payload.get("status") != "FAIL" else 1
    pre_action_focus = None
    if bool(getattr(args, "focus_runelite", False)) and (args.execute or args.hover_only or args.camera_self_test):
        pre_action_focus = _restore_post_test_focus(
            "runelite",
            window_title_filter=getattr(args, "window_title_filter", "RuneLite"),
        )
    if args.auto_recover_loaded_scene and (args.execute or args.hover_only or args.loop or args.max_actions > 1):
        import liveness_recovery_core

        recovery_payload = liveness_recovery_core.ensure_loaded_scene(
            daemon_url=args.daemon_url,
            snapshot_url=args.snapshot_url,
            backend="arduino",
            arduino_port=args.arduino_port,
            max_total_ms=int(max(1.0, float(args.liveness_max_total_seconds or 120.0)) * 1000.0),
            max_attempts_per_state=max(1, int(args.liveness_max_attempts_per_state or 2)),
            allow_jagex_launcher=bool(args.allow_jagex_launcher_automation),
            allow_credentials=False,
        )
        if recovery_payload.get("status") not in {"loaded_scene_ready", "recovered_loaded_scene"}:
            print(
                json.dumps(recovery_payload, indent=2, sort_keys=False)
                if args.json
                else liveness_recovery_core.format_compact_result(recovery_payload),
                end="",
            )
            return 1
    calibration_status: dict[str, Any] | None = None
    movement_safety: dict[str, Any] | None = None
    if (
        args.backend == "arduino"
        and (args.execute or args.hover_only)
        and not bool(getattr(args, "allow_uncalibrated_arduino_movement", False))
    ):
        calibration_status = _load_pointer_calibration_for_live_movement(args)
        if calibration_status.get("status") != "PASS":
            payload = {
                "schema": "arduino_live_movement_block.v1",
                "status": "FAIL",
                "reason": "arduino_pointer_calibration_required",
                "executed": False,
                "clickSent": False,
                "keySent": False,
                "liveRuneLiteClicksBlocked": True,
                "overrideFlag": "--allow-uncalibrated-arduino-movement",
                "pointerCalibration": calibration_status,
                "preActionFocus": pre_action_focus,
                "warnings": [
                    "Arduino live RuneLite movement is blocked until a closed-loop pointer calibration record is present and valid."
                ],
            }
            print(
                json.dumps(payload, indent=2, sort_keys=False)
                if args.json
                else "Live action blocked: Arduino pointer calibration is required before RuneLite movement.\n",
                end="",
            )
            return 1
        movement_safety = _configure_live_arduino_movement_safety(args, backend, calibration_status)
        if movement_safety.get("status") != "PASS":
            payload = {
                "schema": "arduino_live_movement_block.v1",
                "status": "FAIL",
                "reason": "arduino_live_movement_safety_unavailable",
                "executed": False,
                "clickSent": False,
                "keySent": False,
                "liveRuneLiteClicksBlocked": True,
                "pointerCalibration": calibration_status,
                "movementSafety": movement_safety,
                "preActionFocus": pre_action_focus,
                "warnings": [
                    "Arduino live RuneLite movement is blocked because the pointer calibration could not be applied as a closed-loop movement guard."
                ],
            }
            print(
                json.dumps(payload, indent=2, sort_keys=False)
                if args.json
                else "Live action blocked: Arduino movement safety could not be configured from pointer calibration.\n",
                end="",
            )
            return 1
    if args.require_user_control_confirmation and (args.execute or args.hover_only or args.camera_self_test):
        confirmation = _prompt_user_control_confirmation()
        if not confirmation.get("confirmed"):
            payload = {
                "schema": "live_input_user_control_confirmation.v1",
                "status": "FAIL",
                "reason": "user_control_confirmation_missing",
                "userControlConfirmation": confirmation,
                "executed": False,
            }
            print(json.dumps(payload, indent=2, sort_keys=False) if args.json else "Live action blocked: user-control confirmation was not received.\n", end="")
            return 1
    if args.camera_self_test:
        payload = run_camera_self_test(args.snapshot_url, args, backend=backend)
        print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_camera_self_test(payload))
        return 0 if payload.get("status") != "FAIL" else 1
    run_loop = bool(args.loop or args.max_actions > 1)
    result = execute_action_loop(args.daemon_url, args, backend=backend) if run_loop else execute_next_action(args.daemon_url, args, backend=backend)
    payload = result.to_dict()
    if calibration_status is not None:
        payload["pointerCalibration"] = calibration_status
    if movement_safety is not None:
        payload["movementSafety"] = movement_safety
    if pre_action_focus is not None:
        payload["preActionFocus"] = pre_action_focus
    if overlay_info is not None:
        payload["inputIntegrityOverlay"] = overlay_info
    maybe_record_client_hot(payload, args)
    trace_persist = persist_latest_action_trace(payload, daemon_url=args.daemon_url)
    if trace_persist is not None:
        payload["latestActionTracePersisted"] = trace_persist
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_human(payload), end="")
    return 0 if payload.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

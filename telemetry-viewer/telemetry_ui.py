from __future__ import annotations

import argparse
import json
import math
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import telemetry_capabilities
import telemetry_sources
import arduino_input_bridge
import route_template
import start_game_command


CONFIG_SCHEMA = "osrs_telemetry_ui_config.v1"
CHECK_SCHEMA = "osrs_telemetry_ui_check.v1"
UI_RECORDING_SESSION_SCHEMA = "ui_recording_session_manifest.v1"
DEFAULT_PORT = "8890"
DEFAULT_PROFILE = "woodcutting"
UI_MODE_SIMPLE = "simple"
UI_MODE_ADVANCED = "advanced"
PROFILE_RECORD_EVERYTHING = "record_everything_default"
PROFILE_LEGACY_UNIVERSAL_HUMAN = "universal_human_recording"
PROFILE_UNIVERSAL_HUMAN = PROFILE_RECORD_EVERYTHING
ANALYZER_TIMEOUT_SECONDS = 180
LOG_DRAIN_BATCH_SIZE = 80
LOG_TEXT_MAX_LINES = 2500
PROCESS_NAMES = ("game", "context_service", "live_processor", "recorder", "analyzer", "knowledge", "mcp_server", "arduino_bridge", "input_smoke_test", "route_monitor")
PRESET_BASIC = "Basic Telemetry Recording"
PRESET_MENU_ROW = "Menu Row Validation"
PRESET_LIVE_MIRROR_MENU_ROW = "Live Mirror Menu Row Validation"
PRESET_WOODCUTTING = "Woodcutting Recording"
PRESET_ROUTE = "Route / Traversal Recording"
PRESET_CUSTOM = "Custom / Advanced"
PRESETS = (
    PRESET_BASIC,
    PRESET_MENU_ROW,
    PRESET_LIVE_MIRROR_MENU_ROW,
    PRESET_WOODCUTTING,
    PRESET_ROUTE,
    PRESET_CUSTOM,
)
ARTIFACT_OPTIONS = {
    "Recording folder": None,
    "summary.json": "summary.json",
    "schema_gap_report.md": "schema_gap_report.md",
    "input_action_summary.json": "input_action_summary.json",
    "target_match_summary.json": "target_match_summary.json",
    "menu_interaction_summary.json": "menu_interaction_summary.json",
    "click_ownership_summary.json": "click_ownership_summary.json",
    "arduino_live_mirror_summary.json": "arduino_live_mirror_summary.json",
    "coordinate_alignment_summary.json": "coordinate_alignment_summary.json",
    "banking_lifecycle.json": "banking_lifecycle.json",
    "interruption_lifecycle.json": "interruption_lifecycle.json",
    "combat_damage_summary.json": "combat_damage_summary.json",
    "woodcutting_loop_lifecycle.json": "woodcutting_loop_lifecycle.json",
    "traversal_lifecycle.json": "traversal_lifecycle.json",
    "route_template_comparison.json": "route_template_comparison.json",
    "route_template_variant.json": "route_template_variant.json",
    "route_monitor_status.json": "route_monitor_status.json",
    "route_session_state.json": "route_session_state.json",
    "route_session_events.jsonl": "route_session_events.jsonl",
    "route_history_summary.json": "route_history_summary.json",
}
SIMPLE_MAIN_BUTTONS = (
    "Start Game",
    "Start Telemetry",
    "Start Recording",
    "Stop Recording",
    "Analyze Latest",
    "Open Output Folder",
    "Diagnostics / Settings",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def viewer_dir() -> Path:
    return Path(__file__).resolve().parent


def user_config_path() -> Path:
    return Path.home() / ".osrs-telemetry" / "telemetry_ui_config.json"


def default_route_template_path(root: Path | None = None) -> Path:
    return route_template.default_template_path(root or repo_root())


def ui_control_dir() -> Path:
    return Path.home() / ".osrs-telemetry" / "ui_control"


def recordings_root(root: Path | None = None) -> Path:
    return (root or repo_root()) / "recordings"


def resolve_output_folder(config: dict[str, Any] | None = None, *, root: Path | None = None) -> Path:
    cfg = config if isinstance(config, dict) else {}
    supplied = str(cfg.get("output_folder") or "").strip()
    if supplied:
        path = Path(supplied).expanduser()
        if not path.is_absolute():
            path = (root or repo_root()) / path
        return path.resolve()
    return recordings_root(root).resolve()


def script_exists(relative: str, root: Path | None = None) -> bool:
    return ((root or repo_root()) / relative).exists()


def python_command(*parts: str) -> list[str]:
    return [sys.executable, *[str(part) for part in parts]]


def command_text(command: list[str] | str | None) -> str:
    return start_game_command.command_text(command)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def discover_game_launch_command(root: Path | None = None) -> list[str] | str:
    return start_game_command.discover_game_launch_command(root or repo_root())


def default_config(root: Path | None = None) -> dict[str, Any]:
    game_command = discover_game_launch_command(root)
    template_path = default_route_template_path(root)
    return {
        "schema_version": CONFIG_SCHEMA,
        "ui_mode": UI_MODE_SIMPLE,
        "advanced_expanded": False,
        "recording_profile": PROFILE_UNIVERSAL_HUMAN,
        "auto_analyze_after_stop": True,
        "update_project_knowledge": True,
        "analyzer_timeout_seconds": str(ANALYZER_TIMEOUT_SECONDS),
        "output_folder": str(recordings_root(root)),
        "selected_preset": PRESET_BASIC,
        "compact_mode": False,
        "last_session_path": "",
        "last_recording_label": "manual_recording",
        "recording_label_mode": "auto",
        "recording_label_preset": PRESET_BASIC,
        "last_recording_description": "",
        "duration": "0",
        "poll_interval_ms": "50",
        "latest_session": True,
        "prefer_active_session": True,
        "include_raw": False,
        "pretty": False,
        "sources_override": "",
        "telemetry_preflight": True,
        "telemetry_preflight_seconds": "5",
        "max_telemetry_age_ms": "3000",
        "wait_for_fresh_telemetry": True,
        "wait_for_fresh_telemetry_timeout": "30",
        "menu_capture_burst": False,
        "menu_burst_until_selection": False,
        "menu_burst_tail_ms": "500",
        "menu_burst_max_ms": "4000",
        "menu_burst_ms": "2000",
        "menu_burst_poll_ms": "15",
        "capture_input": False,
        "capture_mouse": True,
        "capture_keyboard": False,
        "input_backend": "polling",
        "prefer_polling_input": True,
        "raw_input_device_attribution": False,
        "input_preflight": True,
        "fail_if_input_preflight_fails": False,
        "input_preflight_seconds": "5",
        "join_input_telemetry": True,
        "camera_behavior_analysis": True,
        "human_click_profile_analysis": True,
        "traversal_lifecycle_analysis": True,
        "route_name": route_template.DEFAULT_ROUTE_NAME,
        "route_template_path": str(template_path),
        "route_template_mode": "auto",
        "route_template_out_dir": "route_templates",
        "route_monitor_enabled": False,
        "route_history_enabled": False,
        "route_template_required": False,
        "route_variant_name": "walk_here_large_door",
        "vm_mouse_mapping": False,
        "arduino_enabled": False,
        "arduino_required_for_recording": False,
        "arduino_auto_start_on_recording": False,
        "arduino_passthrough_mode": "off",
        "arduino_probe": True,
        "arduino_probe_move_dx": "12",
        "arduino_probe_move_dy": "0",
        "arduino_probe_observe_ms": "500",
        "require_arduino_probe_verified": False,
        "arduino_mirror_preflight": True,
        "require_arduino_mirror_verified": False,
        "arduino_live_mirror": False,
        "require_live_mirror_active": False,
        "require_live_mirror_verified": False,
        "persistent_mirror_during_recording": True,
        "mirror_arm_mode": "recording_persistent",
        "mirror_persist_until_stop": True,
        "mirror_keep_armed_while_recording": True,
        "mirror_disarm_on_focus_lost": False,
        "mirror_profile": "full_live_mirror",
        "mirror_click_policy": "live_unsuppressed",
        "require_click_source_suppression": False,
        "allow_unsuppressed_live_clicks": False,
        "max_live_clicks_per_recording": "0",
        "auto_disable_live_clicks_after_first_game_action": False,
        "mirror_disable_movement": False,
        "mirror_disable_clicks": False,
        "mirror_echo_suppression": False,
        "mirror_echo_window_ms": "250",
        "mirror_click_echo_window_ms": "300",
        "mirror_echo_max_error_px": "100",
        "mirror_max_queue_size": "25",
        "mirror_drop_move_older_than_ms": "150",
        "mirror_clear_queue_on_game_action": False,
        "mirror_clear_queue_on_menu_selection": False,
        "mirror_clear_queue_on_plane_change": False,
        "mirror_clear_queue_on_target_action": False,
        "mirror_auto_pause_after_first_game_action": False,
        "mirror_auto_pause_after_menu_selection": False,
        "mirror_auto_pause_after_plane_change": False,
        "mirror_auto_pause_after_target_quality": "off",
        "mirror_validation_mode": "custom",
        "mirror_move_min_px": "1",
        "mirror_max_step_px": "25",
        "mirror_send_interval_ms": "5",
        "mirror_scale_x": "1.0",
        "mirror_scale_y": "1.0",
        "mirror_invert_x": False,
        "mirror_invert_y": False,
        "mirror_button_mode": "click",
        "mirror_quiet_probe": True,
        "mirror_test_duration_sec": "5",
        "mirror_test_arm_delay_ms": "2000",
        "mirror_arm_delay_ms": "500",
        "mirror_max_clicks_per_second": "4",
        "mirror_max_button_commands_per_second": "8",
        "mirror_max_move_commands_per_second": "120",
        "mirror_max_total_commands_per_second": "150",
        "mirror_click_cooldown_ms": "120",
        "mirror_same_button_cooldown_ms": "80",
        "mirror_max_burst_commands": "50",
        "mirror_panic_command_threshold": "100",
        "mirror_panic_window_ms": "1000",
        "mirror_arm_only_when_runelite_focused": True,
        "mirror_window_title_allow": "RuneLite",
        "mirror_exclude_window_title": "OSRS Telemetry Control",
        "mirror_region": "client",
        "mirror_ignore_ui_clicks": True,
        "input_path_integrity": True,
        "arduino_port": "",
        "arduino_baud": "115200",
        "arduino_calibration_profile": "",
        "context_service_port": DEFAULT_PORT,
        "profile": DEFAULT_PROFILE,
        "game_launch_command": command_text(game_command),
        "authenticated_game_start_command": "",
        "preferred_script_commands": {},
    }


def merge_config(value: dict[str, Any] | None, root: Path | None = None) -> dict[str, Any]:
    merged = default_config(root)
    supplied_label_mode = isinstance(value, dict) and "recording_label_mode" in value
    if isinstance(value, dict):
        merged.update({key: val for key, val in value.items() if key != "schema_version"})
    merged["schema_version"] = CONFIG_SCHEMA
    merged.setdefault("preferred_script_commands", {})
    migrate_route_template_config(merged, root=root)
    merged["ui_mode"] = UI_MODE_SIMPLE
    merged["advanced_expanded"] = False
    profile = str(merged.get("recording_profile") or "").strip()
    if profile == PROFILE_LEGACY_UNIVERSAL_HUMAN:
        profile = PROFILE_RECORD_EVERYTHING
    if profile not in {
        PROFILE_UNIVERSAL_HUMAN,
        PROFILE_RECORD_EVERYTHING,
        "route_traversal",
        "woodcutting",
        "menu_validation",
        "custom",
    }:
        profile = PROFILE_RECORD_EVERYTHING
    merged["recording_profile"] = PROFILE_RECORD_EVERYTHING if profile in {"route_traversal", "woodcutting", "menu_validation", "custom"} else profile
    merged["selected_preset"] = PRESET_BASIC
    merged["output_folder"] = str(resolve_output_folder(merged, root=root))
    merged["auto_analyze_after_stop"] = bool(merged.get("auto_analyze_after_stop", True))
    if (not supplied_label_mode) or str(merged.get("recording_label_mode") or "").strip() not in {"auto", "custom"}:
        merged["recording_label_mode"] = "auto" if label_looks_auto_generated(merged.get("last_recording_label")) else "custom"
    if not str(merged.get("last_recording_label") or "").strip():
        merged["recording_label_mode"] = "auto"
        merged["last_recording_label"] = suggested_recording_label_for_preset(
            str(merged.get("selected_preset") or PRESET_BASIC),
            route_name=str(merged.get("route_name") or route_template.DEFAULT_ROUTE_NAME),
        )
    return merged


def public_template_resolution(resolution: dict[str, Any]) -> dict[str, Any]:
    return {key: val for key, val in resolution.items() if key != "template"}


def migrate_route_template_config(config: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    repo = root or repo_root()
    supplied = str(config.get("route_template_path") or config.get("route_name") or route_template.DEFAULT_ROUTE_NAME).strip()
    resolution = route_template.resolve_route_template(
        supplied,
        root=repo,
        default_template=default_route_template_path(repo),
    )
    if resolution.get("status") != "PASS":
        resolution = route_template.resolve_route_template(
            route_template.DEFAULT_ROUTE_NAME,
            root=repo,
            default_template=default_route_template_path(repo),
        )
    config["route_template_resolution"] = public_template_resolution(resolution)
    if resolution.get("status") == "PASS":
        config["route_template_path"] = str(resolution.get("resolvedPath") or "")
        config["route_name"] = str(resolution.get("routeName") or route_template.DEFAULT_ROUTE_NAME)
        config["route_template_revision"] = resolution.get("templateRevision")
        config["route_required_segment_count"] = resolution.get("requiredSegmentCount")
    else:
        config["route_template_path"] = str(default_route_template_path(repo))
        config["route_name"] = route_template.DEFAULT_ROUTE_NAME
        config["route_template_revision"] = None
        config["route_required_segment_count"] = 0
    return config


def resolve_template_from_config(config: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    return route_template.resolve_route_template(
        config.get("route_template_path") or config.get("route_name") or route_template.DEFAULT_ROUTE_NAME,
        root=root or repo_root(),
        default_template=default_route_template_path(root),
    )


def resolved_route_template_path(config: dict[str, Any], *, root: Path | None = None) -> str:
    resolution = resolve_template_from_config(config, root=root)
    return str(resolution.get("resolvedPath") or "")


def use_auto_route_template(config: dict[str, Any] | None = None) -> bool:
    cfg = config if isinstance(config, dict) else {}
    return str(cfg.get("route_template_mode") or "auto").strip().lower() == "auto"


def preset_updates(preset: str) -> dict[str, Any]:
    base_input = {
        "latest_session": True,
        "input_backend": "polling",
        "prefer_polling_input": True,
        "input_preflight": True,
        "capture_mouse": True,
        "capture_window_context": True,
        "join_input_telemetry": True,
        "input_path_integrity": True,
    }
    if preset == PRESET_BASIC:
        return {
            "capture_input": False,
            "arduino_enabled": False,
            "arduino_auto_start_on_recording": False,
            "arduino_passthrough_mode": "off",
            "arduino_live_mirror": False,
            "vm_mouse_mapping": False,
            "camera_behavior_analysis": False,
            "traversal_lifecycle_analysis": False,
        }
    if preset == PRESET_MENU_ROW:
        return {
            **base_input,
            "capture_input": True,
            "capture_keyboard": True,
            "menu_capture_burst": True,
            "menu_burst_until_selection": True,
            "menu_burst_tail_ms": "500",
            "menu_burst_max_ms": "4000",
            "menu_burst_ms": "2000",
            "menu_burst_poll_ms": "15",
            "camera_behavior_analysis": True,
            "traversal_lifecycle_analysis": True,
            "vm_mouse_mapping": False,
            "arduino_enabled": False,
            "arduino_auto_start_on_recording": False,
            "arduino_passthrough_mode": "off",
            "arduino_live_mirror": False,
        }
    if preset == PRESET_LIVE_MIRROR_MENU_ROW:
        return {
            **base_input,
            "capture_input": True,
            "capture_keyboard": True,
            "raw_input_device_attribution": True,
            "menu_capture_burst": True,
            "menu_burst_until_selection": True,
            "menu_burst_tail_ms": "500",
            "menu_burst_max_ms": "4000",
            "menu_burst_ms": "2000",
            "menu_burst_poll_ms": "15",
            "camera_behavior_analysis": True,
            "traversal_lifecycle_analysis": True,
            "vm_mouse_mapping": True,
            "arduino_enabled": True,
            "arduino_auto_start_on_recording": True,
            "arduino_passthrough_mode": "mirror",
            "arduino_probe": True,
            "arduino_probe_move_dx": "25",
            "arduino_probe_move_dy": "0",
            "arduino_probe_observe_ms": "750",
            "arduino_mirror_preflight": True,
            "arduino_live_mirror": True,
            "mirror_profile": "validation_menu_row",
            "mirror_click_policy": "map_only",
            "require_click_source_suppression": False,
            "allow_unsuppressed_live_clicks": False,
            "max_live_clicks_per_recording": "0",
            "auto_disable_live_clicks_after_first_game_action": True,
            "mirror_disable_movement": True,
            "mirror_disable_clicks": False,
            "mirror_echo_suppression": True,
            "mirror_echo_window_ms": "250",
            "mirror_click_echo_window_ms": "300",
            "mirror_echo_max_error_px": "100",
            "mirror_clear_queue_on_game_action": True,
            "mirror_clear_queue_on_menu_selection": True,
            "mirror_clear_queue_on_plane_change": True,
            "mirror_auto_pause_after_menu_selection": True,
            "mirror_auto_pause_after_plane_change": True,
            "mirror_auto_pause_after_target_quality": "medium",
            "mirror_validation_mode": "menu_row",
            "persistent_mirror_during_recording": True,
            "mirror_arm_mode": "recording_persistent",
            "mirror_persist_until_stop": True,
            "mirror_keep_armed_while_recording": True,
            "mirror_arm_only_when_runelite_focused": True,
            "mirror_ignore_ui_clicks": True,
            "mirror_quiet_probe": True,
            "mirror_max_clicks_per_second": "4",
            "mirror_max_button_commands_per_second": "8",
            "mirror_max_move_commands_per_second": "120",
            "mirror_max_total_commands_per_second": "150",
            "mirror_click_cooldown_ms": "120",
            "mirror_same_button_cooldown_ms": "80",
            "mirror_panic_command_threshold": "100",
            "mirror_panic_window_ms": "1000",
        }
    if preset == PRESET_WOODCUTTING:
        return {
            **base_input,
            "capture_input": True,
            "capture_keyboard": False,
            "camera_behavior_analysis": True,
            "traversal_lifecycle_analysis": False,
            "vm_mouse_mapping": False,
            "arduino_enabled": False,
            "arduino_auto_start_on_recording": False,
            "arduino_passthrough_mode": "off",
            "arduino_live_mirror": False,
        }
    if preset == PRESET_ROUTE:
        return {
            **base_input,
            "capture_input": True,
            "capture_keyboard": True,
            "route_name": route_template.DEFAULT_ROUTE_NAME,
            "route_template_path": str(default_route_template_path()),
            "route_template_mode": "explicit",
            "route_monitor_enabled": True,
            "route_history_enabled": True,
            "menu_capture_burst": True,
            "menu_burst_until_selection": True,
            "menu_burst_tail_ms": "500",
            "menu_burst_max_ms": "4000",
            "menu_burst_ms": "2000",
            "menu_burst_poll_ms": "15",
            "camera_behavior_analysis": True,
            "traversal_lifecycle_analysis": True,
            "vm_mouse_mapping": False,
            "arduino_enabled": False,
            "arduino_auto_start_on_recording": False,
            "arduino_passthrough_mode": "off",
            "arduino_live_mirror": False,
        }
    return {}


def universal_human_recording_updates(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    updates = {
        "recording_profile": PROFILE_RECORD_EVERYTHING,
        "latest_session": True,
        "prefer_active_session": True,
        "telemetry_preflight": True,
        "telemetry_preflight_seconds": "5",
        "max_telemetry_age_ms": "3000",
        "wait_for_fresh_telemetry": True,
        "wait_for_fresh_telemetry_timeout": "30",
        "capture_input": True,
        "input_backend": "polling",
        "prefer_polling_input": True,
        "input_preflight": True,
        "input_preflight_seconds": "5",
        "capture_mouse": True,
        "capture_keyboard": True,
        "raw_input_device_attribution": True,
        "join_input_telemetry": True,
        "camera_behavior_analysis": True,
        "traversal_lifecycle_analysis": True,
        "menu_capture_burst": True,
        "menu_burst_until_selection": True,
        "menu_burst_tail_ms": "500",
        "menu_burst_max_ms": "4000",
        "menu_burst_ms": "2000",
        "menu_burst_poll_ms": "15",
        "preserve_bank_ui": True,
        "preserve_combat_state": True,
        "plugin_snapshot_url": telemetry_sources.DEFAULT_PLUGIN_SNAPSHOT_URL,
        "plugin_snapshot_timeout": str(telemetry_sources.DEFAULT_PLUGIN_SNAPSHOT_TIMEOUT_SECONDS),
        "input_path_integrity": True,
        "arduino_required_for_recording": False,
        "arduino_auto_start_on_recording": False,
        "arduino_passthrough_mode": "off",
        "arduino_enabled": bool(cfg.get("arduino_enabled")) and bool(str(cfg.get("arduino_port") or "").strip()),
        "arduino_live_mirror": False,
        "require_live_mirror_active": False,
        "require_live_mirror_verified": False,
        "mirror_profile": "observe_only",
        "mirror_click_policy": "map_only",
        "mirror_disable_movement": True,
        "mirror_disable_clicks": True,
        "route_template_mode": "auto",
        "route_monitor_enabled": False,
        "route_history_enabled": False,
    }
    if bool(updates.get("arduino_enabled")) or bool(cfg.get("vm_mouse_mapping")):
        updates.update(
            {
                "arduino_passthrough_mode": "label_only",
                "arduino_probe": True,
                "vm_mouse_mapping": True,
                "mirror_click_policy": "map_only",
                "mirror_disable_movement": True,
                "mirror_disable_clicks": True,
            }
        )
    if resolve_template_from_config({**cfg, **updates}).get("status") == "PASS":
        updates["route_monitor_enabled"] = True
        updates["route_history_enabled"] = True
    return updates


def config_for_recording_profile(config: dict[str, Any], profile: str | None = None) -> dict[str, Any]:
    merged = merge_config(config)
    selected = str(profile or merged.get("recording_profile") or PROFILE_UNIVERSAL_HUMAN)
    if selected == PROFILE_LEGACY_UNIVERSAL_HUMAN:
        selected = PROFILE_RECORD_EVERYTHING
    if selected == PROFILE_UNIVERSAL_HUMAN:
        merged.update(universal_human_recording_updates(merged))
        if not str(merged.get("last_recording_label") or "").strip() or str(merged.get("recording_label_mode") or "auto") != "custom":
            merged["last_recording_label"] = simple_recording_label()
            merged["recording_label_mode"] = "auto"
            merged["recording_label_preset"] = PROFILE_RECORD_EVERYTHING
    elif selected == "route_traversal":
        merged = config_for_preset(merged, PRESET_ROUTE)
    elif selected == "woodcutting":
        merged = config_for_preset(merged, PRESET_WOODCUTTING)
    elif selected == "menu_validation":
        merged = config_for_preset(merged, PRESET_MENU_ROW)
    else:
        merged["recording_profile"] = "custom"
    return merged


def config_for_analysis_run(config: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = merge_config(config or {})
    profile = str(merged.get("recording_profile") or PROFILE_UNIVERSAL_HUMAN)
    if profile != "custom":
        return config_for_recording_profile(merged, profile)
    preset = str(merged.get("selected_preset") or PRESET_CUSTOM)
    if preset != PRESET_CUSTOM:
        return config_for_preset(merged, preset)
    return merged


def config_for_preset(config: dict[str, Any], preset: str) -> dict[str, Any]:
    merged = merge_config(config)
    merged["selected_preset"] = preset if preset in PRESETS else PRESET_CUSTOM
    merged.update(preset_updates(merged["selected_preset"]))
    apply_preset_recording_label(merged, merged["selected_preset"])
    return merged


def load_config(path: str | Path | None = None, *, root: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).expanduser() if path else user_config_path()
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        value = {}
    return merge_config(value if isinstance(value, dict) else {}, root=root)


def save_config(config: dict[str, Any], path: str | Path | None = None) -> Path:
    config_path = Path(path).expanduser() if path else user_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = merge_config(config)
    temp = config_path.with_name(f".{config_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temp.replace(config_path)
    return config_path


def port_from_config(config: dict[str, Any]) -> int:
    try:
        return int(str(config.get("context_service_port") or DEFAULT_PORT))
    except ValueError:
        return int(DEFAULT_PORT)


def profile_from_config(config: dict[str, Any]) -> str:
    return str(config.get("profile") or DEFAULT_PROFILE).strip() or DEFAULT_PROFILE


def script_supports_flag(relative: str, flag: str, root: Path | None = None) -> bool:
    path = (root or repo_root()) / relative
    try:
        return flag in path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def build_context_service_command(config: dict[str, Any]) -> list[str]:
    return python_command("telemetry-viewer\\context_service.py", "--latest-session", "--port", str(port_from_config(config)))


def build_recovery_command(_config: dict[str, Any]) -> list[str]:
    return python_command("telemetry-viewer\\context_service.py", "--latest-session", "--ensure-loaded-scene")


def build_live_processor_command(config: dict[str, Any], *, root: Path | None = None) -> list[str] | None:
    if not script_exists("telemetry-viewer\\live_target_processor.py", root):
        return None
    command = python_command(
        "telemetry-viewer\\live_target_processor.py",
        "--latest-session",
        "--input-source",
        "plugin-snapshot",
        "--profile",
        profile_from_config(config),
        "--follow",
        "--latency-mode",
        "realtime",
    )
    if script_supports_flag("telemetry-viewer\\live_target_processor.py", "--liveness-mode", root):
        command.extend(["--liveness-mode", "delta", "--liveness-budget-ms", "20"])
    command.extend(
        [
            "--no-startup-backfill",
            "--max-new-ticks-per-update",
            "1",
            "--candidate-output-window",
            "latest",
            "--window-ticks",
            "10",
            "--limit",
            "100",
            "--no-ui-targets",
            "--emit-world-targets",
            "candidates",
            "--drain-backlog-on-overrun",
            "--summary",
            "--benchmark",
            "--plugin-snapshot-tier",
            "hot",
            "--plugin-snapshot-projection-field-mode",
            "compact",
            "--plugin-snapshot-fallback",
            "none",
        ]
    )
    return command


def build_mcp_list_tools_command(_config: dict[str, Any]) -> list[str] | None:
    if not script_exists("telemetry-viewer\\mcp_server.py"):
        return None
    return python_command("telemetry-viewer\\mcp_server.py", "--list-tools")


def build_arduino_status_command(config: dict[str, Any]) -> list[str]:
    command = python_command("telemetry-viewer\\arduino_input_bridge.py", "--status", "--baud", str(config.get("arduino_baud") or "115200"))
    if str(config.get("arduino_port") or "").strip():
        command.extend(["--port", str(config.get("arduino_port")).strip()])
    return command


def build_arduino_bridge_command(config: dict[str, Any], *, out: str | Path | None = None) -> list[str]:
    command = python_command(
        "telemetry-viewer\\arduino_input_bridge.py",
        "--record-events",
        "--baud",
        str(config.get("arduino_baud") or "115200"),
        "--passthrough-mode",
        str(config.get("arduino_passthrough_mode") or "bridge"),
    )
    if str(config.get("arduino_port") or "").strip():
        command.extend(["--port", str(config.get("arduino_port")).strip()])
    if out:
        command.extend(["--out", str(out)])
    return command


def build_arduino_probe_command(config: dict[str, Any], *, out: str | Path | None = None) -> list[str]:
    output = out or (ui_control_dir() / "arduino_probe")
    command = python_command(
        "telemetry-viewer\\arduino_mirror_verifier.py",
        "--probe",
        "--baud",
        str(config.get("arduino_baud") or "115200"),
        "--move",
        str(config.get("arduino_probe_move_dx") or "12"),
        str(config.get("arduino_probe_move_dy") or "0"),
        "--observe-ms",
        str(config.get("arduino_probe_observe_ms") or "500"),
        "--out",
        str(output),
    )
    if bool(config.get("mirror_quiet_probe")):
        command.append("--quiet-window")
        command.extend(["--pre-observe-ms", "250", "--post-observe-ms", str(config.get("arduino_probe_observe_ms") or "500")])
    if str(config.get("arduino_port") or "").strip():
        command.extend(["--port", str(config.get("arduino_port")).strip()])
    return command


def build_live_mirror_test_command(config: dict[str, Any], *, out: str | Path | None = None) -> list[str]:
    output = Path(out) if out is not None else (ui_control_dir() / "arduino_live_mirror_test")
    try:
        test_duration = max(1.0, float(config.get("mirror_test_duration_sec") or 5))
    except (TypeError, ValueError):
        test_duration = 5.0
    try:
        arm_delay = max(0.0, float(config.get("mirror_test_arm_delay_ms") or 2000) / 1000.0)
    except (TypeError, ValueError):
        arm_delay = 2.0
    total_duration = str(int(math.ceil(test_duration + arm_delay + 2)))
    panic_file = output / "live_mirror_test.panic"
    command = python_command(
        "telemetry-viewer\\manual_recorder.py",
        "--label",
        "ui_live_mirror_test",
        "--duration",
        total_duration,
        "--out-dir",
        str(output),
        "--summary",
        "--capture-input",
        "--input-backend",
        str(config.get("input_backend") or "polling"),
        "--prefer-polling-input",
        "--capture-mouse",
        "--capture-window-context",
        "--join-input-telemetry",
        "--arduino-passthrough-mode",
        "mirror",
        "--arduino-probe",
        "--arduino-live-mirror",
        "--input-path-integrity",
        "--mirror-correlation-window-ms",
        "250",
        "--mirror-max-move-error-px",
        "100",
    )
    if str(config.get("arduino_port") or "").strip():
        command.extend(["--arduino-port", str(config.get("arduino_port")).strip()])
    command.extend(["--arduino-baud", str(config.get("arduino_baud") or "115200")])
    append_live_mirror_options(command, config, panic_stop_file=panic_file, ui_test=True)
    return command


def build_input_smoke_test_command(config: dict[str, Any], *, out: str | Path | None = None) -> list[str]:
    output = out or (ui_control_dir() / "input_smoke_test")
    command = python_command(
        "telemetry-viewer\\input_trace_recorder.py",
        "--smoke-test",
        "--backend",
        str(config.get("input_backend") or "polling"),
        "--duration",
        "8",
        "--out",
        str(output),
        "--capture-mouse",
        "--capture-keyboard",
        "--capture-window-context",
        "--json",
    )
    return command


def parse_positive_duration(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def safe_label(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value or "recording").strip())
    text = text.strip("._-")
    return text[:80] or "recording"


AUTO_LABEL_EXACT = {
    "manual_recording",
    "manual_action",
    "manual_action-menu_row_validation",
    "manual_action-menu_row_validation_live_mirror",
    "manual_action-menu_row_validation_live_mirror_controlled",
    "manual_action-tree_cutting",
    "manual_task-woodcutting",
}


def route_recording_label(route_name: str | None = None) -> str:
    route_part = safe_label(route_name or route_template.DEFAULT_ROUTE_NAME).lower()
    return f"manual_route-{route_part}"


def simple_recording_label(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"manual_recording_{stamp}"


def suggested_recording_label_for_preset(preset: str, *, route_name: str | None = None) -> str:
    if preset == PRESET_ROUTE:
        return route_recording_label(route_name)
    if preset == PRESET_MENU_ROW:
        return "manual_action-menu_row_validation"
    if preset == PRESET_LIVE_MIRROR_MENU_ROW:
        return "manual_action-menu_row_validation_live_mirror_controlled"
    if preset == PRESET_WOODCUTTING:
        return "manual_task-woodcutting"
    if preset == PRESET_BASIC:
        return "manual_recording"
    return "manual_recording"


def label_looks_auto_generated(label: Any) -> bool:
    text = str(label or "").strip()
    if not text:
        return True
    if text in AUTO_LABEL_EXACT:
        return True
    return text.startswith("manual_route-")


def apply_preset_recording_label(config: dict[str, Any], preset: str) -> dict[str, Any]:
    mode = str(config.get("recording_label_mode") or "").strip()
    current = str(config.get("last_recording_label") or "").strip()
    if mode != "custom" or not current:
        config["last_recording_label"] = suggested_recording_label_for_preset(
            preset,
            route_name=str(config.get("route_name") or route_template.DEFAULT_ROUTE_NAME),
        )
        config["recording_label_mode"] = "auto"
        config["recording_label_preset"] = preset
    return config


def new_recording_control_paths(label: str, *, base: Path | None = None, now: datetime | None = None) -> dict[str, Path]:
    base = base or ui_control_dir()
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    prefix = f"{stamp}_{safe_label(label)}"
    return {
        "stop_file": base / f"{prefix}.stop",
        "marker_file": base / f"{prefix}.markers",
        "mirror_panic_stop_file": base / f"{prefix}.mirror_panic",
    }


def append_live_mirror_options(
    command: list[str],
    config: dict[str, Any],
    *,
    panic_stop_file: str | Path | None = None,
    ui_test: bool = False,
) -> None:
    arm_mode = "test_window" if ui_test else str(config.get("mirror_arm_mode") or "recording_persistent")
    if not ui_test and bool(config.get("persistent_mirror_during_recording", True)):
        arm_mode = "recording_persistent"
    command.extend(["--mirror-profile", str(config.get("mirror_profile") or "full_live_mirror")])
    command.extend(["--mirror-click-policy", str(config.get("mirror_click_policy") or "live_unsuppressed")])
    if bool(config.get("require_click_source_suppression")):
        command.append("--require-click-source-suppression")
    if bool(config.get("allow_unsuppressed_live_clicks")):
        command.append("--allow-unsuppressed-live-clicks")
    if str(config.get("max_live_clicks_per_recording") or "0").strip() not in {"", "0"}:
        command.extend(["--max-live-clicks-per-recording", str(config.get("max_live_clicks_per_recording") or "0")])
    if bool(config.get("auto_disable_live_clicks_after_first_game_action")):
        command.append("--auto-disable-live-clicks-after-first-game-action")
    if bool(config.get("mirror_disable_movement")):
        command.append("--mirror-disable-movement")
    if bool(config.get("mirror_disable_clicks")):
        command.append("--mirror-disable-clicks")
    if bool(config.get("mirror_echo_suppression")):
        command.append("--mirror-echo-suppression")
    command.extend(["--mirror-echo-window-ms", str(config.get("mirror_echo_window_ms") or "250")])
    command.extend(["--mirror-click-echo-window-ms", str(config.get("mirror_click_echo_window_ms") or "300")])
    command.extend(["--mirror-echo-max-error-px", str(config.get("mirror_echo_max_error_px") or "100")])
    command.extend(["--mirror-max-queue-size", str(config.get("mirror_max_queue_size") or "25")])
    command.extend(["--mirror-drop-move-older-than-ms", str(config.get("mirror_drop_move_older_than_ms") or "150")])
    if bool(config.get("mirror_clear_queue_on_game_action")):
        command.append("--mirror-clear-queue-on-game-action")
    if bool(config.get("mirror_clear_queue_on_menu_selection")):
        command.append("--mirror-clear-queue-on-menu-selection")
    if bool(config.get("mirror_clear_queue_on_plane_change")):
        command.append("--mirror-clear-queue-on-plane-change")
    if bool(config.get("mirror_clear_queue_on_target_action")):
        command.append("--mirror-clear-queue-on-target-action")
    if bool(config.get("mirror_auto_pause_after_first_game_action")):
        command.append("--mirror-auto-pause-after-first-game-action")
    if bool(config.get("mirror_auto_pause_after_menu_selection")):
        command.append("--mirror-auto-pause-after-menu-selection")
    if bool(config.get("mirror_auto_pause_after_plane_change")):
        command.append("--mirror-auto-pause-after-plane-change")
    command.extend(["--mirror-auto-pause-after-target-quality", str(config.get("mirror_auto_pause_after_target_quality") or "off")])
    command.extend(["--mirror-validation-mode", str(config.get("mirror_validation_mode") or "custom")])
    command.extend(["--mirror-move-min-px", str(config.get("mirror_move_min_px") or "1")])
    command.extend(["--mirror-max-step-px", str(config.get("mirror_max_step_px") or "25")])
    command.extend(["--mirror-send-interval-ms", str(config.get("mirror_send_interval_ms") or "5")])
    command.extend(["--mirror-scale-x", str(config.get("mirror_scale_x") or "1.0")])
    command.extend(["--mirror-scale-y", str(config.get("mirror_scale_y") or "1.0")])
    if bool(config.get("mirror_invert_x")):
        command.append("--mirror-invert-x")
    if bool(config.get("mirror_invert_y")):
        command.append("--mirror-invert-y")
    command.extend(["--mirror-button-mode", str(config.get("mirror_button_mode") or "click")])
    command.extend(["--mirror-max-clicks-per-second", str(config.get("mirror_max_clicks_per_second") or "4")])
    command.extend(["--mirror-max-button-commands-per-second", str(config.get("mirror_max_button_commands_per_second") or "8")])
    command.extend(["--mirror-max-move-commands-per-second", str(config.get("mirror_max_move_commands_per_second") or "120")])
    command.extend(["--mirror-max-total-commands-per-second", str(config.get("mirror_max_total_commands_per_second") or "150")])
    command.extend(["--mirror-click-cooldown-ms", str(config.get("mirror_click_cooldown_ms") or "120")])
    command.extend(["--mirror-same-button-cooldown-ms", str(config.get("mirror_same_button_cooldown_ms") or "80")])
    command.extend(["--mirror-max-burst-commands", str(config.get("mirror_max_burst_commands") or "50")])
    command.extend(["--mirror-panic-command-threshold", str(config.get("mirror_panic_command_threshold") or "100")])
    command.extend(["--mirror-panic-window-ms", str(config.get("mirror_panic_window_ms") or "1000")])
    arm_delay_value = (config.get("mirror_test_arm_delay_ms") or "2000") if ui_test else (config.get("mirror_arm_delay_ms") or "500")
    command.extend(["--mirror-arm-delay-ms", str(arm_delay_value)])
    command.extend(["--mirror-arm-mode", arm_mode])
    if ui_test or arm_mode == "test_window":
        command.extend(["--mirror-test-duration-sec", str(config.get("mirror_test_duration_sec") or "5")])
    elif bool(config.get("mirror_persist_until_stop", True)) or bool(config.get("persistent_mirror_during_recording", True)):
        command.append("--mirror-persist-until-stop")
        command.append("--mirror-keep-armed-while-recording")
    if bool(config.get("mirror_disarm_on_focus_lost")):
        command.append("--mirror-disarm-on-focus-lost")
    command.extend(["--mirror-window-title-allow", str(config.get("mirror_window_title_allow") or "RuneLite")])
    command.extend(["--mirror-exclude-window-title", str(config.get("mirror_exclude_window_title") or "OSRS Telemetry Control")])
    command.extend(["--mirror-region", str(config.get("mirror_region") or "client")])
    if bool(config.get("mirror_arm_only_when_runelite_focused")):
        command.append("--mirror-arm-only-when-runelite-focused")
    if bool(config.get("mirror_ignore_ui_clicks", True)):
        command.append("--mirror-ignore-ui-clicks")
    if bool(config.get("mirror_quiet_probe")):
        command.append("--mirror-quiet-probe")
    if panic_stop_file:
        command.extend(["--mirror-panic-stop-file", str(panic_stop_file)])


def build_recorder_command(
    config: dict[str, Any],
    *,
    stop_file: str | Path,
    marker_file: str | Path,
    mirror_panic_stop_file: str | Path | None = None,
) -> list[str]:
    label = str(config.get("last_recording_label") or "manual_action").strip() or "manual_action"
    description = str(config.get("last_recording_description") or "").strip()
    command = python_command(
        "telemetry-viewer\\manual_recorder.py",
        "--label",
        label,
        "--out-dir",
        str(resolve_output_folder(config)),
        "--poll-interval-ms",
        str(config.get("poll_interval_ms") or "50"),
        "--stop-file",
        str(stop_file),
        "--marker-file",
        str(marker_file),
        "--summary",
    )
    if description:
        command.extend(["--description", description])
    duration = parse_positive_duration(config.get("duration"))
    if duration is None:
        command.append("--until-stopped")
    else:
        command.extend(["--duration", str(duration)])
    if bool(config.get("latest_session", True)):
        command.append("--latest-session")
        if bool(config.get("prefer_active_session", True)):
            command.append("--prefer-active-session")
    elif str(config.get("last_session_path") or "").strip():
        command.extend(["--session", str(config.get("last_session_path")).strip()])
    if str(config.get("sources_override") or "").strip():
        command.extend(["--sources", str(config.get("sources_override")).strip()])
    if bool(config.get("include_raw")):
        command.append("--include-raw")
    if bool(config.get("preserve_bank_ui", True)):
        command.append("--preserve-bank-ui")
        command.extend(["--plugin-snapshot-url", str(config.get("plugin_snapshot_url") or telemetry_sources.DEFAULT_PLUGIN_SNAPSHOT_URL)])
        command.extend(["--plugin-snapshot-timeout", str(config.get("plugin_snapshot_timeout") or telemetry_sources.DEFAULT_PLUGIN_SNAPSHOT_TIMEOUT_SECONDS)])
    else:
        command.append("--no-preserve-bank-ui")
    if bool(config.get("preserve_combat_state", True)):
        command.append("--preserve-combat-state")
    else:
        command.append("--no-preserve-combat-state")
    if bool(config.get("pretty")):
        command.append("--pretty")
    if bool(config.get("telemetry_preflight")):
        command.append("--telemetry-preflight")
        command.extend(["--telemetry-preflight-seconds", str(config.get("telemetry_preflight_seconds") or "5")])
        command.extend(["--max-telemetry-age-ms", str(config.get("max_telemetry_age_ms") or "3000")])
        if bool(config.get("wait_for_fresh_telemetry")):
            command.append("--wait-for-fresh-telemetry")
            command.extend(["--wait-for-fresh-telemetry-timeout", str(config.get("wait_for_fresh_telemetry_timeout") or "30")])
    if bool(config.get("menu_capture_burst")):
        command.append("--menu-capture-burst")
        if bool(config.get("menu_burst_until_selection")):
            command.append("--menu-burst-until-selection")
        command.extend(["--menu-burst-tail-ms", str(config.get("menu_burst_tail_ms") or "500")])
        command.extend(["--menu-burst-max-ms", str(config.get("menu_burst_max_ms") or "4000")])
        command.extend(["--menu-burst-ms", str(config.get("menu_burst_ms") or "2000")])
        command.extend(["--menu-burst-poll-ms", str(config.get("menu_burst_poll_ms") or "15")])
    if bool(config.get("capture_input")):
        command.append("--capture-input")
        command.extend(["--input-backend", str(config.get("input_backend") or "polling")])
        if bool(config.get("prefer_polling_input", True)):
            command.append("--prefer-polling-input")
        if bool(config.get("input_preflight", True)):
            command.append("--input-preflight")
            command.extend(["--input-preflight-seconds", str(config.get("input_preflight_seconds") or "5")])
        if bool(config.get("fail_if_input_preflight_fails")):
            command.append("--fail-if-input-preflight-fails")
        if bool(config.get("capture_mouse", True)):
            command.append("--capture-mouse")
        if bool(config.get("capture_keyboard")):
            command.append("--capture-keyboard")
        command.append("--capture-window-context")
        if bool(config.get("raw_input_device_attribution")):
            command.append("--raw-input-device-attribution")
    if bool(config.get("join_input_telemetry")):
        command.append("--join-input-telemetry")
        command.append("--input-summary")
    if bool(config.get("camera_behavior_analysis")):
        command.append("--camera-behavior")
    if bool(config.get("arduino_enabled")) or bool(config.get("arduino_auto_start_on_recording")):
        command.append("--arduino")
    if bool(config.get("arduino_required_for_recording")):
        command.append("--arduino-required")
    if bool(config.get("arduino_auto_start_on_recording")):
        command.append("--arduino-auto-start")
    if bool(config.get("arduino_enabled")) or bool(config.get("arduino_auto_start_on_recording")):
        command.append("--arduino-record-events")
    if str(config.get("arduino_port") or "").strip():
        command.extend(["--arduino-port", str(config.get("arduino_port")).strip()])
    if str(config.get("arduino_baud") or "").strip():
        command.extend(["--arduino-baud", str(config.get("arduino_baud")).strip()])
    if str(config.get("arduino_passthrough_mode") or "off") != "off":
        command.extend(["--arduino-passthrough-mode", str(config.get("arduino_passthrough_mode") or "bridge")])
    arduino_mode = str(config.get("arduino_passthrough_mode") or "off")
    probe_requested = bool(config.get("arduino_probe")) and (
        bool(config.get("arduino_enabled"))
        or bool(config.get("arduino_auto_start_on_recording"))
        or arduino_mode == "mirror"
    )
    if probe_requested or arduino_mode == "mirror":
        command.append("--arduino-probe")
        command.extend(
            [
                "--arduino-probe-move",
                str(config.get("arduino_probe_move_dx") or "12"),
                str(config.get("arduino_probe_move_dy") or "0"),
                "--arduino-probe-observe-ms",
                str(config.get("arduino_probe_observe_ms") or "500"),
            ]
        )
    if bool(config.get("require_arduino_probe_verified")) and (probe_requested or arduino_mode == "mirror"):
        command.append("--require-arduino-probe-verified")
    if arduino_mode == "mirror" or bool(config.get("arduino_mirror_preflight")):
        command.append("--arduino-mirror-preflight")
    if bool(config.get("require_arduino_mirror_verified")):
        command.append("--require-arduino-mirror-verified")
    if arduino_mode == "mirror" and bool(config.get("arduino_live_mirror")):
        command.append("--arduino-live-mirror")
        append_live_mirror_options(command, config, panic_stop_file=mirror_panic_stop_file)
        if bool(config.get("require_live_mirror_active")):
            command.append("--require-live-mirror-active")
        if bool(config.get("require_live_mirror_verified")):
            command.append("--require-live-mirror-verified")
    if bool(config.get("input_path_integrity", True)):
        command.append("--input-path-integrity")
    if str(config.get("arduino_calibration_profile") or "").strip():
        command.extend(["--arduino-calibration", str(config.get("arduino_calibration_profile")).strip()])
    if bool(config.get("vm_mouse_mapping")):
        command.append("--vm-mouse-mapping")
        command.append("--write-arduino-mapping")
    return command


def list_recording_dirs(root: Path | None = None) -> list[Path]:
    base = recordings_root(root)
    if not base.exists():
        return []
    return sorted([path for path in base.iterdir() if path.is_dir()], key=lambda path: path.stat().st_mtime)


def latest_recording_dir(root: Path | None = None) -> Path | None:
    recordings = list_recording_dirs(root)
    return recordings[-1] if recordings else None


def latest_recording_dir_for_config(config: dict[str, Any] | None = None, *, root: Path | None = None) -> Path | None:
    base = resolve_output_folder(config, root=root)
    if not base.exists():
        return None
    recordings = sorted([path for path in base.iterdir() if path.is_dir()], key=lambda path: path.stat().st_mtime)
    return recordings[-1] if recordings else None


def build_analyzer_command(recording: str | Path | None, config: dict[str, Any] | None = None) -> list[str] | None:
    if not recording:
        return None
    command = python_command("telemetry-viewer\\analyze_manual_recording.py", str(recording), "--summary", "--schema-gap", "--woodcutting-lifecycle", "--woodcutting-loop-lifecycle", "--banking-lifecycle", "--interruption-lifecycle", "--combat-damage-summary")
    if config is None or bool(config.get("traversal_lifecycle_analysis", True)):
        command.append("--traversal-lifecycle")
        command.append("--group-traversal-steps")
        command.append("--auto-route-template")
    if config is None or bool(config.get("join_input_telemetry", True)):
        command.append("--input-trace")
        command.append("--join-input")
        command.append("--human-input-summary")
        command.append("--classify-input-actions")
        command.append("--target-match-quality")
        command.append("--menu-interactions")
        command.append("--menu-row-diagnostics")
        command.append("--coordinate-alignment")
        command.append("--input-path-integrity")
        command.append("--arduino-mirror-verification")
    if config is None or bool(config.get("camera_behavior_analysis", True)):
        command.append("--camera-behavior")
    if config is None or bool(config.get("human_click_profile_analysis", True)):
        command.append("--human-click-profile")
    if config is None or bool(config.get("arduino_enabled")):
        command.append("--arduino-trace")
    if config is None or bool(config.get("vm_mouse_mapping")):
        command.append("--vm-mouse-mapping")
    if config is not None and (bool(config.get("route_monitor_enabled")) or bool(config.get("route_history_enabled")) or str(config.get("selected_preset")) == PRESET_ROUTE):
        template_path = resolved_route_template_path(config)
        if template_path and not use_auto_route_template(config):
            command.extend(["--compare-route-template", template_path])
        if bool(config.get("route_monitor_enabled", True)):
            command.append("--route-monitor")
            if template_path and not use_auto_route_template(config):
                command.extend(["--route-monitor-template", template_path])
        if bool(config.get("route_history_enabled", True)):
            command.append("--route-history")
    if config is None or bool(config.get("update_project_knowledge", True)):
        command.append("--update-knowledge")
    return command


def build_project_knowledge_command(config: dict[str, Any] | None = None, *, write_docs: bool = True) -> list[str] | None:
    if not script_exists("telemetry-viewer\\update_project_knowledge.py"):
        return None
    command = python_command("telemetry-viewer\\update_project_knowledge.py", "--scan-recordings")
    if write_docs:
        command.append("--write-docs")
    command.append("--json")
    cfg = config if isinstance(config, dict) else {}
    out_dir = str(cfg.get("knowledge_out_dir") or "").strip()
    if out_dir:
        command.extend(["--knowledge-out", out_dir])
    return command


def build_bootstrap_check_command() -> list[str] | None:
    if not script_exists("telemetry-viewer\\update_project_knowledge.py"):
        return None
    return python_command("telemetry-viewer\\update_project_knowledge.py", "--check", "--json")


def build_command_registry_check_command() -> list[str] | None:
    if not script_exists("telemetry-viewer\\command_registry.py"):
        return None
    return python_command("telemetry-viewer\\command_registry.py", "--check", "--json")


def build_bot_eval_preflight_command() -> list[str] | None:
    if not script_exists("telemetry-viewer\\bot_eval_runner.py"):
        return None
    return python_command("telemetry-viewer\\bot_eval_runner.py", "--task", "woodcutting_loop", "--preflight", "--json")


def build_input_geometry_check_command() -> list[str] | None:
    if not script_exists("telemetry-viewer\\bot_eval_runner.py"):
        return None
    return python_command("telemetry-viewer\\bot_eval_runner.py", "--task", "woodcutting_loop", "--check-input-geometry", "--json")


def build_human_click_profile_command(config: dict[str, Any] | None = None, recordings: list[str | Path] | None = None) -> list[str] | None:
    if not script_exists("telemetry-viewer\\human_click_profile.py"):
        return None
    cfg = config if isinstance(config, dict) else {}
    selected = recordings or []
    if not selected:
        latest = latest_recording_dir_for_config(cfg)
        if latest:
            selected = [latest]
    if not selected:
        return None
    out = repo_root() / "telemetry-viewer" / "knowledge_base" / "human_click_profile.json"
    markdown = repo_root() / "docs" / "human_click_profile.md"
    command = python_command("telemetry-viewer\\human_click_profile.py", "--recordings")
    command.extend(str(path) for path in selected)
    command.extend(["--out", str(out), "--markdown", str(markdown)])
    return command


def build_extract_route_template_command(recording: str | Path | None, config: dict[str, Any] | None = None) -> list[str] | None:
    command = build_analyzer_command(recording, config)
    if not command:
        return None
    cfg = config if isinstance(config, dict) else {}
    command.append("--extract-route-template")
    command.extend(["--route-template-out", str(cfg.get("route_template_out_dir") or "route_templates")])
    command.append("--print-route-segments")
    return command


def build_compare_route_template_command(recording: str | Path | None, config: dict[str, Any] | None = None) -> list[str] | None:
    command = build_analyzer_command(recording, config)
    cfg = config if isinstance(config, dict) else {}
    template_path = resolved_route_template_path(cfg)
    if not command or not template_path:
        return None
    if "--compare-route-template" not in command:
        command.extend(["--compare-route-template", template_path])
    command.append("--print-route-template-comparison")
    return command


def build_register_route_variant_command(recording: str | Path | None, config: dict[str, Any] | None = None) -> list[str] | None:
    command = build_analyzer_command(recording, config)
    cfg = config if isinstance(config, dict) else {}
    template_path = resolved_route_template_path(cfg)
    if not command or not template_path:
        return None
    variant_name = str(cfg.get("route_variant_name") or "route_variant").strip() or "route_variant"
    if "--compare-route-template" not in command:
        command.extend(["--compare-route-template", template_path])
    command.extend(
        [
            "--extract-route-variant",
            "--route-variant-name",
            variant_name,
            "--add-route-variant-to-template",
            template_path,
            "--print-route-variant",
        ]
    )
    return command


def build_route_readiness_command(config: dict[str, Any] | None = None) -> list[str] | None:
    cfg = config if isinstance(config, dict) else {}
    template_arg = "auto" if use_auto_route_template(cfg) else resolved_route_template_path(cfg)
    if not template_arg:
        return None
    command = python_command("telemetry-viewer\\route_monitor.py", "--template", template_arg, "--live", "--json")
    if bool(cfg.get("latest_session", True)):
        command.append("--latest-session")
    else:
        session_path = str(cfg.get("last_session_path") or "").strip()
        if session_path:
            command.extend(["--session", session_path])
    sources = str(cfg.get("sources_override") or "").strip()
    if sources:
        command.extend(["--sources", sources])
    session_id = str(cfg.get("route_session_id") or "").strip()
    if session_id:
        command.extend(["--session-id", session_id])
    return command


def build_route_history_follow_command(config: dict[str, Any] | None = None) -> list[str] | None:
    cfg = config if isinstance(config, dict) else {}
    template_arg = "auto" if use_auto_route_template(cfg) else resolved_route_template_path(cfg)
    if not template_arg:
        return None
    out_dir = Path.home() / ".osrs-telemetry" / "route_monitor"
    command = python_command(
        "telemetry-viewer\\route_monitor.py",
        "--template",
        template_arg,
        "--live",
        "--follow",
        "--poll-ms",
        "250",
        "--out-dir",
        str(out_dir),
    )
    if bool(cfg.get("latest_session", True)):
        command.append("--latest-session")
    else:
        session = str(cfg.get("last_session_path") or "").strip()
        if session:
            command.extend(["--session", session])
    sources = str(cfg.get("sources_override") or "").strip()
    if sources:
        command.extend(["--sources", sources])
    session_id = str(cfg.get("route_session_id") or "").strip()
    if session_id:
        command.extend(["--session-id", session_id])
    return command


def build_route_monitor_recording_command(recording: str | Path | None, config: dict[str, Any] | None = None) -> list[str] | None:
    if not recording:
        return None
    cfg = config if isinstance(config, dict) else {}
    template_arg = "auto" if use_auto_route_template(cfg) else resolved_route_template_path(cfg)
    if not template_arg:
        return None
    return python_command("telemetry-viewer\\route_monitor.py", "--template", template_arg, "--recording", str(recording), "--json")


def build_route_session_plan(route_name_or_template: str | Path | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config_for_preset(config or default_config(), PRESET_ROUTE)
    if route_name_or_template:
        cfg["route_template_path"] = str(route_name_or_template)
        resolution = route_template.resolve_route_template(route_name_or_template, root=repo_root())
    else:
        resolution = resolve_template_from_config(cfg)
    public_resolution = public_template_resolution(resolution)
    if resolution.get("status") == "PASS":
        cfg["route_template_path"] = str(resolution.get("resolvedPath") or "")
        cfg["route_name"] = str(resolution.get("routeName") or route_template.DEFAULT_ROUTE_NAME)
        cfg["route_template_revision"] = resolution.get("templateRevision")
        cfg["route_required_segment_count"] = resolution.get("requiredSegmentCount")
        cfg["route_template_mode"] = "explicit"
        apply_preset_recording_label(cfg, PRESET_ROUTE)
    warnings = list(resolution.get("warnings") or [])
    can_start = resolution.get("status") == "PASS"
    session_id = "route_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    cfg["route_session_id"] = session_id
    label = str(cfg.get("last_recording_label") or "").strip() or route_recording_label(str(cfg.get("route_name") or route_template.DEFAULT_ROUTE_NAME))
    cfg["last_recording_label"] = label
    control_paths = new_recording_control_paths(label)
    monitor_command = build_route_history_follow_command(cfg) if can_start else None
    recorder_command = (
        build_recorder_command(
            cfg,
            stop_file=control_paths["stop_file"],
            marker_file=control_paths["marker_file"],
            mirror_panic_stop_file=control_paths.get("mirror_panic_stop_file"),
        )
        if can_start
        else None
    )
    monitor_folder = Path.home() / ".osrs-telemetry" / "route_monitor" / str(resolution.get("routeName") or "route") / session_id
    return {
        "schema": "route_session_plan.v1",
        "status": "PASS" if can_start else "FAIL",
        "canStart": can_start,
        "templateResolution": public_resolution,
        "routeName": resolution.get("routeName"),
        "templateRevision": resolution.get("templateRevision"),
        "templatePath": resolution.get("resolvedPath"),
        "requiredSegmentCount": resolution.get("requiredSegmentCount"),
        "sessionId": session_id,
        "routeMonitorCommand": monitor_command,
        "recorderCommand": recorder_command,
        "analyzerCommand": None,
        "presetName": PRESET_ROUTE,
        "recordingLabel": label,
        "routeMonitorSessionFolder": str(monitor_folder),
        "recordingControlPaths": {key: str(value) for key, value in control_paths.items()},
        "warnings": warnings,
        "config": cfg,
    }


def route_template_status_text(config: dict[str, Any]) -> str:
    resolution = resolve_template_from_config(config)
    if resolution.get("status") == "PASS":
        return f"Template loaded: {resolution.get('routeName')} rev {resolution.get('templateRevision')}"
    return "Template missing: " + "; ".join(str(item) for item in resolution.get("warnings") or ["unable to resolve"])


def ui_recording_session_dir() -> Path:
    return ui_control_dir() / "recording_session"


def ui_recording_session_manifest_path() -> Path:
    return ui_recording_session_dir() / "ui_recording_session_manifest.json"


def read_ui_recording_manifest(path: str | Path | None = None) -> dict[str, Any]:
    manifest_path = Path(path) if path else ui_recording_session_manifest_path()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_ui_recording_manifest(payload: dict[str, Any], path: str | Path | None = None) -> Path:
    manifest_path = Path(path) if path else ui_recording_session_manifest_path()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    telemetry_sources.atomic_write_json(manifest_path, payload, pretty=True)
    return manifest_path


def _recording_folder_manifest_path(payload: dict[str, Any]) -> Path | None:
    recording_folder = str(payload.get("recordingFolder") or "").strip()
    if not recording_folder:
        return None
    folder = Path(recording_folder)
    if not folder.is_dir():
        return None
    return folder / "ui_recording_session_manifest.json"


def mirror_ui_recording_manifest_to_recording(payload: dict[str, Any], *, source_path: str | Path | None = None) -> Path | None:
    manifest_path = _recording_folder_manifest_path(payload)
    if manifest_path is None:
        return None
    if source_path is not None:
        try:
            if manifest_path.resolve() == Path(source_path).resolve():
                return manifest_path
        except OSError:
            pass
    return write_ui_recording_manifest(payload, manifest_path)


def update_ui_recording_manifest(updates: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    payload = read_ui_recording_manifest(path)
    payload.update(updates)
    manifest_path = write_ui_recording_manifest(payload, path)
    mirror_ui_recording_manifest_to_recording(payload, source_path=manifest_path)
    return payload


def woodcutting_loop_simple_phase_summary(lifecycle: dict[str, Any]) -> str:
    if not isinstance(lifecycle, dict) or not lifecycle:
        return ""
    phases = {_safe_dict(item).get("phase"): _safe_dict(item) for item in lifecycle.get("detectedPhases") or []}
    routes = _safe_dict(lifecycle.get("routes"))
    route_legs = {_safe_dict(item).get("phase"): _safe_dict(item) for item in routes.get("routeLegs") or []}
    route_legs_by_direction = {_safe_dict(item).get("direction"): _safe_dict(item) for item in routes.get("routeLegs") or []}

    def status(*phase_names: str, default: str = "not observed") -> str:
        for phase_name in phase_names:
            phase = phases.get(phase_name)
            if phase:
                return str(phase.get("status") or "PASS")
        return default

    def route_suffix(phase_name: str, direction: str) -> str:
        leg = route_legs.get(phase_name) or route_legs_by_direction.get(direction)
        route_name = str(leg.get("routeName") or direction).strip() if leg else ""
        return f", {route_name}" if route_name else ""

    parts = [
        f"Woodcutting: {status('cutting', 'at_trees')}",
        f"Route to Bank: {status('routing_to_bank')}{route_suffix('route_to_bank', 'woodcutting_area_to_bank')}",
        f"Banking: {status('banking')}",
        f"Route to Trees: {status('routing_to_trees')}{route_suffix('route_to_trees', 'bank_to_woodcutting_area')}",
        f"Resume Cutting: {status('resumed_cutting')}",
    ]
    return "; ".join(parts)


def detected_activity_type(summary: dict[str, Any]) -> str:
    traversal = summary.get("traversal_lifecycle") if isinstance(summary.get("traversal_lifecycle"), dict) else {}
    woodcutting = summary.get("woodcutting_lifecycle") if isinstance(summary.get("woodcutting_lifecycle"), dict) else {}
    woodcutting_loop = summary.get("woodcutting_loop_lifecycle") if isinstance(summary.get("woodcutting_loop_lifecycle"), dict) else {}
    banking = summary.get("banking_lifecycle") if isinstance(summary.get("banking_lifecycle"), dict) else {}
    menu = summary.get("menu_interaction_summary") if isinstance(summary.get("menu_interaction_summary"), dict) else {}
    camera = summary.get("camera_behavior") if isinstance(summary.get("camera_behavior"), dict) else {}
    action = summary.get("input_action_summary") if isinstance(summary.get("input_action_summary"), dict) else {}
    traversal_route_name = str(traversal.get("routeName") or "").strip()
    traversal_segment_count = _safe_int(traversal.get("routeSegmentCount")) if traversal else 0
    traversal_start_area = str(_safe_dict(traversal.get("start")).get("areaLabel") or "") if traversal else ""
    traversal_end_area = str(_safe_dict(traversal.get("end")).get("areaLabel") or "") if traversal else ""
    label_text = " ".join(
        str(summary.get(key) or "")
        for key in ("label", "description", "recording_id", "recording_path")
    ).lower()
    woodcutting_phase = str(woodcutting.get("phase") or "").lower() if woodcutting else ""
    woodcutting_inventory = _safe_dict(woodcutting.get("inventory")) if woodcutting else {}
    loop_state = str(woodcutting_loop.get("loopState") or "").lower() if woodcutting_loop else ""
    loop_phases = woodcutting_loop.get("detectedPhases") if isinstance(woodcutting_loop.get("detectedPhases"), list) else []
    if woodcutting_loop and loop_state not in {"", "unknown"} and loop_phases:
        return "Woodcutting Loop"
    if woodcutting and (
        woodcutting_phase not in {"", "idle", "unknown"}
        or _safe_int(woodcutting_inventory.get("normalLogsGained")) > 0
        or _safe_int(_safe_dict(woodcutting.get("clicks")).get("freshChopClickCount")) > 0
    ):
        return "Woodcutting"
    bank_label_signal = any(token in label_text for token in ("deposit", "opening bank", "open bank", "banking"))
    bank_action_signal = _safe_int(action.get("bankInteractionCount") if action else 0) > 0
    route_is_named = bool(traversal_route_name and traversal_route_name != "route_unknown")
    route_changes_area = bool(traversal_start_area and traversal_end_area and traversal_start_area != traversal_end_area)
    banking_deposit = bool(_safe_dict(banking.get("deposit")).get("detected")) if banking else False
    banking_withdraw = bool(_safe_dict(banking.get("withdraw")).get("detected")) if banking else False
    banking_signal = bool(
        banking
        and (
            banking.get("status") not in (None, "FAIL")
            or banking_deposit
            or banking_withdraw
            or _safe_dict(banking.get("bank")).get("openSeen")
            or _safe_dict(banking.get("bank")).get("targetEvidence")
        )
    )
    if (bank_action_signal or bank_label_signal) and not route_is_named:
        return "Banking"
    if banking_signal and not route_is_named:
        return "Banking"
    if traversal and (route_is_named or (traversal_segment_count >= 3 and route_changes_area)):
        return "Route / Traversal"
    if woodcutting and (woodcutting.get("status") or woodcutting.get("phase") or woodcutting.get("treeActionCount")):
        return "Woodcutting"
    if bank_action_signal or bank_label_signal:
        return "Banking"
    if _safe_int(menu.get("menuSelectionCount") if menu else 0) > 0:
        return "Menu Interaction"
    if camera and _safe_int(camera.get("totalCameraSegments")) > 0:
        return "Input / Camera Sample"
    if summary.get("input_trace") or summary.get("input_action_summary"):
        return "Human Input Sample"
    return "Generic Telemetry"


def biggest_analysis_warning(summary: dict[str, Any]) -> str:
    warnings: list[str] = []
    raw = summary.get("warnings")
    if isinstance(raw, list):
        warnings.extend(str(item) for item in raw[:3])
    for key in (
        "traversal_lifecycle",
        "route_template_comparison",
        "route_monitor",
        "route_history",
        "banking_lifecycle",
        "interruption_lifecycle",
        "combat_damage_summary",
        "woodcutting_lifecycle",
        "menu_interaction_summary",
        "target_match_summary",
        "input_path_integrity_summary",
    ):
        section = summary.get(key) if isinstance(summary.get(key), dict) else {}
        raw_section_warnings = section.get("warnings") if isinstance(section, dict) else None
        if isinstance(raw_section_warnings, list):
            warnings.extend(str(item) for item in raw_section_warnings[:2])
    return warnings[0] if warnings else "none"


def analysis_progress_text(state: str, elapsed_seconds: float | int | None = None, detail: str | None = None) -> str:
    base = {
        "stopping": "stopping recorder",
        "analyzing": "analyzing",
        "reading_report": "reading report",
        "complete": "complete",
        "failed": "failed",
        "timeout": "analysis timed out",
    }.get(state, state or "idle")
    if elapsed_seconds is not None and state == "analyzing":
        base = f"Analyzing... {int(elapsed_seconds)}s"
    if detail:
        return f"{base}: {detail}"
    return base


def analyzer_timed_out(started_monotonic: float | None, now_monotonic: float, timeout_seconds: float | int | None) -> bool:
    if started_monotonic is None:
        return False
    try:
        timeout = float(timeout_seconds if timeout_seconds is not None else ANALYZER_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = float(ANALYZER_TIMEOUT_SECONDS)
    return timeout > 0 and (now_monotonic - started_monotonic) >= timeout


def safe_analysis_result(recording: str | Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": "ui_analysis_result.v1",
        "recordingFolder": str(recording) if recording else None,
        "summaryStatus": None,
        "detectedActivityType": "Unknown",
        "verdict": "WARN",
        "reportPath": None,
        "biggestWarning": "summary.json is missing",
        "summaryPresent": False,
    }
    if not recording:
        result["biggestWarning"] = "no recording folder found"
        return result
    recording_path = Path(recording)
    summary_path = recording_path / "summary.json"
    report_path = recording_path / "schema_gap_report.md"
    if not report_path.exists():
        report_path = summary_path
    result["reportPath"] = str(report_path)
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return result
    except (OSError, json.JSONDecodeError) as error:
        result["biggestWarning"] = f"summary.json could not be read: {type(error).__name__}: {error}"
        return result
    if not isinstance(summary, dict):
        result["biggestWarning"] = "summary.json did not contain an object"
        return result
    mirror_timing = summary.get("mirror_action_timing") if isinstance(summary.get("mirror_action_timing"), dict) else {}
    result.update(
        {
            "summaryPresent": True,
            "summaryStatus": summary.get("status"),
            "detectedActivityType": detected_activity_type(summary),
            "verdict": str(mirror_timing.get("finalMirrorRecordingVerdict") or summary.get("status") or "WARN"),
            "biggestWarning": biggest_analysis_warning(summary),
        }
    )
    return result


def ensure_stop_file(path: str | Path) -> Path:
    stop_path = Path(path).expanduser()
    stop_path.parent.mkdir(parents=True, exist_ok=True)
    stop_path.write_text(f"stop requested at {utc_now()}\n", encoding="utf-8")
    return stop_path


def append_marker_line(path: str | Path, label: str | None = None) -> Path:
    marker_path = Path(path).expanduser()
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    text = label.strip() if isinstance(label, str) and label.strip() else "ui_marker"
    with marker_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{utc_now()} {text}\n")
    return marker_path


def woodcutting_lifecycle_status_text(recording: str | Path | None) -> str | None:
    if not recording:
        return None
    summary_path = Path(recording) / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return None
    lifecycle = summary.get("woodcutting_lifecycle") if isinstance(summary.get("woodcutting_lifecycle"), dict) else {}
    if not lifecycle:
        return None
    inventory = lifecycle.get("inventory") if isinstance(lifecycle.get("inventory"), dict) else {}
    clicks = lifecycle.get("clicks") if isinstance(lifecycle.get("clicks"), dict) else {}
    return (
        "woodcutting lifecycle: "
        f"phase={lifecycle.get('phase')} "
        f"confidence={lifecycle.get('confidence')} "
        f"logs_gained={inventory.get('normalLogsGained')} "
        f"inventory_full={inventory.get('inventoryFull')} "
        f"fresh_chop_clicks={clicks.get('freshChopClickCount')}"
    )


def latest_trace_status_text(recording: str | Path | None) -> tuple[str | None, str | None]:
    if not recording:
        return None, None
    summary_path = Path(recording) / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return None, None
    input_trace = summary.get("input_trace") if isinstance(summary.get("input_trace"), dict) else {}
    action = summary.get("input_action_summary") if isinstance(summary.get("input_action_summary"), dict) else {}
    target_quality = summary.get("target_match_summary") if isinstance(summary.get("target_match_summary"), dict) else {}
    menu_interaction = summary.get("menu_interaction_summary") if isinstance(summary.get("menu_interaction_summary"), dict) else {}
    coordinate = summary.get("coordinate_alignment_summary") if isinstance(summary.get("coordinate_alignment_summary"), dict) else {}
    traversal = summary.get("traversal_lifecycle") if isinstance(summary.get("traversal_lifecycle"), dict) else {}
    route_comparison = summary.get("route_template_comparison") if isinstance(summary.get("route_template_comparison"), dict) else {}
    route_monitor = summary.get("route_monitor") if isinstance(summary.get("route_monitor"), dict) else {}
    route_history = summary.get("route_history") if isinstance(summary.get("route_history"), dict) else {}
    input_path = summary.get("input_path_integrity_summary") if isinstance(summary.get("input_path_integrity_summary"), dict) else {}
    live_mirror = summary.get("arduino_live_mirror") if isinstance(summary.get("arduino_live_mirror"), dict) else {}
    arduino = summary.get("arduino_trace") if isinstance(summary.get("arduino_trace"), dict) else {}
    input_text = None
    arduino_text = None
    if input_trace:
        input_text = (
            f"{input_trace.get('captureStatus')} "
            f"backend={input_trace.get('backendUsed') or input_trace.get('backend')} "
            f"real={input_trace.get('realEventCount')} "
            f"moves={input_trace.get('mouseMoveCount')} "
            f"clicks={input_trace.get('clickCount')} "
            f"keys={input_trace.get('keyboardEventCount')}"
        )
    if action:
        counts = target_quality.get("qualityCounts") if isinstance(target_quality.get("qualityCounts"), dict) else {}
        quality_text = (
            f" strong={counts.get('strong')} medium={counts.get('medium')} weak={counts.get('weak')} unmatched={counts.get('unmatched')}"
            if target_quality
            else ""
        )
        input_text = (
            f"classification={action.get('status')} raw={action.get('rawOsClickCount')} "
            f"eligible={action.get('eligibleGameActionClickCount')} "
            f"target={action.get('targetRelativeClickCount')} "
            f"camera_excluded={action.get('cameraDragReleaseCount')} "
            f"ui_excluded={action.get('uiControlClickCount')} "
            f"ambiguous={action.get('ambiguousClickCount')}"
            f"{quality_text}"
        )
    if menu_interaction:
        input_text = (
            (input_text + " " if input_text else "")
            + f"menus={menu_interaction.get('rightClickMenuOpenCount')} "
            + f"selections={menu_interaction.get('menuSelectionCount')} "
            + f"row_geom={menu_interaction.get('menuSelectionsWithRowGeometryCount')} "
            + f"linked={menu_interaction.get('menuSelectionsLinkedToTargetsCount')}"
        )
    if arduino:
        arduino_text = (
            f"{arduino.get('classification')} "
            f"actions={arduino.get('actionCommandCount')} "
            f"moves={arduino.get('movementCommandCount')} "
            f"clicks={arduino.get('clickCommandCount')} "
            f"acks={arduino.get('ackCount')} errors={arduino.get('errorCount')}"
        )
    if coordinate:
        input_text = (input_text or "") + (
            f" coord={coordinate.get('status')} transform={coordinate.get('chosenTransform')} "
            f"row_hits={coordinate.get('normalizedMenuRowHitCount')}"
        )
    if traversal:
        movement = traversal.get("movement") if isinstance(traversal.get("movement"), dict) else {}
        plane_changes = movement.get("planeChanges") if isinstance(movement.get("planeChanges"), list) else []
        input_text = (input_text or "") + (
            f" traversal={traversal.get('status')} route={traversal.get('routeName')} "
            f"segments={traversal.get('routeSegmentCount') or traversal.get('stepCount')} "
            f"success={traversal.get('successfulSegmentCount') or traversal.get('successfulStepCount')} "
            f"review={traversal.get('reviewEvidenceCount')} "
            f"plane_changes={len(plane_changes)}"
        )
    if route_comparison:
        input_text = (input_text or "") + (
            f" template={route_comparison.get('status')} reason={route_comparison.get('statusReason')} "
            f"variant={route_comparison.get('matchedVariantName')} score={route_comparison.get('score')} "
            f"matched={route_comparison.get('matchedSegmentCount')}/{route_comparison.get('requiredSegmentCount')} "
            f"missing={len(route_comparison.get('missingSegments') or [])} "
            f"nav_subs={len(route_comparison.get('navigationSupportSubstitutions') or [])}"
        )
    if route_monitor:
        next_segment = route_monitor.get("nextExpectedSegment") if isinstance(route_monitor.get("nextExpectedSegment"), dict) else {}
        input_text = (input_text or "") + (
            f" route_monitor={route_monitor.get('status')} state={route_monitor.get('routeState')} "
            f"current_area={route_monitor.get('currentArea')} next={next_segment.get('label')}"
        )
    if route_history:
        input_text = (input_text or "") + (
            f" route_history={route_history.get('status')} state={route_history.get('routeState')} "
            f"completed={route_history.get('completedSegmentCount')} remaining={route_history.get('remainingSegmentCount')}"
        )
    if input_path:
        arduino_text = (arduino_text or "") + (
            f" path={input_path.get('inputPathClassification')} mirror={input_path.get('mirrorVerificationStatus')} probe={input_path.get('probeVerified')}"
        )
    if live_mirror:
        arduino_text = (arduino_text or "") + (
            f" live_active={live_mirror.get('liveMirrorActive')} live_verified={live_mirror.get('liveMirrorVerified')} non_probe={live_mirror.get('nonProbeActionCommandCount')}"
            f" click_policy={live_mirror.get('clickPolicyUsed') or live_mirror.get('mirrorClickPolicy')}"
            f" live_clicks={live_mirror.get('liveClickWithoutSuppressionCount')} map_only={live_mirror.get('mapOnlyClickCount')}"
        )
    ownership = input_path.get("clickOwnershipSummary") if isinstance(input_path.get("clickOwnershipSummary"), dict) else {}
    if ownership:
        arduino_text = (arduino_text or "") + (
            f" duplicate_clicks={ownership.get('duplicateClickLikelyCount')} owners={ownership.get('clickOwners')}"
        )
    return input_text, arduino_text


def localhost_port_is_listening(port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def request_json(url: str, *, timeout: float = 0.75) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8", errors="replace"))
            return value if isinstance(value, dict) else {}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def context_service_snapshot(port: int) -> dict[str, Any] | None:
    base = f"http://127.0.0.1:{int(port)}"
    health = request_json(f"{base}/health")
    if health is None:
        return None
    status = request_json(f"{base}/status") or {}
    capabilities = request_json(f"{base}/capabilities") or {}
    telemetry_discovery = capabilities.get("telemetryDiscovery") if isinstance(capabilities.get("telemetryDiscovery"), dict) else {}
    source_freshness = status.get("sourceFreshness") or telemetry_discovery.get("source_freshness") or {}
    return {
        "status_source": "context_service",
        "context_service_running": True,
        "context_service_status": health.get("status") or status.get("status") or "PASS",
        "health": health,
        "status_payload": status,
        "capabilities_payload": capabilities,
        "session_path": status.get("sessionPath") or telemetry_discovery.get("session_path"),
        "source_freshness": source_freshness,
        "latest_tick": status.get("latestTick") or telemetry_discovery.get("latest_tick"),
        "latest_export_sequence": status.get("latestExportSequence") or telemetry_discovery.get("latest_export_sequence"),
        "parser_warnings": telemetry_discovery.get("parse_warnings") or status.get("warnings") or [],
        "capability_summary": telemetry_discovery.get("normalized") or {},
    }


def file_status_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    discovery = telemetry_sources.discover_sources(
        session=str(config.get("last_session_path") or "").strip() or None,
        latest_session=bool(config.get("latest_session", True)),
        sources_override=str(config.get("sources_override") or "").strip() or None,
    )
    reads = telemetry_sources.read_sources(discovery.get("paths") or {}, include_raw=False)
    capabilities = telemetry_capabilities.capability_summary_from_reads(reads)
    freshness = capabilities.get("source_freshness") or {}
    return {
        "status_source": "files",
        "context_service_running": False,
        "context_service_status": "not running",
        "session_path": discovery.get("session_path"),
        "source_freshness": freshness,
        "latest_tick": capabilities.get("latest_tick"),
        "latest_export_sequence": capabilities.get("latest_export_sequence"),
        "parser_warnings": capabilities.get("parse_warnings") or [],
        "capability_summary": capabilities.get("normalized") or {},
        "source_files": capabilities.get("source_files") or [],
    }


def build_status_snapshot(config: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    port = port_from_config(config)
    snapshot = context_service_snapshot(port)
    if snapshot is None:
        snapshot = file_status_snapshot(config)
    output = resolve_output_folder(config, root=root)
    latest = latest_recording_dir_for_config(config, root=root)
    recording_dirs = sorted([path for path in output.iterdir() if path.is_dir()], key=lambda path: path.stat().st_mtime) if output.exists() else []
    snapshot.update(
        {
            "schema": "osrs_telemetry_ui_status.v1",
            "generated_at_utc": utc_now(),
            "repo_root": str(root or repo_root()),
            "python_executable": sys.executable,
            "context_service_port": port,
            "recordings_root": str(output),
            "output_folder": str(output),
            "recording_folder_count": len(recording_dirs),
            "latest_recording_path": str(latest) if latest else None,
            "latest_route_session_state": latest_route_session_state(),
        }
    )
    return snapshot


def latest_route_session_state() -> dict[str, Any]:
    root = Path.home() / ".osrs-telemetry" / "route_monitor"
    try:
        candidates = list(root.glob("*/*/route_session_state.json"))
    except OSError:
        candidates = []
    if not candidates:
        return {}
    newest = max(candidates, key=lambda path: path.stat().st_mtime)
    try:
        state = json.loads(newest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(state, dict):
        state["statePath"] = str(newest)
        state["actualRouteMonitorFolder"] = str(newest.parent)
        return state
    return {}


def build_command_preview(config: dict[str, Any], *, recording_paths: dict[str, Path] | None = None, latest_recording: Path | None = None) -> str:
    recording_paths = recording_paths or new_recording_control_paths(str(config.get("last_recording_label") or "manual_action"))
    live_processor = build_live_processor_command(config)
    analyzer = build_analyzer_command(latest_recording, config)
    extract_template = build_extract_route_template_command(latest_recording, config)
    compare_template = build_compare_route_template_command(latest_recording, config)
    register_variant = build_register_route_variant_command(latest_recording, config)
    route_readiness = build_route_readiness_command(config)
    route_history = build_route_history_follow_command(config)
    route_monitor_latest = build_route_monitor_recording_command(latest_recording, config)
    route_plan = build_route_session_plan(config.get("route_name") or config.get("route_template_path"), config)
    knowledge_update = build_project_knowledge_command(config)
    bootstrap_check = build_bootstrap_check_command()
    command_registry_check = build_command_registry_check_command()
    bot_preflight = build_bot_eval_preflight_command()
    input_geometry_check = build_input_geometry_check_command()
    human_profile = build_human_click_profile_command(config)
    game_command = config.get("game_launch_command") or command_text(discover_game_launch_command())
    authenticated_command = str(config.get("authenticated_game_start_command") or "").strip()
    launch_mode = start_game_command.classify_launch_mode(game_command, command_source="ui_config")
    lines = [
        "Run Game:",
        f"  {game_command or '(configure a launch command)'}",
        f"  Launch mode: {launch_mode.get('launchMode')}",
        f"  Authenticated Game Start: {authenticated_command or '(not configured)'}",
    ]
    for warning in launch_mode.get("warnings") or []:
        lines.append(f"  WARN: {warning}")
    lines.extend([
        "Start Telemetry Stack:",
        f"  live_processor: {command_text(live_processor) if live_processor else '(live_target_processor.py not found)'}",
        f"  context_service: {command_text(build_context_service_command(config))}",
        "Run Recovery / Loaded Scene Check:",
        f"  {command_text(build_recovery_command(config))}",
        "Start Recording:",
        f"  {command_text(build_recorder_command(config, stop_file=recording_paths['stop_file'], marker_file=recording_paths['marker_file'], mirror_panic_stop_file=recording_paths.get('mirror_panic_stop_file')))}",
        "Analyze Latest Recording:",
        f"  {command_text(analyzer) if analyzer else '(no recording found yet)'}",
        "Extract Route Template:",
        f"  {command_text(extract_template) if extract_template else '(no recording found yet)'}",
        "Compare Route Template:",
        f"  {command_text(compare_template) if compare_template else '(set route template path first)'}",
        "Register Route Variant:",
        f"  {command_text(register_variant) if register_variant else '(set route template path first)'}",
        "Check Route Readiness:",
        f"  {command_text(route_readiness) if route_readiness else '(set route template path first)'}",
        "Start Route Monitor:",
        f"  {command_text(route_history) if route_history else '(set route template path first)'}",
        "Start Route Session:",
        f"  Template: {route_plan.get('routeName')} rev {route_plan.get('templateRevision')} can_start={route_plan.get('canStart')}",
        f"  Monitor: {command_text(route_plan.get('routeMonitorCommand')) if route_plan.get('routeMonitorCommand') else '(template invalid)'}",
        f"  Recorder: {command_text(route_plan.get('recorderCommand')) if route_plan.get('recorderCommand') else '(template invalid)'}",
        "Monitor Latest Route Recording:",
        f"  {command_text(route_monitor_latest) if route_monitor_latest else '(set route template path first)'}",
        "MCP Tools Check:",
        f"  {command_text(build_mcp_list_tools_command(config)) if build_mcp_list_tools_command(config) else '(mcp_server.py not found)'}",
        "Bootstrap Check:",
        f"  {command_text(bootstrap_check) if bootstrap_check else '(update_project_knowledge.py not found)'}",
        "Command Registry Check:",
        f"  {command_text(command_registry_check) if command_registry_check else '(command_registry.py not found)'}",
        "Bot Eval Preflight:",
        f"  {command_text(bot_preflight) if bot_preflight else '(bot_eval_runner.py not found)'}",
        "Input Geometry Check:",
        f"  {command_text(input_geometry_check) if input_geometry_check else '(bot_eval_runner.py not found)'}",
        "Refresh Project Knowledge:",
        f"  {command_text(knowledge_update) if knowledge_update else '(update_project_knowledge.py not found)'}",
        "Generate Human Click Profile:",
        f"  {command_text(human_profile) if human_profile else '(no recording found yet)'}",
        "Arduino Status:",
        f"  {command_text(build_arduino_status_command(config))}",
        "Arduino Probe:",
        f"  {command_text(build_arduino_probe_command(config))}",
        "Live Mirror Test:",
        f"  {command_text(build_live_mirror_test_command(config))}",
        "Input Smoke Test:",
        f"  {command_text(build_input_smoke_test_command(config))}",
    ])
    return "\n".join(lines)


def simple_screen_model(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = merge_config(config or {})
    return {
        "schema": "human_recording_console_simple_screen.v1",
        "title": "OSRS Telemetry Recorder",
        "mode": UI_MODE_SIMPLE,
        "statusCards": ["Game", "Telemetry", "Recording", "Last Result", "Output"],
        "mainButtons": list(SIMPLE_MAIN_BUTTONS),
        "visibleControls": ["Label", "Output folder", "Auto Analyze After Stop", "Diagnostics / Settings"],
        "advancedHiddenByDefault": True,
        "advancedTabsVisible": False,
        "diagnosticsSeparateWindow": True,
        "defaultProfile": PROFILE_RECORD_EVERYTHING,
        "hiddenControls": [
            "profile dropdown",
            "preset dropdown",
            "route template path",
            "route template buttons",
            "route monitor internals",
            "route variant buttons",
            "Arduino mirror controls",
            "Arduino controls",
            "input backend controls",
            "capture flags",
            "raw command preview",
            "individual analyzer flags",
            "telemetry preflight flags",
            "artifact buttons",
            "schema/debug widgets",
            "source override widgets",
            "VM mapping controls",
            "coordinate alignment controls",
            "menu burst controls",
            "advanced mirror settings",
            "advanced tabs",
        ],
    }


def check_payload(*, config_path: str | Path | None = None, root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    config = load_config(config_path, root=root)
    save_config(config, config_path)
    record_everything = config_for_recording_profile(config, PROFILE_RECORD_EVERYTHING)
    paths = new_recording_control_paths(str(record_everything.get("last_recording_label") or "check"), base=Path(tempfile.gettempdir()))
    latest = latest_recording_dir_for_config(config, root=root)
    template_resolution = resolve_template_from_config(config, root=root)
    route_plan = build_route_session_plan(config.get("route_name") or config.get("route_template_path"), config)
    game_command = config.get("game_launch_command") or command_text(discover_game_launch_command(root))
    authenticated_command = str(config.get("authenticated_game_start_command") or "").strip()
    launch_mode = start_game_command.classify_launch_mode(game_command, command_source="ui_config")
    return {
        "schema": CHECK_SCHEMA,
        "status": "PASS" if script_exists("telemetry-viewer\\manual_recorder.py", root) and script_exists("telemetry-viewer\\analyze_manual_recording.py", root) else "FAIL",
        "simple_screen": simple_screen_model(config),
        "record_everything_profile": {
            "profile": PROFILE_RECORD_EVERYTHING,
            "recordingLabel": record_everything.get("last_recording_label"),
            "outputFolder": str(resolve_output_folder(record_everything, root=root)),
            "autoAnalyzeAfterStop": bool(record_everything.get("auto_analyze_after_stop", True)),
            "requiresArduino": bool(record_everything.get("arduino_required_for_recording")),
            "requiresRouteTemplate": False,
            "routeMonitorEnabled": bool(record_everything.get("route_monitor_enabled")),
        },
        "universal_recording_profile": {
            "profile": PROFILE_RECORD_EVERYTHING,
            "recordingLabel": record_everything.get("last_recording_label"),
            "outputFolder": str(resolve_output_folder(record_everything, root=root)),
            "autoAnalyzeAfterStop": bool(record_everything.get("auto_analyze_after_stop", True)),
            "requiresArduino": bool(record_everything.get("arduino_required_for_recording")),
        },
        "repo_root": str(root),
        "python_executable": sys.executable,
        "config_path": str(config_path) if config_path else str(user_config_path()),
        "scripts": {
            "manual_recorder": script_exists("telemetry-viewer\\manual_recorder.py", root),
            "analyze_manual_recording": script_exists("telemetry-viewer\\analyze_manual_recording.py", root),
            "context_service": script_exists("telemetry-viewer\\context_service.py", root),
            "live_target_processor": script_exists("telemetry-viewer\\live_target_processor.py", root),
            "mcp_server": script_exists("telemetry-viewer\\mcp_server.py", root),
            "arduino_input_bridge": script_exists("telemetry-viewer\\arduino_input_bridge.py", root),
            "human_click_profile": script_exists("telemetry-viewer\\human_click_profile.py", root),
        },
        "commands": {
            "game": command_text(config.get("game_launch_command") or discover_game_launch_command(root)),
            "authenticated_game_start": authenticated_command,
            "context_service": command_text(build_context_service_command(config)),
            "live_processor": command_text(build_live_processor_command(config, root=root)),
            "recovery": command_text(build_recovery_command(config)),
            "recorder": command_text(build_recorder_command(record_everything, stop_file=paths["stop_file"], marker_file=paths["marker_file"], mirror_panic_stop_file=paths.get("mirror_panic_stop_file"))),
            "analyzer": command_text(build_analyzer_command(latest, record_everything)),
            "project_knowledge_update": command_text(build_project_knowledge_command(config)),
            "human_click_profile": command_text(build_human_click_profile_command(config)),
            "extract_route_template": command_text(build_extract_route_template_command(latest, config)),
            "compare_route_template": command_text(build_compare_route_template_command(latest, config)),
            "register_route_variant": command_text(build_register_route_variant_command(latest, config)),
            "route_readiness": command_text(build_route_readiness_command(config)),
            "route_history_follow": command_text(build_route_history_follow_command(config)),
            "route_monitor_latest": command_text(build_route_monitor_recording_command(latest, config)),
            "route_session_monitor": command_text(route_plan.get("routeMonitorCommand")),
            "route_session_recorder": command_text(route_plan.get("recorderCommand")),
            "mcp_list_tools": command_text(build_mcp_list_tools_command(config)),
            "bootstrap_check": command_text(build_bootstrap_check_command()),
            "command_registry_check": command_text(build_command_registry_check_command()),
            "bot_eval_preflight": command_text(build_bot_eval_preflight_command()),
            "input_geometry_check": command_text(build_input_geometry_check_command()),
            "arduino_status": command_text(build_arduino_status_command(config)),
            "arduino_probe": command_text(build_arduino_probe_command(config)),
            "arduino_live_mirror_test": command_text(build_live_mirror_test_command(config)),
            "input_smoke_test": command_text(build_input_smoke_test_command(config, out=Path(tempfile.gettempdir()) / "osrs_input_smoke_test")),
        },
        "game_launch": {
            "startGameCommand": command_text(game_command),
            "authenticatedGameStartCommand": authenticated_command,
            "launchMode": launch_mode.get("launchMode"),
            "launchModeReason": launch_mode.get("reason"),
            "launchModeWarnings": launch_mode.get("warnings") or [],
            "authenticatedLaunchConfigured": bool(authenticated_command),
        },
        "route_template_resolution": public_template_resolution(template_resolution),
        "route_session_plan": {
            "status": route_plan.get("status"),
            "canStart": route_plan.get("canStart"),
            "routeName": route_plan.get("routeName"),
            "templateRevision": route_plan.get("templateRevision"),
            "templatePath": route_plan.get("templatePath"),
            "requiredSegmentCount": route_plan.get("requiredSegmentCount"),
            "routeMonitorSessionFolder": route_plan.get("routeMonitorSessionFolder"),
            "warnings": route_plan.get("warnings") or [],
        },
        "knowledge_update": {
            "enabledByDefault": bool(record_everything.get("update_project_knowledge", True)),
            "scriptExists": script_exists("telemetry-viewer\\update_project_knowledge.py", root),
            "command": command_text(build_project_knowledge_command(config)),
            "bootstrapCheckCommand": command_text(build_bootstrap_check_command()),
            "commandRegistryCheckCommand": command_text(build_command_registry_check_command()),
            "knowledgeBaseDir": str(root / "telemetry-viewer" / "knowledge_base"),
            "docsDir": str(root / "docs" / "knowledge"),
        },
        "status_snapshot": build_status_snapshot(config, root=root),
    }


class TelemetryControlApp:
    def __init__(self, root_window: tk.Tk, *, config_path: Path | None = None):
        self.root = root_window
        self.root.title("OSRS Telemetry Recorder")
        self.config_path = config_path or user_config_path()
        self.config = load_config(self.config_path)
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.last_recording_path: Path | None = latest_recording_dir_for_config(self.config)
        self.current_recording_paths: dict[str, Path] | None = None
        self.pending_recording_paths = new_recording_control_paths(str(self.config.get("last_recording_label") or "manual_action"))
        self.analysis_started_monotonic: float | None = None
        self.analysis_timeout_seconds = ANALYZER_TIMEOUT_SECONDS
        self.analysis_timeout_reported = False
        self._applying_config_to_vars = False
        self.main_buttons: dict[str, ttk.Button] = {}
        self.status_vars: dict[str, tk.StringVar] = {}
        self.diagnostics_window: tk.Toplevel | None = None
        self.diagnostics_log_text: ScrolledText | None = None
        self.recent_log_lines: list[str] = []
        self._build_variables()
        self._build_ui()
        self._refresh_command_preview()
        self.refresh_status()
        self.root.after(100, self._drain_log_queue)

    def _build_variables(self) -> None:
        cfg = self.config
        self.preset_var = tk.StringVar(value=str(cfg.get("selected_preset") or PRESET_BASIC))
        self.compact_mode_var = tk.BooleanVar(value=bool(cfg.get("compact_mode")))
        self.advanced_expanded_var = tk.BooleanVar(value=bool(cfg.get("advanced_expanded")))
        self.auto_analyze_var = tk.BooleanVar(value=bool(cfg.get("auto_analyze_after_stop", True)))
        self.output_folder_var = tk.StringVar(value=str(resolve_output_folder(cfg)))
        self.recording_profile_var = tk.StringVar(value=str(cfg.get("recording_profile") or PROFILE_UNIVERSAL_HUMAN))
        self.label_var = tk.StringVar(value=str(cfg.get("last_recording_label") or "manual_action"))
        self.description_var = tk.StringVar(value=str(cfg.get("last_recording_description") or ""))
        self.duration_var = tk.StringVar(value=str(cfg.get("duration") or "0"))
        self.poll_var = tk.StringVar(value=str(cfg.get("poll_interval_ms") or "50"))
        self.latest_session_var = tk.BooleanVar(value=bool(cfg.get("latest_session", True)))
        self.session_path_var = tk.StringVar(value=str(cfg.get("last_session_path") or ""))
        self.include_raw_var = tk.BooleanVar(value=bool(cfg.get("include_raw")))
        self.pretty_var = tk.BooleanVar(value=bool(cfg.get("pretty")))
        self.sources_var = tk.StringVar(value=str(cfg.get("sources_override") or ""))
        self.capture_input_var = tk.BooleanVar(value=bool(cfg.get("capture_input")))
        self.capture_mouse_var = tk.BooleanVar(value=bool(cfg.get("capture_mouse", True)))
        self.capture_keyboard_var = tk.BooleanVar(value=bool(cfg.get("capture_keyboard")))
        self.input_backend_var = tk.StringVar(value=str(cfg.get("input_backend") or "polling"))
        self.prefer_polling_var = tk.BooleanVar(value=bool(cfg.get("prefer_polling_input", True)))
        self.raw_input_attr_var = tk.BooleanVar(value=bool(cfg.get("raw_input_device_attribution")))
        self.input_preflight_var = tk.BooleanVar(value=bool(cfg.get("input_preflight", True)))
        self.fail_input_preflight_var = tk.BooleanVar(value=bool(cfg.get("fail_if_input_preflight_fails")))
        self.input_preflight_seconds_var = tk.StringVar(value=str(cfg.get("input_preflight_seconds") or "5"))
        self.join_input_var = tk.BooleanVar(value=bool(cfg.get("join_input_telemetry", True)))
        self.camera_behavior_var = tk.BooleanVar(value=bool(cfg.get("camera_behavior_analysis", True)))
        self.vm_mouse_mapping_var = tk.BooleanVar(value=bool(cfg.get("vm_mouse_mapping")))
        self.route_name_var = tk.StringVar(value=str(cfg.get("route_name") or route_template.DEFAULT_ROUTE_NAME))
        self.route_template_path_var = tk.StringVar(value=str(cfg.get("route_template_path") or ""))
        self.route_template_out_dir_var = tk.StringVar(value=str(cfg.get("route_template_out_dir") or "route_templates"))
        self.route_variant_name_var = tk.StringVar(value=str(cfg.get("route_variant_name") or "walk_here_large_door"))
        self.arduino_enabled_var = tk.BooleanVar(value=bool(cfg.get("arduino_enabled")))
        self.arduino_required_var = tk.BooleanVar(value=bool(cfg.get("arduino_required_for_recording")))
        self.arduino_auto_start_var = tk.BooleanVar(value=bool(cfg.get("arduino_auto_start_on_recording")))
        self.arduino_passthrough_var = tk.StringVar(value=str(cfg.get("arduino_passthrough_mode") or "off"))
        self.arduino_probe_var = tk.BooleanVar(value=bool(cfg.get("arduino_probe", True)))
        self.arduino_probe_dx_var = tk.StringVar(value=str(cfg.get("arduino_probe_move_dx") or "12"))
        self.arduino_probe_dy_var = tk.StringVar(value=str(cfg.get("arduino_probe_move_dy") or "0"))
        self.arduino_probe_observe_var = tk.StringVar(value=str(cfg.get("arduino_probe_observe_ms") or "500"))
        self.require_probe_verified_var = tk.BooleanVar(value=bool(cfg.get("require_arduino_probe_verified")))
        self.arduino_mirror_preflight_var = tk.BooleanVar(value=bool(cfg.get("arduino_mirror_preflight", True)))
        self.require_mirror_verified_var = tk.BooleanVar(value=bool(cfg.get("require_arduino_mirror_verified")))
        self.arduino_live_mirror_var = tk.BooleanVar(value=bool(cfg.get("arduino_live_mirror")))
        self.require_live_mirror_active_var = tk.BooleanVar(value=bool(cfg.get("require_live_mirror_active")))
        self.require_live_mirror_verified_var = tk.BooleanVar(value=bool(cfg.get("require_live_mirror_verified")))
        self.persistent_mirror_var = tk.BooleanVar(value=bool(cfg.get("persistent_mirror_during_recording", True)))
        self.mirror_arm_mode_var = tk.StringVar(value=str(cfg.get("mirror_arm_mode") or "recording_persistent"))
        self.mirror_disarm_focus_var = tk.BooleanVar(value=bool(cfg.get("mirror_disarm_on_focus_lost")))
        self.mirror_profile_var = tk.StringVar(value=str(cfg.get("mirror_profile") or "full_live_mirror"))
        self.mirror_click_policy_var = tk.StringVar(value=str(cfg.get("mirror_click_policy") or "live_unsuppressed"))
        self.require_click_source_suppression_var = tk.BooleanVar(value=bool(cfg.get("require_click_source_suppression")))
        self.allow_unsuppressed_live_clicks_var = tk.BooleanVar(value=bool(cfg.get("allow_unsuppressed_live_clicks")))
        self.max_live_clicks_var = tk.StringVar(value=str(cfg.get("max_live_clicks_per_recording") or "0"))
        self.auto_disable_live_clicks_var = tk.BooleanVar(value=bool(cfg.get("auto_disable_live_clicks_after_first_game_action")))
        self.mirror_disable_movement_var = tk.BooleanVar(value=bool(cfg.get("mirror_disable_movement")))
        self.mirror_disable_clicks_var = tk.BooleanVar(value=bool(cfg.get("mirror_disable_clicks")))
        self.mirror_echo_suppression_var = tk.BooleanVar(value=bool(cfg.get("mirror_echo_suppression")))
        self.mirror_echo_window_var = tk.StringVar(value=str(cfg.get("mirror_echo_window_ms") or "250"))
        self.mirror_click_echo_window_var = tk.StringVar(value=str(cfg.get("mirror_click_echo_window_ms") or "300"))
        self.mirror_max_queue_size_var = tk.StringVar(value=str(cfg.get("mirror_max_queue_size") or "25"))
        self.mirror_drop_move_older_var = tk.StringVar(value=str(cfg.get("mirror_drop_move_older_than_ms") or "150"))
        self.mirror_clear_menu_var = tk.BooleanVar(value=bool(cfg.get("mirror_clear_queue_on_menu_selection")))
        self.mirror_clear_action_var = tk.BooleanVar(value=bool(cfg.get("mirror_clear_queue_on_game_action")))
        self.mirror_clear_plane_var = tk.BooleanVar(value=bool(cfg.get("mirror_clear_queue_on_plane_change")))
        self.mirror_auto_pause_menu_var = tk.BooleanVar(value=bool(cfg.get("mirror_auto_pause_after_menu_selection")))
        self.mirror_auto_pause_plane_var = tk.BooleanVar(value=bool(cfg.get("mirror_auto_pause_after_plane_change")))
        self.mirror_auto_pause_quality_var = tk.StringVar(value=str(cfg.get("mirror_auto_pause_after_target_quality") or "off"))
        self.mirror_move_min_var = tk.StringVar(value=str(cfg.get("mirror_move_min_px") or "1"))
        self.mirror_max_step_var = tk.StringVar(value=str(cfg.get("mirror_max_step_px") or "25"))
        self.mirror_send_interval_var = tk.StringVar(value=str(cfg.get("mirror_send_interval_ms") or "5"))
        self.mirror_scale_x_var = tk.StringVar(value=str(cfg.get("mirror_scale_x") or "1.0"))
        self.mirror_scale_y_var = tk.StringVar(value=str(cfg.get("mirror_scale_y") or "1.0"))
        self.mirror_invert_x_var = tk.BooleanVar(value=bool(cfg.get("mirror_invert_x")))
        self.mirror_invert_y_var = tk.BooleanVar(value=bool(cfg.get("mirror_invert_y")))
        self.mirror_button_mode_var = tk.StringVar(value=str(cfg.get("mirror_button_mode") or "click"))
        self.mirror_quiet_probe_var = tk.BooleanVar(value=bool(cfg.get("mirror_quiet_probe", True)))
        self.mirror_test_duration_var = tk.StringVar(value=str(cfg.get("mirror_test_duration_sec") or "5"))
        self.mirror_arm_delay_var = tk.StringVar(value=str(cfg.get("mirror_arm_delay_ms") or "500"))
        self.mirror_max_clicks_var = tk.StringVar(value=str(cfg.get("mirror_max_clicks_per_second") or "4"))
        self.mirror_click_cooldown_var = tk.StringVar(value=str(cfg.get("mirror_click_cooldown_ms") or "120"))
        self.mirror_panic_threshold_var = tk.StringVar(value=str(cfg.get("mirror_panic_command_threshold") or "100"))
        self.mirror_arm_runelite_var = tk.BooleanVar(value=bool(cfg.get("mirror_arm_only_when_runelite_focused", True)))
        self.mirror_ignore_ui_clicks_var = tk.BooleanVar(value=bool(cfg.get("mirror_ignore_ui_clicks", True)))
        self.input_path_integrity_var = tk.BooleanVar(value=bool(cfg.get("input_path_integrity", True)))
        self.arduino_port_var = tk.StringVar(value=str(cfg.get("arduino_port") or ""))
        self.arduino_baud_var = tk.StringVar(value=str(cfg.get("arduino_baud") or "115200"))
        self.arduino_calibration_var = tk.StringVar(value=str(cfg.get("arduino_calibration_profile") or ""))
        self.port_var = tk.StringVar(value=str(cfg.get("context_service_port") or DEFAULT_PORT))
        self.profile_var = tk.StringVar(value=str(cfg.get("profile") or DEFAULT_PROFILE))
        self.game_command_var = tk.StringVar(value=str(cfg.get("game_launch_command") or command_text(discover_game_launch_command())))
        self.authenticated_game_command_var = tk.StringVar(value=str(cfg.get("authenticated_game_start_command") or ""))
        for var in (
            self.preset_var,
            self.description_var,
            self.duration_var,
            self.poll_var,
            self.session_path_var,
            self.sources_var,
            self.input_backend_var,
            self.input_preflight_seconds_var,
            self.arduino_passthrough_var,
            self.arduino_probe_dx_var,
            self.arduino_probe_dy_var,
            self.arduino_probe_observe_var,
            self.mirror_move_min_var,
            self.mirror_max_step_var,
            self.mirror_send_interval_var,
            self.mirror_scale_x_var,
            self.mirror_scale_y_var,
            self.mirror_button_mode_var,
            self.route_name_var,
            self.route_template_path_var,
            self.route_template_out_dir_var,
            self.route_variant_name_var,
            self.mirror_arm_mode_var,
            self.mirror_test_duration_var,
            self.mirror_arm_delay_var,
            self.mirror_max_clicks_var,
            self.mirror_click_cooldown_var,
            self.mirror_panic_threshold_var,
            self.mirror_profile_var,
            self.mirror_click_policy_var,
            self.max_live_clicks_var,
            self.mirror_echo_window_var,
            self.mirror_click_echo_window_var,
            self.mirror_max_queue_size_var,
            self.mirror_drop_move_older_var,
            self.mirror_auto_pause_quality_var,
            self.arduino_port_var,
            self.arduino_baud_var,
            self.arduino_calibration_var,
            self.port_var,
            self.profile_var,
            self.game_command_var,
            self.authenticated_game_command_var,
            self.output_folder_var,
            self.recording_profile_var,
        ):
            var.trace_add("write", lambda *_args: self._settings_changed())
        self.label_var.trace_add("write", self._label_changed)
        for var in (
            self.latest_session_var,
            self.include_raw_var,
            self.pretty_var,
            self.capture_input_var,
            self.capture_mouse_var,
            self.capture_keyboard_var,
            self.prefer_polling_var,
            self.input_preflight_var,
            self.fail_input_preflight_var,
            self.raw_input_attr_var,
            self.join_input_var,
            self.camera_behavior_var,
            self.vm_mouse_mapping_var,
            self.arduino_enabled_var,
            self.arduino_required_var,
            self.arduino_auto_start_var,
            self.arduino_probe_var,
            self.require_probe_verified_var,
            self.arduino_mirror_preflight_var,
            self.require_mirror_verified_var,
            self.arduino_live_mirror_var,
            self.require_live_mirror_active_var,
            self.require_live_mirror_verified_var,
            self.mirror_invert_x_var,
            self.mirror_invert_y_var,
            self.mirror_quiet_probe_var,
            self.mirror_arm_runelite_var,
            self.mirror_ignore_ui_clicks_var,
            self.input_path_integrity_var,
            self.compact_mode_var,
            self.advanced_expanded_var,
            self.auto_analyze_var,
            self.persistent_mirror_var,
            self.mirror_disarm_focus_var,
            self.require_click_source_suppression_var,
            self.allow_unsuppressed_live_clicks_var,
            self.auto_disable_live_clicks_var,
            self.mirror_disable_movement_var,
            self.mirror_disable_clicks_var,
            self.mirror_echo_suppression_var,
            self.mirror_clear_menu_var,
            self.mirror_clear_action_var,
            self.mirror_clear_plane_var,
            self.mirror_auto_pause_menu_var,
            self.mirror_auto_pause_plane_var,
        ):
            var.trace_add("write", lambda *_args: self._settings_changed())

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        status = ttk.LabelFrame(self.root, text="Status")
        status.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        for index in range(5):
            status.columnconfigure(index, weight=1)
        for index, (key, title) in enumerate(
            (
                ("game_chip", "Game"),
                ("telemetry_chip", "Telemetry"),
                ("recording_chip", "Recording"),
                ("analysis_chip", "Last Result"),
                ("output_chip", "Output"),
            )
        ):
            var = tk.StringVar(value="-")
            self.status_vars[key] = var
            card = ttk.LabelFrame(status, text=title)
            card.grid(row=0, column=index, sticky="ew", padx=4, pady=4)
            ttk.Label(card, textvariable=var).grid(row=0, column=0, sticky="ew", padx=6, pady=4)
        self.status_vars.update(
            {
                "repo_root": tk.StringVar(value="-"),
                "python_executable": tk.StringVar(value="-"),
                "session_path": tk.StringVar(value="-"),
                "context_service_status": tk.StringVar(value="-"),
                "source_freshness": tk.StringVar(value="-"),
                "tick_export": tk.StringVar(value="-"),
                "recorder_status": tk.StringVar(value="-"),
                "last_recording": tk.StringVar(value="-"),
                "process_pids": tk.StringVar(value="-"),
                "input_capture_status": tk.StringVar(value="-"),
                "arduino_status": tk.StringVar(value="-"),
                "latest_analysis": tk.StringVar(value="-"),
            }
        )

        workflow = ttk.LabelFrame(self.root, text="Human Recording Console")
        workflow.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        for index in range(7):
            workflow.columnconfigure(index, weight=1)
        self.status_vars["route_template_status"] = tk.StringVar(value="-")
        self.status_vars["route_template_status"].set(route_template_status_text(self.config))
        self.status_vars["recommendation"] = tk.StringVar(value="Ready to record")
        ttk.Label(workflow, textvariable=self.status_vars["recommendation"]).grid(row=0, column=0, columnspan=7, sticky="ew", padx=4, pady=4)
        for column, (label, command) in enumerate(
            (
                ("Start Game", self.start_game),
                ("Start Telemetry", self.start_telemetry_stack),
                ("Start Recording", self.start_recording),
                ("Stop Recording", self.stop_recording),
                ("Analyze Latest", self.analyze_latest_recording),
                ("Open Output Folder", self.open_output_folder),
                ("Diagnostics / Settings", self.open_diagnostics_settings),
            )
        ):
            button = ttk.Button(workflow, text=label, command=command)
            button.grid(row=1, column=column, sticky="ew", padx=3, pady=3)
            self.main_buttons[label] = button
        self._entry(workflow, 2, 0, "Label", self.label_var, columnspan=2)
        ttk.Checkbutton(workflow, text="Auto Analyze After Stop", variable=self.auto_analyze_var).grid(row=2, column=3, sticky="w", padx=4, pady=4)
        self._entry(workflow, 3, 0, "Output folder", self.output_folder_var, columnspan=4)
        ttk.Button(workflow, text="Change Output Folder", command=self.change_output_folder).grid(row=3, column=5, sticky="ew", padx=4, pady=4)
        self._status_row(workflow, 4, "Latest output", "last_recording")

        notebook = ttk.Notebook(self.root)
        self.advanced_notebook = notebook

        basic_tab = ttk.Frame(notebook)
        mirror_tab = ttk.Frame(notebook)
        recording_tab = ttk.Frame(notebook)
        advanced_tab = ttk.Frame(notebook)
        logs_tab = ttk.Frame(notebook)
        for tab in (basic_tab, mirror_tab, recording_tab, advanced_tab, logs_tab):
            tab.columnconfigure(1, weight=1)
        notebook.add(basic_tab, text="Basic")
        notebook.add(mirror_tab, text="Arduino / Mirror")
        notebook.add(recording_tab, text="Recording Options")
        notebook.add(advanced_tab, text="Advanced Flags")
        notebook.add(logs_tab, text="Logs")

        self._status_row(basic_tab, 0, "Repo root", "repo_root")
        self._status_row(basic_tab, 1, "Python", "python_executable")
        self._status_row(basic_tab, 2, "Session", "session_path")
        self._status_row(basic_tab, 3, "Context service", "context_service_status")
        self._status_row(basic_tab, 4, "Freshness", "source_freshness")
        self._status_row(basic_tab, 5, "Tick / export", "tick_export")
        self._status_row(basic_tab, 6, "Input capture", "input_capture_status")
        self._status_row(basic_tab, 7, "Arduino", "arduino_status")
        self._status_row(basic_tab, 8, "PIDs", "process_pids")
        self._status_row(basic_tab, 9, "Latest analysis", "latest_analysis")
        self._status_row(basic_tab, 10, "Route template", "route_template_detail")
        self._status_row(basic_tab, 11, "Route monitor", "route_monitor_detail")

        ttk.Checkbutton(mirror_tab, text="Arduino enabled", variable=self.arduino_enabled_var).grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Auto-start on recording", variable=self.arduino_auto_start_var).grid(row=0, column=1, sticky="w", padx=4, pady=4)
        self._entry(mirror_tab, 1, 0, "Port", self.arduino_port_var, width=12)
        self._entry(mirror_tab, 1, 2, "Baud", self.arduino_baud_var, width=10)
        ttk.Button(mirror_tab, text="Refresh ports", command=self.refresh_arduino_ports).grid(row=1, column=4, sticky="ew", padx=4, pady=4)
        ttk.Button(mirror_tab, text="Run Probe", command=self.run_arduino_probe).grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(mirror_tab, text="Run Live Mirror Test", command=self.run_live_mirror_test).grid(row=2, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(mirror_tab, text="Panic Stop Mirror", command=self.panic_stop_mirror).grid(row=2, column=2, sticky="ew", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Live mirror enabled", variable=self.arduino_live_mirror_var).grid(row=3, column=0, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Persistent during recording", variable=self.persistent_mirror_var).grid(row=3, column=1, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="RuneLite focus only", variable=self.mirror_arm_runelite_var).grid(row=3, column=2, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Ignore UI clicks", variable=self.mirror_ignore_ui_clicks_var).grid(row=3, column=3, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Disarm on focus lost", variable=self.mirror_disarm_focus_var).grid(row=3, column=4, sticky="w", padx=4, pady=4)
        ttk.Label(mirror_tab, text="Mirror profile").grid(row=4, column=0, sticky="w", padx=4, pady=4)
        ttk.Combobox(mirror_tab, textvariable=self.mirror_profile_var, values=("observe_only", "click_only", "move_only", "full_live_mirror", "validation_menu_row"), state="readonly").grid(row=4, column=1, sticky="ew", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Disable movement", variable=self.mirror_disable_movement_var).grid(row=4, column=2, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Disable clicks", variable=self.mirror_disable_clicks_var).grid(row=4, column=3, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Echo suppression", variable=self.mirror_echo_suppression_var).grid(row=4, column=4, sticky="w", padx=4, pady=4)
        ttk.Label(mirror_tab, text="Arm mode").grid(row=5, column=0, sticky="w", padx=4, pady=4)
        ttk.Combobox(mirror_tab, textvariable=self.mirror_arm_mode_var, values=("test_window", "recording_persistent", "manual"), state="readonly").grid(row=5, column=1, sticky="ew", padx=4, pady=4)
        self._entry(mirror_tab, 5, 2, "Test sec", self.mirror_test_duration_var, width=8)
        ttk.Checkbutton(mirror_tab, text="Auto-pause menu", variable=self.mirror_auto_pause_menu_var).grid(row=6, column=0, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Auto-pause plane", variable=self.mirror_auto_pause_plane_var).grid(row=6, column=1, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Clear queue menu", variable=self.mirror_clear_menu_var).grid(row=6, column=2, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Clear queue action", variable=self.mirror_clear_action_var).grid(row=6, column=3, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Clear queue plane", variable=self.mirror_clear_plane_var).grid(row=6, column=4, sticky="w", padx=4, pady=4)
        ttk.Label(mirror_tab, text="Click policy").grid(row=7, column=0, sticky="w", padx=4, pady=4)
        ttk.Combobox(
            mirror_tab,
            textvariable=self.mirror_click_policy_var,
            values=("off", "map_only", "live_unsuppressed", "live_requires_source_suppression", "arduino_source_only"),
            state="readonly",
        ).grid(row=7, column=1, sticky="ew", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Source required", variable=self.require_click_source_suppression_var).grid(row=7, column=2, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Allow duplicate-risk live clicks", variable=self.allow_unsuppressed_live_clicks_var).grid(row=7, column=3, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(mirror_tab, text="Disable live clicks after action", variable=self.auto_disable_live_clicks_var).grid(row=7, column=4, sticky="w", padx=4, pady=4)

        advanced_mirror = ttk.LabelFrame(mirror_tab, text="Advanced Mirror Settings")
        advanced_mirror.grid(row=8, column=0, columnspan=5, sticky="ew", padx=4, pady=8)
        for index in range(6):
            advanced_mirror.columnconfigure(index, weight=1)
        self._entry(advanced_mirror, 0, 0, "Move min", self.mirror_move_min_var, width=8)
        self._entry(advanced_mirror, 0, 2, "Max step", self.mirror_max_step_var, width=8)
        self._entry(advanced_mirror, 0, 4, "Send ms", self.mirror_send_interval_var, width=8)
        self._entry(advanced_mirror, 1, 0, "Max clicks/s", self.mirror_max_clicks_var, width=8)
        self._entry(advanced_mirror, 1, 2, "Click cd ms", self.mirror_click_cooldown_var, width=8)
        self._entry(advanced_mirror, 1, 4, "Panic cmds", self.mirror_panic_threshold_var, width=8)
        self._entry(advanced_mirror, 2, 0, "Scale X", self.mirror_scale_x_var, width=8)
        self._entry(advanced_mirror, 2, 2, "Scale Y", self.mirror_scale_y_var, width=8)
        ttk.Checkbutton(advanced_mirror, text="Invert X", variable=self.mirror_invert_x_var).grid(row=2, column=4, sticky="w", padx=4, pady=3)
        ttk.Checkbutton(advanced_mirror, text="Invert Y", variable=self.mirror_invert_y_var).grid(row=2, column=5, sticky="w", padx=4, pady=3)
        self._entry(advanced_mirror, 3, 0, "Echo ms", self.mirror_echo_window_var, width=8)
        self._entry(advanced_mirror, 3, 2, "Click echo", self.mirror_click_echo_window_var, width=8)
        self._entry(advanced_mirror, 3, 4, "Max queue", self.mirror_max_queue_size_var, width=8)
        self._entry(advanced_mirror, 4, 0, "Drop move ms", self.mirror_drop_move_older_var, width=8)
        ttk.Label(advanced_mirror, text="Pause quality").grid(row=4, column=2, sticky="w", padx=4, pady=3)
        ttk.Combobox(advanced_mirror, textvariable=self.mirror_auto_pause_quality_var, values=("off", "weak", "medium", "strong"), state="readonly", width=8).grid(row=4, column=3, sticky="ew", padx=4, pady=3)
        self._entry(advanced_mirror, 4, 4, "Max live clicks", self.max_live_clicks_var, width=8)

        ttk.Label(recording_tab, text="Recording profile").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Combobox(recording_tab, textvariable=self.recording_profile_var, values=(PROFILE_RECORD_EVERYTHING, "route_traversal", "woodcutting", "menu_validation", "custom"), state="readonly").grid(row=0, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Label(recording_tab, text="Preset").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ttk.Combobox(recording_tab, textvariable=self.preset_var, values=PRESETS, state="readonly").grid(row=1, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Button(recording_tab, text="Apply Preset", command=self.apply_selected_preset).grid(row=1, column=3, sticky="ew", padx=4, pady=4)
        ttk.Button(recording_tab, text="Reset Defaults", command=self.reset_to_recommended_defaults).grid(row=1, column=4, sticky="ew", padx=4, pady=4)
        ttk.Checkbutton(recording_tab, text="Capture input", variable=self.capture_input_var).grid(row=2, column=0, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(recording_tab, text="Mouse", variable=self.capture_mouse_var).grid(row=2, column=1, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(recording_tab, text="Keyboard", variable=self.capture_keyboard_var).grid(row=2, column=2, sticky="w", padx=4, pady=4)
        ttk.Label(recording_tab, text="Window context: on with input").grid(row=2, column=3, sticky="w", padx=4, pady=4)
        ttk.Label(recording_tab, text="Input backend").grid(row=3, column=0, sticky="w", padx=4, pady=4)
        ttk.Combobox(recording_tab, textvariable=self.input_backend_var, values=("auto", "windows_hook", "polling"), width=13, state="readonly").grid(row=3, column=1, sticky="ew", padx=4, pady=4)
        ttk.Checkbutton(recording_tab, text="Prefer polling", variable=self.prefer_polling_var).grid(row=3, column=2, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(recording_tab, text="Input preflight", variable=self.input_preflight_var).grid(row=3, column=3, sticky="w", padx=4, pady=4)
        self._entry(recording_tab, 4, 0, "Duration", self.duration_var, width=10)
        self._entry(recording_tab, 4, 2, "Poll ms", self.poll_var, width=10)
        self._entry(recording_tab, 4, 4, "Preflight sec", self.input_preflight_seconds_var, width=8)
        ttk.Checkbutton(recording_tab, text="Latest session", variable=self.latest_session_var).grid(row=5, column=0, sticky="w", padx=4, pady=4)
        self._entry(recording_tab, 5, 1, "Session path", self.session_path_var, columnspan=3)
        ttk.Button(recording_tab, text="Browse", command=self.browse_session).grid(row=5, column=5, padx=4, pady=4, sticky="ew")
        ttk.Checkbutton(recording_tab, text="Camera behavior", variable=self.camera_behavior_var).grid(row=6, column=0, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(recording_tab, text="VM mapping", variable=self.vm_mouse_mapping_var).grid(row=6, column=1, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(recording_tab, text="Raw Input device", variable=self.raw_input_attr_var).grid(row=6, column=2, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(recording_tab, text="Join input", variable=self.join_input_var).grid(row=6, column=3, sticky="w", padx=4, pady=4)
        self._entry(recording_tab, 7, 0, "Route template", self.route_template_path_var, columnspan=3)
        self._entry(recording_tab, 7, 4, "Out dir", self.route_template_out_dir_var, width=16)
        ttk.Button(recording_tab, text="Extract Template From Latest", command=self.extract_route_template_from_latest).grid(row=8, column=0, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Button(recording_tab, text="Compare Latest To Template", command=self.compare_latest_to_route_template).grid(row=8, column=2, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Button(recording_tab, text="Open Route Template", command=self.open_route_template).grid(row=8, column=4, sticky="ew", padx=4, pady=4)
        ttk.Button(recording_tab, text="Open Route Comparison", command=lambda: self.open_latest_artifact("route_template_comparison.json")).grid(row=8, column=5, sticky="ew", padx=4, pady=4)
        self._entry(recording_tab, 9, 0, "Variant name", self.route_variant_name_var, columnspan=2)
        ttk.Button(recording_tab, text="Register Latest As Variant", command=self.register_latest_as_route_variant).grid(row=9, column=3, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Button(recording_tab, text="Check Route Readiness", command=self.check_route_readiness).grid(row=10, column=0, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Button(recording_tab, text="Monitor Latest Recording", command=self.monitor_latest_route_recording).grid(row=10, column=2, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Button(recording_tab, text="Open Route Monitor", command=lambda: self.open_latest_artifact("route_monitor_status.json")).grid(row=10, column=4, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Button(recording_tab, text="Start Route Monitor", command=self.start_route_monitor).grid(row=11, column=0, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Button(recording_tab, text="Stop Route Monitor", command=self.stop_route_monitor).grid(row=11, column=2, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Button(recording_tab, text="Open Route Monitor Folder", command=self.open_route_monitor_folder).grid(row=11, column=4, columnspan=2, sticky="ew", padx=4, pady=4)

        self._entry(advanced_tab, 0, 0, "Sources override", self.sources_var, columnspan=3)
        ttk.Checkbutton(advanced_tab, text="Include raw", variable=self.include_raw_var).grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(advanced_tab, text="Pretty output", variable=self.pretty_var).grid(row=1, column=1, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(advanced_tab, text="Fail if preflight fails", variable=self.fail_input_preflight_var).grid(row=1, column=2, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(advanced_tab, text="Input path integrity", variable=self.input_path_integrity_var).grid(row=1, column=3, sticky="w", padx=4, pady=4)
        self._entry(advanced_tab, 2, 0, "Context port", self.port_var, width=10)
        self._entry(advanced_tab, 2, 2, "Profile", self.profile_var, width=16)
        self._entry(advanced_tab, 3, 0, "Game launch command", self.game_command_var, columnspan=4)
        self._entry(advanced_tab, 4, 0, "Authenticated Game Start", self.authenticated_game_command_var, columnspan=4)
        ttk.Button(advanced_tab, text="Save settings", command=self.save_settings).grid(row=5, column=0, padx=4, pady=4, sticky="ew")
        self.artifact_var = tk.StringVar(value="schema_gap_report.md")
        ttk.Label(advanced_tab, text="Artifacts").grid(row=5, column=1, sticky="w", padx=4, pady=4)
        ttk.Combobox(advanced_tab, textvariable=self.artifact_var, values=tuple(ARTIFACT_OPTIONS), state="readonly").grid(row=5, column=2, sticky="ew", padx=4, pady=4)
        ttk.Button(advanced_tab, text="Open", command=self.open_selected_artifact).grid(row=5, column=3, padx=4, pady=4, sticky="ew")
        preview_frame = ttk.LabelFrame(advanced_tab, text="Command Preview")
        preview_frame.grid(row=6, column=0, columnspan=5, sticky="nsew", padx=4, pady=4)
        preview_frame.columnconfigure(0, weight=1)
        self.preview_text = ScrolledText(preview_frame, height=10, wrap="word")
        self.preview_text.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        logs_tab.rowconfigure(0, weight=1)
        logs_tab.columnconfigure(0, weight=1)
        self.log_text = ScrolledText(logs_tab, height=18, wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

    def _status_row(self, parent: ttk.LabelFrame, row: int, label: str, key: str) -> None:
        var = tk.StringVar(value="-")
        self.status_vars[key] = var
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(parent, textvariable=var).grid(row=row, column=1, columnspan=3, sticky="ew", padx=4, pady=2)

    def _entry(self, parent: ttk.LabelFrame, row: int, column: int, label: str, variable: tk.StringVar, *, width: int | None = None, columnspan: int = 1) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=4, pady=3)
        ttk.Entry(parent, textvariable=variable, width=width).grid(row=row, column=column + 1, columnspan=columnspan, sticky="ew", padx=4, pady=3)

    def open_diagnostics_settings(self) -> None:
        if self.diagnostics_window is not None and self.diagnostics_window.winfo_exists():
            self.diagnostics_window.lift()
            self.diagnostics_window.focus_force()
            self._refresh_command_preview()
            return
        window = tk.Toplevel(self.root)
        window.title("Diagnostics / Settings")
        window.geometry("980x680")
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        self.diagnostics_window = window
        self.advanced_expanded_var.set(False)

        notebook = ttk.Notebook(window)
        notebook.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        for name in ("Paths", "Telemetry", "Route Templates", "Arduino / Mapping", "Commands / Logs", "Reset"):
            frame = ttk.Frame(notebook)
            frame.columnconfigure(1, weight=1)
            notebook.add(frame, text=name)
            setattr(self, "_diagnostics_" + name.lower().replace(" / ", "_").replace(" ", "_"), frame)

        paths_tab = self._diagnostics_paths
        self._status_row(paths_tab, 0, "Repo root", "diag_repo_root")
        self.status_vars["diag_repo_root"].set(str(repo_root()))
        self._entry(paths_tab, 1, 0, "Output folder", self.output_folder_var, columnspan=3)
        ttk.Button(paths_tab, text="Change Output Folder", command=self.change_output_folder).grid(row=1, column=5, sticky="ew", padx=4, pady=4)
        self._entry(paths_tab, 2, 0, "Game launch command", self.game_command_var, columnspan=4)
        self._entry(paths_tab, 3, 0, "Authenticated Game Start", self.authenticated_game_command_var, columnspan=4)
        ttk.Button(paths_tab, text="Save settings", command=self.save_settings).grid(row=4, column=0, sticky="ew", padx=4, pady=4)

        telemetry_tab = self._diagnostics_telemetry
        self._status_row(telemetry_tab, 0, "Active session", "diag_session_path")
        self._status_row(telemetry_tab, 1, "Telemetry freshness", "diag_source_freshness")
        self._status_row(telemetry_tab, 2, "Context service", "diag_context_service")
        self._status_row(telemetry_tab, 3, "Tick / export", "diag_tick_export")
        ttk.Button(telemetry_tab, text="Refresh Status", command=self.refresh_status).grid(row=4, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(telemetry_tab, text="Run Recovery Check", command=self.run_recovery).grid(row=4, column=1, sticky="ew", padx=4, pady=4)

        route_tab = self._diagnostics_route_templates
        self._entry(route_tab, 0, 0, "Default route template", self.route_template_path_var, columnspan=4)
        self._status_row(route_tab, 1, "Template status", "diag_route_template_status")
        ttk.Button(route_tab, text="Validate Template", command=self.check_route_readiness).grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(route_tab, text="Extract From Latest", command=self.extract_route_template_from_latest).grid(row=2, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(route_tab, text="Compare Latest", command=self.compare_latest_to_route_template).grid(row=2, column=2, sticky="ew", padx=4, pady=4)
        ttk.Button(route_tab, text="Register Variant", command=self.register_latest_as_route_variant).grid(row=2, column=3, sticky="ew", padx=4, pady=4)

        arduino_tab = self._diagnostics_arduino_mapping
        ttk.Label(arduino_tab, text="Arduino is optional. Mapping-only evidence is used by default; live mirror is experimental.").grid(row=0, column=0, columnspan=5, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(arduino_tab, text="Arduino configured", variable=self.arduino_enabled_var).grid(row=1, column=0, sticky="w", padx=4, pady=4)
        self._entry(arduino_tab, 1, 1, "Port", self.arduino_port_var, width=12)
        self._entry(arduino_tab, 1, 3, "Baud", self.arduino_baud_var, width=10)
        ttk.Button(arduino_tab, text="Refresh Ports", command=self.refresh_arduino_ports).grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(arduino_tab, text="Run Probe", command=self.run_arduino_probe).grid(row=2, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(arduino_tab, text="Panic Stop Mirror", command=self.panic_stop_mirror).grid(row=2, column=2, sticky="ew", padx=4, pady=4)
        ttk.Checkbutton(arduino_tab, text="Live mirror experimental", variable=self.arduino_live_mirror_var).grid(row=3, column=0, sticky="w", padx=4, pady=4)

        commands_tab = self._diagnostics_commands_logs
        commands_tab.rowconfigure(3, weight=1)
        commands_tab.rowconfigure(5, weight=1)
        commands_tab.columnconfigure(0, weight=1)
        ttk.Button(commands_tab, text="Refresh Commands", command=self._refresh_command_preview).grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Button(commands_tab, text="Bootstrap Check", command=self.run_bootstrap_check).grid(row=0, column=1, sticky="w", padx=4, pady=4)
        ttk.Button(commands_tab, text="Open Project Bootstrap Report", command=self.open_project_bootstrap_report).grid(row=0, column=2, sticky="w", padx=4, pady=4)
        ttk.Button(commands_tab, text="Open Knowledge", command=self.open_project_knowledge).grid(row=0, column=3, sticky="w", padx=4, pady=4)
        ttk.Button(commands_tab, text="Run Command Registry Check", command=self.run_command_registry_check).grid(row=0, column=4, sticky="w", padx=4, pady=4)
        ttk.Button(commands_tab, text="Open Recording Index", command=self.open_recording_index).grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ttk.Button(commands_tab, text="Open Open Gaps", command=self.open_open_gaps).grid(row=1, column=1, sticky="w", padx=4, pady=4)
        ttk.Button(commands_tab, text="Refresh Knowledge Index", command=self.refresh_project_knowledge).grid(row=1, column=2, sticky="w", padx=4, pady=4)
        ttk.Button(commands_tab, text="Generate Human Click Profile", command=self.generate_human_click_profile).grid(row=1, column=3, sticky="w", padx=4, pady=4)
        ttk.Button(commands_tab, text="Open Human Click Profile", command=self.open_human_click_profile).grid(row=1, column=4, sticky="w", padx=4, pady=4)
        ttk.Button(commands_tab, text="Check Input Geometry", command=self.run_input_geometry_check).grid(row=2, column=0, sticky="w", padx=4, pady=4)
        self.preview_text = ScrolledText(commands_tab, height=12, wrap="word")
        self.preview_text.grid(row=3, column=0, columnspan=5, sticky="nsew", padx=4, pady=4)
        ttk.Label(commands_tab, text="Logs").grid(row=4, column=0, columnspan=5, sticky="w", padx=4, pady=2)
        self.diagnostics_log_text = ScrolledText(commands_tab, height=12, wrap="word")
        self.diagnostics_log_text.grid(row=5, column=0, columnspan=5, sticky="nsew", padx=4, pady=4)
        self.diagnostics_log_text.insert(tk.END, "".join(self.recent_log_lines))
        self.diagnostics_log_text.see(tk.END)

        reset_tab = self._diagnostics_reset
        ttk.Label(reset_tab, text="Reset restores the foolproof Record Everything console while keeping normal defaults.").grid(row=0, column=0, columnspan=3, sticky="w", padx=4, pady=4)
        ttk.Button(reset_tab, text="Reset to Recommended Defaults", command=self.reset_to_recommended_defaults).grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        ttk.Button(reset_tab, text="Open Output Folder", command=self.open_output_folder).grid(row=1, column=1, sticky="ew", padx=4, pady=4)

        def on_close() -> None:
            self.diagnostics_window = None
            self.diagnostics_log_text = None
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", on_close)
        self.status_vars["recommendation"].set("Diagnostics / Settings opened")
        self.refresh_status()
        self._refresh_command_preview()

    def toggle_advanced(self) -> None:
        self.open_diagnostics_settings()

    def change_output_folder(self) -> None:
        selected = filedialog.askdirectory(title="Select recording output folder", initialdir=self.output_folder_var.get() or str(recordings_root()))
        if selected:
            self.output_folder_var.set(str(Path(selected).resolve()))
            self.status_vars["output_chip"].set("selected")
            self._settings_changed()

    def _label_changed(self, *_args: object) -> None:
        if self._applying_config_to_vars:
            return
        self.config["recording_label_mode"] = "custom"
        self.config["last_recording_label"] = self.label_var.get().strip()
        self._settings_changed()

    def _settings_changed(self) -> None:
        if self._applying_config_to_vars:
            return
        self.pending_recording_paths = new_recording_control_paths(self.label_var.get())
        self._refresh_route_template_status()
        self._refresh_command_preview()

    def _refresh_route_template_status(self) -> None:
        if "route_template_status" not in self.status_vars:
            return
        config = self.config_from_vars() if hasattr(self, "route_template_path_var") else self.config
        text = route_template_status_text(config)
        self.status_vars["route_template_status"].set(text)
        if "route_template_chip" in self.status_vars:
            self.status_vars["route_template_chip"].set("loaded" if text.startswith("Template loaded") else "missing")
        if "route_template_detail" in self.status_vars:
            self.status_vars["route_template_detail"].set(text)

    def apply_config_to_vars(self, config: dict[str, Any]) -> None:
        self.config = merge_config(config)
        self._applying_config_to_vars = True
        self.preset_var.set(str(self.config.get("selected_preset") or PRESET_BASIC))
        self.compact_mode_var.set(bool(self.config.get("compact_mode")))
        self.advanced_expanded_var.set(bool(self.config.get("advanced_expanded")))
        self.auto_analyze_var.set(bool(self.config.get("auto_analyze_after_stop", True)))
        self.output_folder_var.set(str(resolve_output_folder(self.config)))
        self.recording_profile_var.set(str(self.config.get("recording_profile") or PROFILE_UNIVERSAL_HUMAN))
        self.label_var.set(str(self.config.get("last_recording_label") or suggested_recording_label_for_preset(str(self.config.get("selected_preset") or PRESET_BASIC))))
        self.description_var.set(str(self.config.get("last_recording_description") or ""))
        self.duration_var.set(str(self.config.get("duration") or "0"))
        self.poll_var.set(str(self.config.get("poll_interval_ms") or "50"))
        self.latest_session_var.set(bool(self.config.get("latest_session", True)))
        self.session_path_var.set(str(self.config.get("last_session_path") or ""))
        self.include_raw_var.set(bool(self.config.get("include_raw")))
        self.pretty_var.set(bool(self.config.get("pretty")))
        self.sources_var.set(str(self.config.get("sources_override") or ""))
        self.capture_input_var.set(bool(self.config.get("capture_input")))
        self.capture_mouse_var.set(bool(self.config.get("capture_mouse", True)))
        self.capture_keyboard_var.set(bool(self.config.get("capture_keyboard")))
        self.input_backend_var.set(str(self.config.get("input_backend") or "polling"))
        self.prefer_polling_var.set(bool(self.config.get("prefer_polling_input", True)))
        self.input_preflight_var.set(bool(self.config.get("input_preflight", True)))
        self.raw_input_attr_var.set(bool(self.config.get("raw_input_device_attribution")))
        self.join_input_var.set(bool(self.config.get("join_input_telemetry", True)))
        self.camera_behavior_var.set(bool(self.config.get("camera_behavior_analysis", True)))
        self.vm_mouse_mapping_var.set(bool(self.config.get("vm_mouse_mapping")))
        self.route_name_var.set(str(self.config.get("route_name") or route_template.DEFAULT_ROUTE_NAME))
        self.route_template_path_var.set(str(self.config.get("route_template_path") or ""))
        self.route_template_out_dir_var.set(str(self.config.get("route_template_out_dir") or "route_templates"))
        self.route_variant_name_var.set(str(self.config.get("route_variant_name") or "walk_here_large_door"))
        self.arduino_enabled_var.set(bool(self.config.get("arduino_enabled")))
        self.arduino_auto_start_var.set(bool(self.config.get("arduino_auto_start_on_recording")))
        self.arduino_passthrough_var.set(str(self.config.get("arduino_passthrough_mode") or "off"))
        self.arduino_probe_var.set(bool(self.config.get("arduino_probe", True)))
        self.arduino_probe_dx_var.set(str(self.config.get("arduino_probe_move_dx") or "12"))
        self.arduino_probe_dy_var.set(str(self.config.get("arduino_probe_move_dy") or "0"))
        self.arduino_probe_observe_var.set(str(self.config.get("arduino_probe_observe_ms") or "500"))
        self.arduino_mirror_preflight_var.set(bool(self.config.get("arduino_mirror_preflight", True)))
        self.arduino_live_mirror_var.set(bool(self.config.get("arduino_live_mirror")))
        self.persistent_mirror_var.set(bool(self.config.get("persistent_mirror_during_recording", True)))
        self.mirror_arm_mode_var.set(str(self.config.get("mirror_arm_mode") or "recording_persistent"))
        self.mirror_disarm_focus_var.set(bool(self.config.get("mirror_disarm_on_focus_lost")))
        self.mirror_profile_var.set(str(self.config.get("mirror_profile") or "full_live_mirror"))
        self.mirror_click_policy_var.set(str(self.config.get("mirror_click_policy") or "live_unsuppressed"))
        self.require_click_source_suppression_var.set(bool(self.config.get("require_click_source_suppression")))
        self.allow_unsuppressed_live_clicks_var.set(bool(self.config.get("allow_unsuppressed_live_clicks")))
        self.max_live_clicks_var.set(str(self.config.get("max_live_clicks_per_recording") or "0"))
        self.auto_disable_live_clicks_var.set(bool(self.config.get("auto_disable_live_clicks_after_first_game_action")))
        self.mirror_disable_movement_var.set(bool(self.config.get("mirror_disable_movement")))
        self.mirror_disable_clicks_var.set(bool(self.config.get("mirror_disable_clicks")))
        self.mirror_echo_suppression_var.set(bool(self.config.get("mirror_echo_suppression")))
        self.mirror_echo_window_var.set(str(self.config.get("mirror_echo_window_ms") or "250"))
        self.mirror_click_echo_window_var.set(str(self.config.get("mirror_click_echo_window_ms") or "300"))
        self.mirror_max_queue_size_var.set(str(self.config.get("mirror_max_queue_size") or "25"))
        self.mirror_drop_move_older_var.set(str(self.config.get("mirror_drop_move_older_than_ms") or "150"))
        self.mirror_clear_menu_var.set(bool(self.config.get("mirror_clear_queue_on_menu_selection")))
        self.mirror_clear_action_var.set(bool(self.config.get("mirror_clear_queue_on_game_action")))
        self.mirror_clear_plane_var.set(bool(self.config.get("mirror_clear_queue_on_plane_change")))
        self.mirror_auto_pause_menu_var.set(bool(self.config.get("mirror_auto_pause_after_menu_selection")))
        self.mirror_auto_pause_plane_var.set(bool(self.config.get("mirror_auto_pause_after_plane_change")))
        self.mirror_auto_pause_quality_var.set(str(self.config.get("mirror_auto_pause_after_target_quality") or "off"))
        self.mirror_arm_runelite_var.set(bool(self.config.get("mirror_arm_only_when_runelite_focused", True)))
        self.mirror_ignore_ui_clicks_var.set(bool(self.config.get("mirror_ignore_ui_clicks", True)))
        self._applying_config_to_vars = False
        self._settings_changed()

    def apply_selected_preset(self) -> None:
        preset = self.preset_var.get() if self.preset_var.get() in PRESETS else PRESET_BASIC
        updated = config_for_preset(self.config_from_vars(), preset)
        self.apply_config_to_vars(updated)
        self.log("UI", f"Applied preset: {preset}")

    def reset_to_recommended_defaults(self) -> None:
        updated = config_for_recording_profile(default_config(), PROFILE_UNIVERSAL_HUMAN)
        self.apply_config_to_vars(updated)
        self.log("UI", "Reset to recommended defaults.")

    def config_from_vars(self) -> dict[str, Any]:
        config = merge_config(
            {
                "ui_mode": UI_MODE_ADVANCED if bool(self.advanced_expanded_var.get()) else UI_MODE_SIMPLE,
                "advanced_expanded": bool(self.advanced_expanded_var.get()),
                "recording_profile": self.recording_profile_var.get().strip() or PROFILE_UNIVERSAL_HUMAN,
                "auto_analyze_after_stop": bool(self.auto_analyze_var.get()),
                "analyzer_timeout_seconds": self.config.get("analyzer_timeout_seconds") or str(ANALYZER_TIMEOUT_SECONDS),
                "output_folder": self.output_folder_var.get().strip() or str(recordings_root()),
                "selected_preset": self.preset_var.get().strip() or PRESET_BASIC,
                "compact_mode": bool(self.compact_mode_var.get()),
                "last_session_path": self.session_path_var.get().strip(),
                "last_recording_label": self.label_var.get().strip(),
                "recording_label_mode": self.config.get("recording_label_mode") or "custom",
                "recording_label_preset": self.config.get("recording_label_preset") or self.preset_var.get().strip() or PRESET_BASIC,
                "last_recording_description": self.description_var.get().strip(),
                "duration": self.duration_var.get().strip(),
                "poll_interval_ms": self.poll_var.get().strip() or "50",
                "latest_session": bool(self.latest_session_var.get()),
                "include_raw": bool(self.include_raw_var.get()),
                "pretty": bool(self.pretty_var.get()),
                "sources_override": self.sources_var.get().strip(),
                "capture_input": bool(self.capture_input_var.get()),
                "capture_mouse": bool(self.capture_mouse_var.get()),
                "capture_keyboard": bool(self.capture_keyboard_var.get()),
                "input_backend": self.input_backend_var.get().strip() or "polling",
                "prefer_polling_input": bool(self.prefer_polling_var.get()),
                "raw_input_device_attribution": bool(self.raw_input_attr_var.get()),
                "input_preflight": bool(self.input_preflight_var.get()),
                "fail_if_input_preflight_fails": bool(self.fail_input_preflight_var.get()),
                "input_preflight_seconds": self.input_preflight_seconds_var.get().strip() or "5",
                "join_input_telemetry": bool(self.join_input_var.get()),
                "camera_behavior_analysis": bool(self.camera_behavior_var.get()),
                "vm_mouse_mapping": bool(self.vm_mouse_mapping_var.get()),
                "route_name": self.route_name_var.get().strip() or route_template.DEFAULT_ROUTE_NAME,
                "route_template_path": self.route_template_path_var.get().strip(),
                "route_template_out_dir": self.route_template_out_dir_var.get().strip() or "route_templates",
                "route_variant_name": self.route_variant_name_var.get().strip() or "walk_here_large_door",
                "arduino_enabled": bool(self.arduino_enabled_var.get()),
                "arduino_required_for_recording": bool(self.arduino_required_var.get()),
                "arduino_auto_start_on_recording": bool(self.arduino_auto_start_var.get()),
                "arduino_passthrough_mode": self.arduino_passthrough_var.get().strip() or "off",
                "arduino_probe": bool(self.arduino_probe_var.get()),
                "arduino_probe_move_dx": self.arduino_probe_dx_var.get().strip() or "12",
                "arduino_probe_move_dy": self.arduino_probe_dy_var.get().strip() or "0",
                "arduino_probe_observe_ms": self.arduino_probe_observe_var.get().strip() or "500",
                "require_arduino_probe_verified": bool(self.require_probe_verified_var.get()),
                "arduino_mirror_preflight": bool(self.arduino_mirror_preflight_var.get()),
                "require_arduino_mirror_verified": bool(self.require_mirror_verified_var.get()),
                "arduino_live_mirror": bool(self.arduino_live_mirror_var.get()),
                "require_live_mirror_active": bool(self.require_live_mirror_active_var.get()),
                "require_live_mirror_verified": bool(self.require_live_mirror_verified_var.get()),
                "persistent_mirror_during_recording": bool(self.persistent_mirror_var.get()),
                "mirror_arm_mode": self.mirror_arm_mode_var.get().strip() or "recording_persistent",
                "mirror_persist_until_stop": bool(self.persistent_mirror_var.get()),
                "mirror_keep_armed_while_recording": bool(self.persistent_mirror_var.get()),
                "mirror_disarm_on_focus_lost": bool(self.mirror_disarm_focus_var.get()),
                "mirror_profile": self.mirror_profile_var.get().strip() or "full_live_mirror",
                "mirror_click_policy": self.mirror_click_policy_var.get().strip() or "live_unsuppressed",
                "require_click_source_suppression": bool(self.require_click_source_suppression_var.get()),
                "allow_unsuppressed_live_clicks": bool(self.allow_unsuppressed_live_clicks_var.get()),
                "max_live_clicks_per_recording": self.max_live_clicks_var.get().strip() or "0",
                "auto_disable_live_clicks_after_first_game_action": bool(self.auto_disable_live_clicks_var.get()),
                "mirror_disable_movement": bool(self.mirror_disable_movement_var.get()),
                "mirror_disable_clicks": bool(self.mirror_disable_clicks_var.get()),
                "mirror_echo_suppression": bool(self.mirror_echo_suppression_var.get()),
                "mirror_echo_window_ms": self.mirror_echo_window_var.get().strip() or "250",
                "mirror_click_echo_window_ms": self.mirror_click_echo_window_var.get().strip() or "300",
                "mirror_max_queue_size": self.mirror_max_queue_size_var.get().strip() or "25",
                "mirror_drop_move_older_than_ms": self.mirror_drop_move_older_var.get().strip() or "150",
                "mirror_clear_queue_on_game_action": bool(self.mirror_clear_action_var.get()),
                "mirror_clear_queue_on_menu_selection": bool(self.mirror_clear_menu_var.get()),
                "mirror_clear_queue_on_plane_change": bool(self.mirror_clear_plane_var.get()),
                "mirror_auto_pause_after_menu_selection": bool(self.mirror_auto_pause_menu_var.get()),
                "mirror_auto_pause_after_plane_change": bool(self.mirror_auto_pause_plane_var.get()),
                "mirror_auto_pause_after_target_quality": self.mirror_auto_pause_quality_var.get().strip() or "off",
                "mirror_validation_mode": "menu_row" if self.mirror_profile_var.get().strip() == "validation_menu_row" else self.config.get("mirror_validation_mode", "custom"),
                "mirror_move_min_px": self.mirror_move_min_var.get().strip() or "1",
                "mirror_max_step_px": self.mirror_max_step_var.get().strip() or "25",
                "mirror_send_interval_ms": self.mirror_send_interval_var.get().strip() or "5",
                "mirror_scale_x": self.mirror_scale_x_var.get().strip() or "1.0",
                "mirror_scale_y": self.mirror_scale_y_var.get().strip() or "1.0",
                "mirror_invert_x": bool(self.mirror_invert_x_var.get()),
                "mirror_invert_y": bool(self.mirror_invert_y_var.get()),
                "mirror_button_mode": self.mirror_button_mode_var.get().strip() or "click",
                "mirror_quiet_probe": bool(self.mirror_quiet_probe_var.get()),
                "mirror_test_duration_sec": self.mirror_test_duration_var.get().strip() or "5",
                "mirror_arm_delay_ms": self.mirror_arm_delay_var.get().strip() or "500",
                "mirror_max_clicks_per_second": self.mirror_max_clicks_var.get().strip() or "4",
                "mirror_max_button_commands_per_second": self.config.get("mirror_max_button_commands_per_second") or "8",
                "mirror_max_move_commands_per_second": self.config.get("mirror_max_move_commands_per_second") or "120",
                "mirror_max_total_commands_per_second": self.config.get("mirror_max_total_commands_per_second") or "150",
                "mirror_click_cooldown_ms": self.mirror_click_cooldown_var.get().strip() or "120",
                "mirror_same_button_cooldown_ms": self.config.get("mirror_same_button_cooldown_ms") or "80",
                "mirror_max_burst_commands": self.config.get("mirror_max_burst_commands") or "50",
                "mirror_panic_command_threshold": self.mirror_panic_threshold_var.get().strip() or "100",
                "mirror_panic_window_ms": self.config.get("mirror_panic_window_ms") or "1000",
                "mirror_arm_only_when_runelite_focused": bool(self.mirror_arm_runelite_var.get()),
                "mirror_window_title_allow": self.config.get("mirror_window_title_allow") or "RuneLite",
                "mirror_exclude_window_title": self.config.get("mirror_exclude_window_title") or "OSRS Telemetry Control",
                "mirror_region": self.config.get("mirror_region") or "client",
                "mirror_ignore_ui_clicks": bool(self.mirror_ignore_ui_clicks_var.get()),
                "input_path_integrity": bool(self.input_path_integrity_var.get()),
                "arduino_port": self.arduino_port_var.get().strip(),
                "arduino_baud": self.arduino_baud_var.get().strip() or "115200",
                "arduino_calibration_profile": self.arduino_calibration_var.get().strip(),
                "context_service_port": self.port_var.get().strip() or DEFAULT_PORT,
                "profile": self.profile_var.get().strip() or DEFAULT_PROFILE,
                "game_launch_command": self.game_command_var.get().strip(),
                "authenticated_game_start_command": self.authenticated_game_command_var.get().strip(),
                "preferred_script_commands": self.config.get("preferred_script_commands") or {},
            }
        )
        latest = latest_recording_dir_for_config(config)
        config["preferred_script_commands"] = {
            "context_service": command_text(build_context_service_command(config)),
            "live_processor": command_text(build_live_processor_command(config)),
            "recovery": command_text(build_recovery_command(config)),
            "analyzer": command_text(build_analyzer_command(latest, config)),
            "extract_route_template": command_text(build_extract_route_template_command(latest, config)),
            "compare_route_template": command_text(build_compare_route_template_command(latest, config)),
            "register_route_variant": command_text(build_register_route_variant_command(latest, config)),
            "mcp_list_tools": command_text(build_mcp_list_tools_command(config)),
            "arduino_status": command_text(build_arduino_status_command(config)),
            "arduino_probe": command_text(build_arduino_probe_command(config)),
            "arduino_live_mirror_test": command_text(build_live_mirror_test_command(config)),
            "input_smoke_test": command_text(build_input_smoke_test_command(config)),
        }
        return config

    def _refresh_command_preview(self) -> None:
        if not hasattr(self, "preview_text"):
            return
        try:
            if not self.preview_text.winfo_exists():
                return
        except tk.TclError:
            return
        config = self.config_from_vars()
        if str(config.get("recording_profile") or PROFILE_UNIVERSAL_HUMAN) != "custom":
            config = config_for_recording_profile(config, str(config.get("recording_profile") or PROFILE_UNIVERSAL_HUMAN))
        preview = build_command_preview(config, recording_paths=self.pending_recording_paths, latest_recording=latest_recording_dir_for_config(config))
        try:
            self.preview_text.configure(state="normal")
            self.preview_text.delete("1.0", tk.END)
            self.preview_text.insert(tk.END, preview)
            self.preview_text.configure(state="disabled")
        except tk.TclError:
            return

    def save_settings(self) -> None:
        self.config = self.config_from_vars()
        path = save_config(self.config, self.config_path)
        self.log("UI", f"Saved settings to {path}")

    def log(self, name: str, message: str) -> None:
        self.log_queue.put((name, message))

    def _drain_log_queue(self) -> None:
        drained = 0
        while drained < LOG_DRAIN_BATCH_SIZE:
            try:
                name, message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            stamp = datetime.now().strftime("%H:%M:%S")
            line = f"[{stamp}] {name}: {message}\n"
            self.recent_log_lines.append(line)
            if len(self.recent_log_lines) > LOG_TEXT_MAX_LINES:
                del self.recent_log_lines[: len(self.recent_log_lines) - LOG_TEXT_MAX_LINES]
            self.log_text.insert(tk.END, line)
            self.log_text.see(tk.END)
            if self.diagnostics_log_text is not None and self.diagnostics_log_text.winfo_exists():
                self.diagnostics_log_text.insert(tk.END, line)
                self.diagnostics_log_text.see(tk.END)
            drained += 1
        try:
            line_count = int(float(self.log_text.index("end-1c").split(".")[0]))
            if line_count > LOG_TEXT_MAX_LINES:
                self.log_text.delete("1.0", f"{line_count - LOG_TEXT_MAX_LINES}.0")
        except (tk.TclError, ValueError, IndexError):
            pass
        self._update_process_status()
        self.root.after(1 if not self.log_queue.empty() else 100, self._drain_log_queue)

    def _set_button_enabled(self, label: str, enabled: bool) -> None:
        button = self.main_buttons.get(label)
        if button is not None:
            button.configure(state=("normal" if enabled else "disabled"))

    def _set_analysis_status(self, state: str, *, detail: str | None = None, elapsed_seconds: float | int | None = None) -> None:
        text = analysis_progress_text(state, elapsed_seconds=elapsed_seconds, detail=detail)
        if "recording_chip" in self.status_vars and state in {"analyzing", "reading_report"}:
            self.status_vars["recording_chip"].set("analyzing")
        if "analysis_chip" in self.status_vars:
            self.status_vars["analysis_chip"].set("WARN" if state in {"failed", "timeout"} else ("PASS" if state == "complete" else text))
        if "recommendation" in self.status_vars:
            self.status_vars["recommendation"].set(text)

    def _begin_analysis_status(self) -> None:
        self.analysis_started_monotonic = time.monotonic()
        self.analysis_timeout_reported = False
        try:
            self.analysis_timeout_seconds = int(str(self.config.get("analyzer_timeout_seconds") or ANALYZER_TIMEOUT_SECONDS))
        except ValueError:
            self.analysis_timeout_seconds = ANALYZER_TIMEOUT_SECONDS
        self._set_button_enabled("Analyze Latest", False)
        self._set_button_enabled("Start Recording", False)
        self._set_analysis_status("analyzing", elapsed_seconds=0)
        self.root.after(1000, self._analysis_heartbeat)

    def _analysis_heartbeat(self) -> None:
        analyzer = self.processes.get("analyzer")
        if not analyzer or analyzer.poll() is not None or self.analysis_started_monotonic is None:
            return
        elapsed = time.monotonic() - self.analysis_started_monotonic
        if analyzer_timed_out(self.analysis_started_monotonic, time.monotonic(), self.analysis_timeout_seconds):
            if not self.analysis_timeout_reported:
                self.analysis_timeout_reported = True
                self.log("analyzer", f"analysis timed out after {int(elapsed)} seconds; stopping analyzer.")
                self._set_analysis_status("timeout", detail=f"{int(elapsed)}s")
                update_ui_recording_manifest(
                    {
                        "finalVerdict": "WARN",
                        "warnings": [f"analysis timed out after {int(elapsed)} seconds"],
                    }
                )
            self.stop_process("analyzer", grace_seconds=2)
            return
        self._set_analysis_status("analyzing", elapsed_seconds=elapsed)
        self.root.after(1000, self._analysis_heartbeat)

    def _handle_analyzer_finished(self, code: int) -> None:
        self._set_analysis_status("reading_report")
        self.last_recording_path = latest_recording_dir_for_config(self.config)
        status_text = woodcutting_lifecycle_status_text(self.last_recording_path)
        if status_text:
            self.log("analyzer", status_text)
        result = self.log_input_analysis_status(self.last_recording_path)
        self.analysis_started_monotonic = None
        self._set_button_enabled("Analyze Latest", True)
        self._set_button_enabled("Start Recording", True)
        if code != 0:
            self._set_analysis_status("failed", detail=f"analyzer exited with code {code}")
            update_ui_recording_manifest(
                {
                    "finalVerdict": "WARN",
                    "warnings": [f"analyzer exited with code {code}"],
                }
            )
        elif result and result.get("summaryPresent"):
            self._set_analysis_status("complete", detail=f"{result.get('detectedActivityType')} {result.get('verdict')}")
        else:
            warning = str((result or {}).get("biggestWarning") or "summary/report missing")
            self._set_analysis_status("failed", detail=warning)
        self.root.after(0, self.refresh_status)

    def _reader_thread(self, name: str, process: subprocess.Popen[str]) -> None:
        stream = process.stdout
        if stream is not None:
            for line in iter(stream.readline, ""):
                if not line:
                    break
                self.log(name, line.rstrip("\r\n"))
        code = process.wait()
        self.log(name, f"exited with code {code}")
        if name == "recorder":
            self.last_recording_path = latest_recording_dir_for_config(self.config)
            self.root.after(0, self.refresh_status)
        if name == "analyzer":
            self.root.after(0, lambda exit_code=code: self._handle_analyzer_finished(exit_code))
        if name == "input_smoke_test":
            self.root.after(0, lambda: (self.log_input_smoke_status(), self.refresh_status()))
        if name == "arduino_bridge":
            self.root.after(0, lambda: (self.log_arduino_probe_status(), self.refresh_status()))

    def start_process(self, name: str, command: list[str] | str | None, *, shell: bool = False) -> subprocess.Popen[str] | None:
        if not command:
            self.log(name, "No command configured.")
            return None
        existing = self.processes.get(name)
        if existing and existing.poll() is None:
            self.log(name, f"Already running with PID {existing.pid}.")
            return existing
        self.log(name, f"starting: {command_text(command)}")
        try:
            process = subprocess.Popen(
                command,
                cwd=repo_root(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                shell=shell,
            )
        except OSError as error:
            self.log(name, f"start failed: {type(error).__name__}: {error}")
            return None
        self.processes[name] = process
        self.log(name, f"started PID {process.pid}")
        threading.Thread(target=self._reader_thread, args=(name, process), daemon=True).start()
        if name == "analyzer":
            self._begin_analysis_status()
        self._update_process_status()
        return process

    def stop_process(self, name: str, *, grace_seconds: float = 3.0) -> None:
        process = self.processes.get(name)
        if not process or process.poll() is not None:
            self.log(name, "Not running.")
            return
        self.log(name, f"stopping PID {process.pid}")
        try:
            process.terminate()
            process.wait(timeout=grace_seconds)
            self.log(name, f"stopped with code {process.returncode}")
            return
        except subprocess.TimeoutExpired:
            self.log(name, "terminate timed out; forcing process tree stop.")
        except OSError as error:
            self.log(name, f"terminate failed: {type(error).__name__}: {error}")
        self._force_stop_process_tree(process, name)

    def _force_stop_process_tree(self, process: subprocess.Popen[str], name: str) -> None:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], cwd=repo_root(), capture_output=True, text=True, timeout=5, check=False)
                self.log(name, f"forced stop for PID {process.pid}")
                return
            except OSError as error:
                self.log(name, f"taskkill failed: {type(error).__name__}: {error}")
        try:
            process.kill()
            self.log(name, f"killed PID {process.pid}")
        except OSError as error:
            self.log(name, f"kill failed: {type(error).__name__}: {error}")

    def _update_process_status(self) -> None:
        pids = []
        for name in PROCESS_NAMES:
            process = self.processes.get(name)
            if process and process.poll() is None:
                pids.append(f"{name}:{process.pid}")
        if "process_pids" in self.status_vars:
            self.status_vars["process_pids"].set(", ".join(pids) if pids else "-")
        recorder = self.processes.get("recorder")
        analyzer = self.processes.get("analyzer")
        recorder_status = f"running PID {recorder.pid}" if recorder and recorder.poll() is None else "stopped"
        if "recorder_status" in self.status_vars:
            self.status_vars["recorder_status"].set(recorder_status)
        if "recording_chip" in self.status_vars:
            if analyzer and analyzer.poll() is None:
                self.status_vars["recording_chip"].set("analyzing")
            else:
                self.status_vars["recording_chip"].set("recording" if recorder and recorder.poll() is None else "idle")
        self._set_button_enabled("Start Recording", not bool((recorder and recorder.poll() is None) or (analyzer and analyzer.poll() is None)))
        self._set_button_enabled("Stop Recording", bool(recorder and recorder.poll() is None))
        self._set_button_enabled("Analyze Latest", not bool(analyzer and analyzer.poll() is None))

    def browse_session(self) -> None:
        selected = filedialog.askdirectory(title="Select telemetry session")
        if selected:
            self.session_path_var.set(selected)
            self.latest_session_var.set(False)

    def start_game(self) -> None:
        config = self.config_from_vars()
        configured_command = str(config.get("authenticated_game_start_command") or config.get("game_launch_command") or "").strip()
        command_info = start_game_command.resolve_start_game_command(configured_command=configured_command or None)
        command = str(command_info.get("command") or "").strip()
        if command_info.get("status") != "PASS" or not command:
            self.log("game", "No game launch command is configured.")
            if "recommendation" in self.status_vars:
                self.status_vars["recommendation"].set("No game launch command configured")
            self.open_diagnostics_settings()
            return
        for warning in command_info.get("launchModeWarnings") or []:
            self.log("game", f"WARN: {warning}")
        self.start_process("game", command, shell=bool(command_info.get("shell", True)))

    def start_telemetry_stack(self) -> None:
        config = self.config_from_vars()
        live_processor = build_live_processor_command(config)
        if live_processor:
            self.start_process("live_processor", live_processor)
        port = port_from_config(config)
        managed = self.processes.get("context_service")
        if localhost_port_is_listening(port) and not (managed and managed.poll() is None):
            self.log("context_service", f"Port {port} is already listening; reusing existing context service/daemon.")
        else:
            self.start_process("context_service", build_context_service_command(config))
        if "recommendation" in self.status_vars:
            self.status_vars["recommendation"].set("Starting telemetry")
        self.root.after(1500, self.refresh_status)

    def stop_telemetry_stack(self) -> None:
        for name in ("live_processor", "context_service", "mcp_server"):
            self.stop_process(name)

    def run_recovery(self) -> None:
        self.start_process("analyzer", build_recovery_command(self.config_from_vars()))

    def start_recording(self) -> None:
        recorder = self.processes.get("recorder")
        if recorder and recorder.poll() is None:
            self.log("recorder", f"Already running with PID {recorder.pid}.")
            return
        config = config_for_recording_profile(self.config_from_vars(), PROFILE_RECORD_EVERYTHING)
        self.apply_config_to_vars(config)
        if bool(config.get("arduino_required_for_recording")):
            status = self.arduino_status_payload(config)
            if not status.get("available"):
                self.log("arduino", "Required Arduino is unavailable; recording was not started.")
                for warning in status.get("warnings") or []:
                    self.log("arduino", str(warning))
                return
        self.config = merge_config(config)
        save_config(self.config, self.config_path)
        self.current_recording_paths = new_recording_control_paths(str(config.get("last_recording_label") or "manual_action"))
        stop_file = self.current_recording_paths["stop_file"]
        marker_file = self.current_recording_paths["marker_file"]
        panic_file = self.current_recording_paths["mirror_panic_stop_file"]
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        marker_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            if stop_file.exists():
                stop_file.unlink()
            if panic_file.exists():
                panic_file.unlink()
            marker_file.write_text("", encoding="utf-8")
        except OSError as error:
            self.log("recorder", f"control file setup failed: {type(error).__name__}: {error}")
            return
        route_monitor_command = None
        route_monitor_folder = None
        route_resolution = resolve_template_from_config(config)
        if bool(config.get("route_monitor_enabled")) and route_resolution.get("status") == "PASS":
            session_id = "ui_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            config["route_session_id"] = session_id
            route_monitor_command = build_route_history_follow_command(config)
            route_monitor_folder = Path.home() / ".osrs-telemetry" / "route_monitor" / str(route_resolution.get("routeName") or "route") / session_id
        recorder_command = build_recorder_command(config, stop_file=stop_file, marker_file=marker_file, mirror_panic_stop_file=panic_file)
        manifest = {
            "schema": UI_RECORDING_SESSION_SCHEMA,
            "startedAtUtc": utc_now(),
            "stoppedAtUtc": None,
            "uiMode": UI_MODE_SIMPLE,
            "profile": PROFILE_RECORD_EVERYTHING,
            "recordingLabel": str(config.get("last_recording_label") or ""),
            "recordingFolder": None,
            "outputFolder": str(resolve_output_folder(config)),
            "telemetryStatusAtStart": build_status_snapshot(config),
            "routeTemplate": public_template_resolution(route_resolution) if route_resolution.get("status") == "PASS" else None,
            "routeTemplateResolvedPath": str(route_resolution.get("resolvedPath") or "") if route_monitor_command else None,
            "routeTemplateRevision": route_resolution.get("templateRevision") if route_monitor_command else None,
            "routeMonitorFolder": str(route_monitor_folder) if route_monitor_folder else None,
            "recorderCommand": command_text(recorder_command),
            "analyzerCommand": None,
            "detectedActivityType": None,
            "finalVerdict": None,
            "finalReportPath": None,
            "warnings": [] if route_resolution.get("status") == "PASS" or not config.get("route_monitor_enabled") else list(route_resolution.get("warnings") or []),
        }
        write_ui_recording_manifest(manifest)
        if route_monitor_command:
            route_process = self.start_process("route_monitor", route_monitor_command)
            update_ui_recording_manifest(
                {
                    "routeMonitorStarted": bool(route_process and route_process.poll() is None),
                    "routeMonitorCommand": command_text(route_monitor_command),
                }
            )
        self.start_process("recorder", recorder_command)
        self.pending_recording_paths = new_recording_control_paths(str(config.get("last_recording_label") or "manual_action"))
        self.status_vars["recommendation"].set("Recording")
        self._refresh_command_preview()

    def add_marker(self) -> None:
        recorder = self.processes.get("recorder")
        if not recorder or recorder.poll() is not None or not self.current_recording_paths:
            self.log("recorder", "No active recorder to mark.")
            return
        marker_file = self.current_recording_paths["marker_file"]
        append_marker_line(marker_file)
        self.log("recorder", f"marker appended to {marker_file}")

    def stop_recording(self, *, auto_analyze: bool | None = None) -> None:
        recorder = self.processes.get("recorder")
        if not recorder or recorder.poll() is not None:
            self.log("recorder", "Recorder is not running.")
            return
        config = self.config_from_vars()
        should_analyze = bool(config.get("auto_analyze_after_stop", True)) if auto_analyze is None else bool(auto_analyze)
        if self.processes.get("route_monitor") and self.processes["route_monitor"].poll() is None:
            self.stop_route_monitor()
        if not self.current_recording_paths:
            self.log("recorder", "No stop file path is known; terminating recorder.")
            self.stop_process("recorder")
            return
        if "recording_chip" in self.status_vars:
            self.status_vars["recording_chip"].set("stopping")
        if "recommendation" in self.status_vars:
            self.status_vars["recommendation"].set(analysis_progress_text("stopping"))
        stop_file = ensure_stop_file(self.current_recording_paths["stop_file"])
        self.log("recorder", f"stop file created: {stop_file}")

        def wait_then_force() -> None:
            try:
                recorder.wait(timeout=5)
                self.log("recorder", f"clean stop completed with code {recorder.returncode}")
            except subprocess.TimeoutExpired:
                self.log("recorder", "clean stop timed out; terminating recorder.")
                self.stop_process("recorder", grace_seconds=2)
            latest = latest_recording_dir_for_config(config)
            update_ui_recording_manifest(
                {
                    "stoppedAtUtc": utc_now(),
                    "recordingFolder": str(latest) if latest else None,
                }
            )
            self.root.after(0, self.refresh_status)
            if should_analyze:
                self.root.after(0, self.analyze_latest_recording)

        threading.Thread(target=wait_then_force, daemon=True).start()

    def analyze_latest_recording(self) -> None:
        config = config_for_analysis_run(self.config_from_vars())
        latest = latest_recording_dir_for_config(config)
        if latest is None:
            self.log("analyzer", "No recording folder found.")
            return
        self.last_recording_path = latest
        command = build_analyzer_command(latest, config)
        update_ui_recording_manifest(
            {
                "recordingFolder": str(latest),
                "analyzerCommand": command_text(command),
            }
        )
        self.status_vars["recording_chip"].set("analyzing")
        self.start_process("analyzer", command)

    def extract_route_template_from_latest(self) -> None:
        latest = latest_recording_dir_for_config(self.config_from_vars())
        if latest is None:
            self.log("analyzer", "No recording folder found.")
            return
        config = self.config_from_vars()
        self.last_recording_path = latest
        self.start_process("analyzer", build_extract_route_template_command(latest, config))

    def compare_latest_to_route_template(self) -> None:
        latest = latest_recording_dir_for_config(self.config_from_vars())
        if latest is None:
            self.log("analyzer", "No recording folder found.")
            return
        config = self.config_from_vars()
        if resolve_template_from_config(config).get("status") != "PASS":
            self.log("analyzer", "Route template is missing or invalid; compare was not started.")
            return
        self.last_recording_path = latest
        self.start_process("analyzer", build_compare_route_template_command(latest, config))

    def register_latest_as_route_variant(self) -> None:
        latest = latest_recording_dir_for_config(self.config_from_vars())
        if latest is None:
            self.log("analyzer", "No recording folder found.")
            return
        config = self.config_from_vars()
        if resolve_template_from_config(config).get("status") != "PASS":
            self.log("analyzer", "Route template is missing or invalid; variant registration was not started.")
            return
        self.last_recording_path = latest
        self.start_process("analyzer", build_register_route_variant_command(latest, config))

    def check_route_readiness(self) -> None:
        config = self.config_from_vars()
        if resolve_template_from_config(config).get("status") != "PASS":
            self.log("route", "Route template is missing or invalid; readiness check was not started.")
            return
        self.start_process("analyzer", build_route_readiness_command(config))

    def monitor_latest_route_recording(self) -> None:
        latest = latest_recording_dir_for_config(self.config_from_vars())
        if latest is None:
            self.log("route", "No recording folder found.")
            return
        config = self.config_from_vars()
        if resolve_template_from_config(config).get("status") != "PASS":
            self.log("route", "Route template is missing or invalid; recording monitor was not started.")
            return
        self.last_recording_path = latest
        self.start_process("analyzer", build_route_monitor_recording_command(latest, config))

    def start_route_monitor(self) -> None:
        plan = build_route_session_plan(self.route_name_var.get() or None, self.config_from_vars())
        if not plan.get("canStart"):
            self.log("route", "Template validation failed; route monitor was not started.")
            for warning in plan.get("warnings") or []:
                self.log("route", str(warning))
            return
        self.apply_config_to_vars(plan["config"])
        self.log("route", f"Starting monitor for {plan.get('routeName')} rev {plan.get('templateRevision')}.")
        self.start_process("route_monitor", plan.get("routeMonitorCommand"))

    def stop_route_monitor(self) -> None:
        self.stop_process("route_monitor")

    def start_route_session(self) -> None:
        plan = build_route_session_plan(self.route_name_var.get() or None, self.config_from_vars())
        if not plan.get("canStart"):
            self.log("route", "Start Route Session is blocked: template is not loaded.")
            for warning in plan.get("warnings") or []:
                self.log("route", str(warning))
            return
        self.apply_config_to_vars(plan["config"])
        self.current_recording_paths = {key: Path(value) for key, value in (plan.get("recordingControlPaths") or {}).items()}
        stop_file = self.current_recording_paths["stop_file"]
        marker_file = self.current_recording_paths["marker_file"]
        panic_file = self.current_recording_paths["mirror_panic_stop_file"]
        try:
            stop_file.parent.mkdir(parents=True, exist_ok=True)
            marker_file.parent.mkdir(parents=True, exist_ok=True)
            if stop_file.exists():
                stop_file.unlink()
            if panic_file.exists():
                panic_file.unlink()
            marker_file.write_text("", encoding="utf-8")
        except OSError as error:
            self.log("route", f"control file setup failed: {type(error).__name__}: {error}")
            return
        manifest = {
            "schema": "route_session_manifest.v1",
            "startedAtUtc": utc_now(),
            "presetName": plan.get("presetName") or PRESET_ROUTE,
            "recordingLabel": plan.get("recordingLabel"),
            "routeMonitorSessionFolder": plan.get("routeMonitorSessionFolder"),
            "routeMonitorFolder": plan.get("routeMonitorSessionFolder"),
            "plannedRouteMonitorFolder": plan.get("routeMonitorSessionFolder"),
            "actualRouteMonitorFolder": plan.get("routeMonitorSessionFolder"),
            "recordingFolder": None,
            "templatePath": plan.get("templatePath"),
            "templateResolvedPath": plan.get("templatePath"),
            "routeName": plan.get("routeName"),
            "templateRevision": plan.get("templateRevision"),
            "routeSessionId": plan.get("sessionId"),
            "sessionId": plan.get("sessionId"),
            "recordingControlPaths": plan.get("recordingControlPaths"),
            "routeMonitorStarted": False,
            "routeMonitorStartupStatus": "not_started",
        }
        manifest_path = ui_control_dir() / "route_session" / "route_session_manifest.json"
        telemetry_sources.atomic_write_json(manifest_path, manifest, pretty=True)
        self.log("route", f"Template loaded: {plan.get('routeName')} rev {plan.get('templateRevision')}.")
        self.log("route", f"Monitor folder: {plan.get('routeMonitorSessionFolder')}")
        route_monitor_process = self.start_process("route_monitor", plan.get("routeMonitorCommand"))
        manifest["routeMonitorStarted"] = bool(route_monitor_process and route_monitor_process.poll() is None)
        manifest["routeMonitorStartupStatus"] = "started" if manifest["routeMonitorStarted"] else "failed"
        telemetry_sources.atomic_write_json(manifest_path, manifest, pretty=True)
        self.start_process("recorder", plan.get("recorderCommand"))

    def stop_route_session(self) -> None:
        self.stop_recording(auto_analyze=False)
        self.stop_route_monitor()

        def analyze_after_stop() -> None:
            latest = latest_recording_dir_for_config(self.config_from_vars())
            if latest is None:
                self.log("route", "No recording folder found after route session stop.")
                return
            config = config_for_preset(self.config_from_vars(), PRESET_ROUTE)
            self.last_recording_path = latest
            manifest_path = ui_control_dir() / "route_session" / "route_session_manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
            except (OSError, json.JSONDecodeError):
                manifest = {}
            actual_folder = str(manifest.get("actualRouteMonitorFolder") or manifest.get("plannedRouteMonitorFolder") or manifest.get("routeMonitorSessionFolder") or "")
            if actual_folder and not Path(actual_folder).exists():
                session_id = str(manifest.get("routeSessionId") or manifest.get("sessionId") or "").strip()
                route_name = str(manifest.get("routeName") or route_template.DEFAULT_ROUTE_NAME).strip()
                candidate = Path.home() / ".osrs-telemetry" / "route_monitor" / route_name / session_id
                if session_id and candidate.exists():
                    self.log("route", "Route monitor folder mismatch; using actual folder.")
                    actual_folder = str(candidate)
            manifest.update(
                {
                    "stoppedAtUtc": utc_now(),
                    "recordingFolder": str(latest),
                    "actualRouteMonitorFolder": actual_folder or manifest.get("actualRouteMonitorFolder"),
                }
            )
            telemetry_sources.atomic_write_json(manifest_path, manifest, pretty=True)
            self.start_process("analyzer", build_analyzer_command(latest, config))

        self.root.after(6500, analyze_after_stop)

    def open_route_monitor_folder(self) -> None:
        self.open_path(Path.home() / ".osrs-telemetry" / "route_monitor", label="route monitor folder")

    def open_path(self, path: Path | None, *, label: str) -> None:
        if path is None or not path.exists():
            self.log("UI", f"{label} not found.")
            return
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except AttributeError:
            webbrowser.open(path.as_uri())
        except OSError as error:
            self.log("UI", f"Could not open {label}: {type(error).__name__}: {error}")

    def open_recordings_folder(self) -> None:
        path = resolve_output_folder(self.config_from_vars())
        path.mkdir(parents=True, exist_ok=True)
        self.open_path(path, label="recordings folder")

    def open_output_folder(self) -> None:
        path = resolve_output_folder(self.config_from_vars())
        path.mkdir(parents=True, exist_ok=True)
        self.open_path(path, label="output folder")

    def open_project_knowledge(self) -> None:
        path = repo_root() / "docs" / "knowledge" / "PROJECT_STATE.md"
        if not path.exists():
            path = repo_root() / "docs" / "knowledge"
        self.open_path(path, label="project knowledge")

    def open_project_bootstrap_report(self) -> None:
        self.open_path(repo_root() / "docs" / "project_bootstrap_sweep.md", label="project bootstrap report")

    def open_recording_index(self) -> None:
        path = repo_root() / "docs" / "knowledge" / "RECORDING_INDEX.md"
        if not path.exists():
            path = repo_root() / "telemetry-viewer" / "knowledge_base" / "recordings_index.json"
        self.open_path(path, label="recording index")

    def open_open_gaps(self) -> None:
        path = repo_root() / "docs" / "knowledge" / "OPEN_GAPS.md"
        if not path.exists():
            path = repo_root() / "telemetry-viewer" / "knowledge_base" / "open_gaps.json"
        self.open_path(path, label="open gaps")

    def refresh_project_knowledge(self) -> None:
        command = build_project_knowledge_command(self.config_from_vars(), write_docs=True)
        if not command:
            self.log("knowledge", "update_project_knowledge.py not found.")
            return
        self.start_process("knowledge", command)

    def run_bootstrap_check(self) -> None:
        command = build_bootstrap_check_command()
        if not command:
            self.log("knowledge", "update_project_knowledge.py not found.")
            return
        self.start_process("knowledge", command)

    def run_command_registry_check(self) -> None:
        command = build_command_registry_check_command()
        if not command:
            self.log("commands", "command_registry.py not found.")
            return
        self.start_process("knowledge", command)

    def run_input_geometry_check(self) -> None:
        command = build_input_geometry_check_command()
        if not command:
            self.log("commands", "bot_eval_runner.py not found.")
            return
        self.start_process("knowledge", command)

    def generate_human_click_profile(self) -> None:
        command = build_human_click_profile_command(self.config_from_vars())
        if not command:
            self.log("knowledge", "No latest recording found for human click profile.")
            return
        self.start_process("knowledge", command)

    def open_human_click_profile(self) -> None:
        path = repo_root() / "docs" / "human_click_profile.md"
        if not path.exists():
            path = repo_root() / "telemetry-viewer" / "knowledge_base" / "human_click_profile.json"
        self.open_path(path, label="human click profile")

    def open_latest_summary(self) -> None:
        latest = latest_recording_dir_for_config(self.config_from_vars())
        self.open_path((latest / "summary.json") if latest else None, label="latest summary")

    def open_latest_schema_gap(self) -> None:
        latest = latest_recording_dir_for_config(self.config_from_vars())
        self.open_path((latest / "schema_gap_report.md") if latest else None, label="latest schema gap")

    def open_latest_artifact(self, filename: str) -> None:
        latest = latest_recording_dir_for_config(self.config_from_vars())
        self.open_path((latest / filename) if latest else None, label=filename)

    def open_route_template(self) -> None:
        resolution = resolve_template_from_config(self.config_from_vars())
        path = Path(str(resolution.get("resolvedPath"))) if resolution.get("resolvedPath") else None
        self.open_path(path, label="route template")

    def open_selected_artifact(self) -> None:
        selection = getattr(self, "artifact_var", tk.StringVar(value="schema_gap_report.md")).get()
        filename = ARTIFACT_OPTIONS.get(selection)
        if filename is None:
            self.open_recordings_folder()
        else:
            self.open_latest_artifact(filename)

    def refresh_arduino_ports(self) -> None:
        ports = arduino_input_bridge.discover_arduino_ports()
        if not ports:
            self.log("arduino", "No serial ports discovered.")
            return
        first = ports[0]
        if not self.arduino_port_var.get().strip() and first.get("device"):
            self.arduino_port_var.set(str(first.get("device")))
        self.log("arduino", "ports: " + ", ".join(str(port.get("device")) for port in ports if port.get("device")))

    def arduino_status_payload(self, config: dict[str, Any]) -> dict[str, Any]:
        try:
            return arduino_input_bridge.status_payload(
                str(config.get("arduino_port") or "").strip() or None,
                baud=int(str(config.get("arduino_baud") or "115200")),
            )
        except Exception as error:  # noqa: BLE001
            return {"status": "unavailable", "available": False, "warnings": [f"{type(error).__name__}: {error}"]}

    def run_arduino_status(self) -> None:
        self.start_process("arduino_bridge", build_arduino_status_command(self.config_from_vars()))

    def run_arduino_probe(self) -> None:
        out = ui_control_dir() / "arduino_probe"
        out.mkdir(parents=True, exist_ok=True)
        self.log("arduino", "Arduino probe starting. Keep the pointer in a safe area; the probe sends a small relative move.")
        self.start_process("arduino_bridge", build_arduino_probe_command(self.config_from_vars(), out=out))

    def run_live_mirror_test(self) -> None:
        out = ui_control_dir() / "arduino_live_mirror_test"
        out.mkdir(parents=True, exist_ok=True)
        panic_file = out / "live_mirror_test.panic"
        try:
            if panic_file.exists():
                panic_file.unlink()
        except OSError:
            pass
        config = self.config_from_vars()
        test_arm_delay = str(config.get("mirror_test_arm_delay_ms") or "2000")
        self.log(
            "arduino",
            f"Live mirror test will arm after {test_arm_delay} ms for {config.get('mirror_test_duration_sec') or '5'} seconds. Move slightly and click once inside RuneLite.",
        )
        self.start_process("arduino_bridge", build_live_mirror_test_command(config, out=out))

    def panic_stop_mirror(self) -> None:
        paths = []
        if self.current_recording_paths and self.current_recording_paths.get("mirror_panic_stop_file"):
            paths.append(self.current_recording_paths["mirror_panic_stop_file"])
        paths.append(ui_control_dir() / "arduino_live_mirror_test" / "live_mirror_test.panic")
        for path in paths:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"panic stop requested at {utc_now()}\n", encoding="utf-8")
                self.log("arduino", f"mirror panic stop file created: {path}")
            except OSError as error:
                self.log("arduino", f"panic stop failed for {path}: {type(error).__name__}: {error}")

    def run_input_smoke_test(self) -> None:
        config = self.config_from_vars()
        out = ui_control_dir() / "input_smoke_test"
        out.mkdir(parents=True, exist_ok=True)
        self.log("input", "Smoke test starting. Move the mouse, click, and press an arrow key while it runs.")
        self.start_process("input_smoke_test", build_input_smoke_test_command(config, out=out))

    def log_input_smoke_status(self) -> None:
        path = ui_control_dir() / "input_smoke_test" / "input_capture_smoke_test.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            self.log("input", "Smoke test result file was not found.")
            return
        counts = payload.get("eventCounts") if isinstance(payload.get("eventCounts"), dict) else {}
        text = (
            f"smoke success={payload.get('success')} "
            f"backend={payload.get('backendUsed')} "
            f"moves={counts.get('moves')} "
            f"clicks={counts.get('clicks')} "
            f"key_downs={counts.get('key_downs')} "
            f"reason={payload.get('reason')}"
        )
        self.log("input", text)
        if "input_capture_status" in self.status_vars:
            self.status_vars["input_capture_status"].set(text)

    def log_arduino_probe_status(self) -> None:
        path = ui_control_dir() / "arduino_probe" / "arduino_probe_result.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        ack = payload.get("ack") if isinstance(payload.get("ack"), dict) else {}
        observed = payload.get("observed") if isinstance(payload.get("observed"), dict) else {}
        text = (
            f"probe classification={payload.get('classification')} "
            f"success={payload.get('success')} "
            f"observed_dx={observed.get('dx')} observed_dy={observed.get('dy')} "
            f"acks={ack.get('count')} reason={payload.get('reason')}"
        )
        self.log("arduino", text)
        if "arduino_status" in self.status_vars:
            self.status_vars["arduino_status"].set(text)

    def start_arduino_bridge(self) -> None:
        config = self.config_from_vars()
        if str(config.get("arduino_passthrough_mode") or "off") == "mirror":
            self.log("arduino", "Mirror mode selected; running probe preflight instead of claiming passive bridge status is mirror verification.")
            self.run_arduino_probe()
            return
        out = ui_control_dir() / "arduino_bridge"
        out.mkdir(parents=True, exist_ok=True)
        self.start_process("arduino_bridge", build_arduino_bridge_command(config, out=out))

    def calibrate_arduino(self) -> None:
        config = self.config_from_vars()
        command = build_arduino_status_command(config)
        command.append("--calibrate")
        calibration = str(config.get("arduino_calibration_profile") or "").strip()
        if calibration:
            command.extend(["--out", calibration])
        self.start_process("arduino_bridge", command)

    def log_input_analysis_status(self, recording: str | Path | None) -> dict[str, Any]:
        result = safe_analysis_result(recording)
        if not recording:
            if "analysis_chip" in self.status_vars:
                self.status_vars["analysis_chip"].set("WARN")
            if "recommendation" in self.status_vars:
                self.status_vars["recommendation"].set(str(result.get("biggestWarning") or "no recording folder found"))
            return result
        summary_path = Path(recording) / "summary.json"
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            if "analysis_chip" in self.status_vars:
                self.status_vars["analysis_chip"].set("WARN")
            if "recommendation" in self.status_vars:
                self.status_vars["recommendation"].set(str(result.get("biggestWarning") or "analysis report missing"))
            update_ui_recording_manifest(
                {
                    "recordingFolder": str(Path(recording)),
                    "finalVerdict": "WARN",
                    "finalReportPath": result.get("reportPath"),
                    "warnings": [str(result.get("biggestWarning") or "analysis report missing")],
                }
            )
            return result
        input_trace = summary.get("input_trace") if isinstance(summary.get("input_trace"), dict) else {}
        click = summary.get("click_analysis") if isinstance(summary.get("click_analysis"), dict) else {}
        camera = summary.get("camera_behavior") if isinstance(summary.get("camera_behavior"), dict) else {}
        arduino = summary.get("arduino_trace") if isinstance(summary.get("arduino_trace"), dict) else {}
        action = summary.get("input_action_summary") if isinstance(summary.get("input_action_summary"), dict) else {}
        target_quality = summary.get("target_match_summary") if isinstance(summary.get("target_match_summary"), dict) else {}
        menu_interaction = summary.get("menu_interaction_summary") if isinstance(summary.get("menu_interaction_summary"), dict) else {}
        coordinate = summary.get("coordinate_alignment_summary") if isinstance(summary.get("coordinate_alignment_summary"), dict) else {}
        banking = summary.get("banking_lifecycle") if isinstance(summary.get("banking_lifecycle"), dict) else {}
        interruption = summary.get("interruption_lifecycle") if isinstance(summary.get("interruption_lifecycle"), dict) else {}
        combat_damage = summary.get("combat_damage_summary") if isinstance(summary.get("combat_damage_summary"), dict) else {}
        woodcutting_loop = summary.get("woodcutting_loop_lifecycle") if isinstance(summary.get("woodcutting_loop_lifecycle"), dict) else {}
        traversal = summary.get("traversal_lifecycle") if isinstance(summary.get("traversal_lifecycle"), dict) else {}
        route_comparison = summary.get("route_template_comparison") if isinstance(summary.get("route_template_comparison"), dict) else {}
        route_monitor = summary.get("route_monitor") if isinstance(summary.get("route_monitor"), dict) else {}
        route_history = summary.get("route_history") if isinstance(summary.get("route_history"), dict) else {}
        human_profile = summary.get("human_click_profile") if isinstance(summary.get("human_click_profile"), dict) else {}
        input_path = summary.get("input_path_integrity_summary") if isinstance(summary.get("input_path_integrity_summary"), dict) else {}
        click_ownership = summary.get("click_ownership_summary") if isinstance(summary.get("click_ownership_summary"), dict) else {}
        if not click_ownership and isinstance(input_path.get("clickOwnershipSummary"), dict):
            click_ownership = input_path.get("clickOwnershipSummary")
        live_mirror = summary.get("arduino_live_mirror") if isinstance(summary.get("arduino_live_mirror"), dict) else {}
        mirror_timing = summary.get("mirror_action_timing") if isinstance(summary.get("mirror_action_timing"), dict) else {}
        verdict = mirror_timing.get("finalMirrorRecordingVerdict") or summary.get("status") or "WARN"
        activity = detected_activity_type(summary)
        warning = biggest_analysis_warning(summary)
        loop_phase_summary = woodcutting_loop_simple_phase_summary(woodcutting_loop)
        if "analysis_chip" in self.status_vars:
            self.status_vars["analysis_chip"].set(str(verdict))
        if "recommendation" in self.status_vars:
            if loop_phase_summary and activity == "Woodcutting Loop":
                self.status_vars["recommendation"].set(f"Detected: Woodcutting Loop; {loop_phase_summary}; warning={warning}")
            else:
                self.status_vars["recommendation"].set(f"Detected: {activity}; verdict={verdict}; warning={warning}")
        if "latest_analysis" in self.status_vars:
            loop_text = f"phases={loop_phase_summary} " if loop_phase_summary else ""
            self.status_vars["latest_analysis"].set(
                f"detected={activity} verdict={verdict} input_events={_safe_int(input_trace.get('eventCount')) if input_trace else '-'} "
            f"{loop_text}"
            f"menu={_safe_int(menu_interaction.get('menuSelectionCount')) if menu_interaction else '-'} "
            f"row_geometry={_safe_int(menu_interaction.get('menuSelectionsWithRowGeometryCount')) if menu_interaction else '-'} "
            f"banking={banking.get('status') if banking else '-'} "
            f"interruption={interruption.get('interruptionType') if interruption else '-'} "
            f"cause={interruption.get('primaryCause') if interruption else '-'} "
            f"damage_taken={_safe_int(_dict(combat_damage.get('damageTaken')).get('total')) if combat_damage else '-'} "
            f"damage_dealt={_safe_int(_dict(combat_damage.get('damageDealt')).get('total')) if combat_damage else '-'} "
            f"loop={woodcutting_loop.get('loopState') if woodcutting_loop else '-'} "
            f"next={_dict(woodcutting_loop.get('nextExpectedPhase')).get('phase') if woodcutting_loop else '-'} "
            f"traversal={traversal.get('status') if traversal else '-'} "
            f"segments={traversal.get('routeSegmentCount') if traversal else '-'} "
            f"review={traversal.get('reviewEvidenceCount') if traversal else '-'} "
            f"template={route_comparison.get('status') if route_comparison else '-'} "
            f"template_score={route_comparison.get('score') if route_comparison else '-'} "
            f"route_state={route_monitor.get('routeState') if route_monitor else '-'} "
            f"history_state={route_history.get('routeState') if route_history else '-'} "
            f"human_clicks={human_profile.get('imperfectSuccessfulClickCount') if human_profile else '-'} "
            f"mirror_verified={input_path.get('liveMirrorVerified') if input_path else '-'} "
                f"duplicate_clicks={click_ownership.get('duplicateClickLikelyCount') if click_ownership else '-'} "
                f"warning={warning}"
            )
        report_path = Path(recording) / "schema_gap_report.md"
        if not report_path.exists():
            report_path = summary_path
        update_ui_recording_manifest(
            {
                "recordingFolder": str(Path(recording)),
                "detectedActivityType": activity,
                "finalVerdict": str(verdict),
                "finalReportPath": str(report_path),
                "biggestWarning": warning,
                "knowledgeUpdated": bool(summary.get("knowledgeUpdated")),
                "knowledgeIndexPath": summary.get("knowledgeIndexPath"),
                "warnings": [warning] if warning and warning != "none" else [],
            }
        )
        if summary.get("knowledgeUpdated"):
            self.log("knowledge", f"updated: {summary.get('knowledgeIndexPath')}")
        elif summary.get("knowledgeUpdate"):
            self.log("knowledge", "knowledge update did not complete; see summary.json.")
        if input_trace:
            if int(input_trace.get("realEventCount") or 0) <= 0 and int(input_trace.get("eventCount") or 0) > 0:
                self.log("input", "Input capture failed to observe real input. Run Smoke Test or use polling backend.")
            self.log(
                "input",
                (
                    f"status={input_trace.get('captureStatus')} "
                    f"events={input_trace.get('eventCount')} "
                    f"real={input_trace.get('realEventCount')} "
                    f"moves={input_trace.get('mouseMoveCount')} "
                    f"clicks={input_trace.get('clickCount')} "
                    f"keys={input_trace.get('keyboardEventCount')} "
                    f"target_relative={click.get('targetRelativeClickCount')}"
                ),
            )
        if action:
            self.log(
                "input",
                (
                    f"classification={action.get('status')} "
                    f"raw={action.get('rawOsClickCount')} "
                    f"eligible={action.get('eligibleGameActionClickCount')} "
                    f"target_relative={action.get('targetRelativeClickCount')} "
                    f"camera_drag_releases={action.get('cameraDragReleaseCount')} "
                    f"ui={action.get('uiControlClickCount')} "
                    f"minimap={action.get('minimapClickCount')} "
                    f"menu_selection={action.get('menuSelectionClickCount')} "
                    f"ambiguous={action.get('ambiguousClickCount')}"
                ),
            )
        if target_quality:
            counts = target_quality.get("qualityCounts") if isinstance(target_quality.get("qualityCounts"), dict) else {}
            examples = target_quality.get("examples") if isinstance(target_quality.get("examples"), list) else []
            self.log(
                "input",
                (
                    f"target_quality status={target_quality.get('status')} "
                    f"target_relative={target_quality.get('targetRelativeClickCount')} "
                    f"strong={counts.get('strong')} medium={counts.get('medium')} "
                    f"weak={counts.get('weak')} unmatched={counts.get('unmatched')}"
                ),
            )
            for item in examples[:3]:
                if isinstance(item, dict):
                    self.log(
                        "input",
                        (
                            f"target_quality_example event={item.get('eventSeq')} "
                            f"{item.get('classification')} {item.get('targetName')}/{item.get('targetAction')} "
                            f"quality={item.get('quality')} score={item.get('score')}"
                        ),
                    )
        if menu_interaction:
            diagnostics = menu_interaction.get("menuRowDiagnostics") if isinstance(menu_interaction.get("menuRowDiagnostics"), list) else []
            missing = next((item for item in diagnostics if isinstance(item, dict) and not item.get("rowBoundsPresent")), {})
            self.log(
                "input",
                (
                    f"menu_interactions status={menu_interaction.get('status')} "
                    f"opens={menu_interaction.get('rightClickMenuOpenCount')} "
                    f"selections={menu_interaction.get('menuSelectionCount')} "
                    f"row_geometry={menu_interaction.get('menuSelectionsWithRowGeometryCount')} "
                    f"linked_targets={menu_interaction.get('menuSelectionsLinkedToTargetsCount')} "
                    f"missing_row_geometry={menu_interaction.get('menuSelectionsMissingRowGeometryCount')}"
                ),
            )
            if diagnostics:
                self.log(
                    "input",
                    (
                        f"row_geometry_consistency={menu_interaction.get('status')} "
                        f"first_missing_reason={missing.get('missingRowGeometryReason') if missing else 'none'}"
                    ),
                )
        if human_profile:
            landing = human_profile.get("landing") if isinstance(human_profile.get("landing"), dict) else {}
            camera_profile = human_profile.get("camera") if isinstance(human_profile.get("camera"), dict) else {}
            self.log(
                "input",
                (
                    f"human_click_profile status={human_profile.get('status')} "
                    f"target_relative={_safe_dict(human_profile.get('clicks')).get('targetRelativeClicks')} "
                    f"median_aim_px={landing.get('medianAimDistancePx')} "
                    f"camera_segments={camera_profile.get('cameraSegmentCount')} "
                    f"imperfect_success={human_profile.get('imperfectSuccessfulClickCount')}"
                ),
            )
        if banking:
            deposit = banking.get("deposit") if isinstance(banking.get("deposit"), dict) else {}
            bank = banking.get("bank") if isinstance(banking.get("bank"), dict) else {}
            deposited = deposit.get("items") if isinstance(deposit.get("items"), list) else []
            first_item = deposited[0] if deposited and isinstance(deposited[0], dict) else {}
            item_text = (
                f"{first_item.get('name')} x{first_item.get('quantity')}"
                if first_item
                else "none"
            )
            self.log(
                "input",
                (
                    f"banking status={banking.get('status')} "
                    f"phase={banking.get('phase')} "
                    f"deposit={deposit.get('detected')} "
                    f"deposited={item_text} "
                    f"bank_open={bank.get('openSeen')} "
                    f"bank_container={bank.get('containerAvailable')} "
                    f"bank_delta={banking.get('bankContainerDeltaAvailable') or bank.get('bankContainerDeltaAvailable')} "
                    f"bank_ui={bank.get('bankUiPresent')} "
                    f"warning={(banking.get('warnings') or ['none'])[0] if isinstance(banking.get('warnings'), list) else 'none'}"
                ),
            )
        if interruption:
            combat = interruption.get("combat") if isinstance(interruption.get("combat"), dict) else {}
            self.log(
                "input",
                (
                    f"interruption status={interruption.get('status')} "
                    f"type={interruption.get('interruptionType')} "
                    f"cause={interruption.get('primaryCause')} "
                    f"resumed={interruption.get('taskResumed')} "
                    f"combat={combat.get('combatObserved')} "
                    f"hitsplats={combat.get('hitsplatsSeen')} "
                    f"warning={(interruption.get('warnings') or ['none'])[0] if isinstance(interruption.get('warnings'), list) else 'none'}"
                ),
            )
        if combat_damage:
            opponent = combat_damage.get("primaryOpponent") if isinstance(combat_damage.get("primaryOpponent"), dict) else {}
            taken = combat_damage.get("damageTaken") if isinstance(combat_damage.get("damageTaken"), dict) else {}
            dealt = combat_damage.get("damageDealt") if isinstance(combat_damage.get("damageDealt"), dict) else {}
            hitsplats = combat_damage.get("hitsplats") if isinstance(combat_damage.get("hitsplats"), dict) else {}
            health = combat_damage.get("health") if isinstance(combat_damage.get("health"), dict) else {}
            self.log(
                "input",
                (
                    f"combat damage status={combat_damage.get('status')} "
                    f"opponent={opponent.get('name') or '-'} "
                    f"taken={taken.get('total')} dealt={dealt.get('total')} "
                    f"hitsplats={hitsplats.get('total')} "
                    f"hp={health.get('hpBefore')}->{health.get('hpAfter')} "
                    f"task_resumed={_dict(combat_damage.get('taskResume')).get('taskResumed')} "
                    f"warning={(combat_damage.get('warnings') or ['none'])[0] if isinstance(combat_damage.get('warnings'), list) else 'none'}"
                ),
            )
        if coordinate:
            self.log(
                "input",
                (
                    f"coordinate_alignment status={coordinate.get('status')} "
                    f"scale={coordinate.get('detectedDpiScale')} "
                    f"transform={coordinate.get('chosenTransform')} "
                    f"raw_row_hits={coordinate.get('rawMenuRowHitCount')} "
                    f"normalized_row_hits={coordinate.get('normalizedMenuRowHitCount')}"
                ),
            )
        if traversal:
            movement = traversal.get("movement") if isinstance(traversal.get("movement"), dict) else {}
            plane_changes = movement.get("planeChanges") if isinstance(movement.get("planeChanges"), list) else []
            warning = (traversal.get("warnings") or ["none"])[0] if isinstance(traversal.get("warnings"), list) else "none"
            self.log(
                "input",
                (
                    f"traversal status={traversal.get('status')} "
                    f"route={traversal.get('routeName')} "
                    f"segments={traversal.get('routeSegmentCount') or traversal.get('stepCount')} "
                    f"success={traversal.get('successfulSegmentCount') or traversal.get('successfulStepCount')} "
                    f"partial={traversal.get('partialSegmentCount') or traversal.get('partialStepCount')} "
                    f"review={traversal.get('reviewEvidenceCount')} "
                    f"plane_changes={len(plane_changes)} "
                    f"warning={warning}"
                ),
            )
        if route_comparison:
            self.log(
                "input",
                (
                    f"route_template status={route_comparison.get('status')} "
                    f"reason={route_comparison.get('statusReason')} "
                    f"score={route_comparison.get('score')} "
                    f"variant={route_comparison.get('matchedVariantName')} "
                    f"matched={route_comparison.get('matchedSegmentCount')}/{route_comparison.get('requiredSegmentCount')} "
                    f"missing={len(route_comparison.get('missingSegments') or [])} "
                    f"extra={len(route_comparison.get('extraSegments') or [])} "
                    f"allowed_extra={len(route_comparison.get('allowedExtraSegments') or [])} "
                    f"nav_subs={len(route_comparison.get('navigationSupportSubstitutions') or [])} "
                    f"warning={(route_comparison.get('warnings') or ['none'])[0] if isinstance(route_comparison.get('warnings'), list) else 'none'}"
                ),
            )
        if route_monitor:
            next_segment = route_monitor.get("nextExpectedSegment") if isinstance(route_monitor.get("nextExpectedSegment"), dict) else {}
            self.log(
                "input",
                (
                    f"route_monitor status={route_monitor.get('status')} "
                    f"state={route_monitor.get('routeState')} "
                    f"current_area={route_monitor.get('currentArea')} "
                    f"completed={route_monitor.get('completedSegmentCount')} "
                    f"remaining={route_monitor.get('remainingSegmentCount')} "
                    f"next={next_segment.get('label')} "
                    f"off_route={route_monitor.get('offRoute')}"
                ),
            )
        if route_history:
            next_segment = route_history.get("nextExpectedSegment") if isinstance(route_history.get("nextExpectedSegment"), dict) else {}
            self.log(
                "input",
                (
                    f"route_history status={route_history.get('status')} "
                    f"state={route_history.get('routeState')} "
                    f"current_area={route_history.get('currentArea')} "
                    f"completed={route_history.get('completedSegmentCount')} "
                    f"remaining={route_history.get('remainingSegmentCount')} "
                    f"next={next_segment.get('label')} "
                    f"off_route={route_history.get('offRoute')}"
                ),
            )
        if input_path:
            self.log(
                "arduino",
                (
                    f"input_path={input_path.get('inputPathClassification')} "
                    f"mode={input_path.get('requestedMode')} "
                    f"mirror={input_path.get('mirrorVerificationStatus')} "
                    f"probe={input_path.get('probeVerified')} "
                    f"commands={input_path.get('commandCount')} "
                    f"move_cmds={input_path.get('movementCommandCount')} "
                    f"click_cmds={input_path.get('clickCommandCount')} "
                    f"acks={input_path.get('ackCount')} "
                    f"corr_move={input_path.get('correlatedCommandToObservedMovementCount')} "
                    f"corr_click={input_path.get('correlatedCommandToObservedClickCount')} "
                    f"max_click_s={input_path.get('maxClickCommandsPerSecond')} "
                    f"safety={','.join(input_path.get('liveMirrorSafetyClassifications') or []) or 'none'}"
                ),
            )
        if click_ownership:
            self.log(
                "arduino",
                (
                    f"click_ownership status={click_ownership.get('status')} "
                    f"policy={click_ownership.get('clickPolicyUsed')} "
                    f"os_clicks={click_ownership.get('totalOsClicks')} "
                    f"live_clicks={click_ownership.get('totalArduinoLiveClickCommands')} "
                    f"map_only={click_ownership.get('mapOnlyClickCount')} "
                    f"duplicates={click_ownership.get('duplicateClickLikelyCount')} "
                    f"owners={click_ownership.get('clickOwners')}"
                ),
            )
        if mirror_timing:
            self.log(
                "arduino",
                (
                    f"mirror_timing verdict={mirror_timing.get('finalMirrorRecordingVerdict')} "
                    f"arm_mode={mirror_timing.get('armMode')} "
                    f"armed_start={mirror_timing.get('mirrorArmedStartElapsedSeconds')} "
                    f"disarm={mirror_timing.get('mirrorDisarmElapsedSeconds')} "
                    f"menu_after_disarm={mirror_timing.get('menuSelectionsAfterDisarm')} "
                    f"actions_after_disarm={mirror_timing.get('actionClicksAfterDisarm')} "
                    f"post_action_cmds={mirror_timing.get('postActionArduinoCommandCount')} "
                    f"post_action_moves={mirror_timing.get('postActionMovementCommandCount')} "
                    f"post_action_clicks={mirror_timing.get('postActionClickCommandCount')} "
                    f"feedback_suspected={mirror_timing.get('feedbackLoopSuspected')}"
                ),
            )
        if live_mirror:
            self.log(
                "arduino",
                (
                    f"live_mirror status={live_mirror.get('status')} "
                    f"profile={live_mirror.get('mirrorProfile')} "
                    f"active={live_mirror.get('liveMirrorActive')} "
                    f"verified={live_mirror.get('liveMirrorVerified')} "
                    f"non_probe={live_mirror.get('nonProbeActionCommandCount')} "
                    f"moves={live_mirror.get('movementCommandCount')} "
                    f"clicks={live_mirror.get('clickCommandCount')} "
                    f"echo_moves={live_mirror.get('echoSuppressedMoveCount')} "
                    f"echo_clicks={live_mirror.get('echoSuppressedClickCount')} "
                    f"stale_dropped={live_mirror.get('staleCommandsDropped')} "
                    f"auto_pause={live_mirror.get('autoPauseReason')} "
                    f"acks={live_mirror.get('ackCount')} "
                    f"dropped={live_mirror.get('droppedCommandCount')} "
                    f"throttled={live_mirror.get('throttledCommandCount')} "
                    f"panic={live_mirror.get('panicStopCount')} "
                    f"safety={','.join(live_mirror.get('liveMirrorSafetyClassifications') or []) or 'none'}"
                ),
            )
        if camera:
            self.log("camera", f"segments={camera.get('totalCameraSegments')} camera_before_click={camera.get('cameraBeforeClickCount')}")
        if arduino:
            self.log(
                "arduino",
                (
                    f"classification={arduino.get('classification')} "
                    f"events={arduino.get('eventCount')} "
                    f"actions={arduino.get('actionCommandCount')} "
                    f"moves={arduino.get('movementCommandCount')} "
                    f"clicks={arduino.get('clickCommandCount')} "
                    f"acks={arduino.get('ackCount')} errors={arduino.get('errorCount')}"
                ),
            )
        return result

    def stop_all(self) -> None:
        if self.processes.get("recorder") and self.processes["recorder"].poll() is None:
            self.stop_recording(auto_analyze=False)
        for name in ("live_processor", "context_service", "mcp_server", "analyzer", "game", "arduino_bridge", "route_monitor"):
            self.stop_process(name)

    def refresh_status(self) -> None:
        config = self.config_from_vars()

        def worker() -> None:
            snapshot = build_status_snapshot(config)
            self.root.after(0, lambda: self.apply_status(snapshot))

        threading.Thread(target=worker, daemon=True).start()

    def apply_status(self, snapshot: dict[str, Any]) -> None:
        try:
            config = self.config_from_vars()
        except Exception:
            config = dict(getattr(self, "config", {}) or {})

        def set_status(key: str, value: Any) -> None:
            if key in self.status_vars:
                self.status_vars[key].set(str(value))

        set_status("repo_root", str(snapshot.get("repo_root") or "-"))
        set_status("python_executable", str(snapshot.get("python_executable") or "-"))
        set_status("session_path", str(snapshot.get("session_path") or "-"))
        source = snapshot.get("status_source")
        context_status = snapshot.get("context_service_status")
        set_status("context_service_status", f"{context_status} ({source})")
        set_status("diag_context_service", f"{context_status} ({source})")
        freshness = snapshot.get("source_freshness") if isinstance(snapshot.get("source_freshness"), dict) else {}
        freshness_text = f"present {freshness.get('present_count', '-')}/{freshness.get('file_count', '-')} stale {freshness.get('stale_count', '-')} latest age {freshness.get('latest_age_seconds', '-')}"
        set_status("source_freshness", freshness_text)
        set_status("diag_source_freshness", freshness_text)
        set_status("tick_export", f"tick {snapshot.get('latest_tick')} / export {snapshot.get('latest_export_sequence')}")
        set_status("diag_tick_export", f"tick {snapshot.get('latest_tick')} / export {snapshot.get('latest_export_sequence')}")
        latest = snapshot.get("latest_recording_path")
        set_status("last_recording", str(latest or "-"))
        set_status("diag_session_path", str(snapshot.get("session_path") or "-"))
        latest_input, latest_arduino = latest_trace_status_text(latest)
        set_status(
            "input_capture_status",
            latest_input
            or f"capture={bool(config.get('capture_input'))} backend={config.get('input_backend')} preflight={bool(config.get('input_preflight'))} join={bool(config.get('join_input_telemetry'))}"
        )
        set_status(
            "arduino_status",
            latest_arduino
            or f"enabled={bool(config.get('arduino_enabled'))} mode={config.get('arduino_passthrough_mode')} port={config.get('arduino_port') or '-'}"
        )
        telemetry_chip = "fresh" if context_status in {"ok", "PASS", "running"} else ("stale" if freshness.get("present_count") else "stopped")
        stale_count = int(freshness.get("stale_count") or 0)
        session_chip = "fresh" if freshness.get("present_count") and stale_count == 0 else ("stale" if freshness.get("present_count") else "none")
        input_chip = "polling OK" if "captured_real_input" in (latest_input or "") else ("warning" if latest_input else ("off" if not config.get("capture_input") else "polling"))
        arduino_chip = "mirror verified" if "mirror=verified" in (latest_arduino or "") or "verified=True" in (latest_arduino or "") else ("connected" if latest_arduino else ("off" if not config.get("arduino_enabled") else "warning"))
        recording_chip = "recording" if self.processes.get("recorder") and self.processes["recorder"].poll() is None else "idle"
        route_monitor_proc = self.processes.get("route_monitor")
        route_monitor_chip = "monitoring" if route_monitor_proc and route_monitor_proc.poll() is None else "idle"
        route_state = snapshot.get("latest_route_session_state") if isinstance(snapshot.get("latest_route_session_state"), dict) else {}
        if route_state:
            current_segment = route_state.get("currentSegment") if isinstance(route_state.get("currentSegment"), dict) else {}
            next_segment = route_state.get("nextExpectedSegment") if isinstance(route_state.get("nextExpectedSegment"), dict) else {}
            arrival_hint = ""
            if route_state.get("arrivalGateStatus") == "waiting" and not route_state.get("nearEndCluster"):
                arrival_hint = "; arrival candidate: waiting for end cluster"
            elif route_state.get("distanceOnlyProgressRejected"):
                arrival_hint = "; distance progress only, not arrived"
            route_monitor_detail = (
                f"{route_monitor_chip}; state={route_state.get('routeState')} area={route_state.get('currentArea')} "
                f"segment={current_segment.get('label') or route_state.get('currentSegmentLabel') or '-'} "
                f"next={next_segment.get('label') if next_segment else '-'} "
                f"arrival_gate={route_state.get('arrivalGateStatus') or '-'} "
                f"near_end={route_state.get('nearEndCluster') if route_state.get('nearEndCluster') is not None else '-'} "
                f"completed={len(route_state.get('completedSegments') or [])} "
                f"remaining={len(route_state.get('remainingSegments') or [])} "
                f"folder={route_state.get('actualRouteMonitorFolder') or '-'}"
                f"{arrival_hint}"
            )
        else:
            route_monitor_detail = route_monitor_chip
        game_proc = self.processes.get("game")
        game_chip = "running" if game_proc and game_proc.poll() is None else "unknown"
        output = Path(str(snapshot.get("output_folder") or resolve_output_folder(config)))
        output_chip = "ready" if output.exists() else "missing"
        if latest:
            output_chip = Path(str(latest)).name
        set_status("game_chip", game_chip)
        set_status("telemetry_chip", telemetry_chip)
        set_status("session_chip", session_chip)
        self._refresh_route_template_status()
        set_status("diag_route_template_status", self.status_vars.get("route_template_status").get() if self.status_vars.get("route_template_status") else route_template_status_text(config))
        set_status("route_monitor_chip", route_monitor_chip)
        set_status("route_monitor_detail", route_monitor_detail)
        set_status("input_chip", input_chip)
        set_status("arduino_chip", arduino_chip)
        set_status("recording_chip", recording_chip)
        set_status("output_chip", output_chip)
        if self.status_vars.get("analysis_chip") and self.status_vars["analysis_chip"].get() in {"-", ""}:
            set_status("analysis_chip", "none")
        recommendation = "Recording in progress" if recording_chip == "recording" else ("Telemetry stale" if telemetry_chip == "stale" else ("Start Telemetry first" if telemetry_chip == "stopped" else "Ready to record"))
        set_status("recommendation", recommendation)
        warnings = snapshot.get("parser_warnings") or []
        self.log("status", f"{context_status} via {source}; recordings: {snapshot.get('recording_folder_count')}; warnings: {len(warnings)}")
        self._refresh_command_preview()

    def on_close(self) -> None:
        running = [name for name, proc in self.processes.items() if proc and proc.poll() is None]
        if running and not messagebox.askyesno("Stop processes?", "Stop processes started by this UI before closing?"):
            self.root.destroy()
            return
        self.stop_all()
        self.root.destroy()


def run_check() -> int:
    with tempfile.TemporaryDirectory(prefix="osrs_telemetry_ui_check_") as temp_dir:
        config_path = Path(temp_dir) / "telemetry_ui_config.json"
        payload = check_payload(config_path=config_path)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def run_reset_config(*, advanced: bool = False) -> int:
    config = config_for_recording_profile(default_config(), PROFILE_RECORD_EVERYTHING)
    config["ui_mode"] = UI_MODE_SIMPLE
    config["advanced_expanded"] = False
    path = save_config(config)
    payload = {
        "schema": "osrs_telemetry_ui_reset.v1",
        "status": "PASS",
        "configPath": str(path),
        "uiMode": config.get("ui_mode"),
        "recordingProfile": config.get("recording_profile"),
        "outputFolder": config.get("output_folder"),
        "routeTemplatePath": config.get("route_template_path"),
        "routeName": config.get("route_name"),
        "templateRevision": config.get("route_template_revision"),
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OSRS Telemetry Recorder desktop UI.")
    parser.add_argument("--check", action="store_true", help="Run import/config/command/status checks without opening a window.")
    parser.add_argument("--reset-config", action="store_true", help="Reset UI config to recommended simple recording defaults.")
    parser.add_argument("--simple", action="store_true", help="Open Simple Mode. This is the default.")
    parser.add_argument("--diagnostics", action="store_true", help="Open Diagnostics / Settings after the main console.")
    parser.add_argument("--advanced", action="store_true", help="Alias for --diagnostics.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.reset_config:
        reset_code = run_reset_config(advanced=bool(args.advanced))
        if args.check:
            return run_check()
        return reset_code
    if args.check:
        return run_check()
    root_window = tk.Tk()
    app = TelemetryControlApp(root_window)
    if args.advanced or args.diagnostics:
        app.open_diagnostics_settings()
    root_window.protocol("WM_DELETE_WINDOW", app.on_close)
    root_window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

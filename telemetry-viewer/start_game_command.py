from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


SCHEMA = "start_game_command_resolution.v1"
LAUNCH_SCHEMA = "start_game_launch_result.v1"
LAUNCH_MODE_SCHEMA = "start_game_launch_mode.v1"

MODE_DEV_GRADLE_RUN = "dev_gradle_run"
MODE_LAUNCHER_AUTHENTICATED = "launcher_authenticated"
MODE_EXTERNAL_EXISTING_CLIENT = "external_existing_client"
MODE_UNKNOWN = "unknown"

DEV_GRADLE_WARNING = (
    "This launch mode may start RuneLite but may not provide an authenticated/loaded game scene."
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def user_config_path() -> Path:
    return Path.home() / ".osrs-telemetry" / "telemetry_ui_config.json"


def command_text(command: list[str] | str | None) -> str:
    if not command:
        return ""
    if isinstance(command, str):
        return command
    return subprocess.list2cmdline([str(part) for part in command])


def discover_game_launch_command(root: Path | None = None) -> list[str] | str:
    root = root or repo_root()
    if (root / "gradlew.bat").exists():
        return ["cmd", "/c", ".\\gradlew.bat", "--no-daemon", "run"]
    if (root / "gradlew").exists():
        return ["./gradlew", "--no-daemon", "run"]
    return ""


def classify_launch_mode(command: list[str] | str | None, *, command_source: str | None = None) -> dict[str, Any]:
    text = command_text(command).strip()
    lowered = text.lower()
    source = str(command_source or "").lower()
    warnings: list[str] = []
    if not text:
        mode = MODE_EXTERNAL_EXISTING_CLIENT if "existing" in source else MODE_UNKNOWN
        reason = "no launch command configured"
    elif "gradlew" in lowered or "gradle.bat" in lowered or ("gradle" in lowered and " run" in lowered):
        mode = MODE_DEV_GRADLE_RUN
        reason = "command uses the Gradle/dev RuneLite run path"
        warnings.append(DEV_GRADLE_WARNING)
    elif "jagex" in lowered or "launcher" in lowered:
        mode = MODE_LAUNCHER_AUTHENTICATED
        reason = "command appears to use an external authenticated launcher"
    elif "runelite" in lowered and "gradle" not in lowered:
        mode = MODE_LAUNCHER_AUTHENTICATED
        reason = "command appears to launch a configured RuneLite client outside Gradle"
    else:
        mode = MODE_UNKNOWN
        reason = "launch command type could not be classified"
    return {
        "schema": LAUNCH_MODE_SCHEMA,
        "launchMode": mode,
        "reason": reason,
        "command": text,
        "commandSource": command_source,
        "warnings": warnings,
        "authenticatedLaunchLikely": mode == MODE_LAUNCHER_AUTHENTICATED,
    }


def _read_config(path: Path) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def resolve_start_game_command(
    *,
    root: Path | None = None,
    config_path: Path | None = None,
    configured_command: str | list[str] | None = None,
    prefer_authenticated: bool = False,
) -> dict[str, Any]:
    root = root or repo_root()
    source = "none"
    raw: str | list[str] | None = configured_command
    if command_text(raw).strip():
        source = "explicit"
    else:
        path = config_path or user_config_path()
        config = _read_config(path)
        if prefer_authenticated:
            raw = config.get("authenticated_game_start_command")
            if command_text(raw).strip():
                source = f"authenticated_ui_config:{path}"
        if not command_text(raw).strip():
            raw = config.get("game_launch_command")
            if command_text(raw).strip():
                source = f"ui_config:{path}"
        if command_text(raw).strip():
            pass
        else:
            raw = discover_game_launch_command(root)
            if command_text(raw).strip():
                source = "discovered_gradle_wrapper"
    text = command_text(raw).strip()
    mode = classify_launch_mode(text, command_source=source)
    if not text:
        return {
            "schema": SCHEMA,
            "status": "FAIL",
            "reason": "relaunch_command_missing",
            "command": "",
            "commandSource": source,
            "cwd": str(root),
            "shell": True,
            "launchMode": mode["launchMode"],
            "launchModeReason": mode["reason"],
            "launchModeWarnings": mode["warnings"],
            "authenticatedLaunchLikely": False,
        }
    return {
        "schema": SCHEMA,
        "status": "PASS",
        "reason": "start_game_command_resolved",
        "command": text,
        "commandSource": source,
        "cwd": str(root),
        "shell": True,
        "launchMode": mode["launchMode"],
        "launchModeReason": mode["reason"],
        "launchModeWarnings": mode["warnings"],
        "authenticatedLaunchLikely": mode["authenticatedLaunchLikely"],
    }


def launch_start_game(command_info: dict[str, Any] | None = None, *, execute: bool = True) -> dict[str, Any]:
    resolved = command_info if isinstance(command_info, dict) else resolve_start_game_command()
    command = str(resolved.get("command") or "").strip()
    result: dict[str, Any] = {
        "schema": LAUNCH_SCHEMA,
        "status": "FAIL",
        "relaunchSucceeded": False,
        "relaunchAttempted": False,
        "command": command,
        "commandSource": resolved.get("commandSource"),
        "launchMode": resolved.get("launchMode") or MODE_UNKNOWN,
        "launchModeReason": resolved.get("launchModeReason"),
        "launchModeWarnings": list(resolved.get("launchModeWarnings") or []),
        "cwd": resolved.get("cwd") or str(repo_root()),
        "launchedProcessPid": None,
        "warnings": list(resolved.get("launchModeWarnings") or []),
    }
    if resolved.get("status") != "PASS" or not command:
        result["reason"] = "relaunch_command_missing"
        return result
    if not execute:
        result.update({"status": "PASS", "reason": "dry_run", "relaunchAttempted": False})
        return result
    try:
        process = subprocess.Popen(
            command,
            cwd=str(resolved.get("cwd") or repo_root()),
            shell=bool(resolved.get("shell", True)),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as error:  # noqa: BLE001
        result["reason"] = "launch_failed"
        result["error"] = f"{type(error).__name__}: {error}"
        return result
    result.update(
        {
            "status": "PASS",
            "reason": "launched",
            "relaunchSucceeded": True,
            "relaunchAttempted": True,
            "launchedProcessPid": process.pid,
        }
    )
    return result

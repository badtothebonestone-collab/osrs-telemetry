from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any


SCHEMA = "start_game_command_resolution.v1"
LAUNCH_SCHEMA = "start_game_launch_result.v1"
LAUNCH_MODE_SCHEMA = "start_game_launch_mode.v1"
INVENTORY_SCHEMA = "start_game_command_inventory.v1"

MODE_DEV_GRADLE_RUN = "dev_gradle_run"
MODE_JAGEX_LAUNCHER_RUNELITE = "jagex_launcher_runelite"
MODE_JAGEX_LAUNCHER_RUNELITE_QUICK_LAUNCH = "jagex_launcher_runelite_quick_launch"
MODE_AUTHENTICATED_LAUNCHER_PATH = "authenticated_launcher_path"
MODE_AUTHENTICATED_EXISTING_CLIENT = "authenticated_existing_client"
MODE_UNKNOWN = "unknown"

# Backwards-compatible aliases used by older tests/docs.
MODE_LAUNCHER_AUTHENTICATED = MODE_AUTHENTICATED_LAUNCHER_PATH
MODE_EXTERNAL_EXISTING_CLIENT = MODE_AUTHENTICATED_EXISTING_CLIENT

AUTHENTICATED_LIVE_MODES = {
    MODE_JAGEX_LAUNCHER_RUNELITE,
    MODE_JAGEX_LAUNCHER_RUNELITE_QUICK_LAUNCH,
    MODE_AUTHENTICATED_LAUNCHER_PATH,
    MODE_AUTHENTICATED_EXISTING_CLIENT,
}

DEV_GRADLE_WARNING = (
    "Not suitable as authenticated live bot login path unless it reaches loadedSceneVerified."
)
LIVE_START_MISSING_WARNING = (
    "Live bot recovery cannot authenticate with dev_gradle_run. Configure Jagex Launcher RuneLite launch "
    "or start an already-loaded client."
)
JAGEX_QUICK_LAUNCH_ARG = "--launch=osrs_runelite"


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


def _launch_warning_for_mode(mode: str, text: str, source: str) -> list[str]:
    warnings: list[str] = []
    if mode == MODE_DEV_GRADLE_RUN:
        warnings.append(DEV_GRADLE_WARNING)
    elif mode == MODE_AUTHENTICATED_LAUNCHER_PATH and "runelite" in text.lower() and "jagex" not in text.lower():
        warnings.append("Configured RuneLite command is treated as authenticated only because it is configured as a live/authenticated path.")
    if mode == MODE_UNKNOWN and "live" in source.lower():
        warnings.append("Live Start Command is configured but could not be classified as an authenticated launcher path.")
    return warnings


def classify_launch_mode(command: list[str] | str | None, *, command_source: str | None = None) -> dict[str, Any]:
    text = command_text(command).strip()
    lowered = text.lower()
    source = str(command_source or "")
    source_lower = source.lower()
    if not text:
        mode = MODE_AUTHENTICATED_EXISTING_CLIENT if "existing" in source_lower else MODE_UNKNOWN
        reason = "no launch command configured"
    elif "gradlew" in lowered or "gradle.bat" in lowered or ("gradle" in lowered and " run" in lowered):
        mode = MODE_DEV_GRADLE_RUN
        reason = "command uses the Gradle/dev RuneLite run path"
    elif "jagexlauncher" in lowered or "jagex launcher" in lowered or ("jagex" in lowered and "launcher" in lowered):
        if JAGEX_QUICK_LAUNCH_ARG in lowered:
            mode = MODE_JAGEX_LAUNCHER_RUNELITE_QUICK_LAUNCH
            reason = "command uses Jagex Launcher RuneLite quick launch"
        else:
            mode = MODE_JAGEX_LAUNCHER_RUNELITE
            reason = "command uses Jagex Launcher for RuneLite"
    elif "runelite" in lowered and "gradle" not in lowered:
        mode = MODE_AUTHENTICATED_LAUNCHER_PATH
        reason = "command appears to launch a configured RuneLite client outside Gradle"
    elif "existing" in source_lower:
        mode = MODE_AUTHENTICATED_EXISTING_CLIENT
        reason = "existing loaded client/session is the live start path"
    else:
        mode = MODE_UNKNOWN
        reason = "launch command type could not be classified"
    warnings = _launch_warning_for_mode(mode, text, source)
    return {
        "schema": LAUNCH_MODE_SCHEMA,
        "launchMode": mode,
        "reason": reason,
        "command": text,
        "commandSource": command_source,
        "warnings": warnings,
        "authenticatedLaunchLikely": mode in AUTHENTICATED_LIVE_MODES,
    }


def _read_config(path: Path) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _write_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _first_config_command(config: dict[str, Any], keys: list[str]) -> tuple[str, str | None]:
    for key in keys:
        text = command_text(config.get(key)).strip()
        if text:
            return text, key
    return "", None


def discover_game_launch_command(root: Path | None = None) -> list[str] | str:
    return discover_dev_start_command(root)


def discover_dev_start_command(root: Path | None = None) -> list[str] | str:
    root = root or repo_root()
    if (root / "gradlew.bat").exists():
        return ["cmd", "/c", ".\\gradlew.bat", "--no-daemon", "run"]
    if (root / "gradlew").exists():
        return ["./gradlew", "--no-daemon", "run"]
    return ""


def _known_jagex_launcher_paths(env: dict[str, str] | None = None) -> list[Path]:
    env = env or os.environ
    candidates = [
        Path(env.get("LOCALAPPDATA", "")) / "Jagex Launcher" / "JagexLauncher.exe",
        Path(env.get("LOCALAPPDATA", "")) / "Programs" / "Jagex Launcher" / "JagexLauncher.exe",
        Path(env.get("ProgramFiles", "")) / "Jagex Launcher" / "JagexLauncher.exe",
        Path(env.get("ProgramFiles(x86)", "")) / "Jagex Launcher" / "JagexLauncher.exe",
    ]
    return [path for path in candidates if str(path) and path.exists()]


def _known_runelite_paths(env: dict[str, str] | None = None) -> list[Path]:
    env = env or os.environ
    candidates = [
        Path(env.get("LOCALAPPDATA", "")) / "RuneLite" / "RuneLite.exe",
        Path(env.get("ProgramFiles", "")) / "RuneLite" / "RuneLite.exe",
        Path(env.get("ProgramFiles(x86)", "")) / "RuneLite" / "RuneLite.exe",
    ]
    return [path for path in candidates if str(path) and path.exists()]


def _shortcut_roots(env: dict[str, str] | None = None) -> list[Path]:
    env = env or os.environ
    roots = [
        Path(env.get("USERPROFILE", "")) / "Desktop",
        Path(env.get("PUBLIC", "")) / "Desktop",
        Path(env.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(env.get("ProgramData", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]
    return [root for root in roots if str(root) and root.exists()]


def _resolve_shortcut(path: Path) -> dict[str, Any]:
    if os.name != "nt":
        return {}
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($args[0]);"
        "[pscustomobject]@{Target=$s.TargetPath;Arguments=$s.Arguments;WorkingDirectory=$s.WorkingDirectory}|ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script, str(path)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception as error:  # noqa: BLE001
        return {"shortcut": str(path), "error": f"{type(error).__name__}: {error}"}
    if completed.returncode != 0:
        return {"shortcut": str(path), "error": completed.stderr.strip()}
    try:
        decoded = json.loads(completed.stdout.strip() or "{}")
    except json.JSONDecodeError:
        decoded = {}
    return decoded if isinstance(decoded, dict) else {}


def _quick_launch_command(path: Path) -> str:
    return command_text([str(path), JAGEX_QUICK_LAUNCH_ARG])


def _candidate_from_command(
    command: str,
    *,
    command_source: str,
    path: Path | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    mode = classify_launch_mode(command, command_source=command_source)
    authenticated = bool(mode["authenticatedLaunchLikely"]) and not command_source.startswith("discovered_runelite_standalone")
    return {
        "schema": "start_game_command_candidate.v1",
        "status": "PASS" if authenticated else "WARN",
        "command": command,
        "commandSource": command_source,
        "path": str(path) if path else None,
        "launchMode": mode["launchMode"],
        "launchModeReason": mode["reason"],
        "launchModeWarnings": mode["warnings"],
        "authenticatedLaunchLikely": authenticated,
        "note": note,
    }


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for candidate in candidates:
        key = str(candidate.get("command") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def discover_live_start_candidates(
    *,
    include_shortcuts: bool = False,
    include_runelite_standalone: bool = True,
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in _known_jagex_launcher_paths(env):
        candidates.append(
            _candidate_from_command(
                _quick_launch_command(path),
                command_source=f"discovered_jagex_launcher:{path}",
                path=path,
                note="Jagex Launcher RuneLite quick launch",
            )
        )
    if include_shortcuts:
        for root in _shortcut_roots(env):
            for shortcut in root.rglob("*.lnk"):
                if not any(token in shortcut.name.lower() for token in ("jagex", "runelite", "old school", "osrs")):
                    continue
                resolved = _resolve_shortcut(shortcut)
                target = str(resolved.get("Target") or resolved.get("target") or "").strip()
                args = str(resolved.get("Arguments") or resolved.get("arguments") or "").strip()
                if not target:
                    continue
                target_lower = target.lower()
                if "jagex" in target_lower and "launcher" in target_lower:
                    command = command_text([target, JAGEX_QUICK_LAUNCH_ARG]) if JAGEX_QUICK_LAUNCH_ARG not in args.lower() else f'{command_text([target])} {args}'.strip()
                    candidates.append(
                        _candidate_from_command(
                            command,
                            command_source=f"shortcut:{shortcut}",
                            path=Path(target),
                            note="Jagex Launcher shortcut target",
                        )
                    )
    if include_runelite_standalone:
        for path in _known_runelite_paths(env):
            candidates.append(
                _candidate_from_command(
                    command_text([str(path)]),
                    command_source=f"discovered_runelite_standalone:{path}",
                    path=path,
                    note="Standalone RuneLite path; prefer Jagex Launcher quick launch for Jagex-account authentication",
                )
            )
    return _dedupe_candidates(candidates)


def discover_existing_runelite_clients() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -match '^(java|javaw|runelite|JagexLauncher)\\.exe$' -or "
        "($_.Name -notmatch 'powershell|cmd' -and $_.CommandLine -match 'RuneLite|Jagex') } | "
        "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        decoded = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    rows = decoded if isinstance(decoded, list) else [decoded]
    clients: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        command_line = str(row.get("CommandLine") or "")
        name = str(row.get("Name") or "")
        if name.lower() in {"powershell.exe", "pwsh.exe", "cmd.exe"}:
            continue
        if not command_line and name.lower() not in {"java.exe", "runelite.exe", "jagexlauncher.exe"}:
            continue
        clients.append(
            {
                "schema": "start_game_existing_client_candidate.v1",
                "processId": row.get("ProcessId"),
                "name": name,
                "commandLine": command_line,
                "launchMode": MODE_AUTHENTICATED_EXISTING_CLIENT,
                "authenticatedLaunchLikely": True,
                "note": "Process discovery only; loaded-scene proof still comes from liveness_recovery_core/context_service.",
            }
        )
    return clients


def _dev_command_from_config_or_discovery(config: dict[str, Any], root: Path) -> tuple[str, str]:
    configured, key = _first_config_command(config, ["dev_start_command", "game_launch_command"])
    if configured:
        return configured, f"ui_config:{key}"
    discovered = command_text(discover_dev_start_command(root)).strip()
    if discovered:
        return discovered, "discovered_gradle_wrapper"
    return "", "none"


def _live_command_from_config(config: dict[str, Any], config_path: Path) -> tuple[str, str]:
    configured, key = _first_config_command(config, ["live_start_command", "authenticated_game_start_command"])
    if configured and key:
        return configured, f"live_ui_config:{config_path}:{key}"
    return "", "none"


def _base_resolution(
    *,
    root: Path,
    source: str,
    dev_text: str,
    dev_source: str,
    live_text: str,
    live_source: str,
    discovered_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    dev_mode = classify_launch_mode(dev_text, command_source=dev_source)
    return {
        "schema": SCHEMA,
        "cwd": str(root),
        "shell": True,
        "devStartCommand": dev_text,
        "devStartCommandSource": dev_source,
        "devLaunchMode": dev_mode["launchMode"],
        "devLaunchModeReason": dev_mode["reason"],
        "devLaunchModeWarnings": dev_mode["warnings"],
        "liveStartCommand": live_text,
        "liveStartCommandSource": live_source,
        "authenticatedLiveStartConfigured": bool(live_text and str(live_source).startswith("live_ui_config:")),
        "discoveredCandidates": discovered_candidates,
        "commandSource": source,
    }


def _failure_resolution(
    *,
    root: Path,
    reason: str,
    source: str,
    dev_text: str,
    dev_source: str,
    live_text: str,
    live_source: str,
    discovered_candidates: list[dict[str, Any]],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    mode = classify_launch_mode(live_text, command_source=live_source if live_text else source)
    payload = _base_resolution(
        root=root,
        source=source,
        dev_text=dev_text,
        dev_source=dev_source,
        live_text=live_text,
        live_source=live_source,
        discovered_candidates=discovered_candidates,
    )
    payload.update(
        {
            "status": "FAIL",
            "reason": reason,
            "command": "",
            "launchMode": mode["launchMode"],
            "launchModeReason": mode["reason"],
            "launchModeWarnings": list(dict.fromkeys([*mode["warnings"], *(warnings or [])])),
            "authenticatedLaunchLikely": False,
            "nextRecommendation": "Configure liveStartCommand with JagexLauncher.exe --launch=osrs_runelite or attach an already-loaded client.",
        }
    )
    return payload


def _pass_resolution(
    *,
    root: Path,
    command: str,
    source: str,
    dev_text: str,
    dev_source: str,
    live_text: str,
    live_source: str,
    discovered_candidates: list[dict[str, Any]],
    reason: str = "start_game_command_resolved",
) -> dict[str, Any]:
    mode = classify_launch_mode(command, command_source=source)
    payload = _base_resolution(
        root=root,
        source=source,
        dev_text=dev_text,
        dev_source=dev_source,
        live_text=live_text or command,
        live_source=live_source if live_text else source,
        discovered_candidates=discovered_candidates,
    )
    payload.update(
        {
            "status": "PASS",
            "reason": reason,
            "command": command,
            "launchMode": mode["launchMode"],
            "launchModeReason": mode["reason"],
            "launchModeWarnings": mode["warnings"],
            "authenticatedLaunchLikely": mode["authenticatedLaunchLikely"],
        }
    )
    return payload


def resolve_start_game_command(
    *,
    root: Path | None = None,
    config_path: Path | None = None,
    configured_command: str | list[str] | None = None,
    prefer_authenticated: bool = False,
    allow_dev_fallback: bool = False,
) -> dict[str, Any]:
    root = root or repo_root()
    path = config_path or user_config_path()
    config = _read_config(path)
    dev_text, dev_source = _dev_command_from_config_or_discovery(config, root)
    live_text, live_source = _live_command_from_config(config, path)
    discovered_candidates = discover_live_start_candidates(include_shortcuts=False)

    explicit = command_text(configured_command).strip()
    if explicit:
        source = "explicit_live" if prefer_authenticated else "explicit"
        mode = classify_launch_mode(explicit, command_source=source)
        if prefer_authenticated and mode["launchMode"] == MODE_DEV_GRADLE_RUN and not allow_dev_fallback:
            return _failure_resolution(
                root=root,
                reason="authenticated_live_start_missing",
                source=source,
                dev_text=dev_text or explicit,
                dev_source=dev_source if dev_text else source,
                live_text=explicit,
                live_source=source,
                discovered_candidates=discovered_candidates,
                warnings=[LIVE_START_MISSING_WARNING, *mode["warnings"]],
            )
        if prefer_authenticated and not mode["authenticatedLaunchLikely"] and not allow_dev_fallback:
            return _failure_resolution(
                root=root,
                reason="authenticated_live_start_invalid",
                source=source,
                dev_text=dev_text,
                dev_source=dev_source,
                live_text=explicit,
                live_source=source,
                discovered_candidates=discovered_candidates,
                warnings=mode["warnings"],
            )
        return _pass_resolution(
            root=root,
            command=explicit,
            source=source,
            dev_text=dev_text,
            dev_source=dev_source,
            live_text=explicit if prefer_authenticated else live_text,
            live_source=source if prefer_authenticated else live_source,
            discovered_candidates=discovered_candidates,
        )

    if prefer_authenticated:
        if live_text:
            mode = classify_launch_mode(live_text, command_source=live_source)
            if mode["launchMode"] == MODE_DEV_GRADLE_RUN and not allow_dev_fallback:
                return _failure_resolution(
                    root=root,
                    reason="authenticated_live_start_missing",
                    source=live_source,
                    dev_text=dev_text,
                    dev_source=dev_source,
                    live_text=live_text,
                    live_source=live_source,
                    discovered_candidates=discovered_candidates,
                    warnings=[LIVE_START_MISSING_WARNING, *mode["warnings"]],
                )
            if mode["authenticatedLaunchLikely"] or allow_dev_fallback:
                return _pass_resolution(
                    root=root,
                    command=live_text,
                    source=live_source,
                    dev_text=dev_text,
                    dev_source=dev_source,
                    live_text=live_text,
                    live_source=live_source,
                    discovered_candidates=discovered_candidates,
                )
            return _failure_resolution(
                root=root,
                reason="authenticated_live_start_invalid",
                source=live_source,
                dev_text=dev_text,
                dev_source=dev_source,
                live_text=live_text,
                live_source=live_source,
                discovered_candidates=discovered_candidates,
                warnings=mode["warnings"],
            )
        for candidate in discovered_candidates:
            if candidate.get("authenticatedLaunchLikely") and candidate.get("launchMode") in {
                MODE_JAGEX_LAUNCHER_RUNELITE,
                MODE_JAGEX_LAUNCHER_RUNELITE_QUICK_LAUNCH,
            }:
                command = str(candidate.get("command") or "").strip()
                source = str(candidate.get("commandSource") or "discovered_live_start")
                return _pass_resolution(
                    root=root,
                    command=command,
                    source=source,
                    dev_text=dev_text,
                    dev_source=dev_source,
                    live_text=command,
                    live_source=source,
                    discovered_candidates=discovered_candidates,
                    reason="authenticated_live_start_discovered",
                )
        return _failure_resolution(
            root=root,
            reason="authenticated_live_start_missing",
            source="live_start_missing",
            dev_text=dev_text,
            dev_source=dev_source,
            live_text="",
            live_source="none",
            discovered_candidates=discovered_candidates,
            warnings=[LIVE_START_MISSING_WARNING],
        )

    raw = dev_text or live_text
    source = dev_source if dev_text else live_source
    if not raw:
        return _failure_resolution(
            root=root,
            reason="relaunch_command_missing",
            source=source,
            dev_text=dev_text,
            dev_source=dev_source,
            live_text=live_text,
            live_source=live_source,
            discovered_candidates=discovered_candidates,
        )
    return _pass_resolution(
        root=root,
        command=raw,
        source=source,
        dev_text=dev_text,
        dev_source=dev_source,
        live_text=live_text,
        live_source=live_source,
        discovered_candidates=discovered_candidates,
    )


def launch_start_game(command_info: dict[str, Any] | None = None, *, execute: bool = True) -> dict[str, Any]:
    resolved = command_info if isinstance(command_info, dict) else resolve_start_game_command()
    command = str(resolved.get("command") or "").strip()
    result: dict[str, Any] = {
        "schema": LAUNCH_SCHEMA,
        "status": "FAIL",
        "relaunchSucceeded": False,
        "relaunchAttempted": False,
        "attachExistingLoadedClient": False,
        "command": command,
        "commandSource": resolved.get("commandSource"),
        "launchMode": resolved.get("launchMode") or MODE_UNKNOWN,
        "launchModeReason": resolved.get("launchModeReason"),
        "launchModeWarnings": list(resolved.get("launchModeWarnings") or []),
        "cwd": resolved.get("cwd") or str(repo_root()),
        "launchedProcessPid": None,
        "warnings": list(resolved.get("launchModeWarnings") or []),
    }
    if resolved.get("launchMode") == MODE_AUTHENTICATED_EXISTING_CLIENT:
        result.update(
            {
                "status": "PASS",
                "reason": "attach_existing_loaded_client",
                "relaunchAttempted": False,
                "relaunchSucceeded": True,
                "attachExistingLoadedClient": True,
            }
        )
        return result
    if resolved.get("status") != "PASS" or not command:
        result["reason"] = resolved.get("reason") or "relaunch_command_missing"
        result["nextRecommendation"] = resolved.get("nextRecommendation")
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


def set_live_start_command(command: str, *, config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or user_config_path()
    config = _read_config(path)
    text = command_text(command).strip()
    config["live_start_command"] = text
    config["authenticated_game_start_command"] = text
    _write_config(path, config)
    resolution = resolve_start_game_command(config_path=path, prefer_authenticated=True)
    return {
        "schema": "start_game_live_command_config.v1",
        "status": "PASS" if text else "FAIL",
        "configPath": str(path),
        "liveStartCommand": text,
        "resolution": resolution,
    }


def command_inventory(*, root: Path | None = None, config_path: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    path = config_path or user_config_path()
    config = _read_config(path)
    dev_text, dev_source = _dev_command_from_config_or_discovery(config, root)
    live_text, live_source = _live_command_from_config(config, path)
    resolution = resolve_start_game_command(root=root, config_path=path, prefer_authenticated=True)
    runelite_standalone_candidates = _dedupe_candidates(
        [
            _candidate_from_command(
                command_text([str(runelite_path)]),
                command_source=f"discovered_runelite_standalone:{runelite_path}",
                path=runelite_path,
                note="Standalone RuneLite path; prefer Jagex Launcher quick launch for Jagex-account authentication",
            )
            for runelite_path in _known_runelite_paths()
        ]
    )
    return {
        "schema": INVENTORY_SCHEMA,
        "repoRoot": str(root),
        "configPath": str(path),
        "devStartCommand": dev_text,
        "devStartCommandSource": dev_source,
        "devLaunchMode": classify_launch_mode(dev_text, command_source=dev_source),
        "liveStartCommand": live_text,
        "liveStartCommandSource": live_source,
        "liveResolution": resolution,
        "jagexLauncherCandidates": discover_live_start_candidates(include_shortcuts=True, include_runelite_standalone=False),
        "runeliteStandaloneCandidates": runelite_standalone_candidates,
        "existingClientCandidates": discover_existing_runelite_clients(),
    }


def validate_live_start(*, root: Path | None = None, config_path: Path | None = None) -> dict[str, Any]:
    resolution = resolve_start_game_command(root=root, config_path=config_path, prefer_authenticated=True)
    status = "PASS" if resolution.get("status") == "PASS" and resolution.get("authenticatedLaunchLikely") else "FAIL"
    return {
        "schema": "start_game_live_validation.v1",
        "status": status,
        "resolution": resolution,
        "blocker": None if status == "PASS" else resolution.get("reason") or "authenticated_live_start_missing",
        "warnings": resolution.get("launchModeWarnings") or [],
        "nextRecommendation": resolution.get("nextRecommendation"),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve and validate OSRS Start Game commands.")
    parser.add_argument("--list", action="store_true", help="List dev/live start command inventory.")
    parser.add_argument("--validate-live", action="store_true", help="Validate the authenticated live Start Game command.")
    parser.add_argument("--print-live-command", action="store_true", help="Print the resolved live Start Game command.")
    parser.add_argument("--set-live-command", help="Store a live Start Game command in the UI config.")
    parser.add_argument("--config-path", type=Path, help="Override telemetry UI config path.")
    parser.add_argument("--root", type=Path, help="Override repo root.")
    parser.add_argument("--json", action="store_true", help="Print JSON output. This is the default for --list/--validate-live.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root or repo_root()
    if args.set_live_command is not None:
        payload = set_live_start_command(args.set_live_command, config_path=args.config_path)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload.get("status") == "PASS" else 1
    if args.list:
        print(json.dumps(command_inventory(root=root, config_path=args.config_path), indent=2, sort_keys=True))
        return 0
    if args.validate_live:
        payload = validate_live_start(root=root, config_path=args.config_path)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload.get("status") == "PASS" else 1
    if args.print_live_command:
        payload = resolve_start_game_command(root=root, config_path=args.config_path, prefer_authenticated=True)
        command = str(payload.get("command") or "").strip()
        if payload.get("status") == "PASS" and command:
            print(command)
            return 0
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1
    payload = resolve_start_game_command(root=root, config_path=args.config_path, prefer_authenticated=True)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

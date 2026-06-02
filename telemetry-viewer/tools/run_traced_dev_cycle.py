from __future__ import annotations

import argparse
import collections
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA = "traced_dev_cycle_orchestrator.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
VIEWER_DIR = PROJECT_ROOT / "telemetry-viewer"
DEFAULT_CONFIG_PATH = VIEWER_DIR / "config" / "dev_cycle.local.json"
DEFAULT_TRACE_PATH = PROJECT_ROOT / "interaction_geometry" / "live" / "navigation_decisions.jsonl"
DEFAULT_PROFILE = "woodcutting"

if str(VIEWER_DIR) not in sys.path:
    sys.path.insert(0, str(VIEWER_DIR))


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class DevCycleConfig:
    daemon_url: str = "http://127.0.0.1:8890"
    snapshot_url: str = "http://127.0.0.1:8893"
    backend: str = "arduino"
    arduino_port: str | None = None
    runelite_launch_command: str | list[str] | None = None
    daemon_launch_command: list[str] | None = None
    launch_runelite_if_missing: bool = True
    start_daemon_if_missing: bool = True
    trace_output_path: str = "interaction_geometry/live/navigation_decisions.jsonl"
    profile: str = DEFAULT_PROFILE
    max_actions: int = 1
    max_runtime_seconds: float = 30.0
    wait_for_ready_seconds: float = 10.0
    action_timeout_ms: int = 5000
    result_timeout_ms: int = 15000
    nav_verify_game_ticks: int = 3
    nav_progress_min_distance: float = 1.0
    input_profile: str = "steady"
    movement_profile: str = "fitts_guided"
    pacing_profile: str = "steady"
    window_title_filter: str = "RuneLite"
    startup_wait_seconds: float = 60.0
    max_wait_seconds: float = 300.0
    watch_poll_seconds: float = 5.0
    auto_login_enabled: bool | None = None
    auto_login_command: str | list[str] | None = None
    auto_login_working_directory: str | None = None
    auto_login_timeout_seconds: float = 120.0
    auto_login_max_attempts: int = 1

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "DevCycleConfig":
        config = cls()
        for field in cls.__dataclass_fields__:  # type: ignore[attr-defined]
            if field in payload:
                setattr(config, field, payload[field])
        return config


@dataclass
class RuntimeDeps:
    run_command: Callable[..., CommandResult]
    launch_process: Callable[..., dict[str, Any]]
    fetch_json: Callable[..., dict[str, Any]]
    detect_window: Callable[[str], dict[str, Any]]
    detect_processes: Callable[[], list[dict[str, Any]]]
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch/check/run one bounded navigation-traced dev cycle.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Check launch/readiness and summarize trace state without sending live input.")
    mode.add_argument("--run", action="store_true", help="Run one bounded traced cycle only when readiness says live execution is safe.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--daemon-url")
    parser.add_argument("--snapshot-url")
    parser.add_argument("--backend", choices=["arduino"])
    parser.add_argument("--arduino-port")
    parser.add_argument("--trace-output-path")
    parser.add_argument("--runelite-launch-command")
    parser.add_argument("--no-launch", action="store_true", help="Do not launch RuneLite if no dev window/process is detected.")
    parser.add_argument("--no-start-daemon", action="store_true", help="Do not start the live daemon if the daemon URL is unavailable.")
    parser.add_argument("--watch", action="store_true", help="Keep polling readiness until safe or until --max-wait-seconds expires.")
    parser.add_argument("--max-wait-seconds", type=float, help="Maximum seconds to watch readiness before reporting a timeout blocker.")
    parser.add_argument("--poll-seconds", type=float, help="Readiness polling interval used by --watch.")
    auto_login = parser.add_mutually_exclusive_group()
    auto_login.add_argument("--use-auto-login", action="store_true", help="Use the existing loaded-scene/login recovery command while watching readiness.")
    auto_login.add_argument("--no-auto-login", action="store_true", help="Disable auto-login/readiness recovery while watching readiness.")
    parser.add_argument("--auto-login-command", help="Existing local recovery command to invoke. Do not include secrets.")
    parser.add_argument("--auto-login-timeout-seconds", type=float, help="Timeout for each auto-login/recovery attempt.")
    parser.add_argument("--auto-login-max-attempts", type=int, help="Maximum auto-login/recovery attempts during one watch run.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.run:
        args.dry_run = True
    return args


def load_config(path: str | Path | None) -> tuple[DevCycleConfig, dict[str, Any]]:
    raw: dict[str, Any] = {}
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if config_path.exists():
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"config must be a JSON object: {config_path}")
    config = DevCycleConfig.from_mapping(raw)
    config.arduino_port = config.arduino_port or os.environ.get("OSRS_TELEMETRY_ARDUINO_PORT")
    if config.runelite_launch_command is None:
        config.runelite_launch_command = default_runelite_launch_command()
    if config.daemon_launch_command is None:
        config.daemon_launch_command = default_daemon_launch_command(config)
    if config.arduino_port is None:
        config.arduino_port = discover_arduino_port()
    return config, {"path": str(config_path), "exists": config_path.exists(), "loadedKeys": sorted(raw.keys())}


def apply_cli_overrides(config: DevCycleConfig, args: argparse.Namespace) -> DevCycleConfig:
    if args.daemon_url:
        config.daemon_url = args.daemon_url
    if args.snapshot_url:
        config.snapshot_url = args.snapshot_url
    if args.backend:
        config.backend = args.backend
    if args.arduino_port:
        config.arduino_port = args.arduino_port
    if args.trace_output_path:
        config.trace_output_path = args.trace_output_path
    if args.runelite_launch_command:
        config.runelite_launch_command = args.runelite_launch_command
    if args.no_launch:
        config.launch_runelite_if_missing = False
    if args.no_start_daemon:
        config.start_daemon_if_missing = False
    if args.max_wait_seconds is not None:
        config.max_wait_seconds = args.max_wait_seconds
    if args.poll_seconds is not None:
        config.watch_poll_seconds = args.poll_seconds
    if args.use_auto_login:
        config.auto_login_enabled = True
    if args.no_auto_login:
        config.auto_login_enabled = False
    if args.auto_login_command:
        config.auto_login_command = args.auto_login_command
    if args.auto_login_timeout_seconds is not None:
        config.auto_login_timeout_seconds = args.auto_login_timeout_seconds
    if args.auto_login_max_attempts is not None:
        config.auto_login_max_attempts = args.auto_login_max_attempts
    return config


def default_runelite_launch_command() -> str | None:
    gradlew = PROJECT_ROOT / "gradlew.bat"
    if gradlew.exists():
        return ".\\gradlew.bat run"
    return None


def default_daemon_launch_command(config: DevCycleConfig) -> list[str]:
    return [
        sys.executable,
        str(VIEWER_DIR / "live_core_daemon.py"),
        "--latest-session",
        "--profile",
        config.profile,
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
        context_port(config.daemon_url),
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


def context_port(daemon_url: str) -> str:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(daemon_url)
        return str(parsed.port or 8890)
    except Exception:  # noqa: BLE001
        return "8890"


def discover_arduino_port() -> str | None:
    candidates = [
        PROJECT_ROOT / "interaction_geometry" / "live" / "arduino_backend_status.json",
        PROJECT_ROOT / "interaction_geometry" / "live" / "input_integrity_status.json",
    ]
    live_dir = PROJECT_ROOT / "interaction_geometry" / "live"
    if live_dir.exists():
        candidates.extend(sorted(live_dir.glob("arduino_pointer_calibration_*.json"), reverse=True))
    for path in candidates:
        port = arduino_port_from_json(path)
        if port:
            return port
    return None


def arduino_port_from_json(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    stack: list[Any] = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if str(key).lower() in {"arduinoport", "arduino_port", "port"} and isinstance(value, str) and value.upper().startswith("COM"):
                    return value
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return None


def default_deps() -> RuntimeDeps:
    return RuntimeDeps(
        run_command=run_command,
        launch_process=launch_process,
        fetch_json=fetch_json,
        detect_window=detect_runelite_window,
        detect_processes=detect_runelite_processes,
    )


def run_command(command: list[str], *, timeout: float = 30.0, cwd: Path = PROJECT_ROOT) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return CommandResult(command=list(command), returncode=completed.returncode, stdout=completed.stdout or "", stderr=completed.stderr or "")
    except subprocess.TimeoutExpired as error:
        return CommandResult(command=list(command), returncode=124, stdout=error.stdout or "", stderr=f"timeout after {timeout}s")
    except Exception as error:  # noqa: BLE001
        return CommandResult(command=list(command), returncode=1, stderr=f"{type(error).__name__}: {error}")


def launch_process(command: str | list[str] | None, *, cwd: Path = PROJECT_ROOT) -> dict[str, Any]:
    if not command:
        return {"started": False, "reason": "launch_command_missing", "pid": None}
    try:
        if isinstance(command, list):
            process = subprocess.Popen(command, cwd=str(cwd), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            shown = " ".join(command)
        else:
            process = subprocess.Popen(command, cwd=str(cwd), shell=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            shown = command
        return {"started": True, "reason": "started", "pid": process.pid, "command": shown}
    except Exception as error:  # noqa: BLE001
        return {"started": False, "reason": f"{type(error).__name__}: {error}", "pid": None, "command": command}


def fetch_json(url: str, *, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8", errors="replace"))
    return decoded if isinstance(decoded, dict) else {}


def detect_runelite_window(window_title_filter: str) -> dict[str, Any]:
    try:
        import run_runelite_bootstrap as bootstrap

        return bootstrap.find_window(bootstrap.title_filters(window_title_filter))
    except Exception as error:  # noqa: BLE001
        return {"matchedWindowTitle": None, "warnings": [f"window detection unavailable: {type(error).__name__}: {error}"]}


def detect_runelite_processes() -> list[dict[str, Any]]:
    script = r"""
$items = Get-CimInstance Win32_Process | Where-Object {
  ($_.CommandLine -like '*com.osrstelemetry.TelemetryPluginTest*') -or
  ($_.CommandLine -like '*GradleWrapperMain run*') -or
  ($_.CommandLine -like '*gradle-wrapper.jar*run*') -or
  ($_.CommandLine -like '*RuneLite*')
} | Select-Object ProcessId,ProcessName,CommandLine
$items | ConvertTo-Json -Depth 3
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0 or not (completed.stdout or "").strip():
        return []
    try:
        decoded = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(decoded, dict):
        return [decoded]
    return [item for item in decoded if isinstance(item, dict)] if isinstance(decoded, list) else []


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


def snapshot_health_url(snapshot_url: str) -> str:
    base = snapshot_url.rstrip("/")
    if base.endswith("/snapshot"):
        base = base[: -len("/snapshot")]
    return base + "/health"


def parse_json_output(result: CommandResult) -> dict[str, Any]:
    text = (result.stdout or "").strip()
    if not text:
        text = (result.stderr or "").strip()
    if not text:
        return {}
    try:
        decoded = json.loads(text)
        return decoded if isinstance(decoded, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                decoded = json.loads(text[start : end + 1])
                return decoded if isinstance(decoded, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def run_readiness(config: DevCycleConfig, deps: RuntimeDeps) -> tuple[dict[str, Any], CommandResult]:
    command = [
        sys.executable,
        str(VIEWER_DIR / "diagnose_live_readiness.py"),
        "--daemon-url",
        config.daemon_url,
        "--profile",
        config.profile,
        "--json",
    ]
    result = deps.run_command(command, timeout=20.0, cwd=PROJECT_ROOT)
    return parse_json_output(result), result


def run_pipeline_health(deps: RuntimeDeps) -> tuple[dict[str, Any], CommandResult]:
    command = [sys.executable, str(VIEWER_DIR / "context_service.py"), "--pipeline-health"]
    result = deps.run_command(command, timeout=20.0, cwd=PROJECT_ROOT)
    return parse_json_output(result), result


def run_ensure_loaded_scene(config: DevCycleConfig, deps: RuntimeDeps) -> tuple[dict[str, Any], CommandResult]:
    command = [
        sys.executable,
        str(VIEWER_DIR / "context_service.py"),
        "--ensure-loaded-scene",
        "--daemon-url",
        config.daemon_url,
    ]
    if config.arduino_port:
        command.extend(["--arduino-port", config.arduino_port])
    result = deps.run_command(command, timeout=max(30.0, config.startup_wait_seconds), cwd=PROJECT_ROOT)
    return parse_json_output(result), result


SENSITIVE_COMMAND_KEYS = {
    "--password",
    "--passwd",
    "--secret",
    "--token",
    "--auth-token",
    "--credential",
    "--credentials",
    "--api-key",
}
SENSITIVE_TEXT_MARKERS = ("password", "passwd", "secret", "token", "credential", "api_key", "api-key")


def format_command_template(value: str, config: DevCycleConfig) -> str:
    return value.format(
        python=sys.executable,
        viewer_dir=str(VIEWER_DIR),
        project_root=str(PROJECT_ROOT),
        daemon_url=config.daemon_url,
        snapshot_url=config.snapshot_url,
        arduino_port=config.arduino_port or "",
        timeout_seconds=max(1.0, float(config.auto_login_timeout_seconds)),
    )


def split_config_command(value: str | list[str] | None, config: DevCycleConfig) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [format_command_template(str(item), config) for item in value if str(item).strip()]
    text = format_command_template(str(value).strip(), config)
    if not text:
        return []
    return shlex.split(text, posix=os.name != "nt")


def default_auto_login_command(config: DevCycleConfig) -> list[str] | None:
    script = VIEWER_DIR / "context_service.py"
    if not script.exists():
        return None
    command = [
        sys.executable,
        str(script),
        "--ensure-loaded-scene",
        "--daemon-url",
        config.daemon_url,
        "--snapshot-url",
        config.snapshot_url,
        "--liveness-max-total-seconds",
        str(max(1.0, float(config.auto_login_timeout_seconds))),
        "--liveness-max-attempts-per-state",
        "2",
    ]
    if config.arduino_port:
        command.extend(["--arduino-port", config.arduino_port])
    return command


def discover_auto_login_command(config: DevCycleConfig) -> tuple[list[str] | None, str, str | None]:
    if config.auto_login_command is not None:
        command = split_config_command(config.auto_login_command, config)
        if not command:
            return None, "configured", "auto_login_command is configured but empty"
        return command, "configured", None
    command = default_auto_login_command(config)
    if command:
        return command, "discovered_context_service_ensure_loaded_scene", None
    return None, "missing", f"{VIEWER_DIR / 'context_service.py'} was not found"


def auto_login_enabled_for_mode(config: DevCycleConfig, args: argparse.Namespace) -> bool:
    if config.auto_login_enabled is False:
        return False
    if config.auto_login_enabled is True:
        return True
    return bool(getattr(args, "watch", False))


def redact_command(command: list[str] | None) -> list[str] | None:
    if command is None:
        return None
    redacted: list[str] = []
    redact_next = False
    for item in command:
        text = str(item)
        lower = text.lower()
        key = lower.split("=", 1)[0]
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        if key in SENSITIVE_COMMAND_KEYS:
            if "=" in text:
                redacted.append(text.split("=", 1)[0] + "=<redacted>")
            else:
                redacted.append(text)
                redact_next = True
            continue
        if any(marker in lower for marker in SENSITIVE_TEXT_MARKERS) and "=" in text:
            redacted.append(text.split("=", 1)[0] + "=<redacted>")
            continue
        redacted.append(text)
    return redacted


def redact_text(text: str) -> str:
    lines: list[str] = []
    for line in (text or "").splitlines():
        lower = line.lower()
        if any(marker in lower for marker in SENSITIVE_TEXT_MARKERS):
            lines.append("<redacted sensitive line>")
        else:
            lines.append(line)
    return "\n".join(lines)


def redacted_tail_lines(text: str, count: int) -> list[str]:
    return tail_lines(redact_text(text), count)


def auto_login_working_directory(config: DevCycleConfig) -> Path:
    if not config.auto_login_working_directory:
        return PROJECT_ROOT
    path = Path(format_command_template(str(config.auto_login_working_directory), config))
    return path if path.is_absolute() else PROJECT_ROOT / path


def readiness_needs_auto_login(readiness: dict[str, Any], blocker: dict[str, Any] | None = None) -> bool:
    if readiness.get("unknownScreen") is True:
        return False
    loaded = readiness.get("loadedSceneProof") if isinstance(readiness.get("loadedSceneProof"), dict) else {}
    game_state = str(loaded.get("gameState") or readiness.get("gameState") or "").upper()
    liveness_state = str(readiness.get("livenessState") or "").lower()
    category = str((blocker or {}).get("category") or "").lower()
    if readiness.get("manualLoginRequired") is True:
        return True
    if game_state == "LOGIN_SCREEN" or liveness_state == "login_screen":
        return True
    if category in {"manual_login_required", "login_screen"}:
        return True
    if readiness.get("livenessRecoveryRecommended") is True and not readiness.get("unknownScreen"):
        return True
    blockers = readiness.get("blockers") if isinstance(readiness.get("blockers"), list) else []
    return any("login" in str(item).lower() for item in blockers)


def auto_login_successful(payload: dict[str, Any], result: CommandResult) -> bool:
    if result.returncode != 0:
        return False
    status = str(payload.get("status") or "").lower()
    if payload.get("loadedSceneVerified") is True:
        return True
    return status in {"pass", "ok", "loaded_scene_ready", "recovered_loaded_scene"}


def run_auto_login_recovery(config: DevCycleConfig, deps: RuntimeDeps, command: list[str]) -> tuple[dict[str, Any], CommandResult]:
    timeout = max(1.0, float(config.auto_login_timeout_seconds))
    result = deps.run_command(command, timeout=timeout, cwd=auto_login_working_directory(config))
    return parse_json_output(result), result


def append_auto_login_attempt(
    payload: dict[str, Any],
    *,
    elapsed_seconds: float,
    command: list[str],
    result_payload: dict[str, Any],
    result: CommandResult,
) -> dict[str, Any]:
    attempt = {
        "elapsedSeconds": round(elapsed_seconds, 3),
        "command": redact_command(command),
        "returnCode": result.returncode,
        "status": result_payload.get("status"),
        "loadedSceneVerified": result_payload.get("loadedSceneVerified"),
        "blocker": result_payload.get("blocker"),
        "stdoutTail": redacted_tail_lines(result.stdout, 12),
        "stderrTail": redacted_tail_lines(result.stderr, 12),
    }
    auto = payload.get("autoLogin") if isinstance(payload.get("autoLogin"), dict) else {}
    attempts = auto.setdefault("attempts", [])
    if isinstance(attempts, list):
        attempts.append(attempt)
    return attempt


def build_execute_command(config: DevCycleConfig, trace_path: Path) -> list[str]:
    command = [
        sys.executable,
        str(VIEWER_DIR / "execute_next_action.py"),
        "--daemon-url",
        config.daemon_url,
        "--snapshot-url",
        config.snapshot_url,
        "--backend",
        config.backend,
        "--execute",
        "--loop",
        "--max-actions",
        str(max(1, int(config.max_actions))),
        "--max-runtime-seconds",
        str(max(1.0, float(config.max_runtime_seconds))),
        "--wait-for-ready",
        str(max(0.0, float(config.wait_for_ready_seconds))),
        "--verify-after-action",
        "--action-timeout-ms",
        str(max(1, int(config.action_timeout_ms))),
        "--result-timeout-ms",
        str(max(1, int(config.result_timeout_ms))),
        "--nav-verify-game-ticks",
        str(max(0, int(config.nav_verify_game_ticks))),
        "--nav-progress-min-distance",
        str(float(config.nav_progress_min_distance)),
        "--nav-trace",
        "--nav-trace-output",
        str(trace_path),
        "--nav-trace-console",
        "--input-profile",
        config.input_profile,
        "--movement-profile",
        config.movement_profile,
        "--pacing-profile",
        config.pacing_profile,
    ]
    if config.backend == "arduino" and config.arduino_port:
        command.extend(["--arduino-port", config.arduino_port])
    return command


def readiness_allows_execution(readiness: dict[str, Any]) -> bool:
    action_readiness = readiness.get("actionReadiness") if isinstance(readiness.get("actionReadiness"), dict) else {}
    action_execution = readiness.get("actionExecution") if isinstance(readiness.get("actionExecution"), dict) else {}
    return bool(
        readiness.get("ready") is True
        and action_readiness.get("executionAllowed") is True
        and action_execution.get("allowed") is True
    )


def readiness_blocker(readiness: dict[str, Any]) -> dict[str, Any]:
    if readiness.get("manualLoginRequired") is True:
        return {"category": "manual_login_required", "reason": "RuneLite is on a login/account surface; credentials are never automated."}
    if readiness.get("unknownScreen") is True:
        return {"category": "unknown_screen", "reason": "RuneLite screen is not a known safe liveness or loaded-scene surface."}
    blockers = readiness.get("blockers") if isinstance(readiness.get("blockers"), list) else []
    if blockers:
        first = blockers[0] if isinstance(blockers[0], dict) else {"message": str(blockers[0])}
        return {"category": str(first.get("code") or "readiness_blocker"), "reason": str(first.get("message") or first)}
    return {"category": "not_ready", "reason": str(readiness.get("status") or "readiness did not pass")}


def trace_path_from_config(config: DevCycleConfig) -> Path:
    path = Path(str(config.trace_output_path))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def read_trace_records(path: Path, *, start_line: int = 0) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        if index < start_line:
            continue
        text = line.strip()
        if not text:
            continue
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            decoded.setdefault("_line", index + 1)
            records.append(decoded)
    return records


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def summarize_trace(records: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = collections.Counter(str(record.get("decision") or "missing") for record in records)
    reasons = collections.Counter(str(record.get("reason") or "missing") for record in records)
    suspicious_index = first_suspicious_index(records)
    suspicious = None
    context_rows: list[dict[str, Any]] = []
    if suspicious_index is not None:
        suspicious = suspicious_summary(records[suspicious_index], records[suspicious_index - 1] if suspicious_index > 0 else None)
        start = max(0, suspicious_index - 5)
        end = min(len(records), suspicious_index + 6)
        context_rows = [compact_trace_row(record) for record in records[start:end]]
    return {
        "decisionCount": len(records),
        "decisionCounts": dict(sorted(decisions.items())),
        "reasonCounts": dict(sorted(reasons.items())),
        "firstSuspiciousDecision": suspicious,
        "contextRows": context_rows,
    }


def first_suspicious_index(records: list[dict[str, Any]]) -> int | None:
    for index, record in enumerate(records):
        previous = records[index - 1] if index > 0 else None
        if suspicious_summary(record, previous):
            return index
    return None


def suspicious_summary(record: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any] | None:
    reason = str(record.get("reason") or "").strip()
    decision = str(record.get("decision") or "").strip()
    observed = record.get("observed") if isinstance(record.get("observed"), dict) else {}
    distances = record.get("distances") if isinstance(record.get("distances"), dict) else {}
    subgoal = record.get("chosenSubgoal") if isinstance(record.get("chosenSubgoal"), dict) else {}

    issue: str | None = None
    if not reason:
        issue = "missing_reason_string"
    elif decision in {"click", "recover"} and subgoal.get("executable") is False:
        issue = "click_or_recover_for_non_executable_subgoal"
    elif decision in {"click", "recover"} and observed.get("nextActionAllowed") is False:
        issue = "click_or_recover_while_previous_result_pending"
    elif decision in {"click", "recover"} and distances.get("distanceImproving") is True:
        issue = "click_or_recover_while_distance_improving"
    elif decision in {"click", "recover"} and any(token in reason for token in ("stale", "daemon_latest_tick_missing", "input_geometry_unavailable")):
        issue = "stale_state_allowed_click"
    elif decision in {"click", "recover"} and "wall" in reason.lower() and not block_evidence_present(record):
        issue = "blocked_recovery_without_block_evidence"
    elif previous and repeated_short_click(record, previous):
        issue = "repeated_short_click"

    if issue is None:
        return None
    return {
        "issue": issue,
        "line": record.get("_line"),
        "decision": decision or None,
        "reason": reason or None,
        "observedResult": observed.get("observedResult"),
        "targetTile": subgoal.get("targetTile"),
    }


def block_evidence_present(record: dict[str, Any]) -> bool:
    text = json.dumps(record, sort_keys=True).lower()
    return any(token in text for token in ("blockevidence", "barrierdetected", "localreachability=blocked", "wallhuggingdetected"))


def repeated_short_click(record: dict[str, Any], previous: dict[str, Any]) -> bool:
    if str(record.get("decision") or "") != "click" or str(previous.get("decision") or "") != "click":
        return False
    current_goal = record.get("chosenSubgoal") if isinstance(record.get("chosenSubgoal"), dict) else {}
    previous_goal = previous.get("chosenSubgoal") if isinstance(previous.get("chosenSubgoal"), dict) else {}
    if current_goal.get("targetTile") != previous_goal.get("targetTile"):
        return False
    distances = record.get("distances") if isinstance(record.get("distances"), dict) else {}
    delta = distances.get("distanceDelta")
    try:
        return abs(float(delta)) < 1.0
    except Exception:  # noqa: BLE001
        return True


def compact_trace_row(record: dict[str, Any]) -> dict[str, Any]:
    observed = record.get("observed") if isinstance(record.get("observed"), dict) else {}
    distances = record.get("distances") if isinstance(record.get("distances"), dict) else {}
    subgoal = record.get("chosenSubgoal") if isinstance(record.get("chosenSubgoal"), dict) else {}
    route = record.get("routeStep") if isinstance(record.get("routeStep"), dict) else {}
    return {
        "line": record.get("_line"),
        "decision": record.get("decision"),
        "reason": record.get("reason"),
        "playerWorldPosition": record.get("playerWorldPosition"),
        "targetTile": subgoal.get("targetTile"),
        "routeNode": route.get("currentNodeId"),
        "routeStepStatus": route.get("routeStepStatus"),
        "distanceDelta": distances.get("distanceDelta"),
        "distanceImproving": distances.get("distanceImproving"),
        "observedResult": observed.get("observedResult"),
        "nextActionAllowed": observed.get("nextActionAllowed"),
    }


def run_dev_cycle(args: argparse.Namespace, *, deps: RuntimeDeps | None = None) -> dict[str, Any]:
    deps = deps or default_deps()
    mode = "run" if args.run else "dry-run"
    config, config_summary = load_config(args.config)
    config = apply_cli_overrides(config, args)
    trace_path = trace_path_from_config(config)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    auto_login_enabled = auto_login_enabled_for_mode(config, args)
    auto_login_command, auto_login_source, auto_login_missing = discover_auto_login_command(config)
    auto_login_invoke_allowed = bool(auto_login_enabled and mode == "run" and getattr(args, "watch", False))

    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "mode": mode,
        "watch": {
            "enabled": bool(getattr(args, "watch", False)),
            "maxWaitSeconds": max(0.0, float(config.max_wait_seconds)),
            "pollSeconds": max(0.1, float(config.watch_poll_seconds)),
            "events": [],
            "timedOut": False,
        },
        "projectRoot": str(PROJECT_ROOT),
        "viewerDir": str(VIEWER_DIR),
        "config": config_summary,
        "tracePath": str(trace_path),
        "status": "PASS",
        "ready": False,
        "blocker": None,
        "warnings": [],
        "autoLogin": {
            "enabled": auto_login_enabled,
            "invokeAllowed": auto_login_invoke_allowed,
            "available": bool(auto_login_command),
            "source": auto_login_source,
            "missingReason": auto_login_missing,
            "command": redact_command(auto_login_command),
            "timeoutSeconds": max(1.0, float(config.auto_login_timeout_seconds)),
            "maxAttempts": max(0, int(config.auto_login_max_attempts)),
            "attempts": [],
        },
    }

    window = deps.detect_window(config.window_title_filter)
    processes = deps.detect_processes()
    runelite_running = bool(window.get("matchedWindowTitle") or processes)
    payload["runelite"] = {
        "running": runelite_running,
        "windowTitle": window.get("matchedWindowTitle"),
        "processCount": len(processes),
        "warnings": window.get("warnings") or [],
    }

    if not runelite_running:
        if config.launch_runelite_if_missing:
            launch = deps.launch_process(config.runelite_launch_command, cwd=PROJECT_ROOT)
            payload["runelite"]["launch"] = launch
            if not launch.get("started"):
                payload["status"] = "BLOCKED"
                payload["blocker"] = {"category": "runelite_launch_failed", "reason": str(launch.get("reason") or "RuneLite launch failed")}
                return finalize_payload(payload, trace_path)
            wait_for(lambda: bool(deps.detect_window(config.window_title_filter).get("matchedWindowTitle") or deps.detect_processes()), config.startup_wait_seconds, deps.sleep)
        else:
            payload["status"] = "BLOCKED"
            payload["blocker"] = {"category": "runelite_not_running", "reason": "RuneLite dev client was not detected and launch is disabled."}
            return finalize_payload(payload, trace_path)

    services = check_services(config, deps)
    payload["services"] = services
    if not services["daemon"]["reachable"] and config.start_daemon_if_missing:
        daemon_launch = deps.launch_process(config.daemon_launch_command, cwd=PROJECT_ROOT)
        payload["services"]["daemonLaunch"] = daemon_launch
        wait_for(lambda: bool(safe_fetch(deps, daemon_status_url(config.daemon_url))), config.startup_wait_seconds, deps.sleep)
        services = check_services(config, deps)
        payload["services"].update(services)

    pipeline_health, pipeline_command = run_pipeline_health(deps)
    payload["pipelineHealth"] = {
        "status": pipeline_health.get("status") or ("FAIL" if pipeline_command.returncode else "UNKNOWN"),
        "livePacketWriterActive": pipeline_health.get("livePacketWriterActive"),
        "livePacketsRuntimeRemoved": pipeline_health.get("livePacketsRuntimeRemoved"),
        "ndjsonRuntimeRemoved": pipeline_health.get("ndjsonRuntimeRemoved"),
        "jsonlRuntimeRemoved": pipeline_health.get("jsonlRuntimeRemoved"),
    }

    readiness, readiness_command = run_readiness(config, deps)
    payload["readiness"] = compact_readiness(readiness, readiness_command)
    payload["ready"] = readiness_allows_execution(readiness)

    if bool(getattr(args, "watch", False)) and not payload["ready"]:
        readiness, readiness_command, watch_ready = watch_until_ready(
            config,
            deps,
            args,
            payload,
            readiness,
            readiness_command,
            auto_login_command=auto_login_command,
        )
        payload["readiness"] = compact_readiness(readiness, readiness_command)
        payload["ready"] = watch_ready
        if not watch_ready:
            payload["status"] = "BLOCKED"
            watch = payload.get("watch") if isinstance(payload.get("watch"), dict) else {}
            terminal = watch.get("terminalBlocker") if isinstance(watch.get("terminalBlocker"), dict) else None
            if terminal:
                payload["blocker"] = terminal
            else:
                last_blocker = readiness_blocker(readiness)
                payload["blocker"] = {
                    "category": "watch_timeout",
                    "reason": f"readiness did not become safe before {max(0.0, float(config.max_wait_seconds))} seconds",
                    "lastBlocker": last_blocker,
                }
            return finalize_payload(payload, trace_path)

    if mode == "dry-run":
        payload["wouldRunCommand"] = build_execute_command(config, trace_path)
        if not payload["ready"]:
            payload["status"] = "BLOCKED"
            payload["blocker"] = readiness_blocker(readiness)
        return finalize_payload(payload, trace_path)

    if (
        not bool(getattr(args, "watch", False))
        and not payload["ready"]
        and readiness.get("livenessRecoveryRecommended")
        and not readiness.get("manualLoginRequired")
        and not readiness.get("unknownScreen")
    ):
        if not config.arduino_port:
            payload["status"] = "BLOCKED"
            payload["blocker"] = {"category": "arduino_port_unknown", "reason": "Loaded-scene recovery needs Arduino, but no port was configured or discovered."}
            return finalize_payload(payload, trace_path)
        recovery, recovery_command = run_ensure_loaded_scene(config, deps)
        payload["loadedSceneRecovery"] = {"returnCode": recovery_command.returncode, "status": recovery.get("status"), "payload": recovery}
        readiness, readiness_command = run_readiness(config, deps)
        payload["readiness"] = compact_readiness(readiness, readiness_command)
        payload["ready"] = readiness_allows_execution(readiness)

    if not payload["ready"]:
        payload["status"] = "BLOCKED"
        payload["blocker"] = readiness_blocker(readiness)
        return finalize_payload(payload, trace_path)

    before_lines = count_lines(trace_path)
    command = build_execute_command(config, trace_path)
    payload["runCommand"] = command
    result = deps.run_command(command, timeout=max(config.max_runtime_seconds + 30.0, 60.0), cwd=PROJECT_ROOT)
    payload["cycleRun"] = {
        "returnCode": result.returncode,
        "stdoutTail": tail_lines(result.stdout, 30),
        "stderrTail": tail_lines(result.stderr, 20),
    }
    payload["status"] = "PASS" if result.returncode == 0 else "FAIL"
    new_records = read_trace_records(trace_path, start_line=before_lines)
    payload["trace"] = summarize_trace(new_records if new_records else read_trace_records(trace_path))
    payload["trace"]["newDecisionCount"] = len(new_records)
    return payload


def watch_until_ready(
    config: DevCycleConfig,
    deps: RuntimeDeps,
    args: argparse.Namespace,
    payload: dict[str, Any],
    readiness: dict[str, Any],
    readiness_command: CommandResult,
    *,
    auto_login_command: list[str] | None,
) -> tuple[dict[str, Any], CommandResult, bool]:
    started = deps.monotonic()
    max_wait = max(0.0, float(config.max_wait_seconds))
    poll_seconds = max(0.1, float(config.watch_poll_seconds))
    current_readiness = readiness
    current_command = readiness_command
    auto = payload.get("autoLogin") if isinstance(payload.get("autoLogin"), dict) else {}
    auto_enabled = bool(auto.get("enabled"))
    auto_invoke_allowed = bool(auto.get("invokeAllowed"))
    auto_attempts = 0
    auto_max_attempts = max(0, int(auto.get("maxAttempts") if auto.get("maxAttempts") is not None else config.auto_login_max_attempts))

    while True:
        ready = readiness_allows_execution(current_readiness)
        elapsed = max(0.0, deps.monotonic() - started)
        blocker = None if ready else readiness_blocker(current_readiness)
        event = append_watch_event(payload, elapsed_seconds=elapsed, ready=ready, blocker=blocker, readiness=current_readiness, emit=not bool(getattr(args, "json", False)))
        if ready:
            return current_readiness, current_command, True
        auto_eligible = readiness_needs_auto_login(current_readiness, blocker)
        event["autoLoginEligible"] = auto_eligible
        if auto_eligible and auto_enabled:
            if not auto_invoke_allowed:
                event["autoLoginWouldAttempt"] = True
            elif not auto_login_command:
                terminal = {
                    "category": "auto_login_missing",
                    "reason": str(auto.get("missingReason") or "no existing auto-login/readiness-recovery command was found"),
                }
                event["autoLoginBlocked"] = terminal
                watch = payload.get("watch") if isinstance(payload.get("watch"), dict) else {}
                watch["terminalBlocker"] = terminal
                return current_readiness, current_command, False
            elif auto_attempts >= auto_max_attempts:
                terminal = {
                    "category": "auto_login_max_attempts_reached",
                    "reason": f"auto-login/readiness recovery reached max attempts ({auto_max_attempts}) before readiness became safe",
                    "lastBlocker": blocker,
                }
                event["autoLoginBlocked"] = terminal
                watch = payload.get("watch") if isinstance(payload.get("watch"), dict) else {}
                watch["terminalBlocker"] = terminal
                return current_readiness, current_command, False
            else:
                auto_attempts += 1
                auto["attemptCount"] = auto_attempts
                recovery_payload, recovery_result = run_auto_login_recovery(config, deps, auto_login_command)
                attempt = append_auto_login_attempt(
                    payload,
                    elapsed_seconds=elapsed,
                    command=auto_login_command,
                    result_payload=recovery_payload,
                    result=recovery_result,
                )
                event["autoLoginAttempt"] = {
                    "number": auto_attempts,
                    "returnCode": attempt.get("returnCode"),
                    "status": attempt.get("status"),
                    "loadedSceneVerified": attempt.get("loadedSceneVerified"),
                    "blocker": attempt.get("blocker"),
                }
                if not auto_login_successful(recovery_payload, recovery_result):
                    terminal = {
                        "category": "auto_login_failed",
                        "reason": "existing auto-login/readiness-recovery command did not recover a loaded scene",
                        "returnCode": recovery_result.returncode,
                        "status": recovery_payload.get("status"),
                        "scriptBlocker": recovery_payload.get("blocker"),
                    }
                    event["autoLoginBlocked"] = terminal
                    watch = payload.get("watch") if isinstance(payload.get("watch"), dict) else {}
                    watch["terminalBlocker"] = terminal
                    return current_readiness, current_command, False
                current_readiness, current_command = run_readiness(config, deps)
                continue
        if elapsed >= max_wait:
            watch = payload.get("watch") if isinstance(payload.get("watch"), dict) else {}
            watch["timedOut"] = True
            return current_readiness, current_command, False
        deps.sleep(min(poll_seconds, max(0.0, max_wait - elapsed)))
        current_readiness, current_command = run_readiness(config, deps)


def append_watch_event(
    payload: dict[str, Any],
    *,
    elapsed_seconds: float,
    ready: bool,
    blocker: dict[str, Any] | None,
    readiness: dict[str, Any],
    emit: bool,
) -> dict[str, Any]:
    loaded = readiness.get("loadedSceneProof") if isinstance(readiness.get("loadedSceneProof"), dict) else {}
    event = {
        "elapsedSeconds": round(elapsed_seconds, 3),
        "ready": ready,
        "status": readiness.get("status"),
        "livenessState": readiness.get("livenessState"),
        "manualLoginRequired": readiness.get("manualLoginRequired"),
        "loadedSceneVerified": loaded.get("loadedSceneVerified"),
        "blocker": blocker,
    }
    watch = payload.get("watch") if isinstance(payload.get("watch"), dict) else {}
    events = watch.setdefault("events", [])
    if isinstance(events, list):
        events.append(event)
    if emit:
        if blocker:
            print(
                f"[watch] {event['elapsedSeconds']}s blocked: {blocker.get('category')} - {blocker.get('reason')}",
                file=sys.stderr,
                flush=True,
            )
        else:
            print(f"[watch] {event['elapsedSeconds']}s ready: action execution allowed", file=sys.stderr, flush=True)
    return event


def check_services(config: DevCycleConfig, deps: RuntimeDeps) -> dict[str, Any]:
    daemon = safe_fetch(deps, daemon_status_url(config.daemon_url))
    snapshot = safe_fetch(deps, snapshot_health_url(config.snapshot_url))
    return {
        "daemon": {
            "url": daemon_status_url(config.daemon_url),
            "reachable": bool(daemon),
            "status": daemon.get("status"),
            "latestTick": daemon.get("latestTick"),
            "sessionPath": daemon.get("sessionPath"),
        },
        "snapshot": {
            "url": snapshot_health_url(config.snapshot_url),
            "reachable": bool(snapshot),
            "status": snapshot.get("status"),
            "latestTick": snapshot.get("latestTick"),
            "staleReasons": snapshot.get("staleReasons") if isinstance(snapshot.get("staleReasons"), list) else [],
        },
    }


def safe_fetch(deps: RuntimeDeps, url: str) -> dict[str, Any]:
    try:
        return deps.fetch_json(url, timeout=3.0)
    except Exception:  # noqa: BLE001
        return {}


def compact_readiness(readiness: dict[str, Any], command: CommandResult) -> dict[str, Any]:
    loaded = readiness.get("loadedSceneProof") if isinstance(readiness.get("loadedSceneProof"), dict) else {}
    daemon = readiness.get("daemon") if isinstance(readiness.get("daemon"), dict) else {}
    action = readiness.get("actionReadiness") if isinstance(readiness.get("actionReadiness"), dict) else {}
    execution = readiness.get("actionExecution") if isinstance(readiness.get("actionExecution"), dict) else {}
    client_tick = readiness.get("clientTickHot") if isinstance(readiness.get("clientTickHot"), dict) else {}
    return {
        "returnCode": command.returncode,
        "status": readiness.get("status"),
        "ready": readiness.get("ready"),
        "executionAllowed": action.get("executionAllowed"),
        "actionAllowed": execution.get("allowed"),
        "manualLoginRequired": readiness.get("manualLoginRequired"),
        "unknownScreen": readiness.get("unknownScreen"),
        "livenessState": readiness.get("livenessState"),
        "loadedSceneVerified": loaded.get("loadedSceneVerified"),
        "gameState": loaded.get("gameState") or client_tick.get("gameState"),
        "latestTick": daemon.get("latestTick"),
        "sessionPath": daemon.get("sessionPath"),
        "proposedAction": readiness.get("proposedAction"),
        "currentIntent": readiness.get("currentIntent"),
        "blockers": readiness.get("blockers") if isinstance(readiness.get("blockers"), list) else [],
        "missingCapabilities": readiness.get("missingCapabilities") if isinstance(readiness.get("missingCapabilities"), list) else [],
    }


def finalize_payload(payload: dict[str, Any], trace_path: Path) -> dict[str, Any]:
    records = read_trace_records(trace_path)
    payload["trace"] = summarize_trace(records)
    payload["trace"]["newDecisionCount"] = 0
    return payload


def wait_for(predicate: Callable[[], bool], seconds: float, sleep: Callable[[float], None]) -> bool:
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if predicate():
            return True
        sleep(1.0)
    return predicate()


def tail_lines(text: str, count: int) -> list[str]:
    return (text or "").splitlines()[-max(0, count) :]


def format_summary(payload: dict[str, Any]) -> str:
    lines = [
        f"TRACED DEV CYCLE - {payload.get('status')}",
        f"Mode: {payload.get('mode')}",
        f"Ready: {'yes' if payload.get('ready') else 'no'}",
    ]
    blocker = payload.get("blocker") if isinstance(payload.get("blocker"), dict) else None
    if blocker:
        lines.append(f"Blocker: {blocker.get('category')} - {blocker.get('reason')}")
        if isinstance(blocker.get("lastBlocker"), dict):
            last = blocker["lastBlocker"]
            lines.append(f"Last blocker: {last.get('category')} - {last.get('reason')}")
    watch = payload.get("watch") if isinstance(payload.get("watch"), dict) else {}
    if watch.get("enabled"):
        events = watch.get("events") if isinstance(watch.get("events"), list) else []
        lines.append(
            f"Watch: enabled max={watch.get('maxWaitSeconds')}s poll={watch.get('pollSeconds')}s "
            f"events={len(events)} timedOut={bool(watch.get('timedOut'))}"
        )
        for event in events[-5:]:
            event_blocker = event.get("blocker") if isinstance(event.get("blocker"), dict) else {}
            lines.append(
                "  watch "
                f"{event.get('elapsedSeconds')}s ready={event.get('ready')} "
                f"liveness={event.get('livenessState')} "
                f"blocker={event_blocker.get('category') or 'none'}"
            )
    auto = payload.get("autoLogin") if isinstance(payload.get("autoLogin"), dict) else {}
    if auto:
        attempts = auto.get("attempts") if isinstance(auto.get("attempts"), list) else []
        lines.append(
            "Auto-login recovery: "
            f"enabled={bool(auto.get('enabled'))} "
            f"invokeAllowed={bool(auto.get('invokeAllowed'))} "
            f"available={bool(auto.get('available'))} "
            f"source={auto.get('source') or 'unknown'} "
            f"attempts={len(attempts)}/{auto.get('maxAttempts')}"
        )
    runelite = payload.get("runelite") if isinstance(payload.get("runelite"), dict) else {}
    lines.append(f"RuneLite: running={'yes' if runelite.get('running') else 'no'} window={runelite.get('windowTitle') or 'none'} processes={runelite.get('processCount', 0)}")
    services = payload.get("services") if isinstance(payload.get("services"), dict) else {}
    daemon = services.get("daemon") if isinstance(services.get("daemon"), dict) else {}
    snapshot = services.get("snapshot") if isinstance(services.get("snapshot"), dict) else {}
    if daemon or snapshot:
        lines.append(f"Daemon: reachable={'yes' if daemon.get('reachable') else 'no'} status={daemon.get('status') or 'unknown'} tick={daemon.get('latestTick')}")
        lines.append(f"Snapshot: reachable={'yes' if snapshot.get('reachable') else 'no'} status={snapshot.get('status') or 'unknown'} tick={snapshot.get('latestTick')}")
    readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
    if readiness:
        lines.append(
            "Readiness: "
            f"status={readiness.get('status')} "
            f"loadedScene={readiness.get('loadedSceneVerified')} "
            f"liveness={readiness.get('livenessState')} "
            f"manualLogin={readiness.get('manualLoginRequired')} "
            f"actionAllowed={readiness.get('actionAllowed')}"
        )
    trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
    lines.append(f"Trace: {payload.get('tracePath')}")
    lines.append(f"Trace decisions: {trace.get('decisionCount', 0)} new={trace.get('newDecisionCount', 0)}")
    if trace.get("decisionCounts"):
        lines.append(f"Decision counts: {json.dumps(trace.get('decisionCounts'), sort_keys=True)}")
    if trace.get("reasonCounts"):
        lines.append(f"Reason counts: {json.dumps(trace.get('reasonCounts'), sort_keys=True)}")
    suspicious = trace.get("firstSuspiciousDecision")
    lines.append(f"First suspicious decision: {json.dumps(suspicious, sort_keys=True) if suspicious else 'none'}")
    if payload.get("mode") == "dry-run" and payload.get("wouldRunCommand"):
        lines.append("Would run existing executor command:")
        lines.append("  " + " ".join(str(item) for item in payload["wouldRunCommand"]))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = run_dev_cycle(args)
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_summary(payload), end="")
    if payload.get("status") == "FAIL":
        return 1
    if payload.get("status") == "BLOCKED" and args.run:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from telemetry_paths import find_newest_session, get_sessions_dir


SCHEMA = "daily_live_gauntlet.v1"


def fetch_json(url: str, timeout: float = 1.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    return value if isinstance(value, dict) else {}


def post_json(url: str, payload: dict, timeout: float = 1.0) -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    return value if isinstance(value, dict) else {}


def daemon_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


def command_text(process: dict[str, Any]) -> str:
    return str(process.get("commandLine") or process.get("cmdline") or process.get("command") or "")


def boolish_true(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return False


def process_matches(process: dict[str, Any], token: str) -> bool:
    return token.lower() in command_text(process).lower()


def detect_process_conflicts(processes: list[dict[str, Any]]) -> dict:
    live_daemons = [process for process in processes if process_matches(process, "live_core_daemon.py")]
    live_processors = [process for process in processes if process_matches(process, "live_target_processor.py")]
    context_services = [process for process in processes if process_matches(process, "context_service.py")]
    warnings: list[str] = []
    if len(live_daemons) > 1:
        warnings.append("multiple live_core_daemon.py processes appear active; duplicate daemons can cause flicker or confusing context")
    if live_daemons and live_processors:
        warnings.append("live_core_daemon.py and live_target_processor.py both appear active; stop the legacy live processor for daily daemon mode")
    if live_daemons and context_services:
        warnings.append("live_core_daemon.py and context_service.py both appear active; the daemon already serves the context API")
    return {
        "liveCoreDaemonCount": len(live_daemons),
        "liveTargetProcessorCount": len(live_processors),
        "contextServiceCount": len(context_services),
        "warnings": warnings,
        "status": "WARN" if warnings else "PASS",
    }


def context_request() -> dict:
    return {
        "schema": "context_request.v1",
        "task": "woodcutting",
        "needs": ["baseline", "best:tree", "inventory", "activity", "liveness", "navigation_readiness", "diagnostics"],
        "maxCandidates": 3,
        "maxEvents": 5,
        "responseMode": "compact",
    }


def forbidden_field_paths(value: Any, path: str = "") -> list[str]:
    forbidden_exact = {"click", "mouse", "keyboard", "menu", "invoke", "execute", "input"}
    forbidden_fragments = ("click", "mouse", "keyboard", "menu", "invoke", "execute")
    allowed = {"noActionEmitted", "readOnlyTelemetry", "inputSourceActive", "inputSourceRequestedByDaemon"}
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            lowered = key_text.lower()
            if key_text not in allowed and (lowered in forbidden_exact or any(fragment in lowered for fragment in forbidden_fragments)):
                paths.append(child_path)
            paths.extend(forbidden_field_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value[:50]):
            paths.extend(forbidden_field_paths(child, f"{path}[{index}]"))
    return paths


def progress_failures_from(payload: dict) -> list[str]:
    failures: list[str] = []
    progress = payload.get("brainProgress") if isinstance(payload.get("brainProgress"), dict) else {}
    brain = payload.get("brain") if isinstance(payload.get("brain"), dict) else {}
    if not progress and isinstance(brain.get("goalProgress"), dict):
        progress = brain["goalProgress"]
    matched_details = progress.get("matchedSlotDetails") if isinstance(progress.get("matchedSlotDetails"), list) else []
    invalid_slots = [
        row for row in matched_details
        if isinstance(row, dict) and row.get("counted") is True and row.get("itemId") is None
    ]
    if invalid_slots:
        failures.append("brain progress has counted matched slots without itemId")
    if progress.get("currentSnapshotValid") is False and progress.get("progressRetainedFromPrevious") is not True and progress.get("lastValidProgressTick") is not None:
        failures.append("invalid inventory snapshot did not retain previous valid progress")
    if progress.get("complete") and progress.get("displayedGoalProgress") is not None and progress.get("goalCount") is not None:
        try:
            if int(progress.get("displayedGoalProgress")) < int(progress.get("goalCount")):
                failures.append("goal complete is true below displayed goal progress")
        except (TypeError, ValueError):
            failures.append("goal progress fields are not numeric")
    return failures


def evaluate_daemon_payloads(
    daemon_health: dict[str, Any],
    daemon_status: dict[str, Any],
    context_payload: dict[str, Any] | None = None,
    brain_payload: dict[str, Any] | None = None,
) -> dict:
    warnings: list[str] = []
    failures: list[str] = []
    if daemon_status:
        if daemon_status.get("liveCoreDaemonActive") is not True:
            failures.append("daemon status does not report liveCoreDaemonActive=true")
        if daemon_status.get("writeDebugLiveFiles"):
            warnings.append("debug live files are enabled; daily daemon should keep rolling writes off")
        source = daemon_status.get("inputSourceActive")
        if source and source != "compact-packets":
            failures.append(f"{source} is active; daily daemon should use compact-packets")
        if source == "compact-packets" and daemon_status.get("compactPacketsRecent") is False:
            failures.append("compact-packets input is active but compact packets are not recent")
        if boolish_true(daemon_status.get("rawTickRecordingEnabled")):
            failures.append("raw tick recording appears enabled; daily mode should keep raw ticks off")
        if boolish_true(daemon_status.get("rawEventRecordingEnabled")):
            failures.append("raw event recording appears enabled; daily mode should keep raw events off")
        if boolish_true(daemon_status.get("frameRecordingEnabled")):
            failures.append("frame recording appears enabled; daily mode should keep frames off")
        if any(boolish_true(daemon_status.get(key)) for key in ("captureScreenshots", "screenshotCaptureEnabled", "screenshotsEnabled")):
            failures.append("screenshot capture appears enabled; daily mode should keep screenshots off")
        if any(boolish_true(daemon_status.get(key)) for key in ("cropCaptureEnabled", "cropCaptureActive", "cropsEnabled")):
            failures.append("crop capture appears enabled; daily mode should keep crop/perception tools off")
        if any(boolish_true(daemon_status.get(key)) for key in ("perceptionCaptureEnabled", "perceptionCaptureActive", "perceptionEnabled")):
            failures.append("perception capture appears enabled; daily mode should keep crop/perception tools off")
        if daemon_status.get("overlayStateWritten") and daemon_status.get("overlayStateFresh") is False:
            failures.append("overlay state is enabled but not fresh")
        if daemon_status.get("overlayStateWritten") and daemon_status.get("overlayMode") not in (None, "intent"):
            warnings.append("daily overlay is not in intent mode; use --overlay-mode intent to avoid candidate clutter")
        if daemon_status.get("contextRetainedPrevious"):
            warnings.append("context retained previous good state this poll; check source freshness if this persists")
        if daemon_status.get("progressRetainedFromPrevious") or daemon_status.get("progressRetainedPreviousThisPoll"):
            warnings.append("progress retained previous valid snapshot this poll; inventory input may be transiently incomplete")
        failures.extend(progress_failures_from(daemon_status))
    if context_payload:
        if context_payload.get("status") == "FAIL":
            failures.append("daily context endpoint returned FAIL")
        elif context_payload.get("status") == "WARN":
            warnings.append("daily context endpoint returned WARN")
        forbidden = forbidden_field_paths(context_payload)
        if forbidden:
            failures.append("context output contains action/input/menu-shaped fields: " + ", ".join(forbidden[:5]))
    if brain_payload:
        failures.extend(progress_failures_from({"brain": brain_payload, "brainProgress": brain_payload.get("goalProgress")}))
        forbidden = forbidden_field_paths(brain_payload)
        dangerous = [path for path in forbidden if not path.endswith("noActionEmitted")]
        if dangerous:
            failures.append("brain output contains action/input/menu-shaped fields: " + ", ".join(dangerous[:5]))
    if daemon_health and daemon_health.get("status") == "FAIL":
        failures.append("daemon health returned FAIL")
    return {"warnings": warnings, "failures": failures}


def list_processes() -> list[dict[str, Any]]:
    if sys.platform.startswith("win"):
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Depth 3",
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)
        except (OSError, subprocess.SubprocessError):
            return []
        try:
            value = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError:
            return []
        rows = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
        return [
            {"pid": row.get("ProcessId"), "commandLine": row.get("CommandLine")}
            for row in rows
            if isinstance(row, dict) and row.get("CommandLine")
        ]
    try:
        completed = subprocess.run(["ps", "-eo", "pid=,command="], capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_text, _, command = line.partition(" ")
        rows.append({"pid": pid_text.strip(), "commandLine": command.strip()})
    return rows


def resolve_session(args: argparse.Namespace) -> str | None:
    if args.session:
        return str(Path(args.session).resolve())
    if args.latest_session:
        session = find_newest_session(get_sessions_dir(args.sessions_dir))
        return str(session.resolve()) if session else None
    return None


def build_report(args: argparse.Namespace, processes: list[dict[str, Any]] | None = None) -> dict:
    warnings: list[str] = []
    failures: list[str] = []
    process_report = detect_process_conflicts(processes if processes is not None else list_processes() if args.check_processes or args.strict else [])
    warnings.extend(process_report.get("warnings") or [])

    daemon_health: dict[str, Any] = {}
    daemon_status: dict[str, Any] = {}
    context_payload: dict[str, Any] = {}
    brain_payload: dict[str, Any] = {}
    try:
        daemon_health = fetch_json(daemon_url(args.daemon_url, "/health"))
        daemon_status = fetch_json(daemon_url(args.daemon_url, "/status"))
        context_payload = post_json(daemon_url(args.daemon_url, "/context"), context_request())
        brain_payload = fetch_json(daemon_url(args.daemon_url, "/brain?task=woodcutting"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        if args.strict:
            failures.append(f"live_core_daemon health/status unavailable: {type(error).__name__}")
        else:
            warnings.append(f"live_core_daemon health/status unavailable: {type(error).__name__}")

    daemon_eval = evaluate_daemon_payloads(daemon_health, daemon_status, context_payload, brain_payload)
    warnings.extend(daemon_eval.get("warnings") or [])
    failures.extend(daemon_eval.get("failures") or [])

    status = "FAIL" if failures else "WARN" if warnings else "PASS"
    return {
        "schema": SCHEMA,
        "status": status,
        "strict": bool(args.strict),
        "sessionPath": resolve_session(args),
        "daemonUrl": args.daemon_url,
        "daemonHealth": daemon_health,
        "daemonStatus": daemon_status,
        "context": context_payload,
        "brain": brain_payload,
        "processes": process_report,
        "warnings": warnings,
        "failures": failures,
        "suggestions": suggestions_for(warnings + failures),
    }


def suggestions_for(messages: list[str]) -> list[str]:
    suggestions: list[str] = []
    joined = "\n".join(messages).lower()
    if "multiple live_core_daemon" in joined or "live_target_processor" in joined or "context_service" in joined:
        suggestions.append("Use Stop All in the Live Control Panel, then start only the Streamlined Live Daemon.")
    if "debug live files" in joined:
        suggestions.append("Start live_core_daemon without --write-debug-live-files for daily mode.")
    if "compact-stream" in joined:
        suggestions.append("Use compact-packets for daily mode; compact-stream remains experimental.")
    if "screenshot capture" in joined or "crop capture" in joined or "perception capture" in joined:
        suggestions.append("Apply Daily Live Preset and keep screenshot/crop/perception tooling under Advanced Debug.")
    if "inventory" in joined:
        suggestions.append("Run diagnose_brain_progress.py --from-daemon --strict to inspect retained progress.")
    return list(dict.fromkeys(suggestions))


def format_human(report: dict) -> str:
    lines = [
        "DAILY LIVE GAUNTLET",
        "",
        f"Status: {report.get('status')}",
        f"Session: {report.get('sessionPath') or 'not resolved'}",
        f"Daemon: {report.get('daemonUrl')}",
        "",
        "Processes:",
    ]
    processes = report.get("processes") if isinstance(report.get("processes"), dict) else {}
    lines.extend(
        [
            f"  live_core_daemon.py: {processes.get('liveCoreDaemonCount', 0)}",
            f"  live_target_processor.py: {processes.get('liveTargetProcessorCount', 0)}",
            f"  context_service.py: {processes.get('contextServiceCount', 0)}",
            "",
            "Warnings:",
        ]
    )
    warnings = report.get("warnings") or []
    failures = report.get("failures") or []
    if not warnings and not failures:
        lines.append("  none")
    for failure in failures:
        lines.append(f"  FAIL: {failure}")
    for warning in warnings:
        lines.append(f"  WARN: {warning}")
    suggestions = report.get("suggestions") or []
    if suggestions:
        lines.extend(["", "Suggestions:"])
        for suggestion in suggestions:
            lines.append(f"  {suggestion}")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only daily live telemetry gauntlet.")
    parser.add_argument("--session", help="Explicit telemetry session directory.")
    parser.add_argument("--latest-session", action="store_true", help="Use newest telemetry session.")
    parser.add_argument("--sessions-dir", help="Override sessions directory when using --latest-session.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890", help="live_core_daemon URL.")
    parser.add_argument("--check-processes", action="store_true", help="Check for duplicate/conflicting live processes.")
    parser.add_argument("--strict", action="store_true", help="Treat missing daemon status as failure.")
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(format_human(report), end="")
    return 1 if report.get("status") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())

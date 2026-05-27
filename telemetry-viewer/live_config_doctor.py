from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telemetry_paths import find_newest_session, get_sessions_dir, list_event_files, list_tick_files


SCHEMA = "live_config_doctor.v1"
RECENT_LEGACY_SECONDS = 120.0
STALE_SESSION_SECONDS = 15 * 60
MODES = ("daily", "snapshot_no_file", "visual_qa", "debug_audit", "plugin_snapshot_experimental")
MODE_PRESET_RECOMMENDATIONS = {
    "daily": ("DAILY_SNAPSHOT_NO_FILE", "Click Apply Daily Snapshot No-File Preset."),
    "snapshot_no_file": ("DAILY_SNAPSHOT_NO_FILE", "Click Apply Daily Snapshot No-File Preset."),
    "visual_qa": ("VISUAL_QA", "Click Apply Visual QA Preset."),
    "debug_audit": ("DEBUG_AUDIT", "Click Apply Debug Audit Preset."),
    "plugin_snapshot_experimental": ("PLUGIN_SNAPSHOT_EXPERIMENTAL", "Click Apply Plugin Snapshot Experimental Preset."),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return {}


def file_age_seconds(path: Path | None, *, now: float | None = None) -> float | None:
    if path is None:
        return None
    try:
        return max(0.0, (time.time() if now is None else now) - path.stat().st_mtime)
    except OSError:
        return None


def first_present(*values):
    for value in values:
        if value is not None:
            return value
    return None


def boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "on", "1"}:
            return True
        if lowered in {"false", "no", "off", "0"}:
            return False
    return None


def numeric(value: Any) -> int | float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def resolve_session(args: argparse.Namespace) -> Path | None:
    if args.session:
        return Path(args.session).expanduser().resolve()
    if args.latest_session:
        newest = find_newest_session(get_sessions_dir(args.sessions_dir))
        return newest.resolve() if newest else None
    return None


def compact_packet_summary(session: Path, *, now: float | None = None) -> dict:
    packet_dir = session / "live_packets"
    files = sorted(list(packet_dir.glob("live-*.ndjson")) + list(packet_dir.glob("live-*.jsonl"))) if packet_dir.exists() else []
    total_bytes = 0
    ages = []
    for path in files:
        try:
            total_bytes += int(path.stat().st_size)
        except OSError:
            continue
        age = file_age_seconds(path, now=now)
        if age is not None:
            ages.append(age)
    age = min(ages) if ages else None
    return {
        "available": False,
        "recent": False,
        "runtimeRemoved": True,
        "writerActive": False,
        "legacyLivePacketFilesPresent": bool(files),
        "legacyLivePacketFileCount": len(files),
        "legacyLivePacketTotalBytes": total_bytes,
        "legacyLivePacketTotalMb": round(total_bytes / (1024 * 1024), 3),
        "ageSeconds": age,
        "indexPath": None,
        "latestSegment": str(files[-1]) if files else None,
        "latestSegmentExists": False,
        "latestTick": None,
        "latestSequence": None,
    }


def request_json(url: str, *, timeout: float = 0.25) -> tuple[dict | None, str | None, int | None]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=max(0.001, timeout)) as response:
            raw = response.read()
            status = response.getcode()
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        return payload if isinstance(payload, dict) else None, None, status
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        return None, f"{type(error).__name__}: {error}", None


def plugin_snapshot_health(host: str, port: int, *, timeout: float = 0.25) -> dict:
    payload, error, code = request_json(f"http://{host}:{int(port)}/health", timeout=timeout)
    return {
        "available": isinstance(payload, dict),
        "statusCode": code,
        "error": error,
        "health": payload if isinstance(payload, dict) else {},
        "status": payload.get("status") if isinstance(payload, dict) else None,
        "latestTick": payload.get("latestTick") if isinstance(payload, dict) else None,
        "cachedPacketTypes": payload.get("cachedPacketTypes") if isinstance(payload, dict) else [],
    }


def context_service_health(port: int, *, timeout: float = 0.25) -> dict:
    payload, error, code = request_json(f"http://127.0.0.1:{int(port)}/health", timeout=timeout)
    return {
        "available": isinstance(payload, dict),
        "statusCode": code,
        "error": error,
        "health": payload if isinstance(payload, dict) else {},
        "status": payload.get("status") if isinstance(payload, dict) else None,
        "latestTick": payload.get("latestTick") if isinstance(payload, dict) else None,
        "service": payload.get("service") if isinstance(payload, dict) else None,
        "liveCoreDaemonActive": bool(payload.get("liveCoreDaemonActive")) if isinstance(payload, dict) else False,
        "inputSourceActive": payload.get("inputSourceActive") if isinstance(payload, dict) else None,
        "dailyMode": payload.get("dailyMode") if isinstance(payload, dict) else None,
        "noFileDaily": payload.get("noFileDaily") if isinstance(payload, dict) else None,
        "compactPacketFilesRequired": payload.get("compactPacketFilesRequired") if isinstance(payload, dict) else None,
        "compactPacketFilesWriting": payload.get("compactPacketFilesWriting") if isinstance(payload, dict) else None,
        "debugMirrorEnabled": payload.get("debugMirrorEnabled") if isinstance(payload, dict) else None,
        "writeDebugLiveFiles": payload.get("writeDebugLiveFiles") if isinstance(payload, dict) else None,
        "overlayStateWritten": payload.get("overlayStateWritten") if isinstance(payload, dict) else None,
        "candidateCount": payload.get("candidateCount") if isinstance(payload, dict) else None,
    }


def process_summary() -> dict:
    commands: list[str]
    if os.name == "nt":
        commands = ["wmic", "process", "get", "ProcessId,CommandLine"]
    else:
        commands = ["ps", "-eo", "pid,args"]
    try:
        result = subprocess.run(commands, capture_output=True, text=True, timeout=2.0)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "error": f"{type(error).__name__}: {error}", "liveTargetProcessorCount": None}
    haystack = result.stdout or ""
    return {
        "available": result.returncode == 0,
        "error": None if result.returncode == 0 else (result.stderr or f"exit {result.returncode}"),
        "liveTargetProcessorCount": haystack.count("live_target_processor.py"),
        "contextServiceCount": haystack.count("context_service.py"),
        "liveCoreDaemonCount": haystack.count("live_core_daemon.py"),
    }


def add_issue(report: dict, severity: str, code: str, message: str, suggestion: str | None = None) -> None:
    issue = {"severity": severity, "code": code, "message": message}
    if suggestion:
        issue["suggestion"] = suggestion
        report.setdefault("fixSuggestions", []).append(suggestion)
    report.setdefault("issues", []).append(issue)
    if severity == "FAIL":
        report.setdefault("failures", []).append(message)
    elif severity == "WARN":
        report.setdefault("warnings", []).append(message)


def derive_status(report: dict) -> str:
    if report.get("failures"):
        return "FAIL"
    if report.get("warnings"):
        return "WARN"
    return "PASS"


def collect_live_snapshot(
    session: Path | None,
    *,
    context_port: int = 8890,
    plugin_snapshot_host: str = "127.0.0.1",
    plugin_snapshot_port: int = 8893,
    check_context_service: bool = True,
    check_plugin_snapshot: bool = False,
    check_processes: bool = False,
    now: float | None = None,
) -> dict:
    if session is None:
        return {"sessionPath": None, "sessionExists": False}

    live_dir = session / "interaction_geometry" / "live"
    status = safe_load_json(live_dir / "live_status.json")
    performance = safe_load_json(live_dir / "live_performance_summary.json")
    context = safe_load_json(live_dir / "live_context_index.json")
    overlay = safe_load_json(live_dir / "overlay_debug_state.json")
    manifest = safe_load_json(session / "manifest.json")
    packet = compact_packet_summary(session, now=now)
    overlay_summary = overlay.get("summary") if isinstance(overlay.get("summary"), dict) else {}
    overlay_targets = overlay.get("targets") if isinstance(overlay.get("targets"), list) else []
    collision_window = overlay.get("collisionWindow") if isinstance(overlay.get("collisionWindow"), dict) else {}
    plugin_enabled_hint = first_present(
        status.get("pluginSnapshotEnabled"),
        status.get("enablePluginSnapshotEndpoint"),
        manifest.get("pluginSnapshotEnabled"),
        manifest.get("enablePluginSnapshotEndpoint"),
    )
    stream_enabled = first_present(
        status.get("compactLiveStreamEnabled"),
        manifest.get("compactLiveStreamEnabled"),
    )
    stream_dropped = first_present(
        status.get("compactStreamDroppedPackets"),
        status.get("compactLiveStreamPacketsDropped"),
        status.get("compactLiveStreamPacketsDroppedTotal"),
    )
    latest_live_path = live_dir / "live_status.json"
    latest_live_age = file_age_seconds(latest_live_path, now=now)
    session_age = min(
        [age for age in (file_age_seconds(session, now=now), packet.get("ageSeconds"), latest_live_age) if age is not None],
        default=None,
    )
    raw_ticks = list_tick_files(session)
    raw_events = list_event_files(session)

    context_health = context_service_health(context_port) if check_context_service else {"available": False}
    live_core_active = bool(context_health.get("liveCoreDaemonActive"))
    daemon_health = context_health.get("health") if isinstance(context_health.get("health"), dict) else {}
    daemon_status = daemon_health if live_core_active else {}
    active_input = first_present(daemon_status.get("inputSourceActive"), status.get("inputSourceActive"))
    compact_file_manifest = first_present(
        daemon_status.get("compactPacketFilesEnabledInManifest"),
        status.get("compactLivePacketFilesEnabled"),
        manifest.get("compactLivePacketFilesEnabled"),
        False,
    )
    compact_files_required = first_present(
        daemon_status.get("compactPacketFilesRequired"),
        status.get("compactPacketFilesRequired"),
        False,
    )
    compact_files_writing = first_present(
        daemon_status.get("compactPacketFilesWriting"),
        status.get("compactPacketFilesWriting"),
        False,
    )
    snapshot = {
        "sessionPath": str(session),
        "sessionExists": session.exists(),
        "status": status,
        "performance": performance,
        "context": context,
        "overlayDebug": overlay,
        "manifest": manifest,
        "packetIndex": {},
        "liveCoreDaemonActive": live_core_active,
        "liveCoreWriteDebugLiveFiles": daemon_status.get("writeDebugLiveFiles"),
        "liveCoreOverlayStateWritten": daemon_status.get("overlayStateWritten"),
        "dailyMode": first_present(daemon_status.get("dailyMode"), status.get("dailyMode")),
        "noFileDaily": first_present(daemon_status.get("noFileDaily"), status.get("noFileDaily")),
        "compactPacketFilesRequired": compact_files_required,
        "compactPacketFilesWriting": compact_files_writing,
        "compactPacketFilesEnabledInManifest": compact_file_manifest,
        "debugMirrorEnabled": first_present(daemon_status.get("debugMirrorEnabled"), status.get("debugMirrorEnabled")),
        "inputSourceActive": active_input,
        "recordingMode": first_present(status.get("recordingMode"), manifest.get("recordingMode")),
        "rawTicksEnabled": first_present(status.get("rawTickRecordingEnabled"), manifest.get("rawTickRecordingEnabled")),
        "rawEventsEnabled": first_present(status.get("rawEventRecordingEnabled"), manifest.get("rawEventRecordingEnabled")),
        "framesEnabled": first_present(status.get("frameRecordingEnabled"), manifest.get("frameRecordingEnabled")),
        "screenshotsEnabled": first_present(
            status.get("captureScreenshots"),
            status.get("screenshotCaptureEnabled"),
            status.get("screenshotsEnabled"),
            manifest.get("captureScreenshots"),
            manifest.get("screenshotCaptureEnabled"),
            manifest.get("screenshotsEnabled"),
        ),
        "cropCaptureEnabled": first_present(
            status.get("cropCaptureEnabled"),
            status.get("cropCaptureActive"),
            status.get("cropsEnabled"),
            manifest.get("cropCaptureEnabled"),
            manifest.get("cropCaptureActive"),
            manifest.get("cropsEnabled"),
        ),
        "perceptionCaptureEnabled": first_present(
            status.get("perceptionCaptureEnabled"),
            status.get("perceptionCaptureActive"),
            status.get("perceptionEnabled"),
            manifest.get("perceptionCaptureEnabled"),
            manifest.get("perceptionCaptureActive"),
            manifest.get("perceptionEnabled"),
        ),
        "rawTickFilesAvailable": bool(raw_ticks),
        "rawEventFilesAvailable": bool(raw_events),
        "compactPacketsAvailable": packet.get("available"),
        "compactPacketsRecent": packet.get("recent"),
        "livePacketsRuntimeRemoved": packet.get("runtimeRemoved"),
        "livePacketWriterActive": packet.get("writerActive"),
        "legacyLivePacketFilesPresent": packet.get("legacyLivePacketFilesPresent"),
        "legacyLivePacketFileCount": packet.get("legacyLivePacketFileCount"),
        "legacyLivePacketTotalMb": packet.get("legacyLivePacketTotalMb"),
        "compactPacketAgeSeconds": packet.get("ageSeconds"),
        "latestSegment": packet.get("latestSegment"),
        "latestTick": first_present(daemon_status.get("latestTick"), status.get("latestTickProcessed"), status.get("lastProcessedTick"), status.get("latestTick"), context.get("latestTick"), packet.get("latestTick")),
        "compactPacketLatestTick": packet.get("latestTick"),
        "compactPacketLatestSequence": packet.get("latestSequence"),
        "compactStreamEnabled": stream_enabled,
        "compactStreamActive": active_input == "compact-stream",
        "compactStreamDroppedPackets": stream_dropped,
        "compactStreamMissingRequiredTypes": status.get("compactStreamMissingRequiredTypesForLatestTick") or [],
        "pluginSnapshotEnabledHint": plugin_enabled_hint,
        "pluginSnapshotInputActive": active_input == "plugin-snapshot",
        "pluginSnapshotTier": status.get("pluginSnapshotTier"),
        "pluginSnapshotProjectionFieldMode": status.get("pluginSnapshotProjectionFieldMode") or status.get("projectionFieldMode"),
        "pluginSnapshotMaxProjectionRefs": status.get("pluginSnapshotMaxProjectionRefs"),
        "pluginSnapshotTotalActiveMillis": first_present(status.get("pluginSnapshotTotalActiveMillis"), status.get("totalActiveMillis"), status.get("totalWallMillis")),
        "pluginSnapshotBottleneck": status.get("pluginSnapshotBottleneck"),
        "candidateCount": first_present(daemon_status.get("candidateCount"), status.get("candidateCount")),
        "activeMs": first_present(daemon_status.get("activeMs"), status.get("activeMs"), status.get("liveCoreActiveMillis")),
        "windowTicks": status.get("windowTicks"),
        "candidateOutputWindow": status.get("candidateOutputWindow"),
        "livenessMode": status.get("livenessMode"),
        "overlayTargetCount": len(overlay_targets),
        "overlayTargetLimit": first_present(overlay_summary.get("targetLimit"), status.get("overlayDebugTargetLimit")),
        "overlayMode": first_present(overlay_summary.get("overlayMode"), status.get("overlayMode")),
        "intentMarkerCount": first_present(overlay_summary.get("intentMarkerCount"), status.get("intentMarkerCount")),
        "overlayGeometryMode": first_present(overlay.get("geometryMode"), overlay_summary.get("geometryMode"), status.get("telemetryDebugOverlayGeometryMode")),
        "collisionWindowStatus": "available" if collision_window.get("available") is True else "unknown",
        "budgetExceeded": first_present(status.get("budgetExceeded"), status.get("warningUpdateExceeded")),
        "writeFailures": first_present(status.get("writeFailureCount"), status.get("writeFailures"), 0),
        "liveStatusAgeSeconds": latest_live_age,
        "sessionAgeSeconds": session_age,
        "liveStatusPath": str(latest_live_path),
    }
    if check_plugin_snapshot or plugin_enabled_hint is True or snapshot["pluginSnapshotInputActive"]:
        snapshot["pluginSnapshotHealth"] = plugin_snapshot_health(plugin_snapshot_host, plugin_snapshot_port)
    else:
        snapshot["pluginSnapshotHealth"] = {"available": False}
    snapshot["contextServiceHealth"] = context_health
    if check_processes:
        snapshot["processes"] = process_summary()
    return snapshot


def apply_common_rules(report: dict, snapshot: dict, mode: str) -> None:
    if not snapshot.get("sessionExists"):
        add_issue(report, "FAIL", "missing_session", "No session was found.", "Start RuneLite dev, verify the plugin snapshot endpoint, then rerun the doctor.")
        return
    session_age = snapshot.get("sessionAgeSeconds")
    if isinstance(session_age, (int, float)) and session_age > STALE_SESSION_SECONDS:
        add_issue(
            report,
            "WARN",
            "stale_session",
            f"Latest session activity is about {int(session_age // 60)} minutes old.",
            "Confirm this is the intended session or start a fresh normal live run.",
        )
    if snapshot.get("legacyLivePacketFilesPresent"):
        add_issue(
            report,
            "WARN",
            "legacy_live_packets_present",
            f"Legacy live packet archives remain on disk ({snapshot.get('legacyLivePacketTotalMb')} MB).",
            "Run maintenance.py --prune-legacy-live-packets --dry-run, then --apply only after review.",
        )
    write_failures = numeric(snapshot.get("writeFailures")) or 0
    if write_failures > 0:
        add_issue(report, "WARN", "write_failures", f"Live processor reports {write_failures} write failures.", "Check permissions and disk pressure in the session folder.")
    processes = snapshot.get("processes") if isinstance(snapshot.get("processes"), dict) else {}
    live_count = processes.get("liveTargetProcessorCount")
    if isinstance(live_count, int) and live_count > 1:
        add_issue(report, "WARN", "duplicate_live_processors", f"{live_count} live_target_processor.py processes appear to be running.", "Stop duplicate processors from the control panel and keep one live processor per session.")
    if snapshot.get("liveCoreDaemonActive"):
        if isinstance(live_count, int) and live_count > 0:
            add_issue(report, "WARN", "daemon_and_legacy_processor", "Both live_core_daemon and legacy live_target_processor appear active.", "Use the streamlined daemon for daily mode, or stop it before starting the legacy file-based stack.")
        context_count = processes.get("contextServiceCount")
        if isinstance(context_count, int) and context_count > 0:
            add_issue(report, "WARN", "daemon_and_legacy_context", "Both live_core_daemon and legacy context_service appear active.", "Keep only one context API listener on the daily port.")


def apply_daily_rules(report: dict, snapshot: dict) -> None:
    input_source = snapshot.get("inputSourceActive")
    daemon_active = bool(snapshot.get("liveCoreDaemonActive"))
    if input_source and input_source != "plugin-snapshot":
        add_issue(report, "WARN", "daily_input_source", f"Daily mode is using {input_source}.", "Use --daily-mode snapshot-no-files --input-source plugin-snapshot for daily mode.")
    if daemon_active and snapshot.get("liveCoreWriteDebugLiveFiles") is True:
        add_issue(report, "WARN", "daily_daemon_debug_writes", "Streamlined live daemon is writing debug live files.", "Run live_core_daemon without --write-debug-live-files for daily mode.")
    recording_mode = snapshot.get("recordingMode")
    if recording_mode and recording_mode != "LIVE_COMPACT_ONLY":
        add_issue(report, "WARN", "daily_recording_mode", f"Recording mode is {recording_mode}.", "Use LIVE_COMPACT_ONLY for normal live.")
    for key, label in (
        ("rawTicksEnabled", "raw tick recording"),
        ("rawEventsEnabled", "raw event recording"),
        ("framesEnabled", "frame recording"),
        ("screenshotsEnabled", "screenshot capture"),
        ("cropCaptureEnabled", "crop capture"),
        ("perceptionCaptureEnabled", "perception capture"),
    ):
        if boolish(snapshot.get(key)) is True:
            add_issue(report, "WARN", f"daily_{key}", f"{label} is enabled.", f"Disable {label} for daily LIVE_COMPACT_ONLY mode.")
    if snapshot.get("compactStreamActive") or boolish(snapshot.get("compactStreamEnabled")) is True:
        add_issue(report, "WARN", "daily_compact_stream", "Compact stream is enabled or active in daily mode.", "Disable compact stream; live packet stream runtime is retired.")
    window_ticks = snapshot.get("windowTicks")
    if isinstance(window_ticks, int) and window_ticks > 20:
        add_issue(report, "WARN", "daily_window_ticks", f"windowTicks is {window_ticks}.", "Use --window-ticks 10.")
    output_window = snapshot.get("candidateOutputWindow")
    if output_window and output_window != "latest":
        add_issue(report, "WARN", "daily_candidate_output_window", f"candidateOutputWindow is {output_window}.", "Use --candidate-output-window latest.")
    liveness = snapshot.get("livenessMode")
    if liveness and liveness != "delta":
        add_issue(report, "WARN", "daily_liveness_mode", f"livenessMode is {liveness}.", "Use --liveness-mode delta.")
    overlay_limit = snapshot.get("overlayTargetLimit")
    if isinstance(overlay_limit, int) and overlay_limit > 10:
        add_issue(report, "WARN", "daily_overlay_limit", f"Overlay target limit is {overlay_limit}.", "Set overlay target limit to 5 or 10 for daily mode.")
    overlay_mode = snapshot.get("overlayMode")
    if overlay_mode and overlay_mode != "intent":
        add_issue(report, "WARN", "daily_overlay_mode", f"Overlay mode is {overlay_mode}.", "Use --overlay-mode intent for daily mode; keep candidates/debug for visual QA.")
    if snapshot.get("budgetExceeded") is True:
        add_issue(report, "WARN", "daily_budget_exceeded", "Live processor budget is exceeded.", "Use daily snapshot no-file settings, then inspect timing buckets.")


def apply_visual_qa_rules(report: dict, snapshot: dict) -> None:
    input_source = snapshot.get("inputSourceActive")
    if input_source and input_source != "plugin-snapshot":
        add_issue(report, "WARN", "visual_qa_input_source", f"Visual QA is using {input_source}.", "Prefer --input-source plugin-snapshot for live visual QA.")
    overlay_limit = snapshot.get("overlayTargetLimit")
    if isinstance(overlay_limit, int) and overlay_limit > 25:
        add_issue(report, "WARN", "visual_qa_overlay_limit", f"Overlay target limit is {overlay_limit}.", "Use an overlay target limit of 25 or lower for visual QA.")
    if snapshot.get("recordingMode") == "DEBUG_RECORDING":
        add_issue(report, "WARN", "visual_qa_debug_recording", "DEBUG_RECORDING is enabled during visual QA.", "Use visual QA settings unless you intentionally need disk-heavy debug audit recording.")


def apply_debug_audit_rules(report: dict, snapshot: dict) -> None:
    if boolish(snapshot.get("rawTicksEnabled")) is True or boolish(snapshot.get("framesEnabled")) is True:
        add_issue(report, "WARN", "debug_audit_disk_growth", "Raw ticks or frames are enabled for debug audit.", "Watch disk usage and stop recording when the audit capture is complete.")


def apply_plugin_snapshot_rules(report: dict, snapshot: dict, *, require_compact_fallback: bool = False) -> None:
    health = snapshot.get("pluginSnapshotHealth") if isinstance(snapshot.get("pluginSnapshotHealth"), dict) else {}
    if health.get("status") != "PASS":
        add_issue(report, "FAIL", "plugin_snapshot_health", f"Plugin snapshot endpoint health is {health.get('status') or 'unavailable'}.", "Enable the plugin snapshot endpoint and verify GET http://127.0.0.1:8893/health.")
    if snapshot.get("inputSourceActive") and snapshot.get("inputSourceActive") != "plugin-snapshot":
        add_issue(report, "WARN", "plugin_snapshot_input_source", f"Input source is {snapshot.get('inputSourceActive')}.", "Use --input-source plugin-snapshot only when testing the experimental snapshot bridge.")
    tier = snapshot.get("pluginSnapshotTier")
    if tier and tier != "hot":
        add_issue(report, "WARN", "plugin_snapshot_tier", f"Plugin snapshot tier is {tier}.", "Use --plugin-snapshot-tier hot for realtime experiments.")
    field_mode = snapshot.get("pluginSnapshotProjectionFieldMode")
    if field_mode and field_mode != "compact":
        add_issue(report, "WARN", "plugin_snapshot_field_mode", f"Projection field mode is {field_mode}.", "Use --plugin-snapshot-projection-field-mode compact.")
    max_refs = snapshot.get("pluginSnapshotMaxProjectionRefs")
    if isinstance(max_refs, int) and max_refs > 100:
        add_issue(report, "WARN", "plugin_snapshot_refs", f"maxProjectionRefs is {max_refs}.", "Use hot tier or --plugin-snapshot-max-projection-refs 100 for realtime tests.")
    active_ms = numeric(snapshot.get("pluginSnapshotTotalActiveMillis"))
    if active_ms is not None and active_ms > 100:
        add_issue(report, "WARN", "plugin_snapshot_active_ms", f"Plugin snapshot active time is {active_ms:.1f} ms.", "Inspect pluginSnapshotBottleneck and keep plugin-snapshot experimental until hot tier stays under 100 ms.")
    if boolish(snapshot.get("livePacketWriterActive")) is True:
        add_issue(report, "FAIL", "live_packet_writer_active", "Retired live packet writer appears active.", "Restart RuneLite with the updated plugin; runtime packet archives cannot be enabled.")


def apply_snapshot_no_file_rules(report: dict, snapshot: dict) -> None:
    recording_mode = snapshot.get("recordingMode")
    if recording_mode and recording_mode != "LIVE_COMPACT_ONLY":
        add_issue(report, "WARN", "snapshot_no_file_recording_mode", f"Recording mode is {recording_mode}.", "Use LIVE_COMPACT_ONLY for snapshot no-file live.")
    for key, label in (
        ("rawTicksEnabled", "raw ticks"),
        ("rawEventsEnabled", "raw events"),
        ("framesEnabled", "frames"),
        ("screenshotsEnabled", "screenshots"),
        ("cropCaptureEnabled", "crop/perception capture"),
        ("perceptionCaptureEnabled", "perception capture"),
    ):
        if boolish(snapshot.get(key)) is True:
            add_issue(report, "WARN", f"snapshot_no_file_{key}", f"{label} is enabled.", f"Disable {label} for snapshot no-file daily mode.")
    if snapshot.get("compactStreamActive") or boolish(snapshot.get("compactStreamEnabled")) is True:
        add_issue(report, "WARN", "snapshot_no_file_compact_stream", "Compact stream is enabled or active in snapshot no-file mode.", "Disable compact stream; snapshot no-file uses the plugin snapshot endpoint.")
    health = snapshot.get("pluginSnapshotHealth") if isinstance(snapshot.get("pluginSnapshotHealth"), dict) else {}
    if health.get("status") != "PASS":
        add_issue(
            report,
            "FAIL",
            "snapshot_no_file_health",
            f"Plugin snapshot endpoint health is {health.get('status') or 'unavailable'}.",
            "Enable the plugin snapshot endpoint; live packet archive fallback has been retired.",
        )
    if snapshot.get("inputSourceActive") and snapshot.get("inputSourceActive") != "plugin-snapshot":
        add_issue(
            report,
            "FAIL",
            "snapshot_no_file_input_source",
            f"Input source is {snapshot.get('inputSourceActive')}.",
            "Start live_core_daemon with --daily-mode snapshot-no-files --input-source plugin-snapshot.",
        )
    candidate_count = numeric(snapshot.get("candidateCount"))
    if candidate_count is not None and candidate_count <= 0:
        add_issue(
            report,
            "WARN",
            "snapshot_no_file_no_candidates",
            "Plugin snapshot daemon currently has no candidates.",
            "Inspect WorldModel/Knowledge Fabric queries if snapshot no-file cannot build candidates in this area.",
        )
    active_ms = numeric(snapshot.get("activeMs")) or numeric(snapshot.get("pluginSnapshotTotalActiveMillis"))
    if active_ms is not None and active_ms > 100:
        add_issue(
            report,
            "WARN",
            "snapshot_no_file_active_ms",
            f"Snapshot no-file active time is {active_ms:.1f} ms.",
            "Inspect plugin snapshot/WorldModel timing until snapshot no-file stays under budget.",
        )
    if boolish(snapshot.get("compactPacketFilesRequired")) is True:
        add_issue(
            report,
            "FAIL",
            "snapshot_no_file_compact_required",
            "Retired live packet archive files are still marked required in snapshot no-file mode.",
            "Start live_core_daemon with --daily-mode snapshot-no-files --input-source plugin-snapshot.",
        )
    if boolish(snapshot.get("compactPacketFilesWriting")) is True:
        add_issue(
            report,
            "WARN",
            "snapshot_no_file_compact_packet_files",
            "Retired live packet archive files appear to be enabled or growing in snapshot no-file mode.",
            "Restart RuneLite/daemon with the updated code; the archive cannot be enabled.",
        )
    if boolish(snapshot.get("debugMirrorEnabled")) is True:
        add_issue(
            report,
            "WARN",
            "snapshot_no_file_debug_mirror",
            "Retired compact packet debug mirror is enabled in snapshot no-file mode.",
            "Restart RuneLite/daemon with the updated code; live packet file mirrors are retired.",
        )
    apply_plugin_snapshot_rules(report, snapshot, require_compact_fallback=False)


def evaluate_live_config(
    session: Path | None,
    *,
    mode: str = "daily",
    context_port: int = 8890,
    plugin_snapshot_host: str = "127.0.0.1",
    plugin_snapshot_port: int = 8893,
    check_context_service: bool = True,
    check_processes: bool = False,
    now: float | None = None,
) -> dict:
    mode = mode if mode in MODES else "daily"
    snapshot = collect_live_snapshot(
        session,
        context_port=context_port,
        plugin_snapshot_host=plugin_snapshot_host,
        plugin_snapshot_port=plugin_snapshot_port,
        check_context_service=check_context_service,
        check_plugin_snapshot=mode in {"daily", "plugin_snapshot_experimental", "snapshot_no_file", "visual_qa"},
        check_processes=check_processes,
        now=now,
    )
    report = {
        "schema": SCHEMA,
        "generatedAtUtc": utc_now(),
        "mode": mode,
        "sessionPath": snapshot.get("sessionPath"),
        "status": "PASS",
        "summary": {
            key: snapshot.get(key)
            for key in (
                "inputSourceActive",
                "liveCoreDaemonActive",
                "liveCoreWriteDebugLiveFiles",
                "liveCoreOverlayStateWritten",
                "dailyMode",
                "noFileDaily",
                "compactPacketFilesRequired",
                "compactPacketFilesWriting",
                "compactPacketFilesEnabledInManifest",
                "debugMirrorEnabled",
                "candidateCount",
                "activeMs",
                "recordingMode",
                "rawTicksEnabled",
                "rawEventsEnabled",
                "framesEnabled",
                "screenshotsEnabled",
                "cropCaptureEnabled",
                "perceptionCaptureEnabled",
                "compactPacketsAvailable",
                "compactPacketsRecent",
                "livePacketsRuntimeRemoved",
                "livePacketWriterActive",
                "legacyLivePacketFilesPresent",
                "legacyLivePacketFileCount",
                "legacyLivePacketTotalMb",
                "compactStreamEnabled",
                "compactStreamActive",
                "pluginSnapshotInputActive",
                "windowTicks",
                "candidateOutputWindow",
                "livenessMode",
                "overlayTargetCount",
                "overlayTargetLimit",
                "overlayGeometryMode",
                "collisionWindowStatus",
                "budgetExceeded",
                "writeFailures",
                "latestTick",
            )
        },
        "legacyLivePackets": {
            "runtimeRemoved": snapshot.get("livePacketsRuntimeRemoved"),
            "writerActive": snapshot.get("livePacketWriterActive"),
            "filesPresent": snapshot.get("legacyLivePacketFilesPresent"),
            "fileCount": snapshot.get("legacyLivePacketFileCount"),
            "totalMb": snapshot.get("legacyLivePacketTotalMb"),
            "ageSeconds": snapshot.get("compactPacketAgeSeconds"),
            "latestLegacySegment": snapshot.get("latestSegment"),
        },
        "stream": {
            "enabled": snapshot.get("compactStreamEnabled"),
            "active": snapshot.get("compactStreamActive"),
            "droppedPackets": snapshot.get("compactStreamDroppedPackets"),
            "missingRequiredTypes": snapshot.get("compactStreamMissingRequiredTypes"),
        },
        "pluginSnapshot": snapshot.get("pluginSnapshotHealth") or {},
        "contextService": snapshot.get("contextServiceHealth") or {},
        "processes": snapshot.get("processes") or {},
        "issues": [],
        "warnings": [],
        "failures": [],
        "fixSuggestions": [],
    }
    apply_common_rules(report, snapshot, mode)
    if snapshot.get("sessionExists"):
        if mode == "daily":
            apply_daily_rules(report, snapshot)
        elif mode == "snapshot_no_file":
            apply_snapshot_no_file_rules(report, snapshot)
        elif mode == "visual_qa":
            apply_visual_qa_rules(report, snapshot)
        elif mode == "debug_audit":
            apply_debug_audit_rules(report, snapshot)
        elif mode == "plugin_snapshot_experimental":
            apply_plugin_snapshot_rules(report, snapshot)
    preset_name, preset_suggestion = MODE_PRESET_RECOMMENDATIONS[mode]
    mismatch_reasons = [issue.get("code") for issue in report.get("issues", []) if issue.get("code")]
    report["presetRecommended"] = preset_name
    report["presetMismatchReasons"] = mismatch_reasons
    report["presetAppliedLikely"] = not mismatch_reasons
    if mismatch_reasons:
        report.setdefault("fixSuggestions", []).insert(0, preset_suggestion)
    report["status"] = derive_status(report)
    report["topWarnings"] = report.get("warnings", [])[:3]
    report["fixSuggestions"] = list(dict.fromkeys(report.get("fixSuggestions", [])))
    return report


def print_human(report: dict, *, fix_suggestions: bool = False) -> None:
    print(f"Live Config Doctor - {report.get('status')}")
    print(f"mode: {report.get('mode')}")
    print(f"session: {report.get('sessionPath')}")
    summary = report.get("summary") or {}
    print(
        "live: "
        f"input={summary.get('inputSourceActive') or 'unknown'} "
        f"daemon={summary.get('liveCoreDaemonActive')} "
        f"recording={summary.get('recordingMode') or 'unknown'} "
        f"rawTicks={summary.get('rawTicksEnabled')} rawEvents={summary.get('rawEventsEnabled')} frames={summary.get('framesEnabled')}"
    )
    print(
        "capture: "
        f"screenshots={summary.get('screenshotsEnabled')} "
        f"crops={summary.get('cropCaptureEnabled')} "
        f"perception={summary.get('perceptionCaptureEnabled')}"
    )
    legacy = report.get("legacyLivePackets") or {}
    print(
        "live packet archive: "
        f"retired={summary.get('livePacketsRuntimeRemoved')} writerActive={summary.get('livePacketWriterActive')} "
        f"legacyFiles={legacy.get('fileCount') or 0} legacyMb={legacy.get('totalMb') or 0}"
    )
    print(
        "processor: "
        f"windowTicks={summary.get('windowTicks')} "
        f"candidateOutputWindow={summary.get('candidateOutputWindow')} "
        f"livenessMode={summary.get('livenessMode')} "
        f"budgetExceeded={summary.get('budgetExceeded')} writeFailures={summary.get('writeFailures')}"
    )
    print(
        "overlay: "
        f"mode={summary.get('overlayMode') or 'unknown'} targets={summary.get('overlayTargetCount')} "
        f"intentMarkers={summary.get('intentMarkerCount')} limit={summary.get('overlayTargetLimit')} "
        f"geometry={summary.get('overlayGeometryMode') or 'unknown'} collision={summary.get('collisionWindowStatus')}"
    )
    plugin = report.get("pluginSnapshot") or {}
    if plugin.get("available"):
        print(f"plugin snapshot: health={plugin.get('status')} latestTick={plugin.get('latestTick')}")
    context = report.get("contextService") or {}
    if context.get("available"):
        print(f"context service: health={context.get('status')} latestTick={context.get('latestTick')}")
    if report.get("issues"):
        print("")
        print("Findings:")
        for issue in report.get("issues") or []:
            print(f"  {issue.get('severity')}: {issue.get('message')}")
    if fix_suggestions and report.get("fixSuggestions"):
        print("")
        print("Fix suggestions:")
        for suggestion in dict.fromkeys(report.get("fixSuggestions") or []):
            print(f"  - {suggestion}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only live workflow/config doctor.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--latest-session", action="store_true", help="Use newest telemetry session.")
    group.add_argument("--session", help="Session path to inspect.")
    parser.add_argument("--sessions-dir", help="Override sessions root.")
    parser.add_argument("--mode", default="daily", choices=MODES, help="Workflow preset to check.")
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    parser.add_argument("--fix-suggestions", action="store_true", help="Print copy/paste fix suggestions in human output.")
    parser.add_argument("--check-processes", action="store_true", help="Best-effort duplicate process check.")
    parser.add_argument("--context-port", type=int, default=8890)
    parser.add_argument("--plugin-snapshot-host", default="127.0.0.1")
    parser.add_argument("--plugin-snapshot-port", type=int, default=8893)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session = resolve_session(args)
    report = evaluate_live_config(
        session,
        mode=args.mode,
        context_port=args.context_port,
        plugin_snapshot_host=args.plugin_snapshot_host,
        plugin_snapshot_port=args.plugin_snapshot_port,
        check_processes=args.check_processes,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report, fix_suggestions=args.fix_suggestions)
    return 2 if report.get("status") == "FAIL" else 1 if report.get("status") == "WARN" else 0


if __name__ == "__main__":
    raise SystemExit(main())

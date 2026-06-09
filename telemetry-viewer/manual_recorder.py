from __future__ import annotations

import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import telemetry_capabilities
import telemetry_schema
import telemetry_sources


RECORDER_SCHEMA_VERSION = "manual_telemetry_recording.v1"
EVENT_SCHEMA_VERSION = "manual_telemetry_event.v1"


def utc_now() -> str:
    return telemetry_sources.utc_now()


def safe_label(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "recording").strip())
    text = text.strip("._-")
    return text[:80] or "recording"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def git_metadata() -> dict[str, Any]:
    root = repo_root()

    def run(args: list[str]) -> str | None:
        try:
            result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, timeout=5, check=False)
        except Exception:
            return None
        text = (result.stdout or result.stderr or "").strip()
        return text or None

    status = run(["status", "--short"])
    return {
        "branch": run(["branch", "--show-current"]),
        "commit": run(["rev-parse", "--short", "HEAD"]),
        "dirty": bool(status),
        "statusShort": status.splitlines()[:50] if status else [],
    }


def event_envelope(event_type: str, session_id: str, *, started_monotonic: float, **payload: Any) -> dict[str, Any]:
    now = time.monotonic()
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_type": event_type,
        "session_id": session_id,
        "wall_time_utc": utc_now(),
        "monotonic_time": now,
        "elapsed_seconds": round(now - started_monotonic, 6),
        **payload,
    }


class EventWriter:
    def __init__(self, path: Path, *, pretty: bool = False):
        self.path = path
        self.pretty = pretty
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a", encoding="utf-8")

    def write(self, event: dict[str, Any]) -> None:
        if self.pretty:
            line = json.dumps(event, sort_keys=False, default=str)
        else:
            line = json.dumps(event, separators=(",", ":"), sort_keys=False, default=str)
        self.handle.write(line + "\n")
        self.handle.flush()

    def close(self) -> None:
        self.handle.close()


def start_marker_reader(markers: "queue.Queue[str]") -> threading.Event:
    stop = threading.Event()

    def worker() -> None:
        while not stop.is_set():
            try:
                line = sys.stdin.readline()
            except Exception:
                return
            if line == "":
                return
            markers.put(line.rstrip("\r\n"))

    thread = threading.Thread(target=worker, name="manual-recorder-markers", daemon=True)
    thread.start()
    return stop


def duration_seconds_from_arg(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def stop_file_requested(path: str | Path | None) -> bool:
    if not path:
        return False
    try:
        return Path(path).expanduser().exists()
    except OSError:
        return False


def marker_file_state(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {"path": None, "offset": 0}
    marker_path = Path(path).expanduser()
    try:
        offset = marker_path.stat().st_size
    except OSError:
        offset = 0
    return {"path": marker_path, "offset": offset}


def poll_marker_file(state: dict[str, Any]) -> list[str]:
    marker_path = state.get("path")
    if not marker_path:
        return []
    path = Path(marker_path)
    try:
        size = path.stat().st_size
        if size < int(state.get("offset") or 0):
            state["offset"] = 0
        with path.open("r", encoding="utf-8") as handle:
            handle.seek(int(state.get("offset") or 0))
            text = handle.read()
            state["offset"] = handle.tell()
    except FileNotFoundError:
        return []
    except (PermissionError, OSError):
        return []
    return [line.rstrip("\r\n") for line in text.splitlines() if line.strip()]


def write_manifest(recording_dir: Path, manifest: dict[str, Any], *, pretty: bool) -> None:
    telemetry_sources.atomic_write_json(recording_dir / "manifest.json", manifest, pretty=pretty)


def source_snapshot_event(
    sources: dict[str, Path],
    changed_names: list[str],
    *,
    session_id: str,
    started_monotonic: float,
    max_bytes: int,
    include_raw: bool,
    plugin_snapshot_needs: list[str] | tuple[str, ...] | None = None,
    plugin_snapshot_url: str | None = None,
    plugin_snapshot_timeout_seconds: float = telemetry_sources.DEFAULT_PLUGIN_SNAPSHOT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    reads = telemetry_sources.read_sources(sources, max_bytes=max_bytes, include_raw=include_raw)
    endpoint_reads = telemetry_sources.read_plugin_snapshot_needs(
        plugin_snapshot_needs or [],
        snapshot_url=plugin_snapshot_url,
        timeout_seconds=plugin_snapshot_timeout_seconds,
        include_raw=include_raw,
    )
    reads.extend(endpoint_reads)
    payloads = telemetry_sources.parsed_payload_by_source(reads)
    normalized = telemetry_schema.normalized_telemetry(payloads)
    capabilities = telemetry_capabilities.capability_summary_from_reads(reads)
    changed_name_set = set(changed_names)
    changed_name_set.update(
        str(read.get("name"))
        for read in endpoint_reads
        if read.get("parse_status") in {"ok", "partial"}
    )
    changed_reads = [read for read in reads if read.get("name") in changed_name_set]
    def event_source(read: dict[str, Any]) -> dict[str, Any]:
        item = {
            key: read.get(key)
            for key in (
                "name",
                "path",
                "url",
                "source_kind",
                "method",
                "need",
                "exists",
                "size_bytes",
                "modified_utc",
                "age_seconds",
                "stale",
                "parse_status",
                "read_error",
                "http_status",
                "truncated",
                "record_count",
                "malformed_line_count",
                "warnings",
                "freshness",
                "latest_tick",
                "latest_export_sequence",
                "raw",
            )
            if key in read
        }
        if read.get("name") == "bank_ui" or read.get("source_kind") == "plugin_snapshot":
            item["data"] = read.get("data")
        return item

    return event_envelope(
        "source_snapshot",
        session_id,
        started_monotonic=started_monotonic,
        changed_sources=sorted(changed_name_set),
        sources=[event_source(read) for read in changed_reads],
        all_source_freshness=telemetry_sources.source_freshness_summary(reads),
        high_value_fields=normalized,
        field_presence=capabilities.get("field_scan"),
        missing_fields=capabilities.get("missing_fields"),
        available_fields=capabilities.get("available_fields"),
        schema_gap_categories=capabilities.get("gap_categories"),
        latest_tick=capabilities.get("latest_tick"),
        latest_export_sequence=capabilities.get("latest_export_sequence"),
        parse_warnings=capabilities.get("parse_warnings") or [],
    )


def plugin_snapshot_needs_from_args(args: argparse.Namespace) -> list[str]:
    needs: list[str] = []
    if getattr(args, "preserve_bank_ui", False):
        needs.append("bank_ui")
    if getattr(args, "preserve_combat_state", False):
        needs.append("combat_state")
    return needs


def run_analyzer(recording_dir: Path, *, pretty: bool) -> tuple[dict[str, Any], str]:
    import analyze_manual_recording

    summary = analyze_manual_recording.analyze_recording(recording_dir)
    report = analyze_manual_recording.render_schema_gap_report(summary)
    telemetry_sources.atomic_write_json(recording_dir / "summary.json", summary, pretty=pretty)
    (recording_dir / "schema_gap_report.md").write_text(report, encoding="utf-8")
    return summary, report


def effective_capture_mouse(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "capture_mouse", False) or (getattr(args, "capture_input", False) and not getattr(args, "capture_keyboard", False)))


def effective_input_backend(args: argparse.Namespace) -> str:
    backend = str(getattr(args, "input_backend", "auto") or "auto")
    if getattr(args, "prefer_polling_input", False) and backend == "auto":
        return "polling"
    return backend


def input_preflight_output_dir(label: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path.home() / ".osrs-telemetry" / "input_preflight" / f"{stamp}_{safe_label(label)}"


def run_input_preflight(args: argparse.Namespace, label: str) -> dict[str, Any] | None:
    if not getattr(args, "capture_input", False) or not getattr(args, "input_preflight", False):
        return None
    import input_trace_recorder

    result = input_trace_recorder.run_smoke_test(
        input_preflight_output_dir(label),
        backend=effective_input_backend(args),
        duration=float(getattr(args, "input_preflight_seconds", 5) or 5),
        sample_ms=int(getattr(args, "input_sample_ms", 10) or 10),
        mouse_move_min_px=int(getattr(args, "mouse_move_min_px", 2) or 2),
        capture_mouse=effective_capture_mouse(args),
        capture_keyboard=bool(getattr(args, "capture_keyboard", False)),
        capture_window_context=bool(getattr(args, "capture_window_context", False)),
        raw_input_device_attribution=bool(getattr(args, "raw_input_device_attribution", False)),
        json_output=False,
    )
    counts = result.get("eventCounts") if isinstance(result.get("eventCounts"), dict) else {}
    min_mouse = int(getattr(args, "input_min_mouse_events", 1) or 0)
    min_click = int(getattr(args, "input_min_click_events", 0) or 0)
    min_key = int(getattr(args, "input_min_key_events", 0) or 0)
    moves = int(counts.get("moves") or 0)
    clicks = int(counts.get("clicks") or 0) + int(counts.get("downs") or 0)
    keys = int(counts.get("key_downs") or 0)
    thresholds_met = moves >= min_mouse and clicks >= min_click and keys >= min_key
    result["requiredMinimums"] = {"mouseMoves": min_mouse, "clicksOrDowns": min_click, "keyDowns": min_key}
    result["thresholdsMet"] = thresholds_met
    result["success"] = bool(result.get("success") and thresholds_met)
    if not thresholds_met:
        result["reason"] = "input preflight did not meet configured minimum event counts"
    return result


def arduino_requested(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "arduino", False)
        or getattr(args, "arduino_auto_start", False)
        or getattr(args, "arduino_probe", False)
        or getattr(args, "arduino_live_mirror", False)
        or getattr(args, "arduino_required", False)
        or getattr(args, "arduino_record_events", False)
        or getattr(args, "arduino_calibrate", False)
        or str(getattr(args, "arduino_passthrough_mode", "off") or "off") != "off"
    )


def mirror_preflight_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "arduino_mirror_preflight", False) or str(getattr(args, "arduino_passthrough_mode", "off") or "off") == "mirror")


def run_arduino_mirror_preflight(args: argparse.Namespace) -> dict[str, Any] | None:
    if not mirror_preflight_requested(args):
        return None
    import arduino_mirror_verifier

    return arduino_mirror_verifier.preflight_unproven_payload(
        requested_mode=str(getattr(args, "arduino_passthrough_mode", "mirror") or "mirror"),
        port=getattr(args, "arduino_port", None),
        reason="mirror preflight could not be proven before recording with the current bridge; use analyzer correlation after recording",
    )


def run_arduino_probe(args: argparse.Namespace, recording_dir: Path) -> dict[str, Any] | None:
    if not getattr(args, "arduino_probe", False):
        return None
    import arduino_mirror_verifier

    move = tuple(getattr(args, "arduino_probe_move", None) or (12, 0))
    return arduino_mirror_verifier.run_probe(
        recording_dir,
        port=getattr(args, "arduino_port", None),
        baud=int(getattr(args, "arduino_baud", 115200) or 115200),
        move=(int(move[0]), int(move[1])),
        observe_ms=int(getattr(args, "arduino_probe_observe_ms", 500) or 500),
        quiet_window=bool(getattr(args, "mirror_quiet_probe", False)),
        pre_observe_ms=int(getattr(args, "pre_observe_ms", 250) or 250),
        post_observe_ms=int(getattr(args, "post_observe_ms", getattr(args, "arduino_probe_observe_ms", 500)) or getattr(args, "arduino_probe_observe_ms", 500) or 500),
        max_background_move_px=int(getattr(args, "max_background_move_px", 3) or 3),
        probe_min_observed_dxdy=int(getattr(args, "probe_min_observed_dxdy", 1) or 1),
        probe_max_error_px=float(getattr(args, "probe_max_error_px", getattr(args, "mirror_max_move_error_px", 100)) or 100),
    )


def _probe_verified(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("success") or payload.get("classification") in {"arduino_probe_verified", "arduino_probe_verified_clean", "arduino_probe_verified_noisy"})


def _mirror_verified(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("mirrorVerified") or payload.get("classification") == "arduino_mirror_verified")


def effective_mirror_arm_mode(args: argparse.Namespace) -> str:
    requested = str(getattr(args, "mirror_arm_mode", "") or "").strip()
    if getattr(args, "mirror_persist_until_stop", False) or getattr(args, "mirror_keep_armed_while_recording", False):
        return "recording_persistent"
    if requested in {"test_window", "recording_persistent", "manual"}:
        return requested
    return "recording_persistent" if getattr(args, "arduino_live_mirror", False) else "manual"


def preflight_required_arduino(args: argparse.Namespace) -> None:
    if not getattr(args, "arduino_required", False):
        return
    import arduino_input_bridge

    bridge = arduino_input_bridge.ArduinoInputBridge(
        None,
        port=getattr(args, "arduino_port", None),
        baud=int(getattr(args, "arduino_baud", 115200) or 115200),
        passthrough_mode=getattr(args, "arduino_passthrough_mode", "bridge") or "bridge",
    )
    try:
        status = bridge.start(require_available=True)
    finally:
        try:
            bridge.stop()
        except Exception:
            pass
    if not status.get("available"):
        raise RuntimeError("; ".join(status.get("warnings") or ["Arduino unavailable"]))


def record(args: argparse.Namespace) -> int:
    label = safe_label(args.label)
    duration_seconds = duration_seconds_from_arg(getattr(args, "duration", None))
    until_stopped = bool(getattr(args, "until_stopped", False) or duration_seconds is None)
    input_preflight = run_input_preflight(args, label)
    if input_preflight and not input_preflight.get("success") and getattr(args, "fail_if_input_preflight_fails", False):
        print(json.dumps({"status": "FAIL", "input_preflight": input_preflight}, indent=2, default=str), file=sys.stderr)
        return 3
    mirror_preflight = run_arduino_mirror_preflight(args)
    if (
        mirror_preflight
        and not mirror_preflight.get("mirrorVerified")
        and getattr(args, "require_arduino_mirror_verified", False)
        and not getattr(args, "arduino_probe", False)
    ):
        print(json.dumps({"status": "FAIL", "arduino_mirror_preflight": mirror_preflight}, indent=2, default=str), file=sys.stderr)
        return 4
    try:
        preflight_required_arduino(args)
    except Exception as error:  # noqa: BLE001
        print(f"Arduino is required but unavailable: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    recording_dir = Path(args.out_dir).expanduser() / f"{timestamp}_{label}"
    recording_dir.mkdir(parents=True, exist_ok=False)
    session_id = str(uuid.uuid4())
    stop_file = Path(args.stop_file).expanduser() if getattr(args, "stop_file", None) else None
    marker_path = Path(args.marker_file).expanduser() if getattr(args, "marker_file", None) else None
    if stop_file is not None:
        stop_file.parent.mkdir(parents=True, exist_ok=True)
    if marker_path is not None:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.touch(exist_ok=True)
    discovery = telemetry_sources.discover_sources(
        session=args.session,
        latest_session=args.latest_session,
        sources_override=args.sources,
        plugin_snapshot_needs=plugin_snapshot_needs_from_args(args),
        plugin_snapshot_url=args.plugin_snapshot_url,
    )
    sources = discovery["paths"]
    manifest = {
        "schema_version": RECORDER_SCHEMA_VERSION,
        "recording_id": recording_dir.name,
        "session_id": session_id,
        "label": args.label,
        "description": args.description or "",
        "created_at_utc": utc_now(),
        "duration_seconds": duration_seconds,
        "until_stopped": until_stopped,
        "stop_file": str(stop_file) if stop_file else None,
        "marker_file": str(marker_path) if marker_path else None,
        "poll_interval_ms": args.poll_interval_ms,
        "source_discovery": {key: value for key, value in discovery.items() if key != "paths"},
        "include_raw": bool(args.include_raw),
        "max_bytes": args.max_bytes,
        "repo": git_metadata(),
        "outputs": {
            "manifest": "manifest.json",
            "events": "events.jsonl",
            "summary": "summary.json",
            "schemaGapReport": "schema_gap_report.md",
            "inputEvents": "input_events.jsonl" if args.capture_input else None,
            "arduinoEvents": "arduino_events.jsonl" if arduino_requested(args) else None,
            "arduinoActionCommands": "arduino_action_commands.jsonl" if arduino_requested(args) else None,
            "arduinoLiveMirrorStatus": "arduino_live_mirror_status.json" if args.arduino_live_mirror else None,
            "arduinoLiveMirrorSummary": "arduino_live_mirror_summary.json" if args.arduino_live_mirror else None,
            "joinedInputTelemetry": "joined_input_telemetry.jsonl" if args.join_input_telemetry else None,
            "cameraBehavior": "camera_behavior_summary.json" if args.camera_behavior else None,
            "vmMouseArduinoMapping": "vm_mouse_arduino_mapping.json" if args.write_arduino_mapping or args.vm_mouse_mapping else None,
            "coordinateAlignment": "coordinate_alignment_summary.json" if args.join_input_telemetry else None,
            "inputPathIntegrity": "input_path_integrity_summary.json" if args.input_path_integrity or args.join_input_telemetry else None,
            "arduinoMirrorVerification": "arduino_mirror_verification.json" if mirror_preflight_requested(args) or args.join_input_telemetry else None,
        },
        "input_capture": {
            "enabled": bool(args.capture_input),
            "backend": args.input_backend,
            "effective_backend": effective_input_backend(args),
            "prefer_polling_input": bool(args.prefer_polling_input),
            "sample_ms": args.input_sample_ms,
            "mouse_move_min_px": args.mouse_move_min_px,
            "capture_mouse": effective_capture_mouse(args),
            "capture_keyboard": bool(args.capture_keyboard),
            "capture_window_context": bool(args.capture_window_context),
            "raw_input_device_attribution": bool(args.raw_input_device_attribution),
            "join_input_telemetry": bool(args.join_input_telemetry),
            "preflight": input_preflight,
        },
        "arduino": {
            "enabled": arduino_requested(args),
            "required": bool(args.arduino_required),
            "auto_start": bool(args.arduino_auto_start),
            "record_events": bool(args.arduino_record_events),
            "port": args.arduino_port,
            "baud": args.arduino_baud,
            "passthrough_mode": args.arduino_passthrough_mode,
            "calibration_profile": args.arduino_calibration,
            "probe_requested": bool(args.arduino_probe),
            "probe_move": list(args.arduino_probe_move or []),
            "probe_observe_ms": args.arduino_probe_observe_ms,
            "mirror_quiet_probe": bool(args.mirror_quiet_probe),
            "require_probe_verified": bool(args.require_arduino_probe_verified),
            "mirror_preflight": mirror_preflight,
            "require_mirror_verified": bool(args.require_arduino_mirror_verified),
            "input_path_integrity": bool(args.input_path_integrity),
            "mirror_correlation_window_ms": args.mirror_correlation_window_ms,
            "mirror_max_move_error_px": args.mirror_max_move_error_px,
            "live_mirror_requested": bool(args.arduino_live_mirror),
            "live_mirror": {
                "enabled": bool(args.arduino_live_mirror),
                "arm_mode": effective_mirror_arm_mode(args),
                "persist_until_stop": bool(args.mirror_persist_until_stop or effective_mirror_arm_mode(args) == "recording_persistent"),
                "keep_armed_while_recording": bool(args.mirror_keep_armed_while_recording or effective_mirror_arm_mode(args) == "recording_persistent"),
                "disarm_on_focus_lost": bool(args.mirror_disarm_on_focus_lost),
                "move_min_px": args.mirror_move_min_px,
                "max_step_px": args.mirror_max_step_px,
                "send_interval_ms": args.mirror_send_interval_ms,
                "scale_x": args.mirror_scale_x,
                "scale_y": args.mirror_scale_y,
                "invert_x": bool(args.mirror_invert_x),
                "invert_y": bool(args.mirror_invert_y),
                "button_mode": args.mirror_button_mode,
                "keys": bool(args.mirror_keys),
                "require_active": bool(args.require_live_mirror_active),
                "require_verified": bool(args.require_live_mirror_verified),
                "calibration": args.mirror_calibration,
            },
        },
        "telemetry_preflight": {
            "enabled": bool(args.telemetry_preflight),
            "seconds": args.telemetry_preflight_seconds,
            "max_telemetry_age_ms": args.max_telemetry_age_ms,
            "wait_for_fresh_telemetry": bool(args.wait_for_fresh_telemetry),
            "wait_timeout_seconds": args.wait_for_fresh_telemetry_timeout,
            "prefer_active_session": bool(args.prefer_active_session),
        },
        "menu_capture_burst": {
            "enabled": bool(args.menu_capture_burst),
            "burst_ms": args.menu_burst_ms,
            "poll_ms": args.menu_burst_poll_ms,
            "until_selection": bool(args.menu_burst_until_selection),
            "tail_ms": args.menu_burst_tail_ms,
            "max_ms": args.menu_burst_max_ms,
        },
        "plugin_snapshot": {
            "preserve_bank_ui": bool(args.preserve_bank_ui),
            "preserve_combat_state": bool(args.preserve_combat_state),
            "url": args.plugin_snapshot_url if plugin_snapshot_needs_from_args(args) else None,
            "timeout_seconds": args.plugin_snapshot_timeout,
            "needs": plugin_snapshot_needs_from_args(args),
        },
    }
    write_manifest(recording_dir, manifest, pretty=args.pretty)
    if input_preflight and not input_preflight.get("success"):
        manifest.setdefault("warnings", []).append("input preflight failed; recording continued because fail-if-input-preflight-fails was not set")
        write_manifest(recording_dir, manifest, pretty=args.pretty)

    marker_queue: queue.Queue[str] = queue.Queue()
    marker_stop = start_marker_reader(marker_queue) if args.interactive else None
    file_marker_state = marker_file_state(marker_path)
    signal_stop = threading.Event()
    signal_reason: dict[str, str | None] = {"value": None}

    def request_signal_stop(signum: int, _frame: Any) -> None:
        signal_reason["value"] = f"signal_{signum}"
        signal_stop.set()

    previous_handlers: list[tuple[Any, Any]] = []
    for signum in (getattr(signal, "SIGTERM", None),):
        if signum is None:
            continue
        try:
            previous_handlers.append((signum, signal.getsignal(signum)))
            signal.signal(signum, request_signal_stop)
        except (OSError, ValueError):
            pass

    started_monotonic = time.monotonic()
    stop_reason = "completed"
    signatures: dict[str, dict[str, Any]] = {}
    latest_telemetry_ref: dict[str, Any] = {"latest_tick": None, "latest_export_sequence": None}
    writer = EventWriter(recording_dir / "events.jsonl", pretty=args.pretty)
    input_recorder: Any | None = None
    arduino_bridge: Any | None = None
    arduino_probe_result: dict[str, Any] | None = None
    live_mirror: Any | None = None
    live_mirror_summary: dict[str, Any] | None = None

    if getattr(args, "arduino_probe", False):
        arduino_probe_result = run_arduino_probe(args, recording_dir)
        manifest.setdefault("arduino", {})["probe"] = arduino_probe_result
        write_manifest(recording_dir, manifest, pretty=args.pretty)
        probe_ok = bool(_probe_verified(arduino_probe_result))
        if getattr(args, "require_arduino_probe_verified", False) and not probe_ok:
            writer.write(
                event_envelope(
                    "arduino_probe_failed",
                    session_id,
                    started_monotonic=started_monotonic,
                    probe=arduino_probe_result,
                )
            )
            writer.close()
            telemetry_sources.atomic_write_json(
                recording_dir / "summary.json",
                {
                    "schema": "manual_recording_summary.v1",
                    "status": "FAIL",
                    "recording_id": recording_dir.name,
                    "arduino_probe": arduino_probe_result,
                    "warnings": ["Arduino probe was required but did not verify the command path"],
                },
                pretty=args.pretty,
            )
            print(json.dumps({"status": "FAIL", "arduino_probe": arduino_probe_result}, indent=2, default=str), file=sys.stderr)
            return 5
        if getattr(args, "require_arduino_mirror_verified", False) and not (probe_ok or _mirror_verified(arduino_probe_result)):
            writer.write(
                event_envelope(
                    "arduino_mirror_preflight_failed",
                    session_id,
                    started_monotonic=started_monotonic,
                    probe=arduino_probe_result,
                )
            )
            writer.close()
            telemetry_sources.atomic_write_json(
                recording_dir / "summary.json",
                {
                    "schema": "manual_recording_summary.v1",
                    "status": "FAIL",
                    "recording_id": recording_dir.name,
                    "arduino_probe": arduino_probe_result,
                    "warnings": ["Arduino mirror verification was required but no probe/action path was verified"],
                },
                pretty=args.pretty,
            )
            print(json.dumps({"status": "FAIL", "arduino_probe": arduino_probe_result}, indent=2, default=str), file=sys.stderr)
            return 4

    if args.arduino_live_mirror:
        try:
            import arduino_live_mirror

            live_mirror = arduino_live_mirror.ArduinoLiveMirror(
                recording_dir,
                recording_id=recording_dir.name,
                port=args.arduino_port,
                baud=args.arduino_baud,
                settings=arduino_live_mirror.LiveMirrorSettings.from_args(args),
                pretty=bool(args.pretty),
            )
            live_status = live_mirror.start(require_active=bool(args.require_live_mirror_active))
            manifest.setdefault("arduino", {})["live_mirror_status"] = live_status
            manifest.setdefault("arduino", {})["live_mirror_settings"] = live_mirror.settings.__dict__.copy()
            write_manifest(recording_dir, manifest, pretty=args.pretty)
        except Exception as error:  # noqa: BLE001
            manifest.setdefault("arduino", {})["live_mirror_startup_error"] = f"{type(error).__name__}: {error}"
            write_manifest(recording_dir, manifest, pretty=args.pretty)
            if args.require_live_mirror_active:
                writer.close()
                return 6

    if args.capture_input:
        try:
            import input_trace_recorder

            input_recorder = input_trace_recorder.InputTraceRecorder(
                recording_dir,
                session_id=session_id,
                recording_id=recording_dir.name,
                backend_name=effective_input_backend(args),
                sample_ms=args.input_sample_ms,
                mouse_move_min_px=args.mouse_move_min_px,
                capture_mouse=effective_capture_mouse(args),
                capture_keyboard=bool(args.capture_keyboard),
                capture_window_context=bool(args.capture_window_context),
                raw_input_device_attribution=bool(args.raw_input_device_attribution),
                include_raw=bool(args.input_debug or args.include_raw),
                pretty=bool(args.pretty),
                telemetry_provider=lambda: dict(latest_telemetry_ref),
                started_monotonic=started_monotonic,
            )
            if live_mirror is not None:
                input_recorder.add_event_listener(live_mirror.process_input_event)
            input_recorder.start()
            if live_mirror is not None:
                arm_mode = effective_mirror_arm_mode(args)
                live_mirror.arm(
                    delay_ms=args.mirror_arm_delay_ms,
                    duration_sec=args.mirror_test_duration_sec if arm_mode == "test_window" else 0,
                    mode=arm_mode,
                )
                manifest.setdefault("arduino", {})["live_mirror_armed"] = {
                    "arm_delay_ms": args.mirror_arm_delay_ms,
                    "arm_mode": arm_mode,
                    "test_duration_sec": args.mirror_test_duration_sec if arm_mode == "test_window" else 0,
                    "recording_persistent": arm_mode == "recording_persistent",
                    "persist_until_stop": bool(args.mirror_persist_until_stop or arm_mode == "recording_persistent"),
                    "keep_armed_while_recording": bool(args.mirror_keep_armed_while_recording or arm_mode == "recording_persistent"),
                    "armed_after_input_capture": True,
                }
                writer.write(
                    event_envelope(
                        "live_mirror_arm",
                        session_id,
                        started_monotonic=started_monotonic,
                        arm_mode=arm_mode,
                        arm_delay_ms=args.mirror_arm_delay_ms,
                        test_duration_sec=args.mirror_test_duration_sec if arm_mode == "test_window" else 0,
                        recording_persistent=arm_mode == "recording_persistent",
                    )
                )
                write_manifest(recording_dir, manifest, pretty=args.pretty)
        except Exception as error:  # noqa: BLE001
            manifest.setdefault("input_capture", {})["startup_error"] = f"{type(error).__name__}: {error}"
            write_manifest(recording_dir, manifest, pretty=args.pretty)

    if arduino_requested(args) and not args.arduino_live_mirror:
        try:
            import arduino_input_bridge

            mode = args.arduino_passthrough_mode
            if mode == "off" and (args.arduino or args.arduino_auto_start):
                mode = "bridge"
            arduino_bridge = arduino_input_bridge.ArduinoInputBridge(
                recording_dir,
                recording_id=recording_dir.name,
                port=args.arduino_port,
                baud=args.arduino_baud,
                passthrough_mode=mode,
                include_raw=bool(args.input_debug or args.include_raw),
                pretty=bool(args.pretty),
            )
            status = arduino_bridge.start(require_available=bool(args.arduino_required))
            manifest.setdefault("arduino", {})["startup_status"] = status
            if args.arduino_calibrate:
                calibration = arduino_bridge.calibrate()
                manifest.setdefault("arduino", {})["calibration"] = calibration
        except Exception as error:  # noqa: BLE001
            manifest.setdefault("arduino", {})["startup_error"] = f"{type(error).__name__}: {error}"
            write_manifest(recording_dir, manifest, pretty=args.pretty)
            if args.arduino_required:
                writer.close()
                return 2
        write_manifest(recording_dir, manifest, pretty=args.pretty)

    try:
        writer.write(
            event_envelope(
                "recording_start",
                session_id,
                started_monotonic=started_monotonic,
                label=args.label,
                description=args.description or "",
                recording_dir=str(recording_dir),
                source_discovery={key: value for key, value in discovery.items() if key != "paths"},
                repo=manifest["repo"],
            )
        )
        if args.menu_capture_burst:
            writer.write(
                event_envelope(
                    "menu_capture_burst_start",
                    session_id,
                    started_monotonic=started_monotonic,
                    burst_ms=args.menu_burst_ms,
                    poll_ms=args.menu_burst_poll_ms,
                    until_selection=bool(args.menu_burst_until_selection),
                    tail_ms=args.menu_burst_tail_ms,
                    max_ms=args.menu_burst_max_ms,
                    reason="recording_configured",
                )
            )
        while True:
            elapsed = time.monotonic() - started_monotonic
            if signal_stop.is_set():
                stop_reason = signal_reason.get("value") or "termination_signal"
                writer.write(
                    event_envelope(
                        "recording_stop_requested",
                        session_id,
                        started_monotonic=started_monotonic,
                        reason=stop_reason,
                    )
                )
                break
            if stop_file_requested(stop_file):
                stop_reason = "stop_file"
                writer.write(
                    event_envelope(
                        "recording_stop_requested",
                        session_id,
                        started_monotonic=started_monotonic,
                        reason=stop_reason,
                        stop_file=str(stop_file),
                    )
                )
                break
            if not until_stopped and duration_seconds is not None and elapsed >= duration_seconds:
                stop_reason = "duration_elapsed"
                break

            while True:
                try:
                    marker = marker_queue.get_nowait()
                except queue.Empty:
                    break
                if marker.strip().lower() == "q":
                    stop_reason = "interactive_q"
                    writer.write(
                        event_envelope(
                            "recording_stop_requested",
                            session_id,
                            started_monotonic=started_monotonic,
                            reason=stop_reason,
                        )
                    )
                    break
                writer.write(
                    event_envelope(
                        "manual_marker",
                        session_id,
                        started_monotonic=started_monotonic,
                        label=marker.strip() or None,
                    )
                )
            if stop_reason == "interactive_q":
                break

            for marker in poll_marker_file(file_marker_state):
                writer.write(
                    event_envelope(
                        "manual_marker",
                        session_id,
                        started_monotonic=started_monotonic,
                        label=marker.strip() or None,
                        marker_source="marker_file",
                    )
                )

            changed: list[str] = []
            for name, path in sources.items():
                signature = telemetry_sources.source_signature(path)
                previous = signatures.get(name)
                if previous is None or signature != previous:
                    changed.append(name)
                    signatures[name] = signature
            if changed:
                snapshot = source_snapshot_event(
                    sources,
                    changed,
                    session_id=session_id,
                    started_monotonic=started_monotonic,
                    max_bytes=args.max_bytes,
                    include_raw=args.include_raw,
                    plugin_snapshot_needs=plugin_snapshot_needs_from_args(args),
                    plugin_snapshot_url=args.plugin_snapshot_url,
                    plugin_snapshot_timeout_seconds=args.plugin_snapshot_timeout,
                )
                latest_telemetry_ref["latest_tick"] = snapshot.get("latest_tick")
                latest_telemetry_ref["latest_export_sequence"] = snapshot.get("latest_export_sequence")
                writer.write(snapshot)
                if live_mirror is not None:
                    try:
                        live_mirror.process_telemetry_snapshot(snapshot)
                    except Exception:
                        pass
            time.sleep(max(0.001, float(args.poll_interval_ms) / 1000.0))
    except KeyboardInterrupt:
        stop_reason = "keyboard_interrupt"
        writer.write(
            event_envelope(
                "recording_stop_requested",
                session_id,
                started_monotonic=started_monotonic,
                reason=stop_reason,
            )
        )
    finally:
        writer.write(
            event_envelope(
                "recording_stop",
                session_id,
                started_monotonic=started_monotonic,
                reason=stop_reason,
                duration_seconds=round(time.monotonic() - started_monotonic, 6),
            )
        )
        if args.menu_capture_burst:
            writer.write(
                event_envelope(
                    "menu_capture_burst_stop",
                    session_id,
                    started_monotonic=started_monotonic,
                    reason=stop_reason,
                )
            )
        for signum, handler in previous_handlers:
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                pass
        if marker_stop is not None:
            marker_stop.set()
        if input_recorder is not None:
            try:
                manifest.setdefault("input_capture", {})["summary"] = input_recorder.stop()
            except Exception as error:  # noqa: BLE001
                manifest.setdefault("input_capture", {})["stop_error"] = f"{type(error).__name__}: {error}"
        if live_mirror is not None:
            try:
                live_mirror_summary = live_mirror.stop()
                manifest.setdefault("arduino", {})["live_mirror_summary"] = live_mirror_summary
                writer.write(
                    event_envelope(
                        "live_mirror_disarm",
                        session_id,
                        started_monotonic=started_monotonic,
                        arm_mode=live_mirror_summary.get("armMode"),
                        disarm_reason=live_mirror_summary.get("disarmReason") or live_mirror_summary.get("pauseReason"),
                        panic_stop_count=live_mirror_summary.get("panicStopCount"),
                        actions_after_disarm_count=live_mirror_summary.get("actionsAfterDisarmCount"),
                        clicks_after_disarm_count=live_mirror_summary.get("clicksAfterDisarmCount"),
                    )
                )
                if int(live_mirror_summary.get("panicStopCount") or 0) > 0:
                    writer.write(
                        event_envelope(
                            "live_mirror_panic_stop",
                            session_id,
                            started_monotonic=started_monotonic,
                            disarm_reason=live_mirror_summary.get("disarmReason") or live_mirror_summary.get("pauseReason"),
                        )
                    )
            except Exception as error:  # noqa: BLE001
                manifest.setdefault("arduino", {})["live_mirror_stop_error"] = f"{type(error).__name__}: {error}"
        if arduino_bridge is not None:
            try:
                manifest.setdefault("arduino", {})["final_status"] = arduino_bridge.stop()
            except Exception as error:  # noqa: BLE001
                manifest.setdefault("arduino", {})["stop_error"] = f"{type(error).__name__}: {error}"
        writer.close()

    if args.join_input_telemetry or args.input_summary or args.camera_behavior or args.vm_mouse_mapping or args.write_arduino_mapping or args.arduino_trace or args.input_path_integrity:
        try:
            import input_trace_joiner

            input_trace_joiner.analyze_recording(recording_dir, write=True, include_mapping=bool(args.vm_mouse_mapping or args.write_arduino_mapping))
        except Exception as error:  # noqa: BLE001
            manifest.setdefault("post_analysis_warnings", []).append(f"input trace join failed: {type(error).__name__}: {error}")

    summary, _report = run_analyzer(recording_dir, pretty=args.pretty)
    if input_preflight:
        summary["input_preflight"] = input_preflight
        if not input_preflight.get("success"):
            summary.setdefault("warnings", []).append("input preflight failed; recording continued because fail-if-input-preflight-fails was not set")
        telemetry_sources.atomic_write_json(recording_dir / "summary.json", summary, pretty=args.pretty)
    if mirror_preflight:
        summary["arduino_mirror_preflight"] = mirror_preflight
        if not mirror_preflight.get("mirrorVerified"):
            summary.setdefault("warnings", []).append("Arduino mirror preflight was not verified")
        telemetry_sources.atomic_write_json(recording_dir / "summary.json", summary, pretty=args.pretty)
    if arduino_probe_result:
        summary["arduino_probe"] = arduino_probe_result
        if not _probe_verified(arduino_probe_result):
            summary.setdefault("warnings", []).append("Arduino probe did not verify the command path")
        telemetry_sources.atomic_write_json(recording_dir / "summary.json", summary, pretty=args.pretty)
    if live_mirror_summary:
        summary["arduino_live_mirror"] = live_mirror_summary
        if args.require_live_mirror_verified:
            input_path = summary.get("input_path_integrity_summary") if isinstance(summary.get("input_path_integrity_summary"), dict) else {}
            if not input_path.get("liveMirrorVerified"):
                summary.setdefault("warnings", []).append("Live Arduino mirror verification was required but was not proven by analyzer correlation")
        telemetry_sources.atomic_write_json(recording_dir / "summary.json", summary, pretty=args.pretty)
    manifest["completed_at_utc"] = utc_now()
    manifest["event_count"] = summary.get("event_count")
    manifest["snapshot_count"] = summary.get("snapshot_count")
    write_manifest(recording_dir, manifest, pretty=args.pretty)

    if args.summary:
        print(json.dumps(summary, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":"), default=str))
    else:
        print(str(recording_dir))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record manual RuneLite telemetry snapshots into an analyzable folder.")
    parser.add_argument("--label", required=True, help="Short action label for the recording folder.")
    parser.add_argument("--description", default="", help="Human-readable intent/context for the recording.")
    parser.add_argument("--duration", help="Seconds to record. Blank, missing, or 0 records until stopped.")
    parser.add_argument("--until-stopped", action="store_true", help="Record until a stop file, Ctrl+C, or termination signal requests shutdown.")
    parser.add_argument("--stop-file", help="Path whose existence requests a graceful recorder stop.")
    parser.add_argument("--marker-file", help="Optional file to poll for appended manual marker lines.")
    parser.add_argument("--out-dir", default="recordings", help="Output root. Default: recordings.")
    parser.add_argument("--poll-interval-ms", type=int, default=50, help="Source poll interval. Default: 50.")
    parser.add_argument("--session", help="Telemetry session path.")
    parser.add_argument("--latest-session", action="store_true", help="Use newest live telemetry session.")
    parser.add_argument("--prefer-active-session", action="store_true", help="Prefer the currently active/latest session when discovery can tell.")
    parser.add_argument("--interactive", action="store_true", help="Read manual markers from stdin. Enter adds a marker; q stops.")
    parser.add_argument("--max-bytes", type=int, default=telemetry_sources.DEFAULT_MAX_BYTES, help="Max bytes read per source snapshot.")
    parser.add_argument("--summary", action="store_true", help="Print final summary JSON.")
    parser.add_argument("--sources", help="Optional comma-separated source paths or name=path entries.")
    parser.add_argument("--include-raw", action="store_true", help="Include bounded raw source text in source snapshot events.")
    parser.add_argument("--preserve-bank-ui", action=argparse.BooleanOptionalAction, default=True, help="Preserve bank_ui from the plugin snapshot live cache when available. Default: enabled.")
    parser.add_argument("--preserve-combat-state", action=argparse.BooleanOptionalAction, default=True, help="Preserve combat_state from the plugin snapshot live cache when available. Default: enabled.")
    parser.add_argument("--plugin-snapshot-url", default=telemetry_sources.DEFAULT_PLUGIN_SNAPSHOT_URL, help="Plugin snapshot endpoint URL used for live-cache needs such as bank_ui and combat_state.")
    parser.add_argument("--plugin-snapshot-timeout", type=float, default=telemetry_sources.DEFAULT_PLUGIN_SNAPSHOT_TIMEOUT_SECONDS, help="Plugin snapshot read timeout seconds. Default: 0.2.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output files where practical.")
    parser.add_argument("--telemetry-preflight", action="store_true", help="Record telemetry freshness preflight metadata before recording.")
    parser.add_argument("--telemetry-preflight-seconds", type=float, default=5.0, help="Telemetry preflight seconds metadata.")
    parser.add_argument("--max-telemetry-age-ms", type=int, default=3000, help="Maximum acceptable telemetry age metadata.")
    parser.add_argument("--wait-for-fresh-telemetry", action="store_true", help="Wait for fresh telemetry before recording when supported.")
    parser.add_argument("--wait-for-fresh-telemetry-timeout", type=float, default=30.0, help="Fresh telemetry wait timeout metadata.")
    parser.add_argument("--menu-capture-burst", action="store_true", help="Use a short lower-latency menu capture burst when supported.")
    parser.add_argument("--menu-burst-ms", type=int, default=2000, help="Menu capture burst duration metadata.")
    parser.add_argument("--menu-burst-poll-ms", type=int, default=15, help="Menu capture burst poll interval metadata.")
    parser.add_argument("--menu-burst-until-selection", action="store_true", help="Keep menu burst metadata active until a menu selection is observed or timeout expires.")
    parser.add_argument("--menu-burst-tail-ms", type=int, default=500, help="Menu burst tail duration after selection metadata.")
    parser.add_argument("--menu-burst-max-ms", type=int, default=4000, help="Maximum menu burst duration metadata.")
    parser.add_argument("--capture-input", action="store_true", help="Capture OS-level mouse/keyboard events into input_events.jsonl.")
    parser.add_argument("--input-backend", choices=("auto", "windows_hook", "polling"), default="auto", help="Input capture backend. Default: auto.")
    parser.add_argument("--prefer-polling-input", action="store_true", help="Use polling when --input-backend is auto.")
    parser.add_argument("--input-preflight", action="store_true", help="Run a short input smoke test before the full recording.")
    parser.add_argument("--input-preflight-seconds", type=float, default=5.0, help="Seconds for the input preflight smoke test. Default: 5.")
    parser.add_argument("--input-min-mouse-events", type=int, default=1, help="Minimum mouse move events required by preflight. Default: 1.")
    parser.add_argument("--input-min-click-events", type=int, default=0, help="Minimum click/down events required by preflight. Default: 0.")
    parser.add_argument("--input-min-key-events", type=int, default=0, help="Minimum key down events required by preflight. Default: 0.")
    parser.add_argument("--fail-if-input-preflight-fails", action="store_true", help="Abort before creating the full recording if input preflight fails.")
    parser.add_argument("--input-sample-ms", type=int, default=10, help="Polling input sample interval. Default: 10.")
    parser.add_argument("--mouse-move-min-px", type=int, default=2, help="Minimum mouse movement delta to record in polling mode.")
    parser.add_argument("--capture-keyboard", action="store_true", help="Capture non-text keyboard events such as camera/navigation keys.")
    parser.add_argument("--capture-mouse", action="store_true", help="Capture mouse movement, buttons, clicks, drags, and wheel events.")
    parser.add_argument("--capture-window-context", action="store_true", help="Attach foreground window and client-region context to input events.")
    parser.add_argument("--raw-input-device-attribution", action="store_true", help="Request optional Windows Raw Input device attribution when available.")
    parser.add_argument("--join-input-telemetry", action="store_true", help="Join input_events.jsonl with telemetry snapshots after recording.")
    parser.add_argument("--input-summary", action="store_true", help="Write input_trace_summary.json after recording.")
    parser.add_argument("--input-debug", action="store_true", help="Include raw backend payloads in input/Arduino debug fields.")
    parser.add_argument("--camera-behavior", action="store_true", help="Analyze camera yaw/pitch behavior after recording.")
    parser.add_argument("--arduino", action="store_true", help="Enable Arduino bridge status/events for this recording.")
    parser.add_argument("--arduino-port", help="Arduino serial port, for example COM6.")
    parser.add_argument("--arduino-baud", type=int, default=115200, help="Arduino serial baud rate. Default: 115200.")
    parser.add_argument("--arduino-required", action="store_true", help="Fail before recording if Arduino is unavailable.")
    parser.add_argument("--arduino-auto-start", action="store_true", help="Initialize Arduino bridge when recording starts.")
    parser.add_argument("--arduino-record-events", action="store_true", help="Write arduino_events.jsonl when Arduino bridge is active.")
    parser.add_argument("--arduino-calibration", help="Optional Arduino calibration profile path.")
    parser.add_argument("--arduino-calibrate", action="store_true", help="Write a simple Arduino calibration sample at recording start.")
    parser.add_argument("--arduino-passthrough-mode", choices=("off", "label_only", "bridge", "mirror"), default="off", help="Arduino involvement mode.")
    parser.add_argument("--arduino-probe", action="store_true", help="Send a deliberate Arduino probe movement before the recording loop.")
    parser.add_argument("--arduino-probe-move", nargs=2, type=int, metavar=("DX", "DY"), default=(12, 0), help="Arduino probe movement delta. Default: 12 0.")
    parser.add_argument("--arduino-probe-observe-ms", type=int, default=500, help="Milliseconds to observe cursor movement after Arduino probe.")
    parser.add_argument("--mirror-quiet-probe", action="store_true", help="Use quiet-window cursor observation for Arduino probe classification.")
    parser.add_argument("--pre-observe-ms", type=int, default=250, help="Quiet probe pre-command observation window.")
    parser.add_argument("--post-observe-ms", type=int, default=None, help="Quiet probe post-command observation window. Defaults to --arduino-probe-observe-ms.")
    parser.add_argument("--max-background-move-px", type=int, default=3, help="Maximum pre-command cursor movement for a clean probe.")
    parser.add_argument("--probe-min-observed-dxdy", type=int, default=1, help="Minimum observed cursor delta for a move probe.")
    parser.add_argument("--probe-max-error-px", type=float, default=100.0, help="Maximum probe movement error for a clean probe.")
    parser.add_argument("--require-arduino-probe-verified", action="store_true", help="Abort if Arduino probe movement/click evidence is not verified.")
    parser.add_argument("--arduino-live-mirror", action="store_true", help="Convert captured OS input events into live Arduino action commands during recording.")
    parser.add_argument("--mirror-profile", choices=("observe_only", "click_only", "move_only", "full_live_mirror", "validation_menu_row"), default="full_live_mirror", help="Live mirror operation profile. Default: full_live_mirror.")
    parser.add_argument("--mirror-click-policy", choices=("off", "map_only", "live_unsuppressed", "live_requires_source_suppression", "arduino_source_only"), default="live_unsuppressed", help="How live mirror handles click source ownership. validation_menu_row defaults to map_only unless live unsuppressed clicks are explicitly allowed.")
    parser.add_argument("--require-click-source-suppression", action="store_true", help="Downgrade live clicks unless source suppression or Arduino-owned input is verified.")
    parser.add_argument("--allow-unsuppressed-live-clicks", action="store_true", help="Allow live Arduino clicks sourced from normal OS clicks; this can create duplicate clicks.")
    parser.add_argument("--max-live-clicks-per-recording", type=int, default=0, help="Maximum live Arduino CLICK commands for the recording; 0 means unlimited.")
    parser.add_argument("--auto-disable-live-clicks-after-first-game-action", action="store_true", help="After the first mirrored game-action click, downgrade later live clicks to mapping-only.")
    parser.add_argument("--mirror-disable-movement", action="store_true", help="Do not send live Arduino MOVE commands.")
    parser.add_argument("--mirror-disable-clicks", action="store_true", help="Do not send live Arduino click/button commands.")
    parser.add_argument("--mirror-echo-suppression", action="store_true", help="Suppress OS polling events that match recent Arduino command output.")
    parser.add_argument("--mirror-echo-window-ms", type=int, default=250, help="MOVE echo suppression window. Default: 250.")
    parser.add_argument("--mirror-click-echo-window-ms", type=int, default=300, help="Click echo suppression window. Default: 300.")
    parser.add_argument("--mirror-echo-max-error-px", type=int, default=100, help="Maximum movement error for echo matching. Default: 100.")
    parser.add_argument("--mirror-max-queue-size", type=int, default=25, help="Maximum pending mirror input/echo queue size. Default: 25.")
    parser.add_argument("--mirror-drop-move-older-than-ms", type=int, default=150, help="Drop queued MOVE source events older than this. Default: 150.")
    parser.add_argument("--mirror-clear-queue-on-game-action", action="store_true", help="Clear pending mirror input after a mirrored game-action click.")
    parser.add_argument("--mirror-clear-queue-on-menu-selection", action="store_true", help="Clear pending mirror input after a mirrored menu-selection click.")
    parser.add_argument("--mirror-clear-queue-on-plane-change", action="store_true", help="Clear pending mirror input after a detected plane change.")
    parser.add_argument("--mirror-clear-queue-on-target-action", action="store_true", help="Clear pending mirror input after a target-action signal.")
    parser.add_argument("--mirror-auto-pause-after-first-game-action", action="store_true", help="Auto-pause mirror after the first mirrored left-click game action.")
    parser.add_argument("--mirror-auto-pause-after-menu-selection", action="store_true", help="Auto-pause mirror after a mirrored menu-selection click.")
    parser.add_argument("--mirror-auto-pause-after-plane-change", action="store_true", help="Auto-pause mirror after a detected plane change.")
    parser.add_argument("--mirror-auto-pause-after-target-quality", choices=("strong", "medium", "weak", "off"), default="off", help="Target quality threshold for analyzer diagnostics/validation mode.")
    parser.add_argument("--mirror-validation-mode", choices=("menu_row", "route", "woodcutting", "custom"), default="custom", help="Validation mode hint for live mirror safety behavior.")
    parser.add_argument("--mirror-move-min-px", type=int, default=1, help="Minimum movement delta mirrored to Arduino.")
    parser.add_argument("--mirror-max-step-px", type=int, default=25, help="Maximum Arduino MOVE step before chunking.")
    parser.add_argument("--mirror-send-interval-ms", type=int, default=5, help="Delay between mirrored Arduino command chunks.")
    parser.add_argument("--mirror-scale-x", type=float, default=1.0, help="Scale factor for mirrored X movement.")
    parser.add_argument("--mirror-scale-y", type=float, default=1.0, help="Scale factor for mirrored Y movement.")
    parser.add_argument("--mirror-invert-x", action="store_true", help="Invert mirrored X movement.")
    parser.add_argument("--mirror-invert-y", action="store_true", help="Invert mirrored Y movement.")
    parser.add_argument("--mirror-button-mode", choices=("click", "down_up"), default="click", help="Mirror clicks as CLICK or MOUSE_DOWN/MOUSE_UP.")
    parser.add_argument("--mirror-keys", action="store_true", help="Mirror selected key events through Arduino.")
    parser.add_argument("--mirror-max-clicks-per-second", type=int, default=4, help="Maximum mirrored CLICK commands per second. Default: 4.")
    parser.add_argument("--mirror-max-button-commands-per-second", type=int, default=8, help="Maximum mirrored button commands per second. Default: 8.")
    parser.add_argument("--mirror-max-move-commands-per-second", type=int, default=120, help="Maximum mirrored MOVE commands per second. Default: 120.")
    parser.add_argument("--mirror-max-total-commands-per-second", type=int, default=150, help="Maximum total mirrored Arduino commands per second. Default: 150.")
    parser.add_argument("--mirror-click-cooldown-ms", type=int, default=120, help="Cooldown between mirrored CLICK commands for the same button. Default: 120.")
    parser.add_argument("--mirror-same-button-cooldown-ms", type=int, default=80, help="Cooldown between any mirrored button commands for the same button. Default: 80.")
    parser.add_argument("--mirror-max-burst-commands", type=int, default=50, help="Maximum mirrored commands in the panic window before throttling. Default: 50.")
    parser.add_argument("--mirror-panic-command-threshold", type=int, default=100, help="Command count in the panic window that stops mirroring. Default: 100.")
    parser.add_argument("--mirror-panic-window-ms", type=int, default=1000, help="Panic/throttle window in milliseconds. Default: 1000.")
    parser.add_argument("--mirror-arm-delay-ms", type=int, default=500, help="Delay after input capture starts before the mirror arms. Default: 500.")
    parser.add_argument("--mirror-arm-mode", choices=("test_window", "recording_persistent", "manual"), default=None, help="Live mirror arming behavior. Recording runs default to recording_persistent.")
    parser.add_argument("--mirror-persist-until-stop", action="store_true", help="Keep live mirror armed until recording stop/panic/cleanup.")
    parser.add_argument("--mirror-keep-armed-while-recording", action="store_true", help="Alias for persistent live mirror behavior during manual recordings.")
    parser.add_argument("--mirror-test-duration-sec", type=float, default=0.0, help="Auto-disarm window used only in --mirror-arm-mode test_window.")
    parser.add_argument("--mirror-disarm-on-focus-lost", action="store_true", help="Disarm instead of merely dropping events when the allowed foreground window loses focus.")
    parser.add_argument("--mirror-panic-stop-file", help="Path whose existence immediately panic-stops the live mirror.")
    parser.add_argument("--mirror-arm-only-when-runelite-focused", action="store_true", help="Mirror only while the foreground title matches the allow pattern.")
    parser.add_argument("--mirror-allow-ui-events", action="store_true", help="Allow mirroring telemetry UI events. Off by default.")
    parser.add_argument("--mirror-window-title-allow", default="RuneLite", help="Foreground window title substring allowed for guarded mirror input.")
    parser.add_argument("--mirror-exclude-window-title", default="OSRS Telemetry Control", help="Foreground window title substring excluded from mirror input.")
    parser.add_argument("--mirror-region", choices=("viewport", "client", "any"), default="client", help="Allowed source region for mirror input. Default: client.")
    parser.add_argument("--mirror-ignore-ui-clicks", action="store_true", default=True, help="Drop UI/control click regions before mirroring.")
    parser.add_argument("--require-live-mirror-active", action="store_true", help="Abort if the live Arduino mirror stream cannot start.")
    parser.add_argument("--require-live-mirror-verified", action="store_true", help="Mark recording warnings if live mirror commands do not correlate after analysis.")
    parser.add_argument("--mirror-calibration", help="Optional mirror calibration profile path.")
    parser.add_argument("--arduino-mirror-preflight", action="store_true", help="Try to verify Arduino mirror mode before/during recording.")
    parser.add_argument("--require-arduino-mirror-verified", action="store_true", help="Abort if Arduino mirror mode cannot be verified.")
    parser.add_argument("--input-path-integrity", action="store_true", help="Write input path integrity and Arduino mirror verification summaries.")
    parser.add_argument("--mirror-correlation-window-ms", type=int, default=250, help="Window for correlating Arduino commands to observed input.")
    parser.add_argument("--mirror-max-move-error-px", type=float, default=5.0, help="Maximum movement error for mirror correlation.")
    parser.add_argument("--arduino-trace", action="store_true", help="Summarize arduino_events.jsonl after recording.")
    parser.add_argument("--vm-mouse-mapping", action="store_true", help="Build VM mouse to Arduino relative movement mapping.")
    parser.add_argument("--write-arduino-mapping", action="store_true", help="Write vm_mouse_arduino_mapping.json after recording.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return record(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())

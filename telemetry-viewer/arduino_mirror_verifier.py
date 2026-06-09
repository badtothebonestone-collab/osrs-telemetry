from __future__ import annotations

import json
import math
import os
import argparse
import ctypes
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import arduino_input_bridge
import arduino_live_mirror


MIRROR_VERIFICATION_SCHEMA = "arduino_mirror_verification.v1"
INPUT_PATH_INTEGRITY_SCHEMA = "input_path_integrity_summary.v1"

INPUT_PATH_CLASSIFICATIONS = {
    "os_polling_only",
    "arduino_status_only",
    "arduino_bridge_connected",
    "arduino_probe_verified",
    "arduino_probe_verified_clean",
    "arduino_probe_verified_noisy",
    "arduino_probe_verified_uncorrelated",
    "arduino_probe_sent_no_observed_delta",
    "arduino_probe_unsupported",
    "arduino_probe_port_error",
    "arduino_mirror_requested",
    "arduino_mirror_active",
    "arduino_mirror_verified",
    "arduino_mirror_failed",
    "conversion_trace_only",
    "mixed_input_path",
    "live_mirror_click_storm",
    "live_mirror_rate_limited",
    "live_mirror_panic_stopped",
    "live_mirror_feedback_suspected",
    "live_mirror_ui_click_loop_suspected",
    "unknown_input_path",
    "unsupported_protocol",
    "no_observed_cursor_delta",
    "no_ack",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _event_time(event: dict[str, Any]) -> float | None:
    return _float(event.get("elapsed_seconds")) or _float(event.get("sourceInputElapsedSeconds")) or _float(event.get("monotonic_time"))


def _command_name(event: dict[str, Any]) -> str:
    trace = event.get("commandTrace") if isinstance(event.get("commandTrace"), dict) else {}
    return str(event.get("command") or event.get("commandSent") or event.get("commandName") or trace.get("commandName") or "").upper()


def _movement_delta(event: dict[str, Any]) -> tuple[float, float]:
    payload = _dict(event.get("payload"))
    return (
        float(event.get("dx") or event.get("commanded_dx") or payload.get("dx") or 0.0),
        float(event.get("dy") or event.get("commanded_dy") or payload.get("dy") or 0.0),
    )


def _observed_delta(event: dict[str, Any]) -> tuple[float, float]:
    return (float(event.get("dx") or event.get("observed_dx") or 0.0), float(event.get("dy") or event.get("observed_dy") or 0.0))


def _is_observed_movement(event: dict[str, Any]) -> bool:
    return str(event.get("kind") or "") in {"mouse_move", "drag_move"}


def _is_observed_click(event: dict[str, Any]) -> bool:
    return str(event.get("kind") or "") in {"mouse_down", "mouse_up", "click", "double_click"}


def _correlate_commands_to_input(
    arduino_events: list[dict[str, Any]],
    input_events: list[dict[str, Any]],
    *,
    window_ms: int = 250,
    max_move_error_px: float = 5.0,
) -> dict[str, Any]:
    window_seconds = max(0.001, float(window_ms or 250) / 1000.0)
    move_commands = [event for event in arduino_events if _command_name(event) in arduino_input_bridge.ARDUINO_MOVEMENT_COMMANDS]
    click_commands = [event for event in arduino_events if _command_name(event) in arduino_input_bridge.ARDUINO_CLICK_COMMANDS]
    movements = [event for event in input_events if _is_observed_movement(event)]
    clicks = [event for event in input_events if _is_observed_click(event)]
    movement_matches: list[dict[str, Any]] = []
    click_matches: list[dict[str, Any]] = []

    for command in move_commands:
        command_time = _event_time(command)
        if command_time is None:
            continue
        source_seq = command.get("sourceInputEventSeq")
        nearby = [
            event
            for event in movements
            if _event_time(event) is not None
            and event.get("event_seq") != source_seq
            and 0 <= float(_event_time(event) or 0) - command_time <= window_seconds
        ]
        if not nearby:
            continue
        observed_dx = sum(_observed_delta(event)[0] for event in nearby)
        observed_dy = sum(_observed_delta(event)[1] for event in nearby)
        commanded_dx, commanded_dy = _movement_delta(command)
        error_dx = observed_dx - commanded_dx
        error_dy = observed_dy - commanded_dy
        distance_error = math.hypot(error_dx, error_dy)
        movement_matches.append(
            {
                "commandEventSeq": command.get("event_seq"),
                "observedEventSeqs": [event.get("event_seq") for event in nearby[:8]],
                "latencyMs": round((float(_event_time(nearby[0]) or 0) - command_time) * 1000.0, 3),
                "commandedDx": commanded_dx,
                "commandedDy": commanded_dy,
                "observedDx": round(observed_dx, 3),
                "observedDy": round(observed_dy, 3),
                "errorDx": round(error_dx, 3),
                "errorDy": round(error_dy, 3),
                "distanceErrorPx": round(distance_error, 3),
                "withinErrorBudget": distance_error <= float(max_move_error_px),
            }
        )

    for command in click_commands:
        command_time = _event_time(command)
        if command_time is None:
            continue
        source_seq = command.get("sourceInputEventSeq")
        nearby = [
            event
            for event in clicks
            if _event_time(event) is not None
            and event.get("event_seq") != source_seq
            and 0 <= float(_event_time(event) or 0) - command_time <= window_seconds
        ]
        if nearby:
            click_matches.append(
                {
                    "commandEventSeq": command.get("event_seq"),
                    "observedEventSeqs": [event.get("event_seq") for event in nearby[:8]],
                    "latencyMs": round((float(_event_time(nearby[0]) or 0) - command_time) * 1000.0, 3),
                }
            )

    latencies = [item.get("latencyMs") for item in movement_matches + click_matches if isinstance(item.get("latencyMs"), (int, float))]
    errors = [item.get("distanceErrorPx") for item in movement_matches if isinstance(item.get("distanceErrorPx"), (int, float))]
    return {
        "movementCommandCount": len(move_commands),
        "clickCommandCount": len(click_commands),
        "observedCursorMovementCount": len(movements),
        "observedClickCount": len(clicks),
        "correlatedMovementCount": len(movement_matches),
        "correlatedClickCount": len(click_matches),
        "movementMatches": movement_matches[:10],
        "clickMatches": click_matches[:10],
        "latencyMs": {
            "min": min(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
            "average": round(sum(latencies) / len(latencies), 3) if latencies else None,
        },
        "conversionErrorPx": {
            "max": max(errors) if errors else None,
            "average": round(sum(errors) / len(errors), 3) if errors else None,
        },
    }


def requested_mode_from_manifest(manifest: dict[str, Any] | None) -> str:
    arduino = _dict(_dict(manifest).get("arduino"))
    return str(arduino.get("passthrough_mode") or arduino.get("passthroughMode") or "off")


def build_input_path_integrity(
    input_events: list[dict[str, Any]],
    arduino_events: list[dict[str, Any]] | None = None,
    *,
    manifest: dict[str, Any] | None = None,
    arduino_summary: dict[str, Any] | None = None,
    requested_mode: str | None = None,
    correlation_window_ms: int = 250,
    max_move_error_px: float = 5.0,
) -> dict[str, Any]:
    arduino_events = list(arduino_events or [])
    arduino_summary = dict(arduino_summary or arduino_input_bridge.summarize_arduino_events(arduino_events))
    requested = str(requested_mode or requested_mode_from_manifest(manifest) or "off")
    requested_mirror = requested == "mirror"
    correlation = _correlate_commands_to_input(
        arduino_events,
        input_events,
        window_ms=correlation_window_ms,
        max_move_error_px=max_move_error_px,
    )
    command_count = int(arduino_summary.get("commandCount") or 0)
    action_count = int(arduino_summary.get("actionCommandCount") or 0)
    live_action_events = [
        event
        for event in arduino_events
        if event.get("kind") != "command_dropped"
        and _command_name(event) in arduino_input_bridge.ARDUINO_ACTION_COMMANDS
        and not event.get("probeCommand")
    ]
    non_probe_action_count = len(live_action_events)
    movement_count = int(arduino_summary.get("movementCommandCount") or 0)
    click_count = int(arduino_summary.get("clickCommandCount") or 0)
    ack_count = int(arduino_summary.get("ackCount") or 0)
    observed_input = any(event.get("kind") in {"mouse_move", "drag_move", "mouse_down", "mouse_up", "click", "double_click", "key_down", "key_up"} for event in input_events)
    connected = bool(arduino_events and arduino_summary.get("classification") != "arduino_unavailable")
    warnings: list[str] = []
    safety = arduino_live_mirror.command_safety_diagnostics(arduino_events)
    safety_classes = set(safety.get("liveMirrorSafetyClassifications") or [])

    probe_verified = any(event.get("probeVerified") is True for event in arduino_events)
    probe_classifications = [
        str(event.get("probeClassification") or event.get("probeResult") or "")
        for event in arduino_events
        if event.get("probeCommand")
    ]
    probe_clean = any(value == "arduino_probe_verified_clean" or value == "verified_clean" for value in probe_classifications)
    probe_noisy = any(value in {"arduino_probe_verified_noisy", "arduino_probe_verified_uncorrelated"} for value in probe_classifications)
    if probe_clean:
        probe_classification = "arduino_probe_verified_clean"
    elif probe_noisy or probe_verified:
        probe_classification = "arduino_probe_verified_noisy" if probe_noisy else "arduino_probe_verified"
    else:
        probe_classification = None
    if requested_mirror:
        if non_probe_action_count and (correlation["correlatedMovementCount"] or correlation["correlatedClickCount"]):
            classification = "arduino_mirror_verified"
        elif probe_classification:
            classification = probe_classification
            warnings.append("Arduino probe command path was verified, but no recording action stream was correlated.")
        elif non_probe_action_count:
            classification = "arduino_mirror_active"
            warnings.append("mirror action commands were seen but were not correlated to observed OS input")
        elif connected:
            classification = "arduino_mirror_failed"
            warnings.append("Mirror requested but not proven. Recording contains Arduino status/connection evidence but no per-action mirror command stream.")
        else:
            classification = "arduino_mirror_failed"
            warnings.append("Mirror requested but Arduino bridge evidence is missing.")
    elif connected and action_count:
        classification = "arduino_bridge_connected"
    elif connected and command_count:
        classification = "arduino_status_only"
        warnings.append("Arduino status/health commands were captured, but no action-path commands were captured.")
    elif connected:
        classification = "arduino_bridge_connected"
        warnings.append("Arduino bridge connected, but no command stream was captured.")
    elif observed_input:
        classification = "os_polling_only"
    else:
        classification = "unknown_input_path"
        warnings.append("No OS input or Arduino action-path evidence was available.")

    if requested_mirror and observed_input and not non_probe_action_count:
        warnings.append("Observed OS polling input exists without matching Arduino mirror commands.")
    if requested_mirror and non_probe_action_count and observed_input and not (correlation["correlatedMovementCount"] or correlation["correlatedClickCount"]):
        classification = "mixed_input_path"
        warnings.append("Mirror mode has both Arduino action commands and uncorrelated OS input.")
    if requested_mirror and "live_mirror_click_storm" in safety_classes:
        classification = "live_mirror_click_storm"
        warnings.append("Live mirror generated an unsafe click storm; repeated click commands should not be treated as verified gameplay input.")
    elif requested_mirror and "live_mirror_panic_stopped" in safety_classes:
        classification = "live_mirror_panic_stopped"
        warnings.append("Live mirror panic-stopped after exceeding its safety threshold.")
    elif requested_mirror and "live_mirror_rate_limited" in safety_classes and non_probe_action_count:
        warnings.append("Live mirror commands were rate limited by safety controls.")

    return {
        "schema": INPUT_PATH_INTEGRITY_SCHEMA,
        "status": "PASS" if classification == "arduino_mirror_verified" or (classification == "arduino_probe_verified_clean" and not requested_mirror) or (not requested_mirror and observed_input) else "WARN",
        "generated_at_utc": utc_now(),
        "inputPathClassification": classification if classification in INPUT_PATH_CLASSIFICATIONS else "unknown_input_path",
        "requestedMode": requested,
        "requestedMirror": requested_mirror,
        "actualDetectedMode": "mirror" if classification.startswith("arduino_mirror") or classification.startswith("arduino_probe_verified") or classification.startswith("live_mirror") else ("bridge" if connected else "os_polling"),
        "mirrorVerificationStatus": "verified" if classification == "arduino_mirror_verified" else (classification if classification.startswith("arduino_probe_verified") or classification.startswith("live_mirror") else ("failed" if requested_mirror else "not_requested")),
        "arduinoPort": arduino_summary.get("port"),
        "arduinoProtocol": arduino_summary.get("protocol"),
        "arduinoConnected": connected,
        "mirrorSupported": "unknown",
        "probeVerified": classification in {"arduino_probe_verified", "arduino_probe_verified_clean", "arduino_probe_verified_noisy", "arduino_probe_verified_uncorrelated"},
        "probeVerifiedClean": classification == "arduino_probe_verified_clean",
        "probeClassification": probe_classification,
        "mirrorActive": classification in {"arduino_mirror_active", "arduino_mirror_verified", "mixed_input_path", "live_mirror_click_storm", "live_mirror_rate_limited", "live_mirror_panic_stopped"},
        "mirrorVerified": classification == "arduino_mirror_verified",
        "liveMirrorActive": bool(non_probe_action_count),
        "liveMirrorVerified": classification == "arduino_mirror_verified",
        "mirrorQuality": "verified" if classification == "arduino_mirror_verified" else ("unsafe_click_storm" if classification == "live_mirror_click_storm" else "active_unverified" if non_probe_action_count else ("probe_only" if probe_classification else "failed" if requested_mirror else "not_requested")),
        "nonProbeActionCommandCount": non_probe_action_count,
        "commandCount": command_count,
        "movementCommandCount": movement_count,
        "clickCommandCount": click_count,
        "keyboardCommandCount": sum(1 for event in arduino_events if _command_name(event) in {"KEY_DOWN", "KEY_UP", "KEY_PRESS", "HOLD_KEYS"}),
        "ackCount": ack_count,
        "observedCursorMovementCount": correlation["observedCursorMovementCount"],
        "observedClickCount": correlation["observedClickCount"],
        "correlatedCommandToObservedMovementCount": correlation["correlatedMovementCount"],
        "correlatedCommandToObservedClickCount": correlation["correlatedClickCount"],
        "correlationLatencyMs": correlation["latencyMs"],
        "conversionErrorPx": correlation["conversionErrorPx"],
        "possibleDoubleInput": bool(requested_mirror and non_probe_action_count and observed_input and not (correlation["correlatedMovementCount"] or correlation["correlatedClickCount"])),
        "uncorrelatedOsInputEventCount": max(0, len([event for event in input_events if event.get("kind") in {"mouse_move", "drag_move", "click", "double_click"}]) - correlation["correlatedMovementCount"] - correlation["correlatedClickCount"]),
        "uncorrelatedOsMoveCount": max(0, len([event for event in input_events if event.get("kind") in {"mouse_move", "drag_move"}]) - correlation["correlatedMovementCount"]),
        "uncorrelatedOsClickCount": max(0, len([event for event in input_events if event.get("kind") in {"click", "double_click"}]) - correlation["correlatedClickCount"]),
        "uncorrelatedArduinoCommandCount": max(0, action_count - correlation["correlatedMovementCount"] - correlation["correlatedClickCount"]),
        "maxArduinoCommandsPerSecond": safety.get("maxCommandsPerSecondObserved"),
        "maxClickCommandsPerSecond": safety.get("maxClickCommandsPerSecondObserved"),
        "repeatedClickSourceCount": safety.get("repeatedClickSourceCount"),
        "duplicateSourceEventCount": safety.get("duplicateSourceEventCount"),
        "throttledCommandCount": safety.get("throttledCommandCount"),
        "droppedCommandCount": safety.get("droppedCommandCount"),
        "panicStopCount": safety.get("panicStopCount"),
        "droppedCommandsByReason": safety.get("droppedCommandsByReason") or {},
        "liveMirrorSafetyClassifications": sorted(safety_classes),
        "rawInputAttributionAvailable": any(_dict(event.get("rawInputDevice")).get("available") for event in input_events),
        "observedInputDeviceLabels": sorted({str(_dict(event.get("rawInputDevice")).get("deviceClass")) for event in input_events if _dict(event.get("rawInputDevice")).get("deviceClass")}),
        "evidenceExamples": {
            "movementMatches": correlation["movementMatches"][:3],
            "clickMatches": correlation["clickMatches"][:3],
            "lastArduinoEvent": arduino_events[-1] if arduino_events else None,
            "lastInputEvent": input_events[-1] if input_events else None,
        },
        "warnings": sorted(set(warnings)),
    }


def build_mirror_verification(
    input_events: list[dict[str, Any]],
    arduino_events: list[dict[str, Any]] | None = None,
    *,
    manifest: dict[str, Any] | None = None,
    requested_mode: str | None = None,
    correlation_window_ms: int = 250,
    max_move_error_px: float = 5.0,
) -> dict[str, Any]:
    integrity = build_input_path_integrity(
        input_events,
        arduino_events,
        manifest=manifest,
        requested_mode=requested_mode,
        correlation_window_ms=correlation_window_ms,
        max_move_error_px=max_move_error_px,
    )
    return {
        "schema": MIRROR_VERIFICATION_SCHEMA,
        "status": "PASS" if integrity.get("mirrorVerified") or integrity.get("probeVerifiedClean") else ("WARN" if integrity.get("probeVerified") or integrity.get("arduinoConnected") else "FAIL"),
        "generated_at_utc": utc_now(),
        "requestedMode": integrity.get("requestedMode"),
        "actualDetectedMode": integrity.get("actualDetectedMode"),
        "inputPathClassification": integrity.get("inputPathClassification"),
        "arduinoPort": integrity.get("arduinoPort"),
        "arduinoProtocol": integrity.get("arduinoProtocol"),
        "arduinoConnected": integrity.get("arduinoConnected"),
        "mirrorSupported": integrity.get("mirrorSupported"),
        "mirrorActive": integrity.get("mirrorActive"),
        "mirrorVerified": integrity.get("mirrorVerified"),
        "probeVerified": integrity.get("probeVerified"),
        "probeVerifiedClean": integrity.get("probeVerifiedClean"),
        "probeClassification": integrity.get("probeClassification"),
        "liveMirrorActive": integrity.get("liveMirrorActive"),
        "liveMirrorVerified": integrity.get("liveMirrorVerified"),
        "mirrorQuality": integrity.get("mirrorQuality"),
        "mirrorVerificationStatus": integrity.get("mirrorVerificationStatus"),
        "commandCount": integrity.get("commandCount"),
        "nonProbeActionCommandCount": integrity.get("nonProbeActionCommandCount"),
        "movementCommandCount": integrity.get("movementCommandCount"),
        "clickCommandCount": integrity.get("clickCommandCount"),
        "keyboardCommandCount": integrity.get("keyboardCommandCount"),
        "ackCount": integrity.get("ackCount"),
        "observedCursorMovementCount": integrity.get("observedCursorMovementCount"),
        "observedClickCount": integrity.get("observedClickCount"),
        "correlatedCommandToObservedMovementCount": integrity.get("correlatedCommandToObservedMovementCount"),
        "correlatedCommandToObservedClickCount": integrity.get("correlatedCommandToObservedClickCount"),
        "uncorrelatedOsMoveCount": integrity.get("uncorrelatedOsMoveCount"),
        "uncorrelatedOsClickCount": integrity.get("uncorrelatedOsClickCount"),
        "uncorrelatedArduinoCommandCount": integrity.get("uncorrelatedArduinoCommandCount"),
        "correlationLatencyMs": integrity.get("correlationLatencyMs"),
        "conversionErrorPx": integrity.get("conversionErrorPx"),
        "warnings": integrity.get("warnings") or [],
        "evidenceExamples": integrity.get("evidenceExamples") or {},
    }


def atomic_write_json(path: str | Path, payload: dict[str, Any], *, pretty: bool = True) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, default=str)
        handle.write("\n")
    temp.replace(output)


def write_verification_outputs(recording_dir: str | Path, integrity: dict[str, Any], verification: dict[str, Any] | None = None) -> None:
    recording = Path(recording_dir)
    atomic_write_json(recording / "input_path_integrity_summary.json", integrity)
    if verification is not None:
        atomic_write_json(recording / "arduino_mirror_verification.json", verification)


def write_jsonl(path: str | Path, records: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")


def load_json(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_recording_inputs(recording_dir: str | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    recording = Path(recording_dir)
    try:
        import input_trace_recorder

        input_events = input_trace_recorder.load_input_events(recording / "input_events.jsonl")
    except Exception:
        input_events = []
    arduino_events = arduino_input_bridge.load_combined_arduino_events(recording)
    probe_result = load_json(recording / "arduino_probe_result.json")
    if probe_result:
        quality = probe_quality_from_payload(probe_result)
        for event in arduino_events:
            if event.get("probeCommand"):
                event["probeClassification"] = quality.get("classification")
                event["probeVerified"] = bool(quality.get("success"))
                event["probeVerifiedClean"] = quality.get("classification") == "arduino_probe_verified_clean"
                event["probeResult"] = quality.get("classification")
    manifest = load_json(recording / "manifest.json")
    return input_events, arduino_events, manifest


def analyze_recording(
    recording_dir: str | Path,
    *,
    write: bool = True,
    requested_mode: str | None = None,
    correlation_window_ms: int = 250,
    max_move_error_px: float = 5.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    input_events, arduino_events, manifest = load_recording_inputs(recording_dir)
    integrity = build_input_path_integrity(
        input_events,
        arduino_events,
        manifest=manifest,
        requested_mode=requested_mode,
        correlation_window_ms=correlation_window_ms,
        max_move_error_px=max_move_error_px,
    )
    verification = build_mirror_verification(
        input_events,
        arduino_events,
        manifest=manifest,
        requested_mode=requested_mode,
        correlation_window_ms=correlation_window_ms,
        max_move_error_px=max_move_error_px,
    )
    if write:
        write_verification_outputs(recording_dir, integrity, verification)
    return integrity, verification


def preflight_unproven_payload(*, requested_mode: str, port: str | None = None, reason: str | None = None) -> dict[str, Any]:
    return {
        "schema": MIRROR_VERIFICATION_SCHEMA,
        "status": "WARN",
        "generated_at_utc": utc_now(),
        "requestedMode": requested_mode,
        "actualDetectedMode": "unknown",
        "inputPathClassification": "arduino_mirror_requested" if requested_mode == "mirror" else "unknown_input_path",
        "arduinoPort": port,
        "arduinoProtocol": None,
        "arduinoConnected": None,
        "mirrorSupported": "unknown",
        "mirrorActive": None,
        "mirrorVerified": False,
        "probeVerified": False,
        "commandCount": 0,
        "movementCommandCount": 0,
        "clickCommandCount": 0,
        "keyboardCommandCount": 0,
        "ackCount": 0,
        "observedCursorMovementCount": 0,
        "observedClickCount": 0,
        "correlatedCommandToObservedMovementCount": 0,
        "correlatedCommandToObservedClickCount": 0,
        "correlationLatencyMs": {"min": None, "max": None, "average": None},
        "conversionErrorPx": {"max": None, "average": None},
        "warnings": [reason or "mirror preflight is configured but the current bridge cannot prove mirror mode before recording"],
        "evidenceExamples": {},
    }


def cursor_position() -> tuple[int, int]:
    if os.name != "nt":
        return (0, 0)
    try:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):  # type: ignore[attr-defined]
            return int(point.x), int(point.y)
    except Exception:  # noqa: BLE001
        pass
    return (0, 0)


def _probe_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Path.home() / ".osrs-telemetry" / "arduino_probe" / stamp


def _probe_input_event(kind: str, *, event_seq: int, when: float, dx: int = 0, dy: int = 0, button: str | None = None) -> dict[str, Any]:
    event = {
        "schema": "input_event.v1",
        "kind": kind,
        "event_seq": event_seq,
        "monotonic_time": when,
        "wall_time_utc": utc_now(),
        "dx": dx,
        "dy": dy,
        "source_backend": "probe_observer",
    }
    if button:
        event["button"] = button
    return event


def _cursor_sample(cursor_reader: Any) -> tuple[int, int]:
    value = cursor_reader()
    return int(value[0]), int(value[1])


def _observe_cursor_window(cursor_reader: Any, *, duration_ms: int, sleep_func: Any, poll_ms: int = 25) -> dict[str, Any]:
    start = _cursor_sample(cursor_reader)
    samples = [{"monotonic": time.monotonic(), "x": start[0], "y": start[1]}]
    duration = max(0.0, float(duration_ms or 0) / 1000.0)
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        sleep_func(max(0.001, min(float(poll_ms or 25) / 1000.0, max(0.001, deadline - time.monotonic()))))
        current = _cursor_sample(cursor_reader)
        samples.append({"monotonic": time.monotonic(), "x": current[0], "y": current[1]})
    end = (int(samples[-1]["x"]), int(samples[-1]["y"]))
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    return {
        "start": {"x": start[0], "y": start[1]},
        "end": {"x": end[0], "y": end[1]},
        "dx": dx,
        "dy": dy,
        "distancePx": round(math.hypot(dx, dy), 3),
        "samples": samples[:20],
    }


def classify_probe_result(
    *,
    command_sent: bool,
    supported: bool,
    acked: bool,
    requested_move: bool,
    commanded_dx: int,
    commanded_dy: int,
    observed_dx: int,
    observed_dy: int,
    background_distance_px: float = 0.0,
    max_background_move_px: float = 3.0,
    min_observed_delta: float = 1.0,
    max_error_px: float = 100.0,
    port_error: bool = False,
) -> dict[str, Any]:
    error_dx = int(observed_dx) - int(commanded_dx)
    error_dy = int(observed_dy) - int(commanded_dy)
    error_distance = math.hypot(error_dx, error_dy)
    observed_distance = math.hypot(observed_dx, observed_dy)
    direction_ok = True
    if commanded_dx:
        direction_ok = direction_ok and ((observed_dx > 0) == (commanded_dx > 0))
    if commanded_dy:
        direction_ok = direction_ok and ((observed_dy > 0) == (commanded_dy > 0))
    enough_delta = True if not requested_move else observed_distance >= float(min_observed_delta or 1.0)
    quiet = float(background_distance_px or 0.0) <= float(max_background_move_px or 3.0)
    clean = bool(command_sent and supported and acked and enough_delta and direction_ok and quiet and error_distance <= float(max_error_px or 100.0))
    if port_error:
        classification = "arduino_probe_port_error"
        status = "FAIL"
        success = False
        reason = "Arduino port was unavailable or locked during probe."
    elif command_sent and not supported:
        classification = "arduino_probe_unsupported"
        status = "FAIL"
        success = False
        reason = "Arduino command is not supported by the current protocol wrapper."
    elif command_sent and not acked:
        classification = "no_ack"
        status = "FAIL"
        success = False
        reason = "Arduino probe command did not receive an acknowledgement."
    elif requested_move and command_sent and not enough_delta:
        classification = "arduino_probe_sent_no_observed_delta"
        status = "FAIL"
        success = False
        reason = "Arduino probe move was sent but no cursor delta was observed."
    elif clean:
        classification = "arduino_probe_verified_clean"
        status = "PASS"
        success = True
        reason = "probe command produced Arduino ack and a clean matching cursor delta"
    elif command_sent and supported and acked and enough_delta:
        classification = "arduino_probe_verified_noisy"
        status = "WARN"
        success = True
        reason = "probe command produced Arduino ack and cursor movement, but movement was noisy or outside tolerance"
    elif command_sent:
        classification = "arduino_probe_failed"
        status = "FAIL"
        success = False
        reason = "Arduino probe command was sent but did not produce usable evidence."
    else:
        classification = "arduino_status_only"
        status = "WARN"
        success = False
        reason = "Arduino status worked, but no action-path probe was sent."
    return {
        "classification": classification,
        "status": status,
        "success": success,
        "reason": reason,
        "clean": clean,
        "observedDistancePx": round(observed_distance, 3),
        "errorDx": error_dx,
        "errorDy": error_dy,
        "distanceErrorPx": round(error_distance, 3),
        "directionOk": direction_ok,
        "quietWindowOk": quiet,
        "backgroundDistancePx": round(float(background_distance_px or 0.0), 3),
    }


def probe_quality_from_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    commanded = payload.get("commanded") if isinstance(payload.get("commanded"), dict) else {}
    observed = payload.get("observed") if isinstance(payload.get("observed"), dict) else {}
    ack = payload.get("ack") if isinstance(payload.get("ack"), dict) else {}
    commands = [command for command in payload.get("commands") or [] if isinstance(command, dict)]
    requested_move = any(str(command.get("command") or command.get("command_kind") or "").upper() in arduino_input_bridge.ARDUINO_MOVEMENT_COMMANDS for command in commands)
    return classify_probe_result(
        command_sent=bool(commands),
        supported=not any(command.get("supported") is False for command in commands),
        acked=bool(ack.get("received")),
        requested_move=requested_move,
        commanded_dx=int(commanded.get("dx") or 0),
        commanded_dy=int(commanded.get("dy") or 0),
        observed_dx=int(observed.get("dx") or 0),
        observed_dy=int(observed.get("dy") or 0),
        background_distance_px=float(_dict(payload.get("quietWindow")).get("backgroundDistancePx") or 0.0),
        max_background_move_px=float(_dict(payload.get("probeSettings")).get("maxBackgroundMovePx") or 3.0),
        min_observed_delta=float(_dict(payload.get("probeSettings")).get("probeMinObservedDxdy") or 1.0),
        max_error_px=float(_dict(payload.get("probeSettings")).get("probeMaxErrorPx") or 100.0),
    )


def run_probe(
    out_dir: str | Path | None = None,
    *,
    port: str | None = None,
    baud: int = arduino_input_bridge.DEFAULT_BAUD,
    move: tuple[int, int] | None = None,
    click: str | None = None,
    observe_ms: int = 500,
    quiet_window: bool = False,
    pre_observe_ms: int = 250,
    post_observe_ms: int | None = None,
    max_background_move_px: int = 3,
    probe_min_observed_dxdy: int = 1,
    probe_max_error_px: float = 100.0,
    serial_factory: Any | None = None,
    cursor_reader: Any | None = None,
    sleep_func: Any = time.sleep,
    pretty: bool = True,
) -> dict[str, Any]:
    output = Path(out_dir) if out_dir is not None else _probe_output_dir()
    output.mkdir(parents=True, exist_ok=True)
    cursor = cursor_reader or cursor_position
    post_observe_ms = int(post_observe_ms if post_observe_ms is not None else observe_ms)
    quiet_payload = {"enabled": bool(quiet_window), "backgroundDistancePx": 0.0, "backgroundDx": 0, "backgroundDy": 0}
    if quiet_window:
        pre = _observe_cursor_window(cursor, duration_ms=int(pre_observe_ms or 0), sleep_func=sleep_func)
        quiet_payload.update(
            {
                "start": pre["start"],
                "end": pre["end"],
                "backgroundDx": pre["dx"],
                "backgroundDy": pre["dy"],
                "backgroundDistancePx": pre["distancePx"],
                "maxBackgroundMovePx": max_background_move_px,
            }
        )
        before = (int(pre["end"]["x"]), int(pre["end"]["y"]))
    else:
        before = _cursor_sample(cursor)
    client = arduino_input_bridge.ArduinoCommandClient(
        output,
        recording_id=output.name,
        port=port,
        baud=baud,
        serial_factory=serial_factory,
        pretty=pretty,
    )
    commands: list[dict[str, Any]] = []
    warnings: list[str] = []
    status_payload: dict[str, Any] = {}
    port_error = False
    try:
        status_payload = client.connect()
        if move is not None:
            dx, dy = int(move[0]), int(move[1])
            command = client.send_move(dx, dy)
            command["probeCommand"] = True
            commands.append(command)
        if click:
            command = client.send_click(str(click))
            command["probeCommand"] = True
            commands.append(command)
        if post_observe_ms and post_observe_ms > 0:
            post = _observe_cursor_window(cursor, duration_ms=int(post_observe_ms), sleep_func=sleep_func)
            after = (int(post["end"]["x"]), int(post["end"]["y"]))
            observed_dx = int(after[0]) - int(before[0])
            observed_dy = int(after[1]) - int(before[1])
        else:
            after = _cursor_sample(cursor)
            observed_dx = int(after[0]) - int(before[0])
            observed_dy = int(after[1]) - int(before[1])
    except Exception as error:  # noqa: BLE001
        after = _cursor_sample(cursor)
        observed_dx = int(after[0]) - int(before[0])
        observed_dy = int(after[1]) - int(before[1])
        port_error = "port" in f"{type(error).__name__}: {error}".lower() or "serial" in f"{type(error).__name__}: {error}".lower()
        warnings.append(f"{type(error).__name__}: {error}")
    finally:
        try:
            client.close()
        except Exception:
            pass

    input_events = []
    event_seq = 1
    if observed_dx or observed_dy:
        input_events.append(_probe_input_event("mouse_move", event_seq=event_seq, when=time.monotonic(), dx=observed_dx, dy=observed_dy))
        event_seq += 1
    if click:
        # A short HID click often finishes before polling can sample button state; this keeps the probe honest.
        input_events.append(_probe_input_event("click", event_seq=event_seq, when=time.monotonic(), button=str(click)))

    supported = all(command.get("supported") is not False for command in commands)
    acked = any(command.get("ack_received") for command in commands)
    requested_move = move is not None
    expected_dx = int(move[0]) if move is not None else 0
    expected_dy = int(move[1]) if move is not None else 0
    error_dx = observed_dx - expected_dx
    error_dy = observed_dy - expected_dy
    error_distance = math.hypot(error_dx, error_dy)
    quality = classify_probe_result(
        command_sent=bool(commands),
        supported=supported,
        acked=acked,
        requested_move=requested_move,
        commanded_dx=expected_dx,
        commanded_dy=expected_dy,
        observed_dx=observed_dx,
        observed_dy=observed_dy,
        background_distance_px=float(quiet_payload.get("backgroundDistancePx") or 0.0),
        max_background_move_px=float(max_background_move_px or 3),
        min_observed_delta=float(probe_min_observed_dxdy or 1),
        max_error_px=float(probe_max_error_px or 100.0),
        port_error=port_error,
    )
    classification = str(quality["classification"])
    status = str(quality["status"])
    reason = str(quality["reason"])

    action_commands = arduino_input_bridge.load_arduino_action_commands(output / "arduino_action_commands.jsonl")
    for record in action_commands:
        record["probeCommand"] = True
        record["probeVerified"] = classification in {"arduino_probe_verified", "arduino_probe_verified_clean", "arduino_probe_verified_noisy"}
        record["probeVerifiedClean"] = classification == "arduino_probe_verified_clean"
        record["probeClassification"] = classification
        record["probeResult"] = "verified_clean" if classification == "arduino_probe_verified_clean" else classification
    if action_commands:
        write_jsonl(output / "arduino_action_commands.jsonl", action_commands)
    integrity = build_input_path_integrity(input_events, action_commands, requested_mode="mirror")
    integrity.update(
        {
            "status": status if status != "FAIL" else "FAIL",
            "inputPathClassification": classification if classification in INPUT_PATH_CLASSIFICATIONS else "unknown_input_path",
            "probeVerified": classification in {"arduino_probe_verified", "arduino_probe_verified_clean", "arduino_probe_verified_noisy"},
            "probeVerifiedClean": classification == "arduino_probe_verified_clean",
            "mirrorVerificationStatus": classification if classification.startswith("arduino_probe_verified") else "failed",
            "probe": {
                "commandedDx": expected_dx,
                "commandedDy": expected_dy,
                "observedDx": observed_dx,
                "observedDy": observed_dy,
                "errorDx": error_dx,
                "errorDy": error_dy,
                "distanceErrorPx": round(error_distance, 3),
                "cursorBefore": {"x": before[0], "y": before[1]},
                "cursorAfter": {"x": after[0], "y": after[1]},
                "quietWindow": quiet_payload,
                "quality": quality,
                "reason": reason,
            },
        }
    )
    verification = build_mirror_verification(input_events, action_commands, requested_mode="mirror")
    verification.update(
        {
            "status": status,
            "inputPathClassification": integrity["inputPathClassification"],
            "probeVerified": classification in {"arduino_probe_verified", "arduino_probe_verified_clean", "arduino_probe_verified_noisy"},
            "probeVerifiedClean": classification == "arduino_probe_verified_clean",
            "mirrorVerificationStatus": integrity["mirrorVerificationStatus"],
            "reason": reason,
            "probe": integrity["probe"],
            "warnings": sorted(set(list(verification.get("warnings") or []) + warnings)),
        }
    )
    payload = {
        "schema": "arduino_probe_result.v1",
        "status": status,
        "success": bool(quality.get("success")),
        "classification": classification,
        "reason": reason,
        "outputDir": str(output),
        "port": port or status_payload.get("port"),
        "baud": baud,
        "commanded": {"dx": expected_dx, "dy": expected_dy, "click": click},
        "observed": {"dx": observed_dx, "dy": observed_dy},
        "error": {"dx": error_dx, "dy": error_dy, "distancePx": round(error_distance, 3)},
        "quietWindow": quiet_payload,
        "probeSettings": {
            "quietWindow": bool(quiet_window),
            "preObserveMs": int(pre_observe_ms or 0),
            "postObserveMs": int(post_observe_ms or 0),
            "maxBackgroundMovePx": int(max_background_move_px or 0),
            "probeMinObservedDxdy": int(probe_min_observed_dxdy or 0),
            "probeMaxErrorPx": float(probe_max_error_px or 0.0),
        },
        "quality": quality,
        "ack": {
            "received": acked,
            "count": sum(1 for command in commands if command.get("ack_received")),
            "latencyMs": [command.get("ack_latency_ms") for command in commands if command.get("ack_latency_ms") is not None],
        },
        "commands": commands,
        "warnings": warnings,
    }
    arduino_input_bridge.atomic_write_json(output / "arduino_probe_result.json", payload)
    write_verification_outputs(output, integrity, verification)
    return payload


def status_payload(port: str | None = None, *, baud: int = arduino_input_bridge.DEFAULT_BAUD) -> dict[str, Any]:
    return arduino_input_bridge.status_payload(port, baud=baud)


def parse_moves(value: str) -> list[tuple[int, int]]:
    moves: list[tuple[int, int]] = []
    for part in str(value or "").split(";"):
        text = part.strip()
        if not text:
            continue
        x_text, y_text = [item.strip() for item in text.split(",", 1)]
        moves.append((int(x_text), int(y_text)))
    return moves


def run_calibration(
    out_path: str | Path | None = None,
    *,
    port: str | None = None,
    baud: int = arduino_input_bridge.DEFAULT_BAUD,
    moves: list[tuple[int, int]] | None = None,
    observe_ms: int = 750,
    quiet_window: bool = True,
    serial_factory: Any | None = None,
    cursor_reader: Any | None = None,
    sleep_func: Any = time.sleep,
) -> dict[str, Any]:
    output = Path(out_path) if out_path else _probe_output_dir() / "arduino_calibration_probe.json"
    output_dir = output.parent if output.suffix else output
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, move in enumerate(moves or [(25, 0), (-25, 0), (0, 25), (0, -25)], start=1):
        probe_dir = output_dir / f"sample_{index:02d}_{move[0]}_{move[1]}"
        rows.append(
            run_probe(
                probe_dir,
                port=port,
                baud=baud,
                move=move,
                observe_ms=observe_ms,
                quiet_window=quiet_window,
                serial_factory=serial_factory,
                cursor_reader=cursor_reader,
                sleep_func=sleep_func,
            )
        )
    clean = [row for row in rows if row.get("classification") == "arduino_probe_verified_clean"]
    payload = {
        "schema": "arduino_probe_calibration.v1",
        "status": "PASS" if clean else "WARN",
        "generated_at_utc": utc_now(),
        "port": port,
        "baud": baud,
        "sampleCount": len(rows),
        "cleanSampleCount": len(clean),
        "samples": rows,
        "warnings": [] if clean else ["no clean probe samples were captured"],
    }
    target = output if output.suffix else output_dir / "arduino_probe_calibration.json"
    atomic_write_json(target, payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Arduino probe/mirror input path.")
    parser.add_argument("--probe", action="store_true", help="Send a deliberate Arduino probe command and observe OS cursor evidence.")
    parser.add_argument("--calibrate", action="store_true", help="Run several quiet probe movements for calibration.")
    parser.add_argument("--click-probe", action="store_true", help="Run a deliberate click probe.")
    parser.add_argument("--status", action="store_true", help="Print Arduino status.")
    parser.add_argument("--port", help="Arduino serial port, for example COM6.")
    parser.add_argument("--baud", type=int, default=arduino_input_bridge.DEFAULT_BAUD)
    parser.add_argument("--move", nargs=2, type=int, metavar=("DX", "DY"), help="Probe move delta.")
    parser.add_argument("--moves", default="25,0;-25,0;0,25;0,-25", help="Calibration moves, for example '25,0;-25,0;0,25;0,-25'.")
    parser.add_argument("--click", choices=("left", "right", "middle"), help="Probe click button.")
    parser.add_argument("--button", choices=("left", "right", "middle"), default="left", help="Click probe button.")
    parser.add_argument("--observe-ms", type=int, default=500)
    parser.add_argument("--quiet-window", action="store_true", help="Require a quiet pre-command cursor window and classify noisy probes.")
    parser.add_argument("--pre-observe-ms", type=int, default=250)
    parser.add_argument("--post-observe-ms", type=int, default=None)
    parser.add_argument("--max-background-move-px", type=int, default=3)
    parser.add_argument("--probe-min-observed-dxdy", type=int, default=1)
    parser.add_argument("--probe-max-error-px", type=float, default=100.0)
    parser.add_argument("--calibration-out", help="Calibration output path.")
    parser.add_argument("--out", help="Output folder for probe artifacts.")
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    args = parser.parse_args(argv)
    if args.status and not args.probe:
        payload = status_payload(args.port, baud=args.baud)
        print(json.dumps(payload, indent=2, default=str))
        return 0 if payload.get("available") else 1
    if args.calibrate:
        payload = run_calibration(
            args.calibration_out or args.out,
            port=args.port,
            baud=args.baud,
            moves=parse_moves(args.moves),
            observe_ms=args.observe_ms,
            quiet_window=args.quiet_window,
        )
        print(json.dumps(payload, indent=2, default=str))
        return 0 if payload.get("status") == "PASS" else 1
    if args.probe or args.click_probe:
        move = tuple(args.move) if args.move else None
        payload = run_probe(
            args.out,
            port=args.port,
            baud=args.baud,
            move=move,
            click=args.click or (args.button if args.click_probe else None),
            observe_ms=args.observe_ms,
            quiet_window=args.quiet_window,
            pre_observe_ms=args.pre_observe_ms,
            post_observe_ms=args.post_observe_ms,
            max_background_move_px=args.max_background_move_px,
            probe_min_observed_dxdy=args.probe_min_observed_dxdy,
            probe_max_error_px=args.probe_max_error_px,
        )
        print(json.dumps(payload, indent=2, default=str))
        return 0 if payload.get("success") else 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

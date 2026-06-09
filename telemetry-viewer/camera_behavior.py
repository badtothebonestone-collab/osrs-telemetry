from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import telemetry_schema


CAMERA_SUMMARY_SCHEMA = "camera_behavior_summary.v1"
CAMERA_SEGMENT_SCHEMA = "camera_segment.v1"


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


def _lookup(root: Any, aliases: tuple[str, ...]) -> Any:
    values, _paths = telemetry_schema.lookup_any(root, list(aliases))
    return values[0] if values else None


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
    except OSError:
        return []
    return records


def camera_sample_from_telemetry_event(event: dict[str, Any]) -> dict[str, Any] | None:
    high = _dict(event.get("high_value_fields"))
    root = {"event": event, "high_value_fields": high}
    yaw = _lookup(root, ("**.cameraYaw", "**.yaw"))
    pitch = _lookup(root, ("**.cameraPitch", "**.pitch"))
    zoom = _lookup(root, ("**.cameraZoom", "**.zoom"))
    viewport = _lookup(root, ("**.cameraViewport", "**.viewport"))
    if yaw is None and pitch is None and zoom is None:
        return None
    return {
        "elapsed_seconds": _float(event.get("elapsed_seconds")),
        "wall_time_utc": event.get("wall_time_utc"),
        "latest_tick": event.get("latest_tick") or high.get("latest_tick"),
        "latest_export_sequence": event.get("latest_export_sequence") or high.get("latest_export_sequence"),
        "yaw": _float(yaw),
        "pitch": _float(pitch),
        "zoom": _float(zoom),
        "viewport": viewport,
    }


def camera_samples_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    samples = []
    for event in events:
        if event.get("event_type") != "source_snapshot":
            continue
        sample = camera_sample_from_telemetry_event(event)
        if sample:
            samples.append(sample)
    return samples


def _input_between(input_events: list[dict[str, Any]], start_time: float | None, end_time: float | None) -> list[dict[str, Any]]:
    if start_time is None or end_time is None:
        return []
    return [
        event
        for event in input_events
        if _float(event.get("elapsed_seconds")) is not None
        and float(start_time) - 0.25 <= float(event.get("elapsed_seconds")) <= float(end_time) + 0.25
    ]


def _source_from_input(events: list[dict[str, Any]]) -> str:
    if any(event.get("kind") in {"drag_start", "drag_move", "drag_end"} and event.get("button") == "middle" for event in events):
        return "middle_mouse_drag"
    if any(event.get("kind") in {"key_down", "key_up"} and event.get("key_name") in {"left", "right", "up", "down"} for event in events):
        return "arrow_keys"
    if any(event.get("kind") == "wheel" for event in events):
        return "mouse_wheel"
    return "unknown"


def _mouse_drag(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    drag_points = [
        event
        for event in events
        if event.get("button") == "middle" and event.get("kind") in {"drag_start", "drag_move", "drag_end"}
        and event.get("screen_x") is not None
        and event.get("screen_y") is not None
    ]
    if len(drag_points) < 2:
        return None
    start = drag_points[0]
    end = drag_points[-1]
    dx = int(end.get("screen_x") or 0) - int(start.get("screen_x") or 0)
    dy = int(end.get("screen_y") or 0) - int(start.get("screen_y") or 0)
    return {
        "start": {"x": start.get("screen_x"), "y": start.get("screen_y")},
        "end": {"x": end.get("screen_x"), "y": end.get("screen_y")},
        "dx": dx,
        "dy": dy,
    }


def _next_click(input_events: list[dict[str, Any]], end_time: float | None, eligible_event_seqs: set[int] | None = None) -> dict[str, Any] | None:
    if end_time is None:
        return None
    clicks = [
        event
        for event in input_events
        if event.get("kind") in {"click", "double_click"}
        and _float(event.get("elapsed_seconds")) is not None
        and float(event.get("elapsed_seconds")) >= end_time
        and (eligible_event_seqs is None or int(event.get("event_seq") or 0) in eligible_event_seqs)
    ]
    return clicks[0] if clicks else None


def detect_camera_segments(
    telemetry_events: list[dict[str, Any]],
    input_events: list[dict[str, Any]] | None = None,
    *,
    min_delta: float = 1.0,
    eligible_event_seqs: set[int] | None = None,
) -> list[dict[str, Any]]:
    input_events = input_events or []
    samples = camera_samples_from_events(telemetry_events)
    segments: list[dict[str, Any]] = []
    active_start: dict[str, Any] | None = None
    previous: dict[str, Any] | None = None
    for sample in samples:
        if previous is None:
            previous = sample
            continue
        dyaw = (sample.get("yaw") or 0) - (previous.get("yaw") or 0)
        dpitch = (sample.get("pitch") or 0) - (previous.get("pitch") or 0)
        changed = abs(dyaw) >= min_delta or abs(dpitch) >= min_delta
        if changed and active_start is None:
            active_start = previous
        if not changed and active_start is not None:
            segments.append(_segment(active_start, previous, input_events, len(segments) + 1, eligible_event_seqs=eligible_event_seqs))
            active_start = None
        previous = sample
    if active_start is not None and previous is not None:
        segments.append(_segment(active_start, previous, input_events, len(segments) + 1, eligible_event_seqs=eligible_event_seqs))
    return segments


def _segment(
    start: dict[str, Any],
    end: dict[str, Any],
    input_events: list[dict[str, Any]],
    index: int,
    *,
    eligible_event_seqs: set[int] | None = None,
) -> dict[str, Any]:
    start_time = _float(start.get("elapsed_seconds"))
    end_time = _float(end.get("elapsed_seconds"))
    nearby_input = _input_between(input_events, start_time, end_time)
    next_click = _next_click(input_events, end_time, eligible_event_seqs)
    duration_ms = int(round(max(0.0, ((end_time or 0.0) - (start_time or 0.0))) * 1000))
    delta_yaw = (end.get("yaw") or 0) - (start.get("yaw") or 0)
    delta_pitch = (end.get("pitch") or 0) - (start.get("pitch") or 0)
    effects = []
    if next_click:
        effects.append("click_followed_camera")
    if abs(delta_yaw) or abs(delta_pitch):
        effects.append("camera_changed")
    return {
        "schema": CAMERA_SEGMENT_SCHEMA,
        "segmentId": f"cam_{index:03d}",
        "startTime": start_time,
        "endTime": end_time,
        "durationMs": duration_ms,
        "source": _source_from_input(nearby_input),
        "startYaw": start.get("yaw"),
        "endYaw": end.get("yaw"),
        "deltaYaw": round(delta_yaw, 3),
        "startPitch": start.get("pitch"),
        "endPitch": end.get("pitch"),
        "deltaPitch": round(delta_pitch, 3),
        "mouseDrag": _mouse_drag(nearby_input),
        "nextClick": next_click,
        "targetBefore": None,
        "targetAfter": None,
        "effect": effects,
        "confidence": 0.75 if effects else 0.45,
        "notes": [] if nearby_input else ["camera telemetry changed but OS input source was not captured"],
    }


def summarize_camera_behavior(
    telemetry_events: list[dict[str, Any]],
    input_events: list[dict[str, Any]] | None = None,
    *,
    action_classifications: list[dict[str, Any]] | None = None,
    target_match_quality: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    input_events = input_events or []
    eligible_event_seqs = None
    camera_drag_release_exclusions = 0
    quality_by_seq = {int(item.get("eventSeq") or 0): item for item in target_match_quality or []}
    if action_classifications is not None:
        eligible_event_seqs = {
            int(item.get("eventSeq") or 0)
            for item in action_classifications
            if item.get("targetRelativeEligible") or item.get("eligibleForTargetMatching")
        }
        camera_drag_release_exclusions = sum(
            1
            for item in action_classifications
            if item.get("eventKind") in {"click", "double_click"} and item.get("classification") == "camera_drag_release"
        )
    segments = detect_camera_segments(telemetry_events, input_events, eligible_event_seqs=eligible_event_seqs)
    for segment in segments:
        click = _dict(segment.get("nextClick"))
        quality = quality_by_seq.get(int(click.get("event_seq") or 0))
        if quality:
            target = _dict(quality.get("matchedTarget"))
            segment["nextClickTargetName"] = target.get("name")
            segment["nextClickTargetAction"] = target.get("action")
            segment["nextClickTargetQuality"] = quality.get("quality")
            segment["nextClickTargetMatchScore"] = quality.get("score")
    strong_medium_times = _camera_to_quality_click_ms(segments, quality_by_seq, {"strong", "medium"})
    durations = [segment.get("durationMs") for segment in segments if isinstance(segment.get("durationMs"), int)]
    yaw_deltas = [abs(float(segment.get("deltaYaw") or 0)) for segment in segments]
    pitch_deltas = [abs(float(segment.get("deltaPitch") or 0)) for segment in segments]
    camera_before_click = [segment for segment in segments if isinstance(segment.get("nextClick"), dict)]
    return {
        "schema": CAMERA_SUMMARY_SCHEMA,
        "status": "PASS" if segments else "WARN",
        "generated_at_utc": utc_now(),
        "totalCameraSegments": len(segments),
        "middleMouseDragSegments": sum(1 for segment in segments if segment.get("source") == "middle_mouse_drag"),
        "arrowKeyCameraSegments": sum(1 for segment in segments if segment.get("source") == "arrow_keys"),
        "wheelZoomEvents": sum(1 for event in input_events if event.get("kind") == "wheel"),
        "averageCameraSegmentDurationMs": round(sum(durations) / len(durations), 3) if durations else None,
        "averageYawDelta": round(sum(yaw_deltas) / len(yaw_deltas), 3) if yaw_deltas else None,
        "averagePitchDelta": round(sum(pitch_deltas) / len(pitch_deltas), 3) if pitch_deltas else None,
        "cameraBeforeClickCount": len(camera_before_click),
        "averageTimeFromCameraToClickMs": _average_camera_to_click_ms(camera_before_click),
        "cameraDragsExcludedFromTargetClickAnalysis": camera_drag_release_exclusions,
        "eligibleClickFilteringApplied": action_classifications is not None,
        "targetQualityApplied": target_match_quality is not None,
        "cameraBeforeStrongOrMediumClickCount": len(strong_medium_times),
        "averageTimeFromCameraToStrongOrMediumClickMs": round(sum(strong_medium_times) / len(strong_medium_times), 3) if strong_medium_times else None,
        "cameraMovementPrecededStrongTargetMatch": any(
            _dict(quality_by_seq.get(int(_dict(segment.get("nextClick")).get("event_seq") or 0))).get("quality") == "strong"
            for segment in segments
        ),
        "targetsClickedAfterCameraAdjustment": [
            _dict(segment.get("nextClick")).get("target") or _dict(segment.get("nextClick")).get("hover_target")
            for segment in camera_before_click
            if segment.get("nextClick")
        ],
        "segments": segments,
        "examples": segments[:5],
        "warnings": [] if segments else ["No camera yaw/pitch changes were found, or source snapshots lack camera fields."],
    }


def _average_camera_to_click_ms(segments: list[dict[str, Any]]) -> float | None:
    values = []
    for segment in segments:
        click = _dict(segment.get("nextClick"))
        click_time = _float(click.get("elapsed_seconds"))
        end_time = _float(segment.get("endTime"))
        if click_time is not None and end_time is not None:
            values.append((click_time - end_time) * 1000.0)
    return round(sum(values) / len(values), 3) if values else None


def _camera_to_quality_click_ms(segments: list[dict[str, Any]], quality_by_seq: dict[int, dict[str, Any]], accepted: set[str]) -> list[float]:
    values: list[float] = []
    for segment in segments:
        click = _dict(segment.get("nextClick"))
        quality = _dict(quality_by_seq.get(int(click.get("event_seq") or 0)))
        if quality.get("quality") not in accepted:
            continue
        click_time = _float(click.get("elapsed_seconds"))
        end_time = _float(segment.get("endTime"))
        if click_time is not None and end_time is not None:
            values.append(round((click_time - end_time) * 1000.0, 3))
    return values


def atomic_write_json(path: str | Path, payload: dict[str, Any], *, pretty: bool = True) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, default=str)
        handle.write("\n")
    temp.replace(output)


def analyze_recording(recording_dir: str | Path, *, write: bool = True) -> dict[str, Any]:
    recording = Path(recording_dir)
    telemetry_events = load_jsonl(recording / "events.jsonl")
    input_events = load_jsonl(recording / "input_events.jsonl")
    summary = summarize_camera_behavior(telemetry_events, input_events)
    if write:
        atomic_write_json(recording / "camera_behavior_summary.json", summary)
    return summary

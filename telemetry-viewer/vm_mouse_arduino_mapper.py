from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAPPING_SCHEMA = "vm_mouse_arduino_mapping.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _point(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    x = value.get("x", value.get("screen_x", value.get("canvas_x", value.get("client_x"))))
    y = value.get("y", value.get("screen_y", value.get("canvas_y", value.get("client_y"))))
    try:
        return {"x": float(x), "y": float(y)}
    except (TypeError, ValueError):
        return None


def _event_point(event: dict[str, Any]) -> dict[str, float] | None:
    for x_key, y_key in (("canvas_x", "canvas_y"), ("client_x", "client_y"), ("screen_x", "screen_y"), ("x", "y")):
        if event.get(x_key) is not None and event.get(y_key) is not None:
            try:
                return {"x": float(event[x_key]), "y": float(event[y_key])}
            except (TypeError, ValueError):
                return None
    return None


def chunk_delta(dx: int | float, dy: int | float, *, max_step: int = 20) -> list[dict[str, int]]:
    dx_i = int(round(dx or 0))
    dy_i = int(round(dy or 0))
    max_step = max(1, int(max_step or 20))
    steps = max(abs(dx_i), abs(dy_i), 1)
    chunk_count = max(1, math.ceil(steps / max_step))
    chunks: list[dict[str, int]] = []
    prev_x = 0
    prev_y = 0
    for index in range(1, chunk_count + 1):
        next_x = int(round(dx_i * index / chunk_count))
        next_y = int(round(dy_i * index / chunk_count))
        part = {"dx": next_x - prev_x, "dy": next_y - prev_y}
        if part["dx"] or part["dy"]:
            chunks.append(part)
        prev_x = next_x
        prev_y = next_y
    return chunks or [{"dx": 0, "dy": 0}]


def point_to_relative_segments(start: dict[str, Any], end: dict[str, Any], *, max_step: int = 20) -> dict[str, Any]:
    start_point = _point(start) or {"x": 0.0, "y": 0.0}
    end_point = _point(end) or start_point
    dx = end_point["x"] - start_point["x"]
    dy = end_point["y"] - start_point["y"]
    segments = chunk_delta(dx, dy, max_step=max_step)
    return {
        "schema": MAPPING_SCHEMA,
        "kind": "point_to_relative_segments",
        "start": start_point,
        "end": end_point,
        "total_dx": int(round(dx)),
        "total_dy": int(round(dy)),
        "segments": segments,
        "timing": timing_model(segments),
    }


def mouse_path_to_relative_sequence(points: list[dict[str, Any]], *, max_step: int = 20) -> dict[str, Any]:
    normalized = [_point(point) for point in points]
    normalized = [point for point in normalized if point is not None]
    segments: list[dict[str, int]] = []
    for before, after in zip(normalized, normalized[1:]):
        segments.extend(chunk_delta(after["x"] - before["x"], after["y"] - before["y"], max_step=max_step))
    total_dx = int(round((normalized[-1]["x"] - normalized[0]["x"]) if len(normalized) >= 2 else 0))
    total_dy = int(round((normalized[-1]["y"] - normalized[0]["y"]) if len(normalized) >= 2 else 0))
    return {
        "schema": MAPPING_SCHEMA,
        "kind": "mouse_path_to_relative_sequence",
        "pointCount": len(normalized),
        "segment_count": len(segments),
        "total_dx": total_dx,
        "total_dy": total_dy,
        "segments": segments,
        "timing": timing_model(segments),
    }


def click_to_arduino_button_events(click_event: dict[str, Any], *, hold_ms: int = 40) -> list[dict[str, Any]]:
    button = str(click_event.get("button") or "left")
    return [
        {"command": "MOUSE_DOWN", "button": button, "delay_ms": 0},
        {"command": "MOUSE_UP", "button": button, "delay_ms": max(0, int(hold_ms or 0))},
    ]


def wheel_to_arduino_command(wheel_event: dict[str, Any]) -> dict[str, Any]:
    delta = int(wheel_event.get("wheel_delta") or wheel_event.get("delta") or 0)
    return {"command": "WHEEL", "delta": delta, "supported": False, "notes": ["firmware wheel command support is not confirmed"]}


def timing_model(segments: list[dict[str, Any]], *, duration_ms: int | None = None) -> dict[str, Any]:
    count = len(segments)
    total_dx = sum(int(segment.get("dx") or 0) for segment in segments)
    total_dy = sum(int(segment.get("dy") or 0) for segment in segments)
    max_dx = max([abs(int(segment.get("dx") or 0)) for segment in segments] or [0])
    max_dy = max([abs(int(segment.get("dy") or 0)) for segment in segments] or [0])
    duration = int(duration_ms if duration_ms is not None else max(0, count * 8))
    return {
        "segment_count": count,
        "total_dx": total_dx,
        "total_dy": total_dy,
        "duration_ms": duration,
        "average_step_dx": (total_dx / count) if count else 0,
        "average_step_dy": (total_dy / count) if count else 0,
        "max_step_dx": max_dx,
        "max_step_dy": max_dy,
        "pause_count": 0,
    }


def compare_observed_to_commanded(observed: dict[str, Any], commanded: dict[str, Any]) -> dict[str, Any]:
    observed_dx = int(round(float(observed.get("dx", observed.get("observed_dx", 0)) or 0)))
    observed_dy = int(round(float(observed.get("dy", observed.get("observed_dy", 0)) or 0)))
    commanded_dx = int(round(float(commanded.get("dx", commanded.get("commanded_dx", 0)) or 0)))
    commanded_dy = int(round(float(commanded.get("dy", commanded.get("commanded_dy", 0)) or 0)))
    return {
        "schema": "vm_mouse_arduino_comparison.v1",
        "observed_dx": observed_dx,
        "observed_dy": observed_dy,
        "commanded_dx": commanded_dx,
        "commanded_dy": commanded_dy,
        "error_dx": observed_dx - commanded_dx,
        "error_dy": observed_dy - commanded_dy,
        "distance_error": round(math.hypot(observed_dx - commanded_dx, observed_dy - commanded_dy), 3),
    }


def target_relative_click(click_event: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    click = _event_point(click_event)
    aim = _point(target.get("aimPoint")) or _point(target.get("canvas")) or _point(target.get("canvasLocation"))
    if click is None or aim is None:
        return {"status": "WARN", "missingFields": ["click_point" if click is None else "target_aim_point"]}
    dx = click["x"] - aim["x"]
    dy = click["y"] - aim["y"]
    bounds = target.get("clickboxBounds") if isinstance(target.get("clickboxBounds"), dict) else target.get("bounds")
    inside = None
    if isinstance(bounds, dict):
        left = bounds.get("left", bounds.get("x"))
        top = bounds.get("top", bounds.get("y"))
        width = bounds.get("width")
        height = bounds.get("height")
        right = bounds.get("right", (left + width) if left is not None and width is not None else None)
        bottom = bounds.get("bottom", (top + height) if top is not None and height is not None else None)
        try:
            inside = float(left) <= click["x"] <= float(right) and float(top) <= click["y"] <= float(bottom)
        except (TypeError, ValueError):
            inside = None
    return {
        "schema": "target_relative_click.v1",
        "status": "PASS",
        "click": click,
        "targetAimPoint": aim,
        "dx": round(dx, 3),
        "dy": round(dy, 3),
        "distance": round(math.hypot(dx, dy), 3),
        "insideClickbox": inside,
        "target": {
            "ref": target.get("ref"),
            "id": target.get("id") or target.get("effectiveId"),
            "name": target.get("name") or target.get("effectiveName"),
            "actions": target.get("actions") or target.get("effectiveActions"),
        },
    }


def build_mapping(
    input_events: list[dict[str, Any]],
    arduino_events: list[dict[str, Any]] | None = None,
    *,
    max_step: int = 20,
    telemetry_summary: dict[str, Any] | None = None,
    arduino_summary: dict[str, Any] | None = None,
    action_classifications: list[dict[str, Any]] | None = None,
    target_match_quality: list[dict[str, Any]] | None = None,
    input_path_integrity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    real_input_events = [
        event
        for event in input_events
        if event.get("kind") in {"mouse_move", "drag_move", "click", "double_click", "mouse_down", "mouse_up"}
    ]
    if not real_input_events:
        return {
            "schema": MAPPING_SCHEMA,
            "generated_at_utc": utc_now(),
            "status": "WARN",
            "reason": "input_trace_missing_or_empty",
            "inputEventCount": len(input_events),
            "arduinoEventCount": len(arduino_events or []),
            "arduino": arduino_summary,
            "telemetry": telemetry_summary,
            "inputPathIntegrity": input_path_integrity,
            "inputPathClassification": (input_path_integrity or {}).get("inputPathClassification"),
            "mirrorVerificationStatus": (input_path_integrity or {}).get("mirrorVerificationStatus"),
            "finalMirrorRecordingVerdict": (input_path_integrity or {}).get("finalMirrorRecordingVerdict"),
            "menuSelectionsAfterDisarm": (input_path_integrity or {}).get("menuSelectionsAfterDisarm"),
            "actionClicksAfterDisarm": (input_path_integrity or {}).get("actionClicksAfterDisarm"),
            "postActionArduinoCommandCount": (input_path_integrity or {}).get("postActionArduinoCommandCount"),
            "postActionMovementCommandCount": (input_path_integrity or {}).get("postActionMovementCommandCount"),
            "postActionClickCommandCount": (input_path_integrity or {}).get("postActionClickCommandCount"),
            "postActionWeirdMovementSuspected": (input_path_integrity or {}).get("postActionWeirdMovementSuspected"),
            "feedbackLoopSuspected": (input_path_integrity or {}).get("feedbackLoopSuspected"),
            "mirrorArmedStartElapsedSeconds": (input_path_integrity or {}).get("mirrorArmedStartElapsedSeconds"),
            "mirrorDisarmElapsedSeconds": (input_path_integrity or {}).get("mirrorDisarmElapsedSeconds"),
            "mousePath": mouse_path_to_relative_sequence([], max_step=max_step),
            "clickMappings": [],
            "observedVsCommanded": None,
            "warnings": ["input_trace_missing_or_empty"],
        }
    mouse_points = [_event_point(event) for event in input_events if event.get("kind") in {"mouse_move", "drag_move", "click"}]
    mouse_points = [point for point in mouse_points if point is not None]
    path_mapping = mouse_path_to_relative_sequence(mouse_points, max_step=max_step)
    classification_by_seq = {int(item.get("eventSeq") or 0): item for item in action_classifications or []}
    quality_by_seq = {int(item.get("eventSeq") or 0): item for item in target_match_quality or []}
    all_clicks = [event for event in input_events if event.get("kind") in {"click", "double_click"}]
    if action_classifications is None:
        target_clicks = all_clicks
        game_action_clicks = all_clicks
        excluded_clicks: list[dict[str, Any]] = []
        ambiguous_clicks: list[dict[str, Any]] = []
        camera_drag_clicks: list[dict[str, Any]] = []
    else:
        target_clicks = [
            event
            for event in all_clicks
            if classification_by_seq.get(int(event.get("event_seq") or 0), {}).get("targetRelativeEligible")
        ]
        game_action_clicks = [
            event
            for event in all_clicks
            if classification_by_seq.get(int(event.get("event_seq") or 0), {}).get("eligibleForTargetMatching")
        ]
        excluded_clicks = [
            event
            for event in all_clicks
            if not classification_by_seq.get(int(event.get("event_seq") or 0), {}).get("targetRelativeEligible")
        ]
        ambiguous_clicks = [
            event
            for event in all_clicks
            if classification_by_seq.get(int(event.get("event_seq") or 0), {}).get("classification") == "ambiguous_click"
        ]
        camera_drag_clicks = [
            event
            for event in all_clicks
            if classification_by_seq.get(int(event.get("event_seq") or 0), {}).get("classification") == "camera_drag_release"
        ]
    if target_match_quality is not None:
        strong_medium_target_clicks = [
            event
            for event in target_clicks
            if quality_by_seq.get(int(event.get("event_seq") or 0), {}).get("quality") in {"strong", "medium"}
        ]
        weak_target_clicks = [
            event
            for event in target_clicks
            if quality_by_seq.get(int(event.get("event_seq") or 0), {}).get("quality") == "weak"
        ]
        unmatched_target_clicks = [
            event
            for event in target_clicks
            if quality_by_seq.get(int(event.get("event_seq") or 0), {}).get("quality") == "unmatched"
        ]
    else:
        strong_medium_target_clicks = target_clicks
        weak_target_clicks = []
        unmatched_target_clicks = []
    command_deltas = []
    for event in arduino_events or []:
        command = str(event.get("command") or event.get("commandSent") or "").upper()
        if command == "MOVE":
            command_deltas.append({"dx": event.get("dx") or 0, "dy": event.get("dy") or 0})
    if input_path_integrity and input_path_integrity.get("nonProbeActionCommandCount") is not None:
        live_action_command_count = int(input_path_integrity.get("nonProbeActionCommandCount") or 0)
    else:
        live_action_command_count = int((arduino_summary or {}).get("actionCommandCount") or 0)
    mapping_classification = (
        (input_path_integrity or {}).get("inputPathClassification")
        if live_action_command_count
        else "conversion_trace_only"
    )
    return {
        "schema": MAPPING_SCHEMA,
        "generated_at_utc": utc_now(),
        "status": "PASS" if mouse_points else "WARN",
        "reason": None if mouse_points else "input_trace_missing_or_empty",
        "inputEventCount": len(input_events),
        "arduinoEventCount": len(arduino_events or []),
        "arduino": arduino_summary,
        "telemetry": telemetry_summary,
        "inputPathIntegrity": input_path_integrity,
        "inputPathClassification": (input_path_integrity or {}).get("inputPathClassification"),
        "mappingClassification": mapping_classification,
        "conversionTraceOnly": live_action_command_count == 0,
        "liveMirrorActive": bool(live_action_command_count),
        "liveMirrorVerified": bool((input_path_integrity or {}).get("liveMirrorVerified") or (input_path_integrity or {}).get("mirrorVerified")),
        "nonProbeActionCommandCount": live_action_command_count,
        "movementCommandCount": (input_path_integrity or {}).get("movementCommandCount"),
        "clickCommandCount": (input_path_integrity or {}).get("clickCommandCount"),
        "mirrorVerificationStatus": (input_path_integrity or {}).get("mirrorVerificationStatus"),
        "finalMirrorRecordingVerdict": (input_path_integrity or {}).get("finalMirrorRecordingVerdict"),
        "menuSelectionsAfterDisarm": (input_path_integrity or {}).get("menuSelectionsAfterDisarm"),
        "actionClicksAfterDisarm": (input_path_integrity or {}).get("actionClicksAfterDisarm"),
        "postActionArduinoCommandCount": (input_path_integrity or {}).get("postActionArduinoCommandCount"),
        "postActionMovementCommandCount": (input_path_integrity or {}).get("postActionMovementCommandCount"),
        "postActionClickCommandCount": (input_path_integrity or {}).get("postActionClickCommandCount"),
        "postActionWeirdMovementSuspected": (input_path_integrity or {}).get("postActionWeirdMovementSuspected"),
        "feedbackLoopSuspected": (input_path_integrity or {}).get("feedbackLoopSuspected"),
        "mirrorArmedStartElapsedSeconds": (input_path_integrity or {}).get("mirrorArmedStartElapsedSeconds"),
        "mirrorDisarmElapsedSeconds": (input_path_integrity or {}).get("mirrorDisarmElapsedSeconds"),
        "correlatedCommandToObservedMovementCount": (input_path_integrity or {}).get("correlatedCommandToObservedMovementCount"),
        "correlatedCommandToObservedClickCount": (input_path_integrity or {}).get("correlatedCommandToObservedClickCount"),
        "possibleDoubleInput": (input_path_integrity or {}).get("possibleDoubleInput"),
        "maxArduinoCommandsPerSecond": (input_path_integrity or {}).get("maxArduinoCommandsPerSecond"),
        "maxClickCommandsPerSecond": (input_path_integrity or {}).get("maxClickCommandsPerSecond"),
        "droppedCommandCount": (input_path_integrity or {}).get("droppedCommandCount"),
        "throttledCommandCount": (input_path_integrity or {}).get("throttledCommandCount"),
        "panicStopCount": (input_path_integrity or {}).get("panicStopCount"),
        "liveMirrorSafetyClassifications": (input_path_integrity or {}).get("liveMirrorSafetyClassifications") or [],
        "clickPolicyUsed": (input_path_integrity or {}).get("clickPolicyUsed"),
        "duplicateClickCandidateCount": (input_path_integrity or {}).get("duplicateClickCandidateCount"),
        "duplicateClickLikelyCount": (input_path_integrity or {}).get("duplicateClickLikelyCount"),
        "liveClickWithoutSuppressionCount": (input_path_integrity or {}).get("liveClickWithoutSuppressionCount"),
        "mapOnlyClickCount": (input_path_integrity or {}).get("mapOnlyClickCount"),
        "arduinoPhysicalClickCount": (input_path_integrity or {}).get("arduinoPhysicalClickCount"),
        "clickOwnershipSummary": (input_path_integrity or {}).get("clickOwnershipSummary"),
        "classificationApplied": action_classifications is not None,
        "targetQualityApplied": target_match_quality is not None,
        "mousePath": path_mapping,
        "clickMappings": [
            {
                "event_seq": click.get("event_seq"),
                "buttonSequence": click_to_arduino_button_events(click),
                "point": _event_point(click),
                "classification": classification_by_seq.get(int(click.get("event_seq") or 0), {}).get("classification"),
                "targetMatchQuality": quality_by_seq.get(int(click.get("event_seq") or 0), {}).get("quality"),
                "targetMatchScore": quality_by_seq.get(int(click.get("event_seq") or 0), {}).get("score"),
            }
            for click in strong_medium_target_clicks
        ],
        "mappedGameActionClickCount": len(game_action_clicks),
        "mappedTargetRelativeClickCount": len(target_clicks),
        "mappedStrongTargetClickCount": sum(1 for event in target_clicks if quality_by_seq.get(int(event.get("event_seq") or 0), {}).get("quality") == "strong"),
        "mappedMediumTargetClickCount": sum(1 for event in target_clicks if quality_by_seq.get(int(event.get("event_seq") or 0), {}).get("quality") == "medium"),
        "mappedWeakTargetClickCount": len(weak_target_clicks),
        "unmatchedTargetClickCount": len(unmatched_target_clicks),
        "qualityCounts": _quality_counts(quality_by_seq.values()),
        "mappedCameraDragClickCount": len(camera_drag_clicks),
        "excludedClickCount": len(excluded_clicks),
        "ambiguousClickCount": len(ambiguous_clicks),
        "mappedMouseMovementSegmentCount": path_mapping.get("segment_count"),
        "cameraDragSegments": [
            {
                "event_seq": event.get("event_seq"),
                "point": _event_point(event),
                "buttonSequence": click_to_arduino_button_events(event),
            }
            for event in camera_drag_clicks
        ],
        "weakOrUnmatchedTargetClicks": [
            {
                "event_seq": event.get("event_seq"),
                "quality": quality_by_seq.get(int(event.get("event_seq") or 0), {}).get("quality"),
                "score": quality_by_seq.get(int(event.get("event_seq") or 0), {}).get("score"),
                "warnings": quality_by_seq.get(int(event.get("event_seq") or 0), {}).get("warnings") or [],
                "reasons": quality_by_seq.get(int(event.get("event_seq") or 0), {}).get("reasons") or [],
            }
            for event in weak_target_clicks + unmatched_target_clicks
        ],
        "excludedClicks": [
            {
                "event_seq": event.get("event_seq"),
                "classification": classification_by_seq.get(int(event.get("event_seq") or 0), {}).get("classification"),
                "reasons": classification_by_seq.get(int(event.get("event_seq") or 0), {}).get("reasons") or [],
            }
            for event in excluded_clicks
        ][:20],
        "observedVsCommanded": compare_observed_to_commanded(
            {"dx": path_mapping.get("total_dx") or 0, "dy": path_mapping.get("total_dy") or 0},
            {
                "dx": sum(int(item.get("dx") or 0) for item in command_deltas),
                "dy": sum(int(item.get("dy") or 0) for item in command_deltas),
            },
        )
        if command_deltas
        else None,
        "warnings": ([] if input_events else ["input trace is missing; mapping contains no observed mouse path"])
        + (["weak_or_unmatched_target_clicks_retained_but_not_target_relative_mapped"] if weak_target_clicks or unmatched_target_clicks else []),
    }


def _quality_counts(rows: Any) -> dict[str, int]:
    counts = {"strong": 0, "medium": 0, "weak": 0, "unmatched": 0}
    for row in rows:
        if isinstance(row, dict) and row.get("quality") in counts:
            counts[str(row.get("quality"))] += 1
    return counts


def atomic_write_json(path: str | Path, payload: dict[str, Any], *, pretty: bool = True) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, default=str)
        handle.write("\n")
    temp.replace(output)


def write_mapping(path: str | Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, payload)

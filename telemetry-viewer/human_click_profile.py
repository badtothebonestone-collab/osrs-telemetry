from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROFILE_SCHEMA = "human_click_profile.v1"
RECORDING_SUMMARY_SCHEMA = "human_click_profile_recording_summary.v1"
TASK_BUCKET_SCHEMA = "human_click_profile_task_bucket.v1"

QUALITY_ORDER = ("strong", "medium", "weak", "unmatched")
ACTIVITY_BUCKETS = ("woodcutting", "route_traversal", "banking", "menu_interaction", "camera_input_sample", "generic")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except (FileNotFoundError, OSError):
        return []
    return rows


def atomic_write_json(path: str | Path, payload: dict[str, Any], *, pretty: bool = True) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, default=str)
        handle.write("\n")
    temp.replace(output)
    return output


def _stats(values: list[float]) -> dict[str, Any]:
    clean = sorted(value for value in values if isinstance(value, (int, float)) and math.isfinite(float(value)))
    if not clean:
        return {"count": 0, "min": None, "max": None, "average": None, "median": None, "p75": None, "p90": None}
    return {
        "count": len(clean),
        "min": round(clean[0], 3),
        "max": round(clean[-1], 3),
        "average": round(sum(clean) / len(clean), 3),
        "median": round(statistics.median(clean), 3),
        "p75": round(_percentile(clean, 75), 3),
        "p90": round(_percentile(clean, 90), 3),
    }


def _percentile(ordered_values: list[float], percentile: float) -> float:
    if not ordered_values:
        return 0.0
    if len(ordered_values) == 1:
        return float(ordered_values[0])
    position = (len(ordered_values) - 1) * (percentile / 100.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered_values[int(position)])
    weight = position - lower
    return float(ordered_values[lower] * (1 - weight) + ordered_values[upper] * weight)


def _distance_buckets(values: list[float], *, unknown: int = 0) -> dict[str, int]:
    buckets = {"le12": 0, "le30": 0, "le80": 0, "gt80": 0, "unknown": int(unknown)}
    for value in values:
        if value <= 12:
            buckets["le12"] += 1
        elif value <= 30:
            buckets["le30"] += 1
        elif value <= 80:
            buckets["le80"] += 1
        else:
            buckets["gt80"] += 1
    return buckets


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _target_name(row: dict[str, Any]) -> str | None:
    target = _dict(row.get("matchedTarget")) or _dict(_dict(row.get("targetContext")).get("matchedTarget"))
    return _first_present(target.get("name"), target.get("effectiveName"), _dict(row.get("targetContext")).get("targetName"))


def _target_action(row: dict[str, Any]) -> str | None:
    target = _dict(row.get("matchedTarget")) or _dict(_dict(row.get("targetContext")).get("matchedTarget"))
    actions = target.get("actions") or target.get("effectiveActions") or []
    action = actions[0] if isinstance(actions, list) and actions else None
    return _first_present(target.get("action"), action, _dict(row.get("targetContext")).get("targetAction"))


def detect_activity_buckets(summary: dict[str, Any], lifecycles: dict[str, dict[str, Any]], input_action_summary: dict[str, Any], camera_summary: dict[str, Any]) -> list[str]:
    buckets: list[str] = []
    woodcutting = _dict(lifecycles.get("woodcutting"))
    banking = _dict(lifecycles.get("banking"))
    traversal = _dict(lifecycles.get("traversal"))
    menu_count = _int(input_action_summary.get("menuSelectionClickCount"))
    if woodcutting.get("status") == "PASS" or woodcutting.get("phase") in {"chopping", "inventory_full"}:
        buckets.append("woodcutting")
    if banking.get("status") == "PASS" or _dict(banking.get("deposit")).get("detected"):
        buckets.append("banking")
    if traversal.get("status") == "PASS" and (traversal.get("routeName") not in (None, "", "route_unknown") or _int(traversal.get("successfulSegmentCount")) > 0):
        buckets.append("route_traversal")
    if menu_count > 0 or _int(_dict(summary.get("menu_interaction_summary")).get("menuSelectionCount")) > 0:
        buckets.append("menu_interaction")
    if _int(camera_summary.get("totalCameraSegments")) > 0:
        buckets.append("camera_input_sample")
    return buckets or ["generic"]


def primary_activity(buckets: list[str]) -> str:
    for name in ("woodcutting", "banking", "route_traversal", "menu_interaction", "camera_input_sample"):
        if name in buckets:
            return name
    return "generic"


def _click_point(row: dict[str, Any]) -> dict[str, Any]:
    geometry = _dict(row.get("geometry"))
    if isinstance(geometry.get("clickCanvas"), dict):
        return {"canvas": geometry.get("clickCanvas")}
    position = _dict(row.get("position"))
    return {
        "screen": position.get("screen"),
        "client": position.get("client"),
        "canvas": position.get("canvas"),
    }


def _postcondition_success(row: dict[str, Any]) -> tuple[bool, dict[str, Any], str]:
    post = _dict(row.get("postClickResult"))
    if post.get("matchedExpectedOutcome") is True:
        return True, post, "matched_expected_postcondition"
    positive = [key for key in ("planeChanged", "positionChanged", "animationStarted", "inventoryChanged", "widgetOpened", "menuClosed") if post.get(key) is True]
    if positive:
        return True, post, "postcondition_" + "_".join(positive[:3])
    menu_quality = _dict(row.get("menuSelectionQuality"))
    if menu_quality.get("insideRowBounds") is True or menu_quality.get("selectedOption") or menu_quality.get("selectedTarget"):
        return True, post, "menu_or_hover_evidence_confirmed_action"
    return False, post, "no_postcondition_evidence"


def _is_imperfect_success(row: dict[str, Any]) -> bool:
    if str(row.get("quality") or "") not in {"strong", "medium"}:
        return False
    geometry = _dict(row.get("geometry"))
    imperfect_geometry = (
        geometry.get("insideClickbox") is False
        or geometry.get("clickboxAvailable") is False
        or _float(geometry.get("distanceFromAimPointPx")) is not None
        and (_float(geometry.get("distanceFromAimPointPx")) or 0.0) > 80
        or any("geometry_missing" in str(warning) or "click_outside" in str(warning) or "target_not_on_screen" in str(warning) for warning in _list(row.get("warnings")))
    )
    if not imperfect_geometry:
        return False
    success, _post, _reason = _postcondition_success(row)
    return success


def mouse_path_summary(input_events: list[dict[str, Any]]) -> dict[str, Any]:
    point_events = []
    for event in input_events:
        kind = str(event.get("kind") or "")
        if kind not in {"move", "mouse_move", "drag_move", "drag_start", "drag_end"}:
            continue
        x = _float(_first_present(event.get("screen_x"), event.get("raw_screen_x"), _dict(event.get("screen")).get("x")))
        y = _float(_first_present(event.get("screen_y"), event.get("raw_screen_y"), _dict(event.get("screen")).get("y")))
        elapsed = _float(event.get("elapsed_seconds"))
        if x is None or y is None or elapsed is None:
            continue
        point_events.append({"x": x, "y": y, "elapsed": elapsed, "kind": kind})
    distances: list[float] = []
    speeds: list[float] = []
    pauses = 0
    total = 0.0
    previous = None
    for event in point_events:
        if previous:
            dx = event["x"] - previous["x"]
            dy = event["y"] - previous["y"]
            dt = event["elapsed"] - previous["elapsed"]
            distance = math.hypot(dx, dy)
            if distance > 0:
                distances.append(distance)
                total += distance
                if dt > 0:
                    speeds.append(distance / dt)
            if dt > 0.25:
                pauses += 1
        previous = event
    examples = []
    if point_events:
        examples.append({"start": point_events[0], "end": point_events[-1], "pathLengthPx": round(total, 3)})
    return {
        "movementSegments": len(distances),
        "mouseMoveEventCount": len(point_events),
        "pathLengthPx": round(total, 3),
        "pathSegmentDistancePx": _stats(distances),
        "speedPxPerSec": _stats(speeds),
        "pauseCount": pauses,
        "examples": examples[:3],
    }


def analyze_recording(recording: str | Path) -> dict[str, Any]:
    root = Path(recording)
    summary = _read_json(root / "summary.json")
    input_action_summary = _read_json(root / "input_action_summary.json")
    target_summary = _read_json(root / "target_match_summary.json")
    menu_summary = _read_json(root / "menu_interaction_summary.json")
    camera_summary = _read_json(root / "camera_behavior_summary.json")
    coordinate_summary = _read_json(root / "coordinate_alignment_summary.json")
    vm_mapping = _read_json(root / "vm_mouse_arduino_mapping.json")
    lifecycles = {
        "woodcutting": _read_json(root / "woodcutting_lifecycle.json"),
        "banking": _read_json(root / "banking_lifecycle.json"),
        "traversal": _read_json(root / "traversal_lifecycle.json"),
    }
    input_events = _read_jsonl(root / "input_events.jsonl")
    classifications = _read_jsonl(root / "input_action_classifications.jsonl")
    target_rows = _read_jsonl(root / "target_match_quality.jsonl")
    joined_rows = _read_jsonl(root / "joined_input_telemetry.jsonl")
    target_rows_by_event_seq = {row.get("eventSeq"): row for row in target_rows if row.get("eventSeq") is not None}

    buckets = detect_activity_buckets(summary, lifecycles, input_action_summary, camera_summary)
    quality_counts = Counter(str(row.get("quality") or "unmatched") for row in target_rows)
    for quality in QUALITY_ORDER:
        quality_counts.setdefault(quality, 0)

    distances: list[float] = []
    row_distances: list[float] = []
    clickbox_counts = Counter({"inside": 0, "outside": 0, "unknown": 0, "unavailable": 0})
    menu_row_counts = Counter({"inside": 0, "outside": 0, "unknown": 0, "missingBounds": 0})
    imperfect_examples: list[dict[str, Any]] = []
    hover_examples: list[dict[str, Any]] = []

    for row in target_rows:
        geometry = _dict(row.get("geometry"))
        distance = _float(geometry.get("distanceFromAimPointPx"))
        if distance is not None:
            distances.append(distance)
        if geometry.get("clickboxAvailable") is False:
            clickbox_counts["unavailable"] += 1
        elif geometry.get("insideClickbox") is True:
            clickbox_counts["inside"] += 1
        elif geometry.get("insideClickbox") is False:
            clickbox_counts["outside"] += 1
        else:
            clickbox_counts["unknown"] += 1
        menu_quality = _dict(row.get("menuSelectionQuality"))
        if menu_quality:
            if menu_quality.get("rowBounds"):
                if menu_quality.get("insideRowBounds") is True:
                    menu_row_counts["inside"] += 1
                elif menu_quality.get("insideRowBounds") is False:
                    menu_row_counts["outside"] += 1
                else:
                    menu_row_counts["unknown"] += 1
            else:
                menu_row_counts["missingBounds"] += 1
            row_distance = _float(menu_quality.get("rowCenterDistancePx"))
            if row_distance is not None:
                row_distances.append(row_distance)
        if _is_imperfect_success(row):
            success, post, reason = _postcondition_success(row)
            imperfect_examples.append(
                {
                    "recording": root.name,
                    "eventSeq": row.get("eventSeq"),
                    "action": _target_action(row),
                    "target": _target_name(row),
                    "classification": row.get("classification"),
                    "targetQuality": row.get("quality"),
                    "targetScore": row.get("score"),
                    "clickPoint": _click_point(row),
                    "aimDistancePx": distance,
                    "geometryStatus": {
                        "insideClickbox": geometry.get("insideClickbox"),
                        "clickboxAvailable": geometry.get("clickboxAvailable"),
                        "targetOnScreen": geometry.get("targetOnScreen"),
                    },
                    "postcondition": post,
                    "whyItStillSucceeded": reason if success else "quality_only",
                    "warnings": _list(row.get("warnings"))[:6],
                }
            )

    hover_durations: list[float] = []
    for row in joined_rows:
        click_analysis = _dict(row.get("clickAnalysis"))
        hover = _dict(click_analysis.get("hoverBeforeClick"))
        duration = _float(hover.get("durationMs"))
        if duration is not None:
            hover_durations.append(duration)
    for row in classifications:
        menu_context = _dict(row.get("menuContext"))
        if menu_context.get("hoverOption") or menu_context.get("hoverTarget"):
            resolved_target_row = _dict(target_rows_by_event_seq.get(row.get("eventSeq")))
            hover_examples.append(
                {
                    "recording": root.name,
                    "eventSeq": row.get("eventSeq"),
                    "classification": row.get("classification"),
                    "hoverOption": menu_context.get("hoverOption"),
                    "hoverTarget": menu_context.get("hoverTarget"),
                    "targetName": _target_name(resolved_target_row) or _dict(row.get("targetContext")).get("targetName"),
                    "targetAction": _target_action(resolved_target_row) or _dict(row.get("targetContext")).get("targetAction"),
                    "targetAssociation": _dict(resolved_target_row.get("targetAssociation")).get("associationMethod"),
                }
            )

    camera_segments = _list(camera_summary.get("segments"))
    camera_to_click_values = []
    camera_examples = []
    for segment in camera_segments:
        click = _dict(segment.get("nextClick"))
        end_time = _float(segment.get("endTime"))
        click_time = _float(click.get("elapsed_seconds"))
        if end_time is not None and click_time is not None:
            camera_to_click_values.append((click_time - end_time) * 1000.0)
        camera_examples.append(
            {
                "recording": root.name,
                "segmentId": segment.get("segmentId"),
                "source": segment.get("source"),
                "durationMs": segment.get("durationMs"),
                "deltaYaw": segment.get("deltaYaw"),
                "deltaPitch": segment.get("deltaPitch"),
                "mouseDrag": segment.get("mouseDrag"),
                "nextClickEventSeq": click.get("event_seq"),
                "nextClickTargetName": segment.get("nextClickTargetName"),
                "nextClickTargetAction": segment.get("nextClickTargetAction"),
                "nextClickTargetQuality": segment.get("nextClickTargetQuality"),
            }
        )

    woodcutting = _dict(lifecycles.get("woodcutting"))
    banking = _dict(lifecycles.get("banking"))
    traversal = _dict(lifecycles.get("traversal"))
    woodcutting_clicks = _dict(woodcutting.get("clicks"))
    woodcutting_inventory = _dict(woodcutting.get("inventory"))
    banking_deposit = _dict(banking.get("deposit"))
    banking_bank = _dict(banking.get("bank"))
    traversal_movement = _dict(traversal.get("movement"))

    warnings = []
    missing = []
    if not target_rows:
        warnings.append(f"{root.name}: target_match_quality.jsonl missing or empty")
        missing.append("target_match_quality")
    if not classifications:
        warnings.append(f"{root.name}: input_action_classifications.jsonl missing or empty")
        missing.append("input_action_classifications")
    if not camera_summary:
        warnings.append(f"{root.name}: camera_behavior_summary.json missing")
        missing.append("camera_behavior_summary")

    return {
        "schema": RECORDING_SUMMARY_SCHEMA,
        "status": "PASS" if target_rows or classifications or camera_segments else "WARN",
        "recordingId": root.name,
        "recordingPath": str(root),
        "activityBuckets": buckets,
        "primaryActivity": primary_activity(buckets),
        "durationSeconds": summary.get("duration_seconds"),
        "clicks": {
            "rawClickCount": _int(input_action_summary.get("rawOsClickCount")),
            "eligibleGameActionClicks": _int(input_action_summary.get("eligibleGameActionClickCount")),
            "targetRelativeClicks": _int(input_action_summary.get("targetRelativeClickCount")) or len(target_rows),
            "strongTargetClicks": quality_counts.get("strong", 0),
            "mediumTargetClicks": quality_counts.get("medium", 0),
            "weakTargetClicks": quality_counts.get("weak", 0),
            "unmatchedTargetClicks": quality_counts.get("unmatched", 0),
            "insideClickboxCount": clickbox_counts["inside"],
            "outsideRecoveredGeometryCount": clickbox_counts["outside"],
            "unknownClickboxCount": clickbox_counts["unknown"],
            "unavailableClickboxCount": clickbox_counts["unavailable"],
            "menuRowSelectionCount": _int(input_action_summary.get("menuSelectionClickCount")),
            "rightClickMenuOpenCount": _int(input_action_summary.get("rightClickMenuOpenCount")),
            "duplicateClickLikelyCount": _int(_dict(summary.get("click_ownership_summary")).get("duplicateClickLikelyCount")),
            "classificationCounts": input_action_summary.get("classificationCounts") or {},
        },
        "landing": {
            "aimDistancesPx": distances,
            "aimDistancePx": _stats(distances),
            "aimDistanceBucketsPx": _distance_buckets(distances),
            "menuRowCenterDistancePx": _stats(row_distances),
            "menuRowCounts": dict(menu_row_counts),
        },
        "hover": {
            "hoverBeforeClickSamples": len(hover_durations),
            "hoverBeforeClickMs": _stats(hover_durations),
            "examples": hover_examples[:8],
        },
        "camera": {
            "cameraSegmentCount": _int(camera_summary.get("totalCameraSegments")),
            "middleMouseDragCount": _int(camera_summary.get("middleMouseDragSegments")),
            "arrowKeyCameraSegmentCount": _int(camera_summary.get("arrowKeyCameraSegments")),
            "cameraBeforeClickCount": _int(camera_summary.get("cameraBeforeClickCount")),
            "cameraBeforeStrongOrMediumClickCount": _int(camera_summary.get("cameraBeforeStrongOrMediumClickCount")),
            "cameraToClickMs": _stats(camera_to_click_values),
            "averageCameraSegmentDurationMs": camera_summary.get("averageCameraSegmentDurationMs"),
            "averageYawDelta": camera_summary.get("averageYawDelta"),
            "averagePitchDelta": camera_summary.get("averagePitchDelta"),
            "examples": camera_examples[:8],
        },
        "mousePath": mouse_path_summary(input_events),
        "woodcutting": {
            "status": woodcutting.get("status"),
            "phase": woodcutting.get("phase"),
            "confidence": woodcutting.get("confidence"),
            "freshChopClickCount": _first_present(woodcutting_clicks.get("freshChopClickCount"), woodcutting_clicks.get("freshChopDownClickCount")),
            "inputActionChopClickCount": woodcutting_clicks.get("inputActionChopClickCount"),
            "inputTreeTargetEvidenceCount": woodcutting_clicks.get("inputTreeTargetEvidenceCount"),
            "normalLogsGained": woodcutting_inventory.get("normalLogsGained"),
            "inventoryFull": woodcutting_inventory.get("inventoryFull"),
            "animationActiveSnapshotCount": _dict(woodcutting.get("animation")).get("activeSnapshotCount"),
        },
        "banking": {
            "status": banking.get("status"),
            "phase": banking.get("phase"),
            "confidence": banking.get("confidence"),
            "bankOpenSeen": banking_bank.get("openSeen"),
            "depositBoxOpenSeen": banking_bank.get("depositBoxOpenSeen"),
            "bankUiPresent": banking_bank.get("bankUiPresent"),
            "bankContainerAvailable": banking_bank.get("containerAvailable"),
            "bankContainerDeltaAvailable": banking.get("bankContainerDeltaAvailable"),
            "depositedItems": banking_deposit.get("items") or [],
            "depositConfirmationLevel": banking.get("depositConfirmationLevel"),
        },
        "traversal": {
            "status": traversal.get("status"),
            "routeName": traversal.get("routeName"),
            "phase": traversal.get("phase"),
            "confidence": traversal.get("confidence"),
            "routeSegmentCount": traversal.get("routeSegmentCount"),
            "successfulSegmentCount": traversal.get("successfulSegmentCount"),
            "partialSegmentCount": traversal.get("partialSegmentCount"),
            "reviewEvidenceCount": traversal.get("reviewEvidenceCount"),
            "planeChangeCount": len(_list(traversal_movement.get("planeChanges"))),
            "distanceApprox": traversal_movement.get("distanceApprox"),
            "routeSegments": _list(traversal.get("routeSegments"))[:8],
        },
        "imperfectSuccessfulClicks": imperfect_examples[:12],
        "imperfectSuccessfulClickCount": len(imperfect_examples),
        "warnings": warnings,
        "missingCapabilities": missing,
        "artifactStatus": {
            "inputEvents": bool(input_events),
            "inputActionClassifications": bool(classifications),
            "targetMatchQuality": bool(target_rows),
            "cameraBehavior": bool(camera_summary),
            "menuInteractions": bool(menu_summary),
            "coordinateAlignment": bool(coordinate_summary),
            "vmMouseArduinoMapping": bool(vm_mapping),
        },
    }


def _aggregate_counts(recordings: list[dict[str, Any]], path: list[str]) -> int:
    total = 0
    for recording in recordings:
        value: Any = recording
        for key in path:
            value = _dict(value).get(key)
        total += _int(value)
    return total


def _collect_numbers(recordings: list[dict[str, Any]], path: list[str]) -> list[float]:
    values: list[float] = []
    for recording in recordings:
        value: Any = recording
        for key in path:
            value = _dict(value).get(key)
        if isinstance(value, list):
            values.extend(float(item) for item in value if isinstance(item, (int, float)))
            continue
        numeric = _float(value)
        if numeric is not None:
            values.append(numeric)
    return values


def build_task_bucket(name: str, recordings: list[dict[str, Any]]) -> dict[str, Any]:
    quality_total = sum(_int(_dict(recording.get("clicks")).get(f"{quality}TargetClicks")) for recording in recordings for quality in ("strong", "medium", "weak"))
    strong_medium = sum(_int(_dict(recording.get("clicks")).get(f"{quality}TargetClicks")) for recording in recordings for quality in ("strong", "medium"))
    camera_segments = _aggregate_counts(recordings, ["camera", "cameraSegmentCount"])
    camera_before = _aggregate_counts(recordings, ["camera", "cameraBeforeClickCount"])
    aim_values = []
    for recording in recordings:
        aim_values.extend(_list(_dict(recording.get("landing")).get("aimDistancesPx")))
    examples = []
    for recording in recordings:
        examples.extend(_list(recording.get("imperfectSuccessfulClicks"))[:2])
    bucket = {
        "schema": TASK_BUCKET_SCHEMA,
        "activity": name,
        "recordingCount": len(recordings),
        "clickCount": _aggregate_counts(recordings, ["clicks", "rawClickCount"]),
        "targetRelativeClickCount": _aggregate_counts(recordings, ["clicks", "targetRelativeClicks"]),
        "strongTargetClickCount": _aggregate_counts(recordings, ["clicks", "strongTargetClicks"]),
        "mediumTargetClickCount": _aggregate_counts(recordings, ["clicks", "mediumTargetClicks"]),
        "weakTargetClickCount": _aggregate_counts(recordings, ["clicks", "weakTargetClicks"]),
        "strongOrMediumTargetRate": round(strong_medium / quality_total, 3) if quality_total else None,
        "menuRowSelectionCount": _aggregate_counts(recordings, ["clicks", "menuRowSelectionCount"]),
        "rightClickMenuOpenCount": _aggregate_counts(recordings, ["clicks", "rightClickMenuOpenCount"]),
        "hoverBeforeClickMs": _stats([value for recording in recordings for value in _collect_numbers([recording], ["hover", "hoverBeforeClickMs", "median"])]),
        "clickLandingDistancePx": _stats([float(value) for value in aim_values if isinstance(value, (int, float))]),
        "cameraBeforeClickFrequency": round(camera_before / camera_segments, 3) if camera_segments else None,
        "imperfectSuccessfulClickCount": sum(_int(recording.get("imperfectSuccessfulClickCount")) for recording in recordings),
        "usefulImperfectExamples": examples[:6],
        "warnings": sorted({warning for recording in recordings for warning in _list(recording.get("warnings"))}),
        "missingCapabilities": sorted({cap for recording in recordings for cap in _list(recording.get("missingCapabilities"))}),
    }
    if name == "woodcutting":
        bucket["woodcutting"] = {
            "freshChopClickCount": _aggregate_counts(recordings, ["woodcutting", "freshChopClickCount"]),
            "inputActionChopClickCount": _aggregate_counts(recordings, ["woodcutting", "inputActionChopClickCount"]),
            "inputTreeTargetEvidenceCount": _aggregate_counts(recordings, ["woodcutting", "inputTreeTargetEvidenceCount"]),
            "animation879Recordings": sum(1 for recording in recordings if _int(_dict(recording.get("woodcutting")).get("animationActiveSnapshotCount")) > 0),
            "inventoryFullRecordings": sum(1 for recording in recordings if _dict(recording.get("woodcutting")).get("inventoryFull") is True),
            "normalLogsGainedTotal": _aggregate_counts(recordings, ["woodcutting", "normalLogsGained"]),
        }
    elif name == "banking":
        deposited = []
        for recording in recordings:
            deposited.extend(_list(_dict(recording.get("banking")).get("depositedItems")))
        bucket["banking"] = {
            "bankOpenSeenRecordings": sum(1 for recording in recordings if _dict(recording.get("banking")).get("bankOpenSeen") is True),
            "bankUiPresentRecordings": sum(1 for recording in recordings if _dict(recording.get("banking")).get("bankUiPresent") is True),
            "bankContainerAvailableRecordings": sum(1 for recording in recordings if _dict(recording.get("banking")).get("bankContainerAvailable") is True),
            "bankContainerDeltaAvailableRecordings": sum(1 for recording in recordings if _dict(recording.get("banking")).get("bankContainerDeltaAvailable") is True),
            "depositedItems": deposited[:12],
        }
    elif name == "route_traversal":
        bucket["traversal"] = {
            "routeNames": sorted({str(_dict(recording.get("traversal")).get("routeName")) for recording in recordings if _dict(recording.get("traversal")).get("routeName")}),
            "routeSegmentCount": _aggregate_counts(recordings, ["traversal", "routeSegmentCount"]),
            "successfulSegmentCount": _aggregate_counts(recordings, ["traversal", "successfulSegmentCount"]),
            "planeChangeCount": _aggregate_counts(recordings, ["traversal", "planeChangeCount"]),
            "distanceApproxTotal": round(sum(_float(_dict(recording.get("traversal")).get("distanceApprox")) or 0.0 for recording in recordings), 3),
        }
    return bucket


def aggregate_recording_summaries(recording_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for recording in recording_summaries:
        for bucket in _list(recording.get("activityBuckets")) or ["generic"]:
            by_bucket[str(bucket)].append(recording)
    all_distances = [value for recording in recording_summaries for value in _list(_dict(recording.get("landing")).get("aimDistancesPx")) if isinstance(value, (int, float))]
    clickbox_counts = Counter()
    menu_row_counts = Counter()
    classification_counts = Counter()
    quality_counts = Counter()
    imperfect_examples = []
    camera_examples = []
    hover_examples = []
    warnings = set()
    missing = set()
    for recording in recording_summaries:
        clicks = _dict(recording.get("clicks"))
        landing = _dict(recording.get("landing"))
        clickbox_counts.update(
            {
                "inside": _int(clicks.get("insideClickboxCount")),
                "outside": _int(clicks.get("outsideRecoveredGeometryCount")),
                "unknown": _int(clicks.get("unknownClickboxCount")),
                "unavailable": _int(clicks.get("unavailableClickboxCount")),
            }
        )
        menu_row_counts.update(_dict(landing.get("menuRowCounts")))
        classification_counts.update(_dict(clicks.get("classificationCounts")))
        quality_counts.update(
            {
                "strong": _int(clicks.get("strongTargetClicks")),
                "medium": _int(clicks.get("mediumTargetClicks")),
                "weak": _int(clicks.get("weakTargetClicks")),
                "unmatched": _int(clicks.get("unmatchedTargetClicks")),
            }
        )
        imperfect_examples.extend(_list(recording.get("imperfectSuccessfulClicks")))
        camera_examples.extend(_list(_dict(recording.get("camera")).get("examples")))
        hover_examples.extend(_list(_dict(recording.get("hover")).get("examples")))
        warnings.update(str(warning) for warning in _list(recording.get("warnings")))
        missing.update(str(cap) for cap in _list(recording.get("missingCapabilities")))
    camera_to_click = []
    hover_medians = []
    path_lengths = []
    speeds = []
    for recording in recording_summaries:
        camera_to_click.extend(_collect_numbers([recording], ["camera", "cameraToClickMs", "median"]))
        hover_medians.extend(_collect_numbers([recording], ["hover", "hoverBeforeClickMs", "median"]))
        path_lengths.extend(_collect_numbers([recording], ["mousePath", "pathLengthPx"]))
        speeds.extend(_collect_numbers([recording], ["mousePath", "speedPxPerSec", "median"]))
    target_relative = _aggregate_counts(recording_summaries, ["clicks", "targetRelativeClicks"])
    raw_clicks = _aggregate_counts(recording_summaries, ["clicks", "rawClickCount"])
    task_profiles = {name: build_task_bucket(name, rows) for name, rows in sorted(by_bucket.items())}
    status = "PASS" if recording_summaries and target_relative else "WARN" if recording_summaries else "FAIL"
    if not target_relative:
        warnings.add("No target-relative clicks were available in the aggregated recordings.")
    return {
        "schema": PROFILE_SCHEMA,
        "status": status,
        "generatedAtUtc": utc_now(),
        "recordingCount": len(recording_summaries),
        "activityBuckets": sorted(by_bucket.keys()),
        "recordings": recording_summaries,
        "clicks": {
            "rawClickCount": raw_clicks,
            "eligibleGameActionClicks": _aggregate_counts(recording_summaries, ["clicks", "eligibleGameActionClicks"]),
            "targetRelativeClicks": target_relative,
            "strongTargetClicks": quality_counts.get("strong", 0),
            "mediumTargetClicks": quality_counts.get("medium", 0),
            "weakTargetClicks": quality_counts.get("weak", 0),
            "unmatchedTargetClicks": quality_counts.get("unmatched", 0),
            "insideClickboxCount": clickbox_counts.get("inside", 0),
            "outsideRecoveredGeometryCount": clickbox_counts.get("outside", 0),
            "unknownClickboxCount": clickbox_counts.get("unknown", 0),
            "unavailableClickboxCount": clickbox_counts.get("unavailable", 0),
            "menuRowSelectionCount": _aggregate_counts(recording_summaries, ["clicks", "menuRowSelectionCount"]),
            "rightClickMenuOpenCount": _aggregate_counts(recording_summaries, ["clicks", "rightClickMenuOpenCount"]),
            "duplicateClickLikelyCount": _aggregate_counts(recording_summaries, ["clicks", "duplicateClickLikelyCount"]),
            "classificationCounts": dict(sorted(classification_counts.items())),
            "qualityCounts": {quality: quality_counts.get(quality, 0) for quality in QUALITY_ORDER},
        },
        "landing": {
            "aimDistanceBucketsPx": _distance_buckets([float(value) for value in all_distances]),
            "aimDistancePx": _stats([float(value) for value in all_distances]),
            "medianAimDistancePx": _stats([float(value) for value in all_distances]).get("median"),
            "p75AimDistancePx": _stats([float(value) for value in all_distances]).get("p75"),
            "p90AimDistancePx": _stats([float(value) for value in all_distances]).get("p90"),
            "clickboxCounts": dict(clickbox_counts),
            "menuRowCounts": dict(menu_row_counts),
            "examples": imperfect_examples[:8],
        },
        "hover": {
            "hoverBeforeClickSamples": sum(_int(_dict(recording.get("hover")).get("hoverBeforeClickSamples")) for recording in recording_summaries),
            "medianHoverMs": _stats(hover_medians).get("median"),
            "hoverBeforeClickMs": _stats(hover_medians),
            "examples": hover_examples[:8],
        },
        "camera": {
            "cameraSegmentCount": _aggregate_counts(recording_summaries, ["camera", "cameraSegmentCount"]),
            "middleMouseDragCount": _aggregate_counts(recording_summaries, ["camera", "middleMouseDragCount"]),
            "cameraBeforeClickCount": _aggregate_counts(recording_summaries, ["camera", "cameraBeforeClickCount"]),
            "medianCameraToClickMs": _stats(camera_to_click).get("median"),
            "cameraToClickMs": _stats(camera_to_click),
            "examples": camera_examples[:8],
        },
        "mousePath": {
            "movementSegments": _aggregate_counts(recording_summaries, ["mousePath", "movementSegments"]),
            "medianPathLengthPx": _stats(path_lengths).get("median"),
            "medianSpeedPxPerSec": _stats(speeds).get("median"),
            "pauseCount": _aggregate_counts(recording_summaries, ["mousePath", "pauseCount"]),
            "examples": [example for recording in recording_summaries for example in _list(_dict(recording.get("mousePath")).get("examples"))][:6],
        },
        "taskProfiles": task_profiles,
        "imperfectSuccessfulClickCount": sum(_int(recording.get("imperfectSuccessfulClickCount")) for recording in recording_summaries),
        "imperfectSuccessfulClicks": imperfect_examples[:12],
        "warnings": sorted(warnings),
        "missingCapabilities": sorted(missing),
        "exampleRecordings": [
            {
                "recordingId": recording.get("recordingId"),
                "primaryActivity": recording.get("primaryActivity"),
                "status": recording.get("status"),
                "path": recording.get("recordingPath"),
            }
            for recording in recording_summaries[:12]
        ],
    }


def analyze_recordings(recordings: list[str | Path]) -> dict[str, Any]:
    summaries = [analyze_recording(path) for path in recordings]
    return aggregate_recording_summaries(summaries)


def compact_profile(profile: dict[str, Any], *, activity: str | None = None) -> dict[str, Any]:
    task_profiles = _dict(profile.get("taskProfiles"))
    bucket = _dict(task_profiles.get(activity)) if activity else {}
    return {
        "schema": "human_click_profile_compact.v1",
        "status": profile.get("status"),
        "recordingCount": profile.get("recordingCount"),
        "activityBuckets": profile.get("activityBuckets") or [],
        "clicks": {
            "rawClickCount": _dict(profile.get("clicks")).get("rawClickCount"),
            "targetRelativeClicks": _dict(profile.get("clicks")).get("targetRelativeClicks"),
            "strongTargetClicks": _dict(profile.get("clicks")).get("strongTargetClicks"),
            "mediumTargetClicks": _dict(profile.get("clicks")).get("mediumTargetClicks"),
            "weakTargetClicks": _dict(profile.get("clicks")).get("weakTargetClicks"),
            "menuRowSelectionCount": _dict(profile.get("clicks")).get("menuRowSelectionCount"),
            "rightClickMenuOpenCount": _dict(profile.get("clicks")).get("rightClickMenuOpenCount"),
        },
        "landing": {
            "medianAimDistancePx": _dict(profile.get("landing")).get("medianAimDistancePx"),
            "p75AimDistancePx": _dict(profile.get("landing")).get("p75AimDistancePx"),
            "p90AimDistancePx": _dict(profile.get("landing")).get("p90AimDistancePx"),
            "aimDistanceBucketsPx": _dict(profile.get("landing")).get("aimDistanceBucketsPx"),
            "clickboxCounts": _dict(profile.get("landing")).get("clickboxCounts"),
        },
        "camera": {
            "cameraSegmentCount": _dict(profile.get("camera")).get("cameraSegmentCount"),
            "middleMouseDragCount": _dict(profile.get("camera")).get("middleMouseDragCount"),
            "cameraBeforeClickCount": _dict(profile.get("camera")).get("cameraBeforeClickCount"),
            "medianCameraToClickMs": _dict(profile.get("camera")).get("medianCameraToClickMs"),
        },
        "mousePath": {
            "medianPathLengthPx": _dict(profile.get("mousePath")).get("medianPathLengthPx"),
            "medianSpeedPxPerSec": _dict(profile.get("mousePath")).get("medianSpeedPxPerSec"),
            "pauseCount": _dict(profile.get("mousePath")).get("pauseCount"),
        },
        "taskProfile": bucket or None,
        "imperfectSuccessfulClickCount": profile.get("imperfectSuccessfulClickCount"),
        "warnings": (profile.get("warnings") or [])[:8],
        "missingCapabilities": (profile.get("missingCapabilities") or [])[:8],
    }


def resolve_profile_path(source: Any = None) -> Path | None:
    if source:
        path = Path(str(source)).expanduser()
        if path.is_dir():
            candidate = path / "human_click_profile.json"
            if candidate.exists():
                return candidate
        if path.exists():
            return path
    default = Path(__file__).resolve().parent / "knowledge_base" / "human_click_profile.json"
    return default if default.exists() else None


def load_profile(source: Any = None) -> dict[str, Any]:
    path = resolve_profile_path(source)
    return _read_json(path) if path else {}


def write_profile(profile: dict[str, Any], out: str | Path, *, pretty: bool = True) -> Path:
    output = Path(out)
    if output.suffix.lower() != ".json":
        output = output / "human_click_profile.json"
    return atomic_write_json(output, profile, pretty=pretty)


def render_markdown(profile: dict[str, Any], *, title: str = "Human Click Profile") -> str:
    clicks = _dict(profile.get("clicks"))
    landing = _dict(profile.get("landing"))
    camera = _dict(profile.get("camera"))
    mouse = _dict(profile.get("mousePath"))
    lines = [
        f"# {title}",
        "",
        f"Schema: `{profile.get('schema')}`",
        f"Status: `{profile.get('status')}`",
        f"Generated: `{profile.get('generatedAtUtc')}`",
        f"Recordings: `{profile.get('recordingCount')}`",
        f"Activity buckets: `{', '.join(profile.get('activityBuckets') or [])}`",
        "",
        "## Recordings Included",
    ]
    for item in _list(profile.get("exampleRecordings")):
        lines.append(f"- `{item.get('recordingId')}`: `{item.get('primaryActivity')}` `{item.get('status')}`")
    lines.extend(
        [
            "",
            "## Overall Click Profile",
            f"- Raw clicks: `{clicks.get('rawClickCount')}`",
            f"- Target-relative clicks: `{clicks.get('targetRelativeClicks')}`",
            f"- Strong / medium / weak: `{clicks.get('strongTargetClicks')}` / `{clicks.get('mediumTargetClicks')}` / `{clicks.get('weakTargetClicks')}`",
            f"- Menu row selections: `{clicks.get('menuRowSelectionCount')}`",
            f"- Right-click menu opens: `{clicks.get('rightClickMenuOpenCount')}`",
            f"- Duplicate likely clicks: `{clicks.get('duplicateClickLikelyCount')}`",
            "",
            "## Click Landing Quality",
            f"- Aim distance median / p75 / p90 px: `{landing.get('medianAimDistancePx')}` / `{landing.get('p75AimDistancePx')}` / `{landing.get('p90AimDistancePx')}`",
            f"- Aim buckets: `{landing.get('aimDistanceBucketsPx')}`",
            f"- Clickbox counts: `{landing.get('clickboxCounts')}`",
            f"- Menu row counts: `{landing.get('menuRowCounts')}`",
            "",
            "## Hover / Menu Behavior",
            f"- Hover samples: `{_dict(profile.get('hover')).get('hoverBeforeClickSamples')}`",
            f"- Median hover before click ms: `{_dict(profile.get('hover')).get('medianHoverMs')}`",
            "",
            "## Camera Behavior",
            f"- Camera segments: `{camera.get('cameraSegmentCount')}`",
            f"- Middle-mouse drags: `{camera.get('middleMouseDragCount')}`",
            f"- Camera-before-click count: `{camera.get('cameraBeforeClickCount')}`",
            f"- Median camera-to-click ms: `{camera.get('medianCameraToClickMs')}`",
            "",
            "## Mouse Path",
            f"- Movement segments: `{mouse.get('movementSegments')}`",
            f"- Median path length px: `{mouse.get('medianPathLengthPx')}`",
            f"- Median speed px/sec: `{mouse.get('medianSpeedPxPerSec')}`",
            f"- Pause count: `{mouse.get('pauseCount')}`",
        ]
    )
    for section, label in (("woodcutting", "Woodcutting Profile"), ("banking", "Banking Profile"), ("route_traversal", "Traversal Profile"), ("menu_interaction", "Menu Profile")):
        bucket = _dict(_dict(profile.get("taskProfiles")).get(section))
        if not bucket:
            continue
        lines.extend(
            [
                "",
                f"## {label}",
                f"- Recordings: `{bucket.get('recordingCount')}`",
                f"- Target-relative clicks: `{bucket.get('targetRelativeClickCount')}`",
                f"- Strong/medium rate: `{bucket.get('strongOrMediumTargetRate')}`",
                f"- Menu selections: `{bucket.get('menuRowSelectionCount')}`",
                f"- Imperfect successful clicks: `{bucket.get('imperfectSuccessfulClickCount')}`",
            ]
        )
        extra = _dict(bucket.get(section))
        for key, value in extra.items():
            lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Imperfect But Successful Clicks"])
    examples = _list(profile.get("imperfectSuccessfulClicks"))
    if not examples:
        lines.append("- none")
    for item in examples[:10]:
        lines.append(
            f"- `{item.get('recording')}` event `{item.get('eventSeq')}`: `{item.get('action')}` `{item.get('target')}` "
            f"quality=`{item.get('targetQuality')}` distance=`{item.get('aimDistancePx')}` reason=`{item.get('whyItStillSucceeded')}`"
        )
    lines.extend(["", "## Missing Data / Caveats"])
    warnings = _list(profile.get("warnings"))
    missing = _list(profile.get("missingCapabilities"))
    if not warnings and not missing:
        lines.append("- none")
    for item in warnings[:12]:
        lines.append(f"- warning: {item}")
    for item in missing[:12]:
        lines.append(f"- missing: {item}")
    lines.extend(
        [
            "",
            "## Script-Facing Recommendations",
            "- Prefer strong/medium target-quality evidence over exact clickbox-center replication.",
            "- Allow target-relative variance; outside recovered geometry can still be successful when menu or postcondition evidence proves the action.",
            "- Preserve hover/menu context because it explains many human menu selections and slightly messy clicks.",
            "- Treat camera adjustment as useful pre-action evidence, especially for route transitions and tree visibility.",
            "- Use routeSegments/world/plane for route proof instead of raw click counts.",
            "- Use bank_ui and bank container delta when judging banking; input region labels alone can be misleading.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_markdown(profile: dict[str, Any], markdown: str | Path, *, title: str = "Human Click Profile") -> Path:
    output = Path(markdown)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(profile, title=title), encoding="utf-8")
    return output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate human click/camera behavior from Record Everything recordings.")
    parser.add_argument("--recordings", nargs="+", required=True, help="Recording folders to include.")
    parser.add_argument("--out", default="telemetry-viewer/knowledge_base/human_click_profile.json", help="Output JSON file or directory.")
    parser.add_argument("--markdown", help="Optional Markdown report path.")
    parser.add_argument("--print", dest="print_profile", action="store_true", help="Print profile JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    profile = analyze_recordings([Path(item) for item in args.recordings])
    write_profile(profile, args.out)
    if args.markdown:
        write_markdown(profile, args.markdown)
    if args.print_profile:
        print(json.dumps(profile, indent=2, default=str))
    return 0 if profile.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

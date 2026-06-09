from __future__ import annotations

import json
import math
import os
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import arduino_input_bridge
import arduino_live_mirror
import arduino_mirror_verifier
import camera_behavior
import coordinate_spaces
import input_action_classifier
import input_trace_recorder
import menu_interaction_model
import target_match_quality
import telemetry_schema
import vm_mouse_arduino_mapper


JOINED_EVENT_SCHEMA = "joined_input_telemetry_event.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _command_name(record: dict[str, Any]) -> str:
    return str(record.get("command") or record.get("command_kind") or record.get("commandKind") or "").upper()


def _source_seq(record: dict[str, Any]) -> Any:
    return record.get("sourceInputEventSeq", record.get("source_event_seq"))


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict):
                    records.append(decoded)
    except OSError:
        return []
    return records


def load_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def atomic_write_json(path: str | Path, payload: dict[str, Any], *, pretty: bool = True) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, default=str)
        handle.write("\n")
    temp.replace(output)


def write_jsonl(path: str | Path, records: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")


def telemetry_snapshots(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshots = []
    for event in events:
        if event.get("event_type") != "source_snapshot":
            continue
        high = _dict(event.get("high_value_fields"))
        snapshots.append(
            {
                "elapsed_seconds": _float(event.get("elapsed_seconds")),
                "wall_time_utc": event.get("wall_time_utc"),
                "latest_tick": event.get("latest_tick") or high.get("latest_tick"),
                "latest_export_sequence": event.get("latest_export_sequence") or high.get("latest_export_sequence"),
                "player_world_point": _dict(high.get("player")).get("worldPoint"),
                "camera_yaw": _lookup(event, ("**.cameraYaw", "**.yaw")),
                "camera_pitch": _lookup(event, ("**.cameraPitch", "**.pitch")),
                "hover": high.get("hover"),
                "menu": high.get("menu"),
                "activity": _lookup(event, ("**.activityState", "**.woodcuttingState")),
                "inventory": high.get("inventory"),
                "nearby_objects": high.get("nearby_objects") or [],
                "route_objects": high.get("route_objects") or [],
                "nearby_npcs": high.get("nearby_npcs") or [],
                "raw_event": event,
            }
        )
    return snapshots


def _lookup(root: Any, aliases: tuple[str, ...]) -> Any:
    values, _paths = telemetry_schema.lookup_any(root, list(aliases))
    return values[0] if values else None


def nearest_snapshots(snapshots: list[dict[str, Any]], elapsed: float | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if elapsed is None or not snapshots:
        return None, None
    before = [item for item in snapshots if _float(item.get("elapsed_seconds")) is not None and float(item["elapsed_seconds"]) <= elapsed]
    after = [item for item in snapshots if _float(item.get("elapsed_seconds")) is not None and float(item["elapsed_seconds"]) >= elapsed]
    return (before[-1] if before else None, after[0] if after else None)


def nearest_arduino_event(arduino_events: list[dict[str, Any]], elapsed: float | None) -> dict[str, Any] | None:
    if elapsed is None or not arduino_events:
        return None
    timed = [(abs((_float(event.get("elapsed_seconds")) or _float(event.get("monotonic_time")) or 0.0) - elapsed), event) for event in arduino_events]
    timed.sort(key=lambda item: item[0])
    return timed[0][1] if timed else None


def _point(event: dict[str, Any]) -> dict[str, float] | None:
    for key in ("normalizedCanvas", "normalizedMenuPoint"):
        value = event.get(key)
        if isinstance(value, dict):
            try:
                return {"x": float(value["x"]), "y": float(value["y"]), "space": key}
            except (KeyError, TypeError, ValueError):
                pass
    for x_key, y_key in (("canvas_x", "canvas_y"), ("client_x", "client_y"), ("screen_x", "screen_y"), ("x", "y")):
        if event.get(x_key) is None or event.get(y_key) is None:
            continue
        try:
            return {"x": float(event[x_key]), "y": float(event[y_key]), "space": x_key.rsplit("_", 1)[0] if "_" in x_key else "point"}
        except (TypeError, ValueError):
            return None
    return None


def _candidate_point(candidate: dict[str, Any]) -> dict[str, Any] | None:
    for value in (
        candidate.get("aimPoint"),
        candidate.get("canvas"),
        candidate.get("canvasLocation"),
        _dict(candidate.get("geometry")).get("canvas"),
    ):
        if not isinstance(value, dict):
            continue
        x = value.get("x", value.get("canvasX", value.get("screen_x")))
        y = value.get("y", value.get("canvasY", value.get("screen_y")))
        if x is None or y is None:
            continue
        return {"x": x, "y": y}
    return None


def nearest_target_for_click(click: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    point = _point(click)
    if point is None or snapshot is None:
        return None
    candidates = []
    for candidate in _list(snapshot.get("nearby_objects")) + _list(snapshot.get("route_objects")):
        if not isinstance(candidate, dict):
            continue
        aim = _candidate_point(candidate)
        if not aim:
            continue
        try:
            distance = math.hypot(float(point["x"]) - float(aim["x"]), float(point["y"]) - float(aim["y"]))
        except (TypeError, ValueError):
            continue
        candidates.append((distance, candidate, aim))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    distance, candidate, aim = candidates[0]
    target = dict(candidate)
    target["aimPoint"] = aim
    target["clickDistance"] = round(distance, 3)
    return target


def hover_duration_before_click(input_event: dict[str, Any], snapshots: list[dict[str, Any]], target: dict[str, Any] | None) -> dict[str, Any]:
    elapsed = _float(input_event.get("elapsed_seconds"))
    if elapsed is None:
        return {"durationMs": None, "target": None, "actionVisible": None}
    target_name = str((_dict(target).get("effectiveName") or _dict(target).get("name") or "")).lower()
    hover_samples = []
    for snapshot in snapshots:
        sample_time = _float(snapshot.get("elapsed_seconds"))
        if sample_time is None or sample_time > elapsed:
            continue
        hover = _dict(snapshot.get("hover"))
        menu = _dict(snapshot.get("menu"))
        text = " ".join(str(value).lower() for value in (hover.get("topTarget"), hover.get("topOption"), menu.get("topTarget"), menu.get("topOption")))
        if target_name and target_name not in text:
            continue
        hover_samples.append(snapshot)
    if not hover_samples:
        return {"durationMs": None, "target": target_name or None, "actionVisible": None}
    start = _float(hover_samples[0].get("elapsed_seconds"))
    action_visible = any("chop" in json.dumps(_dict(sample.get("hover"))).lower() or "chop" in json.dumps(_dict(sample.get("menu"))).lower() for sample in hover_samples)
    return {
        "durationMs": round((elapsed - (start or elapsed)) * 1000.0, 3),
        "target": target_name or None,
        "actionVisible": action_visible,
    }


def click_analysis(input_event: dict[str, Any], before: dict[str, Any] | None, after: dict[str, Any] | None, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    target = nearest_target_for_click(input_event, before)
    relative = vm_mouse_arduino_mapper.target_relative_click(input_event, target) if target else {"status": "WARN", "missingFields": ["nearest_target"]}
    hover = hover_duration_before_click(input_event, snapshots, target)
    elapsed = _float(input_event.get("elapsed_seconds"))
    later = [snapshot for snapshot in snapshots if elapsed is not None and (_float(snapshot.get("elapsed_seconds")) or 0) > elapsed]
    result_hints = []
    before_inv = _dict(before.get("inventory") if before else {})
    after_inv = _dict(after.get("inventory") if after else {})
    if before_inv and after_inv and before_inv != after_inv:
        result_hints.append("inventory_changed")
    if later and any((_dict(_dict(snapshot.get("raw_event")).get("high_value_fields")).get("player") or {}).get("animation") not in (None, -1, "-1", 0, "0") for snapshot in later[:5]):
        result_hints.append("animation_started")
    return {
        "schema": "target_relative_click_analysis.v1",
        "clickPoint": _point(input_event),
        "nearestTarget": target,
        "targetRelative": relative,
        "hoverBeforeClick": hover,
        "telemetryObservedClickHistory": _dict(before.get("menu") if before else {}).get("lastMenuOptionClicked"),
        "osInputClickEvent": input_event,
        "resultHints": result_hints,
    }


def movement_segments(input_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments = []
    current: list[dict[str, Any]] = []
    last_time: float | None = None
    for event in input_events:
        if event.get("kind") not in {"mouse_move", "drag_move"}:
            continue
        elapsed = _float(event.get("elapsed_seconds"))
        if current and elapsed is not None and last_time is not None and elapsed - last_time > 0.25:
            segments.append(_movement_segment(current, len(segments) + 1))
            current = []
        current.append(event)
        last_time = elapsed
    if current:
        segments.append(_movement_segment(current, len(segments) + 1))
    return segments


def _movement_segment(events: list[dict[str, Any]], index: int) -> dict[str, Any]:
    points = [_point(event) for event in events]
    points = [point for point in points if point is not None]
    path_length = 0.0
    for before, after in zip(points, points[1:]):
        path_length += math.hypot(after["x"] - before["x"], after["y"] - before["y"])
    start = _float(events[0].get("elapsed_seconds"))
    end = _float(events[-1].get("elapsed_seconds"))
    duration = max(0.001, (end or 0.0) - (start or 0.0)) if start is not None and end is not None else None
    return {
        "segmentId": f"mouse_{index:03d}",
        "eventCount": len(events),
        "startTime": start,
        "endTime": end,
        "durationMs": round(duration * 1000.0, 3) if duration is not None else None,
        "pathLengthPx": round(path_length, 3),
        "speedPxPerSecond": round(path_length / duration, 3) if duration else None,
        "arduinoRelativeSequence": vm_mouse_arduino_mapper.mouse_path_to_relative_sequence(points),
    }


ACTION_CLASSES = {"game_action_click", "object_action_click", "npc_action_click", "world_walk_click", "menu_selection_click"}


def _first_elapsed(input_events: list[dict[str, Any]], kind: str) -> float | None:
    for event in input_events:
        if event.get("kind") == kind:
            return _float(event.get("elapsed_seconds"))
    return None


def _mirror_arm_window(input_events: list[dict[str, Any]], manifest: dict[str, Any] | None) -> dict[str, Any]:
    arduino = _dict(_dict(manifest).get("arduino"))
    armed = _dict(arduino.get("live_mirror_armed"))
    settings = _dict(arduino.get("live_mirror_settings"))
    if not settings:
        settings = _dict(_dict(arduino.get("live_mirror_summary")).get("settings"))
    test_duration = _float(armed.get("test_duration_sec"))
    if test_duration is None:
        test_duration = _float(settings.get("test_duration_sec")) or 0.0
    arm_mode = str(armed.get("arm_mode") or settings.get("arm_mode") or "").strip()
    if not arm_mode:
        arm_mode = "test_window" if test_duration and test_duration > 0 else ("recording_persistent" if arduino.get("live_mirror_requested") else "manual")
    arm_delay_ms = _float(armed.get("arm_delay_ms"))
    if arm_delay_ms is None:
        arm_delay_ms = _float(settings.get("arm_delay_ms")) or 0.0
    capture_start = _first_elapsed(input_events, "capture_start")
    start_elapsed = round(capture_start + arm_delay_ms / 1000.0, 6) if capture_start is not None else None
    disarm_elapsed = round(start_elapsed + test_duration, 6) if start_elapsed is not None and arm_mode == "test_window" and test_duration > 0 else None
    disarm_reason = "test_window_elapsed" if disarm_elapsed is not None else None
    summary = _dict(arduino.get("live_mirror_summary"))
    if summary.get("disarmReason"):
        disarm_reason = str(summary.get("disarmReason"))
    return {
        "armMode": arm_mode,
        "mirrorArmedStartElapsedSeconds": start_elapsed,
        "mirrorDisarmElapsedSeconds": disarm_elapsed,
        "disarmReason": disarm_reason,
        "testDurationSec": test_duration if arm_mode == "test_window" else 0.0,
        "recordingPersistent": arm_mode == "recording_persistent",
        "testWindowUsedForRecording": bool(arm_mode == "test_window" and test_duration > 0 and arduino.get("live_mirror_requested")),
    }


def _nearest_command(commands: list[dict[str, Any]], elapsed: float | None, *, before: bool) -> dict[str, Any] | None:
    if elapsed is None:
        return None
    candidates: list[tuple[float, dict[str, Any]]] = []
    for command in commands:
        if command.get("probeCommand") or not command.get("liveMirrorCommand"):
            continue
        command_time = _float(command.get("elapsed_seconds"))
        if command_time is None:
            continue
        delta = elapsed - command_time
        if before and delta >= 0:
            candidates.append((delta, command))
        elif not before and delta <= 0:
            candidates.append((abs(delta), command))
    if not candidates:
        return None
    _, command = sorted(candidates, key=lambda item: item[0])[0]
    return {
        "command": command.get("command") or command.get("command_kind"),
        "eventSeq": command.get("sourceInputEventSeq") or command.get("source_event_seq"),
        "elapsedSeconds": command.get("elapsed_seconds"),
        "deltaMs": round(abs((elapsed or 0) - float(command.get("elapsed_seconds") or 0)) * 1000.0, 3),
    }


def annotate_mirror_action_timing(
    input_events: list[dict[str, Any]],
    classifications: list[dict[str, Any]],
    arduino_events: list[dict[str, Any]] | None,
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    window = _mirror_arm_window(input_events, manifest)
    start = _float(window.get("mirrorArmedStartElapsedSeconds"))
    disarm = _float(window.get("mirrorDisarmElapsedSeconds"))
    action_rows: list[dict[str, Any]] = []
    menu_after = 0
    action_after = 0
    target_after = 0
    first_game: float | None = None
    first_menu: float | None = None
    commands = [event for event in (arduino_events or []) if event.get("kind") == "command_sent"]
    for item in classifications:
        classification = str(item.get("classification") or "")
        event_kind = str(item.get("eventKind") or "")
        if event_kind not in {"click", "double_click"} or classification not in ACTION_CLASSES:
            continue
        elapsed = _float(_dict(item.get("time")).get("elapsedSeconds"))
        armed = bool(start is not None and elapsed is not None and elapsed >= start and (disarm is None or elapsed <= disarm))
        after_disarm = bool(disarm is not None and elapsed is not None and elapsed > disarm)
        if first_game is None and elapsed is not None:
            first_game = elapsed
        if classification == "menu_selection_click" and first_menu is None and elapsed is not None:
            first_menu = elapsed
        if after_disarm:
            action_after += 1
            if classification == "menu_selection_click":
                menu_after += 1
            if bool(item.get("targetRelativeEligible")):
                target_after += 1
        time_since_disarm = round((elapsed - disarm) * 1000.0, 3) if after_disarm and elapsed is not None and disarm is not None else None
        before = _nearest_command(commands, elapsed, before=True)
        after = _nearest_command(commands, elapsed, before=False)
        mirrored_likely = bool(armed and ((before and before.get("deltaMs", 999999) <= 250) or (after and after.get("deltaMs", 999999) <= 250)))
        timing = {
            "mirrorArmedAtAction": armed,
            "timeSinceMirrorDisarmMs": time_since_disarm,
            "nearestArduinoCommandBefore": before,
            "nearestArduinoCommandAfter": after,
            "mirroredActionLikely": mirrored_likely,
        }
        item.update(timing)
        action_rows.append(
            {
                "eventSeq": item.get("eventSeq"),
                "classification": classification,
                "elapsedSeconds": elapsed,
                "target": _dict(item.get("targetContext")).get("targetName") or item.get("selectedTarget"),
                "action": _dict(item.get("targetContext")).get("targetAction") or item.get("selectedOption"),
                **timing,
            }
        )
    warnings: list[str] = []
    if window.get("testWindowUsedForRecording"):
        warnings.append("test_window_used_for_recording")
    if menu_after:
        warnings.append("menu_actions_after_mirror_disarm")
    if action_after:
        warnings.append("clicks_after_mirror_disarm")
    if target_after:
        warnings.append("target_actions_after_mirror_disarm")
    if _dict(_dict(manifest).get("arduino")).get("live_mirror_requested") and window.get("armMode") != "recording_persistent":
        warnings.append("recording_persistent_arm_missing")
    verdict = "PASS"
    if action_after:
        verdict = "WARN"
    elif _dict(_dict(manifest).get("arduino")).get("live_mirror_requested") and start is None:
        verdict = "FAIL"
    post_action_commands: list[dict[str, Any]] = []
    if first_game is not None:
        for command in commands:
            command_time = _float(command.get("elapsed_seconds"))
            if command_time is not None and command_time > first_game and command.get("liveMirrorCommand") and not command.get("probeCommand"):
                post_action_commands.append(command)
    post_action_move = sum(1 for command in post_action_commands if str(command.get("command") or command.get("command_kind")).upper() == "MOVE")
    post_action_click = sum(1 for command in post_action_commands if str(command.get("command") or command.get("command_kind")).upper() in {"CLICK", "MOUSE_DOWN", "MOUSE_UP"})
    if post_action_commands:
        warnings.append("live_mirror_post_action_commands")
        if verdict == "PASS":
            verdict = "WARN"
    if post_action_move:
        warnings.append("live_mirror_post_action_movement")
    if post_action_click:
        warnings.append("live_mirror_post_action_clicks")
    source_counts = Counter(command.get("sourceInputEventSeq") for command in post_action_commands if command.get("sourceInputEventSeq") is not None)
    feedback_suspected = bool(post_action_move and any(count > 2 for count in source_counts.values()))
    if feedback_suspected:
        warnings.append("live_mirror_feedback_loop_suspected")
    return {
        "schema": "mirror_action_timing.v1",
        **window,
        "firstGameActionTime": first_game,
        "firstMenuSelectionTime": first_menu,
        "menuSelectionsAfterDisarm": menu_after,
        "actionClicksAfterDisarm": action_after,
        "targetActionsAfterDisarm": target_after,
        "postActionArduinoCommandCount": len(post_action_commands),
        "postActionMovementCommandCount": post_action_move,
        "postActionClickCommandCount": post_action_click,
        "postActionWeirdMovementSuspected": bool(post_action_move or post_action_click),
        "feedbackLoopSuspected": feedback_suspected,
        "postActionCommandExamples": [
            {
                "command": command.get("command") or command.get("command_kind"),
                "elapsedSeconds": command.get("elapsed_seconds"),
                "sourceInputEventSeq": command.get("sourceInputEventSeq"),
                "sourceInputKind": command.get("sourceInputKind"),
                "dx": command.get("dx") or _dict(command.get("payload")).get("dx"),
                "dy": command.get("dy") or _dict(command.get("payload")).get("dy"),
            }
            for command in post_action_commands[:10]
        ],
        "finalMirrorRecordingVerdict": verdict,
        "actions": action_rows,
        "warnings": warnings,
    }


def _click_policy_from_manifest(manifest: dict[str, Any] | None) -> str:
    arduino = _dict(_dict(manifest).get("arduino"))
    settings = _dict(arduino.get("live_mirror_settings"))
    if not settings:
        settings = _dict(_dict(arduino.get("live_mirror_summary")).get("settings"))
    policy = str(settings.get("mirror_click_policy") or "").strip()
    return policy or "live_unsuppressed"


def _command_click_owner(command: dict[str, Any]) -> str:
    owner = str(command.get("clickOwner") or "").strip()
    if owner:
        return owner
    if command.get("mapOnlyClick") or command.get("dropReason") in {
        "click_policy_map_only",
        "click_policy_source_suppression_not_verified",
        "click_policy_arduino_source_not_verified",
        "click_policy_live_click_limit_reached",
    }:
        return "conversion_trace_click_only"
    if command.get("probeCommand"):
        return "arduino_probe_click"
    if command.get("liveMirrorCommand"):
        return "arduino_live_click"
    return "unknown_click_source"


def _click_commands_for_ownership(arduino_events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    live: list[dict[str, Any]] = []
    mapping: list[dict[str, Any]] = []
    for command in arduino_events:
        if _command_name(command) not in arduino_input_bridge.ARDUINO_CLICK_COMMANDS:
            continue
        if command.get("kind") == "command_dropped":
            mapping.append(command)
        elif not command.get("probeCommand"):
            live.append(command)
    return live, mapping


def build_click_ownership_summary(
    input_events: list[dict[str, Any]],
    arduino_events: list[dict[str, Any]] | None,
    manifest: dict[str, Any] | None,
    classifications: list[dict[str, Any]] | None = None,
    *,
    duplicate_window_ms: int = 300,
) -> dict[str, Any]:
    classifications = classifications or []
    by_seq = {item.get("eventSeq"): item for item in classifications}
    os_clicks = [event for event in input_events if event.get("kind") in {"click", "double_click"}]
    live_clicks, map_clicks = _click_commands_for_ownership(list(arduino_events or []))
    live_by_source = defaultdict(list)
    map_by_source = defaultdict(list)
    for command in live_clicks:
        live_by_source[_source_seq(command)].append(command)
    for command in map_clicks:
        map_by_source[_source_seq(command)].append(command)
    click_policy = _click_policy_from_manifest(manifest)
    records: list[dict[str, Any]] = []
    owners = Counter()
    duplicate_candidates = 0
    duplicate_likely = 0
    live_without_suppression = 0
    map_only_count = len(map_clicks)
    arduino_physical_count = 0
    first_duplicate_time = None
    used_live_command_keys: set[Any] = set()
    for event in os_clicks:
        seq = event.get("event_seq")
        elapsed = _float(event.get("elapsed_seconds"))
        live = [command for command in (live_by_source.get(seq) or []) if _source_seq(command) not in used_live_command_keys and command.get("command_id") not in used_live_command_keys]
        mapped = map_by_source.get(seq) or []
        echo_command = None
        if not live:
            nearby = [
                command
                for command in live_clicks
                if elapsed is not None
                and _float(command.get("elapsed_seconds")) is not None
                and abs(float(_float(command.get("elapsed_seconds")) or 0.0) - elapsed) * 1000.0 <= duplicate_window_ms
            ]
            unused_nearby = [command for command in nearby if (command.get("command_id") or id(command)) not in used_live_command_keys]
            live = unused_nearby[:1]
            if not live and nearby:
                echo_command = nearby[0]
        if not mapped:
            mapped = [
                command
                for command in map_clicks
                if elapsed is not None
                and _float(command.get("elapsed_seconds")) is not None
                and abs(float(_float(command.get("elapsed_seconds")) or 0.0) - elapsed) * 1000.0 <= duplicate_window_ms
            ][:1]
        command = live[0] if live else (mapped[0] if mapped else None)
        owner = "os_click_only"
        warnings: list[str] = []
        duplicate = False
        source_suppressed = False
        delta_ms = None
        if command and command in live_clicks:
            used_live_command_keys.add(command.get("command_id") or id(command))
        if command:
            command_elapsed = _float(command.get("elapsed_seconds"))
            if elapsed is not None and command_elapsed is not None:
                delta_ms = round((command_elapsed - elapsed) * 1000.0, 3)
            owner = _command_click_owner(command)
            source_suppressed = bool(command.get("sourceSuppressionVerified"))
            if owner == "arduino_physical_click_source":
                arduino_physical_count += 1
            elif owner == "arduino_live_click" and not source_suppressed:
                duplicate = True
                owner = "duplicate_os_plus_arduino_click"
                duplicate_candidates += 1
                duplicate_likely += 1
                live_without_suppression += 1
                if first_duplicate_time is None:
                    first_duplicate_time = elapsed
                warnings.append("live Arduino CLICK was sent for an unsuppressed OS click")
            elif owner == "conversion_trace_click_only":
                warnings.append("click mapped to Arduino format without sending a live CLICK")
        elif echo_command:
            command_elapsed = _float(echo_command.get("elapsed_seconds"))
            if elapsed is not None and command_elapsed is not None:
                delta_ms = round((command_elapsed - elapsed) * 1000.0, 3)
            owner = "arduino_click_echo"
            warnings.append("click-like OS event was near an already-accounted Arduino click command")
        owners[owner] += 1
        row = {
            "eventSeq": seq,
            "clickOwner": owner,
            "originalOsClickEventSeq": seq,
            "arduinoCommandId": command.get("command_id") if command else None,
            "arduinoAckTime": command.get("ack_at_monotonic") if command else None,
            "osClickTime": elapsed,
            "arduinoClickTime": _float(command.get("elapsed_seconds")) if command else None,
            "clickTimeDeltaMs": delta_ms,
            "duplicateClickLikely": duplicate,
            "sourceSuppressionVerified": source_suppressed,
            "rawInputDeviceAttribution": event.get("rawInputDevice") or event.get("raw_input_device"),
            "reasons": [f"click_policy={click_policy}", f"owner={owner}"],
            "warnings": warnings,
        }
        if seq in by_seq:
            by_seq[seq].update(
                {
                    "clickOwner": owner,
                    "originalOsClickEventSeq": seq,
                    "arduinoCommandId": row["arduinoCommandId"],
                    "arduinoAckTime": row["arduinoAckTime"],
                    "osClickTime": row["osClickTime"],
                    "arduinoClickTime": row["arduinoClickTime"],
                    "clickTimeDeltaMs": row["clickTimeDeltaMs"],
                    "duplicateClickLikely": duplicate,
                    "sourceSuppressionVerified": source_suppressed,
                    "clickOwnershipWarnings": warnings,
                }
            )
        records.append(row)
    probe_clicks = [command for command in (arduino_events or []) if command.get("probeCommand") and _command_name(command) in arduino_input_bridge.ARDUINO_CLICK_COMMANDS]
    for command in live_clicks:
        if _source_seq(command) not in {event.get("event_seq") for event in os_clicks} and not command.get("sourceInputEventSeq"):
            owners["arduino_live_click"] += 1
    status = "WARN" if duplicate_likely or (click_policy == "live_unsuppressed" and live_without_suppression) else "PASS"
    warnings = []
    if duplicate_likely:
        warnings.append("duplicate OS plus Arduino click likely; normal OS click was mirrored as a second live Arduino click")
    if click_policy == "map_only":
        warnings.append("map_only click policy avoids live Arduino CLICK commands during manual OS input")
    return {
        "schema": "click_ownership_summary.v1",
        "status": status,
        "clickPolicyUsed": click_policy,
        "totalOsClicks": len(os_clicks),
        "totalArduinoLiveClickCommands": len(live_clicks),
        "arduinoProbeClickCount": len(probe_clicks),
        "duplicateClickCandidateCount": duplicate_candidates,
        "duplicateClickLikelyCount": duplicate_likely,
        "liveClickWithoutSuppressionCount": live_without_suppression,
        "mapOnlyClickCount": map_only_count,
        "arduinoPhysicalClickCount": arduino_physical_count,
        "clickOwners": dict(sorted(owners.items())),
        "sourceSuppressionAvailable": any(_dict(event.get("rawInputDevice") or event.get("raw_input_device")).get("available") for event in os_clicks),
        "sourceSuppressionVerified": bool(arduino_physical_count),
        "firstDuplicateClickTime": first_duplicate_time,
        "duplicateClickExamples": [row for row in records if row.get("duplicateClickLikely")][:5],
        "clicks": records,
        "warnings": warnings,
    }


def _fallback_target_from_classification(classification: dict[str, Any] | None) -> dict[str, Any] | None:
    classification = _dict(classification)
    target_context = _dict(classification.get("targetContext"))
    target = _dict(target_context.get("matchedTarget")) or _dict(classification.get("linkedGameTarget"))
    if not target:
        return None
    target = dict(target)
    if target_context.get("targetAction") and not target.get("action"):
        target["action"] = target_context.get("targetAction")
    if target_context.get("targetName") and not target.get("name"):
        target["name"] = target_context.get("targetName")
    return target


def _refine_menu_selection_classification(
    input_event: dict[str, Any],
    classification: dict[str, Any] | None,
    menu_snapshot_buffer: list[dict[str, Any]],
    *,
    previous_right_click: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not classification or classification.get("classification") != "menu_selection_click":
        return classification, None
    fallback_target = _fallback_target_from_classification(classification)
    pairing = menu_interaction_model.select_menu_snapshot_for_selection(
        input_event,
        menu_snapshot_buffer,
        fallback_target=fallback_target,
        previous_right_click=previous_right_click,
    )
    refined_selection = _dict(pairing.get("selection"))
    selected_snapshot = _dict(pairing.get("selectedSnapshot"))
    diagnostics = _dict(pairing.get("diagnostics"))
    if not refined_selection:
        return classification, selected_snapshot or None
    classification["menuSelection"] = refined_selection
    classification["selectedRowIndex"] = refined_selection.get("selectedRowIndex")
    classification["selectedOption"] = refined_selection.get("selectedOption")
    classification["selectedTarget"] = refined_selection.get("selectedTarget")
    classification["menuRowBounds"] = refined_selection.get("rowBounds")
    classification["insideMenuRowBounds"] = refined_selection.get("insideRowBounds")
    classification["rowCenterDistancePx"] = refined_selection.get("rowCenterDistancePx")
    classification["linkedGameTarget"] = refined_selection.get("linkedGameTarget")
    classification["normalizedMenuPoint"] = refined_selection.get("normalizedClickPoint")
    classification["coordinateTransformUsed"] = refined_selection.get("coordinateTransformUsed")
    classification["coordinateTransformConfidence"] = refined_selection.get("coordinateTransformConfidence")
    classification["coordinateTransformReasons"] = refined_selection.get("coordinateTransformReasons") or []
    classification["menuSnapshotDiagnostics"] = diagnostics
    classification["candidateMenuSnapshots"] = diagnostics.get("candidateSnapshots") or []
    classification["selectedMenuSnapshot"] = menu_interaction_model.snapshot_brief(selected_snapshot) if selected_snapshot else None
    classification["rowGeometrySource"] = refined_selection.get("rowGeometrySource")
    warnings = set(str(item) for item in classification.get("warnings") or [])
    warnings.update(str(item) for item in refined_selection.get("warnings") or [])
    if refined_selection.get("rowBounds"):
        warnings.discard("menu_row_bounds_missing")
        warnings.discard("selection_inferred_from_game_target_without_row_geometry")
    classification["warnings"] = sorted(warnings)
    return classification, selected_snapshot or None


def join_events(
    input_events: list[dict[str, Any]],
    telemetry_events: list[dict[str, Any]],
    arduino_events: list[dict[str, Any]] | None = None,
    *,
    manifest: dict[str, Any] | None = None,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    snapshots = telemetry_snapshots(telemetry_events)
    menu_snapshot_buffer = menu_interaction_model.build_menu_snapshot_buffer(snapshots)
    arduino_summary_for_path = arduino_input_bridge.summarize_arduino_events(arduino_events or [])
    input_path_integrity = arduino_mirror_verifier.build_input_path_integrity(
        input_events,
        arduino_events or [],
        manifest=manifest,
        arduino_summary=arduino_summary_for_path,
    )
    mirror_verification = arduino_mirror_verifier.build_mirror_verification(
        input_events,
        arduino_events or [],
        manifest=manifest,
    )
    input_events = [
        {
            **event,
            "inputPathClassification": event.get("inputPathClassification") or input_path_integrity.get("inputPathClassification"),
            "mirrorVerificationStatus": event.get("mirrorVerificationStatus") or input_path_integrity.get("mirrorVerificationStatus"),
        }
        for event in input_events
    ]
    classifications, action_summary = input_action_classifier.classify_input_actions(input_events, snapshots)
    mirror_action_timing = annotate_mirror_action_timing(input_events, classifications, arduino_events or [], manifest)
    click_ownership_summary = build_click_ownership_summary(input_events, arduino_events or [], manifest, classifications)
    input_path_integrity["mirrorActionTiming"] = mirror_action_timing
    input_path_integrity["clickOwnershipSummary"] = click_ownership_summary
    input_path_integrity["finalMirrorRecordingVerdict"] = mirror_action_timing.get("finalMirrorRecordingVerdict")
    input_path_integrity["clickPolicyUsed"] = click_ownership_summary.get("clickPolicyUsed")
    input_path_integrity["duplicateClickCandidateCount"] = click_ownership_summary.get("duplicateClickCandidateCount")
    input_path_integrity["duplicateClickLikelyCount"] = click_ownership_summary.get("duplicateClickLikelyCount")
    input_path_integrity["liveClickWithoutSuppressionCount"] = click_ownership_summary.get("liveClickWithoutSuppressionCount")
    input_path_integrity["mapOnlyClickCount"] = click_ownership_summary.get("mapOnlyClickCount")
    input_path_integrity["arduinoPhysicalClickCount"] = click_ownership_summary.get("arduinoPhysicalClickCount")
    if int(click_ownership_summary.get("duplicateClickLikelyCount") or 0) > 0:
        safety_classes = set(input_path_integrity.get("liveMirrorSafetyClassifications") or [])
        safety_classes.add("live_mirror_duplicate_click_risk")
        input_path_integrity["liveMirrorSafetyClassifications"] = sorted(safety_classes)
        input_path_integrity["possibleDoubleInput"] = True
        input_path_integrity["mirrorQuality"] = "duplicate_click_risk"
        input_path_integrity["status"] = "WARN"
        if input_path_integrity.get("finalMirrorRecordingVerdict") == "PASS":
            input_path_integrity["finalMirrorRecordingVerdict"] = "WARN"
        if mirror_action_timing.get("finalMirrorRecordingVerdict") == "PASS":
            mirror_action_timing["finalMirrorRecordingVerdict"] = "WARN"
        mirror_action_timing["warnings"] = sorted(
            set(list(mirror_action_timing.get("warnings") or []) + ["live_mirror_duplicate_click_risk"])
        )
    input_path_integrity["menuSelectionsAfterDisarm"] = mirror_action_timing.get("menuSelectionsAfterDisarm")
    input_path_integrity["actionClicksAfterDisarm"] = mirror_action_timing.get("actionClicksAfterDisarm")
    input_path_integrity["postActionArduinoCommandCount"] = mirror_action_timing.get("postActionArduinoCommandCount")
    input_path_integrity["postActionMovementCommandCount"] = mirror_action_timing.get("postActionMovementCommandCount")
    input_path_integrity["postActionClickCommandCount"] = mirror_action_timing.get("postActionClickCommandCount")
    input_path_integrity["postActionWeirdMovementSuspected"] = mirror_action_timing.get("postActionWeirdMovementSuspected")
    input_path_integrity["feedbackLoopSuspected"] = bool(input_path_integrity.get("feedbackLoopSuspected") or mirror_action_timing.get("feedbackLoopSuspected"))
    input_path_integrity["mirrorArmedStartElapsedSeconds"] = mirror_action_timing.get("mirrorArmedStartElapsedSeconds")
    input_path_integrity["mirrorDisarmElapsedSeconds"] = mirror_action_timing.get("mirrorDisarmElapsedSeconds")
    input_path_integrity["armMode"] = mirror_action_timing.get("armMode")
    input_path_integrity["warnings"] = sorted(
        set(
            list(input_path_integrity.get("warnings") or [])
            + list(mirror_action_timing.get("warnings") or [])
            + list(click_ownership_summary.get("warnings") or [])
        )
    )
    if int(click_ownership_summary.get("duplicateClickLikelyCount") or 0) > 0:
        mirror_verification["status"] = "WARN"
        mirror_verification["mirrorQuality"] = "duplicate_click_risk"
        mirror_verification["warnings"] = sorted(
            set(list(mirror_verification.get("warnings") or []) + list(click_ownership_summary.get("warnings") or []))
        )
    classification_by_seq = {item.get("eventSeq"): item for item in classifications}
    joined: list[dict[str, Any]] = []
    click_rows = []
    skipped_clicks = []
    target_quality_rows: list[dict[str, Any]] = []
    coordinate_transform_results: list[dict[str, Any]] = []
    last_right_click_event: dict[str, Any] | None = None
    for input_event in input_events:
        elapsed = _float(input_event.get("elapsed_seconds"))
        before, after = nearest_snapshots(snapshots, elapsed)
        arduino = nearest_arduino_event(arduino_events or [], elapsed)
        classification = classification_by_seq.get(input_event.get("event_seq"))
        selected_menu_snapshot: dict[str, Any] | None = None
        if classification and input_event.get("kind") in {"click", "double_click"}:
            label = classification.get("classification")
            if label == "menu_selection_click":
                classification, selected_menu_snapshot = _refine_menu_selection_classification(
                    input_event,
                    classification,
                    menu_snapshot_buffer,
                    previous_right_click=last_right_click_event,
                )
            if label == "right_click_menu_open":
                last_right_click_event = input_event
            elif label == "menu_selection_click":
                last_right_click_event = None
        row = {
            "schema": JOINED_EVENT_SCHEMA,
            "inputEvent": input_event,
            "nearestTelemetryBefore": _compact_snapshot(before),
            "nearestTelemetryAfter": _compact_snapshot(after),
            "nearestArduinoEvent": arduino,
        }
        if classification:
            row["inputActionClassification"] = classification
            row["classification"] = classification.get("classification")
            row["eligibleForTargetMatching"] = classification.get("eligibleForTargetMatching")
            row["targetRelativeEligible"] = classification.get("targetRelativeEligible")
            row["classificationConfidence"] = classification.get("confidence")
            row["classificationReasons"] = classification.get("reasons") or []
            if classification.get("normalizedMenuPoint"):
                row["normalizedMenuPoint"] = classification.get("normalizedMenuPoint")
            if classification.get("coordinateTransformUsed"):
                row["coordinateTransformUsed"] = classification.get("coordinateTransformUsed")
                row["coordinateTransformConfidence"] = classification.get("coordinateTransformConfidence")
                row["coordinateTransformReasons"] = classification.get("coordinateTransformReasons") or []
            if classification.get("inputPathClassification"):
                row["inputPathClassification"] = classification.get("inputPathClassification")
            if classification.get("mirrorVerificationStatus"):
                row["mirrorVerificationStatus"] = classification.get("mirrorVerificationStatus")
            if classification.get("menuSnapshotDiagnostics"):
                row["menuSnapshotDiagnostics"] = classification.get("menuSnapshotDiagnostics")
            if classification.get("selectedMenuSnapshot"):
                row["selectedMenuSnapshot"] = classification.get("selectedMenuSnapshot")
        if input_event.get("kind") in {"click", "double_click"}:
            if _dict(classification).get("classification") == "menu_selection_click":
                selected_snapshot_id = _dict(_dict(classification).get("menuSnapshotDiagnostics")).get("selectedSnapshotId")
                if selected_menu_snapshot is None and selected_snapshot_id:
                    selected_menu_snapshot = next((item for item in menu_snapshot_buffer if item.get("snapshotId") == selected_snapshot_id), None)
                menu_snapshot = selected_menu_snapshot or menu_interaction_model.normalize_menu_snapshot(before or after or {})
                fallback_target = _fallback_target_from_classification(classification)
                chosen = coordinate_spaces.infer_best_transform_for_menu_hit(input_event, menu_snapshot, fallback_target=fallback_target)
                raw_point = coordinate_spaces.event_point(input_event, "client") or coordinate_spaces.event_point(input_event, "canvas")
                raw_row = None
                for menu_row in menu_snapshot.get("rowsVisualOrder") or []:
                    if coordinate_spaces.point_in_bounds(raw_point, _dict(menu_row).get("bounds")) is True:
                        raw_row = menu_row
                        break
                coordinate_transform_results.append(
                    {
                        "eventSeq": input_event.get("event_seq"),
                        "rawPoint": raw_point,
                        "rawHitTest": {
                            "insideRowBounds": raw_row is not None,
                            "selectedRowIndex": _dict(raw_row).get("rowIndex"),
                            "selectedOption": _dict(raw_row).get("option"),
                            "selectedTarget": _dict(raw_row).get("target"),
                        },
                        "chosen": chosen,
                        "warnings": chosen.get("warnings") or [],
                    }
                )
            if _dict(classification).get("targetRelativeEligible"):
                row["clickAnalysis"] = click_analysis(input_event, before, after, snapshots)
                quality = target_match_quality.score_target_match(input_event, classification, row["clickAnalysis"], before, after, snapshots)
                row["targetMatchQuality"] = quality.get("quality")
                row["targetMatchScore"] = quality.get("score")
                row["targetMatchReasons"] = quality.get("reasons") or []
                row["targetMatchWarnings"] = quality.get("warnings") or []
                row["targetMatchEvidence"] = quality.get("evidence") or {}
                row["postClickResult"] = quality.get("postClickResult") or {}
                row["targetMatchQualityDetail"] = quality
                if quality.get("menuSelectionQuality"):
                    row["menuSelectionQuality"] = quality.get("menuSelectionQuality")
                    row["gameTargetQuality"] = quality.get("gameTargetQuality")
                target_quality_rows.append(quality)
                click_rows.append(row["clickAnalysis"])
            else:
                row["targetMatchingSkipped"] = {
                    "reason": "not_target_relative_eligible",
                    "classification": _dict(classification).get("classification"),
                    "reasons": _dict(classification).get("reasons") or [],
                }
                skipped_clicks.append(row["targetMatchingSkipped"])
        joined.append(row)
    menu_interactions, menu_interaction_summary = menu_interaction_model.build_menu_interactions_from_joined(
        joined,
        target_quality_rows,
        menu_snapshots=menu_snapshot_buffer,
    )
    menu_by_seq = {row.get("eventSeq"): row for row in menu_interactions}
    for row in joined:
        seq = _dict(row.get("inputEvent")).get("event_seq")
        menu_interaction = menu_by_seq.get(seq)
        if menu_interaction:
            row["menuInteraction"] = menu_interaction
            if menu_interaction.get("menuSelection"):
                row["menuSelection"] = menu_interaction.get("menuSelection")
            if menu_interaction.get("menuSnapshot"):
                row["menuSnapshot"] = menu_interaction.get("menuSnapshot")
    hover_durations = [
        _dict(row.get("hoverBeforeClick")).get("durationMs")
        for row in click_rows
        if isinstance(_dict(row.get("hoverBeforeClick")).get("durationMs"), (int, float))
    ]
    target_relative = [row for row in click_rows if _dict(row.get("targetRelative")).get("status") == "PASS"]
    target_quality_summary = target_match_quality.summarize_quality(target_quality_rows)
    coordinate_alignment_summary = coordinate_spaces.build_coordinate_alignment_summary(
        input_events,
        snapshots,
        transform_results=coordinate_transform_results,
        input_path_integrity=input_path_integrity,
    )
    summary = {
        "schema": "joined_input_telemetry_summary.v1",
        "status": "PASS" if input_events else "WARN",
        "generated_at_utc": utc_now(),
        "inputEventCount": len(input_events),
        "telemetrySnapshotCount": len(snapshots),
        "arduinoEventCount": len(arduino_events or []),
        "clickCount": action_summary.get("rawOsClickCount"),
        "classifiedClickCount": action_summary.get("classifiedClickCount"),
        "eligibleGameActionClickCount": action_summary.get("eligibleGameActionClickCount"),
        "targetRelativeClickCount": len(target_relative),
        "skippedTargetMatchingClickCount": len(skipped_clicks),
        "inputActionSummary": action_summary,
        "targetMatchSummary": target_quality_summary,
        "menuInteractionSummary": menu_interaction_summary,
        "coordinateAlignmentSummary": coordinate_alignment_summary,
        "inputPathIntegritySummary": input_path_integrity,
        "arduinoMirrorVerification": mirror_verification,
        "mirrorActionTiming": mirror_action_timing,
        "clickOwnershipSummary": click_ownership_summary,
        "hoverToClickMedianMs": statistics.median(hover_durations) if hover_durations else None,
        "hoverToClickAverageMs": round(sum(hover_durations) / len(hover_durations), 3) if hover_durations else None,
        "movementSegments": movement_segments(input_events),
        "warnings": [] if input_events else ["input_events.jsonl is missing or empty; no OS-level input trace can be joined"],
    }
    return (
        joined,
        summary,
        classifications,
        action_summary,
        target_quality_rows,
        target_quality_summary,
        menu_interactions,
        menu_interaction_summary,
        coordinate_alignment_summary,
        input_path_integrity,
        mirror_verification,
        mirror_action_timing,
    )


def _compact_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snapshot:
        return None
    return {
        "elapsed_seconds": snapshot.get("elapsed_seconds"),
        "wall_time_utc": snapshot.get("wall_time_utc"),
        "tick": snapshot.get("latest_tick"),
        "export_sequence": snapshot.get("latest_export_sequence"),
        "playerWorldPoint": snapshot.get("player_world_point"),
        "cameraYaw": snapshot.get("camera_yaw"),
        "cameraPitch": snapshot.get("camera_pitch"),
        "hover": snapshot.get("hover"),
        "menu": snapshot.get("menu"),
        "inventory": snapshot.get("inventory"),
    }


def analyze_recording(
    recording_dir: str | Path,
    *,
    write: bool = True,
    include_mapping: bool = False,
) -> dict[str, Any]:
    recording = Path(recording_dir)
    telemetry_events = load_jsonl(recording / "events.jsonl")
    input_path = recording / "input_events.jsonl"
    input_events = input_trace_recorder.load_input_events(input_path)
    arduino_events = arduino_input_bridge.load_combined_arduino_events(recording)
    probe_result = load_json(recording / "arduino_probe_result.json")
    probe_quality: dict[str, Any] = {}
    if probe_result:
        probe_quality = arduino_mirror_verifier.probe_quality_from_payload(probe_result)
        for event in arduino_events:
            if event.get("probeCommand"):
                event["probeClassification"] = probe_quality.get("classification")
                event["probeVerified"] = bool(probe_quality.get("success"))
                event["probeVerifiedClean"] = probe_quality.get("classification") == "arduino_probe_verified_clean"
                event["probeResult"] = probe_quality.get("classification")
        if probe_quality.get("classification") and probe_quality.get("classification") != probe_result.get("classification"):
            updated_probe = dict(probe_result)
            updated_probe["originalClassification"] = probe_result.get("classification")
            updated_probe["classification"] = probe_quality.get("classification")
            updated_probe["status"] = probe_quality.get("status")
            updated_probe["success"] = bool(probe_quality.get("success"))
            updated_probe["reason"] = probe_quality.get("reason")
            updated_probe["quality"] = probe_quality
            atomic_write_json(recording / "arduino_probe_result.json", updated_probe)
    manifest = load_json(recording / "manifest.json")
    (
        joined,
        joined_summary,
        classifications,
        action_summary,
        target_quality_rows,
        target_quality_summary,
        menu_interactions,
        menu_interaction_summary,
        coordinate_alignment_summary,
        input_path_integrity,
        mirror_verification,
        mirror_action_timing,
    ) = join_events(input_events, telemetry_events, arduino_events, manifest=manifest)
    input_summary = input_trace_recorder.summarize_input_events(input_events, input_file_exists=input_path.exists())
    camera_summary = camera_behavior.summarize_camera_behavior(
        telemetry_events,
        input_events,
        action_classifications=classifications,
        target_match_quality=target_quality_rows,
    )
    arduino_summary = arduino_input_bridge.summarize_arduino_events(arduino_events)
    action_commands = arduino_input_bridge.load_arduino_action_commands(recording / "arduino_action_commands.jsonl")
    existing_live_mirror_summary = load_json(recording / "arduino_live_mirror_summary.json")
    live_mirror_summary = arduino_live_mirror.build_summary_from_commands(action_commands)
    if isinstance(existing_live_mirror_summary, dict):
        preserved = {
            key: value
            for key, value in existing_live_mirror_summary.items()
            if key
            in {
                "mirrorState",
                "mirrorPaused",
                "pauseReason",
                "armMode",
                "armedAtMonotonic",
                "disarmedAtMonotonic",
                "disarmReason",
                "testDurationSec",
                "recordingPersistent",
                "activeWindows",
                "actionsAfterDisarmCount",
                "clicksAfterDisarmCount",
                "movementAfterDisarmCount",
                "droppedCommandCount",
                "throttledCommandCount",
                "duplicateCommandCount",
                "duplicateInputEventCount",
                "panicStopCount",
                "uiControlEventsDropped",
                "foregroundFilteredEventsDropped",
                "sameButtonCooldownDrops",
                "feedbackSuppressedButtonEventCount",
                "droppedEventsByReason",
                "settings",
                "mirrorClickPolicy",
                "clickPolicyUsed",
                "clickPolicyDowngraded",
                "clickPolicyDowngradeReason",
                "sourceSuppressionAvailable",
                "sourceSuppressionVerified",
                "mapOnlyClickCount",
                "clickPolicyOffCount",
                "liveUnsuppressedClickCount",
                "liveClickWithoutSuppressionCount",
                "duplicateRiskClickCount",
                "arduinoPhysicalClickCount",
                "liveClicksAutoDisabled",
            }
        }
        live_mirror_summary.update({key: value for key, value in preserved.items() if value not in (None, "", [], {})})
    live_mirror_summary["liveMirrorVerified"] = bool(input_path_integrity.get("liveMirrorVerified"))
    live_mirror_summary["mirrorVerificationStatus"] = input_path_integrity.get("mirrorVerificationStatus")
    live_mirror_summary["inputPathClassification"] = input_path_integrity.get("inputPathClassification")
    live_mirror_summary["correlatedCommandToObservedMovementCount"] = input_path_integrity.get("correlatedCommandToObservedMovementCount")
    live_mirror_summary["correlatedCommandToObservedClickCount"] = input_path_integrity.get("correlatedCommandToObservedClickCount")
    live_mirror_summary["maxArduinoCommandsPerSecond"] = input_path_integrity.get("maxArduinoCommandsPerSecond")
    live_mirror_summary["maxClickCommandsPerSecond"] = input_path_integrity.get("maxClickCommandsPerSecond")
    live_mirror_summary["liveMirrorSafetyClassifications"] = input_path_integrity.get("liveMirrorSafetyClassifications") or live_mirror_summary.get("liveMirrorSafetyClassifications") or []
    live_mirror_summary["mirrorActionTiming"] = mirror_action_timing
    live_mirror_summary["clickOwnershipSummary"] = input_path_integrity.get("clickOwnershipSummary")
    live_mirror_summary["clickPolicyUsed"] = input_path_integrity.get("clickPolicyUsed") or live_mirror_summary.get("clickPolicyUsed")
    live_mirror_summary["duplicateClickLikelyCount"] = input_path_integrity.get("duplicateClickLikelyCount")
    live_mirror_summary["liveClickWithoutSuppressionCount"] = input_path_integrity.get("liveClickWithoutSuppressionCount")
    live_mirror_summary["mapOnlyClickCount"] = input_path_integrity.get("mapOnlyClickCount")
    live_mirror_summary["arduinoPhysicalClickCount"] = input_path_integrity.get("arduinoPhysicalClickCount")
    live_mirror_summary["finalMirrorRecordingVerdict"] = input_path_integrity.get("finalMirrorRecordingVerdict") or mirror_action_timing.get("finalMirrorRecordingVerdict")
    live_mirror_summary["menuSelectionsAfterDisarm"] = mirror_action_timing.get("menuSelectionsAfterDisarm")
    live_mirror_summary["actionClicksAfterDisarm"] = mirror_action_timing.get("actionClicksAfterDisarm")
    live_mirror_summary["postActionArduinoCommandCount"] = mirror_action_timing.get("postActionArduinoCommandCount")
    live_mirror_summary["postActionMovementCommandCount"] = mirror_action_timing.get("postActionMovementCommandCount")
    live_mirror_summary["postActionClickCommandCount"] = mirror_action_timing.get("postActionClickCommandCount")
    live_mirror_summary["postActionWeirdMovementSuspected"] = mirror_action_timing.get("postActionWeirdMovementSuspected")
    live_mirror_summary["feedbackLoopSuspected"] = bool(live_mirror_summary.get("feedbackLoopSuspected") or mirror_action_timing.get("feedbackLoopSuspected"))
    live_mirror_summary["armMode"] = mirror_action_timing.get("armMode") or live_mirror_summary.get("armMode")
    live_mirror_summary["mirrorArmedStartElapsedSeconds"] = mirror_action_timing.get("mirrorArmedStartElapsedSeconds")
    live_mirror_summary["mirrorDisarmElapsedSeconds"] = mirror_action_timing.get("mirrorDisarmElapsedSeconds")
    mapping = (
        vm_mouse_arduino_mapper.build_mapping(
            input_events,
            arduino_events,
            telemetry_summary={"snapshotCount": len(telemetry_snapshots(telemetry_events))},
            arduino_summary=arduino_summary,
            action_classifications=classifications,
            target_match_quality=target_quality_rows,
            input_path_integrity=input_path_integrity,
        )
        if include_mapping
        else None
    )
    result = {
        "schema": "input_trace_join_analysis.v1",
        "input_trace": input_summary,
        "joined_input_telemetry": joined_summary,
        "joined_input_telemetry_rows": joined,
        "input_action_summary": action_summary,
        "input_action_classifications": classifications,
        "target_match_summary": target_quality_summary,
        "target_match_quality": target_quality_rows,
        "menu_interaction_summary": menu_interaction_summary,
        "menu_interactions": menu_interactions,
        "coordinate_alignment_summary": coordinate_alignment_summary,
        "input_path_integrity_summary": input_path_integrity,
        "arduino_mirror_verification": mirror_verification,
        "mirror_action_timing": mirror_action_timing,
        "click_ownership_summary": input_path_integrity.get("clickOwnershipSummary"),
        "click_analysis": {
            "schema": "click_analysis_summary.v1",
            "clickCount": joined_summary.get("clickCount"),
            "eligibleGameActionClickCount": joined_summary.get("eligibleGameActionClickCount"),
            "targetRelativeClickCount": joined_summary.get("targetRelativeClickCount"),
            "hoverToClickMedianMs": joined_summary.get("hoverToClickMedianMs"),
            "hoverToClickAverageMs": joined_summary.get("hoverToClickAverageMs"),
        },
        "hover_analysis": {
            "schema": "hover_analysis_summary.v1",
            "hoverToClickMedianMs": joined_summary.get("hoverToClickMedianMs"),
            "hoverToClickAverageMs": joined_summary.get("hoverToClickAverageMs"),
        },
        "camera_behavior": camera_summary,
        "arduino_trace": arduino_summary,
        "arduino_live_mirror": live_mirror_summary,
        "vm_mouse_arduino_mapping": mapping,
        "warnings": (
            list(input_summary.get("warnings") or [])
            + list(camera_summary.get("warnings") or [])
            + list(arduino_summary.get("warnings") or [])
            + list(_dict(mapping).get("warnings") or [])
            + list(mirror_action_timing.get("warnings") or [])
        ),
    }
    if write:
        write_jsonl(recording / "joined_input_telemetry.jsonl", joined)
        write_jsonl(recording / "input_action_classifications.jsonl", classifications)
        write_jsonl(recording / "target_match_quality.jsonl", target_quality_rows)
        write_jsonl(recording / "menu_interactions.jsonl", menu_interactions)
        atomic_write_json(recording / "coordinate_alignment_summary.json", coordinate_alignment_summary)
        atomic_write_json(recording / "input_path_integrity_summary.json", input_path_integrity)
        atomic_write_json(recording / "click_ownership_summary.json", input_path_integrity.get("clickOwnershipSummary") or {})
        atomic_write_json(recording / "arduino_mirror_verification.json", mirror_verification)
        atomic_write_json(recording / "arduino_live_mirror_summary.json", live_mirror_summary)
        atomic_write_json(recording / "mirror_action_timing_summary.json", mirror_action_timing)
        atomic_write_json(recording / "input_trace_summary.json", input_summary)
        atomic_write_json(recording / "input_action_summary.json", action_summary)
        atomic_write_json(recording / "target_match_summary.json", target_quality_summary)
        atomic_write_json(recording / "menu_interaction_summary.json", menu_interaction_summary)
        atomic_write_json(recording / "camera_behavior_summary.json", camera_summary)
        if arduino_events:
            atomic_write_json(recording / "arduino_trace_summary.json", arduino_summary)
        if mapping is not None:
            atomic_write_json(recording / "vm_mouse_arduino_mapping.json", mapping)
    return result

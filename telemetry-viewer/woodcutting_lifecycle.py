from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import telemetry_schema
import telemetry_sources


SCHEMA_VERSION = "woodcutting_lifecycle.v1"
WOODCUTTING_ANIMATION_ID = 879
NORMAL_LOG_ITEM_IDS = {1511}
MAX_CONTEXT_CLICK_AGE_TICKS = 15
ACTION_CLICK_CLASSES = {"object_action_click", "menu_selection_click", "ambiguous_click"}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _jsonl_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError as error:
                    warnings.append(f"{path.name} line {line_number} JSONDecodeError: {error.msg}")
                    continue
                if isinstance(value, dict):
                    records.append(value)
    except FileNotFoundError:
        pass
    except OSError as error:
        warnings.append(f"{path.name} unreadable: {type(error).__name__}: {error}")
    return records, warnings


def _source_data(source: dict[str, Any]) -> Any:
    if "data" in source:
        return source.get("data")
    raw = source.get("raw")
    if not isinstance(raw, str) or not raw:
        return None
    name = str(source.get("name") or "").lower()
    path = str(source.get("path") or "").lower()
    if name == "events" or path.endswith(".jsonl") or path.endswith(".ndjson"):
        return _jsonl_records(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _payloads_by_source(event: dict[str, Any]) -> dict[str, Any]:
    payloads: dict[str, Any] = {}
    for source in event.get("sources") or []:
        if not isinstance(source, dict):
            continue
        name = str(source.get("name") or source.get("path") or "")
        if not name:
            continue
        data = _source_data(source)
        if data is not None:
            payloads[name] = data
    return payloads


def load_recording_events(path: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
    recording = Path(path)
    events_path = recording / "events.jsonl" if recording.is_dir() else recording
    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        with events_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError as error:
                    warnings.append(f"events.jsonl line {line_number} JSONDecodeError: {error.msg}")
                    continue
                if isinstance(value, dict):
                    events.append(value)
    except FileNotFoundError:
        warnings.append(f"events file missing: {events_path}")
    except OSError as error:
        warnings.append(f"events file unreadable: {type(error).__name__}: {error}")
    return events, warnings


def _normal_logs_count(inventory: dict[str, Any]) -> int | None:
    resource_counts = _dict(inventory.get("resourceCounts"))
    for key in ("normal_logs", "woodcutting_logs", "logs"):
        count = _int(_dict(resource_counts.get(key)).get("count"))
        if count is not None:
            return count
    total = 0
    seen = False
    for item in inventory.get("items") or []:
        record = _dict(item)
        item_id = _int(_first(record.get("itemId"), record.get("id")))
        if item_id not in NORMAL_LOG_ITEM_IDS:
            continue
        total += _int(_first(record.get("quantity"), record.get("count"))) or 1
        seen = True
    return total if seen else None


def _inventory_from_payloads(payloads: dict[str, Any], high_value: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    activity = _dict(payloads.get("activity"))
    return _dict(
        _first(
            activity.get("inventoryState"),
            activity.get("inventory"),
            high_value.get("inventory"),
            baseline.get("inventory"),
        )
    )


def _player_from_payloads(payloads: dict[str, Any], high_value: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    player = _dict(_first(_dict(high_value.get("player")), _dict(payloads.get("activity")).get("player"), baseline.get("player")))
    if "worldPoint" not in player and {"worldX", "worldY"} & set(player):
        player = dict(player)
        player["worldPoint"] = {
            key: player.get(key)
            for key in ("worldX", "worldY", "plane")
            if player.get(key) is not None
        }
    return player


def _activity_state(payloads: dict[str, Any]) -> tuple[str | None, str | None, list[str]]:
    activity = _dict(payloads.get("activity"))
    state = _dict(_first(activity.get("activityState"), activity.get("activity")))
    wood = _dict(activity.get("woodcuttingState"))
    return (
        _first(state.get("apparentState"), state.get("state")),
        _first(wood.get("woodcuttingState"), wood.get("state")),
        [str(item) for item in wood.get("evidence") or []],
    )


def _menu_payload(status: dict[str, Any]) -> dict[str, Any]:
    hot = _dict(status.get("clientTickHot"))
    return _dict(_first(hot.get("postMenuSort"), hot.get("hoverMenu"), status.get("hoverMenu"), status.get("postMenuSort")))


def _last_click(status: dict[str, Any]) -> dict[str, Any] | None:
    hot = _dict(status.get("clientTickHot"))
    value = _first(hot.get("lastMenuOptionClicked"), status.get("lastMenuOptionClicked"))
    return value if isinstance(value, dict) else None


def _is_tree_name(value: Any) -> bool:
    return "tree" in _clean_menu_text(value).lower()


def _clean_menu_text(value: Any) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def _has_chop_action(actions: Any) -> bool:
    if isinstance(actions, str):
        actions = [actions]
    return any("chop" in str(action).lower() for action in actions or [])


def _tree_candidate_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    candidate = telemetry_schema.normalized_candidate(record, fallback_kind="object")
    name = _first(candidate.get("effectiveName"), record.get("objectName"), record.get("name"))
    actions = _first(candidate.get("effectiveActions"), record.get("actions"))
    if not _is_tree_name(name) or not _has_chop_action(actions):
        return None
    projection = _dict(record.get("projection"))
    return {
        "ref": _first(candidate.get("ref"), record.get("objectKey"), record.get("hash")),
        "id": _first(candidate.get("effectiveId"), candidate.get("rawId"), record.get("id")),
        "name": name,
        "actions": candidate.get("effectiveActions") or actions or [],
        "worldPoint": candidate.get("worldPoint"),
        "distance": candidate.get("distance"),
        "onScreen": _first(candidate.get("onScreen"), projection.get("onScreen")),
        "actionable": _first(projection.get("actionableByCanvas"), candidate.get("menuActionAvailable")),
        "aimPoint": _first(_dict(candidate.get("geometry")).get("aimPoint"), projection.get("aimPoint")),
        "resourceType": record.get("resourceType"),
    }


def _tree_candidates(payloads: dict[str, Any], high_value: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    status = _dict(payloads.get("status"))
    resource_census = _dict(status.get("worldModelResourceObjectCensus"))
    records.extend(item for item in resource_census.get("objects") or [] if isinstance(item, dict))
    records.extend(item for item in high_value.get("nearby_objects") or [] if isinstance(item, dict))
    records.extend(item for item in payloads.get("candidates") or [] if isinstance(item, dict))

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        candidate = _tree_candidate_from_record(record)
        if not candidate:
            continue
        key = str(_first(candidate.get("ref"), candidate.get("id"), candidate.get("worldPoint"), repr(candidate)))
        if key in seen:
            continue
        seen.add(key)
        result.append({key: value for key, value in candidate.items() if value not in (None, "", [], {})})
    return result


def _timeline_events(payloads: dict[str, Any]) -> list[dict[str, Any]]:
    events = payloads.get("events")
    if isinstance(events, list):
        return [event for event in events if isinstance(event, dict)]
    return []


def _snapshot_from_event(event: dict[str, Any]) -> dict[str, Any]:
    payloads = _payloads_by_source(event)
    high_value = _dict(event.get("high_value_fields"))
    baseline = _dict(payloads.get("baseline"))
    status = _dict(payloads.get("status"))
    activity = _dict(payloads.get("activity"))
    player = _player_from_payloads(payloads, high_value, baseline)
    inventory = _inventory_from_payloads(payloads, high_value, baseline)
    activity_state, woodcutting_state, wood_evidence = _activity_state(payloads)
    menu = _menu_payload(status)
    tree_candidates = _tree_candidates(payloads, high_value)
    tick = _int(
        _first(
            event.get("latest_tick"),
            high_value.get("latest_tick"),
            activity.get("latestTick"),
            status.get("latestTickProcessed"),
            status.get("latestTick"),
            baseline.get("latestTick"),
        )
    )
    return {
        "elapsedSeconds": _float(event.get("elapsed_seconds")),
        "wallTimeUtc": event.get("wall_time_utc"),
        "tick": tick,
        "exportSequence": _first(event.get("latest_export_sequence"), high_value.get("latest_export_sequence"), status.get("compactPacketLastSequence")),
        "player": player,
        "animation": _int(_first(player.get("animation"), player.get("animationId"))),
        "poseAnimation": _int(player.get("poseAnimation")),
        "worldPoint": player.get("worldPoint"),
        "inventory": inventory,
        "freeSlots": _int(inventory.get("freeSlots")),
        "filledSlots": _int(inventory.get("filledSlots")),
        "inventoryFull": bool(inventory.get("inventoryFull")) if inventory.get("inventoryFull") is not None else None,
        "normalLogs": _normal_logs_count(inventory),
        "activityState": activity_state,
        "woodcuttingState": woodcutting_state,
        "woodcuttingEvidence": wood_evidence,
        "menuEntries": [entry for entry in menu.get("entries") or [] if isinstance(entry, dict)],
        "menuOpen": menu.get("menuOpen"),
        "lastClick": _last_click(status),
        "treeCandidates": tree_candidates,
        "timelineEvents": _timeline_events(payloads),
    }


def _snapshot_from_context(context: dict[str, Any]) -> dict[str, Any]:
    payloads = {
        "baseline": context.get("baseline") or {},
        "status": context.get("status") or {},
        "activity": context.get("activity") or {},
        "candidates": context.get("candidates") or [],
        "events": context.get("events") or [],
    }
    event = {
        "event_type": "source_snapshot",
        "wall_time_utc": telemetry_sources.utc_now(),
        "elapsed_seconds": 0.0,
        "latest_tick": _first(_dict(payloads["status"]).get("latestTickProcessed"), _dict(payloads["baseline"]).get("latestTick")),
        "high_value_fields": telemetry_schema.normalized_telemetry(context),
        "sources": [{"name": name, "data": value} for name, value in payloads.items()],
    }
    return _snapshot_from_event(event)


def _timeline_click(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("eventType") or event.get("event_type") or "").lower()
    if "click" not in event_type and "menu" not in event_type:
        return None
    details = _dict(event.get("details"))
    click = _first(details.get("click"), details.get("menuOptionClicked"), event.get("click"), event.get("menuOptionClicked"))
    if isinstance(click, dict):
        record = dict(click)
    else:
        record = {
            "option": _first(event.get("option"), details.get("option")),
            "target": _first(event.get("target"), details.get("target")),
            "type": _first(event.get("type"), details.get("type")),
            "identifier": _first(event.get("identifier"), details.get("identifier")),
            "gameTickAtSample": _first(event.get("tick"), details.get("tick")),
            "timestampUtc": _first(event.get("generatedAtUtc"), details.get("timestampUtc")),
        }
    if not record.get("option") and not record.get("target"):
        return None
    record.setdefault("source", "event_timeline")
    return record


def _input_action_click(record: dict[str, Any]) -> dict[str, Any] | None:
    event_kind = str(record.get("eventKind") or "").lower()
    if event_kind and event_kind != "click":
        return None
    if str(record.get("button") or "").lower() not in {"left", ""}:
        return None
    classification = str(record.get("classification") or "")
    if classification and classification not in ACTION_CLICK_CLASSES:
        return None

    menu_context = _dict(record.get("menuContext"))
    selected = _dict(record.get("menuSelection"))
    option = _first(
        menu_context.get("hoverOption"),
        record.get("selectedOption"),
        selected.get("selectedOption"),
        _dict(record.get("targetContext")).get("targetAction"),
    )
    target = _first(
        menu_context.get("hoverTarget"),
        record.get("selectedTarget"),
        selected.get("selectedTarget"),
        _dict(record.get("targetContext")).get("targetName"),
    )
    if not option and not target:
        return None
    if not _has_chop_action(option) or not _is_tree_name(target):
        return None

    time = _dict(record.get("time"))
    position = _dict(record.get("position"))
    client = _dict(position.get("client"))
    canvas = _dict(position.get("canvas"))
    drag = _dict(record.get("dragContext"))
    return {
        "option": _clean_menu_text(option),
        "target": _clean_menu_text(target),
        "type": "INPUT_ACTION_HOVER_MENU",
        "identifier": _first(_dict(record.get("targetContext")).get("targetRef"), record.get("clickId")),
        "timestampUtc": time.get("wallTimeUtc"),
        "elapsedSeconds": time.get("elapsedSeconds"),
        "eventSeq": record.get("eventSeq"),
        "clickId": record.get("clickId"),
        "mouseCanvasX": _first(canvas.get("x"), client.get("x")),
        "mouseCanvasY": _first(canvas.get("y"), client.get("y")),
        "dragDistancePx": drag.get("dragDistancePx"),
        "inputClassification": classification,
        "source": "input_action_menu_hover",
        "evidence": [
            "input_action_menu_context_hover_option",
            "input_action_menu_context_hover_target",
        ],
    }


def _click_key(click: dict[str, Any]) -> tuple[Any, ...]:
    return (
        click.get("source"),
        click.get("clickId"),
        click.get("eventSeq"),
        click.get("option"),
        click.get("target"),
        click.get("type"),
        click.get("identifier"),
        click.get("itemId"),
        click.get("param0"),
        click.get("param1"),
        click.get("gameTickAtSample"),
        click.get("timestampUtc"),
        click.get("mouseCanvasX"),
        click.get("mouseCanvasY"),
    )


def _is_chop_click(click: dict[str, Any]) -> bool:
    option = str(click.get("option") or "").lower()
    target = str(click.get("target") or "").lower()
    click_type = str(click.get("type") or "").lower()
    return "chop" in option and ("tree" in target or "game_object" in click_type)


def _click_summary(click: dict[str, Any], snapshot: dict[str, Any] | None, classification: str) -> dict[str, Any]:
    tick = _int(_first(click.get("gameTickAtSample"), click.get("tick"), _dict(snapshot or {}).get("tick")))
    return {
        key: value
        for key, value in {
            "classification": classification,
            "option": click.get("option"),
            "target": click.get("target"),
            "type": click.get("type"),
            "identifier": click.get("identifier"),
            "itemId": click.get("itemId"),
            "tick": tick,
            "timestampUtc": click.get("timestampUtc"),
            "mouseCanvasX": click.get("mouseCanvasX"),
            "mouseCanvasY": click.get("mouseCanvasY"),
            "param0": click.get("param0"),
            "param1": click.get("param1"),
            "eventSeq": click.get("eventSeq"),
            "clickId": click.get("clickId"),
            "elapsedSeconds": click.get("elapsedSeconds"),
            "dragDistancePx": click.get("dragDistancePx"),
            "inputClassification": click.get("inputClassification"),
            "evidence": click.get("evidence"),
            "snapshotTick": _dict(snapshot or {}).get("tick"),
            "snapshotElapsedSeconds": _dict(snapshot or {}).get("elapsedSeconds"),
            "source": click.get("source") or "lastMenuOptionClicked",
        }.items()
        if value not in (None, "", [], {})
    }


def _classify_clicks(
    snapshots: list[dict[str, Any]],
    timeline_events: list[dict[str, Any]],
    start_wall: datetime | None,
    start_tick: int | None,
    *,
    input_action_classifications: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    explicit_clicks = [_timeline_click(event) for event in timeline_events]
    explicit_clicks = [click for click in explicit_clicks if click]
    input_clicks = [
        click
        for click in (_input_action_click(record) for record in input_action_classifications or [])
        if click
    ]
    explicit_clicks.extend(input_clicks)
    click_source = "event_timeline" if explicit_clicks else "lastMenuOptionClicked"
    if input_clicks and click_source == "event_timeline":
        click_source = "event_timeline+input_action_classifications"
    elif input_clicks:
        click_source = "input_action_classifications"
    if explicit_clicks:
        records = [(click, None) for click in explicit_clicks]
        latest_tick = max((_int(snapshot.get("tick")) for snapshot in snapshots if _int(snapshot.get("tick")) is not None), default=None)
    else:
        records = [
            (snapshot.get("lastClick"), snapshot)
            for snapshot in snapshots
            if isinstance(snapshot.get("lastClick"), dict)
        ]
        latest_tick = None

    seen: set[tuple[Any, ...]] = set()
    fresh: list[dict[str, Any]] = []
    classified: list[dict[str, Any]] = []
    counts = {
        "fresh_chop_click": 0,
        "repeated_old_click": 0,
        "pre_recording_click": 0,
        "unrelated_click": 0,
        "ambiguous_click": 0,
    }
    for raw_click, snapshot in records:
        click = _dict(raw_click)
        key = _click_key(click)
        if key in seen:
            classification = "repeated_old_click"
        else:
            seen.add(key)
            click_time = _parse_time(click.get("timestampUtc"))
            click_tick = _int(_first(click.get("gameTickAtSample"), click.get("tick")))
            snapshot_tick = _int(_dict(snapshot or {}).get("tick"))
            if start_wall and click_time and click_time < start_wall:
                classification = "pre_recording_click"
            elif start_tick is not None and click_tick is not None and click_tick < start_tick - 2:
                classification = "pre_recording_click"
            elif not _is_chop_click(click):
                classification = "unrelated_click"
            elif not any(value is not None for value in (click_time, click_tick, click.get("wallTimeMillis"))):
                classification = "ambiguous_click"
            elif start_wall is None and start_tick is None and click_tick is not None and snapshot_tick is not None and snapshot_tick - click_tick > MAX_CONTEXT_CLICK_AGE_TICKS:
                classification = "ambiguous_click"
            elif start_wall is None and start_tick is None and latest_tick is not None and click_tick is not None and latest_tick - click_tick > MAX_CONTEXT_CLICK_AGE_TICKS:
                classification = "ambiguous_click"
            else:
                classification = "fresh_chop_click"
        summary = _click_summary(click, snapshot, classification)
        counts[classification] += 1
        classified.append(summary)
        if classification == "fresh_chop_click":
            fresh.append(summary)
    return {
        "source": click_source,
        "inputActionChopClickCount": len(input_clicks),
        "freshChopClicks": fresh,
        "freshChopClickCount": len(fresh),
        "ignoredRepeatedClickCount": counts["repeated_old_click"],
        "ignoredPreRecordingClickCount": counts["pre_recording_click"],
        "ignoredUnrelatedClickCount": counts["unrelated_click"],
        "ambiguousClickCount": counts["ambiguous_click"],
        "lastFreshChopClick": fresh[-1] if fresh else None,
        "classifiedClicks": classified[:50],
    }


def _input_tree_target_evidence(input_action_classifications: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for record in input_action_classifications or []:
        menu_context = _dict(record.get("menuContext"))
        option = menu_context.get("hoverOption")
        target = menu_context.get("hoverTarget")
        if not _has_chop_action(option) or not _is_tree_name(target):
            continue
        key = (record.get("eventSeq"), option, target)
        if key in seen:
            continue
        seen.add(key)
        time = _dict(record.get("time"))
        evidence.append(
            {
                "eventSeq": record.get("eventSeq"),
                "clickId": record.get("clickId"),
                "eventKind": record.get("eventKind"),
                "classification": record.get("classification"),
                "option": _clean_menu_text(option),
                "target": _clean_menu_text(target),
                "elapsedSeconds": time.get("elapsedSeconds"),
                "timestampUtc": time.get("wallTimeUtc"),
                "source": "input_action_menu_context",
            }
        )
    return evidence[:50]


def _unique_timeline_events(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for snapshot in snapshots:
        for event in snapshot.get("timelineEvents") or []:
            key = (
                event.get("eventType"),
                event.get("tick"),
                event.get("generatedAtUtc"),
                event.get("summary"),
                repr(event.get("currentValue")),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(event)
    return sorted(result, key=lambda event: (_int(event.get("tick")) or -1, str(event.get("generatedAtUtc") or "")))


def _log_gain_events(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    previous: int | None = None
    for snapshot in snapshots:
        logs = _int(snapshot.get("normalLogs"))
        if logs is not None and previous is not None and logs > previous:
            events.append(
                {
                    "tick": snapshot.get("tick"),
                    "elapsedSeconds": snapshot.get("elapsedSeconds"),
                    "normalLogsBefore": previous,
                    "normalLogsAfter": logs,
                    "logsGained": logs - previous,
                }
            )
        if logs is not None:
            previous = logs
    return events


def _target_depleted_events(snapshots: list[dict[str, Any]], timeline_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    previous_state: str | None = None
    for snapshot in snapshots:
        state = snapshot.get("woodcuttingState")
        if state == "target_depleted" and previous_state != "target_depleted":
            events.append(
                {
                    "source": "activity_state",
                    "tick": snapshot.get("tick"),
                    "elapsedSeconds": snapshot.get("elapsedSeconds"),
                    "evidence": snapshot.get("woodcuttingEvidence") or [],
                }
            )
        if state:
            previous_state = str(state)
    seen_ticks = {event.get("tick") for event in events}
    for event in timeline_events:
        if event.get("eventType") != "target_depleted":
            continue
        tick = event.get("tick")
        if tick in seen_ticks:
            continue
        seen_ticks.add(tick)
        events.append(
            {
                "source": "event_timeline",
                "tick": tick,
                "summary": event.get("summary"),
                "target": telemetry_schema.compact_value(event.get("relatedCandidate"), limit=6),
            }
        )
    return sorted(events, key=lambda event: _int(event.get("tick")) or 0)


def _find_animation_start(active_ticks: list[int], start_tick: int | None, end_tick: int | None) -> int | None:
    if not active_ticks:
        return None
    if end_tick is None:
        return active_ticks[0]
    candidates = [
        tick for tick in active_ticks
        if tick <= end_tick and (start_tick is None or tick >= start_tick)
    ]
    if candidates:
        return candidates[0]
    previous = [tick for tick in active_ticks if tick <= end_tick]
    return previous[-1] if previous else None


def _cycles(log_events: list[dict[str, Any]], clicks: list[dict[str, Any]], active_ticks: list[int], depleted_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cycles: list[dict[str, Any]] = []
    fresh_clicks = sorted(clicks, key=lambda click: (_int(click.get("tick")) or 0, str(click.get("timestampUtc") or "")))
    for index, log_event in enumerate(log_events, start=1):
        log_tick = _int(log_event.get("tick"))
        click = None
        for candidate in fresh_clicks:
            click_tick = _int(candidate.get("tick"))
            if log_tick is None or click_tick is None or click_tick <= log_tick:
                click = candidate
            else:
                break
        click_tick = _int(_dict(click).get("tick"))
        animation_start = _find_animation_start(active_ticks, click_tick, log_tick)
        target_depleted = any(
            (_int(event.get("tick")) is not None and log_tick is not None and _int(event.get("tick")) >= log_tick - 3 and _int(event.get("tick")) <= log_tick + 10)
            for event in depleted_events
        )
        confidence = 0.4
        notes: list[str] = []
        if click:
            confidence += 0.2
        else:
            notes.append("no fresh click matched this log gain")
        if animation_start is not None:
            confidence += 0.2
        else:
            notes.append("no woodcutting animation matched this log gain")
        if log_event.get("logsGained"):
            confidence += 0.2
        if target_depleted:
            confidence += 0.05
        cycles.append(
            {
                "cycleIndex": index,
                "startTick": _first(click_tick, animation_start, log_tick),
                "endTick": log_tick,
                "click": click or {},
                "animationStartTick": animation_start,
                "logGainTick": log_tick,
                "logsGained": log_event.get("logsGained"),
                "targetDepleted": target_depleted,
                "confidence": round(min(0.95, confidence), 3),
                "notes": notes,
            }
        )
    return cycles


def _phase(latest: dict[str, Any] | None, inventory_full: bool, logs_gained: int, fresh_clicks: list[dict[str, Any]], tree_count: int) -> str:
    if not latest:
        return "unknown"
    if inventory_full:
        return "inventory_full"
    if latest.get("woodcuttingState") == "target_depleted":
        return "target_depleted"
    if _int(latest.get("animation")) == WOODCUTTING_ANIMATION_ID:
        return "chopping"
    last_tick = _int(latest.get("tick"))
    last_click_tick = _int(_dict(fresh_clicks[-1] if fresh_clicks else {}).get("tick"))
    if last_tick is not None and last_click_tick is not None and last_tick - last_click_tick <= 5:
        return "chop_clicked"
    if logs_gained > 0:
        return "log_gained"
    if tree_count:
        return "tree_available"
    return "idle"


def analyze_snapshots(
    snapshots: list[dict[str, Any]],
    *,
    start_event: dict[str, Any] | None = None,
    stop_event: dict[str, Any] | None = None,
    input_action_classifications: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    snapshots = sorted(snapshots, key=lambda snapshot: (_float(snapshot.get("elapsedSeconds")) is None, _float(snapshot.get("elapsedSeconds")) or 0.0))
    warnings = list(warnings or [])
    ticks = [_int(snapshot.get("tick")) for snapshot in snapshots if _int(snapshot.get("tick")) is not None]
    start_wall = _parse_time(_dict(start_event).get("wall_time_utc"))
    start_tick = min(ticks) if start_event and ticks else None
    duration = _first(
        _dict(stop_event).get("duration_seconds"),
        _dict(stop_event).get("elapsed_seconds"),
        (
            (_float(snapshots[-1].get("elapsedSeconds")) or 0.0) - (_float(snapshots[0].get("elapsedSeconds")) or 0.0)
            if len(snapshots) >= 2
            else 0.0
        ),
    )
    timeline_events = _unique_timeline_events(snapshots)
    click_summary = _classify_clicks(
        snapshots,
        timeline_events,
        start_wall,
        start_tick,
        input_action_classifications=input_action_classifications,
    )
    log_events = _log_gain_events(snapshots)
    active_ticks = [
        _int(snapshot.get("tick"))
        for snapshot in snapshots
        if _int(snapshot.get("animation")) == WOODCUTTING_ANIMATION_ID and _int(snapshot.get("tick")) is not None
    ]
    all_trees = [tree for snapshot in snapshots for tree in snapshot.get("treeCandidates") or []]
    input_tree_evidence = _input_tree_target_evidence(input_action_classifications)
    tree_ids = sorted({item for item in (_int(tree.get("id")) for tree in all_trees) if item is not None})
    tree_keys = {
        str(_first(tree.get("ref"), tree.get("worldPoint"), tree.get("id"), repr(tree)))
        for tree in all_trees
    }
    tree_evidence_present = bool(tree_keys or input_tree_evidence)
    depleted_events = _target_depleted_events(snapshots, timeline_events)
    cycles = _cycles(log_events, click_summary["freshChopClicks"], active_ticks, depleted_events)
    first_snapshot = snapshots[0] if snapshots else {}
    latest_snapshot = snapshots[-1] if snapshots else {}
    normal_start = _int(first_snapshot.get("normalLogs"))
    normal_end = _int(latest_snapshot.get("normalLogs"))
    logs_gained = (
        normal_end - normal_start
        if normal_start is not None and normal_end is not None
        else sum(_int(event.get("logsGained")) or 0 for event in log_events)
    )
    inventory_full = bool(_first(latest_snapshot.get("inventoryFull"), (_int(latest_snapshot.get("freeSlots")) == 0 if _int(latest_snapshot.get("freeSlots")) is not None else None)))

    evidence: list[str] = []
    if normal_start is not None and normal_end is not None:
        evidence.append(f"normal logs changed {normal_start} -> {normal_end}")
    if first_snapshot.get("freeSlots") is not None and latest_snapshot.get("freeSlots") is not None:
        evidence.append(f"free slots changed {first_snapshot.get('freeSlots')} -> {latest_snapshot.get('freeSlots')}")
    if active_ticks:
        evidence.append(f"woodcutting animation {WOODCUTTING_ANIMATION_ID} observed in {len(active_ticks)} snapshot(s)")
    if click_summary["freshChopClickCount"]:
        evidence.append(f"{click_summary['freshChopClickCount']} fresh Chop down click(s) observed")
    if all_trees:
        evidence.append(f"{len(tree_keys)} unique tree target(s) observed")
    if input_tree_evidence:
        evidence.append(f"{len(input_tree_evidence)} input/menu tree hover evidence record(s) observed")
    if inventory_full:
        evidence.append("inventory full detected")

    signals = sum(
        bool(value)
        for value in (
            all_trees,
            active_ticks,
            click_summary["freshChopClickCount"],
            logs_gained > 0,
            first_snapshot.get("freeSlots") is not None,
            any(snapshot.get("woodcuttingState") for snapshot in snapshots),
            input_tree_evidence,
        )
    )
    if signals == 0:
        status = "FAIL"
        warnings.append("No woodcutting lifecycle signals were found.")
    elif logs_gained > 0 and tree_evidence_present and (active_ticks or click_summary["freshChopClickCount"]):
        status = "PASS"
    else:
        status = "WARN"
        if not tree_evidence_present:
            warnings.append("No tree target evidence was found.")
        if not active_ticks:
            warnings.append(f"Woodcutting animation {WOODCUTTING_ANIMATION_ID} was not observed.")
        if not click_summary["freshChopClickCount"]:
            warnings.append("No fresh Chop down click was found.")
        if logs_gained <= 0:
            warnings.append("No positive normal log gain was found.")

    confidence = 0.0
    if signals:
        confidence = 0.25
        if logs_gained > 0:
            confidence += 0.25
        if click_summary["freshChopClickCount"]:
            confidence += 0.2
        if active_ticks:
            confidence += 0.15
        if tree_evidence_present:
            confidence += 0.1
        if inventory_full:
            confidence += 0.05
    lifecycle = {
        "schema": SCHEMA_VERSION,
        "status": status,
        "phase": _phase(latest_snapshot, inventory_full, logs_gained, click_summary["freshChopClicks"], len(all_trees)),
        "confidence": round(min(0.95, confidence), 3),
        "tickRange": {"start": min(ticks) if ticks else None, "end": max(ticks) if ticks else None},
        "durationSeconds": duration,
        "inventory": {
            "freeSlotsStart": first_snapshot.get("freeSlots"),
            "freeSlotsEnd": latest_snapshot.get("freeSlots"),
            "normalLogsStart": normal_start,
            "normalLogsEnd": normal_end,
            "normalLogsGained": logs_gained,
            "inventoryFull": inventory_full,
        },
        "animation": {
            "woodcuttingAnimationId": WOODCUTTING_ANIMATION_ID,
            "activeTicks": active_ticks,
            "activeSnapshotCount": len(active_ticks),
            "firstSeenTick": active_ticks[0] if active_ticks else None,
            "lastSeenTick": active_ticks[-1] if active_ticks else None,
        },
        "clicks": click_summary,
        "targets": {
            "treeIdsSeen": tree_ids,
            "treeCountSeen": len(tree_keys),
            "inputTreeTargetEvidenceCount": len(input_tree_evidence),
            "inputTreeTargetEvidence": input_tree_evidence,
            "lastTarget": (latest_snapshot.get("treeCandidates") or [None])[0],
            "targetDepletedEvents": depleted_events,
        },
        "current": {
            "animationActive": _int(latest_snapshot.get("animation")) == WOODCUTTING_ANIMATION_ID,
            "targetDepleted": latest_snapshot.get("woodcuttingState") == "target_depleted",
            "woodcuttingState": latest_snapshot.get("woodcuttingState"),
            "activityState": latest_snapshot.get("activityState"),
            "normalLogs": normal_end,
            "freeSlots": latest_snapshot.get("freeSlots"),
            "inventoryFull": inventory_full,
        },
        "cycles": cycles,
        "warnings": sorted(set(str(warning) for warning in warnings if warning)),
        "evidence": evidence,
    }
    return lifecycle


def analyze_events(
    events: list[dict[str, Any]],
    *,
    input_action_classifications: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    start_event = next((event for event in events if event.get("event_type") == "recording_start"), None)
    stop_event = next((event for event in reversed(events) if event.get("event_type") == "recording_stop"), None)
    snapshots = [_snapshot_from_event(event) for event in events if event.get("event_type") == "source_snapshot"]
    return analyze_snapshots(
        snapshots,
        start_event=start_event,
        stop_event=stop_event,
        input_action_classifications=input_action_classifications,
        warnings=warnings,
    )


def analyze_recording(path: str | Path) -> dict[str, Any]:
    events, warnings = load_recording_events(path)
    recording = Path(path)
    input_actions, input_warnings = _load_jsonl(recording / "input_action_classifications.jsonl")
    lifecycle = analyze_events(events, input_action_classifications=input_actions, warnings=[*warnings, *input_warnings])
    lifecycle["recordingPath"] = str(Path(path))
    return lifecycle


def analyze_context(context: dict[str, Any]) -> dict[str, Any]:
    return analyze_snapshots([_snapshot_from_context(context)])


def attach_interruption(lifecycle: dict[str, Any], interruption: dict[str, Any]) -> dict[str, Any]:
    result = dict(lifecycle or {})
    if not interruption:
        return result
    combat = _dict(interruption.get("combat"))
    result["interruption"] = {
        "schema": "woodcutting_interruption_summary.v1",
        "status": interruption.get("status"),
        "interruptionDetected": bool(interruption.get("interruptionDetected")),
        "interruptionType": interruption.get("interruptionType"),
        "primaryCause": interruption.get("primaryCause"),
        "taskResumed": bool(interruption.get("taskResumed")),
        "confidence": interruption.get("confidence"),
        "combatObserved": bool(combat.get("combatObserved")),
        "hitsplatsSeen": combat.get("hitsplatsSeen") or 0,
        "missingCapabilities": interruption.get("missingCapabilities") or [],
        "warnings": interruption.get("warnings") or [],
    }
    return result


def compact_lifecycle(lifecycle: dict[str, Any]) -> dict[str, Any]:
    inventory = _dict(lifecycle.get("inventory"))
    animation = _dict(lifecycle.get("animation"))
    clicks = _dict(lifecycle.get("clicks"))
    targets = _dict(lifecycle.get("targets"))
    current = _dict(lifecycle.get("current"))
    interruption = _dict(lifecycle.get("interruption"))
    return {
        "schema": lifecycle.get("schema") or SCHEMA_VERSION,
        "status": lifecycle.get("status"),
        "phase": lifecycle.get("phase"),
        "confidence": lifecycle.get("confidence"),
        "freshChopClickCount": clicks.get("freshChopClickCount") or 0,
        "normalLogsGained": inventory.get("normalLogsGained"),
        "normalLogs": current.get("normalLogs"),
        "freeSlots": current.get("freeSlots"),
        "freeSlotsStart": inventory.get("freeSlotsStart"),
        "freeSlotsEnd": inventory.get("freeSlotsEnd"),
        "inventoryFull": inventory.get("inventoryFull"),
        "animationActive": current.get("animationActive"),
        "activeSnapshotCount": animation.get("activeSnapshotCount") or 0,
        "targetDepleted": current.get("targetDepleted"),
        "lastTarget": targets.get("lastTarget"),
        "cycleCount": len(lifecycle.get("cycles") or []),
        "interruption": interruption,
        "warnings": lifecycle.get("warnings") or [],
    }

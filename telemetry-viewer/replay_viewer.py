import argparse
import json
import mimetypes
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


sys.path.insert(0, str(Path(__file__).resolve().parent))

from telemetry_paths import (  # noqa: E402
    classify_frame_state,
    frame_index_by_tick,
    frame_index_stats,
    frame_timing_fields,
    find_newest_session,
    get_sessions_dir,
    is_segmented_session,
    iter_jsonl,
    list_event_files,
    list_tick_files,
    load_frame_index_summaries,
    resolve_frame_path,
    safe_read_json,
    session_size_mb,
)


CATEGORY_BY_EVENT_TYPE = {
    "HitsplatApplied": "combat",
    "ProjectileMoved": "combat",
    "GraphicsObjectCreated": "combat",
    "InteractingChanged": "combat",
    "AnimationChanged": "combat",
    "NpcDeath": "combat",
    "ItemContainerChanged": "inventory",
    "ItemSpawned": "inventory",
    "ItemDespawned": "inventory",
    "ItemQuantityChanged": "inventory",
    "WidgetLoaded": "ui",
    "WidgetClosed": "ui",
    "MenuOpened": "ui",
    "VarbitChanged": "var",
    "VarClientIntChanged": "var",
    "VarClientStrChanged": "var",
    "NpcSpawned": "entity",
    "NpcDespawned": "entity",
    "NpcChanged": "entity",
    "PlayerSpawned": "entity",
    "PlayerDespawned": "entity",
    "PlayerChanged": "entity",
    "StatChanged": "skills",
    "GameStateChanged": "world",
    "OverheadTextChanged": "world",
}

MAX_INLINE_DICTIONARY_BYTES = 256 * 1024
RECENT_EXAMPLE_COUNT = 3


COMBAT_EVENT_TYPES = {
    "HitsplatApplied",
    "ProjectileMoved",
    "GraphicsObjectCreated",
    "InteractingChanged",
    "AnimationChanged",
    "NpcDeath",
}
INVENTORY_EVENT_TYPES = {
    "ItemContainerChanged",
    "ItemSpawned",
    "ItemDespawned",
    "ItemQuantityChanged",
    "StatChanged",
    "AnimationChanged",
}
UI_EVENT_TYPES = {
    "WidgetLoaded",
    "WidgetClosed",
    "MenuOpened",
    "VarbitChanged",
    "VarClientIntChanged",
    "VarClientStrChanged",
}


def count_items(items) -> int:
    if not isinstance(items, list):
        return 0

    return sum(
        1
        for item in items
        if isinstance(item, dict)
        and item.get("itemId", -1) > 0
        and item.get("quantity", 0) > 0
    )


def actor_summary(actor) -> str | None:
    if not isinstance(actor, dict):
        return None

    actor_type = actor.get("actorType") or actor.get("type") or "UNKNOWN"
    name = actor.get("name") or actor.get("nameHash") or actor.get("id") or actor.get("index")
    animation = actor.get("animation")
    parts = [str(actor_type)]

    if name is not None:
        parts.append(str(name))

    if animation is not None:
        parts.append(f"anim={animation}")

    return " ".join(parts)


def interacting_target(status: dict) -> str | None:
    interacting_type = status.get("interactingType")
    interacting_name = status.get("interactingName") or status.get("interactingId")

    if interacting_type and interacting_type != "UNKNOWN":
        return f"{interacting_type}:{interacting_name}"

    return None


def event_summary_text(event_type: str | None, payload) -> str:
    if not isinstance(payload, dict):
        return ""

    if event_type == "StatChanged":
        return f"{payload.get('skill')} level={payload.get('level')} boosted={payload.get('boostedLevel')}"

    if event_type == "MenuOpened":
        entries = payload.get("entries") or []
        preview = []

        for entry in entries[:3]:
            if isinstance(entry, dict):
                preview.append(f"{entry.get('option', '')} {entry.get('target', '')}".strip())

        return f"menuEntryCount={payload.get('menuEntryCount')} entries={'; '.join(preview)}"

    if event_type == "ItemContainerChanged":
        return f"containerId={payload.get('containerId')} size={payload.get('size')}"

    if event_type in ("ItemSpawned", "ItemDespawned", "ItemQuantityChanged"):
        return f"id={payload.get('id')} qty={payload.get('quantity')} {payload.get('worldX')},{payload.get('worldY')}"

    if event_type in (
        "AnimationChanged",
        "NpcSpawned",
        "NpcDespawned",
        "PlayerSpawned",
        "PlayerDespawned",
        "PlayerChanged",
        "NpcDeath",
    ):
        return actor_summary(payload.get("actor") if "actor" in payload else payload) or ""

    if event_type == "InteractingChanged":
        return f"{actor_summary(payload.get('source'))} -> {actor_summary(payload.get('target'))}"

    if event_type == "HitsplatApplied":
        return f"{actor_summary(payload.get('actor'))} amount={payload.get('amount')} type={payload.get('hitsplatType')}"

    if event_type == "ProjectileMoved":
        return f"id={payload.get('id')} target={actor_summary(payload.get('target'))}"

    if event_type == "GraphicsObjectCreated":
        return f"id={payload.get('id')} at={payload.get('worldX')},{payload.get('worldY')}"

    if event_type == "GameStateChanged":
        return f"gameState={payload.get('gameState')}"

    if event_type and event_type.startswith("Var"):
        return " ".join(
            f"{key}={payload.get(key)}"
            for key in ("index", "varbitId", "varpId", "value")
            if key in payload
        )

    return " ".join(f"{key}={value}" for key, value in list(payload.items())[:4])


def summarize_event(source: Path, event: dict) -> dict:
    event_type = event.get("eventType")
    payload = event.get("payload")

    return {
        "tickId": event.get("tickId"),
        "timestampUtc": event.get("timestampUtc"),
        "eventType": event_type,
        "category": CATEGORY_BY_EVENT_TYPE.get(event_type, "unknown"),
        "summary": event_summary_text(event_type, payload),
        "source": str(source),
    }


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def duration_seconds(ticks: list[dict]) -> float | None:
    if len(ticks) < 2:
        return None

    first = parse_timestamp(ticks[0].get("timestampUtc"))
    last = parse_timestamp(ticks[-1].get("timestampUtc"))

    if first is None or last is None:
        return None

    return max(0.0, (last - first).total_seconds())


def percentile(values: list[float | int], fraction: float) -> float | int | None:
    numeric = sorted(value for value in values if isinstance(value, (int, float)))

    if not numeric:
        return None

    index = round((len(numeric) - 1) * fraction)
    return numeric[index]


def compact_counts(counter: Counter, limit: int | None = None) -> dict:
    items = counter.most_common(limit)
    return {key: value for key, value in items}


def compact_event(event: dict) -> dict:
    return {
        "tickId": event.get("tickId"),
        "timestampUtc": event.get("timestampUtc"),
        "eventType": event.get("eventType"),
        "category": event.get("category", "unknown"),
        "summary": event.get("summary"),
    }


def compact_tick(tick: dict, event_counts_by_category: Counter | None = None, important_event_types=None) -> dict:
    return {
        "tickId": tick.get("tickId"),
        "timestampUtc": tick.get("timestampUtc"),
        "hp": value_pair(tick.get("hpBoosted"), tick.get("hpReal")),
        "prayer": value_pair(tick.get("prayerBoosted"), tick.get("prayerReal")),
        "runEnergyPercent": tick.get("runEnergyPercent"),
        "position": {
            "worldX": tick.get("worldX"),
            "worldY": tick.get("worldY"),
            "plane": tick.get("plane"),
        },
        "interactingTarget": tick.get("interactingTarget"),
        "activePrayerNames": tick.get("activePrayerNames") or [],
        "inventoryCount": tick.get("inventoryCount"),
        "equipmentCount": tick.get("equipmentCount"),
        "eventCountsByCategory": dict(event_counts_by_category or {}),
        "importantEventTypes": list(important_event_types or []),
        "frameStatus": tick.get("frameIndexStatus") or tick.get("frameCaptureStatus"),
        "frameExists": tick.get("frameExists"),
        "framePending": tick.get("framePending"),
        "frameExpiredOrMissing": tick.get("frameExpiredOrMissing"),
        "frameDropped": tick.get("frameDropped"),
        "frameDeleted": tick.get("frameDeleted"),
        "frameFailed": tick.get("frameFailed"),
        "frameWriteDelayMs": tick.get("frameWriteDelayMs"),
        "frameTotalLatencyMs": tick.get("frameTotalLatencyMs"),
        "captureErrorCount": tick.get("captureErrorCount", 0),
    }


def value_pair(current, maximum) -> dict:
    return {"current": current, "max": maximum}


def tick_delta(previous: dict | None, current: dict, key: str):
    if previous is None:
        return None

    previous_value = previous.get(key)
    current_value = current.get(key)

    if previous_value == current_value:
        return None

    return {"from": previous_value, "to": current_value}


def prayer_set(tick: dict) -> set:
    return set(tick.get("activePrayerNames") or [])


def count_frame_files(session_path: Path) -> int:
    frames_dir = session_path / "frames"

    if not frames_dir.exists():
        return 0

    try:
        return sum(
            1
            for path in frames_dir.iterdir()
            if path.is_file() and path.name != "frame_index.jsonl"
        )
    except OSError:
        return 0


def event_type_breakdown(events: list[dict]) -> dict:
    breakdown = {}

    for event in events:
        event_type = event.get("eventType") or "UNKNOWN"
        entry = breakdown.setdefault(
            event_type,
            {
                "count": 0,
                "category": event.get("category", "unknown"),
                "firstTickId": event.get("tickId"),
                "lastTickId": event.get("tickId"),
                "firstExamples": [],
                "recentExamples": [],
            },
        )
        entry["count"] += 1
        entry["lastTickId"] = event.get("tickId")

        if len(entry["firstExamples"]) < RECENT_EXAMPLE_COUNT:
            entry["firstExamples"].append(compact_event(event))

        entry["recentExamples"].append(compact_event(event))
        entry["recentExamples"] = entry["recentExamples"][-RECENT_EXAMPLE_COUNT:]

    return dict(sorted(breakdown.items(), key=lambda item: (-item[1]["count"], item[0])))


def grouped_event_breakdown(events: list[dict], key_name: str) -> dict:
    breakdown = {}

    for event in events:
        key = event.get(key_name) or "unknown"
        entry = breakdown.setdefault(
            key,
            {
                "count": 0,
                "firstTickId": event.get("tickId"),
                "lastTickId": event.get("tickId"),
                "firstExamples": [],
                "recentExamples": [],
            },
        )
        entry["count"] += 1
        entry["lastTickId"] = event.get("tickId")

        if len(entry["firstExamples"]) < RECENT_EXAMPLE_COUNT:
            entry["firstExamples"].append(compact_event(event))

        entry["recentExamples"].append(compact_event(event))
        entry["recentExamples"] = entry["recentExamples"][-RECENT_EXAMPLE_COUNT:]

    return dict(sorted(breakdown.items(), key=lambda item: (-item[1]["count"], item[0])))


def build_analysis(
    session_path: Path,
    tick_summaries: list[dict],
    events: list[dict],
    events_by_tick: dict,
    frame_index_summary: dict,
) -> dict:
    event_type_counts = Counter(event.get("eventType") or "UNKNOWN" for event in events)
    category_counts = Counter(event.get("category") or "unknown" for event in events)
    capture_error_count = sum(tick.get("captureErrorCount", 0) for tick in tick_summaries)
    frame_write_delays = [
        tick["frameWriteDelayMs"]
        for tick in tick_summaries
        if isinstance(tick.get("frameWriteDelayMs"), (int, float))
    ]
    timeline = []
    combat_timeline = []
    inventory_timeline = []
    ui_timeline = []
    hp_prayer_changes = []
    active_prayer_changes = []
    inventory_count_changes = []
    previous_tick = None

    for tick in tick_summaries:
        tick_id = tick.get("tickId")
        tick_events = events_by_tick.get(tick_id, [])
        event_counts_by_category = Counter(event.get("category") or "unknown" for event in tick_events)
        important_event_types = sorted({event.get("eventType") for event in tick_events if event.get("eventType")})
        row = compact_tick(tick, event_counts_by_category, important_event_types)
        timeline.append(row)

        hp_delta = tick_delta(previous_tick, tick, "hpBoosted")
        prayer_delta = tick_delta(previous_tick, tick, "prayerBoosted")
        inventory_delta = tick_delta(previous_tick, tick, "inventoryCount")

        if hp_delta or prayer_delta:
            hp_prayer_changes.append({
                "tickId": tick_id,
                "timestampUtc": tick.get("timestampUtc"),
                "hp": hp_delta,
                "prayer": prayer_delta,
            })

        if previous_tick is not None and prayer_set(previous_tick) != prayer_set(tick):
            previous_prayers = prayer_set(previous_tick)
            current_prayers = prayer_set(tick)
            active_prayer_changes.append({
                "tickId": tick_id,
                "timestampUtc": tick.get("timestampUtc"),
                "activated": sorted(current_prayers - previous_prayers),
                "deactivated": sorted(previous_prayers - current_prayers),
                "active": sorted(current_prayers),
            })

        if inventory_delta:
            inventory_count_changes.append({
                "tickId": tick_id,
                "timestampUtc": tick.get("timestampUtc"),
                "inventoryCount": inventory_delta,
            })

        combat_events = [event for event in tick_events if event.get("eventType") in COMBAT_EVENT_TYPES]
        inventory_events = [event for event in tick_events if event.get("eventType") in INVENTORY_EVENT_TYPES]
        ui_events = [event for event in tick_events if event.get("eventType") in UI_EVENT_TYPES]

        if combat_events or hp_delta or prayer_delta or (active_prayer_changes and active_prayer_changes[-1].get("tickId") == tick_id):
            combat_timeline.append({
                **row,
                "events": [compact_event(event) for event in combat_events],
                "hpChange": hp_delta,
                "prayerChange": prayer_delta,
                "activePrayerChange": active_prayer_changes[-1] if active_prayer_changes and active_prayer_changes[-1].get("tickId") == tick_id else None,
            })

        if inventory_events or inventory_delta:
            inventory_timeline.append({
                **row,
                "events": [compact_event(event) for event in inventory_events],
                "inventoryCountChange": inventory_delta,
            })

        if ui_events:
            ui_timeline.append({
                **row,
                "events": [compact_event(event) for event in ui_events],
            })

        previous_tick = tick

    event_breakdown = event_type_breakdown(events)
    ui_counts = Counter(event.get("eventType") or "UNKNOWN" for event in events if event.get("eventType") in UI_EVENT_TYPES)

    return {
        "summary": {
            "tickCount": len(tick_summaries),
            "eventCount": len(events),
            "durationEstimateSeconds": duration_seconds(tick_summaries),
            "firstTickId": tick_summaries[0].get("tickId") if tick_summaries else None,
            "lastTickId": tick_summaries[-1].get("tickId") if tick_summaries else None,
            "frameCount": count_frame_files(session_path),
            "frameIndexCount": frame_index_summary.get("totalRecords", 0),
            "frameWrittenCount": frame_index_summary.get("FrameWritten", 0),
            "frameDroppedCount": frame_index_summary.get("FrameDropped", 0),
            "frameDeletedCount": frame_index_summary.get("FrameDeleted", 0),
            "avgFrameWriteDelayMs": frame_index_summary.get("avgWriteDelayMs"),
            "p95FrameWriteDelayMs": percentile(frame_write_delays, 0.95),
            "maxFrameWriteDelayMs": frame_index_summary.get("maxWriteDelayMs"),
            "captureErrorCount": capture_error_count,
            "topEventTypes": compact_counts(event_type_counts, 20),
            "topEventCategories": compact_counts(category_counts, 20),
        },
        "timeline": timeline,
        "events": {
            "eventTypeCounts": compact_counts(event_type_counts),
            "categoryCounts": compact_counts(category_counts),
            "eventTypes": event_breakdown,
            "categories": grouped_event_breakdown(events, "category"),
        },
        "combat": {
            "eventTypes": {event_type: event_breakdown.get(event_type, {"count": 0}) for event_type in sorted(COMBAT_EVENT_TYPES)},
            "timeline": combat_timeline,
            "hpPrayerChanges": hp_prayer_changes,
            "activePrayerChanges": active_prayer_changes,
        },
        "inventory": {
            "eventTypes": {event_type: event_breakdown.get(event_type, {"count": 0}) for event_type in sorted(INVENTORY_EVENT_TYPES)},
            "timeline": inventory_timeline,
            "inventoryCountChanges": inventory_count_changes,
            "statChanges": [compact_event(event) for event in events if event.get("eventType") == "StatChanged"],
        },
        "ui": {
            "eventTypeCounts": compact_counts(ui_counts),
            "eventTypes": {event_type: event_breakdown.get(event_type, {"count": 0}) for event_type in sorted(UI_EVENT_TYPES)},
            "timeline": ui_timeline,
            "jumpTicks": sorted({event.get("tickId") for event in events if event.get("eventType") in UI_EVENT_TYPES and event.get("tickId") is not None}),
        },
    }


def summarize_tick(
    session_path: Path,
    source: Path,
    tick: dict,
    *,
    is_latest: bool,
    active_session: bool,
    frame_index: dict | None = None,
    frame_index_available: bool = False,
) -> dict:
    local_player = tick.get("localPlayer") or {}
    status = tick.get("status") or {}
    active_prayers = [
        prayer.get("name")
        for prayer in (tick.get("activePrayers") or [])
        if isinstance(prayer, dict) and prayer.get("active") and prayer.get("name")
    ]
    frame = classify_frame_state(
        session_path,
        tick,
        is_latest=is_latest,
        active_session=active_session,
    )
    frame_timing = frame_timing_fields(frame_index)
    frame_index_event_type = frame_index.get("eventType") if frame_index else None
    frame_dropped = frame_index_event_type == "FrameDropped"
    frame_deleted = frame_index_event_type == "FrameDeleted"
    frame_failed = frame_index_event_type == "FrameFailed"

    return {
        "tickId": tick.get("tickId"),
        "timestampUtc": tick.get("timestampUtc"),
        "gameState": tick.get("gameState"),
        "worldX": local_player.get("worldX"),
        "worldY": local_player.get("worldY"),
        "plane": local_player.get("plane"),
        "hpBoosted": status.get("hitpointsBoosted"),
        "hpReal": status.get("hitpointsReal"),
        "prayerBoosted": status.get("prayerBoosted"),
        "prayerReal": status.get("prayerReal"),
        "runEnergyPercent": status.get("runEnergyPercent"),
        "inventoryCount": count_items(tick.get("inventory")),
        "equipmentCount": count_items(tick.get("equipment")),
        "npcCount": len(tick.get("npcs") or []),
        "playerCount": len(tick.get("players") or []),
        "widgetCount": len(tick.get("widgets") or []),
        "sceneObjectsCount": len(tick.get("sceneObjects") or []),
        "groundItemsCount": len(tick.get("groundItems") or []),
        "activePrayerNames": active_prayers,
        "interactingTarget": interacting_target(status),
        "framePath": frame["framePath"],
        "frameExists": frame["frameExists"],
        "framePending": frame["framePending"],
        "frameExpiredOrMissing": frame["frameExpiredOrMissing"],
        "frameCaptureStatus": frame["frameCaptureStatus"],
        "frameCaptureSource": frame["frameCaptureSource"],
        "frameIndexAvailable": frame_index_available,
        "frameIndexEventType": frame_index_event_type,
        "frameIndexStatus": frame_timing["frameIndexStatus"],
        "frameRequested": frame_timing["frameRequested"],
        "frameCaptured": frame_timing["frameCaptured"],
        "frameQueued": frame_timing["frameQueued"],
        "frameWritten": frame_timing["frameWritten"],
        "frameDropped": frame_dropped,
        "frameDeleted": frame_deleted,
        "frameFailed": frame_failed,
        "frameWriteDelayMs": frame_timing["frameWriteDelayMs"],
        "frameTotalLatencyMs": frame_timing["frameTotalLatencyMs"],
        "frameCaptureLatencyMs": frame_timing["frameCaptureLatencyMs"],
        "frameQueueLatencyMs": frame_timing["frameQueueLatencyMs"],
        "latestFrameIndexEvent": frame_index,
        "captureErrorCount": len(tick.get("captureErrors") or []),
        "source": str(source),
    }


def load_dictionaries(session_path: Path) -> dict:
    dictionary_dir = session_path / "dictionaries"
    summaries = {}

    for name in ("items", "npcs", "objects"):
        path = dictionary_dir / f"{name}.json"
        entry = {"exists": path.exists(), "count": 0, "inline": None}

        if path.exists():
            try:
                entry["sizeBytes"] = path.stat().st_size
            except OSError:
                entry["sizeBytes"] = None

            data = safe_read_json(path)

            if isinstance(data, dict):
                entry["count"] = len(data)

                if entry.get("sizeBytes") is not None and entry["sizeBytes"] <= MAX_INLINE_DICTIONARY_BYTES:
                    entry["inline"] = data
            elif isinstance(data, list):
                entry["count"] = len(data)

                if entry.get("sizeBytes") is not None and entry["sizeBytes"] <= MAX_INLINE_DICTIONARY_BYTES:
                    entry["inline"] = data

        summaries[name] = entry

    return summaries


def session_from_arg(session: str | None, sessions_dir: str | None) -> Path | None:
    if session:
        return Path(session).expanduser().resolve()

    return find_newest_session(get_sessions_dir(sessions_dir))


def load_replay(session_path: Path) -> dict:
    manifest = safe_read_json(session_path / "manifest.json")
    manifest = manifest if isinstance(manifest, dict) else None
    tick_files = list_tick_files(session_path)
    event_files = list_event_files(session_path)
    frame_index_summaries = load_frame_index_summaries(session_path)
    frame_index_lookup = frame_index_by_tick(frame_index_summaries)
    frame_index_summary = frame_index_stats(frame_index_summaries)
    frame_index_available = bool(frame_index_summaries)
    raw_ticks = []
    raw_tick_by_id = {}
    tick_summaries = []
    events = []
    events_by_tick = defaultdict(list)
    event_type_counts = Counter()
    active_session = bool(manifest and manifest.get("active"))

    for source, tick in iter_jsonl(tick_files):
        if not isinstance(tick, dict):
            continue

        raw_ticks.append((source, tick))
        tick_id = tick.get("tickId")

        if tick_id is not None:
            raw_tick_by_id[str(tick_id)] = tick

    latest_tick = raw_ticks[-1][1] if raw_ticks else None

    for source, tick in raw_ticks:
        summary = summarize_tick(
            session_path,
            source,
            tick,
            is_latest=tick is latest_tick,
            active_session=active_session,
            frame_index=frame_index_lookup.get(tick.get("tickId")),
            frame_index_available=frame_index_available,
        )
        tick_summaries.append(summary)

    for source, event in iter_jsonl(event_files):
        if not isinstance(event, dict):
            continue

        summary = summarize_event(source, event)
        events.append(summary)
        event_type_counts[summary.get("eventType") or "UNKNOWN"] += 1
        tick_id = summary.get("tickId")

        if tick_id is not None:
            try:
                events_by_tick[int(tick_id)].append(summary)
            except (TypeError, ValueError):
                pass

    first_tick_id = tick_summaries[0].get("tickId") if tick_summaries else None
    last_tick_id = tick_summaries[-1].get("tickId") if tick_summaries else None
    analysis = build_analysis(
        session_path,
        tick_summaries,
        events,
        events_by_tick,
        frame_index_summary,
    )

    return {
        "sessionPath": session_path,
        "manifest": manifest,
        "layout": "segmented" if is_segmented_session(session_path) else "legacy-flat",
        "tickFiles": tick_files,
        "eventFiles": event_files,
        "frameIndexSummary": frame_index_summary,
        "rawTickById": raw_tick_by_id,
        "frameIndexByTick": {str(key): value for key, value in frame_index_lookup.items()},
        "tickSummaries": tick_summaries,
        "events": events,
        "eventsByTick": events_by_tick,
        "eventTypeCounts": dict(event_type_counts.most_common(20)),
        "analysis": analysis,
        "dictionaries": load_dictionaries(session_path),
        "loadedAtUtc": datetime.now(timezone.utc).isoformat(),
        "firstTickId": first_tick_id,
        "lastTickId": last_tick_id,
    }


def is_under_directory(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def html_page() -> bytes:
    body = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OSRS Telemetry Replay Viewer</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --text: #202124;
      --muted: #656a70;
      --line: #d9ddd5;
      --accent: #2f6f73;
      --accent-strong: #1f5154;
      --warn-bg: #fff6df;
      --warn-text: #694a00;
      --code-bg: #f0f2ef;
    }

    * {
      box-sizing: border-box;
    }

    html,
    body {
      height: 100%;
      overflow: hidden;
    }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.4;
    }

    button,
    input,
    select {
      font: inherit;
    }

    button {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 6px;
      padding: 0.45rem 0.65rem;
      cursor: pointer;
    }

    button:hover {
      border-color: var(--accent);
    }

    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #ffffff;
    }

    button.primary:hover {
      background: var(--accent-strong);
      border-color: var(--accent-strong);
    }

    input,
    select {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      padding: 0.45rem 0.55rem;
      min-width: 0;
    }

    .app {
      height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr auto;
      overflow: hidden;
    }

    header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 1rem;
      padding: 0.55rem 0.75rem;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      min-height: 0;
    }

    h1 {
      font-size: 1rem;
      margin: 0;
      font-weight: 700;
    }

    .session-meta {
      color: var(--muted);
      font-size: 0.875rem;
      text-align: right;
      overflow-wrap: anywhere;
    }

    main {
      display: grid;
      grid-template-columns: minmax(0, 2fr) minmax(360px, 1fr);
      gap: 0.75rem;
      min-height: 0;
      overflow: hidden;
      padding: 0.75rem;
    }

    .frame-panel,
    .detail-panel {
      min-width: 0;
      min-height: 0;
    }

    .frame-panel {
      display: flex;
      flex-direction: column;
      overflow: hidden;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
    }

    .frame-topline {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      padding: 0.45rem 0.6rem;
      border-bottom: 1px solid var(--line);
      color: var(--muted);
      font-size: 0.9rem;
    }

    .frame-actions {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      min-width: 0;
    }

    .frame-wrap {
      flex: 1;
      min-height: 0;
      background: #111612;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }

    #frameImage {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      display: none;
    }

    #frameImage.actual-width {
      width: auto;
      max-width: none;
      height: auto;
      max-height: 100%;
    }

    .missing-frame {
      max-width: 28rem;
      padding: 1rem;
      color: #e8ece7;
      text-align: center;
    }

    .detail-panel {
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      overflow-y: auto;
      padding-right: 0.15rem;
    }

    .section {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      min-width: 0;
      overflow: hidden;
      flex: 0 0 auto;
    }

    .section.events-section {
      display: flex;
      flex-direction: column;
      min-height: 0;
      max-height: 34vh;
    }

    .section.analysis-section {
      display: flex;
      flex-direction: column;
      min-height: 0;
      max-height: 44vh;
    }

    .section.raw-section {
      overflow: visible;
    }

    .section h2 {
      margin: 0;
      padding: 0.65rem 0.75rem;
      border-bottom: 1px solid var(--line);
      font-size: 0.95rem;
    }

    details.section summary {
      cursor: pointer;
      padding: 0.65rem 0.75rem;
      font-size: 0.95rem;
      font-weight: 700;
      border-bottom: 1px solid transparent;
    }

    details.section[open] summary {
      border-bottom-color: var(--line);
    }

    .summary-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.5rem 0.75rem;
      padding: 0.75rem;
      font-size: 0.9rem;
    }

    .metric {
      min-width: 0;
    }

    .metric span {
      display: block;
      color: var(--muted);
      font-size: 0.78rem;
    }

    .metric strong {
      overflow-wrap: anywhere;
    }

    .analysis-body {
      display: flex;
      flex-direction: column;
      gap: 0.65rem;
      min-height: 0;
      overflow: hidden;
      padding: 0.75rem;
    }

    .analysis-cards {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 0.45rem;
    }

    .analysis-card {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0.45rem;
      min-width: 0;
      background: #fbfcfa;
    }

    .analysis-card span {
      display: block;
      color: var(--muted);
      font-size: 0.72rem;
    }

    .analysis-card strong {
      display: block;
      font-size: 0.92rem;
      overflow-wrap: anywhere;
    }

    .analysis-controls {
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
      min-width: 0;
    }

    .analysis-filter-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem 0.55rem;
      align-items: center;
      min-width: 0;
      font-size: 0.78rem;
    }

    .analysis-filter-row label {
      display: inline-flex;
      align-items: center;
      gap: 0.25rem;
      white-space: nowrap;
    }

    #analysisEventSearch {
      width: 100%;
      font-size: 0.82rem;
      padding: 0.35rem 0.45rem;
    }

    .analysis-table-wrap,
    .analysis-quick-grid {
      min-height: 0;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 6px;
    }

    .analysis-table-wrap {
      max-height: 18vh;
    }

    .analysis-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.78rem;
    }

    .analysis-table th,
    .analysis-table td {
      border-bottom: 1px solid #edf0eb;
      padding: 0.35rem 0.4rem;
      text-align: left;
      vertical-align: top;
    }

    .analysis-table th {
      position: sticky;
      top: 0;
      background: var(--panel);
      z-index: 1;
      color: var(--muted);
      font-weight: 700;
    }

    .analysis-row {
      cursor: pointer;
    }

    .analysis-row:hover {
      background: #f5f8f4;
    }

    .badge {
      display: inline-block;
      border-radius: 999px;
      padding: 0.08rem 0.4rem;
      margin: 0.08rem 0.12rem 0.08rem 0;
      border: 1px solid var(--line);
      color: var(--text);
      background: #f4f5f2;
      font-size: 0.72rem;
      white-space: nowrap;
    }

    .badge.combat {
      background: #fdecec;
      border-color: #f1b4b4;
    }

    .badge.inventory {
      background: #eef6e8;
      border-color: #bfdbb3;
    }

    .badge.ui {
      background: #eef2fb;
      border-color: #b9c7ed;
    }

    .badge.var {
      background: #f5eefb;
      border-color: #d4bbe8;
    }

    .badge.frame {
      background: #ecf7f7;
      border-color: #a9d5d7;
    }

    .badge.error {
      background: #fff1df;
      border-color: #edc184;
    }

    .analysis-quick-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 0.5rem;
      max-height: 16vh;
      padding: 0.5rem;
    }

    .analysis-quick-panel {
      min-width: 0;
    }

    .analysis-quick-panel h3 {
      margin: 0 0 0.35rem;
      font-size: 0.82rem;
    }

    .analysis-jump {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.35rem;
      padding: 0.25rem 0;
      border-top: 1px solid #edf0eb;
      font-size: 0.76rem;
    }

    .analysis-jump:first-of-type {
      border-top: 0;
    }

    .analysis-jump button {
      padding: 0.2rem 0.35rem;
      font-size: 0.72rem;
      flex: 0 0 auto;
    }

    .events-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
      padding: 0.65rem 0.75rem;
      border-bottom: 1px solid var(--line);
    }

    .events-head h2 {
      padding: 0;
      border: 0;
    }

    #eventFilter {
      width: 12rem;
    }

    .events-list {
      max-height: 28vh;
      overflow: auto;
      padding: 0.5rem 0.75rem;
      font-size: 0.86rem;
    }

    .event-row {
      padding: 0.4rem 0;
      border-bottom: 1px solid #edf0eb;
    }

    .event-row:last-child {
      border-bottom: 0;
    }

    .event-type {
      font-weight: 700;
    }

    .event-meta {
      color: var(--muted);
      font-size: 0.78rem;
    }

    pre {
      margin: 0;
      max-height: 28vh;
      overflow: auto;
      background: var(--code-bg);
      padding: 0.75rem;
      font-size: 0.78rem;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    footer {
      display: grid;
      grid-template-columns: auto auto auto minmax(12rem, 1fr) auto auto auto;
      gap: 0.6rem;
      align-items: center;
      padding: 0.55rem 0.75rem;
      border-top: 1px solid var(--line);
      background: var(--panel);
      z-index: 10;
    }

    #timeline {
      width: 100%;
    }

    .jump {
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }

    .status {
      color: var(--muted);
      font-size: 0.85rem;
      overflow-wrap: anywhere;
    }

    .warning {
      background: var(--warn-bg);
      color: var(--warn-text);
      padding: 0.55rem 0.75rem;
      border-bottom: 1px solid #f0d590;
      display: none;
    }

    @media (max-width: 900px) {
      main {
        grid-template-columns: 1fr;
        grid-template-rows: minmax(0, 1fr) minmax(240px, 0.9fr);
      }

      .frame-panel {
        min-height: 0;
      }

      .detail-panel {
        min-height: 0;
      }

      footer {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      footer > * {
        width: 100%;
      }

      .analysis-cards {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .analysis-quick-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <header>
      <h1>OSRS Telemetry Replay Viewer</h1>
      <div class="session-meta" id="sessionMeta">Loading session...</div>
    </header>

    <main>
      <section class="frame-panel">
        <div class="frame-topline">
          <div id="tickLabel">Tick -</div>
          <div class="frame-actions">
            <div id="timestampLabel">-</div>
            <button id="fitToggle" type="button">Fit: contain</button>
          </div>
        </div>
        <div class="frame-wrap">
          <img id="frameImage" alt="Telemetry frame">
          <div id="missingFrame" class="missing-frame">
            Frame missing, pending, or expired by retention. Tick data is still valid.
          </div>
        </div>
      </section>

      <section class="detail-panel">
        <div id="loadWarning" class="warning"></div>

        <section class="section">
          <h2>Tick Summary</h2>
          <div class="summary-grid" id="summaryGrid"></div>
        </section>

        <section class="section analysis-section">
          <h2>Analysis</h2>
          <div class="analysis-body">
            <div class="analysis-cards" id="analysisCards"></div>
            <div class="analysis-controls">
              <input id="analysisEventSearch" type="search" placeholder="Filter event type">
              <div class="analysis-filter-row" id="analysisCategories">
                <label><input type="checkbox" data-analysis-category="combat"> combat</label>
                <label><input type="checkbox" data-analysis-category="inventory"> inventory</label>
                <label><input type="checkbox" data-analysis-category="ui"> ui</label>
                <label><input type="checkbox" data-analysis-category="var"> var</label>
                <label><input type="checkbox" data-analysis-category="entity"> entity</label>
                <label><input type="checkbox" data-analysis-category="skills"> skills</label>
                <label><input type="checkbox" data-analysis-category="world"> world</label>
                <label><input type="checkbox" data-analysis-category="unknown"> unknown</label>
              </div>
              <div class="analysis-filter-row">
                <label><input id="analysisOnlyEvents" type="checkbox"> only ticks with events</label>
                <label><input id="analysisOnlyIssues" type="checkbox"> only frame/capture issues</label>
              </div>
            </div>
            <div class="analysis-table-wrap">
              <table class="analysis-table">
                <thead>
                  <tr>
                    <th>Tick</th>
                    <th>Vitals</th>
                    <th>Target</th>
                    <th>Events</th>
                    <th>Frame</th>
                  </tr>
                </thead>
                <tbody id="analysisTimeline"></tbody>
              </table>
            </div>
            <div class="analysis-quick-grid">
              <div class="analysis-quick-panel">
                <h3>Combat Events</h3>
                <div id="analysisCombat"></div>
              </div>
              <div class="analysis-quick-panel">
                <h3>Inventory/Skilling Events</h3>
                <div id="analysisInventory"></div>
              </div>
              <div class="analysis-quick-panel">
                <h3>UI/Menu Events</h3>
                <div id="analysisUi"></div>
              </div>
            </div>
          </div>
        </section>

        <section class="section events-section">
          <div class="events-head">
            <h2>Recent Events</h2>
            <input id="eventFilter" type="search" placeholder="Filter event type">
          </div>
          <div class="events-list" id="eventsList"></div>
        </section>

        <details class="section raw-section">
          <summary>Raw Tick JSON</summary>
          <pre id="rawJson">{}</pre>
        </details>
      </section>
    </main>

    <footer>
      <button id="prevTick" type="button">Previous tick</button>
      <button id="playPause" class="primary" type="button">Play</button>
      <input id="timeline" type="range" min="0" max="0" value="0">
      <button id="nextTick" type="button">Next tick</button>
      <div class="jump">
        <input id="jumpTick" type="number" placeholder="tickId">
        <button id="jumpButton" type="button">Jump</button>
      </div>
      <select id="speed">
        <option value="1200">0.5x</option>
        <option value="600" selected>1x</option>
        <option value="300">2x</option>
        <option value="120">5x</option>
      </select>
      <div class="status" id="statusText">No ticks loaded</div>
    </footer>
  </div>

  <script>
    const ANALYSIS_CATEGORIES = ["combat", "inventory", "ui", "var", "entity", "skills", "world", "unknown"];
    const ANALYSIS_FILTER_STORAGE_KEY = "osrsTelemetryReplayAnalysisFilters";

    const state = {
      session: null,
      ticks: [],
      currentIndex: 0,
      currentEvents: [],
      rawTick: null,
      analysisSummary: null,
      analysisTimeline: [],
      analysisCombat: null,
      analysisInventory: null,
      analysisUi: null,
      analysisFilters: loadAnalysisFilters(),
      playTimer: null,
      frameFit: "contain"
    };

    const el = {
      sessionMeta: document.getElementById("sessionMeta"),
      tickLabel: document.getElementById("tickLabel"),
      timestampLabel: document.getElementById("timestampLabel"),
      fitToggle: document.getElementById("fitToggle"),
      frameImage: document.getElementById("frameImage"),
      missingFrame: document.getElementById("missingFrame"),
      loadWarning: document.getElementById("loadWarning"),
      summaryGrid: document.getElementById("summaryGrid"),
      analysisCards: document.getElementById("analysisCards"),
      analysisEventSearch: document.getElementById("analysisEventSearch"),
      analysisCategoryInputs: Array.from(document.querySelectorAll("[data-analysis-category]")),
      analysisOnlyEvents: document.getElementById("analysisOnlyEvents"),
      analysisOnlyIssues: document.getElementById("analysisOnlyIssues"),
      analysisTimeline: document.getElementById("analysisTimeline"),
      analysisCombat: document.getElementById("analysisCombat"),
      analysisInventory: document.getElementById("analysisInventory"),
      analysisUi: document.getElementById("analysisUi"),
      eventsList: document.getElementById("eventsList"),
      eventFilter: document.getElementById("eventFilter"),
      rawJson: document.getElementById("rawJson"),
      prevTick: document.getElementById("prevTick"),
      nextTick: document.getElementById("nextTick"),
      playPause: document.getElementById("playPause"),
      timeline: document.getElementById("timeline"),
      jumpTick: document.getElementById("jumpTick"),
      jumpButton: document.getElementById("jumpButton"),
      speed: document.getElementById("speed"),
      statusText: document.getElementById("statusText")
    };

    async function fetchJson(url) {
      const response = await fetch(url, { cache: "no-store" });

      if (!response.ok) {
        throw new Error(`${url} returned ${response.status}`);
      }

      return response.json();
    }

    function valueOrDash(value) {
      return value === null || value === undefined || value === "" ? "-" : value;
    }

    function escapeHtml(value) {
      return String(valueOrDash(value))
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function setWarning(message) {
      el.loadWarning.textContent = message || "";
      el.loadWarning.style.display = message ? "block" : "none";
    }

    function metric(label, value) {
      return `<div class="metric"><span>${label}</span><strong>${valueOrDash(value)}</strong></div>`;
    }

    function formatMs(value) {
      return value === null || value === undefined ? "-" : `${value} ms`;
    }

    function formatDuration(seconds) {
      if (seconds === null || seconds === undefined) {
        return "-";
      }

      const total = Math.round(seconds);
      const minutes = Math.floor(total / 60);
      const remaining = total % 60;

      return minutes ? `${minutes}m ${remaining}s` : `${remaining}s`;
    }

    function defaultAnalysisFilters() {
      return {
        categories: Object.fromEntries(ANALYSIS_CATEGORIES.map((category) => [category, true])),
        eventTypeSearch: "",
        onlyEvents: false,
        onlyIssues: false
      };
    }

    function loadAnalysisFilters() {
      const defaults = defaultAnalysisFilters();

      try {
        const stored = JSON.parse(localStorage.getItem(ANALYSIS_FILTER_STORAGE_KEY) || "{}");

        return {
          ...defaults,
          ...stored,
          categories: {
            ...defaults.categories,
            ...(stored.categories || {})
          }
        };
      } catch (error) {
        return defaults;
      }
    }

    function saveAnalysisFilters() {
      try {
        localStorage.setItem(ANALYSIS_FILTER_STORAGE_KEY, JSON.stringify(state.analysisFilters));
      } catch (error) {
        // localStorage can be unavailable in some browser contexts; filtering still works in memory.
      }
    }

    function applyAnalysisFilterControls() {
      const filters = state.analysisFilters;
      el.analysisEventSearch.value = filters.eventTypeSearch || "";
      el.analysisOnlyEvents.checked = Boolean(filters.onlyEvents);
      el.analysisOnlyIssues.checked = Boolean(filters.onlyIssues);

      for (const input of el.analysisCategoryInputs) {
        input.checked = Boolean(filters.categories?.[input.dataset.analysisCategory]);
      }
    }

    function readAnalysisFilterControls() {
      return {
        categories: Object.fromEntries(
          el.analysisCategoryInputs.map((input) => [input.dataset.analysisCategory, input.checked])
        ),
        eventTypeSearch: el.analysisEventSearch.value.trim(),
        onlyEvents: el.analysisOnlyEvents.checked,
        onlyIssues: el.analysisOnlyIssues.checked
      };
    }

    function updateAnalysisFilters() {
      state.analysisFilters = readAnalysisFilterControls();
      saveAnalysisFilters();
      renderAnalysisTimeline();
    }

    function firstKey(counts) {
      if (!counts || !Object.keys(counts).length) {
        return "-";
      }

      return Object.entries(counts)
        .sort((left, right) => right[1] - left[1])[0][0];
    }

    function analysisCard(label, value) {
      return `<div class="analysis-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
    }

    function badge(label, kind = "") {
      if (!label) {
        return "";
      }

      return `<span class="badge ${kind}">${escapeHtml(label)}</span>`;
    }

    function categoryBadges(counts) {
      if (!counts || !Object.keys(counts).length) {
        return "-";
      }

      return Object.entries(counts)
        .map(([category, count]) => badge(`${category}:${count}`, category))
        .join("");
    }

    function eventBadges(types) {
      if (!Array.isArray(types) || !types.length) {
        return "-";
      }

      return types.slice(0, 4)
        .map((eventType) => badge(eventType, eventTypeToBadgeKind(eventType)))
        .join("");
    }

    function eventTypeToBadgeKind(eventType) {
      const mapping = {
        HitsplatApplied: "combat",
        ProjectileMoved: "combat",
        GraphicsObjectCreated: "combat",
        InteractingChanged: "combat",
        AnimationChanged: "combat",
        NpcDeath: "combat",
        ItemContainerChanged: "inventory",
        ItemSpawned: "inventory",
        ItemDespawned: "inventory",
        ItemQuantityChanged: "inventory",
        StatChanged: "inventory",
        WidgetLoaded: "ui",
        WidgetClosed: "ui",
        MenuOpened: "ui",
        VarbitChanged: "var",
        VarClientIntChanged: "var",
        VarClientStrChanged: "var"
      };

      return mapping[eventType] || "";
    }

    function rowEventCount(row) {
      return Object.values(row.eventCountsByCategory || {})
        .reduce((total, count) => total + Number(count || 0), 0);
    }

    function allAnalysisCategoriesSelected() {
      return ANALYSIS_CATEGORIES.every((category) => state.analysisFilters.categories?.[category]);
    }

    function rowMatchesCategoryFilter(row) {
      const counts = row.eventCountsByCategory || {};

      if (!Object.keys(counts).length) {
        return allAnalysisCategoriesSelected();
      }

      return Object.keys(counts).some((category) => state.analysisFilters.categories?.[category]);
    }

    function rowMatchesEventTypeSearch(row) {
      const search = (state.analysisFilters.eventTypeSearch || "").toLowerCase();

      if (!search) {
        return true;
      }

      return (row.importantEventTypes || [])
        .some((eventType) => String(eventType || "").toLowerCase().includes(search));
    }

    function isHighFrameLatency(row) {
      const p95 = state.analysisSummary?.p95FrameWriteDelayMs;

      if (p95 && row.frameWriteDelayMs >= p95) {
        return true;
      }

      return Boolean(row.frameTotalLatencyMs && row.frameTotalLatencyMs >= 1000);
    }

    function frameIssueLabels(row) {
      const labels = [];

      if (row.captureErrorCount) {
        labels.push(`capture errors:${row.captureErrorCount}`);
      }

      if (row.frameExpiredOrMissing) {
        labels.push("frame missing/expired");
      }

      if (row.frameDropped) {
        labels.push("dropped frame");
      }

      if (row.frameFailed) {
        labels.push("frame failed");
      }

      if (isHighFrameLatency(row)) {
        labels.push("high frame latency");
      }

      return labels;
    }

    function hasFrameOrCaptureIssue(row) {
      return frameIssueLabels(row).length > 0;
    }

    function rowMatchesAnalysisFilters(row) {
      if (state.analysisFilters.onlyEvents && rowEventCount(row) === 0) {
        return false;
      }

      if (state.analysisFilters.onlyIssues && !hasFrameOrCaptureIssue(row)) {
        return false;
      }

      return rowMatchesCategoryFilter(row) && rowMatchesEventTypeSearch(row);
    }

    function frameIssueBadges(row) {
      return frameIssueLabels(row)
        .map((label) => badge(label, "error"))
        .join("");
    }

    function renderAnalysisSummary() {
      const summary = state.analysisSummary || {};
      const delay = `${formatMs(summary.avgFrameWriteDelayMs)} / ${formatMs(summary.p95FrameWriteDelayMs)}`;
      el.analysisCards.innerHTML = [
        analysisCard("Ticks", summary.tickCount),
        analysisCard("Events", summary.eventCount),
        analysisCard("Duration", formatDuration(summary.durationEstimateSeconds)),
        analysisCard("Frames written", summary.frameWrittenCount),
        analysisCard("Dropped frames", summary.frameDroppedCount),
        analysisCard("Capture errors", summary.captureErrorCount),
        analysisCard("Avg / p95 delay", delay),
        analysisCard("Top event type", firstKey(summary.topEventTypes))
      ].join("");
    }

    function renderAnalysisTimeline() {
      const rows = (state.analysisTimeline || []).filter(rowMatchesAnalysisFilters);

      if (!rows.length) {
        el.analysisTimeline.innerHTML = `<tr><td colspan="5">No analysis rows match the current filters.</td></tr>`;
        return;
      }

      el.analysisTimeline.innerHTML = rows.map((row) => {
        const hp = row.hp ? `${valueOrDash(row.hp.current)}/${valueOrDash(row.hp.max)}` : "-";
        const prayer = row.prayer ? `${valueOrDash(row.prayer.current)}/${valueOrDash(row.prayer.max)}` : "-";
        const frameBadges = [
          row.frameStatus ? badge(row.frameStatus, "frame") : "",
          row.frameWriteDelayMs !== null && row.frameWriteDelayMs !== undefined ? badge(`write ${formatMs(row.frameWriteDelayMs)}`, "frame") : "",
          row.frameTotalLatencyMs !== null && row.frameTotalLatencyMs !== undefined ? badge(`lat ${formatMs(row.frameTotalLatencyMs)}`, "frame") : "",
          frameIssueBadges(row)
        ].join("");

        return `
          <tr class="analysis-row" data-tick-id="${escapeHtml(row.tickId)}">
            <td>${escapeHtml(row.tickId)}</td>
            <td>HP ${escapeHtml(hp)}<br>Pray ${escapeHtml(prayer)}<br>Run ${escapeHtml(valueOrDash(row.runEnergyPercent))}</td>
            <td>${escapeHtml(row.interactingTarget)}</td>
            <td>${categoryBadges(row.eventCountsByCategory)}<br>${eventBadges(row.importantEventTypes)}</td>
            <td>${frameBadges || "-"}</td>
          </tr>
        `;
      }).join("");
    }

    function timelineRows(source) {
      return Array.isArray(source?.timeline) ? source.timeline : [];
    }

    function renderQuickPanel(container, rows) {
      const visibleRows = rows.slice(0, 8);

      if (!visibleRows.length) {
        container.innerHTML = `<div class="event-meta">No matching events.</div>`;
        return;
      }

      container.innerHTML = visibleRows.map((row) => {
        const events = Array.isArray(row.events) && row.events.length
          ? row.events.map((event) => event.eventType).filter(Boolean).slice(0, 2).join(", ")
          : row.importantEventTypes?.slice(0, 2).join(", ") || "state change";

        return `
          <div class="analysis-jump">
            <span>tick ${escapeHtml(row.tickId)}<br><span class="event-meta">${escapeHtml(events)}</span></span>
            <button type="button" data-tick-id="${escapeHtml(row.tickId)}">Jump</button>
          </div>
        `;
      }).join("");
    }

    function renderAnalysisQuickPanels() {
      renderQuickPanel(el.analysisCombat, timelineRows(state.analysisCombat));
      renderQuickPanel(el.analysisInventory, timelineRows(state.analysisInventory));
      renderQuickPanel(el.analysisUi, timelineRows(state.analysisUi));
    }

    function renderAnalysis() {
      renderAnalysisSummary();
      renderAnalysisTimeline();
      renderAnalysisQuickPanels();
    }

    function frameIndexState(tick) {
      if (!tick.frameIndexAvailable) {
        return "unavailable";
      }

      if (!tick.frameRequested) {
        return "no event";
      }

      return tick.frameIndexEventType || tick.frameIndexStatus || "indexed";
    }

    function frameLifecycle(tick) {
      if (!tick.frameIndexAvailable) {
        return "unavailable";
      }

      if (!tick.frameRequested) {
        return "no event";
      }

      const states = [];

      if (tick.frameRequested) {
        states.push("requested");
      }

      if (tick.frameCaptured) {
        states.push("captured");
      }

      if (tick.frameQueued) {
        states.push("queued");
      }

      if (tick.frameWritten) {
        states.push("written");
      }

      if (tick.frameDropped) {
        states.push("dropped");
      }

      if (tick.frameDeleted) {
        states.push("deleted");
      }

      if (tick.frameFailed) {
        states.push("failed");
      }

      return states.join(" / ");
    }

    function renderSummary(tick) {
      const position = [tick.worldX, tick.worldY, tick.plane].map(valueOrDash).join(", ");
      const hp = `${valueOrDash(tick.hpBoosted)} / ${valueOrDash(tick.hpReal)}`;
      const prayer = `${valueOrDash(tick.prayerBoosted)} / ${valueOrDash(tick.prayerReal)}`;
      const activePrayers = Array.isArray(tick.activePrayerNames) && tick.activePrayerNames.length
        ? tick.activePrayerNames.join(", ")
        : "-";
      const frameState = tick.frameExists
        ? "exists"
        : tick.framePending
          ? "pending"
          : tick.frameExpiredOrMissing
            ? "expiredOrMissing"
            : "-";

      el.summaryGrid.innerHTML = [
        metric("Game state", tick.gameState),
        metric("Position", position),
        metric("HP", hp),
        metric("Prayer", prayer),
        metric("Run", tick.runEnergyPercent),
        metric("Active prayers", activePrayers),
        metric("Interacting", tick.interactingTarget),
        metric("Inventory", tick.inventoryCount),
        metric("Equipment", tick.equipmentCount),
        metric("NPCs / players", `${valueOrDash(tick.npcCount)} / ${valueOrDash(tick.playerCount)}`),
        metric("Scene / ground", `${valueOrDash(tick.sceneObjectsCount)} / ${valueOrDash(tick.groundItemsCount)}`),
        metric("Widgets", tick.widgetCount),
        metric("Frame state", frameState),
        metric("Capture source", tick.frameCaptureSource),
        metric("Capture status", tick.frameCaptureStatus),
        metric("Frame index", frameIndexState(tick)),
        metric("Frame lifecycle", frameLifecycle(tick)),
        metric("Frame index status", tick.frameIndexStatus),
        metric("Write delay", formatMs(tick.frameWriteDelayMs)),
        metric("Total latency", formatMs(tick.frameTotalLatencyMs)),
        metric("Capture latency", formatMs(tick.frameCaptureLatencyMs)),
        metric("Queue latency", formatMs(tick.frameQueueLatencyMs)),
        metric("Capture errors", tick.captureErrorCount)
      ].join("");
    }

    function renderEvents() {
      const filter = el.eventFilter.value.trim().toLowerCase();
      const events = state.currentEvents.filter((event) => {
        if (!filter) {
          return true;
        }

        return String(event.eventType || "").toLowerCase().includes(filter)
          || String(event.category || "").toLowerCase().includes(filter);
      });

      if (!events.length) {
        el.eventsList.innerHTML = `<div class="event-row">No nearby events.</div>`;
        return;
      }

      el.eventsList.innerHTML = events.map((event) => `
        <div class="event-row">
          <div><span class="event-type">${valueOrDash(event.eventType)}</span> ${valueOrDash(event.summary)}</div>
          <div class="event-meta">tick ${valueOrDash(event.tickId)} - ${valueOrDash(event.category)} - ${valueOrDash(event.timestampUtc)}</div>
        </div>
      `).join("");
    }

    function setFrame(tick) {
      el.frameImage.style.display = "none";
      el.missingFrame.style.display = "block";
      el.frameImage.removeAttribute("src");
      el.frameImage.classList.toggle("actual-width", state.frameFit === "actual");

      if (!tick || !tick.frameExists) {
        return;
      }

      el.frameImage.onload = () => {
        el.missingFrame.style.display = "none";
        el.frameImage.style.display = "block";
      };
      el.frameImage.onerror = () => {
        el.frameImage.style.display = "none";
        el.missingFrame.style.display = "block";
      };
      el.frameImage.src = `/api/frame/${encodeURIComponent(tick.tickId)}?v=${Date.now()}`;
    }

    async function selectIndex(index) {
      if (!state.ticks.length) {
        return;
      }

      state.currentIndex = Math.max(0, Math.min(index, state.ticks.length - 1));
      const tick = state.ticks[state.currentIndex];
      el.timeline.value = String(state.currentIndex);
      el.jumpTick.value = tick.tickId ?? "";
      el.tickLabel.textContent = `Tick ${valueOrDash(tick.tickId)}`;
      el.timestampLabel.textContent = valueOrDash(tick.timestampUtc);
      el.statusText.textContent = `${state.currentIndex + 1} of ${state.ticks.length}`;
      renderSummary(tick);
      setFrame(tick);
      setWarning("");

      try {
        const [rawTick, eventPayload] = await Promise.all([
          fetchJson(`/api/tick/${encodeURIComponent(tick.tickId)}`),
          fetchJson(`/api/events?tick=${encodeURIComponent(tick.tickId)}&window=5`)
        ]);
        state.rawTick = rawTick;
        state.currentEvents = eventPayload.events || [];
        el.rawJson.textContent = JSON.stringify(rawTick, null, 2);
        renderEvents();
      } catch (error) {
        setWarning(error.message);
      }
    }

    function selectByTickId(tickId) {
      const requested = String(tickId);
      const index = state.ticks.findIndex((tick) => String(tick.tickId) === requested);

      if (index >= 0) {
        selectIndex(index);
      } else {
        setWarning(`Tick not found: ${requested}`);
      }
    }

    function stopPlayback() {
      if (state.playTimer) {
        clearInterval(state.playTimer);
        state.playTimer = null;
      }

      el.playPause.textContent = "Play";
    }

    function startPlayback() {
      stopPlayback();
      el.playPause.textContent = "Pause";
      state.playTimer = setInterval(() => {
        if (state.currentIndex >= state.ticks.length - 1) {
          stopPlayback();
          return;
        }

        selectIndex(state.currentIndex + 1);
      }, Number(el.speed.value));
    }

    function toggleFrameFit() {
      state.frameFit = state.frameFit === "contain" ? "actual" : "contain";
      el.fitToggle.textContent = state.frameFit === "contain" ? "Fit: contain" : "Fit: actual width";
      el.frameImage.classList.toggle("actual-width", state.frameFit === "actual");
    }

    function isTextInput(target) {
      return target instanceof HTMLInputElement
        || target instanceof HTMLTextAreaElement
        || target instanceof HTMLSelectElement;
    }

    async function init() {
      try {
        const [
          session,
          ticks,
          analysisSummary,
          analysisTimeline,
          analysisCombat,
          analysisInventory,
          analysisUi
        ] = await Promise.all([
          fetchJson("/api/session"),
          fetchJson("/api/ticks"),
          fetchJson("/api/analysis/summary"),
          fetchJson("/api/analysis/timeline"),
          fetchJson("/api/analysis/combat"),
          fetchJson("/api/analysis/inventory"),
          fetchJson("/api/analysis/ui")
        ]);
        state.session = session;
        state.ticks = ticks;
        state.analysisSummary = analysisSummary;
        state.analysisTimeline = analysisTimeline;
        state.analysisCombat = analysisCombat;
        state.analysisInventory = analysisInventory;
        state.analysisUi = analysisUi;
        el.sessionMeta.textContent = `${session.layout || "session"} - ${session.tickCount || 0} ticks - ${session.sessionPath || ""}`;
        el.timeline.max = String(Math.max(0, ticks.length - 1));
        applyAnalysisFilterControls();
        renderAnalysis();

        if (!ticks.length) {
          setWarning("No ticks found in this session.");
          return;
        }

        await selectIndex(0);
      } catch (error) {
        setWarning(error.message);
        el.sessionMeta.textContent = "Unable to load session";
      }
    }

    function handleAnalysisJump(event) {
      const target = event.target.closest("[data-tick-id]");

      if (!target) {
        return;
      }

      selectByTickId(target.dataset.tickId);
    }

    el.prevTick.addEventListener("click", () => selectIndex(state.currentIndex - 1));
    el.nextTick.addEventListener("click", () => selectIndex(state.currentIndex + 1));
    el.fitToggle.addEventListener("click", toggleFrameFit);
    el.timeline.addEventListener("input", () => selectIndex(Number(el.timeline.value)));
    el.jumpButton.addEventListener("click", () => selectByTickId(el.jumpTick.value));
    el.jumpTick.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        selectByTickId(el.jumpTick.value);
      }
    });
    el.playPause.addEventListener("click", () => {
      if (state.playTimer) {
        stopPlayback();
      } else {
        startPlayback();
      }
    });
    el.speed.addEventListener("change", () => {
      if (state.playTimer) {
        startPlayback();
      }
    });
    el.analysisTimeline.addEventListener("click", handleAnalysisJump);
    el.analysisCombat.addEventListener("click", handleAnalysisJump);
    el.analysisInventory.addEventListener("click", handleAnalysisJump);
    el.analysisUi.addEventListener("click", handleAnalysisJump);
    el.analysisEventSearch.addEventListener("input", updateAnalysisFilters);
    el.analysisOnlyEvents.addEventListener("change", updateAnalysisFilters);
    el.analysisOnlyIssues.addEventListener("change", updateAnalysisFilters);

    for (const input of el.analysisCategoryInputs) {
      input.addEventListener("change", updateAnalysisFilters);
    }

    el.eventFilter.addEventListener("input", renderEvents);
    document.addEventListener("keydown", (event) => {
      if (isTextInput(event.target)) {
        return;
      }

      if (event.key === "ArrowRight") {
        event.preventDefault();
        selectIndex(state.currentIndex + 1);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        selectIndex(state.currentIndex - 1);
      } else if (event.key === " ") {
        event.preventDefault();

        if (state.playTimer) {
          stopPlayback();
        } else {
          startPlayback();
        }
      }
    });

    init();
  </script>
</body>
</html>"""
    return body.encode("utf-8")


class ReplayHandler(BaseHTTPRequestHandler):
    replay = None

    def log_message(self, format, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))

    def send_json(self, payload, status=HTTPStatus.OK):
        data = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, data: bytes):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_missing(self, message: str, status=HTTPStatus.NOT_FOUND):
        self.send_json({"error": message}, status)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/":
            self.send_html(html_page())
            return

        if path == "/api/session":
            self.handle_session()
            return

        if path == "/api/ticks":
            self.send_json(self.replay["tickSummaries"])
            return

        if path.startswith("/api/tick/"):
            self.handle_tick(path.removeprefix("/api/tick/"))
            return

        if path == "/api/events":
            self.handle_events(parse_qs(parsed.query))
            return

        if path.startswith("/api/frame/"):
            self.handle_frame(path.removeprefix("/api/frame/"))
            return

        if path == "/api/dictionaries":
            self.send_json(self.replay["dictionaries"])
            return

        if path.startswith("/api/analysis/"):
            self.handle_analysis(path.removeprefix("/api/analysis/"))
            return

        self.send_missing("Not found")

    def handle_session(self):
        session_path = self.replay["sessionPath"]
        payload = {
            "sessionPath": str(session_path),
            "manifest": self.replay["manifest"],
            "layout": self.replay["layout"],
            "loadedAtUtc": self.replay["loadedAtUtc"],
            "sessionSizeMb": round(session_size_mb(session_path), 3),
            "tickCount": len(self.replay["tickSummaries"]),
            "eventCount": len(self.replay["events"]),
            "tickSegmentCount": len(self.replay["tickFiles"]),
            "eventSegmentCount": len(self.replay["eventFiles"]),
            "frameIndexAvailable": self.replay["frameIndexSummary"].get("totalRecords", 0) > 0,
            "frameIndexSummary": self.replay["frameIndexSummary"],
            "firstTickId": self.replay["firstTickId"],
            "lastTickId": self.replay["lastTickId"],
            "topEventTypeCounts": self.replay["eventTypeCounts"],
            "dictionarySummaries": {
                key: {
                    item_key: item_value
                    for item_key, item_value in value.items()
                    if item_key != "inline"
                }
                for key, value in self.replay["dictionaries"].items()
            },
        }
        self.send_json(payload)

    def handle_tick(self, tick_id: str):
        tick = self.replay["rawTickById"].get(tick_id)

        if tick is None:
            self.send_missing(f"Tick not found: {tick_id}")
            return

        self.send_json(tick)

    def handle_events(self, query: dict):
        tick_values = query.get("tick") or []

        if not tick_values:
            self.send_json(self.replay["events"])
            return

        try:
            selected_tick = int(tick_values[0])
            window = int((query.get("window") or ["5"])[0])
        except ValueError:
            self.send_missing("tick and window must be integers", HTTPStatus.BAD_REQUEST)
            return

        window = max(0, min(window, 1000))
        start = selected_tick - window
        end = selected_tick + window
        events = []

        for tick_id in range(start, end + 1):
            events.extend(self.replay["eventsByTick"].get(tick_id, []))

        self.send_json(
            {
                "tick": selected_tick,
                "window": window,
                "startTick": start,
                "endTick": end,
                "events": events,
            }
        )

    def handle_analysis(self, name: str):
        analysis = self.replay.get("analysis") or {}

        if name == "summary":
            self.send_json(analysis.get("summary", {}))
            return

        if name == "timeline":
            self.send_json(analysis.get("timeline", []))
            return

        if name == "events":
            self.send_json(analysis.get("events", {}))
            return

        if name == "combat":
            self.send_json(analysis.get("combat", {}))
            return

        if name == "inventory":
            self.send_json(analysis.get("inventory", {}))
            return

        if name == "ui":
            self.send_json(analysis.get("ui", {}))
            return

        self.send_missing(f"Analysis endpoint not found: {name}")

    def handle_frame(self, tick_id: str):
        tick = self.replay["rawTickById"].get(tick_id)

        if tick is None:
            self.send_missing(f"Tick not found: {tick_id}")
            return

        frame_path_value = tick.get("framePath")
        frame_path = resolve_frame_path(self.replay["sessionPath"], frame_path_value)

        if frame_path is None:
            self.send_missing(f"No frame associated with tick: {tick_id}")
            return

        if not is_under_directory(frame_path, self.replay["sessionPath"]):
            self.send_missing("Frame path escapes the session directory", HTTPStatus.FORBIDDEN)
            return

        if not frame_path.exists() or not frame_path.is_file():
            self.send_missing(f"Frame missing for tick: {tick_id}")
            return

        content_type = mimetypes.guess_type(frame_path.name)[0] or "application/octet-stream"

        try:
            data = frame_path.read_bytes()
        except OSError:
            self.send_missing(f"Unable to read frame for tick: {tick_id}")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def parse_args():
    parser = argparse.ArgumentParser(description="Serve a local browser API for OSRS telemetry replay.")
    parser.add_argument("--session", help="Path to a specific telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override the telemetry sessions directory.")
    parser.add_argument("--port", type=int, default=8765, help="Local server port. Default: 8765.")
    return parser.parse_args()


def main():
    args = parse_args()
    session_path = session_from_arg(args.session, args.sessions_dir)

    if session_path is None:
        sessions_dir = get_sessions_dir(args.sessions_dir)
        print(f"No telemetry sessions found in: {sessions_dir}", file=sys.stderr)
        return 1

    if not session_path.exists() or not session_path.is_dir():
        print(f"Session directory does not exist: {session_path}", file=sys.stderr)
        return 1

    replay = load_replay(session_path)
    ReplayHandler.replay = replay
    server = ThreadingHTTPServer(("127.0.0.1", args.port), ReplayHandler)
    url = f"http://127.0.0.1:{args.port}/"

    print(f"Serving telemetry replay: {session_path}")
    print(f"Open: {url}")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping replay viewer.")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

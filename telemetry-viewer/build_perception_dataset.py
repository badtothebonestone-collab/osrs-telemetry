import argparse
import json
import os
import shutil
import struct
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from telemetry_paths import (
    classify_frame_state,
    find_newest_session,
    frame_index_by_tick,
    frame_index_stats,
    frame_timing_fields,
    get_sessions_dir,
    iter_jsonl,
    list_event_files,
    list_frame_index_files,
    list_tick_files,
    load_frame_index_summaries,
    safe_read_json,
)


SCHEMA_VERSION_INDEX = "perception.index.v1"
SCHEMA_VERSION_TICK_BUNDLE = "perception.tick_bundle.v1"
SCHEMA_VERSION_EVENT_WINDOW = "perception.event_window.v1"
SCHEMA_VERSION_SCREEN_REGIONS = "perception.screen_regions.v1"
DEFAULT_EVENT_WINDOW_TICKS = 2
HIGH_FRAME_LATENCY_MS = 1000
MANY_MISSING_FRAMES_MIN = 10
MANY_MISSING_FRAMES_RATIO = 0.25

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
COMBAT_CATEGORIES = {"combat"}
INVENTORY_CATEGORIES = {"inventory", "skills"}
UI_CATEGORIES = {"ui"}
VAR_CATEGORIES = {"var"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dump_compact(data) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def session_relative(session: Path, path: str | Path | None) -> str | None:
    if path is None:
        return None

    candidate = Path(path)

    try:
        resolved = candidate.resolve() if candidate.is_absolute() else (session / candidate).resolve()
        return str(resolved.relative_to(session.resolve()))
    except (OSError, ValueError):
        return str(path)


def is_under_directory(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except (OSError, ValueError):
        return False


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


def actor_summary(actor) -> str:
    if not isinstance(actor, dict):
        return ""

    actor_type = actor.get("actorType") or actor.get("type") or "UNKNOWN"
    name = actor.get("name") or actor.get("nameHash") or actor.get("id") or actor.get("index")
    animation = actor.get("animation")
    parts = [str(actor_type)]

    if name is not None:
        parts.append(str(name))

    if animation is not None:
        parts.append(f"anim={animation}")

    return " ".join(parts)


def event_summary_text(event_type: str | None, payload) -> str:
    event_type = event_type or ""

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
        return actor_summary(payload.get("actor") if "actor" in payload else payload)

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

    if event_type.startswith("Var"):
        return " ".join(
            f"{key}={payload.get(key)}"
            for key in ("index", "varbitId", "varpId", "value")
            if key in payload
        )

    return " ".join(f"{key}={value}" for key, value in list(payload.items())[:4])


def summarize_event(session: Path, source: Path, event: dict, relative_tick: int | None = None) -> dict:
    event_type = event.get("eventType")
    category = CATEGORY_BY_EVENT_TYPE.get(event_type, "unknown")
    summary = {
        "tickId": event.get("tickId"),
        "timestampUtc": event.get("timestampUtc"),
        "eventType": event_type,
        "category": category,
        "summary": event_summary_text(event_type, event.get("payload")),
        "source": session_relative(session, source),
    }

    if relative_tick is not None:
        summary["relativeTick"] = relative_tick

    return summary


def read_jsonl_objects(
    files: list[Path],
    label: str,
    *,
    fatal_errors: bool = False,
) -> tuple[list[tuple[Path, dict]], list[str], list[str]]:
    rows = []
    warnings = []
    errors = []

    for source, line_number, record, error in iter_jsonl(files, with_errors=True):
        if error is not None:
            message = f"{label} JSON error in {source.name}:{line_number}: {error}"
            (errors if fatal_errors else warnings).append(message)
            continue

        if not isinstance(record, dict):
            message = f"{label} record is not an object in {source.name}:{line_number}"
            (errors if fatal_errors else warnings).append(message)
            continue

        rows.append((source, record))

    return rows, warnings, errors


def read_dictionary_stats(session: Path) -> tuple[dict, list[str]]:
    dictionaries = session / "dictionaries"
    stats = {}
    warnings = []

    for name in ("items", "npcs", "objects"):
        path = dictionaries / f"{name}.json"

        if not path.exists():
            stats[name] = {"present": False, "count": 0}
            continue

        data = safe_read_json(path)

        if isinstance(data, dict):
            stats[name] = {"present": True, "count": len(data)}
        else:
            stats[name] = {"present": True, "count": 0}
            warnings.append(f"dictionary unreadable or not object: {session_relative(session, path)}")

    return stats, warnings


def png_size(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as file:
            header = file.read(24)
    except OSError:
        return None

    if len(header) >= 24 and header[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", header[16:24])
        return width, height

    return None


def jpeg_size(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as file:
            if file.read(2) != b"\xff\xd8":
                return None

            while True:
                byte = file.read(1)

                while byte == b"\xff":
                    marker = file.read(1)

                    if marker in (b"\xc0", b"\xc1", b"\xc2", b"\xc3", b"\xc5", b"\xc6", b"\xc7", b"\xc9", b"\xca", b"\xcb", b"\xcd", b"\xce", b"\xcf"):
                        length_bytes = file.read(2)

                        if len(length_bytes) != 2:
                            return None

                        file.read(1)
                        size_bytes = file.read(4)

                        if len(size_bytes) != 4:
                            return None

                        height, width = struct.unpack(">HH", size_bytes)
                        return width, height

                    if marker in (b"\xd8", b"\xd9"):
                        break

                    length_bytes = file.read(2)

                    if len(length_bytes) != 2:
                        return None

                    length = struct.unpack(">H", length_bytes)[0]

                    if length < 2:
                        return None

                    file.seek(length - 2, os.SEEK_CUR)

                if not byte:
                    return None
    except OSError:
        return None


def image_size(path: Path | None) -> tuple[int | None, int | None]:
    if path is None or not path.exists() or not path.is_file():
        return None, None

    size = png_size(path) or jpeg_size(path)

    if size is None:
        return None, None

    return size


def active_prayer_names(tick: dict) -> list[str]:
    return [
        prayer.get("name")
        for prayer in (tick.get("activePrayers") or [])
        if isinstance(prayer, dict) and prayer.get("active") and prayer.get("name")
    ]


def interacting_summary(status: dict) -> str | None:
    interacting_type = status.get("interactingType")
    interacting_name = status.get("interactingName") or status.get("interactingId")

    if interacting_type and interacting_type != "UNKNOWN":
        return f"{interacting_type}:{interacting_name}"

    return None


def events_for_range(events_by_tick: dict[int, list[dict]], tick_id: int, start_offset: int, end_offset: int) -> list[dict]:
    rows = []

    for candidate_tick in range(tick_id + start_offset, tick_id + end_offset + 1):
        rows.extend(events_by_tick.get(candidate_tick, []))

    return rows


def event_category_counts(events: list[dict]) -> dict:
    return dict(Counter(event.get("category", "unknown") for event in events).most_common())


def event_type_list(events: list[dict]) -> list[str]:
    return sorted({event.get("eventType") for event in events if event.get("eventType")})


def source_files_for_events(events: list[dict]) -> list[str]:
    return sorted({event.get("source") for event in events if event.get("source")})


def tick_sequence_warnings(ticks: list[tuple[Path, dict]]) -> list[str]:
    warnings = []
    tick_ids = [tick.get("tickId") for _, tick in ticks if isinstance(tick.get("tickId"), int)]

    if len(tick_ids) != len(ticks):
        warnings.append(f"ticks with missing/non-integer tickId: {len(ticks) - len(tick_ids)}")

    if not tick_ids:
        return warnings

    duplicate_count = len(tick_ids) - len(set(tick_ids))

    if duplicate_count:
        warnings.append(f"duplicate tickIds detected: {duplicate_count}")

    sorted_ids = sorted(set(tick_ids))
    gaps = []

    for previous, current in zip(sorted_ids, sorted_ids[1:]):
        if current != previous + 1:
            gaps.append(f"{previous}->{current}")

    if gaps:
        warnings.append(f"tick IDs are non-contiguous: {len(gaps)} gap(s), examples: {', '.join(gaps[:5])}")

    return warnings


def build_frame_payload(
    session: Path,
    tick: dict,
    frame_index: dict | None,
    frame_index_available: bool,
    is_latest: bool,
    active_session: bool,
) -> tuple[dict, list[str]]:
    warnings = []
    frame_state = classify_frame_state(session, tick, is_latest=is_latest, active_session=active_session)
    timing = frame_timing_fields(frame_index)
    frame_path = frame_state.get("framePath")
    absolute_path_value = frame_state.get("absoluteFramePath")
    absolute_path = Path(absolute_path_value) if absolute_path_value else None
    escaped = bool(absolute_path and not is_under_directory(absolute_path, session))

    if escaped:
        warnings.append("frame path escapes session directory")

    width = frame_index.get("width") if frame_index else None
    height = frame_index.get("height") if frame_index else None

    if (width is None or height is None) and not escaped:
        detected_width, detected_height = image_size(absolute_path)
        width = width if width is not None else detected_width
        height = height if height is not None else detected_height

    payload = {
        "path": frame_path,
        "absolutePath": str(absolute_path) if absolute_path else None,
        "exists": frame_state.get("frameExists"),
        "pending": frame_state.get("framePending"),
        "expiredOrMissing": frame_state.get("frameExpiredOrMissing"),
        "width": width,
        "height": height,
        "captureSource": frame_state.get("frameCaptureSource") or (frame_index.get("captureSource") if frame_index else None),
        "captureStatus": frame_state.get("frameCaptureStatus"),
        "frameIndexAvailable": frame_index_available,
        "frameIndexStatus": timing["frameIndexStatus"],
        "frameIndexEventType": frame_index.get("eventType") if frame_index else None,
        "writeDelayMs": timing["frameWriteDelayMs"],
        "totalLatencyMs": timing["frameTotalLatencyMs"],
        "captureLatencyMs": timing["frameCaptureLatencyMs"],
        "queueLatencyMs": timing["frameQueueLatencyMs"],
    }

    if escaped:
        payload["pathEscapesSession"] = True

    return payload, warnings


def derived_flags(tick: dict, frame: dict, nearby_events: list[dict]) -> dict:
    categories = {event.get("category", "unknown") for event in nearby_events}
    capture_error_count = len(tick.get("captureErrors") or [])
    frame_event_type = frame.get("frameIndexEventType")
    high_latency = bool(
        isinstance(frame.get("totalLatencyMs"), (int, float))
        and frame["totalLatencyMs"] >= HIGH_FRAME_LATENCY_MS
    )
    has_frame_issue = bool(
        frame.get("expiredOrMissing")
        or frame.get("pathEscapesSession")
        or frame_event_type in {"FrameDropped", "FrameFailed"}
        or high_latency
    )
    issue_reasons = []
    warning_reasons = []

    if tick.get("tickId") is None:
        issue_reasons.append("missing tickId")

    if not tick.get("timestampUtc"):
        issue_reasons.append("missing timestampUtc")

    if frame.get("pathEscapesSession"):
        issue_reasons.append("frame path escapes session")

    if frame_event_type == "FrameDropped":
        issue_reasons.append("frame dropped")

    if frame_event_type == "FrameFailed":
        issue_reasons.append("frame failed")

    if capture_error_count:
        warning_reasons.append(f"capture errors:{capture_error_count}")

    if frame.get("expiredOrMissing"):
        warning_reasons.append("frame missing or expired")

    if frame.get("pending"):
        warning_reasons.append("frame pending")

    if frame.get("path") and not frame.get("frameIndexAvailable"):
        warning_reasons.append("frame index unavailable")

    if high_latency:
        warning_reasons.append("high frame latency")

    if issue_reasons:
        health_state = "ISSUE"
    elif warning_reasons:
        health_state = "WARNING"
    else:
        health_state = "OK"

    return {
        "hasCombatSignal": bool(categories & COMBAT_CATEGORIES),
        "hasInventorySignal": bool(categories & INVENTORY_CATEGORIES),
        "hasUiSignal": bool(categories & UI_CATEGORIES),
        "hasVarSignal": bool(categories & VAR_CATEGORIES),
        "hasFrameIssue": has_frame_issue,
        "hasCaptureError": bool(capture_error_count),
        "healthState": health_state,
        "warnings": warning_reasons,
        "issues": issue_reasons,
    }


def derived_summary(tick: dict, frame: dict, events: list[dict], derived: dict) -> str:
    tick_id = tick.get("tickId")
    event_types = event_type_list(events)
    frame_label = "frame"

    if frame.get("exists") is True:
        frame_label = "frame exists"
    elif frame.get("pending"):
        frame_label = "frame pending"
    elif frame.get("expiredOrMissing"):
        frame_label = "frame missing/expired"
    elif not frame.get("path"):
        frame_label = "no frame path"

    event_label = ", ".join(event_types[:3]) if event_types else "no nearby events"
    return f"tick {tick_id}: {derived['healthState']} - {frame_label}; {event_label}"


def build_tick_bundle(
    session: Path,
    session_id: str | None,
    tick_source: Path,
    tick: dict,
    latest_tick: dict | None,
    active_session: bool,
    frame_index: dict | None,
    frame_index_available: bool,
    current_events: list[dict],
    nearby_events: list[dict],
) -> dict:
    local_player = tick.get("localPlayer") or {}
    status = tick.get("status") or {}
    frame, frame_warnings = build_frame_payload(
        session,
        tick,
        frame_index,
        frame_index_available,
        tick is latest_tick,
        active_session,
    )
    derived = derived_flags(tick, frame, nearby_events)
    derived["warnings"].extend(frame_warnings)
    source_files = {
        "tick": session_relative(session, tick_source),
        "events": source_files_for_events(nearby_events),
        "frameIndex": session_relative(session, frame_index.get("source")) if frame_index else None,
    }
    state = {
        "gameState": tick.get("gameState"),
        "position": {
            "worldX": local_player.get("worldX"),
            "worldY": local_player.get("worldY"),
            "plane": local_player.get("plane"),
        },
        "hp": {
            "boosted": status.get("hitpointsBoosted"),
            "real": status.get("hitpointsReal"),
        },
        "prayer": {
            "boosted": status.get("prayerBoosted"),
            "real": status.get("prayerReal"),
        },
        "runEnergyPercent": status.get("runEnergyPercent"),
        "activePrayerNames": active_prayer_names(tick),
        "interacting": interacting_summary(status),
        "inventoryCount": count_items(tick.get("inventory")),
        "equipmentCount": count_items(tick.get("equipment")),
        "npcCount": len(tick.get("npcs") or []),
        "playerCount": len(tick.get("players") or []),
        "widgetCount": len(tick.get("widgets") or []),
        "sceneObjectsCount": len(tick.get("sceneObjects") or []),
        "groundItemsCount": len(tick.get("groundItems") or []),
        "captureErrorCount": len(tick.get("captureErrors") or []),
    }
    events = {
        "onTickEventTypes": event_type_list(current_events),
        "nearbyEventSummaries": nearby_events,
        "countsByCategory": event_category_counts(nearby_events),
    }
    derived["summary"] = derived_summary(tick, frame, nearby_events, derived)

    return {
        "schemaVersion": SCHEMA_VERSION_TICK_BUNDLE,
        "sessionId": session_id,
        "tickId": tick.get("tickId"),
        "timestampUtc": tick.get("timestampUtc"),
        "sourceFiles": source_files,
        "frame": frame,
        "state": state,
        "events": events,
        "derived": derived,
    }


def build_event_window(
    tick_id: int,
    events_by_tick: dict[int, list[dict]],
    window_ticks: int,
) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION_EVENT_WINDOW,
        "tickId": tick_id,
        "windowTicks": window_ticks,
        "previousEvents": events_for_range(events_by_tick, tick_id, -window_ticks, -1),
        "currentEvents": events_by_tick.get(tick_id, []),
        "nextEvents": events_for_range(events_by_tick, tick_id, 1, window_ticks),
    }


def screen_regions() -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION_SCREEN_REGIONS,
        "coordinateSpace": "normalized",
        "note": "Approximate review regions based on the current captured frame layout. Images are not cropped or modified.",
        "regions": {
            "fullFrame": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
            "gameViewport": {"x": 0.0, "y": 0.0, "w": 0.735, "h": 0.74},
            "minimap": {"x": 0.735, "y": 0.0, "w": 0.265, "h": 0.25},
            "inventory": {"x": 0.735, "y": 0.34, "w": 0.265, "h": 0.42},
            "chatbox": {"x": 0.0, "y": 0.74, "w": 0.735, "h": 0.26},
            "sidePanel": {"x": 0.735, "y": 0.25, "w": 0.265, "h": 0.75},
            "tabs": {"x": 0.735, "y": 0.25, "w": 0.265, "h": 0.09},
            "compassOrbArea": {"x": 0.735, "y": 0.0, "w": 0.265, "h": 0.18},
        },
    }


def write_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
        file.write("\n")


def write_jsonl(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json_dump_compact(row))
            file.write("\n")


def atomic_publish(perception_dir: Path, temp_dir: Path, filenames: tuple[str, ...]) -> None:
    perception_dir.mkdir(parents=True, exist_ok=True)

    for filename in filenames:
        os.replace(temp_dir / filename, perception_dir / filename)


def build_perception_dataset(session: Path, *, window_ticks: int = DEFAULT_EVENT_WINDOW_TICKS) -> dict:
    session = session.expanduser().resolve()
    perception_dir = session / "perception"
    temp_dir = perception_dir / f".tmp-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}-{os.getpid()}"
    tick_files = list_tick_files(session)
    event_files = list_event_files(session)
    manifest = safe_read_json(session / "manifest.json")
    manifest = manifest if isinstance(manifest, dict) else {}
    session_id = manifest.get("sessionId") or session.name
    active_session = bool(manifest.get("active"))
    ticks, tick_warnings, tick_errors = read_jsonl_objects(tick_files, "tick", fatal_errors=True)
    events, event_warnings, event_errors = read_jsonl_objects(event_files, "event")
    dictionary_stats, dictionary_warnings = read_dictionary_stats(session)
    warnings = tick_warnings + event_warnings + event_errors + dictionary_warnings

    if tick_errors:
        preview = "; ".join(tick_errors[:5])
        raise ValueError(f"Unreadable tick JSON: {preview}")

    if not ticks:
        raise ValueError(f"No ticks found in session: {session}")

    ticks_without_frame_path = sum(1 for _, tick in ticks if not tick.get("framePath"))

    if ticks_without_frame_path:
        warnings.append(f"ticks without framePath: {ticks_without_frame_path}/{len(ticks)}")

    warnings.extend(tick_sequence_warnings(ticks))
    frame_index_files = list_frame_index_files(session)
    frame_index_summaries = load_frame_index_summaries(session)
    frame_index_lookup = frame_index_by_tick(frame_index_summaries)
    frame_index_summary = frame_index_stats(frame_index_summaries)
    frame_index_available = bool(frame_index_summaries)

    if not frame_index_files:
        warnings.append("frame_index.jsonl missing; frame timing fields may be unavailable")
    elif not frame_index_available:
        warnings.append("frame_index.jsonl present but no frame-index records were parsed")

    latest_tick = ticks[-1][1] if ticks else None
    events_by_tick = defaultdict(list)
    event_type_counts = Counter()
    event_category_counts = Counter()

    for source, event in events:
        summary = summarize_event(session, source, event)
        event_type = summary.get("eventType") or "UNKNOWN"
        category = summary.get("category") or "unknown"
        event_type_counts[event_type] += 1
        event_category_counts[category] += 1

        tick_id = summary.get("tickId")

        if isinstance(tick_id, int):
            events_by_tick[tick_id].append(summary)

    tick_bundles = []
    event_windows = []
    health_state_counts = Counter()

    for tick_source, tick in ticks:
        tick_id = tick.get("tickId")

        if isinstance(tick_id, int):
            current_events = events_by_tick.get(tick_id, [])
            nearby_events = [
                {
                    **event,
                    "relativeTick": int(event["tickId"]) - tick_id,
                }
                for event in events_for_range(events_by_tick, tick_id, -window_ticks, window_ticks)
                if isinstance(event.get("tickId"), int)
            ]
            event_windows.append(build_event_window(tick_id, events_by_tick, window_ticks))
        else:
            current_events = []
            nearby_events = []
            warnings.append(f"tick without integer tickId in {session_relative(session, tick_source)}")

        bundle = build_tick_bundle(
            session,
            session_id,
            tick_source,
            tick,
            latest_tick,
            active_session,
            frame_index_lookup.get(tick_id),
            frame_index_available,
            current_events,
            nearby_events,
        )
        tick_bundles.append(bundle)
        health_state_counts[bundle["derived"]["healthState"]] += 1

    frame_exists_count = sum(1 for bundle in tick_bundles if bundle["frame"]["exists"] is True)
    frame_missing_count = sum(
        1
        for bundle in tick_bundles
        if bundle["frame"]["path"] and bundle["frame"]["exists"] is False
    )
    frame_issue_count = sum(1 for bundle in tick_bundles if bundle["derived"]["hasFrameIssue"])
    combat_signal_count = sum(1 for bundle in tick_bundles if bundle["derived"]["hasCombatSignal"])
    inventory_signal_count = sum(1 for bundle in tick_bundles if bundle["derived"]["hasInventorySignal"])
    ui_signal_count = sum(1 for bundle in tick_bundles if bundle["derived"]["hasUiSignal"])
    capture_error_count = sum(bundle["state"]["captureErrorCount"] for bundle in tick_bundles)

    if frame_missing_count:
        warnings.append(f"frame files missing or expired: {frame_missing_count}/{len(tick_bundles)}")

        if (
            frame_missing_count >= MANY_MISSING_FRAMES_MIN
            and frame_missing_count / max(1, len(tick_bundles)) >= MANY_MISSING_FRAMES_RATIO
        ):
            warnings.append(
                "many frame files are missing; this is usually retention side data, not corrupt tick telemetry"
            )

    generated_files = {
        "perceptionIndex": "perception/perception_index.json",
        "tickBundles": "perception/tick_bundles.jsonl",
        "eventWindows": "perception/event_windows.jsonl",
        "screenRegions": "perception/screen_regions.json",
    }
    index = {
        "schemaVersion": SCHEMA_VERSION_INDEX,
        "sessionPath": str(session),
        "sessionId": session_id,
        "generatedAtUtc": utc_now(),
        "tickBundleCount": len(tick_bundles),
        "firstTickId": tick_bundles[0]["tickId"] if tick_bundles else None,
        "lastTickId": tick_bundles[-1]["tickId"] if tick_bundles else None,
        "frameExistsCount": frame_exists_count,
        "frameMissingCount": frame_missing_count,
        "frameIssueCount": frame_issue_count,
        "combatSignalCount": combat_signal_count,
        "inventorySignalCount": inventory_signal_count,
        "uiSignalCount": ui_signal_count,
        "captureErrorCount": capture_error_count,
        "topEventTypes": dict(event_type_counts.most_common(20)),
        "topEventCategories": dict(event_category_counts.most_common()),
        "healthStateCounts": dict(health_state_counts.most_common()),
        "frameIndexSummary": frame_index_summary,
        "dictionaryStats": dictionary_stats,
        "paths": generated_files,
        "warnings": warnings[:100],
        "warningCount": len(warnings),
    }

    filenames = (
        "perception_index.json",
        "tick_bundles.jsonl",
        "event_windows.jsonl",
        "screen_regions.json",
    )

    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    temp_dir.mkdir(parents=True)

    try:
        write_json(temp_dir / "perception_index.json", index)
        write_jsonl(temp_dir / "tick_bundles.jsonl", tick_bundles)
        write_jsonl(temp_dir / "event_windows.jsonl", event_windows)
        write_json(temp_dir / "screen_regions.json", screen_regions())
        atomic_publish(perception_dir, temp_dir, filenames)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

    return index


def parse_args():
    parser = argparse.ArgumentParser(description="Build a derived perception dataset for an OSRS telemetry session.")
    parser.add_argument("--session", help="Telemetry session directory to process.")
    parser.add_argument("--sessions-dir", help="Override the telemetry sessions directory when --session is omitted.")
    parser.add_argument("--window", type=int, default=DEFAULT_EVENT_WINDOW_TICKS, help="Nearby event window in ticks. Default: 2.")
    return parser.parse_args()


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def main() -> int:
    args = parse_args()
    session = resolve_session(args)

    if session is None:
        sessions_dir = get_sessions_dir(args.sessions_dir)
        print(f"No sessions found in: {sessions_dir}")
        return 1

    try:
        index = build_perception_dataset(session, window_ticks=max(0, args.window))
    except (OSError, ValueError) as error:
        print(f"Unable to build perception dataset: {error}")
        return 1

    print(f"Built perception dataset: {index['sessionPath']}")
    print(f"  perception/perception_index.json")
    print(f"  perception/tick_bundles.jsonl ({index['tickBundleCount']} rows)")
    print(f"  perception/event_windows.jsonl ({index['tickBundleCount']} rows)")
    print(f"  perception/screen_regions.json")
    print(f"  healthStateCounts: {index['healthStateCounts']}")
    print(f"  warningCount: {index['warningCount']}")

    if index["warnings"]:
        print("  warnings:")

        for warning in index["warnings"][:10]:
            print(f"    - {warning}")

        if index["warningCount"] > 10:
            print(f"    - ... {index['warningCount'] - 10} more warning(s)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


TELEMETRY_ROOT = Path(r"C:\Users\stone\.osrs-telemetry\sessions")
EXPORT_DIR = Path("exports")


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


def tick_files(session: Path) -> list[Path]:
    segmented = sorted((session / "ticks").glob("ticks-*.jsonl"))

    if segmented:
        return segmented

    legacy = session / "ticks.jsonl"
    return [legacy] if legacy.exists() else []


def event_files(session: Path) -> list[Path]:
    segmented = sorted((session / "events").glob("events-*.jsonl"))

    if segmented:
        return segmented

    legacy = session / "events.jsonl"
    return [legacy] if legacy.exists() else []


def file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def session_mtime(session: Path) -> float:
    manifest = session / "manifest.json"
    files = tick_files(session) + event_files(session)
    mtimes = [file_mtime(manifest)] if manifest.exists() else []
    mtimes.extend(file_mtime(path) for path in files)
    return max(mtimes) if mtimes else file_mtime(session)


def find_newest_session() -> Path | None:
    if not TELEMETRY_ROOT.exists():
        return None

    sessions = [
        path for path in TELEMETRY_ROOT.iterdir()
        if path.is_dir() and (tick_files(path) or event_files(path) or (path / "manifest.json").exists())
    ]

    if not sessions:
        return None

    return max(sessions, key=session_mtime)


def iter_jsonl(files: list[Path]):
    for file_path in files:
        with file_path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()

                if not line:
                    continue

                try:
                    yield file_path, json.loads(line)
                except json.JSONDecodeError:
                    continue


def read_manifest(session: Path) -> dict | None:
    path = session / "manifest.json"

    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def directory_size(path: Path) -> int:
    total = 0

    if not path.exists():
        return total

    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                pass

    return total


def dictionary_counts(session: Path) -> dict[str, int]:
    dictionaries = session / "dictionaries"
    counts = {}

    for key, filename in (("items", "items.json"), ("npcs", "npcs.json"), ("objects", "objects.json")):
        path = dictionaries / filename

        if not path.exists():
            counts[key] = 0
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            counts[key] = len(data) if isinstance(data, dict) else 0
        except json.JSONDecodeError:
            counts[key] = 0

    return counts


def frame_exists(session: Path, frame_path: str | None) -> bool | None:
    if not frame_path:
        return None

    return (session / frame_path).exists()


def count_items(items: list[dict]) -> int:
    return sum(1 for item in items if item.get("itemId", -1) > 0 and item.get("quantity", 0) > 0)


def tick_summary(session: Path, source: Path, tick: dict) -> dict:
    local_player = tick.get("localPlayer") or {}
    status = tick.get("status") or {}
    active_prayers = [
        prayer.get("name")
        for prayer in (tick.get("activePrayers") or [])
        if prayer.get("active") and prayer.get("name")
    ]
    interacting_type = status.get("interactingType")
    interacting_name = status.get("interactingName") or status.get("interactingId")

    if interacting_type and interacting_type != "UNKNOWN":
        interacting = f"{interacting_type}:{interacting_name}"
    else:
        interacting = None

    return {
        "tickId": tick.get("tickId"),
        "timestampUtc": tick.get("timestampUtc"),
        "gameState": tick.get("gameState"),
        "worldX": local_player.get("worldX"),
        "worldY": local_player.get("worldY"),
        "plane": local_player.get("plane"),
        "runEnergyPercent": status.get("runEnergyPercent"),
        "hpBoosted": status.get("hitpointsBoosted"),
        "hpReal": status.get("hitpointsReal"),
        "prayerBoosted": status.get("prayerBoosted"),
        "prayerReal": status.get("prayerReal"),
        "inventoryCount": count_items(tick.get("inventory") or []),
        "equipmentCount": count_items(tick.get("equipment") or []),
        "npcCount": len(tick.get("npcs") or []),
        "playerCount": len(tick.get("players") or []),
        "widgetCount": len(tick.get("widgets") or []),
        "sceneObjectsCount": len(tick.get("sceneObjects") or []),
        "groundItemsCount": len(tick.get("groundItems") or []),
        "activePrayerNames": active_prayers,
        "interactingTarget": interacting,
        "framePath": tick.get("framePath"),
        "frameExists": frame_exists(session, tick.get("framePath")),
        "frameCaptureStatus": tick.get("frameCaptureStatus"),
        "frameCaptureSource": tick.get("frameCaptureSource"),
        "frameCaptureWarning": tick.get("frameCaptureWarning"),
        "captureErrorCount": len(tick.get("captureErrors") or []),
        "source": str(source),
    }


def actor_summary(actor: dict) -> str:
    if not isinstance(actor, dict):
        return ""

    actor_type = actor.get("actorType", "UNKNOWN")
    name = actor.get("name") or actor.get("nameHash") or actor.get("id") or actor.get("index")
    animation = actor.get("animation")
    parts = [str(actor_type)]

    if name is not None:
        parts.append(str(name))

    if animation is not None:
        parts.append(f"anim={animation}")

    return " ".join(parts)


def event_summary_text(event_type: str, payload) -> str:
    if not isinstance(payload, dict):
        return ""

    if event_type == "StatChanged":
        return f"{payload.get('skill')} level={payload.get('level')} boosted={payload.get('boostedLevel')}"

    if event_type == "MenuOpened":
        entries = payload.get("entries") or []
        preview = []

        for entry in entries[:3]:
            preview.append(f"{entry.get('option', '')} {entry.get('target', '')}".strip())

        return f"menuEntryCount={payload.get('menuEntryCount')} entries={'; '.join(preview)}"

    if event_type == "ItemContainerChanged":
        return f"containerId={payload.get('containerId')} size={payload.get('size')}"

    if event_type in ("ItemSpawned", "ItemDespawned", "ItemQuantityChanged"):
        return f"id={payload.get('id')} qty={payload.get('quantity')} {payload.get('worldX')},{payload.get('worldY')}"

    if event_type in ("AnimationChanged", "NpcSpawned", "NpcDespawned", "PlayerSpawned", "PlayerDespawned", "PlayerChanged", "NpcDeath"):
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
        return " ".join(f"{key}={payload.get(key)}" for key in ("index", "varbitId", "varpId", "value") if key in payload)

    return " ".join(f"{key}={value}" for key, value in list(payload.items())[:4])


def event_summary(source: Path, event: dict) -> dict:
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


def atomic_replace(path: Path, writer):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    writer(temp_path)
    os.replace(temp_path, path)


def write_json(path: Path, data):
    def writer(temp_path: Path):
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)

    atomic_replace(path, writer)


def write_jsonl(path: Path, rows):
    def writer(temp_path: Path):
        with temp_path.open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row, separators=(",", ":")))
                file.write("\n")

    atomic_replace(path, writer)


def export_session(session: Path):
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    ticks = tick_files(session)
    events = event_files(session)
    tick_rows = []
    event_rows = []
    event_type_counts = Counter()
    first_tick_id = None
    last_tick_id = None

    for source, tick in iter_jsonl(ticks):
        summary = tick_summary(session, source, tick)
        tick_rows.append(summary)
        tick_id = summary.get("tickId")

        if first_tick_id is None:
            first_tick_id = tick_id

        last_tick_id = tick_id

    for source, event in iter_jsonl(events):
        summary = event_summary(source, event)
        event_rows.append(summary)
        event_type_counts[summary.get("eventType", "UNKNOWN")] += 1

    session_index = {
        "sessionPath": str(session),
        "manifest": read_manifest(session),
        "exportedAtUtc": datetime.now(timezone.utc).isoformat(),
        "tickCount": len(tick_rows),
        "eventCount": len(event_rows),
        "tickSegmentCount": len(ticks),
        "eventSegmentCount": len(events),
        "dictionaryCounts": dictionary_counts(session),
        "sessionSizeMb": round(directory_size(session) / (1024 * 1024), 3),
        "firstTickId": first_tick_id,
        "lastTickId": last_tick_id,
        "topEventTypeCounts": dict(event_type_counts.most_common(20)),
    }

    write_json(EXPORT_DIR / "session_index.json", session_index)
    write_jsonl(EXPORT_DIR / "tick_summary.jsonl", tick_rows)
    write_jsonl(EXPORT_DIR / "event_summary.jsonl", event_rows)

    print(f"Exported session: {session}")
    print(f"  {EXPORT_DIR / 'session_index.json'}")
    print(f"  {EXPORT_DIR / 'tick_summary.jsonl'} ({len(tick_rows)} rows)")
    print(f"  {EXPORT_DIR / 'event_summary.jsonl'} ({len(event_rows)} rows)")


def main():
    session = find_newest_session()

    if session is None:
        print(f"No sessions found in: {TELEMETRY_ROOT}")
        return

    export_session(session)


if __name__ == "__main__":
    main()

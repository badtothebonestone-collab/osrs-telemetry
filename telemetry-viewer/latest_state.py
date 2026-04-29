import json
import os
import time
from collections import deque
from pathlib import Path


TELEMETRY_ROOT = Path(r"C:\Users\stone\.osrs-telemetry\sessions")
LATEST_DIR = Path("latest")
LATEST_TICK_FILE = LATEST_DIR / "latest_tick.json"
LATEST_STATUS_FILE = LATEST_DIR / "latest_status.json"
LATEST_EVENTS_FILE = LATEST_DIR / "latest_events.json"
MAX_EVENTS = 50


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


def atomic_write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, separators=(",", ":"))

    os.replace(temp_path, path)


def read_manifest(session: Path) -> dict | None:
    path = session / "manifest.json"

    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def tick_files(session: Path) -> list[Path]:
    return sorted((session / "ticks").glob("ticks-*.jsonl"))


def event_files(session: Path) -> list[Path]:
    return sorted((session / "events").glob("events-*.jsonl"))


def file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def find_newest_active_session() -> Path | None:
    if not TELEMETRY_ROOT.exists():
        return None

    active_sessions = []

    for session in TELEMETRY_ROOT.iterdir():
        if not session.is_dir():
            continue

        manifest = read_manifest(session)

        if manifest and manifest.get("active"):
            active_sessions.append(session)

    if not active_sessions:
        return None

    return max(active_sessions, key=lambda session: file_mtime(session / "manifest.json"))


def iter_existing_records(files: list[Path]):
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


def count_items(items: list[dict]) -> int:
    return sum(1 for item in items if item.get("itemId", -1) > 0 and item.get("quantity", 0) > 0)


def summarize_status(tick: dict) -> dict:
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
        interacting = {
            "type": interacting_type,
            "nameOrHash": interacting_name,
            "index": status.get("interactingIndex"),
            "id": status.get("interactingId"),
            "worldX": status.get("interactingWorldX"),
            "worldY": status.get("interactingWorldY"),
            "plane": status.get("interactingPlane"),
        }
    else:
        interacting = None

    return {
        "tickId": tick.get("tickId"),
        "timestampUtc": tick.get("timestampUtc"),
        "gameState": tick.get("gameState"),
        "position": {
            "worldX": local_player.get("worldX"),
            "worldY": local_player.get("worldY"),
            "plane": local_player.get("plane"),
        },
        "runEnergyPercent": status.get("runEnergyPercent"),
        "hp": {
            "boosted": status.get("hitpointsBoosted"),
            "real": status.get("hitpointsReal"),
        },
        "prayer": {
            "boosted": status.get("prayerBoosted"),
            "real": status.get("prayerReal"),
        },
        "activePrayerNames": active_prayers,
        "interactingTarget": interacting,
        "inventoryCount": count_items(tick.get("inventory") or []),
        "equipmentCount": count_items(tick.get("equipment") or []),
        "npcCount": len(tick.get("npcs") or []),
        "playerCount": len(tick.get("players") or []),
        "sceneObjectsCount": len(tick.get("sceneObjects") or []),
        "groundItemsCount": len(tick.get("groundItems") or []),
        "captureErrorCount": len(tick.get("captureErrors") or []),
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


def summarize_event(event: dict) -> dict:
    event_type = event.get("eventType")
    payload = event.get("payload")

    return {
        "tickId": event.get("tickId"),
        "timestampUtc": event.get("timestampUtc"),
        "eventType": event_type,
        "category": CATEGORY_BY_EVENT_TYPE.get(event_type, "unknown"),
        "summary": event_summary_text(event_type, payload),
    }


def newest_file(files: list[Path]) -> Path | None:
    return files[-1] if files else None


def open_at_end(path: Path):
    file = path.open("r", encoding="utf-8")
    file.seek(0, 2)
    return file


def write_latest_tick(tick: dict):
    atomic_write_json(LATEST_TICK_FILE, tick)
    status = summarize_status(tick)
    atomic_write_json(LATEST_STATUS_FILE, status)
    print(
        f"tick={status.get('tickId')} state={status.get('gameState')} "
        f"pos={status['position'].get('worldX')},{status['position'].get('worldY')},{status['position'].get('plane')} "
        f"hp={status['hp'].get('boosted')}/{status['hp'].get('real')} "
        f"prayer={status['prayer'].get('boosted')}/{status['prayer'].get('real')} "
        f"events->{LATEST_EVENTS_FILE}",
        flush=True,
    )


def write_latest_events(events: deque):
    atomic_write_json(LATEST_EVENTS_FILE, {"events": list(events)})


def seed_latest(session: Path) -> tuple[Path | None, Path | None, deque]:
    latest_tick = None
    recent_events = deque(maxlen=MAX_EVENTS)

    for _, tick in iter_existing_records(tick_files(session)):
        latest_tick = tick

    for _, event in iter_existing_records(event_files(session)):
        recent_events.append(summarize_event(event))

    if latest_tick is not None:
        write_latest_tick(latest_tick)

    write_latest_events(recent_events)
    return newest_file(tick_files(session)), newest_file(event_files(session)), recent_events


def follow_session(session: Path):
    tick_file_path, event_file_path, recent_events = seed_latest(session)

    if tick_file_path is None and event_file_path is None:
        print(f"No tick or event segments found in active session: {session}")
        return

    tick_file = open_at_end(tick_file_path) if tick_file_path else None
    event_file = open_at_end(event_file_path) if event_file_path else None
    print(f"Following active session: {session}", flush=True)

    try:
        while True:
            updated = False

            if tick_file is not None:
                line = tick_file.readline()

                if line:
                    try:
                        write_latest_tick(json.loads(line))
                        updated = True
                    except json.JSONDecodeError:
                        pass

            if event_file is not None:
                line = event_file.readline()

                if line:
                    try:
                        recent_events.append(summarize_event(json.loads(line)))
                        write_latest_events(recent_events)
                        updated = True
                    except json.JSONDecodeError:
                        pass

            latest_tick_file = newest_file(tick_files(session))

            if latest_tick_file is not None and latest_tick_file != tick_file_path:
                if tick_file is not None:
                    tick_file.close()
                tick_file_path = latest_tick_file
                tick_file = tick_file_path.open("r", encoding="utf-8")
                print(f"Switched to tick segment: {tick_file_path.name}", flush=True)

            latest_event_file = newest_file(event_files(session))

            if latest_event_file is not None and latest_event_file != event_file_path:
                if event_file is not None:
                    event_file.close()
                event_file_path = latest_event_file
                event_file = event_file_path.open("r", encoding="utf-8")
                print(f"Switched to event segment: {event_file_path.name}", flush=True)

            if not updated:
                time.sleep(0.25)
    finally:
        if tick_file is not None:
            tick_file.close()
        if event_file is not None:
            event_file.close()


def main():
    session = find_newest_active_session()

    if session is None:
        print(f"No active sessions found in: {TELEMETRY_ROOT}", flush=True)
        return

    follow_session(session)


if __name__ == "__main__":
    main()

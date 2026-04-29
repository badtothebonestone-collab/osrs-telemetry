import json
import time
from pathlib import Path


TELEMETRY_ROOT = Path(r"C:\Users\stone\.osrs-telemetry\sessions")


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


def read_records(file_path: Path) -> list[dict]:
    records = []

    if not file_path.exists() or file_path.stat().st_size == 0:
        return records

    with file_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if not line:
                continue

            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    return records


def read_latest_tick(session: Path) -> dict | None:
    for file_path in reversed(tick_files(session)):
        records = read_records(file_path)

        if records:
            return records[-1]

    return None


def read_recent_events(session: Path, limit: int) -> list[dict]:
    events: list[dict] = []

    for file_path in reversed(event_files(session)):
        records = read_records(file_path)

        if records:
            events = records + events

        if len(events) >= limit:
            break

    return events[-limit:]


def recent_event_counts(session: Path, event_types: list[str], max_records: int = 500) -> dict[str, int]:
    counts = {event_type: 0 for event_type in event_types}
    events = read_recent_events(session, max_records)

    for event in events:
        event_type = event.get("eventType")

        if event_type in counts:
            counts[event_type] += 1

    return {event_type: count for event_type, count in counts.items() if count > 0}


def newest_tick_file(session: Path) -> Path | None:
    files = tick_files(session)
    return files[-1] if files else None


def follow_ticks(session: Path):
    current_file = newest_tick_file(session)

    if current_file is None:
        print("No tick files found yet.")
        return

    file = current_file.open("r", encoding="utf-8")
    file.seek(0, 2)

    try:
        while True:
            line = file.readline()

            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    print("Skipped bad JSON line.")
                continue

            latest_file = newest_tick_file(session)

            if latest_file is not None and latest_file != current_file:
                file.close()
                current_file = latest_file
                print(f"Switched to tick segment: {current_file.name}")
                file = current_file.open("r", encoding="utf-8")
                continue

            time.sleep(0.25)
    finally:
        file.close()


def summarize_tick(tick: dict):
    tick_id = tick.get("tickId")
    game_state = tick.get("gameState")

    local_player = tick.get("localPlayer") or {}
    world_x = local_player.get("worldX")
    world_y = local_player.get("worldY")
    plane = local_player.get("plane")
    animation = local_player.get("animation")

    inventory = tick.get("inventory") or []
    filled_slots = [
        item for item in inventory
        if item.get("itemId", -1) > 0 and item.get("quantity", 0) > 0
    ]
    equipment = tick.get("equipment") or []
    equipped_slots = [
        item for item in equipment
        if item.get("itemId", -1) > 0 and item.get("quantity", 0) > 0
    ]

    skills = tick.get("skills") or []
    hp = next((skill for skill in skills if skill.get("name") == "HITPOINTS"), None)
    prayer = next((skill for skill in skills if skill.get("name") == "PRAYER"), None)
    status = tick.get("status") or {}
    active_prayers = [
        prayer_snapshot
        for prayer_snapshot in (tick.get("activePrayers") or [])
        if prayer_snapshot.get("active")
    ]
    active_prayer_names = [prayer_snapshot.get("name", "?") for prayer_snapshot in active_prayers[:4]]
    hp_current = status.get("hitpointsBoosted", hp.get("boostedLevel") if hp else "?")
    hp_real = status.get("hitpointsReal", hp.get("realLevel") if hp else "?")
    prayer_current = status.get("prayerBoosted", prayer.get("boostedLevel") if prayer else "?")
    prayer_real = status.get("prayerReal", prayer.get("realLevel") if prayer else "?")
    run_percent = status.get("runEnergyPercent")
    run_display = f"{run_percent:.1f}%" if isinstance(run_percent, (int, float)) else "?"
    interacting = status.get("interactingType")

    if interacting and interacting != "UNKNOWN":
        target_name = status.get("interactingName") or status.get("interactingId") or "?"
        interacting_display = f"{interacting}:{target_name}"
    else:
        interacting_display = "none"

    print(
        f"tick={tick_id} | "
        f"state={game_state} | "
        f"pos=({world_x}, {world_y}, {plane}) | "
        f"anim={animation} | "
        f"inventory={len(filled_slots)}/28 | "
        f"equipped={len(equipped_slots)} | "
        f"run={run_display} | "
        f"hp={hp_current}/{hp_real} | "
        f"prayer={prayer_current}/{prayer_real} | "
        f"activePrayers={len(active_prayers)}[{','.join(active_prayer_names)}] | "
        f"target={interacting_display} | "
        f"npcs={len(tick.get('npcs') or [])} | "
        f"players={len(tick.get('players') or [])} | "
        f"sceneObjects={len(tick.get('sceneObjects') or [])} | "
        f"groundItems={len(tick.get('groundItems') or [])}"
    )


def summarize_event(event: dict):
    event_seq = event.get("eventSeq")
    tick_id = event.get("tickId")
    event_type = event.get("eventType")
    payload = event.get("payload") or {}

    detail = ""

    if event_type == "GameStateChanged":
        detail = f"gameState={payload.get('gameState')}"
    elif event_type == "ItemContainerChanged":
        detail = f"containerId={payload.get('containerId')} size={payload.get('size')}"
    elif event_type == "StatChanged":
        detail = (
            f"skill={payload.get('skill')} "
            f"level={payload.get('level')} "
            f"boosted={payload.get('boostedLevel')}"
        )
    elif event_type == "MenuOpened":
        entries = payload.get("entries") or []
        preview = []

        for entry in entries[:3]:
            option = entry.get("option") or ""
            target = entry.get("target") or ""
            entry_type = entry.get("type") or ""
            preview.append(f"{option} {target} ({entry_type})".strip())

        detail = (
            f"menuEntryCount={payload.get('menuEntryCount', len(entries))} "
            f"entries={'; '.join(preview)}"
        )

    print(f"event={event_seq} | tick={tick_id} | type={event_type} | {detail}".rstrip())


def main():
    session = find_newest_session()

    if session is None:
        print(f"No sessions found in: {TELEMETRY_ROOT}")
        return

    print("Reading newest session:")
    print(session)
    print()

    events = read_recent_events(session, 10)
    event_counts = recent_event_counts(session, [
        "AnimationChanged",
        "InteractingChanged",
        "HitsplatApplied",
        "ProjectileMoved",
        "GraphicsObjectCreated",
    ])

    if event_counts:
        print(
            "Recent combat/effect events: "
            + " ".join(f"{name}={count}" for name, count in event_counts.items())
        )
        print()

    if events:
        menu_opened_count = sum(1 for event in events if event.get("eventType") == "MenuOpened")
        print(f"Recent MenuOpened events: {menu_opened_count}")
        print("Last events:")
        for event in events:
            summarize_event(event)
        print()

    latest_tick = read_latest_tick(session)

    if latest_tick is not None:
        print("Latest existing tick:")
        summarize_tick(latest_tick)
        print()
    else:
        print("No ticks have been written yet.")
        print("Keep this viewer running; new ticks will appear here once RuneLite emits GameTick events.")
        print()

    print("Waiting for new ticks...")

    for tick in follow_ticks(session):
        summarize_tick(tick)


if __name__ == "__main__":
    main()

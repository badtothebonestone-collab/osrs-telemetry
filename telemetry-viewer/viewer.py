import argparse
import json
import os
import time
from pathlib import Path

from telemetry_paths import (
    classify_frame_state,
    find_newest_session,
    get_sessions_dir,
    iter_jsonl,
    list_event_files,
    list_tick_files,
    safe_read_json,
)

VERBOSE_FRAMES = os.environ.get("OSRS_TELEMETRY_VERBOSE_FRAMES") == "1"


def tick_files(session: Path) -> list[Path]:
    return list_tick_files(session)


def event_files(session: Path) -> list[Path]:
    return list_event_files(session)


def read_records(file_path: Path) -> list[dict]:
    records = []

    if not file_path.exists() or file_path.stat().st_size == 0:
        return records

    for _, record in iter_jsonl([file_path]):
        records.append(record)

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


def frame_state(session: Path, tick: dict, is_latest: bool, active_session: bool) -> dict:
    return classify_frame_state(
        session,
        tick,
        is_latest=is_latest,
        active_session=active_session,
    )


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


def summarize_tick(session: Path, tick: dict, is_latest: bool = True, active_session: bool = False):
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
    frame = frame_state(session, tick, is_latest, active_session)
    frame_status = frame["frameCaptureStatus"]
    frame_source = frame["frameCaptureSource"]
    frame_warning = frame["frameCaptureWarning"]
    frame_path = frame["framePath"]
    frame_display = "none"

    if frame_path:
        frame_display = (
            f"{frame_path} exists={frame['frameExists']} "
            f"pending={frame['framePending']} "
            f"expiredOrMissing={frame['frameExpiredOrMissing']} "
            f"source={frame_source or '?'}"
        )

        if VERBOSE_FRAMES:
            frame_display = f"{frame_display} absolute={frame['absoluteFramePath']}"
    elif frame_status:
        frame_display = f"{frame_status} source={frame_source or '?'}"

    if frame_warning:
        frame_display = f"{frame_display} warning={frame_warning}"

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
        f"groundItems={len(tick.get('groundItems') or [])} | "
        f"frame={frame_display}"
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


def parse_args():
    parser = argparse.ArgumentParser(description="Follow the newest OSRS telemetry session.")
    parser.add_argument("--sessions-dir", help="Override the telemetry sessions directory.")
    return parser.parse_args()


def main():
    args = parse_args()
    sessions_dir = get_sessions_dir(args.sessions_dir)
    session = find_newest_session(sessions_dir)

    if session is None:
        print(f"No sessions found in: {sessions_dir}")
        return

    print("Reading newest session:")
    print(session)
    print()
    manifest = safe_read_json(session / "manifest.json")
    active_session = bool(isinstance(manifest, dict) and manifest.get("active"))

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
        summarize_tick(session, latest_tick, active_session=active_session)
        print()
    else:
        print("No ticks have been written yet.")
        print("Keep this viewer running; new ticks will appear here once RuneLite emits GameTick events.")
        print()

    print("Waiting for new ticks...")

    for tick in follow_ticks(session):
        summarize_tick(session, tick, active_session=active_session)


if __name__ == "__main__":
    main()

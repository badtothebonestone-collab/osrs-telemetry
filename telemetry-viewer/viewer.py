import json
import time
from pathlib import Path


TELEMETRY_ROOT = Path(r"C:\Users\stone\.osrs-telemetry\sessions")


def find_newest_session() -> Path | None:
    if not TELEMETRY_ROOT.exists():
        return None

    sessions = [
        path for path in TELEMETRY_ROOT.iterdir()
        if path.is_dir() and (path / "ticks.jsonl").exists()
    ]

    if not sessions:
        return None

    return max(sessions, key=lambda path: (path / "ticks.jsonl").stat().st_mtime)


def read_latest_tick(file_path: Path) -> dict | None:
    latest_tick = None

    with file_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if not line:
                continue

            try:
                latest_tick = json.loads(line)
            except json.JSONDecodeError:
                continue

    return latest_tick


def follow_jsonl(file_path: Path):
    with file_path.open("r", encoding="utf-8") as file:
        # Jump to end of file so we only see new ticks.
        file.seek(0, 2)

        while True:
            line = file.readline()

            if not line:
                time.sleep(0.25)
                continue

            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                print("Skipped bad JSON line.")


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

    print(
        f"tick={tick_id} | "
        f"state={game_state} | "
        f"pos=({world_x}, {world_y}, {plane}) | "
        f"anim={animation} | "
        f"inventory={len(filled_slots)}/28 | "
        f"equipped={len(equipped_slots)} | "
        f"hp={hp.get('boostedLevel') if hp else '?'} "
        f"prayer={prayer.get('boostedLevel') if prayer else '?'} | "
        f"npcs={len(tick.get('npcs') or [])} | "
        f"players={len(tick.get('players') or [])}"
    )


def main():
    session = find_newest_session()

    if session is None:
        print(f"No sessions found in: {TELEMETRY_ROOT}")
        return

    tick_file = session / "ticks.jsonl"

    print(f"Reading newest session:")
    print(tick_file)
    print()

    latest_tick = read_latest_tick(tick_file)

    if latest_tick is not None:
        print("Latest existing tick:")
        summarize_tick(latest_tick)
        print()

    print("Waiting for new ticks...")

    for tick in follow_jsonl(tick_file):
        summarize_tick(tick)


if __name__ == "__main__":
    main()

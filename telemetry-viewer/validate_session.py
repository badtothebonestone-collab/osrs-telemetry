import json
from collections import Counter
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


def read_manifest(session: Path) -> tuple[dict | None, list[str]]:
    manifest_path = session / "manifest.json"

    if not manifest_path.exists():
        return None, ["missing manifest"]

    try:
        return json.loads(manifest_path.read_text(encoding="utf-8")), []
    except json.JSONDecodeError as error:
        return None, [f"manifest JSON decode error: {error}"]


def iter_jsonl(file_paths: list[Path]):
    for file_path in file_paths:
        try:
            with file_path.open("r", encoding="utf-8") as file:
                for line_number, line in enumerate(file, start=1):
                    line = line.strip()

                    if not line:
                        continue

                    try:
                        yield file_path, line_number, json.loads(line), None
                    except json.JSONDecodeError as error:
                        yield file_path, line_number, None, error
        except OSError as error:
            yield file_path, 0, None, error


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


def frame_files(session: Path) -> list[Path]:
    frames = session / "frames"

    if not frames.exists():
        return []

    return sorted(path for path in frames.iterdir() if path.is_file())


def dictionary_summary(session: Path) -> list[str]:
    dictionaries = session / "dictionaries"
    lines = []

    for name in ("items.json", "npcs.json", "objects.json"):
        path = dictionaries / name

        if not path.exists():
            lines.append(f"{name}: missing")
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            count = len(data) if isinstance(data, dict) else 0
            lines.append(f"{name}: present, {count} entries")
        except json.JSONDecodeError:
            lines.append(f"{name}: present, JSON decode error")

    return lines


def dictionary_summary_with_problems(session: Path) -> tuple[list[str], list[str]]:
    dictionaries = session / "dictionaries"
    lines = []
    problems = []

    for name in ("items.json", "npcs.json", "objects.json"):
        path = dictionaries / name

        if not path.exists():
            lines.append(f"{name}: missing")
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            count = len(data) if isinstance(data, dict) else 0
            lines.append(f"{name}: present, {count} entries")
        except json.JSONDecodeError as error:
            lines.append(f"{name}: present, JSON decode error")
            problems.append(f"dictionary JSON error in {name}: {error}")

    return lines, problems


def missing_fields(record: dict, required_fields: tuple[str, ...]) -> list[str]:
    return [field for field in required_fields if field not in record]


def validate_session(session: Path):
    problems = []
    manifest, manifest_problems = read_manifest(session)
    problems.extend(manifest_problems)

    ticks = tick_files(session)
    events = event_files(session)

    if not ticks:
        problems.append("no tick files")
    elif ticks[-1].stat().st_size == 0:
        problems.append(f"empty newest tick segment: {ticks[-1]}")

    if manifest and manifest.get("active") and (
        not manifest.get("currentTickSegment") or not manifest.get("currentEventSegment")
    ):
        problems.append("active session missing current segment in manifest")

    if manifest:
        for key in ("currentTickSegment", "currentEventSegment"):
            value = manifest.get(key)

            if value and not (session / value).exists():
                problems.append(f"manifest {key} does not exist: {value}")

    tick_count = 0
    event_count = 0
    first_tick_id = None
    last_tick_id = None
    previous_tick_id = None
    capture_error_count = 0
    event_type_counts = Counter()
    tick_schema_counts = Counter()
    event_schema_counts = Counter()
    sampled_tick_required_missing = Counter()
    sampled_event_required_missing = Counter()
    tick_samples_checked = 0
    event_samples_checked = 0
    json_error_count = 0
    ticks_with_frame_path = 0
    missing_referenced_frames = 0

    for file_path, line_number, record, error in iter_jsonl(ticks):
        if error is not None:
            json_error_count += 1
            problems.append(f"tick JSON error in {file_path.name}:{line_number}: {error}")
            continue

        tick_count += 1
        tick_schema_counts[record.get("schemaVersion", "MISSING")] += 1
        tick_id = record.get("tickId")

        if tick_samples_checked < 100:
            tick_samples_checked += 1

            for field in missing_fields(record, ("schemaVersion", "tickId", "timestampUtc")):
                sampled_tick_required_missing[field] += 1

        if first_tick_id is None:
            first_tick_id = tick_id

        if isinstance(tick_id, int):
            if isinstance(previous_tick_id, int) and tick_id <= previous_tick_id:
                problems.append(f"tickId not increasing at {file_path.name}:{line_number}: {tick_id} <= {previous_tick_id}")
            previous_tick_id = tick_id

        last_tick_id = tick_id
        capture_errors = record.get("captureErrors") or []

        if capture_errors:
            capture_error_count += len(capture_errors)

        frame_path = record.get("framePath")

        if frame_path:
            ticks_with_frame_path += 1

            if not (session / frame_path).exists():
                missing_referenced_frames += 1

    if capture_error_count:
        problems.append(f"captureErrors present: {capture_error_count}")

    for file_path, line_number, record, error in iter_jsonl(events):
        if error is not None:
            json_error_count += 1
            problems.append(f"event JSON error in {file_path.name}:{line_number}: {error}")
            continue

        event_count += 1
        event_schema_counts[record.get("schemaVersion", "MISSING")] += 1
        event_type_counts[record.get("eventType", "UNKNOWN")] += 1

        if event_samples_checked < 100:
            event_samples_checked += 1

            for field in missing_fields(record, ("schemaVersion", "tickId", "timestampUtc", "eventType")):
                sampled_event_required_missing[field] += 1

    print(f"Session: {session}")
    print()
    print("Manifest:")

    if manifest:
        keys = [
            "schemaVersion",
            "sessionId",
            "active",
            "startedAtUtc",
            "endedAtUtc",
            "currentTickSegment",
            "currentEventSegment",
            "tickSegmentIndex",
            "eventSegmentIndex",
            "tickCount",
            "eventCount",
            "droppedRecords",
            "frameCount",
            "droppedFrameCount",
            "screenshotEveryTicks",
            "screenshotFormat",
            "maxFrameStorageMb",
            "lastUpdatedUtc",
        ]

        for key in keys:
            print(f"  {key}: {manifest.get(key)}")
    else:
        print("  missing or unreadable")

    print()
    print(f"Total tick records: {tick_count}")
    print(f"Total event records: {event_count}")
    print(f"First tickId / last tickId: {first_tick_id} / {last_tick_id}")
    print(f"Total captureErrors count: {capture_error_count}")
    print(f"Tick segment count: {len(ticks)}")
    print(f"Event segment count: {len(events)}")
    frames = frame_files(session)
    print(f"Ticks with framePath: {ticks_with_frame_path}")
    print(f"Existing frame files: {len(frames)}")
    print(f"Missing referenced frames: {missing_referenced_frames}")
    print(f"Frames folder size MB: {directory_size(session / 'frames') / (1024 * 1024):.2f}")
    print(f"Session size MB: {directory_size(session) / (1024 * 1024):.2f}")
    print(f"JSON decode errors: {json_error_count}")
    print(f"Tick schemaVersion counts: {dict(tick_schema_counts)}")
    print(f"Event schemaVersion counts: {dict(event_schema_counts)}")
    print(f"Sampled tick required-field misses: {dict(sampled_tick_required_missing)}")
    print(f"Sampled event required-field misses: {dict(sampled_event_required_missing)}")
    print()
    print("Top event types:")

    if event_type_counts:
        for event_type, count in event_type_counts.most_common(20):
            print(f"  {event_type}: {count}")
    else:
        print("  none")

    print()
    print("Dictionaries:")
    dictionary_lines, dictionary_problems = dictionary_summary_with_problems(session)
    problems.extend(dictionary_problems)

    for line in dictionary_lines:
        print(f"  {line}")

    print()
    print("Problems:")

    if problems:
        for problem in problems:
            print(f"  - {problem}")
    else:
        print("  none detected")


def main():
    session = find_newest_session()

    if session is None:
        print(f"No sessions found in: {TELEMETRY_ROOT}")
        return

    validate_session(session)


if __name__ == "__main__":
    main()

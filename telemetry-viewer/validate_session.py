import argparse
import json
from collections import Counter
from pathlib import Path

from telemetry_paths import (
    classify_frame_state,
    directory_size,
    find_newest_session,
    get_sessions_dir,
    iter_jsonl,
    list_event_files,
    list_tick_files,
    session_size_mb,
)


def tick_files(session: Path) -> list[Path]:
    return list_tick_files(session)


def event_files(session: Path) -> list[Path]:
    return list_event_files(session)


def read_manifest(session: Path) -> tuple[dict | None, list[str]]:
    manifest_path = session / "manifest.json"

    if not manifest_path.exists():
        return None, ["missing manifest"]

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return manifest if isinstance(manifest, dict) else None, []
    except json.JSONDecodeError as error:
        return None, [f"manifest JSON decode error: {error}"]
    except OSError as error:
        return None, [f"manifest read error: {error}"]


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
    frame_capture_source_counts = Counter()
    sampled_tick_required_missing = Counter()
    sampled_event_required_missing = Counter()
    tick_samples_checked = 0
    event_samples_checked = 0
    json_error_count = 0
    ticks_with_frame_path = 0
    missing_referenced_frames = 0
    missing_frame_records = []

    for file_path, line_number, record, error in iter_jsonl(ticks, with_errors=True):
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

            frame = classify_frame_state(session, record)

            if frame["frameExists"] is False:
                missing_referenced_frames += 1
                missing_frame_records.append({
                    "tickId": tick_id,
                    "tick": record,
                    "status": record.get("frameCaptureStatus"),
                    "framePath": frame_path,
                    "source": record.get("frameCaptureSource"),
                })

        frame_source = record.get("frameCaptureSource")

        if frame_source:
            frame_capture_source_counts[frame_source] += 1

    if capture_error_count:
        problems.append(f"captureErrors present: {capture_error_count}")

    pending_queued_frames = 0
    expired_or_deleted_frames = 0
    active_session = bool(manifest and manifest.get("active"))

    for missing in missing_frame_records:
        is_newest_active_tick = active_session and missing.get("tickId") == last_tick_id
        frame = classify_frame_state(
            session,
            missing["tick"],
            is_latest=is_newest_active_tick,
            active_session=active_session,
        )

        if frame["framePending"]:
            pending_queued_frames += 1
        else:
            expired_or_deleted_frames += 1

    for file_path, line_number, record, error in iter_jsonl(events, with_errors=True):
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
            "deletedFrameCount",
            "screenshotEveryTicks",
            "screenshotFormat",
            "maxFrameStorageMb",
            "frameCleanupIntervalSeconds",
            "frameCaptureMode",
            "allowScreenRectangleFallback",
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
    print(f"ticksWithFramePath: {ticks_with_frame_path}")
    print(f"existingFrameFiles: {len(frames)}")
    print(f"missingReferencedFrames: {missing_referenced_frames}")
    print(f"pendingQueuedFrames: {pending_queued_frames}")
    print(f"expiredOrDeletedFrames: {expired_or_deleted_frames}")
    print(f"Frames folder size MB: {directory_size(session / 'frames') / (1024 * 1024):.2f}")
    print(f"Frame capture source counts: {dict(frame_capture_source_counts)}")
    print(f"Session size MB: {session_size_mb(session):.2f}")
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


def parse_args():
    parser = argparse.ArgumentParser(description="Validate the newest OSRS telemetry session.")
    parser.add_argument("--sessions-dir", help="Override the telemetry sessions directory.")
    return parser.parse_args()


def main():
    args = parse_args()
    sessions_dir = get_sessions_dir(args.sessions_dir)
    session = find_newest_session(sessions_dir)

    if session is None:
        print(f"No sessions found in: {sessions_dir}")
        return

    validate_session(session)


if __name__ == "__main__":
    main()

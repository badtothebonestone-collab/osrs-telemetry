import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_SESSIONS_DIR = Path.home() / ".osrs-telemetry" / "sessions"
SESSIONS_DIR_ENV = "OSRS_TELEMETRY_SESSIONS_DIR"
FRAME_PENDING_GRACE_SECONDS = 2.0
FRAME_INDEX_REQUIRED_FIELDS = ("schemaVersion", "tickId", "status")
FRAME_INDEX_WRITTEN_STATUSES = {"WRITTEN", "FRAME_WRITTEN"}
FRAME_INDEX_DROPPED_STATUSES = {"DROPPED_QUEUE_FULL", "DROPPED", "FRAME_DROPPED"}
FRAME_INDEX_DELETED_STATUSES = {"DELETED", "FRAME_DELETED", "EXPIRED", "FRAME_EXPIRED"}
FRAME_INDEX_FAILED_STATUSES = {"CAPTURE_FAILED", "WRITE_FAILED", "WRITE_REJECTED", "FAILED", "FRAME_FAILED"}


def get_sessions_dir(sessions_dir: str | Path | None = None) -> Path:
    if sessions_dir:
        return Path(sessions_dir).expanduser()

    configured = os.environ.get(SESSIONS_DIR_ENV)

    if configured:
        return Path(configured).expanduser()

    return DEFAULT_SESSIONS_DIR


def list_tick_files(session_path: Path) -> list[Path]:
    segmented = sorted((session_path / "ticks").glob("ticks-*.jsonl"))

    if segmented:
        return segmented

    legacy = session_path / "ticks.jsonl"
    return [legacy] if legacy.exists() else []


def list_event_files(session_path: Path) -> list[Path]:
    segmented = sorted((session_path / "events").glob("events-*.jsonl"))

    if segmented:
        return segmented

    legacy = session_path / "events.jsonl"
    return [legacy] if legacy.exists() else []


def list_frame_index_files(session_path: Path) -> list[Path]:
    candidates = [
        session_path / "frame_index.jsonl",
        session_path / "frames" / "frame_index.jsonl",
    ]
    files = []

    for candidate in candidates:
        if candidate.exists() and candidate not in files:
            files.append(candidate)

    return files


def is_segmented_session(session_path: Path) -> bool:
    return bool(
        sorted((session_path / "ticks").glob("ticks-*.jsonl"))
        or sorted((session_path / "events").glob("events-*.jsonl"))
    )


def resolve_frame_path(session_path: Path, frame_path: str | None) -> Path | None:
    if not frame_path:
        return None

    return (session_path / frame_path).resolve()


def safe_read_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def raw_recording_unavailable_message(session_path: Path) -> str:
    manifest = safe_read_json(session_path / "manifest.json")
    recording_mode = manifest.get("recordingMode") if isinstance(manifest, dict) else None
    if recording_mode in {"LIVE_COMPACT_ONLY", "LIVE_COMPACT_WITH_FRAMES"}:
        return (
            f"Raw tick recording is disabled for this live session ({recording_mode}). "
            "Use DEBUG_RECORDING mode to create full audit datasets for batch/debug builders."
        )
    return (
        f"Raw tick files not found in session: {session_path}. "
        "Use DEBUG_RECORDING mode to create full audit datasets for batch/debug builders."
    )

def file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def session_mtime(session_path: Path) -> float:
    manifest = session_path / "manifest.json"
    files = list_tick_files(session_path) + list_event_files(session_path)
    mtimes = [file_mtime(manifest)] if manifest.exists() else []
    mtimes.extend(file_mtime(path) for path in files)
    mtimes.append(live_output_mtime(session_path))
    return max(mtimes) if mtimes else file_mtime(session_path)


LIVE_OUTPUT_RELATIVE_PATHS = (
    Path("interaction_geometry") / "live" / "overlay_debug_state.json",
    Path("interaction_geometry") / "live" / "live_candidates.jsonl",
    Path("interaction_geometry") / "live" / "live_status.json",
    Path("interaction_geometry") / "live" / "live_context_index.json",
)


def live_output_mtime(session_path: Path) -> float:
    mtimes = [file_mtime(session_path / relative) for relative in LIVE_OUTPUT_RELATIVE_PATHS if (session_path / relative).exists()]
    return max(mtimes) if mtimes else 0


def session_has_live_outputs(session_path: Path) -> bool:
    return any((session_path / relative).exists() for relative in LIVE_OUTPUT_RELATIVE_PATHS)


def find_newest_live_session(
    sessions_dir: str | Path | None = None,
    *,
    active_only: bool = False,
) -> Path | None:
    root = get_sessions_dir(sessions_dir)

    if not root.exists():
        return None

    sessions = []

    for path in root.iterdir():
        if not path.is_dir() or not session_has_live_outputs(path):
            continue

        manifest = safe_read_json(path / "manifest.json")
        if active_only and not (isinstance(manifest, dict) and manifest.get("active")):
            continue

        sessions.append(path)

    if not sessions:
        return None

    return max(sessions, key=lambda path: (live_output_mtime(path), str(path).lower()))


def find_newest_session(
    sessions_dir: str | Path | None = None,
    *,
    active_only: bool = False,
) -> Path | None:
    root = get_sessions_dir(sessions_dir)

    if not root.exists():
        return None

    sessions = []

    for path in root.iterdir():
        if not path.is_dir():
            continue

        manifest = safe_read_json(path / "manifest.json")
        has_session_data = bool(
            list_tick_files(path)
            or list_event_files(path)
            or session_has_live_outputs(path)
            or manifest
        )

        if not has_session_data:
            continue

        if active_only and not (isinstance(manifest, dict) and manifest.get("active")):
            continue

        sessions.append(path)

    if not sessions:
        return None

    return max(sessions, key=lambda path: (session_mtime(path), str(path).lower()))


def iter_jsonl(files: list[Path], *, with_errors: bool = False):
    for file_path in files:
        try:
            with file_path.open("r", encoding="utf-8") as file:
                for line_number, line in enumerate(file, start=1):
                    line = line.strip()

                    if not line:
                        continue

                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as error:
                        if with_errors:
                            yield file_path, line_number, None, error
                        continue

                    if with_errors:
                        yield file_path, line_number, record, None
                    else:
                        yield file_path, record
        except OSError as error:
            if with_errors:
                yield file_path, 0, None, error


def frame_index_event_type(record: dict) -> str:
    explicit = record.get("eventType")

    if isinstance(explicit, str) and explicit:
        return explicit

    status = str(record.get("status") or "").upper()

    if status in FRAME_INDEX_WRITTEN_STATUSES:
        return "FrameWritten"

    if status in FRAME_INDEX_DROPPED_STATUSES:
        return "FrameDropped"

    if status in FRAME_INDEX_DELETED_STATUSES or "DELETED" in status or "EXPIRED" in status:
        return "FrameDeleted"

    if status in FRAME_INDEX_FAILED_STATUSES:
        return "FrameFailed"

    if status in {"REQUESTED", "QUEUED"}:
        return "FrameRequested"

    return "FrameIndex"


def numeric_value(record: dict, *keys: str) -> int | float | None:
    for key in keys:
        value = record.get(key)

        if isinstance(value, (int, float)):
            return value

    return None


def frame_index_write_delay_ms(record: dict) -> int | float | None:
    return numeric_value(record, "frameWriteDelayMs", "writeDelayMs", "queueLatencyMs")


def frame_index_total_latency_ms(record: dict) -> int | float | None:
    return numeric_value(record, "frameTotalLatencyMs", "totalLatencyMs", "latencyMs")


def summarize_frame_index_record(source: Path, record: dict) -> dict:
    event_type = frame_index_event_type(record)
    status = record.get("status")
    error = record.get("error") or record.get("reason")

    return {
        "eventType": event_type,
        "tickId": record.get("tickId"),
        "framePath": record.get("framePath"),
        "captureSource": record.get("captureSource"),
        "status": status,
        "requestedAtUtc": record.get("requestedAtUtc") or record.get("timestampUtc"),
        "capturedAtUtc": record.get("capturedAtUtc"),
        "enqueuedAtUtc": record.get("enqueuedAtUtc"),
        "writtenAtUtc": record.get("writtenAtUtc"),
        "deletedAtUtc": record.get("deletedAtUtc"),
        "frameWritten": event_type == "FrameWritten",
        "frameWriteDelayMs": frame_index_write_delay_ms(record),
        "frameTotalLatencyMs": frame_index_total_latency_ms(record),
        "captureLatencyMs": numeric_value(record, "captureLatencyMs"),
        "queueLatencyMs": numeric_value(record, "queueLatencyMs"),
        "writeLatencyMs": numeric_value(record, "writeLatencyMs"),
        "width": record.get("width"),
        "height": record.get("height"),
        "sizeBytes": record.get("sizeBytes", record.get("bytes")),
        "droppedFrameCount": record.get("droppedFrameCount"),
        "error": error,
        "source": str(source),
    }


def iter_frame_index_records(session_path: Path, *, with_errors: bool = False):
    yield from iter_jsonl(list_frame_index_files(session_path), with_errors=with_errors)


def load_frame_index_summaries(session_path: Path) -> list[dict]:
    summaries = []

    for source, record in iter_frame_index_records(session_path):
        if isinstance(record, dict):
            summaries.append(summarize_frame_index_record(source, record))

    return summaries


def frame_index_by_tick(summaries: list[dict]) -> dict:
    by_tick = {}

    for summary in summaries:
        tick_id = summary.get("tickId")

        if tick_id is not None:
            by_tick[tick_id] = summary

    return by_tick


def latest_frame_index_event(summaries: list[dict]) -> dict | None:
    return summaries[-1] if summaries else None


def frame_timing_fields(summary: dict | None) -> dict:
    return {
        "frameRequested": summary is not None,
        "frameCaptured": bool(
            summary
            and (
                summary.get("capturedAtUtc")
                or isinstance(summary.get("captureLatencyMs"), (int, float))
            )
        ),
        "frameQueued": bool(
            summary
            and (
                summary.get("enqueuedAtUtc")
                or isinstance(summary.get("queueLatencyMs"), (int, float))
                or summary.get("frameWritten")
            )
        ),
        "frameWritten": bool(summary and summary.get("frameWritten")),
        "frameWriteDelayMs": summary.get("frameWriteDelayMs") if summary else None,
        "frameTotalLatencyMs": summary.get("frameTotalLatencyMs") if summary else None,
        "frameCaptureLatencyMs": summary.get("captureLatencyMs") if summary else None,
        "frameQueueLatencyMs": summary.get("queueLatencyMs") if summary else None,
        "frameIndexStatus": (summary.get("status") or summary.get("eventType")) if summary else None,
        "latestFrameIndexEvent": summary,
    }


def frame_index_stats(summaries: list[dict]) -> dict:
    event_type_counts = Counter(summary.get("eventType", "FrameIndex") for summary in summaries)
    status_counts = Counter(summary.get("status", "MISSING") for summary in summaries)
    write_delays = [
        summary["frameWriteDelayMs"]
        for summary in summaries
        if isinstance(summary.get("frameWriteDelayMs"), (int, float))
    ]
    latest = latest_frame_index_event(summaries)
    explicit_requested = event_type_counts.get("FrameRequested", 0)
    requested = explicit_requested if explicit_requested else len(summaries)

    return {
        "totalRecords": len(summaries),
        "FrameRequested": requested,
        "FrameWritten": event_type_counts.get("FrameWritten", 0),
        "FrameDropped": event_type_counts.get("FrameDropped", 0),
        "FrameDeleted": event_type_counts.get("FrameDeleted", 0),
        "FrameFailed": event_type_counts.get("FrameFailed", 0),
        "eventTypeCounts": dict(event_type_counts.most_common()),
        "statusCounts": dict(status_counts.most_common()),
        "latestWriteDelayMs": latest.get("frameWriteDelayMs") if latest else None,
        "maxWriteDelayMs": max(write_delays) if write_delays else None,
        "avgWriteDelayMs": (sum(write_delays) / len(write_delays)) if write_delays else None,
        "latestEvent": latest,
    }


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


def session_size_mb(session_path: Path) -> float:
    return directory_size(session_path) / (1024 * 1024)


def tick_age_seconds(tick: dict) -> float | None:
    timestamp = tick.get("timestampUtc")

    if not timestamp:
        return None

    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None

    return (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()


def classify_frame_state(
    session_path: Path,
    tick: dict,
    *,
    is_latest: bool = False,
    active_session: bool = False,
    grace_seconds: float = FRAME_PENDING_GRACE_SECONDS,
) -> dict:
    frame_path = tick.get("framePath")
    absolute_frame_path = resolve_frame_path(session_path, frame_path)
    exists = absolute_frame_path.exists() if absolute_frame_path else None
    pending = False
    expired_or_missing = False

    if frame_path and exists is False:
        age = tick_age_seconds(tick)
        fresh = age is None or age <= grace_seconds
        pending = (
            tick.get("frameCaptureStatus") == "QUEUED"
            and active_session
            and is_latest
            and fresh
        )
        expired_or_missing = not pending

    return {
        "framePath": frame_path,
        "frameExists": exists,
        "framePending": pending,
        "frameExpiredOrMissing": expired_or_missing,
        "frameCaptureStatus": tick.get("frameCaptureStatus"),
        "frameCaptureSource": tick.get("frameCaptureSource"),
        "frameCaptureWarning": tick.get("frameCaptureWarning"),
        "absoluteFramePath": str(absolute_frame_path) if absolute_frame_path else None,
    }

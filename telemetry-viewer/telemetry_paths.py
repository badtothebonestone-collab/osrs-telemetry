import json
import os
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_SESSIONS_DIR = Path.home() / ".osrs-telemetry" / "sessions"
SESSIONS_DIR_ENV = "OSRS_TELEMETRY_SESSIONS_DIR"
FRAME_PENDING_GRACE_SECONDS = 2.0


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
    return max(mtimes) if mtimes else file_mtime(session_path)


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
        has_session_data = bool(list_tick_files(path) or list_event_files(path) or manifest)

        if not has_session_data:
            continue

        if active_only and not (isinstance(manifest, dict) and manifest.get("active")):
            continue

        sessions.append(path)

    if not sessions:
        return None

    return max(sessions, key=session_mtime)


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

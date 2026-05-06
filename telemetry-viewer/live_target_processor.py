from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import build_world_target_geometry as world_builder
import inspect_target_geometry as geometry
import select_target_candidates as candidate_builder
from telemetry_paths import find_newest_session, get_sessions_dir, list_tick_files


LIVE_STATUS_SCHEMA = "live_status.v1"
LIVE_INDEX_SCHEMA = "live_index.v1"
LIVE_CONTEXT_INDEX_SCHEMA = "live_context_index.v1"
LIVE_BASELINE_SCHEMA = "live_baseline_state.v1"
LIVE_NAVIGATION_SCHEMA = "live_navigation_summary.v1"
LIVE_TICK_SUMMARY_SCHEMA = "live_tick_summary.v1"
LIVE_WORLD_TARGET_SCHEMA = "live_world_target_update.v1"
LIVE_UI_TARGET_SCHEMA = "live_ui_target_update.v1"
LIVE_CANDIDATE_SCHEMA = "live_candidate_packet.v1"
DEFAULT_TARGET_LIBRARY_PATH = Path(__file__).resolve().with_name("target_library.json")
DEFAULT_TARGET_PROFILES_PATH = Path(__file__).resolve().with_name("target_profiles.json")
DEFAULT_WRITE_RETRY_ATTEMPTS = 10
DEFAULT_WRITE_RETRY_DELAY_SECONDS = 0.01
MAX_WRITE_RETRY_DELAY_SECONDS = 0.25
WORLD_TARGET_EMIT_MODES = {"none", "candidates", "profile", "visible", "full"}
WORLD_TARGET_TYPES = {"npc", "player", "sceneObject", "groundItem", "tile"}
LATENCY_MODES = {"realtime", "complete"}
CANDIDATE_OUTPUT_WINDOWS = {"latest", "rolling"}
ALL_LIVE_TARGET_TYPES = WORLD_TARGET_TYPES | {
    "inventorySlot",
    "equipmentSlot",
    "prayerIcon",
    "magicSpell",
    "baseUiRegion",
}
LIVE_OUTPUT_FILES = (
    "live_world_targets.jsonl",
    "live_ui_targets.jsonl",
    "live_candidates.jsonl",
    "live_tick_summary.jsonl",
    "live_baseline_state.json",
    "live_context_index.json",
    "live_navigation_summary.json",
    "live_index.json",
    "live_status.json",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def freshness_millis_for(ticks: list[dict]) -> float | None:
    if not ticks:
        return None

    value = ticks[-1].get("timestampUtc")
    if not isinstance(value, str) or not value:
        return None

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        timestamp = datetime.fromisoformat(text)
    except ValueError:
        return None

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    return round(max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds() * 1000.0), 3)


def json_dump_compact(data) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or positive")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def resolve_session(args) -> Path:
    if args.session:
        session = Path(args.session).expanduser()
        if not session.exists():
            raise RuntimeError(f"Session does not exist: {session}")
        return session.resolve()

    if not args.latest_session:
        raise RuntimeError("Pass --session explicitly, or pass --latest-session to use the newest session.")

    session = find_newest_session(get_sessions_dir(args.sessions_dir))
    if session is None:
        raise RuntimeError(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")

    return session.resolve()


def live_output_dir(session: Path) -> Path:
    return session / "interaction_geometry" / "live"


def live_output_paths(session: Path) -> dict[str, Path]:
    output_dir = live_output_dir(session)
    return {
        "dir": output_dir,
        "worldTargets": output_dir / "live_world_targets.jsonl",
        "uiTargets": output_dir / "live_ui_targets.jsonl",
        "candidates": output_dir / "live_candidates.jsonl",
        "tickSummary": output_dir / "live_tick_summary.jsonl",
        "baseline": output_dir / "live_baseline_state.json",
        "contextIndex": output_dir / "live_context_index.json",
        "navigation": output_dir / "live_navigation_summary.json",
        "index": output_dir / "live_index.json",
        "status": output_dir / "live_status.json",
    }


def atomic_write_text(
    path: Path,
    text: str,
    *,
    options: WriteOptions | None = None,
    stats: WriteStats | None = None,
) -> int:
    options = options or WriteOptions()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")

    with temp.open("w", encoding="utf-8") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())

    attempts = max(1, options.retry_attempts)
    delay = max(0.0, options.retry_delay_seconds)
    last_error: OSError | None = None

    for attempt in range(attempts):
        try:
            temp.replace(path)
            if stats is not None:
                stats.record_success()
            return path.stat().st_size if path.exists() else 0
        except OSError as error:
            last_error = error
            if attempt >= attempts - 1:
                break
            if stats is not None:
                stats.record_retry()
            time.sleep(delay)
            delay = min(MAX_WRITE_RETRY_DELAY_SECONDS, max(delay * 2.0, 0.001))

    if stats is not None and last_error is not None:
        stats.record_failure(path, last_error)

    if options.strict and last_error is not None:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise last_error

    return 0


def atomic_write_json(path: Path, data: dict, *, options: WriteOptions | None = None, stats: WriteStats | None = None) -> int:
    return atomic_write_text(path, json.dumps(data, indent=2, sort_keys=False) + "\n", options=options, stats=stats)


def atomic_write_jsonl(path: Path, records: list[dict], *, options: WriteOptions | None = None, stats: WriteStats | None = None) -> int:
    return atomic_write_text(path, "".join(json_dump_compact(record) + "\n" for record in records), options=options, stats=stats)


def remove_file_if_exists(path: Path) -> int:
    if path.exists() and path.is_file():
        path.unlink()
    return 0


def clear_live_outputs(session: Path) -> None:
    output_dir = live_output_dir(session)
    for name in LIVE_OUTPUT_FILES:
        path = output_dir / name
        if path.exists() and path.is_file():
            path.unlink()


def stat_size(path: Path | None) -> tuple[int | None, float | None]:
    if path is None or not path.exists():
        return None, None
    try:
        stat = path.stat()
    except OSError:
        return None, None
    return stat.st_size, stat.st_mtime


@dataclass
class WriteStats:
    retry_count: int = 0
    failure_count: int = 0
    last_error: str | None = None
    last_error_path: str | None = None
    last_error_utc: str | None = None
    last_successful_write_utc: str | None = None

    def record_retry(self) -> None:
        self.retry_count += 1

    def record_failure(self, path: Path, error: BaseException) -> None:
        self.failure_count += 1
        self.last_error = f"{type(error).__name__}: {error}"
        self.last_error_path = str(path)
        self.last_error_utc = utc_now()

    def record_success(self) -> None:
        self.last_successful_write_utc = utc_now()


@dataclass
class WriteOptions:
    retry_attempts: int = DEFAULT_WRITE_RETRY_ATTEMPTS
    retry_delay_seconds: float = DEFAULT_WRITE_RETRY_DELAY_SECONDS
    strict: bool = False


def write_options_from(args) -> WriteOptions:
    return WriteOptions(
        retry_attempts=max(1, int(args.write_retry_attempts)),
        retry_delay_seconds=max(0.0, float(args.write_retry_delay_ms) / 1000.0),
        strict=bool(args.strict_writes),
    )


@dataclass
class Timing:
    values: dict[str, float] = field(default_factory=dict)

    def measure(self, key: str):
        return TimingContext(self, key)

    def set(self, key: str, value: float) -> None:
        self.values[key] = self.values.get(key, 0.0) + value

    def rounded(self) -> dict:
        return {key: round(value, 3) for key, value in self.values.items()}


class TimingContext:
    def __init__(self, timing: Timing, key: str):
        self.timing = timing
        self.key = key
        self.started = 0.0

    def __enter__(self):
        self.started = time.perf_counter()
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.timing.set(self.key, (time.perf_counter() - self.started) * 1000.0)


TIMING_BUCKETS = [
    "tailReadMillis",
    "jsonParseMillis",
    "rawTickIngestMillis",
    "baselineStateMillis",
    "worldTargetBuildMillis",
    "worldTargetFilterMillis",
    "candidateSelectMillis",
    "contextIndexMillis",
    "uiTargetLoadMillis",
    "outputWriteMillis",
    "totalDurationMillis",
]

TIMING_MODE = "nested"


def timing_payload(timing: Timing, total_duration_ms: float, tailer=None) -> dict:
    payload = {key: 0.0 for key in TIMING_BUCKETS}
    payload.update(timing.rounded())
    if tailer is not None:
        payload["tailReadMillis"] = round(tailer.last_tail_read_millis, 3)
        payload["jsonParseMillis"] = round(tailer.last_json_parse_millis, 3)
    payload["totalDurationMillis"] = round(total_duration_ms, 3)
    return payload


@dataclass
class TailState:
    offset: int = 0
    pending: str = ""
    line_number: int = 0


class TickJsonlTailer:
    def __init__(self, session: Path):
        self.session = session
        self.states: dict[Path, TailState] = {}
        self.malformed_counts: Counter[str] = Counter()
        self.malformed_total = 0
        self.read_errors: list[str] = []
        self.last_tail_read_millis = 0.0
        self.last_json_parse_millis = 0.0

    def files(self) -> list[Path]:
        return list_tick_files(self.session)

    def partial_line_files(self) -> list[str]:
        return [str(path) for path, state in self.states.items() if state.pending]

    def seek_to_end(self) -> None:
        for path in self.files():
            try:
                size = path.stat().st_size
            except OSError:
                continue
            self.states[path] = TailState(offset=size)

    def read_new_records(self) -> list[tuple[Path, int, dict]]:
        records: list[tuple[Path, int, dict]] = []
        self.last_tail_read_millis = 0.0
        self.last_json_parse_millis = 0.0

        for path in self.files():
            state = self.states.setdefault(path, TailState())

            try:
                size = path.stat().st_size
            except OSError as error:
                self.read_errors.append(f"could not stat {path}: {error}")
                continue

            if size < state.offset:
                state.offset = 0
                state.pending = ""
                state.line_number = 0

            if size == state.offset:
                continue

            started = time.perf_counter()
            try:
                with path.open("rb") as file:
                    file.seek(state.offset)
                    data = file.read()
            except OSError as error:
                self.read_errors.append(f"could not read {path}: {error}")
                continue
            self.last_tail_read_millis += (time.perf_counter() - started) * 1000.0

            state.offset += len(data)
            text = state.pending + data.decode("utf-8", errors="replace")
            last_newline = max(text.rfind("\n"), text.rfind("\r"))

            if last_newline < 0:
                state.pending = text
                continue

            complete = text[: last_newline + 1]
            state.pending = text[last_newline + 1 :]

            for raw_line in complete.splitlines():
                state.line_number += 1
                line = raw_line.strip()
                if not line:
                    continue
                parse_started = time.perf_counter()
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    self.last_json_parse_millis += (time.perf_counter() - parse_started) * 1000.0
                    self.malformed_counts[str(path)] += 1
                    self.malformed_total += 1
                    continue
                self.last_json_parse_millis += (time.perf_counter() - parse_started) * 1000.0

                if isinstance(record, dict):
                    records.append((path, state.line_number, record))
                else:
                    self.malformed_counts[str(path)] += 1
                    self.malformed_total += 1

        return records


def parse_tick_line(path: Path, line: str, malformed: Counter[str]) -> dict | None:
    text = line.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        malformed[str(path)] += 1
        return None
    return value if isinstance(value, dict) else None


def tail_existing_records(files: list[Path], limit: int, malformed: Counter[str]) -> list[tuple[Path, int, dict]]:
    if limit <= 0:
        return []

    found: deque[tuple[Path, int, dict]] = deque(maxlen=limit)

    # This is intentionally simple and schema-tolerant. Startup backfill is
    # capped by default, so we keep only the last N parsed tick records.
    for path in files:
        try:
            with path.open("r", encoding="utf-8") as file:
                for line_number, line in enumerate(file, start=1):
                    record = parse_tick_line(path, line, malformed)
                    if record is not None:
                        found.append((path, line_number, record))
        except OSError:
            continue

    return list(found)


def tick_id_for(tick: dict) -> int | None:
    value = tick.get("tickId")
    return value if isinstance(value, int) else None


def raw_count(value) -> int:
    return len(value) if isinstance(value, list) else 0


def raw_counts_for_tick(tick: dict) -> dict:
    deltas = tick.get("sceneObjectDeltas") if isinstance(tick.get("sceneObjectDeltas"), dict) else {}
    return {
        "npcs": raw_count(tick.get("npcs")),
        "players": raw_count(tick.get("players")),
        "sceneObjects": raw_count(tick.get("sceneObjects")),
        "visibleSceneObjectRefs": raw_count(tick.get("visibleSceneObjectRefs")),
        "groundItems": raw_count(tick.get("groundItems")),
        "sceneObjectDeltasNew": raw_count(deltas.get("newObjects")),
        "sceneObjectDeltasUpdated": raw_count(deltas.get("updatedObjects")),
        "sceneObjectDeltasDespawned": raw_count(deltas.get("despawnedObjects")),
    }


def retained_frame_path(session: Path, tick: dict) -> Path | None:
    frame_path = tick.get("framePath")
    if isinstance(frame_path, str) and frame_path:
        path = Path(frame_path)
        if not path.is_absolute():
            path = session / path
        if path.exists() and path.is_file():
            return path

    tick_id = tick_id_for(tick)
    if tick_id is None:
        return None

    frames_dir = session / "frames"
    for suffix in (".jpg", ".jpeg", ".png"):
        path = frames_dir / f"frame-tick-{tick_id:08d}{suffix}"
        if path.exists() and path.is_file():
            return path

    return None


def retained_frame_exists(session: Path, tick: dict) -> bool:
    return retained_frame_path(session, tick) is not None


def frame_summary_for_tick(session: Path, tick: dict) -> dict:
    frame_path = tick.get("framePath")
    resolved = retained_frame_path(session, tick)
    if not frame_path and resolved:
        try:
            frame_path = str(resolved.relative_to(session))
        except ValueError:
            frame_path = str(resolved)

    return {
        "path": frame_path,
        "exists": resolved is not None,
        "resolvedPath": str(resolved) if resolved else None,
        "width": tick.get("frameWidth"),
        "height": tick.get("frameHeight"),
        "captureStatus": tick.get("frameCaptureStatus"),
        "captureSource": tick.get("frameCaptureSource"),
    }


def latest_frame_tick(session: Path, ticks: list[dict]) -> tuple[int | None, str | None, list[int]]:
    frame_ticks = []
    latest_tick = None
    latest_path = None

    for tick in ticks:
        tick_id = tick_id_for(tick)
        path = retained_frame_path(session, tick)
        if tick_id is not None and path is not None:
            frame_ticks.append(tick_id)
            latest_tick = tick_id
            latest_path = str(path)

    return latest_tick, latest_path, frame_ticks


def source_schema_for_tick(tick: dict) -> str:
    if isinstance(tick.get("sceneIndexSummary"), dict) or isinstance(tick.get("visibleSceneObjectRefs"), list):
        return "staticIndexDelta"
    if isinstance(tick.get("sceneObjects"), list):
        return "fullSnapshot"
    return "unknown"


def source_scene_summary(ticks: list[dict]) -> dict:
    summaries = [tick.get("sceneCaptureSummary") for tick in ticks if isinstance(tick.get("sceneCaptureSummary"), dict)]
    index_summaries = [tick.get("sceneIndexSummary") for tick in ticks if isinstance(tick.get("sceneIndexSummary"), dict)]

    if not summaries and not index_summaries:
        return {
            "sourceSceneKnowledgeComplete": None,
            "sourceCapHit": None,
            "selectedTicksSkippedByCap": 0,
            "sceneObjectsSeen": None,
            "sceneObjectsCaptured": None,
            "sceneObjectsSkippedByCap": None,
            "staticSceneIndexObjectCount": None,
            "visibleSceneObjectRefsCount": sum(raw_count(tick.get("visibleSceneObjectRefs")) for tick in ticks),
        }

    cap_hit = any(summary.get("sceneObjectCapHit") is True for summary in summaries)
    skipped = sum(int(summary.get("sceneObjectsSkippedByCap") or 0) for summary in summaries)
    seen = sum(int(summary.get("sceneObjectsSeen") or 0) for summary in summaries)
    captured = sum(int(summary.get("sceneObjectsCaptured") or 0) for summary in summaries)
    skipped_ticks = sum(
        1
        for summary in summaries
        if summary.get("sceneObjectCapHit") is True or int(summary.get("sceneObjectsSkippedByCap") or 0) > 0
    )
    index_cap_hit = any(summary.get("indexCapHit") is True for summary in index_summaries)
    complete = not cap_hit and skipped == 0 and not index_cap_hit
    index_counts = [
        int(summary.get("indexObjectCount") or summary.get("presentObjectCount") or 0)
        for summary in index_summaries
        if summary.get("indexObjectCount") is not None or summary.get("presentObjectCount") is not None
    ]
    return {
        "sourceSceneKnowledgeComplete": complete,
        "sourceCapHit": cap_hit or index_cap_hit,
        "selectedTicksSkippedByCap": skipped_ticks,
        "sceneObjectsSeen": seen if summaries else None,
        "sceneObjectsCaptured": captured if summaries else None,
        "sceneObjectsSkippedByCap": skipped if summaries else None,
        "staticSceneIndexObjectCount": max(index_counts) if index_counts else None,
        "visibleSceneObjectRefsCount": sum(raw_count(tick.get("visibleSceneObjectRefs")) for tick in ticks),
    }


def selection_label(args) -> str:
    if args.latest_with_frames is not None:
        return f"latest-with-frames {args.latest_with_frames}"
    if args.latest is not None:
        return f"latest {args.latest}"
    return f"rolling-window {args.window_ticks}"


def selected_window_ticks(session: Path, tick_window: OrderedDict[int, dict], args) -> list[dict]:
    ticks = list(tick_window.values())

    if args.latest_with_frames is not None:
        return [tick for tick in ticks if retained_frame_exists(session, tick)][-args.latest_with_frames :]

    if args.latest is not None:
        return ticks[-args.latest :]

    return ticks


def decorate_live_record(record: dict, schema: str, processed_at: str) -> dict:
    decorated = dict(record)
    decorated["liveSchema"] = schema
    decorated["liveProcessedAtUtc"] = processed_at
    return decorated


def read_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    records = []
    warnings = []

    if not path.exists():
        return records, warnings

    try:
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    record = json.loads(text)
                except json.JSONDecodeError as error:
                    warnings.append(f"{path.name}:{line_number}: invalid JSON: {error.msg}")
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError as error:
        warnings.append(f"could not read {path}: {error}")

    return records, warnings


class UiTargetCache:
    def __init__(self, session: Path):
        self.session = session
        self.path = session / "interaction_geometry" / "ui_targets.jsonl"
        self.size = None
        self.mtime = None
        self.records_by_tick: dict[int, list[dict]] = {}
        self.warnings: list[str] = []

    def load_if_needed(self) -> None:
        size, mtime = stat_size(self.path)
        if size == self.size and mtime == self.mtime:
            return

        self.size = size
        self.mtime = mtime
        self.records_by_tick = {}
        self.warnings = []

        records, warnings = read_jsonl(self.path)
        self.warnings.extend(warnings)

        if not self.path.exists():
            self.warnings.append(f"UI targets unavailable: {self.path}")
            return

        for record in records:
            tick_id = record.get("tickId")
            if isinstance(tick_id, int):
                self.records_by_tick.setdefault(tick_id, []).append(record)

    def records_for_ticks(self, tick_ids: set[int]) -> tuple[list[dict], list[str]]:
        self.load_if_needed()
        records = []
        for tick_id in sorted(tick_ids):
            records.extend(self.records_by_tick.get(tick_id, []))
        return records, list(self.warnings)


class LiveTargetGeometryDataset:
    def __init__(self, session: Path, world_records: list[dict], ui_records: list[dict]):
        self.session = session
        self.messages: list[str] = []
        self.warnings: list[str] = []
        self.world_records = self._decorate(world_records, "world")
        self.ui_records = self._decorate(ui_records, "ui", start_index=len(self.world_records))
        self.records = self.world_records + self.ui_records

    def _decorate(self, records: list[dict], source_kind: str, start_index: int = 0) -> list[dict]:
        decorated = []
        for index, record in enumerate(records):
            value = dict(record)
            value["_sourceKind"] = source_kind
            value["_sourceIndex"] = index
            value["_inspector"] = {
                "sourceKind": source_kind,
                "sourceIndex": index,
                "globalIndex": start_index + index,
            }
            decorated.append(value)
        return decorated

    def ticks(self) -> list[int]:
        return sorted({record.get("tickId") for record in self.records if isinstance(record.get("tickId"), int)})

    def resolve_session_path(self, value: str) -> Path | None:
        path = Path(value)
        if not path.is_absolute():
            path = self.session / path
        try:
            resolved = path.resolve()
            resolved.relative_to(self.session.resolve())
        except (OSError, ValueError):
            return None
        return resolved

    def frame_exists_for_record(self, record: dict) -> bool:
        frame = geometry.frame_for(record)
        path = frame.get("path")
        if not path:
            return False
        resolved = self.resolve_session_path(str(path))
        return bool(resolved and resolved.exists() and resolved.is_file())


def candidate_args_from(args) -> SimpleNamespace:
    return SimpleNamespace(
        tick=None,
        tick_range=None,
        latest=None,
        target_type=args.target_type,
        role=None,
        category=None,
        tag=None,
        name=None,
        id=None,
        only_on_screen=False,
        geometry_available=False,
        exclude_ui_blocked=args.exclude_ui_blocked,
        profile=args.profile,
        target_library=str(Path(args.target_library).expanduser()),
        target_profiles=str(Path(args.target_profiles).expanduser()),
        limit=args.limit,
        no_limit=args.limit == 0,
        no_dedupe=False,
    )


def target_types_for_profile(args, profile: dict | None) -> set[str]:
    if args.target_type != "all":
        return {args.target_type} & WORLD_TARGET_TYPES

    if isinstance(profile, dict):
        values = {
            str(value)
            for value in profile.get("includeTargetTypes") or []
            if str(value) in WORLD_TARGET_TYPES
        }
        if values:
            return values

    return set(WORLD_TARGET_TYPES)


def object_cache_key(tick: dict, source: dict, target_type: str) -> tuple:
    if target_type == "sceneObject":
        return tuple(world_builder.scene_object_identity_key(tick, source))

    return (
        target_type,
        source.get("objectKey"),
        source.get("id"),
        source.get("hash"),
        source.get("worldX"),
        source.get("worldY"),
        source.get("plane"),
        source.get("sceneX"),
        source.get("sceneY"),
    )


def object_cache_fingerprint(source: dict) -> tuple:
    actions = source.get("actions")
    if isinstance(actions, list):
        actions_value = tuple(str(item) for item in actions if item is not None)
    else:
        actions_value = ()

    return (
        source.get("id"),
        source.get("hash"),
        source.get("kind"),
        source.get("objectName"),
        source.get("objectNameSource"),
        source.get("itemName"),
        source.get("itemNameSource"),
        source.get("npcName"),
        source.get("npcNameSource"),
        source.get("name"),
        actions_value,
        source.get("targetRole"),
        source.get("targetCategory"),
        tuple(str(tag) for tag in source.get("targetTags") or []),
    )


def profile_stable_match(record: dict, class_info: dict, profile: dict | None) -> bool:
    if not profile:
        return True

    include_ok, _include_reasons = candidate_builder.profile_include_match(record, class_info, profile)
    if not include_ok:
        return False

    class_ids = {str(class_id).lower() for class_id in class_info.get("targetClassIds") or []}
    target_role = geometry.target_role_for(record).lower()
    target_category = geometry.target_category_for(record).lower()

    if class_ids & candidate_builder.lower_set(profile.get("excludeTargetClasses")):
        return False
    if target_role in candidate_builder.lower_set(profile.get("excludeRoles")):
        return False
    if target_category in candidate_builder.lower_set(profile.get("excludeCategories")):
        return False

    return True


def dynamic_profile_reject(source: dict, profile: dict | None) -> str | None:
    if not profile:
        return None

    if profile.get("requireOnScreen") is True and world_builder.on_screen_value(source) is not True:
        return "requiresOnScreen"

    if profile.get("requireGeometryAvailable") is True and not world_builder.geometry_available(source):
        return "requiresGeometryAvailable"

    return None


def preview_scene_object_record(tick: dict, source: dict, overrides: dict) -> dict:
    target = world_builder.scene_object_target_payload(tick, source, overrides)
    return {
        "tickId": tick.get("tickId"),
        "target": target,
        "geometry": world_builder.geometry_payload(source),
    }


def build_scene_object_record(session_id: str, tick: dict, frame: dict, source: dict, overrides: dict) -> dict:
    target = world_builder.scene_object_target_payload(tick, source, overrides)
    return world_builder.base_target_record(
        session_id,
        tick,
        frame,
        target,
        world_builder.geometry_payload(source),
        world_builder.state_payload(source),
        world_builder.record_warnings(source),
    )


def build_world_records_for_tick(
    session_id: str,
    session: Path,
    tick: dict,
    target_types: set[str],
    overrides: dict,
    scene_object_filter=None,
) -> tuple[list[dict], dict]:
    frame = world_builder.frame_payload(session, tick, None)
    records = []
    stats = {
        "sourceRecordsConsidered": 0,
        "sourceRecordsPrefilteredOut": 0,
        "buildScope": "full",
    }

    if "npc" in target_types:
        records.extend(world_builder.npc_records(session_id, tick, frame, overrides))
    if "player" in target_types:
        records.extend(world_builder.player_records(session_id, tick, frame))
    if "sceneObject" in target_types:
        if scene_object_filter is None:
            sources = world_builder.scene_object_sources_for_tick(tick)
            stats["sourceRecordsConsidered"] += len(sources)
            records.extend(world_builder.scene_object_records(session_id, tick, frame, overrides))
        else:
            stats["buildScope"] = "profilePrefiltered"
            for source in world_builder.scene_object_sources_for_tick(tick):
                if not isinstance(source, dict):
                    continue
                stats["sourceRecordsConsidered"] += 1
                if not scene_object_filter(tick, source):
                    stats["sourceRecordsPrefilteredOut"] += 1
                    continue
                records.append(build_scene_object_record(session_id, tick, frame, source, overrides))
    if "groundItem" in target_types:
        records.extend(world_builder.ground_item_records(session_id, tick, frame, overrides))
    if "tile" in target_types:
        records.extend(world_builder.tile_records(session_id, tick, frame))

    stats["sourceRecordsBuilt"] = len(records)
    return records, stats


def world_args_from(args) -> SimpleNamespace:
    return SimpleNamespace(
        target_type=args.target_type,
        id=None,
        name=None,
        only_on_screen=False,
        include_off_screen=True,
    )


def filter_target_type(records: list[dict], args) -> list[dict]:
    world_args = world_args_from(args)
    return [record for record in records if world_builder.should_include_record(record, world_args)]


def profile_source_record(record: dict, library: dict, profile: dict | None) -> bool:
    if not profile:
        return True

    class_info = candidate_builder.classify_record(record, library)
    target_type = geometry.target_type_for(record).lower()
    target_role = geometry.target_role_for(record).lower()
    target_category = geometry.target_category_for(record).lower()
    class_ids = {str(value).lower() for value in class_info.get("targetClassIds") or []}

    exclude_classes = candidate_builder.lower_set(profile.get("excludeTargetClasses"))
    exclude_roles = candidate_builder.lower_set(profile.get("excludeRoles"))
    exclude_categories = candidate_builder.lower_set(profile.get("excludeCategories"))

    if class_ids & exclude_classes:
        return False
    if target_role in exclude_roles:
        return False
    if target_category in exclude_categories:
        return False

    include_classes = candidate_builder.lower_set(profile.get("includeTargetClasses"))
    include_types = candidate_builder.lower_set(profile.get("includeTargetTypes"))
    include_roles = candidate_builder.lower_set(profile.get("includeRoles"))
    include_categories = candidate_builder.lower_set(profile.get("includeCategories"))

    if not any((include_classes, include_types, include_roles, include_categories)):
        return True

    return bool(
        (include_classes and class_ids & include_classes)
        or (include_types and target_type in include_types)
        or (include_roles and target_role in include_roles)
        or (include_categories and target_category in include_categories)
    )


def limit_records(records: list[dict], limit: int) -> list[dict]:
    if limit == 0:
        return records
    return records[:limit]


def candidate_source_world_records(candidates: list[dict], source_records: list[dict]) -> list[dict]:
    selected_indexes = []
    seen = set()

    for candidate in candidates:
        source = candidate.get("sourceTarget") if isinstance(candidate.get("sourceTarget"), dict) else {}
        if source.get("sourceFileType") != "world":
            continue
        index = source.get("originalTargetRecordIndex")
        if not isinstance(index, int) or index < 0 or index >= len(source_records):
            continue
        if index in seen:
            continue
        seen.add(index)
        selected_indexes.append(index)

    return [source_records[index] for index in selected_indexes]


def load_profile_documents(args) -> tuple[dict, dict, dict | None, list[str]]:
    warnings = []
    library, library_warnings = candidate_builder.load_target_library(Path(args.target_library).expanduser())
    profiles, profile_warnings = candidate_builder.load_target_profiles(Path(args.target_profiles).expanduser())
    warnings.extend(library_warnings)
    warnings.extend(profile_warnings)
    profile = candidate_builder.profile_by_id(profiles, args.profile)

    if args.profile and profile is None:
        available = ", ".join(
            str(item.get("profileId"))
            for item in profiles.get("profiles") or []
            if isinstance(item, dict) and item.get("profileId")
        )
        raise RuntimeError(f"Unknown target profile: {args.profile}. Available profiles: {available or 'none'}")

    return library, profiles, profile, warnings


def rank_live_candidates(session: Path, ticks: list[dict], source_records: list[dict], ui_records: list[dict], args, library: dict, profile: dict | None) -> tuple[list[dict], dict, list[str]]:
    candidate_args = candidate_args_from(args)
    if candidate_args.limit is None and profile and not candidate_args.no_limit:
        default_limit = profile.get("defaultLimit")
        if isinstance(default_limit, int) and default_limit >= 0:
            candidate_args.limit = default_limit
    if candidate_args.limit is None:
        candidate_args.limit = 500

    dataset = LiveTargetGeometryDataset(session, source_records, ui_records)
    records, selected_ticks = candidate_builder.candidate_input_records(dataset, candidate_args)
    player_world_by_tick = {
        tick_id: player_world
        for tick in ticks
        for tick_id, player_world in [(tick_id_for(tick), candidate_builder.tick_player_world(tick))]
        if tick_id is not None and player_world is not None
    }
    ui_blockers = candidate_builder.ui_block_regions_by_tick(dataset)
    candidates, stats = candidate_builder.rank_candidates(dataset, records, candidate_args, player_world_by_tick, library, profile, ui_blockers)
    stats["matchingTargetsBeforeFilters"] = len(records)
    stats["selectedTicks"] = sorted(selected_ticks)
    warnings = list(dataset.messages) + list(dataset.warnings)
    return candidates, stats, warnings


def enrich_live_candidate(candidate: dict, processed_at: str) -> dict:
    enriched = decorate_live_record(candidate, LIVE_CANDIDATE_SCHEMA, processed_at)
    aim = enriched.get("aimPoint")
    geometry_payload = enriched.get("geometry") if isinstance(enriched.get("geometry"), dict) else {}
    preferred = geometry_payload.get("preferredAimGeometryType") or enriched.get("preferredGeometryType")
    x = aim.get("x") if isinstance(aim, dict) else None
    y = aim.get("y") if isinstance(aim, dict) else None
    enriched["schema"] = LIVE_CANDIDATE_SCHEMA
    enriched["targetClass"] = enriched.get("classId")
    enriched["preferredGeometryType"] = preferred
    enriched["distanceTiles"] = enriched.get("targetDistanceChebyshev")
    enriched["aimPointContext"] = {
        "canvasX": x,
        "canvasY": y,
        "source": preferred,
    } if isinstance(x, (int, float)) and isinstance(y, (int, float)) else None
    summary = enriched.get("geometrySummary") if isinstance(enriched.get("geometrySummary"), dict) else {}
    available = set(geometry_payload.get("availableGeometryTypes") or summary.get("availableGeometryTypes") or [])
    bounds = geometry_payload.get("aimBounds") or summary.get("aimBounds")
    enriched["geometrySummary"] = {
        **summary,
        "hasClickbox": bool({"clickboxPolygon", "clickboxBounds"} & available),
        "hasConvexHull": bool({"convexHullPolygon", "convexHullBounds"} & available),
        "hasCanvasTilePolygon": bool({"canvasTilePolygon", "tilePolygon"} & available),
        "bounds": bounds,
    }
    enriched["pathContext"] = {
        "directLineDistance": enriched.get("targetDistanceEuclidean"),
        "collisionKnown": False,
        "obviousBlocked": None,
    }
    return enriched


def counts_for_candidates(candidates: list[dict]) -> dict:
    quality = Counter(str(candidate.get("qualityTier") or "unknown") for candidate in candidates)
    class_ids = Counter(str(candidate.get("classId") or "unclassified") for candidate in candidates)
    categories = Counter(str(candidate.get("category") or "unknown") for candidate in candidates)
    target_types = Counter(str(candidate.get("targetType") or "unknown") for candidate in candidates)
    return {
        "qualityTier": dict(quality.most_common()),
        "classId": dict(class_ids.most_common()),
        "category": dict(categories.most_common()),
        "targetType": dict(target_types.most_common()),
    }


def best_candidate_summary(candidate: dict | None) -> dict | None:
    if not candidate:
        return None
    return {
        "rank": candidate.get("rank"),
        "score": candidate.get("score"),
        "qualityScore": candidate.get("qualityScore"),
        "qualityTier": candidate.get("qualityTier"),
        "classId": candidate.get("classId"),
        "targetType": candidate.get("targetType"),
        "name": candidate.get("name"),
        "id": candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id"),
        "worldX": candidate.get("worldX"),
        "worldY": candidate.get("worldY"),
        "plane": candidate.get("plane"),
        "distanceTiles": candidate.get("targetDistanceChebyshev"),
        "onScreen": candidate.get("onScreen"),
        "geometryAvailable": candidate.get("geometryAvailable"),
        "uiBlocked": candidate.get("uiBlocked"),
        "aimPoint": candidate.get("aimPoint"),
        "preferredGeometryType": candidate.get("preferredGeometryType"),
    }


def context_index_for(session: Path, args, ticks: list[dict], candidates: list[dict], processed_at: str) -> dict:
    paths = live_output_paths(session)
    counts = counts_for_candidates(candidates)
    by_class: dict[str, list[dict]] = {}
    for candidate in candidates:
        by_class.setdefault(str(candidate.get("classId") or "unclassified"), []).append(candidate)

    best_by_class = {}
    nearest_by_class = {}
    on_screen_counts = {}
    ui_blocked_counts = {}

    for class_id, values in by_class.items():
        best_by_class[class_id] = best_candidate_summary(values[0])
        with_distance = [value for value in values if isinstance(value.get("targetDistanceChebyshev"), int)]
        nearest = min(with_distance, key=lambda value: value.get("targetDistanceChebyshev")) if with_distance else values[0]
        nearest_by_class[class_id] = best_candidate_summary(nearest)
        on_screen_counts[class_id] = sum(1 for value in values if value.get("onScreen") is True)
        ui_blocked_counts[class_id] = sum(1 for value in values if value.get("uiBlocked") is True)

    query_hints = {
        "nearest tree": nearest_by_class.get("tree"),
        "nearest oak_tree": nearest_by_class.get("oak_tree"),
        "nearest willow_tree": nearest_by_class.get("willow_tree"),
        "nearest npc": nearest_by_class.get("npc"),
        "nearest ground_item": nearest_by_class.get("ground_item"),
    }
    tick_ids = [tick_id_for(tick) for tick in ticks if tick_id_for(tick) is not None]
    return {
        "schema": LIVE_CONTEXT_INDEX_SCHEMA,
        "generatedAtUtc": processed_at,
        "sessionPath": str(session),
        "baselineStatePath": str(paths["baseline"]),
        "liveCandidatesPath": str(paths["candidates"]),
        "liveWorldTargetsPath": str(paths["worldTargets"]) if args.emit_world_targets != "none" else None,
        "liveStatusPath": str(paths["status"]),
        "latestTick": max(tick_ids) if tick_ids else None,
        "tickRangeInWindow": [min(tick_ids), max(tick_ids)] if tick_ids else None,
        "activeProfile": args.profile,
        "candidateCountsByClassId": counts["classId"],
        "bestCandidateByClassId": best_by_class,
        "nearestCandidateByClassId": nearest_by_class,
        "onScreenCandidateCountsByClassId": on_screen_counts,
        "uiBlockedCountsByClassId": ui_blocked_counts,
        "queryHints": query_hints,
    }


def local_player_for(tick: dict) -> dict:
    value = tick.get("localPlayer")
    return value if isinstance(value, dict) else {}


def inventory_summary(tick: dict) -> dict:
    inventory = tick.get("inventory")
    items = inventory if isinstance(inventory, list) else inventory.get("items") if isinstance(inventory, dict) else []
    items = items if isinstance(items, list) else []
    filled = 0
    signature_parts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        quantity = item.get("quantity")
        if item_id not in (None, -1, 0):
            filled += 1
            signature_parts.append(f"{item_id}:{quantity}")
    return {
        "itemCount": filled,
        "freeSlots": max(0, 28 - filled) if items else None,
        "signature": "|".join(signature_parts) if signature_parts else None,
    }


def baseline_state_for(session: Path, args, latest_tick: dict | None, ticks: list[dict], candidates: list[dict], processed_at: str, duration_ms: float, budget_exceeded: bool) -> dict:
    latest_tick = latest_tick or {}
    player = local_player_for(latest_tick)
    capture = latest_tick.get("sceneCaptureSummary") if isinstance(latest_tick.get("sceneCaptureSummary"), dict) else {}
    index = latest_tick.get("sceneIndexSummary") if isinstance(latest_tick.get("sceneIndexSummary"), dict) else {}
    projection = latest_tick.get("sceneProjectionSummary") if isinstance(latest_tick.get("sceneProjectionSummary"), dict) else {}
    latest_frame, latest_frame_path, _frame_ticks = latest_frame_tick(session, ticks)
    counts = counts_for_candidates(candidates)
    return {
        "schema": LIVE_BASELINE_SCHEMA,
        "sessionPath": str(session),
        "generatedAtUtc": processed_at,
        "latestTick": tick_id_for(latest_tick),
        "latestFrameTick": latest_frame,
        "latestFramePath": latest_frame_path,
        "gameState": latest_tick.get("gameState"),
        "player": {
            "worldX": player.get("worldX"),
            "worldY": player.get("worldY"),
            "plane": player.get("plane"),
            "localX": player.get("localX"),
            "localY": player.get("localY"),
            "sceneX": player.get("sceneX"),
            "sceneY": player.get("sceneY"),
            "animation": player.get("animation"),
            "poseAnimation": player.get("poseAnimation"),
            "idlePoseAnimation": player.get("idlePoseAnimation"),
            "isMoving": player.get("isMoving"),
            "interacting": player.get("interacting"),
            "healthRatio": player.get("healthRatio"),
            "healthScale": player.get("healthScale"),
            "runEnergy": latest_tick.get("runEnergy"),
            "weight": latest_tick.get("weight"),
        },
        "cameraViewport": {
            "cameraX": latest_tick.get("cameraX"),
            "cameraY": latest_tick.get("cameraY"),
            "cameraZ": latest_tick.get("cameraZ"),
            "cameraPitch": latest_tick.get("cameraPitch"),
            "cameraYaw": latest_tick.get("cameraYaw"),
            "canvasWidth": latest_tick.get("canvasWidth"),
            "canvasHeight": latest_tick.get("canvasHeight"),
            "viewportWidth": latest_tick.get("viewportWidth"),
            "viewportHeight": latest_tick.get("viewportHeight"),
            "projectionStateHash": projection.get("projectionStateHash"),
        },
        "inventory": inventory_summary(latest_tick),
        "sceneCache": {
            "sceneCaptureMode": capture.get("sceneCaptureMode") or index.get("sceneCaptureMode"),
            "sceneIndexEnabled": index.get("indexEnabled"),
            "sourceObjectCount": capture.get("sceneObjectsSeen"),
            "presentObjectCount": index.get("presentObjectCount"),
            "visibleObjectCount": projection.get("visibleObjectCount"),
            "capHit": capture.get("sceneObjectCapHit") or index.get("indexCapHit"),
            "skippedByCap": capture.get("sceneObjectsSkippedByCap"),
        },
        "candidates": {
            "activeProfile": args.profile,
            "candidateCount": len(candidates),
            "bestCandidateSummary": best_candidate_summary(candidates[0] if candidates else None),
            "countsByClassId": counts["classId"],
            "countsByCategory": counts["category"],
            "countsByQualityTier": counts["qualityTier"],
        },
        "performance": {
            "lastUpdateDurationMillis": round(duration_ms, 3),
            "budgetExceeded": budget_exceeded,
        },
    }


def navigation_summary_for(tick: dict | None, processed_at: str) -> dict:
    tick = tick or {}
    player = local_player_for(tick)
    return {
        "schema": LIVE_NAVIGATION_SCHEMA,
        "generatedAtUtc": processed_at,
        "collisionKnown": False,
        "plane": player.get("plane") if player else tick.get("plane"),
        "playerSceneX": player.get("sceneX"),
        "playerSceneY": player.get("sceneY"),
        "mapBounds": None,
        "blockedMovementTileCount": None,
        "obstaclesKnown": False,
        "notes": [
            "Collision maps are not captured in the current read-only live context.",
            "Navigation profiles can use scene-object obstacle geometry now; collision-aware pathing needs a future read-only collision summary.",
        ],
    }


def tick_summaries_for(session: Path, ticks: list[dict], source_files: dict[int, str], world_counts: Counter, candidate_counts: Counter, durations: dict[int, float], processed_at: str) -> list[dict]:
    summaries = []

    for tick in ticks:
        tick_id = tick_id_for(tick)
        summaries.append(
            {
                "schemaVersion": LIVE_TICK_SUMMARY_SCHEMA,
                "tickId": tick_id,
                "timestampUtc": tick.get("timestampUtc"),
                "sourceTickFile": source_files.get(tick_id),
                "processedAtUtc": processed_at,
                "rawCounts": raw_counts_for_tick(tick),
                "worldTargetCount": world_counts.get(tick_id, 0),
                "candidateCount": candidate_counts.get(tick_id, 0),
                "frame": frame_summary_for_tick(session, tick),
                "sceneCaptureSummary": tick.get("sceneCaptureSummary"),
                "sceneIndexSummary": tick.get("sceneIndexSummary"),
                "sceneProjectionSummary": tick.get("sceneProjectionSummary"),
                "sourceSchema": source_schema_for_tick(tick),
                "processingDurationMillis": round(durations.get(tick_id, 0.0), 3) if tick_id is not None else None,
            }
        )

    return summaries


@dataclass
class ProcessedTick:
    tick: dict
    source_file: str
    all_world_records: list[dict]
    candidate_source_records: list[dict]
    build_duration_millis: float
    source_records_considered: int = 0
    source_records_prefiltered_out: int = 0
    build_scope: str = "full"


class LiveTargetProcessor:
    def __init__(self, session: Path, args):
        self.session = session
        self.args = args
        self.tailer = TickJsonlTailer(session)
        self.write_options = write_options_from(args)
        self.tick_window: OrderedDict[int, dict] = OrderedDict()
        self.processed_ticks: OrderedDict[int, ProcessedTick] = OrderedDict()
        self.source_files: dict[int, str] = {}
        self.target_overrides, self.override_warnings = world_builder.load_target_overrides()
        self.library, self.profiles, self.profile, self.profile_warnings = load_profile_documents(args)
        self.profile_doc_signature = self.current_profile_doc_signature()
        self.ui_cache = UiTargetCache(session)
        self.session_id = world_builder.session_id_for(session)
        self.total_ticks_seen = 0
        self.total_dropped_old_ticks = 0
        self.last_processed_new_ticks = 0
        self.last_dropped_old_ticks = 0
        self.last_full_window_rebuild = False
        self.last_rebuild_reason = "incremental"
        self.latest_raw_tick_seen = None
        self.last_processed_tick_ids: list[int] = []
        self.last_skipped_intermediate_tick_ids: list[int] = []
        self.last_coalesced_backlog_ticks = 0
        self.last_backlog_depth = 0
        self.last_source_records_considered = 0
        self.last_source_records_prefiltered_out = 0
        self.last_classification_cache_hits = 0
        self.last_classification_cache_misses = 0
        self.last_classification_cache_invalidations = 0
        self.last_candidate_tick_cache_hits = 0
        self.last_candidate_tick_cache_misses = 0
        self.last_old_ticks_dropped_from_candidate_cache = 0
        self.classification_cache: dict[tuple, dict] = {}
        self.total_write_retries = 0
        self.total_write_failures = 0

    def current_profile_doc_signature(self) -> tuple:
        paths = [
            Path(self.args.target_library).expanduser(),
            Path(self.args.target_profiles).expanduser(),
            world_builder.TARGET_NAME_OVERRIDES_PATH,
        ]
        return tuple((str(path), *stat_size(path)) for path in paths)

    def refresh_profile_documents_if_needed(self) -> None:
        signature = self.current_profile_doc_signature()
        if signature == self.profile_doc_signature:
            return

        invalidated = len(self.classification_cache)
        self.profile_doc_signature = signature
        self.target_overrides, self.override_warnings = world_builder.load_target_overrides()
        self.library, self.profiles, self.profile, self.profile_warnings = load_profile_documents(self.args)
        self.classification_cache.clear()
        self.processed_ticks.clear()
        self.last_classification_cache_invalidations += invalidated

    def memory_limit(self) -> int:
        selectors = [self.args.window_ticks]
        if self.args.latest:
            selectors.append(self.args.latest)
        if self.args.latest_with_frames:
            selectors.append(self.args.latest_with_frames)
        return max(selectors)

    def startup_backfill_limit(self) -> int:
        if self.args.no_startup_backfill:
            return 0
        if self.args.process_existing:
            return self.memory_limit()
        selected = self.args.startup_backfill_ticks
        if self.args.latest:
            selected = max(selected, self.args.latest)
        if self.args.latest_with_frames:
            selected = max(selected, self.args.latest_with_frames)
        return selected

    def initialize_from_existing(self) -> int:
        limit = self.startup_backfill_limit()
        malformed = Counter()
        records = tail_existing_records(self.tailer.files(), limit, malformed)
        for key, value in malformed.items():
            self.tailer.malformed_counts[key] += value
            self.tailer.malformed_total += value
        added, _dropped = self.add_ticks(records)
        self.tailer.seek_to_end()
        return added

    def add_ticks(self, records: list[tuple[Path, int, dict]]) -> tuple[int, int]:
        added = 0
        for path, _line_number, tick in records:
            tick_id = tick_id_for(tick)
            if tick_id is None:
                continue
            if self.latest_raw_tick_seen is None or tick_id > self.latest_raw_tick_seen:
                self.latest_raw_tick_seen = tick_id
            if tick_id not in self.tick_window:
                added += 1
                self.total_ticks_seen += 1
            else:
                self.processed_ticks.pop(tick_id, None)
            self.tick_window[tick_id] = tick
            self.tick_window.move_to_end(tick_id)
            self.source_files[tick_id] = str(path)

        limit = self.memory_limit()
        dropped = 0
        while len(self.tick_window) > limit:
            old_tick, _old_record = self.tick_window.popitem(last=False)
            self.source_files.pop(old_tick, None)
            self.processed_ticks.pop(old_tick, None)
            dropped += 1

        self.total_dropped_old_ticks += dropped
        self.last_dropped_old_ticks = dropped
        self.last_old_ticks_dropped_from_candidate_cache = dropped
        return added, dropped

    def selected_ticks(self) -> list[dict]:
        return selected_window_ticks(self.session, self.tick_window, self.args)

    def use_early_profile_prefilter(self) -> bool:
        if self.args.emit_world_targets in {"full", "visible"}:
            return False
        if self.args.profile == "broad_qa":
            return False
        return bool(self.profile)

    def scene_source_matches_profile(self, tick: dict, source: dict) -> bool:
        reject = dynamic_profile_reject(source, self.profile)
        if reject:
            return False

        key = object_cache_key(tick, source, "sceneObject")
        fingerprint = object_cache_fingerprint(source)
        cached = self.classification_cache.get(key)
        if cached and cached.get("fingerprint") == fingerprint:
            self.last_classification_cache_hits += 1
            return bool(cached.get("profileMatch"))

        self.last_classification_cache_misses += 1
        preview = preview_scene_object_record(tick, source, self.target_overrides)
        class_info = candidate_builder.classify_record(preview, self.library)
        profile_match = profile_stable_match(preview, class_info, self.profile)
        self.classification_cache[key] = {
            "fingerprint": fingerprint,
            "profileMatch": profile_match,
            "classId": class_info.get("classId"),
            "targetClassIds": class_info.get("targetClassIds") or [],
            "id": source.get("id"),
            "hash": source.get("hash"),
            "kind": source.get("kind"),
            "name": geometry.target_name_for(preview),
            "category": geometry.target_category_for(preview),
            "role": geometry.target_role_for(preview),
            "objectKey": source.get("objectKey"),
            "worldX": source.get("worldX"),
            "worldY": source.get("worldY"),
            "plane": source.get("plane"),
            "sceneX": source.get("sceneX"),
            "sceneY": source.get("sceneY"),
        }
        return profile_match

    def processing_ticks_for(self, selected_ticks: list[dict], force: bool) -> list[dict]:
        self.last_skipped_intermediate_tick_ids = []
        self.last_coalesced_backlog_ticks = 0
        self.last_backlog_depth = 0

        if self.args.latency_mode != "realtime" or force or not self.args.drop_backlog_to_meet_budget:
            return selected_ticks

        max_new = self.args.max_new_ticks_per_update
        if max_new <= 0 or len(selected_ticks) <= max_new:
            return selected_ticks

        processing = selected_ticks[-max_new:]
        processing_ids = {tick_id_for(tick) for tick in processing}
        skipped_ids = [
            tick_id_for(tick)
            for tick in selected_ticks
            if tick_id_for(tick) is not None and tick_id_for(tick) not in processing_ids
        ]
        self.last_skipped_intermediate_tick_ids = skipped_ids
        self.last_coalesced_backlog_ticks = len(skipped_ids)
        self.last_backlog_depth = len(selected_ticks) - len(processing)
        return processing

    def output_ticks_for(self, selected_ticks: list[dict], processed_now: list[ProcessedTick]) -> list[dict]:
        if self.args.candidate_output_window == "rolling":
            return [
                tick
                for tick in selected_ticks
                if tick_id_for(tick) in self.processed_ticks
            ]

        if processed_now:
            latest_tick = max(
                (processed.tick for processed in processed_now),
                key=lambda tick: tick_id_for(tick) if tick_id_for(tick) is not None else -1,
            )
            return [latest_tick]

        selected_with_cache = [tick for tick in selected_ticks if tick_id_for(tick) in self.processed_ticks]
        return selected_with_cache[-1:]

    def process_tick(self, tick_id: int, tick: dict, force: bool, timing: Timing) -> ProcessedTick:
        cached = self.processed_ticks.get(tick_id)
        if cached is not None and not force:
            self.last_candidate_tick_cache_hits += 1
            return cached
        self.last_candidate_tick_cache_misses += 1

        target_types = target_types_for_profile(self.args, self.profile)
        if self.args.emit_world_targets in {"full", "visible"}:
            target_types = WORLD_TARGET_TYPES if self.args.target_type == "all" else target_types

        started = time.perf_counter()
        with timing.measure("worldTargetBuildMillis"):
            scene_filter = self.scene_source_matches_profile if self.use_early_profile_prefilter() and "sceneObject" in target_types else None
            built, build_stats = build_world_records_for_tick(
                self.session_id,
                self.session,
                tick,
                target_types,
                self.target_overrides,
                scene_object_filter=scene_filter,
            )
            built = filter_target_type(built, self.args)
        build_ms = (time.perf_counter() - started) * 1000.0

        with timing.measure("worldTargetFilterMillis"):
            if self.use_early_profile_prefilter():
                profile_records = built
            else:
                profile_records = [record for record in built if profile_source_record(record, self.library, self.profile)]

        processed = ProcessedTick(
            tick=tick,
            source_file=self.source_files.get(tick_id, ""),
            all_world_records=built,
            candidate_source_records=profile_records,
            build_duration_millis=build_ms,
            source_records_considered=int(build_stats.get("sourceRecordsConsidered") or 0),
            source_records_prefiltered_out=int(build_stats.get("sourceRecordsPrefilteredOut") or 0),
            build_scope=str(build_stats.get("buildScope") or "full"),
        )
        self.processed_ticks[tick_id] = processed
        self.processed_ticks.move_to_end(tick_id)
        return processed

    def process_selected_ticks(self, selected_ticks: list[dict], force: bool, timing: Timing) -> tuple[list[ProcessedTick], int]:
        processed = []
        new_count = 0
        self.last_candidate_tick_cache_hits = 0
        self.last_candidate_tick_cache_misses = 0
        self.last_classification_cache_hits = 0
        self.last_classification_cache_misses = 0
        self.last_source_records_considered = 0
        self.last_source_records_prefiltered_out = 0
        for tick in selected_ticks:
            tick_id = tick_id_for(tick)
            if tick_id is None:
                continue
            was_cached = tick_id in self.processed_ticks and not force
            processed_tick = self.process_tick(tick_id, tick, force, timing)
            processed.append(processed_tick)
            if not was_cached:
                new_count += 1
            self.last_source_records_considered += processed_tick.source_records_considered
            self.last_source_records_prefiltered_out += processed_tick.source_records_prefiltered_out
        self.last_processed_new_ticks = new_count
        self.last_processed_tick_ids = [tick_id_for(item.tick) for item in processed if tick_id_for(item.tick) is not None]
        return processed, new_count

    def output_world_records(self, candidates: list[dict], source_records: list[dict], full_records: list[dict]) -> list[dict]:
        mode = self.args.emit_world_targets
        if mode == "none":
            return []
        if mode == "full":
            return limit_records(full_records, self.args.world_target_output_limit)
        if mode == "visible":
            visible = [record for record in full_records if geometry.on_screen_for(record) is True]
            return limit_records(visible, self.args.world_target_output_limit)
        if mode == "profile":
            return limit_records(source_records, self.args.world_target_output_limit)
        return limit_records(candidate_source_world_records(candidates, source_records), self.args.world_target_output_limit)

    def process_window(self, force_rebuild: bool = False, rebuild_reason: str = "incremental") -> dict:
        total_started = time.perf_counter()
        processed_at = utc_now()
        timing = Timing()
        self.last_classification_cache_invalidations = 0
        self.refresh_profile_documents_if_needed()
        warnings = list(self.override_warnings) + list(self.profile_warnings)
        selected_ticks = self.selected_ticks()
        selected_tick_ids = [tick_id_for(tick) for tick in selected_ticks if tick_id_for(tick) is not None]
        processing_ticks = self.processing_ticks_for(selected_ticks, force_rebuild)
        self.last_full_window_rebuild = bool(force_rebuild)
        self.last_rebuild_reason = rebuild_reason if force_rebuild else "incremental"

        with timing.measure("rawTickIngestMillis"):
            processed_now, new_count = self.process_selected_ticks(processing_ticks, force_rebuild, timing)

        output_ticks = self.output_ticks_for(selected_ticks, processed_now)
        output_tick_ids = {tick_id_for(tick) for tick in output_ticks if tick_id_for(tick) is not None}
        processed_ticks = [
            self.processed_ticks[tick_id]
            for tick_id in sorted(output_tick_ids)
            if tick_id in self.processed_ticks
        ]
        if self.args.latency_mode == "complete":
            self.last_skipped_intermediate_tick_ids = []
            self.last_coalesced_backlog_ticks = 0
            self.last_backlog_depth = 0

        source_records = []
        full_records = []
        build_durations = {}
        for processed_tick in processed_ticks:
            tick_id = tick_id_for(processed_tick.tick)
            source_records.extend(processed_tick.candidate_source_records)
            full_records.extend(processed_tick.all_world_records)
            if tick_id is not None:
                build_durations[tick_id] = processed_tick.build_duration_millis

        tick_ids = set(output_tick_ids)
        ui_records = []
        ui_warnings = []
        with timing.measure("uiTargetLoadMillis"):
            if self.args.include_ui_targets:
                ui_records, ui_warnings = self.ui_cache.records_for_ticks(tick_ids)
            elif self.args.exclude_ui_blocked:
                ui_warnings.append("UI-blocked filtering requested but UI targets are disabled or unavailable; treating UI blocking as unknown/false.")
        warnings.extend(ui_warnings)
        ui_records = [decorate_live_record(record, LIVE_UI_TARGET_SCHEMA, processed_at) for record in ui_records]

        with timing.measure("candidateSelectMillis"):
            candidates, candidate_stats, candidate_warnings = rank_live_candidates(
                self.session,
                selected_ticks,
                source_records,
                ui_records,
                self.args,
                self.library,
                self.profile,
            )
        warnings.extend(candidate_warnings)
        candidates = [enrich_live_candidate(candidate, processed_at) for candidate in candidates]

        with timing.measure("worldTargetFilterMillis"):
            world_output_records = self.output_world_records(candidates, source_records, full_records)
            world_output_records = [decorate_live_record(record, LIVE_WORLD_TARGET_SCHEMA, processed_at) for record in world_output_records]

        world_counts = Counter(record.get("tickId") for record in world_output_records)
        candidate_counts = Counter(candidate.get("tickId") for candidate in candidates)
        tick_summaries = tick_summaries_for(self.session, output_ticks, self.source_files, world_counts, candidate_counts, build_durations, processed_at)
        latest_tick_record = output_ticks[-1] if output_ticks else (selected_ticks[-1] if selected_ticks else (next(reversed(self.tick_window.values())) if self.tick_window else None))

        total_duration_ms = (time.perf_counter() - total_started) * 1000.0
        budget_exceeded = total_duration_ms > self.args.target_update_ms
        warning_exceeded = total_duration_ms > self.args.warn_update_ms

        with timing.measure("baselineStateMillis"):
            baseline = baseline_state_for(self.session, self.args, latest_tick_record, selected_ticks, candidates, processed_at, total_duration_ms, budget_exceeded)

        with timing.measure("contextIndexMillis"):
            context_index = context_index_for(self.session, self.args, selected_ticks, candidates, processed_at)
            navigation = navigation_summary_for(latest_tick_record, processed_at)

        source_summary = source_scene_summary(selected_ticks)
        latest_frame, latest_frame_path, frame_ticks = latest_frame_tick(self.session, output_ticks or selected_ticks)
        selected_tick_has_frame = bool(latest_tick_record and retained_frame_exists(self.session, latest_tick_record))
        paths = live_output_paths(self.session)

        status = self.status_payload(
            output_ticks,
            source_records,
            full_records,
            world_output_records,
            ui_records,
            candidates,
            candidate_stats,
            warnings,
            processed_at,
            total_duration_ms,
            budget_exceeded,
            warning_exceeded,
            timing,
            source_summary,
            latest_frame,
            latest_frame_path,
            frame_ticks,
            selected_tick_has_frame,
            new_count,
            selected_tick_ids,
        )
        index = self.index_payload(output_ticks, world_output_records, ui_records, candidates, candidate_stats, processed_at, frame_ticks)

        output_bytes = {}
        write_stats = WriteStats()
        with timing.measure("outputWriteMillis"):
            if self.args.emit_world_targets == "none":
                output_bytes["worldTargets"] = remove_file_if_exists(paths["worldTargets"])
            else:
                output_bytes["worldTargets"] = atomic_write_jsonl(paths["worldTargets"], world_output_records, options=self.write_options, stats=write_stats)
            output_bytes["uiTargets"] = atomic_write_jsonl(paths["uiTargets"], ui_records, options=self.write_options, stats=write_stats)
            output_bytes["candidates"] = atomic_write_jsonl(paths["candidates"], candidates, options=self.write_options, stats=write_stats)
            output_bytes["tickSummary"] = atomic_write_jsonl(paths["tickSummary"], tick_summaries, options=self.write_options, stats=write_stats)
            output_bytes["baseline"] = atomic_write_json(paths["baseline"], baseline, options=self.write_options, stats=write_stats)
            output_bytes["contextIndex"] = atomic_write_json(paths["contextIndex"], context_index, options=self.write_options, stats=write_stats)
            output_bytes["navigation"] = atomic_write_json(paths["navigation"], navigation, options=self.write_options, stats=write_stats)
            output_bytes["index"] = atomic_write_json(paths["index"], index, options=self.write_options, stats=write_stats)
            output_bytes["status"] = atomic_write_json(paths["status"], status, options=self.write_options, stats=write_stats)

        self.total_write_retries += write_stats.retry_count
        self.total_write_failures += write_stats.failure_count
        status["timingBreakdownMillis"] = timing_payload(timing, total_duration_ms, self.tailer)
        status["outputBytes"] = {
            "outputBytesWorldTargets": output_bytes.get("worldTargets", 0),
            "outputBytesCandidates": output_bytes.get("candidates", 0),
            "outputBytesBaseline": output_bytes.get("baseline", 0),
            "outputBytesStatus": output_bytes.get("status", 0),
            "outputBytesIndex": output_bytes.get("contextIndex", 0),
            "outputBytesTotal": sum(output_bytes.values()),
        }
        status["writeRetryCount"] = write_stats.retry_count
        status["writeFailureCount"] = write_stats.failure_count
        status["cumulativeWriteRetryCount"] = self.total_write_retries
        status["cumulativeWriteFailureCount"] = self.total_write_failures
        status["lastWriteError"] = write_stats.last_error
        status["lastWriteErrorPath"] = write_stats.last_error_path
        status["lastWriteErrorUtc"] = write_stats.last_error_utc
        status["lastSuccessfulWriteUtc"] = write_stats.last_successful_write_utc
        status["budgetExceeded"] = budget_exceeded
        status["warningUpdateExceeded"] = warning_exceeded
        if write_stats.retry_count or write_stats.failure_count:
            status.setdefault("warnings", []).append(
                f"live output write retries={write_stats.retry_count}, failures={write_stats.failure_count}"
            )
            status["warningCount"] = len(status["warnings"])
        if budget_exceeded:
            status.setdefault("warnings", []).append(f"target update budget exceeded: {total_duration_ms:.1f} ms > {self.args.target_update_ms} ms")
            status["warningCount"] = len(status["warnings"])
        if warning_exceeded and not budget_exceeded:
            status.setdefault("warnings", []).append(f"update warning threshold exceeded: {total_duration_ms:.1f} ms > {self.args.warn_update_ms} ms")
            status["warningCount"] = len(status["warnings"])
        before_status_failures = write_stats.failure_count
        status_size = atomic_write_json(paths["status"], status, options=self.write_options, stats=write_stats)
        if status_size:
            output_bytes["status"] = status_size
        if write_stats.retry_count or write_stats.failure_count:
            self.total_write_retries += write_stats.retry_count - status.get("writeRetryCount", 0)
            self.total_write_failures += write_stats.failure_count - status.get("writeFailureCount", 0)
        if write_stats.failure_count > before_status_failures:
            print(f"Warning: could not refresh live_status.json after retries: {write_stats.last_error}")

        return {
            "worldRecords": world_output_records,
            "uiRecords": ui_records,
            "candidates": candidates,
            "tickSummaries": tick_summaries,
            "baseline": baseline,
            "contextIndex": context_index,
            "navigation": navigation,
            "status": status,
            "index": index,
        }

    def status_payload(
        self,
        selected_ticks: list[dict],
        source_records: list[dict],
        full_records: list[dict],
        world_output_records: list[dict],
        ui_records: list[dict],
        candidates: list[dict],
        candidate_stats: dict,
        warnings: list[str],
        processed_at: str,
        duration_ms: float,
        budget_exceeded: bool,
        warning_exceeded: bool,
        timing: Timing,
        source_summary: dict,
        latest_frame: int | None,
        latest_frame_path: str | None,
        frame_ticks: list[int],
        selected_tick_has_frame: bool,
        processed_new_ticks: int,
        selected_tick_ids: list[int],
    ) -> dict:
        output_ids = [tick_id_for(tick) for tick in selected_ticks if tick_id_for(tick) is not None]
        window_ids = list(self.tick_window.keys())
        counts = counts_for_candidates(candidates)
        suppressed = max(0, len(full_records) - len(world_output_records))
        frame_index = self.session / "frames" / "frame_index.jsonl"
        skipped_ids = list(self.last_skipped_intermediate_tick_ids)
        return {
            "schema": LIVE_STATUS_SCHEMA,
            "generatedAtUtc": processed_at,
            "sessionPath": str(self.session),
            "profile": self.args.profile,
            "profileId": self.args.profile,
            "targetType": self.args.target_type,
            "mode": "follow" if self.args.follow else "once",
            "latencyMode": self.args.latency_mode,
            "candidateOutputWindow": self.args.candidate_output_window,
            "maxNewTicksPerUpdate": self.args.max_new_ticks_per_update,
            "dropBacklogToMeetBudget": bool(self.args.drop_backlog_to_meet_budget),
            "selection": selection_label(self.args),
            "includeUiTargets": bool(self.args.include_ui_targets),
            "excludeUiBlocked": bool(self.args.exclude_ui_blocked),
            "emitWorldTargetsMode": self.args.emit_world_targets,
            "worldTargetOutputLimit": self.args.world_target_output_limit,
            "worldTargetsBuilt": len(full_records),
            "worldTargetsWritten": len(world_output_records),
            "worldTargetsSuppressed": suppressed,
            "worldTargetBuildScope": "profilePrefiltered" if self.last_source_records_prefiltered_out else "full",
            "worldTargetSourceRecordsConsidered": self.last_source_records_considered,
            "worldTargetsPrefilteredOut": self.last_source_records_prefiltered_out,
            "fullWorldTargetOutputEnabled": self.args.emit_world_targets == "full",
            **source_summary,
            "windowTicks": self.args.window_ticks,
            "lastProcessedTick": max(output_ids) if output_ids else None,
            "latestRawTickSeen": self.latest_raw_tick_seen,
            "latestTickProcessed": max(self.last_processed_tick_ids) if self.last_processed_tick_ids else None,
            "processedTickIds": self.last_processed_tick_ids,
            "coalescedBacklogTicks": self.last_coalesced_backlog_ticks,
            "skippedIntermediateTickIds": skipped_ids if len(skipped_ids) <= 25 else [],
            "skippedIntermediateTickCount": len(skipped_ids),
            "skippedIntermediateTickIdsTruncated": len(skipped_ids) > 25,
            "backlogDepth": self.last_backlog_depth,
            "liveFreshnessMillis": freshness_millis_for(selected_ticks),
            "latestTick": max(window_ids) if window_ids else None,
            "tickRangeInWindow": [min(window_ids), max(window_ids)] if window_ids else None,
            "selectedTickRange": [min(output_ids), max(output_ids)] if output_ids else None,
            "selectedTickCount": len(output_ids),
            "rawSelectedTickRange": [min(selected_tick_ids), max(selected_tick_ids)] if selected_tick_ids else None,
            "rawSelectedTickCount": len(selected_tick_ids),
            "latestFrameTick": latest_frame,
            "latestFramePath": latest_frame_path,
            "frameIndexExists": frame_index.exists(),
            "frameTicksInWindow": frame_ticks,
            "selectedTickHasFrame": selected_tick_has_frame,
            "filesWatched": [str(path) for path in self.tailer.files()],
            "malformedLineCount": self.tailer.malformed_total,
            "malformedCountsByFile": dict(self.tailer.malformed_counts),
            "partialLineFiles": self.tailer.partial_line_files(),
            "readErrors": self.tailer.read_errors[-20:],
            "totalTicksInMemory": len(self.tick_window),
            "worldTargetCount": len(world_output_records),
            "uiTargetCount": len(ui_records),
            "candidateCount": len(candidates),
            "candidateCountsByQualityTier": counts["qualityTier"],
            "candidateCountsByClassId": counts["classId"],
            "candidateCountsByCategory": counts["category"],
            "candidateCountsByTargetType": counts["targetType"],
            "candidateStats": candidate_stats,
            "targetUpdateMillis": self.args.target_update_ms,
            "warnUpdateMillis": self.args.warn_update_ms,
            "budgetExceeded": budget_exceeded,
            "warningUpdateExceeded": warning_exceeded,
            "processedNewTicks": processed_new_ticks,
            "reusedTicks": max(0, len(selected_ticks) - processed_new_ticks),
            "droppedOldTicks": self.last_dropped_old_ticks,
            "classificationCacheSize": len(self.classification_cache),
            "classificationCacheHits": self.last_classification_cache_hits,
            "classificationCacheMisses": self.last_classification_cache_misses,
            "classificationCacheInvalidations": self.last_classification_cache_invalidations,
            "candidateTickCacheSize": len(self.processed_ticks),
            "candidateTickCacheHits": self.last_candidate_tick_cache_hits,
            "candidateTickCacheMisses": self.last_candidate_tick_cache_misses,
            "oldTicksDroppedFromCandidateCache": self.last_old_ticks_dropped_from_candidate_cache,
            "fullWindowRebuild": self.last_full_window_rebuild,
            "rebuildReason": self.last_rebuild_reason,
            "processingDurationMillis": round(duration_ms, 3),
            "timingMode": TIMING_MODE,
            "timingBreakdownMillis": timing_payload(timing, duration_ms, self.tailer),
            "warnings": warnings[:100],
            "warningCount": len(warnings),
        }

    def index_payload(self, selected_ticks: list[dict], world_records: list[dict], ui_records: list[dict], candidates: list[dict], candidate_stats: dict, processed_at: str, frame_ticks: list[int]) -> dict:
        paths = live_output_paths(self.session)
        selected_ids = [tick_id_for(tick) for tick in selected_ticks if tick_id_for(tick) is not None]
        tick_to_frame = {}
        for tick in selected_ticks:
            tick_id = tick_id_for(tick)
            frame = frame_summary_for_tick(self.session, tick)
            if tick_id is not None and frame.get("path"):
                tick_to_frame[str(tick_id)] = frame
        return {
            "schema": LIVE_INDEX_SCHEMA,
            "generatedAtUtc": processed_at,
            "sessionPath": str(self.session),
            "profile": self.args.profile,
            "profileId": self.args.profile,
            "targetType": self.args.target_type,
            "targetLibraryPath": str(Path(self.args.target_library).expanduser()),
            "targetProfilesPath": str(Path(self.args.target_profiles).expanduser()),
            "tickRange": [min(selected_ids), max(selected_ids)] if selected_ids else None,
            "selectedTickCount": len(selected_ids),
            "worldTargetRecordCount": len(world_records),
            "uiTargetRecordCount": len(ui_records),
            "candidateRecordCount": len(candidates),
            "emitWorldTargetsMode": self.args.emit_world_targets,
            "candidateStats": candidate_stats,
            "frameIndexPath": str(self.session / "frames" / "frame_index.jsonl"),
            "frameDir": str(self.session / "frames"),
            "frameTicksInWindow": frame_ticks,
            "tickToFrame": tick_to_frame,
            "paths": {
                "liveWorldTargets": str(paths["worldTargets"]) if self.args.emit_world_targets != "none" else None,
                "liveUiTargets": str(paths["uiTargets"]),
                "liveCandidates": str(paths["candidates"]),
                "liveTickSummary": str(paths["tickSummary"]),
                "liveBaselineState": str(paths["baseline"]),
                "liveContextIndex": str(paths["contextIndex"]),
                "liveNavigationSummary": str(paths["navigation"]),
                "liveStatus": str(paths["status"]),
            },
        }

    def poll_new_records(self, force_rebuild: bool = False) -> tuple[int, dict]:
        records = self.tailer.read_new_records()
        added, dropped = self.add_ticks(records)
        result = self.process_window(force_rebuild=force_rebuild, rebuild_reason="force-window-rebuild" if force_rebuild else "incremental")
        result["status"]["droppedOldTicks"] = dropped
        return added, result

    def poll_once(self) -> tuple[int, dict]:
        return self.poll_new_records(force_rebuild=self.args.force_window_rebuild)


def print_startup(session: Path, args) -> None:
    print("Live target processor")
    print(f"session: {session}")
    print(f"profile: {args.profile}")
    print(f"mode: {'follow' if args.follow else 'once'}")
    print(f"latency mode: {args.latency_mode}")
    print(f"candidate output window: {args.candidate_output_window}")
    print(f"window ticks: {args.window_ticks}")
    print(f"selection: {selection_label(args)}")
    print(f"emit world targets: {args.emit_world_targets}")
    print(f"include UI targets: {str(bool(args.include_ui_targets)).lower()}")
    print(f"output: {live_output_dir(session)}")


def print_result_summary(result: dict, tailer: TickJsonlTailer) -> None:
    status = result["status"]
    timing = status.get("timingBreakdownMillis") or {}
    output = status.get("outputBytes") or {}
    print(f"processed ticks: {status['selectedTickCount']}")
    print(f"latest tick: {status['lastProcessedTick']}")
    print(f"latest raw tick seen: {status.get('latestRawTickSeen')}")
    print(f"coalesced backlog ticks: {status.get('coalescedBacklogTicks', 0)}")
    print(f"world targets built: {status['worldTargetsBuilt']}")
    print(f"world targets written: {status['worldTargetsWritten']}")
    print(f"world targets suppressed: {status['worldTargetsSuppressed']}")
    print(f"candidates in window: {status['candidateCount']}")
    print(f"processing duration: {status['processingDurationMillis']} ms")
    print(f"candidate selection: {timing.get('candidateSelectMillis', 0)} ms")
    print(f"output bytes total: {output.get('outputBytesTotal', 0)}")
    print(f"budget exceeded: {str(bool(status.get('budgetExceeded'))).lower()}")
    if status.get("writeRetryCount") or status.get("writeFailureCount"):
        print(f"write retries: {status.get('writeRetryCount', 0)}")
        print(f"write failures: {status.get('writeFailureCount', 0)}")
    print(f"malformed lines: {tailer.malformed_total}")
    print(f"output: {live_output_paths(Path(status['sessionPath']))['status']}")


def print_follow_update(added: int, result: dict) -> None:
    status = result["status"]
    timing = status.get("timingBreakdownMillis") or {}
    print(
        f"latestTick={status['lastProcessedTick']} "
        f"newTicks={added} "
        f"coalesced={status.get('coalescedBacklogTicks', 0)} "
        f"worldBuilt={status['worldTargetsBuilt']} "
        f"worldWritten={status['worldTargetsWritten']} "
        f"candidates={status['candidateCount']} "
        f"baselineMs={timing.get('baselineStateMillis', 0)} "
        f"candidateMs={timing.get('candidateSelectMillis', 0)} "
        f"writeMs={timing.get('outputWriteMillis', 0)} "
        f"totalMs={status['processingDurationMillis']} "
        f"budgetExceeded={str(bool(status.get('budgetExceeded'))).lower()} "
        f"writeRetries={status.get('writeRetryCount', 0)} "
        f"writeFailures={status.get('writeFailureCount', 0)}"
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Tail raw tick JSONL files and write rolling read-only target context/candidate outputs. "
            "This does not interact with RuneLite or generate actions."
        )
    )
    parser.add_argument("--session", help="Explicit telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions directory when --latest-session is used.")
    parser.add_argument("--latest-session", action="store_true", help="Use the newest available session when --session is omitted.")
    parser.add_argument(
        "--profile",
        default="broad_qa",
        choices=["broad_qa", "woodcutting", "navigation_qa", "npc_qa", "ground_item_qa", "ui_qa"],
        help="Candidate profile. Default: broad_qa.",
    )
    parser.add_argument("--target-type", default="all", choices=sorted(geometry.TARGET_TYPES), help="Target type filter. Default: all.")
    parser.add_argument("--limit", type=non_negative_int, default=500, help="Candidate limit. Use 0 for no limit. Default: 500.")
    parser.add_argument("--window-ticks", type=positive_int, default=100, help="Rolling tick window size. Default: 100.")
    parser.add_argument("--poll-interval", type=positive_float, default=0.5, help="Follow-mode poll interval in seconds. Default: 0.5.")
    parser.add_argument("--once", action="store_true", help="Process currently available tick records and exit.")
    parser.add_argument("--follow", action="store_true", help="Keep watching for new tick records until Ctrl+C or --max-runtime-seconds.")
    parser.add_argument("--latency-mode", choices=sorted(LATENCY_MODES), default="realtime", help="Realtime coalesces backlog for freshness; complete processes every tick. Default: realtime.")
    parser.add_argument("--max-new-ticks-per-update", type=non_negative_int, help="Maximum newest ticks to process per realtime update. Default: 1 in realtime, unlimited in complete.")
    parser.add_argument("--candidate-output-window", choices=sorted(CANDIDATE_OUTPUT_WINDOWS), help="Write latest candidates only or rolling cached candidates. Default: latest in realtime, rolling in complete.")
    parser.add_argument("--drop-backlog-to-meet-budget", dest="drop_backlog_to_meet_budget", action="store_true", default=None, help="Coalesce intermediate ticks when realtime backlog exceeds the per-update limit.")
    parser.add_argument("--no-drop-backlog-to-meet-budget", dest="drop_backlog_to_meet_budget", action="store_false", help="Do not coalesce intermediate ticks for latency.")
    parser.add_argument("--include-ui-targets", action="store_true", help="Include existing batch ui_targets.jsonl records for ticks in the rolling window.")
    parser.add_argument("--no-ui-targets", action="store_true", help="Disable UI target loading even if live UI files exist.")
    parser.add_argument("--latest", type=positive_int, metavar="N", help="Select latest N ticks from the rolling window.")
    parser.add_argument("--latest-with-frames", type=positive_int, metavar="N", help="Select latest N ticks with retained frame files from the rolling window.")
    parser.add_argument("--exclude-ui-blocked", action="store_true", help="Exclude candidates whose aim point intersects known UI targets.")
    parser.add_argument("--emit-world-targets", choices=sorted(WORLD_TARGET_EMIT_MODES), default="candidates", help="Live world target output policy. Default: candidates.")
    parser.add_argument("--world-target-output-limit", type=non_negative_int, default=2000, help="Max live world target records to write. Use 0 for unlimited. Default: 2000.")
    parser.add_argument("--target-update-ms", type=positive_float, default=100.0, help="Target update budget in milliseconds. Default: 100.")
    parser.add_argument("--warn-update-ms", type=positive_float, default=250.0, help="Warning update threshold in milliseconds. Default: 250.")
    parser.add_argument("--benchmark", action="store_true", help="Print timing and output-size summary fields.")
    parser.add_argument("--force-window-rebuild", action="store_true", help="Rebuild cached world records for every selected tick on each update.")
    parser.add_argument("--startup-backfill-ticks", type=non_negative_int, default=10, help="Initial existing tick catch-up count. Default: 10.")
    parser.add_argument("--no-startup-backfill", action="store_true", help="Start from current end of tick files and wait for new records.")
    parser.add_argument("--process-existing", action="store_true", help="Process the current rolling window before following.")
    parser.add_argument("--summary", action="store_true", help="Print summary output.")
    parser.add_argument("--clear-live-output", action="store_true", help="Clear known live output files before processing.")
    parser.add_argument("--max-runtime-seconds", type=positive_float, help="Maximum follow-mode runtime.")
    parser.add_argument("--target-library", default=str(DEFAULT_TARGET_LIBRARY_PATH), help="Path to target_library.json.")
    parser.add_argument("--target-profiles", default=str(DEFAULT_TARGET_PROFILES_PATH), help="Path to target_profiles.json.")
    parser.add_argument("--write-retry-attempts", type=positive_int, default=DEFAULT_WRITE_RETRY_ATTEMPTS, help="Live output replace retry attempts for transient Windows file locks. Default: 10.")
    parser.add_argument("--write-retry-delay-ms", type=non_negative_int, default=int(DEFAULT_WRITE_RETRY_DELAY_SECONDS * 1000), help="Initial live output write retry delay in milliseconds. Default: 10.")
    parser.add_argument("--strict-writes", action="store_true", help="Fail the processor if live output writes still fail after retries.")
    args = parser.parse_args()

    if args.once and args.follow:
        parser.error("--once and --follow are mutually exclusive")

    if not args.once and not args.follow:
        args.once = True

    if args.max_new_ticks_per_update is None:
        args.max_new_ticks_per_update = 1 if args.latency_mode == "realtime" else 0

    if args.candidate_output_window is None:
        args.candidate_output_window = "latest" if args.latency_mode == "realtime" else "rolling"

    if args.drop_backlog_to_meet_budget is None:
        args.drop_backlog_to_meet_budget = args.latency_mode == "realtime"

    if args.latest is not None and args.latest_with_frames is not None:
        parser.error("--latest and --latest-with-frames are mutually exclusive")

    if args.no_startup_backfill and args.process_existing:
        parser.error("--no-startup-backfill cannot be combined with --process-existing")

    if args.no_ui_targets:
        args.include_ui_targets = False
    elif args.exclude_ui_blocked:
        args.include_ui_targets = True

    return args


def main() -> int:
    args = parse_args()

    try:
        session = resolve_session(args)
    except RuntimeError as error:
        print(str(error))
        return 1

    if args.clear_live_output:
        clear_live_outputs(session)

    processor = LiveTargetProcessor(session, args)
    print_startup(session, args)

    startup_added = processor.initialize_from_existing()
    result = processor.process_window(force_rebuild=args.force_window_rebuild, rebuild_reason="startup")

    if args.summary or args.once or args.benchmark:
        print_result_summary(result, processor.tailer)

    if args.once:
        return 0

    started = time.monotonic()
    if startup_added and args.summary:
        print_follow_update(startup_added, result)

    try:
        while True:
            added, result = processor.poll_new_records(force_rebuild=args.force_window_rebuild)
            if added or args.summary or args.benchmark:
                print_follow_update(added, result)

            if args.max_runtime_seconds is not None and time.monotonic() - started >= args.max_runtime_seconds:
                break

            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("\nStopping live target processor.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

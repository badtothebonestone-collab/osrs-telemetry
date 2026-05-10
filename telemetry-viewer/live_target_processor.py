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
import live_packet_reader
import navigation_reachability
import select_target_candidates as candidate_builder
from telemetry_paths import find_newest_session, get_sessions_dir, list_tick_files


LIVE_STATUS_SCHEMA = "live_status.v1"
LIVE_INDEX_SCHEMA = "live_index.v1"
LIVE_CONTEXT_INDEX_SCHEMA = "live_context_index.v1"
LIVE_BASELINE_SCHEMA = "live_baseline_state.v1"
LIVE_ACTIVITY_SCHEMA = "live_activity_state.v1"
LIVE_EVENT_SCHEMA = "live_context_event.v1"
LIVE_PERFORMANCE_SCHEMA = "live_performance_summary.v1"
LIVE_NAVIGATION_SCHEMA = "live_navigation_summary.v1"
LIVE_OVERLAY_DEBUG_SCHEMA = "telemetry_overlay_debug_state.v1"
LIVE_TICK_SUMMARY_SCHEMA = "live_tick_summary.v1"
LIVE_WORLD_TARGET_SCHEMA = "live_world_target_update.v1"
LIVE_UI_TARGET_SCHEMA = "live_ui_target_update.v1"
LIVE_CANDIDATE_SCHEMA = "live_candidate_packet.v1"
DEFAULT_TARGET_LIBRARY_PATH = Path(__file__).resolve().with_name("target_library.json")
DEFAULT_TARGET_PROFILES_PATH = Path(__file__).resolve().with_name("target_profiles.json")
DEFAULT_WRITE_RETRY_ATTEMPTS = 10
DEFAULT_WRITE_RETRY_DELAY_SECONDS = 0.01
MAX_WRITE_RETRY_DELAY_SECONDS = 0.25
COMPACT_PACKET_RECENT_SECONDS = 120.0
WORLD_TARGET_EMIT_MODES = {"none", "candidates", "profile", "visible", "full"}
WORLD_TARGET_TYPES = {"npc", "player", "sceneObject", "groundItem", "tile"}
LATENCY_MODES = {"realtime", "complete"}
CANDIDATE_OUTPUT_WINDOWS = {"latest", "rolling"}
LIVENESS_MODES = {"off", "basic", "delta", "full"}
INPUT_SOURCES = {"raw-ticks", "compact-packets", "auto"}
COMPACT_PACKET_SOURCE = "compact-packets"
RAW_TICK_SOURCE = "raw-ticks"
COMPACT_PACKET_TYPES = {
    "baseline": "live_baseline_packet.v1",
    "sceneDelta": "live_scene_delta_packet.v1",
    "projection": "live_projection_packet.v1",
    "inventory": "live_inventory_packet.v1",
    "inventoryDelta": "live_inventory_delta_packet.v1",
    "activity": "live_activity_packet.v1",
    "navigation": "live_navigation_packet.v1",
    "collisionWindow": "live_collision_window_packet.v1",
    "collisionGrid": "live_collision_grid_packet.v1",
    "writerHealth": "live_writer_health_packet.v1",
}
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
    "live_activity_state.json",
    "live_event_timeline.jsonl",
    "overlay_debug_state.json",
    "live_performance_summary.json",
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


def raw_ticks_available(session: Path) -> bool:
    return bool(list_tick_files(session))


def compact_packet_state(session: Path) -> dict:
    index = live_packet_reader.read_index(session)
    index_path = live_packet_reader.live_packet_index_path(session)
    latest = live_packet_reader.latest_segment_path(session, index=index)
    if latest is None:
        files = live_packet_reader.list_live_packet_files(session, latest_only=True, use_index=False)
        latest = files[-1] if files else None
    age_seconds = None
    if latest is not None:
        try:
            age_seconds = max(0.0, time.time() - latest.stat().st_mtime)
        except OSError:
            age_seconds = None
    available = latest is not None and latest.exists()
    recent = bool(available and age_seconds is not None and age_seconds <= COMPACT_PACKET_RECENT_SECONDS)
    return {
        "available": available,
        "recent": recent,
        "indexPath": str(index_path),
        "indexExists": index_path.exists(),
        "latestSegment": str(latest) if latest else None,
        "latestTick": index.get("latestTick") if isinstance(index, dict) else None,
        "latestSequence": index.get("latestSequence") if isinstance(index, dict) else None,
        "ageSeconds": age_seconds,
    }


def compact_packets_available(session: Path) -> bool:
    return bool(compact_packet_state(session).get("available"))


def choose_input_source(session: Path, requested: str) -> tuple[str, bool, bool, str | None]:
    compact_state = compact_packet_state(session)
    compact_available = bool(compact_state.get("available"))
    compact_recent = bool(compact_state.get("recent"))
    raw_available = raw_ticks_available(session)

    if requested == RAW_TICK_SOURCE:
        reason = None if raw_available else "raw tick files are not available"
        return RAW_TICK_SOURCE, compact_available, raw_available, reason

    if requested == COMPACT_PACKET_SOURCE:
        reason = None if compact_available else "compact live packets are not available"
        return COMPACT_PACKET_SOURCE, compact_available, raw_available, reason

    if compact_available and compact_recent:
        return COMPACT_PACKET_SOURCE, compact_available, raw_available, None

    if compact_available and raw_available:
        return RAW_TICK_SOURCE, compact_available, raw_available, "compact live packets are stale; falling back to raw tick JSONL"

    if compact_available:
        return COMPACT_PACKET_SOURCE, compact_available, raw_available, "compact live packets are stale, but raw tick fallback is unavailable"

    if raw_available:
        return RAW_TICK_SOURCE, compact_available, raw_available, "compact live packets unavailable; falling back to raw tick JSONL"

    return RAW_TICK_SOURCE, compact_available, raw_available, "neither compact live packets nor raw tick JSONL were found"


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
        "activity": output_dir / "live_activity_state.json",
        "events": output_dir / "live_event_timeline.jsonl",
        "overlayDebug": output_dir / "overlay_debug_state.json",
        "performance": output_dir / "live_performance_summary.json",
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


def read_jsonl_objects(path: Path, *, limit: int | None = None) -> list[dict]:
    records: list[dict] = []
    if not path.exists():
        return records
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
    except OSError:
        return records
    if limit is not None and limit >= 0:
        return records[-limit:]
    return records


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
    "fileDiscoverMillis",
    "tailReadMillis",
    "lineSplitMillis",
    "jsonParseMillis",
    "rawTickIngestMillis",
    "tickCoalesceMillis",
    "baselineStateMillis",
    "activityStateMillis",
    "inventoryDeltaMillis",
    "livenessUpdateMillis",
    "livenessTotalMillis",
    "livenessDeltaMillis",
    "livenessCacheLookupMillis",
    "livenessUnavailablePruneMillis",
    "livenessVisibleRefFallbackMillis",
    "livenessCandidateApplyMillis",
    "livenessFullScanMillis",
    "classificationCacheMillis",
    "candidateCacheMillis",
    "worldTargetBuildMillis",
    "worldTargetFilterMillis",
    "candidateSelectMillis",
    "contextIndexMillis",
    "uiTargetLoadMillis",
    "outputWriteMillis",
    "outputSerializeMillis",
    "consolePrintMillis",
    "sleepMillis",
    "idleWaitMillis",
    "pollLoopMillis",
    "totalActiveMillis",
    "totalExclusiveMillis",
    "totalWallMillis",
    "totalDurationMillis",
]

TIMING_MODE = "exclusive"


def timing_payload(timing: Timing, total_duration_ms: float, tailer=None, *, raw_tick_ingest_millis: float = 0.0) -> dict:
    payload = {key: 0.0 for key in TIMING_BUCKETS}
    payload.update(timing.rounded())
    if tailer is not None:
        payload["fileDiscoverMillis"] = round(tailer.last_file_discover_millis, 3)
        payload["tailReadMillis"] = round(tailer.last_tail_read_millis, 3)
        payload["lineSplitMillis"] = round(tailer.last_line_split_millis, 3)
        payload["jsonParseMillis"] = round(tailer.last_json_parse_millis, 3)
    payload["rawTickIngestMillis"] = round(raw_tick_ingest_millis, 3)
    if not payload.get("livenessTotalMillis"):
        payload["livenessTotalMillis"] = round(
            float(payload.get("livenessDeltaMillis") or 0.0)
            + float(payload.get("livenessFullScanMillis") or 0.0)
            + float(payload.get("livenessUnavailablePruneMillis") or 0.0)
            + float(payload.get("livenessCandidateApplyMillis") or 0.0)
            + float(payload.get("livenessCacheLookupMillis") or 0.0)
            + float(payload.get("livenessVisibleRefFallbackMillis") or 0.0),
            3,
        )
    payload["livenessUpdateMillis"] = round(float(payload.get("livenessTotalMillis") or payload.get("livenessUpdateMillis") or 0.0), 3)
    exclusive_keys = [
        key
        for key in TIMING_BUCKETS
        if key
        not in {
            "totalExclusiveMillis",
            "totalWallMillis",
            "totalActiveMillis",
            "totalDurationMillis",
            "pollLoopMillis",
            "idleWaitMillis",
            "sleepMillis",
            "livenessUpdateMillis",
            "livenessTotalMillis",
        }
    ]
    payload["totalExclusiveMillis"] = round(sum(float(payload.get(key) or 0.0) for key in exclusive_keys), 3)
    payload["totalWallMillis"] = round(total_duration_ms, 3)
    payload["totalActiveMillis"] = round(total_duration_ms, 3)
    payload["pollLoopMillis"] = round(total_duration_ms, 3)
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
        self.last_file_discover_millis = 0.0
        self.last_tail_read_millis = 0.0
        self.last_line_split_millis = 0.0
        self.last_json_parse_millis = 0.0
        self.last_raw_records_seen = 0
        self.last_raw_records_fully_parsed = 0
        self.last_raw_records_skipped_before_parse = 0
        self.last_raw_records_light_parsed = 0
        self.last_coalesced_before_parse = 0
        self.last_newest_tick_selected = None
        self.last_file_offsets_advanced_past_skipped_records = False

    def reset_poll_stats(self) -> None:
        self.last_file_discover_millis = 0.0
        self.last_tail_read_millis = 0.0
        self.last_line_split_millis = 0.0
        self.last_json_parse_millis = 0.0
        self.last_raw_records_seen = 0
        self.last_raw_records_fully_parsed = 0
        self.last_raw_records_skipped_before_parse = 0
        self.last_raw_records_light_parsed = 0
        self.last_coalesced_before_parse = 0
        self.last_newest_tick_selected = None
        self.last_file_offsets_advanced_past_skipped_records = False

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

    def read_new_records(self, *, realtime: bool = False, max_records: int | None = None) -> list[tuple[Path, int, dict]]:
        records: list[tuple[Path, int, dict]] = []
        complete_lines: list[tuple[Path, int, str]] = []
        self.reset_poll_stats()

        started = time.perf_counter()
        files = self.files()
        self.last_file_discover_millis = (time.perf_counter() - started) * 1000.0

        for path in files:
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
            split_started = time.perf_counter()
            text = state.pending + data.decode("utf-8", errors="replace")
            last_newline = max(text.rfind("\n"), text.rfind("\r"))

            if last_newline < 0:
                state.pending = text
                self.last_line_split_millis += (time.perf_counter() - split_started) * 1000.0
                continue

            complete = text[: last_newline + 1]
            state.pending = text[last_newline + 1 :]

            for raw_line in complete.splitlines():
                state.line_number += 1
                line = raw_line.strip()
                if not line:
                    continue
                complete_lines.append((path, state.line_number, line))
            self.last_line_split_millis += (time.perf_counter() - split_started) * 1000.0

        self.last_raw_records_seen = len(complete_lines)
        lines_to_parse = complete_lines
        if realtime and max_records and max_records > 0 and len(complete_lines) > max_records:
            skipped = len(complete_lines) - max_records
            lines_to_parse = complete_lines[-max_records:]
            self.last_raw_records_skipped_before_parse = skipped
            self.last_coalesced_before_parse = skipped
            self.last_file_offsets_advanced_past_skipped_records = True

        for path, line_number, line in lines_to_parse:
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
                records.append((path, line_number, record))
            else:
                self.malformed_counts[str(path)] += 1
                self.malformed_total += 1

        self.last_raw_records_fully_parsed = len(records)
        tick_ids = [tick_id_for(record) for _path, _line_number, record in records]
        tick_ids = [tick_id for tick_id in tick_ids if tick_id is not None]
        self.last_newest_tick_selected = max(tick_ids) if tick_ids else None
        return records


def packet_tick(packet: dict) -> int | None:
    tick = packet.get("tick")
    return tick if isinstance(tick, int) else None


def packet_sequence(packet: dict) -> int:
    sequence = packet.get("sequence")
    return sequence if isinstance(sequence, int) else -1


def normalize_compact_point(point: dict | None) -> dict | None:
    if not isinstance(point, dict):
        return None
    x = point.get("x", point.get("canvasX"))
    y = point.get("y", point.get("canvasY"))
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return {"x": x, "y": y}


def normalize_compact_scene_object(value: dict) -> dict:
    record = dict(value)

    if not record.get("objectName") and record.get("name"):
        record["objectName"] = record.get("name")
    if not record.get("objectNameSource") and record.get("nameSource"):
        record["objectNameSource"] = record.get("nameSource")
    if not record.get("kind") and record.get("layer"):
        record["kind"] = record.get("layer")

    aim = record.get("aimPoint") if isinstance(record.get("aimPoint"), dict) else None
    point = normalize_compact_point(aim)
    if point:
        record.setdefault("canvasLocation", point)
        record.setdefault("canvasCenter", point)

    summary = record.get("geometrySummary") if isinstance(record.get("geometrySummary"), dict) else {}
    if isinstance(summary.get("clickboxBounds"), dict):
        record.setdefault("clickboxBounds", summary.get("clickboxBounds"))
    if isinstance(summary.get("convexHullBounds"), dict):
        record.setdefault("convexHullBounds", summary.get("convexHullBounds"))

    if record.get("geometryAvailable") is None:
        record["geometryAvailable"] = bool(point or record.get("clickboxBounds") or record.get("convexHullBounds"))
    if record.get("onScreen") is None:
        record["onScreen"] = bool(record.get("geometryAvailable"))

    return record


def normalize_compact_deltas(deltas: dict | None) -> dict:
    deltas = deltas if isinstance(deltas, dict) else {}
    normalized = {}
    for field_name in ("newObjects", "updatedObjects", "despawnedObjects"):
        normalized[field_name] = [
            normalize_compact_scene_object(item)
            for item in deltas.get(field_name) or []
            if isinstance(item, dict)
        ]
    return normalized


def compact_inventory_container(container) -> dict | list:
    if isinstance(container, dict):
        return container
    return []


def merge_player_status(tick: dict, player: dict | None) -> None:
    if not isinstance(player, dict):
        return

    local_player = tick.setdefault("localPlayer", {})
    status = tick.setdefault("status", {})
    for key in ("worldX", "worldY", "plane", "localX", "localY", "sceneX", "sceneY", "animation", "poseAnimation", "combatLevel"):
        if player.get(key) is not None:
            local_player[key] = player.get(key)

    for key in (
        "runEnergyRaw",
        "runEnergyPercent",
        "weight",
        "hitpointsBoosted",
        "hitpointsReal",
        "localHealthRatio",
        "localHealthScale",
    ):
        if player.get(key) is not None:
            status[key] = player.get(key)

    interacting = player.get("interacting")
    if isinstance(interacting, dict):
        status["interactingType"] = interacting.get("type")
        status["interactingIndex"] = interacting.get("index")
        status["interactingId"] = interacting.get("id")
        status["interactingName"] = interacting.get("name")
        status["interactingWorldX"] = interacting.get("worldX")
        status["interactingWorldY"] = interacting.get("worldY")
        status["interactingPlane"] = interacting.get("plane")


def source_capture_summary_from_compact(source: dict | None) -> dict | None:
    if not isinstance(source, dict):
        return None
    fields = {
        "sceneObjectsSeen": source.get("sceneObjectsSeen"),
        "sceneObjectsCaptured": source.get("sceneObjectsCaptured"),
        "sceneObjectsSkippedByCap": source.get("sceneObjectsSkippedByCap"),
        "sceneObjectCapHit": source.get("sceneObjectCapHit"),
    }
    if not any(value is not None for value in fields.values()):
        return None
    return fields


def compact_packets_to_tick(packets: list[dict]) -> dict | None:
    packets = [packet for packet in packets if isinstance(packet, dict)]
    if not packets:
        return None

    packets.sort(key=packet_sequence)
    latest = packets[-1]
    tick_id = packet_tick(latest)
    if tick_id is None:
        return None

    by_type = {}
    for packet in packets:
        packet_type = packet.get("packetType")
        if isinstance(packet_type, str):
            by_type[packet_type] = packet

    baseline = by_type.get(COMPACT_PACKET_TYPES["baseline"], {}).get("payload") or {}
    scene_delta = by_type.get(COMPACT_PACKET_TYPES["sceneDelta"], {}).get("payload") or {}
    projection = by_type.get(COMPACT_PACKET_TYPES["projection"], {}).get("payload") or {}
    inventory = by_type.get(COMPACT_PACKET_TYPES["inventory"], {}).get("payload") or {}
    inventory_delta_packet = by_type.get(COMPACT_PACKET_TYPES["inventoryDelta"], {}).get("payload") or {}
    activity = by_type.get(COMPACT_PACKET_TYPES["activity"], {}).get("payload") or {}
    navigation = by_type.get(COMPACT_PACKET_TYPES["navigation"], {}).get("payload") or {}
    collision_window = by_type.get(COMPACT_PACKET_TYPES["collisionWindow"], {}).get("payload") or {}
    collision_grid = by_type.get(COMPACT_PACKET_TYPES["collisionGrid"], {}).get("payload") or {}
    writer_health = by_type.get(COMPACT_PACKET_TYPES["writerHealth"], {}).get("payload") or {}

    timestamp = next((packet.get("timestampUtc") for packet in reversed(packets) if isinstance(packet.get("timestampUtc"), str)), None)
    session_id = next((packet.get("sessionId") for packet in reversed(packets) if packet.get("sessionId")), None)
    tick = {
        "schemaVersion": "compact_live_packet_synthetic_tick.v1",
        "sessionId": session_id,
        "tickId": tick_id,
        "timestampUtc": timestamp,
        "gameState": baseline.get("gameState"),
        "_inputSource": COMPACT_PACKET_SOURCE,
        "_compactPacketSequence": packet_sequence(latest),
        "_compactPacketTypes": sorted(by_type.keys()),
    }

    merge_player_status(tick, baseline.get("player"))
    merge_player_status(tick, activity)

    camera = baseline.get("cameraViewport") if isinstance(baseline.get("cameraViewport"), dict) else {}
    for key in (
        "cameraX",
        "cameraY",
        "cameraZ",
        "cameraPitch",
        "cameraYaw",
        "viewportWidth",
        "viewportHeight",
        "viewportXOffset",
        "viewportYOffset",
        "canvasWidth",
        "canvasHeight",
    ):
        if camera.get(key) is not None:
            tick[key] = camera.get(key)

    if baseline.get("latestFramePath"):
        tick["framePath"] = baseline.get("latestFramePath")
    if baseline.get("frameCaptureStatus"):
        tick["frameCaptureStatus"] = baseline.get("frameCaptureStatus")
    if isinstance(baseline.get("source"), dict):
        tick["_sourceCompleteness"] = baseline.get("source")

    scene_capture = scene_delta.get("sceneCaptureSummary") if isinstance(scene_delta.get("sceneCaptureSummary"), dict) else None
    if not scene_capture:
        scene_capture = source_capture_summary_from_compact(baseline.get("source"))
        if scene_capture and baseline.get("sceneCaptureMode"):
            scene_capture["sceneCaptureMode"] = baseline.get("sceneCaptureMode")
    if scene_capture:
        tick["sceneCaptureSummary"] = scene_capture

    if isinstance(scene_delta.get("sceneIndexSummary"), dict):
        tick["sceneIndexSummary"] = scene_delta.get("sceneIndexSummary")
    if isinstance(scene_delta.get("sceneObjectDeltas"), dict):
        tick["sceneObjectDeltas"] = normalize_compact_deltas(scene_delta.get("sceneObjectDeltas"))

    if isinstance(projection.get("sceneProjectionSummary"), dict):
        tick["sceneProjectionSummary"] = projection.get("sceneProjectionSummary")
    visible_refs = projection.get("visibleObjectRefs")
    if isinstance(visible_refs, list):
        tick["visibleSceneObjectRefs"] = [
            normalize_compact_scene_object(item)
            for item in visible_refs
            if isinstance(item, dict)
        ]

    if isinstance(inventory, dict):
        tick["inventory"] = compact_inventory_container(inventory.get("inventory"))
        tick["equipment"] = compact_inventory_container(inventory.get("equipment"))
        tick["_inventoryDeltaTrackingAvailable"] = bool(inventory.get("inventoryDeltaTrackingAvailable"))
    if isinstance(inventory_delta_packet, dict) and inventory_delta_packet:
        tick["_inventoryDelta"] = inventory_delta_packet
    if isinstance(activity, dict) and activity:
        tick["_activityPacket"] = activity

    if isinstance(navigation, dict) and navigation:
        tick["_navigation"] = navigation
    if isinstance(collision_window, dict) and collision_window:
        tick["_collisionWindow"] = collision_window
    if isinstance(collision_grid, dict) and collision_grid:
        tick["_collisionGrid"] = collision_grid

    if isinstance(writer_health, dict):
        tick["writerQueueSize"] = writer_health.get("rawWriterQueueDepth")
        tick["writerDroppedRecords"] = writer_health.get("droppedRawRecords")
        tick["_writerHealth"] = writer_health

    tick.setdefault("npcs", [])
    tick.setdefault("players", [])
    tick.setdefault("groundItems", [])
    return tick


class CompactPacketTailer:
    def __init__(self, session: Path):
        self.session = session
        self.states: dict[Path, TailState] = {}
        self.malformed_counts: Counter[str] = Counter()
        self.malformed_total = 0
        self.read_errors: list[str] = []
        self.last_file_discover_millis = 0.0
        self.last_tail_read_millis = 0.0
        self.last_line_split_millis = 0.0
        self.last_json_parse_millis = 0.0
        self.last_raw_records_seen = 0
        self.last_raw_records_fully_parsed = 0
        self.last_raw_records_skipped_before_parse = 0
        self.last_raw_records_light_parsed = 0
        self.last_coalesced_before_parse = 0
        self.last_newest_tick_selected = None
        self.last_file_offsets_advanced_past_skipped_records = False
        self.last_compact_packets_seen = 0
        self.last_compact_packets_processed = 0
        self.last_compact_packets_coalesced = 0
        self.last_compact_packet_last_sequence = None
        self.last_compact_packet_latest_segment = None
        self.last_compact_packet_rollover_count = 0
        self.last_compact_packet_read_errors = 0
        self._current_latest_segment: Path | None = None

    def reset_poll_stats(self) -> None:
        self.last_file_discover_millis = 0.0
        self.last_tail_read_millis = 0.0
        self.last_line_split_millis = 0.0
        self.last_json_parse_millis = 0.0
        self.last_raw_records_seen = 0
        self.last_raw_records_fully_parsed = 0
        self.last_raw_records_skipped_before_parse = 0
        self.last_raw_records_light_parsed = 0
        self.last_coalesced_before_parse = 0
        self.last_newest_tick_selected = None
        self.last_file_offsets_advanced_past_skipped_records = False
        self.last_compact_packets_seen = 0
        self.last_compact_packets_processed = 0
        self.last_compact_packets_coalesced = 0
        self.last_compact_packet_read_errors = 0

    def files(self) -> list[Path]:
        return live_packet_reader.list_live_packet_files(self.session)

    def partial_line_files(self) -> list[str]:
        return [str(path) for path, state in self.states.items() if state.pending]

    def _note_latest_segment(self) -> None:
        latest = live_packet_reader.latest_segment_path(self.session)
        self.last_compact_packet_latest_segment = str(latest) if latest else None
        if latest and self._current_latest_segment and latest != self._current_latest_segment:
            self.last_compact_packet_rollover_count += 1
        if latest:
            self._current_latest_segment = latest

    def seek_to_end(self) -> None:
        self._note_latest_segment()
        for path in self.files():
            try:
                size = path.stat().st_size
            except OSError:
                continue
            self.states[path] = TailState(offset=size)

    def _records_from_packet_groups(
        self,
        packet_groups: dict[int, list[dict]],
        packet_sources: dict[int, tuple[Path, int]],
        *,
        realtime: bool = False,
        max_records: int | None = None,
    ) -> list[tuple[Path, int, dict]]:
        records = []
        tick_ids = sorted(packet_groups)
        self.last_raw_records_seen = len(tick_ids)

        keep_ids = tick_ids
        if realtime and max_records and max_records > 0 and len(tick_ids) > max_records:
            skipped_ids = tick_ids[:-max_records]
            keep_ids = tick_ids[-max_records:]
            self.last_raw_records_skipped_before_parse = len(skipped_ids)
            self.last_coalesced_before_parse = len(skipped_ids)
            self.last_compact_packets_coalesced = sum(len(packet_groups[tick_id]) for tick_id in skipped_ids)
            self.last_file_offsets_advanced_past_skipped_records = True

        for tick_id in keep_ids:
            packets = packet_groups.get(tick_id) or []
            tick = compact_packets_to_tick(packets)
            if not tick:
                continue
            path, line_number = packet_sources.get(tick_id, (live_packet_reader.live_packet_dir(self.session), 0))
            records.append((path, line_number, tick))

        self.last_raw_records_fully_parsed = len(records)
        self.last_compact_packets_processed = sum(len(packet_groups[tick_id]) for tick_id in keep_ids)
        selected_ids = [tick_id_for(record) for _path, _line_number, record in records]
        selected_ids = [tick_id for tick_id in selected_ids if tick_id is not None]
        self.last_newest_tick_selected = max(selected_ids) if selected_ids else None
        return records

    def read_existing_records(self, limit: int) -> list[tuple[Path, int, dict]]:
        if limit <= 0:
            return []

        packet_groups: dict[int, list[dict]] = {}
        packet_sources: dict[int, tuple[Path, int]] = {}
        for result in live_packet_reader.iter_live_packets(self.files(), ignore_partial_last_line=True):
            if result.error:
                self.malformed_counts[str(result.path)] += 1
                self.malformed_total += 1
                continue
            packet = result.record
            if not isinstance(packet, dict) or packet.get("schema") != live_packet_reader.PACKET_SCHEMA:
                continue
            self.last_compact_packets_seen += 1
            tick = packet_tick(packet)
            if tick is None:
                continue
            packet_groups.setdefault(tick, []).append(packet)
            packet_sources[tick] = (result.path, result.line_number)
            sequence = packet.get("sequence")
            if isinstance(sequence, int):
                self.last_compact_packet_last_sequence = sequence

        if len(packet_groups) > limit:
            keep = set(sorted(packet_groups)[-limit:])
            packet_groups = {tick: packets for tick, packets in packet_groups.items() if tick in keep}
            packet_sources = {tick: source for tick, source in packet_sources.items() if tick in keep}
        return self._records_from_packet_groups(packet_groups, packet_sources)

    def read_new_records(self, *, realtime: bool = False, max_records: int | None = None) -> list[tuple[Path, int, dict]]:
        self.reset_poll_stats()
        packet_groups: dict[int, list[dict]] = {}
        packet_sources: dict[int, tuple[Path, int]] = {}
        complete_lines: list[tuple[Path, int, str]] = []

        started = time.perf_counter()
        files = self.files()
        self._note_latest_segment()
        self.last_file_discover_millis = (time.perf_counter() - started) * 1000.0

        known_files = set(files)
        for path in list(self.states):
            if path not in known_files and not path.exists():
                self.states.pop(path, None)

        for path in files:
            state = self.states.setdefault(path, TailState())
            try:
                size = path.stat().st_size
            except OSError as error:
                self.read_errors.append(f"could not stat {path}: {error}")
                self.last_compact_packet_read_errors += 1
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
                self.last_compact_packet_read_errors += 1
                continue
            self.last_tail_read_millis += (time.perf_counter() - started) * 1000.0
            state.offset += len(data)

            split_started = time.perf_counter()
            text = state.pending + data.decode("utf-8", errors="replace")
            last_newline = max(text.rfind("\n"), text.rfind("\r"))
            if last_newline < 0:
                state.pending = text
                self.last_line_split_millis += (time.perf_counter() - split_started) * 1000.0
                continue

            complete = text[: last_newline + 1]
            state.pending = text[last_newline + 1 :]
            for raw_line in complete.splitlines():
                state.line_number += 1
                line = raw_line.strip()
                if line:
                    complete_lines.append((path, state.line_number, line))
            self.last_line_split_millis += (time.perf_counter() - split_started) * 1000.0

        self.last_compact_packets_seen = len(complete_lines)
        for path, line_number, line in complete_lines:
            parse_started = time.perf_counter()
            try:
                packet = json.loads(line)
            except json.JSONDecodeError:
                self.last_json_parse_millis += (time.perf_counter() - parse_started) * 1000.0
                self.malformed_counts[str(path)] += 1
                self.malformed_total += 1
                continue
            self.last_json_parse_millis += (time.perf_counter() - parse_started) * 1000.0
            if not isinstance(packet, dict) or packet.get("schema") != live_packet_reader.PACKET_SCHEMA:
                self.malformed_counts[str(path)] += 1
                self.malformed_total += 1
                continue
            tick = packet_tick(packet)
            if tick is None:
                self.malformed_counts[str(path)] += 1
                self.malformed_total += 1
                continue
            packet_groups.setdefault(tick, []).append(packet)
            packet_sources[tick] = (path, line_number)
            sequence = packet.get("sequence")
            if isinstance(sequence, int):
                self.last_compact_packet_last_sequence = sequence

        return self._records_from_packet_groups(packet_groups, packet_sources, realtime=realtime, max_records=max_records)


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
    explicit_sources = [tick.get("_sourceCompleteness") for tick in ticks if isinstance(tick.get("_sourceCompleteness"), dict)]

    if not summaries and not index_summaries:
        if explicit_sources:
            cap_hit = any(source.get("sourceCapHit") is True for source in explicit_sources)
            complete_values = [source.get("sourceSceneKnowledgeComplete") for source in explicit_sources]
            complete = all(value is True for value in complete_values) if complete_values else None
            skipped = sum(int(source.get("sceneObjectsSkippedByCap") or 0) for source in explicit_sources)
            return {
                "sourceSceneKnowledgeComplete": complete,
                "sourceCapHit": cap_hit,
                "selectedTicksSkippedByCap": sum(1 for source in explicit_sources if source.get("sourceCapHit") is True or int(source.get("sceneObjectsSkippedByCap") or 0) > 0),
                "sceneObjectsSeen": sum(int(source.get("sceneObjectsSeen") or 0) for source in explicit_sources),
                "sceneObjectsCaptured": sum(int(source.get("sceneObjectsCaptured") or 0) for source in explicit_sources),
                "sceneObjectsSkippedByCap": skipped,
                "staticSceneIndexObjectCount": None,
                "visibleSceneObjectRefsCount": sum(raw_count(tick.get("visibleSceneObjectRefs")) for tick in ticks),
            }
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


def target_classes_by_id(library: dict) -> dict[str, dict]:
    classes = {}
    for target_class in library.get("targetClasses") or []:
        if isinstance(target_class, dict) and target_class.get("classId"):
            classes[str(target_class.get("classId")).lower()] = target_class
    return classes


def candidate_class_ids(candidate: dict) -> set[str]:
    ids = set()
    for key in ("classId", "targetClass"):
        value = candidate.get(key)
        if value:
            ids.add(str(value).lower())
    for value in candidate.get("targetClassIds") or []:
        if value:
            ids.add(str(value).lower())
    target = candidate.get("target") if isinstance(candidate.get("target"), dict) else {}
    for value in target.get("targetClassIds") or []:
        if value:
            ids.add(str(value).lower())
    return ids


def class_config_values(library: dict, class_ids: set[str], field: str) -> list:
    classes = target_classes_by_id(library)
    values = []
    for class_id in class_ids:
        target_class = classes.get(class_id)
        if not isinstance(target_class, dict):
            continue
        field_value = target_class.get(field)
        if isinstance(field_value, list):
            values.extend(field_value)
        elif field_value not in (None, ""):
            values.append(field_value)
    return values


def lower_strings(values) -> set[str]:
    return {str(value).strip().lower() for value in values or [] if str(value).strip()}


def candidate_name(candidate: dict) -> str:
    target = candidate.get("target") if isinstance(candidate.get("target"), dict) else {}
    return str(candidate.get("name") or target.get("name") or target.get("targetName") or "").strip()


def source_name(source: dict) -> str:
    return str(source.get("objectName") or source.get("name") or "").strip()


def actions_for_payload(payload: dict) -> list[str]:
    actions = payload.get("actions")
    if not isinstance(actions, list):
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        actions = target.get("actions")
    return [str(action).strip().lower() for action in actions or [] if str(action).strip()]


def is_tree_class(class_ids: set[str]) -> bool:
    return bool(class_ids & {"tree", "oak_tree", "willow_tree", "maple_tree", "yew_tree", "magic_tree"})


def depleted_name_match(name: str, configured_names: list) -> bool:
    lowered = name.lower()
    configured = lower_strings(configured_names)
    return "stump" in lowered or "depleted" in lowered or any(value in lowered for value in configured)


def candidate_depleted_by_name_or_actions(candidate: dict, library: dict) -> tuple[bool, list[str]]:
    evidence = []
    class_ids = candidate_class_ids(candidate)
    name = candidate_name(candidate)
    if depleted_name_match(name, class_config_values(library, class_ids, "depletedNames")):
        evidence.append(f"name suggests depleted/stump: {name}")

    raw_id = candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id")
    depleted_ids = {str(value) for value in class_config_values(library, class_ids, "depletedObjectIds")}
    if raw_id is not None and str(raw_id) in depleted_ids:
        evidence.append(f"object id is configured as depleted: {raw_id}")

    useful_actions = lower_strings(class_config_values(library, class_ids, "usefulActions"))
    actions = actions_for_payload(candidate)
    if useful_actions and actions and not any(any(useful in action for action in actions) for useful in useful_actions):
        evidence.append(f"useful action missing from available actions: {actions}")

    return bool(evidence), evidence


def source_class_info(tick: dict, source: dict, overrides: dict, library: dict) -> dict:
    preview = preview_scene_object_record(tick, source, overrides)
    return candidate_builder.classify_record(preview, library)


def source_depleted_by_name_or_actions(tick: dict, source: dict, overrides: dict, library: dict) -> tuple[bool, list[str], dict]:
    preview = preview_scene_object_record(tick, source, overrides)
    class_info = candidate_builder.classify_record(preview, library)
    class_ids = {str(value).lower() for value in class_info.get("targetClassIds") or []}
    if class_info.get("classId"):
        class_ids.add(str(class_info.get("classId")).lower())
    target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
    name = str(target.get("name") or source_name(source) or "").strip()
    evidence = []
    if depleted_name_match(name, class_config_values(library, class_ids, "depletedNames")):
        evidence.append(f"name suggests depleted/stump: {name}")

    object_id = source.get("id")
    depleted_ids = {str(value) for value in class_config_values(library, class_ids, "depletedObjectIds")}
    if object_id is not None and str(object_id) in depleted_ids:
        evidence.append(f"object id is configured as depleted: {object_id}")

    useful_actions = lower_strings(class_config_values(library, class_ids, "usefulActions"))
    actions = actions_for_payload(target)
    if useful_actions and actions and not any(any(useful in action for action in actions) for useful in useful_actions):
        evidence.append(f"useful action missing from available actions: {actions}")

    return bool(evidence), evidence, class_info


def object_identity_keys_from_values(
    *,
    target_type: str = "sceneObject",
    object_key=None,
    object_id=None,
    object_hash=None,
    world_x=None,
    world_y=None,
    plane=None,
    scene_x=None,
    scene_y=None,
) -> list[str]:
    keys = []
    if object_key not in (None, ""):
        keys.append(f"objectKey:{object_key}")
    if world_x is not None and world_y is not None and plane is not None:
        keys.append(f"location:{target_type}:{plane}:{world_x}:{world_y}")
        if object_id is not None:
            keys.append(f"idLocation:{target_type}:{object_id}:{plane}:{world_x}:{world_y}")
        if object_hash is not None:
            keys.append(f"hashLocation:{target_type}:{object_hash}:{plane}:{world_x}:{world_y}")
    if scene_x is not None and scene_y is not None and plane is not None:
        keys.append(f"scene:{target_type}:{plane}:{scene_x}:{scene_y}")
    return keys


def source_identity_keys(source: dict) -> list[str]:
    return object_identity_keys_from_values(
        object_key=source.get("objectKey"),
        object_id=source.get("id"),
        object_hash=source.get("hash"),
        world_x=source.get("worldX"),
        world_y=source.get("worldY"),
        plane=source.get("plane"),
        scene_x=source.get("sceneX"),
        scene_y=source.get("sceneY"),
    )


def candidate_identity_keys(candidate: dict) -> list[str]:
    target = candidate.get("target") if isinstance(candidate.get("target"), dict) else {}
    return object_identity_keys_from_values(
        target_type=str(candidate.get("targetType") or target.get("targetType") or "sceneObject"),
        object_key=candidate.get("objectKey") or target.get("objectKey"),
        object_id=candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id") or target.get("rawId") or target.get("id"),
        object_hash=candidate.get("hash") or target.get("hash"),
        world_x=candidate.get("worldX"),
        world_y=candidate.get("worldY"),
        plane=candidate.get("plane"),
        scene_x=candidate.get("sceneX"),
        scene_y=candidate.get("sceneY"),
    )


def limit_records(records: list[dict], limit: int) -> list[dict]:
    if limit == 0:
        return records
    return records[:limit]


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (len(ordered) - 1) * (percent / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = position - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 3)


def average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


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


def candidate_timeline_summary(candidate: dict | None) -> dict | None:
    if not candidate:
        return None
    aim = candidate.get("aimPointContext") if isinstance(candidate.get("aimPointContext"), dict) else candidate.get("aimPoint")
    navigation = candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}
    return {
        "classId": candidate.get("classId"),
        "targetType": candidate.get("targetType"),
        "name": candidate.get("name"),
        "id": candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id"),
        "hash": candidate.get("hash"),
        "objectKey": candidate.get("objectKey"),
        "worldX": candidate.get("worldX"),
        "worldY": candidate.get("worldY"),
        "plane": candidate.get("plane"),
        "sceneX": candidate.get("sceneX"),
        "sceneY": candidate.get("sceneY"),
        "distanceTiles": candidate.get("distanceTiles", candidate.get("targetDistanceChebyshev")),
        "qualityTier": candidate.get("qualityTier"),
        "qualityScore": candidate.get("qualityScore"),
        "targetLiveState": candidate.get("targetLiveState"),
        "directReachability": navigation.get("directReachability"),
        "aimPoint": aim,
    }


def overlay_aim_point(candidate: dict) -> dict | None:
    context = candidate.get("aimPointContext") if isinstance(candidate.get("aimPointContext"), dict) else {}
    aim = candidate.get("aimPoint") if isinstance(candidate.get("aimPoint"), dict) else {}
    canvas_x = context.get("canvasX", aim.get("canvasX", aim.get("x")))
    canvas_y = context.get("canvasY", aim.get("canvasY", aim.get("y")))
    if not isinstance(canvas_x, (int, float)) or not isinstance(canvas_y, (int, float)):
        return None
    return {
        "canvasX": canvas_x,
        "canvasY": canvas_y,
        "source": context.get("source") or candidate.get("preferredGeometryType") or aim.get("source"),
    }


def overlay_bounds(candidate: dict) -> dict | None:
    geometry_payload = candidate.get("geometry") if isinstance(candidate.get("geometry"), dict) else {}
    summary = candidate.get("geometrySummary") if isinstance(candidate.get("geometrySummary"), dict) else {}
    for value in (
        geometry_payload.get("aimBounds"),
        summary.get("bounds"),
        summary.get("aimBounds"),
        summary.get("clickboxBounds"),
        summary.get("convexHullBounds"),
        candidate.get("clickboxBounds"),
        candidate.get("convexHullBounds"),
    ):
        if not isinstance(value, dict):
            continue
        x = value.get("x")
        y = value.get("y")
        width = value.get("width", value.get("w"))
        height = value.get("height", value.get("h"))
        if all(isinstance(part, (int, float)) for part in (x, y, width, height)):
            return {"x": x, "y": y, "width": width, "height": height}
    return None


def compact_polygon(points, *, max_points: int = 16) -> list[list[float]] | None:
    if not isinstance(points, list) or not points or len(points) > max_points:
        return None
    compact = []
    for point in points:
        if isinstance(point, dict):
            x = point.get("x")
            y = point.get("y")
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            x, y = point[0], point[1]
        else:
            return None
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return None
        compact.append([x, y])
    return compact


def overlay_polygon(candidate: dict, key: str) -> list[list[float]] | None:
    geometry_payload = candidate.get("geometry") if isinstance(candidate.get("geometry"), dict) else {}
    summary = candidate.get("geometrySummary") if isinstance(candidate.get("geometrySummary"), dict) else {}
    return compact_polygon(geometry_payload.get(key) or summary.get(key) or candidate.get(key))


def overlay_target_summary(candidate: dict, status: dict | None = None, latest_tick: int | None = None) -> dict:
    status = status or {}
    navigation = candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}
    live_state = candidate.get("targetLiveState")
    reachability = navigation.get("directReachability")
    liveness = overlay_liveness_interpretation(candidate, status)
    label_parts = overlay_label_parts(candidate, reachability, liveness)
    target = {
        "rank": candidate.get("rank"),
        "isBest": bool(candidate.get("_overlayIsBest")),
        "isNearest": bool(candidate.get("_overlayIsNearest")),
        "classId": candidate.get("classId"),
        "name": candidate.get("name"),
        "id": candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id"),
        "hash": candidate.get("hash"),
        "objectKey": candidate.get("objectKey"),
        "category": candidate.get("category"),
        "worldX": candidate.get("worldX"),
        "worldY": candidate.get("worldY"),
        "plane": candidate.get("plane"),
        "sceneX": candidate.get("sceneX"),
        "sceneY": candidate.get("sceneY"),
        "distanceTiles": candidate.get("distanceTiles", candidate.get("targetDistanceChebyshev")),
        "onScreen": candidate.get("onScreen"),
        "geometryAvailable": candidate.get("geometryAvailable"),
        "qualityTier": candidate.get("qualityTier"),
        "qualityScore": candidate.get("qualityScore"),
        "targetLiveState": live_state,
        "livenessInterpretation": liveness,
        "directReachability": reachability,
        "reachabilityConfidence": navigation.get("reachabilityConfidence"),
        "reachabilityEvidence": (navigation.get("reachabilityEvidence") or [])[:3],
        "missingNavigationFields": navigation.get("missingNavigationFields") or [],
        "suppressReason": candidate.get("suppressReason"),
        "targetInCollisionWindow": navigation.get("targetInCollisionWindow"),
        "pathLengthTiles": navigation.get("pathLengthTiles"),
        "interactionRadiusTiles": navigation.get("interactionRadiusTiles"),
        "labelParts": label_parts,
        "overlayLabel": overlay_label_for(candidate, label_parts),
        "overlayColor": overlay_color_for(reachability, live_state),
        "sourceTick": candidate.get("tickId"),
        "latestTick": latest_tick,
        "aimPoint": overlay_aim_point(candidate),
        "bounds": overlay_bounds(candidate),
    }
    clickbox = overlay_polygon(candidate, "clickboxPolygon")
    tile = overlay_polygon(candidate, "canvasTilePolygon")
    if clickbox:
        target["clickboxPolygon"] = clickbox
    if tile:
        target["canvasTilePolygon"] = tile
    return target


def overlay_liveness_interpretation(candidate: dict | None, status: dict) -> str:
    live_state = candidate.get("targetLiveState") if isinstance(candidate, dict) else None
    if status.get("livenessDegraded") or status.get("livenessBudgetExceeded"):
        return "degraded"
    if live_state in ("recently_despawned", "depleted_or_stump", "stale", "changed"):
        return "degraded"
    if live_state == "live":
        return "direct"
    if live_state == "live_assumed":
        return "assumed"
    return "unknown"


def overlay_reachability_token(value) -> str:
    if value == "reachable":
        return "R"
    if value == "blocked":
        return "BLOCK"
    if value == "unknown":
        return "?"
    return "-"


def overlay_liveness_token(value) -> str:
    if value == "live_assumed":
        return "assumed"
    if value == "depleted_or_stump":
        return "depleted"
    if value == "recently_despawned":
        return "gone"
    if value == "stale":
        return "stale"
    if value == "live":
        return "live"
    if value:
        return str(value)
    return "unknown"


def overlay_color_for(reachability, live_state) -> str:
    if live_state in ("depleted_or_stump", "recently_despawned", "stale"):
        return "gray"
    if reachability == "blocked":
        return "red"
    if reachability == "reachable":
        return "green"
    if reachability == "unknown" or live_state in ("live_assumed", "unknown", None):
        return "yellow"
    return "green"


def overlay_label_parts(candidate: dict, reachability, liveness: str) -> dict:
    return {
        "distance": candidate.get("distanceTiles", candidate.get("targetDistanceChebyshev")),
        "reachability": overlay_reachability_token(reachability),
        "liveness": overlay_liveness_token(candidate.get("targetLiveState")),
        "livenessInterpretation": liveness,
        "quality": candidate.get("qualityTier"),
    }


def overlay_label_for(candidate: dict, label_parts: dict) -> str:
    name = candidate.get("name") or candidate.get("classId") or "target"
    parts = [str(name)]
    distance = label_parts.get("distance")
    if isinstance(distance, (int, float)):
        parts.append(f"d{distance:g}")
    reachability = label_parts.get("reachability")
    if reachability and reachability != "-":
        parts.append(str(reachability))
    liveness = label_parts.get("liveness")
    if liveness and liveness not in ("live", "unknown"):
        parts.append(str(liveness))
    return " ".join(parts)


def overlay_debug_state_for(
    session: Path,
    args,
    latest_tick: dict | None,
    candidates: list[dict],
    navigation: dict,
    status: dict,
    processed_at: str,
    events: list[dict] | None = None,
) -> dict:
    latest_tick = latest_tick or {}
    player = local_player_for(latest_tick)
    limit = max(0, int(getattr(args, "overlay_debug_target_limit", 50) or 0))
    nearest = nearest_timeline_candidate(candidates)
    marked_candidates = []
    for index, candidate in enumerate(candidates):
        marked = dict(candidate)
        marked["_overlayIsBest"] = index == 0
        marked["_overlayIsNearest"] = candidate is nearest
        marked_candidates.append(marked)
    capped_candidates = marked_candidates[:limit]
    collision_bounds = navigation.get("collisionWindowBounds") if isinstance(navigation.get("collisionWindowBounds"), dict) else {}
    recent_events = [event for event in (events or []) if isinstance(event, dict)]
    latest_event = recent_events[-1] if recent_events else {}
    warning_event_count = sum(1 for event in recent_events if event.get("severity") in {"warn", "error"})
    return {
        "schema": LIVE_OVERLAY_DEBUG_SCHEMA,
        "generatedAtUtc": processed_at,
        "sessionPath": str(session),
        "latestTick": tick_id_for(latest_tick),
        "profile": args.profile,
        "status": "WARN" if status.get("warnings") else "PASS",
        "latestEventSummary": latest_event.get("summary"),
        "latestEventTick": latest_event.get("tick"),
        "warningEventCount": warning_event_count,
        "lastEventTick": latest_event.get("tick"),
        "player": {
            "worldX": player.get("worldX"),
            "worldY": player.get("worldY"),
            "plane": player.get("plane"),
            "sceneX": player.get("sceneX"),
            "sceneY": player.get("sceneY"),
        },
        "summary": {
            "candidateCount": len(candidates),
            "targetLimit": limit,
            "targetsWritten": len(capped_candidates),
            "targetsSuppressedByCap": max(0, len(candidates) - len(capped_candidates)),
            "bestClass": candidates[0].get("classId") if candidates else None,
            "budgetExceeded": bool(status.get("budgetExceeded")),
            "writeFailures": status.get("writeFailureCount", 0),
            "latestEventSummary": latest_event.get("summary"),
            "latestEventTick": latest_event.get("tick"),
            "warningEventCount": warning_event_count,
            "lastEventTick": latest_event.get("tick"),
        },
        "targets": [overlay_target_summary(candidate, status, tick_id_for(latest_tick)) for candidate in capped_candidates],
        "collisionWindow": {
            "available": bool(navigation.get("collisionWindowAvailable")),
            "minSceneX": collision_bounds.get("minSceneX"),
            "maxSceneX": collision_bounds.get("maxSceneX"),
            "minSceneY": collision_bounds.get("minSceneY"),
            "maxSceneY": collision_bounds.get("maxSceneY"),
            "radius": navigation.get("collisionWindowRadius"),
            "playerSceneX": navigation.get("playerSceneX"),
            "playerSceneY": navigation.get("playerSceneY"),
        },
        "safety": {
            "readOnly": True,
            "drawOnly": True,
            "actionGenerated": False,
            "inputGenerated": False,
        },
    }


def candidate_timeline_key(candidate: dict | None) -> tuple | None:
    if not candidate:
        return None
    return (
        candidate.get("objectKey"),
        candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id"),
        candidate.get("hash"),
        candidate.get("worldX"),
        candidate.get("worldY"),
        candidate.get("plane"),
        candidate.get("classId"),
    )


def candidate_timeline_label(candidate: dict | None) -> str:
    if not candidate:
        return "no candidate"
    name = candidate.get("name") or candidate.get("classId") or "candidate"
    world_x = candidate.get("worldX")
    world_y = candidate.get("worldY")
    if world_x is not None and world_y is not None:
        return f"{name} at {world_x},{world_y}"
    return str(name)


def candidate_aim_point(candidate: dict | None) -> dict | None:
    if not isinstance(candidate, dict):
        return None
    aim = candidate.get("aimPointContext") if isinstance(candidate.get("aimPointContext"), dict) else candidate.get("aimPoint")
    if not isinstance(aim, dict):
        return None
    x = aim.get("canvasX", aim.get("x"))
    y = aim.get("canvasY", aim.get("y"))
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return {"canvasX": round(float(x), 3), "canvasY": round(float(y), 3), "source": aim.get("source")}


def candidate_aim_bucket(candidate: dict | None, *, bucket_pixels: float = 8.0) -> tuple | None:
    aim = candidate_aim_point(candidate)
    if not aim:
        return None
    return (
        round(float(aim["canvasX"]) / bucket_pixels),
        round(float(aim["canvasY"]) / bucket_pixels),
        aim.get("source"),
    )


def candidate_count_change_significant(previous_key, current_count: int) -> bool:
    try:
        previous = json.loads(previous_key) if isinstance(previous_key, str) else previous_key
    except (TypeError, ValueError, json.JSONDecodeError):
        return True
    if not isinstance(previous, int):
        return True
    delta = abs(int(current_count) - previous)
    threshold = max(3, int(max(previous, current_count) * 0.25))
    return delta >= threshold or (previous == 0 and current_count > 0) or (previous > 0 and current_count == 0)


def nearest_timeline_candidate(candidates: list[dict]) -> dict | None:
    if not candidates:
        return None
    with_distance = [
        candidate
        for candidate in candidates
        if isinstance(candidate.get("distanceTiles", candidate.get("targetDistanceChebyshev")), (int, float))
    ]
    if with_distance:
        return min(with_distance, key=lambda candidate: candidate.get("distanceTiles", candidate.get("targetDistanceChebyshev")))
    return candidates[0]


def event_value_key(value) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return str(value)


def inventory_delta_summary(delta: dict | None) -> str:
    if not isinstance(delta, dict):
        return "inventory changed"
    changes = delta.get("changes") if isinstance(delta.get("changes"), list) else delta.get("quantityChanges")
    if isinstance(changes, list) and changes:
        parts = []
        for change in changes[:3]:
            if not isinstance(change, dict):
                continue
            item_id = change.get("itemId")
            delta_value = change.get("delta")
            if isinstance(delta_value, (int, float)):
                sign = "+" if delta_value > 0 else ""
                parts.append(f"{sign}{delta_value} item {item_id}")
            else:
                parts.append(f"item {item_id}")
        if parts:
            suffix = "" if len(changes) <= 3 else f", +{len(changes) - 3} more"
            return ", ".join(parts) + suffix
    changed_slots = delta.get("changedSlots") if isinstance(delta.get("changedSlots"), list) else []
    if changed_slots:
        return f"{len(changed_slots)} inventory slot(s) changed"
    return "inventory signature changed"


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


def int_field(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def inventory_summary(tick: dict) -> dict:
    inventory = tick.get("inventory")
    items = inventory if isinstance(inventory, list) else inventory.get("items") if isinstance(inventory, dict) else []
    items = items if isinstance(items, list) else []
    filled = 0
    total_quantity = 0
    signature_parts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("itemId") if item.get("itemId") is not None else item.get("id")
        quantity = item.get("quantity")
        if item_id not in (None, -1, 0):
            filled += 1
            total_quantity += int(quantity) if isinstance(quantity, int) and not isinstance(quantity, bool) else 1
            signature_parts.append(f"{item_id}:{quantity}")
    if isinstance(inventory, dict):
        filled_value = int_field(inventory.get("filledSlots"))
        free_value = int_field(inventory.get("freeSlots"))
        slot_count_value = int_field(inventory.get("inventorySlotCount"))
        if slot_count_value is None:
            slot_count_value = int_field(inventory.get("slotCount"))
        if slot_count_value is None and filled_value is not None and free_value is not None:
            slot_count_value = max(28, filled_value + free_value)
        if slot_count_value is None and inventory.get("known") is True:
            slot_count_value = 28
        filled_slots = filled_value if filled_value is not None else filled
        free_slots = (
            max(0, slot_count_value - filled_slots)
            if slot_count_value is not None
            else free_value if free_value is not None else (max(0, 28 - filled_slots) if items else None)
        )
        total_quantity_value = int_field(inventory.get("totalItemQuantity"))
        if total_quantity_value is None:
            total_quantity_value = int_field(inventory.get("itemCount"))
        signature_value = inventory.get("signature")
        return {
            "inventorySlotCount": slot_count_value,
            "slotCount": slot_count_value,
            "itemCount": total_quantity_value if total_quantity_value is not None else total_quantity,
            "totalItemQuantity": total_quantity_value if total_quantity_value is not None else total_quantity,
            "filledSlots": filled_slots,
            "freeSlots": free_slots,
            "signature": signature_value if isinstance(signature_value, str) and signature_value else ("|".join(signature_parts) if signature_parts else None),
        }
    slot_count = max(28, len(inventory)) if isinstance(inventory, list) else None
    return {
        "inventorySlotCount": slot_count,
        "slotCount": slot_count,
        "itemCount": total_quantity,
        "totalItemQuantity": total_quantity,
        "filledSlots": filled,
        "freeSlots": max(0, slot_count - filled) if slot_count is not None else None,
        "signature": "|".join(signature_parts) if signature_parts else None,
    }


def normalized_inventory_items(tick: dict) -> list[dict]:
    inventory = tick.get("inventory")
    items = inventory if isinstance(inventory, list) else inventory.get("items") if isinstance(inventory, dict) else []
    normalized = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        item_id = item.get("itemId") if item.get("itemId") is not None else item.get("id")
        quantity = item.get("quantity")
        slot = item.get("slot")
        if item_id in (None, -1, 0):
            continue
        normalized.append({"slot": slot, "itemId": item_id, "quantity": quantity if quantity is not None else 1})
    normalized.sort(key=lambda item: (item.get("slot") is None, item.get("slot"), item.get("itemId")))
    return normalized


def inventory_signature_for_tick(tick: dict) -> str | None:
    items = normalized_inventory_items(tick)
    if not items:
        return None
    return "|".join(f"{item.get('slot')}:{item.get('itemId')}:{item.get('quantity')}" for item in items)


def item_quantity_counter(items: list[dict]) -> Counter:
    counter = Counter()
    for item in items:
        counter[str(item.get("itemId"))] += int(item.get("quantity") or 0)
    return counter


def inventory_delta(previous_tick: dict | None, current_tick: dict | None) -> dict | None:
    if not previous_tick or not current_tick:
        return None
    previous_items = normalized_inventory_items(previous_tick)
    current_items = normalized_inventory_items(current_tick)
    previous = item_quantity_counter(previous_items)
    current = item_quantity_counter(current_items)
    changes = []
    for item_id in sorted(set(previous) | set(current)):
        before = previous.get(item_id, 0)
        after = current.get(item_id, 0)
        if before == after:
            continue
        changes.append(
            {
                "itemId": int(item_id) if item_id.isdigit() else item_id,
                "beforeQuantity": before,
                "afterQuantity": after,
                "delta": after - before,
                "changeType": "itemAdded" if after > before else "itemRemoved",
            }
        )
    if not changes and inventory_signature_for_tick(previous_tick) == inventory_signature_for_tick(current_tick):
        return None
    previous_summary = inventory_summary(previous_tick)
    current_summary = inventory_summary(current_tick)
    return {
        "fromTick": tick_id_for(previous_tick),
        "toTick": tick_id_for(current_tick),
        "changes": changes,
        "changedSlots": [],
        "generatedFromItemContainerChanged": False,
        "filledSlotsBefore": previous_summary.get("filledSlots"),
        "filledSlotsAfter": current_summary.get("filledSlots"),
        "freeSlotsBefore": previous_summary.get("freeSlots"),
        "freeSlotsAfter": current_summary.get("freeSlots"),
    }


def explicit_inventory_delta_for_tick(tick: dict | None) -> dict | None:
    tick = tick or {}
    delta = tick.get("_inventoryDelta")
    if not isinstance(delta, dict):
        return None

    changes = delta.get("quantityChanges")
    if not isinstance(changes, list):
        changes = delta.get("changes") if isinstance(delta.get("changes"), list) else []

    return {
        "fromTick": delta.get("fromTick"),
        "toTick": delta.get("toTick") if delta.get("toTick") is not None else delta.get("tick", tick_id_for(tick)),
        "changes": [change for change in changes if isinstance(change, dict)],
        "changedSlots": [change for change in delta.get("changedSlots", []) if isinstance(change, dict)]
        if isinstance(delta.get("changedSlots"), list)
        else [],
        "addedItems": [change for change in delta.get("addedItems", []) if isinstance(change, dict)]
        if isinstance(delta.get("addedItems"), list)
        else [],
        "removedItems": [change for change in delta.get("removedItems", []) if isinstance(change, dict)]
        if isinstance(delta.get("removedItems"), list)
        else [],
        "filledSlotsBefore": delta.get("filledSlotsBefore"),
        "filledSlotsAfter": delta.get("filledSlotsAfter"),
        "freeSlotsBefore": delta.get("freeSlotsBefore"),
        "freeSlotsAfter": delta.get("freeSlotsAfter"),
        "inventorySignatureBefore": delta.get("inventorySignatureBefore"),
        "inventorySignatureAfter": delta.get("inventorySignatureAfter"),
        "inventoryFull": delta.get("inventoryFull"),
        "generatedFromItemContainerChanged": bool(delta.get("generatedFromItemContainerChanged")),
        "eventSource": delta.get("eventSource"),
    }


def inventory_state_for_ticks(ticks: list[dict], latest_tick: dict | None) -> dict:
    latest_tick = latest_tick or {}
    items = normalized_inventory_items(latest_tick)
    summary = inventory_summary(latest_tick)
    known = isinstance(latest_tick.get("inventory"), (list, dict))
    deltas = []
    ordered = [tick for tick in ticks if tick_id_for(tick) is not None]
    ordered.sort(key=lambda tick: tick_id_for(tick) or -1)
    for previous, current in zip(ordered, ordered[1:]):
        delta = inventory_delta(previous, current)
        if delta:
            deltas.append(delta)
    for tick in ordered:
        explicit_delta = explicit_inventory_delta_for_tick(tick)
        if explicit_delta:
            deltas.append(explicit_delta)
    deltas.sort(key=lambda delta: delta.get("toTick") if delta.get("toTick") is not None else -1)
    latest_delta = deltas[-1] if deltas else None
    delta_tracking_known = (
        len(ordered) >= 2
        or any(isinstance(tick.get("_inventoryDelta"), dict) for tick in ordered)
        or bool(latest_tick.get("_inventoryDeltaTrackingAvailable"))
    )
    return {
        "known": known,
        "inventorySlotCount": summary.get("inventorySlotCount") if known else None,
        "slotCount": summary.get("slotCount") if known else None,
        "freeSlots": summary.get("freeSlots") if known else None,
        "filledSlots": summary.get("filledSlots") if known else None,
        "itemCount": summary.get("itemCount") if known else None,
        "totalItemQuantity": summary.get("totalItemQuantity") if known else None,
        "items": items if known else [],
        "inventoryHash": summary.get("signature"),
        "signature": summary.get("signature"),
        "changedThisTick": bool(latest_delta and latest_delta.get("toTick") == tick_id_for(latest_tick)),
        "changedRecently": bool(deltas),
        "recentItemDeltas": deltas[-10:],
        "inventoryDeltaTrackingKnown": delta_tracking_known,
        "inventoryDeltasAvailable": delta_tracking_known,
        "warnings": [] if delta_tracking_known else ["inventory deltas unavailable in the current live window"],
        "inventoryFull": summary.get("freeSlots") == 0 if known and summary.get("freeSlots") is not None else None,
    }


def equipment_state_for_tick(tick: dict | None) -> dict:
    tick = tick or {}
    equipment = tick.get("equipment")
    items = []
    if isinstance(equipment, list):
        for item in equipment:
            if not isinstance(item, dict):
                continue
            item_id = item.get("itemId") if item.get("itemId") is not None else item.get("id")
            if item_id in (None, -1, 0):
                continue
            items.append({"slot": item.get("slot"), "itemId": item_id, "quantity": item.get("quantity")})
    return {"known": isinstance(equipment, list), "items": items}


def apparent_activity_for_tick(tick: dict | None, inventory_state: dict, liveness_summary: dict) -> dict:
    tick = tick or {}
    player = local_player_for(tick)
    status = tick.get("status") if isinstance(tick.get("status"), dict) else {}
    animation = player.get("animation")
    interacting = status.get("interactingType") or status.get("interactingName") or player.get("interacting")
    evidence = []
    warnings = []
    state = "unknown"
    confidence = 0.2
    if animation not in (None, -1, 0):
        state = "animating"
        confidence = 0.65
        evidence.append(f"local player animation={animation}")
    elif interacting not in (None, "", -1):
        state = "interacting"
        confidence = 0.55
        evidence.append(f"interacting={interacting}")
    elif player.get("isMoving") is True:
        state = "moving"
        confidence = 0.55
        evidence.append("isMoving=true")
    elif animation in (-1, 0):
        state = "idle"
        confidence = 0.5
        evidence.append(f"local player animation={animation}")
    else:
        warnings.append("local player animation/movement fields are incomplete.")

    apparent_task = "unknown"
    if state == "animating":
        apparent_task = "woodcutting_possible"
        evidence.append("woodcutting_possible because a tree profile can pair animation with nearby tree candidates.")
    if inventory_state.get("changedThisTick") or inventory_state.get("changedRecently"):
        evidence.append("inventory changed recently")
    if liveness_summary.get("recentlyDepletedCount"):
        evidence.append("recent target depletion observed")
    return {
        "apparentState": state,
        "apparentTask": apparent_task,
        "confidence": round(confidence, 3),
        "evidence": evidence,
        "warnings": warnings,
    }


def activity_events_for_ticks(ticks: list[dict], latest_tick: dict | None) -> list[dict]:
    ordered = [tick for tick in ticks if tick_id_for(tick) is not None]
    ordered.sort(key=lambda tick: tick_id_for(tick) or -1)
    events: list[dict] = []

    for tick in ordered:
        packet = tick.get("_activityPacket")
        tick_id = tick_id_for(tick)
        if isinstance(packet, dict):
            changed_fields = packet.get("changedFields") if isinstance(packet.get("changedFields"), list) else []
            if changed_fields or packet.get("activityChanged"):
                events.append(
                    {
                        "tick": tick_id,
                        "changedFields": [field for field in changed_fields if isinstance(field, str)],
                        "animation": packet.get("animation"),
                        "previousAnimation": packet.get("previousAnimation"),
                        "poseAnimation": packet.get("poseAnimation"),
                        "previousPoseAnimation": packet.get("previousPoseAnimation"),
                        "interactingSignature": packet.get("interactingSignature"),
                        "previousInteractingSignature": packet.get("previousInteractingSignature"),
                        "eventSource": packet.get("eventSource"),
                    }
                )

    if not events and len(ordered) >= 2:
        previous = local_player_for(ordered[-2])
        current = local_player_for(ordered[-1])
        changed_fields = []
        if previous.get("animation") != current.get("animation"):
            changed_fields.append("animation")
        if previous.get("poseAnimation") != current.get("poseAnimation"):
            changed_fields.append("poseAnimation")
        if changed_fields:
            events.append(
                {
                    "tick": tick_id_for(ordered[-1]),
                    "changedFields": changed_fields,
                    "animation": current.get("animation"),
                    "previousAnimation": previous.get("animation"),
                    "poseAnimation": current.get("poseAnimation"),
                    "previousPoseAnimation": previous.get("poseAnimation"),
                    "eventSource": "rollingTickComparison",
                }
            )

    return events[-10:]


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
    navigation_packet = tick.get("_navigation") if isinstance(tick.get("_navigation"), dict) else {}
    collision_window = tick.get("_collisionWindow") if isinstance(tick.get("_collisionWindow"), dict) else {}
    collision_grid = tick.get("_collisionGrid") if isinstance(tick.get("_collisionGrid"), dict) else {}
    collision = navigation_packet.get("collision") if isinstance(navigation_packet.get("collision"), dict) else {}
    grid_collision = collision_grid.get("collision") if isinstance(collision_grid.get("collision"), dict) else {}
    nav_player = navigation_packet.get("player") if isinstance(navigation_packet.get("player"), dict) else {}
    bounds = navigation_packet.get("bounds") if isinstance(navigation_packet.get("bounds"), dict) else {}
    source = navigation_packet.get("source") if isinstance(navigation_packet.get("source"), dict) else {}
    collision_known = collision.get("collisionKnown")
    if collision_known is None and grid_collision:
        collision_known = grid_collision.get("collisionKnown")
    plane = collision.get("plane")
    if plane is None:
        plane = navigation_packet.get("plane", player.get("plane") if player else tick.get("plane"))
    map_width = collision.get("mapWidth", grid_collision.get("mapWidth"))
    map_height = collision.get("mapHeight", grid_collision.get("mapHeight"))
    player_scene_x = nav_player.get("sceneX", player.get("sceneX"))
    player_scene_y = nav_player.get("sceneY", player.get("sceneY"))
    notes = []
    warnings = []
    if navigation_packet:
        notes.append("Collision summary is available from compact live navigation packets.")
        notes.append("Full route planning is not implemented in this read-only QA layer.")
    else:
        warnings.append("collision/navigation packet unavailable; reachability questions cannot be answered yet")
        notes.append("Navigation profiles can use target/player tile fields now; collision-aware pathing needs compact navigation packets.")
    if collision_known and not collision_grid:
        notes.append("Collision hash/count summary is known; full collision grid pathing is not available.")
    collision_window_available = bool(collision_window.get("flags"))
    return {
        "schema": LIVE_NAVIGATION_SCHEMA,
        "generatedAtUtc": processed_at,
        "latestTick": tick_id_for(tick),
        "collisionKnown": bool(collision_known) if collision_known is not None else False,
        "plane": plane,
        "playerWorldX": nav_player.get("worldX", player.get("worldX")),
        "playerWorldY": nav_player.get("worldY", player.get("worldY")),
        "playerPlane": nav_player.get("plane", player.get("plane")),
        "playerSceneX": player_scene_x,
        "playerSceneY": player_scene_y,
        "playerTileKnown": player_scene_x is not None and player_scene_y is not None,
        "mapBounds": {
            "sceneMinX": bounds.get("sceneMinX"),
            "sceneMaxX": bounds.get("sceneMaxX"),
            "sceneMinY": bounds.get("sceneMinY"),
            "sceneMaxY": bounds.get("sceneMaxY"),
        } if bounds else None,
        "mapWidth": map_width,
        "mapHeight": map_height,
        "blockedMovementTileCount": collision.get("blockedMovementTileCount", grid_collision.get("blockedMovementTileCount")),
        "blockedFullTileCount": collision.get("blockedFullTileCount", grid_collision.get("blockedFullTileCount")),
        "collisionHash": collision.get("collisionHash", grid_collision.get("collisionHash")),
        "signature": collision.get("collisionHash", grid_collision.get("collisionHash")),
        "collisionMapVersion": collision.get("collisionMapVersion", grid_collision.get("collisionMapVersion")),
        "obstaclesKnown": bool(collision_known),
        "collisionWindowAvailable": collision_window_available,
        "collisionWindowRadius": collision_window.get("windowRadius"),
        "collisionWindowBounds": {
            "minSceneX": collision_window.get("minSceneX"),
            "maxSceneX": collision_window.get("maxSceneX"),
            "minSceneY": collision_window.get("minSceneY"),
            "maxSceneY": collision_window.get("maxSceneY"),
            "width": collision_window.get("width"),
            "height": collision_window.get("height"),
        } if collision_window else None,
        "collisionWindowHash": collision_window.get("collisionWindowHash") or collision_window.get("windowHash"),
        "collisionWindowTick": tick_id_for(tick) if collision_window else None,
        "collisionWindowTileCount": collision_window.get("collisionWindowTileCount"),
        "collisionWindowEncoding": collision_window.get("encoding"),
        "collisionWindow": collision_window if collision_window_available else None,
        "reachabilityComputed": collision_window_available,
        "fullCollisionGridAvailable": bool(grid_collision.get("flags")),
        "notes": notes,
        "warnings": warnings,
        "source": "compact-packets" if navigation_packet else tick.get("_inputSource", RAW_TICK_SOURCE),
        "sourceDetails": source,
    }


def candidate_navigation_for(candidate: dict, navigation: dict) -> dict:
    target_scene_x = candidate.get("sceneX")
    target_scene_y = candidate.get("sceneY")
    target_plane = candidate.get("plane")
    player_scene_x = navigation.get("playerSceneX")
    player_scene_y = navigation.get("playerSceneY")
    player_plane = navigation.get("playerPlane", navigation.get("plane"))
    collision_known = navigation.get("collisionKnown")
    player_tile_known = player_scene_x is not None and player_scene_y is not None
    target_tile_known = target_scene_x is not None and target_scene_y is not None
    same_plane = player_plane is not None and target_plane is not None and player_plane == target_plane
    distance = candidate.get("distanceTiles")
    if distance is None and player_tile_known and target_tile_known:
        distance = max(abs(target_scene_x - player_scene_x), abs(target_scene_y - player_scene_y))
    missing = []
    if not collision_known:
        missing.append("collisionSummary")
    if not player_tile_known:
        missing.append("playerTile")
    if not target_tile_known:
        missing.append("targetTile")
    if not same_plane:
        missing.append("samePlane") if player_plane is not None and target_plane is not None else missing.append("plane")
    direct = "unknown"
    confidence = 0.0
    evidence = []
    window_payload = navigation.get("collisionWindow") if isinstance(navigation.get("collisionWindow"), dict) else None
    target_in_window = None
    if window_payload:
        min_x = window_payload.get("minSceneX")
        max_x = window_payload.get("maxSceneX")
        min_y = window_payload.get("minSceneY")
        max_y = window_payload.get("maxSceneY")
        if all(isinstance(value, int) for value in (min_x, max_x, min_y, max_y)) and target_tile_known:
            target_in_window = min_x <= target_scene_x <= max_x and min_y <= target_scene_y <= max_y
    path_length = None
    checked_tiles = None
    conservative_mode = True
    interaction_radius = 1
    if collision_known and player_tile_known and target_tile_known and same_plane and window_payload:
        interaction_radius = 2 if candidate.get("targetType") == "sceneObject" else 1
        reachability = navigation_reachability.reachability_for_target(
            window_payload,
            player_scene_x=player_scene_x,
            player_scene_y=player_scene_y,
            player_plane=player_plane,
            target_scene_x=target_scene_x,
            target_scene_y=target_scene_y,
            target_plane=target_plane,
            interaction_radius=interaction_radius,
        )
        direct = reachability.get("directReachability", "unknown")
        confidence = reachability.get("confidence", 0.0)
        path_length = reachability.get("pathLengthTiles")
        checked_tiles = reachability.get("checkedTiles")
        conservative_mode = bool(reachability.get("conservativeMode", True))
        evidence.extend(reachability.get("reachabilityEvidence") or [])
        if reachability.get("reason"):
            evidence.append(reachability.get("reason"))
        missing.extend(reachability.get("missingNavigationFields") or [])
    elif collision_known and player_tile_known and target_tile_known and same_plane:
        evidence.append("collision summary, player tile, and target tile are known")
        if navigation.get("fullCollisionGridAvailable"):
            direct = "unknown"
            confidence = 0.35
            evidence.append("full collision grid is present, but pathing is not implemented in this pass")
            missing.append("pathfinding")
        else:
            direct = "unknown"
            confidence = 0.25
            evidence.append("collision summary is available, but full grid pathing is not available")
            missing.append("collisionGridPathing")
    return {
        "collisionKnown": bool(collision_known),
        "collisionWindowAvailable": bool(window_payload),
        "targetInCollisionWindow": target_in_window,
        "playerTileKnown": player_tile_known,
        "targetTileKnown": target_tile_known,
        "samePlane": same_plane,
        "distanceTiles": distance,
        "directReachability": direct,
        "pathLengthTiles": path_length,
        "checkedTiles": checked_tiles,
        "interactionRadiusTiles": interaction_radius,
        "reachabilityConfidence": confidence,
        "reachabilityEvidence": evidence,
        "missingNavigationFields": sorted(set(missing)),
        "conservativeMode": conservative_mode,
    }


def apply_navigation_to_candidates(candidates: list[dict], navigation: dict) -> list[dict]:
    return [
        dict(candidate, navigation=candidate_navigation_for(candidate, navigation))
        for candidate in candidates
    ]


def woodcutting_state_for(activity: dict, inventory_state: dict, candidates: list[dict], liveness_summary: dict) -> dict:
    evidence = []
    warnings = []
    if inventory_state.get("inventoryFull") is True:
        return {
            "woodcuttingState": "inventory_full",
            "confidence": 0.9,
            "evidence": ["inventory freeSlots=0"],
            "warnings": warnings,
        }
    if liveness_summary.get("candidatesSuppressedAsDepleted"):
        state = "target_depleted"
        confidence = 0.85
        evidence.append("one or more tree-like candidates were suppressed as depleted/stump")
        if candidates:
            evidence.append("new live candidate is available after suppression")
        return {"woodcuttingState": state, "confidence": confidence, "evidence": evidence, "warnings": warnings}
    if inventory_state.get("changedThisTick") or inventory_state.get("changedRecently"):
        return {
            "woodcuttingState": "inventory_changed",
            "confidence": 0.8,
            "evidence": ["inventory signature changed recently"],
            "warnings": warnings,
        }
    apparent = activity.get("apparentState")
    if apparent == "animating" and candidates:
        return {
            "woodcuttingState": "likely_chopping",
            "confidence": 0.65,
            "evidence": ["local player is animating", "tree-like candidate is available"],
            "warnings": warnings,
        }
    if apparent == "moving":
        return {
            "woodcuttingState": "likely_moving",
            "confidence": 0.55,
            "evidence": ["movement field indicates moving"],
            "warnings": warnings,
        }
    if apparent == "idle" and candidates:
        return {
            "woodcuttingState": "likely_idle",
            "confidence": 0.55,
            "evidence": ["idle animation observed", "live tree-like candidate is available"],
            "warnings": warnings,
        }
    if not candidates:
        warnings.append("no live tree-like candidates available for woodcutting state heuristic.")
    return {
        "woodcuttingState": "unknown",
        "confidence": 0.2,
        "evidence": evidence,
        "warnings": warnings,
    }


def activity_state_for(
    latest_tick: dict | None,
    ticks: list[dict],
    candidates: list[dict],
    liveness_summary: dict,
    processed_at: str,
    build_duration_ms: float,
    *,
    inventory_state: dict | None = None,
) -> dict:
    started = time.perf_counter()
    latest_tick = latest_tick or {}
    player = local_player_for(latest_tick)
    status = latest_tick.get("status") if isinstance(latest_tick.get("status"), dict) else {}
    inventory_state = inventory_state or inventory_state_for_ticks(ticks, latest_tick)
    equipment_state = equipment_state_for_tick(latest_tick)
    activity = apparent_activity_for_tick(latest_tick, inventory_state, liveness_summary)
    recent_activity_events = activity_events_for_ticks(ticks, latest_tick)
    woodcutting = woodcutting_state_for(activity, inventory_state, candidates, liveness_summary)
    elapsed = build_duration_ms or (time.perf_counter() - started) * 1000.0
    return {
        "schema": LIVE_ACTIVITY_SCHEMA,
        "generatedAtUtc": processed_at,
        "latestTick": tick_id_for(latest_tick),
        "player": {
            "worldX": player.get("worldX"),
            "worldY": player.get("worldY"),
            "plane": player.get("plane"),
            "sceneX": player.get("sceneX"),
            "sceneY": player.get("sceneY"),
            "localX": player.get("localX"),
            "localY": player.get("localY"),
            "animation": player.get("animation"),
            "poseAnimation": player.get("poseAnimation"),
            "animationFrame": player.get("animationFrame"),
            "interacting": {
                "type": status.get("interactingType"),
                "index": status.get("interactingIndex"),
                "id": status.get("interactingId"),
                "name": status.get("interactingName"),
                "worldX": status.get("interactingWorldX"),
                "worldY": status.get("interactingWorldY"),
                "plane": status.get("interactingPlane"),
            },
            "isMoving": player.get("isMoving"),
            "runEnergy": status.get("runEnergyPercent") if status.get("runEnergyPercent") is not None else latest_tick.get("runEnergy"),
            "healthRatio": status.get("localHealthRatio"),
            "healthScale": status.get("localHealthScale"),
            "hitpointsBoosted": status.get("hitpointsBoosted"),
            "hitpointsReal": status.get("hitpointsReal"),
        },
        "inventory": inventory_state,
        "inventoryState": inventory_state,
        "recentInventoryDeltas": inventory_state.get("recentItemDeltas", []),
        "equipment": equipment_state,
        "targetLiveness": {
            "activeCandidateLiveState": candidates[0].get("targetLiveState") if candidates else None,
            "bestCandidateLiveState": candidates[0].get("targetLiveState") if candidates else None,
            **liveness_summary,
        },
        "activity": activity,
        "activityState": activity,
        "recentActivityEvents": recent_activity_events,
        "woodcuttingState": woodcutting,
        "performance": {
            "buildDurationMillis": round(elapsed, 3),
        },
        "safety": {
            "readOnly": True,
            "actionGenerated": False,
            "inputGenerated": False,
        },
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
        if not hasattr(self.args, "input_source"):
            self.args.input_source = "auto"
        if not hasattr(self.args, "event_limit"):
            self.args.event_limit = 200
        self.compact_packet_state = compact_packet_state(session)
        (
            self.input_source_active,
            self.compact_packets_available,
            self.raw_ticks_available,
            self.input_fallback_reason,
        ) = choose_input_source(session, args.input_source)
        self.compact_packets_recent = bool(self.compact_packet_state.get("recent"))
        self.tailer = CompactPacketTailer(session) if self.input_source_active == COMPACT_PACKET_SOURCE else TickJsonlTailer(session)
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
        self.last_raw_tick_add_millis = 0.0
        self.previous_update_overran = False
        self.backlog_drain_count = 0
        self.last_backlog_drain_tick = None
        self.last_backlog_drain_reason = None
        self.last_activity_used_rolling_scan = False
        self.last_inventory_used_rolling_scan = False
        self.last_liveness_cache_hits = 0
        self.last_liveness_cache_misses = 0
        self.last_source_records_considered = 0
        self.last_source_records_prefiltered_out = 0
        self.last_classification_cache_hits = 0
        self.last_classification_cache_misses = 0
        self.last_classification_cache_invalidations = 0
        self.last_candidate_tick_cache_hits = 0
        self.last_candidate_tick_cache_misses = 0
        self.last_old_ticks_dropped_from_candidate_cache = 0
        self.classification_cache: dict[tuple, dict] = {}
        self.recently_unavailable_targets: dict[str, dict] = {}
        self.last_recently_unavailable_count = 0
        self.last_recently_depleted_count = 0
        self.last_recently_unavailable_pruned = 0
        self.last_recently_unavailable_cache_over_limit = False
        self.last_candidates_suppressed_by_liveness = 0
        self.last_candidates_suppressed_as_depleted = 0
        self.last_candidates_revived_after_respawn = 0
        self.last_liveness_budget_exceeded = False
        self.last_liveness_degraded = False
        self.last_liveness_candidates_checked = 0
        self.last_liveness_candidates_skipped_by_budget = 0
        self.last_liveness_visible_ref_scan_count = 0
        self.last_liveness_full_scan_count = 0
        self.last_liveness_mode_warning = None
        self.last_prune_tick = None
        self.previous_best_candidate: dict | None = None
        self.last_best_candidate_change: dict = {}
        self.event_state: dict[str, object] = {}
        self.event_timeline: deque[dict] = deque(
            read_jsonl_objects(live_output_paths(session)["events"], limit=max(1, int(self.args.event_limit or 200))),
            maxlen=max(1, int(self.args.event_limit or 200)),
        )
        self.total_write_retries = 0
        self.total_write_failures = 0
        self.performance_history: deque[dict] = deque(maxlen=100)
        self.last_compact_packets_seen = 0
        self.last_compact_packets_processed = 0
        self.last_compact_packets_coalesced = 0
        self.last_compact_packet_last_sequence = None
        self.last_compact_packet_latest_segment = None
        self.last_compact_packet_rollover_count = 0
        self.last_compact_packet_read_errors = 0

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

    def compact_input_warnings(self) -> list[str]:
        if self.input_source_active != COMPACT_PACKET_SOURCE:
            return []

        warnings = []
        target_types = target_types_for_profile(self.args, self.profile)
        unsupported = sorted(target_types - {"sceneObject"})
        if unsupported:
            warnings.append(
                "compact packet input currently builds live candidates from scene projection/delta packets; "
                f"these target types may be missing until compact packets include them: {', '.join(unsupported)}"
            )
        if not self.compact_packets_available:
            warnings.append("compact packet input selected but no compact live packet files are currently available")
        elif not self.compact_packets_recent:
            warnings.append("compact packet input is available but stale; collect fresh packets for normal live mode")
        return warnings

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
        if isinstance(self.tailer, CompactPacketTailer):
            records = self.tailer.read_existing_records(limit)
            added, _dropped = self.add_ticks(records)
            self.tailer.seek_to_end()
            return added

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

    def ticks_for_realtime_state_update(self, selected_ticks: list[dict], processing_ticks: list[dict], output_ticks: list[dict]) -> list[dict]:
        if self.args.latency_mode != "realtime":
            self.last_activity_used_rolling_scan = True
            self.last_inventory_used_rolling_scan = True
            return selected_ticks

        self.last_activity_used_rolling_scan = False
        self.last_inventory_used_rolling_scan = False
        if output_ticks:
            return output_ticks[-1:]
        if processing_ticks:
            return processing_ticks[-1:]
        return selected_ticks[-1:]

    def unavailable_suppress_until(self, tick_id: int | None) -> int | None:
        if tick_id is None:
            return None
        return tick_id + max(1, int(self.args.depleted_suppress_ticks))

    def mark_unavailable(self, keys: list[str], tick_id: int | None, reason: str, state: str, source: dict, class_info: dict | None, evidence: list[str]) -> None:
        if not keys:
            return
        suppress_until = self.unavailable_suppress_until(tick_id)
        class_id = class_info.get("classId") if isinstance(class_info, dict) else None
        record = {
            "unavailableSinceTick": tick_id,
            "lastSeenLiveTick": source.get("lastSeenTick"),
            "reason": reason,
            "targetLiveState": state,
            "targetLiveEvidence": evidence,
            "replacementObjectId": source.get("id"),
            "replacementObjectName": source_name(source),
            "replacementObjectCategory": None,
            "suppressUntilTick": suppress_until,
            "profileId": self.args.profile,
            "classId": class_id,
            "objectKey": source.get("objectKey"),
            "worldX": source.get("worldX"),
            "worldY": source.get("worldY"),
            "plane": source.get("plane"),
            "sceneX": source.get("sceneX"),
            "sceneY": source.get("sceneY"),
        }
        for key in keys:
            self.recently_unavailable_targets[key] = dict(record)

    def clear_unavailable(self, keys: list[str]) -> None:
        cleared = 0
        for key in keys:
            if key in self.recently_unavailable_targets:
                self.recently_unavailable_targets.pop(key, None)
                cleared += 1
        self.last_candidates_revived_after_respawn += cleared

    def prune_unavailable(self, current_tick: int | None, *, force: bool = False) -> None:
        self.last_recently_unavailable_pruned = 0
        self.last_recently_unavailable_cache_over_limit = False
        if current_tick is None and not force:
            return
        max_items = max(1, int(self.args.max_recently_unavailable))
        should_prune = force or self.last_prune_tick is None or (isinstance(current_tick, int) and current_tick >= self.last_prune_tick + 10)
        if not should_prune and len(self.recently_unavailable_targets) <= max_items:
            return

        expired = []
        if current_tick is not None:
            expired = [
                key
                for key, value in self.recently_unavailable_targets.items()
                if isinstance(value.get("suppressUntilTick"), int) and value.get("suppressUntilTick") < current_tick
            ]
        for key in expired:
            self.recently_unavailable_targets.pop(key, None)
        self.last_recently_unavailable_pruned += len(expired)

        if len(self.recently_unavailable_targets) > max_items:
            self.last_recently_unavailable_cache_over_limit = True
            ordered = sorted(
                self.recently_unavailable_targets.items(),
                key=lambda item: (
                    item[1].get("suppressUntilTick") if isinstance(item[1].get("suppressUntilTick"), int) else 10**12,
                    item[1].get("unavailableSinceTick") if isinstance(item[1].get("unavailableSinceTick"), int) else 10**12,
                    item[0],
                ),
            )
            remove_count = len(self.recently_unavailable_targets) - max_items
            for key, _value in ordered[:remove_count]:
                self.recently_unavailable_targets.pop(key, None)
            self.last_recently_unavailable_pruned += remove_count

        if isinstance(current_tick, int):
            self.last_prune_tick = current_tick

    def expire_unavailable(self, current_tick: int | None) -> None:
        self.prune_unavailable(current_tick)

    def update_liveness_from_tick_full(self, tick: dict) -> None:
        tick_id = tick_id_for(tick)
        self.prune_unavailable(tick_id)

        for source in world_builder.scene_object_sources_for_tick(tick):
            if not isinstance(source, dict):
                continue
            self.last_liveness_full_scan_count += 1
            depleted, evidence, class_info = source_depleted_by_name_or_actions(tick, source, self.target_overrides, self.library)
            keys = source_identity_keys(source)
            if depleted:
                self.mark_unavailable(keys, tick_id, "source looks depleted or lacks useful action", "depleted_or_stump", source, class_info, evidence)
            else:
                self.clear_unavailable(keys)

        self.update_liveness_from_tick_delta(tick)

    def update_liveness_from_tick_delta(self, tick: dict) -> None:
        tick_id = tick_id_for(tick)
        self.prune_unavailable(tick_id)
        deltas = tick.get("sceneObjectDeltas") if isinstance(tick.get("sceneObjectDeltas"), dict) else {}
        for source in deltas.get("despawnedObjects") or []:
            if not isinstance(source, dict):
                continue
            class_info = source_class_info(tick, source, self.target_overrides, self.library)
            self.mark_unavailable(
                source_identity_keys(source),
                tick_id,
                "scene object despawned",
                "recently_despawned",
                source,
                class_info,
                ["sceneObjectDeltas.despawnedObjects"],
            )

        for field in ("newObjects", "updatedObjects"):
            for source in deltas.get(field) or []:
                if not isinstance(source, dict):
                    continue
                depleted, evidence, class_info = source_depleted_by_name_or_actions(tick, source, self.target_overrides, self.library)
                if depleted or source.get("present") is False:
                    state = "depleted_or_stump" if depleted else "recently_despawned"
                    reason = "replacement object appears depleted/stump" if depleted else "scene object marked not present"
                    self.mark_unavailable(source_identity_keys(source), tick_id, reason, state, source, class_info, evidence or [field])
                else:
                    self.clear_unavailable(source_identity_keys(source))

        unique = {
            (
                value.get("objectKey"),
                value.get("worldX"),
                value.get("worldY"),
                value.get("plane"),
                value.get("targetLiveState"),
            ): value
            for value in self.recently_unavailable_targets.values()
        }
        self.last_recently_unavailable_count = len(unique)
        self.last_recently_depleted_count = sum(1 for value in unique.values() if value.get("targetLiveState") == "depleted_or_stump")

    def update_liveness_from_ticks(self, ticks: list[dict]) -> None:
        self.last_candidates_revived_after_respawn = 0
        self.last_liveness_full_scan_count = 0
        self.last_liveness_visible_ref_scan_count = 0
        mode = self.args.liveness_mode
        if mode == "off":
            return
        if mode == "basic":
            latest = ticks[-1] if ticks else None
            self.prune_unavailable(tick_id_for(latest) if latest else None)
            return
        ordered = sorted(ticks, key=lambda item: tick_id_for(item) if tick_id_for(item) is not None else -1)
        if mode == "delta":
            if ordered:
                self.update_liveness_from_tick_delta(ordered[-1])
            return
        for tick in ordered:
            self.update_liveness_from_tick_full(tick)

    def liveness_for_candidate(self, candidate: dict) -> dict:
        tick_id = candidate.get("tickId") if isinstance(candidate.get("tickId"), int) else candidate.get("tick")
        mode = self.args.liveness_mode
        if mode == "off":
            return {
                "targetLiveState": "unknown",
                "targetLiveStateConfidence": 0.0,
                "targetLiveEvidence": ["liveness disabled"],
                "suppressUntilTick": None,
                "suppressReason": None,
            }
        self.expire_unavailable(tick_id if isinstance(tick_id, int) else None)
        evidence = []
        unavailable = None
        unavailable_matches = []
        for key in candidate_identity_keys(candidate):
            value = self.recently_unavailable_targets.get(key)
            if not value:
                continue
            suppress_until = value.get("suppressUntilTick")
            if isinstance(tick_id, int) and isinstance(suppress_until, int) and suppress_until < tick_id:
                continue
            unavailable_matches.append(value)
        if unavailable_matches:
            unavailable = next((value for value in unavailable_matches if value.get("targetLiveState") == "depleted_or_stump"), unavailable_matches[0])

        depleted, depleted_evidence = candidate_depleted_by_name_or_actions(candidate, self.library)
        target = candidate.get("target") if isinstance(candidate.get("target"), dict) else {}
        present = target.get("present")
        if unavailable:
            evidence.extend(unavailable.get("targetLiveEvidence") or [])
            state = unavailable.get("targetLiveState") or "stale"
            confidence = 0.9 if state in {"recently_despawned", "depleted_or_stump"} else 0.7
            return {
                "targetLiveState": state,
                "targetLiveStateConfidence": confidence,
                "targetLiveEvidence": evidence or [unavailable.get("reason")],
                "lastSeenTick": target.get("lastSeenTick"),
                "lastChangedTick": target.get("lastUpdatedTick"),
                "lastDespawnedTick": target.get("despawnedTick") or unavailable.get("unavailableSinceTick"),
                "replacementObjectId": unavailable.get("replacementObjectId"),
                "replacementObjectName": unavailable.get("replacementObjectName"),
                "replacementObjectCategory": unavailable.get("replacementObjectCategory"),
                "suppressUntilTick": unavailable.get("suppressUntilTick"),
                "suppressReason": unavailable.get("reason"),
            }
        if present is False:
            return {
                "targetLiveState": "recently_despawned",
                "targetLiveStateConfidence": 0.85,
                "targetLiveEvidence": ["target.present=false"],
                "lastSeenTick": target.get("lastSeenTick"),
                "lastChangedTick": target.get("lastUpdatedTick"),
                "lastDespawnedTick": target.get("despawnedTick"),
                "suppressUntilTick": self.unavailable_suppress_until(tick_id if isinstance(tick_id, int) else None),
                "suppressReason": "target is marked not present",
            }
        if depleted:
            return {
                "targetLiveState": "depleted_or_stump",
                "targetLiveStateConfidence": 0.8,
                "targetLiveEvidence": depleted_evidence,
                "lastSeenTick": target.get("lastSeenTick"),
                "lastChangedTick": target.get("lastUpdatedTick"),
                "lastDespawnedTick": target.get("despawnedTick"),
                "suppressUntilTick": self.unavailable_suppress_until(tick_id if isinstance(tick_id, int) else None),
                "suppressReason": "candidate appears depleted/stump or lacks useful action",
            }
        if mode in {"basic", "delta"}:
            assumed_state = "live_assumed" if target.get("targetType") == "sceneObject" else "unknown"
            return {
                "targetLiveState": assumed_state,
                "targetLiveStateConfidence": 0.55 if assumed_state == "live_assumed" else 0.25,
                "targetLiveEvidence": ["no direct depletion delta seen"] if assumed_state == "live_assumed" else ["no direct liveness evidence"],
                "lastSeenTick": target.get("lastSeenTick"),
                "lastChangedTick": target.get("lastUpdatedTick"),
                "lastDespawnedTick": target.get("despawnedTick"),
                "suppressUntilTick": None,
                "suppressReason": None,
            }
        return {
            "targetLiveState": "live" if target.get("targetType") == "sceneObject" else "unknown",
            "targetLiveStateConfidence": 0.8 if target.get("targetType") == "sceneObject" else 0.35,
            "targetLiveEvidence": ["candidate present in current live candidate source"],
            "lastSeenTick": target.get("lastSeenTick"),
            "lastChangedTick": target.get("lastUpdatedTick"),
            "lastDespawnedTick": target.get("despawnedTick"),
            "suppressUntilTick": None,
            "suppressReason": None,
        }

    def apply_liveness_to_candidates(self, candidates: list[dict]) -> tuple[list[dict], dict]:
        kept = []
        suppressed = 0
        depleted = 0
        live_state_counts = Counter()
        started = time.perf_counter()
        budget_ms = float(self.args.liveness_budget_ms)
        budget_applies = self.args.latency_mode == "realtime" and self.args.liveness_mode != "full"
        self.last_liveness_cache_hits = 0
        self.last_liveness_cache_misses = 0
        self.last_liveness_budget_exceeded = False
        self.last_liveness_degraded = False
        self.last_liveness_candidates_checked = 0
        self.last_liveness_candidates_skipped_by_budget = 0
        for index, candidate in enumerate(candidates):
            if budget_applies and (time.perf_counter() - started) * 1000.0 > budget_ms:
                self.last_liveness_budget_exceeded = True
                self.last_liveness_degraded = True
                self.last_liveness_candidates_skipped_by_budget = len(candidates) - index
                for remaining in candidates[index:]:
                    state = "live_assumed" if self.args.liveness_mode in {"basic", "delta"} else "unknown"
                    remaining.update(
                        {
                            "targetLiveState": state,
                            "targetLiveStateConfidence": 0.25 if state == "unknown" else 0.45,
                            "targetLiveEvidence": ["liveness budget exceeded; state degraded"],
                        }
                    )
                    if state == "unknown":
                        remaining["negativeSignals"] = sorted(set((remaining.get("negativeSignals") or []) + ["livenessUnknown"]))
                    kept.append(remaining)
                    live_state_counts[state] += 1
                break

            keys = candidate_identity_keys(candidate)
            if any(key in self.recently_unavailable_targets for key in keys):
                self.last_liveness_cache_hits += 1
            else:
                self.last_liveness_cache_misses += 1
            self.last_liveness_candidates_checked += 1
            info = self.liveness_for_candidate(candidate)
            candidate.update(info)
            live_state = info.get("targetLiveState") or "unknown"
            live_state_counts[live_state] += 1
            if live_state in {"recently_despawned", "depleted_or_stump", "stale", "changed"}:
                candidate["negativeSignals"] = sorted(set((candidate.get("negativeSignals") or []) + [live_state, "suppressedByLiveness"]))
                candidate["rejectReasons"] = sorted(set((candidate.get("rejectReasons") or []) + [info.get("suppressReason") or live_state]))
                suppressed += 1
                if live_state == "depleted_or_stump":
                    depleted += 1
                continue
            if live_state == "unknown":
                candidate["negativeSignals"] = sorted(set((candidate.get("negativeSignals") or []) + ["livenessUnknown"]))
            kept.append(candidate)
        self.last_candidates_suppressed_by_liveness = suppressed
        self.last_candidates_suppressed_as_depleted = depleted
        return kept, {
            "candidatesSuppressedByLiveness": suppressed,
            "candidatesSuppressedAsDepleted": depleted,
            "candidateLiveStateCounts": dict(live_state_counts.most_common()),
            "livenessBudgetExceeded": self.last_liveness_budget_exceeded,
            "livenessDegraded": self.last_liveness_degraded,
            "livenessCandidatesChecked": self.last_liveness_candidates_checked,
            "livenessCandidatesSkippedByBudget": self.last_liveness_candidates_skipped_by_budget,
        }

    def update_best_candidate_change(self, candidates: list[dict]) -> None:
        current = best_candidate_summary(candidates[0] if candidates else None)
        previous = self.previous_best_candidate
        changed = previous != current
        reason = None
        if changed and previous and current:
            reason = "best candidate identity or rank changed"
        elif changed and previous and not current:
            reason = "previous best candidate no longer available"
        elif changed and current and not previous:
            reason = "first best candidate observed"
        self.last_best_candidate_change = {
            "previousBestCandidate": previous,
            "currentBestCandidate": current,
            "bestCandidateChanged": changed,
            "bestCandidateChangeReason": reason,
            "previousBestSuppressedReason": None if current == previous else "liveness/profile filtering may have changed the best candidate",
        }
        self.previous_best_candidate = current

    def timeline_event(
        self,
        *,
        processed_at: str,
        tick: int | None,
        event_type: str,
        severity: str,
        summary: str,
        details: dict | None = None,
        related_candidate: dict | None = None,
        previous_value=None,
        current_value=None,
    ) -> dict:
        return {
            "schema": LIVE_EVENT_SCHEMA,
            "generatedAtUtc": processed_at,
            "tick": tick,
            "eventType": event_type,
            "severity": severity,
            "summary": summary,
            "details": details or {},
            "relatedCandidate": candidate_timeline_summary(related_candidate),
            "previousValue": previous_value,
            "currentValue": current_value,
            "source": "live_target_processor",
            "profile": self.args.profile,
        }

    def append_timeline_event(self, event: dict) -> None:
        fingerprint = (
            event.get("tick"),
            event.get("eventType"),
            event_value_key(event.get("previousValue")),
            event_value_key(event.get("currentValue")),
            event.get("summary"),
        )
        last = self.event_timeline[-1] if self.event_timeline else None
        last_fingerprint = None
        if last:
            last_fingerprint = (
                last.get("tick"),
                last.get("eventType"),
                event_value_key(last.get("previousValue")),
                event_value_key(last.get("currentValue")),
                last.get("summary"),
            )
        if fingerprint != last_fingerprint:
            self.event_timeline.append(event)

    def emit_timeline_events(
        self,
        *,
        latest_tick_record: dict | None,
        candidates: list[dict],
        inventory_state: dict,
        activity: dict,
        status: dict,
        processed_at: str,
        navigation: dict | None = None,
    ) -> list[dict]:
        tick = tick_id_for(latest_tick_record or {}) or status.get("latestTick") or status.get("lastProcessedTick")
        events_before = len(self.event_timeline)

        def changed(key: str, current) -> tuple[bool, object]:
            previous = self.event_state.get(key)
            current_key = event_value_key(current)
            if previous == current_key:
                return False, None
            self.event_state[key] = current_key
            return True, previous

        best = candidates[0] if candidates else None
        nearest = nearest_timeline_candidate(candidates)
        best_key = candidate_timeline_key(best)
        nearest_key = candidate_timeline_key(nearest)

        candidate_count = int(status.get("candidateCount") if status.get("candidateCount") is not None else len(candidates))
        count_changed, count_previous = changed("candidateCount", candidate_count)
        if count_changed and count_previous is not None and candidate_count_change_significant(count_previous, candidate_count):
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="candidate_count_changed",
                    severity="info",
                    summary=f"Candidate count changed: {candidate_count}",
                    previous_value=count_previous,
                    current_value=candidate_count,
                )
            )

        best_changed, best_previous = changed("bestCandidate", best_key)
        if best_changed and (best_previous is not None or best_key is not None):
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="best_candidate_changed",
                    severity="info",
                    summary=f"Best candidate changed: {candidate_timeline_label(best)}",
                    details={"reason": self.last_best_candidate_change.get("bestCandidateChangeReason")},
                    related_candidate=best,
                    previous_value=best_previous,
                    current_value=best_key,
                )
            )

        best_aim = candidate_aim_bucket(best)
        aim_changed, aim_previous = changed("bestCandidateAimPoint", {"candidate": best_key, "aim": best_aim})
        if aim_changed and aim_previous is not None and best_aim is not None and not best_changed:
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="best_candidate_aim_point_changed",
                    severity="info",
                    summary=f"Best candidate aim point changed: {candidate_timeline_label(best)}",
                    details={"aimPoint": candidate_aim_point(best), "bucketPixels": 8},
                    related_candidate=best,
                    previous_value=aim_previous,
                    current_value=candidate_aim_point(best),
                )
            )

        nearest_changed, nearest_previous = changed("nearestCandidate", nearest_key)
        if nearest_changed and (nearest_previous is not None or nearest_key is not None):
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="nearest_candidate_changed",
                    severity="info",
                    summary=f"Nearest candidate changed: {candidate_timeline_label(nearest)}",
                    related_candidate=nearest,
                    previous_value=nearest_previous,
                    current_value=nearest_key,
                )
            )

        for role, candidate in (("best", best), ("nearest", nearest)):
            key = candidate_timeline_key(candidate)
            nav = candidate.get("navigation") if isinstance(candidate, dict) and isinstance(candidate.get("navigation"), dict) else {}
            reachability = nav.get("directReachability")
            reach_changed, reach_previous = changed(f"{role}CandidateReachability", {"candidate": key, "reachability": reachability})
            if reach_changed and reach_previous is not None:
                self.append_timeline_event(
                    self.timeline_event(
                        processed_at=processed_at,
                        tick=tick,
                        event_type=f"{role}_candidate_reachability_changed",
                        severity="warn" if reachability == "blocked" else "info",
                        summary=f"{role.title()} candidate reachability changed: {reachability or 'unknown'}",
                        related_candidate=candidate,
                        previous_value=reach_previous,
                        current_value=reachability,
                    )
                )
            in_window = nav.get("targetInCollisionWindow")
            window_changed, window_previous = changed(f"{role}CandidateInCollisionWindow", {"candidate": key, "inWindow": in_window})
            if window_changed and window_previous is not None and in_window is False:
                self.append_timeline_event(
                    self.timeline_event(
                        processed_at=processed_at,
                        tick=tick,
                        event_type="target_outside_collision_window",
                        severity="warn",
                        summary=f"{role.title()} candidate moved outside collision window: {candidate_timeline_label(candidate)}",
                        related_candidate=candidate,
                        previous_value=window_previous,
                        current_value=in_window,
                    )
                )

        for candidate in candidates[: max(1, min(len(candidates), int(self.args.limit or 20)))]:
            key = candidate_timeline_key(candidate)
            if key is None:
                continue
            live_state = candidate.get("targetLiveState")
            live_changed, live_previous = changed(f"candidateLiveState:{key}", live_state)
            if live_changed and live_previous is not None:
                event_type = "target_depleted" if live_state == "depleted_or_stump" else "target_liveness_changed"
                severity = "warn" if live_state in {"recently_despawned", "depleted_or_stump", "stale", "changed"} else "info"
                summary = f"Target liveness changed: {candidate_timeline_label(candidate)} is {live_state or 'unknown'}"
                if live_state == "depleted_or_stump":
                    summary = f"Target depleted: {candidate_timeline_label(candidate)}"
                self.append_timeline_event(
                    self.timeline_event(
                        processed_at=processed_at,
                        tick=tick,
                        event_type=event_type,
                        severity=severity,
                        summary=summary,
                        related_candidate=candidate,
                        previous_value=live_previous,
                        current_value=live_state,
                    )
                )

            candidate_navigation = candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}
            reachability = candidate_navigation.get("directReachability")
            reach_changed, reach_previous = changed(f"candidateReachability:{key}", reachability)
            if reach_changed and reach_previous is not None:
                self.append_timeline_event(
                    self.timeline_event(
                        processed_at=processed_at,
                        tick=tick,
                        event_type="reachability_changed",
                        severity="warn" if reachability == "blocked" else "info",
                        summary=f"Reachability changed: {candidate_timeline_label(candidate)} is {reachability or 'unknown'}",
                        related_candidate=candidate,
                        previous_value=reach_previous,
                        current_value=reachability,
                    )
                )

        for unavailable_key, unavailable in list(self.recently_unavailable_targets.items())[:50]:
            if not isinstance(unavailable, dict):
                continue
            state = unavailable.get("targetLiveState") or unavailable.get("reason")
            did_change, previous = changed(f"recentlyUnavailable:{unavailable_key}", state)
            if did_change and previous is not None and state:
                event_type = "target_depleted" if state == "depleted_or_stump" else "target_liveness_changed"
                severity = "warn" if state in {"recently_despawned", "depleted_or_stump", "stale", "changed"} else "info"
                name = unavailable.get("name") or unavailable.get("replacementObjectName") or unavailable.get("classId") or "target"
                world_x = unavailable.get("worldX")
                world_y = unavailable.get("worldY")
                suffix = f" at {world_x},{world_y}" if world_x is not None and world_y is not None else ""
                self.append_timeline_event(
                    self.timeline_event(
                        processed_at=processed_at,
                        tick=tick,
                        event_type=event_type,
                        severity=severity,
                        summary=f"Target depleted: {name}{suffix}" if event_type == "target_depleted" else f"Target liveness changed: {name}{suffix} is {state}",
                        details=unavailable,
                        previous_value=previous,
                        current_value=state,
                    )
                )

        suppressed = status.get("candidatesSuppressedByLiveness")
        suppressed_changed, suppressed_previous = changed("candidatesSuppressedByLiveness", suppressed)
        if suppressed_changed and suppressed_previous is not None and suppressed:
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="liveness_suppressed_candidate",
                    severity="warn",
                    summary=f"Liveness suppressed {suppressed} candidate(s)",
                    previous_value=suppressed_previous,
                    current_value=suppressed,
                )
            )

        suppressed_depleted = status.get("candidatesSuppressedAsDepleted")
        suppressed_depleted_changed, suppressed_depleted_previous = changed("candidatesSuppressedAsDepleted", suppressed_depleted)
        if suppressed_depleted_changed and suppressed_depleted_previous is not None and suppressed_depleted:
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="depleted_candidate_suppressed",
                    severity="warn",
                    summary=f"Depleted/stale liveness suppressed {suppressed_depleted} candidate(s)",
                    previous_value=suppressed_depleted_previous,
                    current_value=suppressed_depleted,
                )
            )

        revived = status.get("candidatesRevivedAfterRespawn")
        revived_changed, revived_previous = changed("candidatesRevivedAfterRespawn", revived)
        if revived_changed and revived_previous is not None and revived:
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="candidate_revived",
                    severity="info",
                    summary=f"{revived} candidate(s) revived after respawn",
                    previous_value=revived_previous,
                    current_value=revived,
                )
            )

        inventory_signature = inventory_state.get("signature") or inventory_state.get("inventoryHash")
        signature_changed, signature_previous = changed("inventorySignature", inventory_signature)
        if signature_changed and signature_previous is not None:
            delta = (inventory_state.get("recentItemDeltas") or [None])[-1]
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="inventory_changed",
                    severity="info",
                    summary=f"Inventory changed: {inventory_delta_summary(delta)}",
                    details={"recentDelta": delta} if isinstance(delta, dict) else {},
                    previous_value=signature_previous,
                    current_value=inventory_signature,
                )
            )

        for key, event_type, label in (
            ("freeSlots", "inventory_free_slots_changed", "Inventory free slots changed"),
            ("inventoryFull", "inventory_full_changed", "Inventory full state changed"),
        ):
            current = inventory_state.get(key)
            did_change, previous = changed(key, current)
            if did_change and previous is not None:
                self.append_timeline_event(
                    self.timeline_event(
                        processed_at=processed_at,
                        tick=tick,
                        event_type=event_type,
                        severity="warn" if key == "inventoryFull" and current is True else "info",
                        summary=f"{label}: {current}",
                        previous_value=previous,
                        current_value=current,
                    )
                )

        activity_state = activity.get("activityState") if isinstance(activity.get("activityState"), dict) else activity.get("activity")
        activity_label = activity_state.get("apparentState") if isinstance(activity_state, dict) else None
        activity_changed, activity_previous = changed("activityState", activity_label)
        if activity_changed and activity_previous is not None:
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="activity_state_changed",
                    severity="info",
                    summary=f"Activity state changed: {activity_label or 'unknown'}",
                    previous_value=activity_previous,
                    current_value=activity_label,
                )
            )

        woodcutting = activity.get("woodcuttingState") if isinstance(activity.get("woodcuttingState"), dict) else {}
        woodcutting_label = woodcutting.get("woodcuttingState") if isinstance(woodcutting, dict) else None
        woodcutting_changed, woodcutting_previous = changed("woodcuttingState", woodcutting_label)
        if woodcutting_changed and woodcutting_previous is not None:
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="woodcutting_state_changed",
                    severity="info",
                    summary=f"Woodcutting state changed: {woodcutting_label or 'unknown'}",
                    details={"confidence": woodcutting.get("confidence"), "evidence": woodcutting.get("evidence") if isinstance(woodcutting, dict) else []},
                    previous_value=woodcutting_previous,
                    current_value=woodcutting_label,
                )
            )

        player = activity.get("player") if isinstance(activity.get("player"), dict) else {}
        animation = player.get("animation")
        animation_changed, animation_previous = changed("playerAnimation", animation)
        if animation_changed and animation_previous is not None:
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="player_animation_changed",
                    severity="info",
                    summary=f"Player animation changed: {animation}",
                    previous_value=animation_previous,
                    current_value=animation,
                )
            )

        interacting = player.get("interacting")
        interacting_changed, interacting_previous = changed("playerInteracting", interacting)
        if interacting_changed and interacting_previous is not None:
            name = interacting.get("name") if isinstance(interacting, dict) else interacting
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="interacting_target_changed",
                    severity="info",
                    summary=f"Interacting target changed: {name or 'none'}",
                    previous_value=interacting_previous,
                    current_value=interacting,
                )
            )

        navigation = navigation if isinstance(navigation, dict) else {}
        collision_available = navigation.get("collisionWindowAvailable")
        collision_changed, collision_previous = changed("collisionWindowAvailable", collision_available)
        if collision_changed and collision_previous is not None:
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="collision_window_availability_changed",
                    severity="warn" if not collision_available else "info",
                    summary=f"Collision window availability changed: {collision_available}",
                    previous_value=collision_previous,
                    current_value=collision_available,
                )
            )

        freshness_ms = status.get("liveFreshnessMillis")
        if isinstance(freshness_ms, (int, float)):
            freshness_threshold_ms = max(2000.0, float(getattr(self.args, "poll_interval", 0.5) or 0.5) * 3000.0)
            freshness_state = "fresh" if freshness_ms <= freshness_threshold_ms else "stale"
            freshness_changed, freshness_previous = changed("liveFreshnessState", freshness_state)
            if freshness_changed and freshness_previous is not None:
                self.append_timeline_event(
                    self.timeline_event(
                        processed_at=processed_at,
                        tick=tick,
                        event_type="live_freshness_changed",
                        severity="warn" if freshness_state == "stale" else "info",
                        summary=f"Live freshness changed: {freshness_state}",
                        details={"liveFreshnessMillis": freshness_ms, "thresholdMillis": freshness_threshold_ms},
                        previous_value=freshness_previous,
                        current_value=freshness_state,
                    )
                )

        warnings_key = {
            "warningCount": status.get("warningCount"),
            "budgetExceeded": status.get("budgetExceeded"),
            "writeFailureCount": status.get("writeFailureCount"),
            "sourceCapHit": status.get("sourceCapHit"),
        }
        warning_changed, warning_previous = changed("warningStatus", warnings_key)
        if warning_changed and warning_previous is not None:
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="warning_status_changed",
                    severity="warn" if status.get("warningCount") else "info",
                    summary=f"Warning status changed: {status.get('warningCount', 0)} warning(s)",
                    previous_value=warning_previous,
                    current_value=warnings_key,
                )
            )

        for key, event_type, label in (
            ("sourceCapHit", "source_cap_changed", "Source cap state changed"),
            ("budgetExceeded", "budget_exceeded_changed", "Realtime budget state changed"),
            ("writeFailureCount", "write_failures_changed", "Write failure count changed"),
        ):
            current = status.get(key)
            did_change, previous = changed(key, current)
            if did_change and previous is not None:
                severity = "error" if key == "writeFailureCount" and current else "warn" if current else "info"
                self.append_timeline_event(
                    self.timeline_event(
                        processed_at=processed_at,
                        tick=tick,
                        event_type=event_type,
                        severity=severity,
                        summary=f"{label}: {current}",
                        previous_value=previous,
                        current_value=current,
                    )
                )

        input_source = status.get("inputSourceActive")
        source_changed, source_previous = changed("inputSourceActive", input_source)
        if source_changed and (source_previous is not None or input_source is not None):
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="input_source_changed",
                    severity="warn" if input_source == RAW_TICK_SOURCE and status.get("inputFallbackReason") else "info",
                    summary=f"Live input source changed: {input_source or 'unknown'}",
                    details={
                        "inputSourceRequested": status.get("inputSourceRequested"),
                        "inputSourceActive": input_source,
                        "compactPacketsAvailable": status.get("compactPacketsAvailable"),
                        "compactPacketsRecent": status.get("compactPacketsRecent"),
                        "inputFallbackReason": status.get("inputFallbackReason"),
                    },
                    previous_value=source_previous,
                    current_value=input_source,
                )
            )

        fallback_reason = status.get("inputFallbackReason")
        fallback_changed, fallback_previous = changed("inputFallbackReason", fallback_reason)
        if fallback_changed and (fallback_previous is not None or fallback_reason):
            if fallback_reason:
                summary = f"Compact packet fallback active: {fallback_reason}"
                severity = "warn"
            else:
                summary = "Compact packet fallback cleared"
                severity = "info"
            self.append_timeline_event(
                self.timeline_event(
                    processed_at=processed_at,
                    tick=tick,
                    event_type="compact_packet_fallback_changed",
                    severity=severity,
                    summary=summary,
                    details={
                        "inputSourceRequested": status.get("inputSourceRequested"),
                        "inputSourceActive": input_source,
                        "compactPacketsAvailable": status.get("compactPacketsAvailable"),
                        "compactPacketsRecent": status.get("compactPacketsRecent"),
                    },
                    previous_value=fallback_previous,
                    current_value=fallback_reason,
                )
            )

        return list(self.event_timeline)[events_before:]

    def liveness_summary(self) -> dict:
        sample = []
        seen = set()
        for value in self.recently_unavailable_targets.values():
            identity = (
                value.get("objectKey"),
                value.get("worldX"),
                value.get("worldY"),
                value.get("plane"),
                value.get("targetLiveState"),
            )
            if identity in seen:
                continue
            seen.add(identity)
            sample.append(value)
            if len(sample) >= 10:
                break
        return {
            "livenessMode": self.args.liveness_mode,
            "livenessBudgetMs": self.args.liveness_budget_ms,
            "livenessBudgetExceeded": self.last_liveness_budget_exceeded,
            "livenessDegraded": self.last_liveness_degraded,
            "livenessCandidatesChecked": self.last_liveness_candidates_checked,
            "livenessCandidatesSkippedByBudget": self.last_liveness_candidates_skipped_by_budget,
            "livenessCacheSize": len(self.recently_unavailable_targets),
            "recentlyUnavailableCount": self.last_recently_unavailable_count,
            "recentlyDepletedCount": self.last_recently_depleted_count,
            "recentlyUnavailablePruned": self.last_recently_unavailable_pruned,
            "recentlyUnavailableCacheMax": self.args.max_recently_unavailable,
            "recentlyUnavailableCacheOverLimit": self.last_recently_unavailable_cache_over_limit,
            "suppressedCandidateCount": self.last_candidates_suppressed_by_liveness,
            "candidatesSuppressedByLiveness": self.last_candidates_suppressed_by_liveness,
            "candidatesSuppressedAsDepleted": self.last_candidates_suppressed_as_depleted,
            "candidatesRevivedAfterRespawn": self.last_candidates_revived_after_respawn,
            "recentlyUnavailableTargets": sample,
            **self.last_best_candidate_change,
        }

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

    def record_performance_sample(self, status: dict) -> None:
        timing = status.get("timingBreakdownMillis") if isinstance(status.get("timingBreakdownMillis"), dict) else {}
        self.performance_history.append(
            {
                "tick": status.get("lastProcessedTick"),
                "totalMs": float(status.get("processingDurationMillis") or 0.0),
                "candidateMs": float(timing.get("candidateSelectMillis") or 0.0),
                "livenessMs": float(timing.get("livenessUpdateMillis") or timing.get("livenessTotalMillis") or 0.0),
                "writeMs": float(timing.get("outputWriteMillis") or 0.0),
                "worldBuilt": int(status.get("worldTargetsBuilt") or 0),
                "candidates": int(status.get("candidateCount") or 0),
                "budgetExceeded": bool(status.get("budgetExceeded")),
                "writeRetryCount": int(status.get("writeRetryCount") or 0),
                "writeFailureCount": int(status.get("writeFailureCount") or 0),
                "rawSeen": int(status.get("rawRecordsSeenThisPoll") or 0),
                "processed": int(status.get("rawRecordsFullyProcessed") or 0),
                "coalesced": int(status.get("coalescedBacklogTicks") or 0),
                "livenessBudgetExceeded": bool(status.get("livenessBudgetExceeded")),
                "inputSourceActive": status.get("inputSourceActive"),
                "compactPacketsSeen": int(status.get("compactPacketsSeen") or 0),
                "compactPacketsProcessed": int(status.get("compactPacketsProcessed") or 0),
                "compactPacketsCoalesced": int(status.get("compactPacketsCoalesced") or 0),
                "tailReadMs": float(timing.get("tailReadMillis") or 0.0),
                "parseMs": float(timing.get("jsonParseMillis") or 0.0),
            }
        )

    def performance_summary_payload(self, status: dict, processed_at: str) -> dict:
        samples = list(self.performance_history)
        totals = [sample["totalMs"] for sample in samples]
        recommendations = []
        if self.args.latency_mode == "complete":
            recommendations.append("Complete audit mode processes every selected tick; use realtime mode for live latency.")
        elif percentile(totals, 95) is not None and percentile(totals, 95) > self.args.target_update_ms:
            recommendations.append("Realtime p95 exceeds the target update budget; keep max-new-ticks-per-update=1 and emit-world-targets=candidates.")
        if any(sample["writeFailureCount"] for sample in samples):
            recommendations.append("Live output write failures were observed; close readers or increase write retry settings.")
        if average([sample["coalesced"] for sample in samples]) and average([sample["coalesced"] for sample in samples]) > 0:
            recommendations.append("Backlog is being coalesced for freshness; this is expected in realtime mode when raw ticks arrive faster than processing.")
        return {
            "schema": LIVE_PERFORMANCE_SCHEMA,
            "generatedAtUtc": processed_at,
            "sessionPath": str(self.session),
            "mode": status.get("mode"),
            "latencyMode": self.args.latency_mode,
            "inputSourceActive": self.input_source_active,
            "latestTick": status.get("lastProcessedTick"),
            "sampleCount": len(samples),
            "avgTotalMs": average(totals),
            "p50TotalMs": percentile(totals, 50),
            "p90TotalMs": percentile(totals, 90),
            "p95TotalMs": percentile(totals, 95),
            "maxTotalMs": round(max(totals), 3) if totals else None,
            "avgCandidateMs": average([sample["candidateMs"] for sample in samples]),
            "avgLivenessMs": average([sample.get("livenessMs", 0.0) for sample in samples]),
            "avgWriteMs": average([sample["writeMs"] for sample in samples]),
            "avgWorldBuilt": average([sample["worldBuilt"] for sample in samples]),
            "avgCandidates": average([sample["candidates"] for sample in samples]),
            "budgetExceededCount": sum(1 for sample in samples if sample["budgetExceeded"]),
            "livenessBudgetExceededCount": sum(1 for sample in samples if sample.get("livenessBudgetExceeded")),
            "writeRetryCount": sum(sample["writeRetryCount"] for sample in samples),
            "writeFailureCount": sum(sample["writeFailureCount"] for sample in samples),
            "avgRawSeen": average([sample["rawSeen"] for sample in samples]),
            "avgProcessed": average([sample["processed"] for sample in samples]),
            "avgCoalesced": average([sample["coalesced"] for sample in samples]),
            "avgCompactPacketReadMs": average([sample.get("tailReadMs", 0.0) for sample in samples if sample.get("inputSourceActive") == COMPACT_PACKET_SOURCE]),
            "avgRawTickReadMs": average([sample.get("tailReadMs", 0.0) for sample in samples if sample.get("inputSourceActive") == RAW_TICK_SOURCE]),
            "avgParseMs": average([sample.get("parseMs", 0.0) for sample in samples]),
            "avgActiveMs": average(totals),
            "p95ActiveMs": percentile(totals, 95),
            "compactPacketCoalescedCount": sum(sample.get("compactPacketsCoalesced", 0) for sample in samples),
            "recommendations": recommendations,
        }

    def process_window(self, force_rebuild: bool = False, rebuild_reason: str = "incremental") -> dict:
        total_started = time.perf_counter()
        processed_at = utc_now()
        timing = Timing()
        self.last_classification_cache_invalidations = 0
        self.refresh_profile_documents_if_needed()
        warnings = list(self.override_warnings) + list(self.profile_warnings) + self.compact_input_warnings()
        if self.input_source_active == RAW_TICK_SOURCE and self.input_fallback_reason and self.args.input_source == "auto":
            warnings.append(f"live processor is using raw tick fallback; {self.input_fallback_reason}")
        selected_ticks = self.selected_ticks()
        selected_tick_ids = [tick_id_for(tick) for tick in selected_ticks if tick_id_for(tick) is not None]
        with timing.measure("tickCoalesceMillis"):
            processing_ticks = self.processing_ticks_for(selected_ticks, force_rebuild)
        self.last_full_window_rebuild = bool(force_rebuild)
        self.last_rebuild_reason = rebuild_reason if force_rebuild else "incremental"

        loop_started = time.perf_counter()
        build_before = timing.values.get("worldTargetBuildMillis", 0.0)
        processed_now, new_count = self.process_selected_ticks(processing_ticks, force_rebuild, timing)
        build_delta = timing.values.get("worldTargetBuildMillis", 0.0) - build_before
        timing.set("candidateCacheMillis", max(0.0, (time.perf_counter() - loop_started) * 1000.0 - build_delta))

        output_ticks = self.output_ticks_for(selected_ticks, processed_now)
        output_tick_ids = {tick_id_for(tick) for tick in output_ticks if tick_id_for(tick) is not None}
        processed_ticks = [
            self.processed_ticks[tick_id]
            for tick_id in sorted(output_tick_ids)
            if tick_id in self.processed_ticks
        ]
        state_update_ticks = self.ticks_for_realtime_state_update(selected_ticks, processing_ticks, output_ticks)
        candidate_context_ticks = state_update_ticks if self.args.latency_mode == "realtime" else selected_ticks
        with timing.measure("livenessTotalMillis"):
            with timing.measure("livenessUnavailablePruneMillis"):
                latest_state_tick = state_update_ticks[-1] if state_update_ticks else None
                self.prune_unavailable(tick_id_for(latest_state_tick) if latest_state_tick else None)
            if self.args.liveness_mode == "full":
                with timing.measure("livenessFullScanMillis"):
                    self.update_liveness_from_ticks(state_update_ticks)
            elif self.args.liveness_mode == "delta":
                with timing.measure("livenessDeltaMillis"):
                    self.update_liveness_from_ticks(state_update_ticks)
            else:
                with timing.measure("livenessCacheLookupMillis"):
                    self.update_liveness_from_ticks(state_update_ticks)
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
                candidate_context_ticks,
                source_records,
                ui_records,
                self.args,
                self.library,
                self.profile,
            )
        warnings.extend(candidate_warnings)
        candidates = [enrich_live_candidate(candidate, processed_at) for candidate in candidates]
        with timing.measure("livenessCandidateApplyMillis"):
            candidates, liveness_stats = self.apply_liveness_to_candidates(candidates)
        timing.set("livenessTotalMillis", timing.values.get("livenessCandidateApplyMillis", 0.0))
        timing.set("livenessUpdateMillis", timing.values.get("livenessTotalMillis", 0.0))
        candidate_stats.update(liveness_stats)
        self.update_best_candidate_change(candidates)

        with timing.measure("worldTargetFilterMillis"):
            world_output_records = self.output_world_records(candidates, source_records, full_records)
            world_output_records = [decorate_live_record(record, LIVE_WORLD_TARGET_SCHEMA, processed_at) for record in world_output_records]

        world_counts = Counter(record.get("tickId") for record in world_output_records)
        candidate_counts = Counter(candidate.get("tickId") for candidate in candidates)
        tick_summaries = tick_summaries_for(self.session, output_ticks, self.source_files, world_counts, candidate_counts, build_durations, processed_at)
        latest_tick_record = output_ticks[-1] if output_ticks else (selected_ticks[-1] if selected_ticks else (next(reversed(self.tick_window.values())) if self.tick_window else None))
        navigation = navigation_summary_for(latest_tick_record, processed_at)
        candidates = apply_navigation_to_candidates(candidates, navigation)

        total_duration_ms = (time.perf_counter() - total_started) * 1000.0
        budget_exceeded = total_duration_ms > self.args.target_update_ms
        warning_exceeded = total_duration_ms > self.args.warn_update_ms

        with timing.measure("baselineStateMillis"):
            baseline = baseline_state_for(self.session, self.args, latest_tick_record, selected_ticks, candidates, processed_at, total_duration_ms, budget_exceeded)

        with timing.measure("inventoryDeltaMillis"):
            inventory_state = inventory_state_for_ticks(state_update_ticks, latest_tick_record)

        with timing.measure("activityStateMillis"):
            activity = activity_state_for(
                latest_tick_record,
                state_update_ticks,
                candidates,
                self.liveness_summary(),
                processed_at,
                0.0,
                inventory_state=inventory_state,
            )

        with timing.measure("contextIndexMillis"):
            context_index = context_index_for(self.session, self.args, selected_ticks, candidates, processed_at)

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
        with timing.measure("outputSerializeMillis"):
            serialized_outputs = {
                "uiTargets": "".join(json_dump_compact(record) + "\n" for record in ui_records),
                "candidates": "".join(json_dump_compact(record) + "\n" for record in candidates),
                "tickSummary": "".join(json_dump_compact(record) + "\n" for record in tick_summaries),
                "baseline": json.dumps(baseline, indent=2, sort_keys=False) + "\n",
                "activity": json.dumps(activity, indent=2, sort_keys=False) + "\n",
                "contextIndex": json.dumps(context_index, indent=2, sort_keys=False) + "\n",
                "navigation": json.dumps(navigation, indent=2, sort_keys=False) + "\n",
                "index": json.dumps(index, indent=2, sort_keys=False) + "\n",
                "status": json.dumps(status, indent=2, sort_keys=False) + "\n",
            }
            if self.args.emit_world_targets != "none":
                serialized_outputs["worldTargets"] = "".join(json_dump_compact(record) + "\n" for record in world_output_records)
        suppress_output_writes = bool(getattr(self.args, "suppress_output_writes", False))
        with timing.measure("outputWriteMillis"):
            if suppress_output_writes:
                output_bytes["worldTargets"] = len(serialized_outputs.get("worldTargets", ""))
                output_bytes["uiTargets"] = len(serialized_outputs["uiTargets"])
                output_bytes["candidates"] = len(serialized_outputs["candidates"])
                output_bytes["tickSummary"] = len(serialized_outputs["tickSummary"])
                output_bytes["baseline"] = len(serialized_outputs["baseline"])
                output_bytes["activity"] = len(serialized_outputs["activity"])
                output_bytes["contextIndex"] = len(serialized_outputs["contextIndex"])
                output_bytes["navigation"] = len(serialized_outputs["navigation"])
                output_bytes["index"] = len(serialized_outputs["index"])
                output_bytes["status"] = len(serialized_outputs["status"])
            else:
                if self.args.emit_world_targets == "none":
                    output_bytes["worldTargets"] = remove_file_if_exists(paths["worldTargets"])
                else:
                    output_bytes["worldTargets"] = atomic_write_text(paths["worldTargets"], serialized_outputs["worldTargets"], options=self.write_options, stats=write_stats)
                output_bytes["uiTargets"] = atomic_write_text(paths["uiTargets"], serialized_outputs["uiTargets"], options=self.write_options, stats=write_stats)
                output_bytes["candidates"] = atomic_write_text(paths["candidates"], serialized_outputs["candidates"], options=self.write_options, stats=write_stats)
                output_bytes["tickSummary"] = atomic_write_text(paths["tickSummary"], serialized_outputs["tickSummary"], options=self.write_options, stats=write_stats)
                output_bytes["baseline"] = atomic_write_text(paths["baseline"], serialized_outputs["baseline"], options=self.write_options, stats=write_stats)
                output_bytes["activity"] = atomic_write_text(paths["activity"], serialized_outputs["activity"], options=self.write_options, stats=write_stats)
                output_bytes["contextIndex"] = atomic_write_text(paths["contextIndex"], serialized_outputs["contextIndex"], options=self.write_options, stats=write_stats)
                output_bytes["navigation"] = atomic_write_text(paths["navigation"], serialized_outputs["navigation"], options=self.write_options, stats=write_stats)
                output_bytes["index"] = atomic_write_text(paths["index"], serialized_outputs["index"], options=self.write_options, stats=write_stats)
                output_bytes["status"] = atomic_write_text(paths["status"], serialized_outputs["status"], options=self.write_options, stats=write_stats)

        process_window_ms = (time.perf_counter() - total_started) * 1000.0
        pre_window_ms = (
            self.tailer.last_file_discover_millis
            + self.tailer.last_tail_read_millis
            + self.tailer.last_json_parse_millis
            + self.last_raw_tick_add_millis
        )
        final_duration_ms = process_window_ms + pre_window_ms
        budget_exceeded = self.args.latency_mode == "realtime" and final_duration_ms > self.args.target_update_ms
        warning_exceeded = self.args.latency_mode == "realtime" and final_duration_ms > self.args.warn_update_ms
        self.total_write_retries += write_stats.retry_count
        self.total_write_failures += write_stats.failure_count
        status["processingDurationMillis"] = round(final_duration_ms, 3)
        status["auditDurationMillis"] = round(final_duration_ms, 3) if self.args.latency_mode == "complete" else None
        status["realtimeDurationMillis"] = round(final_duration_ms, 3) if self.args.latency_mode == "realtime" else None
        status["timingBreakdownMillis"] = timing_payload(timing, final_duration_ms, self.tailer, raw_tick_ingest_millis=self.last_raw_tick_add_millis)
        status["outputBytes"] = {
            "outputBytesWorldTargets": output_bytes.get("worldTargets", 0),
            "outputBytesCandidates": output_bytes.get("candidates", 0),
            "outputBytesBaseline": output_bytes.get("baseline", 0),
            "outputBytesActivity": output_bytes.get("activity", 0),
            "outputBytesOverlayDebug": output_bytes.get("overlayDebug", 0),
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
            status.setdefault("warnings", []).append(f"target update budget exceeded: {final_duration_ms:.1f} ms > {self.args.target_update_ms} ms")
            status["warningCount"] = len(status["warnings"])
        if warning_exceeded and not budget_exceeded:
            status.setdefault("warnings", []).append(f"update warning threshold exceeded: {final_duration_ms:.1f} ms > {self.args.warn_update_ms} ms")
            status["warningCount"] = len(status["warnings"])
        new_events = self.emit_timeline_events(
            latest_tick_record=latest_tick_record,
            candidates=candidates,
            inventory_state=inventory_state,
            activity=activity,
            status=status,
            processed_at=processed_at,
            navigation=navigation,
        )
        status["eventTimelinePath"] = str(paths["events"])
        status["eventTimelineCount"] = len(self.event_timeline)
        status["eventsEmittedThisUpdate"] = len(new_events)
        overlay_debug = overlay_debug_state_for(
            self.session,
            self.args,
            latest_tick_record,
            candidates,
            navigation,
            status,
            processed_at,
            list(self.event_timeline),
        )
        status["overlayDebugStatePath"] = str(paths["overlayDebug"])
        overlay_debug_text = json.dumps(overlay_debug, indent=2, sort_keys=False) + "\n"
        status.setdefault("outputBytes", {})["outputBytesOverlayDebug"] = len(overlay_debug_text)
        status["outputBytes"]["outputBytesTotal"] = sum(output_bytes.values()) + len(overlay_debug_text)
        before_status_failures = write_stats.failure_count
        self.record_performance_sample(status)
        performance = self.performance_summary_payload(status, processed_at)
        with timing.measure("outputSerializeMillis"):
            final_status_text = json.dumps(status, indent=2, sort_keys=False) + "\n"
            performance_text = json.dumps(performance, indent=2, sort_keys=False) + "\n"
            events_text = "".join(json_dump_compact(record) + "\n" for record in self.event_timeline)
        with timing.measure("outputWriteMillis"):
            if suppress_output_writes:
                status_size = len(final_status_text)
                performance_size = len(performance_text)
                events_size = len(events_text)
                overlay_debug_size = len(overlay_debug_text)
            else:
                status_size = atomic_write_text(paths["status"], final_status_text, options=self.write_options, stats=write_stats)
                performance_size = atomic_write_text(paths["performance"], performance_text, options=self.write_options, stats=write_stats)
                events_size = atomic_write_text(paths["events"], events_text, options=self.write_options, stats=write_stats)
                overlay_debug_size = atomic_write_text(paths["overlayDebug"], overlay_debug_text, options=self.write_options, stats=write_stats)
        if status_size:
            output_bytes["status"] = status_size
        output_bytes["performance"] = performance_size
        output_bytes["events"] = events_size
        output_bytes["overlayDebug"] = overlay_debug_size
        if write_stats.retry_count or write_stats.failure_count:
            self.total_write_retries += write_stats.retry_count - status.get("writeRetryCount", 0)
            self.total_write_failures += write_stats.failure_count - status.get("writeFailureCount", 0)
        if write_stats.failure_count > before_status_failures:
            print(f"Warning: could not refresh live_status.json after retries: {write_stats.last_error}")
        self.previous_update_overran = bool(status.get("budgetExceeded") or status.get("warningUpdateExceeded"))

        return {
            "worldRecords": world_output_records,
            "uiRecords": ui_records,
            "candidates": candidates,
            "tickSummaries": tick_summaries,
            "baseline": baseline,
            "activity": activity,
            "events": list(self.event_timeline),
            "overlayDebug": overlay_debug,
            "performance": performance,
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
        current_compact_state = compact_packet_state(self.session)
        skipped_ids = list(self.last_skipped_intermediate_tick_ids)
        coalesced_before = self.tailer.last_coalesced_before_parse
        coalesced_after = self.last_coalesced_backlog_ticks
        raw_records_fully_processed = len(self.last_processed_tick_ids)
        if self.args.latency_mode == "realtime" and self.last_full_window_rebuild and not self.args.force_window_rebuild:
            warnings.append("Realtime mode performed a full window rebuild without --force-window-rebuild.")
        if (
            self.args.latency_mode == "realtime"
            and self.args.max_new_ticks_per_update
            and raw_records_fully_processed > self.args.max_new_ticks_per_update
        ):
            warnings.append(
                f"Realtime mode fully processed {raw_records_fully_processed} ticks, above --max-new-ticks-per-update={self.args.max_new_ticks_per_update}."
            )
        if self.args.latency_mode == "realtime" and self.args.liveness_mode == "full":
            warnings.append("full liveness in realtime mode may exceed update budget; use delta/basic/off for realtime.")
        if self.last_liveness_budget_exceeded:
            warnings.append("liveness budget exceeded; candidate liveness degraded for this tick")
        return {
            "schema": LIVE_STATUS_SCHEMA,
            "generatedAtUtc": processed_at,
            "sessionPath": str(self.session),
            "profile": self.args.profile,
            "profileId": self.args.profile,
            "targetType": self.args.target_type,
            "inputSourceRequested": self.args.input_source,
            "inputSourceActive": self.input_source_active,
            "compactPacketsAvailable": bool(current_compact_state.get("available")),
            "compactPacketsRecent": bool(current_compact_state.get("recent")),
            "compactPacketIndexPath": current_compact_state.get("indexPath"),
            "compactPacketLatestTick": current_compact_state.get("latestTick"),
            "compactPacketLatestSequence": current_compact_state.get("latestSequence"),
            "compactPacketAgeSeconds": current_compact_state.get("ageSeconds"),
            "rawTicksAvailable": bool(self.raw_ticks_available),
            "inputFallbackReason": self.input_fallback_reason,
            "defaultLiveInputPreference": COMPACT_PACKET_SOURCE,
            "mode": "follow" if self.args.follow else "once",
            "latencyMode": self.args.latency_mode,
            "modeLabel": (
                "COMPLETE AUDIT MODE: processes every selected tick; not intended for live latency."
                if self.args.latency_mode == "complete"
                else "REALTIME MODE: latest context prioritized; intermediate ticks may be coalesced."
            ),
            "auditMode": self.args.latency_mode == "complete",
            "realtimeMode": self.args.latency_mode == "realtime",
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
            "rawRecordsSeenThisPoll": self.tailer.last_raw_records_seen,
            "rawRecordsFullyParsedThisPoll": self.tailer.last_raw_records_fully_parsed,
            "rawRecordsSkippedBeforeParse": self.tailer.last_raw_records_skipped_before_parse,
            "rawRecordsLightParsed": self.tailer.last_raw_records_light_parsed,
            "rawRecordsFullyProcessed": raw_records_fully_processed,
            "coalescedBeforeParse": coalesced_before,
            "coalescedAfterParse": coalesced_after,
            "coalescedBacklogTicks": coalesced_before + coalesced_after,
            "newestTickSelectedForProcessing": self.tailer.last_newest_tick_selected or (max(self.last_processed_tick_ids) if self.last_processed_tick_ids else None),
            "fileOffsetsAdvancedPastSkippedRecords": bool(self.tailer.last_file_offsets_advanced_past_skipped_records),
            "compactPacketsSeen": int(getattr(self.tailer, "last_compact_packets_seen", 0) or 0),
            "compactPacketsProcessed": int(getattr(self.tailer, "last_compact_packets_processed", 0) or 0),
            "compactPacketsCoalesced": int(getattr(self.tailer, "last_compact_packets_coalesced", 0) or 0),
            "compactPacketLastSequence": getattr(self.tailer, "last_compact_packet_last_sequence", None),
            "compactPacketLatestSegment": getattr(self.tailer, "last_compact_packet_latest_segment", None) or current_compact_state.get("latestSegment"),
            "compactPacketRolloverCount": int(getattr(self.tailer, "last_compact_packet_rollover_count", 0) or 0),
            "compactPacketReadErrors": int(getattr(self.tailer, "last_compact_packet_read_errors", 0) or 0),
            "skippedIntermediateTickIds": skipped_ids if len(skipped_ids) <= 25 else [],
            "skippedIntermediateTickCount": len(skipped_ids),
            "skippedIntermediateTickIdsTruncated": len(skipped_ids) > 25,
            "backlogDepth": self.last_backlog_depth,
            "backlogDrainCount": self.backlog_drain_count,
            "lastBacklogDrainTick": self.last_backlog_drain_tick,
            "lastBacklogDrainReason": self.last_backlog_drain_reason,
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
            "recentlyUnavailableCount": self.last_recently_unavailable_count,
            "recentlyDepletedCount": self.last_recently_depleted_count,
            "recentlyUnavailablePruned": self.last_recently_unavailable_pruned,
            "recentlyUnavailableCacheMax": self.args.max_recently_unavailable,
            "recentlyUnavailableCacheOverLimit": self.last_recently_unavailable_cache_over_limit,
            "candidatesSuppressedByLiveness": self.last_candidates_suppressed_by_liveness,
            "candidatesSuppressedAsDepleted": self.last_candidates_suppressed_as_depleted,
            "candidatesRevivedAfterRespawn": self.last_candidates_revived_after_respawn,
            "livenessMode": self.args.liveness_mode,
            "livenessBudgetMs": self.args.liveness_budget_ms,
            "livenessBudgetExceeded": self.last_liveness_budget_exceeded,
            "livenessDegraded": self.last_liveness_degraded,
            "livenessCandidatesChecked": self.last_liveness_candidates_checked,
            "livenessCandidatesSkippedByBudget": self.last_liveness_candidates_skipped_by_budget,
            "livenessVisibleRefScanLimit": self.args.liveness_visible_ref_scan_limit,
            "livenessVisibleRefScanCount": self.last_liveness_visible_ref_scan_count,
            "livenessFullScanCount": self.last_liveness_full_scan_count,
            **self.last_best_candidate_change,
            "targetUpdateMillis": self.args.target_update_ms,
            "warnUpdateMillis": self.args.warn_update_ms,
            "budgetAppliesToRealtime": self.args.latency_mode == "realtime",
            "budgetExceeded": budget_exceeded if self.args.latency_mode == "realtime" else False,
            "warningUpdateExceeded": warning_exceeded if self.args.latency_mode == "realtime" else False,
            "auditDurationMillis": round(duration_ms, 3) if self.args.latency_mode == "complete" else None,
            "realtimeDurationMillis": round(duration_ms, 3) if self.args.latency_mode == "realtime" else None,
            "auditPerformanceNote": (
                "Complete audit mode is expected to be slower because it processes every selected tick."
                if self.args.latency_mode == "complete"
                else None
            ),
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
            "livenessCacheSize": len(self.recently_unavailable_targets),
            "livenessCacheHits": self.last_liveness_cache_hits,
            "livenessCacheMisses": self.last_liveness_cache_misses,
            "activityUsedRollingScan": self.last_activity_used_rolling_scan,
            "inventoryUsedRollingScan": self.last_inventory_used_rolling_scan,
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
                "liveActivityState": str(paths["activity"]),
                "liveEventTimeline": str(paths["events"]),
                "livePerformanceSummary": str(paths["performance"]),
                "liveContextIndex": str(paths["contextIndex"]),
                "liveNavigationSummary": str(paths["navigation"]),
                "liveStatus": str(paths["status"]),
            },
        }

    def poll_new_records(self, force_rebuild: bool = False) -> tuple[int, dict]:
        realtime_tail = (
            self.args.latency_mode == "realtime"
            and bool(self.args.drop_backlog_to_meet_budget)
            and not force_rebuild
        )
        max_records = self.args.max_new_ticks_per_update if realtime_tail else None
        drain_this_poll = bool(
            realtime_tail
            and self.args.drain_backlog_on_overrun
            and self.previous_update_overran
        )
        if drain_this_poll:
            max_records = 1 if not max_records or max_records <= 0 else min(max_records, 1)
            self.last_backlog_drain_reason = "previous update exceeded realtime budget"

        records = self.tailer.read_new_records(realtime=realtime_tail, max_records=max_records)
        if drain_this_poll and self.tailer.last_coalesced_before_parse:
            self.backlog_drain_count += self.tailer.last_coalesced_before_parse
            self.last_backlog_drain_tick = self.tailer.last_newest_tick_selected

        started = time.perf_counter()
        added, dropped = self.add_ticks(records)
        self.last_raw_tick_add_millis = (time.perf_counter() - started) * 1000.0
        result = self.process_window(force_rebuild=force_rebuild, rebuild_reason="force-window-rebuild" if force_rebuild else "incremental")
        result["status"]["droppedOldTicks"] = dropped
        return added, result

    def poll_once(self) -> tuple[int, dict]:
        return self.poll_new_records(force_rebuild=self.args.force_window_rebuild)


def print_startup(session: Path, args, processor: LiveTargetProcessor | None = None) -> None:
    print("Live target processor")
    if args.latency_mode == "complete":
        print("COMPLETE AUDIT MODE: processes every selected tick; not intended for live latency.")
    else:
        print("REALTIME MODE: latest context prioritized; intermediate ticks may be coalesced.")
    print(f"session: {session}")
    print(f"profile: {args.profile}")
    print(f"mode: {'follow' if args.follow else 'once'}")
    print(f"latency mode: {args.latency_mode}")
    active_input = getattr(processor, "input_source_active", args.input_source)
    if active_input == COMPACT_PACKET_SOURCE:
        print("Live input: compact packets")
    elif args.input_source == RAW_TICK_SOURCE:
        print("Live input: raw ticks explicitly requested")
    else:
        print("Live input: raw ticks fallback because compact packets were unavailable")
    print(f"input source: {active_input} (requested {args.input_source})")
    if processor and processor.input_fallback_reason:
        print(f"input fallback: {processor.input_fallback_reason}")
    print(f"candidate output window: {args.candidate_output_window}")
    print(f"liveness mode: {args.liveness_mode}")
    print(f"window ticks: {args.window_ticks}")
    print(f"selection: {selection_label(args)}")
    print(f"emit world targets: {args.emit_world_targets}")
    print(f"include UI targets: {str(bool(args.include_ui_targets)).lower()}")
    print(f"output: {live_output_dir(session)}")


def print_result_summary(result: dict, tailer: TickJsonlTailer) -> None:
    status = result["status"]
    timing = status.get("timingBreakdownMillis") or {}
    output = status.get("outputBytes") or {}
    if status.get("auditMode"):
        print("COMPLETE AUDIT MODE: processes every selected tick; not intended for live latency.")
    elif status.get("realtimeMode"):
        print("REALTIME MODE: latest context prioritized; intermediate ticks may be coalesced.")
    print(f"processed ticks: {status['selectedTickCount']}")
    print(f"latest tick: {status['lastProcessedTick']}")
    print(f"input source: {status.get('inputSourceActive')} (requested {status.get('inputSourceRequested')})")
    if status.get("inputFallbackReason"):
        print(f"input fallback: {status.get('inputFallbackReason')}")
    print(f"latest raw tick seen: {status.get('latestRawTickSeen')}")
    print(f"coalesced backlog ticks: {status.get('coalescedBacklogTicks', 0)}")
    print(f"world targets built: {status['worldTargetsBuilt']}")
    print(f"world targets written: {status['worldTargetsWritten']}")
    print(f"world targets suppressed: {status['worldTargetsSuppressed']}")
    print(f"candidates in window: {status['candidateCount']}")
    print(f"liveness mode: {status.get('livenessMode')} budgetExceeded={status.get('livenessBudgetExceeded')}")
    if status.get("auditMode"):
        print(f"audit duration: {status.get('auditDurationMillis')} ms")
    else:
        print(f"realtime duration: {status.get('realtimeDurationMillis', status['processingDurationMillis'])} ms")
    print(f"candidate selection: {timing.get('candidateSelectMillis', 0)} ms")
    print(f"output bytes total: {output.get('outputBytesTotal', 0)}")
    if status.get("realtimeMode"):
        print(f"budget exceeded: {str(bool(status.get('budgetExceeded'))).lower()}")
    if status.get("writeRetryCount") or status.get("writeFailureCount"):
        print(f"write retries: {status.get('writeRetryCount', 0)}")
        print(f"write failures: {status.get('writeFailureCount', 0)}")
    print(f"malformed lines: {tailer.malformed_total}")
    print(f"output: {live_output_paths(Path(status['sessionPath']))['status']}")


def print_follow_update(added: int, result: dict) -> None:
    status = result["status"]
    timing = status.get("timingBreakdownMillis") or {}
    raw_seen = status.get("rawRecordsSeenThisPoll", added)
    processed = status.get("rawRecordsFullyProcessed", status.get("processedNewTicks", added))
    coalesced = status.get("coalescedBacklogTicks", 0)
    performance = result.get("performance") or {}
    print(
        f"latestTick={status['lastProcessedTick']} "
        f"input={status.get('inputSourceActive')} "
        f"rawSeen={raw_seen} "
        f"processed={processed} "
        f"coalesced={coalesced} "
        f"worldBuilt={status['worldTargetsBuilt']} "
        f"worldWritten={status['worldTargetsWritten']} "
        f"candidates={status['candidateCount']} "
        f"livenessMode={status.get('livenessMode')} "
        f"baselineMs={timing.get('baselineStateMillis', 0)} "
        f"livenessMs={timing.get('livenessUpdateMillis', 0)} "
        f"candidateMs={timing.get('candidateSelectMillis', 0)} "
        f"writeMs={timing.get('outputWriteMillis', 0)} "
        f"activeMs={timing.get('totalActiveMillis', status['processingDurationMillis'])} "
        f"p95={performance.get('p95TotalMs')} "
        f"budgetExceeded={str(bool(status.get('budgetExceeded'))).lower()} "
        f"writeRetries={status.get('writeRetryCount', 0)} "
        f"writeFailures={status.get('writeFailureCount', 0)}"
    )


def args_copy(args, **overrides) -> SimpleNamespace:
    values = dict(vars(args))
    values.update(overrides)
    return SimpleNamespace(**values)


def compare_distance(candidate: dict) -> float | None:
    for key in ("distanceTiles", "targetDistanceChebyshev", "targetDistanceTiles"):
        value = candidate.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def compare_aim_point(candidate: dict | None) -> dict | None:
    if not candidate:
        return None
    context = candidate.get("aimPointContext") if isinstance(candidate.get("aimPointContext"), dict) else {}
    aim = candidate.get("aimPoint") if isinstance(candidate.get("aimPoint"), dict) else {}
    x = context.get("canvasX", context.get("x"))
    y = context.get("canvasY", context.get("y"))
    if x is None or y is None:
        x = aim.get("canvasX", aim.get("x"))
        y = aim.get("canvasY", aim.get("y"))
    return {"canvasX": x, "canvasY": y} if x is not None and y is not None else None


def compare_candidate_summary(candidate: dict | None) -> dict | None:
    if not candidate:
        return None
    return {
        "classId": candidate.get("classId"),
        "name": candidate.get("name"),
        "id": candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id"),
        "hash": candidate.get("hash"),
        "worldX": candidate.get("worldX"),
        "worldY": candidate.get("worldY"),
        "plane": candidate.get("plane"),
        "sceneX": candidate.get("sceneX"),
        "sceneY": candidate.get("sceneY"),
        "distanceTiles": compare_distance(candidate),
        "aimPoint": compare_aim_point(candidate),
        "targetLiveState": candidate.get("targetLiveState"),
    }


def compare_player_summary(result: dict) -> dict:
    baseline = result.get("baseline") if isinstance(result.get("baseline"), dict) else {}
    player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
    return {key: player.get(key) for key in ("worldX", "worldY", "plane", "sceneX", "sceneY", "localX", "localY")}


def compare_inventory_summary(result: dict) -> dict:
    activity = result.get("activity") if isinstance(result.get("activity"), dict) else {}
    baseline = result.get("baseline") if isinstance(result.get("baseline"), dict) else {}
    inventory = activity.get("inventory") if isinstance(activity.get("inventory"), dict) else baseline.get("inventory") if isinstance(baseline.get("inventory"), dict) else {}
    return {
        "known": inventory.get("known") if "known" in inventory else bool(inventory),
        "inventorySlotCount": inventory.get("inventorySlotCount") or inventory.get("slotCount"),
        "freeSlots": inventory.get("freeSlots"),
        "filledSlots": inventory.get("filledSlots"),
        "itemCount": inventory.get("itemCount"),
        "totalItemQuantity": inventory.get("totalItemQuantity"),
        "signature": inventory.get("signature") or inventory.get("inventoryHash"),
        "inventoryFull": inventory.get("inventoryFull"),
    }


def compare_inventory_core_matches(raw_inventory: dict, compact_inventory: dict) -> bool:
    core_fields = ("inventorySlotCount", "freeSlots", "filledSlots", "itemCount", "totalItemQuantity", "inventoryFull")
    for field in core_fields:
        raw_value = raw_inventory.get(field)
        compact_value = compact_inventory.get(field)
        if raw_value is not None and compact_value is not None and raw_value != compact_value:
            return False
    return True


def compare_liveness_summary(result: dict) -> dict:
    activity = result.get("activity") if isinstance(result.get("activity"), dict) else {}
    status = result.get("status") if isinstance(result.get("status"), dict) else {}
    liveness = activity.get("targetLiveness") if isinstance(activity.get("targetLiveness"), dict) else {}
    return {
        "bestCandidateLiveState": liveness.get("bestCandidateLiveState"),
        "activeCandidateLiveState": liveness.get("activeCandidateLiveState"),
        "recentlyUnavailableCount": liveness.get("recentlyUnavailableCount", status.get("recentlyUnavailableCount")),
        "recentlyDepletedCount": liveness.get("recentlyDepletedCount", status.get("recentlyDepletedCount")),
        "candidatesSuppressedByLiveness": liveness.get("candidatesSuppressedByLiveness", status.get("candidatesSuppressedByLiveness")),
        "candidatesSuppressedAsDepleted": liveness.get("candidatesSuppressedAsDepleted", status.get("candidatesSuppressedAsDepleted")),
        "livenessMode": status.get("livenessMode"),
        "livenessDegraded": status.get("livenessDegraded"),
        "livenessBudgetExceeded": status.get("livenessBudgetExceeded"),
    }


def compare_result_summary(result: dict | None) -> dict:
    if not result:
        return {"available": False}
    candidates = result.get("candidates") or []
    status = result.get("status") or {}
    tree_candidates = [candidate for candidate in candidates if "tree" in candidate_class_ids(candidate)]
    best_tree = tree_candidates[0] if tree_candidates else None
    nearest_tree = min(tree_candidates, key=lambda candidate: compare_distance(candidate) if compare_distance(candidate) is not None else 999999) if tree_candidates else None
    return {
        "available": True,
        "inputSourceActive": status.get("inputSourceActive"),
        "latestTick": status.get("lastProcessedTick"),
        "player": compare_player_summary(result),
        "candidateCount": len(candidates),
        "candidateCountsByClassId": status.get("candidateCountsByClassId") or {},
        "bestTree": compare_candidate_summary(best_tree),
        "nearestTree": compare_candidate_summary(nearest_tree),
        "inventory": compare_inventory_summary(result),
        "liveness": compare_liveness_summary(result),
        "sourceSceneKnowledgeComplete": status.get("sourceSceneKnowledgeComplete"),
        "sourceCapHit": status.get("sourceCapHit"),
        "missingFieldWarnings": [
            warning
            for warning in status.get("warnings") or []
            if "missing" in str(warning).lower() or "unavailable" in str(warning).lower()
        ],
    }


def run_compare_source(session: Path, args, input_source: str) -> dict | None:
    compare_args = args_copy(
        args,
        input_source=input_source,
        once=True,
        follow=False,
        quiet=True,
        summary=False,
        clear_live_output=False,
        suppress_output_writes=True,
    )
    processor = LiveTargetProcessor(session, compare_args)
    if input_source == COMPACT_PACKET_SOURCE and not processor.compact_packets_available:
        return None
    if input_source == RAW_TICK_SOURCE and not processor.raw_ticks_available:
        return None
    processor.initialize_from_existing()
    return processor.process_window(force_rebuild=args.force_window_rebuild, rebuild_reason=f"compare-{input_source}")


def compare_input_sources(session: Path, args) -> int:
    raw_result = run_compare_source(session, args, RAW_TICK_SOURCE)
    compact_result = run_compare_source(session, args, COMPACT_PACKET_SOURCE)
    raw_summary = compare_result_summary(raw_result)
    compact_summary = compare_result_summary(compact_result)

    warnings = []
    failures = []
    if not raw_summary.get("available"):
        failures.append("raw tick source unavailable")
    if not compact_summary.get("available"):
        failures.append("compact packet source unavailable")

    status = "FAIL" if failures else "PASS"
    if status == "PASS":
        if raw_summary.get("latestTick") != compact_summary.get("latestTick"):
            warnings.append("latest tick differs")
        if raw_summary.get("player") != compact_summary.get("player"):
            warnings.append("player baseline differs")
        if raw_summary.get("candidateCount") != compact_summary.get("candidateCount"):
            warnings.append("candidate counts differ")
        if raw_summary.get("bestTree") != compact_summary.get("bestTree"):
            failures.append("best tree candidate differs")
        if raw_summary.get("nearestTree") != compact_summary.get("nearestTree"):
            failures.append("nearest tree candidate differs")
        raw_inventory = raw_summary.get("inventory") or {}
        compact_inventory = compact_summary.get("inventory") or {}
        if raw_inventory.get("known") and compact_inventory.get("known") and not compare_inventory_core_matches(raw_inventory, compact_inventory):
            failures.append("inventory slot/quantity summary differs")
        elif raw_inventory != compact_inventory:
            warnings.append("inventory summary differs or is missing optional fields")
        if raw_inventory.get("signature") and compact_inventory.get("signature") and raw_inventory.get("signature") != compact_inventory.get("signature"):
            warnings.append("inventory signatures differ or use different formats")
        raw_complete = raw_summary.get("sourceSceneKnowledgeComplete")
        compact_complete = compact_summary.get("sourceSceneKnowledgeComplete")
        if raw_complete is not None and compact_complete is not None and raw_complete != compact_complete:
            failures.append("sourceSceneKnowledgeComplete differs")
        elif raw_complete != compact_complete:
            warnings.append("sourceSceneKnowledgeComplete is missing from one source")
        raw_cap_hit = raw_summary.get("sourceCapHit")
        compact_cap_hit = compact_summary.get("sourceCapHit")
        if raw_cap_hit is not None and compact_cap_hit is not None and raw_cap_hit != compact_cap_hit:
            failures.append("sourceCapHit differs")
        elif raw_cap_hit != compact_cap_hit:
            warnings.append("sourceCapHit is missing from one source")
        if raw_summary.get("liveness") != compact_summary.get("liveness"):
            warnings.append("liveness summary differs")

    if failures:
        status = "FAIL"
    elif warnings:
        status = "WARN"

    payload = {
        "schema": "live_input_source_comparison.v1",
        "status": status,
        "sessionPath": str(session),
        "profile": args.profile,
        "rawTicks": raw_summary,
        "compactPackets": compact_summary,
        "warnings": warnings,
        "failures": failures,
    }
    print(json.dumps(payload, indent=2, sort_keys=False))
    return 0 if status in {"PASS", "WARN"} else 1


def compact_required_error() -> str:
    return (
        "Compact live packets are required but unavailable. Enable compact live packets in the RuneLite telemetry "
        "config, collect a fresh session, then verify with "
        "inspect_live_packets.py --latest-session --summary."
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Tail compact live packets or raw tick JSONL files and write rolling read-only target context/candidate outputs. "
            "This does not interact with RuneLite or generate actions."
        )
    )
    parser.add_argument("--session", help="Explicit telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions directory when --latest-session is used.")
    parser.add_argument("--latest-session", action="store_true", help="Use the newest available session when --session is omitted.")
    parser.add_argument("--input-source", choices=sorted(INPUT_SOURCES), default="auto", help="Read source for live processing. Auto prefers compact live packets when available. Default: auto.")
    parser.add_argument("--compare-input-sources", action="store_true", help="Compare compact-packet and raw-tick candidate output for the selected/latest window, then exit.")
    parser.add_argument("--require-compact-packets", action="store_true", help="Fail fast unless compact live packets are available and recent; do not fall back to raw ticks.")
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
    parser.add_argument("--drain-backlog-on-overrun", dest="drain_backlog_on_overrun", action="store_true", default=None, help="After an over-budget realtime update, jump to the newest complete tick on the next poll. Default: on in realtime.")
    parser.add_argument("--no-drain-backlog-on-overrun", dest="drain_backlog_on_overrun", action="store_false", help="Do not do emergency realtime backlog drains after over-budget updates.")
    parser.add_argument("--include-ui-targets", action="store_true", help="Include existing batch ui_targets.jsonl records for ticks in the rolling window.")
    parser.add_argument("--no-ui-targets", action="store_true", help="Disable UI target loading even if live UI files exist.")
    parser.add_argument("--latest", type=positive_int, metavar="N", help="Select latest N ticks from the rolling window.")
    parser.add_argument("--latest-with-frames", type=positive_int, metavar="N", help="Select latest N ticks with retained frame files from the rolling window.")
    parser.add_argument("--exclude-ui-blocked", action="store_true", help="Exclude candidates whose aim point intersects known UI targets.")
    parser.add_argument("--emit-world-targets", choices=sorted(WORLD_TARGET_EMIT_MODES), default="candidates", help="Live world target output policy. Default: candidates.")
    parser.add_argument("--world-target-output-limit", type=non_negative_int, default=2000, help="Max live world target records to write. Use 0 for unlimited. Default: 2000.")
    parser.add_argument("--depleted-suppress-ticks", type=positive_int, default=20, help="Ticks to suppress recently despawned/depleted target candidates. Default: 20.")
    parser.add_argument("--liveness-mode", choices=sorted(LIVENESS_MODES), help="Target liveness mode. Default: delta in realtime, full in complete.")
    parser.add_argument("--liveness-budget-ms", type=positive_float, default=20.0, help="Realtime liveness budget in milliseconds. Default: 20.")
    parser.add_argument("--max-recently-unavailable", type=positive_int, default=1000, help="Maximum keyed recently-unavailable liveness entries. Default: 1000.")
    parser.add_argument("--liveness-visible-ref-scan-limit", type=non_negative_int, default=500, help="Visible-ref scan limit for future/fallback liveness checks. Default: 500.")
    parser.add_argument("--target-update-ms", type=positive_float, default=100.0, help="Target update budget in milliseconds. Default: 100.")
    parser.add_argument("--warn-update-ms", type=positive_float, default=250.0, help="Warning update threshold in milliseconds. Default: 250.")
    parser.add_argument("--benchmark", action="store_true", help="Print timing and output-size summary fields.")
    parser.add_argument("--verbose", action="store_true", help="Print detailed startup/summary information.")
    parser.add_argument("--quiet", action="store_true", help="Suppress routine console output.")
    parser.add_argument("--log-every", type=positive_int, default=1, help="In follow mode, print one compact update every N processed updates. Default: 1.")
    parser.add_argument("--event-limit", type=positive_int, default=200, help="Maximum rolling live event timeline records to retain. Default: 200.")
    parser.add_argument("--overlay-debug-target-limit", type=non_negative_int, default=50, help="Maximum candidates to include in overlay_debug_state.json. Default: 50.")
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

    if args.require_compact_packets and args.input_source == RAW_TICK_SOURCE:
        parser.error("--require-compact-packets cannot be combined with --input-source raw-ticks")

    if not args.once and not args.follow:
        args.once = True

    if args.max_new_ticks_per_update is None:
        args.max_new_ticks_per_update = 1 if args.latency_mode == "realtime" else 0

    if args.candidate_output_window is None:
        args.candidate_output_window = "latest" if args.latency_mode == "realtime" else "rolling"

    if args.liveness_mode is None:
        args.liveness_mode = "delta" if args.latency_mode == "realtime" else "full"

    if args.drop_backlog_to_meet_budget is None:
        args.drop_backlog_to_meet_budget = args.latency_mode == "realtime"

    if args.drain_backlog_on_overrun is None:
        args.drain_backlog_on_overrun = args.latency_mode == "realtime"

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

    if args.compare_input_sources:
        return compare_input_sources(session, args)

    if args.clear_live_output:
        clear_live_outputs(session)

    processor = LiveTargetProcessor(session, args)
    if args.require_compact_packets and (
        not processor.compact_packets_available
        or not processor.compact_packets_recent
        or processor.input_source_active != COMPACT_PACKET_SOURCE
    ):
        print(compact_required_error())
        print(f"session: {session}")
        print(f"compactPacketIndexPath: {processor.compact_packet_state.get('indexPath')}")
        print(f"compactPacketLatestSegment: {processor.compact_packet_state.get('latestSegment')}")
        print(f"compactPacketsRecent: {str(bool(processor.compact_packets_recent)).lower()}")
        return 1
    if not args.quiet:
        print_startup(session, args, processor)

    startup_added = processor.initialize_from_existing()
    result = processor.process_window(force_rebuild=args.force_window_rebuild, rebuild_reason="startup")

    if not args.quiet and (args.summary or args.once or args.benchmark or args.verbose):
        print_started = time.perf_counter()
        print_result_summary(result, processor.tailer)
        result["status"]["timingBreakdownMillis"]["consolePrintMillis"] = round((time.perf_counter() - print_started) * 1000.0, 3)

    if args.once:
        return 0

    started = time.monotonic()
    update_count = 0
    if startup_added and args.summary and not args.quiet:
        update_count += 1
        print_follow_update(startup_added, result)

    try:
        while True:
            added, result = processor.poll_new_records(force_rebuild=args.force_window_rebuild)
            update_count += 1
            should_log = (
                not args.quiet
                and (added or args.summary or args.benchmark)
                and (update_count % max(1, args.log_every) == 0)
            )
            if should_log:
                print_started = time.perf_counter()
                print_follow_update(added, result)
                result["status"]["timingBreakdownMillis"]["consolePrintMillis"] = round((time.perf_counter() - print_started) * 1000.0, 3)

            if args.max_runtime_seconds is not None and time.monotonic() - started >= args.max_runtime_seconds:
                break

            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("\nStopping live target processor.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

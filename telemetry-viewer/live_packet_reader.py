import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


PACKET_SCHEMA = "osrs_telemetry_live_packet.v1"
INDEX_SCHEMA = "live_packet_index.v1"
LIVE_PACKETS_DIR = "live_packets"
INDEX_FILE_NAME = "live_packet_index.json"
LATEST_SEGMENT_FILE_NAME = "latest_segment.txt"
SEGMENT_GLOB = "live-*.ndjson"


@dataclass
class PacketReadResult:
    path: Path
    line_number: int
    record: dict | None
    error: Exception | None


def live_packet_dir(session_path: Path) -> Path:
    return session_path / LIVE_PACKETS_DIR


def live_packet_index_path(session_path: Path) -> Path:
    return live_packet_dir(session_path) / INDEX_FILE_NAME


def latest_segment_pointer_path(session_path: Path) -> Path:
    return live_packet_dir(session_path) / LATEST_SEGMENT_FILE_NAME


def safe_read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    return data if isinstance(data, dict) else None


def read_index(session_path: Path) -> dict | None:
    index = safe_read_json(live_packet_index_path(session_path))

    if not index or index.get("schema") != INDEX_SCHEMA:
        return None

    return index


def read_latest_segment_name(session_path: Path) -> str | None:
    try:
        value = latest_segment_pointer_path(session_path).read_text(encoding="utf-8").strip()
    except OSError:
        return None

    return value or None


def resolve_segment_path(session_path: Path, segment_name: str | None) -> Path | None:
    if not segment_name:
        return None

    live_dir = live_packet_dir(session_path)
    path = (live_dir / segment_name).resolve()

    try:
        path.relative_to(live_dir.resolve())
    except ValueError:
        return None

    return path if path.exists() else None


def latest_segment_path(session_path: Path, *, index: dict | None = None) -> Path | None:
    pointer = resolve_segment_path(session_path, read_latest_segment_name(session_path))

    if pointer:
        return pointer

    index = index if index is not None else read_index(session_path)

    if index:
        for key in ("activeSegment", "latestSegment"):
            candidate = resolve_segment_path(session_path, index.get(key))

            if candidate:
                return candidate

    files = list_live_packet_files(session_path, use_index=False)
    return files[-1] if files else None


def list_live_packet_files(
    session_path: Path,
    *,
    latest_only: bool = False,
    use_index: bool = True,
) -> list[Path]:
    if latest_only:
        latest = latest_segment_path(session_path)
        return [latest] if latest else []

    live_dir = live_packet_dir(session_path)
    index = read_index(session_path) if use_index else None
    files: list[Path] = []

    if index:
        for segment in index.get("segments") or []:
            if not isinstance(segment, dict):
                continue

            path = resolve_segment_path(session_path, segment.get("path"))

            if path:
                files.append(path)

        if files:
            return files

    return sorted(live_dir.glob(SEGMENT_GLOB)) if live_dir.exists() else []


def iter_live_packets(
    files: list[Path],
    *,
    packet_type: str | None = None,
    since_sequence: int | None = None,
    since_tick: int | None = None,
    max_lines: int | None = None,
    ignore_partial_last_line: bool = True,
) -> Iterator[PacketReadResult]:
    read_count = 0

    for file_path in files:
        try:
            with file_path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if max_lines is not None and read_count >= max_lines:
                        return

                    text = line.strip()

                    if not text:
                        continue

                    try:
                        record = json.loads(text)
                    except json.JSONDecodeError as error:
                        if ignore_partial_last_line and not line.endswith("\n"):
                            return

                        yield PacketReadResult(file_path, line_number, None, error)
                        continue

                    if not isinstance(record, dict):
                        yield PacketReadResult(file_path, line_number, None, ValueError("packet is not an object"))
                        continue

                    if packet_type and record.get("packetType") != packet_type:
                        continue

                    sequence = record.get("sequence")
                    tick = record.get("tick")

                    if since_sequence is not None and isinstance(sequence, int) and sequence <= since_sequence:
                        continue

                    if since_tick is not None and isinstance(tick, int) and tick <= since_tick:
                        continue

                    read_count += 1
                    yield PacketReadResult(file_path, line_number, record, None)
        except OSError as error:
            yield PacketReadResult(file_path, 0, None, error)


def tail_latest_packets(
    session_path: Path,
    *,
    packet_type: str | None = None,
    since_sequence: int | None = None,
    since_tick: int | None = None,
    poll_interval: float = 1.0,
) -> Iterator[PacketReadResult]:
    offsets: dict[Path, int] = {}

    while True:
        latest = latest_segment_path(session_path)

        if latest:
            offset = offsets.get(latest, 0)

            try:
                with latest.open("r", encoding="utf-8") as handle:
                    handle.seek(offset)
                    line_number = 0

                    while True:
                        line = handle.readline()

                        if not line:
                            break

                        line_number += 1
                        text = line.strip()

                        if not text:
                            continue

                        try:
                            record = json.loads(text)
                        except json.JSONDecodeError as error:
                            if line.endswith("\n"):
                                yield PacketReadResult(latest, line_number, None, error)
                            break

                        if not isinstance(record, dict):
                            yield PacketReadResult(latest, line_number, None, ValueError("packet is not an object"))
                            continue

                        if packet_type and record.get("packetType") != packet_type:
                            continue

                        sequence = record.get("sequence")
                        tick = record.get("tick")

                        if since_sequence is not None and isinstance(sequence, int) and sequence <= since_sequence:
                            continue

                        if since_tick is not None and isinstance(tick, int) and tick <= since_tick:
                            continue

                        yield PacketReadResult(latest, line_number, record, None)

                        if isinstance(sequence, int):
                            since_sequence = sequence

                    offsets[latest] = handle.tell()
            except OSError as error:
                yield PacketReadResult(latest, 0, None, error)

        time.sleep(poll_interval)


def index_packet_counts(index: dict | None) -> dict[str, int]:
    counts: dict[str, int] = {}

    if not index:
        return counts

    for segment in index.get("segments") or []:
        if not isinstance(segment, dict):
            continue

        for packet_type, count in (segment.get("packetCountsByType") or {}).items():
            if isinstance(count, int):
                counts[packet_type] = counts.get(packet_type, 0) + count

    return counts

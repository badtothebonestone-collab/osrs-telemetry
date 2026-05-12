import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from live_packet_reader import (
    PACKET_SCHEMA,
    index_packet_counts,
    iter_live_packets,
    list_live_packet_files,
    live_packet_dir,
    read_index,
    tail_latest_packets,
)
from telemetry_paths import find_newest_session

PACKET_NAVIGATION = "live_navigation_packet.v1"
PACKET_COLLISION_WINDOW = "live_collision_window_packet.v1"
PACKET_COLLISION_GRID = "live_collision_grid_packet.v1"
PACKET_WATCH_VALUES = "live_watch_values_packet.v1"


def latest_packet(session_path: Path, packet_type: str) -> dict | None:
    latest = None
    for result in iter_live_packets(
        list_live_packet_files(session_path, latest_only=True, use_index=True),
        packet_type=packet_type,
        ignore_partial_last_line=True,
    ):
        if result.error is not None or not isinstance(result.record, dict):
            continue
        latest = result.record
    return latest


def latest_navigation_packet_summary(session_path: Path) -> dict:
    latest = latest_packet(session_path, PACKET_NAVIGATION)

    if not latest:
        return {
            "latestNavigationTick": None,
            "collisionKnown": None,
            "blockedMovementTileCount": None,
            "blockedFullTileCount": None,
            "collisionHash": None,
        }

    payload = latest.get("payload") if isinstance(latest.get("payload"), dict) else {}
    collision = payload.get("collision") if isinstance(payload.get("collision"), dict) else {}
    return {
        "latestNavigationTick": latest.get("tick"),
        "collisionKnown": collision.get("collisionKnown"),
        "blockedMovementTileCount": collision.get("blockedMovementTileCount"),
        "blockedFullTileCount": collision.get("blockedFullTileCount"),
        "collisionHash": collision.get("collisionHash") or collision.get("collisionMapVersion"),
    }


def latest_collision_window_packet_summary(session_path: Path) -> dict:
    latest = latest_packet(session_path, PACKET_COLLISION_WINDOW)

    if not latest:
        return {
            "latestCollisionWindowTick": None,
            "collisionWindowAvailable": False,
            "windowRadius": None,
            "width": None,
            "height": None,
            "tileCount": None,
            "collisionWindowHash": None,
            "approxPacketBytes": None,
        }

    payload = latest.get("payload") if isinstance(latest.get("payload"), dict) else {}
    approximate_bytes = len(json.dumps(latest, separators=(",", ":")).encode("utf-8"))
    return {
        "latestCollisionWindowTick": latest.get("tick"),
        "collisionWindowAvailable": bool(payload.get("flags")),
        "windowRadius": payload.get("windowRadius"),
        "width": payload.get("width"),
        "height": payload.get("height"),
        "tileCount": payload.get("collisionWindowTileCount"),
        "collisionWindowHash": payload.get("collisionWindowHash") or payload.get("windowHash"),
        "approxPacketBytes": approximate_bytes,
    }


def latest_watch_values_packet_summary(session_path: Path) -> dict:
    latest = latest_packet(session_path, PACKET_WATCH_VALUES)

    if not latest:
        return {
            "latestWatchValuesTick": None,
            "activeWatchCount": None,
            "valueCount": 0,
            "changedCount": 0,
            "watchBudgetExceeded": None,
        }

    payload = latest.get("payload") if isinstance(latest.get("payload"), dict) else {}
    values = payload.get("values") if isinstance(payload.get("values"), list) else []
    changed = [item for item in values if isinstance(item, dict) and item.get("changed")]
    return {
        "latestWatchValuesTick": latest.get("tick"),
        "activeWatchCount": payload.get("activeWatchCount"),
        "valueCount": len(values),
        "changedCount": len(changed),
        "watchBudgetExceeded": payload.get("watchBudgetExceeded"),
    }


def summarize_live_packets(
    session_path: Path,
    *,
    packet_type: str | None = None,
    since_sequence: int | None = None,
    since_tick: int | None = None,
    max_lines: int | None = None,
    latest_only: bool = False,
) -> dict:
    index = read_index(session_path)
    can_use_index = (
        index is not None
        and not packet_type
        and since_sequence is None
        and since_tick is None
        and max_lines is None
        and not latest_only
    )

    if can_use_index:
        packet_types = Counter(index_packet_counts(index))
        schemas = Counter({PACKET_SCHEMA: sum(packet_types.values())}) if packet_types else Counter()
        malformed = 0
        unreadable = 0
        latest_tick = index.get("latestTick")
        latest_sequence = index.get("latestSequence")
        total_bytes = int(index.get("totalBytes") or 0)
        file_count = len(index.get("segments") or [])
        files = [
            str(live_packet_dir(session_path) / segment.get("path"))
            for segment in index.get("segments") or []
            if isinstance(segment, dict) and segment.get("path")
        ]
        used_index = True
    else:
        files_for_scan = list_live_packet_files(session_path, latest_only=latest_only, use_index=True)
        packet_types = Counter()
        schemas = Counter()
        malformed = 0
        unreadable = 0
        latest_tick = None
        latest_sequence = None
        total_bytes = 0

        for file_path in files_for_scan:
            try:
                total_bytes += file_path.stat().st_size
            except OSError:
                pass

        for result in iter_live_packets(
            files_for_scan,
            packet_type=packet_type,
            since_sequence=since_sequence,
            since_tick=since_tick,
            max_lines=max_lines,
        ):
            if result.error is not None:
                if isinstance(result.error, json.JSONDecodeError):
                    malformed += 1
                else:
                    unreadable += 1
                continue

            record = result.record

            if not isinstance(record, dict):
                malformed += 1
                continue

            packet_types[str(record.get("packetType") or "missing")] += 1
            schemas[str(record.get("schema") or "missing")] += 1

            tick = record.get("tick")
            sequence = record.get("sequence")

            if isinstance(tick, int):
                latest_tick = tick if latest_tick is None else max(latest_tick, tick)

            if isinstance(sequence, int):
                latest_sequence = sequence if latest_sequence is None else max(latest_sequence, sequence)

        file_count = len(files_for_scan)
        files = [str(path) for path in files_for_scan]
        used_index = False

    retention = {}

    if index:
        retention = {
            "retentionBytes": index.get("retentionBytes"),
            "retentionSegments": index.get("retentionSegments"),
            "retentionTicks": index.get("retentionTicks"),
            "prunedCount": index.get("prunedCount"),
            "activeSegment": index.get("activeSegment"),
            "latestSegment": index.get("latestSegment"),
        }

    navigation_summary = latest_navigation_packet_summary(session_path)
    collision_window_summary = latest_collision_window_packet_summary(session_path)
    watch_values_summary = latest_watch_values_packet_summary(session_path)

    return {
        "schema": "live_packet_inspection.v1",
        "sessionPath": str(session_path),
        "livePacketDir": str(live_packet_dir(session_path)),
        "indexPresent": index is not None,
        "usedIndex": used_index,
        "fileCount": file_count,
        "files": files,
        "totalBytes": total_bytes,
        "packetTypeCounts": dict(packet_types.most_common()),
        "schemaCounts": dict(schemas.most_common()),
        "latestTick": latest_tick,
        "latestSequence": latest_sequence,
        "malformedLines": malformed,
        "unreadableFiles": unreadable,
        "expectedEnvelopeSchemaPresent": PACKET_SCHEMA in schemas or bool(packet_types),
        "retention": retention,
        "navigation": navigation_summary,
        "collisionWindow": collision_window_summary,
        "watchValues": watch_values_summary,
    }


def print_summary(summary: dict) -> None:
    print("Compact live packet inspection")
    print(f"session: {summary['sessionPath']}")
    print(f"live packet dir: {summary['livePacketDir']}")
    print(f"index present: {summary['indexPresent']}")
    print(f"used index: {summary['usedIndex']}")
    print(f"segments: {summary['fileCount']}")
    print(f"bytes: {summary['totalBytes']}")
    print(f"latest tick: {summary['latestTick']}")
    print(f"latest sequence: {summary['latestSequence']}")
    navigation = summary.get("navigation") or {}
    print(f"latest navigation tick: {navigation.get('latestNavigationTick')}")
    print(f"collision known: {navigation.get('collisionKnown')}")
    print(f"blocked movement tiles: {navigation.get('blockedMovementTileCount')}")
    print(f"collision hash: {navigation.get('collisionHash')}")
    collision_window = summary.get("collisionWindow") or {}
    print(f"latest collision window tick: {collision_window.get('latestCollisionWindowTick')}")
    print(f"collision window available: {collision_window.get('collisionWindowAvailable')}")
    print(
        "collision window: "
        f"radius={collision_window.get('windowRadius')} "
        f"size={collision_window.get('width')}x{collision_window.get('height')} "
        f"tiles={collision_window.get('tileCount')} "
        f"hash={collision_window.get('collisionWindowHash')}"
    )
    print(f"latest collision window packet bytes: {collision_window.get('approxPacketBytes')}")
    watch_values = summary.get("watchValues") or {}
    print(f"latest watch values tick: {watch_values.get('latestWatchValuesTick')}")
    print(
        "watch values: "
        f"active={watch_values.get('activeWatchCount')} "
        f"values={watch_values.get('valueCount')} "
        f"changed={watch_values.get('changedCount')} "
        f"budgetExceeded={watch_values.get('watchBudgetExceeded')}"
    )
    print(f"malformed lines: {summary['malformedLines']}")
    print(f"unreadable files: {summary['unreadableFiles']}")

    retention = summary.get("retention") or {}

    if retention:
        print(f"active segment: {retention.get('activeSegment')}")
        print(f"latest segment: {retention.get('latestSegment')}")
        print(f"retention bytes: {retention.get('retentionBytes')}")
        print(f"retention segments: {retention.get('retentionSegments')}")
        print(f"retention ticks: {retention.get('retentionTicks')}")
        print(f"segments pruned: {retention.get('prunedCount')}")

    print("packet types:")

    if summary["packetTypeCounts"]:
        for packet_type, count in summary["packetTypeCounts"].items():
            print(f"  {packet_type}: {count}")
    else:
        print("  none")


def resolve_session(args: argparse.Namespace) -> Path:
    if args.session:
        return Path(args.session).expanduser()

    if args.latest_session:
        session = find_newest_session(args.sessions_dir)

        if session is None:
            raise SystemExit("No telemetry session found.")

        return session

    raise SystemExit("Pass --session or --latest-session.")


def tail_packets(args: argparse.Namespace, session_path: Path) -> None:
    printed = 0

    for result in tail_latest_packets(
        session_path,
        packet_type=args.packet_type,
        since_sequence=args.since_sequence,
        since_tick=args.since_tick,
    ):
        if result.error is not None:
            print(f"{result.path}: {result.error}", file=sys.stderr)
            continue

        if result.record is None:
            continue

        print(json.dumps(result.record, sort_keys=False))
        printed += 1

        if args.max_lines is not None and printed >= args.max_lines:
            return


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect compact live NDJSON packet files.")
    parser.add_argument("--session", help="Telemetry session path.")
    parser.add_argument("--sessions-dir", help="Telemetry sessions root for --latest-session.")
    parser.add_argument("--latest-session", action="store_true", help="Use the newest available session.")
    parser.add_argument("--tail", action="store_true", help="Print new packet records from the latest active segment.")
    parser.add_argument("--latest-only", action="store_true", help="Read only the latest active segment.")
    parser.add_argument("--packet-type", help="Only read packets with this packetType.")
    parser.add_argument("--since-sequence", type=int, help="Only read packets with sequence greater than this value.")
    parser.add_argument("--since-tick", type=int, help="Only read packets with tick greater than this value.")
    parser.add_argument("--max-lines", type=int, default=None, help="Maximum matching packet records to read or print.")
    parser.add_argument("--limit", type=int, default=None, help="Alias for --max-lines.")
    parser.add_argument("--summary", action="store_true", help="Print a compact summary.")
    args = parser.parse_args(argv)

    if args.max_lines is None and args.limit is not None:
        args.max_lines = args.limit

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    session_path = resolve_session(args)

    if args.tail:
        tail_packets(args, session_path)
        return 0

    summary = summarize_live_packets(
        session_path,
        packet_type=args.packet_type,
        since_sequence=args.since_sequence,
        since_tick=args.since_tick,
        max_lines=args.max_lines,
        latest_only=args.latest_only,
    )
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

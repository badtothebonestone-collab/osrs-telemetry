import argparse
import json
from pathlib import Path

from label_ranges import load_label_ranges
from tab_detection import infer_active_tab, load_rules
from telemetry_paths import (
    find_newest_session,
    get_sessions_dir,
    iter_jsonl,
    list_event_files,
    list_tick_files,
)


MISSING_PERCEPTION_MESSAGE = (
    "Perception dataset not found. "
    "Run python telemetry-viewer\\build_perception_dataset.py first."
)
DEFAULT_EVENT_WINDOW_TICKS = 2


def parse_tick_id(value: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected integer tick id: {value}") from error


def parse_args():
    parser = argparse.ArgumentParser(
        description="Inspect active side-tab inference from existing OSRS telemetry.",
    )
    parser.add_argument("--session", help="Telemetry session directory to inspect.")
    parser.add_argument("--sessions-dir", help="Override the telemetry sessions directory when --session is omitted.")
    parser.add_argument("--labels", help="Path to manual tab label ranges JSON. Defaults to telemetry-viewer\\tab_labels.json when present.")
    parser.add_argument("--tick", type=parse_tick_id, help="Inspect one tick.")
    parser.add_argument("--range", nargs=2, type=parse_tick_id, metavar=("START", "END"), help="Inspect an inclusive tick range.")
    parser.add_argument("--limit", type=int, default=25, help="Maximum rows to print unless --tick is used. Default: 25.")
    parser.add_argument("--unknown-only", action="store_true", help="Print only rows where activeTab is unknown.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON lines.")
    return parser.parse_args()


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def perception_paths(session: Path) -> tuple[Path, Path]:
    perception_dir = session / "perception"
    return perception_dir / "tick_bundles.jsonl", perception_dir / "event_windows.jsonl"


def json_dump_compact(data) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def evidence_text(evidence) -> str:
    if not isinstance(evidence, list) or not evidence:
        return "-"

    parts = []

    for item in evidence[:3]:
        if isinstance(item, dict):
            detail = item.get("detail")
            source = item.get("source")

            if source == "label" and detail:
                parts.append(str(detail))
            elif source and detail:
                parts.append(f"{source}: {detail}")
            elif detail:
                parts.append(str(detail))
            else:
                parts.append(json_dump_compact(item))
        else:
            parts.append(str(item))

    if len(evidence) > 3:
        parts.append(f"+{len(evidence) - 3} more")

    return "; ".join(parts)


def compact_result_text(row: dict) -> str:
    inference = row.get("inference") if isinstance(row.get("inference"), dict) else {}
    confidence = inference.get("confidence")
    confidence_text = f"{confidence:.2f}" if isinstance(confidence, (int, float)) else "?"

    return (
        f"tick={row.get('tickId', '?')} "
        f"activeTab={inference.get('activeTab', 'unknown')} "
        f"source={inference.get('source', 'unknown')} "
        f"confidence={confidence_text} "
        f"evidence={evidence_text(inference.get('evidence'))}"
    )


def selected_by_tick_args(tick_id, args) -> bool:
    if args.tick is not None:
        return tick_id == args.tick

    if args.range is not None:
        start, end = args.range
        return isinstance(tick_id, int) and start <= tick_id <= end

    return True


def iter_jsonl_objects(path: Path):
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Malformed {path.name} at line {line_number}: {error}") from error

            if isinstance(record, dict):
                yield record


def load_event_windows(path: Path) -> dict[int, list[dict]]:
    windows = {}

    if not path.exists():
        return windows

    for window in iter_jsonl_objects(path):
        tick_id = window.get("tickId")

        if not isinstance(tick_id, int):
            continue

        events = []

        for key in ("previousEvents", "currentEvents", "nextEvents"):
            values = window.get(key)

            if isinstance(values, list):
                events.extend(event for event in values if isinstance(event, dict))

        windows[tick_id] = events

    return windows


def bundle_nearby_events(bundle: dict, event_windows: dict[int, list[dict]]) -> list[dict]:
    tick_id = bundle.get("tickId")
    events = bundle.get("events")

    if isinstance(events, dict):
        nearby = events.get("nearbyEventSummaries")

        if isinstance(nearby, list):
            return [event for event in nearby if isinstance(event, dict)]

    if isinstance(tick_id, int):
        return event_windows.get(tick_id, [])

    return []


class RawTickCache:
    def __init__(self, session: Path):
        self.records = iter_jsonl(list_tick_files(session))
        self.cache: dict[int, dict] = {}
        self.exhausted = False

    def get(self, tick_id) -> dict | None:
        if not isinstance(tick_id, int):
            return None

        if tick_id in self.cache:
            return self.cache[tick_id]

        if self.exhausted:
            return None

        for _source, tick in self.records:
            if not isinstance(tick, dict):
                continue

            current_tick_id = tick.get("tickId")

            if isinstance(current_tick_id, int):
                self.cache[current_tick_id] = tick

            if current_tick_id == tick_id:
                return tick

        self.exhausted = True
        return None


def infer_for_bundle(
    bundle: dict,
    event_windows: dict[int, list[dict]],
    raw_ticks: RawTickCache | None,
    rules: dict,
    labels: dict,
) -> dict:
    nearby_events = bundle_nearby_events(bundle, event_windows)
    raw_tick = raw_ticks.get(bundle.get("tickId")) if raw_ticks else None
    tick_for_inference = raw_tick if isinstance(raw_tick, dict) else bundle
    inference = infer_active_tab(tick_for_inference, nearby_events=nearby_events, rules=rules, labels=labels)
    return {
        "tickId": bundle.get("tickId"),
        "timestampUtc": bundle.get("timestampUtc"),
        "dataSource": "perception+raw-tick" if raw_tick else "perception",
        "inference": inference,
    }


def load_raw_events_by_tick(session: Path) -> dict[int, list[dict]]:
    events_by_tick: dict[int, list[dict]] = {}

    for _source, event in iter_jsonl(list_event_files(session)):
        if not isinstance(event, dict):
            continue

        tick_id = event.get("tickId")

        if isinstance(tick_id, int):
            events_by_tick.setdefault(tick_id, []).append(event)

    return events_by_tick


def raw_nearby_events(events_by_tick: dict[int, list[dict]], tick_id: int) -> list[dict]:
    events = []

    for candidate_tick in range(tick_id - DEFAULT_EVENT_WINDOW_TICKS, tick_id + DEFAULT_EVENT_WINDOW_TICKS + 1):
        for event in events_by_tick.get(candidate_tick, []):
            enriched = dict(event)
            enriched["relativeTick"] = candidate_tick - tick_id
            events.append(enriched)

    return events


def infer_for_raw_tick(tick: dict, events_by_tick: dict[int, list[dict]], rules: dict, labels: dict) -> dict:
    tick_id = tick.get("tickId")
    nearby_events = raw_nearby_events(events_by_tick, tick_id) if isinstance(tick_id, int) else []
    inference = infer_active_tab(tick, nearby_events=nearby_events, rules=rules, labels=labels)
    return {
        "tickId": tick_id,
        "timestampUtc": tick.get("timestampUtc"),
        "dataSource": "raw",
        "inference": inference,
    }


def should_print(row: dict, args) -> bool:
    inference = row.get("inference") if isinstance(row.get("inference"), dict) else {}

    if args.unknown_only and inference.get("activeTab") != "unknown":
        return False

    return True


def print_row(row: dict, args) -> None:
    if args.json:
        print(json_dump_compact(row))
    else:
        print(compact_result_text(row))


def inspect_perception(session: Path, args, rules: dict, labels: dict) -> int | None:
    bundle_path, event_window_path = perception_paths(session)

    if not bundle_path.exists():
        return None

    event_windows = load_event_windows(event_window_path)
    raw_ticks = RawTickCache(session) if list_tick_files(session) else None
    printed = 0
    matched_tick = False
    limit = None if args.tick is not None else max(0, args.limit)

    for bundle in iter_jsonl_objects(bundle_path):
        if limit is not None and printed >= limit:
            break

        tick_id = bundle.get("tickId")

        if not selected_by_tick_args(tick_id, args):
            continue

        if args.tick is not None:
            matched_tick = True

        row = infer_for_bundle(bundle, event_windows, raw_ticks, rules, labels)

        if not should_print(row, args):
            continue

        print_row(row, args)
        printed += 1

        if args.tick is not None:
            break

    if args.tick is not None and not matched_tick:
        print(f"No perception bundle matched tick {args.tick}.")
        return 1

    if printed == 0:
        print("No tab detection rows matched the requested filters.")

    return 0


def inspect_raw(session: Path, args, rules: dict, labels: dict) -> int:
    tick_files = list_tick_files(session)

    if not tick_files:
        print(f"session: {session}")
        print(MISSING_PERCEPTION_MESSAGE)
        print("No raw tick files were available for fallback inspection.")
        return 1

    print(f"session: {session}")
    print("Perception dataset not found; falling back to raw tick/event telemetry.")

    events_by_tick = load_raw_events_by_tick(session)
    printed = 0
    matched_tick = False
    limit = None if args.tick is not None else max(0, args.limit)

    for _source, tick in iter_jsonl(tick_files):
        if limit is not None and printed >= limit:
            break

        if not isinstance(tick, dict):
            continue

        tick_id = tick.get("tickId")

        if not selected_by_tick_args(tick_id, args):
            continue

        if args.tick is not None:
            matched_tick = True

        row = infer_for_raw_tick(tick, events_by_tick, rules, labels)

        if not should_print(row, args):
            continue

        print_row(row, args)
        printed += 1

        if args.tick is not None:
            break

    if args.tick is not None and not matched_tick:
        print(f"No raw tick matched tick {args.tick}.")
        return 1

    if printed == 0:
        print("No tab detection rows matched the requested filters.")

    return 0


def main() -> int:
    args = parse_args()

    if args.range is not None:
        start, end = args.range

        if end < start:
            args.range = (end, start)

    session = resolve_session(args)

    if session is None:
        print(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")
        return 1

    rules = load_rules()
    labels = load_label_ranges(args.labels)

    for warning in labels.get("warnings", []):
        print(f"label warning: {warning}")

    try:
        result = inspect_perception(session, args, rules, labels)

        if result is not None:
            return result

        return inspect_raw(session, args, rules, labels)
    except (OSError, ValueError) as error:
        print(f"Unable to inspect tab detection: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

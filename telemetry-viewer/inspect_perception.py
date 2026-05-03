import argparse
import json
from pathlib import Path

from telemetry_paths import find_newest_session, get_sessions_dir, safe_read_json


MISSING_PERCEPTION_MESSAGE = (
    "Perception dataset not found. "
    "Run python telemetry-viewer\\build_perception_dataset.py first."
)


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def perception_paths(session: Path) -> tuple[Path, Path]:
    perception_dir = session / "perception"
    return perception_dir / "perception_index.json", perception_dir / "tick_bundles.jsonl"


def json_dump_compact(data) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def value_or_unknown(value) -> str:
    if value is None or value == "":
        return "?"

    return str(value)


def bool_marker(value) -> str:
    if value is True:
        return "yes"

    if value is False:
        return "no"

    return "?"


def ms(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.0f}ms"

    return "?"


def position_text(position: dict) -> str:
    if not isinstance(position, dict):
        return "?"

    return ",".join(
        value_or_unknown(position.get(key))
        for key in ("worldX", "worldY", "plane")
    )


def pair_text(values: dict, first_key: str = "boosted", second_key: str = "real") -> str:
    if not isinstance(values, dict):
        return "?/?"

    return f"{value_or_unknown(values.get(first_key))}/{value_or_unknown(values.get(second_key))}"


def list_preview(values, *, limit: int = 8) -> str:
    if not isinstance(values, list) or not values:
        return "-"

    labels = [str(value) for value in values if value is not None]

    if len(labels) <= limit:
        return ",".join(labels)

    return ",".join(labels[:limit]) + f",+{len(labels) - limit}"


def flags_text(derived: dict) -> str:
    if not isinstance(derived, dict):
        return "-"

    flags = []
    mapping = (
        ("combat", "hasCombatSignal"),
        ("inventory", "hasInventorySignal"),
        ("ui", "hasUiSignal"),
        ("var", "hasVarSignal"),
        ("frameIssue", "hasFrameIssue"),
        ("captureError", "hasCaptureError"),
    )

    for label, key in mapping:
        if derived.get(key):
            flags.append(label)

    return ",".join(flags) if flags else "-"


def compact_bundle_text(bundle: dict) -> str:
    frame = bundle.get("frame") if isinstance(bundle.get("frame"), dict) else {}
    state = bundle.get("state") if isinstance(bundle.get("state"), dict) else {}
    events = bundle.get("events") if isinstance(bundle.get("events"), dict) else {}
    derived = bundle.get("derived") if isinstance(bundle.get("derived"), dict) else {}

    line_parts = [
        f"tick={value_or_unknown(bundle.get('tickId'))}",
        f"time={value_or_unknown(bundle.get('timestampUtc'))}",
        (
            "frame="
            f"exists:{bool_marker(frame.get('exists'))} "
            f"status:{value_or_unknown(frame.get('frameIndexStatus'))} "
            f"latency:{ms(frame.get('totalLatencyMs'))} "
            f"write:{ms(frame.get('writeDelayMs'))}"
        ),
        f"gameState={value_or_unknown(state.get('gameState'))}",
        f"pos={position_text(state.get('position'))}",
        (
            "stats="
            f"hp:{pair_text(state.get('hp'))} "
            f"prayer:{pair_text(state.get('prayer'))} "
            f"run:{value_or_unknown(state.get('runEnergyPercent'))}"
        ),
        f"target={value_or_unknown(state.get('interacting'))}",
        f"events={list_preview(events.get('onTickEventTypes'))}",
        f"flags={flags_text(derived)}",
        f"summary={value_or_unknown(derived.get('summary'))}",
    ]
    return " | ".join(line_parts)


def print_summary(index: dict, session: Path) -> None:
    print(f"session: {session}")
    print(f"generatedAtUtc: {value_or_unknown(index.get('generatedAtUtc'))}")
    print(
        "ticks: "
        f"{value_or_unknown(index.get('tickBundleCount'))} "
        f"({value_or_unknown(index.get('firstTickId'))}-"
        f"{value_or_unknown(index.get('lastTickId'))})"
    )
    print(
        "frames: "
        f"exists={value_or_unknown(index.get('frameExistsCount'))} "
        f"missing={value_or_unknown(index.get('frameMissingCount'))} "
        f"issues={value_or_unknown(index.get('frameIssueCount'))}"
    )
    print(
        "signals: "
        f"combat={value_or_unknown(index.get('combatSignalCount'))} "
        f"inventory={value_or_unknown(index.get('inventorySignalCount'))} "
        f"ui={value_or_unknown(index.get('uiSignalCount'))} "
        f"captureErrors={value_or_unknown(index.get('captureErrorCount'))}"
    )
    print(f"topEventTypes: {counter_preview(index.get('topEventTypes'))}")
    print(f"topEventCategories: {counter_preview(index.get('topEventCategories'))}")
    print(f"healthStateCounts: {counter_preview(index.get('healthStateCounts'))}")

    warnings = index.get("warnings")

    if isinstance(warnings, list) and warnings:
        print("warnings:")

        for warning in warnings:
            print(f"  - {warning}")


def counter_preview(values, *, limit: int = 8) -> str:
    if not isinstance(values, dict) or not values:
        return "-"

    items = list(values.items())
    parts = [f"{key}={value}" for key, value in items[:limit]]

    if len(items) > limit:
        parts.append(f"+{len(items) - limit}")

    return ", ".join(parts)


def parse_tick_id(value: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected integer tick id: {value}") from error


def selected_by_tick_args(bundle: dict, args) -> bool:
    tick_id = bundle.get("tickId")

    if args.tick is not None:
        return tick_id == args.tick

    if args.range is not None:
        start, end = args.range
        return isinstance(tick_id, int) and start <= tick_id <= end

    return True


def signal_filters(args) -> list[str]:
    filters = []

    if args.combat:
        filters.append("hasCombatSignal")

    if args.inventory:
        filters.append("hasInventorySignal")

    if args.ui:
        filters.append("hasUiSignal")

    if args.var:
        filters.append("hasVarSignal")

    if args.frame_issues:
        filters.append("hasFrameIssue")

    if args.capture_errors:
        filters.append("hasCaptureError")

    return filters


def selected_by_signal_filters(bundle: dict, filters: list[str]) -> bool:
    if not filters:
        return True

    derived = bundle.get("derived")

    if not isinstance(derived, dict):
        return False

    return any(bool(derived.get(key)) for key in filters)


def iter_bundle_records(bundle_path: Path):
    with bundle_path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Malformed tick_bundles.jsonl at line {line_number}: {error}") from error

            if isinstance(record, dict):
                yield record


def print_matching_bundles(bundle_path: Path, args) -> int:
    filters = signal_filters(args)
    printed = 0
    matched = 0
    limit = None if args.tick is not None else max(0, args.limit)

    for bundle in iter_bundle_records(bundle_path):
        if not selected_by_tick_args(bundle, args):
            continue

        if not selected_by_signal_filters(bundle, filters):
            continue

        matched += 1

        if args.json:
            print(json_dump_compact(bundle))
        else:
            print(compact_bundle_text(bundle))

        printed += 1

        if limit is not None and printed >= limit:
            break

        if args.tick is not None:
            break

    if args.tick is not None and matched == 0:
        print(f"No perception bundle matched tick {args.tick}.")
        return 1

    if matched == 0:
        print("No perception bundles matched the requested filters.")

    return 0


def parse_args():
    parser = argparse.ArgumentParser(
        description="Inspect derived OSRS telemetry perception bundles.",
        epilog=(
            "Signal filters such as --combat, --ui, and --frame-issues combine with OR. "
            "--tick or --range first constrain the tick set, then signal filters apply."
        ),
    )
    parser.add_argument("--session", help="Telemetry session directory to inspect.")
    parser.add_argument("--sessions-dir", help="Override the telemetry sessions directory when --session is omitted.")
    parser.add_argument("--summary", action="store_true", help="Print perception_index.json as compact readable text.")
    parser.add_argument("--tick", type=parse_tick_id, help="Print only one tick bundle.")
    parser.add_argument("--range", nargs=2, type=parse_tick_id, metavar=("START", "END"), help="Print an inclusive tick range.")
    parser.add_argument("--combat", action="store_true", help="Show ticks with hasCombatSignal.")
    parser.add_argument("--inventory", action="store_true", help="Show ticks with hasInventorySignal.")
    parser.add_argument("--ui", action="store_true", help="Show ticks with hasUiSignal.")
    parser.add_argument("--var", action="store_true", help="Show ticks with hasVarSignal.")
    parser.add_argument("--frame-issues", action="store_true", help="Show ticks with frame issues.")
    parser.add_argument("--capture-errors", action="store_true", help="Show ticks with capture errors.")
    parser.add_argument("--limit", type=int, default=25, help="Maximum compact rows to print unless --tick is used. Default: 25.")
    parser.add_argument("--json", action="store_true", help="Print matching raw tick bundle JSON objects, one per line.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = resolve_session(args)

    if session is None:
        print(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")
        return 1

    index_path, bundle_path = perception_paths(session)
    index = safe_read_json(index_path)

    if not isinstance(index, dict) or not bundle_path.exists():
        print(f"session: {session}")
        print(MISSING_PERCEPTION_MESSAGE)
        return 1

    if args.range is not None:
        start, end = args.range

        if end < start:
            args.range = (end, start)

    try:
        if args.summary:
            print_summary(index, session)
            return 0

        return print_matching_bundles(bundle_path, args)
    except (OSError, ValueError) as error:
        print(f"Unable to inspect perception dataset: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

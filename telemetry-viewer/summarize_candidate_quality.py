import argparse
import json
from collections import Counter
from pathlib import Path

from telemetry_paths import find_newest_session, get_sessions_dir


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def safe_read_json(path: Path) -> dict:
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}

    return value if isinstance(value, dict) else {}


def iter_jsonl(path: Path):
    if not path.exists():
        return

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
                    yield value
    except OSError:
        return


def selected_ticks(records: list[dict], args) -> set[int] | None:
    ticks = sorted({record.get("tickId") for record in records if isinstance(record.get("tickId"), int)})

    if args.tick is not None:
        return {args.tick}

    if args.tick_range is not None:
        start, end = args.tick_range
        return {tick for tick in ticks if start <= tick <= end}

    if args.latest is not None:
        return set(ticks[-args.latest :])

    return None


def candidate_target(candidate: dict) -> dict:
    target = candidate.get("target")
    return target if isinstance(target, dict) else {}


def candidate_name(candidate: dict) -> str:
    target = candidate_target(candidate)
    return str(candidate.get("name") or target.get("name") or target.get("targetName") or target.get("targetId") or "-")


def candidate_id(candidate: dict):
    target = candidate_target(candidate)
    return candidate.get("rawId") or candidate.get("id") or target.get("rawId") or target.get("id") or target.get("targetId")


def candidate_class(candidate: dict) -> str:
    return str(candidate.get("classId") or candidate_target(candidate).get("classId") or "unclassified")


def candidate_type(candidate: dict) -> str:
    return str(candidate.get("targetType") or candidate_target(candidate).get("targetType") or "unknown")


def candidate_role(candidate: dict) -> str:
    return str(candidate.get("role") or candidate_target(candidate).get("targetRole") or "unknown")


def candidate_category(candidate: dict) -> str:
    return str(candidate.get("category") or candidate_target(candidate).get("targetCategory") or "unknown")


def candidate_tags(candidate: dict) -> list[str]:
    tags = candidate.get("tags")

    if not isinstance(tags, list):
        tags = candidate_target(candidate).get("targetTags")

    return [str(tag) for tag in tags] if isinstance(tags, list) else []


def preferred_geometry(candidate: dict) -> str:
    geometry = candidate.get("geometry") if isinstance(candidate.get("geometry"), dict) else {}
    return str(candidate.get("preferredGeometryType") or geometry.get("preferredAimGeometryType") or "none")


def missing_clickbox(candidate: dict) -> bool:
    geometry = candidate.get("geometry") if isinstance(candidate.get("geometry"), dict) else {}
    available = geometry.get("availableGeometryTypes")

    if not isinstance(available, list):
        summary = candidate.get("geometrySummary") if isinstance(candidate.get("geometrySummary"), dict) else {}
        available = summary.get("availableGeometryTypes")

    available = [str(item) for item in available] if isinstance(available, list) else []
    return "clickboxBounds" not in available and "clickboxPolygon" not in available


def fallback_or_unclassified(candidate: dict) -> bool:
    name = candidate_name(candidate)
    role = candidate_role(candidate).lower()
    category = candidate_category(candidate).lower()
    return (
        name.startswith(("SceneObject[", "GroundItem[", "Npc[", "Tile["))
        or candidate_class(candidate) in {"unknown_scene_object", "unclassified_scene_object", "unclassified"}
        or role in {"unknown", "decoration"}
        or category in {"unknown", "sceneobject", "decoration"}
    )


def filter_candidates(records: list[dict], args) -> list[dict]:
    ticks = selected_ticks(records, args)
    filtered = []

    for record in records:
        if ticks is not None and record.get("tickId") not in ticks:
            continue

        if args.profile and record.get("profileId") != args.profile:
            continue

        filtered.append(record)

    return filtered[: args.limit] if args.limit else filtered


def print_counts(title: str, counter: Counter, limit: int = 20) -> None:
    print(f"{title}:")

    if not counter:
        print("  none")
        return

    for key, count in counter.most_common(limit):
        print(f"  {key}: {count}")


def best_candidates_by_tick(records: list[dict]) -> dict[int, dict]:
    best = {}

    for candidate in records:
        tick_id = candidate.get("tickId")

        if not isinstance(tick_id, int):
            continue

        current = best.get(tick_id)

        if current is None or int(candidate.get("score") or 0) > int(current.get("score") or 0):
            best[tick_id] = candidate

    return best


def summarize(session: Path, args) -> int:
    candidates_path = session / "interaction_geometry" / "target_candidates.jsonl"
    index_path = session / "interaction_geometry" / "target_candidates_index.json"
    index = safe_read_json(index_path)
    records = list(iter_jsonl(candidates_path) or [])
    records = filter_candidates(records, args)

    print("Candidate Quality Summary")
    print(f"session: {session}")
    print(f"candidate file present: {'yes' if candidates_path.exists() else 'no'}")
    print(f"candidate index present: {'yes' if index_path.exists() else 'no'}")
    print(f"index profile: {index.get('profileId') or 'none'}")
    print(f"index candidate count: {index.get('candidateCount', 'unknown')}")
    print(f"records considered: {len(records)}")
    print(f"duplicates removed: {index.get('duplicatesRemoved', 0)}")
    print(f"uiBlocked count: {index.get('uiBlockedCount', 0)}")
    print(f"discarded by limit: {index.get('discardedByLimit', 0)}")
    print()

    by_quality = Counter(str(record.get("qualityTier") or "unknown") for record in records)
    by_class = Counter(candidate_class(record) for record in records)
    by_category = Counter(candidate_category(record) for record in records)
    by_role = Counter(candidate_role(record) for record in records)
    by_type = Counter(candidate_type(record) for record in records)
    by_geometry = Counter(preferred_geometry(record) for record in records)
    tags = Counter(tag for record in records for tag in candidate_tags(record))
    object_ids = Counter(str(candidate_id(record)) for record in records if candidate_id(record) is not None)
    positive = Counter(signal for record in records for signal in record.get("positiveSignals") or [])
    negative = Counter(signal for record in records for signal in record.get("negativeSignals") or [])
    reject_reasons = Counter(signal for record in records for signal in record.get("rejectReasons") or [])
    ui_blocked = sum(1 for record in records if record.get("uiBlocked"))
    missing_clickbox_count = sum(1 for record in records if missing_clickbox(record))
    fallback_count = sum(1 for record in records if fallback_or_unclassified(record))

    print_counts("candidates by quality tier", by_quality)
    print_counts("candidates by classId", by_class)
    print_counts("candidates by targetType", by_type)
    print_counts("candidates by role", by_role)
    print_counts("candidates by category", by_category)
    print_counts("candidates by preferred geometry", by_geometry)
    print_counts("top tags", tags)
    print_counts("top object IDs", object_ids)
    print_counts("top positive signals", positive)
    print_counts("top negative signals", negative)
    print_counts("top reject reasons", reject_reasons)
    print()
    print(f"uiBlocked records: {ui_blocked}")
    print(f"missing clickbox records: {missing_clickbox_count}")
    print(f"fallback/unclassified records: {fallback_count}")
    print()
    print("best candidates per tick:")

    for tick_id, candidate in list(sorted(best_candidates_by_tick(records).items()))[:25]:
        print(
            f"  tick={tick_id} rank={candidate.get('rank')} score={candidate.get('score')} "
            f"quality={candidate.get('qualityTier')} class={candidate_class(candidate)} "
            f"type={candidate_type(candidate)} name=\"{candidate_name(candidate)}\" "
            f"geometry={preferred_geometry(candidate)} uiBlocked={str(bool(candidate.get('uiBlocked'))).lower()}"
        )

    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Read-only summary report for target candidate quality metadata.")
    parser.add_argument("--session", help="Telemetry session directory to inspect.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions directory when --session is omitted.")
    parser.add_argument("--latest", type=int, metavar="N", help="Only summarize latest N candidate ticks.")
    parser.add_argument("--range", nargs=2, type=int, dest="tick_range", metavar=("START", "END"), help="Inclusive tick range.")
    parser.add_argument("--tick", type=int, help="Only summarize one tick.")
    parser.add_argument("--profile", help="Only summarize candidates produced by this profile.")
    parser.add_argument("--limit", type=int, default=0, help="Limit records considered after filters. 0 means no limit.")
    args = parser.parse_args()

    if args.tick is not None and args.tick_range is not None:
        parser.error("--tick cannot be combined with --range")

    if args.tick is not None and args.latest is not None:
        parser.error("--tick cannot be combined with --latest")

    if args.tick_range is not None and args.latest is not None:
        parser.error("--range cannot be combined with --latest")

    if args.tick_range is not None:
        start, end = args.tick_range

        if end < start:
            args.tick_range = (end, start)

    if args.latest is not None and args.latest < 1:
        parser.error("--latest must be positive")

    if args.limit < 0:
        parser.error("--limit must be zero or positive")

    return args


def main() -> int:
    args = parse_args()
    session = resolve_session(args)

    if session is None:
        print(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")
        return 1

    return summarize(session.expanduser().resolve(), args)


if __name__ == "__main__":
    raise SystemExit(main())

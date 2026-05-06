import argparse
import json
import os
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from telemetry_paths import find_newest_session, get_sessions_dir


DEFAULT_SCENARIO = "bank_area"
SCHEMA_VERSION_INDEX = "scenario_dataset.index.v1"
SCHEMA_VERSION_RECORD = "scenario_dataset.record.v1"
TEMPLATE_DIR = Path(__file__).resolve().parent / "scenario_templates"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dump_compact(data) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def output_paths(session: Path, scenario_type: str) -> dict[str, Path]:
    output_dir = session / "scenario_datasets"
    return {
        "outputDir": output_dir,
        "scenario": output_dir / f"{scenario_type}.jsonl",
        "index": output_dir / "scenario_index.json",
    }


def read_json(path: Path) -> tuple[dict, list[str]]:
    warnings = []

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, [f"template not found: {path}"]
    except OSError as error:
        return {}, [f"could not read template {path}: {error}"]
    except json.JSONDecodeError as error:
        return {}, [f"invalid template JSON {path}: {error.msg}"]

    if not isinstance(value, dict):
        return {}, [f"template must be a JSON object: {path}"]

    return value, warnings


def load_template(args) -> tuple[dict, Path, list[str]]:
    template_path = Path(args.template).expanduser() if args.template else TEMPLATE_DIR / f"{args.scenario}.json"
    template, warnings = read_json(template_path)

    if template and not template.get("scenarioType"):
        template["scenarioType"] = args.scenario

    return template, template_path, warnings


def read_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    records = []
    warnings = []

    if not path.exists():
        return records, [f"missing file: {path}"]

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
                else:
                    warnings.append(f"{path.name}:{line_number}: expected JSON object")
    except OSError as error:
        warnings.append(f"could not read {path}: {error}")

    return records, warnings


def target_for(record: dict) -> dict:
    value = record.get("target")
    return value if isinstance(value, dict) else {}


def geometry_for(record: dict) -> dict:
    value = record.get("geometry")
    return value if isinstance(value, dict) else {}


def scoring_for(record: dict) -> dict:
    value = record.get("scoring")
    return value if isinstance(value, dict) else {}


def frame_for(record: dict) -> dict:
    value = record.get("frame")
    return value if isinstance(value, dict) else {}


def target_tags(record: dict) -> list[str]:
    value = target_for(record).get("targetTags")

    if not isinstance(value, list):
        return []

    return [str(item) for item in value if item is not None]


def target_id_values(record: dict) -> list[str]:
    target = target_for(record)
    values = []

    for key in ("id", "rawId", "targetId"):
        value = target.get(key)

        if value is not None:
            values.append(str(value))

    return values


def display_name(record: dict) -> str:
    target = target_for(record)

    for key in ("name", "targetName", "objectName", "itemName", "npcName", "fallbackName", "targetId"):
        value = target.get(key)

        if value is not None and str(value).strip():
            return str(value)

    target_type = str(target.get("targetType") or "target")
    target_id = target.get("rawId")

    if target_id is None:
        target_id = target.get("id")

    return f"{target_type}[{target_id if target_id is not None else 'unknown'}]"


def normalized_values(value) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        return [str(item).strip().lower() for item in value if str(item).strip()]

    text = str(value).strip()
    return [text.lower()] if text else []


def matches_any(actual: str | None, expected) -> bool:
    expected_values = normalized_values(expected)

    if not expected_values:
        return True

    return str(actual or "").strip().lower() in expected_values


def rule_matches(candidate: dict, rule: dict) -> bool:
    if not isinstance(rule, dict) or not rule:
        return False

    target = target_for(candidate)
    name_text = display_name(candidate).lower()
    tags = {tag.lower() for tag in target_tags(candidate)}
    ids = {value.lower() for value in target_id_values(candidate)}

    if "category" in rule and not matches_any(target.get("targetCategory"), rule.get("category")):
        return False

    if "role" in rule and not matches_any(target.get("targetRole"), rule.get("role")):
        return False

    if "targetType" in rule and not matches_any(target.get("targetType"), rule.get("targetType")):
        return False

    if "tag" in rule:
        expected_tags = normalized_values(rule.get("tag"))

        if expected_tags and not any(tag in tags for tag in expected_tags):
            return False

    if "nameContains" in rule:
        needles = normalized_values(rule.get("nameContains"))

        if needles and not any(needle in name_text for needle in needles):
            return False

    if "id" in rule:
        expected_ids = normalized_values(rule.get("id"))

        if expected_ids and not any(expected in ids for expected in expected_ids):
            return False

    return True


def scenario_matches(candidate: dict, template: dict) -> bool:
    rules = template.get("targetRules")

    if not isinstance(rules, list) or not rules:
        return False

    return any(rule_matches(candidate, rule) for rule in rules)


def candidate_score(candidate: dict) -> float:
    value = candidate.get("score")

    if isinstance(value, (int, float)):
        return float(value)

    scoring = scoring_for(candidate)
    value = scoring.get("score")
    return float(value) if isinstance(value, (int, float)) else 0.0


def score_sort_key(candidate: dict) -> tuple:
    rank = candidate.get("rank")

    if not isinstance(rank, int):
        rank = 999999

    distance = candidate.get("targetDistanceChebyshev")

    if not isinstance(distance, int):
        distance = 999999

    return (-candidate_score(candidate), distance, rank, candidate.get("tickId") if candidate.get("tickId") is not None else -1)


def tick_id_for(record: dict) -> int | None:
    value = record.get("tickId")
    return value if isinstance(value, int) else None


def tick_filter(candidates: list[dict], args) -> set[int] | None:
    ticks = sorted({tick for tick in (tick_id_for(candidate) for candidate in candidates) if tick is not None})

    if args.tick is not None:
        return {args.tick}

    if args.tick_range is not None:
        start, end = args.tick_range
        return {tick for tick in ticks if start <= tick <= end}

    if args.latest is not None:
        return set(ticks[-args.latest :])

    return None


def selected_candidates(all_candidates: list[dict], template: dict, args, min_score: float) -> tuple[list[dict], set[int]]:
    scenario_candidates = [
        candidate
        for candidate in all_candidates
        if scenario_matches(candidate, template) and candidate_score(candidate) >= min_score
    ]
    selected_ticks = tick_filter(scenario_candidates, args)

    if selected_ticks is None:
        selected_ticks = {
            tick
            for tick in (tick_id_for(candidate) for candidate in scenario_candidates)
            if tick is not None
        }

    candidates = [
        candidate
        for candidate in scenario_candidates
        if tick_id_for(candidate) in selected_ticks
    ]
    candidates.sort(key=score_sort_key)
    return candidates, selected_ticks


def group_by_tick(records: list[dict]) -> dict[int, list[dict]]:
    grouped = defaultdict(list)

    for record in records:
        tick_id = tick_id_for(record)

        if tick_id is not None:
            grouped[tick_id].append(record)

    return grouped


def compact_target(target: dict) -> dict:
    return {
        "targetId": target.get("targetId"),
        "targetType": target.get("targetType"),
        "name": target.get("name"),
        "id": target.get("id"),
        "rawId": target.get("rawId"),
        "targetRole": target.get("targetRole"),
        "targetCategory": target.get("targetCategory"),
        "targetTags": target.get("targetTags") if isinstance(target.get("targetTags"), list) else [],
    }


def compact_candidate(candidate: dict, rank_within_scenario: int) -> dict:
    target = target_for(candidate)
    candidate_geometry = geometry_for(candidate)
    candidate_scoring = scoring_for(candidate)
    return {
        "rankWithinScenario": rank_within_scenario,
        "originalRank": candidate.get("rank"),
        "score": candidate.get("score"),
        "target": compact_target(target),
        "aimPoint": candidate_geometry.get("aimPoint"),
        "preferredAimGeometryType": candidate_geometry.get("preferredAimGeometryType"),
        "preferredAimGeometry": candidate_geometry.get("preferredAimGeometry"),
        "targetDistanceTiles": candidate.get("targetDistanceTiles"),
        "targetDistanceChebyshev": candidate.get("targetDistanceChebyshev"),
        "targetDistanceManhattan": candidate.get("targetDistanceManhattan"),
        "targetDistanceEuclidean": candidate.get("targetDistanceEuclidean"),
        "playerWorld": candidate.get("playerWorld"),
        "targetWorld": candidate.get("targetWorld"),
        "scoreParts": candidate_scoring.get("scoreParts") if isinstance(candidate_scoring.get("scoreParts"), list) else [],
        "reasons": candidate_scoring.get("reasons") if isinstance(candidate_scoring.get("reasons"), list) else [],
        "penalties": candidate_scoring.get("penalties") if isinstance(candidate_scoring.get("penalties"), list) else [],
    }


def compact_context_target(record: dict) -> dict:
    target = target_for(record)
    geometry = geometry_for(record)
    return {
        "targetType": target.get("targetType"),
        "name": display_name(record),
        "id": target.get("id"),
        "rawId": target.get("rawId"),
        "targetRole": target.get("targetRole"),
        "targetCategory": target.get("targetCategory"),
        "targetTags": target.get("targetTags") if isinstance(target.get("targetTags"), list) else [],
        "onScreen": geometry.get("onScreen"),
        "geometryAvailable": geometry.get("geometryAvailable"),
        "canvasPoint": geometry.get("canvasPoint"),
        "canvasLocation": geometry.get("canvasLocation"),
        "canvasCenter": geometry.get("canvasCenter"),
        "clickboxBounds": geometry.get("clickboxBounds"),
        "convexHullBounds": geometry.get("convexHullBounds"),
        "tilePolygon": geometry.get("tilePolygon") or geometry.get("canvasTilePolygon"),
    }


def context_records_for_tick(
    world_by_tick: dict[int, list[dict]],
    tick_id: int,
    context_roles: set[str],
    limit: int,
) -> tuple[list[dict], dict[str, dict]]:
    matching = []
    role_counts = Counter()
    category_counts = Counter()

    for record in world_by_tick.get(tick_id, []):
        target = target_for(record)
        role = str(target.get("targetRole") or "unknown")
        category = str(target.get("targetCategory") or "unknown")

        if role.lower() not in context_roles:
            continue

        role_counts[role] += 1
        category_counts[category] += 1
        matching.append(record)

    matching.sort(key=lambda record: (str(target_for(record).get("targetRole") or ""), display_name(record), str(target_for(record).get("targetId") or "")))
    compact = [compact_context_target(record) for record in matching[:limit]]
    return compact, {
        "countsByRole": dict(role_counts.most_common()),
        "countsByCategory": dict(category_counts.most_common()),
    }


def scenario_record(
    scenario_type: str,
    session: Path,
    tick_id: int,
    candidates: list[dict],
    context_targets: list[dict],
    context_counts: dict,
) -> dict:
    first = candidates[0]
    frame = frame_for(first)
    return {
        "schemaVersion": SCHEMA_VERSION_RECORD,
        "scenarioType": scenario_type,
        "sessionId": first.get("sessionId") or session.name,
        "tickId": tick_id,
        "timestampUtc": first.get("timestampUtc"),
        "frame": {
            "path": frame.get("path"),
            "exists": frame.get("exists"),
            "width": frame.get("width"),
            "height": frame.get("height"),
        },
        "selectedCandidates": [
            compact_candidate(candidate, rank)
            for rank, candidate in enumerate(candidates, start=1)
        ],
        "context": {
            "targets": context_targets,
            "countsByRole": context_counts.get("countsByRole", {}),
            "countsByCategory": context_counts.get("countsByCategory", {}),
        },
        "warnings": [],
        "safety": {
            "readOnly": True,
            "actionGenerated": False,
            "inputGenerated": False,
        },
    }


def index_for(
    session: Path,
    scenario_type: str,
    template_path: Path,
    selected_ticks: set[int],
    records: list[dict],
    min_score: float,
    limit_per_tick: int,
    warnings: list[str],
) -> dict:
    name_counts = Counter()
    category_counts = Counter()
    geometry_counts = Counter()
    context_count = 0
    selected_candidate_count = 0

    for record in records:
        context_targets = record.get("context", {}).get("targets", [])
        context_count += len(context_targets) if isinstance(context_targets, list) else 0
        selected = record.get("selectedCandidates", [])
        selected_candidate_count += len(selected) if isinstance(selected, list) else 0

        for candidate in selected:
            target = candidate.get("target") if isinstance(candidate.get("target"), dict) else {}
            name_counts[target.get("name") or "unknown"] += 1
            category_counts[target.get("targetCategory") or "unknown"] += 1
            geometry_counts[candidate.get("preferredAimGeometryType") or "none"] += 1

    return {
        "schemaVersion": SCHEMA_VERSION_INDEX,
        "generatedAtUtc": utc_now(),
        "sessionPath": str(session),
        "scenarioType": scenario_type,
        "templatePath": str(template_path),
        "selectedTickCount": len(selected_ticks),
        "scenarioRecordCount": len(records),
        "selectedCandidateCount": selected_candidate_count,
        "contextTargetCount": context_count,
        "countsByTargetName": dict(name_counts.most_common(25)),
        "countsByTargetCategory": dict(category_counts.most_common()),
        "countsByPreferredAimGeometryType": dict(geometry_counts.most_common()),
        "minScoreUsed": min_score,
        "limitPerTickUsed": limit_per_tick,
        "paths": {
            "scenarioDataset": f"scenario_datasets/{scenario_type}.jsonl",
            "scenarioIndex": "scenario_datasets/scenario_index.json",
        },
        "warnings": warnings[:100],
    }


def atomic_write_outputs(paths: dict[str, Path], records: list[dict], index: dict) -> None:
    output_dir = paths["outputDir"]
    temp_dir = output_dir / f".tmp-scenario-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}-{os.getpid()}"

    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    temp_dir.mkdir(parents=True)

    try:
        with (temp_dir / paths["scenario"].name).open("w", encoding="utf-8") as file:
            for record in records:
                file.write(json_dump_compact(record))
                file.write("\n")

        with (temp_dir / "scenario_index.json").open("w", encoding="utf-8") as file:
            json.dump(index, file, indent=2)
            file.write("\n")

        output_dir.mkdir(parents=True, exist_ok=True)
        os.replace(temp_dir / paths["scenario"].name, paths["scenario"])
        os.replace(temp_dir / "scenario_index.json", paths["index"])
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def build_scenario_dataset(session: Path, args) -> tuple[list[dict], dict]:
    template, template_path, warnings = load_template(args)

    if not template:
        raise RuntimeError("; ".join(warnings) if warnings else "could not load scenario template")

    scenario_type = str(template.get("scenarioType") or args.scenario)
    min_score = float(args.min_score if args.min_score is not None else template.get("minScore", 0))
    limit_per_tick = int(args.limit_per_tick if args.limit_per_tick is not None else template.get("limitPerTick", 5))
    include_context = bool(args.include_context or template.get("includeContext"))
    context_limit = int(template.get("contextLimitPerTick", 20))
    context_roles = {str(role).lower() for role in template.get("contextRoles", []) if str(role).strip()}

    geometry_dir = session / "interaction_geometry"
    candidate_path = geometry_dir / "target_candidates.jsonl"
    world_targets_path = geometry_dir / "world_targets.jsonl"
    candidates, candidate_warnings = read_jsonl(candidate_path)
    warnings.extend(candidate_warnings)

    if not candidates:
        raise RuntimeError(
            "No target candidates found. Run python telemetry-viewer\\select_target_candidates.py first."
        )

    selected, selected_ticks = selected_candidates(candidates, template, args, min_score)
    selected_by_tick = group_by_tick(selected)
    world_by_tick = defaultdict(list)

    if include_context:
        world_records, world_warnings = read_jsonl(world_targets_path)
        warnings.extend(world_warnings)
        world_by_tick = group_by_tick(world_records)

    records = []

    for tick_id in sorted(selected_by_tick):
        tick_candidates = sorted(selected_by_tick[tick_id], key=score_sort_key)[:limit_per_tick]
        context_targets = []
        context_counts = {"countsByRole": {}, "countsByCategory": {}}

        if include_context:
            context_targets, context_counts = context_records_for_tick(
                world_by_tick,
                tick_id,
                context_roles,
                context_limit,
            )

        records.append(
            scenario_record(
                scenario_type,
                session,
                tick_id,
                tick_candidates,
                context_targets,
                context_counts,
            )
        )

    paths = output_paths(session, scenario_type)
    index = index_for(
        session,
        scenario_type,
        template_path,
        selected_ticks,
        records,
        min_score,
        limit_per_tick,
        warnings,
    )
    atomic_write_outputs(paths, records, index)
    return records, index


def print_summary(index: dict) -> None:
    print(f"session: {index['sessionPath']}")
    print(f"scenario: {index['scenarioType']}")
    print(f"selected ticks: {index['selectedTickCount']}")
    print(f"scenario records: {index['scenarioRecordCount']}")
    print(f"selected candidates: {index['selectedCandidateCount']}")
    print(f"context targets: {index['contextTargetCount']}")
    print(f"min score: {index['minScoreUsed']}")
    print(f"limit per tick: {index['limitPerTickUsed']}")

    if index["countsByTargetName"]:
        print("target names:")

        for name, count in index["countsByTargetName"].items():
            print(f"  {name}: {count}")
    else:
        print("target names: none")

    if index["warnings"]:
        print("warnings:")

        for warning in index["warnings"][:20]:
            print(f"  - {warning}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Build a read-only scenario dataset from existing target candidates. "
            "Scenario outputs contain geometry/context only and never generate actions."
        )
    )
    parser.add_argument("--session", help="Telemetry session directory to use.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions directory when --session is omitted.")
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO, help="Scenario template name. Default: bank_area.")
    parser.add_argument("--template", help="Explicit scenario template JSON path.")
    parser.add_argument("--tick", type=int, help="Only build the scenario dataset for one tick.")
    parser.add_argument("--latest", type=int, metavar="N", help="Only use the latest N matching candidate ticks.")
    parser.add_argument("--range", nargs=2, type=int, dest="tick_range", metavar=("START", "END"), help="Inclusive tick range.")
    parser.add_argument("--min-score", type=float, help="Override template minScore.")
    parser.add_argument("--limit-per-tick", type=int, help="Override template limitPerTick.")
    parser.add_argument("--include-context", action="store_true", help="Include world context targets for template contextRoles.")
    parser.add_argument("--json", action="store_true", help="Print scenario records as JSON lines after writing outputs.")
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

    if args.limit_per_tick is not None and args.limit_per_tick < 1:
        parser.error("--limit-per-tick must be positive")

    return args


def main() -> int:
    args = parse_args()
    session = resolve_session(args)

    if session is None:
        print(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")
        return 1

    try:
        records, index = build_scenario_dataset(session.expanduser().resolve(), args)
    except RuntimeError as error:
        print(f"session: {session}")
        print(str(error))
        return 1

    if args.json:
        for record in records:
            print(json_dump_compact(record))
    else:
        print_summary(index)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

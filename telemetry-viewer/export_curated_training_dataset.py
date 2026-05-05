import argparse
import json
import os
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from tab_profile_names import canonical_tab_profile_key
from telemetry_paths import find_newest_session, get_sessions_dir


SCHEMA_VERSION_INDEX = "curated_training.index.v1"
SCHEMA_VERSION_MANIFEST = "curated_training.example.v1"
VETO_LABELS = {"bad_crop", "wrong_label", "unsure"}
REVIEW_LABELS = {"good", "bad_crop", "wrong_label", "unsure"}
DEFAULT_SPLIT_RATIOS = (80, 10, 10)
MISSING_TRAINING_MESSAGE = (
    "Training data not found. Run python telemetry-viewer\\build_training_dataset.py first."
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dump_compact(data) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def parse_positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected integer: {value}") from error

    if parsed < 1:
        raise argparse.ArgumentTypeError(f"expected positive integer: {value}")

    return parsed


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def training_paths(session: Path) -> dict[str, Path]:
    training_dir = session / "training_data"
    curated_dir = training_dir / "curated"
    return {
        "trainingDir": training_dir,
        "manifest": training_dir / "training_manifest.jsonl",
        "reviews": training_dir / "review_labels.jsonl",
        "crops": training_dir / "crops",
        "curatedDir": curated_dir,
        "curatedManifest": curated_dir / "curated_manifest.jsonl",
        "curatedIndex": curated_dir / "curated_index.json",
    }


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
                    value = json.loads(text)
                except json.JSONDecodeError as error:
                    warnings.append(f"{path.name}:{line_number}: invalid JSON: {error.msg}")
                    continue

                if isinstance(value, dict):
                    records.append(value)
                else:
                    warnings.append(f"{path.name}:{line_number}: expected JSON object")
    except OSError as error:
        warnings.append(f"could not read {path}: {error}")

    return records, warnings


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
        file.write("\n")

    os.replace(temp_path, path)


def atomic_write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json_dump_compact(record))
            file.write("\n")

    os.replace(temp_path, path)


def normalize_crop_path(value) -> str:
    if value is None:
        return ""

    text = str(value).strip().replace("\\", "/")

    try:
        path = Path(text)

        if path.is_absolute():
            text = str(path.resolve()).replace("\\", "/")
    except OSError:
        pass

    return text.lower()


def labels_for_row(row: dict) -> dict:
    value = row.get("labels")
    return value if isinstance(value, dict) else {}


def active_tab_for_row(row: dict) -> str:
    return canonical_tab_profile_key(labels_for_row(row).get("activeTab") or "unknown")


def region_profile_for_row(row: dict) -> str:
    return canonical_tab_profile_key(row.get("regionProfile") or "base")


def region_name_for_row(row: dict) -> str:
    return str(row.get("regionName") or "unknown")


def tags_for_row(row: dict) -> list[str]:
    tags = row.get("tags")
    return [str(tag) for tag in tags] if isinstance(tags, list) else []


def review_exact_key(record: dict) -> tuple:
    return (
        record.get("tickId"),
        canonical_tab_profile_key(record.get("regionProfile") or "base"),
        str(record.get("regionName") or ""),
        normalize_crop_path(record.get("cropPath")),
    )


def review_fallback_key(record: dict) -> tuple:
    return (
        record.get("tickId"),
        canonical_tab_profile_key(record.get("regionProfile") or "base"),
        str(record.get("regionName") or ""),
    )


def latest_review_maps(records: list[dict]) -> tuple[dict, dict, Counter]:
    exact = {}
    fallback = {}
    counts = Counter()

    for record in records:
        label = str(record.get("reviewLabel") or "").strip().lower()

        if label:
            counts[label] += 1

        exact[review_exact_key(record)] = record
        fallback[review_fallback_key(record)] = record

    return exact, fallback, counts


def latest_review_for_row(row: dict, exact_reviews: dict, fallback_reviews: dict) -> dict | None:
    return exact_reviews.get(review_exact_key(row)) or fallback_reviews.get(review_fallback_key(row))


def resolve_crop_path(session: Path, row: dict) -> Path | None:
    crop_path = row.get("cropPath")

    if not crop_path:
        return None

    path = Path(str(crop_path))

    if not path.is_absolute():
        path = session / path

    try:
        resolved = path.resolve()
        resolved.relative_to((session / "training_data" / "crops").resolve())
    except (OSError, ValueError):
        return None

    return resolved


def crop_file_exists(session: Path, row: dict) -> bool:
    path = resolve_crop_path(session, row)
    return bool(path and path.exists() and path.is_file())


def row_matches_filters(row: dict, args) -> bool:
    if args.active_tab and active_tab_for_row(row) != canonical_tab_profile_key(args.active_tab):
        return False

    if args.region_profile and region_profile_for_row(row) != canonical_tab_profile_key(args.region_profile):
        return False

    if args.region_name and region_name_for_row(row).lower() != args.region_name.lower():
        return False

    if args.tag:
        expected = args.tag.lower()
        if not any(tag.lower() == expected for tag in tags_for_row(row)):
            return False

    return True


def selection_reason(session: Path, row: dict, review: dict | None, args) -> tuple[bool, str]:
    if not row_matches_filters(row, args):
        return False, "excluded_filter"

    manifest_crop_exists = row.get("cropExists") is True
    actual_crop_exists = crop_file_exists(session, row)

    if not args.include_missing_crops and (not manifest_crop_exists or not actual_crop_exists):
        return False, "excluded_missing_crop"

    review_label = str(review.get("reviewLabel") or "").strip().lower() if review else ""

    if args.reviewed_only:
        if review_label == "good":
            return True, "included_review_good"

        return False, f"excluded_review_{review_label or 'unreviewed'}"

    if review_label == "good":
        return True, "included_review_good"

    if review_label in {"bad_crop", "wrong_label"}:
        return False, f"excluded_review_{review_label}"

    if review_label == "unsure":
        if args.include_unsure:
            return True, "included_review_unsure"

        return False, "excluded_review_unsure"

    return True, "included_unreviewed_crop_exists"


def parse_split_names(value: str | None) -> list[str] | None:
    if not value:
        return None

    names = [part.strip() for part in value.split(",") if part.strip()]

    if len(names) < 2:
        raise argparse.ArgumentTypeError("--split must contain at least two comma-separated names")

    return names


def parse_split_ratios(value: str | None, expected_count: int) -> list[int]:
    if not value:
        ratios = list(DEFAULT_SPLIT_RATIOS)
    else:
        try:
            ratios = [int(part.strip()) for part in value.split(",") if part.strip()]
        except ValueError as error:
            raise argparse.ArgumentTypeError("--split-ratios must be comma-separated integers") from error

    if len(ratios) != expected_count:
        raise argparse.ArgumentTypeError("--split-ratios count must match --split names")

    if any(ratio < 0 for ratio in ratios) or sum(ratios) <= 0:
        raise argparse.ArgumentTypeError("--split-ratios must be non-negative and sum above zero")

    return ratios


def assign_splits(records: list[dict], names: list[str], ratios: list[int], seed: int | None) -> Counter:
    rng = random.Random(seed if seed is not None else 0)
    indexes = list(range(len(records)))
    rng.shuffle(indexes)
    total_ratio = sum(ratios)
    counts = Counter()
    boundaries = []
    running = 0

    for ratio in ratios:
        running += ratio
        boundaries.append(running / total_ratio)

    for position, record_index in enumerate(indexes):
        fraction = (position + 1) / max(1, len(indexes))

        for split_name, boundary in zip(names, boundaries):
            if fraction <= boundary:
                records[record_index]["split"] = split_name
                counts[split_name] += 1
                break

    return counts


def count_selected(records: list[dict]) -> dict:
    active_tabs = Counter()
    region_profiles = Counter()
    region_names = Counter()
    tags = Counter()

    for record in records:
        active_tabs[active_tab_for_row(record)] += 1
        region_profiles[region_profile_for_row(record)] += 1
        region_names[region_name_for_row(record)] += 1

        for tag in tags_for_row(record):
            tags[str(tag)] += 1

    return {
        "countsByActiveTab": dict(active_tabs.most_common()),
        "countsByRegionProfile": dict(region_profiles.most_common()),
        "countsByRegionName": dict(region_names.most_common()),
        "countsByTag": dict(tags.most_common()),
    }


def export_curated(session: Path, args) -> dict:
    session = session.expanduser().resolve()
    paths = training_paths(session)
    warnings = []

    if not paths["manifest"].exists():
        raise FileNotFoundError(MISSING_TRAINING_MESSAGE)

    manifest, manifest_warnings = read_jsonl(paths["manifest"])
    reviews, review_warnings = read_jsonl(paths["reviews"])
    warnings.extend(manifest_warnings)
    warnings.extend(review_warnings)
    exact_reviews, fallback_reviews, raw_review_counts = latest_review_maps(reviews)
    latest_review_counts = Counter()
    crop_exists_true = 0
    crop_exists_false = 0
    unreviewed_count = 0
    selected = []
    excluded_reasons = Counter()

    for row in manifest:
        if row.get("cropExists") is True:
            crop_exists_true += 1
        else:
            crop_exists_false += 1

        review = latest_review_for_row(row, exact_reviews, fallback_reviews)
        review_label = str(review.get("reviewLabel") or "").strip().lower() if review else ""

        if not review_label:
            unreviewed_count += 1
        else:
            latest_review_counts[review_label] += 1

        include, reason = selection_reason(session, row, review, args)

        if not include:
            excluded_reasons[reason] += 1
            continue

        output = dict(row)
        output["schemaVersion"] = output.get("schemaVersion") or SCHEMA_VERSION_MANIFEST
        output["latestReviewLabel"] = review_label or None
        output["latestReviewTimestampUtc"] = review.get("timestampUtc") if review else None
        output["curatedReason"] = reason
        selected.append(output)

    if args.seed is not None:
        rng = random.Random(args.seed)
        rng.shuffle(selected)

    if args.max_examples is not None and len(selected) > args.max_examples:
        excluded_reasons["excluded_max_examples"] += len(selected) - args.max_examples
        selected = selected[: args.max_examples]

    split_counts = Counter()
    split_names = parse_split_names(args.split)

    if split_names:
        ratios = parse_split_ratios(args.split_ratios, len(split_names))
        split_counts = assign_splits(selected, split_names, ratios, args.seed)

    counts = count_selected(selected)
    atomic_write_jsonl(paths["curatedManifest"], selected)
    index = {
        "schemaVersion": SCHEMA_VERSION_INDEX,
        "generatedAtUtc": utc_now(),
        "sessionPath": str(session),
        "sourceManifestPath": str(paths["manifest"]),
        "sourceReviewPath": str(paths["reviews"]) if paths["reviews"].exists() else None,
        "totalManifestExamples": len(manifest),
        "cropExistsTrueCount": crop_exists_true,
        "cropExistsFalseCount": crop_exists_false,
        "reviewedGoodCount": int(latest_review_counts.get("good", 0)),
        "reviewedBadCropCount": int(latest_review_counts.get("bad_crop", 0)),
        "reviewedWrongLabelCount": int(latest_review_counts.get("wrong_label", 0)),
        "reviewedUnsureCount": int(latest_review_counts.get("unsure", 0)),
        "reviewCounts": dict(latest_review_counts.most_common()),
        "rawReviewRecordCounts": dict(raw_review_counts.most_common()),
        "unreviewedCount": unreviewed_count,
        "selectedCuratedCount": len(selected),
        "excludedCount": sum(excluded_reasons.values()),
        "excludedByReason": dict(excluded_reasons.most_common()),
        **counts,
        "splitCounts": dict(split_counts.most_common()) if split_names else {},
        "filters": {
            "reviewedOnly": bool(args.reviewed_only),
            "includeUnsure": bool(args.include_unsure),
            "includeMissingCrops": bool(args.include_missing_crops),
            "activeTab": args.active_tab,
            "regionProfile": args.region_profile,
            "regionName": args.region_name,
            "tag": args.tag,
            "maxExamples": args.max_examples,
            "seed": args.seed,
            "split": split_names,
            "splitRatios": list(ratios) if split_names else None,
        },
        "paths": {
            "curatedManifest": "training_data/curated/curated_manifest.jsonl",
            "curatedIndex": "training_data/curated/curated_index.json",
        },
        "warnings": warnings[:100],
        "warningCount": len(warnings),
    }
    atomic_write_json(paths["curatedIndex"], index)
    return index


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export a clean curated training manifest from existing generated training data and QA labels."
    )
    parser.add_argument("--session", help="Telemetry session directory to process.")
    parser.add_argument("--sessions-dir", help="Override the telemetry sessions directory when --session is omitted.")
    parser.add_argument("--reviewed-only", action="store_true", help="Include only examples whose latest review label is good.")
    parser.add_argument("--include-unsure", action="store_true", help="Include examples whose latest review label is unsure.")
    parser.add_argument("--include-missing-crops", action="store_true", help="Include missing-crop examples for diagnostics.")
    parser.add_argument("--active-tab", help="Only include this activeTab.")
    parser.add_argument("--region-profile", help="Only include this regionProfile.")
    parser.add_argument("--region-name", help="Only include this regionName.")
    parser.add_argument("--tag", help="Only include examples with this tag.")
    parser.add_argument("--max-examples", type=parse_positive_int, metavar="N", help="Limit selected curated rows.")
    parser.add_argument("--seed", type=int, help="Seed for deterministic shuffling and split assignment.")
    parser.add_argument("--split", help="Comma-separated split names, for example train,val,test.")
    parser.add_argument("--split-ratios", help="Comma-separated split ratios matching --split. Default: 80,10,10.")
    args = parser.parse_args()

    if args.split_ratios and not args.split:
        parser.error("--split-ratios requires --split")

    return args


def main() -> int:
    args = parse_args()
    session = resolve_session(args)

    if session is None:
        print(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")
        return 1

    try:
        index = export_curated(session, args)
    except FileNotFoundError as error:
        print(f"session: {session}")
        print(error)
        return 1
    except (OSError, argparse.ArgumentTypeError) as error:
        print(f"Unable to export curated training dataset: {error}")
        return 1

    print(f"Exported curated training manifest: {index['sessionPath']}")
    print("  training_data/curated/curated_manifest.jsonl")
    print("  training_data/curated/curated_index.json")
    print(f"  totalManifestExamples: {index['totalManifestExamples']}")
    print(f"  selectedCuratedCount: {index['selectedCuratedCount']}")
    print(f"  excludedCount: {index['excludedCount']}")
    print(f"  excludedByReason: {index['excludedByReason']}")
    print(f"  reviewCounts: {index['reviewCounts']}")

    if index["splitCounts"]:
        print(f"  splitCounts: {index['splitCounts']}")

    print(f"  warningCount: {index['warningCount']}")

    if index["warnings"]:
        print("  warnings:")

        for warning in index["warnings"][:10]:
            print(f"    - {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

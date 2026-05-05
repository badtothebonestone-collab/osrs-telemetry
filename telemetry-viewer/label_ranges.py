import argparse
import json
from collections import Counter
from pathlib import Path


DEFAULT_LABELS_PATH = Path(__file__).resolve().with_name("tab_labels.json")


def int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_label_name(value) -> str:
    return str(value or "unknown").strip().lower().replace(" ", "_").replace("-", "_") or "unknown"


def load_label_ranges(path: str | Path | None = None) -> dict:
    labels_path = Path(path).expanduser() if path else DEFAULT_LABELS_PATH
    explicit_path = path is not None

    if not labels_path.exists():
        warning = f"label file not found: {labels_path}" if explicit_path else None
        return {
            "path": str(labels_path),
            "labels": [],
            "warnings": [warning] if warning else [],
            "loaded": False,
        }

    try:
        with labels_path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        return {
            "path": str(labels_path),
            "labels": [],
            "warnings": [f"unable to load label ranges from {labels_path}: {error}"],
            "loaded": False,
        }

    raw_labels = raw.get("labels") if isinstance(raw, dict) else raw

    if not isinstance(raw_labels, list):
        return {
            "path": str(labels_path),
            "labels": [],
            "warnings": [f"label ranges must be a JSON object with labels[] or a JSON array: {labels_path}"],
            "loaded": False,
        }

    labels = []
    warnings = []

    for index, raw_label in enumerate(raw_labels):
        if not isinstance(raw_label, dict):
            warnings.append(f"label #{index + 1} is not an object; skipped")
            continue

        start_tick = int_or_none(raw_label.get("startTick", raw_label.get("start")))
        end_tick = int_or_none(raw_label.get("endTick", raw_label.get("end")))
        active_tab = normalize_label_name(raw_label.get("activeTab", raw_label.get("tab")))

        if start_tick is None or end_tick is None:
            warnings.append(f"label #{index + 1} missing integer startTick/endTick; skipped")
            continue

        if end_tick < start_tick:
            start_tick, end_tick = end_tick, start_tick

        if active_tab == "unknown":
            warnings.append(f"label #{index + 1} missing activeTab; skipped")
            continue

        labels.append(
            {
                "startTick": start_tick,
                "endTick": end_tick,
                "activeTab": active_tab,
                "uiState": raw_label.get("uiState"),
                "activityState": raw_label.get("activityState"),
                "notes": raw_label.get("notes"),
                "labelSource": str(labels_path),
                "order": index,
            }
        )

    warnings.extend(overlap_warnings(labels))

    return {
        "path": str(labels_path),
        "labels": labels,
        "warnings": warnings,
        "loaded": True,
    }


def overlap_warnings(labels: list[dict]) -> list[str]:
    warnings = []
    ordered = sorted(labels, key=lambda label: (label["startTick"], label["endTick"], label["order"]))

    for left_index, left in enumerate(ordered):
        for right in ordered[left_index + 1:]:
            if right["startTick"] > left["endTick"]:
                break

            if left["startTick"] <= right["endTick"] and right["startTick"] <= left["endTick"]:
                warnings.append(
                    "overlapping label ranges: "
                    f"{left['startTick']}-{left['endTick']} {left['activeTab']} and "
                    f"{right['startTick']}-{right['endTick']} {right['activeTab']}; "
                    "most specific range wins, file order breaks ties"
                )

    return warnings


def matching_labels(tick_id: int, labels_doc: dict) -> list[dict]:
    labels = labels_doc.get("labels") if isinstance(labels_doc, dict) else []

    if not isinstance(labels, list):
        return []

    return [
        label
        for label in labels
        if isinstance(label, dict)
        and isinstance(label.get("startTick"), int)
        and isinstance(label.get("endTick"), int)
        and label["startTick"] <= tick_id <= label["endTick"]
    ]


def label_for_tick(tick_id, labels_doc: dict) -> dict | None:
    tick_id = int_or_none(tick_id)

    if tick_id is None:
        return None

    matches = matching_labels(tick_id, labels_doc)

    if not matches:
        return None

    return sorted(
        matches,
        key=lambda label: (
            label["endTick"] - label["startTick"],
            label.get("order", 0),
        ),
    )[0]


def infer_label_for_tick(tick_id, labels_doc: dict) -> dict | None:
    tick_id = int_or_none(tick_id)

    if tick_id is None:
        return None

    matches = matching_labels(tick_id, labels_doc)

    if not matches:
        return None

    label = sorted(
        matches,
        key=lambda candidate: (
            candidate["endTick"] - candidate["startTick"],
            candidate.get("order", 0),
        ),
    )[0]

    if label is None:
        return None

    evidence = [
        {
            "source": "label",
            "detail": f"manual label range {label['startTick']}-{label['endTick']}",
        }
    ]

    if len(matches) > 1:
        evidence.append(
            {
                "source": "label",
                "detail": "overlapping label ranges matched this tick; most specific range selected",
                "candidates": [
                    f"{match['startTick']}-{match['endTick']} {match['activeTab']}"
                    for match in matches
                ],
            }
        )

    result = {
        "activeTab": label["activeTab"],
        "source": "label",
        "confidence": 1.0,
        "evidence": evidence,
        "uiState": label.get("uiState"),
        "activityState": label.get("activityState"),
        "labelSource": label.get("labelSource"),
    }

    if label.get("notes"):
        result["labelNotes"] = label.get("notes")

    return result


def counts_by_active_tab(labels_doc: dict) -> dict:
    counts = Counter()

    for label in labels_doc.get("labels", []):
        if not isinstance(label, dict):
            continue

        start_tick = label.get("startTick")
        end_tick = label.get("endTick")
        active_tab = label.get("activeTab") or "unknown"

        if isinstance(start_tick, int) and isinstance(end_tick, int):
            counts[active_tab] += max(0, end_tick - start_tick + 1)

    return dict(counts.most_common())


def parse_args():
    parser = argparse.ArgumentParser(description="Inspect manual active-tab label ranges.")
    parser.add_argument("--labels", help="Path to tab label ranges JSON. Defaults to telemetry-viewer\\tab_labels.json.")
    parser.add_argument("--summary", action="store_true", help="Print counts by activeTab.")
    parser.add_argument("--validate", action="store_true", help="Print validation warnings, including overlaps.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    labels_doc = load_label_ranges(args.labels)

    print(f"labels: {labels_doc['path']}")
    print(f"loaded: {labels_doc['loaded']}")
    print(f"rangeCount: {len(labels_doc['labels'])}")

    if args.summary or not args.validate:
        print("activeTabTickCounts:")

        for active_tab, count in counts_by_active_tab(labels_doc).items():
            print(f"  {active_tab}: {count}")

    if args.validate:
        warnings = labels_doc.get("warnings", [])

        if warnings:
            print("warnings:")

            for warning in warnings:
                print(f"  - {warning}")
        else:
            print("warnings: none")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

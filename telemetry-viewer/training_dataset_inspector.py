import argparse
import json
import mimetypes
import random
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from telemetry_paths import find_newest_session, get_sessions_dir, safe_read_json


HOST = "127.0.0.1"
DEFAULT_PORT = 8790
MISSING_TRAINING_MESSAGE = "Run python telemetry-viewer\\build_training_dataset.py first."
REVIEW_LABELS = {"good", "bad_crop", "wrong_label", "unsure"}
LOW_VALUE_BASE_REGIONS = {"fullFrame", "gameViewport", "sidePanel", "tabs"}
SORT_KEYS = {
    "tick_asc",
    "tick_desc",
    "activeTab",
    "regionProfile",
    "regionName",
    "cropExists",
    "reviewStatus",
}
QUEUE_MODES = {
    "random",
    "balanced_active_tab",
    "balanced_region_profile",
    "balanced_region_name",
    "missing_crops",
    "unknown_active_tab",
    "unreviewed_crop_exists",
    "grid_slots",
    "non_base",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dump_compact(data) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None

    normalized = value.strip().lower()

    if normalized in {"1", "true", "yes", "y"}:
        return True

    if normalized in {"0", "false", "no", "n"}:
        return False

    return None


def parse_int(value: str | None, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default

    return max(minimum, min(maximum, parsed))


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
                else:
                    warnings.append(f"{path.name}:{line_number}: expected JSON object")
    except OSError as error:
        warnings.append(f"could not read {path}: {error}")

    return records, warnings


def count_files(path: Path) -> int:
    if not path.exists():
        return 0

    try:
        return sum(1 for child in path.rglob("*") if child.is_file())
    except OSError:
        return 0


def labels_for_row(row: dict) -> dict:
    value = row.get("labels")
    return value if isinstance(value, dict) else {}


def source_for_row(row: dict) -> dict:
    value = row.get("source")
    return value if isinstance(value, dict) else {}


def telemetry_summary_for_row(row: dict) -> dict:
    value = row.get("telemetrySummary")
    return value if isinstance(value, dict) else {}


def active_tab_for_row(row: dict) -> str:
    value = labels_for_row(row).get("activeTab")
    return str(value) if value is not None else "unknown"


def label_source_for_row(row: dict) -> str:
    source = source_for_row(row)
    value = source.get("labelSource")

    if value is None:
        value = labels_for_row(row).get("activeTabSource")

    return str(value) if value is not None else ""


def tags_for_row(row: dict) -> list[str]:
    tags = row.get("tags")

    if not isinstance(tags, list):
        return []

    return [str(tag) for tag in tags if tag is not None]


def increment(counter: dict, key: str, amount: int = 1) -> None:
    counter[key] = counter.get(key, 0) + amount


def as_relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


class TrainingDataset:
    def __init__(self, session: Path | None):
        self.session = session
        self.training_dir = session / "training_data" if session else None
        self.crop_root = self.training_dir / "crops" if self.training_dir else None
        self.index_path = self.training_dir / "training_index.json" if self.training_dir else None
        self.manifest_path = self.training_dir / "training_manifest.jsonl" if self.training_dir else None
        self.labels_path = self.training_dir / "labels_applied.jsonl" if self.training_dir else None
        self.review_path = self.training_dir / "review_labels.jsonl" if self.training_dir else None
        self.index = {}
        self.manifest_rows = []
        self.labels_applied = []
        self.review_records = []
        self.latest_review_by_key = {}
        self.warnings = []
        self.missing_reason = None
        self.trusted_frame_paths = set()

    @property
    def available(self) -> bool:
        return self.missing_reason is None

    def load(self) -> None:
        if self.session is None:
            self.missing_reason = "No telemetry session found."
            return

        if self.training_dir is None or not self.training_dir.exists():
            self.missing_reason = MISSING_TRAINING_MESSAGE
            return

        if self.manifest_path is None or not self.manifest_path.exists():
            self.missing_reason = MISSING_TRAINING_MESSAGE
            return

        index = safe_read_json(self.index_path) if self.index_path else None
        self.index = index if isinstance(index, dict) else {}

        self.manifest_rows, manifest_warnings = read_jsonl(self.manifest_path)
        self.labels_applied, label_warnings = read_jsonl(self.labels_path) if self.labels_path else ([], [])
        self.review_records, review_warnings = read_jsonl(self.review_path) if self.review_path else ([], [])
        self.warnings.extend(manifest_warnings)
        self.warnings.extend(label_warnings)
        self.warnings.extend(review_warnings)
        self.latest_review_by_key = self._latest_review_lookup()
        self.trusted_frame_paths = self._collect_trusted_frame_paths()

    def _latest_review_lookup(self) -> dict:
        lookup = {}

        for record in self.review_records:
            lookup[self.review_key_for_record(record)] = record

        return lookup

    def _collect_trusted_frame_paths(self) -> set[Path]:
        trusted = set()

        if self.session is None:
            return trusted

        session_root = self.session.resolve()

        for row in self.manifest_rows:
            frame_path = row.get("framePath")

            if not frame_path:
                continue

            resolved = self.resolve_session_path(str(frame_path))

            if resolved is None:
                continue

            try:
                resolved.relative_to(session_root)
            except ValueError:
                continue

            trusted.add(resolved)

        return trusted

    def resolve_session_path(self, value: str) -> Path | None:
        if self.session is None:
            return None

        text = unquote(value).strip()

        if not text:
            return None

        path = Path(text)

        if not path.is_absolute():
            path = self.session / path

        try:
            return path.resolve()
        except OSError:
            return None

    def resolve_crop_path(self, value: str) -> Path | None:
        if self.crop_root is None:
            return None

        resolved = self.resolve_session_path(value)

        if resolved is None:
            return None

        try:
            resolved.relative_to(self.crop_root.resolve())
        except ValueError:
            return None

        return resolved

    def crop_path_for_row(self, row: dict) -> Path | None:
        crop_path = row.get("cropPath")

        if not crop_path:
            return None

        return self.resolve_crop_path(str(crop_path))

    def actual_crop_exists_for_row(self, row: dict) -> bool:
        resolved = self.crop_path_for_row(row)
        return bool(resolved and resolved.exists() and resolved.is_file())

    def crop_diagnostics_for_row(self, row: dict) -> dict:
        resolved = self.crop_path_for_row(row)
        actual_exists = bool(resolved and resolved.exists() and resolved.is_file())
        return {
            "manifestCropExists": row.get("cropExists") is True,
            "actualCropExists": actual_exists,
            "cropPath": row.get("cropPath"),
            "resolvedCropPath": str(resolved) if resolved else None,
            "pathAllowed": resolved is not None,
        }

    def resolve_frame_path(self, value: str) -> Path | None:
        resolved = self.resolve_session_path(value)

        if resolved is None:
            return None

        return resolved if resolved in self.trusted_frame_paths else None

    def summary(self) -> dict:
        active_tab_counts = dict(self.index.get("countsByActiveTab") or {})
        region_profile_counts = dict(self.index.get("countsByRegionProfile") or {})
        tag_counts = dict(self.index.get("countsByTag") or {})

        if not active_tab_counts or not region_profile_counts or not tag_counts:
            active_tab_counts = {}
            region_profile_counts = {}
            tag_counts = {}

            for row in self.manifest_rows:
                increment(active_tab_counts, active_tab_for_row(row))
                increment(region_profile_counts, str(row.get("regionProfile") or "unknown"))

                for tag in tags_for_row(row):
                    increment(tag_counts, tag)

        crop_exists_true = sum(1 for row in self.manifest_rows if row.get("cropExists") is True)
        crop_exists_false = len(self.manifest_rows) - crop_exists_true
        actual_crop_file_count = count_files(self.crop_root) if self.crop_root is not None else 0
        manifest_crop_true_missing = sum(
            1 for row in self.manifest_rows
            if row.get("cropExists") is True and not self.actual_crop_exists_for_row(row)
        )
        actual_crop_referenced_count = sum(1 for row in self.manifest_rows if self.actual_crop_exists_for_row(row))
        reviewed_keys = {
            self.review_key_for_record(record)
            for record in self.review_records
        }
        reviewed_examples = sum(
            1 for row in self.manifest_rows
            if self.review_key_for_row(row) in reviewed_keys
        )
        missing_crop_message = None

        if crop_exists_false or manifest_crop_true_missing:
            missing_crop_message = (
                "Many manifest rows do not have crop files. "
                "Rebuild training data or filter to cropExists=true."
            )

        return {
            "available": self.available,
            "message": None if self.available else self.missing_reason,
            "sessionPath": str(self.session) if self.session else None,
            "manifestExamples": int(self.index.get("exampleCount") or len(self.manifest_rows)),
            "totalExamples": int(self.index.get("exampleCount") or len(self.manifest_rows)),
            "loadedExamples": len(self.manifest_rows),
            "cropCount": actual_crop_file_count,
            "manifestCropExistsTrueExamples": crop_exists_true,
            "manifestCropExistsFalseExamples": crop_exists_false,
            "actualCropFilesFound": actual_crop_file_count,
            "actualCropFilesReferencedByManifest": actual_crop_referenced_count,
            "manifestCropTrueMissingFiles": manifest_crop_true_missing,
            "countsByActiveTab": active_tab_counts,
            "countsByRegionProfile": region_profile_counts,
            "countsByTag": tag_counts,
            "unknownActiveTabCount": int(
                self.index.get("unknownActiveTabCount")
                if self.index.get("unknownActiveTabCount") is not None
                else active_tab_counts.get("unknown", 0)
            ),
            "skippedMissingFrames": int(self.index.get("skippedMissingFrameCount") or 0),
            "paths": {
                "trainingDir": str(self.training_dir) if self.training_dir else None,
                "trainingIndex": str(self.index_path) if self.index_path else None,
                "trainingManifest": str(self.manifest_path) if self.manifest_path else None,
                "labelsApplied": str(self.labels_path) if self.labels_path else None,
                "reviewLabels": str(self.review_path) if self.review_path else None,
                "crops": str(self.crop_root) if self.crop_root else None,
            },
            "reviewCount": len(self.review_records),
            "reviewCounts": self.review_counts(),
            "reviewedExamples": reviewed_examples,
            "unreviewedExamples": max(0, len(self.manifest_rows) - reviewed_examples),
            "missingCropWarning": missing_crop_message,
            "warnings": self.warnings,
        }

    def compact_row(self, index: int, row: dict) -> dict:
        labels = labels_for_row(row)
        source = source_for_row(row)

        return {
            "index": index,
            "tickId": row.get("tickId"),
            "timestampUtc": row.get("timestampUtc"),
            "activeTab": labels.get("activeTab", "unknown"),
            "activeTabSource": labels.get("activeTabSource"),
            "labelSource": source.get("labelSource"),
            "regionProfile": row.get("regionProfile"),
            "regionName": row.get("regionName"),
            "regionType": row.get("regionType"),
            "tags": tags_for_row(row),
            "cropPath": row.get("cropPath"),
            "cropExists": row.get("cropExists"),
            "framePath": row.get("framePath"),
            "quality": labels.get("quality"),
            "actualCropExists": self.actual_crop_exists_for_row(row),
            "reviewStatus": self.review_status_for_row(row),
            "latestReview": self.latest_review_for_row(row),
        }

    def review_key_for_row(self, row: dict) -> tuple:
        return (row.get("tickId"), row.get("cropPath"), row.get("regionName"))

    def review_key_for_record(self, record: dict) -> tuple:
        return (record.get("tickId"), record.get("cropPath"), record.get("regionName"))

    def latest_review_for_row(self, row: dict) -> dict | None:
        key = self.review_key_for_row(row)

        return self.latest_review_by_key.get(key)

    def review_status_for_row(self, row: dict) -> str:
        review = self.latest_review_for_row(row)
        return str(review.get("reviewLabel")) if review else "unreviewed"

    def review_counts(self) -> dict:
        counts = {}

        for record in self.review_records:
            review_label = str(record.get("reviewLabel") or "unknown")
            increment(counts, review_label)

        return counts

    def append_review(self, index: int, review_label: str, notes: str = "") -> dict:
        if not self.available:
            raise ValueError(self.missing_reason or MISSING_TRAINING_MESSAGE)

        if index < 0 or index >= len(self.manifest_rows):
            raise ValueError("example index not found")

        normalized_label = review_label.strip().lower()

        if normalized_label not in REVIEW_LABELS:
            raise ValueError("reviewLabel must be one of: good, bad_crop, wrong_label, unsure")

        row = self.manifest_rows[index]
        labels = labels_for_row(row)
        record = {
            "schemaVersion": "training_dataset.review.v1",
            "timestampUtc": utc_now(),
            "exampleIndex": index,
            "tickId": row.get("tickId"),
            "cropPath": row.get("cropPath"),
            "regionName": row.get("regionName"),
            "regionProfile": row.get("regionProfile"),
            "currentLabel": labels.get("activeTab"),
            "reviewLabel": normalized_label,
        }
        clean_notes = notes.strip()

        if clean_notes:
            record["notes"] = clean_notes[:1000]

        if self.review_path is None:
            raise ValueError("review label path is unavailable")

        try:
            self.review_path.parent.mkdir(parents=True, exist_ok=True)

            with self.review_path.open("a", encoding="utf-8") as file:
                file.write(json_dump_compact(record))
                file.write("\n")
        except OSError as error:
            raise ValueError(f"could not append review label: {error}") from error

        self.review_records.append(record)
        self.latest_review_by_key[self.review_key_for_record(record)] = record
        return record

    def filter_options(self) -> dict:
        active_tabs = set()
        region_profiles = set()
        region_names = set()
        tags = set()
        label_sources = set()

        for row in self.manifest_rows:
            active_tabs.add(active_tab_for_row(row))

            if row.get("regionProfile") is not None:
                region_profiles.add(str(row.get("regionProfile")))

            if row.get("regionName") is not None:
                region_names.add(str(row.get("regionName")))

            for tag in tags_for_row(row):
                tags.add(tag)

            label_source = label_source_for_row(row)

            if label_source:
                label_sources.add(label_source)

        key = lambda value: value.lower()
        return {
            "activeTabs": sorted(active_tabs, key=key),
            "regionProfiles": sorted(region_profiles, key=key),
            "regionNames": sorted(region_names, key=key),
            "tags": sorted(tags, key=key),
            "labelSources": sorted(label_sources, key=key),
        }

    def matching_rows(self, query: dict) -> list[tuple[int, dict]]:
        matches = []
        scoped_query = dict(query)
        scoped_query["dataset"] = self

        for index, row in enumerate(self.manifest_rows):
            if not row_matches(row, scoped_query):
                continue

            matches.append((index, row))

        return matches


def row_matches(row: dict, query: dict) -> bool:
    dataset = query.get("dataset")
    labels = labels_for_row(row)
    active_tab = query.get("activeTab")
    region_profile = query.get("regionProfile")
    region_name = query.get("regionName")
    tag = query.get("tag")
    label_source = query.get("labelSource")
    crop_exists = query.get("cropExists")
    actual_crop_exists = query.get("actualCropExists")
    review_status = query.get("reviewStatus")
    hide_low_value_base = query.get("hideLowValueBase")
    non_base_only = query.get("nonBaseOnly")
    region_type = query.get("regionType")
    tick_id = query.get("tickId")
    text = query.get("q")

    if tick_id and str(row.get("tickId")) != str(tick_id):
        return False

    if active_tab and str(labels.get("activeTab", "unknown")).lower() != active_tab.lower():
        return False

    if region_profile and str(row.get("regionProfile", "")).lower() != region_profile.lower():
        return False

    if region_name and str(row.get("regionName", "")).lower() != region_name.lower():
        return False

    if tag and tag.lower() not in {value.lower() for value in tags_for_row(row)}:
        return False

    if label_source and label_source_for_row(row).lower() != label_source.lower():
        return False

    if crop_exists is not None and bool(row.get("cropExists")) is not crop_exists:
        return False

    if actual_crop_exists is not None and dataset is not None:
        if dataset.actual_crop_exists_for_row(row) is not actual_crop_exists:
            return False

    if review_status and dataset is not None:
        current_review_status = dataset.review_status_for_row(row)

        if review_status == "reviewed":
            if current_review_status == "unreviewed":
                return False
        elif current_review_status.lower() != review_status.lower():
            return False

    if hide_low_value_base:
        if (row.get("regionProfile") or "base") == "base" and row.get("regionName") in LOW_VALUE_BASE_REGIONS:
            return False

    if non_base_only and (row.get("regionProfile") or "base") == "base":
        return False

    if region_type and str(row.get("regionType", "")).lower() != region_type.lower():
        return False

    if text and text not in searchable_text(row):
        return False

    return True


def sorted_matches(matches: list[tuple[int, dict]], dataset: TrainingDataset, sort_key: str) -> list[tuple[int, dict]]:
    if sort_key not in SORT_KEYS:
        sort_key = "tick_asc"

    def value(item):
        _index, row = item

        if sort_key in {"tick_asc", "tick_desc"}:
            return row.get("tickId") if row.get("tickId") is not None else -1

        if sort_key == "activeTab":
            return (active_tab_for_row(row), row.get("tickId") or -1)

        if sort_key == "regionProfile":
            return (str(row.get("regionProfile") or ""), row.get("tickId") or -1)

        if sort_key == "regionName":
            return (str(row.get("regionName") or ""), row.get("tickId") or -1)

        if sort_key == "cropExists":
            return (not bool(row.get("cropExists")), not dataset.actual_crop_exists_for_row(row), row.get("tickId") or -1)

        if sort_key == "reviewStatus":
            return (dataset.review_status_for_row(row), row.get("tickId") or -1)

        return row.get("tickId") or -1

    return sorted(matches, key=value, reverse=(sort_key == "tick_desc"))


def queue_filtered_matches(
    matches: list[tuple[int, dict]],
    dataset: TrainingDataset,
    queue_mode: str | None,
) -> list[tuple[int, dict]]:
    if queue_mode == "missing_crops":
        return [
            item for item in matches
            if not dataset.actual_crop_exists_for_row(item[1])
        ]

    if queue_mode == "unknown_active_tab":
        return [
            item for item in matches
            if active_tab_for_row(item[1]).lower() == "unknown"
        ]

    if queue_mode == "unreviewed_crop_exists":
        return [
            item for item in matches
            if item[1].get("cropExists") is True
            and dataset.actual_crop_exists_for_row(item[1])
            and dataset.review_status_for_row(item[1]) == "unreviewed"
        ]

    if queue_mode == "grid_slots":
        return [
            item for item in matches
            if item[1].get("regionType") == "gridSlot"
        ]

    if queue_mode == "non_base":
        return [
            item for item in matches
            if (item[1].get("regionProfile") or "base") != "base"
        ]

    return matches


def sampled_queue(
    matches: list[tuple[int, dict]],
    dataset: TrainingDataset,
    queue_mode: str | None,
    size: int,
    seed: str | None,
) -> list[tuple[int, dict]]:
    if not queue_mode:
        return matches

    queue_mode = queue_mode if queue_mode in QUEUE_MODES else "random"
    filtered = queue_filtered_matches(matches, dataset, queue_mode)
    rng = random.Random(seed) if seed else random.Random()

    if len(filtered) <= size:
        return filtered

    if queue_mode == "balanced_active_tab":
        return balanced_sample(filtered, size, rng, lambda row: active_tab_for_row(row))

    if queue_mode == "balanced_region_profile":
        return balanced_sample(filtered, size, rng, lambda row: str(row.get("regionProfile") or "unknown"))

    if queue_mode == "balanced_region_name":
        return balanced_sample(filtered, size, rng, lambda row: str(row.get("regionName") or "unknown"))

    shuffled = list(filtered)
    rng.shuffle(shuffled)
    return shuffled[:size]


def balanced_sample(matches: list[tuple[int, dict]], size: int, rng: random.Random, key_func) -> list[tuple[int, dict]]:
    groups = {}

    for item in matches:
        groups.setdefault(key_func(item[1]), []).append(item)

    for group in groups.values():
        rng.shuffle(group)

    group_names = sorted(groups.keys(), key=str)
    output = []
    index = 0

    while len(output) < size and group_names:
        group_name = group_names[index % len(group_names)]
        group = groups[group_name]

        if group:
            output.append(group.pop())

        if not group:
            group_names.remove(group_name)

            if not group_names:
                break

            index = index % len(group_names)
            continue

        index += 1

    return output


def searchable_text(row: dict) -> str:
    summary = telemetry_summary_for_row(row)
    event_types = summary.get("eventTypesOnTick")
    event_text = " ".join(str(value) for value in event_types) if isinstance(event_types, list) else ""
    parts = [
        row.get("tickId"),
        row.get("timestampUtc"),
        row.get("regionProfile"),
        row.get("regionName"),
        row.get("regionType"),
        row.get("cropPath"),
        row.get("framePath"),
        active_tab_for_row(row),
        label_source_for_row(row),
        event_text,
    ]
    parts.extend(tags_for_row(row))
    return " ".join(str(part).lower() for part in parts if part is not None)


def html_page(dataset: TrainingDataset) -> str:
    summary = dataset.summary()
    message = (
        f"<div class=\"banner warning\">{escape_html(summary['message'])}</div>"
        if not summary["available"]
        else "<div class=\"banner\">Training data loaded from the selected session.</div>"
    )
    escaped_session = escape_html(summary.get("sessionPath") or "none")
    template = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Training Dataset Inspector</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #607080;
      --line: #d8ddd7;
      --accent: #17696a;
      --accent-soft: #dff0ee;
      --warn: #9a3412;
      --warn-bg: #fff7ed;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      overflow: hidden;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, Segoe UI, sans-serif;
      font-size: 14px;
    }
    header {
      height: 92px;
      padding: 16px 20px 12px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }
    h1 { margin: 0 0 6px; font-size: 22px; }
    h2 { margin: 0 0 10px; font-size: 15px; }
    code {
      background: #eef1ef;
      padding: 0.12rem 0.35rem;
      border-radius: 4px;
      overflow-wrap: anywhere;
    }
    button, input, select, textarea {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #ffffff;
      color: var(--text);
      font: inherit;
      min-height: 30px;
    }
    button {
      padding: 5px 10px;
      cursor: pointer;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #ffffff;
    }
    button:disabled {
      opacity: 0.45;
      cursor: default;
    }
    input, select, textarea { padding: 4px 7px; width: 100%; }
    textarea {
      min-height: 58px;
      resize: vertical;
    }
    label { display: grid; gap: 4px; color: var(--muted); font-size: 12px; }
    pre {
      margin: 0;
      padding: 10px;
      border-radius: 6px;
      background: #111827;
      color: #f9fafb;
      overflow: auto;
      font-size: 12px;
      line-height: 1.35;
    }
    .muted { color: var(--muted); }
    .banner {
      margin-top: 8px;
      padding: 8px 10px;
      border-radius: 6px;
      background: var(--accent-soft);
      color: #164e4f;
      width: fit-content;
      max-width: 100%;
    }
    .warning {
      background: var(--warn-bg);
      color: var(--warn);
      font-weight: 600;
    }
    .app {
      height: calc(100vh - 92px);
      display: grid;
      grid-template-columns: minmax(520px, 1.35fr) minmax(420px, 0.9fr);
      gap: 12px;
      padding: 12px;
      overflow: hidden;
    }
    .panel {
      min-height: 0;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      overflow: hidden;
    }
    .left, .right { display: grid; gap: 12px; min-height: 0; }
    .left { grid-template-rows: auto auto 1fr; }
    .right { grid-template-rows: auto 1fr 1fr; }
    .cards {
      display: grid;
      grid-template-columns: repeat(4, minmax(110px, 1fr));
      gap: 8px;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px;
      background: #fbfcfb;
      min-width: 0;
    }
    .card .label { color: var(--muted); font-size: 12px; }
    .card .value { margin-top: 4px; font-size: 18px; font-weight: 700; overflow-wrap: anywhere; }
    .map-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      max-height: 118px;
      overflow: auto;
    }
    .mini-list {
      margin: 0;
      padding: 0;
      list-style: none;
      font-size: 12px;
    }
    .mini-list li {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      padding: 2px 0;
      border-bottom: 1px solid #edf0ed;
    }
    .filters {
      display: grid;
      grid-template-columns: repeat(7, minmax(110px, 1fr));
      gap: 8px;
      align-items: end;
    }
    .filter-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
    }
    .quick-filters {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }
    .queue-controls {
      display: grid;
      grid-template-columns: repeat(5, minmax(130px, 1fr));
      gap: 8px;
      margin-top: 8px;
      align-items: end;
    }
    .table-panel {
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 8px;
    }
    .table-wrap {
      min-height: 0;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      height: 58px;
      max-height: 58px;
      padding: 4px 6px;
      border-bottom: 1px solid #edf0ed;
      vertical-align: middle;
      text-align: left;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      line-height: 1.2;
    }
    th {
      height: 34px;
      position: sticky;
      top: 0;
      z-index: 1;
      background: #f7faf8;
      color: var(--muted);
      font-size: 12px;
    }
    tr[data-index] { cursor: pointer; }
    tr[data-index]:hover { background: #f5fbfa; }
    tr.selected { background: var(--accent-soft); }
    .thumb {
      width: 64px;
      height: 40px;
      display: grid;
      place-items: center;
      border-radius: 5px;
      border: 1px solid var(--line);
      background: #eef1ef;
      color: var(--muted);
      font-size: 11px;
      overflow: hidden;
    }
    .thumb img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
    }
    body[data-density="comfortable"] th,
    body[data-density="comfortable"] td {
      height: 70px;
      max-height: 70px;
      padding: 6px 8px;
    }
    body[data-density="comfortable"] .thumb {
      width: 76px;
      height: 48px;
    }
    .cell-clip {
      display: block;
      min-width: 0;
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .status-cell {
      display: flex;
      flex-wrap: nowrap;
      gap: 4px;
      align-items: center;
      overflow: hidden;
      white-space: nowrap;
    }
    .tick-group {
      display: grid;
      gap: 10px;
    }
    .tick-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(118px, 1fr));
      gap: 8px;
    }
    .tick-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 7px;
      background: #fbfcfb;
      cursor: pointer;
      min-width: 0;
    }
    .tick-card.selected {
      outline: 2px solid var(--accent);
      background: var(--accent-soft);
    }
    .tick-card .thumb {
      width: 100%;
      height: 78px;
      margin-bottom: 5px;
    }
    .badge {
      display: inline-block;
      padding: 2px 6px;
      border-radius: 999px;
      background: #eef1ef;
      color: #40505c;
      font-size: 11px;
      margin: 1px 2px 1px 0;
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      vertical-align: middle;
    }
    .badge.accent { background: var(--accent-soft); color: var(--accent); }
    .pager, .detail-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .review-box {
      display: grid;
      grid-template-columns: minmax(180px, 1fr) minmax(220px, 1.1fr);
      gap: 8px;
      align-items: start;
    }
    .review-buttons {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .pager input { width: 110px; }
    .media-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      min-height: 0;
    }
    .media-box {
      min-height: 0;
      display: grid;
      grid-template-rows: auto 1fr;
      gap: 6px;
    }
    .media-frame {
      min-height: 160px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #eef1ef;
      display: grid;
      place-items: center;
      overflow: hidden;
      color: var(--muted);
      text-align: center;
      padding: 8px;
    }
    .media-frame img {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      display: block;
    }
    .detail-panel {
      display: grid;
      grid-template-rows: auto auto 1fr;
      gap: 10px;
    }
    .detail-json {
      min-height: 0;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .detail-json pre { min-height: 0; max-height: none; }
    .path {
      font-size: 11px;
      color: var(--muted);
      overflow-wrap: anywhere;
    }
    .detail-paths {
      display: grid;
      gap: 3px;
      margin-top: 6px;
      font-size: 11px;
      color: var(--muted);
    }
    .detail-paths div {
      overflow-wrap: anywhere;
    }
    @media (max-width: 1150px) {
      body { overflow: auto; }
      .app { height: auto; grid-template-columns: 1fr; overflow: visible; }
      .left, .right { grid-template-rows: none; }
      .panel { min-height: 260px; }
      .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .filters { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .queue-controls { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .detail-json, .media-grid { grid-template-columns: 1fr; }
      .review-box { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Training Dataset Inspector</h1>
    <div class="muted">Session: <code>__SESSION__</code></div>
    __MESSAGE__
  </header>
  <main class="app">
    <section class="left">
      <section class="panel">
        <h2>Summary</h2>
        <div id="summaryCards" class="cards"></div>
        <div id="missingCropWarning" class="banner warning" style="display:none"></div>
      </section>
      <section class="panel">
        <h2>Counts</h2>
        <div class="map-grid">
          <div>
            <div class="muted">activeTab</div>
            <ul id="activeTabCounts" class="mini-list"></ul>
          </div>
          <div>
            <div class="muted">regionProfile</div>
            <ul id="regionProfileCounts" class="mini-list"></ul>
          </div>
          <div>
            <div class="muted">tag</div>
            <ul id="tagCounts" class="mini-list"></ul>
          </div>
        </div>
      </section>
      <section class="panel table-panel">
        <div>
          <h2>Examples</h2>
          <div class="filters">
            <label>activeTab<select id="activeTabFilter"><option value="">Any</option></select></label>
            <label>regionProfile<select id="regionProfileFilter"><option value="">Any</option></select></label>
            <label>regionName<input id="regionNameFilter" list="regionNameOptions" placeholder="Any"></label>
            <label>tag<input id="tagFilter" list="tagOptions" placeholder="Any"></label>
            <label>labelSource<input id="labelSourceFilter" list="labelSourceOptions" placeholder="Any"></label>
            <label>cropExists<select id="cropExistsFilter"><option value="">Any</option><option value="true">true</option><option value="false">false</option></select></label>
            <label>text search<input id="searchFilter" placeholder="tick, path, event, label"></label>
          </div>
          <datalist id="regionNameOptions"></datalist>
          <datalist id="tagOptions"></datalist>
          <datalist id="labelSourceOptions"></datalist>
          <div class="filter-actions" style="margin-top:8px">
            <button id="applyFilters" class="primary" type="button">Apply</button>
            <button id="clearFilters" type="button">Clear</button>
            <button id="showAllExamples" type="button">Show all examples</button>
            <label>review<select id="reviewStatusFilter"><option value="">Any</option><option value="unreviewed">unreviewed</option><option value="reviewed">reviewed</option><option value="good">good</option><option value="bad_crop">bad_crop</option><option value="wrong_label">wrong_label</option><option value="unsure">unsure</option></select></label>
            <label>page size<select id="pageSize"><option value="25">25</option><option value="50">50</option><option value="100">100</option><option value="250">250</option></select></label>
            <label>density<select id="densityMode"><option value="compact">Compact</option><option value="comfortable">Comfortable</option></select></label>
            <label>sort<select id="sortBy"><option value="tick_asc">tickId ascending</option><option value="tick_desc">tickId descending</option><option value="activeTab">activeTab</option><option value="regionProfile">regionProfile</option><option value="regionName">regionName</option><option value="cropExists">cropExists</option><option value="reviewStatus">review status</option></select></label>
            <span id="exampleStatus" class="muted"></span>
          </div>
          <div class="quick-filters">
            <button data-quick-filter="reviewQueue" type="button">Review queue</button>
            <button data-quick-filter="cropExists" type="button">Crop exists only</button>
            <button data-quick-filter="missingCrops" type="button">Missing crops only</button>
            <button data-quick-filter="unknownActiveTab" type="button">Unknown activeTab</button>
            <button data-quick-filter="nonBase" type="button">Non-base profiles only</button>
            <button data-quick-filter="inventory" type="button">Inventory only</button>
            <button data-quick-filter="prayer" type="button">Prayer only</button>
            <button data-quick-filter="equipment" type="button">Equipment only</button>
            <button data-quick-filter="gridSlots" type="button">Grid slots only</button>
            <button data-quick-filter="base" type="button">Base regions</button>
            <button data-quick-filter="reviewed" type="button">Reviewed</button>
            <button data-quick-filter="unreviewed" type="button">Unreviewed</button>
          </div>
          <div class="queue-controls">
            <label>Review Queue
              <select id="queueMode">
                <option value="">Off</option>
                <option value="random">Random sample</option>
                <option value="balanced_active_tab">Balanced by activeTab</option>
                <option value="balanced_region_profile">Balanced by regionProfile</option>
                <option value="balanced_region_name">Balanced by regionName</option>
                <option value="missing_crops">Missing crops</option>
                <option value="unknown_active_tab">Unknown activeTab</option>
                <option value="unreviewed_crop_exists">Unreviewed cropExists</option>
                <option value="grid_slots">Grid slots only</option>
                <option value="non_base">Non-base profiles only</option>
              </select>
            </label>
            <label>queue size
              <select id="queueSize">
                <option value="25">25</option>
                <option value="50">50</option>
                <option value="100">100</option>
                <option value="250">250</option>
                <option value="500">500</option>
              </select>
            </label>
            <label>seed<input id="queueSeed" placeholder="optional"></label>
            <label style="align-self:center"><input id="groupByTick" type="checkbox"> Group by tick</label>
            <button id="loadQueue" class="primary" type="button">Load queue</button>
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th style="width:74px">tick</th>
                <th style="width:82px">crop</th>
                <th style="width:110px">activeTab</th>
                <th style="width:110px">profile</th>
                <th style="width:150px">region</th>
                <th style="width:140px">tags</th>
                <th style="width:110px">review</th>
                <th style="width:110px">labelSource</th>
                <th style="width:150px">files</th>
              </tr>
            </thead>
            <tbody id="examplesBody"></tbody>
          </table>
        </div>
        <div class="pager">
          <button id="prevPage" type="button">Previous page</button>
          <button id="nextPage" type="button">Next page</button>
          <button id="prevTickGroup" type="button">Previous tick group</button>
          <button id="nextTickGroup" type="button">Next tick group</button>
          <label>Jump tick<input id="jumpTick" type="number" placeholder="tickId"></label>
          <button id="jumpTickButton" type="button">Jump</button>
        </div>
      </section>
    </section>
    <section class="right">
      <section class="panel detail-panel">
        <div>
          <h2>Selected Example</h2>
          <div id="detailTitle" class="muted">Select an example row.</div>
          <div id="pathDetail" class="detail-paths"></div>
        </div>
        <div class="detail-actions">
          <button id="previousExample" type="button">Previous example</button>
          <button id="nextExample" type="button">Next example</button>
          <button id="openFrame" type="button">Open source frame</button>
          <a id="replayLink" href="#" target="_blank" rel="noreferrer">Replay tick</a>
        </div>
        <div class="review-box">
          <div>
            <div class="muted">QA review</div>
            <div class="review-buttons">
              <button class="review-button" data-review-label="good" type="button">Mark Good</button>
              <button class="review-button" data-review-label="bad_crop" type="button">Mark Bad Crop</button>
              <button class="review-button" data-review-label="wrong_label" type="button">Mark Wrong Label</button>
              <button class="review-button" data-review-label="unsure" type="button">Mark Unsure</button>
            </div>
            <div class="review-buttons" style="margin-top:6px">
              <button class="batch-review-button" data-review-label="good" type="button">Mark current page Good</button>
              <button class="batch-review-button" data-review-label="bad_crop" type="button">Mark current page Bad Crop</button>
              <button class="batch-review-button" data-review-label="unsure" type="button">Mark current page Unsure</button>
            </div>
            <div class="review-buttons" style="margin-top:6px">
              <button class="tick-review-button" data-review-label="good" type="button">Mark tick Good</button>
              <button class="tick-review-button" data-review-label="bad_crop" type="button">Mark tick Bad Crop</button>
              <button class="tick-review-button" data-review-label="unsure" type="button">Mark tick Unsure</button>
            </div>
            <div id="reviewStatus" class="muted" style="margin-top:6px">No review saved for this selection.</div>
          </div>
          <label>notes<textarea id="reviewNotes" placeholder="Optional QA note"></textarea></label>
        </div>
        <div id="cropDiagnostic" class="banner warning" style="display:none"></div>
        <div class="media-grid">
          <div class="media-box">
            <div class="muted">Crop</div>
            <div id="cropPreview" class="media-frame">No crop selected.</div>
          </div>
          <div class="media-box">
            <div class="muted">Source frame</div>
            <div id="framePreview" class="media-frame">No frame selected.</div>
          </div>
        </div>
      </section>
      <section class="panel detail-panel">
        <h2>Telemetry and Labels</h2>
        <div class="detail-json">
          <pre id="telemetryJson">{}</pre>
          <pre id="labelsJson">{}</pre>
        </div>
      </section>
      <section class="panel detail-panel">
        <h2>Geometry and Source</h2>
        <div class="detail-json">
          <pre id="geometryJson">{}</pre>
          <pre id="sourceJson">{}</pre>
        </div>
      </section>
    </section>
  </main>
  <script>
    const state = {
      available: false,
      offset: 0,
      limit: 100,
      totalMatched: 0,
      examples: [],
      selectedPosition: -1,
      selectedIndex: null,
      selectedExample: null,
      latestReview: null,
      cropDiagnostics: null,
      queueMode: "balanced_region_profile",
      queueSize: 100,
      queueSeed: "",
      groupByTick: false,
      tickGroups: [],
      selectedGroupIndex: 0,
      hideLowValueBase: true,
      actualCropExistsFilter: "",
      nonBaseOnly: false,
      regionTypeFilter: "",
      summary: null
    };

    const el = {
      summaryCards: document.getElementById("summaryCards"),
      missingCropWarning: document.getElementById("missingCropWarning"),
      activeTabCounts: document.getElementById("activeTabCounts"),
      regionProfileCounts: document.getElementById("regionProfileCounts"),
      tagCounts: document.getElementById("tagCounts"),
      activeTabFilter: document.getElementById("activeTabFilter"),
      regionProfileFilter: document.getElementById("regionProfileFilter"),
      regionNameFilter: document.getElementById("regionNameFilter"),
      tagFilter: document.getElementById("tagFilter"),
      labelSourceFilter: document.getElementById("labelSourceFilter"),
      cropExistsFilter: document.getElementById("cropExistsFilter"),
      reviewStatusFilter: document.getElementById("reviewStatusFilter"),
      searchFilter: document.getElementById("searchFilter"),
      pageSize: document.getElementById("pageSize"),
      densityMode: document.getElementById("densityMode"),
      sortBy: document.getElementById("sortBy"),
      queueMode: document.getElementById("queueMode"),
      queueSize: document.getElementById("queueSize"),
      queueSeed: document.getElementById("queueSeed"),
      groupByTick: document.getElementById("groupByTick"),
      loadQueue: document.getElementById("loadQueue"),
      regionNameOptions: document.getElementById("regionNameOptions"),
      tagOptions: document.getElementById("tagOptions"),
      labelSourceOptions: document.getElementById("labelSourceOptions"),
      applyFilters: document.getElementById("applyFilters"),
      clearFilters: document.getElementById("clearFilters"),
      examplesBody: document.getElementById("examplesBody"),
      exampleStatus: document.getElementById("exampleStatus"),
      prevPage: document.getElementById("prevPage"),
      nextPage: document.getElementById("nextPage"),
      prevTickGroup: document.getElementById("prevTickGroup"),
      nextTickGroup: document.getElementById("nextTickGroup"),
      jumpTick: document.getElementById("jumpTick"),
      jumpTickButton: document.getElementById("jumpTickButton"),
      previousExample: document.getElementById("previousExample"),
      nextExample: document.getElementById("nextExample"),
      openFrame: document.getElementById("openFrame"),
      replayLink: document.getElementById("replayLink"),
      reviewNotes: document.getElementById("reviewNotes"),
      reviewStatus: document.getElementById("reviewStatus"),
      cropDiagnostic: document.getElementById("cropDiagnostic"),
      detailTitle: document.getElementById("detailTitle"),
      pathDetail: document.getElementById("pathDetail"),
      cropPreview: document.getElementById("cropPreview"),
      framePreview: document.getElementById("framePreview"),
      telemetryJson: document.getElementById("telemetryJson"),
      labelsJson: document.getElementById("labelsJson"),
      geometryJson: document.getElementById("geometryJson"),
      sourceJson: document.getElementById("sourceJson"),
      reviewButtons: Array.from(document.querySelectorAll(".review-button")),
      batchReviewButtons: Array.from(document.querySelectorAll(".batch-review-button")),
      tickReviewButtons: Array.from(document.querySelectorAll(".tick-review-button")),
      quickFilterButtons: Array.from(document.querySelectorAll("[data-quick-filter]")),
      showAllExamples: document.getElementById("showAllExamples")
    };

    const storageKey = "trainingDatasetInspector.v1";

    function savePrefs() {
      const prefs = {
        activeTab: el.activeTabFilter.value,
        regionProfile: el.regionProfileFilter.value,
        regionName: el.regionNameFilter.value,
        tag: el.tagFilter.value,
        labelSource: el.labelSourceFilter.value,
        cropExists: el.cropExistsFilter.value,
        reviewStatus: el.reviewStatusFilter.value,
        q: el.searchFilter.value,
        pageSize: el.pageSize.value,
        densityMode: el.densityMode.value,
        sort: el.sortBy.value,
        queueMode: el.queueMode.value,
        queueSize: el.queueSize.value,
        queueSeed: el.queueSeed.value,
        groupByTick: el.groupByTick.checked,
        offset: state.offset,
        selectedIndex: state.selectedIndex,
        hideLowValueBase: state.hideLowValueBase,
        actualCropExists: state.actualCropExistsFilter,
        nonBaseOnly: state.nonBaseOnly,
        regionType: state.regionTypeFilter
      };
      localStorage.setItem(storageKey, JSON.stringify(prefs));
    }

    function loadPrefs() {
      try {
        return JSON.parse(localStorage.getItem(storageKey) || "{}");
      } catch (_error) {
        return {};
      }
    }

    function hasPrefs() {
      return localStorage.getItem(storageKey) !== null;
    }

    function applyPrefs(prefs) {
      el.activeTabFilter.value = prefs.activeTab || "";
      el.regionProfileFilter.value = prefs.regionProfile || "";
      el.regionNameFilter.value = prefs.regionName || "";
      el.tagFilter.value = prefs.tag || "";
      el.labelSourceFilter.value = prefs.labelSource || "";
      el.cropExistsFilter.value = prefs.cropExists || "";
      el.reviewStatusFilter.value = prefs.reviewStatus || "";
      el.searchFilter.value = prefs.q || "";
      el.pageSize.value = prefs.pageSize || "50";
      el.densityMode.value = prefs.densityMode || "compact";
      document.body.dataset.density = el.densityMode.value;
      el.sortBy.value = prefs.sort || "tick_asc";
      el.queueMode.value = prefs.queueMode || "balanced_region_profile";
      el.queueSize.value = prefs.queueSize || "100";
      el.queueSeed.value = prefs.queueSeed || "";
      el.groupByTick.checked = Boolean(prefs.groupByTick);
      state.queueMode = el.queueMode.value;
      state.queueSize = Number(el.queueSize.value) || 100;
      state.queueSeed = el.queueSeed.value;
      state.groupByTick = el.groupByTick.checked;
      state.limit = Number(el.pageSize.value) || 50;
      state.offset = Number.isFinite(Number(prefs.offset)) ? Math.max(0, Number(prefs.offset)) : 0;
      state.selectedIndex = Number.isFinite(Number(prefs.selectedIndex)) ? Number(prefs.selectedIndex) : null;
      state.hideLowValueBase = prefs.hideLowValueBase !== undefined ? Boolean(prefs.hideLowValueBase) : true;
      state.actualCropExistsFilter = prefs.actualCropExists || "";
      state.nonBaseOnly = Boolean(prefs.nonBaseOnly);
      state.regionTypeFilter = prefs.regionType || "";
    }

    function applyDefaultQueueFilters(summary) {
      el.cropExistsFilter.value = "true";
      state.actualCropExistsFilter = "true";
      state.hideLowValueBase = true;
      state.nonBaseOnly = false;
      state.regionTypeFilter = "";
      el.reviewStatusFilter.value = summary.reviewCount > 0 ? "unreviewed" : "";
      el.pageSize.value = "50";
      el.densityMode.value = "compact";
      document.body.dataset.density = "compact";
      el.sortBy.value = "tick_asc";
      el.queueMode.value = "balanced_region_profile";
      el.queueSize.value = "100";
      el.queueSeed.value = "";
      el.groupByTick.checked = false;
      state.queueMode = "balanced_region_profile";
      state.queueSize = 100;
      state.queueSeed = "";
      state.groupByTick = false;
      state.limit = 50;
      state.offset = 0;
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function jsonBlock(value) {
      return JSON.stringify(value ?? {}, null, 2);
    }

    function formatNumber(value) {
      if (value === null || value === undefined || value === "") return "-";
      return Number(value).toLocaleString();
    }

    function imageUrl(kind, path) {
      return `/${kind}?path=${encodeURIComponent(path || "")}`;
    }

    function renderCountList(target, counts) {
      const entries = Object.entries(counts || {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
      target.innerHTML = entries.length
        ? entries.map(([name, count]) => `<li><span>${escapeHtml(name)}</span><strong>${formatNumber(count)}</strong></li>`).join("")
        : "<li><span>none</span><strong>-</strong></li>";
    }

    function renderSummary(summary) {
      state.summary = summary;
      state.available = Boolean(summary.available);
      const cards = [
        ["manifest examples", summary.manifestExamples ?? summary.totalExamples],
        ["cropExists=true", summary.manifestCropExistsTrueExamples],
        ["cropExists=false", summary.manifestCropExistsFalseExamples],
        ["actual crop files", summary.actualCropFilesFound ?? summary.cropCount],
        ["reviewed examples", summary.reviewedExamples],
        ["unreviewed examples", summary.unreviewedExamples],
        ["unknown activeTab", summary.unknownActiveTabCount],
        ["skipped missing frames", summary.skippedMissingFrames]
      ];
      el.summaryCards.innerHTML = cards.map(([label, value]) => `
        <div class="card">
          <div class="label">${escapeHtml(label)}</div>
          <div class="value">${formatNumber(value)}</div>
        </div>
      `).join("");
      renderCountList(el.activeTabCounts, summary.countsByActiveTab);
      renderCountList(el.regionProfileCounts, summary.countsByRegionProfile);
      renderCountList(el.tagCounts, summary.countsByTag);
      const missingCount = Number(summary.manifestCropTrueMissingFiles || 0) + Number(summary.manifestCropExistsFalseExamples || 0);

      if (summary.missingCropWarning && missingCount > 0) {
        el.missingCropWarning.style.display = "";
        el.missingCropWarning.textContent = `${summary.missingCropWarning} Missing/stale manifest crop rows: ${formatNumber(missingCount)}.`;
      } else {
        el.missingCropWarning.style.display = "none";
        el.missingCropWarning.textContent = "";
      }

      if (!summary.available) {
        el.examplesBody.innerHTML = `<tr><td colspan="9">${escapeHtml(summary.message || "Training data is unavailable.")}</td></tr>`;
      }
    }

    function fillSelect(select, values) {
      const current = select.value;
      const first = select.querySelector("option[value='']");
      select.innerHTML = "";
      select.appendChild(first || new Option("Any", ""));
      for (const value of values || []) {
        select.appendChild(new Option(value, value));
      }
      select.value = current;
    }

    function fillDatalist(list, values) {
      list.innerHTML = (values || []).map((value) => `<option value="${escapeHtml(value)}"></option>`).join("");
    }

    async function loadFilterOptions() {
      const response = await fetch("/api/filter-options");
      const data = await response.json();
      const options = data.options || {};
      fillSelect(el.activeTabFilter, options.activeTabs || []);
      fillSelect(el.regionProfileFilter, options.regionProfiles || []);
      fillDatalist(el.regionNameOptions, options.regionNames || []);
      fillDatalist(el.tagOptions, options.tags || []);
      fillDatalist(el.labelSourceOptions, options.labelSources || []);
    }

    function queryParams(extra = {}) {
      const params = new URLSearchParams();
      const values = {
        activeTab: el.activeTabFilter.value,
        regionProfile: el.regionProfileFilter.value,
        regionName: el.regionNameFilter.value,
        tag: el.tagFilter.value,
        labelSource: el.labelSourceFilter.value,
        cropExists: el.cropExistsFilter.value,
        actualCropExists: state.actualCropExistsFilter,
        reviewStatus: el.reviewStatusFilter.value,
        hideLowValueBase: state.hideLowValueBase ? "true" : "",
        nonBaseOnly: state.nonBaseOnly ? "true" : "",
        regionType: state.regionTypeFilter,
        q: el.searchFilter.value,
        sort: el.sortBy.value,
        queueMode: el.queueMode.value,
        queueSize: el.queueSize.value,
        seed: el.queueSeed.value,
        offset: String(state.offset),
        limit: String(state.limit),
        ...extra
      };
      for (const [key, value] of Object.entries(values)) {
        if (value !== null && value !== undefined && String(value).trim() !== "") {
          params.set(key, String(value).trim());
        }
      }
      return params;
    }

    async function loadExamples({ resetOffset = false } = {}) {
      if (!state.available) return;
      if (resetOffset) state.offset = 0;
      state.queueMode = el.queueMode.value;
      state.queueSize = Number(el.queueSize.value) || 100;
      state.queueSeed = el.queueSeed.value;
      state.groupByTick = el.groupByTick.checked;
      const response = await fetch(`/api/examples?${queryParams()}`);
      const data = await response.json();
      state.examples = data.examples || [];
      state.totalMatched = data.totalMatched || 0;
      state.tickGroups = groupExamplesByTick(state.examples);
      state.selectedGroupIndex = 0;
      renderExamples();
      savePrefs();
    }

    function thumb(row) {
      if (!row.cropPath || row.cropExists !== true || row.actualCropExists !== true) {
        return `<div class="thumb">missing</div>`;
      }
      return `<div class="thumb"><img src="${imageUrl("crop", row.cropPath)}" alt="crop" loading="lazy" onerror="this.parentElement.textContent='missing'"></div>`;
    }

    function badges(values) {
      const list = Array.isArray(values) ? values : [];
      return list.length
        ? list.map((value) => `<span class="badge">${escapeHtml(value)}</span>`).join("")
        : '<span class="muted">-</span>';
    }

    function reviewBadge(review) {
      return review?.reviewLabel
        ? `<span class="badge accent">review: ${escapeHtml(review.reviewLabel)}</span>`
        : "";
    }

    function fileBadges(row) {
      const cropLabel = row.cropExists === true && row.actualCropExists === true ? "crop ok" : "crop missing";
      const cropClass = row.cropExists === true && row.actualCropExists === true ? "accent" : "";
      const frameLabel = row.framePath ? "frame" : "no frame";
      return `<div class="status-cell">
        <span class="badge ${cropClass}">${cropLabel}</span>
        <span class="badge">${frameLabel}</span>
        <span class="badge">manifest</span>
      </div>`;
    }

    function groupExamplesByTick(examples) {
      const groups = [];
      const byTick = new Map();

      for (const row of examples) {
        const key = String(row.tickId ?? "unknown");

        if (!byTick.has(key)) {
          const group = { tickId: row.tickId, rows: [] };
          byTick.set(key, group);
          groups.push(group);
        }

        byTick.get(key).rows.push(row);
      }

      return groups;
    }

    function renderExamples() {
      if (state.groupByTick) {
        renderTickGroup();
        return;
      }
      el.examplesBody.innerHTML = state.examples.length
        ? state.examples.map((row, position) => `
          <tr data-index="${escapeHtml(row.index)}" data-position="${position}" class="${row.index === state.selectedIndex ? "selected" : ""}">
            <td><span class="cell-clip">${escapeHtml(row.tickId)}</span></td>
            <td>${thumb(row)}</td>
            <td><span class="badge accent">${escapeHtml(row.activeTab)}</span></td>
            <td><span class="cell-clip" title="${escapeHtml(row.regionProfile || "")}">${escapeHtml(row.regionProfile)}</span></td>
            <td><span class="cell-clip" title="${escapeHtml(row.regionName || "")}">${escapeHtml(row.regionName)}</span></td>
            <td>${badges(row.tags)}</td>
            <td><span class="cell-clip">${escapeHtml(row.reviewStatus || "unreviewed")}</span></td>
            <td><span class="cell-clip" title="${escapeHtml(row.labelSource || "-")}">${escapeHtml(row.labelSource || "-")}</span></td>
            <td>${fileBadges(row)}</td>
          </tr>
        `).join("")
        : `<tr><td colspan="9">No examples match the current filters.</td></tr>`;
      const first = state.totalMatched === 0 ? 0 : state.offset + 1;
      const last = Math.min(state.offset + state.examples.length, state.totalMatched);
      el.exampleStatus.textContent = `${formatNumber(first)}-${formatNumber(last)} of ${formatNumber(state.totalMatched)}`;
      el.prevPage.disabled = state.offset <= 0;
      el.nextPage.disabled = state.offset + state.limit >= state.totalMatched;
      updateDetailNavigation();
    }

    function renderTickGroup() {
      if (!state.tickGroups.length) {
        el.examplesBody.innerHTML = `<tr><td colspan="9">No examples match the current filters.</td></tr>`;
        el.exampleStatus.textContent = "0 of 0";
        updateDetailNavigation();
        return;
      }
      state.selectedGroupIndex = Math.max(0, Math.min(state.selectedGroupIndex, state.tickGroups.length - 1));
      const group = state.tickGroups[state.selectedGroupIndex];
      el.examplesBody.innerHTML = `
        <tr>
          <td colspan="9">
            <div class="tick-group">
              <div><strong>tick ${escapeHtml(group.tickId)}</strong> <span class="muted">${group.rows.length} examples in this tick group</span></div>
              <div class="tick-grid">
                ${group.rows.map((row) => `
                  <div class="tick-card ${row.index === state.selectedIndex ? "selected" : ""}" data-index="${escapeHtml(row.index)}" data-position="${state.examples.findIndex((example) => example.index === row.index)}">
                    ${thumb(row)}
                    <div><strong>${escapeHtml(row.regionName)}</strong></div>
                    <div class="muted">${escapeHtml(row.regionProfile)} - ${escapeHtml(row.regionType)}</div>
                    <div>${escapeHtml(row.reviewStatus || "unreviewed")}</div>
                  </div>
                `).join("")}
              </div>
            </div>
          </td>
        </tr>`;
      el.exampleStatus.textContent = `tick group ${state.selectedGroupIndex + 1} of ${state.tickGroups.length}; ${state.examples.length} examples in queue`;
      el.prevPage.disabled = true;
      el.nextPage.disabled = true;
      updateDetailNavigation();
    }

    async function selectExample(index, position = -1) {
      const response = await fetch(`/api/example/${encodeURIComponent(index)}`);
      if (!response.ok) return;
      const data = await response.json();
      state.selectedIndex = data.index;
      state.selectedPosition = position;
      state.selectedExample = data.example;
      state.latestReview = data.latestReview || null;
      state.cropDiagnostics = data.cropDiagnostics || null;
      renderExamples();
      renderDetail(data.index, data.example);
      savePrefs();
    }

    function media(path, kind, missingText) {
      if (!path) {
        return `<span>${escapeHtml(missingText)}</span>`;
      }
      return `<img src="${imageUrl(kind, path)}" alt="${kind}" onerror="this.replaceWith(document.createTextNode('${escapeHtml(missingText)}'))">`;
    }

    function renderDetail(index, example) {
      const labels = example.labels || {};
      const summary = example.telemetrySummary || {};
      const source = example.source || {};
      const geometry = {
        pixelGeometry: example.pixelGeometry,
        normalizedGeometry: example.normalizedGeometry
      };
      el.detailTitle.innerHTML = `#${escapeHtml(index)} tick ${escapeHtml(example.tickId)} - ${escapeHtml(example.regionProfile)} / ${escapeHtml(example.regionName)}`;
      el.pathDetail.innerHTML = `
        <div><strong>crop:</strong> ${escapeHtml(example.cropPath || "-")}</div>
        <div><strong>frame:</strong> ${escapeHtml(example.framePath || "-")}</div>
      `;
      const diagnostics = state.cropDiagnostics || {};
      el.cropPreview.innerHTML = example.cropExists === true && diagnostics.actualCropExists === true
        ? media(example.cropPath, "crop", "Crop file missing.")
        : "<span>Crop file missing.</span>";
      el.framePreview.innerHTML = media(example.framePath, "frame", "Source frame missing.");
      el.telemetryJson.textContent = jsonBlock(summary);
      el.labelsJson.textContent = jsonBlock(labels);
      el.geometryJson.textContent = jsonBlock(geometry);
      el.sourceJson.textContent = jsonBlock(source);
      el.openFrame.disabled = !example.framePath;
      el.replayLink.href = `http://127.0.0.1:8765/?tickId=${encodeURIComponent(example.tickId ?? "")}`;
      renderCropDiagnostic(diagnostics);
      renderReviewStatus(state.latestReview);
      updateDetailNavigation();
    }

    function renderCropDiagnostic(diagnostics) {
      if (!diagnostics || diagnostics.actualCropExists === true) {
        el.cropDiagnostic.style.display = "none";
        el.cropDiagnostic.textContent = "";
        return;
      }
      const manifestValue = diagnostics.manifestCropExists ? "true" : "false";
      const actualValue = diagnostics.actualCropExists ? "true" : "false";
      const resolved = diagnostics.resolvedCropPath || "unavailable";
      el.cropDiagnostic.style.display = "";
      el.cropDiagnostic.textContent = `Crop missing. manifest cropExists=${manifestValue}; file exists on disk=${actualValue}; resolved path=${resolved}. Try rebuilding training data with --rebuild, or filter to cropExists=true.`;
    }

    function renderReviewStatus(review) {
      if (!review) {
        el.reviewStatus.textContent = "No review saved for this selection.";
        return;
      }
      const note = review.notes ? ` - ${review.notes}` : "";
      el.reviewStatus.textContent = `Latest review: ${review.reviewLabel} at ${review.timestampUtc}${note}`;
    }

    function updateDetailNavigation() {
      el.previousExample.disabled = state.selectedPosition <= 0;
      el.nextExample.disabled = state.selectedPosition < 0 || state.selectedPosition >= state.examples.length - 1;
      const hasSelection = state.selectedIndex !== null && state.selectedIndex !== undefined;
      for (const button of el.reviewButtons) {
        button.disabled = !hasSelection;
      }
      for (const button of el.batchReviewButtons) {
        button.disabled = !state.examples.length;
      }
      const hasTickGroup = state.groupByTick && state.tickGroups.length > 0;
      for (const button of el.tickReviewButtons) {
        button.disabled = !hasTickGroup;
      }
      el.prevTickGroup.disabled = !hasTickGroup || state.selectedGroupIndex <= 0;
      el.nextTickGroup.disabled = !hasTickGroup || state.selectedGroupIndex >= state.tickGroups.length - 1;
    }

    async function jumpToTick() {
      const tickId = el.jumpTick.value.trim();
      if (!tickId) return;
      const params = new URLSearchParams({ tickId, offset: "0", limit: "1" });
      const response = await fetch(`/api/examples?${params}`);
      const data = await response.json();
      const row = (data.examples || [])[0];
      if (!row) {
        el.exampleStatus.textContent = `No example found for tick ${tickId}.`;
        return;
      }
      await selectExample(row.index, -1);
    }

    function resetFiltersOnly() {
      el.activeTabFilter.value = "";
      el.regionProfileFilter.value = "";
      el.regionNameFilter.value = "";
      el.tagFilter.value = "";
      el.labelSourceFilter.value = "";
      el.cropExistsFilter.value = "";
      el.reviewStatusFilter.value = "";
      el.searchFilter.value = "";
      el.queueMode.value = "";
      state.actualCropExistsFilter = "";
      state.hideLowValueBase = false;
      state.nonBaseOnly = false;
      state.regionTypeFilter = "";
    }

    function clearFilters() {
      resetFiltersOnly();
      loadExamples({ resetOffset: true });
    }

    function showAllExamples() {
      clearFilters();
    }

    function setReviewQueueDefaults() {
      el.cropExistsFilter.value = "true";
      el.reviewStatusFilter.value = "unreviewed";
      el.queueMode.value = "balanced_region_profile";
      el.queueSize.value = "100";
      state.actualCropExistsFilter = "true";
      state.hideLowValueBase = true;
      state.nonBaseOnly = false;
      state.regionTypeFilter = "";
    }

    function applyQuickFilter(name) {
      resetFiltersOnly();

      if (name === "reviewQueue") {
        setReviewQueueDefaults();
      } else if (name === "cropExists") {
        el.cropExistsFilter.value = "true";
        state.actualCropExistsFilter = "true";
      } else if (name === "missingCrops") {
        el.queueMode.value = "missing_crops";
        state.actualCropExistsFilter = "false";
      } else if (name === "unknownActiveTab") {
        el.queueMode.value = "unknown_active_tab";
        el.activeTabFilter.value = "unknown";
      } else if (name === "nonBase") {
        el.queueMode.value = "non_base";
        state.nonBaseOnly = true;
      } else if (name === "inventory" || name === "prayer" || name === "equipment") {
        el.activeTabFilter.value = name;
        el.cropExistsFilter.value = "true";
        state.actualCropExistsFilter = "true";
      } else if (name === "gridSlots") {
        el.queueMode.value = "grid_slots";
        state.regionTypeFilter = "gridSlot";
        el.cropExistsFilter.value = "true";
        state.actualCropExistsFilter = "true";
      } else if (name === "base") {
        el.regionProfileFilter.value = "base";
      } else if (name === "reviewed") {
        el.reviewStatusFilter.value = "reviewed";
      } else if (name === "unreviewed") {
        el.reviewStatusFilter.value = "unreviewed";
      }

      loadExamples({ resetOffset: true });
    }

    async function saveReview(reviewLabel) {
      if (state.selectedIndex === null || state.selectedIndex === undefined) {
        el.reviewStatus.textContent = "Select an example before saving a review.";
        return;
      }
      const response = await fetch("/api/review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          index: state.selectedIndex,
          reviewLabel,
          notes: el.reviewNotes.value || ""
        })
      });
      const data = await response.json();

      if (!response.ok) {
        el.reviewStatus.textContent = data.error || "Review could not be saved.";
        return;
      }

      state.latestReview = data.review;
      el.reviewNotes.value = "";
      renderReviewStatus(data.review);
      const row = state.examples.find((example) => example.index === state.selectedIndex);

      if (row) {
        row.latestReview = data.review;
        renderExamples();
      }
    }

    async function saveReviewBatch(reviewLabel, indexes) {
      const uniqueIndexes = [...new Set(indexes.map((value) => Number(value)).filter((value) => Number.isFinite(value)))];

      if (!uniqueIndexes.length) {
        el.reviewStatus.textContent = "No visible examples to review.";
        return;
      }

      const response = await fetch("/api/review-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          indexes: uniqueIndexes,
          reviewLabel,
          notes: el.reviewNotes.value || ""
        })
      });
      const data = await response.json();

      if (!response.ok) {
        el.reviewStatus.textContent = data.error || "Batch review could not be saved.";
        return;
      }

      el.reviewNotes.value = "";
      el.reviewStatus.textContent = `Saved ${data.reviewedCount} ${reviewLabel} review records.`;
      await loadExamples();
    }

    function visibleIndexes() {
      if (state.groupByTick && state.tickGroups.length) {
        return state.tickGroups[state.selectedGroupIndex].rows.map((row) => row.index);
      }

      return state.examples.map((row) => row.index);
    }

    function currentTickIndexes() {
      if (!state.tickGroups.length) return [];
      return state.tickGroups[state.selectedGroupIndex].rows.map((row) => row.index);
    }

    function moveSelection(delta) {
      if (!state.examples.length) return;
      let position = state.selectedPosition;

      if (position < 0) {
        position = delta > 0 ? 0 : state.examples.length - 1;
      } else {
        position = Math.max(0, Math.min(state.examples.length - 1, position + delta));
      }

      const row = state.examples[position];

      if (row) {
        selectExample(row.index, position);
      }
    }

    function shortcutAllowed(event) {
      const target = event.target;
      const tag = target?.tagName?.toLowerCase();
      return !(tag === "input" || tag === "textarea" || tag === "select" || target?.isContentEditable);
    }

    async function init() {
      const summary = await fetch("/api/summary").then((response) => response.json());
      renderSummary(summary);
      await loadFilterOptions();

      if (hasPrefs()) {
        applyPrefs(loadPrefs());
      } else {
        applyDefaultQueueFilters(summary);
      }

      await loadExamples();

      if (state.selectedIndex !== null) {
        await selectExample(state.selectedIndex, state.examples.findIndex((row) => row.index === state.selectedIndex));
      }
    }

    el.applyFilters.addEventListener("click", () => loadExamples({ resetOffset: true }));
    el.clearFilters.addEventListener("click", clearFilters);
    el.showAllExamples.addEventListener("click", showAllExamples);
    el.prevPage.addEventListener("click", () => {
      state.offset = Math.max(0, state.offset - state.limit);
      loadExamples();
    });
    el.nextPage.addEventListener("click", () => {
      state.offset += state.limit;
      loadExamples();
    });
    el.prevTickGroup.addEventListener("click", () => {
      state.selectedGroupIndex = Math.max(0, state.selectedGroupIndex - 1);
      renderExamples();
    });
    el.nextTickGroup.addEventListener("click", () => {
      state.selectedGroupIndex = Math.min(state.tickGroups.length - 1, state.selectedGroupIndex + 1);
      renderExamples();
    });
    el.jumpTickButton.addEventListener("click", jumpToTick);
    el.jumpTick.addEventListener("keydown", (event) => {
      if (event.key === "Enter") jumpToTick();
    });
    for (const input of [el.regionNameFilter, el.tagFilter, el.labelSourceFilter, el.searchFilter]) {
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") loadExamples({ resetOffset: true });
      });
    }
    for (const input of [el.activeTabFilter, el.regionProfileFilter, el.cropExistsFilter]) {
      input.addEventListener("change", () => loadExamples({ resetOffset: true }));
    }
    el.reviewStatusFilter.addEventListener("change", () => loadExamples({ resetOffset: true }));
    el.pageSize.addEventListener("change", () => {
      state.limit = Number(el.pageSize.value) || 50;
      loadExamples({ resetOffset: true });
    });
    el.densityMode.addEventListener("change", () => {
      document.body.dataset.density = el.densityMode.value || "compact";
      savePrefs();
    });
    el.sortBy.addEventListener("change", () => loadExamples({ resetOffset: true }));
    el.loadQueue.addEventListener("click", () => loadExamples({ resetOffset: true }));
    el.queueMode.addEventListener("change", () => loadExamples({ resetOffset: true }));
    el.queueSize.addEventListener("change", () => loadExamples({ resetOffset: true }));
    el.queueSeed.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadExamples({ resetOffset: true });
    });
    el.groupByTick.addEventListener("change", () => {
      state.groupByTick = el.groupByTick.checked;
      state.selectedGroupIndex = 0;
      renderExamples();
      savePrefs();
    });
    for (const button of el.quickFilterButtons) {
      button.addEventListener("click", () => applyQuickFilter(button.dataset.quickFilter));
    }
    for (const button of el.reviewButtons) {
      button.addEventListener("click", () => saveReview(button.dataset.reviewLabel));
    }
    for (const button of el.batchReviewButtons) {
      button.addEventListener("click", () => saveReviewBatch(button.dataset.reviewLabel, visibleIndexes()));
    }
    for (const button of el.tickReviewButtons) {
      button.addEventListener("click", () => saveReviewBatch(button.dataset.reviewLabel, currentTickIndexes()));
    }
    el.examplesBody.addEventListener("click", (event) => {
      const row = event.target.closest("[data-index]");
      if (!row) return;
      selectExample(row.dataset.index, Number(row.dataset.position));
    });
    el.previousExample.addEventListener("click", () => {
      const position = state.selectedPosition - 1;
      const row = state.examples[position];
      if (row) selectExample(row.index, position);
    });
    el.nextExample.addEventListener("click", () => {
      const position = state.selectedPosition + 1;
      const row = state.examples[position];
      if (row) selectExample(row.index, position);
    });
    el.openFrame.addEventListener("click", () => {
      const path = state.selectedExample?.framePath;
      if (path) window.open(imageUrl("frame", path), "_blank", "noopener,noreferrer");
    });
    document.addEventListener("keydown", (event) => {
      if (!shortcutAllowed(event)) return;
      const key = event.key.toLowerCase();

      if (key === "j" || event.key === "ArrowDown") {
        event.preventDefault();
        moveSelection(1);
      } else if (key === "k" || event.key === "ArrowUp") {
        event.preventDefault();
        moveSelection(-1);
      } else if (key === "g") {
        event.preventDefault();
        saveReview("good");
      } else if (key === "b") {
        event.preventDefault();
        saveReview("bad_crop");
      } else if (key === "w") {
        event.preventDefault();
        saveReview("wrong_label");
      } else if (key === "u") {
        event.preventDefault();
        saveReview("unsure");
      }
    });
    init().catch((error) => {
      el.exampleStatus.textContent = String(error);
    });
  </script>
</body>
</html>
"""
    return template.replace("__SESSION__", escaped_session).replace("__MESSAGE__", message)


def escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


class InspectorHandler(BaseHTTPRequestHandler):
    dataset: TrainingDataset

    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self.send_html(html_page(self.dataset))
            return

        if path == "/api/summary":
            self.send_json(self.dataset.summary())
            return

        if path == "/api/filter-options":
            self.send_json(
                {
                    "available": self.dataset.available,
                    "message": None if self.dataset.available else self.dataset.missing_reason,
                    "options": self.dataset.filter_options() if self.dataset.available else {},
                }
            )
            return

        if path == "/api/reviews":
            self.send_json(
                {
                    "available": self.dataset.available,
                    "message": None if self.dataset.available else self.dataset.missing_reason,
                    "reviewCount": len(self.dataset.review_records),
                    "reviewCounts": self.dataset.review_counts() if self.dataset.available else {},
                    "recentReviews": self.dataset.review_records[-50:] if self.dataset.available else [],
                }
            )
            return

        if path == "/api/examples":
            self.handle_examples(parsed)
            return

        if path.startswith("/api/example/"):
            self.handle_example(path)
            return

        if path == "/crop":
            self.handle_file(parsed, kind="crop")
            return

        if path == "/frame":
            self.handle_file(parsed, kind="frame")
            return

        self.send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/review":
            self.handle_review()
            return

        if parsed.path == "/api/review-batch":
            self.handle_review_batch()
            return

        self.send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def handle_examples(self, parsed) -> None:
        if not self.dataset.available:
            self.send_json({"available": False, "message": self.dataset.missing_reason, "examples": []})
            return

        params = parse_qs(parsed.query)
        query = {
            "activeTab": first_param(params, "activeTab"),
            "regionProfile": first_param(params, "regionProfile"),
            "regionName": first_param(params, "regionName"),
            "tag": first_param(params, "tag"),
            "labelSource": first_param(params, "labelSource"),
            "cropExists": parse_bool(first_param(params, "cropExists")),
            "actualCropExists": parse_bool(first_param(params, "actualCropExists")),
            "reviewStatus": first_param(params, "reviewStatus"),
            "hideLowValueBase": parse_bool(first_param(params, "hideLowValueBase")),
            "nonBaseOnly": parse_bool(first_param(params, "nonBaseOnly")),
            "regionType": first_param(params, "regionType"),
            "tickId": first_param(params, "tickId"),
            "q": (first_param(params, "q") or "").strip().lower(),
        }
        offset = parse_int(first_param(params, "offset"), 0, minimum=0, maximum=10_000_000)
        limit = parse_int(first_param(params, "limit"), 100, minimum=1, maximum=500)
        sort_key = first_param(params, "sort") or "tick_asc"
        queue_mode = first_param(params, "queueMode")
        queue_size = parse_int(first_param(params, "queueSize"), limit, minimum=1, maximum=500)
        queue_seed = first_param(params, "seed")
        matches = sorted_matches(self.dataset.matching_rows(query), self.dataset, sort_key)
        full_match_count = len(matches)

        if queue_mode:
            matches = sampled_queue(matches, self.dataset, queue_mode, queue_size, queue_seed)

        page = matches[offset : offset + limit]

        self.send_json(
            {
                "available": True,
                "offset": offset,
                "limit": limit,
                "sort": sort_key,
                "totalMatched": len(matches),
                "fullMatchedBeforeQueue": full_match_count,
                "queueMode": queue_mode,
                "queueSize": queue_size if queue_mode else None,
                "totalExamples": len(self.dataset.manifest_rows),
                "examples": [self.dataset.compact_row(index, row) for index, row in page],
            }
        )

    def handle_example(self, path: str) -> None:
        if not self.dataset.available:
            self.send_json({"available": False, "message": self.dataset.missing_reason})
            return

        index_text = path.rsplit("/", 1)[-1]

        try:
            index = int(index_text)
        except ValueError:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "example index must be an integer")
            return

        if index < 0 or index >= len(self.dataset.manifest_rows):
            self.send_error_json(HTTPStatus.NOT_FOUND, "example index not found")
            return

        row = self.dataset.manifest_rows[index]
        self.send_json(
            {
                "index": index,
                "example": row,
                "latestReview": self.dataset.latest_review_for_row(row),
                "cropDiagnostics": self.dataset.crop_diagnostics_for_row(row),
            }
        )

    def handle_review(self) -> None:
        if not self.dataset.available:
            self.send_error_json(HTTPStatus.NOT_FOUND, self.dataset.missing_reason or MISSING_TRAINING_MESSAGE)
            return

        try:
            payload = self.read_json_body(max_bytes=16_384)
        except ValueError as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
            return

        try:
            index = int(payload.get("index"))
        except (TypeError, ValueError):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "index is required")
            return

        review_label = str(payload.get("reviewLabel") or "")
        notes = str(payload.get("notes") or "")

        try:
            record = self.dataset.append_review(index, review_label, notes)
        except ValueError as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
            return

        self.send_json({"ok": True, "review": record, "reviewCounts": self.dataset.review_counts()})

    def handle_review_batch(self) -> None:
        if not self.dataset.available:
            self.send_error_json(HTTPStatus.NOT_FOUND, self.dataset.missing_reason or MISSING_TRAINING_MESSAGE)
            return

        try:
            payload = self.read_json_body(max_bytes=131_072)
        except ValueError as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
            return

        indexes = payload.get("indexes")

        if not isinstance(indexes, list) or not indexes:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "indexes must be a non-empty list")
            return

        if len(indexes) > 500:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "batch review is limited to 500 examples")
            return

        review_label = str(payload.get("reviewLabel") or "")
        notes = str(payload.get("notes") or "")
        records = []

        for value in indexes:
            try:
                index = int(value)
                records.append(self.dataset.append_review(index, review_label, notes))
            except (TypeError, ValueError) as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return

        self.send_json(
            {
                "ok": True,
                "reviewedCount": len(records),
                "reviews": records,
                "reviewCounts": self.dataset.review_counts(),
            }
        )

    def handle_file(self, parsed, *, kind: str) -> None:
        if not self.dataset.available:
            self.send_error_json(HTTPStatus.NOT_FOUND, self.dataset.missing_reason or MISSING_TRAINING_MESSAGE)
            return

        params = parse_qs(parsed.query)
        requested = first_param(params, "path")

        if not requested:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "missing path parameter")
            return

        if kind == "crop":
            resolved = self.dataset.resolve_crop_path(requested)
        else:
            resolved = self.dataset.resolve_frame_path(requested)

        if resolved is None:
            self.send_error_json(HTTPStatus.FORBIDDEN, f"{kind} path is not allowed")
            return

        if not resolved.exists() or not resolved.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, f"{kind} file not found")
            return

        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"

        try:
            data = resolved.read_bytes()
        except OSError:
            self.send_error_json(HTTPStatus.NOT_FOUND, f"{kind} file could not be read")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status=status)

    def read_json_body(self, *, max_bytes: int) -> dict:
        length_text = self.headers.get("Content-Length", "0")

        try:
            length = int(length_text)
        except ValueError as error:
            raise ValueError("invalid Content-Length") from error

        if length < 1:
            raise ValueError("request body is required")

        if length > max_bytes:
            raise ValueError("request body is too large")

        raw = self.rfile.read(length)

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("request body must be JSON") from error

        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")

        return payload


def first_param(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name)

    if not values:
        return None

    return values[0]


def parse_args():
    parser = argparse.ArgumentParser(description="Read-only local browser inspector for training_data outputs.")
    parser.add_argument("--session", help="Telemetry session directory to inspect.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions directory when --session is omitted.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Local HTTP port, default {DEFAULT_PORT}.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = resolve_session(args)
    dataset = TrainingDataset(session)
    dataset.load()
    InspectorHandler.dataset = dataset
    server = ThreadingHTTPServer((HOST, args.port), InspectorHandler)
    url = f"http://{HOST}:{args.port}/"

    print(f"Training dataset inspector: {url}")
    print(f"Session: {session if session else 'none'}")

    if not dataset.available:
        print(dataset.missing_reason)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping training dataset inspector.")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

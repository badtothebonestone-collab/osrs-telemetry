import argparse
import json
import os
import random
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from label_ranges import label_for_tick, load_label_ranges
from tab_profile_names import canonical_tab_profile_key
from prepare_visual_perception import (
    build_visual_record,
    crop_box,
    crop_region_image,
    frame_exists_for_selection,
    iter_jsonl_records,
    normalize_screen_regions_document,
    region_groups_for_bundle,
    safe_name,
    shape_mask_metadata,
)
from telemetry_paths import find_newest_session, get_sessions_dir, safe_read_json


SCHEMA_VERSION_INDEX = "training_dataset.index.v1"
SCHEMA_VERSION_MANIFEST = "training_dataset.example.v1"
SCHEMA_VERSION_LABEL = "training_dataset.label.v1"
MISSING_PERCEPTION_MESSAGE = (
    "Required perception files not found. "
    "Run python telemetry-viewer\\build_perception_dataset.py first."
)
FOCUSED_UI_BASE_REGION_NAMES = {"chatbox", "minimap"}
FOCUSED_UI_PROFILES = {"base", "inventory", "equipment", "prayer", "magic", "combat", "stats"}


def folded_name(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dump_compact(data) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def parse_tick_id(value: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected integer tick id: {value}") from error


def positive_int(value: str) -> int:
    parsed = parse_tick_id(value)

    if parsed < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer: {value}")

    return parsed


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def training_paths(session: Path) -> dict[str, Path]:
    training_dir = session / "training_data"
    return {
        "trainingDir": training_dir,
        "index": training_dir / "training_index.json",
        "manifest": training_dir / "training_manifest.jsonl",
        "labelsApplied": training_dir / "labels_applied.jsonl",
        "crops": training_dir / "crops",
    }


def perception_paths(session: Path) -> dict[str, Path]:
    perception_dir = session / "perception"
    return {
        "perceptionDir": perception_dir,
        "tickBundles": perception_dir / "tick_bundles.jsonl",
        "visualTickRecords": perception_dir / "visual_tick_records.jsonl",
        "screenRegions": perception_dir / "screen_regions.json",
        "sessionLabels": perception_dir / "labels.json",
    }


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
        file.write("\n")

    os.replace(temp_path, path)


def append_jsonl(path: Path, records: list[dict]) -> None:
    if not records:
        return

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as file:
        for record in records:
            file.write(json_dump_compact(record))
            file.write("\n")


def read_jsonl_records(path: Path) -> list[dict]:
    if not path.exists():
        return []

    return list(iter_jsonl_records(path))


def load_labels(session: Path, args) -> dict:
    if args.labels:
        return load_label_ranges(args.labels)

    session_labels = perception_paths(session)["sessionLabels"]

    if session_labels.exists():
        return load_label_ranges(session_labels)

    return load_label_ranges()


def selected_by_tick_args(bundle: dict, args) -> bool:
    tick_id = bundle.get("tickId")

    if args.tick is not None:
        return tick_id == args.tick

    if args.tick_range is not None:
        start, end = args.tick_range
        return isinstance(tick_id, int) and start <= tick_id <= end

    return True


def event_types_on_tick(bundle: dict) -> list[str]:
    events = bundle.get("events") if isinstance(bundle.get("events"), dict) else {}
    values = events.get("onTickEventTypes")

    if isinstance(values, list):
        return [str(value) for value in values if value is not None]

    summaries = events.get("onTick")
    if isinstance(summaries, list):
        return [
            str(event.get("eventType"))
            for event in summaries
            if isinstance(event, dict) and event.get("eventType") is not None
        ]

    return []


def has_event_signal(bundle: dict) -> bool:
    return bool(event_types_on_tick(bundle))


def candidate_bundles(session: Path, tick_bundle_path: Path, args) -> tuple[list[dict], int]:
    candidates = []
    skipped_missing_frame = 0

    for bundle in iter_jsonl_records(tick_bundle_path):
        if not selected_by_tick_args(bundle, args):
            continue

        frame_exists = frame_exists_for_selection(session, bundle)
        bundle["_visualFrameExists"] = frame_exists

        if args.only_existing_frames and frame_exists is not True:
            skipped_missing_frame += 1
            continue

        candidates.append(bundle)

    if args.latest is not None:
        candidates = candidates[-args.latest:]

    return candidates, skipped_missing_frame


def sampled_bundles(candidates: list[dict], args) -> list[dict]:
    sample_every = max(1, int(args.sample_every or 1))

    if sample_every <= 1:
        return candidates

    selected = []

    for index, bundle in enumerate(candidates):
        if index % sample_every == 0:
            selected.append(bundle)
            continue

        if args.keep_event_ticks and has_event_signal(bundle):
            selected.append(bundle)

    return selected


def active_tab_for_record(record: dict) -> str:
    labels = record.get("labels") if isinstance(record.get("labels"), dict) else {}
    return normalize_active_tab(labels.get("activeTab") or "unknown")


def region_profile_for_record(record: dict) -> str:
    return canonical_tab_profile_key(record.get("regionProfile") or "base")


def region_name_for_record(record: dict) -> str:
    return str(record.get("regionName") or "unknown")


def tags_for_record(record: dict) -> set[str]:
    return {str(tag).strip().lower() for tag in (record.get("tags") or []) if str(tag).strip()}


def explicit_region_name_keys(args) -> set[str]:
    return {folded_name(name) for name in (args.region_name or [])}


def region_filter_reason(record: dict, args) -> str | None:
    profile = region_profile_for_record(record)
    region_name = region_name_for_record(record)
    region_name_key = folded_name(region_name)
    include_profiles = {canonical_tab_profile_key(value) for value in (args.region_profile or [])}
    include_region_names = explicit_region_name_keys(args)
    include_tags = {str(value).strip().lower() for value in (args.tag or []) if str(value).strip()}
    exclude_region_names = {folded_name(value) for value in (args.exclude_region_name or [])}

    if include_profiles and profile not in include_profiles:
        return "regionProfile"

    if include_region_names and region_name_key not in include_region_names:
        return "regionName"

    if include_tags and not tags_for_record(record).intersection(include_tags):
        return "tag"

    if args.exclude_base_regions and profile == "base":
        return "excludeBaseRegions"

    if region_name_key in exclude_region_names:
        return "excludeRegionName"

    if getattr(args, "_focused_ui_preset", False) and profile == "base":
        if region_name_key not in FOCUSED_UI_BASE_REGION_NAMES and region_name_key not in include_region_names:
            return "focusedUiBaseRegion"

    return None


def apply_region_filters(records: list[dict], args) -> tuple[list[dict], int]:
    kept = []
    skipped = 0

    for record in records:
        if region_filter_reason(record, args) is None:
            kept.append(record)
        else:
            skipped += 1

    return kept, skipped


def balanced_order(entries: list[dict], key_func, rng: random.Random) -> list[dict]:
    groups = defaultdict(list)

    for entry in entries:
        groups[key_func(entry["record"])].append(entry)

    for group in groups.values():
        rng.shuffle(group)

    ordered = []
    keys = sorted(groups.keys(), key=str)
    index = 0

    while keys:
        key = keys[index % len(keys)]
        group = groups[key]

        if group:
            ordered.append(group.pop())

        if not group:
            keys.remove(key)

            if not keys:
                break

            index %= len(keys)
        else:
            index = (index + 1) % len(keys)

    return ordered


def sample_record_entries(entries: list[dict], args) -> tuple[list[dict], int]:
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    ordered = list(entries)

    if args.random_sample:
        rng.shuffle(ordered)
    elif args.balanced_sample:
        ordered = balanced_order(ordered, region_profile_for_record, rng)

    active_tab_counts = Counter()
    region_profile_counts = Counter()
    region_name_counts = Counter()
    kept = []
    skipped = 0

    for index, entry in enumerate(ordered):
        record = entry["record"]
        active_tab = active_tab_for_record(record)
        region_profile = region_profile_for_record(record)
        region_name = region_name_for_record(record)

        if args.max_per_active_tab is not None and active_tab_counts[active_tab] >= args.max_per_active_tab:
            skipped += 1
            continue

        if args.max_per_region_profile is not None and region_profile_counts[region_profile] >= args.max_per_region_profile:
            skipped += 1
            continue

        if args.max_per_region_name is not None and region_name_counts[region_name] >= args.max_per_region_name:
            skipped += 1
            continue

        kept.append(entry)
        active_tab_counts[active_tab] += 1
        region_profile_counts[region_profile] += 1
        region_name_counts[region_name] += 1

        if args.max_examples is not None and len(kept) >= args.max_examples:
            skipped += len(ordered) - index - 1
            break

    return kept, max(0, skipped)


def bundle_with_label_fallback(bundle: dict, labels_doc: dict) -> dict:
    return bundle_with_active_tab_priority(bundle, None, labels_doc)


def normalize_active_tab(value) -> str:
    return canonical_tab_profile_key(value)


def bundle_with_active_tab_priority(bundle: dict, manual_active_tab: str | None, labels_doc: dict) -> dict:
    tick_id = bundle.get("tickId")
    derived = bundle.get("derived") if isinstance(bundle.get("derived"), dict) else {}
    label = label_for_tick(tick_id, labels_doc)
    updated = dict(bundle)
    updated_derived = dict(derived)

    if manual_active_tab and normalize_active_tab(manual_active_tab) != "auto":
        active_tab = normalize_active_tab(manual_active_tab)
        updated_derived.update(
            {
                "activeTab": active_tab,
                "activeTabSource": "manual",
                "activeTabConfidence": 1.0,
                "activeTabEvidence": [
                    {
                        "source": "manual",
                        "detail": f"manual active tab override activeTab={active_tab}",
                    }
                ],
                "uiState": derived.get("uiState") or (label.get("uiState") if label else None),
                "activityState": derived.get("activityState") or (label.get("activityState") if label else None),
                "labelSource": derived.get("labelSource") or (label.get("labelSource") if label else None),
            }
        )
        updated["derived"] = updated_derived
        return updated

    if label is not None:
        updated_derived.update(
            {
                "activeTab": normalize_active_tab(label.get("activeTab")),
                "activeTabSource": "label",
                "activeTabConfidence": 1.0,
                "activeTabEvidence": [
                    {
                        "source": "label",
                        "detail": f"manual label range {label['startTick']}-{label['endTick']}",
                    }
                ],
                "uiState": label.get("uiState"),
                "activityState": label.get("activityState"),
                "labelSource": label.get("labelSource"),
            }
        )
        updated["derived"] = updated_derived
        return updated

    active_tab = updated_derived.get("activeTab")

    if active_tab and active_tab != "unknown":
        return bundle

    updated_derived.update(
        {
            "activeTab": "unknown",
            "activeTabSource": "unknown",
            "activeTabConfidence": 0.0,
            "activeTabEvidence": updated_derived.get("activeTabEvidence") if isinstance(updated_derived.get("activeTabEvidence"), list) else [],
        }
    )
    updated["derived"] = updated_derived
    return updated


def existing_example_keys(path: Path) -> tuple[set[str], list[dict]]:
    keys = set()
    records = []

    for record in read_jsonl_records(path):
        key = example_key(record)

        if key:
            keys.add(key)

        records.append(record)

    return keys, records


def existing_label_ticks(path: Path) -> set[int]:
    ticks = set()

    for record in read_jsonl_records(path):
        tick_id = record.get("tickId")

        if isinstance(tick_id, int):
            ticks.add(tick_id)

    return ticks


def example_key(record: dict) -> str | None:
    session_id = record.get("sessionId")
    tick_id = record.get("tickId")
    region_profile = record.get("regionProfile")
    region_name = record.get("regionName")

    if session_id is None or tick_id is None or region_profile is None or region_name is None:
        return None

    return f"{session_id}|{tick_id}|{region_profile}|{region_name}"


def planned_crop_path(region: dict, tick_id: int, *, slot: dict | None = None) -> str:
    profile = safe_name(str(region.get("regionProfile") or "base"))

    if slot is not None:
        slot_number = int(slot.get("slot") or 0)

        if profile == "inventory":
            name = f"inventorySlot{slot_number:02d}"
        else:
            name = f"{safe_name(str(region.get('name') or 'grid'))}Slot{slot_number:02d}"

        extension = ".jpg"
    else:
        name = safe_name(str(region.get("name") or "region"))
        extension = ".png" if region.get("type") in ("circle", "ellipse") else ".jpg"

    return str(Path("training_data") / "crops" / f"tick-{tick_id:08d}" / profile / f"{name}{extension}")


def load_pillow_modules():
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None, None

    return Image, ImageDraw


def source_frame_path(session: Path, visual_record: dict) -> Path | None:
    frame = visual_record.get("frame") if isinstance(visual_record.get("frame"), dict) else {}
    frame_path_value = frame.get("absolutePath") or frame.get("path")

    if not frame_path_value:
        return None

    frame_path = Path(frame_path_value)

    if not frame_path.is_absolute():
        frame_path = session / frame_path

    try:
        frame_path.resolve().relative_to(session.resolve())
    except (OSError, ValueError):
        return None

    return frame_path


def pixel_box_for_record(record: dict) -> dict | None:
    geometry = record.get("pixelGeometry") if isinstance(record.get("pixelGeometry"), dict) else {}
    pixel_box = geometry.get("pixelBox") or geometry.get("boundingBox")
    return pixel_box if isinstance(pixel_box, dict) else None


def normalized_region_for_crop(record: dict) -> dict:
    geometry = record.get("normalizedGeometry") if isinstance(record.get("normalizedGeometry"), dict) else {}
    region_type = record.get("regionType")
    region = dict(geometry)
    region["type"] = region_type
    region["shapeMask"] = shape_mask_metadata(region)
    return region


def crop_output_path(session: Path, record: dict) -> Path:
    return session / str(record.get("cropPath") or "")


def set_relative_crop_path(session: Path, record: dict, path: Path) -> None:
    try:
        record["cropPath"] = str(path.resolve().relative_to(session.resolve()))
    except (OSError, ValueError):
        record["cropPath"] = str(path)


def generate_crops_for_records(session: Path, visual_record: dict, records: list[dict], image_module, image_draw_module) -> tuple[int, list[str]]:
    if not records:
        return 0, []

    frame_path = source_frame_path(session, visual_record)

    if frame_path is None:
        return 0, [f"tick {visual_record.get('tickId')} frame path unavailable or outside session; crops skipped"]

    if not frame_path.exists():
        return 0, [f"tick {visual_record.get('tickId')} frame file missing; crops skipped"]

    crop_count = 0
    warnings = []

    try:
        with image_module.open(frame_path) as image:
            for record in records:
                output_path = crop_output_path(session, record)

                if output_path.exists():
                    record["cropExists"] = True
                    continue

                box = crop_box(pixel_box_for_record(record))

                if box is None:
                    warnings.append(
                        f"tick {record.get('tickId')} region {record.get('regionName')} has no valid pixel box; crop skipped"
                    )
                    continue

                output_path.parent.mkdir(parents=True, exist_ok=True)

                if record.get("regionType") == "gridSlot":
                    image.crop(box).convert("RGB").save(output_path, "JPEG")
                else:
                    crop_image, crop_format, extension = crop_region_image(
                        image,
                        normalized_region_for_crop(record),
                        box,
                        image_module,
                        image_draw_module,
                    )

                    if output_path.suffix.lower() != extension:
                        output_path = output_path.with_suffix(extension)
                        set_relative_crop_path(session, record, output_path)

                    crop_image.save(output_path, crop_format)

                record["cropExists"] = True
                crop_count += 1
    except OSError as error:
        warnings.append(f"tick {visual_record.get('tickId')} unable to crop frame: {error}")

    return crop_count, warnings


def label_record(bundle: dict, labels_doc: dict) -> dict:
    tick_id = bundle.get("tickId")
    derived = bundle.get("derived") if isinstance(bundle.get("derived"), dict) else {}
    label = label_for_tick(tick_id, labels_doc)

    record = {
        "schemaVersion": SCHEMA_VERSION_LABEL,
        "tickId": tick_id,
        "activeTab": derived.get("activeTab") or "unknown",
        "uiState": derived.get("uiState"),
        "activityState": derived.get("activityState"),
        "labelSource": derived.get("labelSource"),
        "matchingLabelRange": None,
    }

    if label is not None:
        record["matchingLabelRange"] = {
            "startTick": label.get("startTick"),
            "endTick": label.get("endTick"),
            "activeTab": normalize_active_tab(label.get("activeTab")),
            "uiState": label.get("uiState"),
            "activityState": label.get("activityState"),
            "notes": label.get("notes"),
            "labelSource": label.get("labelSource"),
        }

    return record


def state_value(summary: dict, *keys):
    value = summary

    for key in keys:
        if not isinstance(value, dict):
            return None

        value = value.get(key)

    return value


def telemetry_summary(visual_record: dict) -> dict:
    state = visual_record.get("stateSummary") if isinstance(visual_record.get("stateSummary"), dict) else {}
    return {
        "gameState": state.get("gameState"),
        "position": state.get("position"),
        "hp": state.get("hp"),
        "prayer": state.get("prayer"),
        "runEnergyPercent": state.get("runEnergyPercent"),
        "inventoryCount": state.get("inventoryCount"),
        "equipmentCount": state.get("equipmentCount"),
        "npcCount": state.get("npcCount"),
        "playerCount": state.get("playerCount"),
    }


def source_payload(visual_record: dict, region: dict) -> dict:
    frame = visual_record.get("frame") if isinstance(visual_record.get("frame"), dict) else {}
    return {
        "labelSource": visual_record.get("labelSource"),
        "sourceFrameExists": frame.get("exists") is True,
        "sourceFrameLatencyMs": frame.get("totalLatencyMs"),
        "sourceRegionProfile": region.get("regionProfile"),
        "sourceRegionRole": region.get("regionRole"),
    }


def manifest_record_for_region(bundle: dict, visual_record: dict, region: dict) -> dict:
    tick_id = visual_record.get("tickId")
    return {
        "schemaVersion": SCHEMA_VERSION_MANIFEST,
        "sessionId": visual_record.get("sessionId"),
        "tickId": tick_id,
        "timestampUtc": visual_record.get("timestampUtc"),
        "framePath": state_value(visual_record, "frame", "path"),
        "cropPath": planned_crop_path(region, tick_id),
        "cropExists": False,
        "regionName": region.get("name"),
        "regionType": region.get("type"),
        "regionProfile": region.get("regionProfile") or "base",
        "tags": region.get("tags") if isinstance(region.get("tags"), list) else [],
        "pixelGeometry": region.get("pixelGeometry"),
        "normalizedGeometry": region.get("geometry") or {"box": region.get("normalizedBox")},
        "labels": {
            "activeTab": visual_record.get("activeTab") or "unknown",
            "activeTabSource": visual_record.get("activeTabSource") or "unknown",
            "uiState": visual_record.get("uiState"),
            "activityState": visual_record.get("activityState"),
            "quality": "unreviewed",
        },
        "telemetrySummary": {
            **telemetry_summary(visual_record),
            "eventTypesOnTick": event_types_on_tick(bundle),
        },
        "source": source_payload(visual_record, region),
        "split": "",
    }


def manifest_record_for_slot(bundle: dict, visual_record: dict, region: dict, slot: dict) -> dict:
    tick_id = visual_record.get("tickId")
    slot_number = int(slot.get("slot") or 0)
    profile = region.get("regionProfile") or "base"
    region_name = "inventorySlot%02d" % slot_number if profile == "inventory" else f"{region.get('name')}Slot{slot_number:02d}"
    return {
        "schemaVersion": SCHEMA_VERSION_MANIFEST,
        "sessionId": visual_record.get("sessionId"),
        "tickId": tick_id,
        "timestampUtc": visual_record.get("timestampUtc"),
        "framePath": state_value(visual_record, "frame", "path"),
        "cropPath": planned_crop_path(region, tick_id, slot=slot),
        "cropExists": False,
        "regionName": region_name,
        "regionType": "gridSlot",
        "regionProfile": profile,
        "tags": region.get("tags") if isinstance(region.get("tags"), list) else [],
        "pixelGeometry": {"type": "gridSlot", "pixelBox": slot.get("pixelBox")},
        "normalizedGeometry": {"type": "gridSlot", "box": slot.get("normalizedBox")},
        "labels": {
            "activeTab": visual_record.get("activeTab") or "unknown",
            "activeTabSource": visual_record.get("activeTabSource") or "unknown",
            "uiState": visual_record.get("uiState"),
            "activityState": visual_record.get("activityState"),
            "quality": "unreviewed",
        },
        "telemetrySummary": {
            **telemetry_summary(visual_record),
            "eventTypesOnTick": event_types_on_tick(bundle),
        },
        "source": source_payload(visual_record, region),
        "split": "",
    }


def visual_args(args) -> SimpleNamespace:
    return SimpleNamespace(
        active_tab=args.active_tab,
        include_all_tab_profiles=args.include_all_tab_profiles,
    )


def records_for_bundle(session: Path, screen_regions: dict, bundle: dict, args) -> tuple[list[dict], dict, list[str]]:
    groups, inference, profile, included, skipped, warnings = region_groups_for_bundle(
        screen_regions,
        visual_args(args),
        bundle,
    )
    visual_record, record_warnings, _crop_count, _slot_crop_count = build_visual_record(
        session,
        bundle,
        groups,
        active_tab_inference=inference,
        region_profile=profile,
        applied_region_profiles=included,
        skipped_tab_profiles=skipped,
        crop_enabled=False,
        generate_grid_slots=bool(args.generate_grid_slots),
    )
    warnings.extend(record_warnings)
    records = []

    for region in visual_record.get("regions") or []:
        if not isinstance(region, dict):
            continue

        records.append(manifest_record_for_region(bundle, visual_record, region))

        if args.generate_grid_slots and region.get("type") == "grid":
            for slot in region.get("gridSlots") or []:
                if isinstance(slot, dict):
                    records.append(manifest_record_for_slot(bundle, visual_record, region, slot))

    return records, visual_record, warnings


def apply_preset(args) -> None:
    args._focused_ui_preset = False

    if args.preset == "review":
        included_region_names = explicit_region_name_keys(args)

        for region_name in ("fullFrame", "gameViewport", "sidePanel", "tabs"):
            if folded_name(region_name) not in included_region_names and region_name not in args.exclude_region_name:
                args.exclude_region_name.append(region_name)

        if args.max_per_region_name is None:
            args.max_per_region_name = 100

    elif args.preset == "focused-ui":
        args._focused_ui_preset = True

        if not args.region_profile:
            args.region_profile.extend(sorted(FOCUSED_UI_PROFILES))


def counters_for_records(records: list[dict]) -> dict:
    active_tabs = Counter()
    region_profiles = Counter()
    region_names = Counter()
    tags = Counter()
    crop_exists_count = 0
    crop_missing_count = 0

    for record in records:
        labels = record.get("labels") if isinstance(record.get("labels"), dict) else {}
        active_tabs[labels.get("activeTab") or "unknown"] += 1
        region_profiles[record.get("regionProfile") or "unknown"] += 1
        region_names[record.get("regionName") or "unknown"] += 1

        if record.get("cropExists") is True:
            crop_exists_count += 1
        else:
            crop_missing_count += 1

        for tag in record.get("tags") or []:
            tags[str(tag)] += 1

    return {
        "activeTabCounts": dict(active_tabs.most_common()),
        "regionProfileCounts": dict(region_profiles.most_common()),
        "regionNameCounts": dict(region_names.most_common()),
        "tagCounts": dict(tags.most_common()),
        "cropExistsExampleCount": crop_exists_count,
        "cropMissingExampleCount": crop_missing_count,
    }


def build_training_dataset(session: Path, args) -> dict:
    session = session.expanduser().resolve()
    paths = perception_paths(session)
    output_paths = training_paths(session)
    training_dir = output_paths["trainingDir"]
    warnings = []

    if not paths["tickBundles"].exists() or not paths["screenRegions"].exists():
        raise FileNotFoundError(MISSING_PERCEPTION_MESSAGE)

    if args.rebuild and training_dir.exists():
        shutil.rmtree(training_dir)

    output_paths["crops"].mkdir(parents=True, exist_ok=True)
    screen_regions = safe_read_json(paths["screenRegions"])

    if not isinstance(screen_regions, dict):
        raise ValueError(f"Unable to read screen regions: {paths['screenRegions']}")

    screen_regions = normalize_screen_regions_document(screen_regions)
    labels_doc = load_labels(session, args)
    warnings.extend(labels_doc.get("warnings", []))
    existing_keys, existing_records = existing_example_keys(output_paths["manifest"])
    existing_label_tick_ids = existing_label_ticks(output_paths["labelsApplied"])
    candidates, skipped_missing_frame = candidate_bundles(session, paths["tickBundles"], args)
    selected_bundles = sampled_bundles(candidates, args)
    selected_tick_count = len(selected_bundles)
    image_module, image_draw_module = load_pillow_modules()
    planned_entries = []
    new_records = []
    new_label_records = []
    skipped_duplicate_count = 0
    skipped_by_region_filter_count = 0
    skipped_by_sampling_count = 0
    skipped_missing_crop_count = 0
    unknown_active_tab_count = 0
    crop_count = 0

    if image_module is None:
        warnings.append(
            "Pillow not available; training crops were not generated"
            + ("; manifest metadata will be written because --include-missing-crops was provided" if args.include_missing_crops else "; examples without crops will be skipped")
        )

    for raw_bundle in selected_bundles:
        bundle = bundle_with_active_tab_priority(raw_bundle, args.active_tab, labels_doc)
        derived = bundle.get("derived") if isinstance(bundle.get("derived"), dict) else {}

        if (derived.get("activeTab") or "unknown") == "unknown":
            unknown_active_tab_count += 1

        tick_id = bundle.get("tickId")

        if isinstance(tick_id, int) and tick_id not in existing_label_tick_ids:
            new_label_records.append(label_record(bundle, labels_doc))
            existing_label_tick_ids.add(tick_id)

        records, _visual_record, record_warnings = records_for_bundle(session, screen_regions, bundle, args)
        warnings.extend(record_warnings)
        filtered_records, skipped_count = apply_region_filters(records, args)
        skipped_by_region_filter_count += skipped_count

        for record in filtered_records:
            planned_entries.append(
                {
                    "bundle": bundle,
                    "visual_record": _visual_record,
                    "record": record,
                }
            )

    planned_entries, skipped_by_sampling_count = sample_record_entries(planned_entries, args)
    records_by_visual_id = {}
    visual_records_by_id = {}
    planned_keys = set()

    for entry in planned_entries:
        record = entry["record"]
        key = example_key(record)

        if key is None:
            warnings.append(f"skipped record without duplicate key on tick {record.get('tickId')}")
            skipped_by_sampling_count += 1
            continue

        if key in existing_keys or key in planned_keys:
            skipped_duplicate_count += 1
            continue

        planned_keys.add(key)
        visual_id = id(entry["visual_record"])
        visual_records_by_id[visual_id] = entry["visual_record"]
        records_by_visual_id.setdefault(visual_id, []).append(record)

    for visual_id, records in records_by_visual_id.items():
        if image_module is not None:
            added_crops, crop_warnings = generate_crops_for_records(
                session,
                visual_records_by_id[visual_id],
                records,
                image_module,
                image_draw_module,
            )
            crop_count += added_crops
            warnings.extend(crop_warnings)

        for record in records:
            if record.get("cropExists") is True or args.include_missing_crops:
                new_records.append(record)
            else:
                skipped_missing_crop_count += 1

    append_jsonl(output_paths["manifest"], new_records)
    append_jsonl(output_paths["labelsApplied"], new_label_records)
    all_records = existing_records + new_records
    counts = counters_for_records(all_records)
    index = {
        "schemaVersion": SCHEMA_VERSION_INDEX,
        "generatedAtUtc": utc_now(),
        "sessionPath": str(session),
        "selectedTickCount": selected_tick_count,
        "exampleCount": len(all_records),
        "manifestExampleCount": len(all_records),
        "cropExistsExampleCount": counts["cropExistsExampleCount"],
        "cropMissingExampleCount": counts["cropMissingExampleCount"],
        "addedExampleCount": len(new_records),
        "existingExampleCount": len(existing_records),
        "skippedDuplicateCount": skipped_duplicate_count,
        "skippedMissingFrameCount": skipped_missing_frame,
        "skippedByRegionFilterCount": skipped_by_region_filter_count,
        "skippedBySamplingCount": skipped_by_sampling_count,
        "skippedMissingCropCount": skipped_missing_crop_count,
        "unknownActiveTabCount": unknown_active_tab_count,
        "countsByActiveTab": counts["activeTabCounts"],
        "countsByRegionProfile": counts["regionProfileCounts"],
        "countsByRegionName": counts["regionNameCounts"],
        "countsByTag": counts["tagCounts"],
        "labelsLoaded": labels_doc.get("loaded"),
        "labelsPath": labels_doc.get("path"),
        "labelPriority": [
            "CLI explicit active tab",
            "manual label range",
            "tick bundle activeTab",
            "unknown",
        ],
        "rebuilt": bool(args.rebuild),
        "cropsGenerated": bool(crop_count),
        "cropCount": crop_count,
        "cropGenerationReason": None if crop_count else (
            "Pillow not available" if image_module is None else "no new non-duplicate examples required crop generation"
        ),
        "filters": {
            "tick": args.tick,
            "range": list(args.tick_range) if args.tick_range else None,
            "latest": args.latest,
            "activeTab": args.active_tab,
            "sampleEvery": args.sample_every,
            "keepEventTicks": bool(args.keep_event_ticks),
            "onlyExistingFrames": bool(args.only_existing_frames),
            "generateGridSlots": bool(args.generate_grid_slots),
            "includeAllTabProfiles": bool(args.include_all_tab_profiles),
            "maxExamples": args.max_examples,
            "regionProfiles": args.region_profile,
            "regionNames": args.region_name,
            "tags": args.tag,
            "excludeBaseRegions": bool(args.exclude_base_regions),
            "excludeRegionNames": args.exclude_region_name,
            "maxPerActiveTab": args.max_per_active_tab,
            "maxPerRegionProfile": args.max_per_region_profile,
            "maxPerRegionName": args.max_per_region_name,
            "balancedSample": bool(args.balanced_sample),
            "randomSample": bool(args.random_sample),
            "seed": args.seed,
            "preset": args.preset,
            "includeMissingCrops": bool(args.include_missing_crops),
            "rebuild": bool(args.rebuild),
        },
        "paths": {
            "trainingIndex": "training_data/training_index.json",
            "trainingManifest": "training_data/training_manifest.jsonl",
            "labelsApplied": "training_data/labels_applied.jsonl",
            "crops": "training_data/crops",
        },
        "warnings": warnings[:100],
        "warningCount": len(warnings),
    }
    atomic_write_json(output_paths["index"], index)
    return index


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build a durable derived training dataset manifest from existing OSRS telemetry perception data.",
        epilog=(
            "Default behavior is non-destructive: active-tab auto, only existing frames, "
            "sample every 5 ticks, keep event ticks, and skip duplicate examples. "
            "training_data is only cleared when --rebuild is explicitly provided."
        ),
    )
    parser.add_argument("--session", help="Telemetry session directory to process.")
    parser.add_argument("--sessions-dir", help="Override the telemetry sessions directory when --session is omitted.")
    parser.add_argument("--labels", help="Path to tab label ranges JSON. Defaults to session perception\\labels.json when present, otherwise telemetry-viewer\\tab_labels.json.")
    parser.add_argument("--preset", choices=("review", "focused-ui"), help="Apply conservative selection defaults for review queues or focused UI crop datasets.")
    parser.add_argument("--latest", type=positive_int, metavar="N", help="Select the newest N matching tick bundles before sampling.")
    parser.add_argument("--range", nargs=2, type=parse_tick_id, dest="tick_range", metavar=("START", "END"), help="Select an inclusive tick range.")
    parser.add_argument("--tick", type=parse_tick_id, help="Select one tick.")
    parser.add_argument("--active-tab", default="auto", help="Use auto inference or manually apply one tab profile, such as inventory, equipment, prayer, logout, world_switcher, or unknown. Default: auto.")
    parser.add_argument("--sample-every", type=positive_int, default=5, metavar="N", help="Keep every Nth selected tick. Default: 5.")
    parser.add_argument("--keep-event-ticks", action="store_true", default=True, help="Keep ticks with on-tick events even if skipped by sampling. Default: true.")
    parser.add_argument("--only-existing-frames", action="store_true", default=True, help="Use only ticks whose frame file currently exists. Default: true.")
    parser.add_argument("--region-profile", action="append", default=[], metavar="NAME", help="Only include examples from this region profile. Can be repeated.")
    parser.add_argument("--region-name", action="append", default=[], metavar="NAME", help="Only include examples for this region name. Can be repeated.")
    parser.add_argument("--tag", action="append", default=[], metavar="TAG", help="Only include examples with this region tag. Can be repeated.")
    parser.add_argument("--exclude-base-regions", action="store_true", help="Skip all base profile regions.")
    parser.add_argument("--exclude-region-name", action="append", default=[], metavar="NAME", help="Skip examples for this region name. Can be repeated.")
    parser.add_argument("--max-per-active-tab", type=positive_int, metavar="N", help="Cap new examples per active tab after filters.")
    parser.add_argument("--max-per-region-profile", type=positive_int, metavar="N", help="Cap new examples per region profile after filters.")
    parser.add_argument("--max-per-region-name", type=positive_int, metavar="N", help="Cap new examples per region name after filters.")
    sample_group = parser.add_mutually_exclusive_group()
    sample_group.add_argument("--balanced-sample", action="store_true", help="Balance selected examples by region profile before caps/max examples.")
    sample_group.add_argument("--random-sample", action="store_true", help="Randomize selected examples before caps/max examples.")
    parser.add_argument("--seed", type=int, help="Seed for reproducible random or balanced sampling.")
    parser.add_argument("--generate-grid-slots", action="store_true", help="Also add manifest examples for derived grid slots. Crop bytes are still deferred in this pass.")
    parser.add_argument("--include-all-tab-profiles", action="store_true", help="Include every tab profile region set for each selected tick.")
    parser.add_argument("--include-missing-crops", action="store_true", help="Write metadata-only manifest rows when crop files cannot be generated. By default, missing-crop examples are skipped.")
    parser.add_argument("--max-examples", type=positive_int, metavar="N", help="Stop after adding at most N new manifest examples.")
    parser.add_argument("--rebuild", action="store_true", help="Explicitly delete and rebuild training_data for the selected session.")
    args = parser.parse_args()
    apply_preset(args)
    return args


def main() -> int:
    args = parse_args()

    if args.tick_range is not None:
        start, end = args.tick_range

        if end < start:
            args.tick_range = (end, start)

    session = resolve_session(args)

    if session is None:
        print(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")
        return 1

    try:
        index = build_training_dataset(session, args)
    except FileNotFoundError as error:
        print(f"session: {session}")
        print(error)
        return 1
    except (OSError, ValueError) as error:
        print(f"Unable to build training dataset: {error}")
        return 1

    print(f"Built training dataset manifest: {index['sessionPath']}")
    print(f"  training_data/training_index.json")
    print(f"  training_data/training_manifest.jsonl")
    print(f"  training_data/labels_applied.jsonl")
    print(f"  selectedTickCount: {index['selectedTickCount']}")
    print(f"  exampleCount: {index['exampleCount']}")
    print(f"  manifestExampleCount: {index['manifestExampleCount']}")
    print(f"  cropExistsExampleCount: {index['cropExistsExampleCount']}")
    print(f"  cropMissingExampleCount: {index['cropMissingExampleCount']}")
    print(f"  addedExampleCount: {index['addedExampleCount']}")
    print(f"  skippedDuplicateCount: {index['skippedDuplicateCount']}")
    print(f"  skippedMissingFrameCount: {index['skippedMissingFrameCount']}")
    print(f"  skippedByRegionFilterCount: {index['skippedByRegionFilterCount']}")
    print(f"  skippedBySamplingCount: {index['skippedBySamplingCount']}")
    print(f"  skippedMissingCropCount: {index['skippedMissingCropCount']}")
    print(f"  unknownActiveTabCount: {index['unknownActiveTabCount']}")
    print(f"  countsByActiveTab: {index['countsByActiveTab']}")
    print(f"  countsByRegionProfile: {index['countsByRegionProfile']}")
    print(f"  rebuilt: {index['rebuilt']}")
    print(f"  cropsGenerated: {index['cropsGenerated']}")
    print(f"  cropCount: {index['cropCount']}")
    print(f"  warningCount: {index['warningCount']}")

    if index["warnings"]:
        print("  warnings:")

        for warning in index["warnings"][:10]:
            print(f"    - {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

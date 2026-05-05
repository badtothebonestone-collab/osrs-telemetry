import argparse
import json
import os
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from label_ranges import label_for_tick, load_label_ranges
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


def bundle_with_label_fallback(bundle: dict, labels_doc: dict) -> dict:
    return bundle_with_active_tab_priority(bundle, None, labels_doc)


def normalize_active_tab(value) -> str:
    return str(value or "unknown").strip().lower().replace(" ", "_").replace("-", "_") or "unknown"


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
                "activeTab": label.get("activeTab") or "unknown",
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
            "activeTab": label.get("activeTab"),
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


def counters_for_records(records: list[dict]) -> dict:
    active_tabs = Counter()
    region_profiles = Counter()
    tags = Counter()

    for record in records:
        labels = record.get("labels") if isinstance(record.get("labels"), dict) else {}
        active_tabs[labels.get("activeTab") or "unknown"] += 1
        region_profiles[record.get("regionProfile") or "unknown"] += 1

        for tag in record.get("tags") or []:
            tags[str(tag)] += 1

    return {
        "activeTabCounts": dict(active_tabs.most_common()),
        "regionProfileCounts": dict(region_profiles.most_common()),
        "tagCounts": dict(tags.most_common()),
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
    new_records = []
    new_label_records = []
    skipped_duplicate_count = 0
    unknown_active_tab_count = 0
    crop_count = 0
    max_examples = args.max_examples if args.max_examples is not None else None

    if image_module is None:
        warnings.append("Pillow not available; training crops were not generated, but manifest metadata was written")

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
        bundle_new_records = []

        for record in records:
            key = example_key(record)

            if key is None:
                warnings.append(f"skipped record without duplicate key on tick {record.get('tickId')}")
                continue

            if key in existing_keys:
                skipped_duplicate_count += 1
                continue

            bundle_new_records.append(record)
            existing_keys.add(key)

            if max_examples is not None and len(new_records) + len(bundle_new_records) >= max_examples:
                break

        if image_module is not None:
            added_crops, crop_warnings = generate_crops_for_records(
                session,
                _visual_record,
                bundle_new_records,
                image_module,
                image_draw_module,
            )
            crop_count += added_crops
            warnings.extend(crop_warnings)

        new_records.extend(bundle_new_records)

        if max_examples is not None and len(new_records) >= max_examples:
            break

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
        "addedExampleCount": len(new_records),
        "existingExampleCount": len(existing_records),
        "skippedDuplicateCount": skipped_duplicate_count,
        "skippedMissingFrameCount": skipped_missing_frame,
        "unknownActiveTabCount": unknown_active_tab_count,
        "countsByActiveTab": counts["activeTabCounts"],
        "countsByRegionProfile": counts["regionProfileCounts"],
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
    parser.add_argument("--latest", type=positive_int, metavar="N", help="Select the newest N matching tick bundles before sampling.")
    parser.add_argument("--range", nargs=2, type=parse_tick_id, dest="tick_range", metavar=("START", "END"), help="Select an inclusive tick range.")
    parser.add_argument("--tick", type=parse_tick_id, help="Select one tick.")
    parser.add_argument("--active-tab", default="auto", help="Use auto inference or manually apply one tab profile, such as inventory, equipment, prayer, logout, world_switcher, or unknown. Default: auto.")
    parser.add_argument("--sample-every", type=positive_int, default=5, metavar="N", help="Keep every Nth selected tick. Default: 5.")
    parser.add_argument("--keep-event-ticks", action="store_true", default=True, help="Keep ticks with on-tick events even if skipped by sampling. Default: true.")
    parser.add_argument("--only-existing-frames", action="store_true", default=True, help="Use only ticks whose frame file currently exists. Default: true.")
    parser.add_argument("--generate-grid-slots", action="store_true", help="Also add manifest examples for derived grid slots. Crop bytes are still deferred in this pass.")
    parser.add_argument("--include-all-tab-profiles", action="store_true", help="Include every tab profile region set for each selected tick.")
    parser.add_argument("--max-examples", type=positive_int, metavar="N", help="Stop after adding at most N new manifest examples.")
    parser.add_argument("--rebuild", action="store_true", help="Explicitly delete and rebuild training_data for the selected session.")
    return parser.parse_args()


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
    print(f"  addedExampleCount: {index['addedExampleCount']}")
    print(f"  skippedDuplicateCount: {index['skippedDuplicateCount']}")
    print(f"  skippedMissingFrameCount: {index['skippedMissingFrameCount']}")
    print(f"  unknownActiveTabCount: {index['unknownActiveTabCount']}")
    print(f"  countsByActiveTab: {index['countsByActiveTab']}")
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

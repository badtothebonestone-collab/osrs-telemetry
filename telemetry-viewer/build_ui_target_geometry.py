import argparse
import json
import os
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from prepare_visual_perception import (
    bounding_box_for_region,
    grid_slot_boxes,
    normalize_screen_regions_document,
    region_to_pixel_geometry,
)
from tab_profile_names import canonical_tab_profile_key, resolve_tab_profile_key
from telemetry_paths import find_newest_session, get_sessions_dir, iter_jsonl, list_tick_files, resolve_frame_path, safe_read_json


SCHEMA_VERSION_INDEX = "interaction_geometry.ui_index.v1"
SCHEMA_VERSION_RECORD = "interaction_geometry.ui_target.v1"
MISSING_PERCEPTION_MESSAGE = "Run python telemetry-viewer\\build_perception_dataset.py first."
MISSING_SCREEN_REGIONS_MESSAGE = "Run perception build/calibration first."
FRAME_TICK_RE = re.compile(r"frame-tick-(\d+)\.[^.]+$", re.IGNORECASE)
FRAME_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

EQUIPMENT_SLOT_MAP = {
    0: ("Helmet", ("Helmet", "Head")),
    1: ("Cape", ("Cape",)),
    2: ("Necklace", ("Necklace", "Amulet")),
    3: ("Main_hand", ("Main_hand", "Main hand", "Weapon")),
    4: ("Body", ("Body", "Chest")),
    5: ("Offhand", ("Offhand", "Off hand", "Shield")),
    7: ("Legs", ("Legs",)),
    9: ("Gloves", ("Gloves", "Hands")),
    10: ("Boots", ("Boots", "Feet")),
    12: ("Ring", ("Ring",)),
    13: ("Ammo", ("Ammo", "Ammunition")),
}

BASE_UI_REGION_NAMES = (
    "fullFrame",
    "gameViewport",
    "minimap",
    "chatbox",
    "sidePanel",
    "tabs",
    "orbs",
    "compass",
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


def output_paths(session: Path) -> dict[str, Path]:
    output_dir = session / "interaction_geometry"
    return {
        "outputDir": output_dir,
        "targets": output_dir / "ui_targets.jsonl",
        "index": output_dir / "ui_geometry_index.json",
    }


def perception_paths(session: Path) -> dict[str, Path]:
    perception_dir = session / "perception"
    return {
        "tickBundles": perception_dir / "tick_bundles.jsonl",
        "screenRegions": perception_dir / "screen_regions.json",
    }


def session_relative(session: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(session.resolve()))
    except (OSError, ValueError):
        return str(path)


def frame_with_actual_exists(session: Path, bundle: dict) -> dict:
    source = bundle.get("frame") if isinstance(bundle.get("frame"), dict) else {}
    frame = dict(source)
    path = frame.get("path")
    recorded_exists = frame.get("exists")

    if path:
        resolved = resolve_frame_path(session, str(path))
        frame["exists"] = resolved.exists() if resolved else None
        frame["recordedExists"] = recorded_exists

        if frame["exists"] and (not isinstance(frame.get("width"), int) or not isinstance(frame.get("height"), int)):
            width, height = image_dimensions(resolved)

            if width is not None and height is not None:
                frame["width"] = width
                frame["height"] = height

    return frame


def bundle_with_actual_frame(session: Path, bundle: dict) -> dict:
    updated = dict(bundle)
    updated["frame"] = frame_with_actual_exists(session, bundle)
    return updated


def image_dimensions(path: Path | None) -> tuple[int | None, int | None]:
    if path is None:
        return None, None

    try:
        with path.open("rb") as file:
            header = file.read(24)

            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
                return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")

            if header[:2] != b"\xff\xd8":
                return None, None

            file.seek(2)

            while True:
                marker_prefix = file.read(1)

                if not marker_prefix:
                    return None, None

                if marker_prefix != b"\xff":
                    continue

                marker = file.read(1)

                while marker == b"\xff":
                    marker = file.read(1)

                if not marker:
                    return None, None

                marker_value = marker[0]

                if marker_value in {0xD8, 0xD9}:
                    continue

                length_bytes = file.read(2)

                if len(length_bytes) != 2:
                    return None, None

                segment_length = int.from_bytes(length_bytes, "big")

                if segment_length < 2:
                    return None, None

                if marker_value in {
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                }:
                    payload = file.read(5)

                    if len(payload) != 5:
                        return None, None

                    height = int.from_bytes(payload[1:3], "big")
                    width = int.from_bytes(payload[3:5], "big")
                    return width, height

                file.seek(segment_length - 2, 1)
    except OSError:
        return None, None


def folded_name(value) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


def safe_item_id(value):
    return value if isinstance(value, int) and value > 0 else None


def safe_quantity(value):
    return value if isinstance(value, int) and value >= 0 else None


def load_item_names(session: Path) -> dict[int, str]:
    data = safe_read_json(session / "dictionaries" / "items.json")

    if not isinstance(data, dict):
        return {}

    names = {}

    for raw_id, name in data.items():
        try:
            item_id = int(raw_id)
        except (TypeError, ValueError):
            continue

        if isinstance(name, str) and name:
            names[item_id] = name

    return names


def selected_by_tick_args(bundle: dict, args) -> bool:
    tick_id = bundle.get("tickId")

    if args.tick_range is not None:
        start, end = args.tick_range
        return isinstance(tick_id, int) and start <= tick_id <= end

    return True


def frame_tick_from_path(path: Path) -> int | None:
    match = FRAME_TICK_RE.search(path.name)

    if not match:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


def retained_frame_tick_ids(session: Path) -> set[int]:
    frames_dir = session / "frames"

    if not frames_dir.exists():
        return set()

    ticks = set()

    try:
        files = frames_dir.iterdir()
    except OSError:
        return set()

    for path in files:
        if not path.is_file() or path.suffix.lower() not in FRAME_IMAGE_SUFFIXES:
            continue

        tick_id = frame_tick_from_path(path)

        if tick_id is not None:
            ticks.add(tick_id)

    return ticks


def tick_range_payload(bundles: list[dict]) -> list[int] | None:
    tick_ids = [bundle.get("tickId") for bundle in bundles if isinstance(bundle.get("tickId"), int)]

    if not tick_ids:
        return None

    return [min(tick_ids), max(tick_ids)]


def selection_mode(args) -> str:
    if args.latest_with_frames is not None:
        return "latest-with-frames"

    if args.latest is not None:
        return "latest"

    if args.tick_range is not None:
        return "range"

    return "default"


def synthetic_bundle_from_tick(session: Path, tick: dict) -> dict:
    frame_path = tick.get("framePath")
    resolved = resolve_frame_path(session, str(frame_path)) if frame_path else None
    width, height = image_dimensions(resolved)

    if width is None:
        width = tick.get("frameWidth") if isinstance(tick.get("frameWidth"), int) else tick.get("canvasWidth")

    if height is None:
        height = tick.get("frameHeight") if isinstance(tick.get("frameHeight"), int) else tick.get("canvasHeight")

    return {
        "schemaVersion": "perception_tick_bundle.synthetic_from_raw_tick.v1",
        "tickId": tick.get("tickId"),
        "timestampUtc": tick.get("timestampUtc"),
        "frame": {
            "path": frame_path,
            "width": width,
            "height": height,
            "exists": bool(resolved and resolved.exists()),
            "recordedExists": None,
        },
        "derived": {
            "activeTab": tick.get("activeTab") if isinstance(tick.get("activeTab"), str) else "unknown",
            "activeTabSource": tick.get("activeTabSource") if isinstance(tick.get("activeTabSource"), str) else "unknown",
            "activeTabConfidence": tick.get("activeTabConfidence") if isinstance(tick.get("activeTabConfidence"), (int, float)) else 0.0,
            "activeTabEvidence": tick.get("activeTabEvidence") if isinstance(tick.get("activeTabEvidence"), list) else [],
        },
    }


def read_raw_ticks_for_retained_frames(session: Path, args, retained_ticks: set[int]) -> list[dict]:
    tick_files = list_tick_files(session)

    if not tick_files:
        return []

    ticks = []

    for _source, tick in iter_jsonl(tick_files):
        if not isinstance(tick, dict):
            continue

        tick_id = tick.get("tickId")

        if isinstance(tick_id, int) and tick_id in retained_ticks and selected_by_tick_args(tick, args):
            ticks.append(tick)

    return ticks


def read_tick_bundles(path: Path, args, session: Path) -> tuple[list[dict], dict]:
    bundles = []
    retained_ticks = retained_frame_tick_ids(session)
    warnings = []

    if path.exists():
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                line = line.strip()

                if not line:
                    continue

                try:
                    bundle = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"Malformed tick_bundles.jsonl at line {line_number}: {error}") from error

                if isinstance(bundle, dict) and selected_by_tick_args(bundle, args):
                    bundles.append(bundle)

    if args.latest_with_frames is not None:
        if not retained_ticks:
            raise RuntimeError("No retained frame files found. Capture a fresh session or adjust frame retention.")

        bundles = [bundle for bundle in bundles if bundle.get("tickId") in retained_ticks]
        bundle_tick_ids = {bundle.get("tickId") for bundle in bundles if isinstance(bundle.get("tickId"), int)}
        raw_ticks = read_raw_ticks_for_retained_frames(session, args, retained_ticks)
        synthetic_bundles = [
            synthetic_bundle_from_tick(session, tick)
            for tick in raw_ticks
            if isinstance(tick.get("tickId"), int) and tick.get("tickId") not in bundle_tick_ids
        ]

        if synthetic_bundles:
            warnings.append(
                "perception tick bundles did not cover all retained-frame ticks; using raw ticks plus actual frame files for missing UI geometry ticks"
            )
            bundles.extend(synthetic_bundles)
            bundles.sort(key=lambda bundle: bundle.get("tickId") if isinstance(bundle.get("tickId"), int) else -1)

        if not bundles:
            raise RuntimeError(
                "No selected perception tick bundles match retained frame files. Capture a fresh session or adjust the tick selection."
            )

        if len(bundles) < args.latest_with_frames:
            warnings.append(
                f"requested {args.latest_with_frames} retained-frame ticks, but only {len(bundles)} matching tick bundles were found"
            )

        bundles = bundles[-args.latest_with_frames :]
    elif args.latest is not None:
        bundles = bundles[-args.latest :]

    selected_tick_ids = {bundle.get("tickId") for bundle in bundles if isinstance(bundle.get("tickId"), int)}
    selected_frame_ticks = sorted(selected_tick_ids & retained_ticks)
    selection_info = {
        "selectedBy": selection_mode(args),
        "retainedFrameTickCount": len(retained_ticks),
        "retainedFrameTickRange": [min(retained_ticks), max(retained_ticks)] if retained_ticks else None,
        "selectedFrameTickCount": len(selected_frame_ticks),
        "selectedFrameTickRange": [selected_frame_ticks[0], selected_frame_ticks[-1]] if selected_frame_ticks else None,
        "selectedTickRange": tick_range_payload(bundles),
        "warnings": warnings,
    }

    return bundles, selection_info


def read_raw_ticks(session: Path, tick_ids: set[int]) -> dict[int, dict]:
    tick_files = list_tick_files(session)

    if not tick_files:
        raise FileNotFoundError(f"Raw tick files not found in session: {session}")

    ticks = {}

    for _source, tick in iter_jsonl(tick_files):
        if not isinstance(tick, dict):
            continue

        tick_id = tick.get("tickId")

        if isinstance(tick_id, int) and tick_id in tick_ids:
            ticks[tick_id] = tick

            if len(ticks) >= len(tick_ids):
                break

    return ticks


def active_tab_payload(bundle: dict, args, tab_profiles: dict) -> dict:
    requested = canonical_tab_profile_key(args.active_tab or "auto")

    if requested and requested != "auto":
        value = requested
        source = "manual"
        confidence = 1.0
    else:
        derived = bundle.get("derived") if isinstance(bundle.get("derived"), dict) else {}
        value = canonical_tab_profile_key(derived.get("activeTab"))
        source = derived.get("activeTabSource") if isinstance(derived.get("activeTabSource"), str) else "unknown"
        confidence = (
            derived.get("activeTabConfidence")
            if isinstance(derived.get("activeTabConfidence"), (int, float))
            else 0.0
        )

    resolved = resolve_tab_profile_key(tab_profiles, value)

    if resolved:
        value = resolved
    elif value not in ("auto", "unknown"):
        value = canonical_tab_profile_key(value)

    return {
        "value": value if value and value != "auto" else "unknown",
        "source": source,
        "confidence": confidence,
    }


def selected_profiles_for_tick(active_tab: str, tab_profiles: dict, args) -> list[str]:
    if args.include_all_tab_profiles:
        return list(tab_profiles.keys())

    if active_tab and active_tab != "unknown" and active_tab in tab_profiles:
        return [active_tab]

    return []


def pixel_center(pixel_box: dict | None) -> dict | None:
    if not isinstance(pixel_box, dict):
        return None

    x = pixel_box.get("x")
    y = pixel_box.get("y")
    w = pixel_box.get("w")
    h = pixel_box.get("h")

    if not all(isinstance(value, int) for value in (x, y, w, h)):
        return None

    return {
        "x": x + (w / 2.0),
        "y": y + (h / 2.0),
    }


def geometry_for_region(region: dict, frame_width, frame_height) -> tuple[dict | None, dict | None]:
    try:
        normalized_box = bounding_box_for_region(region)
        pixel_geometry = region_to_pixel_geometry(region, frame_width, frame_height)
    except ValueError:
        return None, None

    pixel_box = pixel_geometry.get("boundingBox") if isinstance(pixel_geometry, dict) else None
    return normalized_box, pixel_box


def base_record(
    session_id,
    bundle: dict,
    active_tab: dict,
    frame: dict,
    target: dict,
    normalized_box: dict | None,
    pixel_box: dict | None,
    source: str,
    confidence: float,
    warnings: list[str],
) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION_RECORD,
        "sessionId": session_id,
        "tickId": bundle.get("tickId"),
        "timestampUtc": bundle.get("timestampUtc"),
        "frame": {
            "path": frame.get("path"),
            "width": frame.get("width"),
            "height": frame.get("height"),
            "exists": frame.get("exists"),
            "recordedExists": frame.get("recordedExists"),
        },
        "activeTab": active_tab,
        "target": target,
        "geometry": {
            "coordinateSpace": "framePixels",
            "normalizedBox": normalized_box,
            "pixelBox": pixel_box,
            "center": pixel_center(pixel_box),
        },
        "source": source,
        "confidence": confidence,
        "warnings": warnings,
    }


def inventory_items_by_slot(raw_tick: dict) -> dict[int, dict]:
    inventory = raw_tick.get("inventory") if isinstance(raw_tick, dict) else []

    if not isinstance(inventory, list):
        return {}

    items = {}

    for item in inventory:
        if not isinstance(item, dict):
            continue

        slot = item.get("slot")

        if isinstance(slot, int):
            items[slot] = item

    return items


def equipment_items_by_slot(raw_tick: dict) -> dict[int, dict]:
    equipment = raw_tick.get("equipment") if isinstance(raw_tick, dict) else []

    if not isinstance(equipment, list):
        return {}

    items = {}

    for item in equipment:
        if not isinstance(item, dict):
            continue

        slot = item.get("slot")

        if isinstance(slot, int):
            items[slot] = item

    return items


def named_region_lookup(regions: dict) -> dict[str, tuple[str, dict]]:
    lookup = {}

    for name, region in regions.items():
        lookup.setdefault(folded_name(name), (name, region))

    return lookup


def find_region_by_alias(regions: dict, aliases: tuple[str, ...]) -> tuple[str, dict] | None:
    lookup = named_region_lookup(regions)

    for alias in aliases:
        match = lookup.get(folded_name(alias))

        if match:
            return match

    return None


def slot_box_payload(slot: dict, frame_width, frame_height) -> tuple[dict | None, dict | None]:
    normalized_box = slot.get("box") if isinstance(slot.get("box"), dict) else None

    if normalized_box is None:
        return None, None

    pixel_box = None

    if isinstance(frame_width, int) and isinstance(frame_height, int):
        _, pixel_box = geometry_for_region({"type": "rect", "box": normalized_box, "tags": []}, frame_width, frame_height)

    return normalized_box, pixel_box


def inventory_records(session_id, bundle, raw_tick, screen_regions, active_tab, item_names) -> tuple[list[dict], list[str], bool]:
    frame = bundle.get("frame") if isinstance(bundle.get("frame"), dict) else {}
    frame_width = frame.get("width")
    frame_height = frame.get("height")
    tab_profiles = screen_regions.get("tabProfiles") if isinstance(screen_regions.get("tabProfiles"), dict) else {}
    inventory_regions = tab_profiles.get("inventory") if isinstance(tab_profiles.get("inventory"), dict) else {}
    grid = inventory_regions.get("inventoryGrid")

    if not isinstance(grid, dict):
        return [], ["missing tabProfiles.inventory.inventoryGrid"], True

    slots = grid_slot_boxes(grid)
    items = inventory_items_by_slot(raw_tick)
    records = []

    for slot in slots:
        slot_index = slot.get("slot")
        item = items.get(slot_index, {})
        item_id = safe_item_id(item.get("itemId")) if isinstance(item, dict) else None
        quantity = safe_quantity(item.get("quantity")) if isinstance(item, dict) else None
        normalized_box, pixel_box = slot_box_payload(slot, frame_width, frame_height)
        record_warnings = []

        if pixel_box is None:
            record_warnings.append("frame dimensions unavailable; pixel geometry unavailable")

        target_name = f"inventorySlot{int(slot_index):02d}" if isinstance(slot_index, int) else "inventorySlot"
        records.append(
            base_record(
                session_id,
                bundle,
                active_tab,
                frame,
                {
                    "targetId": f"{bundle.get('tickId')}:inventory:{target_name}",
                    "targetType": "inventorySlot",
                    "targetName": target_name,
                    "regionProfile": "inventory",
                    "regionName": "inventoryGrid",
                    "slotIndex": slot_index,
                    "itemId": item_id,
                    "itemName": item_names.get(item_id),
                    "quantity": quantity,
                },
                normalized_box,
                pixel_box,
                "inventory_json_plus_calibrated_grid",
                1.0 if pixel_box is not None else 0.0,
                record_warnings,
            )
        )

    return records, [], False


def equipment_records(session_id, bundle, raw_tick, screen_regions, active_tab, item_names) -> tuple[list[dict], list[str], int]:
    frame = bundle.get("frame") if isinstance(bundle.get("frame"), dict) else {}
    frame_width = frame.get("width")
    frame_height = frame.get("height")
    tab_profiles = screen_regions.get("tabProfiles") if isinstance(screen_regions.get("tabProfiles"), dict) else {}
    equipment_regions = tab_profiles.get("equipment") if isinstance(tab_profiles.get("equipment"), dict) else {}
    items = equipment_items_by_slot(raw_tick)
    records = []
    warnings = []
    missing_region_count = 0

    for slot_index, (slot_name, aliases) in EQUIPMENT_SLOT_MAP.items():
        match = find_region_by_alias(equipment_regions, aliases)

        if match is None:
            missing_region_count += 1
            warnings.append(f"missing equipment region for slot {slot_index} {slot_name}")
            continue

        region_name, region = match
        item = items.get(slot_index, {})
        item_id = safe_item_id(item.get("itemId")) if isinstance(item, dict) else None
        quantity = safe_quantity(item.get("quantity")) if isinstance(item, dict) else None
        normalized_box, pixel_box = geometry_for_region(region, frame_width, frame_height)
        record_warnings = []

        if pixel_box is None:
            record_warnings.append("frame dimensions unavailable; pixel geometry unavailable")

        records.append(
            base_record(
                session_id,
                bundle,
                active_tab,
                frame,
                {
                    "targetId": f"{bundle.get('tickId')}:equipment:{slot_name}",
                    "targetType": "equipmentSlot",
                    "targetName": slot_name,
                    "regionProfile": "equipment",
                    "regionName": region_name,
                    "equipmentSlotIndex": slot_index,
                    "equipmentSlotName": slot_name,
                    "itemId": item_id,
                    "itemName": item_names.get(item_id),
                    "quantity": quantity,
                },
                normalized_box,
                pixel_box,
                "equipment_json_plus_calibrated_regions",
                1.0 if pixel_box is not None else 0.0,
                record_warnings,
            )
        )

    return records, warnings, missing_region_count


def grid_or_region_records(
    session_id,
    bundle,
    profile_name: str,
    regions: dict,
    active_tab: dict,
    *,
    target_type: str,
    slot_prefix: str,
    source: str,
    semantic_warning: str | None = None,
) -> tuple[list[dict], list[str]]:
    frame = bundle.get("frame") if isinstance(bundle.get("frame"), dict) else {}
    frame_width = frame.get("width")
    frame_height = frame.get("height")
    records = []
    warnings = []

    if not regions:
        warnings.append(f"missing tabProfiles.{profile_name} regions")
        return records, warnings

    for region_name, region in regions.items():
        if not isinstance(region, dict):
            continue

        if region.get("type") == "grid":
            for slot in grid_slot_boxes(region):
                slot_index = slot.get("slot")
                normalized_box, pixel_box = slot_box_payload(slot, frame_width, frame_height)
                record_warnings = []

                if semantic_warning:
                    record_warnings.append(semantic_warning)

                if pixel_box is None:
                    record_warnings.append("frame dimensions unavailable; pixel geometry unavailable")

                target_name = f"{slot_prefix}{int(slot_index):02d}" if isinstance(slot_index, int) else slot_prefix
                target_payload = {
                    "targetId": f"{bundle.get('tickId')}:{profile_name}:{target_name}",
                    "targetType": target_type,
                    "targetName": target_name,
                    "regionProfile": profile_name,
                    "regionName": region_name,
                    "slotIndex": slot_index,
                }

                if target_type == "prayerIcon":
                    target_payload["prayerIndex"] = slot_index
                elif target_type == "magicSpell":
                    target_payload["spellIndex"] = slot_index

                records.append(
                    base_record(
                        session_id,
                        bundle,
                        active_tab,
                        frame,
                        target_payload,
                        normalized_box,
                        pixel_box,
                        source,
                        1.0 if pixel_box is not None else 0.0,
                        record_warnings,
                    )
                )
        else:
            normalized_box, pixel_box = geometry_for_region(region, frame_width, frame_height)
            record_warnings = []

            if semantic_warning:
                record_warnings.append(semantic_warning)

            if pixel_box is None:
                record_warnings.append("frame dimensions unavailable; pixel geometry unavailable")

            records.append(
                base_record(
                    session_id,
                    bundle,
                    active_tab,
                    frame,
                    {
                        "targetId": f"{bundle.get('tickId')}:{profile_name}:{region_name}",
                        "targetType": target_type,
                        "targetName": region_name,
                        "regionProfile": profile_name,
                        "regionName": region_name,
                    },
                    normalized_box,
                    pixel_box,
                    source,
                    1.0 if pixel_box is not None else 0.0,
                    record_warnings,
                )
            )

    return records, warnings


def base_ui_records(session_id, bundle, screen_regions, active_tab) -> tuple[list[dict], list[str]]:
    frame = bundle.get("frame") if isinstance(bundle.get("frame"), dict) else {}
    frame_width = frame.get("width")
    frame_height = frame.get("height")
    base_regions = screen_regions.get("baseRegions") if isinstance(screen_regions.get("baseRegions"), dict) else {}
    records = []
    warnings = []

    for region_name in BASE_UI_REGION_NAMES:
        region = base_regions.get(region_name)

        if not isinstance(region, dict):
            warnings.append(f"missing base region: {region_name}")
            continue

        normalized_box, pixel_box = geometry_for_region(region, frame_width, frame_height)
        record_warnings = []

        if pixel_box is None:
            record_warnings.append("frame dimensions unavailable; pixel geometry unavailable")

        records.append(
            base_record(
                session_id,
                bundle,
                active_tab,
                frame,
                {
                    "targetId": f"{bundle.get('tickId')}:base:{region_name}",
                    "targetType": "baseUiRegion",
                    "targetName": region_name,
                    "regionProfile": "base",
                    "regionName": region_name,
                },
                normalized_box,
                pixel_box,
                "calibrated_base_region",
                1.0 if pixel_box is not None else 0.0,
                record_warnings,
            )
        )

    return records, warnings


def records_for_bundle(session_id, bundle, raw_tick, screen_regions, args, item_names) -> tuple[list[dict], dict]:
    normalized_regions = normalize_screen_regions_document(screen_regions)
    tab_profiles = normalized_regions["tabProfiles"]
    active_tab = active_tab_payload(bundle, args, tab_profiles)
    selected_profiles = selected_profiles_for_tick(active_tab["value"], tab_profiles, args)
    records = []
    warnings = []
    stats = {
        "missingInventoryGrid": 0,
        "missingEquipmentRegions": 0,
        "missingFrameDimensions": 0,
    }

    frame = bundle.get("frame") if isinstance(bundle.get("frame"), dict) else {}

    if not isinstance(frame.get("width"), int) or not isinstance(frame.get("height"), int):
        stats["missingFrameDimensions"] = 1

    if args.include_base_regions:
        base_records, base_warnings = base_ui_records(session_id, bundle, normalized_regions, active_tab)
        records.extend(base_records)
        warnings.extend(base_warnings)

    for profile_name in selected_profiles:
        regions = tab_profiles.get(profile_name) if isinstance(tab_profiles.get(profile_name), dict) else {}

        if profile_name == "inventory":
            inventory_payload, inventory_warnings, missing_grid = inventory_records(
                session_id,
                bundle,
                raw_tick,
                normalized_regions,
                active_tab,
                item_names,
            )
            records.extend(inventory_payload)
            warnings.extend(inventory_warnings)
            stats["missingInventoryGrid"] += 1 if missing_grid else 0
        elif profile_name == "equipment":
            equipment_payload, equipment_warnings, missing_regions = equipment_records(
                session_id,
                bundle,
                raw_tick,
                normalized_regions,
                active_tab,
                item_names,
            )
            records.extend(equipment_payload)
            warnings.extend(equipment_warnings)
            stats["missingEquipmentRegions"] += missing_regions
        elif profile_name == "prayer":
            prayer_payload, prayer_warnings = grid_or_region_records(
                session_id,
                bundle,
                profile_name,
                regions,
                active_tab,
                target_type="prayerIcon",
                slot_prefix="prayerSlot",
                source="calibrated_prayer_grid",
                semantic_warning="semantic prayer mapping incomplete",
            )
            records.extend(prayer_payload)
            warnings.extend(prayer_warnings)
        elif profile_name == "magic":
            magic_payload, magic_warnings = grid_or_region_records(
                session_id,
                bundle,
                profile_name,
                regions,
                active_tab,
                target_type="magicSpell",
                slot_prefix="magicSlot",
                source="calibrated_magic_grid",
                semantic_warning="semantic magic spell mapping incomplete",
            )
            records.extend(magic_payload)
            warnings.extend(magic_warnings)

    return records, {
        "warnings": warnings,
        "stats": stats,
        "activeTab": active_tab["value"],
        "selectedProfiles": selected_profiles,
    }


def atomic_write_outputs(paths: dict[str, Path], records: list[dict], index: dict) -> None:
    output_dir = paths["outputDir"]
    temp_dir = output_dir / f".tmp-ui-targets-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}-{os.getpid()}"

    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    temp_dir.mkdir(parents=True)

    try:
        with (temp_dir / "ui_targets.jsonl").open("w", encoding="utf-8") as file:
            for record in records:
                file.write(json_dump_compact(record))
                file.write("\n")

        with (temp_dir / "ui_geometry_index.json").open("w", encoding="utf-8") as file:
            json.dump(index, file, indent=2)
            file.write("\n")

        output_dir.mkdir(parents=True, exist_ok=True)
        os.replace(temp_dir / "ui_targets.jsonl", paths["targets"])
        os.replace(temp_dir / "ui_geometry_index.json", paths["index"])
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def build_ui_target_geometry(session: Path, args) -> dict:
    session = session.expanduser().resolve()
    paths = perception_paths(session)
    output = output_paths(session)
    warnings = []

    if not paths["tickBundles"].exists() and args.latest_with_frames is None:
        raise FileNotFoundError(MISSING_PERCEPTION_MESSAGE)
    elif not paths["tickBundles"].exists():
        warnings.append("perception tick bundles unavailable; using raw ticks plus actual frame files for retained-frame selection")

    if not paths["screenRegions"].exists():
        raise FileNotFoundError(MISSING_SCREEN_REGIONS_MESSAGE)

    screen_regions = safe_read_json(paths["screenRegions"])

    if not isinstance(screen_regions, dict):
        raise ValueError(f"Unable to read screen regions: {paths['screenRegions']}")

    screen_regions = normalize_screen_regions_document(screen_regions)
    bundles, selection_info = read_tick_bundles(paths["tickBundles"], args, session)
    warnings.extend(selection_info.get("warnings") or [])
    selected_tick_ids = {bundle.get("tickId") for bundle in bundles if isinstance(bundle.get("tickId"), int)}
    raw_ticks = read_raw_ticks(session, selected_tick_ids)
    item_names = load_item_names(session)
    manifest = safe_read_json(session / "manifest.json")
    manifest = manifest if isinstance(manifest, dict) else {}
    session_id = manifest.get("sessionId") or session.name
    all_records = []
    target_type_counts = Counter()
    active_tab_counts = Counter()
    selected_tick_active_tab_counts = Counter()
    region_profile_counts = Counter()
    missing_inventory_grid_count = 0
    missing_equipment_region_count = 0
    missing_frame_dimension_count = 0
    missing_frame_target_count = 0

    for bundle in bundles:
        tick_id = bundle.get("tickId")
        raw_tick = raw_ticks.get(tick_id, {})
        bundle_for_records = bundle_with_actual_frame(session, bundle)

        if isinstance(tick_id, int) and not raw_tick:
            warnings.append(f"raw tick not found for tick {tick_id}; item IDs unavailable")

        records, info = records_for_bundle(session_id, bundle_for_records, raw_tick, screen_regions, args, item_names)
        all_records.extend(records)
        warnings.extend(info["warnings"])
        selected_tick_active_tab_counts[info["activeTab"]] += 1
        missing_inventory_grid_count += info["stats"]["missingInventoryGrid"]
        missing_equipment_region_count += info["stats"]["missingEquipmentRegions"]
        missing_frame_dimension_count += info["stats"]["missingFrameDimensions"]

        for record in records:
            target = record.get("target") if isinstance(record.get("target"), dict) else {}
            record_active_tab = record.get("activeTab") if isinstance(record.get("activeTab"), dict) else {}
            target_type_counts[target.get("targetType") or "unknown"] += 1
            active_tab_counts[record_active_tab.get("value") or "unknown"] += 1
            region_profile_counts[target.get("regionProfile") or "unknown"] += 1
            frame = record.get("frame") if isinstance(record.get("frame"), dict) else {}

            if frame.get("path") and frame.get("exists") is not True:
                missing_frame_target_count += 1

    index = {
        "schemaVersion": SCHEMA_VERSION_INDEX,
        "generatedAtUtc": utc_now(),
        "sessionPath": str(session),
        "selectedBy": selection_info["selectedBy"],
        "selectedTickRange": selection_info["selectedTickRange"],
        "retainedFrameTickCount": selection_info["retainedFrameTickCount"],
        "retainedFrameTickRange": selection_info["retainedFrameTickRange"],
        "selectedFrameTickCount": selection_info["selectedFrameTickCount"],
        "selectedFrameTickRange": selection_info["selectedFrameTickRange"],
        "selectedTickCount": len(bundles),
        "targetRecordCount": len(all_records),
        "countsByTargetType": dict(target_type_counts.most_common()),
        "countsByActiveTab": dict(active_tab_counts.most_common()),
        "selectedTickCountsByActiveTab": dict(selected_tick_active_tab_counts.most_common()),
        "countsByRegionProfile": dict(region_profile_counts.most_common()),
        "missingInventoryGridCount": missing_inventory_grid_count,
        "missingEquipmentRegionCount": missing_equipment_region_count,
        "missingFrameDimensionCount": missing_frame_dimension_count,
        "missingFrameTargetCount": missing_frame_target_count,
        "filters": {
            "latest": args.latest,
            "latestWithFrames": args.latest_with_frames,
            "range": list(args.tick_range) if args.tick_range else None,
            "activeTab": args.active_tab,
            "includeBaseRegions": bool(args.include_base_regions),
            "includeAllTabProfiles": bool(args.include_all_tab_profiles),
        },
        "paths": {
            "uiTargets": "interaction_geometry/ui_targets.jsonl",
            "uiGeometryIndex": "interaction_geometry/ui_geometry_index.json",
        },
        "warnings": warnings[:100],
        "warningCount": len(warnings),
    }
    atomic_write_outputs(output, all_records, index)
    return index


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Build deterministic derived UI target geometry from raw JSON slot telemetry "
            "and calibrated screen-region profiles. This emits geometry records only."
        )
    )
    parser.add_argument("--session", help="Telemetry session directory to process.")
    parser.add_argument("--sessions-dir", help="Override the telemetry sessions directory when --session is omitted.")
    parser.add_argument("--latest", type=positive_int, metavar="N", help="Select the newest N matching tick bundles.")
    parser.add_argument(
        "--latest-with-frames",
        type=positive_int,
        metavar="N",
        help="Select the newest N tick bundles that have retained frame image files on disk.",
    )
    parser.add_argument("--range", nargs=2, type=parse_tick_id, dest="tick_range", metavar=("START", "END"), help="Select an inclusive tick range.")
    parser.add_argument(
        "--active-tab",
        default="auto",
        help="Use auto inference or manually select one tab profile, such as inventory, equipment, prayer, magic, combat, stats, or unknown. Default: auto.",
    )
    parser.add_argument("--include-base-regions", action="store_true", help="Also emit base UI regions such as minimap, chatbox, sidePanel, tabs, orbs, and compass.")
    parser.add_argument("--include-all-tab-profiles", action="store_true", help="Emit supported geometry from all tabProfiles, regardless of activeTab.")
    args = parser.parse_args()

    if args.latest is not None and args.latest_with_frames is not None:
        parser.error("--latest-with-frames cannot be combined with --latest")

    if args.tick_range is not None:
        start, end = args.tick_range

        if end < start:
            args.tick_range = (end, start)

    return args


def print_summary(index: dict) -> None:
    print(f"session: {index['sessionPath']}")
    print(f"selected by: {index.get('selectedBy')}")
    print(f"selected ticks: {index['selectedTickCount']}")
    print(f"selected tick range: {index.get('selectedTickRange') or 'none'}")
    print(f"retained frame tick range: {index.get('retainedFrameTickRange') or 'none'}")
    print(f"selected frame ticks: {index.get('selectedFrameTickCount', 0)}")
    print(f"target records: {index['targetRecordCount']}")
    print("counts by targetType:")

    if index["countsByTargetType"]:
        for target_type, count in index["countsByTargetType"].items():
            print(f"  {target_type}: {count}")
    else:
        print("  none")

    if index["warnings"]:
        print("warnings:")

        for warning in index["warnings"][:20]:
            print(f"  - {warning}")


def main() -> int:
    args = parse_args()
    session = resolve_session(args)

    if session is None:
        print(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")
        return 1

    try:
        index = build_ui_target_geometry(session, args)
    except FileNotFoundError as error:
        print(f"session: {session}")
        print(str(error))
        return 1
    except ValueError as error:
        print(f"session: {session}")
        print(f"Unable to build UI target geometry: {error}")
        return 1
    except RuntimeError as error:
        print(f"session: {session}")
        print(str(error))
        return 1

    print_summary(index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
